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


def _route(rid, triggers, needs=()):
    return routes.parse_routes({"version": 1, "routes": [{
        "id": rid, "description": "synthetic", "triggers": list(triggers), "needs": list(needs),
        "steps": ["artmind query domains-overview --compact"], "evidence": ["S1"],
    }]})[0]


def _hits(trigger, question, **facts):
    return lexical.triggered(_route("r", [trigger]), _facts(question, **facts))


def test_a_slot_in_the_middle_of_a_trigger_bridges_a_few_words():
    assert _hits("what does {document} say", "what does policy_alpha.md say about leave", document="policy_alpha.md")
    assert _hits("what does {document} say", "what does the leave policy say", document="leave policy")
    assert not _hits("what does {document} say", "what does say", document="policy_alpha.md")
    assert not _hits("what does {document} say", "what does the leave policy say")
    far = "what does " + "word " * 9 + "say"
    assert not _hits("what does {document} say", far, document="x")
    assert _hits("summarise {document}", "summarise policy alpha", document="policy alpha")
    assert _hits("{entity} visited", "ada visited lakeport", mentions=1)


def test_word_boundaries_apply_only_at_word_characters():
    assert _hits("tag:", "notes tag: work") and _hits("tag:", "notes tag:work")
    assert not _hits("tag:", "notes stag: work")
    assert _hits("#tagged", "see #tagged items") and _hits("#tagged", "see x #tagged")
    assert not _hits("top", "a desktop view")


def test_single_alphabetic_triggers_match_inflections():
    assert _hits("conflict", "do these conflicts matter") and _hits("conflict", "conflicting notes")
    assert _hits("differ", "the notes are different") and _hits("differ", "this differs")
    assert _hits("trend", "show the trends")
    assert not _hits("trend", "a trendy cafe")
    assert not _hits("how often", "how oftens")


def test_content_tokens_normalise_possessives_and_plurals():
    tokens = lexical.content_tokens
    assert tokens("the team’s policies") == tokens("the team's policy") == {"team", "policy"}
    assert tokens("the teams' boxes") == {"team", "box"}
    assert tokens("which matches") == {"match"}
    assert tokens("  Ada and Bo", drop_capitalised=True) == {"ada"}
    assert lexical._stem("classes") == "class"


def test_dates_need_a_real_month_or_a_plausible_year():
    for text in ("in march 2026", "on 3 sept 2025", "since jan 2024", "2025-05-01", "in 2025", "last week",
                 "this year", "yesterday", "14 december 1999"):
        assert lexical.has_date(text), text
    for text in ("balance 2500", "room 3000", "chapter 2020000", "contact table 4000", "see section nine 1234"):
        assert not lexical.has_date(text), text
    assert not lexical.has_date("the Coffee 2025x")
    regex, _ = lexical.compile_trigger("as of {date}")
    assert not regex.search("as of balance 2500") and regex.search("as of 2025")


def test_value_scores_match_whole_values_only():
    scores = lexical.value_scores("Notes on the C++ project and the art team", ["c++", "art", "ar", "", "  "])
    assert scores == {"c++": 1.0, "art": 1.0, "ar": 0.0, "": 0.0, "  ": 0.0}
    assert lexical.value_scores("a partner", ["art"]) == {"art": 0.0}


def test_decision_default_must_be_an_option():
    with pytest.raises(ValueError):
        Decision("route", "?", {"a": "x"}, default="none")


def test_exact_ties_go_to_the_default_then_the_earlier_option():
    both = Decision("d", "?", {"a": "x", "b": "y", "none": "z"}, {"a": 1.0, "b": 1.0}, default="none", none_mass=2.0)
    assert lexical.answer(both).choice == "none"
    two = Decision("d", "?", {"a": "x", "b": "y", "search": "z"}, {"a": 1.0, "b": 1.0}, default="search",
                   none_mass=0.0)
    assert lexical.answer(two).choice == "a"
    flipped = Decision("d", "?", {"b": "y", "a": "x", "search": "z"}, {"a": 1.0, "b": 1.0}, default="search",
                       none_mass=0.0)
    assert lexical.answer(flipped).choice == "b"
    tied_default = Decision("d", "?", {"a": "x", "b": "y"}, {"a": 1.0, "b": 1.0}, default="b", none_mass=0.0)
    assert lexical.answer(tied_default).choice == "b"


def test_facts_casefold_the_question():
    assert _facts("Hello World").question == "hello world"
    assert lexical.Facts("Hello World", 0, None, False, False).question == "hello world"


def test_an_unknown_classifier_option_is_an_error():
    with pytest.raises(ValueError):
        select_classifier("lexcial", "auto", None)


def test_the_planner_pins_hold():
    shipped = routes.load_routes()
    facts = _facts("How often did the team meet?")
    assert lexical.route_scores(shipped, facts)["aggregate"] == 1.5
    assert lexical.triggered(next(r for r in shipped if r.id == "aggregate"), facts) == ["how often"]
    assert [r.id for r in shipped if r.id != "aggregate" and lexical.triggered(r, facts)] == []
    assert not any(lexical.triggered(r, _facts("Something about the weather?")) for r in shipped)
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
    assert lexical.view_scores("How many visits did Ada make to Lakeport in 2025?", [spec]) == {"visits_by_year": 5.0}
    from types import SimpleNamespace

    option = SimpleNamespace(key="journal", documents=[], top_classes=[])
    assert lexical.domain_scores("How often is the journal mentioned?", [option])["journal"] >= 1


def test_the_survey_cues_match_their_question_shapes():
    survey = next(r for r in routes.load_routes() if r.id == "survey")
    assert "survey" in lexical.triggered(survey, _facts("Run a survey of the notes on sleep?"))
    assert "various" in lexical.triggered(survey, _facts("What do various teams report about storage?"))
    assert "all the contracts" in lexical.triggered(survey, _facts("Summarise all the contracts for the vendor"))
    assert "which documents" in lexical.triggered(survey, _facts("Which documents mention the penalty clause?"))
    assert "the contracts say" in lexical.triggered(survey, _facts("What the contracts say about renewal"))
    assert "the policies" in lexical.triggered(survey, _facts("How the policies treat overtime"))
    assert "the agreements" in lexical.triggered(survey, _facts("Where the agreements differ on notice"))
    assert "the reports" in lexical.triggered(survey, _facts("Do the reports agree on the forecast?"))
    assert "across" in lexical.triggered(survey, _facts("Themes ACROSS the notes?"))
    assert "who is responsible for" in lexical.triggered(survey, _facts("Who is responsible for the audit?"))
    assert "what are the obligations" in lexical.triggered(survey, _facts("What are the obligations under the lease?"))
    assert "responsibilities of" in lexical.triggered(survey, _facts("Responsibilities of the tenant in the lease"))


def test_survey_cues_do_not_fire_on_unrelated_text():
    survey = next(r for r in routes.load_routes() if r.id == "survey")
    assert lexical.triggered(survey, _facts("How many visits did Ada make to Lakeport?")) == []
    assert lexical.triggered(survey, _facts("Who is the landlord named in the lease?")) == []


def test_did_we_and_did_i_are_existence_cues_only_at_the_start():
    existence = next(r for r in routes.load_routes() if r.id == "existence")
    assert "^did we" in lexical.triggered(existence, _facts("Did we visit Lakeport?"))
    assert "^did i" in lexical.triggered(existence, _facts("   ...did I visit Lakeport again?"))
    assert "^did we" in lexical.triggered(existence, _facts('"Did we ever meet?"'))
    assert "^did we" not in lexical.triggered(existence, _facts("Tell me, did we visit Lakeport?"))
    assert "^did i" not in lexical.triggered(existence, _facts("The notes show what did I write?"))


def test_an_anchored_trigger_still_needs_its_words_and_is_case_insensitive():
    assert _hits("^did we", "DID WE meet?") and _hits("^did we", "did   we meet?")
    assert not _hits("^did we", "meet, did we?")
    assert not _hits("^did we", "did what we meet")
    regex, needs = lexical.compile_trigger("^did we")
    assert needs == frozenset() and regex.pattern.startswith("^")


def test_generic_profile_triggers_only_count_for_a_short_leading_question():
    profile = next(r for r in routes.load_routes() if r.id == "entity_profile")
    short = lexical.route_scores([profile], _facts("What is Acme Bank?", mentions=1))
    assert short["entity_profile"] == 1.5
    long_q = ("What is the difference between cancelling a direct debit and cancelling a standing order, "
              "and when does each cancellation take effect?")
    assert lexical.route_scores([profile], _facts(long_q, mentions=1))["entity_profile"] == 0.0
    mid = "An account was opened without checks. What is the immediate remediation?"
    assert lexical.route_scores([profile], _facts(mid, mentions=1))["entity_profile"] == 0.0
    assert lexical.route_scores([profile], _facts("Tell me about the long history of Acme Bank in the region today", mentions=1))["entity_profile"] > 0


def test_difference_between_is_not_a_conflict_cue():
    conflicts = next(r for r in routes.load_routes() if r.id == "conflicts")
    assert lexical.route_scores([conflicts], _facts("What is the difference between a direct debit and a standing order?"))["conflicts"] == 0.0
    assert lexical.route_scores([conflicts], _facts("Where do the two policies differ on notice periods?"))["conflicts"] > 0
