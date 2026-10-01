import uuid
from datetime import datetime
from pathlib import Path

from artmind.db import _get_db


def worker_alive() -> bool:
    """Whether an ingest worker holds its pid-file lock right now.

    Inside a vault, the same check `vault sync` refuses on
    (`vault_sync.worker_running`: the vault's own pid file and both
    process-wide fallbacks), so "stalled" and "sync would refuse" can never
    disagree. Read through `paths` at call time, so a test that repoints them
    is honoured.
    """
    import paths
    from artmind.worker_pid import live_pid

    if paths.ARTMIND_VAULT_DIR:
        from artmind.vault_sync import worker_running

        return worker_running(Path(paths.ARTMIND_VAULT_DIR))
    return any(
        live_pid(pid_file) is not None
        for pid_file in (paths.WORKER_PID_FILE, paths.DATA_DIR / "worker.pid")
    )


def _stalled(status: str) -> bool:
    """A job the worker marked `processing` whose worker is gone: it died (a
    crash, a reboot, a killed terminal) mid-job, and nothing will finish it.
    Only `processing`, never `queued` -- a job just submitted is queued for
    the moment before its worker takes the lock, and must not read as stalled.
    """
    return status == "processing" and not worker_alive()


def _create_job(
    batch_files: list[str], domain: str = "general", force: bool = False, stage_only: bool = False
) -> str:
    """Create a new ingestion job with per-file rows; return job_id."""
    job_id = str(uuid.uuid4())
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO ingestion_jobs (job_id, status, file_count, queued_at, domain, force, stage_only)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                "queued",
                len(batch_files),
                datetime.now().isoformat(),
                domain,
                int(force),
                int(stage_only),
            ),
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
    kg_counts: dict | None = None,
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
        if kg_counts is not None:
            updates.extend(["entity_count = ?", "relationship_count = ?"])
            params.extend([kg_counts.get("entities"), kg_counts.get("relationships")])
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


def _fetch_active_jobs() -> list[dict]:
    """Return queued/processing jobs with their file rows."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT job_id, status, file_count, processed_count,"
            " queued_at, started_at, domain"
            " FROM ingestion_jobs WHERE status IN ('queued','processing')"
            " ORDER BY queued_at DESC LIMIT 20"
        ).fetchall()
        result = []
        for row in rows:
            job_id = row[0]
            files = conn.execute(
                "SELECT filename, status, current_step, doc_sha256, entity_count, relationship_count"
                " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
            result.append({
                "job_id": job_id,
                "status": row[1],
                "file_count": row[2],
                "processed_count": row[3],
                "queued_at": row[4],
                "started_at": row[5],
                "domain": row[6] or "general",
                "stalled": _stalled(row[1]),
                "files": [
                    {
                        "filename": f[0], "status": f[1], "current_step": f[2], "doc_sha256": f[3],
                        "entities": f[4], "relationships": f[5],
                    }
                    for f in files
                ],
            })
        return result
    finally:
        conn.close()


def _fetch_completed_jobs(limit: int = 100) -> list[dict]:
    """Return completed/failed jobs with per-file summary rows."""
    conn = _get_db()
    try:
        jobs = conn.execute(
            "SELECT job_id, status, file_count, processed_count,"
            " queued_at, started_at, completed_at, domain, error_message"
            " FROM ingestion_jobs WHERE status IN ('completed','failed')"
            " ORDER BY completed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for row in jobs:
            job_id = row[0]
            files = conn.execute(
                "SELECT filename, status, error_message, started_at, completed_at,"
                " entity_count, relationship_count"
                " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
            result.append({
                "job_id": job_id,
                "status": row[1],
                "file_count": row[2],
                "processed_count": row[3],
                "queued_at": row[4],
                "started_at": row[5],
                "completed_at": row[6],
                "domain": row[7] or "general",
                "error_message": row[8],
                "files": [
                    {
                        "filename": f[0],
                        "status": f[1],
                        "error_message": f[2],
                        "started_at": f[3],
                        "completed_at": f[4],
                        "entities": f[5],
                        "relationships": f[6],
                    }
                    for f in files
                ],
            })
        return result
    finally:
        conn.close()


def _fetch_chunks(doc_sha256: str) -> list[dict]:
    """Return per-chunk KG extraction status for a document."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT chunk_seq, entities_status, properties_status, relationships_status"
            " FROM kg_chunk_status WHERE doc_sha256 = ? ORDER BY chunk_seq",
            (doc_sha256,),
        ).fetchall()
        return [{"seq": r[0], "e": r[1], "p": r[2], "r": r[3]} for r in rows]
    finally:
        conn.close()


def _job_file_sha256(job_id: str, document: str) -> str | None:
    """The doc_sha256 the worker recorded for `document` (a basename, as the
    progress views show it) in this job, or None if it has none yet.

    Read from the job's own file row rather than the document registry: a
    converted binary registers under its derived markdown, so the registry
    never knows `report.pdf` by that name -- and the job row is unambiguous
    where two domains hold same-named documents.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT filename, doc_sha256 FROM ingestion_job_files WHERE job_id = ? AND doc_sha256 IS NOT NULL",
            (job_id,),
        ).fetchall()
    finally:
        conn.close()
    target = document.upper()
    for filename, sha in rows:
        if Path(filename).name.upper() == target:
            return sha
    return None


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
            "SELECT filename, status, current_step, doc_sha256, entity_count, relationship_count"
            " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        files = []
        for r in cursor.fetchall():
            entry = {
                "filename": r[0], "status": r[1], "current_step": r[2],
                "entities": r[4], "relationships": r[5],
            }
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
            "stalled": _stalled(row[1]),
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
            "SELECT filename, status, error_message, started_at, completed_at,"
            " entity_count, relationship_count"
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
                "entities": r[5],
                "relationships": r[6],
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

    A stalled job (`processing`, no live worker) also resets the file its
    worker died on -- left at `processing`, nothing would ever pick it up.
    While a live worker still has the job, there is nothing to retry yet:
    resetting its rows underneath it would race the worker, so that refuses.
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT domain, status FROM ingestion_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Job '{job_id}' not found")
        domain = row[0] or "general"
        stalled = False
        if row[1] == "processing":
            if worker_alive():
                raise ValueError(f"Job '{job_id}' is still running; retry it once it finishes")
            stalled = True

        statuses = ("failed", "skipped") if include_skipped else ("failed",)
        if stalled:
            statuses = (*statuses, "processing")
        placeholders = ",".join("?" * len(statuses))
        file_rows = conn.execute(
            f"SELECT filename FROM ingestion_job_files"
            f" WHERE job_id = ? AND status IN ({placeholders})",
            (job_id, *statuses),
        ).fetchall()
        filenames = [r[0] for r in file_rows]

        if not filenames and not stalled:
            return {"job_id": job_id, "domain": domain, "retried": 0, "deregistered": 0, "files": [], "stalled": False}
        # A stalled job with nothing failed (its worker died between files)
        # still needs re-queueing: its remaining `queued` files are the retry.

        # Remove from document registry, matched by bare filename against
        # `path`'s own basename -- Phase 5 dropped the registry's `filename`
        # column (it was never anything but `Path(path).name`); no portable
        # basename function in plain sqlite3, so matched in Python instead.
        domain_rows = conn.execute(
            "SELECT path FROM documents WHERE domain = ?", (domain,)
        ).fetchall()
        bare_targets = {Path(f).name.upper() for f in filenames}
        matching_paths = [
            row[0] for row in domain_rows if Path(row[0]).name.upper() in bare_targets
        ]
        deregistered = 0
        for path in matching_paths:
            cursor = conn.execute(
                "DELETE FROM documents WHERE domain = ? AND path = ?", (domain, path)
            )
            deregistered += cursor.rowcount

        # Reset file rows to queued
        fn_placeholders = ",".join("?" * len(filenames))
        if filenames:
            conn.execute(
                f"UPDATE ingestion_job_files"
                f" SET status='queued', current_step=NULL, doc_sha256=NULL,"
                f"     started_at=NULL, completed_at=NULL, error_message=NULL,"
                f"     entity_count=NULL, relationship_count=NULL"
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
            "stalled": stalled,
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
