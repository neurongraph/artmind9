---
name: artmind-query-orient
description: The artmind system stores information from ingested documents. Using this skill respond to natural-language questions from the user given a particular domain. These questions would be in the form of Q&A between the user and artmind. Always orients first (`artmind query orient`): it describes each domain, its classes, types, relations, stores and saved views before any retrieval.
---

# artmind Query

Use this skill to answer user questions over an artmind domain through the deterministic query CLI. The skill provides the reasoning layer; the CLI provides saved views, templated graph/vector retrieval and JSON output.

## Orient — read the corpus first

`orient` describes the corpus in two peels. It never sees the question: you match the
question against what it says.

1. `artmind query orient --compact` once per conversation. Each card says what a domain
   is (`purpose`), what one document is (`unit`), whose voice it is (`voice`: "I" is the
   narrator), how documents are dated (`time`), its stores and its `vocabulary` (top classes).
   `tables` lists the structured tables registered at that domain (rows, bridge classes);
   a card with `tables_at: <domain>` has none of its own but that domain's tables apply to it.
   A question about specific records (a customer, a case, a count, "which/how many") goes to
   those tables (`references/structured-store.md`), even when the wording does not name a table.
2. Pick the domains the question touches from `purpose`, `vocabulary` and `family`. A
   collapsed family row opens with `--domain <family>`; in peel 2 open only the children the
   question needs (a whole family is cut hard). Policies and the SOPs about the same subject
   often sit in sibling domains: take both when the question spans them.
3. `artmind query orient --domain <d> [--domain <d2>] --compact` for those domains. It lists
   classes (definition, `types` with counts, `dated` share), relation shapes
   (`from -rel-> to`), structured tables, saved views, topic pages, insights and superseded
   documents.
4. A saved view whose `summary` or `examples` fit the question → `views show` / `views run`
   (Views below). Otherwise choose the command from the classes, types, relation shapes and
   stores, and spell `--entityClass`, type values and relation names exactly as orient
   listed them. A `chunks-only` card has documents and chunks but no entities, so skip views, insights,
   entity and pattern commands there:
   - a fact → `artmind query vector-text --shape brief --domain <d> --topK 10 --compact "<question>"`;
   - a question across documents → `artmind query survey --shape brief --domain <d> --compact "<question>"`;
   - a whole document → `artmind query graph pattern10 --documentName "<name>"`; more context
     around a hit → `artmind query chunks --shape brief --idList <id> --expand 1`;
   - in a mixed set, leave chunks-only domains out of graph commands but keep them in the
     shared `vector-text` call. Tables listed under `structured` still apply.
5. Orient again only when the user moves to a new area. A part named in `warnings` was not
   read: never treat it as empty. `null` means declared absent (no narrator, no snapshot).
6. `next` and `cuts[].rest` are facts about this result; follow them when they serve the
   question.

## Running commands

On the grounded Q&A surfaces a shell command is allowed only as a single `artmind ...` invocation with nothing before or after it: no `cd`, `&&`, `;`, pipes, redirects or `$(...)`. The working directory is already the vault, so never `cd` into it. A refused command is not a reason to stop: re-run it bare.

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
and is therefore current by construction; only `pattern10 --includeHistory` (see Retrieve)
reaches into history at all, and a document row's `supersedes` names the version it replaced.

For document/chunk questions use `PART_OF` (not `EXTRACTED_FROM`); `pattern10` does this
deterministically.

Add `--compact` to every command — it drops the pretty-printing whitespace. It does not
shrink what a command returns; bounding that is each command's own options (see
"Hub entities" below).

A domain can also have tabular data (csv/xlsx ingested via `artmind ingest`) living
in a separate SQL store, independent of the graph above — see "Store routing" below.

## Insights — for broad or retrospective questions

Some questions are about patterns rather than facts. Examples:
- "what's changed this year";
- "who matters most at work now";
- "what tends to go with bad days";
- "who have I lost touch with".

The domain owner's insights snapshot already answers those. Check it after Orient and Views (a saved view still comes first), before Discover:

```bash
artmind query insights list --domain <domain> --compact
```

Each finding has a `title`, a `narration` (a checked one-line caption, or null) and its `subjects` with their `_id`s.
- Use `--detector` to focus: for example `outcome_lift` for associations, `transitions` for life changes, `dormancy` for people gone quiet, or `communities` for circles.
- Use `--new` for "what changed".
- Use `artmind query insights show <key> --domain <domain> --shape brief --compact` for the numbers behind a finding.

When you answer from insights:
- **Ground each subject** you name with `artmind query entity-context --shape brief --domain <domain> --entityId <_id> --compact`, and cite as usual.
- **outcome_lift findings** always carry the caption "Co-occurrence on the same day, not cause." Pass it on. Never describe a lift as a cause.
- **Freshness:** state the snapshot's `as_of`. If an `artmind:` stderr line says the graph is behind the vault, say that the insights may be older than the latest notes.
- **Refresh:** never run `artmind insights refresh` yourself. If there is no snapshot, say so; the domain owner runs it.

## Catalogue and assets — what is already known

Before building an answer from the raw graph, check what the system already holds for the domain:

```bash
artmind query assets --domain <domain> --like "<the question>" --compact
artmind query catalogue --domain <domain> --compact
```

`assets` lists saved views, topic pages, insight findings and summary coverage, each with an `open` command and a `stale` flag. A topic page or insight is a lead to the cited sources, never evidence itself: ground what you take from it as usual. `catalogue` reports the domain's classes, stores, time coverage and `health`; `health` may be `absent`, and you must not run `artmind catalogue refresh` yourself.

## Survey — questions that span documents

Some questions need coverage, not one passage: "what do the contracts say about
termination", "compare the approaches to X", "overview of Y", "which reports mention Z".
`vector-text` returns its top passages, which cluster in one or two documents. Use:

```bash
artmind query survey --shape brief --domain <d1> --domain <d2> --compact "<question>"
```

It ranks documents by their summaries and passages and returns, per document, its
`summary`, its best `passages` (with `id` and `chunk_id`) and its best `chunk_summary`;
`warnings` says when documents in scope have no summaries yet. `topics` lists topic
pages that cite the returned documents.

Use `--shape brief` for these retrieval commands: rows carry title, date and status. A
survey lists every document: full passages for the strongest matches, a question-matched
`gist` for the next ones, and one line for the rest. `gist_source: passage` is evidence;
a summary gist is only a guide. Open a document with `artmind query survey --domain <d>
--shape brief --expand <doc_id> "<question>"` or `artmind query chunks --shape brief
--idList <chunk_id>`. When a result says `truncated: true`, run the command in its `next`
field (`artmind query page <handle> --from N`) instead of guessing. Copy that `next` command verbatim: `query page` takes no `--domain`.

| The question asks for | Command |
|---|---|
| A fact (who, when, a figure, a quote) | the graph steps below, or `vector-text --topK 10` in a chunks-only domain |
| One document ("summarise X", "what does X say about Y") | `query graph pattern10 --documentName X`: its `summaries` holds the stored summary; read chunks only for detail |
| One entity broadly ("tell me about Acme") | `entity-context` (graph domains); if `query topics list` has a page for it, use it to organise the answer |
| A topic across documents | `query survey`, then `query chunks --shape brief --idList <chunk_id>` for the passages you cite |
| Patterns over time | insights |

Rules:
- A summary is LLM text, not evidence. Use summaries to choose documents and sketch an
  overview; quote facts from passages and cite their documents. For "summarise X" the
  stored summary may be the answer, labelled as artmind's summary of X, with X cited.
- One `query survey` call covers every domain in the set, as with `vector-text`.

### Topic pages

`query survey`'s `topics` lists pages that cite its documents. A page is a cited
write-up of one topic, built by the domain owner with `artmind topic build`.

```bash
artmind query topics show <slug> --compact
artmind query topics list --domain <d> --compact     # when the user asks what pages exist
```

- Read a page when `query survey` lists it or the user asks for "the page on X". It is
  a guide, not evidence: use it to find the right documents and to organise the
  answer, then fetch the passages it cites (`query chunks --shape brief --idList <chunk_id>`; a
  passage id `<chunk_id>_pNN` names its chunk) and cite those.
- If `stale` is true, say so and name `stale_reasons`; check what changed with
  `query survey`.
- Never run `artmind topic build` or `refresh` yourself: they cost LLM calls. If no page
  exists, answer from `query survey`; the owner can build one.

## The Query Protocol: Orient → Views → Resolve → Retrieve → Ground → Adjudicate

### 1. Views — check for a saved view first

A **view** is a reviewed, parameterised, read-only graph query that a domain owner saved
for a question that keeps coming up, together with guidance on how to present it. A
matching view replaces Discover, Resolve and Retrieve: one call instead of several.

The views come from `orient --domain`: match **every** new question against them, not only the first.

A question **matches** a view when the view's `summary` or `examples` describe the same
kind of question, even in different words or about a different entity: the `params` are
there to carry the entity, date or name the user gave. "Who reports to Priya?" matches a
view whose example is "show me Alex's team". When in doubt, `views show` it and let its
`skill_md` say whether it fits — that is one cheap call.

If the question matches a view:

1. `artmind query views show <name> --compact` and follow the view's `skill_md`: it says
   when the view is the wrong tool, what each column means, and how to present the result.
2. Run it, passing one `--param name=value` per entry in the view's `params`:

   ```bash
   artmind query views run <name> --domain <domain> --param <p>=<value> --render markdown --compact
   ```

   If you already resolved the entity, pass the id as `--param <p>=@<_id>`:
   an `@` value skips name resolution. Otherwise give the name as the user said it; the
   view resolves it.
3. Read `status`:
   - `ok`: state which entity each `params` entry resolved to (so the user can catch a
     wrong interpretation), then present `rendered` as the view's `skill_md` directs. Do
     not re-format the rows. If `truncated` is true, say how many rows were shown of
     `rows_total`. Continue to Ground when the answer needs source text.
   - `needs_disambiguation`: show the `candidates` (name, class, `observation_count`),
     ask the user which one, then rerun with `--param <param>=@<_id>`.
   - `no_match`: tell the user nothing matched `input`. If `suggestions` is non-empty,
     offer them as "did you mean" (name, class); if the user picks one, rerun with
     `--param <param>=@<_id>`. Otherwise continue with Discover.

If no view fits, or the result is empty or does not answer the question, continue with
Store routing and then Discover as normal. Never invent a view name; only run names `orient --domain` listed.

#### Store routing — once no view fits

`orient --domain` lists the domain's structured tables and their bridge classes. No `structured` key means a pure-graph domain. If tables are listed, read `references/structured-store.md` before classifying the question.

### 2. Discover — only when orient was cut

`orient --domain` already holds what Discover used to fetch. Run `graph metadata`,
`entity-listing --countAll` or `structural-metadata` only when a `cuts` entry names them
as the `rest`, or for a document list (`structural-metadata`) — never as an opener.
Document/chunk rows carry `valid_from`/`valid_to`/`superseded_by`; use them to judge currency.

### 3. Resolve — map question names to exact graph nodes

Most wrong answers come from name mismatch: the user says "Holmes", the graph has "Sherlock Holmes" AND "Mycroft Holmes", and substring matching silently merges them. Before running retrieval patterns:

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
`views`, `entity-context`, `entity-history`, `document-history`, `chunks`, `vector-text`, `entity-resolve` and
`orient` sit directly under `query` (`artmind query <cmd>`). Copy the prefix
as written — do not infer it.

This table is for questions the Views check found no view for. A question a saved view
covers is answered by `artmind query views run <name>` (see Views), not by a row below.

| Question shape | Command |
|---|---|
| Any question matching a saved view's `summary`/`examples` | `artmind query views run <name> --param <p>=<value> --render markdown` — see Views |
| "Tell me about X / X's role / why did X…" / any question anchored on one entity — facts + relationships + source text in ONE call | `artmind query entity-context --shape brief --entityId <id> --query "<the question>" [--includeChunks 5] [--relType <REL>]` |
| Domain-wide history / sequence of events for a class of things, ordered by date | `artmind query graph timeline --domain <d> [--from <date>] [--to <date>]` — every entity of a `kind: occurrent` class, ordered by `valid_from`. Domain-scoped, NOT entity-scoped — there is no `--entityId` |
| "What was property P of X before it changed" / X's value at a past date | `artmind query entity-history --entityId <id> [--property P] [--asOf <date>]` — every observation behind X, ordered by fact-level valid time (`_valid_from`), spanning both current and history sources; each row's `status` is `current` or `history` |
| An entity entity-resolve no longer finds (a misspelling a corrected note dropped, a retired document's entity) | `artmind query entity-history --name "<name>" [--entityClass <LABEL>]` or `--key "<name>\|<CLASS>\|<domain>"` — matches observations directly, so it works after the entity is gone; `keys` lists every key the name matched |
| "What changed when document D was edited / re-ingested" / earlier version of a note | `artmind query document-history --documentName "<name or path>"` — current vs history observations with the `doc_version` that last asserted each; `dropped_keys` = entities an earlier version stated and the current one no longer does. A name missing its extension is matched when exactly one document fits (`matched_name` + a warning); a miss returns `closest` names and a `next` command. See `references/temporal.md` |
| List entities of a class | `artmind query graph pattern1 --shape brief --entityClass <LABEL> [--limit N]` |
| "Main / key / most important / top" entities | `artmind query graph pattern9 --entityClass <LABEL> --topN 5` (default ranks by entity-entity links; `--degreeMode mentions` ranks by how often sources mention it) |
| Facts/properties of named entities (no text needed, e.g. comparing many) | `artmind query graph pattern2 --shape brief --entityIdList <id>` (or `--entityNameList`) |
| Properties + relationship summary | `artmind query graph pattern3 --entityIdList <id>` |
| Full one-hop neighborhood / contextual role | `artmind query graph pattern4 --entityClass <LABEL> --entityId <id>` |
| Everyone under X / X's whole reporting line / full org subtree / chain of command | `artmind query graph hierarchy --entityId <id> --childOf <REL> [--parentOf <REL>] [--direction up] [--maxDepth N]` — omit `--childOf`/`--parentOf` for discovery mode first; see `references/hierarchy.md` |
| Text of specific chunks by id (doc_sources / evidence ids) | `artmind query chunks --shape brief --idList <id>[,<id>…] [--expand 1]` (repeat the flag or comma-join ids; `--domain <d>` is optional and only scopes the lookup, the output's domains come from the chunks) |
| Does a direct link exist between X and Y, and of what type | `artmind query graph pattern6 --entityId1 <id> --entityId2 <id>` |
| Nature/quality of a relationship, "how are X and Y related/connected" | `artmind query graph pattern5 --mode shortest --entityClass1/2 --entityId1/2` (paths traverse entity-entity edges only); `--mode all` for up to 3 paths |
| Search entities by name/description fragment | `artmind query graph pattern7 --shape brief --searchTerm "<fragment>"` (Lucene-backed; punctuation is stripped automatically). It returns `rows_total`/`truncated`: raise `--limit` (max 500) until `truncated` is false for a complete list; when `rows_total_capped` is true the total is a lower bound. Each row's `documents` (up to 3 source documents, empty for entities built from tables) feed the Sources list |
| Entities of class X connected to entity Y | `artmind query graph pattern8 --entityClass <LABEL> --entityId <id> [--limit N] [--offset N]` |
| All chunks of / summarize a document | `artmind query graph pattern10 --documentName "<name>" [--limit N] [--offset N]` — add `--includeHistory` for retired/superseded versions; zero rows with `history_matches` means only a retired version matches |
| Aggregations, custom filters, multi-hop combinations none of the above cover | `artmind query graph text2cypher "<question>"` — run `--dryRun` first to inspect the Cypher, then run it without `--dryRun` (a dry run executes nothing and returns no rows); rows are capped (`--maxRows`, default 200, `rows_total`/`truncated` alongside) since the generated Cypher carries no LIMIT of its own |

Every command also needs `--domain <domain>` (repeatable) and `--compact`, omitted above for brevity.

Routing notes:
- **entity-context vs pattern4**: for a question anchored on ONE resolved entity that
  needs evidence text, `artmind query entity-context --shape brief --domain <d> --entityId <id>
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
- Patterns 2/3/4 return `doc_sources` (chunk ids) and `source_documents` (the documents those chunks belong to, with `path`/`source_path` for the Sources list) — use the ids to know *where* a fact came from, and pull the actual text deterministically with `artmind query chunks --shape brief --idList <chunk_id> [--expand 1]` (`--domain <d>` optional; never re-search for text you already have ids for). `--expand 1` adds the adjacent chunks of the same document when one chunk is too little context.
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
  per-command meaning, `timeline` vs `entity-history`, `_temporal_props`, and what a
  re-ingested document keeps of its earlier versions (`document-history`).
- **History questions go to `entity-history` / `document-history`, not text2cypher.**
  History observations hang off *current* chunks (chunk ids are reused across
  versions), so a generated query that looks for `:ObservationHistory` under
  `:DocChunkHistory` — or chains a second `MATCH` after a `count()` — returns nothing
  and reads as "no history" when there is plenty.

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
   conflicts) → `artmind query chunks --shape brief --domain <d> --idList <id> [--expand 1]`.
2. **The question is anchored on one resolved entity** → you should have used
   `entity-context` in Retrieve, which grounds in the same call.
3. **Otherwise** (no entity anchor, or the ids you pulled don't answer it):

```bash
artmind query vector-text --shape brief --domain <d1> --domain <d2> --topK 10 --compact "<question>"
```

vector-text searches passages (~1200-char spans of the source text) by meaning and by keyword, fuses both rankings, groups hits into their parent chunk and returns them in relevance order. Each row is a parent chunk: `chunk.text` (the whole chunk), `passage` (`char_start`/`char_end` of the span that matched, inside `chunk.text` — quote from there), `relevance` (0-1, how close the best passage is to the question; comparable across questions; `null` when only keywords matched), `matched_by` (`vector`, `fulltext` or both) and `score` (rank order only — never compare it across calls). Ask for 10 rows: the right chunk is often just below the top 5. `--context passage` returns only the matching passages (smaller, for scanning many hits). Use it for "where/when/how did X happen", motivations, quotes, or whenever graph output is too thin. In hybrid answers, take entity/relationship facts from the graph and narrative evidence from chunk text. When you picked several domains, Ground should query all of them together in one call so results can be compared side by side.

### 6. Adjudicate — surface disagreements, never blend

Never average, reconcile silently, or drop one side when two sources disagree —
surface both claims with both provenances. `:Conflict` has two shapes sharing one
label (adjudicator-produced and projection-produced); before concluding nothing
disagrees, or whenever a question involves comparing claims across documents or
entities, read `references/conflicts.md` for how to check for both shapes, the
fan-out caveat, and how to qualify a claim by time (superseded documents are
history, not a live disagreement).

## Fallback Ladder

The ladder starts after the Views check: a view that fits is tried before any rung below.

1. Thin results in the chosen domain → re-run with sibling domains from orient before concluding data is absent. (Same fix as Adjudicate's "only one side retrieved" case — apply it as soon as results look thin, not only after a disagreement surfaces.)
2. pattern6 empty → pattern5 `--mode shortest`.
3. entity-context/pattern chunks too thin → re-run entity-context with `--query` (if you didn't) or a narrower `--relType`, or pull `chunks --idList <more_chunk ids> --expand 1`, before falling back to search.
4. Pattern output empty or too thin → vector-text.
5. A question that spans documents, whose `vector-text` rows come from one or two documents → re-run it as `query survey`.
6. text2cypher returns no rows but data should exist → run `artmind query graph structural-metadata`, then `artmind query graph text2cypher --dryRun` and compare relationship names; rephrase the question naming the correct relationship (e.g. "use PART_OF to connect DocChunk to Document").
7. text2cypher generates invalid Cypher → vector-text.
8. vector-text sparse or weak (few rows, every `relevance` low, or rows matched by `fulltext` alone on a conceptual question) → state that the available artmind data does not answer the question.
9. `text2sql` returns no rows but data should exist → re-run with `--dryRun` and compare the generated SQL against `db schema`'s column list; rephrase the question naming the exact table/column, or fall back to `db sql` with hand-written SQL if the phrasing keeps generating the wrong filter.
10. `resolve-key` returns no confident match → widen `--topK`, or drop `--column` to resolve against the graph only (the phrase may be a graph entity name with no structured-column analogue).
11. `hierarchy` returns empty rows/edge_candidates → re-run discovery mode to confirm no edges exist at all → if still empty, the relationship may only be stored as a property (e.g. `reports_to` on the class) — fall back to `pattern1` on that class; see `references/hierarchy.md`.

## Answer Style

Answer directly and naturally. Keep provenance concise: say whether the answer comes from graph relationships, entity properties, chunk text, or a combination. Mention unresolved ambiguity when you picked between candidate entities. Do not expose raw JSON unless asked.

If an answer came from reading a note directly (possible on the admin console)
rather than from `artmind query` output, say plainly that the graph does not hold
it, and suggest the operator check `artmind ingest empty-extractions` and
re-extract the note — a fact only the note holds is missed by every later query.

### Provenance in web UIs

Ground every claim in the query output and name the relevant document naturally in
the answer (for example, "per Delivery Leads.md"). Do **not** create a `### Sources`
section or calculate a link target: chat-ui and admin-ui append a deterministic,
passage-level source block from the final query trace after the turn completes.

Never write an `obsidian://` URI or invent a file path. If an interface that does not
have automatic provenance needs citations, its host owns that presentation policy.
