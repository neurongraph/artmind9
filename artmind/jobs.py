import uuid
from datetime import datetime
from pathlib import Path

from artmind.db import _get_db


def _create_job(batch_files: list[str], domain: str = "general", force: bool = False) -> str:
    """Create a new ingestion job with per-file rows; return job_id."""
    job_id = str(uuid.uuid4())
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO ingestion_jobs (job_id, status, file_count, queued_at, domain, force)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, "queued", len(batch_files), datetime.now().isoformat(), domain, int(force)),
        )
        cursor.executemany(
            "INSERT INTO ingestion_job_files (job_id, status, filename) VALUES (?, ?, ?)",
            [(job_id, "queued", f) for f in batch_files],
        )
        conn.commit()
        return job_id
    finally:
        conn.close()


def _update_job_status(
    job_id: str,
    status: str | None = None,
    processed_count: int | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error_message: str | None = None,
) -> None:
    """Update parent job status and metadata."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        updates, params = [], []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if processed_count is not None:
            updates.append("processed_count = ?")
            params.append(processed_count)
        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if not updates:
            return
        params.append(job_id)
        cursor.execute(
            f"UPDATE ingestion_jobs SET {', '.join(updates)} WHERE job_id = ?", params
        )
        conn.commit()
    finally:
        conn.close()


def _update_job_file_status(
    job_id: str,
    filename: str,
    status: str | None = None,
    current_step: str | None = None,
    doc_sha256: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error_message: str | None = None,
) -> None:
    """Update per-file status in ingestion_job_files."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        updates, params = [], []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if current_step is not None:
            updates.append("current_step = ?")
            params.append(current_step)
        if doc_sha256 is not None:
            updates.append("doc_sha256 = ?")
            params.append(doc_sha256)
        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if not updates:
            return
        params.extend([job_id, filename])
        cursor.execute(
            f"UPDATE ingestion_job_files SET {', '.join(updates)} WHERE job_id = ? AND filename = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def _get_chunk_progress(doc_sha256: str) -> dict:
    """Summarise kg_chunk_status for a document; shown in job-status during extract_kg."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT entities_status, properties_status, relationships_status"
            " FROM kg_chunk_status WHERE doc_sha256 = ? ORDER BY chunk_seq",
            (doc_sha256,),
        ).fetchall()
        return {
            "total_chunks": len(rows),
            "entities_done": sum(1 for r in rows if r[0] in ("ok", "skipped")),
            "properties_done": sum(1 for r in rows if r[1] in ("ok", "skipped")),
            "relationships_done": sum(1 for r in rows if r[2] in ("ok", "skipped")),
        }
    finally:
        conn.close()


def _get_job_status(job_id: str) -> dict | None:
    """Retrieve job status with per-file progress; return None if not found."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT job_id, status, file_count, processed_count, queued_at, started_at,"
            " completed_at, error_message, domain FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            "SELECT filename, status, current_step, doc_sha256"
            " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        files = []
        for r in cursor.fetchall():
            entry = {"filename": r[0], "status": r[1], "current_step": r[2]}
            if r[3] and r[2] == "extract_kg":
                entry["chunk_progress"] = _get_chunk_progress(r[3])
            files.append(entry)
        return {
            "job_id": row[0],
            "status": row[1],
            "file_count": row[2],
            "processed_count": row[3],
            "queued_at": row[4],
            "started_at": row[5],
            "completed_at": row[6],
            "error_message": row[7],
            "domain": row[8] or "general",
            "files": files,
        }
    finally:
        conn.close()


def _get_job_results(job_id: str) -> dict | None:
    """Retrieve detailed per-file results; return None if not found."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT status, file_count, error_message FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        status, file_count, error_message = row
        cursor.execute(
            "SELECT filename, status, error_message, started_at, completed_at"
            " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        files = [
            {
                "filename": r[0],
                "status": r[1],
                "error_message": r[2],
                "started_at": r[3],
                "completed_at": r[4],
            }
            for r in cursor.fetchall()
        ]
        result = {"job_id": job_id, "status": status, "file_count": file_count, "files": files}
        if error_message:
            result["error_message"] = error_message
        return result
    finally:
        conn.close()


def _retry_job(job_id: str, include_skipped: bool = False) -> dict:
    """Reset failed (and optionally skipped) files for re-processing.

    Removes those files from the document registry and resets both the file rows
    and the parent job to 'queued' so the worker picks them up again.
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT domain FROM ingestion_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Job '{job_id}' not found")
        domain = row[0] or "general"

        statuses = ("failed", "skipped") if include_skipped else ("failed",)
        placeholders = ",".join("?" * len(statuses))
        file_rows = conn.execute(
            f"SELECT filename FROM ingestion_job_files"
            f" WHERE job_id = ? AND status IN ({placeholders})",
            (job_id, *statuses),
        ).fetchall()
        filenames = [r[0] for r in file_rows]

        if not filenames:
            return {"job_id": job_id, "domain": domain, "retried": 0, "deregistered": 0, "files": []}

        # Remove from document registry using bare filename only
        deregistered = 0
        for filename in filenames:
            bare = Path(filename).name
            cursor = conn.execute(
                "DELETE FROM documents WHERE domain = ? AND UPPER(filename) = ?",
                (domain, bare.upper()),
            )
            deregistered += cursor.rowcount

        # Reset file rows to queued
        fn_placeholders = ",".join("?" * len(filenames))
        conn.execute(
            f"UPDATE ingestion_job_files"
            f" SET status='queued', current_step=NULL, doc_sha256=NULL,"
            f"     started_at=NULL, completed_at=NULL, error_message=NULL"
            f" WHERE job_id = ? AND filename IN ({fn_placeholders})",
            (job_id, *filenames),
        )

        # processed_count = files that are already done and won't be re-queued
        remaining = conn.execute(
            "SELECT COUNT(*) FROM ingestion_job_files"
            " WHERE job_id = ? AND status IN ('completed', 'skipped')",
            (job_id,),
        ).fetchone()[0]

        conn.execute(
            "UPDATE ingestion_jobs SET status='queued', processed_count=?,"
            " started_at=NULL, completed_at=NULL, error_message=NULL WHERE job_id = ?",
            (remaining, job_id),
        )
        conn.commit()

        return {
            "job_id": job_id,
            "domain": domain,
            "retried": len(filenames),
            "deregistered": deregistered,
            "files": filenames,
        }
    finally:
        conn.close()


def _list_jobs(status_filter: str | None = None) -> list[dict]:
    """List recent jobs; optionally filter by status."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if status_filter:
            cursor.execute(
                "SELECT job_id, status, file_count, processed_count, queued_at FROM ingestion_jobs"
                " WHERE status = ? ORDER BY queued_at DESC LIMIT 50",
                (status_filter,),
            )
        else:
            cursor.execute(
                "SELECT job_id, status, file_count, processed_count, queued_at FROM ingestion_jobs"
                " ORDER BY queued_at DESC LIMIT 50"
            )
        return [
            {
                "job_id": row[0],
                "status": row[1],
                "file_count": row[2],
                "processed_count": row[3],
                "queued_at": row[4],
            }
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()
