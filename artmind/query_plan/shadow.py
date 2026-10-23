"""Shadow mode: a `query plan` for every traced turn, stored beside its trace (spec 2026-10-22 §6).

The plan runs in a worker thread alongside the agent, from the user's own text,
and is recorded as a `plan_shadow` trace event when the turn finishes. The agent
never sees it. It never delays the agent; it may hold the end of a turn up to
WAIT_S after the plan started. A plan still running then is recorded later, by a
background thread, up to LATE_WAIT_S after the turn (else `status: timeout`), so a
slow decision model never holds up the next turn; a process that exits first (a
benchmark run's last question) records none. Nobody waits on a shadow plan, so its
decision-model calls get DECISION_TIMEOUT_S (ARTMIND_PLAN_SHADOW_DECISION_TIMEOUT)
instead of the interactive ARTMIND_DECISION_TIMEOUT. It never fails a turn: every
failure becomes `status: error`. `followup` means "the session has an earlier traced turn".

Privacy: with a hosted ARTMIND_DECISION_URL, every traced question is sent
there for classification. ARTMIND_PLAN_SHADOW=0 stops it. At most MAX_RUNNING
plans run at once; further turns record `status: skipped`.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time

from loguru import logger

WAIT_S = 10.0
LATE_WAIT_S = 90.0
DECISION_TIMEOUT_S = 60.0
MAX_RUNNING = 8
MAX_PAYLOAD_BYTES = 60_000
_THREAD_NAME = "plan-shadow"


def enabled() -> bool:
    return (os.environ.get("ARTMIND_PLAN_SHADOW") or "1").strip().casefold() not in ("0", "false", "no", "off")


def decision_timeout() -> float:
    try:
        value = float(os.environ.get("ARTMIND_PLAN_SHADOW_DECISION_TIMEOUT") or DECISION_TIMEOUT_S)
    except ValueError:
        return DECISION_TIMEOUT_S
    return value if math.isfinite(value) and value > 0 else DECISION_TIMEOUT_S


def _trim(plan):
    """Shrink an oversized plan so its event payload stays valid JSON under the store cap."""
    try:
        if not isinstance(plan, dict) or len(json.dumps(plan, default=str)) <= MAX_PAYLOAD_BYTES:
            return plan
        trimmed = dict(plan)
        entities = []
        for entity in plan.get("entities") or []:
            if isinstance(entity, dict) and isinstance(entity.get("candidates"), list):
                entity = {**entity, "candidates": entity["candidates"][:2]}
            entities.append(entity)
        if "entities" in plan:
            trimmed["entities"] = entities
        if isinstance(plan.get("warnings"), list):
            trimmed["warnings"] = plan["warnings"][:10]
        trimmed["trimmed"] = True
        if len(json.dumps(trimmed, default=str)) <= MAX_PAYLOAD_BYTES:
            return trimmed
        # Last resort: keep only what the reader needs to classify the plan.
        minimal = {"confidence": plan.get("confidence"), "route": plan.get("route"),
                   "warnings": list(plan.get("warnings") or [])[:3], "trimmed": True}
        for key in ("steps", "suggested_steps"):
            if plan.get(key):
                minimal[key] = list(plan[key])[:1]
                break
        return minimal
    except Exception:
        return {"trimmed": True}


class _Skipped:
    running = False

    def __init__(self, followup: bool):
        self.followup = followup

    def result(self, timeout: float | None = None) -> dict:
        return {"status": "skipped", "followup": self.followup, "plan": None,
                "error": "too many shadow plans running"}


class ShadowPlan:
    def __init__(self, question: str, domains: list[str] | None, followup: bool):
        self.followup = followup
        self._plan: dict | None = None
        self._error: str | None = None
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, args=(question, domains), name=_THREAD_NAME, daemon=True)
        self._thread.start()

    def _run(self, question: str, domains: list[str] | None) -> None:
        try:
            from artmind.query_plan import planner

            self._plan = planner.plan(question, domains, decision_timeout=decision_timeout())
        except Exception as exc:
            logger.warning("shadow plan failed: {}", exc)
            self._error = f"{type(exc).__name__}: {exc}"

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def result(self, timeout: float | None = None) -> dict:
        if timeout is None:
            timeout = max(0.0, WAIT_S - (time.monotonic() - self._started))
        self._thread.join(timeout)
        if self._thread.is_alive():
            return {"status": "timeout", "followup": self.followup, "plan": None, "error": None}
        if self._error is not None:
            return {"status": "error", "followup": self.followup, "plan": None, "error": self._error}
        return {"status": "ok", "followup": self.followup, "plan": _trim(self._plan), "error": None}


def start(question: str | None, domains: list[str] | None = None, followup: bool = False):
    if not enabled() or not (question or "").strip():
        return None
    try:
        if sum(1 for t in threading.enumerate() if t.name == _THREAD_NAME and t.is_alive()) >= MAX_RUNNING:
            return _Skipped(followup)
        return ShadowPlan(question, domains, followup)
    except Exception as exc:  # a thread that cannot start must not break the turn
        logger.warning("shadow plan not started: {}", exc)
        return None
