"""`artmind query run --plan` (P3 S2): executes a given plan in process. Invented plan, no Neo4j."""

import json

from click.testing import CliRunner

from artmind import _entry
from artmind.cli import cli


def _plan_file(tmp_path, cmd="artmind query domains-overview --compact"):
    plan = {"program": [{"id": "s1", "kind": "run", "run": cmd, "filled": True}], "guards": [],
            "route": {"id": "inventory"}, "suggested_steps": [], "fallbacks": [], "replacements": {}}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_run_a_given_plan(tmp_path, monkeypatch):
    from artmind import cli as cli_mod

    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": [{"domain": "bank"}]})
    out = CliRunner().invoke(cli, ["query", "run", "--plan", str(_plan_file(tmp_path)), "--compact"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.stdout)
    assert payload["command"] == "query run"
    assert [p["id"] for p in payload["path"]] == ["s1"] and payload["path"][0]["exit_code"] == 0
    assert payload["escalate"] is None


def test_a_question_or_a_plan_is_required():
    out = CliRunner().invoke(cli, ["query", "run", "--compact"])
    assert out.exit_code == 1 and "question" in out.output


def test_a_bad_plan_file_is_bad_input(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text("{not json")
    out = CliRunner().invoke(cli, ["query", "run", "--plan", str(bad)])
    assert out.exit_code == 1


def test_run_inside_serve_holds_the_lock_once(tmp_path, monkeypatch):
    from artmind import cli as cli_mod
    from artmind import server

    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": [{"domain": "bank"}]})
    body = server.run_cli_args(["query", "run", "--plan", str(_plan_file(tmp_path)), "--compact"])
    assert body["exit_code"] == 0, body
    assert json.loads(body["output"])["path"][0]["exit_code"] == 0


def test_file_flags_run_in_process():
    assert _entry._proxiable(["query", "run", "q"]) is True
    assert _entry._proxiable(["query", "run", "--plan", "p.json"]) is False
    assert _entry._proxiable(["query", "run", "--plan=p.json"]) is False
    assert _entry._proxiable(["query", "run", "--previous", "-", "q"]) is False
    assert _entry._proxiable(["query", "run", "--each", "c.yaml"]) is False
    assert _entry._proxiable(["query", "plan", "--previous", "-", "q"]) is False
    assert _entry._proxiable(["query", "vector-text", "--plan", "x"]) is True
