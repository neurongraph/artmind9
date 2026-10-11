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
