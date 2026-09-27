"""Is the ingest worker alive? One answer for `vault sync`'s preflight, the
worker's own start-up, and the CLI that spawns it (phase 1 review finding 7:
three copies had drifted). Stdlib only, so `vault_sync` can import it
without pulling in the worker's ingestion stack."""
from __future__ import annotations

import os
from pathlib import Path


def live_pid(pid_file: Path) -> int | None:
    """The pid in `pid_file` when that process is alive, else None.

    A missing or unparseable file, or a pid with no process behind it, is no
    worker (a stale file is safe to overwrite). A process owned by another
    user (`PermissionError` from `kill(pid, 0)`) is alive."""
    try:
        pid = int(Path(pid_file).read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid
