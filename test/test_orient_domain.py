"""Peel 2: the per-domain block (spec 2026-11-09 §4, §4.1)."""
import pytest

from artmind.catalogue.model import DomainCatalogue
from artmind.orient import domain as block


def _cat(**kw):
    base = dict(
        domain="journal", fingerprint=(5, 8, 3, 0, ""), built_at="t",
        classes=(("EVENT", 6, "x"), ("PERSON", 2, "y")),
        shapes=(("PERSON", "VISITED", "PLACE", 7),),
        types=(("EVENT", {"total": 6, "distinct": 3, "dated": 5, "top": [["visit", 3], ["party", 2]]}),
               ("PERSON", {"total": 2, "distinct": 0, "dated": 0, "top": []})),
        versions=(),
        stores={"graph_extraction": True, "kind": "graph", "structured_tables": 0},
        assets=({"kind": "topic_plan", "id": "t", "title": "Lakeport visits", "detail": {"subjects": ["s1"]}},
                {"kind": "insight", "id": "dormancy", "title": "dormancy", "detail": {"as_of": "2026-11-07"}}),
        levels={"n": 6, "anchor_entities": 2, "focused_entities": 6, "passages": 30, "document_summaries": 3,
                "chunk_summaries": 9},
        part_keys={p: [5] for p in ("classes", "shapes", "types", "versions", "stores", "assets", "levels")},
    )
    base.update(kw)
    return DomainCatalogue(**base)


FACTS = {"purpose": "p", "time_rule": {}, "filename_pattern": None,
         "classes": {"EVENT": {"kind": "occurrent", "definition": "Any occurrence."},
                     "UNUSED": {"kind": "x", "definition": "never extracted"}}}


@pytest.fixture
def no_views(monkeypatch):
    from artmind.kg_views import store

    monkeypatch.setattr(store, "list_views", lambda domains: ([], []))


def test_classes_merge_schema_and_measured_types(no_views):
    out = block.build_block(_cat(), FACTS, card={"domain": "journal"}, warnings=[])
    assert out["classes"] == [
        {"class": "EVENT", "count": 6, "kind": "occurrent", "definition": "Any occurrence.",
         "types": {"top": [["visit", 3], ["party", 2]], "distinct": 3}, "dated": 0.83},
        {"class": "PERSON", "count": 2, "kind": None, "definition": None,
         "types": {"top": [], "distinct": 0}, "dated": 0.0},
    ]  # UNUSED (no entities) omitted; PERSON (no schema entry) has nulls


def test_relations_topic_plans_insights_and_no_structured_key(no_views):
    out = block.build_block(_cat(), FACTS, card={}, warnings=[])
    assert out["relations"] == [{"from": "PERSON", "rel": "VISITED", "to": "PLACE", "count": 7}]
    assert out["levels"] == {"n": 6, "focused_entities": 6, "anchor_entities": 2, "views": 0, "passages": 30,
                             "document_summaries": 3, "chunk_summaries": 9,
                             "topic_plans": [{"slug": "t", "topic": "Lakeport visits", "subjects": ["s1"],
                                              "freshness": None}]}
    assert out["insights"] == {"as_of": "2026-11-07", "detectors": ["dormancy"]}
    assert "structured" not in out and out["documents"] == [] and out["views"] == []


def test_views_carry_summary_params_and_two_examples(monkeypatch):
    from artmind.kg_views import store

    class P:
        type, required, entity_class = "date", True, None

    class Spec:
        name, summary, examples, params = "in_range", "Meetings in a range", ("a", "b", "c"), {"start": P()}

    class Loaded:
        spec = Spec()

    monkeypatch.setattr(store, "list_views", lambda domains: ([Loaded()], [{"name": "bad", "error": "x"}]))
    warnings: list = []
    out = block.build_block(_cat(), FACTS, card={}, warnings=warnings)
    assert out["views"] == [{"name": "in_range", "summary": "Meetings in a range",
                             "params": {"start": {"type": "date", "required": True}}, "examples": ["a", "b"]}]
    assert any("1 invalid view" in w for w in warnings)


def test_structured_tables_from_the_registry(no_views, monkeypatch):
    from artmind.structured import registry

    monkeypatch.setattr(registry, "routing_surface", lambda domains, entity_class=None: [
        {"table": "complaints", "grain": "instance", "row_count": 20,
         "entity_classes": [{"entity_class": "ACCOUNT"}, {"entity_class": "CARD"}]}])
    cat = _cat(stores={"graph_extraction": True, "kind": "graph", "structured_tables": 1})
    out = block.build_block(cat, FACTS, card={}, warnings=[])
    assert out["structured"] == [{"table": "complaints", "grain": "instance", "rows": 20,
                                  "bridge_classes": ["ACCOUNT", "CARD"]}]


def test_unread_parts_and_failures_are_warnings(monkeypatch):
    from artmind.kg_views import store

    def boom(domains):
        raise OSError("disk")

    monkeypatch.setattr(store, "list_views", boom)
    cat = _cat(part_keys={"classes": [5], "stores": [5]})
    warnings: list = []
    out = block.build_block(cat, None, card={}, warnings=warnings)
    assert "views" not in out and "relations" not in out and "documents" not in out
    assert out["classes"][0]["definition"] is None and "types" not in out["classes"][0]
    assert any("journal: views unreadable" in w for w in warnings)
    assert any("journal: shapes not read" in w for w in warnings)


def test_a_class_missing_from_types_has_no_invented_types(no_views):
    cat = _cat(types=(("EVENT", {"total": 6, "distinct": 3, "dated": 5, "top": []}),))
    person = next(c for c in block.build_block(cat, FACTS, card={}, warnings=[])["classes"]
                  if c["class"] == "PERSON")
    assert "types" not in person and "dated" not in person


def test_topic_plans_skip_untitled_and_invalid(no_views):
    cat = _cat(assets=({"kind": "topic_plan", "id": "a", "title": None, "detail": {}},
                       {"kind": "topic_plan", "id": "x", "title": "Broken", "status": "invalid", "detail": {}},
                       {"kind": "topic_plan", "id": "b", "title": "Kept", "detail": {}}))
    assert block.build_block(cat, FACTS, card={}, warnings=[])["levels"]["topic_plans"] == [
        {"slug": "b", "topic": "Kept", "subjects": [], "freshness": None}]
