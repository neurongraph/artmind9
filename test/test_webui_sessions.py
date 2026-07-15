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
