"""Bound outputs computed from a step's payload (P3 spec §4.2). Invented payloads."""

import pytest

from artmind.query_plan import program as prog
from artmind.query_run import facts

VT = {"command": "query vector-text", "rows": [
    {"passage_id": "p1", "chunk_id": "c1", "text": "t", "doc": {"id": "d1", "title": "Day 2026-03-01", "date": "2026-03-01"}},
    {"passage_id": "p2", "chunk_id": "c2", "text": "t", "doc": {"id": "d2", "title": "Day 2026-03-02", "date": "2026-03-02"}},
    {"passage_id": "p3", "chunk_id": "c1", "text": "t", "doc": {"id": "d1", "title": "Day 2026-03-01", "date": "2026-03-01"}},
    {"passage_id": "p4", "chunk_id": "c4", "text": "t", "doc": {"id": "d4", "title": "Day 2026-03-09", "date": "2026-03-09"}}]}
RESOLVE = {"command": "query entity-resolve", "rows": [{"_id": "e1", "name": "A"}, {"_id": "e2", "name": "B"}]}
RESOLVE_REAL = {"command": "query entity-resolve", "rows": [{"entity": {"_id": "e1", "name": "A"}},
                                                            {"entity": {"_id": "e2", "name": "B"}}]}


def test_every_declared_output_has_a_rule():
    assert set(prog.BIND_OUTPUTS) == set(facts.OUTPUT_RULES)


def test_scalar_outputs_take_the_best_ranked_row():
    out = facts.bind_outputs(VT)
    assert (out["top_chunk"], out["top_passage"], out["top_doc"], out["top_date"]) == ("c1", "p1", "Day 2026-03-01", "2026-03-01")


def test_list_outputs_are_ranked_and_deduplicated():
    out = facts.bind_outputs(VT)
    assert out["chunk_ids"] == ["c1", "c2", "c4"]
    assert out["doc_names"] == ["Day 2026-03-01", "Day 2026-03-02", "Day 2026-03-09"]


def test_window_docs_are_the_documents_dated_within_the_window():
    out = facts.bind_outputs(VT, window_days=3)
    assert out["window_docs"] == ["Day 2026-03-01", "Day 2026-03-02"]


def test_entity_outputs():
    out = facts.bind_outputs(RESOLVE)
    assert out["top_entity"] == "e1" and out["entity_ids"] == ["e1", "e2"]


def test_entity_outputs_from_the_real_entity_resolve_row_shape():
    out = facts.bind_outputs(RESOLVE_REAL)
    assert out["top_entity"] == "e1" and out["entity_ids"] == ["e1", "e2"]


@pytest.mark.parametrize("payload", [None, {}, {"command": "query vector-text", "rows": []}])
def test_nothing_to_bind_is_none_or_empty(payload):
    out = facts.bind_outputs(payload)
    assert out["top_chunk"] is None and out["chunk_ids"] == []
