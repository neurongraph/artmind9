"""Shadow mode (spec 2026-10-22 §6): a plan beside each traced turn, never seen by the agent."""

import json
import time

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
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: time.sleep(1.0) or {})
    monkeypatch.setattr(shadow, "WAIT_S", 0.05)
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
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
