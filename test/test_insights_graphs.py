import pytest

from _insights_helpers import ent
from artmind.insights import graphs


def two_groups():
    da = [f"2023-01-{d:02d}" for d in range(1, 11)] + [f"2024-01-{d:02d}" for d in range(1, 11)]
    db = [f"2023-06-{d:02d}" for d in range(1, 11)] + [f"2024-06-{d:02d}" for d in range(1, 11)]
    a = [ent(f"a{i}", "PERSON", da) for i in range(1, 5)]
    b = [ent(f"b{i}", "PERSON", db) for i in range(1, 5)]
    x = ent("x", "PERSON", da[:3] + db[:3] + da[10:13] + db[10:13])
    return a + b + [x]


def test_co_mention_weights_and_floor():
    p = ent("p", "PERSON", ["2024-01-01", "2024-01-02", "2024-01-03"])
    q = ent("q", "PERSON", ["2024-01-01", "2024-01-02"])
    r = ent("r", "PERSON", ["2024-01-03"])
    g = graphs.co_mention_graph([p, q, r], min_weight=2)
    assert sorted(g.edges(data="weight")) == [("p", "q", 2)]
    assert g["p"]["q"]["distance"] == 0.5
    assert g.nodes["p"]["name"] == "p"


def test_louvain_finds_the_planted_groups_deterministically():
    g = graphs.co_mention_graph(two_groups())
    first, second = graphs.louvain(g), graphs.louvain(g)
    assert first == second
    sets = [set(c) for c in first]
    assert len(sets) == 2
    assert any({"a1", "a2", "a3", "a4"} <= s for s in sets)
    assert any({"b1", "b2", "b3", "b4"} <= s for s in sets)
    assert graphs.modularity(g, first) > 0.3


def test_restrict_keeps_one_period():
    rows = graphs.restrict(two_groups(), "2023", "year")
    assert all(d.startswith("2023") for e in rows for d in e.days)


def test_lead_and_weighted_degree():
    g = graphs.co_mention_graph(two_groups())
    wdeg = graphs.weighted_degree(g)
    assert graphs.lead(["a1", "a2"], wdeg) == "a1"


def test_caps(monkeypatch):
    monkeypatch.setattr(graphs, "MAX_NODES", 3)
    with pytest.raises(graphs.GraphTooLarge, match="raise min_evidence_days"):
        graphs.co_mention_graph(two_groups())


def test_empty_graph():
    g = graphs.co_mention_graph([])
    assert graphs.louvain(g) == [] and graphs.modularity(g, []) == 0.0
