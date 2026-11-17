"""`graph_query.valid_predicate` (spec 2026-11-15 §6.2, §7.1): pure string checks, plus the
Cypher and params the readers actually send (recorder-based)."""
from _neo4j_recorder import Recorder

from artmind import graph_query as gq


def test_predicate_reads_prefixed_names_only():
    text = gq.valid_predicate("p")
    assert "p._valid_from" in text and "p._valid_to" in text
    assert "p.valid_from" not in text and "p.valid_to" not in text and "coalesce" not in text


def test_predicate_treats_precision_none_as_undated():
    assert "p._valid_precision = 'none'" in gq.valid_predicate("p")


def test_predicate_is_a_point_with_an_exclusive_end():
    text = gq.valid_predicate("p")
    assert "p._valid_from <= $asOf" in text and "p._valid_to > $asOf" in text
    assert text.startswith("($asOf IS NULL OR ")


def test_excluding_undated_negates_the_undated_branch():
    text = gq.valid_predicate("p", include_undated=False)
    assert "NOT (p._valid_from IS NULL OR p._valid_precision = 'none')" in text


def test_a_custom_parameter_name():
    assert "$at IS NULL" in gq.valid_predicate("d", "at") and "$asOf" not in gq.valid_predicate("d", "at")


def test_asof_predicate_is_gone():
    assert not hasattr(gq, "asof_predicate")


def test_vector_text_sends_the_widened_as_of_and_the_predicate(monkeypatch):
    from artmind import vector_query as vq

    rec = Recorder(lambda c, p: [])
    monkeypatch.setattr(vq, "read_session", lambda: rec)
    monkeypatch.setattr(vq, "embed_question", lambda *a, **k: [0.0] * 4)
    vq.vector_text_search(["pj"], "lake", as_of="2026")
    sent = [(c, p) for c, p in rec.calls if "DocPassage" in c or "passage_text_ft" in c]
    assert len(sent) == 2
    for cypher, params in sent:
        assert params["asOf"] == "2026-12-31"
        assert gq.valid_predicate("p") in cypher
        assert "p.valid_from" not in cypher and "coalesce(p" not in cypher


def test_vector_text_without_as_of_sends_no_predicate(monkeypatch):
    from artmind import vector_query as vq

    rec = Recorder(lambda c, p: [])
    monkeypatch.setattr(vq, "read_session", lambda: rec)
    monkeypatch.setattr(vq, "embed_question", lambda *a, **k: [0.0] * 4)
    vq.vector_text_search(["pj"], "lake")
    for cypher, params in rec.calls:
        assert "_valid_precision" not in cypher and "asOf" not in params


def test_docs_list_filters_documents_with_the_predicate(monkeypatch):
    seen = []
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: seen.append((c, p)) or [])
    gq.filing_listing(domains=["pj"], as_of="2026-05")
    cypher, params = seen[0]
    assert gq.valid_predicate("d") in cypher and params["asOf"] == "2026-05-31"


def test_chunks_query_reads_prefixed_dates_only():
    cypher = gq._chunks_query(1, "2026-01-01")
    assert gq.valid_predicate("c") in cypher
    assert "valid_from: c._valid_from, valid_to: c._valid_to" in cypher
    assert "valid_to: sibs[j]._valid_to" in cypher
    assert "valid_from: d._valid_from, valid_to: d._valid_to" in cypher and "date: d._valid_from" in cypher
    assert "coalesce(d._valid_from" not in cypher and ".valid_to," not in cypher


def test_survey_window_and_periods_skip_default_dates():
    from artmind import survey

    window = survey._windowed("RETURN s { .id }", "s", {"date_from": "2026-01-01", "date_to": None})
    assert "d._valid_from >= $date_from" in window and "coalesce(d._valid_precision, 'day') <> 'none'" in window
    assert "coalesce(d._valid_precision, 'day') <> 'none'" in survey._PERIOD_HITS
    assert "WHEN coalesce(d._valid_precision, 'day') = 'none' THEN null" in survey._PERIOD_DATES
    for text in (survey._DOCUMENTS, survey._PERIOD_DATES, survey._PERIOD_HITS, window):
        assert "d.valid_from" not in text


def test_topic_grid_and_members_read_prefixed_dates_only():
    from artmind.topics import grid, members

    for text in (grid._MATCHING, members._CANDIDATES):
        assert "d._valid_from" in text and "d.valid_from" not in text
        assert "coalesce(d._valid_precision, 'day') = 'none'" in text


def test_conflicts_read_prefixed_dates_only():
    from artmind import conflicts

    rec = Recorder(lambda c, p: [{"valid_from": "2026-01-01", "version": "1"}])
    conflicts._doc_validity(rec, [{"doc_id": "d1"}])
    cypher, params = rec.calls[0]
    assert "d._valid_from AS valid_from" in cypher and "d.valid_from" not in cypher and params == {"id": "d1"}
