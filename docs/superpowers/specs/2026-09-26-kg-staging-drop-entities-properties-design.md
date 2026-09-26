# Drop `entities.json`/`properties.json` from KG-staging output

**Status:** Design — approved in brainstorming
**Date:** 2026-09-26
**Owner:** Surjit Das

## 1. Purpose

`artmind/ingest.py`'s `extract_kg` writes six files per document into the git-committed
KG-staging tree: `document.json`, `chunks.json`, `entities.json`, `properties.json`,
`relationships.json`, `observations.json`. Two of those six — `entities.json` and
`properties.json` — are fully redundant: their content is folded into `observations.json`
at extraction time, before any of the six files touch disk (`observations.build_observation`
copies each entity's identity fields, and `props.update(flatten_domain_props(domain_props))`
folds in that entity's properties, in the same pass that produces the observation dict).
`table2graph.py`'s own staging output already proves this — it never writes either file
and nothing about the commit path requires them (`_load_staged` doesn't read them back
either). They exist purely as write-only leftovers from a pre-redesign extraction model.

**Goal:** stop writing both files for new extractions. Reduce git-staging bloat and the
number of files a document's commit touches, with zero change to what actually lands in
Neo4j (this doesn't touch `write_to_graph`/`commit_to_graph`/`projection.rebuild` at all —
those never read these two files today).

## 2. Scope

- **Both files, not just `properties.json`.** `entities.json`'s identity fields (name,
  entity_class, chunk_id, type, description, aliases, context) are exactly as redundant
  with `observations.json` as `properties.json`'s are.
- **Forward-only.** New extractions stop writing these two files. Already-committed
  copies in existing vaults' git history are left alone — harmless leftover files, not a
  migration target. No cleanup script, no `docs`/`ingest` command to strip them from a
  vault retroactively.
- **Dashboard metric change, not removal.** The admin-ui's ingest-artifacts view
  (`/api/artifacts/{domain}`) currently reports `entity_count`/`property_count` derived
  from these two files. `property_count` is dropped entirely (properties are no longer a
  separate concept once folded into observations). `entity_count` is redefined to derive
  from `observations.json` instead — the count of distinct entity mentions this document
  actually contributed, post-dedup, which is arguably the more meaningful number anyway
  (the old `entities.json`-based count included pre-dedup duplicates within the same
  chunk).

## 3. Changes

**`artmind/ingest.py`** (`extract_kg`): delete the two `_write_json("entities.json",
all_entities)` / `_write_json("properties.json", all_properties)` calls. `all_entities`
and `all_properties` stay as in-memory variables — they still feed `_build_observations`
and `canonicalize_document` earlier in the same function; only the disk write goes away.

**`artmind/webui/dashboard_routes.py`** (`/api/artifacts/{domain}` handler): change
`"entity_count": _json_len(doc_dir / "entities.json")` to `"entity_count": _json_len(doc_dir
/ "observations.json")`, and delete the `"property_count": _json_len(doc_dir /
"properties.json")` line entirely.

**`artmind/webui/static/dashboard.js`** (line 379, inside `refreshArtifacts()`): the
artifact-card note line currently reads
`` `${a.entityCount} entities · ${a.propertyCount} properties · ${a.relationshipCount} relationships` ``.
This is a hard-coded template literal, not generic key iteration — dropping
`property_count` from the API response without touching this line would render
`"N entities · undefined properties · N relationships"`. Change it to
`` `${a.entityCount} entities · ${a.relationshipCount} relationships` ``.

**`artmind/observations.py:275`**: `build_observation`'s docstring says `` `entity` is one
entry from a chunk's extracted `entities.json` ``. Now stale (that file no longer exists);
reword to describe the in-memory list it actually comes from (`extract_kg`'s own
`all_entities`), not a file.

## 4. Tests

Every test that references either filename either pads a fixture nothing reads back, or
genuinely asserts on their content:

- **`test/test_ingest_concurrency.py`** — `_merged_entity_chunk_ids` and the two tests that
  use it (`test_extract_kg_concurrent_processes_every_chunk`,
  `test_extract_kg_concurrent_matches_sequential`) assert "exactly one entity per chunk, no
  drops or chunk_id collisions" across 1-way vs 4-way concurrent extraction, by reading
  `entities.json`. Switch the helper to read `observations.json` and pull `chunk_id` off
  each observation dict instead of each entity dict — `build_observation` copies `chunk_id`
  onto every observation verbatim, and for this fixture's synthetic, chunk-distinct names
  dedup never collapses two chunks into one observation, so the count and chunk_id-set
  assertions carry over unchanged in meaning, just reading a different (still-written)
  file.
- **`test/test_webui_admin_api.py`** — `test_artifacts_lists_docs_with_counts_and_in_graph_flag`
  asserts the full API response dict by exact equality, including `propertyCount`. Update
  its fixture helper (`_write_doc_kg_dir`) to populate `observations.json` instead of
  `entities.json`/`properties.json`, and drop `propertyCount` from the expected response.
  `test_artifact_bundle_downloads_zip`'s use of the same helper doesn't assert on
  entity/property counts, so it's unaffected either way.
- **`test/test_kg_pull.py`, `test/test_chunk_offsets.py`, `test/test_filing_metadata.py`,
  `test/test_ingest_identity.py`** — each writes `entities.json`/`properties.json` as
  empty-list padding to fully populate a fake staged directory before calling
  `write_to_graph`/`commit_to_graph`, which never reads either file back. Remove both
  names from each fixture's write loop/tuple.

## 5. Non-goals

- No migration or cleanup path for entities.json/properties.json already committed in
  existing vaults (§2).
- No change to `write_to_graph`, `commit_to_graph`, `table2graph.py`, or
  `projection.rebuild` — none of them read these two files today, so none of them change.
- No change to the `/bundle` zip-download endpoint — it globs whatever's present in the
  folder, so it needs no code change and simply produces smaller zips for documents
  extracted after this change.
