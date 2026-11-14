"""`POST /api/graph/run`: run an ```artmind-graph``` block's Cypher read-only and
return the nodes and edges to draw (spec
docs/superpowers/specs/2026-11-14-interactive-graph-design.md §2).

Both UIs register it. `mode="admin"` takes any Cypher that passes the read-only
checks. `mode="qa"` (chat-ui) also needs `$domains` in the Cypher and is rate
limited per client address. This is presentation, not retrieval, so nothing is
traced. The log line names the domains and the statement length, never the
Cypher text, because chat-ui Cypher can carry user text.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

QUERY_TIMEOUT_S = 15  # the views runner allows 60 s; a drawing should be quicker
ROW_CAP = 2000
NODE_CAP = 500
PROPERTY_KEYS_CAP = 40
PROPERTY_CHARS_CAP = 500
MAX_CYPHER_CHARS = 20_000
QA_RUNS_PER_MINUTE = 10


def _available_domains() -> list[str]:
    from artmind.cli import _get_available_domains

    return _get_available_domains()


def resolve_domains(value: "str | list[str]") -> list[str]:
    """Normalised domain list; each one must be a schema's domain, a dotted
    child of one, or the family root of one."""
    from artmind.graph_query import normalize_domains

    domains = normalize_domains(value)
    known = _available_domains()
    for domain in domains:
        if not any(domain == k or domain.startswith(k + ".") or k.startswith(domain + ".") for k in known):
            raise ValueError(f"unknown domain {domain!r}")
    return domains


def prepare_cypher(cypher: str, *, require_domains: bool) -> str:
    """The Cypher to run, or ValueError. Read-only (the session is READ_ACCESS
    too; this is the friendly check), no CALL, LIMIT 100 when absent, and no
    parameter but `$domains`."""
    from artmind.text2cypher import _PARAM_RE, _prepare_graph_cypher, validate_domain_scoped, validate_read_only

    validate_read_only(cypher)
    prepared = _prepare_graph_cypher(cypher)
    others = sorted({name for name in _PARAM_RE.findall(prepared) if name != "domains"})
    if others:
        named = ", ".join(f"${name}" for name in others)
        raise ValueError(f"only $domains may be a parameter; write other values as literals (found {named})")
    if require_domains:
        validate_domain_scoped(prepared)
    return prepared


def cap_value(value: Any) -> Any:
    """A value small enough for the wire: long text is cut at PROPERTY_CHARS_CAP
    characters, and a long list or map becomes its cut JSON text."""
    if isinstance(value, str):
        return value if len(value) <= PROPERTY_CHARS_CAP else value[:PROPERTY_CHARS_CAP] + "…"
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return value if len(text) <= PROPERTY_CHARS_CAP else text[:PROPERTY_CHARS_CAP] + "…"
    return value


def cap_properties(props: dict) -> dict:
    return {key: cap_value(props[key]) for key in sorted(props)[:PROPERTY_KEYS_CAP]}


def node_class(labels: list[str], props: dict) -> str | None:
    if props.get("entity_class"):
        return str(props["entity_class"])
    rest = sorted(label for label in labels if label != "Entity")
    return rest[0] if rest else (labels[0] if labels else None)


def trim_node(serialized: dict) -> dict:
    """`serialize_value(node)` cut to what the canvas uses."""
    props = serialized.get("properties") or {}
    labels = sorted(serialized.get("labels") or [])
    out = {
        "id": serialized["id"],
        "labels": labels,
        "name": cap_value(props["name"]) if props.get("name") is not None else None,
        "_id": props.get("_id"),
        "class": node_class(labels, props),
        "observation_count": props.get("_observation_count", props.get("observation_count")),
        "_valid_from": props.get("_valid_from"),
        "properties": cap_properties(props),
    }
    if len(props) > PROPERTY_KEYS_CAP:
        out["properties_omitted"] = len(props) - PROPERTY_KEYS_CAP
    return out


def trim_edge(serialized: dict) -> dict:
    """`serialize_value(relationship)` cut to what the canvas uses."""
    props = serialized.get("properties") or {}
    return {
        "id": serialized["id"],
        "source": serialized["start_node_id"],
        "target": serialized["end_node_id"],
        "type": serialized["type"],
        "rel_type": props.get("rel_type"),
        "properties": cap_properties(props),
    }


ENDPOINT_CYPHER = "MATCH (n) WHERE elementId(n) IN $ids RETURN n"


class GraphCollector:
    """Distinct nodes and relationships from raw driver values, capped at
    `node_cap` nodes. Paths split into both; maps and lists are walked; scalar
    columns are ignored. A relationship's endpoints are not added from the
    relationship (the driver gives a bare node there); they are fetched."""

    def __init__(self, node_cap: int = NODE_CAP):
        self.node_cap = node_cap
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.omitted_node_ids: set[str] = set()

    def add_value(self, value: Any) -> None:
        from neo4j.graph import Node, Path, Relationship

        if isinstance(value, Node):
            self.add_node(value)
        elif isinstance(value, Relationship):
            self.add_edge(value)
        elif isinstance(value, Path):
            for node in value.nodes:
                self.add_node(node)
            for rel in value.relationships:
                self.add_edge(rel)
        elif isinstance(value, dict):
            for item in value.values():
                self.add_value(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.add_value(item)

    def add_node(self, node: Any) -> None:
        from artmind.graph_query import serialize_value

        nid = node.element_id
        if nid in self.nodes or nid in self.omitted_node_ids:
            return
        if len(self.nodes) >= self.node_cap:
            self.omitted_node_ids.add(nid)
            return
        self.nodes[nid] = trim_node(serialize_value(node))

    def add_edge(self, rel: Any) -> None:
        from artmind.graph_query import serialize_value

        if rel.element_id not in self.edges:
            self.edges[rel.element_id] = trim_edge(serialize_value(rel))

    def missing_endpoint_ids(self) -> list[str]:
        missing: list[str] = []
        for edge in self.edges.values():
            for nid in (edge["source"], edge["target"]):
                if nid not in self.nodes and nid not in self.omitted_node_ids and nid not in missing:
                    missing.append(nid)
        return missing

    def result(self) -> dict:
        edges = [e for e in self.edges.values() if e["source"] in self.nodes and e["target"] in self.nodes]
        return {
            "nodes": list(self.nodes.values()),
            "edges": edges,
            "omitted": {"nodes": len(self.omitted_node_ids), "edges": len(self.edges) - len(edges)},
        }


def run_graph(cypher: str, domains: list[str], extra_params: dict | None = None) -> dict:
    """Run prepared Cypher in a READ_ACCESS session; at most ROW_CAP records
    are read. Endpoints of returned relationships that the query did not
    return are fetched by element id, up to the node cap."""
    from neo4j import Query

    from artmind.graph_query import read_session

    collector = GraphCollector()
    rows_read = 0
    rows_capped = False
    with read_session() as session:
        for record in session.run(Query(cypher, timeout=QUERY_TIMEOUT_S), {"domains": domains, **(extra_params or {})}):
            if rows_read >= ROW_CAP:
                rows_capped = True
                break
            rows_read += 1
            for value in record.values():
                collector.add_value(value)
        missing = collector.missing_endpoint_ids()
        room = max(0, NODE_CAP - len(collector.nodes))
        collector.omitted_node_ids.update(missing[room:])
        if missing[:room]:
            for record in session.run(Query(ENDPOINT_CYPHER, timeout=QUERY_TIMEOUT_S), {"ids": missing[:room]}):
                collector.add_value(record["n"])
    out = collector.result()
    omitted = out.pop("omitted")
    return {
        **out,
        "truncated": bool(omitted["nodes"] or omitted["edges"] or rows_capped),
        "omitted": omitted,
        "rows_read": rows_read,
        "rows_capped": rows_capped,
        "limits": {"nodes": NODE_CAP, "rows": ROW_CAP, "timeout_s": QUERY_TIMEOUT_S},
    }