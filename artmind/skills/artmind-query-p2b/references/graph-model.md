# Graph model and the pattern commands
Read when a result carries `guide: graph-model` (any `pattern*`, `text2cypher`), when you write Cypher, or when orient was cut.

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
`superseded_by` on the superseded side — see Adjudicate in `references/conflicts.md` for how to read it at
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
and is therefore current by construction; only `pattern10 --includeHistory` (see the command table below)
reaches into history at all, and a document row's `supersedes` names the version it replaced.

For document/chunk questions use `PART_OF` (not `EXTRACTED_FROM`); `pattern10` does this
deterministically.

Add `--compact` to every command — it drops the pretty-printing whitespace. It does not
shrink what a command returns; bounding that is each command's own options (see
`references/hubs.md`).

A domain can also have tabular data (csv/xlsx ingested via `artmind ingest`) living
in a separate SQL store, independent of the graph above — see Store routing in `references/structured-store.md`.


### 2. Discover — only when orient was cut

`orient --domain` already holds what Discover used to fetch. Run `graph metadata`,
`entity-listing --countAll` or `structural-metadata` only when a `cuts` entry names them
as the `rest`, or for a document list (`structural-metadata`) — never as an opener.
Document/chunk rows carry `valid_from`/`valid_to`/`superseded_by`; use them to judge currency.


### 4. Retrieve — run the right pattern

Every command below is written in full. The `pattern*` commands, `text2cypher`,
`timeline`, `conflicts`, `metadata`, `structural-metadata` and
`entity-listing` all live under the `graph` sub-group (`artmind query graph <cmd>`);
`views`, `entity-context`, `entity-history`, `document-history`, `chunks`, `vector-text`, `entity-resolve` and
`orient` sit directly under `query` (`artmind query <cmd>`). Copy the prefix
as written — do not infer it.

This table is for questions the Views check found no view for. A question a saved view
covers is answered by `artmind query views run <name>` (see `references/views.md`), not by a row below.

| Question shape | Command |
|---|---|
| Any question matching a saved view's `summary`/`examples` | `artmind query views run <name> --param <p>=<value> --render markdown` — see `references/views.md` |
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
| Search entities by name/description fragment | `artmind query graph pattern7 --shape brief --searchTerm "<fragment>"` (Lucene-backed; punctuation is stripped automatically). Each row's `documents` (up to 3 source documents, empty for entities built from tables) feed the Sources list |
| Entities of class X connected to entity Y ("CLASS connected to ENTITY") | `artmind query graph pattern8 --entityClass CLASS --entityId <id> --all` (add `--via mentions` for everyone named alongside it) |
| All chunks of / summarize a document | `artmind query graph pattern10 --documentName "<name>" [--limit N] [--offset N]` — add `--includeHistory` for retired/superseded versions; zero rows with `history_matches` means only a retired version matches |
| Aggregations, custom filters, multi-hop combinations none of the above cover | `artmind query graph text2cypher "<question>"` — run `--dryRun` first to inspect the Cypher, then run it without `--dryRun` (a dry run executes nothing and returns no rows); rows are capped (`--maxRows`, default 200, `rows_total`/`truncated` alongside) since the generated Cypher carries no LIMIT of its own |

Every command also needs `--domain <domain>` (repeatable) and `--compact`, omitted above for brevity.

Routing notes:
- **entity-context vs pattern4**: for a question anchored on ONE resolved entity that
  needs evidence text, `artmind query entity-context --shape brief --domain <d> --entityId <id>
  --query "<question>"` replaces the pattern4 + Ground sequence — it returns
  properties, one-hop relationships, and the text of the entity's source chunks most
  relevant to the question (the first `--includeChunks` with full text, the next ids
  in `more_chunks`, fetchable via `chunks --idList`). Always pass the user's question
  (as the last argument, or `--query`): without it, chunks come newest-first and connections by evidence
  weight, which for a busy entity rarely includes the one that answers. Its output is
  bounded — see `references/hubs.md`. Use pattern4 when you only need structure, or
  patterns 2/3 for several entities at once.
- **pattern6 vs pattern5**: pattern6 answers "is there a direct relationship and what type". For the *nature or quality* of a relationship, use pattern5 — then ground with vector-text for narrative evidence.
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
  --relType <type> --query "<question>"` instead (see `references/hubs.md`) so the edge
  that answers the question sorts to the top rather than needing a bigger page.
- **Counts and lists.** Answer a count only from `complete: true`; otherwise say N of M. For "every year / each time" questions use `survey --by year` (or `month`): it reports every period with a hit. `pattern10` pages with `--offset` when `truncated`. For "which / how many X did Y …", list the set with `pattern8 --entityId <Y> --entityClass X --via mentions --all`: `linked: true` rows have a relation to Y (check it is the relation the question asks about); `linked: false` rows were only named in the same notes — confirm each with `artmind query chunks --idList <its chunk_ids>` before counting it.
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
