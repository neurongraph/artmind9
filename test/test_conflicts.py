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



# ── materialize writes the conflict's record and applies it (spec §14 A6) ────


import json as _json

import pytest as _pytest

from artmind import curation_records as _curation_records


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return self._rows


class _MaterializeSession:
    def __init__(self, graph_node=None):
        self.calls = []
        self._graph_node = graph_node

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "properties(co) AS co" in cypher:
            return _Rows([self._graph_node] if self._graph_node else [])
        if "MATCH (a:Entity {_id: $idA}" in cypher:
            return _Rows([{"n": 1}])   # both entities are in the graph
        if "DocChunk" in cypher:
            return _Rows([{"n": len({e["chunk_id"] for e in params["evidence"]})}])
        return _Rows([])

    def applied(self):
        return [p for c, p in self.calls if c.strip().startswith("MERGE (co:Conflict {id: $id})")]

    def edged(self):
        return [p for c, p in self.calls if "MATCH (a:Entity {_id: $idA}" in c]

    def fingerprinted(self):
        return [p for c, p in self.calls if "co.record_fingerprint = $fingerprint" in c]


_PAIR = {
    "id_a": "ea", "name_a": "Fee reversal", "domain_a": "banking.ops", "key_a": "fee reversal|POLICY|banking.ops",
    "id_b": "eb", "name_b": "Fee reversals", "domain_b": "banking.risk", "key_b": "fee reversals|POLICY|banking.risk",
    "entity_class": "POLICY",
}
_VERDICT = {"verdict": "conflicting_claims", "aspect": "approval limit", "claim_a": "CEO", "claim_b": "Manager", "severity": "high"}


@_pytest.fixture()
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


def test_materialize_writes_the_conflicts_record_and_applies_that_record(curation_dir):
    session = _MaterializeSession()

    cid = conflicts_mod.materialize(session, _PAIR, _VERDICT, [{"id": "d1_001", "doc_id": "d1"}], [], "m")

    assert cid == conflict_id("ea", "eb", "approval limit")
    path = curation_dir / "conflicts" / f"{cid}.json"
    record = _json.loads(path.read_text())
    assert (record["status"], record["claim_a"], record["claim_b"]) == ("open", "CEO", "Manager")
    applied = session.applied()
    assert len(applied) == 1
    assert applied[0]["status"] == "open"
    assert [(p["idA"], p["idB"]) for p in session.edged()] == [("ea", "eb")]
    assert session.fingerprinted() == [{"id": cid, "fingerprint": _curation_records.fingerprint(path.read_bytes())}]


def test_re_detecting_a_recorded_conflict_never_reopens_or_rewrites_it(curation_dir):
    session = _MaterializeSession()
    cid = conflicts_mod.materialize(session, _PAIR, _VERDICT, [], [], "m")
    path = curation_dir / "conflicts" / f"{cid}.json"
    record = _json.loads(path.read_text())
    record.update(status="resolved", resolution_reason="settled", resolved_at="t1")
    _curation_records.write_record("conflicts", record)
    before = path.read_bytes()

    again = _MaterializeSession()
    conflicts_mod.materialize(again, _PAIR, dict(_VERDICT, claim_a="a different wording"), [], [], "other-model")

    assert path.read_bytes() == before
    assert again.applied()[0]["status"] == "resolved"
    assert again.applied()[0]["claim_a"] == "CEO"
    assert not [c for c, _ in again.calls if "properties(co) AS co" in c], "the file wins; the graph is not read"


def test_re_detecting_a_conflict_that_predates_records_keeps_the_nodes_status(curation_dir):
    session = _MaterializeSession(graph_node={
        "co": {"id": "x", "aspect": "approval limit", "claim_a": "old A", "claim_b": "old B", "severity": "medium",
               "status": "dismissed", "resolution_reason": "noise", "resolved_at": "t1",
               "detected_at": "t0", "detected_by_model": "old-model", "_source": "adjudicator"},
        "entities": [], "evidence": [],
    })

    cid = conflicts_mod.materialize(session, _PAIR, _VERDICT, [], [], "m")

    record = _json.loads((curation_dir / "conflicts" / f"{cid}.json").read_text())
    assert (record["status"], record["claim_a"], record["detected_at"]) == ("dismissed", "old A", "t0")
    assert [e["id"] for e in record["entities"]] == ["ea", "eb"], "the entities come from this detection"
