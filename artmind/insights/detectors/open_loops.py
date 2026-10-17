"""open_loops: intentions with no follow-up (spec §6.7).

An entity of the configured class whose status is an `open` value and whose
last evidence day is more than `stale_days` before as_of. Statuses that are
neither open nor closed are reported once as a health finding: the config, or
the schema hint, is missing values.
"""

from __future__ import annotations

from collections import Counter

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import days_between

URGENT = frozenset({"high", "urgent", "immediate", "critical"})


def _plan(ctx: RunContext):
    block = ctx.config.block("open_loops")
    if block is None:
        return Skip("not configured", carry=False)
    if block["class"] not in ctx.profile.classes:
        return Skip(f"open_loops class {block['class']} is not in the graph", carry=False)
    if ctx.as_of is None:
        return Skip("no document dates")
    return [block["class"]]


def _normalised(ctx: RunContext, cls: str, prop: str) -> dict[str, str]:
    return {k: str(v).strip().casefold() for k, v in ctx.entity_props.get((cls, prop), {}).items() if v}


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    block = ctx.config.block("open_loops")
    stale = ctx.config.detector("open_loops").get("stale_days", 60)
    status = _normalised(ctx, cls, block["property"])
    urgency = _normalised(ctx, cls, block["urgency_property"])
    open_values, closed_values = set(block["open"]), set(block["closed"])
    out, unclassified = [], Counter()
    for e in ctx.entities:
        if e.cls != cls or not e.days or e._id not in status:
            continue
        s = status[e._id]
        if s not in open_values:
            if s not in closed_values:
                unclassified[s] += 1
            continue
        age = days_between(e.days[-1], ctx.as_of)
        if age <= stale:
            continue
        weight = 1.0 if urgency.get(e._id) in URGENT else 0.8
        out.append(Finding(
            key=f"open_loops:{cls}:{e._id}", detector="open_loops", kind="corpus",
            score=round(min(1.0, age / (4 * stale)) * weight, 4), title=f"Still open: {e.name}",
            subjects=[subject(e)],
            stats={"status": s, "last_seen": e.days[-1], "age_days": age, "urgency": urgency.get(e._id),
                   "headline": age},
            card={"type": "ranking", "columns": ["status", "last_seen", "age_days"]},
        ))
    if unclassified:
        n = sum(unclassified.values())
        out.append(Finding(
            key=f"open_loops:{cls}:unclassified", detector="open_loops", kind="health",
            score=round(min(1.0, n / 50), 4),
            title=f"{n} {cls}.{block['property']} values are neither open nor closed",
            stats={"count": n, "distinct": len(unclassified),
                   "top": dict(sorted(unclassified.items(), key=lambda kv: (-kv[1], kv[0]))[:20]), "headline": n},
            card={"type": "stat"}, action="config",
        ))
    return out


OPEN_LOOPS = Detector("open_loops", "corpus", _plan, _run)
