"""extract_kg stages summaries.json for every domain (spec 2026-11-01 §1.2). Synthetic text only."""
import json

import pytest

import artmind.ingest as ing
from artmind import summary_tree as st


@pytest.fixture()
def notes(ingest_env, monkeypatch, tmp_path):
    vault, _ = ingest_env
    schemas = vault / "schemas"
    schemas.mkdir()
    (schemas / "notes_schema.yaml").write_text("name: notes\ndescription: Search only.\ngraph_extraction: false\n")
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    return vault


def _note(vault, text="A synthetic note about Alpha meeting Beta.\n" * 3):
    note = vault / "notes" / "a.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(text, encoding="utf-8")
    return note


def test_a_chunks_only_note_stages_its_summaries(notes):
    result = ing.ingest_file(_note(notes), "m", "notes")
    folder = ing.extract_kg(result, "notes", max_workers=1)
    data = json.loads((folder / st.SUMMARIES_FILE).read_text())
    chunks = json.loads((folder / "chunks.json").read_text())
    assert [c["chunk_id"] for c in data["chunks"]] == [c["id"] for c in chunks]
    assert data["chunks"][0]["summary"] == "summary of chunk_summary"
    assert data["document"]["summary"] == "summary of chunk_summary"   # one chunk: no reduce
    assert "summary_failed_chunks" not in result


def test_a_second_run_reuses_the_summaries(notes, monkeypatch):
    result = ing.ingest_file(_note(notes), "m", "notes")
    ing.extract_kg(result, "notes", max_workers=1)

    def forbidden(*a, **k):
        raise AssertionError("summary asked again for unchanged text")

    monkeypatch.setattr(ing, "_summary_llm", forbidden)
    assert ing.extract_kg(result, "notes", max_workers=1) is not None


def test_a_failed_summary_does_not_fail_the_file(notes, monkeypatch):
    monkeypatch.setattr(ing, "_summary_llm", lambda *a, **k: None)
    result = ing.ingest_file(_note(notes), "m", "notes")
    folder = ing.extract_kg(result, "notes", max_workers=1)
    assert folder is not None
    data = json.loads((folder / st.SUMMARIES_FILE).read_text())
    assert data["chunks"] == [] and data["document"] is None
    assert len(result["summary_failed_chunks"]) == 1
