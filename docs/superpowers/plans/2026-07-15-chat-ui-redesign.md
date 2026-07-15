# Chat UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the NiceGUI chat front end with a ChatGPT/Claude-style web UI: FastAPI + Jinja + one vanilla JS file, word-by-word streaming, thinking disclosures, right-hand tool-trace drawer, light/dark themes.

**Architecture:** A new `artmind/webui/` package. `agent.py` holds the agent options and a stateful `EventMapper` that turns Claude Agent SDK messages (including partial `StreamEvent`s) into small JSON event dicts. `app.py` is a FastAPI app: `GET /` serves one Jinja template; `POST /api/chat` streams those events as `text/event-stream`, read browser-side with `fetch()` + ReadableStream; a `SessionRegistry` keeps one lazily-connected `ClaudeSDKClient` per browser tab and reaps idle ones. All rendering is `static/app.js` (vanilla) + `static/style.css` (CSS variables for both themes) + a vendored `marked.min.js`.

**Tech Stack:** FastAPI (existing dep), uvicorn (existing dep), jinja2 (new direct dep, already transitive), claude-agent-sdk, vanilla JS, marked.js (vendored single file). NiceGUI is removed.

**Spec:** `docs/superpowers/specs/2026-07-15-chat-ui-redesign-design.md`

**Conventions for this repo:**
- Run Python via `uv run` (e.g. `uv run pytest`, `uv run python`).
- pytest is configured with `asyncio_mode = "auto"` — async test functions need no decorator.
- Commit after every task; end commit messages with `Co-Authored-By:` line per project convention.

**Two deviations from the spec:**
1. The spec says tab-close cleanup uses `navigator.sendBeacon` with `DELETE /api/session/{id}`. `sendBeacon` can only send POST, so the JS uses `fetch(url, {method: "DELETE", keepalive: true})` instead — same route, same effect.
2. The spec puts the session registry inside `agent.py`; the plan gives it its own `artmind/webui/sessions.py` so event mapping and session lifecycle stay independently testable.

---

### Task 1: Event mapping (`agent.py`)

The one piece of real logic: map SDK messages onto UI event dicts. Stateful because `content_block_stop` events only carry an index — the mapper remembers each block's type from `content_block_start` so the client knows whether a text or thinking block just finished.

**Files:**
- Create: `artmind/webui/__init__.py` (empty)
- Create: `artmind/webui/agent.py`
- Test: `tests/test_webui_events.py`

- [ ] **Step 1: Create the package and write the failing tests**

Create empty `artmind/webui/__init__.py`, then `tests/test_webui_events.py`:

```python
"""Unit tests for the SDK→UI event mapping. No API calls."""

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from artmind.webui.agent import EventMapper, clip


def _stream_event(event: dict, parent_tool_use_id=None) -> StreamEvent:
    return StreamEvent(
        uuid="u1", session_id="s1", event=event, parent_tool_use_id=parent_tool_use_id
    )


def test_text_delta():
    mapper = EventMapper()
    events = mapper.map(
        _stream_event(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "Hel"}}
        )
    )
    assert events == [{"type": "text_delta", "text": "Hel"}]


def test_thinking_delta():
    mapper = EventMapper()
    events = mapper.map(
        _stream_event(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "hmm"}}
        )
    )
    assert events == [{"type": "thinking_delta", "text": "hmm"}]


def test_block_done_reports_block_type_from_start_event():
    mapper = EventMapper()
    mapper.map(_stream_event(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking"}}))
    mapper.map(_stream_event(
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text"}}))
    assert mapper.map(_stream_event({"type": "content_block_stop", "index": 0})) == [
        {"type": "block_done", "block": "thinking"}
    ]
    assert mapper.map(_stream_event({"type": "content_block_stop", "index": 1})) == [
        {"type": "block_done", "block": "text"}
    ]


def test_tool_use_block_stop_emits_nothing():
    mapper = EventMapper()
    mapper.map(_stream_event(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use"}}))
    assert mapper.map(_stream_event({"type": "content_block_stop", "index": 0})) == []


def test_subagent_stream_events_are_skipped():
    mapper = EventMapper()
    events = mapper.map(
        _stream_event(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "inner"}},
            parent_tool_use_id="tool_1",
        )
    )
    assert events == []


def test_assistant_message_emits_tool_calls_only():
    # text/thinking already arrived via deltas; only ToolUseBlocks map to events
    message = AssistantMessage(
        content=[
            TextBlock(text="already streamed"),
            ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
        ],
        model="claude-fable-5",
    )
    events = EventMapper().map(message)
    assert events == [
        {"type": "tool_call", "id": "t1", "name": "Bash",
         "input": clip({"command": "ls"})}
    ]


def test_user_message_emits_tool_results():
    message = UserMessage(
        content=[ToolResultBlock(tool_use_id="t1", content="file1\nfile2")]
    )
    events = EventMapper().map(message)
    assert events == [
        {"type": "tool_result", "tool_id": "t1", "content": "file1\nfile2"}
    ]


def test_user_message_with_plain_string_content_emits_nothing():
    assert EventMapper().map(UserMessage(content="hello")) == []


def test_result_message_emits_turn_done():
    message = ResultMessage(
        subtype="success", duration_ms=12345, duration_api_ms=10000,
        is_error=False, num_turns=3, session_id="s1", total_cost_usd=0.0421,
    )
    events = EventMapper().map(message)
    assert events == [
        {"type": "turn_done", "turns": 3, "duration_s": 12.3, "cost": 0.0421}
    ]


def test_clip_truncates_long_values():
    long = "x" * 700
    clipped = clip(long)
    assert clipped.startswith("x" * 600)
    assert "[700 chars total]" in clipped
    assert clip("short") == "short"
    assert clip({"a": 1}) == '{"a": 1}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_webui_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'artmind.webui.agent'` (or ImportError).

- [ ] **Step 3: Write `artmind/webui/agent.py`**

```python
"""Agent configuration and SDK→UI event mapping for the chat web UI.

The chat agent must NOT inherit the repo's developer environment (CLAUDE.md
graphify rules, enforcement hooks): it serves end-user Q&A, not coding. So we
enable only the artmind skills instead of loading all project settings.
"""

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRACE_CLIP = 600

_SYSTEM_APPEND = """\
You are the artmind assistant, an end-user interface to the artmind knowledge
system. Users ask about knowledge stored in artmind domains. Route their
requests through the artmind skills: artmind-query for questions,
artmind-update for adding facts, artmind-refine for graph maintenance,
artmind-ingestion-helper for ingesting documents. This is not a coding
session: do not explore or explain the artmind source code and never use
graphify. Answer conversationally; no raw JSON or command output unless asked."""


def clip(value: Any, limit: int = TRACE_CLIP) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else f"{text[:limit]} … [{len(text)} chars total]"


def agent_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        skills=[
            "artmind-query",
            "artmind-update",
            "artmind-refine",
            "artmind-ingestion-helper",
        ],
        system_prompt={"type": "preset", "preset": "claude_code", "append": _SYSTEM_APPEND},
        permission_mode="bypassPermissions",
        thinking={"type": "enabled", "budget_tokens": 8000, "display": "summarized"},
        include_partial_messages=True,
    )


class EventMapper:
    """Maps one turn's SDK messages onto UI event dicts.

    Stateful: ``content_block_stop`` events only carry an index, so block
    types are remembered from ``content_block_start``. Create one per turn.
    """

    def __init__(self) -> None:
        self._block_types: dict[int, str] = {}

    def map(self, message: Any) -> list[dict[str, Any]]:
        if isinstance(message, StreamEvent):
            return self._map_stream_event(message)
        if isinstance(message, AssistantMessage):
            # Text/thinking content already arrived via stream deltas.
            return [
                {"type": "tool_call", "id": b.id, "name": b.name, "input": clip(b.input)}
                for b in message.content
                if isinstance(b, ToolUseBlock)
            ]
        if isinstance(message, UserMessage):
            content = message.content if isinstance(message.content, list) else []
            return [
                {"type": "tool_result", "tool_id": b.tool_use_id, "content": clip(b.content)}
                for b in content
                if isinstance(b, ToolResultBlock)
            ]
        if isinstance(message, ResultMessage):
            return [
                {
                    "type": "turn_done",
                    "turns": message.num_turns,
                    "duration_s": round(message.duration_ms / 1000, 1),
                    "cost": message.total_cost_usd,
                }
            ]
        return []

    def _map_stream_event(self, message: StreamEvent) -> list[dict[str, Any]]:
        if message.parent_tool_use_id:
            # Inner subagent/tool traffic; the main pane only shows the top level.
            return []
        event = message.event
        kind = event.get("type")
        if kind == "content_block_start":
            index = event.get("index", 0)
            self._block_types[index] = event.get("content_block", {}).get("type", "text")
            return []
        if kind == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                return [{"type": "text_delta", "text": delta.get("text", "")}]
            if delta.get("type") == "thinking_delta":
                return [{"type": "thinking_delta", "text": delta.get("thinking", "")}]
            return []
        if kind == "content_block_stop":
            block = self._block_types.pop(event.get("index", 0), "text")
            if block in ("text", "thinking"):
                return [{"type": "block_done", "block": block}]
            return []
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webui_events.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/__init__.py artmind/webui/agent.py tests/test_webui_events.py
git commit -m "feat(webui): SDK-to-UI event mapping and agent options

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Session registry

One lazily-connected `ClaudeSDKClient` per browser tab, idle-reaped. The client factory is injectable so tests never touch the real SDK.

**Files:**
- Create: `artmind/webui/sessions.py`
- Test: `tests/test_webui_sessions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_webui_sessions.py`:

```python
"""Unit tests for the web UI session registry. Uses a fake SDK client."""

from artmind.webui.sessions import SessionRegistry


class FakeClient:
    def __init__(self):
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False


async def test_get_creates_and_connects_lazily():
    registry = SessionRegistry(client_factory=FakeClient)
    client = await registry.get("tab-1")
    assert isinstance(client, FakeClient)
    assert client.connected


async def test_get_reuses_existing_client():
    registry = SessionRegistry(client_factory=FakeClient)
    first = await registry.get("tab-1")
    second = await registry.get("tab-1")
    assert first is second


async def test_separate_sessions_get_separate_clients():
    registry = SessionRegistry(client_factory=FakeClient)
    assert await registry.get("tab-1") is not await registry.get("tab-2")


async def test_drop_disconnects_and_forgets():
    registry = SessionRegistry(client_factory=FakeClient)
    client = await registry.get("tab-1")
    await registry.drop("tab-1")
    assert not client.connected
    assert await registry.get("tab-1") is not client


async def test_drop_unknown_session_is_a_noop():
    registry = SessionRegistry(client_factory=FakeClient)
    await registry.drop("nope")  # must not raise


async def test_peek_returns_client_without_creating():
    registry = SessionRegistry(client_factory=FakeClient)
    assert registry.peek("tab-1") is None
    client = await registry.get("tab-1")
    assert registry.peek("tab-1") is client


async def test_sweep_drops_only_idle_sessions():
    registry = SessionRegistry(client_factory=FakeClient, idle_timeout_s=100)
    stale = await registry.get("stale-tab")
    fresh = await registry.get("fresh-tab")
    # age only the stale session
    client_entry, _ = registry._sessions["stale-tab"]
    registry._sessions["stale-tab"] = (client_entry, -1000.0)
    dropped = await registry.sweep()
    assert dropped == 1
    assert not stale.connected
    assert fresh.connected
    assert registry.peek("stale-tab") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_webui_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'artmind.webui.sessions'`.

- [ ] **Step 3: Write `artmind/webui/sessions.py`**

```python
"""Per-browser-tab agent sessions for the chat web UI."""

import time
from typing import Any, Callable

from claude_agent_sdk import ClaudeSDKClient

from artmind.webui.agent import agent_options

IDLE_TIMEOUT_S = 30 * 60


def _default_factory() -> ClaudeSDKClient:
    return ClaudeSDKClient(agent_options())


class SessionRegistry:
    """One lazily-connected SDK client per browser tab, reaped when idle."""

    def __init__(
        self,
        client_factory: Callable[[], Any] = _default_factory,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
    ) -> None:
        self._client_factory = client_factory
        self._idle_timeout_s = idle_timeout_s
        self._sessions: dict[str, tuple[Any, float]] = {}

    async def get(self, session_id: str) -> Any:
        entry = self._sessions.get(session_id)
        if entry is None:
            client = self._client_factory()
            await client.connect()
        else:
            client = entry[0]
        self._sessions[session_id] = (client, time.monotonic())
        return client

    def peek(self, session_id: str) -> Any | None:
        entry = self._sessions.get(session_id)
        return entry[0] if entry else None

    async def drop(self, session_id: str) -> None:
        entry = self._sessions.pop(session_id, None)
        if entry:
            await entry[0].disconnect()

    async def sweep(self) -> int:
        now = time.monotonic()
        stale = [
            sid
            for sid, (_, last_used) in self._sessions.items()
            if now - last_used > self._idle_timeout_s
        ]
        for sid in stale:
            await self.drop(sid)
        return len(stale)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webui_sessions.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/sessions.py tests/test_webui_sessions.py
git commit -m "feat(webui): per-tab session registry with idle sweep

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: FastAPI app and routes

Thin glue: serve the shell, stream events, interrupt, close. The streaming route is tested end-to-end with a fake client injected through the registry.

**Files:**
- Create: `artmind/webui/app.py`
- Create: `artmind/webui/templates/index.html` (placeholder body for now — real markup in Task 4; needed so `GET /` works)
- Create: `artmind/webui/static/.gitkeep` (StaticFiles requires the directory)
- Test: `tests/test_webui_app.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_webui_app.py`:

```python
"""Route tests for the web UI FastAPI app, with a fake SDK client."""

import json

from fastapi.testclient import TestClient

from claude_agent_sdk import ResultMessage, StreamEvent

from artmind.webui.app import create_app
from artmind.webui.sessions import SessionRegistry


class FakeClient:
    """Replays a canned message sequence for one turn."""

    def __init__(self):
        self.connected = False
        self.prompts: list[str] = []
        self.interrupted = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        yield StreamEvent(
            uuid="u1", session_id="s1",
            event={"type": "content_block_delta", "index": 0,
                   "delta": {"type": "text_delta", "text": "Hi"}},
        )
        yield ResultMessage(
            subtype="success", duration_ms=1000, duration_api_ms=900,
            is_error=False, num_turns=1, session_id="s1", total_cost_usd=0.01,
        )


def _client() -> tuple[TestClient, SessionRegistry]:
    registry = SessionRegistry(client_factory=FakeClient)
    app = create_app(registry=registry)
    return TestClient(app), registry


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_index_serves_html():
    client, _ = _client()
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_chat_streams_events_as_sse():
    client, _ = _client()
    response = client.post(
        "/api/chat", json={"session_id": "tab-1", "prompt": "hello"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert events[0] == {"type": "text_delta", "text": "Hi"}
    assert events[-1]["type"] == "turn_done"


def test_chat_reuses_session_client():
    client, registry = _client()
    client.post("/api/chat", json={"session_id": "tab-1", "prompt": "one"})
    client.post("/api/chat", json={"session_id": "tab-1", "prompt": "two"})
    assert registry.peek("tab-1").prompts == ["one", "two"]


def test_interrupt_reaches_session_client():
    client, registry = _client()
    client.post("/api/chat", json={"session_id": "tab-1", "prompt": "hello"})
    response = client.post("/api/session/tab-1/interrupt")
    assert response.status_code == 200
    assert registry.peek("tab-1").interrupted


def test_interrupt_unknown_session_is_ok():
    client, _ = _client()
    assert client.post("/api/session/nope/interrupt").status_code == 200


def test_delete_session_disconnects():
    client, registry = _client()
    client.post("/api/chat", json={"session_id": "tab-1", "prompt": "hello"})
    sdk_client = registry.peek("tab-1")
    response = client.delete("/api/session/tab-1")
    assert response.status_code == 200
    assert not sdk_client.connected
    assert registry.peek("tab-1") is None


def test_chat_turns_exception_into_error_event():
    class ExplodingClient(FakeClient):
        async def query(self, prompt):
            raise RuntimeError("boom")

    registry = SessionRegistry(client_factory=ExplodingClient)
    app = create_app(registry=registry)
    client = TestClient(app)
    response = client.post(
        "/api/chat", json={"session_id": "tab-1", "prompt": "hello"}
    )
    events = _parse_sse(response.text)
    assert events == [{"type": "error", "message": "boom"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_webui_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'artmind.webui.app'`.

- [ ] **Step 3: Write `artmind/webui/app.py`, placeholder template, static dir**

`artmind/webui/app.py`:

```python
"""FastAPI app serving the artmind chat web UI."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from artmind.webui.agent import EventMapper
from artmind.webui.sessions import SessionRegistry

WEBUI_DIR = Path(__file__).resolve().parent
DEFAULT_UI_PORT = 8378
SWEEP_INTERVAL_S = 60


class ChatRequest(BaseModel):
    session_id: str
    prompt: str


def create_app(registry: SessionRegistry | None = None) -> FastAPI:
    registry = registry or SessionRegistry()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def sweep_loop():
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_S)
                await registry.sweep()

        task = asyncio.create_task(sweep_loop())
        yield
        task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEBUI_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=WEBUI_DIR / "templates")

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    @app.post("/api/chat")
    async def chat(payload: ChatRequest) -> StreamingResponse:
        client = await registry.get(payload.session_id)

        async def stream():
            mapper = EventMapper()
            try:
                await client.query(payload.prompt)
                async for message in client.receive_response():
                    for event in mapper.map(message):
                        yield f"data: {json.dumps(event)}\n\n"
            except Exception as exc:  # stream errors to the client, don't 500
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/session/{session_id}/interrupt")
    async def interrupt(session_id: str):
        client = registry.peek(session_id)
        if client is not None:
            await client.interrupt()
        return {"ok": True}

    @app.delete("/api/session/{session_id}")
    async def close_session(session_id: str):
        await registry.drop(session_id)
        return {"ok": True}

    return app


def run_chat_ui(host: str = "127.0.0.1", port: int = DEFAULT_UI_PORT) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
```

`artmind/webui/templates/index.html` (placeholder, replaced in Task 4):

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>artmind</title></head>
<body>artmind chat UI — shell lands in Task 4.</body>
</html>
```

Create the static dir: `mkdir -p artmind/webui/static && touch artmind/webui/static/.gitkeep`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webui_app.py -v`
Expected: all PASS. (`test_chat_turns_exception_into_error_event` proves errors stream instead of raising; note TestClient buffers the whole SSE body, which is fine for these assertions.)

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/app.py artmind/webui/templates/index.html artmind/webui/static/.gitkeep tests/test_webui_app.py
git commit -m "feat(webui): FastAPI app with SSE chat streaming and session routes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Vendored marked.js and the real HTML shell

**Files:**
- Create: `artmind/webui/static/vendor/marked.min.js` (downloaded)
- Modify: `artmind/webui/templates/index.html` (replace placeholder entirely)

- [ ] **Step 1: Vendor marked.js**

Download marked v12 (UMD, minified, ~39KB) — **ask the user for permission before downloading if executing under a policy that requires it**:

```bash
mkdir -p artmind/webui/static/vendor
curl -fsSL https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js \
  -o artmind/webui/static/vendor/marked.min.js
```

Verify: `head -c 200 artmind/webui/static/vendor/marked.min.js` shows minified JS beginning with a license/banner comment mentioning `marked`, and `ls -la` shows ~35–45KB.

- [ ] **Step 2: Replace `index.html` with the real shell**

Full contents of `artmind/webui/templates/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>artmind</title>
  <link rel="stylesheet" href="/static/style.css">
  <script>
    // Set theme before first paint to avoid a flash of the wrong theme.
    (function () {
      var saved = localStorage.getItem("artmind-theme");
      var dark = saved ? saved === "dark"
                       : matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    })();
  </script>
</head>
<body>
  <header class="topbar">
    <div class="brand">artmind</div>
    <div class="topbar-actions">
      <button id="theme-toggle" class="icon-btn" title="Toggle theme" aria-label="Toggle theme">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="5"></circle>
          <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path>
        </svg>
      </button>
      <button id="trace-toggle" class="icon-btn" title="Agent trace" aria-label="Toggle agent trace">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14.7 6.3a4.5 4.5 0 0 0-6.1 5.9L3 17.8V21h3.2l5.6-5.6a4.5 4.5 0 0 0 5.9-6.1l-2.9 2.9-2.1-2.1 2-2.8z"></path>
        </svg>
        <span id="trace-badge" class="badge" hidden>0</span>
      </button>
    </div>
  </header>

  <main id="chat" class="chat" aria-live="polite"></main>

  <aside id="drawer" class="drawer" aria-hidden="true">
    <div class="drawer-head">
      <span class="drawer-title">Agent trace</span>
      <button id="drawer-close" class="icon-btn" aria-label="Close trace">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M6 6l12 12M18 6L6 18"></path>
        </svg>
      </button>
    </div>
    <div id="trace-list" class="trace-list"></div>
  </aside>

  <div class="composer-wrap">
    <form id="composer" class="composer">
      <textarea id="prompt" rows="1" placeholder="Ask artmind…" autofocus></textarea>
      <button id="send" type="submit" class="send-btn" disabled aria-label="Send">
        <svg class="icon-send" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2">
          <path d="M12 19V5m0 0-6 6m6-6 6 6"></path>
        </svg>
        <svg class="icon-stop" viewBox="0 0 24 24" width="18" height="18" fill="currentColor" hidden>
          <rect x="6" y="6" width="12" height="12" rx="2"></rect>
        </svg>
      </button>
    </form>
  </div>

  <script src="/static/vendor/marked.min.js"></script>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Verify the app still serves**

Run: `uv run pytest tests/test_webui_app.py -v`
Expected: all PASS (template still renders; missing style.css/app.js just 404 in a browser, which is fine until Tasks 5–6).

- [ ] **Step 4: Commit**

```bash
git add artmind/webui/static/vendor/marked.min.js artmind/webui/templates/index.html
git commit -m "feat(webui): chat shell template and vendored marked.js

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Stylesheet

All styling in one file; CSS variables define both themes. Design intent: content floats on the background, hierarchy from spacing/type, no widget boxes.

**Files:**
- Create: `artmind/webui/static/style.css`

- [ ] **Step 1: Create `artmind/webui/static/style.css`**

Full contents:

```css
/* ── theme variables ─────────────────────────────────────────────── */
:root[data-theme="light"] {
  --bg: #faf9f5;
  --bg-elev: #ffffff;
  --bg-inset: #f1efe9;
  --text: #2d2a26;
  --text-muted: #7a766c;
  --border: rgba(0, 0, 0, 0.08);
  --accent: #6b6fa8;
  --accent-contrast: #ffffff;
  --user-pill: #edebe3;
  --error: #b3423f;
  --shadow: 0 4px 24px rgba(0, 0, 0, 0.07);
}
:root[data-theme="dark"] {
  --bg: #262624;
  --bg-elev: #30302e;
  --bg-inset: #1e1e1c;
  --text: #e8e6e3;
  --text-muted: #9b9890;
  --border: rgba(255, 255, 255, 0.1);
  --accent: #8a8fd0;
  --accent-contrast: #1e1e1c;
  --user-pill: #3a3a37;
  --error: #e07a77;
  --shadow: 0 4px 24px rgba(0, 0, 0, 0.35);
}

/* ── base ────────────────────────────────────────────────────────── */
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font: 15px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; }

/* ── header ──────────────────────────────────────────────────────── */
.topbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 20;
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px;
  background: color-mix(in srgb, var(--bg) 85%, transparent);
  backdrop-filter: blur(8px);
}
.brand { font-weight: 600; font-size: 15px; letter-spacing: 0.01em; }
.topbar-actions { display: flex; gap: 4px; }
.icon-btn {
  position: relative;
  display: inline-flex; align-items: center; justify-content: center;
  width: 34px; height: 34px; border-radius: 8px; color: var(--text-muted);
}
.icon-btn:hover { background: var(--bg-inset); color: var(--text); }
.badge {
  position: absolute; top: 2px; right: 2px;
  min-width: 15px; height: 15px; padding: 0 4px;
  border-radius: 8px; background: var(--accent); color: var(--accent-contrast);
  font-size: 10px; font-weight: 600; line-height: 15px; text-align: center;
}

/* ── chat column ─────────────────────────────────────────────────── */
.chat {
  max-width: 48rem;
  margin: 0 auto;
  padding: 72px 20px 160px;
  transition: margin-right 0.25s ease;
}

/* user message: soft pill, right-aligned */
.msg-user { display: flex; justify-content: flex-end; margin: 28px 0 20px; }
.msg-user .pill {
  max-width: 80%;
  padding: 10px 16px;
  border-radius: 18px;
  background: var(--user-pill);
  white-space: pre-wrap;
  overflow-wrap: break-word;
}

/* assistant: markdown flows directly on the background */
.md { overflow-wrap: break-word; }
.md.streaming { white-space: pre-wrap; }
.md p { margin: 0.6em 0; }
.md h1, .md h2, .md h3 { margin: 1.2em 0 0.5em; line-height: 1.3; }
.md h1 { font-size: 1.35em; } .md h2 { font-size: 1.2em; } .md h3 { font-size: 1.05em; }
.md ul, .md ol { padding-left: 1.4em; }
.md a { color: var(--accent); }
.md blockquote {
  margin: 0.8em 0; padding: 0.2em 1em;
  border-left: 3px solid var(--border); color: var(--text-muted);
}
.md table { border-collapse: collapse; margin: 0.8em 0; display: block; overflow-x: auto; }
.md th, .md td { border: 1px solid var(--border); padding: 6px 12px; }
.md code {
  background: var(--bg-inset);
  padding: 0.15em 0.4em; border-radius: 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.88em;
}
.md pre {
  position: relative;
  background: var(--bg-inset);
  border-radius: 10px;
  padding: 14px 16px;
  overflow-x: auto;
}
.md pre code { background: none; padding: 0; font-size: 0.85em; }
.copy-btn {
  position: absolute; top: 8px; right: 8px;
  padding: 3px 9px; border-radius: 6px;
  background: var(--bg-elev); color: var(--text-muted);
  font-size: 11px; opacity: 0; transition: opacity 0.15s;
}
.md pre:hover .copy-btn { opacity: 1; }
.copy-btn:hover { color: var(--text); }

/* ── thinking disclosure ─────────────────────────────────────────── */
.thinking { margin: 16px 0 8px; }
.thinking summary {
  list-style: none; cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px;
  color: var(--text-muted); font-size: 13px;
  user-select: none;
}
.thinking summary::-webkit-details-marker { display: none; }
.thinking summary::before {
  content: "▸"; font-size: 10px; transition: transform 0.15s;
}
.thinking[open] summary::before { transform: rotate(90deg); }
.thinking-body {
  margin: 8px 0 0 16px; padding-left: 12px;
  border-left: 2px solid var(--border);
  color: var(--text-muted); font-size: 13px; font-style: italic;
  white-space: pre-wrap; overflow-wrap: break-word;
}
.thinking-label.streaming {
  background: linear-gradient(90deg,
    var(--text-muted) 35%, var(--text) 50%, var(--text-muted) 65%);
  background-size: 200% 100%;
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
  animation: shimmer 1.8s linear infinite;
}
@keyframes shimmer { to { background-position: -200% 0; } }

/* inline error notice */
.notice {
  margin: 16px 0; padding: 10px 14px;
  border: 1px solid var(--border); border-radius: 10px;
  color: var(--error); font-size: 13.5px; background: var(--bg-elev);
}

/* ── composer ────────────────────────────────────────────────────── */
.composer-wrap {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 20;
  padding: 24px 20px 20px;
  background: linear-gradient(transparent, var(--bg) 45%);
  transition: margin-right 0.25s ease;
}
.composer {
  display: flex; align-items: flex-end; gap: 8px;
  max-width: 48rem; margin: 0 auto;
  padding: 10px 10px 10px 18px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 22px;
  box-shadow: var(--shadow);
}
.composer:focus-within { border-color: color-mix(in srgb, var(--accent) 50%, var(--border)); }
.composer textarea {
  flex: 1; resize: none; border: none; outline: none;
  background: transparent; color: var(--text);
  font: inherit; line-height: 1.5;
  max-height: 200px; padding: 4px 0;
}
.composer textarea::placeholder { color: var(--text-muted); }
.send-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 34px; height: 34px; border-radius: 50%;
  background: var(--accent); color: var(--accent-contrast);
  transition: opacity 0.15s; flex-shrink: 0;
}
.send-btn:disabled { opacity: 0.35; cursor: default; }

/* ── trace drawer ────────────────────────────────────────────────── */
.drawer {
  position: fixed; top: 0; right: 0; bottom: 0; z-index: 30;
  width: 380px; max-width: 90vw;
  background: var(--bg-elev);
  border-left: 1px solid var(--border);
  transform: translateX(100%);
  transition: transform 0.25s ease;
  display: flex; flex-direction: column;
}
body.drawer-open .drawer { transform: translateX(0); }
/* wide screens: drawer pushes content instead of covering it */
@media (min-width: 1200px) {
  body.drawer-open .chat, body.drawer-open .composer-wrap { margin-right: 380px; }
}
.drawer-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; border-bottom: 1px solid var(--border);
}
.drawer-title { font-weight: 600; font-size: 14px; }
.trace-list { overflow-y: auto; padding: 12px; flex: 1; }

.tool-card {
  border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; margin-bottom: 10px; font-size: 13px;
}
.tool-head { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot.running { background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
.dot.done { background: #5da571; animation: none; }
.dot.error { background: var(--error); animation: none; }
@keyframes pulse { 50% { opacity: 0.3; } }
.tool-card details { margin-top: 6px; }
.tool-card summary {
  cursor: pointer; color: var(--text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  max-width: 100%; display: block;
}
.tool-card pre {
  margin: 6px 0 0; padding: 8px 10px;
  background: var(--bg-inset); border-radius: 8px;
  font-size: 11.5px; white-space: pre-wrap; overflow-wrap: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.tool-result-label { margin-top: 8px; color: var(--text-muted); font-size: 11px; }
.trace-summary { color: var(--text-muted); font-size: 12px; padding: 4px 2px 10px; }
```

- [ ] **Step 2: Commit**

```bash
git add artmind/webui/static/style.css
git commit -m "feat(webui): stylesheet with light/dark themes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Client JS

Everything interactive: session id, theme toggle, composer, stream reader, block rendering, thinking lifecycle, tool drawer, errors, cleanup.

**Files:**
- Create: `artmind/webui/static/app.js`

- [ ] **Step 1: Create `artmind/webui/static/app.js`**

Full contents:

```js
"use strict";

// ── page-level state ─────────────────────────────────────────────────
const sessionId = crypto.randomUUID();

const chatEl = document.getElementById("chat");
const promptEl = document.getElementById("prompt");
const composerEl = document.getElementById("composer");
const sendBtn = document.getElementById("send");
const iconSend = sendBtn.querySelector(".icon-send");
const iconStop = sendBtn.querySelector(".icon-stop");
const drawerEl = document.getElementById("drawer");
const traceListEl = document.getElementById("trace-list");
const traceBadgeEl = document.getElementById("trace-badge");

let streaming = false;
let abortController = null;
let toolCount = 0;

// per-turn rendering state
let turnEl = null;        // container for the current assistant turn
let textBlock = null;     // {el, raw}
let thinkingBlock = null; // {details, label, body, startedAt}

// ── helpers ──────────────────────────────────────────────────────────
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - 120;
}

function scrollToBottom(force) {
  if (force || nearBottom()) window.scrollTo(0, document.body.scrollHeight);
}

// ── theme ────────────────────────────────────────────────────────────
document.getElementById("theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("artmind-theme", next);
});

// ── trace drawer ─────────────────────────────────────────────────────
document.getElementById("trace-toggle").addEventListener("click", () => {
  document.body.classList.toggle("drawer-open");
  drawerEl.setAttribute("aria-hidden", String(!document.body.classList.contains("drawer-open")));
});
document.getElementById("drawer-close").addEventListener("click", () => {
  document.body.classList.remove("drawer-open");
  drawerEl.setAttribute("aria-hidden", "true");
});

function bumpBadge() {
  toolCount += 1;
  traceBadgeEl.textContent = String(toolCount);
  traceBadgeEl.hidden = false;
}

function addToolCard(ev) {
  const card = el("div", "tool-card");
  card.dataset.toolId = ev.id;
  const head = el("div", "tool-head");
  head.appendChild(el("span", "dot running"));
  head.appendChild(el("span", "tool-name", ev.name));
  card.appendChild(head);
  const details = el("details");
  const summary = el("summary", null, ev.input);
  details.appendChild(summary);
  const pre = el("pre", null, ev.input);
  details.appendChild(pre);
  card.appendChild(details);
  traceListEl.appendChild(card);
  bumpBadge();
  traceListEl.scrollTop = traceListEl.scrollHeight;
}

function attachToolResult(ev) {
  const card = traceListEl.querySelector(`[data-tool-id="${CSS.escape(ev.tool_id)}"]`);
  if (!card) return;
  const dot = card.querySelector(".dot");
  dot.className = "dot done";
  card.appendChild(el("div", "tool-result-label", "result"));
  card.appendChild(el("pre", null, ev.content ?? ""));
  traceListEl.scrollTop = traceListEl.scrollHeight;
}

function addTraceSummary(ev) {
  const cost = ev.cost ? ` · $${ev.cost.toFixed(4)}` : "";
  traceListEl.appendChild(
    el("div", "trace-summary", `turn done · ${ev.turns} turns · ${ev.duration_s}s${cost}`)
  );
  traceListEl.scrollTop = traceListEl.scrollHeight;
}

// ── message rendering ────────────────────────────────────────────────
function addUserMessage(text) {
  const wrap = el("div", "msg-user");
  wrap.appendChild(el("div", "pill", text));
  chatEl.appendChild(wrap);
  scrollToBottom(true);
}

function ensureTurn() {
  if (!turnEl) {
    turnEl = el("div", "turn");
    chatEl.appendChild(turnEl);
  }
  return turnEl;
}

function ensureThinking() {
  if (thinkingBlock) return thinkingBlock;
  const details = el("details", "thinking");
  details.open = true;
  const summary = el("summary");
  const label = el("span", "thinking-label streaming", "Thinking");
  summary.appendChild(label);
  details.appendChild(summary);
  const body = el("div", "thinking-body");
  details.appendChild(body);
  ensureTurn().appendChild(details);
  thinkingBlock = { details, label, body, startedAt: Date.now() };
  return thinkingBlock;
}

function finalizeThinking() {
  if (!thinkingBlock) return;
  const secs = Math.max(1, Math.round((Date.now() - thinkingBlock.startedAt) / 1000));
  thinkingBlock.label.classList.remove("streaming");
  thinkingBlock.label.textContent = `Thought for ${secs}s`;
  thinkingBlock.details.open = false;
  thinkingBlock = null;
}

function ensureText() {
  if (textBlock) return textBlock;
  const node = el("div", "md streaming");
  ensureTurn().appendChild(node);
  textBlock = { el: node, raw: "" };
  return textBlock;
}

function finalizeText() {
  if (!textBlock) return;
  textBlock.el.classList.remove("streaming");
  textBlock.el.innerHTML = marked.parse(textBlock.raw);
  addCopyButtons(textBlock.el);
  textBlock = null;
}

function addCopyButtons(scope) {
  for (const pre of scope.querySelectorAll("pre")) {
    const btn = el("button", "copy-btn", "copy");
    btn.type = "button";
    btn.addEventListener("click", () => {
      navigator.clipboard.writeText(pre.querySelector("code")?.textContent ?? pre.textContent);
      btn.textContent = "copied";
      setTimeout(() => (btn.textContent = "copy"), 1200);
    });
    pre.appendChild(btn);
  }
}

function showNotice(text) {
  chatEl.appendChild(el("div", "notice", text));
  scrollToBottom(true);
}

// ── event dispatch ───────────────────────────────────────────────────
function handleEvent(ev) {
  switch (ev.type) {
    case "thinking_delta":
      ensureThinking().body.textContent += ev.text;
      break;
    case "text_delta":
      finalizeThinking(); // answer started: collapse live thinking
      ensureText();
      textBlock.raw += ev.text;
      textBlock.el.textContent = textBlock.raw;
      break;
    case "block_done":
      if (ev.block === "text") finalizeText();
      else finalizeThinking();
      break;
    case "tool_call":
      addToolCard(ev);
      break;
    case "tool_result":
      attachToolResult(ev);
      break;
    case "turn_done":
      addTraceSummary(ev);
      break;
    case "error":
      showNotice(`Agent error: ${ev.message}`);
      const card = el("div", "tool-card");
      const head = el("div", "tool-head");
      head.appendChild(el("span", "dot error"));
      head.appendChild(el("span", "tool-name", "Error"));
      card.appendChild(head);
      card.appendChild(el("pre", null, ev.message));
      traceListEl.appendChild(card);
      break;
  }
  scrollToBottom(false);
}

// ── streaming transport ──────────────────────────────────────────────
async function streamTurn(prompt) {
  abortController = new AbortController();
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, prompt }),
    signal: abortController.signal,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop(); // keep the trailing partial frame
    for (const frame of frames) {
      const line = frame.trim();
      if (line.startsWith("data: ")) handleEvent(JSON.parse(line.slice(6)));
    }
  }
}

function setStreaming(on) {
  streaming = on;
  iconSend.hidden = on;
  iconStop.hidden = !on;
  sendBtn.disabled = on ? false : promptEl.value.trim() === "";
  sendBtn.setAttribute("aria-label", on ? "Stop" : "Send");
}

async function send() {
  const prompt = promptEl.value.trim();
  if (!prompt || streaming) return;
  promptEl.value = "";
  autogrow();
  addUserMessage(prompt);
  setStreaming(true);
  try {
    await streamTurn(prompt);
  } catch (err) {
    if (err.name !== "AbortError") {
      showNotice("Connection lost — send again to retry.");
    }
  } finally {
    finalizeThinking();
    finalizeText();
    turnEl = null;
    setStreaming(false);
    promptEl.focus();
  }
}

async function stop() {
  if (abortController) abortController.abort();
  try {
    await fetch(`/api/session/${sessionId}/interrupt`, { method: "POST" });
  } catch (_) { /* server may already be gone */ }
}

// ── composer wiring ──────────────────────────────────────────────────
function autogrow() {
  promptEl.style.height = "auto";
  promptEl.style.height = Math.min(promptEl.scrollHeight, 200) + "px";
}

promptEl.addEventListener("input", () => {
  autogrow();
  if (!streaming) sendBtn.disabled = promptEl.value.trim() === "";
});

promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

composerEl.addEventListener("submit", (e) => {
  e.preventDefault();
  if (streaming) stop();
  else send();
});

// ── cleanup on tab close ─────────────────────────────────────────────
window.addEventListener("pagehide", () => {
  fetch(`/api/session/${sessionId}`, { method: "DELETE", keepalive: true }).catch(() => {});
});
```

- [ ] **Step 2: Configure marked for safe, code-friendly rendering**

marked v12 handles fenced code blocks and tables by default (GFM on). No extra config needed — verify during the Task 8 smoke test that entity_names with underscores render correctly; if mid-word underscores italicize, add near the top of `app.js` after the state section:

```js
marked.use({ pedantic: false, gfm: true, breaks: false });
```

- [ ] **Step 3: Commit**

```bash
git add artmind/webui/static/app.js
git commit -m "feat(webui): client-side streaming, rendering, and drawer logic

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: CLI wiring, dependency swap, delete old UI

**Files:**
- Modify: `artmind/cli.py:226-234` (the `chat-ui` command)
- Delete: `artmind/chat_ui.py`
- Modify: `pyproject.toml` (remove `nicegui`, add `jinja2`)

- [ ] **Step 1: Point the CLI at the new app**

In `artmind/cli.py`, replace the body of the `chat_ui` command (currently imports `artmind.chat_ui`):

```python
@cli.command("chat-ui")
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface to bind.")
@click.option("--port", default=8378, show_default=True, type=int, help="Port to bind.")
def chat_ui(host: str, port: int) -> None:
    """Launch the artmind chat web UI (FastAPI + Claude Agent SDK)."""
    from artmind.webui.app import run_chat_ui

    click.echo(f"artmind chat UI on http://{host}:{port}")
    run_chat_ui(host=host, port=port)
```

- [ ] **Step 2: Delete the NiceGUI module and swap dependencies**

```bash
git rm artmind/chat_ui.py
```

In `pyproject.toml` dependencies: delete the line `"nicegui>=3.14.0",` and add `"jinja2>=3.1.0",` (keep the list alphabetical). Then:

```bash
uv lock && uv sync
```

Expected: lockfile shrinks (nicegui and its exclusive transitive deps drop out); jinja2 stays (FastAPI templating uses it).

- [ ] **Step 3: Verify nothing else imported the old module, and tests still pass**

```bash
grep -rn "chat_ui" artmind/ tests/ justfile
```

Expected: only `artmind/cli.py` (the new import) and justfile's `uv run artmind chat-ui` command name (which is unchanged behavior, no edit needed).

```bash
uv run pytest tests/ -v
```

Expected: all webui tests PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(webui): switch chat-ui command to new FastAPI UI, drop NiceGUI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Live smoke test and graph update

**Files:** none created — verification only.

- [ ] **Step 1: Launch and verify end-to-end**

Start the serve daemon + UI (`just ui-start`, or `uv run artmind chat-ui` directly) and open `http://127.0.0.1:8378` in a browser. Verify, in order:

1. Shell renders: header, empty chat, floating composer; no console errors (vendored marked loads).
2. Theme: matches system scheme on load; toggle flips it; reload keeps the choice.
3. Send a real prompt (e.g. "what domains do you know about?"): user pill appears right-aligned; "Thinking" shimmer appears and streams; answer text streams word-by-word; thinking collapses to "Thought for Ns" when text starts; markdown renders on `block_done` (no bubble around assistant text).
4. Tool drawer: badge increments as skills/tools run; opening the drawer shows cards with running→done dots, expandable args, results; turn summary line appears at the end.
5. Composer: Enter sends, Shift+Enter makes a newline, textarea autogrows, send button becomes stop while streaming; pressing stop aborts the turn and re-enables the composer.
6. Multi-turn: send a follow-up referencing the first answer — same session context is used.
7. Narrow window (<1200px): drawer overlays; wide: drawer pushes content.

Fix anything broken by editing source and re-checking. This step is done only when all seven checks pass.

- [ ] **Step 2: Update the knowledge graph and commit any fixes**

```bash
graphify update .
git add -A
git commit -m "chore(webui): smoke-test fixes and graph update

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Skip the commit if the smoke test required no changes and graphify produced no diff.)
