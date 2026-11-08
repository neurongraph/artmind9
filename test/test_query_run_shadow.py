"""Shadow dry execution (P3 spec §7.2): run the shadow plan's program, record ids and counts only."""

import pytest

from artmind.query_plan import shadow
from artmind.query_run.capture import StepResult

PLAN = {"program": [{"id": "s1", "kind": "run", "run": "artmind query vector-text --domain b q --compact", "filled": True},
                    {"id": "s2", "kind": "run", "run": "artmind query graph text2cypher --domain b q --compact", "filled": True}],
        "guards": [], "route": {"id": "search"}, "replacements": {}, "confidence": {"level": "low"},
        "classifier": {"backend": "lexical"}}
PAYLOAD = {"command": "query vector-text", "rows": [
    {"passage_id": "p1", "chunk_id": "c1", "text": "private words", "relevance": 0.6, "doc": {"id": "d1", "title": "T"}}]}


def _step(argv, sid):
    return StepResult(sid, list(argv), 0, None, PAYLOAD, 5.0, 100)


@pytest.mark.parametrize("value, expected", [(None, "0"), ("0", "0"), ("1", "1"), ("all", "all"), ("yes", "1")])
def test_the_flag(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ARTMIND_PLAN_SHADOW_RUN", raising=False)
    else:
        monkeypatch.setenv("ARTMIND_PLAN_SHADOW_RUN", value)
    assert shadow.run_mode() == expected


def test_a_dry_run_records_ids_and_counts_only():
    event = shadow.dry_run(PLAN, mode="1", step_fn=_step)
    assert event["status"] == "ok"
    assert [p["id"] for p in event["path"]] == ["s1", "s2", "open"]  # spec 6.5: the open step is skipped (no domain in PLAN)
    assert next(p for p in event["path"] if p["id"] == "s2")["skipped"] == "llm_shadow"
    assert event["documents"] == ["d1"] and event["timing"]["exec_ms"] >= 0
    assert event["bundle_chars"] > 0 and event["escalate"] is None
    assert "private words" not in str(event) and "q --compact" not in str(event)


def test_mode_all_runs_llm_steps_too():
    event = shadow.dry_run(PLAN, mode="all", step_fn=_step)
    assert next(p for p in event["path"] if p["id"] == "s2").get("skipped") is None


def test_a_failing_dry_run_is_an_error_event():
    def boom(argv, sid):
        raise RuntimeError("down")

    event = shadow.dry_run(PLAN, mode="1", step_fn=boom)
    assert event["status"] == "error" and event["error"] == "RuntimeError"


def test_covers_agent():
    assert shadow.covers_agent(["d1", "d2"], [{"evidence": {"documents": [{"doc_id": "d1"}, {"doc_id": "d3"}]}}]) == 0.5
    assert shadow.covers_agent(["d1"], []) is None
