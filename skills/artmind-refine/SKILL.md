---
name: artmind-refine
description: Run artmind's full graph refinement pipeline for ONE domain — temporal normalization, supersession detection, similar-entity merging, conflict detection, and entity description consolidation — with human review at the judgment gates. Use after ingesting new documents into a domain ("refine domain X", "clean up X after ingest"). For cross-domain conflict detection or investigating one specific merge/conflict, use artmind-refine-graph instead.
---

# artmind Refine

Use this skill to refine a domain's knowledge graph after ingestion. The CLI
pipeline guarantees step *order*; this skill supplies the *judgment* at the
three review gates (merges, conflicts, consolidation quality).

Scope: ONE domain per run (loop for several). For conflicts BETWEEN sibling
domains, or forensic questions ("why did these get merged?", "is this a real
disagreement or an older document?"), hand off to the `artmind-refine-graph`
skill — those are targeted workflows, not pipeline runs.

**Cost warning (tell the user BEFORE proposing):** the propose phase does the
real LLM work — merge adjudication per cluster and conflict adjudication per
candidate pair (observed ~20s per conflict candidate on a local model; the
default `--maxPairs 200` can mean over an hour). On large domains suggest
`--maxPairs 20` for a first pass, and quote `candidates_total` before apply.

## Why order matters (encoded in the CLI — do not run steps manually out of order)

`time → supersession → merge → conflicts → consolidate → embed`

- Temporal properties must exist before anything reasons about currency.
- Supersession stamps `valid_to` before conflict detection, so superseded
  claims read as history, not live disagreements.
- Merges precede conflicts (claims about one real-world entity must meet on
  one node) and consolidation (don't pay LLM calls on soon-merged entities).
- Conflicts precede consolidation, so its skip-open-conflict gate works.
- One embedding sweep at the end covers both merges and rewrites.

## Required Inputs

- `domain`: ask if not provided. One domain per pipeline run; loop for several.

## The Workflow: Propose → Review → Apply → Verify

### 1. Propose

```bash
uv run artmind ingest refine-pipeline --domain <domain> --compact
```

Deterministic steps (time, supersession) run for real — they are additive and
idempotent. LLM steps produce proposals only. The output names a
`report_file` plus sub-proposal files (`merges.json`, `conflicts.json`) and an
`apply_with` command. Cost knobs: `--maxPairs` (conflict candidates),
`--sampleConsolidations` (preview size), `--mergeThreshold`, `--simThreshold`.

### 2. Review — the three gates

Present each gate to the user compactly; get explicit approval before apply.

**Merges** (`steps.merge.proposed_merges`, an alias → canonical map): flag
suspicious pairs — different entity classes, negations ("Approval" /
"Non-Approval"), numbers or versions that differ ("Policy v2" / "Policy v3"
may be *supersession*, not duplication). Remove bad pairs by editing
`merges.json` before apply. Merges are effectively irreversible — when in
doubt, drop the pair.

**Conflicts** (`steps.conflicts.conflicts`): check each proposal's evidence
actually disagrees and that neither side's document is superseded (that is
history — remove it). Edit `conflicts.json` to drop noise.

**Consolidation samples** (`steps.consolidate.rows`): read `new_description`
against `old_description` — it must not invent facts, must keep disagreeing
values side by side, and must mark superseded facts as historical. If samples
look bad, fix by adjusting the model or skip the step (`--steps` without
`consolidate`); per-entity vetting is not needed — consolidation is
idempotent, conflict-gated, and preserves `description_raw`.

`candidates_total` on the consolidate step is the number of entities the
apply phase will rewrite — quote it to the user as the LLM cost before apply.

### 3. Apply

```bash
uv run artmind ingest refine-pipeline --domain <domain> --from-file <report_file> --compact
```

Re-runs time/supersession (idempotent), applies the (possibly edited)
merge/conflict proposals, runs consolidation live (`--consolidateLimit N` to
batch large domains), then nulls merged-canonical embeddings and backfills.
`--apply` without `--from-file` is one-shot compute-and-apply — only for
domains the user explicitly says need no review.

### 4. Verify

Spot-check with artmind-query commands:

```bash
uv run artmind query entity-resolve --domain <domain> --topK 3 --compact "<a merged alias>"   # resolves to the canonical
uv run artmind query graph conflicts --domain <domain> --compact                               # materialized conflicts visible
uv run artmind query entity-context --domain <domain> --entityId <id> --compact               # clean description + source docs
```

Report to the user: merges applied, conflicts materialized, entities
consolidated/embedded, and anything skipped with reasons (the `counts` maps).

## Fallbacks

- Merge proposals empty but duplicates visibly exist → lower `--mergeThreshold`
  (e.g. 0.6) and re-propose.
- Propose-mode conflict proposals are computed BEFORE merges apply (and warn
  `missing_refine` on a never-refined domain). After applying substantial
  merges, re-detect: `--steps conflicts --apply` (one-shot is fine here —
  conflicts materialize as reviewable nodes, they don't destroy anything).
- Conflict candidates 0 across sibling domains → run the pipeline per sibling
  domain first (merge precondition), then `detect-conflicts` across domains:
  `uv run artmind ingest detect-conflicts --domain <d1> --domain <d2> --dry-run`.
  Cross-domain conflict detection is NOT part of the single-domain pipeline.
- Consolidation `failed_llm` counts high → check the LLM service, or pass
  `--model` explicitly; re-running is safe (idempotent).
- Interrupted apply → re-run the same `--from-file` command; every step
  tolerates re-application.

## When NOT to run

- Mid-ingestion (worker jobs still processing the domain) — refine afterwards.
- On a domain about to be re-ingested from scratch.
- Do not run `--apply` (one-shot) on a domain with many merge proposals you
  have not reviewed; the propose → review → apply path exists for a reason.
