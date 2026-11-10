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


def test_place_seed_city_and_parent():
    assert rules.decide_place({"name": "Metro City", "place_type": "city"}, "location") == {"city": "Metro City"}
    assert rules.decide_place({"name": "A Cafe", "place_type": "restaurant", "location": "Metro City"},
                              "location") == {"venue": "A Cafe", "city": "Metro City"}
    assert rules.decide_place({"name": "Somewhere", "place_type": "park"}, "location") is None


def test_home_away():
    assert rules.home_away({"city": "Hometown"}, "Hometown") == "home"
    assert rules.home_away({"city": "Elsewhere"}, "Hometown") == "away"
    assert rules.home_away({}, "Hometown") is None
    assert rules.home_away({"city": "X"}, None) == "away"
