"""A named class decides between a search and a listing (class_listing). Synthetic names only;
fixtures from test_query_plan_planner (the catalogue world has classes PERSON, LOCATION, HOBBY)."""

import pytest

from artmind.query_plan import slots
from artmind.query_plan.slots import Expansion
from test_query_plan_planner import _plan, catalogue_world, world  # noqa: F401  (fixtures and helpers)


def _cmds(result):
    return [s["cmd"] for s in result["steps"] + result["suggested_steps"]]


@pytest.fixture
def common_words(monkeypatch):
    monkeypatch.setattr(slots, "rare_term", lambda *a, **k: False)


@pytest.fixture
def no_views(catalogue_world):
    catalogue_world["views"] = []
    return catalogue_world


@pytest.mark.parametrize("question", ["Are there any hobbies?", "Are there any HOBBYs in the journal?",
                                      "What hobbies do I have?", "List my hobbies"])
def test_a_named_class_with_a_list_cue_lists_the_class(no_views, common_words, question):
    result = _plan(question)
    assert result["route"]["id"] == "enumeration", question
    assert result["program_kind"] == "class_listing"
    assert result["plan_facts"]["named_class"] == 1
    cmds = _cmds(result)
    assert cmds[0] == "artmind query graph pattern1 --domain journal --entityClass HOBBY --limit 200 --shape brief --compact"
    assert cmds[1].startswith("artmind query vector-text --shape brief --domain journal ")
    assert [g["then"] for g in result["guards"]] == [{"kind": "replace", "route": "search"}]


def test_the_route_change_is_recorded_and_an_exact_class_name_is_confident(no_views, common_words):
    result = _plan("Are there any hobbies?")
    assert result["route"]["adjusted"] == {"from": "search", "by": "named_class", "classes": ["HOBBY"]}
    assert result["route"]["confidence"] >= result["confidence"]["threshold"]


def test_a_plain_noun_stays_a_search(no_views, common_words):
    result = _plan("Are there any recipes?")
    assert result["route"]["id"] == "search" and result["route"]["adjusted"] is None
    assert result["program_kind"] == "one_step"


def test_a_count_or_a_needle_question_keeps_its_route(no_views, common_words):
    assert _plan("How many hobbies do I have?")["route"]["id"] == "aggregate"
    needle = _plan("What was my hobby in the spring?")
    assert needle["route"]["id"] != "enumeration" and needle["route"]["adjusted"] is None


def test_which_one_item_of_a_class_is_not_a_listing(no_views, common_words):
    result = _plan("Which hobby did I take up after the move?")
    assert result["route"]["adjusted"] is None and result["program_kind"] != "class_listing"


def test_a_description_match_does_not_list_a_class():
    assert slots.named_class(Expansion("pastimes", ("HOBBY",), (), "class_match"), []) == 0


def test_a_named_entity_beside_the_class_keeps_the_route(no_views, common_words):
    result = _plan("Did Ada mention any hobbies?")
    assert result["route"]["adjusted"] is None and result["program_kind"] != "class_listing"


def test_named_class_counts_only_a_class_alone():
    exp = Expansion("hobbies", ("HOBBY",), (), "class_match", narrowed=True)
    narrator = {"via": "first_person", "candidates": [{"_id": "n1", "matched_by": ["narrator"]}]}
    class_word = {"via": "fulltext", "mention": "hobbies", "candidates": [{"_id": "h1", "matched_by": ["fulltext"]}]}
    person = {"via": "catalogue", "mention": "Ada", "candidates": [{"_id": "p1", "matched_by": ["catalogue"]}]}
    assert slots.named_class(exp, [narrator, class_word]) == 1
    assert slots.named_class(exp, [person]) == 0
    assert slots.named_class(None, []) == 0
    assert slots.named_class(Expansion("x", (), ("x",), "category"), []) == 0


def test_list_cue_excludes_counts():
    assert slots.has_list_cue("are there any ideas")
    assert slots.has_list_cue("list the plans")
    assert not slots.has_list_cue("how many ideas are there")
    assert not slots.has_list_cue("what was my idea about the garden")


def test_exact_class_match_is_a_name_or_a_category_not_a_description():
    assert slots.exact_class_match(Expansion("hobbies", ("HOBBY",), (), "class_match", narrowed=True))
    assert slots.exact_class_match(Expansion("doctors", ("DOCTOR",), (), "category"))
    assert not slots.exact_class_match(Expansion("pastimes", ("HOBBY",), (), "class_match"))
