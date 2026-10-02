# artmind

> A knowledge system that synchronizes with your mind using artificial intelligence.

artmind ingests documents (PDFs, Markdown, text), extracts entities, properties, and relationships using a local LLM, stores them in a Neo4j knowledge graph with vector embeddings, and lets you query and update them — either with structured graph patterns via the CLI or through natural language using Claude Code skills.

Runs fully locally with Ollama and a local Neo4j, or against hosted providers (OpenRouter for the LLM, a hosted Neo4j AuraDB instance) — your choice.

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/neurongraph/artmind9)

---

## Getting started

On a Mac, two commands take you from nothing to a ready Obsidian vault backed
by its own Neo4j graph and a private GitHub repo:

```bash
git clone https://github.com/neurongraph/artmind9.git ~/projects/artmind9
cd ~/projects/artmind9 && bash scripts/bootstrap.sh   # once per machine
# open a new terminal, edit ~/.artmind/config.env (LLM provider, model, key), then:
artmind vault new my_vault                            # once per vault (--join on a second machine)
```

You need [Homebrew](https://brew.sh) installed first.
**[docs/INSTALL.md](docs/INSTALL.md) is the full guide:**
- what `bootstrap.sh` and `vault new` do, and every `vault new` option
  (AuraDB, local-only, joining from a second machine);
- the manual install;
- the vault layout;
- upgrading.

[docs/vault.md](docs/vault.md) explains why the vault works the way it does.

`artmind` is a global command that anchors to whichever **vault** you're
standing in: a directory containing `.artmind/`, found by walking up from the
current directory the way git finds `.git/`. **Run the commands below from
inside a vault.** `uv run artmind` from this checkout is not inside one.

### Configuration

Machine-wide: `~/.artmind/config.env`, created by `bootstrap.sh` from
`artmind/env.example`. It holds the credentials and models shared by every
vault. The shipped defaults:

```dotenv
# LLM for extraction: ollama (default), openrouter (+ ARTMIND_OPENROUTER_API_KEY) or ibm_ica
ARTMIND_KG_LLM_PROVIDER=ollama
ARTMIND_KG_LLM_URL=http://localhost:11434
ARTMIND_KG_LLM_MODEL=ministral-3:14b

# Embeddings (always Ollama)
ARTMIND_KG_EMBEDDINGS_PROVIDER=ollama
ARTMIND_KG_EMBEDDINGS_URL=http://localhost:11434
ARTMIND_KG_EMBEDDINGS_MODEL=nomic-embed-text:latest
ARTMIND_KG_EMBEDDING_DIMENSIONS=768

# Image descriptions (used when ingesting PDFs that contain images)
ARTMIND_IMAGE_MODEL=gemma4:e4b
ARTMIND_OLLAMA_TIMEOUT=600

# Your identity for update audit trails
ARTMIND_USER="Your Name"
```

Per-vault: `<vault>/.artmind/config.env`, this vault's Neo4j connection.
`artmind vault new` writes it from the instance it creates. It's gitignored,
because it holds a password:

```dotenv
ARTMIND_KG_NEO4J_URI=bolt://127.0.0.1:7687
ARTMIND_KG_NEO4J_USERNAME=neo4j
ARTMIND_KG_NEO4J_PASSWORD=...
ARTMIND_KG_NEO4J_DATABASE=neo4j
```

Config loads most-specific-first, so a vault's value overrides the machine's,
and a real environment variable beats both.

### Ollama models

With the default Ollama provider, pull the models the config names:

```bash
ollama pull nomic-embed-text     # embeddings (required)
ollama pull ministral-3:14b      # extraction (or any instruction-following model that emits JSON)
ollama pull gemma4:e4b           # image descriptions (optional, for PDFs with images)
```

Larger extraction models produce better graphs.

### Graph setup

`artmind vault new` already ran `artmind setup`, which creates the SQLite
tables and Neo4j constraints, indexes, vector and full-text indexes. It's
idempotent: re-run it inside the vault after pointing it at a different
Neo4j, or if you hit a Neo4j `IndexNotFound` error.

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

Ingestion and updates are domain-scoped. A **domain** is a YAML schema that tells the LLM what entity types and relationship types to look for (e.g. `fiction`, `technical_paper`, `personal_journal`). Several schemas are bundled; you can add your own.

After ingestion, **curation** (`sameas propose`/`approve`, `ingest refine-graph`, `ingest detect-conflicts`, `ingest detect-supersession`) cleans up the graph — duplicate-entity merging, cross-domain identity, conflict detection, and supersession — with every proposal reviewed before it's approved.

---

## Domains

A domain is a YAML schema that scopes ingestion and queries. artmind ships with these built-in domains:

| Domain | Description |
|---|---|
| `general` | Fallback — works for any document |
| `fiction` | Novels and stories — Persons, Locations, Events, Objects |
| `personal_journal` | Journal entries — People, Locations, Activities, Emotions |
| `technical_paper` | Research papers — Methods, Findings, Persons, Concepts |
| `project_governance` | Project docs — Roles, Milestones, Decisions, Risks |
| `sales_collateral` | Sales material — Products, Features, Competitors, Use Cases |
| `contracts` | Legal agreements — Parties, Clauses, Obligations, Terms |
| `banking.*` | Eight sibling schemas for the bundled banking corpus (cases, policy, SOP guides, products, organization, communications, reference, risk governance) — a worked example of deliberately separated sibling domains |

List available domains:

```bash
artmind domains list
```

For further help on the domains commands use:
```bash
artmind domains --help
```

### Hierarchical domains

Domain names are dot-separated, letting you nest sub-domains under a parent. For example, `fiction` is a valid domain, and `fiction.thriller` or `fiction.historical` are sub-domains of it.

All read queries (graph search, vector search, find candidates) match the requested domain **and all of its sub-domains**. A query against `fiction` returns results from `fiction`, `fiction.thriller`, `fiction.historical`, etc. Write operations (ingest, update) always tag data with the exact domain name provided.

```bash
# ingest a thriller novel into its own sub-domain
artmind ingest sync novel.pdf --domain fiction.thriller

# query rolls up all fiction sub-domains automatically
artmind query graph pattern1 --domain fiction --entityClass PERSON
```

**Schema harmonization** ensures sub-domain schemas inherit all entity types from their parent. Run it after updating a parent schema to propagate new entity types down:

```bash
# sync all child schemas against their parents
artmind domains harmonize

# sync one child schema (dry-run to preview changes)
artmind domains harmonize --domain fiction.thriller --dry-run
```

**POOLE+ entity standard**: All built-in schemas follow the POOLE+ convention — `PERSON`, `OBJECT`, `ORGANIZATION`, `LOCATION`, `EVENT` are universal base types present in every domain. Domain-specific types (e.g. `METHOD`, `FINDING` in `technical_paper`) extend these. When creating a custom schema with `artmind-create-schema`, follow the same convention: map characters to `PERSON`, places to `LOCATION`, companies to `ORGANIZATION`, and so on before adding domain-specific extras.

### Creating a custom schema

Use the `artmind-create-schema` Claude Code skill to author a new domain schema tailored to your documents. The skill reads your sample documents, designs entity classes and relationship patterns specific to the domain, and writes a complete `.artmind/domains/schemas/{name}_schema.yaml` file in the vault.

In a Claude Code session in the vault (the admin-ui agent works too):

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

### Mapping folders to domains

A vault's `.artmind/vault.yaml` says which domain governs which folder — and
what to ingest at all:

```yaml
ingest:
  trigger: manual
  mappings:
    - path: policies/**
      domain: banking.policy
    - path: notes/**
      domain: personal_journal
```

Then one command ingests the whole vault, each folder into its own domain:

```bash
artmind ingest sync .
```

An **unmapped path is never ingested**, so an `attachments/` folder needs no
ignore rule, and an unmapped `Inbox/` becomes a drafting area — moving a note
into a mapped folder is what marks it ready.

First match wins, so a specific rule can sit above a general one. Domain
precedence, highest first: `--setDomain`, then the file's own `_domain`
frontmatter, then the mapping, then `--domain` as the fallback for unmapped
files.

### Synchronous (recommended for single files)

```bash
artmind ingest sync path/to/document.pdf --domain fiction
artmind ingest sync path/to/notes.md --domain technical_paper
```

artmind will:
1. Convert the document to Markdown (via Docling for PDFs)
2. Split into chunks
3. Extract entities, properties, and relationships with the LLM
4. Write everything to Neo4j with embeddings

### Asynchronous (background worker)

Submit a file to the background queue:

```bash
artmind ingest async path/to/document.pdf --domain fiction
# returns a job_id
```

Check job status:

```bash
artmind ingest jobs
artmind ingest job-status <job_id>
```

Or watch it live in the admin UI's dashboard (`artmind admin-ui`, then open `/dashboard`).

There are other job related commands as well which you can find with:

```bash
artmind ingest --help
```

### When a conversion comes out wrong

You do not edit `.artmind/` — artmind owns it. Instead: copy the converted
markdown out into your vault as an ordinary note, move the original binary to
`_Inbox/` so it is not re-ingested, and ingest the note. It is then just a note,
with no special machinery holding it. See [docs/vault.md](docs/vault.md), "The
ownership rule", for why.

### Re-running extraction

Re-run only the LLM extraction step on an already-ingested document:

```bash
artmind ingest extract-kg document_name --domain fiction
```

Write previously-extracted KG JSON to Neo4j without re-running the LLM:

```bash
# single document
artmind ingest write-to-graph document_name --domain fiction

# batch — write all documents in a folder
artmind ingest write-to-graph --folder .artmind/data/kg/fiction
```

In folder mode each immediate sub-folder that contains a `document.json` is written to Neo4j. If `--domain` is omitted the domain is inferred from the folder name.

### Importing KG from external repositories

KG extraction is the most compute-intensive part of the pipeline. In team and multi-region setups it often makes sense to run extraction once and share the results as checked-in JSON files in a Git repository, rather than re-extracting on every machine.

The `pull-kg` command fetches a domain folder from an external repository using a sparse Git checkout (only the target path is downloaded) and copies the document sub-folders into the vault's `.artmind/data/kg/<domain>/`. From there you load them into Neo4j with `write-to-graph --folder`.

**Typical workflow — importing `sales_collateral` from another region:**

```bash
# 1. Pull the KG JSON from the APAC team's repo
artmind ingest pull-kg \
  --repo git@github.com:acme/apac-kg-store.git \
  --repo-path data/kg/sales_collateral \
  --domain sales_collateral

# 2. Write all pulled documents to Neo4j
artmind ingest write-to-graph --folder .artmind/data/kg/sales_collateral

# 3. Optionally resolve duplicate entities across the merged data
artmind ingest refine-graph --domain sales_collateral --dry-run
```

**Conflict handling:** If any document sub-folder already exists locally, the pull aborts and lists the conflicting names so you can resolve them manually (e.g. rename or delete the local copy) before re-running.

**Authentication:** The command uses your existing Git credentials (SSH keys, credential helpers). If `GITHUB_TOKEN` is set it is injected into HTTPS URLs as a fallback.

### Graph curation

Extraction runs per-chunk, so a freshly ingested domain accumulates near-duplicate entities and unadjudicated disagreements between documents. Curation fixes that. Two proposers feed one review queue in `same_as.yaml`; nothing here mutates the graph until you approve:

```bash
# propose: cross-domain/cross-class identity + conflict candidates via the LLM adjudicator
artmind sameas propose --domain fiction --compact

# propose: intra-domain naming-variant merge candidates via name-similarity clustering
artmind ingest refine-graph --domain fiction --dry-run --output merges.json
artmind ingest refine-graph --domain fiction --from-file merges.json

# review the queue, then approve or reject each proposal
artmind sameas list --status open --compact
artmind sameas approve <proposal_id>
artmind sameas reject <proposal_id>
```

Pass `--domain` more than once (to `sameas propose` or `ingest detect-conflicts`) to compare sibling domains — a "same thing" verdict proposes a same-as group; a "conflicting claims" verdict proposes a `Conflict` node instead:

```bash
artmind ingest detect-conflicts --domain banking.policy --domain banking.sop_guides --dry-run --output conflicts.json
artmind ingest detect-conflicts --domain banking.policy --domain banking.sop_guides --from-file conflicts.json
artmind ingest resolve-conflict <conflict_id> --status resolved --reason "..."
```

The `artmind-curate` Claude Code skill (below) drives this workflow conversationally, including the review gates. Other standalone commands:

| Command | What it does |
|---|---|
| `ingest detect-supersession --domain <d>` | Detect explicit supersession notices; stamp `SUPERSEDES` edges and `valid_to` |
| `ingest supersede --domain <d> --newer A --older B` | Record a supersession manually |

**Focused merging with `--filter`**: to merge specific entities you've spotted without analyzing the whole domain:

```bash
artmind ingest refine-graph --domain fiction --filter "Holmes,Watson,Moriarty" --dry-run --output merges.json
artmind ingest refine-graph --from-file merges.json
```

### Remove a document

```bash
artmind docs archive --domain fiction --documentName document_name
```

---

## Session management

Neo4j is a running service — if your instance is ephemeral (Docker without volumes, a cloud container that gets recycled), the graph data disappears when it stops. Session snapshots let you save and restore the entire graph as a compressed JSON archive.

### Save the graph (end of session)

```bash
artmind session close
# or: just session-close
```

Exports all nodes and relationships to `.artmind/data/graph_snapshot/snapshot_<timestamp>.tar.gz`.

### Restore the graph (start of session)

```bash
artmind session initiate
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
artmind update draft \
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
artmind update confirm \
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
artmind update history
artmind update history --domain fiction --limit 10
```

### Export — dump UserChat nodes to markdown

```bash
# one file per session, in chronological order
artmind update export --format sequential --output data/chats/

# one file per entity, showing all chats that mention it
artmind update export --format by-entity --output data/chats/ --domain fiction
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
artmind query graph metadata --domain fiction
```

List all entities grouped by type:

```bash
artmind query graph entity-listing --domain fiction
artmind query graph entity-listing --domain fiction --nameFilter "Holmes"
```

**Ten templated graph patterns** (plus LLM-generated `text2cypher`) cover the common retrieval shapes:

| Pattern | Purpose | Key options |
|---|---|---|
| `pattern1` | List all entities of a class | `--entityClass`, `--limit` |
| `pattern2` | Properties of named entities + document/chat sources | `--entityNameList` / `--entityIdList` |
| `pattern3` | Properties + relationship summary + sources | `--entityNameList` / `--entityIdList` |
| `pattern4` | Full one-hop neighborhood + sources | `--entityClass`, `--entityName` / `--entityId` |
| `pattern5` | Paths between two entities (entity-to-entity edges only) | `--entityClass1/2`, `--entityName1/2` / `--entityId1/2`, `--mode shortest\|all` |
| `pattern6` | Direct relationships between two entities | `--entityName1/2` / `--entityId1/2` |
| `pattern7` | Search entities by name/description fragment (Lucene full-text) | `--searchTerm` |
| `pattern8` | Entities of class X connected to entity Y | `--entityClass`, `--entityName` / `--entityId` |
| `pattern9` | Top-N entities by connection degree | `--entityClass`, `--topN`, `--degreeMode` |
| `pattern10` | Retrieve all text chunks for a named document | `--documentName` |
| `text2cypher` | LLM-generated Cypher from natural language | `"<question>"`, `--dry-run` |

Patterns 2, 3, and 4 include source attribution — each row returns `doc_sources` (document chunks that mention the entity) and `chat_sources` (user chat entries that mention it). Their relationship/neighborhood output traverses entity-to-entity edges only, so source text never leaks into connection lists.

**Precise vs fuzzy entity matching**: every `--entityName*` option matches by case-insensitive substring, which can fan out ("Holmes" matches both "Sherlock Holmes" and "Mycroft Holmes"). Each has an exact-match alternative (`--entityId`, `--entityId1/2`, `--entityIdList`) that pins the node by its `id` property — resolve names first with `entity-resolve` (below), then query by id.

**Entity resolution** maps a free-text reference — a name fragment or a pure description — to canonical graph entities, combining Lucene full-text over names/descriptions with vector similarity over entity embeddings via RRF:

```bash
artmind query entity-resolve --domain fiction "the detective"
# → ranked entities with id, name, entity_class, description
```

Entity embeddings (name + description) are written automatically during ingestion and updates. For graphs created before this feature, backfill once:

```bash
artmind ingest embed-entities --domain fiction
```

**Degree modes for `pattern9`**: by default "most connected" ranks by entity-to-entity relationships. Use `--degreeMode mentions` to rank by how often source chunks/chats mention the entity (salience), or `--degreeMode all` to count every edge.

**Deterministic grounding** — two query-level commands close the loop from graph facts to source text without re-searching:

```bash
# everything about one resolved entity in a single call:
# properties + one-hop relationships + the text of its most current source chunks
artmind query entity-context --domain fiction --entityId <id> --includeChunks 5

# fetch chunk text by exact id (the doc_sources / evidence ids other commands return);
# --expand 1 adds the adjacent chunks of the same document
artmind query chunks --domain fiction --idList <chunk_id> --expand 1
```

**Temporal filtering** — timed nodes carry `valid_from`/`valid_to`; superseded documents carry `superseded_by`. Add `--asOf today` (or any ISO date) to filter to what was valid at that time; untimed knowledge is always visible. `pattern5` and `pattern10` cannot currency-scope their output and report `asOf_ignored: true` instead.

Other query-level commands: `query domains-overview` (per-domain routing summary), `query graph conflicts --domain <d>` (materialized disagreements with evidence), `query graph timeline --domain <d>` (every dated event in a domain, ordered by `valid_from`; `--from`/`--to` narrow it), `query entity-history` (one entity's fact history).

Examples:

```bash
# list all locations in a fiction domain
artmind query graph pattern1 --domain fiction --entityClass LOCATION

# get properties of a named person (with document and chat sources)
artmind query graph pattern2 --domain fiction --entityNameList "Sherlock Holmes"

# full neighborhood of a person
artmind query graph pattern4 --domain fiction --entityClass PERSON --entityName "Watson"

# shortest path between two persons
artmind query graph pattern5 --domain fiction \
  --entityClass1 PERSON --entityName1 "Holmes" \
  --entityClass2 PERSON --entityName2 "Moriarty" \
  --mode shortest

# top 5 most-connected persons
artmind query graph pattern9 --domain fiction --entityClass PERSON --topN 5

# retrieve all text chunks for a document
artmind query graph pattern10 --domain fiction --documentName "The Copper Beeches"

# focused structural metadata (Document/DocChunk/UserChat/Entity counts)
artmind query graph structural-metadata --domain fiction

# LLM-generated Cypher for arbitrary graph questions
artmind query graph text2cypher --domain fiction "How many DocChunks are there for each Document?"

# dry-run: inspect the generated Cypher without executing
artmind query graph text2cypher --domain fiction --dry-run "Which persons are connected to more than 3 locations?"
```

All graph commands emit JSON. Pass `--compact` for single-line output.

### Vector + Text search (combined)

Search source text using both semantic similarity (vector embeddings) and keyword matching (full-text). Results are combined using Reciprocal Rank Fusion to balance both relevance signals. Returns both document chunks (`source_type: "document"`) and user chat entries (`source_type: "user_chat"`):

```bash
artmind query vector-text --domain fiction --topK 5 "Where did Holmes first meet Irene Adler?"
```

This single command automatically handles:
- Semantic similarity via vector embeddings
- Keyword matches via Lucene full-text indexes (BM25-ranked, case-insensitive, punctuation-tolerant)
- Balanced ranking via Reciprocal Rank Fusion (RRF)
- Sparse results — reduced chance of getting zero hits
- Semantic drift — keyword matching catches when embeddings miss the intent

The full-text leg runs on the `chunk_text_ft` and `user_chat_text_ft` indexes created by `artmind setup` — no APOC required for querying.

---

## Claude Code skills

artmind ships with five Claude Code skills, located under `artmind/skills/`.

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

### `artmind-curate`

All graph and structured-store maintenance in one skill: review the same-as proposal queue (merging duplicate entities, linking cross-domain/cross-class identity) with guided review at the judgment gates, adjudicate conflicts, un-merge a bad group, investigate a surprising merge or conflict ("why did these get merged", "real disagreement or an older document?"), and review the structured store's machine-proposed table classifications.

```
/artmind-curate
```

### `artmind-create-schema`

Author a new domain schema from sample documents (described in [Creating a custom schema](#creating-a-custom-schema) above).

```
/artmind-create-schema
```

### `artmind-ingestion-helper`

Interactive guide for the ingestion pipeline — picking the right command, checking job status, diagnosing failed extractions.

```
/artmind-ingestion-helper
```

---

## Justfile recipes

From the checkout, common commands are short `just` recipes. They run with
the checkout as their working directory, so recipes that need a vault are
pointed at one with `ARTMIND_VAULT`, and file arguments need absolute paths:

```bash
just                                    # list all recipes
just dev-install                        # put `artmind` on PATH
just dev-uninstall                      # remove the global artmind command
just dev-test                           # run the test suite
just vault-new my_vault                 # create a vault (flags as for `artmind vault new`)

export ARTMIND_VAULT=~/artmind_vaults/my_vault
just ingest-sync ~/path/to/file         # ingest a file (default domain: general)
just ingest-write-to-graph-folder "$ARTMIND_VAULT/.artmind/data/kg/fiction"   # batch write a folder of KG JSON
just ingest-pull-kg <repo> <path> <domain>                    # pull KG from external repo
just query-graph-metadata fiction
just query-graph-entities fiction
just query-text fiction "your question here"
just session-close                      # export Neo4j graph to snapshot
just session-initiate                   # wipe Neo4j and restore from latest snapshot
```

---

## Running tests

The full suite lives in `test/` and needs no running Neo4j or LLM.

```bash
just dev-test        # = uv run --group dev pytest test/ -v
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
  graph_query.py        Neo4j graph query layer (10 patterns, chunks, entity-context, conflicts, timeline)
  text2cypher.py        LLM-generated Cypher from natural language (text2cypher)
  graph_snapshot.py     export/import full Neo4j graph as compressed snapshots
  vector_query.py       Neo4j vector search, full-text search, entity-resolve, RRF combining
  refine_graph.py       similar-entity clustering and merging
  conflicts.py          conflict detection and materialization (Conflict nodes)
  temporal.py           valid-time normalization and document supersession
  harmonizer.py         schema harmonizer — syncs child domain schemas from parent
  worker.py             background ingestion worker
  jobs.py               async job management
  db.py                 SQLite schema (documents, jobs, update sessions/drafts)
  vault.py              vault discovery + layout (docs/vault.md)
  vault_new.py          `artmind vault new`: provision or join a vault in one command
  vault_sync.py         git-diff-driven replay of committed changes into Neo4j/DuckDB
  obsidian_community.py install Obsidian community plugins for `vault new`
  table2graph.py        project structured-table rows into graph entities
  structured/           the structured (SQL/DuckDB) store
  server.py             the warm `artmind serve` daemon
  webui/                chat UI and admin console (admin-ui)
  skills/                Claude Code skills — source of truth, seeded/symlinked into a vault's .claude/skills/
    artmind-query/        natural-language graph queries
    artmind-update/       natural-language graph updates
    artmind-curate/       same-as review, conflict adjudication, structured-store curation
    artmind-create-schema/ author a new domain schema
    artmind-ingestion-helper/ ingestion pipeline guide
  domains/schemas/       built-in domain YAML schemas, seeded into a new vault

banking_document_corpus/ bundled example corpus for the banking.* domains
obsidian/artmind-obsidian/ the artmind Obsidian plugin (TypeScript)
scripts/
  bootstrap.sh          one-time machine setup (docs/INSTALL.md)
  migrate_poole.py      one-time migration script (CHARACTER/AUTHOR/PLACE → POOLE types)
docs/                   design docs, including vault.md (the vault spec) and INSTALL.md
test/                   pytest suite
paths.py                central path configuration — derives from the discovered vault
justfile                task runner recipes
```

---

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 Surjit Das
