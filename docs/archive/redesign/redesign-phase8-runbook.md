# Phase 8 — cutover runbook

The final phase. **Not mostly testing**: roughly half of it is real work —
vault preparation, a full re-ingest, and a curation pass that needs human
judgment. Measurement comes last.

Plan: [redesign-phase-plan.md](./redesign-phase-plan.md) · baseline:
[redesign-quality-scorecard.md](./redesign-quality-scorecard.md)

---

## Pre-flight: four decisions before anything is destroyed

### P1. Three vault files already carry `_artmind_id` — and that is a trap

`reference/interest_rate_schedule_2026{,_02,_03}.md` were ingested by Phase 3's
live gate, so they carry `_artmind_id` **and `_content_sha256`**.

Against a wiped graph, `_content_sha256` still matches the file body — so
`delta` classifies them **`metadata_only`**, skips extraction, and writes **no
observations**. The deferred rebuild then finds their keys with zero latest
observations and correctly deletes whatever they fed.

This is exactly the failure Phase 3's notes record as bug 5, and its gate script
already had the fix: **reset `_content_sha256` on any vault file that carries
one before re-ingesting.** Keep `_artmind_id` — the adopt row of the resolution
table is designed for precisely this, and it means those three documents keep
their identity across the cutover.

```bash
grep -rl "_content_sha256:" ~/Projects/artmind-corpus/ | xargs sed -i '' '/^_content_sha256:/d'
```

Then verify only that key is gone and `_artmind_id` survives.

### P2. Three documents in the vault are *about* the corpus, not *in* it

`index.md`, `schema_mapping.md`, `structured/README.md`. `collect_ingest_files`
skips only dotfiles, so a directory ingest will extract all three as documents —
and artmind will write `_artmind_id` into them.

Decide one of:

- **exclude** — move them to a `_meta/` folder you never point an ingest at
  (mirrors what was done for `benchmarking/questions.md`);
- **ingest deliberately** — `index.md` is arguably legitimate corpus content;
  `schema_mapping.md` is operator documentation and almost certainly is not.

Whatever you choose, choose it explicitly — the failure mode is silent.

### P3. The current graph is the Phase 0 baseline plus test debris

| Label | Count |
|---|---|
| Document | 67 |
| DocChunk | 1,546 |
| **Observation** | **88** |
| Entity | 5,583 |
| Conflict | 11 |
| Synthesis | 0 |

No `Document._domain` is set anywhere — every one of the 67 is pre-Phase-4 and
already unreachable through the new `_domain`-scoped query layer. The 88
observations are residue from Phases 3–6's live gates.

**Snapshot before wiping.** This graph holds the pre-redesign corpus the
scorecard baseline was measured against, and it is the only remaining copy of
that state in queryable form.

### P4. Folder → domain mapping is clean, so no `_domain` seeding is needed

`schema_mapping.md` gives an exact folder→domain mapping, and every folder maps
to exactly one domain:

| Domain | Folders | .md |
|---|---|---|
| `banking.organization` | `organization/` | 6 |
| `banking.products` | `products/`, `faqs/` | 4 |
| `banking.sop_guides` | `sop_procedures/`, `guides/` | 12 |
| `banking.policy` | `policies/` | 10 |
| `banking.risk_governance` | `risk_compliance/`, `governance/`, `regulations/` | 12 |
| `banking.reference` | `reference/` | 8 |
| `banking.communications` | `templates/`, `training/` | 7 |
| `banking.cases` | `cases/` | 4 |
| `banking` (structured) | `structured/*.csv` | 5 csv |

So ingest **folder by folder with `--domain`** and let the first ingest seed
`_domain` into frontmatter. There is no need for a separate seeding pass.

---

## Step 1 — Snapshot and freeze

```bash
just dev-stop-daemons && just dev-install && artmind init
```

```bash
artmind snapshot create
```

Record the filename. This is your rollback point for everything below.

## Step 2 — Wipe the graph

Batched, because a single `DETACH DELETE` over ~7k nodes on AuraDB will strain
the transaction.

```bash
uv run python -c "
from artmind.graph_query import neo4j_session
with neo4j_session() as s:
    s.run('MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 500 ROWS')
    print({r['l']: r['c'] for r in s.run('MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c')})
"
```

Expect `{}`.

## Step 3 — Apply the new schema

```bash
artmind setup
```

Every constraint and index from Phases 3–6 — the `:Observation` zone, the three
History-label mirrors, `Entity.key`, `relates_to_type`, `_id`/`_domain` — lands
on an empty graph with nothing to conflict with.

## Step 4 — Re-ingest, one domain at a time

**Not one big run.** Per-domain runs give you a checkpoint after each, and a
failure costs one domain rather than everything. Directory ingest defers the
projection to a single rebuild at the end of each run, which is what you want.

Start with the smallest to shake out problems cheaply:

```bash
artmind ingest sync ~/Projects/artmind-corpus/cases --domain banking.cases
```

Then, in ascending size — `products`+`faqs`, `organization`, `templates`+`training`,
`reference`, `policies`, `risk_compliance`+`governance`+`regulations`,
`sop_procedures`+`guides`.

**After each domain, before starting the next:**

```bash
artmind docs list --domain <domain> --compact
```

⚠️ **If any document failed to commit, STOP.** Phase 3's bug 5 is the reason: a
deferred rebuild running after a failed commit is *guaranteed* destructive — it
finds keys with zero latest observations and deletes them. Fix the failure and
re-run that domain before continuing.

Then the structured store:

```bash
artmind ingest sync ~/Projects/artmind-corpus/structured --domain banking
```

## Step 5 — Rebuild, embed, synthesize

```bash
artmind projection rebuild
```

```bash
artmind projection status
```

Then the one step that spends model budget without being asked:

```bash
artmind projection synthesize
```

## Step 6 — Curation (this is work, not testing)

The projection is deterministic; identity judgments are not. Baseline measured
**693 near-duplicate name pairs**, of which normalization handles ~119
mechanically. The rest are proposals a human decides.

```bash
artmind sameas propose --domain banking --compact
```

```bash
artmind sameas list --domain banking --compact
```

Review, then approve or reject. Remember Phase 6's open question 1: **`sameas
approve` runs a domain-scoped rebuild and does not clear the global drift flag**,
so finish with a bare rebuild:

```bash
artmind projection rebuild
```

The proposer emits **two** outcomes: same-as groups *and* conflict proposals. A
candidate pair is either one thing or two things making incompatible claims —
both land in the same queue.

## Step 7 — Measure

### Scorecard

Run the script in [redesign-quality-scorecard.md](./redesign-quality-scorecard.md).

| # | Measure | Baseline | Target |
|---|---|---|---|
| 1 | near-duplicate name pairs | 693 | < 100 |
| 2 | accreted `" \| "` descriptions | 512 | **0** |
| 3 | unretired orphans | 235 | **0** |
| 4 | class-name-typed edges | 1,581 | **0** |
| 5 | distinct rel types | 249 | **1** |
| 6 | stale indexed chunks | 124 | **0** |
| 7 | string-versioned documents | 63 | **0** |
| 9 | extracted-status collisions | 115 | **0** |
| 10 | edges with `doc_ids` | 8 / 7,482 | **100%** |
| 12 | **control** — property keys / dupes | 439 / 1 | must not regress |

Rows 2, 3, 5 and 6 should be zero **by construction**. If any isn't, that's a
defect, not an incomplete migration.

The script's row-4 check derives class names from live entities — confirm it
still reads correctly against the new `_domain`/`_id` properties before trusting
its number.

### Benchmark

Re-run the same 36 questions, export, and diff:

```bash
artmind admin-ui
```

Create a run against `benchmarking/questions.md`, export to
`benchmarking/after-cutover.md`, and compare question by question against
[`baseline-2026-08-23.md`](../benchmarking/baseline-2026-08-23.md).

**Graph metrics going to zero while answers get worse is the failure mode to
watch for** — it would mean the model was optimised at retrieval's expense.

### The property-hint watch list

Phase 7 named nine specific scalar properties as plausible instances of the
unformatted-hint bug, unverified against a live run. **The signal is
intra-document conflicts on exactly these:**

`RISK_METRIC.breach_threshold` · `RISK_METRIC.actual_value` ·
`ROLE_ACTOR.approval_limit` · `METRIC_TARGET.value` · `general/METRIC.value` ·
`contracts` currency and duration fields ·
`technical_paper/METRIC.typical_range_or_benchmark_value` ·
`banking.cases/IMPACT_ASSESSMENT.count` · `sop_guides` duration fields ·
`banking.policy/IDENTIFICATION_DOCUMENT.retention_period`

A conflict confined to one document on one of these is a **prompt bug**, not a
corpus finding — the hint doesn't pin a format, so chunks write `£50,000`,
`50000` and `£50k`. The fix is the RIGHT/WRONG template Phase 3 established.
Report the disputed **values**, not just the property names: that distinction is
the whole difference.

## Step 8 — Close out

- `docs/redesign-phase8-implementation-notes.md` — what the re-ingest surfaced,
  which scorecard rows moved, benchmark deltas, which property hints turned out
  real.
- Update the scorecard doc with the after column.
- Take a post-cutover snapshot.
- Phase 7's deferrals that survive: `README.md` full refresh, the end-user
  opencode persona's scope, and the projection-conflict query surface
  (`query graph conflicts --source projection`) if the re-ingest shows it
  matters.

---

## If it goes wrong

| Symptom | Likely cause | Action |
|---|---|---|
| a domain ingests but the projection is empty afterwards | documents failed to commit, then the deferred rebuild GC'd their keys | restore the snapshot; fix; re-run that domain alone |
| documents skip extraction entirely | `_content_sha256` still in frontmatter against a wiped graph (P1) | strip it and re-run |
| entities missing from `entity-resolve` but present in `pattern1` | embed sweep didn't complete — embedder unreachable | re-run the sweep; it is idempotent |
| `projection status` reports drift forever | `sameas approve` doesn't clear the global flag by design | run a bare `projection rebuild` |
| scorecard row 12 regressed | the schema restructure lost a property declaration | compare the assembled prompt against the Phase 0 corpus's property keys |
