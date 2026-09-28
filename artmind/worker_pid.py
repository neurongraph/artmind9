"""Is the ingest worker alive? One answer for `vault sync`'s preflight, the
worker's own start-up, and the CLI that spawns it (phase 1 review finding 7:
three copies had drifted).

Liveness means the worker still holds an exclusive `flock` on its pid file,
not that some process happens to have that pid right now (review follow-up:
the pid file survives reboots, so a stale pid often gets recycled by an
unrelated -- sometimes privileged -- process afterwards; `kill(pid, 0)`
would then report "alive" and a `PermissionError` would make it worse,
silently stalling queued jobs forever). Stdlib only, so `vault_sync` can
import it without pulling in the worker's ingestion stack."""
from __future__ import annotations

import fcntl
from pathlib import Path

# Returned when a locked pid file's contents can't be trusted (missing,
# unparseable, or not a valid pid) but the lock itself proves a worker is
# alive. Not a real pid -- callers only compare it against None.
_UNKNOWN_PID = 1


def live_pid(pid_file: Path) -> int | None:
    """The pid a live worker's flock still holds on `pid_file`, else None.

    A missing file is no worker. Otherwise this takes a non-blocking
    exclusive flock on the file: if that fails, the worker holding it is
    alive, so this returns the pid named in the file (or `1` as a sentinel
    when that content is missing, unparseable, or not a positive pid). If
    the lock succeeds, nobody was holding it, so it is released immediately
    and this returns None -- the file is stale no matter what pid or
    process is sitting behind that number now."""
    try:
        fd = open(Path(pid_file), "r")
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                pid = int(fd.read().strip())
            except (OSError, ValueError, OverflowError):
                return _UNKNOWN_PID
            return pid if pid > 0 else _UNKNOWN_PID
        else:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            return None
    finally:
        fd.close()
