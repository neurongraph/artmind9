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
import math
import re
import threading
import time
from collections import deque
from typing import Any

import anyio.to_thread
from fastapi import FastAPI, HTTPException, Request
from neo4j import READ_ACCESS
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, field_validator, model_validator

from artmind.graph_query import domain_predicate

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 5  # a down Neo4j must not hold a request for the driver's 60 s
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


def resolve_domains(value: str | list[str]) -> list[str]:
    """Normalised domain list; each one must be a schema's domain, a dotted
    child of one, or the family root of one."""
    from artmind.graph_query import normalize_domains

    domains = normalize_domains(value)
    known = _available_domains()
    for domain in domains:
        if not any(domain == k or domain.startswith(k + ".") or k.startswith(domain + ".") for k in known):
            raise ValueError(f"unknown domain {domain!r}")
    return domains


_LOAD_RE = re.compile(r"\bLOAD\b", re.IGNORECASE)


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CLAUSE_BLOCKLIST = frozenset(
    ["USE", "CALL", "LOAD", "SHOW", "CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "FOREACH", "FINISH", "TERMINATE", "GRANT", "DENY", "REVOKE", "ALTER", "RENAME", "START", "STOP", "ENABLE", "DISABLE", "PROFILE", "EXPLAIN", "CYPHER", "INSERT", "YIELD"]
)
_QUALIFIED_CALL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)+\s*\(")
_QUOTED_NAME_USE_RE = re.compile(r"`[ ]*`\s*[.(]")
_BARE_DOLLAR_RE = re.compile(r"\$(?![A-Za-z_])")


def _check_whitelist(masked: str) -> None:
    """Only plain read queries (MATCH/WHERE/WITH/RETURN/UNWIND/...) pass.

    Everything runs on the literal-masked text: a qualified function call is
    code, but `'apoc.x('` inside a string is data. The qualified-call rule is
    what closes APOC: a function that executes a string (apoc.cypher.run*,
    apoc.create.*) is only reachable through a namespaced call, so refusing every
    `ns.fn(` and every backtick-quoted name used as a namespace or function
    leaves only built-in functions. The keyword list is a second, plain-language
    layer for clauses and commands."""
    for word in _WORD_RE.findall(masked):
        if word.upper() in _CLAUSE_BLOCKLIST:
            raise ValueError(
                "only MATCH/OPTIONAL MATCH/WHERE/WITH/RETURN/UNWIND/ORDER BY/SKIP/LIMIT/UNION "
                f"queries can be drawn (found {word.upper()})"
            )
    if _QUALIFIED_CALL_RE.search(masked) or _QUOTED_NAME_USE_RE.search(masked):
        raise ValueError("namespaced or quoted function calls (apoc.x(), db.x(), `x`.y()) are not allowed in a graph block")
    if _BARE_DOLLAR_RE.search(masked):
        raise ValueError("only $domains may be a parameter; `$` must be followed by a name")


def _mask_literals(cypher: str) -> str:
    """The text with the contents of every string literal and backtick-quoted
    identifier replaced by spaces; quotes and length are kept. Follows Neo4j's
    lexer: '...' and "..." where a backslash escapes the next character, and
    `...` where a doubled backtick is an escaped backtick. Comments are not
    recognised here (they are refused before, outside literals)."""
    out: list[str] = []
    i, n = 0, len(cypher)
    while i < n:
        ch = cypher[i]
        if ch not in "'\"`":
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
        while True:
            if i >= n:
                raise ValueError("unterminated string in the Cypher")
            c = cypher[i]
            if ch != "`" and c == "\\":
                out.append(" " * min(2, n - i))
                i += 2
            elif c == ch:
                if ch == "`" and cypher[i + 1:i + 2] == "`":
                    out.append("  ")
                    i += 2
                    continue
                out.append(c)
                i += 1
                break
            else:
                out.append(" ")
                i += 1
    return "".join(out)[:n]


def prepare_cypher(cypher: str, *, require_domains: bool) -> str:
    """The Cypher to run, or ValueError. Read-only (the session is READ_ACCESS
    too; this is the friendly check), no CALL, no comments or LOAD, LIMIT 100
    when absent, and no parameter but `$domains`. Every textual check reads the
    text with string literals masked, so a value like 'Set up account' passes;
    the Cypher returned is the original."""
    from artmind.text2cypher import (
        _PARAM_RE,
        _prepare_graph_cypher,
        validate_domain_scoped,
        validate_read_only,
    )

    masked = _mask_literals(cypher)
    if "//" in masked or "/*" in masked:
        raise ValueError("comments are not allowed in a graph block")
    if _LOAD_RE.search(masked):
        raise ValueError("LOAD is not allowed in a graph block")
    validate_read_only(masked)
    _check_whitelist(masked)
    prepared_masked = _prepare_graph_cypher(masked)
    others = sorted({name for name in _PARAM_RE.findall(prepared_masked) if name != "domains"})
    if others:
        named = ", ".join(f"${name}" for name in others)
        raise ValueError(f"only $domains may be a parameter; write other values as literals (found {named})")
    if require_domains:
        validate_domain_scoped(prepared_masked)
    # the same trimming and LIMIT as _prepare_graph_cypher, applied to the original
    stripped = cypher.strip().rstrip(";").rstrip()
    suffix = prepared_masked[len(masked.strip().rstrip(";").rstrip()):]
    return stripped + suffix


def cap_value(value: Any) -> Any:
    """A value small enough for the wire: long text is cut at PROPERTY_CHARS_CAP
    characters, and a long list or map becomes its cut JSON text."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
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


ENDPOINT_CYPHER = f"MATCH (n) WHERE elementId(n) IN $ids AND {domain_predicate('n')} RETURN n"


EXPAND_CYPHER = (
    f"MATCH (n) WHERE elementId(n) = $id AND {domain_predicate('n')}\n"
    "OPTIONAL MATCH (n)-[r]-(m)\n"
    f"WHERE {domain_predicate('m')}\n"
    "RETURN n, r, m\n"
    "LIMIT 100"
)


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
        missing: dict[str, None] = {}
        for edge in self.edges.values():
            for nid in (edge["source"], edge["target"]):
                if nid not in self.nodes and nid not in self.omitted_node_ids:
                    missing[nid] = None
        return list(missing)

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

    from artmind.graph_query import neo4j_session

    collector = GraphCollector()
    rows_read = 0
    rows_capped = False
    with neo4j_session(READ_ACCESS, timeout=CONNECT_TIMEOUT_S) as session:
        result = session.run(Query(cypher, timeout=QUERY_TIMEOUT_S), {"domains": domains, **(extra_params or {})})
        for record in result:
            if rows_read >= ROW_CAP:
                rows_capped = True
                break
            rows_read += 1
            for value in record.values():
                collector.add_value(value)
        result.consume()  # stop the read here; the next run would otherwise buffer the rest
        missing = collector.missing_endpoint_ids()
        room = max(0, NODE_CAP - len(collector.nodes))
        collector.omitted_node_ids.update(missing[room:])
        if missing[:room]:
            fetched = session.run(Query(ENDPOINT_CYPHER, timeout=QUERY_TIMEOUT_S), {"ids": missing[:room], "domains": domains})
            for record in fetched:
                collector.add_value(record["n"])
            fetched.consume()
        collector.omitted_node_ids.update(nid for nid in missing[:room] if nid not in collector.nodes)
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


class GraphRunRequest(BaseModel):
    domain: str | list[str]
    cypher: str | None = None
    expand: str | None = None  # a node's element id: draw its one-hop neighbours

    @field_validator("cypher")
    @classmethod
    def _cypher(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("cypher must not be empty")
        if len(value) > MAX_CYPHER_CHARS:
            raise ValueError(f"cypher is longer than {MAX_CYPHER_CHARS} characters")
        return value

    @field_validator("expand")
    @classmethod
    def _expand(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value) > 200):
            raise ValueError("expand must be a node id")
        return value

    @model_validator(mode="after")
    def _one_of(self):
        if (self.cypher is None) == (self.expand is None):
            raise ValueError("send exactly one of cypher and expand")
        return self


class RateLimiter:
    """At most `limit` calls per `window_s` seconds per key (a sliding window).
    The route keys on the socket's peer address, so behind a reverse proxy all
    clients share one bucket."""

    def __init__(self, limit: int, window_s: float = 60.0, clock=time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            for k in list(self._hits):
                old = self._hits[k]
                while old and now - old[0] >= self.window_s:
                    old.popleft()
                if not old:
                    del self._hits[k]
            hits = self._hits.setdefault(key, deque())
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def _db_error(exc: Exception) -> HTTPException:
    if not isinstance(exc, Neo4jError):
        return HTTPException(status_code=502, detail="Neo4j is not reachable")
    code = getattr(exc, "code", None) or ""
    if "TransactionTimedOut" in code:
        return HTTPException(status_code=504, detail=f"the query ran longer than {QUERY_TIMEOUT_S} s and was stopped")
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    status = 400 if code.startswith("Neo.ClientError.Statement.") else 502
    return HTTPException(status_code=status, detail=f"Neo4j: {message.splitlines()[0]}")


def register_graph_routes(app: FastAPI, *, mode: str) -> FastAPI:
    if mode not in ("admin", "qa"):
        raise ValueError(f"mode must be 'admin' or 'qa', not {mode!r}")
    limiter = RateLimiter(QA_RUNS_PER_MINUTE) if mode == "qa" else None

    @app.post("/api/graph/run")
    async def api_graph_run(payload: GraphRunRequest, request: Request):
        if limiter is not None:
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                raise HTTPException(status_code=429, detail=f"at most {QA_RUNS_PER_MINUTE} graph runs a minute; try again shortly")
        try:
            domains = resolve_domains(payload.domain)
            if payload.expand is not None:
                cypher, extra = EXPAND_CYPHER, {"id": payload.expand}
            else:
                cypher, extra = prepare_cypher(payload.cypher, require_domains=mode == "qa"), None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            result = await anyio.to_thread.run_sync(run_graph, cypher, domains, extra)
        except (Neo4jError, DriverError) as exc:
            raise _db_error(exc) from exc
        logger.info(
            "graph run: mode=%s domains=%s chars=%d nodes=%d edges=%d truncated=%s",
            mode, ",".join(domains), len(cypher), len(result["nodes"]), len(result["edges"]), result["truncated"],
        )
        return {"domains": domains, "cypher": cypher, **result}

    return app
