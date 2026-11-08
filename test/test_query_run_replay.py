"""`plan replay --run`: latency and bundle reach of executed plans (P3 spec §9.1). Stub plans and steps."""

import json

from artmind.query_plan.labels import Label
from artmind.query_run import replay_run
from artmind.query_run.capture import StepResult

NEEDLE = "artmind query vector-text --shape brief --domain bank --topK 10 fees --compact"


def _label(lid="L1", expected=("policies/fees.md", "Rates.md")):
    return Label(id=lid, question="invented question", source="written", domains=("bank",), route="search",
                 view=None, params={}, expected_documents=tuple(expected))


def _plan(*a, **k):
    return {"program": [{"id": "needle", "kind": "run", "run": NEEDLE, "filled": True}], "guards": [],
            "route": {"id": "search"}, "replacements": {}, "suggested_steps": [], "fallbacks": [],
            "steps": [{"cmd": NEEDLE}], "domains": {"selected": ["bank"]}, "confidence": {"level": "low"},
            "classifier": {"latency_ms": 1.0, "fell_back": False}}


def _step(argv, step_id):
    rows = [{"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "relevance": 0.5,
             "doc": {"id": f"d{i}", "title": t}} for i, t in enumerate(("fees", "other"))]
    return StepResult(step_id, list(argv), 0, None, {"command": "query vector-text", "rows": rows}, 5.0, 100)


def test_per_label_counts_and_totals():
    ticks = iter(range(0, 10_000, 100))
    report = replay_run.run_labels([_label(), _label("L2", expected=())], "lexical", plan_fn=_plan, step_fn=_step,
                                   clock=lambda: next(ticks) / 1000)
    (row,) = report["labels"]
    assert row["id"] == "L1" and row["expected"] == 2 and row["steps_reached"] == 1 and row["bundle_reached"] == 1
    assert row["executed"] == 2 and row["plan_ms"] is not None   # the program's step plus the open step and row["escalate"] is None
    assert report["totals"]["labels"] == 1 and report["totals"]["bundle_reached"] == 1
    assert "invented question" not in json.dumps(replay_run.public_report(report))


def test_p90():
    assert replay_run.p90([]) is None
    assert replay_run.p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 9
