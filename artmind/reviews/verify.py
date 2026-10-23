"""Verification (spec 2026-10-11 §9): every dossier carries one regression
question; `regressions_markdown` assembles them into a benchmark file and
`verify` re-runs one as a one-question benchmark, then sets the new trace
beside the original. Mechanical checks only -- grading against the evaluation
comment is the reviewer's, confirming `verified` is the human's."""

import asyncio
import re

from artmind.reviews.dossier import Dossier, DossierError, append_log, iter_dossiers, load, save
from artmind.reviews.validate import _PLACEHOLDER_RE

GRAPH_BASES = ("graph", "mixed")
ANSWER_EXCERPT = 1500
#: The `R-<review_id> — ` prefix of a regression heading; the benchmark parser keeps it in the title.
_R_PREFIX = re.compile(r"^R-\S+\s*[—:-]\s*")


def _regression_text(dossier: Dossier) -> str:
    return _PLACEHOLDER_RE.sub("", dossier.sections.get("Regression question") or "").strip()


def regression(dossier: Dossier) -> dict:
    from artmind.benchmark import _parse_questions

    questions = _parse_questions(_regression_text(dossier))
    if len(questions) != 1 or not questions[0].get("question"):
        raise DossierError(f"{dossier.review_id}: the Regression question section must hold exactly one question")
    q = questions[0]
    title = _R_PREFIX.sub("", q.get("title") or "").strip() or None
    return {"review_id": dossier.review_id, "title": title, "question": q["question"],
            "eval_comment": q.get("eval_comment")}


def regressions_markdown(*, statuses=None) -> str:
    blocks = []
    for _, dossier, error in iter_dossiers():
        if error or (statuses and dossier.meta.get("status") not in statuses):
            continue
        text = _regression_text(dossier)
        if text:
            blocks.append(text)
    return "# artmind review regressions\n\n" + "\n\n".join(blocks) + "\n"


def summarize(shown: dict) -> dict:
    from artmind.traces.path import parse_sources, span_layer

    turn = shown["turn"]
    spans = [s for e in shown["events"] for s in e.get("spans", [])] + list(shown.get("unattributed_spans") or [])
    return {
        "trace_id": turn["trace_id"],
        "answer": (turn.get("answer") or "")[:ANSWER_EXCERPT],
        "fell_through": bool(turn.get("fell_through")),
        "answer_basis": turn.get("answer_basis"),
        "commands": sorted({s["command"] for s in spans if span_layer(s) != "discovery"}),
        "views": sorted({name for s in spans
                         if (name := ((s.get("evidence") or {}).get("view") or {}).get("name"))}),
        "sources": parse_sources(turn.get("answer")),
    }


def compare(original: dict | None, new: dict) -> dict:
    from artmind.traces.path import is_graph_miss

    new_summary = summarize(new)
    checks = [{"check": "answered", "ok": bool(new_summary["answer"])}]
    if original is not None and is_graph_miss(original["turn"]):
        checks.append({"check": "graph_answer",
                       "ok": not new_summary["fell_through"] and new_summary["answer_basis"] in GRAPH_BASES,
                       "why": "the original turn was a graph miss: the fix must let the graph answer"})
    return {"original": summarize(original) if original is not None else None, "new": new_summary,
            "checks": checks, "mechanical_ok": all(c["ok"] for c in checks)}


def _domain_of(dossier: Dossier, original: dict | None) -> str | None:
    for element in dossier.meta.get("elements") or []:
        if "/" in str(element):
            return str(element).split("/", 1)[0]
    domains = (original or {}).get("turn", {}).get("domains") or []
    return domains[0] if domains else None


def _default_runner(run_id: str) -> None:
    from artmind.benchmark import _run_benchmark

    asyncio.run(_run_benchmark(run_id))


def verify(review_id: str, *, run_id: str | None = None, backend: str = "claude-sdk", runner=None) -> dict:
    from artmind import benchmark
    from artmind.reviews.index import index_dossier
    from artmind.traces.analysis import show

    dossier = load(review_id)
    reg = regression(dossier)
    original_id = (dossier.meta.get("trace_ids") or [None])[0]
    original = show(original_id) if original_id else None
    if run_id is None:
        run_id = benchmark._create_benchmark_run(
            str(dossier.path),
            [{"seq": 1, "qid": f"R-{review_id}", "title": reg["title"], "question": reg["question"],
              "eval_comment": reg["eval_comment"]}],
            domain_hint=_domain_of(dossier, original), backend=backend, name=f"verify {review_id}",
        )
        (runner or _default_runner)(run_id)
    question = benchmark._get_benchmark_question(run_id, 1)
    if question is None:
        raise DossierError(f"benchmark run {run_id} has no question 1")
    new_id = question.get("trace_id")
    new = show(new_id) if new_id else None
    result = {"review_id": review_id, "run_id": run_id, "question": reg["question"],
              "eval_comment": reg["eval_comment"], "run_status": question.get("status"),
              "error": question.get("error_message")}
    if new is None:
        result.update({"original": summarize(original) if original else None, "new": None,
                       "checks": [{"check": "traced", "ok": False,
                                   "why": "the benchmark question recorded no trace (failed, or a stale serve daemon)"}],
                       "mechanical_ok": False})
    else:
        result.update(compare(original, new))
    if new_id:
        # Linked to the dossier (and indexed), so the verify turn is "reviewed":
        # it never lands in `traces triage` as a fresh failure to diagnose.
        dossier.meta["verify_trace_ids"] = [*(dossier.meta.get("verify_trace_ids") or []), new_id]
    verdict = "pass" if result["mechanical_ok"] else "fail"
    append_log(dossier, f"verify run `{run_id}` → trace `{new_id}`: mechanical checks {verdict} "
                        f"(grade against the evaluation comment, then `artmind review status {review_id} verified`)")
    path = save(dossier)
    try:
        index_dossier(dossier, path)
    except Exception:
        pass  # the file is the source of truth; `review reindex` repairs the index
    return result
