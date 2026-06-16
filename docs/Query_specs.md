# Knowledge Graph Query Patterns

A library of canonical Cypher query patterns for Q&A over a domain-scoped knowledge graph. The agent harness selects a pattern based on the user's question, fills in parameters, runs the query via the `artmind query graph` CLI, and passes the structured result to an LLM for natural-language synthesis.

The Cypher shown here mirrors the implementation in `artmind/graph_query.py` (patterns), `artmind/vector_query.py` (vector/full-text/entity-resolve), and `artmind/text2cypher.py`. If they ever disagree, the code is the source of truth.

## Conventions

All patterns assume:

- Every node has a `domain` property. **Every query filters on `$domain` with sub-domain rollup**: `(n.domain = $domain OR n.domain STARTS WITH ($domain + '.'))` — querying `fiction` includes `fiction.thriller` etc. (Abbreviated below as `<domain(n)>`.)
- Extracted entities carry the `:Entity` label **plus** a class label (e.g. `:PERSON:Entity`). All name-based matches are scoped to `:Entity` so Document/DocChunk/UserChat nodes can never match an entity name.
- Name comparisons use **case-insensitive substring matching** via `toLower(...) CONTAINS toLower(...)`. This is robust to user phrasing but can fan out ("Holmes" matches both "Sherlock Holmes" and "Mycroft Holmes").
- **Exact-id matching**: every name option has an id alternative (`--entityId`, `--entityId1/2`, `--entityIdList`) that pins the node via `e.id = $entityId`. When both are supplied, the id wins. Resolve names to ids first (see Entity resolution below), then query by id.
- `entityClass` is a node label as stored by ingestion. Artmind derives labels from extracted `entity_class` values by replacing non-alphanumeric characters with `_` and uppercasing the result, so `Character` becomes `CHARACTER` and `project role` becomes `PROJECT_ROLE`.
- Neighborhood/relationship traversals are restricted to `(t:Entity)` so structural edges (`MENTIONS`, `EXTRACTED_FROM`) never leak full chunk text into connection lists. Source attribution is returned separately as `doc_sources` / `chat_sources`.
- Results are returned as structured objects suitable for JSON serialization to the LLM synthesis step. Embedding fields are always stripped.

## Structural schema (fixed across domains)

- `(:DocChunk)-[:PART_OF]->(:Document)`
- `(:Entity)-[:EXTRACTED_FROM]->(:DocChunk)`
- `(:DocChunk)-[:MENTIONS]->(:Entity)`
- `(:UserChat)-[:MENTIONS]->(:Entity)`

Entity-to-Entity relationship types are domain-specific; discover them via metadata.

## Understanding the graph: metadata and entities

First understand the graph schema and the list of entities with their names and labels, so the right parameters can be supplied to the patterns.

### Graph metadata

```
uv run artmind query graph metadata --domain $domain
```

Returns node labels with property names and distinct `type` values, plus relationship types with their from/to label connections — all domain-rolled-up.

### Structural metadata

```
uv run artmind query graph structural-metadata --domain $domain
```

Compact alternative: Document names and Document/DocChunk/UserChat/Entity counts plus structural relationship counts. Use for document/chunk/count questions and to verify relationship names when debugging text2cypher.

### Entity listing

```cypher
MATCH (n:Entity)
WHERE <domain(n)> AND n.name IS NOT NULL
  AND ($nameFilter IS NULL OR toLower(n.name) CONTAINS toLower($nameFilter))
UNWIND labels(n) AS label
WITH label, n.type AS type, collect(DISTINCT n.name) AS names
RETURN label, collect({type: type, names: names}) AS typeGroups
ORDER BY label
```

**Options:**
- `--nameFilter TEXT` — optional substring to fuzzy-match entity names (case-insensitive). Narrows the listing when the domain has many entities.
- `--countAll` — include `total_entities` (unfiltered count) in the output. Use this to gauge listing size before fetching all names.

```
uv run artmind query graph entity-listing --domain $domain
uv run artmind query graph entity-listing --domain $domain --countAll --compact
uv run artmind query graph entity-listing --domain $domain --nameFilter $searchTerm
```

### Entity resolution (name/description → canonical entity ids)

```
uv run artmind query entity-resolve --domain $domain --topK 5 "$reference"
```

Resolves a free-text reference — a name fragment ("Holmes") or a pure description ("the detective") — to ranked canonical entities. Combines two legs via Reciprocal Rank Fusion:

1. **Full-text leg**: `db.index.fulltext.queryNodes('entity_name_ft', $query)` over entity name + description (input is sanitized of Lucene special characters).
2. **Vector leg**: cosine similarity against the `entity_embedding` vector index (entities are embedded as `name: description` at write time; backfill older graphs with `artmind ingest embed-entities --domain $domain`).

Each row returns `id`, `name`, `entity_class`, `type`, `description`. Feed the chosen `id` into pattern `--entityId*` options. The vector leg degrades gracefully (fulltext-only) if the index or embeddings are missing.

## Pattern Selection Guide

1. **List of one type of thing?** → Pattern 1 (unranked) or Pattern 9 (ranked — "main", "key", "important", "top").
2. **About one or more named entities?** → Pattern 2 (just info), Pattern 3 (info + relationship summary), or Pattern 4 (full neighborhood).
3. **Connection between two named entities?** → Pattern 6 for *existence/type of a direct link*; Pattern 5 for the *nature/quality* of the relationship or when no direct edge exists. (Pattern 6 edge properties are often thin — for "what did X think of Y" / "how did X and Y interact", go straight to Pattern 5, then ground with vector-text.)
4. **Which entities of class X are connected to entity Y?** → Pattern 8.
5. **Entity referenced by description rather than name?** → entity-resolve (or Pattern 7) first, then Pattern 4 on the resolved id.
6. **All text of a document / summarize a document?** → Pattern 10.
7. **Aggregations, custom filters, multi-hop combinations none of the above cover?** → text2cypher.

---

## Pattern 1 — List entities of a class

**When to use:** Enumeration of all entities of a given class within the domain. No ranking.

**Example questions:**
- What are the characters in the novel?
- List all the places mentioned in the story.

**Cypher:**
```cypher
MATCH (e:$entityClass)
WHERE <domain(e)>
RETURN e {.*, label: labels(e)} AS entityData
ORDER BY e.name
LIMIT $limit
```

**CLI:**
```
uv run artmind query graph pattern1 --domain $domain --entityClass $entityClass [--limit 200]
```

---

## Pattern 2 — Info on one or more named entities (+ sources)

**When to use:** The user names one or more entities and wants their properties. Includes `doc_sources` / `chat_sources` attribution.

**Example questions:**
- Tell me briefly about Sherlock Holmes.
- Give me the descriptions of Holmes, Watson, and Rucastle.

**Cypher:**
```cypher
MATCH (e:Entity)
WHERE <domain(e)>
  AND <selector>            // e.id IN $entityIdList
                            // or ANY(n IN $entityNameList WHERE toLower(e.name) CONTAINS toLower(n))
OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
WITH e, collect(DISTINCT chunk { .id, .name, .doc_id, source_type: 'document' }) AS doc_sources
OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
RETURN e {.*, label: labels(e)} AS entityData,
       doc_sources,
       collect(DISTINCT chat { .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }) AS chat_sources
ORDER BY entityData.name
```

**CLI:**
```
uv run artmind query graph pattern2 --domain $domain --entityNameList $entityName
uv run artmind query graph pattern2 --domain $domain --entityIdList $entityId
```

---

## Pattern 3 — Entity + lightweight relationship summary (+ sources)

**When to use:** Entity plus a one-hop summary of who/what it's connected to, without full neighbor properties. Cheaper than Pattern 4 — good default for "tell me about X" when X is highly connected. Connections traverse entity-entity edges only.

**Cypher:**
```cypher
MATCH (e:Entity)
WHERE <domain(e)> AND <selector>
OPTIONAL MATCH (e)-[r]-(t:Entity)
WHERE <domain(t)>
WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE {
  type: type(r),
  properties: properties(r),
  target: {name: t.name, label: labels(t)}
} END) AS connections
OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
WITH e, connections, collect(DISTINCT chunk { .id, .name, .doc_id, source_type: 'document' }) AS doc_sources
OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
RETURN properties(e) AS entityData, connections, doc_sources,
       collect(DISTINCT chat { .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }) AS chat_sources
ORDER BY entityData.name
```

**CLI:**
```
uv run artmind query graph pattern3 --domain $domain --entityNameList $entityName
uv run artmind query graph pattern3 --domain $domain --entityIdList $entityId
```

---

## Pattern 4 — Entity + full neighborhood (+ sources)

**When to use:** Rich picture of one entity including full properties of connected entities (entity-entity edges only — chunk text never appears in connections). Use when the answer requires understanding the entity in context.

**Cypher:**
```cypher
MATCH (e:$entityClass)
WHERE <domain(e)> AND <selector>     // e.id = $entityId or name CONTAINS
OPTIONAL MATCH (e)-[r]-(t:Entity)
WHERE <domain(t)>
WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE {
  rel_type: type(r),
  rel_properties: properties(r),
  connected_to: {label: labels(t), data: properties(t)}
} END) AS connections
OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
WITH e, connections, collect(DISTINCT chunk { .id, .name, .doc_id, source_type: 'document' }) AS doc_sources
OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
RETURN properties(e) AS entityData, connections, doc_sources,
       collect(DISTINCT chat { .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }) AS chat_sources
ORDER BY entityData.name
```

**CLI:**
```
uv run artmind query graph pattern4 --domain $domain --entityClass $entityClass --entityName $entityName
uv run artmind query graph pattern4 --domain $domain --entityClass $entityClass --entityId $entityId
```

---

## Pattern 5 — Paths between two entities (shortest or all-within-depth)

**When to use:** The user asks how two entities are related — the nature/quality of the relationship, or when no direct edge exists. **Paths are constrained to entity space** (`all(x IN nodes(p) WHERE x:Entity)`); without this, the shortest path between any two co-mentioned entities degenerates to a 2-hop co-mention through a DocChunk.

- `mode=shortest` — single shortest entity path
- `mode=all` — up to 3 shortest entity paths within depth 5

**Example questions:**
- How is Miss Hunter connected to Mr. Fowler? *(mode=shortest)*
- What are all the ways Sherlock Holmes is linked to the Copper Beeches estate? *(mode=all)*

**Cypher (mode=shortest):**
```cypher
MATCH p = shortestPath((e:$entityClass1)-[*..5]-(t:$entityClass2))
WHERE <domain(e)> AND <domain(t)>
  AND <selector1> AND <selector2>
  AND all(x IN nodes(p) WHERE x:Entity)
RETURN reduce(acc = [{label: labels(nodes(p)[0]), data: properties(nodes(p)[0])}],
  i IN range(0, length(p)-1) |
  acc + [
    {rel: type(relationships(p)[i]), data: properties(relationships(p)[i])},
    {label: labels(nodes(p)[i+1]), data: properties(nodes(p)[i+1])}
  ]
) AS interleavedPath   // flat [node, rel, node, rel, ..., node]
```

**Cypher (mode=all):**
```cypher
MATCH (e:$entityClass1), (t:$entityClass2)
WHERE <domain(e)> AND <domain(t)>
  AND <selector1> AND <selector2>
WITH e, t
MATCH p = (e)-[*1..5]-(t)
WHERE all(x IN nodes(p) WHERE x:Entity)
WITH p
ORDER BY length(p) ASC
LIMIT 3
RETURN ... interleavedPath   // same shape as above
```

**CLI:**
```
uv run artmind query graph pattern5 --domain $domain --mode {shortest|all} \
  --entityClass1 $c1 --entityClass2 $c2 \
  --entityName1 $n1 --entityName2 $n2        # or --entityId1 / --entityId2
```

---

## Pattern 6 — Direct relationships between two entities

**When to use:** Confirming whether a *direct* edge exists between two named entities and what type/properties it carries. Fastest, most precise — but edge properties are often thin. For relationship *nature/quality* questions, prefer Pattern 5. Fall back to Pattern 5 (mode=shortest) if this returns nothing.

**Cypher:**
```cypher
MATCH (e1:Entity)-[r]-(e2:Entity)
WHERE <domain(e1)> AND <domain(e2)>
  AND <selector1> AND <selector2>
RETURN type(r) AS relType,
       properties(r) AS relProps,
       startNode(r).name AS fromEntity,
       endNode(r).name AS toEntity,
       labels(startNode(r)) AS fromLabels,
       labels(endNode(r)) AS toLabels
ORDER BY relType, fromEntity, toEntity
```

**CLI:**
```
uv run artmind query graph pattern6 --domain $domain --entityName1 $n1 --entityName2 $n2
uv run artmind query graph pattern6 --domain $domain --entityId1 $id1 --entityId2 $id2
```

---

## Pattern 7 — Search entities by name or description fragment (Lucene)

**When to use:** The user references an entity by a phrase or descriptor rather than its canonical name. Backed by the `entity_name_ft` fulltext index over name + description, ranked by Lucene relevance. Input is sanitized of Lucene special characters (`'St Bartholomew's (Hospital)?'` is safe). For descriptive references, `entity-resolve` is usually better (adds the vector leg); Pattern 7 is the fulltext-only variant.

**Cypher:**
```cypher
CALL db.index.fulltext.queryNodes('entity_name_ft', $searchTerm)
YIELD node AS e, score AS ftScore
WHERE <domain(e)>
RETURN e {.*, label: labels(e)} AS entityData
ORDER BY ftScore DESC, e.name
LIMIT $limit
```

**CLI:**
```
uv run artmind query graph pattern7 --domain $domain --searchTerm $searchTerm [--limit 10]
```

---

## Pattern 8 — Entities of class X connected to entity Y

**When to use:** Filtered list — all entities of a class connected (entity-entity edges) to a specific named entity.

**Example questions:**
- Which characters interact with the Copper Beeches estate?
- What places is Miss Hunter associated with?

**Cypher:**
```cypher
MATCH (e:$entityClass)-[r]-(t:Entity)
WHERE <domain(e)> AND <domain(t)>
  AND <selector(t)>          // t.id = $entityId or toLower(t.name) CONTAINS ...
RETURN e {.*, label: labels(e)} AS entityData,
       type(r) AS relType,
       properties(r) AS relProps
ORDER BY e.name, relType
```

**CLI:**
```
uv run artmind query graph pattern8 --domain $domain --entityClass $entityClass --entityName $entityName
uv run artmind query graph pattern8 --domain $domain --entityClass $entityClass --entityId $entityId
```

---

## Pattern 9 — Top-N entities of a class by connection count (centrality)

**When to use:** "Main", "key", "important", "top" entities of a class. Three degree modes:

- `relations` *(default)* — entity-entity relationships only: structural connectivity in the story/domain.
- `mentions` — incoming `MENTIONS` edges from chunks/chats: how often sources talk about the entity (salience).
- `all` — every edge including structural ones (legacy behavior).

**Cypher (degreeMode=relations):**
```cypher
MATCH (e:$entityClass)
WHERE <domain(e)>
OPTIONAL MATCH (e)-[r]-(:Entity)
WITH e, count(r) AS degree
RETURN e {.*, label: labels(e), degree: degree} AS entityData
ORDER BY degree DESC, e.name
LIMIT $topN
```
(`mentions` uses `OPTIONAL MATCH (e)<-[r:MENTIONS]-()`; `all` uses `OPTIONAL MATCH (e)-[r]-()`.)

**CLI:**
```
uv run artmind query graph pattern9 --domain $domain --entityClass $entityClass --topN 5 [--degreeMode relations|mentions|all]
```

---

## Pattern 10 — All text chunks of a named document

**When to use:** "Retrieve all chunks of document X" or "summarize document X". Deterministic — always uses the correct `PART_OF` relationship.

**Cypher:**
```cypher
MATCH (c:DocChunk)-[:PART_OF]->(d:Document)
WHERE <domain(d)>
  AND toLower(d.name) CONTAINS toLower($documentName)
RETURN d { .id, .name, .path } AS document,
       c { .id, .name, .doc_id, .text } AS chunk
ORDER BY c.name
```

**CLI:**
```
uv run artmind query graph pattern10 --domain $domain --documentName $documentName
```

---

## text2cypher — LLM-generated Cypher

**When to use:** The question is clearly a graph query but none of patterns 1–10 fit — complex aggregations, custom filtering/grouping, multi-entity traversals.

The prompt includes a hardcoded structural schema (PART_OF / EXTRACTED_FROM / MENTIONS), the live domain schema and entity listing, and rules requiring: domain scoping on every unbound node, explicit `:Entity` labels, and `all(x IN nodes(p) WHERE x:Entity)` in variable-length entity paths. Generated Cypher is validated read-only — `CREATE/DELETE/DETACH/SET/REMOVE/MERGE/DROP/FOREACH`, `LOAD CSV`, APOC write procedures, and `CALL {…} IN TRANSACTIONS` are rejected.

**CLI:**
```
uv run artmind query graph text2cypher --domain $domain "$question"
uv run artmind query graph text2cypher --domain $domain --dry-run "$question"   # inspect before executing
```

If it returns no rows but data should exist: run `structural-metadata`, re-run with `--dry-run`, compare relationship names, and rephrase naming the correct relationship.

---

## Question-to-Pattern Mapping (Reference)

For the Sherlock Holmes "The Copper Beeches" KG, here is how the seed questions map to patterns. Use this as a worked example when routing new questions.

| Question | Pattern | Parameters |
|---|---|---|
| Tell me about the character of Sherlock Holmes | **4** | `entityClass=PERSON, entityName=Sherlock Holmes` |
| How is his relationship with Dr. Watson | **5** (mode=shortest) | nature/quality question — go straight to 5 |
| Does Holmes know Miss Hunter, and how directly? | **6**, fallback **5** | `entityName1=Holmes, entityName2=Hunter` |
| What does Holmes think of Miss Hunter | **5** + vector-text | path context plus narrative evidence |
| Who is the real villain of the story | **9** then **4** | rank `PERSON`, inspect top results |
| What are the key settings | **9** | `entityClass=LOCATION, topN=5` |
| What is the importance of the coil of hair | **entity-resolve** (or **7**) then **4** | resolve "coil of hair" → id |
| Who are the main characters and brief descriptions | **9** then **2** | rank `PERSON`, then fetch info by id list |
| Summarize The Copper Beeches | **10** | `documentName=Copper Beeches` |
| How many chunks per document? | **text2cypher** | aggregation |
| How did Alice Rucastle die | **4** + vector-text | neighborhood plus chunk evidence |

> **Note:** The current extraction does not produce explicit event-chain nodes (`:Event` with `NEXT`/`CAUSED` edges). Narrative-reconstruction questions like "how did X happen" must be answered via Pattern 4 (full neighborhood) or Pattern 5 mode=all, grounded with vector-text chunk evidence. The synthesizing LLM reconstructs the sequence.

## Output Contract for the Synthesizer

Every pattern returns JSON. The harness should pass the raw result to the LLM along with the original user question and a brief instruction:

> "Using only the structured KG data below, answer the user's question. If the data is insufficient, say so. Do not invent entities or relationships not present in the data."

This keeps synthesis grounded and prevents hallucination of facts not in the extracted graph.

## Vector + Full-Text Search (combined)

Use when a question needs text-grounded narrative evidence or when graph patterns return insufficient data.

**CLI:**
```
uv run artmind query vector-text --domain $domain --topK 5 "$question"
```

Runs two legs and combines them with Reciprocal Rank Fusion:

1. **Vector leg** — `chunk_embedding` / `user_chat_embedding` vector indexes, cosine similarity on the embedded question.
2. **Full-text leg** — `chunk_text_ft` / `user_chat_text_ft` Lucene indexes via `db.index.fulltext.queryNodes` (BM25-ranked, case-insensitive; input sanitized of Lucene special characters, terms OR-combined).

Returns both document chunks (`source_type: "document"`) and user chats (`source_type: "user_chat"`), domain-rolled-up, embeddings stripped. No APOC required on the query path.
