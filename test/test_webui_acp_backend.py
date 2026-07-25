"""End-to-end tests for ACPBackend against the scripted fake agent.

Each test spawns test/fake_acp_agent.py as a real subprocess and talks
JSON-RPC over actual pipes, so the read loop, permission auto-answer, and
crash handling are exercised for real.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from artmind.webui.backends.acp import ACPBackend

FAKE_AGENT = str(Path(__file__).parent / "fake_acp_agent.py")


def _backend(scenario: str, tmp_path) -> ACPBackend:
    return ACPBackend(
        agent_cmd=[sys.executable, FAKE_AGENT, scenario],
        cwd=str(tmp_path),
        connect_timeout_s=10.0,
    )


async def _collect(backend: ACPBackend) -> list[dict]:
    return [event async for event in backend.receive_events()]


async def test_happy_turn_yields_expected_event_sequence(tmp_path):
    backend = _backend("happy", tmp_path)
    await backend.connect()
    try:
        await backend.query("hi")
        events = await asyncio.wait_for(_collect(backend), 10)
    finally:
        await backend.disconnect()

    assert events == [
        {"type": "thinking_delta", "text": "pondering"},
        {"type": "block_done", "block": "thinking"},
        {"type": "text_delta", "text": "Hello "},
        {"type": "text_delta", "text": "world."},
        {"type": "block_done", "block": "text"},
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "artmind query",
            "input": '{"command": "artmind query pattern1"}',
        },
        {"type": "tool_result", "tool_id": "call-1", "content": "42 rows"},
        {"type": "text_delta", "text": "Done."},
        {"type": "block_done", "block": "text"},
        events[-1],
    ]
    assert events[-1]["type"] == "turn_done"
    assert events[-1]["cost"] is None


async def test_permission_request_is_auto_allowed_preferring_allow_always(tmp_path):
    backend = _backend("permission", tmp_path)
    await backend.connect()
    try:
        await backend.query("hi")
        events = await asyncio.wait_for(_collect(backend), 10)
    finally:
        await backend.disconnect()

    texts = [e["text"] for e in events if e["type"] == "text_delta"]
    assert texts == ["perm:always"]
    assert events[-1]["type"] == "turn_done"


async def test_agent_crash_mid_turn_yields_error_and_terminates_stream(tmp_path):
    backend = _backend("crash", tmp_path)
    await backend.connect()
    try:
        await backend.query("hi")
        events = await asyncio.wait_for(_collect(backend), 10)  # must not hang
    finally:
        await backend.disconnect()

    assert any(e["type"] == "error" and "exited" in e["message"] for e in events)
    assert events[-1]["type"] == "turn_done"


async def test_interrupt_sends_cancel_and_turn_ends_cleanly(tmp_path):
    backend = _backend("cancel", tmp_path)
    await backend.connect()
    try:
        await backend.query("hi")

        async def interrupt_soon():
            await asyncio.sleep(0.2)
            await backend.interrupt()

        events, _ = await asyncio.wait_for(
            asyncio.gather(_collect(backend), interrupt_soon()), 10
        )
    finally:
        await backend.disconnect()

    assert not any(e["type"] == "error" for e in events)
    assert events[-1]["type"] == "turn_done"


async def test_disconnect_kills_the_agent_process(tmp_path):
    backend = _backend("happy", tmp_path)
    await backend.connect()
    proc = backend._proc
    await backend.disconnect()
    assert proc is not None
    assert proc.returncode is not None


async def test_connect_with_mode_selects_it_and_still_works(tmp_path):
    backend = ACPBackend(
        agent_cmd=[sys.executable, FAKE_AGENT, "happy"],
        cwd=str(tmp_path),
        mode="artmind",
        connect_timeout_s=10.0,
    )
    await backend.connect()
    try:
        await backend.query("hi")
        events = await asyncio.wait_for(_collect(backend), 10)
    finally:
        await backend.disconnect()
    assert events[-1]["type"] == "turn_done"


def test_subprocess_env_without_model_is_unset(tmp_path):
    backend = ACPBackend(agent_cmd=["opencode", "acp"], cwd=str(tmp_path))
    assert backend._subprocess_env() is None


def test_subprocess_env_with_model_sets_opencode_config_content(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    backend = ACPBackend(
        agent_cmd=["opencode", "acp"], cwd=str(tmp_path), model="anthropic/claude-sonnet-4"
    )
    env = backend._subprocess_env()
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == {"model": "anthropic/claude-sonnet-4"}


def test_subprocess_env_merges_onto_existing_opencode_config_content(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", json.dumps({"small_model": "ollama/gemma4:12b"}))
    backend = ACPBackend(
        agent_cmd=["opencode", "acp"], cwd=str(tmp_path), model="ollama/gemma4:26b-mlx"
    )
    env = backend._subprocess_env()
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == {
        "small_model": "ollama/gemma4:12b",
        "model": "ollama/gemma4:26b-mlx",
    }


async def test_connect_failure_propagates(tmp_path):
    backend = ACPBackend(
        agent_cmd=[sys.executable, "-c", "import sys; sys.exit(1)"],
        cwd=str(tmp_path),
        connect_timeout_s=5.0,
    )
    with pytest.raises(Exception):
        await backend.connect()
    await backend.disconnect()
