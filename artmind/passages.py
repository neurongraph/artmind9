"""Passages: the retrieval unit under a DocChunk (spec 2026-10-31 §2.1).

A DocChunk (~6000 chars) is the extraction and provenance unit; its passages
(~1200 chars) carry every text-retrieval vector. `split` is a pure function of
the chunk text and `split_key()`, so every machine derives identical passages.
No I/O, no Neo4j, no embedding calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from artmind.md_tables import reattach_table_headers

#: Bump whenever the splitting rules change; passages cut under another
#: version are rebuilt by the sweep (`_split_key` on each passage).
SPLIT_VERSION = 1
DEFAULT_CHARS = 1200
OVERLAP_CHARS = 150

_SENTENCE_END = re.compile(r"[.!?][\"')\]]?\s")


@dataclass(frozen=True)
class Passage:
    seq: int          # 1-based
    char_start: int   # offset in the parent chunk's text
    char_end: int
    text: str         # chunk_text[char_start:char_end], with a table header reattached when cut mid-table


def passage_chars() -> int:
    from utils.functions import load_env

    try:
        value = int(load_env().get("ARTMIND_KG_PASSAGE_CHARS", DEFAULT_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_CHARS
    return max(value, 2 * OVERLAP_CHARS)


def split_key() -> str:
    """What a passage was cut under: the rules' version and the target size."""
    return f"{SPLIT_VERSION}:{passage_chars()}"


def passage_id(chunk_id: str, seq: int) -> str:
    return f"{chunk_id}_p{seq:02d}"


def _cut(text: str, start: int, hard_end: int) -> int:
    """Best end in the second half of [start, hard_end): paragraph, line, sentence, word.
    A line break ranks above a sentence so table rows and list items stay whole."""
    lo = start + (hard_end - start) // 2
    window = text[lo:hard_end]
    i = window.rfind("\n\n")
    if i != -1:
        return lo + i + 2
    i = window.rfind("\n")
    if i != -1:
        return lo + i + 1
    last = None
    for m in _SENTENCE_END.finditer(window):
        last = m
    if last is not None:
        return lo + last.end()
    i = max(window.rfind(" "), window.rfind("\n"))
    if i != -1:
        return lo + i + 1
    return hard_end


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _next_start(text: str, start: int, end: int) -> int:
    """Start of the next passage, up to OVERLAP_CHARS before `end`: at a line start
    when the overlap holds one (a table row, so its header can be reattached), else
    at a word start."""
    s = max(end - OVERLAP_CHARS, start + 1)
    nl = text.find("\n", s, end)
    if nl != -1:
        return nl + 1
    while s < end and not text[s - 1].isspace():
        s += 1
    return s


def split(chunk_text: str, chars: int | None = None) -> list[Passage]:
    target = chars or passage_chars()
    n = len(chunk_text)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < n:
        hard_end = min(start + target, n)
        end = n if hard_end == n else _cut(chunk_text, start, hard_end)
        s, e = _trim(chunk_text, start, end)
        if e > s:
            spans.append((s, e))
        if end >= n:
            break
        start = _next_start(chunk_text, start, end)
    texts = reattach_table_headers([chunk_text[s:e] for s, e in spans])
    return [Passage(i, s, e, t) for i, ((s, e), t) in enumerate(zip(spans, texts), start=1)]


def embedding_text(text: str) -> str:
    """The text a passage is embedded as (before the model's task prefix): the text
    alone. `context` (title > header path) is kept for display only -- prepended, it
    is shared by every passage of a document and repeats the heading the text
    already opens with, so siblings embed alike and the one holding the answer
    loses rank (banking_corpus retrieval eval, 2026-11-01)."""
    return text


def passage_rows(chunk: dict, title: str | None) -> list[dict]:
    """One chunk's passages as write rows for `passage_store.write_chunk_passages`."""
    context = " > ".join(p for p in (title, chunk.get("header_path")) if p)
    key = split_key()
    rows = []
    for p in split(chunk.get("text") or ""):
        pid = passage_id(chunk["id"], p.seq)
        props = {
            "id": pid, "chunk_id": chunk["id"], "doc_id": chunk.get("doc_id"),
            "_domain": chunk.get("_domain"), "seq": p.seq,
            "char_start": p.char_start, "char_end": p.char_end, "text": p.text,
            "context": context, "_split_key": key,
        }
        for field in ("valid_from", "valid_to"):
            if chunk.get(field) is not None:
                props[field] = chunk[field]
        rows.append({"id": pid, "chunk_id": chunk["id"], "props": props})
    return rows
