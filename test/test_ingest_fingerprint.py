"""Every graph write of a staging folder records its fingerprint on the
Document (spec 2026-09-26 §6 A3). All commit paths -- ingest, table2graph,
write-to-graph, pull-kg, archive restore, dashboard import, vault sync --
reach the graph through `ingest._write_to_neo4j`, so these tests drive that
function (and table2graph's own writer) and assert on the Cypher parameters
actually sent (CLAUDE.md §3)."""
import json
from contextlib import contextmanager

import artmind.ingest as ing
from artmind import sync_state

_SET_FP = "MATCH (d:Document {id: $doc_id}) SET d.fingerprint = $fingerprint"


class _Result:
    def single(self):
        return None

    def data(self):
        return []

    def consume(self):
        return None

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

    def fingerprint_writes(self):
        return [params for cypher, params in self.calls if cypher == _SET_FP]


def _patch(monkeypatch):
    import artmind.graph_query as graph_query

    session = _Session()

    @contextmanager
    def _fake(*args, **kwargs):
        yield session

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    return session


def _stage(folder, observations='[{"id": "o1", "key": "Acme|ORG|banking"}]'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "a.md"}), encoding="utf-8")
    (folder / "chunks.json").write_text("[]", encoding="utf-8")
    (folder / "relationships.json").write_text("[]", encoding="utf-8")
    (folder / "observations.json").write_text(observations, encoding="utf-8")
    return folder


def test_load_staged_carries_the_folder_fingerprint(tmp_path):
    folder = _stage(tmp_path / "doc")

    staged = ing._load_staged(folder, "banking")

    assert staged["fingerprint"] == sync_state.folder_fingerprint(folder)
    assert staged["fingerprint"] is not None


def test_a_graph_write_records_the_fingerprint_of_the_bytes_on_disk(tmp_path, monkeypatch):
    session = _patch(monkeypatch)
    folder = _stage(tmp_path / "doc")

    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)

    assert session.fingerprint_writes() == [
        {"doc_id": "doc-1", "fingerprint": sync_state.folder_fingerprint(folder)}
    ]


def test_the_fingerprint_is_written_after_the_document_node_exists(tmp_path, monkeypatch):
    """`SET d.fingerprint` matches on `(:Document {id})`; run before the
    Document MERGE, it would silently match nothing on a first ingest."""
    session = _patch(monkeypatch)
    folder = _stage(tmp_path / "doc")

    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)

    cyphers = [c for c, _ in session.calls]
    merge_doc = next(i for i, c in enumerate(cyphers) if "CREATE (n:Document {id: $id})" in c)
    assert cyphers.index(_SET_FP) > merge_doc


def test_a_changed_folder_records_a_changed_fingerprint(tmp_path, monkeypatch):
    session = _patch(monkeypatch)
    folder = _stage(tmp_path / "doc")
    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)
    _stage(folder, observations='[{"id": "o2", "key": "Beta|ORG|banking"}]')
    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)

    first, second = session.fingerprint_writes()
    assert first["fingerprint"] != second["fingerprint"]


def test_table2graph_staging_records_its_fingerprint_too(tmp_path, monkeypatch):
    """table2graph writes its staging with `write_staged` (which adds a
    `table2graph_report.json` outside the fingerprinted set) and commits it
    through `_write_to_neo4j`; the fingerprint must match the four files."""
    from artmind.table2graph import write_staged

    session = _patch(monkeypatch)
    folder = tmp_path / "table__accounts"
    staged = {
        "document": {"id": "table:banking:accounts", "name": "accounts"},
        "chunks": [],
        "observations": [{"id": "o1", "key": "Acme|ORG|banking"}],
        "relationships": [],
    }
    write_staged(staged, {"rows": 1}, folder)

    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)

    assert session.fingerprint_writes() == [
        {"doc_id": "table:banking:accounts", "fingerprint": sync_state.folder_fingerprint(folder)}
    ]
    assert (folder / "table2graph_report.json").is_file()
