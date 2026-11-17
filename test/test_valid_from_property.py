"""Documents and entities store their date in `_valid_from`; reads use only the prefixed names
(spec 2026-11-15 §7.1: no `coalesce` with the unprefixed ones)."""
from __future__ import annotations

import re

from artmind import graph_query, survey, vector_query
from artmind.graph_query import valid_predicate


def test_vector_chunk_fetch_reads_underscore_date():
    cypher = vector_query._chunk_fetch_cypher()
    assert "date: document._valid_from" in cypher
    assert "document.valid_from," not in cypher


def test_survey_documents_query_reads_underscore_date():
    assert "d._valid_from AS date" in survey._DOCUMENTS and "d.valid_from" not in survey._DOCUMENTS


def test_valid_predicate_reads_prefixed_names_only():
    pred = valid_predicate("d")
    assert "d._valid_from" in pred and "d._valid_to" in pred and "coalesce" not in pred
    assert not re.search(r"(?<![_.\w])d\.valid_(from|to)", pred)


def test_chunks_query_document_projection():
    cypher = graph_query._chunks_query(0, "2020-01-01")
    assert "valid_from: d._valid_from" in cypher
    assert "date: d._valid_from" in cypher and "d.valid_from" not in cypher


def test_timeline_window_reads_underscore_date(monkeypatch):
    seen = {}
    monkeypatch.setattr("artmind.temporal.load_schema", lambda d: {"entity_types": {"EVENT": {"kind": "occurrent"}}})
    monkeypatch.setattr(graph_query, "_run_read_query", lambda c, p: seen.setdefault("c", c) and [])
    graph_query.timeline(["d"], from_="2020-01-01", to="2021-01-01")
    cypher = seen["c"]
    assert "e._valid_from >= $from_" in cypher
    assert "e._valid_from <= $to" in cypher
    assert "ORDER BY e._valid_from" in cypher and "e.valid_from" not in cypher
    assert "valid_to: e._valid_to" in cypher
