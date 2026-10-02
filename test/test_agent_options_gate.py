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

import artmind.webui.agent as agent_module
from artmind.webui.agent import agent_options
from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, QA_PROFILE
from artmind.webui.tool_gate import DENIED_TOOLS

# Read is carved out of the hard-denied set -- it is gated via the PreToolUse
# hook's path predicate instead (see test_the_read_hook_* below). Grep/Glob/
# NotebookRead have no such carve-out and stay hard-denied.
HARD_DENIED_TOOLS = tuple(t for t in DENIED_TOOLS if t != "Read")


def _hook_for(options, tool_name):
    """Pull the single hook callback matched to `tool_name` out of options."""
    matchers = options.hooks["PreToolUse"]
    matched = [m for m in matchers if m.matcher == tool_name]
    assert len(matched) == 1, f"expected exactly one {tool_name}-matched PreToolUse hook"
    (hook,) = matched[0].hooks
    return hook


def _bash_hook(options):
    return _hook_for(options, "Bash")


def _read_hook(options):
    return _hook_for(options, "Read")


def test_a_grounded_profile_denies_the_file_reading_tools():
    options = agent_options(QA_PROFILE)

    assert set(HARD_DENIED_TOOLS) <= set(options.disallowed_tools)
    assert "Read" not in options.disallowed_tools, (
        "Read is gated via the PreToolUse hook's path predicate, not hard-denied"
    )


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
    assert set(HARD_DENIED_TOOLS) <= set(options.disallowed_tools)

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


async def test_the_read_hook_allows_a_file_inside_the_package_skills_root(
    monkeypatch, tmp_path
):
    package_skills = tmp_path / "pkg-skills"
    package_skills.mkdir()
    target = package_skills / "artmind-query" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("# query")
    monkeypatch.setattr(agent_module, "PACKAGE_SKILLS_DIR", package_skills)

    options = agent_options(QA_PROFILE)
    hook = _read_hook(options)
    allowed = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(target)},
            "tool_use_id": "toolu_5",
        },
        "toolu_5",
        {"signal": None},
    )

    assert allowed["hookSpecificOutput"]["permissionDecision"] == "allow"


async def test_the_read_hook_allows_a_file_inside_the_run_folder_skills_root(
    monkeypatch, tmp_path
):
    run_folder = tmp_path / "run-folder"
    target = run_folder / ".claude" / "skills" / "artmind-query" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("# query")
    monkeypatch.setattr(agent_module, "RUN_FOLDER", run_folder)

    options = agent_options(QA_PROFILE)
    hook = _read_hook(options)
    allowed = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(target)},
            "tool_use_id": "toolu_6",
        },
        "toolu_6",
        {"signal": None},
    )

    assert allowed["hookSpecificOutput"]["permissionDecision"] == "allow"


async def test_the_read_hook_denies_a_vault_file_outside_the_skills_roots(tmp_path):
    note = tmp_path / "notes" / "journal.md"
    note.parent.mkdir(parents=True)
    note.write_text("private")

    options = agent_options(QA_PROFILE)
    hook = _read_hook(options)
    denied = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(note)},
            "tool_use_id": "toolu_7",
        },
        "toolu_7",
        {"signal": None},
    )

    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_a_non_read_tool_reaching_the_read_hook_is_allowed():
    """Defense-in-depth, mirroring the Bash hook's own non-Bash check."""
    options = agent_options(QA_PROFILE)
    hook = _read_hook(options)

    result = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__canvas__show_card",
            "tool_input": {"id": "x"},
            "tool_use_id": "toolu_8",
        },
        "toolu_8",
        {"signal": None},
    )

    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


async def test_the_read_hook_really_calls_the_path_predicate(monkeypatch):
    """A hook that reimplements its own path check instead of delegating to
    `tool_gate.is_allowed_skill_read` could silently drift from it -- this
    proves the wiring by swapping the predicate itself and watching the
    hook's decision follow it (the same trap the module docstring warns about
    for `can_use_tool` vs `PreToolUse`: a wrong wiring can pass its own unit
    tests while being inert against the real hook)."""
    monkeypatch.setattr(agent_module, "is_allowed_skill_read", lambda path, roots: True)

    options = agent_options(QA_PROFILE)
    hook = _read_hook(options)
    allowed = await hook(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/definitely/not/a/skill.md"},
            "tool_use_id": "toolu_9",
        },
        "toolu_9",
        {"signal": None},
    )

    assert allowed["hookSpecificOutput"]["permissionDecision"] == "allow"


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
