"""Domain predicate + normalization unit tests (no Neo4j)."""
from artmind.graph_query import normalize_domains, domain_predicate


def test_normalize_single_string():
    assert normalize_domains("banking_policy") == ["banking_policy"]


def test_normalize_comma_split_and_strip():
    assert normalize_domains("a, b ,c") == ["a", "b", "c"]


def test_normalize_sequence_flattens_and_dedupes():
    assert normalize_domains(["a,b", "b", "c"]) == ["a", "b", "c"]


def test_normalize_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        normalize_domains("")


def test_domain_predicate_shape():
    pred = domain_predicate("e")
    assert pred == (
        "(e._domain IN $domains OR any(dom IN $domains WHERE e._domain STARTS WITH (dom + '.')))"
    )


def test_domain_predicate_custom_var_and_param():
    pred = domain_predicate("node", param="doms")
    assert "node._domain IN $doms" in pred


def test_pattern1_cypher_uses_in_domains():
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON", "limit": 10}
    )
    # Entity carries `_domain` (Phase 4's `_`-prefix), not `domain`.
    assert "e._domain IN $domains" in cypher
    assert params["domains"] == ["fiction"]
    assert "= $domain" not in cypher


def test_domain_predicate_rolls_up_subdomains_semantics():
    # A one-element list "banking" must match "banking.policy" via STARTS WITH.
    pred = domain_predicate("e")
    assert "STARTS WITH (dom + '.')" in pred


def test_domain_predicate_var_named_d_has_no_shadowing_collision():
    # structural_metadata() calls domain_predicate("d") for its Document arm
    # (MATCH (d:Document) WHERE domain_predicate("d")). If the comprehension's
    # loop variable were also named "d", it would shadow the outer node inside
    # the WHERE clause, turning "d.domain" into a property access on a string
    # and crashing Neo4j with "Invalid input 'STRING' for `d`". Guard against
    # that regression by requiring the loop variable to differ from var.
    pred = domain_predicate("d")
    assert "any(d IN" not in pred
