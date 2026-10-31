# artmind/retrieval_eval/runner.py
"""Run the labelled set through vector-text and score it (spec 2026-10-31 §6.2).

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

Search = Callable[[list[str], str, int, float | None], dict]


def _default_search(domains: list[str], question: str, top_k: int, diversity: float | None) -> dict:
    from artmind import vector_query

    kwargs = {} if diversity is None else {"diversity": diversity}
    return vector_query.vector_text_search(domains, question, top_k, **kwargs)


def run_eval(
    labels: Sequence[RetrievalLabel],
    *,
    ks: Sequence[int] = (5, 20),
    diversity: float | None = None,
    search: Search | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict:
    search = search or _default_search
    ks = sorted(set(int(k) for k in ks))
    top = max(ks)
    per: list[dict] = []
    for label in labels:
        t0 = clock()
        result = search(list(label.domains), label.question, top, diversity)
        latency = clock() - t0
        chunks = [r["chunk"] for r in (result.get("rows") or []) if r.get("chunk")]
        chunk_ids = [c.get("id") for c in chunks][:top]
        doc_ids = [c.get("doc_id") for c in chunks][:top]
        entry: dict = {"id": label.id, "kind": label.kind, "latency_s": round(latency, 3), "chunk_ids": chunk_ids}
        if label.kind == "needle":
            relevant = set(label.relevant)
            entry["recall"] = {str(k): metrics.recall_at_k(chunk_ids, relevant, k) for k in ks}
            entry["rr"] = metrics.reciprocal_rank(chunk_ids, relevant)
        else:
            relevant_docs = set(label.relevant_docs)
            entry["docs_covered"] = {str(k): metrics.docs_covered_at_k(doc_ids, relevant_docs, k) for k in ks}
        per.append(entry)
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ks": ks,
        "diversity": diversity,
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
