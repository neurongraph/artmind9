"""Replay scoring (spec 2026-10-22 §4.6). Plans are synthetic dicts; no graph."""

import json

from click.testing import CliRunner

from artmind.query_plan import replay
from artmind.query_plan.labels import Label


def _label(**over):
    base = dict(id="L001", question="q", source="written", domains=("journal",), route="view",
                view="visits_by_year", params={"person": "Ada", "year": 2025})
    return Label(**{**base, **over})


def _plan(level="high", route="view", view="visits_by_year", person="Ada", year=2025, selected=("journal",),
          latency=12.0):
    return {
        "domains": {"selected": list(selected)},
        "route": {"id": route},
        "view_match": {"name": view, "params": {
            "person": {"type": "entity", "chosen": {"name": person}, "twins": False, "candidates": []},
            "year": {"type": "int", "value": year},
        }} if route == "view" else None,
        "confidence": {"level": level},
        "classifier": {"latency_ms": latency, "fell_back": False},
    }


def test_outcomes():
    assert replay.score(_label(), _plan())["outcome"] == "correct"
    wrong = replay.score(_label(), _plan(year=2024))
    assert (wrong["outcome"], wrong["mismatches"]) == ("wrong", ["param:year"])
    assert replay.score(_label(), _plan(level="low"))["outcome"] == "abstained"
    assert replay.score(_label(expect="low"), _plan(level="low"))["outcome"] == "correct_abstain"
    assert replay.score(_label(expect="low"), _plan())["mismatches"] == ["confidence"]


def test_a_parent_domain_covers_its_children():
    label = _label(domains=("bank.policy", "bank.cases"), route="search", view=None, params={})
    assert replay.score(label, _plan(route="search", view=None, selected=("bank",)))["outcome"] == "correct"
    assert "domains" in replay.score(label, _plan(route="search", view=None, selected=("journal",)))["mismatches"]


def test_twins_must_all_be_acceptable_names():
    label = _label(params={"person": ["Ada", "Ada"]})
    plan = _plan()
    plan["view_match"]["params"]["person"] = {"type": "entity", "chosen": None, "twins": True,
                                              "candidates": [{"name": "Ada"}, {"name": "ada"}]}
    assert replay.score(label, plan)["outcome"] == "correct"


def test_replay_finds_unstable_plans():
    plans = iter([_plan(), _plan(year=2024)])
    report = replay.replay([_label()], "lexical", repeat=2, plan_fn=lambda q, d, classifier: next(plans))
    assert report["unstable"] == ["L001"] and report["outcomes"]["correct"] == 1


def test_stable_plans_are_not_flagged():
    report = replay.replay([_label()], "lexical", repeat=3, plan_fn=lambda q, d, classifier: _plan())
    assert report["unstable"] == [] and report["gate"]["repeat"] == 3


def test_a_failing_plan_is_an_error_not_a_crash():
    def boom(q, d, classifier="lexical"):
        raise RuntimeError("neo4j down")

    report = replay.replay([_label()], "lexical", plan_fn=boom)
    assert report["outcomes"]["error"] == 1 and report["errors"][0]["error"] == "neo4j down"


def test_the_gate():
    report = replay.summarise([
        {"id": "L1", "outcome": "correct", "mismatches": [], "expected_route": "view", "latency_ms": 5.0},
        {"id": "L2", "outcome": "wrong", "mismatches": ["route"], "expected_route": "search", "latency_ms": 9.0},
    ], unstable=[], classifier="lexical", repeat=4)
    assert report["gate"] == {"view_wrong": 0, "wrong_rate": 0.5, "repeat": 4, "passed": False}
    assert report["latency_ms"] == {"p50": 9.0, "p95": 9.0}


def test_detail_goes_to_the_logs_dir(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    path = replay.write_detail({"classifier": "lexical", "results": []})
    assert path.parent == tmp_path and json.loads(path.read_text())["classifier"] == "lexical"


def test_the_cli_summary_carries_no_question_text(tmp_path, monkeypatch):
    import paths

    from artmind.cli import cli

    secret = "Quarterly zebra inventory"
    labels = tmp_path / "labels.yaml"
    labels.write_text(
        "version: 1\nlabels:\n"
        f"  - id: L001\n    question: {secret}\n    source: written\n    domains: [journal]\n"
        "    route: search\n    params: {}\n", encoding="utf-8")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    seen = []

    def fake_plan(question, domains=None, *, classifier="auto", **kw):
        seen.append((question, domains, classifier))
        return _plan(route="search", view=None)

    monkeypatch.setattr("artmind.query_plan.planner.plan", fake_plan)
    result = CliRunner().invoke(cli, ["plan", "replay", "--file", str(labels), "--compact", "--repeat", "2"])
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    payload = json.loads(result.output)
    report = payload["reports"]["lexical"]
    assert report["outcomes"]["correct"] == 1 and "results" not in report
    assert seen == [(secret, None, "lexical")] * 2
    detail = json.loads(open(report["detail_file"], encoding="utf-8").read())
    assert detail["results"][0]["id"] == "L001"


def test_the_cli_refuses_a_decision_classifier_without_an_endpoint(tmp_path, monkeypatch):
    from artmind.cli import cli

    monkeypatch.delenv("ARTMIND_DECISION_URL", raising=False)
    labels = tmp_path / "labels.yaml"
    labels.write_text(
        "version: 1\nlabels:\n  - id: L001\n    question: q\n    source: written\n"
        "    domains: [journal]\n    route: search\n    params: {}\n", encoding="utf-8")
    result = CliRunner().invoke(cli, ["plan", "replay", "--file", str(labels), "--classifier", "decision"])
    assert result.exit_code != 0 and "ARTMIND_DECISION_URL" in result.output
