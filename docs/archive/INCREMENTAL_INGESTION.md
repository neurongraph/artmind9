# Incremental ingestion: updates, versioning, supersession, temporality

> ⚠️ **STALE — Pre-redesign (July 2026).** This is the design rationale for how incremental updates *were* handled before the observation/projection redesign (Phase 8, Aug 2026). The post-redesign current behavior is documented in [`INCREMENTAL_INGESTION_v2.md`](../INCREMENTAL_INGESTION_v2.md). Read v2 for current reference; read this only for historical context on prior design decisions.

A review of how the ingestion pipeline handles change over time: what happens when a
document in an already-ingested set is updated, how the result is timestamped and
versioned, how supersession and valid-time work, and how much of it is genuinely
"query ready" at commit time without a refine pass.

**Summary verdict:** most of the machinery is built in. Document- and chunk-level
versioning, supersession, and as-of querying all land at commit time with no refine
step — *provided the new version declares its lineage* (a supersession notice or
metadata row, or a manual `ingest supersede`). The entity layer is deliberately
accretive at ingest, with cleanup deferred to refine; and one commit-time hook
(entity-level temporal normalization) currently no-ops due to an id mismatch, so
entity dates in practice only land via the refine `time` step. Details and gaps below.

---

## 1. The design in one paragraph

Ingest is **accretive**: everything is written with `MERGE`/upsert semantics and
nothing is ever destructively overwritten. At commit time, two deterministic
per-document hooks apply the document's **self-asserted truth** — its own dates,
version, and supersession notice (`commit_to_graph`, `artmind/ingest.py`). Anything
requiring **cross-document judgment** — duplicate-entity merging, conflict
detection, description consolidation — is deliberately excluded from ingest and
lives in the refine pipeline (`artmind/refine_pipeline.py`). The scope taxonomy
is worth being precise about: **ingest hooks are per-document** (they act only on
the committing document's self-asserted truth — its own header dates, version,
and supersession notice); **refine is cross-document** (merging, conflicts, and
consolidation all weigh what multiple documents said, even within a single
domain); and only one step is ever **cross-domain** — the refine conflicts pass
adds a cross-domain comparison when invoked with 2+ domains
(`refine_pipeline.py`, step 4). Queries are made
version-aware not by deleting stale content but by stamping `valid_to` on it and
filtering with an as-of predicate at query time.

## 2. What happens when a document in an ingested set is updated

Walkthrough of re-ingesting `policy_fees.md` after editing it:

1. **Content identity.** The file's SHA-256 is computed and checked against the
   SQLite registry (`documents` table). The edited file has a new hash, so it is
   **accepted as a new document** — dedup only blocks byte-identical content
   (override with `--force`, which mints a synthetic extraction key so the
   duplicate gets its own Document node and chunk cache).

2. **Name identity.** The filename collides (case-insensitively) with the
   registered original, so the new copy is renamed
   `policy_fees_YYYYMMDD_HHMMSS.md` before storage and registration
   (`ingest_file`, `artmind/ingest.py:432`). Both versions now coexist in the
   registry, on disk, and — after extraction — in the graph, each with its own
   `Document` node, `DocChunk`s, and registry row (`added_at` ingestion timestamp).

3. **Extraction** runs per chunk with resumable per-step status rows
   (`kg_chunk_status`, keyed by doc sha + chunk seq). This makes re-runs of a
   *partially failed* ingestion incremental — already-ok steps are skipped, failed
   ones retried — but it is per-version incrementality, not a diff against the
   prior version: every chunk of the new version is extracted fresh.

4. **Graph write** (`_write_to_neo4j`):
   - `Document` and `DocChunk` are `MERGE`d by id — new nodes for the new version.
   - **Entities upsert by `(name, entity_class, domain)`** — so entities mentioned
     in both versions land on the *same* node, with properties merged accretively
     (`_merge_prop_value`): lists union, strings append as `"old | new"`, and
     **numbers/booleans keep the existing (old) value**. Provenance survives via
     `EXTRACTED_FROM` edges to each version's chunks and `chunk_id`/`doc_id`
     stamps on relationships.

5. **Commit-time hooks** (`commit_to_graph`, the single convergence point for
   `ingest sync`, the async worker, staged `write-to-graph`, `pull-kg`, and
   import-bundle):
   1. `normalize_ingested_document` — lifts canonical dates/version onto the new
      Document from its header table / frontmatter per the schema's `temporal:`
      block, and sets `ingested_at` (first commit only, via `coalesce`).
   2. `detect_supersession(only_doc_name=…)` — parses *this document's* own
      supersession declaration and applies it (see §4).

6. **Result**, if the new version carries a supersession declaration: a
   `(:new)-[:SUPERSEDES {scope, effective, detected_by:'notice'}]->(:old)` edge;
   the old Document gets `valid_to = effective` and `superseded_by`; **the old
   version's chunks also get `valid_to`** — which is what makes `--asOf` queries
   exclude the stale text automatically. No refine step required.

   If it carries **no** declaration: the two versions simply coexist, both fully
   live, with their shared entities merged. This is the main workflow gap (§6.1).

## 3. Timestamp & version inventory

| Property | Where | Set by | Meaning |
|---|---|---|---|
| `added_at` | registry row | `_register_document` | wall-clock ingestion time |
| `last_modified` | `Document` | `extract_kg` | file mtime of the original (UTC ISO) |
| `date`, `author` | `Document` | `extract_kg` | markdown frontmatter, if present |
| `ingested_at` | `Document` | temporal hook (`coalesce` — first commit wins) | first commit time |
| `valid_from`, `version`, `time_source` | `Document` | temporal hook | lifted from header labels (e.g. `\| Effective Date \|`, `\| Version \|`) per schema `temporal.document` mapping; frontmatter fallback; optional default `valid_from = ingestion_date` |
| `valid_to`, `superseded_by` | `Document` | supersession | effective date of the superseding version |
| `valid_to` | `DocChunk` | supersession (document scope) | same — drives as-of exclusion of stale text |
| `valid_from` / `valid_to` / `event_at`, `time_source` | `Entity` | schema `temporal.entities` mapping | deterministic parse of a schema-declared date property. **Currently only lands via refine `time` step** — see bug in §6.2 |
| `scope`, `effective`, `detected_by` | `SUPERSEDES` rel | `apply_supersession` | audit trail of how the link was made (`notice` / `manual`) |
| `at`, `reason`, `source_chat_id`, `status:'superseded'` | node-level supersession | `apply_node_supersession` | entity-fact supersession from the NL update flow |

All valid-time values are ISO strings compared lexically, so year/month prefixes
work in `--asOf`.

## 4. Supersession mechanics

Three routes, all idempotent and additive:

- **Self-declared at commit (automatic).** Two recognized formats in the new
  document's own body (`artmind/temporal.py`):
  1. A `## Supersession Notice` prose section naming a superseded **Version N** —
     resolved against other Documents in the domain via their lifted `version`,
     with a title-family guard (`_title_stem` strips trailing `_v2` / `_2026_03` /
     timestamp-rename suffixes) so boilerplate versions like "1.0" on unrelated
     documents don't mislink. Ambiguity is **skipped, never guessed**.
  2. A metadata-table row `| Supersedes | [[doc_name]] |` (+ `| Effective Date |`),
     resolved by document name.
- **Manual, document scope:** `artmind ingest supersede --domain D NEWER OLDER
  [--effective DATE]`.
- **Node scope (fact-level):** `apply_node_supersession` — used by the
  `artmind update` NL flow and `update supersede` for "the branch manager
  changed"-style fact retirement; retires the older Entity node
  (`valid_to`, `status='superseded'`, `superseded_by`) without touching the
  document layer.

Bulk re-scan of a whole domain: `ingest detect-supersession` / refine pipeline
step 2 — same logic, safe to re-run.

## 5. Query readiness after ingest

- `asof_predicate` (`artmind/graph_query.py:62`) is wired through the graph
  patterns, metadata/entity listings, timeline, **and** vector + fulltext search
  (`vector_query.py`). NULL-safe: untimed nodes are always visible.
- `--asOf` accepts `today`/`now`/ISO (validated; garbage raises instead of
  silently hiding content).
- Timeline / entity-context / conflicts queries project
  `valid_from`/`valid_to`/`superseded_by` so answers can cite currency.
- Conflict detection (refine-time) is supersession-aware: a claim pair explained
  by lineage gets verdict `superseded` (history), not `conflicting_claims`.

So: after a commit with a recognized notice, an `--asOf today` query already
returns only current chunks and correctly-dated documents. That is the intended
"query ready with no refine" path, and it works — at the document/chunk layer.

## 6. Gaps and sharp edges

### 6.1 No declaration → no linkage (biggest workflow gap)
A plain re-ingest of an edited file with no supersession notice/row leaves both
versions fully live and unlinked. Nothing infers supersession from the
name-collision rename plus a newer `valid_from`/`Effective Date`, even though the
title-family machinery (`_title_stem`) already reduces `policy_fees` and
`policy_fees_20260722_153000` to the same family. Until an operator runs
`ingest supersede` (or the doc is fixed and re-scanned), default *and* as-of
queries see both versions.

> **Mitigated:** domains can now opt in via `temporal.defaults.supersede_on_title_family: true`
> in their schema — `detect_supersession` then infers a version chain among same-title-family
> documents ordered by `valid_from` (ties skipped, explicit notices take precedence,
> `detected_by: 'title_family'` on the edge). Off by default because dated *series*
> (meeting notes) share a family without superseding each other.

### 6.2 Commit-time entity temporal hook no-ops (bug)
> **Fixed:** the hook now matches by `(name, entity_class, domain)` — the same key
> `_upsert_entity` merges on — and counts only entities the MATCH actually found.
> Entity-level canonical dates land at commit time; the refine `time` step remains
> a bulk backfill, not a prerequisite. The original defect is kept below for context.

`normalize_ingested_document` writes entity dates with
`MATCH (e:Entity {id:$id})` using ids from `entities.json` — which are
chunk-scoped extraction ids (`<doc_id>_001_<eid>`). But `_upsert_entity` never
stores those; graph entities carry fresh `uuid4` ids. The MATCH finds nothing,
the SET no-ops, and the hook still *reports* the entities as written. The unit
test mocks the Neo4j session, so it can't catch this. Consequence: entity-level
`valid_from`/`event_at` only actually land via the bulk `normalize_time` (refine
step 1 or `ingest normalize-time`), which reads ids from the graph — quietly
contradicting the "query ready without refine" goal for entity temporality.
Document-level lifting is unaffected (the Document id in `document.json` *is*
the graph id).

### 6.3 Entity property accretion favors stale values
Because entities merge across versions by `(name, entity_class, domain)`:
- **Numbers/booleans keep the old value** — an updated fee of 6.0 never
  overwrites 5.0 at ingest.
- **Strings accrete** — `effective_date` becomes `"2026-01-15 | 2026-06-01"`,
  and `parse_iso` on that merged string returns the *older* date (the regex
  matches the leading prefix), so even the refine-time entity date backfill can
  canonicalize to the superseded value.
- Superseded documents retire their *chunks*, but not the merged entity property
  values contributed by their extraction. Surfacing those as disagreements is
  the job of refine's conflict detection; fact-level retirement requires the
  node-supersession route.

This is partly by design (accretion at ingest, judgment at refine), but it means
"entity answers reflect the latest version" is **not** guaranteed by ingest
alone — only chunk-grounded answers (vector search + as-of) are.

> **Mitigated for supersessions:** when a commit applies a SUPERSEDES edge for the
> document, `_reassert_superseding_properties` overwrites the merged entities' domain
> properties with the superseding document's own staged values — updated scalars win,
> and date strings stay clean instead of accreting `"old | new"`. Accretion still
> applies between non-superseding peer documents (by design).

### 6.4 As-of filtering is opt-in
Without `--asOf`, no temporal filter is emitted: superseded chunks and documents
still surface in every query, including vector search. There is no default
"current view" and no `status='superseded'` exclusion at the chunk/document
layer. Callers (skills, UIs) must pass `--asOf today` to get the current-truth
view.

> **Mitigated at the skill layer:** the artmind-query skill now instructs `--asOf today`
> as the default retrieval posture, omitted only for explicitly historical questions.
> The CLI itself still applies no filter without `--asOf`.

### 6.5 Bulk `normalize_time` matches domains exactly
Documented TODO in `temporal.py`: `--domain banking` does not fan out to
`banking.*` children (unlike the query layer's rollup). Per-document commit
hooks are unaffected; only bulk backfills need to loop concrete child domains.

## 7. Recommended operator workflow for updates

1. Author the new version with either a `## Supersession Notice` section
   (naming the superseded Version) or `| Supersedes | [[old_doc_stem]] |` +
   `| Effective Date | … |` metadata rows.
2. `artmind ingest sync <file> --domain D` (or `async`). Hooks link and
   retire the old version at commit.
3. If the notice was missing: `artmind ingest supersede --domain D NEWER OLDER
   --effective DATE`.
4. Query with `--asOf today` for current truth; omit `--asOf` for full history;
   `query graph timeline` for lineage.
5. Reserve `docs clean` for *removal* — it deletes the document, its chunks, and
   orphaned entities, i.e. it erases history rather than versioning it.
6. Run the refine pipeline periodically for the judgment layer (dupes,
   conflicts, consolidation) — not because updates require it, but because
   entity-level hygiene is deferred there by design.

## 8. Suggested improvements (priority order)

1. ~~Fix §6.2~~ — **done**: hook matches by `(name, entity_class, domain)`.
2. ~~Auto-propose supersession on title-family match~~ — **done**: schema-gated
   `supersede_on_title_family` inference in `detect_supersession`.
3. ~~Version-aware property merge~~ — **done** for the supersession case:
   `_reassert_superseding_properties` at commit. Per-property provenance remains
   future work if peer-document merges ever need it.
4. ~~Default current view~~ — **done at the skill layer**; a CLI/env-level default
   was deliberately not added (history queries must stay one flag away).
