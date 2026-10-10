# View format reference

A view is a folder with two files, stored at `<vault>/.artmind/domains/views/<name>/`
(or `~/.artmind/domains/views/<name>/` outside a vault):

```
view.yaml   the machine-read contract (below)
SKILL.md    guidance an agent reads after picking the view from the catalogue
```

`artmind setup` never touches this directory: views are user data, committed with the vault.
Only these two files are copied when a draft is saved.

## view.yaml

```yaml
name: regulation_exposure            # ^[a-z][a-z0-9_]*$ ; must equal the folder name
version: 1                           # positive int; bump on ANY cypher/params/presentation change
domains: [finance]                   # a run for finance.retail is allowed; a sibling domain is not
summary: Regulations reaching a product through any depth of PART_OF / GOVERNED_BY   # one line
examples:                            # 1-5 questions; the catalogue shows them and agents route on them
  - "Which regulations apply to the mortgage product?"
params:
  product:
    type: entity
    entity_class: PRODUCT            # optional; narrows auto-resolve (case-insensitive)
    description: The product to start from
  max_depth:
    type: int
    default: 4                       # no default = required
    min: 1
    max: 6
cypher: |
  MATCH (p:Entity {_id: $product}) ... RETURN r._id AS _id, r.name AS regulation
max_rows: 200                        # optional, default 200
presentation:
  format: table
  table: {columns: [regulation, hops], headers: {hops: Distance}}
provenance:
  created: 2026-10-09
  origin: authored                   # authored | promoted
  source_question: null              # required (non-empty) when origin is promoted
```

Unknown keys are an error. `materialize` is rejected: views always run live. Strings in
lists (`domains`, `examples`, enum `values`) must be real strings: quote YAML booleans
(`'no'`, `'yes'`), which would otherwise parse as `false`/`true`.

### Parameters

Param names are snake_case (`^[a-z][a-z0-9_]*$`); `domains` is reserved.

Types: `string`, `int`, `float`, `bool`, `date` (ISO-8601, passed to Cypher as a string:
use `date($x)` in the query), `enum` (with `values: [..]`), `entity`, `list` (with
`items: <string|int|float|bool|date>`; the CLI value is comma-separated).

Per param: `default`, `min`/`max` (int/float only), `description`, `entity_class`
(entity only). Non-finite floats (`nan`, `inf`) are rejected. An `entity` param cannot
have a default: the user always names the entity.

An `entity` param is given a name on the CLI and resolved to the entity's `_id`, which is
what `$param` holds in the Cypher (`MATCH (p:Entity {_id: $product})`). `@<_id>` skips
resolution. Resolution accepts only names with fulltext evidence: one exact-name hit (or a
single hit) is taken, several are `needs_disambiguation`, none is `no_match` with
`suggestions` (nearest entities by embedding, to rerun with `@<_id>`).

### Validation (load, validate, test and save all run it)

1. required keys, types, no unknown keys, name equals the folder name;
2. the Cypher is one statement and passes the read-only check; string literals, comments
   and backtick spans are masked first, so a keyword or `$name` inside them neither
   triggers nor satisfies a check. `$domains` must appear in real Cypher;
3. every `$name` in the Cypher is a declared param (or `$domains`); every declared param is used;
4. every column the presentation names is a `RETURN ... AS <alias>` of the **last** RETURN.
   Aliases must be plain identifiers: `` AS `a` `` is accepted (unwrapped to `a`), but
   `` AS `my col` `` is rejected. `RETURN *` is refused. A `COLLECT { ... RETURN }`
   subquery inside the final RETURN confuses the check: move it into a `WITH`;
5. defaults satisfy their own type, range and enum;
6. a promoted view has a non-empty `provenance.source_question`;
7. `SKILL.md` starts with `---` frontmatter whose `name` equals the view's name and which
   has a `description`.

### Saving and versions

`views save` validates the draft, then copies `view.yaml` + `SKILL.md` atomically.

- If a view of that name is saved already and cypher, params or presentation differ, the
  draft's `version` must be **greater** than the saved one.
- Any version **downgrade** is refused, even with an unchanged query.
- Edits that do not change the fingerprint (SKILL.md text, summary, examples, max_rows)
  need no bump.
- There is no force flag.

### Running

Runs bind `$domains` (requested domains expanded to sub-domains) and one dict of params.
There is a 60 s server-side timeout and `max_rows` truncation (`truncated`, `rows_total`).
Driver/Neo4j errors surface as `view <name> v<N> failed: ...`.

### Presentation

Exactly one sub-block, matching `format`:

| format | block | renders |
|---|---|---|
| `table` | `{columns: [..], headers: {col: Label}}` (headers optional) | markdown table |
| `list` | `{label: col, detail: col}` (detail optional) | `- **label** — detail` |
| `tree` | `{id: col, parent: col, label: col}` | nested list; rows with a missing/null parent are roots; a node reached twice (cycle, second parent) is shown once more as `label ↻ (see above)` |
| `mermaid_graph` | `{source: col, target: col, edge_label: col}` (edge_label optional) | `graph LR` |
| `mermaid_flow` | same keys | `flowchart TD` |

Truncated results end with `_Showing N of M rows._`

## The view's SKILL.md template

```markdown
---
name: <view name>
description: <when to use this view, in one or two sentences; what it is not for>
---

# <view name>

## When to use / when not to
## Output            (what each column means)
## Presenting the answer   (what to lead with, how to collapse a large result, what to point out)
## Grounding        (how to cite: e.g. pass the _id values to `query entity-context`)
## Known limits
```

## Testing a draft

`artmind views test --path <draft> --domain <d> [--param name=value ...] --compact`
writes nothing and returns the run envelope plus `rendered` (markdown per the
presentation), the EXPLAIN `plan` (operator tree) and `warnings`: zero rows, truncation to
`max_rows`, an ambiguous entity (`needs_disambiguation`, rerun with `@<_id>`) or an
entity that matched nothing (`no_match`, with `suggestions`). `artmind views validate` results carry the folder `path`.

## Cypher checklist

- `x._domain IN $domains` on every matched node (`all(x IN nodes(path) WHERE x._domain IN $domains)` for paths).
- Cypher literals must match the stored `entity_class` / `rel_type` exactly: `rel_type` is
  upper-cased and class names come from the `metadata` / `entity-listing` output (schemas
  use UPPER_SNAKE names such as `REGULATION`). A param's `entity_class` filter, by
  contrast, is compared case-insensitively.
- Entity-to-entity edges are `RELATES_TO`; filter `rel.rel_type = 'UPPER_CASE'` (stored upper-cased).
- Computed properties are underscore-prefixed (`_id`, `_domain`).
- Bound every variable-length path; `ORDER BY` a stable column; alias every returned column.
- Return `_id` for entities so the answer can be grounded.
- Read-only keywords (CREATE, MERGE, SET, DELETE, REMOVE, DROP, LOAD CSV, write APOC) are rejected.
