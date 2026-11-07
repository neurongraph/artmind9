"""B5: step `needs: [insights_snapshot]`, the superlative-over-records move to `structured`, and the
no-snapshot text needle (spec 2026-11-07 b3-b4-b5 section 3). Synthetic names only."""

import pytest

from artmind.query_plan import candidates, planner, routes
from artmind.query_plan import program as prog
from test_query_plan_planner import FakeClassifier, _answer, _plan, _use, world  # noqa: F401


def _cmds(result):
    return [s["cmd"] for s in result["steps"] + result["suggested_steps"]]


def _route(route_id):
    return next(r for r in routes.load_routes() if r.id == route_id)


# ── loader ───────────────────────────────────────────────────────────────────

def _v2(program):
    return {"version": 2, "routes": [{"id": "p1", "description": "A shape.", "triggers": ["x"], "needs": [],
                                      "read": [], "evidence": ["S1"], "program": program}]}


RUN = "artmind query insights list --domain {domain} --compact"


def test_loader_accepts_a_known_need():
    (route,) = routes.parse_routes(_v2([{"id": "s1", "needs": ["insights_snapshot"], "run": RUN}]))
    assert prog.run_nodes(route.program)[0].needs == ("insights_snapshot",)


@pytest.mark.parametrize("needs", [["bogus"], [], "insights_snapshot", [3]])
def test_loader_rejects_an_unknown_or_malformed_need(needs):
    with pytest.raises(routes.RouteError, match="needs"):
        routes.parse_routes(_v2([{"id": "s1", "needs": needs, "run": RUN}]))


def test_loader_rejects_needs_on_a_non_run_node():
    node = {"id": "p", "needs": ["insights_snapshot"], "par": [{"id": "s1", "run": RUN}]}
    with pytest.raises(routes.RouteError, match="run node"):
        routes.parse_routes(_v2([node]))


def test_packaged_routes_declare_the_need():
    for rid in ("superlative", "insights"):
        assert any(n.needs == ("insights_snapshot",) for c in _route(rid).programs for n in prog.run_nodes(c.program))
    assert "insights_snapshot" in prog.PLAN_FACT_NAMES


# ── the planner ──────────────────────────────────────────────────────────────

def _superlative(monkeypatch, conf=0.9):
    _use(monkeypatch, FakeClassifier(override={"route": _answer("superlative", conf)}))


def _two_domains(world, snap_a, snap_b):
    base = world["overview"][0]
    world["overview"][:] = [{**base, "domain": "a", "insights": snap_a}, {**base, "domain": "b", "insights": snap_b}]
    world["views"] = []


def test_the_insights_step_is_skipped_per_domain_without_a_snapshot(world, monkeypatch):
    _two_domains(world, True, False)
    _superlative(monkeypatch)
    result = _plan("What is the biggest thing?", domains=("a", "b"))
    cmds = _cmds(result)
    assert "artmind query insights list --domain a --compact" in cmds
    assert not any("insights list --domain b" in c for c in cmds)
    assert any("text2cypher" in c for c in cmds)
    assert result["route"]["id"] == "superlative"


def test_no_snapshot_means_text2cypher_plus_the_needle(world, monkeypatch):
    world["overview"][0]["insights"] = False
    world["views"] = []
    _superlative(monkeypatch)
    result = _plan("What is the biggest thing?")
    cmds = _cmds(result)
    assert not any("insights list" in c for c in cmds)
    assert "text2cypher" in cmds[0]
    assert "vector-text --shape brief --domain journal --topK 10" in cmds[1]
    assert result["plan_facts"] == {"insights_snapshot": 0}


def test_a_snapshot_keeps_todays_superlative_plan(world, monkeypatch):
    world["overview"][0]["insights"] = True
    world["views"] = []
    _superlative(monkeypatch)
    result = _plan("What is the biggest thing?")
    cmds = _cmds(result)
    assert cmds[0] == "artmind query insights list --domain journal --compact"
    assert "text2cypher" in cmds[1] and cmds[2].startswith("artmind query survey --shape brief") and len(cmds) == 3
    assert result["plan_facts"] == {"insights_snapshot": 1}


def test_an_overview_without_the_flag_counts_as_a_snapshot(world, monkeypatch):
    assert "insights" not in world["overview"][0]
    world["views"] = []
    _superlative(monkeypatch)
    cmds = _cmds(_plan("What is the biggest thing?"))
    assert cmds[0] == "artmind query insights list --domain journal --compact" and len(cmds) == 3


def test_the_insights_route_is_unfilled_without_a_snapshot(world, monkeypatch):
    world["overview"][0]["insights"] = False
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer("insights", 0.95)}))
    result = _plan("What changed lately?")
    assert result["confidence"]["level"] == "low" and result["confidence"]["weakest"] == "unfilled"
    assert result["steps"] == []
    assert any("insights snapshot" in w for w in result["warnings"])
    assert not any("insights list" in c for c in _cmds(result))  # falls back to the search steps


def test_the_insights_route_runs_with_a_snapshot(world, monkeypatch):
    world["overview"][0]["insights"] = True
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer("insights", 0.95)}))
    result = _plan("What changed lately?")
    assert [s["cmd"].split()[2] for s in result["steps"]] == ["insights", "survey"]
    assert result["steps"][0]["cmd"] == "artmind query insights list --domain journal --compact"


# ── superlative over records -> structured ───────────────────────────────────

def _store(monkeypatch, bridge=("CUSTOMER",), kind="hybrid"):
    monkeypatch.setattr(candidates, "store_facts", lambda d, q, classes: {
        "kind": kind, "tables": ["t"], "bridge_classes": list(bridge), "matched_values": []})


def _bridged(world, name="Ada"):
    world["entities"][name] = [{"_id": "c1", "name": name, "entity_class": "CUSTOMER", "matched_by": ["exact"]}]


def test_a_superlative_over_bridged_records_becomes_structured(world, monkeypatch):
    _bridged(world)
    _store(monkeypatch)
    world["views"] = []
    _superlative(monkeypatch)
    result = _plan("Which Ada has the most cases on record?")
    assert result["route"]["id"] == "structured"
    assert result["route"]["adjusted"] == {"from": "superlative", "by": "store_records"}
    cmds = _cmds(result)
    assert "db bridge" in cmds[0] and "text2sql" in cmds[1]


def test_a_records_superlative_gets_the_store_scope(world, monkeypatch):
    seen = []
    real = planner._family_rescope

    def spy(dset, route_id, **kw):
        seen.append(route_id)
        return real(dset, route_id, **kw)

    monkeypatch.setattr(planner, "_family_rescope", spy)
    _bridged(world)
    _store(monkeypatch)
    world["views"] = []
    _superlative(monkeypatch)
    _plan("Which Ada has the most cases on record?")
    assert "structured" in seen


def test_a_superlative_without_a_records_cue_stays_a_superlative(world, monkeypatch):
    _bridged(world)
    _store(monkeypatch)
    world["views"] = []
    _superlative(monkeypatch)
    result = _plan("Which Ada matters the most for the plan?")
    assert result["route"]["id"] == "superlative" and result["route"]["adjusted"] is None


def test_a_superlative_naming_no_bridged_class_stays_a_superlative(world, monkeypatch):
    _store(monkeypatch, bridge=("ACCOUNT",))
    _bridged(world)  # a CUSTOMER, not bridged here
    world["views"] = []
    _superlative(monkeypatch)
    result = _plan("Which Ada has the most cases on record?")
    assert result["route"]["id"] == "superlative"


def test_no_store_leaves_the_superlative_alone(world, monkeypatch):
    _bridged(world)
    world["views"] = []
    _superlative(monkeypatch)
    result = _plan("Which Ada has the most cases on record?")
    assert result["route"]["id"] == "superlative"
