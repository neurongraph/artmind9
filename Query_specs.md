# Knowledge Graph Query Patterns

A library of canonical Cypher query patterns for Q&A over a domain-scoped knowledge graph. The agent harness selects a pattern based on the user's question, fills in parameters, runs the query via the `artmind query graph` CLI, and passes the structured result to an LLM for natural-language synthesis.

## Conventions

All patterns assume:

- Every node has a `domain` property scoping it to one source (e.g. one novel, one corpus). **Every query filters on `$domain`.**
- Name comparisons use **case-insensitive substring matching** via `toLower(...) CONTAINS toLower(...)`. This makes the patterns robust to user phrasing ("sherlock" vs "Sherlock Holmes", "miss hunter" vs "Violet Hunter").
- `entityClass` is a node label (e.g. `Character`, `Place`, `Object`, `Theme`).
- Results are returned as structured objects suitable for JSON serialization to the LLM synthesis step.

## Understanding the Graph metadata and entities
First understand the graph schema, and also the list of entities with their names and labels. You can match the entities from the user query to this information so that the right set of parameters can be provided for the query patterns

### Graph metadata
**Cypher**
```cypher
# Cypher for Graph schema metadata
```cypher
CALL () {
  // 1. Nodes part: Filter by domain
  MATCH (n)
  WHERE n.domain = $domain
  UNWIND labels(n) AS label
  WITH label, keys(n) AS nodeKeys, n.type AS typeVal
  UNWIND nodeKeys AS propName
  RETURN "nodes" AS category, 
         label AS name, 
         collect(DISTINCT propName) AS propertyNames,
         collect(DISTINCT typeVal) AS distinctTypes,
         null AS connections
UNION
  // 2. Relationships part: Ensure both connected nodes are in the domain
  MATCH (s)-[r]->(e)
  WHERE s.domain = $domain AND e.domain = $domain
  WITH type(r) AS relType, labels(s) AS fromLabels, labels(e) AS toLabels, keys(r) AS relKeys
  UNWIND relKeys AS propName
  RETURN "relationships" AS category, 
         relType AS name, 
         collect(DISTINCT propName) AS propertyNames,
         null AS distinctTypes, 
         collect(DISTINCT {from: fromLabels, to: toLabels}) AS connections
}
RETURN category, name, propertyNames, distinctTypes, connections

```
**CLI:**
```
artmind query graph metadata --domain $domain --pattern pattern1
```
### Entity listing
**Cypher**

```cypher
MATCH (n)
WHERE n.domain = $domain
UNWIND labels(n) AS label
WITH label, n.type AS type, collect(DISTINCT n.name) AS names
RETURN label, collect({type: type, names: names}) AS typeGroups
```

**CLI:**
```
artmind query graph entity_listing --domain $domain
```

## Pattern Selection Guide

When a question arrives, the harness should pick a pattern using this decision flow:

1. **Does the question ask for a list of one type of thing?** → Pattern 1 (unranked) or Pattern 9 (ranked by importance — when words like "main", "key", "important", "top" appear).
2. **Does the question ask about one or more named entities?** → Pattern 2 (just info), Pattern 3 (info + relationship summary), or Pattern 4 (full neighborhood).
3. **Does the question ask about the connection between two named entities?** → Pattern 6 (direct edges only — fastest), Pattern 5 (path-based — when no direct edge exists or path context matters).
4. **Does the question ask which entities of class X are connected to entity Y?** → Pattern 8.
5. **Does the question reference an object/clue/theme by description rather than name?** → Pattern 7 first to find the node, then Pattern 4 on the result.

---

## Pattern 1 — List entities of a class

**When to use:** The user wants an enumeration of all entities of a given class within the domain. No ranking, no filtering by connection.

**Example questions:**
- What are the characters in the novel?
- List all the places mentioned in the story.
- What objects appear in the book?

**Cypher:**
```cypher
MATCH (e:$entityClass)
WHERE e.domain = $domain
RETURN e {.*, label: labels(e)} AS entityData
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern1 --entityClass $entityClass 
```

---

## Pattern 2 — Info on one or more named entities

**When to use:** The user names one or more entities and wants their properties. No relationship context needed.

**Example questions:**
- Tell me briefly about Sherlock Holmes.
- Who is Miss Hunter?
- Give me the descriptions of Holmes, Watson, and Rucastle.

**Cypher:**
```cypher
MATCH (e)
WHERE e.domain = $domain
  AND ANY(n IN $entityNameList WHERE toLower(e.name) CONTAINS toLower(n))
RETURN e {.*, label: labels(e)} AS entityData
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern2 --entityNameList $entityNameList
```

---

## Pattern 3 — Entity + lightweight relationship summary

**When to use:** The user wants an entity plus a one-hop summary of who/what it's connected to, without pulling full properties of the neighbors. Cheaper than Pattern 4 — good default for "tell me about X" when X is highly connected.

**Example questions:**
- Tell me about Mr. Rucastle and what he's involved in.
- Give me a quick overview of Miss Hunter and her connections.

**Cypher:**
```cypher
MATCH (e)
WHERE e.domain = $domain
  AND ANY(n IN $entityNameList WHERE toLower(e.name) CONTAINS toLower(n))
OPTIONAL MATCH (e)-[r]-(t)
WITH e, collect({
  type: type(r),
  properties: properties(r),
  target: {
    name: t.name,
    label: labels(t)
  }
}) AS connections
RETURN properties(e) AS entityData, connections
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern3 --entityNameList $entityNameList
```

---

## Pattern 4 — Entity + full neighborhood

**When to use:** The user wants a rich picture of one entity, including full data on everyone it's connected to. Use when the answer requires understanding the entity in context (e.g. a character's role in the story).

**Example questions:**
- Tell me about the character of Sherlock Holmes.
- Describe Mr. Rucastle and his role in the story.
- What is Toller's involvement?

**Cypher:**
```cypher
MATCH (e:$entityClass)
WHERE e.domain = $domain
  AND toLower(e.name) CONTAINS toLower($entityName)
OPTIONAL MATCH (e)-[r]-(t)
WITH e, collect({
  rel_type: type(r),
  rel_properties: properties(r),
  connected_to: {
    label: labels(t),
    data: properties(t)
  }
}) AS connections
RETURN properties(e) AS entityData, connections
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern4 --entityClass $entityClass --entityName $entityName
```

---

## Pattern 5 — Paths between two entities (shortest or all-within-depth)

**When to use:** The user asks how two entities are related and there is no direct edge, or the connecting path itself carries meaning. The `mode` parameter controls behavior:
- `mode=shortest` — single shortest path (was old Pattern 5)
- `mode=all` — up to 3 shortest paths within depth 5 (was old Pattern 6)

**Example questions:**
- How is Miss Hunter connected to Mr. Fowler? *(mode=shortest)*
- What are all the ways Sherlock Holmes is linked to the Copper Beeches estate? *(mode=all)*
- How does Alice Rucastle connect to Mr. Fowler? *(mode=all)*

**Cypher (mode=shortest):**
```cypher
MATCH p = shortestPath((e:$entityClass1)-[*..5]-(t:$entityClass2))
WHERE e.domain = $domain AND t.domain = $domain
  AND toLower(e.name) CONTAINS toLower($entityName1)
  AND toLower(t.name) CONTAINS toLower($entityName2)
RETURN [i IN range(0, length(p)-1) | [
  {labels: labels(nodes(p)[i]), data: properties(nodes(p)[i])},
  {rel: type(relationships(p)[i]), data: properties(relationships(p)[i])}
]] + [{label: labels(nodes(p)[-1]), data: properties(nodes(p)[-1])}] AS interleavedPath
```

**Cypher (mode=all):**
```cypher
MATCH (e:$entityClass1), (t:$entityClass2)
WHERE e.domain = $domain AND t.domain = $domain
  AND toLower(e.name) CONTAINS toLower($entityName1)
  AND toLower(t.name) CONTAINS toLower($entityName2)
WITH e, t
MATCH p = (e)-[*1..5]-(t)
WITH p
ORDER BY length(p) ASC
LIMIT 3
RETURN [i IN range(0, length(p)-1) | [
  {label: labels(nodes(p)[i]), data: properties(nodes(p)[i])},
  {type: type(relationships(p)[i]), data: properties(relationships(p)[i])}
]] + [{label: labels(nodes(p)[-1]), data: properties(nodes(p)[-1])}] AS interleavedPath
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern5 --mode {shortest|all} --entityClass1 $entityClass1 --entityClass2 $entityClass2 --entityName1 $entityName1 --entityName2 $entityName2
```

---

## Pattern 6 — Direct relationships between two entities

**When to use:** The user asks about the relationship between two specific named entities. Returns only direct edges between them — fastest and most precise option when you suspect a direct connection exists. Falls back to Pattern 5 if this returns nothing.

**Example questions:**
- How is Holmes's relationship with Dr. Watson?
- What does Holmes think of Miss Hunter?
- What is the connection between Mr. Rucastle and his daughter Alice?

**Cypher:**
```cypher
MATCH (e1)-[r]-(e2)
WHERE e1.domain = $domain AND e2.domain = $domain
  AND toLower(e1.name) CONTAINS toLower($entityName1)
  AND toLower(e2.name) CONTAINS toLower($entityName2)
RETURN type(r) AS relType,
       properties(r) AS relProps,
       startNode(r).name AS fromEntity,
       endNode(r).name AS toEntity,
       labels(startNode(r)) AS fromLabels,
       labels(endNode(r)) AS toLabels
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern6 --entityName1 $entityName1 --entityName2 $entityName2
```

---

## Pattern 7 — Search entities by name or description fragment

**When to use:** The user references an entity by a phrase or descriptor rather than its canonical name (e.g. "the coil of hair", "the box", "the country house"). Use this to locate the node first, then chain to Pattern 4 for full context.

**Example questions:**
- What is the importance of the coil of hair? *(search → then Pattern 4)*
- Tell me about that locked room in the house.
- What's the significance of the photograph?

**Cypher:**
```cypher
MATCH (e)
WHERE e.domain = $domain
  AND (
    toLower(e.name) CONTAINS toLower($searchTerm)
    OR toLower(coalesce(e.description, '')) CONTAINS toLower($searchTerm)
  )
RETURN e {.*, label: labels(e)} AS entityData
LIMIT 10
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern7 --searchTerm $searchTerm
```

---

## Pattern 8 — Entities of class X connected to entity Y

**When to use:** The user wants a filtered list — all entities of a particular class that are connected to a specific named entity. Useful for "what places is Miss Hunter associated with" or "which characters interact with the Copper Beeches".

**Example questions:**
- Which characters interact with the Copper Beeches estate?
- What places is Miss Hunter associated with?
- What objects are connected to Mr. Rucastle?
- Who lives at the Copper Beeches?

**Cypher:**
```cypher
MATCH (e:$entityClass)-[r]-(t)
WHERE e.domain = $domain
  AND t.domain = $domain
  AND toLower(t.name) CONTAINS toLower($entityName)
RETURN e {.*, label: labels(e)} AS entityData,
       type(r) AS relType,
       properties(r) AS relProps
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern8 --entityClass $entityClass --entityName $entityName
```

---

## Pattern 9 — Top-N entities of a class by connection count (centrality)

**When to use:** The user asks for the "main", "key", "important", or "top" entities of a given class. Ranking is by degree (number of relationships) — a reasonable proxy for narrative importance in an extracted KG.

**Example questions:**
- Who are the main characters of the story?
- What are the key settings where the events take place?
- Who is the real villain of the story? *(rank `:Character` filtered by antagonist relationships, or rank and inspect top-3)*
- What are the most important objects in the book?

**Cypher:**
```cypher
MATCH (e:$entityClass)
WHERE e.domain = $domain
OPTIONAL MATCH (e)-[r]-()
WITH e, count(r) AS degree
RETURN e {.*, label: labels(e), degree: degree} AS entityData
ORDER BY degree DESC
LIMIT $topN
```

**CLI:**
```
artmind query graph --domain $domain --pattern pattern9 --entityClass $entityClass --topN $topN
```

---

## Question-to-Pattern Mapping (Reference)

For the Sherlock Holmes "The Copper Beeches" KG, here is how the seed questions map to patterns. Use this as a worked example when routing new questions.

| Question | Pattern | Parameters |
|---|---|---|
| Tell me about the character of Sherlock Holmes | **4** | `entityClass=Character, entityName=Sherlock Holmes` |
| How is his relationship with Dr. Watson | **6**, fallback **5** (mode=shortest) | `entityName1=Sherlock Holmes, entityName2=Watson` |
| What does Holmes think of Miss Hunter | **6** | `entityName1=Holmes, entityName2=Hunter` |
| Who is the real villain of the story | **9** then **4** | rank `Character`, inspect top results |
| What are the key settings | **9** | `entityClass=Place, topN=5` |
| What is the importance of the coil of hair | **7** then **4** | `searchTerm=coil of hair` |
| Who are the main characters and brief descriptions | **9** then **2** | rank `Character`, then fetch info |
| How did Sherlock solve the mystery | **5** (mode=all) | path between Holmes and resolution-related entities |
| How did Alice Rucastle die | **4** | `entityClass=Character, entityName=Alice Rucastle` — inspect death-related relationships |

> **Note:** The current extraction does not produce explicit event-chain nodes (`:Event` with `NEXT`/`CAUSED` edges). Narrative-reconstruction questions like "how did X happen" must be answered via Pattern 4 (full neighborhood of the key entity) or Pattern 5 mode=all (all paths between actors involved). The synthesizing LLM reconstructs the sequence from relationship properties.

## Additional Question Templates

These templates extend the library's coverage. Add them to your test set as the system matures.

- "What clues did Holmes use?" → Pattern 8 with `entityClass=Object|Clue, entityName=Holmes`
- "Compare Mr. Rucastle and Mr. Fowler" → Pattern 4 on each, plus Pattern 5 (mode=all) between them
- "Who hired Miss Hunter and why?" → Pattern 6 between Hunter and Rucastle (motive likely on the edge)
- "Which characters are family?" → Pattern 1 on `Character` post-filtered by relationship type, or a direct relationship-typed query (future Pattern 10 candidate)
- "What deceptions occurred?" → Pattern 1 on a thematic class if extracted, otherwise Pattern 7 with `searchTerm=deception`

## Output Contract for the Synthesizer

Every pattern returns JSON. The harness should pass the raw result to the LLM along with the original user question and a brief instruction:

> "Using only the structured KG data below, answer the user's question. If the data is insufficient, say so. Do not invent entities or relationships not present in the data."

This keeps synthesis grounded and prevents hallucination of facts not in the extracted graph.
