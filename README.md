# artmind

> A knowledge system that synchronizes with your mind using artificial intelligence.

artmind ingests documents (PDFs, Markdown, text), extracts entities, properties, and relationships using a local LLM, stores them in a Neo4j knowledge graph with vector embeddings, and lets you query and update them — either with structured graph patterns via the CLI or through natural language using Claude Code skills.

Everything runs locally: no cloud APIs, no telemetry.

---

## How it works

```
Document (PDF/MD/text)                    Natural language input
    ↓ Docling (PDF → Markdown)                ↓ artmind update draft
    ↓ Chunking                                ↓ LLM extraction (entities, relationships)
    ↓ LLM extraction                          ↓ Candidate disambiguation
    ↓ Embeddings                              ↓ artmind update confirm
    ↓                                         ↓ Embeddings
    ↓                                         ↓
Neo4j knowledge graph + vector indexes (DocChunk + UserChat)
    ↓
CLI query (graph patterns + combined vector+text search via RRF)
    ↓
artmind-query / artmind-update Claude Code skills
```

Ingestion and updates are domain-scoped. A **domain** is a YAML schema that tells the LLM what entity types and relationship types to look for (e.g. `fiction`, `technical_paper`, `personal_journal`). Six schemas are bundled; you can add your own.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python >= 3.14.4 | You might need to install it using `pyenv install 3.14.4` |
| [uv](https://docs.astral.sh/uv/) | Package manager — `brew install uv` or `pip install uv` |
| [Ollama](https://ollama.ai) | Local LLM inference — runs models for extraction and embeddings |
| [Neo4j](https://neo4j.com/download/) >= 5.x | Graph database with the **APOC** plugin installed |
| [just](https://just.systems) *(optional)* | Task runner for convenience recipes — `brew install just` |

### Ollama models

Pull the models you intend to use. The defaults in `.env.example`:

```bash
ollama pull nomic-embed-text     # embeddings (required)
ollama pull gemma4:e4b           # image descriptions (optional, for PDFs with images)
# pick a reasoning model for extraction, e.g.:
ollama pull qwen3.6:35b-a3b-coding-nvfp4
```

Any Ollama model that follows instructions and produces JSON works for extraction. Larger models produce better graphs.

### Neo4j + APOC

Install Neo4j Desktop or run it via Docker. The **APOC** plugin is required for the `refine-graph` entity resolution command.

**Docker (quick start):**
```bash
docker run \
  --name artmind-neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password \
  -e NEO4J_PLUGINS='["apoc"]' \
  neo4j:latest
```

---

## Installation

```bash
git clone https://github.com/surjitdas/artmind9.git
cd artmind9
uv sync
```

Copy the environment template and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
# Neo4j connection
ARTMIND_KG_NEO4J_URI=neo4j://127.0.0.1:7687
ARTMIND_KG_NEO4J_USERNAME=neo4j
ARTMIND_KG_NEO4J_PASSWORD=your_password
ARTMIND_KG_NEO4J_DATABASE=your_neo4j_database

# LLM for extraction
ARTMIND_KG_LLM_PROVIDER=ollama
ARTMIND_KG_LLM_URL=http://localhost:11434
ARTMIND_KG_LLM_MODEL=qwen3.6:35b-a3b-coding-nvfp4          # or any capable Ollama model

# Embeddings
ARTMIND_KG_EMBEDDINGS_PROVIDER=ollama
ARTMIND_KG_EMBEDDINGS_URL=http://localhost:11434
ARTMIND_KG_EMBEDDINGS_MODEL=nomic-embed-text:latest
ARTMIND_KG_EMBEDDING_DIMENSIONS=768

# Image descriptions (used when ingesting PDFs that contain images)
ARTMIND_IMAGE_MODEL=gemma4:e4b
ARTMIND_OLLAMA_TIMEOUT=600

# Your identity for update audit trails (optional)
ARTMIND_USER=you@example.com
```

Verify the CLI is available:

```bash
uv run artmind --help
```

---

## Setup (first run)

Before ingesting or updating, initialize the SQLite tables and Neo4j constraints/indexes:

```bash
uv run artmind setup
```

Output:
```
SQLite:               ok
Neo4j constraints:    document_id, chunk_id, user_chat_id
Neo4j indexes:        entity_lookup
Neo4j vector indexes: chunk_embedding (dim=768), user_chat_embedding (dim=768)

Setup complete.
```

This is **idempotent** — safe to run at any time. Run it again if you ever hit a `Neo4j IndexNotFound` error after a fresh database or schema change.

---

## Domains

A domain is a YAML schema that scopes ingestion and queries. artmind ships with six built-in domains:

| Domain | Description |
|---|---|
| `general` | Fallback — works for any document |
| `fiction` | Novels and stories — Persons, Locations, Events, Objects |
| `personal_journal` | Journal entries — People, Locations, Activities, Emotions |
| `technical_paper` | Research papers — Methods, Findings, Persons, Concepts |
| `project_governance` | Project docs — Roles, Milestones, Decisions, Risks |
| `sales_collateral` | Sales material — Products, Features, Competitors, Use Cases |

List available domains:

```bash
uv run artmind domains list
```

For further help on the domains commands use:
```bash
uv run artmind domains --help
```

### Hierarchical domains

Domain names are dot-separated, letting you nest sub-domains under a parent. For example, `fiction` is a valid domain, and `fiction.thriller` or `fiction.historical` are sub-domains of it.

All read queries (graph search, vector search, find candidates) match the requested domain **and all of its sub-domains**. A query against `fiction` returns results from `fiction`, `fiction.thriller`, `fiction.historical`, etc. Write operations (ingest, update) always tag data with the exact domain name provided.

```bash
# ingest a thriller novel into its own sub-domain
uv run artmind ingest sync novel.pdf --domain fiction.thriller

# query rolls up all fiction sub-domains automatically
uv run artmind query graph pattern1 --domain fiction --entityClass PERSON
```

**Schema harmonization** ensures sub-domain schemas inherit all entity types from their parent. Run it after updating a parent schema to propagate new entity types down:

```bash
# sync all child schemas against their parents
uv run artmind domains harmonize

# sync one child schema (dry-run to preview changes)
uv run artmind domains harmonize --domain fiction.thriller --dry-run
```

**POOLE+ entity standard**: All built-in schemas follow the POOLE+ convention — `PERSON`, `OBJECT`, `ORGANIZATION`, `LOCATION`, `EVENT` are universal base types present in every domain. Domain-specific types (e.g. `METHOD`, `FINDING` in `technical_paper`) extend these. When creating a custom schema with `artmind-create-schema`, follow the same convention: map characters to `PERSON`, places to `LOCATION`, companies to `ORGANIZATION`, and so on before adding domain-specific extras.

### Creating a custom schema

Use the `artmind-create-schema` Claude Code skill to author a new domain schema tailored to your documents. The skill reads your sample documents, designs entity classes and relationship patterns specific to the domain, and writes a complete `domains/schemas/{name}_schema.yaml` file.

In a Claude Code session within this project:

```
/artmind-create-schema
```

You will be asked for:
- A domain name (e.g. `legal_contract`, `medical_report`)
- A one-sentence description of the documents
- One or more representative sample documents

The skill produces a fully-specified YAML schema with `entities_prompt`, `properties_prompt`, and `relationships_prompt` sections ready for ingestion. Once the file is written, use the new domain name with any `ingest` or `query` command.

---

## Ingesting documents

### Synchronous (recommended for single files)

```bash
uv run artmind ingest sync path/to/document.pdf --domain fiction
uv run artmind ingest sync path/to/notes.md --domain technical_paper
```

artmind will:
1. Convert the document to Markdown (via Docling for PDFs)
2. Split into chunks
3. Extract entities, properties, and relationships with the LLM
4. Write everything to Neo4j with embeddings

### Asynchronous (background worker)

Submit a file to the background queue:

```bash
uv run artmind ingest async path/to/document.pdf --domain fiction
# returns a job_id
```

Check job status:

```bash
uv run artmind dashboard
```

There are other job related commands as well which you can find with:

```bash
uv run artmind ingest --help
```

### Re-running extraction

Re-run only the LLM extraction step on an already-ingested document:

```bash
uv run artmind ingest extract-kg document_name --domain fiction
```

Write previously-extracted KG JSON to Neo4j without re-running the LLM:

```bash
# single document
uv run artmind ingest write-to-graph document_name --domain fiction

# batch — write all documents in a folder
uv run artmind ingest write-to-graph --folder data/kg/fiction
```

In folder mode each immediate sub-folder that contains a `document.json` is written to Neo4j. If `--domain` is omitted the domain is inferred from the folder name.

### Importing KG from external repositories

KG extraction is the most compute-intensive part of the pipeline. In team and multi-region setups it often makes sense to run extraction once and share the results as checked-in JSON files in a Git repository, rather than re-extracting on every machine.

The `pull-kg` command fetches a domain folder from an external repository using a sparse Git checkout (only the target path is downloaded) and copies the document sub-folders into your local `data/kg/<domain>/` directory. From there you load them into Neo4j with `write_to_graph --folder`.

**Typical workflow — importing `sales_collateral` from another region:**

```bash
# 1. Pull the KG JSON from the APAC team's repo
uv run artmind ingest pull-kg \
  --repo git@github.com:acme/apac-kg-store.git \
  --repo-path data/kg/sales_collateral \
  --domain sales_collateral

# 2. Write all pulled documents to Neo4j
uv run artmind ingest write-to-graph --folder data/kg/sales_collateral

# 3. Optionally resolve duplicate entities across the merged data
uv run artmind ingest refine-graph --domain sales_collateral --dry-run
```

**Conflict handling:** If any document sub-folder already exists locally, the pull aborts and lists the conflicting names so you can resolve them manually (e.g. rename or delete the local copy) before re-running.

**Authentication:** The command uses your existing Git credentials (SSH keys, credential helpers). If `GITHUB_TOKEN` is set it is injected into HTTPS URLs as a fallback.

### Entity resolution (graph refinement)

After ingesting several documents into a domain, similar entity names may exist as separate nodes (e.g. "Holmes", "Sherlock Holmes", "Mr. Holmes"). Run entity resolution to cluster and merge them:

```bash
# dry-run: compute proposals, write to file for review
uv run artmind ingest refine-graph --dry-run --domain fiction

# apply proposals from the review file
uv run artmind ingest refine-graph --from-file refine/fiction_proposals.json
```

**Focused refinement with `--filter`**: If you've spotted specific similar entities during an update session that should be merged, use the `--filter` option to focus merge detection on those names:

```bash
# dry-run for specific entities
uv run artmind ingest refine-graph --domain fiction --filter "Holmes,Watson,Moriarty" --dry-run --output merges.json

# review merges.json, then apply
uv run artmind ingest refine-graph --from-file merges.json
```

The `--filter` option accepts comma-separated entity names (case-insensitive substring match) and narrows the merge candidates to only those entities, useful when you want to resolve duplicates without analyzing the entire domain.

### Remove a document

```bash
uv run artmind docs clean --domain fiction document_name
```

---

## Session management

Neo4j is a running service — if your instance is ephemeral (Docker without volumes, a cloud container that gets recycled), the graph data disappears when it stops. Session snapshots let you save and restore the entire graph as a compressed JSON archive.

### Save the graph (end of session)

```bash
uv run artmind session close
# or: just session-close
```

Exports all nodes and relationships to `data/graph_snapshot/snapshot_<timestamp>.tar.gz`.

### Restore the graph (start of session)

```bash
uv run artmind session initiate
# or: just session-initiate
```

Wipes the current Neo4j database, recreates the schema, and restores from the latest snapshot. Pass `--snapshot path/to/file.tar.gz` to restore a specific snapshot instead of the latest.

> **Caution:** `session initiate` is destructive — it deletes all existing data before restoring. It will prompt for confirmation unless you pass `--yes`.

### When to use

- **Persistent Neo4j** (native install, Docker with a named volume): You don't need snapshots — the data survives restarts. Use `artmind setup` after a fresh install to ensure indexes exist.
- **Ephemeral Neo4j** (Docker without volumes, cloud containers): Run `session close` before tearing down and `session initiate` after spinning up.

### Agent integration

These commands are intentionally manual, not automatic hooks. An agent session that only touches code doesn't need Neo4j, and auto-importing a snapshot would be destructive if the database already has current data. If your workflow benefits from automatic restore, add a conditional instruction to your agent rules (e.g. `CLAUDE.md`):

```
If a command fails because Neo4j is empty, run `just session-initiate` to restore from the latest snapshot before retrying.
```

---

## Updating the knowledge graph

Beyond ingesting documents, you can add facts directly in natural language — atomic facts, passages, todos, or pasted text. Each update is extracted, disambiguated against existing entities, and written to Neo4j as a `UserChat` node with full audit metadata.

### Draft — extract and find candidates

```bash
uv run artmind update draft \
  --domain fiction \
  --text "Holmes and Watson first met at St Bartholomew's Hospital in 1881."
```

Returns JSON with:
- `session_id` — carry this for subsequent turns in the same session
- `extracted_entities` — entities the LLM found, each with a `temp_id`
- `extracted_relationships` — relationships between them
- `candidates_per_entity` — existing graph nodes that might match each entity

### Confirm — write to graph

Once you've resolved which extracted entities map to existing nodes (link), should be created new (create), or skipped:

```bash
uv run artmind update confirm \
  --session <session_id> \
  --resolutions '[
    {"entity_temp_id": "e0", "action": "link",   "node_id": "<existing_node_id>"},
    {"entity_temp_id": "e1", "action": "create",  "node_id": null},
    {"entity_temp_id": "e2", "action": "skip",    "node_id": null}
  ]'
```

Returns: `{nodes_created, nodes_updated, relationships_written, user_chat_id}`

### History — list update sessions

```bash
uv run artmind update history
uv run artmind update history --domain fiction --limit 10
```

### Export — dump UserChat nodes to markdown

```bash
# one file per session, in chronological order
uv run artmind update export --format sequential --output data/chats/

# one file per entity, showing all chats that mention it
uv run artmind update export --format by-entity --output data/chats/ --domain fiction
```

### Claude Code skill: `artmind-update`

The conversational way to update the graph. The skill handles domain detection, presents candidates in a single batch, and loops until you're done:

```
/artmind-update
```

Example exchange:

```
You: /artmind-update
I want to add some notes about the Holmes stories.

Skill: Detected domain: fiction. Starting update session.

You: Holmes and Watson first met at Bart's hospital in 1881.

Skill: Found 2 entities:
  "Holmes" — matches: 1. Sherlock Holmes (CHARACTER) ✓   2. Create new
  "Watson" — matches: 1. Dr. Watson (CHARACTER) ✓        2. Create new
  "Bart's hospital" — no matches, will create new.

  Please confirm (reply with: link 1, link 1, create):

You: link 1, link 1, create

Skill: Written. 1 node created, 2 linked, 1 relationship written.
       Anything else to add?
```

---

## Querying

### Graph queries

Inspect the graph schema for a domain:

```bash
uv run artmind query graph metadata --domain fiction
```

List all entities grouped by type:

```bash
uv run artmind query graph entity-listing --domain fiction
uv run artmind query graph entity-listing --domain fiction --nameFilter "Holmes"
```

**Nine graph patterns** cover the common retrieval shapes:

| Pattern | Purpose | Key options |
|---|---|---|
| `pattern1` | List all entities of a class | `--entityClass` |
| `pattern2` | Properties of named entities + document/chat sources | `--entityNameList` |
| `pattern3` | Properties + relationship summary + sources | `--entityNameList` |
| `pattern4` | Full one-hop neighborhood + sources | `--entityClass`, `--entityName` |
| `pattern5` | Paths between two entities | `--entityClass1/2`, `--entityName1/2`, `--mode shortest\|all` |
| `pattern6` | Direct relationships between two entities | `--entityName1`, `--entityName2` |
| `pattern7` | Search entities by name/description fragment | `--searchTerm` |
| `pattern8` | Entities of class X connected to entity Y | `--entityClass`, `--entityName` |
| `pattern9` | Top-N entities by connection degree | `--entityClass`, `--topN` |

Patterns 2, 3, and 4 include source attribution — each row returns `doc_sources` (document chunks that mention the entity) and `chat_sources` (user chat entries that mention it).

Examples:

```bash
# list all locations in a fiction domain
uv run artmind query graph pattern1 --domain fiction --entityClass LOCATION

# get properties of a named person (with document and chat sources)
uv run artmind query graph pattern2 --domain fiction --entityNameList "Sherlock Holmes"

# full neighborhood of a person
uv run artmind query graph pattern4 --domain fiction --entityClass PERSON --entityName "Watson"

# shortest path between two persons
uv run artmind query graph pattern5 --domain fiction \
  --entityClass1 PERSON --entityName1 "Holmes" \
  --entityClass2 PERSON --entityName2 "Moriarty" \
  --mode shortest

# top 5 most-connected persons
uv run artmind query graph pattern9 --domain fiction --entityClass PERSON --topN 5
```

All graph commands emit JSON. Pass `--compact` for single-line output.

### Vector + Text search (combined)

Search source text using both semantic similarity (vector embeddings) and keyword matching (full-text). Results are combined using Reciprocal Rank Fusion to balance both relevance signals. Returns both document chunks (`source_type: "document"`) and user chat entries (`source_type: "user_chat"`):

```bash
uv run artmind query vector-text --domain fiction --topK 5 "Where did Holmes first meet Irene Adler?"
```

This single command automatically handles:
- Semantic similarity via vector embeddings
- Exact phrase and keyword matches via full-text search
- Balanced ranking via Reciprocal Rank Fusion (RRF)
- Sparse results — reduced chance of getting zero hits
- Semantic drift — keyword matching catches when embeddings miss the intent

---

## Claude Code skills

artmind ships with two Claude Code skills, both located under `skills/`.

### `artmind-query`

Ask natural-language questions over any ingested domain. Claude inspects the domain's graph metadata and entity listing, selects the appropriate query pattern(s), runs the CLI commands, and synthesizes a grounded answer using only the returned data.

```
/artmind-query
```

Example:
```
Domain: fiction
Question: Who are the most connected characters, and what do they have in common?
```

The skill is grounded — it will not invent facts not present in the knowledge graph.

### `artmind-update`

Add and update facts through conversational natural language. The skill detects the domain from your input, runs `artmind update draft` to extract entities and find candidates, presents disambiguation choices in one batch, then runs `artmind update confirm` to write to the graph. Multi-turn — all turns in a session share the same `session_id`.

```
/artmind-update
```

---

## Justfile recipes

If you have `just` installed, common commands are available as short recipes:

```bash
just                            # list all recipes
just test                       # run the test suite
just ingest-sync path/to/file   # ingest a file (default domain: general)
just ingest-write-to-graph-folder data/kg/fiction  # batch write a folder of KG JSON
just ingest-pull-kg <repo> <path> <domain>         # pull KG from external repo
just query-graph-metadata fiction
just query-graph-entities fiction
just query-text fiction "your question here"
just session-close              # export Neo4j graph to snapshot
just session-initiate           # wipe Neo4j and restore from latest snapshot
```

---

## Running tests

```bash
uv run --group dev pytest test/ -v
```

---

## Project layout

```
artmind/                core package
  cli.py                Click command definitions
  setup.py              DB + Neo4j index initialization (artmind setup)
  ingest.py             document ingestion pipeline
  kg_pull.py            pull KG JSON from external git repos (sparse checkout)
  extraction.py         shared LLM prompt-build and parse primitives
  update.py             natural-language update backend
  graph_query.py        Neo4j graph query layer (9 patterns)
  graph_snapshot.py     export/import full Neo4j graph as compressed snapshots
  vector_query.py       Neo4j vector search, full-text search, and RRF combining (DocChunk + UserChat)
  refine_graph.py       entity resolution
  harmonizer.py         schema harmonizer — syncs child domain schemas from parent
  worker.py             background ingestion worker
  jobs.py               async job management
  db.py                 SQLite schema (documents, jobs, update sessions/drafts)

domains/schemas/        built-in domain YAML schemas
scripts/
  migrate_poole.py      one-time migration script (CHARACTER/AUTHOR/PLACE → POOLE types)
skills/
  artmind-query/        Claude Code skill — natural-language graph queries
  artmind-update/       Claude Code skill — natural-language graph updates
test/                   pytest test suite
paths.py                central path configuration
justfile                task runner recipes
```

---

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 Surjit Das
