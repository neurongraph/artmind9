"""Shadow plans in traces show/stats/triage (spec 2026-10-22 §6)."""

import json

import pytest

from artmind.traces import analysis, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _shadow(level="high", cmd="artmind query views run visits --domain b --compact", followup=False):
    return {"status": "ok", "followup": followup,
            "plan": {"confidence": {"level": level}, "steps": [{"n": 1, "cmd": cmd}] if level == "high" else [],
                     "suggested_steps": [] if level == "high" else [{"n": 1, "cmd": cmd}]}}


def _span(command, name=None, evidence=None):
    args = {"domain": ["b"], **({"name": name} if name else {})}
    return {"command": command, "args": args, "exit_code": 0, "rows": 1, "evidence": evidence or {}}


def test_shadow_plan_reads_the_event_and_survives_a_truncated_payload():
    assert analysis.shadow_plan([{"type": "plan_shadow", "payload": json.dumps(_shadow())}])["status"] == "ok"
    assert analysis.shadow_plan([{"type": "plan_shadow", "payload": "{\"status\": \"ok\"…[truncated 9 bytes]"}]) == {
        "status": "truncated", "followup": None, "plan": None, "error": None}
    assert analysis.shadow_plan([{"type": "text", "payload": "x"}]) is None


def test_agreement_compares_the_first_step_with_the_agents_spans():
    assert analysis.agreement(_shadow(), [_span("query views run", "visits")]) is True
    assert analysis.agreement(_shadow(), [_span("query views run", "other")]) is False
    assert analysis.agreement(_shadow(cmd="artmind query graph pattern7 --domain b --compact"),
                              [_span("query graph pattern7")]) is True
    assert analysis.agreement({"status": "ok", "plan": {"steps": [], "suggested_steps": []}}, []) is None


def test_agreement_skips_untraced_steps_and_malformed_commands():
    plan = {"status": "ok", "plan": {"steps": [{"n": 1, "cmd": "artmind db bridge --domain b"},
                                               {"n": 2, "cmd": "artmind query text2sql --domain b"}]}}
    assert analysis.agreement(plan, [_span("query text2sql")]) is True
    assert analysis.agreement(plan, [_span("query vector-text")]) is False
    only_db = {"status": "ok", "plan": {"steps": [{"n": 1, "cmd": "artmind db bridge --domain b"}]}}
    assert analysis.agreement(only_db, [_span("query text2sql")]) is None
    assert analysis.agreement(_shadow(cmd="artmind query views run 'visits --domain b"), []) is None


def test_an_executed_plan_span_matches_a_view_run_step():
    executed = _span("query plan", evidence={"view": "visits"})
    assert analysis.agreement(_shadow(), [executed]) is True
    assert analysis.agreement(_shadow(), [_span("query plan", evidence={"view": "other"})]) is False
    assert analysis.agreement(_shadow(), [_span("query plan")]) is False


def test_a_confident_plan_the_agent_ignored_is_a_triage_signal():
    sig = analysis.signals({"turns": 1}, [_span("query vector-text")], None, {"status": "n/a"}, shadow=_shadow())
    assert sig["plan_disagreed_high"] == 2
    # no query ran besides the plan: nothing was ignored
    sig = analysis.signals({"turns": 1}, [_span("query plan")], None, {"status": "n/a"}, shadow=_shadow())
    assert "plan_disagreed_high" not in sig
    sig = analysis.signals({"turns": 1}, [], None, {"status": "n/a"}, shadow=_shadow())
    assert "plan_disagreed_high" not in sig
    sig = analysis.signals({"turns": 1}, [_span("query vector-text")], None, {"status": "n/a"},
                           shadow=_shadow(level="low"))
    assert "plan_disagreed_high" not in sig


def _turn(trace_id, session, payload, agent_command, agent_name=None, rating=None):
    store.insert_turn(trace_id, source="admin", question="q", session_id=session, started_at=1_700_000_000.0)
    store.update_turn(trace_id, status="done", domains=json.dumps(["b"]), completed_at=1_700_000_001.0)
    store.insert_span({"span_id": f"s-{trace_id}", "trace_id": trace_id, "command": agent_command,
                       "args": json.dumps({"domain": ["b"], **({"name": agent_name} if agent_name else {})}),
                       "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
                       "evidence": "{}", "started_at": 1_700_000_000.5}, [])
    store.insert_events(trace_id, [{"seq": 0, "ts": 1.0, "type": "plan_shadow",
                                    "payload": payload if isinstance(payload, str) else json.dumps(payload)}])
    if rating:
        store.add_feedback(trace_id, rating)


def test_stats_report_shadow_agreement_on_high_plans():
    _turn("t1", "s1", _shadow(), "query views run", "visits")
    _turn("t2", "s2", _shadow(), "query vector-text", rating="wrong")
    _turn("t3", "s3", _shadow(followup=True), "query views run", "visits")
    _turn("t4", "s4", _shadow(level="low"), "query vector-text")
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    assert row["plan_shadow"] == {
        "plans": 4, "statuses": {"ok": 4}, "high_share": 0.75,
        "agreement_high": 2 / 3, "agreement_low": 0.0,
        "agreement_first": 0.5, "agreement_followup": 1.0,
        "agreement_by_rating": {"unrated": 1.0, "wrong": 0.0}}


def test_failed_shadows_are_counted_but_leave_the_rates_alone():
    _turn("t1", "s1", _shadow(), "query views run", "visits")
    _turn("t2", "s2", {"status": "error", "followup": False, "plan": None, "error": "boom"}, "query text2sql")
    _turn("t3", "s3", {"status": "timeout", "followup": False, "plan": None}, "query text2sql")
    _turn("t4", "s4", "{\"status\": \"ok\"…[truncated 9 bytes]", "query text2sql")
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    assert row["plan_shadow"]["plans"] == 1
    assert row["plan_shadow"]["statuses"] == {"ok": 1, "error": 1, "timeout": 1, "truncated": 1}
    assert row["plan_shadow"]["agreement_high"] == 1.0


def test_stats_without_shadows_keep_the_same_keys():
    _turn("t1", "s1", {"status": "skipped"}, "query text2sql")
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    assert row["plan_shadow"] == {
        "plans": 0, "statuses": {"skipped": 1}, "high_share": None, "agreement_high": None,
        "agreement_low": None, "agreement_first": None, "agreement_followup": None,
        "agreement_by_rating": {}}


def test_triage_passes_the_shadow_through():
    _turn("t1", "s1", _shadow(), "query vector-text")
    _turn("t2", "s2", _shadow(), "query views run", "visits")
    items = {i["trace_id"]: i for i in analysis.triage()}
    assert items["t1"]["signals"]["plan_disagreed_high"] == 2
    assert "t2" not in items or "plan_disagreed_high" not in items["t2"]["signals"]


def test_show_includes_the_shadow_plan():
    _turn("t1", "s1", _shadow(), "query views run", "visits")
    assert analysis.show("t1")["plan_shadow"]["status"] == "ok"
