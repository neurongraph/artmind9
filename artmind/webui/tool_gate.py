"""Keeping a grounded agent surface grounded (docs/vault.md).

The chat-UI agent's working directory is the vault, so without this it can
answer by reading documents instead of querying the graph. That loses
supersession, materialised conflicts and chunk-level provenance -- and loses
them *silently*, as a fluent answer that quotes a retired policy.

The threat model is a helpful model taking a shortcut, not an adversary
evading a sandbox. Shell allowlisting is genuinely hard against an attacker;
against a model that merely needs the easy path removed and a signpost to the
right one, a prefix match plus a metacharacter ban is sufficient and
comprehensible.

Deliberately free of SDK imports: the interesting part is a string predicate,
and it should be testable without spawning an agent.

``agent.py`` wires this predicate into a **``PreToolUse`` hook**, not a
``can_use_tool`` callback -- do not "simplify" it back. ``agent_options`` sets
``permission_mode="bypassPermissions"`` because the web UIs cannot prompt a
user for tool approval, and under that mode the SDK auto-approves every tool
call *before* ``can_use_tool`` is ever consulted (see the SDK's own
``CanUseToolShadowedWarning``). A commit once wired this predicate in as
``can_use_tool`` anyway: it looked correct, its unit tests passed (they called
the callback directly), and it was silently inert against a real agent --
every Bash call sailed through ungated. A ``PreToolUse`` hook is the
mechanism the SDK's warning itself names as the fix, and it is the only one
that actually runs under ``bypassPermissions``.
"""
from __future__ import annotations

import re

# Every tool that can read a file. If the SDK gains another, add it here --
# the gate is only as good as this list.
DENIED_TOOLS = ("Read", "Grep", "Glob", "NotebookRead")

# Anything that could chain, redirect, substitute or background a second
# command past the prefix check.
_SHELL_METACHARACTERS = (";", "&", "|", ">", "<", "$(", "`", "\n", "\r")

# `artmind` as a whole word, so `artmindfoo` and `artmind-query` do not pass.
_ARTMIND_INVOCATION = re.compile(r"^artmind(\s|$)")


def is_allowed_bash(command: str) -> bool:
    """May the agent run this shell command?

    Allowed only when it is a single `artmind …` invocation. A prefix check
    alone would pass `artmind query x; cat secrets.md`, so metacharacters are
    refused outright rather than parsed -- refusing a legitimate-but-exotic
    command is a far cheaper mistake than admitting a chained one.
    """
    stripped = (command or "").strip()
    if not stripped:
        return False
    if any(meta in stripped for meta in _SHELL_METACHARACTERS):
        return False
    return bool(_ARTMIND_INVOCATION.match(stripped))


def denial_message(command: str) -> str:
    """Why the command was refused, and what to do instead.

    Pedagogical on purpose: the agent is cooperative, so a denial that names
    the right tool redirects it, while a bare refusal invites another attempt.
    """
    return (
        "Filesystem access is disabled on this surface: answers must be grounded "
        "in the knowledge graph, which carries supersession, conflicts and "
        "per-chunk provenance that reading files does not.\n"
        f"Refused: {command.strip()[:120]}\n"
        "Use `artmind query vector-text --domain <d> \"<question>\"` to search "
        "document text, `artmind query chunks --idList <id>` to read a specific "
        "chunk, or `artmind query graph pattern10 --documentName <name>` for a "
        "whole document."
    )
