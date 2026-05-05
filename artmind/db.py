import sqlite3
from pathlib import Path

from paths import DB_PATH


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    conn.commit()
    conn.close()


def _get_db() -> sqlite3.Connection:
    _init_db()
    return sqlite3.connect(DB_PATH)


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
