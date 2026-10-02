# Graph hierarchy traversal + artmind-query skill modularisation — design

Status: approved (2026-10-02). Not implemented.

## 1. Problem

Users ask questions like "who is in X's reporting line", "list every unit under
Retail Banking", or "who does this analyst ultimately report to". None of the
current `query graph` commands can answer these:

| Command | Why it falls short |
|---|---|
| `pattern4`, `pattern8`, `entity-context` | One hop only. |
| `pattern3` | One hop, and it mixes every `rel_type` together. |
| `pattern5` | Paths between two *known* endpoints, capped at depth 5, never more than 3 paths. |
| `text2cypher` | Can write `-[*]-`, but nothing bounds it. It also has to guess the edge orientation, and in this graph the guess is often wrong (see below). |

Two properties of the graph make the obvious Cypher (`MATCH (root)<-[:REPORTS_TO*]-(x)`)
both wrong and unsafe:

1. **Every entity-to-entity edge has the same type.** All of them are
   `:RELATES_TO`, and the meaning is stored in the `rel_type` property. That
   value is stored upper-cased (`ingest.py:3176`), e.g. `REPORTS_TO`. A typed
   variable-length pattern therefore can't express "follow REPORTS_TO only".
2. **Edges don't all point the same way in the hierarchy.** In
   `banking.organization_schema.yaml`, `ROLE_PERSON reports_to ROLE_PERSON` and
   `member_of` / `reports_into` / `part_of` point from child to parent. `manages`,
   `leads` and `contains` point from parent to child. A real org chart mixes the
   two, so "children" means *incoming* edges for some rel types and *outgoing*
   edges for others.

It is a graph, not a tree. Matrix reporting gives a node several parents, and
extraction noise can create cycles (A reports to B, B reports to A). If an
unbounded walk is allowed to enumerate every path through a dense DAG, the
work grows exponentially.

## 2. Proposal: `artmind query graph hierarchy`

The command has a name rather than a number (`pattern11`). That follows the
convention of the later additions (`timeline`, `conflicts`), and it lets the
skill's routing table describe what the command does instead of the agent
having to remember what a number means.

```bash
artmind query graph hierarchy --domain <d> --entityId <id> \
    --childOf REPORTS_TO --childOf MEMBER_OF --parentOf MANAGES \
    [--direction down|up] [--maxDepth 5] [--limit 1500] \
    [--nodeClass ROLE_PERSON --nodeClass ORGANIZATIONAL_UNIT] --compact
```

### Options

| Option | Meaning |
|---|---|
| `--entityId` / `--entityName` (+ optional `--entityClass`) | The root. A name must resolve to **exactly one** entity. If it matches several, the command errors and lists the candidates. It never fans out, because a fan-out would silently merge separate trees. |
| `--childOf REL` (repeatable, comma-split) | Edge runs `(child)-[rel_type=REL]->(parent)`. Examples: `REPORTS_TO`, `PART_OF`, `MEMBER_OF`, `REPORTS_INTO`. |
| `--parentOf REL` (repeatable, comma-split) | Edge runs `(parent)-[rel_type=REL]->(child)`. Examples: `MANAGES`, `LEADS`, `CONTAINS`. |
| `--direction` | `down` (default) returns descendants. `up` returns ancestors, i.e. the chain of command. `up` uses the same edge spec with the orientation reversed, so one mental model covers both directions. |
| `--maxDepth` | Default 5. Values from 1 to 10 are accepted. Anything above 10 is an error, not a silent clamp, so the agent finds out it asked for too much. |
| `--limit` | Total node budget. Default 1500, hard maximum 2000. |
| `--nodeClass` (repeatable) | The classes the walk is allowed to *enter*. This stops a generic edge such as `PART_OF` from leaking out of the org chart into products or processes. If omitted, any `:Entity` in the selected domains is allowed (decided: no smarter default). |

Rel-type values are normalised with ingest's own rule (upper-case,
non-alphanumerics become `_`). Validation errors are raised for:

- the same rel type appearing under both `--childOf` and `--parentOf`;
- `--maxDepth` or `--limit` out of range.

**Discovery mode.** If neither `--childOf` nor `--parentOf` is given, the
command does not traverse anything. It returns `edge_candidates` for the root:

```
{rel_type, direction: in|out, count, neighbor_classes}
```

This fills a real gap. `graph metadata` reports only the Neo4j relationship
*type*, which is always `RELATES_TO`, so today the agent has no cheap way to
learn the `rel_type` vocabulary around an entity. The skill turns the
candidates into an edge spec with one rule:

- an `in` edge on the root means the neighbour points at the root;
- for `REPORTS_TO` that makes the neighbour a child, so pass `--childOf REPORTS_TO`.

### Algorithm: bounded BFS in Python, one read session

A single Cypher query was considered and rejected. The two candidates were:

- a variable-length or quantified path pattern (QPP) with
  `startNode(r)` predicates;
- the GQL `SHORTEST 1` selector.

The QPP version enumerates every path. `SHORTEST` handles cycles, but it needs
a recent Neo4j (local instances vary). Neither version can tell you *where* it
stopped, report multi-parent nodes, or report cycles. A level-at-a-time BFS
makes every safeguard explicit and testable:

```
visited = {root: 0}; frontier = [root]
for depth in 1..maxDepth:
    rows = LEVEL_QUERY(frontier, budget_left + 1)   # one round trip per level
    for (parent_id, child, rel_type) in rows:         # ordered: parent, name
        if child unseen:  add at `depth`, parents=[parent]
        elif visited[child] == depth:  append parent  # DAG / matrix org
        else:  record back_edge(parent -> child)      # cycle or shortcut; not followed
    if budget exceeded: truncate, reason="limit", stop
    frontier = newly added ids; stop if empty
if frontier still non-empty: probe one more level with LIMIT 1 → reason="max_depth"
```

```cypher
UNWIND $frontier AS pid
MATCH (p:Entity {_id: pid})-[r:RELATES_TO]-(c:Entity)
WHERE <domain_predicate("c")>
  AND (   (r.rel_type IN $childOf  AND startNode(r) = c)   -- direction=down
       OR (r.rel_type IN $parentOf AND startNode(r) = p))  -- (swapped for up)
  AND ($nodeLabels IS NULL OR any(l IN labels(c) WHERE l IN $nodeLabels))
RETURN pid AS parent_id, c {._id, .name, .entity_class} AS node, r.rel_type AS via
ORDER BY parent_id, node.name
LIMIT $levelCap
```

How each risk is covered:

| Risk | Safeguard |
|---|---|
| Cycles | The visited set. No node is expanded twice. |
| Exponential path enumeration | Only the frontier is expanded each level, never full paths. |
| Unbounded depth | `maxDepth` is capped at 10. |
| Huge subtrees | A total `--limit` and a per-level `LIMIT`. |
| Leaking across domains | `domain_predicate` on every hop. |
| Leaking across semantic areas | `--nodeClass`. |

A truncated result says so (`truncated_reason`) rather than looking complete.
`maxDepth` round trips (10 at most) on one session is cheap next to the
daemon's start-up cost.

### Output (`rows` keeps the house convention)

```json
{
  "domains": [...], "query_type": "graph", "command": "hierarchy",
  "direction": "down", "parameters": {...},
  "root": {"_id": "...", "name": "...", "entity_class": "ROLE_PERSON"},
  "rows": [
    {"_id": "...", "name": "...", "entity_class": "...", "depth": 1,
     "parents": [{"_id": "<root>", "rel_type": "REPORTS_TO"}]}
  ],
  "summary": {"total": 42, "by_depth": {"1": 5, "2": 37}, "by_class": {...},
              "max_depth_reached": 2, "truncated": false, "truncated_reason": null,
              "multi_parent": 3, "back_edges": 1},
  "back_edges": [{"from": "...", "to": "...", "rel_type": "..."}]
}
```

`rows` is a flat list in BFS order with parent ids. That costs fewer tokens than
a nested tree, and the agent can rebuild a tree if it needs one. A
`--format tree` option is a non-goal for v1. `back_edges` is capped at 50
entries; `summary.back_edges` holds the true count.

### Known limitation to call out in the skill

A hierarchy can only be traversed if it was extracted as **edges**. The org
schema *also* declares `reports_to` as a ROLE_PERSON **property**
(`banking.organization_schema.yaml`, under `properties`). Where extraction filled
in only that property, the walk returns nothing. When `hierarchy` comes back
empty, the skill should fall back to `pattern1` on the class and read the
property. Fixing this at extraction time is out of scope here.

### Wiring checklist

- `artmind/graph_query.py`: add `hierarchy()`, `_hierarchy_level_query()` and
  `hierarchy_edge_candidates()`, plus `HIERARCHY_MAX_DEPTH` and
  `HIERARCHY_MAX_LIMIT`. Reuse `_entity_selector` and `domain_predicate`. Run
  all levels inside one `read_session()`.
- `artmind/cli.py`: add the `graph hierarchy` command, update the `graph` group
  docstring, and add the command to `COMMAND_GROUPS`
  (`test/test_cli_guide.py` enforces this).
- `CLAUDE.md` § Command routing quick reference.
- `justfile`: add a `query-graph-hierarchy` recipe next to its siblings.
- `text2cypher.py` prompt: one line that sends unbounded "all descendants"
  questions to `hierarchy` instead.
- Tests (`test/test_graph_query.py`, CLI tests): use scripted `run`
  side-effects, one per level, following the `run_side_effect` pattern in
  `test_update.py`. Cover:
  - the frontier and rel-type params sent at each level, asserted on the
    params themselves rather than on counts;
  - a cycle producing a back edge with no re-expansion;
  - a multi-parent node at the same depth;
  - `limit` truncation;
  - the `max_depth` probe;
  - name fan-out raising an error;
  - discovery mode;
  - the validation errors.
- End to end: run with `ARTMIND_NO_PROXY=1` against the banking org domain.
  The new subcommand is proxied through `serve`, so a running daemon won't know
  it exists until it is restarted.

## 3. Skill changes for hierarchy

SKILL.md gets three things:

- one Retrieve table row: "everyone under X / X's whole reporting line / full
  org subtree / chain of command";
- one routing note: "pattern4 is one hop; use `hierarchy` for transitive";
- one fallback-ladder line: "hierarchy empty → discovery mode → property
  fallback".

The details go into `references/hierarchy.md`:

- the discovery-to-edge-spec recipe;
- a worked org example;
- how to read `truncated` / `back_edges` / `multi_parent`. The rule is to state
  truncation in the answer, and never to claim "these are all".

## 4. Should artmind-query be modularised? Yes, but there is a blocker first

### Review

SKILL.md is 467 lines, and the full body is loaded for every question. By
section:

| Lines | Section | Needed when |
|---|---|---|
| 1–73 | Grounding, inputs, structural schema | Always |
| 75–259 (~185, **40%**) | Structured store, store routing, worked examples A/B/C, `--asOf` threading | Only when `db bridge` returns tables. A pure-graph domain pays for all of it. |
| 261–363 | Discover / Resolve / Retrieve table + routing notes | Always (the core) |
| 337–363 | `--asOf` semantics, `entity-history`, `_temporal_props` | Only for temporal questions. `--asOf` is also explained three separate times. |
| 381–450 (~70) | Adjudicate: two conflict shapes, fan-out, supersession checks | Only when a conflict or disagreement shows up |
| 452–467 | Fallback ladder, answer style | Always |

### Proposed shape

```
artmind/skills/artmind-query/
  SKILL.md                      ~200 lines: protocol spine + full Retrieve table + ladder
  references/structured-store.md   db commands, store routing, examples A/B/C, hybrid --asOf
  references/temporal.md           --asOf floor vs presence-flag vs point-in-time; entity-history; timeline; _temporal_props
  references/conflicts.md          both :Conflict shapes, fan-out caveat, supersession re-check
  references/hierarchy.md          new (§3)
```

SKILL.md keeps the *always-on rules* in one line each and points to the
reference with an explicit trigger. Example:

> If `db bridge` returns any tables, read `references/structured-store.md`
> before classifying the question.

Each rule stays in SKILL.md:

- never blend disagreeing sources;
- don't pass `--asOf` to entity commands;
- `--compact` everywhere.

### Blocker: the main consumers can't read reference files

`artmind/webui/tool_gate.py:35` denies `Read`/`Grep`/`Glob` on the **QA (chat UI)
and benchmark profiles** (`profiles.py`, `filesystem_access=False`). Bash is
limited to single `artmind …` invocations. Skill reference files are loaded
with `Read`, so on exactly the surfaces that run this skill most, a split skill
would leave the agent unable to open the detail it was pointed to. Today it
works only because everything is in SKILL.md.

Before splitting, one of these is needed:

- **(Recommended)** Let the `PreToolUse` hook (`webui/agent.py:101-113`) allow
  `Read` when the path, after `realpath`, falls inside an allowed skills root.
  The allowed roots are `PACKAGE_SKILLS_DIR` and `<cwd>/.claude/skills`. The
  realpath check matters because vault skills are symlinks into the package.
  `Grep`/`Glob` stay denied. The grounding threat model is unaffected, because
  skill files are package assets and not vault content. Add a test in
  `test/test_tool_gate.py`, including a `../` traversal case and a
  symlink-escape case.
- An alternative is an `artmind skill-ref <skill> <ref>` command that already
  passes the bash gate. It works, but it adds CLI surface only to work around a
  permission rule.

### Test impact of the split

`test/test_query_skill_structural_schema.py` reads only SKILL.md and checks
that every canonical label name appears in it. There are two ways to handle
this:

- keep the structural schema in SKILL.md, which this proposal does;
- change the test to read every `*.md` in the skill directory.

Either works. The second is more robust if more content moves later.

`webui/help.py` reads only SKILL.md front-matter, so it is unaffected. Seeding
copies whole skill directories and the wheel ships `skills/**/*`, so
`references/` reaches the run folder and vaults without changes. The
`artmind-create-schema` skill already ships one.

## 5. Suggested order

1. Change the tool-gate `Read` exception and its tests.
2. Split SKILL.md into references with no content change; review the diff as a
   pure move.
3. Implement `hierarchy` (code, CLI, tests), then add `references/hierarchy.md`
   and the SKILL.md row.
4. Run `artmind setup`, restart `serve`, and run the end-to-end check on the
   banking org domain from the chat UI. That surface is the one that proves the
   `Read` exception works.

## Decisions (2026-10-02)

- Defaults: `--maxDepth` 5 (hard max 10), `--limit` 1500 (hard max 2000).
- `--nodeClass` defaults to any `:Entity` in the selected domains. No inferred default.
- `graph metadata` stays unchanged. The `rel_type` vocabulary comes only from
  `hierarchy`'s discovery mode.
