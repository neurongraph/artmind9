"""Ranking for vector-text (spec 2026-10-31 §4.2 steps 3-5). Pure Python.

`passage_fuse`: weighted Reciprocal Rank Fusion per passage (and per chat) over the vector
and fulltext rankings, then MaxP -- a chunk scores its best passage's fused score.
`mmr_select`: maximal marginal relevance over the fused units, with a
same-document redundancy floor so one document cannot fill the top K.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

RRF_K = 60
# Per-leg RRF weight. BM25 over long natural-language questions matches generic
# words, so an equal vote let keyword noise outrank vector hits; 2:1 restored
# needle recall@5 0.62 -> 0.72+ (banking_corpus retrieval eval, 2026-11-01).
LEG_WEIGHTS = {"vector": 2.0, "fulltext": 1.0}
SAME_DOC_SIMILARITY = 0.85
_LEG_ORDER = ("vector", "fulltext")


@dataclass
class Unit:
    key: str                     # "chunk:<id>" | "chat:<id>"
    kind: str                    # "chunk" | "chat"
    score: float                 # fused RRF score (MaxP over passages for a chunk)
    relevance: float | None      # best cosine among its passages; None if only fulltext found it
    matched_by: list[str]
    best: dict                   # best passage row (chunk) or the chat row
    doc_id: str | None
    vector: list[float] | None   # vector of the best passage when the vector leg returned one
    passages_matched: int = 1


def _legs(legs) -> list[str]:
    return [leg for leg in _LEG_ORDER if leg in legs]


def passage_fuse(vector_rows: list[dict], text_rows: list[dict], k: int = RRF_K) -> list[Unit]:
    per: dict[tuple[str, str], dict] = {}
    for leg, rows in (("vector", vector_rows), ("fulltext", text_rows)):
        for rank, row in enumerate(rows, start=1):
            e = per.setdefault((row["kind"], row["id"]),
                               {"row": row, "score": 0.0, "legs": set(), "cosine": None, "vector": None})
            e["score"] += LEG_WEIGHTS[leg] / (k + rank)
            e["legs"].add(leg)
            if row.get("cosine") is not None:
                e["cosine"] = row["cosine"]
            if row.get("embedding"):
                e["vector"] = row["embedding"]
    units: dict[str, Unit] = {}
    for (kind, rid), e in per.items():
        if kind == "chat":
            key = f"chat:{rid}"
            units[key] = Unit(key, "chat", e["score"], e["cosine"], _legs(e["legs"]), e["row"], None, e["vector"])
            continue
        key = f"chunk:{e['row']['chunk_id']}"
        u = units.get(key)
        if u is None:
            units[key] = Unit(key, "chunk", e["score"], e["cosine"], _legs(e["legs"]), e["row"],
                              e["row"].get("doc_id"), e["vector"])
            continue
        u.passages_matched += 1
        u.matched_by = _legs(set(u.matched_by) | e["legs"])
        if e["cosine"] is not None and (u.relevance is None or e["cosine"] > u.relevance):
            u.relevance = e["cosine"]
        if e["score"] > u.score:
            u.score, u.best = e["score"], e["row"]
            u.vector = e["vector"] or u.vector
    return sorted(units.values(), key=lambda u: (-u.score, u.key))


def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _similarity(a: Unit, b: Unit) -> float:
    sim = _cosine(a.vector, b.vector)
    if a.doc_id and a.doc_id == b.doc_id:
        sim = max(sim, SAME_DOC_SIMILARITY)
    return sim


def mmr_select(units: list[Unit], top_k: int, diversity: float) -> list[Unit]:
    """Pick `top_k` from the best `3 * top_k`: at each step the unit maximising
    (1 - d) * rel - d * redundancy. d = 0 is plain relevance order."""
    ordered = sorted(units, key=lambda u: (-u.score, u.key))
    if diversity <= 0 or len(ordered) <= 1:
        return ordered[:top_k]
    pool = ordered[: 3 * top_k]
    top = pool[0].score or 1.0
    picked: list[Unit] = []
    rest = list(pool)
    while rest and len(picked) < top_k:
        def value(u: Unit) -> float:
            redundancy = max((_similarity(u, p) for p in picked), default=0.0)
            return (1 - diversity) * (u.score / top) - diversity * redundancy
        best = min(rest, key=lambda u: (-value(u), -u.score, u.key))
        picked.append(best)
        rest.remove(best)
    return picked
