"""Agent configuration and SDK→UI event mapping for the chat web UI.

The chat agent must NOT inherit the repo's developer environment (CLAUDE.md
graphify rules, enforcement hooks): it serves end-user Q&A, not coding. So we
enable only the artmind skills instead of loading all project settings.
"""

from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from artmind.webui.backends.base import TRACE_CLIP, clip
from artmind.webui.profiles import AgentProfile, QA_PROFILE
from artmind.webui.tool_gate import DENIED_TOOLS, denial_message, is_allowed_bash
from paths import ARTMIND_HOME

# The chat agent runs from the clean run folder (config + skills + schemas +
# logs) — not the source checkout. Its `.claude/skills/` is discovered via the
# default "project" setting source; the corpus and source tree are not present.
RUN_FOLDER = ARTMIND_HOME

__all__ = ["RUN_FOLDER", "TRACE_CLIP", "clip", "agent_options", "EventMapper"]


def agent_options(
    profile: AgentProfile = QA_PROFILE,
    resume: str | None = None,
    *,
    mcp_servers: dict[str, Any] | None = None,
    allowed_tools: list[str] | None = None,
    model: str | None = None,
    fallback_model: str | None = None,
    env: dict[str, str] | None = None,
) -> ClaudeAgentOptions:
    """Build the SDK options for a chat session.

    ``resume`` (A7 / ADR 0007): when set, the SDK resumes that prior session id
    so the conversation context survives a session refresh — the mechanism that
    lets a freshly-authored skill be picked up (skills are read only at
    session start) without losing the thread. ``fork_session`` is left at its
    default (False) so the resumed turn continues the same session id in place
    rather than branching a new one.

    ``mcp_servers`` / ``allowed_tools`` (generic in-process-tool passthrough):
    a front-end app (e.g. the canvas) may register an in-process SDK MCP server
    so the agent can call app-specific tools. Both default to ``None`` — a
    plain chat session adds nothing. ``allowed_tools`` is additive: under
    ``bypassPermissions`` every tool is already permitted, so naming the MCP
    tool here auto-approves it without narrowing the skill toolset.

    ``model`` / ``fallback_model``: left ``None`` by default, which means "the
    ``claude`` CLI's own default" — normally fine, but it can resolve to a
    model alias your account/endpoint doesn't have access to (see
    ``ARTMIND_SDK_MODEL`` in ``backends/__init__.py``), so both are exposed
    for an operator to pin explicitly.

    ``env``: extra env vars merged onto the spawned ``claude`` CLI's inherited
    process env (overriding it key-for-key — see the SDK's subprocess
    transport). Used for ``ARTMIND_SDK_BASE_URL`` (see ``backends/__init__.py``)
    to point the CLI at a custom endpoint without disturbing the process-wide
    ``ANTHROPIC_BASE_URL`` the KG pipeline also reads.

    **The grounding gate** (``docs/vault.md``): the agent's ``cwd`` is now the
    user's vault, so an ungated agent can answer by reading documents directly
    instead of through ``artmind query`` — silently losing supersession,
    materialised conflicts and chunk-level provenance. When
    ``profile.filesystem_access`` is False, this disallows the file-reading
    tools (``artmind.webui.tool_gate.DENIED_TOOLS``) outright and installs a
    ``PreToolUse`` hook, matched to ``Bash`` only, that narrows it to a single
    ``artmind …`` invocation, denying anything else with a message naming the
    query command to use instead. It is a hook and deliberately *not* a
    ``can_use_tool`` callback — see ``tool_gate.py``'s module docstring for
    why. Every other tool — including in-process MCP tools a front-end
    registers via ``mcp_servers`` — passes through untouched; the gate's
    business is Bash and the read tools only. An operator surface sets
    ``filesystem_access=True`` and gets neither restriction (no hooks at all),
    since inspecting a failed conversion or reading a log is legitimately its
    job. See ``tool_gate.py`` for the reasoning behind the predicate itself.

    The ``HookMatcher(matcher="Bash", ...)`` registration is what scopes the
    hook to Bash calls in the first place, so this ought to be the only tool
    the hook ever sees. The callback still checks ``tool_name`` itself and
    allows anything that isn't Bash, purely as defense-in-depth: live,
    authenticated verification that the CLI honours the matcher wasn't
    possible from this checkout's dev sandbox, so the redundant check stays
    rather than assuming.
    """
    denied_tools: list[str] = []
    hooks: dict[str, list[HookMatcher]] = {}
    if not profile.filesystem_access:
        denied_tools = list(DENIED_TOOLS)

        async def gate(input_data, tool_use_id, context):  # noqa: ANN001
            if input_data.get("tool_name") != "Bash":
                # Defense-in-depth only -- see the docstring above.
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                    }
                }
            command = (input_data.get("tool_input") or {}).get("command", "")
            if is_allowed_bash(command):
                decision, reason = "allow", None
            else:
                decision, reason = "deny", denial_message(command)
            output: dict[str, Any] = {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
            }
            if reason is not None:
                output["permissionDecisionReason"] = reason
            return {"hookSpecificOutput": output}

        hooks = {"PreToolUse": [HookMatcher(matcher="Bash", hooks=[gate])]}

    return ClaudeAgentOptions(
        cwd=str(RUN_FOLDER),
        skills=list(profile.skills),
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": profile.system_append,
        },
        env=env or {},
        permission_mode="bypassPermissions",
        thinking={"type": "enabled", "budget_tokens": 8000, "display": "summarized"},
        include_partial_messages=True,
        resume=resume,
        mcp_servers=mcp_servers or {},
        allowed_tools=allowed_tools or [],
        disallowed_tools=denied_tools,
        hooks=hooks or None,
        model=model,
        fallback_model=fallback_model,
    )


class EventMapper:
    """Maps one turn's SDK messages onto UI event dicts.

    Stateful: ``content_block_stop`` events only carry an index, so block
    types are remembered from ``content_block_start``. Create one per turn.
    """

    def __init__(self) -> None:
        self._block_types: dict[int, str] = {}

    def map(self, message: Any) -> list[dict[str, Any]]:
        if isinstance(message, StreamEvent):
            return self._map_stream_event(message)
        if isinstance(message, AssistantMessage):
            # Normal text/thinking content already arrived via stream deltas.
            # A synthetic error response (e.g. auth failure) bypasses streaming
            # entirely, so its text is only ever available here.
            events: list[dict[str, Any]] = []
            if message.error:
                text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
                events.append({"type": "error", "message": text or message.error})
            events.extend(
                {"type": "tool_call", "id": b.id, "name": b.name, "input": clip(b.input)}
                for b in message.content
                if isinstance(b, ToolUseBlock)
            )
            return events
        if isinstance(message, UserMessage):
            content = message.content if isinstance(message.content, list) else []
            return [
                {"type": "tool_result", "tool_id": b.tool_use_id, "content": clip(b.content)}
                for b in content
                if isinstance(b, ToolResultBlock)
            ]
        if isinstance(message, ResultMessage):
            return [
                {
                    "type": "turn_done",
                    "turns": message.num_turns,
                    "duration_s": round(message.duration_ms / 1000, 1),
                    "cost": message.total_cost_usd,
                }
            ]
        return []

    def _map_stream_event(self, message: StreamEvent) -> list[dict[str, Any]]:
        if message.parent_tool_use_id:
            # Inner subagent/tool traffic; the main pane only shows the top level.
            return []
        event = message.event
        kind = event.get("type")
        if kind == "content_block_start":
            index = event.get("index", 0)
            self._block_types[index] = event.get("content_block", {}).get("type", "text")
            return []
        if kind == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                return [{"type": "text_delta", "text": delta.get("text", "")}]
            if delta.get("type") == "thinking_delta":
                return [{"type": "thinking_delta", "text": delta.get("thinking", "")}]
            return []
        if kind == "content_block_stop":
            block = self._block_types.pop(event.get("index", 0), "text")
            if block in ("text", "thinking"):
                return [{"type": "block_done", "block": block}]
            return []
        return []
