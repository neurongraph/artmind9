# artmind-update Design Spec
**Date:** 2026-05-05
**Status:** Approved for implementation

---

## Overview

`artmind-update` allows users to add and update facts in the artmind knowledge graph through natural language input. It follows the same three-layer architecture as the rest of the system (skill → CLI → backend) and is domain-scoped, multi-user aware, and auditable.

Input can range from a single atomic fact ("Alice is now VP of Engineering") to a multi-sentence scenario, a pasted email, or an entire section of a document or web page. All input sizes go through the same unified extraction pipeline for consistency and schema-awareness.

---

## Architecture

```
Skill (skills/artmind-update/)
  └── orchestrates multi-turn conversation with user
      └── calls CLI commands for all data operations

CLI (artmind/cli.py — new `update` command group)
  └── artmind update draft    → extract facts + find graph candidates
  └── artmind update confirm  → write confirmed facts to Neo4j
  └── artmind update export   → dump UserChat nodes to markdown files
  └── artmind update history  → list recent update sessions

Backend (artmind/update.py — new module)
  └── extract_facts()         → calls extraction.py primitives (3 passes)
  └── find_candidates()       → vector + name match against Neo4j
  └── write_user_chat()       → writes UserChat node + entities + relationships
  └── update_node()           → updates existing node with audit metadata
  └── export_chats()          → generates markdown from UserChat nodes

Shared (artmind/extraction.py — new module, refactored from ingest.py)
  └── LLM prompt-building and response-parsing primitives for all 3 passes
  └── All Ollama calls logged to llm_calls.log via log_llm_call()
```

The skill never touches Neo4j directly. All graph operations go through the CLI, consistent with `artmind-query`.

---

## Data Model

### New node type: `UserChat`

```
(:UserChat {
  id:          string,     # UUID
  raw_text:    string,     # full original user input
  embedding:   float[],    # vector embedding (same model as DocChunk)
  domain:      string,     # domain name
  session_id:  string,     # UUID grouping all turns in one skill invocation
  input_hint:  string,     # auto-tagged by backend from text length/structure:
                           #   "atomic_fact" (<1 sentence), "todo" (task-like phrasing),
                           #   "passage" (multi-sentence), "bulk" (>500 chars)
  created_at:  datetime,
  created_by:  string      # from ARTMIND_USER env var
})
```

### Relationships from `UserChat`

```
(:UserChat)-[:MENTIONS]->(:Entity)
```

`UserChat` nodes link directly to all entity nodes they contributed — whether newly created or updated existing ones.

### Relationship provenance

Extracted relationships between entities carry provenance as properties (not a graph link, which Neo4j does not support):

```
(:EntityA)-[:SOME_REL {
  source_chat_id: string,    # UUID of originating UserChat node
  created_at:     datetime,
  created_by:     string,
  updated_at:     datetime,
  updated_by:     string
}]->(:EntityB)
```

### Audit metadata on all nodes

Every entity node (new or updated) carries:

```
created_at:   datetime
created_by:   string
updated_at:   datetime
updated_by:   string
```

`created_by` and `updated_by` are populated from the `ARTMIND_USER` environment variable. This field is designed for future multi-user use — a single user system leaves it set to one value, a team system sets it per user at runtime.

### Vector index

The existing vector index (currently covering `DocChunk`) is extended to include `UserChat` nodes. `vector_query.py` unions results from both node types and returns a `source_type` field (`"document"` or `"user_chat"`) so callers know the provenance of each result.

---

## SQLite Schema Changes (`db.py`)

Two new tables added to `document_registry.db`:

```sql
CREATE TABLE IF NOT EXISTS update_sessions (
    session_id    TEXT PRIMARY KEY,
    domain        TEXT NOT NULL,
    status        TEXT NOT NULL,   -- 'draft' | 'confirmed' | 'abandoned'
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS update_drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES update_sessions(session_id),
    raw_text        TEXT NOT NULL,
    input_hint      TEXT,           -- 'atomic_fact' | 'passage' | 'todo' | 'bulk'
    extraction_json TEXT,           -- extracted entities + relationships (JSON)
    candidates_json TEXT,           -- top N candidates per entity (JSON)
    status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'confirmed' | 'failed'
    created_at      TEXT NOT NULL
);
```

`update_drafts` is the bridge between `artmind update draft` and `artmind update confirm`. The draft step writes extraction results and candidates here; the confirm step reads user resolutions against this record and then writes to Neo4j.

---

## Skill Interaction Flow

The skill is multi-turn. All turns within one invocation share a `session_id`.

```
1. LOAD DOMAINS
   Skill calls: artmind domains list
   → gets available domains

2. DETECT DOMAIN
   Skill inspects the user's first message for domain signals.
   If confident → announces chosen domain, proceeds.
   If ambiguous → asks user to pick from list (one question).

3. INPUT LOOP (multi-turn)
   User provides input (any size, any format).
   Skill calls: artmind update draft --domain <d> --text "<input>"
   → returns: session_id, extracted entities/relationships,
              top N graph candidates per entity

4. AMBIGUITY RESOLUTION
   For each entity with candidates, skill presents options:
     "Found 'John' — did you mean:
      1. John Smith (Person, linked to Acme Corp)
      2. John Doe (Person, linked to Project Alpha)
      3. None of these — create new"
   Multiple entities are batched into one confirmation round.
   User picks for each.

5. CONFIRM & WRITE
   Skill calls: artmind update confirm
     --session <id>
     --resolutions <json>
   → writes UserChat node + entities + relationships to Neo4j
   → returns summary: nodes_created, nodes_updated, relationships_written

6. CONTINUE OR EXIT
   Skill asks: "Anything else to add?"
   If yes → back to step 3 (same session_id, new UserChat node + update_draft row)
   If no → skill exits, reports session summary.
```

---

## CLI Commands

```
artmind update draft
  --domain    TEXT   domain name (required)
  --text      TEXT   raw user input
  --session   UUID   optional — resumes an existing session
  Output JSON:
    { session_id, extracted_entities, extracted_relationships,
      candidates_per_entity: [{ entity, top_n: [...] }] }

artmind update confirm
  --session     UUID   session from draft step
  --resolutions JSON   [{ entity_temp_id, action: "link"|"create"|"skip",
                          node_id: UUID|null }]
  Output JSON:
    { nodes_created, nodes_updated, relationships_written, user_chat_id }

artmind update history
  --domain    TEXT   filter by domain (optional)
  --user      TEXT   filter by created_by (optional)
  --limit     INT    default 20
  Output: table of recent sessions
    (session_id, domain, created_by, created_at, input_count, excerpt of first input)

artmind update export
  --domain    TEXT   filter by domain (optional)
  --format    TEXT   "sequential" (chronological) | "by-entity" (grouped by entity)
  --output    PATH   destination directory (default: data/chats/)
  Writes markdown files. Sequential: one file per session. By-entity: one
  file per entity with all UserChat nodes that mention it.
```

All commands output structured JSON (for skill consumption) or formatted tables (for direct CLI use), consistent with existing `artmind` commands.

---

## Backend Module (`artmind/update.py`)

```python
extract_facts(text, domain, schema)
  # Calls extraction.py primitives for all 3 passes (entities → properties → relationships)
  # Returns: { entities: [{temp_id, name, type, properties}],
  #            relationships: [{source_temp_id, target_temp_id, type, properties}] }
  # No SQLite chunk tracking — result returned in-memory for candidate matching

find_candidates(entity, domain, top_n=5)
  # Vector similarity search + exact/fuzzy name match in Neo4j
  # Searches within domain first, falls back to cross-domain if sparse
  # Returns: [{ node_id, name, type, match_score, context_snippet }]

write_user_chat(session_id, raw_text, domain, user_id, resolutions,
                extracted_relationships)
  # Creates UserChat node with embedding (same Ollama embedding model as DocChunk in ingest.py)
  # Creates new entity nodes or updates existing ones per resolutions
  # Writes relationships with source_chat_id + audit properties
  # Links (:UserChat)-[:MENTIONS]->(:Entity) for all touched entities
  # Updates vector index

update_node(node_id, new_properties, user_id)
  # Merges new properties onto existing node
  # Stamps updated_at, updated_by

export_chats(domain, format, output_dir)
  # Queries UserChat nodes filtered by domain
  # "sequential": one markdown file per session, chronological order
  # "by-entity": one markdown file per entity, all chats mentioning it
```

---

## Shared Extraction Module (`artmind/extraction.py`)

Refactored from `ingest.py`. Exposes LLM prompt-building and response-parsing primitives only — no orchestration, no SQLite tracking, no file I/O.

```python
_build_entities_prompt(text, schema) → str
_build_properties_prompt(text, entities, schema) → str
_build_relationships_prompt(text, entities, schema) → str
_parse_entities_response(raw) → list
_parse_properties_response(raw) → list
_parse_relationships_response(raw) → list
```

All Ollama calls made through these primitives are logged to `llm_calls.log` via the existing `log_llm_call()` utility, consistent with `ingest.py`.

`ingest.py` is updated to import these primitives instead of defining them inline. Its per-chunk SQLite tracking, retry logic, and KG JSON file persistence remain untouched.

---

## Query Layer Changes (`graph_query.py`, `vector_query.py`)

### Vector search
`vector_query.py` unions results from `DocChunk` and `UserChat` embeddings. Each result carries a `source_type` field: `"document"` or `"user_chat"`.

### Source attribution in query patterns
Patterns that trace facts back to their source currently follow `(:Entity)<-[:MENTIONS]-(:DocChunk)`. These are updated to branch:

```cypher
OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
RETURN e,
       collect(chunk) AS doc_sources,
       collect(chat)  AS chat_sources
```

When surfacing a result, `doc_sources` shows document title; `chat_sources` shows `created_by` + `created_at`.

### Relationship provenance
Patterns that show where a relationship came from resolve `source_chat_id` on the relationship back to the `UserChat` node for display.

### No new query pattern needed
"Show only user-entered facts" and "what did I add last week" are expressible through existing patterns with a label filter (`UserChat` vs `DocChunk`) and `created_by` / `created_at` on the `UserChat` node.

The specific Cypher changes per pattern will be identified during implementation by reading the 9 pattern definitions in `graph_query.py`.

---

## Configuration

| Env var | Purpose |
|---------|---------|
| `ARTMIND_USER` | Identity of the current user. Stored as `created_by` / `updated_by` on all nodes and relationships written by `artmind-update`. Designed for future multi-user use. |

All other configuration (Neo4j URL, Ollama endpoint, domain paths) uses existing env vars.
