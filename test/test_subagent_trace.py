"""Background sub-agents keep the trace id of the turn that started them (P62)."""

import asyncio

import pytest

from artmind._entry import _trace_id
from artmind.webui.agent import agent_options
from artmind.webui.profiles import ADMIN_PROFILE, QA_PROFILE
from artmind.webui.subagent_trace import SubagentTraceBinder


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)


def _bash(command, agent_id=None):
    data = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_id:
        data["agent_id"] = agent_id
    return data


def _run(coro):
    return asyncio.run(coro)


def test_subagent_after_turn_end_and_later_turn_keeps_starting_id(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("turn1")
    binder = SubagentTraceBinder(ctx)
    _run(binder.on_subagent_start({"agent_id": "a1"}, None, None))
    ctx.write_text("")  # turn 1 ended
    assert binder.stamp(_bash("artmind query x", "a1"), "artmind query x") == "ARTMIND_TRACE_ID=turn1 artmind query x"
    ctx.write_text("turn2")  # a later turn is open
    assert binder.stamp(_bash("artmind query x", "a1"), "artmind query x").startswith("ARTMIND_TRACE_ID=turn1 ")


def test_main_agent_and_non_artmind_commands_untouched(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("t1")
    binder = SubagentTraceBinder(ctx)
    assert binder.stamp(_bash("artmind query x"), "artmind query x") == "artmind query x"
    assert binder.stamp(_bash("ls", "a1"), "ls") == "ls"


def test_unbound_subagent_is_untraced_not_misattributed(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("turn2")
    binder = SubagentTraceBinder(ctx)
    assert binder.stamp(_bash("artmind query x", "ghost"), "artmind query x") == "ARTMIND_TRACE_ID=- artmind query x"


def test_start_with_no_open_turn_binds_nothing(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")
    binder = SubagentTraceBinder(ctx)
    binder.bind("a1")
    ctx.write_text("turn2")
    assert binder.trace_for("a1") == "-"


def test_unsafe_context_value_is_not_bound(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("x; rm -rf /")
    binder = SubagentTraceBinder(ctx)
    binder.bind("a1")
    assert binder.trace_for("a1") == "-"


def test_on_bash_hook_rewrites_only_subagent_calls(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("t1")
    binder = SubagentTraceBinder(ctx)
    binder.bind("a1")
    out = _run(binder.on_bash(_bash("artmind query x", "a1"), None, None))
    assert out["hookSpecificOutput"]["updatedInput"]["command"] == "ARTMIND_TRACE_ID=t1 artmind query x"
    assert _run(binder.on_bash(_bash("artmind query x"), None, None)) == {}


def test_entry_precedence_explicit_id_over_context_file(tmp_path, monkeypatch):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("turn2")
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    assert _trace_id() == "turn2"
    monkeypatch.setenv("ARTMIND_TRACE_ID", "turn1")
    assert _trace_id() == "turn1"
    monkeypatch.setenv("ARTMIND_TRACE_ID", "-")
    assert _trace_id() is None  # untraced, does not fall back to the file


def test_current_trace_id_in_span_follows_env(tmp_path, monkeypatch):
    from artmind.traces.span import current_trace_id

    ctx = tmp_path / "ctx.id"
    ctx.write_text("turn2")
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    monkeypatch.setenv("ARTMIND_TRACE_ID", "turn1")
    assert current_trace_id() == "turn1"


def test_agent_options_registers_hooks(tmp_path):
    binder = SubagentTraceBinder(tmp_path / "c.id")
    admin = agent_options(ADMIN_PROFILE, trace_binder=binder)
    assert "SubagentStart" in admin.hooks
    assert any(m.matcher == "Bash" for m in admin.hooks["PreToolUse"])
    assert "SubagentStart" not in (agent_options(ADMIN_PROFILE).hooks or {})


def test_gated_profile_stamps_inside_the_gate(tmp_path):
    ctx = tmp_path / "c.id"
    ctx.write_text("t1")
    binder = SubagentTraceBinder(ctx)
    binder.bind("a1")
    opts = agent_options(QA_PROFILE, trace_binder=binder)
    gate = next(m for m in opts.hooks["PreToolUse"] if m.matcher == "Bash").hooks[0]
    out = _run(gate(_bash("artmind query x", "a1"), None, None))
    assert out["hookSpecificOutput"]["updatedInput"]["command"] == "ARTMIND_TRACE_ID=t1 artmind query x"
    assert len([m for m in opts.hooks["PreToolUse"] if m.matcher == "Bash"]) == 1
    denied = _run(gate(_bash("cat /etc/passwd", "a1"), None, None))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
