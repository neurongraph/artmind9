---
name: artmind-curate
description: Curation for artmind domains across both stores — same-as identity review (merging duplicate entities, linking cross-domain/cross-class identity), conflict adjudication, description synthesis, and review of the structured store's machine-proposed table classifications (grain, bridge columns, column→entity_class mappings). Use for "curate domain X", "clean up duplicates", "find conflicts between domain A and B", "review the same-as queue", "review/confirm the proposed mappings", or "are these table classifications right".
---

# artmind Curate

Use this skill for everything that maintains artmind's knowledge after
ingestion — the graph (Workflows A–C) and the structured store's table
classifications (Workflow D). Two proposers feed one review queue; you supply
the judgment at the review gates.

**Same-as groups are declarative and reversible.** A group in `same_as.yaml`
is a human's bounded assertion that these aggregate keys denote one thing;
removing it and rebuilding un-merges just as cleanly as adding it merged.
There is no destructive graph surgery anywhere in this workflow — review
generously, and don't hesitate to approve a group you're only fairly
confident about, since walking it back later costs one edit and one rebuild.
Structured-table classifications (Workflow D) are lighter still: a registry
row, cleared or overwritten with no graph write involved.

## Required Inputs

- `domain` (one or more): ask if not provided. Pass every sibling domain the
  user wants compared to one `sameas propose`/`ingest detect-conflicts` call
  to get the cross-domain pass.

## Workflow A — Review the same-as queue: Propose → Review → Approve → Verify

### 1. Propose

Two independent proposers feed the same queue — run either or both:

```bash
# cross-domain / cross-class candidates via the embedding adjudicator
artmind sameas propose --domain <d1> [--domain <d2>] --compact

# intra-domain naming-variant candidates via name-similarity clustering
artmind ingest refine-graph --domain <d> --dry-run --output merges.json
artmind ingest refine-graph --domain <d> --from-file merges.json
```

`sameas propose` reuses the cross-domain adjudicator's candidate pairing and
adjudication prompt: one judgment, two outcomes — a "same thing" verdict
writes a same-as proposal here; a "conflicting claims" verdict writes a
`:Conflict` instead (`query graph conflicts` / `ingest resolve-conflict`).
Pass one `--domain` for naming-variant merge candidates within it, 2+ for
cross-domain identity candidates. `--maxPairs` bounds LLM cost (~20s per
candidate on a local model; `--maxPairs 20` for a first pass).

`ingest refine-graph` is the other proposer — intra-class name-similarity
clustering rather than semantic neighbours, cheaper and complementary. Its
`--from-file` step writes proposals into the same queue; it does not touch
the graph.

### 2. Review the queue

```bash
artmind sameas list --status open --compact
```

Each proposal names a `canonical` and its `members`, with `source`
(`adjudicator` or `refine_graph`) and `reason`. Judge:

- **Is this genuinely one real-world thing?** Read the proposal's evidence;
  for anything non-obvious, pull a source chunk:
  ```bash
  artmind query vector-text --domain <d> --topK 3 --compact "<questionable member>"
  ```
- **Watch for negations and version numbers** — "Policy v2" and "Policy v3"
  may be the same document at two points in time (a `Series`, or simple
  document supersession — see Workflow C), not one identity.
- **A large cluster (many members) means extraction over-fragmented one
  concept**, not that the merge is automatically right — spot-check names
  that could plausibly denote something distinct.
- **Cross-domain/cross-class candidates need real scrutiny**: a group
  spanning classes or domains links (`SAME_AS`) rather than merges into one
  Entity — correct for "this policy entity and this SOP entity are about the
  same real thing," wrong if the shared name is coincidental.

Reject anything that doesn't hold up:

```bash
artmind sameas reject <proposal_id> --reason "<why>"
```

### 3. Approve

```bash
artmind sameas approve <proposal_id> [--canonical <key>]
```

Appends the group to `same_as.yaml` and runs a full rebuild scoped to the
domain families the group touches. `--canonical` overrides which member wins
(default: the proposer's own suggestion) — override it when the "obviously
right" display name isn't the one the proposer picked.

**This is a two-step workflow, not one command.** `sameas approve` does NOT
clear `projection status`'s drift flag — its rebuild is scoped to the
group's own domain families, and `same_as.yaml`'s hash (what drift tracks)
is a global signal a partial rebuild can't honestly clear. After approving
everything in a review session, run:

```bash
artmind projection status --compact      # confirm same_as_drift: true
artmind projection rebuild --compact     # clears it
```

Skipping the second command leaves `projection status` reporting drift
indefinitely even though every approved group is already live in the graph —
harmless to queries (the projection is already correct), but confusing to a
later operator who reads the drift flag as "something is stale."

### 4. Verify

```bash
artmind query entity-resolve --domain <d> --topK 3 --compact "<a merged alias>"   # resolves to canonical
artmind query graph conflicts --domain <d1> --domain <d2> --compact               # materialized conflicts
artmind query entity-context --domain <d> --entityId <id> --compact              # clean description + source docs
```

When reporting materialized conflicts, **group by root cause, not a flat
list** — cluster by shared `aspect` wording / shared entity sets, call out
severity, and cite both documents' provenance per finding.

## Workflow B — Un-merge a bad group

Same-as groups are the reversible mechanism they're described as above — this
is not a forensic recovery, it's the ordinary undo path:

1. Open `same_as.yaml` (in the run folder) and remove the offending group, or
   remove just the wrong member from it.
2. `artmind projection rebuild --domain <d> --compact` (or a bare
   `artmind projection rebuild --compact` to also clear drift).
3. The un-merged entity returns under its original deterministic id —
   confirm with `entity-resolve` on its name.

## Workflow C — Investigate a surprising conflict or same-as decision

**Conflict**: pull its evidence and read both sides:

```bash
artmind query graph conflicts --domain <d1> --domain <d2> --entityName "<name>" --compact
```

Check `status` and whether either document has since been superseded
(`valid_to` set) — if so, run `docs retire` on the superseded document rather
than treating the two as a live disagreement (superseded content drops out of
every index once retired, so the comparison resolves itself on the next
query).

**Same-as decision**: pull the entity's properties and check the aliases
that fed it:

```bash
artmind query graph pattern2 --domain <d> --entityNameList "<canonical>" --compact
```

Judge: reasonable (same real-world thing — leave it), over-merged (Workflow B),
or needs a same-as **link** instead of a merge (same real-world thing referred
to across domains/classes, but each side's own facts should stay on its own
entity — reject the merge proposal and hand-propose it via `sameas propose`
scoped to just those domains, so the adjudicator can pick "link" over
"merge").

## Workflow D — Review structured-table classifications (grain / bridge / mappings)

The structured store's counterpart to Workflow A. Ingest classifies each
registered table in three steps — `grain` (do these rows record facts or
assert rules), `bridge` (whose *values* are worth searching the graph for),
and `mapping` (which columns denote instances of which `entity_class`) — and
every result lands **unconfirmed**, awaiting exactly this review.

Use `artmind-ingestion-helper` instead when a step's *status* is `failed` or
`pending` (that's a re-run problem: `db propose`). Come here when the steps
ran fine and the question is whether the answers are *right*.

### 1. Read the proposals

```bash
artmind db mappings <table> --domain <d> --compact   # column → entity_class, confirmed flag
artmind db grain <table> --domain <d> --compact      # grain + bridge columns
```

**Start here for a whole domain** — one call lists every table with anything
still unadjudicated, and a fully-reviewed table drops out entirely, so an
empty `tables` means the domain is done:

```bash
artmind db review --domain <d> --compact
```

Per table it returns the unconfirmed `mappings`, the unconfirmed
`bridge_columns`, `grain`/`grain_confirmed`, and the three step statuses —
everything the gates below need, without walking tables one at a time. Use
`pending_count` to tell the user the size of the job before starting.

### 2. Judge — what to look for

**Judge sampled values against the class description, never the column
name.** The proposer weighed exactly two things — the column's sampled values
and the schema's class descriptions — so re-examine both, and read both
through the CLI rather than hunting for files on disk:

```bash
artmind db schema <table> --domain <d> --compact   # each column's profile + distinct_sample
artmind domains entities-prompt <d>                # the class descriptions the proposer read
```

`domains entities-prompt` prints the assembled entity-type prompt the
proposer read (the schema's `entity_types` map rendered through
`meta.yaml`'s templates). Do **not** go looking for `domains/schemas/*.yaml`
— the schema lives in the run folder, not the working directory, and the CLI
is the supported way to read it. The same rule holds throughout this skill:
reach for an `artmind` command before the filesystem.

- **High confidence is not correctness.** A confident proposal can be
  confidently wrong — an `agents.name` column scoring 0.9 for `CUSTOMER` when
  agents are staff, alongside `department → ORGANIZATIONAL_UNIT` at 1.0 which
  is right. Read every proposal above the floor; do not skim by score.
- **Identifier columns are the ones worth keeping.** `customer_id →
  CUSTOMER` on opaque keys (`CUST-0019`) is the mechanism working as intended:
  it is a judgment about what the column *denotes*, which no string match
  could reach. Don't reject it for "the values don't look like customers".
- **Multiple classes per column are legitimate**, not a bug to resolve — a
  `category` column can denote both a `PRODUCT` and an `ISSUE_TYPE`. Only
  reject the ones that are actually wrong.
- **Booleans, dates, and measures should map to nothing.** A proposal on
  `resolved_first_contact` or a `*_gbp` amount is noise; clear it.
- **Grain**: only `normative` changes behaviour (it quarantines the table from
  answer synthesis, because a document also asserts that content). If torn
  between `instance` and `normative`, the proposer is instructed to choose
  `normative` to force this review — so a surprising `normative` is the
  system working, not a mistake. Confirming it requires
  `refresh_mode=temporal`, enforced in the registry, not just the CLI.

### 3. Apply the decisions

```bash
# accept one pair
artmind db mappings <table> --domain <d> confirm --column <c> --entityClass <CLASS>
# accept every proposal on the table (only when you have read them all)
artmind db mappings <table> --domain <d> --acceptProposed
# reject
artmind db mappings <table> --domain <d> clear --column <c>
# add one the model missed
artmind db mappings <table> --domain <d> set --column <c> --entityClass <CLASS>
# confirm grain
artmind db grain <table> --domain <d> --set instance|lookup|normative
# bridge columns (see them with `db grain <table>`, which lists them)
artmind db bridge confirm --table <table> --domain <d> --column <c>
artmind db bridge clear   --table <table> --domain <d> --column <c>
```

Three traps worth stating to the user before they act:

- **`clear --column c` drops every class for that column**, not just the bad
  one. Where a column has two proposals and one is right, `confirm` the good
  one first — a re-propose can never overwrite a confirmed pair.
- **`clear` is not a durable rejection.** The next `db propose ... --redo` may
  propose it again. Confirming the *correct* mapping is what makes a decision
  stick; clearing alone only defers it.
- **Confirming is a trust signal, not a switch.** Unconfirmed mappings are
  already projected into the catalogue carrying `confirmed: false` and are
  already usable for routing — deliberately, so a fresh table isn't invisible
  until reviewed. Confirming does not "turn a mapping on".

### 4. Verify

```bash
artmind db bridge --domain <d> --compact   # the routing surface the query side actually reads
artmind db catalogue --domain <d>          # push confirmations into Neo4j (no re-ingest)
```

`db catalogue` matters: confirming a mapping updates the registry only. The
graph's catalogue subgraph is refreshed by ingest hooks, so a later
confirmation needs this explicit re-projection to reach Neo4j.

### What confirming a bridge column currently buys

Nothing reads `column_roles.confirmed` yet — no caller passes
`confirmed_only=True`, and the fusion path uses every bridge column
regardless. So confirming one records a human judgment for the next reviewer
rather than changing retrieval. Worth saying plainly if a user asks why it
made no difference; it is not a reason to skip the review, since the flag is
what stops the same column being re-litigated on every pass.

Track it down to zero: re-run `db review --domain <d>` after applying, and
stop when `pending_count` reaches 0.

## projection {rebuild, status, synthesize}

Three commands underlie everything above, mostly invisible in ordinary use —
a rebuild runs automatically inside whatever operation dirtied the
projection (a document commit, `docs retire`, `sameas approve`). Reach for
them directly only for the cases with no natural host:

```bash
artmind projection status --compact             # drift: same_as.yaml / schema-set hash vs. last full rebuild
artmind projection rebuild [--domain <d>] --compact   # recompute Entities from observations; no LLM
artmind projection synthesize --domain <d> [--nameFilter <f>] [--force] --compact
```

`projection synthesize` rewrites an entity's description as one coherent
passage drawn from all its observations — the only step in this whole skill
that spends language-model budget without being explicitly asked to, so it's
always a deliberate, separate call. Run it after a bulk ingest
(`ingest sync <dir>` → `projection synthesize`) or after approving a same-as
group that meaningfully changed an entity's observation set. It skips an
entity with an open (projection) conflict, one below `--minObservations`, or
one whose observation set hasn't changed since the last synthesis (unless
`--force`).

## Fallbacks

- Same-as proposals empty but duplicates visibly exist → lower
  `sameas propose --simThreshold` (e.g. 0.6) or `ingest refine-graph
  --threshold`, and re-propose.
- `sameas propose`/`ingest detect-conflicts` computed BEFORE any merges apply
  in this session — if you've just approved a batch of merges, re-propose
  afterward so the next pass compares the post-merge entity set, not the
  pre-merge one.
- Consolidation (`projection synthesize`) `failed_embedding` counts high →
  check the embedding service or pass `--model`; re-running is safe
  (idempotent — an unchanged observation set is skipped, not re-billed).
- Interrupted `sameas approve` or `projection rebuild` → re-run the same
  command; both are safe to re-apply.

## Closing a conflict

Detection never closes conflicts — two authorities disagreeing is a human
judgment. Once you have adjudicated one:

```bash
artmind ingest resolve-conflict <conflict_id> --status resolved --reason "<why>"
```

Use `--status dismissed` for a false positive. `query graph conflicts --status all`
shows closed ones afterwards. This applies to adjudicator-produced conflicts
(`_source: 'adjudicator'`, the ones `query graph conflicts` surfaces);
projection-produced conflicts (`_source: 'projection'`, a single entity's own
property disputed within one instant — see artmind-query's Adjudicate step)
resolve themselves on the next rebuild once the underlying observations stop
disagreeing, and have no separate close step.

## When NOT to run

Same-as review (Workflows A–C):

- Mid-ingestion (worker jobs still processing the domain) — curate afterwards.
- Never approve a same-as proposal you haven't actually read the evidence
  for, even though approving is cheap to undo — an unread approval still
  costs the next reviewer's time to notice and reverse it.

Structured classification review (Workflow D):

- When any of `grain_status`/`bridge_status`/`mapping_status` is `pending` or
  `failed` — there is nothing to adjudicate yet. Send the user to
  `artmind-ingestion-helper` to get the step running, then review.
- A re-ingest is *not* a reason to defer: `register_table` leaves confirmed
  grain and mappings alone on update, so review work survives a refresh.
