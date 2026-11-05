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

import os
import re
from collections.abc import Iterable

# Every tool that can read a file. If the SDK gains another, add it here --
# the gate is only as good as this list.
DENIED_TOOLS = ("Read", "Grep", "Glob", "NotebookRead")

# Anything that could chain, redirect, substitute or background a second
# command past the prefix check.
_SHELL_METACHARACTERS = (";", "&", "|", ">", "<", "$(", "`", "\n", "\r")

# `artmind` as a whole word, so `artmindfoo` and `artmind-query` do not pass.
_ARTMIND_INVOCATION = re.compile(r"^artmind(\s|$)")

# `artmind <sub>` as a command word anywhere: used only on a command already
# refused, to tell "wrapped in `cd ... &&`" from "no artmind in it at all".
_WRAPPED_ARTMIND = re.compile(r"(^|[\s;&|(])artmind\s+\S")


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


_CD_PREFIX = re.compile(r"""^\s*cd\s+("[^"]*"|'[^']*'|[^\s"';&|<>$`]+)\s*&&\s*(.+)$""", re.DOTALL)


def rewrite_cd_command(command: str, allowed_dirs: Iterable[os.PathLike[str] | str],
                       cwd: os.PathLike[str] | str | None = None) -> str | None:
    """Strip a leading ``cd <dir> &&`` from an otherwise allowed command.

    Returns the command without the prefix when ``<dir>`` (expanduser,
    realpath; a relative dir is taken against ``cwd``) is one of
    ``allowed_dirs`` (also realpath'd) and what remains passes
    ``is_allowed_bash`` -- which already refuses any further chaining, so only
    one leading ``cd`` can ever be stripped. Otherwise ``None``.
    """
    match = _CD_PREFIX.match(command or "")
    if not match:
        return None
    target, rest = match.group(1), match.group(2).strip()
    if target[0] in "\"'":
        target = target[1:-1]
    if not target or not is_allowed_bash(rest):
        return None
    target = os.path.expanduser(target)
    if not os.path.isabs(target) and cwd is not None:
        target = os.path.join(os.fspath(cwd), target)
    real = os.path.realpath(target)
    if any(real == os.path.realpath(d) for d in allowed_dirs):
        return rest
    return None


def claude_project_slug(path: os.PathLike[str] | str) -> str:
    """Claude Code's project folder name for a directory: every non-alphanumeric character becomes ``-``."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.fspath(path))


def is_allowed_tool_result_read(path: str, cwd: os.PathLike[str] | str,
                                projects_dir: os.PathLike[str] | str | None = None) -> bool:
    """May the agent ``Read`` this persisted tool result?

    Allowed when the real path is inside
    ``<projects_dir>/<slug of cwd>/<any>/tool-results/`` (default
    ``~/.claude/projects``): the agent's own tool output, not vault content.
    Same realpath segment check as ``is_allowed_skill_read``, so a symlink
    pointing out of the folder, or another project's slug, does not pass.
    """
    if not path:
        return False
    base = os.fspath(projects_dir) if projects_dir is not None else os.path.expanduser("~/.claude/projects")
    real_base = os.path.realpath(base)
    real_path = os.path.realpath(path)
    prefix = os.path.join(real_base, claude_project_slug(os.path.realpath(cwd)))
    if not real_path.startswith(prefix + os.sep):
        return False
    parts = real_path[len(prefix) + 1:].split(os.sep)
    # <session>/tool-results/<file>
    return len(parts) >= 3 and parts[1] == "tool-results" and parts[0] not in ("", "..")


def is_allowed_skill_read(path: str, roots: Iterable[os.PathLike[str] | str]) -> bool:
    """May the agent `Read` this path?

    Allowed only when the *real* path (symlinks resolved) falls inside one of
    the *real* roots. Skill reference files are package assets, not vault
    content, so this is a narrow carve-out, not a reopening of the gate --
    `Grep`/`Glob`/`NotebookRead` stay hard-denied regardless.

    Both sides are realpath'd before comparison: a vault skill directory is a
    symlink into the installed package, and a malicious path could itself be
    a symlink pointing back out. Comparison is by path-segment prefix, not by
    string prefix, so a sibling directory that merely shares a name prefix
    (`skills` vs `skills-evil`) does not pass.
    """
    if not path:
        return False
    real_path = os.path.realpath(path)
    for root in roots:
        real_root = os.path.realpath(root)
        if real_path == real_root or real_path.startswith(real_root + os.sep):
            return True
    return False


def denial_message(command: str) -> str:
    """Why the command was refused, and what to do instead.

    Pedagogical on purpose: the agent is cooperative, so a denial that names
    the right tool redirects it, while a bare refusal invites another attempt.
    """
    stripped = command.strip()
    shape_hint = ""
    if _WRAPPED_ARTMIND.search(stripped):
        # The agent did try to query the graph, but wrapped the command (a `cd`
        # into the vault is the usual one). Naming the fix keeps a weaker model
        # from giving up and answering with a plan instead of a result.
        shape_hint = (
            "That is an artmind command, but it was refused for its shape. Run it again as a "
            "single `artmind ...` command with nothing before or after it: no `cd`, `&&`, `;`, "
            "pipes, redirects or `$(...)`. The working directory is already the vault.\n"
        )
    return (
        "Filesystem access is disabled on this surface: answers must be grounded "
        "in the knowledge graph, which carries supersession, conflicts and "
        "per-chunk provenance that reading files does not.\n"
        f"Refused: {stripped[:120]}\n"
        f"{shape_hint}"
        "Use `artmind query vector-text --shape brief --domain <d> \"<question>\"` to search "
        "document text, `artmind query chunks --idList <id>` to read a specific "
        "chunk, or `artmind query graph pattern10 --documentName <name>` for a "
        "whole document."
    )
