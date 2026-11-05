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


def _row_passage_chunk(row: dict) -> str | None:
    """The chunk id a row shows passage text from, or None when it shows none."""
    if not isinstance(row, dict):
        return None
    passage = row.get("passage")
    if isinstance(passage, dict) and passage.get("text") is not None:           # brief passage row
        return passage.get("chunk_id")
    if row.get("gist_source") == "passage":                                      # gist taken from the best passage
        return (row.get("best") or {}).get("chunk_id")
    return None


def visible_passage_chunks(payload: dict) -> tuple[set[str], dict[str, set[str]]]:
    """(chunk ids whose passage text the payload shows, doc id -> those chunk ids).

    Handles full and brief survey (`documents[]`), and full and brief `vector-text`
    (`rows[]`). Pure: reads the payload dict only.
    """
    chunks: set[str] = set()
    by_doc: dict[str, set[str]] = {}

    def add(doc_id, chunk_id):
        if chunk_id:
            chunks.add(chunk_id)
            if doc_id:
                by_doc.setdefault(doc_id, set()).add(chunk_id)

    for d in payload.get("documents") or []:
        doc_id = d.get("doc_id")
        for p in d.get("passages") or []:                                        # full survey
            if isinstance(p, dict) and p.get("text") is not None:
                add(doc_id, p.get("chunk_id"))
        add(doc_id, _row_passage_chunk(d))                                       # brief survey
    for r in payload.get("rows") or []:
        if not isinstance(r, dict):
            continue
        if isinstance(r.get("chunk"), dict):                                     # full vector-text
            add((r.get("document") or {}).get("id") or r["chunk"].get("doc_id"), r["chunk"].get("id"))
        elif r.get("chunk_id") and r.get("text") is not None:                    # brief passage row
            add((r.get("doc") or {}).get("id"), r.get("chunk_id"))
    return chunks, by_doc


def evidence_visible(payload: dict, kind: str, relevant: set[str]) -> float:
    """Spec 2026-11-05 §4.3. needle: `relevant` = chunk ids, 1.0 if a labelled chunk's
    passage text is shown. survey: `relevant` = doc ids, share whose row shows passage text."""
    if not relevant:
        return 0.0
    chunks, by_doc = visible_passage_chunks(payload or {})
    if kind == "needle":
        return 1.0 if chunks & relevant else 0.0
    return len([d for d in relevant if by_doc.get(d)]) / len(relevant)


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
        if all("evidence_visible" in q for q in needles):
            s["evidence_visible"] = _mean(q["evidence_visible"] for q in needles)
        s["latency_s"] = _mean(q["latency_s"] for q in needles)
        out["needle"] = s
    if surveys:
        s = {"questions": len(surveys)}
        for k in ks:
            s[f"docs_covered@{k}"] = _mean(q["docs_covered"][str(k)] for q in surveys)
        if all("evidence_visible" in q for q in surveys):
            s["evidence_visible"] = _mean(q["evidence_visible"] for q in surveys)
        s["latency_s"] = _mean(q["latency_s"] for q in surveys)
        out["survey"] = s
    return out
