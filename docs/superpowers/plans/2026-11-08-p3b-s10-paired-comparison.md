# P3b slice S10: the paired comparison of agent contracts, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command reads the P3b gate from three benchmark runs per vault (classic A, classic B, bundle) on one build:

```
artmind traces compare --runs A,B,BUNDLE [--judge] [--model M] [--compact]
```

It reports:
- **mechanical measures** per run: ratings, answer reach, commands per turn, latency and cost;
- **a judged answer comparison:** an LLM reads each question's answers in pairs, against the question and its expected-answer note;
- **the gate verdict,** with the variance band.

**Architecture:**
- `artmind/traces/compare.py` gathers each run's turns and per-question data from the registry DB, and computes the mechanical measures. No LLM.
- `artmind/traces/judge.py` builds one judge prompt per question pair, calls `extraction.call_llm`, parses and checks the verdict, and caches it in the DB table `trace_judgements`. A re-run then makes no new LLM calls.
- `artmind/traces/gate.py` turns measures plus judgements into the §9.2 gate.
- The CLI command `traces compare` prints counts and question ids only. The judge's per-question reasons go to a private file under `<ARTMIND_HOME>/logs/compare-*.json`, because they quote answers.

**Tech stack:** Python 3.14, Click, sqlite3, pytest (hermetic, a fake judge), uv.

**Spec:** `docs/superpowers/specs/2026-11-07-p3-executor-design.md` §9.2 (the P3b gate, with the variance band added 2026-11-07) and §11 S10. Earlier plans: `docs/superpowers/plans/2026-11-07-p3b-s9-bundle-skill-contract.md` (S9: contract fields, the benchmark form).

**Why three runs:** a judged read of two classic runs on one build (brain01, 14 questions) found 5 of 14 answers materially different, from how far the agent read. A single classic vs bundle pair can't separate a regression from that noise.

**Starting point:** branch `claude/p3-design-a9f633` at `01d4d7b` or later. Work in a new worktree cut from it, never in that worktree itself (the user's installed `artmind` points at it).

---

## Before you start

- **The command name.** The spec says `traces stats --compareRuns`. This plan uses a separate command, `traces compare`, because its output is per run pair, not per domain and period like `stats`. Record that deviation in the spec's §9.2 in the last task.
- **Privacy:**
  - **Answers and questions never reach stdout.** Only counts, qids/seqs and verdict labels do. The judge's reasons and quotes go to the private detail file under `logs/`, which is gitignored.
  - **The judge sends both answers and the question to the configured LLM provider.** It runs only with `--judge`, and only when the vault's config allows it: `ARTMIND_COMPARE_JUDGE=1` in the vault's `.artmind/config.env`, or the flag `--allowJudge` on the command.
  - Tests use invented questions and answers.
- **Read-only.** `traces compare` writes only its own cache table (`trace_judgements`) and the detail file. Like `traces feedback`, it is not a graph, staging or vault write. Add it to the contract test's `_READ_ONLY` with that reason if the write check misfires; do not put it in `WRITE_COMMANDS`.
- Run tests with `uv run --group dev pytest <path> -q`, and the full suite at the end (`uv run --group dev pytest -q test/`, about 4 min).
- Commit after every task with the given message, plus `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the model you are). Do not push.

## Facts about the code you will need

- **`benchmark_runs`** (`artmind/db.py` ~line 100) columns: `run_id`, `name`, `domain_hint`, `source_path`, `backend`, `status`, `question_count`, `processed_count`, `queued_at`, `started_at`, `completed_at`, `error_message`, `contract` (S9).
- **`benchmark_questions`:** `run_id`, `seq`, `qid`, `title`, `question`, `eval_comment` (the expected-answer note, never shown to the agent), `status`, `answer_text`, `trace_json`, `turns`, `duration_s`, `cost_usd`, `started_at`, `completed_at`, `error_message`, `trace_id`.
- **`trace_turns`:** `trace_id`, `benchmark_run_id`, `benchmark_seq`, `contract`, `duration_s`, `cost_usd`, `turns`, `answer`, plus others.
- **`trace_feedback`:** `trace_id`, `rating` (`good` / `partial` / `wrong`), `expected`, `note`. One per trace (`store.add_feedback` upserts). `store.latest_feedback(trace_id)`.
- **`trace_spans`:** `parent_span_id` NULL means the agent's own call. `evidence` is JSON with `documents: [{doc_id, path}]` and `provenance.documents[]` (path, title).
- **Run state:** `artmind/traces/store.py` `_connect()` gives a sqlite3 connection to the registry DB, and `insert_events` / `list_spans_for` etc. live there. `artmind/db.py` creates tables idempotently; add the new table the same way.
- **`artmind/traces/path.py` `parse_sources(answer)`:** the link targets under an answer's `### Sources` heading. Note that the UI adds provenance, so benchmark answers usually have no Sources section.
- **`artmind/query_plan/reach.py`:** `reached_documents(expected, docs)` and `match_kind`. The plan labels file (`<vault>/benchmarking/plan_labels.yaml`, loaded by `artmind.query_plan.labels.load_labels`) has `expected_documents` per label. `Label.benchmark` exists, but **no label in the vaults sets it** (checked 2026-11-08). Labels with `source: benchmark` carry the benchmark question's text in `question`, so labels link to benchmark questions **by question text**, normalised (casefold, whitespace collapsed).
- **LLM calls:**
  - `artmind/extraction.py` `call_llm(model, prompt, call_type=...)` uses `ARTMIND_KG_LLM_PROVIDER` and logs to `llm_calls.log`.
  - `extraction.parse_json_response(text)` repairs JSON.
  - `utils.functions.resolve_llm_model(load_env(), override)` picks the model.
- **CLI:**
  - `traces_group` commands are in `artmind/cli.py` from ~line 4756.
  - `_echo_json(payload, compact)` prints with the envelope.
  - Options are camelCase.
  - `test/test_cli_conventions.py` checks options, the envelope and `--compact`.
  - `test/test_cli_guide.py` fails on a command not routed in `COMMAND_GROUPS`. `traces` subcommands sit under the `traces` group, so no change is needed there, but check.

---

### Task 1: Run data and mechanical measures

**Files:**
- Create: `artmind/traces/compare.py`
- Test: `test/test_traces_compare.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Comparing benchmark runs (P3 spec §9.2): per-question pairing and mechanical measures. Invented data."""

import json

import pytest

from artmind.traces import compare, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _run(run_id, contract, answers, *, durations=None, costs=None, ratings=None, spans=None):
    """A completed benchmark run with one trace turn per answer."""
    from artmind import benchmark

    qs = [{"seq": i, "qid": f"Q{i:02d}", "title": f"t{i}", "question": f"invented question {i}?",
           "eval_comment": f"expect fact {i}"} for i in range(1, len(answers) + 1)]
    rid = benchmark._create_benchmark_run("/tmp/q.md", qs, contract=contract)
    conn = store._connect()
    for i, answer in enumerate(answers, 1):
        tid = f"{run_id}-{i}"
        store.insert_turn(tid, source="benchmark", question=qs[i - 1]["question"], benchmark_run_id=rid,
                          benchmark_seq=i, contract=contract, started_at=1_700_000_000.0 + i)
        store.update_turn(tid, status="done", answer=answer, duration_s=(durations or {}).get(i, 20.0),
                          cost_usd=(costs or {}).get(i, 0.1), turns=3)
        conn.execute("UPDATE benchmark_questions SET answer_text=?, trace_id=?, status='done' WHERE run_id=? AND seq=?",
                     (answer, tid, rid, i))
        for n, docs in enumerate((spans or {}).get(i, [["d1"]])):
            store.insert_span({"span_id": f"{tid}-s{n}", "trace_id": tid, "command": "query vector-text", "args": "{}",
                               "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
                               "evidence": json.dumps({"documents": [{"doc_id": d} for d in docs]}),
                               "started_at": 1_700_000_000.5 + i}, [])
        if ratings and i in ratings:
            store.add_feedback(tid, ratings[i])
    conn.commit()
    conn.close()
    return rid


def test_runs_pair_by_seq_and_carry_their_contract():
    a = _run("a", "classic", ["ans a1", "ans a2"])
    b = _run("b", "bundle", ["ans b1", "ans b2"])
    runs = compare.load_runs([a, b])
    assert [r.contract for r in runs] == ["classic", "bundle"]
    pairs = compare.pair(runs[0], runs[1])
    assert [(p.seq, p.qid) for p in pairs] == [(1, "Q01"), (2, "Q02")]
    assert pairs[0].question == "invented question 1?" and pairs[0].expected == "expect fact 1"
    assert (pairs[0].left.answer, pairs[0].right.answer) == ("ans a1", "ans b1")


def test_runs_of_different_question_files_do_not_pair():
    a = _run("a", "classic", ["x"])
    from artmind import benchmark

    other = benchmark._create_benchmark_run("/tmp/other.md", [{"seq": 1, "qid": "Z1", "title": "t",
                                                               "question": "different?", "eval_comment": None}])
    with pytest.raises(compare.CompareError, match="same questions"):
        compare.pair(*compare.load_runs([a, other]))


def test_mechanical_measures():
    a = _run("a", "classic", ["x", "y", "z"], durations={1: 10, 2: 20, 3: 30}, costs={1: 0.1, 2: 0.2, 3: 0.3},
             ratings={1: "good", 2: "good", 3: "wrong"}, spans={1: [["d1"], ["d2"]], 2: [["d1"]], 3: [["d3"]]})
    m = compare.measures(compare.load_runs([a])[0])
    assert m["questions"] == 3 and m["answered"] == 3
    assert m["ratings"] == {"good": 2, "partial": 0, "wrong": 1, "unrated": 0}
    assert m["duration_s_median"] == 20 and m["cost_usd_median"] == 0.2
    assert m["commands_median"] == 1  # top-level spans per turn: 2, 1, 1


def test_a_missing_run_is_an_error():
    with pytest.raises(compare.CompareError, match="no benchmark run"):
        compare.load_runs(["nope"])


def test_a_run_id_prefix_resolves():
    a = _run("a", "classic", ["x"])
    assert compare.load_runs([a[:8]])[0].run_id == a
```

Check `store.update_turn`'s accepted keyword names against the real function before relying on them.

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_traces_compare.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Write `artmind/traces/compare.py`**

```python
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
                rating, commands, docs = None, 0, ()
                if tid:
                    fb = conn.execute("SELECT rating FROM trace_feedback WHERE trace_id = ? ORDER BY id DESC LIMIT 1",
                                      (tid,)).fetchone()
                    rating = fb[0] if fb else None
                    spans = conn.execute("SELECT evidence FROM trace_spans WHERE trace_id = ? AND parent_span_id IS NULL",
                                         (tid,)).fetchall()
                    commands = len(spans)
                    found = []
                    for (ev,) in spans:
                        try:
                            found += [d.get("doc_id") for d in (json.loads(ev or "{}").get("documents") or [])
                                      if isinstance(d, dict) and d.get("doc_id")]
                        except ValueError:
                            continue
                    docs = tuple(dict.fromkeys(found))
                run.answers[seq] = Answer(seq, q[5], tid, q[7], q[8], rating, commands, docs)
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
```

`benchmark_questions.trace_id` may be NULL for runs recorded before that column existed. The code handles it (no rating, no spans).

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_traces_compare.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/compare.py test/test_traces_compare.py
git commit -m "feat(traces): read and pair benchmark runs, with mechanical measures (P3 S10)"
```

### Task 2: Answer reach from plan labels

**Files:**
- Modify: `artmind/traces/compare.py`
- Test: `test/test_traces_compare.py` (append)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_answer_reach_uses_labels_linked_to_benchmark_questions():
    from artmind.query_plan.labels import Label

    a = _run("a", "classic", ["x", "y"], spans={1: [["policies/fees.md"]], 2: [["other.md"]]})
    run = compare.load_runs([a])[0]
    labels = [Label(id="L1", question="Invented  Question 1?", source="benchmark", domains=("b",), route="search",
                    view=None, params={}, expected_documents=("policies/fees.md", "rates.md"))]
    reach = compare.answer_reach(run, labels)
    assert reach == {"labels": 1, "expected": 2, "reached": 1, "by_qid": {"Q01": [1, 2]}}


def test_answer_reach_without_labels_is_none():
    a = _run("a", "classic", ["x"])
    assert compare.answer_reach(compare.load_runs([a])[0], []) is None
```

The `documents` the agent's spans recorded are doc ids or paths. `reach.match_kind` matches by path, name, id or title. In these tests the span doc ids are paths, so they match. In a real vault the span evidence holds `doc_id` (a UUID) and `path`. Make `load_runs` keep both forms per document as dicts `{"id": doc_id, "path": path}` in a separate field `Answer.doc_refs`, and match against that. Update Task 1's `Answer` accordingly and keep `documents` as the doc ids.

- [ ] **Step 2: Run it and check it fails.** Run: `uv run --group dev pytest test/test_traces_compare.py -q`. Expected: FAIL (`answer_reach` missing).

- [ ] **Step 3: Implement**

```python
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
```

A benchmark question's text in `benchmark_questions.question` is the parsed question body. Check on banking_corpus (read-only) how many of its 34 labels with `expected_documents` match a question of run `417025bb` by normalised text, and report the count. If fewer than 30 match, the parser and the labels differ in some systematic way (a prefix such as "Q04 — ", trailing punctuation): normalise that too, and say how.

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(traces): answer reach of a benchmark run from the plan labels (P3 S10)`

### Task 3: The judge

**Files:**
- Create: `artmind/traces/judge.py`
- Modify: `artmind/db.py` (table `trace_judgements`)
- Test: `test/test_traces_judge.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The answer judge (P3 spec §9.2): one LLM verdict per question pair, checked and cached. Fake LLM."""

import json

import pytest

from artmind.traces import judge


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


QUESTION, EXPECTED = "When did the invented project end?", "It ended in March; the later note supersedes the first."
LEFT, RIGHT = "It ended in March, per the later note.", "It ended in February."


def _llm(answer: dict):
    calls = []

    def call(prompt):
        calls.append(prompt)
        return json.dumps(answer)

    call.calls = calls
    return call


def test_the_prompt_holds_the_question_the_note_and_both_answers_unlabelled():
    p = judge.prompt(QUESTION, EXPECTED, LEFT, RIGHT)
    assert QUESTION in p and EXPECTED in p and LEFT in p and RIGHT in p
    assert "classic" not in p.lower() and "bundle" not in p.lower()  # the judge never learns which contract


def test_a_verdict_is_parsed_and_normalised():
    llm = _llm({"verdict": "materially different", "better": "A", "reason": "B gives the wrong month."})
    v = judge.judge_pair(QUESTION, EXPECTED, LEFT, RIGHT, llm=llm, model="m")
    assert (v.verdict, v.better) == ("material", "left") and "wrong month" in v.reason


@pytest.mark.parametrize("raw", ["not json", json.dumps({"verdict": "odd"}), json.dumps({"better": "A"})])
def test_a_bad_verdict_is_unjudged_not_a_crash(raw):
    v = judge.judge_pair(QUESTION, EXPECTED, LEFT, RIGHT, llm=lambda p: raw, model="m")
    assert v.verdict == "unjudged" and v.better is None


def test_order_is_randomised_and_undone():
    """The judge sees the pair in a seeded order; the stored verdict is about left/right, not A/B."""
    llm = _llm({"verdict": "material", "better": "B", "reason": "r"})
    v = judge.judge_pair(QUESTION, EXPECTED, LEFT, RIGHT, llm=llm, model="m", swap=True)
    assert v.better == "left"  # B in the swapped prompt was LEFT
    assert llm.calls[0].index(RIGHT) < llm.calls[0].index(LEFT)


def test_verdicts_are_cached_by_content():
    llm = _llm({"verdict": "same", "better": "equal", "reason": "r"})
    a = judge.cached_judge("run1", "run2", 1, QUESTION, EXPECTED, LEFT, RIGHT, llm=llm, model="m")
    b = judge.cached_judge("run1", "run2", 1, QUESTION, EXPECTED, LEFT, RIGHT, llm=llm, model="m")
    assert len(llm.calls) == 1 and a == b
    judge.cached_judge("run1", "run2", 1, QUESTION, EXPECTED, LEFT, "changed answer", llm=llm, model="m")
    assert len(llm.calls) == 2


def test_an_empty_answer_needs_no_llm():
    llm = _llm({"verdict": "same", "better": "equal", "reason": "r"})
    v = judge.judge_pair(QUESTION, EXPECTED, LEFT, "", llm=llm, model="m")
    assert (v.verdict, v.better, llm.calls) == ("material", "left", [])
```

- [ ] **Step 2: Run them and check they fail.** Run: `uv run --group dev pytest test/test_traces_judge.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement**

In `db.py`, after the `trace_feedback` table:

```python
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_judgements (
            key          TEXT PRIMARY KEY,
            left_run     TEXT NOT NULL,
            right_run    TEXT NOT NULL,
            seq          INTEGER NOT NULL,
            model        TEXT,
            verdict      TEXT NOT NULL,
            better       TEXT,
            reason       TEXT,
            created_at   REAL NOT NULL
        )
    """)
```

`artmind/traces/judge.py`:

```python
"""The answer judge for the P3b gate (spec 2026-11-07 P3 §9.2).

One LLM call per question pair: the question, its expected-answer note and two answers labelled A and B
(order seeded per pair, so a bias toward A or B averages out), never which contract produced which.
Verdict: same | minor | material, and better: A | B | equal. Parsed defensively; anything unusable is
`unjudged`. Cached in `trace_judgements` by a hash of (model, PROMPT_VERSION, question, note, both answers),
so a re-run costs nothing. Sends both answers and the question to the configured LLM provider.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

PROMPT_VERSION = 1
VERDICTS = ("same", "minor", "material")
ANSWER_CHARS = 8000

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
    return TEMPLATE.format(question=question, expected=expected or "(none)", a=(a or "")[:ANSWER_CHARS],
                           b=(b or "")[:ANSWER_CHARS])


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
```

`test_order_is_randomised_and_undone` calls `judge_pair(..., swap=True)` directly. In `cached_judge` the swap is seeded from the key, so the cache is stable. An unjudged verdict is not cached, so the next run retries it.

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(traces): an LLM answer judge for paired benchmark runs, cached by content (P3 S10)`

### Task 4: The gate

**Files:**
- Create: `artmind/traces/gate.py`
- Test: `test/test_traces_gate.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The P3b gate from measures and judgements (P3 spec §9.2, variance band)."""

from artmind.traces import gate


def _j(*pairs):
    """[(verdict, better)] per seq."""
    return {i: {"verdict": v, "better": b} for i, (v, b) in enumerate(pairs, 1)}


M = lambda contract, good, wrong, cmds, dur, cost: {  # noqa: E731
    "contract": contract, "questions": 10, "answered": 10,
    "ratings": {"good": good, "partial": 10 - good - wrong, "wrong": wrong, "unrated": 0},
    "commands_median": cmds, "duration_s_median": dur, "cost_usd_median": cost}


def test_balance_counts_left_minus_right():
    assert gate.balance(_j(("material", "left"), ("minor", "right"), ("same", "equal"), ("material", "left"))) == 1


def test_a_passing_set():
    classic_a, classic_b, bundle = M("classic", 7, 1, 6, 30, 0.3), M("classic", 6, 1, 6, 31, 0.3), M("bundle", 7, 1, 3, 20, 0.2)
    # A vs B: A better on 2, B better on 1 -> band 1. A vs bundle: A better on 1, bundle on 1 -> balance 0.
    ab = _j(("material", "left"), ("material", "left"), ("minor", "right"))
    a_bundle = _j(("material", "left"), ("minor", "right"), ("same", "equal"))
    b_bundle = _j(("minor", "equal"), ("material", "right"), ("same", "equal"))
    g = gate.evaluate(classic_a, classic_b, bundle, ab, a_bundle, b_bundle)
    assert g["passed"] is True and g["band"] == 1 and g["balance"] == 0
    assert g["worse_than_both"] == []


def test_bundle_materially_worse_than_both_classic_runs_fails():
    classic_a, classic_b, bundle = M("classic", 7, 1, 6, 30, 0.3), M("classic", 7, 1, 6, 30, 0.3), M("bundle", 7, 1, 3, 20, 0.2)
    ab = _j(("same", "equal"), ("same", "equal"))
    a_bundle = _j(("material", "left"), ("same", "equal"))
    b_bundle = _j(("material", "left"), ("same", "equal"))
    g = gate.evaluate(classic_a, classic_b, bundle, ab, a_bundle, b_bundle)
    assert g["passed"] is False and g["worse_than_both"] == [1]
    assert "materially worse than both classic runs" in " ".join(g["reasons"])


def test_ratings_and_cost_rows():
    classic_a, classic_b = M("classic", 8, 0, 6, 30, 0.3), M("classic", 8, 0, 6, 30, 0.3)
    bundle = M("bundle", 6, 2, 7, 40, 0.5)  # lost 2 good, more commands, slower, dearer
    same = _j(("same", "equal"))
    g = gate.evaluate(classic_a, classic_b, bundle, same, same, same)
    assert g["passed"] is False
    assert {r["row"] for r in g["rows"] if not r["ok"]} == {"rated_good", "commands", "latency", "cost"}


def test_unrated_runs_skip_the_rating_row():
    m = lambda c: {**M(c, 0, 0, 5, 30, 0.3), "ratings": {"good": 0, "partial": 0, "wrong": 0, "unrated": 10}}  # noqa: E731
    same = _j(("same", "equal"))
    g = gate.evaluate(m("classic"), m("classic"), {**m("bundle"), "commands_median": 3, "duration_s_median": 20}, same, same, same)
    assert next(r for r in g["rows"] if r["row"] == "rated_good")["ok"] is None  # not measured
```

- [ ] **Step 2: Run them and check they fail.** Expected: FAIL.

- [ ] **Step 3: Write `artmind/traces/gate.py`**

```python
"""The P3b gate (spec 2026-11-07 P3 §9.2, with the variance band): three runs on one build, classic A,
classic B and bundle, judged pairwise. A row with `ok: None` was not measured (e.g. no ratings) and does
not fail the gate; the gate passes when every measured row is ok."""

from __future__ import annotations


def balance(judgements: dict) -> int:
    """left-better minus right-better over judged questions."""
    js = [j for j in judgements.values() if j.get("verdict") not in (None, "unjudged")]
    return sum(1 for j in js if j.get("better") == "left") - sum(1 for j in js if j.get("better") == "right")


def _material_left(judgements: dict) -> set[int]:
    return {seq for seq, j in judgements.items() if j.get("verdict") == "material" and j.get("better") == "left"}


def evaluate(classic_a: dict, classic_b: dict, bundle: dict, ab: dict, a_bundle: dict, b_bundle: dict) -> dict:
    band = abs(balance(ab))
    bal = balance(a_bundle)                      # > 0: classic A better more often than bundle
    worse_both = sorted(_material_left(a_bundle) & _material_left(b_bundle))
    rows = []

    def row(name, ok, detail):
        rows.append({"row": name, "ok": ok, "detail": detail})

    rated = all(m["ratings"]["unrated"] < m["questions"] for m in (classic_a, bundle))
    row("rated_good", (bundle["ratings"]["good"] >= classic_a["ratings"]["good"] - 1) if rated else None,
        f"bundle {bundle['ratings']['good']} vs classic {classic_a['ratings']['good']} rated good")
    row("judged_balance", bal <= band, f"classic-over-bundle balance {bal}, classic A-vs-B band {band}")
    row("no_question_worse_than_both", not worse_both, f"materially worse than both classic runs: {worse_both}")
    for name, key, rule in (("commands", "commands_median", "lower"), ("latency", "duration_s_median", "lower"),
                            ("cost", "cost_usd_median", "not_higher")):
        a, b = classic_a.get(key), bundle.get(key)
        ok = None if a is None or b is None else (b < a if rule == "lower" else b <= a)
        row(name, ok, f"bundle {b} vs classic {a} ({rule})")
    failed = [r for r in rows if r["ok"] is False]
    reasons = [r["detail"] if r["row"] != "no_question_worse_than_both"
               else f"questions {worse_both} are materially worse than both classic runs" for r in failed]
    return {"passed": not failed, "band": band, "balance": bal, "worse_than_both": worse_both,
            "rows": rows, "reasons": reasons}
```

The worse-than-both reason must contain the phrase "materially worse than both classic runs", because the test checks for it.

The spec's "answer reach ≥ classic" row is added in Task 5, where the CLI has the labels. Add it as `row("answer_reach", ...)` when both runs have `answer_reach`, and as `None` otherwise.

- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(traces): the P3b gate with the classic-vs-classic variance band (P3 S10)`

### Task 5: `artmind traces compare`

**Files:**
- Modify: `artmind/cli.py` (new command under `traces_group`), `artmind/traces/gate.py` (the `answer_reach` row)
- Test: `test/test_traces_compare_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
"""`artmind traces compare --runs A,B,BUNDLE` (P3 S10). Invented runs; the judge is faked."""

import json

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from test_traces_compare import _isolated_db, _run  # noqa: F401  (fixture and helper)


@pytest.fixture
def three_runs():
    a = _run("a", "classic", ["ans one", "ans two"])
    b = _run("b", "classic", ["ans one", "ans two b"])
    c = _run("c", "bundle", ["ans one", "ans two c"])
    return a, b, c


def test_without_judge_prints_measures_and_no_text(three_runs, tmp_path, monkeypatch):
    monkeypatch.setattr("paths.LOGS_DIR", tmp_path / "logs")
    out = CliRunner().invoke(cli, ["traces", "compare", "--runs", ",".join(three_runs), "--compact"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.stdout)
    assert [m["contract"] for m in payload["measures"]] == ["classic", "classic", "bundle"]
    assert payload["judged"] is False and payload["gate"] is None
    assert "invented question" not in out.stdout and "ans two" not in out.stdout


def test_judge_needs_consent(three_runs, monkeypatch):
    monkeypatch.delenv("ARTMIND_COMPARE_JUDGE", raising=False)
    out = CliRunner().invoke(cli, ["traces", "compare", "--runs", ",".join(three_runs), "--judge"])
    assert out.exit_code == 1 and "ARTMIND_COMPARE_JUDGE" in out.output


def test_judged_compare_prints_counts_gate_and_writes_reasons_privately(three_runs, tmp_path, monkeypatch):
    from artmind.traces import judge

    monkeypatch.setattr("paths.LOGS_DIR", tmp_path / "logs")
    monkeypatch.setenv("ARTMIND_COMPARE_JUDGE", "1")
    monkeypatch.setattr(judge, "default_llm", lambda model=None: (
        lambda p: json.dumps({"verdict": "same", "better": "equal", "reason": "quoting ans two"}), "fake-model"))
    out = CliRunner().invoke(cli, ["traces", "compare", "--runs", ",".join(three_runs), "--judge", "--compact"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.stdout)
    assert payload["judged"] is True and payload["model"] == "fake-model"
    assert payload["pairs"]["classic_a__bundle"]["counts"] == {"same": 2, "minor": 0, "material": 0, "unjudged": 0}
    assert payload["gate"]["band"] == 0 and isinstance(payload["gate"]["passed"], bool)
    assert "quoting ans two" not in out.stdout
    detail = json.loads((tmp_path / "logs" / payload["detail_file"].rsplit("/", 1)[-1]).read_text())
    assert any("quoting ans two" in r["reason"] for r in detail["pairs"]["classic_a__bundle"]["questions"])


def test_runs_must_be_three_with_the_right_contracts(three_runs):
    a, b, c = three_runs
    out = CliRunner().invoke(cli, ["traces", "compare", "--runs", f"{a},{b}"])
    assert out.exit_code == 1 and "three" in out.output
    out = CliRunner().invoke(cli, ["traces", "compare", "--runs", f"{a},{c},{b}"])
    assert out.exit_code == 1 and "classic, classic, bundle" in out.output
```

- [ ] **Step 2: Run them and check they fail.** Expected: FAIL (no such command).

- [ ] **Step 3: Implement the command**

```python
@traces_group.command("compare")
@click.option("--runs", "runs", required=True, help="Three benchmark run ids (or unique prefixes), comma-separated: classic A, classic B, bundle")
@click.option("--judge", "use_judge", is_flag=True, help="Also judge each question's answers pairwise with the LLM (needs ARTMIND_COMPARE_JUDGE=1 or --allowJudge)")
@click.option("--allowJudge", "allow_judge", is_flag=True, help="Allow the judge for this call: it sends questions and answers to the configured LLM provider")
@click.option("--model", "model", default=None, help="Judge model (default: ARTMIND_KG_LLM_MODEL)")
@click.option("--labels", "labels_file", type=click.Path(path_type=Path), default=None,
              help="Plan labels file for answer reach (default: <vault>/benchmarking/plan_labels.yaml when present)")
@compact_option
def traces_compare(runs, use_judge, allow_judge, model, labels_file, compact) -> None:
    """Compare three benchmark runs for the P3b gate: classic A, classic B, bundle (spec P3 §9.2).

    Prints per-run measures, judged counts per pair, and the gate. Questions, answers and the judge's reasons
    never reach stdout; with --judge they go to <ARTMIND_HOME>/logs/compare-*.json (private).
    """
    from artmind.traces import compare as cmp
    from artmind.traces import gate as gate_mod

    refs = [r.strip() for r in runs.split(",") if r.strip()]
    if len(refs) != 3:
        raise click.ClickException("--runs takes three run ids: classic A, classic B, bundle")
    try:
        loaded = cmp.load_runs(refs)
        if [r.contract for r in loaded] != ["classic", "classic", "bundle"]:
            raise click.ClickException(
                f"--runs must be in the order classic, classic, bundle; got {', '.join(r.contract for r in loaded)}")
        names = ("classic_a", "classic_b", "bundle")
        pairs = {"classic_a__classic_b": cmp.pair(loaded[0], loaded[1]),
                 "classic_a__bundle": cmp.pair(loaded[0], loaded[2]),
                 "classic_b__bundle": cmp.pair(loaded[1], loaded[2])}
    except cmp.CompareError as exc:
        raise click.ClickException(str(exc)) from exc
    labels = _compare_labels(labels_file)
    measures = []
    for name, run in zip(names, loaded):
        m = cmp.measures(run)
        m["answer_reach"] = cmp.answer_reach(run, labels) if labels else None
        measures.append({"name": name, **m})
    out = {"runs": [r.run_id for r in loaded], "measures": measures, "judged": False, "model": None,
           "pairs": None, "gate": None, "detail_file": None}
    if use_judge:
        if not (allow_judge or os.environ.get("ARTMIND_COMPARE_JUDGE") == "1"):
            raise click.ClickException("--judge sends questions and answers to the LLM provider: set "
                                       "ARTMIND_COMPARE_JUDGE=1 in this vault's config, or pass --allowJudge")
        from artmind.traces import judge

        llm, chosen = judge.default_llm(model)
        judged, detail = {}, {}
        for key, plist in pairs.items():
            left_id, right_id = key.split("__")
            verdicts = {p.seq: judge.cached_judge(loaded[names.index(left_id)].run_id,
                                                  loaded[names.index(right_id)].run_id, p.seq, p.question,
                                                  p.expected, p.left.answer, p.right.answer, llm=llm, model=chosen)
                        for p in plist}
            counts = {k: sum(1 for v in verdicts.values() if v.verdict == k)
                      for k in ("same", "minor", "material", "unjudged")}
            better = {k: sum(1 for v in verdicts.values() if v.better == k) for k in ("left", "right", "equal")}
            judged[key] = {"counts": counts, "better": better,
                           "material_qids": [p.qid for p in plist if verdicts[p.seq].verdict == "material"],
                           "_verdicts": {s: {"verdict": v.verdict, "better": v.better} for s, v in verdicts.items()}}
            detail[key] = {"questions": [{"seq": p.seq, "qid": p.qid, "verdict": verdicts[p.seq].verdict,
                                          "better": verdicts[p.seq].better, "reason": verdicts[p.seq].reason}
                                         for p in plist]}
        g = gate_mod.evaluate(measures[0], measures[1], measures[2],
                              judged["classic_a__classic_b"]["_verdicts"], judged["classic_a__bundle"]["_verdicts"],
                              judged["classic_b__bundle"]["_verdicts"])
        for v in judged.values():
            v.pop("_verdicts")
        out.update(judged=True, model=chosen, pairs=judged, gate=g, detail_file=str(_write_compare_detail(
            {"runs": out["runs"], "model": chosen, "pairs": detail})))
    _echo_json(out, compact)
```

Add these helpers next to it:

```python
def _compare_labels(labels_file):
    from artmind.query_plan import labels as plan_labels

    path = labels_file or _default_labels_path_if_present()
    if path is None:
        return []
    try:
        return plan_labels.load_labels(path)
    except plan_labels.LabelError as exc:
        raise click.ClickException(f"invalid labels file {path}: {exc}") from exc


def _write_compare_detail(report: dict) -> Path:
    from datetime import datetime

    path = Path(LOGS_DIR) / f"compare-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
```

`_default_labels_path_if_present()`: find how `plan replay` resolves its default labels path (`_labels_path` in cli.py). Return that path if the file exists, else None. `--labels` given and missing is an error.

In `gate.evaluate`, add the `answer_reach` row: when `classic_a["answer_reach"]` and `bundle["answer_reach"]` are both present, `ok` is `bundle.reached >= classic_a.reached`; otherwise `None`. Update Task 4's tests if they pass dicts without `answer_reach`: use `.get`.

`_write_compare_detail` uses `write_text(`, which the contract test's write check looks for in callbacks. It sits in a helper, not in the callback, so the check passes. If it does not, add `"traces compare": "writes only its own judgement cache and a private detail file under logs/"` to `_READ_ONLY` in `test/test_cli_conventions.py`.

Add `ARTMIND_COMPARE_JUDGE` to `artmind/env.example`, commented, with one line on what it sends where.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_traces_compare_cli.py test/test_traces_compare.py test/test_traces_judge.py test/test_traces_gate.py test/test_cli_conventions.py test/test_cli_guide.py -q`
Expected: PASS.

- [ ] **Step 5: Commit.** `feat(traces): artmind traces compare reads the P3b gate from three benchmark runs (P3 S10)`

### Task 6: Gate run and docs

- [ ] **Step 1: Full suite.** `uv run --group dev pytest -q test/`. Expected: all pass.
- [ ] **Step 2: Dry check on real data, read-only and without the judge.** In banking_corpus, run the two classic runs from 2026-11-07 twice as A and B, with no bundle run yet. That shows the command works on real rows. Use the env stripping from the handover:

```bash
cd ~/artmind_vaults/banking_corpus && env -u ANTHROPIC_BASE_URL ARTMIND_NO_PROXY=1 uv run --project <this worktree> python -I -c "from artmind.traces import compare; r=compare.load_runs(['f73ad456','417025bb']); print([compare.measures(x) for x in r]); print(len(compare.pair(*r)))"
```

Expected: two measure dicts and 36 pairs. Report the numbers: counts only.
- [ ] **Step 3: Docs.**
  - **CLAUDE.md:** in the `artmind/traces/` row, add `compare` / `judge` / `gate` and the `artmind traces compare --runs A,B,BUNDLE [--judge]` command, with the consent rule (`ARTMIND_COMPARE_JUDGE=1` or `--allowJudge`; sends questions and answers to the provider; reasons only in `logs/compare-*.json`). Add `traces compare` to the command routing quick reference.
  - **Spec §9.2:** note the command is `traces compare` (not `traces stats --compareRuns`), with the reason.
  - **Handover:** S10 built, and the user steps:
    1. `just dev-install` from the P3 worktree after the merge.
    2. In each vault, three benchmark runs from the admin console on one build: Contract classic, classic again, bundle. Same question file.
    3. Rate the answers in the admin UI if you want the rating row; it is optional.
    4. Run, in each vault:
       ```bash
       artmind traces compare --runs <classic A>,<classic B>,<bundle> --judge --allowJudge --compact
       ```
       Then read `gate.passed` and `gate.reasons`.
- [ ] **Step 4: Commit.** `docs: traces compare and the P3b gate in CLAUDE.md, the spec and the handover (P3 S10)`

---

## Self-review notes

- **Spec coverage, §9.2:**
  - rated good ≥ classic − 1 → Task 4 `rated_good`;
  - answer reach ≥ classic → Task 5 `answer_reach`;
  - commands lower, latency lower, cost not higher → Task 4;
  - variance band (three runs, judged balance within the A–B spread, no question materially worse than both) → Tasks 3–4;
  - judge opt-in per vault → Task 5 consent rule;
  - user ratings stay the final word → the rating row is a gate row, and the judge never overrides a rating.
- **The judge's own reliability:**
  - Order bias is cancelled by a seeded swap.
  - Unusable answers are `unjudged` and not cached.
  - The judge never learns the contract.
  - A judge model call fails soft.
  - The spec does not ask for an agreement check between judge and ratings, so it is out of S10. When ratings exist, the detail file holds both, which allows a later check.
- **Not in S10:** turning the gate result into the `ARTMIND_AGENT_CONTRACT` default (S13), the fast-path switch (S12), the subplan node (S11).
