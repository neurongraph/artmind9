---
name: artmind-query
description: The artmind system stores information from ingested documents. Using this skill respond to natural-language questions from the user given a particular domain. These questions would be in the form of Q&A between the user and artmind
---

# artmind Query

Use this skill to answer user questions over an artmind domain through the deterministic query CLI. The skill provides the reasoning layer; the CLI provides templated graph/vector retrieval and JSON output.

## Grounding Rule

Use only the structured KG data and chunk text returned by artmind query commands. If the data is insufficient, say so clearly. Do not invent entities, events, relationships, motivations, or source details not present in the returned data.

## Required Inputs

- `domain`: Ask for it if the user did not provide one.
- `question`: The natural-language question to answer. If the user asks multiple questions, break them down and answer each.

## Fixed Structural Schema

Four structural node types with fixed relationships, identical across domains:

- `(:DocChunk)-[:PART_OF]->(:Document)` — chunk belongs to a document
- `(:Entity)-[:EXTRACTED_FROM]->(:DocChunk)` — entity was extracted from a chunk; this is also the only edge to use to find which chunk/document an entity came from (there is no separate `(:DocChunk)-[:MENTIONS]->(:Entity)` edge — ingestion never writes one)
- `(:UserChat)-[:MENTIONS]->(:Entity)` — user chat mentions an entity

Key properties: `Document` (id, name, path, domain), `DocChunk` (id, name, doc_id, text, domain), `UserChat` (id, raw_text, domain, session_id, created_by, created_at), `Entity` (id, name, entity_class, domain, description, type).

Every extracted entity carries the `:Entity` label plus a class label (e.g. `PERSON`). Entity-to-Entity relationship types are domain-specific — always check metadata. For document/chunk questions use `PART_OF` (not EXTRACTED_FROM); `pattern10` does this deterministically.

Add `--compact` to every command — it halves the JSON you must read.

## Structured store (`db`)

A domain can also have tabular data (csv/xlsx ingested via `artmind ingest`) living
in a separate SQL store, independent of the graph above. Rows never become graph
nodes — the graph only ever holds a catalogue of what tables/columns exist.

- `artmind db list --domain <d> --compact` — which structured tables (if any)
  exist for this domain.
- `artmind db schema <table> --compact` — columns, types, and (once confirmed)
  column→entity-class mappings for a table.
- `artmind db sql "<SQL>" --compact` — raw read-only SQL, no LLM involved.
- `artmind db mappings <table> --compact` — review proposed vs confirmed
  column→entityClass mappings for a table (registry rows, not a file). Bulk-confirm
  everything proposed with `--acceptProposed`, or manage one mapping at a time with
  the `set`/`confirm`/`clear` subcommands (`db mappings <table> set --column c
  --entityClass PRODUCT`, `... confirm --column c --entityClass PRODUCT`,
  `... clear --column c` or `... clear` for all).

Full graph/SQL/hybrid routing logic lands in a later increment of this skill —
for now, check `db list` for the domain when a question is clearly analytical
("average/total/count by X") and the graph patterns below don't fit.

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

Document/chunk rows and `metadata` now carry `valid_from`/`valid_to`/`superseded_by` — use them to judge document currency.

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
`timeline`, `conflicts`, `metadata`, `structural-metadata` and `entity-listing` all live
under the `graph` sub-group (`artmind query graph <cmd>`); `entity-context`, `chunks`,
`vector-text`, `entity-resolve` and `domains-overview` sit directly under `query`
(`artmind query <cmd>`). Copy the prefix as written — do not infer it.

| Question shape | Command |
|---|---|
| "Tell me about X / X's role / why did X…" — facts + relationships + source text in ONE call | `artmind query entity-context --entityId <id> [--includeChunks 5]` |
| History / sequence of events / "when did X change / start / end" for ONE entity | `artmind query graph timeline --entityId <id>` — dated relationships (`event_at`/`valid_from`/`valid_to`) in chronological order |
| List entities of a class | `artmind query graph pattern1 --entityClass <LABEL> [--limit N]` |
| "Main / key / most important / top" entities | `artmind query graph pattern9 --entityClass <LABEL> --topN 5` (default ranks by entity-entity links; `--degreeMode mentions` ranks by how often sources mention it) |
| Facts/properties of named entities (no text needed, e.g. comparing many) | `artmind query graph pattern2 --entityIdList <id>` (or `--entityNameList`) |
| Properties + relationship summary | `artmind query graph pattern3 --entityIdList <id>` |
| Full one-hop neighborhood / contextual role | `artmind query graph pattern4 --entityClass <LABEL> --entityId <id>` |
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
- **timeline vs entity-context/pattern3/pattern4**: entity-context and patterns 2-4 give current-state facts and one-hop structure; `graph timeline --entityId <id>` instead reconstructs *change over time* for that one entity — its dated relationships sorted chronologically. Use it for "history of X", "what changed", or "when did X start/end", not for a general one-hop snapshot. It only has an entity's edges, no chunk text — pair it with `entity-context`/`chunks` if the question also needs narrative evidence at a given point in time.
- Patterns 2/3/4 return `doc_sources` and `chat_sources` — use these ids to know *where* a fact came from, and pull the actual text deterministically with `artmind query chunks --domain <d> --idList <chunk_id> [--expand 1]` (never re-search for text you already have ids for). `--expand 1` adds the adjacent chunks of the same document when one chunk is too little context.
- All commands accept repeatable `--domain` (comma-splittable) and roll sub-domains up.
  Rows carry `.domain` on chunks/documents — every fact you state must be attributed
  to BOTH its document name AND its domain.
- **Default to `--asOf today` on every retrieval** — without it there is NO temporal
  filter, and superseded documents and chunks surface alongside current ones. Omit it
  (or pass a past ISO date, e.g. `--asOf 2026-01`) only when the question is explicitly
  historical: "what did the policy say in January", "history of…", "previous version",
  "what changed". Untimed knowledge is always visible either way. EXCEPTION: pattern5
  and pattern10 cannot currency-scope their results and ignore `--asOf` — their JSON
  then carries `asOf_ignored: true`; judge currency yourself from the returned
  `valid_to`/`superseded_by` fields.

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

First check for already-materialized conflicts. Two sources, cheapest first:

1. **Free check:** if Retrieve already called `entity-context`/`pattern3`/`pattern4` on
   the resolved entity, scan the `connections` it returned for an edge of type
   `CONFLICTS_WITH` — those relationship-agnostic patterns fetch every one-hop edge, so
   a live conflict is often already sitting in context with zero extra calls. This only
   fires if you resolved to the *specific claim-bearing entity* (e.g. "Mortgage
   Statement"), not an umbrella container (e.g. the policy or process that mentions it)
   — CONFLICTS_WITH sits on the concrete entities being compared, not their containers.
2. **Dedicated lookup**, for anything step 1 didn't cover or when you haven't already
   called an entity-anchored pattern:

```bash
artmind query graph conflicts --domain <d1> --domain <d2> --entityId <id> --compact
```

This matches the `CONFLICTS_WITH` edge between entities directly, so it still finds a
conflict even if the heavier `Conflict`/`EVIDENCE` node it was minted with has since
been deleted — check each row's `materialized` flag: `true` means `claim_a`/`claim_b`/
`evidence`/`severity` are populated straight from the `Conflict` node; `false` means
only `aspect` and the two entities are known, so pull grounding yourself via `chunks`/
`vector-text` on those entities before stating the claims. Either way, surface `aspect`
and both entities' `name`/`domain`; never assert `claim_a`/`claim_b` text that isn't
actually present on a `materialized: true` row.

**Fan-out caveat:** one real disagreement (e.g. a document-tier reclassification) can
produce many pairwise conflict rows sharing the same root cause across different
document pairs in the same tier bucket — group rows by shared `aspect`/entity-class
pattern and report the *underlying* disagreement once, not each pairwise row separately.

Then independently compare the retrieved claims (below) to catch conflicts introduced
by new documents since the last detect-conflicts run — materialized conflicts are a
snapshot, not a live guarantee.

After grounding, compare quantitative/authority claims across the retrieved
documents and domains (no extra LLM calls — the evidence is already in context).
When two sources disagree, surface BOTH claims with BOTH provenances in this format:

> Sources disagree: policy_complaints.md (banking_policy) says X; escalation_matrix.md
> (banking_sop_guides) says Y.

Never average, reconcile silently, or drop one side. If retrieval returned only one
side, re-run Ground with the sibling domains from Route before concluding.

Qualify claims by time: report present-tense answers "as of <date>, source A says X".
A claim whose document is superseded (has `superseded_by` / a `valid_to` in the past)
is HISTORY, not a live disagreement — say so. Before treating a materialized Conflict
as live, verify both documents' valid-time windows overlap and neither supersedes the
other — detect-conflicts' LLM adjudication tries to catch this at detection time, but
it isn't a structural guarantee, so re-check at query time using each side's
`valid_to`/`superseded_by`.

## Fallback Ladder

1. Thin results in the chosen domain → re-run with sibling domains from Route before concluding data is absent. (Same fix as Adjudicate's "only one side retrieved" case — apply it as soon as results look thin, not only after a disagreement surfaces.)
2. pattern6 empty → pattern5 `--mode shortest`.
3. entity-context/pattern chunks too thin → `chunks --idList <more_chunk ids> --expand 1` before falling back to search.
4. Pattern output empty or too thin → vector-text.
5. text2cypher returns no rows but data should exist → run `artmind query graph structural-metadata`, then `artmind query graph text2cypher --dry-run` and compare relationship names; rephrase the question naming the correct relationship (e.g. "use PART_OF to connect DocChunk to Document").
6. text2cypher generates invalid Cypher → vector-text.
7. vector-text sparse or weak → state that the available artmind data does not answer the question.

## Answer Style

Answer directly and naturally. Keep provenance concise: say whether the answer comes from graph relationships, entity properties, chunk text, or a combination. Mention unresolved ambiguity when you picked between candidate entities. Do not expose raw JSON unless asked.
