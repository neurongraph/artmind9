"""Route tests for the web UI FastAPI app, with a fake agent backend."""

import json

import pytest
from fastapi.testclient import TestClient
from jinja2.exceptions import TemplateNotFound

from artmind.webui.app import create_app
from artmind.webui.sessions import SessionRegistry


class FakeBackend:
    """Replays a canned UI-event sequence for one turn."""

    def __init__(self, backend: str = "claude-sdk"):
        self.backend = backend
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

    async def receive_events(self):
        yield {"type": "text_delta", "text": "Hi"}
        yield {"type": "turn_done", "turns": 1, "duration_s": 1.0, "cost": 0.01}


def _client() -> tuple[TestClient, SessionRegistry]:
    registry = SessionRegistry(client_factory=FakeBackend)
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


def test_index_uses_custom_template_name():
    """create_app's `/` route must actually render whatever template_name
    it was given, not a hardcoded "index.html" — proven here by pointing it
    at a template that doesn't exist and observing Jinja2 look for that
    exact name instead of silently falling back to the default."""
    registry = SessionRegistry(client_factory=FakeBackend)
    app = create_app(registry=registry, template_name="does-not-exist.html")
    client = TestClient(app)
    with pytest.raises(TemplateNotFound) as exc_info:
        client.get("/")
    assert exc_info.value.name == "does-not-exist.html"


def test_admin_template_serves_html_with_admin_chrome():
    """admin.html must render (Task 1.3) and carry the admin-specific chrome:
    retitled brand, and a nav link to the Lane B dashboard — while keeping
    the same element IDs app.js relies on (#chat, #prompt, #composer, etc.)."""
    registry = SessionRegistry(client_factory=FakeBackend)
    app = create_app(registry=registry, template_name="admin.html")
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "text/html" in response.headers["content-type"]
    assert "artmind admin" in body
    assert 'href="/dashboard"' in body
    for element_id in ("chat", "prompt", "composer", "send", "drawer", "trace-list"):
        assert f'id="{element_id}"' in body


def test_index_accepts_page_title_without_breaking_default_template():
    registry = SessionRegistry(client_factory=FakeBackend)
    app = create_app(registry=registry, page_title="Admin Console")
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200


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


def test_chat_backend_reaches_factory():
    client, registry = _client()
    client.post(
        "/api/chat",
        json={"session_id": "tab-1", "prompt": "hello", "backend": "acp"},
    )
    assert registry.peek("tab-1").backend == "acp"


def test_chat_rejects_unknown_backend():
    client, _ = _client()
    response = client.post(
        "/api/chat",
        json={"session_id": "tab-1", "prompt": "hello", "backend": "nope"},
    )
    assert response.status_code == 422


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
    backend = registry.peek("tab-1")
    response = client.delete("/api/session/tab-1")
    assert response.status_code == 200
    assert not backend.connected
    assert registry.peek("tab-1") is None


def test_chat_turns_exception_into_error_event():
    class ExplodingBackend(FakeBackend):
        async def query(self, prompt):
            raise RuntimeError("boom")

    registry = SessionRegistry(client_factory=ExplodingBackend)
    app = create_app(registry=registry)
    client = TestClient(app)
    response = client.post(
        "/api/chat", json={"session_id": "tab-1", "prompt": "hello"}
    )
    events = _parse_sse(response.text)
    assert events == [{"type": "error", "message": "boom"}]


def test_chat_connect_failure_streams_error_not_500():
    class UnconnectableBackend(FakeBackend):
        async def connect(self):
            raise RuntimeError("connect failed")

    registry = SessionRegistry(client_factory=UnconnectableBackend)
    app = create_app(registry=registry)
    client = TestClient(app)
    response = client.post(
        "/api/chat", json={"session_id": "tab-1", "prompt": "hello"}
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events == [{"type": "error", "message": "connect failed"}]
