"""The plan payload carries what the executor needs (P3 spec §3.5): replacement programs and unmet needs."""

import pytest

from artmind.query_plan import planner, program as prog, slots
from test_query_plan_planner import _plan, world  # noqa: F401  (fixture and helper)


@pytest.fixture
def common_words(monkeypatch):
    monkeypatch.setattr(slots, "rare_term", lambda *a, **k: False)


def test_a_replace_guard_ships_the_rendered_replacement(world, common_words):
    world["views"] = []
    result = _plan("Did I visit Lakeport again?")
    assert [g["then"] for g in result["guards"]] == [{"kind": "replace", "route": "search"}]  # unchanged
    rep = result["replacements"]["search"]
    first = rep["program"][0]
    assert first["kind"] == "par" and [c["id"] for c in first["children"]] == ["needle", "wide"]
    assert all(c["filled"] for c in first["children"])
    assert [g["then"]["kind"] for g in rep["guards"]] == ["page", "escalate"]


def test_search_is_always_a_replacement_except_for_search_itself(world, common_words):
    world["views"] = []
    assert "search" in _plan("Did I visit Lakeport again?")["replacements"]
    assert "search" not in _plan("How do I renew a passport?")["replacements"]


def test_an_unmet_need_is_marked_on_the_node():
    node = prog.Node("s1", "run", run="artmind query insights list --domain {domain} --compact",
                     needs=("insights_snapshot",))
    ctx = {"domain": "journal", "domain_list": ("journal",), "snapshot_domains": frozenset()}
    assert planner._node_json(node, ctx)["needs_unmet"] is True
    met = {**ctx, "snapshot_domains": frozenset({"journal"})}
    assert "needs_unmet" not in planner._node_json(node, met)


def test_the_shadow_trim_drops_replacements_when_over_the_cap(monkeypatch):
    from artmind.query_plan import shadow
    monkeypatch.setattr(shadow, "MAX_PAYLOAD_BYTES", 2000)
    plan = {"confidence": {"level": "low"}, "route": {"id": "x"}, "program": [{"id": "s1", "pad": "a" * 100}],
            "guards": [], "gaps": [], "replacements": {"search": {"program": [{"pad": "b" * 3000}], "guards": []}}}
    out = shadow._trim(plan)
    assert out["trimmed"] is True and out["replacements"] == {} and out["program"] == []
    assert plan["replacements"]["search"]["program"][0]["pad"] == "b" * 3000  # the input is not mutated

