# artmind

> A knowledge system that synchronizes with your mind using artificial intelligence.

artmind ingests documents (PDFs, Markdown, text), extracts entities, properties, and relationships using a local LLM, stores them in a Neo4j knowledge graph with vector embeddings, and lets you query them — either with structured graph patterns via the CLI or through natural language using a Claude Code skill.

Everything runs locally: no cloud APIs, no telemetry.

---

## How it works

```
Document (PDF/MD/text)
    ↓ Docling (PDF → Markdown)
    ↓ Chunking
    ↓ LLM extraction (entities, properties, relationships)
    ↓ Embeddings
    ↓
Neo4j knowledge graph + vector index
    ↓
CLI query (graph patterns + vector search)
    ↓
artmind-query Claude Code skill (natural-language answers)
```

Ingestion is domain-scoped. A **domain** is a YAML schema that tells the LLM what entity types and relationship types to look for (e.g. `fiction`, `technical_paper`, `personal_journal`). Six schemas are bundled; you can add your own.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python >= 3.14.4 | |
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
ollama pull qwen2.5:14b
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
ARTMIND_KG_NEO4J_DATABASE=artmind9

# LLM for extraction
ARTMIND_KG_LLM_PROVIDER=ollama
ARTMIND_KG_LLM_URL=http://localhost:11434
ARTMIND_KG_LLM_MODEL=qwen2.5:14b          # or any capable Ollama model

# Embeddings
ARTMIND_KG_EMBEDDINGS_PROVIDER=ollama
ARTMIND_KG_EMBEDDINGS_URL=http://localhost:11434
ARTMIND_KG_EMBEDDINGS_MODEL=nomic-embed-text:latest
ARTMIND_KG_EMBEDDING_DIMENSIONS=768

# Image descriptions (used when ingesting PDFs that contain images)
ARTMIND_IMAGE_MODEL=gemma4:e4b
ARTMIND_OLLAMA_TIMEOUT=600
```

Verify the CLI is available:

```bash
uv run artmind --help
```

---

## Domains

A domain is a YAML schema that scopes ingestion and queries. artmind ships with six built-in domains:

| Domain | Description |
|---|---|
| `general` | Fallback — works for any document |
| `fiction` | Novels and stories — Characters, Locations, Events, Objects |
| `personal_journal` | Journal entries — People, Places, Activities, Emotions |
| `technical_paper` | Research papers — Methods, Findings, Authors, Concepts |
| `project_governance` | Project docs — Roles, Milestones, Decisions, Risks |
| `sales_collateral` | Sales material — Products, Features, Competitors, Use Cases |

List available domains:

```bash
uv run artmind domains list
```

Inspect a domain's entity and relationship types:

```bash
uv run artmind domains inspect fiction
```

Add a custom domain from a YAML schema file:

```bash
uv run artmind domains add path/to/my_schema.yaml
```

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
uv run artmind ingest status <job_id>
```

List recent jobs:

```bash
uv run artmind ingest list-jobs
```

### Re-running extraction

Re-run only the LLM extraction step on an already-ingested document:

```bash
uv run artmind ingest extract_kg document_name --domain fiction
```

Write previously-extracted KG JSON to Neo4j without re-running the LLM:

```bash
uv run artmind ingest write_to_graph document_name
```

### Entity resolution (graph refinement)

After ingesting several documents into a domain, similar entity names may exist as separate nodes (e.g. "Holmes", "Sherlock Holmes", "Mr. Holmes"). Run entity resolution to cluster and merge them:

```bash
# dry-run: compute proposals, write to file for review
uv run artmind ingest refine-graph --dry-run --domain fiction

# apply proposals from the review file
uv run artmind ingest refine-graph --from-file refine/fiction_proposals.json
```

### Remove a document

```bash
uv run artmind docs clean --domain fiction document_name
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
uv run artmind query graph entity_listing --domain fiction
uv run artmind query graph entity_listing --domain fiction --nameFilter "Holmes"
```

**Nine graph patterns** cover the common retrieval shapes:

| Pattern | Purpose | Key options |
|---|---|---|
| `pattern1` | List all entities of a class | `--entityClass` |
| `pattern2` | Properties of named entities | `--entityNameList` |
| `pattern3` | Properties + relationship summary | `--entityNameList` |
| `pattern4` | Full one-hop neighborhood | `--entityClass`, `--entityName` |
| `pattern5` | Paths between two entities | `--entityClass1/2`, `--entityName1/2`, `--mode shortest\|all` |
| `pattern6` | Direct relationships between two entities | `--entityName1`, `--entityName2` |
| `pattern7` | Search entities by name/description fragment | `--searchTerm` |
| `pattern8` | Entities of class X connected to entity Y | `--entityClass`, `--entityName` |
| `pattern9` | Top-N entities by connection degree | `--entityClass`, `--topN` |

Examples:

```bash
# list all locations in a fiction domain
uv run artmind query graph pattern1 --domain fiction --entityClass LOCATION

# get properties of a named character
uv run artmind query graph pattern2 --domain fiction --entityNameList "Sherlock Holmes"

# full neighborhood of a character
uv run artmind query graph pattern4 --domain fiction --entityClass CHARACTER --entityName "Watson"

# shortest path between two characters
uv run artmind query graph pattern5 --domain fiction \
  --entityClass1 CHARACTER --entityName1 "Holmes" \
  --entityClass2 CHARACTER --entityName2 "Moriarty" \
  --mode shortest

# top 5 most-connected characters
uv run artmind query graph pattern9 --domain fiction --entityClass CHARACTER --topN 5
```

All graph commands emit JSON. Pass `--compact` for single-line output.

### Vector search

Search source text chunks by semantic similarity:

```bash
uv run artmind query vector --domain fiction --topK 5 "Where did Holmes first meet Irene Adler?"
```

---

## Claude Code skill: `artmind-query`

artmind ships with a Claude Code skill that lets you ask natural-language questions over any ingested domain directly from Claude Code.

### Setup

The skill file is at `.claude/skills/artmind-query/SKILL.md`. Claude Code picks it up automatically when you open the project.

### Usage

In a Claude Code session within this project, invoke the skill:

```
/artmind-query
```

Then provide a domain and your question. Claude will inspect the domain's graph metadata and entity listing, select the appropriate query pattern(s), run the CLI commands, and synthesize a grounded answer using only the returned data.

Example exchange:

```
You: /artmind-query
Domain: fiction
Question: Who are the most connected characters, and what do they have in common?
```

The skill is grounded — it will not invent facts not present in the knowledge graph.

---

## Justfile recipes

If you have `just` installed, common commands are available as short recipes:

```bash
just                            # list all recipes
just test                       # run the test suite
just ingest-sync path/to/file   # ingest a file (default domain: general)
just query-graph-metadata fiction
just query-graph-entities fiction
just query-vector fiction "your question here"
```

---

## Running tests

```bash
uv run --group dev pytest test/ -v
```

---

## Project layout

```
artmind/           core package
  cli.py           Click command definitions
  ingest.py        ingestion pipeline
  graph_query.py   Neo4j graph query layer (9 patterns)
  vector_query.py  Neo4j vector search
  refine_graph.py  entity resolution
  worker.py        background ingestion worker
  jobs.py          async job management
  db.py            SQLite registry schema

domains/schemas/   built-in domain YAML schemas
.claude/skills/    artmind-query Claude Code skill
test/              pytest test suite
paths.py           central path configuration
justfile           task runner recipes
```

---

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 Surjit Das
