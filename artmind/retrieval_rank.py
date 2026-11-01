"""Ranking for vector-text (spec 2026-10-31 §4.2 steps 3-4). Pure Python.

`passage_fuse`: weighted Reciprocal Rank Fusion per passage (and per chat) over the vector
and fulltext rankings, then MaxP -- a chunk scores its best passage's fused score.

`fuse_ids`, `first_per_doc`, `survey_fuse`: the document rankings of `query survey`
(spec 2026-11-01 §2.2).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

RRF_K = 60
# Per-leg RRF weight. BM25 over long natural-language questions matches generic
# words, so an equal vote let keyword noise outrank vector hits; 2:1 restored
# needle recall@5 0.62 -> 0.72+ (banking_corpus retrieval eval, 2026-11-01).
LEG_WEIGHTS = {"vector": 2.0, "fulltext": 1.0}
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
    passages_matched: int = 1


def _legs(legs) -> list[str]:
    return [leg for leg in _LEG_ORDER if leg in legs]


def passage_fuse(vector_rows: list[dict], text_rows: list[dict], k: int = RRF_K) -> list[Unit]:
    per: dict[tuple[str, str], dict] = {}
    for leg, rows in (("vector", vector_rows), ("fulltext", text_rows)):
        for rank, row in enumerate(rows, start=1):
            e = per.setdefault((row["kind"], row["id"]),
                               {"row": row, "score": 0.0, "legs": set(), "cosine": None})
            e["score"] += LEG_WEIGHTS[leg] / (k + rank)
            e["legs"].add(leg)
            if row.get("cosine") is not None:
                e["cosine"] = row["cosine"]
    units: dict[str, Unit] = {}
    for (kind, rid), e in per.items():
        if kind == "chat":
            key = f"chat:{rid}"
            units[key] = Unit(key, "chat", e["score"], e["cosine"], _legs(e["legs"]), e["row"], None)
            continue
        key = f"chunk:{e['row']['chunk_id']}"
        u = units.get(key)
        if u is None:
            units[key] = Unit(key, "chunk", e["score"], e["cosine"], _legs(e["legs"]), e["row"],
                              e["row"].get("doc_id"))
            continue
        u.passages_matched += 1
        u.matched_by = _legs(set(u.matched_by) | e["legs"])
        if e["cosine"] is not None and (u.relevance is None or e["cosine"] > u.relevance):
            u.relevance = e["cosine"]
        if e["score"] > u.score:
            u.score, u.best = e["score"], e["row"]
    return sorted(units.values(), key=lambda u: (-u.score, u.key))


#: `query survey` leg weights (spec 2026-11-01 §2.2): D document summaries,
#: C chunk summaries, P passages. Equal until the eval says otherwise.
SURVEY_LEG_WEIGHTS = {"D": 1.0, "C": 1.0, "P": 1.0}

_SURVEY_LEGS = frozenset(SURVEY_LEG_WEIGHTS)


def survey_leg_weights(env: dict | None = None) -> dict[str, float]:
    """Return the per-leg weights for `survey_fuse`, resolved from the environment.

    ``ARTMIND_SURVEY_LEG_WEIGHTS`` accepts a comma-separated ``LEG=VALUE`` list
    (e.g. ``D=2,C=1,P=1``).  Any subset of D, C, P may be specified; missing
    legs keep the default weight of 1.  All weights must be non-negative and at
    least one must be > 0.

    Raises ``ValueError`` with a message naming the variable on invalid input.
    """
    if env is None:
        env = os.environ
    raw = env.get("ARTMIND_SURVEY_LEG_WEIGHTS")
    weights = dict(SURVEY_LEG_WEIGHTS)
    if not raw:
        return weights
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError(
                f"ARTMIND_SURVEY_LEG_WEIGHTS: {token!r} is not in LEG=VALUE form "
                f"(expected e.g. D=1,C=1,P=1)"
            )
        leg, val = token.split("=", 1)
        leg = leg.strip().upper()
        if leg not in _SURVEY_LEGS:
            raise ValueError(
                f"ARTMIND_SURVEY_LEG_WEIGHTS: unknown leg {leg!r}; must be one of D, C, P"
            )
        try:
            value = float(val.strip())
        except ValueError:
            raise ValueError(
                f"ARTMIND_SURVEY_LEG_WEIGHTS: {val.strip()!r} is not a valid number for leg {leg}"
            )
        if value < 0:
            raise ValueError(
                f"ARTMIND_SURVEY_LEG_WEIGHTS: weight for {leg} must be non-negative, got {value}"
            )
        weights[leg] = value
    if not any(v > 0 for v in weights.values()):
        raise ValueError(
            "ARTMIND_SURVEY_LEG_WEIGHTS: at least one weight must be > 0"
        )
    return weights


def fuse_ids(vector_rows: list[dict], text_rows: list[dict], k: int = RRF_K) -> list[dict]:
    """Weighted RRF per row `id` over the vector and fulltext legs (2:1, as passages).
    Returns [{"row", "score", "cosine", "matched_by"}], best first, ties by id."""
    per: dict[str, dict] = {}
    for leg, rows in (("vector", vector_rows), ("fulltext", text_rows)):
        for rank, row in enumerate(rows, start=1):
            e = per.setdefault(row["id"], {"row": row, "score": 0.0, "legs": set(), "cosine": None})
            e["score"] += LEG_WEIGHTS[leg] / (k + rank)
            e["legs"].add(leg)
            if row.get("cosine") is not None:
                e["cosine"] = row["cosine"]
    out = [{"row": e["row"], "score": e["score"], "cosine": e["cosine"], "matched_by": _legs(e["legs"])}
           for e in per.values()]
    return sorted(out, key=lambda e: (-e["score"], e["row"]["id"]))


def first_per_doc(items: list[dict]) -> list[str]:
    """Document ids in the order of their best item (MaxP over a best-first list)."""
    order: list[str] = []
    seen: set[str] = set()
    for item in items:
        doc_id = item["row"].get("doc_id")
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            order.append(doc_id)
    return order


def survey_fuse(rankings: dict[str, list[str]], weights: dict[str, float] | None = None,
                k: int = RRF_K) -> list[tuple[str, float, dict[str, int]]]:
    """RRF over per-leg document rankings: [(doc_id, score, {leg: rank})], best first, ties by id."""
    weights = weights or SURVEY_LEG_WEIGHTS
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for leg, order in rankings.items():
        for rank, doc_id in enumerate(order, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weights.get(leg, 1.0) / (k + rank)
            ranks.setdefault(doc_id, {})[leg] = rank
    return sorted(((d, s, ranks[d]) for d, s in scores.items()), key=lambda t: (-t[1], t[0]))
