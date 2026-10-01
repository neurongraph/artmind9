"""Per-file entity/relationship counts on ingest jobs, and the job-chunks lookup.

Runs against the real (per-test temp) registry DB the conftest provides, so
the assertions are on what the job read paths actually return from SQLite --
not on a mock that answers every query (CLAUDE.md).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner
from fastapi.testclient import TestClient

import artmind.db as db
import artmind.ingest as ing
import artmind.worker as worker_module
from artmind.cli import cli
from artmind.jobs import (
    _create_job,
    _fetch_active_jobs,
    _fetch_completed_jobs,
    _get_job_results,
    _get_job_status,
    _job_file_sha256,
    _update_job_file_status,
    _update_job_status,
)
from artmind.webui.app import create_app

PDF = "/vault/Reports/Blue Carbuncle.pdf"
NOTE = "/vault/Notes/plain.md"


def _job() -> str:
    return _create_job([PDF, NOTE], domain="fiction")


def _files_by_name(report: dict) -> dict:
    return {Path(f["filename"]).name: f for f in report["files"]}


# ── migration ────────────────────────────────────────────────────────────────

def test_an_old_job_files_table_gains_the_count_columns():
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE ingestion_job_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, status TEXT NOT NULL,
            filename TEXT NOT NULL, current_step TEXT, doc_sha256 TEXT, started_at TEXT,
            completed_at TEXT, error_message TEXT
        )
    """)
    conn.execute("INSERT INTO ingestion_job_files (job_id, status, filename) VALUES ('old', 'completed', 'a.md')")
    conn.commit()
    conn.close()

    db._get_db().close()

    conn = sqlite3.connect(db.DB_PATH)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ingestion_job_files)")}
    row = conn.execute("SELECT entity_count, relationship_count FROM ingestion_job_files").fetchone()
    conn.close()
    assert {"entity_count", "relationship_count"} <= cols
    assert row == (None, None), "pre-existing rows have no count, not a zero count"


# ── read paths ───────────────────────────────────────────────────────────────

def test_counts_are_null_until_a_file_is_extracted():
    job_id = _job()
    for report in (_get_job_status(job_id), _get_job_results(job_id), _fetch_active_jobs()[0]):
        for f in report["files"]:
            assert f["entities"] is None and f["relationships"] is None


def test_every_job_read_path_returns_the_recorded_counts():
    job_id = _job()
    _update_job_file_status(job_id, PDF, status="completed", kg_counts={"entities": 12, "relationships": 7})

    active = _fetch_active_jobs()[0]
    assert _files_by_name(active)["Blue Carbuncle.pdf"]["entities"] == 12
    for report in (_get_job_status(job_id), _get_job_results(job_id)):
        files = _files_by_name(report)
        assert (files["Blue Carbuncle.pdf"]["entities"], files["Blue Carbuncle.pdf"]["relationships"]) == (12, 7)
        assert files["plain.md"]["entities"] is None, "only the file that was updated"

    _update_job_status(job_id, status="completed", completed_at="2026-10-01T00:00:00")
    completed = _files_by_name(_fetch_completed_jobs()[0])
    assert completed["Blue Carbuncle.pdf"]["relationships"] == 7


def test_the_cli_emits_the_counts():
    job_id = _job()
    _update_job_file_status(job_id, PDF, kg_counts={"entities": 3, "relationships": 1})
    for command in ("job-status", "job-results"):
        result = CliRunner().invoke(cli, ["ingest", command, job_id, "--compact"])
        assert result.exit_code == 0, result.output
        files = _files_by_name(json.loads(result.output))
        assert files["Blue Carbuncle.pdf"]["entities"] == 3


# ── staged_counts / ingest_to_kg ────────────────────────────────────────────

def _stage(folder: Path, observations: list, relationships: list) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "observations.json").write_text(json.dumps(observations))
    (folder / "relationships.json").write_text(json.dumps(relationships))
    return folder


def test_staged_counts_counts_distinct_entities_not_observations(tmp_path):
    folder = _stage(
        tmp_path / "doc",
        [{"key": "PERSON:holmes"}, {"key": "PERSON:holmes"}, {"key": "PERSON:watson"}],
        [{"type": "knows"}, {"type": "lives_with"}],
    )
    assert ing.staged_counts(folder) == {"entities": 2, "relationships": 2}


def test_staged_counts_is_none_for_an_unreadable_folder(tmp_path):
    assert ing.staged_counts(tmp_path / "missing") is None


def test_ingest_to_kg_records_the_counts_it_staged(tmp_path, monkeypatch):
    folder = _stage(tmp_path / "doc", [{"key": "A"}, {"key": "B"}], [{"type": "r"}])
    monkeypatch.setattr(ing, "extract_kg", lambda fr, d, tm, em: folder)
    monkeypatch.setattr(ing, "commit_to_graph", lambda p, d, defer_rebuild=False: True)
    file_result = {"chunks_dir": str(tmp_path), "chunk_count": 1}

    assert ing.ingest_to_kg(file_result, "fiction") is True
    assert file_result["kg_counts"] == {"entities": 2, "relationships": 1}


def test_a_no_op_file_gets_no_counts(monkeypatch):
    monkeypatch.setattr(ing, "extract_kg", lambda *a: (_ for _ in ()).throw(AssertionError("must not extract")))
    file_result = {"tier": "no_op"}
    assert ing.ingest_to_kg(file_result, "fiction") is True
    assert "kg_counts" not in file_result


def test_the_worker_writes_the_counts_to_the_job(monkeypatch, tmp_path):
    job_id = _create_job([NOTE], domain="fiction")

    def fake_ingest_to_kg(result, *a, **k):
        result["kg_counts"] = {"entities": 4, "relationships": 2}
        return True

    monkeypatch.setattr(worker_module, "ARTMIND_VAULT_DIR", tmp_path, raising=False)
    monkeypatch.setattr(worker_module, "ingest_file", lambda *a, **k: {"status": "ok", "sha256": "s1"})
    monkeypatch.setattr(worker_module, "ingest_to_kg", fake_ingest_to_kg)
    monkeypatch.setattr(ing, "rebuild_projection", lambda d: {}, raising=False)

    worker_module._process_job(job_id=job_id, domain="fiction", env={})

    f = _get_job_status(job_id)["files"][0]
    assert (f["status"], f["entities"], f["relationships"]) == ("completed", 4, 2)


# ── job-chunks finds a converted binary by its job row ─────────────────────

def test_job_file_sha256_reads_the_job_row():
    job_id = _job()
    assert _job_file_sha256(job_id, "Blue Carbuncle.pdf") is None, "no sha before extract_kg"
    _update_job_file_status(job_id, PDF, current_step="extract_kg", doc_sha256="sha-pdf")
    assert _job_file_sha256(job_id, "blue carbuncle.PDF") == "sha-pdf"
    assert _job_file_sha256(job_id, "plain.md") is None


def test_job_chunks_finds_a_binary_the_registry_knows_only_by_its_markdown(monkeypatch):
    """Found live: a converted binary registers under its derived markdown,
    so a registry lookup by `….pdf` failed for every binary, in both the CLI
    and the admin console's chunk grid."""
    import artmind.cli as cli_module
    from artmind.webui import dashboard_routes

    job_id = _job()
    _update_job_file_status(job_id, PDF, current_step="extract_kg", doc_sha256="sha-pdf")
    asked = []
    fake_chunks = lambda sha: asked.append(sha) or [{"seq": 1, "e": "ok", "p": "ok", "r": "pending"}]
    monkeypatch.setattr(cli_module, "_fetch_chunks", fake_chunks)
    monkeypatch.setattr(dashboard_routes, "_fetch_chunks", fake_chunks)

    result = CliRunner().invoke(cli, ["ingest", "job-chunks", job_id, "Blue Carbuncle.pdf", "--compact"])
    assert result.exit_code == 0, result.output
    response = TestClient(create_app(admin_routes=True)).get(
        f"/api/jobs/{job_id}/chunks", params={"doc": "Blue Carbuncle.pdf"}
    )
    assert response.status_code == 200
    assert asked == ["sha-pdf", "sha-pdf"]
