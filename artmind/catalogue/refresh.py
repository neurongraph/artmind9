"""Background refresh of the `health` part (spec 2026-11-02 §4).

Only the long-lived servers (`serve`, `chat-ui`, `admin-ui`) call `enable_background()`
at start. A one-shot CLI call never does, so `schedule_health` is then a no-op and no
thread starts. Debounced: at most one rebuild per domain per `DEBOUNCE_S`.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from loguru import logger

DEBOUNCE_S = 300.0

_lock = threading.Lock()
_enabled = False
_last: dict = {}  # domain -> monotonic time of the last scheduled rebuild
_running: set = set()
_clock: Callable[[], float] = time.monotonic
_spawn: Callable[[Callable[[], None]], None] | None = None  # tests run the job inline


def enable_background() -> None:
    global _enabled
    _enabled = True


def disable_background() -> None:
    global _enabled
    _enabled = False


def enabled() -> bool:
    return _enabled


def reset(clock: Callable[[], float] | None = None, spawn=None) -> None:
    global _enabled, _clock, _spawn
    with _lock:
        _enabled = False
        _last.clear()
        _running.clear()
    _clock = clock or time.monotonic
    _spawn = spawn


def _job(domain: str) -> None:
    try:
        from artmind.catalogue import cache

        cache.rebuild([domain], ["health"])
    except Exception:
        logger.exception("catalogue: background health refresh failed for {}", domain)
    finally:
        with _lock:
            _running.discard(domain)


def schedule_health(domains) -> list:
    """Start a background `health` rebuild for each domain not rebuilt in the last
    `DEBOUNCE_S`. Returns the domains scheduled; `[]` unless `enable_background()` ran."""
    if not _enabled:
        return []
    now = _clock()
    started = []
    with _lock:
        for d in domains:
            if d in _running or now - _last.get(d, -DEBOUNCE_S) < DEBOUNCE_S:
                continue
            _last[d] = now
            _running.add(d)
            started.append(d)
    for d in started:
        if _spawn is not None:
            _spawn(lambda d=d: _job(d))
        else:
            threading.Thread(target=_job, args=(d,), name=f"catalogue-health-{d}", daemon=True).start()
    return started
