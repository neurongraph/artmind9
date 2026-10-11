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
