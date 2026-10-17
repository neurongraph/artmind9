from _insights_helpers import ent, make_ctx
from artmind.insights.detectors.health import (
    KIND_MISMATCH, OPEN_CONFLICTS, RELATIONSHIP_USAGE, VOCABULARY_SPRAWL, hint_options,
)


def test_kind_mismatch_flags_a_recurrent_class_seen_once_each():
    acts = [ent(f"a{i}", "ACTIVITY", [f"2024-01-{(i % 28) + 1:02d}"]) for i in range(25)]
    ctx = make_ctx(acts, schema={"entity_types": {"ACTIVITY": {"kind": "recurrent"}}})
    assert KIND_MISMATCH.plan(ctx) == ["all"]
    (f,) = KIND_MISMATCH.run(ctx, "all")
    assert f.key == "kind_mismatch:ACTIVITY" and f.kind == "health" and f.action == "schema"
    assert f.stats["declared"] == "recurrent" and f.stats["effective"] == "occurrent"


def test_hint_options():
    assert hint_options("firm intention, tentative, wishful") == ["firm intention", "tentative", "wishful"]
    assert hint_options("mild, moderate, strong") == ["mild", "moderate", "strong"]
    assert hint_options('e.g., "wife", "son"') == []
    assert hint_options("health, mood, life situation as mentioned") == []
    assert hint_options("why this plan matters") == []
    assert hint_options(None) == []
    assert hint_options("what happened, was it positive/negative/neutral") == []


def test_vocabulary_sprawl_on_hinted_and_type_properties():
    schema = {"entity_types": {"PLAN": {"kind": "recurrent", "type_examples": ["work", "family"], "properties": {
        "status": {"hint": "firm intention, tentative, wishful"},
        "motivation": {"hint": "why this plan matters"},
        "urgency": {},
    }}}}
    props = {"PLAN": {
        "status": {"filled": 100, "distinct": 50, "sample": ["Firm intention", "firm intention", "tentative"]},
        "motivation": {"filled": 100, "distinct": 95, "sample": ["a", "b"]},
        "urgency": {"filled": 10, "distinct": 3, "sample": ["low"]},
        "type": {"filled": 100, "distinct": 8, "sample": ["work", "Work", "family"]},
    }}
    ctx = make_ctx([ent("p1", "PLAN", ["2024-01-01"])], schema=schema, properties=props)

    found = {f.key: f for f in VOCABULARY_SPRAWL.run(ctx, "all")}

    assert set(found) == {"vocabulary_sprawl:PLAN:status", "vocabulary_sprawl:PLAN:type"}
    status = found["vocabulary_sprawl:PLAN:status"]
    assert status.stats["expected_max"] == 15 and status.stats["basis"] == "hint options"
    assert status.stats["near_duplicates"] == [["Firm intention", "firm intention"]]
    assert found["vocabulary_sprawl:PLAN:type"].stats["distinct"] == 8   # within 5x2, flagged for near-duplicates
    assert found["vocabulary_sprawl:PLAN:type"].stats["near_duplicates"] == [["Work", "work"]]


def test_relationship_usage():
    schema = {"entity_types": {"PERSON": {"kind": "recurrent", "relates_to": {"LOCATION": ["went_to", "visits", "lives_at"]}}}}
    rels = [
        {"rel": "WENT_TO", "src": "PERSON", "dst": "LOCATION", "n": 20, "declared": True, "targets": [f"l{i}" for i in range(10)]},
        {"rel": "VISITS", "src": "PERSON", "dst": "LOCATION", "n": 15, "declared": True, "targets": [f"l{i}" for i in range(8)]},
        {"rel": "WORKS_AT", "src": "PERSON", "dst": "PLACE", "n": 6, "declared": False, "targets": ["x"]},
        {"rel": "OWNS", "src": "PERSON", "dst": "OBJECT", "n": 2, "declared": False, "targets": ["o"]},
    ]
    ctx = make_ctx([ent("p", "PERSON", ["2024-01-01"])], schema=schema, relations=rels)

    found = {f.key: f for f in RELATIONSHIP_USAGE.run(ctx, "all")}

    assert set(found) == {
        "relationship_usage:undeclared", "relationship_usage:unused",
        "relationship_usage:overlap:PERSON:VISITS:WENT_TO:LOCATION",
    }
    assert found["relationship_usage:undeclared"].stats["triples"] == [{"rel": "WORKS_AT", "src": "PERSON", "dst": "PLACE", "n": 6}]
    assert found["relationship_usage:unused"].stats["triples"] == [{"rel": "LIVES_AT", "src": "PERSON", "dst": "LOCATION"}]
    assert found["relationship_usage:overlap:PERSON:VISITS:WENT_TO:LOCATION"].stats["overlap"] == 0.8


def test_relationship_overlap_uses_jaccard_not_containment():
    schema = {"entity_types": {"PERSON": {"kind": "recurrent"}}}
    rels = [
        {"rel": "WENT_TO", "src": "PERSON", "dst": "LOCATION", "n": 40, "declared": True, "targets": [f"l{i}" for i in range(30)]},
        {"rel": "COMMUTES_TO", "src": "PERSON", "dst": "LOCATION", "n": 12, "declared": True, "targets": ["l0", "l1", "l2"]},
    ]
    ctx = make_ctx([ent("p", "PERSON", ["2024-01-01"])], schema=schema, relations=rels)

    found = {f.key for f in RELATIONSHIP_USAGE.run(ctx, "all")}

    assert not any(k.startswith("relationship_usage:overlap:") for k in found)


def test_open_conflicts_grouped_by_class():
    ctx = make_ctx([ent("p", "PERSON", ["2024-01-01"])], open_conflicts=[
        {"cls": "PERSON", "property": "occupation", "n": 30}, {"cls": "PERSON", "property": "age", "n": 10},
        {"cls": None, "property": "x", "n": 1},
    ])
    found = {f.key: f for f in OPEN_CONFLICTS.run(ctx, "all")}
    assert found["open_conflicts:PERSON"].stats == {"open": 40, "by_property": {"occupation": 30, "age": 10}, "headline": 40}
    assert found["open_conflicts:PERSON"].action == "curate"
    assert "open_conflicts:UNKNOWN" in found


def test_relationship_usage_tolerates_none_rel():
    rels = [
        {"rel": None, "src": "PERSON", "dst": "LOCATION", "n": 20, "declared": False, "targets": ["a"]},
        {"rel": "VISITS", "src": "PERSON", "dst": "LOCATION", "n": 15, "declared": False, "targets": ["a"]},
        {"rel": "WENT_TO", "src": "PERSON", "dst": "LOCATION", "n": 15, "declared": False, "targets": ["a"]},
    ]
    ctx = make_ctx([ent("p", "PERSON", ["2024-01-01"])], relations=rels)
    found = RELATIONSHIP_USAGE.run(ctx, "all")
    assert all("None" not in f.key for f in found)


from artmind.insights.detectors.health import ATTRIBUTION_SUSPECTS, FRAGMENTS


def _rel(i, cls, type):
    return {"rel": "WORKS_AT", "_id": f"t{i}", "name": f"T{i}", "cls": cls, "type": type, "days": ("2024-01-01",)}


def test_attribution_suspects_flags_rare_target_types_on_focused_relations():
    rels = [_rel(i, "ORGANIZATION", "employer") for i in range(15)]
    rels += [_rel(15 + i, "LOCATION", "workplace") for i in range(10)]
    rels += [_rel(30 + i, "PERSON", "boss") for i in range(4)]   # fills the top 3 with school 4th
    rels += [_rel(40, "ORGANIZATION", "school")]
    rels += [{"rel": "VISITS", "_id": f"v{i}", "name": f"V{i}", "cls": "LOCATION", "type": f"kind{i}", "days": ("2024-01-01",)}
             for i in range(30)]   # unfocused relation: no suspects
    ctx = make_ctx([ent("me", "PERSON", ["2024-01-01"])], anchors={"me"}, anchor_relations=rels)
    assert ATTRIBUTION_SUSPECTS.plan(ctx) == ["anchor"]
    (f,) = ATTRIBUTION_SUSPECTS.run(ctx, "anchor")
    assert f.key == "attribution_suspects:WORKS_AT" and f.action == "reextract" and f.kind == "health"
    assert [s["_id"] for s in f.stats["suspects"]] == ["t40"]
    assert f.stats["typical"][0] == {"class": "ORGANIZATION", "type": "employer", "count": 15}


def test_attribution_suspects_ignores_combos_in_the_top_three():
    rels = [_rel(i, "ORGANIZATION", "employer") for i in range(87)]
    rels += [_rel(100 + i, "LOCATION", "workplace") for i in range(10)]
    rels += [_rel(200 + i, "ORGANIZATION", "school") for i in range(3)]
    ctx = make_ctx([ent("me", "PERSON", ["2024-01-01"])], anchors={"me"}, anchor_relations=rels)
    assert ATTRIBUTION_SUSPECTS.run(ctx, "anchor") == []


def test_fragments_reports_small_cut_off_groups():
    main = [("m0", f"m{i}") for i in range(1, 8)]
    islands = [("i1", "i2"), ("j1", "j2"), ("j2", "j3")]
    ents = [ent(x, "PERSON", ["2024-01-01"]) for x in {n for e in main + islands for n in e}]
    ctx = make_ctx(ents, entity_edges=sorted(main + islands))
    (f,) = FRAGMENTS.run(ctx, "all")
    assert f.key == "fragments:all" and f.stats["count"] == 1 and f.stats["main_component"] == 8
    assert f.stats["sizes"] == {"3": 1}
    assert FRAGMENTS.plan(make_ctx(ents)).reason == "no entity relations"
