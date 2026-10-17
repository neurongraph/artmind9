from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.transitions import TRANSITIONS
from artmind.insights.model import Finding

A_DAYS = tuple(f"2022-{m:02d}-10" for m in range(2, 12))
B_DAYS = tuple(f"2023-{m:02d}-05" for m in range(1, 11)) + tuple(f"2024-{m:02d}-05" for m in range(1, 11))


def rel(rel, _id, name, cls, days, type=None):
    return {"rel": rel, "_id": _id, "name": name, "cls": cls, "type": type, "days": tuple(days)}


def world():
    ents = [ent("me", "PERSON", A_DAYS + B_DAYS, name="Me", type="self"),
            ent("orgA", "ORGANIZATION", A_DAYS, name="Acme"), ent("orgB", "ORGANIZATION", B_DAYS, name="Bolt"),
            ent("act", "ACTIVITY", ["2024-03-03"], name="Pottery")]
    rels = [rel("WORKS_AT", "orgA", "Acme", "ORGANIZATION", A_DAYS),
            rel("WORKS_AT", "orgB", "Bolt", "ORGANIZATION", B_DAYS),
            rel("LEFT", "orgA", "Acme", "ORGANIZATION", ["2022-12-20"]),
            rel("JOINED", "orgB", "Bolt", "ORGANIZATION", ["2023-01-10"]),
            rel("STARTED", "act", "Pottery", "ACTIVITY", ["2024-03-03"])]
    return ents, rels


def ctx_for(**kw):
    ents, rels = world()
    kw.setdefault("anchor_relations", rels)
    return make_ctx(ents, anchors={"me"}, effective={"ACTIVITY": "occurrent"}, **kw)


def test_job_change_is_one_transition_with_two_sources():
    ctx = ctx_for()
    assert TRANSITIONS.plan(ctx) == ["anchor"]
    found = TRANSITIONS.run(ctx, "anchor")
    assert len(found) == 1
    f = found[0]
    assert f.key == "transitions:2022-12-20"
    assert set(f.stats["kinds"]) == {"explicit", "dominance"}
    labels = [e["label"] for e in f.card["events"]]
    assert "left Acme" in labels and "joined Bolt" in labels and "works_at: Acme -> Bolt" in labels
    assert {s["_id"] for s in f.subjects} == {"orgA", "orgB"}
    assert f.score >= 0.6


def test_occurrent_targets_are_not_transitions():
    keys = [e["label"] for f in TRANSITIONS.run(ctx_for(), "anchor") for e in f.card["events"]]
    assert not any("pottery" in k.lower() for k in keys)


def test_circle_shift_from_share_findings():
    ctx = ctx_for(anchor_relations=[])
    ctx.anchor_relations = [rel("WORKS_AT", "orgA", "Acme", "ORGANIZATION", ["2021-05-05"])]
    share = Finding(key="communities:PERSON:share:x", detector="communities", kind="corpus", score=0.5,
                    title="Circle around X: share of mentions",
                    stats={"members": ["x"], "rates": {"2021": 5.0, "2022": 6.0, "2023": 40.0, "2024": 42.0}})
    ctx.memo["findings"] = [share]
    found = TRANSITIONS.run(ctx, "anchor")
    circle = [f for f in found if "circle" in f.stats["kinds"]]
    assert circle and circle[0].stats["start"] == "2023-01-01"


def test_plan_skips_without_anchor_or_relations():
    ents, rels = world()
    assert isinstance(TRANSITIONS.plan(make_ctx(ents, anchor_relations=rels)), Skip)
    assert isinstance(TRANSITIONS.plan(make_ctx(ents, anchors={"me"})), Skip)
