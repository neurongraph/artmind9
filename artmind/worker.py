#!/usr/bin/env python3
"""Background worker for processing artmind ingestion jobs. Run with: uv run artmind/worker.py"""
import os
import sys
from datetime import datetime
from pathlib import Path

# When run as a script, add the project root to sys.path so absolute imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from artmind.db import _get_db
from artmind.ingest import ingest_file, ingest_to_kg
from artmind.jobs import _update_job_file_status, _update_job_status
from paths import LOGS_DIR, PROJECT_ROOT, WORKER_LOG, WORKER_PID_FILE
from utils.functions import load_env

WORKER_LOG.parent.mkdir(parents=True, exist_ok=True)


def _acquire_pid_file() -> bool:
    if WORKER_PID_FILE.exists():
        try:
            pid = int(WORKER_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            logger.warning("Worker already running (PID {}), exiting", pid)
            return False
        except (ProcessLookupError, ValueError):
            logger.info("Stale PID file found, overwriting")
    WORKER_PID_FILE.write_text(str(os.getpid()))
    return True


def _get_queued_files(job_id: str) -> list[str]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT filename FROM ingestion_job_files"
            " WHERE job_id = ? AND status = 'queued' ORDER BY id",
            (job_id,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _count_processed(job_id: str) -> int:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT processed_count FROM ingestion_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def _final_file_statuses(job_id: str) -> list[str]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT status FROM ingestion_job_files WHERE job_id = ?", (job_id,)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _process_job(job_id: str, domain: str, env: dict) -> None:
    image_model = env.get("ARTMIND_IMAGE_MODEL", "gemma4:e4b")
    text_model = env.get("ARTMIND_KG_LLM_MODEL", "ministral-3:14b")
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    chunk_size = int(env.get("ARTMIND_KG_CHUNK_SIZE", "6000"))

    queued_files = _get_queued_files(job_id)
    processed_count = _count_processed(job_id)

    _update_job_status(job_id, status="processing", started_at=datetime.now().isoformat())
    logger.info("Processing job {} — {} queued file(s)", job_id, len(queued_files))

    for file_path_str in queued_files:
        file_path = Path(file_path_str)
        logger.info("File: {}", file_path.name)

        try:
            result = ingest_file(
                file_path, image_model, domain, job_id=job_id, chunk_size=chunk_size
            )
            if result.get("status") == "ok":
                _update_job_file_status(
                    job_id, file_path_str,
                    current_step="extract_kg",
                    doc_sha256=result.get("sha256"),
                )
                kg_ok = ingest_to_kg(result, domain, text_model, embed_model, chunk_size)
                _update_job_file_status(
                    job_id,
                    file_path_str,
                    status="completed" if kg_ok else "failed",
                    current_step=None,
                    completed_at=datetime.now().isoformat(),
                    error_message=None if kg_ok else "KG ingestion failed",
                )
            elif result.get("status") == "skipped":
                # ingest_file already updated status to "skipped"
                pass
            else:
                _update_job_file_status(
                    job_id,
                    file_path_str,
                    status="failed",
                    current_step=None,
                    completed_at=datetime.now().isoformat(),
                    error_message=result.get("error", "ingest_file failed"),
                )
        except Exception as e:
            logger.error("Unexpected error on {}: {}", file_path.name, e)
            _update_job_file_status(
                job_id,
                file_path_str,
                status="failed",
                current_step=None,
                completed_at=datetime.now().isoformat(),
                error_message=str(e),
            )

        processed_count += 1
        _update_job_status(job_id, processed_count=processed_count)
        logger.info("Progress: {} processed", processed_count)

    statuses = _final_file_statuses(job_id)
    final = "failed" if any(s == "failed" for s in statuses) else "completed"
    _update_job_status(job_id, status=final, completed_at=datetime.now().isoformat())
    logger.info("Job {} → {}", job_id, final)


def _worker_loop(env: dict) -> None:
    logger.info("Worker started (PID {})", os.getpid())
    while True:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT job_id, domain FROM ingestion_jobs"
                " WHERE status = 'queued' ORDER BY queued_at ASC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        if row:
            _process_job(row[0], row[1] or "general", env)
        else:
            logger.info("Queue empty, worker exiting")
            return


if __name__ == "__main__":
    logger.remove()
    logger.add(
        WORKER_LOG,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} [{level:<7}] {message}",
        level="DEBUG",
        rotation="10 MB",
        retention=5,
    )
    logger.add(
        sys.stderr,
        format="{time:YYYY-MM-DD HH:mm:ss} [{level:<7}] {message}",
        level="INFO",
    )

    if not _acquire_pid_file():
        sys.exit(0)
    try:
        _worker_loop(load_env())
    finally:
        WORKER_PID_FILE.unlink(missing_ok=True)
