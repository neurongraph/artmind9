"""`artmind query plan` (spec 2026-10-22 §4.1)."""

import json

from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_plan import planner


def test_the_command_prints_the_plan(monkeypatch):
    seen = {}

    def fake_plan(question, domains, execute=False, classifier="auto"):
        seen.update(question=question, domains=domains, execute=execute, classifier=classifier)
        return {"plan_version": 1, "steps": []}

    monkeypatch.setattr(planner, "plan", fake_plan)
    result = CliRunner().invoke(cli, ["query", "plan", "--domain", "a,b", "--execute", "--classifier", "lexical",
                                      "--compact", "who leads?"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"plan_version": 1, "steps": []}
    assert seen == {"question": "who leads?", "domains": ["a", "b"], "execute": True, "classifier": "lexical"}


def test_domain_is_optional(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains, **kw: {"domains_seen": domains})
    result = CliRunner().invoke(cli, ["query", "plan", "--compact", "who leads?"])
    assert json.loads(result.output) == {"domains_seen": None}


def test_a_plan_error_is_a_click_error(monkeypatch):
    def boom(*a, **k):
        raise planner.PlanError("the question is empty")

    monkeypatch.setattr(planner, "plan", boom)
    result = CliRunner().invoke(cli, ["query", "plan", " "])
    assert result.exit_code != 0 and "the question is empty" in result.output
