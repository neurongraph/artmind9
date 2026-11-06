"""Exact names win their parameter; a fully filled view reaches the threshold on the lexical path only.
Synthetic data only; fixtures come from test_query_plan_planner."""

from pathlib import Path

import pytest

from artmind.kg_views.model import parse_view
from artmind.kg_views.store import LoadedView
from test_query_plan_planner import FakeClassifier, _answer, _plan, _use, world  # noqa: F401

TRIPS = {
    "name": "trips_by_year", "version": 1, "domains": ["journal"],
    "summary": "Count the trips a person made to a place in a year",
    "examples": ["How many times did Ada go to Lakeport in 2025?"],
    "params": {
        "person": {"type": "entity", "entity_class": "PERSON", "description": "The traveller"},
        "place": {"type": "entity", "entity_class": "LOCATION", "description": "The destination"},
        "year": {"type": "int", "min": 1900, "max": 2100, "description": "Calendar year"},
        "merge_within_days": {"type": "int", "default": 10, "min": 0, "max": 60, "description": "Merge window"},
    },
    "cypher": ("MATCH (p:Entity {_id: $person}), (l:Entity {_id: $place}) WHERE p._domain IN $domains "
               "AND l._domain IN $domains AND $year > 0 AND $merge_within_days >= 0 RETURN p.name AS who"),
    "presentation": {"format": "table", "table": {"columns": ["who"]}},
    "provenance": {"created": "2026-10-09", "origin": "authored"},
}


def _loc(i, name, matched="catalogue"):
    return {"_id": i, "name": name, "entity_class": "LOCATION", "matched_by": [matched]}


@pytest.fixture
def trips_world(world):  # noqa: F811
    world["views"] = [LoadedView(parse_view(TRIPS, TRIPS["name"]), "", Path("."))]
    world["entities"]["Quayside"] = [_loc("q1", "Quayside"), _loc("q2", "Quayside Airport"),
                                     _loc("q3", "Travel to Quayside")]
    return world


def _place(result):
    return result["view_match"]["params"]["place"]


def test_an_exact_name_wins_its_parameter_over_longer_names(trips_world):
    result = _plan("how many visits did Ada make to Quayside in 2024")
    assert result["route"]["id"] == "view"
    assert result["view_match"]["status"] == "ok"
    assert _place(result)["chosen"]["_id"] == "q1" and _place(result)["twins"] is False
    assert "--param place=@q1" in result["steps"][0]["cmd"]
    assert result["confidence"]["level"] == "high"


def test_exact_name_match_ignores_case_and_spacing(trips_world):
    trips_world["entities"]["Quayside"][0]["name"] = "QUAYSIDE  "
    result = _plan("how many visits did Ada make to Quayside in 2024")
    assert _place(result)["chosen"]["_id"] == "q1"


def test_without_an_exact_name_the_candidates_still_split(trips_world):
    trips_world["entities"]["Quayside"] = trips_world["entities"]["Quayside"][1:]
    result = _plan("how many visits did Ada make to Quayside in 2024")
    assert result["view_match"]["status"] == "needs_disambiguation"
    assert result["confidence"]["level"] == "low"


def test_true_twins_stay_twins(trips_world):
    trips_world["entities"]["Quayside"] = [_loc("q1", "Quayside"), _loc("q4", "Quayside"),
                                           _loc("q2", "Quayside Airport")]
    result = _plan("how many visits did Ada make to Quayside in 2024")
    assert _place(result)["twins"] is True and _place(result)["chosen"] is None
    assert sorted(c["_id"] for c in _place(result)["candidates"]) == ["q1", "q4"]
    assert [s["cmd"].split("--param place=")[1].split()[0] for s in result["steps"]] == ["@q1", "@q4"]


def test_a_fully_filled_view_below_the_threshold_is_lifted_on_the_lexical_path(trips_world):
    result = _plan("How many times did Ada go to Quayside in 2025 ?")
    assert result["classifier"]["backend"] == "lexical"
    assert result["route"]["id"] == "view"
    assert result["view_match"]["params"]["year"]["value"] == 2025
    assert result["view_match"]["params"]["merge_within_days"]["value"] == 10
    lift = result["view_match"]["confidence_lifted"]
    assert 0.5 <= lift["from"] < 0.7 and lift["to"] == 0.7
    assert result["confidence"]["level"] == "high"
    assert "--param place=@q1" in result["steps"][0]["cmd"]


def test_a_view_below_the_floor_is_not_lifted(trips_world):
    result = _plan("Tell me about Ada and Quayside in 2025")
    assert result["route"]["id"] != "view"
    assert result["view_match"] is None


def test_the_lift_needs_every_required_param(trips_world):
    result = _plan("How many times did Ada go to Quayside ?")  # no year
    assert result["route"]["id"] != "view"
    assert result["confidence"]["level"] == "low"


def test_a_fulltext_neighbour_is_not_name_evidence_for_the_lift(trips_world):
    trips_world["entities"]["Quayside"] = [_loc("q2", "Quayside Airport", matched="fulltext")]
    result = _plan("How many times did Ada go to Quayside in 2025 ?")
    assert result["route"]["id"] != "view"
    assert "confidence_lifted" not in (result["view_match"] or {})
    assert result["confidence"]["level"] == "low"


def test_the_decision_path_is_not_lifted(trips_world, monkeypatch):
    fake = _use(monkeypatch, FakeClassifier(override={"view": _answer("trips_by_year", 0.6, ["no_view"])}))
    result = _plan("How many times did Ada go to Quayside in 2025 ?")
    assert result["classifier"]["backend"] == "decision" and not result["classifier"]["fell_back"]
    assert result["route"]["id"] != "view"
    assert "confidence_lifted" not in (result["view_match"] or {})
    assert fake.batches
