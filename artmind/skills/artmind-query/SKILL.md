---
name: artmind-query
description: The artmind system stores information from ingested documents. Using this skill respond to natural-language questions from the user given a particular domain. These questions would be in the form of Q&A between the user and artmind
---

# artmind Query

Use this skill to answer user questions over an artmind domain through the deterministic query CLI. The skill provides the reasoning layer; the CLI provides templated graph/vector retrieval and JSON output.

## Grounding Rule

Use only the structured KG data and chunk text returned by artmind query commands. If the data is insufficient, say so clearly. Do not invent entities, events, relationships, motivations, or source details not present in the returned data.

Any stderr line starting `artmind: ` about a store being behind, unreachable, not pulled, or unable to sync — e.g. `artmind: graph is N docs / M tables behind the vault`, `artmind: structured store is M tables behind the vault`, `artmind: the graph store's sync bookmark is not in this clone's history -- let Obsidian Git pull...`, or `artmind: the graph store's sync would fail -- ...` — is advisory, not an error and not query data: the answer may miss recently pulled documents, or the vault owner may need to pull or fix something before syncing. Tell the user once, in your own words; never run `vault sync` (or any of its flags) yourself.

## Required Inputs

- `domain`: Ask for it if the user did not provide one.
- `question`: The natural-language question to answer. If the user asks multiple questions, break them down and answer each.

## Fixed Structural Schema

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
and is therefore current by construction; only `pattern10 --asOf` (see Retrieve) reaches
into history at all.

For document/chunk questions use `PART_OF` (not `EXTRACTED_FROM`); `pattern10` does this
deterministically.

Add `--compact` to every command — it halves the JSON you must read.

A domain can also have tabular data (csv/xlsx ingested via `artmind ingest`) living
in a separate SQL store, independent of the graph above — see "Store routing" below.

## The Query Protocol: Route → Discover → Resolve → Retrieve → Ground → Adjudicate

### 0. Route — pick the domain set

Policies and the SOPs/matrices about the same subject live in DIFFERENT sibling
domains by design (e.g. `banking_policy` vs `banking_sop_guides`). Before answering:

```bash
artmind query domains-overview --compact
```

- If the user names an exact single small domain, use it and skip to Discover.
- If the user names two or more SPECIFIC domains directly (e.g. "compare banking_policy
  and banking_sop_guides"), use exactly those domains and skip to Discover — no
  sub-agent needed, since routing is already resolved.
- If the user names an AREA ("banking", "our policies"), or it's unclear which
  domain(s) hold the answer, or listings look large, launch ONE sub-agent that runs
  `domains-overview` + per-domain `structural-metadata --compact` + `entity-resolve`,
  and returns ONLY a compact routing report:
  `{domains, resolved_entities:[{id,name,class,domain}], relevant_classes, relevant_rel_types}`.
  Main context never sees the raw listings.
- Pass `--domain` once per selected domain on every subsequent command; a single
  command call now spans all of them.

#### Store routing

Once the domain set is fixed, check whether a structured store is even in play:

```bash
artmind db bridge --domain <d> --compact
```

An empty `tables` list means the domain is genuinely pure-graph — skip ahead to
Discover below, no further `db` calls. If it returns any tables, read
`references/structured-store.md` before classifying the question — it covers
store routing (narrative vs analytical vs hybrid vs records-plus-guidance),
worked examples, and how `--asOf` threads through the hybrid chain.

### 1. Discover — learn the domain's shape

Start every new domain/question session with:

```bash
artmind query graph metadata --domain <domain> --compact
artmind query graph entity-listing --domain <domain> --countAll --compact
```

For document/chunk/count questions, use the compact alternative instead of the two commands above:

```bash
artmind query graph structural-metadata --domain <domain> --compact
```

It returns Document names plus structural counts (Document/DocChunk/UserChat/Entity) without the full class/relationship breakdown — cheaper when you only need document names or counts, not the entity-class/relationship schema. From metadata identify: stored class labels (derived from `entity_class`, uppercased, non-alphanumerics → `_`), relationship types and directions, and whether the question needs graph facts, text evidence, or both.

If `total_entities` is large (> ~100), do not fetch the full listing. Narrow with `--nameFilter "<fragment>"`, or go straight to `artmind query graph pattern7`.

Document/chunk rows and `metadata` carry `valid_from`/`valid_to`/`superseded_by` — use them to judge document currency.

### 2. Resolve — map question names to exact graph nodes

Most wrong answers come from name mismatch: the user says "Holmes", the graph has "Sherlock Holmes" AND "Mycroft Holmes", and substring matching silently merges them. If Route's sub-agent already returned `resolved_entities`, reuse those ids directly and skip straight to step 3 — don't re-resolve. Otherwise, before running retrieval patterns:

1. Resolve every entity reference in the question:

```bash
artmind query entity-resolve --domain <domain> --topK 5 --compact "<name fragment or description>"
```

This combines Lucene full-text over entity names/descriptions with vector similarity over entity embeddings (RRF), so it handles both name fragments ("Holmes") and purely descriptive references ("the detective"). Each row returns the entity's `id`, `name`, `entity_class`, and `description`. (Alternatives: `entity-listing --nameFilter` for plain fragments, `pattern7 --searchTerm` for fulltext-only.)

2. Pick the canonical entity. If several are plausible, prefer the best name/description match and note the ambiguity in your answer; ask the user only if the choices change the answer materially.
3. Use the entity's exact `id` in retrieval via `--entityId` / `--entityId1` / `--entityId2` / `--entityIdList`. Ids never fan out; names can. Fall back to `--entityName` only when resolution was skipped because the name is unambiguous.

If entity-resolve returns nothing for an old graph, embeddings may be missing — `artmind ingest embed-entities --domain <domain>` backfills them.

### 3. Retrieve — run the right pattern

Every command below is written in full. The `pattern*` commands, `text2cypher`,
`timeline`, `conflicts`, `metadata`, `structural-metadata` and
`entity-listing` all live under the `graph` sub-group (`artmind query graph <cmd>`);
`entity-context`, `entity-history`, `chunks`, `vector-text`, `entity-resolve` and
`domains-overview` sit directly under `query` (`artmind query <cmd>`). Copy the prefix
as written — do not infer it.

| Question shape | Command |
|---|---|
| "Tell me about X / X's role / why did X…" — facts + relationships + source text in ONE call | `artmind query entity-context --entityId <id> [--includeChunks 5]` |
| Domain-wide history / sequence of events for a class of things, ordered by date | `artmind query graph timeline --domain <d> [--from <date>] [--to <date>]` — every entity of a `kind: occurrent` class, ordered by `valid_from`. Domain-scoped, NOT entity-scoped — there is no `--entityId` |
| "What was property P of X before it changed" / X's value at a past date | `artmind query entity-history --entityId <id> [--property P] [--asOf <date>]` — every observation behind X, ordered by fact-level valid time (`_valid_from`), spanning both current and retired sources |
| List entities of a class | `artmind query graph pattern1 --entityClass <LABEL> [--limit N]` |
| "Main / key / most important / top" entities | `artmind query graph pattern9 --entityClass <LABEL> --topN 5` (default ranks by entity-entity links; `--degreeMode mentions` ranks by how often sources mention it) |
| Facts/properties of named entities (no text needed, e.g. comparing many) | `artmind query graph pattern2 --entityIdList <id>` (or `--entityNameList`) |
| Properties + relationship summary | `artmind query graph pattern3 --entityIdList <id>` |
| Full one-hop neighborhood / contextual role | `artmind query graph pattern4 --entityClass <LABEL> --entityId <id>` |
| Everyone under X / X's whole reporting line / full org subtree / chain of command | `artmind query graph hierarchy --entityId <id> --childOf <REL> [--parentOf <REL>] [--direction up] [--maxDepth N]` — omit `--childOf`/`--parentOf` for discovery mode first; see `references/hierarchy.md` |
| Text of specific chunks by id (doc_sources / evidence ids) | `artmind query chunks --idList <id> [--expand 1]` |
| Does a direct link exist between X and Y, and of what type | `artmind query graph pattern6 --entityId1 <id> --entityId2 <id>` |
| Nature/quality of a relationship, "how are X and Y related/connected" | `artmind query graph pattern5 --mode shortest --entityClass1/2 --entityId1/2` (paths traverse entity-entity edges only); `--mode all` for up to 3 paths |
| Search entities by name/description fragment | `artmind query graph pattern7 --searchTerm "<fragment>"` (Lucene-backed; punctuation is stripped automatically) |
| Entities of class X connected to entity Y | `artmind query graph pattern8 --entityClass <LABEL> --entityId <id>` |
| All chunks of / summarize a document | `artmind query graph pattern10 --documentName "<name>"` |
| Aggregations, custom filters, multi-hop combinations none of the above cover | `artmind query graph text2cypher "<question>"` — run `--dry-run` first to inspect the Cypher |

Every command also needs `--domain <domain>` (repeatable) and `--compact`, omitted above for brevity.

Routing notes:
- **entity-context vs pattern4**: for a question anchored on ONE resolved entity that
  needs evidence text, `artmind query entity-context --domain <d> --entityId <id>`
  replaces the pattern4 + Ground sequence — it returns properties, one-hop
  relationships, and the text of the entity's most current source chunks
  (current-first ordering; the first `--includeChunks` with full text, the rest as
  ids in `more_chunks`, fetchable via `chunks --idList`). Use pattern4 when you
  only need structure, or patterns 2/3 for several entities at once.
- **pattern6 vs pattern5**: pattern6 answers "is there a direct relationship and what type". For the *nature or quality* of a relationship, use pattern5 — then ground with vector-text for narrative evidence. If pattern6 returns no rows, escalate to pattern5 `--mode shortest`.
- **pattern4 vs hierarchy**: pattern4 is one hop only. For a transitive walk — everyone under X, X's whole reporting line, chain of command — use `hierarchy` instead; see `references/hierarchy.md`.
- Patterns 2/3/4 return `doc_sources` — use these ids to know *where* a fact came from, and pull the actual text deterministically with `artmind query chunks --domain <d> --idList <chunk_id> [--expand 1]` (never re-search for text you already have ids for). `--expand 1` adds the adjacent chunks of the same document when one chunk is too little context.
- All commands accept repeatable `--domain` (comma-splittable) and roll sub-domains up.
  Rows carry `_domain` on chunks/documents — every fact you state must be attributed
  to BOTH its document name AND its domain.
- **No `--asOf` on entity commands, and none needed** — the projection (`:Entity`) is
  current by construction, so none of the ten `pattern*` commands, `entity-listing`,
  `entity-resolve`, `entity-context`, or `graph metadata` accept it. For a temporal
  question ("as of <date>", "history of…", "what changed"), or an entity carrying
  `_temporal_props`, read `references/temporal.md` — it covers `--asOf`'s narrower
  per-command meaning, `timeline` vs `entity-history`, and `_temporal_props`.

### 4. Ground — pull source text when narrative evidence is needed

Grounding has a deterministic path and a search path — prefer the deterministic one:

1. **You already have chunk ids** (doc_sources from patterns 2/3/4, evidence from
   conflicts) → `artmind query chunks --domain <d> --idList <id> [--expand 1]`.
2. **The question is anchored on one resolved entity** → you should have used
   `entity-context` in Retrieve, which grounds in the same call.
3. **Otherwise** (no entity anchor, or the ids you pulled don't answer it):

```bash
artmind query vector-text --domain <d1> --domain <d2> --topK 5 --compact "<question>"
```

vector-text combines semantic (vector) and keyword (Lucene BM25 full-text) search via Reciprocal Rank Fusion; returns both document chunks and user chats. Use it for "where/when/how did X happen", motivations, quotes, or whenever graph output is too thin. In hybrid answers, take entity/relationship facts from the graph and narrative evidence from chunk text. When Route selected multiple domains, Ground should query all of them together in one call so results can be compared side by side.

### 5. Adjudicate — surface disagreements, never blend

Never average, reconcile silently, or drop one side when two sources disagree —
surface both claims with both provenances. `:Conflict` has two shapes sharing one
label (adjudicator-produced and projection-produced); before concluding nothing
disagrees, or whenever a question involves comparing claims across documents or
entities, read `references/conflicts.md` for how to check for both shapes, the
fan-out caveat, and how to qualify a claim by time (superseded documents are
history, not a live disagreement).

## Fallback Ladder

1. Thin results in the chosen domain → re-run with sibling domains from Route before concluding data is absent. (Same fix as Adjudicate's "only one side retrieved" case — apply it as soon as results look thin, not only after a disagreement surfaces.)
2. pattern6 empty → pattern5 `--mode shortest`.
3. entity-context/pattern chunks too thin → `chunks --idList <more_chunk ids> --expand 1` before falling back to search.
4. Pattern output empty or too thin → vector-text.
5. text2cypher returns no rows but data should exist → run `artmind query graph structural-metadata`, then `artmind query graph text2cypher --dry-run` and compare relationship names; rephrase the question naming the correct relationship (e.g. "use PART_OF to connect DocChunk to Document").
6. text2cypher generates invalid Cypher → vector-text.
7. vector-text sparse or weak → state that the available artmind data does not answer the question.
8. `text2sql` returns no rows but data should exist → re-run with `--dry-run` and compare the generated SQL against `db schema`'s column list; rephrase the question naming the exact table/column, or fall back to `db sql` with hand-written SQL if the phrasing keeps generating the wrong filter.
9. `resolve-key` returns no confident match → widen `--topK`, or drop `--column` to resolve against the graph only (the phrase may be a graph entity name with no structured-column analogue).
10. `hierarchy` returns empty rows/edge_candidates → re-run discovery mode to confirm no edges exist at all → if still empty, the relationship may only be stored as a property (e.g. `reports_to` on the class) — fall back to `pattern1` on that class; see `references/hierarchy.md`.

## Answer Style

Answer directly and naturally. Keep provenance concise: say whether the answer comes from graph relationships, entity properties, chunk text, or a combination. Mention unresolved ambiguity when you picked between candidate entities. Do not expose raw JSON unless asked.
