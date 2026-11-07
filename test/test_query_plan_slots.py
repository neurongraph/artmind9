"""Slot fillers (planner v2 spec §4). Invented data only."""

from dataclasses import dataclass, field
from datetime import date

import pytest

from artmind.query_plan import slots
from artmind.query_plan.candidates import Mention


# Local doubles of the config contract (slice C extends the real PlanConfig).
@dataclass(frozen=True)
class Narrator:
    entity_class: str
    name: str
    aliases: tuple = ()


@dataclass(frozen=True)
class Rule:
    id: str
    when_mention: str | None = None
    when_not_cue: tuple = ()
    when_route: str | None = None
    set_participants: str | None = None
    exclude_classes: tuple = ()
    require_participant: str | None = None


@dataclass(frozen=True)
class Event:
    name: str
    month_day: str
    span_days: int
    recurring: str = "yearly"
    year: int | None = None


@dataclass(frozen=True)
class Cfg:
    narrator: Narrator | None = None
    categories: tuple = ()
    kinship: tuple = ()
    scope_rules: tuple = ()
    calendar: tuple = ()

    def category(self, word):
        w = word.casefold()
        for key, members in self.categories:
            if w in (key, key + "s", key + "es"):
                return members
        return None


def m(text, via="capitalised"):
    return Mention(text, 0, len(text), via)


# ---- narrator

NARR = Cfg(narrator=Narrator("Person", "Ann Lee", ("Annie", "A. Lee")))


def test_narrator_first_person():
    s = slots.narrator_slot([m("I", "first_person")], NARR)
    assert s.value == {"entity_class": "Person", "name": "Ann Lee"} and s.source == "narrator"


def test_narrator_alias_whole_text():
    assert slots.narrator_slot([m("annie")], NARR).source == "alias"
    assert slots.narrator_slot([m("Annie Brown")], NARR) is None


def test_narrator_none_without_config_or_mentions():
    assert slots.narrator_slot([m("I", "first_person")], Cfg()) is None
    assert slots.narrator_slot([m("Bob Roe")], NARR) is None


def test_narrator_stub_gap():
    ents = [{"candidates": [{"_id": "n1", "name": "Ann Lee", "entity_class": "Person"},
                            {"_id": "n2", "name": "annie", "entity_class": "Person"},
                            {"_id": "x", "name": "Annie", "entity_class": "Pet"}]}]
    gaps = slots.narrator_stub_gaps(ents, NARR, narrator_id="n1")
    assert gaps[0]["kind"] == "identity" and [e["_id"] for e in gaps[0]["entities"]] == ["n1", "n2"]
    assert slots.narrator_stub_gaps(ents[:1], NARR, narrator_id="n1")[0]["entities"][1]["_id"] == "n2"
    only = [{"candidates": [{"_id": "n1", "name": "Ann Lee", "entity_class": "Person"}]}]
    assert slots.narrator_stub_gaps(only, NARR, "n1") == []
    assert slots.narrator_stub_gaps(only, Cfg()) == []


# ---- search term

def test_search_term_from_mentions_with_expansion():
    s = slots.search_term_slot("What did Ann Lee say about doctors?", [m("Ann Lee")], ["Dentist", "dental"])
    assert s.source == "mention" and s.quality == 1.0
    assert s.value == '"Ann Lee" Dentist dental'


def test_search_term_ignores_first_person_mentions():
    s = slots.search_term_slot("which doctors did I see", [m("I", "first_person")], [])
    assert s.source == "filler_stripped" and s.value == "doctors see"


def test_search_term_filler_stripped():
    s = slots.search_term_slot("Tell me about all the dentist visits", [], [])
    assert s.value == "dentist visits"


def test_search_term_empty_after_filler():
    s = slots.search_term_slot("What did I do last year?", [m("I", "first_person")], [])
    assert (s.value, s.source, s.quality) == ("", "question", slots.EMPTY_SEARCH)


def test_search_term_only_expansion():
    s = slots.search_term_slot("what is there", [], ["dental"])
    assert (s.value, s.source) == ("dental", "category")


def test_search_term_dedupes_and_sanitises():
    s = slots.search_term_slot("x", [m("Ann Lee"), m("ann lee")], ['(Ann]'])
    assert s.value.count("Ann") == 2 or s.value.casefold().count("ann") >= 1
    assert "(" not in s.value and "]" not in s.value


# ---- categories

CLASSES = {"Doctor": "A physician", "Dentist": "", "Town": "A settlement", "BANK_ACCOUNT": ""}
DOC_CFG = Cfg(categories=(("doctor", ("Doctor", "Dentist", "assessment")),))


def test_category_cfg_wins_and_splits_members():
    e = slots.category_expansion("list all medical specialists, ex-dentists", CLASSES,
                                 Cfg(categories=(("specialist", ("Doctor", "Dentist", "assessment")),)))
    assert e.classes == ("Doctor", "Dentist") and e.terms == ("assessment",) and e.source == "category"
    assert not e.narrowed


def test_category_key_sharing_a_member_class_name_expands_fully():
    e = slots.category_expansion("which doctors did I see", CLASSES, DOC_CFG)
    assert e.classes == ("Doctor", "Dentist") and e.terms == ("assessment",) and not e.narrowed


def test_member_level_word_narrows_through_class_match():
    e = slots.category_expansion("which dentists did I see", CLASSES, DOC_CFG)
    assert e.classes == ("Dentist",) and e.terms == () and e.narrowed and e.source == "class_match"


def test_class_name_match_plural_and_underscore():
    assert slots.category_expansion("which towns", CLASSES, Cfg()).classes == ("Town",)
    e = slots.category_expansion("my bank accounts", CLASSES, Cfg())
    assert e.classes == ("BANK_ACCOUNT",) and e.source == "class_match"


def test_class_description_match_unique_only():
    assert slots.category_expansion("a physician note", CLASSES, Cfg()).classes == ("Doctor",)
    dup = {"A": "physician", "B": "physician"}
    assert slots.category_expansion("physician", dup, Cfg()) is None


def test_category_none():
    assert slots.category_expansion("hello there", CLASSES, Cfg()) is None
    assert slots.category_expansion("towns", [], Cfg()) is None


def test_catalogue_classes_as_list_of_tuples():
    assert slots.category_expansion("towns", [("Town", "x")], Cfg()).classes == ("Town",)


# ---- twins

def ref(i, name, cls="Person"):
    return {"_id": i, "name": name, "entity_class": cls}


def test_twin_name_equality_and_class():
    me = ref("1", "Bob Roe")
    out = slots.twins_for(me, [ref("2", "bob roe"), ref("3", "Bob Roe", "Pet"), me], [])
    assert [(t["_id"], t["why"]) for t in out] == [("2", "name")]


def test_twin_first_name_needs_shared_spouse():
    me, full = ref("1", "Bob"), ref("2", "Bob Roe")
    assert slots.twins_for(me, [full], [], {}, None) == []
    assert slots.twins_for(me, [full], [], {"1": "s"}, None) == []
    assert slots.twins_for(me, [full], [], {"1": "s", "2": "t"}, None) == []
    out = slots.twins_for(me, [full], [], {"1": "s", "2": "s"}, None)
    assert out[0]["why"] == "shared_spouse"
    # symmetric
    assert slots.twins_for(full, [me], [], {"1": "s", "2": "s"})[0]["why"] == "shared_spouse"
    # different first names never match, even with a shared spouse
    assert slots.twins_for(ref("1", "Cy"), [full], [], {"1": "s", "2": "s"}) == []


def test_twin_kinship_from_groups_and_cfg():
    groups = slots.kin_groups(Cfg(kinship=(("aunty", ("auntie", "tia")),)), base_groups=[["dad", "papa"]])
    assert slots.twins_for(ref("1", "Dad"), [ref("2", "Papa")], groups)[0]["why"] == "kinship"
    assert slots.twins_for(ref("1", "Auntie"), [ref("2", "Tia")], groups)[0]["why"] == "kinship"
    assert slots.twins_for(ref("1", "Dad"), [ref("2", "Tia")], groups) == []


def test_kin_groups_loads_default_yaml():
    groups = slots.kin_groups(Cfg())
    assert any({"mom", "mother"} <= g for g in groups)


def test_twin_open_same_as():
    out = slots.twins_for(ref("1", "Ann"), [ref("2", "Zed")], [], None, {frozenset(("1", "2"))})
    assert out[0]["why"] == "same_as"


# ---- scope

RULES = (
    Rule("we_is_family", when_mention="we", set_participants="family"),
    Rule("no_work", when_not_cue=("work", "office"), exclude_classes=("WorkEvent",)),
    Rule("friction", when_route="relationship", require_participant="narrator"),
)


def test_scope_given_and_rules():
    cfg = Cfg(scope_rules=RULES)
    s = slots.scope_slot(["j"], ["j"], 0.2, 0.7, cfg, "where did we go", "search", ["d1", "d1"])
    assert s.source == "given" and s.quality == 1.0
    assert s.value.rules == ("we_is_family", "no_work")
    assert s.value.filters == {"exclude_classes": ["WorkEvent"], "participants": ["family"],
                               "require_participant": None}
    assert s.value.exclude_documents == ("d1",)


def test_scope_cue_blocks_rule_and_route_rule():
    cfg = Cfg(scope_rules=RULES)
    s = slots.scope_slot([], ["j"], 0.9, 0.7, cfg, "my office dispute", "relationship")
    assert s.source == "chosen" and s.value.rules == ("friction",)
    assert s.value.filters["require_participant"] == "narrator"
    # "we" is a whole word: "went" does not trip it
    assert "we_is_family" not in slots.scope_slot([], ["j"], 0.9, 0.7, cfg, "went home", "x").value.rules


def test_scope_all_domains_quality():
    s = slots.scope_slot([], ["a", "b"], 0.3, 0.7, Cfg(), "q", None)
    assert (s.source, s.quality, s.value.domains) == ("all", slots.ALL_DOMAINS, ("a", "b"))


# ---- date window

TODAY = date(2026, 11, 5)
CAL = Cfg(calendar=(Event("Lantern Fest", "10-20", 5), Event("Winter Fair", "12-30", 5),
                    Event("Founding", "03-01", 2, "once", 2019)))


def win(q, cfg=Cfg(), doc=None):
    s = slots.date_window_slot(q, TODAY, cfg, doc)
    return None if s is None else (s.value["from"], s.value["to"], s.value["event"], s.source, s.note)


def test_calendar_event_most_recent_occurrence():
    assert win("what happened at Lantern Fest", CAL) == ("2026-10-20", "2026-10-25", "Lantern Fest", "calendar", None)
    # this year's Winter Fair is still ahead of today: last year's is used, across the year boundary
    assert win("winter fair photos", CAL) == ("2025-12-30", "2026-01-04", "Winter Fair", "calendar", None)


def test_calendar_event_with_year_and_once():
    assert win("lantern fest 2023", CAL)[:2] == ("2023-10-20", "2023-10-25")
    assert win("the founding party", CAL)[:2] == ("2019-03-01", "2019-03-03")


def test_explicit_dates():
    assert win("on 2024-02-29")[:2] == ("2024-02-29", "2024-03-01")
    assert win("on 3 March 2024")[:2] == ("2024-03-03", "2024-03-04")
    assert win("in Dec 2023")[:2] == ("2023-12-01", "2024-01-01")
    assert win("in 2022") == ("2022-01-01", "2023-01-01", None, "explicit_date", None)


def test_partial_current_year_and_month():
    assert win("in 2026")[4] == "partial_year"
    assert win("this year")[4] == "partial_year"
    assert win("in November 2026")[4] == "partial_month"
    assert win("last year") == ("2025-01-01", "2026-01-01", None, "relative", None)


def test_relative_windows():
    assert win("last month")[:2] == ("2026-10-01", "2026-11-01")
    assert win("last week")[:2] == ("2026-10-26", "2026-11-02")
    assert win("yesterday")[:2] == ("2026-11-04", "2026-11-05")
    assert slots.date_window_slot("last month", date(2026, 1, 15), Cfg()).value["from"] == "2025-12-01"
    assert win("last year", doc=date(2020, 6, 1))[:2] == ("2019-01-01", "2020-01-01")


def test_no_date():
    assert win("who is Ann Lee") is None


# ---- follow-up

def test_follow_up_subjects():
    out = slots.follow_up_subjects([{"_id": "1", "name": "Ann Lee"}, "Bob"], [{"name": "notes.md"}, ""])
    assert [(s.text, s.kind, s.via) for s in out] == [
        ("Ann Lee", "entity", "previous"), ("Bob", "entity", "previous"), ("notes.md", "document", "previous")]
    assert out[0].ref["_id"] == "1"
    assert slots.follow_up_subjects([], []) == []


# ---- quality

def test_slot_quality_product_and_breakdown():
    q, info = slots.slot_quality({}, twins_found=True, twins_unioned=False, entity_from_fuzzy=True,
                                 search_term_empty=True, all_domains=True)
    assert q == pytest.approx(0.7 * 0.5 * 0.6 * 0.8)
    assert info["weakest"] == "twins" and info["factors"]["mention"] == slots.FUZZY_MENTION


def test_slot_quality_clean_and_unioned():
    assert slots.slot_quality({}, twins_found=True, twins_unioned=True) == (1.0, {
        "factors": {"mention": 1.0, "twins": 1.0, "search_term": 1.0, "scope": 1.0, "topic": 1.0}, "weakest": None})


def test_slot_quality_reads_slots():
    sl = {"search_term": slots.Slot("search_term", "", "question", 0.6),
          "scope": slots.Slot("scope", None, "all", 0.8)}
    q, info = slots.slot_quality(sl)
    assert q == pytest.approx(0.48) and info["weakest"] == "search_term"


# ---- phrasings, list slots and cues (planner v2 P2b)

@dataclass(frozen=True)
class SynCfg:
    synonyms: tuple = ()

    def synonym(self, word):
        w = word.casefold()
        for key, members in self.synonyms:
            if w in (key, key + "s", key + "es"):
                return members
        return None


Q = "Was there a sauna with Ada at the lake?"


def _ph(question=Q, mentions=(), term="sauna", cfg=None, classes=None):
    return slots.phrasings_slot(question, list(mentions), term, cfg, classes or {})


def test_phrasings_are_the_term_then_config_synonyms_then_a_paraphrase_without_the_person():
    mention = Mention("Ada", Q.index("Ada"), Q.index("Ada") + 3, "capitalised")
    slot = _ph(mentions=[mention], cfg=SynCfg((("sauna", ("steam room", "bathhouse")),)))
    assert slot.name == "phrasings" and slot.quality == 1.0
    assert slot.value == ["sauna", "steam room", "bathhouse", "sauna lake"]
    assert "ada" not in " ".join(slot.value).casefold()
    assert slot.source == "search_term+config_synonyms+paraphrase"


def test_catalogue_class_descriptions_add_synonyms_after_the_configured_ones():
    classes = {"STEAM_ROOM": "A sauna or hot room", "HOBBY": "Something done for fun", "SPA": "sauna spa"}
    slot = _ph(term="sauna", cfg=SynCfg((("sauna", ("bathhouse",)),)), classes=classes)
    assert slot.value[:4] == ["sauna", "bathhouse", "steam room", "spa"]
    assert "catalogue_synonyms" in slot.source and "config_synonyms" in slot.source


def test_synonyms_are_capped_at_three_and_phrasings_at_five():
    cfg = SynCfg((("sauna", ("a1", "a2", "a3", "a4", "a5")),))
    slot = _ph(cfg=cfg)
    assert slot.value[:4] == ["sauna", "a1", "a2", "a3"] and len(slot.value) == 5  # + the paraphrase
    assert len(slots.phrasings_slot("x " * 9, [], "t", SynCfg((("t", tuple(f"s{i}" for i in range(9))),)), {}).value) <= 5


def test_phrasings_dedupe_casefold_and_skip_empty_pieces():
    slot = slots.phrasings_slot("Sauna?", [], "sauna", SynCfg((("sauna", ("SAUNA", "Steam")),)), {})
    assert slot.value == ["sauna", "steam"]  # the paraphrase "sauna" repeats the term
    empty = slots.phrasings_slot("What was there?", [], "", None, {})
    assert empty.value == [] and empty.quality == 1.0


def test_phrasings_work_with_a_config_that_has_no_synonyms():
    class Plain:
        pass
    assert _ph(cfg=Plain()).value[0] == "sauna" and _ph(cfg=None).value[0] == "sauna"


def test_years_cover_the_window_and_stop_at_ten():
    def win(a, b):
        return slots.Slot("date_window", {"from": a, "to": b, "event": None}, "explicit_date", 1.0)
    assert slots.years_slot(None).value == []
    assert slots.years_slot(win("2025-01-01", "2026-01-01")).value == [2025]
    assert slots.years_slot(win("2023-06-01", "2025-03-01")).value == [2023, 2024, 2025]
    assert slots.years_slot(win("2023-01-01", "2025-01-01")).value == [2023, 2024]  # `to` is exclusive
    assert slots.years_slot(win("2000-01-01", "2020-01-01")).value == list(range(2000, 2010))


def test_twin_ids_start_with_the_primary_and_dedupe():
    primary = {"candidates": [{"_id": "p1"}], "twins": [{"_id": "p2"}, {"_id": "p1"}, {"_id": "p3"}]}
    assert slots.twin_ids_slot(primary).value == ["p1", "p2", "p3"]
    assert slots.twin_ids_slot({"candidates": [{"_id": "p1"}]}).value == ["p1"]
    assert slots.twin_ids_slot(None).value == [] and slots.twin_ids_slot({"candidates": []}).value == []


def test_rare_term_compares_with_the_name_index_and_class_names():
    names = ["ada lovelace", "greenhouse", "pier"]
    assert slots.rare_term(["sauna"], names, ["PERSON", "HOBBY"]) is True
    assert slots.rare_term(["greenhouses"], names, ["PERSON"]) is False  # plural of a known word
    assert slots.rare_term(["hobbies"], names, ["HOBBY"]) is False
    assert slots.rare_term(["sauna"], [], []) is False  # no catalogue: nothing to compare with
    assert slots.rare_term([], names, []) is False


@pytest.mark.parametrize("question, fn, expected", [
    ("Was it ever mentioned?", slots.has_negative_cue, True),
    ("Did we never go there?", slots.has_negative_cue, True),
    ("We didn't go there", slots.has_negative_cue, True),
    ("Where did we go?", slots.has_negative_cue, False),
    ("What is the latest version?", slots.has_latest_cue, True),
    ("Is it currently amended?", slots.has_latest_cue, True),
    ("What is the first version?", slots.has_latest_cue, False),
    ("How many visits?", slots.has_count_cue, True),
    ("What is the number of visits?", slots.has_count_cue, True),
    ("Which visits?", slots.has_count_cue, False),
])
def test_cues(question, fn, expected):
    assert fn(question) is expected
