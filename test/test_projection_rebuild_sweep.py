"""`artmind projection rebuild --sweep` (plan 2026-10-03, B4): what the
plugin's **Rebuild graph** runs. Asserts on the calls actually made -- which
rebuild, which sweep, for which domain, with which keys -- and on the job
rows SQLite holds afterwards.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import artmind.ingest as ing
import artmind.projection as projection
from artmind.cli import cli
from artmind.jobs import _add_finalize_domain, _create_job, _get_job_status, _set_finalize_state

KEYS = {
    ("ann lee", "PERSON", "general"),
    ("acme", "ORGANIZATION", "general"),
    ("morning run", "HABIT", "habits"),
}


@pytest.fixture
def calls(monkeypatch):
    seen: dict[str, list] = {"rebuild": [], "entities": [], "chunks": []}
    monkeypatch.setattr(
        projection, "full_rebuild_batched",
        lambda domains=None: seen["rebuild"].append(domains) or {
            "rebuilt": 3, "deleted": 0, "absent": 0, "keys": 3, "batches": 1,
            "domains": ["general", "habits"], "recorded": domains is None,
        },
    )
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: set(KEYS))
    monkeypatch.setattr(
        ing, "_sweep_embeddings",
        lambda domain, keys, strict=False: seen["entities"].append((domain, sorted(keys), strict)) or len(keys),
    )
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings",
        lambda chunk_ids=None, domain=None, strict=False: seen["chunks"].append((domain, strict)) or 1,
    )
    return seen


def _failed_job() -> str:
    job_id = _create_job(["/vault/a.md", "/vault/b.md"], domain="general")
    _add_finalize_domain(job_id, "general")
    _set_finalize_state(job_id, "failed", error="general: Couldn't connect")
    return job_id


def _run(*args: str):
    return CliRunner().invoke(cli, ["projection", "rebuild", *args, "--compact"])


def test_sweep_rebuilds_every_domain_then_sweeps_each_domain_it_rebuilt(calls):
    result = _run("--sweep")

    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert calls["rebuild"] == [None], "a full, unscoped rebuild -- the only kind that clears drift"
    assert calls["entities"] == [
        ("general", sorted(k for k in KEYS if k[2] == "general"), True),
        ("habits", [("morning run", "HABIT", "habits")], True),
    ]
    assert calls["chunks"] == [("general", True), ("habits", True)]
    assert (out["domains_swept"], out["embedded"], out["chunks_embedded"], out["sweep_errors"]) == (
        ["general", "habits"], 3, 2, [],
    )
    assert out["recorded"] is True


def test_a_clean_sweep_clears_a_jobs_failed_finalize(calls):
    job_id = _failed_job()

    out = json.loads(_run("--sweep").stdout)

    assert out["finalize_resolved"] == [job_id]
    assert _get_job_status(job_id)["finalize"] == {"state": "done", "domains": ["general"], "error": None}


def test_a_skipped_sweep_is_reported_and_resolves_no_job(calls, monkeypatch):
    job_id = _failed_job()

    def refuse(chunk_ids=None, domain=None, strict=False):
        raise ing.SweepSkipped(f"chunk embed sweep skipped for domain {domain!r} (Connection refused)")

    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", refuse)

    out = json.loads(_run("--sweep").stdout)

    assert out["sweep_errors"] == [
        "general: chunk embed sweep skipped for domain 'general' (Connection refused)",
        "habits: chunk embed sweep skipped for domain 'habits' (Connection refused)",
    ]
    assert out["finalize_resolved"] == []
    assert _get_job_status(job_id)["finalize"]["state"] == "failed"


def test_sweep_refuses_a_domain(calls):
    result = _run("--sweep", "--domain", "general")

    assert result.exit_code == 2
    assert "drop --domain" in result.output
    assert calls["rebuild"] == []
