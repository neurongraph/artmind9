# Redesign phase plan

Execution order for the observation/projection redesign. Model in
[CONTEXT.md](../CONTEXT.md) · mechanism in
[projection-pipeline.md](./projection-pipeline.md) · scope in
[redesign-change-inventory.md](./redesign-change-inventory.md) · skills in
[redesign-skills-review.md](./redesign-skills-review.md) · baseline in
[redesign-quality-scorecard.md](./redesign-quality-scorecard.md) · infrastructure in
[stores-and-repos.md](./stores-and-repos.md).

**No backward compatibility.** The corpus is re-ingested from scratch at Phase 8.

**Standing rule for every phase** — from `CLAUDE.md`: stop the daemons and re-run
`artmind init`, or you will verify against stale code and a stale run folder.

```bash
just dev-stop-daemons && just dev-install
```

**Carry-forward convention.** Each phase runs in its own session, and sessions share
nothing but this repo. So every phase ends by writing
`docs/redesign-phaseN-implementation-notes.md` recording what actually landed, what
was deferred and to which phase, and any bug the exit gate caught. Every phase
**begins** by reading the notes from all prior phases. A decision that lives only in
a session transcript is a decision that will be silently re-invented.

Notes so far: [Phase 2](./redesign-phase2-implementation-notes.md).

---

## Phase 0 — Baseline ✅ complete

| | |
|---|---|
| Snapshot | `~/artmind_data/graph_snapshot/artmind_snapshot_2026-08-23_164618.zip` |
| Benchmark | `banking_corpus_before_artmind_changes` — 36 questions, completed 2026-08-23 → [`benchmarking/baseline-2026-08-23.md`](../benchmarking/baseline-2026-08-23.md) |
| Scorecard | 12 measurements, re-runnable script → [redesign-quality-scorecard.md](./redesign-quality-scorecard.md) |

**Still to do in Phase 0:** create the vault repo and set `ARTMIND_VAULT_DIR`. Do
this before Phase 2, and **do not ingest anything in between** — setting it changes
`_canonical_key` on today's code from casefolded-basename to vault-relative-path.

---

## Phase 1 — Schemas as structured data · master

The largest single work item, and deliberately first: it changes **extraction
quality**, which is the biggest variable in the system, and it must not be changed
in the same commit as the node model or a regression has two possible causes.

- `artmind/domains/meta.yaml` — package asset, one level above `schemas/` so it
  isn't caught by the four `*_schema.yaml` globs
- Meta-schema validator, run by `init` and `domains harmonize`, **failing loudly**
- `entity_types` promoted from list to map; **`kind` mandatory** on all 97 classes
- Per-class `properties` and `relates_to` declarations; `temporal.entities` folds in
- Prompts assembled at runtime from `meta.yaml` + declarations; `guidance:` stays prose
- `harmonizer.py` raw-text surgery → dict merge
- `schema_reference.py` renders the **assembled** prompt
- Relationship prompt layout fixed — the `A ↔ B:` header leak

**Migration:** 16 schemas × 97 classes, LLM-assisted then human-reviewed. Its own
work item, not a sub-task.

**Exit gate:** re-extract one document, diff the entity/property/relationship output
against the current run. Property-key hygiene (scorecard row 12) must not regress;
relationship-type leakage (row 4) should already fall.

## Phase 2 — Identity and versioning · master

**Full specification: [document-identity.md](./document-identity.md)** — read it
before planning; it carries the resolution table, the frontmatter contract, and the
promotion rules.

- `_artmind_id` in frontmatter, uuid7, seeded on first ingest (one bulk commit)
- The six-row resolution table: re-ingest / move / refuse / adopt / heal / new
- `_content_sha256` (**body only**) drives `_version`; `declared_version` split out
- `_`-prefixing of system properties, including `_id` and `_domain`
- `--replace` and `--force` deleted
- Git commit per artmind-authored frontmatter change; push opt-in, non-fatal
- **No copies of vault-native files** into `originals/` or `markdowns/` — the vault
  file *is* the document and git *is* its history
- Single `markdown_path_for(document)` resolver replaces four hand-built paths
  (`temporal.py` ×3, `delta._compute_body_block_hashes`)
- Derived-markdown promotion (`_derived_sha256`)

**Exit gate:** edit a vault file, re-ingest, confirm one version bump; touch only
frontmatter, confirm none; `git mv` it, confirm identity survives.

---

*Everything below is on a branch.*

## Phase 3 — Observations and the projection

The core. `:Observation` written per (doc_version, chunk, entity-identity); the
projection rebuilt deterministically inside the same transaction.

- Observation write replaces `_upsert_entity`; `_prop_sources` and the accretive
  merge deleted
- Retrieval-gated name vocabulary (ANN, recurrent classes only) + per-document
  canonicalization pass → `canonical_name`
- Key function: normalization layers 1–2; `_id = sha256(key)`
- Property merge by shape; `_temporal_props`; `:Conflict` by `kind` + `valid_from`
- Affected-key rebuild, zero-observations GC
- `embedding_stale` marking — **never null an embedding**

### ⛔ Vertical slice gate

Ingest the three `interest_rate_schedule_*` documents. **Pass = one
`SmartSaver Account Tier 2 Rate` entity holding 4.50%, `_temporal_props:
["rate_value"]`, three observations behind it.** Do not build Phase 4 on an
unproven projection.

## Phase 4 — Query layer

- `:DocumentHistory` / `:DocChunkHistory` / `:ObservationHistory` label swaps
- `--asOf` removed from 12 commands; `not_deleted_*` deleted
- `query entity-history` added; `entity-versions` deleted; `timeline` re-specified
  as a preset over `entity-listing`
- 249 relationship types → one `RELATES_TO {rel_type}`
- Aggregate edges materialized with real provenance

## Phase 5 — Lifecycle

- `docs retire` / `restore` / `archive` / `restore-from-archive` / `archived`
- `docs clean` and `purge` deleted
- `ARTMIND_ARCHIVE_DIR` + `archive/index.jsonl`; bundles include the binary
- `graph_snapshot` inverted — export sources, rebuild on import
- `curation` snapshot component (`same_as.yaml` + schemas, **never `.env`**);
  `originals` component; `registry` component dropped; `docs reindex` on import
- Registry shrunk to a path↔id cache

**Inherited from Phase 2** — see
[its notes](./redesign-phase2-implementation-notes.md), "Deferred, on purpose":

- **Derived-markdown promotion** (`_derived_sha256`) and extending `_artmind_id` to
  binary sources. Phase 2 deliberately left binaries on the old
  `_logical_id`/`_resolve_doc_identity` path — this is where they're upgraded, and
  it's a prerequisite for the two items below.
- **`docs reindex`** — has nothing to rebuild *from* until promotion lands.
- **`ingest async` flags** — `worker.py` commits once per file rather than once per
  job; `ingest sync`'s directory batching got the bulk-commit treatment, async did
  not. A fast-follow, not a correctness gap.

## Phase 6 — Curation

- `same_as.yaml` — groups, not pairs; run folder, not data dir
- `sameas propose / list / approve / reject`; one proposer, two outcomes
- `refine_graph`'s clustering survives as proposer; `apply_merges` deleted
- `projection synthesize` — `consolidate.py` relocated and re-scoped
- `refine_pipeline.py`, `entity_history.py`, `summarize_gates.py` deleted

## Phase 7 — Surfaces

- Skills: `artmind-query` (heaviest), `artmind-refine` → **`artmind-curate`**,
  `artmind-update`, `artmind-ingestion-helper`, `artmind-create-schema`
- **Drift test** comparing the query skill's stated schema against
  `structural_metadata` — the guard, not generation
- `CAPABILITIES.md` restructured around sources → observations → projection → query
- `text2cypher` prompt · `webui/help.py` · admin-ui · opencode personas
- `placement.py` role expanded — it fills the human-owned frontmatter fields

## Phase 8 — Cutover

```
wipe Neo4j → ingest sync <vault> → projection rebuild → embed sweep
           → projection synthesize → benchmark → scorecard
```

**Done when:** scorecard rows 1–11 hit target, row 12 has not regressed, and the
36-question benchmark is no worse than
[`baseline-2026-08-23.md`](../benchmarking/baseline-2026-08-23.md) question by
question.

Graph metrics going to zero while benchmark answers get worse would mean the
redesign optimised the model at retrieval's expense. That is the failure mode to
watch for.
