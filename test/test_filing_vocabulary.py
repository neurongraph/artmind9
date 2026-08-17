"""A5 — controlled filing vocabulary (ADR 0012).

filing_vocabulary() returns distinct project/area/tags/domain values from the
graph, with document counts, so the placement classifier (A6) can propose
labels that already exist rather than inventing new ones.

These tests exercise the query construction and result-shaping via a mocked
_run_read_query — no live Neo4j.
"""
from __future__ import annotations

import artmind.graph_query as gq


def test_vocabulary_reshapes_flat_rows_into_facet_map(monkeypatch):
    """The flat facet/value/count rows Neo4j returns become a {facet: [{value, count}, ...]} dict."""
    fake_rows = [
        {"facet": "area", "value": "engineering", "cnt": 5},
        {"facet": "area", "value": "sales", "cnt": 2},
        {"facet": "domain", "value": "general", "cnt": 7},
        {"facet": "project", "value": "Alpha", "cnt": 4},
        {"facet": "project", "value": "Beta", "cnt": 3},
        {"facet": "tags", "value": "roadmap", "cnt": 6},
        {"facet": "tags", "value": "skeleton", "cnt": 1},
    ]
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: fake_rows)

    result = gq.filing_vocabulary()

    assert result["command"] == "filing_vocabulary"
    assert result["vocabulary"]["project"] == [
        {"value": "Alpha", "count": 4},
        {"value": "Beta", "count": 3},
    ]
    assert result["vocabulary"]["area"] == [
        {"value": "engineering", "count": 5},
        {"value": "sales", "count": 2},
    ]
    assert result["vocabulary"]["tags"] == [
        {"value": "roadmap", "count": 6},
        {"value": "skeleton", "count": 1},
    ]
    assert result["vocabulary"]["domain"] == [{"value": "general", "count": 7}]


def test_vocabulary_returns_empty_facets_when_graph_has_nothing(monkeypatch):
    """An empty graph yields the four facets as empty lists — never absent keys."""
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: [])

    result = gq.filing_vocabulary()

    assert set(result["vocabulary"].keys()) == {"project", "area", "tags", "domain"}
    for facet in ("project", "area", "tags", "domain"):
        assert result["vocabulary"][facet] == []


def test_vocabulary_domain_scope_passes_domains_param(monkeypatch):
    """--domain filtering scopes vocabulary to those domains."""
    captured = {}
    def fake_run(cypher, params):
        captured["cypher"] = cypher
        captured["params"] = params
        return []

    monkeypatch.setattr(gq, "_run_read_query", fake_run)

    gq.filing_vocabulary(domains=["general", "banking"])

    assert captured["params"]["domains"] == ["general", "banking"]
    # The cypher must reference the domain predicate (uses $domains).
    assert "$domains" in captured["cypher"]


def test_vocabulary_min_count_is_clamped_to_at_least_one(monkeypatch):
    """min_count=0 (or negative) would return every rare label incl. NULLs and
    accidentally match every group; clamp to >= 1."""
    captured = {}
    def fake_run(cypher, params):
        captured["params"] = params
        return []

    monkeypatch.setattr(gq, "_run_read_query", fake_run)

    gq.filing_vocabulary(min_count=0)
    assert captured["params"]["min_count"] == 1

    gq.filing_vocabulary(min_count=3)
    assert captured["params"]["min_count"] == 3


def test_vocabulary_unwinds_tags_list_property(monkeypatch):
    """The tags property is a list on the Document node — the cypher must
    UNWIND it so each tag counts individually."""
    captured = {}
    def fake_run(cypher, params):
        captured["cypher"] = cypher
        return []

    monkeypatch.setattr(gq, "_run_read_query", fake_run)

    gq.filing_vocabulary()

    assert "UNWIND d.tags AS tag" in captured["cypher"]


def test_vocabulary_reports_scope_in_output(monkeypatch):
    """The result includes the effective scope for callers/UIs."""
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: [])

    result = gq.filing_vocabulary(domains=["general"], min_count=2)

    assert result["scope"]["domains"] == ["general"]
    assert result["scope"]["min_count"] == 2
