# test/test_summary_store.py
from _neo4j_recorder import Recorder

from artmind import summary_store

DATA = {
    "version": 1, "model": "m", "prompt_version": 1,
    "document": {"key": "dk", "summary": "The whole thing."},
    "chunks": [{"chunk_id": "c1", "key": "k1", "summary": "Part one."},
               {"chunk_id": "c2", "key": "k2", "summary": "Part two."}],
}
DOC = {"id": "d1", "_domain": "notes", "valid_from": "2026-01-01"}


def test_rows_cover_both_levels_with_deterministic_ids():
    rows = summary_store.summary_rows(DATA, DOC)
    assert [r["id"] for r in rows] == ["d1:summary", "c1:summary", "c2:summary"]
    doc, c1 = rows[0], rows[1]
    assert doc["props"] == {"id": "d1:summary", "level": "document", "doc_id": "d1", "_domain": "notes",
                            "text": "The whole thing.", "key": "dk", "valid_from": "2026-01-01"}
    assert doc["target"] == "d1" and doc["target_label"] == "Document"
    assert c1["props"]["level"] == "chunk" and c1["props"]["chunk_id"] == "c1"
    assert c1["target"] == "c1" and c1["target_label"] == "DocChunk"


def test_no_document_summary_still_writes_chunk_rows():
    rows = summary_store.summary_rows({**DATA, "document": None}, DOC)
    assert [r["props"]["level"] for r in rows] == ["chunk", "chunk"]


def test_missing_or_malformed_data_writes_nothing():
    assert summary_store.summary_rows(None, DOC) == []
    assert summary_store.summary_rows({"chunks": "bad"}, DOC) == []


def test_write_retires_then_merges_and_links_each_level():
    tx = Recorder()
    n = summary_store.write_document_summaries(tx, "d1", summary_store.summary_rows(DATA, DOC))
    assert n == 3
    retire, merge_doc, merge_chunk = tx.calls
    assert "MATCH (s:DocSummary {doc_id: $doc_id})" in retire[0] and "SET s:DocSummaryHistory" in retire[0]
    assert retire[1] == {"doc_id": "d1"}
    assert "MATCH (t:Document {id: row.target})" in merge_doc[0]
    assert [r["id"] for r in merge_doc[1]["rows"]] == ["d1:summary"]
    assert "MATCH (t:DocChunk {id: row.target})" in merge_chunk[0]
    assert [r["id"] for r in merge_chunk[1]["rows"]] == ["c1:summary", "c2:summary"]
    assert "MERGE (n)-[:SUMMARIZES]->(t)" in merge_chunk[0]
    # an unchanged key keeps its vector; a changed one drops it
    assert "CASE WHEN old_key = row.props.key THEN old_embedding END" in merge_chunk[0]


def test_write_with_no_rows_only_retires():
    tx = Recorder()
    assert summary_store.write_document_summaries(tx, "d1", []) == 0
    assert len(tx.calls) == 1


def test_retract_and_lifecycle_swap_summaries():
    import artmind.ingest as ing
    from artmind import lifecycle

    tx = Recorder(respond=lambda c, p: [{"n": 0}])
    ing._retract_prior_version(tx, "notes", "d1")
    assert tx.queries_containing("MATCH (s:DocSummary {doc_id: $doc_id})")
    assert any("SET s:DocSummaryHistory" in c for c, _ in tx.calls)

    tx = Recorder(respond=lambda c, p: [{"n": 0}])
    import artmind.projection as projection
    lifecycle_keys = projection.keys_for_document
    try:
        projection.keys_for_document = lambda tx, doc_id: []
        lifecycle._transition(tx, "d1", to_history=True, rebuild=False)
        assert any("MATCH (s:DocSummary {doc_id: $doc_id}) REMOVE s:DocSummary SET s:DocSummaryHistory" in c
                   for c, _ in tx.calls)
        tx.calls.clear()
        lifecycle._transition(tx, "d1", to_history=False, rebuild=False)
        assert any("MATCH (s:DocSummaryHistory {doc_id: $doc_id}) REMOVE s:DocSummaryHistory SET s:DocSummary" in c
                   for c, _ in tx.calls)
    finally:
        projection.keys_for_document = lifecycle_keys
