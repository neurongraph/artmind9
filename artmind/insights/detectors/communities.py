"""communities: circles in the co-mention graph, their share over time, and cohesion (spec §6.3).

Leaves `ctx.memo[("circles", cls)] = (graph, circles)` for `brokers`. A circle's
key is built from its lead member's _id, so it is stable while the lead holds.
diff.py re-keys circles whose lead changed by member Jaccard.
"""

from __future__ import annotations

from collections import Counter

from artmind.insights import graphs
from artmind.insights.detectors import Detector, RunContext, Skip
from artmind.insights.model import Finding
from artmind.insights.stats import period_of, rate_per_100, theil_sen_slope

NETWORK_CARD_NODES = 60


def _classes(ctx: RunContext) -> list[str]:
    wanted = ctx.config.detector("communities").get("classes")
    if wanted:
        return [c for c in wanted if c in ctx.profile.classes]
    recurrent = ctx.classes_of_kind("recurrent")
    if "PERSON" in recurrent:
        return ["PERSON"]
    return sorted(recurrent, key=lambda c: (-len(ctx.members(c)), c))[:1]


def _plan(ctx: RunContext):
    classes = [c for c in _classes(ctx) if len(ctx.members(c)) >= 3]
    return classes or Skip("no class with >= 3 entities above the evidence floor")


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    cfg = ctx.config.detector("communities")
    min_weight, resolution = cfg.get("min_weight", 2), cfg.get("resolution", 1.0)
    members = ctx.members(cls)
    try:
        g = graphs.co_mention_graph(members, min_weight)
    except graphs.GraphTooLarge as exc:
        return [Finding(
            key=f"communities:{cls}:too_large", detector="communities", kind="health", score=1.0,
            title=f"{cls} co-mention graph is too large to analyse", stats={"reason": str(exc), "headline": 0},
            card={"type": "stat"}, action="config",
        )]
    comms = [c for c in graphs.louvain(g, resolution) if len(c) >= cfg.get("min_circle_size", 3)]
    if not comms:
        return []
    wdeg = graphs.weighted_degree(g)
    total = sum(wdeg.values()) or 1.0
    circles, out = [], []
    for c in comms:
        ld = graphs.lead(c, wdeg)
        ordered = sorted(c, key=lambda n: (-wdeg[n], n))
        name = f"Circle around {g.nodes[ld]['name']}"
        circles.append({"id": f"{cls}:{ld}", "class": cls, "lead": ld, "name": name,
                        "name_source": "template", "members": ordered})
        top = ordered[:NETWORK_CARD_NODES]
        sub = g.subgraph(top)
        out.append(Finding(
            key=f"communities:{cls}:circle:{ld}", detector="communities", kind="corpus",
            score=round(min(1.0, sum(wdeg[n] for n in c) / total), 4),
            title=f"{name} ({len(c)})",
            subjects=[{"_id": n, "name": g.nodes[n]["name"], "class": cls} for n in ordered[:10]],
            stats={"members": ordered, "size": len(c), "headline": len(c)},
            card={"type": "network",
                  "truncated": len(c) > NETWORK_CARD_NODES,
                  "nodes": [{"_id": n, "name": g.nodes[n]["name"], "weight": wdeg[n]} for n in top],
                  "edges": [{"source": a, "target": b, "weight": d["weight"]} for a, b, d in sorted(sub.edges(data=True))]},
        ))
    ctx.memo[("circles", cls)] = (g, circles)
    if ctx.temporal() and len(ctx.scored_periods()) >= 2:
        out += _shares(ctx, cls, members, circles)
        out += _cohesion(ctx, cls, members, cfg)
    return out


def _shares(ctx: RunContext, cls: str, members, circles) -> list[Finding]:
    periods, docs = ctx.scored_periods(), ctx.profile.docs_per_period
    by_id = {e._id: e for e in members}
    out = []
    for c in circles:
        counts = Counter(period_of(d, ctx.config.period) for n in c["members"] for d in by_id[n].days)
        rates = [round(rate_per_100(counts.get(p, 0), docs[p]), 3) for p in periods]
        shift = max(abs(b - a) for a, b in zip(rates, rates[1:]))
        first, last = rates[0], rates[-1]
        out.append(Finding(
            key=f"communities:{cls}:share:{c['lead']}", detector="communities", kind="corpus",
            score=round(min(1.0, abs(last - first) / max(first, last, 1.0)), 4),
            title=f"{c['name']}: share of mentions",
            subjects=[{"_id": c["lead"], "name": by_id[c["lead"]].name, "class": cls}],
            stats={"members": c["members"], "rates": dict(zip(periods, rates)), "max_shift": round(shift, 3), "headline": last},
            card={"type": "series", "periods": periods, "values": rates, "unit": "mention-days per 100 documents"},
        ))
    return out


def _cohesion(ctx: RunContext, cls: str, members, cfg: dict) -> list[Finding]:
    min_weight, resolution = cfg.get("min_weight", 2), cfg.get("resolution", 1.0)
    min_edges = cfg.get("min_period_edges", 3)
    periods, values = [], []
    for p in ctx.scored_periods():
        try:
            gp = graphs.co_mention_graph(graphs.restrict(members, p, ctx.config.period), min_weight)
        except graphs.GraphTooLarge:
            continue
        if gp.number_of_edges() < min_edges:
            continue
        periods.append(p)
        values.append(round(graphs.modularity(gp, graphs.louvain(gp, resolution)), 4))
    if len(periods) < 2:
        return []
    slope = theil_sen_slope(values)
    return [Finding(
        key=f"communities:{cls}:cohesion", detector="communities", kind="corpus",
        score=round(min(1.0, abs(slope) * 10), 4),
        title=f"{cls} circles are {'merging' if slope < 0 else 'separating'}",
        stats={"modularity": dict(zip(periods, values)), "slope": round(slope, 4), "headline": values[-1]},
        card={"type": "series", "periods": periods, "values": values, "unit": "modularity"},
    )]


COMMUNITIES = Detector("communities", "corpus", _plan, _run)
