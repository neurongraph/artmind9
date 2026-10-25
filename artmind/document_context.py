"""The document context every chunk extraction prompt carries.

Chunks extract in isolation; an entity whose meaning depends on the whole
document is named differently in each chunk unless each chunk can see what the
document is. This module decides WHAT that context says: the document's
metadata, its heading outline with the chunk's position, and a summary. It does
no I/O and calls no LLM -- `ingest.extract_kg` does both and passes the results
in. Schema-agnostic: nothing here names a domain.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The block's length cap in characters. Only the legacy `document_header: {chars: N}` setting changes it; `document_context` has no cap key.
DEFAULT_CAP = 2000


@dataclass(frozen=True)
class ContextSettings:
    enabled: bool
    summary: bool
    focus: str
    cap: int


_OFF = ContextSettings(enabled=False, summary=False, focus="", cap=DEFAULT_CAP)


def context_settings(schema: dict) -> ContextSettings:
    """What this schema asks for. `document_context` is the key; the older
    `document_header` (commit cf787bd) is read when `document_context` is absent:
    `false` turns the block off, `{chars: N}` sets the cap. Only an explicit
    `false` turns the summary off."""
    if "document_context" in schema:
        setting = schema["document_context"]
        if setting is False:
            return _OFF
        if isinstance(setting, dict):
            focus = setting.get("focus")
            return ContextSettings(
                enabled=True,
                summary=setting.get("summary", True) is not False,
                focus=focus.strip() if isinstance(focus, str) else "",
                cap=DEFAULT_CAP,
            )
        return ContextSettings(enabled=True, summary=True, focus="", cap=DEFAULT_CAP)

    legacy = schema.get("document_header", True)
    if legacy is False:
        return _OFF
    cap = DEFAULT_CAP
    if isinstance(legacy, dict):
        try:
            cap = max(int(legacy.get("chars", DEFAULT_CAP)), 0)
        except (TypeError, ValueError):
            cap = DEFAULT_CAP
    return ContextSettings(enabled=True, summary=True, focus="", cap=cap)


def _chunk_headings(chunks_meta: dict) -> dict[int, str]:
    """{chunk seq: header_path} for the numbered entries of `chunks_meta.json`
    (the `_body_sha256` stamp and anything else non-numeric are ignored)."""
    out: dict[int, str] = {}
    for key, value in chunks_meta.items():
        if isinstance(value, dict) and str(key).isdigit():
            out[int(key)] = str(value.get("header_path") or "")
    return out


def build_outline(
    chunks_meta: dict, seq: int, chunk_count: int, *, document_outline: bool = False,
) -> str:
    """'You are in: <heading> (chunk N of M)', from the chunk's own breadcrumb.

    With `document_outline`, the distinct headings of the whole document follow,
    in order of first appearance, so a chunk can place itself in the document."""
    headings = _chunk_headings(chunks_meta)
    here = headings.get(seq, "")
    position = f"(chunk {seq} of {chunk_count})"
    lines = [f"You are in: {here} {position}" if here else f"You are in: chunk {seq} of {chunk_count}"]
    if document_outline:
        seen: list[str] = []
        for seq_no in sorted(headings):
            heading = headings[seq_no]
            if heading and heading not in seen:
                seen.append(heading)
        if seen:
            lines.append("Document outline:")
            lines.extend(f"  - {heading}" for heading in seen)
    return "\n".join(lines)


def _text(value) -> str:
    """One line of text: whitespace (newlines, tabs, runs of spaces) collapsed, so a
    multi-line frontmatter value cannot add a line that looks like another field."""
    if isinstance(value, (list, tuple)):
        return ", ".join(t for t in (_text(v) for v in value) if t)
    return " ".join(str(value).split()) if value is not None else ""


def build_metadata(document: dict) -> str:
    """The document's filing metadata as labelled lines, fields with a value only.

    `document` is the dict `extract_kg` builds for `document.json`. The date is
    `_valid_from` (the resolved document date), not the file's own mtime."""
    provenance = document.get("provenance") or {}
    source_type = _text(provenance.get("source_type") or document.get("source_type"))
    path = _text(document.get("path")) or _text(document.get("name"))
    rows = [
        ("Title", _text(document.get("title"))),
        ("Source", f"{path} ({source_type})" if path and source_type else path),
        ("Date", _text(document.get("_valid_from"))),
        ("Author", _text(document.get("author"))),
        ("Project", _text(document.get("project"))),
        ("Area", _text(document.get("area"))),
        ("Tags", _text(document.get("tags"))),
    ]
    return "\n".join(f"{label}: {value}" for label, value in rows if value)


#: The most text the summary call is given, in characters.
SAMPLE_CHARS = 12000
_EVEN_SAMPLES = 10


def _first_paragraph(text: str) -> str:
    """The first paragraph of a chunk: the text up to the first blank line, skipping a
    block that is only Markdown heading lines (a heading says where we are, not what is
    said). A chunk of nothing but headings returns its first heading block."""
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    for block in blocks:
        if not all(line.lstrip().startswith("#") for line in block.splitlines() if line.strip()):
            return block
    return blocks[0] if blocks else ""


def sample_for_summary(chunk_texts: list[str], chunks_meta: dict) -> str:
    """A bounded sample of the document for the summary call.

    The opening and the closing always; then the first paragraph under each
    distinct top-level heading (`header_path`'s first segment). A document with
    no headings contributes ten evenly spaced chunks instead. Filled in document
    order and cut at `SAMPLE_CHARS`, so the call is one call however long the
    document is. The opening and closing are the first paragraph of the first and
    last chunk, and each keeps a guaranteed share of the cap."""
    if not chunk_texts:
        return ""
    last = len(chunk_texts) - 1
    headings = _chunk_headings(chunks_meta)
    picked: set[int] = {0, last}
    if any(headings.values()):
        first_seen: dict[str, int] = {}
        for seq in sorted(headings):
            top = headings[seq].split(" > ", 1)[0]
            if top and top not in first_seen:
                first_seen[top] = seq
        picked |= {seq - 1 for seq in first_seen.values() if 0 <= seq - 1 <= last}
    else:
        count = min(_EVEN_SAMPLES, len(chunk_texts))
        picked = {round(i * last / max(count - 1, 1)) for i in range(count)}

    paragraphs = {index: _first_paragraph(chunk_texts[index]) for index in picked}
    must = sorted({0, last})
    share = SAMPLE_CHARS if len(must) == 1 else SAMPLE_CHARS // 4
    chosen = {index: _fit(paragraphs[index], share) for index in must}
    room = SAMPLE_CHARS - sum(len(text) + 2 for text in chosen.values())
    for index in sorted(picked):
        if index in chosen:
            continue
        if len(paragraphs[index]) + 2 > room:
            continue
        chosen[index] = paragraphs[index]
        room -= len(paragraphs[index]) + 2
    return "\n\n".join(chosen[index] for index in sorted(chosen) if chosen[index])


def _fit(paragraph: str, room: int) -> str:
    """`paragraph` cut to at most `room` characters, on a word boundary when it has one."""
    if len(paragraph) <= room:
        return paragraph
    cut = paragraph[: max(room, 0)]
    head = cut.rsplit(None, 1)[0] if len(cut.split()) > 1 else cut
    return head.rstrip()


#: Bumped when the summary prompt changes, so a cached summary made by the old
#: wording is regenerated (it is part of the cache key).
PROMPT_VERSION = 1
SUMMARY_CHARS = 1200
IDENTIFIER_LIMIT = 25


def _one_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    end = text.find(". ")
    return text[: end + 1] if end != -1 else text


def summary_prompt(schema: dict, sample: str, *, focus: str) -> str:
    """The prompt for the one summary call, built from the schema alone: its
    subject and each class's first sentence. `focus` is the schema's own free
    text, appended when given. The answer is JSON; see `parse_summary`."""
    subject = str(schema.get("subject") or schema.get("description") or "documents").strip()
    classes = []
    for name, decl in (schema.get("entity_types") or {}).items():
        description = _one_sentence((decl or {}).get("description") if isinstance(decl, dict) else "")
        classes.append(f"- {name}: {description}" if description else f"- {name}")
    lines = [
        f"You are reading a document about {subject}.",
        "Say what this document is in two or three sentences.",
        "Then list, as identifiers, the names, numbers, dates and parties that recur in it",
        "and identify instances of these classes:",
        *classes,
    ]
    if focus:
        lines.append(f"Also: {focus}")
    lines += [
        "",
        'Return only JSON: {"summary": "...", "identifiers": ["...", ...]}. No preamble, no code fences.',
        "",
        "DOCUMENT (sampled):",
        sample,
    ]
    return "\n".join(lines)


def parse_summary(answer) -> tuple[str, list[str]] | None:
    """`(summary, identifiers)` from the model's JSON, or None when it is not a
    JSON object. Both end up inside every chunk's prompt, so each is one line
    (whitespace collapsed): model output must not add a line that reads like
    another field. The summary is capped at SUMMARY_CHARS; identifiers keep only
    non-empty strings and are capped at IDENTIFIER_LIMIT."""
    if not isinstance(answer, dict):
        return None
    summary = " ".join(str(answer.get("summary") or "").split())[:SUMMARY_CHARS]
    raw = answer.get("identifiers") or []
    identifiers = [" ".join(v.split()) for v in raw if isinstance(v, str)]
    return summary, [i for i in identifiers if i][:IDENTIFIER_LIMIT]
