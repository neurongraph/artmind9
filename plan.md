# Artmind Query CLI and Skill Plan

## Goal

Build a deterministic query layer for Artmind that exposes graph and vector retrieval through CLI commands, then define a clean `artmind-query` skill that can route natural-language questions to those CLI commands.

The architecture has two layers:

- Layer 1: `artmind-query` skill. This is the intelligent routing layer used by the agent harness. It reads the user's question, inspects graph metadata/entity listings, chooses one or more query patterns, fills parameters, runs the CLI, and synthesizes a grounded answer.
- Layer 2: `artmind query` CLI commands. These are deterministic, templated commands that execute graph/vector retrieval and return structured JSON. They should not infer intent beyond validation and light normalization.

## CLI Scope

Add a new top-level command group:

```bash
uv run artmind query ...
```

Examples of Subcommands:

```bash
uv run artmind query graph metadata --domain fiction
uv run artmind query graph entity_listing --domain fiction
uv run artmind query graph pattern1 --domain fiction --entityClass LOCATION "List all the places mentioned in the story."
uv run artmind query vector --domain fiction "Where did Sherlock Holmes die?"
```

Keep graph output as JSON that the skill can pass to an LLM. The CLI should serialize Neo4j records into JSON-compatible values, including arrays/maps returned by Cypher. Remove or omit large embedding fields from output.

## Files

Add or update:

- `artmind/cli.py`: Click command definitions only.
- `artmind/graph_query.py`: Neo4j connection helpers, graph metadata/entity listing, graph pattern execution.
- `artmind/vector_query.py`: Query embedding and Neo4j vector index lookup.
- `test/test_query_cli.py`: CLI behavior tests with mocked query modules.
- `test/test_graph_query.py`: Parameter validation and query dispatch tests where feasible.
- `test/test_vector_query.py`: Vector result shaping tests where feasible.
- `Query_specs.md`: Align command examples with `artmind query graph`, including metadata and entity_listing commands.
- `justfile`: Add convenience recipes for graph/vector query commands.

## Graph Query Commands

Implementation must include CLI support and tests for every graph pattern listed in `Query_specs.md`, not just metadata/vector plumbing. Pattern coverage means each pattern has a callable CLI path, validation for required parameters, dispatch to the intended Cypher, and JSON-shaped output.

### Metadata

Command:

```bash
uv run artmind query graph metadata --domain fiction
```

Purpose:

Return graph schema metadata for a domain so the skill can understand available labels, properties, relationship types, relationship directions, and distinct type values.

Implementation notes:

- Run the metadata Cypher from `Query_specs.md`.
- Filter all nodes and relationships by `domain`.
- Return JSON with top-level metadata and rows.
- Preserve relationship connection shapes as label arrays.
- Does not accept a `--pattern` option; metadata is its own subcommand.

### Entity Listing

Command:

```bash
uv run artmind query graph entity_listing --domain fiction
```

Purpose:

Return entity names grouped by label/type so the skill can match user phrasing to canonical graph entities.

Implementation notes:

- Run the entity listing Cypher from `Query_specs.md`.
- Return JSON grouped by label and type.
- Include labels for non-entity nodes only if they have a domain and name; otherwise prefer `Entity`-focused output to avoid noisy chunks/documents.


### Pattern Execution

Each pattern is a dedicated subcommand under `artmind query graph`. Examples:

```bash
uv run artmind query graph pattern1 --domain fiction --entityClass LOCATION "List all the places mentioned in the story."
uv run artmind query graph pattern5 --domain fiction --entityClass1 CHARACTER --entityClass2 CHARACTER --entityName1 Holmes --entityName2 Watson --mode shortest
uv run artmind query graph pattern9 --domain fiction --entityClass CHARACTER --topN 5
```

Supported patterns:

- `pattern1`: list entities of a class.
- `pattern2`: info on one or more named entities.
- `pattern3`: entity plus lightweight relationship summary.
- `pattern4`: entity plus full neighborhood.
- `pattern5`: paths between two entities, with `--mode shortest|all`.
- `pattern6`: direct relationships between two entities.
- `pattern7`: search entities by name or description fragment.
- `pattern8`: entities of class X connected to entity Y.
- `pattern9`: top-N entities of a class by connection count.

Pattern parameter requirements:

| Subcommand | Required options | Optional/defaulted options |
|---|---|---|
| `pattern1` | `--domain`, `--entityClass` | positional `question` |
| `pattern2` | `--domain`, one or more `--entityNameList` | positional `question` |
| `pattern3` | `--domain`, one or more `--entityNameList` | positional `question` |
| `pattern4` | `--domain`, `--entityClass`, `--entityName` | positional `question` |
| `pattern5` | `--domain`, `--entityClass1`, `--entityClass2`, `--entityName1`, `--entityName2` | `--mode shortest`, positional `question` |
| `pattern6` | `--domain`, `--entityName1`, `--entityName2` | positional `question` |
| `pattern7` | `--domain`, `--searchTerm` | `--limit 10`, positional `question` |
| `pattern8` | `--domain`, `--entityClass`, `--entityName` | positional `question` |
| `pattern9` | `--domain`, `--entityClass` | `--topN 5`, positional `question` |

Options per subcommand (pattern-specific; all subcommands also accept `--compact`):

```bash
--domain TEXT              Required on every subcommand
--entityClass TEXT         pattern1, pattern4, pattern8, pattern9
--entityClass1 TEXT        pattern5
--entityClass2 TEXT        pattern5
--entityName TEXT          pattern4, pattern8
--entityName1 TEXT         pattern5, pattern6
--entityName2 TEXT         pattern5, pattern6
--entityNameList TEXT      pattern2, pattern3 (repeatable)
--searchTerm TEXT          pattern7
--mode shortest|all        pattern5 — default: shortest
--topN INTEGER             pattern9 — default: 5
--limit INTEGER            pattern7 — default: 10
--compact                  All subcommands — emit compact JSON
question                   All subcommands — optional positional, included in output metadata
```

Validation:

- Validate required options per pattern before connecting to Neo4j.
- Validate dynamic labels with a strict regex before interpolating them into Cypher.
- Normalize entity class aliases where useful, but keep this conservative:
  - `character`, `characters`, `person`, `people` -> `CHARACTER`
  - `place`, `places`, `location`, `locations`, `setting`, `settings` -> `LOCATION`
  - `object`, `objects`, `item`, `items`, `clue`, `clues` -> `OBJECT`
  - `event`, `events` -> `EVENT`
  - `concept`, `concepts`, `theme`, `themes` -> `CONCEPT`
  - otherwise uppercase the supplied class.

Output shape:

```json
{
  "domain": "fiction",
  "query_type": "graph",
  "command": "pattern",
  "pattern": "pattern1",
  "question": "List all the places mentioned in the story.",
  "parameters": {
    "entityClass": "LOCATION"
  },
  "rows": []
}
```

## Vector Query Command

Command:

```bash
uv run artmind query vector --domain fiction --topK 5 "Where did Sherlock Holmes die?"
```

Purpose:

Search `DocChunk` nodes by embedding similarity for questions that need text-grounded retrieval rather than graph traversal.

Implementation notes:

- Embed the user question with `ARTMIND_KG_EMBEDDINGS_MODEL`, defaulting to the existing ingestion default.
- Query Neo4j vector index `chunk_embedding`.
- Filter returned chunks by `domain`.
- Return chunk text by default.
- Exclude the raw embedding from output.
- Include score, chunk metadata, and parent document metadata.

Options:

```bash
--domain TEXT      Required
--topK INTEGER     Default: 5
--compact          Emit compact JSON
question           Required
```

Output shape:

```json
{
  "domain": "fiction",
  "query_type": "vector",
  "question": "Where did Sherlock Holmes die?",
  "parameters": {
    "topK": 5
  },
  "rows": [
    {
      "score": 0.91,
      "chunk": {
        "id": "...",
        "name": "...",
        "doc_id": "...",
        "text": "..."
      },
      "document": {
        "id": "...",
        "name": "..."
      }
    }
  ]
}
```

## JSON Serialization

Neo4j records are not directly JSON serializable in all cases. Add a small serializer in `graph_query.py` or shared query utilities:

- Convert Neo4j `Record` to dict.
- Recursively convert lists, tuples, dicts, Neo4j nodes, relationships, and paths.
- Convert unsupported values to strings as a fallback.
- Strip `embedding` keys from graph/vector output unless explicitly requested in the future.

Do not use `json_repair` for CLI output. The CLI should produce valid JSON directly from Python objects. `json_repair` remains relevant for LLM extraction responses, not for Neo4j query serialization.

## Skill Scope

Create a clean `SKILL.md` for `artmind-query`.

The skill is responsible for intelligence:

1. Receive a user question and domain.
2. Run graph metadata:

   ```bash
   uv run artmind query graph metadata --domain <domain>
   ```

3. Run entity listing:

   ```bash
   uv run artmind query graph entity_listing --domain <domain>
   ```

4. Use metadata and entity listing to map the question to entity classes, canonical names, and candidate relationships.
5. Select one or more graph/vector commands.
6. Run commands in sequence.
7. If a direct graph query returns no rows, apply documented fallbacks.
8. Synthesize the answer using only returned structured data.
9. If data is insufficient, say so clearly.

## Skill Routing Guidance

Graph-first cases:

- Listing entities by class -> `pattern1` or `pattern9`.
- Named entity facts -> `pattern2`, `pattern3`, or `pattern4`.
- Relationship between named entities -> `pattern6`, fallback to `pattern5 --mode shortest`.
- Rich contextual role of one entity -> `pattern4`.
- Descriptor-based references -> `pattern7`, then `pattern4` on the best candidate.
- Class X connected to entity Y -> `pattern8`.

Vector-first cases:

- Questions asking for source-text evidence.
- Questions likely requiring narrative details that may not be captured as entity properties or relationships.
- Ambiguous factual questions where graph metadata/entity listing does not expose relevant entities.
- "Where/when/how did X happen?" questions when graph patterns return insufficient data.

Hybrid cases:

- Use graph metadata and entity listing first.
- Use graph patterns to identify candidate entities and relationships.
- Use vector search to retrieve supporting chunks.
- Synthesize from both, explicitly prioritizing graph structure for entity/relationship facts and chunk text for narrative evidence.

Fallback rules:

- `pattern6` no rows -> `pattern5 --mode shortest`.
- `pattern7` multiple candidates -> choose the best candidate by name/description match, then use `pattern4`; mention ambiguity if needed.
- `pattern9` top entities -> inspect top result(s) with `pattern4` when the user asks for explanation, not just ranking.
- Graph result insufficient -> run vector query.

## Skill Answer Contract

The skill must use this synthesis rule:

```text
Using only the structured KG data and chunk text returned by Artmind query commands, answer the user's question. If the data is insufficient, say so. Do not invent entities, events, or relationships not present in the returned data.
```

The skill should include concise provenance in its internal reasoning, but the user-facing answer should be natural and direct.

## Implementation Phases

### Phase 1: Query Modules

- Add Neo4j query connection helpers.
- Add graph metadata and entity listing functions.
- Add graph pattern dispatch and validation for `pattern1` through `pattern9`.
- Implement every Cypher pattern from `Query_specs.md`, adjusted only where needed for the actual stored labels/properties.
- Add vector query function.
- Add JSON serialization utilities.

### Phase 2: CLI

- Add `query` group to `artmind/cli.py`.
- Add `query graph` as a subgroup with subcommands `metadata`, `entity_listing`, and `pattern1`–`pattern9`.
- Add `query vector`.
- Each pattern subcommand declares its required options explicitly so `--help` is fully self-documenting.
- Ensure all commands emit valid JSON and useful Click errors.

### Phase 3: Tests

- Test CLI option parsing and dispatch with mocks.
- Test successful CLI invocation for every graph pattern from `pattern1` through `pattern9`.
- Test missing required pattern params for every graph pattern from `pattern1` through `pattern9`.
- Test `pattern5` mode validation for `shortest` and `all`.
- Test `pattern2` and `pattern3` repeated `--entityNameList` handling.
- Test `pattern7` default limit handling.
- Test `pattern9` default `topN` handling.
- Test entity class normalization.
- Test compact vs pretty JSON output.
- Test vector query output shape with mocked embedding/query calls.
- Test metadata command output shape.
- Test entity listing command output shape.

### Phase 4: Documentation

- Update `Query_specs.md` command examples from any old command names to `artmind query graph`.
- Add metadata/entity_listing examples.
- Add vector query examples.
- Add `justfile` recipes.

### Phase 5: Skill

- Draft `SKILL.md`.
- Include routing algorithm, CLI command templates, fallback rules, output contract, and examples.
- Keep the skill implementation separate from the deterministic CLI.

## Verification

Run:

```bash
uv run --group dev pytest test/ -v
uv run artmind query graph --help
uv run artmind query graph metadata --help
uv run artmind query graph entity_listing --help
uv run artmind query graph pattern1 --help
uv run artmind query vector --help
```

Manual smoke tests with a running Neo4j instance:

```bash
uv run artmind query graph metadata --domain fiction
uv run artmind query graph entity_listing --domain fiction
uv run artmind query graph pattern1 --domain fiction --entityClass LOCATION "List all the places mentioned in the story."
uv run artmind query vector --domain fiction --topK 5 "Where did Sherlock Holmes die?"
```
