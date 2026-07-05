"""Conflict detection + refine-graph guard unit tests (no Neo4j unless noted)."""
import inspect
import artmind.refine_graph as rg

from artmind.conflicts import conflict_id, _name_ratio


def test_refine_graph_accepts_allow_cross_domain_merge():
    sig = inspect.signature(rg.refine_graph)
    assert "allow_cross_domain_merge" in sig.parameters


def test_apply_merges_writes_refine_run_marker():
    src = inspect.getsource(rg)
    assert "RefineRun" in src


def test_conflict_id_is_order_independent():
    a = conflict_id("idB", "idA", "fee reversal approval limit")
    b = conflict_id("idA", "idB", "fee reversal approval limit")
    assert a == b


def test_conflict_id_differs_by_aspect():
    assert conflict_id("idA", "idB", "aspect one") != conflict_id("idA", "idB", "aspect two")


def test_name_ratio_high_for_similar():
    assert _name_ratio("Fee Reversal", "fee reversal") > 0.9


def test_name_ratio_low_for_different():
    assert _name_ratio("Fee Reversal", "Sanctions List") < 0.5


from artmind.conflicts import _verdict_from_raw


def test_verdict_conflicting_claims():
    raw = '{"verdict":"conflicting_claims","aspect":"fee reversal approval limit","claim_a":"CEO >£500","claim_b":"Manager £1,000","severity":"high"}'
    v = _verdict_from_raw(raw)
    assert v["verdict"] == "conflicting_claims"
    assert v["severity"] == "high"


def test_verdict_defaults_to_unrelated_on_garbage():
    v = _verdict_from_raw("not json at all")
    assert v["verdict"] == "unrelated"
