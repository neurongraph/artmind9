"""POST /api/graph/run (spec docs/superpowers/specs/2026-11-14-interactive-graph-design.md §2)."""

import pytest
from neo4j.graph import Graph, Node

from artmind.graph_query import serialize_value
from artmind.webui import graph_routes

SCOPED = (
    "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
    "WHERE (a._domain IN $domains OR any(dom IN $domains WHERE a._domain STARTS WITH (dom + '.'))) "
    "RETURN a, r, b"
)


def _factory():
    g = Graph()

    def node(eid, labels=(), **props):
        return Node(g, eid, 0, list(labels), props)

    def rel(eid, a, b, type_="RELATES_TO", **props):
        r = g.relationship_type(type_)(g, eid, 0, props)
        r._start_node, r._end_node = a, b
        return r

    return node, rel


@pytest.fixture
def known(monkeypatch):
    monkeypatch.setattr(graph_routes, "_available_domains", lambda: ["personal_journal", "banking.reference"])


# ── domains ──────────────────────────────────────────────────────────

def test_resolve_domains_accepts_known_children_and_family_roots(known):
    assert graph_routes.resolve_domains("personal_journal") == ["personal_journal"]
    assert graph_routes.resolve_domains(["personal_journal.health"]) == ["personal_journal.health"]
    assert graph_routes.resolve_domains("banking") == ["banking"]  # root of banking.reference


def test_resolve_domains_refuses_unknown_and_empty(known):
    with pytest.raises(ValueError, match="unknown domain 'bankingx'"):
        graph_routes.resolve_domains("bankingx")
    with pytest.raises(ValueError):
        graph_routes.resolve_domains(" , ")


# ── Cypher checks ────────────────────────────────────────────────────

def test_prepare_cypher_appends_the_graph_limit():
    assert graph_routes.prepare_cypher(SCOPED, require_domains=True) == SCOPED + "\nLIMIT 100"


def test_prepare_cypher_keeps_an_explicit_limit():
    assert graph_routes.prepare_cypher(SCOPED + " LIMIT 5000;", require_domains=False) == SCOPED + " LIMIT 5000"


@pytest.mark.parametrize("cypher, message", [
    ("MATCH (n) DETACH DELETE n", "write operation"),
    ("MATCH (n) SET n.x = 1 RETURN n", "write operation"),
    ("CALL db.labels()", "CALL"),
    ("MATCH (n {name: $name}) WHERE n._domain IN $domains RETURN n", r"\$name"),
])
def test_prepare_cypher_refuses(cypher, message):
    with pytest.raises(ValueError, match=message):
        graph_routes.prepare_cypher(cypher, require_domains=False)


def test_prepare_cypher_requires_domains_only_when_asked():
    graph_routes.prepare_cypher("MATCH (n:Entity) RETURN n", require_domains=False)
    with pytest.raises(ValueError, match=r"\$domains"):
        graph_routes.prepare_cypher("MATCH (n:Entity) RETURN n", require_domains=True)


# ── trimming ─────────────────────────────────────────────────────────

def test_cap_value_trims_long_text_and_long_lists():
    assert graph_routes.cap_value("x" * 10) == "x" * 10
    assert graph_routes.cap_value("x" * 600) == "x" * 500 + "…"
    long_list = graph_routes.cap_value(["abcdef"] * 200)
    assert isinstance(long_list, str) and len(long_list) == 501
    assert graph_routes.cap_value([1, 2]) == [1, 2]
    assert graph_routes.cap_value(3) == 3


def test_cap_properties_keeps_forty_keys():
    capped = graph_routes.cap_properties({f"k{i:02}": i for i in range(45)})
    assert len(capped) == 40 and "k00" in capped and "k44" not in capped


def test_trim_node_reads_class_count_and_strips_internal_props():
    node, _ = _factory()
    raw = node("n1", ["Entity", "Doctor"], name="Dr Rao", _id="e1", _observation_count=7,
               _valid_from="2024-01-02", embedding=[0.1, 0.2], _prop_sources="{}")
    out = graph_routes.trim_node(serialize_value(raw))
    assert out["id"] == "n1" and out["labels"] == ["Doctor", "Entity"]
    assert out["name"] == "Dr Rao" and out["_id"] == "e1" and out["class"] == "Doctor"
    assert out["observation_count"] == 7 and out["_valid_from"] == "2024-01-02"
    assert "embedding" not in out["properties"] and "_prop_sources" not in out["properties"]


def test_trim_node_prefers_entity_class_and_counts_omitted_properties():
    node, _ = _factory()
    props = {f"p{i:02}": i for i in range(41)} | {"entity_class": "Physician"}
    out = graph_routes.trim_node(serialize_value(node("n1", ["Entity", "Doctor"], **props)))
    assert out["class"] == "Physician" and out["properties_omitted"] == 2


def test_trim_edge_names_its_meaning():
    node, rel = _factory()
    a, b = node("n1", ["Entity"]), node("n2", ["Entity"])
    out = graph_routes.trim_edge(serialize_value(rel("r1", a, b, rel_type="treated_by", observation_count=2)))
    assert out == {"id": "r1", "source": "n1", "target": "n2", "type": "RELATES_TO",
                   "rel_type": "treated_by", "properties": {"observation_count": 2, "rel_type": "treated_by"}}


from neo4j import READ_ACCESS, Record
from neo4j.graph import Path


def _patch_session(monkeypatch, session):
    seen = []

    def fake(*a, **k):
        seen.append((a, k))
        return session

    monkeypatch.setattr("artmind.graph_query.neo4j_session", fake)
    return seen


class FakeResult:
    def __init__(self, rows, events):
        self._rows, self._events = rows, events

    def __iter__(self):
        return iter(self._rows)

    def consume(self):
        self._events.append("consume")


class FakeSession:
    """Stands in for read_session(): records (Query, parameters), returns one batch per run."""

    def __init__(self, *batches):
        self.calls = []
        self.events = []
        self._batches = list(batches)

    def run(self, query, parameters=None):
        self.calls.append((query, parameters))
        self.events.append("run")
        return FakeResult(self._batches.pop(0) if self._batches else [], self.events)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_collector_splits_paths_dedupes_and_ignores_scalars():
    node, rel = _factory()
    a, b = node("n1", ["Entity"], name="A"), node("n2", ["Entity"], name="B")
    r = rel("r1", a, b)
    c = graph_routes.GraphCollector()
    for value in (Path(a, r), [a, b], {"x": r}, 42, "text", None):
        c.add_value(value)
    out = c.result()
    assert [n["id"] for n in out["nodes"]] == ["n1", "n2"]
    assert [e["id"] for e in out["edges"]] == ["r1"]
    assert out["omitted"] == {"nodes": 0, "edges": 0}


def test_collector_caps_nodes_and_drops_their_edges():
    node, rel = _factory()
    a, b, c3 = node("n1", ["Entity"]), node("n2", ["Entity"]), node("n3", ["Entity"])
    c = graph_routes.GraphCollector(node_cap=2)
    for value in (a, b, c3, rel("r1", a, b), rel("r2", b, c3)):
        c.add_value(value)
    out = c.result()
    assert len(out["nodes"]) == 2 and [e["id"] for e in out["edges"]] == ["r1"]
    assert out["omitted"] == {"nodes": 1, "edges": 1}
    assert c.missing_endpoint_ids() == []  # n3 is omitted, not missing


def test_run_graph_sends_only_domains_with_the_timeout(monkeypatch):
    node, rel = _factory()
    a, b = node("n1", ["Entity", "Person"], name="Mummy"), node("n2", ["Entity", "Doctor"], name="Dr Rao")
    session = FakeSession([Record({"a": a, "r": rel("r1", a, b, rel_type="treated_by"), "b": b, "n": 3})])
    _patch_session(monkeypatch, session)
    seen = _patch_session(monkeypatch, session)
    out = graph_routes.run_graph(SCOPED + "\nLIMIT 100", ["personal_journal"])
    assert seen == [((READ_ACCESS,), {"timeout": 5})]
    assert len(session.calls) == 1
    assert session.events == ["run", "consume"]
    query, params = session.calls[0]
    assert query.text == SCOPED + "\nLIMIT 100" and query.timeout == 15
    assert params == {"domains": ["personal_journal"]}
    assert {n["name"] for n in out["nodes"]} == {"Mummy", "Dr Rao"}
    assert out["edges"][0]["rel_type"] == "treated_by"
    assert out["truncated"] is False and out["rows_read"] == 1 and out["rows_capped"] is False
    assert out["limits"] == {"nodes": 500, "rows": 2000, "timeout_s": 15}


def test_run_graph_fetches_a_relationship_endpoint_that_was_not_returned(monkeypatch):
    node, rel = _factory()
    a = node("n1", ["Entity"], name="Mummy")
    shell = node("n9")  # the driver gives a bare node for an endpoint the query did not return
    full = _factory()[0]("n9", ["Entity", "Doctor"], name="Dr X")
    session = FakeSession([Record({"a": a, "r": rel("r1", a, shell)})], [Record({"n": full})])
    _patch_session(monkeypatch, session)
    out = graph_routes.run_graph("MATCH (a)-[r]->() RETURN a, r LIMIT 5", ["d"])
    assert session.events == ["run", "consume", "run", "consume"]
    query, params = session.calls[1]
    assert "elementId(n) IN $ids" in query.text and "$domains" in query.text
    assert params == {"ids": ["n9"], "domains": ["d"]}
    assert {n["name"] for n in out["nodes"]} == {"Mummy", "Dr X"} and len(out["edges"]) == 1


def test_run_graph_stops_at_the_row_cap(monkeypatch):
    node, _ = _factory()
    session = FakeSession([Record({"n": node(f"n{i}", ["Entity"])}) for i in range(3)])
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(graph_routes, "ROW_CAP", 2)
    out = graph_routes.run_graph("MATCH (n) RETURN n LIMIT 3", ["d"])
    assert session.events == ["run", "consume"]
    assert out["rows_read"] == 2 and out["rows_capped"] is True and out["truncated"] is True
    assert len(out["nodes"]) == 2


import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j.exceptions import ClientError

RESULT = {"nodes": [{"id": "n1"}], "edges": [], "truncated": False, "omitted": {"nodes": 0, "edges": 0},
          "rows_read": 1, "rows_capped": False, "limits": {"nodes": 500, "rows": 2000, "timeout_s": 15}}


def _client(mode):
    app = FastAPI()
    graph_routes.register_graph_routes(app, mode=mode)
    return TestClient(app)


@pytest.fixture
def ran(monkeypatch, known):
    calls = []

    def fake(cypher, domains, extra_params=None):
        calls.append((cypher, domains, extra_params))
        return RESULT

    monkeypatch.setattr(graph_routes, "run_graph", fake)
    return calls


def test_admin_run_returns_the_graph_and_the_cypher_it_ran(ran):
    r = _client("admin").post("/api/graph/run", json={"domain": "personal_journal", "cypher": SCOPED})
    assert r.status_code == 200
    body = r.json()
    assert body["cypher"] == SCOPED + "\nLIMIT 100" and body["domains"] == ["personal_journal"]
    assert body["nodes"] == [{"id": "n1"}]
    assert ran == [(SCOPED + "\nLIMIT 100", ["personal_journal"], None)]


def test_admin_runs_unscoped_cypher_but_qa_refuses_it(ran):
    body = {"domain": "personal_journal", "cypher": "MATCH (n:Entity) RETURN n"}
    assert _client("admin").post("/api/graph/run", json=body).status_code == 200
    r = _client("qa").post("/api/graph/run", json=body)
    assert r.status_code == 400 and "$domains" in r.json()["detail"]


@pytest.mark.parametrize("body", [
    {"domain": "personal_journal", "cypher": "MATCH (n) DETACH DELETE n"},
    {"domain": "personal_journal", "cypher": "CALL db.labels() YIELD label RETURN label"},
    {"domain": "nowhere", "cypher": SCOPED},
])
def test_refusals_are_400_and_nothing_runs(ran, body):
    assert _client("admin").post("/api/graph/run", json=body).status_code == 400
    assert ran == []


@pytest.mark.parametrize("body", [{"cypher": SCOPED}, {"domain": "d", "cypher": "  "}])
def test_malformed_requests_are_422(ran, body):
    assert _client("admin").post("/api/graph/run", json=body).status_code == 422


def test_qa_is_rate_limited_per_client(ran):
    client = _client("qa")
    body = {"domain": "personal_journal", "cypher": SCOPED}
    codes = [client.post("/api/graph/run", json=body).status_code for _ in range(11)]
    assert codes == [200] * 10 + [429]


def test_rate_limiter_window_slides():
    now = [0.0]
    limiter = graph_routes.RateLimiter(2, window_s=60, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 60.0
    assert limiter.allow("a")


def test_timeout_is_504_and_statement_errors_400_in_one_line(monkeypatch, known):
    def timed_out(*_a, **_k):
        raise ClientError._hydrate_neo4j(code="Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
                                          message="terminated")

    monkeypatch.setattr(graph_routes, "run_graph", timed_out)
    body = {"domain": "personal_journal", "cypher": SCOPED}
    r = _client("admin").post("/api/graph/run", json=body)
    assert r.status_code == 504 and "15 s" in r.json()["detail"]

    def syntax(*_a, **_k):
        raise ClientError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="bad\nat line 2")

    monkeypatch.setattr(graph_routes, "run_graph", syntax)
    r = _client("admin").post("/api/graph/run", json=body)
    assert r.status_code == 400 and r.json()["detail"] == "Neo4j: bad"


def test_the_log_line_never_holds_the_cypher(ran, caplog):
    caplog.set_level(logging.INFO, logger="artmind.webui.graph_routes")
    secret = SCOPED.replace("RETURN a, r, b", "AND a.name = 'Secret Person' RETURN a, r, b")
    _client("admin").post("/api/graph/run", json={"domain": "personal_journal", "cypher": secret})
    assert "graph run" in caplog.text and "Secret Person" not in caplog.text


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        graph_routes.register_graph_routes(FastAPI(), mode="benchmark")


def test_run_graph_counts_an_endpoint_the_fetch_did_not_return(monkeypatch):
    node, rel = _factory()
    a = node("n1", ["Entity"], name="Mummy")
    session = FakeSession([Record({"a": a, "r": rel("r1", a, node("n9"))})], [])
    _patch_session(monkeypatch, session)
    out = graph_routes.run_graph("MATCH (a)-[r]->() RETURN a, r LIMIT 5", ["d"])
    assert out["edges"] == [] and out["omitted"] == {"nodes": 1, "edges": 1} and out["truncated"] is True


@pytest.mark.parametrize("cypher", [
    "MATCH (n) // $domains\nRETURN n",
    "MATCH (n) /* hi */ RETURN n",
    "MATCH (n) /* x */ SET n.a = 1 RETURN n",
    "MATCH (n) WHERE n.u = 'http://x' RETURN n",
    "LOAD CSV FROM 'http://x' AS r RETURN r",
    "load csv from 'http://x' AS r RETURN r",
])
def test_prepare_cypher_refuses_comments_and_load(cypher):
    with pytest.raises(ValueError, match="comments are not allowed|LOAD"):
        graph_routes.prepare_cypher(cypher, require_domains=False)


def test_cap_value_drops_non_finite_floats():
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert graph_routes.cap_value(bad) is None
    assert graph_routes.cap_value(1.5) == 1.5


def test_rate_limiter_forgets_idle_keys():
    now = [0.0]
    limiter = graph_routes.RateLimiter(1, window_s=10, clock=lambda: now[0])
    limiter.allow("a")
    now[0] = 20.0
    limiter.allow("b")
    limiter.allow("a")
    assert set(limiter._hits) == {"a", "b"}
    now[0] = 40.0
    limiter.allow("c")
    assert set(limiter._hits) == {"c"}


def test_unreachable_neo4j_is_502_without_host(monkeypatch, known):
    from neo4j.exceptions import ServiceUnavailable

    def down(*_a, **_k):
        raise ServiceUnavailable("Couldn't connect to localhost:7689")

    monkeypatch.setattr(graph_routes, "run_graph", down)
    r = _client("admin").post("/api/graph/run", json={"domain": "personal_journal", "cypher": SCOPED})
    assert r.status_code == 502 and r.json()["detail"] == "Neo4j is not reachable" and "7689" not in r.text


def test_statement_errors_are_400(monkeypatch, known):
    def bad(*_a, **_k):
        raise ClientError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="bad\nat line 2")

    monkeypatch.setattr(graph_routes, "run_graph", bad)
    r = _client("admin").post("/api/graph/run", json={"domain": "personal_journal", "cypher": SCOPED})
    assert r.status_code == 400 and r.json()["detail"] == "Neo4j: bad"


def test_expand_runs_the_fixed_one_hop_query_with_the_node_id(ran):
    r = _client("qa").post("/api/graph/run", json={"domain": "personal_journal", "expand": "4:abc:12"})
    assert r.status_code == 200
    cypher, domains, extra = ran[0]
    assert cypher == graph_routes.EXPAND_CYPHER and extra == {"id": "4:abc:12"}
    assert "$domains" in cypher and "elementId(n) = $id" in cypher


@pytest.mark.parametrize("body", [
    {"domain": "personal_journal"},
    {"domain": "personal_journal", "cypher": SCOPED, "expand": "4:abc:12"},
    {"domain": "personal_journal", "expand": "  "},
])
def test_exactly_one_of_cypher_and_expand(ran, body):
    assert _client("admin").post("/api/graph/run", json=body).status_code == 422


def test_run_graph_expand_sends_exactly_domains_and_id_and_needs_no_endpoint_fetch(monkeypatch):
    node, rel = _factory()
    n, m = node("n1", ["Entity"], name="A"), node("n2", ["Entity"], name="B")
    session = FakeSession([Record({"n": n, "r": rel("r1", n, m), "m": m})])
    _patch_session(monkeypatch, session)
    out = graph_routes.run_graph(graph_routes.EXPAND_CYPHER, ["personal_journal"], {"id": "n1"})
    assert len(session.calls) == 1
    assert session.calls[0][1] == {"domains": ["personal_journal"], "id": "n1"}
    assert {x["id"] for x in out["nodes"]} == {"n1", "n2"} and len(out["edges"]) == 1
    assert out["truncated"] is False
