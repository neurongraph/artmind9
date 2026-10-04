"""The worker's deferred rebuild is owed by the job row, not by the run
(plan 2026-10-03, B2).

Job 5f5c2d11: the deferred rebuild killed Neo4j, the uncaught error killed the
worker, and the retry re-ran the job with 0 files -- which rebuilt nothing,
because only files processed in the current run counted -- and marked it
`completed`. Assertions are on the job rows SQLite holds and on the rebuild
calls actually made, in order.
"""
from __future__ import annotations

import pytest

import artmind.ingest as ing
import artmind.worker as worker_module
import paths
from artmind.jobs import (
    _add_finalize_domain,
    _create_job,
    _get_finalize,
    _get_job_status,
    _retry_job,
    _update_job_file_status,
    _update_job_status,
)

FILES = ["/vault/notes/a.md", "/vault/habits/b.md"]


def _domain_of(path) -> str:
    return "habits" if "habits" in str(path) else "general"


@pytest.fixture
def events(tmp_path, monkeypatch):
    """Record each file commit and each rebuild, in order; no worker is alive."""
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", None)
    monkeypatch.setattr(worker_module, "ARTMIND_VAULT_DIR", None)
    log: list[tuple] = []
    monkeypatch.setattr(
        worker_module, "ingest_file",
        lambda path, *a, **k: {"status": "ok", "sha256": f"sha-{path}", "domain": _domain_of(path)},
    )
    monkeypatch.setattr(
        worker_module, "ingest_to_kg",
        lambda result, domain, *a, **k: log.append(("commit", domain, k.get("defer_rebuild"))) or True,
    )
    monkeypatch.setattr(worker_module, "kg_work_was_done", lambda result: True)
    return log


def test_a_multi_file_job_persists_what_it_owes_then_rebuilds_each_domain_once(events, monkeypatch):
    job_id = _create_job(FILES, domain="general")

    def rebuild(domain):
        assert _get_finalize(job_id)["state"] == "pending", "owed domains are on the row before the rebuild runs"
        events.append(("rebuild", domain))
        return {"sweep_errors": []}

    monkeypatch.setattr(ing, "rebuild_projection", rebuild)

    worker_module._process_job(job_id, "general", env={})

    assert events == [
        ("commit", "general", True), ("commit", "habits", True),
        ("rebuild", "general"), ("rebuild", "habits"),
    ]
    status = _get_job_status(job_id)
    assert status["status"] == "completed"
    assert status["finalize"] == {"state": "done", "domains": ["general", "habits"], "error": None}


def test_a_retried_stalled_job_with_no_queued_files_still_rebuilds_what_it_owes(events, monkeypatch):
    job_id = _create_job(FILES, domain="general")
    _update_job_status(job_id, status="processing", processed_count=2, started_at="2026-10-03T10:00:00")
    for f in FILES:
        _update_job_file_status(job_id, f, status="completed")
    _add_finalize_domain(job_id, "general")  # the worker died inside the deferred rebuild
    assert _retry_job(job_id)["stalled"] is True

    monkeypatch.setattr(worker_module, "ingest_file", lambda *a, **k: pytest.fail("no file is queued"))
    rebuilds: list = []
    monkeypatch.setattr(ing, "rebuild_projection", lambda d: rebuilds.append(d) or {"sweep_errors": []})

    worker_module._process_job(job_id, "general", env={})

    assert rebuilds == ["general"]
    status = _get_job_status(job_id)
    assert status["status"] == "completed"
    assert status["finalize"] == {"state": "done", "domains": ["general"], "error": None}


def test_a_failing_rebuild_marks_finalize_failed_and_the_worker_survives(events, monkeypatch):
    job_id = _create_job(FILES, domain="general")
    attempted: list = []

    def rebuild(domain):
        attempted.append(domain)
        if domain == "general":
            raise RuntimeError("projection rebuild failed on batch 3/11 (Couldn't connect)")
        return {"sweep_errors": []}

    monkeypatch.setattr(ing, "rebuild_projection", rebuild)

    worker_module._process_job(job_id, "general", env={})  # must not raise

    assert attempted == ["general", "habits"], "one domain failing must not skip the next"
    status = _get_job_status(job_id)
    assert status["status"] == "completed", "every file committed; the finalize failure is reported separately"
    assert status["finalize"] == {
        "state": "failed", "domains": ["general", "habits"],
        "error": "general: projection rebuild failed on batch 3/11 (Couldn't connect)",
    }


def test_a_skipped_sweep_marks_finalize_failed(events, monkeypatch):
    job_id = _create_job(FILES, domain="general")
    monkeypatch.setattr(
        ing, "rebuild_projection",
        lambda d: {"sweep_errors": [f"entity embed sweep skipped for {d} (Connection refused)"]},
    )

    worker_module._process_job(job_id, "general", env={})

    finalize = _get_job_status(job_id)["finalize"]
    assert finalize["state"] == "failed"
    assert finalize["error"] == (
        "general: entity embed sweep skipped for general (Connection refused); "
        "habits: entity embed sweep skipped for habits (Connection refused)"
    )


def test_a_single_file_job_defers_nothing_and_owes_nothing(events, monkeypatch):
    job_id = _create_job(FILES[:1], domain="general")
    monkeypatch.setattr(ing, "rebuild_projection", lambda d: pytest.fail("a single file rebuilds inline"))

    worker_module._process_job(job_id, "general", env={})

    assert events == [("commit", "general", False)]
    assert _get_job_status(job_id)["finalize"] == {"state": "none", "domains": [], "error": None}
