# Session Graph Snapshot — Export & Import

## Problem
Neo4j data may not persist between work sessions (e.g. Docker restarts, environment rebuilds). The extraction pipeline already produces per-document JSON files that get written to Neo4j, but post-extraction changes (entity refinements via `refine-graph`, UserChat nodes via `update`) exist only in Neo4j. We need CLI commands to save and restore the full graph state at session boundaries.

## Current State
- KG extraction writes per-document JSONs to `data/kg/<domain>/<doc>/` (document.json, chunks.json, entities.json, properties.json, relationships.json)
- `_write_to_neo4j()` in `ingest.py` reads those JSONs and MERGEs into Neo4j
- `refine-graph` merges duplicate entities directly in Neo4j — not reflected in the on-disk JSONs
- `update` creates UserChat nodes directly in Neo4j
- Neo4j schema (constraints, indexes, vector indexes, fulltext indexes) is created by `_setup_neo4j()` in `setup.py`, all using `IF NOT EXISTS`
- Node types in the graph: Document, DocChunk, Entity (with dynamic labels like CHARACTER:Entity), UserChat

## Design

### CLI Commands
New `artmind session` CLI group with two commands:

- `artmind session close` — export the full Neo4j graph to a compressed snapshot file
- `artmind session initiate [--snapshot PATH] [--yes]` — wipe Neo4j data and restore from a snapshot

### Snapshot Format
A `.tar.gz` file in `data/graph_snapshot/` named `snapshot_YYYY-MM-DD_HHMMSS.tar.gz`, containing a single `snapshot.json`:

```json
{
  "meta": {
    "exported_at": "ISO timestamp",
    "neo4j_database": "neo4j",
    "node_counts": {"Document": 5, "DocChunk": 42, "Entity": 187, "UserChat": 3},
    "relationship_count": 412
  },
  "schema": {
    "constraints": ["document_id", "chunk_id", "user_chat_id"],
    "indexes": ["entity_lookup"],
    "vector_indexes": ["chunk_embedding", "user_chat_embedding"],
    "fulltext_indexes": ["chunk_text_ft", "user_chat_text_ft"]
  },
  "nodes": {
    "Document": [{"id": "...", "name": "...", "domain": "...", ...}],
    "DocChunk": [{"id": "...", "doc_id": "...", "text": "...", "embedding": [...], ...}],
    "Entity": [{"name": "...", "entity_class": "CHARACTER", "domain": "fiction", "labels": ["CHARACTER", "Entity"], ...}],
    "UserChat": [{"id": "...", "raw_text": "...", "embedding": [...], ...}]
  },
  "relationships": [
    {
      "type": "EXTRACTED_FROM",
      "start_match": {"name": "Elara", "entity_class": "CHARACTER", "domain": "fiction"},
      "start_labels": ["CHARACTER", "Entity"],
      "end_match": {"id": "abc_001"},
      "end_labels": ["DocChunk"],
      "properties": {}
    }
  ]
}
```

Node match keys by label:
- Document: `{id}`
- DocChunk: `{id}`
- Entity: `{name, entity_class, domain}`
- UserChat: `{id}`

### Export Logic (`artmind session close`)
1. Open Neo4j session via `neo4j_session()`
2. Capture schema info for the `meta` section (informational, not used by import)
3. Export nodes: query each base label exactly once — Document, DocChunk, Entity, UserChat — via `MATCH (n:<BaseLabel>) RETURN properties(n), labels(n)`. Store the full label set per node (e.g. Entity nodes store `["CHARACTER", "Entity"]`). Embeddings are preserved as float arrays. Group results by base label in the `nodes` dict
4. Export relationships: `MATCH (s)-[r]->(e) RETURN labels(s), properties(s), type(r), properties(r), labels(e), properties(e)`. For each relationship, extract match keys from start/end node properties based on their base label (see match keys above)
5. Write `snapshot.json` to a temp file, compress into `data/graph_snapshot/snapshot_YYYY-MM-DD_HHMMSS.tar.gz`, delete temp file
6. Log summary: node counts per label, relationship count, file size

### Import Logic (`artmind session initiate`)
1. Locate snapshot: if `--snapshot` given, use it. Otherwise find newest `.tar.gz` in `data/graph_snapshot/` by filename sort
2. Extract tar to temp directory, read `snapshot.json`
3. Wipe Neo4j completely:
   a. Drop all constraints: `SHOW CONSTRAINTS` → `DROP CONSTRAINT <name>` for each
   b. Drop all indexes: `SHOW INDEXES` → `DROP INDEX <name>` for each (covers regular, vector, and fulltext)
   c. Delete all data: `MATCH (n) DETACH DELETE n` in batches of 10,000
4. Recreate schema: call `_setup_neo4j(session, embedding_dim)` — creates constraints, indexes, vector indexes, and fulltext indexes fresh, matching the current artmind code version. This ensures snapshots from older versions import correctly into newer versions with different schema definitions
5. Restore nodes: CREATE each node with all properties and labels. Entities get their dynamic label via `_sanitize_label()` from `ingest.py`
6. Restore relationships: MATCH start/end nodes by stored match keys, CREATE relationship with type and properties
7. Clean up temp directory
8. Log summary: nodes per label, relationships, elapsed time

### Safety
- `session initiate` prompts for confirmation: "This will delete all data in Neo4j database 'neo4j'. Continue? [y/N]"
- `--yes` flag skips the prompt (for scripted/justfile usage)
- Empty database export succeeds with zero counts (valid empty snapshot)

### New Module
`artmind/graph_snapshot.py` with functions:
- `export_graph() -> Path` — full export flow, returns path to `.tar.gz`
- `import_graph(snapshot_path: Path) -> dict` — full import flow, returns summary stats
- `_find_latest_snapshot() -> Path | None` — scans snapshot dir for newest file
- `_export_nodes(session) -> dict` — queries and groups nodes by label
- `_export_relationships(session) -> list` — queries relationships with match keys
- `_wipe_database(session)` — drop all constraints/indexes, then batched DETACH DELETE
- `_restore_nodes(session, nodes: dict)` — creates all nodes
- `_restore_relationships(session, relationships: list)` — creates all relationships

### Reused Code
- `neo4j_session()` from `graph_query.py`
- `_setup_neo4j()` from `setup.py`
- `_sanitize_label()`, `_flatten_props()` from `ingest.py`
- `load_env()` from `utils/functions.py`

### New Path
`paths.py`: `GRAPH_SNAPSHOT_DIR = DATA_DIR / "graph_snapshot"`

### Justfile Recipes
- `just session-close` → `uv run artmind session close`
- `just session-initiate` → `uv run artmind session initiate --yes`

### Error Handling
- Neo4j connection failure → `click.ClickException` with message
- Empty database on export → valid snapshot with zero counts
- No snapshot found on import → `click.ClickException("No snapshots found in data/graph_snapshot/")`
- Corrupt tar or missing `snapshot.json` → `click.ClickException` with details
