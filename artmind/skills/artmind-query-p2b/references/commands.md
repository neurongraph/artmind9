# artmind commands

Generated from the CLI by `just dev-skill-commands`; do not edit. Every `query` command and the read-only
`db` commands, with every option. Each also takes `--domain` (repeatable) and `--compact` where listed.

## artmind db bridge

Read the structured↔graph bridge: class scope, bridge columns, grain.

`artmind db bridge [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` | TEXT |  | Domain(s) to scope (repeatable; comma-splittable). Omit for all. |
| `--entityClass` | TEXT |  | Only tables whose class scope contains this class. |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind db list

List registered structured tables, optionally domain-scoped.

`artmind db list [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` | TEXT |  | Domain(s) to scope (repeatable; comma-splittable). Omit for all. |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind db schema

Show columns/types (+ profiles/mappings once populated) — the context an LLM needs to write SQL.

`artmind db schema [TABLE] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `TABLE` | TEXT |  |  |
| `--domain` | TEXT |  | Domain(s) to scope (repeatable; comma-splittable). Omit for all. |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind db review

List every table classification still awaiting human adjudication.

`artmind db review [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` | TEXT |  | Domain(s) to scope (repeatable; comma-splittable). Omit for all. |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind db sql

Run raw read-only SQL against the structured store — no LLM (the independent-query guarantee).

`artmind db sql SQL [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `SQL` (required) | TEXT |  |  |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind db timeline

Point-in-time query over a temporal (SCD-2) table's captured history.

`artmind db timeline TABLE [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `TABLE` (required) | TEXT |  |  |
| `--domain` | TEXT |  | Domain(s) to scope table resolution (repeatable; comma-splittable). |
| `--asOf` | TEXT |  | Only rows in force by this date (_valid_from <= asOf; _valid_to is rarely set, so this is a floor, not a point-in-time snapshot). ISO date; omit for the currently-open rows. |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph metadata

Return graph schema metadata (labels, properties, relationship types).

`artmind query graph metadata [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph structural-metadata

Return focused structural metadata (Document, DocChunk, UserChat, Entity counts and relationships).

`artmind query graph structural-metadata [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--maxNames` | INTEGER | `50` | Max Document names returned (sorted first); names_total carries the full count |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph entity-listing

Return entity names grouped by label/type.

`artmind query graph entity-listing [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--nameFilter` | TEXT |  | Fuzzy match entity names (case-insensitive substring) |
| `--countAll` | FLAG |  | Include total unfiltered entity count in output |
| `--maxNames` | INTEGER | `50` | Max names returned per type group (sorted first); names_total carries the full count |
| `--all` | FLAG |  | Every name in every type group (ignores --maxNames); `complete` says whether the list is whole |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph filing-listing

List Documents filtered by filing taxonomy (project, area, tags, domain).

`artmind query graph filing-listing [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` | TEXT |  | Domain to query (repeatable; comma-splittable). Optional. |
| `--project` | TEXT |  | Filter by project (ADR 0010 filing metadata) |
| `--area` | TEXT |  | Filter by area (ADR 0010 filing metadata) |
| `--tag` | TEXT |  | Filter by tag (repeatable; matches ANY listed tag) |
| `--asOf` | TEXT |  | Only docs in force by this date (valid_from <= asOf; valid_to is rarely set, so this is a floor, not a point-in-time snapshot). ISO date or 'today'; docs without valid-time always shown |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern1

List entities of a class. `--shape brief`: id, name, class, one-line description per row.

`artmind query graph pattern1 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityClass` (required) | TEXT |  | Entity class label (e.g. CHARACTER, LOCATION) |
| `--limit` | INTEGER | `200` | Max results |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern2

Info on one or more named entities. `--shape brief`: id, name, class, one-line description, evidence ids.

`artmind query graph pattern2 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityNameList` | TEXT |  | Entity name (repeatable, substring match) |
| `--entityIdList` | TEXT |  | Exact entity id (repeatable; comma-splittable; overrides name list) |
| `--maxDocSources` | INTEGER | `20` | Max doc_sources returned per entity; doc_sources_total carries the full count |
| `--maxSourceDocuments` | INTEGER | `10` | Max source_documents returned per entity; source_documents_total carries the full count |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern3

Entity + lightweight relationship summary.

`artmind query graph pattern3 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityNameList` | TEXT |  | Entity name (repeatable, substring match) |
| `--entityIdList` | TEXT |  | Exact entity id (repeatable; comma-splittable; overrides name list) |
| `--maxConnections` | INTEGER | `25` | Max connections returned per entity; connections_total carries the full count |
| `--maxDocSources` | INTEGER | `20` | Max doc_sources returned per entity; doc_sources_total carries the full count |
| `--maxSourceDocuments` | INTEGER | `10` | Max source_documents returned per entity; source_documents_total carries the full count |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern4

Entity + full neighborhood.

`artmind query graph pattern4 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityClass` (required) | TEXT |  | Entity class label |
| `--entityName` | TEXT |  | Entity name (substring match) |
| `--entityId` | TEXT |  | Exact entity id (overrides --entityName) |
| `--maxConnections` | INTEGER | `25` | Max connections returned; connections_total carries the full count |
| `--maxListItems` | INTEGER | `10` | Max items kept (newest first) of each list property on a connected entity |
| `--maxDocSources` | INTEGER | `20` | Max doc_sources returned; doc_sources_total carries the full count |
| `--maxSourceDocuments` | INTEGER | `10` | Max source_documents returned; source_documents_total carries the full count |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern5

Paths between two entities (shortest or all within depth 5).

`artmind query graph pattern5 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityClass1` (required) | TEXT |  | Class of first entity |
| `--entityClass2` (required) | TEXT |  | Class of second entity |
| `--entityName1` | TEXT |  | Name of first entity |
| `--entityName2` | TEXT |  | Name of second entity |
| `--entityId1` | TEXT |  | Exact id of first entity (overrides --entityName1) |
| `--entityId2` | TEXT |  | Exact id of second entity (overrides --entityName2) |
| `--mode` | ENUM | `shortest` | Path mode |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern6

Direct relationships between two entities.

`artmind query graph pattern6 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityName1` | TEXT |  | Name of first entity |
| `--entityName2` | TEXT |  | Name of second entity |
| `--entityId1` | TEXT |  | Exact id of first entity (overrides --entityName1) |
| `--entityId2` | TEXT |  | Exact id of second entity (overrides --entityName2) |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern7

Search entities by name or description fragment. `--shape brief`: id, name, class, one-line description, documents.

`artmind query graph pattern7 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--searchTerm` (required) | TEXT |  | Substring match on name or description |
| `--limit` | INTRANGE | `10` | Max results |
| `--all` | FLAG |  | Return up to 500 rows (the --limit maximum); `complete` says whether that is every match |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern8

Entities of class X connected to entity Y. `--shape brief`: id, name, class, one-line description, rel_type per row.

`artmind query graph pattern8 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityClass` (required) | TEXT |  | Class of entities to return |
| `--entityName` | TEXT |  | Name of the connected entity |
| `--entityId` | TEXT |  | Exact id of the connected entity (overrides --entityName) |
| `--limit` | INTEGER | `50` | Max rows; rows_total carries the full count |
| `--offset` | INTEGER | `0` | Skip this many rows before --limit; page past a truncated result with offset += limit |
| `--all` | FLAG |  | Page through every row (steps of --limit, at most 2000); `complete` says whether all arrived |
| `--via` | ENUM | `links` | links: entities with a relation to Y. mentions: entities named in the same notes as Y |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern9

Top-N entities of a class by connection count.

`artmind query graph pattern9 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityClass` (required) | TEXT |  | Entity class label |
| `--topN` | INTEGER | `5` | Number of top entities |
| `--degreeMode` | ENUM | `relations` | Degree to rank by: entity-entity relationships, source mentions, or all edges |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph pattern10

Retrieve all text chunks for a named document (current only, or with history).

`artmind query graph pattern10 [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--documentName` (required) | TEXT |  | Document name (substring match) |
| `--includeHistory` | FLAG |  | Also reach retired/superseded documents and chunks (:DocumentHistory / :DocChunkHistory). Omit for current content only |
| `--asOf` | TEXT |  | Alias of --includeHistory kept for old callers: any value turns history on. It is NOT a date filter here (chunks carry no date of their own) |
| `--limit` | INTEGER | `300` | Max chunks; rows_total carries the full count |
| `--offset` | INTEGER | `0` | Skip this many chunks (ordered by chunk id) before --limit; page past a truncated result with offset += limit |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph text2cypher

Generate and execute a Cypher query from a natural-language question (LLM-powered).

`artmind query graph text2cypher QUESTION [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` (required) | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--compact` | FLAG |  | Emit compact JSON |
| `--dryRun` | FLAG |  | Show generated Cypher without executing it |
| `--maxRows` | INTEGER | `200` | Max rows returned; rows_total carries the full count |

```bash
artmind query graph text2cypher "Which entities are connected to Acme Corp?" --domain banking
```

## artmind query graph conflicts

List materialized Conflict nodes scoped to the given domains.

`artmind query graph conflicts [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | Domain(s) (repeatable) |
| `--entityId` | TEXT |  | Filter to conflicts touching this entity id (repeatable; comma-splittable) |
| `--entityName` | TEXT |  | Filter to conflicts touching an entity whose name contains this |
| `--status` | ENUM | `open` |  |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph timeline

Every entity of a `kind: occurrent` class in these domains, ordered by valid_from.

`artmind query graph timeline [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | Domain(s) |
| `--from` | TEXT |  | ISO date: only entities with valid_from on or after this |
| `--to` | TEXT |  | ISO date: only entities with valid_from on or before this |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query graph hierarchy

Bounded transitive walk (descendants or ancestors) along chosen rel types.

`artmind query graph hierarchy [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityId` | TEXT |  | Exact id of the root entity |
| `--entityName` | TEXT |  | Name of the root entity (must resolve to exactly one; otherwise errors listing candidates) |
| `--entityClass` | TEXT |  | Narrow --entityName resolution to this class |
| `--childOf` | TEXT |  | Rel type(s) where (child)-[rel]->(parent) (repeatable; comma-splittable) |
| `--parentOf` | TEXT |  | Rel type(s) where (parent)-[rel]->(child) (repeatable; comma-splittable) |
| `--direction` | ENUM | `down` | down = descendants, up = ancestors (chain of command) |
| `--maxDepth` | INTEGER | `5` | 1-10 |
| `--limit` | INTEGER | `1500` | Total node budget, max 2000 |
| `--nodeClass` | TEXT |  | Classes the walk may enter (repeatable; comma-splittable). Default: any :Entity in the selected domains |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query views list

The view catalogue: name, summary, example questions, parameters. Invalid views are reported, not hidden.

`artmind query views list [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` | TEXT |  | Only views for this domain family (repeatable; comma-splittable). Default: all views |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query views show

The full view.yaml plus the view's SKILL.md guidance (read it before running the view).

`artmind query views show NAME [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `NAME` (required) | TEXT |  |  |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query views run

Run a saved view. status is ok \| needs_disambiguation (ask the user, rerun with @<_id>) \| no_match.

`artmind query views run NAME [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `NAME` (required) | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--param` | TEXT |  | name=value (repeatable). Entity params take a name, or @<_id> to skip resolution; list params are comma-separated |
| `--render` | ENUM |  | Also return `rendered`: deterministic markdown per the view's presentation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query insights list

Top findings by score, with captions and the subjects' _ids for grounding.

`artmind query insights list [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | The domain (one) |
| `--detector` | TEXT |  | Only this detector (e.g. outcome_lift, transitions) |
| `--kind` | ENUM |  |  |
| `--new` | FLAG |  | Only what changed since the previous run |
| `--limit` | INTRANGE | `25` |  |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query insights show

One finding with its full stats and card (the evidence behind a caption).

`artmind query insights show KEY [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `KEY` (required) | TEXT |  |  |
| `--domain` (required) | TEXT |  | The domain (one) |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query topics plan

One topic plan: plan.yaml, its SKILL.md, its `freshness` and, per cell, `matching` at build vs now.

`artmind query topics plan [SLUG] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `SLUG` | TEXT |  |  |
| `--list` | FLAG |  | Every plan in scope (slug, topic, subjects, freshness) |
| `--domain` | TEXT |  | --list: only plans over this domain (repeatable; comma-splittable) |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query topics run

Run a topic plan's surveys live: one per cell, each row naming its cell (no LLM).

`artmind query topics run SLUG [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `SLUG` (required) | TEXT |  |  |
| `--subject` | INTEGER |  | Only this subject (1-based, as `plan` lists them) |
| `--cell` | TEXT |  | Only cells of this period (e.g. 2023) or domain, or one cell label |
| `--shape` | ENUM | `full` | brief: one passage per document, cut; full: up to 3 passages and the summary |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query orient

What the corpus is and how it is organised: facts, not a plan.

`artmind query orient [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` | TEXT |  | Open these domains (repeatable; comma-splittable; a family root opens its children) |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query domains-overview

Per-domain routing summary: doc names/counts, entity counts, top classes.

`artmind query domains-overview [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query catalogue

What the system knows about a domain: classes, relations, documents, names, stores, time, health.

`artmind query catalogue [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | Domain (repeatable; comma-splittable; rolls up sub-domains) |
| `--part` | TEXT |  | Only this part (repeatable): classes, relations, shapes, types, documents, versions, names, stores, time, health |
| `--names` | FLAG |  | List the name index (otherwise only its size) |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query assets

What a domain already answers: saved views, topic pages, insight findings and summary coverage.

`artmind query assets [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` | TEXT |  | Domain (repeatable; comma-splittable). Omit for every domain with a schema |
| `--kind` | TEXT |  | Only this kind (repeatable): view, topic, insight, summary_set |
| `--like` | TEXT |  | Rank by meaning against this text; each asset carries a `score` |
| `--stale` | FLAG |  | Only stale assets |
| `--limit` | INTRANGE | `50` |  |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query vector-text

Hybrid search over passages: vector + keyword (RRF), grouped into parent chunks.

`artmind query vector-text QUESTION [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` (required) | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--topK` | INTEGER | `5` | Results to return (1-50) |
| `--context` | ENUM | `chunk` | Return each hit's parent chunk text, or only the matching passage |
| `--asOf` | TEXT |  | Only rows in force by this date (valid_from <= asOf; valid_to is rarely set, so this is a floor, not a point-in-time snapshot). ISO date or 'today'; nodes without valid-time always shown |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--compact` | FLAG |  | Emit compact JSON |

```bash
artmind query vector-text "interest rate changes" --domain banking --topK 10
```

## artmind query survey

Documents for a question that spans documents: document summaries, chunk summaries and passages, fused.

`artmind query survey QUESTION [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` (required) | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--topDocs` | INTRANGE |  | Documents to return (default 50 for --shape brief, 10 for full; overridden by ARTMIND_SURVEY_TOP_DOCS) |
| `--passages` | INTRANGE | `1` | Best passages shown per document |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--passageDocs` | INTRANGE | `3` | --shape brief: documents that get their best passage at least (plus any within 0.03 relevance of the top) |
| `--gistDocs` | INTRANGE | `10` | --shape brief: documents that get a gist row at least (more while ARTMIND_OUTPUT_BUDGET allows) |
| `--expand` | TEXT |  | Passage rows for these document ids (repeatable; comma-splittable) under the same question, no re-ranking |
| `--by` | ENUM |  | One group per period of the document date (`periods`, with keyword `hits` over the whole index); ignores --topDocs, max 50 documents |
| `--perPeriod` | INTRANGE | `2` | --by: best documents kept per period |
| `--from` | TEXT |  | YYYY-MM-DD: only documents dated on or after this (all legs, before fusion) |
| `--to` | TEXT |  | YYYY-MM-DD: only documents dated on or before this |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query page

Show more rows of a result that ARTMIND_OUTPUT_BUDGET cut (its `handle` and `next` say how).

`artmind query page HANDLE [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `HANDLE` (required) | TEXT |  |  |
| `--from` | INTRANGE | `0` | First row to show (0-based) |
| `--rows` | INTRANGE |  | Rows to show (default: as many as fit ARTMIND_OUTPUT_BUDGET, else all remaining) |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query entity-resolve

Resolve a name fragment or description to canonical graph entities (exact name first, then fulltext + vector via RRF).

`artmind query entity-resolve REFERENCE [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `REFERENCE` (required) | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--topK` | INTEGER | `5` |  |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query chunks

Fetch chunk text by exact id (the doc_sources / evidence ids other commands return).

`artmind query chunks [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` | TEXT |  | Scope the lookup to this domain (repeatable; comma-splittable). Optional: without it the ids are looked up in every domain and the output's domains come from the chunks |
| `--idList` (required) | TEXT |  | Exact chunk id (repeatable; comma-splittable) |
| `--expand` | INTEGER | `0` | Also return up to N adjacent chunks per hit (same document) |
| `--asOf` | TEXT |  | Only rows in force by this date (valid_from <= asOf; valid_to is rarely set, so this is a floor, not a point-in-time snapshot). ISO date or 'today'; nodes without valid-time always shown |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query entity-context

Entity properties + one-hop relationships + source chunk text in one call.

`artmind query entity-context [QUESTION] [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityId` (required) | TEXT |  | Exact entity id (from entity-resolve) |
| `--includeChunks` | INTEGER | `5` | Source chunks returned with full text (rest as ids) |
| `--query` | TEXT |  | The question being answered: ranks chunks and connections by similarity to it |
| `--relType` | TEXT |  | Only return connections of this rel_type (repeatable; comma-splittable). connection_summary still covers all |
| `--maxConnections` | INTEGER | `25` | Connections returned (connection_summary counts every edge) |
| `--maxListItems` | INTEGER | `10` | Newest items kept per list property (full lengths in truncated_properties) |
| `--shape` | ENUM | `full` | brief: short rows (id, title, domain, date, status, extractive gist) for an agent; full: every key. Changes row content; independent of --compact, which only changes serialisation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query entity-history

Every observation behind one entity, ordered by fact-level valid time.

`artmind query entity-history [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--entityId` | TEXT |  | Exact entity id (from entity-resolve / entity-context); finds nothing once the entity has no current observations left |
| `--key` | TEXT |  | Aggregate key name\|CLASS\|domain (as entity-resolve and history rows show it); works whether or not the entity still exists |
| `--name` | TEXT |  | Entity name, matched against the key's normalized name part and the extracted name; works whether or not the entity still exists |
| `--entityClass` | TEXT |  | With --name: only observations of this entity class |
| `--asOf` | TEXT |  | Only facts in force by this date (_valid_from <= asOf; _valid_to is rarely set, so this is a floor, not a point-in-time snapshot). ISO date or 'today' |
| `--property` | TEXT |  | Narrow to one property's value at each point instead of the full observation |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query document-history

What a document's ingested versions asserted: current vs history observations.

`artmind query document-history [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--documentName` (required) | TEXT |  | Document name (case-insensitive; a missing extension is matched when exactly one document fits), vault path, or document id |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query text2sql

Natural language to read-only DuckDB SQL against the structured store, then execute it.

`artmind query text2sql QUESTION [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `QUESTION` (required) | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain(s) to scope (repeatable; comma-splittable) |
| `--asOf` | TEXT |  | Only rows in force by this date (valid_from <= asOf; valid_to is rarely set, so this is a floor, not a point-in-time snapshot). ISO date or 'today'; rows without temporal history always shown |
| `--dryRun` | FLAG |  | Generate SQL without executing it |
| `--compact` | FLAG |  | Emit compact JSON |

## artmind query resolve-key

Resolve a free-text value to a canonical column value and/or graph entity.

`artmind query resolve-key PHRASE [OPTIONS]`

| Flag | Type | Default | Help |
|---|---|---|---|
| `PHRASE` (required) | TEXT |  |  |
| `--domain` (required) | TEXT |  | Domain to query (repeatable; comma-splittable) |
| `--column` | TEXT |  | Scope matching to this column's profiled values (omit to resolve against the graph only) |
| `--table` | TEXT |  | Table containing --column, if ambiguous across tables (optional) |
| `--topK` | INTEGER | `5` |  |
| `--compact` | FLAG |  | Emit compact JSON |
