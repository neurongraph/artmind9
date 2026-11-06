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


def test_a_nonretrieval_label_is_wrong_only_for_a_confident_lookup():
    # No lookup planned: right whatever the route, and whichever non-retrieval intent was chosen.
    ok = replay.score(_nr_label(route="view"), _plan(route="search"))
    assert (ok["outcome"], ok["mismatches"], ok["nonretrieval"], ok["intent_ok"]) == ("correct", [], True, True)
    other = replay.score(_nr_label(), _plan(intent="meta"))
    assert (other["outcome"], other["mismatches"], other["intent_ok"]) == ("correct", [], False)
    # A confident lookup for a turn that needs none is the harm the gate counts.
    bad = replay.score(_nr_label(), _plan(intent="retrieve"))
    assert (bad["outcome"], bad["mismatches"]) == ("wrong", ["lookup"])


def test_nonretrieval_confidence_rules():
    assert replay.score(_nr_label(), _plan(level="low"))["outcome"] == "abstained"
    assert replay.score(_nr_label(expect="low"), _plan(level="low"))["outcome"] == "correct_abstain"
    # A low-confidence lookup is harmless: the agent decides.
    assert replay.score(_nr_label(), _plan(intent="retrieve", level="low"))["outcome"] == "abstained"
    assert replay.score(_nr_label(expect="low"), _plan(intent="retrieve", level="low"))["outcome"] == "correct_abstain"
    # A confident no-lookup plan is never overconfident, whatever the label expects.
    assert replay.score(_nr_label(expect="low"), _plan())["outcome"] == "correct"
    assert replay.score(_nr_label(expect="low"), _plan(intent="retrieve"))["outcome"] == "wrong"


def test_retrieval_label_planned_as_curate_is_wrong():
    result = replay.score(_label(), _plan(intent="curate"))
    assert result["outcome"] == "wrong" and "intent" in result["mismatches"]
    assert replay.score(_label(intent="retrieve"), _plan(intent="retrieve"))["outcome"] == "correct"


def test_gate_needs_no_confident_lookup_for_a_nonretrieval_label():
    good = {"outcome": "correct", "mismatches": [], "expected_route": "search", "latency_ms": 5.0}
    ok = {"id": "L2", "outcome": "correct", "mismatches": [], "expected_route": "intent:curate",
          "latency_ms": 5.0, "nonretrieval": True, "intent_ok": True}
    other_intent = {**ok, "id": "L3", "intent_ok": False}
    lookup = {**ok, "id": "L4", "outcome": "wrong", "mismatches": ["lookup"], "intent_ok": False}
    many = [{**good, "id": f"G{i}"} for i in range(30)]
    report = replay.summarise([*many, ok, other_intent], [], "lexical", 4)
    assert report["nonretrieval"] == {"labelled": 2, "intent_ok": 1, "no_lookup": 2, "wrong": [],
                                      "intent_wrong": ["L3"]}
    assert report["gate"]["passed"]
    report = replay.summarise([*many, ok, lookup], [], "lexical", 4)
    assert report["nonretrieval"]["wrong"] == ["L4"] and report["gate"]["passed"] is False
