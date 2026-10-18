from artmind.insights.model import Finding
from artmind.insights.prompts import (
    PROMPT_VERSION, allowed_numbers, check_circle_name, check_narration, compact_stats, finding_prompt, circle_prompt,
)


def finding(detector="dormancy", stats=None, subjects=None):
    return Finding(key="k", detector=detector, kind="corpus", score=0.8, title="Quiet: Asha",
                   subjects=subjects if subjects is not None else [{"_id": "p1", "name": "Asha Bose", "class": "PERSON"}],
                   stats=stats if stats is not None else {"last_seen": "2025-03-02", "cadence_days": 18.0,
                                                          "silence_days": 578, "support": 30})


KNOWN = {"asha bose", "ben okoro", "acme", "home"}


def test_allowed_numbers_include_rounded_forms_and_date_parts():
    nums = allowed_numbers({"share": 0.4567, "silence_days": 578, "last_seen": "2025-03-02",
                            "rates": {"2023": 12.25}, "list": [3, 4]})
    for n in ("0.46", "0.5", "46", "45.7", "578", "2025", "2023", "12.2", "12", "3", "4"):
        assert n in nums, n


def test_good_narration_passes():
    ok, reason = check_narration("Asha Bose has not appeared for 578 days, against a usual gap of 18 days.",
                                 finding(), KNOWN)
    assert ok, reason


def test_unknown_entity_number_and_causal_wording_are_rejected():
    assert check_narration("Asha Bose went quiet after meeting Ben Okoro.", finding(), KNOWN) == (False, "names Ben Okoro")
    assert check_narration("Asha Bose has been quiet for 600 days.", finding(), KNOWN)[1] == "number 600 not in stats"
    lift = finding("outcome_lift", stats={"negative_share": 0.7, "baseline_negative_share": 0.4, "z": 3.1})
    assert check_narration("Days with Asha Bose cause worse moods.", lift, KNOWN)[1] == "causal wording"
    assert check_narration("", finding(), KNOWN) == (False, "empty")
    assert check_narration("word " * 45, finding(), KNOWN) == (False, "too long")


def test_sentence_start_capital_is_not_an_entity():
    ok, _ = check_narration("Home is where Asha Bose spends 30 days.", finding(subjects=[
        {"_id": "p1", "name": "Asha Bose", "class": "PERSON"}, {"_id": "l1", "name": "Home", "class": "LOCATION"}]), KNOWN)
    assert ok


def test_circle_names():
    assert check_circle_name("Work team") == (True, "")
    assert check_circle_name("The extended family of the diarist")[0] is False
    assert check_circle_name("Team 2")[0] is False
    assert check_circle_name("")[0] is False


def test_compact_stats_truncates():
    s = compact_stats({"members": list(range(50)), "rates": {str(i): i for i in range(30)}, "x": 1})
    assert len(s["members"]) == 5 and len(s["rates"]) == 12 and s["x"] == 1


def test_prompts_carry_version_and_items():
    p = finding_prompt([finding()])
    assert '"key": "k"' in p and "JSON" in p
    c = circle_prompt([{"id": "PERSON:a", "members": [{"name": "A"}], "shared": ["Acme"]}])
    assert "PERSON:a" in c
    assert isinstance(PROMPT_VERSION, int)


def test_words_from_the_subjects_own_name_are_allowed():
    plan = finding("open_loops", stats={"age_days": 271},
                   subjects=[{"_id": "p", "name": "Summer Vacation Trip", "class": "PLAN"}])
    known = {"vacation", "summer vacation trip", "ben okoro"}
    assert check_narration("Summer Vacation Trip has been open for 271 days.", plan, known)[0]
    assert check_narration("The Vacation plan is still open after 271 days.", plan, known)[0]
    assert check_narration("Vacation with Ben Okoro is open.", plan, known) == (False, "names Ben Okoro")


def test_title_numbers_and_list_lengths_are_allowed():
    broker = Finding(key="b", detector="brokers", kind="corpus", score=0.9, title="Connects 4 circles: Asha Bose",
                     subjects=[{"_id": "p1", "name": "Asha Bose", "class": "PERSON"}],
                     stats={"betweenness": 0.53, "circles": ["a", "b", "c"]})
    assert check_narration("Asha Bose connects 4 circles.", broker, KNOWN)[0]
    assert check_narration("Asha Bose links 3 circles.", broker, KNOWN)[0]
    assert check_narration("Asha Bose links 9 circles.", broker, KNOWN)[1] == "number 9 not in stats"
