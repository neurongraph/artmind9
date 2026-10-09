---
name: artmind-create-view
description: Creates, promotes and edits saved graph views for the artmind knowledge graph -- reviewed, parameterised, read-only Cypher queries (view.yaml) plus agent guidance (SKILL.md) that artmind-query runs with one call. Use when an operator wants a reusable lens on a domain ("save this as a view", "create a view for the org structure"), or to edit a view that already exists.
---

# artmind View Creator

A **view** is a saved, parameterised, read-only Cypher query plus guidance on when and
how to use it. It lives with the domain's schemas (committed with the vault), shows up in
`artmind query views list`, and lets `artmind-query` answer a recurring question in one
call instead of walking Discover, Resolve, Retrieve.

Read `references/view_format.md` before drafting: it is the full contract (every key,
every validation rule, the presentation formats). `references/examples/` holds two
worked views to copy from.

## Ground rules

- A view is **one read-only Cypher statement** (a single trailing `;` is tolerated).
  No writes, no multi-statement scripts.
- Every node you match must be scoped with `x._domain IN $domains`, and `$domains` must
  appear in the real Cypher (a mention in a comment or string does not count). The runner
  binds `$domains` itself (the requested domains, expanded to their sub-domains); never
  declare it as a parameter.
- Look up real names. Entity classes, `rel_type` values and property names are
  case-sensitive and come from the graph, not from memory. `rel_type` is stored
  upper-cased (`PART_OF`); computed properties are underscore-prefixed (`_id`, `_domain`).
  Every entity-to-entity edge is `:RELATES_TO`; its meaning is `rel.rel_type`.
- **Get the operator's explicit approval before `views save` or `views delete`.** Neither
  prompts: `save` overwrites the saved view and `delete` removes the folder at once.
  Approval of the rendered test output is the gate for save; name the view and ask
  before a delete.
- Results are capped at `max_rows` (default 200); set it deliberately and expect the
  `truncated` flag on larger results.
- Drafts live in a plain temporary folder, never in the views directory and not under
  `.artmind/data/` (that is committed with the vault). Create one with `mktemp -d` and
  put the draft in `<tmp>/<view_name>/` (the folder name must equal the view's `name`).
  Only `view.yaml` and `SKILL.md` are copied on save; anything else in the folder is ignored.

## Flow A: authored ("make me a view for ...")

1. **Gather**: the lens in one sentence, the domain(s), and 2-5 example questions a user
   would ask. Ask for what is missing; do not guess the domain.
2. **Look up the real vocabulary** for those domains:
   ```bash
   artmind query graph metadata --domain <d> --compact        # labels, properties, relationship types
   artmind query graph entity-listing --domain <d> --compact   # class names with sample entities
   ```
   (`structural-metadata` gives document names and counts only; it does not help here.)
   Note the exact entity classes, `rel_type` values and properties you will use, and
   check the direction of each relationship you traverse.
3. **Draft** `<tmp>/<name>/view.yaml` and `<tmp>/<name>/SKILL.md`:
   - parameters (snake_case names) for everything a user would vary; an entity the user
     names is a `type: entity` parameter (it auto-resolves from a name to an `_id`; it
     cannot have a default);
   - bound variable-length paths (`*1..N`) and an `ORDER BY` on a stable column;
   - alias every column the presentation uses with a plain identifier
     (`RETURN x.name AS name`; `` AS `my col` `` is rejected); return `_id` for entities
     so answers can be grounded;
   - pick the `presentation` format that matches the shape of the result (table, list,
     tree, mermaid_graph, mermaid_flow);
   - the view's own `SKILL.md`: when to use / not use, what each column means, how to
     present the answer, how to ground it, known limits (template in
     `references/view_format.md`).
4. **Validate**: `artmind views validate --path <tmp>/<name> --compact`. Fix every error.
5. **Test live**:
   ```bash
   artmind views test --path <tmp>/<name> --domain <d> --param <entity_param>=<a real name> --compact
   ```
   It runs the query for real (60 s server-side limit) and returns `rows`, `rendered`,
   the EXPLAIN `plan` and a `warnings` list (zero rows, truncation, an ambiguous or
   unmatched entity). Zero rows almost always means a wrong class name or `rel_type`.
   If `status` is `needs_disambiguation`, pick a candidate and rerun with `@<_id>`. A
   database failure is reported as `view <name> v<N> failed: ...`.
6. **Show the operator** the rendered markdown, a few raw rows, and a one-line reading of
   the plan (look for `AllNodesScan`/`CartesianProduct` on a large domain: add a label or
   a tighter anchor). Revise and repeat steps 4-6 until they approve.
7. **Save** (only after the operator says so): `artmind views save --path <tmp>/<name> --compact`,
   then confirm with `artmind query views list --domain <d> --compact` and tell the
   operator that agents pick it up on their next catalogue listing (a conversation that
   already listed views needs the user to say a view was added).

## Flow B: promoted ("save this as a view")

Start from a query that already answered a question in this conversation: the
`generated_cypher` from `query graph text2cypher`, or the pattern calls you made.

1. Take the Cypher. If it came from pattern calls, reconstruct one equivalent statement.
2. **Turn literals into parameters**: a hard-coded entity `_id` or name becomes a
   `type: entity` parameter; thresholds and depths become `int`/`float` parameters with
   defaults; keep class and `rel_type` literals in the query.
3. Replace unbounded paths with bounded ones, add `ORDER BY`, ensure `$domains` scopes
   every matched node, and alias the returned columns.
4. In `provenance` set `origin: promoted` and `source_question` to the user's original
   question verbatim (a promoted view without a non-empty `source_question` is rejected).
5. Then Flow A from step 3's SKILL.md drafting onward: validate, test (with the original
   entity as the parameter, expecting the same answer as before), show, approve, save.

## Flow C: edit an existing view

1. `artmind query views show <name> --compact` gives the full `view` and `skill_md`;
   write them to `<tmp>/<name>/view.yaml` and `SKILL.md`.
2. Modify. **Bump `version`** whenever the cypher, params or presentation change: `save`
   refuses a changed query at the same version, and refuses any version lower than the
   saved one (there is no force flag). Edits to `SKILL.md` or to summary/examples alone
   need no bump.
3. Validate, test, show, approve, save.

To remove a view: `artmind views delete <name>` -- only after the operator has said yes.

## Failure modes

- `needs_disambiguation` on test: expected for ambiguous names; rerun with `@<_id>`.
- `artmind query views list` shows broken views as `invalid` entries; `artmind views validate <name>` gives the reason.
- Query fails with a Neo4j error: the message names the view and version; fix the Cypher
  and re-test. Do not save a view that has not produced a sensible result.
- `save` refuses with a version message: bump `version` above the saved one.
