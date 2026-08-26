---
name: artmind-refine
description: Maintenance and curation for artmind domains across both stores — the graph refinement pipeline (temporal normalization, supersession, similar-entity merging, conflict detection including cross-domain, description consolidation) and review of the structured store's machine-proposed table classifications (grain, bridge columns, column→entity_class mappings), plus targeted workflows and forensics. Use for "refine domain X", "clean up duplicates", "find conflicts between domain A and B", "why did these get merged", "is this a real disagreement or just an older document", "review/confirm the proposed mappings", or "are these table classifications right".
---

# artmind Refine

Use this skill for everything that maintains artmind's knowledge after
ingestion — the graph (Workflows A–D) and the structured store's table
classifications (Workflow E). The CLI guarantees step *order* and does the
proposing; this skill supplies the *judgment* at the review gates and the
forensic workflows around them.
Background: `docs/refine-merge-conflict-supersede-guide.md`.

Both stores share one shape — a machine proposes, a human adjudicates, then
it's applied — but they differ in reversibility, and that governs how hard to
push back before approving. A graph merge deletes alias nodes with no
un-merge (Workflow A, Gate 1). A structured classification is only ever a
registry row: wrong ones are cleared or overwritten, and nothing is
destroyed. So review mappings briskly; review merges slowly.

## Why order matters (encoded in the CLI — do not run steps manually out of order)

`time → supersession → merge → conflicts → consolidate → embed`

- Temporal properties must exist before anything reasons about currency.
- Supersession stamps `valid_to` before conflict detection, so superseded
  claims read as history, not live disagreements.
- Merges precede conflicts (claims about one real-world entity must meet on
  one node) and consolidation (don't pay LLM calls on soon-merged entities).
- Conflicts precede consolidation, so its skip-open-conflict gate works.
- One embedding sweep at the end covers both merges and rewrites.

With 2+ `--domain` flags the pipeline adds a **cross-domain conflicts pass**
after every domain's own steps — merges land first by construction, which is
exactly the precondition cross-domain detection needs.

## Safety & Cost Rules

- **Never apply merges without a reviewed propose run.** Applying deletes
  alias entity nodes and there is no built-in un-merge. Conflicts materialize
  additively (MERGE-only) and consolidation preserves `description_raw`, so
  those two are recoverable; merges are not.
- **State cost BEFORE proposing.** The propose phase does the real LLM work:
  merge adjudication per cluster and conflict adjudication per candidate
  (observed ~20s per conflict candidate on a local model; `--maxPairs 200`
  can mean over an hour, and it applies PER detection pass — n domains means
  n intra passes + 1 cross pass). Suggest `--maxPairs 20` for a first pass.
- **Don't trust alias-cluster size as merge quality.** A big cluster means
  extraction over-fragmented one concept, not that the merge is right —
  spot-check clusters above ~10 aliases against source chunks (see Gate 1).
- **Cross-domain merges are guarded by design.** refine-graph skips
  same-named entities across domains by default — that keeps them available
  for conflict detection to evaluate. Don't reach for
  `--allow-cross-domain-merge` because a cluster "looks the same"; it removes
  the very pairs conflict detection needs.

## Required Inputs

- `domain` (one or more): ask if not provided. Pass every sibling domain the
  user wants compared in ONE pipeline run to get the cross-domain pass.

## Workflow A — Refine domain(s): Propose → Review → Apply → Verify

### 1. Propose

```bash
artmind ingest refine-pipeline --domain <d1> [--domain <d2>] --compact
```

Deterministic steps (time, supersession) run for real — additive and
idempotent. LLM steps produce proposals only. The output names a
`report_file`, per-domain sub-proposal files (`merges_<d>.json`,
`conflicts_<d>.json`, `conflicts_cross.json` for 2+ domains), and an
`apply_with` command. Knobs: `--maxPairs`, `--sampleConsolidations`,
`--mergeThreshold`, `--simThreshold`.

### 2. Review — the three gates

Start with a structured summary instead of hand-parsing the (large)
`pipeline_report.json`:

```bash
python3 skills/artmind-refine/scripts/summarize_gates.py <report_file>
```

Prints, per domain and for the cross-domain pass: Gate 1's proposed merges
grouped by canonical (flags clusters >5 aliases to spot-check), Gate 2's
conflict candidates grouped by verdict (only non-`superseded`/`no_conflict`
verdicts are live conflicts worth reading), and Gate 3's consolidation
samples with old/new description diffs. Re-run it against the apply-phase
report after applying to confirm what actually landed (`merged`/`skipped`/
`errors` counts, `written`/`examined`). Missing keys print a loud
`‹missing key›` marker rather than silently defaulting — if you see one, the
CLI's report schema has likely changed and the script needs a look, not the
report.

Present each gate compactly; get explicit approval before apply.

**Gate 1: merges** (`per_domain.<d>.merge.proposed_merges`): cross-class pairs
(a thing proposed into its own FEE, etc.) are now structurally prevented —
clustering groups by `entity_class` before string-similarity, and
`apply_merges` skips any cross-class pair even if it sneaks in via a
hand-edited `--from-file`. What's still worth flagging by eye: negations, and
differing numbers or versions ("Policy v2"/"Policy v3" may be *supersession*,
not duplication). For clusters above ~10 aliases or any alias that could
denote a distinct concept, pull a source chunk and judge:

```bash
artmind query vector-text --domain <d> --topK 3 --compact "<questionable alias>"
```

Remove bad pairs by editing `merges_<d>.json`. When in doubt, drop the pair.

**Gate 2: conflicts** (`per_domain.<d>.conflicts` and
`cross_domain_conflicts`): check each proposal's evidence actually disagrees
and that neither side's document is superseded (that is history — remove it).
Edit the `conflicts_*.json` files to drop noise.

**Gate 3: consolidation samples** (`per_domain.<d>.consolidate.rows`): the
`new_description` must not invent facts, must keep disagreeing values side by
side, and must mark superseded facts historical. If samples look bad, adjust
the model or drop the step; per-entity vetting is unnecessary (idempotent,
conflict-gated, original kept in `description_raw`). Quote
`candidates_total` to the user as the apply-phase LLM cost.

### 3. Apply

```bash
artmind ingest refine-pipeline --domain <d1> [--domain <d2>] --from-file <report_file> --compact
```

Re-runs time/supersession, applies the (edited) merge and conflict proposals,
runs consolidation live (`--consolidateLimit N` to batch), then nulls
merged-canonical embeddings and backfills. `--apply` without `--from-file` is
one-shot compute-and-apply — only for domains the user explicitly says need
no review.

### 4. Verify

```bash
artmind query entity-resolve --domain <d> --topK 3 --compact "<a merged alias>"   # resolves to canonical
artmind query graph conflicts --domain <d1> --domain <d2> --compact               # materialized conflicts
artmind query entity-context --domain <d> --entityId <id> --compact              # clean description + source docs
```

When reporting materialized conflicts, **group by root cause, not a flat
list** — cluster by shared `aspect` wording / shared entity sets (a real case
collapsed 14 pairwise Conflict nodes into one root disagreement), call out
severity, and cite both documents' provenance per finding.

## Workflow B — Focused merge of specific entities

Spotted duplicates mid-session? Scope detection to just those names:

```bash
artmind ingest refine-graph --domain <d> --filter "<name1>,<name2>" --dry-run --output merges.json
# review, then:
artmind ingest refine-graph --from-file merges.json
```

## Workflow C — Investigate a surprising merge or conflict (forensics)

**Merge**: pull the entity's properties and spot-check the odd alias:

```bash
artmind query graph pattern2 --domain <d> --entityNameList "<canonical>" --compact
artmind query vector-text --domain <d> --topK 5 --compact "<the alias that looks off>"
```

Judge: reasonable (same real-world thing) / over-merged (recommend a split —
there is no built-in un-merge, so record the recommendation, don't attempt a
fix) / needs human review.

**Conflict**: pull its evidence and read both sides:

```bash
artmind query graph conflicts --domain <d1> --domain <d2> --entityName "<name>" --compact
```

Check `status` and whether either document has since been superseded
(`valid_to` set) — if so, the conflict may be resolvable by re-running
detection, not by manual edit.

## Workflow D — "Real conflict or just an older document?"

1. Check for `valid_to` / `SUPERSEDES` already:
   `artmind query graph timeline --domain <d> --entityId <id> --compact`
2. Same document lineage (same title, sequential versions) but unmarked →
   look for a Supersession Notice; apply `ingest detect-supersession
   --domain <d>` or the manual
   `ingest supersede --domain <d> --newer "<n>" --older "<o>" --effective <date>`.
3. Once supersession is applied, answer present-tense questions with
   `--asOf today` and historical ones without it (see artmind-query's
   Adjudicate step).
4. Genuinely different authorities describing the same thing differently →
   live conflict: run Workflow A's conflicts step across those domains.

## Workflow E — Review structured-table classifications (grain / bridge / mappings)

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

`domains entities-prompt` prints the same `ENTITY TYPES YOU MUST EXTRACT`
block that `propose_mapping` parses, so what you read is literally what the
model was given. Do **not** go looking for `domains/schemas/*.yaml` — the
schema lives in the run folder, not the working directory, and the CLI is the
supported way to read it. The same rule holds throughout this skill: reach for
an `artmind` command before the filesystem. The only files worth opening
directly are the pipeline's own proposal artifacts (`merges_<d>.json`,
`conflicts_*.json`), because editing them *is* the review mechanism.

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

## Fallbacks

- Merge proposals empty but duplicates visibly exist → lower
  `--mergeThreshold` (e.g. 0.6) and re-propose.
- Propose-mode conflict proposals are computed BEFORE merges apply (a
  never-refined domain also warns `missing_refine`). After applying
  substantial merges, re-detect: `--steps conflicts --apply` (safe —
  conflicts materialize additively).
- Consolidation `failed_llm` counts high → check the LLM service or pass
  `--model`; re-running is safe (idempotent).
- Interrupted apply → re-run the same `--from-file` command; every step
  tolerates re-application.

## Known Caveats (tell the user when relevant)

- Re-running detect-conflicts is not a guaranteed no-op — `Conflict.id`
  hashes the aspect text, so re-phrased LLM output for the same dispute
  creates a new node rather than updating the old one.
- `EVIDENCE` edges accumulate and are never pruned as documents are ingested.
- `--allow-cross-domain-merge` only affects clustering, not `--from-file`.
- `ingest supersede` sets `valid_to`/`superseded_by` — not deletion, but not
  reversible via CLI.

## Closing a conflict

Detection never closes conflicts — two authorities disagreeing is a human
judgment. Once you have adjudicated one:

```bash
artmind ingest resolve-conflict <conflict_id> --status resolved --reason "<why>"
```

Use `--status dismissed` for a false positive. `query graph conflicts --status all`
shows closed ones afterwards.

## Reading superseded entity values

`entity-versions` and the snapshot-on-supersede mechanism it read are gone —
they belonged to the pre-observation entity model (an accretive property
merge that had to snapshot a value before overwriting it). Under the
observation/projection model nothing is ever overwritten: every value a
document asserted stays on its own immutable `:Observation`, whether or not
a later document disagrees or a document is later retired.

The equivalent read is `query entity-history`, which is fact-level (it
orders by `_valid_from`/`_valid_to`, not by document valid_from) and spans
both current and retired (`docs retire`) observations:

```bash
artmind query entity-history --domain <d> --entityId <id> --compact
artmind query entity-history --domain <d> --entityId <id> --property rate_value --compact
```

## When NOT to run

Graph pipeline (Workflows A–D):

- Mid-ingestion (worker jobs still processing the domain) — refine afterwards.
- On a domain about to be re-ingested from scratch.
- Never one-shot `--apply` on a domain with unreviewed merge proposals.

Structured classification review (Workflow E):

- When any of `grain_status`/`bridge_status`/`mapping_status` is `pending` or
  `failed` — there is nothing to adjudicate yet. Send the user to
  `artmind-ingestion-helper` to get the step running, then review.
- A re-ingest is *not* a reason to defer: `register_table` leaves confirmed
  grain and mappings alone on update, so review work survives a refresh.
