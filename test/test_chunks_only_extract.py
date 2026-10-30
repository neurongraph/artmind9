"""extract_kg in a `graph_extraction: false` domain (spec 2026-10-30). Hermetic:
every LLM seam raises, so any call fails the test. Synthetic text only."""
import json

import pytest

import artmind.ingest as ing
from conftest import stage_as_extracted


@pytest.fixture()
def chunks_only(ingest_env, monkeypatch, tmp_path):
    vault, _ = ingest_env
    schemas = vault / "schemas"
    schemas.mkdir()
    (schemas / "notes_schema.yaml").write_text("name: notes\ndescription: Search only.\ngraph_extraction: false\n")
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")

    def forbidden(*a, **k):
        raise AssertionError("an LLM seam was called in a chunks-only domain")

    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    for name in ("_llm_extract", "_summarise_document", "canonicalize_document"):
        monkeypatch.setattr(ing, name, forbidden)
    import artmind.canonicalize as canon

    monkeypatch.setattr(canon, "retrieve_vocabulary", forbidden)
    monkeypatch.setattr(canon, "retrieve_property_vocabulary", forbidden)
    return vault


def _note(vault, text="A synthetic note about Alpha meeting Beta.\n" * 3):
    note = vault / "notes" / "a.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(text, encoding="utf-8")
    return note


def test_chunks_and_embeddings_are_staged_with_no_extraction(chunks_only):
    result = ing.ingest_file(_note(chunks_only), "m", "notes")

    folder = ing.extract_kg(result, "notes", max_workers=1)

    assert folder is not None
    assert json.loads((folder / "observations.json").read_text()) == []
    assert json.loads((folder / "relationships.json").read_text()) == []
    chunks = json.loads((folder / "chunks.json").read_text())
    assert len(chunks) == 1 and "Alpha" in chunks[0]["text"]
    assert list(ing.read_embedding_sidecar(folder).values()) == [[0.0, 0.1]]
    status = ing._get_chunk_statuses(result["sha256"])[1]
    assert (status["entities_status"], status["properties_status"], status["relationships_status"]) == ("skipped",) * 3
    assert not (folder / ing.DOCUMENT_CONTEXT_FILE).exists()


def test_a_resume_reuses_the_chunk_and_still_makes_no_llm_call(chunks_only):
    result = ing.ingest_file(_note(chunks_only), "m", "notes")
    assert ing.extract_kg(result, "notes", max_workers=1) is not None

    assert ing.extract_kg(result, "notes", max_workers=1) is not None


def test_reextracting_a_previously_extracted_note_stages_empty_instead_of_refusing(chunks_only):
    note = _note(chunks_only)
    first = ing.ingest_file(note, "m", "notes")
    folder = stage_as_extracted(ing.KG_DIR, first, domain="notes")
    (folder / "observations.json").write_text(json.dumps([{"id": "o1", "key": "alpha", "name": "Alpha"}]))
    result = ing.ingest_file(note, "m", "notes", reextract=True)
    result["reextract"] = True

    assert ing.extract_kg(result, "notes", max_workers=1) == folder

    assert json.loads((folder / "observations.json").read_text()) == []
    assert "kg_error" not in result
