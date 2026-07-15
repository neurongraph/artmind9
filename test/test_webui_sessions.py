"""Unit tests for the web UI session registry. Uses a fake agent backend."""

import asyncio

from artmind.webui.sessions import SessionRegistry


class FakeClient:
    def __init__(self, backend: str = "claude-sdk"):
        self.backend = backend
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False


class SlowFakeClient(FakeClient):
    """Like FakeClient but yields to the event loop mid-connect/disconnect,
    so concurrent registry calls actually interleave under asyncio.gather
    instead of one running to completion before the other starts."""

    async def connect(self):
        await asyncio.sleep(0)
        self.connected = True

    async def disconnect(self):
        await asyncio.sleep(0)
        self.connected = False


class FailingConnectClient(FakeClient):
    """Fails on connect(); tracks whether disconnect() cleanup was invoked."""

    def __init__(self, backend: str = "claude-sdk"):
        super().__init__(backend)
        self.disconnect_called = False

    async def connect(self):
        raise RuntimeError("boom")

    async def disconnect(self):
        self.disconnect_called = True
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


async def test_get_passes_backend_to_factory():
    registry = SessionRegistry(client_factory=FakeClient)
    client = await registry.get("tab-1", backend="acp")
    assert client.backend == "acp"


async def test_backend_mismatch_replaces_session():
    """The UI issues a fresh session id on backend switch, but a mismatched
    request must not be answered by the wrong agent."""
    registry = SessionRegistry(client_factory=FakeClient)
    first = await registry.get("tab-1", backend="claude-sdk")
    second = await registry.get("tab-1", backend="acp")
    assert second is not first
    assert not first.connected
    assert second.connected
    assert second.backend == "acp"
    assert registry.peek("tab-1") is second


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
    client_entry, backend, _ = registry._sessions["stale-tab"]
    registry._sessions["stale-tab"] = (client_entry, backend, -1000.0)
    dropped = await registry.sweep()
    assert dropped == 1
    assert not stale.connected
    assert fresh.connected
    assert registry.peek("stale-tab") is None


async def test_concurrent_get_for_new_session_creates_only_one_client():
    """Regression test: two simultaneous get() calls for a brand-new
    session id used to each construct and connect their own client, with
    the loser's connection never stored and never disconnected (leaked)."""
    created = []

    def factory(backend):
        client = SlowFakeClient(backend)
        created.append(client)
        return client

    registry = SessionRegistry(client_factory=factory)
    first, second = await asyncio.gather(
        registry.get("tab-1"), registry.get("tab-1")
    )

    assert first is second
    assert len(created) == 1
    assert first.connected
    # the registry itself only tracks the one client that was returned
    assert registry.peek("tab-1") is first


async def test_concurrent_sweep_and_get_never_returns_a_disconnected_zombie():
    """Regression test: sweep() used to snapshot stale session ids and then
    evict them without rechecking, so a concurrent get() that had already
    refreshed a session's timestamp could still have it yanked out from
    under it, or get() could hand back a client sweep was disconnecting.
    Whatever order the lock resolves the race in, get() must never return
    a client that isn't the one live in the registry, and it must be
    connected."""
    registry = SessionRegistry(client_factory=SlowFakeClient, idle_timeout_s=100)
    original = await registry.get("tab-1")
    entry, backend, _ = registry._sessions["tab-1"]
    registry._sessions["tab-1"] = (entry, backend, -1000.0)  # mark stale

    _, result = await asyncio.gather(registry.sweep(), registry.get("tab-1"))

    assert result.connected
    assert registry.peek("tab-1") is result
    # if sweep won the race, get() must have created a fresh client rather
    # than handing back the (now disconnected) original
    if result is original:
        assert original.connected
    else:
        assert not original.connected


async def test_get_cleans_up_client_on_connect_failure():
    registry = SessionRegistry(client_factory=FailingConnectClient)
    try:
        await registry.get("tab-1")
        assert False, "expected connect() failure to propagate"
    except RuntimeError:
        pass
    # the failed client must not be left registered
    assert registry.peek("tab-1") is None
