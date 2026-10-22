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
    ("query chunks", "provenance"),
    ("query text2sql", "sql"),
    ("query resolve-key", "sql"),
    ("query entity-resolve", "discovery"),
    ("query graph structural-metadata", "discovery"),
    ("query views list", "discovery"),
    ("query views show", "discovery"),
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


def test_entity_context_then_vector_text_is_mixed_when_both_cited():
    """Rewrite of the old chunks-mixed test: use vector-text for the vector side."""
    spans = [_span("query entity-context", rows=1, started_at=1, documents=["b/A.md"]),
             _span("query vector-text", rows=2, started_at=2, documents=["b/B.md"])]
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


def test_cited_doc_matches_by_path_suffix():
    spans = [_span("query graph pattern2", rows=1, documents=["/abs/vault/b/Org.md"]),
             _span("query vector-text", rows=1, started_at=1, documents=["b/Other.md"])]
    assert answer_basis("### Sources\n- [Org](<b/Org.md>) — b\n", spans) == "graph"


def test_same_basename_in_another_folder_does_not_match():
    spans = [_span("query graph pattern2", rows=1, documents=["a/README.md"]),
             _span("query vector-text", rows=1, started_at=1, documents=["b/README.md"])]
    assert answer_basis("### Sources\n- [README](<b/README.md>) — b\n", spans) == "vector"


# ── New tests for the updated heuristics ─────────────────────────────────────

def test_live_shape_graph_provenance_not_fall_through():
    """Live admin-console shape: graph answered, chunks fetched for provenance,
    vector-text ran as a cross-check with OTHER documents → not a fall-through."""
    spans = [
        _span("query domains-overview", rows=1, started_at=1),
        _span("query views list", rows=1, started_at=2),
        _span("query entity-resolve", rows=5, started_at=3),
        _span("query entity-resolve", rows=5, started_at=3.1),
        _span("query graph entity-listing", rows=7, started_at=4),      # graph attempt 1, productive
        _span("query entity-resolve", rows=3, started_at=5),
        _span("query graph pattern6", rows=2, started_at=6),             # graph attempt 2, productive
        _span("query graph text2cypher", rows=0, exit_code=1, started_at=7),  # graph attempt 3, not productive
        _span("query chunks", rows=0, started_at=8),                     # provenance, not productive
        _span("query chunks", rows=11, started_at=9,
              documents=["journal/2025-05-19.md",
                         "journal/2025-06-01.md",
                         "journal/2025-07-15.md"]),                      # provenance, productive
        _span("query graph entity-listing", rows=4, started_at=10),     # graph attempt 4, productive
        _span("query vector-text", rows=8, started_at=11,
              documents=["other/2024-01-01.md", "other/2024-02-01.md"]),  # vector cross-check (not cited)
    ]
    answer = (
        "The information shows the following.\n\n### Sources\n"
        "- [Entry A](journal/2025-05-19.md)\n"
        "- [Entry B](journal/2025-06-01.md)\n"
        "- [Entry C](journal/2025-07-15.md)\n"
    )
    d = derive(spans, answer)
    assert d["fell_through"] is False
    assert d["answer_basis"] == "graph"
    assert d["graph_attempts"] == 4
    assert d["graph_productive"] == 3


def test_graph_miss_empty_graph_vector_cited():
    """Graph returned nothing; vector-text result is cited → fell_through True, basis vector."""
    spans = [
        _span("query graph pattern2", rows=0, started_at=1),
        _span("query vector-text", rows=3, started_at=2,
              documents=["b/Report.md"]),
    ]
    answer = "### Sources\n- [Report](b/Report.md)\n"
    d = derive(spans, answer)
    assert d["fell_through"] is True
    assert d["answer_basis"] == "vector"
    assert d["graph_productive"] == 0


def test_doc_returned_by_graph_and_vector_counts_as_graph():
    """A vector-text cross-check that re-finds a document the graph side already
    returned adds nothing: the citation is the graph's, not a fall-through."""
    spans = [
        _span("query graph pattern2", rows=1, started_at=1),
        _span("query chunks", rows=1, started_at=2, documents=["b/A.md"]),          # provenance
        _span("query vector-text", rows=2, started_at=3, documents=["b/A.md"]),     # vector also has it
    ]
    d = derive(spans, "### Sources\n- [A](b/A.md)\n")
    assert d["answer_basis"] == "graph"
    assert d["fell_through"] is False


def test_vector_only_citation_alongside_graph_citation_is_mixed_fell_through():
    spans = [
        _span("query graph pattern2", rows=1, started_at=1),
        _span("query chunks", rows=1, started_at=2, documents=["b/A.md"]),
        _span("query vector-text", rows=2, started_at=3, documents=["b/A.md", "b/C.md"]),
    ]
    d = derive(spans, "### Sources\n- [A](b/A.md)\n- [C](b/C.md)\n")
    assert d["answer_basis"] == "mixed"
    assert d["fell_through"] is True


def test_no_sources_empty_graph_vector_productive_fell_through_via_rule_b():
    """No Sources section; graph returned nothing; vector productive → fell_through via rule (b)."""
    spans = [
        _span("query graph pattern1", rows=0, started_at=1),
        _span("query vector-text", rows=5, started_at=2),
    ]
    d = derive(spans, "The answer is here.")
    assert d["fell_through"] is True
    assert d["graph_productive"] == 0


def test_no_sources_graph_productive_vector_productive_not_fell_through():
    """No Sources section; graph returned rows; vector also ran → NOT a fall-through (rule b requires graph_productive==0)."""
    spans = [
        _span("query graph pattern1", rows=3, started_at=1),
        _span("query vector-text", rows=5, started_at=2),
    ]
    d = derive(spans, "The answer is here.")
    assert d["fell_through"] is False
    assert d["graph_productive"] == 1


def test_chunks_provenance_included_in_retrieval_path():
    """query chunks spans appear in retrieval_path with layer 'provenance', not 'vector'."""
    spans = [
        _span("query graph pattern3", rows=2, started_at=1),
        _span("query chunks", rows=5, started_at=2, documents=["b/Doc.md"]),
    ]
    d = derive(spans, "")
    layers = [p["layer"] for p in d["retrieval_path"]]
    assert layers == ["graph", "provenance"]
    assert d["fell_through"] is False  # no vector-text at all


def _dry_span(started_at=1.0):
    return {"span_id": "d1", "command": "query graph text2cypher", "args": {"domain": ["b"], "dry_run": True},
            "exit_code": 0, "rows": 0, "started_at": started_at, "evidence": {}}


def test_a_dry_run_is_discovery_not_a_graph_attempt():
    from artmind.traces import path

    derived = path.derive([_dry_span()], answer=None)
    assert derived["graph_attempts"] == 0
    assert derived["retrieval_path"] == []
    assert path.span_layer(_dry_span()) == "discovery"


def test_an_executed_text2cypher_still_counts():
    from artmind.traces import path

    span = {**_dry_span(), "args": {"domain": ["b"], "dry_run": False}}
    assert path.span_layer(span) == "text2cypher"
    assert path.derive([span], answer=None)["graph_attempts"] == 1


def test_a_dry_run_then_vector_text_is_not_a_fall_through():
    from artmind.traces import path

    dry = _dry_span(started_at=1.0)
    vector = _span("query vector-text", rows=3, started_at=2.0)
    derived = path.derive([dry, vector], answer=None)
    assert derived["graph_attempts"] == 0
    assert derived["fell_through"] is False


def test_a_text2sql_dry_run_is_discovery():
    from artmind.traces import path

    span = {"span_id": "s1", "command": "query text2sql", "args": {"dry_run": True},
            "exit_code": 0, "rows": 0, "started_at": 1.0, "evidence": {}}
    assert path.span_layer(span) == "discovery"


def test_a_plan_span_is_its_own_layer_and_an_executed_one_is_a_view():
    from artmind.traces import path

    plan_span = {"command": "query plan", "args": {"domain": ["b"]}, "exit_code": 0, "rows": 0,
                 "started_at": 1.0, "evidence": {}}
    assert path.span_layer(plan_span) == "plan"
    assert path.derive([plan_span], answer=None)["graph_attempts"] == 0
    view = {"name": "org_tree", "version": 2, "status": "ok", "params": {}}
    executed = {**plan_span, "rows": 2, "evidence": {"view": view}}
    assert path.span_layer(executed) == "view"
    assert path.derive([executed], answer=None)["graph_attempts"] == 1
    empty = {**plan_span, "rows": 0, "evidence": {"view": view}}
    assert path.span_layer(empty) == "view"
    assert path.derive([empty], answer=None)["graph_attempts"] == 1
    # rows without a view run (candidates only) do not make a plan a view run
    assert path.span_layer({**plan_span, "rows": 2}) == "plan"
