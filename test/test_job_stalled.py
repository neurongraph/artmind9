"""Stalled ingest jobs: detected, and retried.

A job is stalled when it is `processing` but no worker holds the pid-file
lock -- the worker died mid-job and nothing will finish it. A live worker is
simulated by holding that lock, exactly as `worker._acquire_pid_file` does.
Assertions are on the job rows SQLite actually holds afterwards.
"""
from __future__ import annotations

import fcntl
import json

import pytest
from click.testing import CliRunner

import artmind.cli as cli_module
import paths
from artmind.cli import cli
from artmind.jobs import (
    _create_job,
    _fetch_active_jobs,
    _get_job_status,
    _retry_job,
    _update_job_file_status,
    _update_job_status,
    worker_alive,
)

FILES = ["/vault/a.md", "/vault/b.md", "/vault/c.md"]


@pytest.fixture(autouse=True)
def pid_file(tmp_path, monkeypatch):
    """Keep every liveness check on a temp pid file, never the run folder's."""
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    return tmp_path / "worker.pid"


@pytest.fixture
def live_worker(pid_file):
    fd = open(pid_file, "w")
    fd.write("4242")
    fd.flush()
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield
    fd.close()


@pytest.fixture
def started(monkeypatch):
    """Record worker starts instead of spawning one."""
    calls: list[bool] = []
    monkeypatch.setattr(cli_module, "_ensure_worker_running", lambda: calls.append(True))
    return calls


def _died_mid_job() -> str:
    """a.md done, the worker died extracting b.md, c.md never started."""
    job_id = _create_job(FILES, domain="general")
    _update_job_status(job_id, status="processing", processed_count=1, started_at="2026-10-01T00:00:00")
    _update_job_file_status(job_id, FILES[0], status="completed", kg_counts={"entities": 3, "relationships": 1})
    _update_job_file_status(
        job_id, FILES[1], status="processing", current_step="extract_kg", doc_sha256="sha-b",
        kg_counts={"entities": 9, "relationships": 9},
    )
    return job_id


def _statuses(job_id: str) -> dict[str, str]:
    return {f["filename"]: f["status"] for f in _get_job_status(job_id)["files"]}


# ── detection ───────────────────────────────────────────────────────────────

def test_a_processing_job_without_a_worker_is_stalled():
    job_id = _died_mid_job()
    assert worker_alive() is False
    assert _get_job_status(job_id)["stalled"] is True
    assert _fetch_active_jobs()[0]["stalled"] is True


def test_a_processing_job_with_a_live_worker_is_not(live_worker):
    job_id = _died_mid_job()
    assert worker_alive() is True
    assert _get_job_status(job_id)["stalled"] is False


def test_a_queued_job_is_never_stalled():
    """The moment between submitting and the worker taking its lock."""
    job_id = _create_job(FILES, domain="general")
    assert _get_job_status(job_id)["stalled"] is False


# ── retry ───────────────────────────────────────────────────────────────────

def test_retrying_a_stalled_job_requeues_the_file_its_worker_died_on():
    job_id = _died_mid_job()

    result = _retry_job(job_id)

    assert result["stalled"] is True
    assert result["files"] == [FILES[1]]
    assert _statuses(job_id) == {FILES[0]: "completed", FILES[1]: "queued", FILES[2]: "queued"}
    status = _get_job_status(job_id)
    assert (status["status"], status["processed_count"]) == ("queued", 1)
    b = next(f for f in status["files"] if f["filename"] == FILES[1])
    assert (b["entities"], b["relationships"]) == (None, None), "a reset file's old counts must not survive"


def test_a_stalled_job_with_nothing_failed_is_still_requeued():
    """The worker died between files: nothing is at processing, but the job
    still has queued files nobody will run."""
    job_id = _create_job(FILES, domain="general")
    _update_job_status(job_id, status="processing", processed_count=1)
    _update_job_file_status(job_id, FILES[0], status="completed")

    result = _retry_job(job_id)

    assert (result["retried"], result["stalled"]) == (0, True)
    assert _get_job_status(job_id)["status"] == "queued"


def test_retrying_a_job_a_live_worker_still_runs_is_refused(live_worker):
    job_id = _died_mid_job()
    with pytest.raises(ValueError, match="still running"):
        _retry_job(job_id)
    assert _statuses(job_id)[FILES[1]] == "processing", "nothing reset underneath the worker"


def test_retrying_a_failed_job_resets_only_its_failed_files():
    job_id = _create_job(FILES, domain="general")
    _update_job_file_status(job_id, FILES[0], status="completed")
    _update_job_file_status(job_id, FILES[1], status="failed", error_message="KG ingestion failed")
    _update_job_file_status(job_id, FILES[2], status="completed")
    _update_job_status(job_id, status="failed", processed_count=3)

    result = _retry_job(job_id)

    assert (result["files"], result["stalled"]) == ([FILES[1]], False)
    assert _statuses(job_id) == {FILES[0]: "completed", FILES[1]: "queued", FILES[2]: "completed"}


# ── the CLI ─────────────────────────────────────────────────────────────────

def test_retry_job_compact_reports_json_and_starts_the_worker(started):
    job_id = _died_mid_job()

    result = CliRunner().invoke(cli, ["ingest", "retry-job", job_id, "--compact"])

    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert (out["requeued"], out["stalled"], out["files"]) == (True, True, [FILES[1]])
    assert started == [True]


def test_retry_job_with_nothing_to_retry_starts_no_worker(started):
    job_id = _create_job(FILES[:1], domain="general")
    _update_job_file_status(job_id, FILES[0], status="completed")
    _update_job_status(job_id, status="completed", processed_count=1)

    result = CliRunner().invoke(cli, ["ingest", "retry-job", job_id, "--compact"])

    assert result.exit_code == 0, (result.output, result.exception)
    assert json.loads(result.output)["requeued"] is False
    assert started == []


def test_retry_job_refuses_a_running_job(live_worker, started):
    job_id = _died_mid_job()
    result = CliRunner().invoke(cli, ["ingest", "retry-job", job_id, "--compact"])
    assert result.exit_code != 0
    assert "still running" in result.output
    assert started == []
