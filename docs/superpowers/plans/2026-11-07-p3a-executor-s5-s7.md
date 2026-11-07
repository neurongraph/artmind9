# P3a executor, slices S5–S7: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish P3a with three slices:
- **S5, step bindings:** a step reads an earlier step's output.
- **S6, shadow dry execution:** the bundle is measured beside every traced turn.
- **S7, `query run --each`:** a batch of claims, one bundle per item.

**Architecture:**
- **S5:** the grammar in `program.py` gains `{node.output}` placeholders, checked at load. The planner leaves them unrendered and marks the node with `binds`. The executor fills them at run time from a closed output vocabulary in `facts.py`.
- **S6:** the shadow thread runs the executor on the finished shadow plan, then writes a `run_shadow` trace event that holds ids and counts only. `traces stats` reads it.
- **S7:** a fixed batch program per item, run in-process. It writes JSONL under `logs/runs/` and prints a summary.

**Tech stack:** Python 3.14, Click 8.4 / rich-click, pytest (hermetic), uv.

**Spec:** `docs/superpowers/specs/2026-11-07-p3-executor-design.md` §4 (bindings), §7.2 (shadow dry execution), §8.6 and planner-v2 §7.5 (`--each`), §9.1 (live row), §11 S5–S7.

**Starting point:** branch `claude/p3-design-a9f633` at `c9eda61` (S1–S4 built). Read `docs/superpowers/plans/2026-11-07-p3a-executor-s1-s4.md` § "File structure" for the modules S1–S4 added.

---

## Before you start

- Work on `claude/p3-design-a9f633`, or a branch cut from it. Agent worktrees are cut from master, so run `git merge claude/p3-design-a9f633` first.
- **The three slices touch different files and can be built in parallel** (one worktree each), with one exception: `artmind/query_run/facts.py` is touched by S5 only, and `artmind/cli.py` by S7 only. S6 touches neither. Merge them in the order S5, S6, S7.
- Run tests with `uv run --group dev pytest <path> -q`. Run the full suite (`uv run --group dev pytest -q test/`, about 4 min) at the end of each slice.
- Privacy: test data is invented. `run_shadow` events hold ids and counts, never corpus text.
- Commit after every task with the given message, plus a final line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the model you are). Do not push.

## Facts about the code you will need

- The plan payload's node JSON comes from `planner._node_json` (`artmind/query_plan/planner.py` ~line 785). A run node is `{"id", "kind": "run", "run": <rendered command>, "filled": bool, ...}`. A map node carries `map: {over, as, max, items, items_total, do, expanded}`, where each `expanded` item is the `do` node rendered with the item bound.
- `routes.render(template, context)` (`artmind/query_plan/routes.py` ~line 270) fills `{name}` placeholders with `shlex.quote`d values. A missing value makes the whole command `None` (unfilled). The placeholder regex is `_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")`, and it does **not** match dotted names.
- The executor (`artmind/query_run/executor.py`) is `_Run` with `node`, `run`, `par`, `map`, `pick`, `page`, `apply_guards` and `execute`. `run(node)` reads `node["run"]` through `capture.argv_of`, then calls `self.step_fn(argv, nid)` and records `self.ex.results[nid]` and `self.ex.facts[nid]`.
- `capture.invoke_step(argv, step_id)` runs one step and returns a `StepResult`. `capture.allowed(argv)` refuses anything that is not a leaf `query` command (except run/plan/page) or a read-only `db` command.
- `bundle.build(plan, ex, budget=)` returns `(printed, full)`. `printed["evidence"]` rows carry `doc.id`.
- The shadow plan runs in `ShadowPlan._run` (`artmind/query_plan/shadow.py`). The recorder (`artmind/traces/recorder.py` `finish`) writes it as a `plan_shadow` event, or writes it late from a thread (`_record_shadow_late`) when it outlasts the turn.
- `traces.analysis.stats` builds one row per (domain, period). `_run_block(turns, spans_by_trace)` already exists there.

---

# Slice S5: step bindings

### Task 1: Grammar: bound placeholders, load rules

**Files:**
- Modify: `artmind/query_plan/program.py`
- Modify: `artmind/query_plan/routes.py` (`_PLACEHOLDER_RE`, `placeholders`, `_template`, `render`)
- Test: `test/test_query_run_bindings_grammar.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Step bindings: `{node.output}` placeholders and their load rules (P3 spec §4.1–§4.2). Invented routes."""

import pytest

from artmind.query_plan import program as prog
from artmind.query_plan import routes

VT = "artmind query vector-text --shape brief --domain {domains} --topK 5 {search_term} --compact"
CHUNKS = "artmind query chunks --idList {anchor.top_chunk} --expand 2 --shape brief --compact"
P10 = "artmind query graph pattern10 --domain {domains} --documentName {doc} --compact"


def _route(program, guards=None):
    entry = {"id": "fixture", "description": "d", "triggers": [], "needs": [], "kind": "one_step",
             "program": program, "read": [], "evidence": ["S1"]}
    if guards is not None:
        entry["guards"] = guards
    return routes.parse_routes({"version": 2, "routes": [entry]})[0]


def test_bound_placeholders_are_found_and_kept_apart():
    assert routes.placeholders(CHUNKS) == {"anchor.top_chunk"}
    assert routes.bound_placeholders(CHUNKS) == {("anchor", "top_chunk")}
    assert routes.bound_placeholders(VT) == set()


def test_render_leaves_bound_placeholders_and_still_counts_as_filled():
    out = routes.render(CHUNKS, {})
    assert out == CHUNKS


def test_a_binding_to_an_earlier_node_loads():
    route = _route([{"id": "anchor", "run": VT}, {"id": "around", "run": CHUNKS}])
    node = prog.run_nodes(route.program)[1]
    assert prog.node_binds(node) == (("anchor", "top_chunk"),)


def test_a_map_over_a_bound_list_loads():
    route = _route([{"id": "anchor", "run": VT},
                    {"id": "dated", "map": {"over": "anchor.window_docs", "as": "doc", "max": 6,
                                            "do": {"id": "day", "run": P10}}}])
    dated = route.program[1]
    assert dated.map.over == "anchor.window_docs"


@pytest.mark.parametrize("program, message", [
    ([{"id": "around", "run": CHUNKS}], "unknown node"),
    ([{"id": "around", "run": CHUNKS}, {"id": "anchor", "run": VT}], "not run yet"),
    ([{"id": "p", "par": [{"id": "anchor", "run": VT}, {"id": "around", "run": CHUNKS}]}], "same par"),
    ([{"id": "anchor", "run": VT}, {"id": "around", "run": CHUNKS.replace("top_chunk", "nope")}], "unknown output"),
    ([{"id": "anchor", "run": VT}, {"id": "around", "run": CHUNKS.replace("top_chunk", "chunk_ids")}], "list output"),
    ([{"id": "anchor", "run": VT},
      {"id": "dated", "map": {"over": "anchor.top_doc", "as": "doc", "do": {"id": "day", "run": P10}}}], "scalar output"),
    ([{"id": "anchor", "run": VT},
      {"id": "around", "run": "artmind query chunks --idList x{anchor.top_chunk} --compact"}], "whole token"),
])
def test_bad_bindings_fail_at_load(program, message):
    with pytest.raises(routes.RouteError, match=message):
        _route(program)


def test_a_list_output_may_feed_a_comma_split_option():
    route = _route([{"id": "anchor", "run": VT},
                    {"id": "around", "run": "artmind query chunks --idList {anchor.chunk_ids} --shape brief --compact"}])
    assert prog.node_binds(prog.run_nodes(route.program)[1]) == (("anchor", "chunk_ids"),)


def test_the_packaged_routes_still_load_and_use_no_binding():
    for route in routes.load_routes():
        for choice in route.programs:
            assert not any(prog.node_binds(n) for n in prog.run_nodes(choice.program)), route.id
```

`ProgramError` subclasses `ValueError`, and the routes loader wraps it as `RouteError`. Before you finish the tests, check how `parse_routes` reports a `ProgramError`. If it re-raises it unwrapped, use `pytest.raises((routes.RouteError, prog.ProgramError), ...)`.

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_bindings_grammar.py -q`
Expected: FAIL (`bound_placeholders` and `node_binds` do not exist; `anchor.top_chunk` is rejected as a stray brace).

- [ ] **Step 3: Implement in `routes.py`**

Replace the placeholder regex and add a bound-placeholder helper:

```python
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+(?:\.[a-z_]+)?)\}")


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(template))


def bound_placeholders(template: str) -> set[tuple[str, str]]:
    """`{node.output}` placeholders (step bindings, P3 §4): filled by the executor, never by the planner."""
    return {tuple(p.split(".", 1)) for p in placeholders(template) if "." in p}
```

In `_template`, check only the plain placeholders against `allowed`:

```python
    unknown = {p for p in placeholders(cmd) if "." not in p} - allowed
```

In `render`, leave a bound placeholder as it is:

```python
    def substitute(match: re.Match) -> str:
        nonlocal missing
        name = match.group(1)
        if "." in name:  # a step binding: the executor fills it (P3 §4.3)
            return match.group(0)
        value = context.get(name)
        ...
```

Keep the rest of `substitute` unchanged.

- [ ] **Step 4: Implement in `program.py`**

Add after `LIST_SLOTS`:

```python
#: Step outputs a later step may bind (P3 spec §4.2): name -> "scalar" | "list". Computed in query_run/facts.py.
BIND_OUTPUTS: dict[str, str] = {
    "top_chunk": "scalar", "top_passage": "scalar", "top_doc": "scalar", "top_date": "scalar", "top_entity": "scalar",
    "chunk_ids": "list", "doc_names": "list", "entity_ids": "list", "window_docs": "list",
}
#: Options documented as comma-split, which a list output may feed.
LIST_OPTIONS = frozenset({"--idList", "--domain", "--entityClass"})
_BIND_RE = re.compile(r"\{([a-z_]+)\.([a-z_]+)\}")


def node_binds(node) -> tuple[tuple[str, str], ...]:
    """The (node, output) pairs a run node's template binds, in order of appearance."""
    if node.kind != "run" or not node.run:
        return ()
    return tuple(dict.fromkeys(_BIND_RE.findall(node.run)))
```

In `_parse_node`, map branch: accept a bound list as `over`. Replace the two `over` checks with:

```python
        over, as_ = body["over"], body["as"]
        if not isinstance(as_, str) or not _SLOT_RE.match(as_):
            raise ProgramError(f"{where}.map.as must be a snake_case name")
        if not isinstance(over, str) or not (over in LIST_SLOTS or re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z_]+", over)):
            raise ProgramError(f"{where}.map.over must be a list slot ({list(LIST_SLOTS)}) or a bound list (node.output)")
```

In `validate_program`, after the existing per-node checks, add the binding rules. Compute each node's enclosing `par` id once:

```python
    par_of: dict[str, str] = {}
    for node in order:
        if node.kind == "par":
            for child in iter_nodes(node.children):
                par_of.setdefault(child.id, node.id)
    for i, node in enumerate(order):
        loc = f"{where}: node {node.id!r}"
        refs = list(node_binds(node))
        if node.kind == "map" and "." in node.map.over:
            refs.append(tuple(node.map.over.split(".", 1)) + ("map",))
        for ref in refs:
            src, output = ref[0], ref[1]
            as_map = len(ref) == 3
            if src not in ids:
                raise ProgramError(f"{loc}: binding {src}.{output} names unknown node {src!r}")
            if ids[src] >= i:
                raise ProgramError(f"{loc}: binding {src}.{output} names node {src!r}, which has not run yet")
            if par_of.get(src) is not None and par_of.get(src) == par_of.get(node.id):
                raise ProgramError(f"{loc}: binding {src}.{output} names a node in the same par (siblings have no order)")
            if kinds[src] not in ("run", "pick"):
                raise ProgramError(f"{loc}: binding {src}.{output} must name a run or pick node, not {kinds[src]}")
            kind = BIND_OUTPUTS.get(output)
            if kind is None:
                raise ProgramError(f"{loc}: unknown output {output!r}; use {sorted(BIND_OUTPUTS)}")
            if as_map and kind != "list":
                raise ProgramError(f"{loc}: map.over {src}.{output} is a scalar output; a map needs a list output")
            if not as_map:
                tokens = node.run.split()
                token = next((t for t in tokens if f"{{{src}.{output}}}" in t), "")
                if token != f"{{{src}.{output}}}":
                    raise ProgramError(f"{loc}: binding {{{src}.{output}}} must be a whole token of the command")
                before = tokens[tokens.index(token) - 1] if tokens.index(token) > 0 else ""
                if kind == "list" and before not in LIST_OPTIONS:
                    raise ProgramError(f"{loc}: list output {output!r} may only feed {sorted(LIST_OPTIONS)}")
```

`kinds` and `ids` already exist in `validate_program`. Nodes inside a map's `do` sit in the same `order`, so a binding inside `do` can name nodes before the map.

`executed_run_calls` counts a map's body `max` times, whatever `over` is, so a bound map needs no change there.

- [ ] **Step 5: Run the tests and the routes suite**

Run: `uv run --group dev pytest test/test_query_run_bindings_grammar.py test/test_query_plan_routes.py test/test_query_plan_programs.py -q`
Expected: PASS. `test_every_template_names_a_real_command_and_real_options` must still pass, because no packaged template binds.

- [ ] **Step 6: Commit**

```bash
git add artmind/query_plan/program.py artmind/query_plan/routes.py test/test_query_run_bindings_grammar.py
git commit -m "feat(query-plan): step bindings in the program grammar, checked at load (P3 S5)"
```

### Task 2: Output vocabulary in facts

**Files:**
- Modify: `artmind/query_run/facts.py`
- Test: `test/test_query_run_bind_outputs.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Bound outputs computed from a step's payload (P3 spec §4.2). Invented payloads."""

import pytest

from artmind.query_plan import program as prog
from artmind.query_run import facts

VT = {"command": "query vector-text", "rows": [
    {"passage_id": "p1", "chunk_id": "c1", "text": "t", "doc": {"id": "d1", "title": "Day 2026-03-01", "date": "2026-03-01"}},
    {"passage_id": "p2", "chunk_id": "c2", "text": "t", "doc": {"id": "d2", "title": "Day 2026-03-02", "date": "2026-03-02"}},
    {"passage_id": "p3", "chunk_id": "c1", "text": "t", "doc": {"id": "d1", "title": "Day 2026-03-01", "date": "2026-03-01"}},
    {"passage_id": "p4", "chunk_id": "c4", "text": "t", "doc": {"id": "d4", "title": "Day 2026-03-09", "date": "2026-03-09"}}]}
RESOLVE = {"command": "query entity-resolve", "rows": [{"_id": "e1", "name": "A"}, {"_id": "e2", "name": "B"}]}


def test_every_declared_output_has_a_rule():
    assert set(prog.BIND_OUTPUTS) == set(facts.OUTPUT_RULES)


def test_scalar_outputs_take_the_best_ranked_row():
    out = facts.bind_outputs(VT)
    assert (out["top_chunk"], out["top_passage"], out["top_doc"], out["top_date"]) == ("c1", "p1", "Day 2026-03-01", "2026-03-01")


def test_list_outputs_are_ranked_and_deduplicated():
    out = facts.bind_outputs(VT)
    assert out["chunk_ids"] == ["c1", "c2", "c4"]
    assert out["doc_names"] == ["Day 2026-03-01", "Day 2026-03-02", "Day 2026-03-09"]


def test_window_docs_are_the_documents_dated_within_the_window():
    out = facts.bind_outputs(VT, window_days=3)
    assert out["window_docs"] == ["Day 2026-03-01", "Day 2026-03-02"]


def test_entity_outputs():
    out = facts.bind_outputs(RESOLVE)
    assert out["top_entity"] == "e1" and out["entity_ids"] == ["e1", "e2"]


@pytest.mark.parametrize("payload", [None, {}, {"command": "query vector-text", "rows": []}])
def test_nothing_to_bind_is_none_or_empty(payload):
    out = facts.bind_outputs(payload)
    assert out["top_chunk"] is None and out["chunk_ids"] == []
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_bind_outputs.py -q`
Expected: FAIL (`bind_outputs` does not exist).

- [ ] **Step 3: Implement in `facts.py`**

Append:

```python
# ── bound outputs (P3 spec §4.2) ─────────────────────────────────────────────

BIND_WINDOW_DAYS = 3


def _text_rows(payload) -> list[dict]:
    """Rows carrying text with a document, in rank order (brief `doc` rows, full `document` rows, `chunk` rows)."""
    out = []
    for r in main_list(payload):
        if not isinstance(r, dict):
            continue
        chunk = r.get("chunk") if isinstance(r.get("chunk"), dict) else {}
        if isinstance(r.get("text"), str) or isinstance(chunk.get("text"), str):
            out.append(r)
    return out


def _doc_of(row: dict) -> dict:
    return (row.get("doc") if isinstance(row.get("doc"), dict) else None) or \
           (row.get("document") if isinstance(row.get("document"), dict) else None) or {}


def _doc_name(doc: dict) -> str | None:
    """The form `--documentName` accepts: a full row's `name`, else the brief row's `title`."""
    return doc.get("name") or doc.get("title")


def _chunk_id(row: dict) -> str | None:
    chunk = row.get("chunk") if isinstance(row.get("chunk"), dict) else {}
    return row.get("chunk_id") or chunk.get("id")


def _entity_id(row: dict) -> str | None:
    entity = row.get("entity") if isinstance(row.get("entity"), dict) else {}
    return row.get("_id") or row.get("id") or entity.get("id")


def _dedup(values) -> list:
    return [v for v in dict.fromkeys(v for v in values if v)]


def _window(rows, days: int) -> list[str]:
    from datetime import date, timedelta

    dated = []
    for r in rows:
        doc = _doc_of(r)
        raw = doc.get("date") or doc.get("valid_from")
        try:
            dated.append((date.fromisoformat(str(raw)[:10]), _doc_name(doc)))
        except (TypeError, ValueError):
            continue
    if not dated:
        return []
    start = dated[0][0]
    end = start + timedelta(days=days)
    return _dedup(name for d, name in sorted(dated) if start <= d <= end)


def bind_outputs(payload, *, window_days: int = BIND_WINDOW_DAYS) -> dict:
    rows = _text_rows(payload)
    entities = [r for r in main_list(payload) if isinstance(r, dict) and _entity_id(r)]
    top = rows[0] if rows else {}
    top_doc = _doc_of(top)
    return {
        "top_chunk": _chunk_id(top) if rows else None,
        "top_passage": top.get("passage_id") if rows else None,
        "top_doc": _doc_name(top_doc) if rows else None,
        "top_date": (top_doc.get("date") or top_doc.get("valid_from")) if rows else None,
        "top_entity": _entity_id(entities[0]) if entities else None,
        "chunk_ids": _dedup(_chunk_id(r) for r in rows),
        "doc_names": _dedup(_doc_name(_doc_of(r)) for r in rows),
        "entity_ids": _dedup(_entity_id(r) for r in entities),
        "window_docs": _window(rows, window_days),
    }


OUTPUT_RULES = frozenset(bind_outputs(None))
```

`OUTPUT_RULES` must match `program.BIND_OUTPUTS`. The first test pins that.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_bind_outputs.py test/test_query_run_facts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_run/facts.py test/test_query_run_bind_outputs.py
git commit -m "feat(query-run): the bound-output vocabulary computed from step payloads (P3 S5)"
```

### Task 3: Planner node JSON for bindings

**Files:**
- Modify: `artmind/query_plan/planner.py` (`_node_json`; `_list_slots`; `_expand_commands`; `_route_steps`)
- Test: `test/test_query_run_bindings_planner.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The planner marks bound nodes and leaves their placeholders to the executor (P3 spec §4.3)."""

from artmind.query_plan import planner, program as prog

VT = "artmind query vector-text --shape brief --domain {domains} --topK 5 {search_term} --compact"
CHUNKS = "artmind query chunks --idList {anchor.top_chunk} --expand 2 --shape brief --compact"
P10 = "artmind query graph pattern10 --domain {domains} --documentName {doc} --compact"
CTX = {"domains": "journal", "domain_list": ("journal",), "search_term": "trip"}


def test_a_bound_run_node_is_filled_and_lists_its_binds():
    node = prog.Node("around", "run", run=CHUNKS)
    out = planner._node_json(node, CTX)
    assert out["filled"] is True and out["binds"] == ["anchor.top_chunk"]
    assert out["run"] == CHUNKS


def test_a_map_over_a_bound_list_has_no_items_yet():
    do = prog.Node("day", "run", run=P10)
    node = prog.Node("dated", "map", map=prog.MapSpec("anchor.window_docs", "doc", do, 6))
    out = planner._node_json(node, CTX)
    assert out["map"]["over_bound"] == "anchor.window_docs"
    assert out["map"]["items"] == [] and out["map"]["expanded"] == []
    assert out["map"]["do"]["run"] == P10.replace("{domains}", "journal")


def test_expand_commands_skips_bound_maps():
    do = prog.Node("day", "run", run=P10)
    node = prog.Node("dated", "map", map=prog.MapSpec("anchor.window_docs", "doc", do, 6))
    assert planner._expand_commands((node,), CTX, {}) == []
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_bindings_planner.py -q`
Expected: FAIL (`binds` missing; `over_bound` missing).

- [ ] **Step 3: Implement**

In `_node_json`, after the `out.update(run=..., filled=...)` line for a run node:

```python
        binds = [f"{a}.{b}" for a, b in program_mod.node_binds(node)]
        if binds:
            out["binds"] = binds
```

In the map branch of `_node_json`, at the start:

```python
    if node.map is not None and "." in node.map.over:  # a map over a bound list (P3 §4.3): items come at run time
        out["map"] = {"over": node.map.over, "over_bound": node.map.over, "as": node.map.as_, "max": node.map.max,
                      "items": [], "items_total": 0, "do": _node_json(node.map.do, context, lists), "expanded": []}
        if node.when is not None:
            out["when"] = node.when.text
        return out
```

Place it right after `out: dict = {"id": node.id, "kind": node.kind}`, before the `if node.run is not None:` line.

In `_expand_commands`, inside the `elif node.kind == "map":` branch, add a first line: `if "." in node.map.over: continue`. A bound map issues no plan-time command; its steps appear in `program` only.

In `_list_slots`, skip bound names. Change `wanted = {...}` to:

```python
    wanted = {n.map.over for n in program_mod.iter_nodes(choice.program) if n.kind == "map" and "." not in n.map.over}
```

- [ ] **Step 4: Run the tests and the planner suite**

Run: `uv run --group dev pytest test/test_query_run_bindings_planner.py test/test_query_plan_*.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_plan/planner.py test/test_query_run_bindings_planner.py
git commit -m "feat(query-plan): bound nodes carry binds; a map over a bound list expands at run time (P3 S5)"
```

### Task 4: The executor fills bindings

**Files:**
- Modify: `artmind/query_run/executor.py` (`run`, `map`)
- Test: `test/test_query_run_bindings_executor.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The executor fills step bindings at run time (P3 spec §4). Fake steps, invented payloads."""

from artmind.query_run import executor
from artmind.query_run.capture import StepResult

VT = "artmind query vector-text --shape brief --domain journal --topK 5 trip --compact"
CHUNKS = "artmind query chunks --idList {anchor.top_chunk} --expand 2 --shape brief --compact"
P10 = "artmind query graph pattern10 --domain journal --documentName {doc} --compact"


def _rows(*docs):
    return {"command": "query vector-text", "rows": [
        {"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "doc": {"id": d, "title": t, "date": dt}}
        for i, (d, t, dt) in enumerate(docs)]}


class Steps:
    def __init__(self, by_word):
        self.by_word, self.calls = by_word, []

    def __call__(self, argv, sid):
        self.calls.append((sid, list(argv)))
        return StepResult(sid, list(argv), 0, None, self.by_word[argv[1] if argv[1] != "graph" else argv[2]], 1.0, 10)


def _plan(program):
    return {"program": program, "guards": [], "route": {"id": "episode"}, "replacements": {}}


ANCHOR = _rows(("d1", "Day 1 & 2; rm", "2026-03-01"), ("d2", "Day 2", "2026-03-02"), ("d9", "Day 9", "2026-03-09"))


def test_a_scalar_binding_becomes_one_argument():
    steps = Steps({"vector-text": ANCHOR, "chunks": {"command": "query chunks", "rows": []}})
    executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                            {"id": "around", "kind": "run", "run": CHUNKS, "filled": True, "binds": ["anchor.top_chunk"]}]),
                     step_fn=steps)
    argv = steps.calls[1][1]
    assert argv[argv.index("--idList") + 1] == "c0"


def test_a_value_with_spaces_and_shell_characters_stays_one_argument():
    steps = Steps({"vector-text": ANCHOR, "pattern10": {"command": "query graph pattern10", "rows": []}})
    run = "artmind query graph pattern10 --domain journal --documentName {anchor.top_doc} --compact"
    executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                            {"id": "day", "kind": "run", "run": run, "filled": True, "binds": ["anchor.top_doc"]}]),
                     step_fn=steps)
    argv = steps.calls[1][1]
    assert argv[argv.index("--documentName") + 1] == "Day 1 & 2; rm"


def test_an_empty_binding_skips_the_node():
    steps = Steps({"vector-text": {"command": "query vector-text", "rows": []}})
    ex = executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                                 {"id": "around", "kind": "run", "run": CHUNKS, "filled": True, "binds": ["anchor.top_chunk"]}]),
                          step_fn=steps)
    assert [c[0] for c in steps.calls] == ["anchor"]
    assert next(p for p in ex.path if p["id"] == "around")["skipped"] == "unbound"


def test_a_map_over_a_bound_list_expands_at_run_time():
    steps = Steps({"vector-text": ANCHOR, "pattern10": {"command": "query graph pattern10", "rows": [
        {"chunk": {"id": "x", "text": "t", "doc_id": "d"}}]}})
    do = {"id": "day", "kind": "run", "run": P10, "filled": True}
    ex = executor.execute(_plan([
        {"id": "anchor", "kind": "run", "run": VT, "filled": True},
        {"id": "dated", "kind": "map", "map": {"over": "anchor.window_docs", "over_bound": "anchor.window_docs",
                                               "as": "doc", "max": 6, "items": [], "expanded": [], "do": do}}]),
        step_fn=steps)
    called = [c for c in steps.calls if c[0].startswith("day.")]
    assert [c[0] for c in called] == ["day.1", "day.2"]  # d1, d2 within 3 days of the anchor; d9 is not
    assert called[0][1][called[0][1].index("--documentName") + 1] == "Day 1 & 2; rm"
    assert ex.facts["dated"]["rows"] == 2


def test_a_bound_map_respects_max():
    many = _rows(*[(f"d{i}", f"Day {i}", "2026-03-01") for i in range(10)])
    steps = Steps({"vector-text": many, "pattern10": {"command": "query graph pattern10", "rows": []}})
    do = {"id": "day", "kind": "run", "run": P10, "filled": True}
    executor.execute(_plan([
        {"id": "anchor", "kind": "run", "run": VT, "filled": True},
        {"id": "dated", "kind": "map", "map": {"over": "anchor.window_docs", "over_bound": "anchor.window_docs",
                                               "as": "doc", "max": 3, "items": [], "expanded": [], "do": do}}]),
        step_fn=steps)
    assert len([c for c in steps.calls if c[0].startswith("day.")]) == 3


def test_a_failed_producer_unbinds():
    class Fail(Steps):
        def __call__(self, argv, sid):
            self.calls.append((sid, list(argv)))
            return StepResult(sid, list(argv), 1, "boom", None, 1.0, 0)

    steps = Fail({})
    ex = executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                                 {"id": "around", "kind": "run", "run": CHUNKS, "filled": True, "binds": ["anchor.top_chunk"]}]),
                          step_fn=steps)
    assert len(steps.calls) == 1 and next(p for p in ex.path if p["id"] == "around")["skipped"] == "unbound"
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_bindings_executor.py -q`
Expected: FAIL (the bound command runs with a literal `{anchor.top_chunk}`; the map runs no items).

- [ ] **Step 3: Implement in `executor.py`**

Add a helper above `class _Run`:

```python
import re as _re

_BIND_TOKEN = _re.compile(r"^\{([a-z][a-z0-9_]*)\.([a-z_]+)\}$")
```

In `_Run.__init__` add `self.outputs: dict = {}` (node id → bound outputs, computed lazily).

Add these methods to `_Run`:

```python
    def _outputs(self, node_id: str) -> dict:
        if node_id not in self.outputs:
            result = self.ex.results.get(node_id)
            payload = result.payload if result is not None and result.exit_code == 0 else None
            if payload is None and node_id in self.ex.facts:  # a pick: bind over the steps it kept
                leaves = [i for i in self._leaves(node_id) if self.ex.results[i].exit_code == 0]
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
            if value in (None, ""):
                return None
            out.append(str(value))
        return out
```

Two notes:
- `capture.argv_of` uses `shlex.split`, which turns `{anchor.top_chunk}` into one token.
- `_outputs` reads `self.ex.results` for a run node. For a `pick` node it walks `_leaves(node_id)`, which matches pick ids only when they are leaves, so a pick id goes through the `node_id in self.ex.facts` branch.

In `run`, after the `needs_unmet` and `filled` checks and before `argv = argv_override or ...`:

```python
        if node.get("binds") and argv_override is None:
            argv_override = self._bind(node.get("run") or "")
            if argv_override is None:
                self.ex.path.append({"id": nid, "kind": "run", "skipped": "unbound"})
                return
```

In `map`, at the start:

```python
        spec = node.get("map") or {}
        if spec.get("over_bound"):
            src, output = spec["over_bound"].split(".", 1)
            items = list(self._outputs(src).get(output) or [])[: int(spec.get("max") or 10)]
            do = spec.get("do") or {}
            as_ = spec.get("as")
            expanded = []
            for item in items:
                child = dict(do)
                child["run"] = (do.get("run") or "").replace("{" + as_ + "}", shlex.quote(str(item)))
                child["filled"] = True
                expanded.append(child)
            if not expanded:
                self.ex.path.append({"id": node["id"], "kind": "map", "skipped": "unbound"})
            spec = {**spec, "expanded": expanded}
            node = {**node, "map": spec}
```

Keep the rest of `map` as it is. It iterates `spec.get("expanded")`, so it now runs the run-time items. Add `import shlex` at the top of `executor.py`.

The planner rendered `do` with every plan-time slot already filled except `{as_}`. `do.run` therefore still holds the literal `{doc}`, and the replacement above fills it with a shell-quoted value that `argv_of` splits back into one token.

In `_leaves`, a map with `over_bound` has no `expanded` in `self.program`. Make the map branch count the `do.id.i` results that exist instead of reading `expanded`:

```python
        if node.get("kind") == "map":
            do_id = ((node.get("map") or {}).get("do") or {}).get("id")
            return sorted((i for i in self.ex.results if do_id and i.startswith(f"{do_id}.")),
                          key=lambda i: int(i.rsplit(".", 1)[1]))
```

- [ ] **Step 4: Run the tests and the executor suite**

Run: `uv run --group dev pytest test/test_query_run_bindings_executor.py test/test_query_run_executor.py test/test_query_run_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_run/executor.py test/test_query_run_bindings_executor.py
git commit -m "feat(query-run): the executor fills step bindings and expands bound maps at run time (P3 S5)"
```

### Task 5: S5 gate

- [ ] **Step 1: Full suite.** Run `uv run --group dev pytest -q test/`. Expected: all pass.
- [ ] **Step 2: Route parity.** No packaged route binds, so plans are unchanged. On each of the three vaults, run the parity script from the handover ("How the work was done") before and after the slice, and compare. Expected: no route or step changes. If `~/artmind-private/scripts/p2a_plan_parity.py` is not on this machine, run `artmind plan replay --classifier lexical --repeat 1 --compact` in each vault before and after instead, and compare the per-label outcomes in the detail files.

---

# Slice S6: shadow dry execution

### Task 6: Run the shadow plan behind a flag

**Files:**
- Modify: `artmind/query_plan/shadow.py`
- Test: `test/test_query_run_shadow.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Shadow dry execution (P3 spec §7.2): run the shadow plan's program, record ids and counts only."""

import pytest

from artmind.query_plan import shadow
from artmind.query_run.capture import StepResult

PLAN = {"program": [{"id": "s1", "kind": "run", "run": "artmind query vector-text --domain b q --compact", "filled": True},
                    {"id": "s2", "kind": "run", "run": "artmind query graph text2cypher --domain b q --compact", "filled": True}],
        "guards": [], "route": {"id": "search"}, "replacements": {}, "confidence": {"level": "low"},
        "classifier": {"backend": "lexical"}}
PAYLOAD = {"command": "query vector-text", "rows": [
    {"passage_id": "p1", "chunk_id": "c1", "text": "private words", "relevance": 0.6, "doc": {"id": "d1", "title": "T"}}]}


def _step(argv, sid):
    return StepResult(sid, list(argv), 0, None, PAYLOAD, 5.0, 100)


@pytest.mark.parametrize("value, expected", [(None, "0"), ("0", "0"), ("1", "1"), ("all", "all"), ("yes", "1")])
def test_the_flag(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ARTMIND_PLAN_SHADOW_RUN", raising=False)
    else:
        monkeypatch.setenv("ARTMIND_PLAN_SHADOW_RUN", value)
    assert shadow.run_mode() == expected


def test_a_dry_run_records_ids_and_counts_only():
    event = shadow.dry_run(PLAN, mode="1", step_fn=_step)
    assert event["status"] == "ok"
    assert [p["id"] for p in event["path"]] == ["s1", "s2"]
    assert next(p for p in event["path"] if p["id"] == "s2")["skipped"] == "llm_shadow"
    assert event["documents"] == ["d1"] and event["timing"]["exec_ms"] >= 0
    assert event["bundle_chars"] > 0 and event["escalate"] is None
    assert "private words" not in str(event) and "q --compact" not in str(event)


def test_mode_all_runs_llm_steps_too():
    event = shadow.dry_run(PLAN, mode="all", step_fn=_step)
    assert next(p for p in event["path"] if p["id"] == "s2").get("skipped") is None


def test_a_failing_dry_run_is_an_error_event():
    def boom(argv, sid):
        raise RuntimeError("down")

    event = shadow.dry_run(PLAN, mode="1", step_fn=boom)
    assert event["status"] == "error" and event["error"] == "RuntimeError"


def test_covers_agent():
    assert shadow.covers_agent(["d1", "d2"], [{"evidence": {"documents": [{"doc_id": "d1"}, {"doc_id": "d3"}]}}]) == 0.5
    assert shadow.covers_agent(["d1"], []) is None
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_shadow.py -q`
Expected: FAIL (`run_mode`, `dry_run` and `covers_agent` missing).

- [ ] **Step 3: Implement in `shadow.py`**

Add:

```python
#: Commands that make an LLM call; a dry run skips them unless ARTMIND_PLAN_SHADOW_RUN=all.
LLM_COMMANDS = frozenset({"query graph text2cypher", "query text2sql"})


def run_mode() -> str:
    """ARTMIND_PLAN_SHADOW_RUN: 0 (default, off), 1 (dry-run the program, LLM steps skipped), all (LLM steps too)."""
    raw = (os.environ.get("ARTMIND_PLAN_SHADOW_RUN") or "0").strip().casefold()
    if raw in ("", "0", "false", "no", "off"):
        return "0"
    return "all" if raw == "all" else "1"


def dry_run(plan: dict, *, mode: str, step_fn=None) -> dict:
    """Execute a shadow plan with no trace spans; return a `run_shadow` payload of ids and counts only."""
    try:
        from artmind.query_run import bundle, capture, executor
        from artmind.traces.span import trace_override

        real = step_fn or capture.invoke_step

        def step(argv, sid):
            if mode != "all" and capture.command_key(argv) in LLM_COMMANDS:
                return capture.StepResult(sid, list(argv), 0, None, None, 0.0, 0, skipped="llm_shadow")
            return real(argv, sid)

        with trace_override(None):
            ex = executor.execute(plan, step_fn=step)
            printed, _ = bundle.build(plan, ex, budget=bundle.DEFAULT_BUDGET)
        skipped = {sid for sid, r in ex.results.items() if r.skipped}
        path = [{"id": p.get("id"), "kind": p.get("kind"), "rows": p.get("rows"), "ms": p.get("ms"),
                 "exit_code": p.get("exit_code"),
                 "skipped": "llm_shadow" if p.get("id") in skipped else p.get("skipped")} for p in ex.path]
        docs = [d for d in dict.fromkeys((e.get("doc") or {}).get("id") for e in printed.get("evidence") or []) if d]
        docs += [d.get("doc_id") for d in printed.get("documents") or [] if d.get("doc_id") and d.get("doc_id") not in docs]
        return {"status": "ok", "path": path, "timing": {"exec_ms": ex.exec_ms}, "escalate": (ex.escalate or {}).get("reason"),
                "bundle_chars": len(json.dumps(printed, default=str)), "documents": docs[:200],
                "route": (plan.get("route") or {}).get("id"), "level": (plan.get("confidence") or {}).get("level"),
                "program_kind": plan.get("program_kind")}
    except Exception as exc:
        logger.warning("shadow dry run failed: {}", exc)
        return {"status": "error", "error": type(exc).__name__}


def covers_agent(bundle_docs: list[str], agent_spans: list[dict]) -> float | None:
    """Share of the documents the agent's own spans cite that the bundle also holds; None when the agent cited none."""
    agent = {d.get("doc_id") for s in agent_spans for d in ((s.get("evidence") or {}).get("documents") or [])
             if isinstance(d, dict) and d.get("doc_id")}
    if not agent:
        return None
    return round(len(agent & set(bundle_docs)) / len(agent), 3)
```

In `ShadowPlan._run`, after `self._plan = planner.plan(...)`:

```python
            mode = run_mode()
            if mode != "0" and isinstance(self._plan, dict):
                self._dry = dry_run(self._plan, mode=mode)
```

Initialise `self._dry: dict | None = None` in `__init__`, and add a property:

```python
    @property
    def dry(self) -> dict | None:
        return self._dry
```

`dry_run` passes `skipped="llm_shadow"` into `StepResult`, which already has a `skipped` field with default `None`. The executor records the result and computes facts from `payload=None`, so the step counts as zero rows, which is what a skipped step should be.

`trace_override(None)` makes `current_trace_id()` return None inside the dry run, so `TracedGroup` writes no spans.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_shadow.py test/test_query_plan_shadow.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_plan/shadow.py test/test_query_run_shadow.py
git commit -m "feat(query-plan): shadow dry execution of the plan's program behind ARTMIND_PLAN_SHADOW_RUN (P3 S6)"
```

### Task 7: Record `run_shadow` with `covers_agent`

**Files:**
- Modify: `artmind/traces/recorder.py` (`finish`, `_record_shadow_late`)
- Test: `test/test_query_run_shadow_recorder.py`

- [ ] **Step 1: Write the failing test**

Read `test/test_traces_recorder.py` first and reuse its fixtures (an isolated DB and how a recorder is built and fed events). Then write:

```python
"""The recorder writes a run_shadow event beside the plan_shadow one (P3 spec §7.2)."""

import json

import pytest

from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


class FakeShadow:
    running = False

    def __init__(self, dry):
        self.dry = dry

    def result(self, timeout=None):
        return {"status": "ok", "followup": False, "plan": {"route": {"id": "search"}}, "error": None}


def test_run_shadow_is_recorded_with_covers_agent():
    from artmind.traces.recorder import TraceRecorder

    store.insert_turn("t1", source="admin", question="q")
    store.insert_span({"span_id": "a1", "trace_id": "t1", "command": "query vector-text", "args": "{}", "exit_code": 0,
                       "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
                       "evidence": json.dumps({"documents": [{"doc_id": "d1"}, {"doc_id": "d2"}]}),
                       "started_at": 1.0}, [])
    rec = TraceRecorder("t1", None)
    rec.enabled = True
    rec._shadow = FakeShadow({"status": "ok", "documents": ["d1"], "path": [], "timing": {"exec_ms": 5}})
    rec.finish()
    events = [e for e in store.list_events("t1") if e["type"] == "run_shadow"]
    assert len(events) == 1
    payload = json.loads(events[0]["payload"])
    assert payload["covers_agent"] == 0.5 and payload["documents"] == ["d1"]
```

The class is `TraceRecorder(trace_id, context_path)`. If `finish` needs more state than the sketch sets (for example `_done`, `_tools`), copy the setup from `test/test_traces_recorder.py` and keep the assertions.

- [ ] **Step 2: Run it and check it fails**

Run: `uv run --group dev pytest test/test_query_run_shadow_recorder.py -q`
Expected: FAIL (no `run_shadow` event).

- [ ] **Step 3: Implement in `recorder.py`**

In `finish`, the shadow handling adds a `plan_shadow` event. Change it to also add a `run_shadow` event when the shadow has a dry run. `covers_agent` needs the spans, which are read after `insert_events`, so write `run_shadow` in a second `insert_events` call after `spans = store.list_spans(...)`. Like this:

```python
            dry = getattr(self._shadow, "dry", None) if self._shadow is not None else None
            store.insert_events(self.trace_id, self._events)
            spans = store.list_spans(self.trace_id)
            top = [sp for sp in spans if not sp.get("parent_span_id")]
            if isinstance(dry, dict):
                try:
                    payload = {**dry, "covers_agent": shadow.covers_agent(dry.get("documents") or [], top)}
                    store.insert_events(self.trace_id, [{"seq": len(self._events), "ts": time.time(), "type": "run_shadow",
                                                         "tool_id": None, "parent_tool_id": None, "name": "query run",
                                                         "payload": store.cap(payload)}])
                except Exception:
                    logger.exception("run shadow not recorded")
```

Keep the existing `top = ...` line in one place: move it up and reuse it.

`trace_events` has `PRIMARY KEY (trace_id, seq)`. When the shadow plan runs late, `_record_shadow_late(seq=len(self._events))` has already reserved `len(self._events)` for `plan_shadow`, so in the normal path use `seq = len(self._events)` (the `plan_shadow` event is already in `self._events`). In the late path, pass `seq + 1` for `run_shadow` from inside `_record_shadow_late`'s thread, after `shadow_plan.result(...)` returns, reading the spans at that point. When the dry run outlasts `LATE_WAIT_S`, the shadow thread is still alive: write `{"status": "timeout"}` as the `run_shadow` payload then. `store.cap` accepts a dict (it is used for `plan_shadow`).

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_shadow_recorder.py test/test_traces_recorder.py test/test_traces_plan_shadow.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/recorder.py test/test_query_run_shadow_recorder.py
git commit -m "feat(traces): record run_shadow beside the shadow plan, with covers_agent (P3 S6)"
```

### Task 8: `traces stats` reads `run_shadow`

**Files:**
- Modify: `artmind/traces/analysis.py` (`stats`)
- Test: `test/test_query_run_shadow_stats.py`

- [ ] **Step 1: Write the failing test**

```python
"""traces stats: the run_shadow block (P3 spec §7.2)."""

import json

import pytest

from artmind.traces import analysis, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _turn(tid, run_shadow):
    store.insert_turn(tid, source="benchmark", question="q", started_at=1_700_000_000.0)
    store.update_turn(tid, status="done", domains=json.dumps(["b"]), completed_at=1_700_000_001.0)
    store.insert_events(tid, [{"seq": 0, "ts": 1.0, "type": "run_shadow", "payload": json.dumps(run_shadow)}])


def test_the_run_shadow_block():
    _turn("t1", {"status": "ok", "route": "search", "level": "low", "program_kind": "cross_document",
                 "timing": {"exec_ms": 800}, "escalate": None, "covers_agent": 1.0})
    _turn("t2", {"status": "ok", "route": "search", "level": "high", "program_kind": "cross_document",
                 "timing": {"exec_ms": 1200}, "escalate": "no_evidence", "covers_agent": 0.5})
    _turn("t3", {"status": "timeout"})
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    block = row["run_shadow"]
    assert block["count"] == 3 and block["statuses"] == {"ok": 2, "timeout": 1}
    assert block["exec_ms_p90"] == {"cross_document": 1200}
    assert block["escalation_rate"] == {"search": 0.5}
    assert block["covers_agent"] == {"search": {"low": 1.0, "high": 0.5}}


def test_no_run_shadow_keeps_the_same_keys():
    store.insert_turn("t9", source="admin", question="q", started_at=1_700_000_000.0)
    store.update_turn("t9", status="done", domains=json.dumps(["b"]), completed_at=1_700_000_001.0)
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    assert row["run_shadow"] == {"count": 0, "statuses": {}, "exec_ms_p90": {}, "escalation_rate": {}, "covers_agent": {}}
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run --group dev pytest test/test_query_run_shadow_stats.py -q`
Expected: FAIL (`KeyError: 'run_shadow'`).

- [ ] **Step 3: Implement**

In `analysis.py`, add:

```python
def _run_shadow_block(turns: list[dict], runs: dict[str, list[dict]]) -> dict:
    """Shadow dry runs (P3 §7.2): exec time by program kind, escalation by route, covers_agent by route and level."""
    from collections import defaultdict

    from artmind.query_run import replay_run

    statuses: Counter = Counter()
    ms, esc, cov = defaultdict(list), defaultdict(list), defaultdict(lambda: defaultdict(list))
    for turn in turns:
        for event in runs.get(turn["trace_id"], []):
            try:
                p = json.loads(event.get("payload") or "{}")
            except ValueError:
                statuses["truncated"] += 1
                continue
            statuses[p.get("status") or "unknown"] += 1
            if p.get("status") != "ok":
                continue
            if (p.get("timing") or {}).get("exec_ms") is not None and p.get("program_kind"):
                ms[p["program_kind"]].append(p["timing"]["exec_ms"])
            if p.get("route"):
                esc[p["route"]].append(bool(p.get("escalate")))
                if p.get("covers_agent") is not None:
                    cov[p["route"]][p.get("level") or "unknown"].append(p["covers_agent"])
    return {"count": sum(statuses.values()), "statuses": dict(statuses),
            "exec_ms_p90": {k: replay_run.p90(v) for k, v in sorted(ms.items())},
            "escalation_rate": {k: sum(v) / len(v) for k, v in sorted(esc.items())},
            "covers_agent": {r: {lvl: round(statistics.mean(v), 3) for lvl, v in levels.items()}
                             for r, levels in sorted(cov.items())}}
```

In `stats`, add `runs = store.list_events_of_type(ids, "run_shadow")` next to the `shadows = ...` line, and add `"run_shadow": _run_shadow_block(turns, runs),` to each row after `"run": ...`.

Use `replay_run.p90` (nearest-rank, defined for any number of values), not `analysis._p90`: the latter returns None below 10 values, and a benchmark run has few turns per program kind. `replay_run.p90([800, 1200])` is 1200.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_shadow_stats.py test/test_traces_*.py -q`
Expected: PASS. Existing stats tests that compare whole rows may need the new key. If any does, add `"run_shadow"` with its empty value to that expectation, and list it in the commit message.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/analysis.py test/test_query_run_shadow_stats.py
git commit -m "feat(traces): traces stats reports shadow dry runs (P3 S6)"
```

### Task 9: S6 gate (live row of §9.1)

- [ ] **Step 1: Full suite.** Run `uv run --group dev pytest -q test/`.
- [ ] **Step 2: Reinstall and restart.** `cd ~/projects/artmind9 && just dev-install && just dev-stop-daemons`. Start `admin-ui` with the flag from inside each vault, as the handover's "Setting up" trap 1 shows, adding `ARTMIND_PLAN_SHADOW_RUN=1`.
- [ ] **Step 3: One benchmark run per vault** (banking_corpus 36 questions, brain01 10) from the admin UI. The user starts these runs; it is their collection method.
- [ ] **Step 4: Read the gate.** Run `artmind traces stats --by all --compact` inside each vault and read the `run_shadow` block. Pass when:
  - `count` equals the number of benchmark turns, with every status `ok` (a `timeout` is a fail to explain);
  - no turn was delayed: the turns' `duration_s` is in line with round 9's;
  - `exec_ms_p90` is at most 3000 for program kinds whose plans have one to four steps.

  Record the numbers (counts and milliseconds only) in the handover.

---

# Slice S7: `query run --each`

### Task 10: Claims file and the batch program

**Files:**
- Create: `artmind/query_run/each.py`
- Test: `test/test_query_run_each.py`

- [ ] **Step 1: Write the failing tests**

```python
"""`query run --each FILE` (P3 spec §8.6, planner v2 §7.5): a fixed program per claim. Invented claims."""

import json

import pytest

from artmind.query_run import each
from artmind.query_run.capture import StepResult


def _write(tmp_path, text, name="claims.jsonl"):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_load_jsonl_and_yaml(tmp_path):
    a = each.load_claims(_write(tmp_path, '{"id": "c1", "claim": "fees rose"}\n{"id": "c2", "claim": "x", "source_doc": "memo.md"}\n'))
    assert [(c.id, c.claim, c.source_doc) for c in a] == [("c1", "fees rose", None), ("c2", "x", "memo.md")]
    b = each.load_claims(_write(tmp_path, "- id: c1\n  claim: fees rose\n", "claims.yaml"))
    assert [c.id for c in b] == ["c1"]


@pytest.mark.parametrize("text, message", [
    ('{"claim": "no id"}\n', "id"),
    ('{"id": "c1"}\n', "claim"),
    ('{"id": "c1", "claim": "a"}\n{"id": "c1", "claim": "b"}\n', "duplicate"),
    ("not json\n", "line 1"),
])
def test_bad_claims_files(tmp_path, text, message):
    with pytest.raises(each.ClaimsError, match=message):
        each.load_claims(_write(tmp_path, text))


def test_the_item_program_excludes_the_source_and_unions_two_searches():
    plan = each.item_plan(each.Claim("c1", "fees rose in March", "memo.md"), ["bank"])
    (par, pick) = plan["program"]
    runs = [c["run"] for c in par["children"]]
    assert runs[0].startswith("artmind query vector-text --shape brief --domain bank --topK 10 ")
    assert runs[1].startswith("artmind query survey --shape brief --domain bank --topDocs 10 ")
    assert all("'fees rose in March'" in r for r in runs)
    assert pick["pick"]["how"] == "union" and plan["scope"]["excluded_documents"] == ["memo.md"]


def test_a_claim_is_cut_to_the_character_cap():
    plan = each.item_plan(each.Claim("c1", "x" * 5000, None), ["bank"])
    assert len(plan["program"][0]["children"][0]["run"]) < 5000


def test_run_each_writes_one_bundle_per_item_and_a_summary(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    payload = {"command": "query vector-text", "rows": [
        {"passage_id": "p1", "chunk_id": "c1", "text": "t", "relevance": 0.5, "doc": {"id": "d1", "title": "T"}},
        {"passage_id": "p2", "chunk_id": "c2", "text": "t", "relevance": 0.4, "doc": {"id": "memo", "title": "memo"}}]}

    def step(argv, sid):
        return StepResult(sid, list(argv), 0, None, payload, 3.0, 50)

    claims = [each.Claim("c1", "a", "memo"), each.Claim("c2", "b", None)]
    summary = each.run_each(claims, ["bank"], step_fn=step, max_items=50)
    assert summary["items"] == 2 and summary["escalated"] == 0 and summary["exec_ms_p90"] is not None
    lines = (tmp_path / "logs" / "runs" / f"{summary['handle']}.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    assert first["id"] == "c1" and first["bundle"]["bundle_version"] == 1
    assert all((e["doc"] or {}).get("id") != "memo" for e in first["bundle"]["evidence"])  # the claim's own source is left out


def test_max_items_caps_the_batch(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    claims = [each.Claim(f"c{i}", "a", None) for i in range(5)]
    summary = each.run_each(claims, ["bank"], step_fn=lambda a, s: StepResult(s, a, 0, None, {"rows": []}, 1.0, 1),
                            max_items=2)
    assert summary["items"] == 2 and summary["skipped_items"] == 3
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_each.py -q`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Write `artmind/query_run/each.py`**

```python
"""`query run --each FILE` (P3 spec §8.6; planner v2 §7.5): a fixed program per claim, in-process.

Per item: needle vector-text (top 10) beside a brief survey (top 10), unioned; the claim's own
`source_doc` is left out of the evidence. Claims are used as written (the catalogue has no `terms` yet).
No LLM call. One bundle per item goes to <ARTMIND_HOME>/logs/runs/<handle>.jsonl (private, gitignored);
the command prints a summary only.
"""

from __future__ import annotations

import json
import secrets
import shlex
from dataclasses import dataclass
from pathlib import Path

from artmind.query_run import bundle as bundle_mod
from artmind.query_run import executor
from artmind.query_run.replay_run import p90

CLAIM_CHARS = 1000
MAX_ITEMS_DEFAULT = 50
ITEM_BUDGET = 12_000


class ClaimsError(ValueError):
    pass


@dataclass(frozen=True)
class Claim:
    id: str
    claim: str
    source_doc: str | None = None


def load_claims(path) -> list[Claim]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClaimsError(f"cannot read {path}: {exc}") from exc
    if path.suffix in (".yaml", ".yml"):
        import yaml

        try:
            raw = yaml.safe_load(text) or []
        except yaml.YAMLError as exc:
            raise ClaimsError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(raw, list):
            raise ClaimsError(f"{path}: expected a list of {{id, claim, source_doc?}}")
        items = list(enumerate(raw, 1))
    else:
        items = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                items.append((n, json.loads(line)))
            except ValueError as exc:
                raise ClaimsError(f"{path} line {n}: not JSON ({exc})") from None
    out, seen = [], set()
    for n, item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ClaimsError(f"{path} item {n}: needs a string `id`")
        if not isinstance(item.get("claim"), str) or not item["claim"].strip():
            raise ClaimsError(f"{path} item {n} ({item['id']}): needs a string `claim`")
        if item["id"] in seen:
            raise ClaimsError(f"{path}: duplicate id {item['id']!r}")
        seen.add(item["id"])
        src = item.get("source_doc")
        out.append(Claim(item["id"], item["claim"], src if isinstance(src, str) and src else None))
    return out


def item_plan(claim: Claim, domains: list[str]) -> dict:
    text = shlex.quote(claim.claim[:CLAIM_CHARS])
    dom = shlex.quote(",".join(domains))
    needle = f"artmind query vector-text --shape brief --domain {dom} --topK 10 {text} --compact"
    wide = f"artmind query survey --shape brief --domain {dom} --topDocs 10 {text} --compact"
    return {"program": [{"id": "p", "kind": "par", "children": [
                {"id": "needle", "kind": "run", "run": needle, "filled": True},
                {"id": "wide", "kind": "run", "run": wide, "filled": True}]},
                        {"id": "u", "kind": "pick", "pick": {"from": "p", "how": "union", "score": []}}],
            "guards": [], "replacements": {}, "route": {"id": "batch_claims"}, "program_kind": "batch_claims",
            "intent": {"id": "retrieve"}, "confidence": {"level": "low"},
            "scope": {"domains": list(domains), "excluded_documents": [claim.source_doc] if claim.source_doc else [],
                      "rules": []},
            "gaps": [], "suggested_steps": [], "fallbacks": []}


def _drop_source(printed: dict, source: str | None) -> dict:
    if not source:
        return printed
    key = source.casefold()

    def own(doc: dict) -> bool:
        return any(str(doc.get(f) or "").casefold() in (key, key.rsplit(".", 1)[0]) for f in ("id", "title", "doc_id"))

    printed["evidence"] = [e for e in printed.get("evidence") or [] if not own(e.get("doc") or {})]
    printed["documents"] = [d for d in printed.get("documents") or [] if not own(d)]
    return printed


def run_each(claims: list[Claim], domains: list[str], *, step_fn=None, max_items: int = MAX_ITEMS_DEFAULT) -> dict:
    import paths

    runs_dir = Path(paths.LOGS_DIR) / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    handle = secrets.token_hex(6)
    out_path = runs_dir / f"{handle}.jsonl"
    took = claims[:max_items]
    times, escalated = [], 0
    with out_path.open("w", encoding="utf-8") as fh:
        for claim in took:
            plan = item_plan(claim, domains)
            ex = executor.execute(plan, step_fn=step_fn)
            printed, _ = bundle_mod.build(plan, ex, budget=ITEM_BUDGET)
            printed = _drop_source(printed, claim.source_doc)
            times.append(ex.exec_ms)
            escalated += bool(ex.escalate)
            fh.write(json.dumps({"id": claim.id, "bundle": printed}, ensure_ascii=False, default=str) + "\n")
    return {"items": len(took), "skipped_items": len(claims) - len(took), "escalated": escalated,
            "exec_ms_p50": sorted(times)[len(times) // 2] if times else None, "exec_ms_p90": p90(times),
            "handle": handle, "file": str(out_path)}
```

Check before finishing:
- **`query survey` options.** Its `--topDocs` is `IntRange(1, 50)` and it takes a positional question (checked in `artmind/cli.py` `query_survey`). The routes test does not cover these templates, so add an assertion in `test_query_run_each.py` that both commands resolve in the Click tree. Copy the `_resolve` helper from `test/test_query_plan_routes.py`.
- **Bundle handles.** `bundle.build` may store a handle under `logs/results/` for an item that is over its budget. That is fine: the JSONL line keeps the printed bundle, whose `handle` reaches the rest.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_each.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_run/each.py test/test_query_run_each.py
git commit -m "feat(query-run): the batch claims program, one bundle per item under logs/runs (P3 S7)"
```

### Task 11: `--each` on the command, child span per item

**Files:**
- Modify: `artmind/cli.py` (`query_run`)
- Modify: `artmind/query_run/each.py` (a span per item)
- Test: `test/test_query_run_each_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
"""`artmind query run --each` (P3 S7). Invented claims; the step function is faked."""

import json

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_run import each
from artmind.query_run.capture import StepResult


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)
    monkeypatch.setattr(each, "_default_step", lambda: (lambda argv, sid: StepResult(
        sid, list(argv), 0, None, {"command": "query vector-text", "rows": []}, 1.0, 1)))


def test_each_prints_a_summary_and_never_the_bundles(tmp_path):
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "invented claim text"}\n')
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(claims), "--domain", "bank", "--compact"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.stdout)
    assert payload["command"] == "query run" and payload["items"] == 1 and payload["handle"]
    assert "invented claim text" not in out.stdout


def test_each_needs_a_domain(tmp_path):
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "x"}\n')
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(claims)])
    assert out.exit_code == 1 and "--domain" in out.output


def test_each_with_a_question_or_plan_is_refused(tmp_path):
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "x"}\n')
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(claims), "--domain", "b", "a question"])
    assert out.exit_code == 1


def test_a_bad_claims_file_is_bad_input(tmp_path):
    bad = tmp_path / "c.jsonl"
    bad.write_text("nope\n")
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(bad), "--domain", "b"])
    assert out.exit_code == 1 and "line 1" in out.output


def test_each_item_is_a_child_span_of_the_run(tmp_path, monkeypatch):
    from artmind.traces import store

    store.insert_turn("t1", source="admin", question="q")
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    claims = tmp_path / "c.jsonl"
    claims.write_text('{"id": "c1", "claim": "a"}\n{"id": "c2", "claim": "b"}\n')
    out = CliRunner().invoke(cli, ["query", "run", "--each", str(claims), "--domain", "bank", "--compact"])
    assert out.exit_code == 0, out.output
    spans = store.list_spans("t1")
    run = next(s for s in spans if s["command"] == "query run")
    items = [s for s in spans if s["parent_span_id"] == run["span_id"]]
    assert [s["command"] for s in items] == ["query run item", "query run item"]
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_each_cli.py -q`
Expected: FAIL (no `--each` option; no `_default_step`).

- [ ] **Step 3: Implement**

In `each.py`:
- add `def _default_step(): from artmind.query_run import capture; return capture.invoke_step`;
- use `step_fn or _default_step()` in `run_each`;
- wrap each item in a span through a new helper in `artmind/traces/span.py`:

```python
@contextmanager
def child_span(command: str, args: dict):
    """A span under the active one (a `query run --each` item); a no-op outside a traced span. Never raises."""
    parent = _active.get()
    if parent is None:
        yield None
        return
    span = {"span_id": uuid.uuid4().hex, "trace_id": parent["trace_id"], "argv": [], "command": command,
            "started_at": time.time(), "summary": None, "params": args, "llm_calls": [],
            "parent_span_id": parent["span_id"]}
    try:
        yield span
    finally:
        _record(span, 0)
```

In `run_each`, around each item:

```python
            from artmind.traces.span import child_span

            with child_span("query run item", {"id": claim.id}) as span:
                ...  # the plan / execute / build / write lines
                if span is not None:
                    span["summary"] = {"rows": len(printed["evidence"]), "truncated": printed["truncated"],
                                       "rows_total": printed["rows_total"], "evidence": {}}
```

The item's own steps run through `capture.invoke_step` while the item span is not the active span, so they become children of the run span. That is acceptable for P3. Keep the item span's `params` to the claim id only, never the claim text.

In `cli.py` `query_run`, add the option `@click.option("--each", "each_file", default=None, type=click.Path(path_type=Path), help="Run the batch claims program over this JSONL/YAML file ([{id, claim, source_doc?}]): one bundle per item under logs/runs/, a summary printed. In-process; needs --domain")` and `@click.option("--maxItems", "max_items", type=click.IntRange(1, 500), default=50, show_default=True, help="Most claims one --each run takes")`. Add both parameters to the signature. At the start of the body:

```python
    if each_file is not None:
        from artmind.query_run import each as each_mod

        if question is not None or plan_file is not None:
            raise click.ClickException("--each runs a claims file; give no question and no --plan")
        if not domain:
            raise click.ClickException("--each needs --domain (the claims are searched in these domains)")
        try:
            claims = each_mod.load_claims(each_file)
        except each_mod.ClaimsError as exc:
            raise click.ClickException(str(exc)) from exc
        _echo_json(each_mod.run_each(claims, _parse_domains(domain), max_items=max_items), compact)
        return
```

The `query run` path is in `LIST_KEY` as `evidence`, but the summary has no `evidence` list, so `_echo_json`'s cut falls back to its fallback lists. The summary is small, so no cut happens. The bundle skip in `_echo_json` keys on `bundle_version`, which the summary does not carry.

`_entry._proxiable` already runs `--each` in-process (S2).

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_each_cli.py test/test_query_run_each.py test/test_query_run_cli.py test/test_cli_conventions.py test/test_traces_span.py -q`
Expected: PASS. `test_cli_conventions` checks camelCase options (`--maxItems`) and that the command is read-only. `run_each` writes a file under `logs/runs/`, and the contract test's source scan looks for `write_text(` in the command callback. The write happens in `each.py`, not in the callback, so the scan passes. Do not add `query run` to `_READ_ONLY`: the file is a private log like `logs/results/`.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py artmind/query_run/each.py artmind/traces/span.py test/test_query_run_each_cli.py
git commit -m "feat(query-run): query run --each runs a claims file in process, one child span per item (P3 S7)"
```

### Task 12: S7 gate and docs

- [ ] **Step 1: Full suite.** Run `uv run --group dev pytest -q test/`.
- [ ] **Step 2: A live run on banking claims** (banking_corpus is public). Write 10 invented claims about banking topics to `/tmp/claims.jsonl` (not in the repo). Run:

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 ARTMIND_PLAN_SHADOW=0 artmind query run --each /tmp/claims.jsonl --domain banking --compact
```

Pass when `items` is 10, no step errored (read the JSONL's `path` entries), and `exec_ms_p90` is at most 3000.
- [ ] **Step 3: Docs.** In CLAUDE.md's `artmind/query_run/` row, add: "`each` (`query run --each FILE --domain D`: a fixed needle + survey program per claim, the claim's source left out, one bundle per item in `logs/runs/<handle>.jsonl`, a summary printed; in-process; `--maxItems`)". In the same row, add "step bindings `{node.output}` (`facts.bind_outputs`; a map may run `over` a bound list)" and "`ARTMIND_PLAN_SHADOW_RUN=0|1|all` records a `run_shadow` event per traced turn". Commit with `docs: query run --each, bindings and shadow dry runs in CLAUDE.md (P3 S5-S7)`.
- [ ] **Step 4: Handover.** Add the S5–S7 status and the S6 and S7 gate numbers (counts and milliseconds) to the P3 entry of `docs/query-routing-study/15-handover-2026-11-07.md`'s "Next steps". Commit.

---

## Self-review notes

- **Spec coverage:**
  - §4.1 grammar and load rules (earlier node, not a sibling, known output, type fit, whole token, list only into comma-split options) → Task 1;
  - §4.2 vocabulary → Task 2;
  - §4.3 planner (`binds`, `over_bound`, `steps` keeps the placeholder because `render` leaves it) → Task 3;
  - runtime binding, unbound skip, bound map with `max` → Task 4;
  - §7.2 flag, LLM-step skip, ids and counts only, `covers_agent`, timeout, stats block → Tasks 6–8;
  - §8.6 `--each` (in-process, caps, a span per item, JSONL under `logs/runs/`, summary, no LLM) → Tasks 10–11;
  - §9.1 live row → Task 9;
  - the S7 gate → Task 12.
- **Limits of this plan:**
  - A `when` predicate cannot name a node inside a bound map, which is the same as for planned maps.
  - An item's steps are children of the run span, not of its item span.
  - `window_docs` reads only the producing step's rows (spec §4.2), so the window holds only documents that step returned.
- **Not here:** routes that use bindings (PA3 `episode`, B11), the subplan node (S11), P3b (S8–S13).
