from _insights_helpers import ent, make_ctx
from artmind.insights.config import KNOWN_DETECTORS
from artmind.insights.detectors import Skip, registry, subject


def test_registry_order_and_names():
    names = [d.name for d in registry()]
    assert names == [
        "salience_trend", "type_trend", "dormancy", "communities", "brokers",
        "outcome_lift", "transitions", "open_loops", "same_as_candidates", "split_signal",
        "kind_mismatch", "vocabulary_sprawl", "relationship_usage", "open_conflicts",
        "attribution_suspects", "fragments",
    ]
    assert set(names) <= KNOWN_DETECTORS
    assert {d.kind for d in registry()} == {"corpus", "health"}


def test_context_helpers():
    a = ent("a", "PERSON", ["2022-01-01", "2022-02-01", "2022-03-01", "2022-04-01"])
    b = ent("b", "PERSON", ["2022-01-01"])
    me = ent("me", "PERSON", a.days)
    ctx = make_ctx([a, b, me], anchors={"me"}, docs_per_period={"2021": 5, "2022": 100, "2023": 30})
    assert [e._id for e in ctx.members("PERSON")] == ["a"]
    assert [e._id for e in ctx.members("PERSON", floor=1)] == ["a", "b"]
    assert ctx.scored_periods() == ["2022", "2023"]
    assert ctx.temporal()
    assert ctx.classes_of_kind("recurrent") == ["PERSON"]
    assert subject(a) == {"_id": "a", "name": "a", "class": "PERSON"}


def test_context_not_temporal_when_dates_defaulted():
    ctx = make_ctx([ent("a", "PERSON", ["2022-01-01"])], default_time_share=0.9)
    assert not ctx.temporal()
    assert Skip("x").reason == "x"


def test_context_extras_default_empty():
    ctx = make_ctx([ent("a", "PERSON", ["2022-01-01"])])
    assert ctx.entity_props == {} and ctx.anchor_relations == [] and ctx.profile.entity_edges == []
    ctx2 = make_ctx([ent("a", "PERSON", ["2022-01-01"])], entity_props={("PERSON", "x"): {"a": "1"}},
                    anchor_relations=[{"rel": "R", "_id": "a"}], entity_edges=[("a", "b")])
    assert ctx2.entity_props[("PERSON", "x")] == {"a": "1"} and ctx2.profile.entity_edges == [("a", "b")]
