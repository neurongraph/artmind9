"""Backends spawn with ARTMIND_TRACE_CONTEXT when given a trace-context file."""

import asyncio

from artmind.webui import backends
from artmind.webui.profiles import ADMIN_PROFILE


def test_new_trace_context_creates_empty_file_under_logs(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    ctx = backends.new_trace_context()
    assert ctx.parent == tmp_path / "logs" / "trace-context"
    assert ctx.read_text() == ""


def test_claude_sdk_backend_env_and_path(tmp_path):
    ctx = tmp_path / "c.id"
    backend = backends.create_backend("claude-sdk", ADMIN_PROFILE, trace_context=ctx)
    assert backend.trace_context_path == ctx
    assert backend._env["ARTMIND_TRACE_CONTEXT"] == str(ctx)


def test_claude_sdk_backend_untraced_by_default():
    backend = backends.create_backend("claude-sdk")
    assert backend.trace_context_path is None
    assert "ARTMIND_TRACE_CONTEXT" not in (backend._env or {})


def test_acp_backend_env_and_path(tmp_path):
    ctx = tmp_path / "c.id"
    backend = backends.create_backend("acp", ADMIN_PROFILE, trace_context=ctx)
    assert backend.trace_context_path == ctx
    assert backend._subprocess_env()["ARTMIND_TRACE_CONTEXT"] == str(ctx)


def test_backend_factory_trace_gives_each_backend_its_own_file(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    factory = backends.backend_factory(ADMIN_PROFILE, trace=True)
    a, b = factory("claude-sdk"), factory("claude-sdk")
    assert a.trace_context_path != b.trace_context_path
    assert backends.backend_factory(ADMIN_PROFILE)("claude-sdk").trace_context_path is None


def test_claude_sdk_disconnect_unlinks_trace_context(tmp_path):
    """disconnect() unlinks the trace-context file without spawning a process."""
    ctx = tmp_path / "c.id"
    ctx.write_text("trace-id-123")
    backend = backends.create_backend("claude-sdk", ADMIN_PROFILE, trace_context=ctx)

    # Stub the inner SDK client so no real agent is started.
    class _StubClient:
        async def disconnect(self):
            pass

    backend._client = _StubClient()
    asyncio.run(backend.disconnect())
    assert not ctx.exists()


def test_acp_disconnect_unlinks_trace_context(tmp_path):
    """disconnect() unlinks the trace-context file even when never connected."""
    ctx = tmp_path / "c.id"
    ctx.write_text("trace-id-456")
    backend = backends.create_backend("acp", ADMIN_PROFILE, trace_context=ctx)
    # _proc is None (never connected); disconnect is safe to call and should
    # unlink the context file before its early return for the no-process case.
    asyncio.run(backend.disconnect())
    assert not ctx.exists()
