"""Agent configuration and SDK→UI event mapping for the chat web UI.

The chat agent must NOT inherit the repo's developer environment (CLAUDE.md
graphify rules, enforcement hooks): it serves end-user Q&A, not coding. So we
enable only the artmind skills instead of loading all project settings.
"""

import json
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from paths import ARTMIND_HOME

# The chat agent runs from the clean run folder (config + skills + schemas +
# logs) — not the source checkout. Its `.claude/skills/` is discovered via the
# default "project" setting source; the corpus and source tree are not present.
RUN_FOLDER = ARTMIND_HOME
TRACE_CLIP = 600

_SYSTEM_APPEND = """\
You are the artmind assistant, an end-user interface to the artmind knowledge
system. Users ask about knowledge stored in artmind domains. Route their
requests through the artmind skills: artmind-query for questions,
artmind-update for adding facts, artmind-refine for graph maintenance,
artmind-ingestion-helper for ingesting documents. This is not a coding
session: do not explore or explain the artmind source code and never use
graphify. Answer conversationally; no raw JSON or command output unless asked."""


def clip(value: Any, limit: int = TRACE_CLIP) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else f"{text[:limit]} … [{len(text)} chars total]"


def agent_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(RUN_FOLDER),
        skills=[
            "artmind-query",
            "artmind-update",
            "artmind-refine",
            "artmind-ingestion-helper",
        ],
        system_prompt={"type": "preset", "preset": "claude_code", "append": _SYSTEM_APPEND},
        permission_mode="bypassPermissions",
        thinking={"type": "enabled", "budget_tokens": 8000, "display": "summarized"},
        include_partial_messages=True,
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
