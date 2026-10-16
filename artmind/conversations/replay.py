"""Condensed-transcript replay (spec 2026-10-16 §4.1).

When the session registry must create a fresh agent for a conversation that
already has turns, the chat route prepends this transcript to the prompt. Only
user questions and final answers are replayed (no thinking, no tool calls),
newest first up to a character budget, so the agent regains the thread without
re-reading tool noise. Pure functions; the caller supplies the stored turns.
"""

import os
from dataclasses import dataclass

DEFAULT_BUDGET_CHARS = 24_000

_END = "[End of restored conversation. The new message follows.]"


@dataclass(frozen=True)
class Replay:
    text: str
    included: int
    omitted: int


def budget_chars() -> int:
    raw = os.environ.get("ARTMIND_REPLAY_BUDGET_CHARS")
    try:
        value = int(raw) if raw else DEFAULT_BUDGET_CHARS
    except ValueError:
        return DEFAULT_BUDGET_CHARS
    return value if value > 0 else DEFAULT_BUDGET_CHARS


def _pair(turn: dict) -> str:
    return f"User: {turn['user_text']}\nAssistant: {turn['answer_text']}"


def build_context(turns: list[dict], budget: int | None = None) -> Replay | None:
    """The transcript to prepend, or None when there is nothing to replay."""
    limit = budget if budget is not None else budget_chars()
    replayable = [t for t in turns if t.get("status") == "done" and t.get("answer_text")]
    if not replayable:
        return None

    selected: list[str] = []
    used = 0
    for turn in reversed(replayable):
        pair = _pair(turn)
        cost = len(pair) + 1  # the newline that joins it to its neighbour
        if used + cost > limit:
            if not selected:
                selected.append(pair[: max(limit - 1, 1)] + "…")
            break
        selected.append(pair)
        used += cost
    selected.reverse()

    omitted = len(replayable) - len(selected)
    note = ""
    if omitted:
        note = f" {omitted} earlier turn{'s' if omitted != 1 else ''} omitted."
    header = f"[Restored conversation. The earlier exchange is below; continue it naturally.{note}]"
    return Replay(text="\n".join([header, *selected, _END]), included=len(selected), omitted=omitted)
