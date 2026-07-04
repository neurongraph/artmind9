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
        "(e.domain IN $domains OR any(d IN $domains WHERE e.domain STARTS WITH (d + '.')))"
    )


def test_domain_predicate_custom_var_and_param():
    pred = domain_predicate("node", param="doms")
    assert "node.domain IN $doms" in pred
