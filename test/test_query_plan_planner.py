"""The planner: stages → payload (spec 2026-10-22 §3.2, §4.2). Synthetic data only."""

from datetime import date
from pathlib import Path

import pytest

from artmind import graph_query
from artmind.kg_views import runner as views_runner
from artmind.kg_views import store as views_store
from artmind.kg_views.model import parse_view
from artmind.kg_views.store import LoadedView
from artmind.query_plan import candidates, planner

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
PAYLOAD_KEYS = {"plan_version", "question", "domains", "route", "view_match", "entities", "store", "confidence",
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
