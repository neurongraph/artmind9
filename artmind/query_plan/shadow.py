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
failure becomes `status: error`. The plan is given the session's previous turn, rebuilt from the trace
store (`previous.previous_from_trace`; the plan's `previous_used` says whether it mattered). `followup` means "the session has an earlier traced turn".

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

#: Commands that make an LLM call; a dry run skips them unless ARTMIND_PLAN_SHADOW_RUN=all.
LLM_COMMANDS = frozenset({"query graph text2cypher", "query text2sql"})


def run_mode() -> str:
    """ARTMIND_PLAN_SHADOW_RUN: 0 (default, off), 1 (dry-run the program, LLM steps skipped), all (LLM steps too)."""
    raw = (os.environ.get("ARTMIND_PLAN_SHADOW_RUN") or "0").strip().casefold()
    if raw in ("", "0", "false", "no", "off"):
        return "0"
    return "all" if raw == "all" else "1"


def dry_run(plan: dict, *, mode: str, step_fn=None) -> dict:
    """Execute a shadow plan with no trace spans; return a `run_shadow` payload of ids and counts only."""
    try:
        from artmind.query_run import bundle, capture, executor
        from artmind.traces.span import trace_override

        real = step_fn or capture.invoke_step

        def step(argv, sid):
            if mode != "all" and capture.command_key(argv) in LLM_COMMANDS:
                return capture.StepResult(sid, list(argv), 0, None, None, 0.0, 0, skipped="llm_shadow")
            return real(argv, sid)

        with trace_override(None):
            ex = executor.execute(plan, step_fn=step)
            printed, _ = bundle.build(plan, ex, budget=bundle.DEFAULT_BUDGET)
        skipped = {sid for sid, r in ex.results.items() if r.skipped}
        path = [{"id": p.get("id"), "kind": p.get("kind"), "rows": p.get("rows"), "ms": p.get("ms"),
                 "exit_code": p.get("exit_code"),
                 "skipped": "llm_shadow" if p.get("id") in skipped else p.get("skipped")} for p in ex.path]
        docs = [d for d in dict.fromkeys((e.get("doc") or {}).get("id") for e in printed.get("evidence") or []) if d]
        docs += [d.get("doc_id") for d in printed.get("documents") or [] if d.get("doc_id") and d.get("doc_id") not in docs]
        return {"status": "ok", "path": path, "timing": {"exec_ms": ex.exec_ms}, "escalate": (ex.escalate or {}).get("reason"),
                "bundle_chars": len(json.dumps(printed, default=str)), "documents": docs[:200],
                "route": (plan.get("route") or {}).get("id"), "level": (plan.get("confidence") or {}).get("level"),
                "program_kind": plan.get("program_kind")}
    except Exception as exc:
        logger.warning("shadow dry run failed: {}", exc)
        return {"status": "error", "error": type(exc).__name__}


def covers_agent(bundle_docs: list[str], agent_spans: list[dict]) -> float | None:
    """Share of the documents the agent's own spans cite that the bundle also holds; None when the agent cited none."""
    agent = {d.get("doc_id") for s in agent_spans for d in ((s.get("evidence") or {}).get("documents") or [])
             if isinstance(d, dict) and d.get("doc_id")}
    if not agent:
        return None
    return round(len(agent & set(bundle_docs)) / len(agent), 3)


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
        if len(json.dumps(trimmed, default=str)) > MAX_PAYLOAD_BYTES:
            for key in ("program", "guards", "gaps"):  # the v2 blocks the reader can do without
                if key in trimmed:
                    trimmed[key] = []
            if "replacements" in trimmed:
                trimmed["replacements"] = {}
        if len(json.dumps(trimmed, default=str)) <= MAX_PAYLOAD_BYTES:
            return trimmed
        # Last resort: keep only what the reader needs to classify the plan.
        minimal = {"confidence": plan.get("confidence"), "route": plan.get("route"),
                   "warnings": list(plan.get("warnings") or [])[:3], "trimmed": True}
        for key in ("intent", "program_kind", "slot_quality"):  # v2 fields the traces CLI reads
            if plan.get(key):
                minimal[key] = plan[key]
        for key in ("steps", "suggested_steps"):
            if plan.get(key):
                minimal[key] = list(plan[key])[:1]
                break
        return minimal
    except Exception:
        return {"trimmed": True}


def _previous_for(trace_id: str | None):
    """The turn before this one in its session, for a reply like "yes please". Any failure plans without it."""
    if not trace_id:
        return None
    try:
        from artmind.query_plan import previous

        return previous.previous_from_trace(trace_id)
    except Exception as exc:
        logger.warning("shadow plan: previous turn not built: {}", exc)
        return None


class _Skipped:
    running = False

    def __init__(self, followup: bool):
        self.followup = followup

    def result(self, timeout: float | None = None) -> dict:
        return {"status": "skipped", "followup": self.followup, "plan": None,
                "error": "too many shadow plans running"}


class ShadowPlan:
    def __init__(self, question: str, domains: list[str] | None, followup: bool, trace_id: str | None = None):
        self.followup = followup
        self._plan: dict | None = None
        self._error: str | None = None
        self._dry: dict | None = None
        self._previous_given = False
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, args=(question, domains, trace_id), name=_THREAD_NAME, daemon=True)
        self._thread.start()

    def _run(self, question: str, domains: list[str] | None, trace_id: str | None = None) -> None:
        try:
            from artmind.query_plan import planner

            kwargs = {"decision_timeout": decision_timeout()}
            previous = _previous_for(trace_id)
            self._previous_given = previous is not None
            if previous is not None:
                kwargs["previous"] = previous
            self._plan = planner.plan(question, domains, **kwargs)
            mode = run_mode()
            if mode != "0" and isinstance(self._plan, dict):
                self._dry = dry_run(self._plan, mode=mode)
        except Exception as exc:
            logger.warning("shadow plan failed: {}", exc)
            self._error = f"{type(exc).__name__}: {exc}"

    @property
    def dry(self) -> dict | None:
        return self._dry

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
        return {"status": "ok", "followup": self.followup, "plan": _trim(self._plan), "error": None,
                "previous_given": self._previous_given}


def start(question: str | None, domains: list[str] | None = None, followup: bool = False,
          trace_id: str | None = None):
    if not enabled() or not (question or "").strip():
        return None
    try:
        if sum(1 for t in threading.enumerate() if t.name == _THREAD_NAME and t.is_alive()) >= MAX_RUNNING:
            return _Skipped(followup)
        return ShadowPlan(question, domains, followup, trace_id)
    except Exception as exc:  # a thread that cannot start must not break the turn
        logger.warning("shadow plan not started: {}", exc)
        return None
