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
    assert f.key == "transitions:2022-12-20:left_acme"
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


def _explicit_ctx(dates, **kw):
    ents, _ = world()
    rels = [rel("JOINED", "orgA" if i % 2 == 0 else "orgB", f"Org{i}", "ORGANIZATION", [d]) for i, d in enumerate(dates)]
    return make_ctx(ents, anchors={"me"}, anchor_relations=rels, **kw)


def test_clusters_are_bounded_by_the_group_start():
    found = TRANSITIONS.run(_explicit_ctx(["2023-01-01", "2023-03-22", "2023-06-10"]), "anchor")
    assert [f.stats["events"] for f in found] == [2, 1]


def test_key_is_stable_when_a_non_explicit_event_joins():
    base = {f.key for f in TRANSITIONS.run(ctx_for(), "anchor")}
    ctx = ctx_for()
    ctx.memo["findings"] = [Finding(
        key="communities:PERSON:share:x", detector="communities", kind="corpus", score=0.5,
        title="Circle around X: share of mentions", subjects=[{"_id": "x", "name": "Circle X", "class": "PERSON"}],
        stats={"rates": {"2021": 5.0, "2022": 6.0, "2023": 40.0, "2024": 42.0}})]
    found = TRANSITIONS.run(ctx, "anchor")
    assert {f.key for f in found} == base
    assert any("Circle X rose" in e["label"] for f in found for e in f.card["events"])


def test_score_formula():
    (f,) = TRANSITIONS.run(_explicit_ctx(["2023-01-01", "2023-01-05"]), "anchor")
    assert f.score == 0.35


def _dominance_ctx(per_year, **kw):
    """per_year: {year: {target_id: n_days}} for WORKS_AT."""
    ents, _ = world()
    rels = []
    for tid in sorted({t for v in per_year.values() for t in v}):
        days = [f"{y}-{(i % 12) + 1:02d}-{15 + (i // 12)}" for y, v in sorted(per_year.items()) for i in range(v.get(tid, 0))]
        rels.append(rel("WORKS_AT", tid, tid.upper(), "ORGANIZATION", days))
    return make_ctx(ents, anchors={"me"}, anchor_relations=rels, **kw)


def _kinds(found):
    return [e["kind"] for f in found for e in f.card["events"]]


def test_a_tie_is_not_a_dominant_target():
    ctx = _dominance_ctx({"2022": {"b": 8}, "2023": {"a": 5, "b": 5}})
    assert "dominance" not in _kinds(TRANSITIONS.run(ctx, "anchor"))


def test_dominance_must_beat_runner_up_by_margin():
    ctx = _dominance_ctx({"2022": {"a": 8}, "2023": {"b": 6, "a": 5}})
    assert "dominance" not in _kinds(TRANSITIONS.run(ctx, "anchor"))
    ctx = _dominance_ctx({"2022": {"a": 8}, "2023": {"b": 9, "a": 5}})
    assert "dominance" in _kinds(TRANSITIONS.run(ctx, "anchor"))


def test_a_quiet_period_between_dominant_targets_still_counts():
    ctx = _dominance_ctx({"2022": {"a": 8}, "2023": {"a": 1, "b": 1, "c": 1}, "2024": {"b": 8}})
    assert _kinds(TRANSITIONS.run(ctx, "anchor")).count("dominance") == 1


def test_gap_longer_than_max_gap_periods_breaks_the_link():
    years = {"2018": {"a": 8}, "2022": {"b": 8}}
    ctx = _dominance_ctx(years, docs_per_period={str(y): 100 for y in range(2018, 2023)})
    assert "dominance" not in _kinds(TRANSITIONS.run(ctx, "anchor"))      # 3 quiet years in between
    ctx = _dominance_ctx(years, docs_per_period={str(y): 100 for y in range(2018, 2023)},
                         config={"detectors": {"transitions": {"max_gap_periods": 3}}})
    assert "dominance" in _kinds(TRANSITIONS.run(ctx, "anchor"))


def test_extra_verbs_are_explicit_only():
    ents, _ = world()
    rels = [rel("MENTORS", "orgA", "Acme", "ORGANIZATION", [f"2022-{m:02d}-10" for m in range(2, 8)]),
            rel("MENTORS", "orgB", "Bolt", "ORGANIZATION", [f"2023-{m:02d}-10" for m in range(1, 7)])]
    ctx = make_ctx(ents, anchors={"me"}, anchor_relations=rels,
                   config={"transitions": {"extra_verbs": ["MENTORS"]}})
    assert set(_kinds(TRANSITIONS.run(ctx, "anchor"))) == {"explicit"}


def test_circle_shift_label_prefers_subject_name():
    ctx = ctx_for(anchor_relations=[])
    ctx.anchor_relations = [rel("WORKS_AT", "orgA", "Acme", "ORGANIZATION", ["2021-05-05"])]
    ctx.memo["findings"] = [Finding(
        key="communities:PERSON:share:x", detector="communities", kind="corpus", score=0.5,
        title="Something: share of mentions", subjects=[{"_id": "x", "name": "Book club", "class": "PERSON"}],
        stats={"rates": {"2021": 5.0, "2022": 6.0, "2023": 40.0, "2024": 42.0}})]
    labels = [e["label"] for f in TRANSITIONS.run(ctx, "anchor") for e in f.card["events"]]
    assert any(l.startswith("Book club rose") for l in labels)
