# Phase 4 implementation notes

What actually landed for Phase 4 (the query layer), against the plan's bullets
in [redesign-phase-plan.md](./redesign-phase-plan.md) and the five items Phase
3 explicitly deferred here (see its own notes, "Deferred, on purpose"). Read
[projection-pipeline.md](./projection-pipeline.md) and
[redesign-phase3-implementation-notes.md](./redesign-phase3-implementation-notes.md)
first — this is implementation scope and decisions, not the design, and it
assumes Phase 3's model (observations, the projection, `_status`, the two
valid-time axes) as background.

---

## What changed

### Relationships: collapsed, and re-derived from a new raw layer

249 per-domain Entity-to-Entity Neo4j relationship *types* collapse to one:
`(:Entity)-[:RELATES_TO {rel_type, observation_count, chunk_ids, doc_ids}]->(:Entity)`.
`rel_type` is a **property**, not the Neo4j type — mirrored on `Entity.type`
(which stays a property) rather than `Entity.entity_class` (which is promoted
to a real label). The reasoning, confirmed with you before coding: the
problem being solved is `text2cypher` having to guess one of 249 possible
type spellings per domain, and a relationship-property index
(`relates_to_type`) gives the same lookup speed a native type would without
that guessing problem.

This is not just a rename. Before this phase, `_write_relationships` wrote
`RELATES_TO`-shaped edges **directly between `:Entity` nodes at commit time**,
accumulating `doc_ids`/`chunk_ids` onto the live edge with
`apoc.coll.toSet(...)`. That is derived state written directly — the same
category of defect "never write `:Entity` properties directly" exists to
prevent, just on an edge instead of a node property. Phase 4 replaces it with
a raw/aggregate split matching the Observation/Entity one:

- **Raw, immutable layer** — `artmind.observations.relation_observation_id` /
  `ingest._write_relation_observations` write
  `(:Observation)-[:ASSERTS_RELATION {id, rel_type, doc_id, chunk_id, ...}]->(:Observation)`
  between the two `:Observation` nodes a relationship connects, at commit
  time, before the rebuild. Endpoints resolve against **this document's own
  observations** (by `chunk_id` first, then a doc-wide name fallback) — the
  same limitation the old writer always had: an endpoint not itself extracted
  as an entity in this document silently drops the relationship. Whatever a
  relationship extraction carries beyond the structural fields (a schema's own
  `relates_to` declaration adding e.g. "role") flattens onto the edge via
  `_flatten_props`, same drop-nested-with-warning discipline as everywhere
  else.
- **Aggregate layer** — `projection._sync_relates_to` (new, called from
  `rebuild_key` alongside the existing property/conflict/AGGREGATES work)
  follows `ASSERTS_RELATION` from a key's `latest` observations in both
  directions, groups by `(rel_type, other_key)`, and `MERGE`s one `RELATES_TO`
  edge per group — deleting and recreating every edge touching the entity from
  scratch each rebuild, the same "recompute from the authoritative source"
  idiom `_write_conflicts` and the `AGGREGATES` rewiring already use.

Two correctness properties fall out of this for free, the way the
Observation/Entity split already gives Phase 3's model:

- **Retiring a document silently drops its relationship contributions.**
  `_retract_prior_version` no longer touches edges at all (the old
  `edges_retracted`/`edges_deleted` block is gone) — relabelling a document's
  observations to `:ObservationHistory` makes their `ASSERTS_RELATION` edges
  structurally invisible to the aggregation query (both `MATCH` patterns
  require the `:Observation` label on both ends), and the affected-key rebuild
  recomputes the aggregate from what's left.
- **A relationship's endpoints are always in the same affected-key set.**
  Because both sides of `ASSERTS_RELATION` are always entities this same
  document observed, `_sync_relates_to` never depends on the *other*
  endpoint's own rebuild having already run in the same pass — whichever of
  the two keys is processed second (in `rebuild`'s `sorted(keys)` order) finds
  the first one's `:Entity` already `MERGE`d and writes the edge. No ordering
  dependency, order-independent by construction.

`update.py::write_user_chat` mirrors the document path: relationship
extraction from a chat writes `ASSERTS_RELATION` between the chat's own fresh
observations (`_write_chat_relation_observations`, replacing the old
`apoc.merge.relationship` call), inside the same transaction as the
observation write and the rebuild — not after it, as the direct-to-Entity
writer required.

`RESERVED_REL_TYPES` gained `RELATES_TO`, `ASSERTS_RELATION`, `AGGREGATES`
alongside the existing `SUPERSEDES`/`EXTRACTED_FROM`/`PRIOR_STATE` — the
system's own collapsed-relationship machinery, which an extractor must never
be able to claim as its own `rel_type`.

### Label swaps: `:DocumentHistory` / `:DocChunkHistory` / `:ObservationHistory`

Additive to the label, not a replacement for a property — because there was
no property to replace. **`_status` is deleted outright, not kept alongside
the labels.** Decided mid-phase, at your call: keeping both a property and a
label that must always agree is the same shape of defect as the three
competing GC mechanisms Phase 3's notes describe, and "no backward
compatibility, full re-ingest at Phase 8" removes the only reason to keep the
old representation alive for continuity. The label pair *is* the state now:

- `ingest._retract_prior_version` and `lifecycle._transition` swap
  `:Observation`\/`:DocChunk`\/`:Document` for their History counterpart (and
  back), instead of setting a property. Chunks used to be `DETACH DELETE`d on
  re-ingest; they are relabelled instead now, same as `docs retire` already
  did for observations.
- `projection.read_latest_observations` / `all_keys` / `keys_for_document`
  read the label instead of a `_status` property. `keys_for_document`'s
  `status=` parameter now selects a **label** (`"latest"` → `:Observation`,
  `"history"` → `:ObservationHistory`, `None` → either, via `(o:Observation OR
  o:ObservationHistory)`) rather than filtering a property.
- The label swap is *why* retiring a document now structurally drops its
  chunks out of `chunk_text_ft` / `chunk_embedding` — both indexes are defined
  `FOR (c:DocChunk)` only, and Neo4j native indexes have no room for an inline
  predicate. A property flag could never have done this; a label can.
- `setup.py` mirrors constraints (id uniqueness) and indexes (`doc_id`,
  `domain`, and for Observation, `key`) onto all three History labels.
  `graph_snapshot.BASE_LABELS` gained the three History labels — they *are*
  the retired half of Document/DocChunk/Observation, not a separate zone, so
  omitting them would silently drop every retired document from a snapshot.
- The vault frontmatter's own `_status` field (`document_identity.py`) is
  untouched — unrelated data (on-disk YAML, independent of the graph's
  internal representation), not the property this bullet removes.

`query entity-history` (below) is the one reader that spans both labels on
purpose — everywhere else, matching the base label alone is now sufficient
and is what "current" means structurally.

### `Entity.id` → `_id`, `Entity.domain` → `_domain`

Extraction-contract fields stay unprefixed (`name`, `description`,
`entity_class`, `type`, `context`, `aliases` — the whole query layer reads
these, and Phase 3 already established the pattern of not touching them).
`_id`/`_domain` are artmind-computed, not extracted, which is the same
reasoning that already put `_status`/`_valid_from`/`_kind` etc. on
Observations.

`graph_query.domain_predicate` gained a `prop` parameter (default `"domain"`,
callers touching an `:Entity` node pass `prop="_domain"`), and a new
`domain_predicate_any` covers the two truly unlabeled matches
(`graph_metadata`'s full-schema scan) where the property name isn't known
ahead of time — it ORs both predicates rather than picking one.

This rename reaches every file that matches `:Entity` directly, not just
`graph_query.py`: `projection.py`, `vector_query.py`, `update.py`,
`conflicts.py`, `refine_graph.py`, `consolidate.py`, and the embed sweep in
`ingest.py`. The **six** existing Entity id/domain indexes/constraints
(`entity_id`, `entity_id_idx`, `entity_lookup`, `entity_domain`,
`entity_name_domain`, `entity_class_domain`) move onto `_id`/`_domain`.

**Query output follows storage, not a translated shape.** Where a query
returns Entity properties via `.* ` spread (patterns 1–4/7–9) or an explicit
map projection (`entity_context`, `entity_resolve`, `list_conflicts`), the
JSON now genuinely shows `_id`/`_domain` — no re-aliasing back to `id`/`domain`
for API-shape continuity. Consistent with how every other `_`-prefixed system
field already surfaces in raw output, and there is no compatibility
constraint left to preserve.

### `query entity-history` (new)

`query entity-history --entityId X [--asOf T] [--property P]`. The **fact**-
level axis (`_valid_from`/`_valid_to`), never `_doc_valid_from` (document-
level; that only decides the projection's *winner* — see
projection-pipeline.md, "Two valid-time axes on every observation"). Resolves
`--entityId` through the live `:Entity` node's `key` property, then matches
`(o) WHERE (o:Observation OR o:ObservationHistory) AND o.key = key` — the one
reader in the whole query layer that spans both labels, because its entire
purpose is "what did this used to be", which by definition includes retired
sources. `--property P` narrows the output to that one property's value at
each point (`{value, valid_from, valid_to, doc_id, observation_id}`) instead
of the full observation.

**Known limitation, not fixed**: an entity with zero remaining observations
anywhere (fully retired) has no `:Entity` node left to resolve `--entityId`
through, so `entity-history` has nothing to look the id up against in that
case. It answers "what was true about a still-projecting entity", not "what
did we ever know about something now fully gone" — the latter would need a
different resolution path (there is no way back from an opaque hashed id to
its key without one), and nothing in this phase's scope asked for it.

### `timeline` re-specified

Domain-scoped, not entity-scoped: every entity of a `kind: occurrent` class
(read from each domain's schema via `temporal.load_schema`, a **local**
import inside `graph_query.timeline` — `temporal.py` already imports
`graph_query.py`, so a module-level import the other way would cycle),
ordered by `valid_from`, windowed by `--from`/`--to`. `--entityId` is gone —
an entity's own fact history now lives in `entity-history`. `list_timeline`
(the old per-entity two-hop relationship traversal reading `event_at` off
*neighbouring* entities) is deleted, and so is `event_at` itself plus its
index: for an occurrent entity, `valid_from` already **is** the event date, so
a second axis was redundant. Implemented as a sibling of `entity_listing`'s
domain-scoped `:Entity`-matching shape (same `domain_predicate` idiom, no
`--asOf` — the projection is current by construction) rather than a literal
call into `entity_listing`, whose grouped-by-label output shape doesn't carry
`valid_from` and isn't suited to a time-ordered list.

### `entity_history.py` and `entity-versions` deleted

The whole module, the `graph entity-versions` command, its two Cypher blocks
(`_CAPTURE_CYPHER`/`_SNAPSHOT_CYPHER`), and the `:EntityVersion`
constraint+3-indexes zone in `setup.py`. Confirmed already unhooked from
`commit_to_graph` since Phase 3 — deleting it here was purely a "nothing
references it, remove the dead zone" cleanup, not a design decision.

### `--asOf` removed from 12 named commands (13 counted literally — see below)

Removed from `graph metadata`, `entity-listing`, `entity-resolve`,
`entity-context`, and patterns 1–9 (all ten entity patterns, `pattern5`
included, per your task description's explicit call-out). Kept on
`pattern10`, `vector-text`, `chunks`, `docs list` (i.e. `graph
filing-listing`), `db timeline`, `db sql`. `resolve_as_of`/`asof_predicate`
survive — the retained options still need them.

**Arithmetic note, not a design change**: counting the named removal list
literally gives 13 commands, not 12 (9 patterns + entity-context +
entity-resolve + entity-listing + graph-metadata). Implemented against the
full named list regardless of the discrepancy; flagging it in case the "12"
was meant to exclude one of these and I misread which.

**`pattern10`'s `--asOf` stopped being ignored.** Its old help text said
"Accepted but IGNORED... output carries `asOf_ignored`"; the `asOf_ignored`
escape hatch is deleted (per your instruction) for both the patterns that
lose the option and pattern5, which keeps it structurally impossible to need
(a path traversal has no single filterable node). But pattern10 *keeps*
`--asOf`, and once the label swap exists, "ignored" was no longer the honest
answer for it: chunks carry no valid-time of their own (never stamped at
ingest — confirmed by grep, there is no `chunk.valid_from` write anywhere),
so there is no date to compare against, but the label swap gives it a real
history pool to reach into for the first time. **New behaviour**: without
`--asOf`, pattern10 matches only `:Document`/`:DocChunk` (current), as always.
With `--asOf` (any value — a presence flag here, not a point in time), it
also matches `:DocumentHistory`/`:DocChunkHistory`, surfacing a retired
document and its retired/superseded chunks. Coarser than "in force by T"
elsewhere, and the CLI help text says so rather than implying a date filter
that doesn't exist. This is a small behavioural expansion beyond a pure
option removal, flagged as such rather than silently added — the label swap
existing is what makes it possible, not a separate decision.

Every surviving `--asOf` help string is reworded off "as of" framing:
`valid_to` is rarely set, so `asof_predicate` collapses to `valid_from <=
asOf` — "in force by this date", a floor, not a point-in-time snapshot. The
old wording ("Valid-time filter... nodes without valid-time always shown")
read as a snapshot and wasn't one.

### `not_deleted_chunk` / `not_deleted_doc` / the `deleted` flag deleted —
### and `docs clean` / `docs purge` pulled forward with them

Your call, mid-phase. Deleting the flag machinery this task asked for would
have orphaned two live commands (`docs clean` sets the flag; `docs purge`
hard-deletes) that write exactly what was being removed. Rather than leave
them half-working or defer the whole bullet to Phase 5 (where the phase plan
originally put their removal), both commands are gone now — the same call
Phase 3 made pulling `docs retire`/`restore` forward when it hit the
equivalent gap. Deleted: `docs.command("clean")`, `docs.command("purge")`,
`tombstone_document`, `purge_document`, `_purge_from_neo4j`,
`_retract_document_from_neo4j` (only caller was `_purge_from_neo4j`), and the
now-orphaned local-file helpers `_find_registered_documents`,
`_delete_from_registry`, `_table_exists`, `_delete_chunk_status(_by_doc_id)`,
`_path_is_under`, `_delete_path` (all had exactly one caller: `purge_document`).
`justfile`'s `docs-clean`/`docs-purge` recipes became `docs-retire`/`docs-restore`.
Phase 5 still owns `archive`/`restore-from-archive`/`archived` — retire/restore
were already Phase 3's, this just closes out the flag-based half of the old
lifecycle surface early.

### `_neo4j_value`'s dict→JSON branch deleted

It survived only for the old per-type relationship properties (arbitrary
extracted fields on a direct Entity-Entity edge); `ASSERTS_RELATION`
properties now flatten through the same discipline `flatten_domain_props`
already established for observations. `_neo4j_value(key, value)` (gained a
`key` parameter, for the warning message) now drops a nested object with a
warning instead of `json.dumps`-ing it, for **every** caller of
`_flatten_props` — Document, DocChunk, and relationship properties alike, not
observations alone. "Properties flatten or they don't exist" is a codebase-
wide invariant now, not an observation-specific one.

### `ingest async` deferral

`worker.py::_process_job` mirrors `cli.py::ingest_sync`'s directory-batching
exactly: `defer_rebuild = len(queued_files) > 1 and not stage_only`, one
`rebuild_projection(domain)` per touched domain after the loop instead of one
incremental rebuild (and one embed sweep against descriptions the next file
was about to change) per file.

---

## Bugs the gate caught (pre-existing, found while touching adjacent code)

None of these are Phase 4 regressions — all three predate this phase (two
from Phase 3's EXTRACTED_FROM move, one from Phase 3 apparently never
wiring MENTIONS's replacement) — but were fixed here because leaving a
provably-dead or provably-wrong pattern in a function I was otherwise
rewriting line-by-line for `_id`/`_domain` would have been worse than fixing
it, and in one case (`entity_context`) the bug is directly relevant to this
phase's own exit gate.

1. **`entity_context`'s source-chunk retrieval always returned zero chunks.**
   It matched `(e:Entity)-[:EXTRACTED_FROM]->(c:DocChunk)` directly — but
   Phase 3 moved that provenance onto observations; an Entity has never
   carried a direct `EXTRACTED_FROM` edge since. Fixed to
   `(e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)`. This
   is the flagship "grounded picture of an entity" command silently missing
   its whole grounding half.
2. **The same bug, three more places**: `structural_metadata`'s
   `EXTRACTED_FROM` relationship count (always 0), `conflicts.py`'s
   `gather_evidence` and `materialize`'s superseded-branch (the pairwise
   conflict adjudicator's evidence-gathering and document-lineage lookup for
   `Entity->DocChunk`, both always empty). All four fixed the same way.
3. **`:MENTIONS` (UserChat→Entity) is dead and was left wired into six read
   paths** (`entity_context`, patterns 2/3/4/9's `mentions` degree mode,
   `structural_metadata`) despite `update.py::write_user_chat` never having
   written it (Phase 3's own change-inventory lists "the MENTIONS edge" as
   deleted from the write side; the reads were never cleaned up). Removed
   from every read path this phase already had open for other reasons;
   `pattern9 --degreeMode mentions` now counts via
   `AGGREGATES->Observation->EXTRACTED_FROM` instead. **Not swept
   exhaustively** — only the paths this phase's other changes already
   touched. A skill-file audit (below) found two more live references.

## Skill-file drift caught in passing

`artmind-query`, `artmind-refine`, and `artmind-ingestion-helper` are Phase
7's rewrite, not this phase's — but three of their documented commands
(`entity-versions`, `docs clean`, `docs purge`) and one flag
(`ingest sync --replace`, dead since Phase 2) now hard-fail if the chat-ui
agent follows them literally, which is worse than stale prose. Patched the
specific broken command references and the routing-table rows built around
them (`timeline`'s old entity-scoped shape, `entity-versions` →
`entity-history`, `chat_sources` which no longer exists in patterns 2/3/4's
output) — **not** a full rewrite of either skill's surrounding narrative,
which is genuinely Phase 7's job and out of scope here. `artmind-query`
still has ~13 other `--asOf` mentions across its body that were not
individually audited against which commands actually kept the option;
flagged for Phase 7 rather than guessed at.

---

## Exit gate

`just dev-test`: 1554 passed, 14 skipped, 0 failed.

Live, against real AuraDB (`ARTMIND_NO_PROXY=1`), after `just dev-stop-daemons
&& just dev-install && artmind setup` (the new constraints/indexes, including
the three History-label mirrors and `relates_to_type`, applied cleanly against
the existing production graph — `entity_id`'s uniqueness constraint on `_id`
succeeded because no existing entity has that property yet, which is also why
none of the pre-Phase-4 corpus is reachable through the new `_domain`-scoped
query layer until something rebuilds it; expected, not a bug, and exactly what
"no backward compatibility, full re-ingest at Phase 8" means in practice).

Ingested two small throwaway documents (`ZZZTEST2 Widget Savings` — Tier A/B
rates, January and February 2026, a `higher_tier_than` relationship between
the tiers) via the ad-hoc/binary ingest path, **outside `ARTMIND_VAULT_DIR`**
so the run touches only Neo4j/the registry/local KG staging and never the
user's actual vault git repo. Domain `banking.reference` (real schema, so
`RATE_ENTRY`'s `kind: recurrent` and its declared `higher_tier_than`
vocabulary are exercised for real). Cleaned up afterward (Neo4j nodes, local
`kg/` staging dirs, registry rows) — none of it left behind.

```
PASS  fresh Entities carry _id/_domain, not id/domain
PASS  ASSERTS_RELATION written between Observations (rel_type, doc_id, chunk_id)
PASS  RELATES_TO aggregate materialized (rel_type, observation_count, chunk_ids, doc_ids)
PASS  query graph metadata: entities touched by fresh data show ONE relationship
      type (RELATES_TO), with rel_type carried as a property (APPLIES_TO,
      APPLIES_TO_ACCOUNT_TYPE, HIGHER_TIER_THAN all observed as property values)
PASS  entity-history (Tier A rate, --property rate_value) returns BOTH January
      (3.0) and February (2.9), ordered by date, spanning two documents
PASS  entity-context on the same entity shows only the current winner (2.9),
      no history
PASS  docs retire: 3 observations → history; chunks relabelled DocChunkHistory,
      not deleted; retired chunks vanish from chunk_text_ft (search still finds
      the still-latest February document's chunks, not January's)
PASS  after retire, the entity's _temporal_props correctly drops rate_value —
      only one instant (February) remains in the latest pool, so it no longer
      varies; the rebuild recomputes this dynamically, not from a cached flag
```

### A critical bug the gate caught: re-committing a document duplicated its chunks/observations

Found on the **second** live write (re-running the same staged document to
apply a hand-edited fixture — but this is not a contrived scenario: any
ordinary re-ingest of an edited document hits the identical code path, since
chunk ids are deterministic per `{doc_id}_{seq}` regardless of content
changes, and an unmoved entity's observation id is equally deterministic).
Neo4j rejected the write outright:

```
Neo.ClientError.Schema.ConstraintValidationFailed: Node already exists with
label `DocChunkHistory` and property `id` = '<chunk-id>'
```

**Root cause**: `MERGE (o:Observation {id: $id})` (and the equivalent for
`DocChunk`/`Document`) includes the label in its match pattern. Step 2 of
`_commit_document_tx` (`_retract_prior_version`) had, moments earlier in the
*same transaction*, relabelled the prior version's chunk/observation nodes at
that same id from `:DocChunk`/`:Observation` to their History counterpart.
Under Phase 3's `_status`-property retraction this was harmless — the label
never changed, so `MERGE` always found the same physical node and a fresh
`SET o = $props` simply reasserted its content. Once retraction became a
**label swap** (this phase), `MERGE (o:Observation {id: $id})` could no
longer find that id at all — it now lives under `:ObservationHistory` — and
silently created a **second** node with the same id under `:Observation`,
one write away from a duplicate the id-uniqueness constraint doesn't catch
(the constraint is per-label, so a `:DocChunkHistory` node and a `:DocChunk`
node may legally share an id value) until a *second* re-commit tried to do it
again and collided against the History-labelled leftover from the first.

This would have hit **every ordinary re-ingest** of an already-known
document, silently on the first occurrence (a duplicate node, no error) and
loudly on the second (this constraint violation) — a correctness bug the unit
tests couldn't have caught, because none of them chain a real `_commit_document_tx` twice against a real Neo4j inside the same test.

**Fix**: `ingest._merge_relabeled(tx, base_label, history_label, id, props,
*, replace=True)` — looks the id up under *both* labels (two indexed lookups,
each backed by that label's own id constraint/index, so this stays
index-backed rather than falling back to an unlabelled scan), then either
creates a fresh node (id genuinely new) or revives whichever one it found:
sets its properties, adds `base_label`, removes `history_label`. Used by
`_write_observations`, the chunk-write loop, and the Document write in
`_commit_document_tx` — the three places retraction can relabel out from
under a same-transaction MERGE. Verified live: a second `write-to-graph` of
the same staged document now completes cleanly, with exactly three `:DocChunk`
(no `:DocChunkHistory` twins) and one `:Observation` per name, matching the
first commit exactly.

`update.py::write_user_chat` does **not** need the same fix — a chat's
`doc_id` is a fresh `uuid4()` on every call, so it never re-commits under an
id retraction could have touched in the same transaction.

---

## Deferred, on purpose

| Deferred | To | Why |
|---|---|---|
| `docs archive` / `restore-from-archive` / `archived` | **Phase 5** | Unchanged scope — this phase only pulled `docs clean`/`purge`'s *deletion* forward, not archive's *addition*. |
| Full rewrite of `artmind-query`/`artmind-refine`/`artmind-ingestion-helper` | **Phase 7** | Already the plan's assignment. This phase patched only the specific command references that would hard-fail if followed literally (see "Skill-file drift caught in passing," above); the remaining ~13 `--asOf` mentions in `artmind-query` and the rest of both skills' surrounding narrative were not audited. |
| Property-hint audit across the 14 schemas Phase 3 didn't reach | **Phase 7** (per Phase 3's own notes) | Unrelated to this phase's scope; restated here only so the pointer isn't lost. |
| Entity-level supersession replacement | **Phase 6** (per Phase 3's own notes) | Unchanged; `docs retire`/a same-as group are still the answer today. |
| `consolidate.py` / `refine_graph.py`'s deeper pre-Phase-3 shape (fragment-language descriptions, `embedding = null` violating the never-null invariant, APOC destructive merges) | **Phase 6** | Both files got the mechanical `_id`/`_domain` rename (needed to keep them from silently breaking) and, in `consolidate.py`, the same `EXTRACTED_FROM`-direction fix applied everywhere else this phase — but their actual behaviour is explicitly Phase 6's ("rewrite in place as `projection synthesize`" / "`apply_merges` and `apoc.mergeNodes` delete"). Reworking either now would be doing that phase's job early. |

---

## Open questions for later phases

1. **`pattern10 --asOf`'s new meaning is a presence flag, not a date filter** —
   because chunks carry no valid-time of their own. If a later phase ever
   stamps chunk-level `valid_from`/`valid_to` (there is no such plan today),
   revisit whether `--asOf` should become a genuine point-in-time filter
   there instead.
2. **Skill-file staleness beyond what this phase patched.** `artmind-query`
   still has ~13 unaudited `--asOf` mentions; `artmind-refine`'s surrounding
   narrative around detect-supersession/refine-graph was not re-read start to
   finish. Both are Phase 7's job per the phase plan; flagging so the patches
   made here aren't mistaken for a full pass.
3. **The `_merge_relabeled` fix pattern (two labelled lookups instead of one
   MERGE) is now the correct idiom anywhere a label can legitimately hold two
   different values for the "same" logical node across its lifetime.** If a
   later phase introduces a fourth such pair (e.g. Phase 5's archive state),
   it will need the same treatment — a plain `MERGE` on the "active" label
   alone will silently duplicate under the same conditions this bug did.
