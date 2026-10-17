from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.brokers import BROKERS
from artmind.insights.detectors.communities import COMMUNITIES

DOCS = {"2023": 100, "2024": 100}


def two_groups():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)] + [f"2024-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)] + [f"2024-06-{d:02d}" for d in range(1, 11)]
    a = [ent(f"a{i}", "PERSON", da) for i in range(1, 5)]
    b = [ent(f"b{i}", "PERSON", db) for i in range(1, 5)]
    x = ent("x", "PERSON", da[:3] + db[:3] + da[10:13] + db[10:13], name="X")
    return a + b + [x]


def test_bridge_node_is_the_top_broker():
    ctx = make_ctx(two_groups(), docs_per_period=DOCS)
    COMMUNITIES.run(ctx, "PERSON")
    assert BROKERS.plan(ctx) == ["PERSON"]

    found = BROKERS.run(ctx, "PERSON")

    assert found[0].key == "brokers:PERSON:x" and found[0].score == 1.0
    assert found[0].title == "Connects 2 circles: X"
    assert len(found[0].stats["circles"]) == 2


def test_plan_skips_when_communities_did_not_run():
    assert isinstance(BROKERS.plan(make_ctx(two_groups(), docs_per_period=DOCS)), Skip)
