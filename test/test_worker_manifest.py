"""The background worker applies the manifest per file, like `ingest sync`.

Asserts on the DOMAIN each file was ingested with, by recording the calls --
never on summary counts, which can report success for work that never happened
(CLAUDE.md).

`_process_job` fetches its own file list from the registry, so the queue is
stubbed rather than seeded: this is a test of domain resolution, not of job
bookkeeping.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import artmind.ingest as ingest_module
import artmind.worker as worker_module


@pytest.fixture
def vault(tmp_path, monkeypatch):
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text("""
ingest:
  mappings:
    - path: policies/**
      domain: banking.policy
    - path: notes/**
      domain: personal_journal
""")
    monkeypatch.setattr(worker_module, "ARTMIND_VAULT_DIR", tmp_path, raising=False)
    return tmp_path


@pytest.fixture
def recorded(monkeypatch):
    """Record (filename, domain) per ingest_file call; stub the rest of the job."""
    calls: list[tuple[str, str | None]] = []

    def fake_ingest_file(source, image_model, domain=None, **kwargs):
        calls.append((Path(source).name, domain))
        return {"status": "ok", "domain": domain}

    monkeypatch.setattr(worker_module, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(worker_module, "ingest_to_kg", lambda *a, **k: True)
    monkeypatch.setattr(worker_module, "_update_job_file_status", lambda *a, **k: None)
    monkeypatch.setattr(worker_module, "_update_job_status", lambda *a, **k: None)
    monkeypatch.setattr(worker_module, "_count_processed", lambda job_id: 0)
    monkeypatch.setattr(worker_module, "_final_file_statuses", lambda job_id: [])
    # Imported inside _process_job at call time, so patch it at its source.
    monkeypatch.setattr(ingest_module, "rebuild_projection", lambda d: {}, raising=False)
    return calls


def test_the_worker_ingests_each_file_under_its_mapped_domain(vault, recorded, monkeypatch):
    for folder, name in (("policies", "p.md"), ("notes", "n.md")):
        (vault / folder).mkdir()
        (vault / folder / name).write_text("# x")
    monkeypatch.setattr(worker_module, "_get_queued_files", lambda job_id: [
        str(vault / "policies" / "p.md"), str(vault / "notes" / "n.md"),
    ])

    worker_module._process_job(job_id="job-1", domain="general", env={})

    assert dict(recorded) == {"p.md": "banking.policy", "n.md": "personal_journal"}


def test_an_unmapped_file_falls_back_to_the_job_domain(vault, recorded, monkeypatch):
    """A file queued by name from an unmapped folder still ingests, under the
    domain the job was submitted with."""
    (vault / "scratch").mkdir()
    (vault / "scratch" / "s.md").write_text("# s")
    monkeypatch.setattr(worker_module, "_get_queued_files",
                        lambda job_id: [str(vault / "scratch" / "s.md")])

    worker_module._process_job(job_id="job-1", domain="general", env={})

    assert recorded == [("s.md", "general")]


def test_a_malformed_manifest_does_not_abort_the_queue(vault, recorded, monkeypatch):
    """Unlike the CLI, which refuses to start a run the user is watching, the
    worker is draining a queue in the background -- failing every job silently
    would be worse than processing them under the job's own domain."""
    (vault / ".artmind" / "vault.yaml").write_text("ingest:\n  mappings:\n    - path: notes/**\n")
    (vault / "notes").mkdir()
    (vault / "notes" / "n.md").write_text("# n")
    monkeypatch.setattr(worker_module, "_get_queued_files",
                        lambda job_id: [str(vault / "notes" / "n.md")])

    worker_module._process_job(job_id="job-1", domain="general", env={})

    assert recorded == [("n.md", "general")]
