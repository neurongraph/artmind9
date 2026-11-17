"""Identity gates (spec 2026-11-15 §5.3). Recorder-based: assert on what was sent. Invented names."""
from unittest.mock import patch

import pytest
from _insights_helpers import ent, make_ctx
from _neo4j_recorder import Recorder

from artmind import canonicalize, conflicts, identity, refine_graph, sameas
from artmind.insights.detectors.same_as import SAME_AS_CANDIDATES

SCHEMA = {"entity_types": {"PERSON": {"kind": "recurrent"}, "EMOTION": {"kind": "recurrent", "identity": "category"},
                           "ACTIVITY": {"kind": "occurrent", "identity": "occasion"}}}


@pytest.fixture
def schema(monkeypatch):
    monkeypatch.setattr(sameas, "_schema_for", lambda domain: SCHEMA)


def test_named_classes():
    assert canonicalize.named_classes(SCHEMA) == {"PERSON", "EMOTION"}


# ── sameas.propose: the central guard ───────────────────────────────────────


def test_propose_refuses_occasions_on_different_days(schema):
    rec = Recorder()
    pid = sameas.propose(rec, ("tennis~2024-03-30", "ACTIVITY", "pj"), [("tennis match~2024-04-02", "ACTIVITY", "pj")],
                         source="test")
    assert pid is None and not rec.queries_containing("MERGE (p:SameAsProposal")


def test_propose_refuses_a_dated_and_an_undated_occasion(schema):
    rec = Recorder()
    assert sameas.propose(rec, ("tennis~2024-03-30", "ACTIVITY", "pj"), [("tennis match", "ACTIVITY", "pj")],
                          source="test") is None
    assert rec.calls == []


def test_propose_accepts_same_day_variants(schema):
    rec = Recorder()
    pid = sameas.propose(rec, ("tennis~2024-03-30", "ACTIVITY", "pj"), [("tennis match~2024-03-30", "ACTIVITY", "pj")],
                         source="test")
    (cypher, params), = rec.queries_containing("MERGE (p:SameAsProposal")
    assert pid == params["id"]
    assert params["members"] == ["tennis match~2024-03-30|ACTIVITY|pj", "tennis~2024-03-30|ACTIVITY|pj"]
    assert params["canonical"] == "tennis~2024-03-30|ACTIVITY|pj"


def test_propose_refuses_category_members(schema):
    rec = Recorder()
    assert sameas.propose(rec, ("calm", "EMOTION", "pj"), [("serenity", "EMOTION", "pj")], source="t") is None
    assert rec.calls == []


def test_propose_accepts_name_members(schema):
    rec = Recorder()
    sameas.propose(rec, ("alex rivera", "PERSON", "pj"), [("alex", "PERSON", "pj")], source="t")
    (_, params), = rec.queries_containing("MERGE (p:SameAsProposal")
    assert params["members"] == ["alex rivera|PERSON|pj", "alex|PERSON|pj"]


def test_identity_reads_each_members_own_domain():
    seen = []

    def schema_for(domain):
        seen.append(domain)
        return SCHEMA if domain == "pj" else {"entity_types": {"EMOTION": {"kind": "recurrent"}}}

    # EMOTION is a category in pj, a name class in work: the pj member refuses the group.
    assert sameas.identity_refusal(("calm", "EMOTION", "work"), [("calm", "EMOTION", "pj")],
                                   schema_for=schema_for) == "identity:category (EMOTION)"
    assert sameas.identity_refusal(("calm", "EMOTION", "work"), [("calm", "EMOTION", "work2")],
                                   schema_for=schema_for) is None
    assert seen.count("pj") == 1  # one read per domain per call


def test_approve_rechecks_an_old_cross_day_proposal(schema):
    proposal = {"id": "p1", "status": "open", "canonical": "tennis~2024-03-30|ACTIVITY|pj",
                "members": ["tennis match~2024-04-02|ACTIVITY|pj", "tennis~2024-03-30|ACTIVITY|pj"]}
    with patch("artmind.sameas.get_proposal", return_value=proposal), \
         patch("artmind.same_as.load_groups", return_value=[]):
        with pytest.raises(ValueError, match="different days"):
            sameas.approve("p1", dry_run=True)


# ── refine_graph: clustering and counting ───────────────────────────────────


def test_refine_graph_counts_identity_refusals(schema, monkeypatch):
    def respond(cypher, params):
        if "collect(DISTINCT e.entity_class)" in cypher:
            return [{"name": n, "classes": ["ACTIVITY"]} for n in params["names"]]
        if "collect(DISTINCT e.key)" in cypher:
            day = {"Tennis": "2024-03-30", "Tennis match": "2024-04-02"}
            return [{"name": n, "keys": [f"{n.lower()}~{day[n]}|ACTIVITY|pj"]} for n in params["names"]]
        return []

    rec = Recorder(respond)
    monkeypatch.setattr(refine_graph, "neo4j_session", lambda: rec)
    result = refine_graph.propose_merges({"Tennis match": "Tennis"}, "pj", "m")
    assert result["stats"]["skipped_identity"] == 1 and result["stats"]["proposed"] == 0
    assert result["proposal_ids"] == [] and not rec.queries_containing("SameAsProposal")


def test_refine_graph_clusters_no_category_names(schema, monkeypatch):
    rows = [{"name": "Calm", "entity_class": "EMOTION", "domain": "pj"},
            {"name": "Calmness", "entity_class": "EMOTION", "domain": "pj"},
            {"name": "Alex", "entity_class": "PERSON", "domain": "pj"},
            {"name": "Alexx", "entity_class": "PERSON", "domain": "pj"}]
    rec = Recorder(lambda cypher, params: rows if "RETURN DISTINCT e.name" in cypher else [])
    monkeypatch.setattr(refine_graph, "neo4j_session", lambda: rec)
    clustered = []
    monkeypatch.setattr(refine_graph, "llm_merge_cluster",
                        lambda cluster, model, timeout: clustered.append(sorted(cluster)) or {})
    monkeypatch.setattr("utils.functions.load_env", lambda: {})
    refine_graph.refine_graph("pj", None, "m", 0.7, dry_run=True, output_file=None, from_file=None)
    (fetch, params), = rec.queries_containing("RETURN DISTINCT e.name")
    assert "e._domain AS domain" in fetch and params["domain"] == "pj"
    assert clustered == [["Alex", "Alexx"]]


# ── conflicts.candidate_pairs ───────────────────────────────────────────────


def test_candidate_pairs_skip_categories_and_pair_occasions_on_one_day(schema, monkeypatch):
    sources = [{"id": "s1", "name": "Tennis", "entity_class": "ACTIVITY", "domain": "pj",
                "embedding": [0.1], "key": "tennis~2024-03-30|ACTIVITY|pj"}]
    neighbours = [
        {"id": "n1", "name": "Tennis match", "domain": "pj.b", "key": "tennis match~2024-03-30|ACTIVITY|pj.b",
         "score": 0.9},
        {"id": "n2", "name": "Tennis", "domain": "pj.b", "key": "tennis~2024-04-02|ACTIVITY|pj.b", "score": 0.95},
    ]
    rec = Recorder(lambda cypher, params: neighbours if "SEARCH node IN" in cypher else sources)
    monkeypatch.setattr(conflicts, "neo4j_session", lambda: rec)
    pairs = conflicts.candidate_pairs(["pj", "pj.b"], None, sim_threshold=0.5, max_pairs=10)
    assert [p["id_b"] for p in pairs] == ["n1"]
    (fetch, params), = [(c, p) for c, p in rec.calls if "SEARCH node IN" not in c]
    assert "NOT e.entity_class IN coalesce($categories[e._domain], [])" in fetch
    assert params["categories"] == {"pj": ["EMOTION"], "pj.b": ["EMOTION"]}
    (ann, ann_params), = rec.queries_containing("SEARCH node IN")
    assert "NOT node.entity_class IN coalesce($categories[node._domain], [])" in ann
    assert ann_params["categories"] == params["categories"]


def test_detect_conflicts_from_file_counts_identity_refusals(schema, monkeypatch, tmp_path):
    import json

    rec = Recorder()
    monkeypatch.setattr(conflicts, "neo4j_session", lambda: rec)
    item = {"pair": {"id_a": "a", "id_b": "b", "name_a": "Tennis", "name_b": "Tennis match",
                     "key_a": "tennis~2024-03-30|ACTIVITY|pj", "key_b": "tennis match~2024-04-02|ACTIVITY|pj"},
            "verdict": {"verdict": "same_entity_consistent", "aspect": "x"}, "evidence_a": [], "evidence_b": []}
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"conflicts": [item]}))
    report = conflicts.detect_conflicts(["pj"], from_file=path)
    assert report["stats"] == {"materialized": 0, "skipped_identity": 1}
    assert not rec.queries_containing("SameAsProposal")


# ── twins ───────────────────────────────────────────────────────────────────


def test_twins_skip_occasion_and_category_rows():
    rows = [{"entity": {"_id": i, "name": "Tennis", "entity_class": "ACTIVITY"}} for i in ("a", "b")]
    rows += [{"entity": {"_id": i, "name": "Calm", "entity_class": "EMOTION"}} for i in ("e", "f")]
    rows += [{"entity": {"_id": i, "name": "Alex", "entity_class": "PERSON"}} for i in ("c", "d")]
    groups = identity.twin_groups(rows, schema=SCHEMA)
    assert [g["entity_class"] for g in groups] == ["PERSON"]
    assert len(identity.twin_groups(rows)) == 3  # without a schema, nothing is gated


# ── the insights same-as detector ───────────────────────────────────────────


def test_detector_plans_no_category_class():
    ents = [ent("e1", "EMOTION", ["2023-01-01"], name="Calm"), ent("e2", "EMOTION", ["2023-01-02"], name="Calmness"),
            ent("p1", "PERSON", ["2023-01-01"], name="Alex"), ent("p2", "PERSON", ["2023-01-02"], name="Alexx"),
            ent("a1", "ACTIVITY", ["2023-01-01"], name="Tennis"), ent("a2", "ACTIVITY", ["2023-01-02"], name="Tennis")]
    ctx = make_ctx(ents, schema=SCHEMA)
    assert SAME_AS_CANDIDATES.plan(ctx) == ["ACTIVITY", "PERSON"]


def test_detector_pairs_occasions_only_on_one_day():
    ents = [ent("a1", "ACTIVITY", ["2023-01-01"], name="Tennis"),
            ent("a2", "ACTIVITY", ["2023-01-01"], name="Tennis match"),
            ent("a3", "ACTIVITY", ["2023-01-05"], name="Tennis")]
    ctx = make_ctx(ents, schema=SCHEMA, docs_per_period={"2023": 100})
    found = SAME_AS_CANDIDATES.run(ctx, "ACTIVITY")
    assert [sorted(s["_id"] for s in f.subjects) for f in found] == [["a1", "a2"]]
