"""The pairwise answer judge (spec 2026-11-08 §3.2).

One LLM call per question pair: the question, its expected-answer note and two answers labelled A and B
(order seeded per pair, so a bias toward A or B averages out), never which run produced which.
Verdict: same | minor | material, and better: A | B | equal. Parsed defensively; anything unusable is
`unjudged`. Cached in `trace_judgements` by a hash of (model, PROMPT_VERSION, question, note, both answers),
so a re-run costs nothing. Sends both answers and the question to the configured LLM provider.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

PROMPT_VERSION = 2
VERDICTS = ("same", "minor", "material")
ANSWER_CEILING = 40_000  # answers are judged whole; one over this is unjudged, never cut

TEMPLATE = """You compare two answers to the same question from a question-answering system over a document corpus.

Question:
{question}

What a good answer contains (the grader's note; may be empty):
{expected}

Answer A:
{a}

Answer B:
{b}

Judge the substance, not the wording or the length.
- "same": the same facts, figures, dates and conclusion.
- "minor": the same conclusion; they differ in detail or coverage in ways that do not change what the reader learns.
- "material": they differ in a fact, figure, date, count or conclusion, or one misses a part of the question the other answers.
Then say which is better against the question and the note: "A", "B" or "equal". Prefer the one that is correct,
answers every part, uses the latest applicable source, and states what it could not find instead of guessing.

Return only JSON: {{"verdict": "same|minor|material", "better": "A|B|equal", "reason": "<one sentence>"}}"""


@dataclass(frozen=True)
class Verdict:
    verdict: str          # same | minor | material | unjudged
    better: str | None    # left | right | equal | None
    reason: str


def prompt(question: str, expected: str | None, a: str, b: str) -> str:
    return TEMPLATE.format(question=question, expected=expected or "(none)", a=a or "", b=b or "")


def _norm_verdict(v) -> str | None:
    v = str(v or "").strip().casefold()
    if v.startswith("material"):
        return "material"
    if v.startswith("minor"):
        return "minor"
    return "same" if v == "same" else None


def judge_pair(question, expected, left, right, *, llm, model: str, swap: bool = False) -> Verdict:
    if not (left or "").strip() or not (right or "").strip():
        if not (left or "").strip() and not (right or "").strip():
            return Verdict("same", "equal", "both answers are empty")
        return Verdict("material", "right" if not (left or "").strip() else "left", "one answer is empty")
    if len(left) > ANSWER_CEILING or len(right) > ANSWER_CEILING:
        return Verdict("unjudged", None, f"an answer is over the judging ceiling of {ANSWER_CEILING} characters")
    a, b = (right, left) if swap else (left, right)
    try:
        from artmind.extraction import parse_json_response

        data = parse_json_response(llm(prompt(question, expected, a, b)))
    except Exception:
        return Verdict("unjudged", None, "the judge call failed")
    if not isinstance(data, dict):
        return Verdict("unjudged", None, "the judge returned no object")
    verdict = _norm_verdict(data.get("verdict"))
    better_ab = str(data.get("better") or "").strip().upper()
    if verdict is None or better_ab not in ("A", "B", "EQUAL"):
        return Verdict("unjudged", None, "the judge's answer was not in the expected form")
    if better_ab == "EQUAL":
        better = "equal"
    else:
        better = ("right" if better_ab == "A" else "left") if swap else ("left" if better_ab == "A" else "right")
    return Verdict(verdict, better, str(data.get("reason") or "")[:500])


def _key(model, question, expected, left, right) -> str:
    raw = json.dumps([PROMPT_VERSION, model, question, expected, left, right], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def cached_judge(left_run, right_run, seq, question, expected, left, right, *, llm, model: str) -> Verdict:
    from artmind.traces import store

    key = _key(model, question, expected, left, right)
    conn = store._connect()
    try:
        row = conn.execute("SELECT verdict, better, reason FROM trace_judgements WHERE key = ?", (key,)).fetchone()
        if row:
            return Verdict(row[0], row[1], row[2] or "")
        swap = int(key[:2], 16) % 2 == 1  # seeded by the content, so a re-run asks the same way
        v = judge_pair(question, expected, left, right, llm=llm, model=model, swap=swap)
        if v.verdict != "unjudged":
            conn.execute("INSERT OR REPLACE INTO trace_judgements (key, left_run, right_run, seq, model, verdict, better,"
                         " reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (key, left_run, right_run, seq, model, v.verdict, v.better, v.reason, time.time()))
            conn.commit()
        return v
    finally:
        conn.close()


def default_llm(model: str | None = None):
    from artmind.extraction import call_llm
    from utils.functions import load_env, resolve_llm_model

    chosen = resolve_llm_model(load_env(), model)
    return (lambda p: call_llm(chosen, p, call_type="compare_judge")), chosen
