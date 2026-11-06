"""Replay grading of non-retrieval labels: intent only (spec 2026-11-05 §12). Plans are synthetic dicts."""

from artmind.query_plan import replay
from artmind.query_plan.labels import Label


def _label(**over):
    base = dict(id="L001", question="q", source="written", domains=("journal",), route="search",
                view=None, params={})
    return Label(**{**base, **over})


def _nr_label(**over):
    return _label(**{"intent": "curate", "route": "", "domains": (), **over})


def _plan(intent="curate", route="search", level="high"):
    return {"domains": {"selected": ["journal"]}, "route": {"id": route}, "view_match": None,
            "confidence": {"level": level}, "intent": {"id": intent},
            "classifier": {"latency_ms": 1.0, "fell_back": False}}


def test_nonretrieval_label_is_graded_on_intent_not_route():
    ok = replay.score(_nr_label(route="view"), _plan(route="search"))
    assert (ok["outcome"], ok["mismatches"], ok["nonretrieval"]) == ("correct", [], True)
    bad = replay.score(_nr_label(), _plan(intent="meta"))
    assert (bad["outcome"], bad["mismatches"]) == ("wrong", ["intent"])


def test_nonretrieval_confidence_rules():
    assert replay.score(_nr_label(), _plan(level="low"))["outcome"] == "abstained"
    assert replay.score(_nr_label(expect="low"), _plan(level="low"))["outcome"] == "correct_abstain"
    assert replay.score(_nr_label(), _plan(intent="meta", level="low"))["outcome"] == "wrong"
    assert replay.score(_nr_label(expect="low"), _plan(intent="meta", level="low"))["outcome"] == "wrong"
    high_low = replay.score(_nr_label(expect="low"), _plan())
    assert (high_low["outcome"], high_low["mismatches"]) == ("wrong", ["confidence"])


def test_retrieval_label_planned_as_curate_is_wrong():
    result = replay.score(_label(), _plan(intent="curate"))
    assert result["outcome"] == "wrong" and "intent" in result["mismatches"]
    assert replay.score(_label(intent="retrieve"), _plan(intent="retrieve"))["outcome"] == "correct"


def test_gate_needs_every_nonretrieval_intent_right():
    good = {"outcome": "correct", "mismatches": [], "expected_route": "search", "latency_ms": 5.0}
    ok = {"id": "L2", "outcome": "correct", "mismatches": [], "expected_route": "intent:curate",
          "latency_ms": 5.0, "nonretrieval": True, "intent_ok": True}
    bad = {**ok, "id": "L3", "outcome": "abstained", "intent_ok": False}
    many = [{**good, "id": f"G{i}"} for i in range(20)]
    report = replay.summarise([*many, ok], [], "lexical", 4)
    assert report["nonretrieval"] == {"labelled": 1, "intent_ok": 1, "wrong": []} and report["gate"]["passed"]
    report = replay.summarise([*many, ok, bad], [], "lexical", 4)
    assert report["nonretrieval"]["wrong"] == ["L3"] and report["gate"]["passed"] is False
