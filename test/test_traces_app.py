"""Admin console capture through /api/chat, and the feedback endpoint."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from artmind.traces import store
from artmind.webui.app import ChatRequest, create_app
from artmind.webui.sessions import SessionRegistry


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


class TracedFakeBackend:
    instances: list["TracedFakeBackend"] = []

    def __init__(self, backend="claude-sdk", context=None):
        self.trace_context_path = context
        self.seen_trace_id = None
        TracedFakeBackend.instances.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, prompt):
        self.seen_trace_id = self.trace_context_path.read_text() if self.trace_context_path else None

    async def interrupt(self):
        pass

    async def receive_events(self):
        yield {"type": "tool_call", "id": "tu1", "name": "Bash", "input": "clip",
               "_trace": {"input": {"command": "artmind query chunks"}, "parent_tool_id": None}}
        yield {"type": "tool_result", "tool_id": "tu1", "content": "clip", "_trace": {"content": "FULL"}}
        yield {"type": "text_delta", "text": "Hi"}
        yield {"type": "turn_done", "turns": 1, "duration_s": 1.0, "cost": 0.01, "_trace": {"usage": {}, "model": "m"}}


def _sse(body):
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def _admin_client(tmp_path):
    TracedFakeBackend.instances = []
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")
    registry = SessionRegistry(client_factory=lambda name: TracedFakeBackend(name, ctx))
    return TestClient(create_app(registry=registry, admin_routes=True, trace_turns=True)), ctx


def test_admin_turn_is_traced_and_sse_is_clean(tmp_path):
    client, ctx = _admin_client(tmp_path)
    resp = client.post("/api/chat", json={"conversation_id": "s1", "prompt": "Who?"})
    events = _sse(resp.text)
    assert events[0]["type"] == "trace"
    trace_id = events[0]["trace_id"]
    assert all("_trace" not in e for e in events)
    assert TracedFakeBackend.instances[0].seen_trace_id == trace_id  # written before query()
    assert ctx.read_text() == ""  # cleared after the turn

    turn = store.get_turn(trace_id)
    assert (turn["source"], turn["question"], turn["session_id"], turn["status"]) == ("admin", "Who?", "s1", "done")
    payloads = [e["payload"] for e in store.list_events(trace_id)]
    assert "FULL" in payloads


def test_chat_ui_is_not_traced(tmp_path):
    TracedFakeBackend.instances = []
    registry = SessionRegistry(client_factory=lambda name: TracedFakeBackend(name, None))
    client = TestClient(create_app(registry=registry))
    events = _sse(client.post("/api/chat", json={"conversation_id": "s1", "prompt": "Who?"}).text)
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "text_delta", "turn_done"]
    assert all("_trace" not in e for e in events)
    assert store.list_turns() == []


def test_feedback_endpoint(tmp_path):
    client, _ = _admin_client(tmp_path)
    trace_id = _sse(client.post("/api/chat", json={"conversation_id": "s1", "prompt": "Who?"}).text)[0]["trace_id"]
    ok = client.post(f"/api/traces/{trace_id}/feedback", json={"rating": "wrong", "expected": "Bob"})
    assert ok.status_code == 200
    assert store.latest_feedback(trace_id)["expected"] == "Bob"
    assert client.post("/api/traces/nope/feedback", json={"rating": "good"}).status_code == 404
    assert client.post(f"/api/traces/{trace_id}/feedback", json={"rating": "meh"}).status_code == 422


def test_feedback_endpoint_absent_without_tracing():
    client = TestClient(create_app())
    assert client.post("/api/traces/x/feedback", json={"rating": "good"}).status_code == 404


def test_benchmark_question_is_traced(tmp_path, monkeypatch):
    import asyncio

    from artmind import benchmark
    from artmind.webui import backends as backends_mod

    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    created = []

    def fake_create_backend(name, profile, *, trace_context=None, **kw):
        backend = TracedFakeBackend(name, trace_context)
        created.append(backend)
        return backend

    monkeypatch.setattr(backends_mod, "create_backend", fake_create_backend)
    run_id = benchmark._create_benchmark_run("q.md", [{"seq": 1, "qid": "Q1", "title": "t", "question": "Who?",
                                                        "eval_comment": None}])
    asyncio.run(benchmark._run_benchmark(run_id))

    question = benchmark._get_benchmark_question(run_id, 1)
    assert question["status"] == "completed"
    trace_id = question["trace_id"]
    assert created[0].seen_trace_id == trace_id
    turn = store.get_turn(trace_id)
    assert (turn["source"], turn["benchmark_run_id"], turn["benchmark_seq"]) == ("benchmark", run_id, 1)
    assert benchmark._export_benchmark_json(run_id) is not None  # export still works


def test_cancelled_turn_recorded_as_error(tmp_path):
    """A client disconnect (CancelledError) must be recorded with status='error'."""

    class CancellingBackend(TracedFakeBackend):
        async def receive_events(self):
            yield {"type": "tool_call", "id": "tu1", "name": "Bash", "input": "x",
                   "_trace": {"input": {"command": "artmind query chunks"}, "parent_tool_id": None}}
            raise asyncio.CancelledError("simulated client disconnect")

    TracedFakeBackend.instances = []
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")
    registry = SessionRegistry(client_factory=lambda name: CancellingBackend(name, ctx))
    app = create_app(registry=registry, admin_routes=True, trace_turns=True)
    endpoint = [r for r in app.routes if getattr(r, "path", "") == "/api/chat"][0].endpoint

    async def run():
        resp = await endpoint(ChatRequest(conversation_id="s1", prompt="Who?"))
        trace_id = None
        try:
            async for chunk in resp.body_iterator:
                text = chunk if isinstance(chunk, str) else chunk.decode()
                for line in text.splitlines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if event.get("type") == "trace":
                            trace_id = event["trace_id"]
        except (asyncio.CancelledError, Exception):
            pass
        return trace_id

    trace_id = asyncio.run(run())
    assert trace_id is not None
    turn = store.get_turn(trace_id)
    assert turn["status"] == "error"
    assert (turn.get("error_message") or "").startswith("interrupted")


def _spy_recorder_start(monkeypatch):
    from artmind.traces.recorder import TraceRecorder

    calls = []
    original = TraceRecorder.start

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(TraceRecorder, "start", staticmethod(spy))
    return calls


def test_admin_chat_passes_the_users_own_text_as_the_shadow_question(tmp_path, monkeypatch):
    calls = _spy_recorder_start(monkeypatch)
    client, _ = _admin_client(tmp_path)
    client.post("/api/chat", json={"conversation_id": "s1", "prompt": "Who?"})
    assert calls[0]["shadow_question"] == "Who?"


def test_benchmark_passes_the_raw_question_and_domain_hint_as_shadow(tmp_path, monkeypatch):
    from artmind import benchmark
    from artmind.webui import backends as backends_mod

    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(backends_mod, "create_backend",
                        lambda name, profile, *, trace_context=None, **kw: TracedFakeBackend(name, trace_context))
    calls = _spy_recorder_start(monkeypatch)
    run_id = benchmark._create_benchmark_run(
        "q.md", [{"seq": 1, "qid": "Q1", "title": "t", "question": "Who?", "eval_comment": None}],
        domain_hint="dom")
    asyncio.run(benchmark._run_benchmark(run_id))
    assert calls[0]["shadow_question"] == "Who?"  # not the "Domain: dom" prefixed prompt
    assert calls[0]["shadow_domains"] == ["dom"]
    assert calls[0]["question"] == "Domain: dom\n\nWho?"
