"""Regression test: `_retry_job` deregisters failed files from the registry
by matching a bare filename against `path`'s own basename.

Phase 5's registry shrink dropped the `filename` column (docs/redesign-
phase-plan.md, "E") -- `_retry_job` was the one caller this repo-wide grep
missed the first time around: `DELETE FROM documents WHERE domain = ? AND
UPPER(filename) = ?` would raise `sqlite3.OperationalError: no such column:
filename` the moment a failed job was retried, because `_retry_job` is only
ever mocked in test_webui_admin_api.py, never exercised against a real
registry db.
"""
import sqlite3

import artmind.db as db
import artmind.jobs as jobs


def _patch_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    return db


def test_retry_job_deregisters_failed_files_by_bare_filename(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)
    db._init_db()

    job_id = jobs._create_job(["/incoming/deck.pptx", "/incoming/ok.pptx"], domain="banking")
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "UPDATE ingestion_job_files SET status='failed' WHERE job_id=? AND filename=?",
        (job_id, "/incoming/deck.pptx"),
    )
    conn.execute(
        "INSERT INTO documents (artmind_id, domain, path, content_sha256, last_ingested_at)"
        " VALUES (NULL, 'banking', 'documents/originals/deck.pptx', 'x', 'now')"
    )
    conn.commit()
    conn.close()

    result = jobs._retry_job(job_id)

    assert result["retried"] == 1
    assert result["deregistered"] == 1

    conn = sqlite3.connect(db.DB_PATH)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE path = 'documents/originals/deck.pptx'"
        ).fetchone()[0]
        assert remaining == 0
    finally:
        conn.close()


def test_retry_job_leaves_unrelated_registry_rows_alone(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)
    db._init_db()

    job_id = jobs._create_job(["/incoming/deck.pptx"], domain="banking")
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "UPDATE ingestion_job_files SET status='failed' WHERE job_id=? AND filename=?",
        (job_id, "/incoming/deck.pptx"),
    )
    conn.execute(
        "INSERT INTO documents (artmind_id, domain, path, content_sha256, last_ingested_at)"
        " VALUES (NULL, 'banking', 'documents/originals/other.pptx', 'x', 'now')"
    )
    conn.commit()
    conn.close()

    jobs._retry_job(job_id)

    conn = sqlite3.connect(db.DB_PATH)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE path = 'documents/originals/other.pptx'"
        ).fetchone()[0]
        assert remaining == 1
    finally:
        conn.close()
