"""`agent_options` carries the gate, per profile (docs/vault.md)."""
from __future__ import annotations

import pytest

from artmind.webui.agent import agent_options
from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, QA_PROFILE
from artmind.webui.tool_gate import DENIED_TOOLS


def test_a_grounded_profile_denies_the_file_reading_tools():
    options = agent_options(QA_PROFILE)

    assert set(DENIED_TOOLS) <= set(options.disallowed_tools)


def test_a_grounded_profile_gates_bash():
    options = agent_options(QA_PROFILE)

    assert options.can_use_tool is not None


async def test_the_gate_allows_artmind_and_denies_a_read(monkeypatch):
    options = agent_options(QA_PROFILE)

    allowed = await options.can_use_tool(
        "Bash", {"command": "artmind query domains-overview --compact"}, None
    )
    denied = await options.can_use_tool(
        "Bash", {"command": "cat policies/policy_aml.md"}, None
    )

    assert allowed.behavior == "allow"
    assert denied.behavior == "deny"
    assert "artmind query" in denied.message, "the denial must signpost the alternative"


async def test_a_non_bash_tool_is_left_alone(monkeypatch):
    """The gate judges Bash. Anything else the SDK permits is not its business
    -- an in-process MCP tool registered by the canvas must still work."""
    options = agent_options(QA_PROFILE)

    result = await options.can_use_tool("mcp__canvas__show_card", {"id": "x"}, None)

    assert result.behavior == "allow"


def test_an_operator_profile_is_not_gated():
    options = agent_options(ADMIN_PROFILE)

    assert options.can_use_tool is None
    assert not set(DENIED_TOOLS) & set(options.disallowed_tools or [])


def test_the_benchmark_profile_is_gated():
    """Otherwise a benchmark run can grep its way to an answer and every score
    in benchmarking/ becomes meaningless."""
    options = agent_options(BENCHMARK_PROFILE)

    assert options.can_use_tool is not None
    assert set(DENIED_TOOLS) <= set(options.disallowed_tools)
