"""Answerability patterns (spec 2026-10-11 §7.3): does a subgraph hold the
traversal a question needs? Pure; no I/O except `load_pattern_arg`'s @file.

A pattern is a JSON list: node and relationship steps alternate, starting and
ending with a node; `require` steps follow.

    [{"class": "PERSON", "name": "Mira"}, {"rel": "WENT_TO", "dir": "out"},
     {"class": "LOCATION"}, {"require": {"edge": 0, "properties": ["date"]}}]

`class`, `name`, `rel` compare case-insensitively; `dir` is out|in|any
(default out); `require` indexes node / relationship steps from 0. A
subgraph is {"nodes": [{id, name, class, properties}], "edges": [{source,
target, rel_type, properties}]}.
"""

import json
from pathlib import Path

MAX_MATCHES = 5
MAX_EXPLORED = 100_000
_DIRS = ("out", "in", "any")
_SKIP_RELS = frozenset({"extracted_from"})
_NODE_KEYS = frozenset({"class", "name"})
_REL_KEYS = frozenset({"rel", "dir"})
_REQUIRE_KEYS = frozenset({"node", "edge", "properties"})


class PatternError(ValueError):
    pass


def _cf(value) -> str:
    return str(value or "").strip().casefold()


def parse_pattern(pattern) -> tuple[list[dict], list[dict], list[dict]]:
    """Validate; return (node steps, relationship steps, require steps)."""
    if not isinstance(pattern, list) or not pattern:
        raise PatternError("a pattern is a non-empty JSON list")
    steps, requires = [], []
    for step in pattern:
        if not isinstance(step, dict):
            raise PatternError(f"every step is an object, got {step!r}")
        if "require" in step:
            requires.append(step["require"])
        elif requires:
            raise PatternError("require steps come after every node and relationship step")
        else:
            steps.append(step)
    if not steps or len(steps) % 2 == 0:
        raise PatternError("node and relationship steps alternate, starting and ending with a node")
    nodes, rels = steps[0::2], steps[1::2]
    for i, n in enumerate(nodes):
        unknown = sorted(set(n) - _NODE_KEYS)
        if unknown:
            raise PatternError(f"unknown key(s) in node step {i}: {unknown}")
    for i, r in enumerate(rels):
        unknown = sorted(set(r) - _REL_KEYS)
        if unknown:
            raise PatternError(f"unknown key(s) in relationship step {i}: {unknown}")
        if r.get("dir", "out") not in _DIRS:
            raise PatternError(f"dir must be one of {_DIRS}, got {r.get('dir')!r}")
    for req in requires:
        if not isinstance(req, dict) or not isinstance(req.get("properties"), list):
            raise PatternError("require is {node|edge: index, properties: [..]}")
        unknown = sorted(set(req) - _REQUIRE_KEYS)
        if unknown:
            raise PatternError(f"unknown key(s) in require step: {unknown}")
        if "node" in req and "edge" in req:
            raise PatternError("require names a node or an edge index, not both")
        if "node" in req:
            if not isinstance(req["node"], int) or not 0 <= req["node"] < len(nodes):
                raise PatternError(f"require node index {req.get('node')!r} is out of range")
        elif "edge" in req:
            if not isinstance(req["edge"], int) or not 0 <= req["edge"] < len(rels):
                raise PatternError(f"require edge index {req.get('edge')!r} is out of range")
        else:
            raise PatternError("require names a node or an edge index")
    return nodes, rels, requires


def load_pattern_arg(value: str) -> list:
    """A CLI --pattern value: inline JSON, or @path to a JSON file."""
    try:
        text = Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value
        pattern = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise PatternError(f"--pattern is not valid JSON (or @file): {exc}") from exc
    parse_pattern(pattern)
    return pattern


def _node_ok(step: dict, node: dict) -> bool:
    if "class" in step and _cf(step["class"]) != _cf(node.get("class")):
        return False
    if "name" in step and _cf(step["name"]) != _cf(node.get("name")):
        return False
    return True


def _filled(value) -> bool:
    return value not in (None, "", [], {})


def _has(item: dict, prop: str) -> bool:
    if prop in ("name", "class", "rel_type") and _filled(item.get(prop)):
        return True
    return _filled((item.get("properties") or {}).get(prop))


def match(pattern, subgraph: dict, *, limit: int = MAX_MATCHES, max_explored: int = MAX_EXPLORED) -> dict:
    node_steps, rel_steps, requires = parse_pattern(pattern)
    nodes = {n["id"]: n for n in subgraph.get("nodes") or []}
    out_edges: dict[str, list[dict]] = {}
    in_edges: dict[str, list[dict]] = {}
    for edge in subgraph.get("edges") or []:
        out_edges.setdefault(edge["source"], []).append(edge)
        in_edges.setdefault(edge["target"], []).append(edge)

    found: list[list[str]] = []
    count = 0
    walk_calls = 0
    truncated = False

    def satisfied(path_nodes, path_edges) -> bool:
        for req in requires:
            item = path_nodes[req["node"]] if "node" in req else path_edges[req["edge"]]
            if not all(_has(item, p) for p in req["properties"]):
                return False
        return True

    def walk(i: int, path_nodes: list[dict], path_edges: list[dict]) -> None:
        nonlocal count, walk_calls, truncated
        walk_calls += 1
        if walk_calls > max_explored:
            truncated = True
            return
        if i == len(node_steps):
            if satisfied(path_nodes, path_edges):
                count += 1
                if len(found) < limit:
                    found.append([n.get("name") for n in path_nodes])
            return
        rel = rel_steps[i - 1]
        here = path_nodes[-1]["id"]
        direction = rel.get("dir", "out")
        candidates = []
        if direction in ("out", "any"):
            candidates += [(e, e["target"]) for e in out_edges.get(here, [])]
        if direction in ("in", "any"):
            candidates += [(e, e["source"]) for e in in_edges.get(here, [])]
        for edge, other in candidates:
            if truncated:
                return
            if "rel" in rel and _cf(rel["rel"]) != _cf(edge.get("rel_type")):
                continue
            node = nodes.get(other)
            if node is None or not _node_ok(node_steps[i], node) or node in path_nodes:
                continue
            walk(i + 1, path_nodes + [node], path_edges + [edge])

    for node in nodes.values():
        if truncated:
            break
        if _node_ok(node_steps[0], node):
            walk(1, [node], [])
    return {"matched": count > 0, "count": count, "matches": found, "truncated": truncated}


_OBS_META = frozenset({
    "id", "name", "canonical_name", "key", "entity_class", "_domain", "doc_id", "doc_version", "chunk_id",
    "_kind", "type", "description", "context", "aliases", "_valid_from", "_valid_to", "_valid_time_source",
    "_doc_valid_from", "_retracts", "embedding",
})


def _staging_node_id(name, cls) -> str:
    return f"{_cf(name)}|{_cf(cls)}"


def from_staging(observations: list[dict], relationships: list[dict]) -> dict:
    """Staging observations/relationships -> subgraph. Nodes merge by
    (name, class); edges join by name (as commit does), EXTRACTED_FROM skipped.
    Edges join by name only (as commit does); when one name is shared across
    classes, the first-seen class's node takes the edge."""
    nodes: dict[str, dict] = {}
    by_name: dict[str, str] = {}
    for obs in observations:
        nid = _staging_node_id(obs.get("name"), obs.get("entity_class"))
        node = nodes.setdefault(nid, {"id": nid, "name": obs.get("name"), "class": obs.get("entity_class"),
                                      "properties": {}})
        node["properties"].update({k: v for k, v in obs.items() if k not in _OBS_META})
        by_name.setdefault(_cf(obs.get("name")), nid)
        if obs.get("canonical_name"):
            by_name.setdefault(_cf(obs.get("canonical_name")), nid)
    edges = []
    for rel in relationships:
        if _cf(rel.get("rel_type")) in _SKIP_RELS:
            continue
        src, tgt = by_name.get(_cf(rel.get("source_name"))), by_name.get(_cf(rel.get("target_name")))
        if src and tgt:
            edges.append({"source": src, "target": tgt, "rel_type": rel.get("rel_type"),
                          "properties": rel.get("properties") or {}})
    return {"nodes": list(nodes.values()), "edges": edges}


def from_extraction(entities: list[dict], relationships: list[dict]) -> dict:
    """A fresh extraction (LLM ids) -> subgraph. Dangling ids are dropped."""
    nodes = {e["id"]: {"id": e["id"], "name": e.get("name"), "class": e.get("entity_class"),
                       "properties": e.get("properties") or {}} for e in entities if e.get("id")}
    edges = [{"source": r["source_id"], "target": r["target_id"], "rel_type": r.get("rel_type"),
              "properties": r.get("properties") or {}}
             for r in relationships if r.get("source_id") in nodes and r.get("target_id") in nodes]
    return {"nodes": list(nodes.values()), "edges": edges}
