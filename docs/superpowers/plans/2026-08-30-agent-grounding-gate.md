# Agent Grounding Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the chat-UI agent from answering questions by reading vault files, so its answers stay grounded in the knowledge graph — with its supersession, conflict and provenance machinery — rather than in whatever `grep` happened to find.

**Architecture:** Two levers on the `claude-sdk` backend only. `disallowed_tools` removes Read/Grep/Glob outright. A `can_use_tool` callback gates Bash to `artmind …` invocations with no shell metacharacters, and denies everything else with a message naming the query command to use instead. Both are attached per **profile**, so the end-user surface is constrained and the operator surface is not.

**Tech Stack:** Python 3.14, `claude-agent-sdk`, pytest, `uv`, `just`.

**Read before starting:** [docs/vault.md](../../vault.md) and `artmind/webui/profiles.py`.

---

## Why this exists

Until the vault model landed, the chat agent's `cwd` was `~/.artmind` — a directory containing skills, schemas and logs, and **no documents**. It could not usefully grep the corpus, so every answer came through `artmind query`.

That barrier was **incidental, not designed**. Nothing enforced it: Bash could always `cat ~/artmind_data/…`; the agent simply had no reason to. Now `cwd` is the vault, so it has both reason and proximity.

The cost of losing it is not a nudge in answer quality. Graph answers carry supersession (`--asOf`), materialised conflicts, and chunk-level provenance. A grepped answer has none of that — and `benchmarking/questions.md` contains questions that turn on exactly this. Q08 asks who can approve £300 compensation *after* a policy revision; the vault holds both `policy_complaints.md` and `policy_complaints_v3.md`, and an agent that greps `policies/` finds both and may confidently quote the retired one. The failure is silent: a fluent, wrong, well-sourced-looking answer.

**The threat model is a helpful model taking a shortcut**, not an adversary evading a sandbox. That is what makes a prefix allowlist sufficient here — the agent is not trying to get around the gate, it just needs the easy path removed and a signpost to the right one.

**Scope:** the `claude-sdk` backend only. The ACP backend spawns `opencode` as a separate process with its own tool handling, so neither lever reaches it; opencode appears to have its own permission mechanism, and that is a separate piece of work.

---

## File Structure

| File | Responsibility |
|---|---|
| `artmind/webui/tool_gate.py` (create) | The gate: which tools are denied, and the Bash command predicate. Pure, no SDK imports, so it is testable without spawning an agent. |
| `artmind/webui/profiles.py` (modify) | `AgentProfile` gains `filesystem_access: bool`. |
| `artmind/webui/agent.py` (modify) | Wire `disallowed_tools` and `can_use_tool` into `agent_options`. |
| `test/test_tool_gate.py` (create) | The predicate, including the evasion shapes. |
| `test/test_agent_options_gate.py` (create) | The options actually carry the gate, per profile. |

`tool_gate.py` is separate from `agent.py` because the interesting logic is a string predicate that deserves direct testing, and `agent.py` is about SDK plumbing.

---

## Task 1: The gate predicate

**Files:**
- Create: `artmind/webui/tool_gate.py`
- Test: `test/test_tool_gate.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_tool_gate.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_tool_gate.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'artmind.webui.tool_gate'`

- [ ] **Step 3: Write the implementation**

Create `artmind/webui/tool_gate.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_tool_gate.py -v`
Expected: PASS, 22 passed

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/tool_gate.py test/test_tool_gate.py
git commit -m "feat(webui): a Bash gate that keeps grounded surfaces grounded"
```

---

## Task 2: Filesystem access becomes a profile property

**Files:**
- Modify: `artmind/webui/profiles.py`
- Test: `test/test_tool_gate.py`

The constraint belongs to the surface, not to artmind globally. The chat UI answers end-user questions, where filesystem access is pure downside. The admin console is an **operator** surface — inspecting a failed conversion, reading a log, checking what actually landed on disk are legitimate operator jobs, and denying them makes the console worse at its purpose.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_tool_gate.py`:

```python
def test_the_qa_surface_has_no_filesystem_access():
    """End-user Q&A: reading files is pure downside."""
    from artmind.webui.profiles import QA_PROFILE

    assert QA_PROFILE.filesystem_access is False


def test_the_benchmark_surface_has_no_filesystem_access():
    """A benchmark that greps its way to an answer silently invalidates every
    score in benchmarking/."""
    from artmind.webui.profiles import BENCHMARK_PROFILE

    assert BENCHMARK_PROFILE.filesystem_access is False


def test_the_admin_surface_keeps_filesystem_access():
    """An operator console must be able to inspect a failed conversion, read a
    log, and see what actually landed on disk."""
    from artmind.webui.profiles import ADMIN_PROFILE

    assert ADMIN_PROFILE.filesystem_access is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_tool_gate.py -v`
Expected: FAIL, `AttributeError: 'AgentProfile' object has no attribute 'filesystem_access'`

- [ ] **Step 3: Implement**

In `artmind/webui/profiles.py`, add the field to `AgentProfile`:

```python
    # Whether this surface may read the vault directly. False means answers
    # must come through `artmind query`, so they carry supersession, conflicts
    # and provenance (docs/vault.md). True is for OPERATOR surfaces, where
    # inspecting a failed conversion or a log is the job.
    filesystem_access: bool = False
```

Defaulting to `False` is deliberate: a new surface should have to *ask* for filesystem access rather than inherit it by omission.

Then set `filesystem_access=True` on `ADMIN_PROFILE` only. Leave `QA_PROFILE` and `BENCHMARK_PROFILE` on the default, and add a short comment on each saying why.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_tool_gate.py -v`
Expected: PASS, 25 passed

Run: `just dev-test`
Expected: all green. `AgentProfile` is a dataclass with existing keyword construction, so a defaulted field is additive.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/profiles.py test/test_tool_gate.py
git commit -m "feat(webui): filesystem access is a property of the surface"
```

---

## Task 3: Wire the gate into the agent options

**Files:**
- Modify: `artmind/webui/agent.py`
- Test: `test/test_agent_options_gate.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_agent_options_gate.py`:

```python
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
```

`pyproject.toml` sets `asyncio_mode = "auto"`, so the `async def` tests above are collected without a decorator and `import pytest` is only needed if you add parametrisation.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_agent_options_gate.py -v`
Expected: FAIL — `disallowed_tools` is empty and `can_use_tool` is None.

- [ ] **Step 3: Implement**

In `artmind/webui/agent.py`, inside `agent_options`, build the gate from the profile and pass it to `ClaudeAgentOptions`:

```python
    from artmind.webui.tool_gate import DENIED_TOOLS, denial_message, is_allowed_bash

    # A surface that must stay grounded gets the file-reading tools removed and
    # Bash narrowed to `artmind …`. An operator surface gets neither
    # (docs/vault.md, and profiles.AgentProfile.filesystem_access).
    gate = None
    denied: list[str] = []
    if not profile.filesystem_access:
        denied = list(DENIED_TOOLS)

        async def gate(tool_name: str, tool_input: dict, context):  # noqa: ANN001
            if tool_name != "Bash":
                return PermissionResultAllow()
            command = (tool_input or {}).get("command", "")
            if is_allowed_bash(command):
                return PermissionResultAllow()
            return PermissionResultDeny(message=denial_message(command))
```

then add to the `ClaudeAgentOptions(...)` call:

```python
        disallowed_tools=denied,
        can_use_tool=gate,
```

Import `PermissionResultAllow` and `PermissionResultDeny` from `claude_agent_sdk` at the top of the module, beside the existing SDK imports.

Extend the `agent_options` docstring with a paragraph explaining the gate and pointing at `tool_gate.py` for the reasoning — the *why* belongs where someone changing this will read it.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_agent_options_gate.py test/test_tool_gate.py -v`
Expected: PASS

Run: `just dev-test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/agent.py test/test_agent_options_gate.py
git commit -m "feat(webui): grounded surfaces cannot read the vault"
```

---

## Task 4: Verify against a live agent

Green tests do not prove the SDK honours the options. This task is manual.

- [ ] **Step 1: Start the chat UI in a real vault**

```bash
just dev-stop-daemons && just dev-install
cd /tmp && rm -rf gate-e2e && mkdir gate-e2e && cd gate-e2e
ARTMIND_NO_PROXY=1 artmind init
mkdir -p policies && cat > policies/secret_policy.md <<'EOF'
# Compensation Policy
The magic approval limit is EXACTLY 4271 pounds.
EOF
ARTMIND_NO_PROXY=1 artmind chat-ui
```

The number is deliberately arbitrary: it appears in **no** knowledge graph, so the only way to produce it is by reading the file.

- [ ] **Step 2: Ask the question that requires reading**

In the chat UI, ask: *"What is the magic approval limit in the compensation policy? Read the file if you need to."*

Expected: the agent **cannot** produce 4271. It should report that it cannot read files, or answer from the (empty) graph. If it produces 4271, the gate is not working — check that `can_use_tool` is actually reaching the SDK and that `disallowed_tools` is populated.

- [ ] **Step 3: Confirm normal queries still work**

Ask: *"What domains are available?"*

Expected: a real answer — the agent runs `artmind query domains-overview` through the gate successfully. If this fails, the allowlist is too tight; check the exact command in the logs at `.artmind/logs/`.

- [ ] **Step 4: Confirm the operator surface is unaffected**

```bash
ARTMIND_NO_PROXY=1 artmind admin-ui
```

Ask the admin console the same file-reading question. Expected: it **can** answer, because an operator surface is not gated.

- [ ] **Step 5: Clean up**

```bash
cd /tmp && rm -rf gate-e2e
```

---

## Notes for whoever writes the next plan

- **The ACP backend is unprotected.** `opencode` runs as a separate process with its own tool handling, so neither `disallowed_tools` nor `can_use_tool` reaches it. opencode appears to have its own permission mechanism; that is the next piece of work, and until it lands the ACP path should be treated as operator-only.
- **`DENIED_TOOLS` is a maintenance surface.** If the SDK gains another file-reading tool, the gate silently narrows. The test asserting the list covers Read/Grep/Glob is a reminder, not a guarantee — there is no way to enumerate the SDK's tools programmatically today.
- **The gate does not stop the agent writing.** Write, Edit and NotebookEdit are still permitted on a grounded surface. That may be wrong for the chat UI, but it is a separate decision from grounding and was left alone deliberately.
