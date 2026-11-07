"""The recorder writes a run_shadow event beside the plan_shadow one (P3 spec §7.2)."""

import json
import threading

import pytest

from artmind.traces import store
from artmind.traces.recorder import TraceRecorder


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr("artmind.traces.recorder.graph_state", lambda domains: {})


class FakeShadow:
    running = False

    def __init__(self, dry, status="ok"):
        self.dry = dry
        self._status = status

    def result(self, timeout=None):
        return {"status": self._status, "followup": False, "plan": {"route": {"id": "search"}}, "error": None}


def _span(span_id, docs, parent=None):
    return {"span_id": span_id, "trace_id": "t1", "command": "query vector-text", "args": "{}", "exit_code": 0,
            "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
            "evidence": json.dumps({"documents": [{"doc_id": d} for d in docs]}),
            "started_at": 1.0, "parent_span_id": parent}


def _run_events():
    return [e for e in store.list_events("t1") if e["type"] == "run_shadow"]


def test_run_shadow_is_recorded_with_covers_agent():
    store.insert_turn("t1", source="admin", question="q")
    store.insert_span(_span("a1", ["d1", "d2"]), [])
    rec = TraceRecorder("t1", None)
    rec._shadow = FakeShadow({"status": "ok", "documents": ["d1"], "path": [], "timing": {"exec_ms": 5}})
    rec.finish()
    (event,) = _run_events()
    payload = json.loads(event["payload"])
    assert payload["covers_agent"] == 0.5 and payload["documents"] == ["d1"]
    assert [e["seq"] for e in store.list_events("t1")] == [0, 1]


def test_covers_agent_ignores_the_executors_child_spans():
    store.insert_turn("t1", source="admin", question="q")
    store.insert_span(_span("a1", ["d1", "d2"]), [])
    store.insert_span(_span("c1", ["d1"], parent="a1"), [])
    rec = TraceRecorder("t1", None)
    rec._shadow = FakeShadow({"status": "ok", "documents": ["d1"]})
    rec.finish()
    assert json.loads(_run_events()[0]["payload"])["covers_agent"] == 0.5


def test_no_dry_run_writes_no_run_shadow():
    store.insert_turn("t1", source="admin", question="q")
    rec = TraceRecorder("t1", None)
    rec._shadow = FakeShadow(None)
    rec.finish()
    assert _run_events() == []


def test_late_path_writes_run_shadow_after_plan_shadow(monkeypatch):
    from artmind.query_plan import shadow

    store.insert_turn("t1", source="admin", question="q")
    store.insert_span(_span("a1", ["d1"]), [])
    release = threading.Event()

    class Slow(FakeShadow):
        running = True

        def result(self, timeout=None):
            if timeout is None:
                return {"status": "timeout", "followup": False, "plan": None, "error": None}
            release.wait(2)
            return super().result()

    rec = TraceRecorder("t1", None)
    rec._shadow = Slow({"status": "ok", "documents": ["d1", "d9"]})
    rec.finish()
    release.set()
    rec._late_shadow.join(2)
    events = store.list_events("t1")
    assert [(e["seq"], e["type"]) for e in events] == [(0, "plan_shadow"), (1, "run_shadow")]
    assert json.loads(events[1]["payload"])["covers_agent"] == 1.0
