"""`graph timeline` covers occasion classes (spec 2026-11-15 §5.2)."""
from _neo4j_recorder import Recorder

from artmind import graph_query as gq

SCHEMA = {"entity_types": {
    "EVENT": {"kind": "occurrent"},
    "ACTIVITY": {"kind": "occurrent", "identity": "occasion"},
    "EMOTION": {"kind": "recurrent", "identity": "category"},
    "AMENDMENT": {"kind": "occurrent", "identity": "name"},
}}


def _run(monkeypatch, **kw):
    rec = Recorder()
    monkeypatch.setattr("artmind.temporal.load_schema", lambda d: SCHEMA)
    monkeypatch.setattr(gq, "read_session", lambda: rec)
    gq.timeline(["pj"], **kw)
    return rec


def test_timeline_labels_are_the_occasion_classes(monkeypatch):
    rec = _run(monkeypatch, from_="2024-01-01", to="2024-12-31")
    cypher, params = rec.calls[0]
    assert params["labels"] == ["ACTIVITY", "EVENT"]
    assert params["from_"] == "2024-01-01" and params["to"] == "2024-12-31"


def test_timeline_rows_carry_precision_and_time_and_undated_sort_last(monkeypatch):
    cypher, _ = _run(monkeypatch).calls[0]
    assert "valid_precision: e._valid_precision" in cypher
    assert "valid_time_of_day: e._valid_time_of_day" in cypher
    order = cypher.split("ORDER BY", 1)[1]
    assert "coalesce(e._valid_precision, '') = 'none'" in order
    assert order.index("'none'") < order.index("e._valid_from,")


def test_undated_rows_are_not_dropped_by_a_window(monkeypatch):
    cypher, _ = _run(monkeypatch, from_="2024-01-01").calls[0]
    assert "'none') OR e._valid_from >= $from_" in cypher
