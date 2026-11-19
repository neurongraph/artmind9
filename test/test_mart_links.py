"""Dimension links (spec 2026-11-19): a fact's members from edges, then declared properties, then the same chunk."""
from artmind.mart import links
from artmind.mart.declaration import PersonDim, PlaceDim

PEOPLE = [{"key": "kid", "props": {"name": "Kid One", "aliases": ["Kiddo"]}},
          {"key": "kid2", "props": {"name": "Kid Two"}},
          {"key": "amb1", "props": {"name": "Sam Lee"}}, {"key": "amb2", "props": {"name": "Sam Lee"}}]
PLACES = [{"key": "hill", "props": {"name": "Hill Town"}}, {"key": "inn", "props": {"name": "Lake Inn"}},
          {"key": "spa", "props": {"name": "Spa"}}]


def _facts(**props):
    return [{"key": "e", "class": "EVENT", "dim_props": props}]


def _run(facts, edges=(), same=(), person=None, place=None):
    return links.link_all(
        facts, list(edges), {"PERSON": list(same) if person and person.same_chunk else [],
                             "LOCATION": list(same) if place and place.same_chunk else []},
        person or PersonDim(), place or PlaceDim(), {"PERSON": PEOPLE, "LOCATION": PLACES})


def test_edges_come_first_and_keep_their_role():
    out = _run(_facts(location="Lake Inn"),
               edges=[{"fact_key": "e", "member_key": "hill", "member_class": "LOCATION", "role": "OCCURS_AT"}],
               place=PlaceDim(from_properties={"EVENT": ["location"]}))
    assert out[0] == {"fact_key": "e", "member_key": "hill", "member_class": "LOCATION", "role": "OCCURS_AT"}
    assert {"fact_key": "e", "member_key": "inn", "member_class": "LOCATION", "role": "property:location"} in out


def test_a_property_links_a_fact_with_no_edge():
    out = _run(_facts(location="Lake Inn, Hill Town"), place=PlaceDim(from_properties={"EVENT": ["location"]}))
    assert [(b["member_key"], b["role"]) for b in out] == [("inn", "property:location"), ("hill", "property:location")]


def test_list_values_aliases_and_ambiguous_names():
    out = _run(_facts(participants=["Kiddo", "Sam Lee", "Nobody Known"]),
               person=PersonDim(from_properties={"EVENT": ["participants"]}))
    assert [(b["member_key"], b["role"]) for b in out] == [("kid", "property:participants")]


def test_collectives_apply_to_person_properties():
    out = _run(_facts(participants=["the diarist and the other group members"]),
               person=PersonDim(from_properties={"EVENT": ["participants"]},
                                collectives={"group members": ["Kid One", "Kid Two"]}))
    assert sorted(b["member_key"] for b in out) == ["kid", "kid2"]


def test_same_chunk_only_when_edges_and_properties_found_nothing():
    same = [{"fact_key": "e", "member_key": "hill"}]
    place = PlaceDim(from_properties={"EVENT": ["location"]}, same_chunk=True)
    assert [(b["member_key"], b["role"]) for b in _run(_facts(), same=same, place=place)] == [("hill", "same_chunk")]
    out = _run(_facts(location="Lake Inn"), same=same, place=place)
    assert [(b["member_key"], b["role"]) for b in out] == [("inn", "property:location")]


def test_same_chunk_is_off_unless_declared():
    out = _run(_facts(), same=[{"fact_key": "e", "member_key": "hill"}], place=PlaceDim())
    assert out == []


def test_a_member_found_twice_is_linked_once_with_the_first_role():
    out = _run(_facts(location="Hill Town"),
               edges=[{"fact_key": "e", "member_key": "hill", "member_class": "LOCATION", "role": "OCCURS_AT"}],
               place=PlaceDim(from_properties={"EVENT": ["location"]}))
    assert [(b["member_key"], b["role"]) for b in out] == [("hill", "OCCURS_AT")]


def test_short_place_names_match_but_short_person_names_do_not():
    places = [{"key": "goa", "props": {"name": "Goa"}}]
    out = links.link_all(_facts(location="Goa"), [], {"PERSON": [], "LOCATION": []}, PersonDim(),
                         PlaceDim(from_properties={"EVENT": ["location"]}), {"PERSON": [], "LOCATION": places})
    assert [b["member_key"] for b in out] == ["goa"]
    people = [{"key": "al", "props": {"name": "Al"}}]
    out = links.link_all(_facts(participants=["Al"]), [], {"PERSON": [], "LOCATION": []},
                         PersonDim(from_properties={"EVENT": ["participants"]}), PlaceDim(),
                         {"PERSON": people, "LOCATION": []})
    assert out == []


def test_a_class_without_declared_properties_uses_edges_only():
    facts = [{"key": "a", "class": "ACTIVITY", "dim_props": {"location": "Lake Inn"}}]
    out = _run(facts, place=PlaceDim(from_properties={"EVENT": ["location"]}))
    assert out == []
