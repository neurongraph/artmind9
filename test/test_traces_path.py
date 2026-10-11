"""artmind.traces.path: layers, fall-through, answer basis (spec §4.1)."""

import pytest

from artmind.traces.path import answer_basis, derive, layer_of, parse_sources


def _span(command, rows=1, exit_code=0, started_at=0.0, documents=None):
    ev = {"documents": [{"doc_id": None, "path": p} for p in documents]} if documents else {}
    return {"span_id": f"{command}-{started_at}", "command": command, "rows": rows,
            "exit_code": exit_code, "started_at": started_at, "evidence": ev}


@pytest.mark.parametrize("command,layer", [
    ("query views run", "view"),
    ("query graph pattern5", "graph"),
    ("query graph pattern10", "graph"),
    ("query graph hierarchy", "graph"),
    ("query entity-context", "graph"),
    ("query graph text2cypher", "text2cypher"),
    ("query vector-text", "vector"),
    ("query chunks", "vector"),
    ("query text2sql", "sql"),
    ("query resolve-key", "sql"),
    ("query entity-resolve", "discovery"),
    ("query graph structural-metadata", "discovery"),
    ("query views list", "discovery"),
    ("query something-new", "discovery"),
    (None, "discovery"),
])
def test_layer_of(command, layer):
    assert layer_of(command) == layer


def test_parse_sources_angle_and_plain_links():
    answer = (
        "Alice leads it.\n\n### Sources\n"
        "- [Delivery Leads.md](<performance_management/Delivery Leads.md>) — performance_management\n"
        "- [Org.md](banking/Org.md) — banking\n\n## Other\n- [x](not/a/source.md)\n"
    )
    assert parse_sources(answer) == ["performance_management/Delivery Leads.md", "banking/Org.md"]
    assert parse_sources("no sources here") == []
    assert parse_sources(None) == []


def test_pattern_empty_then_vector_is_a_fall_through():
    spans = [_span("query entity-resolve", started_at=1), _span("query graph pattern2", rows=0, started_at=2),
             _span("query vector-text", rows=3, started_at=3, documents=["b/P.md"])]
    d = derive(spans, "x\n### Sources\n- [P](<b/P.md>) — b\n")
    assert [p["layer"] for p in d["retrieval_path"]] == ["graph", "vector"]
    assert d["graph_attempts"] == 1
    assert d["graph_productive"] == 0
    assert d["fell_through"] is True
    assert d["answer_basis"] == "vector"


def test_productive_view_without_vector_is_graph():
    d = derive([_span("query views run", rows=4, started_at=1)], "answer without sources")
    assert d["fell_through"] is False
    assert d["answer_basis"] == "graph"


def test_entity_context_then_chunks_is_mixed_when_both_cited():
    spans = [_span("query entity-context", rows=1, started_at=1, documents=["b/A.md"]),
             _span("query chunks", rows=2, started_at=2, documents=["b/B.md"])]
    answer = "### Sources\n- [A](<b/A.md>) — b\n- [B](<b/B.md>) — b\n"
    d = derive(spans, answer)
    assert d["fell_through"] is True
    assert d["answer_basis"] == "mixed"


def test_text2cypher_error_then_vector():
    spans = [_span("query graph text2cypher", rows=0, exit_code=1, started_at=1),
             _span("query vector-text", rows=2, started_at=2)]
    d = derive(spans, "no sources")
    assert d["fell_through"] is True
    assert d["graph_productive"] == 0
    assert d["answer_basis"] == "vector"


def test_discovery_only_is_none():
    d = derive([_span("query domains-overview"), _span("query entity-resolve")], "dunno")
    assert d["retrieval_path"] == []
    assert d["graph_attempts"] == 0
    assert d["fell_through"] is False
    assert d["answer_basis"] == "none"


def test_sql_only():
    d = derive([_span("query text2sql", rows=5, documents=["tables/loans.csv"])],
               "### Sources\n- [loans](<tables/loans.csv>) — b\n")
    assert d["answer_basis"] == "sql"


def test_vector_before_graph_is_not_a_fall_through():
    spans = [_span("query vector-text", started_at=1), _span("query graph pattern1", started_at=2)]
    assert derive(spans, "")["fell_through"] is False


def test_cited_doc_matches_by_suffix_and_basename():
    spans = [_span("query graph pattern2", rows=1, documents=["/abs/vault/b/Org.md"]),
             _span("query vector-text", rows=1, started_at=1, documents=["b/Other.md"])]
    assert answer_basis("### Sources\n- [Org](<b/Org.md>) — b\n", spans) == "graph"
