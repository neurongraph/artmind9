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
