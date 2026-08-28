"""A7 — durable session lifecycle (ADR 0007 / 0013).

Covers the Claude SDK backend's session-id capture + resume-on-restart, the ACP
backend's clean restart, and ``SessionRegistry.refresh`` driving either one.
The SDK client and ACP subprocess are faked; no real ``claude`` CLI, no ACP
agent, no network.
"""
from __future__ import annotations

import types

import pytest

import artmind.webui.backends.claude_sdk as claude_sdk
from artmind.webui.sessions import SessionRegistry


# ── agent_options: resume wiring ───────────────────────────────────────────


def test_agent_options_passes_resume_through():
    from artmind.webui.agent import agent_options

    opts = agent_options(resume="sess-123")
    assert opts.resume == "sess-123"


def test_agent_options_resume_defaults_to_none():
    from artmind.webui.agent import agent_options

    assert agent_options().resume is None


def test_agent_options_passes_model_through():
    from artmind.webui.agent import agent_options

    opts = agent_options(model="claude-haiku-4-5", fallback_model="claude-sonnet-5")
    assert opts.model == "claude-haiku-4-5"
    assert opts.fallback_model == "claude-sonnet-5"


def test_agent_options_model_defaults_to_none():
    from artmind.webui.agent import agent_options

    opts = agent_options()
    assert opts.model is None
    assert opts.fallback_model is None


def test_agent_options_passes_env_through():
    from artmind.webui.agent import agent_options

    opts = agent_options(env={"ANTHROPIC_BASE_URL": "https://gateway.example.com/ica"})
    assert opts.env == {"ANTHROPIC_BASE_URL": "https://gateway.example.com/ica"}


def test_agent_options_env_defaults_to_empty():
    from artmind.webui.agent import agent_options

    assert agent_options().env == {}


# ── fake SDK client ─────────────────────────────────────────────────────────


class FakeSDKClient:
    """Records constructor options and yields a canned message stream."""

    instances: list["FakeSDKClient"] = []

    def __init__(self, options):
        self.options = options
        self.connected = False
        self.disconnected = False
        self.queries: list[str] = []
        self.messages: list = []
        FakeSDKClient.instances.append(self)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False
        self.disconnected = True

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        for message in self.messages:
            yield message

    async def interrupt(self):
        pass


@pytest.fixture
def fake_sdk(monkeypatch):
    FakeSDKClient.instances = []
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", FakeSDKClient)
    return FakeSDKClient


# ── session-id capture ──────────────────────────────────────────────────────


async def _drain(backend):
    async for _ in backend.receive_events():
        pass


async def test_sdk_backend_captures_session_id_from_message_attr(fake_sdk):
    backend = claude_sdk.ClaudeSDKBackend()
    assert backend.session_id is None
    assert backend.supports_resume is True
    backend._client.messages = [types.SimpleNamespace(session_id="sess-abc")]
    await _drain(backend)
    assert backend.session_id == "sess-abc"


async def test_sdk_backend_captures_session_id_from_system_init_data(fake_sdk):
    """The SystemMessage init frame carries the id under data['session_id']."""
    backend = claude_sdk.ClaudeSDKBackend()
    backend._client.messages = [types.SimpleNamespace(data={"session_id": "sess-init"})]
    await _drain(backend)
    assert backend.session_id == "sess-init"


async def test_sdk_backend_latest_session_id_wins(fake_sdk):
    backend = claude_sdk.ClaudeSDKBackend()
    backend._client.messages = [
        types.SimpleNamespace(session_id="first"),
        types.SimpleNamespace(session_id="second"),
    ]
    await _drain(backend)
    assert backend.session_id == "second"


# ── restart: resume vs clean ─────────────────────────────────────────────────


async def test_sdk_restart_resumes_captured_session(fake_sdk):
    backend = claude_sdk.ClaudeSDKBackend()
    await backend.connect()
    backend._client.messages = [types.SimpleNamespace(session_id="sess-xyz")]
    await _drain(backend)
    original_client = backend._client

    await backend.restart(preserve_context=True)

    assert original_client.disconnected, "old client must be torn down"
    assert backend._client is not original_client, "a new client is built"
    assert backend._client.options.resume == "sess-xyz", "resumes the captured session"
    assert backend._client.connected
    # The session id is retained across the refresh.
    assert backend.session_id == "sess-xyz"


async def test_sdk_restart_without_preserve_starts_clean(fake_sdk):
    backend = claude_sdk.ClaudeSDKBackend()
    await backend.connect()
    backend._client.messages = [types.SimpleNamespace(session_id="sess-xyz")]
    await _drain(backend)

    await backend.restart(preserve_context=False)

    assert backend._client.options.resume is None, "clean restart does not resume"
    assert backend._client.connected


async def test_sdk_restart_with_no_session_yet_starts_clean(fake_sdk):
    """preserve_context is a no-op when no session id has been observed."""
    backend = claude_sdk.ClaudeSDKBackend()
    await backend.connect()
    await backend.restart(preserve_context=True)
    assert backend._client.options.resume is None


async def test_sdk_backend_constructed_with_resume(fake_sdk):
    backend = claude_sdk.ClaudeSDKBackend(resume="prior-sess")
    assert backend.session_id == "prior-sess"
    assert backend._client.options.resume == "prior-sess"


async def test_sdk_backend_constructed_with_model(fake_sdk):
    backend = claude_sdk.ClaudeSDKBackend(model="claude-haiku-4-5", fallback_model="claude-sonnet-5")
    assert backend._client.options.model == "claude-haiku-4-5"
    assert backend._client.options.fallback_model == "claude-sonnet-5"


async def test_sdk_restart_preserves_model(fake_sdk):
    backend = claude_sdk.ClaudeSDKBackend(model="claude-haiku-4-5")
    await backend.connect()
    await backend.restart(preserve_context=False)
    assert backend._client.options.model == "claude-haiku-4-5"


async def test_sdk_restart_preserves_env_override(fake_sdk):
    backend = claude_sdk.ClaudeSDKBackend(env={"ANTHROPIC_BASE_URL": "https://gateway.example.com/ica"})
    await backend.connect()
    await backend.restart(preserve_context=False)
    assert backend._client.options.env == {"ANTHROPIC_BASE_URL": "https://gateway.example.com/ica"}


# ── SessionRegistry.refresh ──────────────────────────────────────────────────


class FakeBackend:
    def __init__(self, backend: str = "claude-sdk", supports_resume: bool = True):
        self.backend = backend
        self._supports_resume = supports_resume
        self.connected = False
        self.restarts: list[bool] = []
        self.session_id = "sess-1"

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def restart(self, *, preserve_context: bool = True):
        self.restarts.append(preserve_context)

    @property
    def supports_resume(self):
        return self._supports_resume


async def test_registry_refresh_restarts_the_live_client():
    registry = SessionRegistry(client_factory=FakeBackend)
    client = await registry.get("tab-1")
    refreshed = await registry.refresh("tab-1")
    assert refreshed is client, "the same client object is kept (it rebuilt itself)"
    assert client.restarts == [True], "restart invoked with preserve_context=True"
    assert registry.peek("tab-1") is client


async def test_registry_refresh_forwards_preserve_context_flag():
    registry = SessionRegistry(client_factory=FakeBackend)
    await registry.get("tab-1")
    await registry.refresh("tab-1", preserve_context=False)
    assert registry.peek("tab-1").restarts == [False]


async def test_registry_refresh_unknown_session_returns_none():
    registry = SessionRegistry(client_factory=FakeBackend)
    assert await registry.refresh("nope") is None


async def test_registry_refresh_bumps_idle_timer():
    import time

    registry = SessionRegistry(client_factory=FakeBackend)
    await registry.get("tab-1")
    # age the session
    entry, backend, _ = registry._sessions["tab-1"]
    registry._sessions["tab-1"] = (entry, backend, -1000.0)
    await registry.refresh("tab-1")
    _, _, last_used = registry._sessions["tab-1"]
    assert last_used > time.monotonic() - 5, "refresh resets the idle timer"


# ── ACP backend: clean restart (context loss accepted) ───────────────────────


def _make_acp_backend():
    from artmind.webui.backends.acp import ACPBackend

    return ACPBackend(agent_cmd=["true"], cwd="/tmp")


async def test_acp_backend_does_not_support_resume():
    backend = _make_acp_backend()
    assert backend.supports_resume is False


async def test_acp_restart_is_a_clean_reconnect(monkeypatch):
    backend = _make_acp_backend()
    backend._session_id = "acp-old"
    backend._first_prompt_sent = True

    calls = {"disconnect": 0, "connect": 0}

    async def fake_disconnect():
        calls["disconnect"] += 1

    async def fake_connect():
        calls["connect"] += 1
        backend._session_id = "acp-new"

    monkeypatch.setattr(backend, "disconnect", fake_disconnect)
    monkeypatch.setattr(backend, "connect", fake_connect)

    await backend.restart(preserve_context=True)  # honoured only as a log signal

    assert calls == {"disconnect": 1, "connect": 1}
    assert backend._first_prompt_sent is False, "fresh session re-sends the preamble"
    assert backend.session_id == "acp-new", "a new session id is established"
