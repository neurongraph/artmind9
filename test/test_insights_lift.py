from datetime import date, timedelta

from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.lift import CAPTION, OUTCOME_LIFT, day_polarity

DAYS = [(date(2023, 1, 1) + timedelta(days=i)).isoformat() for i in range(40)]
JOY, SAD = DAYS[:20], DAYS[20:]
OUTCOME = {"outcome": {"class": "EMOTION", "positive": ["joy"], "negative": ["sadness"]}}


def world():
    ents = [ent(f"j{i}", "EMOTION", [d], type="joy") for i, d in enumerate(JOY)]
    ents += [ent(f"s{i}", "EMOTION", [d], type="Sadness") for i, d in enumerate(SAD)]
    ents.append(ent("w", "PERSON", SAD[:15] + JOY[:2], name="Wren"))
    ents.append(ent("t", "PERSON", JOY[:15] + SAD[:2], name="Tess"))
    ents.append(ent("n", "PERSON", JOY[5:10] + SAD[5:10], name="Nell"))
    ents.append(ent("me", "PERSON", DAYS, name="Me"))
    ents += [ent(f"v{i}", "EVENT", [d], type="clash") for i, d in enumerate(SAD[:17])]
    return ents


def ctx_for(config=OUTCOME, **kw):
    return make_ctx(world(), config=config, anchors={"me"}, docs_per_period={"2023": 100},
                    effective={"EVENT": "occurrent"}, **kw)


def test_day_polarity_majority_and_ties():
    ctx = ctx_for()
    mood = day_polarity(ctx, ctx.config.block("outcome"))
    assert len(mood) == 40 and mood[JOY[0]] == 1 and mood[SAD[0]] == -1


def test_worse_and_better_subjects():
    ctx = ctx_for()
    assert OUTCOME_LIFT.plan(ctx) == ["all"]
    found = {f.key: f for f in OUTCOME_LIFT.run(ctx, "all")}
    worse, better = found["outcome_lift:PERSON:w"], found["outcome_lift:PERSON:t"]
    assert worse.stats["direction"] == "worse" and worse.title == "Worse days with Wren" and worse.stats["z"] > 2
    assert better.stats["direction"] == "better" and better.stats["z"] < -2
    assert worse.stats["baseline_negative_share"] == 0.5 and worse.card["caption"] == CAPTION
    assert "outcome_lift:PERSON:n" not in found          # no lift
    assert "outcome_lift:PERSON:me" not in found         # anchor
    assert not any(k.startswith("outcome_lift:EMOTION") for k in found)   # never the outcome class itself
    assert found["outcome_lift:EVENT:type:clash"].stats["direction"] == "worse"
    assert all(0 < f.score <= 1 for f in found.values())


def test_plan_skips():
    assert OUTCOME_LIFT.plan(ctx_for(config={})).reason == "not configured"
    other = {"outcome": {"class": "MOOD", "positive": ["joy"], "negative": ["sad"]}}
    assert isinstance(OUTCOME_LIFT.plan(ctx_for(config=other)), Skip)
    assert isinstance(OUTCOME_LIFT.plan(ctx_for(default_time_share=0.9)), Skip)


def test_non_type_property_uses_entity_props():
    cfg = {"outcome": {"class": "EMOTION", "property": "valence", "positive": ["good"], "negative": ["bad"]}}
    props = {("EMOTION", "valence"): {**{f"j{i}": "Good" for i in range(20)}, **{f"s{i}": "bad" for i in range(20)}}}
    found = {f.key for f in OUTCOME_LIFT.run(ctx_for(config=cfg, entity_props=props), "all")}
    assert "outcome_lift:PERSON:w" in found


def test_bh_controls_false_discoveries_across_many_null_subjects():
    import random
    rng = random.Random(7)
    ents = [ent(f"j{i}", "EMOTION", [d], type="joy") for i, d in enumerate(JOY)]
    ents += [ent(f"s{i}", "EMOTION", [d], type="sadness") for i, d in enumerate(SAD)]
    for i in range(60):
        k = rng.choice([8, 8, 9, 10, 11, 12])      # mostly balanced, a few mildly skewed (z ~ 2-3)
        days = rng.sample(SAD, k) + rng.sample(JOY, 16 - k)
        ents.append(ent(f"null{i}", "PERSON", days, name=f"Null{i}"))
    ents.append(ent("strong", "PERSON", SAD[:19] + JOY[:1], name="Strong"))
    ents.append(ent("me", "PERSON", DAYS, name="Me"))
    ctx = make_ctx(ents, config=OUTCOME, anchors={"me"}, docs_per_period={"2023": 100})
    found = OUTCOME_LIFT.run(ctx, "all")
    assert [f.key for f in found] == ["outcome_lift:PERSON:strong"]
    st = found[0].stats
    assert st["q"] <= 0.05 and st["p"] <= st["q"]


def test_fdr_and_min_support_are_configurable():
    cfg = {**OUTCOME, "detectors": {"outcome_lift": {"fdr": 1e-12}}}
    assert OUTCOME_LIFT.run(ctx_for(config=cfg), "all") == []
