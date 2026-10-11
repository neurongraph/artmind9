"""Retrieval path, graph->vector fall-through and answer basis for one turn
(spec 2026-10-11 §4.1). Pure functions over span dicts as `store.list_spans`
returns them: `command`, `rows`, `exit_code`, `started_at`, `evidence`.
"""

import re
from pathlib import PurePosixPath
from urllib.parse import unquote

GRAPH_LAYERS = frozenset({"view", "graph", "text2cypher"})

_EXACT = {
    "query views run": "view",
    "query graph text2cypher": "text2cypher",
    "query vector-text": "vector",
    "query chunks": "vector",
    "query text2sql": "sql",
    "query resolve-key": "sql",
    "query entity-context": "graph",
    "query entity-history": "graph",
    "query graph entity-listing": "graph",
    "query graph filing-listing": "graph",
    "query graph timeline": "graph",
    "query graph hierarchy": "graph",
    "query graph conflicts": "graph",
}

_SOURCES_HEADING = re.compile(r"^#{2,4}\s*Sources\s*$", re.IGNORECASE | re.MULTILINE)
_ANY_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LINK = re.compile(r"\]\(\s*(?:<([^>]+)>|([^)\s]+))\s*\)")


def layer_of(command: str | None) -> str:
    """`view` | `graph` | `text2cypher` | `vector` | `sql` | `discovery`.
    Unknown commands count as discovery: they locate things, they are not
    evidence for an answer."""
    if not command:
        return "discovery"
    if command in _EXACT:
        return _EXACT[command]
    if command.startswith("query graph pattern"):
        return "graph"
    return "discovery"


def parse_sources(answer: str | None) -> list[str]:
    """Link targets listed under the answer's `### Sources` heading."""
    if not answer:
        return []
    match = _SOURCES_HEADING.search(answer)
    if not match:
        return []
    rest = answer[match.end():]
    nxt = _ANY_HEADING.search(rest)
    block = rest[: nxt.start()] if nxt else rest
    return [unquote(a or b) for a, b in _LINK.findall(block)]


def _norm(path: str) -> str:
    return unquote(path).strip().removeprefix("./").replace("\\", "/")


def _same_document(cited: str, held: str) -> bool:
    a, b = _norm(cited), _norm(held)
    if a == b or a.endswith("/" + b) or b.endswith("/" + a):
        return True
    return PurePosixPath(a).name == PurePosixPath(b).name


def _group(layer: str) -> str:
    return "graph" if layer in GRAPH_LAYERS else layer


def _productive(span: dict) -> bool:
    return span.get("exit_code") == 0 and (span.get("rows") or 0) > 0


def _basis_from(groups: set[str]) -> str:
    if not groups:
        return "none"
    if groups == {"sql"}:
        return "sql"
    if groups == {"graph"}:
        return "graph"
    if groups == {"vector"}:
        return "vector"
    return "mixed"


def answer_basis(answer: str | None, spans: list[dict]) -> str:
    productive = [s for s in spans if _productive(s) and layer_of(s.get("command")) != "discovery"]
    if not productive:
        return "none"
    found: set[str] = set()
    for cited in parse_sources(answer):
        for span in productive:
            docs = (span.get("evidence") or {}).get("documents") or []
            if any(_same_document(cited, d.get("path") or "") for d in docs):
                found.add(_group(layer_of(span["command"])))
    if found:
        return _basis_from(found)
    return _basis_from({_group(layer_of(s["command"])) for s in productive})


def derive(spans: list[dict], answer: str | None) -> dict:
    ordered = sorted(spans, key=lambda s: s.get("started_at") or 0.0)
    path = []
    for span in ordered:
        layer = layer_of(span.get("command"))
        if layer == "discovery":
            continue
        path.append({"span_id": span.get("span_id"), "layer": layer, "command": span.get("command"),
                     "rows": span.get("rows") or 0, "productive": _productive(span)})
    graph_idx = [i for i, step in enumerate(path) if step["layer"] in GRAPH_LAYERS]
    fell_through = bool(graph_idx) and any(step["layer"] == "vector" for step in path[graph_idx[-1] + 1:])
    return {
        "retrieval_path": path,
        "graph_attempts": len(graph_idx),
        "graph_productive": sum(1 for i in graph_idx if path[i]["productive"]),
        "fell_through": fell_through,
        "answer_basis": answer_basis(answer, ordered),
    }


def is_graph_miss(turn: dict) -> bool:
    return bool(turn.get("fell_through")) and turn.get("answer_basis") == "vector"
