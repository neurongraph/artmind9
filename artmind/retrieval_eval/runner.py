# artmind/retrieval_eval/runner.py
"""Run the labelled set through vector-text or query survey and score it (spec 2026-10-31 §6.2).

Runs in-process (no `serve` proxy). Runs are written to
<vault>/.artmind/logs/retrieval-eval/<timestamp>.json: private, vault only.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from artmind.retrieval_eval import metrics
from artmind.retrieval_eval.labels import RetrievalLabel

Search = Callable[[list[str], str, int], dict]


def default_search_fn(shape: str = "full") -> Search:
    if shape == "full":
        return lambda domains, question, top_k: _default_search(domains, question, top_k)

    def search(domains: list[str], question: str, top_k: int) -> dict:
        from artmind import vector_query

        return vector_query.vector_text_search(domains, question, top_k, shape=shape)
    return search


def _default_search(domains: list[str], question: str, top_k: int) -> dict:
    from artmind import vector_query

    return vector_query.vector_text_search(domains, question, top_k)


def survey_search_fn(legs: Sequence[str], shape: str = "full", top_docs: int | None = None) -> Search:
    """`query survey` as an eval search function (spec 2026-11-01 §4.2).

    `top_docs` pins the depth (the visibility comparison runs full at 10, brief at 50)."""
    def search(domains: list[str], question: str, top_k: int) -> dict:
        from artmind import survey

        return survey.survey_search(domains, question, top_docs=top_docs or top_k, legs=tuple(legs), **({} if shape == "full" else {"shape": shape}))
    return search


def budget_view(payload: dict, retriever: str) -> dict:
    """The payload as the agent sees it: through `result_store.fit` with `_echo_json`'s
    compact serialiser, when ARTMIND_OUTPUT_BUDGET is set. Handles go to a temp dir."""
    import tempfile

    from artmind import result_store
    from artmind.cli_conventions import LIST_KEY

    budget = result_store.current_budget()
    if not budget:
        return payload
    dumps = lambda p: json.dumps(p, ensure_ascii=False, default=str, separators=(",", ":"))  # noqa: E731
    declared = LIST_KEY.get("query survey" if retriever == "survey" else "query vector-text")
    with tempfile.TemporaryDirectory() as tmp:
        out, *_ = result_store.fit(payload, budget, dumps, declared, Path(tmp))
    return out


def visible_means(run: dict) -> dict:
    return {kind: stats["evidence_visible"] for kind, stats in (run.get("summary") or {}).items()
            if "evidence_visible" in stats}


def visibility_gate(full: dict, brief: dict) -> dict:
    """§4.3: brief survey(50) must show at least as much evidence as full survey(10)."""
    def mean(run, kind):
        return ((run.get("summary") or {}).get(kind) or {}).get("evidence_visible")
    out = {}
    for kind, key in (("needle", "needle_ok"), ("survey", "survey_ok")):
        b, f = mean(brief, kind), mean(full, kind)
        out[key] = b is None or f is None or b >= f
    return out


def run_eval(
    labels: Sequence[RetrievalLabel],
    *,
    ks: Sequence[int] = (5, 20),
    search: Search | None = None,
    clock: Callable[[], float] = time.perf_counter,
    only_kinds: Sequence[str] | None = None,
    retriever: str = "vector-text",
    measure_visible: bool = False,
) -> dict:
    labels = [l for l in labels if not only_kinds or l.kind in only_kinds]
    search = search or _default_search
    ks = sorted(set(int(k) for k in ks))
    top = max(ks)
    per: list[dict] = []
    for label in labels:
        t0 = clock()
        result = search(list(label.domains), label.question, top)
        latency = clock() - t0
        shown = budget_view(result, retriever) if measure_visible else None
        if result.get("documents") is not None:            # a survey payload ranks documents
            chunk_ids = []
            doc_ids = [d.get("doc_id") for d in result["documents"]][:top]
        else:
            rows = result.get("rows") or []
            chunk_ids = [(r.get("chunk") or {}).get("id") or r.get("chunk_id") for r in rows
                         if r.get("chunk") or r.get("chunk_id")][:top]
            doc_ids = [(r.get("chunk") or {}).get("doc_id") or (r.get("doc") or {}).get("id") for r in rows
                       if r.get("chunk") or r.get("chunk_id")][:top]
        entry: dict = {"id": label.id, "kind": label.kind, "latency_s": round(latency, 3), "chunk_ids": chunk_ids}
        if result.get("documents") is None:               # needle step facts, top-10 rows of vector-text
            entry.update(metrics.needle_stats(result.get("rows") or []))
            if label.kind == "needle":
                entry["in_top10"] = bool(set(label.relevant) & set(chunk_ids[:metrics.STAT_ROWS]))
        if label.kind == "needle":
            relevant = set(label.relevant)
            entry["recall"] = {str(k): metrics.recall_at_k(chunk_ids, relevant, k) for k in ks}
            entry["rr"] = metrics.reciprocal_rank(chunk_ids, relevant)
        else:
            relevant_docs = set(label.relevant_docs)
            entry["docs_covered"] = {str(k): metrics.docs_covered_at_k(doc_ids, relevant_docs, k) for k in ks}
        if measure_visible:
            entry["evidence_visible"] = metrics.evidence_visible(
                shown, label.kind, set(label.relevant if label.kind == "needle" else label.relevant_docs))
        per.append(entry)
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ks": ks,
        "labels": len(labels),
        "summary": metrics.summarise(per, ks),
        "questions": per,
    }


def runs_dir() -> Path:
    import paths

    return Path(paths.LOGS_DIR) / "retrieval-eval"


def write_run(run: dict, directory: Path | None = None) -> Path:
    directory = Path(directory or runs_dir())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(run, indent=2), encoding="utf-8")
    return path


def compare_runs(before: dict, after: dict) -> dict:
    """Per kind, per metric: after - before (questions and latency left out of the diff)."""
    out: dict = {}
    for kind, b_stats in (after.get("summary") or {}).items():
        a_stats = (before.get("summary") or {}).get(kind) or {}
        diff = {
            metric: round(value - a_stats[metric], 4)
            for metric, value in b_stats.items()
            if metric not in ("questions", "latency_s") and metric in a_stats
        }
        if diff:
            out[kind] = diff
    return out
