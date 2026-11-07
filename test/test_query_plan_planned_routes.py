"""Labels may name a planned route (designed, not yet in routes.yaml); replay counts them as `planned`.
Plans are synthetic dicts; label text is synthetic."""

import pytest

from artmind.query_plan import replay
from artmind.query_plan import routes as routes_mod
from artmind.query_plan.labels import PLANNED_ROUTES, Label, LabelError, parse_labels


def _label(**over):
    base = dict(id="L001", question="q", source="written", domains=("journal",), route="episode",
                view=None, params={})
    return Label(**{**base, **over})


def _plan(intent="retrieve", route="search", level="high"):
    return {"domains": {"selected": ["journal"]}, "route": {"id": route}, "view_match": None,
            "confidence": {"level": level}, "intent": {"id": intent},
            "classifier": {"latency_ms": 1.0, "fell_back": False}}


def test_planned_routes_are_not_built_routes():
    # When a planned route lands in routes.yaml, remove it from PLANNED_ROUTES.
    assert not set(PLANNED_ROUTES) & {r.id for r in routes_mod.load_routes()}


def test_validation_accepts_a_planned_route(tmp_path):
    data = {"version": 1, "labels": [
        {"id": "L001", "question": "when did PERSON_A go into HOSPITAL_B and for how long", "source": "written",
         "domains": ["journal"], "route": "episode", "expect": "low"}]}
    (label,) = parse_labels(data, tmp_path, view_params={})
    assert label.planned
    with pytest.raises(LabelError, match="unknown route 'not_a_route'"):
        parse_labels({**data, "labels": [{**data["labels"][0], "route": "not_a_route"}]}, tmp_path, view_params={})


def test_planned_label_is_recorded_not_graded():
    for level in ("high", "low"):
        result = replay.score(_label(), _plan(level=level))
        assert result["outcome"] == "planned" and "route" not in result["mismatches"]
        assert (result["route"], result["expected_route"]) == ("search", "episode")


def test_planned_label_planned_as_nonretrieval_is_wrong():
    result = replay.score(_label(), _plan(intent="curate"))
    assert result["outcome"] == "wrong" and "intent" in result["mismatches"]


def test_planned_labels_stay_out_of_the_wrong_rate():
    good = {"outcome": "correct", "mismatches": [], "expected_route": "search", "latency_ms": 5.0}
    bad = {**good, "id": "W1", "outcome": "wrong", "mismatches": ["route"]}
    planned = {"id": "P1", "outcome": "planned", "mismatches": [], "expected_route": "episode",
               "route": "search", "level": "high", "latency_ms": 5.0}
    rows = [*[{**good, "id": f"G{i}"} for i in range(9)], bad, *[{**planned, "id": f"P{i}"} for i in range(30)]]
    report = replay.summarise(rows, [], "lexical", 4)
    assert report["outcomes"]["planned"] == 30 and len(report["planned"]) == 30
    assert report["gate"]["wrong_rate"] == pytest.approx(0.1)
    assert report["per_route"]["episode"]["planned"] == 30
