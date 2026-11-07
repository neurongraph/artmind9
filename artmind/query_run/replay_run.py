"""`artmind plan replay --run` (P3 spec §9.1): plan each label once, execute it, and measure.

Per label: plan_ms, exec_ms, executed steps, escalation, and expected documents reached by any step
(`steps_reached`) and by the placed bundle (`bundle_reached`). Counts and label ids are printed;
matched and missed paths go to <ARTMIND_HOME>/logs/plan-run-*.json (private).
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

from artmind.query_plan import reach as reach_mod
from artmind.query_run import bundle as bundle_mod
from artmind.query_run import executor, facts as facts_mod


def p90(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[max(0, math.ceil(0.9 * len(values)) - 1)]


def _bundle_docs(printed: dict) -> list[dict]:
    docs = [{"id": (e.get("doc") or {}).get("id"), "title": (e.get("doc") or {}).get("title"), "path": None,
             "source_path": None, "name": None} for e in printed.get("evidence") or []]
    docs += [{"id": d.get("doc_id"), "title": d.get("title"), "path": None, "source_path": None, "name": None}
             for d in printed.get("documents") or []]
    return docs


def run_labels(labels, classifier: str, *, model=None, given_domains=False, plan_fn=None, step_fn=None,
               clock=time.perf_counter, budget: int = bundle_mod.DEFAULT_BUDGET) -> dict:
    from artmind.query_plan import planner
    from artmind.query_plan import replay as replay_mod

    base = plan_fn or planner.plan
    timings: dict[str, list[float]] = {}   # question -> timings in call order (two labels may share a question)

    def timed(question, *a, **k):
        started = clock()
        try:
            return base(question, *a, **k)
        finally:
            timings.setdefault(question, []).append(round((clock() - started) * 1000, 1))

    subset = [lb for lb in labels if lb.expected_documents]
    plans: dict[str, dict] = {}
    report = replay_mod.replay(subset, classifier, 1, given_domains=given_domains, model=model, plan_fn=timed,
                               threads={}, plans_out=plans)
    errors = {r["id"] for r in report["errors"]}
    rows = []
    for lb in subset:
        expected = list(lb.expected_documents)
        plan_ms = (timings.get(lb.question) or [None]).pop(0)
        if lb.id in errors:
            rows.append({"id": lb.id, "expected": len(expected), "steps_reached": 0, "bundle_reached": 0, "plan_ms": plan_ms,
                         "exec_ms": None, "executed": 0, "escalate": "plan_error", "detail": {}})
            continue
        plan = plans[lb.id]
        ex = executor.execute(plan, step_fn=step_fn, clock=clock)
        printed, _ = bundle_mod.build(plan, ex, budget=budget)
        step_docs = [d for r in ex.results.values() if r.payload for d in facts_mod.step_documents(r.payload)]
        steps_matched, _ = reach_mod.reached_documents(expected, step_docs)
        bundle_matched, missed = reach_mod.reached_documents(expected, _bundle_docs(printed))
        rows.append({"id": lb.id, "expected": len(expected), "steps_reached": len(steps_matched),
                     "bundle_reached": len(bundle_matched), "plan_ms": plan_ms, "exec_ms": ex.exec_ms,
                     "executed": ex.executed, "escalate": (ex.escalate or {}).get("reason"),
                     "detail": {"matched": bundle_matched, "missed": missed, "path": ex.path}})
    small = [r["exec_ms"] for r in rows if r["exec_ms"] is not None and 1 <= r["executed"] <= 4]
    totals = {"labels": len(rows), "expected": sum(r["expected"] for r in rows),
              "steps_reached": sum(r["steps_reached"] for r in rows), "bundle_reached": sum(r["bundle_reached"] for r in rows),
              "plan_ms_p90": p90([r["plan_ms"] for r in rows]), "exec_ms_p90_1_4_steps": p90(small),
              "exec_ms_p90_all": p90([r["exec_ms"] for r in rows]),
              "escalated": sum(1 for r in rows if r["escalate"])}
    return {"classifier": classifier, "totals": totals, "labels": rows}


def public_report(report: dict) -> dict:
    return {**report, "labels": [{k: v for k, v in r.items() if k != "detail"} for r in report["labels"]]}


def write_detail(report: dict) -> Path:
    import paths

    path = Path(paths.LOGS_DIR) / f"plan-run-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return path
