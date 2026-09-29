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


def test_reindex_reads_the_content_hash_from_document_json_for_a_minimal_note(vault, tmp_path, monkeypatch):
    """Spec 2026-09-26 R6: a note carries identity only; the hash the last
    extraction recorded is in its staging folder's document.json. A note
    with neither falls back to hashing its body."""
    import json

    import artmind.ingest as ing
    from artmind.document_identity import compute_content_sha256

    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
    (vault / "notes").mkdir()
    (vault / "notes" / "a.md").write_text("---\n_artmind_id: id-1\n_domain: general\n---\n\nBody A\n")
    (vault / "notes" / "b.md").write_text("---\n_artmind_id: id-2\n_domain: general\n---\n\nBody B\n")
    folder = tmp_path / "kg" / "general" / "a"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "id-1", "version": 2, "content_sha256": "staged-hash"}))

    reindex_mod.reindex()

    conn = sqlite3.connect(vault.parent / "registry.db")
    try:
        rows = dict(conn.execute("SELECT artmind_id, content_sha256 FROM documents"))
    finally:
        conn.close()
    assert rows == {"id-1": "staged-hash", "id-2": compute_content_sha256("Body B\n")}


def test_reindex_prefers_document_json_over_a_legacy_frontmatter_hash(vault, tmp_path, monkeypatch):
    import json

    import artmind.ingest as ing

    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
    _write(vault / "notes" / "a.md", "id-1", "general", "Body A\n")  # legacy _content_sha256: abc
    _write(vault / "notes" / "b.md", "id-2", "general", "Body B\n")  # legacy hash, nothing staged
    folder = tmp_path / "kg" / "general" / "a"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "id-1", "content_sha256": "staged-hash"}))
    other = tmp_path / "kg" / "general" / "b"      # a folder that names another id is never taken
    other.mkdir(parents=True)
    (other / "document.json").write_text(json.dumps({"id": "someone-else", "content_sha256": "wrong"}))

    reindex_mod.reindex()

    conn = sqlite3.connect(vault.parent / "registry.db")
    try:
        rows = dict(conn.execute("SELECT artmind_id, content_sha256 FROM documents"))
    finally:
        conn.close()
    assert rows == {"id-1": "staged-hash", "id-2": "abc"}


def _registry(vault):
    conn = sqlite3.connect(vault.parent / "registry.db")
    try:
        return {r[0]: (r[1], r[2]) for r in conn.execute("SELECT artmind_id, domain, content_sha256 FROM documents")}
    finally:
        conn.close()


def test_reindex_hashes_the_body_of_a_moved_note_and_never_scans_the_domain(vault, tmp_path, monkeypatch):
    """A note moved since its extraction has its staging folder under the OLD
    stem. Reindex looks up by stem only (a scan per note would read a whole
    domain thousands of times), so it falls to hashing the body -- which is
    what an ingest of the moved note records too."""
    import json

    import artmind.ingest as ing
    from artmind.document_identity import compute_content_sha256

    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
    (vault / "n").mkdir()
    (vault / "n" / "moved.md").write_text("---\n_artmind_id: id-1\n_domain: general\n---\n\nBody\n")
    old = tmp_path / "kg" / "general" / "old"
    old.mkdir(parents=True)
    (old / "document.json").write_text(json.dumps({"id": "id-1", "content_sha256": "staged-under-old-stem"}))
    seen = []
    real = ing.staged_document

    def spy(domain, stems, artmind_id, **kw):
        seen.append((domain, list(stems), artmind_id, kw.get("search", False)))
        return real(domain, stems, artmind_id, **kw)

    monkeypatch.setattr(ing, "staged_document", spy)

    reindex_mod.reindex()

    assert _registry(vault) == {"id-1": ("general", compute_content_sha256("Body\n"))}
    assert seen == [("general", ["moved"], "id-1", False)]


@pytest.mark.parametrize("domain", ["5", "[a]", "null"])
def test_reindex_survives_a_non_string_domain(vault, tmp_path, monkeypatch, domain):
    """A hand-edited `_domain` that is not a string must not crash the whole
    rebuild: the note is still registered, hashed from its body, with no
    domain."""
    import artmind.ingest as ing
    from artmind.document_identity import compute_content_sha256

    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
    (vault / "a.md").write_text(f"---\n_artmind_id: i\n_domain: {domain}\n---\n\nB\n")
    (vault / "b.md").write_text("---\n_artmind_id: j\n_domain: g\n---\n\nB\n")

    reindex_mod.reindex()

    assert _registry(vault) == {"i": ("", compute_content_sha256("B\n")), "j": ("g", compute_content_sha256("B\n"))}


def test_reindex_does_not_follow_a_traversing_domain_out_of_the_staging_dir(vault, tmp_path, monkeypatch):
    import json

    import artmind.ingest as ing
    from artmind.document_identity import compute_content_sha256

    (tmp_path / "deep" / "kg").mkdir(parents=True)
    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "deep" / "kg")
    outside = tmp_path / "x" / "a"           # kg / ../../x / a  -- outside the staging dir
    outside.mkdir(parents=True)
    (outside / "document.json").write_text(json.dumps({"id": "i", "content_sha256": "ESCAPED"}))
    (vault / "a.md").write_text("---\n_artmind_id: i\n_domain: ../../x\n---\n\nB\n")

    reindex_mod.reindex()

    assert _registry(vault)["i"][1] == compute_content_sha256("B\n")
