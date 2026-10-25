"""Every chunk prompt carries a short header about the whole document (study
item 3, option a).

Chunks extract in isolation, so a 40-page amendment's clause "this Change
Notice supersedes ..." became a new AMENDMENT entity per chunk, each named after
whatever label that chunk happened to use. The header (source file, section,
and the document's opening text) lets every chunk name the thing the same way.
No LLM, no Neo4j. Synthetic text only."""
import json
from pathlib import Path

import pytest
import yaml
from test_ingest_concurrency import _setup_extract_env

import artmind.ingest as ing
from artmind.ingest import build_document_header, with_document_header


OPENING = "CHANGE CONTROL NOTE CCN001. Effective 2026-03-01. Parties: Alpha Ltd and Beta plc."


def test_the_header_names_the_document_and_quotes_its_opening():
    header = build_document_header(
        "Contract Amendment CCN001.pdf", opening=OPENING, section="Schedule 2 > Rates", limit=800,
    )
    assert "Contract Amendment CCN001.pdf" in header
    assert "Schedule 2 > Rates" in header
    assert OPENING in header
    assert "do not extract" in header.lower(), "the header is context, not text to extract from"


def test_the_opening_is_cut_to_the_limit_on_a_word_boundary():
    header = build_document_header("a.pdf", opening="word " * 400, limit=100)
    opening_lines = [l for l in header.splitlines() if l.startswith("word")]
    assert opening_lines and len(opening_lines[0]) <= 101
    assert opening_lines[0].endswith("…")


def test_a_header_with_nothing_to_add_is_empty():
    assert build_document_header("", opening="", section="", limit=800) == ""


def test_strip_document_header_returns_the_passage():
    header = build_document_header("a.pdf", opening=OPENING)
    assert ing.strip_document_header(with_document_header(header, "the passage")) == "the passage"
    assert ing.strip_document_header("a passage with no header") == "a passage with no header"


def test_with_document_header_puts_the_header_before_the_passage():
    prompt_text = with_document_header("HEADER", "the passage")
    assert prompt_text.startswith("HEADER") and prompt_text.endswith("the passage")
    assert with_document_header("", "the passage") == "the passage"


def _prompts_for(monkeypatch, tmp_path, *, schema_extra=None, n_chunks=3):
    file_result = _setup_extract_env(monkeypatch, tmp_path, n_chunks)
    chunks = Path(file_result["chunks_dir"])
    (chunks / "chunk_001.md").write_text(OPENING + "\n\nDefinitions follow.", encoding="utf-8")
    (chunks / "chunk_002.md").write_text("Clause 7. This Notice supersedes the earlier schedule.", encoding="utf-8")
    (chunks / "chunk_003.md").write_text("Signed by both parties.", encoding="utf-8")
    (chunks / "chunks_meta.json").write_text(json.dumps({
        "001": {"header_path": ""}, "002": {"header_path": "Schedule 2 > Rates"}, "003": {"header_path": ""},
    }))
    schema = {"entities_prompt": "p", **(schema_extra or {})}
    (ing.DOMAIN_SCHEMAS_DIR / "testdom_schema.yaml").write_text(yaml.safe_dump(schema))
    seen = {}
    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: text)
    monkeypatch.setattr(ing, "build_properties_prompt",
                        lambda text, ents, schema, vocabulary=None, anchor_date=None: text)
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: text)

    def llm(step_name, model, prompt, debug_dir):
        seen[step_name] = prompt
        if step_name.endswith("_entities"):
            return ([{"id": "e1", "entity_class": "Thing", "type": "t", "name": step_name}], True)
        return ([], True)

    monkeypatch.setattr(ing, "_llm_extract", llm)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    return seen


def test_every_chunk_prompt_carries_the_documents_opening(monkeypatch, tmp_path):
    seen = _prompts_for(monkeypatch, tmp_path)

    for step in ("chunk_002_entities", "chunk_003_entities", "chunk_002_properties", "chunk_002_relationships"):
        assert OPENING in seen[step], step
    assert "Schedule 2 > Rates" in seen["chunk_002_entities"]
    assert "Clause 7. This Notice supersedes" in seen["chunk_002_entities"]  # the passage itself is still there


def test_the_first_chunk_is_not_given_its_own_text_twice(monkeypatch, tmp_path):
    seen = _prompts_for(monkeypatch, tmp_path)
    assert seen["chunk_001_entities"].count(OPENING) == 1


def test_the_chunk_text_that_is_stored_is_the_passage_only(monkeypatch, tmp_path):
    _prompts_for(monkeypatch, tmp_path)
    staged = next(ing.KG_DIR.glob("testdom/*/chunks.json"))
    texts = [c["text"] for c in json.loads(staged.read_text())]
    assert texts[1] == "Clause 7. This Notice supersedes the earlier schedule."


def test_a_schema_can_turn_the_header_off(monkeypatch, tmp_path):
    seen = _prompts_for(monkeypatch, tmp_path, schema_extra={"document_header": False})
    assert OPENING not in seen["chunk_002_entities"]


def test_a_schema_can_resize_the_opening(monkeypatch, tmp_path):
    seen = _prompts_for(monkeypatch, tmp_path, schema_extra={"document_header": {"chars": 20}})
    assert "CHANGE CONTROL NOTE" in seen["chunk_002_entities"]
    assert "Parties: Alpha Ltd" not in seen["chunk_002_entities"]


def test_a_split_piece_keeps_the_header(monkeypatch, tmp_path):
    """A cut-off chunk is halved (item 1); each half is still read as part of this document."""
    file_result = _setup_extract_env(monkeypatch, tmp_path, 2)
    chunks = Path(file_result["chunks_dir"])
    (chunks / "chunk_001.md").write_text(OPENING, encoding="utf-8")
    (chunks / "chunk_002.md").write_text("\n\n".join(f"Clause {i} " + "text " * 40 for i in range(6)), encoding="utf-8")
    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: text)
    monkeypatch.setattr(ing, "build_properties_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda *a, **k: "p")
    pieces = []

    def llm(step_name, model, prompt, debug_dir):
        if step_name.startswith("chunk_002_entities"):
            if len(prompt) > 1300:
                from artmind.extraction import TruncatedAnswer
                raise TruncatedAnswer("cut off")
            pieces.append(prompt)
        return ([], True)

    monkeypatch.setattr(ing, "_llm_extract", llm)
    ing.extract_kg(file_result, "testdom", max_workers=1)

    assert len(pieces) == 2 and all(OPENING in p for p in pieces)
