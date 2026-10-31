"""Retrieval metrics (spec 2026-10-31 §6.2). Pure functions over ranked ids."""
from __future__ import annotations

from collections.abc import Iterable, Sequence


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    return 1.0 if any(r in relevant for r in ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float:
    for i, r in enumerate(ranked, start=1):
        if r in relevant:
            return 1.0 / i
    return 0.0


def docs_covered_at_k(ranked_docs: Sequence[str], relevant_docs: set[str], k: int) -> float:
    if not relevant_docs:
        return 0.0
    return len(set(ranked_docs[:k]) & relevant_docs) / len(relevant_docs)


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(sum(values) / len(values), 4) if values else 0.0


def summarise(per_question: list[dict], ks: Sequence[int]) -> dict:
    out: dict = {}
    needles = [q for q in per_question if q["kind"] == "needle"]
    surveys = [q for q in per_question if q["kind"] == "survey"]
    if needles:
        s: dict = {"questions": len(needles)}
        for k in ks:
            s[f"recall@{k}"] = _mean(q["recall"][str(k)] for q in needles)
        s["mrr"] = _mean(q["rr"] for q in needles)
        s["latency_s"] = _mean(q["latency_s"] for q in needles)
        out["needle"] = s
    if surveys:
        s = {"questions": len(surveys)}
        for k in ks:
            s[f"docs_covered@{k}"] = _mean(q["docs_covered"][str(k)] for q in surveys)
        s["latency_s"] = _mean(q["latency_s"] for q in surveys)
        out["survey"] = s
    return out
