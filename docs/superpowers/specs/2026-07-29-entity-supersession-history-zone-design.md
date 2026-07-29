# Entity-level supersession: a history zone beside the live graph

**Status:** Design — approved in brainstorming
**Date:** 2026-07-29
**Owner:** Surjit Das
**Extends:** `2026-07-04-cross-domain-conflicts-and-temporality.md` — completes Phase T2,
whose document-level supersession never reached `:Entity` nodes.

## 1. Purpose

Three defects were found while grounding §4 of `docs/CAPABILITIES.md` against source. One
is a silent-wrong-answer bug on the primary retrieval path; two are smaller. This document
designs all three fixes.

### 1.1 The primary defect

`apply_supersession()` (`temporal.py`) writes `valid_to` to `:Document` (always) and
`:DocChunk` (when `scope == "document"`). It never touches `:Entity` — for any scope.

But `asof_predicate(var)` is applied **per node type**. Chunk-oriented reads
(`vector-text`, `chunks`) filter on `DocChunk.valid_to` and so honour supersession;
entity-oriented reads — `pattern1`, `pattern2`, `pattern9`, `entity_listing`,
`graph_metadata` — filter on `Entity.valid_to`, which nothing ever sets. Entities
extracted from a superseded document therefore keep answering as current, indefinitely.

This is not a theoretical gap. The `artmind-query` skill instructs agents to *"Default to
`--asOf today` on every retrieval"* and names `pattern5`/`pattern10` as the only patterns
that ignore it. That documentation is wrong about `pattern1`/`pattern2`/`pattern9` once a
*document* — rather than an individual entity — has been superseded.

### 1.2 The secondary defects

- **Conflicts cannot be resolved.** `materialize()` only ever sets `status='open'`. No code
  path anywhere writes `resolved` or `dismissed`, and `text2cypher` is hard-blocked from
  writes. The `open|resolved|dismissed|all` filter on `query graph conflicts` selects over a
  state nothing can produce.
- **`normalize-time` and cross-domain conflict pairing do not roll up.** Both match
  `domain` with exact equality (self-documented `TODO(hierarchical-domains)` at
  `temporal.py:228` and `conflicts.py:62`), so a parent-scoped run silently touches nothing
  — unlike every retrieval path, which rolls up via `domain_predicate()`.

## 2. Prior art

The pattern chosen here is **selective versioning with an anchor and deltas**, which the
graph-versioning literature converges on for read patterns dominated by present-tense
queries. Sources consulted:

- **Entity-State model** ([neo4j-versioner-core](https://h-omer.github.io/neo4j-versioner-core/),
  [Neo4j data-modeling guidance](https://neo4j.com/docs/getting-started/data-modeling/versioning/)):
  split immutable identity from mutable state, linked by `CURRENT`/`HAS_STATE`/`PREVIOUS`.
  Its ESR extension versions relationships too, via a per-entity "R node".
- **Selective versioning** ([Neo4j temporal versioning walkthrough](https://medium.com/neo4j/keeping-track-of-graph-changes-using-temporal-versioning-3b0f854536fa)):
  version only the portions that require it; if current state dominates reads, structure
  the model so current state stays cheap to reach. Also warns that relationship properties
  are not indexed — which rules out putting validity on edges.
- **Bitemporality** ([Towards Probabilistic Bitemporal Knowledge Graphs](https://dl.acm.org/doi/fullHtml/10.1145/3184558.3191637)):
  valid time (when a fact held in the world) versus transaction time (when the system
  recorded it).
- **Diachronic legal norms** ([arXiv 2506.07853](https://arxiv.org/pdf/2506.07853)):
  supersession is the *replacement relationship* between norms; validity intervals are the
  *temporal span* a version applies. Keeping them distinct is load-bearing. Also introduces
  component-level granularity — versioning below the document, at article or clause level.

**Full Entity-State migration was considered and rejected.** artmind's read path is
overwhelmingly present-tense, and the sources are consistent that comprehensive versioning
raises query complexity substantially because every `MATCH` must account for versioned
elements. artmind would additionally pay it across every write path, the embedding model,
and refine-graph clustering.

### 2.1 Supersession and expiry are different, and artmind already has both

The legal-norms distinction resolves an open question: superseded and timed-out entities
are not the same thing, and artmind already produces both by separate routes.

| | How validity ends | Route | Successor? |
|---|---|---|---|
| **Superseded** | An event — a named authority replaced it | `apply_supersession()` writes `SUPERSEDES` + `valid_to` | Yes (`superseded_by`) |
| **Expired** | Intrinsically — its own window lapsed | `normalize_time()` from a schema mapping, e.g. `AGREEMENT: {valid_to: term_end_date}` | No |

Both land on the same `valid_to`, which is correct: `asof_predicate` should not care *why*
something stopped being current. The distinction stays recoverable — `valid_to` set with no
incoming `SUPERSEDES` edge means natural expiry. **This design preserves that property
rather than collapsing the two mechanisms**, and records the reason explicitly via
`closed_by` on history nodes.

Transaction/assertion time is explicitly **out of scope**. The requirement is "what was
true in January," not "what did the graph believe last Tuesday."

## 3. The four cases

Every decision below follows from these. Cases 3 and 4 are what the guards must not break.

| | Situation | Required outcome |
|---|---|---|
| 1 | The newer document asserts entity E with **different** property values | Live node takes the new values; prior values snapshot to the history zone |
| 2 | The newer document **drops** E entirely (the superseded document was its only source) | Live node retires — `valid_to` stamped. No snapshot: nothing changed |
| 3 | The newer document asserts E with **identical** values | Nothing |
| 4 | E is also sourced from an **unrelated live** document | Nothing — E remains asserted by current content |

Case 1 is the snapshot mechanism (§5). Case 2 is the retirement mechanism (§6). They are
independent and separately testable.

**Snapshots are created for overwrites only.** An entity from a superseded document whose
values nothing overwrites gets `valid_to` but no history node. Full document-version
reconstruction ("the entity set as v2 asserted it") is deliberately not a goal — it would
produce large numbers of snapshots identical to their predecessor.

## 4. The history zone

A new `:EntityVersion` label carrying **neither `:Entity` nor a class label**.

That single choice is what makes the zone free. Every existing consumer matches on
`:Entity` or on a class label — `pattern1`–`pattern9`, `entity_listing`, `entity-resolve`,
`embed_entities_backfill`, refine-graph clustering, `candidate_pairs`, `consolidate` — so
none can see history without being changed. The `entity_embedding` vector index is defined
on `:Entity(embedding)`, so snapshots cannot pollute semantic search either. Both
properties hold **by construction, not by remembering to filter.**

`entity_class` and `domain` are stored as **properties**, denormalized the way `DocChunk`
already denormalizes `domain` and `doc_id`, so history is queryable without traversal.

### 4.1 Node and edge shape

`(:EntityVersion)` properties:

| Property | Meaning |
|---|---|
| `id` | uuid, unique |
| `entity_id` | the live `:Entity`'s `id` — the anchor link |
| `name`, `entity_class`, `domain` | denormalized identity |
| *(changed keys)* | the prior values of exactly the properties that were overwritten |
| `valid_from` | when this state became current (the live entity's prior `valid_from`, may be null) |
| `valid_to` | the supersession's effective date |
| `closed_by` | `'supersession'` — reserved for `'expiry'` later (§2.1) |
| `superseded_by_doc` | the newer document's id — why this state closed |
| `snapshot_at` | when the snapshot was written |

Edge: `(:Entity)-[:PRIOR_STATE]->(:EntityVersion)`.

`PRIOR_STATE` is added to `RESERVED_REL_TYPES`. The existing comment's argument for
`SUPERSEDES`/`EXTRACTED_FROM` applies unchanged: LLM extraction must not be able to mint
history edges carrying no provenance.

**No `PREVIOUS` chain** between snapshots, unlike the versioner-core model. Ordering by
`valid_from` satisfies point-in-time reads; the chain is deferred under YAGNI.

### 4.2 Store setup

Added to `setup.py`, following its existing idempotent conventions:

```
CREATE CONSTRAINT entity_version_id  FOR (n:EntityVersion) REQUIRE n.id IS UNIQUE
CREATE INDEX entity_version_entity   FOR (n:EntityVersion) ON (n.entity_id)
CREATE INDEX entity_version_valid_to FOR (n:EntityVersion) ON (n.valid_to)
CREATE INDEX entity_version_domain   FOR (n:EntityVersion) ON (n.domain)
```

The `entity_valid_to` index already exists, so §6's retirement filtering is index-backed.

## 5. Write path — snapshots (case 1)

### 5.1 Capture must precede the write

By the time `_reassert_superseding_properties()` runs, `_upsert_entity`'s accretive merge
has already concatenated values: the live node holds `"£500 | £2,000"`, not the clean prior
value. Capturing there would record the blob.

Capture therefore happens **before** `write_to_graph()`:

```
commit_to_graph(doc_kg_dir, domain):
    prior = capture_prior_values(doc_kg_dir, domain)   # NEW — read-only
    ok = write_to_graph(doc_kg_dir)                    # accretive merge happens here
    if not ok: return False
    normalize_ingested_document(...)                   # temporal hook (unchanged)
    sup_report = detect_supersession(domain, only_doc_name=...)
    if sup_report.applied:
        _reassert_superseding_properties(..., prior, sup_report)   # compare → snapshot → overwrite
```

`capture_prior_values()` reads, for each entity key in `entities.json` that has properties
in `properties.json`, the current values of exactly the keys this document will assert,
plus `valid_from` and the live node's `id` (needed as the snapshot's `entity_id` anchor).
Snapshots are written only for keys whose value actually differs — case 3 produces nothing.

An entity the newer document introduces for the first time has no pre-write node, so
capture returns nothing for it and no snapshot is written. Correct by construction: there
is no prior state to preserve.

**Cost:** one extra read query per commit, including commits where supersession never
fires. Negligible against extraction's LLM calls, and preferable to recording concatenated
blobs.

**Semantics:** a snapshot records *the graph's then-current state*, not "what the older
document claimed." This is the right answer for point-in-time questions and is honest about
the multi-document case, where the prior value may legitimately be a blend of several
still-live sources.

### 5.2 Scope of the snapshot

`_reassert_superseding_properties` already overwrites only the domain-specific properties
from `properties.json` — `name`/`description`/`aliases`/`context` stay accretive, which is
consolidation's job. Snapshots inherit exactly that scope. Descriptions are already
non-destructively preserved in `description_raw` by `consolidate.py`.

## 6. Write path — retirement (case 2)

A new `_retire_orphaned_entities(session, older_doc_id, effective)` in `temporal.py`,
called from `apply_supersession()` when `scope == "document"` — the same gate the existing
chunk stamp uses:

```cypher
MATCH (e:Entity)-[:EXTRACTED_FROM]->(c:DocChunk)
WITH e, collect(DISTINCT c.doc_id) AS docIds
WHERE size(docIds) = 1 AND docIds[0] = $olderDocId
SET e.valid_to      = coalesce(e.valid_to, $effective),
    e.superseded_by = $newerDocId,
    e.status        = 'superseded'
```

The single-source condition is what makes cases 3 and 4 safe. Ordering matters and already
holds: `apply_supersession` runs after `write_to_graph`, so the newer document's
`EXTRACTED_FROM` edges exist — an entity re-asserted by the newer document has two
`doc_id`s and is correctly left alone.

Placing this in `apply_supersession()` means all three supersession routes — manual CLI,
notice scan, conflict adjudicator — inherit it uniformly, matching the convergence-point
style `commit_to_graph` already uses. `coalesce` keeps it idempotent.

### 6.1 Blast radius

Smaller than a split-node model would require. Because there is only ever one `:Entity` per
identity, refine-graph cannot re-merge versions and `candidate_pairs` cannot pair them.
Skipping retired entities in `consolidate`/`refine` is an **optional cost optimization**,
not a correctness requirement, and is out of scope here.

## 7. Read path

**New:** `artmind query graph entity-versions --entityId <id> [--asOf <date>]`

- without `--asOf` — the full version history, ordered by `valid_from`
- with `--asOf` — the state current on that date: the covering snapshot, else the live node

**Extended:** `query graph timeline` gains prior-state events. It is already the "history of
this entity" command and is where a user looks first.

**Deliberately unchanged:** `pattern2 --asOf` does **not** transparently return historical
values. That would silently change the meaning of the most-used pattern; `pattern10`'s
`asOf_ignored` flag sets the precedent that this codebase prefers explicit over
silently-partial. Deferred until `entity-versions` proves the model.

**Integration point:** `text2cypher.py`'s hardcoded schema prompt must learn about
`:EntityVersion`, or generated Cypher will neither know it exists nor know to exclude it.
`graph_metadata`'s unlabelled `MATCH (n)` will begin reporting the new label — expected.

## 8. `--scope section|clause` fails loudly

Current behaviour is worse than a no-op: `apply_supersession` retires the whole Document
regardless of scope but gates only the chunk stamp on `scope == "document"`, so
`--scope clause` leaves a retired document with live chunks — an inconsistent state with no
way to reach the promised behaviour.

Real sub-document scoping needs graph units that do not exist (component-level granularity,
per §2). **`ingest supersede` will reject non-`document` scopes** with a "not yet
supported" error. Non-breaking for the only value that currently works, and honest about
the gap. The enum is retained so the eventual implementation needs no CLI change.

## 9. Fix #2 — conflict resolution

`artmind ingest resolve-conflict CONFLICT_ID --status resolved|dismissed [--reason TEXT]`,
backed by `resolve_conflict()` in `conflicts.py`, routed into the CLI's Refinement group
(`test_cli_guide.py` enforces routing).

Sets `status`, `resolved_at`, `resolution_reason` on the `Conflict` node. **Explicit only —
no automatic resolution**, per the field guide's principle that conflicts are never
silently resolved. Errors clearly when the id does not resolve, including the orphaned-edge
case: a `CONFLICTS_WITH` edge whose `Conflict` node was deleted cannot carry status, and
`list_conflicts` reports such rows as `open` via `coalesce`.

## 10. Fix #3 — hierarchical domain rollup

`expand_domain_family(domain) -> list[str]` in `graph_query.py`, beside `normalize_domains`
and `domain_predicate`. Children are derived **from the graph**
(`DISTINCT n.domain STARTS WITH domain + '.'`), not the schema directory — no filesystem
dependency, no `cli` import, and it returns exactly the domains holding data.

Applied at both `TODO(hierarchical-domains)` sites:

- **`normalize_time`** loops the expanded list so each child's own schema loads. Return
  shape stays flat with summed counts, plus an additive `domains_processed` key, so
  `refine_pipeline`'s report and `summarize_gates.py` keep working.
- **`candidate_pairs`** expands before ANN pairing, which makes
  `detect-conflicts --domain banking` mean "cross-child conflicts within the family" —
  the intent the TODO states. `detect_conflicts` records both the requested and expanded
  lists in its report.

**Out of scope:** `refine_graph`'s entity fetch and `detect_supersession`'s document scan
have the same exact-match gap but no TODO. Parent rollup in refine-graph would let
cross-child merges bypass the cross-domain guard, which needs its own design.

## 11. Files

**New:** `artmind/entity_history.py` — prior-value capture, snapshot write, version query.
Kept out of `ingest.py`, which is already ~1650 lines.

**Modified:** `temporal.py` (retirement, scope rejection) · `ingest.py` (commit wiring,
`RESERVED_REL_TYPES`) · `graph_query.py` (family expansion, version query) ·
`conflicts.py` (resolve, domain expansion) · `cli.py` (two commands, scope validation) ·
`setup.py` (constraint + indexes) · `text2cypher.py` (schema prompt) ·
`skills/artmind-refine/SKILL.md` · `docs/CAPABILITIES.md` §4.

## 12. Testing

Hermetic, following the fake-session and `monkeypatch` style of
`test/test_ingest_hooks.py` and `test/test_supersession.py`.

Each of §3's four cases gets an explicit test. Cases 3 and 4 are the likeliest regressions:

1. Differing values → snapshot written with prior values, live node holds new values
2. Dropped entity → `valid_to` stamped, no snapshot
3. Identical values → **no** snapshot, no retirement
4. Entity with a second live source → **no** retirement despite the superseded source

Additionally: capture-precedes-write ordering (a snapshot must never contain a `" | "`
concatenation produced by the accretive merge); `PRIOR_STATE` rejected as an
LLM-extractable relationship type; `apply_supersession` idempotent under re-run;
`entity-versions --asOf` selecting the covering snapshot and falling back to the live node;
`resolve-conflict` erroring on an unknown id; `expand_domain_family` returning parent plus
children and leaving a childless domain unchanged.

## 13. Out of scope

- Full Entity-State/ESR migration and relationship-level versioning (§2)
- Transaction/assertion time (§2.1)
- Sub-document supersession scopes (§8)
- `pattern2 --asOf` returning historical values (§7)
- Rollup for `refine_graph` / `detect_supersession` (§10)
- Skipping retired entities in consolidate/refine (§6.1)
- Snapshots for natural expiry — `closed_by` reserves the vocabulary (§2.1)
