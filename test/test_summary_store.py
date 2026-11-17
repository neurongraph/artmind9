# test/test_summary_store.py
from _neo4j_recorder import Recorder

from artmind import summary_store

DATA = {
    "version": 1, "model": "m", "prompt_version": 1,
    "document": {"key": "dk", "summary": "The whole thing."},
    "chunks": [{"chunk_id": "c1", "key": "k1", "summary": "Part one."},
               {"chunk_id": "c2", "key": "k2", "summary": "Part two."}],
}
DOC = {"id": "d1", "_domain": "notes", "_valid_from": "2026-01-01"}


def test_rows_cover_both_levels_with_deterministic_ids():
    rows = summary_store.summary_rows(DATA, DOC)
    assert [r["id"] for r in rows] == ["d1:summary", "c1:summary", "c2:summary"]
    doc, c1 = rows[0], rows[1]
    assert doc["props"] == {"id": "d1:summary", "level": "document", "doc_id": "d1", "_domain": "notes",
                            "text": "The whole thing.", "key": "dk", "_valid_from": "2026-01-01",
                            "_doc_valid_from": "2026-01-01"}
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
    n = summary_store.write_document_summaries(tx, "d1", summary_store.summary_rows(DATA, DOC),
                                               recorded_at="2026-11-16T00:00:00+00:00", retired_by="version:2")
    assert n == 3
    retire, merge_doc, merge_chunk = tx.calls
    assert "MATCH (s:DocSummary {doc_id: $doc_id})" in retire[0] and "SET s:DocSummaryHistory" in retire[0]
    assert retire[1] == {"doc_id": "d1", "now": "2026-11-16T00:00:00+00:00", "by": "version:2"}
    assert "MATCH (t:Document {id: row.target})" in merge_doc[0]
    assert [r["id"] for r in merge_doc[1]["rows"]] == ["d1:summary"]
    assert "MATCH (t:DocChunk {id: row.target})" in merge_chunk[0]
    assert [r["id"] for r in merge_chunk[1]["rows"]] == ["c1:summary", "c2:summary"]
    assert "MERGE (n)-[:SUMMARIZES]->(t)" in merge_chunk[0]
    # an unchanged key keeps its vector; a changed one drops it
    assert "CASE WHEN old_key = row.props.key THEN old_embedding END" in merge_chunk[0]


def test_write_with_no_rows_only_retires():
    tx = Recorder()
    assert summary_store.write_document_summaries(tx, "d1", [], recorded_at="2026-11-16T00:00:00+00:00", retired_by="version:2") == 0
    assert len(tx.calls) == 1


def test_retract_and_lifecycle_swap_summaries():
    import artmind.ingest as ing
    from artmind import lifecycle

    tx = Recorder(respond=lambda c, p: [{"n": 0}])
    ing._retract_prior_version(tx, "notes", "d1", retired_by="version:2")
    assert tx.queries_containing("MATCH (s:DocSummary {doc_id: $doc_id})")
    assert any("SET s:DocSummaryHistory" in c for c, _ in tx.calls)

    tx = Recorder(respond=lambda c, p: [{"n": 0}])
    import artmind.projection as projection
    lifecycle_keys = projection.keys_for_document
    try:
        projection.keys_for_document = lambda tx, doc_id: []
        lifecycle._transition(tx, "d1", to_history=True, rebuild=False)
        assert any("MATCH (s:DocSummary {doc_id: $doc_id}) REMOVE s:DocSummary SET s:DocSummaryHistory, "
                   "s._retired_at = $now, s._retired_by = $by" in c for c, _ in tx.calls)
        tx.calls.clear()
        lifecycle._transition(tx, "d1", to_history=False, rebuild=False)
        assert any("MATCH (s:DocSummaryHistory {doc_id: $doc_id}) WHERE (s._retired_by = 'retire' OR "
                   "s._retired_by STARTS WITH 'supersede:') REMOVE s:DocSummaryHistory SET s:DocSummary, "
                   "s._revived_at = $now REMOVE s._retired_at, s._retired_by" in c for c, _ in tx.calls)
    finally:
        projection.keys_for_document = lifecycle_keys


def test_summary_rows_carry_the_documents_prefixed_valid_time():
    from artmind.summary_store import summary_rows

    data = {"document": {"summary": "S", "key": "k"}, "chunks": [{"chunk_id": "c1", "summary": "C", "key": "k1"}]}
    document = {"id": "d1", "_domain": "pj", "_valid_from": "2024-03-30", "_valid_to": "2024-04-01"}
    rows = summary_rows(data, document)
    assert len(rows) == 2
    for row in rows:
        assert row["props"]["_valid_from"] == "2024-03-30"
        assert row["props"]["_doc_valid_to"] == "2024-04-01"
        assert "valid_from" not in row["props"]
    assert rows[0]["props"]["_valid_to"] == "2024-04-01"   # the document summary's own end


def test_chunk_summaries_take_their_chunks_date():
    from artmind.summary_store import summary_rows

    data = {"document": {"summary": "S"}, "chunks": [{"chunk_id": "c1", "summary": "C"}]}
    doc = {"id": "d1", "_domain": "pj", "_valid_from": "2026-01-01", "_valid_precision": "month"}
    rows = summary_rows(data, doc, chunks=[{"id": "c1", "_valid_from": "2026-01-14", "_valid_precision": "day"}])
    by_level = {r["props"]["level"]: r["props"] for r in rows}
    assert (by_level["chunk"]["_valid_from"], by_level["chunk"]["_valid_precision"]) == ("2026-01-14", "day")
    assert (by_level["document"]["_valid_from"], by_level["document"]["_valid_precision"]) == ("2026-01-01", "month")
    assert by_level["chunk"]["_doc_valid_from"] == by_level["document"]["_doc_valid_from"] == "2026-01-01"


def test_only_the_document_summary_takes_the_documents_end():
    from artmind.summary_store import summary_rows

    data = {"document": {"summary": "S"}, "chunks": [{"chunk_id": "c1", "summary": "C"}]}
    doc = {"id": "d1", "_domain": "pj", "_valid_from": "2026-01-01", "_valid_to": "2026-02-01"}
    by_level = {r["props"]["level"]: r["props"] for r in summary_rows(data, doc)}
    assert by_level["document"]["_valid_to"] == "2026-02-01"
    assert "_valid_to" not in by_level["chunk"] and by_level["chunk"]["_doc_valid_to"] == "2026-02-01"
