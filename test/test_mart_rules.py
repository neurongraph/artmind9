from artmind.mart import rules, words
from artmind.mart.declaration import TypeDim

W = words.load("kinship")


def test_person_exact_words():
    assert rules.decide_person("colleague", W) == {"group": "colleague", "relation": None, "side": None}
    assert rules.decide_person("Father", W)["relation"] == "parent"


def test_person_kinship_terms_and_side():
    assert rules.decide_person("uncle (mama)", W) == {"group": "family", "relation": "uncle", "side": "maternal"}
    assert rules.decide_person("aunt (pishi)", W) == {"group": "family", "relation": "aunt", "side": "paternal"}
    assert rules.decide_person("mother-in-law", W) == {"group": "family", "relation": "in-law", "side": "marital"}


def test_person_unknown_is_none():
    assert rules.decide_person("new attendant for someone", W) is None
    assert rules.decide_person(None, W) is None


def test_type_family():
    t = TypeDim(families={"trip": ["visit", "outing"]})
    assert rules.decide_type("Outing", t) == {"family": "trip"}
    assert rules.decide_type("meeting", t) is None


def test_place_seed_city_only_when_it_names_a_known_city():
    cities = {"metro city", "hometown"}
    assert rules.decide_place({"name": "Metro City", "place_type": "city"}, "location", cities) == {"city": "Metro City"}
    assert rules.decide_place({"name": "A Cafe", "location": "Metro City"}, "location", cities) == \
        {"venue": "A Cafe", "city": "Metro City"}
    assert rules.decide_place({"name": "Desk", "location": "7th floor, Tower One, Hometown"}, "location", cities) == \
        {"venue": "Desk", "city": "Hometown", "locality": "7th floor, Tower One, Hometown"}
    assert rules.decide_place({"name": "Shop", "location": "near the temple"}, "location", cities) == \
        {"venue": "Shop", "locality": "near the temple"}
    assert rules.decide_place({"name": "Somewhere", "place_type": "park"}, "location", cities) is None


def test_home_away():
    assert rules.home_away({"city": "Hometown"}, "Hometown") == "home"
    assert rules.home_away({"city": "Elsewhere"}, "Hometown") == "away"
    assert rules.home_away({}, "Hometown") is None
    assert rules.home_away({"city": "X"}, None) == "away"


def test_chain_places_takes_the_city_of_a_locality_that_is_a_place():
    seeded = {"cafe": {"venue": "Cafe", "locality": "Uptown"}, "uptown": {"venue": "Uptown", "city": "Metro"}}
    out = rules.chain_places(seeded, {"cafe": "Cafe", "uptown": "Uptown"})
    assert out["cafe"] == {"venue": "Cafe", "locality": "Uptown", "city": "Metro"}


def test_home_away_counts_a_stay_without_a_city_as_away():
    assert rules.home_away({}, "Hometown", "resort", ["resort", "villa"]) == "away"
    assert rules.home_away({}, "Hometown", "restaurant", ["resort", "villa"]) is None
    assert rules.home_away({"city": "Hometown"}, "Hometown", "resort", ["resort"]) == "home"
