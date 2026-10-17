"""networkx helpers for insights (spec §6.3-§6.4, §8). Pure-Python algorithms only:
no numpy/scipy. Inputs are inserted in sorted order and Louvain is seeded, so
the same rows always give the same communities."""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import replace

import networkx as nx
from networkx.algorithms import community as nx_comm

from artmind.insights.model import InsightsError
from artmind.insights.profile import EntityRow
from artmind.insights.stats import period_of

MAX_NODES = 5000
MAX_EDGES = 200_000
MAX_PAIRS = 2_000_000
SEED = 7


class GraphTooLarge(InsightsError):
    pass


def co_mention_graph(members: list[EntityRow], min_weight: int = 2) -> nx.Graph:
    """Entities linked by the number of evidence days they share."""
    # An entity with fewer evidence days than min_weight can never reach min_weight with anyone.
    ordered = sorted((e for e in members if len(e.days) >= min_weight), key=lambda e: e._id)
    by_day: dict[str, list[str]] = defaultdict(list)
    for e in ordered:
        for d in e.days:
            by_day[d].append(e._id)
    total = sum(len(ids) * (len(ids) - 1) // 2 for ids in by_day.values())
    if total > MAX_PAIRS:
        raise GraphTooLarge(
            f"co-mention graph needs {total} pair counts (cap {MAX_PAIRS}); raise min_evidence_days"
        )
    weights: Counter = Counter()
    for d in sorted(by_day):
        weights.update(itertools.combinations(by_day[d], 2))
    kept = sorted((pair, w) for pair, w in weights.items() if w >= min_weight)
    nodes = sorted({n for (a, b), _ in kept for n in (a, b)})
    if len(nodes) > MAX_NODES or len(kept) > MAX_EDGES:
        raise GraphTooLarge(
            f"co-mention graph has {len(nodes)} nodes / {len(kept)} edges "
            f"(caps {MAX_NODES} / {MAX_EDGES}); raise min_evidence_days"
        )
    names = {e._id: e.name for e in ordered}
    g = nx.Graph()
    for n in nodes:
        g.add_node(n, name=names[n])
    for (a, b), w in kept:
        g.add_edge(a, b, weight=w, distance=1.0 / w)
    return g


def louvain(g: nx.Graph, resolution: float = 1.0) -> list[frozenset[str]]:
    if g.number_of_edges() == 0:
        return []
    comms = nx_comm.louvain_communities(g, weight="weight", resolution=resolution, seed=SEED)
    return sorted((frozenset(c) for c in comms), key=lambda c: (-len(c), min(c)))


def modularity(g: nx.Graph, comms: list[frozenset[str]]) -> float:
    if not comms or g.number_of_edges() == 0:
        return 0.0
    return float(nx_comm.modularity(g, comms, weight="weight"))


def weighted_degree(g: nx.Graph) -> dict[str, float]:
    return {n: float(d) for n, d in g.degree(weight="weight")}


def lead(members: Iterable[str], wdeg: dict[str, float]) -> str:
    return sorted(members, key=lambda n: (-wdeg.get(n, 0.0), n))[0]


def jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def restrict(members: list[EntityRow], keep: str, period: str) -> list[EntityRow]:
    out = []
    for e in members:
        days = tuple(d for d in e.days if period_of(d, period) == keep)
        if days:
            out.append(replace(e, days=days, docs=len(days)))
    return out
