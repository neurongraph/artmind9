"""Documents and entities store their date in `_valid_from`; reads must not use bare `valid_from`."""
from __future__ import annotations

import re

from artmind import graph_query, survey, vector_query
from artmind.graph_query import asof_predicate


def test_vector_chunk_fetch_reads_underscore_date():
    cypher = vector_query._chunk_fetch_cypher()
    assert "date: coalesce(document._valid_from, document.valid_from)" in cypher
    assert "document.valid_from," not in cypher


def test_survey_documents_query_reads_underscore_date():
    assert "coalesce(d._valid_from, d.valid_from) AS date" in survey._DOCUMENTS


def test_asof_predicate_coalesces():
    pred = asof_predicate("d")
    assert "coalesce(d._valid_from, d.valid_from)" in pred
    assert "coalesce(d.valid_to, d._valid_to)" in pred
    assert not re.search(r"(?<![_.\w])d\.valid_from IS NULL", pred)


def test_chunks_query_document_projection():
    cypher = graph_query._chunks_query(0, "2020-01-01")
    assert "valid_from: coalesce(d._valid_from, d.valid_from)" in cypher
    assert "date: coalesce(d._valid_from, d.valid_from)" in cypher


def test_timeline_window_reads_underscore_date(monkeypatch):
    seen = {}
    monkeypatch.setattr("artmind.temporal.load_schema", lambda d: {"entity_types": {"EVENT": {"kind": "occurrent"}}})
    monkeypatch.setattr(graph_query, "_run_read_query", lambda c, p: seen.setdefault("c", c) and [])
    graph_query.timeline(["d"], from_="2020-01-01", to="2021-01-01")
    cypher = seen["c"]
    assert "coalesce(e._valid_from, e.valid_from) >= $from_" in cypher
    assert "coalesce(e._valid_from, e.valid_from) <= $to" in cypher
    assert "ORDER BY coalesce(e._valid_from, e.valid_from)" in cypher
