"""A1b — relationship→chunk provenance on Entity→Entity edges.

_write_to_neo4j must accumulate doc_ids/chunk_ids as idempotent sets on the
collapsed edge (and its bidirectional reverse), keeping them out of the scalar
rel props. We assert the actual Cypher and params sent, never summary counts —
a recording session returns truthy for any query (per CLAUDE.md).
"""
import json

import artmind.ingest as ing


class _Rec:
    def single(self):
        return None

    def data(self):
        return []


class _RecordingSession:
    def __init__(self, runs):
        self.runs = runs

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec()


class _Driver:
    def __init__(self, session):
        self._session = session

    def session(self, **kwargs):
        session = self._session

        class _Ctx:
            def __enter__(self):
                return session

            def __exit__(self, *exc):
                return False

        return _Ctx()

    def close(self):
        pass


def _run_write(tmp_path, monkeypatch, relationships):
    (tmp_path / "document.json").write_text(
        json.dumps({"id": "docA", "name": "a.md", "domain": "banking"}), encoding="utf-8"
    )
    (tmp_path / "chunks.json").write_text("[]", encoding="utf-8")
    (tmp_path / "entities.json").write_text("[]", encoding="utf-8")
    (tmp_path / "properties.json").write_text("[]", encoding="utf-8")
    (tmp_path / "relationships.json").write_text(json.dumps(relationships), encoding="utf-8")

    runs: list = []
    session = _RecordingSession(runs)
    monkeypatch.setattr(
        ing,
        "GraphDatabase",
        type("G", (), {"driver": staticmethod(lambda *a, **k: _Driver(session))}),
    )
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", lambda *a, **k: 0)

    assert ing._write_to_neo4j(tmp_path) is True
    return runs


def _entity_edge_runs(runs):
    return [
        (c, k)
        for c, k in runs
        if "apoc.merge.relationship" in c and "MATCH (src:Entity" in c
    ]


def test_edge_write_accumulates_doc_and_chunk_ids(tmp_path, monkeypatch):
    rel = {
        "source_name": "Bank",
        "target_name": "Customer",
        "rel_type": "SERVES",
        "chunk_id": "docA_002",
        "doc_id": "docA",
        "description": "the bank serves the customer",
    }
    runs = _run_write(tmp_path, monkeypatch, [rel])
    edges = _entity_edge_runs(runs)
    assert len(edges) == 1, "one forward Entity→Entity edge expected"
    cypher, kwargs = edges[0]

    # Idempotent set-union accumulation, not a blind overwrite.
    assert "apoc.coll.toSet" in cypher
    assert "coalesce(rel.doc_ids, [])" in cypher
    assert "coalesce(rel.chunk_ids, [])" in cypher
    assert kwargs["doc_ids"] == ["docA"]
    assert kwargs["chunk_ids"] == ["docA_002"]

    # Provenance travels as its own params, never smuggled into scalar rel props.
    assert "doc_id" not in kwargs["props"]
    assert "chunk_id" not in kwargs["props"]
    assert kwargs["props"]["description"] == "the bank serves the customer"


def test_bidirectional_edge_carries_lists_both_ways(tmp_path, monkeypatch):
    rel = {
        "source_name": "A",
        "target_name": "B",
        "rel_type": "RELATED_TO",
        "chunk_id": "docA_001",
        "doc_id": "docA",
        "bidirectional": True,
    }
    runs = _run_write(tmp_path, monkeypatch, [rel])
    edges = _entity_edge_runs(runs)
    assert len(edges) == 2, "forward + reverse edge writes expected"
    for _, kwargs in edges:
        assert kwargs["doc_ids"] == ["docA"]
        assert kwargs["chunk_ids"] == ["docA_001"]


def test_edge_provenance_lists_empty_when_no_doc_id(tmp_path, monkeypatch):
    # A rel with no doc_id/chunk_id must union an empty list, never [""].
    rel = {"source_name": "A", "target_name": "B", "rel_type": "R"}
    # Drop the doc-level id too so there is genuinely nothing to attribute.
    (tmp_path / "document.json").write_text(
        json.dumps({"id": "", "name": "a.md", "domain": "banking"}), encoding="utf-8"
    )
    for name, val in (
        ("chunks.json", "[]"),
        ("entities.json", "[]"),
        ("properties.json", "[]"),
        ("relationships.json", json.dumps([rel])),
    ):
        (tmp_path / name).write_text(val, encoding="utf-8")

    runs: list = []
    session = _RecordingSession(runs)
    monkeypatch.setattr(
        ing, "GraphDatabase",
        type("G", (), {"driver": staticmethod(lambda *a, **k: _Driver(session))}),
    )
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", lambda *a, **k: 0)
    assert ing._write_to_neo4j(tmp_path) is True

    edges = _entity_edge_runs(runs)
    assert len(edges) == 1
    _, kwargs = edges[0]
    assert kwargs["doc_ids"] == []
    assert kwargs["chunk_ids"] == []
