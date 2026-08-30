"""The Bash gate for grounded agent surfaces (docs/vault.md).

The threat model is a helpful model taking a shortcut -- not an adversary
evading a sandbox. So a prefix allowlist plus a metacharacter ban is the right
strength: it removes the easy path and signposts the right one.
"""
from __future__ import annotations

import pytest

from artmind.webui.tool_gate import DENIED_TOOLS, is_allowed_bash


@pytest.mark.parametrize("command", [
    "artmind query vector-text --domain banking 'what is the rate?'",
    "artmind query graph pattern1 --domain fiction --entityClass PERSON",
    "artmind query domains-overview --compact",
    "  artmind query chunks --idList abc123",
])
def test_artmind_commands_are_allowed(command):
    assert is_allowed_bash(command) is True


@pytest.mark.parametrize("command", [
    "cat policies/policy_aml.md",
    "grep -r 'compensation' .",
    "rg compensation",
    "ls -R",
    "head -50 notes/journal.md",
    "find . -name '*.md'",
    "python -c \"print(open('x.md').read())\"",
])
def test_reading_the_vault_directly_is_denied(command):
    assert is_allowed_bash(command) is False


@pytest.mark.parametrize("command", [
    "artmind query domains-overview; cat policies/policy_aml.md",
    "artmind query domains-overview && grep -r x .",
    "artmind query domains-overview | tee /tmp/out",
    "artmind query domains-overview > /tmp/out",
    "artmind $(cat /etc/passwd)",
    "artmind query `whoami`",
    "artmind query x\ncat secrets.md",
    "artmind query x & cat secrets.md",
])
def test_chaining_past_the_prefix_is_denied(command):
    """A prefix match alone would pass every one of these."""
    assert is_allowed_bash(command) is False


def test_a_command_merely_starting_with_the_word_artmind_is_not_enough():
    """`artmindfoo` and `artmind-something` are not `artmind`."""
    assert is_allowed_bash("artmindfoo --help") is False
    assert is_allowed_bash("artmind-query x") is False


def test_an_empty_command_is_denied():
    assert is_allowed_bash("") is False
    assert is_allowed_bash("   ") is False


def test_the_denied_tool_list_covers_every_way_to_read_a_file():
    """If the SDK gains another file-reading tool, this list must grow -- the
    gate is only as good as its coverage."""
    assert {"Read", "Grep", "Glob"} <= set(DENIED_TOOLS)
