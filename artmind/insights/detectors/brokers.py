"""brokers: entities whose neighbours span circles, ranked by betweenness (spec §6.4)."""

from __future__ import annotations

import networkx as nx

from artmind.insights.detectors import Detector, RunContext, Skip
from artmind.insights.model import Finding

EXACT_BETWEENNESS_MAX_NODES = 2000  # above this, sample k pivots (seeded)


def _plan(ctx: RunContext):
    classes = sorted(key[1] for key in ctx.memo if isinstance(key, tuple) and key[0] == "circles")
    return classes or Skip("communities did not run")


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    g, circles = ctx.memo[("circles", cls)]
    if len(circles) < 2:
        return []
    circle_of = {n: c["id"] for c in circles for n in c["members"]}
    names = {c["id"]: c["name"] for c in circles}
    k = None if g.number_of_nodes() <= EXACT_BETWEENNESS_MAX_NODES else 500
    bt = nx.betweenness_centrality(g, weight="distance", k=k, seed=7)
    candidates = []
    for n in sorted(g):
        spans = {circle_of[m] for m in g[n] if m in circle_of}
        if n in circle_of:
            spans.add(circle_of[n])
        if len(spans) >= 2 and bt[n] > 0:
            candidates.append((n, sorted(spans)))
    top = max((bt[n] for n, _ in candidates), default=0.0)
    out = []
    for n, spans in candidates:
        out.append(Finding(
            key=f"brokers:{cls}:{n}", detector="brokers", kind="corpus", score=round(bt[n] / top, 4),
            title=f"Connects {len(spans)} circles: {g.nodes[n]['name']}",
            subjects=[{"_id": n, "name": g.nodes[n]["name"], "class": cls}],
            stats={"betweenness": round(bt[n], 4), "circles": [names[s] for s in spans],
                   "degree": g.degree(n), "headline": round(bt[n], 4)},
            card={"type": "ranking", "columns": ["betweenness", "circles"]},
        ))
    return sorted(out, key=lambda f: (-f.score, f.key))


BROKERS = Detector("brokers", "corpus", _plan, _run)
