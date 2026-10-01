#!/usr/bin/env python3
"""Background worker for processing artmind ingestion jobs. Run with: uv run artmind/worker.py"""
import fcntl
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# When run as a script, add the project root to sys.path so absolute imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from artmind.db import _get_db
from artmind.ingest import ingest_file, ingest_to_kg, kg_work_was_done
from artmind.jobs import _update_job_file_status, _update_job_status
from artmind.structured import is_structured_source
from artmind.structured.pipeline import ingest_structured_file
from paths import ARTMIND_VAULT_DIR, LOGS_DIR, PROJECT_ROOT, WORKER_LOG, WORKER_PID_FILE
from utils.functions import load_env, resolve_llm_model

WORKER_LOG.parent.mkdir(parents=True, exist_ok=True)

# Kept open (and thus flocked) for the lifetime of this process -- dropping
# the reference would close the fd and silently release the lock.
_pid_file_handle = None


# `live_pid` briefly takes this same lock (LOCK_EX, then releases it) to
# probe whether anyone holds it. A starting worker's own LOCK_NB attempt can
# land inside that window, so a bare single attempt could see a transient
# "already running" and exit -- retry briefly before believing it.
_LOCK_ATTEMPTS = 6  # the first attempt, plus up to 5 retries
_LOCK_RETRY_DELAY_S = 0.05


def _acquire_pid_file() -> bool:
    """Take the pid file's exclusive flock, or refuse if another worker
    already holds it (review follow-up: liveness is the lock, not the pid
    number -- a pid file surviving a reboot can get its pid recycled by an
    unrelated process, which `kill(pid, 0)` used to mistake for us)."""
    global _pid_file_handle
    existed = WORKER_PID_FILE.exists()
    fd = open(WORKER_PID_FILE, "a+")
    for attempt in range(_LOCK_ATTEMPTS):
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if attempt == _LOCK_ATTEMPTS - 1:
                fd.seek(0)
                try:
                    pid = int(fd.read().strip())
                except (OSError, ValueError, OverflowError):
                    pid = None
                fd.close()
                logger.warning("Worker already running (PID {}), exiting", pid)
                return False
            time.sleep(_LOCK_RETRY_DELAY_S)
    if existed:
        logger.info("Stale PID file found, overwriting")
    fd.seek(0)
    fd.truncate()
    fd.write(str(os.getpid()))
    fd.flush()
    _pid_file_handle = fd  # hold the lock for this process's lifetime
    return True


def _release_pid_file() -> None:
    """Release this process's hold on the pid file at exit.

    Deliberately does NOT unlink it. Locks live on the inode, but `live_pid`
    finds the file by *path*: if this unlinked it, a fresh process could
    open a new inode at the same path and take an uncontended lock while an
    older, slower-to-exit process's lock on the orphaned old inode was still
    live -- `live_pid` would then report "no worker" and let a third worker
    start, so two would run at once. An unlocked, stale pid file is
    harmless: the next worker's `_acquire_pid_file` overwrites it in place."""
    global _pid_file_handle
    if _pid_file_handle is not None:
        _pid_file_handle.close()
        _pid_file_handle = None


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


def _process_job(
    job_id: str, domain: str, env: dict, force: bool = False, stage_only: bool = False
) -> None:
    image_model = env.get("ARTMIND_IMAGE_MODEL", "gemma4:e4b")
    text_model = resolve_llm_model(env)
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    chunk_size = int(env.get("ARTMIND_KG_CHUNK_SIZE", "6000"))

    queued_files = _get_queued_files(job_id)
    processed_count = _count_processed(job_id)

    _update_job_status(job_id, status="processing", started_at=datetime.now().isoformat())
    logger.info("Processing job {} — {} queued file(s)", job_id, len(queued_files))

    # One file rebuilds incrementally; a multi-file job defers to one full
    # rebuild per domain at the end — mirroring `ingest sync`'s directory
    # batching (cli.py::ingest_sync). Before this, the worker committed once
    # per file with no deferral at all: correct, just N incremental rebuilds
    # (and N embed sweeps against descriptions the next file was about to
    # change) instead of one.
    defer_rebuild = len(queued_files) > 1 and not stage_only
    deferred_domains: set[str] = set()

    # The manifest is the source of truth, re-read here rather than frozen
    # into the job row: a mapping corrected between queueing and processing
    # takes effect, and no job-schema change is needed.
    vault_manifest = None
    if ARTMIND_VAULT_DIR is not None:
        from artmind.manifest import ManifestError, load as _load_manifest

        try:
            vault_manifest = _load_manifest(ARTMIND_VAULT_DIR)
        except ManifestError as exc:
            logger.warning(
                "Ignoring the vault manifest for this batch -- {}. Files will "
                "use the job's own domain.", exc,
            )

    def _domain_for(f: Path) -> str:
        """The mapped domain, falling back to the job's own."""
        if vault_manifest is None or ARTMIND_VAULT_DIR is None:
            return domain
        try:
            rel = Path(f).resolve().relative_to(ARTMIND_VAULT_DIR).as_posix()
        except ValueError:
            return domain
        return vault_manifest.domain_for(rel) or domain

    for file_path_str in queued_files:
        file_path = Path(file_path_str)
        logger.info("File: {}", file_path.name)

        try:
            if is_structured_source(file_path):
                # No stage_only waypoint for structured files — a parquet load
                # is inherently a single commit, so the flag is ignored here.
                res = ingest_structured_file(file_path, _domain_for(file_path), force=force)
                if res.get("status") in ("ok", "skipped"):
                    _update_job_file_status(
                        job_id,
                        file_path_str,
                        status="completed",
                        current_step=None,
                        completed_at=datetime.now().isoformat(),
                    )
                else:
                    _update_job_file_status(
                        job_id,
                        file_path_str,
                        status="failed",
                        current_step=None,
                        completed_at=datetime.now().isoformat(),
                        error_message=res.get("error", "ingest_structured_file failed"),
                    )
            else:
                result = ingest_file(
                    file_path, image_model, _domain_for(file_path),
                    job_id=job_id, chunk_size=chunk_size,
                )
                if result.get("status") == "ok":
                    _update_job_file_status(
                        job_id, file_path_str,
                        current_step="extract_kg",
                        doc_sha256=result.get("sha256"),
                    )
                    effective_domain = result.get("domain", domain)
                    kg_ok = ingest_to_kg(
                        result, effective_domain, text_model, embed_model, chunk_size,
                        stage_only=stage_only, defer_rebuild=defer_rebuild,
                    )
                    # kg_ok is True for a short-circuited no_op/metadata_only
                    # file just as much as for one that did real extraction --
                    # only the latter leaves anything for a deferred rebuild
                    # to pick up (see ingest.kg_work_was_done).
                    if kg_ok and defer_rebuild and kg_work_was_done(result):
                        deferred_domains.add(effective_domain)
                    _update_job_file_status(
                        job_id,
                        file_path_str,
                        status="completed" if kg_ok else "failed",
                        current_step=None,
                        completed_at=datetime.now().isoformat(),
                        error_message=None if kg_ok else "KG ingestion failed",
                        kg_counts=result.get("kg_counts"),
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

    if deferred_domains:
        from artmind.ingest import rebuild_projection

        logger.info("═══ Deferred projection rebuild over {} domain(s)", len(deferred_domains))
        for deferred_domain in sorted(deferred_domains):
            summary = rebuild_projection(deferred_domain)
            logger.info("Projection rebuilt for {}: {}", deferred_domain, summary)

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
                "SELECT job_id, domain, force, stage_only FROM ingestion_jobs"
                " WHERE status = 'queued' ORDER BY queued_at ASC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        if row:
            _process_job(
                row[0], row[1] or "general", env, force=bool(row[2]), stage_only=bool(row[3])
            )
        else:
            logger.info("Queue empty, worker exiting")
            return


def main() -> None:
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
        _release_pid_file()


if __name__ == "__main__":
    main()
