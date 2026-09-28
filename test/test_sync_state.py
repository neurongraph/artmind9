"""The graph bookmark and document fingerprints (spec 2026-09-26 §6 A2, A3).

Neo4j is never reached: a recording session stands in for it, and every test
asserts on the Cypher and parameters actually sent (CLAUDE.md §3), never on a
count the fake invents."""
import hashlib
from contextlib import contextmanager

import pytest

from artmind import sync_state


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _RecordingSession:
    """Answers each `run` with the next canned row list and records
    `(cypher, params)`."""

    def __init__(self, answers=()):
        self.calls = []
        self._answers = list(answers)

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return _Result(self._answers.pop(0) if self._answers else [])


@pytest.fixture()
def session(monkeypatch):
    """Patches `graph_query.neo4j_session`, which `sync_state` resolves at call
    time, and records the `timeout` each session was opened with."""
    import artmind.graph_query as graph_query

    fake = _RecordingSession()
    fake.timeouts = []

    @contextmanager
    def _fake_session(access_mode=None, *, timeout=None):
        fake.timeouts.append(timeout)
        yield fake

    monkeypatch.setattr(graph_query, "neo4j_session", _fake_session)
    return fake


# ── the fingerprint's byte definition ─────────────────────────────────────────


def _folder(tmp_path, **files):
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    return tmp_path


def test_fingerprint_is_sha256_over_named_length_prefixed_files_in_fixed_order(tmp_path):
    doc = _folder(
        tmp_path,
        **{"document.json": b'{"id": "d1"}', "observations.json": b"[]", "chunks.json": b"[1]"},
    )

    expected = hashlib.sha256(
        b"document.json\0" + b"12\0" + b'{"id": "d1"}'
        + b"chunks.json\0" + b"3\0" + b"[1]"
        + b"observations.json\0" + b"2\0" + b"[]"
        + b"relationships.json\0" + b"absent\0"
    ).hexdigest()
    assert sync_state.folder_fingerprint(doc) == expected


def test_fingerprint_is_none_without_observations(tmp_path):
    doc = _folder(tmp_path, **{"document.json": b"{}"})

    assert sync_state.folder_fingerprint(doc) is None


@pytest.mark.parametrize("name", sync_state.FINGERPRINTED_FILES)
def test_a_change_to_any_committed_file_changes_the_fingerprint(tmp_path, name):
    """Track A replays a folder when ANY of its files changed (spec §14 A4);
    a fingerprint that ignored one of them would skip that replay."""
    base = {n: b"[]" for n in sync_state.FINGERPRINTED_FILES}
    before = sync_state.staging_fingerprint(lambda n: base.get(n))
    changed = dict(base, **{name: b"[ ]"})

    assert sync_state.staging_fingerprint(lambda n: changed.get(n)) != before


def test_files_outside_the_fingerprinted_set_do_not_change_it(tmp_path):
    """The sidecar and the chunk cache never reach the graph."""
    doc = _folder(tmp_path, **{"document.json": b"{}", "observations.json": b"[]"})
    before = sync_state.folder_fingerprint(doc)
    (doc / "embeddings.json").write_bytes(b'{"c1": [0.1]}')
    (doc / "chunks").mkdir()
    (doc / "chunks" / "0.json").write_bytes(b"{}")

    assert sync_state.folder_fingerprint(doc) == before


def test_an_absent_file_and_an_empty_file_differ():
    present = {"observations.json": b"[]", "relationships.json": b""}
    absent = {"observations.json": b"[]"}

    assert sync_state.staging_fingerprint(present.get) != sync_state.staging_fingerprint(absent.get)


# ── the Cypher, asserted on what is sent ──────────────────────────────────────


class _Tx:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))


def test_set_document_fingerprint_targets_the_live_document_by_id():
    tx = _Tx()

    sync_state.set_document_fingerprint(tx, "doc-1", "abc")

    assert tx.calls == [
        ("MATCH (d:Document {id: $doc_id}) SET d.fingerprint = $fingerprint",
         {"doc_id": "doc-1", "fingerprint": "abc"}),
    ]


def test_read_document_fingerprints_is_one_query_over_every_id(session):
    session._answers = [[{"id": "d1", "fingerprint": "f1"}, {"id": "d2", "fingerprint": None}]]

    got = sync_state.read_document_fingerprints(["d2", "d1", "d3", "d1"], timeout=2.0)

    assert got == {"d1": "f1", "d2": None}
    assert len(session.calls) == 1
    cypher, params = session.calls[0]
    assert "MATCH (d:Document) WHERE d.id IN $doc_ids" in cypher
    assert params == {"doc_ids": ["d1", "d2", "d3"]}
    assert session.timeouts == [2.0]


def test_read_document_fingerprints_of_nothing_never_connects(session):
    assert sync_state.read_document_fingerprints([]) == {}
    assert session.calls == []


def test_read_graph_bookmark_matches_on_vault_id(session):
    session._answers = [[{"commit": "c0ffee"}]]

    assert sync_state.read_graph_bookmark("vault-a", timeout=1.5) == "c0ffee"
    cypher, params = session.calls[0]
    assert cypher.startswith("MATCH (s:ArtmindSyncState {vault_id: $vault_id})")
    assert params == {"vault_id": "vault-a"}
    assert session.timeouts == [1.5]


def test_read_graph_bookmark_is_none_when_the_vault_has_no_node(session):
    assert sync_state.read_graph_bookmark("vault-a") is None


def test_write_graph_bookmark_merges_one_node_per_vault(session):
    sync_state.write_graph_bookmark("vault-a", "c0ffee")

    cypher, params = session.calls[0]
    assert cypher.startswith("MERGE (s:ArtmindSyncState {vault_id: $vault_id})")
    assert "SET s.last_applied_commit = $commit, s.applied_at = $applied_at" in cypher
    assert params["vault_id"] == "vault-a"
    assert params["commit"] == "c0ffee"
    assert params["applied_at"].endswith("+00:00")


def test_the_setup_creates_the_vault_id_constraint():
    from artmind.setup import _setup_neo4j

    tx = _Tx()
    _setup_neo4j(tx, 768)

    assert (sync_state.CONSTRAINT_CYPHER, {}) in tx.calls
    assert "FOR (n:ArtmindSyncState) REQUIRE n.vault_id IS UNIQUE" in sync_state.CONSTRAINT_CYPHER
