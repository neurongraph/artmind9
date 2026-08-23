# artmind

The knowledge-graph system: it ingests documents, extracts what they assert, stores
that in Neo4j and a structured store, and answers natural-language questions over
the result. This glossary is the shared vocabulary for the knowledge model — what
things *are*, not how they are built.

## Knowledge

**Fact**:
Something an artifact asserts. Not a node type — an invariant that holds in every
store: everything asserted carries provenance, an assertion status, and a valid-time
window, whether it lives in the graph, the text index, or the structured store.
_Avoid_: claim, statement, triple.

**Observation**:
What one chunk of one document version asserted about one thing — its class, its
description, its properties. The primary, immutable record; nothing ever merges or
overwrites it. Many observations may describe the same real-world thing.
_Avoid_: mention, entity version, extraction, assertion.

**Entity**:
One real-world thing, as artmind currently understands it — the aggregate of every
`latest` observation that refers to it. Derived and rebuildable, never authored.
Reading an Entity with its properties is meant to read like a concept wiki page.
_Avoid_: node, concept, canonical entity.

**Projection**:
The whole population of Entities — the current best picture, rebuilt from
observations. The only layer that is indexed and the only layer ordinary queries
touch.
_Avoid_: view, layer, materialized view, cache.

**Rebuild**:
Recomputing Entities from observations. Deterministic, needs no language model,
and runs as a step inside whatever operation dirtied the projection — never
something a person has to remember.
_Avoid_: consolidate, refresh, reproject, sync.

**Synthesize**:
Rewriting an Entity's description as one coherent passage drawn from all its
observations, rather than one observation's wording. The only step in the pipeline
that spends language-model budget without being asked to, so it is always explicit.
_Avoid_: consolidate, summarize, describe.

**Aggregate key**:
What decides which observations describe the same Entity: a normalized canonical
name, its entity class, and its domain. Purely computed — every judgment call lives
in a same-as group instead, so the key never depends on stored state.
_Avoid_: merge key, identity, cluster.

**Canonical name**:
The name an observation contributes to its aggregate key, after reconciling what the
chunk actually said against names already in use. The chunk's own wording is always
kept alongside it; canonicalisation never overwrites what a document said.
_Avoid_: normalized name, preferred name, display name.

**Same-as group**:
A curated set of aggregate keys a human has declared to denote one thing, with one
named as canonical. Groups rather than pairs, because pairs compose transitively and
transitive identity avalanches — two regulators fuse through a shared category.
_Avoid_: merge rule, alias link, equivalence class.

**Recurrent** / **Occurrent**:
Whether a class describes something that persists and changes (a rate, a policy, a
role) or a point event complete once it has happened (an incident, an audit finding).
Declared per class. It decides whether names may carry dates and values, whether
existing names are shown to the extractor, and whether two observations disagreeing
means the thing changed or the sources conflict.
_Avoid_: static/dynamic, stateful/stateless, entity/event.

**Conflict**:
Two observations disagreeing about the same property of the same Entity *at the same
instant*. Disagreement across disjoint valid-time windows is ordinary history, not a
conflict.
_Avoid_: contradiction, discrepancy, mismatch.

## Time and status

Two independent axes. Confusing them is the single most common modelling error here.

**Assertion time**:
Whether artmind still holds something to be its current record. Carried by
**status**, never by dates.
_Avoid_: system time, transaction time, ingestion time.

**Valid time**:
When something was true in the world. Carried by `valid_from` / `valid_to`, derived
from the document's own content, and untouched by re-ingestion.
_Avoid_: effective date, event time, as-of.

**Status**:
An assertion's standing on the assertion-time axis. Exactly three values:
`latest` (current record — in storage and in the index), `history` (retained in
storage, out of every index, reachable only by asking for it), `archive` (out of
storage entirely, moved to an external archive).
_Avoid_: state, deleted, superseded, active, tombstone.

**Retire**:
Moving a document and everything it asserted from `latest` to `history`. An
assertion-time act with no date semantics — a retired document's facts keep the
valid-time window they always had.
_Avoid_: supersede, delete, deprecate, close.

## Sources

**Vault**:
The externally-editable markdown tree a human owns, under git. The source of truth
for document content and for document identity.
_Avoid_: corpus, library, content root.

**Document**:
One logical thing in the vault, identified by an id artmind assigns and stores in
its frontmatter. Its name, path, and domain can all change without changing which
document it is.
_Avoid_: file, artifact, source.

**Document version**:
One distinct content state of a document. A version exists only when the body
changed; re-filing a document or editing its frontmatter creates none.
_Avoid_: revision, edition, update.

**DocChunk**:
A contiguous passage of one document version — the unit of text retrieval, and the
unit of change detection.
_Avoid_: block, passage, segment, fragment.

**Series**:
A declared group of documents that succeed one another in valid time — monthly rate
schedules, quarterly reports. Members do not supersede each other; each remains the
authoritative record of its own period. Declared by the author, never inferred from
filenames.
_Avoid_: version chain, title family, lineage.

## Organisation

**Domain**:
Which extraction schema governs a document, and the primary scope of every query.
Hierarchical, dot-separated (`banking.policy`).
_Avoid_: namespace, category, corpus, collection.

**Meta-schema**:
The contract every domain schema must satisfy — which properties every document and
every entity carries, and which names artmind reserves for itself. An underscore
prefix means artmind owns that property and extraction must never emit it.
_Avoid_: base schema, core schema, common fields.

**Structured store**:
Where facts from tabular artifacts (csv, xlsx) live — rows, not nodes. Carries the
same status and valid-time invariants as the graph.
_Avoid_: database, warehouse, SQL store.
