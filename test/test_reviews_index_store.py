"""trace_reviews as a (review_id, trace_id) link table, and its index helpers."""

import json
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _pk(table):
    import artmind.db as db

    conn = db._get_db()
    try:
        rows = [r for r in conn.execute(f"PRAGMA table_info({table})") if r[5]]
        return [r[1] for r in sorted(rows, key=lambda r: r[5])]
    finally:
        conn.close()


def test_trace_reviews_primary_key_is_review_and_trace():
    assert _pk("trace_reviews") == ["review_id", "trace_id"]


def test_old_single_key_table_is_recreated(tmp_path, monkeypatch):
    import artmind.db as db

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trace_reviews (review_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL,"
                 " stage TEXT, elements TEXT, status TEXT NOT NULL DEFAULT 'open', dossier_path TEXT,"
                 " updated_at REAL NOT NULL)")
    conn.execute("INSERT INTO trace_reviews VALUES ('rv-old', 't1', NULL, NULL, 'open', NULL, 0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    assert _pk("trace_reviews") == ["review_id", "trace_id"]


def test_index_review_replaces_rows_for_that_review():
    from artmind.traces import store

    store.index_review("rv-1", ["t1", "t2", "t1"], stage="schema", elements=["d/A-REL->"],
                       status="open", dossier_path="/x/rv-1.md")
    store.index_review("rv-1", ["t2"], stage="schema", elements=[], status="fixed", dossier_path="/x/rv-1.md")
    store.index_review("rv-2", ["t3"], stage=None, elements=[], status="open", dossier_path="/x/rv-2.md")

    import artmind.db as db

    conn = db._get_db()
    try:
        rows = conn.execute("SELECT review_id, trace_id, status, elements FROM trace_reviews"
                            " ORDER BY review_id, trace_id").fetchall()
    finally:
        conn.close()
    assert rows == [("rv-1", "t2", "fixed", "[]"), ("rv-2", "t3", "open", "[]")]


def test_clear_review_index():
    from artmind.traces import store

    store.index_review("rv-1", ["t1", "t2"], stage=None, elements=[], status="open", dossier_path=None)
    assert store.clear_review_index() == 2
    assert store.clear_review_index() == 0


def test_reviewed_turns_still_excluded_from_triage_and_prune():
    import time

    from artmind.traces import store

    store.insert_turn("t1", source="admin", question="q", started_at=time.time() - 200 * 86400)
    store.index_review("rv-1", ["t1"], stage="routing", elements=[], status="open", dossier_path=None)
    assert store.list_turns()[0]["reviewed"] == 1
    assert store.prune(90) == 0
