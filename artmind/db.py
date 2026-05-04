import sqlite3

from paths import DB_PATH


def _init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY,
            domain       TEXT NOT NULL,
            filename     TEXT NOT NULL,
            sha256       TEXT NOT NULL,
            original_path TEXT NOT NULL,
            added_at     TEXT NOT NULL,
            UNIQUE(filename),
            UNIQUE(sha256)
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
            domain           TEXT DEFAULT 'general'
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
    conn.commit()
    conn.close()


def _get_db() -> sqlite3.Connection:
    _init_db()
    return sqlite3.connect(DB_PATH)
