"""The document context reaches every chunk's prompts (spec 2026-10-25 §3-§5), checked on
the prompt text `extract_kg` actually sends and the files it writes. The LLM is the
`_llm_extract` seam."""
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
    calls = []

    def llm(step_name, model, prompt, debug_dir):
        seen[step_name] = prompt
        calls.append(step_name)
        if step_name == "document_summary":
            return {"summary": "A change notice for Alpha.", "identifiers": ["CCN001", "Alpha Ltd"]}, True
        if step_name.endswith("_entities"):
            return [{"id": "e1", "entity_class": "AGREEMENT", "type": "t", "name": step_name}], True
        return [], True

    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: text)
    monkeypatch.setattr(ing, "build_properties_prompt", lambda text, ents, schema, vocabulary=None, anchor_date=None: text)
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: text)
    monkeypatch.setattr(ing, "_llm_extract", llm)
    return file_result, seen, calls


def test_every_chunk_step_sees_the_summary_identifiers_outline_and_metadata(monkeypatch, tmp_path):
    file_result, seen, _ = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)

    for step in ("chunk_002_entities", "chunk_003_entities", "chunk_002_properties", "chunk_002_relationships"):
        assert "Summary: A change notice for Alpha." in seen[step], step
        assert "Identifiers: CCN001; Alpha Ltd" in seen[step], step
    assert "You are in: Schedule 2 > Rates (chunk 2 of 3)" in seen["chunk_002_entities"]
    assert "Source:" in seen["chunk_002_entities"]
    assert "Title: " in seen["chunk_002_entities"]


def test_the_passage_is_still_the_chunk_text_and_nothing_else(monkeypatch, tmp_path):
    file_result, seen, _ = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert ing.strip_document_context(seen["chunk_002_entities"]) == "Clause 7. This Notice supersedes the earlier schedule."


def test_the_summary_is_one_call_for_the_whole_document(monkeypatch, tmp_path):
    file_result, _, calls = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert calls.count("document_summary") == 1
    assert calls.index("document_summary") < calls.index("chunk_001_entities"), "made before the fan-out"


def test_the_summary_sees_a_sample_of_the_document(monkeypatch, tmp_path):
    file_result, seen, _ = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    prompt = seen["document_summary"]
    assert "CHANGE CONTROL NOTE CCN001" in prompt and "Signed by both parties." in prompt
    assert "AGREEMENT: A contract." in prompt


def test_document_context_false_removes_the_block_and_the_call(monkeypatch, tmp_path):
    file_result, seen, calls = _harness(monkeypatch, tmp_path, schema_extra={"document_context": False})
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert "document_summary" not in calls
    assert "DOCUMENT CONTEXT" not in seen["chunk_002_entities"]


def test_summary_false_keeps_metadata_and_outline_without_a_call(monkeypatch, tmp_path):
    file_result, seen, calls = _harness(monkeypatch, tmp_path, schema_extra={"document_context": {"summary": False}})
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert "document_summary" not in calls
    assert "You are in: Schedule 2 > Rates" in seen["chunk_002_entities"]
    assert "Summary:" not in seen["chunk_002_entities"]


def test_focus_text_reaches_the_summary_prompt(monkeypatch, tmp_path):
    file_result, seen, _ = _harness(monkeypatch, tmp_path, schema_extra={"document_context": {"focus": "Watch the effective dates."}})
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert "Watch the effective dates." in seen["document_summary"]


def test_a_failing_summary_call_leaves_the_chunks_going_and_the_file_staged(monkeypatch, tmp_path):
    file_result, seen, _ = _harness(monkeypatch, tmp_path)
    real = ing._llm_extract

    def failing_summary(step_name, model, prompt, debug_dir):
        if step_name == "document_summary":
            raise RuntimeError("summary provider down")
        return real(step_name, model, prompt, debug_dir)

    monkeypatch.setattr(ing, "_llm_extract", failing_summary)
    folder = ing.extract_kg(file_result, "testdom", max_workers=1)

    assert folder is not None, "the file stages"
    assert "You are in: Schedule 2 > Rates" in seen["chunk_002_entities"]
    assert "Summary:" not in seen["chunk_002_entities"]
    assert not (folder / "document_context.json").exists()


def test_the_legacy_document_header_key_still_switches_the_block_off(monkeypatch, tmp_path):
    file_result, seen, _ = _harness(monkeypatch, tmp_path, schema_extra={"document_header": False})
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert "DOCUMENT CONTEXT" not in seen["chunk_002_entities"]


def test_a_second_extraction_of_the_same_document_reuses_the_summary(monkeypatch, tmp_path):
    file_result, _, calls = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    calls.clear()
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert "document_summary" not in calls


def test_a_schema_edit_regenerates_the_summary(monkeypatch, tmp_path):
    file_result, _, calls = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    calls.clear()
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "testdom_schema.yaml"
    schema_file.write_text(schema_file.read_text() + "\n# edited\n")
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert calls.count("document_summary") == 1


def test_a_content_change_regenerates_the_summary(monkeypatch, tmp_path):
    file_result, _, calls = _harness(monkeypatch, tmp_path)
    ing.extract_kg(file_result, "testdom", max_workers=1)
    calls.clear()
    file_result["sha256"] = "sha-changed"
    ing.extract_kg(file_result, "testdom", max_workers=1)
    assert calls.count("document_summary") == 1


def test_a_reextraction_of_unchanged_text_reuses_the_summary(monkeypatch, tmp_path):
    file_result, _, calls = _harness(monkeypatch, tmp_path)
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")  # the re-extraction stash goes there
    ing.extract_kg(file_result, "testdom", max_workers=1)
    calls.clear()
    file_result["reextract"] = True
    assert ing.extract_kg(file_result, "testdom", max_workers=1) is not None
    assert "document_summary" not in calls
    assert "chunk_002_entities" in calls, "the chunks were still re-extracted"


def test_a_split_piece_keeps_the_context(monkeypatch, tmp_path):
    """A cut-off chunk is halved; each half is still read as part of this document."""
    from artmind.extraction import TruncatedAnswer

    file_result, seen, _ = _harness(monkeypatch, tmp_path, n_chunks=2)
    chunks = Path(file_result["chunks_dir"])
    (chunks / "chunk_002.md").write_text("\n\n".join(f"Clause {i} " + "text " * 40 for i in range(6)), encoding="utf-8")
    real = ing._llm_extract
    pieces = []

    def llm(step_name, model, prompt, debug_dir):
        if step_name.startswith("chunk_002_entities"):
            if len(ing.strip_document_context(prompt)) > 800:
                raise TruncatedAnswer("cut off")
            pieces.append(prompt)
        return real(step_name, model, prompt, debug_dir)

    monkeypatch.setattr(ing, "_llm_extract", llm)
    ing.extract_kg(file_result, "testdom", max_workers=1)

    assert len(pieces) == 2
    assert all("Summary: A change notice for Alpha." in p and "You are in:" in p for p in pieces)
