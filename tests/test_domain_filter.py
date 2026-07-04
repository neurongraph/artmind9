"""Tests that domain filters in Cypher queries use the expanded STARTS WITH form."""
import inspect
import artmind.graph_query as gq
import artmind.vector_query as vq
import artmind.update as upd


def _get_pattern_cypher(pattern: str, **kwargs) -> str:
    """Extract the Cypher string for a given pattern without hitting Neo4j."""
    fake_params = {
        'domains': ['fiction'],
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


def test_vector_search_cypher_uses_expanded_filter(monkeypatch):
    """vector_search must scope Cypher via domain_predicate, which still
    rolls up sub-domains using STARTS WITH under the hood."""
    captured = {"cyphers": []}

    class FakeSession:
        def run(self, cypher, **params):
            captured["cyphers"].append(cypher)
            return []

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(vq, "embed_question", lambda question: [0.1, 0.2])
    monkeypatch.setattr(vq, "neo4j_session", lambda: FakeSessionContext())

    vq.vector_search("fiction", "Where?", 2)

    assert captured["cyphers"]
    assert all('STARTS WITH' in cypher for cypher in captured["cyphers"])
    assert 'domain_predicate(' in inspect.getsource(vq.vector_search)


def test_full_text_search_cypher_uses_expanded_filter(monkeypatch):
    """full_text_search must scope Cypher via domain_predicate, which still
    rolls up sub-domains using STARTS WITH under the hood."""
    captured = {"cyphers": []}

    class FakeSession:
        def run(self, cypher, **params):
            captured["cyphers"].append(cypher)
            return []

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(vq, "neo4j_session", lambda: FakeSessionContext())

    vq.full_text_search("fiction", "Watson Holmes", 2)

    assert captured["cyphers"]
    assert all('STARTS WITH' in cypher for cypher in captured["cyphers"])
    assert 'domain_predicate(' in inspect.getsource(vq.full_text_search)
