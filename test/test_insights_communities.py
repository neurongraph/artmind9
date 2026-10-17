from _insights_helpers import ent, make_ctx
from artmind.insights import graphs
from artmind.insights.detectors import Skip
from artmind.insights.detectors.communities import COMMUNITIES

DOCS = {"2023": 100, "2024": 100}


def two_groups():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)] + [f"2024-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)] + [f"2024-06-{d:02d}" for d in range(1, 11)]
    a = [ent(f"a{i}", "PERSON", da, name=f"A{i}") for i in range(1, 5)]
    b = [ent(f"b{i}", "PERSON", db, name=f"B{i}") for i in range(1, 5)]
    x = ent("x", "PERSON", da[:3] + db[:3] + da[10:13] + db[10:13], name="X")
    return a + b + [x]


def test_circles_shares_and_cohesion():
    ctx = make_ctx(two_groups(), docs_per_period=DOCS)
    assert COMMUNITIES.plan(ctx) == ["PERSON"]

    found = COMMUNITIES.run(ctx, "PERSON")

    circles = [f for f in found if ":circle:" in f.key]
    assert len(circles) == 2
    member_sets = [set(f.stats["members"]) for f in circles]
    assert any({"a1", "a2", "a3", "a4"} <= s for s in member_sets)
    assert any({"b1", "b2", "b3", "b4"} <= s for s in member_sets)
    net = circles[0].card
    assert net["type"] == "network" and net["nodes"] and net["edges"]
    assert net["truncated"] is False
    assert {f.key.split(":")[2] for f in found} == {"circle", "share", "cohesion"}
    share = next(f for f in found if ":share:" in f.key)
    assert set(share.stats["rates"]) == {"2023", "2024"}
    _, memo_circles = ctx.memo[("circles", "PERSON")]
    assert {c["id"] for c in memo_circles} == {f"PERSON:{f.key.rsplit(':', 1)[1]}" for f in circles}
    assert all(0 <= f.score <= 1 for f in found)


def test_deterministic():
    one = [f.to_dict() for f in COMMUNITIES.run(make_ctx(two_groups(), docs_per_period=DOCS), "PERSON")]
    two = [f.to_dict() for f in COMMUNITIES.run(make_ctx(two_groups(), docs_per_period=DOCS), "PERSON")]
    assert one == two


def test_no_dates_means_circles_only():
    ctx = make_ctx(two_groups(), docs_per_period=DOCS, default_time_share=0.9)
    assert {f.key.split(":")[2] for f in COMMUNITIES.run(ctx, "PERSON")} == {"circle"}


def test_too_large_becomes_a_health_finding(monkeypatch):
    monkeypatch.setattr(graphs, "MAX_NODES", 3)
    (f,) = COMMUNITIES.run(make_ctx(two_groups(), docs_per_period=DOCS), "PERSON")
    assert f.key == "communities:PERSON:too_large" and f.kind == "health" and f.action == "config"


def test_cohesion_needs_two_periods_with_edges():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)]
    only_2023 = [ent(f"a{i}", "PERSON", da, name=f"A{i}") for i in range(1, 5)]
    only_2023 += [ent(f"b{i}", "PERSON", db, name=f"B{i}") for i in range(1, 5)]
    ctx = make_ctx(only_2023, docs_per_period=DOCS)

    found = COMMUNITIES.run(ctx, "PERSON")

    assert any(":circle:" in f.key for f in found)
    assert not any(":cohesion" in f.key for f in found)


def test_plan_prefers_configured_classes_and_needs_members():
    ctx = make_ctx(two_groups(), docs_per_period=DOCS, config={"detectors": {"communities": {"classes": ["LOCATION"]}}})
    assert isinstance(COMMUNITIES.plan(ctx), Skip)
