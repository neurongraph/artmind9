"""The absolute grade (spec 2026-11-08 §3.2): one LLM call per answer against its tiered expected answer.

Each must item found or missing, each must-not item present or absent, and correct | partial | wrong. Cached in
`trace_grades` by a hash of (model, PROMPT_VERSION, question, tiers, answer). Sends the question, the tiers and
the answer to the configured LLM provider.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from artmind.traces.tiers import Tiers

PROMPT_VERSION = 1
ANSWER_CEILING = 40_000
GRADES = ("correct", "partial", "wrong")

TEMPLATE = """You grade one answer from a question-answering system over a document corpus.

Question:
{question}

Items a correct answer MUST contain (numbered):
{must}

Items that may appear (no credit needed):
{optional}

Items the answer must NOT contain (numbered):
{must_not}

Grader's notes:
{notes}

Answer:
{answer}

For each MUST item say whether the answer states it (same meaning counts; wording may differ). For each MUST NOT
item say whether the answer states it. Then grade: "correct" (every must item, no must-not item), "partial"
(some must items), "wrong" (none, or the answer's main claim is false).

Return only JSON: {{"must": [true|false, ...], "must_not": [true|false, ...], "grade": "correct|partial|wrong",
"reason": "<one sentence>"}}"""


@dataclass(frozen=True)
class Grade:
    grade: str                      # correct | partial | wrong | ungraded
    must_found: tuple[str, ...]
    must_missed: tuple[str, ...]
    must_not_hit: tuple[str, ...]
    reason: str


def _numbered(items) -> str:
    return "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1)) or "(none)"


def prompt(question: str, tiers: Tiers, answer: str) -> str:
    return TEMPLATE.format(question=question, must=_numbered(tiers.must), optional=_numbered(tiers.optional),
                           must_not=_numbered(tiers.must_not), notes=tiers.notes or "(none)", answer=answer)


def _ungraded(reason: str) -> Grade:
    return Grade("ungraded", (), (), (), reason)


def grade_answer(question: str, tiers: Tiers, answer: str | None, *, llm) -> Grade:
    if not tiers.must:
        return _ungraded("no tiered expected answer with must items")
    answer = answer or ""
    if not answer.strip():
        return Grade("wrong", (), tiers.must, (), "the answer is empty")
    if len(answer) > ANSWER_CEILING:
        return _ungraded(f"the answer is over the judging ceiling of {ANSWER_CEILING} characters")
    try:
        from artmind.extraction import parse_json_response

        data = parse_json_response(llm(prompt(question, tiers, answer)))
    except Exception:
        return _ungraded("the grader call failed")
    if not isinstance(data, dict):
        return _ungraded("the grader returned no object")
    must, must_not = data.get("must"), data.get("must_not") or []
    g = str(data.get("grade") or "").strip().casefold()
    if (not isinstance(must, list) or len(must) != len(tiers.must) or g not in GRADES
            or not isinstance(must_not, list) or len(must_not) != len(tiers.must_not)):
        return _ungraded("the grader's answer was not in the expected form")
    found = tuple(x for x, ok in zip(tiers.must, must) if ok is True)
    missed = tuple(x for x, ok in zip(tiers.must, must) if ok is not True)
    hit = tuple(x for x, ok in zip(tiers.must_not, must_not) if ok is True)
    if g == "correct" and (missed or hit):
        g = "partial"
    return Grade(g, found, missed, hit, str(data.get("reason") or "")[:500])


def _key(model, question, tiers: Tiers, answer) -> str:
    raw = json.dumps([PROMPT_VERSION, model, question, tiers.must, tiers.optional, tiers.must_not, tiers.notes,
                      answer], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def cached_grade(run_id: str, seq: int, question: str, tiers: Tiers, answer: str | None, *, llm, model: str) -> Grade:
    from artmind.traces import store

    key = _key(model, question, tiers, answer)
    conn = store._connect()
    try:
        row = conn.execute("SELECT detail FROM trace_grades WHERE key = ?", (key,)).fetchone()
        if row:
            d = json.loads(row[0])
            return Grade(d["grade"], tuple(d["must_found"]), tuple(d["must_missed"]), tuple(d["must_not_hit"]),
                         d["reason"])
        g = grade_answer(question, tiers, answer, llm=llm)
        if g.grade != "ungraded":
            conn.execute("INSERT OR REPLACE INTO trace_grades (key, run_id, seq, model, grade, detail, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (key, run_id, seq, model, g.grade, json.dumps({
                             "grade": g.grade, "must_found": g.must_found, "must_missed": g.must_missed,
                             "must_not_hit": g.must_not_hit, "reason": g.reason}), time.time()))
            conn.commit()
        return g
    finally:
        conn.close()
