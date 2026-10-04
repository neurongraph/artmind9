"""The `finalize` block on ingest jobs (plan 2026-10-03, B2).

Against the real (per-test temp) registry DB, so the assertions are on what
SQLite holds and what every read path returns.
"""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

import artmind.db as db
from artmind.cli import cli
from artmind.jobs import (
    _add_finalize_domain,
    _create_job,
    _fetch_active_jobs,
    _get_finalize,
    _get_job_results,
    _get_job_status,
    _set_finalize_state,
)

FILES = ["/vault/a.md", "/vault/b.md"]
NONE = {"state": "none", "domains": [], "error": None}


def test_an_old_jobs_table_gains_the_finalize_columns():
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE ingestion_jobs (
            job_id TEXT PRIMARY KEY, status TEXT NOT NULL, file_count INTEGER NOT NULL,
            processed_count INTEGER DEFAULT 0, queued_at TEXT NOT NULL, started_at TEXT,
            completed_at TEXT, error_message TEXT, results_json TEXT, domain TEXT DEFAULT 'general'
        )
    """)
    conn.execute("INSERT INTO ingestion_jobs (job_id, status, file_count, queued_at) VALUES ('old', 'completed', 1, 't')")
    conn.commit()
    conn.close()

    db._get_db().close()

    conn = sqlite3.connect(db.DB_PATH)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ingestion_jobs)")}
    conn.close()
    assert {"finalize_domains", "finalize_state", "finalize_error"} <= cols
    assert _get_job_status("old")["finalize"] == NONE, "a job from before the columns owes nothing"


def test_a_new_job_owes_nothing_on_every_read_path():
    job_id = _create_job(FILES, domain="general")
    assert _get_job_status(job_id)["finalize"] == NONE
    assert _get_job_results(job_id)["finalize"] == NONE
    assert _fetch_active_jobs()[0]["finalize"] == NONE


def test_owed_domains_accumulate_once_each_and_mark_the_job_pending():
    job_id = _create_job(FILES, domain="general")
    for domain in ("habits", "general", "habits"):
        _add_finalize_domain(job_id, domain)

    expected = {"state": "pending", "domains": ["general", "habits"], "error": None}
    assert _get_finalize(job_id) == expected
    assert _get_job_status(job_id)["finalize"] == expected
    assert _fetch_active_jobs()[0]["finalize"] == expected


def test_a_failed_finalize_carries_its_error_and_done_clears_it():
    job_id = _create_job(FILES, domain="general")
    _add_finalize_domain(job_id, "general")

    _set_finalize_state(job_id, "failed", error="general: Couldn't connect")
    assert _get_job_results(job_id)["finalize"] == {
        "state": "failed", "domains": ["general"], "error": "general: Couldn't connect",
    }

    _set_finalize_state(job_id, "done")
    assert _get_job_status(job_id)["finalize"] == {"state": "done", "domains": ["general"], "error": None}


def test_adding_a_domain_to_an_unknown_job_is_a_no_op():
    _add_finalize_domain("no-such-job", "general")
    assert _get_finalize("no-such-job") == NONE


def test_the_cli_emits_finalize():
    job_id = _create_job(FILES, domain="general")
    _add_finalize_domain(job_id, "general")
    for command in ("job-status", "job-results"):
        result = CliRunner().invoke(cli, ["ingest", command, job_id, "--compact"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["finalize"]["domains"] == ["general"]
    result = CliRunner().invoke(cli, ["ingest", "jobs-active", "--compact"])
    assert json.loads(result.stdout)[0]["finalize"]["state"] == "pending"
