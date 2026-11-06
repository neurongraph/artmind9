"""The previous turn of a conversation, rebuilt from recorded traces (planner v2, spec 2026-11-05 §3.2).

Live, the planner is handed a `Previous` by whoever kept the conversation. Shadow plans and replay have only
the trace store, so this module reads the registry DB the way `artmind.traces.store` does and builds one:
the question of the latest earlier turn in the same session, its intent / route / resolved entities from its
`plan_shadow` event when that was recorded, and an `offer` the assistant made at the end of its answer.

`open_job` is always None here: whether an ingestion job was running is not recorded with a turn, and
guessing it from today's job table would describe the wrong moment.
`documents` stay empty: a turn's documents live in agent spans, not in the plan.

Nothing here raises for a missing row; a store failure propagates so callers decide (shadow logs and plans
without a previous turn).
"""

from __future__ import annotations

import json
import re
import sqlite3

from artmind.query_plan.intents import INTENT_IDS, Previous, PreviousEntity

OFFER_TAIL_CHARS = 400
_OFFER_RE = re.compile(r"\b(shall (i|we)|should i|do you want|would you like|want me to)\b", re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


def extract_offer(answer: str | None) -> str | None:
    """The last question in the answer's final ~400 characters that offers to do something, else None."""
    if not answer:
        return None
    tail = answer.strip()[-OFFER_TAIL_CHARS:]
    for sentence in reversed([s.strip() for s in _SENTENCE_BREAK.split(tail) if s.strip()]):
        if sentence.endswith("?") and _OFFER_RE.search(sentence):
            return sentence
    return None


def _entities(plan: dict) -> tuple[PreviousEntity, ...]:
    out: list[PreviousEntity] = []
    seen: set[str] = set()
    for entity in plan.get("entities") or []:
        candidates = entity.get("candidates") if isinstance(entity, dict) else None
        top = candidates[0] if candidates and isinstance(candidates[0], dict) else None
        if top and isinstance(top.get("_id"), str) and isinstance(top.get("name"), str) and top["_id"] not in seen:
            seen.add(top["_id"])
            out.append(PreviousEntity(top["_id"], top["name"]))
    return tuple(out)


def previous_from_plan(question: str | None, plan: dict | None, answer: str | None = None) -> Previous:
    """A `Previous` from a plan payload (and, if known, the answer the turn gave)."""
    plan = plan or {}
    intent = (plan.get("intent") or {}).get("id")
    route = (plan.get("route") or {}).get("id")
    return Previous(intent=intent if intent in INTENT_IDS else None, route=route if isinstance(route, str) else None,
                    question=(question or "").strip() or None, offer=extract_offer(answer),
                    entities=_entities(plan))


def _connect() -> sqlite3.Connection:
    from artmind.traces import store

    return store._connect()


def _with_db(db, fn):
    conn = db if db is not None else _connect()
    conn.row_factory = sqlite3.Row
    try:
        return fn(conn)
    finally:
        if db is None:
            conn.close()


def resolve_trace_id(trace_id: str, *, db=None) -> str | None:
    """A full trace id from itself or a unique prefix (label sources may carry a short id)."""
    def run(conn):
        rows = conn.execute("SELECT trace_id FROM trace_turns WHERE substr(trace_id, 1, ?) = ? LIMIT 2",
                            (len(trace_id), trace_id)).fetchall()
        exact = [r[0] for r in rows if r[0] == trace_id]
        return exact[0] if exact else (rows[0][0] if len(rows) == 1 else None)
    return _with_db(db, run)


def previous_turn(trace_id: str, *, db=None) -> dict | None:
    """The latest turn of the same session that started before this one (`trace_id`, `question`, `answer`)."""
    def run(conn):
        turn = conn.execute("SELECT session_id, started_at FROM trace_turns WHERE trace_id = ?",
                            (trace_id,)).fetchone()
        if turn is None or not turn["session_id"]:
            return None
        row = conn.execute(
            "SELECT trace_id, question, answer FROM trace_turns WHERE session_id = ? AND trace_id != ?"
            " AND started_at < ? ORDER BY started_at DESC LIMIT 1",
            (turn["session_id"], trace_id, turn["started_at"])).fetchone()
        return dict(row) if row else None
    return _with_db(db, run)


def turn_answer(trace_id: str, *, db=None) -> str | None:
    return _with_db(db, lambda conn: next(iter(
        r[0] for r in conn.execute("SELECT answer FROM trace_turns WHERE trace_id = ?", (trace_id,))), None))


def shadow_plan(trace_id: str, *, db=None) -> dict | None:
    """The plan of a turn's `plan_shadow` event, None when missing (or written late, or not `ok`)."""
    def run(conn):
        rows = conn.execute("SELECT payload FROM trace_events WHERE trace_id = ? AND type = 'plan_shadow'"
                            " ORDER BY seq DESC", (trace_id,)).fetchall()
        for row in rows:
            try:
                plan = (json.loads(row[0]) or {}).get("plan")
            except (TypeError, ValueError):
                continue
            if isinstance(plan, dict):
                return plan
        return None
    return _with_db(db, run)


def previous_from_trace(trace_id: str, *, db=None) -> Previous | None:
    """The previous turn of `trace_id`'s session, or None when it is the session's first turn."""
    earlier = previous_turn(trace_id, db=db)
    if earlier is None:
        return None
    plan = shadow_plan(earlier["trace_id"], db=db)
    return previous_from_plan(earlier["question"], plan, earlier.get("answer"))
