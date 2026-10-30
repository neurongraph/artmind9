# Chunks-only domains (`graph_extraction: false`)

Status: design, 2026-10-30.

## Goal

Let an operator ingest a domain's documents and stop at `:DocChunk` nodes with
embeddings: no entity, property or relationship extraction. The domain is then
a vector + full-text search index (`artmind query vector-text`).

## Non-goals

- A per-run CLI flag. The mode is a property of the domain, not of a run.
- Inheritance of the flag by dotted child domains.
- Changing how graph domains behave (`graph_extraction` defaults to `true`).
- Any change to `ingest table2graph` or structured tables. The flag governs
  document ingestion only; `table2graph` neither reads it nor changes. A
  chunks-only domain is not meant to receive table projections.

## The flag

`graph_extraction` is a top-level boolean in `<domain>_schema.yaml`. Absent or
`true`: today's behaviour. `false`: chunks-only.

```yaml
name: research_papers
description: Papers kept for semantic search only.
graph_extraction: false
```

- A chunks-only schema needs only `name`, `description` and the flag;
  `entity_types` is optional.
- A dotted child (`banking.policy`) does **not** inherit the flag from its
  parent: each domain's file states it. (`temporal.load_schema` merges only
  `temporal:` and `property_aliases:`; it stays that way.)
- `schema_validate.graph_extraction_enabled(schema) -> bool` is the one reader.
  Every site below calls it.
- `domains validate`: a non-boolean value is an ERROR. `false` together with
  `entity_types`, `document_entity` or `property_aliases` is a WARN (ignored).

## Documents: `extract_kg`

`extract_kg` branches once, after the schema is loaded, on
`graph_extraction_enabled(schema)`. Under `false`:

Skipped: name and property vocabulary retrieval, the document summary
(`_summarise_document`), the entities / properties / relationships LLM steps,
`canonicalize_document`, the document-entity fold and date stamping.

Kept, unchanged: identity and versioning, `document_dates`, chunk cache and
re-split check, embeddings and the embedding sidecar, filing metadata and
chunk offsets, the atomic `write_staging`, and `_commit_document_tx`.

Per chunk, the three steps are recorded `skipped` in `kg_chunk_status`
(`_incomplete_chunks` already treats only `failed`/`pending` as incomplete).
`chunks.json` is written normally; `observations.json` and
`relationships.json` are `[]`. No LLM client is constructed or called.

The `--reextract` empty-result guard (spec 2026-10-18 §3.2) is bypassed:
empty is the intended result. Everything else in `--reextract` works: the
cache is stashed and rebuilt, embeddings come from the sidecar.

### Switching a domain to chunks-only

Setting `graph_extraction: false` on a domain that already has entities and
re-ingesting (`--reextract` for unchanged notes) commits a new version whose
observations are empty. `_retract_prior_version` demotes the previous
observations to `:ObservationHistory`; the projection's zero-latest-observation
rule then removes the entities. Nothing new is needed. Switching back to
`true` and re-ingesting with `--reextract` re-extracts; embeddings are reused.

## Audit

`ingest empty-extractions` skips staging folders whose domain is chunks-only
(empty observations are correct there). Domain lookup: the folder's domain
schema via `load_schema` (no Neo4j).

## Query side

- `graph_query.domains_overview`: each entry gains `"graph_extraction": false`
  for chunks-only domains (read from the local schema, so a query-only host
  without schemas simply omits it). `stores` lists `"chunks"` instead of
  `"graph"` for them.
- `query_plan.planner.plan`: when every selected domain is chunks-only, the
  route is forced to `search` (`vector-text`), view matching is skipped, and a
  warning says why. Mixed scope keeps normal routing.
- `artmind-query` skill: a domain with `graph_extraction: false` goes straight
  to `vector-text`; graph patterns, views and insights are not tried.
- Views, insights and `text2cypher` against such a domain return no rows; no
  special handling beyond the guidance above.

## Docs

- CLAUDE.md: a repo-layout row for the mode.
- `artmind-create-schema` skill: the flag, with the example above.
- `artmind-ingestion-helper` skill: how to tell a chunks-only domain and how to
  switch it.
- Command help for `domains validate`.

## Tests (hermetic, in `test/`, synthetic text)

1. `graph_extraction_enabled`: absent/true/false; non-boolean rejected;
   dotted child does not inherit; validate WARN for ignored keys.
2. `extract_kg` under `false` with a mocked LLM that raises if called: zero
   LLM calls, embeddings computed, staged `observations.json == []`,
   chunk rows `skipped`, no summary sidecar.
3. `--reextract` over a previously extracted staging folder in a now
   chunks-only domain is accepted (guard bypassed) and stages empty
   observations; `_commit_document_tx` is asserted to receive the empty list
   and to demote prior observations (assert the Cypher/params sent, not counts).
4. `empty_extractions` omits chunks-only folders.
5. `domains_overview` entry and `stores`; `plan` forces `search` for a
   chunks-only selection and keeps routing for a mixed one.

## Verification beyond unit tests

End to end against a live Neo4j with `ARTMIND_NO_PROXY=1`: ingest a few notes
into a `graph_extraction: false` domain, confirm `:DocChunk` nodes with
vectors and no `:Entity`, run `query vector-text`; then flip the flag on a
copy and confirm entities are retired by `--reextract`.
