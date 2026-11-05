"""Sub-agents keep the trace id of the turn that started them (round-8 P62).

The admin console writes each turn's trace id into one per-session context file
and clears it when the turn ends (`TraceRecorder`). A background sub-agent
keeps calling `artmind query` after that -- with no id (the span is lost) or,
once a later turn has rewritten the file, with the *later* turn's id (wrong
attribution).

Mechanism, chosen over per-turn context files that are never cleared: the
Claude SDK reports every tool call with the `agent_id` of the sub-agent that
made it, and a `SubagentStart` hook fires inside the starting turn. So:

- `SubagentStart` records `agent_id -> the trace id in the context file now`;
- a `PreToolUse` Bash hook prefixes `ARTMIND_TRACE_ID=<id>` onto an
  `artmind ...` command coming from a sub-agent. `_entry._trace_id` honours
  that variable before the context file and forwards it to `serve`.

Per-turn files would need the sub-agent told which file to read (a prompt
convention it can forget); a per-command env prefix is applied by the harness.
A sub-agent that was never bound (its start was not seen, or no turn was open)
gets `ARTMIND_TRACE_ID=-`, meaning "record nothing", so it can never land on a
later turn. The main agent (no `agent_id`) is untouched and keeps the file.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

#: `ARTMIND_TRACE_ID` value meaning "explicitly untraced" (see `_entry._trace_id`).
NO_TRACE = "-"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_AGENTS = 512


class SubagentTraceBinder:
    def __init__(self, context_path: Path | None) -> None:
        self._context_path = Path(context_path) if context_path else None
        self._bound: OrderedDict[str, str] = OrderedDict()

    def _current(self) -> str | None:
        if self._context_path is None:
            return None
        try:
            value = self._context_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value if _SAFE_ID.match(value) else None

    def bind(self, agent_id: str) -> None:
        """Record the open turn's id for a sub-agent that is starting."""
        if not agent_id or agent_id in self._bound:
            return
        current = self._current()
        if current:
            self._bound[agent_id] = current
            while len(self._bound) > _MAX_AGENTS:
                self._bound.popitem(last=False)

    def trace_for(self, agent_id: str) -> str:
        return self._bound.get(agent_id, NO_TRACE)

    def stamp(self, input_data: dict[str, Any], command: str) -> str:
        """`command` with the sub-agent's trace id prefixed; unchanged for the
        main agent or for anything that is not an `artmind` invocation."""
        agent_id = input_data.get("agent_id")
        stripped = (command or "").lstrip()
        if not agent_id or not (stripped == "artmind" or stripped.startswith("artmind ")):
            return command
        return f"ARTMIND_TRACE_ID={self.trace_for(str(agent_id))} {stripped}"

    # ── hook callbacks ──────────────────────────────────────────────────
    async def on_subagent_start(self, input_data, tool_use_id, context):  # noqa: ANN001
        try:
            self.bind(str(input_data.get("agent_id") or ""))
        except Exception:
            pass
        return {}

    async def on_bash(self, input_data, tool_use_id, context):  # noqa: ANN001
        """Standalone Bash hook for surfaces without the grounding gate."""
        tool_input = input_data.get("tool_input") or {}
        command = tool_input.get("command", "")
        stamped = self.stamp(input_data, command) if input_data.get("tool_name") == "Bash" else command
        if stamped == command:
            return {}
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "allow",
            "updatedInput": {**tool_input, "command": stamped},
        }}
