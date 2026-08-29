# Redesign change inventory

Everything the observation/projection redesign deletes, rewrites, or adds.
Model in [CONTEXT.md](../CONTEXT.md), mechanism in
[projection-pipeline.md](./projection-pipeline.md).

No backward compatibility is required — the corpus is re-ingested from scratch —
so "delete" means delete, not deprecate.

---

## Whole modules

| Module | Fate |
|---|---|
| `entity_history.py` | **delete.** The `:EntityVersion` zone holds 0 nodes and has never fired. Observations replace it. |
| `refine_pipeline.py` | **delete.** Its six steps evaporate: *time* → ingest-time frontmatter; *supersession* → gone; *merge* → `sameas propose`; *conflicts* → a projection output; *consolidate* → `projection synthesize`; *embed* → the post-commit sweep. Nothing is left to orchestrate. |
| `consolidate.py` | **rewrite in place as `projection synthesize`.** ~80% reusable: idempotency by comparing the contributing set, embedding refresh after write, `description_raw` preservation, provenance recording, conflict skip. Retarget from "entity + its chunks" to "entity + its observations"; drop the HISTORICAL-chunk prompt marking (history is structurally excluded now). |
| `refine_graph.py` | **halve.** Clustering + the merge prompt survive as a *proposer* of same-as groups. `apply_merges` and `apoc.mergeNodes` delete — a destructive merge cannot survive a rebuild. |
| `temporal.py` | **gut.** See below. |
| `delta.py` | **keep, retarget.** `_read_prior_document` keys on `_artmind_id` rather than a path-derived `logical_id`; the four tiers stay. |
| `placement.py` | **unchanged.** Filing classification reads Documents, which are unaffected. |
| `benchmark.py` | **unchanged** — it drives the query agent, not the node model. Becomes the **regression gate** for the whole redesign. |

## `temporal.py`

| Delete | Keep |
|---|---|
| `parse_supersession_notice` · `parse_supersession_metadata_table` | `parse_iso` |
| `detect_supersession` · `apply_supersession` · `apply_node_supersession` | `_find_header_value` |
| `_infer_family_supersessions` · `_title_stem` · `_resolve_version_candidate` | `lift_document_dates` *(retargeted to frontmatter-first)* |
| `_retire_orphaned_entities` — replaced by the zero-observations GC rule | `canonical_entity_dates` |
| `_stamp_chunk_valid_from` — replaced by symmetric `valid_from`/`valid_to` inheritance | `load_schema` · `_deep_merge_temporal` |
| `normalize_time` / `_normalize_time_one_domain` | |

Roughly 400 lines, and the most defect-prone in the repo: on the live corpus,
entity retirement fired on **2 of 5** supersessions, leaving 235 entities from
superseded documents still marked live.

## `ingest.py`

| Delete | Why |
|---|---|
| `_upsert_entity` | replaced by the observation write |
| `_merge_prop_value` · `_merge_props_dicts` | accretive `"A \| B"` — produced 512 self-repeating descriptions |
| `_parse_prop_sources` · `_ledger_upsert` · `_fold_ledger` · `_rollback_property_ledger` | the ledger existed only to un-merge; nothing merges now |
| `_reassert_superseding_properties` · `_incoming_property_values` | supersession is gone |
| `_retract_document_from_neo4j` | replaced by the version/status transition |
| `_purge_from_neo4j` · `purge_document` · `tombstone_document` | replaced by `retire` and `archive` |
| `_sha256_in_registry` as a **global** guard | scoped per-`_artmind_id`; a copied template with an unedited body is a legitimate new document |
| `_filename_in_registry` collision rename | filenames are not identity |
| `_neo4j_value`'s dict→JSON branch | JSON blobs are forbidden; properties flatten or don't exist |

## `graph_query.py`

| Delete | Note |
|---|---|
| `not_deleted_chunk` · `not_deleted_doc` | the `deleted` flag and `status='deleted'` are subsumed by `_status` |
| `list_timeline` | re-expressed as a preset over `entity_listing` |
| `entity_versions` | reads a zone with 0 nodes |
| `asof_predicate` **from the default path** | kept only for the document/chunk `--asOf` predicates |

`resolve_as_of` survives — the retained `--asOf` options still need it.

## `update.py`

Delete the direct `CREATE (e:Entity)`, `_link_entity_in_session`'s property writes,
`apply_node_supersession`, and the `MENTIONS` edge. `update propose` / `confirm`
and the resolution UX stay; the write becomes "record a UserChat and its
Observations, then rebuild". This also retires the defect `CLAUDE.md` warns about —
`update confirm` matching by extracted name rather than the chosen node id —
because the write is no longer "find the Entity and patch it".

## `graph_snapshot.py` — invert it

`BASE_LABELS = ("Document", "DocChunk", "Entity", "UserChat")` and entity matching
on `(name, entity_class, domain)`. Left as-is it would export the **projection but
not the observations**, so an import would land Entities with no backing
observations and the first rebuild would delete every one.

**Export sources, rebuild on import.** Sources are Documents, DocChunks, UserChats,
Observations (both statuses), and `:Synthesis`. The projection, `:Conflict` nodes
and `SAME_AS` edges are all derived and are rebuilt after import — smaller
snapshots, and no way to import a stale projection.

## CLI surface

| Removed | Replaced by |
|---|---|
| `ingest supersede` · `ingest detect-supersession` | `docs retire`; frontmatter declares lineage |
| `ingest normalize-time` | ingest-time frontmatter derivation |
| `ingest consolidate-descriptions` | `projection synthesize` |
| `ingest refine-graph` | `sameas propose` |
| `ingest refine-pipeline` | nothing — no steps left |
| `docs clean` · `docs purge` | `docs retire` · `docs archive` |
| `query graph entity-versions` | `query entity-history` |
| `--replace` | re-ingest of a known identity is always a replace |
| `--force` | the global sha guard it existed for is gone |
| `--asOf` on 12 commands | the projection is current by construction |

**Added:** `projection {rebuild, status, synthesize}` · `sameas {propose, list, approve, reject}` · `docs {retire, restore, archive, restore-from-archive, archived, reindex}` · `query entity-history`.

## Schema, config, storage

| Delete | Replaced by |
|---|---|
| `supersede_on_title_family` | nothing — no inference remains |
| `entity_types` as a list | a map, with a required `kind` |
| `event_at` (+ the `entity_event_at` index) | `valid_from` — for an occurrent entity they are the same date |
| `_prop_sources` | observations are the provenance |
| `Entity.superseded_by` · `status='superseded'` | `_status` on observations |
| `Document.status='deleted'` · `DocChunk.deleted` | `_status` |
| `Document.version` as a string | `_version` (int) + `declared_version` (string) |
| `time_source` · `valid_from_inferred` | `_valid_time_source` |
| the four `entity_version_*` indexes | observation indexes |
| SCD-2 `_is_current` | `_status` |

## Docs, skills, UI

| Surface | Work |
|---|---|
| `artmind/skills/*` (5) | `artmind-query` and `artmind-refine` rewritten; `artmind-update` reworked; `artmind-ingestion-helper` and `artmind-create-schema` updated for the meta-schema |
| `docs/CAPABILITIES.md` (1,533 lines) | large rewrite |
| `text2cypher.py` | schema prompt: new labels, and 249 relationship types collapse to one `RELATES_TO` — a significant simplification for the model |
| `schema_reference.py` | render the **assembled** prompt, not the stored one |
| `cli_guide.py` | follows `COMMAND_GROUPS` automatically; only its examples need review |
| `webui/help.py` | regenerate the concept catalogue |
| chat-ui · admin-ui · opencode persona | follow the CLI and skill changes |
| `artmind_canvas/` | **out of scope** — being redesigned separately |
