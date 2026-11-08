"""Comparing benchmark runs for the P3b gate (spec 2026-11-07 P3 §9.2).

Pure reads of the registry DB: runs, their questions (paired by seq), the trace turn behind each answer,
its rating and its top-level spans. No LLM here (judge.py), no gate logic (gate.py).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

from artmind.traces import store


class CompareError(ValueError):
    pass


@dataclass(frozen=True)
class Answer:
    seq: int
    answer: str | None
    trace_id: str | None
    duration_s: float | None
    cost_usd: float | None
    rating: str | None
    commands: int
    documents: tuple[str, ...]
    doc_refs: tuple[dict, ...] = ()


@dataclass
class Run:
    run_id: str
    name: str | None
    contract: str
    source_path: str
    status: str
    questions: dict[int, dict] = field(default_factory=dict)   # seq -> {qid, title, question, eval_comment}
    answers: dict[int, Answer] = field(default_factory=dict)


@dataclass(frozen=True)
class Pair:
    seq: int
    qid: str | None
    question: str
    expected: str | None
    left: Answer
    right: Answer


def _resolve(conn, ref: str) -> str:
    rows = [r[0] for r in conn.execute("SELECT run_id FROM benchmark_runs WHERE run_id = ? OR run_id LIKE ?",
                                       (ref, ref + "%"))]
    exact = [r for r in rows if r == ref]
    if exact:
        return exact[0]
    if len(rows) == 1:
        return rows[0]
    raise CompareError(f"no benchmark run {ref!r}" if not rows else f"run id {ref!r} is ambiguous")


def load_runs(refs) -> list[Run]:
    conn = store._connect()
    try:
        out = []
        for ref in refs:
            rid = _resolve(conn, ref)
            row = conn.execute("SELECT run_id, name, contract, source_path, status FROM benchmark_runs WHERE run_id = ?",
                               (rid,)).fetchone()
            run = Run(row[0], row[1], row[2] or "classic", row[3], row[4])
            for q in conn.execute("SELECT seq, qid, title, question, eval_comment, answer_text, trace_id, duration_s,"
                                  " cost_usd FROM benchmark_questions WHERE run_id = ? ORDER BY seq", (rid,)):
                seq = q[0]
                run.questions[seq] = {"qid": q[1], "title": q[2], "question": q[3], "eval_comment": q[4]}
                tid = q[6]
                rating, commands, docs, refs = None, 0, (), ()
                if tid:
                    fb = conn.execute("SELECT rating FROM trace_feedback WHERE trace_id = ? ORDER BY id DESC LIMIT 1",
                                      (tid,)).fetchone()
                    rating = fb[0] if fb else None
                    spans = conn.execute("SELECT evidence FROM trace_spans WHERE trace_id = ? AND parent_span_id IS NULL",
                                         (tid,)).fetchall()
                    commands = len(spans)
                    found: dict[tuple, dict] = {}
                    for (ev,) in spans:
                        try:
                            listed = json.loads(ev or "{}").get("documents") or []
                        except ValueError:
                            continue
                        for d in listed:
                            if isinstance(d, dict) and (d.get("doc_id") or d.get("path")):
                                ref = {"id": d.get("doc_id"), "path": d.get("path")}
                                found.setdefault((ref["id"], ref["path"]), ref)
                    refs = tuple(found.values())
                    docs = tuple(dict.fromkeys(r["id"] for r in refs if r["id"]))
                run.answers[seq] = Answer(seq, q[5], tid, q[7], q[8], rating, commands, docs, refs)
            out.append(run)
        return out
    finally:
        conn.close()


def pair(left: Run, right: Run) -> list[Pair]:
    if [left.questions[s]["question"] for s in sorted(left.questions)] != \
            [right.questions[s]["question"] for s in sorted(right.questions)]:
        raise CompareError(f"runs {left.run_id[:8]} and {right.run_id[:8]} do not ask the same questions")
    return [Pair(s, left.questions[s]["qid"], left.questions[s]["question"], left.questions[s]["eval_comment"],
                 left.answers[s], right.answers[s]) for s in sorted(left.questions)]


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def measures(run: Run) -> dict:
    answers = list(run.answers.values())
    ratings = {"good": 0, "partial": 0, "wrong": 0, "unrated": 0}
    for a in answers:
        ratings[a.rating if a.rating in ratings else "unrated"] += 1
    return {"run_id": run.run_id, "contract": run.contract, "questions": len(run.questions),
            "answered": sum(1 for a in answers if (a.answer or "").strip()),
            "ratings": ratings,
            "duration_s_median": _median(a.duration_s for a in answers),
            "cost_usd_median": _median(a.cost_usd for a in answers),
            "commands_median": _median(a.commands for a in answers)}


def answer_reach(run: Run, labels) -> dict | None:
    """Expected documents (plan labels whose question text is this benchmark question's text) found in the
    agent's own spans for that question. None when no label links to the run's questions."""
    from artmind.query_plan import reach as reach_mod

    norm = lambda t: " ".join(str(t or "").casefold().split())  # noqa: E731
    by_text = {norm(q["question"]): seq for seq, q in run.questions.items()}
    rows = {}
    for lb in labels:
        seq = by_text.get(norm(lb.question))
        if seq is None or not lb.expected_documents:
            continue
        qid = run.questions[seq].get("qid") or str(seq)
        ans = run.answers[seq]
        docs = [{"id": r.get("id"), "path": r.get("path"), "source_path": None, "name": None, "title": None}
                for r in ans.doc_refs]
        matched, _ = reach_mod.reached_documents(list(lb.expected_documents), docs)
        rows[qid] = [len(matched), len(lb.expected_documents)]
    if not rows:
        return None
    return {"labels": len(rows), "expected": sum(v[1] for v in rows.values()),
            "reached": sum(v[0] for v in rows.values()), "by_qid": dict(sorted(rows.items()))}
