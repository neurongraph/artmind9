"""SQLite access for query traces (spec 2026-10-11 §4).

The tables are created with every other registry table in `artmind.db._init_db`.
Every function opens and closes its own connection, the convention the rest of
the registry code follows.
"""

import json
import sqlite3
import time
import uuid
from typing import Any, Iterable

from artmind.db import _get_db

#: Cap on any one stored payload/prompt/response, in UTF-8 bytes.
PAYLOAD_CAP = 64 * 1024

RATINGS = ("good", "partial", "wrong")

_TURN_UPDATABLE = frozenset({
    "answer", "domains", "model", "turns", "duration_s", "cost_usd", "input_tokens",
    "output_tokens", "graph_state", "retrieval_path", "graph_attempts", "graph_productive",
    "fell_through", "answer_basis", "status", "error_message", "completed_at",
})


def new_trace_id() -> str:
    return uuid.uuid4().hex


def cap(value: Any) -> str | None:
    """`value` as text, at most PAYLOAD_CAP bytes, with a truncation marker."""
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    raw = text.encode("utf-8")
    if len(raw) <= PAYLOAD_CAP:
        return text
    kept = raw[:PAYLOAD_CAP].decode("utf-8", "ignore")
    return f"{kept}…[truncated {len(raw) - PAYLOAD_CAP} bytes]"


def _connect() -> sqlite3.Connection:
    conn = _get_db()
    conn.row_factory = sqlite3.Row
    return conn


def insert_turn(
    trace_id: str,
    *,
    source: str,
    question: str,
    session_id: str | None = None,
    profile: str | None = None,
    backend: str | None = None,
    benchmark_run_id: str | None = None,
    benchmark_seq: int | None = None,
    started_at: float | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO trace_turns (trace_id, source, session_id, benchmark_run_id, benchmark_seq,"
            " question, profile, backend, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
            (trace_id, source, session_id, benchmark_run_id, benchmark_seq, question, profile, backend,
             started_at if started_at is not None else time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def update_turn(trace_id: str, **fields: Any) -> None:
    unknown = set(fields) - _TURN_UPDATABLE
    if unknown:
        raise ValueError(f"not updatable on trace_turns: {sorted(unknown)}")
    if not fields:
        return
    sets = ", ".join(f"{col} = ?" for col in fields)
    conn = _connect()
    try:
        conn.execute(f"UPDATE trace_turns SET {sets} WHERE trace_id = ?", [*fields.values(), trace_id])
        conn.commit()
    finally:
        conn.close()


def insert_events(trace_id: str, events: list[dict]) -> None:
    if not events:
        return
    conn = _connect()
    try:
        conn.executemany(
            "INSERT INTO trace_events (trace_id, seq, ts, type, tool_id, parent_tool_id, name, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(trace_id, e["seq"], e["ts"], e["type"], e.get("tool_id"), e.get("parent_tool_id"),
              e.get("name"), e.get("payload")) for e in events],
        )
        conn.commit()
    finally:
        conn.close()


def insert_span(span: dict, llm_calls: list[dict]) -> None:
    """One span row plus the LLM calls made inside it, in one transaction."""
    now = time.time()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO trace_spans (span_id, trace_id, command, args, exit_code, duration_ms, rows,"
            " rows_total, truncated, evidence, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (span["span_id"], span["trace_id"], span["command"], span["args"], span["exit_code"],
             span["duration_ms"], span["rows"], span["rows_total"], span["truncated"], span["evidence"],
             span["started_at"]),
        )
        conn.executemany(
            "INSERT INTO trace_llm_calls (span_id, seq, call_type, model, prompt, response, duration_ms,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(span["span_id"], c["seq"], c.get("call_type"), c.get("model"), cap(c.get("prompt")),
              cap(c.get("response")), c.get("duration_ms"), now) for c in llm_calls],
        )
        conn.commit()
    finally:
        conn.close()


def set_span_tool_ids(pairs: Iterable[tuple[str, str]]) -> None:
    pairs = list(pairs)
    if not pairs:
        return
    conn = _connect()
    try:
        conn.executemany("UPDATE trace_spans SET tool_id = ? WHERE span_id = ?", [(t, s) for s, t in pairs])
        conn.commit()
    finally:
        conn.close()


def add_feedback(trace_id: str, rating: str, expected: str | None = None) -> None:
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {RATINGS}")
    conn = _connect()
    try:
        if conn.execute("SELECT 1 FROM trace_turns WHERE trace_id = ?", (trace_id,)).fetchone() is None:
            raise KeyError(trace_id)
        conn.execute(
            "INSERT INTO trace_feedback (trace_id, rating, expected, created_at) VALUES (?, ?, ?, ?)",
            (trace_id, rating, expected or None, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def insert_review_stub(review_id: str, trace_id: str, status: str = "open") -> None:
    """Index a review against a turn. P2's dossier writer owns the real rows;
    P1 uses this only in tests and to keep `prune`'s contract checkable."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO trace_reviews (review_id, trace_id, status, updated_at) VALUES (?, ?, ?, ?)",
            (review_id, trace_id, status, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def _turn_dict(row: sqlite3.Row) -> dict:
    turn = dict(row)
    for key in ("domains", "graph_state", "retrieval_path"):
        if isinstance(turn.get(key), str):
            turn[key] = json.loads(turn[key])
    return turn


def get_turn(trace_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM trace_turns WHERE trace_id = ?", (trace_id,)).fetchone()
        return _turn_dict(row) if row else None
    finally:
        conn.close()


def list_events(trace_id: str) -> list[dict]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM trace_events WHERE trace_id = ? ORDER BY seq", (trace_id,))]
    finally:
        conn.close()


def list_spans(trace_id: str) -> list[dict]:
    conn = _connect()
    try:
        spans = [dict(r) for r in conn.execute(
            "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at", (trace_id,))]
    finally:
        conn.close()
    for span in spans:
        span["args"] = json.loads(span["args"]) if span["args"] else None
        span["evidence"] = json.loads(span["evidence"]) if span["evidence"] else {}
    return spans


def list_llm_calls(span_ids: list[str]) -> dict[str, list[dict]]:
    if not span_ids:
        return {}
    marks = ",".join("?" for _ in span_ids)
    conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM trace_llm_calls WHERE span_id IN ({marks}) ORDER BY span_id, seq", span_ids)]
    finally:
        conn.close()
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["span_id"], []).append(row)
    return out


def latest_feedback(trace_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM trace_feedback WHERE trace_id = ? ORDER BY id DESC LIMIT 1", (trace_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_turns(*, source: str | None = None, since: float | None = None, limit: int | None = None) -> list[dict]:
    """Turns newest first, each with its latest `rating` and a `reviewed` flag."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT t.*,"
            " (SELECT f.rating FROM trace_feedback f WHERE f.trace_id = t.trace_id ORDER BY f.id DESC LIMIT 1) AS rating,"
            " (SELECT f.expected FROM trace_feedback f WHERE f.trace_id = t.trace_id ORDER BY f.id DESC LIMIT 1) AS expected,"
            " EXISTS (SELECT 1 FROM trace_reviews r WHERE r.trace_id = t.trace_id) AS reviewed"
            " FROM trace_turns t"
            " WHERE (? IS NULL OR t.source = ?) AND (? IS NULL OR t.started_at >= ?)"
            " ORDER BY t.started_at DESC LIMIT ?",
            (source, source, since, since, limit if limit is not None else -1),
        ).fetchall()
        return [_turn_dict(r) for r in rows]
    finally:
        conn.close()


def prune(retention_days: int) -> int:
    """Delete turns older than `retention_days` that have no feedback and no
    review, with their events, spans and LLM calls. Returns turns deleted."""
    cutoff = time.time() - retention_days * 86400
    conn = _connect()
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT trace_id FROM trace_turns t WHERE t.started_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM trace_feedback f WHERE f.trace_id = t.trace_id)"
            " AND NOT EXISTS (SELECT 1 FROM trace_reviews r WHERE r.trace_id = t.trace_id)",
            (cutoff,),
        )]
        for trace_id in ids:
            conn.execute(
                "DELETE FROM trace_llm_calls WHERE span_id IN (SELECT span_id FROM trace_spans WHERE trace_id = ?)",
                (trace_id,))
            conn.execute("DELETE FROM trace_spans WHERE trace_id = ?", (trace_id,))
            conn.execute("DELETE FROM trace_events WHERE trace_id = ?", (trace_id,))
            conn.execute("DELETE FROM trace_turns WHERE trace_id = ?", (trace_id,))
        conn.commit()
        return len(ids)
    finally:
        conn.close()
