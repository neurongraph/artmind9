"""B4: time questions resolve a versioned document family by its title words
(spec 2026-11-07-b3-b4-b5 section 2). Synthetic names only; fixtures from test_query_plan_planner."""

import pytest

from artmind import graph_query
from artmind.query_plan import candidates, routes, slots
from artmind.query_plan.catalogue import DocRecord
from test_query_plan_planner import _cat, _plan, catalogue_world, world  # noqa: F401  (fixtures)

NAMES = [
    "policy_complaints.md", "policy_complaints_v3.md", "policy_complaints_v2.md",
    "interest_rate_schedule_2026.md", "interest_rate_schedule_2026_02.md",
    "sop_account_opening.md", "sop_account_opening_v2.md",
    "sop_account_closure.md", "sop_account_closure_v2.md",
    "policy_alpha.md",
]


def test_the_stem_strips_extension_version_and_date_suffixes():
    assert candidates.family_stem("policy_complaints_v3.md")[0] == "policy complaints"
    assert candidates.family_stem("interest_rate_schedule_2026_02.md")[0] == "interest rate schedule"
    assert candidates.family_stem("interest_rate_schedule_2026.md")[0] == "interest rate schedule"
    assert candidates.family_stem("policy_alpha.md") == ("policy alpha", ())


def test_a_family_needs_two_members():
    assert candidates.topic_families("what is in policy alpha", NAMES) == []


def test_members_come_newest_first_by_suffix_without_records():
    (family,) = candidates.topic_families("complaints policy", NAMES)
    assert family == ["policy_complaints_v3.md", "policy_complaints_v2.md", "policy_complaints.md"]
    (schedule,) = candidates.topic_families("the interest rate schedule", NAMES)
    assert schedule == ["interest_rate_schedule_2026_02.md", "interest_rate_schedule_2026.md"]


def test_valid_from_orders_members_when_the_catalogue_gives_it():
    records = [DocRecord("1", "policy_complaints.md", "d", "2026-06-01"),
               DocRecord("2", "policy_complaints_v3.md", "d", "2026-01-01"),
               DocRecord("3", "policy_complaints_v2.md", "d", "2026-03-01")]
    (family,) = candidates.topic_families("complaints policy", NAMES, records)
    assert family == ["policy_complaints.md", "policy_complaints_v2.md", "policy_complaints_v3.md"]


def test_opening_and_closure_are_different_families():
    # the shared words "account" and "sop" never merge the two: each is its own family, the named one first
    opening = candidates.topic_families("account opening sop", NAMES)
    assert opening[0] == ["sop_account_opening_v2.md", "sop_account_opening.md"]
    assert ["sop_account_opening.md" in f for f in opening].count(True) == 1
    closure = candidates.topic_families("account closure sop", NAMES)
    assert closure[0] == ["sop_account_closure_v2.md", "sop_account_closure.md"]


def test_score_is_the_share_of_stem_words_named_and_generic_words_count_half():
    # "policy" alone is half a weight of 0.5 + 1 (complaints) = 0.25 / 1.5: below the bar
    assert candidates.topic_families("which policy applies", NAMES) == []
    # "complaint" (stemmed like complaints) is 1 / 1.5
    assert candidates.topic_families("a complaint came in", NAMES) != []
    # a generic word beside one subject word: only the subject word is needed
    assert candidates.topic_families("who may approve rate changes", NAMES) == []  # "rate" is 1 of 2.5
    assert candidates.topic_families("rate interest", NAMES) != []  # 2 of 2.5


def test_the_best_two_families_are_kept_best_first():
    got = candidates.topic_families("account opening and account closure sop, complaints policy", NAMES)
    assert len(got) == candidates.TOPIC_FAMILIES_MAX
    assert got[0][0].startswith(("sop_account_", "policy_complaints"))


def test_widening_reads_supersedes_once_per_picked_family(monkeypatch):
    calls = []

    def fake(domains, document, pool=()):
        calls.append(document)
        return [document, "policy_complaints_v1.md"]

    monkeypatch.setattr(candidates, "family_documents", fake)
    got = candidates.topic_documents(["d"], [["a_v2.md", "a.md"], ["b_v2.md", "b.md"]])
    assert calls == ["a_v2.md", "b_v2.md"]
    assert got == ["a_v2.md", "a.md", "policy_complaints_v1.md", "b_v2.md", "b.md"]
    many = [[f"x{i}.md" for i in range(5)], [f"y{i}.md" for i in range(5)]]
    assert len(candidates.topic_documents([], many)) == candidates.TOPIC_MEMBERS_MAX


def test_the_time_route_chooses_topic_history_only_without_a_document():
    route = next(r for r in routes.load_routes() if r.id == "time_history")
    assert [c.kind for c in route.programs] == ["topic_history", "history"]
    assert route.programs[0].when.text == "not document and topic_family >= 1"


def test_the_slot_quality_factor_applies_only_with_topic_documents():
    clean, _ = slots.slot_quality({})
    topic, info = slots.slot_quality({"topic_documents": slots.Slot("topic_documents", ["a"], "topic", 0.8)})
    assert clean == 1.0 and topic == slots.TOPIC_DOCUMENTS == 0.8
    assert info["factors"]["topic"] == 0.8 and info["weakest"] == "topic"


def _topic_world(world, monkeypatch, names):
    world["views"] = []
    monkeypatch.setattr(candidates, "document_names", lambda domains: names)
    monkeypatch.setattr(graph_query, "_run_read_query", lambda cypher, params: [])


def test_a_time_question_naming_a_family_plans_topic_history(world, monkeypatch):
    _topic_world(world, monkeypatch, NAMES)
    result = _plan("As of 15 May 2026, which complaints policy applied?")
    assert result["route"]["id"] == "time_history" and result["program_kind"] == "topic_history"
    assert result["plan_facts"] == {"document": False, "topic_family": 1}
    cmds = [s["cmd"] for s in result["steps"] + result["suggested_steps"]]
    assert any("--documentName policy_complaints_v3.md --includeHistory" in c for c in cmds)
    assert any("--documentName policy_complaints.md --includeHistory" in c for c in cmds)
    assert any(c.startswith("artmind query vector-text") for c in cmds)
    slot = result["slots"]["topic_documents"]
    assert slot["source"] == "topic" and slot["quality"] == 0.8
    assert result["slot_quality"]["factors"]["topic"] == 0.8
    assert "unfilled" not in str(result.get("warnings"))


def test_a_time_question_with_no_family_keeps_todays_plan(world, monkeypatch):
    _topic_world(world, monkeypatch, ["policy_alpha.md", "policy_beta.md"])
    result = _plan("As of 15 May 2026, which policy applied?")
    assert "topic_documents" not in result["slots"]
    assert result["program_kind"] != "topic_history"
    assert result["plan_facts"].get("topic_family", 0) == 0


def test_a_named_document_still_plans_the_history_program(world, monkeypatch):
    _topic_world(world, monkeypatch, NAMES)
    result = _plan("Which version of policy_complaints.md was in force on 15 May 2026?")
    assert result["program_kind"] != "topic_history"
    assert "topic_documents" not in result["slots"]
