"""Retrieval path, graph->vector fall-through and answer basis for one turn
(spec 2026-10-11 §4.1). Pure functions over span dicts as `store.list_spans`
returns them: `command`, `rows`, `exit_code`, `started_at`, `evidence`.
"""

import re
from urllib.parse import unquote

#: Layers that count as a graph *attempt* (graph_attempts column).
GRAPH_LAYERS = frozenset({"view", "graph", "text2cypher"})

#: Graph layers plus provenance (chunk fetches that support graph results).
#: All of these group as "graph" for answer-basis purposes.
GRAPH_SIDE = GRAPH_LAYERS | frozenset({"provenance"})

_EXACT = {
    "query views run": "view",
    "query graph text2cypher": "text2cypher",
    "query vector-text": "vector",
    "query chunks": "provenance",   # source text fetched by id for graph results
    "query text2sql": "sql",
    "query resolve-key": "sql",
    "query entity-context": "graph",
    "query entity-history": "graph",
    "query document-history": "graph",
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
    """`view` | `graph` | `text2cypher` | `provenance` | `vector` | `sql` | `discovery`.
    Unknown commands count as discovery: they locate things, they are not
    evidence for an answer."""
    if not command:
        return "discovery"
    if command in _EXACT:
        return _EXACT[command]
    if command.startswith("query graph pattern"):
        return "graph"
    return "discovery"


def is_dry_run(span: dict) -> bool:
    """A `--dry-run` text2cypher/text2sql span: it generated a query and ran nothing."""
    args = span.get("args")
    return isinstance(args, dict) and args.get("dry_run") is True


def span_layer(span: dict) -> str:
    """`layer_of` the span's command, except that a dry run is discovery:
    it executed nothing, so it is neither a graph attempt nor evidence."""
    if is_dry_run(span):
        return "discovery"
    return layer_of(span.get("command"))


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
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _group(layer: str) -> str:
    """Map a layer to its answer-basis group. provenance counts on the graph side."""
    return "graph" if layer in GRAPH_SIDE else layer


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


def _cited_groups(answer: str | None, productive_spans: list[dict]) -> set[str]:
    """The group each cited document is credited to, over all citations.

    A document returned by both a graph-side span and a vector span is
    credited to the graph: a vector-text cross-check that re-finds what the
    graph already supplied is not a fall-through. Returns an empty set when
    the answer has no Sources section or no citation matches any span."""
    cited = parse_sources(answer)
    if not cited:
        return set()
    found: set[str] = set()
    for path_str in cited:
        groups = set()
        for span in productive_spans:
            docs = (span.get("evidence") or {}).get("documents") or []
            if any(_same_document(path_str, d.get("path") or "") for d in docs):
                groups.add(_group(span_layer(span)))
        if "graph" in groups:
            groups = {"graph"}
        found |= groups
    return found


def answer_basis(answer: str | None, spans: list[dict]) -> str:
    productive = [s for s in spans if _productive(s) and span_layer(s) != "discovery"]
    if not productive:
        return "none"
    cg = _cited_groups(answer, productive)
    if cg:
        return _basis_from(cg)
    return _basis_from({_group(span_layer(s)) for s in productive})


def derive(spans: list[dict], answer: str | None) -> dict:
    ordered = sorted(spans, key=lambda s: s.get("started_at") or 0.0)
    path = []
    for span in ordered:
        layer = span_layer(span)
        if layer == "discovery":
            continue
        path.append({"span_id": span.get("span_id"), "layer": layer, "command": span.get("command"),
                     "rows": span.get("rows") or 0, "productive": _productive(span)})

    # graph_attempts counts only GRAPH_LAYERS spans (not provenance)
    graph_idx = [i for i, step in enumerate(path) if step["layer"] in GRAPH_LAYERS]
    graph_attempts = len(graph_idx)
    graph_productive = sum(1 for i in graph_idx if path[i]["productive"])

    # fell_through: graph was attempted, a vector-text span ran after the first
    # graph attempt, AND either the cited sources point to vector, OR no cited
    # document matched any span and the graph produced nothing.
    fell_through = False
    if graph_idx:
        first_graph_pos = graph_idx[0]
        vector_after = [i for i, step in enumerate(path)
                        if step["layer"] == "vector" and i > first_graph_pos]
        if vector_after:
            productive_spans = [s for s in ordered
                                if _productive(s) and span_layer(s) != "discovery"]
            cg = _cited_groups(answer, productive_spans)
            if cg:
                # Rule (a): cited sources include the vector layer
                fell_through = "vector" in cg
            else:
                # Rule (b): no cited source matched any span, and the graph side
                # returned nothing, so vector-text is the only plausible source.
                fell_through = (graph_productive == 0)

    return {
        "retrieval_path": path,
        "graph_attempts": graph_attempts,
        "graph_productive": graph_productive,
        "fell_through": fell_through,
        "answer_basis": answer_basis(answer, ordered),
    }


def is_graph_miss(turn: dict) -> bool:
    return bool(turn.get("fell_through")) and turn.get("answer_basis") == "vector"


#: Commands that may run before the Views check: Route picks the domain set
#: the check needs (artmind-query, "The first two commands, every time").
_BEFORE_VIEWS_OK = frozenset({"query domains-overview"})


def span_domains(span: dict) -> set[str] | None:
    """The `--domain` values a span ran with, comma-split; None when it named
    none (for `views list` that means every domain)."""
    args = span.get("args")
    raw: list = []
    if isinstance(args, dict):
        value = args.get("domain", args.get("domains"))
        raw = list(value) if isinstance(value, (list, tuple)) else ([value] if value else [])
    elif isinstance(args, list):
        for i, token in enumerate(args):
            if token == "--domain" and i + 1 < len(args):
                raw.append(args[i + 1])
            elif isinstance(token, str) and token.startswith("--domain="):
                raw.append(token.split("=", 1)[1])
    out = {d.strip() for item in raw if isinstance(item, str) for d in item.split(",") if d.strip()}
    return out or None


def _covers(listed: str, domain: str) -> bool:
    """`views list --domain` matches the domain family in both directions."""
    return listed == domain or domain.startswith(listed + ".") or listed.startswith(domain + ".")


def views_check(spans: list[dict], listed_before: set[str] | None = None, *,
                listed_all_before: bool = False) -> dict:
    """Did the turn check saved views before any other query?

    Any `query views ...` span counts as the check for its domains (`show`
    and `run` only name views a listing returned). `listed_before` /
    `listed_all_before` carry listings from earlier turns of the same
    conversation, which the skill says to reuse. Status:

    - `first`: every domain a query used was checked before that query ran;
    - `late`: checked, but only after some other query on that domain;
    - `skipped`: some queried domain was never checked;
    - `n/a`: nothing ran besides Route and listing/showing views.

    `views_available` is the largest `views list` row count this turn (None
    when it did not list), so `skipped`/`late` can be told apart from a
    domain that has no views at all only when some turn listed them.
    """
    listed = set(listed_before or ())
    listed_all = listed_all_before
    late = skipped = applicable = False
    available: int | None = None
    ran = False
    unchecked: set[str] = set()
    for span in sorted(spans, key=lambda s: s.get("started_at") or 0.0):
        command = span.get("command") or ""
        domains = span_domains(span)
        if command.startswith("query views"):
            if domains is None:
                listed_all = True
            else:
                listed |= domains
            if command == "query views list":
                available = max(available or 0, span.get("rows") or 0)
            if command == "query views run":
                applicable = True  # a view answering the question is the check working
                ran = ran or span.get("exit_code") == 0
            continue
        if command in _BEFORE_VIEWS_OK or not command.startswith("query"):
            continue
        applicable = True
        if listed_all or not domains:
            continue
        missing = {d for d in domains if not any(_covers(l, d) for l in listed)}
        if missing:
            late = True
            unchecked |= missing
    if unchecked and not listed_all:
        skipped = any(not any(_covers(l, d) for l in listed) for d in unchecked)
    status = "n/a" if not applicable else "skipped" if skipped else "late" if late else "first"
    return {"status": status, "listed": sorted(listed), "listed_all": listed_all,
            "views_available": available, "view_ran": ran}
