"""The previous turn of a conversation, rebuilt from recorded traces (planner v2, spec 2026-11-05 §3.2).

Live, the planner is handed a `Previous` by whoever kept the conversation. Shadow plans and replay have only
the trace store, so this module reads the registry DB the way `artmind.traces.store` does and builds one:
the question of the latest earlier turn in the same session, its intent / route / resolved entities from its
`plan_shadow` event when that was recorded, and an `offer` the assistant made at the end of its answer.

`open_job` is always None here: whether an ingestion job was running is not recorded with a turn, and
guessing it from today's job table would describe the wrong moment.
`documents` come from the earlier turn's top-level spans (a `query run` span first), capped at 20.

Nothing here raises for a missing row; a store failure propagates so callers decide (shadow logs and plans
without a previous turn).
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import json
import logging
import os
import re
import sqlite3

from artmind.query_plan.intents import INTENT_IDS, IntentError, Previous, PreviousEntity, load_previous, parse_previous

logger = logging.getLogger(__name__)

PREVIOUS_CONTEXT_ENV = "ARTMIND_PREVIOUS_CONTEXT"
MAX_DOCUMENTS = 20

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
    prev = previous_from_plan(earlier["question"], plan, earlier.get("answer"))
    documents = turn_documents(earlier["trace_id"], db=db)
    return dataclasses.replace(prev, documents=documents) if documents else prev


def turn_documents(trace_id: str, *, db=None) -> tuple[str, ...]:
    """Document ids the turn's top-level spans returned, `query run` first, deduplicated, at most 20."""
    def run(conn):
        rows = conn.execute("SELECT command, evidence FROM trace_spans WHERE trace_id = ?"
                            " AND parent_span_id IS NULL ORDER BY started_at", (trace_id,)).fetchall()
        ordered = sorted(rows, key=lambda r: 0 if (r["command"] or "").strip().endswith("query run") else 1)
        out: dict[str, None] = {}
        for row in ordered:
            try:
                docs = (json.loads(row["evidence"]) if row["evidence"] else {}).get("documents") or []
            except (TypeError, ValueError, AttributeError):
                continue
            for doc in docs:
                doc_id = doc.get("doc_id") if isinstance(doc, dict) else None
                if isinstance(doc_id, str) and doc_id:
                    out.setdefault(doc_id, None)
        return tuple(list(out)[:MAX_DOCUMENTS])
    return _with_db(db, run)


# --- resolution --------------------------------------------------------------------------

_previous_override: contextvars.ContextVar = contextvars.ContextVar("artmind_previous_override", default=None)


@contextlib.contextmanager
def previous_override(text: str | None):
    """`serve` sets the caller's previous-context text for one request (it never reads its own env)."""
    token = _previous_override.set(text)
    try:
        yield
    finally:
        _previous_override.reset(token)


def _context_previous() -> Previous | None:
    """Source (b): the request's override, else the file named by ARTMIND_PREVIOUS_CONTEXT. Never raises."""
    text = _previous_override.get()
    if text is None:
        path = os.environ.get(PREVIOUS_CONTEXT_ENV)
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            logger.debug("previous context unreadable: %s", exc)
            return None
    if not text.strip():
        return None
    try:
        return parse_previous(text)
    except IntentError as exc:
        logger.debug("previous context ignored: %s", exc)
        return None


def resolve_previous(previous_file: str | None, *, trace_id: str | None = None) -> Previous | None:
    """The previous turn from the first source that gives one: `--previous` (errors raise `IntentError`),
    the previous-context override / file, then the trace store."""
    if previous_file is not None:
        return load_previous(previous_file)
    found = _context_previous()
    if found is not None:
        return found
    if trace_id:
        try:
            return previous_from_trace(trace_id)
        except Exception as exc:  # noqa: BLE001 - a store failure means no previous
            logger.debug("previous from trace failed: %s", exc)
    return None


def context_json(turns: list[dict]) -> str:
    """The text for a previous-context file: the last finished (`done`) turn of a conversation, as the JSON
    `parse_previous` reads; empty when there is none. `turns` are `conv_store.list_turns` rows."""
    done = [t for t in turns if t.get("status") == "done" and t.get("user_text")]
    if not done:
        return ""
    last = done[-1]
    plan = None
    if last.get("trace_id"):
        try:
            plan = shadow_plan(last["trace_id"])
        except Exception as exc:  # noqa: BLE001 - the plan is optional
            logger.debug("previous turn's shadow plan unavailable: %s", exc)
    prev = previous_from_plan(last["user_text"], plan, last.get("answer_text"))
    documents: tuple[str, ...] = ()
    if last.get("trace_id"):
        try:
            documents = turn_documents(last["trace_id"])
        except Exception as exc:  # noqa: BLE001
            logger.debug("previous turn's documents unavailable: %s", exc)
    return json.dumps({
        "intent": prev.intent, "route": prev.route, "question": prev.question, "offer": prev.offer,
        "entities": [{"id": e.id, "name": e.name} for e in prev.entities], "documents": list(documents),
    }, ensure_ascii=False)


def write_context(path, turns: list[dict]) -> None:
    """Write `context_json(turns)` to `path`. Best effort: logs and returns on any failure."""
    try:
        text = context_json(turns)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        logger.exception("could not write the previous-turn context")
