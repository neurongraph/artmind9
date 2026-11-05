"""Plan programs: the grammar of `routes.yaml` v2 (spec 2026-11-05 planner v2, §5).

A program is a tuple of `Node`s plus a tuple of `Guard`s. This module only
parses and validates; nothing here runs a command. Predicates are parsed into
a small AST (`Predicate`), never evaluated with `eval`.

Limits, checked at load (spec §5.1 gives "at most 12 run nodes after `map`
expansion is bounded by `max` (default 10, at most 40), at most 3 levels"):

* **Written run nodes <= 12.** Every `run` counts once; a `map`'s `do` body is
  counted once however large `max` is (it is one node in the file).
* **Executed run calls <= 40.** The same count with a `map` expanded: its body's
  run nodes times `max`. `max` is 1..40 (default 10), so one map alone can
  reach the ceiling but no program can exceed 40 commands.
* **Nesting <= 3 levels.** A top-level node is level 1; the children of a
  `par` and the `do` of a `map` are one level deeper.

Converted v1 routes (`program_from_v1`) are sequences of `run` nodes `s1`, `s2`,
... with their fallbacks as guards (see that function for the mapping).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

MAX_WRITTEN_RUN_NODES = 12
MAX_EXECUTED_RUN_CALLS = 40
MAX_NESTING = 3
MAP_MAX_DEFAULT = 10
MAP_MAX_LIMIT = 40
PAGE_K_LIMIT = 500

#: Named constants a predicate's right-hand side may use (spec §11).
CONSTANTS: dict[str, float] = {"NEEDLE_MARGIN": 0.03}

#: Predicate vocabulary (spec §5.3) and each name's type.
PREDICATE_TYPES: dict[str, str] = {
    "rows": "num", "rows_total": "num", "truncated": "bool",
    "distinct_documents": "num", "docs_cited": "num",
    "max_relevance": "num", "score_drop": "num",
    "payload_chars": "num", "persisted": "bool",
    "entity_docs": "num", "rows_below_k": "num", "twins": "num",
    "stale": "bool", "view_status": "enum", "history_matches": "num",
    "concentration": "num", "level_agreement": "num",
}
PREDICATE_NAMES = frozenset(PREDICATE_TYPES)
OPS = ("==", "!=", "<=", ">=", "<", ">")
NODE_KINDS = ("run", "par", "map", "pick", "page")
PICK_HOWS = frozenset({"best_of", "union", "first_ok"})
PAGE_UNTIL = frozenset({"rows_total", "k"})
GUARD_ACTIONS = ("escalate", "replace", "retry_with", "widen_domains", "page", "run")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SLOT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ProgramError(ValueError):
    """A program breaks a rule of spec §5. `routes.RouteError` wraps it."""


# ── predicates ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Cmp:
    """`[node.]name op value`; a bare boolean name is `name == True`."""

    name: str
    node: str | None
    op: str
    value: float | bool | str  # number, bool, or an enum word (view_status)
    const: str | None = None  # the UPPER_CASE constant the value came from


@dataclass(frozen=True)
class Not:
    operand: object


@dataclass(frozen=True)
class And:
    operands: tuple


@dataclass(frozen=True)
class Predicate:
    text: str
    ast: object

    def comparisons(self) -> list[Cmp]:
        out: list[Cmp] = []

        def walk(node):
            if isinstance(node, Cmp):
                out.append(node)
            elif isinstance(node, Not):
                walk(node.operand)
            elif isinstance(node, And):
                for operand in node.operands:
                    walk(operand)

        walk(self.ast)
        return out

    def nodes(self) -> set[str]:
        return {c.node for c in self.comparisons() if c.node}


_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<word>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)"
    r"|(?P<op>==|!=|<=|>=|<|>))"
)


def _tokens(text: str, where: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise ProgramError(f"{where}: cannot read predicate {text!r} at {text[pos:pos + 12]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def parse_predicate(text, where: str) -> Predicate:
    """`<name> <op> <number|bool|CONSTANT>` or a bare boolean name, joined by `and` / `not`."""
    if not isinstance(text, str) or not text.strip():
        raise ProgramError(f"{where}: a predicate must be a non-empty string")
    tokens = _tokens(text, where)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else (None, None)

    def take():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        return tok

    def parse_and():
        operands = [parse_not()]
        while peek() == ("word", "and"):
            take()
            operands.append(parse_not())
        return operands[0] if len(operands) == 1 else And(tuple(operands))

    def parse_not():
        if peek() == ("word", "not"):
            take()
            return Not(parse_not())
        return parse_atom()

    def parse_atom():
        kind, word = peek()
        if kind != "word" or word in ("and", "not", "true", "false"):
            raise ProgramError(f"{where}: expected a predicate name in {text!r}")
        take()
        node, _, name = word.rpartition(".")
        if name not in PREDICATE_NAMES:
            raise ProgramError(f"{where}: unknown predicate name {name!r}; use {sorted(PREDICATE_NAMES)}")
        if node and not _ID_RE.match(node):
            raise ProgramError(f"{where}: bad node qualifier {node!r} in {text!r}")
        ptype = PREDICATE_TYPES[name]
        kind, op = peek()
        if kind != "op":
            if ptype != "bool":
                raise ProgramError(f"{where}: {name!r} needs a comparison (e.g. {name} > 0)")
            return Cmp(name, node or None, "==", True)
        take()
        kind, rhs = peek()
        if kind is None:
            raise ProgramError(f"{where}: {text!r} ends after {op!r}")
        take()
        const = None
        if kind == "num":
            value: float | bool | str = float(rhs) if "." in rhs else int(rhs)
            actual = "num"
        elif rhs in ("true", "false"):
            value, actual = rhs == "true", "bool"
        elif rhs in CONSTANTS:
            value, actual, const = CONSTANTS[rhs], "num", rhs
        elif re.fullmatch(r"[A-Z][A-Z0-9_]*", rhs):
            raise ProgramError(f"{where}: unknown constant {rhs!r}; use {sorted(CONSTANTS)}")
        elif ptype == "enum" and re.fullmatch(r"[a-z][a-z0-9_]*", rhs):
            value, actual = rhs, "enum"
        else:
            raise ProgramError(f"{where}: bad value {rhs!r} in {text!r}")
        if actual != ptype:
            raise ProgramError(f"{where}: {name!r} is {ptype}, not comparable with a {actual} value")
        if ptype != "num" and op not in ("==", "!="):
            raise ProgramError(f"{where}: {name!r} only supports == and !=")
        return Cmp(name, node or None, op, value, const)

    ast = parse_and()
    if pos != len(tokens):
        raise ProgramError(f"{where}: unexpected {tokens[pos][1]!r} in {text!r}")
    return Predicate(text.strip(), ast)


# ── nodes and guards ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PageSpec:
    until: str  # rows_total | k
    k: int | None = None
    from_: str | None = None  # run node id; a guard's page action takes its `on` node


@dataclass(frozen=True)
class PickSpec:
    from_: str
    how: str
    score: tuple[Predicate, ...] = ()


@dataclass(frozen=True)
class MapSpec:
    over: str
    as_: str
    do: "Node"
    max: int = MAP_MAX_DEFAULT


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # run | par | map | pick | page
    run: str | None = None
    children: tuple["Node", ...] = ()  # par
    map: MapSpec | None = None
    pick: PickSpec | None = None
    page: PageSpec | None = None
    when: Predicate | None = None


@dataclass(frozen=True)
class GuardAction:
    kind: str  # escalate | replace | retry_with | widen_domains | page | run
    route: str | None = None  # replace
    slots: tuple[tuple[str, str], ...] = ()  # retry_with
    page: PageSpec | None = None  # page
    run: str | None = None  # run: only on guards converted from a v1 fallback


@dataclass(frozen=True)
class Guard:
    on: str
    when: Predicate
    then: GuardAction
    v1_when: str | None = None  # the v1 fallback condition this guard came from


# ── parsing ──────────────────────────────────────────────────────────────────

TemplateCheck = Callable[[object, str, frozenset], str]


def _node_id(value, where: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ProgramError(f"{where}: id must be snake_case")
    return value


def _page_spec(raw, where: str, *, need_from: bool) -> PageSpec:
    if not isinstance(raw, dict):
        raise ProgramError(f"{where} must be a mapping")
    allowed = {"until", "k", "from"}
    if set(raw) - allowed:
        raise ProgramError(f"{where} has unknown key(s) {sorted(set(raw) - allowed)}")
    until = raw.get("until")
    if until not in PAGE_UNTIL:
        raise ProgramError(f"{where}.until must be one of {sorted(PAGE_UNTIL)}")
    k = raw.get("k")
    if until == "k":
        if type(k) is not int or not 1 <= k <= PAGE_K_LIMIT:
            raise ProgramError(f"{where}.k must be an integer 1-{PAGE_K_LIMIT} when until is k")
    elif k is not None:
        raise ProgramError(f"{where}.k only goes with until: k")
    src = raw.get("from")
    if need_from:
        _node_id(src, f"{where}.from")
    elif src is not None:
        raise ProgramError(f"{where} in a guard acts on the guard's `on` node; drop `from`")
    return PageSpec(until, k, src)


def _parse_node(raw, where: str, check: TemplateCheck, extra: frozenset, depth: int) -> Node:
    if depth > MAX_NESTING:
        raise ProgramError(f"{where}: nesting deeper than {MAX_NESTING} levels")
    if not isinstance(raw, dict):
        raise ProgramError(f"{where} must be a mapping")
    nid = _node_id(raw.get("id"), where)
    where = f"{where} ({nid})"
    kinds = [k for k in NODE_KINDS if k in raw]
    if len(kinds) != 1:
        raise ProgramError(f"{where} must have exactly one of {list(NODE_KINDS)}")
    kind = kinds[0]
    extra_keys = set(raw) - {"id", "when", kind}
    if extra_keys:
        raise ProgramError(f"{where} has unknown key(s) {sorted(extra_keys)}")
    when = parse_predicate(raw["when"], f"{where}.when") if "when" in raw else None
    body = raw[kind]
    if kind == "run":
        return Node(nid, kind, run=check(body, f"{where}.run", extra), when=when)
    if kind == "par":
        if not isinstance(body, list) or not body:
            raise ProgramError(f"{where}.par must be a non-empty list of nodes")
        kids = tuple(_parse_node(c, f"{where}.par[{i}]", check, extra, depth + 1) for i, c in enumerate(body))
        return Node(nid, kind, children=kids, when=when)
    if kind == "map":
        if not isinstance(body, dict) or set(body) - {"over", "as", "do", "max"} or not {"over", "as", "do"} <= set(body):
            raise ProgramError(f"{where}.map must be {{over, as, do[, max]}}")
        over, as_ = body["over"], body["as"]
        for label, value in (("over", over), ("as", as_)):
            if not isinstance(value, str) or not _SLOT_RE.match(value):
                raise ProgramError(f"{where}.map.{label} must be a snake_case name")
        limit = body.get("max", MAP_MAX_DEFAULT)
        if type(limit) is not int or not 1 <= limit <= MAP_MAX_LIMIT:
            raise ProgramError(f"{where}.map.max must be an integer 1-{MAP_MAX_LIMIT}")
        do = _parse_node(body["do"], f"{where}.map.do", check, extra | {as_}, depth + 1)
        return Node(nid, kind, map=MapSpec(over, as_, do, limit), when=when)
    if kind == "pick":
        if not isinstance(body, dict) or set(body) - {"from", "how", "score"} or not {"from", "how"} <= set(body):
            raise ProgramError(f"{where}.pick must be {{from, how[, score]}}")
        if body["how"] not in PICK_HOWS:
            raise ProgramError(f"{where}.pick.how must be one of {sorted(PICK_HOWS)}")
        score = body.get("score") or []
        if not isinstance(score, list):
            raise ProgramError(f"{where}.pick.score must be a list of predicates")
        if score and body["how"] != "best_of":
            raise ProgramError(f"{where}.pick.score only goes with how: best_of")
        preds = tuple(parse_predicate(p, f"{where}.pick.score[{i}]") for i, p in enumerate(score))
        return Node(nid, kind, pick=PickSpec(_node_id(body["from"], f"{where}.pick.from"), body["how"], preds), when=when)
    return Node(nid, kind, page=_page_spec(body, f"{where}.page", need_from=True), when=when)


def parse_program(raw, where: str, check: TemplateCheck, extra: frozenset = frozenset()) -> tuple[Node, ...]:
    """Parse and validate a `program:` list. `check(template, where, extra)` validates a run template."""
    if not isinstance(raw, list) or not raw:
        raise ProgramError(f"{where} must be a non-empty list of nodes")
    nodes = tuple(_parse_node(n, f"{where}[{i}]", check, extra, 1) for i, n in enumerate(raw))
    validate_program(nodes, where)
    return nodes


def iter_nodes(program):
    """Every node in document order (a node before its children; a map's `do` after the map)."""
    for node in program:
        yield node
        if node.kind == "par":
            yield from iter_nodes(node.children)
        elif node.kind == "map":
            yield from iter_nodes((node.map.do,))


def run_nodes(program) -> tuple[Node, ...]:
    return tuple(n for n in iter_nodes(program) if n.kind == "run")


def executed_run_calls(program) -> int:
    """Run commands one execution can issue: a map's body counts `max` times."""
    total = 0
    for node in program:
        if node.kind == "run":
            total += 1
        elif node.kind == "par":
            total += executed_run_calls(node.children)
        elif node.kind == "map":
            total += node.map.max * executed_run_calls((node.map.do,))
    return total


def validate_program(program, where: str) -> None:
    order = list(iter_nodes(program))
    ids: dict[str, int] = {}
    for i, node in enumerate(order):
        if node.id in ids:
            raise ProgramError(f"{where}: duplicate node id {node.id!r}")
        ids[node.id] = i
    kinds = {n.id: n.kind for n in order}
    written = sum(1 for n in order if n.kind == "run")
    if not written:
        raise ProgramError(f"{where} has no run node")
    if written > MAX_WRITTEN_RUN_NODES:
        raise ProgramError(f"{where} has {written} run nodes; at most {MAX_WRITTEN_RUN_NODES}")
    calls = executed_run_calls(program)
    if calls > MAX_EXECUTED_RUN_CALLS:
        raise ProgramError(f"{where} can execute {calls} run commands after map expansion; at most {MAX_EXECUTED_RUN_CALLS}")
    for i, node in enumerate(order):
        loc = f"{where}: node {node.id!r}"
        preds = [node.when] if node.when else []
        if node.pick:
            preds += list(node.pick.score)
        for pred in preds:
            for ref in pred.nodes():
                if ref not in ids:
                    raise ProgramError(f"{loc}: predicate {pred.text!r} names unknown node {ref!r}")
                if ids[ref] >= i:
                    raise ProgramError(f"{loc}: predicate {pred.text!r} names node {ref!r}, which has not run yet")
        if node.when:
            bare = [c.name for c in node.when.comparisons() if not c.node]
            if bare:
                raise ProgramError(f"{loc}: `when` must qualify {bare} with a node id (node.name)")
        if node.pick:
            src = node.pick.from_
            if src not in ids or ids[src] >= i:
                raise ProgramError(f"{loc}: pick.from {src!r} is not an earlier node")
            if kinds[src] not in ("par", "map"):
                raise ProgramError(f"{loc}: pick.from must name a par or map node, not {kinds[src]}")
        if node.page:
            src = node.page.from_
            if src not in ids or ids[src] >= i:
                raise ProgramError(f"{loc}: page.from {src!r} is not an earlier node")
            if kinds[src] != "run":
                raise ProgramError(f"{loc}: page.from must name a run node, not {kinds[src]}")


def parse_guards(raw, program, where: str, *, route_ids: frozenset | None = None) -> tuple[Guard, ...]:
    """Parse `guards:` against a validated program. `route_ids`, when given, resolves `replace`."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ProgramError(f"{where} must be a list")
    ids = {n.id: n for n in iter_nodes(program)}
    out = []
    for i, g in enumerate(raw):
        loc = f"{where}[{i}]"
        if not isinstance(g, dict) or set(g) != {"on", "if", "then"}:
            raise ProgramError(f"{loc} must be {{on, if, then}}")
        on = g["on"]
        if not isinstance(on, str) or on not in ids:
            raise ProgramError(f"{loc}.on names unknown node {on!r}")
        pred = parse_predicate(g["if"], f"{loc}.if")
        for ref in pred.nodes():
            if ref not in ids:
                raise ProgramError(f"{loc}.if names unknown node {ref!r}")
        out.append(Guard(on, pred, _parse_action(g["then"], on, ids, f"{loc}.then", route_ids)))
    return tuple(out)


def _parse_action(raw, on: str, ids: dict, where: str, route_ids: frozenset | None) -> GuardAction:
    if raw in ("escalate", "widen_domains"):
        return GuardAction(raw)
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ProgramError(f"{where} must be 'escalate', 'widen_domains' or a one-key mapping of {list(GUARD_ACTIONS[1:-1])}")
    (key, body), = raw.items()
    if key == "replace":
        if not isinstance(body, str) or not _ID_RE.match(body):
            raise ProgramError(f"{where}.replace must be a route id")
        if route_ids is not None and body not in route_ids:
            raise ProgramError(f"{where}.replace names unknown route {body!r}")
        return GuardAction("replace", route=body)
    if key == "retry_with":
        if not isinstance(body, dict) or not body or not all(isinstance(k, str) and _SLOT_RE.match(k) for k in body):
            raise ProgramError(f"{where}.retry_with must be a non-empty mapping of slot name to value")
        if not all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in body.values()):
            raise ProgramError(f"{where}.retry_with values must be strings or numbers")
        return GuardAction("retry_with", slots=tuple((k, str(v)) for k, v in body.items()))
    if key == "page":
        if ids[on].kind != "run":
            raise ProgramError(f"{where}.page needs `on` to be a run node")
        return GuardAction("page", page=_page_spec(body, f"{where}.page", need_from=False))
    if key == "run":
        raise ProgramError(f"{where}: `run` is only for guards converted from a v1 fallback")
    raise ProgramError(f"{where} has unknown action {key!r}")


def validate_replacements(guards, own_route: str, route_ids: frozenset, where: str) -> None:
    for g in guards:
        if g.then.kind == "replace":
            if g.then.route == own_route:
                raise ProgramError(f"{where}: guard replaces its own route")
            if g.then.route not in route_ids:
                raise ProgramError(f"{where}: guard replaces unknown route {g.then.route!r}")


# ── v1 routes as programs ────────────────────────────────────────────────────

#: v1 fallback condition -> guard predicate. v1 fires a fallback when "the first
#: step returns <when>"; the guard is on node s1. A failed step reads as no rows
#: (`error`), a missing document as no rows (`no_document`), and ambiguous entity
#: resolution as the step's entity having twins (`needs_disambiguation`).
V1_FALLBACK_PREDICATES = {
    "zero_rows": "rows == 0",
    "truncated": "truncated",
    "no_document": "rows == 0",
    "needs_disambiguation": "twins > 0",
    "error": "rows == 0",
}


def program_from_v1(steps, fallbacks) -> tuple[tuple[Node, ...], tuple[Guard, ...]]:
    """Read a v1 route as a program: `steps` become `run` nodes s1, s2, ...; each fallback a guard on s1.

    The guard action is `{run: <template>}` ("run this instead"), valid only here.
    `variants` and `optional_steps` stay on the `Route`; the program covers `steps`.
    """
    program = tuple(Node(f"s{i}", "run", run=cmd) for i, cmd in enumerate(steps, 1))
    guards = tuple(
        Guard("s1", parse_predicate(V1_FALLBACK_PREDICATES[when], "v1 fallback"), GuardAction("run", run=cmd), v1_when=when)
        for when, cmd in fallbacks
    )
    return program, guards
