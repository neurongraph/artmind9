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


# ── the daily topic plan check (spec 2026-11-10 §8.3) ─────────────────────────

TOPIC_CHECK_INTERVAL_S = 24 * 3600.0
TOPIC_CHECK_FIRST_S = 120.0
_topic_thread: threading.Thread | None = None


def start_topic_checks(auto_refresh: bool = False) -> bool:
    """Start the daily freshness check of every topic plan (no LLM); `auto_refresh` (the admin
    console only) also refreshes plans that opted in. Once per process; False if running."""
    global _topic_thread
    with _lock:
        if _topic_thread is not None and _topic_thread.is_alive():
            return False

        def loop():
            from artmind.topics import schedule

            time.sleep(TOPIC_CHECK_FIRST_S)
            while True:
                try:
                    schedule.run_once(auto_refresh=auto_refresh)
                except Exception:
                    logger.exception("topics: daily plan check failed")
                time.sleep(TOPIC_CHECK_INTERVAL_S)

        _topic_thread = threading.Thread(target=loop, name="topic-plan-checks", daemon=True)
        _topic_thread.start()
    return True
