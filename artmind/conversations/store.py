"""SQLite access for chat conversations (spec 2026-10-16-chat-conversations-design.md §3-§4).

The tables are created with every other registry table in `artmind.db._init_db`.
Every function opens and closes its own connection, the convention the rest of
the registry code follows. `now` parameters exist so tests control ordering.
"""

import json
import sqlite3
import time
from typing import Any

from artmind.db import _get_db
from artmind.traces.store import cap

TITLE_MAX = 60
STATUSES = ("running", "done", "interrupted", "error")


class WrongProfile(Exception):
    """The conversation exists but belongs to the other app (qa vs admin)."""


def _connect() -> sqlite3.Connection:
    conn = _get_db()
    conn.row_factory = sqlite3.Row
    return conn


def _now(now: float | None) -> float:
    return now if now is not None else time.time()


def make_title(text: str) -> str:
    """The conversation's automatic title: first message, one line, <= TITLE_MAX chars."""
    one_line = " ".join(text.split())
    if not one_line:
        return "New conversation"
    if len(one_line) <= TITLE_MAX:
        return one_line
    return one_line[: TITLE_MAX - 1].rstrip() + "…"


def ensure(conversation_id: str, profile: str, first_message: str, *, now: float | None = None) -> dict:
    """Create the conversation if absent; return it. The first message sets the
    title and later calls never change it. Raises WrongProfile if it exists for
    another profile."""
    ts = _now(now)
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (conversation_id, profile, title, title_source, pinned,"
            " archived_at, created_at, updated_at) VALUES (?, ?, ?, 'auto', 0, NULL, ?, ?)",
            (conversation_id, profile, make_title(first_message), ts, ts),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
    finally:
        conn.close()
    if row["profile"] != profile:
        raise WrongProfile(conversation_id)
    return dict(row)


def get(conversation_id: str, profile: str | None = None) -> dict | None:
    """The conversation, or None if absent (or owned by a different `profile`, when given)."""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
    finally:
        conn.close()
    if row is None or (profile is not None and row["profile"] != profile):
        return None
    return dict(row)


def list_conversations(
    profile: str,
    *,
    archived: bool = False,
    limit: int = 50,
    before: tuple[int, float, str] | None = None,
) -> list[dict]:
    """Pinned first, then most recently active. `before` is the (pinned,
    updated_at, conversation_id) of the last row of the previous page (keyset
    pagination)."""
    where = ["c.profile = ?", "c.archived_at IS NOT NULL" if archived else "c.archived_at IS NULL"]
    params: list[Any] = [profile]
    if before is not None:
        where.append(
            "(c.pinned < ? OR (c.pinned = ? AND (c.updated_at < ? OR (c.updated_at = ? AND c.conversation_id < ?))))"
        )
        params += [before[0], before[0], before[1], before[1], before[2]]
    params.append(limit)
    sql = (
        "SELECT c.*, (SELECT COUNT(*) FROM conversation_turns t WHERE t.conversation_id = c.conversation_id)"
        " AS turn_count FROM conversations c WHERE " + " AND ".join(where) +
        " ORDER BY c.pinned DESC, c.updated_at DESC, c.conversation_id DESC LIMIT ?"
    )
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def update(
    conversation_id: str,
    profile: str,
    *,
    title: str | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
    now: float | None = None,
) -> bool:
    """Rename / pin / archive. Does not touch updated_at (list order is by turn
    activity). Returns False if no such conversation for `profile`."""
    sets: list[str] = []
    params: list[Any] = []
    if title is not None:
        title = title.strip()
        if not title:
            raise ValueError("title must not be empty")
        sets += ["title = ?", "title_source = 'user'"]
        params.append(title)
    if pinned is not None:
        sets.append("pinned = ?")
        params.append(1 if pinned else 0)
    if archived is not None:
        sets.append("archived_at = ?")
        params.append(_now(now) if archived else None)
    conn = _connect()
    try:
        if sets:
            cursor = conn.execute(
                f"UPDATE conversations SET {', '.join(sets)} WHERE conversation_id = ? AND profile = ?",
                [*params, conversation_id, profile],
            )
            conn.commit()
            return cursor.rowcount > 0
        return conn.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ? AND profile = ?", (conversation_id, profile)
        ).fetchone() is not None
    finally:
        conn.close()


def delete(conversation_id: str, profile: str) -> bool:
    """Remove the conversation and its turns in one transaction."""
    conn = _connect()
    try:
        owned = conn.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ? AND profile = ?", (conversation_id, profile)
        ).fetchone()
        if owned is None:
            return False
        conn.execute("DELETE FROM conversation_turns WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def begin_turn(conversation_id: str, user_text: str, backend: str, *, now: float | None = None) -> int:
    """Insert the next turn as `running`; return its seq. The caller must `ensure` the conversation first."""
    ts = _now(now)
    conn = _connect()
    try:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM conversation_turns WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO conversation_turns (conversation_id, seq, user_text, blocks, answer_text, backend,"
            " status, started_at) VALUES (?, ?, ?, '[]', NULL, ?, 'running', ?)",
            (conversation_id, seq, cap(user_text), backend, ts),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (ts, conversation_id))
        conn.commit()
        return seq
    finally:
        conn.close()


def finish_turn(
    conversation_id: str,
    seq: int,
    *,
    blocks: list[dict],
    answer_text: str | None,
    status: str,
    error_message: str | None = None,
    trace_id: str | None = None,
    cost_usd: float | None = None,
    duration_s: float | None = None,
    now: float | None = None,
) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    ts = _now(now)
    conn = _connect()
    try:
        conn.execute(
            "UPDATE conversation_turns SET blocks = ?, answer_text = ?, status = ?, error_message = ?,"
            " trace_id = ?, cost_usd = ?, duration_s = ?, completed_at = ?"
            " WHERE conversation_id = ? AND seq = ?",
            (json.dumps(blocks, ensure_ascii=False, default=str), answer_text, status, error_message, trace_id, cost_usd,
             duration_s, ts, conversation_id, seq),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (ts, conversation_id))
        conn.commit()
    finally:
        conn.close()


def list_turns(conversation_id: str, *, with_blocks: bool = True) -> list[dict]:
    """The conversation's turns in order. `with_blocks=False` reads only what
    replay needs (`seq`, `user_text`, `answer_text`, `status`) and returns
    `blocks: []`, skipping the JSON of the stored blocks."""
    columns = "*" if with_blocks else "seq, user_text, answer_text, status"
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT {columns} FROM conversation_turns WHERE conversation_id = ? ORDER BY seq", (conversation_id,)
        ).fetchall()
    finally:
        conn.close()
    turns = []
    for row in rows:
        turn = dict(row)
        if not with_blocks:
            turn["blocks"] = []
        else:
            try:
                turn["blocks"] = json.loads(turn["blocks"])
            except (TypeError, ValueError):
                turn["blocks"] = []
        turns.append(turn)
    return turns


def sweep_running(profile: str, *, now: float | None = None) -> int:
    """Mark leftover `running` turns of `profile` as interrupted (a crash or
    restart). Scoped to one profile: chat-ui and admin-ui are separate processes
    sharing this DB, and starting one must not touch the other's live turns."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE conversation_turns SET status = 'interrupted', error_message = 'interrupted: server restarted',"
            " completed_at = ? WHERE status = 'running' AND conversation_id IN"
            " (SELECT conversation_id FROM conversations WHERE profile = ?)",
            (_now(now), profile),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
