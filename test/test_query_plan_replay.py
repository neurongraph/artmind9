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
    report = replay.replay([_label()], "lexical", repeat=2, plan_fn=lambda q, d, classifier, today: next(plans))
    assert report["unstable"] == ["L001"] and report["outcomes"]["correct"] == 1


def test_stable_plans_are_not_flagged():
    report = replay.replay([_label()], "lexical", repeat=3, plan_fn=lambda q, d, classifier, today: _plan())
    assert report["unstable"] == [] and report["gate"]["repeat"] == 3


def test_a_failing_plan_is_an_error_not_a_crash():
    def boom(q, d, classifier="lexical", today=None):
        raise RuntimeError("neo4j down")

    report = replay.replay([_label()], "lexical", plan_fn=boom)
    assert report["outcomes"]["error"] == 1 and report["errors"][0]["error"] == "neo4j down"


def test_the_gate():
    report = replay.summarise([
        {"id": "L1", "outcome": "correct", "mismatches": [], "expected_route": "view", "latency_ms": 5.0},
        {"id": "L2", "outcome": "wrong", "mismatches": ["route"], "expected_route": "search", "latency_ms": 9.0},
    ], unstable=[], classifier="lexical", repeat=4)
    assert report["gate"] == {"view_wrong": 0, "wrong_rate": 0.5, "repeat": 4, "errors": 0, "passed": False}
    assert report["latency_ms"] == {"p50": 9.0, "p95": 9.0}


def test_detail_goes_to_the_logs_dir(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    path = replay.write_detail({"classifier": "lexical", "results": []})
    assert path.parent == tmp_path and json.loads(path.read_text())["classifier"] == "lexical"


def test_errors_fail_the_gate():
    results = [{"id": f"L{i}", "outcome": "error", "mismatches": [], "expected_route": "search",
                "latency_ms": None, "error_type": "RuntimeError", "error": "x"} for i in range(3)]
    report = replay.summarise(results, unstable=[], classifier="lexical", repeat=4)
    assert report["gate"]["errors"] == 3 and report["gate"]["passed"] is False
    good = [{"id": "L1", "outcome": "correct", "mismatches": [], "expected_route": "search", "latency_ms": 1.0}]
    assert replay.summarise(good, [], "lexical", 4)["gate"]["passed"] is True


def _fell_back(reason):
    plan = _plan(route="search", view=None)
    plan["classifier"].update(fell_back=True, reason=reason)
    return plan


def test_fallbacks_are_counted_and_fail_a_decision_gate():
    labels = [_label(id="L001", question="one", route="search", view=None, params={}),
              _label(id="L002", question="two", route="search", view=None, params={})]
    reasons = {"one": "timeout", "two": "breaker_open"}
    report = replay.replay(labels, "decision", repeat=4,
                           plan_fn=lambda q, d, classifier, today: _fell_back(reasons[q]))
    assert report["fell_back"] == 2 and report["fallback_reasons"] == ["breaker_open", "timeout"]
    assert report["gate"]["passed"] is False
    lexical = replay.replay(labels[:1], "lexical", repeat=4,
                            plan_fn=lambda q, d, classifier, today: _fell_back("timeout"))
    assert lexical["fell_back"] == 1 and lexical["gate"]["passed"] is True


def test_an_unquoted_yaml_date_scores_against_the_iso_string(tmp_path):
    import datetime

    from artmind.query_plan import labels as plan_labels

    data = {"version": 1, "labels": [{
        "id": "L001", "question": "q", "source": "written", "domains": ["journal"], "route": "view",
        "view": "visits_by_year",
        "params": {"day": datetime.date(2025, 3, 4), "span": [datetime.date(2025, 1, 1), datetime.datetime(2025, 2, 1, 9, 30)]}}]}
    [label] = plan_labels.parse_labels(data, tmp_path, view_params={"visits_by_year": {"day", "span"}})
    assert label.params == {"day": "2025-03-04", "span": ["2025-01-01", "2025-02-01T09:30:00"]}
    plan = _plan()
    plan["view_match"]["params"] = {"day": {"type": "date", "value": "2025-03-04"},
                                    "span": {"type": "list", "value": ["2025-01-01", "2025-02-01T09:30:00"]}}
    assert replay.score(label, plan)["outcome"] == "correct"


def test_replay_aborts_after_three_consecutive_identical_errors():
    calls = []

    def boom(q, d, classifier="lexical", today=None):
        calls.append(q)
        raise RuntimeError("neo4j down")

    labels = [_label(id=f"L00{i}") for i in range(1, 6)]
    report = replay.replay(labels, "lexical", plan_fn=boom)
    assert len(calls) == 3 and report["outcomes"]["error"] == 5
    assert [e["error_type"] for e in report["errors"]] == ["RuntimeError"] * 3 + ["aborted"] * 2


def test_a_success_resets_the_error_streak():
    outcomes = iter(["err", "err", "ok", "err", "err", "ok"])

    def flaky(q, d, classifier="lexical", today=None):
        if next(outcomes) == "err":
            raise RuntimeError("x")
        return _plan()

    labels = [_label(id=f"L00{i}") for i in range(1, 7)]
    assert replay.replay(labels, "lexical", plan_fn=flaky)["outcomes"]["error"] == 4


def test_one_date_for_the_whole_run():
    from datetime import date

    seen = []

    def spy(q, d, classifier, today):
        seen.append(today)
        return _plan()

    replay.replay([_label(id="L001"), _label(id="L002")], "lexical", repeat=2, plan_fn=spy)
    assert len(seen) == 4 and len(set(seen)) == 1 and isinstance(seen[0], date)


def test_the_model_override_reaches_the_plan_fn_only_when_given():
    seen = []

    def spy(q, d, classifier, today, **kw):
        seen.append(kw)
        return _plan()

    replay.replay([_label()], "decision", plan_fn=spy)
    replay.replay([_label()], "decision", model="m1", plan_fn=spy)
    assert seen == [{}, {"model": "m1"}]


# ── CLI ──────────────────────────────────────────────────────────────────────

SECRET = "Quarterly zebra inventory"


def _labels_file(tmp_path, ids=("L001",)):
    body = "".join(
        f"  - id: {i}\n    question: {SECRET} {i}\n    source: written\n    domains: [journal]\n"
        "    route: search\n    params: {}\n" for i in ids)
    path = tmp_path / "labels.yaml"
    path.write_text("version: 1\nlabels:\n" + body, encoding="utf-8")
    return path


def _cli(monkeypatch, tmp_path, plan_fn, args, ids=("L001",)):
    import paths

    from artmind.cli import cli

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("artmind.graph_query.domains_overview", lambda *a, **k: {"rows": []})
    monkeypatch.setattr("artmind.query_plan.planner.plan", plan_fn)
    return CliRunner().invoke(cli, ["plan", "replay", "--file", str(_labels_file(tmp_path, ids)), "--compact", *args])


def test_the_cli_summary_carries_no_question_text(tmp_path, monkeypatch):
    seen = []

    def fake_plan(question, domains=None, *, classifier="auto", today=None, **kw):
        seen.append((question, domains, classifier))
        return _plan(route="search", view=None)

    result = _cli(monkeypatch, tmp_path, fake_plan, ["--repeat", "4"])
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    report = json.loads(result.output)["reports"]["lexical"]
    assert report["outcomes"]["correct"] == 1 and "results" not in report and report["gate"]["passed"] is True
    assert seen == [(f"{SECRET} L001", None, "lexical")] * 4
    detail = json.loads(open(report["detail_file"], encoding="utf-8").read())
    assert detail["results"][0]["id"] == "L001"


def test_a_failing_plans_exception_text_never_reaches_stdout(tmp_path, monkeypatch):
    def boom(question, domains=None, **kw):
        raise RuntimeError(f"cannot plan {question}")

    result = _cli(monkeypatch, tmp_path, boom, [])
    assert SECRET not in result.output
    report = json.loads(result.output)["reports"]["lexical"]
    assert report["errors"] == [{"id": "L001", "error_type": "RuntimeError"}]
    assert SECRET in open(report["detail_file"], encoding="utf-8").read()  # private, gitignored logs
    assert result.exit_code == 1


def test_a_failed_gate_exits_1(tmp_path, monkeypatch):
    result = _cli(monkeypatch, tmp_path, lambda q, d=None, **kw: _plan(route="search", view=None), ["--repeat", "2"])
    assert result.exit_code == 1 and json.loads(result.output)["reports"]["lexical"]["gate"]["passed"] is False


def test_both_makes_two_reports_and_passes_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTMIND_DECISION_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("ARTMIND_DECISION_MODEL", "m0")
    seen = []

    def fake_plan(question, domains=None, *, classifier="auto", today=None, **kw):
        seen.append((classifier, kw))
        return _plan(route="search", view=None)

    result = _cli(monkeypatch, tmp_path, fake_plan, ["--classifier", "both", "--model", "m9"])
    assert sorted(json.loads(result.output)["reports"]) == ["decision", "lexical"]
    assert seen == [("lexical", {"model": "m9"}), ("decision", {"model": "m9"})]


def test_given_domains_passes_the_label_domains(tmp_path, monkeypatch):
    seen = []

    def fake_plan(question, domains=None, **kw):
        seen.append(domains)
        return _plan(route="search", view=None)

    _cli(monkeypatch, tmp_path, fake_plan, ["--givenDomains"])
    assert seen == [["journal"]]


def test_the_cli_probes_neo4j_before_replaying(tmp_path, monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    calls = []
    monkeypatch.setattr("artmind.graph_query.domains_overview",
                        lambda *a, **k: (_ for _ in ()).throw(ServiceUnavailable("no route")))
    import paths

    from artmind.cli import cli

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("artmind.query_plan.planner.plan", lambda *a, **k: calls.append(1))
    result = CliRunner().invoke(cli, ["plan", "replay", "--file", str(_labels_file(tmp_path))])
    assert result.exit_code != 0 and "Neo4j is unreachable" in result.output and calls == []


def test_the_cli_refuses_a_decision_classifier_without_an_endpoint(tmp_path, monkeypatch):
    from artmind.cli import cli

    monkeypatch.delenv("ARTMIND_DECISION_URL", raising=False)
    result = CliRunner().invoke(cli, ["plan", "replay", "--file", str(_labels_file(tmp_path)), "--classifier", "decision"])
    assert result.exit_code != 0 and "ARTMIND_DECISION_URL" in result.output


def test_stable_ignores_catalogue_source_and_build_time():
    base = _plan()
    a = {**base, "catalogue": {"journal": {"source": "live", "built_at": "t1", "names": 3, "truncated": False}}}
    b = {**base, "catalogue": {"journal": {"source": "memory", "built_at": "t2", "names": 3, "truncated": False}}}
    c = {**base, "catalogue": {"journal": {"source": "live", "built_at": "t1", "names": 4, "truncated": False}}}
    assert replay._stable(a) == replay._stable(b)
    assert replay._stable(a) != replay._stable(c)
