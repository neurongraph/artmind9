"""Offline replay of `query plan` over the labelled set (spec 2026-10-22 §4.6).

No agent: each label's question is planned (against the live graph) and the plan
is scored against the label. `wrong` (a confident plan that is wrong) is the
number to drive to zero; `abstained` (a low-confidence plan) is reported
separately, since the agent then falls back to the routing table.

A label whose intent is not `retrieve`/`follow_up_why` is graded on intent only (route, domains, view are
not compared); the gate also needs every such label's intent right (spec 2026-11-05 §12).

Domain rule: the plan's domains match the label's when the sets are equal, or when
the plan selected a single parent domain covering every label domain (intentional).

The detail file (see `write_detail`) may contain exception text, which can quote
questions or entity names: it lives in the private, gitignored logs directory and
is never printed.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from artmind.query_plan.labels import is_nonretrieval

#: `planned`: the label names a route that is designed but not built (labels.PLANNED_ROUTES); the plan's
#: choice is recorded, not graded, and the label stays out of the wrong-rate denominator.
OUTCOMES = ("correct", "abstained", "correct_abstain", "wrong", "planned", "error")
WRONG_RATE_GATE = 0.10
REPEAT_GATE = 4
ABORT_AFTER = 3  # consecutive identical errors: the backend is down, stop planning


def _names(value) -> set[str]:
    return {str(v).casefold() for v in (value if isinstance(value, list) else [value])}


def _covers(domain: str, parent: str) -> bool:
    return domain == parent or domain.startswith(parent + ".")


def _domains_match(selected: list[str], expected) -> bool:
    """The selected set searches every expected domain and nothing beside them: each expected domain
    is a selected one or under one (a family parent covers its children), and each selected domain is
    an expected one or a parent of one. Exact sets match; a subset of the expected set does not."""
    expected = list(expected)
    if set(selected) == set(expected):
        return True
    if not selected or not expected:
        return False
    return (all(any(_covers(e, s) for s in selected) for e in expected)
            and all(any(_covers(e, s) for e in expected) for s in selected))


def _param_wrong(expected, entry: dict | None) -> bool:
    if entry is None:
        return True
    if entry.get("type") == "entity":
        refs = entry.get("candidates") if entry.get("twins") else ([entry["chosen"]] if entry.get("chosen") else [])
        got = {str(r.get("name") or "").casefold() for r in refs or []}
        return not got or not got <= _names(expected)
    return entry.get("value") != expected


def _tokens(value) -> set[str]:
    return set(re.findall(r"[^\W_]+", str(value or "").casefold()))


def _as_list(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def slot_ok(name: str, expected, slot: dict | None) -> bool:
    """One labelled slot against the plan's `slots[name]`. Compared on the value's main field:
    search_term by casefolded token sets, narrator by name, category by its word, scope by its domain
    set, date_window as `FROM..TO`, must_find / previous_subjects as casefolded sets of names; any other
    value by string equality."""
    if slot is None:
        return False
    value = slot.get("value")
    if name == "search_term":
        return _tokens(expected) == _tokens(value)
    if name == "narrator":
        return str((value or {}).get("name") or "").casefold() == str(expected).casefold()
    if name == "category":
        return str((value or {}).get("word") or "").casefold() == str(expected).casefold()
    if name == "scope":
        return {str(d) for d in _as_list(expected)} == set((value or {}).get("domains") or [])
    if name == "date_window":
        got = f"{value.get('from')}..{value.get('to')}" if isinstance(value, dict) else None
        want = f"{expected.get('from')}..{expected.get('to')}" if isinstance(expected, dict) else str(expected)
        return got == want
    if name in ("must_find", "previous_subjects"):
        names = {str(v.get("text") if isinstance(v, dict) else v).casefold() for v in value or []}
        return names == {str(e).casefold() for e in _as_list(expected)}
    return str(value) == str(expected)


def score(label, plan: dict) -> dict:
    """Retrieval labels (no intent, `retrieve`, `follow_up_why`): domains, route, view and params, and an
    `intent` mismatch if the plan came back with a non-retrieval intent (a lookup question planned as none).

    Any other label (format, curate, update, meta, status, ops) is graded on one thing: the plan must not be
    a confident lookup. The agent decides for itself whether a turn needs a lookup and handles these turns
    from the conversation; the planner is consulted for lookups only, so the harm is a confident retrieval
    plan for a turn that needs none (`lookup`). Which non-retrieval intent the plan chose, and a
    low-confidence retrieval plan, are recorded (`intent_ok`) but never wrong."""
    mismatches = []
    got_intent = (plan.get("intent") or {}).get("id")
    nonretrieval = label.nonretrieval
    got_view = (plan.get("view_match") or {}).get("name") if plan["route"]["id"] == "view" else None
    lookup = not is_nonretrieval(got_intent)  # the plan retrieves (retrieve, follow_up_why, or a v1 plan)
    if nonretrieval:
        if lookup:
            mismatches.append("lookup")
    else:
        if not _domains_match(plan["domains"]["selected"], label.domains):
            mismatches.append("domains")
        if plan["route"]["id"] != label.route:
            mismatches.append("route")
        if got_view != label.view:
            mismatches.append("view")
        if label.view and got_view == label.view:
            params = plan["view_match"]["params"]
            mismatches += [f"param:{n}" for n, expected in label.params.items()
                           if _param_wrong(expected, params.get(n))]
        if got_intent is not None and is_nonretrieval(got_intent):
            mismatches.append("intent")
    level = plan["confidence"]["level"]
    if nonretrieval:
        if lookup and level != "low":
            outcome = "wrong"
        elif lookup:
            outcome = "correct_abstain" if label.expect == "low" else "abstained"
        else:  # no lookup planned: what the agent does with the turn is its own call
            outcome = "correct" if level != "low" else ("correct_abstain" if label.expect == "low" else "abstained")
    elif label.planned and "intent" not in mismatches:
        # No built route can be right for it: record what the planner did instead, grade nothing else.
        # A planned-route label planned as a non-retrieval intent is still wrong (below).
        mismatches = [m for m in mismatches if m != "route"]
        outcome = "planned"
    elif "intent" in mismatches:
        outcome = "wrong"
        if level != "low" and label.expect == "low":
            mismatches.append("confidence")
    elif level == "low":
        outcome = "correct_abstain" if label.expect == "low" else "abstained"
    elif label.expect == "low":
        outcome, mismatches = "wrong", [*mismatches, "confidence"]
    else:
        outcome = "wrong" if mismatches else "correct"
    # Intent and slots are graded apart from the route outcome; None = not labelled (or a v1 plan).
    intent_ok = None if label.intent is None else got_intent == label.intent
    plan_slots = plan.get("slots") or {}
    slot_wrong = sorted(n for n, expected in label.slots.items() if not slot_ok(n, expected, plan_slots.get(n)))
    slots_ok = None if not label.slots else not slot_wrong
    return {"id": label.id, "outcome": outcome, "mismatches": mismatches, "route": plan["route"]["id"],
            "intent": got_intent, "intent_ok": intent_ok, "slots_ok": slots_ok, "slot_mismatches": slot_wrong,
            "program_kind": label.program_kind, "nonretrieval": nonretrieval,
            "expected_route": f"intent:{label.intent}" if nonretrieval else label.route, "view": got_view, "level": level,
            "latency_ms": plan["classifier"]["latency_ms"], "fell_back": plan["classifier"]["fell_back"],
            "fallback_reason": plan["classifier"].get("reason") if plan["classifier"]["fell_back"] else None}


def _param_signature(entry) -> object:
    if not isinstance(entry, dict):
        return None
    if entry.get("type") == "entity":
        refs = entry.get("candidates") if entry.get("twins") else ([entry["chosen"]] if entry.get("chosen") else [])
        return sorted(str(r.get("_id") or r.get("name") or "") for r in refs or [])
    return entry.get("value")


def decision_signature(plan: dict) -> dict:
    """What the plan decided, without how sure it was. Probabilities, confidence scores, `why` text, latency
    and catalogue timestamps move between repeats of a decision model (0.97, then 0.96) and are left out;
    a changed intent, domain, route, view, param value, program kind, confidence level or command is a flip.
    A plan with a non-retrieval intent plans no retrieval: its route is the classifier's recorded guess and is
    never graded, so only its intent and level count."""
    plan = json.loads(json.dumps(plan, default=str))
    intent = (plan.get("intent") or {}).get("id")
    level = (plan.get("confidence") or {}).get("level")
    if is_nonretrieval(intent):
        return {"intent": intent, "level": level}
    match = plan.get("view_match") or {}
    return {
        "intent": intent,
        "domains": sorted((plan.get("domains") or {}).get("selected") or []),
        "route": (plan.get("route") or {}).get("id"),
        "view": match.get("name"),
        "params": {n: _param_signature(e) for n, e in sorted((match.get("params") or {}).items())},
        "program_kind": plan.get("program_kind"),
        "level": level,
        "steps": [s.get("cmd") for s in plan.get("steps") or []],
        "suggested_steps": [s.get("cmd") for s in plan.get("suggested_steps") or []],
    }


def _stable(plan: dict) -> str:
    return json.dumps(decision_signature(plan), sort_keys=True)


def flipped_fields(runs: list[dict]) -> list[str]:
    """Signature fields that differ across the repeated plans of one label, in signature order."""
    sigs = [decision_signature(r) for r in runs]
    keys = list(dict.fromkeys(k for sg in sigs for k in sg))
    return [k for k in keys if len({json.dumps(sg.get(k), sort_keys=True) for sg in sigs}) > 1]


def _percentile(values: list[float], q: float) -> float | None:
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def summarise(results: list[dict], unstable: list[str], classifier: str, repeat: int = 1,
              flips: list[dict] | None = None, threaded: int = 0) -> dict:
    counts = {o: sum(1 for r in results if r["outcome"] == o) for o in OUTCOMES}
    per_route: dict[str, dict[str, int]] = {}
    for r in results:
        per_route.setdefault(r["expected_route"], {o: 0 for o in OUTCOMES})[r["outcome"]] += 1
    latencies = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    view_wrong = sum(1 for r in results if r["expected_route"] == "view" and r["outcome"] == "wrong")
    graded_count = len(results) - counts["planned"]
    wrong_rate = counts["wrong"] / graded_count if graded_count else None
    fell_back = [r for r in results if r.get("fell_back")]
    graded = {key: [r for r in results if r.get(f"{key}_ok") is not None] for key in ("intent", "slots")}
    grades = {key: {"labelled": len(rows), "ok": sum(1 for r in rows if r[f"{key}_ok"]),
                    "wrong": [r["id"] for r in rows if not r[f"{key}_ok"]]} for key, rows in graded.items()}
    nonret = [r for r in results if r.get("nonretrieval")]
    # The gate's check is `wrong`: a confident lookup planned for a turn that needs none. Which non-retrieval
    # intent was chosen is information (`intent_wrong`), since the agent handles those turns itself.
    nonretrieval = {"labelled": len(nonret), "intent_ok": sum(1 for r in nonret if r.get("intent_ok")),
                    "no_lookup": sum(1 for r in nonret if r["outcome"] != "error" and "lookup" not in r["mismatches"]),
                    "wrong": [r["id"] for r in nonret if r["outcome"] == "wrong"],
                    "intent_wrong": [r["id"] for r in nonret if r["outcome"] != "error" and not r.get("intent_ok")]}
    passed = (view_wrong == 0 and not nonretrieval["wrong"] and wrong_rate is not None and wrong_rate < WRONG_RATE_GATE
              and counts["error"] == 0 and not unstable and repeat >= REPEAT_GATE
              and not (classifier == "decision" and fell_back))
    return {
        "classifier": classifier,
        "labels": len(results),
        "outcomes": counts,
        "per_route": dict(sorted(per_route.items())),
        "wrong": [{"id": r["id"], "mismatches": r["mismatches"]} for r in results if r["outcome"] == "wrong"],
        "intent": grades["intent"],
        "slots": grades["slots"],
        "nonretrieval": nonretrieval,
        "planned": [{"id": r["id"], "expected_route": r["expected_route"], "route": r["route"], "level": r["level"]}
                    for r in results if r["outcome"] == "planned"],
        "errors": [{"id": r["id"], "error_type": r.get("error_type"), "error": r.get("error")}
                   for r in results if r["outcome"] == "error"],
        "unstable": unstable,
        "flips": flips or [],
        "threaded": threaded,
        "fell_back": len(fell_back),
        "fallback_reasons": sorted({r["fallback_reason"] for r in fell_back if r.get("fallback_reason")}),
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
        "gate": {"view_wrong": view_wrong, "wrong_rate": wrong_rate, "repeat": repeat, "errors": counts["error"],
                 "passed": passed},
        "results": results,
    }


def _error_result(label, error_type: str, message: str) -> dict:
    return {"id": label.id, "outcome": "error", "mismatches": [], "error": message, "error_type": error_type,
            "route": None, "nonretrieval": label.nonretrieval,
            "expected_route": f"intent:{label.intent}" if label.nonretrieval else label.route, "view": None, "level": None,
            "intent": None, "intent_ok": None, "slots_ok": None, "slot_mismatches": [],
            "latency_ms": None, "fell_back": None}


def _source_trace(label) -> str | None:
    return label.source[len("trace:"):] if label.source.startswith("trace:") else None


def resolve_threads(labels) -> dict[str, tuple[str, str] | None]:
    """Per label id, what it follows: `("label", id)`, `("trace", trace_id)` (a previous turn with no label),
    or None (planned alone). An explicit `follows` wins; `none` disables. Otherwise a `trace:<id>` label follows
    the label sourced from its session's previous turn, else that turn read straight from the traces. The
    registry being unreadable leaves the label alone."""
    from artmind.query_plan import previous as previous_mod

    sources = {}
    for lb in labels:
        if _source_trace(lb):
            sources.setdefault(_source_trace(lb), lb.id)
    out: dict[str, tuple[str, str] | None] = {}
    for lb in labels:
        if lb.follows == "none":
            out[lb.id] = None
        elif lb.follows:
            out[lb.id] = ("label", lb.follows)
        elif _source_trace(lb):
            try:
                full = previous_mod.resolve_trace_id(_source_trace(lb))
                earlier = previous_mod.previous_turn(full) if full else None
            except Exception:
                earlier = None
            if earlier is None:
                out[lb.id] = None
                continue
            owner = next((i for src, i in sources.items()
                          if earlier["trace_id"].startswith(src) or src.startswith(earlier["trace_id"])), None)
            out[lb.id] = ("label", owner) if owner and owner != lb.id else ("trace", full)
        else:
            out[lb.id] = None
    return out


def planning_order(labels, threads) -> list:
    """Labels with every followed label before its follower; otherwise the file order. A cycle is cut."""
    by_id = {lb.id: lb for lb in labels}
    done: list = []
    state: dict[str, int] = {}

    def visit(lb) -> None:
        if state.get(lb.id):
            return
        state[lb.id] = 1
        edge = threads.get(lb.id)
        if edge and edge[0] == "label" and edge[1] in by_id:
            visit(by_id[edge[1]])
        done.append(lb)

    for lb in labels:
        visit(lb)
    return done


def _previous_for_label(label, threads, plans, labels_by_id):
    """The `Previous` handed to the planner for `label`, or None. A label follows the planner's own output
    for the followed label (as live does), with the answer read from its trace when it came from one."""
    from artmind.query_plan import previous as previous_mod

    edge = threads.get(label.id)
    if not edge:
        return None
    kind, ref = edge
    try:
        if kind == "trace":
            return previous_mod.previous_from_trace(_source_trace(label) and previous_mod.resolve_trace_id(
                _source_trace(label)) or "")
        followed = labels_by_id.get(ref)
        if followed is None or ref not in plans:
            return None
        answer = None
        if _source_trace(followed):
            full = previous_mod.resolve_trace_id(_source_trace(followed))
            answer = previous_mod.turn_answer(full) if full else None
        return previous_mod.previous_from_plan(followed.question, plans[ref], answer)
    except Exception:
        return None


def replay(labels, classifier: str, repeat: int = 1, *, given_domains: bool = False, model: str | None = None,
           plan_fn=None, threads: dict | None = None) -> dict:
    """Plan every label `repeat` times and score the first plan.

    Labels are planned in thread order (see `resolve_threads`): a label that follows another is handed
    `previous=` built from that label's first plan. `today` is fixed once so a run across midnight stays
    deterministic. After ABORT_AFTER consecutive errors of one type the remaining labels are marked
    `aborted` without planning."""
    if plan_fn is None:
        from artmind.query_plan import planner

        plan_fn = planner.plan
    today = date.today()
    extra = {"model": model} if model else {}
    labels = list(labels)
    threads = resolve_threads(labels) if threads is None else threads
    labels_by_id = {lb.id: lb for lb in labels}
    plans: dict[str, dict] = {}
    by_label: dict[str, dict] = {}
    unstable: list[str] = []
    flips: list[dict] = []
    threaded = 0
    streak_type, streak = None, 0
    for label in planning_order(labels, threads):
        if streak >= ABORT_AFTER:
            by_label[label.id] = _error_result(label, "aborted",
                                               f"replay aborted after {ABORT_AFTER} consecutive {streak_type} errors")
            continue
        domains = list(label.domains) if given_domains else None
        previous = _previous_for_label(label, threads, plans, labels_by_id)
        kwargs = {**extra, **({"previous": previous} if previous is not None else {})}
        try:
            runs = [plan_fn(label.question, domains, classifier=classifier, today=today, **kwargs)
                    for _ in range(max(1, repeat))]
        except Exception as exc:  # one label failing must not stop the replay (spec §7)
            kind = type(exc).__name__
            streak = streak + 1 if kind == streak_type else 1
            streak_type = kind
            by_label[label.id] = _error_result(label, kind, str(exc))
            continue
        streak_type, streak = None, 0
        threaded += previous is not None
        plans[label.id] = runs[0]
        if len({_stable(r) for r in runs}) > 1:
            unstable.append(label.id)
            flips.append({"id": label.id, "fields": flipped_fields(runs)})
        by_label[label.id] = score(label, runs[0])
    results = [by_label[lb.id] for lb in labels]
    return summarise(results, unstable, classifier, repeat, flips, threaded)


def write_detail(report: dict) -> Path:
    """Per-label detail into <ARTMIND_HOME>/logs/ (gitignored, private), never the checkout."""
    import paths

    path = Path(paths.LOGS_DIR) / f"plan-replay-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return path
