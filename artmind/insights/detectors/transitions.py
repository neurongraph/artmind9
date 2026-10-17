"""transitions: moments when the anchor's life changed (spec §6.6).

Three sources:
- explicit transition verbs on the anchor's relations (joined, left, moved_to, ...);
- a change in the dominant target of one of the anchor's relations between periods;
- a sharp jump in a circle's share of mentions (from communities' share findings).

Events within `cluster_days` of each other form one transition; more
independent sources score higher.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from artmind.insights.detectors import Detector, RunContext, Skip
from artmind.insights.model import Finding
from artmind.insights.stats import days_between, period_of, period_start

TRANSITION_VERBS = frozenset({
    "JOINED", "LEFT", "QUIT", "STARTED", "RESUMED", "STOPPED", "MOVED_TO", "MOVED_FROM",
    "MARRIED_TO", "DIVORCED", "ENROLLED_IN", "GRADUATED_FROM", "RETIRED_FROM", "PROMOTED_TO",
})


def _plan(ctx: RunContext):
    if not ctx.anchors:
        return Skip("no anchor entity")
    if not ctx.temporal():
        return Skip("no document dates (valid time mostly defaulted)")
    if not ctx.anchor_relations:
        return Skip("the anchor has no dated relations")
    return ["anchor"]


def _classes(ctx: RunContext) -> set[str]:
    block = ctx.config.block("transitions") or {}
    return set(block.get("classes") or ctx.classes_of_kind("recurrent"))


def _explicit(ctx: RunContext, classes: set[str]) -> list[dict]:
    verbs = TRANSITION_VERBS | set((ctx.config.block("transitions") or {}).get("extra_verbs", []))
    return [
        {"date": r["days"][0], "kind": "explicit", "label": f"{r['rel'].lower()} {r['name']}", "_id": r["_id"]}
        for r in ctx.anchor_relations if r["rel"] in verbs and r["cls"] in classes and r["days"]
    ]


def _dominance(ctx: RunContext, classes: set[str], cfg: dict) -> list[dict]:
    min_days, min_share = cfg.get("dominance_min_days", 3), cfg.get("dominance_share", 0.4)
    period, periods = ctx.config.period, ctx.scored_periods()
    by_rel: dict[str, list[dict]] = defaultdict(list)
    for r in ctx.anchor_relations:
        if r["cls"] in classes and r["days"] and r["rel"] not in TRANSITION_VERBS:
            by_rel[r["rel"]].append(r)
    events = []
    for rel, rows in sorted(by_rel.items()):
        rows = sorted(rows, key=lambda r: r["_id"])
        dominant = []
        for p in periods:
            counts = {r["_id"]: sum(1 for d in r["days"] if period_of(d, period) == p) for r in rows}
            total = sum(counts.values())
            best = max(counts, key=lambda k: counts[k]) if counts else None  # first max in _id order
            ok = best is not None and counts[best] >= min_days and counts[best] / total >= min_share
            dominant.append((p, best if ok else None))
        names = {r["_id"]: r["name"] for r in rows}
        days_of = {r["_id"]: r["days"] for r in rows}
        for (_p0, a), (p1, b) in zip(dominant, dominant[1:]):
            if a and b and a != b:
                first = min(d for d in days_of[b] if period_of(d, period) == p1)
                events.append({"date": first, "kind": "dominance",
                               "label": f"{rel.lower()}: {names[a]} -> {names[b]}", "_id": b})
    return events


def _circle_shifts(ctx: RunContext, cfg: dict) -> list[dict]:
    min_shift = cfg.get("circle_min_shift", 10)
    events = []
    for f in ctx.memo.get("findings", []):
        if f.detector != "communities" or ":share:" not in f.key:
            continue
        rates = sorted(f.stats.get("rates", {}).items())
        steps = [(p1, b - a) for (_p0, a), (p1, b) in zip(rates, rates[1:])]
        if len(steps) < 2:
            continue
        typical = statistics.median(abs(d) for _, d in steps)
        for p1, d in steps:
            if abs(d) >= min_shift and abs(d) > 2 * typical:
                verb = "rose" if d > 0 else "fell"
                events.append({"date": period_start(p1), "kind": "circle",
                               "label": f"{f.title.split(':')[0]} {verb} by {abs(d):.0f} per 100 documents", "_id": None})
    return events


def _run(ctx: RunContext, _target: str) -> list[Finding]:
    cfg = ctx.config.detector("transitions")
    classes = _classes(ctx)
    events = _explicit(ctx, classes) + _dominance(ctx, classes, cfg) + _circle_shifts(ctx, cfg)
    events.sort(key=lambda e: (e["date"], e["kind"], e["label"]))
    groups: list[list[dict]] = []
    for e in events:
        if groups and days_between(groups[-1][-1]["date"], e["date"]) <= cfg.get("cluster_days", 90):
            groups[-1].append(e)
        else:
            groups.append([e])
    targets = {r["_id"]: r for r in ctx.anchor_relations}
    out = []
    for g in groups:
        kinds = sorted({e["kind"] for e in g})
        ids = list(dict.fromkeys(e["_id"] for e in g if e["_id"]))
        out.append(Finding(
            key=f"transitions:{g[0]['date']}", detector="transitions", kind="corpus",
            score=round(min(1.0, 0.3 + 0.3 * (len(kinds) - 1) + 0.05 * (len(g) - 1)), 4),
            title=f"Change around {g[0]['date'][:7]}: " + "; ".join(e["label"] for e in g[:2]),
            subjects=[{"_id": i, "name": targets[i]["name"], "class": targets[i]["cls"]} for i in ids if i in targets],
            stats={"start": g[0]["date"], "end": g[-1]["date"], "kinds": kinds, "events": len(g),
                   "headline": len(kinds)},
            card={"type": "timeline", "events": g},
        ))
    return out


TRANSITIONS = Detector("transitions", "corpus", _plan, _run)
