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
- `question`: The natural-language question to answer.

## Discovery

Start every new domain/question session by inspecting metadata and entities:

```bash
uv run artmind query graph metadata --domain <domain>
uv run artmind query graph entity-listing --domain <domain> --countAll --compact
```
If there are multiple questions in the user query, break them down into individual questions.

Use metadata and entity listings to identify:
- Stored labels such as `PERSON`, `LOCATION`, or domain-specific labels like `PROJECT_ROLE`.
- Canonical entity names.
- Relationship types, properties, and connection directions.
- Whether the question needs graph facts, vector text evidence, or both.

Entity classes are not hard-coded. artmind ingestion derives Neo4j labels from extracted `entity_class` values by replacing non-alphanumeric characters with `_` and uppercasing. Choose labels from metadata/entity listings whenever possible.

### Managing large entity listings

If `total_entities` from the initial `--countAll` call is large (roughly > 100), fetching the full listing may consume too much context. In that case, extract a name fragment from the user's question and narrow the listing:

```bash
uv run artmind query graph entity-listing --domain <domain> --nameFilter "<fragment>"
```

Use the returned canonical names to populate `--entityName`, `--entityNameList`, or other pattern parameters. If no fragment is identifiable from the question, skip the filtered listing and go straight to `pattern7` (search by description) or vector search to locate the relevant entities.

## Command Templates

Check total entity count (before deciding whether to fetch full listing):

```bash
uv run artmind query graph entity-listing --domain <domain> --countAll --compact
```

Filter entity listing by name fragment:

```bash
uv run artmind query graph entity-listing --domain <domain> --nameFilter "<fragment>"
```

List entities of a class:

```bash
uv run artmind query graph pattern1 --domain <domain> --entityClass <LABEL> "<question>"
```

Fetch properties for named entities:

```bash
uv run artmind query graph pattern2 --domain <domain> --entityNameList "<name>" "<question>"
```

Fetch properties plus a lightweight relationship summary:

```bash
uv run artmind query graph pattern3 --domain <domain> --entityNameList "<name>" "<question>"
```

Fetch a full one-hop neighborhood for an entity:

```bash
uv run artmind query graph pattern4 --domain <domain> --entityClass <LABEL> --entityName "<name>" "<question>"
```

Find shortest or limited paths between two entities:

```bash
uv run artmind query graph pattern5 --domain <domain> --mode shortest --entityClass1 <LABEL1> --entityClass2 <LABEL2> --entityName1 "<name1>" --entityName2 "<name2>" "<question>"
uv run artmind query graph pattern5 --domain <domain> --mode all --entityClass1 <LABEL1> --entityClass2 <LABEL2> --entityName1 "<name1>" --entityName2 "<name2>" "<question>"
```

Fetch direct relationships between two named entities:

```bash
uv run artmind query graph pattern6 --domain <domain> --entityName1 "<name1>" --entityName2 "<name2>" "<question>"
```

Search entities by name or description:

```bash
uv run artmind query graph pattern7 --domain <domain> --searchTerm "<fragment>" "<question>"
```

Find entities of class X connected to entity Y:

```bash
uv run artmind query graph pattern8 --domain <domain> --entityClass <LABEL> --entityName "<name>" "<question>"
```

Rank top entities of a class by graph degree:

```bash
uv run artmind query graph pattern9 --domain <domain> --entityClass <LABEL> --topN 5 "<question>"
```

Search source text by combining vector embeddings and keyword matching:

```bash
uv run artmind query vector-text --domain <domain> --topK 5 "<question>"
```

This command automatically balances semantic similarity (vector) and keyword matching (full-text) using Reciprocal Rank Fusion to produce optimal results.

Generate and execute an LLM-powered Cypher query from natural language:

```bash
uv run artmind query graph text2cypher --domain <domain> "<question>"
uv run artmind query graph text2cypher --domain <domain> --dry-run "<question>"
```

Use `--dry-run` to inspect the generated Cypher without executing it. This command grounds the LLM with graph schema metadata and entity listings to produce accurate, read-only Cypher. Only read queries are allowed — write operations are rejected.

## Routing

Prefer graph queries for questions about entity lists, named entities, explicit relationships, graph neighborhoods, connected entities, and rankings.

Use `text2cypher` when the question is clearly a graph query but none of patterns 1–9 fit — for example, aggregation counts ("how many DocChunks for Document X?"), multi-entity relationship traversals, queries referencing specific Neo4j labels or properties, or custom filtering/grouping that the templated patterns do not support.

Prefer `vector-text` search for source-text evidence, narrative details, "where/when/how did X happen" questions, ambiguous facts not exposed in metadata, or cases where graph output is too thin.

`vector-text` automatically combines semantic embeddings and keyword matching using Reciprocal Rank Fusion, so it handles both semantic drift and keyword-specific queries without manual fallback logic.

Use hybrid retrieval when the graph identifies candidate entities or relationships but source text is needed to explain context. In hybrid answers, prioritize graph structure for entity/relationship facts and chunk text for narrative evidence.

## Pattern Selection

- Listing entities by class: use `pattern1`; use `pattern9` for "main", "key", "important", or "top".
- Named entity facts: use `pattern2`, `pattern3`, or `pattern4`.
- Relationship between two named entities: use `pattern6`, then fallback to `pattern5 --mode shortest` if no rows.
- Rich contextual role of one entity: use `pattern4`.
- Descriptor-based references: use `pattern7`, then `pattern4` on the best candidate.
- Class X connected to entity Y: use `pattern8`.
- Ranking plus explanation: use `pattern9`, then inspect top result(s) with `pattern4`.
- Aggregation, counting, custom filtering, or multi-entity relationship queries not covered by patterns 1–9: use `text2cypher`.

## Fallbacks

- If `pattern6` returns no rows, run `pattern5 --mode shortest`.
- If `pattern7` returns multiple plausible candidates, choose the best name/description match and mention ambiguity when answering.
- If no pattern fits but the question is clearly a graph query, use `text2cypher`.
- If `text2cypher` fails (e.g. the LLM produces invalid Cypher), fall back to `vector-text` search.
- If graph results are empty or insufficient, run `vector-text` search.
- If `vector-text` results are sparse or weak, say the available artmind data does not answer the question.

## Answer Style

Answer directly and naturally. Keep provenance concise: mention whether the answer is based on graph relationships, entity properties, chunk text, or a combination. Avoid exposing raw JSON unless the user asks for it.
