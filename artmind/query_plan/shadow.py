"""Shadow mode: a `query plan` for every traced turn, stored beside its trace (spec 2026-10-22 §6).

The plan runs in a worker thread alongside the agent, from the user's own text,
and is recorded as a `plan_shadow` trace event when the turn finishes. The agent
never sees it. It never delays or fails a turn: every failure becomes
`status: error`. Kill switch: ARTMIND_PLAN_SHADOW=0.
"""

from __future__ import annotations

import os
import threading

from loguru import logger

WAIT_S = 10.0


def enabled() -> bool:
    return (os.environ.get("ARTMIND_PLAN_SHADOW") or "1").strip().casefold() not in ("0", "false", "no", "off")


class ShadowPlan:
    def __init__(self, question: str, domains: list[str] | None, followup: bool):
        self.followup = followup
        self._plan: dict | None = None
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, args=(question, domains), name="plan-shadow", daemon=True)
        self._thread.start()

    def _run(self, question: str, domains: list[str] | None) -> None:
        try:
            from artmind.query_plan import planner

            self._plan = planner.plan(question, domains)
        except Exception as exc:
            logger.debug("shadow plan failed: {}", exc)
            self._error = f"{type(exc).__name__}: {exc}"

    def result(self, timeout: float | None = None) -> dict:
        self._thread.join(WAIT_S if timeout is None else timeout)
        if self._thread.is_alive():
            return {"status": "timeout", "followup": self.followup, "plan": None, "error": None}
        if self._error is not None:
            return {"status": "error", "followup": self.followup, "plan": None, "error": self._error}
        return {"status": "ok", "followup": self.followup, "plan": self._plan, "error": None}


def start(question: str | None, domains: list[str] | None = None, followup: bool = False) -> ShadowPlan | None:
    if not enabled() or not (question or "").strip():
        return None
    try:
        return ShadowPlan(question, domains, followup)
    except Exception as exc:  # a thread that cannot start must not break the turn
        logger.debug("shadow plan not started: {}", exc)
        return None
