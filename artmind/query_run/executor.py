"""Run a plan's program (P3 spec §3.5–§3.8).

Input is the plan payload as `planner.plan` returns it: `program` (node JSON with every plan-time
slot rendered), `guards`, `replacements` (rendered programs of the routes a guard may replace with,
plus `search`), `suggested_steps`, `fallbacks`. The planner fills; the executor runs and never
re-reads routes.yaml.

`par` runs its children in order (sequential in P3, spec §13 Q3). Guards are evaluated after their
`on` node, first match wins, each at most once. A `replace` runs the replacement program once; a
replacement cannot replace again. Guard actions no packaged route uses yet (`run`, `retry_with`,
`widen_domains`) escalate.
"""

from __future__ import annotations

import re as _re
import shlex
import time
from dataclasses import dataclass, field
from typing import Callable

from artmind.query_plan import program as program_mod
from artmind.query_run import capture, facts as facts_mod, opening

DEFAULT_MAX_STEPS = 12
DEFAULT_DEADLINE_S = 20.0
#: Largest `--limit` a page node may re-run a command with (graph_query.PATTERN7_MAX_LIMIT; a test pins it).
PAGE_LIMITS = {"query graph pattern7": 500}
_UNSUPPORTED = ("run", "retry_with", "widen_domains")


@dataclass
class Execution:
    results: dict = field(default_factory=dict)     # run node id -> StepResult (ran or refused)
    facts: dict = field(default_factory=dict)       # node id -> facts
    path: list = field(default_factory=list)
    escalate: dict | None = None
    executed: int = 0
    replaced_by: str | None = None
    answer_ids: list = field(default_factory=list)
    partial: bool = False
    exec_ms: float = 0.0
    unfilled: bool = False
    opened: list = field(default_factory=list)     # [{doc_id, why}] the open step asked for (spec 6.5)

    @property
    def exit_code(self) -> int:
        ran = [r for r in self.results.values() if r.error != "not_allowed"]
        return 3 if ran and all(r.exit_code == 3 for r in ran) else 0


class _Stop(Exception):
    def __init__(self, reason: str, **extra):
        super().__init__(reason)
        self.reason, self.extra = reason, extra


class _Replace(Exception):
    def __init__(self, route: str):
        super().__init__(route)
        self.route = route


_BIND_TOKEN = _re.compile(r"^\{([a-z][a-z0-9_]*)\.([a-z_]+)\}$")


def _pred(text: str):
    return program_mod.parse_predicate(text, "query run")


def _cut(text, n=300) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


class _Run:
    def __init__(self, plan, step_fn, max_steps, deadline_s, clock):
        self.plan, self.step_fn, self.max_steps, self.deadline_s, self.clock = plan, step_fn, max_steps, deadline_s, clock
        self.ex = Execution()
        self.start = clock()
        self.fired: set[int] = set()
        self.guards: list = []
        self.program: list = []
        self.outputs: dict = {}
        self.open_documents = False

    # ── walking ──────────────────────────────────────────────────────────
    def nodes(self, nodes) -> None:
        for i, node in enumerate(nodes):
            self.node(node, nodes[i + 1:])

    def node(self, node, rest) -> None:
        if node.get("when") and node.get("kind") != "pick" and not facts_mod.evaluate(_pred(node["when"]), self.ex.facts, node["id"]):
            self.ex.path.append({"id": node["id"], "kind": node.get("kind"), "skipped": "when"})
            return
        kind = node.get("kind")
        if kind == "run":
            self.run(node)
        elif kind == "par":
            self.par(node, rest)
        elif kind == "map":
            self.map(node)
        elif kind == "pick":
            if not self.pick(node):
                return
        elif kind == "page":
            self.page(node["id"], node["page"]["from"], node["page"])
        self.apply_guards(node["id"])

    def run(self, node, argv_override=None) -> None:
        nid = node["id"]
        if node.get("needs_unmet"):
            self.ex.path.append({"id": nid, "kind": "run", "skipped": "needs"})
            return
        if not node.get("filled", True):
            if not node.get("optional"):
                self.ex.unfilled = True
            self.ex.path.append({"id": nid, "kind": "run", "skipped": "unfilled"})
            return
        if node.get("binds") and argv_override is None:
            argv_override = self._bind(node.get("run") or "")
            if argv_override is None:
                self.ex.path.append({"id": nid, "kind": "run", "skipped": "unbound"})
                return
        argv = argv_override or capture.argv_of(node.get("run") or "")
        if argv is None or not capture.allowed(argv):
            self.ex.results[nid] = capture.StepResult(nid, argv or [], 1, "not_allowed", None, 0.0, 0)
            self.ex.path.append({"id": nid, "kind": "run", "cmd": _cut(node.get("run")), "exit_code": 1, "error": "not_allowed"})
            return
        if self.ex.executed >= self.max_steps:
            self.ex.path.append({"id": nid, "kind": "run", "skipped": "limit"})
            self.ex.partial = True
            raise _Stop("limit", node=nid)
        if self.clock() - self.start > self.deadline_s:
            self.ex.path.append({"id": nid, "kind": "run", "skipped": "deadline"})
            self.ex.partial = True
            raise _Stop("deadline", node=nid)
        result = self.step_fn(argv, nid)
        self.ex.executed += 1
        self.ex.results[nid] = result
        self.ex.facts[nid] = facts_mod.step_facts(result.payload, argv)
        self.ex.path.append({"id": nid, "kind": "run", "cmd": _cut(" ".join(["artmind", *argv])),
                             "exit_code": result.exit_code, "error": result.error, "rows": self.ex.facts[nid]["rows"],
                             "ms": result.ms, "chars": result.chars, "skipped": None,
                             "facts": facts_mod.public(self.ex.facts[nid])})

    def par(self, node, rest) -> None:
        children = node.get("children") or []
        pick = next((n for n in rest if n.get("kind") == "pick" and (n.get("pick") or {}).get("from") == node["id"]
                     and n.get("when")), None)
        ran: list[str] = []
        for i, child in enumerate(children):
            self.node(child, [])
            ran.append(child["id"])
            if pick is not None and i + 1 < len(children):
                pred = _pred(pick["when"])
                if pred.nodes() and pred.nodes() <= set(ran) and not facts_mod.evaluate(pred, self.ex.facts, pick["id"]):
                    for skipped in children[i + 1:]:
                        self.ex.path.append({"id": skipped["id"], "kind": skipped.get("kind"), "skipped": "short_circuit"})
                    break
        self.ex.facts[node["id"]] = facts_mod.merge([self.ex.facts[c["id"]] for c in children if c["id"] in self.ex.facts])

    def map(self, node) -> None:
        spec = node.get("map") or {}
        if spec.get("over_bound"):
            src, output = spec["over_bound"].split(".", 1)
            items = list(self._outputs(src).get(output) or [])[: int(spec.get("max") or 10)]
            do = spec.get("do") or {}
            as_ = spec.get("as")
            expanded = []
            if not do.get("filled", True):
                if not do.get("optional"):
                    self.ex.unfilled = True
                self.ex.path.append({"id": node["id"], "kind": "map", "skipped": "unfilled"})
                items = []
            for item in items:
                if str(item).startswith("-"):  # same rule as routes.render: never an option
                    continue
                child = dict(do)
                child["run"] = (do.get("run") or "").replace("{" + as_ + "}", shlex.quote(str(item)))
                child["filled"] = True
                expanded.append(child)
            if not expanded and not any(p.get("id") == node["id"] and p.get("skipped") for p in self.ex.path):
                self.ex.path.append({"id": node["id"], "kind": "map", "skipped": "unbound"})
            spec = {**spec, "expanded": expanded}
            node = {**node, "map": spec}
        ids = []
        for i, child in enumerate(spec.get("expanded") or [], 1):
            unique = {**child, "id": f"{child['id']}.{i}"}
            self.node(unique, [])
            ids.append(unique["id"])
        merged = facts_mod.merge([self.ex.facts[i] for i in ids if i in self.ex.facts])
        self.ex.facts[node["id"]] = merged
        do_id = (spec.get("do") or {}).get("id")
        if do_id:
            self.ex.facts[do_id] = merged

    def _outputs(self, node_id: str) -> dict:
        if node_id not in self.outputs:
            result = self.ex.results.get(node_id)
            payload = result.payload if result is not None and result.exit_code == 0 else None
            if payload is None and node_id in self.ex.facts:  # a pick or map: bind over the steps it kept
                pick = next((n for n in _walk(self.program) if n.get("id") == node_id and n.get("kind") == "pick"), None)
                source = (pick.get("pick") or {}).get("from") if pick else node_id
                leaves = [i for i in self._leaves(source) if i in self.ex.results and self.ex.results[i].exit_code == 0]
                rows = [r for i in leaves for r in facts_mod.main_list(self.ex.results[i].payload)]
                payload = {"command": "query vector-text", "rows": rows} if rows else None
            self.outputs[node_id] = facts_mod.bind_outputs(payload)
        return self.outputs[node_id]

    def _bind(self, run: str) -> list[str] | None:
        """argv with each whole-token binding replaced by its value; None when a value is empty."""
        argv = capture.argv_of(run)
        if argv is None:
            return None
        out = []
        for token in argv:
            m = _BIND_TOKEN.match(token)
            if m is None:
                out.append(token)
                continue
            value = self._outputs(m.group(1)).get(m.group(2))
            if isinstance(value, list):
                value = ",".join(str(v) for v in value)
            if value in (None, "") or str(value).startswith("-"):
                return None
            out.append(str(value))
        return out

    def _leaves(self, node_id) -> list[str]:
        node = next((n for n in _walk(self.program) if n.get("id") == node_id), None)
        if node is None:
            return [node_id] if node_id in self.ex.results else []
        if node.get("kind") == "par":
            return [i for c in node.get("children") or [] for i in self._leaves(c["id"])]
        if node.get("kind") == "map":
            do_id = node["map"]["do"]["id"]
            n = len(node["map"].get("expanded") or [])
            if node["map"].get("over_bound"):  # expanded at run time: read what ran
                return [i for i in self.ex.results if _re.fullmatch(_re.escape(do_id) + r"\.\d+", i)]
            return [f"{do_id}.{i}" for i in range(1, n + 1) if f"{do_id}.{i}" in self.ex.results]
        return [node_id] if node_id in self.ex.results else []

    def pick(self, node) -> bool:
        spec = node.get("pick") or {}
        source = spec.get("from")
        if node.get("when") and not facts_mod.evaluate(_pred(node["when"]), self.ex.facts, node["id"]):
            self.ex.path.append({"id": node["id"], "kind": "pick", "skipped": "when"})
            self.ex.answer_ids = [i for i in self._leaves(source) if self.ex.results[i].exit_code == 0]
            return False
        leaves = [i for i in self._leaves(source) if self.ex.results[i].exit_code == 0]
        items = [self.ex.facts[i] for i in leaves]
        how = spec.get("how")
        if how == "first_ok":
            chosen = next((f for f in items if f["rows"] > 0), facts_mod.empty())
        elif how == "best_of":
            chosen = max(items, key=lambda f: f["rows"], default=facts_mod.empty())
        else:
            chosen = facts_mod.merge(items)
        self.ex.facts[node["id"]] = chosen
        self.ex.answer_ids = leaves
        self.ex.path.append({"id": node["id"], "kind": "pick", "how": how, "rows": chosen["rows"]})
        return True

    def page(self, node_id, source_id, spec) -> None:
        src = self.ex.results.get(source_id)
        if src is None or src.exit_code != 0:
            self.ex.path.append({"id": node_id, "kind": "page", "skipped": "no_source"})
            return
        src_facts = self.ex.facts[source_id]
        target = spec.get("k") if spec.get("until") == "k" else src_facts["rows_total"]
        argv = list(src.argv)
        ceiling = PAGE_LIMITS.get(capture.command_key(argv))
        current = int(argv[argv.index("--limit") + 1]) if "--limit" in argv[:-1] else None
        if ceiling is None or current is None or not target or min(int(target), ceiling) <= current:
            self.ex.facts[node_id] = src_facts
            self.ex.path.append({"id": node_id, "kind": "page", "skipped": "page_unsupported"})
            return
        argv[argv.index("--limit") + 1] = str(min(int(target), ceiling))
        self.run({"id": source_id, "kind": "run", "run": " ".join(argv)}, argv_override=argv)
        self.ex.facts[node_id] = self.ex.facts[source_id]

    def apply_guards(self, node_id) -> None:
        for index, guard in enumerate(self.guards):
            if guard.get("on") != node_id or index in self.fired:
                continue
            if node_id not in self.ex.facts:
                continue
            if not facts_mod.evaluate(_pred(guard["if"]), self.ex.facts, node_id):
                continue
            self.fired.add(index)
            then = guard.get("then") or {}
            kind = then.get("kind")
            self.ex.path.append({"id": node_id, "kind": "guard", "if": guard["if"], "then": kind})
            if kind == "escalate":
                raise _Stop("guard", node=node_id, predicate=guard["if"])
            if kind == "replace":
                raise _Replace(then.get("route"))
            if kind == "page":
                self.page(f"{node_id}_page", node_id, then.get("page") or {})
                return
            raise _Stop("guard", node=node_id, predicate=guard["if"], action=kind)

    # ── the whole run ────────────────────────────────────────────────────
    def suggested(self) -> list[str]:
        return [s["cmd"] for s in [*(self.plan.get("suggested_steps") or []), *(self.plan.get("fallbacks") or [])]
                if isinstance(s, dict) and s.get("cmd")]

    def replacement(self, route):
        rep = (self.plan.get("replacements") or {}).get(route)
        return (rep.get("program") or [], rep.get("guards") or []) if rep else None

    def execute(self) -> Execution:
        program = self.plan.get("program") or []
        if not program:
            self.ex.escalate = {"reason": "no_lookup", "answer_from": self.plan.get("answer_from"),
                                "action": self.plan.get("action"), "suggested": self.suggested()}
            if self.open_documents:
                self.ex.path.append({"id": opening.OPEN_ID, "kind": "run", "skipped": "no_lookup"})
            return self.ex
        self.program, self.guards = program, list(self.plan.get("guards") or [])
        if _has_unfilled(program) and self.replacement("search") and (self.plan.get("route") or {}).get("id") != "search":
            self.ex.path.append({"id": "-", "kind": "replace", "route": "search", "reason": "unfilled"})
            self.ex.replaced_by = "search"
            self.program, self.guards = self.replacement("search")
        try:
            try:
                self.nodes(self.program)
            except _Replace as rep:
                found = self.replacement(rep.route)
                if not found or self.ex.replaced_by:
                    raise _Stop("guard", action="replace", route=rep.route) from None
                self.ex.path.append({"id": "-", "kind": "replace", "route": rep.route, "reason": "guard"})
                self.ex.replaced_by = rep.route
                self.ex.results.clear()
                self.ex.facts.clear()
                self.outputs.clear()
                self.ex.answer_ids = []
                self.program, self.guards = found
                self.fired = set()
                self.nodes(self.program)
        except _Replace as rep:  # a replacement tried to replace again
            self.ex.escalate = {"reason": "guard", "action": "replace", "route": rep.route, "suggested": self.suggested()}
        except _Stop as stop:
            self.ex.escalate = {"reason": stop.reason, **stop.extra, "suggested": self.suggested()}
        self.finish()
        if self.open_documents:
            self.open()
        return self.ex

    def open(self) -> None:
        """The one open step (spec 6.5): read the documents the program only listed. Never raises."""
        def skip(reason):
            self.ex.path.append({"id": opening.OPEN_ID, "kind": "run", "skipped": reason})

        if (self.ex.escalate or {}).get("reason") == "no_lookup" or not self.plan.get("program"):
            return skip("no_lookup")
        from artmind.query_run import bundle

        evidence, documents, facts, _ = bundle._rank(self.ex)
        question = self.plan.get("effective_question") or self.plan.get("question") or ""
        domains = _plan_domains(self.plan)
        if not domains:
            return skip("no_domains")
        behind = opening.fact_documents(facts, evidence)
        chosen = opening.select_documents(documents, evidence, question, fact_docs=behind)
        if not chosen:
            return skip("no_documents")
        if self.ex.executed >= self.max_steps:
            self.ex.partial = True
            return skip("limit")
        if self.clock() - self.start > self.deadline_s:
            self.ex.partial = True
            return skip("deadline")
        argv = opening.command([d for d, _ in chosen], domains, question)
        result = self.step_fn(argv, opening.OPEN_ID)
        self.ex.executed += 1
        self.ex.results[opening.OPEN_ID] = result
        self.ex.opened = [{"doc_id": d, "why": why} for d, why in chosen]
        self.ex.path.append({"id": opening.OPEN_ID, "kind": "run", "cmd": _cut(" ".join(["artmind", *argv])),
                             "exit_code": result.exit_code, "error": result.error, "ms": result.ms,
                             "chars": result.chars, "skipped": None, "opened": len(chosen)})
        self.ex.exec_ms = round((self.clock() - self.start) * 1000, 1)

    def finish(self) -> None:
        ok = [i for i, r in self.ex.results.items() if r.exit_code == 0 and r.error is None]
        if not self.ex.answer_ids:
            self.ex.answer_ids = ok
        self.ex.exec_ms = round((self.clock() - self.start) * 1000, 1)
        if self.ex.escalate is not None:
            return
        ran = [r for r in self.ex.results.values() if r.error != "not_allowed"]
        if ran and not ok:
            reason = "step_error"
        elif self.ex.unfilled and not ok:
            reason = "unfilled"
        elif not any(self.ex.facts[i]["rows"] for i in self.ex.answer_ids if i in self.ex.facts):
            reason = "no_evidence"
        else:
            return
        self.ex.escalate = {"reason": reason, "suggested": self.suggested()}


def _plan_domains(plan) -> list[str]:
    sel = (plan.get("scope") or {}).get("domains")
    if not sel:
        sel = (plan.get("domains") or {}).get("selected")
    return [str(d) for d in sel or [] if d]


def _walk(nodes):
    for n in nodes or []:
        yield n
        yield from _walk(n.get("children"))
        if n.get("kind") == "map":
            yield from _walk([(n.get("map") or {}).get("do") or {}])


def _runnable(nodes):
    """The nodes that will run: a map contributes its `expanded` items, never its `do` template (the
    planner renders `do` without the `as` value, so the template always reads unfilled) — except a
    bound map, whose `do` the planner renders with an inert item token, so its `filled` is real."""
    for n in nodes or []:
        yield n
        yield from _runnable(n.get("children"))
        if n.get("kind") == "map":
            yield from _runnable((n.get("map") or {}).get("expanded"))
            if (n.get("map") or {}).get("over_bound"):  # expanded at run time: its `do` is real (item inert)
                yield from _runnable([(n.get("map") or {}).get("do") or {}])


def _has_unfilled(program) -> bool:
    return any(n.get("kind") == "run" and n.get("filled") is False and not n.get("optional")
               for n in _runnable(program) if n)


def execute(plan: dict, *, step_fn: Callable | None = None, max_steps: int = DEFAULT_MAX_STEPS,
            deadline_s: float = DEFAULT_DEADLINE_S, clock: Callable[[], float] = time.monotonic,
            open_documents: bool = True) -> Execution:
    """Run the plan. `open_documents` adds the spec 6.5 open step; `query run --each` turns it off."""
    run = _Run(plan, step_fn or capture.invoke_step, max_steps, deadline_s, clock)
    run.open_documents = open_documents
    return run.execute()
