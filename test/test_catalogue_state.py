"""`catalogue.state` -- the per-domain generation (spec 2026-11-02 §3)."""
from _neo4j_recorder import Recorder

from artmind.catalogue import state


def test_bump_sends_one_statement_for_all_domains_sorted_and_deduplicated():
    tx = Recorder()
    bumped = state.bump(tx, ["b.child", "a", "a", " ", None, "b.child"])
    assert bumped == ["a", "b.child"]
    (cypher, params), = tx.calls
    assert "MERGE (s:DomainState {_domain: d})" in cypher
    assert "s.generation = coalesce(s.generation, 0) + 1" in cypher
    assert "s.changed_at = $now" in cypher
    assert params["domains"] == ["a", "b.child"]
    assert params["now"]


def test_bump_sends_nothing_for_no_domains():
    tx = Recorder()
    assert state.bump(tx, [None, "", "  "]) == []
    assert tx.calls == []


def test_bump_for_document_reads_the_domain_from_either_document_label():
    tx = Recorder()
    state.bump_for_document(tx, "d1")
    (cypher, params), = tx.calls
    assert "d:Document OR d:DocumentHistory" in cypher
    assert "MERGE (s:DomainState {_domain: dom})" in cypher
    assert params["doc_id"] == "d1" and params["now"]


def test_generations_reads_once_and_sums_children_into_the_parent():
    def respond(cypher, params):
        assert "DomainState" in cypher
        return [{"domain": "bank", "generation": 2}, {"domain": "bank.ref", "generation": 3},
                {"domain": "other", "generation": 7}]

    session = Recorder(respond)
    out = state.generations(session, ["bank", "bank.ref", "unseen"])
    assert out == {"bank": 5, "bank.ref": 3, "unseen": 0}
    assert len(session.calls) == 1
    assert session.calls[0][1]["domains"] == ["bank", "bank.ref", "unseen"]
    assert "STARTS WITH" in session.calls[0][0]


def test_generations_of_nothing_asks_nothing():
    session = Recorder()
    assert state.generations(session, []) == {}
    assert session.calls == []


def test_constraint_is_a_uniqueness_on_the_domain():
    assert "FOR (n:DomainState) REQUIRE n._domain IS UNIQUE" in state.CONSTRAINT_CYPHER


def test_setup_creates_the_constraint():
    import inspect

    from artmind import setup

    assert "domain_state_constraint" in inspect.getsource(setup._setup_neo4j)
