"""An `artmind update` staging folder commits through the same transaction as
every document (`ingest._commit_document_tx`), as a `:UserChat` rather than a
`:Document` -- the one code path `update confirm` and `vault sync` share
(spec 2026-09-26 §7). A recording session stands in for Neo4j; the tests
assert on the Cypher and parameters sent (CLAUDE.md §3)."""
import json
from contextlib import contextmanager

import pytest

import artmind.ingest as ing
from artmind import sync_state


class _Result:
    def single(self):
        return None

    def data(self):
        return []

    def __iter__(self):
        return iter(())


class _Session:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return _Result()

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    execute_read = execute_write

    def matching(self, text):
        return [(c, p) for c, p in self.calls if text in c]


@pytest.fixture()
def session(monkeypatch):
    import artmind.graph_query as graph_query

    fake = _Session()

    @contextmanager
    def _fake(*args, **kwargs):
        yield fake

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    return fake


_OBS = {"id": "o1", "key": "alice|PERSON|general", "canonical_name": "Alice", "chunk_id": "update:s1:7",
        "doc_id": "update:s1:7", "source_kind": "user_chat"}


def _stage_chat(folder):
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({
        "id": "update:s1:7", "source_kind": "user_chat", "_domain": "general",
        "raw_text": "Alice is the CEO.", "session_id": "s1",
    }))
    (folder / "chunks.json").write_text("[]")
    (folder / "observations.json").write_text(json.dumps([_OBS]))
    (folder / "relationships.json").write_text("[]")
    return folder


def test_an_update_folder_commits_as_a_user_chat_not_a_document(tmp_path, session):
    folder = _stage_chat(tmp_path / "update__s1__7")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    chats = session.matching("CREATE (n:UserChat {id: $id})")
    assert len(chats) == 1
    assert chats[0][1]["id"] == "update:s1:7"
    assert chats[0][1]["props"]["raw_text"] == "Alice is the CEO."
    assert "REMOVE n:UserChatHistory" in chats[0][0], "a retracted chat is revived, not duplicated"
    assert session.matching("CREATE (n:Document {id: $id})") == []


def test_an_update_folders_fingerprint_is_recorded_on_its_user_chat(tmp_path, session):
    folder = _stage_chat(tmp_path / "update__s1__7")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    assert [p for c, p in session.matching("SET c.fingerprint = $fingerprint")] == [
        {"doc_id": "update:s1:7", "fingerprint": sync_state.folder_fingerprint(folder)}
    ]
    assert session.matching("SET d.fingerprint") == []


def test_an_update_folders_observations_are_extracted_from_its_user_chat(tmp_path, session):
    folder = _stage_chat(tmp_path / "update__s1__7")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    links = session.matching("MERGE (o)-[:EXTRACTED_FROM]->(c)")
    assert [(("UserChat" in c), p) for c, p in links] == [(True, {"id": "o1", "doc_id": "update:s1:7"})]


def test_a_document_still_commits_as_a_document(tmp_path, session):
    folder = tmp_path / "doc"
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "a.md"}))
    (folder / "observations.json").write_text("[]")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    assert len(session.matching("CREATE (n:Document {id: $id})")) == 1
    assert session.matching("UserChat") == []


def test_a_garbage_source_kind_still_commits_as_a_document(tmp_path, session):
    folder = tmp_path / "doc"
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "a.md", "source_kind": "bogus"}))
    (folder / "observations.json").write_text("[]")

    ing._write_to_neo4j(folder, "general", defer_rebuild=True)

    assert len(session.matching("CREATE (n:Document {id: $id})")) == 1
    assert session.matching("UserChat") == []


def test_retract_document_also_retires_a_user_chat(session, monkeypatch):
    import artmind.projection as projection

    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, **kw: set())

    ing.retract_document("update:s1:7", "general")

    assert session.matching("REMOVE c:UserChat SET c:UserChatHistory") == [
        ("MATCH (c:UserChat {id: $doc_id}) REMOVE c:UserChat SET c:UserChatHistory", {"doc_id": "update:s1:7"})
    ]


def test_fingerprints_are_read_from_documents_and_user_chats_in_one_query(monkeypatch):
    import artmind.graph_query as graph_query

    fake = _Session()

    @contextmanager
    def _fake(access_mode=None, *, timeout=None):
        yield fake

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)

    sync_state.read_document_fingerprints(["update:s1:7", "doc-1"])

    (cypher, params), = fake.calls
    assert "MATCH (d:Document) WHERE d.id IN $doc_ids" in cypher
    assert "UNION ALL" in cypher and "MATCH (c:UserChat) WHERE c.id IN $doc_ids" in cypher
    assert params == {"doc_ids": ["doc-1", "update:s1:7"]}


def test_a_fingerprint_goes_on_a_document_or_a_user_chat_only():
    with pytest.raises(ValueError, match="only Document or UserChat"):
        sync_state.set_document_fingerprint(_Session(), "x", "fp", label="Entity")


def test_setup_constrains_the_user_chat_history_id():
    from artmind.setup import _setup_neo4j

    fake = _Session()
    _setup_neo4j(fake, 768)

    assert fake.matching("CREATE CONSTRAINT user_chat_history_id IF NOT EXISTS FOR (n:UserChatHistory) REQUIRE n.id IS UNIQUE")
