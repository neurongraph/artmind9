from _neo4j_recorder import Recorder

from artmind import extraction, ingest


def test_entity_sweep_selects_stale_keys_and_writes_the_key(monkeypatch):
    monkeypatch.setattr(extraction, "embed_text", lambda model, text: [0.1])
    s = Recorder(lambda c, p: [{"id": "e1", "name": "Ada", "description": "a person"}] if "RETURN e._id AS id" in c else [])
    ingest.embed_missing_entity_embeddings(s, "people", "nomic-embed-text:latest")
    (select, sp), = s.queries_containing("RETURN e._id AS id")
    assert "e._embed_key IS NULL OR e._embed_key <> $embed_key" in select
    assert sp["embed_key"] == extraction.embed_key("nomic-embed-text:latest")
    (write, wp), = s.queries_containing("SET e.embedding = $embedding")
    assert "e._embed_key = $embed_key" in write and wp["embed_key"] == sp["embed_key"]


def test_entity_text_gets_the_document_prefix(monkeypatch):
    sent = []
    monkeypatch.setattr(extraction, "embed_text", lambda model, text: sent.append(text) or [0.1])
    s = Recorder(lambda c, p: [{"id": "e1", "name": "Ada", "description": None}] if "RETURN e._id AS id" in c else [])
    ingest.embed_missing_entity_embeddings(s, "people", "nomic-embed-text:latest")
    assert sent == ["search_document: Ada"]
