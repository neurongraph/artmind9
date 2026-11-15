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
    monkeypatch.setattr(ingest._extraction, "embed_text", lambda model, text: [0.1, 0.2])

    count = ingest.embed_missing_entity_embeddings(session, "fiction", "test-model")

    assert count == 2
    main = [w for w in session.writes if "embedding" in w]
    names = [w for w in session.writes if "name_key" in w]
    assert [w["id"] for w in main] == ["ent-1", "ent-2"]
    assert all(w["embedding"] == [0.1, 0.2] for w in main)
    # every swept entity also gets its name vector, keyed by model + name text
    assert [w["id"] for w in names] == ["ent-1", "ent-2"]
    assert all(w["vector"] == [0.1, 0.2] for w in names)
    from artmind import extraction, projection
    assert names[0]["name_key"] == f"{extraction.embed_key('test-model')}:{projection.name_source('Holmes')}"


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

    monkeypatch.setattr(ingest._extraction, "embed_text", flaky_embed)

    count = ingest.embed_missing_entity_embeddings(session, "fiction", "test-model")

    assert count == 1
    assert [w["id"] for w in session.writes if "embedding" in w] == ["ent-2"]


# ── progress logging: a full-vault backfill must not go silent ─────────────
# Found live: after an 860-key full_rebuild, the entity embed sweep that
# follows it (one embedding call per entity, sequentially) ran with zero
# progress output -- the same "went silent for minutes, indistinguishable
# from stuck" symptom as projection.rebuild's own 860-key loop.


def test_embed_missing_entity_embeddings_logs_progress_past_fifty(monkeypatch):
    session = FakeSession(
        [{"id": f"ent-{i}", "name": f"Entity {i}", "description": None} for i in range(60)]
    )
    monkeypatch.setattr(ingest._extraction, "embed_text", lambda model, text: [0.1])

    logged = []
    monkeypatch.setattr(ingest.logger, "info", lambda *a, **k: logged.append(a))

    count = ingest.embed_missing_entity_embeddings(session, "fiction", "test-model")

    assert count == 60
    progress_lines = [a for a in logged if "entity node(s) (" in str(a[0])]
    assert progress_lines, "a 60-entity sweep must log at least one progress checkpoint"
    assert 50 in progress_lines[0][1:], "the checkpoint must fire at entity 50"


def test_embed_missing_entity_embeddings_stays_quiet_under_the_threshold(monkeypatch):
    session = FakeSession(
        [{"id": "ent-1", "name": "Holmes", "description": None}]
    )
    monkeypatch.setattr(ingest._extraction, "embed_text", lambda model, text: [0.1])

    logged = []
    monkeypatch.setattr(ingest.logger, "info", lambda *a, **k: logged.append(a))

    ingest.embed_missing_entity_embeddings(session, "fiction", "test-model")

    progress_lines = [a for a in logged if "%)" in str(a)]
    assert progress_lines == []


def test_name_vector_only_when_main_vector_is_fresh(monkeypatch):
    """A row whose main vector is current (`needs_main` false) gets only its
    name vector: no second `name: description` embedding call."""
    sent = []
    monkeypatch.setattr(ingest._extraction, "embed_text", lambda model, text: sent.append(text) or [0.5])
    session = FakeSession([{"id": "ent-1", "name": "Holmes", "description": "a detective",
                            "aliases": ["Sherlock", "S. Holmes"], "needs_main": False}])
    count = ingest.embed_missing_entity_embeddings(session, "fiction", "test-model")
    assert count == 0
    assert sent == ["Holmes; Sherlock; S. Holmes"]
    (write,) = session.writes
    assert write["vector"] == [0.5] and write["id"] == "ent-1"
