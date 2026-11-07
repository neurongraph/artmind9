"""The v2 plan payload: intents, previous turn, slots, slot quality, scope, program (spec 2026-11-05).
Synthetic data only; fixtures come from test_query_plan_planner."""

import json
import sys
from datetime import date

import pytest
from click.testing import CliRunner

from _envelope import plain
from artmind.cli import cli
from artmind.query_plan import intents, planner
from test_query_plan_planner import (  # noqa: F401  (fixtures and helpers)
    ENTITIES, FakeClassifier, PAYLOAD_KEYS, V2_KEYS, _answer, _person, _plan, _use, world,
)

QUESTION = "How many visits did Ada make to Lakeport in 2025?"


def _prev(**kw):
    return intents.parse_previous(kw)


# ── payload shape ─────────────────────────────────────────────────────────────────────────

def test_a_retrieve_plan_carries_every_v1_key_and_the_v2_blocks(world):
    result = _plan(QUESTION)
    assert set(result) == PAYLOAD_KEYS | V2_KEYS and result["plan_version"] == 2
    assert result["intent"] == {"id": "retrieve", "confidence": result["intent"]["confidence"],
                                "source": "classifier", "inherited_offer": None, "unsure": None, "instruction": [],
                                "alternatives": result["intent"]["alternatives"]}
    assert result["effective_question"] is None and result["previous_used"] is False
    assert result["action"] is None and result["answer_from"] is None and result["asset_match"] is None
    assert set(result["slots"]) >= {"search_term", "scope", "date_window"}
    assert result["slots"]["search_term"]["source"] == "mention"
    assert result["slots"]["date_window"]["value"] == {"from": "2025-01-01", "to": "2026-01-01", "event": None}
    assert result["scope"] == {"domains": ["journal"], "exclude_documents": [], "rules": [],
                               "filters": {"exclude_classes": [], "participants": [], "require_participant": None}}
    assert set(result["slot_quality"]) == {"score", "factors", "weakest"} and result["slot_quality"]["score"] == 1.0
    json.dumps(result, default=str)  # every block serialises


def test_a_view_plan_has_one_run_node_with_the_view_command(world):
    result = _plan(QUESTION)
    assert result["route"]["id"] == "view"
    assert [n["kind"] for n in result["program"]] == ["par", "pick"]  # a count question: view + text check
    assert result["program_kind"] == "view_count"
    assert result["program"][0]["children"][0]["run"] == result["steps"][0]["cmd"]
    assert result["guards"] == []


def test_a_route_plan_serialises_its_program_and_guards(world):
    world["views"] = []
    result = _plan("How often did the team meet?")
    assert [n["run"] for n in result["program"]] == [s["cmd"] for s in result["steps"]]
    assert result["program"][0]["id"] == "s1" and result["program"][0]["filled"] is True


def test_v1_templates_still_render_with_keywords_not_search_term(world):
    world["views"] = []
    result = _plan("How often did the team meet?")
    assert result["steps"][0]["cmd"] == (
        "artmind query graph text2cypher --domain journal 'How often did the team meet?' --compact")


# ── non-retrieve intents ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question, intent, action", [
    ("which model are you", "meta", None),
    ("make that a table", "follow_up_format", None),
    ("Ada and Lakeport are the same", "curate", {"skill": "artmind-curate", "draft": None}),
    ("remember that Ada moved", "update", {"skill": "artmind-update", "draft": None}),
    ("ingest the new notes folder", "ops", {"skill": "artmind-ingestion-helper", "draft": None}),
    ("is it done", "status", {"skill": None, "draft": "artmind ingest jobs --compact"}),
])
def test_a_non_retrieval_intent_plans_no_steps(world, question, intent, action):
    result = _plan(question)
    assert result["intent"]["id"] == intent
    assert result["program"] == [] and result["steps"] == [] and result["suggested_steps"] == []
    assert result["action"] == action
    assert result["answer_from"] == ("previous" if intent == "follow_up_format" else None)
    assert result["confidence"]["level"] in ("high", "low") and result["confidence"]["weakest"] == "intent"
    assert result["entities"] == [] and result["slot_quality"] is None
    assert result["route"]["id"]  # the route decision is still recorded


def test_status_with_an_open_job_drafts_job_status(world):
    result = _plan("is it done", previous=_prev(open_job="job 7"))
    assert result["intent"]["source"] == "status_open_job"
    assert result["action"]["draft"] == "artmind ingest job-status 'job 7' --compact"
    assert result["confidence"]["level"] == "high"


def test_execute_is_ignored_for_a_non_retrieval_intent(world):
    result = _plan("which model are you", execute=True)
    assert any("--execute ignored: intent meta" in w for w in result["warnings"])


# ── the previous turn ─────────────────────────────────────────────────────────────────────

def test_a_context_only_reply_with_an_offer_plans_the_previous_question(world):
    previous = _prev(intent="retrieve", route="view", question=QUESTION, offer="run a full survey")
    result = _plan("yes please", previous=previous)
    assert result["question"] == "yes please" and result["effective_question"] == QUESTION
    assert result["previous_used"] is True
    assert result["intent"]["id"] == "retrieve" and result["intent"]["source"] == "previous_offer"
    assert result["intent"]["inherited_offer"] == "run a full survey"
    assert result["route"]["id"] == "view" and result["steps"]


def test_a_context_only_reply_inherits_the_previous_intent_without_an_offer(world):
    result = _plan("yes", previous=_prev(intent="curate", question="Ada and Lakeport are the same"))
    assert result["intent"] == {**result["intent"], "id": "curate", "source": "previous_intent"}
    assert result["action"]["skill"] == "artmind-curate"


def test_follow_up_why_takes_the_previous_route_and_must_find(world):
    world["views"] = []
    result = _plan("why did you miss Lakeport", previous=_prev(route="existence", question="q"))
    assert result["intent"]["id"] == "follow_up_why"
    assert result["route"]["id"] == "existence" and result["route"]["confidence"] == 1.0
    assert result["slots"]["must_find"]["value"] == ["Lakeport"]
    assert result["slot_quality"]["score"] == 1.0


def test_follow_up_why_without_a_previous_route_uses_the_classified_one(world):
    world["views"] = []
    result = _plan("why did you miss Lakeport")
    assert result["intent"]["id"] == "follow_up_why"
    assert result["route"]["id"] != "" and "must_find" in result["slots"]


def test_a_pronoun_follow_up_takes_the_previous_subjects(world):
    world["views"] = []
    previous = _prev(entities=[{"id": "p1", "name": "Ada"}])
    result = _plan("what did she say next", previous=previous)
    via = [e["via"] for e in result["entities"]]
    assert via == ["previous"] and result["entities"][0]["candidates"][0]["_id"] == "p1"
    assert result["slots"]["previous_subjects"]["value"] == [{"text": "Ada", "kind": "entity"}]


# ── slots ─────────────────────────────────────────────────────────────────────────────────

def test_the_narrator_slot_carries_the_resolved_id(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("narrator: {entity_class: PERSON, name: Diarist}\n")
    result = _plan("How many visits did I make to Lakeport in 2025?")
    assert result["slots"]["narrator"]["value"] == {"entity_class": "PERSON", "name": "Diarist", "id": "n1"}
    assert result["slots"]["narrator"]["source"] == "narrator"


def test_a_narrator_stub_is_an_identity_gap(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("narrator: {entity_class: PERSON, name: Diarist}\n")
    world["entities"]["Diarist"] = [
        {"_id": "s9", "name": "Diarist", "entity_class": "PERSON", "matched_by": ["exact"]},
        {"_id": "n1", "name": "Diarist", "entity_class": "PERSON", "matched_by": ["narrator", "exact"]}]
    result = _plan("Tell me about Diarist and Lakeport")
    assert result["slots"]["narrator"]["source"] == "alias" and result["slots"]["narrator"]["value"]["id"] == "n1"
    (gap,) = [g for g in result["gaps"] if g.get("why") == "narrator_stub"]
    assert gap["kind"] == "identity" and "s9" in {e["_id"] for e in gap["entities"]}


def test_a_category_word_expands_to_classes_and_terms(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("categories:\n  place: [LOCATION, harbour]\n")
    world["views"] = []
    result = _plan("Which places did Ada visit?")
    category = result["slots"]["category"]
    assert category["value"]["classes"] == ["LOCATION"] and category["value"]["terms"] == ["harbour"]
    assert "harbour" in result["slots"]["search_term"]["value"]
    assert "LOCATION" in result["slots"]["search_term"]["value"]


def test_the_v2_placeholders_render_from_the_context(world, tmp_path):
    from artmind.query_plan import routes

    (tmp_path / "journal.yaml").write_text("categories:\n  place: [LOCATION, harbour]\n")
    world["views"] = []
    captured = {}
    real = planner._route_steps
    world_context = {}

    def spy(route, confidence, context, *args):
        world_context.update(context)
        return real(route, confidence, context, *args)

    planner._route_steps, saved = spy, planner._route_steps
    try:
        _plan("Which places did Ada visit in 2025?")
    finally:
        planner._route_steps = saved
    assert routes.V2_PLACEHOLDERS <= set(world_context), sorted(routes.V2_PLACEHOLDERS - set(world_context))
    assert world_context["date_window"] == "2025-01-01..2026-01-01"
    assert world_context["entity_class_list"] == "LOCATION"
    assert world_context["search_term"]


def test_an_empty_search_term_falls_back_to_the_question_in_the_context(world):
    world["views"] = []
    seen = {}
    real = planner._route_steps

    def spy(route, confidence, context, *args):
        seen.update(context)
        return real(route, confidence, context, *args)

    planner._route_steps, saved = spy, planner._route_steps
    try:
        result = _plan("What was there?")
    finally:
        planner._route_steps = saved
    assert result["slots"]["search_term"]["value"] == "" and seen["search_term"] == "What was there?"
    assert result["slot_quality"]["factors"]["search_term"] == 0.6


# ── twins ─────────────────────────────────────────────────────────────────────────────────

def test_twins_are_a_warning_and_an_identity_gap_and_lower_a_route_plan(world):
    world["views"] = []
    world["entities"]["Ada"] = [_person("p1"), _person("p2")]
    result = _plan("Tell me about Ada")
    twins = result["entities"][0]["twins"]
    assert [t["_id"] for t in twins] == ["p2"] and twins[0]["why"] == "name"
    assert any(w.startswith("twins: 'Ada'") for w in result["warnings"])
    (gap,) = [g for g in result["gaps"] if g["why"] == "twins"]
    assert gap["kind"] == "identity" and [e["_id"] for e in gap["entities"]] == ["p1", "p2"]
    assert result["slot_quality"]["factors"]["twins"] == 0.5
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "slot_quality"


def test_view_twins_run_once_each_and_keep_the_view_high(world):
    world["entities"]["Lakeport"] = [
        {"_id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
        {"_id": "l2", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
    ]
    result = _plan(QUESTION)
    assert result["entities"][1]["twins"] and result["slot_quality"]["factors"]["twins"] == 1.0
    assert result["confidence"]["level"] == "high" and len(result["program"]) == 2


# ── slot quality and confidence ───────────────────────────────────────────────────────────

def test_all_domains_and_an_empty_search_term_make_slot_quality_the_weakest(world, monkeypatch):
    world["views"] = []
    world["overview"].append({"domain": "bank", "document_count": 1, "documents": ["a.md"], "top_classes": ["X"]})
    _use(monkeypatch, FakeClassifier(override={"domain": _answer(planner.ALL_DOMAINS, 0.0, ("journal", "bank")),
                                               "route": _answer("search", 0.9)}))
    result = _plan("What was there?", domains=None)
    assert result["domains"]["source"] == "all"
    assert result["slot_quality"]["score"] == 0.48
    # the v1 domain stage is already 0.0 for "all", so it stays the weakest link; the slot factors still say why
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "domain"
    assert result["slot_quality"]["factors"] == {"mention": 1.0, "twins": 1.0, "search_term": 0.6, "scope": 0.8, "topic": 1.0}
    assert result["slots"]["scope"]["source"] == "all"


def test_an_empty_search_term_alone_lowers_confidence_to_slot_quality(world, monkeypatch):
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer("search", 0.9)}))
    result = _plan("What was there?")
    assert result["confidence"] == {**result["confidence"], "level": "low", "weakest": "slot_quality", "score": 0.6}


def test_a_fuzzy_entity_costs_slot_quality(world):
    world["views"] = []
    world["entities"]["Zed"] = [{"_id": "z1", "name": "Zedd", "entity_class": "PERSON", "matched_by": ["fulltext"]}]
    result = _plan("Tell me about Zed")
    assert result["slot_quality"]["factors"]["mention"] == 0.7
    exact = _plan("Tell me about Ada")
    assert exact["slot_quality"]["factors"]["mention"] == 1.0


# ── scope ─────────────────────────────────────────────────────────────────────────────────

def test_exclude_is_recorded_in_the_scope(world):
    result = _plan(QUESTION, exclude=["doc one", "doc two,doc three", "doc one"])
    assert result["scope"]["exclude_documents"] == ["doc one", "doc two", "doc three"]


# ── CLI ───────────────────────────────────────────────────────────────────────────────────

def _cli(monkeypatch, args, stdin=None):
    seen = {}

    def fake_plan(question, domains, **kw):
        seen.update(question=question, domains=domains, **kw)
        return {"plan_version": 2}

    monkeypatch.setattr(planner, "plan", fake_plan)
    result = CliRunner().invoke(cli, ["query", "plan", "--compact", *args, "q?"], input=stdin)
    return result, seen


def test_previous_from_a_file(monkeypatch, tmp_path):
    path = tmp_path / "prev.json"
    path.write_text(json.dumps({"intent": "retrieve", "question": "earlier", "offer": "go wider"}))
    result, seen = _cli(monkeypatch, ["--previous", str(path), "--exclude", "a,b", "--exclude", "c"])
    assert result.exit_code == 0, result.output
    assert seen["previous"].question == "earlier" and seen["previous"].offer == "go wider"
    assert seen["exclude"] == ["a,b", "c"]


def test_previous_from_stdin(monkeypatch):
    result, seen = _cli(monkeypatch, ["--previous", "-"], stdin=json.dumps({"route": "survey"}))
    assert result.exit_code == 0, result.output
    assert seen["previous"].route == "survey"


@pytest.mark.parametrize("stdin", ["{not json", json.dumps({"intent": "dance"}), json.dumps([1])])
def test_a_bad_previous_is_a_click_error(monkeypatch, stdin):
    result, seen = _cli(monkeypatch, ["--previous", "-"], stdin=stdin)
    assert result.exit_code == 1 and "previous" in result.output and not seen


def test_a_missing_previous_file_is_a_click_error(monkeypatch, tmp_path):
    result, _ = _cli(monkeypatch, ["--previous", str(tmp_path / "nope.json")])
    assert result.exit_code == 1 and "cannot read previous file" in result.output


def test_entry_does_not_proxy_a_plan_with_previous():
    from artmind import _entry

    assert _entry._proxiable(["query", "plan", "q"]) is True
    assert _entry._proxiable(["query", "plan", "--previous", "-", "q"]) is False
    assert _entry._proxiable(["query", "plan", "--previous=p.json", "q"]) is False
    assert _entry._proxiable(["query", "survey", "--previous", "x"]) is True


# ── labels and replay ─────────────────────────────────────────────────────────────────────

def test_labels_take_intent_slots_and_program_kind():
    from pathlib import Path

    from artmind.query_plan import labels

    base = {"id": "L001", "question": "q", "source": "written", "domains": ["journal"], "route": "search"}
    (label,) = labels.parse_labels({"version": 1, "labels": [{**base, "intent": "retrieve",
                                    "slots": {"search_term": "harbour visits"}, "program_kind": "one step"}]},
                                   Path("."), view_params={})
    assert (label.intent, label.slots, label.program_kind) == ("retrieve", {"search_term": "harbour visits"}, "one step")
    (plain_label,) = labels.parse_labels({"version": 1, "labels": [base]}, Path("."), view_params={})
    assert (plain_label.intent, plain_label.slots, plain_label.program_kind) == (None, {}, None)
    for bad, message in [({"intent": "dance"}, "intent must be one of"), ({"slots": {"colour": "red"}}, "slots must be"),
                         ({"slots": ["x"]}, "slots must be"), ({"program_kind": 3}, "program_kind must be")]:
        with pytest.raises(labels.LabelError) as caught:
            labels.parse_labels({"version": 1, "labels": [{**base, **bad}]}, Path("."), view_params={})
        assert any(message in e for e in caught.value.errors)


def _label(**over):
    from artmind.query_plan.labels import Label

    base = dict(id="L001", question="q", source="written", domains=("journal",), route="search", view=None, params={})
    return Label(**{**base, **over})


def _v2_plan(intent="retrieve", **slots):
    return {"domains": {"selected": ["journal"]}, "route": {"id": "search"}, "view_match": None,
            "confidence": {"level": "high"}, "classifier": {"latency_ms": 1.0, "fell_back": False},
            "intent": {"id": intent}, "slots": slots}


def test_replay_grades_intent_and_slots_apart_from_the_route():
    from artmind.query_plan import replay

    plan = _v2_plan("meta", search_term={"value": '"Harbour" visits'}, narrator={"value": {"name": "Diarist"}},
                    date_window={"value": {"from": "2025-01-01", "to": "2026-01-01"}})
    ok = replay.score(_label(intent="meta", slots={"search_term": "visits harbour", "narrator": "diarist",
                                                   "date_window": "2025-01-01..2026-01-01"}), plan)
    assert (ok["outcome"], ok["intent_ok"], ok["slots_ok"], ok["slot_mismatches"]) == ("correct", True, True, [])
    bad = replay.score(_label(intent="retrieve", slots={"search_term": "visits", "narrator": "diarist"}), plan)
    # a retrieval label planned as `meta` planned no retrieval: wrong on intent, whatever the route
    assert bad["outcome"] == "wrong" and "intent" in bad["mismatches"]
    assert (bad["intent_ok"], bad["slots_ok"], bad["slot_mismatches"]) == (False, False, ["search_term"])
    unlabelled = replay.score(_label(), plan)
    assert (unlabelled["intent_ok"], unlabelled["slots_ok"]) == (None, None)
    missing = replay.score(_label(slots={"scope": ["journal"]}), _v2_plan())
    assert missing["slots_ok"] is False and missing["slot_mismatches"] == ["scope"]


def test_replay_summary_counts_each_grade_and_keeps_its_old_keys():
    from artmind.query_plan import replay

    plans = {"L001": _v2_plan("meta"), "L002": _v2_plan("retrieve", search_term={"value": "a b"})}
    labels = [_label(id="L001", intent="meta"), _label(id="L002", intent="update", slots={"search_term": "b a"}),
              _label(id="L003")]

    def fake(question, domains, classifier, today):
        return plans.get(fake.ids.pop(0), _v2_plan())

    fake.ids = ["L001", "L002", "L003"]
    report = replay.replay(labels, "lexical", plan_fn=fake)
    assert report["intent"] == {"labelled": 2, "ok": 1, "wrong": ["L002"]}
    assert report["slots"] == {"labelled": 1, "ok": 1, "wrong": []}
    assert {"outcomes", "per_route", "wrong", "gate", "results"} <= set(report)


def test_the_shadow_trim_drops_the_v2_blocks_before_giving_up():
    from artmind.query_plan import shadow

    plan = {"confidence": {"level": "high"}, "route": {"id": "search"}, "intent": {"id": "retrieve"},
            "steps": [{"n": 1, "cmd": "c"}], "program": [{"run": "x" * 70_000}], "guards": [], "gaps": []}
    trimmed = shadow._trim(plan)
    assert trimmed["program"] == [] and trimmed["trimmed"] is True and trimmed["intent"] == {"id": "retrieve"}
