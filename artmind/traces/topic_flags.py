"""Benchmark turns that ran a topic plan matching their question (spec 2026-11-10 §10).

A plan built from a benchmark question encodes its decomposition. `traces compare` flags a
turn that ran `query topics run <slug>` for a plan whose topic or a subject matches the
turn's question: equal after case and whitespace folding, or one contains the other when
both are at least MIN_CONTAIN characters. A fact about the run; nothing is refused.
"""
from __future__ import annotations

import json

MIN_CONTAIN = 40
COMMAND = "query topics run"


def _norm(text) -> str:
    return " ".join(str(text or "").casefold().split())


def matches(question: str, texts) -> bool:
    q = _norm(question)
    for t in texts:
        t = _norm(t)
        if not t:
            continue
        if t == q:
            return True
        if len(t) >= MIN_CONTAIN and len(q) >= MIN_CONTAIN and (t in q or q in t):
            return True
    return False


def slugs_run(conn, trace_id: str) -> list[str]:
    """The plan slugs a turn ran with `query topics run`, in order, once each."""
    out = []
    for (args,) in conn.execute("SELECT args FROM trace_spans WHERE trace_id = ? AND command = ?", (trace_id, COMMAND)):
        try:
            slug = (json.loads(args) or {}).get("slug") if args else None
        except (ValueError, AttributeError):
            slug = None
        if slug and slug not in out:
            out.append(slug)
    return out


def flags(runs, plan_texts=None) -> list[dict]:
    """[{run, seq, qid, slug}] for every turn that ran a plan matching its question.

    `plan_texts(slug) -> [topic, *subjects] | None` (default: this vault's plan files)."""
    from artmind.traces import store

    if plan_texts is None:
        plan_texts = _plan_texts
    conn = store._connect()
    out = []
    try:
        for run in runs:
            for seq, q in sorted(run.questions.items()):
                ans = run.answers.get(seq)
                tid = getattr(ans, "trace_id", None)
                if not tid:
                    continue
                for slug in slugs_run(conn, tid):
                    texts = plan_texts(slug)
                    if texts and matches(q["question"], texts):
                        out.append({"run": run.run_id, "seq": seq, "qid": q["qid"] or str(seq), "slug": slug})
    finally:
        conn.close()
    return out


def _plan_texts(slug: str):
    from artmind.topics import plan_store

    try:
        spec = plan_store.load_plan(slug).spec
    except Exception:
        return None
    return [spec.topic, *spec.subjects]
