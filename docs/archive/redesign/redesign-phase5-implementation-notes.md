# Phase 5 implementation notes

What actually landed for Phase 5 (lifecycle: archive, snapshots, registry
shrink), against the plan's bullets in
[redesign-phase-plan.md](./redesign-phase-plan.md), CONTEXT.md's Retire/Status
vocabulary, and the specs in [document-identity.md](./document-identity.md)
and [stores-and-repos.md](./stores-and-repos.md). Read those first — this is
implementation scope and decisions, not the design, and it assumes Phase 3's
model (observations, the projection, `_status`) and Phase 4's (the History
label pairs, `_id`/`_domain`) as background.

---

## What changed

### New modules

| Module | Holds |
|---|---|
| `artmind/archive.py` | `archive_document` / `restore_from_archive` / `list_archived`, the graph-deletion primitive (`_delete_document_tx`), and `index.jsonl`. |
| `artmind/derived_markdown.py` | The pure promotion decision (`decide`) and its two signals (`markdown_was_edited`, `is_promoted`) for binary-source derived-markdown promotion. No I/O — mirrors `document_identity.py`'s pure/orchestration split. |
| `artmind/reindex.py` | `reindex()` — rebuilds the registry from vault frontmatter. |

### A. Archive

`docs archive` / `docs archived` / `docs restore-from-archive`, all new.
`ARTMIND_ARCHIVE_DIR` (new, default `~/artmind_archive`) added to `paths.py`,
deliberately its own root — not under `ARTMIND_DATA_DIR` — so a data-dir wipe
can't touch it, and excluded from `artmind snapshot` for the same reason.

- **Bundle layout**, under `ARTMIND_ARCHIVE_DIR/<artmind_id>/`: `manifest.json`,
  `document.md` (the vault file's final content), `original.<ext>` (binary
  sources only), `kg/<domain>/<stem>/...` (copied verbatim from `KG_DIR`
  staging). Self-contained by construction — nothing in the bundle points
  back at the data dir or the vault.
- **Manifest**: `_artmind_id`, title, domain, `version` (the document's own
  version counter — there's no separate per-version node to span, so this
  is just `Document.version`), `valid_from`/`valid_to` (the **full** span
  across every observation this document ever contributed, both statuses —
  not just the current version's own window, which only covers the latest),
  `original_vault_path`, `source_type`, `has_original_binary`,
  `vault_commit`, `archived_at`.
- **Graph removal** (`_delete_document_tx`): `DETACH DELETE` on
  `(:Observation OR :ObservationHistory)`, `(:DocChunk OR :DocChunkHistory)`,
  `(:Document OR :DocumentHistory)` all matched by `doc_id`/`id`, then the
  affected-key rebuild — the same key-capture idiom `lifecycle._transition`
  uses, just deleting instead of relabelling. **No `:DocumentArchived`
  label** — archive removes, it does not relabel; retire (Phase 3) is the
  one that relabels, and conflating the two was explicitly the trap to
  avoid (see the prompt's trap 2).
- **Vault removal**: `vault_git.remove_paths` (new — `git rm` + commit).
  Falls back to a plain `unlink()` only when there's no vault/git repo to
  commit into, logged loudly since that path leaves no commit recording the
  removal.
- **Data-dir original deleted too** (confirmed with you: Q2, "Delete the
  data-dir copy too"): once archived, nothing recoverable is left outside
  the bundle for a binary source, matching "portable and the original is
  elsewhere cannot both be true".
- **`index.jsonl`** at `ARTMIND_ARCHIVE_DIR/index.jsonl`, append-only — read
  by `list_archived()`/`docs archived`. Written right after the bundle is
  fully on disk, before any destructive step, so a partial failure downstream
  (graph delete or git rm) still leaves the index knowing the bundle exists.
- **`restore-from-archive`**: replays the bundle, recommits the KG staging
  JSON via the existing `commit_to_graph` path, then **immediately** calls
  `lifecycle.retire_document` — reusing Phase 3's tested primitive rather
  than inventing a parallel "commit as history" code path. Refuses on
  either collision the spec calls out (target vault path holds a different
  file; the id is already live), mirroring `resolve_identity`'s `refuse`
  row exactly; `--toPath`/`--newId` resolve them the way `fork`/`adopt` do
  there.
- **No `purge`.** Deleting a bundle is a filesystem act, stated as a
  limitation in `docs archive`'s own CLI help text, not left implicit.

**Scope note**: archive operates on documents that have a `:Document`/
`:DocumentHistory` node — vault-native and binary-derived. csv/xlsx never
produce those (they project to `:Table`/`:TableColumn` only — see
stores-and-repos.md's flow C), so `docs archive` doesn't apply to them; not a
gap, just outside what "document" means for the graph. An ad-hoc `.md`
ingested outside the vault (or a binary ingested with no
`ARTMIND_VAULT_DIR` configured) still resolves and bundles fine, but the
"vault removal" step is a no-op in that case — there's no vault file to
`git rm`, only the data-dir/logical-id-keyed copy, which the bundle already
captured under `document.md`.

### D. Derived-markdown promotion — binary sources get `_artmind_id`

`_ingest_binary_or_adhoc` never touched the vault at all before this phase
(confirmed by reading it — no `_derived/` writes existed anywhere). New
function `_ingest_binary_derived` (in `ingest.py`) handles every **true**
binary source (`pdf`/`pptx`/`docx`, not an ad-hoc `.md`) when a vault is
configured; falls back to the old `_ingest_binary_or_adhoc` path otherwise
(no vault → nowhere to mirror derived output into).

**Identity now runs through `_artmind_id`, one identity not two.** The
derived (or promoted) markdown's `_artmind_id` becomes the graph's
`Document.id` directly — `ingest_to_kg`'s existing `"artmind_id" in
file_result` branch (Phase 2's "one identity" reasoning, already coded for
vault-native) picks this up unmodified. The old `_logical_id`/
`_resolve_doc_identity` two-tier scheme is untouched for ad-hoc `.md` and the
no-vault case, exactly as Phase 2 left it.

**"Has this binary been converted before" is answered by the filesystem, at
two deterministic, domain+stem-scoped locations — not the registry.**
`_derived/<domain>/<stem>.md` existing means "not yet promoted"; NOT
existing does not mean "never converted" — promotion moves the file out by
design. The second location, `<domain>/<stem>.md`, is where promotion moves
it to (this module's own choice of destination folder — the spec doesn't
name one). Both are scoped by `domain`, so re-ingesting under a genuinely
different domain without `--setDomain` reads as a fresh document — the same
limitation `_ingest_vault_native` already accepts for a plain `--domain`
before a file's own frontmatter can override it, except here a binary has no
frontmatter to consult until *after* this lookup finds it.

**`binary_changed` needs no registry round-trip either.** `dest_path`
(`documents/originals/<filename>`) already persists "the last original we
saw" on disk; comparing its hash against the incoming file's, *before*
`shutil.copy2` overwrites it, is enough. (An earlier draft tried to detect
this via the registry, keying two rows off the same `artmind_id` — that
collided with the registry's own `UNIQUE(artmind_id)` constraint the moment
both the original and the derived doc tried to register under it. Caught by
the integration test, not by hand-reading; see "Bugs the gate caught".)

The 2x2 from docs/document-identity.md:

| markdown edited? | binary changed? | action |
|---|---|---|
| no | no | `no_op` |
| no | yes | `convert` (reconvert, safe) |
| yes | no | `promote` (stop deriving it) |
| yes | yes | `collision` (refuse, report both) |

An already-promoted document (`_source_type == "md"`) refuses reconversion
outright, before this 2x2 ever runs — checked first, at whichever of the two
locations currently holds it.

`_derived_sha256` joins `document_identity.SYSTEM_FIELDS` — present only on
a not-yet-promoted derived document, removed outright on promotion (unlike
every other system field, which persists).

`_ingest_binary_or_adhoc` itself lost nothing — its docling-conversion +
image-description block was extracted into a shared
`_convert_binary_via_docling` helper so `_ingest_binary_derived` could reuse
it without duplicating ~70 lines of docling/image-description logic.

**Registry rows**: only the derived/promoted document gets one (artmind_id
+ path + content_sha256, upserted by artmind_id like any vault-native
document). The raw original binary does **not** get a separate row anymore
— it doesn't need one now that lookup is filesystem-based, and the earlier
design that gave it one is exactly what caused the collision above.

### E. Registry shrink

`documents` table: `id, artmind_id, domain, filename, path, sha256, added_at`
→ `id, artmind_id, domain, path, content_sha256, last_ingested_at`.

- **`filename` dropped** — it was always `Path(path).name`. The one caller
  that queried by it (`_build_file_result_from_db`, used by CLI retry-job
  paths and the admin dashboard) now matches in Python against every row in
  the domain (registries are small; no portable basename function in plain
  sqlite3) rather than in SQL.
- **`sha256` → `content_sha256`, and it's now the RIGHT number.** Before
  this phase, the registry stored a whole-file hash (frontmatter included)
  for every source, vault-native included — a second, disagreeing number
  from the body-only `_content_sha256` `decide_version` already computes
  and writes into frontmatter. `_register_document` now takes an optional
  `content_sha256` kwarg; vault-native ingest passes its already-computed
  `decide_version(...).content_sha256` through. Binary/tabular sources
  (no separable body) keep a whole-file hash — there's nothing else to hash.
- **`added_at` → `last_ingested_at`, and it's now honest.** The old column
  was actually already refreshed on every upsert in the *live* code path
  (`_register_document`'s `ON CONFLICT` clause) — only the dead,
  never-called `_registry_upsert` helper failed to refresh it. Renamed
  anyway, since "added_at" read as "first insert" regardless of what the
  code actually did, and `_registry_upsert` is deleted (see below).
- **Migration**: no migration path, per Phase 2's own precedent for the
  `artmind_id` column — an old-shaped table is dropped and recreated empty.
  The check in `db._init_db()` now looks for `content_sha256` rather than
  `artmind_id` (subsuming the older check: any table missing the new column
  is missing the old one's fix too).
- **Dead code removed**: `_sha256_in_registry`/`_filename_in_registry`
  (zero callers anywhere — leftover from the pre-Phase-2 dedup era) and
  `_registry_upsert` (also zero callers; `_register_document` has always
  done its own SQL, never called through this helper).
- **A real, independent bug fixed in passing**: the path-only branch of
  `_register_document` (binary/tabular sources, `artmind_id=None`) had no
  `UNIQUE` to `ON CONFLICT` against, so re-ingesting the *same* unchanged
  binary inserted a **new duplicate row every single time** — the table
  only ever grew. Fixed with an explicit delete-then-insert keyed on `path`
  alone (deliberately not scoped by `domain` — a path is already a natural
  unique key, and scoping by domain would leave a stale duplicate behind
  under the old domain on a re-home).

### B. Snapshot inversion

`graph_snapshot.BASE_LABELS` drops `Entity` and adds `Synthesis`
(pre-emptively, for Phase 6 — an empty `MATCH` costs nothing). Export is
sources only now: `Document(History)`, `DocChunk(History)`, `UserChat`,
`Observation(History)`, `Synthesis`. `:Conflict` and every projection-owned
edge (`RELATES_TO`, `AGGREGATES`, `SAME_AS`) were never in `BASE_LABELS` and
still aren't — `_export_relationships`'s existing
`any(l IN labels(s) WHERE l IN $base_labels)` filter drops them automatically
now that their `:Entity` endpoint isn't in the set, no separate change
needed. `_restore_nodes`'s `:Entity`-specific label-reconstruction branch
(rebuilding `<CLASS>:Entity` from stored labels or `entity_class`) is deleted
outright — there's no derived label left to reconstruct.

**`import_graph`'s new final phase, unconditional**: docs reindex → full
projection rebuild → embed sweep, in that order.

- Reindex runs in a `try/except` — a restore with no vault configured (a
  pure query host, say) must not lose the far more important rebuild that
  follows. Its failure is returned as `reindex_error`, not swallowed.
- The rebuild is `projection.full_rebuild(tx, None)` (every domain) — **not**
  optional, no `try/except` around it, per CLAUDE.md's invariant.
- The embed sweep needed its own orchestration: `ingest.rebuild_projection`
  already does "full rebuild + sweep" for a *single* domain, but its sweep
  is gated `if domain:` — a `domain=None` full rebuild (exactly restore's
  case) skips the sweep entirely today. `import_graph` instead computes
  `all_keys` once, groups them by **top-level** domain family (`key[2]` up
  to the first `.`), and sweeps each family separately — `_sweep_embeddings`
  itself scopes by exact domain match (`e._domain = $domain OR STARTS WITH
  $domain + '.'`), so a family-level sweep is required to reach e.g. both
  `banking.reference` and `banking.products` entities.
- **Reported loudly, not merely logged**: after the sweep, a direct count
  of `embedding IS NULL OR embedding_stale` across `:Entity` is returned as
  `embedding_stale_remaining` in the result dict — the CLI surfaces this in
  its JSON output, not buried in a log line only visible with `-v`.

### C. Snapshot components

- **`registry` dropped** from `VALID_COMPONENTS`/`DEFAULT_COMPONENTS` — it's
  now a pure path↔id cache, rebuildable in seconds by `docs reindex`.
- **`curation` added** (not in the default set — opt in via `--only`):
  `same_as.yaml` (tolerates absence — Phase 6 hasn't shipped it yet) +
  every file under `domains/schemas/`. Named explicitly, never a glob of
  `ARTMIND_HOME` — `.env` sits right next to `same_as.yaml` in that folder,
  and a snapshot zip is exactly the artifact people hand to each other.
  Verified live in the hermetic suite: a `.env` planted alongside
  `same_as.yaml` never appears in the produced tar or zip.
- **`originals` added, in the default set** (confirmed as deliberate and
  counter-intuitive, per the prompt — not "fixed back"): bundles
  `documents/originals/` whole. This was in **no** snapshot before Phase 5;
  a data-dir wipe used to lose every ingested binary permanently.
- **Manifest gains `vault_commit`/`vault_dirty`** (`vault_git.current_commit`/
  a new `vault_git.is_dirty`), both `None` — not `False` — when no vault is
  configured or the check itself fails, so a manifest never claims a
  guarantee it didn't actually check.
- **The `curation` restore precondition was investigated and dropped, not
  implemented.** The prompt flagged this directly: Phase 3 denormalized
  `_kind` onto every observation specifically so the rebuild would never
  need to re-consult a domain schema. Confirmed by reading
  `projection.rebuild_key`: it reads `winner.get("_kind")` straight off the
  observation, never loads `DOMAIN_SCHEMAS_DIR`. A `graph`-only restore
  therefore needs nothing from the run folder, full stop — the precondition
  the plan proposed would have been a check against a condition that can't
  actually occur, so it isn't there.
- **`ARTMIND_ARCHIVE_DIR` stays excluded**, stated as such in
  `unified_snapshot.py`'s docstrings, not left as an accident of living
  outside the data dir.

---

## Bugs the gate caught

**1. The registry's `UNIQUE(artmind_id)` constraint rejects two rows sharing
one id** — found while building `_ingest_binary_derived`'s first draft, which
tried to register *both* the original binary's row and the derived
document's row under the same `artmind_id` (reasoning: "one logical document,
one id, so both its physical locations should carry it"). The second
`_register_document` call's `INSERT ... ON CONFLICT(artmind_id) DO UPDATE`
doesn't create a second row — it overwrites the *first* row's `path`,
silently merging the two into one, so "where is the original binary"
information was lost the instant the derived doc was also registered.
Caught by `test_ingest_binary_derived.py`'s `test_reingest_unchanged_binary_
is_a_no_op` — reconversion happened on the *second* ingest when it should
not have, because the original's registry row (needed to detect `binary_
changed`) no longer existed under a lookup key the design assumed. Root
cause traced back to the schema constraint, not a logic bug in either
branch; the fix was architectural (see "What changed", D) — locate the prior
derived doc by two deterministic filesystem paths instead, and detect
`binary_changed` by re-hashing `documents/originals/<filename>` directly,
needing no second registry row at all.

**2. A duplicate-row leak in the registry's path-only branch**, found while
rewriting `_register_document` for the shrink (not by a failing test — by
re-reading `_register_document`'s own INSERT statement while renaming its
columns). The `artmind_id IS NULL` branch had no `ON CONFLICT` target at
all — every call was a bare `INSERT`, so re-ingesting the *same* unchanged
binary or csv/xlsx file inserted a fresh row every time, forever. Predates
this phase (present since Phase 2), but Phase 5's binary-identity work would
have made it materially worse: `_ingest_binary_derived`'s `binary_changed`
detection no longer depends on this row at all (see bug 1's fix), but a
`_registry_row_by_path` caller elsewhere trying to answer "what do we know
about this path" would have silently gotten an arbitrary one of several
stale rows. Fixed with an explicit delete-then-insert (see "What changed",
E).

**3. A third registry-shrink call site, missed by the first grep pass.**
`jobs._retry_job` (re-queuing a failed ingestion job's files) deregistered
each failed file from the registry with `DELETE FROM documents WHERE domain
= ? AND UPPER(filename) = ?` — untouched by the earlier sweep because it
lives in `jobs.py`, not `ingest.py`/`db.py`, and `_retry_job` is only ever
*mocked* in the test suite (`test_webui_admin_api.py` monkeypatches it
directly), never exercised against a real registry db. Would have raised
`sqlite3.OperationalError: no such column: filename` on the first retry of
any failed job after this phase shipped. Found by a final grep sweep for
every registry-column reference across the whole codebase (not by a test,
since none existed) after believing the shrink was complete; fixed by
matching a bare filename against `path`'s own basename in Python, same
pattern as `_build_file_result_from_db`, and a regression test
(`test_retry_job_deregister.py`) added since none existed before.

**4. The admin-ui's snapshot endpoints still defaulted to, and accepted,
`registry` as a component** — `artmind/webui/dashboard_routes.py` had its
own copies of the valid-component set and the default component list
(`RestoreRequest`'s Pydantic default, `api_create_snapshot`'s and
`api_import_snapshot`'s query-param defaults), none of which import from
`unified_snapshot.py` — so dropping `registry` there didn't touch these at
all. A create-snapshot call with no explicit `components` query param would
have 400'd on its own hardcoded default the moment `registry` stopped being
valid. Found by a repo-wide grep for "registry" near "snapshot" after
believing every call site was covered; fixed by having all four spots import
`VALID_COMPONENTS`/`DEFAULT_COMPONENTS` from `unified_snapshot.py` instead of
hardcoding their own copies (a good change independent of this bug — one
list feeding both surfaces means dropping `registry` again, or adding a
component in a later phase, can't repeat this). A live test
(`test_create_snapshot_passes_selected_components`) had asserted this exact
broken default worked; fixed to exercise `curation` instead of `registry`.
The admin dashboard's own HTML (`dashboard.html`'s create/import checkbox
groups, literally labeled "Registry (documents)"/"Registry", checked by
default) and its JS fallback array (`dashboard.js`) had the same problem one
layer further out — fixed alongside, replacing the `registry` checkbox with
`originals` (checked, matching the new default set) and `curation` (unchecked,
matching it not being in the default set).

No bug was found in the archive/restore-from-archive graph-deletion or
snapshot-rebuild logic itself, live or in hermetic testing — but see "What
this phase's exit gate did and did not exercise" below for what that claim
does and doesn't cover.

---

## Exit gate

`just dev-test`: **1611 passed, 14 skipped, 0 failed** (baseline before this
phase: 1554 passed). New test files: `test_derived_markdown.py`,
`test_ingest_binary_derived.py`, `test_reindex.py`, `test_archive.py`,
`test_unified_snapshot_phase5.py`, `test_retry_job_deregister.py`, plus
additions to `test_vault_git.py` and fixes to
`test_graph_snapshot.py`/`test_ingest_identity.py` for the Phase-5-obsoleted
Entity-reconstruction and `filename`-column behavior.

Live, against real AuraDB (`ARTMIND_NO_PROXY=1`), after `just dev-stop-
daemons && just dev-install && artmind setup` — schema application is clean
(Phase 5 introduces no new Neo4j constraints/indexes; `archive.py`'s deletes
and `graph_snapshot.py`'s narrower export both run against the existing
Phase 4 schema unchanged).

Live, on a **throwaway document outside the real vault**
(`ARTMIND_VAULT_DIR`/`ARTMIND_ARCHIVE_DIR` both pointed at fresh `/tmp` paths
for the duration of the gate, domain `banking.reference` — a real schema,
cleaned up afterward): ingested via the real pipeline (real docling-free
vault-native path, real `qwen3.6:35b-mlx` extraction, real
`nomic-embed-text` embeddings, real AuraDB commit) — 2 chunks, 3
observations, 2 entities, one real vault git commit.

```
PASS  docs archive: bundle exists at ARTMIND_ARCHIVE_DIR/<id>/ with
      manifest.json + document.md (kg/ subdir present too — the document
      had no original binary, so no original.<ext>)
PASS  manifest fields populated correctly: version=1, valid_from=2026-01-15
      (lifted from the observation, not a placeholder), vault_commit set,
      source_type=md, has_original_binary=false
PASS  vault file gone; a real git commit recorded the removal
      ("artmind: archive zzztest5-widget-rate (banking.reference)")
PASS  graph holds nothing for it (entity-listing on the domain returns
      zero rows; a direct MATCH by id/doc_id returns zero nodes)
PASS  archive/index.jsonl has the row; `docs archived` lists it (reading
      the index, confirmed by design, not by re-deriving from the
      filesystem)
PASS  restore-from-archive: vault file restored + git-committed; KG staging
      JSON recommitted (2 chunks, 3 observations, 2 entities rebuilt);
      immediately retired afterward
PASS  restored status is history, NOT latest -- confirmed both indirectly
      (the projection rebuild after retirement shows 0 entities, since
      this was the document's only source and retirement demotes it) and
      directly (a raw Cypher check: exactly one :DocumentHistory, two
      :DocChunkHistory, three :ObservationHistory for this doc_id -- zero
      nodes under the LATEST labels)
PASS  no duplicated chunks or observations (trap 1): exactly 2 chunks and
      3 observations after the archive -> restore round trip, matching the
      original ingest exactly -- not 4 and 6
PASS  snapshot create (against the real graph, non-destructively): default
      components (graph, structured, kg_staging, originals) all present;
      registry absent (dropped); curation absent (opt-in, not requested);
      manifest carries vault_commit + vault_dirty=false
PASS  snapshot zip contains NO .env -- checked at the zip's own top level
      AND by opening every one of its four component .tar.gz files and
      checking their members too (a leak one level down would not show up
      checking only the zip's top level)
```

One thing found live, not a bug in this phase's own code: `docs restore`
(Phase 3, promoting history back to latest) resolves a document by name via
`lifecycle.resolve_document_id`, whose Cypher matches `(d:Document)` only --
**not** `(d:Document OR d:DocumentHistory)`. So a document sitting in
`history` (exactly the state `restore-from-archive` deliberately leaves
one in) cannot be found by `docs restore --documentName <name-or-id>` at
all, even by its exact id. Pre-existing (unrelated to anything this phase
changed — `resolve_document_id` hasn't been touched since Phase 3), and the
same shape of gap Phase 4's notes already accepted for `entity-history`
("an entity with zero remaining observations has no node left to resolve
`--entityId` through"). `restore_from_archive` itself is unaffected — it
calls `lifecycle.retire_document(doc_id, domain)` directly with the id it
already has, never through `resolve_document_id` — but a human trying to
promote a restored document afterwards via the CLI hits this. Flagged
below as an open question rather than fixed here, since `resolve_document_id`
is Phase 3's function and out of this phase's stated scope.

**Not performed live**: `snapshot restore` (wiping the database and
reloading it) was **not** run against the shared production AuraDB this
repo is configured against — doing so would have wiped the user's real
corpus, and no disposable second Neo4j instance was available to restore
into instead. The reindex → full-rebuild → embed-sweep sequencing this
would exercise is covered by `TestImportGraphRebuildPhase` in
`test_graph_snapshot.py` (mocked session, asserting call order and
per-domain-family sweep scoping) but that is not the same as watching it
happen against a real graph. If you want that leg verified live, point
`ARTMIND_KG_NEO4J_URI` at a disposable Neo4j instance and run `artmind
snapshot restore <the-zip-from-above>`.

### What this phase's exit gate did and did not exercise

The hermetic suite (1609 tests) verifies every pure decision (the promotion
2x2, the archive/restore collision rules, the registry's column semantics)
and every graph query's shape (which Cypher runs, with which parameters) via
mocked sessions — per CLAUDE.md, a `MagicMock()` session answers any query
truthily, so these tests assert on the parameters sent and the queries that
ran, never on summary counts. `test_vault_git.py`'s and
`test_ingest_binary_derived.py`'s git operations run against **real** temp
git repositories, not mocks — the actual `git mv`/`git rm`/`git commit`
machinery is exercised, just not against the user's real vault.

Not exercised by the hermetic suite, and only as much of it as the live gate
above actually ran: a real multi-chunk document going through real docling
conversion and real LLM extraction before being archived; a `docs archive`
of a document that has genuine conflict/temporal history behind it; a
snapshot `curation`/`originals` restore landing on top of a **live**,
non-empty run folder / data dir (the hermetic tests restore into empty temp
directories); and the full promote → archive → restore-from-archive chain
end-to-end on one document.

---

## Deferred, on purpose

| Deferred | To | Why |
|---|---|---|
| `:Synthesis` actually being written anywhere | **Phase 6** | `BASE_LABELS` lists it pre-emptively (an empty `MATCH` costs nothing); nothing produces one yet — unchanged from Phase 3's own deferral. |
| Applying `same_as.yaml` groups during the rebuild | **Phase 6** | Unchanged from Phase 3 — the seam (`same_as.load_groups()`) is tested, the merge-across-keys isn't built. The `curation` snapshot component ships the *file*, not the behavior. |
| A full rewrite of the `artmind-query`/`artmind-refine`/`artmind-ingestion-helper` skills to document `docs archive`/`docs reindex` | **Phase 7** | Already the plan's assignment (Phase 4's notes flagged the same boundary for its own new commands). |
| Async ingest (`ingest async`) picking up derived-markdown promotion | Not deferred — **already correct**, but worth stating: `worker.py`'s per-file dispatch calls the same `ingest_file`, which already routes a true binary through `_ingest_binary_derived` when a vault is configured. Nothing async-specific was needed. |

---

## Open questions for later phases

1. **Promotion's destination folder (`<vault>/<domain>/<stem>.md`) is this
   module's own choice, not something docs/document-identity.md specifies.**
   It shares a namespace with hand-authored vault-native documents of the
   same domain+stem — an unlikely collision, and the failure mode if hit is
   safe (`git mv` refuses to overwrite a tracked file, so promotion fails
   loudly rather than clobbering), but if a later phase wants a dedicated,
   collision-proof promoted-document folder, this is where to change it.
2. **`restore-from-archive --toPath`/`--newId` re-point the staged KG JSON's
   own identity fields (`id`, `path`, `name`) by hand-editing
   `document.json` inside the restored KG staging directory**, rather than
   through any resolution table. This mirrors `resolve_identity`'s
   `fork`/`adopt` in *effect* but not in *mechanism* — there's no equivalent
   of the six-row resolution table for a restore-from-archive collision.
   Exercised by unit tests (mocked graph); not exercised live in this
   phase's gate. If collisions turn out to be common in practice (repeated
   archive/restore cycles on the same id, say), this is worth formalizing.
3. **The registry no longer has any row for a binary's raw original once
   Phase 5's promotion path handles it** (see "What changed", D) — anything
   that used to look up a binary by its data-dir path via the registry
   (none found in this codebase during this phase, but a future feature
   might assume one exists) needs to instead resolve the binary through its
   derived/promoted document's own registry row, or through the filesystem
   convention `_ingest_binary_derived` uses.
4. **`lifecycle.resolve_document_id` cannot find a document once it's in
   `history`** — found live (see "Exit gate" above), not by a test. Its
   Cypher matches `(d:Document)` only, never `(d:Document OR
   d:DocumentHistory)`, so `docs restore --documentName <anything, even the
   exact id>` fails to resolve a document `restore-from-archive` just placed
   in history — which is exactly the state a human would want to promote
   next. Pre-existing since Phase 3, unrelated to anything this phase
   touched, and the same shape of accepted gap Phase 4's notes already
   documented for `entity-history`. Worth a one-line fix (widen the MATCH)
   whichever phase next touches `lifecycle.py` — flagged here rather than
   fixed, since `resolve_document_id` is outside this phase's stated scope
   and `docs restore`'s own tests never exercised a history-status target.
5. **`Document.path` (and therefore `manifest["original_vault_path"]`) is an
   absolute, resolved path, not the vault-relative `canonical_path()` string
   the registry uses.** This is inherited from Phase 2's own `file_result`
   shape (`"registered_path": str(source.resolve())`, unchanged by this
   phase), not something Phase 5 introduced, but it means a restored-from-
   archive document's default target path is only portable back to the same
   machine/environment it was archived from — moving a bundle to a
   different machine needs an explicit `--toPath`. If a later phase wants
   bundles portable across machines by default, this is the field to
   change, and it would touch every reader of `Document.path`, not just
   archive.
