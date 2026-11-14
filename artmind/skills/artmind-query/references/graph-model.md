# Graph model — labels, relationships, properties

Read this before you read, judge or hand-edit any Cypher: `text2cypher` output, a `--dryRun`,
or a question about history labels. Every command in SKILL.md already hides this structure.

Two populations exist in the graph, and ordinary queries touch only one of them:

- **`:Observation`** — what one chunk of one document version asserted. Immutable,
  never merged or overwritten. Carries **no `:Entity` label and no class label** — it
  never appears in an entity query by accident, and it is provenance only, never the
  answer to "what is true now."
- **`:Entity`** — the projection: one node per real-world thing, rebuilt from
  observations, current by construction. This is the layer every pattern/entity command
  reads. Its id property is `_id` (leading underscore — Entity-only; every other label
  below uses plain `id`). `_domain`, unlike `_id`, is NOT Entity-specific: every label in
  this graph carries `_domain`, always underscore-prefixed, since it is artmind-computed
  (assigned by `--domain`/vault.yaml/frontmatter) rather than extracted from content —
  never write a query filtering on plain `.domain`, on any label.

Fixed relationships, identical across domains:

- `(:DocChunk)-[:PART_OF]->(:Document)` — chunk belongs to a document
- `(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)` — an observation's source chunk
- `(:Entity)-[:AGGREGATES]->(:Observation)` — an entity's current (`latest`) observations.
  To find which chunks/documents back an entity, go two hops:
  `(:Entity)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)` — an Entity
  never has a direct `EXTRACTED_FROM` edge.
- `(:Entity)-[:RELATES_TO {rel_type, observation_count, chunk_ids, doc_ids}]->(:Entity)` —
  **every** entity-to-entity relationship, whatever its real-world meaning (owns,
  regulates, part_of, ...), uses this one Neo4j relationship type. The meaning is
  `rel_type`, a **property**, not the type — filter on it, never assume a domain-specific
  type name. Its raw, chunk-scoped counterpart is
  `(:Observation)-[:ASSERTS_RELATION]->(:Observation)` — provenance only, not what you
  query for "what relationships does this entity have."

A document that replaces another carries `(:Document)-[:SUPERSEDES]->(:Document)` plus
`superseded_by` on the superseded side — see Adjudicate below for how to read it at
query time.

Key properties: `Document` (id, name, path, `_domain`, valid_from, valid_to, superseded_by),
`DocChunk` (id, name, doc_id, text, `_domain`), `UserChat` (id, raw_text, `_domain`, session_id,
created_by, created_at), `Observation` (id, name, canonical_name, entity_class, `_domain`,
doc_id, chunk_id, `_valid_from`/`_valid_to`, `_kind`), `Entity` (`_id`, name, entity_class,
`_domain`, description, type). Pattern/entity-command JSON output shows `_id`/`_domain`
verbatim — there is no re-aliasing back to `id`/`domain`.

`:DocumentHistory` / `:DocChunkHistory` / `:ObservationHistory` are the retired
counterpart of the base labels (same properties, mutually exclusive with it) — what a
retired document's contributions move to. Every command below reads the base label only
and is therefore current by construction; only `pattern10 --includeHistory` (see Retrieve)
reaches into history at all, and a document row's `supersedes` names the version it replaced.

For document/chunk questions use `PART_OF` (not `EXTRACTED_FROM`); `pattern10` does this
deterministically.
