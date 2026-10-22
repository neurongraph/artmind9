"""artmind.traces.analysis: triage order, signals, stats, show (spec §4.1, §6)."""

import json
import time

import pytest

from artmind.traces import analysis, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _turn(tid, *, started_at, answer="ok", turns=2, fell_through=0, basis="graph", attempts=1, productive=1,
          domains=("b",)):
    store.insert_turn(tid, source="admin", question=f"q-{tid}", started_at=started_at)
    store.update_turn(tid, answer=answer, turns=turns, fell_through=fell_through, answer_basis=basis,
                      graph_attempts=attempts, graph_productive=productive, domains=json.dumps(list(domains)),
                      retrieval_path="[]", status="done")


def _span(tid, command, *, rows=1, exit_code=0, truncated=0, evidence=None, at=0.0):
    store.insert_span({"span_id": f"{tid}-{command}-{at}", "trace_id": tid, "command": command, "args": "{}",
                       "exit_code": exit_code, "duration_ms": 1, "rows": rows, "rows_total": None,
                       "truncated": truncated, "evidence": json.dumps(evidence or {}), "started_at": at}, [])


def test_parse_since():
    now = 1_000_000.0
    assert analysis.parse_since("7d", now=now) == now - 7 * 86400
    assert analysis.parse_since("12h", now=now) == now - 12 * 3600
    assert analysis.parse_since(None, now=now) is None
    assert analysis.parse_since("2026-10-01", now=now) > 0
    with pytest.raises(ValueError):
        analysis.parse_since("soon", now=now)


def test_signals_each_fire():
    turn = {"fell_through": 1, "answer_basis": "vector", "graph_attempts": 3, "graph_productive": 0,
            "turns": 9, "answer": "I couldn't find that in the graph."}
    spans = [
        {"command": "query graph pattern2", "rows": 0, "exit_code": 0, "truncated": 0, "evidence": {}},
        {"command": "query graph text2cypher", "rows": 0, "exit_code": 1, "truncated": 0, "evidence": {}},
        {"command": "query entity-resolve", "rows": 2, "exit_code": 0, "truncated": 0,
         "evidence": {"resolved": [{"score": 0.033}, {"score": 0.031}]}},
        {"command": "query vector-text", "rows": 3, "exit_code": 0, "truncated": 1, "evidence": {}},
    ]
    s = analysis.signals(turn, spans, turns_p90=5)
    assert s == {"graph_miss": 6, "empty_rows": 1, "truncated": 1, "text2cypher": 1, "deep_fallback": 1,
                 "many_turns": 1, "not_found_answer": 1, "ambiguous_entity": 1, "command_error": 1}


def test_partial_fall_through_scores_one():
    turn = {"fell_through": 1, "answer_basis": "mixed", "graph_attempts": 1, "graph_productive": 1,
            "turns": 1, "answer": "fine"}
    assert analysis.signals(turn, [], turns_p90=None) == {"fell_through": 1}


def test_triage_order_and_exclusions():
    now = time.time()
    _turn("wrong", started_at=now - 10)
    store.add_feedback("wrong", "wrong")
    _turn("partial", started_at=now - 9)
    store.add_feedback("partial", "partial")
    _turn("good-miss", started_at=now - 8, fell_through=1, basis="vector", attempts=1, productive=0)
    store.add_feedback("good-miss", "good")
    _turn("good-plain", started_at=now - 7)
    store.add_feedback("good-plain", "good")
    _turn("unrated-miss", started_at=now - 6, fell_through=1, basis="vector", attempts=1, productive=0)
    _turn("unrated-quiet", started_at=now - 5)
    _turn("reviewed", started_at=now - 4)
    store.add_feedback("reviewed", "wrong")
    store.insert_review_stub("rv", "reviewed")

    ids = [item["trace_id"] for item in analysis.triage()]
    assert ids == ["wrong", "partial", "unrated-miss", "good-miss"]
    first = analysis.triage()[0]
    assert first["rating"] == "wrong" and first["question"] == "q-wrong"


def test_stats_per_domain():
    now = time.time()
    _turn("a", started_at=now, fell_through=1, basis="vector", attempts=2, productive=0, domains=("b",))
    _turn("c", started_at=now, fell_through=0, basis="graph", attempts=1, productive=1, domains=("b", "hr"))
    rows = analysis.stats(by="all")
    b = next(r for r in rows if r["domain"] == "b")
    assert b["turns"] == 2
    assert b["fall_through_rate"] == 0.5
    assert b["graph_miss_rate"] == 0.5
    assert b["graph_productivity"] == pytest.approx(1 / 3)
    assert b["answer_basis"] == {"vector": 1, "graph": 1}
    assert b["mean_graph_attempts_before_fall_through"] == 2
    assert [r["domain"] for r in analysis.stats(domain=["hr"], by="all")] == ["hr"]


def test_show_nests_spans_under_tool_calls_and_warns_on_stale_daemon():
    now = time.time()
    _turn("t", started_at=now)
    store.insert_events("t", [
        {"seq": 0, "ts": 1.0, "type": "tool_call", "tool_id": "tu1", "name": "Bash",
         "payload": json.dumps({"command": "artmind query chunks --domain b"})},
    ])
    shown = analysis.show("t")
    assert shown["events"][0]["spans"] == []
    assert any("serve" in w for w in shown["warnings"])

    _span("t", "query chunks", at=1.5)
    store.set_span_tool_ids([("t-query chunks-1.5", "tu1")])
    shown = analysis.show("t")
    assert shown["events"][0]["spans"][0]["command"] == "query chunks"
    assert shown["warnings"] == []
    assert analysis.show("missing") is None


def test_show_orphaned_span_appears_in_unattributed(tmp_path):
    """A span with a tool_id that matches no tool_call event is unattributed (orphaned)."""
    now = time.time()
    _turn("t2", started_at=now)
    # No tool_call events — just a span with a tool_id of "ghost"
    store.insert_span(
        {"span_id": "t2-ghost", "trace_id": "t2", "command": "query chunks", "args": "{}",
         "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None,
         "truncated": 0, "evidence": "{}", "started_at": now},
        [],
    )
    store.set_span_tool_ids([("t2-ghost", "ghost")])
    shown = analysis.show("t2")
    unattributed_ids = [s["span_id"] for s in shown["unattributed_spans"]]
    assert "t2-ghost" in unattributed_ids


def test_signals_not_found_requires_graph_subject():
    """'does not contain' only fires when the subject is graph/data/documents."""
    # Ordinary factual negative — no flag
    turn_plain = {"fell_through": 0, "answer_basis": "graph", "graph_attempts": 1, "graph_productive": 1,
                  "turns": 1, "answer": "The policy does not contain an exemption."}
    assert "not_found_answer" not in analysis.signals(turn_plain, [], turns_p90=None)

    # Graph subject — must fire
    turn_graph = {"fell_through": 0, "answer_basis": "graph", "graph_attempts": 1, "graph_productive": 1,
                  "turns": 1, "answer": "The graph does not contain that."}
    assert analysis.signals(turn_graph, [], turns_p90=None).get("not_found_answer") == 1

    # Existing test phrase still works
    turn_existing = {"fell_through": 0, "answer_basis": "graph", "graph_attempts": 1, "graph_productive": 1,
                     "turns": 1, "answer": "I couldn't find that in the graph."}
    assert analysis.signals(turn_existing, [], turns_p90=None).get("not_found_answer") == 1


def test_a_dry_run_is_not_an_empty_result():
    from artmind.traces import analysis

    span = {"command": "query graph text2cypher", "args": {"dry_run": True}, "exit_code": 0, "rows": 0}
    sig = analysis.signals({"turns": 1}, [span], None, {"status": "n/a"})
    assert "empty_rows" not in sig


def test_deep_fallback_ignores_a_dry_run():
    from artmind.traces import analysis

    spans = [
        {"command": "query graph pattern1", "args": {"domain": ["b"]}, "exit_code": 0, "rows": 1},
        {"command": "query entity-context", "args": {"domain": ["b"]}, "exit_code": 0, "rows": 1},
        {"command": "query graph text2cypher", "args": {"domain": ["b"], "dry_run": True}, "exit_code": 0, "rows": 0},
    ]
    sig = analysis.signals({"turns": 1}, spans, None, {"status": "n/a"})
    assert "deep_fallback" not in sig
    assert sig.get("text2cypher") == 1
