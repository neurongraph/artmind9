"""outcome_lift: entities and occurrent types that co-occur with better or worse days (spec §6.5).

A day's polarity is the majority of the configured outcome class's positive vs
negative values observed that day; ties are neutral and left out. Each subject's
negative share on its polar days is compared with every other polar day by a
two-proportion z-score. Co-occurrence, never cause: every card says so.
"""

from __future__ import annotations

from collections import defaultdict

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import two_proportion_z

CAPTION = "Co-occurrence on the same day, not cause."


def _values(ctx: RunContext, block: dict) -> dict[str, str]:
    cls, prop = block["class"], block["property"]
    if prop == "type":
        return {e._id: e.type.strip().casefold() for e in ctx.entities if e.cls == cls and e.type and e.type.strip()}
    return {k: str(v).strip().casefold() for k, v in ctx.entity_props.get((cls, prop), {}).items() if v}


def day_polarity(ctx: RunContext, block: dict) -> dict[str, int]:
    """{day: +1 | -1} for days whose outcome values lean one way."""
    positive, negative = set(block["positive"]), set(block["negative"])
    values = _values(ctx, block)
    balance: dict[str, int] = defaultdict(int)
    for e in ctx.entities:
        if e.cls != block["class"] or e._id not in values:
            continue
        sign = 1 if values[e._id] in positive else -1 if values[e._id] in negative else 0
        if sign:
            for d in e.days:
                balance[d] += sign
    return {d: (1 if s > 0 else -1) for d, s in sorted(balance.items()) if s != 0}


def _plan(ctx: RunContext):
    block = ctx.config.block("outcome")
    if block is None:
        return Skip("not configured")
    if block["class"] not in ctx.profile.classes:
        return Skip(f"outcome class {block['class']} is not in the graph")
    if not ctx.temporal():
        return Skip("no document dates (valid time mostly defaulted)")
    return ["all"]


def _subjects(ctx: RunContext, outcome_cls: str):
    for cls in ctx.classes_of_kind("recurrent"):
        if cls == outcome_cls:
            continue
        for e in ctx.members(cls):
            yield f"{cls}:{e._id}", e.name, [subject(e)], set(e.days), cls
    for cls in ctx.classes_of_kind("occurrent"):
        if cls == outcome_cls:
            continue
        by_type: dict[str, set[str]] = defaultdict(set)
        for e in ctx.entities:
            if e.cls == cls and e.type and e.type.strip() and e._id not in ctx.anchors:
                by_type[e.type.strip().lower()].update(e.days)
        for t in sorted(by_type):
            yield f"{cls}:type:{t}", f"{t} ({cls})", [], by_type[t], cls


def _run(ctx: RunContext, _target: str) -> list[Finding]:
    block = ctx.config.block("outcome")
    cfg = ctx.config.detector("outcome_lift")
    min_support, min_z = cfg.get("min_support", 10), cfg.get("min_z", 2.0)
    mood = day_polarity(ctx, block)
    total = len(mood)
    total_negative = sum(1 for m in mood.values() if m < 0)
    if total == 0:
        return []
    baseline = total_negative / total
    out = []
    for suffix, name, subjects, days, cls in _subjects(ctx, block["class"]):
        hits = [mood[d] for d in days if d in mood]
        n1 = len(hits)
        if n1 < min_support:
            continue
        x1 = sum(1 for m in hits if m < 0)
        z = two_proportion_z(x1, n1, total_negative - x1, total - n1)
        if z is None or abs(z) < min_z:
            continue
        share = x1 / n1
        worse = z > 0
        out.append(Finding(
            key=f"outcome_lift:{suffix}", detector="outcome_lift", kind="corpus",
            score=round(min(1.0, abs(z) / 5), 4),
            title=f"{'Worse' if worse else 'Better'} days with {name}", subjects=subjects,
            stats={"direction": "worse" if worse else "better", "negative_share": round(share, 3),
                   "baseline_negative_share": round(baseline, 3), "lift": round(share - baseline, 3),
                   "z": round(z, 3), "support": n1, "class": cls, "headline": round(share - baseline, 3)},
            card={"type": "pairs", "subject": name, "share": round(share, 3), "baseline": round(baseline, 3),
                  "lift": round(share - baseline, 3), "caption": CAPTION},
        ))
    return out


OUTCOME_LIFT = Detector("outcome_lift", "corpus", _plan, _run)
