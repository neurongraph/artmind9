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

Add `--compact` to every command — it drops the pretty-printing whitespace. It does not
shrink what a command returns; bounding that is each command's own options (see
"Hub entities" below).

A domain can also have tabular data (csv/xlsx ingested via `artmind ingest`) living
in a separate SQL store, independent of the graph above — see "Store routing" below.

## The Query Protocol: Route → Views → Discover → Resolve → Retrieve → Ground → Adjudicate

### 0. Route — pick the domain set

Policies and the SOPs/matrices about the same subject live in DIFFERENT sibling
domains by design (e.g. `banking_policy` vs `banking_sop_guides`). Before answering:

```bash
artmind query domains-overview --compact
```

- If the user names an exact single small domain, use it and go on to Views.
- If the user names two or more SPECIFIC domains directly (e.g. "compare banking_policy
  and banking_sop_guides"), use exactly those domains and go on to Views — no
  sub-agent needed, since routing is already resolved.
- If the user names an AREA ("banking", "our policies"), or it's unclear which
  domain(s) hold the answer, or listings look large, launch ONE sub-agent that runs
  `domains-overview` + per-domain `structural-metadata --compact` + `entity-resolve`,
  and returns ONLY a compact routing report:
  `{domains, resolved_entities:[{_id,name,class,domain,observation_count}], relevant_classes, relevant_rel_types}`.
  Main context never sees the raw listings.
- Pass `--domain` once per selected domain on every subsequent command; a single
  command call now spans all of them.

#### Store routing

Once the domain set is fixed, check whether a structured store is even in play:

```bash
artmind db bridge --domain <d> --compact
```

An empty `tables` list means the domain is genuinely pure-graph — go on to
Views below, no further `db` calls. If it returns any tables, read
`references/structured-store.md` before classifying the question — it covers
store routing (narrative vs analytical vs hybrid vs records-plus-guidance),
worked examples, and how `--asOf` threads through the hybrid chain.

### 1. Views — check for a saved view first

A **view** is a reviewed, parameterised, read-only graph query that a domain owner saved
for a question that keeps coming up, together with guidance on how to present it. A
matching view replaces Discover, Resolve and Retrieve: one call instead of several.

List the catalogue **once per domain per conversation** (it needs the domain set from
Route), and reuse that listing for later questions in the same conversation:

```bash
artmind query views list --domain <domain> --compact
```

Re-list only if the user says they just added or changed a view. An empty `views` list
means this domain has none: go on to Discover. `invalid` entries are broken views;
ignore them (mention them only if the user asks about views).

If the question matches a view's `summary` or `examples`:

1. `artmind query views show <name> --compact` and follow the view's `skill_md`: it says
   when the view is the wrong tool, what each column means, and how to present the result.
2. Run it, passing one `--param name=value` per entry in the view's `params`:

   ```bash
   artmind query views run <name> --domain <domain> --param <p>=<value> --render markdown --compact
   ```

   If Route already returned `resolved_entities`, pass the id as `--param <p>=@<_id>`:
   an `@` value skips name resolution. Otherwise give the name as the user said it; the
   view resolves it.
3. Read `status`:
   - `ok`: state which entity each `params` entry resolved to (so the user can catch a
     wrong interpretation), then present `rendered` as the view's `skill_md` directs. Do
     not re-format the rows. If `truncated` is true, say how many rows were shown of
     `rows_total`. Continue to Ground when the answer needs source text.
   - `needs_disambiguation`: show the `candidates` (name, class, `observation_count`),
     ask the user which one, then rerun with `--param <param>=@<_id>`.
   - `no_match`: tell the user nothing matched `input`, then continue with Discover.

If no view fits, or the result is empty or does not answer the question, continue with
Discover as normal. Never invent a view name; only run names returned by `views list`.

### 2. Discover — learn the domain's shape

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

`structural-metadata`'s `names` and `entity-listing`'s per-class `typeGroups[].names` are
each capped (`--maxNames`, default 50) with a `names_total` count alongside — a vault with
1000+ documents or a hub-heavy domain would otherwise exceed what agent harnesses show
before truncating. If `names_total` exceeds what came back, raise `--maxNames`, but
prefer narrowing instead (below) — a bigger page is rarely the fix for "I need the
count/names of a *specific* thing."

If `total_entities` is large (> ~100), do not fetch the full listing. Narrow with `--nameFilter "<fragment>"`, or go straight to `artmind query graph pattern7`. For a specific document's content rather than the list of document names, go straight to `pattern10 --documentName "<name>"` instead of scanning `structural-metadata`'s capped `names`.

Document/chunk rows and `metadata` carry `valid_from`/`valid_to`/`superseded_by` — use them to judge document currency.

### 3. Resolve — map question names to exact graph nodes

Most wrong answers come from name mismatch: the user says "Holmes", the graph has "Sherlock Holmes" AND "Mycroft Holmes", and substring matching silently merges them. If Route's sub-agent already returned `resolved_entities`, reuse those ids directly and skip straight to Retrieve — don't re-resolve. Otherwise, before running retrieval patterns:

1. Resolve every entity reference in the question:

```bash
artmind query entity-resolve --domain <domain> --topK 5 --compact "<name fragment or description>"
```

This combines Lucene full-text over entity names/descriptions with vector similarity over entity embeddings (RRF), so it handles both name fragments ("Holmes") and purely descriptive references ("the detective"). Each row's `entity` carries `_id`, `name`, `entity_class`, `description`, plus `observation_count` and `connection_count` — how much the entity has aggregated. `score` is the fused RRF score (rows from both legs rank highest; `matched_by` names the legs), comparable across rows. (Alternatives: `entity-listing --nameFilter` for plain fragments, `pattern7 --searchTerm` for fulltext-only.)

2. Pick the canonical entity. If several are plausible, prefer the best name/description match and note the ambiguity in your answer; ask the user only if the choices change the answer materially. A person's name also matches events named after them ("Irene Adler's Wedding") — when the question is about such an event, that hit can answer it outright.
3. Use the entity's exact `_id` in retrieval via `--entityId` / `--entityId1` / `--entityId2` / `--entityIdList`. Ids never fan out; names can. Fall back to `--entityName` only when resolution was skipped because the name is unambiguous.

If entity-resolve returns nothing for an old graph, embeddings may be missing — `artmind ingest embed-entities --domain <domain>` backfills them.

### 4. Retrieve — run the right pattern

Every command below is written in full. The `pattern*` commands, `text2cypher`,
`timeline`, `conflicts`, `metadata`, `structural-metadata` and
`entity-listing` all live under the `graph` sub-group (`artmind query graph <cmd>`);
`entity-context`, `entity-history`, `chunks`, `vector-text`, `entity-resolve` and
`domains-overview` sit directly under `query` (`artmind query <cmd>`). Copy the prefix
as written — do not infer it.

| Question shape | Command |
|---|---|
| "Tell me about X / X's role / why did X…" / any question anchored on one entity — facts + relationships + source text in ONE call | `artmind query entity-context --entityId <id> --query "<the question>" [--includeChunks 5] [--relType <REL>]` |
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
| Entities of class X connected to entity Y | `artmind query graph pattern8 --entityClass <LABEL> --entityId <id> [--limit N] [--offset N]` |
| All chunks of / summarize a document | `artmind query graph pattern10 --documentName "<name>" [--limit N] [--offset N]` |
| Aggregations, custom filters, multi-hop combinations none of the above cover | `artmind query graph text2cypher "<question>"` — run `--dry-run` first to inspect the Cypher; rows are capped (`--maxRows`, default 200, `rows_total`/`truncated` alongside) since the generated Cypher carries no LIMIT of its own |

Every command also needs `--domain <domain>` (repeatable) and `--compact`, omitted above for brevity.

Routing notes:
- **entity-context vs pattern4**: for a question anchored on ONE resolved entity that
  needs evidence text, `artmind query entity-context --domain <d> --entityId <id>
  --query "<question>"` replaces the pattern4 + Ground sequence — it returns
  properties, one-hop relationships, and the text of the entity's source chunks most
  relevant to the question (the first `--includeChunks` with full text, the next ids
  in `more_chunks`, fetchable via `chunks --idList`). Always pass `--query` with the
  user's question: without it, chunks come newest-first and connections by evidence
  weight, which for a busy entity rarely includes the one that answers. Its output is
  bounded — see "Hub entities" below. Use pattern4 when you only need structure, or
  patterns 2/3 for several entities at once.
- **pattern6 vs pattern5**: pattern6 answers "is there a direct relationship and what type". For the *nature or quality* of a relationship, use pattern5 — then ground with vector-text for narrative evidence. If pattern6 returns no rows, escalate to pattern5 `--mode shortest`.
- **pattern4 vs hierarchy**: pattern4 is one hop only. For a transitive walk — everyone under X, X's whole reporting line, chain of command — use `hierarchy` instead; see `references/hierarchy.md`.
- Patterns 2/3/4 return `doc_sources` (chunk ids) and `source_documents` (the documents those chunks belong to, with `path`/`source_path` for the Sources list) — use the ids to know *where* a fact came from, and pull the actual text deterministically with `artmind query chunks --domain <d> --idList <chunk_id> [--expand 1]` (never re-search for text you already have ids for). `--expand 1` adds the adjacent chunks of the same document when one chunk is too little context.
- **Patterns 2/3/4 are bounded like entity-context, without its ranking.** `doc_sources`/
  `source_documents` are capped (`--maxDocSources` default 20, `--maxSourceDocuments`
  default 10) with `*_total` counts; patterns 3/4's `connections` are capped
  (`--maxConnections`, default 25, `connections_total` alongside), and pattern4's
  `connected_to.data` list properties are additionally capped (`--maxListItems`, default
  10, `truncated_properties` per connection). Each `rel_properties`/`properties` map on a
  connection no longer carries the edge's raw `chunk_ids`/`doc_ids` (those are unbounded
  per edge) — only `observation_count` and a `doc_count`. Unlike entity-context, these
  three patterns only ever sort alphabetically/deterministically, so raising a `--max*`
  option is a blunt instrument: for a genuine hub entity, route to `entity-context
  --relType <type> --query "<question>"` instead (see "Hub entities" below) so the edge
  that answers the question sorts to the top rather than needing a bigger page.
- **pattern8/pattern10 paginate with `--offset`.** Both cap output (`--limit`, defaults
  50/300) and report `rows_total`/`truncated`. Unlike the commands above, neither has a
  ranked alternative to narrow toward instead — "entities of class X connected to Y" and
  "chunk N of document D" have no relevance signal to sort by — so when `truncated` is
  true and the row you need might be past the cut, re-run with `--offset <previous
  offset + limit>` to walk the rest (pattern10 orders chunks by `c.id`, so offsets are
  stable across calls).
- **text2cypher has no `--offset`.** The generated Cypher is LLM-written per call, so
  there's no generic way to inject a `SKIP`. If `truncated` is true, don't ask it to
  "get the next page" — rephrase the question to be more selective (name the entity/date
  range the row you need falls under), or add a dedicated `ORDER BY` to the dry-run
  Cypher yourself if you need a specific slice.
- All commands accept repeatable `--domain` (comma-splittable) and roll sub-domains up.
  Rows carry `_domain` on chunks/documents — every fact you state must be attributed
  to BOTH its document name AND its domain.
- **No `--asOf` on entity commands, and none needed** — the projection (`:Entity`) is
  current by construction, so none of the ten `pattern*` commands, `entity-listing`,
  `entity-resolve`, `entity-context`, or `graph metadata` accept it. For a temporal
  question ("as of <date>", "history of…", "what changed"), or an entity carrying
  `_temporal_props`, read `references/temporal.md` — it covers `--asOf`'s narrower
  per-command meaning, `timeline` vs `entity-history`, and `_temporal_props`.

#### Hub entities

Some entities aggregate a lot — the protagonist of a novel series, the bank in a
policy corpus: hundreds of observations, hundreds of edges. `entity-resolve`'s
`observation_count`/`connection_count` show it up front. Their full dossier is far too
big to read (and agent harnesses truncate it — a `...output truncated...` notice means
you never saw the part that answers), so `entity-context` returns a bounded one:

- `connection_summary` — `{rel_type, direction, count}` over **every** edge: the shape
  of the entity. `connections_total` is the edge count; `connections` lists only the
  top `--maxConnections` (default 25), each `{rel_type, direction, target{id, name,
  entity_class}, observation_count, doc_count}`.
- `chunks_total` / `source_documents_total` count everything behind the entity;
  `chunks`, `more_chunks` (≤20) and `source_documents` (≤10, the returned chunks'
  documents first) are the cut.
- List properties keep their newest `--maxListItems` (default 10) entries;
  `truncated_properties` gives each cut list's full length.

How to work with it:

1. **Pass `--query "<the question>"`.** It ranks connections (question words matched
   against target names and rel_types, then embedding similarity) and chunks (vector +
   keyword, fused) toward the question. "When did Holmes first meet Watson" then puts
   the `PARTICIPATES_IN → First Meeting of Holmes and Watson` edge and its source chunk
   on top, where the unranked dossier buried them under hundreds of other edges.
2. **Drill in with `--relType`** (repeatable): when `connection_summary` shows the
   relevant kind of edge (e.g. `PARTICIPATES_IN` × 130 for "which cases did he work on"),
   re-run with `--relType PARTICIPATES_IN --maxConnections 50`. The summary still covers
   everything, so you can see what you filtered out.
3. **A specific fact may be better served by search.** A single dated fact about X is
   often answered directly by `vector-text "<question>"` — a few KB of the
   matching chunks — or by `entity-resolve` hitting the event entity itself. Use
   `entity-context` for questions *about the entity as a whole* (role, relationships,
   characteristics), search for a single dated fact.
4. A list cut in `truncated_properties` keeps only the newest entries. For an older value
   use `entity-history --entityId <id> --property <p>`, not a larger `--maxListItems`.

### 5. Ground — pull source text when narrative evidence is needed

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

### 6. Adjudicate — surface disagreements, never blend

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
3. entity-context/pattern chunks too thin → re-run entity-context with `--query` (if you didn't) or a narrower `--relType`, or pull `chunks --idList <more_chunk ids> --expand 1`, before falling back to search.
4. Pattern output empty or too thin → vector-text.
5. text2cypher returns no rows but data should exist → run `artmind query graph structural-metadata`, then `artmind query graph text2cypher --dry-run` and compare relationship names; rephrase the question naming the correct relationship (e.g. "use PART_OF to connect DocChunk to Document").
6. text2cypher generates invalid Cypher → vector-text.
7. vector-text sparse or weak → state that the available artmind data does not answer the question.
8. `text2sql` returns no rows but data should exist → re-run with `--dry-run` and compare the generated SQL against `db schema`'s column list; rephrase the question naming the exact table/column, or fall back to `db sql` with hand-written SQL if the phrasing keeps generating the wrong filter.
9. `resolve-key` returns no confident match → widen `--topK`, or drop `--column` to resolve against the graph only (the phrase may be a graph entity name with no structured-column analogue).
10. `hierarchy` returns empty rows/edge_candidates → re-run discovery mode to confirm no edges exist at all → if still empty, the relationship may only be stored as a property (e.g. `reports_to` on the class) — fall back to `pattern1` on that class; see `references/hierarchy.md`.

## Answer Style

Answer directly and naturally. Keep provenance concise: say whether the answer comes from graph relationships, entity properties, chunk text, or a combination. Mention unresolved ambiguity when you picked between candidate entities. Do not expose raw JSON unless asked.

### Sources

End every answer that used artmind data with a `### Sources` section: one bullet per
document or table the answer actually relies on — not everything retrieval returned —
as a markdown link to its file, followed by its domain:

```markdown
### Sources
- [Delivery Leads.md](<performance_management/Delivery Leads.md>) — performance_management
- [hercules_output_20261002](<performance_management/hercules_output_20261002.xlsx>) — performance_management (table)
```

- **Link target:** the document's `source_path` when present, else its `path`, copied
  exactly as returned. `source_path` is the original file (a PDF stays a PDF); a
  binary's `path` points at an internal converted copy that won't open. Table-backed
  facts: link the `source_file` from `text2sql`'s `source_tables` (or `db list`).
- **Always wrap the target in `<…>`** — paths contain spaces, which break a bare
  markdown link.
- **Links go only in Sources.** In the body, name a document in plain text
  ("per Delivery Leads.md") — never as a link.
- The chat UI turns these links into "open in Obsidian" links. Never write an
  `obsidian://` URI yourself, and never invent a path: if no returned row carried one
  for a document, list its name and domain without a link.
- Omit the section only when no artmind data was used (e.g. the data doesn't answer
  the question).
