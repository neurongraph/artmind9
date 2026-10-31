"""`_retry_job` deregisters a failed file's own registry row, found by the
path that file was registered under -- never by bare filename.

History: Phase 5's registry shrink dropped the `filename` column and
`_retry_job` was moved to match a bare filename against `path`'s basename.
That deleted every same-named document's row in the domain: retrying one
failed `Notes.md` dropped the row of an unrelated `Notes.md` elsewhere in the
vault (spec 2026-10-31 document-key-collisions: same-named sources are
different documents).
"""
import sqlite3

import artmind.db as db
import artmind.jobs as jobs


def _row(path: str, domain: str = "banking") -> None:
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "INSERT INTO documents (artmind_id, domain, path, content_sha256, last_ingested_at)"
        " VALUES (NULL, ?, ?, 'x', 'now')",
        (domain, path),
    )
    conn.commit()
    conn.close()


def _fail(job_id: str, filename: str) -> None:
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "UPDATE ingestion_job_files SET status='failed' WHERE job_id=? AND filename=?",
        (job_id, filename),
    )
    conn.commit()
    conn.close()


def _paths() -> set[str]:
    conn = sqlite3.connect(db.DB_PATH)
    try:
        return {r[0] for r in conn.execute("SELECT path FROM documents")}
    finally:
        conn.close()


def _vault(tmp_path, monkeypatch):
    import artmind.document_identity as di
    import artmind.ingest as ing

    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    return vault


def test_retry_job_deregisters_only_the_failed_note_not_a_same_named_one(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    for folder in ("a", "b"):
        (vault / folder).mkdir()
        (vault / folder / "Notes.md").write_text("body\n")
    _row("a/Notes.md")
    _row("b/Notes.md")
    failed = str(vault / "a" / "Notes.md")
    job_id = jobs._create_job([failed, str(vault / "b" / "Notes.md")], domain="banking")
    _fail(job_id, failed)

    result = jobs._retry_job(job_id)

    assert result["retried"] == 1
    assert result["deregistered"] == 1
    assert _paths() == {"b/Notes.md"}


def test_retry_job_deregisters_a_vaultless_original_copy(tmp_path, monkeypatch):
    import artmind.document_identity as di
    import artmind.ingest as ing

    originals = tmp_path / "originals"
    originals.mkdir()
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", None)
    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", None)
    monkeypatch.setattr(ing, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    _row(str((originals / "deck.pptx").resolve()))
    _row(str((originals / "other.pptx").resolve()))
    job_id = jobs._create_job(["/incoming/deck.pptx"], domain="banking")
    _fail(job_id, "/incoming/deck.pptx")

    result = jobs._retry_job(job_id)

    assert result["deregistered"] == 1
    assert _paths() == {str((originals / "other.pptx").resolve())}


def test_retry_job_deregisters_an_external_copy_by_its_hashed_path(tmp_path, monkeypatch):
    import artmind.ingest as ing

    vault = _vault(tmp_path, monkeypatch)
    outside = tmp_path / "incoming" / "Notes.md"
    outside.parent.mkdir()
    outside.write_text("body\n")
    copy = ing.external_copy_path(outside, vault)
    _row(copy.relative_to(vault).as_posix())
    _row("Notes.md")  # a vault note sharing the filename
    job_id = jobs._create_job([str(outside)], domain="banking")
    _fail(job_id, str(outside))

    result = jobs._retry_job(job_id)

    assert result["deregistered"] == 1
    assert _paths() == {"Notes.md"}


def test_retry_job_leaves_other_domains_rows_alone(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    (vault / "Notes.md").write_text("body\n")
    _row("Notes.md", domain="other")
    job_id = jobs._create_job([str(vault / "Notes.md")], domain="banking")
    _fail(job_id, str(vault / "Notes.md"))

    jobs._retry_job(job_id)

    assert _paths() == {"Notes.md"}
