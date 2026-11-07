"""`artmind query run --each` (P3 S7). Invented claims; the step function is faked."""

import json

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_run import each
from artmind.query_run.capture import StepResult


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)
    monkeypatch.setattr(each, "_default_step", lambda: (lambda argv, sid: StepResult(
        sid, list(argv), 0, None, {"command": "query vector-text", "rows": []}, 1.0, 1)))


def test_each_prints_a_summary_and_never_the_bundles(tmp_path):
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "invented claim text"}\n')
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(claims), "--domain", "bank", "--compact"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.stdout)
    assert payload["command"] == "query run" and payload["items"] == 1 and payload["handle"]
    assert "invented claim text" not in out.stdout


def test_each_needs_a_domain(tmp_path):
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "x"}\n')
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(claims)])
    assert out.exit_code == 1 and "--domain" in out.output


def test_each_with_a_question_or_plan_is_refused(tmp_path):
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "x"}\n')
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(claims), "--domain", "b", "a question"])
    assert out.exit_code == 1


def test_a_bad_claims_file_is_bad_input(tmp_path):
    bad = tmp_path / "c.jsonl"
    bad.write_text("nope\n")
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(bad), "--domain", "b"])
    assert out.exit_code == 1 and "line 1" in out.output


def test_each_item_is_a_child_span_of_the_run(tmp_path, monkeypatch):
    from artmind.traces import store

    store.insert_turn("t1", source="admin", question="q")
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "a"}\n{"id": "c2", "claim": "b"}\n')
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(claims), "--domain", "bank", "--compact"])
    assert out.exit_code == 0, out.output
    spans = store.list_spans("t1")
    run = next(s for s in spans if s["command"] == "query run")
    items = [s for s in spans if s["parent_span_id"] == run["span_id"]]
    assert [s["command"] for s in items] == ["query run item", "query run item"]


def _claims(tmp_path):
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "x"}\n')
    return claims


def test_each_exits_3_when_every_step_failed_on_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(each, "_default_step", lambda: (lambda argv, sid: StepResult(sid, list(argv), 3, "store", None, 1.0, 0)))
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(_claims(tmp_path)), "--domain", "b", "--compact"])
    assert out.exit_code == 3, out.output
    assert json.loads(out.stdout)["store_failure"] is True


@pytest.mark.parametrize("extra", [["--exclude", "doc"], ["--previous", "p.json"], ["--classifier", "lexical"]])
def test_each_refuses_options_it_ignores(tmp_path, extra):
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(_claims(tmp_path)), "--domain", "b", *extra])
    assert out.exit_code == 1 and "--each" in out.output


def test_each_accepts_the_default_classifier_spelled_out(tmp_path):
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(_claims(tmp_path)), "--domain", "b",
                                   "--classifier", "auto", "--compact"])
    assert out.exit_code == 0, out.output


def test_each_removes_old_run_files_and_traces_prune_removes_them(tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.cli.LOGS_DIR", tmp_path / "logs")
    import os
    import time

    runs = tmp_path / "logs" / "runs"
    runs.mkdir(parents=True)
    old = runs / "aaaaaaaaaaaa.jsonl"
    old.write_text("{}\n")
    stamp = time.time() - 48 * 3600
    os.utime(old, (stamp, stamp))
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(_claims(tmp_path)), "--domain", "b", "--compact"])
    assert out.exit_code == 0, out.output
    assert not old.exists()
    assert len(list(runs.glob("*.jsonl"))) == 1
    out = CliRunner().invoke(cli, ["traces", "prune", "--compact"])
    assert out.exit_code == 0, out.output
    assert not list(runs.glob("*.jsonl"))
