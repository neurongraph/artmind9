from artmind import ingest


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class FakeSession:
    def __init__(self, missing_rows):
        self.missing_rows = missing_rows
        self.writes = []

    def run(self, cypher, **params):
        if "e.embedding IS NULL" in cypher:
            return FakeResult(self.missing_rows)
        self.writes.append(params)
        return FakeResult([])


def test_entity_embedding_text_includes_description():
    assert ingest.entity_embedding_text("Holmes", "a detective") == "Holmes: a detective"
    assert ingest.entity_embedding_text("Holmes", None) == "Holmes"


def test_embed_missing_entity_embeddings_writes_vectors(monkeypatch):
    session = FakeSession(
        [
            {"id": "ent-1", "name": "Holmes", "description": "a detective"},
            {"id": "ent-2", "name": "Watson", "description": None},
        ]
    )
    monkeypatch.setattr(ingest, "_embed_text", lambda model, text: [0.1, 0.2])

    count = ingest.embed_missing_entity_embeddings(session, "fiction", "test-model")

    assert count == 2
    assert [w["id"] for w in session.writes] == ["ent-1", "ent-2"]
    assert all(w["embedding"] == [0.1, 0.2] for w in session.writes)


def test_embed_missing_entity_embeddings_skips_failures(monkeypatch):
    session = FakeSession(
        [
            {"id": "ent-1", "name": "Holmes", "description": None},
            {"id": "ent-2", "name": "Watson", "description": None},
        ]
    )

    def flaky_embed(model, text):
        if "Holmes" in text:
            raise RuntimeError("ollama down")
        return [0.3]

    monkeypatch.setattr(ingest, "_embed_text", flaky_embed)

    count = ingest.embed_missing_entity_embeddings(session, "fiction", "test-model")

    assert count == 1
    assert session.writes[0]["id"] == "ent-2"
