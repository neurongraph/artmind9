"""Authored programs of the history, survey, search, path and reading routes (planner v2 P2b, slice P2).
Synthetic data only; fixtures come from test_query_plan_planner."""

import pytest

from artmind.query_plan import planner, program as prog, routes
from test_query_plan_planner import FakeClassifier, _answer, _plan, _use, world  # noqa: F401

OWNED = {
    "entity_history": "history", "time_history": "history", "conflicts": "one_step", "hierarchy": "one_step",
    "path": "one_step", "inventory": "one_step",
    "insights": "one_step", "filing": "one_step", "survey": "cross_document",
}


def _route(rid):
    return next(r for r in routes.load_routes() if r.id == rid)


@pytest.mark.parametrize("rid, kind", sorted(OWNED.items()))
def test_each_converted_route_loads_with_its_kind(rid, kind):
    route = _route(rid)
    assert route.authored_program and route.kind == kind and not route.chooses
    assert route.fallbacks == () and route.optional_steps == () and route.variants == {}
    assert len(prog.run_nodes(route.program)) <= prog.MAX_WRITTEN_RUN_NODES
    assert prog.executed_run_calls(route.program) <= prog.MAX_EXECUTED_RUN_CALLS


def test_superlative_chooses_between_snapshot_and_needle_programs():
    route = _route("superlative")  # B5: `insights_snapshot == 0` swaps the insights step for the text needle
    assert route.chooses and [c.kind for c in route.programs] == ["one_step", "one_step"]
    assert route.programs[0].when.text == "insights_snapshot == 0"
    assert not any("insights list" in n.run for n in prog.run_nodes(route.programs[0].program))


def test_history_routes_never_use_text2cypher():
    for rid in ("entity_history", "time_history"):
        assert not any("text2cypher" in n.run for n in prog.run_nodes(_route(rid).program))


def test_only_structured_keeps_v1_steps():
    assert [r.id for r in routes.load_routes() if not r.authored_program] == ["structured"]


def test_survey_is_the_always_merge_cross_document_program():
    route = _route("survey")
    assert [n.kind for n in route.program] == ["par", "pick"]
    assert [c.id for c in route.program[0].children] == ["needle", "wide"]
    assert route.program[1].when is None
    assert [(g.on, g.when.text, g.then.kind) for g in route.guards] == [
        ("wide", "truncated", "page"), ("u", "distinct_documents == 0", "escalate")]
    assert route.guards[0].then.page.k == 50 and "P43" in route.evidence


def test_search_is_one_cross_document_program_in_every_scope():
    route = _route("search")
    assert route.authored_program and not route.chooses and route.kind == "cross_document"
    assert [c.kind for c in route.programs] == ["cross_document"]
    assert [c.id for c in route.program[0].children] == ["needle", "wide"]
    assert route.program[1].when.text == "needle.score_drop < NEEDLE_MARGIN or needle.distinct_documents >= NEEDLE_SPREAD"
    assert {c.const for c in route.program[1].when.comparisons()} == {"NEEDLE_MARGIN", "NEEDLE_SPREAD"}
    assert [(g.on, g.then.kind) for g in route.guards] == [("wide", "page"), ("u", "escalate")]
    assert {"S2", "S19", "P14", "P43"} <= set(route.evidence)
    assert "how do i" in route.triggers and route.description.startswith("Any other factual question, or how to do something")


def test_procedure_is_no_longer_a_route():
    assert "procedure" not in {r.id for r in routes.load_routes()}


def test_a_graph_scope_search_plans_both_commands_with_the_rule(world):
    world["views"] = []
    result = _plan("What was the weather like?")
    assert result["route"]["id"] == "search" and result["program_kind"] == "cross_document"
    assert result["plan_facts"] == {}  # one program: nothing to choose, so no plan fact is evaluated
    assert [n["kind"] for n in result["program"]] == ["par", "pick"]
    assert [c["id"] for c in result["program"][0]["children"]] == ["needle", "wide"]
    assert result["program"][1]["when"] == "needle.score_drop < NEEDLE_MARGIN or needle.distinct_documents >= NEEDLE_SPREAD"
    assert [g["then"]["kind"] for g in result["guards"]] == ["page", "escalate"]
    assert [s["cmd"].split()[2] for s in result["steps"] + result["suggested_steps"]] == ["vector-text", "survey"]


def test_a_chunks_only_search_plans_the_same_program(world, monkeypatch):
    world["views"] = []
    world["overview"] = [{"domain": "journal", "document_count": 3, "documents": [], "top_classes": [],
                          "graph_extraction": False}]
    monkeypatch.setattr(planner, "_chunks_only", lambda selected, overview: True)
    result = _plan("What was the weather like?")
    assert result["route"]["id"] == "search" and result["program_kind"] == "cross_document"
    assert result["plan_facts"] == {}
    assert result["program"][1]["when"].endswith("NEEDLE_SPREAD")
    assert [s["cmd"].split()[2] for s in result["steps"]] == ["vector-text", "survey"]


def _route_plan(world, monkeypatch, rid, question):
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer(rid, 0.9)}))
    return _plan(question)


def test_survey_plans_the_needle_and_the_wide_survey(world, monkeypatch):
    result = _route_plan(world, monkeypatch, "survey", "Compare what the reports say about the weather")
    assert result["route"]["id"] == "survey" and result["program_kind"] == "cross_document"
    assert [s["cmd"].split()[2] for s in result["steps"]] == ["vector-text", "survey"]
    assert all("--shape brief" in s["cmd"] for s in result["steps"])


def test_entity_history_is_a_history_program_with_an_escalating_guard(world, monkeypatch):
    result = _route_plan(world, monkeypatch, "entity_history", "How did Ada change over time?")
    assert result["program_kind"] == "history"
    assert [s["cmd"] for s in result["steps"]] == [
        "artmind query entity-history --domain journal --entityId p1 --compact"]
    assert [(g["on"], g["then"]["kind"]) for g in result["guards"]] == [("s1", "escalate")]


def test_path_keeps_pattern6_first_and_the_rest_are_guarded_alternatives(world, monkeypatch):
    result = _route_plan(world, monkeypatch, "path", "How are Ada and Lakeport related?")
    assert result["program_kind"] == "one_step"
    assert [s["cmd"].split()[3] for s in result["steps"]] == ["pattern6"]
    assert [n.get("when") for n in result["program"]] == [None, "s1.rows == 0", "s1.rows == 0"]
    assert [f["when"] for f in result["fallbacks"]] == ["zero_rows", "zero_rows"]


def test_time_history_keeps_both_history_commands():
    route = _route("time_history")
    assert [n.run.split()[2:4] for n in route.program] == [["graph", "pattern10"], ["document-history", "--domain"]]
    assert "--includeHistory" in route.program[0].run


def test_inventory_class_listing_is_an_optional_node(world, monkeypatch):
    route = _route("inventory")
    assert [n.optional for n in route.program] == [False, True]
    result = _route_plan(world, monkeypatch, "inventory", "What documents exist?")
    assert [s["cmd"] for s in result["steps"]] == ["artmind query domains-overview --compact"]
    assert result["confidence"]["level"] == "high"


def test_the_on_key_survives_yaml_one_point_one():
    (route,) = routes.parse_routes({"version": 2, "routes": [{
        "id": "x", "description": "d", "triggers": [], "needs": [], "evidence": ["S1"], "read": [],
        "program": [{"id": "a", "run": "artmind query domains-overview --compact"}],
        "guards": [{True: "a", "if": "rows == 0", "then": "escalate"}]}]})
    assert route.guards[0].on == "a"


def test_optional_goes_only_on_run_nodes():
    with pytest.raises(routes.RouteError, match="optional"):
        routes.parse_routes({"version": 2, "routes": [{
            "id": "x", "description": "d", "triggers": [], "needs": [], "evidence": ["S1"], "read": [],
            "program": [{"id": "a", "par": [{"id": "b", "run": "artmind query domains-overview --compact"}],
                         "optional": True}]}]})


def test_a_view_count_question_checks_the_text(world):
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["program_kind"] == "view_count" and result["plan_facts"] == {"view_count": True}
    assert result["steps"][-1]["cmd"] == (
        "artmind query vector-text --shape brief --domain journal 'Ada Lakeport' --topK 10 --compact")


def test_a_view_without_a_count_cue_stays_one_step(world):
    result = _plan("When did Ada travel to Lakeport in 2024?")
    assert result["route"]["id"] == "view" and result["program_kind"] == "one_step"
    assert [n["kind"] for n in result["program"]] == ["run"] and len(result["steps"]) == 1
