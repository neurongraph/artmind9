"""`artmind traces compare` (spec 2026-11-08 §3). Invented runs; judge and grader are faked."""

import json

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from test_traces_compare import _isolated_db, _run  # noqa: F401


@pytest.fixture
def runs():
    a = _run("a", "classic", ["ans one", "ans two"])
    d = _run("d", "classic", ["ans one", "ans two d"])
    v = _run("v", "p2-1", ["ans one", "ans two v"])
    return a, d, v


def _fake_llm(monkeypatch):
    from artmind.traces import judge

    def llm(prompt):
        if "MUST contain" in prompt:
            return json.dumps({"must": [True], "must_not": [], "grade": "correct", "reason": "r"})
        return json.dumps({"verdict": "minor", "better": "equal", "reason": "secret reason"})
    monkeypatch.setattr(judge, "default_llm", lambda model=None: (llm, "fake-model"))


def test_measures_without_judge_print_no_text(runs, tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.cli.LOGS_DIR", tmp_path / "logs")
    a, d, _ = runs
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", f"{a},{d}", "--compact"])
    assert out.exit_code == 0, out.output
    p = json.loads(out.stdout)
    assert len(p["measures"]) == 2 and p["band"] is None and p["variant"] is None
    assert "invented question" not in out.stdout and "ans two" not in out.stdout


def test_needs_two_baseline_runs(runs):
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", runs[0]])
    assert out.exit_code == 1 and "two" in out.output


def test_judge_needs_consent(runs, monkeypatch):
    monkeypatch.delenv("ARTMIND_COMPARE_JUDGE", raising=False)
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", f"{runs[0]},{runs[1]}", "--judge"])
    assert out.exit_code == 1 and "ARTMIND_COMPARE_JUDGE" in out.output


def test_judged_and_graded_with_variant(runs, tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.cli.LOGS_DIR", tmp_path / "logs")
    _fake_llm(monkeypatch)
    qfile = tmp_path / "questions.md"
    qfile.write_text("## Q01\n**Question:** invented question 1?\n**Evaluation comment:** Must: fact one\n\n"
                     "## Q02\n**Question:** invented question 2?\n**Evaluation comment:** Must: fact two\n")
    a, d, v = runs
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", f"{a},{d}", "--variant", v,
                                   "--questions", str(qfile), "--judge", "--grade", "--allowJudge", "--compact"])
    assert out.exit_code == 0, out.output
    p = json.loads(out.stdout)
    assert p["band"] == {"balance": 0, "material": 0}
    assert p["variant"]["gate"]["passed"] is True
    assert all(g["must"] == {"found": 2, "total": 2} for g in p["grades"].values())
    assert "secret reason" not in out.stdout
    detail = json.loads(open(p["detail_file"]).read())
    assert "secret reason" in json.dumps(detail)


def test_gate_keys_verdicts_in_the_anchor_seq_space(runs, tmp_path, monkeypatch):
    """A baseline run that asked the same questions in another order must still line up with the anchor."""
    import artmind.db as db  # noqa: F401
    from artmind.traces import judge, store

    monkeypatch.setattr("artmind.cli.LOGS_DIR", tmp_path / "logs")
    a, d, v = runs
    conn = store._connect()
    # d asked question 2 first: swap the question texts of its two rows
    conn.execute("UPDATE benchmark_questions SET question='tmp' WHERE run_id=? AND seq=1", (d,))
    conn.execute("UPDATE benchmark_questions SET question='invented question 1?' WHERE run_id=? AND seq=2", (d,))
    conn.execute("UPDATE benchmark_questions SET question='invented question 2?' WHERE run_id=? AND seq=1", (d,))
    conn.commit()
    conn.close()

    def llm(prompt):
        if "MUST contain" in prompt:
            return json.dumps({"must": [True], "must_not": [], "grade": "correct", "reason": "r"})
        if "invented question 2?" in prompt:  # the baseline runs' answers beat the variant's
            a = prompt.split("Answer A:\n")[1].split("\n\nAnswer B:")[0]
            better = "B" if a == "ans two v" else "A"
            return json.dumps({"verdict": "material", "better": better, "reason": "r"})
        return json.dumps({"verdict": "same", "better": "equal", "reason": "r"})
    monkeypatch.setattr(judge, "default_llm", lambda model=None: (llm, "fake-model"))
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", f"{a},{d}", "--variant", v,
                                   "--judge", "--allowJudge", "--compact"])
    assert out.exit_code == 0, out.output
    p = json.loads(out.stdout)
    assert p["variant"]["gate"]["worse_than_all"] == [2]
    assert p["flagged"] == ["Q02"]
