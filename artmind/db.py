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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY,
            domain       TEXT NOT NULL,
            filename     TEXT NOT NULL,
            sha256       TEXT NOT NULL,
            original_path TEXT NOT NULL,
            added_at     TEXT NOT NULL,
            UNIQUE(filename)
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
    # Migrations for columns added after initial schema deployment
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(ingestion_job_files)")}
    if "doc_sha256" not in existing:
        cursor.execute("ALTER TABLE ingestion_job_files ADD COLUMN doc_sha256 TEXT")

    existing = {row[1] for row in cursor.execute("PRAGMA table_info(ingestion_jobs)")}
    if "force" not in existing:
        cursor.execute("ALTER TABLE ingestion_jobs ADD COLUMN force INTEGER DEFAULT 0")
    if "stage_only" not in existing:
        cursor.execute("ALTER TABLE ingestion_jobs ADD COLUMN stage_only INTEGER DEFAULT 0")

    # Drop the legacy UNIQUE(sha256) constraint on documents so the same content
    # can be force-ingested as an independent document (see --force).
    documents_sql = cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if documents_sql and "UNIQUE(sha256)" in documents_sql[0]:
        cursor.execute("ALTER TABLE documents RENAME TO documents_pre_force_migration")
        cursor.execute("""
            CREATE TABLE documents (
                id           INTEGER PRIMARY KEY,
                domain       TEXT NOT NULL,
                filename     TEXT NOT NULL,
                sha256       TEXT NOT NULL,
                original_path TEXT NOT NULL,
                added_at     TEXT NOT NULL,
                UNIQUE(filename)
            )
        """)
        cursor.execute(
            "INSERT INTO documents (id, domain, filename, sha256, original_path, added_at)"
            " SELECT id, domain, filename, sha256, original_path, added_at"
            " FROM documents_pre_force_migration"
        )
        cursor.execute("DROP TABLE documents_pre_force_migration")

    conn.commit()
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
            conditions.append("s.domain = ?")
            params.append(domain)
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
