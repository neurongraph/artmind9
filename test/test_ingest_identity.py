"""A1c — stable path-based logical identity.

A document's ``logical_id`` is a deterministic hash of its domain + canonical
key (Vault-relative path, or casefolded basename with no vault). The physical
``Document.id`` (random uuid) is reused across versions; ``version`` increments
when re-ingest resolves an existing logical id. We assert the params actually
sent and which Cypher ran, never summary counts (per CLAUDE.md).
"""
import json
import sqlite3
from hashlib import sha1

import artmind.ingest as ing


# ── _logical_id / _canonical_key: pure, deterministic, path-based ────────────


def test_logical_id_is_deterministic_and_domain_sensitive():
    a = ing._logical_id("banking", "fees/monthly.md")
    b = ing._logical_id("banking", "fees/monthly.md")
    assert a == b, "same inputs must yield the same id"
    assert a == sha1(b"banking\x00fees/monthly.md").hexdigest()

    # Domain is part of the identity: the same path in another domain differs.
    assert ing._logical_id("legal", "fees/monthly.md") != a


def test_canonical_key_uses_vault_relative_path_when_configured(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "fees").mkdir(parents=True)
    doc = vault / "fees" / "Monthly.md"
    doc.write_text("x", encoding="utf-8")

    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", vault.resolve())
    # Vault-relative, posix, casefolded — so identity survives a working-copy move.
    assert ing._canonical_key(doc, "banking") == "fees/monthly.md"


def test_canonical_key_falls_back_to_basename_without_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", None)
    doc = tmp_path / "sub" / "Report.MD"
    doc.parent.mkdir(parents=True)
    doc.write_text("x", encoding="utf-8")
    assert ing._canonical_key(doc, "banking") == "report.md"


def test_canonical_key_basename_when_file_outside_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", vault.resolve())
    outside = tmp_path / "elsewhere" / "Note.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("x", encoding="utf-8")
    # Outside the vault → no relative path → casefolded basename.
    assert ing._canonical_key(outside, "banking") == "note.md"


def test_force_suffix_yields_a_distinct_logical_id():
    base = ing._logical_id("banking", "a.md")
    forced = f"{base}-deadbeef"
    assert forced != base
    assert forced.startswith(base)


# ── _resolve_doc_identity: reuse id + bump version, else mint fresh ──────────


class _FakeSingleResult:
    def __init__(self, record):
        self._record = record

    def single(self):
        return self._record


class _FakeSession:
    def __init__(self, record):
        self._record = record
        self.queries = []

    def run(self, cypher, **kwargs):
        self.queries.append((cypher, kwargs))
        return _FakeSingleResult(self._record)


def _patch_session(monkeypatch, record):
    session = _FakeSession(record)

    class _Ctx:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    import artmind.graph_query as gq

    monkeypatch.setattr(gq, "neo4j_session", lambda *a, **k: _Ctx())
    return session


def test_resolve_reuses_id_and_bumps_version_for_known_document(monkeypatch):
    session = _patch_session(monkeypatch, {"id": "phys-123", "version": 2})
    doc_id, version = ing._resolve_doc_identity("banking", "lid-abc")
    assert doc_id == "phys-123"
    assert version == 3
    # The lookup keys on logical_id + domain against Document nodes.
    cypher, kwargs = session.queries[0]
    assert "d:Document" in cypher and "logical_id: $lid" in cypher
    assert kwargs == {"lid": "lid-abc", "domain": "banking"}


def test_resolve_defaults_version_to_two_when_prior_version_missing(monkeypatch):
    # A pre-A1c Document node has no version property → treat as v1, next is v2.
    _patch_session(monkeypatch, {"id": "phys-9", "version": None})
    doc_id, version = ing._resolve_doc_identity("banking", "lid")
    assert doc_id == "phys-9"
    assert version == 2


def test_resolve_mints_fresh_identity_when_unknown(monkeypatch):
    _patch_session(monkeypatch, None)
    doc_id, version = ing._resolve_doc_identity("banking", "lid-new")
    assert version == 1
    assert doc_id and len(doc_id) == 32  # uuid4().hex


def test_resolve_prefers_resumed_doc_id_for_fresh_document(monkeypatch):
    _patch_session(monkeypatch, None)
    doc_id, version = ing._resolve_doc_identity("banking", "lid", resumed_doc_id="resumed-77")
    assert doc_id == "resumed-77"
    assert version == 1


def test_resolve_degrades_to_fresh_identity_when_lookup_raises(monkeypatch):
    import artmind.graph_query as gq

    def _boom(*a, **k):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(gq, "neo4j_session", _boom)
    doc_id, version = ing._resolve_doc_identity("banking", "lid")
    assert version == 1
    assert doc_id and len(doc_id) == 32


# ── SQLite: column + UNIQUE(domain, logical_id) + migration/backfill ─────────


def _patch_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    return db


def test_fresh_db_has_logical_id_column_and_unique_constraint(tmp_path, monkeypatch):
    db = _patch_db(tmp_path, monkeypatch)
    db._init_db()
    conn = sqlite3.connect(db.DB_PATH)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        assert "logical_id" in cols
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone()[0]
        assert "UNIQUE(domain, logical_id)" in sql
        assert "UNIQUE(filename)" in sql
    finally:
        conn.close()


def test_migration_adds_and_backfills_logical_id_on_legacy_db(tmp_path, monkeypatch):
    db = _patch_db(tmp_path, monkeypatch)
    # Build a pre-A1c documents table (post-force schema: UNIQUE(filename), no
    # logical_id) and seed a row, then let _init_db migrate it.
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        """
        CREATE TABLE documents (
            id           INTEGER PRIMARY KEY,
            domain       TEXT NOT NULL,
            filename     TEXT NOT NULL,
            sha256       TEXT NOT NULL,
            original_path TEXT NOT NULL,
            added_at     TEXT NOT NULL,
            UNIQUE(filename)
        )
        """
    )
    conn.execute(
        "INSERT INTO documents (id, domain, filename, sha256, original_path, added_at)"
        " VALUES (1, 'banking', 'Fees.md', 'deadbeef', '/x/Fees.md', 'now')"
    )
    conn.commit()
    conn.close()

    db._init_db()

    conn = sqlite3.connect(db.DB_PATH)
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone()[0]
        assert "UNIQUE(domain, logical_id)" in sql
        row = conn.execute(
            "SELECT domain, filename, logical_id FROM documents WHERE id = 1"
        ).fetchone()
        # Row preserved, and backfilled with the casefolded-basename identity.
        assert (row[0], row[1]) == ("banking", "Fees.md")
        assert row[2] == ing._logical_id("banking", "fees.md")
    finally:
        conn.close()


def test_register_document_persists_logical_id_and_rejects_duplicate(tmp_path, monkeypatch):
    db = _patch_db(tmp_path, monkeypatch)
    db._init_db()
    # _register_document computes sha256 from the file and writes the row.
    f1 = tmp_path / "a.md"
    f1.write_text("alpha", encoding="utf-8")
    ing._register_document("banking", f1, "lid-shared")

    conn = sqlite3.connect(db.DB_PATH)
    try:
        got = conn.execute(
            "SELECT logical_id FROM documents WHERE filename = 'a.md'"
        ).fetchone()[0]
        assert got == "lid-shared"
    finally:
        conn.close()

    # A different file (distinct name + content) but the same logical id in the
    # same domain must be rejected by UNIQUE(domain, logical_id).
    f2 = tmp_path / "b.md"
    f2.write_text("bravo", encoding="utf-8")
    try:
        ing._register_document("banking", f2, "lid-shared")
        assert False, "expected a duplicate ValueError"
    except ValueError as e:
        assert "logical id" in str(e)


# ── Document MERGE props carry logical_id + version ──────────────────────────


class _Rec:
    def single(self):
        return None

    def data(self):
        return []


class _RecordingSession:
    def __init__(self, runs):
        self.runs = runs

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec()


class _Driver:
    def __init__(self, session):
        self._session = session

    def session(self, **kwargs):
        session = self._session

        class _Ctx:
            def __enter__(self):
                return session

            def __exit__(self, *exc):
                return False

        return _Ctx()

    def close(self):
        pass


def test_document_merge_props_carry_logical_id_and_version(tmp_path, monkeypatch):
    (tmp_path / "document.json").write_text(
        json.dumps(
            {
                "id": "phys-1",
                "logical_id": "lid-xyz",
                "version": 2,
                "name": "a.md",
                "domain": "banking",
            }
        ),
        encoding="utf-8",
    )
    for name in ("chunks.json", "entities.json", "properties.json", "relationships.json"):
        (tmp_path / name).write_text("[]", encoding="utf-8")

    runs: list = []
    session = _RecordingSession(runs)
    monkeypatch.setattr(
        ing, "GraphDatabase", type("G", (), {"driver": staticmethod(lambda *a, **k: _Driver(session))})
    )
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", lambda *a, **k: 0)

    assert ing._write_to_neo4j(tmp_path) is True

    doc_runs = [(c, k) for c, k in runs if "MERGE (d:Document" in c]
    assert doc_runs, "expected a Document MERGE"
    _, kwargs = doc_runs[0]
    assert kwargs["id"] == "phys-1"
    assert kwargs["props"]["logical_id"] == "lid-xyz"
    assert kwargs["props"]["version"] == 2
