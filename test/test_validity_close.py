"""Ends of validity (spec 2026-11-15 §7.2): superseding closes, retiring does not,
un-asserting reopens only what a supersession closed. Recorder-based, plus a tiny
in-memory graph that runs the close/reopen statements to check what they leave."""
import json
import re

import pytest
from _neo4j_recorder import FakeResult, Recorder

from artmind import ingest, lifecycle, projection, supersession_records as sr

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


def test_restore_moves_back_only_what_a_retirement_moved():
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "A", to_history=False, rebuild=False)
    moves = [c for c, _ in rec.calls if "History" in c and "REMOVE" in c]
    assert len(moves) == 5
    for cypher in moves:
        var = re.search(r"MATCH \((\w):", cypher).group(1)
        assert f"WHERE ({var}._retired_by = 'retire' OR {var}._retired_by STARTS WITH 'supersede:')" in cypher
    assert not [c for c, p in rec.calls if "_valid_to" in c]


def test_retire_moves_everything_whatever_its_stamp():
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "A", to_history=True, rebuild=False)
    assert not [c for c, _ in rec.calls if "_retired_by = 'retire' OR" in c]


# ── what the statements leave (an in-memory graph) ──────────────────────────


class _Graph:
    """Runs `supersession_records`' edge reads, reopen and close statements over
    in-memory nodes; every other statement is recorded and ignored. The edge read
    computes each end date and the close order itself, as `_DOCUMENT_EDGES` does:
    `coalesce(effective, newer's _valid_from unless its precision is none)`,
    ordered earliest effective first (an undated edge last), then newer id."""

    def __init__(self, nodes, edges, newer_docs=None):
        self.nodes = nodes        # [{"label": ..., "id"/"doc_id": ..., props}]
        self.edges = edges        # [{"record_id", "newer", "effective", "older"?}] in any order
        self.newer_docs = newer_docs or {}   # newer id -> {"_valid_from", "_valid_precision"}
        self.calls = []

    def _edge_rows(self, older):
        rows = []
        for edge in self.edges:
            if edge.get("older", "A") != older:
                continue
            doc = self.newer_docs.get(edge["newer"], {})
            fallback = None if doc.get("_valid_precision") == "none" else doc.get("_valid_from")
            rows.append({"record_id": edge["record_id"], "newer": edge["newer"],
                         "e": edge.get("effective") or fallback, "_sort": (edge.get("effective") is None, edge.get("effective") or "", edge["newer"])})
        rows.sort(key=lambda r: r.pop("_sort"))
        return rows

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "RETURN s.record_id AS record_id" in cypher:
            assert "ORDER BY s.effective IS NULL, s.effective ASC, newer.id ASC" in cypher
            return FakeResult(self._edge_rows(params["older"]))
        if "RETURN DISTINCT older.id AS older" in cypher:
            assert "WHERE s.effective IS NULL" in cypher
            return FakeResult([{"older": o} for o in sorted({e.get("older", "A") for e in self.edges
                                                               if e["newer"] == params["newer"]
                                                               and not e.get("effective")})])
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
    graph = _Graph(_nodes(), [{"record_id": RID, "newer": "B", "effective": "2026-02-01"}])
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
    graph = _Graph(nodes, [{"record_id": RID, "newer": "B", "effective": "2026-02-01"}])
    sr.apply(graph, _record(), "fp")
    assert _by_id(graph) == {"A": ("2026-02-01", RID)}
    graph.edges = []
    sr.remove(graph, _record())
    assert _by_id(graph) == {"A": (None, None)}


def test_two_supersessions_close_with_the_earliest_and_removing_it_hands_over_to_the_other():
    later = {"record_id": "r2", "newer": "C", "effective": "2026-05-01"}
    earlier = {"record_id": RID, "newer": "B", "effective": "2026-02-01"}
    graph = _Graph([{"label": "DocChunkHistory", "doc_id": "A", "id": "c1"}], [earlier, later])
    sr.apply(graph, _record(), "fp")
    assert _by_id(graph) == {"c1": ("2026-02-01", RID)}, "the first replacement ends it, whichever applied last"
    graph.edges = [later]
    sr.remove(graph, _record())
    assert _by_id(graph) == {"c1": ("2026-05-01", "r2")}, "removing the first hands over to the second"


def test_supersede_with_no_end_date_leaves_everything_open():
    graph = _Graph(_nodes(), [{"record_id": RID, "newer": "B", "effective": None}])
    sr.apply(graph, _record(None), "fp")
    assert {i: v for i, v in _by_id(graph).items() if v != (None, None)} == {
        "A": ("2026-06-30", None), "o2": ("2026-01-15", None)}


# ── a re-commit of the older document keeps the supersession's end ─────────


def _stage(tmp_path, *, stated_end=None):
    folder = tmp_path / "doc"
    folder.mkdir()
    document = {"id": "A", "name": "a.md", "_valid_from": "2026-01-07", "version": 2}
    if stated_end:
        document["_valid_to"] = stated_end
    (folder / "document.json").write_text(json.dumps(document))
    (folder / "chunks.json").write_text(json.dumps([
        {"id": "A_000", "doc_id": "A", "text": "Alpha met Beta at the harbour.", "_domain": "pj"}]))
    (folder / "observations.json").write_text(json.dumps([
        {"id": "o1", "key": "alpha|PERSON|pj", "chunk_id": "A_000", "doc_id": "A", "name": "Alpha",
         "canonical_name": "Alpha", "_domain": "pj"}]))
    (folder / "relationships.json").write_text(json.dumps([]))
    return folder


def _commit(tmp_path, *, stated_end=None, retired=False, edges=()):
    def respond(cypher, params):
        if "RETURN s.record_id AS record_id" in cypher:
            return list(edges)
        return []
    tx = Recorder(respond)
    staged = ingest._load_staged(_stage(tmp_path, stated_end=stated_end), "pj")
    staged["retired"], staged["retired_by"] = retired, f"supersede:{RID}" if retired else None
    ingest._commit_document_tx(tx, staged, True)
    return tx


def test_a_commit_clears_the_staged_nodes_supersession_marker_then_derives_the_end_again(tmp_path):
    tx = _commit(tmp_path, retired=True, edges=[{"record_id": RID, "newer": "B", "e": "2026-02-01"}])

    (doc_merge, doc_params), = [(c, p) for c, p in tx.calls if "CREATE (n:Document " in c]
    assert doc_params["props"]["_valid_to"] is None and doc_params["props"]["_valid_to_by"] is None
    (chunk_merge, chunk_params), = [(c, p) for c, p in tx.calls if "CREATE (n:DocChunk " in c]
    assert chunk_params["props"]["_valid_to"] is None and chunk_params["props"]["_valid_to_by"] is None

    reopens = tx.queries_containing("REMOVE x._valid_to, x._valid_to_by")
    closes = tx.queries_containing("SET x._valid_to = $effective")
    assert reopens and {(p["older"], p["effective"], p["record_id"]) for _, p in closes} == {("A", "2026-02-01", RID)}
    # after every write of this document's nodes, including the re-retirement
    last_write = max(i for i, (c, _) in enumerate(tx.calls)
                     if "MERGE_PASSAGES" in c or "CREATE (n:" in c or "SET o:ObservationHistory" in c
                     or "SET d:DocumentHistory" in c)
    assert tx.calls.index(reopens[0]) > last_write


def test_a_commit_keeps_a_stated_document_end(tmp_path):
    tx = _commit(tmp_path, stated_end="2026-06-30")
    (_, doc_params), = [(c, p) for c, p in tx.calls if "CREATE (n:Document " in c]
    assert doc_params["props"]["_valid_to"] == "2026-06-30" and doc_params["props"]["_valid_to_by"] is None


def test_a_commit_with_no_supersession_closes_nothing(tmp_path):
    tx = _commit(tmp_path)
    assert tx.queries_containing("RETURN s.record_id AS record_id")
    assert not tx.queries_containing("SET x._valid_to = $effective")


def test_recommit_end_to_end_the_supersession_end_survives(tmp_path):
    """The commit clears the marker on the nodes it rewrites (as the props above show);
    the restate then writes the end again from the edge."""
    graph = _Graph([{"label": "DocumentHistory", "id": "A"}, {"label": "DocChunkHistory", "doc_id": "A", "id": "c1"}],
                   [{"record_id": RID, "newer": "B", "effective": "2026-02-01"}])
    sr.apply(graph, _record(), "fp")
    for n in graph.nodes:                     # what the commit's rewrite leaves
        n["_valid_to"] = n["_valid_to_by"] = None
    sr.restate_validity(graph, "A")
    assert _by_id(graph) == {"A": ("2026-02-01", RID), "c1": ("2026-02-01", RID)}


def test_a_retired_documents_commit_restamps_the_prior_versions_rows(tmp_path):
    tx = _commit(tmp_path, retired=True)
    restamps = tx.queries_containing("WHERE NOT x.id IN $ids")
    by_label = {re.search(r"MATCH \(x:(\w+)", c).group(1): p for c, p in restamps}
    assert set(by_label) == {"ObservationHistory", "DocChunkHistory", "DocPassageHistory", "DocSummaryHistory"}
    assert by_label["ObservationHistory"]["ids"] == ["o1"] and by_label["DocChunkHistory"]["ids"] == ["A_000"]
    assert by_label["DocPassageHistory"]["ids"], "the passages this version wrote"
    assert all(p["by"] == "version:2" and p["doc_id"] == "A" for p in by_label.values())
    assert all("x._retired_by = 'retire' OR x._retired_by STARTS WITH 'supersede:'" in c for c, _ in restamps)


def test_a_live_documents_commit_restamps_nothing(tmp_path):
    assert not _commit(tmp_path).queries_containing("WHERE NOT x.id IN $ids")


def test_a_passage_re_split_from_a_closed_chunk_is_closed_by_the_same_record():
    from artmind import embed_sweep, passages

    rows = passages.passage_rows({"id": "A_000", "doc_id": "A", "text": "Alpha met Beta.",
                                  "_valid_to": "2026-02-01", "_valid_to_by": RID}, "a")
    assert rows and all(r["props"]["_valid_to"] == "2026-02-01" and r["props"]["_valid_to_by"] == RID
                        for r in rows)
    assert "c._valid_to_by AS _valid_to_by" in embed_sweep._CHUNKS_NEEDING_PASSAGES



def test_with_no_effective_the_end_is_the_newer_documents_stated_date():
    graph = _Graph([{"label": "DocChunkHistory", "doc_id": "A", "id": "c1"}],
                   [{"record_id": RID, "newer": "B", "effective": None}],
                   newer_docs={"B": {"_valid_from": "2026-03-10", "_valid_precision": "day"}})
    sr.restate_validity(graph, "A")
    assert _by_id(graph) == {"c1": ("2026-03-10", RID)}
    graph.newer_docs["B"]["_valid_precision"] = "none"
    sr.restate_validity(graph, "A")
    assert _by_id(graph) == {"c1": (None, None)}, "a default date ends nothing"


def test_a_dated_edge_closes_before_an_undated_one():
    graph = _Graph([{"label": "DocChunkHistory", "doc_id": "A", "id": "c1"}],
                   [{"record_id": "r9", "newer": "Z", "effective": None},
                    {"record_id": RID, "newer": "B", "effective": "2026-02-01"}],
                   newer_docs={"Z": {"_valid_from": "2027-01-01", "_valid_precision": "day"}})
    sr.restate_validity(graph, "A")
    assert _by_id(graph) == {"c1": ("2026-02-01", RID)}


def test_recommitting_the_newer_document_with_a_new_date_moves_the_olders_end():
    graph = _Graph([{"label": "DocChunkHistory", "doc_id": "A", "id": "c1"}],
                   [{"record_id": RID, "newer": "B", "effective": None}],
                   newer_docs={"B": {"_valid_from": "2026-03-10", "_valid_precision": "day"}})
    sr.restate_validity(graph, "A")
    graph.newer_docs["B"]["_valid_from"] = "2026-04-01"      # B committed again with a new stated date
    assert sr.restate_superseded(graph, "B") == ["A"]
    assert _by_id(graph) == {"c1": ("2026-04-01", RID)}


def test_a_commit_restates_only_undated_supersessions():
    graph = _Graph([{"label": "DocChunkHistory", "doc_id": "A", "id": "c1"}],
                   [{"record_id": RID, "newer": "B", "effective": "2026-02-01"},
                    {"record_id": "r2", "newer": "B", "effective": None, "older": "Z"}],
                   newer_docs={"B": {"_valid_from": "2026-04-01", "_valid_precision": "day"}})
    assert sr.restate_superseded(graph, "B") == ["Z"], "a dated edge's older document is not restated"
    (cypher, _), = [c for c in graph.calls if "RETURN DISTINCT older.id AS older" in c[0]]
    assert "WHERE s.effective IS NULL" in cypher


def test_a_commit_restates_the_documents_it_supersedes_and_rebuilds_their_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, **kw: {(doc_id, "C", "d")})

    def respond(cypher, params):
        if "RETURN DISTINCT older.id AS older" in cypher:
            return [{"older": "Z"}]
        if "RETURN s.record_id AS record_id" in cypher and params["older"] == "Z":
            return [{"record_id": "rz", "newer": "A", "e": "2026-01-07"}]
        return []
    tx = Recorder(respond)
    staged = ingest._load_staged(_stage(tmp_path), "pj")
    summary = ingest._commit_document_tx(tx, staged, True)

    (_, params), = tx.queries_containing("RETURN DISTINCT older.id AS older")
    assert params == {"newer": "A"}
    closes = tx.queries_containing("SET x._valid_to = $effective")
    assert {(p["older"], p["effective"], p["record_id"]) for _, p in closes} == {("Z", "2026-01-07", "rz")}
    assert any("MERGE (s:DomainState" in c and p.get("doc_id") == "Z" for c, p in tx.calls)
    assert "Z|C|d" in {"|".join(k) for k in summary["deferred_keys"]}


# ── apply_supersession / unapply_supersession ──────────────────────────────


class _Session(Recorder):
    """A session: `run` outside a transaction is recorded as such."""

    def __init__(self, respond=None):
        super().__init__(respond)
        self.writes = 0
        self.in_tx = False
        self.outside = []

    def run(self, cypher, **params):
        if not self.in_tx:
            self.outside.append(cypher)
        return super().run(cypher, **params)

    def execute_write(self, fn, *args, **kwargs):
        self.writes += 1
        self.in_tx = True
        try:
            return fn(self, *args, **kwargs)
        finally:
            self.in_tx = False


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from contextlib import contextmanager

    import paths
    from artmind import temporal

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")
    session = _Session(lambda c, p: [{"record_id": RID, "newer": "B", "e": "2026-02-01"}]
                       if "RETURN s.record_id AS record_id" in c else [])

    @contextmanager
    def _fake(*a, **k):
        yield session

    monkeypatch.setattr(temporal, "neo4j_session", _fake)
    calls = {"retire": [], "restore": [], "rebuild": []}
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda d, *a, **k: calls["retire"].append(d))
    monkeypatch.setattr("artmind.lifecycle.restore_document", lambda d, *a, **k: calls["restore"].append(d))
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: calls["rebuild"].append(set(keys)) or {})
    return session, calls


def test_apply_supersession_writes_the_graph_in_one_transaction(world):
    from artmind import temporal

    session, calls = world
    temporal.apply_supersession("B", "A", "document", "2026-02-01")

    assert session.writes == 1
    assert not [c for c in session.outside if re.search(r"\b(MERGE|SET|REMOVE|DELETE)\b", c)], \
        "every write inside the one transaction"
    assert session.queries_containing("SET x._valid_to = $effective")
    assert any("MERGE (s:DomainState" in c for c, _ in session.calls)
    assert calls["retire"] == ["A"], "the retirement rebuilds the older document's keys"


def test_unsupersede_of_a_restored_document_rebuilds_the_reopened_keys(world, monkeypatch):
    """supersede -> docs restore -> unsupersede: the document is live, nothing restores it,
    so the unsupersede transaction itself rebuilds the keys whose ends it removed."""
    from artmind import temporal

    session, calls = world
    session._respond = lambda c, p: []          # no edge remains after the delete
    monkeypatch.setattr(temporal, "_unsupersede_state", lambda *a: {
        "edge": True, "others": 0, "retired": False, "names": {}, "domains": ["pj"]})

    out = temporal.unapply_supersession("B", "A", "document")

    assert out["removed"] is True and out["restored"] is False and calls["restore"] == []
    assert session.writes == 1
    assert calls["rebuild"] == [{("a", "C", "d")}]
    assert session.queries_containing("REMOVE x._valid_to, x._valid_to_by")


# ── a document's stated end is every derived node's own (Q4, decided 2026-11-18) ──


def test_a_stated_end_earlier_than_the_supersession_stays_on_every_node_and_unsupersede_keeps_it():
    stated, other = "2026-01-15", "2026-02-01"
    nodes = [
        {"label": "DocChunkHistory", "doc_id": "A", "id": "c1", "_valid_to": stated},
        {"label": "DocPassageHistory", "doc_id": "A", "id": "p1", "_valid_to": stated},
        {"label": "DocSummaryHistory", "doc_id": "A", "id": "s1", "_valid_to": stated},
        {"label": "DocChunkHistory", "doc_id": "A", "id": "c2"},   # an open one still closes
    ]
    graph = _Graph(nodes, [{"record_id": RID, "newer": "B", "effective": other}])
    sr.apply(graph, _record(other), "fp")
    assert _by_id(graph) == {"c1": (stated, None), "p1": (stated, None), "s1": (stated, None),
                             "c2": (other, RID)}
    graph.edges = []
    sr.remove(graph, _record(other))
    assert _by_id(graph) == {"c1": (stated, None), "p1": (stated, None), "s1": (stated, None),
                             "c2": (None, None)}, "unsupersede reopens only the marker-closed node"


def test_a_document_without_a_stated_end_takes_the_supersession_end_on_chunk_passage_summary():
    nodes = [{"label": "DocChunkHistory", "doc_id": "A", "id": "c1"},
             {"label": "DocPassageHistory", "doc_id": "A", "id": "p1"},
             {"label": "DocSummaryHistory", "doc_id": "A", "id": "s1"}]
    graph = _Graph(nodes, [{"record_id": RID, "newer": "B", "effective": "2026-02-01"}])
    sr.apply(graph, _record(), "fp")
    assert set(_by_id(graph).values()) == {("2026-02-01", RID)}


def test_a_recommit_leaves_a_chunks_stated_end_and_restate_closes_only_open_ones(tmp_path):
    folder = tmp_path / "doc"
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps(
        {"id": "A", "name": "a.md", "_valid_from": "2026-01-07", "_valid_to": "2026-01-20", "version": 2}))
    chunks = [{"id": "A_000", "doc_id": "A", "text": "Alpha met Beta at the harbour.", "_domain": "pj"}]
    (folder / "chunks.json").write_text(json.dumps(ingest._date_chunks(
        chunks, {"id": "A", "_valid_from": "2026-01-07", "_valid_to": "2026-01-20"})))
    for name in ("observations.json", "relationships.json"):
        (folder / name).write_text("[]")
    tx = Recorder(lambda c, p: [{"record_id": RID, "newer": "B", "e": "2026-02-01"}]
                  if "RETURN s.record_id AS record_id" in c else [])
    ingest._commit_document_tx(tx, ingest._load_staged(folder, "pj"), True)
    (_, chunk_params), = [(c, p) for c, p in tx.calls if "CREATE (n:DocChunk " in c]
    assert chunk_params["props"]["_valid_to"] == "2026-01-20" and chunk_params["props"]["_valid_to_by"] is None
    closes = tx.queries_containing("SET x._valid_to = $effective")
    assert closes and all("WHERE x._valid_to IS NULL" in c for c, _ in closes)


def test_a_dated_edge_closes_before_an_undated_one_even_when_the_undated_is_earlier():
    graph = _Graph([{"label": "DocChunkHistory", "doc_id": "A", "id": "c1"}],
                   [{"record_id": "r9", "newer": "Z", "effective": None},
                    {"record_id": "r2", "newer": "C", "effective": "2026-05-01"},
                    {"record_id": RID, "newer": "B", "effective": "2026-02-01"}],
                   newer_docs={"Z": {"_valid_from": "2025-01-01", "_valid_precision": "day"}})
    sr.restate_validity(graph, "A")
    assert _by_id(graph) == {"c1": ("2026-02-01", RID)}
