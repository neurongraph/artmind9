# Query review loop — P1: trace capture, feedback, `traces` CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every admin-console turn and benchmark question as a structured trace. A trace holds the agent events, a span per `artmind query` call (with evidence read from that call's output), and the LLM calls those spans made. Also derive graph→vector fall-through, take operator feedback, and expose `artmind traces list/show/triage/feedback/stats/prune`.

**Architecture:**
- A new `artmind/traces/` package with these modules:
  - `store`: SQLite access. The tables are created in `artmind/db.py`.
  - `evidence`: a pure parser of query output payloads.
  - `path`: retrieval layers, fall-through, answer basis.
  - `span`: CLI-side capture through `TracedGroup` on the `query` group.
  - `recorder`: turn-side capture.
  - `analysis`: triage and stats.
- **How the trace id reaches the CLI.** The admin console creates each backend with a per-session trace-context file (env `ARTMIND_TRACE_CONTEXT`) and writes the current turn's trace id into it. The CLI reads it, and `_entry` forwards it to `serve`.
- **What spans record.** Spans read row counts and evidence from the payload `_echo_json` prints, so no query function changes.

**Tech Stack:** Python 3.14, click 8.4 / rich-click, FastAPI, sqlite3, pytest with `CliRunner` / `TestClient`, vanilla JS (`static/app.js`).

**Spec:** `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`. P1 is §4, §4.1, §5, §6 and §10's P1 row.

**Branch:** `spec/query-review-loop` (already checked out; the spec commits are on it).

---

## Ground rules for every task

- Run tests with `uv run --group dev pytest <path> -v` from the repo root. `test/conftest.py` already points `ARTMIND_HOME` / `ARTMIND_DATA_DIR` at a temp dir and blocks Neo4j.
- Every test module that touches the DB isolates it the way `test/test_benchmark.py` does:

  ```python
  @pytest.fixture(autouse=True)
  def _isolated_db(tmp_path, monkeypatch):
      import artmind.db as db
      monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
  ```

  `artmind.db._get_db()` reads the module global `DB_PATH` at call time, so this patch reaches `artmind.traces.store`, which calls `_get_db()`.
- **Capture must never break a query.** Every write path wrapped in this plan swallows its exceptions. Do not remove those `try/except` blocks to "simplify".
- Following CLAUDE.md §3, assert on the **recorded values** (command, args, evidence, payloads), never only on counts.
- Commit after each task. End every commit message with:

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```

## File map

| File | Status | Responsibility |
|---|---|---|
| `artmind/db.py` | modify | DDL for `trace_turns`, `trace_events`, `trace_spans`, `trace_llm_calls`, `trace_feedback`, `trace_reviews`; `benchmark_questions.trace_id` migration |
| `artmind/traces/__init__.py` | create | package docstring |
| `artmind/traces/store.py` | create | all trace SQL: insert/update/read/prune |
| `artmind/traces/evidence.py` | create | `summarize(payload)` → rows / truncated / rows_total / evidence |
| `artmind/traces/path.py` | create | `layer_of`, `parse_sources`, `answer_basis`, `derive` |
| `artmind/traces/span.py` | create | trace-id resolution, `trace_override`, `TracedGroup`, `observe_output`, `record_llm_call` |
| `artmind/traces/recorder.py` | create | `TraceRecorder`, `public_event`, `match_tool_ids`, `domains_of`, `graph_state` |
| `artmind/traces/analysis.py` | create | `parse_since`, `signals`, `triage`, `stats`, `show` |
| `artmind/_entry.py` | modify | `_trace_id()`; `_proxy` sends `trace_id` |
| `artmind/server.py` | modify | `QueryRequest.trace_id`; `run_cli_args(..., trace_id)` under `trace_override` |
| `artmind/cli.py` | modify | `query` group → `cls=TracedGroup`; `_echo_json` → `observe_output`; new `traces` group; `COMMAND_GROUPS` |
| `utils/functions.py` | modify | `log_llm_call` → `record_llm_call` |
| `artmind/webui/agent.py` | modify | `EventMapper(capture=...)` adds `_trace` |
| `artmind/webui/backends/acp_events.py` | modify | `ACPEventMapper(capture=...)` adds `_trace` |
| `artmind/webui/backends/claude_sdk.py` | modify | `trace_context` param, `trace_context_path`, unlink on disconnect |
| `artmind/webui/backends/acp.py` | modify | `extra_env`, `trace_context` params, `trace_context_path`, unlink on disconnect |
| `artmind/webui/backends/__init__.py` | modify | `create_backend(trace_context=)`, `new_trace_context()`, `backend_factory(trace=)` |
| `artmind/webui/app.py` | modify | `create_app(trace_turns=)`, recorder in `/api/chat`, `trace` SSE event, feedback endpoint, prune at startup |
| `artmind/benchmark.py` | modify | recorder per question, `trace_id` column |
| `artmind/webui/static/app.js`, `static/style.css` | modify | feedback control |
| `artmind/env.example`, `CLAUDE.md`, `justfile` | modify | docs and recipes |
| `test/test_traces_store.py`, `test_traces_evidence.py`, `test_traces_path.py`, `test_traces_span.py`, `test_traces_proxy.py`, `test_traces_mappers.py`, `test_traces_backends.py`, `test_traces_recorder.py`, `test_traces_app.py`, `test_traces_analysis.py`, `test_traces_cli.py` | create | tests |

---

### Task 1: Trace tables

**Files:**
- Modify: `artmind/db.py` (insert before the final `conn.commit()` / `conn.close()` of `_init_db`, after the `for step in ("grain", "bridge", "mapping"):` loop)
- Test: `test/test_traces_store.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_traces_store.py`:

```python
"""Trace tables (spec 2026-10-11 §4) and artmind.traces.store."""

import sqlite3

import pytest


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


def test_trace_tables_exist_with_spec_columns():
    assert {
        "trace_id", "source", "session_id", "benchmark_run_id", "benchmark_seq",
        "question", "answer", "domains", "profile", "backend", "model", "turns",
        "duration_s", "cost_usd", "input_tokens", "output_tokens", "graph_state",
        "retrieval_path", "graph_attempts", "graph_productive", "fell_through",
        "answer_basis", "status", "error_message", "started_at", "completed_at",
    } <= _columns("trace_turns")
    assert {"trace_id", "seq", "ts", "type", "tool_id", "parent_tool_id", "name", "payload"} <= _columns("trace_events")
    assert {
        "span_id", "trace_id", "tool_id", "command", "args", "exit_code", "duration_ms",
        "rows", "rows_total", "truncated", "evidence", "started_at",
    } <= _columns("trace_spans")
    assert {"span_id", "seq", "call_type", "model", "prompt", "response", "duration_ms", "created_at"} <= _columns("trace_llm_calls")
    assert {"trace_id", "rating", "expected", "created_at"} <= _columns("trace_feedback")
    assert {"review_id", "trace_id", "stage", "elements", "status", "dossier_path", "updated_at"} <= _columns("trace_reviews")


def test_benchmark_questions_gains_trace_id():
    assert "trace_id" in _columns("benchmark_questions")


def test_benchmark_questions_migration_on_existing_db(tmp_path, monkeypatch):
    import artmind.db as db

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE benchmark_questions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " run_id TEXT NOT NULL, seq INTEGER NOT NULL, question TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'queued')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    assert "trace_id" in _columns("benchmark_questions")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_traces_store.py -v`
Expected: FAIL (`trace_turns` has no columns, so the set comparison fails).

- [ ] **Step 3: Add the DDL**

In `artmind/db.py`, directly before the final `    conn.commit()\n    conn.close()` of `_init_db`, insert:

```python
    # Query traces (docs/superpowers/specs/2026-10-11-query-review-loop-design.md
    # §4): admin-console turns and benchmark questions, the agent's events, one
    # span per `artmind query` call, and the LLM calls those spans made. Local
    # to this machine like the rest of this DB -- never synced, never committed.
    # Timestamps are epoch seconds (REAL): spans are matched to the agent's
    # tool calls by time window, and both sides read one clock.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_turns (
            trace_id          TEXT PRIMARY KEY,
            source            TEXT NOT NULL,
            session_id        TEXT,
            benchmark_run_id  TEXT,
            benchmark_seq     INTEGER,
            question          TEXT NOT NULL,
            answer            TEXT,
            domains           TEXT,
            profile           TEXT,
            backend           TEXT,
            model             TEXT,
            turns             INTEGER,
            duration_s        REAL,
            cost_usd          REAL,
            input_tokens      INTEGER,
            output_tokens     INTEGER,
            graph_state       TEXT,
            retrieval_path    TEXT,
            graph_attempts    INTEGER,
            graph_productive  INTEGER,
            fell_through      INTEGER,
            answer_basis      TEXT,
            status            TEXT NOT NULL DEFAULT 'running',
            error_message     TEXT,
            started_at        REAL NOT NULL,
            completed_at      REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trace_turns_started ON trace_turns(started_at)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_events (
            trace_id        TEXT NOT NULL,
            seq             INTEGER NOT NULL,
            ts              REAL NOT NULL,
            type            TEXT NOT NULL,
            tool_id         TEXT,
            parent_tool_id  TEXT,
            name            TEXT,
            payload         TEXT,
            PRIMARY KEY (trace_id, seq)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_spans (
            span_id      TEXT PRIMARY KEY,
            trace_id     TEXT NOT NULL,
            tool_id      TEXT,
            command      TEXT,
            args         TEXT,
            exit_code    INTEGER,
            duration_ms  INTEGER,
            rows         INTEGER,
            rows_total   INTEGER,
            truncated    INTEGER,
            evidence     TEXT,
            started_at   REAL NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trace_spans_trace ON trace_spans(trace_id)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_llm_calls (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            span_id      TEXT NOT NULL,
            seq          INTEGER NOT NULL,
            call_type    TEXT,
            model        TEXT,
            prompt       TEXT,
            response     TEXT,
            duration_ms  INTEGER,
            created_at   REAL NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trace_llm_calls_span ON trace_llm_calls(span_id)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_feedback (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id    TEXT NOT NULL,
            rating      TEXT NOT NULL,
            expected    TEXT,
            created_at  REAL NOT NULL
        )
    """)
    # A local index of review dossiers (P2 writes it; P1 only needs it to
    # exist so `traces triage` and `prune` can tell reviewed turns apart).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_reviews (
            review_id     TEXT PRIMARY KEY,
            trace_id      TEXT NOT NULL,
            stage         TEXT,
            elements      TEXT,
            status        TEXT NOT NULL DEFAULT 'open',
            dossier_path  TEXT,
            updated_at    REAL NOT NULL
        )
    """)
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(benchmark_questions)")}
    if "trace_id" not in existing:
        cursor.execute("ALTER TABLE benchmark_questions ADD COLUMN trace_id TEXT")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_store.py test/test_benchmark.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/db.py test/test_traces_store.py
git commit -m "feat(traces): trace tables in the registry DB"
```

---

### Task 2: `artmind.traces.store`

**Files:**
- Create: `artmind/traces/__init__.py`, `artmind/traces/store.py`
- Test: `test/test_traces_store.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `test/test_traces_store.py`)

```python
from artmind.traces import store


def _turn(trace_id="t1", started_at=1000.0, **kw):
    store.insert_turn(trace_id, source=kw.pop("source", "admin"), question=kw.pop("question", "Who leads X?"),
                      session_id="s1", profile="admin", backend="claude-sdk", started_at=started_at, **kw)


def test_cap_keeps_short_text_and_marks_long_text():
    assert store.cap("abc") == "abc"
    assert store.cap({"a": 1}) == '{"a": 1}'
    assert store.cap(None) is None
    long = "x" * (store.PAYLOAD_CAP + 10)
    capped = store.cap(long)
    assert capped.startswith("x" * 100)
    assert capped.endswith("…[truncated 10 bytes]")


def test_insert_and_update_turn_roundtrip():
    _turn()
    store.update_turn("t1", answer="A", turns=3, fell_through=1, answer_basis="vector", status="done")
    turn = store.get_turn("t1")
    assert turn["question"] == "Who leads X?"
    assert turn["answer"] == "A"
    assert turn["turns"] == 3
    assert turn["fell_through"] == 1
    assert turn["answer_basis"] == "vector"
    assert turn["status"] == "done"
    assert turn["started_at"] == 1000.0


def test_update_turn_rejects_unknown_columns():
    _turn()
    with pytest.raises(ValueError):
        store.update_turn("t1", question="rewritten")


def test_events_spans_llm_calls_roundtrip():
    _turn()
    store.insert_events("t1", [
        {"seq": 0, "ts": 1.0, "type": "tool_call", "tool_id": "tu1", "parent_tool_id": None,
         "name": "Bash", "payload": '{"command": "artmind query vector-text"}'},
        {"seq": 1, "ts": 2.0, "type": "text", "tool_id": None, "parent_tool_id": None, "name": None, "payload": "Hi"},
    ])
    store.insert_span(
        {"span_id": "sp1", "trace_id": "t1", "command": "query vector-text", "args": '{"domain": ["b"]}',
         "exit_code": 0, "duration_ms": 12, "rows": 2, "rows_total": None, "truncated": 0,
         "evidence": '{"chunks": [{"chunk_id": "c1", "doc_id": "d1"}]}', "started_at": 1.5},
        [{"seq": 0, "call_type": "chat", "model": "m", "prompt": "p", "response": "r", "duration_ms": None}],
    )
    store.set_span_tool_ids([("sp1", "tu1")])

    events = store.list_events("t1")
    assert [e["type"] for e in events] == ["tool_call", "text"]
    spans = store.list_spans("t1")
    assert spans[0]["tool_id"] == "tu1"
    assert spans[0]["args"] == {"domain": ["b"]}
    assert spans[0]["evidence"] == {"chunks": [{"chunk_id": "c1", "doc_id": "d1"}]}
    calls = store.list_llm_calls(["sp1"])
    assert calls["sp1"][0]["prompt"] == "p"


def test_feedback_latest_wins_and_unknown_trace_raises():
    _turn()
    store.add_feedback("t1", "partial", "expected Alice")
    store.add_feedback("t1", "wrong", None)
    assert store.latest_feedback("t1")["rating"] == "wrong"
    with pytest.raises(KeyError):
        store.add_feedback("nope", "good")
    with pytest.raises(ValueError):
        store.add_feedback("t1", "meh")


def test_list_turns_joins_rating_and_reviewed():
    _turn("t1", started_at=1000.0)
    _turn("t2", started_at=2000.0, source="benchmark")
    store.add_feedback("t1", "wrong")
    turns = store.list_turns()
    assert [t["trace_id"] for t in turns] == ["t2", "t1"]
    assert turns[1]["rating"] == "wrong"
    assert turns[0]["rating"] is None
    assert [t["trace_id"] for t in store.list_turns(source="benchmark")] == ["t2"]
    assert [t["trace_id"] for t in store.list_turns(since=1500.0)] == ["t2"]


def test_prune_keeps_rated_and_reviewed_turns():
    import time

    old = time.time() - 200 * 86400
    for tid in ("old-plain", "old-rated", "old-reviewed"):
        _turn(tid, started_at=old)
        store.insert_span({"span_id": f"sp-{tid}", "trace_id": tid, "command": "query chunks", "args": "[]",
                           "exit_code": 0, "duration_ms": 1, "rows": 0, "rows_total": None, "truncated": 0,
                           "evidence": "{}", "started_at": old}, [])
    _turn("new", started_at=time.time())
    store.add_feedback("old-rated", "good")
    store.insert_review_stub("rv1", "old-reviewed")

    assert store.prune(90) == 1
    remaining = {t["trace_id"] for t in store.list_turns()}
    assert remaining == {"old-rated", "old-reviewed", "new"}
    assert store.list_spans("old-plain") == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.traces'`.

- [ ] **Step 3: Implement**

Create `artmind/traces/__init__.py`:

```python
"""Query traces: capture, storage and analysis of admin-console and benchmark
turns (docs/superpowers/specs/2026-10-11-query-review-loop-design.md).

`store` owns the SQL, `evidence` reads a query command's output payload,
`path` derives the retrieval path, `span` is the CLI side of capture,
`recorder` the turn side, and `analysis` the read surface behind
`artmind traces`.
"""
```

Create `artmind/traces/store.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_store.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/__init__.py artmind/traces/store.py test/test_traces_store.py
git commit -m "feat(traces): store module for turns, events, spans, LLM calls, feedback"
```

---

### Task 3: Evidence extraction from query payloads

**Files:**
- Create: `artmind/traces/evidence.py`
- Test: `test/test_traces_evidence.py`

The payload shapes below come from a survey of every `artmind query` command (2026-10-11):
- Every payload is a dict, and its main list is `rows`.
- Chunks are dicts that carry `id` + `doc_id` + `name`. Conflict evidence uses `chunk_id` + `doc_id`.
- Documents carry `id` + `path` / `source_path`, with no `doc_id`.
- Entities carry `_id`.

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_evidence.py`:

```python
"""artmind.traces.evidence.summarize over real query payload shapes."""

from artmind.traces.evidence import summarize

VECTOR_TEXT = {
    "query_type": "vector_text", "domains": ["banking"], "question": "q", "parameters": {},
    "rows": [
        {"score": 0.8, "source_type": "document",
         "chunk": {"id": "c1", "name": "c1", "doc_id": "d1", "text": "…"},
         "document": {"id": "d1", "name": "Policy", "path": "banking/Policy.md", "source_path": "banking/Policy.md", "_domain": "banking"}},
        {"score": 0.5, "source_type": "user_chat", "chat": {"id": "u1", "raw_text": "…"}},
    ],
}

PATTERN2 = {
    "query_type": "graph", "command": "pattern", "pattern": "pattern2", "rows": [
        {"entityData": {"_id": "e1", "name": "Alice", "label": "Person"},
         "doc_sources": [{"id": "c9", "name": "c9", "doc_id": "d9", "_domain": "b", "source_type": "document"}],
         "source_documents": [{"id": "d9", "name": "Org", "path": "b/Org.md", "source_path": "b/Org.md"}]},
    ],
}

TEXT2CYPHER = {"command": "text2cypher", "generated_cypher": "MATCH (n) RETURN n", "rows": [{"n": 1}],
               "rows_total": 7, "truncated": True}

VIEWS_RUN_OK = {"view": "org_tree", "version": 2, "domains": ["b"], "status": "ok",
                "params": {"root": {"input": "Alice", "_id": "e1"}}, "rows": [{"x": 1}, {"x": 2}],
                "rows_total": 2, "truncated": False}

VIEWS_RUN_AMBIGUOUS = {"view": "org_tree", "version": 2, "status": "needs_disambiguation", "param": "root",
                       "candidates": [{"_id": "e1", "name": "Alice"}, {"_id": "e2", "name": "Alicia"}]}

ENTITY_RESOLVE = {"query_type": "entity_resolve", "rows": [
    {"entity": {"_id": "e1", "name": "Alice", "entity_class": "Person"}, "score": 0.033, "matched_by": ["vector"]},
    {"entity": {"_id": "e2", "name": "Alicia", "entity_class": "Person"}, "score": 0.031, "matched_by": ["fulltext"]},
]}

HIERARCHY = {"command": "hierarchy", "root": {"_id": "e1"}, "rows": [{"_id": "e2", "depth": 1}],
             "summary": {"total": 40, "truncated": True}}

TEXT2SQL = {"command": "text2sql", "generated_sql": "SELECT 1", "rows": [{"a": 1}],
            "source_tables": [{"table": "loans", "source_file": "tables/loans.csv"}]}

CONFLICTS = {"command": "conflicts", "rows": [{
    "conflict": {"id": "k1"}, "entities": [{"_id": "e5", "name": "X"}],
    "evidence": [{"side": "a", "chunk_id": "c5", "doc_id": "d5", "text": "…",
                  "document": {"name": "N", "path": "b/N.md", "source_path": "b/N.md"}}]}]}


def test_vector_text_rows_chunks_and_documents():
    s = summarize(VECTOR_TEXT)
    assert s["rows"] == 2
    assert s["truncated"] is False
    assert s["evidence"]["chunks"] == [{"chunk_id": "c1", "doc_id": "d1"}]
    assert s["evidence"]["documents"] == [{"doc_id": "d1", "path": "banking/Policy.md"}]


def test_pattern2_entities_chunks_documents():
    ev = summarize(PATTERN2)["evidence"]
    assert ev["entities"] == ["e1"]
    assert ev["chunks"] == [{"chunk_id": "c9", "doc_id": "d9"}]
    assert ev["documents"] == [{"doc_id": "d9", "path": "b/Org.md"}]


def test_text2cypher_cypher_and_truncation():
    s = summarize(TEXT2CYPHER)
    assert s["evidence"]["cypher"] == "MATCH (n) RETURN n"
    assert s["truncated"] is True
    assert s["rows_total"] == 7


def test_views_run_ok_and_disambiguation():
    ok = summarize(VIEWS_RUN_OK)
    assert ok["rows"] == 2
    assert ok["evidence"]["view"] == {"name": "org_tree", "version": 2, "status": "ok",
                                      "params": {"root": {"input": "Alice", "_id": "e1"}}}
    amb = summarize(VIEWS_RUN_AMBIGUOUS)
    assert amb["rows"] == 0  # a disambiguation prompt is not an answer
    assert amb["evidence"]["resolved"] == [{"id": "e1", "name": "Alice", "entity_class": None, "score": None},
                                           {"id": "e2", "name": "Alicia", "entity_class": None, "score": None}]


def test_entity_resolve_candidates_in_order_with_scores():
    ev = summarize(ENTITY_RESOLVE)["evidence"]
    assert ev["resolved"] == [{"id": "e1", "name": "Alice", "entity_class": "Person", "score": 0.033},
                              {"id": "e2", "name": "Alicia", "entity_class": "Person", "score": 0.031}]


def test_hierarchy_truncation_lives_in_summary():
    s = summarize(HIERARCHY)
    assert s["truncated"] is True
    assert s["rows_total"] == 40


def test_text2sql_sql_and_source_files():
    ev = summarize(TEXT2SQL)["evidence"]
    assert ev["sql"] == "SELECT 1"
    assert ev["documents"] == [{"doc_id": None, "path": "tables/loans.csv"}]


def test_conflict_evidence_chunk_ids():
    ev = summarize(CONFLICTS)["evidence"]
    assert ev["chunks"] == [{"chunk_id": "c5", "doc_id": "d5"}]
    assert ev["entities"] == ["e5"]


def test_lists_are_deduplicated_and_capped():
    payload = {"rows": [{"entityData": {"_id": f"e{i % 300}"}} for i in range(600)]}
    ev = summarize(payload)["evidence"]
    assert len(ev["entities"]) == 200
    assert len(set(ev["entities"])) == 200


def test_non_dict_payloads_do_not_raise():
    assert summarize([1, 2])["rows"] == 2
    assert summarize("text")["rows"] == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_evidence.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/traces/evidence.py`:

```python
"""Row counts, truncation and evidence ids from a query command's JSON output
(spec 2026-10-11 §4, §5.3). Pure: no I/O, never raises on odd shapes.

Every `artmind query` payload is a dict whose main list is `rows` (exceptions:
`resolve-key` -> `candidates`, hierarchy discovery -> `edge_candidates`,
`views list` -> `views`). Evidence is found by shape, not by command:
a chunk is a dict with `doc_id` plus `chunk_id`, or plus `id` and `name`; a
document is a dict with `id` and `path`/`source_path` but no `doc_id`; an
entity is any `_id` string.
"""

from typing import Any

CAP = 200
_ROW_KEYS = ("rows", "candidates", "edge_candidates", "views")
_NOT_AN_ANSWER = ("needs_disambiguation", "no_match")


def summarize(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {"rows": len(payload) if isinstance(payload, list) else 0,
                "truncated": False, "rows_total": None, "evidence": {}}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows_total = payload.get("rows_total", summary.get("total"))
    return {
        "rows": _count(payload),
        "truncated": bool(payload.get("truncated") or summary.get("truncated")),
        "rows_total": rows_total if isinstance(rows_total, int) else None,
        "evidence": _evidence(payload),
    }


def _count(payload: dict) -> int:
    if payload.get("status") in _NOT_AN_ANSWER:
        return 0
    for key in _ROW_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


class _Acc:
    def __init__(self) -> None:
        self.entities: dict[str, None] = {}
        self.chunks: dict[tuple, None] = {}
        self.documents: dict[tuple, None] = {}

    def add(self, bucket: dict, key: Any) -> None:
        if len(bucket) < CAP:
            bucket.setdefault(key, None)


def _walk(node: Any, acc: _Acc) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("_id"), str):
            acc.add(acc.entities, node["_id"])
        doc_id = node.get("doc_id")
        if isinstance(doc_id, str):
            chunk_id = node.get("chunk_id")
            if chunk_id is None and "name" in node:
                chunk_id = node.get("id")
            if isinstance(chunk_id, str) and chunk_id != doc_id:
                acc.add(acc.chunks, (chunk_id, doc_id))
        else:
            path = node.get("path") or node.get("source_path")
            if isinstance(path, str) and isinstance(node.get("id"), str):
                acc.add(acc.documents, (node["id"], path))
        for value in node.values():
            _walk(value, acc)
    elif isinstance(node, list):
        for value in node:
            _walk(value, acc)


def _resolved(payload: dict) -> list[dict]:
    out = []
    if payload.get("query_type") == "entity_resolve":
        for row in payload.get("rows") or []:
            entity = row.get("entity") if isinstance(row, dict) else None
            if isinstance(entity, dict):
                out.append({"id": entity.get("_id"), "name": entity.get("name"),
                            "entity_class": entity.get("entity_class"), "score": row.get("score")})
    elif isinstance(payload.get("candidates"), list):
        for cand in payload["candidates"]:
            if isinstance(cand, dict):
                out.append({"id": cand.get("_id"), "name": cand.get("name", cand.get("value")),
                            "entity_class": cand.get("entity_class"), "score": cand.get("score")})
    return out[:CAP]


def _evidence(payload: dict) -> dict:
    acc = _Acc()
    _walk(payload, acc)
    for table in payload.get("source_tables") or []:
        if isinstance(table, dict) and isinstance(table.get("source_file"), str):
            acc.add(acc.documents, (None, table["source_file"]))

    ev: dict[str, Any] = {}
    if isinstance(payload.get("view"), str) and "version" in payload:
        ev["view"] = {"name": payload["view"], "version": payload.get("version"),
                      "status": payload.get("status"), "params": payload.get("params")}
    if isinstance(payload.get("generated_cypher"), str):
        ev["cypher"] = payload["generated_cypher"]
    if isinstance(payload.get("generated_sql"), str):
        ev["sql"] = payload["generated_sql"]
    resolved = _resolved(payload)
    if resolved:
        ev["resolved"] = resolved
    if acc.chunks:
        ev["chunks"] = [{"chunk_id": c, "doc_id": d} for c, d in acc.chunks]
    if acc.documents:
        ev["documents"] = [{"doc_id": d, "path": p} for d, p in acc.documents]
    if acc.entities:
        ev["entities"] = list(acc.entities)
    return ev
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_evidence.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/evidence.py test/test_traces_evidence.py
git commit -m "feat(traces): evidence extraction from query output payloads"
```

---

### Task 4: Retrieval path, fall-through, answer basis

**Files:**
- Create: `artmind/traces/path.py`
- Test: `test/test_traces_path.py`

Spec §4.1, with one refinement that this task also writes into the spec: when the `### Sources` links cannot be matched to any span's documents (graph patterns often return entities with no document paths), the basis falls back to **which layers produced rows**. `none` means no productive span at all.

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_path.py`:

```python
"""artmind.traces.path: layers, fall-through, answer basis (spec §4.1)."""

import pytest

from artmind.traces.path import answer_basis, derive, layer_of, parse_sources


def _span(command, rows=1, exit_code=0, started_at=0.0, documents=None):
    ev = {"documents": [{"doc_id": None, "path": p} for p in documents]} if documents else {}
    return {"span_id": f"{command}-{started_at}", "command": command, "rows": rows,
            "exit_code": exit_code, "started_at": started_at, "evidence": ev}


@pytest.mark.parametrize("command,layer", [
    ("query views run", "view"),
    ("query graph pattern5", "graph"),
    ("query graph pattern10", "graph"),
    ("query graph hierarchy", "graph"),
    ("query entity-context", "graph"),
    ("query graph text2cypher", "text2cypher"),
    ("query vector-text", "vector"),
    ("query chunks", "vector"),
    ("query text2sql", "sql"),
    ("query resolve-key", "sql"),
    ("query entity-resolve", "discovery"),
    ("query graph structural-metadata", "discovery"),
    ("query views list", "discovery"),
    ("query something-new", "discovery"),
    (None, "discovery"),
])
def test_layer_of(command, layer):
    assert layer_of(command) == layer


def test_parse_sources_angle_and_plain_links():
    answer = (
        "Alice leads it.\n\n### Sources\n"
        "- [Delivery Leads.md](<performance_management/Delivery Leads.md>) — performance_management\n"
        "- [Org.md](banking/Org.md) — banking\n\n## Other\n- [x](not/a/source.md)\n"
    )
    assert parse_sources(answer) == ["performance_management/Delivery Leads.md", "banking/Org.md"]
    assert parse_sources("no sources here") == []
    assert parse_sources(None) == []


def test_pattern_empty_then_vector_is_a_fall_through():
    spans = [_span("query entity-resolve", started_at=1), _span("query graph pattern2", rows=0, started_at=2),
             _span("query vector-text", rows=3, started_at=3, documents=["b/P.md"])]
    d = derive(spans, "x\n### Sources\n- [P](<b/P.md>) — b\n")
    assert [p["layer"] for p in d["retrieval_path"]] == ["graph", "vector"]
    assert d["graph_attempts"] == 1
    assert d["graph_productive"] == 0
    assert d["fell_through"] is True
    assert d["answer_basis"] == "vector"


def test_productive_view_without_vector_is_graph():
    d = derive([_span("query views run", rows=4, started_at=1)], "answer without sources")
    assert d["fell_through"] is False
    assert d["answer_basis"] == "graph"


def test_entity_context_then_chunks_is_mixed_when_both_cited():
    spans = [_span("query entity-context", rows=1, started_at=1, documents=["b/A.md"]),
             _span("query chunks", rows=2, started_at=2, documents=["b/B.md"])]
    answer = "### Sources\n- [A](<b/A.md>) — b\n- [B](<b/B.md>) — b\n"
    d = derive(spans, answer)
    assert d["fell_through"] is True
    assert d["answer_basis"] == "mixed"


def test_text2cypher_error_then_vector():
    spans = [_span("query graph text2cypher", rows=0, exit_code=1, started_at=1),
             _span("query vector-text", rows=2, started_at=2)]
    d = derive(spans, "no sources")
    assert d["fell_through"] is True
    assert d["graph_productive"] == 0
    assert d["answer_basis"] == "vector"


def test_discovery_only_is_none():
    d = derive([_span("query domains-overview"), _span("query entity-resolve")], "dunno")
    assert d["retrieval_path"] == []
    assert d["graph_attempts"] == 0
    assert d["fell_through"] is False
    assert d["answer_basis"] == "none"


def test_sql_only():
    d = derive([_span("query text2sql", rows=5, documents=["tables/loans.csv"])],
               "### Sources\n- [loans](<tables/loans.csv>) — b\n")
    assert d["answer_basis"] == "sql"


def test_vector_before_graph_is_not_a_fall_through():
    spans = [_span("query vector-text", started_at=1), _span("query graph pattern1", started_at=2)]
    assert derive(spans, "")["fell_through"] is False


def test_cited_doc_matches_by_suffix_and_basename():
    spans = [_span("query graph pattern2", rows=1, documents=["/abs/vault/b/Org.md"]),
             _span("query vector-text", rows=1, started_at=1, documents=["b/Other.md"])]
    assert answer_basis("### Sources\n- [Org](<b/Org.md>) — b\n", spans) == "graph"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_path.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/traces/path.py`:

```python
"""Retrieval path, graph->vector fall-through and answer basis for one turn
(spec 2026-10-11 §4.1). Pure functions over span dicts as `store.list_spans`
returns them: `command`, `rows`, `exit_code`, `started_at`, `evidence`.
"""

import re
from pathlib import PurePosixPath
from urllib.parse import unquote

GRAPH_LAYERS = frozenset({"view", "graph", "text2cypher"})

_EXACT = {
    "query views run": "view",
    "query graph text2cypher": "text2cypher",
    "query vector-text": "vector",
    "query chunks": "vector",
    "query text2sql": "sql",
    "query resolve-key": "sql",
    "query entity-context": "graph",
    "query entity-history": "graph",
    "query graph entity-listing": "graph",
    "query graph filing-listing": "graph",
    "query graph timeline": "graph",
    "query graph hierarchy": "graph",
    "query graph conflicts": "graph",
}

_SOURCES_HEADING = re.compile(r"^#{2,4}\s*Sources\s*$", re.IGNORECASE | re.MULTILINE)
_ANY_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LINK = re.compile(r"\]\(\s*(?:<([^>]+)>|([^)\s]+))\s*\)")


def layer_of(command: str | None) -> str:
    """`view` | `graph` | `text2cypher` | `vector` | `sql` | `discovery`.
    Unknown commands count as discovery: they locate things, they are not
    evidence for an answer."""
    if not command:
        return "discovery"
    if command in _EXACT:
        return _EXACT[command]
    if command.startswith("query graph pattern"):
        return "graph"
    return "discovery"


def parse_sources(answer: str | None) -> list[str]:
    """Link targets listed under the answer's `### Sources` heading."""
    if not answer:
        return []
    match = _SOURCES_HEADING.search(answer)
    if not match:
        return []
    rest = answer[match.end():]
    nxt = _ANY_HEADING.search(rest)
    block = rest[: nxt.start()] if nxt else rest
    return [unquote(a or b) for a, b in _LINK.findall(block)]


def _norm(path: str) -> str:
    return unquote(path).strip().removeprefix("./").replace("\\", "/")


def _same_document(cited: str, held: str) -> bool:
    a, b = _norm(cited), _norm(held)
    if a == b or a.endswith("/" + b) or b.endswith("/" + a):
        return True
    return PurePosixPath(a).name == PurePosixPath(b).name


def _group(layer: str) -> str:
    return "graph" if layer in GRAPH_LAYERS else layer


def _productive(span: dict) -> bool:
    return span.get("exit_code") == 0 and (span.get("rows") or 0) > 0


def _basis_from(groups: set[str]) -> str:
    if not groups:
        return "none"
    if groups == {"sql"}:
        return "sql"
    if groups == {"graph"}:
        return "graph"
    if groups == {"vector"}:
        return "vector"
    return "mixed"


def answer_basis(answer: str | None, spans: list[dict]) -> str:
    productive = [s for s in spans if _productive(s) and layer_of(s.get("command")) != "discovery"]
    if not productive:
        return "none"
    found: set[str] = set()
    for cited in parse_sources(answer):
        for span in productive:
            docs = (span.get("evidence") or {}).get("documents") or []
            if any(_same_document(cited, d.get("path") or "") for d in docs):
                found.add(_group(layer_of(span["command"])))
    if found:
        return _basis_from(found)
    return _basis_from({_group(layer_of(s["command"])) for s in productive})


def derive(spans: list[dict], answer: str | None) -> dict:
    ordered = sorted(spans, key=lambda s: s.get("started_at") or 0.0)
    path = []
    for span in ordered:
        layer = layer_of(span.get("command"))
        if layer == "discovery":
            continue
        path.append({"span_id": span.get("span_id"), "layer": layer, "command": span.get("command"),
                     "rows": span.get("rows") or 0, "productive": _productive(span)})
    graph_idx = [i for i, step in enumerate(path) if step["layer"] in GRAPH_LAYERS]
    fell_through = bool(graph_idx) and any(step["layer"] == "vector" for step in path[graph_idx[-1] + 1:])
    return {
        "retrieval_path": path,
        "graph_attempts": len(graph_idx),
        "graph_productive": sum(1 for i in graph_idx if path[i]["productive"]),
        "fell_through": fell_through,
        "answer_basis": answer_basis(answer, ordered),
    }


def is_graph_miss(turn: dict) -> bool:
    return bool(turn.get("fell_through")) and turn.get("answer_basis") == "vector"
```

- [ ] **Step 4: Update the spec's answer-basis paragraph**

In `docs/superpowers/specs/2026-10-11-query-review-loop-design.md` §4.1, replace the bullet that begins `- The basis is \`vector\` when every cited document` and ends `is \`none\`.` with:

```markdown
- The basis is `vector` when every cited document came only from `vector`
  spans, and `graph` when every one came only from `view`/`graph`/
  `text2cypher` spans. A mix of both is `mixed`. Structured-store answers
  are `sql`.
- When no cited document can be matched to a span (graph patterns often
  return entities with no document paths), or the answer has no Sources
  section, the basis is taken from the layers that returned rows. It is
  `none` only when no retrieval span returned rows.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_path.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/traces/path.py test/test_traces_path.py docs/superpowers/specs/2026-10-11-query-review-loop-design.md
git commit -m "feat(traces): retrieval layers, graph-to-vector fall-through, answer basis"
```

---

### Task 5: Trace-id resolution in `_entry` (stdlib-only)

**Files:**
- Modify: `artmind/_entry.py`
- Test: `test/test_traces_proxy.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_proxy.py`:

```python
"""Trace id resolution and forwarding through the `serve` proxy (spec §5.2, §5.4)."""

import io
import json

from artmind import _entry


def test_trace_id_prefers_explicit_env(monkeypatch, tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("from-file")
    monkeypatch.setenv("ARTMIND_TRACE_ID", "explicit")
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    assert _entry._trace_id() == "explicit"


def test_trace_id_reads_context_file(monkeypatch, tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("abc123\n")
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    assert _entry._trace_id() == "abc123"


def test_empty_or_missing_context_file_means_no_trace(monkeypatch, tmp_path):
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    assert _entry._trace_id() is None
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(tmp_path / "missing.id"))
    assert _entry._trace_id() is None
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT")
    assert _entry._trace_id() is None


def test_proxy_payload_carries_trace_id(monkeypatch, tmp_path, capsys):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("t-42")
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    sent = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        sent["body"] = json.loads(request.data)
        return _Resp(json.dumps({"exit_code": 0, "output": "{}\n", "stderr": ""}).encode())

    monkeypatch.setattr(_entry.urllib.request, "urlopen", fake_urlopen)
    assert _entry._proxy("http://127.0.0.1:1", ["query", "chunks"]) == 0
    assert sent["body"] == {"args": ["query", "chunks"], "trace_id": "t-42"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_proxy.py -v`
Expected: FAIL with `AttributeError: module 'artmind._entry' has no attribute '_trace_id'`.

- [ ] **Step 3: Implement**

In `artmind/_entry.py`, after `QUERY_TIMEOUT = 600`, add:

```python
#: Query-trace id sources (docs/superpowers/specs/2026-10-11-query-review-loop-design.md
#: §5.2): an explicit id, else the per-session file the admin console rewrites
#: at the start of every turn (the agent process outlives a turn, so the id
#: cannot travel in its environment).
TRACE_ID_ENV = "ARTMIND_TRACE_ID"
TRACE_CONTEXT_ENV = "ARTMIND_TRACE_CONTEXT"


def _trace_id() -> str | None:
    """The query-trace id this process should record under, or None. Never raises."""
    explicit = os.environ.get(TRACE_ID_ENV, "").strip()
    if explicit:
        return explicit
    path = os.environ.get(TRACE_CONTEXT_ENV)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None
```

In `_proxy`, change the first line to:

```python
    payload = json.dumps({"args": args, "trace_id": _trace_id()}).encode()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_proxy.py test/test_entry_proxy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/_entry.py test/test_traces_proxy.py
git commit -m "feat(traces): resolve the trace id in _entry and forward it to serve"
```

---

### Task 6: CLI spans — `TracedGroup`, `observe_output`, `record_llm_call`

**Files:**
- Create: `artmind/traces/span.py`
- Modify: `artmind/cli.py` (the imports, `_echo_json` at line ~290, `@cli.group()` above `def query()` at line ~2432)
- Modify: `utils/functions.py` (`log_llm_call`, line ~58)
- Test: `test/test_traces_span.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_span.py`:

```python
"""CLI-side capture: one span per `artmind query` call (spec §5.2, §5.3, §5.5)."""

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)


VECTOR_PAYLOAD = {
    "query_type": "vector_text", "question": "who", "rows": [
        {"score": 0.9, "chunk": {"id": "c1", "name": "c1", "doc_id": "d1", "text": "t"},
         "document": {"id": "d1", "name": "P", "path": "b/P.md", "source_path": "b/P.md"}}],
}


def _spans(trace_id="t1"):
    from artmind.traces import store

    return store.list_spans(trace_id)


def test_traced_query_records_command_params_and_evidence(monkeypatch):
    from artmind import cli as cli_mod
    from artmind.traces import store

    store.insert_turn("t1", source="admin", question="q")
    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", lambda *a, **k: VECTOR_PAYLOAD)
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")

    result = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "who"])

    assert result.exit_code == 0, result.output
    spans = _spans()
    assert len(spans) == 1
    span = spans[0]
    assert span["command"] == "query vector-text"
    assert span["args"]["domain"] == ["b"]
    assert span["args"]["question"] == "who"
    assert span["exit_code"] == 0
    assert span["rows"] == 1
    assert span["evidence"]["chunks"] == [{"chunk_id": "c1", "doc_id": "d1"}]
    assert span["evidence"]["documents"] == [{"doc_id": "d1", "path": "b/P.md"}]


def test_nested_group_command_path(monkeypatch):
    from artmind import cli as cli_mod

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda: {"rows": []})
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    CliRunner().invoke(cli_mod.cli, ["query", "domains-overview"])
    assert [s["command"] for s in _spans()] == ["query domains-overview"]


def test_failed_command_still_records_a_span(monkeypatch):
    from artmind import cli as cli_mod

    def boom(*a, **k):
        raise ValueError("bad question")

    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", boom)
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    result = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "who"])
    assert result.exit_code == 1
    span = _spans()[0]
    assert span["exit_code"] == 1
    assert span["rows"] == 0
    assert span["command"] == "query vector-text"
    assert span["args"] == ["vector-text", "--domain", "b", "who"]


def test_no_trace_id_writes_nothing_and_opens_no_db(monkeypatch):
    from artmind import cli as cli_mod
    from artmind.traces import store

    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", lambda *a, **k: VECTOR_PAYLOAD)
    opened = []
    monkeypatch.setattr(store, "_connect", lambda: opened.append(1))
    result = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "who"])
    assert result.exit_code == 0
    assert opened == []


def test_help_is_not_spanned(monkeypatch):
    from artmind import cli as cli_mod

    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--help"])
    assert _spans() == []


def test_capture_failure_leaves_output_and_exit_code_unchanged(monkeypatch):
    from artmind import cli as cli_mod
    from artmind.traces import store

    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", lambda *a, **k: VECTOR_PAYLOAD)
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "insert_span", broken)
    traced = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "--compact", "who"])
    monkeypatch.delenv("ARTMIND_TRACE_ID")
    plain = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "--compact", "who"])
    assert traced.exit_code == plain.exit_code == 0
    assert traced.stdout == plain.stdout


def test_llm_calls_inside_a_span_are_recorded(monkeypatch):
    from artmind import cli as cli_mod
    from utils.functions import log_llm_call

    def fake_search(*a, **k):
        log_llm_call("chat", "m1", "PROMPT", "RESPONSE")
        return VECTOR_PAYLOAD

    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", fake_search)
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "who"])
    from artmind.traces import store

    span = _spans()[0]
    calls = store.list_llm_calls([span["span_id"]])[span["span_id"]]
    assert [(c["call_type"], c["model"], c["prompt"], c["response"]) for c in calls] == [
        ("chat", "m1", "PROMPT", "RESPONSE")]


def test_override_beats_environment(monkeypatch):
    from artmind.traces.span import current_trace_id, trace_override

    monkeypatch.setenv("ARTMIND_TRACE_ID", "from-env")
    with trace_override(None):
        assert current_trace_id() is None
    with trace_override("from-serve"):
        assert current_trace_id() == "from-serve"
    assert current_trace_id() == "from-env"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_span.py -v`
Expected: FAIL (`artmind.traces.span` is missing, and no spans are recorded).

- [ ] **Step 3: Implement `artmind/traces/span.py`**

```python
"""CLI side of trace capture (spec 2026-10-11 §5.2-§5.5): one span per
`artmind query` invocation, run in-process or inside `serve`.

`TracedGroup` wraps the `query` group's invoke: it opens a span when a trace
id resolves, sees the exit code from whatever the subcommand raised, and
writes the span in `finally`. `_echo_json` hands the payload it prints to
`observe_output`; `log_llm_call` hands each LLM call to `record_llm_call`.
With no trace id, nothing here touches the DB. Every write is swallowed --
capture must never change a query's output or exit code.
"""

import contextvars
import json
import time
import uuid
from contextlib import contextmanager

import click
from rich_click import RichGroup

from artmind._entry import _trace_id as _env_trace_id
from artmind.traces import evidence

_UNSET = object()
_override: contextvars.ContextVar = contextvars.ContextVar("artmind_trace_override", default=_UNSET)
_active: contextvars.ContextVar = contextvars.ContextVar("artmind_trace_span", default=None)


def current_trace_id() -> str | None:
    """The in-process override when one is set (even to None: `serve` never
    reads its own environment), else ARTMIND_TRACE_ID / ARTMIND_TRACE_CONTEXT."""
    value = _override.get()
    if value is not _UNSET:
        return value
    return _env_trace_id()


@contextmanager
def trace_override(trace_id: str | None):
    token = _override.set(trace_id or None)
    try:
        yield
    finally:
        _override.reset(token)


def _command_path(group: click.Group, ctx: click.Context, tokens: list[str]) -> str:
    path = [group.name or "query"]
    cmd: click.Command = group
    for token in tokens:
        if token.startswith("-") or not isinstance(cmd, click.Group):
            break
        sub = cmd.get_command(ctx, token)
        if sub is None:
            break
        path.append(token)
        cmd = sub
    return " ".join(path)


class TracedGroup(RichGroup):
    """The `query` group. Nested groups (`query graph`, `query views`) are
    plain RichGroups -- RichGroup's `group_class = type` would otherwise make
    them TracedGroups too -- and run inside this group's span."""

    group_class = RichGroup

    def invoke(self, ctx: click.Context):
        if _active.get() is not None:
            return super().invoke(ctx)
        tokens = [*getattr(ctx, "_protected_args", []), *ctx.args]
        try:
            trace_id = None if "--help" in tokens else current_trace_id()
        except Exception:
            trace_id = None
        if not trace_id:
            return super().invoke(ctx)

        span = {
            "span_id": uuid.uuid4().hex, "trace_id": trace_id, "argv": list(tokens),
            "command": _command_path(self, ctx, tokens), "started_at": time.time(),
            "summary": None, "params": None, "llm_calls": [],
        }
        token = _active.set(span)
        exit_code = 0
        try:
            return super().invoke(ctx)
        except click.exceptions.Exit as exc:
            exit_code = exc.exit_code
            raise
        except click.ClickException as exc:
            exit_code = exc.exit_code
            raise
        except BaseException:
            exit_code = 1
            raise
        finally:
            _active.reset(token)
            _record(span, exit_code)


def observe_output(payload) -> None:
    """Called by `cli._echo_json` with the payload it is about to print."""
    span = _active.get()
    if span is None:
        return
    try:
        span["summary"] = evidence.summarize(payload)
        ctx = click.get_current_context(silent=True)
        if ctx is not None:
            span["params"] = dict(ctx.params)
    except Exception:
        pass


def record_llm_call(call_type: str, model: str, prompt: str, response: str) -> None:
    """Called by `utils.functions.log_llm_call`; a no-op outside a span."""
    span = _active.get()
    if span is None:
        return
    span["llm_calls"].append({"seq": len(span["llm_calls"]), "call_type": call_type, "model": model,
                              "prompt": prompt, "response": response, "duration_ms": None})


def _record(span: dict, exit_code: int | None) -> None:
    try:
        from artmind.traces import store

        summary = span["summary"] or {"rows": 0, "truncated": False, "rows_total": None, "evidence": {}}
        args = span["params"] if span["params"] is not None else span["argv"]
        store.insert_span(
            {
                "span_id": span["span_id"], "trace_id": span["trace_id"], "command": span["command"],
                "args": json.dumps(args, default=str), "exit_code": exit_code if exit_code is not None else 0,
                "duration_ms": int((time.time() - span["started_at"]) * 1000),
                "rows": summary["rows"], "rows_total": summary["rows_total"],
                "truncated": int(bool(summary["truncated"])),
                "evidence": json.dumps(summary["evidence"], default=str),
                "started_at": span["started_at"],
            },
            span["llm_calls"],
        )
    except Exception:
        pass
```

Note on `args`: on success it is the leaf context's `params`, a dict whose `domain` value is a tuple and serializes as a JSON list. On failure before output it is the raw argv after `query`, exactly as the user typed it.

- [ ] **Step 4: Wire it into `artmind/cli.py`**

1. Next to the other module imports near the top of `artmind/cli.py`, add:

   ```python
   from artmind.traces.span import TracedGroup, observe_output
   ```

2. Make `_echo_json`'s first statement:

   ```python
   def _echo_json(payload: dict, compact: bool = False) -> None:
       observe_output(payload)  # query-trace span evidence; a no-op outside a traced query
       # DuckDB DATE/TIMESTAMP/DECIMAL values arrive here straight off a result set
   ```

   Keep the rest of the function unchanged.

3. Change the decorator above `def query():` from `@cli.group()` to:

   ```python
   @cli.group(cls=TracedGroup)
   def query():
   ```

   Then append one paragraph to the `query` docstring, before its closing `"""`:

   ```text
       Inside the admin console or a benchmark run, every query call is recorded
       as a span of the turn's trace (command, args, rows, evidence ids, LLM
       calls). Read them with `artmind traces`.
   ```

- [ ] **Step 5: Wire up `log_llm_call`**

In `utils/functions.py`, append to the end of `log_llm_call` (after the `with _LLM_LOG_LOCK:` block):

```python
    # The same call, attributed to the query-trace span it ran in (spec
    # 2026-10-11 §5.5); a no-op outside a traced `artmind query`.
    try:
        from artmind.traces.span import record_llm_call

        record_llm_call(call_type, model, prompt, response)
    except Exception:
        pass
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_span.py test/test_entry_proxy.py test/test_cli_guide.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

`_echo_json` and the `query` group sit under every command. Run: `uv run --group dev pytest test/ -q`
Expected: the same pass count as on `master`, plus the new tests. If anything fails, fix it before you continue.

- [ ] **Step 8: Commit**

```bash
git add artmind/traces/span.py artmind/cli.py utils/functions.py test/test_traces_span.py
git commit -m "feat(traces): a span per artmind query call, with output evidence and LLM calls"
```

---

### Task 7: `serve` honours the forwarded trace id

**Files:**
- Modify: `artmind/server.py`
- Test: `test/test_traces_proxy.py` (append)

- [ ] **Step 1: Write the failing tests** (append)

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def _db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")


def test_serve_records_span_under_forwarded_trace_id(_db, monkeypatch):
    from artmind import cli as cli_mod
    from artmind.server import create_app
    from artmind.traces import store
    from artmind.traces.span import current_trace_id

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda: {"rows": [{"domain": "b"}]})
    client = TestClient(create_app())
    resp = client.post("/query", json={"args": ["query", "domains-overview"], "trace_id": "t-serve"})
    assert resp.status_code == 200
    assert resp.json()["exit_code"] == 0
    spans = store.list_spans("t-serve")
    assert [(s["command"], s["rows"]) for s in spans] == [("query domains-overview", 1)]
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)
    assert current_trace_id() is None  # override cleared after the request


def test_serve_ignores_its_own_environment(_db, monkeypatch):
    from artmind import cli as cli_mod
    from artmind.server import create_app
    from artmind.traces import store

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda: {"rows": []})
    monkeypatch.setenv("ARTMIND_TRACE_ID", "daemon-env")
    TestClient(create_app()).post("/query", json={"args": ["query", "domains-overview"]})
    assert store.list_spans("daemon-env") == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_proxy.py -v`
Expected: FAIL. The first test finds no spans, because `trace_id` is ignored. The second finds one span, because the daemon reads its own environment.

- [ ] **Step 3: Implement**

In `artmind/server.py`:

```python
from artmind.traces.span import trace_override
```

```python
class QueryRequest(BaseModel):
    args: list[str]
    #: The caller's query-trace id (spec 2026-10-11 §5.4), forwarded by
    #: `_entry._proxy`. The daemon never reads a trace id from its own env.
    trace_id: str | None = None
```

```python
def run_cli_args(args: list[str], trace_id: str | None = None) -> dict:
    if not args or args[0] != "query":
        raise ValueError("only 'artmind query ...' commands are served")
    with _invoke_lock, trace_override(trace_id):
        result = CliRunner().invoke(cli, args)
```

Leave the rest of `run_cli_args` unchanged. In the `/query` route:

```python
            return run_cli_args(request.args, request.trace_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_proxy.py test/test_entry_proxy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/server.py test/test_traces_proxy.py
git commit -m "feat(traces): serve records spans under the forwarded trace id only"
```

---

### Task 8: Mappers carry unclipped `_trace` data when capturing

**Files:**
- Modify: `artmind/webui/agent.py` (`EventMapper`, line ~206)
- Modify: `artmind/webui/backends/acp_events.py`
- Test: `test/test_traces_mappers.py`

`capture` defaults to `False`, so every existing mapper test (exact-dict assertions in `test/test_webui_events.py` and `test/test_webui_acp_mapper.py`) is untouched.

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_mappers.py`:

```python
"""Mappers add a private `_trace` key with unclipped data only when capturing."""

from claude_agent_sdk.types import AssistantMessage, ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage

from artmind.webui.agent import EventMapper
from artmind.webui.backends.acp_events import ACPEventMapper
from artmind.webui.backends.base import TRACE_CLIP

LONG = "x" * (TRACE_CLIP * 3)


def _assistant_tool_call():
    return AssistantMessage(content=[ToolUseBlock(id="tu1", name="Bash", input={"command": LONG})],
                            model="claude-test", parent_tool_use_id=None)


def test_claude_mapper_without_capture_has_no_trace_key():
    events = EventMapper().map(_assistant_tool_call())
    assert "_trace" not in events[0]


def test_claude_mapper_capture_keeps_raw_input_result_and_usage():
    mapper = EventMapper(capture=True)
    call = mapper.map(_assistant_tool_call())[0]
    assert len(call["input"]) < len(LONG)  # the UI copy is still clipped
    assert call["_trace"] == {"input": {"command": LONG}, "parent_tool_id": None}

    result = mapper.map(UserMessage(content=[ToolResultBlock(tool_use_id="tu1", content=LONG)]))[0]
    assert result["_trace"]["content"] == LONG

    done = mapper.map(ResultMessage(subtype="success", duration_ms=1500, duration_api_ms=1000, is_error=False,
                                    num_turns=4, session_id="s", total_cost_usd=0.02,
                                    usage={"input_tokens": 10, "output_tokens": 5}, result="final"))[0]
    assert done["type"] == "turn_done"
    assert done["_trace"] == {"usage": {"input_tokens": 10, "output_tokens": 5}, "model": "claude-test",
                              "result": "final"}


def test_acp_mapper_capture_keeps_raw_input_and_output():
    mapper = ACPEventMapper(capture=True)
    call = mapper.map_update({"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "bash",
                              "rawInput": {"command": LONG}})[0]
    assert call["_trace"] == {"input": {"command": LONG}}
    upd = mapper.map_update({"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "in_progress",
                             "rawInput": {"command": "artmind query chunks"}})[0]
    assert upd["_trace"] == {"input": {"command": "artmind query chunks"}}
    res = mapper.map_update({"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
                             "rawOutput": LONG})[0]
    assert res["_trace"]["content"] == LONG
    assert len(res["content"]) < len(LONG)


def test_acp_mapper_without_capture_unchanged():
    call = ACPEventMapper().map_update({"sessionUpdate": "tool_call", "toolCallId": "c1", "rawInput": {}})[0]
    assert "_trace" not in call
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_mappers.py -v`
Expected: FAIL with `TypeError: EventMapper.__init__() got an unexpected keyword argument 'capture'`.

- [ ] **Step 3: Implement `EventMapper(capture=...)`**

In `artmind/webui/agent.py`, replace `EventMapper.__init__` and `map` with:

```python
    def __init__(self, capture: bool = False) -> None:
        self._block_types: dict[int, str] = {}
        # Query-trace capture (spec 2026-10-11 §5.1): add a private `_trace`
        # key with the unclipped data; TraceRecorder strips it before SSE.
        self._capture = capture
        self._model: str | None = None

    def map(self, message: Any) -> list[dict[str, Any]]:
        if isinstance(message, StreamEvent):
            return self._map_stream_event(message)
        if isinstance(message, AssistantMessage):
            # Normal text/thinking content already arrived via stream deltas.
            # A synthetic error response (e.g. auth failure) bypasses streaming
            # entirely, so its text is only ever available here.
            self._model = message.model or self._model
            events: list[dict[str, Any]] = []
            if message.error:
                text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
                events.append({"type": "error", "message": text or message.error})
            for b in message.content:
                if isinstance(b, ToolUseBlock):
                    event = {"type": "tool_call", "id": b.id, "name": b.name, "input": clip(b.input)}
                    if self._capture:
                        event["_trace"] = {"input": b.input, "parent_tool_id": message.parent_tool_use_id}
                    events.append(event)
            return events
        if isinstance(message, UserMessage):
            content = message.content if isinstance(message.content, list) else []
            events = []
            for b in content:
                if isinstance(b, ToolResultBlock):
                    event = {"type": "tool_result", "tool_id": b.tool_use_id, "content": clip(b.content)}
                    if self._capture:
                        event["_trace"] = {"content": b.content, "parent_tool_id": message.parent_tool_use_id}
                    events.append(event)
            return events
        if isinstance(message, ResultMessage):
            event = {
                "type": "turn_done",
                "turns": message.num_turns,
                "duration_s": round(message.duration_ms / 1000, 1),
                "cost": message.total_cost_usd,
            }
            if self._capture:
                event["_trace"] = {"usage": message.usage, "model": self._model, "result": message.result}
            return [event]
        return []
```

- [ ] **Step 4: Implement `ACPEventMapper(capture=...)`**

In `artmind/webui/backends/acp_events.py`:

1. Change `_tool_output` so it can return unclipped text:

   ```python
   def _tool_output(update: dict[str, Any], *, clipped: bool = True) -> str:
       """Best-effort text from a tool_call_update's content or rawOutput."""
       finish = clip if clipped else (lambda v: v if isinstance(v, str) else json.dumps(v, default=str))
       parts = []
       for item in update.get("content") or []:
           if isinstance(item, dict) and item.get("type") == "content":
               parts.append(_content_text(item.get("content")))
           elif isinstance(item, dict) and item.get("type") == "diff":
               parts.append(f"[diff] {item.get('path', '')}")
       if parts:
           return finish("\n".join(parts))
       if "rawOutput" in update:
           return finish(update["rawOutput"])
       return ""
   ```

   Add `import json` at the top of the module.

2. Give `ACPEventMapper.__init__` a `capture` flag:

   ```python
       def __init__(self, capture: bool = False) -> None:
           self._open_block: str | None = None
           self._capture = capture
   ```

3. In `map_update`, after building each of the three tool events, add the `_trace` key when capturing. The `tool_call` branch becomes:

   ```python
           if kind == "tool_call":
               event = {
                   "type": "tool_call",
                   "id": update.get("toolCallId", ""),
                   "name": update.get("title") or update.get("kind") or "tool",
                   "input": clip(update.get("rawInput", {})),
               }
               if self._capture:
                   event["_trace"] = {"input": update.get("rawInput", {})}
               events.append(event)
   ```

   In the `in_progress` branch, build the `tool_call_update` dict as `event`. If capturing, set `event["_trace"] = {"input": update["rawInput"]}`, then append it.

   In the `completed` / `failed` branch, build the `tool_result` dict as `event`. If capturing, set:

   ```python
   raw = _tool_output(update, clipped=False)
   event["_trace"] = {"content": f"[failed] {raw}".rstrip() if status == "failed" else raw}
   ```

   Then append it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_mappers.py test/test_webui_events.py test/test_webui_acp_mapper.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/webui/agent.py artmind/webui/backends/acp_events.py test/test_traces_mappers.py
git commit -m "feat(traces): mappers keep unclipped tool I/O and usage when capturing"
```

---

### Task 9: Backends take a trace-context file

**Files:**
- Modify: `artmind/webui/backends/__init__.py`, `claude_sdk.py`, `acp.py`
- Test: `test/test_traces_backends.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_backends.py`:

```python
"""Backends spawn with ARTMIND_TRACE_CONTEXT when given a trace-context file."""

from artmind.webui import backends
from artmind.webui.profiles import ADMIN_PROFILE


def test_new_trace_context_creates_empty_file_under_logs(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    ctx = backends.new_trace_context()
    assert ctx.parent == tmp_path / "logs" / "trace-context"
    assert ctx.read_text() == ""


def test_claude_sdk_backend_env_and_path(tmp_path):
    ctx = tmp_path / "c.id"
    backend = backends.create_backend("claude-sdk", ADMIN_PROFILE, trace_context=ctx)
    assert backend.trace_context_path == ctx
    assert backend._env["ARTMIND_TRACE_CONTEXT"] == str(ctx)


def test_claude_sdk_backend_untraced_by_default():
    backend = backends.create_backend("claude-sdk")
    assert backend.trace_context_path is None
    assert "ARTMIND_TRACE_CONTEXT" not in (backend._env or {})


def test_acp_backend_env_and_path(tmp_path):
    ctx = tmp_path / "c.id"
    backend = backends.create_backend("acp", ADMIN_PROFILE, trace_context=ctx)
    assert backend.trace_context_path == ctx
    assert backend._subprocess_env()["ARTMIND_TRACE_CONTEXT"] == str(ctx)


def test_backend_factory_trace_gives_each_backend_its_own_file(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    factory = backends.backend_factory(ADMIN_PROFILE, trace=True)
    a, b = factory("claude-sdk"), factory("claude-sdk")
    assert a.trace_context_path != b.trace_context_path
    assert backends.backend_factory(ADMIN_PROFILE)("claude-sdk").trace_context_path is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_backends.py -v`
Expected: FAIL (`new_trace_context` is missing, and `create_backend` does not accept the `trace_context` keyword).

- [ ] **Step 3: Implement `backends/__init__.py`**

Add the imports `import uuid` and `from pathlib import Path`. Then add:

```python
def new_trace_context() -> Path:
    """A fresh, empty per-session trace-context file (spec 2026-10-11 §5.1).
    The admin console writes each turn's trace id into it; the agent's Bash
    subprocesses find it via ARTMIND_TRACE_CONTEXT. Machine-local (logs/)."""
    import paths

    directory = paths.LOGS_DIR / "trace-context"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.id"
    path.write_text("")
    return path
```

Change `create_backend`'s signature to add `trace_context: Path | None = None` after `allowed_tools`. Add this sentence to its docstring: "`trace_context`: a file from `new_trace_context()`; the agent is spawned with `ARTMIND_TRACE_CONTEXT` pointing at it, so every `artmind query` it runs is recorded under the turn's trace."

In the body, before the `if name == "claude-sdk":` branch:

```python
    from artmind._entry import TRACE_CONTEXT_ENV

    trace_env = {TRACE_CONTEXT_ENV: str(trace_context)} if trace_context else {}
```

In the claude-sdk branch, pass:

```python
            env={**_sdk_env_overrides(), **trace_env} or None,
            trace_context=trace_context,
```

These replace the existing `env=_sdk_env_overrides() or None,` line.

In the acp branch, add these to the `ACPBackend(...)` call:

```python
            extra_env=trace_env or None,
            trace_context=trace_context,
```

Replace `backend_factory` with:

```python
def backend_factory(
    profile: AgentProfile = QA_PROFILE, *, trace: bool = False
) -> Callable[[str], AgentBackend]:
    """A ``SessionRegistry`` client_factory bound to ``profile``.

    Lets each front-end app pick its persona once — ``create_app`` and the
    registry stay profile-agnostic:

        create_app(SessionRegistry(client_factory=backend_factory(ADMIN_PROFILE)))

    ``trace=True`` gives every backend its own trace-context file (the admin
    console's query-trace capture, spec 2026-10-11 §5.1).
    """
    if not trace:
        return lambda name: create_backend(name, profile)
    return lambda name: create_backend(name, profile, trace_context=new_trace_context())
```

Add `"new_trace_context"` to `__all__`.

- [ ] **Step 4: Implement `ClaudeSDKBackend`**

In `artmind/webui/backends/claude_sdk.py`:
- Add `from pathlib import Path`.
- Add the keyword parameter `trace_context: Path | None = None` after `env`, and store it as `self._trace_context = trace_context`.
- Add a property:

  ```python
      @property
      def trace_context_path(self) -> Path | None:
          return self._trace_context
  ```

- In `receive_events`, change `mapper = EventMapper()` to `mapper = EventMapper(capture=self._trace_context is not None)`.
- In `disconnect`, after `await self._client.disconnect()`:

  ```python
          if self._trace_context is not None:
              self._trace_context.unlink(missing_ok=True)
  ```

`restart` calls `self._client.disconnect()`, not `self.disconnect()`, so a refresh keeps the file.

- [ ] **Step 5: Implement `ACPBackend`**

In `artmind/webui/backends/acp.py`:
- Add `from pathlib import Path` if it isn't imported already.
- Add the parameters `extra_env: dict[str, str] | None = None, trace_context: Path | None = None` before `connect_timeout_s`, and store them as `self._extra_env = extra_env` and `self._trace_context = trace_context`.
- Add the same `trace_context_path` property as in Step 4.
- In `_subprocess_env`, directly after `changed = False`:

  ```python
          if self._extra_env:
              env.update(self._extra_env)
              changed = True
  ```

- Change `self._mapper = ACPEventMapper()` (line ~111) to `self._mapper = ACPEventMapper(capture=self._trace_context is not None)`.
- At the end of `disconnect`, add:

  ```python
          if self._trace_context is not None:
              self._trace_context.unlink(missing_ok=True)
  ```

  If `restart` calls `self.disconnect()`, the file is unlinked there. That is harmless, because the app rewrites the file at the start of every turn and the env var keeps the same path.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_backends.py test/test_webui_profiles.py test/test_webui_acp_backend.py test/test_webui_session_resume.py test/test_webui_acp_sdk_free.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add artmind/webui/backends/ test/test_traces_backends.py
git commit -m "feat(traces): backends spawn with a per-session trace-context file"
```

---

### Task 10: `TraceRecorder`

**Files:**
- Create: `artmind/traces/recorder.py`
- Test: `test/test_traces_recorder.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_recorder.py`:

```python
"""TraceRecorder: turn-side capture, tool-id matching, derived columns."""

import json

import pytest

from artmind.traces import store
from artmind.traces.recorder import TraceRecorder, domains_of, match_tool_ids, public_event


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _span(trace_id, span_id, command, started_at, rows=1, documents=None, domain=("b",)):
    store.insert_span({
        "span_id": span_id, "trace_id": trace_id, "command": command,
        "args": json.dumps({"domain": list(domain)}), "exit_code": 0, "duration_ms": 5, "rows": rows,
        "rows_total": None, "truncated": 0,
        "evidence": json.dumps({"documents": [{"doc_id": None, "path": p} for p in documents or []]}),
        "started_at": started_at,
    }, [])


def test_public_event_strips_trace_key_without_mutating():
    event = {"type": "tool_call", "input": "x", "_trace": {"input": "xx"}}
    assert public_event(event) == {"type": "tool_call", "input": "x"}
    assert "_trace" in event


def test_start_writes_context_file_and_running_row(tmp_path):
    ctx = tmp_path / "c.id"
    ctx.write_text("")
    rec = TraceRecorder.start(source="admin", question="Who?", session_id="s", profile="admin",
                              backend="claude-sdk", context_path=ctx)
    assert rec.enabled
    assert ctx.read_text() == rec.trace_id
    assert store.get_turn(rec.trace_id)["status"] == "running"


def test_start_failure_yields_disabled_recorder(monkeypatch):
    monkeypatch.setattr(store, "insert_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    rec = TraceRecorder.start(source="admin", question="Who?")
    assert not rec.enabled
    assert rec.observe({"type": "text_delta", "text": "a", "_trace": {}}) == {"type": "text_delta", "text": "a"}
    rec.finish()  # no-op, no raise


def test_full_turn_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.traces.recorder.graph_state", lambda domains: {"schemas": {}, "domains": domains})
    ctx = tmp_path / "c.id"
    rec = TraceRecorder.start(source="admin", question="Who leads X?", context_path=ctx)

    long_input = {"command": "artmind query vector-text --domain b 'who leads'" + " " * 2000}
    events = [
        {"type": "thinking_delta", "text": "plan"},
        {"type": "block_done", "block": "thinking"},
        {"type": "tool_call", "id": "tu1", "name": "Bash", "input": "clipped", "_trace": {"input": long_input, "parent_tool_id": None}},
        {"type": "tool_result", "tool_id": "tu1", "content": "clipped", "_trace": {"content": "FULL OUTPUT"}},
        {"type": "text_delta", "text": "Alice.\n\n### Sources\n- [P](<b/P.md>) — b\n"},
        {"type": "block_done", "block": "text"},
        {"type": "turn_done", "turns": 3, "duration_s": 4.2, "cost": 0.05,
         "_trace": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20},
                    "model": "claude-x", "result": "Alice."}},
    ]
    out = [rec.observe(e) for e in events]
    assert all("_trace" not in e for e in out)

    # the CLI wrote its span while the tool call was running
    window = rec._tools["tu1"]
    _span(rec.trace_id, "sp1", "query vector-text",
          started_at=(window["call_ts"] + window["result_ts"]) / 2, rows=2, documents=["b/P.md"])
    rec.finish()

    turn = store.get_turn(rec.trace_id)
    assert turn["status"] == "done"
    assert turn["answer"].startswith("Alice.")
    assert (turn["turns"], turn["duration_s"], turn["cost_usd"]) == (3, 4.2, 0.05)
    assert (turn["input_tokens"], turn["output_tokens"], turn["model"]) == (150, 20, "claude-x")
    assert turn["domains"] == ["b"]
    assert turn["answer_basis"] == "vector"
    assert turn["fell_through"] == 0  # no graph attempt, so not a fall-through
    assert turn["retrieval_path"][0]["command"] == "query vector-text"

    recorded = store.list_events(rec.trace_id)
    assert [e["type"] for e in recorded] == ["thinking", "tool_call", "tool_result", "text"]
    assert json.loads(recorded[1]["payload"]) == long_input  # unclipped
    assert recorded[2]["payload"] == "FULL OUTPUT"
    assert store.list_spans(rec.trace_id)[0]["tool_id"] == "tu1"
    assert ctx.read_text() == ""  # cleared at turn end


def test_tool_call_update_replaces_input():
    rec = TraceRecorder.start(source="admin", question="q")
    rec.observe({"type": "tool_call", "id": "c1", "name": "bash", "input": "{}", "_trace": {"input": {"cwd": "/v"}}})
    rec.observe({"type": "tool_call_update", "id": "c1", "name": "bash", "input": "x",
                 "_trace": {"input": {"command": "artmind query chunks"}}})
    rec.finish()
    events = store.list_events(rec.trace_id)
    assert [e["type"] for e in events] == ["tool_call"]
    assert json.loads(events[0]["payload"]) == {"command": "artmind query chunks"}


def test_error_marks_turn():
    rec = TraceRecorder.start(source="admin", question="q")
    rec.observe({"type": "error", "message": "boom"})
    rec.finish(error="stream died")
    turn = store.get_turn(rec.trace_id)
    assert turn["status"] == "error"
    assert turn["error_message"] == "stream died"


def test_match_tool_ids_by_window_and_command_text():
    tools = {
        "a": {"input": {"command": "artmind query graph pattern5 --domain b"}, "call_ts": 1.0, "result_ts": 2.0},
        "b": {"input": {"command": "artmind query vector-text --domain b q"}, "call_ts": 3.0, "result_ts": 4.0},
        "c": {"input": {"command": "ls"}, "call_ts": 5.0, "result_ts": 6.0},
    }
    spans = [
        {"span_id": "s1", "command": "query graph pattern5", "started_at": 1.5},
        {"span_id": "s2", "command": "query vector-text", "started_at": 3.2},
        {"span_id": "s3", "command": "query chunks", "started_at": 5.5},  # no artmind call there
    ]
    assert match_tool_ids(spans, tools) == [("s1", "a"), ("s2", "b")]


def test_domains_of_splits_commas_and_reads_argv():
    spans = [{"args": {"domain": ["banking,hr"]}}, {"args": ["vector-text", "--domain", "ops", "q"]},
             {"args": None}]
    assert domains_of(spans) == ["banking", "hr", "ops"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/traces/recorder.py`:

```python
"""Turn side of trace capture (spec 2026-10-11 §5.1, §5.6).

One `TraceRecorder` per question -> answer turn. `observe(event)` records a
neutral UI event (with the mapper's private `_trace` data) and returns the
event as the UI should see it. `finish()` writes the events, assigns CLI
spans to the tool calls that ran them, and stores the derived columns.
Nothing here may break a turn: a recorder that fails to start is disabled
and still strips `_trace`; `finish()` swallows its own failures.
"""

import hashlib
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from artmind.traces import path as trace_path
from artmind.traces import store

logger = logging.getLogger(__name__)

#: Seconds of slack when matching a span's start to a tool call's window.
SKEW_S = 0.5


def public_event(event: dict) -> dict:
    return {k: v for k, v in event.items() if k != "_trace"} if "_trace" in event else event


class TraceRecorder:
    def __init__(self, trace_id: str | None, context_path: Path | None) -> None:
        self.trace_id = trace_id
        self.enabled = trace_id is not None
        self._context_path = Path(context_path) if context_path else None
        self._events: list[dict] = []
        self._tools: dict[str, dict] = {}
        self._tool_event: dict[str, dict] = {}
        self._buffers = {"text": [], "thinking": []}
        self._answer: list[str] = []
        self._done: dict = {}

    @classmethod
    def start(cls, *, source: str, question: str, session_id: str | None = None, profile: str | None = None,
              backend: str | None = None, benchmark_run_id: str | None = None, benchmark_seq: int | None = None,
              context_path: Path | None = None) -> "TraceRecorder":
        try:
            trace_id = store.new_trace_id()
            store.insert_turn(trace_id, source=source, question=question, session_id=session_id, profile=profile,
                              backend=backend, benchmark_run_id=benchmark_run_id, benchmark_seq=benchmark_seq)
            if context_path:
                Path(context_path).write_text(trace_id)
            return cls(trace_id, context_path)
        except Exception:
            logger.exception("trace capture disabled for this turn")
            return cls(None, None)

    # ── per event ────────────────────────────────────────────────────────
    def observe(self, event: dict) -> dict:
        public = public_event(event)
        if self.enabled:
            try:
                self._record(public, event.get("_trace") or {})
            except Exception:
                logger.exception("trace event dropped")
        return public

    def _add(self, type_: str, payload: Any, *, tool_id=None, parent=None, name=None) -> dict:
        row = {"seq": len(self._events), "ts": time.time(), "type": type_, "tool_id": tool_id,
               "parent_tool_id": parent, "name": name, "payload": store.cap(payload)}
        self._events.append(row)
        return row

    def _flush(self, block: str) -> None:
        if self._buffers[block]:
            self._add(block, "".join(self._buffers[block]))
            self._buffers[block] = []

    def _record(self, event: dict, raw: dict) -> None:
        kind = event.get("type")
        if kind == "text_delta":
            self._buffers["text"].append(event.get("text", ""))
            self._answer.append(event.get("text", ""))
        elif kind == "thinking_delta":
            self._buffers["thinking"].append(event.get("text", ""))
        elif kind == "block_done":
            self._flush("text" if event.get("block") == "text" else "thinking")
        elif kind == "tool_call":
            raw_input = raw.get("input", event.get("input"))
            row = self._add("tool_call", raw_input, tool_id=event.get("id"), parent=raw.get("parent_tool_id"),
                            name=event.get("name"))
            self._tools[event.get("id")] = {"input": raw_input, "call_ts": row["ts"], "result_ts": None}
            self._tool_event[event.get("id")] = row
        elif kind == "tool_call_update":
            raw_input = raw.get("input", event.get("input"))
            if event.get("id") in self._tool_event:
                self._tool_event[event["id"]]["payload"] = store.cap(raw_input)
                self._tools[event["id"]]["input"] = raw_input
        elif kind == "tool_result":
            self._add("tool_result", raw.get("content", event.get("content")), tool_id=event.get("tool_id"),
                      parent=raw.get("parent_tool_id"))
            if event.get("tool_id") in self._tools:
                self._tools[event["tool_id"]]["result_ts"] = time.time()
        elif kind == "turn_done":
            self._done = {**event, **raw}
        elif kind == "error":
            self._add("error", event.get("message"))

    # ── end of turn ──────────────────────────────────────────────────────
    def finish(self, error: str | None = None) -> None:
        if not self.enabled:
            return
        try:
            self._flush("thinking")
            self._flush("text")
            store.insert_events(self.trace_id, self._events)
            spans = store.list_spans(self.trace_id)
            pairs = match_tool_ids(spans, self._tools)
            store.set_span_tool_ids(pairs)
            answer = "".join(self._answer) or self._done.get("result")
            derived = trace_path.derive(spans, answer)
            domains = domains_of(spans)
            usage = self._done.get("usage") or {}
            input_tokens = sum(usage.get(k) or 0 for k in
                               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")) or None
            store.update_turn(
                self.trace_id,
                answer=answer or None,
                domains=json.dumps(domains),
                model=self._done.get("model"),
                turns=self._done.get("turns"),
                duration_s=self._done.get("duration_s"),
                cost_usd=self._done.get("cost"),
                input_tokens=input_tokens,
                output_tokens=usage.get("output_tokens"),
                graph_state=json.dumps(graph_state(domains)),
                retrieval_path=json.dumps(derived["retrieval_path"]),
                graph_attempts=derived["graph_attempts"],
                graph_productive=derived["graph_productive"],
                fell_through=int(derived["fell_through"]),
                answer_basis=derived["answer_basis"],
                status="error" if error else "done",
                error_message=error,
                completed_at=time.time(),
            )
        except Exception:
            logger.exception("trace finish failed")
        finally:
            self._clear_context()

    def _clear_context(self) -> None:
        try:
            if self._context_path is not None and self._context_path.exists():
                self._context_path.write_text("")
        except OSError:
            pass


def _input_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def match_tool_ids(spans: list[dict], tools: dict[str, dict]) -> list[tuple[str, str]]:
    """(span_id, tool_id) for each span whose start falls in a tool call's
    window and whose leaf command appears in that call's input next to
    `artmind`. The latest-starting matching call wins."""
    pairs = []
    for span in spans:
        command = span.get("command") or ""
        leaf = command.split()[-1] if command else ""
        best, best_start = None, None
        for tool_id, tool in tools.items():
            start = tool["call_ts"] - SKEW_S
            end = (tool["result_ts"] if tool["result_ts"] is not None else float("inf")) + SKEW_S
            if not (start <= span["started_at"] <= end):
                continue
            text = _input_text(tool["input"])
            if "artmind" in text and leaf and leaf in text and (best_start is None or tool["call_ts"] > best_start):
                best, best_start = tool_id, tool["call_ts"]
        if best is not None:
            pairs.append((span["span_id"], best))
    return pairs


def domains_of(spans: list[dict]) -> list[str]:
    found: set[str] = set()
    for span in spans:
        args = span.get("args")
        values: list[str] = []
        if isinstance(args, dict):
            raw = args.get("domain") or []
            values = [raw] if isinstance(raw, str) else list(raw)
        elif isinstance(args, list):
            values = [args[i + 1] for i, tok in enumerate(args[:-1]) if tok == "--domain"]
        for value in values:
            found.update(part.strip() for part in str(value).split(",") if part.strip())
    return sorted(found)


def graph_state(domains: list[str]) -> dict:
    """What the turn ran against: package version, schema file hashes, vault HEAD."""
    import paths

    try:
        from importlib.metadata import version

        artmind_version = version("artmind9")
    except Exception:
        artmind_version = None
    schemas = {}
    for domain in domains:
        schema = paths.DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
        if schema.is_file():
            schemas[domain] = hashlib.sha256(schema.read_bytes()).hexdigest()
    vault_head = None
    if paths.ARTMIND_VAULT_DIR:
        try:
            out = subprocess.run(["git", "-C", str(paths.ARTMIND_VAULT_DIR), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=2)
            vault_head = out.stdout.strip() or None if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            vault_head = None
    return {"artmind_version": artmind_version, "schemas": schemas, "vault_head": vault_head}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_recorder.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/recorder.py test/test_traces_recorder.py
git commit -m "feat(traces): TraceRecorder records a turn, matches spans to tool calls, derives fall-through"
```

---

### Task 11: Admin console capture, `trace` event, feedback endpoint, prune at startup

**Files:**
- Modify: `artmind/webui/app.py`
- Test: `test/test_traces_app.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_app.py`:

```python
"""Admin console capture through /api/chat, and the feedback endpoint."""

import json

import pytest
from fastapi.testclient import TestClient

from artmind.traces import store
from artmind.webui.app import create_app
from artmind.webui.sessions import SessionRegistry


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


class TracedFakeBackend:
    instances: list["TracedFakeBackend"] = []

    def __init__(self, backend="claude-sdk", context=None):
        self.trace_context_path = context
        self.seen_trace_id = None
        TracedFakeBackend.instances.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, prompt):
        self.seen_trace_id = self.trace_context_path.read_text() if self.trace_context_path else None

    async def interrupt(self):
        pass

    async def receive_events(self):
        yield {"type": "tool_call", "id": "tu1", "name": "Bash", "input": "clip",
               "_trace": {"input": {"command": "artmind query chunks"}, "parent_tool_id": None}}
        yield {"type": "tool_result", "tool_id": "tu1", "content": "clip", "_trace": {"content": "FULL"}}
        yield {"type": "text_delta", "text": "Hi"}
        yield {"type": "turn_done", "turns": 1, "duration_s": 1.0, "cost": 0.01, "_trace": {"usage": {}, "model": "m"}}


def _sse(body):
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def _admin_client(tmp_path):
    TracedFakeBackend.instances = []
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")
    registry = SessionRegistry(client_factory=lambda name: TracedFakeBackend(name, ctx))
    return TestClient(create_app(registry=registry, admin_routes=True, trace_turns=True)), ctx


def test_admin_turn_is_traced_and_sse_is_clean(tmp_path):
    client, ctx = _admin_client(tmp_path)
    resp = client.post("/api/chat", json={"session_id": "s1", "prompt": "Who?"})
    events = _sse(resp.text)
    assert events[0]["type"] == "trace"
    trace_id = events[0]["trace_id"]
    assert all("_trace" not in e for e in events)
    assert TracedFakeBackend.instances[0].seen_trace_id == trace_id  # written before query()
    assert ctx.read_text() == ""  # cleared after the turn

    turn = store.get_turn(trace_id)
    assert (turn["source"], turn["question"], turn["session_id"], turn["status"]) == ("admin", "Who?", "s1", "done")
    payloads = [e["payload"] for e in store.list_events(trace_id)]
    assert "FULL" in payloads


def test_chat_ui_is_not_traced(tmp_path):
    TracedFakeBackend.instances = []
    registry = SessionRegistry(client_factory=lambda name: TracedFakeBackend(name, None))
    client = TestClient(create_app(registry=registry))
    events = _sse(client.post("/api/chat", json={"session_id": "s1", "prompt": "Who?"}).text)
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "text_delta", "turn_done"]
    assert all("_trace" not in e for e in events)
    assert store.list_turns() == []


def test_feedback_endpoint(tmp_path):
    client, _ = _admin_client(tmp_path)
    trace_id = _sse(client.post("/api/chat", json={"session_id": "s1", "prompt": "Who?"}).text)[0]["trace_id"]
    ok = client.post(f"/api/traces/{trace_id}/feedback", json={"rating": "wrong", "expected": "Bob"})
    assert ok.status_code == 200
    assert store.latest_feedback(trace_id)["expected"] == "Bob"
    assert client.post("/api/traces/nope/feedback", json={"rating": "good"}).status_code == 404
    assert client.post(f"/api/traces/{trace_id}/feedback", json={"rating": "meh"}).status_code == 422


def test_feedback_endpoint_absent_without_tracing():
    client = TestClient(create_app())
    assert client.post("/api/traces/x/feedback", json={"rating": "good"}).status_code == 404
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_app.py -v`
Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'trace_turns'`.

- [ ] **Step 3: Implement**

In `artmind/webui/app.py`:

Add the imports `import os` and `from typing import Literal`.

Add the models below `OpenSourceRequest`:

```python
class FeedbackRequest(BaseModel):
    rating: Literal["good", "partial", "wrong"]
    expected: str | None = None
```

Add `trace_turns: bool = False` to `create_app`'s parameters, after `admin_routes`.

In `lifespan`, before `task = asyncio.create_task(sweep_loop())`:

```python
        if trace_turns:
            try:
                from artmind.traces import store

                store.prune(int(os.environ.get("ARTMIND_TRACE_RETENTION_DAYS", "90")))
            except Exception:
                logger.exception("trace prune failed")
```

Replace the `/api/chat` route with:

```python
    @app.post("/api/chat")
    async def chat(payload: ChatRequest) -> StreamingResponse:
        from artmind.traces.recorder import TraceRecorder, public_event

        async def stream():
            recorder = None
            error = None
            try:
                client = await registry.get(payload.session_id, payload.backend)
                context_path = getattr(client, "trace_context_path", None)
                if trace_turns and context_path is not None:
                    # Query-trace capture (spec 2026-10-11 §5.1): the id goes into
                    # the session's context file before the agent sees the prompt.
                    recorder = TraceRecorder.start(
                        source="admin", question=payload.prompt, session_id=payload.session_id,
                        profile="admin", backend=payload.backend, context_path=context_path,
                    )
                    if recorder.enabled:
                        yield f"data: {json.dumps({'type': 'trace', 'trace_id': recorder.trace_id})}\n\n"
                await client.query(payload.prompt)
                async for event in client.receive_events():
                    event = recorder.observe(event) if recorder is not None else public_event(event)
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as exc:  # stream errors to the client, don't 500
                error = str(exc)
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            finally:
                if recorder is not None:
                    recorder.finish(error=error)

        return StreamingResponse(stream(), media_type="text/event-stream")
```

Add the feedback route inside `create_app`, just before `if admin_routes:`:

```python
    if trace_turns:
        @app.post("/api/traces/{trace_id}/feedback")
        async def trace_feedback(trace_id: str, payload: FeedbackRequest):
            from artmind.traces import store

            try:
                await asyncio.to_thread(store.add_feedback, trace_id, payload.rating, payload.expected)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"no trace {trace_id}") from None
            return {"ok": True}
```

In `run_admin_ui`:

```python
    app = create_app(
        SessionRegistry(client_factory=backend_factory(ADMIN_PROFILE, trace=True)),
        template_name="admin.html",
        admin_routes=True,
        trace_turns=True,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_app.py test/test_webui_app.py test/test_webui_admin_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/app.py test/test_traces_app.py
git commit -m "feat(traces): admin console records every turn; feedback endpoint; prune on start"
```

---

### Task 12: Benchmark capture

**Files:**
- Modify: `artmind/benchmark.py` (`_update_benchmark_question`, `_run_benchmark`)
- Test: `test/test_traces_app.py` (append)

- [ ] **Step 1: Write the failing test** (append to `test/test_traces_app.py`)

```python
def test_benchmark_question_is_traced(tmp_path, monkeypatch):
    import asyncio

    from artmind import benchmark
    from artmind.webui import backends as backends_mod

    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    created = []

    def fake_create_backend(name, profile, *, trace_context=None, **kw):
        backend = TracedFakeBackend(name, trace_context)
        created.append(backend)
        return backend

    monkeypatch.setattr(backends_mod, "create_backend", fake_create_backend)
    run_id = benchmark._create_benchmark_run("q.md", [{"seq": 1, "qid": "Q1", "title": "t", "question": "Who?",
                                                        "eval_comment": None}])
    asyncio.run(benchmark._run_benchmark(run_id))

    question = benchmark._get_benchmark_question(run_id, 1)
    assert question["status"] == "completed"
    trace_id = question["trace_id"]
    assert created[0].seen_trace_id == trace_id
    turn = store.get_turn(trace_id)
    assert (turn["source"], turn["benchmark_run_id"], turn["benchmark_seq"]) == ("benchmark", run_id, 1)
    assert benchmark._export_benchmark_json(run_id) is not None  # export still works
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_traces_app.py::test_benchmark_question_is_traced -v`
Expected: FAIL (`create_backend` is called without `trace_context`, and `question` has no `trace_id` key).

- [ ] **Step 3: Implement**

In `artmind/benchmark.py`:

1. In `_update_benchmark_question`, add the parameter `trace_id: str | None = None` after `error_message`, and add `("trace_id", trace_id),` to the column tuple.

2. In `_get_benchmark_question`, change the second line of the `SELECT` to
   `" turns, duration_s, cost_usd, started_at, completed_at, error_message, trace_id"` and add
   `"trace_id": row[14],` to the returned dict after `"error_message": row[13],`.

3. In `_run_benchmark`:
   - Add these to the lazy imports at the top of the function body:

     ```python
         from artmind.traces.recorder import TraceRecorder
         from artmind.webui.backends import new_trace_context
     ```

   - Replace `backend_client = create_backend(run["backend"], BENCHMARK_PROFILE)` with:

     ```python
             context_path = new_trace_context()
             backend_client = create_backend(run["backend"], BENCHMARK_PROFILE, trace_context=context_path)
             recorder = TraceRecorder.start(
                 source="benchmark", question=prompt, profile="benchmark", backend=run["backend"],
                 benchmark_run_id=run_id, benchmark_seq=seq, context_path=context_path,
             )
     ```

   - As the first line inside `async for event in backend_client.receive_events():`, add `event = recorder.observe(event)`.

   - In the `finally:` block, before `try: await backend_client.disconnect()`, add `recorder.finish(error=error_message)`.

   - In the `_update_benchmark_question(...)` call after the loop, add `trace_id=recorder.trace_id,`.

   `create_backend` is imported inside the function (`from artmind.webui.backends import create_backend`), so the test's `monkeypatch.setattr(backends_mod, "create_backend", ...)` takes effect, because the import runs at call time.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_app.py test/test_benchmark.py test/test_webui_benchmark_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/benchmark.py test/test_traces_app.py
git commit -m "feat(traces): benchmark questions are traced like admin turns"
```

---

### Task 13: Feedback control in the admin console

**Files:**
- Modify: `artmind/webui/static/app.js`, `artmind/webui/static/style.css`
- Test: manual, in the browser preview (Step 4)

`app.js` is shared by the chat UI and the admin console. The control appears only after a `trace` event, which only the admin console sends. Listeners are attached by delegation on `#chat`, because the chat history is restored from stored HTML, which loses directly attached listeners.

- [ ] **Step 1: Track the trace id per turn**

In `app.js`:
- Under `// per-turn rendering state`, add:

  ```javascript
  let traceId = null;       // admin console: this turn's query-trace id
  ```

- In `handleEvent`'s `switch`, add a case before `case "turn_done":`:

  ```javascript
      case "trace":
        traceId = ev.trace_id;
        break;
  ```

- In `send()`'s `finally` block, replace `if (turnEl) pushHistory(turnEl.outerHTML);` with:

  ```javascript
      if (turnEl && traceId) turnEl.appendChild(feedbackControl(traceId));
      if (turnEl) pushHistory(turnEl.outerHTML);
      traceId = null;
  ```

- [ ] **Step 2: Add the control and its delegated handlers**

Add a `// ── answer feedback (admin console) ──` section above `// ── event dispatch ──`:

```javascript
// ── answer feedback (admin console) ─────────────────────────────────
// Rates one traced turn (spec 2026-10-11 §5.7). Rendered only when the
// server sent a `trace` event; handlers are delegated on #chat because a
// restored history is plain HTML with no listeners.
function feedbackControl(id) {
  const wrap = el("div", "feedback");
  wrap.dataset.traceId = id;
  for (const [rating, label] of [["good", "Good"], ["partial", "Partial"], ["wrong", "Wrong"]]) {
    const btn = el("button", "feedback-btn", label);
    btn.type = "button";
    btn.dataset.rating = rating;
    wrap.appendChild(btn);
  }
  const note = el("textarea", "feedback-note");
  note.placeholder = "What was expected? (optional)";
  note.rows = 2;
  note.hidden = true;
  wrap.appendChild(note);
  const save = el("button", "feedback-save", "Save note");
  save.type = "button";
  save.hidden = true;
  wrap.appendChild(save);
  wrap.appendChild(el("span", "feedback-status"));
  return wrap;
}

async function postFeedback(wrap, rating) {
  const status = wrap.querySelector(".feedback-status");
  const note = wrap.querySelector(".feedback-note");
  try {
    const r = await fetch(`/api/traces/${wrap.dataset.traceId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating, expected: note.value.trim() || null }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    wrap.dataset.rating = rating;
    for (const b of wrap.querySelectorAll(".feedback-btn")) {
      b.classList.toggle("selected", b.dataset.rating === rating);
    }
    status.textContent = "Saved";
  } catch (err) {
    status.textContent = `Not saved: ${err.message}`;
  }
}

chatEl.addEventListener("click", (e) => {
  const wrap = e.target.closest(".feedback");
  if (!wrap) return;
  const btn = e.target.closest(".feedback-btn");
  if (btn) {
    const note = wrap.querySelector(".feedback-note");
    const save = wrap.querySelector(".feedback-save");
    const wantsNote = btn.dataset.rating !== "good";
    note.hidden = !wantsNote;
    save.hidden = !wantsNote;
    postFeedback(wrap, btn.dataset.rating);
    if (wantsNote) note.focus();
    return;
  }
  if (e.target.closest(".feedback-save") && wrap.dataset.rating) {
    postFeedback(wrap, wrap.dataset.rating);
  }
});
```

- [ ] **Step 3: Style it**

`admin.html` and `index.html` both load `style.css`, which defines `--border`, `--accent`, `--text` and `--text-muted` for both themes. Append to `artmind/webui/static/style.css`, next to the `.trace-summary` rule:

```css
.feedback { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 8px; }
.feedback-btn, .feedback-save {
  font: inherit; font-size: 12px; padding: 2px 10px; border-radius: 999px;
  border: 1px solid var(--border); background: transparent; color: var(--text-muted); cursor: pointer;
}
.feedback-btn.selected { color: var(--text); border-color: var(--accent); }
.feedback-note { flex-basis: 100%; font: inherit; font-size: 13px; resize: vertical; }
.feedback-status { color: var(--text-muted); font-size: 12px; }
```

- [ ] **Step 4: Verify in the browser**

1. If `.claude/launch.json` has no `admin-ui` configuration, add one:

   ```json
   {"name": "admin-ui", "runtimeExecutable": "uv", "runtimeArgs": ["run", "artmind", "admin-ui"], "port": 8379}
   ```

2. Start it with `preview_start("admin-ui")`.
3. Send a short question.
4. `preview_snapshot`: confirm the three buttons appear under the answer.
5. Click "Wrong" (`preview_click`, selector `.feedback-btn[data-rating=wrong]`), type a note in `.feedback-note`, and click "Save note".
6. `preview_network`: confirm two `POST /api/traces/<id>/feedback` requests returned 200.
7. `ARTMIND_NO_PROXY=1 uv run artmind traces show <id> --compact` (available after Task 15; until then, query `trace_feedback` with sqlite3): confirm the rating and note were stored.
8. Take a `preview_screenshot` of the control.

The chat UI (`chat-ui`) must show no control.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/static/app.js artmind/webui/static/style.css .claude/launch.json
git commit -m "feat(traces): good/partial/wrong feedback under each admin-console answer"
```

(Leave `.claude/launch.json` out of the commit if it is gitignored.)

---

### Task 14: Triage, stats and show

**Files:**
- Create: `artmind/traces/analysis.py`
- Test: `test/test_traces_analysis.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_analysis.py`:

```python
"""artmind.traces.analysis: triage order, signals, stats, show (spec §4.1, §6)."""

import json
import time

import pytest

from artmind.traces import analysis, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _turn(tid, *, started_at, answer="ok", turns=2, fell_through=0, basis="graph", attempts=1, productive=1,
          domains=("b",)):
    store.insert_turn(tid, source="admin", question=f"q-{tid}", started_at=started_at)
    store.update_turn(tid, answer=answer, turns=turns, fell_through=fell_through, answer_basis=basis,
                      graph_attempts=attempts, graph_productive=productive, domains=json.dumps(list(domains)),
                      retrieval_path="[]", status="done")


def _span(tid, command, *, rows=1, exit_code=0, truncated=0, evidence=None, at=0.0):
    store.insert_span({"span_id": f"{tid}-{command}-{at}", "trace_id": tid, "command": command, "args": "{}",
                       "exit_code": exit_code, "duration_ms": 1, "rows": rows, "rows_total": None,
                       "truncated": truncated, "evidence": json.dumps(evidence or {}), "started_at": at}, [])


def test_parse_since():
    now = 1_000_000.0
    assert analysis.parse_since("7d", now=now) == now - 7 * 86400
    assert analysis.parse_since("12h", now=now) == now - 12 * 3600
    assert analysis.parse_since(None, now=now) is None
    assert analysis.parse_since("2026-10-01", now=now) > 0
    with pytest.raises(ValueError):
        analysis.parse_since("soon", now=now)


def test_signals_each_fire():
    turn = {"fell_through": 1, "answer_basis": "vector", "graph_attempts": 3, "graph_productive": 0,
            "turns": 9, "answer": "I couldn't find that in the graph."}
    spans = [
        {"command": "query graph pattern2", "rows": 0, "exit_code": 0, "truncated": 0, "evidence": {}},
        {"command": "query graph text2cypher", "rows": 0, "exit_code": 1, "truncated": 0, "evidence": {}},
        {"command": "query entity-resolve", "rows": 2, "exit_code": 0, "truncated": 0,
         "evidence": {"resolved": [{"score": 0.033}, {"score": 0.031}]}},
        {"command": "query vector-text", "rows": 3, "exit_code": 0, "truncated": 1, "evidence": {}},
    ]
    s = analysis.signals(turn, spans, turns_p90=5)
    assert s == {"graph_miss": 6, "empty_rows": 1, "truncated": 1, "text2cypher": 1, "deep_fallback": 1,
                 "many_turns": 1, "not_found_answer": 1, "ambiguous_entity": 1, "command_error": 1}


def test_partial_fall_through_scores_one():
    turn = {"fell_through": 1, "answer_basis": "mixed", "graph_attempts": 1, "graph_productive": 1,
            "turns": 1, "answer": "fine"}
    assert analysis.signals(turn, [], turns_p90=None) == {"fell_through": 1}


def test_triage_order_and_exclusions():
    now = time.time()
    _turn("wrong", started_at=now - 10)
    store.add_feedback("wrong", "wrong")
    _turn("partial", started_at=now - 9)
    store.add_feedback("partial", "partial")
    _turn("good-miss", started_at=now - 8, fell_through=1, basis="vector", attempts=1, productive=0)
    store.add_feedback("good-miss", "good")
    _turn("good-plain", started_at=now - 7)
    store.add_feedback("good-plain", "good")
    _turn("unrated-miss", started_at=now - 6, fell_through=1, basis="vector", attempts=1, productive=0)
    _turn("unrated-quiet", started_at=now - 5)
    _turn("reviewed", started_at=now - 4)
    store.add_feedback("reviewed", "wrong")
    store.insert_review_stub("rv", "reviewed")

    ids = [item["trace_id"] for item in analysis.triage()]
    assert ids == ["wrong", "partial", "unrated-miss", "good-miss"]
    first = analysis.triage()[0]
    assert first["rating"] == "wrong" and first["question"] == "q-wrong"


def test_stats_per_domain():
    now = time.time()
    _turn("a", started_at=now, fell_through=1, basis="vector", attempts=2, productive=0, domains=("b",))
    _turn("c", started_at=now, fell_through=0, basis="graph", attempts=1, productive=1, domains=("b", "hr"))
    rows = analysis.stats(by="all")
    b = next(r for r in rows if r["domain"] == "b")
    assert b["turns"] == 2
    assert b["fall_through_rate"] == 0.5
    assert b["graph_miss_rate"] == 0.5
    assert b["graph_productivity"] == pytest.approx(1 / 3)
    assert b["answer_basis"] == {"vector": 1, "graph": 1}
    assert b["mean_graph_attempts_before_fall_through"] == 2
    assert [r["domain"] for r in analysis.stats(domain=["hr"], by="all")] == ["hr"]


def test_show_nests_spans_under_tool_calls_and_warns_on_stale_daemon():
    now = time.time()
    _turn("t", started_at=now)
    store.insert_events("t", [
        {"seq": 0, "ts": 1.0, "type": "tool_call", "tool_id": "tu1", "name": "Bash",
         "payload": json.dumps({"command": "artmind query chunks --domain b"})},
    ])
    shown = analysis.show("t")
    assert shown["events"][0]["spans"] == []
    assert any("serve" in w for w in shown["warnings"])

    _span("t", "query chunks", at=1.5)
    store.set_span_tool_ids([("t-query chunks-1.5", "tu1")])
    shown = analysis.show("t")
    assert shown["events"][0]["spans"][0]["command"] == "query chunks"
    assert shown["warnings"] == []
    assert analysis.show("missing") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/traces/analysis.py`:

```python
"""The read side behind `artmind traces` (spec 2026-10-11 §4.1, §6):
triage ranking, fall-through stats, and one turn's evidence chain."""

import re
import statistics
import time
from collections import Counter
from datetime import datetime, timedelta

from artmind.traces import store
from artmind.traces.path import GRAPH_LAYERS, is_graph_miss, layer_of

_NOT_FOUND = re.compile(
    r"\b(couldn'?t find|could not find|unable to find|no information|not in the (graph|knowledge graph|data)"
    r"|does(?: not|n'?t) (contain|answer|say)|no (matching|relevant) (data|results|records|entities))\b",
    re.IGNORECASE,
)
_RELATIVE = re.compile(r"^(\d+)([dh])$")


def parse_since(value: str | None, *, now: float | None = None) -> float | None:
    """`7d`, `24h` or an ISO date/datetime -> epoch seconds."""
    if not value:
        return None
    now = time.time() if now is None else now
    match = _RELATIVE.match(value.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return now - amount * (86400 if unit == "d" else 3600)
    try:
        return datetime.fromisoformat(value.strip()).timestamp()
    except ValueError:
        raise ValueError(f"--since must look like 7d, 24h or 2026-10-01, not {value!r}") from None


def _ambiguous(span: dict) -> bool:
    resolved = (span.get("evidence") or {}).get("resolved") or []
    scores = [c.get("score") for c in resolved[:2]]
    return (len(scores) == 2 and all(isinstance(s, (int, float)) for s in scores)
            and scores[0] > 0 and scores[1] >= 0.9 * scores[0])


def signals(turn: dict, spans: list[dict], turns_p90: float | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if is_graph_miss(turn):
        unproductive = (turn.get("graph_attempts") or 0) - (turn.get("graph_productive") or 0)
        out["graph_miss"] = 3 + min(3, max(0, unproductive))
    elif turn.get("fell_through"):
        out["fell_through"] = 1
    if any(s.get("exit_code") == 0 and (s.get("rows") or 0) == 0 for s in spans):
        out["empty_rows"] = 1
    if any(s.get("truncated") for s in spans):
        out["truncated"] = 1
    if any(s.get("command") == "query graph text2cypher" for s in spans):
        out["text2cypher"] = 1
    if len({s.get("command") for s in spans if layer_of(s.get("command")) != "discovery"}) >= 3:
        out["deep_fallback"] = 1
    if turns_p90 is not None and (turn.get("turns") or 0) > turns_p90:
        out["many_turns"] = 1
    if turn.get("answer") and _NOT_FOUND.search(turn["answer"]):
        out["not_found_answer"] = 1
    if any(_ambiguous(s) for s in spans):
        out["ambiguous_entity"] = 1
    if any(s.get("exit_code") not in (0, None) for s in spans):
        out["command_error"] = 1
    return out


def _p90(values: list[int]) -> float | None:
    if len(values) < 10:
        return None  # too few turns for a percentile to mean anything
    return statistics.quantiles(values, n=10)[-1]


def triage(*, since: float | None = None, limit: int = 20) -> list[dict]:
    """Turns worth reviewing: rated wrong, then partial, then unrated with
    signals, with `good`-rated graph misses after unrated turns of equal score."""
    turns = [t for t in store.list_turns(since=since) if t["status"] in ("done", "error") and not t["reviewed"]]
    p90 = _p90([t["turns"] or 0 for t in turns])
    items = []
    for turn in turns:
        rating = turn.get("rating")
        sig = signals(turn, store.list_spans(turn["trace_id"]), p90)
        score = sum(sig.values())
        if rating == "good" and "graph_miss" not in sig:
            continue
        if rating is None and score == 0:
            continue
        tier = {"wrong": 0, "partial": 1}.get(rating, 2)
        items.append({
            "trace_id": turn["trace_id"], "rating": rating, "expected": turn.get("expected"),
            "score": score, "signals": sig, "question": turn["question"], "started_at": turn["started_at"],
            "_key": (tier, -score, 1 if rating == "good" else 0, -turn["started_at"]),
        })
    items.sort(key=lambda item: item["_key"])
    for item in items:
        del item["_key"]
    return items[:limit]


def _period(started_at: float, by: str) -> str:
    day = datetime.fromtimestamp(started_at).date()
    if by == "day":
        return day.isoformat()
    if by == "week":
        return (day - timedelta(days=day.weekday())).isoformat()
    return "all"


def stats(*, domain: list[str] | None = None, since: float | None = None, by: str = "week") -> list[dict]:
    buckets: dict[tuple[str, str], list[dict]] = {}
    for turn in store.list_turns(since=since):
        if turn["status"] != "done":
            continue
        for d in turn.get("domains") or ["(none)"]:
            if domain and d not in domain:
                continue
            buckets.setdefault((d, _period(turn["started_at"], by)), []).append(turn)
    rows = []
    for (d, period), turns in sorted(buckets.items()):
        n = len(turns)
        attempts = sum(t["graph_attempts"] or 0 for t in turns)
        fell = [t for t in turns if t["fell_through"]]
        rows.append({
            "domain": d, "period": period, "turns": n,
            "fall_through_rate": len(fell) / n,
            "graph_miss_rate": sum(1 for t in turns if is_graph_miss(t)) / n,
            "graph_productivity": (sum(t["graph_productive"] or 0 for t in turns) / attempts) if attempts else None,
            "answer_basis": dict(Counter(t["answer_basis"] or "none" for t in turns)),
            "mean_graph_attempts_before_fall_through":
                statistics.mean(t["graph_attempts"] or 0 for t in fell) if fell else None,
        })
    return rows


def show(trace_id: str) -> dict | None:
    turn = store.get_turn(trace_id)
    if turn is None:
        return None
    events = store.list_events(trace_id)
    spans = store.list_spans(trace_id)
    calls = store.list_llm_calls([s["span_id"] for s in spans])
    for span in spans:
        span["llm_calls"] = calls.get(span["span_id"], [])
    by_tool: dict[str, list[dict]] = {}
    for span in spans:
        if span["tool_id"]:
            by_tool.setdefault(span["tool_id"], []).append(span)
    for event in events:
        if event["type"] == "tool_call":
            event["spans"] = by_tool.get(event["tool_id"], [])
    warnings = []
    ran_query = any(e["type"] == "tool_call" and "artmind query" in (e["payload"] or "") for e in events)
    if ran_query and not spans:
        warnings.append(
            "No CLI spans were recorded although the agent ran `artmind query`. A `serve` daemon started "
            "before trace capture shipped answers without recording: restart it (just dev-stop-daemons, "
            "then `artmind serve` from the vault)."
        )
    return {
        "turn": turn,
        "feedback": store.latest_feedback(trace_id),
        "events": events,
        "unattributed_spans": [s for s in spans if not s["tool_id"]],
        "graph_layers": sorted(GRAPH_LAYERS),
        "warnings": warnings,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_analysis.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/analysis.py test/test_traces_analysis.py
git commit -m "feat(traces): triage ranking, fall-through stats, evidence-chain view"
```

---

### Task 15: `artmind traces` CLI group

**Files:**
- Modify: `artmind/cli.py` (new group after `views_group`'s commands; `COMMAND_GROUPS` at line ~221)
- Test: `test/test_traces_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_cli.py`:

```python
"""`artmind traces list/show/triage/feedback/stats/prune`."""

import json
import time

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    now = time.time()
    store.insert_turn("t1", source="admin", question="Who leads X?", started_at=now)
    store.update_turn("t1", answer="Alice", status="done", fell_through=1, answer_basis="vector",
                      graph_attempts=1, graph_productive=0, domains='["b"]', retrieval_path="[]", turns=2)


def _json(args):
    result = CliRunner().invoke(cli, ["traces", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_list_and_show():
    listed = _json(["list"])
    assert [t["trace_id"] for t in listed["turns"]] == ["t1"]
    assert listed["turns"][0]["question"] == "Who leads X?"
    shown = _json(["show", "t1"])
    assert shown["turn"]["answer"] == "Alice"


def test_show_missing_is_an_error():
    result = CliRunner().invoke(cli, ["traces", "show", "nope"])
    assert result.exit_code != 0
    assert "no trace" in result.output


def test_feedback_then_list_filters_by_rating():
    result = CliRunner().invoke(cli, ["traces", "feedback", "t1", "--rating", "wrong", "--expected", "Bob"])
    assert result.exit_code == 0, result.output
    assert [t["trace_id"] for t in _json(["list", "--rating", "wrong"])["turns"]] == ["t1"]
    assert _json(["list", "--rating", "good"])["turns"] == []


def test_triage_and_stats():
    assert [i["trace_id"] for i in _json(["triage"])["items"]] == ["t1"]
    stats = _json(["stats", "--by", "all"])["rows"]
    assert stats[0]["domain"] == "b" and stats[0]["graph_miss_rate"] == 1.0


def test_prune_reports_count():
    assert _json(["prune", "--days", "90"]) == {"pruned": 0}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_traces_cli.py -v`
Expected: FAIL with `No such command 'traces'`.

- [ ] **Step 3: Implement**

In `artmind/cli.py`, change the Query entry of `COMMAND_GROUPS["artmind"]` to:

```python
        {"name": "Query", "commands": ["query", "views", "traces"]},
```

Then add the group after the last `views_group` command:

```python
@cli.group("traces")
def traces_group() -> None:
    """Inspect recorded query traces: admin-console turns and benchmark questions.

    Every admin-console turn and benchmark question is recorded in this machine's
    registry DB: the agent's events, one span per `artmind query` call (command,
    args, rows, evidence ids, LLM calls), and whether the graph's attempts fell
    through to vector search. `triage` ranks turns worth reviewing, `show`
    prints one turn's evidence chain, `stats` tracks graph-to-vector
    fall-through per domain. Never synced, never committed.
    """
    pass


def _since_option(func):
    return click.option("--since", default=None, help="Only turns since: 7d, 24h or an ISO date")(func)


@traces_group.command("list")
@click.option("--source", type=click.Choice(["admin", "benchmark"]), default=None)
@click.option("--rating", type=click.Choice(["good", "partial", "wrong", "unrated"]), default=None)
@click.option("--domain", "domain", multiple=True, help="Only turns touching this domain (repeatable; comma-splittable)")
@_since_option
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def traces_list(source, rating, domain, since, limit, compact) -> None:
    """One line per turn, newest first: id, time, rating, question, cost."""
    from artmind.traces import analysis, store

    try:
        since_ts = analysis.parse_since(since)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    domains = set(_parse_repeatable(domain))
    turns = []
    for t in store.list_turns(source=source, since=since_ts):
        if rating == "unrated" and t["rating"] is not None:
            continue
        if rating not in (None, "unrated") and t["rating"] != rating:
            continue
        if domains and not domains & set(t.get("domains") or []):
            continue
        turns.append({k: t[k] for k in ("trace_id", "source", "started_at", "rating", "question", "turns",
                                        "cost_usd", "fell_through", "answer_basis", "status")})
        if len(turns) >= limit:
            break
    _echo_json({"command": "traces_list", "turns": turns}, compact)


@traces_group.command("show")
@click.argument("trace_id")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def traces_show(trace_id, compact) -> None:
    """One turn's full evidence chain: events in order, CLI spans under the
    Bash call that ran them (with evidence and LLM calls), feedback."""
    from artmind.traces import analysis

    shown = analysis.show(trace_id)
    if shown is None:
        raise click.ClickException(f"no trace {trace_id}")
    _echo_json(shown, compact)


@traces_group.command("triage")
@_since_option
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def traces_triage(since, limit, compact) -> None:
    """Rank unreviewed turns worth reviewing: rated wrong, then partial, then
    unrated turns by signal score; graph misses rated good come last."""
    from artmind.traces import analysis

    try:
        items = analysis.triage(since=analysis.parse_since(since), limit=limit)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json({"command": "traces_triage", "items": items}, compact)


@traces_group.command("feedback")
@click.argument("trace_id")
@click.option("--rating", type=click.Choice(["good", "partial", "wrong"]), required=True)
@click.option("--expected", default=None, help="What the answer should have said")
def traces_feedback(trace_id, rating, expected) -> None:
    """Rate a turn (the latest rating wins)."""
    from artmind.traces import store

    try:
        store.add_feedback(trace_id, rating, expected)
    except KeyError:
        raise click.ClickException(f"no trace {trace_id}") from None
    click.echo(f"{trace_id}: {rating}")


@traces_group.command("stats")
@click.option("--domain", "domain", multiple=True, help="Only these domains (repeatable; comma-splittable)")
@_since_option
@click.option("--by", type=click.Choice(["week", "day", "all"]), default="week", show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def traces_stats(domain, since, by, compact) -> None:
    """Per domain and period: turns, fall-through rate, graph-miss rate, graph
    productivity, answer basis split, graph attempts before a fall-through."""
    from artmind.traces import analysis

    try:
        rows = analysis.stats(domain=_parse_repeatable(domain) or None, since=analysis.parse_since(since), by=by)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json({"command": "traces_stats", "by": by, "rows": rows}, compact)


@traces_group.command("prune")
@click.option("--days", type=int, default=None, help="Retention in days (default: ARTMIND_TRACE_RETENTION_DAYS or 90)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def traces_prune(days, compact) -> None:
    """Delete turns older than the retention window that have no feedback and no review."""
    from artmind.traces import store

    retention = days if days is not None else int(os.environ.get("ARTMIND_TRACE_RETENTION_DAYS", "90"))
    _echo_json({"pruned": store.prune(retention)}, compact)
```

Check that `os` is imported at the top of `cli.py`. It already is, because `_warn_if_vault_stale` uses `os.environ`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_cli.py test/test_cli_guide.py -v`
Expected: PASS. `test_every_command_is_routed_into_the_guide` passes because `traces` is in `COMMAND_GROUPS`.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_traces_cli.py
git commit -m "feat(traces): artmind traces list/show/triage/feedback/stats/prune"
```

---

### Task 16: Docs, recipes, env example

**Files:**
- Modify: `CLAUDE.md`, `justfile`, `artmind/env.example`

- [ ] **Step 1: Add the `CLAUDE.md` repo-layout row**

Insert this row after the `artmind/kg_views/` row:

```markdown
| `artmind/traces/` | Query traces (spec `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`). Every admin-console turn and benchmark question → `trace_turns`/`trace_events` in the registry DB. Every `artmind query` call made under a trace → a `trace_spans` row (via `TracedGroup` on the `query` group; evidence read from the payload `_echo_json` prints) plus its `trace_llm_calls`. The trace id reaches the agent's Bash through a per-session file named by `ARTMIND_TRACE_CONTEXT`, which `_entry` forwards to `serve`. `path` derives graph→vector fall-through and answer basis. CLI: `artmind traces list/show/triage/feedback/stats/prune` (not proxied). |
```

- [ ] **Step 2: Update the `CLAUDE.md` command routing reference**

Under "Command routing quick reference", after the `artmind views <cmd>` bullet, add:

```markdown
- `artmind traces <cmd>` — recorded query traces: `list`, `show`, `triage`, `feedback`, `stats`, `prune` (not proxied)
```

- [ ] **Step 3: Add the `CLAUDE.md` testing note**

Under "### 1. A running daemon serves stale code", append:

```markdown
Trace capture has the same trap: a `serve` daemon started before `artmind/traces`
shipped answers proxied queries without recording spans. `artmind traces show`
warns when a turn ran `artmind query` but has no spans.
```

- [ ] **Step 4: Add the justfile recipes**

Add a section after the `query` / `views` recipes and before `# ── artmind serve & web UIs`:

```just
# ── artmind traces ───────────────────────────────────────────────────────────

# recorded admin-console/benchmark turns, newest first  (usage: just traces-list [rating])
traces-list rating="":
    uv run artmind traces list {{ if rating != "" { "--rating " + rating } else { "" } }}

# one turn's full evidence chain  (usage: just traces-show <trace_id>)
traces-show trace_id:
    uv run artmind traces show {{ trace_id }}

# turns worth reviewing, ranked  (usage: just traces-triage [since], e.g. 7d)
traces-triage since="":
    uv run artmind traces triage {{ if since != "" { "--since " + since } else { "" } }}

# graph-to-vector fall-through per domain and week  (usage: just traces-stats [domain])
traces-stats domain="":
    uv run artmind traces stats {{ if domain != "" { "--domain " + domain } else { "" } }}
```

- [ ] **Step 5: Add the env example**

Append to `artmind/env.example`:

```bash

# Query traces (admin console + benchmark): days to keep turns that have no
# feedback and no review. Pruned when admin-ui starts and by `artmind traces
# prune`. Leave unset to use the default, 90.
# ARTMIND_TRACE_RETENTION_DAYS=90
```

- [ ] **Step 6: Check the recipes and help**

Run: `just --list | grep traces` and `uv run artmind traces --help`
Expected: four recipes are listed, and the help shows six commands.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md justfile artmind/env.example
git commit -m "docs(traces): CLAUDE.md layout and routing, just recipes, retention env var"
```

---

### Task 17: Full suite and end-to-end verification

- [ ] **Step 1: Run the full hermetic suite**

Run: `just dev-test`
Expected: everything passes. The count is the `master` baseline plus the new `test_traces_*` tests. Fix any failure before continuing.

- [ ] **Step 2: Reinstall, and restart the daemons so none serves stale code**

```bash
just dev-install
```

Then start `serve` again from inside the vault (`cd <vault> && artmind serve &`), because `dev-install` stops it and doesn't restart it. Confirm the restart:

```bash
curl -s http://127.0.0.1:8377/health
```

- [ ] **Step 3: Live turn through the proxied path**

1. Start the admin console from the vault: `artmind admin-ui`.
2. Ask a question that is likely to fall through to vector search. For example, ask about a detail that only appears in prose.
3. Then run:

```bash
artmind traces list --limit 1
artmind traces show <trace_id>
```

Check:
- `turn.status == "done"`, and `answer`, `model` and token counts are filled in.
- The `tool_call` events have unclipped payloads.
- At least one span sits under a `Bash` `tool_call` (its `tool_id` is set), with `command`, `args.domain` and `evidence` (chunks/documents) filled in.
- `retrieval_path`, `fell_through` and `answer_basis` match what the agent did in the trace drawer.
- `warnings` is empty. If it isn't, the daemon is stale: repeat Step 2.

- [ ] **Step 4: Feedback and triage**

Rate the answer "Wrong" with a note in the console. Then run:

```bash
artmind traces triage
artmind traces stats --by all
```

Check that the turn tops the triage list with its rating and note, and that the stats row for its domain reflects it.

- [ ] **Step 5: Benchmark**

Run a two-question benchmark from the admin console's Benchmark tab. Then run `artmind traces list --source benchmark` and check two `benchmark` turns appear, each with spans.

- [ ] **Step 6: Report**

Write down what was verified and anything that didn't behave as the spec says. Commit any fixes, each with a test.

---

## Self-review

**Spec coverage (P1 row of §10):**
- trace tables: Task 1
- `TraceRecorder`: Task 10
- admin capture (§5.1): Tasks 8, 9 and 11
- CLI spans and evidence (§5.2–5.3): Tasks 3 and 6
- proxy (§5.4): Tasks 5 and 7
- LLM calls (§5.5): Task 6, Step 5
- benchmark (§5.6): Task 12
- feedback UI and endpoint (§5.7): Tasks 11 and 13
- retrieval path and fall-through (§4.1): Tasks 4 and 10
- `traces` CLI including `stats` (§6): Tasks 14 and 15
- retention: Tasks 2, 11 and 15
- docs and recipes: Task 16
- end-to-end check: Task 17

`trace_reviews` is created in Task 1 but only P2 writes it, apart from `insert_review_stub`, which P1 uses for `prune` and `triage`.

**Known limits, accepted for P1:**
- `trace_llm_calls.duration_ms` is always null, because `log_llm_call` receives no timing.
- A trace's `domains` value comes only from the `--domain` arguments of its spans.
