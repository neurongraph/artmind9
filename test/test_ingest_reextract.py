"""`ingest sync|async --reextract`: re-run the LLM extraction of vault notes
whose body has not changed (a schema/prompt change leaves them on the
metadata-only fast path otherwise).

No Neo4j, no LLM. The decision (`plan_vault_native` / `NotePlan.tier`) is
asserted on real notes in a throwaway vault; the flag's journey (CLI -> job row
-> worker -> `ingest_file` -> `extract_kg`) is asserted on the parameters
actually passed at each hop, never on summary counts."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

import artmind.cli as cli_module
import artmind.ingest as ing
import paths
from artmind.cli import cli
from conftest import stage_as_extracted


def _note(vault, rel, text="Hello.\n"):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _ingested(vault, rel="notes/a.md", text="Hello.\n"):
    """A note ingested once, its extraction staged: the state a schema change
    finds it in."""
    note = _note(vault, rel, text)
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(note, "m", "general"))
    return note


# ── the decision ─────────────────────────────────────────────────────────────


def test_an_unchanged_note_stays_on_the_fast_path_without_the_flag(ingest_env):
    vault, _ = ingest_env
    note = _ingested(vault)

    plan = ing.plan_vault_native(note, domain="general")

    assert (plan.tier, plan.reextract) == ("metadata_only", False)


def test_reextract_makes_an_unchanged_note_content_without_a_version_bump(ingest_env):
    vault, _ = ingest_env
    note = _ingested(vault)
    before = ing.plan_vault_native(note, domain="general")

    plan = ing.plan_vault_native(note, domain="general", reextract=True)

    assert (plan.tier, plan.is_new, plan.stamp) == ("content", False, False)
    assert plan.reextract is True
    assert plan.version.version == before.version.version, "an unchanged body is not a new version"
    assert plan.version.tier == "metadata_only", "the body-hash decision itself is untouched"
    assert plan.resolution.artmind_id == before.resolution.artmind_id


def test_planning_with_reextract_writes_nothing(ingest_env):
    vault, _ = ingest_env
    note = _ingested(vault)
    text = note.read_text()

    ing.plan_vault_native(note, domain="general", reextract=True)

    assert note.read_text() == text


def test_ingest_file_reextract_runs_the_content_path_for_an_unchanged_note(ingest_env, monkeypatch):
    vault, _ = ingest_env
    note = _ingested(vault)
    first = ing.plan_vault_native(note, domain="general")

    result = ing.ingest_file(note, "m", "general", reextract=True)

    assert result["status"] == "ok"
    assert result["tier"] == "content"
    assert result["reextract"] is True
    assert result["version"] == first.version.version
    assert result["chunk_count"] >= 1 and Path(result["chunks_dir"]).exists()
    assert ing.kg_work_was_done(result), "the batch must queue its projection rebuild"


def test_ingest_file_without_the_flag_applies_metadata_only(ingest_env, monkeypatch):
    vault, _ = ingest_env
    note = _ingested(vault)
    applied = []
    import artmind.delta as delta

    monkeypatch.setattr(delta, "apply_metadata_only", lambda **kw: applied.append(kw))

    result = ing.ingest_file(note, "m", "general")

    assert (result["tier"], result["reextract"], result["chunk_count"]) == ("metadata_only", False, 0)
    assert len(applied) == 1 and not ing.kg_work_was_done(result)


def test_ingest_file_reextract_ignores_files_that_are_not_vault_notes(ingest_env, monkeypatch):
    vault, _ = ingest_env
    seen = {}

    def fake_binary(source, image_model, domain, job_id, chunk_size, *, set_domain=None):
        seen["called"] = True
        return {"status": "ok"}

    monkeypatch.setattr(ing, "_ingest_binary_derived", fake_binary)
    assert ing.ingest_file(vault / "x.pdf", "m", "general", reextract=True) == {"status": "ok"}
    assert seen == {"called": True}


# ── the extraction cache ─────────────────────────────────────────────────────


def test_reset_extraction_cache_forgets_the_chunk_status_and_cached_chunks(ingest_env, tmp_path, monkeypatch):
    vault, _ = ingest_env
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")  # the reset moves the cache aside there
    chunk_dir = vault / "cache" / "sha-1"
    chunk_dir.mkdir(parents=True)
    (chunk_dir / "chunk_001.json").write_text("{}")
    ing._init_chunk_rows("sha-1", "doc", 2)
    ing._init_chunk_rows("sha-other", "other", 1)
    ing._update_chunk_step("sha-1", 1, "entities", "ok")

    ing._reset_extraction_cache(chunk_dir, "sha-1")

    assert ing._get_chunk_statuses("sha-1") == {}
    assert not chunk_dir.exists()
    assert list(ing._get_chunk_statuses("sha-other")) == [1], "another document's rows are not touched"


@pytest.mark.parametrize("reextract, expect_reset", [(True, True), (False, False)])
def test_extract_kg_resets_its_cache_only_when_asked(ingest_env, monkeypatch, reextract, expect_reset):
    vault, _ = ingest_env
    note = _ingested(vault)
    result = ing.ingest_file(note, "m", "general", reextract=True)  # chunked, as the content tier is
    result["reextract"] = reextract
    schemas = vault / "schemas"
    schemas.mkdir()
    src = Path(ing.__file__).parent / "domains" / "schemas" / "general_schema.yaml"
    (schemas / "general_schema.yaml").write_text(src.read_text())
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)

    calls = []

    class Stop(Exception):
        pass

    def fake_reset(chunk_data_dir, doc_sha256):
        calls.append((chunk_data_dir, doc_sha256))

    def stop(*a, **k):
        raise Stop

    monkeypatch.setattr(ing, "_reset_extraction_cache", fake_reset)
    monkeypatch.setattr(ing, "_document_valid_time", stop)  # first step after the cache setup

    with pytest.raises(Stop):
        ing.extract_kg(result, "general")

    assert bool(calls) is expect_reset
    if expect_reset:
        assert calls[0][1] == result["sha256"]
        assert calls[0][0] == ing.KG_DIR / "general" / "a" / "chunks" / result["sha256"]


# ── the flag's journey through a queued job ──────────────────────────────────


def test_create_job_persists_reextract(tmp_path, monkeypatch):
    import artmind.db as db
    import artmind.jobs as jobs

    dbfile = tmp_path / "reg.db"
    monkeypatch.setattr(db, "DB_PATH", dbfile)

    on = jobs._create_job(["/a.md"], domain="d", reextract=True)
    off = jobs._create_job(["/a.md"], domain="d")

    conn = sqlite3.connect(dbfile)
    rows = dict(conn.execute("SELECT job_id, reextract FROM ingestion_jobs"))
    assert rows == {on: 1, off: 0}


def test_an_old_jobs_table_gains_the_reextract_column(tmp_path, monkeypatch):
    import artmind.db as db

    dbfile = tmp_path / "old.db"
    conn = sqlite3.connect(dbfile)
    conn.execute(
        "CREATE TABLE ingestion_jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, file_count INTEGER NOT NULL,"
        " queued_at TEXT NOT NULL, domain TEXT, force INTEGER DEFAULT 0, stage_only INTEGER DEFAULT 0)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", dbfile)

    db._get_db().close()

    cols = {r[1] for r in sqlite3.connect(dbfile).execute("PRAGMA table_info(ingestion_jobs)")}
    assert "reextract" in cols


@pytest.fixture
def vault(tmp_path, monkeypatch):
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text(
        "ingest:\n  mappings:\n    - path: notes/**\n      domain: general\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", tmp_path)
    (tmp_path / "notes").mkdir()
    for name in ("2026-10-01.md", "2026-10-02.md", "other.md"):
        (tmp_path / "notes" / name).write_text("# n")
    return tmp_path


@pytest.fixture
def queued(monkeypatch):
    jobs = []

    def fake_create_job(batch_files, **kwargs):
        jobs.append(([Path(f).name for f in batch_files], kwargs))
        return "job-1"

    monkeypatch.setattr(cli_module, "_create_job", fake_create_job)
    monkeypatch.setattr(cli_module, "_ensure_worker_running", lambda: None)
    return jobs


def test_async_passes_reextract_to_the_job_and_match_picks_the_files(vault, queued):
    result = CliRunner().invoke(
        cli, ["ingest", "async", "notes", "--domain", "general", "--reextract", "--match", "2026-10-*.md"]
    )

    assert result.exit_code == 0, result.output
    files, kwargs = queued[0]
    assert files == ["2026-10-01.md", "2026-10-02.md"]
    assert kwargs["reextract"] is True


def test_async_without_the_flag_queues_no_reextract(vault, queued):
    result = CliRunner().invoke(cli, ["ingest", "async", "notes", "--domain", "general"])

    assert result.exit_code == 0, result.output
    files, kwargs = queued[0]
    assert len(files) == 3 and kwargs["reextract"] is False


def test_async_match_that_selects_nothing_is_an_error(vault, queued):
    result = CliRunner().invoke(
        cli, ["ingest", "async", "notes", "--domain", "general", "--match", "nope-*.md"]
    )

    assert result.exit_code != 0 and "no file" in result.output
    assert queued == []


def test_async_match_may_use_a_path_relative_to_the_folder(vault, queued):
    (vault / "notes" / "sub").mkdir()
    (vault / "notes" / "sub" / "deep.md").write_text("# d")

    result = CliRunner().invoke(
        cli, ["ingest", "async", "notes", "--domain", "general", "--match", "sub/*.md"]
    )

    assert result.exit_code == 0, result.output
    assert queued[0][0] == ["deep.md"]


def test_sync_passes_reextract_to_ingest_file(vault, monkeypatch):
    calls = []

    def fake_ingest_file(source, image_model, domain=None, **kwargs):
        calls.append((Path(source).name, kwargs))
        return {"status": "ok", "domain": domain, "tier": "content"}

    monkeypatch.setattr(cli_module, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(cli_module, "ingest_to_kg", lambda *a, **k: True)
    monkeypatch.setattr(cli_module, "_require_ingest_extra", lambda: None)

    result = CliRunner().invoke(
        cli, ["ingest", "sync", "notes", "--domain", "general", "--reextract", "--match", "other.md"]
    )

    assert result.exit_code == 0, result.output
    assert [(n, kw["reextract"]) for n, kw in calls] == [("other.md", True)]


def test_worker_hands_the_jobs_reextract_to_ingest_file(tmp_path, monkeypatch):
    import artmind.worker as worker

    seen = []

    def fake_ingest_file(source, image_model, domain=None, **kwargs):
        seen.append(kwargs.get("reextract"))
        return {"status": "ok", "domain": domain}

    monkeypatch.setattr(worker, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(worker, "ingest_to_kg", lambda *a, **k: True)
    monkeypatch.setattr(worker, "_update_job_file_status", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_update_job_status", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_count_processed", lambda job_id: 0)
    monkeypatch.setattr(worker, "_final_file_statuses", lambda job_id: [])
    monkeypatch.setattr(worker, "_finalize_job", lambda job_id: None)
    monkeypatch.setattr(worker, "_get_queued_files", lambda job_id: [str(tmp_path / "a.md")])
    monkeypatch.setattr(worker, "ARTMIND_VAULT_DIR", None, raising=False)

    worker._process_job("job-1", "general", {}, reextract=True)
    worker._process_job("job-2", "general", {})

    assert seen == [True, False]


def test_worker_loop_reads_reextract_from_the_job_row(tmp_path, monkeypatch):
    import artmind.db as db
    import artmind.jobs as jobs
    import artmind.worker as worker

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    jobs._create_job(["/a.md"], domain="general", reextract=True)
    processed = []

    def fake_process(job_id, domain, env, force=False, stage_only=False, reextract=False):
        processed.append((domain, force, stage_only, reextract))
        sqlite3.connect(tmp_path / "reg.db").execute(
            "UPDATE ingestion_jobs SET status='completed'"
        ).connection.commit()

    monkeypatch.setattr(worker, "_process_job", fake_process)

    worker._worker_loop({})

    assert processed == [("general", False, False, True)]


def test_classify_reingest_reextract_reports_content_for_an_unchanged_body(tmp_path, monkeypatch):
    import artmind.delta as delta

    note = _note(tmp_path, "n.md", "---\n_domain: general\n---\n\nBody.\n")
    monkeypatch.setattr(delta, "_read_prior_document", lambda lid, dom: {"id": "d1", "version": 2})
    hashes = delta._compute_body_block_hashes(note.read_text(), chunk_size=6000)
    monkeypatch.setattr(delta, "_read_prior_block_hashes", lambda doc_id: hashes)

    plain = delta.classify_reingest(note, domain="general", logical_id="L")
    forced = delta.classify_reingest(note, domain="general", logical_id="L", reextract=True)

    assert plain.tier == "metadata_only"
    assert forced.tier == "content"
    assert (forced.doc_id, forced.version) == ("d1", 2)
    assert "reextract" in forced.reason
