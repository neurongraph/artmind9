"""Ends of validity (spec 2026-11-15 §7.2): superseding closes, retiring does not,
un-asserting reopens only what a supersession closed. Recorder-based, plus a tiny
in-memory graph that runs the close/reopen statements to check what they leave."""
import re

import pytest
from _neo4j_recorder import FakeResult, Recorder

from artmind import lifecycle, projection, supersession_records as sr

RID = "r1"


def _record(effective="2026-02-01", rid=RID, newer="B"):
    return {"id": rid, "newer_doc_id": newer, "older_doc_id": "A", "scope": "document", "effective": effective}


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, **kw: {("a", "C", "d")})


def _edges(*edges):
    """A recorder whose `_DOCUMENT_EDGES` read returns `edges` ({record_id, e})."""
    def respond(cypher, params):
        if "RETURN s.record_id AS record_id" in cypher:
            return list(edges)
        return [{"n": 1}]
    return Recorder(respond)


# ── the statements ──────────────────────────────────────────────────────────


def test_apply_closes_the_older_documents_open_nodes_with_the_effective_date():
    rec = _edges({"record_id": RID, "newer": "B", "e": "2026-02-01"})

    keys = sr.apply(rec, _record(), "fp")

    closes = rec.queries_containing("SET x._valid_to = $effective")
    assert {p["effective"] for _, p in closes} == {"2026-02-01"}
    assert all(p == {"older": "A", "effective": "2026-02-01", "record_id": RID} for _, p in closes)
    assert all("WHERE x._valid_to IS NULL" in c and "x._valid_to_by = $record_id" in c for c, _ in closes)
    labels = {re.search(r"MATCH \(x:(\w+)", c).group(1) for c, _ in closes}
    assert labels == {"Document", "DocumentHistory", "Observation", "ObservationHistory", "DocChunk",
                      "DocChunkHistory", "DocPassage", "DocPassageHistory", "DocSummary", "DocSummaryHistory"}
    assert all("{doc_id: $older}" in c or "{id: $older}" in c for c, _ in closes), "index-backed, per label"
    assert keys == {("a", "C", "d")}, "the older document's keys go back for the rebuild"


def test_the_end_date_falls_back_to_the_newer_documents_stated_date_only():
    rec = _edges()
    sr.apply(rec, _record(None), "fp")
    (edges, params), = rec.queries_containing("RETURN s.record_id AS record_id")
    assert "coalesce(s.effective, CASE WHEN newer._valid_precision = 'none' THEN null ELSE newer._valid_from END)" in edges
    assert "{scope: 'document'}" in edges and params == {"older": "A"}


def test_no_effective_date_closes_nothing():
    rec = _edges({"record_id": RID, "newer": "B", "e": None})
    sr.apply(rec, _record(None), "fp")
    assert not rec.queries_containing("SET x._valid_to")


def test_the_document_end_is_no_longer_overwritten_by_the_recompute():
    rec = _edges()
    sr.apply(rec, _record(), "fp")
    (recompute, _), = rec.queries_containing("older.superseded_by = CASE")
    assert "older._valid_to" not in recompute


def test_a_node_scope_record_closes_nothing():
    rec = _edges({"record_id": RID, "newer": "B", "e": "2026-02-01"})
    assert sr.apply(rec, {**_record(), "scope": "node"}, "fp") == set()
    assert not rec.queries_containing("_valid_to_by")


def test_remove_reopens_what_supersession_closed_then_closes_from_the_remaining_edges():
    rec = _edges({"record_id": "r2", "newer": "C", "e": "2026-05-01"})

    keys = sr.remove(rec, _record())

    reopens = rec.queries_containing("REMOVE x._valid_to, x._valid_to_by")
    assert len(reopens) == len(sr.VALIDITY_TARGETS)
    assert all("WHERE x._valid_to_by IS NOT NULL" in c for c, _ in reopens), "a stated end is never reopened"
    closes = rec.queries_containing("SET x._valid_to = $effective")
    assert {(p["record_id"], p["effective"]) for _, p in closes} == {("r2", "2026-05-01")}
    first_close = rec.calls.index(closes[0])
    assert all(rec.calls.index(r) < first_close for r in reopens)
    assert keys == {("a", "C", "d")}


def test_remove_of_a_stub_record_restates_each_older_document():
    def respond(cypher, params):
        return [{"oid": "A"}] if "RETURN oid" in cypher else []
    rec = Recorder(respond)
    sr.remove(rec, {"id": RID})
    assert {p["older"] for _, p in rec.queries_containing("REMOVE x._valid_to")} == {"A"}


def test_retire_leaves_valid_to_alone():
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "A", to_history=True, rebuild=False)
    assert not [c for c, p in rec.calls if "_valid_to" in c]


# ── what the statements leave (an in-memory graph) ──────────────────────────


class _Graph:
    """Runs `supersession_records`' edge read, reopen and close statements over
    in-memory nodes; every other statement is recorded and ignored."""

    def __init__(self, nodes, edges):
        self.nodes = nodes        # [{"label": ..., "id"/"doc_id": ..., props}]
        self.edges = edges        # [{"record_id", "newer", "e"}], already in close order
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "RETURN s.record_id AS record_id" in cypher:
            return FakeResult(list(self.edges))
        m = re.search(r"MATCH \(x:(\w+) \{(\w+): \$older\}\)", cypher)
        if not m:
            return FakeResult([{"oid": "A"}] if "RETURN oid" in cypher else [])
        label, key = m.groups()
        hit = [n for n in self.nodes if n["label"] == label and n.get(key) == params["older"]]
        if "REMOVE x._valid_to, x._valid_to_by" in cypher:
            hit = [n for n in hit if n.get("_valid_to_by") is not None]
            for n in hit:
                n.pop("_valid_to", None)
                n.pop("_valid_to_by", None)
        elif "SET x._valid_to = $effective" in cypher:
            hit = [n for n in hit if n.get("_valid_to") is None]
            for n in hit:
                n["_valid_to"], n["_valid_to_by"] = params["effective"], params["record_id"]
        return FakeResult([{"n": len(hit)}])


def _nodes():
    return [
        {"label": "DocumentHistory", "id": "A", "_valid_to": "2026-06-30"},       # a stated end
        {"label": "ObservationHistory", "doc_id": "A", "id": "o1"},
        {"label": "ObservationHistory", "doc_id": "A", "id": "o2", "_valid_to": "2026-01-15"},  # stated
        {"label": "ObservationHistory", "doc_id": "A", "id": "o0", "_retired_by": "version:1"},  # earlier version
        {"label": "DocChunkHistory", "doc_id": "A", "id": "c1"},
        {"label": "DocPassageHistory", "doc_id": "A", "id": "p1"},
        {"label": "DocSummaryHistory", "doc_id": "A", "id": "s1"},
        {"label": "DocChunk", "doc_id": "Z", "id": "z1"},                          # another document
    ]


def _by_id(graph):
    return {n["id"]: (n.get("_valid_to"), n.get("_valid_to_by")) for n in graph.nodes}


def test_supersede_then_unsupersede_restores_every_stated_end():
    graph = _Graph(_nodes(), [{"record_id": RID, "newer": "B", "e": "2026-02-01"}])
    sr.apply(graph, _record(), "fp")
    assert _by_id(graph) == {
        "A": ("2026-06-30", None), "o1": ("2026-02-01", RID), "o2": ("2026-01-15", None),
        "o0": ("2026-02-01", RID), "c1": ("2026-02-01", RID), "p1": ("2026-02-01", RID),
        "s1": ("2026-02-01", RID), "z1": (None, None),
    }

    graph.edges = []
    sr.remove(graph, _record())
    assert _by_id(graph) == {
        "A": ("2026-06-30", None), "o1": (None, None), "o2": ("2026-01-15", None), "o0": (None, None),
        "c1": (None, None), "p1": (None, None), "s1": (None, None), "z1": (None, None),
    }
    assert next(n for n in graph.nodes if n["id"] == "o0")["_retired_by"] == "version:1"


def test_an_open_document_gets_the_end_and_loses_it_on_unsupersede():
    nodes = [{"label": "DocumentHistory", "id": "A"}]
    graph = _Graph(nodes, [{"record_id": RID, "newer": "B", "e": "2026-02-01"}])
    sr.apply(graph, _record(), "fp")
    assert _by_id(graph) == {"A": ("2026-02-01", RID)}
    graph.edges = []
    sr.remove(graph, _record())
    assert _by_id(graph) == {"A": (None, None)}


def test_two_supersessions_close_with_the_latest_and_removing_it_hands_over_to_the_other():
    later, earlier = {"record_id": "r2", "newer": "C", "e": "2026-05-01"}, {"record_id": RID, "newer": "B", "e": "2026-02-01"}
    graph = _Graph([{"label": "DocChunkHistory", "doc_id": "A", "id": "c1"}], [later, earlier])
    sr.apply(graph, _record(), "fp")
    assert _by_id(graph) == {"c1": ("2026-05-01", "r2")}, "the same result whichever record applied last"
    graph.edges = [earlier]
    sr.remove(graph, _record(rid="r2", newer="C"))
    assert _by_id(graph) == {"c1": ("2026-02-01", RID)}


def test_supersede_with_no_end_date_leaves_everything_open():
    graph = _Graph(_nodes(), [{"record_id": RID, "newer": "B", "e": None}])
    sr.apply(graph, _record(None), "fp")
    assert {i: v for i, v in _by_id(graph).items() if v != (None, None)} == {
        "A": ("2026-06-30", None), "o2": ("2026-01-15", None)}
