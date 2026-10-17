"""transitions: moments when the anchor's life changed (spec §6.6).

Three sources:
- explicit transition verbs on the anchor's relations (joined, left, moved_to, ...);
- a change in the dominant target of one of the anchor's relations between periods;
- a sharp jump in a circle's share of mentions (from communities' share findings).

Events within `cluster_days` of each other form one transition; more
independent sources score higher.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict

from artmind.insights.detectors import Detector, RunContext, Skip
from artmind.insights.model import Finding
from artmind.insights.stats import days_between, period_of, period_start

TRANSITION_VERBS = frozenset({
    "JOINED", "LEFT", "QUIT", "STARTED", "RESUMED", "STOPPED", "MOVED_TO", "MOVED_FROM",
    "MARRIED_TO", "DIVORCED", "ENROLLED_IN", "GRADUATED_FROM", "RETIRED_FROM", "PROMOTED_TO",
})


def _plan(ctx: RunContext):
    if not ctx.anchors:
        return Skip("no anchor entity", carry=False)
    if not ctx.temporal():
        return Skip("no document dates (valid time mostly defaulted)")
    if not ctx.anchor_relations:
        return Skip("the anchor has no dated relations", carry=False)
    return ["anchor"]


def _classes(ctx: RunContext) -> set[str]:
    block = ctx.config.block("transitions") or {}
    return set(block.get("classes") or ctx.classes_of_kind("recurrent"))


def _verbs(ctx: RunContext) -> frozenset[str]:
    return TRANSITION_VERBS | set((ctx.config.block("transitions") or {}).get("extra_verbs", []))


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", label.lower())[:40]


def _explicit(ctx: RunContext, classes: set[str], verbs: frozenset[str]) -> list[dict]:
    return [
        {"date": r["days"][0], "kind": "explicit", "label": f"{r['rel'].lower()} {r['name']}", "_id": r["_id"]}
        for r in ctx.anchor_relations if r["rel"] in verbs and r["cls"] in classes and r["days"]
    ]


def _dominance(ctx: RunContext, classes: set[str], cfg: dict, verbs: frozenset[str]) -> list[dict]:
    min_days, min_share = cfg.get("dominance_min_days", 3), cfg.get("dominance_share", 0.4)
    margin, max_gap = cfg.get("dominance_margin", 1.5), cfg.get("max_gap_periods", 2)
    period, periods = ctx.config.period, ctx.scored_periods()
    by_rel: dict[str, list[dict]] = defaultdict(list)
    for r in ctx.anchor_relations:
        if r["cls"] in classes and r["days"] and r["rel"] not in verbs:
            by_rel[r["rel"]].append(r)
    events = []
    for rel, rows in sorted(by_rel.items()):
        rows = sorted(rows, key=lambda r: r["_id"])
        buckets = {r["_id"]: Counter(period_of(d, period) for d in r["days"]) for r in rows}
        dominant = []   # (period index, period, dominant target id)
        for i, p in enumerate(periods):
            counts = {k: b[p] for k, b in buckets.items()}
            total = sum(counts.values())
            if not total:
                continue
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            best, top = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0
            if top >= min_days and top / total >= min_share and top >= margin * second:
                dominant.append((i, p, best))
        names = {r["_id"]: r["name"] for r in rows}
        days_of = {r["_id"]: r["days"] for r in rows}
        for (i0, _p0, a), (i1, p1, b) in zip(dominant, dominant[1:]):
            if a != b and i1 - i0 - 1 <= max_gap:
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
        label = f.subjects[0]["name"] if f.subjects else f.title.split(": share")[0]
        typical = statistics.median(abs(d) for _, d in steps)
        for p1, d in steps:
            if abs(d) >= min_shift and abs(d) > 2 * typical:
                verb = "rose" if d > 0 else "fell"
                events.append({"date": period_start(p1), "kind": "circle",
                               "label": f"{label} {verb} by {abs(d):.0f} per 100 documents", "_id": None})
    return events


def _run(ctx: RunContext, _target: str) -> list[Finding]:
    cfg = ctx.config.detector("transitions")
    classes = _classes(ctx)
    verbs = _verbs(ctx)
    events = _explicit(ctx, classes, verbs) + _dominance(ctx, classes, cfg, verbs) + _circle_shifts(ctx, cfg)
    events.sort(key=lambda e: (e["date"], e["kind"], e["label"]))
    groups: list[list[dict]] = []
    for e in events:
        if groups and days_between(groups[-1][0]["date"], e["date"]) <= cfg.get("cluster_days", 90):
            groups[-1].append(e)
        else:
            groups.append([e])
    targets = {r["_id"]: r for r in ctx.anchor_relations}
    out = []
    for g in groups:
        kinds = sorted({e["kind"] for e in g})
        ids = list(dict.fromkeys(e["_id"] for e in g if e["_id"]))
        explicit = [e for e in g if e["kind"] == "explicit"]   # g is sorted by date
        key = f"transitions:{explicit[0]['date']}:{_slug(explicit[0]['label'])}" if explicit \
            else f"transitions:{g[0]['date']}:{g[0]['kind']}"
        out.append(Finding(
            key=key, detector="transitions", kind="corpus",
            score=round(min(1.0, 0.3 + 0.3 * (len(kinds) - 1) + 0.05 * min(len(g) - 1, 4)), 4),
            title=f"Change around {g[0]['date'][:7]}: " + "; ".join(e["label"] for e in g[:2]),
            subjects=[{"_id": i, "name": targets[i]["name"], "class": targets[i]["cls"]} for i in ids if i in targets],
            stats={"start": g[0]["date"], "end": g[-1]["date"], "kinds": kinds, "events": len(g),
                   "headline": len(kinds)},
            card={"type": "timeline", "events": g},
        ))
    return out


TRANSITIONS = Detector("transitions", "corpus", _plan, _run)
