"""Parser fallback ladder + CRUD for the batch benchmark driver."""

import json

import pytest

from artmind import benchmark


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


# ── parsing: strategy 1 — heading blocks with **Question:**/**Evaluation comment:** ──

STRUCTURED_TEXT = """\
# Corpus

## A. Section

### Q01 — First question

**Question:** What is the capital of France?

**Evaluation comment:** Should say Paris.

### Q02 — Second question

**Question:** What is 2+2?

**Evaluation comment:** Should say 4.
"""


def test_parse_structured_headings():
    rows = benchmark._parse_questions(STRUCTURED_TEXT)
    assert len(rows) == 2
    assert rows[0] == {
        "seq": 1, "qid": "Q01", "title": "First question",
        "question": "What is the capital of France?",
        "eval_comment": "Should say Paris.",
    }
    assert rows[1]["qid"] == "Q02"
    assert rows[1]["question"] == "What is 2+2?"


def test_parse_structured_without_eval_comment():
    text = "### Q07 — Only question\n\n**Question:** What color is the sky?\n"
    rows = benchmark._parse_questions(text)
    assert len(rows) == 1
    assert rows[0]["qid"] == "Q07"
    assert rows[0]["question"] == "What color is the sky?"
    assert rows[0]["eval_comment"] is None


# ── parsing: strategy 2 — **Question:** labels without heading scoping ──

LABELS_NO_HEADINGS_TEXT = """\
Some intro text that isn't a heading.

**Question:** What is the capital of Spain?

**Evaluation comment:** Should say Madrid.

**Question:** What is the capital of Italy?

**Evaluation comment:** Should say Rome.
"""


def test_parse_question_labels_without_headings():
    rows = benchmark._parse_questions(LABELS_NO_HEADINGS_TEXT)
    assert len(rows) == 2
    assert rows[0]["title"] is None
    assert rows[0]["qid"] == "Q01"
    assert rows[0]["question"] == "What is the capital of Spain?"
    assert rows[0]["eval_comment"] == "Should say Madrid."
    assert rows[1]["question"] == "What is the capital of Italy?"
    assert rows[1]["eval_comment"] == "Should say Rome."


# ── parsing: strategy 3 — headings without **Question:** labels, verbatim ──

HEADINGS_NO_LABELS_TEXT = """\
### First topic

What is the capital of Germany?

### Second topic

What is the capital of Japan?
"""


def test_parse_heading_blocks_without_labels():
    rows = benchmark._parse_questions(HEADINGS_NO_LABELS_TEXT)
    assert len(rows) == 2
    assert rows[0]["title"] == "First topic"
    assert rows[0]["question"] == "What is the capital of Germany?"
    assert rows[0]["eval_comment"] is None
    assert rows[1]["title"] == "Second topic"


# ── parsing: strategy 4 — flat list of '?'-terminated lines ──

FLAT_LINES_TEXT = """\
What is the capital of Brazil?
Not a question, just a statement.
What is the capital of Canada?
"""


def test_parse_flat_question_lines():
    rows = benchmark._parse_questions(FLAT_LINES_TEXT)
    assert len(rows) == 2
    assert rows[0]["title"] is None
    assert rows[0]["question"] == "What is the capital of Brazil?"
    assert rows[1]["question"] == "What is the capital of Canada?"


def test_parse_empty_or_questionless_text_returns_empty_list():
    assert benchmark._parse_questions("") == []
    assert benchmark._parse_questions("no questions here at all, just prose.") == []


def test_parse_questions_file(tmp_path):
    path = tmp_path / "q.md"
    path.write_text(STRUCTURED_TEXT, encoding="utf-8")
    rows = benchmark._parse_questions_file(path)
    assert len(rows) == 2


# ── CRUD ─────────────────────────────────────────────────────────────

def _make_run(**kwargs):
    questions = benchmark._parse_questions(STRUCTURED_TEXT)
    return benchmark._create_benchmark_run("corpus/questions.md", questions, **kwargs)


def test_create_and_get_benchmark_run():
    run_id = _make_run(domain_hint="banking", backend="claude-sdk", name="test run")
    run = benchmark._get_benchmark_run(run_id)
    assert run["status"] == "queued"
    assert run["question_count"] == 2
    assert run["domain_hint"] == "banking"
    assert run["name"] == "test run"
    assert len(run["questions"]) == 2
    assert run["questions"][0]["qid"] == "Q01"


def test_get_benchmark_question_full_detail():
    run_id = _make_run()
    q = benchmark._get_benchmark_question(run_id, 1)
    assert q["question"] == "What is the capital of France?"
    assert q["eval_comment"] == "Should say Paris."
    assert q["status"] == "queued"
    assert q["trace"] == []


def test_get_missing_run_or_question_returns_none():
    assert benchmark._get_benchmark_run("nope") is None
    run_id = _make_run()
    assert benchmark._get_benchmark_question(run_id, 999) is None


def test_update_benchmark_question_and_run_progress():
    run_id = _make_run()
    benchmark._update_benchmark_question(
        run_id, 1,
        status="completed", answer_text="Paris",
        trace_json=json.dumps([{"type": "tool_call", "name": "vector-text"}]),
        turns=2, duration_s=3.5, cost_usd=0.01, completed_at="t",
    )
    benchmark._update_benchmark_run(run_id, processed_count=1)

    q = benchmark._get_benchmark_question(run_id, 1)
    assert q["status"] == "completed"
    assert q["answer_text"] == "Paris"
    assert q["trace"] == [{"type": "tool_call", "name": "vector-text"}]
    assert q["turns"] == 2

    run = benchmark._get_benchmark_run(run_id)
    assert run["processed_count"] == 1
    # untouched question stays queued
    assert run["questions"][1]["status"] == "queued"


def test_fetch_active_and_completed_runs():
    run_id = _make_run()
    assert any(r["run_id"] == run_id for r in benchmark._fetch_active_benchmark_runs())
    assert not any(r["run_id"] == run_id for r in benchmark._fetch_completed_benchmark_runs())

    benchmark._update_benchmark_run(run_id, status="completed", completed_at="2026-01-01T00:00:00")

    assert any(r["run_id"] == run_id for r in benchmark._fetch_completed_benchmark_runs())
    assert not any(r["run_id"] == run_id for r in benchmark._fetch_active_benchmark_runs())


def test_list_benchmark_runs_filters_by_status():
    run_id = _make_run()
    assert any(r["run_id"] == run_id for r in benchmark._list_benchmark_runs())
    assert any(r["run_id"] == run_id for r in benchmark._list_benchmark_runs(status_filter="queued"))
    assert not any(r["run_id"] == run_id for r in benchmark._list_benchmark_runs(status_filter="completed"))


def test_cancel_queued_run():
    run_id = _make_run()
    result = benchmark._cancel_benchmark_run(run_id)
    assert result == {"run_id": run_id, "cancelled": True, "status": "cancelled"}
    assert benchmark._get_benchmark_run(run_id)["status"] == "cancelled"


def test_cancel_already_completed_run_is_noop():
    run_id = _make_run()
    benchmark._update_benchmark_run(run_id, status="completed", completed_at="t")
    result = benchmark._cancel_benchmark_run(run_id)
    assert result == {"run_id": run_id, "cancelled": False, "status": "completed"}


def test_cancel_missing_run_raises():
    with pytest.raises(ValueError):
        benchmark._cancel_benchmark_run("nope")


# ── export ───────────────────────────────────────────────────────────

def test_export_markdown_includes_answer_and_grading_notes():
    run_id = _make_run(name="export test")
    benchmark._update_benchmark_question(
        run_id, 1, status="completed", answer_text="Paris", completed_at="t"
    )

    md = benchmark._export_benchmark_markdown(run_id)
    assert "export test" in md
    assert "What is the capital of France?" in md
    assert "Paris" in md
    # eval_comment is a grading note included in the export, but never sent to the agent
    assert "Should say Paris." in md
    assert "not shown to artmind" in md


def test_export_json_round_trips_question_detail():
    run_id = _make_run()
    benchmark._update_benchmark_question(
        run_id, 1, status="completed", answer_text="Paris", completed_at="t"
    )
    data = benchmark._export_benchmark_json(run_id)
    assert data["run_id"] == run_id
    assert data["questions"][0]["answer_text"] == "Paris"
    assert data["questions"][1]["status"] == "queued"


def test_export_missing_run_returns_none():
    assert benchmark._export_benchmark_markdown("nope") is None
    assert benchmark._export_benchmark_json("nope") is None
