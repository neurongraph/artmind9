import pytest

from _insights_helpers import FakeProjector, ent
from artmind.insights.config import parse_config
from artmind.insights.model import InsightsError
from artmind.insights.profile import (
    build_profile, declared_triples, effective_kind, infer_anchors, summarise_class,
)

SCHEMA = {
    "entity_types": {
        "PERSON": {"kind": "recurrent", "relates_to": {
            "LOCATION": ["went_to", "visits"],
            "EVENT": ["participates_in (properties: {role: organiser|participant})"],
        }},
        "EVENT": {"kind": "occurrent"},
    }
}


def _tables():
    return {
        "entity_evidence": [
            {"_id": "p1", "name": "Asha", "cls": "PERSON", "type": "self", "days": ["2023-01-02", "2023-01-01"], "docs": 2},
            {"_id": "e1", "name": "Party", "cls": "EVENT", "type": "Celebration", "days": ["2023-01-02"], "docs": 1},
            {"_id": "x1", "name": "Undated", "cls": "EVENT", "type": None, "days": [None], "docs": 1},
        ],
        "documents": [
            {"id": "d1", "valid_from": "2023-01-01", "time_source": "document_label"},
            {"id": "d2", "valid_from": "2023-01-02", "time_source": "document_label"},
            {"id": "d3", "valid_from": "2024-03-01", "time_source": "default_ingestion"},
        ],
        "property_stats": [
            {"cls": "PERSON", "property": "relationship_to_diarist", "filled": 1, "distinct_values": 1, "sample": ["self"]},
        ],
        "relation_triples": [
            {"rel": "WENT_TO", "src": "PERSON", "dst": "LOCATION", "n": 4, "targets": ["l1"]},
            {"rel": "OWNS", "src": "PERSON", "dst": "OBJECT", "n": 2, "targets": ["o1"]},
        ],
        "open_conflicts": [{"cls": "PERSON", "property": "occupation", "n": 3}],
    }


def test_build_profile_measures_classes_documents_and_relations():
    proj = FakeProjector(_tables())
    profile, entities = build_profile(proj, SCHEMA, parse_config({}, "demo"))

    assert [e._id for e in entities] == ["e1", "p1", "x1"]
    assert entities[1].days == ("2023-01-01", "2023-01-02")
    assert entities[2].days == ()
    assert profile.classes["PERSON"].declared_kind == "recurrent"
    assert profile.classes["EVENT"].type_counts == {"celebration": 1}
    assert profile.classes["PERSON"].properties["relationship_to_diarist"]["filled_rate"] == 1.0
    assert profile.docs_per_period == {"2023": 2}
    assert profile.default_time_share == pytest.approx(1 / 3, abs=1e-4)
    assert profile.latest_doc == "2023-01-02" and profile.dated_days == 2
    assert [(r["rel"], r["declared"]) for r in profile.relations] == [("WENT_TO", True), ("OWNS", False)]
    assert profile.open_conflicts == [{"cls": "PERSON", "property": "occupation", "n": 3}]
    assert ("relation_triples", {"max_targets": 2000}) in proj.calls
    d = profile.to_dict()
    assert "targets" not in d["relations"][0]
    assert profile.documents == 3 and d["documents"] == 3


def test_declared_triples_parse_verbs_with_annotations():
    assert declared_triples(SCHEMA) == {
        ("WENT_TO", "PERSON", "LOCATION"), ("VISITS", "PERSON", "LOCATION"), ("PARTICIPATES_IN", "PERSON", "EVENT"),
    }


def test_declared_triples_sanitise_tokens_like_ingestion():
    schema = {"entity_types": {"PERSON": {"relates_to": {"LOCATION": ["works-with"]}}}}
    assert declared_triples(schema) == {("WORKS_WITH", "PERSON", "LOCATION")}


def test_declared_triples_skip_bare_string_verbs():
    schema = {"entity_types": {"PERSON": {"relates_to": {"LOCATION": "went_to"}}}}
    assert declared_triples(schema) == set()


@pytest.mark.parametrize("declared, median, single, n, expected", [
    ("recurrent", 1, 0.9, 50, "occurrent"),
    ("recurrent", 1, 0.9, 5, "recurrent"),
    ("recurrent", 2, 0.5, 50, "recurrent"),
    ("occurrent", 4, 0.1, 50, "recurrent"),
    ("occurrent", 1, 0.95, 50, "occurrent"),
])
def test_effective_kind(declared, median, single, n, expected):
    assert effective_kind(declared, median, single, n) == expected


def test_summarise_class_falls_back_to_documents_when_undated():
    cp = summarise_class("NOTE", "recurrent", [ent("n1", "NOTE", [])], None)
    assert cp.entities == 1 and cp.median_days == 0.0


def test_undated_entities_do_not_flip_declared_kind():
    rows = [ent(f"n{i}", "NOTE", []) for i in range(30)]
    cp = summarise_class("NOTE", "recurrent", rows, None)
    assert cp.entities == 30
    assert cp.median_days == 0.0 and cp.single_day_share == 0.0
    assert cp.effective_kind == "recurrent"


def test_entity_evidence_drops_invalid_calendar_days():
    proj = FakeProjector({"entity_evidence": [
        {"_id": "e1", "name": "Odd", "cls": "EVENT", "type": None, "days": ["2024-13-45", "2024-01-05"], "docs": 2},
    ]})
    _, entities = build_profile(proj, SCHEMA, parse_config({}, "demo"))
    assert entities[0].days == ("2024-01-05",)


def test_anchor_inference_rules():
    asha = ent("p1", "PERSON", [f"2024-01-{d:02d}" for d in range(1, 11)], name="Asha", type="self")
    ben = ent("p2", "PERSON", ["2024-01-01"], name="Ben")
    cfg = parse_config({}, "demo")
    assert infer_anchors([asha, ben], cfg, 10) == [{"_id": "p1", "name": "Asha", "rule": "type=self"}]

    asha_untyped = ent("p1", "PERSON", asha.days, name="Asha")
    assert infer_anchors([asha_untyped, ben], cfg, 10) == [{"_id": "p1", "name": "Asha", "rule": "coverage"}]
    assert infer_anchors([ben], cfg, 0) == []

    both_days = ent("p3", "PERSON", ["2024-01-01", "2024-01-02"], name="Cleo")
    assert infer_anchors([both_days], cfg, 2) == []

    assert infer_anchors([asha, ben], parse_config({"anchors": ["ben"]}, "demo"), 10) == [
        {"_id": "p2", "name": "Ben", "rule": "config"}]
    assert infer_anchors([asha, ben], parse_config({"anchors": ["@p1"]}, "demo"), 10)[0]["_id"] == "p1"
    with pytest.raises(InsightsError, match="matched 0"):
        infer_anchors([asha, ben], parse_config({"anchors": ["Cleo"]}, "demo"), 10)


def test_relation_rows_without_a_rel_are_skipped():
    tables = _tables()
    tables["relation_triples"] = tables["relation_triples"] + [{"rel": None, "src": "A", "dst": "B", "n": 3}]
    profile, _ = build_profile(FakeProjector(tables), SCHEMA, parse_config({}, "demo"))
    assert all(r["rel"] is not None for r in profile.relations)


def test_profile_components_and_edges():
    tables = _tables()
    tables["entity_edges"] = [{"a": "p1", "b": "e1"}, {"a": "e1", "b": "p1"}, {"a": "x1", "b": "x1"}]
    profile, _ = build_profile(FakeProjector(tables), SCHEMA, parse_config({}, "demo"))
    assert profile.entity_edges == [("e1", "p1")]
    assert profile.components == {"count": 2, "largest": 2, "isolated": 1}
    assert "entity_edges" not in profile.to_dict() and profile.to_dict()["components"]["count"] == 2
