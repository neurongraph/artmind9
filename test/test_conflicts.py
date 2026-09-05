"""Conflict detection + refine-graph guard unit tests (no Neo4j unless noted)."""
import inspect
import artmind.refine_graph as rg
import artmind.conflicts as conflicts_mod

from artmind.conflicts import conflict_id, _name_ratio, candidate_pairs


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class _FakeSession:
    """Answers `candidate_pairs`'s two query shapes: the plain entity fetch,
    and the per-source ANN neighbor lookup (routed by cypher text, per
    CLAUDE.md's "assert on the query that ran" rule — a MagicMock can't
    distinguish the two)."""

    def __init__(self, sources, neighbors_by_src_id):
        self.calls: list[tuple[str, dict]] = []
        self._sources = sources
        self._neighbors_by_src_id = neighbors_by_src_id

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "SEARCH node IN" in cypher:
            return _FakeResult(self._neighbors_by_src_id.get(params["srcId"], []))
        return _FakeResult(self._sources)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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


def test_candidate_pairs_ann_leg_uses_search_construct_not_deprecated_query_nodes(monkeypatch):
    """The ANN neighbor query must use Cypher 25's `SEARCH ... VECTOR INDEX`
    construct, not the deprecated `db.index.vector.queryNodes` procedure
    (Neo4j 2026.04 deprecation) -- and it must still find the right pair."""
    sources = [{
        "id": "e1", "name": "Alice Cooper", "entity_class": "PERSON",
        "domain": "fiction.a", "embedding": [0.1, 0.2], "key": "k1",
    }]
    neighbors_by_src_id = {
        "e1": [{"id": "e2", "name": "Alice Cooper", "domain": "fiction.b", "key": "k2", "score": 0.95}],
    }
    fake = _FakeSession(sources, neighbors_by_src_id)
    monkeypatch.setattr(conflicts_mod, "neo4j_session", lambda: fake)

    pairs = candidate_pairs(["fiction.a", "fiction.b"], None, sim_threshold=0.8, max_pairs=10)

    assert pairs == [{
        "id_a": "e1", "name_a": "Alice Cooper", "domain_a": "fiction.a", "key_a": "k1",
        "id_b": "e2", "name_b": "Alice Cooper", "domain_b": "fiction.b", "key_b": "k2",
        "entity_class": "PERSON", "sim": 0.95, "name_ratio": 1.0,
    }]

    ann_calls = [(cy, p) for cy, p in fake.calls if "SEARCH node IN" in cy]
    assert len(ann_calls) == 1, "one ANN query per source entity"
    cypher, params = ann_calls[0]
    assert "db.index.vector.queryNodes" not in cypher
    assert "VECTOR INDEX entity_embedding" in cypher
    assert "vector.similarity.cosine(node.embedding, $embedding)" in cypher
    assert params["srcId"] == "e1"
    assert params["others"] == ["fiction.b"]
    assert params["cls"] == "PERSON"


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
