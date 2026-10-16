"""Conversation recording, replay and routes through the FastAPI app (spec 2026-10-16 §4)."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from artmind.conversations import store
from artmind.webui.app import ChatRequest, create_app
from artmind.webui.sessions import SessionRegistry


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


class FakeBackend:
    instances: list["FakeBackend"] = []

    def __init__(self, backend: str = "claude-sdk"):
        self.backend = backend
        self.prompts: list[str] = []
        FakeBackend.instances.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def interrupt(self):
        pass

    async def receive_events(self):
        yield {"type": "text_delta", "text": f"Answer {len(self.prompts)}"}
        yield {"type": "block_done", "block": "text"}
        yield {"type": "turn_done", "turns": 1, "duration_s": 1.0, "cost": 0.01}


def _app(**kwargs):
    FakeBackend.instances = []
    registry = SessionRegistry(client_factory=FakeBackend)
    return create_app(registry=registry, **kwargs), registry


def _sse(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def _chat(client, cid, prompt, backend="claude-sdk"):
    return client.post("/api/chat", json={"conversation_id": cid, "prompt": prompt, "backend": backend})


def test_chat_records_the_conversation_and_turn():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "Who runs Atlas?")

    listing = client.get("/api/conversations").json()
    assert [c["conversation_id"] for c in listing["conversations"]] == ["c1"]
    assert listing["conversations"][0]["title"] == "Who runs Atlas?"
    assert listing["next_cursor"] is None

    detail = client.get("/api/conversations/c1").json()
    turn = detail["turns"][0]
    assert turn["user_text"] == "Who runs Atlas?"
    assert turn["blocks"] == [{"type": "text", "md": "Answer 1"}]
    assert (turn["answer_text"], turn["status"], turn["backend"], turn["cost_usd"]) == (
        "Answer 1", "done", "claude-sdk", 0.01)


def test_a_live_client_gets_the_bare_prompt_and_no_replay():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "one")
    events = _sse(_chat(client, "c1", "two").text)
    assert FakeBackend.instances[0].prompts == ["one", "two"]
    assert "context_restored" not in [e["type"] for e in events]


def test_a_fresh_client_with_history_gets_the_transcript_prepended():
    app, _ = _app()
    _chat(TestClient(app), "c1", "first question")

    # A new app == a new process / swept session: same DB, no live agent.
    app2, _ = _app()
    events = _sse(_chat(TestClient(app2), "c1", "second question").text)

    assert events[0] == {"type": "context_restored", "turns": 1, "omitted": 0}
    prompt = FakeBackend.instances[0].prompts[0]
    assert prompt.startswith("[Restored conversation.")
    assert "User: first question\nAssistant: Answer 1" in prompt
    assert prompt.endswith("\n\nsecond question")
    # what is stored is the user's text, never the augmented prompt
    turns = store.list_turns("c1")
    assert [t["user_text"] for t in turns] == ["first question", "second question"]


def test_a_first_turn_has_no_replay():
    app, _ = _app()
    events = _sse(_chat(TestClient(app), "c1", "hello").text)
    assert FakeBackend.instances[0].prompts == ["hello"]
    assert "context_restored" not in [e["type"] for e in events]


def test_switching_backend_replays_into_the_new_backend_and_records_it():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "q1", backend="claude-sdk")
    events = _sse(_chat(client, "c1", "q2", backend="acp").text)

    assert events[0]["type"] == "context_restored"
    assert [b.backend for b in FakeBackend.instances] == ["claude-sdk", "acp"]
    assert "User: q1\nAssistant: Answer 1" in FakeBackend.instances[1].prompts[0]
    assert [t["backend"] for t in store.list_turns("c1")] == ["claude-sdk", "acp"]


def test_interrupted_and_failed_turns_are_not_replayed():
    store.ensure("c1", "qa", "q0")
    seq = store.begin_turn("c1", "q0", "claude-sdk")
    store.finish_turn("c1", seq, blocks=[], answer_text="partial", status="interrupted")
    app, _ = _app()
    events = _sse(_chat(TestClient(app), "c1", "q1").text)
    assert "context_restored" not in [e["type"] for e in events]
    assert FakeBackend.instances[0].prompts == ["q1"]


def test_invalid_conversation_ids_are_rejected():
    app, _ = _app()
    client = TestClient(app)
    for bad in ["", "has space", "../etc", "x" * 65]:
        assert _chat(client, bad, "hi").status_code == 422


def test_a_conversation_owned_by_the_other_profile_is_404_for_chat():
    app, _ = _app()
    _chat(TestClient(app), "c1", "hi")
    admin_app, _ = _app(admin_routes=True)
    assert _chat(TestClient(admin_app), "c1", "hi").status_code == 404


class BlockingBackend(FakeBackend):
    gate: asyncio.Event

    async def receive_events(self):
        yield {"type": "text_delta", "text": "a"}
        await BlockingBackend.gate.wait()
        yield {"type": "turn_done", "turns": 1, "duration_s": 0.1, "cost": None}


async def test_a_second_turn_on_a_busy_conversation_is_409_then_allowed_again():
    BlockingBackend.gate = asyncio.Event()
    registry = SessionRegistry(client_factory=BlockingBackend)
    app = create_app(registry=registry)
    endpoint = [r for r in app.routes if getattr(r, "path", "") == "/api/chat"][0].endpoint

    first = await endpoint(ChatRequest(conversation_id="c1", prompt="one"))
    iterator = first.body_iterator
    await iterator.__anext__()  # the turn is now in flight

    with pytest.raises(HTTPException) as busy:
        await endpoint(ChatRequest(conversation_id="c1", prompt="two"))
    assert busy.value.status_code == 409

    BlockingBackend.gate.set()
    async for _ in iterator:
        pass
    again = await endpoint(ChatRequest(conversation_id="c1", prompt="three"))
    assert again.status_code == 200
    async for _ in again.body_iterator:
        pass


def test_a_failed_connect_still_records_the_turn_as_an_error():
    class BrokenBackend(FakeBackend):
        async def connect(self):
            raise RuntimeError("cannot start agent")

    registry = SessionRegistry(client_factory=BrokenBackend)
    client = TestClient(create_app(registry=registry))
    events = _sse(_chat(client, "c1", "hi").text)
    assert events[-1] == {"type": "error", "message": "cannot start agent"}
    turn = store.list_turns("c1")[0]
    assert (turn["status"], turn["error_message"]) == ("error", "cannot start agent")


def test_admin_turn_links_to_its_trace(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")

    class TracedBackend(FakeBackend):
        trace_context_path = ctx

    registry = SessionRegistry(client_factory=TracedBackend)
    client = TestClient(create_app(registry=registry, admin_routes=True, trace_turns=True))
    events = _sse(_chat(client, "c1", "hi").text)
    assert events[0]["type"] == "trace"  # first turn: no replay event ahead of it

    turn = store.list_turns("c1")[0]
    assert turn["trace_id"] == events[0]["trace_id"]
    assert store.get("c1")["profile"] == "admin"


def test_chat_ui_turns_have_no_trace_id():
    app, _ = _app()
    _chat(TestClient(app), "c1", "hi")
    assert store.list_turns("c1")[0]["trace_id"] is None
