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

    monkeypatch.setattr("artmind.cli.LOGS_DIR", tmp_path / "logs")
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
