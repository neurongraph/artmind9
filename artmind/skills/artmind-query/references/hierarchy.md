# Hierarchy — transitive walks (reporting lines, org subtrees, chains of command)

Read this for "everyone under X", "X's whole reporting line", "full org subtree",
or "chain of command" — any question that needs more than one hop and the walk
could fan out or cycle. `pattern4` is one hop only; `hierarchy` is the transitive
version.

## Discovery → edge-spec recipe

You almost never know the right `rel_type`s up front, so start in discovery mode —
omit both `--childOf` and `--parentOf`:

```bash
artmind query graph hierarchy --domain <d> --entityId <id> --compact
```

This returns `edge_candidates`: every `rel_type` one hop from the root, its
`direction` (`in` or `out`), how many edges, and the neighbour classes. Turn a
candidate into an edge spec with one rule:

- an `in` edge means the neighbour points AT the root — for `REPORTS_TO` that
  makes the neighbour a child, so pass `--childOf REPORTS_TO`.
- an `out` edge means the root points AT the neighbour — for `MANAGES` that
  makes the neighbour a child too (the root is the parent), so pass
  `--parentOf MANAGES`.

Don't guess the direction from the rel-type name alone — a schema can orient
`contains`/`part_of` either way depending on which side the extraction prompt
asked the model to name first. Trust `edge_candidates`' `direction`, not intuition.

## Worked example — banking org chart

Question: "list everyone under Priya Nair, the Head of Retail Banking."

1. Resolve the root: `artmind query entity-resolve --domain banking_org --topK 5
   --compact "Priya Nair"` → one `ROLE_PERSON` entity, id `e42`.
2. Discover the edge vocabulary: `artmind query graph hierarchy --domain
   banking_org --entityId e42 --compact` → `edge_candidates` shows `REPORTS_TO`
   (`direction: in`, 6 edges, neighbour class `ROLE_PERSON`) and `MEMBER_OF`
   (`direction: in`, 6 edges, neighbour class `ORGANIZATIONAL_UNIT`).
3. Walk down: `artmind query graph hierarchy --domain banking_org --entityId e42
   --childOf REPORTS_TO --childOf MEMBER_OF --nodeClass ROLE_PERSON --nodeClass
   ORGANIZATIONAL_UNIT --compact`.
4. Report `rows` grouped by `depth`, noting any `summary.multi_parent` entries
   (someone with two reporting lines) and `summary.truncated`.

For "who does Priya ultimately report to" (the chain of command upward), same
edge spec, `--direction up`.

## Reading `truncated` / `back_edges` / `multi_parent`

- **`summary.truncated` / `summary.truncated_reason`** — `"limit"` means the
  `--limit` node budget ran out before the walk finished; `"max_depth"` means
  there was more beyond `--maxDepth`. Either way, **always state the truncation
  in your answer** ("...showing the first 1500 of what may be more — raise
  `--limit` or narrow `--nodeClass` to see further") — never claim "this is
  everyone under X" when `truncated` is `true`. When `false`, the walk reached
  every reachable node within `--maxDepth`, so completeness claims are safe.
- **`back_edges`** — a cycle or a shortcut edge back into an already-visited
  node at a *different* depth; it is reported, not followed (so the walk stays
  linear in work, not exponential). A non-empty `summary.back_edges` on a
  reporting-line question is itself worth surfacing ("A reports to B, but a
  REPORTS_TO edge also runs B → A — extraction noise or a genuine dual role,
  worth flagging") — don't silently drop it.
- **`multi_parent`** — a node reached from more than one parent at the same
  depth (matrix reporting, or an org unit two teams both roll into). Each such
  row's `parents` list has more than one entry; read all of them rather than
  picking one arbitrarily when describing that node's position.

## Property fallback

A hierarchy can only be walked if it was extracted as an **edge**. Some schemas
(e.g. `banking.organization_schema.yaml`) also declare `reports_to` as a
**property** on `ROLE_PERSON`, independent of whether a `REPORTS_TO` edge was
also extracted. If `hierarchy` comes back with an empty `rows`/`edge_candidates`
for a root you know has reports, the relationship likely only exists as that
property — fall back to:

```bash
artmind query graph pattern1 --domain <d> --entityClass ROLE_PERSON --compact
```

and read each entity's `reports_to` property directly. This only gives you one
level at a time (you'd re-run `pattern1` and manually chase the chain), so say
so rather than presenting it as equivalent to a `hierarchy` walk.
