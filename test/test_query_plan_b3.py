"""B3: instruction cues, unsure non-retrieval intents, the decision model's margin (invented text only)."""

import pytest

from artmind.query_plan import config as plan_config, intents, planner
from artmind.query_plan.config import PlanConfigError, parse_config
from test_query_plan_planner import FakeClassifier, _answer, _plan, _use, world  # noqa: F401

LOOKUP = "How often did the team meet?"


def _intent(choice, p):
    return {"intent": _answer(choice, p, intents.INTENT_IDS)}


# ── B3a ───────────────────────────────────────────────────────────────────────────────────

def test_instructions_are_parsed_and_validated():
    assert "into a file" in intents.load_intents().instructions
    data = {"version": 1, "intents": [{"id": i, "description": "d"} for i in intents.INTENT_IDS],
            "affirmatives": ["yes"], "instructions": "nope"}
    with pytest.raises(intents.IntentError, match="instructions"):
        intents.parse_intents(data)


def test_instruction_cue_caps_a_lookup_and_lists_the_cues(world):
    result = _plan("How often did the team meet? Write the table into a file.")
    assert result["intent"]["id"] == "retrieve"
    assert result["intent"]["instruction"] == ["into a file", "write the table"]
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "instruction"
    assert result["confidence"]["score"] == planner.INSTRUCTION_CAP
    assert result["steps"] == [] and result["suggested_steps"]  # the route stays, as suggestions
    assert _plan(LOOKUP)["intent"]["instruction"] == []


def test_instruction_cue_on_the_substituted_previous_question(world):
    prev = intents.parse_previous({"question": "How often did the team meet? Save it to a file.", "offer": "x"})
    result = _plan("yes", previous=prev)
    assert result["effective_question"] and result["previous_used"]
    assert "to a file" in result["intent"]["instruction"]
    assert result["confidence"]["level"] == "low"


# ── B3b ───────────────────────────────────────────────────────────────────────────────────

def test_an_unsure_non_retrieval_intent_plans_a_low_lookup(world, monkeypatch):
    _use(monkeypatch, FakeClassifier(override=_intent("update", 0.6)))
    result = _plan(LOOKUP)
    assert result["intent"]["id"] == "retrieve"
    assert result["intent"]["unsure"] == {"id": "update", "p": 0.6}
    assert result["confidence"]["level"] == "low" and result["confidence"]["score"] <= 0.6
    assert result["suggested_steps"] and result["action"] is None


def test_a_confident_non_retrieval_intent_is_unchanged(world, monkeypatch):
    _use(monkeypatch, FakeClassifier(override=_intent("update", 0.95)))
    result = _plan("remember that Ada moved")
    assert result["intent"]["id"] == "update" and result["intent"]["unsure"] is None
    assert result["program"] == [] and result["steps"] == [] and result["action"]
    assert result["confidence"]["level"] == "high"
    assert result["confidence"]["margin"] == planner.DECISION_MARGIN


def test_a_non_retrieval_intent_between_threshold_and_margin_is_low(world, monkeypatch):
    _use(monkeypatch, FakeClassifier(override=_intent("update", 0.72)))
    result = _plan("remember that Ada moved")
    assert result["intent"]["id"] == "update" and result["confidence"]["level"] == "low"


def test_a_lexical_non_retrieval_intent_is_not_called_unsure(world):
    result = _plan("make that a table")
    assert result["intent"]["id"] == "follow_up_format" and result["intent"]["unsure"] is None


# ── B3c ───────────────────────────────────────────────────────────────────────────────────

def test_the_margin_applies_to_an_answering_decision_backend(world, monkeypatch):
    assert _plan(LOOKUP)["confidence"]["margin"] == 0.0  # lexical: bare threshold
    _use(monkeypatch, FakeClassifier())
    assert _plan(LOOKUP)["confidence"]["margin"] == planner.DECISION_MARGIN
    route = _plan(LOOKUP)["route"]["id"]
    for p, level in ((0.72, "low"), (0.76, "high")):
        _use(monkeypatch, FakeClassifier(override={"route": _answer(route, p, ("search", "view"))}))
        assert _plan(LOOKUP)["confidence"]["level"] == level


def test_no_margin_after_a_fallback(world, monkeypatch):
    _use(monkeypatch, FakeClassifier(unavailable=True))
    result = _plan(LOOKUP)
    assert result["classifier"]["fell_back"] is True
    assert result["confidence"]["margin"] == 0.0


def test_the_margin_comes_from_the_domain_config(world, monkeypatch, tmp_path):
    (tmp_path / "journal.yaml").write_text("decision_margin: 0.2\n")
    route = _plan(LOOKUP)["route"]["id"]
    _use(monkeypatch, FakeClassifier(override={"route": _answer(route, 0.8, ("search", "view"))}))
    result = _plan(LOOKUP)
    assert result["confidence"]["margin"] == 0.2 and result["confidence"]["level"] == "low"


def test_decision_margin_config_is_validated_and_merged():
    assert parse_config({"decision_margin": 0}, "t").decision_margin == 0.0
    for bad in (1, -0.1, "x", True):
        with pytest.raises(PlanConfigError, match="decision_margin"):
            parse_config({"decision_margin": bad}, "t")
    a, b, c = (parse_config(d, "t") for d in ({"decision_margin": 0.1}, {"decision_margin": 0.3}, {}))
    assert plan_config.merge([a, b, c]).decision_margin == 0.3
    assert plan_config.merge([c, c]).decision_margin is None
