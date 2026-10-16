"""Conversation recording, replay and routes through the FastAPI app (spec 2026-10-16 §4)."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from artmind.conversations import store
from artmind.traces import store as trace_store
from artmind.webui.app import ChatRequest, _ClosingStreamingResponse, create_app
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
    seq = store.begin_turn("c1", "q0b", "claude-sdk")
    store.finish_turn("c1", seq, blocks=[], answer_text="half", status="error", error_message="boom")
    app, _ = _app()
    events = _sse(_chat(TestClient(app), "c1", "q1").text)
    assert "context_restored" not in [e["type"] for e in events]
    assert FakeBackend.instances[0].prompts == ["q1"]


def test_invalid_conversation_ids_are_rejected():
    app, _ = _app()
    client = TestClient(app)
    for bad in ["", "has space", "../etc", "x" * 65, "abc\n", "x" * 64 + "\n"]:
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


def _asgi_scope():
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/chat", "raw_path": b"/api/chat",
            "root_path": "", "query_string": b"", "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80)}


async def test_a_client_disconnect_mid_turn_leaves_an_interrupted_turn_and_no_lock(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")

    class HangingBackend(FakeBackend):
        trace_context_path = ctx

        async def receive_events(self):
            yield {"type": "text_delta", "text": "partial"}
            await asyncio.sleep(3600)  # the agent is still working when the client leaves

    registry = SessionRegistry(client_factory=HangingBackend)
    app = create_app(registry=registry, admin_routes=True, trace_turns=True)
    body = json.dumps({"conversation_id": "c1", "prompt": "hi"}).encode()
    got_chunk = asyncio.Event()
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await got_chunk.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and b"partial" in message.get("body", b""):
            got_chunk.set()

    await app(_asgi_scope(), receive, send)

    turn = store.list_turns("c1")[0]
    assert turn["status"] == "interrupted"
    assert turn["blocks"] == [{"type": "text", "md": "partial"}]
    assert turn["trace_id"] is not None
    assert trace_store.get_turn(turn["trace_id"])["status"] == "error"

    # the conversation is not locked: a follow-up is accepted, not 409
    endpoint = [r for r in app.routes if getattr(r, "path", "") == "/api/chat"][0].endpoint
    again = await endpoint(ChatRequest(conversation_id="c1", prompt="again"))
    assert again.status_code == 200
    await again.body_iterator.aclose()


async def test_a_response_that_is_never_started_does_not_lock_the_conversation():
    app, _ = _app()
    endpoint = [r for r in app.routes if getattr(r, "path", "") == "/api/chat"][0].endpoint

    abandoned = await endpoint(ChatRequest(conversation_id="c1", prompt="one"))
    assert isinstance(abandoned, _ClosingStreamingResponse)
    with pytest.raises(HTTPException) as busy:  # claimed as soon as it is returned
        await endpoint(ChatRequest(conversation_id="c1", prompt="two"))
    assert busy.value.status_code == 409

    # the server runs the response and the client is already gone
    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        raise OSError("client went away")

    with pytest.raises(OSError):
        await abandoned(_asgi_scope(), receive, send)

    again = await endpoint(ChatRequest(conversation_id="c1", prompt="three"))
    assert again.status_code == 200
    async for _ in again.body_iterator:
        pass


def test_a_failed_history_read_does_not_break_a_fresh_turn_and_is_retried_next_turn(monkeypatch):
    app, _ = _app()
    _chat(TestClient(app), "c1", "first question")

    real = store.list_turns
    calls = []

    def flaky(cid, **kwargs):
        calls.append(cid)
        if len(calls) == 1:
            raise RuntimeError("db unavailable")
        return real(cid, **kwargs)

    monkeypatch.setattr(store, "list_turns", flaky)
    app2, registry2 = _app()
    client = TestClient(app2)
    events = _sse(_chat(client, "c1", "second question").text)
    assert "error" not in [e["type"] for e in events]
    assert FakeBackend.instances[0].prompts == ["second question"]
    assert registry2.peek("c1") is None  # dropped, so the next turn is fresh and retries the replay

    events = _sse(_chat(client, "c1", "third question").text)
    assert events[0] == {"type": "context_restored", "turns": 2, "omitted": 0}
    assert "User: first question\nAssistant: Answer 1" in FakeBackend.instances[1].prompts[0]


def test_an_undelivered_replay_is_replayed_again_on_the_next_turn():
    app, _ = _app()
    _chat(TestClient(app), "c1", "first question")

    class FailsOnce(FakeBackend):
        async def query(self, prompt):
            await super().query(prompt)
            if len(FakeBackend.instances) == 1:
                raise RuntimeError("agent refused the prompt")

    FakeBackend.instances = []
    registry = SessionRegistry(client_factory=FailsOnce)
    app2 = create_app(registry=registry)
    client = TestClient(app2)
    failed = _sse(_chat(client, "c1", "second question").text)
    assert failed[0]["type"] == "context_restored"
    assert failed[-1]["type"] == "error"
    assert registry.peek("c1") is None  # the agent that missed the replay was dropped

    retried = _sse(_chat(client, "c1", "second question").text)
    assert retried[0]["type"] == "context_restored"
    assert retried[0]["turns"] == 1  # the failed turn is not replayed, the good one is


class QueueAgent(FakeBackend):
    """Like the Claude SDK: one message stream per agent, and the agent keeps
    working on a prompt whether or not anyone is reading its events."""

    def __init__(self, backend: str = "claude-sdk"):
        super().__init__(backend)
        self.queue: asyncio.Queue = asyncio.Queue()

    work_s = 0.1  # how long the agent works between its two chunks

    async def query(self, prompt):
        self.prompts.append(prompt)

        tag = prompt.rsplit("\n\n", 1)[-1]  # the user's message, without any replayed transcript

        async def work():
            await self.queue.put({"type": "text_delta", "text": f"[{tag}] part1 "})
            await asyncio.sleep(self.work_s)
            await self.queue.put({"type": "text_delta", "text": f"[{tag}] part2"})
            await self.queue.put({"type": "turn_done", "turns": 1, "duration_s": 1.0, "cost": 0.0})

        asyncio.get_running_loop().create_task(work())

    async def receive_events(self):
        while True:
            event = await self.queue.get()
            yield event
            if event["type"] == "turn_done":
                return


async def _asgi_chat(app, cid, prompt, *, disconnect_after_first=False):
    body = json.dumps({"conversation_id": cid, "prompt": prompt}).encode()
    first = asyncio.Event()
    sent = False
    out: list[str] = []
    status: list[int] = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        if disconnect_after_first:
            await first.wait()
            return {"type": "http.disconnect"}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])
        if message["type"] == "http.response.body" and message.get("body"):
            out.append(message["body"].decode())
            first.set()

    await app(_asgi_scope(), receive, send)
    return status[0], _sse("".join(out))


async def test_a_disconnected_turn_never_bleeds_into_the_next_one():
    registry = SessionRegistry(client_factory=QueueAgent)
    app = create_app(registry=registry)

    _, events = await _asgi_chat(app, "c1", "one")  # a finished turn, so there is history to replay
    assert events[-1]["type"] == "turn_done"

    # the client leaves after the first chunk; the agent is still working
    status, _ = await _asgi_chat(app, "c1", "two", disconnect_after_first=True)
    assert status == 200
    assert [t["status"] for t in store.list_turns("c1")] == ["done", "interrupted"]
    assert registry.peek("c1") is None  # the agent mid-way through "two" was dropped

    status, events = await _asgi_chat(app, "c1", "three")
    assert status == 200
    assert events[0] == {"type": "context_restored", "turns": 1, "omitted": 0}
    answer = "".join(e["text"] for e in events if e["type"] == "text_delta")
    # only this turn's own output: one part1, one part2, none of the abandoned turn's
    assert answer.count("part1") == 1 and answer.count("part2") == 1
    assert "[two]" not in answer and "[one]" not in answer
    turns = store.list_turns("c1")
    assert [(t["user_text"], t["status"]) for t in turns] == [("one", "done"), ("two", "interrupted"), ("three", "done")]
    assert turns[2]["answer_text"] == answer


async def test_a_disconnect_on_the_first_turn_gives_a_clean_bare_next_turn():
    registry = SessionRegistry(client_factory=QueueAgent)
    app = create_app(registry=registry)
    await _asgi_chat(app, "c1", "one", disconnect_after_first=True)
    assert registry.peek("c1") is None

    _, events = await _asgi_chat(app, "c1", "two")
    assert "context_restored" not in [e["type"] for e in events]  # nothing done to replay
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "[two] part1 [two] part2"
    assert store.list_turns("c1")[1]["answer_text"] == "[two] part1 [two] part2"


def test_a_stream_that_raises_midway_drops_the_agent():
    class DiesMidway(FakeBackend):
        async def receive_events(self):
            yield {"type": "text_delta", "text": "partial"}
            raise RuntimeError("agent crashed")

    registry = SessionRegistry(client_factory=DiesMidway)
    client = TestClient(create_app(registry=registry))
    events = _sse(_chat(client, "c1", "hi").text)
    assert events[-1] == {"type": "error", "message": "agent crashed"}
    assert registry.peek("c1") is None
    assert store.list_turns("c1")[0]["status"] == "error"


async def test_the_next_turn_cannot_start_before_the_previous_one_has_finished_recording(tmp_path, monkeypatch):
    """The trace-context file is shared by a conversation's turns: a late
    `recorder.finish` of turn one must not clear the id turn two just wrote."""
    import time

    from artmind.traces import recorder as trace_recorder

    ctx = tmp_path / "ctx.id"
    ctx.write_text("")
    seen: list[tuple[str, str]] = []

    class CtxAgent(QueueAgent):
        trace_context_path = ctx
        work_s = 0.6

        async def query(self, prompt):
            await super().query(prompt)

            async def look():
                # turn two looks after any late `finish` of turn one would have run
                await asyncio.sleep(0.35 if prompt == "two" else 0.02)
                seen.append((prompt, ctx.read_text()))  # what a tool call would tag its span with

            asyncio.get_running_loop().create_task(look())

    original = trace_recorder.TraceRecorder.finish

    def slow_finish(self, error=None):
        time.sleep(0.15)
        original(self, error=error)

    monkeypatch.setattr(trace_recorder.TraceRecorder, "finish", slow_finish)
    app = create_app(registry=SessionRegistry(client_factory=CtxAgent), admin_routes=True, trace_turns=True)

    first = asyncio.create_task(_asgi_chat(app, "c1", "one"))
    while True:  # POST "two" until the app accepts it (409 while "one" is still recording)
        await asyncio.sleep(0.02)
        status, _ = await _asgi_chat(app, "c1", "two")
        if status == 200:
            break
    await first

    ids = {t["user_text"]: t["trace_id"] for t in store.list_turns("c1")}
    assert dict(seen) == ids


def test_get_conversation_404s_for_unknown_and_other_profile():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    assert client.get("/api/conversations/nope").status_code == 404
    admin_app, _ = _app(admin_routes=True)
    admin_client = TestClient(admin_app)
    assert admin_client.get("/api/conversations/c1").status_code == 404
    assert admin_client.get("/api/conversations").json()["conversations"] == []


def test_list_paginates_with_a_cursor():
    for i, cid in enumerate(["a", "b", "c"]):
        store.ensure(cid, "qa", f"title {cid}", now=100.0 + i)
    app, _ = _app()
    client = TestClient(app)
    page1 = client.get("/api/conversations?limit=2").json()
    assert [c["conversation_id"] for c in page1["conversations"]] == ["c", "b"]
    assert page1["next_cursor"]
    page2 = client.get(f"/api/conversations?limit=2&cursor={page1['next_cursor']}").json()
    assert [c["conversation_id"] for c in page2["conversations"]] == ["a"]
    assert page2["next_cursor"] is None
    assert client.get("/api/conversations?cursor=garbage").status_code == 400


def test_patch_renames_pins_and_archives():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    body = client.patch("/api/conversations/c1", json={"title": "My thread", "pinned": True}).json()
    assert (body["title"], body["title_source"], body["pinned"]) == ("My thread", "user", 1)
    client.patch("/api/conversations/c1", json={"archived": True})
    assert client.get("/api/conversations").json()["conversations"] == []
    archived = client.get("/api/conversations?archived=true").json()["conversations"]
    assert [c["conversation_id"] for c in archived] == ["c1"]
    client.patch("/api/conversations/c1", json={"archived": False})
    assert len(client.get("/api/conversations").json()["conversations"]) == 1


def test_patch_validates_the_title_and_404s():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    assert client.patch("/api/conversations/c1", json={"title": "   "}).status_code == 422
    assert client.patch("/api/conversations/c1", json={"title": "x" * 201}).status_code == 422
    assert client.patch("/api/conversations/nope", json={"title": "x"}).status_code == 404


def test_delete_drops_the_live_agent_then_the_rows():
    app, registry = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    assert registry.peek("c1") is not None
    assert client.delete("/api/conversations/c1").json() == {"ok": True}
    assert registry.peek("c1") is None
    assert client.get("/api/conversations/c1").status_code == 404
    assert store.list_turns("c1") == []
    assert client.delete("/api/conversations/c1").status_code == 404


def _endpoint(app, path, method):
    return [r for r in app.routes if getattr(r, "path", "") == path and method in getattr(r, "methods", ())][0].endpoint


async def test_delete_while_a_chat_turn_is_in_flight_is_409_and_keeps_the_rows():
    BlockingBackend.gate = asyncio.Event()
    app = create_app(registry=SessionRegistry(client_factory=BlockingBackend))
    chat = _endpoint(app, "/api/chat", "POST")
    delete = _endpoint(app, "/api/conversations/{conversation_id}", "DELETE")

    first = await chat(ChatRequest(conversation_id="c1", prompt="one"))
    await first.body_iterator.__anext__()  # the turn is now in flight
    with pytest.raises(HTTPException) as busy:
        await delete("c1")
    assert busy.value.status_code == 409
    assert store.get("c1") is not None and len(store.list_turns("c1")) == 1

    BlockingBackend.gate.set()
    async for _ in first.body_iterator:
        pass
    assert store.list_turns("c1")[0]["status"] == "done"
    assert await delete("c1") == {"ok": True}  # the claim was released; now it can go


async def test_a_chat_arriving_during_a_delete_is_409_and_the_claim_is_released_after():
    class SlowDisconnect(FakeBackend):
        release = asyncio.Event()

        async def disconnect(self):
            await SlowDisconnect.release.wait()

    SlowDisconnect.release = asyncio.Event()
    app = create_app(registry=SessionRegistry(client_factory=SlowDisconnect))
    chat = _endpoint(app, "/api/chat", "POST")
    delete = _endpoint(app, "/api/conversations/{conversation_id}", "DELETE")

    response = await chat(ChatRequest(conversation_id="c1", prompt="one"))
    async for _ in response.body_iterator:
        pass
    deleting = asyncio.create_task(delete("c1"))
    await asyncio.sleep(0.05)  # the delete is parked in disconnect(), holding the claim

    with pytest.raises(HTTPException) as busy:
        await chat(ChatRequest(conversation_id="c1", prompt="racing"))
    assert busy.value.status_code == 409
    with pytest.raises(HTTPException) as busy_delete:
        await delete("c1")
    assert busy_delete.value.status_code == 409

    SlowDisconnect.release.set()
    assert await deleting == {"ok": True}
    assert store.get("c1") is None and store.list_turns("c1") == []

    again = await chat(ChatRequest(conversation_id="c1", prompt="fresh"))
    async for _ in again.body_iterator:
        pass
    assert [t["user_text"] for t in store.list_turns("c1")] == ["fresh"]


async def test_a_delete_that_404s_releases_its_claim():
    app, _ = _app()
    delete = _endpoint(app, "/api/conversations/{conversation_id}", "DELETE")
    for _ in range(2):  # a leaked claim would turn the second 404 into a 409
        with pytest.raises(HTTPException) as missing:
            await delete("nope")
        assert missing.value.status_code == 404


def test_patch_and_delete_of_another_profiles_conversation_are_404_and_change_nothing():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    admin_app, _ = _app(admin_routes=True)
    admin_client = TestClient(admin_app)
    assert admin_client.patch("/api/conversations/c1", json={"title": "hijack", "pinned": True}).status_code == 404
    assert admin_client.delete("/api/conversations/c1").status_code == 404
    kept = client.get("/api/conversations/c1").json()
    assert kept["title"] != "hijack" and kept["pinned"] == 0 and kept["title_source"] != "user"
    assert len(kept["turns"]) == 1


def test_limit_is_clamped():
    for i, cid in enumerate(["a", "b", "c"]):
        store.ensure(cid, "qa", f"title {cid}", now=100.0 + i)
    client = TestClient(_app()[0])
    zero = client.get("/api/conversations?limit=0").json()
    assert [c["conversation_id"] for c in zero["conversations"]] == ["c"]
    assert zero["next_cursor"]
    huge = client.get("/api/conversations?limit=9999")
    assert huge.status_code == 200
    assert len(huge.json()["conversations"]) == 3 and huge.json()["next_cursor"] is None


def test_archived_filter_paginates():
    for i, cid in enumerate(["a", "b", "c", "d"]):
        store.ensure(cid, "qa", f"title {cid}", now=100.0 + i)
    for cid in ("a", "b", "c"):
        store.update(cid, "qa", archived=True)
    client = TestClient(_app()[0])
    assert [c["conversation_id"] for c in client.get("/api/conversations").json()["conversations"]] == ["d"]
    page1 = client.get("/api/conversations?archived=true&limit=2").json()
    assert [c["conversation_id"] for c in page1["conversations"]] == ["c", "b"]
    page2 = client.get(f"/api/conversations?archived=true&limit=2&cursor={page1['next_cursor']}").json()
    assert [c["conversation_id"] for c in page2["conversations"]] == ["a"]
    assert page2["next_cursor"] is None


def test_pagination_across_a_tie_on_updated_at_neither_skips_nor_repeats():
    for cid in ("a", "b", "c"):
        store.ensure(cid, "qa", f"title {cid}", now=100.0)
    client = TestClient(_app()[0])
    seen = []
    cursor = None
    while True:
        url = "/api/conversations?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url).json()
        seen += [c["conversation_id"] for c in page["conversations"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ["c", "b", "a"]


@pytest.mark.parametrize("cursor", ["nan", "inf:x", "-inf:x", "nan:abc"])
def test_non_finite_cursors_are_400(cursor):
    assert TestClient(_app()[0]).get(f"/api/conversations?cursor={cursor}").status_code == 400


def test_release_keeps_the_stored_conversation():
    app, registry = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    assert client.delete("/api/session/c1").json() == {"ok": True, "released": True}
    assert registry.peek("c1") is None
    assert client.get("/api/conversations/c1").status_code == 200


class TrackedBlocking(BlockingBackend):
    disconnects = 0
    restarts = 0

    async def disconnect(self):
        TrackedBlocking.disconnects += 1

    async def restart(self, preserve_context=True):
        TrackedBlocking.restarts += 1


async def test_release_and_refresh_during_a_turn_leave_the_live_agent_alone():
    BlockingBackend.gate = asyncio.Event()
    TrackedBlocking.disconnects = TrackedBlocking.restarts = 0
    registry = SessionRegistry(client_factory=TrackedBlocking)
    app = create_app(registry=registry)
    chat = _endpoint(app, "/api/chat", "POST")
    release = _endpoint(app, "/api/session/{session_id}", "DELETE")
    refresh = _endpoint(app, "/api/session/{session_id}/refresh", "POST")

    response = await chat(ChatRequest(conversation_id="c1", prompt="one"))
    await response.body_iterator.__anext__()  # the turn is now in flight
    live = registry.peek("c1")

    assert await release("c1") == {"ok": True, "released": False}
    assert await refresh("c1") == {"ok": False, "refreshed": False, "reason": "turn in progress"}
    assert registry.peek("c1") is live
    assert TrackedBlocking.disconnects == 0 and TrackedBlocking.restarts == 0

    BlockingBackend.gate.set()
    events = []
    async for chunk in response.body_iterator:
        events.append(chunk)
    assert any("turn_done" in chunk for chunk in events)
    assert store.list_turns("c1")[0]["status"] == "done"

    # once the turn is over, refresh and release work again
    assert (await refresh("c1"))["refreshed"] is True
    assert TrackedBlocking.restarts == 1
    assert await release("c1") == {"ok": True, "released": True}
    assert registry.peek("c1") is None and TrackedBlocking.disconnects == 1


def test_pinned_conversation_older_than_a_page_of_newer_ones_is_on_page_one():
    store.ensure("old", "qa", "old", now=1.0)
    store.update("old", "qa", pinned=True)
    for i in range(30):
        store.ensure(f"n{i:02d}", "qa", f"n{i}", now=100.0 + i)
    client = TestClient(_app()[0])
    page1 = client.get("/api/conversations?limit=10").json()
    assert page1["conversations"][0]["conversation_id"] == "old"
    assert len(page1["conversations"]) == 10


def test_pagination_across_the_pinned_boundary_neither_skips_nor_repeats():
    for i, cid in enumerate(["a", "b", "c", "d", "e"]):
        store.ensure(cid, "qa", cid, now=100.0 + i)
    store.update("a", "qa", pinned=True)
    store.update("c", "qa", pinned=True)
    client = TestClient(_app()[0])
    for limit in (1, 2, 3):
        seen, cursor = [], None
        while True:
            url = f"/api/conversations?limit={limit}" + (f"&cursor={cursor}" if cursor else "")
            page = client.get(url).json()
            seen += [c["conversation_id"] for c in page["conversations"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == ["c", "a", "e", "d", "b"], limit


def test_ties_across_the_pinned_boundary_stay_safe():
    for cid in ("a", "b", "c", "d"):
        store.ensure(cid, "qa", cid, now=100.0)
    store.update("b", "qa", pinned=True)
    store.update("d", "qa", pinned=True)
    client = TestClient(_app()[0])
    page1 = client.get("/api/conversations?limit=3").json()
    page2 = client.get(f"/api/conversations?limit=3&cursor={page1['next_cursor']}").json()
    assert [c["conversation_id"] for c in page1["conversations"] + page2["conversations"]] == ["d", "b", "c", "a"]


@pytest.mark.parametrize(
    "cursor", ["100.0:a", "garbage", "2:100.0:a", "-1:100.0:a", "x:100.0:a", "1:nan:a", "1:inf:a", "1:abc:a", "1:100.0:"]
)
def test_malformed_cursors_are_400(cursor):
    assert TestClient(_app()[0]).get(f"/api/conversations?cursor={cursor}").status_code == 400


def test_a_failed_conversation_lookup_does_not_500_the_turn(monkeypatch):
    real = store.get

    def broken(cid, profile=None):
        raise RuntimeError("db unavailable")

    app, _ = _app()
    monkeypatch.setattr(store, "get", broken)
    response = _chat(TestClient(app), "c1", "hi")
    assert response.status_code == 200
    assert "turn_done" in [e["type"] for e in _sse(response.text)]
    monkeypatch.setattr(store, "get", real)


def test_replay_reads_turns_without_their_blocks(monkeypatch):
    app, _ = _app()
    _chat(TestClient(app), "c1", "first question")
    seen = []
    real = store.list_turns

    def spy(cid, **kwargs):
        seen.append(kwargs)
        return real(cid, **kwargs)

    monkeypatch.setattr(store, "list_turns", spy)
    app2, _ = _app()
    _chat(TestClient(app2), "c1", "second question")
    assert seen == [{"with_blocks": False}]


def test_startup_marks_this_profiles_running_turns_interrupted_only():
    store.ensure("q", "qa", "q")
    store.ensure("a", "admin", "a")
    store.begin_turn("q", "q", "claude-sdk")
    store.begin_turn("a", "a", "claude-sdk")
    app, _ = _app()
    with TestClient(app):  # entering the context runs the lifespan startup
        pass
    assert store.list_turns("q")[0]["status"] == "interrupted"
    assert store.list_turns("a")[0]["status"] == "running"


@pytest.mark.parametrize("template_name", ["index.html", "admin.html"])
def test_pages_carry_the_sidebar_and_conversations_script(template_name):
    from artmind.webui.app import WEBUI_DIR

    app, _ = _app(template_name=template_name)
    html = TestClient(app).get("/").text
    mtime = (WEBUI_DIR / "static" / "conversations.js").stat().st_mtime_ns
    assert f'src="/static/conversations.js?v={mtime}"' in html
    for needle in ('id="sidebar"', 'id="new-chat"', 'id="conv-list"', 'id="sidebar-toggle"'):
        assert needle in html
