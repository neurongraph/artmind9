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


# ── needle statistics and NEEDLE_SPREAD calibration (B2 spec 2026-11-07 §4.2-§4.3) ──

STAT_ROWS = 10      # the planner's needle step reads its ten rows
SPREAD_CANDIDATES = range(2, 11)
MIN_PRECISION = 0.95


def _row_doc_id(row: dict) -> str | None:
    if not isinstance(row, dict) or row.get("chat"):
        return None
    chunk = row.get("chunk") if isinstance(row.get("chunk"), dict) else {}
    return ((row.get("document") or {}).get("id") or chunk.get("doc_id")
            or (row.get("doc") or {}).get("id"))


def needle_stats(rows: Sequence[dict]) -> dict:
    """`distinct_documents` and `score_drop` over the first ten vector-text rows, as the
    planner's step predicates define them: distinct document ids among the rows; relevance of
    rank 1 minus rank 2, None when either has no relevance (found by the keyword leg only)."""
    top = list(rows or [])[:STAT_ROWS]
    distinct = len({d for d in map(_row_doc_id, top) if d})
    drop = None
    if len(top) >= 2:
        a, b = (top[0] or {}).get("relevance"), (top[1] or {}).get("relevance")
        if a is not None and b is not None:
            drop = round(a - b, 4)
    return {"distinct_documents": distinct, "score_drop": drop}


def answers_alone(q: dict, spread: int, margin: float) -> bool:
    drop, docs = q.get("score_drop"), q.get("distinct_documents")
    return drop is not None and docs is not None and drop >= margin and docs < spread


def needle_spread_table(per_question: Sequence[dict], margin: float) -> dict:
    """For each candidate s: survey labels the shortcut would answer alone (must be none) and
    the precision of the shortcut on needle labels (relevant chunk in the top ten; >= 0.95).
    Counts and label ids only. Recommended s: the largest meeting both, else 2."""
    scored = [q for q in per_question if "distinct_documents" in q]
    surveys = [q for q in scored if q["kind"] == "survey"]
    needles = [q for q in scored if q["kind"] == "needle"]
    rows, recommended = [], 2
    for s in SPREAD_CANDIDATES:
        bad = [q["id"] for q in surveys if answers_alone(q, s, margin)]
        alone = [q for q in needles if answers_alone(q, s, margin)]
        hit = sum(1 for q in alone if q.get("in_top10"))
        precision = round(hit / len(alone), 4) if alone else None
        meets = not bad and precision is not None and precision >= MIN_PRECISION
        if meets:
            recommended = s
        rows.append({"s": s, "survey_answered_alone": len(bad), "survey_ids": bad,
                     "needle_answered_alone": len(alone), "needle_precision": precision,
                     "needle_misses": [q["id"] for q in alone if not q.get("in_top10")],
                     "meets": meets})
    return {"margin": margin, "min_precision": MIN_PRECISION,
            "labels": {"needle": len(needles), "survey": len(surveys)},
            "candidates": rows, "recommended": recommended}
