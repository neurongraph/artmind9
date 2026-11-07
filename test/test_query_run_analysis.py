"""traces analysis on run turns (P3 spec §7.1): the agent's calls are top-level spans; steps are children."""

import json

import pytest

from artmind.traces import analysis, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _span(trace_id, sid, command, parent=None, started=1_700_000_000.5):
    store.insert_span({"span_id": sid, "trace_id": trace_id, "command": command, "args": json.dumps({"domain": ["b"]}),
                       "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0, "evidence": "{}",
                       "started_at": started, "parent_span_id": parent}, [])


def _turn(trace_id):
    store.insert_turn(trace_id, source="admin", question="q", session_id=f"s-{trace_id}", started_at=1_700_000_000.0)
    store.update_turn(trace_id, status="done", domains=json.dumps(["b"]), completed_at=1_700_000_001.0)


def test_top_level_drops_children():
    spans = [{"span_id": "r", "parent_span_id": None}, {"span_id": "c", "parent_span_id": "r"}]
    assert [s["span_id"] for s in analysis.top_level(spans)] == ["r"]


def test_stats_count_run_turns_and_added_after_run():
    _turn("t1")
    _span("t1", "r1", "query run")
    for i, cmd in enumerate(("query vector-text", "query survey", "query graph pattern7")):
        _span("t1", f"c{i}", cmd, parent="r1", started=1_700_000_000.6)
    _turn("t2")
    _span("t2", "r2", "query run")
    _span("t2", "x2", "query vector-text", started=1_700_000_000.9)
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    assert row["run"] == {"run_turns": 2, "added_after_run": 0.5}


def test_child_spans_do_not_raise_deep_fallback():
    _turn("t1")
    _span("t1", "r1", "query run")
    for i, cmd in enumerate(("query vector-text", "query survey", "query graph pattern7")):
        _span("t1", f"c{i}", cmd, parent="r1")
    sig = analysis.signals({"turns": 1}, analysis.top_level(store.list_spans("t1")), None, {"status": "n/a"})
    assert "deep_fallback" not in sig


def test_show_nests_children_under_their_run():
    _turn("t1")
    _span("t1", "r1", "query run")
    _span("t1", "c1", "query vector-text", parent="r1")
    shown = analysis.show("t1")
    (run,) = [s for s in shown["unattributed_spans"] if s["span_id"] == "r1"]
    assert [c["span_id"] for c in run["children"]] == ["c1"]
    assert all(s["span_id"] != "c1" for s in shown["unattributed_spans"])
