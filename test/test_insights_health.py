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
    assert found["relationship_usage:overlap:PERSON:VISITS:WENT_TO:LOCATION"].stats["overlap"] == 1.0


def test_open_conflicts_grouped_by_class():
    ctx = make_ctx([ent("p", "PERSON", ["2024-01-01"])], open_conflicts=[
        {"cls": "PERSON", "property": "occupation", "n": 30}, {"cls": "PERSON", "property": "age", "n": 10},
        {"cls": None, "property": "x", "n": 1},
    ])
    found = {f.key: f for f in OPEN_CONFLICTS.run(ctx, "all")}
    assert found["open_conflicts:PERSON"].stats == {"open": 40, "by_property": {"occupation": 30, "age": 10}, "headline": 40}
    assert found["open_conflicts:PERSON"].action == "curate"
    assert "open_conflicts:UNKNOWN" in found
