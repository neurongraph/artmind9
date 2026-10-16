"""Turn side of conversation history (spec 2026-10-16 §4).

One `ConversationRecorder` per question -> answer turn, modelled on
`artmind.traces.recorder.TraceRecorder`. `observe(event)` folds a *public*
neutral UI event into ordered blocks in memory (no DB I/O); `finish()` writes
the turn. Nothing here may break a turn: a recorder that fails to start is
disabled and every later call is a no-op, and `observe` swallows its own errors.
"""

import logging
from typing import Any

from artmind.conversations import store
from artmind.traces.store import cap

logger = logging.getLogger(__name__)


class ConversationRecorder:
    def __init__(self, conversation_id: str, seq: int | None) -> None:
        self.conversation_id = conversation_id
        self.seq = seq
        self.enabled = seq is not None
        self._blocks: list[dict] = []
        self._tools: dict[str, dict] = {}
        self._buffers: dict[str, list[str]] = {"text": [], "thinking": []}
        self._answer: list[str] = []
        self._done: dict = {}
        self._finished = False  # a `turn_done` event was seen
        self._error: str | None = None

    @classmethod
    def start(cls, *, conversation_id: str, profile: str, user_text: str, backend: str) -> "ConversationRecorder":
        try:
            store.ensure(conversation_id, profile, user_text)
            seq = store.begin_turn(conversation_id, user_text, backend)
            return cls(conversation_id, seq)
        except Exception:
            logger.exception("conversation recording disabled for this turn")
            return cls(conversation_id, None)

    # ── per event ────────────────────────────────────────────────────────
    def observe(self, event: dict) -> None:
        if not self.enabled:
            return
        try:
            self._record(event)
        except Exception:
            logger.exception("conversation event dropped")

    def _flush(self, kind: str) -> None:
        if self._buffers[kind]:
            self._blocks.append({"type": kind, "md": cap("".join(self._buffers[kind]))})
            self._buffers[kind] = []

    def _record(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "text_delta":
            text = event.get("text") or ""
            self._buffers["text"].append(text)
            self._answer.append(text)
        elif kind == "thinking_delta":
            self._buffers["thinking"].append(event.get("text") or "")
        elif kind == "block_done":
            self._flush("text" if event.get("block") == "text" else "thinking")
        elif kind == "tool_call":
            tool_id = event["id"]
            self._flush("thinking")
            self._flush("text")
            block = {"type": "tool", "id": tool_id, "name": event.get("name"),
                     "input": cap(event.get("input")), "result": None}
            self._blocks.append(block)
            self._tools[tool_id] = block
        elif kind == "tool_call_update":
            block = self._tools.get(event.get("id"))
            if block is not None:
                block["name"] = event.get("name") or block["name"]
                block["input"] = cap(event.get("input"))
        elif kind == "tool_result":
            block = self._tools.get(event.get("tool_id"))
            if block is not None:
                block["result"] = cap(event.get("content"))
            # The stored answer is text emitted AFTER the last tool result, so
            # mid-turn narration is not replayed (same rule as TraceRecorder).
            self._answer = []
        elif kind == "turn_done":
            self._done = event
            self._finished = True
        elif kind == "error":
            self._error = event.get("message") or "error"

    # ── end of turn ──────────────────────────────────────────────────────
    def finish(self, error: str | None = None, trace_id: str | None = None) -> None:
        if not self.enabled:
            return
        try:
            self._flush("thinking")
            self._flush("text")
            message = error or self._error
            if not message and not self._finished:
                message = "interrupted: the stream ended before the turn finished"
            if message and message.startswith("interrupted"):
                status = "interrupted"
            elif message:
                status = "error"
            else:
                status = "done"
            answer = "".join(self._answer) or None
            store.finish_turn(
                self.conversation_id,
                self.seq,
                blocks=self._blocks,
                answer_text=answer,
                status=status,
                error_message=message,
                trace_id=trace_id,
                cost_usd=self._done.get("cost"),
                duration_s=self._done.get("duration_s"),
            )
        except Exception:
            logger.exception("conversation finish failed")
