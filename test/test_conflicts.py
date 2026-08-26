"""Conflict detection + refine-graph guard unit tests (no Neo4j unless noted)."""
import inspect
import artmind.refine_graph as rg
import artmind.conflicts as conflicts_mod

from artmind.conflicts import conflict_id, _name_ratio


def test_refine_graph_accepts_allow_cross_domain_merge():
    sig = inspect.signature(rg.refine_graph)
    assert "allow_cross_domain_merge" in sig.parameters


def test_refine_run_gate_is_gone():
    # Phase 6: RefineRun blocked conflict detection since day one (count 0).
    # Deleted outright, along with the destructive apply_merges/mergeNodes
    # path -- refine_graph's clustering survives only as a same-as proposer.
    assert "RefineRun" not in inspect.getsource(rg)
    assert not hasattr(conflicts_mod, "check_refine_precondition")
    assert not hasattr(rg, "apply_merges")
    assert not hasattr(rg, "_merge_entity_pair")


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


def test_verdict_superseded_recognized():
    from artmind.conflicts import _verdict_from_raw
    raw = '{"verdict":"superseded","aspect":"x","claim_a":"old","claim_b":"new","severity":"low"}'
    assert _verdict_from_raw(raw)["verdict"] == "superseded"


def test_materialize_superseded_creates_supersedes_not_conflict(monkeypatch):
    import artmind.conflicts as c
    calls = {"supersede": 0}
    def fake_apply(newer_doc_id, older_doc_id, scope="document", effective=None, detected_by="adjudicator"):
        calls["supersede"] += 1
        return {}
    monkeypatch.setattr(c, "apply_supersession", fake_apply, raising=False)
    # verdict=superseded must route to supersession, returning None for Conflict id
    class FakeSession:
        def run(self, *a, **k):
            class R:
                def single(self_inner): return {"a": "docA", "b": "docB"}
                def data(self_inner): return [{"a": "docA", "b": "docB"}]
            return R()
    pair = {"id_a": "eA", "id_b": "eB", "domain_a": "d", "domain_b": "d", "entity_class": "POLICY",
            "name_a": "x", "name_b": "x"}
    verdict = {"verdict": "superseded", "aspect": "x", "claim_a": "old", "claim_b": "new", "severity": "low"}
    cid = c.materialize(FakeSession(), pair, verdict, [], [], "m")
    assert cid is None
    assert calls["supersede"] == 1
