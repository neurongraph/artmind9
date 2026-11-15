"""SAME_AS links are read: entity-resolve and entity-context rows carry `same_as` (plan 2026-11-15 task 6)."""
from _neo4j_recorder import Recorder

from artmind import graph_query

LINK = {"_id": "e2", "name": "Zephyr Saver Policy", "entity_class": "POLICY", "_domain": "bank.policy"}


def test_same_as_links_reads_both_directions_for_the_ids_sent():
    s = Recorder(lambda c, p: [{"id": "e1", "same_as": [LINK, {"_id": "e1", "name": "self"}]}]
                 if "[:SAME_AS]-" in c else [])
    out = graph_query.same_as_links(s, ["e1", None, "e1", "e9"])
    (cypher, params), = s.calls
    assert "MATCH (e:Entity {_id: id})-[:SAME_AS]-(t:Entity)" in cypher  # undirected
    assert params == {"ids": ["e1", "e9"]}
    assert out == {"e1": [LINK]}


def test_same_as_links_sends_nothing_for_no_ids_and_survives_a_failure():
    s = Recorder()
    assert graph_query.same_as_links(s, [None, ""]) == {} and s.calls == []

    def boom(c, p):
        raise RuntimeError("down")
    assert graph_query.same_as_links(Recorder(boom), ["e1"]) == {}


def test_entity_resolve_rows_carry_same_as(monkeypatch):
    from artmind import vector_query

    def respond(cypher, params):
        if "[:SAME_AS]-" in cypher:
            return [{"id": "e1", "same_as": [LINK]}]
        if "toLower(e.name) = toLower($reference)" in cypher:
            return [{"entity": {"_id": "e1", "name": "Zephyr Saver", "entity_class": "PRODUCT", "_domain": "bank"}}]
        return []

    s = Recorder(respond)
    monkeypatch.setattr(vector_query, "read_session", lambda: s)
    monkeypatch.setattr(vector_query, "embed_question", lambda q: [0.1])
    out = vector_query.entity_resolve(["bank"], "Zephyr Saver")
    assert out["rows"][0]["entity"]["same_as"] == [LINK]
