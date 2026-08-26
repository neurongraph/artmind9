"""Tests for artmind.reindex.reindex: rebuilding the registry from vault
frontmatter (docs/document-identity.md; Phase 5 "D")."""

import sqlite3

import pytest

import artmind.reindex as reindex_mod


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    monkeypatch.setattr(reindex_mod, "ARTMIND_VAULT_DIR", v)
    import artmind.document_identity as di

    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", v)

    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    return v


def _write(path, artmind_id, domain, body, extra=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = f"_artmind_id: {artmind_id}\n_domain: {domain}\n_content_sha256: abc\n{extra}" if artmind_id else extra
    path.write_text(f"---\n{fm}---\n\n{body}", encoding="utf-8")


def test_reindex_registers_documents_with_artmind_id(vault):
    _write(vault / "notes" / "a.md", "id-1", "general", "Body A\n")
    _write(vault / "notes" / "b.md", "id-2", "banking", "Body B\n")

    result = reindex_mod.reindex()

    assert result["registered"] == 2
    conn = sqlite3.connect(vault.parent / "registry.db")
    try:
        rows = {r[0]: r[1] for r in conn.execute("SELECT artmind_id, domain FROM documents")}
    finally:
        conn.close()
    assert rows == {"id-1": "general", "id-2": "banking"}


def test_reindex_reports_markdown_with_no_artmind_id(vault):
    _write(vault / "notes" / "a.md", "id-1", "general", "Body A\n")
    (vault / "scratch.md").write_text("# Just a plain file\n", encoding="utf-8")

    result = reindex_mod.reindex()

    assert result["registered"] == 1
    assert str(vault / "scratch.md") in result["skipped_no_id"]


def test_reindex_scans_derived_subdir_too(vault):
    _write(vault / "_derived" / "banking" / "deck.md", "id-deck", "banking", "Body\n")
    result = reindex_mod.reindex()
    assert result["registered"] == 1


def test_reindex_wipes_stale_id_bearing_rows_not_present_in_vault(vault):
    conn = sqlite3.connect(vault.parent / "registry.db")
    import artmind.db as db

    db._init_db()
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "INSERT INTO documents (artmind_id, domain, path, content_sha256, last_ingested_at)"
        " VALUES ('stale-id', 'general', 'gone.md', 'x', 'now')"
    )
    conn.commit()
    conn.close()

    result = reindex_mod.reindex()
    assert result["registered"] == 0

    conn = sqlite3.connect(db.DB_PATH)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM documents WHERE artmind_id = 'stale-id'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_reindex_leaves_path_only_rows_untouched(vault):
    """Binaries still on the pre-Phase-5 logical_id path, or csv/xlsx --
    reindex has nothing to rebuild these from, so it must not delete them."""
    import artmind.db as db

    db._init_db()
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "INSERT INTO documents (artmind_id, domain, path, content_sha256, last_ingested_at)"
        " VALUES (NULL, 'banking', '/data/originals/report.pdf', 'x', 'now')"
    )
    conn.commit()
    conn.close()

    reindex_mod.reindex()

    conn = sqlite3.connect(db.DB_PATH)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM documents WHERE path = '/data/originals/report.pdf'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_reindex_raises_without_a_configured_vault(monkeypatch):
    monkeypatch.setattr(reindex_mod, "ARTMIND_VAULT_DIR", None)
    with pytest.raises(RuntimeError):
        reindex_mod.reindex()
