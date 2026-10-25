# Document context for chunk extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every chunk extraction prompt a schema-derived picture of the whole document (metadata, heading outline with the chunk's position, and a cached one-call summary), replacing the opening-lines header from commit cf787bd.

**Architecture:** A new pure module, `artmind/document_context.py`, owns rendering, sampling, the prompt, the cache key and the schema controls, with no I/O and no LLM. `artmind/ingest.py`'s `extract_kg` resolves the metadata before the chunk fan-out and calls one narrow `_summarise_document`, which reads and writes a `document_context.json` staging sidecar and tolerates failure. The chunk prompts receive the rendered block in front of the passage, so `prompt_builder.py` and the templates do not change.

**Tech Stack:** Python 3.14, pytest (`uv run --group dev pytest`), loguru, the existing `_llm_extract` seam, `json`, `hashlib`.

**Spec:** `docs/superpowers/specs/2026-10-25-document-context-design.md`.

**Working branch:** `fix/extraction-failures-contracts-study`. Run every command from the repo root `/Users/surjitdas/projects/artmind9`.

---

## File Structure

- **Create** `artmind/document_context.py`: the pure part. Settings parsing, outline, metadata, sampling, summary prompt, rendering with the length cap, cache key, and the legacy-header alias. No I/O, no LLM.
- **Create** `test/test_document_context.py`: unit tests for the pure module.
- **Modify** `artmind/ingest.py`: remove the cf787bd helpers (`DOCUMENT_HEADER_CHARS`, `DOCUMENT_HEADER_END`, `build_document_header`, `strip_document_header`, `with_document_header`, `_document_header_settings`), add `_summarise_document`, `_read_document_context`, `_write_document_context`, `with_document_context`, `strip_document_context`, and rewire `extract_kg`'s header block (around `ingest.py:3064-3083`) and the `_step` call (around `ingest.py:3159`).
- **Modify** `test/test_document_header.py`: rename to `test/test_document_context_wiring.py` via `git mv`, and move its assertions onto the new helpers.
- **Modify** `test/test_vault_chunk_cache_merge.py:51-56` and `test/test_extraction_truncation.py` (the `strip_document_header` call): switch to `strip_document_context`.
- **Modify** `docs/vault.md`: a paragraph on what a chunk prompt receives and the privacy note.
- **Modify** `artmind/skills/artmind-create-schema/SKILL.md`: the `document_context:` schema key.

Each module keeps one job: `document_context.py` decides what the context says, `ingest.py` decides when and where it is computed and stored.

---

## Conventions used below

- Test commands: `uv run --group dev pytest <file> -q -p no:cacheprovider`.
- The LLM is always replaced by the `_llm_extract` seam, as in `test/test_document_header.py` and `test/test_extraction_truncation.py`. The conftest autouse fixture makes any real call fail loudly.
- `_setup_extract_env(monkeypatch, tmp_path, n_chunks)` from `test/test_ingest_concurrency.py` builds a minimal `extract_kg` harness. It writes `schemas/testdom_schema.yaml` containing only `entities_prompt: p`. Tests that need `subject`, `entity_types` or `document_context` overwrite that file.

---

### Task 1: Settings parsing (`document_context` key and legacy `document_header`)

**Files:**
- Create: `artmind/document_context.py`
- Test: `test/test_document_context.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_document_context.py
"""The pure part of the document context (spec 2026-10-25-document-context-design.md).

No LLM, no Neo4j, no files: every function here takes plain values and returns
plain values, so the tests assert on the returned values directly."""
import pytest

from artmind.document_context import context_settings


def test_no_setting_means_everything_on_with_the_default_cap():
    s = context_settings({})
    assert s.enabled is True
    assert s.summary is True
    assert s.focus == ""
    assert s.cap == 2000


def test_document_context_false_turns_the_whole_block_off():
    s = context_settings({"document_context": False})
    assert s.enabled is False
    assert s.summary is False


def test_summary_false_keeps_metadata_and_outline_without_a_call():
    s = context_settings({"document_context": {"summary": False}})
    assert s.enabled is True
    assert s.summary is False


def test_focus_text_is_read_and_stripped():
    s = context_settings({"document_context": {"focus": "  Parties and effective dates.  "}})
    assert s.focus == "Parties and effective dates."


def test_legacy_document_header_false_maps_to_document_context_false():
    s = context_settings({"document_header": False})
    assert s.enabled is False


def test_legacy_document_header_chars_sets_the_cap():
    s = context_settings({"document_header": {"chars": 500}})
    assert s.enabled is True
    assert s.cap == 500


def test_document_context_wins_over_the_legacy_key_when_both_are_given():
    s = context_settings({"document_header": False, "document_context": {"focus": "x"}})
    assert s.enabled is True and s.focus == "x"


def test_a_bad_chars_value_falls_back_to_the_default_cap():
    assert context_settings({"document_header": {"chars": "lots"}}).cap == 2000
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider`
Expected: collection error, `ModuleNotFoundError: No module named 'artmind.document_context'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# artmind/document_context.py
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

#: The block's length cap, in characters (`document_context.cap` in a schema can
#: override it through the legacy `document_header: {chars: N}` alias).
DEFAULT_CAP = 2000


@dataclass(frozen=True)
class ContextSettings:
    enabled: bool
    summary: bool
    focus: str
    cap: int


def context_settings(schema: dict) -> ContextSettings:
    """What this schema asks for. `document_context` is the key; the older
    `document_header` (commit cf787bd) is read when `document_context` is absent:
    `false` turns the block off, `{chars: N}` sets the cap."""
    if "document_context" in schema:
        setting = schema["document_context"]
        if setting is False:
            return ContextSettings(enabled=False, summary=False, focus="", cap=DEFAULT_CAP)
        if isinstance(setting, dict):
            return ContextSettings(
                enabled=True,
                summary=setting.get("summary", True) is not False,
                focus=str(setting.get("focus") or "").strip(),
                cap=DEFAULT_CAP,
            )
        return ContextSettings(enabled=True, summary=True, focus="", cap=DEFAULT_CAP)

    legacy = schema.get("document_header", True)
    if legacy is False:
        return ContextSettings(enabled=False, summary=False, focus="", cap=DEFAULT_CAP)
    cap = DEFAULT_CAP
    if isinstance(legacy, dict):
        try:
            cap = max(int(legacy.get("chars", DEFAULT_CAP)), 0)
        except (TypeError, ValueError):
            cap = DEFAULT_CAP
    return ContextSettings(enabled=True, summary=True, focus="", cap=cap)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add artmind/document_context.py test/test_document_context.py
git commit -m "feat(context): parse document_context and legacy document_header settings

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: Heading outline with the chunk's position

**Files:**
- Modify: `artmind/document_context.py`
- Test: `test/test_document_context.py`

The outline is built from `chunks_meta.json`, which `_persist_chunks` writes as `{"001": {"header_path": "...", ...}, "_body_sha256": ...}`. Only `header_path` and the chunk count are needed.

- [ ] **Step 1: Write the failing tests**

```python
# append to test/test_document_context.py
from artmind.document_context import build_outline


def test_outline_names_the_chunks_heading_and_its_position():
    meta = {
        "001": {"header_path": ""},
        "002": {"header_path": "Schedule 2 > Rates"},
        "003": {"header_path": "Schedule 2 > Rates"},
    }
    assert build_outline(meta, 2, 3) == "You are in: Schedule 2 > Rates (chunk 2 of 3)"


def test_a_chunk_under_no_heading_gets_only_its_position():
    assert build_outline({"001": {"header_path": ""}}, 1, 4) == "You are in: chunk 1 of 4"


def test_missing_chunks_meta_still_gives_the_position():
    assert build_outline({}, 5, 9) == "You are in: chunk 5 of 9"


def test_the_outline_lists_the_document_headings_in_order_when_given_them():
    meta = {
        "001": {"header_path": "Intro"},
        "002": {"header_path": "Schedule 2 > Rates"},
        "003": {"header_path": "Schedule 2 > Fees"},
    }
    text = build_outline(meta, 2, 3, document_outline=True)
    assert text.splitlines() == [
        "You are in: Schedule 2 > Rates (chunk 2 of 3)",
        "Document outline:",
        "  - Intro",
        "  - Schedule 2 > Rates",
        "  - Schedule 2 > Fees",
    ]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k outline`
Expected: `ImportError: cannot import name 'build_outline'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# append to artmind/document_context.py

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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k outline`
Expected: `3 passed` for the first three plus the outline test, `4 passed` total for the selection.

- [ ] **Step 5: Commit**

```bash
git add artmind/document_context.py test/test_document_context.py
git commit -m "feat(context): heading outline with the chunk's position

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: Metadata block

**Files:**
- Modify: `artmind/document_context.py`
- Test: `test/test_document_context.py`

Fields come from the document dict `extract_kg` builds (`ingest.py` around 3390-3470): `name`, `path`, `title`, `project`, `area`, `tags`, `author`, `date`, `_valid_from`, `_valid_time_source`, and `source_type` (inside `provenance`). Only fields with a value are rendered.

- [ ] **Step 1: Write the failing tests**

```python
# append to test/test_document_context.py
from artmind.document_context import build_metadata


def test_metadata_renders_the_fields_that_have_values_in_a_fixed_order():
    document = {
        "name": "CCN001.pdf",
        "path": "contracts/CCN001.pdf",
        "title": "Change Control Notice 001",
        "project": "Alpha",
        "area": "Legal",
        "tags": ["amendment", "rates"],
        "author": "J. Smith",
        "_valid_from": "2026-03-01",
        "_valid_time_source": "property",
        "provenance": {"source_type": "pdf"},
    }
    assert build_metadata(document).splitlines() == [
        "Title: Change Control Notice 001",
        "Source: contracts/CCN001.pdf (pdf)",
        "Date: 2026-03-01",
        "Author: J. Smith",
        "Project: Alpha",
        "Area: Legal",
        "Tags: amendment, rates",
    ]


def test_metadata_skips_what_is_missing_and_never_renders_empty_lines():
    text = build_metadata({"name": "a.md", "path": "notes/a.md"})
    assert text == "Source: notes/a.md"


def test_metadata_falls_back_to_the_file_name_when_there_is_no_path():
    assert build_metadata({"name": "a.md"}) == "Source: a.md"


def test_metadata_is_empty_for_an_empty_document():
    assert build_metadata({}) == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k metadata`
Expected: `ImportError: cannot import name 'build_metadata'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# append to artmind/document_context.py

def _text(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if str(v).strip())
    return str(value).strip() if value is not None else ""


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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k metadata`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add artmind/document_context.py test/test_document_context.py
git commit -m "feat(context): document metadata block from the resolved fields

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: Sampling the document for the summary

**Files:**
- Modify: `artmind/document_context.py`
- Test: `test/test_document_context.py`

The summary call reads: the outline, the opening, the closing, and the first paragraph under each top-level heading (or ten evenly spaced chunks when there are no headings), capped at `SAMPLE_CHARS = 12000`. Input is the list of chunk texts plus `chunks_meta`.

- [ ] **Step 1: Write the failing tests**

```python
# append to test/test_document_context.py
from artmind.document_context import SAMPLE_CHARS, sample_for_summary


def _chunks(texts):
    return list(texts)


def test_a_short_document_is_sampled_whole():
    chunks = _chunks(["Opening para.", "Middle para.", "Closing para."])
    sample = sample_for_summary(chunks, {})
    assert "Opening para." in sample and "Middle para." in sample and "Closing para." in sample


def test_the_sample_takes_the_first_paragraph_under_each_top_level_heading():
    chunks = _chunks(["# Intro\n\nIntro text.", "## Rates\n\nRates detail.\n\nMore rates."])
    meta = {"001": {"header_path": "Intro"}, "002": {"header_path": "Intro > Rates"}}
    sample = sample_for_summary(chunks, meta)
    assert "Intro text." in sample
    assert "Rates detail." in sample


def test_without_headings_ten_evenly_spaced_chunks_are_sampled():
    chunks = [f"chunk {i} text." for i in range(1, 41)]
    sample = sample_for_summary(chunks, {})
    assert "chunk 1 text." in sample and "chunk 40 text." in sample
    assert sample.count("text.") == 10


def test_the_sample_never_exceeds_the_cap():
    chunks = [("word " * 2000) for _ in range(20)]
    assert len(sample_for_summary(chunks, {})) <= SAMPLE_CHARS


def test_an_empty_document_gives_an_empty_sample():
    assert sample_for_summary([], {}) == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k "sample or sampled or cap or empty_document"`
Expected: `ImportError: cannot import name 'sample_for_summary'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# append to artmind/document_context.py

#: The most text the summary call is given, in characters.
SAMPLE_CHARS = 12000
_EVEN_SAMPLES = 10


def _first_paragraph(text: str) -> str:
    """The text up to the first blank line, with leading '#' heading markers kept
    as they are: a heading line is part of what the paragraph says."""
    for block in text.split("\n\n"):
        if block.strip():
            return block.strip()
    return ""


def sample_for_summary(chunk_texts: list[str], chunks_meta: dict) -> str:
    """A bounded sample of the document for the summary call.

    The opening and the closing always; then the first paragraph under each
    distinct top-level heading (`header_path`'s first segment). A document with
    no headings contributes ten evenly spaced chunks instead. Filled in document
    order and cut at `SAMPLE_CHARS`, so the call is one call however long the
    document is."""
    if not chunk_texts:
        return ""
    headings = _chunk_headings(chunks_meta)
    picked: list[tuple[int, str]] = []
    if headings and any(headings.values()):
        first_seen: dict[str, int] = {}
        for seq in sorted(headings):
            top = headings[seq].split(" > ", 1)[0] if headings[seq] else ""
            if top and top not in first_seen:
                first_seen[top] = seq
        picked = [(0, chunk_texts[0])]
        picked += [(seq - 1, chunk_texts[seq - 1]) for seq in first_seen.values() if seq - 1 < len(chunk_texts)]
        picked.append((len(chunk_texts) - 1, chunk_texts[-1]))
    else:
        step = max(len(chunk_texts) / _EVEN_SAMPLES, 1)
        picked = [(int(i * step), chunk_texts[int(i * step)]) for i in range(min(_EVEN_SAMPLES, len(chunk_texts)))]
        picked.append((len(chunk_texts) - 1, chunk_texts[-1]))

    seen: set[int] = set()
    parts: list[str] = []
    total = 0
    for index, text in sorted(picked, key=lambda pair: pair[0]):
        if index in seen:
            continue
        seen.add(index)
        paragraph = _first_paragraph(text)
        if total + len(paragraph) + 2 > SAMPLE_CHARS:
            break
        parts.append(paragraph)
        total += len(paragraph) + 2
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k "sample or sampled or cap or empty_document"`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add artmind/document_context.py test/test_document_context.py
git commit -m "feat(context): bounded document sample for the summary call

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: The summary prompt and its parser

**Files:**
- Modify: `artmind/document_context.py`
- Test: `test/test_document_context.py`

The prompt is built from the schema: `subject`, and each class's name with its one-line description (the first sentence of `description`). Output is JSON `{"summary": str, "identifiers": [str]}`, with the summary capped at 1200 characters and identifiers at 25.

- [ ] **Step 1: Write the failing tests**

```python
# append to test/test_document_context.py
from artmind.document_context import parse_summary, summary_prompt


SCHEMA = {
    "subject": "legal contracts and their amendments",
    "entity_types": {
        "AGREEMENT": {"kind": "recurrent", "description": "A contract between parties. Captures its title."},
        "AMENDMENT": {"kind": "occurrent", "description": "A change notice that alters an agreement."},
    },
}


def test_the_prompt_names_the_schema_subject_and_each_class_with_its_first_sentence():
    prompt = summary_prompt(SCHEMA, "a sample", focus="")
    assert "legal contracts and their amendments" in prompt
    assert "AGREEMENT: A contract between parties." in prompt
    assert "AMENDMENT: A change notice that alters an agreement." in prompt
    assert "Captures its title." not in prompt, "only each class's first sentence"


def test_the_prompt_carries_the_sample_and_asks_for_json():
    prompt = summary_prompt(SCHEMA, "THE SAMPLE TEXT", focus="")
    assert "THE SAMPLE TEXT" in prompt
    assert '"summary"' in prompt and '"identifiers"' in prompt


def test_a_schema_focus_is_appended_to_the_prompt():
    prompt = summary_prompt(SCHEMA, "s", focus="Watch for effective dates.")
    assert "Watch for effective dates." in prompt


def test_the_prompt_is_built_from_the_schema_not_from_a_domain_name():
    other = {"subject": "journal entries", "entity_types": {"EVENT": {"kind": "occurrent", "description": "Something that happened."}}}
    prompt = summary_prompt(other, "s", focus="")
    assert "journal entries" in prompt and "EVENT: Something that happened." in prompt
    assert "AGREEMENT" not in prompt


def test_parse_summary_reads_the_json_answer():
    summary, identifiers = parse_summary({"summary": "A rate change.", "identifiers": ["CCN001", "Alpha Ltd"]})
    assert summary == "A rate change." and identifiers == ["CCN001", "Alpha Ltd"]


def test_parse_summary_caps_the_lengths():
    summary, identifiers = parse_summary({"summary": "x" * 5000, "identifiers": [f"id{i}" for i in range(40)]})
    assert len(summary) == 1200
    assert len(identifiers) == 25


def test_parse_summary_rejects_a_list_answer_and_keeps_only_strings():
    assert parse_summary(["not", "a", "dict"]) is None
    summary, identifiers = parse_summary({"summary": "ok", "identifiers": ["a", 3, None, "b"]})
    assert identifiers == ["a", "b"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k "prompt or summary_prompt or parse_summary or schema_focus or built_from"`
Expected: `ImportError: cannot import name 'parse_summary'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# append to artmind/document_context.py

#: Bumped when the summary prompt changes, so a cached summary made by the old
#: wording is regenerated (it is part of the cache key, Task 6).
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
    JSON object. The summary is capped at SUMMARY_CHARS; identifiers keep only
    strings and are capped at IDENTIFIER_LIMIT."""
    if not isinstance(answer, dict):
        return None
    summary = " ".join(str(answer.get("summary") or "").split())[:SUMMARY_CHARS]
    raw = answer.get("identifiers") or []
    identifiers = [str(v).strip() for v in raw if isinstance(v, str) and str(v).strip()]
    return summary, identifiers[:IDENTIFIER_LIMIT]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider`
Expected: all tests in the file pass (`~24 passed`).

- [ ] **Step 5: Commit**

```bash
git add artmind/document_context.py test/test_document_context.py
git commit -m "feat(context): schema-derived summary prompt and its parser

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: Rendering with the cap, and the cache key

**Files:**
- Modify: `artmind/document_context.py`
- Test: `test/test_document_context.py`

`render_context` joins the metadata, outline and summary into one block, capped at `cap` characters. Trimming order: the outline's "Document outline" list goes first, then metadata lines from the bottom up; the summary is never cut mid-sentence and is kept whole, or dropped last. `context_key` hashes (content hash, domain schema file hash, model, `PROMPT_VERSION`).

- [ ] **Step 1: Write the failing tests**

```python
# append to test/test_document_context.py
from artmind.document_context import context_key, render_context


def test_render_joins_metadata_outline_and_summary_under_a_heading():
    block = render_context(
        metadata="Title: Notice",
        outline="You are in: Rates (chunk 2 of 3)",
        summary="A rate notice. Identifiers: CCN001.",
        cap=2000,
    )
    assert block.splitlines()[0].startswith("DOCUMENT CONTEXT")
    assert "Title: Notice" in block
    assert "You are in: Rates (chunk 2 of 3)" in block
    assert "A rate notice." in block
    assert block.rstrip().endswith("PASSAGE TO EXTRACT FROM:")


def test_render_is_empty_when_there_is_nothing_to_say():
    assert render_context(metadata="", outline="", summary="", cap=2000) == ""


def test_render_drops_the_outline_list_before_the_summary_when_over_the_cap():
    outline = "You are in: A (chunk 1 of 2)\nDocument outline:\n" + "\n".join(f"  - heading {i} " + "x" * 80 for i in range(30))
    block = render_context(metadata="Title: T", outline=outline, summary="The summary stays.", cap=600)
    assert len(block) <= 600
    assert "The summary stays." in block
    assert "Document outline:" not in block


def test_render_drops_metadata_lines_from_the_bottom_when_still_over_the_cap():
    metadata = "\n".join(f"Tag{i}: " + "value " * 20 for i in range(40))
    block = render_context(metadata=metadata, outline="You are in: x (chunk 1 of 1)", summary="S.", cap=500)
    assert len(block) <= 500
    assert "Tag0:" in block and "You are in: x" in block and "S." in block


def test_the_summary_is_never_cut_mid_sentence_when_the_cap_is_tight():
    summary = "First sentence here. Second sentence that is long " + "word " * 100 + "end."
    block = render_context(metadata="", outline="", summary=summary, cap=120)
    assert "Second sentence" not in block
    assert "First sentence here." in block


def test_context_key_changes_with_each_input_and_is_stable_otherwise():
    base = context_key(content_sha="c1", schema_sha="s1", model="m1")
    assert base == context_key(content_sha="c1", schema_sha="s1", model="m1")
    assert base != context_key(content_sha="c2", schema_sha="s1", model="m1")
    assert base != context_key(content_sha="c1", schema_sha="s2", model="m1")
    assert base != context_key(content_sha="c1", schema_sha="s1", model="m2")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k "render or context_key"`
Expected: `ImportError: cannot import name 'render_context'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# append to artmind/document_context.py
import hashlib

#: The block's closing line: what follows it is the passage to extract from.
PASSAGE_LINE = "PASSAGE TO EXTRACT FROM:"
_HEADING = (
    "DOCUMENT CONTEXT (only for naming and identifying things consistently -- "
    "do not extract from this block):"
)


def _assemble(metadata: str, outline: str, summary: str) -> str:
    parts = [_HEADING]
    if metadata:
        parts += metadata.splitlines()
    if outline:
        parts += outline.splitlines()
    if summary:
        parts += ["Summary:", summary]
    parts.append(PASSAGE_LINE)
    return "\n".join(parts)


def _trim_outline(outline: str) -> str:
    """The outline without its 'Document outline:' list (the chunk's own line stays)."""
    return "\n".join(line for line in outline.splitlines() if not line.startswith(("Document outline:", "  - ")))


def render_context(*, metadata: str, outline: str, summary: str, cap: int) -> str:
    """The one block every chunk prompt carries, at most `cap` characters.

    Over the cap: the document's outline list goes first, then metadata lines
    from the bottom up, then (only if still over) the whole outline. The
    summary is kept whole or, if nothing else fits, dropped; it is never cut
    mid-sentence. '' when there is nothing to say."""
    if not (metadata or outline or summary):
        return ""
    block = _assemble(metadata, outline, summary)
    if len(block) <= cap:
        return block
    block = _assemble(metadata, _trim_outline(outline), summary)
    meta_lines = metadata.splitlines()
    while len(block) > cap and meta_lines:
        meta_lines.pop()
        block = _assemble("\n".join(meta_lines), _trim_outline(outline), summary)
    if len(block) > cap:
        block = _assemble("\n".join(meta_lines), "", summary)
    if len(block) > cap and summary:
        sentences = summary.split(". ")
        kept = ""
        for sentence in sentences:
            candidate = f"{kept}. {sentence}" if kept else sentence
            if len(_assemble("", "", candidate)) > cap:
                break
            kept = candidate
        block = _assemble("", "", kept) if kept else _assemble("", "", "")
        if len(block) > cap:
            block = _assemble("", "", "")
    return block if block.count("\n") > 1 else ""


def context_key(*, content_sha: str, schema_sha: str, model: str) -> str:
    """Identifies one summary: the document's content, its own domain schema
    file, the model that wrote it and the prompt's version. Any of them changing
    means the summary is regenerated."""
    digest = hashlib.sha256()
    for part in (content_sha, schema_sha, model, str(PROMPT_VERSION)):
        digest.update(part.encode("utf-8") + b"\0")
    return digest.hexdigest()
```

Note: `render_context` returns `""` when the assembled block has no content lines beyond the heading and the closing line, so an empty summary, metadata and outline together yield nothing.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider`
Expected: all tests pass.

If `test_render_drops_metadata_lines_from_the_bottom_when_still_over_the_cap` fails because the summary line itself is trimmed, the cap test is correct and `_assemble` needs no change: lower the metadata line count in the test's fixture (the 40 lines of 6 words each exceed 500 characters only together with the outline). Fix the test fixture, not the trimming order.

- [ ] **Step 5: Commit**

```bash
git add artmind/document_context.py test/test_document_context.py
git commit -m "feat(context): render the context block within its cap; cache key

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: Summary cache, sidecar read and write (`ingest.py`)

**Files:**
- Modify: `artmind/ingest.py` (add the helpers after `_document_header_settings`, which is removed in Task 9)
- Test: `test/test_document_context_cache.py`

The sidecar is `document_context.json` in the staging folder. `write_staging` (`ingest.py:2195`) is the atomic-swap writer; `_summarise_document` writes through it (`write_staging(folder, {"document_context.json": data}, default=...)`). Read it back with a plain JSON read, returning `None` if absent or unreadable.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_document_context_cache.py
"""The summary's sidecar and cache (spec §4): one call per document and key,
reused on resume, regenerated when its key changes. The LLM is the `_llm_extract`
seam; assertions are on the calls made and the files written."""
import json

import pytest
from test_ingest_concurrency import _setup_extract_env

import artmind.ingest as ing
from artmind import document_context as dc


SUMMARY_ANSWER = {"summary": "A rate notice for Alpha.", "identifiers": ["CCN001"]}


@pytest.fixture
def summarising(monkeypatch, tmp_path):
    """A harness whose summary call is recorded and answered."""
    file_result = _setup_extract_env(monkeypatch, tmp_path, 2)
    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: "E")
    monkeypatch.setattr(ing, "build_properties_prompt", lambda *a, **k: "P")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda *a, **k: "R")
    calls = []

    def llm(step_name, model, prompt, debug_dir):
        if step_name.endswith("_summary"):
            calls.append(prompt)
            return SUMMARY_ANSWER, True
        if step_name.endswith("_entities"):
            return [{"id": "e1", "entity_class": "Thing", "type": "t", "name": step_name}], True
        return [], True

    monkeypatch.setattr(ing, "_llm_extract", llm)
    return file_result, calls


def test_the_first_extraction_makes_one_summary_call_and_writes_the_sidecar(summarising):
    file_result, calls = summarising
    folder = ing.extract_kg(file_result, "testdom", max_workers=1)

    assert len(calls) == 1
    sidecar = json.loads((folder / "document_context.json").read_text())
    assert sidecar["summary"] == "A rate notice for Alpha."
    assert sidecar["identifiers"] == ["CCN001"]
    assert sidecar["key"]


def test_a_resumed_extraction_reuses_the_sidecar_and_makes_no_call(summarising):
    file_result, calls = summarising
    ing.extract_kg(file_result, "testdom", max_workers=1)
    calls.clear()
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert calls == []


def test_a_schema_change_regenerates_the_summary(summarising, monkeypatch):
    file_result, calls = summarising
    ing.extract_kg(file_result, "testdom", max_workers=1)
    calls.clear()
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "testdom_schema.yaml"
    schema_file.write_text(schema_file.read_text() + "\n# edited\n")
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert len(calls) == 1


def test_a_content_change_regenerates_the_summary(summarising):
    file_result, calls = summarising
    ing.extract_kg(file_result, "testdom", max_workers=1)
    calls.clear()
    file_result["sha256"] = "sha-changed"
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert len(calls) == 1


def test_a_corrupt_sidecar_is_regenerated_not_fatal(summarising):
    file_result, calls = summarising
    folder = ing.extract_kg(file_result, "testdom", max_workers=1)
    (folder / "document_context.json").write_text("{not json")
    calls.clear()
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert len(calls) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context_cache.py -q -p no:cacheprovider`
Expected: the first test fails with `AssertionError` (no sidecar written, no summary call).

- [ ] **Step 3: Write the minimal implementation**

Add these to `artmind/ingest.py`, directly after `_document_header_settings` (that function is removed in Task 9). The summary call goes through `_llm_extract`, so it inherits the truncation retry.

```python
# ── document context: one summary per document (spec 2026-10-25) ───────────

#: The staging sidecar that holds the document's summary and its cache key.
DOCUMENT_CONTEXT_FILE = "document_context.json"


def _read_document_context(doc_kg_dir: Path) -> dict | None:
    """The staged summary sidecar, or None when absent or unreadable."""
    try:
        data = json.loads((doc_kg_dir / DOCUMENT_CONTEXT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _summarise_document(
    *,
    doc_kg_dir: Path,
    key: str,
    schema: dict,
    sample: str,
    focus: str,
    model: str,
    debug_dir: Path,
) -> dict | None:
    """The document's summary, from the sidecar when its key matches, else one
    call. Returns {"key", "summary", "identifiers"} or None when the call fails;
    a failure is logged and never fails the file (spec §4)."""
    cached = _read_document_context(doc_kg_dir)
    if cached and cached.get("key") == key:
        return cached
    prompt = dc.summary_prompt(schema, sample, focus=focus)
    try:
        answer, ok = _llm_extract("document_summary", model, prompt, debug_dir)
    except Exception as e:  # a TruncatedAnswer or a provider error
        logger.warning("Document summary unavailable for {} ({}); chunks extract with metadata and outline only", doc_kg_dir.name, e)
        return None
    parsed = dc.parse_summary(answer) if ok else None
    if parsed is None:
        logger.warning("Document summary unusable for {}; chunks extract with metadata and outline only", doc_kg_dir.name)
        return None
    summary, identifiers = parsed
    data = {"key": key, "summary": summary, "identifiers": identifiers}
    try:
        write_staging(doc_kg_dir, {DOCUMENT_CONTEXT_FILE: data}, default=None)
    except OSError as e:  # the chunks still get the summary; only the cache is lost
        logger.warning("Could not cache the document summary for {} ({})", doc_kg_dir.name, e)
    logger.debug("Document summary for {}: {}", doc_kg_dir.name, summary)
    return data
```

Two details the code above depends on, both checked against the current code:

- `_llm_extract` returns `(answer, ok)`. `extract_with_retry` returns the parsed value, not text, so `answer` is already the JSON object. The `parse_summary` call above relies on that.
- `write_staging`'s `default=` argument is the existing one (`ingest.py:2195`). Passing `None` leaves other staged files alone, as the docstring says: "Other entries in the folder ... are carried over unchanged."

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context_cache.py -q -p no:cacheprovider`
Expected: `5 passed` once Task 8 wires the call into `extract_kg`. Before Task 8, the first test still fails, which is correct: the function exists but nothing calls it. Run the unit tests (`test/test_document_context.py`) to confirm nothing else moved.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_document_context_cache.py
git commit -m "feat(context): cached one-call document summary and its sidecar

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: Wire metadata, outline and summary into `extract_kg`

**Files:**
- Modify: `artmind/ingest.py` (the header block near lines 3064-3083 and the `_document_header` call inside `_step` near line 3159; add `with_document_context` and `strip_document_context`)
- Test: `test/test_document_context_wiring.py` (new file; `test/test_document_header.py` is retired in Task 9)

The approach: `extract_kg` builds the metadata from the same `document` dict it writes at the end (so the two never disagree), reads the chunks and sections, asks `_summarise_document` once when the schema allows it, and renders one block per chunk. `with_document_context(block, passage)` puts the block in front of the passage. `strip_document_context` removes it for tests and for any caller that wants the passage back.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_document_context_wiring.py
"""The document context reaches every chunk's prompts (spec §3, §5), checked on
the prompt text the extraction actually sends."""
import json
from pathlib import Path

import yaml
from test_ingest_concurrency import _setup_extract_env

import artmind.ingest as ing

OPENING = "CHANGE CONTROL NOTE CCN001. Effective 2026-03-01. Parties: Alpha Ltd and Beta plc."


def _harness(monkeypatch, tmp_path, *, schema_extra=None, n_chunks=3):
    file_result = _setup_extract_env(monkeypatch, tmp_path, n_chunks)
    chunks = Path(file_result["chunks_dir"])
    (chunks / "chunk_001.md").write_text(OPENING + "\n\nDefinitions follow.", encoding="utf-8")
    (chunks / "chunk_002.md").write_text("Clause 7. This Notice supersedes the earlier schedule.", encoding="utf-8")
    (chunks / "chunk_003.md").write_text("Signed by both parties.", encoding="utf-8")
    (chunks / "chunks_meta.json").write_text(json.dumps({
        "001": {"header_path": ""}, "002": {"header_path": "Schedule 2 > Rates"}, "003": {"header_path": ""},
    }))
    schema = {
        "subject": "contracts", "entity_types": {"AGREEMENT": {"kind": "recurrent", "description": "A contract."}},
        **(schema_extra or {}),
    }
    (ing.DOMAIN_SCHEMAS_DIR / "testdom_schema.yaml").write_text(yaml.safe_dump(schema))
    seen = {}

    def llm(step_name, model, prompt, debug_dir):
        seen[step_name] = prompt
        if step_name.endswith("_summary"):
            return {"summary": "A change notice for Alpha.", "identifiers": ["CCN001"]}, True
        if step_name.endswith("_entities"):
            return [{"id": "e1", "entity_class": "AGREEMENT", "type": "t", "name": step_name}], True
        return [], True

    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: text)
    monkeypatch.setattr(ing, "build_properties_prompt", lambda text, ents, schema, vocabulary=None, anchor_date=None: text)
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: text)
    monkeypatch.setattr(ing, "_llm_extract", llm)
    return file_result, seen


def test_every_chunk_step_sees_the_summary_the_outline_and_the_metadata(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)

    for step in ("chunk_002_entities", "chunk_003_entities", "chunk_002_properties", "chunk_002_relationships"):
        assert "A change notice for Alpha." in seen[step], step
    assert "You are in: Schedule 2 > Rates (chunk 2 of 3)" in seen["chunk_002_entities"]
    assert "Source:" in seen["chunk_002_entities"]


def test_the_passage_is_still_the_chunk_text_and_nothing_else(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert ing.strip_document_context(seen["chunk_002_entities"]) == "Clause 7. This Notice supersedes the earlier schedule."


def test_the_summary_is_one_call_for_the_whole_document(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path, n_chunks=3)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert [s for s in seen if s.endswith("_summary")] == ["chunk_001_summary"] or \
        [s for s in seen if s.endswith("_summary")] == ["document_summary"]


def test_document_context_false_removes_the_block_and_the_call(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path, schema_extra={"document_context": False})
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert not any(s.endswith("_summary") or s == "document_summary" for s in seen)
    assert "DOCUMENT CONTEXT" not in seen["chunk_002_entities"]


def test_summary_false_keeps_metadata_and_outline_without_a_call(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path, schema_extra={"document_context": {"summary": False}})
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert "document_summary" not in seen
    assert "You are in: Schedule 2 > Rates" in seen["chunk_002_entities"]
    assert "A change notice" not in seen["chunk_002_entities"]


def test_focus_text_reaches_the_summary_prompt(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path, schema_extra={"document_context": {"focus": "Watch the effective dates."}})
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert "Watch the effective dates." in seen["document_summary"]


def test_a_failing_summary_call_leaves_the_chunks_going_and_the_file_staged(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path)
    real = ing._llm_extract

    def failing_summary(step_name, model, prompt, debug_dir):
        if step_name == "document_summary":
            raise RuntimeError("summary provider down")
        return real(step_name, model, prompt, debug_dir)

    monkeypatch.setattr(ing, "_llm_extract", failing_summary)
    folder = ing.extract_kg(file_result, "testdom", max_workers=1)

    assert folder is not None, "the file stages"
    assert "You are in: Schedule 2 > Rates" in seen["chunk_002_entities"]
    assert "A change notice" not in seen["chunk_002_entities"]
    assert not (folder / "document_context.json").exists()


def test_the_legacy_document_header_key_still_switches_the_block_off(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path, schema_extra={"document_header": False})
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert "DOCUMENT CONTEXT" not in seen["chunk_002_entities"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context_wiring.py -q -p no:cacheprovider`
Expected: failures on the summary and outline assertions, plus `AttributeError: module 'artmind.ingest' has no attribute 'strip_document_context'`.

- [ ] **Step 3: Write the minimal implementation**

In `artmind/ingest.py`, add the two public helpers next to the summary functions from Task 7:

```python
from artmind import document_context as dc  # add to the imports near the top of ingest.py


def with_document_context(block: str, text: str) -> str:
    """`text` as the prompt builders take it: the context block in front, if any."""
    return f"{block}\n\n{text}" if block else text


def strip_document_context(text: str) -> str:
    """`text` without a leading context block: the passage a chunk step was given."""
    head, sep, passage = text.partition(dc.PASSAGE_LINE + "\n\n")
    return passage if sep and head.startswith("DOCUMENT CONTEXT") else text
```

Then replace the header block in `extract_kg` (the code starting `# The document header every chunk's prompts carry` and ending with the `header_sections = {}` fallback, currently around `ingest.py:3064-3083`) with:

```python
    # The document context every chunk's prompts carry (spec 2026-10-25): the
    # document's metadata, its heading outline with this chunk's position, and
    # a summary made once for the whole document. Built here, before the chunk
    # fan-out; the metadata is the same dict `document.json` is written from.
    context_settings = dc.context_settings(schema)
    try:
        header_sections = json.loads((chunks_dir / "chunks_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        header_sections = {}
    chunk_texts = [f.read_text(encoding="utf-8") for f in chunk_files] if context_settings.enabled else []
    context_metadata = dc.build_metadata({
        **{"name": registered_path.name, "path": canonical_path(registered_path)},
        **(file_result.get("provenance") or {}),
        **document_dates,
    }) if context_settings.enabled else ""
    context_summary = None
    if context_settings.enabled and context_settings.summary:
        content_sha = (file_result.get("provenance") or {}).get("content_sha256") or doc_sha256
        schema_file_sha = hashlib.sha256(domain_schema_file.read_bytes()).hexdigest()
        context_summary = _summarise_document(
            doc_kg_dir=doc_kg_dir,
            key=dc.context_key(content_sha=content_sha, schema_sha=schema_file_sha, model=text_model),
            schema=schema,
            sample=dc.sample_for_summary(chunk_texts, header_sections),
            focus=context_settings.focus,
            model=text_model,
            debug_dir=doc_kg_dir,
        )
    summary_text = context_summary["summary"] if context_summary else ""

    def _document_context(seq: int) -> str:
        if not context_settings.enabled:
            return ""
        return dc.render_context(
            metadata=context_metadata,
            outline=dc.build_outline(header_sections, seq, chunk_count, document_outline=True),
            summary=summary_text,
            cap=context_settings.cap,
        )
```

Use `document_dates` as the valid-time source: it is the value `extract_kg` already resolves before the chunk fan-out (`ingest.py` around line 2992), and `document.update(document_dates)` writes it later.

Finally, in the `_step` helper inside `_process_chunk`, replace the `_document_header(seq)` call and `with_document_header` with:

```python
                context = _document_context(seq)
                return _extract_splitting(
                    f"chunk_{seq:03d}_{step}", chunk_text,
                    lambda text, label: _llm_extract(
                        label, text_model, prompt_for(with_document_context(context, text)), doc_kg_dir,
                    ),
                    merge,
                )
```

Add `import hashlib` to the top of `ingest.py` if it is not already there (check with `grep -n "^import hashlib" artmind/ingest.py` first; `hashlib` is already used in `sync_state.py`, not here).

Note on the summary step name: `_summarise_document` calls `_llm_extract("document_summary", ...)`, so the step name is `document_summary`. The test in Step 1 that checks "one call" accepts either name; after this change it should assert `document_summary` exactly. Tighten it now:

```python
def test_the_summary_is_one_call_for_the_whole_document(monkeypatch, tmp_path):
    file_result, seen = _harness(monkeypatch, tmp_path, n_chunks=3)
    calls = []
    real = ing._llm_extract
    monkeypatch.setattr(ing, "_llm_extract", lambda s, m, p, d: calls.append(s) or real(s, m, p, d))
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert calls.count("document_summary") == 1
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context_wiring.py test/test_document_context_cache.py test/test_document_context.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_document_context_wiring.py
git commit -m "feat(context): compute metadata, outline and summary before the chunk fan-out

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: Retire the cf787bd header helpers and move their consumers

**Files:**
- Modify: `artmind/ingest.py` (delete `DOCUMENT_HEADER_CHARS`, `DOCUMENT_HEADER_END`, `build_document_header`, `strip_document_header`, `with_document_header`, `_document_header_settings`)
- Modify: `test/test_vault_chunk_cache_merge.py` (the `_seams` entities prompt, currently calling `ing.strip_document_header`)
- Modify: `test/test_extraction_truncation.py` (the split test, currently calling `ing.strip_document_header`)
- Delete (rename via git): `test/test_document_header.py`, which is superseded by `test/test_document_context_wiring.py` and `test/test_document_context.py`

- [ ] **Step 1: Find every consumer of the old names**

Run: `grep -rn "strip_document_header\|with_document_header\|build_document_header\|_document_header_settings\|DOCUMENT_HEADER" artmind test --include="*.py"`

Expected output lists `artmind/ingest.py` (definitions and the call sites removed in Task 8), `test/test_vault_chunk_cache_merge.py`, `test/test_extraction_truncation.py`, and `test/test_document_header.py`. Nothing else. If something else appears, stop and move it to the new names before deleting.

- [ ] **Step 2: Write the failing check**

Add to `test/test_document_context_wiring.py`:

```python
def test_the_cf787bd_header_helpers_are_gone():
    for name in ("build_document_header", "strip_document_header", "with_document_header",
                 "_document_header_settings", "DOCUMENT_HEADER_CHARS", "DOCUMENT_HEADER_END"):
        assert not hasattr(ing, name), f"{name} should be retired (spec 2026-10-25)"
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context_wiring.py -q -p no:cacheprovider -k retired`
Expected: FAIL, `build_document_header should be retired`.

- [ ] **Step 4: Delete the helpers and move the consumers**

Delete from `artmind/ingest.py`: the block beginning `#: How much of the document's opening every chunk prompt quotes.` through the end of `_document_header_settings` (the cf787bd helpers, roughly lines 1852-1906).

In `test/test_vault_chunk_cache_merge.py`, change:

```python
        seen.append(ing.strip_document_header(text))  # the passage, not the header around it
```

to:

```python
        seen.append(ing.strip_document_context(text))  # the passage, not the context around it
```

In `test/test_extraction_truncation.py`, change `ing.strip_document_header(prompt)` to `ing.strip_document_context(prompt)`.

Remove the old test file with git so the rename is recorded:

```bash
git rm test/test_document_header.py
```

Its cases are covered by `test/test_document_context.py` (rendering, sampling, parsing) and `test/test_document_context_wiring.py` (the prompt text `extract_kg` sends).

- [ ] **Step 5: Run the full suite**

Run: `uv run --group dev pytest test/ -q -p no:cacheprovider`
Expected: all pass. The count changes: the removed file's tests are replaced, so compare the pass count to the 4907 baseline from the previous commit's run and account for the difference by test-file (`git stash` is not needed; the changed files are listed above).

- [ ] **Step 6: Commit**

```bash
git add -A artmind/ingest.py test/
git commit -m "refactor(context): retire the cf787bd header helpers

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 10: Cross-schema behaviour test

**Files:**
- Test: `test/test_document_context_across_schemas.py`

The spec requires that the context reaches prompts for at least three shipped schemas of different shape. This test uses the real shipped schemas (`contracts_schema.yaml`, `general_schema.yaml`, `banking.reference_schema.yaml`) read from `artmind/domains/schemas/`, so it catches a schema that breaks the summary prompt.

- [ ] **Step 1: Write the test**

```python
# test/test_document_context_across_schemas.py
"""The summary prompt is built from each shipped schema alone (spec §4): it names
that schema's subject and classes, for shapes as different as a contract, a
general note and a banking reference. Asserted on the prompt text."""
from pathlib import Path

import pytest
import yaml

import artmind.document_context as dc

SCHEMAS = Path(dc.__file__).parent / "domains" / "schemas"


@pytest.mark.parametrize("name", ["contracts", "general", "banking.reference"])
def test_the_summary_prompt_names_the_schemas_own_subject_and_classes(name):
    schema = yaml.safe_load((SCHEMAS / f"{name}_schema.yaml").read_text())
    prompt = dc.summary_prompt(schema, "SAMPLE", focus="")

    assert str(schema.get("subject") or schema.get("description") or "documents").split(".")[0][:20] in prompt
    classes = list((schema.get("entity_types") or {}))
    assert classes, f"{name} declares classes"
    assert all(cls in prompt for cls in classes[:5]), "the first classes are named"
    assert "SAMPLE" in prompt and '"summary"' in prompt


def test_no_shipped_schema_yields_a_prompt_without_its_classes():
    for path in sorted(SCHEMAS.glob("*_schema.yaml")):
        schema = yaml.safe_load(path.read_text()) or {}
        classes = list((schema.get("entity_types") or {}))
        if not classes:
            continue
        prompt = dc.summary_prompt(schema, "s", focus="")
        assert classes[0] in prompt, path.name
```

- [ ] **Step 2: Run to verify**

Run: `uv run --group dev pytest test/test_document_context_across_schemas.py -q -p no:cacheprovider`
Expected: pass. If the subject check fails for `banking.reference` because its `subject` is long, the assertion's 20-character prefix is a tolerance, not a requirement; reduce the prefix to the first word of the subject. Fix the test, not the prompt.

- [ ] **Step 3: Commit**

```bash
git add test/test_document_context_across_schemas.py
git commit -m "test(context): summary prompt works across shipped schema shapes

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 11: Settings for the sample size, and the docs

**Files:**
- Modify: `artmind/document_context.py` (read `ARTMIND_KG_SUMMARY_SAMPLE_CHARS`)
- Modify: `docs/vault.md` (the config table and a paragraph)
- Modify: `artmind/skills/artmind-create-schema/SKILL.md` (the `document_context:` key)
- Test: `test/test_document_context.py`

Spec §11 asks for `ARTMIND_KG_SUMMARY_SAMPLE_CHARS`.

- [ ] **Step 1: Write the failing test**

```python
# append to test/test_document_context.py
def test_the_sample_size_can_be_tuned_from_the_environment(monkeypatch):
    from artmind import document_context as dc

    monkeypatch.setattr(dc, "load_env", lambda: {"ARTMIND_KG_SUMMARY_SAMPLE_CHARS": "300"})
    chunks = ["p" * 200, "q" * 200, "r" * 200]
    assert len(dc.sample_for_summary(chunks, {})) <= 300


def test_a_bad_sample_size_setting_falls_back_to_the_default(monkeypatch):
    from artmind import document_context as dc

    monkeypatch.setattr(dc, "load_env", lambda: {"ARTMIND_KG_SUMMARY_SAMPLE_CHARS": "lots"})
    assert dc.sample_chars() == dc.SAMPLE_CHARS
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider -k "tuned or bad_sample"`
Expected: `AttributeError` on `load_env` / `sample_chars`.

- [ ] **Step 3: Implement**

In `artmind/document_context.py`, add the import and a reader, and use the reader in `sample_for_summary`:

```python
from utils.functions import load_env


def sample_chars() -> int:
    """The summary's input cap: ARTMIND_KG_SUMMARY_SAMPLE_CHARS, else SAMPLE_CHARS."""
    try:
        value = int(load_env().get("ARTMIND_KG_SUMMARY_SAMPLE_CHARS") or SAMPLE_CHARS)
    except ValueError:
        return SAMPLE_CHARS
    return max(value, 1)
```

In `sample_for_summary`, replace `if total + len(paragraph) + 2 > SAMPLE_CHARS:` with `if total + len(paragraph) + 2 > sample_chars():`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_document_context.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Document, then commit**

In `docs/vault.md`, add `ARTMIND_KG_SUMMARY_SAMPLE_CHARS` to the **Machine** row of the config table (the row beginning `| **Machine** — ...`), and add this paragraph after the "When a chunk fails to extract" section:

```markdown
### What a chunk's prompt is given

Every chunk's extraction prompts open with a short **document context**: the
document's metadata (title, source, date, author, project, tags), the heading
outline with the chunk's own position, and a summary of the whole document.
The summary is one LLM call per document, made before the chunks extract, from
a bounded sample of the document's text (`ARTMIND_KG_SUMMARY_SAMPLE_CHARS`,
default 12000). It is cached in the staging folder as `document_context.json`
and reused until the document, its schema or the model changes. The sample is
sent to the configured provider, the same exposure as chunk extraction. A
schema turns the context off or adds a focus with `document_context:` (see the
create-schema skill); a summary failure is logged and the file still extracts.
```

In `artmind/skills/artmind-create-schema/SKILL.md`, add a bullet next to the `naming_example` paragraph:

```markdown
- **Document context (on by default).** Every chunk sees the document's
  metadata, its outline and a summary made from the schema's own subject and
  class descriptions, so you write nothing for it. To change it, add:

  ```yaml
  document_context:
    focus: "Emphasise effective dates and the parties to each amendment."
  ```

  `document_context: false` removes the block and its summary call;
  `document_context: {summary: false}` keeps the metadata and outline with no call.
```

Commit:

```bash
git add artmind/document_context.py test/test_document_context.py docs/vault.md artmind/skills/artmind-create-schema/SKILL.md
git commit -m "docs(context): sample-size setting, vault and schema-skill docs

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Self-review

**1. Spec coverage**

- §3 Metadata (no LLM, resolved before the fan-out): Task 3 renders it; Task 8 builds it from the same dict `document.json` uses.
- §3 Outline with chunk position: Task 2; Task 8 passes `document_outline=True`.
- §3 Summary block, closing instruction, chunk 1 not quoted twice: Task 6 (`PASSAGE_LINE`, `_HEADING`); the summary covers the opening through §4's sampling, so no separate quote exists.
- §3 Cap of about 2,000 characters, metadata and outline trimmed first, summary never cut mid-sentence: Task 6 (`render_context`), tested in its own cases.
- §4 One call per document, after schema and anchor date, before the fan-out: Task 8 (the call sits beside the header block, before `_init_chunk_rows`).
- §4 Input (outline, opening and closing, first paragraph under each top-level heading; ten evenly spaced chunks without headings; ~12,000 cap): Task 4.
- §4 Prompt built from the schema, `focus` appended: Task 5.
- §4 Output JSON, caps 1,200 and 25, retried like any step: Task 5 for caps; the retry is inherited from `_llm_extract` (Task 7).
- §4 Cache key (content hash, per-domain schema file, model, prompt version), resume and `--reextract` reuse: Task 6 (`context_key`), Task 7 (sidecar read), Task 8 (per-domain schema file hash). The spec's cache line was updated to `document_context.json` in this plan's preparation.
- §4 Failure never fails the file: Task 7 (`_summarise_document` returns None) and Task 8's test `test_a_failing_summary_call_leaves_the_chunks_going_and_the_file_staged`.
- §5 Schema controls (`false`, `{focus, summary}`, legacy `document_header` alias): Task 1 and Task 8.
- §6 Components (`document_context.py` pure; `_summarise_document` in ingest; removal of cf787bd helpers; `strip_document_context` rename; consumers moved): Tasks 1–9.
- §9 Privacy and logging: Task 7 logs the summary at debug level only (the `logger.debug` line); Task 11 writes the docs paragraph.
- §10 Tests (unit, cache, failure, controls, cross-schema, regression): Tasks 1–10.
- §11 Sample-size setting: Task 11.

Gap found and fixed in this review: §4's "one call" test originally accepted either step name. Task 8 now tightens it to `document_summary`.

**2. Placeholder scan**

Searched for TBD, TODO, "appropriate", "similar to", "add error handling". The only hedged instruction is Task 6 Step 4's note on a possible fixture adjustment, which names the exact test and the exact reason, so it is a concrete fallback rather than a placeholder. Task 9 Step 5 gives a count-comparison instruction without the number: the expected pass count depends on how many cf787bd tests the removed file held, and the engineer can compute it from `git show cf787bd:test/test_document_header.py`. That is a verification step, not a missing requirement.

**3. Type and name consistency**

- `ContextSettings(enabled, summary, focus, cap)`: defined in Task 1, read in Task 8 as `context_settings.enabled/summary/focus/cap`. Consistent.
- `build_outline(meta, seq, chunk_count, *, document_outline=False)`: defined in Task 2, called in Task 8 as `dc.build_outline(header_sections, seq, chunk_count, document_outline=True)`. Consistent.
- `build_metadata(document)`: Task 3, called in Task 8 with a dict. Consistent.
- `sample_for_summary(chunk_texts, chunks_meta)`: Task 4, called in Task 8 as `dc.sample_for_summary(chunk_texts, header_sections)`. Consistent.
- `summary_prompt(schema, sample, *, focus)`: Task 5, called in Task 7 as `dc.summary_prompt(schema, sample, focus=focus)`. Consistent.
- `parse_summary(answer)` returns `(summary, identifiers)` or None: Task 5, used in Task 7. Consistent.
- `render_context(*, metadata, outline, summary, cap)`: Task 6, called in Task 8 with those keyword names. Consistent.
- `context_key(*, content_sha, schema_sha, model)`: Task 6, called in Task 8 with those keyword names. Consistent.
- `_summarise_document(*, doc_kg_dir, key, schema, sample, focus, model, debug_dir)`: Task 7 definition, Task 8 call. Consistent.
- `with_document_context(block, text)` and `strip_document_context(text)`: Task 8 definitions; Task 9 consumers. Consistent.
- `DOCUMENT_CONTEXT_FILE = "document_context.json"`: Task 7; the test in Task 7 reads that exact name. Consistent.
- `dc.PASSAGE_LINE`: Task 6 defines it, Task 8's `strip_document_context` uses it. Consistent.
- `sample_chars()` and `SAMPLE_CHARS`: Task 4 uses `SAMPLE_CHARS` as the constant; Task 11 adds `sample_chars()`. Task 11's test asserts `dc.sample_chars() == dc.SAMPLE_CHARS`. Consistent.

Two things a reviewer should check against the code once it exists, since they depend on names outside this plan:
- `document_dates` in Task 8 is the variable `extract_kg` resolves before the fan-out (around `ingest.py:2992`), and it is the source of `_valid_from`. Confirm the variable is in scope at the header block.
- `domain_schema_file` is defined in `extract_kg` before the header block (around `ingest.py:2915`), which Task 8 relies on for the schema-file hash.
