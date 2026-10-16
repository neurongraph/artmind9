# Chat Conversations and History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Durable, server-side chat conversations for chat-ui and admin-ui, stored in the existing SQLite registry DB, listed in a sidebar, reopenable by URL, and continuable by replaying a condensed transcript into a fresh agent.

**Architecture:** Two new tables (`conversations`, `conversation_turns`) in `document_registry.db`, accessed through a new `artmind/conversations/` package (`store`, `recorder`, `replay`) that follows the `artmind/traces/` pattern. `/api/chat` takes a client-generated `conversation_id`, records every turn, and prepends a replay transcript whenever the session registry has to create a fresh agent for a conversation that already has turns. The UI gets a sidebar (`conversations.js`) and the `sessionStorage` history is deleted in favour of `/?c=<id>`.

**Tech Stack:** Python 3.14, FastAPI, SQLite (WAL, per-call connections), pytest (`asyncio_mode = auto`), vanilla JS (marked + DOMPurify already vendored).

**Spec:** `docs/superpowers/specs/2026-10-16-chat-conversations-design.md` (read it first). One refinement to the spec, made while planning: the startup sweep of `running` turns is **scoped to the app's profile** (`sweep_running(profile)`), because chat-ui and admin-ui are separate processes sharing one DB, and starting one must not mark the other's live turns as interrupted. Task 1 updates the spec line.

**Conventions you must follow (from `CLAUDE.md`):**
- Tests go in `test/` (singular). Run **targeted files**, not the whole suite (the full suite stalls on this Mac; see the memory note). Use `uv run --group dev pytest <file> -v`.
- Tests are hermetic: no Neo4j, no network. Every test touching the DB uses the `_isolated_db` autouse fixture shown below (monkeypatches `artmind.db.DB_PATH`).
- Never put private corpus text in tests; use synthetic strings.
- A running `chat-ui` / `admin-ui` process serves stale code. Restart it before manual checks (`just dev-stop-daemons`).
- Commit trailer on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `artmind/db.py` | modify | Create `conversations` and `conversation_turns` tables in `_init_db`. |
| `artmind/conversations/__init__.py` | create | Empty package marker. |
| `artmind/conversations/store.py` | create | All SQL: ensure/get/list/update/delete conversations; begin/finish/list turns; startup sweep. |
| `artmind/conversations/replay.py` | create | `build_context(turns, budget)` -> `Replay(text, included, omitted)`; pure, no DB. |
| `artmind/conversations/recorder.py` | create | `ConversationRecorder`: folds neutral UI events into `blocks`, writes the turn. |
| `artmind/webui/sessions.py` | modify | `get_or_create` returning `(client, fresh)`; `get` delegates. |
| `artmind/webui/app.py` | modify | `ChatRequest.conversation_id`, in-flight set, recorder + replay in `/api/chat`, conversation routes, lifespan sweep. |
| `artmind/webui/templates/index.html`, `admin.html` | modify | Sidebar markup, toggle button, `conversations.js` script tag. |
| `artmind/webui/static/style.css` | modify | Sidebar, rows, menu, turn-status styles. |
| `artmind/webui/static/app.js` | modify | `conversationId`, remove `sessionStorage` history, `context_restored`, hooks, backend switch keeps the conversation. |
| `artmind/webui/static/conversations.js` | create | Sidebar list/filter/menu, `openConversation`, `newConversation`, stored-turn rendering, URL routing. |
| `test/test_conversations_store.py` | create | Store tests. |
| `test/test_conversations_replay.py` | create | Replay tests. |
| `test/test_conversations_recorder.py` | create | Recorder tests. |
| `test/test_conversations_api.py` | create | Route tests with a fake backend. |
| `test/test_webui_sessions.py` | modify | `get_or_create` tests. |
| `test/test_webui_app.py`, `test/test_traces_app.py` | modify | Rename `session_id` -> `conversation_id` in chat posts; isolate DB. |
| `CLAUDE.md` | modify | Repo-layout row for `artmind/conversations/`. |

---

## Task 0: Branch

**Files:** none

- [ ] **Step 1: Create the implementation branch from the spec branch**

The spec and this plan live on `docs/chat-conversations-spec`; branch from it so they travel with the code.

```bash
cd /Users/surjitdas/projects/artmind9
git checkout docs/chat-conversations-spec
git checkout -b feat/chat-conversations
git status --short
```
Expected: no output from `git status --short` (clean tree), branch `feat/chat-conversations`.

---

## Task 1: Schema and store

**Files:**
- Modify: `artmind/db.py` (insert before the `existing = {row[1] for row in cursor.execute("PRAGMA table_info(benchmark_questions)")}` line, near `db.py:466`)
- Create: `artmind/conversations/__init__.py`
- Create: `artmind/conversations/store.py`
- Create: `test/test_conversations_store.py`
- Modify: `docs/superpowers/specs/2026-10-16-chat-conversations-design.md` (sweep scope line)

- [ ] **Step 1: Write the failing store tests**

Create `test/test_conversations_store.py`:

```python
"""Conversation tables and artmind.conversations.store (spec 2026-10-16 §3-§4)."""

import pytest

from artmind.conversations import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _columns(table: str) -> set[str]:
    import artmind.db as db

    conn = db._get_db()
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_tables_exist_with_spec_columns():
    assert {
        "conversation_id", "profile", "title", "title_source", "pinned", "archived_at",
        "created_at", "updated_at",
    } <= _columns("conversations")
    assert {
        "conversation_id", "seq", "user_text", "blocks", "answer_text", "backend", "status",
        "error_message", "trace_id", "cost_usd", "duration_s", "started_at", "completed_at",
    } <= _columns("conversation_turns")


def test_make_title_collapses_whitespace_and_truncates():
    assert store.make_title("  hello\n  world \t!") == "hello world !"
    long = "word " * 40
    title = store.make_title(long)
    assert len(title) == store.TITLE_MAX
    assert title.endswith("…")
    assert store.make_title("   ") == "New conversation"


def test_ensure_creates_once_and_keeps_the_first_title():
    first = store.ensure("c1", "qa", "First question", now=100.0)
    again = store.ensure("c1", "qa", "A different question", now=200.0)
    assert first["title"] == again["title"] == "First question"
    assert again["title_source"] == "auto"
    assert again["created_at"] == 100.0


def test_ensure_rejects_a_conversation_owned_by_another_profile():
    store.ensure("c1", "qa", "Hi")
    with pytest.raises(store.WrongProfile):
        store.ensure("c1", "admin", "Hi")


def test_get_filters_by_profile():
    store.ensure("c1", "qa", "Hi")
    assert store.get("c1")["profile"] == "qa"
    assert store.get("c1", profile="qa") is not None
    assert store.get("c1", profile="admin") is None
    assert store.get("nope") is None


def test_turn_lifecycle_assigns_sequential_seq_and_bumps_updated_at():
    store.ensure("c1", "qa", "Hi", now=100.0)
    seq0 = store.begin_turn("c1", "Hi", "claude-sdk", now=110.0)
    row = store.list_turns("c1")[0]
    assert (seq0, row["status"], row["blocks"], row["answer_text"]) == (0, "running", [], None)
    assert store.get("c1")["updated_at"] == 110.0

    store.finish_turn(
        "c1", seq0, blocks=[{"type": "text", "md": "Hello"}], answer_text="Hello", status="done",
        trace_id="t1", cost_usd=0.02, duration_s=1.5, now=120.0,
    )
    seq1 = store.begin_turn("c1", "Again", "acp", now=130.0)
    assert seq1 == 1
    turns = store.list_turns("c1")
    assert [t["seq"] for t in turns] == [0, 1]
    assert turns[0]["blocks"] == [{"type": "text", "md": "Hello"}]
    assert (turns[0]["answer_text"], turns[0]["status"], turns[0]["trace_id"]) == ("Hello", "done", "t1")
    assert (turns[0]["cost_usd"], turns[0]["duration_s"], turns[0]["completed_at"]) == (0.02, 1.5, 120.0)
    assert turns[1]["backend"] == "acp"
    assert store.get("c1")["updated_at"] == 130.0


def test_finish_turn_rejects_unknown_status():
    store.ensure("c1", "qa", "Hi")
    seq = store.begin_turn("c1", "Hi", "claude-sdk")
    with pytest.raises(ValueError):
        store.finish_turn("c1", seq, blocks=[], answer_text=None, status="bogus")


def test_list_orders_by_activity_and_paginates_with_a_keyset_cursor():
    for i, cid in enumerate(["a", "b", "c"]):
        store.ensure(cid, "qa", f"title {cid}", now=100.0 + i)
    page1 = store.list_conversations("qa", limit=2)
    assert [c["conversation_id"] for c in page1] == ["c", "b"]
    assert page1[0]["turn_count"] == 0
    last = page1[-1]
    page2 = store.list_conversations("qa", limit=2, before=(last["updated_at"], last["conversation_id"]))
    assert [c["conversation_id"] for c in page2] == ["a"]


def test_list_ties_on_updated_at_do_not_skip_or_repeat():
    for cid in ["a", "b", "c"]:
        store.ensure(cid, "qa", cid, now=100.0)
    page1 = store.list_conversations("qa", limit=2)
    last = page1[-1]
    page2 = store.list_conversations("qa", limit=2, before=(last["updated_at"], last["conversation_id"]))
    seen = [c["conversation_id"] for c in page1 + page2]
    assert sorted(seen) == ["a", "b", "c"] and len(seen) == 3


def test_list_filters_profile_and_archived():
    store.ensure("q1", "qa", "q")
    store.ensure("a1", "admin", "a")
    store.ensure("q2", "qa", "q2")
    store.update("q2", "qa", archived=True, now=500.0)
    assert [c["conversation_id"] for c in store.list_conversations("qa")] == ["q1"]
    assert [c["conversation_id"] for c in store.list_conversations("qa", archived=True)] == ["q2"]
    assert [c["conversation_id"] for c in store.list_conversations("admin")] == ["a1"]


def test_update_rename_pin_archive_and_unarchive():
    store.ensure("c1", "qa", "Original", now=100.0)
    assert store.update("c1", "qa", title="  Renamed  ") is True
    row = store.get("c1")
    assert (row["title"], row["title_source"], row["updated_at"]) == ("Renamed", "user", 100.0)
    store.update("c1", "qa", pinned=True)
    assert store.get("c1")["pinned"] == 1
    store.update("c1", "qa", archived=True, now=300.0)
    assert store.get("c1")["archived_at"] == 300.0
    store.update("c1", "qa", archived=False)
    assert store.get("c1")["archived_at"] is None
    assert store.update("c1", "admin", title="x") is False
    assert store.update("nope", "qa", title="x") is False


def test_delete_removes_conversation_and_turns_only_for_its_profile():
    store.ensure("c1", "qa", "Hi")
    store.begin_turn("c1", "Hi", "claude-sdk")
    assert store.delete("c1", "admin") is False
    assert store.get("c1") is not None
    assert store.delete("c1", "qa") is True
    assert store.get("c1") is None
    assert store.list_turns("c1") == []
    assert store.delete("c1", "qa") is False


def test_sweep_running_marks_only_this_profiles_running_turns():
    store.ensure("q", "qa", "q")
    store.ensure("a", "admin", "a")
    store.begin_turn("q", "q", "claude-sdk")
    store.begin_turn("a", "a", "claude-sdk")
    done = store.begin_turn("q", "q2", "claude-sdk")
    store.finish_turn("q", done, blocks=[], answer_text="x", status="done")

    assert store.sweep_running("qa", now=900.0) == 1
    qa_turns = store.list_turns("q")
    assert (qa_turns[0]["status"], qa_turns[0]["completed_at"]) == ("interrupted", 900.0)
    assert qa_turns[0]["error_message"] == "interrupted: server restarted"
    assert qa_turns[1]["status"] == "done"
    assert store.list_turns("a")[0]["status"] == "running"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/surjitdas/projects/artmind9 && uv run --group dev pytest test/test_conversations_store.py -v`
Expected: collection ERROR `ModuleNotFoundError: No module named 'artmind.conversations'`.

- [ ] **Step 3: Add the tables to `_init_db`**

In `artmind/db.py`, immediately before the line `    existing = {row[1] for row in cursor.execute("PRAGMA table_info(benchmark_questions)")}`, insert:

```python
    # Chat conversations (spec 2026-10-16-chat-conversations-design.md §3): the
    # durable history behind the chat-ui / admin-ui sidebar. Local to this
    # machine like the rest of this DB -- never synced, never committed -- since
    # a turn can quote private corpus content. Timestamps are epoch seconds.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            profile         TEXT NOT NULL,
            title           TEXT NOT NULL,
            title_source    TEXT NOT NULL DEFAULT 'auto',
            pinned          INTEGER NOT NULL DEFAULT 0,
            archived_at     REAL,
            created_at      REAL NOT NULL,
            updated_at      REAL NOT NULL
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_profile_updated"
        " ON conversations(profile, updated_at)"
    )
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_turns (
            conversation_id TEXT NOT NULL,
            seq             INTEGER NOT NULL,
            user_text       TEXT NOT NULL,
            blocks          TEXT NOT NULL,
            answer_text     TEXT,
            backend         TEXT NOT NULL,
            status          TEXT NOT NULL,
            error_message   TEXT,
            trace_id        TEXT,
            cost_usd        REAL,
            duration_s      REAL,
            started_at      REAL NOT NULL,
            completed_at    REAL,
            PRIMARY KEY (conversation_id, seq)
        )
    """)
```

- [ ] **Step 4: Create the package and store**

Create an empty `artmind/conversations/__init__.py`:

```bash
mkdir -p /Users/surjitdas/projects/artmind9/artmind/conversations && : > /Users/surjitdas/projects/artmind9/artmind/conversations/__init__.py
```

Create `artmind/conversations/store.py`:

```python
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
    before: tuple[float, str] | None = None,
) -> list[dict]:
    """Most recently active first. `before` is the (updated_at, conversation_id)
    of the last row of the previous page (keyset pagination)."""
    where = ["c.profile = ?", "c.archived_at IS NOT NULL" if archived else "c.archived_at IS NULL"]
    params: list[Any] = [profile]
    if before is not None:
        where.append("(c.updated_at < ? OR (c.updated_at = ? AND c.conversation_id < ?))")
        params += [before[0], before[0], before[1]]
    params.append(limit)
    sql = (
        "SELECT c.*, (SELECT COUNT(*) FROM conversation_turns t WHERE t.conversation_id = c.conversation_id)"
        " AS turn_count FROM conversations c WHERE " + " AND ".join(where) +
        " ORDER BY c.updated_at DESC, c.conversation_id DESC LIMIT ?"
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
        sets += ["title = ?", "title_source = 'user'"]
        params.append(title.strip())
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
    """Insert the next turn as `running`; return its seq."""
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
            (json.dumps(blocks, ensure_ascii=False), answer_text, status, error_message, trace_id, cost_usd,
             duration_s, ts, conversation_id, seq),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?", (ts, conversation_id))
        conn.commit()
    finally:
        conn.close()


def list_turns(conversation_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conversation_turns WHERE conversation_id = ? ORDER BY seq", (conversation_id,)
        ).fetchall()
    finally:
        conn.close()
    turns = []
    for row in rows:
        turn = dict(row)
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
```

- [ ] **Step 5: Update the spec's sweep line**

In `docs/superpowers/specs/2026-10-16-chat-conversations-design.md`, replace:

```
- `create_app` lifespan calls `store.sweep_running()` once at startup: any
  `running` turn becomes `interrupted` (a server crash or restart).
```
with:
```
- `create_app` lifespan calls `store.sweep_running(profile)` once at startup:
  any `running` turn of this app's profile becomes `interrupted` (a server crash
  or restart). Scoped to the profile because chat-ui and admin-ui are separate
  processes sharing one DB; starting one must not touch the other's live turns.
```
Also in section 3's `store.py` bullet in section 4, change `sweep_running` to `sweep_running(profile)`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_conversations_store.py -v`
Expected: 13 passed.

- [ ] **Step 7: Commit**

```bash
git add artmind/db.py artmind/conversations test/test_conversations_store.py docs/superpowers/specs/2026-10-16-chat-conversations-design.md
git commit -m "feat(conversations): tables and store for chat history

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 2: Replay builder

**Files:**
- Create: `artmind/conversations/replay.py`
- Create: `test/test_conversations_replay.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_conversations_replay.py`:

```python
"""Replay transcript builder (spec 2026-10-16 §4.1). Pure: no DB."""

from artmind.conversations import replay


def _turn(seq, q, a, status="done"):
    return {"seq": seq, "user_text": q, "answer_text": a, "status": status}


def test_no_replayable_turns_returns_none():
    assert replay.build_context([]) is None
    assert replay.build_context([_turn(0, "q", None), _turn(1, "q", "a", status="error")]) is None
    assert replay.build_context([_turn(0, "q", "a", status="interrupted")]) is None


def test_pairs_are_emitted_oldest_first_inside_the_markers():
    result = replay.build_context([_turn(0, "q1", "a1"), _turn(1, "q2", "a2")], budget=10_000)
    assert (result.included, result.omitted) == (2, 0)
    lines = result.text.splitlines()
    assert lines[0].startswith("[Restored conversation.")
    assert "omitted" not in lines[0]
    assert lines[1:3] == ["User: q1", "Assistant: a1"]
    assert lines[3:5] == ["User: q2", "Assistant: a2"]
    assert lines[-1] == "[End of restored conversation. The new message follows.]"


def test_budget_drops_the_oldest_turns_and_says_so():
    turns = [_turn(i, f"question {i}", "x" * 100) for i in range(5)]
    one_pair = len("User: question 0\nAssistant: ") + 100 + 2
    result = replay.build_context(turns, budget=one_pair * 2 + 5)
    assert (result.included, result.omitted) == (2, 3)
    assert "User: question 3" in result.text and "User: question 4" in result.text
    assert "User: question 2" not in result.text
    assert "3 earlier turns omitted" in result.text.splitlines()[0]


def test_single_omitted_turn_uses_singular_wording():
    turns = [_turn(0, "old", "x" * 50), _turn(1, "new", "y" * 50)]
    result = replay.build_context(turns, budget=len("User: new\nAssistant: ") + 50 + 5)
    assert result.omitted == 1
    assert "1 earlier turn omitted" in result.text.splitlines()[0]


def test_an_oversized_newest_turn_is_truncated_not_skipped():
    result = replay.build_context([_turn(0, "q", "z" * 5_000)], budget=200)
    assert result.included == 1
    body = result.text.split("\n", 1)[1]
    assert "User: q" in body
    assert "…" in body
    assert len(result.text) < 500


def test_non_replayable_turns_are_ignored_and_not_counted_as_omitted():
    turns = [_turn(0, "q0", "a0"), _turn(1, "q1", None, status="error"), _turn(2, "q2", "a2")]
    result = replay.build_context(turns, budget=10_000)
    assert (result.included, result.omitted) == (2, 0)
    assert "q1" not in result.text


def test_budget_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("ARTMIND_REPLAY_BUDGET_CHARS", raising=False)
    assert replay.budget_chars() == replay.DEFAULT_BUDGET_CHARS
    monkeypatch.setenv("ARTMIND_REPLAY_BUDGET_CHARS", "500")
    assert replay.budget_chars() == 500
    monkeypatch.setenv("ARTMIND_REPLAY_BUDGET_CHARS", "not-a-number")
    assert replay.budget_chars() == replay.DEFAULT_BUDGET_CHARS
    monkeypatch.setenv("ARTMIND_REPLAY_BUDGET_CHARS", "-5")
    assert replay.budget_chars() == replay.DEFAULT_BUDGET_CHARS
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_conversations_replay.py -v`
Expected: collection ERROR `cannot import name 'replay'`.

- [ ] **Step 3: Implement**

Create `artmind/conversations/replay.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_conversations_replay.py -v`
Expected: 7 passed.

If `test_budget_drops_the_oldest_turns_and_says_so` fails on counts, recompute: each pair costs `len(pair) + 1`; the test's budget (`one_pair * 2 + 5` where `one_pair` already includes +2) must fit exactly two pairs and not three. Do not loosen the assertions; adjust the arithmetic in the test only if the implementation's cost model is what the spec says (pair length + 1).

- [ ] **Step 5: Commit**

```bash
git add artmind/conversations/replay.py test/test_conversations_replay.py
git commit -m "feat(conversations): replay transcript builder

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 3: ConversationRecorder

**Files:**
- Create: `artmind/conversations/recorder.py`
- Create: `test/test_conversations_recorder.py`

The recorder consumes the **public** neutral events (what the browser sees), so it needs no `_trace` handling. Event shapes (from `artmind/webui/agent.py` and `app.js`): `text_delta {text}`, `thinking_delta {text}`, `block_done {block: "text"|"thinking"}`, `tool_call {id, name, input}`, `tool_call_update {id, name, input}`, `tool_result {tool_id, content}`, `turn_done {turns, duration_s, cost}`, `error {message}`.

- [ ] **Step 1: Write the failing tests**

Create `test/test_conversations_recorder.py`:

```python
"""ConversationRecorder: folds neutral UI events into stored blocks."""

import pytest

from artmind.conversations import store
from artmind.conversations.recorder import ConversationRecorder
from artmind.traces.store import PAYLOAD_CAP


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _start(cid="c1", user_text="Who?", backend="claude-sdk"):
    return ConversationRecorder.start(conversation_id=cid, profile="qa", user_text=user_text, backend=backend)


def _feed(rec, events):
    for event in events:
        rec.observe(event)


def _only_turn(cid="c1"):
    turns = store.list_turns(cid)
    assert len(turns) == 1
    return turns[0]


def test_start_creates_conversation_and_running_turn():
    rec = _start(user_text="  Who runs   Atlas?  ")
    assert rec.enabled
    assert store.get("c1")["title"] == "Who runs Atlas?"
    turn = _only_turn()
    assert (turn["status"], turn["user_text"], turn["backend"]) == ("running", "  Who runs   Atlas?  ", "claude-sdk")


def test_plain_answer_becomes_one_text_block_with_cost_and_duration():
    rec = _start()
    _feed(rec, [
        {"type": "text_delta", "text": "Hel"},
        {"type": "text_delta", "text": "lo"},
        {"type": "block_done", "block": "text"},
        {"type": "turn_done", "turns": 1, "duration_s": 2.5, "cost": 0.02},
    ])
    rec.finish()
    turn = _only_turn()
    assert turn["blocks"] == [{"type": "text", "md": "Hello"}]
    assert (turn["answer_text"], turn["status"], turn["cost_usd"], turn["duration_s"]) == ("Hello", "done", 0.02, 2.5)


def test_thinking_precedes_text_and_is_not_part_of_the_answer():
    rec = _start()
    _feed(rec, [
        {"type": "thinking_delta", "text": "hmm"},
        {"type": "block_done", "block": "thinking"},
        {"type": "text_delta", "text": "Answer"},
        {"type": "block_done", "block": "text"},
    ])
    rec.finish()
    turn = _only_turn()
    assert [b["type"] for b in turn["blocks"]] == ["thinking", "text"]
    assert turn["blocks"][0]["md"] == "hmm"
    assert turn["answer_text"] == "Answer"


def test_tool_call_update_and_result_fill_one_tool_block_in_order():
    rec = _start()
    _feed(rec, [
        {"type": "text_delta", "text": "Looking."},
        {"type": "block_done", "block": "text"},
        {"type": "tool_call", "id": "t1", "name": "Bash", "input": "artmind query"},
        {"type": "tool_call_update", "id": "t1", "name": "Bash", "input": "artmind query chunks"},
        {"type": "tool_result", "tool_id": "t1", "content": "3 rows"},
        {"type": "text_delta", "text": "Found it."},
        {"type": "block_done", "block": "text"},
    ])
    rec.finish()
    turn = _only_turn()
    assert turn["blocks"] == [
        {"type": "text", "md": "Looking."},
        {"type": "tool", "id": "t1", "name": "Bash", "input": "artmind query chunks", "result": "3 rows"},
        {"type": "text", "md": "Found it."},
    ]
    # narration before the last tool result is not the answer
    assert turn["answer_text"] == "Found it."


def test_unflushed_text_before_a_tool_call_keeps_its_place():
    rec = _start()
    _feed(rec, [
        {"type": "text_delta", "text": "Checking"},
        {"type": "tool_call", "id": "t1", "name": "Bash", "input": "x"},
        {"type": "tool_result", "tool_id": "t1", "content": "ok"},
        {"type": "text_delta", "text": "Done"},
    ])
    rec.finish()
    blocks = _only_turn()["blocks"]
    assert [b["type"] for b in blocks] == ["text", "tool", "text"]
    assert blocks[0]["md"] == "Checking" and blocks[2]["md"] == "Done"


def test_tool_result_for_an_unknown_tool_is_ignored():
    rec = _start()
    _feed(rec, [{"type": "tool_result", "tool_id": "ghost", "content": "x"}, {"type": "text_delta", "text": "A"}])
    rec.finish()
    turn = _only_turn()
    assert [b["type"] for b in turn["blocks"]] == ["text"]


def test_an_error_event_marks_the_turn_error():
    rec = _start()
    _feed(rec, [{"type": "error", "message": "model overloaded"}])
    rec.finish()
    turn = _only_turn()
    assert (turn["status"], turn["error_message"]) == ("error", "model overloaded")


def test_finish_with_an_interrupted_error_is_interrupted_and_keeps_partial_text():
    rec = _start()
    _feed(rec, [{"type": "text_delta", "text": "Partial"}])
    rec.finish(error="interrupted: the client disconnected")
    turn = _only_turn()
    assert (turn["status"], turn["answer_text"]) == ("interrupted", "Partial")
    assert turn["blocks"] == [{"type": "text", "md": "Partial"}]


def test_finish_with_another_error_is_error_and_records_the_trace_id():
    rec = _start()
    rec.finish(error="boom", trace_id="trace-9")
    turn = _only_turn()
    assert (turn["status"], turn["error_message"], turn["trace_id"]) == ("error", "boom", "trace-9")


def test_long_tool_payloads_are_capped():
    rec = _start()
    big = "x" * (PAYLOAD_CAP + 1000)
    _feed(rec, [
        {"type": "tool_call", "id": "t1", "name": "Bash", "input": "i"},
        {"type": "tool_result", "tool_id": "t1", "content": big},
    ])
    rec.finish()
    result = _only_turn()["blocks"][0]["result"]
    assert len(result.encode()) < len(big.encode())
    assert "truncated" in result


def test_start_failure_yields_a_disabled_recorder_that_never_raises(monkeypatch):
    monkeypatch.setattr(store, "begin_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    rec = _start()
    assert not rec.enabled
    rec.observe({"type": "text_delta", "text": "a"})
    rec.finish(error="x")  # no-op, no raise


def test_observe_swallows_malformed_events():
    rec = _start()
    rec.observe({"type": "tool_call"})  # no id
    rec.observe({"type": "text_delta"})  # no text
    rec.finish()
    assert _only_turn()["status"] == "done"


def test_wrong_profile_disables_the_recorder():
    store.ensure("c1", "admin", "Hi")
    rec = ConversationRecorder.start(conversation_id="c1", profile="qa", user_text="Hi", backend="claude-sdk")
    assert not rec.enabled
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_conversations_recorder.py -v`
Expected: collection ERROR `No module named 'artmind.conversations.recorder'`.

- [ ] **Step 3: Implement**

Create `artmind/conversations/recorder.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_conversations_recorder.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add artmind/conversations/recorder.py test/test_conversations_recorder.py
git commit -m "feat(conversations): recorder folds UI events into stored turns

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 4: Registry reports fresh clients

**Files:**
- Modify: `artmind/webui/sessions.py:29-55` (the `get` method)
- Modify: `test/test_webui_sessions.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_webui_sessions.py`:

```python
async def test_get_or_create_reports_fresh_only_when_it_creates():
    registry = SessionRegistry(client_factory=FakeClient)
    first, fresh_first = await registry.get_or_create("tab-1")
    second, fresh_second = await registry.get_or_create("tab-1")
    assert first is second
    assert (fresh_first, fresh_second) == (True, False)


async def test_get_or_create_is_fresh_again_after_the_session_was_dropped():
    registry = SessionRegistry(client_factory=FakeClient)
    await registry.get_or_create("tab-1")
    await registry.drop("tab-1")
    _, fresh = await registry.get_or_create("tab-1")
    assert fresh is True


async def test_get_or_create_is_fresh_when_the_backend_is_replaced():
    registry = SessionRegistry(client_factory=FakeClient)
    old, _ = await registry.get_or_create("tab-1", "claude-sdk")
    new, fresh = await registry.get_or_create("tab-1", "acp")
    assert fresh is True
    assert new is not old and new.backend == "acp"
    assert old.connected is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_webui_sessions.py -v -k get_or_create`
Expected: FAIL `AttributeError: 'SessionRegistry' object has no attribute 'get_or_create'`.

- [ ] **Step 3: Implement**

In `artmind/webui/sessions.py`, replace the whole `get` method (from `async def get(self, session_id: str, backend: str = DEFAULT_BACKEND) -> Any:` through its final `return client`) with:

```python
    async def get_or_create(self, session_id: str, backend: str = DEFAULT_BACKEND) -> tuple[Any, bool]:
        """The live client for ``session_id`` and whether it is *fresh*.

        ``fresh`` is True when this call created the client -- including when it
        replaced one whose backend differed -- i.e. whenever the agent has no
        memory of the conversation. The chat route uses it to decide whether to
        replay a stored transcript (spec 2026-10-16 §4.1).
        """
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                client, entry_backend, _ = entry
                if entry_backend == backend:
                    self._sessions[session_id] = (client, backend, time.monotonic())
                    return client, False
                # A backend switch inside one conversation: replace the session
                # rather than answering with the wrong agent. The caller replays
                # the stored transcript into the new one.
                self._sessions.pop(session_id)
                await client.disconnect()

            client = self._client_factory(backend)
            try:
                await client.connect()
            except BaseException:
                disconnect = getattr(client, "disconnect", None)
                if disconnect is not None:
                    try:
                        await disconnect()
                    except Exception:
                        pass
                raise
            self._sessions[session_id] = (client, backend, time.monotonic())
            return client, True

    async def get(self, session_id: str, backend: str = DEFAULT_BACKEND) -> Any:
        client, _ = await self.get_or_create(session_id, backend)
        return client
```

Also update the class docstring's first line sentence "One lazily-connected agent backend per browser tab" to "One lazily-connected agent backend per conversation" and the module docstring to "Per-conversation live agent sessions for the chat web UI."

- [ ] **Step 4: Run to verify pass (new and old)**

Run: `uv run --group dev pytest test/test_webui_sessions.py -v`
Expected: all pass (existing tests still use `get`).

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/sessions.py test/test_webui_sessions.py
git commit -m "feat(webui): registry reports whether a client was freshly created

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 5: `/api/chat` records turns, replays context, rejects concurrent turns

**Files:**
- Modify: `artmind/webui/app.py` (imports, `ChatRequest`, `create_app` body: `/api/chat`)
- Modify: `test/test_webui_app.py`, `test/test_traces_app.py` (rename field, isolate DB)
- Create: `test/test_conversations_api.py`

- [ ] **Step 1: Migrate the existing chat tests to the new field name**

Run:

```bash
cd /Users/surjitdas/projects/artmind9
sed -i '' 's/"session_id": "/"conversation_id": "/g' test/test_webui_app.py test/test_traces_app.py
sed -i '' 's/ChatRequest(session_id=/ChatRequest(conversation_id=/' test/test_traces_app.py
grep -n 'session_id' test/test_webui_app.py test/test_traces_app.py
```
Expected remaining `session_id` hits: only `turn["session_id"]` in `test/test_traces_app.py` (the trace-turn column, which stays; it will now hold the conversation id).

Add the DB-isolation fixture to `test/test_webui_app.py`, directly after the imports (after `from artmind.webui.sessions import SessionRegistry`):

```python


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    # Chat turns are recorded in the registry DB. Without this, conversation
    # "tab-1" would leak history from one test into the next and trigger replay.
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
```
(`test/test_traces_app.py` already has this fixture.)

Run: `uv run --group dev pytest test/test_webui_app.py test/test_traces_app.py -v`
Expected: FAIL: `ChatRequest` has no `conversation_id` (422 responses / validation errors). This is the red state.

- [ ] **Step 2: Write the new failing API tests (chat part)**

Create `test/test_conversations_api.py`:

```python
"""Conversation recording, replay and routes through the FastAPI app (spec 2026-10-16 §4)."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from artmind.conversations import store
from artmind.webui.app import ChatRequest, create_app
from artmind.webui.sessions import SessionRegistry


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


class FakeBackend:
    instances: list["FakeBackend"] = []

    def __init__(self, backend: str = "claude-sdk"):
        self.backend = backend
        self.prompts: list[str] = []
        FakeBackend.instances.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def interrupt(self):
        pass

    async def receive_events(self):
        yield {"type": "text_delta", "text": f"Answer {len(self.prompts)}"}
        yield {"type": "block_done", "block": "text"}
        yield {"type": "turn_done", "turns": 1, "duration_s": 1.0, "cost": 0.01}


def _app(**kwargs):
    FakeBackend.instances = []
    registry = SessionRegistry(client_factory=FakeBackend)
    return create_app(registry=registry, **kwargs), registry


def _sse(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def _chat(client, cid, prompt, backend="claude-sdk"):
    return client.post("/api/chat", json={"conversation_id": cid, "prompt": prompt, "backend": backend})


def test_chat_records_the_conversation_and_turn():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "Who runs Atlas?")

    listing = client.get("/api/conversations").json()
    assert [c["conversation_id"] for c in listing["conversations"]] == ["c1"]
    assert listing["conversations"][0]["title"] == "Who runs Atlas?"
    assert listing["next_cursor"] is None

    detail = client.get("/api/conversations/c1").json()
    turn = detail["turns"][0]
    assert turn["user_text"] == "Who runs Atlas?"
    assert turn["blocks"] == [{"type": "text", "md": "Answer 1"}]
    assert (turn["answer_text"], turn["status"], turn["backend"], turn["cost_usd"]) == (
        "Answer 1", "done", "claude-sdk", 0.01)


def test_a_live_client_gets_the_bare_prompt_and_no_replay():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "one")
    events = _sse(_chat(client, "c1", "two").text)
    assert FakeBackend.instances[0].prompts == ["one", "two"]
    assert "context_restored" not in [e["type"] for e in events]


def test_a_fresh_client_with_history_gets_the_transcript_prepended():
    app, _ = _app()
    _chat(TestClient(app), "c1", "first question")

    # A new app == a new process / swept session: same DB, no live agent.
    app2, _ = _app()
    events = _sse(_chat(TestClient(app2), "c1", "second question").text)

    assert events[0] == {"type": "context_restored", "turns": 1, "omitted": 0}
    prompt = FakeBackend.instances[0].prompts[0]
    assert prompt.startswith("[Restored conversation.")
    assert "User: first question\nAssistant: Answer 1" in prompt
    assert prompt.endswith("\n\nsecond question")
    # what is stored is the user's text, never the augmented prompt
    turns = store.list_turns("c1")
    assert [t["user_text"] for t in turns] == ["first question", "second question"]


def test_a_first_turn_has_no_replay():
    app, _ = _app()
    events = _sse(_chat(TestClient(app), "c1", "hello").text)
    assert FakeBackend.instances[0].prompts == ["hello"]
    assert "context_restored" not in [e["type"] for e in events]


def test_switching_backend_replays_into_the_new_backend_and_records_it():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "q1", backend="claude-sdk")
    events = _sse(_chat(client, "c1", "q2", backend="acp").text)

    assert events[0]["type"] == "context_restored"
    assert [b.backend for b in FakeBackend.instances] == ["claude-sdk", "acp"]
    assert "User: q1\nAssistant: Answer 1" in FakeBackend.instances[1].prompts[0]
    assert [t["backend"] for t in store.list_turns("c1")] == ["claude-sdk", "acp"]


def test_interrupted_and_failed_turns_are_not_replayed():
    store.ensure("c1", "qa", "q0")
    seq = store.begin_turn("c1", "q0", "claude-sdk")
    store.finish_turn("c1", seq, blocks=[], answer_text="partial", status="interrupted")
    app, _ = _app()
    events = _sse(_chat(TestClient(app), "c1", "q1").text)
    assert "context_restored" not in [e["type"] for e in events]
    assert FakeBackend.instances[0].prompts == ["q1"]


def test_invalid_conversation_ids_are_rejected():
    app, _ = _app()
    client = TestClient(app)
    for bad in ["", "has space", "../etc", "x" * 65]:
        assert _chat(client, bad, "hi").status_code == 422


def test_a_conversation_owned_by_the_other_profile_is_404_for_chat():
    app, _ = _app()
    _chat(TestClient(app), "c1", "hi")
    admin_app, _ = _app(admin_routes=True)
    assert _chat(TestClient(admin_app), "c1", "hi").status_code == 404


class BlockingBackend(FakeBackend):
    gate: asyncio.Event

    async def receive_events(self):
        yield {"type": "text_delta", "text": "a"}
        await BlockingBackend.gate.wait()
        yield {"type": "turn_done", "turns": 1, "duration_s": 0.1, "cost": None}


async def test_a_second_turn_on_a_busy_conversation_is_409_then_allowed_again():
    BlockingBackend.gate = asyncio.Event()
    registry = SessionRegistry(client_factory=BlockingBackend)
    app = create_app(registry=registry)
    endpoint = [r for r in app.routes if getattr(r, "path", "") == "/api/chat"][0].endpoint

    first = await endpoint(ChatRequest(conversation_id="c1", prompt="one"))
    iterator = first.body_iterator
    await iterator.__anext__()  # the turn is now in flight

    with pytest.raises(HTTPException) as busy:
        await endpoint(ChatRequest(conversation_id="c1", prompt="two"))
    assert busy.value.status_code == 409

    BlockingBackend.gate.set()
    async for _ in iterator:
        pass
    again = await endpoint(ChatRequest(conversation_id="c1", prompt="three"))
    assert again.status_code == 200
    async for _ in again.body_iterator:
        pass


def test_a_failed_connect_still_records_the_turn_as_an_error():
    class BrokenBackend(FakeBackend):
        async def connect(self):
            raise RuntimeError("cannot start agent")

    registry = SessionRegistry(client_factory=BrokenBackend)
    client = TestClient(create_app(registry=registry))
    events = _sse(_chat(client, "c1", "hi").text)
    assert events[-1] == {"type": "error", "message": "cannot start agent"}
    turn = store.list_turns("c1")[0]
    assert (turn["status"], turn["error_message"]) == ("error", "cannot start agent")


def test_admin_turn_links_to_its_trace(tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")

    class TracedBackend(FakeBackend):
        trace_context_path = ctx

    registry = SessionRegistry(client_factory=TracedBackend)
    client = TestClient(create_app(registry=registry, admin_routes=True, trace_turns=True))
    events = _sse(_chat(client, "c1", "hi").text)
    assert events[0]["type"] == "trace"  # first turn: no replay event ahead of it

    turn = store.list_turns("c1")[0]
    assert turn["trace_id"] == events[0]["trace_id"]
    assert store.get("c1")["profile"] == "admin"


def test_chat_ui_turns_have_no_trace_id():
    app, _ = _app()
    _chat(TestClient(app), "c1", "hi")
    assert store.list_turns("c1")[0]["trace_id"] is None
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run --group dev pytest test/test_conversations_api.py -v`
Expected: FAIL (422 on `conversation_id`, 404 for `/api/conversations`).

- [ ] **Step 4: Implement the chat route changes in `artmind/webui/app.py`**

4a. Imports: after `from artmind.webui.backends import ...` add the conversation imports (keep alphabetical groups as in the file):

```python
from artmind.conversations import store as conv_store
from artmind.conversations.recorder import ConversationRecorder
from artmind.conversations.replay import build_context
```

and add `import re` to the stdlib imports at the top of the file.

4b. Replace the `ChatRequest` class with the following (and add `import anyio` / `import anyio.to_thread` to the third-party imports). `_ClosingStreamingResponse` closes the body generator when the response ends or is cancelled and runs `on_close` even if the generator never started; `fullmatch` is used because `$` would accept a trailing newline:

```python
_CONVERSATION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class _ClosingStreamingResponse(StreamingResponse):
    """Closes the body generator when the response ends or is cancelled, so the
    generator's `finally` (turn recording, agent cleanup) always runs promptly
    rather than at garbage collection. `on_close` runs last, even when the
    generator never started (an unstarted generator's `finally` never runs)."""

    def __init__(self, content, *, on_close=None, **kwargs) -> None:
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    if self._on_close is not None:
                        self._on_close()


class ChatRequest(BaseModel):
    conversation_id: str
    prompt: str
    backend: str = DEFAULT_BACKEND

    @field_validator("conversation_id")
    @classmethod
    def _valid_conversation_id(cls, value: str) -> str:
        if not _CONVERSATION_ID.fullmatch(value):
            raise ValueError("conversation_id must be 1-64 characters of letters, digits, '-' or '_'")
        return value

    @field_validator("backend")
    @classmethod
    def _known_backend(cls, value: str) -> str:
        if value not in BACKEND_NAMES:
            raise ValueError(f"backend must be one of {BACKEND_NAMES}")
        return value
```

4c. In `create_app`, immediately after `registry = registry or SessionRegistry()`, add:

```python
    profile_name = "admin" if admin_routes else "qa"
    # conversations with a turn currently streaming -> that turn's owner token
    in_flight: dict[str, object] = {}
```

4d. Replace the entire `chat` route (from `@app.post("/api/chat")` through its final `return`) with:

```python
    @app.post("/api/chat")
    async def chat(payload: ChatRequest) -> StreamingResponse:
        from artmind.traces.recorder import TraceRecorder, public_event

        cid = payload.conversation_id
        existing = await asyncio.to_thread(conv_store.get, cid)
        if existing is not None and existing["profile"] != profile_name:
            raise HTTPException(status_code=404, detail=f"no conversation {cid}")
        # Nothing is awaited between this check and the claim below, so two
        # requests cannot both pass it.
        if cid in in_flight:
            raise HTTPException(status_code=409, detail="this conversation already has a turn in progress")
        owner = object()
        in_flight[cid] = owner

        def release() -> None:
            # Idempotent, and never releases a later turn's claim.
            if in_flight.get(cid) is owner:
                del in_flight[cid]

        async def stream():
            recorder = None
            conv = None
            error = None
            fresh = False
            delivered = False
            try:
                # Recorded first so a turn whose agent fails to start is still
                # kept (as an error) rather than silently lost.
                conv = await asyncio.to_thread(
                    ConversationRecorder.start,
                    conversation_id=cid, profile=profile_name, user_text=payload.prompt, backend=payload.backend,
                )
                client, fresh = await registry.get_or_create(cid, payload.backend)
                prompt = payload.prompt
                if fresh:
                    # The agent has no memory of this conversation: replay it.
                    replay = None
                    try:
                        prior = await asyncio.to_thread(conv_store.list_turns, cid)
                        replay = build_context(prior)
                    except Exception:
                        logger.exception("conversation replay unavailable")
                    if replay is not None:
                        prompt = f"{replay.text}\n\n{payload.prompt}"
                        restored = {"type": "context_restored", "turns": replay.included, "omitted": replay.omitted}
                        yield f"data: {json.dumps(restored)}\n\n"
                context_path = getattr(client, "trace_context_path", None)
                if trace_turns and context_path is not None:
                    # Query-trace capture (spec 2026-10-11 §5.1): the id goes into
                    # the session's context file before the agent sees the prompt.
                    recorder = await asyncio.to_thread(
                        TraceRecorder.start,
                        source="admin", question=payload.prompt, session_id=cid,
                        profile="admin", backend=payload.backend, context_path=context_path,
                    )
                    if recorder.enabled:
                        yield f"data: {json.dumps({'type': 'trace', 'trace_id': recorder.trace_id})}\n\n"
                await client.query(prompt)
                delivered = True
                async for event in client.receive_events():
                    event = recorder.observe(event) if recorder is not None else public_event(event)
                    conv.observe(event)
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as exc:  # stream errors to the client, don't 500
                error = str(exc)
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            except BaseException:
                error = error or "interrupted: the client disconnected"
                raise
            finally:
                trace_id = recorder.trace_id if recorder is not None and recorder.enabled else None

                def _finish() -> None:
                    if recorder is not None:
                        recorder.finish(error=error)
                    if conv is not None:
                        conv.finish(error=error, trace_id=trace_id)

                # Shielded: on a client disconnect Starlette cancels this task
                # and re-cancels every await, which would skip the recording.
                with anyio.CancelScope(shield=True):
                    try:
                        if fresh and not delivered:
                            # The agent never saw the replayed transcript. Drop
                            # it so the next turn is fresh again and replays.
                            try:
                                await registry.drop(cid)
                            except Exception:
                                logger.exception("could not drop an agent that never received its turn")
                    finally:
                        release()
                    await anyio.to_thread.run_sync(_finish)

        return _ClosingStreamingResponse(stream(), media_type="text/event-stream", on_close=release)
```

Notes for the implementer: the profile lookup (404) comes first and the 409 check last, with nothing awaited between the check and the claim in `in_flight`, so two requests cannot both pass. The claim is released by the generator's `finally` and by `_ClosingStreamingResponse`'s `on_close` (an unstarted generator's `finally` never runs); the owner token keeps a late release from freeing a later turn's claim. Recording runs in one shielded `finish` because a client disconnect cancels the task and re-cancels every await. A `fresh` agent whose prompt was never delivered is dropped so the next turn replays again.

4e. Update the docstring on the existing `close_session` route to say it releases the live agent and keeps the stored conversation:

```python
    @app.delete("/api/session/{session_id}")
    async def close_session(session_id: str):
        """Release the live agent for a conversation. The stored conversation
        is kept; DELETE /api/conversations/{id} removes it."""
        await registry.drop(session_id)
        return {"ok": True}
```

- [ ] **Step 5: Run the chat-related tests**

Run: `uv run --group dev pytest test/test_conversations_api.py test/test_webui_app.py test/test_traces_app.py -v`
Expected: everything passes **except** `test_chat_records_the_conversation_and_turn`, the only test in this file that reads through the `GET /api/conversations*` routes (they arrive in Task 6). The other tests check the stored rows via `store` directly, so they pass now.

If `test_webui_app.py` shows a failing assertion that depends on stored history (a prompt unexpectedly prefixed with `[Restored conversation`), the `_isolated_db` fixture from Step 1 is missing.

- [ ] **Step 6: Commit**

```bash
git add artmind/webui/app.py test/test_webui_app.py test/test_traces_app.py test/test_conversations_api.py
git commit -m "feat(webui): record chat turns per conversation and replay on fresh agents

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 6: Conversation routes and startup sweep

**Files:**
- Modify: `artmind/webui/app.py` (new routes after `close_session`; lifespan)
- Modify: `test/test_conversations_api.py` (append)

- [ ] **Step 1: Append the failing route tests**

Append to `test/test_conversations_api.py`:

```python
def test_get_conversation_404s_for_unknown_and_other_profile():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    assert client.get("/api/conversations/nope").status_code == 404
    admin_app, _ = _app(admin_routes=True)
    admin_client = TestClient(admin_app)
    assert admin_client.get("/api/conversations/c1").status_code == 404
    assert admin_client.get("/api/conversations").json()["conversations"] == []


def test_list_paginates_with_a_cursor():
    for i, cid in enumerate(["a", "b", "c"]):
        store.ensure(cid, "qa", f"title {cid}", now=100.0 + i)
    app, _ = _app()
    client = TestClient(app)
    page1 = client.get("/api/conversations?limit=2").json()
    assert [c["conversation_id"] for c in page1["conversations"]] == ["c", "b"]
    assert page1["next_cursor"]
    page2 = client.get(f"/api/conversations?limit=2&cursor={page1['next_cursor']}").json()
    assert [c["conversation_id"] for c in page2["conversations"]] == ["a"]
    assert page2["next_cursor"] is None
    assert client.get("/api/conversations?cursor=garbage").status_code == 400


def test_patch_renames_pins_and_archives():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    body = client.patch("/api/conversations/c1", json={"title": "My thread", "pinned": True}).json()
    assert (body["title"], body["title_source"], body["pinned"]) == ("My thread", "user", 1)
    client.patch("/api/conversations/c1", json={"archived": True})
    assert client.get("/api/conversations").json()["conversations"] == []
    archived = client.get("/api/conversations?archived=true").json()["conversations"]
    assert [c["conversation_id"] for c in archived] == ["c1"]
    client.patch("/api/conversations/c1", json={"archived": False})
    assert len(client.get("/api/conversations").json()["conversations"]) == 1


def test_patch_validates_the_title_and_404s():
    app, _ = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    assert client.patch("/api/conversations/c1", json={"title": "   "}).status_code == 422
    assert client.patch("/api/conversations/c1", json={"title": "x" * 201}).status_code == 422
    assert client.patch("/api/conversations/nope", json={"title": "x"}).status_code == 404


def test_delete_drops_the_live_agent_then_the_rows():
    app, registry = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    assert registry.peek("c1") is not None
    assert client.delete("/api/conversations/c1").json() == {"ok": True}
    assert registry.peek("c1") is None
    assert client.get("/api/conversations/c1").status_code == 404
    assert store.list_turns("c1") == []
    assert client.delete("/api/conversations/c1").status_code == 404


def test_release_keeps_the_stored_conversation():
    app, registry = _app()
    client = TestClient(app)
    _chat(client, "c1", "hi")
    assert client.delete("/api/session/c1").json() == {"ok": True}
    assert registry.peek("c1") is None
    assert client.get("/api/conversations/c1").status_code == 200


def test_startup_marks_this_profiles_running_turns_interrupted_only():
    store.ensure("q", "qa", "q")
    store.ensure("a", "admin", "a")
    store.begin_turn("q", "q", "claude-sdk")
    store.begin_turn("a", "a", "claude-sdk")
    app, _ = _app()
    with TestClient(app):  # entering the context runs the lifespan startup
        pass
    assert store.list_turns("q")[0]["status"] == "interrupted"
    assert store.list_turns("a")[0]["status"] == "running"


@pytest.mark.parametrize("template_name", ["index.html", "admin.html"])
def test_pages_carry_the_sidebar_and_conversations_script(template_name):
    from artmind.webui.app import WEBUI_DIR

    app, _ = _app(template_name=template_name)
    html = TestClient(app).get("/").text
    mtime = (WEBUI_DIR / "static" / "conversations.js").stat().st_mtime_ns
    assert f'src="/static/conversations.js?v={mtime}"' in html
    for needle in ('id="sidebar"', 'id="new-chat"', 'id="conv-list"', 'id="sidebar-toggle"'):
        assert needle in html
```

(The last test stays red until Task 7 creates the template changes and `conversations.js`; that is expected. Mark progress per step below.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_conversations_api.py -v`
Expected: the route tests FAIL with 404 on `/api/conversations`.

- [ ] **Step 3: Implement the routes**

In `artmind/webui/app.py`, add models next to `FeedbackRequest`:

```python
class ConversationPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or len(value) > 200:
            raise ValueError("title must be 1-200 characters")
        return value


def _parse_cursor(cursor: str | None) -> tuple[float, str] | None:
    if not cursor:
        return None
    stamp, _, conversation_id = cursor.partition(":")
    try:
        return float(stamp), conversation_id
    except ValueError:
        raise HTTPException(status_code=400, detail="bad cursor") from None
```

Then, directly after the `close_session` route, add:

```python
    @app.get("/api/conversations")
    async def list_conversations(limit: int = 50, cursor: str | None = None, archived: bool = False):
        limit = max(1, min(limit, 200))
        before = _parse_cursor(cursor)
        rows = await asyncio.to_thread(
            conv_store.list_conversations, profile_name, archived=archived, limit=limit + 1, before=before
        )
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = f"{rows[-1]['updated_at']!r}:{rows[-1]['conversation_id']}"
        return {"conversations": rows, "next_cursor": next_cursor}

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str):
        conversation = await asyncio.to_thread(conv_store.get, conversation_id, profile_name)
        if conversation is None:
            raise HTTPException(status_code=404, detail=f"no conversation {conversation_id}")
        turns = await asyncio.to_thread(conv_store.list_turns, conversation_id)
        return {**conversation, "turns": turns}

    @app.patch("/api/conversations/{conversation_id}")
    async def patch_conversation(conversation_id: str, payload: ConversationPatch):
        found = await asyncio.to_thread(
            conv_store.update, conversation_id, profile_name,
            title=payload.title, pinned=payload.pinned, archived=payload.archived,
        )
        if not found:
            raise HTTPException(status_code=404, detail=f"no conversation {conversation_id}")
        return await asyncio.to_thread(conv_store.get, conversation_id, profile_name)

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str):
        if conversation_id in in_flight:
            raise HTTPException(status_code=409, detail="this conversation has a turn in progress")
        if await asyncio.to_thread(conv_store.get, conversation_id, profile_name) is None:
            raise HTTPException(status_code=404, detail=f"no conversation {conversation_id}")
        await registry.drop(conversation_id)
        await asyncio.to_thread(conv_store.delete, conversation_id, profile_name)
        return {"ok": True}
```

Startup sweep: in the `lifespan` function, immediately after the `if trace_turns:` block and before `async def sweep_loop():`, add:

```python
        try:
            await asyncio.to_thread(conv_store.sweep_running, profile_name)
        except Exception:
            logger.exception("conversation sweep failed")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --group dev pytest test/test_conversations_api.py test/test_webui_app.py test/test_traces_app.py test/test_webui_sessions.py -v`
Expected: all pass **except** `test_pages_carry_the_sidebar_and_conversations_script` (2 params), which is fixed by Task 7.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/app.py test/test_conversations_api.py
git commit -m "feat(webui): conversation list/get/rename/delete routes and startup sweep

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 7: Page markup, styles and `app.js` refactor

**Files:**
- Modify: `artmind/webui/templates/index.html`, `artmind/webui/templates/admin.html`
- Modify: `artmind/webui/static/style.css`
- Modify: `artmind/webui/static/app.js`
- Create: `artmind/webui/static/conversations.js` (stub here; real content in Task 8)

There is no JS test harness in this repo; verify with `node --check` and the manual run in Task 9.

- [ ] **Step 1: Templates**

In **both** `index.html` and `admin.html`, replace the brand element.

`index.html`: replace
```html
    <div class="brand">artmind</div>
```
with
```html
    <div class="topbar-left">
      <button id="sidebar-toggle" class="icon-btn" title="Conversations" aria-label="Toggle conversations">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M4 6h16M4 12h16M4 18h16"></path>
        </svg>
      </button>
      <div class="brand">artmind</div>
    </div>
```

`admin.html`: replace
```html
    <div class="brand">artmind <span class="brand-badge">admin</span></div>
```
with
```html
    <div class="topbar-left">
      <button id="sidebar-toggle" class="icon-btn" title="Conversations" aria-label="Toggle conversations">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M4 6h16M4 12h16M4 18h16"></path>
        </svg>
      </button>
      <div class="brand">artmind <span class="brand-badge">admin</span></div>
    </div>
```

In both files, insert the sidebar immediately before the line `  <main id="chat" class="chat" aria-live="polite">`:

```html
  <aside id="sidebar" class="sidebar" aria-label="Conversations" aria-hidden="true">
    <div class="sidebar-head">
      <button id="new-chat" class="new-chat-btn" type="button">+ New chat</button>
    </div>
    <input id="conv-filter" class="conv-filter" type="search" placeholder="Filter conversations"
           aria-label="Filter conversations" autocomplete="off">
    <div id="conv-list" class="conv-list"></div>
    <button id="conv-more" class="conv-more" type="button" hidden>Load more</button>
    <button id="conv-archived-toggle" class="conv-archived-toggle" type="button">Archived</button>
  </aside>

```

In both files, add the script after the `app.js` tag. `index.html`:

```html
  <script src="{{ static_url('app.js') }}"></script>
  <script src="{{ static_url('conversations.js') }}"></script>
```
`admin.html` (keep `admin-help.js` last):
```html
  <script src="{{ static_url('app.js') }}"></script>
  <script src="{{ static_url('conversations.js') }}"></script>
  <script src="{{ static_url('admin-help.js') }}"></script>
```

- [ ] **Step 2: Styles**

Append to `artmind/webui/static/style.css`:

```css

/* ── conversation sidebar ────────────────────────────────────────── */
.topbar-left { display: flex; align-items: center; gap: 4px; }
.sidebar {
  position: fixed; top: 0; left: 0; bottom: 0; z-index: 15;
  width: 280px; max-width: 85vw; padding-top: 56px;
  display: flex; flex-direction: column;
  background: var(--bg-elev); border-right: 1px solid var(--border);
  transform: translateX(-100%); transition: transform 0.25s ease;
}
body.sidebar-open .sidebar { transform: translateX(0); }
.sidebar-head { padding: 8px 12px 4px; }
.new-chat-btn {
  width: 100%; padding: 8px 12px; border-radius: 10px; text-align: left;
  border: 1px solid var(--border); background: var(--bg); font-weight: 600; font-size: 13.5px;
}
.new-chat-btn:hover { background: var(--bg-inset); }
.conv-filter {
  margin: 8px 12px; padding: 6px 10px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text); font: inherit; font-size: 13px;
}
.conv-list { flex: 1; overflow-y: auto; padding: 0 8px 120px; }
.conv-group {
  padding: 12px 8px 4px; color: var(--text-muted);
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
}
.conv-empty { padding: 16px 8px; color: var(--text-muted); font-size: 13px; }
.conv-row { position: relative; display: flex; align-items: center; border-radius: 8px; }
.conv-row:hover, .conv-row.active { background: var(--bg-inset); }
.conv-row.active .conv-title { font-weight: 600; }
.conv-title {
  flex: 1; min-width: 0; padding: 7px 8px; text-align: left; font-size: 13.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.conv-rename {
  flex: 1; min-width: 0; margin: 3px 4px; padding: 3px 6px; font: inherit; font-size: 13.5px;
  border: 1px solid var(--accent); border-radius: 6px; background: var(--bg); color: var(--text);
}
.conv-menu { position: relative; }
.conv-menu summary {
  list-style: none; cursor: pointer; padding: 4px 8px; border-radius: 6px; color: var(--text-muted);
  visibility: hidden;
}
.conv-menu summary::-webkit-details-marker { display: none; }
.conv-row:hover .conv-menu summary, .conv-menu[open] summary { visibility: visible; }
.conv-menu-panel {
  position: absolute; right: 0; top: 100%; z-index: 5; min-width: 130px; padding: 4px;
  display: flex; flex-direction: column;
  background: var(--bg-elev); border: 1px solid var(--border); border-radius: 8px; box-shadow: var(--shadow);
}
.conv-menu-panel button { padding: 6px 10px; text-align: left; border-radius: 6px; font-size: 13px; }
.conv-menu-panel button:hover { background: var(--bg-inset); }
.conv-menu-panel .danger { color: var(--error); }
.conv-more, .conv-archived-toggle {
  margin: 0 12px 8px; padding: 6px 10px; border-radius: 8px;
  color: var(--text-muted); font-size: 12.5px; text-align: left;
}
.conv-more:hover, .conv-archived-toggle:hover { background: var(--bg-inset); color: var(--text); }
.turn-status { margin: 8px 0; color: var(--text-muted); font-size: 12.5px; }
.turn-status.error { color: var(--error); }
@media (min-width: 900px) {
  body.sidebar-open { padding-left: 280px; }
  body.sidebar-open .topbar { left: 280px; }
  body.sidebar-open .composer-wrap { left: 280px; width: calc(100% - 280px); }
}
@media (min-width: 1200px) {
  body.sidebar-open.drawer-open .composer-wrap { width: calc(100% - 280px - 380px); }
}
```

- [ ] **Step 3: `app.js` edits**

Apply these edits to `artmind/webui/static/app.js`. Use exact-match edits for items 1–2 and 4–9; item 3 is a block deletion done with a script.

1. Page-level state. Replace
```js
let sessionId = crypto.randomUUID();
let backend = "claude-sdk";
```
with
```js
let conversationId = crypto.randomUUID();
let backend = "claude-sdk";
```

2. Directly after the line `let traceId = null;       // admin console: this turn's query-trace id` add:
```js

// Filled in by conversations.js (loaded after this file): lets the sidebar
// follow a conversation as its first turn starts.
const chatHooks = { onTurnStart(_conversationId, _prompt) {} };
```

3. Delete the sessionStorage history block. Run:
```bash
cd /Users/surjitdas/projects/artmind9 && python3 - <<'PY'
import re, pathlib
p = pathlib.Path("artmind/webui/static/app.js")
s = p.read_text()
start = s.index("// ── chat history persistence")
end = s.index("restoreHistory();\n", start) + len("restoreHistory();\n")
p.write_text(s[:start] + s[end:].lstrip("\n"))
PY
grep -n "pushHistory\|restoreHistory\|chatHistory\|CHAT_HISTORY_KEY\|sessionStorage" artmind/webui/static/app.js
```
Expected remaining hits: `pushHistory(` in `addUserMessage`, in `send()` (two places: none after step 6/7 below), and in the backend-picker handler. Remove them in the next items; after item 8, the grep must print nothing.

4. In `addUserMessage`, delete the line `  pushHistory(wrap.outerHTML);`.

5. Update the two stale comments. Replace
```js
// here, rather than a listener attached to each element, because a restored
// turn (see "chat history persistence" below) is re-inserted as raw HTML --
// a listener attached directly to the original element would not survive
// that round trip through storage.
```
with
```js
// here, rather than a listener attached to each element, because a stored
// conversation is rendered by conversations.js after the fact -- one listener
// on #chat covers turns however they got there.
```
and in the answer-feedback section replace `// restored history is plain HTML with no listeners.` with `// restored turns are rendered later by conversations.js.`

6. In `handleEvent`, add a case before `case "trace":`:
```js
    case "context_restored":
      showNotice(
        `Context restored from ${ev.turns} earlier turn${ev.turns === 1 ? "" : "s"}` +
          (ev.omitted ? ` (${ev.omitted} older omitted)` : "") + "."
      );
      break;
```
`showNotice` renders in the error colour, so the `context_restored` case passes the neutral `conversation-divider` class instead (item 6b makes `showNotice` take a class). Write the case as:
```js
    case "context_restored":
      showNotice(
        `Context restored from ${ev.turns} earlier turn${ev.turns === 1 ? "" : "s"}` +
          (ev.omitted ? ` (${ev.omitted} older omitted)` : "") + ".",
        "conversation-divider"
      );
      break;
```
(replacing the two-argument-less version above).

6b. Replace
```js
function showNotice(text) {
  chatEl.appendChild(el("div", "notice", text));
  scrollToBottom(true);
}
```
with
```js
function showNotice(text, cls = "notice") {
  chatEl.appendChild(el("div", cls, text));
  scrollToBottom(true);
}
```
All existing one-argument callers keep the error-styled default.

7. `streamTurn`: replace `body: JSON.stringify({ session_id: sessionId, prompt, backend }),` with `body: JSON.stringify({ conversation_id: conversationId, prompt, backend }),`.

8. `send()`: after `addUserMessage(prompt);` add `chatHooks.onTurnStart(conversationId, prompt);`, and in its `finally` block delete the line `    if (turnEl) pushHistory(turnEl.outerHTML);`. `stop()`: replace `/api/session/${sessionId}/interrupt` with `/api/session/${conversationId}/interrupt`.

9. Replace the whole backend-picker section (from `// ── backend picker` through the closing `}` of the `for (const radio of backendRadios)` loop) with:

```js
// ── backend picker ───────────────────────────────────────────────────
// History is stored server-side, so switching backends keeps the conversation:
// the next send carries the new backend and the server replays the stored
// transcript into the fresh agent (spec 2026-10-16 §4.1).
const backendRadios = document.querySelectorAll('input[name="backend"]');
const BACKEND_LABELS = { "claude-sdk": "Claude (SDK)", "acp": "opencode (ACP)" };

for (const radio of backendRadios) {
  radio.addEventListener("change", () => {
    if (!radio.checked || radio.value === backend) return;
    backend = radio.value;
    if (chatEl.querySelector(".msg-user")) {
      chatEl.appendChild(el("div", "conversation-divider", `Continued on ${BACKEND_LABELS[backend]}`));
      scrollToBottom(true);
    }
    promptEl.focus();
  });
}
```

10. Last, the `pagehide` handler: replace `fetch(\`/api/session/${sessionId}\`, { method: "DELETE", keepalive: true })` with `fetch(\`/api/session/${conversationId}\`, { method: "DELETE", keepalive: true })`, and update its comment line `// ── cleanup on tab close` to `// ── release the live agent on tab close (the stored conversation is kept)`.

- [ ] **Step 4: Stub `conversations.js` so the pages resolve**

Create `artmind/webui/static/conversations.js` with just:

```js
"use strict";
// Conversation sidebar and server-side history. Implemented in the next task.
```

- [ ] **Step 5: Verify**

```bash
cd /Users/surjitdas/projects/artmind9
node --check artmind/webui/static/app.js
grep -n "pushHistory\|restoreHistory\|chatHistory\|sessionStorage\|sessionId" artmind/webui/static/app.js
uv run --group dev pytest test/test_conversations_api.py test/test_webui_app.py -v
```
Expected: `node --check` prints nothing; the grep prints nothing; all tests pass, including `test_pages_carry_the_sidebar_and_conversations_script` (both params) and `test_pages_version_static_assets_by_mtime`.

- [ ] **Step 6: Commit**

```bash
git add artmind/webui/templates artmind/webui/static
git commit -m "feat(webui): sidebar markup and styles; drop sessionStorage history

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 8: `conversations.js` (sidebar, restore, routing)

**Files:**
- Modify: `artmind/webui/static/conversations.js` (replace the stub)

The file is an IIFE so its names cannot collide with `app.js`'s top-level bindings, while still reading and assigning them (`conversationId`, `backend`, `streaming`, `turnEl`, `textBlock`, `thinkingBlock`, `traceId`, `toolCount`, `chatEl`, `promptEl`, `traceListEl`, `traceBadgeEl`, `backendRadios`, `BACKEND_LABELS`, `chatHooks`, `el`, `scrollToBottom`, `showNotice`, `addCopyButtons`, `linkVaultSources`, `addToolCard`, `attachToolResult`, `feedbackControl`).

- [ ] **Step 1: Write the file**

Replace the contents of `artmind/webui/static/conversations.js` with:

```js
"use strict";

// Conversation sidebar and server-side history (spec 2026-10-16 §5).
// Loaded after app.js: it shares app.js's top-level bindings, and the URL
// (/?c=<id>) is the state, so reload, back/forward and Obsidian Web Viewer
// round trips all come back to the same conversation.
(() => {
  const sidebarEl = document.getElementById("sidebar");
  const listEl = document.getElementById("conv-list");
  const moreEl = document.getElementById("conv-more");
  const filterEl = document.getElementById("conv-filter");
  const archivedToggleEl = document.getElementById("conv-archived-toggle");
  const toggleEl = document.getElementById("sidebar-toggle");
  const newChatEl = document.getElementById("new-chat");

  const SIDEBAR_KEY = "artmind-sidebar";
  const WIDE = window.matchMedia("(min-width: 900px)");
  const GROUPS = ["Pinned", "Today", "Yesterday", "Previous 7 days", "Earlier"];
  const TITLE_MAX = 60;

  let items = [];
  let nextCursor = null;
  let showArchived = false;
  let filterText = "";

  // ── api ────────────────────────────────────────────────────────────
  async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      const err = new Error(`HTTP ${response.status}`);
      err.status = response.status;
      throw err;
    }
    return response.json();
  }

  const JSON_HEADERS = { "Content-Type": "application/json" };

  // ── sidebar open/closed ────────────────────────────────────────────
  function setSidebar(open, persist = true) {
    document.body.classList.toggle("sidebar-open", open);
    sidebarEl.inert = !open;
    sidebarEl.setAttribute("aria-hidden", String(!open));
    if (!persist) return;
    try {
      localStorage.setItem(SIDEBAR_KEY, open ? "open" : "closed");
    } catch (_) { /* a per-viewer convenience only */ }
  }

  function initialSidebarState() {
    try {
      const saved = localStorage.getItem(SIDEBAR_KEY);
      if (saved) return saved === "open";
    } catch (_) { /* fall through to the default */ }
    return WIDE.matches;
  }

  function closeSidebarIfOverlay() {
    if (!WIDE.matches) setSidebar(false, false);
  }

  toggleEl.addEventListener("click", () => {
    setSidebar(!document.body.classList.contains("sidebar-open"));
  });

  // ── list rendering ─────────────────────────────────────────────────
  function makeTitle(text) {
    const oneLine = text.split(/\s+/).filter(Boolean).join(" ");
    if (!oneLine) return "New conversation";
    return oneLine.length <= TITLE_MAX ? oneLine : oneLine.slice(0, TITLE_MAX - 1).trimEnd() + "…";
  }

  function groupOf(c, now) {
    if (c.pinned) return "Pinned";
    const startOfToday = new Date(now).setHours(0, 0, 0, 0);
    const t = c.updated_at * 1000;
    if (t >= startOfToday) return "Today";
    if (t >= startOfToday - 86400000) return "Yesterday";
    if (t >= startOfToday - 7 * 86400000) return "Previous 7 days";
    return "Earlier";
  }

  function renderList() {
    listEl.replaceChildren();
    const needle = filterText.trim().toLowerCase();
    const shown = items.filter((c) => !needle || (c.title || "").toLowerCase().includes(needle));
    if (shown.length === 0) {
      const text = items.length ? "No matches." : showArchived ? "No archived conversations." : "No conversations yet.";
      listEl.appendChild(el("div", "conv-empty", text));
    }
    const now = Date.now();
    for (const group of GROUPS) {
      const rows = shown.filter((c) => groupOf(c, now) === group);
      if (rows.length === 0) continue;
      listEl.appendChild(el("div", "conv-group", group));
      for (const c of rows) listEl.appendChild(rowFor(c));
    }
    moreEl.hidden = nextCursor === null;
    archivedToggleEl.textContent = showArchived ? "Back to conversations" : "Archived";
  }

  function rowFor(c) {
    const row = el("div", "conv-row");
    row.dataset.id = c.conversation_id;
    if (c.conversation_id === conversationId) row.classList.add("active");

    const open = el("button", "conv-title", c.title || "(untitled)");
    open.type = "button";
    open.title = c.title || "";
    open.addEventListener("click", () => openConversation(c.conversation_id));
    row.appendChild(open);

    const menu = el("details", "conv-menu");
    menu.appendChild(el("summary", null, "⋯"));
    const panel = el("div", "conv-menu-panel");
    const actions = [
      [c.pinned ? "Unpin" : "Pin", () => patch(c, { pinned: !c.pinned })],
      ["Rename", () => startRename(row, c)],
      [c.archived_at ? "Unarchive" : "Archive", () => patch(c, { archived: !c.archived_at })],
    ];
    for (const [label, run] of actions) {
      const button = el("button", null, label);
      button.type = "button";
      button.addEventListener("click", () => {
        menu.open = false;
        run();
      });
      panel.appendChild(button);
    }
    panel.appendChild(deleteButton(menu, c));
    menu.appendChild(panel);
    row.appendChild(menu);
    return row;
  }

  // Two-step delete: the first click arms it for 3 s. No window.confirm --
  // embedded webviews (Obsidian's Web Viewer) may suppress native dialogs.
  function deleteButton(menu, c) {
    const button = el("button", "danger", "Delete");
    button.type = "button";
    let armed = null;
    button.addEventListener("click", () => {
      if (armed === null) {
        button.textContent = "Confirm delete";
        armed = setTimeout(() => {
          armed = null;
          button.textContent = "Delete";
        }, 3000);
        return;
      }
      clearTimeout(armed);
      menu.open = false;
      remove(c);
    });
    return button;
  }

  function startRename(row, c) {
    const titleButton = row.querySelector(".conv-title");
    const input = el("input", "conv-rename");
    input.type = "text";
    input.value = c.title || "";
    input.maxLength = 200;
    titleButton.replaceWith(input);
    input.focus();
    input.select();
    let finished = false;
    const finish = async (commit) => {
      if (finished) return;
      finished = true;
      const value = input.value.trim();
      if (commit && value && value !== c.title) await patch(c, { title: value });
      else renderList();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
  }

  // ── list data ──────────────────────────────────────────────────────
  async function loadList(reset) {
    if (reset) {
      items = [];
      nextCursor = null;
    }
    const query = new URLSearchParams({ limit: "30" });
    if (showArchived) query.set("archived", "true");
    if (nextCursor) query.set("cursor", nextCursor);
    try {
      const data = await api(`/api/conversations?${query}`);
      items = items.concat(data.conversations);
      nextCursor = data.next_cursor;
    } catch (err) {
      console.error("conversation list failed", err);
      showNotice(`Couldn't load conversations: ${err.message}`);
    }
    renderList();
  }

  async function patch(c, body) {
    try {
      await api(`/api/conversations/${encodeURIComponent(c.conversation_id)}`, {
        method: "PATCH",
        headers: JSON_HEADERS,
        body: JSON.stringify(body),
      });
    } catch (err) {
      showNotice(`Couldn't update the conversation: ${err.message}`);
      return;
    }
    await loadList(true);
  }

  async function remove(c) {
    try {
      await api(`/api/conversations/${encodeURIComponent(c.conversation_id)}`, { method: "DELETE" });
    } catch (err) {
      showNotice(
        err.status === 409
          ? "That conversation is still answering; stop it first."
          : `Couldn't delete the conversation: ${err.message}`
      );
      return;
    }
    if (c.conversation_id === conversationId) newConversation(true);
    await loadList(true);
  }

  filterEl.addEventListener("input", () => {
    filterText = filterEl.value;
    renderList();
  });
  moreEl.addEventListener("click", () => loadList(false));
  archivedToggleEl.addEventListener("click", () => {
    showArchived = !showArchived;
    loadList(true);
  });

  // ── view reset and stored-turn rendering ───────────────────────────
  function resetView() {
    for (const node of [...chatEl.children]) {
      if (node.id !== "suggested-chips") node.remove();
    }
    traceListEl.replaceChildren();
    toolCount = 0;
    traceBadgeEl.textContent = "0";
    traceBadgeEl.hidden = true;
    turnEl = null;
    textBlock = null;
    thinkingBlock = null;
    traceId = null;
  }

  function renderThinking(block) {
    const details = el("details", "thinking");
    const summary = el("summary");
    summary.appendChild(el("span", "thinking-label", "Thought"));
    details.appendChild(summary);
    details.appendChild(el("div", "thinking-body", block.md));
    return details;
  }

  function renderText(block) {
    const node = el("div", "md");
    node.innerHTML = DOMPurify.sanitize(marked.parse(block.md));
    addCopyButtons(node);
    linkVaultSources(node);
    return node;
  }

  function renderTurns(turns) {
    let previousBackend = null;
    for (const t of turns) {
      if (previousBackend && t.backend !== previousBackend) {
        chatEl.appendChild(
          el("div", "conversation-divider", `Continued on ${BACKEND_LABELS[t.backend] ?? t.backend}`)
        );
      }
      previousBackend = t.backend;

      const user = el("div", "msg-user");
      user.appendChild(el("div", "pill", t.user_text));
      chatEl.appendChild(user);

      const turn = el("div", "turn");
      for (const block of t.blocks) {
        if (block.type === "thinking") turn.appendChild(renderThinking(block));
        else if (block.type === "text") turn.appendChild(renderText(block));
        else if (block.type === "tool") {
          addToolCard({ id: block.id, name: block.name, input: block.input ?? "" });
          if (block.result !== null && block.result !== undefined) {
            attachToolResult({ tool_id: block.id, content: block.result });
          }
        }
      }
      if (t.status === "interrupted") {
        turn.appendChild(el("div", "turn-status", "Interrupted: this turn did not finish."));
      } else if (t.status === "error") {
        turn.appendChild(el("div", "turn-status error", `Error: ${t.error_message ?? "the turn failed"}`));
      } else if (t.status === "running") {
        turn.appendChild(el("div", "turn-status", "Still running, or interrupted."));
      }
      if (t.trace_id) turn.appendChild(feedbackControl(t.trace_id));
      chatEl.appendChild(turn);
    }
  }

  // ── opening and starting conversations ─────────────────────────────
  function currentIdFromUrl() {
    return new URLSearchParams(window.location.search).get("c");
  }

  function newConversation(push) {
    if (streaming) return;
    resetView();
    conversationId = crypto.randomUUID();
    if (push) history.pushState({}, "", "/");
    renderList();
    promptEl.focus();
  }

  async function openConversation(id, push = true) {
    if (streaming) return;
    let data;
    try {
      data = await api(`/api/conversations/${encodeURIComponent(id)}`);
    } catch (err) {
      newConversation(false);
      history.replaceState({}, "", "/");
      showNotice(err.status === 404 ? "Conversation not found. Started a new chat." : `Couldn't load it: ${err.message}`);
      return;
    }
    resetView();
    conversationId = id;
    const last = data.turns[data.turns.length - 1];
    if (last && last.backend !== backend) {
      backend = last.backend;
      for (const radio of backendRadios) radio.checked = radio.value === backend;
    }
    renderTurns(data.turns);
    if (push) history.pushState({ c: id }, "", `/?c=${encodeURIComponent(id)}`);
    renderList();
    scrollToBottom(true);
    closeSidebarIfOverlay();
  }

  newChatEl.addEventListener("click", () => {
    newConversation(true);
    closeSidebarIfOverlay();
  });

  window.addEventListener("popstate", () => {
    if (streaming) return;
    const id = currentIdFromUrl();
    if (id) openConversation(id, false);
    else newConversation(false);
  });

  // ── follow the live conversation ───────────────────────────────────
  chatHooks.onTurnStart = (id, prompt) => {
    if (currentIdFromUrl() !== id) history.replaceState({ c: id }, "", `/?c=${encodeURIComponent(id)}`);
    if (showArchived) return;
    const at = items.findIndex((c) => c.conversation_id === id);
    const c = at >= 0
      ? items.splice(at, 1)[0]
      : { conversation_id: id, title: makeTitle(prompt), pinned: 0, archived_at: null };
    c.updated_at = Date.now() / 1000;
    items.unshift(c);
    renderList();
  };

  // ── start ──────────────────────────────────────────────────────────
  setSidebar(initialSidebarState(), false);
  loadList(true);
  const initialId = currentIdFromUrl();
  if (initialId) openConversation(initialId, false);
})();
```

- [ ] **Step 2: Syntax check and tests**

```bash
cd /Users/surjitdas/projects/artmind9
node --check artmind/webui/static/conversations.js
uv run --group dev pytest test/test_conversations_api.py test/test_webui_app.py -v
```
Expected: no `node` output; tests pass (the `conversations.js` mtime test reads the real file).

- [ ] **Step 3: Commit**

```bash
git add artmind/webui/static/conversations.js
git commit -m "feat(webui): conversation sidebar, restore by URL, stored-turn rendering

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Task 9: Docs, targeted regression run, manual verification

**Files:**
- Modify: `CLAUDE.md` (repo-layout table)

- [ ] **Step 1: Document the package**

In `CLAUDE.md`, add a row to the repo-layout table directly after the `artmind/traces/` row:

```
| `artmind/conversations/` | Chat history for chat-ui and admin-ui (spec `docs/superpowers/specs/2026-10-16-chat-conversations-design.md`). `store` (the `conversations` / `conversation_turns` tables in the registry DB: ensure/get/list/update/delete, begin/finish/list turns, profile-scoped startup sweep), `recorder` (`ConversationRecorder`: folds a turn's neutral UI events into ordered `blocks` and writes the turn; wraps `/api/chat`), `replay` (`build_context`: user questions + final answers within `ARTMIND_REPLAY_BUDGET_CHARS`, default 24000, prepended to the prompt whenever `SessionRegistry.get_or_create` reports a fresh agent). The URL `/?c=<id>` is the UI state; `static/conversations.js` is the sidebar. Conversations hold private corpus content: local DB only, and tests use synthetic text. A running `chat-ui` / `admin-ui` serves stale code, so restart it after editing. |
```

Also fix one spec line that cannot be met: in `docs/superpowers/specs/2026-10-16-chat-conversations-design.md` section 5, replace "re-render the feedback
  control (existing `feedbackControl`, delegated handler already in place) and a
  link to the trace" with "re-render the feedback control (existing
  `feedbackControl`, delegated handler already in place); there is no web trace
  page to link to, so the `trace_id` is stored for `artmind traces show`".

- [ ] **Step 2: Run every test file this change touched, plus neighbours**

```bash
cd /Users/surjitdas/projects/artmind9
uv run --group dev pytest test/test_conversations_store.py test/test_conversations_replay.py \
  test/test_conversations_recorder.py test/test_conversations_api.py test/test_webui_sessions.py \
  test/test_webui_app.py test/test_traces_app.py test/test_traces_store.py test/test_traces_recorder.py \
  test/test_webui_admin_api.py test/test_webui_benchmark_api.py test/test_webui_source_links.py \
  test/test_webui_session_resume.py test/test_cli_guide.py -v
```
Expected: all pass. (Do not run the whole suite; it stalls on this machine.)

- [ ] **Step 3: Manual UI verification with a fake agent (no Claude, no Neo4j)**

Restart nothing that is running; this uses a throwaway DB and its own port.

```bash
cd /Users/surjitdas/projects/artmind9
export ARTMIND_HOME=$(mktemp -d) ARTMIND_DATA_DIR=$(mktemp -d)
uv run python - <<'PY'
import asyncio, uvicorn
from artmind.webui.app import create_app
from artmind.webui.sessions import SessionRegistry

class Fake:
    def __init__(self, backend="claude-sdk"): self.backend, self.prompts = backend, []
    async def connect(self): pass
    async def disconnect(self): pass
    async def interrupt(self): pass
    async def query(self, prompt): self.prompts.append(prompt)
    async def receive_events(self):
        yield {"type": "tool_call", "id": "t1", "name": "Bash", "input": "artmind query chunks"}
        await asyncio.sleep(0.3)
        yield {"type": "tool_result", "tool_id": "t1", "content": "2 rows"}
        said = "restored" if "[Restored conversation" in self.prompts[-1] else "fresh"
        yield {"type": "text_delta", "text": f"**{said}** answer to: {self.prompts[-1][-30:]}"}
        yield {"type": "block_done", "block": "text"}
        yield {"type": "turn_done", "turns": 1, "duration_s": 0.3, "cost": 0.001}

app = create_app(SessionRegistry(client_factory=Fake))
uvicorn.run(app, host="127.0.0.1", port=8390, log_level="warning")
PY
```
(Run it in a terminal tab or with `run_in_background`.) Open `http://127.0.0.1:8390/` in the browser pane and check, in order. Each item names the observable result:

1. Sidebar is open on a wide window and lists "No conversations yet."; the hamburger button toggles it.
2. Send "first question": the URL becomes `/?c=<uuid>`, the sidebar row appears at the top under Today with the title "first question", the tool badge shows 1, the answer says **fresh**.
3. Reload the page: the conversation, its answer (rendered markdown, bold), the tool card in the trace drawer and the badge all come back.
4. Send "second question" in the reloaded page: a "Context restored from 1 earlier turn." line appears, and the answer says **restored** (the server replayed because this process's registry has a live agent only after the first send in this page; if it says fresh, the agent from the first load is still alive within the 30-minute idle window: stop the server, start it again, reload and send, then it must say restored).
5. Click **+ New chat**: the chat clears, URL is `/`, the old row stays in the list. Send a message, then click the first conversation's row: it reopens with both turns and no duplicate rows.
6. Row menu: Rename (Enter commits, Escape cancels), Pin (row moves to a Pinned group), Archive (row disappears; **Archived** toggle shows it with Unarchive), Delete (first click arms "Confirm delete", second deletes; deleting the open conversation starts a new chat).
7. Filter box narrows the loaded rows by title.
8. Switch the backend radio mid-conversation: a "Continued on opencode (ACP)" divider appears, the next answer says **restored**, and after a reload the divider is shown between those two turns.
9. Open `http://127.0.0.1:8390/?c=does-not-exist`: a "Conversation not found. Started a new chat." notice and URL `/`.
10. Narrow the window below 900px (use `resize_window` with preset `mobile`): the sidebar overlays and closes after choosing a conversation.
11. Browser console shows no errors.

Then stop the server and unset the env vars (`unset ARTMIND_HOME ARTMIND_DATA_DIR`).

- [ ] **Step 4: Repeat the smoke check for admin-ui**

Same fake, but build the app with `create_app(SessionRegistry(client_factory=Fake), template_name="admin.html", admin_routes=True, trace_turns=True)` on port 8391. Verify the sidebar and the new-chat/restore flow also work there, that the suggested-action chips row still shows above the first message and survives New chat (it must not be removed), and that admin conversations do not appear in the chat-ui server's list (start both against the same `ARTMIND_DATA_DIR`).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-10-16-chat-conversations-design.md
git commit -m "docs: document artmind/conversations in the repo layout

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Restart long-running UIs** after merging: `just dev-stop-daemons`, then `just serve-ui` / `just serve-admin-ui` from inside the vault. No `artmind setup` is needed (no skills or schemas changed), and no migration is needed (tables are `CREATE TABLE IF NOT EXISTS` in `_init_db`, run on every `_get_db`).
- **Dangling trace ids:** `trace_turns` is pruned by age (default 90 days) independently of conversations, so a stored `trace_id` may point at a deleted trace. The UI only renders the feedback control, which POSTs to `/api/traces/{id}/feedback` and shows "Not saved: HTTP 404" in that case. Acceptable for this phase.
- **Feedback state is not restored:** `GET /api/conversations/{id}` does not return prior ratings, so a restored feedback control shows no selection; clicking re-posts a rating. Acceptable for this phase.
- **Out of scope here (phase 3 per the spec):** FTS search, markdown export, retention prune, and auto-replay after an ACP skill refresh (spec section 7).
