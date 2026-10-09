# kg_views — saved, parameterised graph queries with agent guidance — design

Status: approved (2026-10-09). Not implemented.

## 1. Problem

Neo4j has no views, materialized or otherwise. In artmind, questions that come up
again and again are re-derived from scratch every time:

- **Agent latency and token cost.** A recurring question walks the full
  `artmind-query` protocol (Route → Discover → Resolve → Retrieve → …): several CLI
  calls, sometimes an LLM `text2cypher` generation. Executing the Cypher is usually
  the cheap part.
- **No shareable lenses.** A team cannot commit "the org-structure view" or "the
  regulation-exposure view" of a domain to the vault and have every agent use it.
- **Depth/breadth the patterns can't reach.** `pattern1`–`pattern10`,
  `entity-context` and `hierarchy` are generic. Domain-specific shapes
  (class-filtered variable-length traversals, multi-rel-type walks, aggregations)
  fall to `text2cypher`, which is unbounded and non-deterministic.
- **Consistency (secondary).** `text2cypher` can write different Cypher, and get
  different answers, for the same question.

## 2. Goals and non-goals

Goals (v1):

1. A **kg_view**: a reviewed, parameterised, read-only Cypher query, plus a
   `SKILL.md` that tells an agent when and how to use it.
2. Views are **domain content**: committed to the vault next to the schemas,
   synced with the vault, never overwritten by `artmind setup`.
3. Agents find views through a **catalogue** call early in `artmind-query`, and
   skip Discover/Resolve/Retrieve when a view fits.
4. **Entity parameters auto-resolve** from a name to an `_id` inside the view run.
5. A **deterministic markdown renderer** (table / list / tree / mermaid) so the
   agent can pass formatted output through without re-formatting rows.
6. Two creation paths, **authored** and **promoted**, sharing one
   draft → test → approve → save loop, driven by a new `artmind-create-view` skill.

Non-goals (v1), deliberately deferred:

- **Result caching / materialization.** Views run live on every call. The format
  keeps room for a future `materialize:` key (a cache keyed on the
  `ArtmindSyncState` bookmark + a view fingerprint), but v1 rejects that key.
- **Derived graph writes** (shortcut edges/nodes written back into Neo4j). That
  changes the projection pipeline and the read-only nature of query tools, so it
  needs its own design.
- **Mining query logs** to suggest views.
- **Multi-statement views** (one view = one Cypher statement).
- **Registering views as harness skills** (`.claude/skills/view-*`). The catalogue
  scales better and works the same on every agent host.
- **Live schema-drift checks** (`views validate --live`).

## 3. Storage layout

```
<vault>/.artmind/domains/views/<view_name>/     # inside a vault: VaultLayout.domains_dir / "views"
~/.artmind/domains/views/<view_name>/           # outside a vault: the run folder
    view.yaml
    SKILL.md
```

- **Flat layout.** The folder name equals `view.yaml`'s `name`. The domain binding
  lives in YAML, not in the path, so a view can cover more than one domain.
- **Never seeded or overwritten.** Unlike skills, opencode and schemas,
  `domains/views/` holds user-authored content. `scaffold_run_folder()` /
  `_seed_tree()` must leave it untouched. A regression test pins this.
- **The package ships no views.** Worked examples live in
  `artmind/skills/artmind-create-view/references/` as templates only.
- **Inside a vault, views are committed to git** (everything under `.artmind/` is
  committed by default; nothing in `GITIGNORE_BLOCK` should match `domains/views/`).

## 4. The view format

### 4.1 `view.yaml` — the machine-read contract

```yaml
name: regulation_exposure            # snake_case, unique, equals the folder name
version: 1                           # bumped by hand on any cypher/params/presentation change
domains: [finance]                   # family-expanded via expand_domain_family
summary: Regulations reaching a product through any depth of PART_OF / GOVERNED_BY
examples:                            # shown in the catalogue; drive agent routing
  - "Which regulations apply to the mortgage product?"
  - "What rules govern Product X, directly or indirectly?"
params:
  product:
    type: entity
    entity_class: Product            # optional; narrows auto-resolve
    description: The product to start from
  max_depth:
    type: int
    default: 4
    min: 1
    max: 6
    description: Maximum traversal depth
cypher: |
  MATCH (p:Entity {_id: $product})
  MATCH path = (p)-[:RELATES_TO*1..6]-(r:Entity {entity_class: 'Regulation'})
  WHERE length(path) <= $max_depth
    AND all(x IN nodes(path) WHERE x._domain IN $domains)
    AND all(rel IN relationships(path) WHERE rel.rel_type IN ['PART_OF', 'GOVERNED_BY'])
  RETURN r._id AS _id, r.name AS regulation, min(length(path)) AS hops
  ORDER BY hops, regulation
max_rows: 200
presentation:
  format: table
  table:
    columns: [regulation, hops]
    headers: {hops: Distance}
provenance:
  created: 2026-10-09
  origin: authored                   # authored | promoted
  source_question: null              # set when origin = promoted
```

**Fields**

| Key | Required | Notes |
|---|---|---|
| `name` | yes | `^[a-z][a-z0-9_]*$`; must equal the folder name. |
| `version` | yes | Positive int. |
| `domains` | yes | Non-empty list of domain names. |
| `summary` | yes | One line, shown in the catalogue. |
| `examples` | yes | 1–5 example questions. |
| `params` | no | Map of name → param spec (§4.2). |
| `cypher` | yes | One read-only statement. |
| `max_rows` | no | Default 200, the same as `TEXT2CYPHER_MAX_ROWS`. |
| `presentation` | yes | §4.3. |
| `provenance` | yes | `created` (date), `origin`, `source_question` (string or null). |

Unknown keys are an error. In particular `materialize` is rejected in v1.

**Authoring note.** `rel_type` values are stored upper-cased (`PART_OF`), and
every artmind-computed property is underscore-prefixed (`_id`, `_domain`). The
create-view skill must look up real class names and `rel_type`s with
`query graph structural-metadata` / `metadata` before drafting.

### 4.2 Parameters

Types: `string`, `int`, `float`, `bool`, `date` (ISO-8601), `enum` (with
`values: [...]`), `entity`, and `list` (with `items: <scalar type>`).

Optional per param: `default` (no default means required), `min` / `max`
(int/float), `description`, `entity_class` (entity only).

`domains` is reserved. The runner always binds `$domains`, and a view may not
declare it.

### 4.3 Presentation

```yaml
presentation:
  format: table | list | tree | mermaid_graph | mermaid_flow
  table:         {columns: [..], headers: {col: Label}}      # headers optional
  list:          {label: col, detail: col}                   # detail optional
  tree:          {id: col, parent: col, label: col}
  mermaid_graph: {source: col, target: col, edge_label: col} # edge_label optional
  mermaid_flow:  {source: col, target: col, edge_label: col} # edge_label optional
```

Exactly one sub-block, and it must match `format`. Every column it names must be
a `RETURN … AS <alias>` in the Cypher's final `RETURN` clause. Views must alias
every returned column the presentation uses.

### 4.4 Validation rules (load and save)

1. Schema: required keys, types, no unknown keys, name equals the folder name.
2. `text2cypher.validate_read_only(cypher)` and
   `text2cypher.validate_domain_scoped(cypher)` are reused unchanged.
3. Every `$ident` in the Cypher is a declared param or `domains`. Every declared
   param is used.
4. The presentation columns are among the final `RETURN`'s aliases.
5. Defaults satisfy their own type/range/enum.

### 4.5 `SKILL.md` — agent-facing guidance

Skill-style frontmatter (`name`, `description`) so it stays portable. Body sections:

- **When to use / when not to.** For example: "for direct obligations only, `pattern2` suffices".
- **Output.** What each column means.
- **Presenting the answer.** Judgement calls the structured `presentation` field
  can't express: what to lead with, how to collapse large results, what to point out.
- **Grounding.** How to cite, e.g. "pass the `_id`s to `query entity-context`".
- **Known limits.**

The agent reads it only after picking the view from the catalogue.

## 5. Module: `artmind/kg_views/`

| File | Job | Depends on |
|---|---|---|
| `__init__.py` | Public surface re-exports. | — |
| `model.py` | `ViewSpec`, `ParamSpec`, `Presentation` dataclasses; `parse_view(dict, folder_name)`; every rule in §4.4. Pure. | `text2cypher.validate_*` |
| `store.py` | `views_dir()` (vault → `VaultLayout.domains_dir / "views"`, otherwise run folder); `list_views(domains)`, `load_view(name)`, `save_view(draft_dir)`, `delete_view(name)`; the version guard (§7); domain filtering via `expand_domain_family`. | `model`, `vault`, `paths` |
| `params.py` | Coerce `--param k=v` strings to typed values; defaults/ranges/enum; entity auto-resolve and the `@<_id>` bypass (§7). | `model`, `vector_query`, `graph_query` |
| `runner.py` | Bind `$domains` + params, execute in `read_session`, apply `max_rows` truncation, build the result envelope (§6.3). | `graph_query`, `params`, `render` |
| `render.py` | `render_markdown(rows, presentation, truncated, rows_total) -> str` for every format. Pure. | `model` |

Views are read from disk **on every request** (as `schema_reference.py` does), so
a warm `serve` daemon never serves a stale view.

## 6. CLI

### 6.1 Read path — under `query` (proxied through `serve`)

```
artmind query views list [--domain X ...] [--compact]
artmind query views show NAME [--compact]
artmind query views run  NAME --domain X [--param k=v ...] [--render markdown] [--compact]
```

- `list`: `{views: [{name, version, domains, summary, examples, params: {name: {type, required, description, entity_class?}}, presentation_format}], invalid: [{name, error}]}`.
  Without `--domain` it lists all views.
- `show`: the full parsed `view.yaml` plus the `SKILL.md` text.
- `run`: the result envelope (§6.3). `--param` is repeatable; `k=v` is split on the
  first `=`. For `list` params, the value is comma-split.

### 6.2 Authoring path — top-level `views` group (not proxied)

```
artmind views validate [NAME | --path DIR]                       # static checks, no Neo4j
artmind views test     --path DIR --domain X [--param k=v ...]   # validate + live run + EXPLAIN; writes nothing
artmind views save     --path DIR                                # validate + version guard + copy into views_dir()
artmind views delete   NAME
```

Drafts are plain folders in the note scratch dir (`vault.note_scratch_dir`, or
the session scratchpad outside a vault). `views test` prints the envelope with
`rendered` always included, the EXPLAIN plan, and a warning when zero rows come back.

All new commands are added to `COMMAND_GROUPS` in `cli.py` (so
`test/test_cli_guide.py` passes), to the `query` group docstring, and to the
`justfile` where a recipe is useful.

### 6.3 Result envelope (`query views run`)

```json
{
  "view": "regulation_exposure", "version": 1, "domains": ["finance"],
  "status": "ok",
  "params": {
    "product": {"input": "mortgage", "_id": "ent_9f2…", "name": "Retail Mortgage", "entity_class": "Product"},
    "max_depth": 4
  },
  "rows": [ ... ], "rows_total": 37, "truncated": false,
  "presentation": {"format": "table", "table": { ... }},
  "rendered": "| Regulation | Distance |\n…"
}
```

- `status`: `ok | needs_disambiguation | no_match`. Only `ok` carries `rows`,
  `rows_total`, `truncated` and (with `--render markdown`) `rendered`.
- `needs_disambiguation` carries `param` and `candidates: [{_id, name, entity_class, observation_count}]` (top 5).
- `no_match` carries `param` and `input`.
- The resolved entity is echoed back so the agent can state its interpretation.
- Rows go through `strip_internal_props` / `serialize_value` like other query output.

## 7. Behaviour details

**Entity auto-resolve** (`params.py`). It is deterministic and has no score thresholds:

1. Call `vector_query.entity_resolve(domains, input, topK=10)`.
2. If the param declares `entity_class`, drop candidates of other classes.
3. Exactly one case-insensitive exact-name match → accept it.
4. Otherwise exactly one candidate → accept it.
5. Otherwise, more than one candidate → `needs_disambiguation` (top 5). None → `no_match`.

`@<_id>` skips resolution but is still checked: the node exists, `_domain` is in
the requested (expanded) domains, and `entity_class` matches if one is declared.
Otherwise it raises a `ClickException`. If the embedding service is unavailable,
resolution must degrade to the fulltext leg rather than fail. During
implementation, check how `entity_resolve` handles a dead vector leg today, and add
the fallback in `params.py` if it doesn't already degrade.

**Domain guard.** `run --domain X` fails unless every requested domain is covered by
the view's `domains` after family expansion.

**Errors.** Invalid param values give a `ClickException` naming the param and its
spec. A view that fails to load or validate is skipped by `list` (reported under
`invalid`) and is a `ClickException` for `show`/`run`. A `Neo4jError` is wrapped as
`view <name> v<N> failed: …`. Stale-store warnings on stderr fire as for every
`query` command (same group result callback).

**Version guard** (`views save`). Fingerprint = hash of the canonical
`cypher` + `params` + `presentation`. If a view with that name exists and the
fingerprint differs while `version` is not greater than the existing one, it
refuses. There is no `--force`; bump the version.

**Renderer.**
- `table`: a markdown table of the listed columns, with headers.
- `list`: `- **label** — detail`.
- `tree`: a nested markdown list built from id/parent. Orphans (parent missing
  or null) become roots. A node reached again (a cycle or second parent) renders
  as `label ↻ (see above)` and is not expanded.
- `mermaid_graph` / `mermaid_flow`: a ```` ```mermaid ```` block (`graph LR` / `flowchart TD`)
  with stable node ids and labels escaped for quotes, brackets and pipes.
- A truncated result ends with `_Showing N of M rows._`

## 8. Agent integration

### 8.1 `artmind-query` SKILL.md

- The protocol becomes **Route → Views → Discover → Resolve → Retrieve → Ground →
  Adjudicate**. Views needs the domain set from Route, and a matching view
  replaces Discover/Resolve/Retrieve.
- New `### 1. Views` section. Discover…Adjudicate are renumbered 2–6.
- **Every cross-reference by number becomes a reference by name** ("skip straight
  to Retrieve", not "step 3"). Check `references/*.md` and `artmind/opencode/` for
  the same.
- Content of the Views step:
  - Run `artmind query views list --domain X --compact` **once per domain per
    conversation**. Re-list if the user says they just added or changed a view.
  - If the question matches a view's `summary`/`examples` → `views show NAME` →
    follow its SKILL.md → `views run … --render markdown --compact`.
  - If Route already returned `resolved_entities`, pass them as `--param k=@<_id>`.
  - `needs_disambiguation` → ask the user, then rerun with `@<_id>`. `no_match` →
    tell the user and fall through.
  - No fitting view, or an empty or unhelpful result → continue with Discover as normal.

### 8.2 New skill `artmind/skills/artmind-create-view/`

`SKILL.md`, `references/view_format.md` (the §4 contract), and
`references/examples/` (two worked views, e.g. a table-format traversal and a
tree-format hierarchy). Flows:

1. **Authored:** gather the lens, domain(s) and example questions → look up real
   classes and `rel_type`s with `query graph structural-metadata` / `metadata` →
   draft `view.yaml` + `SKILL.md` in the scratch dir → `views test` → show rendered
   rows and the plan → revise → on approval, `views save`.
2. **Promoted** ("save this as a view"): start from Cypher already run (from
   `text2cypher` output, or reconstructed from the pattern calls used); turn
   literals into parameters (an entity `_id` becomes a `type: entity` param); set
   `origin: promoted` and `source_question`; then the same test → approve → save loop.
3. **Edit:** `views show` → modify → bump `version` → test → save.

Skills are seeded and linked as usual (`artmind setup` / `artmind init` /
`just dev-refresh-skills`).

### 8.3 opencode persona

`artmind/opencode/` gets the same Views step, so opencode/ACP agents route the same way.

## 9. Testing

Hermetic, in `test/`:

| File | Covers |
|---|---|
| `test_kg_views_model.py` | Fixture YAMLs for every §4.4 rule; presentation/alias matching; reserved `domains`; rejection of `materialize`. |
| `test_kg_views_params.py` | Type conversion, ranges, enum, list; `entity_resolve` patched for single / exact-name / ambiguous / none / class-filtered cases; `@<_id>` checks. |
| `test_kg_views_runner.py` | The `run_side_effect` recorder pattern from `test_update.py`: assert **the Cypher and the parameters sent** (resolved `_id`, family-expanded `$domains`), never counts alone; truncation; envelope shape per status. |
| `test_kg_views_render.py` | Golden markdown per format; tree cycles, multiple parents and orphans; mermaid escaping; truncation footer. |
| `test_kg_views_store.py` | Vault vs run-folder resolution; list with an invalid view; version guard; **`artmind setup` leaves `domains/views/` untouched**. |
| `test_kg_views_cli.py` | `CliRunner` over `query views list/show/run` and `views validate/test/save/delete`. |
| `test_cli_guide.py` (existing) | The new commands are routed in `COMMAND_GROUPS`. |

Manual end-to-end before calling it done: author one real view against
`~/artmind_vaults/my_work`, run it with `ARTMIND_NO_PROXY=1`, then through a freshly
restarted `serve`, to prove the proxy path serves the new commands.

## 10. Future work (not v1)

- `materialize: cache`: a result cache keyed on the sync bookmark + view
  fingerprint + params; freshness shown in `views list`.
- Derived graph structure written by views (separate design).
- `views validate --live`: check class/rel_type literals against `structural-metadata`.
- Query-log mining to suggest views.
- Opt-in `promote_to_skill: true` for very frequently used views.
- Admin-UI tab listing views (like the Schemas tab).
