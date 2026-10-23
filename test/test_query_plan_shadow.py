"""Shadow mode (spec 2026-10-22 §6): a plan beside each traced turn, never seen by the agent."""

import json
import threading
from types import SimpleNamespace

import pytest

from artmind.query_plan import planner, shadow
from artmind.traces import store
from artmind.traces.recorder import TraceRecorder


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr("artmind.traces.recorder.graph_state", lambda domains: {})
    monkeypatch.delenv("ARTMIND_PLAN_SHADOW", raising=False)


def _shadow_events(trace_id):
    return [e for e in store.list_events(trace_id) if e["type"] == "plan_shadow"]


def test_a_finished_turn_carries_its_shadow_plan(monkeypatch):
    seen = {}

    def fake_plan(question, domains=None):
        seen.update(question=question, domains=domains)
        return {"confidence": {"level": "high"}, "steps": [{"n": 1, "cmd": "artmind query views run v --compact"}]}

    monkeypatch.setattr(planner, "plan", fake_plan)
    rec = TraceRecorder.start(source="admin", question="Who leads X?", session_id="s1",
                              shadow_question="Who leads X?", shadow_domains=["b"])
    rec.finish()
    (event,) = _shadow_events(rec.trace_id)
    payload = json.loads(event["payload"])
    assert payload["status"] == "ok" and payload["followup"] is False
    assert payload["plan"]["confidence"]["level"] == "high"
    assert seen == {"question": "Who leads X?", "domains": ["b"]}


def test_a_second_turn_in_a_session_is_a_followup(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
    TraceRecorder.start(source="admin", question="first", session_id="s1").finish()
    rec = TraceRecorder.start(source="admin", question="yes", session_id="s1", shadow_question="yes")
    rec.finish()
    assert json.loads(_shadow_events(rec.trace_id)[0]["payload"])["followup"] is True


def test_a_failing_plan_is_recorded_as_an_error(monkeypatch):
    def boom(question, domains=None):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(planner, "plan", boom)
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
    payload = json.loads(_shadow_events(rec.trace_id)[0]["payload"])
    assert payload["status"] == "error" and "neo4j down" in payload["error"]


def test_a_slow_plan_times_out(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: release.wait(5) and {})
    monkeypatch.setattr(shadow, "WAIT_S", 0.05)
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    try:
        rec.finish()
    finally:
        release.set()
        rec._shadow._thread.join(2)
    assert json.loads(_shadow_events(rec.trace_id)[0]["payload"])["status"] == "timeout"


def test_the_kill_switch(monkeypatch):
    monkeypatch.setenv("ARTMIND_PLAN_SHADOW", "0")
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
    assert _shadow_events(rec.trace_id) == []


def test_no_shadow_question_means_no_shadow(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
    rec = TraceRecorder.start(source="admin", question="q")
    rec.finish()
    assert _shadow_events(rec.trace_id) == []


def test_list_events_of_type_batches_by_trace():
    a = TraceRecorder.start(source="admin", question="a")
    store.insert_events(a.trace_id, [{"seq": 0, "ts": 1.0, "type": "plan_shadow", "payload": "{}"}])
    b = TraceRecorder.start(source="admin", question="b")
    store.insert_events(b.trace_id, [{"seq": 0, "ts": 1.0, "type": "text", "payload": "x"}])
    found = store.list_events_of_type([a.trace_id, b.trace_id], "plan_shadow")
    assert list(found) == [a.trace_id] and found[a.trace_id][0]["payload"] == "{}"


class _Exploding:
    followup = False

    def result(self, timeout=None):
        raise RuntimeError("boom")


def test_a_shadow_that_raises_never_costs_the_trace(monkeypatch):
    rec = TraceRecorder.start(source="admin", question="q")
    rec.observe({"type": "text_delta", "text": "answer"})
    rec.observe({"type": "block_done", "block": "text"})
    rec._shadow = _Exploding()
    rec.finish()
    assert _shadow_events(rec.trace_id) == []
    assert [e["type"] for e in store.list_events(rec.trace_id)] == ["text"]
    assert store.get_turn(rec.trace_id)["status"] == "done"


def test_the_wait_is_counted_from_the_plan_start(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: release.wait(5) and {})
    monkeypatch.setattr(shadow, "WAIT_S", 10.0)
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    plan = rec._shadow
    joins = []
    real_join = plan._thread.join
    plan._thread.join = lambda timeout=None: (joins.append(timeout), real_join(0))[1]
    # the turn itself took longer than the whole budget
    monkeypatch.setattr(shadow, "time", SimpleNamespace(monotonic=lambda: plan._started + shadow.WAIT_S + 1))
    try:
        rec.finish()
    finally:
        release.set()
        real_join(2)
    assert joins == [0.0]
    assert json.loads(_shadow_events(rec.trace_id)[0]["payload"])["status"] == "timeout"


def test_too_many_running_plans_are_skipped(monkeypatch):
    release = threading.Event()
    blockers = [threading.Thread(target=release.wait, name="plan-shadow", daemon=True) for _ in range(shadow.MAX_RUNNING)]
    for t in blockers:
        t.start()
    try:
        monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
        rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
        rec.finish()
        payload = json.loads(_shadow_events(rec.trace_id)[0]["payload"])
        assert payload["status"] == "skipped" and payload["plan"] is None
        assert payload["error"] == "too many shadow plans running"
    finally:
        release.set()
        for t in blockers:
            t.join(1)


def test_an_oversized_plan_is_trimmed_to_valid_json(monkeypatch):
    big = {
        "confidence": {"level": "low"},
        "entities": [{"mention": f"m{i}", "candidates": [{"name": f"n{j}", "pad": "x" * 2000} for j in range(10)]}
                     for i in range(6)],
        "warnings": [f"warning {i} " + "y" * 500 for i in range(40)],
    }
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: big)
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
    payload = json.loads(_shadow_events(rec.trace_id)[0]["payload"])  # valid JSON, not truncated
    plan = payload["plan"]
    assert plan["trimmed"] is True and len(plan["warnings"]) == 10
    assert all(len(e["candidates"]) == 2 for e in plan["entities"])
    assert plan["confidence"] == {"level": "low"}


def test_a_small_plan_is_not_trimmed(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {"warnings": ["a"] * 20})
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
    plan = json.loads(_shadow_events(rec.trace_id)[0]["payload"])["plan"]
    assert len(plan["warnings"]) == 20 and "trimmed" not in plan


def test_a_thread_that_cannot_start_means_no_shadow(monkeypatch):
    class NoThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(shadow, "threading", SimpleNamespace(Thread=NoThread, enumerate=threading.enumerate))
    assert shadow.start("q") is None


def test_a_session_count_failure_means_not_a_followup(monkeypatch):
    def boom(session_id):
        raise RuntimeError("db locked")

    monkeypatch.setattr(store, "count_session_turns", boom)
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
    rec = TraceRecorder.start(source="admin", question="q", session_id="s1", shadow_question="q")
    rec.finish()
    assert json.loads(_shadow_events(rec.trace_id)[0]["payload"])["followup"] is False


def test_a_disabled_recorder_starts_no_shadow(monkeypatch):
    started = []

    def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "insert_turn", boom)
    monkeypatch.setattr(shadow, "start", lambda *a, **kw: started.append(a))
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    assert rec.enabled is False and rec._shadow is None and started == []


@pytest.mark.parametrize("value", ["false", "off", "no", "0", "FALSE"])
def test_kill_switch_values(monkeypatch, value):
    monkeypatch.setenv("ARTMIND_PLAN_SHADOW", value)
    assert shadow.enabled() is False
    assert shadow.start("q") is None


def test_the_shadow_event_never_reaches_the_ui(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    seen = [rec.observe({"type": "text_delta", "text": "hi"}),
            rec.observe({"type": "turn_done", "turns": 1, "_trace": {"usage": {}}})]
    rec.finish()
    assert all("plan" not in json.dumps(e) for e in seen)
    assert [e["type"] for e in seen] == ["text_delta", "turn_done"]


def test_a_plan_still_oversized_after_trimming_falls_back_to_a_minimum(monkeypatch):
    big = {
        "confidence": {"level": "high"},
        "route": {"id": "view"},
        "steps": [{"n": i, "cmd": "artmind query views run v " + "z" * 500} for i in range(400)],
        "warnings": [f"w{i}" for i in range(20)],
    }
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: big)
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
    plan = json.loads(_shadow_events(rec.trace_id)[0]["payload"])["plan"]
    assert plan == {"confidence": {"level": "high"}, "route": {"id": "view"}, "steps": [big["steps"][0]],
                    "warnings": ["w0", "w1", "w2"], "trimmed": True}
