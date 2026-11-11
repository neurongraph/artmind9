from artmind.mart import wholes
from artmind.mart.declaration import MartDeclaration, PersonDim, PlaceDim, TypeDim, Whole


def decl(**w):
    return MartDeclaration("j", 1, "Home", ["EVENT"], ["EVENT"], False, PersonDim(), PlaceDim(),
                           TypeDim(["EVENT"], "type", {"trip": ["outing"], "health": ["illness"], "ritual": ["rite"]}),
                           w, {})


def fact(k, day, fam, away=True, name=None):
    return {"entity_key": k, "name": name or k, "date": day, "type_family": fam, "home_away": "away" if away else "home",
            "place_key": f"pl-{k}"}


TRIP = Whole("trip", families=["trip"], place="away", consecutive_days=1, share=["participant", "place"],
             alone_never_joins=["narrator", "household"])
LIFE = Whole("life_event", families=["health", "ritual"], anchor="person", match=["edge", "name", "participants"],
             window_gap_days=21)


def test_trip_groups_consecutive_away_days_with_a_shared_companion():
    t = {"fact_happening": [fact("a", "2025-01-01", "trip"), fact("b", "2025-01-02", "trip"),
                            fact("c", "2025-01-09", "trip"), fact("d", "2025-01-03", "trip", away=False)],
         "bridge_person": [{"entity_key": k, "person_key": "kid", "role": "participates_in"} for k in "abcd"],
         "dim_person": [{"person_key": "kid", "group": "family"}]}
    out = wholes.compute(decl(trip=TRIP), t)
    trips = sorted(out["whole_trip"], key=lambda r: r["start"])
    assert [(r["start"], r["end"]) for r in trips] == [("2025-01-01", "2025-01-02"), ("2025-01-09", "2025-01-09")]
    assert {r["entity_key"] for r in out["fact_whole"] if r["whole_id"] == trips[0]["whole_id"]} == {"a", "b"}


def test_an_unknown_day_between_away_days_stays_in_the_trip():
    t = {"fact_happening": [fact("a", "2025-01-01", "trip"), {**fact("b", "2025-01-02", "trip"), "home_away": None},
                            fact("c", "2025-01-03", "trip"), fact("h", "2025-01-04", "work", away=False)],
         "bridge_person": [], "dim_person": []}
    trips = wholes.compute(decl(trip=TRIP), t)["whole_trip"]
    assert [(r["start"], r["end"]) for r in trips] == [("2025-01-01", "2025-01-03")]


def test_unknown_days_alone_are_not_a_trip():
    t = {"fact_happening": [{**fact("a", "2025-01-01", "trip"), "home_away": None}], "bridge_person": [], "dim_person": []}
    assert wholes.compute(decl(trip=TRIP), t)["whole_trip"] == []


def test_life_event_by_edge_and_name_with_aftermath():
    t = {"fact_happening": [fact("ill", "2025-02-01", "health"), fact("dies", "2025-02-10", "health"),
                            fact("rite", "2025-02-20", "ritual", name="Uncle's rite"),
                            fact("late", "2025-05-01", "ritual", name="Uncle's anniversary")],
         "bridge_person": [{"entity_key": "ill", "person_key": "u", "role": "is_affected_by"},
                           {"entity_key": "dies", "person_key": "u", "role": "is_affected_by"}],
         "dim_person": [{"person_key": "u", "name": "Uncle", "group": "family"}]}
    out = wholes.compute(decl(life_event=LIFE), t)
    ev = sorted(out["whole_life_event"], key=lambda r: r["start"])
    assert (ev[0]["start"], ev[0]["end"]) == ("2025-02-01", "2025-02-20")
    roles = {r["entity_key"]: r["role"] for r in out["fact_whole"] if r["whole_id"] == ev[0]["whole_id"]}
    assert roles == {"ill": "during", "dies": "during", "rite": "aftermath"}


def test_life_event_rows_carry_the_anchor_and_trips_their_companions():
    t = {"fact_happening": [fact("a", "2025-01-01", "trip"), fact("b", "2025-01-02", "trip")],
         "bridge_person": [{"entity_key": "a", "person_key": "kid", "role": "participates_in"},
                           {"entity_key": "b", "person_key": "me", "role": "participates_in"}],
         "dim_person": [{"person_key": "kid", "group": "family"}, {"person_key": "me", "group": "self"}]}
    trip = wholes.compute(decl(trip=TRIP), t)["whole_trip"][0]
    assert trip["participant_keys"] == ["kid"] and trip["anchor_key"] is None
    t2 = {"fact_happening": [fact("ill", "2025-02-01", "health")],
          "bridge_person": [{"entity_key": "ill", "person_key": "u", "role": "is_affected_by"}],
          "dim_person": [{"person_key": "u", "name": "Uncle", "group": "family"}]}
    assert wholes.compute(decl(life_event=LIFE), t2)["whole_life_event"][0]["anchor_key"] == "u"


def test_trip_companions_come_from_participant_names_too():
    t = {"fact_happening": [{**fact("a", "2025-01-01", "trip"), "participants": ["Kiddo"]}],
         "bridge_person": [], "dim_person": [{"person_key": "kid", "name": "Kid Person", "aliases": ["Kiddo"], "group": "family"}]}
    assert wholes.compute(decl(trip=TRIP), t)["whole_trip"][0]["participant_keys"] == ["kid"]


def test_collective_names_link_their_members():
    t = {"fact_happening": [{**fact("a", "2025-01-01", "trip"), "participants": ["Children"]}], "bridge_person": [],
         "dim_person": [{"person_key": "k1", "name": "Kid One", "group": "family"}, {"person_key": "k2", "name": "Kid Two", "group": "family"}]}
    d = decl(trip=TRIP)
    d.person.collectives = {"children": ["Kid One", "Kid Two"]}
    assert wholes.compute(d, t)["whole_trip"][0]["participant_keys"] == ["k1", "k2"]


def test_a_run_splits_where_companions_and_cities_both_change():
    t = {"fact_happening": [{**fact("a", "2025-01-01", "trip"), "place_key": "p1"},
                            {**fact("b", "2025-01-02", "work"), "place_key": "p2"}],
         "bridge_person": [{"entity_key": "a", "person_key": "kid", "role": "x"}, {"entity_key": "b", "person_key": "boss", "role": "x"}],
         "dim_person": [{"person_key": "kid", "group": "family"}, {"person_key": "boss", "group": "colleague"}],
         "dim_place": [{"place_key": "p1", "city": "A"}, {"place_key": "p2", "city": "B"}]}
    trips = wholes.compute(decl(trip=TRIP), t)["whole_trip"]
    assert sorted((r["start"], r["end"]) for r in trips) == [("2025-01-01", "2025-01-01"), ("2025-01-02", "2025-01-02")]


def test_whole_rows_carry_their_part_names_and_families():
    t = {"fact_happening": [fact("ill", "2025-02-01", "health", name="fall at home"),
                            fact("rite", "2025-02-10", "ritual", name="last rites")],
         "bridge_person": [{"entity_key": "ill", "person_key": "u", "role": "is_affected_by"},
                           {"entity_key": "rite", "person_key": "u", "role": "is_affected_by"}],
         "dim_person": [{"person_key": "u", "name": "Uncle", "group": "family"}]}
    row = wholes.compute(decl(life_event=LIFE), t)["whole_life_event"][0]
    assert row["part_names"] == ["fall at home", "last rites"] and row["families"] == ["health", "ritual"]


def _edge_case(edge_people):
    unknown = {**fact("e", "2025-01-01", "trip", name="drive"), "home_away": None, "place_key": None}
    t = {"fact_happening": [unknown, fact("a", "2025-01-02", "trip"), fact("b", "2025-01-03", "trip")],
         "bridge_person": [{"entity_key": "a", "person_key": "kid", "role": "x"},
                           *[{"entity_key": "e", "person_key": p, "role": "x"} for p in edge_people]],
         "dim_person": [{"person_key": "kid", "group": "family"}, {"person_key": "boss", "group": "colleague"}]}
    return wholes.compute(decl(trip=TRIP), t)


def test_an_unknown_edge_day_sharing_a_companion_joins_the_trip():
    out = _edge_case(["kid"])
    trip = out["whole_trip"][0]
    assert (trip["start"], trip["end"]) == ("2025-01-01", "2025-01-03")
    assert "e" in {r["entity_key"] for r in out["fact_whole"]}


def test_an_unknown_edge_day_without_a_shared_companion_stays_out():
    assert [(r["start"], r["end"]) for r in _edge_case(["boss"])["whole_trip"]] == [("2025-01-02", "2025-01-03")]
    assert [(r["start"], r["end"]) for r in _edge_case([])["whole_trip"]] == [("2025-01-02", "2025-01-03")]


def test_edge_days_do_not_chain():
    t = {"fact_happening": [{**fact(k, f"2025-01-0{i}", "trip"), "home_away": None, "place_key": None}
                            for i, k in ((1, "x"), (2, "y"))] + [fact("a", "2025-01-03", "trip")],
         "bridge_person": [{"entity_key": k, "person_key": "kid", "role": "x"} for k in "xya"],
         "dim_person": [{"person_key": "kid", "group": "family"}]}
    assert [(r["start"], r["end"]) for r in wholes.compute(decl(trip=TRIP), t)["whole_trip"]] == [("2025-01-02", "2025-01-03")]


def test_an_unknown_edge_day_that_is_not_an_outing_stays_out():
    t = {"fact_happening": [{**fact("e", "2025-01-01", "social"), "home_away": None, "place_key": None},
                            fact("a", "2025-01-02", "trip")],
         "bridge_person": [{"entity_key": k, "person_key": "kid", "role": "x"} for k in "ea"],
         "dim_person": [{"person_key": "kid", "group": "family"}]}
    assert [(r["start"], r["end"]) for r in wholes.compute(decl(trip=TRIP), t)["whole_trip"]] == [("2025-01-02", "2025-01-02")]


def test_trip_rows_carry_place_names_and_cities():
    t = {"fact_happening": [fact("a", "2025-01-01", "trip"), fact("b", "2025-01-02", "trip")],
         "bridge_person": [], "dim_person": [],
         "dim_place": [{"place_key": "pl-a", "name": "Fort", "city": "Jodhpur"}, {"place_key": "pl-b", "name": "Villa", "city": None}]}
    row = wholes.compute(decl(trip=TRIP), t)["whole_trip"][0]
    assert row["place_names"] == ["Fort", "Villa"] and row["cities"] == ["Jodhpur"]
