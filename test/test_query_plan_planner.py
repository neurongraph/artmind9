"""The planner: stages → payload (spec 2026-10-22 §3.2, §4.2). Synthetic data only."""

import shlex
from datetime import date
from pathlib import Path

import pytest

from artmind import graph_query
from artmind.kg_views import runner as views_runner
from artmind.kg_views import store as views_store
from artmind.kg_views.model import ViewError, parse_view
from artmind.kg_views.store import LoadedView
from artmind.query_plan import candidates, planner
from artmind.query_plan.classify import Answer, ClassifierUnavailable, LexicalClassifier

VIEW = {
    "name": "visits_by_year", "version": 1, "domains": ["journal"],
    "summary": "Visits a person made to a place in a year, one row per visit",
    "examples": ["How many visits did Ada make to Lakeport in 2025?", "When did Ada travel to Lakeport in 2024?"],
    "params": {
        "person": {"type": "entity", "entity_class": "PERSON", "description": "The traveller"},
        "place": {"type": "entity", "entity_class": "LOCATION", "description": "The destination"},
        "year": {"type": "int", "min": 1900, "max": 2100, "description": "Calendar year"},
    },
    "cypher": ("MATCH (p:Entity {_id: $person}), (l:Entity {_id: $place}) "
               "WHERE p._domain IN $domains AND l._domain IN $domains AND $year > 0 RETURN p.name AS who"),
    "presentation": {"format": "table", "table": {"columns": ["who"]}},
    "provenance": {"created": "2026-10-09", "origin": "authored"},
}
PAYLOAD_KEYS = {"plan_version", "question", "domains", "route", "view_match", "entities", "catalogue", "store", "confidence",
                "steps", "suggested_steps", "fallbacks", "read", "classifier", "warnings", "executed",
                "rows", "rows_total", "truncated"}
ENTITIES = {
    "Ada": [{"_id": "p1", "name": "Ada", "entity_class": "PERSON", "matched_by": ["exact"]}],
    "Lakeport": [{"_id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]}],
}


@pytest.fixture
def world(monkeypatch, tmp_path):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", tmp_path)
    state = {"views": [LoadedView(parse_view(VIEW, VIEW["name"]), "", Path("."))], "entities": dict(ENTITIES),
             "overview": [{"domain": "journal", "document_count": 3, "documents": ["2025-05-01.md"],
                           "top_classes": ["PERSON", "LOCATION"]}]}
    monkeypatch.setattr(graph_query, "domains_overview", lambda: {"rows": state["overview"]})
    monkeypatch.setattr(candidates, "document_names", lambda domains: [])
    monkeypatch.setattr(candidates, "store_facts", lambda domains, question, classes: None)
    monkeypatch.setattr(views_store, "list_views", lambda domains=None: (state["views"], []))

    def fake_resolve(mention, domains, narrator):
        if mention.via == "first_person":
            return ([{"_id": "n1", "name": narrator.name, "entity_class": "PERSON", "matched_by": ["narrator"]}]
                    if narrator else [])
        return list(state["entities"].get(mention.text, []))

    monkeypatch.setattr(candidates, "resolve_mention", fake_resolve)
    return state


def _plan(question, domains=("journal",), **kw):
    return planner.plan(question, list(domains) if domains else None, today=date(2026, 10, 22), **kw)


def test_a_view_question_gets_one_ready_view_run(world):
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert set(result) == PAYLOAD_KEYS
    assert result["route"]["id"] == "view"
    assert result["confidence"]["level"] == "high"
    assert result["view_match"]["status"] == "ok"
    assert result["view_match"]["params"]["year"]["value"] == 2025
    assert [s["cmd"] for s in result["steps"]] == [
        "artmind query views run visits_by_year --domain journal --param person=@p1 --param place=@l1 "
        "--param year=2025 --compact"
    ]
    assert result["suggested_steps"] == []


def test_the_same_question_gives_the_same_plan(world):
    first = _plan("How many visits did Ada make to Lakeport in 2025?")
    second = _plan("How many visits did Ada make to Lakeport in 2025?")
    first["classifier"].pop("latency_ms")
    second["classifier"].pop("latency_ms")
    assert first == second


def test_twins_get_one_run_each(world):
    world["entities"]["Lakeport"] = [
        {"_id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
        {"_id": "l2", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
    ]
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["view_match"]["params"]["place"]["twins"] is True
    assert result["view_match"]["params"]["place"]["chosen"] is None
    assert [s["cmd"].split("--param place=")[1].split()[0] for s in result["steps"]] == ["@l1", "@l2"]
    assert any("share the name 'Lakeport'" in w for w in result["warnings"])


def test_first_person_maps_to_the_configured_narrator(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("narrator: {entity_class: PERSON, name: Diarist}\n")
    result = _plan("How many visits did I make to Lakeport in 2025?")
    person = result["view_match"]["params"]["person"]
    assert person["via"] == "narrator" and person["chosen"]["name"] == "Diarist"
    assert "--param person=@n1" in result["steps"][0]["cmd"]


def test_a_route_question_fills_the_route_templates(world):
    world["views"] = []
    result = _plan("How often did the team meet?")
    assert result["route"]["id"] == "aggregate"
    assert result["route"]["triggered_by"] == ["how often"]
    assert result["view_match"] is None
    assert [s["cmd"] for s in result["steps"]] == [
        "artmind query graph text2cypher --domain journal 'How often did the team meet?' --compact"
    ]


def test_nothing_matching_is_low_confidence_with_suggestions(world):
    result = _plan("Something about the weather?")
    assert result["route"]["id"] == "search"
    assert result["confidence"]["level"] == "low"
    assert result["confidence"]["weakest"] == "route"
    assert result["steps"] == []
    assert result["suggested_steps"][0]["cmd"].startswith("artmind query vector-text --domain journal")
    assert result["read"][-1] == "SKILL.md"


def test_an_unresolved_entity_makes_the_view_no_match(world):
    world["entities"].pop("Lakeport")
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["view_match"]["status"] == "no_match"
    assert result["confidence"]["level"] == "low"


def test_the_domain_is_chosen_when_not_given(world):
    world["overview"].append({"domain": "bank.policy", "document_count": 4, "documents": ["policy_alpha.md"],
                              "top_classes": ["POLICY"]})
    result = _plan("How often is the journal mentioned?", domains=None)
    assert result["domains"]["source"] == "matched"
    assert result["domains"]["selected"] == ["journal"]


def test_execute_runs_a_single_high_confidence_view(world, monkeypatch):
    seen = {}

    def fake_run_view(spec, domains, raw):
        seen.update(name=spec.name, domains=domains, raw=raw)
        return {"view": spec.name, "status": "ok", "params": {}, "rows": [{"who": "Ada"}], "rows_total": 1,
                "truncated": False}

    monkeypatch.setattr(views_runner, "run_view", fake_run_view)
    result = _plan("How many visits did Ada make to Lakeport in 2025?", execute=True)
    assert seen == {"name": "visits_by_year", "domains": ["journal"],
                    "raw": {"person": "@p1", "place": "@l1", "year": "2025"}}
    assert result["rows"] == [{"who": "Ada"}] and result["rows_total"] == 1
    assert result["executed"]["status"] == "ok" and "rows" not in result["executed"]["result"]


def test_execute_is_ignored_on_a_low_confidence_plan(world):
    result = _plan("Something about the weather?", execute=True)
    assert result["executed"] is None
    assert any(w.startswith("--execute ignored") for w in result["warnings"])


def test_an_empty_question_is_refused(world):
    with pytest.raises(planner.PlanError):
        _plan("   ")


def test_a_date_param_is_filled_before_an_int_year_param(world):
    spec = {
        "name": "trips_on", "version": 1, "domains": ["journal"],
        "summary": "Trips taken on a date", "examples": ["Which trips on 4 May 2025?"],
        "params": {
            "year": {"type": "int", "min": 1900, "max": 2100, "default": 2020, "description": "Calendar year"},
            "when": {"type": "date", "description": "The day of the trip"},
        },
        "cypher": "MATCH (t:Entity) WHERE t._domain IN $domains AND $year > 0 AND $when IS NOT NULL RETURN t.name AS trip",
        "presentation": {"format": "table", "table": {"columns": ["trip"]}},
        "provenance": {"created": "2026-10-09", "origin": "authored"},
    }
    world["views"] = [LoadedView(parse_view(spec, spec["name"]), "", Path("."))]
    result = _plan("trips on 2 March 2026")
    match = result["view_match"]
    assert match is not None and match["name"] == "trips_on"
    assert match["params"]["when"]["value"] == "2026-03-02"
    assert match["params"]["year"]["value"] != 2026 and match["params"]["year"]["via"] == "default"
    assert list(match["params"]) == ["year", "when"]  # the view's declared order
    assert result["steps"][0]["cmd"].endswith("--param when=2026-03-02 --compact")


# ── guidance on every low plan, consistent entities, sticky fallback ─────────────────────────


class FakeClassifier:
    """A decision backend stand-in: lexical answers, with optional outage, gaps and overrides."""

    backend = "decision"
    model = "fake-model"

    def __init__(self, unavailable=False, drop=(), override=None):
        self.unavailable, self.drop, self.override = unavailable, set(drop), override or {}
        self.batches: list[list[str]] = []

    def decide(self, question, decisions):
        self.batches.append([d.name for d in decisions])
        if self.unavailable:
            raise ClassifierUnavailable("timeout")
        out = LexicalClassifier().decide(question, decisions)
        out.update({k: v for k, v in self.override.items() if k in out})
        for name in self.drop:
            out.pop(name, None)
        return out


def _answer(choice, p, options=()):
    return Answer(choice, {choice: p, **{o: 0.0 for o in options if o != choice}}, p)


def _use(monkeypatch, fake):
    monkeypatch.setattr(planner, "select_classifier", lambda *a, **k: (fake, None))
    return fake


def _spec(name, params, question):
    names = " AND ".join(f"${p} IS NOT NULL" for p in params)
    return LoadedView(parse_view({
        "name": name, "version": 1, "domains": ["journal"], "summary": question, "examples": [question],
        "params": params,
        "cypher": f"MATCH (t:Entity) WHERE t._domain IN $domains AND {names} RETURN t.name AS trip",
        "presentation": {"format": "table", "table": {"columns": ["trip"]}},
        "provenance": {"created": "2026-10-09", "origin": "authored"},
    }, name), "", Path("."))


def _person(i, name="Ada"):
    return {"_id": i, "name": name, "entity_class": "PERSON", "matched_by": ["exact"]}


def test_an_unfillable_route_still_gets_search_suggestions(world):
    result = _plan("Did I go to the gym again?")
    assert result["route"]["id"] == "existence"
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "unfilled"
    assert result["steps"] == []
    assert [s["cmd"] for s in result["suggested_steps"]] == [
        "artmind query vector-text --domain journal 'Did I go to the gym again?' --compact"
    ]
    assert result["fallbacks"] == []
    assert any("route existence needs entity_name" in w and "suggested search instead" in w
               for w in result["warnings"])
    assert result["read"][-1] == "SKILL.md"


def test_the_narrator_is_never_a_search_term(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("narrator: {entity_class: PERSON, name: Diarist}\n")
    result = _plan("Did I go to the gym again?")
    assert result["route"]["id"] == "existence"
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "unfilled"
    assert result["steps"] == []
    assert result["suggested_steps"][0]["cmd"].startswith("artmind query vector-text --domain journal")
    assert any("route existence needs entity_name" in w for w in result["warnings"])
    assert "--searchTerm" not in str(result["suggested_steps"]) and "Diarist" not in str(result["suggested_steps"])


def test_the_narrator_is_the_second_entity_beside_one_named_mention(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("narrator: {entity_class: PERSON, name: Diarist}\n")
    result = _plan("How am I related to Ada?")
    assert result["route"]["id"] == "path"
    assert result["confidence"]["level"] == "high"
    assert [s["cmd"] for s in result["steps"]] == [
        "artmind query graph pattern6 --domain journal --entityId1 p1 --entityId2 n1 --compact"
    ]


def test_the_narrator_stands_in_for_the_id_when_nobody_is_named(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("narrator: {entity_class: PERSON, name: Diarist}\n")
    result = _plan("What do we know about me?")
    assert result["route"]["id"] == "entity_profile"
    assert result["steps"][0]["cmd"] == "artmind query entity-context --domain journal --entityId n1 --compact"


def test_the_weakest_stage_is_a_blocker_only_when_every_stage_clears_the_threshold(world, monkeypatch):
    # every stage clears the threshold: the blocker is named
    world["entities"].pop("Lakeport")
    assert _plan("How many visits did Ada make to Lakeport in 2025?")["confidence"]["weakest"] == "view_status"
    # a stage below the threshold keeps its own name, blocker or not
    weak = {"view": _answer("visits_by_year", 0.8, ("none",))}
    _use(monkeypatch, FakeClassifier(override={"param_person": _answer("c1", 0.5, ("none",)), **weak}))
    world["entities"]["Lakeport"] = ENTITIES["Lakeport"]
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["view_match"]["status"] == "needs_disambiguation"
    assert result["confidence"]["weakest"] == "param:person" and result["confidence"]["score"] == 0.5


def test_an_unresolved_named_entity_keeps_its_text_but_has_no_id(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("narrator: {entity_class: PERSON, name: Diarist}\n")
    existence = _plan("Did I ever see Zed again?")
    assert existence["steps"][0]["cmd"].startswith("artmind query graph pattern7 --domain journal --searchTerm Zed")
    profile = _plan("Tell me about Zed")
    assert profile["route"]["id"] == "entity_profile"
    assert profile["steps"] == [] and "--entityId" not in profile["suggested_steps"][0]["cmd"]
    assert any("route entity_profile needs entity_id" in w for w in profile["warnings"])


def test_a_view_that_is_not_ok_still_gets_search_suggestions(world):
    world["entities"].pop("Lakeport")
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["route"]["id"] == "view"
    assert result["confidence"]["weakest"] == "view_status"
    assert result["steps"] == []
    assert result["suggested_steps"][0]["cmd"].startswith("artmind query vector-text --domain journal")
    assert result["fallbacks"] == []
    assert any("view visits_by_year: status no_match" in w for w in result["warnings"])


def test_too_many_twin_combinations_are_low_with_search_suggestions(world):
    world["entities"]["Ada"] = [_person("p1"), _person("p2"), _person("p3")]
    world["entities"]["Lakeport"] = [
        {"_id": f"l{i}", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]} for i in (1, 2)
    ]
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "too_many"
    assert result["steps"] == []
    assert result["suggested_steps"][0]["cmd"].startswith("artmind query vector-text")
    assert any("6 twin combinations exceed 4" in w for w in result["warnings"])


def test_two_distinct_candidates_need_disambiguation(world):
    world["entities"]["Ada"] = [_person("p1", "Ada Lovelace"), _person("p2", "Ada Byron")]
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    match = result["view_match"]
    assert match["status"] == "needs_disambiguation"
    assert match["params"]["person"]["chosen"] is None and match["params"]["person"]["twins"] is False
    assert {c["_id"] for c in match["params"]["person"]["candidates"]} == {"p1", "p2"}
    assert result["confidence"]["level"] == "low" and result["steps"] == []
    assert result["suggested_steps"]


def test_a_required_scalar_with_no_value_is_missing(world):
    q = "Which trips had a note?"
    world["views"] = [_spec("noted", {"note": {"type": "string", "description": "A quoted note"}}, q)]
    result = _plan(q)
    assert result["view_match"]["name"] == "noted"
    assert result["view_match"]["status"] == "missing_param"
    assert result["view_match"]["params"]["note"]["via"] is None
    assert result["confidence"]["level"] == "low" and result["steps"] == []
    assert any("status missing_param" in w for w in result["warnings"])


def test_an_enum_param_takes_the_value_named_in_the_question(world):
    q = "Which work trips happened?"
    world["views"] = [_spec("by_kind", {"kind": {"type": "enum", "values": ["work", "home"],
                                                  "description": "Trip kind"}}, q)]
    result = _plan(q)
    assert result["view_match"]["params"]["kind"]["value"] == "work"
    assert result["view_match"]["params"]["kind"]["via"] == "classifier"
    assert result["steps"][0]["cmd"].endswith("--param kind=work --compact")
    assert result["confidence"]["level"] == "high"


def test_a_weak_enum_answer_is_treated_as_missing_or_defaulted(world, monkeypatch):
    q = "Which work trips happened?"
    weak = {"param_kind": _answer("work", 0.4, ("home", "none"))}
    _use(monkeypatch, FakeClassifier(override=weak))
    world["views"] = [_spec("by_kind", {"kind": {"type": "enum", "values": ["work", "home"],
                                                  "description": "Trip kind"}}, q)]
    required = _plan(q)
    assert required["view_match"]["status"] == "missing_param"
    assert required["view_match"]["params"]["kind"]["value"] is None
    world["views"] = [_spec("by_kind", {"kind": {"type": "enum", "values": ["work", "home"], "default": "home",
                                                  "description": "Trip kind"}}, q)]
    optional = _plan(q)
    assert optional["view_match"]["status"] == "ok"
    assert optional["view_match"]["params"]["kind"] == {"type": "enum", "via": "default", "value": "home"}
    assert "--param kind" not in optional["steps"][0]["cmd"]


def test_a_confident_enum_answer_adds_a_stage(world, monkeypatch):
    q = "Which work trips happened?"
    strong = {"param_kind": _answer("work", 0.9, ("home", "none"))}
    _use(monkeypatch, FakeClassifier(override=strong))
    world["views"] = [_spec("by_kind", {"kind": {"type": "enum", "values": ["work", "home"],
                                                  "description": "Trip kind"}}, q)]
    assert _plan(q)["view_match"]["params"]["kind"]["confidence"] == 0.9
    weak_view = {"view": _answer("by_kind", 0.95, ("none",)), "param_kind": _answer("work", 0.75, ("home", "none"))}
    _use(monkeypatch, FakeClassifier(override=weak_view))
    result = _plan(q)
    assert result["confidence"]["weakest"] == "param:kind" and result["confidence"]["score"] == 0.75


def test_one_entity_cannot_fill_two_entity_params(world):
    q = "How is Ada connected to the trip?"
    person = {"type": "entity", "entity_class": "PERSON", "description": "A person"}
    world["views"] = [_spec("pair", {"first": dict(person), "second": dict(person)}, q)]
    result = _plan(q)
    params = result["view_match"]["params"]
    assert params["first"]["chosen"]["_id"] == "p1"
    assert params["second"]["chosen"] is None and params["second"]["candidates"] == []
    assert result["view_match"]["status"] == "no_match"
    assert result["steps"] == []


def test_a_question_with_a_quote_is_shell_quoted(world):
    world["views"] = []
    question = "How often didn't the team meet?"
    result = _plan(question)
    cmd = result["steps"][0]["cmd"]
    assert shlex.split(cmd)[-2] == question
    assert "'" in cmd and cmd.endswith(" --compact")


def test_a_classifier_outage_falls_back_for_the_rest_of_the_plan(world, monkeypatch):
    fake = _use(monkeypatch, FakeClassifier(unavailable=True))
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert len(fake.batches) == 1  # the second batch never reached the backend
    assert result["classifier"]["fell_back"] is True and result["classifier"]["reason"] == "timeout"
    assert result["classifier"]["calls"] == 2
    assert result["classifier"]["backend"] == "decision"
    assert any("decision classifier unavailable (timeout)" in w for w in result["warnings"])
    assert result["view_match"]["status"] == "ok" and result["confidence"]["level"] == "high"


def test_answers_a_backend_left_out_come_from_the_lexical_classifier(world, monkeypatch):
    _use(monkeypatch, FakeClassifier(drop=("route",)))
    result = _plan("Something about the weather?")
    assert result["route"]["id"] == "search"
    assert result["classifier"]["fell_back"] is False
    assert any("route" in w and "lexical" in w for w in result["warnings"])


def test_a_forced_decision_classifier_without_an_endpoint_is_refused(world):
    with pytest.raises(planner.PlanError):
        _plan("How many visits did Ada make to Lakeport in 2025?", classifier="decision")


def test_a_broken_plan_config_is_a_plan_error(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("not_a_key: 1\n")
    with pytest.raises(planner.PlanError):
        _plan("How many visits did Ada make to Lakeport in 2025?")
    with pytest.raises(planner.PlanError):
        _plan("How many visits did Ada make to Lakeport in 2025?", domains=None)


def test_store_facts_failing_does_not_sink_the_plan(world, monkeypatch):
    def boom(domains, question, classes):
        raise RuntimeError("registry offline")

    monkeypatch.setattr(candidates, "store_facts", boom)
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["store"] is None
    assert any("store facts unavailable" in w and "registry offline" in w for w in result["warnings"])
    assert result["confidence"]["level"] == "high"


def test_execute_reports_a_view_error(world, monkeypatch):
    def fail(spec, domains, raw):
        raise ViewError("query timed out")

    monkeypatch.setattr(views_runner, "run_view", fail)
    result = _plan("How many visits did Ada make to Lakeport in 2025?", execute=True)
    assert result["executed"]["status"] == "error" and result["executed"]["error"] == "query timed out"
    assert result["executed"]["result"] is None and result["rows"] == []


def test_execute_is_ignored_for_twin_combinations(world, monkeypatch):
    world["entities"]["Lakeport"] = [
        {"_id": f"l{i}", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]} for i in (1, 2)
    ]
    monkeypatch.setattr(views_runner, "run_view", lambda *a: pytest.fail("must not run"))
    result = _plan("How many visits did Ada make to Lakeport in 2025?", execute=True)
    assert len(result["steps"]) == 2 and result["executed"] is None
    assert any(w.startswith("--execute ignored") and "2 parameter combinations" in w for w in result["warnings"])


@pytest.mark.parametrize("kind, expected", [
    ("analytical", ["text2sql"]),
    ("hybrid", ["db bridge", "text2sql"]),
    ("records_plus_guidance", ["db bridge", "text2sql", "vector-text"]),
    ("graph", ["db bridge", "text2sql"]),
])
def test_the_structured_route_follows_the_store_kind(world, monkeypatch, kind, expected):
    world["views"] = []
    calls = []

    def facts(domains, question, classes):
        calls.append(1)
        return {"kind": kind, "tables": ["t"], "bridge_classes": [], "matched_values": []}

    monkeypatch.setattr(candidates, "store_facts", facts)
    _use(monkeypatch, FakeClassifier(override={"route": _answer("structured", 0.9)}))
    result = _plan("Show the table records")
    assert result["route"]["id"] == "structured"
    cmds = [s["cmd"] for s in result["steps"]]
    assert [next(e for e in ("db bridge", "text2sql", "vector-text") if e in c) for c in cmds] == expected
    assert len(calls) == 1


def test_the_structured_route_keeps_its_default_steps_without_store_facts(world, monkeypatch):
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer("structured", 0.9)}))
    result = _plan("Show the table records")
    cmds = [s["cmd"] for s in result["steps"] or result["suggested_steps"]]
    assert result["route"]["id"] == "structured"
    assert "db bridge" in cmds[0] and "text2sql" in cmds[1]


def _fallback_cmds(result):
    return [f["cmd"] for f in result["fallbacks"]]


def test_path_falls_back_to_shortest_path_when_both_classes_are_known(world, monkeypatch):
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer("path", 0.9)}))
    result = _plan("How are Ada and Lakeport related?")
    assert result["route"]["id"] == "path"
    pattern5 = [c for c in _fallback_cmds(result) if "pattern5" in c]
    assert pattern5 == [
        "artmind query graph pattern5 --domain journal --entityClass1 PERSON --entityClass2 LOCATION "
        "--entityId1 p1 --entityId2 l1 --mode shortest --compact"
    ]
    assert "vector-text" in _fallback_cmds(result)[-1]


def test_path_shortest_fallback_is_left_out_when_a_class_is_unknown(world, monkeypatch):
    world["views"] = []
    world["entities"].pop("Lakeport")
    _use(monkeypatch, FakeClassifier(override={"route": _answer("path", 0.9)}))
    result = _plan("How are Ada and Lakeport related?")
    assert not any("pattern5" in c for c in _fallback_cmds(result))


def test_inventory_adds_a_class_listing_when_the_question_names_a_class(world, monkeypatch):
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer("inventory", 0.9)}))
    for question in ("Which person entities exist?", "List the persons we know"):
        result = _plan(question)
        assert result["route"]["id"] == "inventory" and result["confidence"]["level"] == "high"
        assert [s["cmd"] for s in result["steps"]] == [
            "artmind query domains-overview --compact",
            "artmind query graph pattern1 --domain journal --entityClass PERSON --compact",
        ]


def test_inventory_without_a_class_word_has_one_step_and_stays_high(world, monkeypatch):
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer("inventory", 0.9)}))
    result = _plan("What documents exist?")
    assert [s["cmd"] for s in result["steps"]] == ["artmind query domains-overview --compact"]
    assert result["confidence"]["level"] == "high" and result["fallbacks"] == []


def test_class_word_matches_singular_plural_and_domain_scope():
    overview = [{"domain": "a.x", "top_classes": ["PERSON", "CITY"]}, {"domain": "b", "top_classes": ["TOOL"]}]
    assert planner._class_word("which cities", ["a"], overview) == "CITY"
    assert planner._class_word("the person list", ["a.x"], overview) == "PERSON"
    assert planner._class_word("which tools", ["a"], overview) is None
    assert planner._class_word("which tools", ["b"], overview) == "TOOL"


def test_class_word_stems_irregulars_and_underscore_classes():
    overview = [{"domain": "d", "top_classes": ["PLACE", "CASE", "PERSON", "CHILD", "BANK_ACCOUNT"]}]
    assert planner._class_word("which places", ["d"], overview) == "PLACE"
    assert planner._class_word("list the cases", ["d"], overview) == "CASE"
    assert planner._class_word("who are the people", ["d"], overview) == "PERSON"
    assert planner._class_word("the children", ["d"], overview) == "CHILD"
    assert planner._class_word("which bank accounts", ["d"], overview) == "BANK_ACCOUNT"
    assert planner._class_word("which bank holidays", ["d"], overview) is None



# ── the domain catalogue (spec §11) ─────────────────────────────────────────

def _cat(domain="journal", names=(), classes=(), relations=(), documents=(), truncated=False):
    from artmind.query_plan.catalogue import DomainCatalogue, NameEntry

    entries = tuple(NameEntry(i, n, tuple(a), c, o) for i, n, a, c, o in names)
    return DomainCatalogue(domain, (1, 1, 0, ""), "2026-10-23T00:00:00+00:00", tuple(classes), tuple(relations),
                           tuple(documents), entries, truncated)


@pytest.fixture
def catalogue_world(world, monkeypatch):
    cat = _cat(names=[("p1", "Ada", ["Countess"], "PERSON", 5), ("g1", "Greenhouse", [], "LOCATION", 3),
                      ("g2", "Pier", [], "LOCATION", 1)],
               classes=[("PERSON", 3, ""), ("LOCATION", 2, ""), ("HOBBY", 1, "")],
               relations=[("VISITED", 4)], documents=["policy_alpha.md"])
    state = {"cats": {"journal": cat}, "sources": {"journal": "live"}, "asked": []}

    def get(cover):
        state["asked"].append(list(cover))
        return state["cats"], state["sources"]

    monkeypatch.setattr(planner, "_get_catalogue", get)
    state["resolved"] = []
    original = candidates.resolve_mention

    def tracking(mention, domains, narrator):
        state["resolved"].append(mention.text)
        return original(mention, domains, narrator)

    monkeypatch.setattr(candidates, "resolve_mention", tracking)
    world.update(state)
    return world


def test_a_lowercase_catalogue_name_becomes_a_mention_without_a_name_query(catalogue_world):
    result = _plan("what did I do at the greenhouse last month")
    found = [e for e in result["entities"] if e["via"] == "catalogue"]
    assert [e["mention"] for e in found] == ["greenhouse"]
    assert [(c["_id"], c["matched_by"]) for c in found[0]["candidates"]] == [("g1", ["catalogue"])]
    assert "greenhouse" not in catalogue_world["resolved"]


def test_a_capitalised_mention_equal_to_a_catalogue_name_still_runs_the_name_query(catalogue_world):
    result = _plan("When did Ada visit")
    assert catalogue_world["resolved"] == ["Ada"]
    assert [c["_id"] for c in result["entities"][0]["candidates"]] == ["p1"]
    assert result["entities"][0]["candidates"][0]["matched_by"] == ["exact"]


def test_a_capitalised_mention_the_catalogue_lacks_is_resolved_as_before(catalogue_world):
    result = _plan("When did Lakeport open")
    assert catalogue_world["resolved"] == ["Lakeport"]
    assert result["entities"][0]["candidates"][0]["_id"] == "l1"


def test_catalogue_twins_still_warn(catalogue_world):
    catalogue_world["cats"]["journal"] = _cat(names=[("p1", "Marlowe", [], "PERSON", 5),
                                                     ("p2", "Marlowe", [], "PERSON", 3)])
    result = _plan("when did marlowe visit")
    assert any("2 nodes share the name" in w for w in result["warnings"])


def test_the_cover_drops_a_domain_whose_parent_is_in_scope(catalogue_world):
    _plan("anything", domains=("fam", "fam.a", "other"))
    assert catalogue_world["asked"] == [["fam", "other"]]


def test_a_failing_catalogue_plans_as_before_with_a_warning(world, monkeypatch):
    def boom(cover):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(planner, "_get_catalogue", boom)
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert "domain catalogue unavailable: neo4j down" in result["warnings"]
    assert result["catalogue"] == {}
    assert result["route"]["id"] == "view"


def test_the_class_word_comes_from_every_catalogue_class(catalogue_world):
    cats = list(catalogue_world["cats"].values())
    assert planner._class_word("list my hobbies", ["journal"], [], cats) == "HOBBY"  # beyond the overview's top
    catalogue_world["cats"]["journal"] = _cat(classes=[("PERSON", 3, ""), ("HOBBY", 1, "")])
    cats = list(catalogue_world["cats"].values())
    assert planner._class_word("list every hobby", ["journal"], [], cats) == "HOBBY"
    # without a catalogue the overview's top classes decide
    overview = [{"domain": "journal", "top_classes": ["PERSON"]}]
    assert planner._class_word("list every hobby", ["journal"], overview, None) is None
    assert planner._class_word("list every person", ["journal"], overview, None) == "PERSON"


def test_the_catalogue_documents_replace_the_document_query(catalogue_world, monkeypatch):
    def forbidden(domains):
        raise AssertionError("document_names must not run")

    monkeypatch.setattr(candidates, "document_names", forbidden)
    result = _plan("what does policy_alpha.md say")
    assert "policy_alpha.md" in " ".join(s["cmd"] for s in result["steps"] + result["suggested_steps"])


def test_the_payload_reports_the_catalogue(catalogue_world):
    result = _plan("anything")
    assert result["catalogue"] == {"journal": {"source": "live", "built_at": "2026-10-23T00:00:00+00:00",
                                               "names": 3, "truncated": False}}


def test_a_selected_domain_below_the_cover_does_not_use_the_catalogue(catalogue_world):
    cats = catalogue_world["cats"]
    assert planner._selected_cats(cats, ["journal.sub"]) is None
    assert planner._selected_cats(cats, ["journal"]) == [cats["journal"]]


def test_a_parent_cover_is_filtered_to_the_selected_leaf(catalogue_world):
    from artmind.query_plan.catalogue import NameEntry

    parent = _cat("fam", names=[])
    parent = type(parent)(**{**parent.__dict__, "names": (
        NameEntry("a1", "Ada", (), "PERSON", 4, "fam.one", 7), NameEntry("a2", "Ada", (), "PERSON", 3, "fam.two", 1),
        NameEntry("a3", "Ada", (), "PERSON", 2, "fam", 0))})
    names = planner._selected_names({"fam": parent}, ["fam.one"])
    assert [n._id for n in names] == ["a1"]
    assert sorted(n._id for n in planner._selected_names({"fam": parent}, ["fam"])) == ["a1", "a2", "a3"]
    assert planner._selected_names({"fam": parent}, ["other"]) is None
    # a selected domain with its own catalogue uses all of it
    own = type(parent)(**{**parent.__dict__, "domain": "fam.one"})
    assert [n._id for n in planner._selected_names({"fam": parent, "fam.one": own}, ["fam.one"])] == ["a1", "a2", "a3"]


def test_catalogue_refs_carry_domain_and_connection_count(catalogue_world):
    from artmind.query_plan.catalogue import NameEntry

    cat = catalogue_world["cats"]["journal"]
    catalogue_world["cats"]["journal"] = type(cat)(**{**cat.__dict__, "names": (
        NameEntry("p1", "Marlowe", (), "PERSON", 5, "journal", 9),)})
    ref = _plan("when did marlowe visit")["entities"][0]["candidates"][0]
    assert (ref["domain"], ref["connection_count"], ref["description"]) == ("journal", 9, "")


def test_a_catalogue_name_of_a_non_selected_domain_is_no_mention():
    from artmind.query_plan.catalogue import NameEntry
    from artmind.query_plan.config import PlanConfig  # noqa: F401

    parent = _cat("fam", names=[])
    parent = type(parent)(**{**parent.__dict__, "names": (
        NameEntry("o1", "Marlowe", (), "PERSON", 5, "fam.other", 2),
        NameEntry("g1", "Greenhouse", (), "PLACE", 5, "fam.one", 2))})
    all_index = candidates.name_index(planner._merged_names([parent]))
    question = "did marlowe go to the greenhouse"
    mentions = candidates.gazetteer_mentions(question, [], all_index)
    assert [m.text for m in mentions] == ["marlowe", "greenhouse"]  # the gazetteer ran over the whole cover
    index = candidates.name_index(planner._selected_names({"fam": parent}, ["fam.one"]))
    cfg = type("Cfg", (), {"narrator": None})()
    entities = planner._resolve_entities(mentions, ["fam.one"], cfg, [], index)
    assert [(e["mention"], e["via"], e["candidates"][0]["domain"]) for e in entities] == [
        ("greenhouse", "catalogue", "fam.one")]


def test_a_spelled_out_name_outranks_a_catalogue_name_as_the_primary_entity(catalogue_world):
    result = _plan("did the greenhouse see Lakeport")
    context = planner._context(["journal"], "q", result["entities"], None, [])
    assert context["entity_name"] == "Lakeport"
