"""The shape-audit bundle: text, graph subgraph, staging, schema."""

import json
import time
from contextlib import contextmanager

import pytest

from artmind.reviews import shape
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", tmp_path / "schemas")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "d_schema.yaml").write_text(
        "name: d\nentity_types:\n  PERSON: {kind: recurrent, description: A person.}\n"
        "  LOCATION: {kind: recurrent, description: A place.}\n  EVENT: {kind: occurrent, description: x}\n")
    folder = tmp_path / "kg" / "d" / "2031-03-04"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "2031-03-04", "path": "j/2031-03-04.md",
                                                      "_valid_from": "2031-03-04"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "doc-1_001", "doc_id": "doc-1",
                                                     "text": "I went to Lakeport with Mira. We stayed late!"}]))
    (folder / "observations.json").write_text(json.dumps([
        {"id": "o1", "name": "Mira", "entity_class": "PERSON", "chunk_id": "doc-1_001", "role": "friend"},
        {"id": "o2", "name": "Lakeport", "entity_class": "LOCATION", "chunk_id": "doc-1_001"},
        {"id": "o3", "name": "Other", "entity_class": "PERSON", "chunk_id": "doc-1_002"}]))
    (folder / "relationships.json").write_text(json.dumps([
        {"source_name": "Mira", "target_name": "Lakeport", "rel_type": "visits", "chunk_id": "doc-1_001"},
        {"source_name": "Mira", "target_name": "doc-1_001", "rel_type": "EXTRACTED_FROM", "chunk_id": "doc-1_001"}]))
    (tmp_path / "kg" / "d" / "other").mkdir()
    (tmp_path / "kg" / "d" / "other" / "document.json").write_text(json.dumps({"id": "doc-2"}))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


@pytest.fixture
def fake_graph(monkeypatch):
    from artmind import graph_query

    calls = []
    rows = [
        {"entity": {"_id": "e-m", "name": "Mira", "entity_class": "PERSON", "role": "friend", "embedding": [0.1]},
         "rels": [{"rel_type": "VISITS", "out": True, "other": "e-l", "other_name": "Lakeport",
                   "other_class": "LOCATION", "observation_count": 1}]},
        {"entity": {"_id": "e-l", "name": "Lakeport", "entity_class": "LOCATION"},
         "rels": [{"rel_type": "VISITS", "out": False, "other": "e-m", "other_name": "Mira",
                   "other_class": "PERSON", "observation_count": 1}]},
    ]

    class _Session:
        def run(self, cypher, **params):
            calls.append((cypher, params))
            return _FakeResult(rows)

    @contextmanager
    def fake_read_session():
        yield _Session()

    monkeypatch.setattr(graph_query, "read_session", fake_read_session)
    monkeypatch.setattr(graph_query, "chunks_by_id", lambda domains, ids, **k: {"rows": [
        {"chunk": {"id": "doc-1_001", "text": "I went to Lakeport with Mira. We stayed late!"},
         "document": {"path": "j/2031-03-04.md", "valid_from": "2031-03-04"}}]})
    return calls


def test_doc_id_of():
    assert shape.doc_id_of("01ab-cd_001") == "01ab-cd"
    assert shape.doc_id_of("plain") == "plain"


def test_staging_for_chunk_filters_by_chunk():
    s = shape.staging_for_chunk("d", "doc-1_001")
    assert s["document"]["id"] == "doc-1"
    assert s["chunk"]["text"].startswith("I went to Lakeport")
    assert [o["name"] for o in s["observations"]] == ["Mira", "Lakeport"]
    assert [r["rel_type"] for r in s["relationships"]] == ["visits"]
    assert shape.staging_for_chunk("d", "doc-9_001") is None


def test_sentences():
    assert shape.sentences("One. Two!\n\nThree?") == [{"n": 1, "text": "One."}, {"n": 2, "text": "Two!"},
                                                     {"n": 3, "text": "Three?"}]


def test_graph_for_chunks_query_and_normalization(fake_graph):
    g = shape.graph_for_chunks(["d"], ["doc-1_001"])
    cypher, params = fake_graph[0]
    assert "EXTRACTED_FROM" in cypher and "AGGREGATES" in cypher and "RELATES_TO" in cypher
    assert params["chunk_ids"] == ["doc-1_001"] and params["domains"] == ["d"]
    names = {n["name"]: n for n in g["nodes"]}
    assert names["Mira"]["properties"] == {"role": "friend"}  # embedding stripped, meta keys dropped
    assert g["edges"] == [{"source": "e-m", "target": "e-l", "rel_type": "VISITS",
                           "properties": {"observation_count": 1}}]


def test_bundle_with_explicit_chunks_and_pattern(fake_graph):
    pattern = [{"name": "Mira"}, {"rel": "WENT_TO"}, {"name": "Lakeport"}]
    b = shape.bundle(domain="d", chunk_ids=["doc-1_001"], pattern=pattern)
    assert b["chunk_ids"] == ["doc-1_001"]
    assert b["text"][0]["sentences"][0] == {"n": 1, "text": "I went to Lakeport with Mira."}
    assert b["staging"]["doc-1_001"]["observations"][0]["name"] == "Mira"
    assert set(b["schema"]["entity_types"]) == {"PERSON", "LOCATION"}  # only classes present
    assert b["answerability"]["graph"]["matched"] is False   # only VISITS, no WENT_TO
    assert b["answerability"]["staging"]["matched"] is False


def _trace_with_cited_chunk():
    store.insert_turn("t1", source="admin", question="Where did Mira go?", started_at=time.time())
    store.update_turn("t1", answer="Lakeport.\n\n### Sources\n- [x](<j/2031-03-04.md>) — d\n", status="done",
                      domains='["d"]', retrieval_path="[]")
    store.insert_span({"span_id": "s1", "trace_id": "t1", "command": "query vector-text", "args": "{}",
                       "exit_code": 0, "duration_ms": 1, "rows": 2, "rows_total": None, "truncated": 0,
                       "evidence": json.dumps({"chunks": [{"chunk_id": "doc-1_001", "doc_id": "doc-1"},
                                                          {"chunk_id": "doc-5_001", "doc_id": "doc-5"}],
                                               "documents": [{"doc_id": "doc-1", "path": "j/2031-03-04.md"},
                                                             {"doc_id": "doc-5", "path": "j/other.md"}]}),
                       "started_at": time.time()}, [])


def test_default_chunks_prefers_cited_documents():
    _trace_with_cited_chunk()
    from artmind.traces.analysis import show

    assert shape.default_chunks(show("t1")) == ["doc-1_001"]


def test_bundle_from_trace(fake_graph):
    _trace_with_cited_chunk()
    b = shape.bundle(trace_id="t1")
    assert b["domain"] == "d" and b["chunk_ids"] == ["doc-1_001"]
    assert b["question"] == "Where did Mira go?"


def test_bundle_errors():
    with pytest.raises(shape.ShapeError):
        shape.bundle(trace_id="missing")
    with pytest.raises(shape.ShapeError):
        shape.bundle(chunk_ids=["doc-1_001"])  # no domain
