"""Lexical scoring and the lexical classifier (spec 2026-10-22 §4.4)."""

import pytest

from artmind.query_plan import routes
from artmind.query_plan.classify import Decision, LexicalClassifier, select_classifier
from artmind.query_plan.classify import lexical


def _facts(question, **over):
    base = dict(question=question.casefold(), mentions=0, document=None, has_store=False, has_date=False)
    return lexical.Facts(**{**base, **over})


def test_a_single_trigger_wins_the_route_with_high_confidence():
    scores = lexical.route_scores(routes.load_routes(), _facts("How often did the team meet?"))
    assert scores["aggregate"] == 1.5 and scores["search"] == 0.0
    decision = Decision("route", "?", {r.id: r.description for r in routes.load_routes()}, scores,
                        default="search", default_confidence=0.5, none_mass=0.5)
    answer = lexical.answer(decision)
    assert answer.choice == "aggregate" and answer.confidence == 0.75


def test_nothing_matching_is_the_default_at_its_default_confidence():
    decision = Decision("route", "?", {"search": "s", "aggregate": "a"}, {"search": 0.0, "aggregate": 0.0},
                        default="search", default_confidence=0.5, none_mass=0.5)
    answer = lexical.answer(decision)
    assert (answer.choice, answer.confidence) == ("search", 0.5)


def test_slot_triggers_need_their_fact():
    regex, needs = lexical.compile_trigger("summarise {document}")
    assert needs == frozenset({"document"}) and regex.search("please summarise policy alpha")
    regex, needs = lexical.compile_trigger("as of {date}")
    assert regex.search("what applied as of 2026-01-15") and regex.search("as of march 2026")
    assert lexical.compile_trigger("{document}") == (None, frozenset({"document"}))


def test_a_route_needing_an_entity_gets_no_bonus_without_one():
    facts = _facts("tell me about the weather", mentions=0)
    assert lexical.route_scores(routes.load_routes(), facts)["entity_profile"] == 1.0
    facts = _facts("tell me about the weather", mentions=1)
    assert lexical.route_scores(routes.load_routes(), facts)["entity_profile"] == 1.5


def test_view_scores_match_examples_not_names():
    from artmind.kg_views.model import parse_view

    spec = parse_view({
        "name": "visits_by_year", "version": 1, "domains": ["journal"],
        "summary": "Visits a person made to a place in a year",
        "examples": ["How many visits did Ada make to Lakeport in 2025?"],
        "params": {"year": {"type": "int", "description": "Year"}},
        "cypher": "MATCH (n:Entity) WHERE n._domain IN $domains AND $year > 0 RETURN n.name AS who",
        "presentation": {"format": "table", "table": {"columns": ["who"]}},
        "provenance": {"created": "2026-10-09", "origin": "authored"},
    }, "visits_by_year")
    assert lexical.view_scores("How many visits did Bo make to Port Ellen in 2024?", [spec]) == {"visits_by_year": 5.0}
    assert lexical.view_scores("What did Bo eat?", [spec]) == {"visits_by_year": 0.0}


def test_the_classifier_is_deterministic():
    decision = Decision("view", "?", {"v": "x", "none": "no"}, {"v": 5.0}, default="none",
                        default_confidence=1.0, none_mass=1.0)
    first = LexicalClassifier().decide("q", [decision])
    assert first == LexicalClassifier().decide("q", [decision])
    assert first["view"].choice == "v" and first["view"].confidence == pytest.approx(0.8333, abs=1e-4)


def test_triggered_lists_the_matching_patterns():
    route = next(r for r in routes.load_routes() if r.id == "aggregate")
    assert lexical.triggered(route, _facts("how many times, on average?")) == ["how many", "average"]


def test_auto_without_an_endpoint_is_lexical(monkeypatch):
    monkeypatch.delenv("ARTMIND_DECISION_URL", raising=False)
    classifier, reason = select_classifier("auto", "auto", None)
    assert classifier.backend == "lexical" and reason == "no_endpoint"
    classifier, reason = select_classifier("lexical", "auto", None)
    assert classifier.backend == "lexical" and reason is None
