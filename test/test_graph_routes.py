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
