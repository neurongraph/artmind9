import sqlite3
from pathlib import Path

from paths import DB_PATH


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # WAL lets concurrent readers/writers coexist without "database is locked":
    # required now that KG extraction fans chunks out across threads, each of
    # which writes its own kg_chunk_status row. Persists on the db file, so
    # setting it here (called on every _get_db) is idempotent and cheap.
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()
    # Phase 2 (docs/document-identity.md): identity is `artmind_id` (a uuid7
    # written into vault frontmatter), not a path/filename derivative, so this
    # is a path <-> id CACHE, not identity storage -- `docs reindex` (Phase 5)
    # rebuilds it from the vault. `artmind_id` is nullable: a source that
    # cannot carry frontmatter (a binary original, a structured csv/xlsx) has
    # no id at all and stays path-keyed, per the spec's "sources that cannot
    # carry frontmatter" table. SQLite's UNIQUE treats each NULL as distinct,
    # so any number of path-only rows coexist with the id-bearing ones.
    # `filename`/`sha256` are informational display/lookup convenience, not
    # identity -- no UNIQUE on either; a global sha256 duplicate guard is
    # exactly what this redesign removes (a copied template with an unedited
    # body is a legitimate new document).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY,
            artmind_id   TEXT,
            domain       TEXT NOT NULL,
            filename     TEXT NOT NULL,
            path         TEXT NOT NULL,
            sha256       TEXT,
            added_at     TEXT NOT NULL,
            UNIQUE(artmind_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_jobs (
            job_id           TEXT PRIMARY KEY,
            status           TEXT NOT NULL,
            file_count       INTEGER NOT NULL,
            processed_count  INTEGER DEFAULT 0,
            queued_at        TEXT NOT NULL,
            started_at       TEXT,
            completed_at     TEXT,
            error_message    TEXT,
            results_json     TEXT,
            domain           TEXT DEFAULT 'general',
            force            INTEGER DEFAULT 0,
            stage_only       INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_job_files (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id           TEXT NOT NULL REFERENCES ingestion_jobs(job_id),
            status           TEXT NOT NULL,
            filename         TEXT NOT NULL,
            current_step     TEXT,
            doc_sha256       TEXT,
            started_at       TEXT,
            completed_at     TEXT,
            error_message    TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kg_chunk_status (
            doc_sha256           TEXT NOT NULL,
            doc_id               TEXT NOT NULL,
            chunk_seq            INTEGER NOT NULL,
            entities_status      TEXT NOT NULL DEFAULT 'pending',
            properties_status    TEXT NOT NULL DEFAULT 'pending',
            relationships_status TEXT NOT NULL DEFAULT 'pending',
            updated_at           TEXT NOT NULL,
            PRIMARY KEY (doc_sha256, chunk_seq)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_runs (
            run_id           TEXT PRIMARY KEY,
            name             TEXT,
            domain_hint      TEXT,
            source_path      TEXT NOT NULL,
            backend          TEXT NOT NULL DEFAULT 'claude-sdk',
            status           TEXT NOT NULL DEFAULT 'queued',
            question_count   INTEGER NOT NULL,
            processed_count  INTEGER DEFAULT 0,
            queued_at        TEXT NOT NULL,
            started_at       TEXT,
            completed_at     TEXT,
            error_message    TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_questions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        TEXT NOT NULL REFERENCES benchmark_runs(run_id),
            seq           INTEGER NOT NULL,
            qid           TEXT,
            title         TEXT,
            question      TEXT NOT NULL,
            eval_comment  TEXT,
            status        TEXT NOT NULL DEFAULT 'queued',
            answer_text   TEXT,
            trace_json    TEXT,
            turns         INTEGER,
            duration_s    REAL,
            cost_usd      REAL,
            started_at    TEXT,
            completed_at  TEXT,
            error_message TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS update_sessions (
            session_id    TEXT PRIMARY KEY,
            domain        TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'draft',
            created_by    TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS update_drafts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL REFERENCES update_sessions(session_id),
            raw_text        TEXT NOT NULL,
            input_hint      TEXT,
            extraction_json TEXT,
            candidates_json TEXT,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS datasources (
            name        TEXT PRIMARY KEY,
            type        TEXT NOT NULL,              -- 'duckdb' in v1
            path_or_dsn TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tables (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            datasource             TEXT NOT NULL REFERENCES datasources(name),
            table_name             TEXT NOT NULL,
            domain                 TEXT NOT NULL,
            source_file            TEXT,
            sheet                  TEXT,
            parquet_path           TEXT NOT NULL,
            version                INTEGER NOT NULL DEFAULT 1,
            row_count              INTEGER,
            refresh_mode           TEXT NOT NULL DEFAULT 'replace',   -- 'replace' | 'temporal'
            business_key           TEXT,             -- comma-joined column names (temporal only)
            effective_date_column  TEXT,
            grain                  TEXT NOT NULL DEFAULT 'instance',  -- 'instance' | 'lookup' | 'normative'
            grain_confirmed        INTEGER NOT NULL DEFAULT 0,        -- 0 = proposed/default, 1 = operator-confirmed
            grain_status           TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'ok' | 'failed' -- did the LLM attempt+succeed since the last profile change
            bridge_status          TEXT NOT NULL DEFAULT 'pending',
            mapping_status         TEXT NOT NULL DEFAULT 'pending',
            ingested_at            TEXT NOT NULL,
            sha256                 TEXT,
            UNIQUE(datasource, domain, table_name)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS columns (
            table_id     INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            dtype        TEXT NOT NULL,
            profile_json TEXT,
            PRIMARY KEY (table_id, name)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS column_mappings (
            table_id     INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
            column       TEXT NOT NULL,
            entity_class TEXT NOT NULL,
            confirmed    INTEGER NOT NULL DEFAULT 0,   -- 0 = proposed, 1 = confirmed
            confidence   REAL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (table_id, column, entity_class)
        )
    """)
    # Which columns' *values* seed graph retrieval during fusion (e.g.
    # vulnerable_customers.vulnerability_driver), as opposed to identifiers that
    # mean nothing to the graph (customer_id). Deliberately NOT a column on
    # `columns`: replace_columns() DELETEs and re-INSERTs that table on every
    # ingest/refresh, which would wipe an operator's confirmed role. Durable
    # semantic annotation belongs alongside column_mappings, same
    # proposed→confirmed shape. `bridge_role` is an open vocabulary; 'term' is
    # the only value the fusion path reads today.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS column_roles (
            table_id    INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
            column      TEXT NOT NULL,
            bridge_role TEXT NOT NULL,                -- 'term' = values seed graph retrieval
            confirmed   INTEGER NOT NULL DEFAULT 0,   -- 0 = proposed, 1 = confirmed
            confidence  REAL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (table_id, column)
        )
    """)
    # Migrations for columns added after initial schema deployment
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(ingestion_job_files)")}
    if "doc_sha256" not in existing:
        cursor.execute("ALTER TABLE ingestion_job_files ADD COLUMN doc_sha256 TEXT")

    existing = {row[1] for row in cursor.execute("PRAGMA table_info(ingestion_jobs)")}
    if "force" not in existing:
        cursor.execute("ALTER TABLE ingestion_jobs ADD COLUMN force INTEGER DEFAULT 0")
    if "stage_only" not in existing:
        cursor.execute("ALTER TABLE ingestion_jobs ADD COLUMN stage_only INTEGER DEFAULT 0")

    # Phase 2 dropped path/filename-derived identity (logical_id) for
    # frontmatter-carried `artmind_id` -- no backward compatibility, no
    # migration: a `documents` table from before this change is simply
    # incompatible, so it is dropped and recreated empty. Re-ingesting the
    # vault repopulates it under the new resolution table (every row reads as
    # `new`, or `adopt` if the frontmatter already carries an id from a prior
    # install). CREATE TABLE IF NOT EXISTS above then makes the fresh one.
    documents_sql = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if documents_sql and "artmind_id" not in documents_sql[0]:
        cursor.execute("DROP TABLE documents")
        cursor.execute("""
            CREATE TABLE documents (
                id           INTEGER PRIMARY KEY,
                artmind_id   TEXT,
                domain       TEXT NOT NULL,
                filename     TEXT NOT NULL,
                path         TEXT NOT NULL,
                sha256       TEXT,
                added_at     TEXT NOT NULL,
                UNIQUE(artmind_id)
            )
        """)

    # Migrate the `tables` registry's UNIQUE constraint from
    # (datasource, table_name) to (datasource, domain, table_name) so two
    # domains can register a same-named table without colliding. The
    # CREATE TABLE IF NOT EXISTS above is a no-op on any DB that already has
    # a `tables` table from before this change (i.e. any real install that
    # ingested structured data before this branch), so without this
    # migration, register_table's per-domain existence check finds no
    # conflict but the INSERT still hits the old 2-column UNIQUE index and
    # raises sqlite3.IntegrityError. No FK-enforcement concern here: this
    # codebase never turns on `PRAGMA foreign_keys`, so `columns` and
    # `column_mappings` rows (which reference tables(id)) are unaffected by
    # the rename/recreate as long as `id` values are preserved verbatim,
    # which the copy below does.
    tables_sql = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tables'"
    ).fetchone()
    if tables_sql and "UNIQUE(datasource, domain, table_name)" not in tables_sql[0]:
        cursor.execute('ALTER TABLE "tables" RENAME TO tables_pre_domain_unique_migration')
        cursor.execute("""
            CREATE TABLE "tables" (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                datasource             TEXT NOT NULL REFERENCES datasources(name),
                table_name             TEXT NOT NULL,
                domain                 TEXT NOT NULL,
                source_file            TEXT,
                sheet                  TEXT,
                parquet_path           TEXT NOT NULL,
                version                INTEGER NOT NULL DEFAULT 1,
                row_count              INTEGER,
                refresh_mode           TEXT NOT NULL DEFAULT 'replace',
                business_key           TEXT,
                effective_date_column  TEXT,
                ingested_at            TEXT NOT NULL,
                sha256                 TEXT,
                UNIQUE(datasource, domain, table_name)
            )
        """)
        cursor.execute(
            'INSERT INTO "tables" (id, datasource, table_name, domain, source_file, sheet,'
            " parquet_path, version, row_count, refresh_mode, business_key,"
            " effective_date_column, ingested_at, sha256)"
            " SELECT id, datasource, table_name, domain, source_file, sheet,"
            " parquet_path, version, row_count, refresh_mode, business_key,"
            " effective_date_column, ingested_at, sha256"
            " FROM tables_pre_domain_unique_migration"
        )
        cursor.execute("DROP TABLE tables_pre_domain_unique_migration")

    # `grain` is additive with a default, so a plain ADD COLUMN suffices — no need
    # for the rename/recreate dance above, which exists only to change a UNIQUE
    # constraint. Runs after that block deliberately: the recreate path rebuilds
    # `tables` from the pre-grain definition, so this has to be the last word.
    existing = {row[1] for row in cursor.execute('PRAGMA table_info("tables")')}
    if "grain" not in existing:
        cursor.execute(
            'ALTER TABLE "tables" ADD COLUMN grain TEXT NOT NULL DEFAULT \'instance\''
        )
    if "grain_confirmed" not in existing:
        cursor.execute(
            'ALTER TABLE "tables" ADD COLUMN grain_confirmed INTEGER NOT NULL DEFAULT 0'
        )
    # Same additive-with-default shape as grain/grain_confirmed above --
    # per-step run status (docs/superpowers/specs/2026-07-31-structured-
    # semantic-classification-pipeline-design.md §4), not a persisted error
    # message: a step's failure is only ever visible via loguru, matching the
    # rest of the codebase's best-effort hooks.
    for step in ("grain", "bridge", "mapping"):
        col = f"{step}_status"
        if col not in existing:
            cursor.execute(
                f'ALTER TABLE "tables" ADD COLUMN {col} TEXT NOT NULL DEFAULT \'pending\''
            )

    conn.commit()
    conn.close()


def _registry_row_by_artmind_id(artmind_id: str) -> dict | None:
    """The registry's path <-> id cache, read by id -- the lookup the
    resolution table's `reingest`/`move`/`refuse` rows need."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, artmind_id, domain, filename, path, sha256, added_at"
            " FROM documents WHERE artmind_id = ?",
            (artmind_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "artmind_id": row[1], "domain": row[2],
            "filename": row[3], "path": row[4], "sha256": row[5], "added_at": row[6],
        }
    finally:
        conn.close()


def _registry_row_by_path(path: str) -> dict | None:
    """The registry's path <-> id cache, read by path -- the lookup `heal`
    needs (frontmatter lost its id; the registry still has it)."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, artmind_id, domain, filename, path, sha256, added_at"
            " FROM documents WHERE path = ?",
            (path,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "artmind_id": row[1], "domain": row[2],
            "filename": row[3], "path": row[4], "sha256": row[5], "added_at": row[6],
        }
    finally:
        conn.close()


def _registry_upsert(
    artmind_id: str | None, domain: str, filename: str, path: str, sha256: str | None
) -> None:
    """Write (or move/refresh) a path <-> id cache row.

    `artmind_id=None` is the path-only case (a source that cannot carry
    frontmatter). Keyed on `artmind_id` when present -- an `adopt`/`move`
    verdict repoints an *existing* id's path rather than minting a new row,
    which is exactly the UPSERT this needs (no separate branch for "new row"
    vs "row already exists under this id").
    """
    conn = _get_db()
    now = __import__("datetime").datetime.now().isoformat()
    try:
        if artmind_id is not None:
            conn.execute(
                """
                INSERT INTO documents (artmind_id, domain, filename, path, sha256, added_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(artmind_id) DO UPDATE SET
                    domain = excluded.domain,
                    filename = excluded.filename,
                    path = excluded.path,
                    sha256 = excluded.sha256
                """,
                (artmind_id, domain, filename, path, sha256, now),
            )
        else:
            conn.execute(
                "INSERT INTO documents (artmind_id, domain, filename, path, sha256, added_at)"
                " VALUES (NULL, ?, ?, ?, ?, ?)",
                (domain, filename, path, sha256, now),
            )
        conn.commit()
    finally:
        conn.close()


def _get_db() -> sqlite3.Connection:
    _init_db()
    # 30s busy-timeout (up from sqlite's 5s default) so a chunk's short status
    # UPDATE waits out a concurrent writer under thread fan-out instead of
    # raising OperationalError("database is locked").
    return sqlite3.connect(DB_PATH, timeout=30.0)


def _create_update_session(session_id: str, domain: str, created_by: str) -> None:
    conn = _get_db()
    now = __import__("datetime").datetime.now().isoformat()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO update_sessions"
            " (session_id, domain, status, created_by, created_at, updated_at)"
            " VALUES (?, ?, 'draft', ?, ?, ?)",
            (session_id, domain, created_by, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _get_update_session(session_id: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT session_id, domain, status, created_by, created_at, updated_at"
            " FROM update_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "session_id": row[0], "domain": row[1], "status": row[2],
            "created_by": row[3], "created_at": row[4], "updated_at": row[5],
        }
    finally:
        conn.close()


def _update_session_status(session_id: str, status: str) -> None:
    conn = _get_db()
    now = __import__("datetime").datetime.now().isoformat()
    try:
        conn.execute(
            "UPDATE update_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, now, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def _create_update_draft(
    session_id: str,
    raw_text: str,
    input_hint: str | None,
    extraction_json: str,
    candidates_json: str,
) -> int:
    conn = _get_db()
    now = __import__("datetime").datetime.now().isoformat()
    try:
        cursor = conn.execute(
            "INSERT INTO update_drafts"
            " (session_id, raw_text, input_hint, extraction_json, candidates_json, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (session_id, raw_text, input_hint, extraction_json, candidates_json, now),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _get_latest_pending_draft(session_id: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT d.id, d.session_id, d.raw_text, d.input_hint,"
            "       d.extraction_json, d.candidates_json, d.status, d.created_at,"
            "       s.domain, s.created_by"
            " FROM update_drafts d"
            " JOIN update_sessions s ON d.session_id = s.session_id"
            " WHERE d.session_id = ? AND d.status = 'pending'"
            " ORDER BY d.id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "session_id": row[1], "raw_text": row[2],
            "input_hint": row[3], "extraction_json": row[4],
            "candidates_json": row[5], "status": row[6], "created_at": row[7],
            "domain": row[8], "created_by": row[9],
        }
    finally:
        conn.close()


def _update_draft_status(draft_id: int, status: str) -> None:
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE update_drafts SET status = ? WHERE id = ?",
            (status, draft_id),
        )
        conn.commit()
    finally:
        conn.close()


def _list_update_sessions(
    domain: str | None, user: str | None, limit: int
) -> list[dict]:
    conn = _get_db()
    try:
        conditions = []
        params: list = []
        if domain:
            # Hierarchical rollup (a parent filter includes descendants), the
            # convention every domain-scoped query path follows. Prefix-compared
            # via substr rather than LIKE because '_' is a LIKE wildcard and
            # real domain names contain underscores (banking_organization.*),
            # which would let 'a_b.%' match 'axb.child'.
            prefix = domain + "."
            conditions.append("(s.domain = ? OR substr(s.domain, 1, ?) = ?)")
            params.extend([domain, len(prefix), prefix])
        if user:
            conditions.append("s.created_by = ?")
            params.append(user)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"""
            SELECT s.session_id, s.domain, s.created_by, s.created_at, s.status,
                   COUNT(d.id) AS input_count,
                   MIN(d.raw_text) AS first_raw_text
            FROM update_sessions s
            LEFT JOIN update_drafts d ON s.session_id = d.session_id
            {where}
            GROUP BY s.session_id
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [
            {
                "session_id": r[0], "domain": r[1], "created_by": r[2],
                "created_at": r[3], "status": r[4], "input_count": r[5],
                "excerpt": (r[6] or "")[:80],
            }
            for r in rows
        ]
    finally:
        conn.close()
