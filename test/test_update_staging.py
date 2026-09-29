"""`artmind update confirm` travels (spec 2026-09-26 §7, D6): it writes a
staging folder, `kg/<domain>/update__<session_id>__<draft_id>/`, and commits
the graph FROM that folder through `ingest._commit_document_tx` -- the same
transaction `vault sync` replays the folder with on another machine. No
Neo4j: recording sessions stand in, and the tests compare the Cypher and
parameters actually sent (CLAUDE.md §3)."""
import json
from contextlib import contextmanager

import pytest

import artmind.ingest as ing
import artmind.update as upd
from artmind import sync_state


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _Session:
    """Records every statement; those sent inside `execute_write` also go to
    `tx_calls` -- the commit transaction, which is what must be identical
    between `confirm` and a replay."""

    def __init__(self):
        self.calls = []
        self.tx_calls = []
        self._in_tx = False

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if self._in_tx:
            self.tx_calls.append((cypher, params))
        return _Result()

    def execute_write(self, fn, *args, **kwargs):
        self._in_tx = True
        try:
            return fn(self, *args, **kwargs)
        finally:
            self._in_tx = False


@pytest.fixture()
def kg_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / ".artmind" / "data" / "kg"
    monkeypatch.setattr(paths, "KG_DIR", target)
    return target


@pytest.fixture()
def rebuilds(monkeypatch):
    import artmind.projection as projection

    seen = []
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: seen.append(sorted(keys)) or {"keys": len(keys)})
    return seen


def _confirm(monkeypatch, *, draft_id=7, relationships=True):
    session = _Session()

    @contextmanager
    def _fake():
        yield session

    monkeypatch.setattr(upd, "neo4j_session", _fake)
    monkeypatch.setattr(upd, "embed_text", lambda model, text: [0.25, 0.5])
    result = upd.write_user_chat(
        session_id="s1", raw_text="Alice works at Acme.", domain="general", user_id="u@example.com",
        resolutions=[
            {"entity_temp_id": "e0", "action": "create", "node_id": None},
            {"entity_temp_id": "e1", "action": "create", "node_id": None},
        ],
        extracted_entities=[
            {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {"role": "CEO"}},
            {"temp_id": "e1", "name": "Acme", "entity_class": "ORGANIZATION", "properties": {}},
        ],
        extracted_relationships=[{"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "works_at"}] if relationships else [],
        draft_id=draft_id,
    )
    return session, result


def test_confirm_writes_one_staging_folder_per_confirmed_draft(kg_dir, monkeypatch, rebuilds):
    _, result = _confirm(monkeypatch)

    folder = kg_dir / "general" / "update__s1__7"
    assert result["staging_dir"] == str(folder)
    assert sorted(p.name for p in (kg_dir / "general").iterdir()) == ["update__s1__7"], "no atomic-write scratch left"
    document = json.loads((folder / "document.json").read_text())
    assert document["id"] == result["user_chat_id"] == "update:s1:7"
    assert (document["source_kind"], document["raw_text"], document["session_id"]) == ("user_chat", "Alice works at Acme.", "s1")
    assert "embedding" not in document, "a vector is derived, never staged"
    observations = json.loads((folder / "observations.json").read_text())
    assert sorted(o["canonical_name"] for o in observations) == ["Acme", "Alice"]
    assert all(o["doc_id"] == "update:s1:7" for o in observations)
    (relationship,) = json.loads((folder / "relationships.json").read_text())
    by_name = {o["canonical_name"]: o["id"] for o in observations}
    assert relationship == {
        "source_observation_id": by_name["Alice"], "target_observation_id": by_name["Acme"],
        "source_name": "Alice", "target_name": "Acme", "rel_type": "WORKS_AT",
        "chunk_id": "update:s1:7", "doc_id": "update:s1:7",
    }


def test_confirm_commits_from_the_folder_and_records_its_fingerprint(kg_dir, monkeypatch, rebuilds):
    session, result = _confirm(monkeypatch)

    folder = kg_dir / "general" / "update__s1__7"
    assert [p for c, p in session.tx_calls if "SET c.fingerprint" in c] == [
        {"doc_id": "update:s1:7", "fingerprint": sync_state.folder_fingerprint(folder)}
    ]
    assert result["relationships_written"] == 1
    assert [p for c, p in session.calls if c == "MATCH (c:UserChat {id: $id}) SET c.embedding = $embedding"] == [
        {"id": "update:s1:7", "embedding": [0.25, 0.5]}
    ]


def test_replaying_the_folder_writes_exactly_what_confirm_wrote(kg_dir, monkeypatch, rebuilds):
    """What another machine's `vault sync` does with the committed folder:
    the same commit transaction, statement for statement, parameter for
    parameter."""
    confirm_session, _ = _confirm(monkeypatch)

    import artmind.graph_query as graph_query

    replay_session = _Session()

    @contextmanager
    def _fake(*a, **k):
        yield replay_session

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)

    ing._write_to_neo4j(kg_dir / "general" / "update__s1__7", "general")

    assert replay_session.tx_calls == confirm_session.tx_calls
    assert any("ASSERTS_RELATION" in c for c, _ in replay_session.tx_calls)
    assert rebuilds[0] == rebuilds[1]


def test_a_retried_confirm_of_the_same_draft_rewrites_the_same_folder(kg_dir, monkeypatch, rebuilds):
    first, a = _confirm(monkeypatch)
    second, b = _confirm(monkeypatch)

    assert a["user_chat_id"] == b["user_chat_id"]
    assert [p.name for p in (kg_dir / "general").iterdir()] == ["update__s1__7"]

    def _observation_ids(session):
        return sorted(p["id"] for c, p in session.tx_calls if "CREATE (n:Observation {id: $id})" in c)

    assert _observation_ids(first) == _observation_ids(second) and len(_observation_ids(first)) == 2


def test_confirm_update_names_the_folder_after_the_draft(monkeypatch):
    seen = {}
    monkeypatch.setattr(upd, "_get_latest_pending_draft", lambda sid: {
        "id": 42, "session_id": sid, "raw_text": "x", "extraction_json": json.dumps({"entities": [], "relationships": []}),
        "domain": "general",
    })
    monkeypatch.setattr(upd, "write_user_chat", lambda **kw: seen.update(kw) or {"ok": True})
    monkeypatch.setattr(upd, "_update_draft_status", lambda *a: None)
    monkeypatch.setattr(upd, "_update_session_status", lambda *a: None)

    upd.confirm_update("s1", [], "u")

    assert seen["draft_id"] == 42


def test_user_chat_ids_are_deterministic_and_session_scoped():
    assert upd.user_chat_id("s1", 7) == "update:s1:7"
    assert upd.update_folder("general", "s1", 7).parts[-2:] == ("general", "update__s1__7")


def test_embed_user_chats_embeds_only_chats_without_a_vector(monkeypatch):
    class _Chats(_Session):
        def run(self, cypher, **params):
            super().run(cypher, **params)
            if "c.embedding IS NULL" in cypher:
                return _Result([{"id": "update:s1:7", "raw_text": "Alice works at Acme."}])
            return _Result()

    session = _Chats()

    @contextmanager
    def _fake():
        yield session

    monkeypatch.setattr(upd, "neo4j_session", _fake)
    monkeypatch.setattr(upd, "embed_text", lambda model, text: [0.1] if text == "Alice works at Acme." else None)

    assert upd.embed_user_chats(domain="general") == 1
    read, write = session.calls
    assert read[1] == {"domain": "general", "ids": None}
    assert write == ("MATCH (c:UserChat {id: $id}) SET c.embedding = $embedding", {"id": "update:s1:7", "embedding": [0.1]})
