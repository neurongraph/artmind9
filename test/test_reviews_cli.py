"""`artmind review new/list/show/status/validate/reindex/leakcheck`."""

import json
import subprocess
import time

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.reviews import dossier as dz
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    store.insert_turn("t1", source="admin", question="How many trips did Person A take?", started_at=time.time())
    store.update_turn("t1", answer="Two.", status="done", retrieval_path="[]", domains='["d"]')


def _run(*args, ok=True):
    result = CliRunner().invoke(cli, ["review", *args])
    if ok:
        assert result.exit_code == 0, result.output
    return result


def _json(*args):
    return json.loads(_run(*args, "--compact").stdout)


def test_new_list_show():
    created = _json("new", "t1", "--stage", "routing")
    rid = created["review_id"]
    assert created["path"].endswith(f"{rid}.md")
    listed = _json("list")["reviews"]
    assert [(r["review_id"], r["status"], r["stage"], r["trace_ids"]) for r in listed] == [(rid, "open", "routing", ["t1"])]
    shown = _json("show", rid)
    assert shown["meta"]["review_id"] == rid and "Symptom" in shown["sections"]
    raw = _run("show", rid, "--markdown").stdout
    assert raw.startswith("---\nreview_id: ")
    assert store.list_turns()[0]["reviewed"] == 1


def test_new_refuses_a_second_open_review_unless_forced():
    _json("new", "t1")
    result = _run("new", "t1", ok=False)
    assert result.exit_code != 0 and "already has open review" in result.output
    _json("new", "t1", "--force")
    assert len(_json("list")["reviews"]) == 2


def test_new_unknown_trace_is_an_error():
    result = _run("new", "nope", ok=False)
    assert result.exit_code != 0 and "no trace" in result.output


def test_list_filters():
    a = _json("new", "t1", "--stage", "schema", "--element", "d/Person-WENT_TO->")["review_id"]
    _json("new", "t1", "--force", "--stage", "routing")
    assert [r["review_id"] for r in _json("list", "--stage", "schema")["reviews"]] == [a]
    assert [r["review_id"] for r in _json("list", "--element", "WENT_TO")["reviews"]] == [a]


def test_status_updates_frontmatter_log_and_index():
    rid = _json("new", "t1")["review_id"]
    _run("status", rid, "fixed", "--note", "patched the skill")
    d = dz.load(rid)
    assert d.meta["status"] == "fixed"
    assert d.sections["Log"].splitlines()[-1].endswith("status open → fixed: patched the skill")
    assert _json("list", "--status", "fixed")["reviews"][0]["review_id"] == rid


def test_validate_exit_codes():
    rid = _json("new", "t1")["review_id"]
    result = _run("validate", rid, "--compact", ok=False)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False


def test_reindex():
    _json("new", "t1")
    assert store.clear_review_index() == 1
    assert _json("reindex")["indexed"] == 1


def test_leakcheck_exit_codes(tmp_path):
    rid = _json("new", "t1")["review_id"]
    repo = tmp_path / "co"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True)
    assert _json("leakcheck", rid, "--repo", str(repo))["ok"] is True
    (repo / "a.py").write_text("x = 1\nq = 'How many trips did Person A take'\n")
    result = _run("leakcheck", rid, "--repo", str(repo), "--compact", ok=False)
    assert result.exit_code == 1 and json.loads(result.stdout)["hit_count"] >= 1
