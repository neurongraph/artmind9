"""Tests that domain filters in Cypher queries use the expanded STARTS WITH form."""
import inspect
import artmind.graph_query as gq
import artmind.vector_query as vq
import artmind.update as upd


def _get_pattern_cypher(pattern: str, **kwargs) -> str:
    """Extract the Cypher string for a given pattern without hitting Neo4j."""
    fake_params = {
        'domain': 'fiction',
        'entityClass': 'PERSON',
        'entityName': 'Holmes',
        'entityNameList': ['Holmes'],
        'entityClass1': 'PERSON',
        'entityClass2': 'LOCATION',
        'entityName1': 'Holmes',
        'entityName2': 'London',
        'searchTerm': 'detective',
        'topN': 5,
        'limit': 10,
        'mode': 'shortest',
        **kwargs,
    }
    cypher, _ = gq._pattern_query(pattern, fake_params)
    return cypher


def test_pattern1_uses_expanded_domain_filter():
    cypher = _get_pattern_cypher('pattern1')
    assert 'STARTS WITH' in cypher


def test_pattern2_uses_expanded_domain_filter():
    cypher = _get_pattern_cypher('pattern2')
    assert 'STARTS WITH' in cypher


def test_pattern3_uses_expanded_domain_filter():
    cypher = _get_pattern_cypher('pattern3')
    assert 'STARTS WITH' in cypher


def test_pattern4_uses_expanded_domain_filter():
    cypher = _get_pattern_cypher('pattern4')
    assert 'STARTS WITH' in cypher


def test_pattern7_uses_expanded_domain_filter():
    cypher = _get_pattern_cypher('pattern7')
    assert 'STARTS WITH' in cypher


def test_pattern9_uses_expanded_domain_filter():
    cypher = _get_pattern_cypher('pattern9')
    assert 'STARTS WITH' in cypher


def test_find_candidates_cypher_uses_expanded_filter():
    src = inspect.getsource(upd.find_candidates)
    assert 'STARTS WITH' in src


def test_vector_search_cypher_uses_expanded_filter():
    src = inspect.getsource(vq.vector_search)
    assert 'STARTS WITH' in src


def test_full_text_search_cypher_uses_expanded_filter():
    src = inspect.getsource(vq.full_text_search)
    assert 'STARTS WITH' in src
