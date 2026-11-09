"""Catalogue parts orient reads (spec 2026-11-09 §4, §4.1, §5.3): shapes, types, versions, time."""
import json

import pytest

from artmind import graph_query
from artmind.catalogue import cache, parts
from artmind.catalogue.model import CATALOGUE_FORMAT, DomainCatalogue


class Graph:
    def __init__(self):
        self.calls = []

    def __call__(self, cypher, params):
        self.calls.append((cypher, params))
        if "AS src" in cypher:
            return [{"src": "PERSON", "rel": "VISITED", "dst": "PLACE", "n": 7},
                    {"src": "PERSON", "rel": "KNOWS", "dst": "PERSON", "n": 3}]
        if "AS types" in cypher:
            return [{"cls": "EVENT", "total": 6, "dated": 5,
                     "types": [{"type": "visit", "n": 3}, {"type": "party", "n": 2}, {"type": "x", "n": 1}]},
                    {"cls": "PERSON", "total": 2, "dated": 0, "types": [{"type": None, "n": 2}]}]
        if "AS successor" in cypher:
            return [{"name": "policy_a.md", "status": "retired", "valid_from": "2026-01-01",
                     "successor": "policy_a_v2.md", "replaces": 0},
                    {"name": "policy_a_v2.md", "status": "current", "valid_from": "2026-06-01",
                     "successor": None, "replaces": 1}]
        if "from_content" in cypher:
            return [{"documents": 4, "with_valid_from": 4, "from_content": 3,
                     "earliest": "2025-01-01", "latest": "2026-03-01",
                     "content_earliest": "2026-01-01", "content_latest": "2026-03-01"}]
        raise AssertionError(cypher)


@pytest.fixture
def graph(monkeypatch):
    g = Graph()
    monkeypatch.setattr(graph_query, "_run_read_query", g)
    return g


def test_shapes_are_class_rel_class_with_counts(graph):
    out = parts.build_shapes("journal")
    assert out == {"shapes": (("PERSON", "VISITED", "PLACE", 7), ("PERSON", "KNOWS", "PERSON", 3))}
    cypher, params = graph.calls[0]
    assert params == {"domains": ["journal"], "limit": parts.SHAPES_MAX}
    assert "a.entity_class" in cypher and "b.entity_class" in cypher and "_domain IN $domains" in cypher


def test_types_top_values_distinct_and_dated_share(graph):
    out = parts.build_types("journal")["types"]
    event = dict(out)["EVENT"]
    assert event == {"total": 6, "distinct": 3, "dated": 5, "top": [["visit", 3], ["party", 2]]}
    person = dict(out)["PERSON"]
    assert person == {"total": 2, "distinct": 0, "dated": 0, "top": []}  # a null type is no value
    cypher, _ = graph.calls[0]
    assert "toLower(trim(toString(e.type)))" in cypher and "_valid_time_source = 'property'" in cypher


def test_types_keep_only_values_on_two_or_more_entities_and_cap(graph, monkeypatch):
    monkeypatch.setattr(parts, "TYPES_TOP", 1)
    event = dict(parts.build_types("journal")["types"])["EVENT"]
    assert event["top"] == [["visit", 3]] and event["distinct"] == 3


def test_versions_list_retired_and_replacing_documents(graph):
    out = parts.build_versions("bank.policy")["versions"]
    assert out == (
        {"name": "policy_a.md", "status": "retired", "valid_from": "2026-01-01", "successor": "policy_a_v2.md"},
        {"name": "policy_a_v2.md", "status": "current", "valid_from": "2026-06-01", "successor": None,
         "replaces": 1},
    )
    cypher, _ = graph.calls[0]
    assert "DocumentHistory" in cypher and "SUPERSEDES" in cypher


def test_time_counts_content_dated_documents_apart(graph):
    t = parts.build_time("journal")["time"]
    assert t["documents"] == 4 and t["with_valid_from"] == 4 and t["share"] == 1.0  # unchanged meaning
    assert t["from_content"] == 3
    assert (t["content_earliest"], t["content_latest"]) == ("2026-01-01", "2026-03-01")
    assert "default_ingestion" in graph.calls[0][0]


def test_parts_are_registered_keyed_on_the_graph_and_round_trip(graph):
    assert {"shapes", "types", "versions"} <= set(parts.BUILDERS)
    fp = (5, 3, 2, 1, "s:1|v:2")
    for p in ("shapes", "types", "versions"):
        assert parts.part_key(p, fp) == [5, 3, 2, 1]
    cat = cache.build("journal", fp, ("shapes", "types", "versions", "time"))
    again = DomainCatalogue.from_json(json.loads(json.dumps(cat.to_json())))
    assert again == cat and CATALOGUE_FORMAT == 4


def test_report_marks_new_parts_as_graph_parts():
    from artmind.catalogue import report

    assert {"shapes", "types", "versions"} <= report.GRAPH_PARTS
