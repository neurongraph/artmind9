# test/test_db_update.py
import sqlite3
from datetime import datetime

import pytest

from artmind.db import (
    _get_db,
    _create_update_session,
    _get_update_session,
    _update_session_status,
    _create_update_draft,
    _get_latest_pending_draft,
    _update_draft_status,
    _list_update_sessions,
)


def test_update_sessions_and_drafts_tables_exist():
    conn = _get_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "update_sessions" in tables
    assert "update_drafts" in tables


def test_create_and_get_session():
    _create_update_session("sess1", "general", "alice@example.com")
    session = _get_update_session("sess1")
    assert session is not None
    assert session["domain"] == "general"
    assert session["created_by"] == "alice@example.com"
    assert session["status"] == "draft"


def test_update_session_status():
    _create_update_session("sess2", "fiction", "alice@example.com")
    _update_session_status("sess2", "confirmed")
    session = _get_update_session("sess2")
    assert session["status"] == "confirmed"


def test_create_draft_and_get_latest_pending():
    _create_update_session("sess3", "general", "alice@example.com")
    draft_id = _create_update_draft(
        session_id="sess3",
        raw_text="Alice is the CEO.",
        input_hint="atomic_fact",
        extraction_json='{"entities": [], "relationships": []}',
        candidates_json="[]",
    )
    assert isinstance(draft_id, int)
    draft = _get_latest_pending_draft("sess3")
    assert draft is not None
    assert draft["raw_text"] == "Alice is the CEO."
    assert draft["domain"] == "general"


def test_update_draft_status():
    _create_update_session("sess4", "general", "alice@example.com")
    draft_id = _create_update_draft(
        session_id="sess4",
        raw_text="Bob is a manager.",
        input_hint="atomic_fact",
        extraction_json='{"entities": [], "relationships": []}',
        candidates_json="[]",
    )
    _update_draft_status(draft_id, "confirmed")
    draft = _get_latest_pending_draft("sess4")
    assert draft is None  # confirmed drafts are not returned as pending


def test_list_update_sessions_returns_sessions():
    _create_update_session("sess5", "general", "alice@example.com")
    _create_update_draft(
        session_id="sess5",
        raw_text="Carol joined in 2024.",
        input_hint="atomic_fact",
        extraction_json='{"entities": [], "relationships": []}',
        candidates_json="[]",
    )
    sessions = _list_update_sessions(domain=None, user=None, limit=20)
    ids = [s["session_id"] for s in sessions]
    assert "sess5" in ids
