import json
from contextlib import contextmanager

import pytest
from _neo4j_recorder import Recorder

from artmind import ingest, passage_store, passages


ROWS = [{"id": "d1_001_p01", "chunk_id": "d1_001", "props": {"id": "d1_001_p01", "text": "t"}}]


def test_write_chunk_passages_retires_old_then_merges_new():
    tx = Recorder()
    passage_store.write_chunk_passages(tx, "d1_001", ROWS)
    (retire, p1), (merge, p2) = tx.calls
    assert "MATCH (p:DocPassage {chunk_id: $chunk_id})" in retire and "SET p:DocPassageHistory" in retire
    assert p1 == {"chunk_id": "d1_001"}
    assert "UNWIND $rows AS row" in merge and "OPTIONAL MATCH (o2:DocPassageHistory {id: row.id})" in merge
    assert "SET n = row.props" in merge and "MERGE (n)-[:PART_OF]->(c)" in merge
    assert p2 == {"rows": ROWS}


def test_write_chunk_passages_with_no_rows_only_retires():
    tx = Recorder()
    passage_store.write_chunk_passages(tx, "d1_001", [])
    assert len(tx.calls) == 1


def test_retract_prior_version_moves_passages_to_history():
    tx = Recorder(lambda c, p: [{"n": 0}])
    ingest._retract_prior_version(tx, "papers", "d1")
    swaps = tx.queries_containing("MATCH (p:DocPassage {doc_id: $doc_id})")
    assert swaps and "REMOVE p:DocPassage SET p:DocPassageHistory" in swaps[0][0]
    assert swaps[0][1] == {"doc_id": "d1"}


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


@pytest.fixture()
def session(monkeypatch):
    import artmind.graph_query as graph_query

    fake = _Session()

    @contextmanager
    def _fake(*args, **kwargs):
        yield fake

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ingest, "_ensure_neo4j_schema", lambda *a, **k: None)
    return fake


def _stage(folder, document):
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps(document))
    chunks = [
        {"id": "doc-1_001", "doc_id": "doc-1", "_domain": "general", "text": "Alpha beta gamma.",
         "embedding": [0.1, 0.2]},
        {"id": "doc-1_002", "doc_id": "doc-1", "_domain": "general", "text": "Delta epsilon."},
    ]
    (folder / "chunks.json").write_text(json.dumps(chunks))
    (folder / "observations.json").write_text("[]")
    (folder / "relationships.json").write_text("[]")
    return chunks


def _commit(tmp_path, session, monkeypatch, document):
    written = []
    monkeypatch.setattr(passage_store, "write_chunk_passages",
                        lambda tx, chunk_id, rows: written.append((chunk_id, rows)) or len(rows))
    chunks = _stage(tmp_path / "doc", document)
    summary = ingest._commit_document_tx(session, ingest._load_staged(tmp_path / "doc", "general"), True)
    return chunks, written, summary


def test_commit_chunk_merge_carries_no_embedding(tmp_path, session, monkeypatch):
    _commit(tmp_path, session, monkeypatch, {"id": "doc-1", "name": "a.md", "title": "Title"})
    chunk_merges = [p for c, p in session.calls if p.get("id") in ("doc-1_001", "doc-1_002") and "props" in p]
    assert chunk_merges
    for p in chunk_merges:
        assert "embedding" not in p["props"]


def test_commit_writes_passages_per_chunk_with_title(tmp_path, session, monkeypatch):
    chunks, written, summary = _commit(tmp_path, session, monkeypatch, {"id": "doc-1", "name": "a.md", "title": "Title"})
    assert [cid for cid, _ in written] == ["doc-1_001", "doc-1_002"]
    for (cid, rows), chunk in zip(written, chunks):
        assert rows == passages.passage_rows(chunk, "Title")
    assert summary["passage_chunk_ids"] == ["doc-1_001", "doc-1_002"]


def test_commit_title_falls_back_to_name(tmp_path, session, monkeypatch):
    chunks, written, _ = _commit(tmp_path, session, monkeypatch, {"id": "doc-1", "name": "a.md"})
    assert written[0][1] == passages.passage_rows(chunks[0], "a.md")
