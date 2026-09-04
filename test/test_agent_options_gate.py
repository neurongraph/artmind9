"""`agent_options` carries the gate, per profile (docs/vault.md).

The gate is a `PreToolUse` hook, not a `can_use_tool` callback: under
`permission_mode="bypassPermissions"` (required so the web UIs never need to
prompt a user for tool approval) the SDK auto-approves every tool call before
`can_use_tool` is ever consulted -- see `CanUseToolShadowedWarning`. A
`PreToolUse` hook is the mechanism the SDK's own warning names as the fix, so
these tests invoke the hook the way the SDK will: as a `HookCallback`,
`async (input_dict, tool_use_id, context) -> HookJSONOutput`, asserting on
the returned `hookSpecificOutput.permissionDecision`.
"""
from __future__ import annotations

import pytest

from artmind.webui.agent import agent_options
from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, QA_PROFILE
from artmind.webui.tool_gate import DENIED_TOOLS


def _bash_hook(options):
    """Pull the single Bash-matched PreToolUse hook callback out of options."""
    matchers = options.hooks["PreToolUse"]
    bash_matchers = [m for m in matchers if m.matcher == "Bash"]
    assert len(bash_matchers) == 1, "expected exactly one Bash-matched PreToolUse hook"
    (hook,) = bash_matchers[0].hooks
    return hook


def test_a_grounded_profile_denies_the_file_reading_tools():
    options = agent_options(QA_PROFILE)

    assert set(DENIED_TOOLS) <= set(options.disallowed_tools)


def test_a_grounded_profile_gates_bash_via_a_pretooluse_hook():
    options = agent_options(QA_PROFILE)

    assert options.can_use_tool is None, "can_use_tool is inert under bypassPermissions"
    assert options.hooks, "the gate must be registered as a hook"
    assert "PreToolUse" in options.hooks


async def test_the_hook_allows_artmind_and_denies_a_read():
    options = agent_options(QA_PROFILE)
    hook = _bash_hook(options)

    allowed = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "artmind query domains-overview --compact"},
            "tool_use_id": "toolu_1",
        },
        "toolu_1",
        {"signal": None},
    )
    denied = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "cat policies/policy_aml.md"},
            "tool_use_id": "toolu_2",
        },
        "toolu_2",
        {"signal": None},
    )

    allowed_output = allowed["hookSpecificOutput"]
    denied_output = denied["hookSpecificOutput"]
    assert allowed_output["hookEventName"] == "PreToolUse"
    assert allowed_output["permissionDecision"] == "allow"
    assert denied_output["permissionDecision"] == "deny"
    assert "artmind query" in denied_output["permissionDecisionReason"], (
        "the denial must signpost the alternative"
    )


async def test_a_non_bash_tool_reaching_the_hook_is_allowed():
    """`HookMatcher(matcher="Bash", ...)` should mean the SDK never calls this
    hook for anything else, but the callback also checks `tool_name` itself as
    defense-in-depth (see `agent.py`'s docstring) -- exercise that path
    directly, the way the SDK would if the matcher were ever bypassed."""
    options = agent_options(QA_PROFILE)
    hook = _bash_hook(options)

    result = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__canvas__show_card",
            "tool_input": {"id": "x"},
            "tool_use_id": "toolu_4",
        },
        "toolu_4",
        {"signal": None},
    )

    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_an_operator_profile_is_not_gated():
    options = agent_options(ADMIN_PROFILE)

    assert options.can_use_tool is None
    assert not options.hooks, "an operator surface must get no hooks at all"
    assert not set(DENIED_TOOLS) & set(options.disallowed_tools or [])


async def test_the_benchmark_profile_is_gated():
    """Otherwise a benchmark run can grep its way to an answer and every score
    in benchmarking/ becomes meaningless."""
    options = agent_options(BENCHMARK_PROFILE)

    assert options.hooks and "PreToolUse" in options.hooks
    assert set(DENIED_TOOLS) <= set(options.disallowed_tools)

    hook = _bash_hook(options)
    denied = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "cat policies/policy_aml.md"},
            "tool_use_id": "toolu_3",
        },
        "toolu_3",
        {"signal": None},
    )
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_gate_is_not_shadowed_by_the_permission_mode():
    """`can_use_tool` is silently ignored under permission_mode
    'bypassPermissions' -- the SDK auto-approves before consulting it. The web
    UIs cannot prompt for approval, so that mode is required, which makes a
    PreToolUse hook the only mechanism that actually runs. This test exists
    because the unit tests passed against a callback the SDK never invoked."""
    options = agent_options(QA_PROFILE)

    assert options.can_use_tool is None, (
        "can_use_tool is inert under bypassPermissions -- use a PreToolUse hook"
    )
    assert options.hooks, "the gate must be registered as a hook"
