"""Offline replay of `query plan` over the labelled set (spec 2026-10-22 §4.6).

No agent: each label's question is planned (against the live graph) and the plan
is scored against the label. `wrong` (a confident plan that is wrong) is the
number to drive to zero; `abstained` (a low-confidence plan) is reported
separately, since the agent then falls back to the routing table.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

OUTCOMES = ("correct", "abstained", "correct_abstain", "wrong", "error")
WRONG_RATE_GATE = 0.10
REPEAT_GATE = 4


def _names(value) -> set[str]:
    return {str(v).casefold() for v in (value if isinstance(value, list) else [value])}


def _domains_match(selected: list[str], expected) -> bool:
    if set(selected) == set(expected):
        return True
    return len(selected) == 1 and all(d == selected[0] or d.startswith(selected[0] + ".") for d in expected)


def _param_wrong(expected, entry: dict | None) -> bool:
    if entry is None:
        return True
    if entry.get("type") == "entity":
        refs = entry.get("candidates") if entry.get("twins") else ([entry["chosen"]] if entry.get("chosen") else [])
        got = {str(r.get("name") or "").casefold() for r in refs or []}
        return not got or not got <= _names(expected)
    return entry.get("value") != expected


def score(label, plan: dict) -> dict:
    mismatches = []
    if not _domains_match(plan["domains"]["selected"], label.domains):
        mismatches.append("domains")
    if plan["route"]["id"] != label.route:
        mismatches.append("route")
    got_view = (plan.get("view_match") or {}).get("name") if plan["route"]["id"] == "view" else None
    if got_view != label.view:
        mismatches.append("view")
    if label.view and got_view == label.view:
        params = plan["view_match"]["params"]
        mismatches += [f"param:{n}" for n, expected in label.params.items() if _param_wrong(expected, params.get(n))]
    level = plan["confidence"]["level"]
    if level == "low":
        outcome = "correct_abstain" if label.expect == "low" else "abstained"
    elif label.expect == "low":
        outcome, mismatches = "wrong", [*mismatches, "confidence"]
    else:
        outcome = "wrong" if mismatches else "correct"
    return {"id": label.id, "outcome": outcome, "mismatches": mismatches, "route": plan["route"]["id"],
            "expected_route": label.route, "view": got_view, "level": level,
            "latency_ms": plan["classifier"]["latency_ms"], "fell_back": plan["classifier"]["fell_back"]}


def _stable(plan: dict) -> str:
    copy = json.loads(json.dumps(plan, default=str))
    copy.get("classifier", {}).pop("latency_ms", None)
    return json.dumps(copy, sort_keys=True)


def _percentile(values: list[float], q: float) -> float | None:
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def summarise(results: list[dict], unstable: list[str], classifier: str, repeat: int = 1) -> dict:
    counts = {o: sum(1 for r in results if r["outcome"] == o) for o in OUTCOMES}
    per_route: dict[str, dict[str, int]] = {}
    for r in results:
        per_route.setdefault(r["expected_route"], {o: 0 for o in OUTCOMES})[r["outcome"]] += 1
    latencies = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    view_wrong = sum(1 for r in results if r["expected_route"] == "view" and r["outcome"] == "wrong")
    wrong_rate = counts["wrong"] / len(results) if results else None
    passed = (view_wrong == 0 and wrong_rate is not None and wrong_rate < WRONG_RATE_GATE
              and not unstable and repeat >= REPEAT_GATE)
    return {
        "classifier": classifier,
        "labels": len(results),
        "outcomes": counts,
        "per_route": dict(sorted(per_route.items())),
        "wrong": [{"id": r["id"], "mismatches": r["mismatches"]} for r in results if r["outcome"] == "wrong"],
        "errors": [{"id": r["id"], "error_type": r.get("error_type"), "error": r.get("error")}
                   for r in results if r["outcome"] == "error"],
        "unstable": unstable,
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
        "gate": {"view_wrong": view_wrong, "wrong_rate": wrong_rate, "repeat": repeat, "passed": passed},
        "results": results,
    }


def replay(labels, classifier: str, repeat: int = 1, *, given_domains: bool = False, plan_fn=None) -> dict:
    if plan_fn is None:
        from artmind.query_plan import planner

        plan_fn = planner.plan
    results: list[dict] = []
    unstable: list[str] = []
    for label in labels:
        domains = list(label.domains) if given_domains else None
        try:
            runs = [plan_fn(label.question, domains, classifier=classifier) for _ in range(max(1, repeat))]
        except Exception as exc:  # one label failing must not stop the replay (spec §7)
            results.append({"id": label.id, "outcome": "error", "mismatches": [], "error": str(exc),
                            "error_type": type(exc).__name__,
                            "route": None, "expected_route": label.route, "view": None, "level": None,
                            "latency_ms": None, "fell_back": None})
            continue
        if len({_stable(r) for r in runs}) > 1:
            unstable.append(label.id)
        results.append(score(label, runs[0]))
    return summarise(results, unstable, classifier, repeat)


def write_detail(report: dict) -> Path:
    """Per-label detail into <ARTMIND_HOME>/logs/ (gitignored, private), never the checkout."""
    import paths

    path = Path(paths.LOGS_DIR) / f"plan-replay-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return path
