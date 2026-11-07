"""B12: confidence guards for shapes the decision model plans wrongly at high confidence (invented text only)."""

from artmind.query_plan import guards, intents, planner
from test_query_plan_planner import FakeClassifier, _answer, _person, _plan, _use, world  # noqa: F401


def _ent(via, name="X", cls="PERSON"):
    return {"mention": name, "span": [0, 1], "via": via, "candidates": [{"_id": name, "name": name, "entity_class": cls}]}


# ── pure guard rules ──────────────────────────────────────────────────────────────────────

def test_path_between_two_catalogue_entities_fires():
    hits = guards.evaluate("path", "retrieve", "how do I relate to my cousins and my neighbours",
                           [_ent("first_person"), _ent("catalogue", "cousins"), _ent("catalogue", "neighbours")])
    assert [h["id"] for h in hits] == ["path.unnamed"]


def test_path_between_spelled_out_names_does_not_fire():
    assert guards.evaluate("path", "retrieve", "How is Ada related to Bob?",
                           [_ent("capitalised", "Ada"), _ent("capitalised", "Bob")]) == []
    assert guards.evaluate("path", "retrieve", "q", [_ent("capitalised", "Ada"), _ent("catalogue", "Bob")]) == []
    assert guards.evaluate("entity_profile", "retrieve", "q", [_ent("catalogue"), _ent("catalogue")]) == []


def test_follow_up_why_with_two_named_entities_fires_one_does_not():
    two = [_ent("capitalised", "Ada"), _ent("capitalised", "Bob"), _ent("first_person")]
    assert [h["id"] for h in guards.evaluate("search", "follow_up_why", "q", two)] == ["follow_up_why.names"]
    assert guards.evaluate("search", "follow_up_why", "q", two[:1]) == []
    assert guards.evaluate("search", "retrieve", "q", two) == []


def test_conflicts_about_a_superseded_version_fires():
    for q in ("Two limits appear in the old policy. Which one should I follow?",
              "Which version of the rule applies now?", "What changed from the previous procedure?",
              "Is the latest version different?"):
        assert [h["id"] for h in guards.evaluate("conflicts", "retrieve", q, [])] == ["conflicts.version"], q
    assert guards.evaluate("conflicts", "retrieve", "Do the two manuals contradict each other?", []) == []
    assert guards.evaluate("search", "retrieve", "Which version applies?", []) == []


# ── in the plan ───────────────────────────────────────────────────────────────────────────

def test_a_fired_guard_caps_the_plan_and_is_recorded(world, monkeypatch):  # noqa: F811
    _use(monkeypatch, FakeClassifier(override={"route": _answer("conflicts", 0.95, ("search", "view"))}))
    result = _plan("Two limits appear in the old policy. Which one should I follow?")
    assert result["route"]["id"] == "conflicts"
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "guard"
    assert result["confidence"]["score"] == planner.GUARD_CAP
    assert [g["id"] for g in result["confidence"]["guards"]] == ["conflicts.version"]
    assert result["steps"] == [] and result["suggested_steps"]


def test_no_guard_leaves_the_confidence_block_with_an_empty_list(world):  # noqa: F811
    assert _plan("How often did the team meet?")["confidence"]["guards"] == []


# ── follow-ups use the previous turn ──────────────────────────────────────────────────────

def test_a_long_affirmative_with_an_action_points_back():
    for q in ("Yes - do run a broader sweep as I have dealt with quite a few more than above",
              "yeah, go ahead and run the wider search please",
              "Ok please proceed with the second option"):
        assert intents.refers_to_previous(q), q
    assert not intents.refers_to_previous("Yes is the answer to which question in the survey of the old forms")
    assert not intents.refers_to_previous("Do the two manuals contradict each other regarding the leave limits?")


def test_a_long_affirmative_inherits_the_previous_offer():
    prev = intents.parse_previous({"question": "List my clients", "offer": "Shall I run a broader sweep?"})
    result = intents.decide_intent("Yes - do run a broader sweep as I have dealt with quite a few more than above", prev)
    assert result.source == "previous_offer" and result.previous_used


def test_a_forced_route_counts_as_previous_used(world):  # noqa: F811
    prev = intents.parse_previous({"question": "Where did Ada go?", "route": "search", "intent": "retrieve"})
    result = _plan("Why did you miss Lakeport?", previous=prev)
    assert result["intent"]["id"] == "follow_up_why" and result["previous_used"] is True
