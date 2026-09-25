# `vault sync`: a git-diff-driven, CDC-style replay into Neo4j and the structured store

**Status:** Design — approved in brainstorming
**Date:** 2026-09-25
**Owner:** Surjit Das
**Depends on:** [2026-09-25-projection-rebuild-batching-design.md](./2026-09-25-projection-rebuild-batching-design.md)
— this spec calls `projection.rebuild(tx, keys)` as a black box, but relies on it being
fast for the batch sizes described in §5.

## 1. Purpose

Today, applying committed KG-staging JSON (and committed structured-store text) to a
*given* Neo4j/DuckDB instance is entirely manual: an operator decides which documents or
tables changed and runs `write-to-graph`/`table2graph`/`db restore-text` themselves. The
only automated whole-graph mechanism is `graph_snapshot.py`'s `session close`/`session
initiate` — a full export/import of the *entire* Neo4j graph, gitignored
(`.gitignore:25`), not portable via git, and all-or-nothing.

**Goal:** an explicit CLI command, `artmind vault sync`, that detects exactly which
KG-staging document folders and structured-store text changed in git since the last
sync, and replays only those changes into Neo4j and the structured store — incremental,
CDC-like, and git-native, complementing (not replacing) the whole-graph snapshot
mechanism.

## 2. Non-goals

- **No automatic trigger.** Not on every `artmind` invocation, not a git hook. The user
  runs `artmind vault sync` explicitly, whenever they choose.
- **Not a replacement for `session close`/`session initiate`.** Those remain the
  full-backup/full-restore path (e.g., disaster recovery, or seeding a machine without
  replaying git history at all). `vault sync` is the incremental complement.
- **No `same_as.yaml`/schema-diff-scoped partial rebuild.** A `same_as.yaml` or domain
  schema change still relies on the existing drift-detection path (`ProjectionState`
  hash comparison in `import_graph`, or a manual `projection rebuild`) for a full
  rebuild. Fine-grained "only rebuild the keys a same-as group's diff touched" is a
  plausible future enhancement, explicitly deferred.
- **No fix to `db restore-text`'s wholesale scope.** See §7 — documented as a known v1
  limitation, not solved here.

## 3. State: the sync cursor

A new field in `.artmind/state.json` (machine-local, gitignored — `VaultLayout.state_json`):
`last_synced_commit`. Distinct from the already-documented-but-unimplemented
`last_ingested_commit` field on the same file (`vault.py:160`), which belongs to a
different, not-yet-built pipeline stage (auto-triggered *extraction* on new commits —
source file → KG JSON). `last_synced_commit` tracks a different boundary: KG JSON (and
structured text) already committed to git → already applied to *this machine's*
Neo4j/DuckDB. Both cursors can coexist in the same file without conflict.

No marker present is a distinct state from "marker present" — see §6 (bootstrap).

## 4. Diff scope and classification

Two independent, git-diffed trees, using `git diff --name-status
<last_synced_commit>..HEAD` scoped to each:

**A. `.artmind/data/kg/**`** (document folders and `table__<name>` folders alike — both
already share one shape, per `table2graph.write_staged`'s own docstring: "the same
staged files a document's extract leaves behind, so `ingest write-to-graph --folder` can
replay a table's contribution too").

- A folder with an added/modified `observations.json` → **replay**: `commit_to_graph(dir,
  domain, defer_rebuild=True)`.
- A folder that disappeared entirely (every file under it removed) → **retract** (§8).

Note: a `table__<name>` folder is **never an independent trigger** for regeneration —
only for replay if it shows up in this diff directly (e.g., someone ran `table2graph` by
hand and committed the result out-of-band; sync should still pick that up and apply it,
uniformly with a document folder). The *normal* path for a table change is B below, which
produces new `table__<name>` files as an output, not an input, of this run — see §5 for
why the two tracks don't double-apply each other.

**B. `.artmind/data/structured_text/**`** (CSV + `manifest.json` — `VaultLayout
.structured_text_dir`'s own docstring: "the committed, diffable source").

- Any changed file under a given table's export → **regenerate**: rebuild that table's
  parquet from the committed text, then `ingest table2graph <table> --domain <domain>`
  to produce fresh `table__<name>/*.json` and commit it through the same
  `commit_to_graph(..., defer_rebuild=True)` path as track A.
- A table's export disappearing entirely → **retract** the corresponding
  `table__<name>` graph folder (§8), symmetric with a removed document.

## 5. Sequencing within one run

Ordering matters, specifically so track B's *output* (freshly regenerated `table__*`
JSON) is never mistaken for a track-A *input* diff within the same run:

1. Resolve `last_synced_commit` (or fail per §6 if absent and no bootstrap flag given).
   Capture `diff_range = last_synced_commit..HEAD` **once**, up front.
2. Process track B first, over `diff_range`: for each changed structured-text table,
   rebuild its parquet from committed text (§7) and run `table2graph`
   (`defer_rebuild=True`), collecting deferred keys. This writes new `table__*/*.json`
   files to the working tree — **not yet committed**, so they cannot appear in
   `diff_range` (already fixed from step 1) and cannot be double-counted by track A in
   this same run.
3. Process track A, still over the *same* `diff_range` fixed in step 1: for each
   added/modified document folder, `commit_to_graph(..., defer_rebuild=True)`, collecting
   deferred keys. For each removed folder (either track), retract (§8), collecting the
   vacated keys.
4. Union every collected key across steps 2-4. Split into transaction-sized chunks
   (reusing/generalizing `table2graph`'s `REBUILD_BATCH = 400` convention — this is the
   caller-side chunking spec 1 explicitly leaves as the caller's responsibility) and call
   `rebuild_projection(domain, keys=chunk)` once per chunk per domain, with the existing
   progress/ETA logging (now cheap per spec 1).
5. Run the domain-scoped embed sweeps (`_sweep_embeddings`, `_sweep_chunk_embeddings`) —
   same pattern the deferred-rebuild directory-ingest path already uses.
6. `git add` + commit everything newly written under `data/kg/**` this run (the
   regenerated `table__*` folders from step 2, if any) via the existing `vault_git.py`
   primitives (`commit_paths`).
7. **Only on full success of every step above**, advance `last_synced_commit` to the
   `HEAD` sha captured in step 1, and write it to `state.json`.

A failure at any step leaves the marker untouched — the next run recomputes the same
`diff_range` from scratch. Every write involved is a deterministic `MERGE`, so a retry is
*safe* (produces the same end state), though not free: retrying a large already-partially-applied
batch re-demotes/re-writes document versions that succeeded last time, which is a real
but accepted cost — see §9.

## 6. Bootstrap (no marker yet)

`vault sync` refuses to run with no `last_synced_commit` and no explicit flag — it does
not guess. Two flags:

- `--bootstrap-empty`: `diff_range` becomes `<the vault's first commit>..HEAD` — a full
  replay of everything, correct for a genuinely empty Neo4j/DuckDB. Expected to be slow
  for a large existing vault; spec 1 and §5 step 4's chunking are what make it merely slow
  rather than broken.
- `--bootstrap-synced`: no replay at all — just stamps `last_synced_commit = HEAD` and
  exits. For right after a `session initiate`/`db restore` full restore, where the graph
  is already known-current and a full replay would be redundant (and would spuriously
  re-demote every document to a "new version" for no reason).

## 7. Structured-table regeneration (§4 track B) — and its known limitation

The only existing command that rebuilds the structured store's parquet cache *from
committed text* is `db restore-text`, and it is **wholesale**: it wipes and rebuilds
every table, not just the changed one (`cli.py:2133`, `db_restore_text`). `db refresh
<table>`, by contrast, is correctly table-scoped but reads from the table's *original
external* `source_file` (`pipeline.py:588`) — not from the committed vault text, so it's
useless on a machine that never had that external file (exactly the machine `vault sync`
runs on).

**v1 decision:** when track B's diff is non-empty, call `db restore-text` wholesale (once
per sync run, regardless of how many tables changed), then run `table2graph` only for the
tables the diff actually touched. This is correct, and simple, but not incremental at the
structured-store layer — a sync run that touches one small table still pays the cost of
rebuilding every table's parquet. Documented as an accepted v1 limitation, not solved
here. A follow-up (adding table-scoping to `db restore-text`, e.g. `--table <name>`
reading just that table's committed CSV) is a small, contained, separate change —
unlike the rebuild-batching spec, it doesn't need its own full brainstorming pass, but it
should land before `vault sync` is used against a structured store with enough tables for
the wholesale cost to matter.

## 8. Retraction (deletions)

Neither existing "delete" path fits: `_retract_prior_version` demotes a document's prior
version *as part of writing a new one* (`_commit_document_tx`, step 2) — it has no
standalone entry point for "demote and write nothing new." A new function is needed
(`ingest.retract_document(doc_id, domain) -> summary`, name indicative, not final) that:

1. Demotes the document's own `:Document`/`:DocChunk`/`:Observation` nodes to their
   History labels, reusing the exact relabeling primitive `_merge_relabeled`/
   `_retract_prior_version` already use — no new Cypher pattern, just called without a
   subsequent "write the new version" step.
2. Computes the vacated aggregate keys (this document's own observations' keys, read
   *before* demotion) — the same `projection.keys_for_document(tx, doc_id)` call
   `_commit_document_tx` already uses for its own prior-version bookkeeping.
3. Returns those keys to the caller, to be unioned into §5 step 4's rebuild batch exactly
   like any other affected-key set. `rebuild_key`'s existing "zero observations → GC the
   Entity" path (`rebuild_key`'s `absent`/`deleted` outcomes) already handles what happens
   next — no new projection logic needed, only a new way to *reach* it without a
   replacement document.

A `table__<name>` folder's removal (structured-table retraction) uses the identical
function — `retract_document` operates on a `doc_id`/domain pair, and a table's synthetic
`document.json` (written by `table2graph.write_staged`) has a `doc_id` exactly like any
other document's.

## 9. Failure handling and idempotency

All-or-nothing per run, matching this codebase's existing philosophy
(`_commit_document_tx`'s own docstring: "a failure anywhere fails the whole commit,
deliberately — a silently-skipped projection is a silently-stale query layer"). A
transient failure mid-run (Neo4j connection drop, a DuckDB lock, etc.) leaves
`last_synced_commit` untouched; the next invocation recomputes the identical `diff_range`
and reprocesses it from scratch. This is safe (every write is a deterministic `MERGE` on
a stable id) but not free — a document that had already been fully committed before the
failure gets re-demoted-and-rewritten on retry, producing a redundant History version
that carries no new information. Accepted for v1: bounded by "how much of a large batch
had already succeeded before the failure," which should be rare once §5's chunking keeps
any single transaction short.

## 10. Query-only host (no `[ingest]` extra)

Track A (document-folder replay) needs nothing beyond Neo4j — no LLM, no `docling`, no
`duckdb`. Track B (structured-table regeneration) needs the full `[ingest]` extra
(`duckdb`, the structured pipeline). If track B's diff is non-empty and the extra isn't
installed, **the whole sync run fails** (via the existing `_require_ingest_extra()`
pattern) rather than partially applying track A and silently skipping track B — chosen
over a partial-apply-with-warning because a sync that "mostly worked" is a worse outcome
here than one that visibly didn't run at all.

## 11. CLI surface

`vault` is currently a bare command (`artmind vault`, status only — `cli.py:3330`), not a
group. This spec promotes it to a group: `artmind vault status` (existing behavior,
preserved) and `artmind vault sync` (new). Per `CLAUDE.md`'s own convention, this needs
`COMMAND_GROUPS`/`cli_guide.py` routing updated in the same change, or
`test/test_cli_guide.py` fails.

Sketch (exact flags subject to the plan phase): `artmind vault sync [--bootstrap-empty |
--bootstrap-synced] [--domain ...] [--dry-run]`. `--dry-run` reports the classified diff
(N document folders to replay, N to retract, N structured tables to regenerate) without
writing anything — useful for a first look at what a sync run would do before committing
to a possibly-long run.

## 12. Testing

- **Diff classification**: a fixture git repo (via `tmp_path` + real `git init`/`commit`
  calls, matching this suite's existing style for git-dependent tests) exercising every
  branch of §4: added/modified/removed document folder, added/modified/removed
  structured-text table, a `table__*` folder changed directly (the out-of-band case).
- **Sequencing**: assert track B's regenerated files are excluded from track A's diff
  within the same run (§5's core correctness property) — construct a fixture where a
  table's structured-text changes, run sync once, and assert the resulting `table__*`
  commit is not re-processed as a "new" track-A diff entry in that same invocation.
- **Retraction**: `retract_document` demotes exactly the right nodes and returns exactly
  the right vacated keys — using the `run_side_effect`/parameter-recording pattern
  `test/test_update.py` already established (assert on the Cypher actually sent, never on
  a summary count alone — this codebase's own stated lesson from the `update confirm`
  defect in `CLAUDE.md`).
- **Bootstrap**: refuses with no marker and no flag; `--bootstrap-empty` computes the
  right range; `--bootstrap-synced` writes the marker and touches nothing else.
- **Idempotent retry**: a simulated mid-run failure, followed by a second run, converges
  to the same end state as an uninterrupted run (allowing for, and explicitly asserting,
  the redundant-History-version cost noted in §9 — not asserting it away).
- **Query-only host**: track B present + `[ingest]` extra absent → whole run fails with
  the `_require_ingest_extra()` message; track A alone still succeeds when the extra truly
  is absent and no structured diff exists.

## 13. Relationship to whole-graph snapshots

`session close`/`initiate` and this spec's `vault sync` serve different needs and are
both kept: a snapshot is a fast, complete, git-independent restore (disaster recovery, or
skipping replay of a long git history entirely) but isn't portable via git and can't tell
you *what changed*; `vault sync` is git-native and incremental but depends on git history
existing and being reachable. One nuance worth flagging, not fixing here:
`graph_snapshot.py`'s exported `meta` records `exported_at` but no git commit sha
(`graph_snapshot.py`'s `export_graph`) — so after a `session initiate` restore, nothing
machine-readable says *which* commit the restored graph corresponds to, which is exactly
why §6 requires an explicit `--bootstrap-synced` rather than trying to infer it. A future
enhancement could have `export_graph` record `vault_git.current_commit()` in its
`meta`, letting `session initiate` auto-seed `last_synced_commit` — left as a nice-to-have,
not required for this spec.
