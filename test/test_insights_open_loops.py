from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.open_loops import OPEN_LOOPS

CFG = {"open_loops": {"class": "PLAN", "open": ["firm intention", "tentative"], "closed": ["done"]}}


def ctx_for(config=CFG, props=None):
    ents = [ent("p1", "PLAN", ["2024-01-10"], name="Fix the roof"),
            ent("p2", "PLAN", ["2024-06-20"], name="Call the bank"),
            ent("p3", "PLAN", ["2024-01-05"], name="Paint the fence"),
            ent("p4", "PLAN", ["2024-02-01"], name="Learn guitar"),
            ent("x", "PERSON", ["2024-07-01"])]
    props = props if props is not None else {
        ("PLAN", "status"): {"p1": "Firm intention", "p2": "tentative", "p3": "done", "p4": "maybe later"},
        ("PLAN", "urgency"): {"p1": "High"},
    }
    return make_ctx(ents, config=config, entity_props=props, as_of="2024-07-01")


def test_stale_open_plans_and_unclassified_statuses():
    ctx = ctx_for()
    assert OPEN_LOOPS.plan(ctx) == ["PLAN"]
    found = {f.key: f for f in OPEN_LOOPS.run(ctx, "PLAN")}
    assert set(found) == {"open_loops:PLAN:p1", "open_loops:PLAN:unclassified"}
    f = found["open_loops:PLAN:p1"]
    assert f.title == "Still open: Fix the roof" and f.stats["age_days"] == 173 and f.stats["urgency"] == "high"
    u = found["open_loops:PLAN:unclassified"]
    assert u.kind == "health" and u.stats["top"] == {"maybe later": 1} and u.action == "config"


def test_urgency_weights_the_score():
    props = {("PLAN", "status"): {"p1": "firm intention", "p4": "firm intention"}}
    found = {f.key: f for f in OPEN_LOOPS.run(ctx_for(props=props), "PLAN")}
    high = {("PLAN", "status"): props[("PLAN", "status")], ("PLAN", "urgency"): {"p1": "urgent"}}
    boosted = {f.key: f for f in OPEN_LOOPS.run(ctx_for(props=high), "PLAN")}
    assert boosted["open_loops:PLAN:p1"].score > found["open_loops:PLAN:p1"].score


def test_plan_skips():
    assert OPEN_LOOPS.plan(ctx_for(config={})).reason == "not configured"
    other = {"open_loops": {"class": "TASK", "open": ["open"]}}
    assert isinstance(OPEN_LOOPS.plan(ctx_for(config=other)), Skip)
