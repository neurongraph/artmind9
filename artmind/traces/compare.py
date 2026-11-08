"""Comparing benchmark runs (spec 2026-11-08 §3).

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
                    spans = conn.execute("SELECT evidence FROM trace_spans WHERE trace_id = ?",
                                         (tid,)).fetchall()
                    commands = len(spans)
                    found: dict[str, dict] = {}
                    anon: dict[str, dict] = {}

                    def note(doc_id, **forms):
                        forms = {k: v for k, v in forms.items() if isinstance(v, str) and v}
                        if not doc_id and not forms.get("path") and not forms.get("source_path"):
                            return
                        if doc_id:
                            target = found.setdefault(doc_id, {"id": doc_id})
                        else:
                            target = anon.setdefault(forms.get("path") or forms["source_path"], {"id": None})
                        for k, v in forms.items():
                            target.setdefault(k, v)

                    for (ev,) in spans:
                        try:
                            ev_d = json.loads(ev or "{}")
                        except ValueError:
                            continue
                        if not isinstance(ev_d, dict):
                            continue
                        for d in ev_d.get("documents") or []:
                            if isinstance(d, dict):
                                note(d.get("doc_id"), path=d.get("path"))
                        prov = ev_d.get("provenance")
                        for d in (prov.get("documents") if isinstance(prov, dict) else None) or []:
                            if isinstance(d, dict):
                                note(d.get("doc_id"), path=d.get("path"), source_path=d.get("source_path"),
                                     title=d.get("title"))
                        for c in ev_d.get("chunks") or []:
                            if isinstance(c, dict):
                                note(c.get("doc_id"))
                    refs = tuple([*found.values(), *anon.values()])
                    docs = tuple(dict.fromkeys(r["id"] for r in refs if r["id"]))
                run.answers[seq] = Answer(seq, q[5], tid, q[7], q[8], rating, commands, docs, refs)
            out.append(run)
        return out
    finally:
        conn.close()


def _norm_q(text) -> str:
    return " ".join(str(text or "").casefold().split())


def pair_by_text(left: Run, right: Run, *, expected: dict[str, str] | None = None
                 ) -> tuple[list[Pair], list[str], list[str]]:
    """Pairs of answers to the same question text (whitespace and case folded), in `left`'s order, and the qids
    asked in only one run. `expected` maps a question text to an expected-answer note that overrides the note
    stored with `left`'s run (the current questions file, spec §2.5)."""
    expected = {_norm_q(k): v for k, v in (expected or {}).items()}
    right_by = {_norm_q(q["question"]): s for s, q in right.questions.items()}
    pairs, only_left = [], []
    for s in sorted(left.questions):
        q = left.questions[s]
        key = _norm_q(q["question"])
        rs = right_by.pop(key, None)
        if rs is None:
            only_left.append(q["qid"] or str(s))
            continue
        pairs.append(Pair(s, q["qid"], q["question"], expected.get(key, q["eval_comment"]),
                          left.answers[s], right.answers[rs]))
    only_right = [right.questions[s]["qid"] or str(s) for s in sorted(right_by.values())]
    return pairs, only_left, only_right


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
    from artmind import benchmark_labels as reach_mod

    by_text = {_norm_q(q["question"]): seq for seq, q in run.questions.items()}
    rows = {}
    for lb in labels:
        seq = by_text.get(_norm_q(lb.question))
        if seq is None or not lb.expected_documents:
            continue
        qid = run.questions[seq].get("qid") or str(seq)
        ans = run.answers[seq]
        docs = [{"id": r.get("id"), "path": r.get("path"), "source_path": r.get("source_path"), "name": None,
                 "title": r.get("title")} for r in ans.doc_refs]
        matched, _ = reach_mod.reached_documents(list(lb.expected_documents), docs)
        rows[qid] = [len(matched), len(lb.expected_documents)]
    if not rows:
        return None
    return {"labels": len(rows), "expected": sum(v[1] for v in rows.values()),
            "reached": sum(v[0] for v in rows.values()), "by_qid": dict(sorted(rows.items()))}
