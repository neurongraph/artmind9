"""Maps ACP ``session/update`` notifications onto UI event dicts.

Stateful: ACP streams untyped chunk sequences with no block-stop marker, so
the mapper tracks which block kind (text/thinking) is open and emits
``block_done`` on transitions and at end of turn — ``app.js`` finalizes
markdown rendering on ``block_done``. Create one per turn.

Unknown update variants (``plan``, ``current_mode_update``, future additions)
are ignored: ACP agents may emit variants newer than this client.
"""

from typing import Any

from artmind.webui.agent import clip
from artmind.webui.backends.base import UIEvent

_CHUNK_KINDS = {
    "agent_message_chunk": ("text", "text_delta"),
    "agent_thought_chunk": ("thinking", "thinking_delta"),
}


def _content_text(content: Any) -> str:
    """Extract plain text from an ACP ContentBlock, clipping non-text shapes."""
    if isinstance(content, dict) and content.get("type") == "text":
        return content.get("text", "")
    return clip(content)


def _tool_output(update: dict[str, Any]) -> str:
    """Best-effort text from a tool_call_update's content or rawOutput."""
    parts = []
    for item in update.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "content":
            parts.append(_content_text(item.get("content")))
        elif isinstance(item, dict) and item.get("type") == "diff":
            parts.append(f"[diff] {item.get('path', '')}")
    if parts:
        return clip("\n".join(parts))
    if "rawOutput" in update:
        return clip(update["rawOutput"])
    return ""


class ACPEventMapper:
    def __init__(self) -> None:
        self._open_block: str | None = None

    def _flush_block(self) -> list[UIEvent]:
        if self._open_block is None:
            return []
        block, self._open_block = self._open_block, None
        return [{"type": "block_done", "block": block}]

    def map_update(self, update: dict[str, Any]) -> list[UIEvent]:
        kind = update.get("sessionUpdate")
        if kind in _CHUNK_KINDS:
            block, event_type = _CHUNK_KINDS[kind]
            events = [] if self._open_block == block else self._flush_block()
            self._open_block = block
            events.append(
                {"type": event_type, "text": _content_text(update.get("content"))}
            )
            return events

        events = self._flush_block()
        if kind == "tool_call":
            events.append(
                {
                    "type": "tool_call",
                    "id": update.get("toolCallId", ""),
                    "name": update.get("title") or update.get("kind") or "tool",
                    "input": clip(update.get("rawInput", {})),
                }
            )
        elif kind == "tool_call_update":
            status = update.get("status")
            if status in ("completed", "failed"):
                content = _tool_output(update)
                if status == "failed":
                    content = f"[failed] {content}".rstrip()
                events.append(
                    {
                        "type": "tool_result",
                        "tool_id": update.get("toolCallId", ""),
                        "content": content,
                    }
                )
        return events

    def finish(self, stop_reason: str, duration_s: float) -> list[UIEvent]:
        """End-of-turn events once the session/prompt response resolves."""
        events = self._flush_block()
        if stop_reason == "refusal":
            events.append({"type": "error", "message": "The agent refused the request."})
        events.append(
            {
                "type": "turn_done",
                "turns": 1,
                "duration_s": round(duration_s, 1),
                "cost": None,
            }
        )
        return events
