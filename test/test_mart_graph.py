from artmind import graph_query
from artmind.mart import graph as mg


def _capture(monkeypatch, rows):
    calls = []

    def fake(cypher, params):
        calls.append((cypher, params))
        return rows
    monkeypatch.setattr(graph_query, "_run_read_query", fake)
    return calls


def test_members_reads_one_class_in_scope(monkeypatch):
    calls = _capture(monkeypatch, [{"key": "a|PERSON|j", "props": {"name": "A"}}])
    out = mg.members(["j"], "PERSON")
    cypher, params = calls[0]
    assert "n.entity_class = $cls" in cypher and params == {"domains": ["j"], "cls": "PERSON"}
    assert out == [{"key": "a|PERSON|j", "props": {"name": "A"}}]


def test_happenings_collect_evidence(monkeypatch):
    calls = _capture(monkeypatch, [])
    mg.happenings(["j"], ["EVENT"])
    cypher, params = calls[0]
    assert "AGGREGATES" in cypher and "collect(o.chunk_id)" in cypher and params["classes"] == ["EVENT"]


def test_mentions_and_bridges(monkeypatch):
    calls = _capture(monkeypatch, [])
    mg.mentions(["j"], ["ACTIVITY"])
    mg.bridges(["j"], ["EVENT"], ["PERSON", "LOCATION"])
    assert "o._valid_from" in calls[0][0]
    assert "RELATES_TO" in calls[1][0] and calls[1][1]["dims"] == ["PERSON", "LOCATION"]


# --- dimension links (spec 2026-11-19) ------------------------------------------------------------------


def test_happenings_return_the_declared_dimension_properties(monkeypatch):
    calls = _capture(monkeypatch, [{"key": "k", "name": "n", "class": "EVENT", "participants": ["A"],
                                    "_dim_pairs": [["participants", ["A"]], ["location", "Hill Town"], ["who_involved", ""]]}])
    out = mg.happenings(["j"], ["EVENT"], ["participants", "location", "who_involved"])
    cypher, params = calls[0]
    assert params["dimProps"] == ["participants", "location", "who_involved"]
    assert "[k IN $dimProps WHERE e[k] IS NOT NULL | [k, e[k]]]" in cypher
    assert "properties(e)" not in cypher  # embeddings and every other property stay in the graph
    assert out[0]["dim_props"] == {"participants": ["A"], "location": "Hill Town"} and "_dim_pairs" not in out[0]


def test_happenings_without_dimension_properties_ask_for_none(monkeypatch):
    calls = _capture(monkeypatch, [])
    mg.happenings(["j"], ["EVENT"])
    assert calls[0][1]["dimProps"] == []


def test_same_chunk_members_join_through_the_fact_chunks(monkeypatch):
    calls = _capture(monkeypatch, [{"fact_key": "f", "member_key": "m"}])
    out = mg.same_chunk_members(["j"], ["EVENT"], "LOCATION")
    cypher, params = calls[0]
    assert "(f:Entity)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)" in cypher
    assert "(c)<-[:EXTRACTED_FROM]-(:Observation)<-[:AGGREGATES]-(d:Entity)" in cypher
    assert "d.entity_class = $dim" in cypher and "f.entity_class IN $facts" in cypher
    assert params == {"domains": ["j"], "facts": ["EVENT"], "dim": "LOCATION"}
    assert out == [{"fact_key": "f", "member_key": "m"}]
