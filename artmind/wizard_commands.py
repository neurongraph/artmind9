"""
Command configuration for the artmind wizard TUI.

COMMANDS: dict keyed by command_id, each entry has:
  stage         — int 1-7, which lifecycle stage
  label         — display name in the tree
  description   — teaching text (2-3 sentences) for the right panel
  args          — list of form field specs (see REQUIRED_ARG_KEYS)
  cli_cmd       — base CLI command as list[str]; form flags are appended at runtime
  views         — dict[view_name, jq_expression] for output tab strip

INFO_NODES: informational-only nodes (no form/execution).

Arg field spec (REQUIRED_ARG_KEYS):
  flag          — CLI flag e.g. "--domain" or positional name e.g. "document_name"
  label         — display label in the form
  type          — "text" | "select" | "file" | "bool"
  required      — bool
  placeholder   — hint text shown in empty input
  sample_value  — value pre-filled in sample mode (None = no pre-fill,
                  "__FIXTURE__" = path to wizard_fixtures/sample_fiction.md)
"""
from typing import Any

REQUIRED_KEYS = {"stage", "label", "description", "args", "cli_cmd", "views"}
REQUIRED_ARG_KEYS = {"flag", "label", "type", "required", "placeholder", "sample_value"}

STAGES = [
    {"num": 1, "label": "1. Setup"},
    {"num": 2, "label": "2. Domains"},
    {"num": 3, "label": "3. Ingest"},
    {"num": 4, "label": "4. Refine", "optional": True},
    {"num": 5, "label": "5. Query"},
    {"num": 6, "label": "6. Update"},
    {"num": 7, "label": "7. Session"},
]


def _arg(flag: str, label: str, type_: str, required: bool,
         placeholder: str = "", sample_value: Any = None, multi: bool = False) -> dict:
    return {
        "flag": flag,
        "label": label,
        "type": type_,
        "required": required,
        "placeholder": placeholder,
        "sample_value": sample_value,
        "multi": multi,
    }


def _domain_arg(sample_value: str = "fiction") -> dict:
    return _arg("--domain", "Domain", "select", True,
                placeholder="fiction", sample_value=sample_value)


COMMANDS: dict[str, dict] = {

    # ── Stage 1: Setup ──────────────────────────────────────────────────────
    "setup": {
        "stage": 1,
        "label": "setup",
        "description": (
            "Initialises the SQLite registry and Neo4j constraints/indexes. "
            "Run this once before any other artmind command. "
            "It is idempotent — safe to re-run if you see index errors."
        ),
        "args": [],
        "cli_cmd": ["artmind", "setup"],
        "views": {},
    },

    # ── Stage 2: Domains ────────────────────────────────────────────────────
    "domains.list": {
        "stage": 2,
        "label": "list",
        "description": (
            "Lists all domain schemas available in this artmind instance. "
            "Domains scope all data — documents, entities, and relationships. "
            "Built-in domains include: general, fiction, technical_paper, "
            "personal_journal, project_governance, sales_collateral."
        ),
        "args": [],
        "cli_cmd": ["artmind", "domains", "list"],
        "views": {},
    },
    "domains.add": {
        "stage": 2,
        "label": "add",
        "description": (
            "Adds a custom domain schema from a YAML file. "
            "The YAML defines entity types and LLM prompts for extraction. "
            "After adding, use 'domains list' to confirm it appears."
        ),
        "args": [
            _arg("domain_file", "Schema YAML file", "file", True,
                 placeholder="/path/to/my_schema.yaml", sample_value=None),
        ],
        "cli_cmd": ["artmind", "domains", "add"],
        "views": {},
    },
    "domains.delete": {
        "stage": 2,
        "label": "delete",
        "description": (
            "Removes a domain schema by name. "
            "This does not delete documents or graph data in that domain — only the schema definition. "
            "Use with care: ingestion into that domain will fail without its schema."
        ),
        "args": [
            _arg("domain_name", "Domain name", "text", True,
                 placeholder="my_domain", sample_value=None),
        ],
        "cli_cmd": ["artmind", "domains", "delete"],
        "views": {},
    },
    "domains.harmonize": {
        "stage": 2,
        "label": "harmonize",
        "description": (
            "Syncs child domain schemas against their parent's entity types. "
            "Use this when you've added a parent domain and want child schemas to "
            "inherit its entity types automatically. "
            "Pass --dry-run to preview changes without writing them."
        ),
        "args": [
            _arg("--domain", "Domain (optional)", "text", False,
                 placeholder="fiction", sample_value=None),
            _arg("--dry-run", "Dry run (preview only)", "bool", False,
                 placeholder="", sample_value=None),
        ],
        "cli_cmd": ["artmind", "domains", "harmonize"],
        "views": {},
    },
    "domains.entities-prompt": {
        "stage": 2,
        "label": "entities-prompt",
        "description": (
            "Displays the entity extraction prompt artmind sends to the LLM for a given domain. "
            "Useful for debugging why certain entities are or aren't being extracted. "
            "The prompt is derived from the domain's YAML schema."
        ),
        "args": [
            _arg("domain_name", "Domain name", "text", True,
                 placeholder="fiction", sample_value="fiction"),
        ],
        "cli_cmd": ["artmind", "domains", "entities-prompt"],
        "views": {},
    },
    "domains.properties-prompt": {
        "stage": 2,
        "label": "properties-prompt",
        "description": (
            "Displays the property extraction prompt artmind sends to the LLM for a given domain. "
            "Shows how the LLM is instructed to extract properties for entities. "
            "The prompt is derived from the domain's YAML schema."
        ),
        "args": [
            _arg("domain_name", "Domain name", "text", True,
                 placeholder="fiction", sample_value="fiction"),
        ],
        "cli_cmd": ["artmind", "domains", "properties-prompt"],
        "views": {},
    },
    "domains.relationships-prompt": {
        "stage": 2,
        "label": "relationships-prompt",
        "description": (
            "Displays the relationship extraction prompt artmind sends to the LLM for a given domain. "
            "Shows how the LLM is instructed to extract relationships between entities. "
            "The prompt is derived from the domain's YAML schema."
        ),
        "args": [
            _arg("domain_name", "Domain name", "text", True,
                 placeholder="fiction", sample_value="fiction"),
        ],
        "cli_cmd": ["artmind", "domains", "relationships-prompt"],
        "views": {},
    },

    # ── Stage 3: Ingest — Full Pipeline ─────────────────────────────────────
    "ingest.sync": {
        "stage": 3,
        "label": "sync",
        "description": (
            "Ingests a file or folder synchronously (blocking). "
            "Runs the full pipeline: convert → chunk → LLM extract → embed → write to Neo4j. "
            "Intermediate JSON files are also written to data/kg/{domain}/{doc_stem}/ for inspection."
        ),
        "args": [
            _arg("file_path", "File or folder path", "file", True,
                 placeholder="/path/to/document.pdf", sample_value="__FIXTURE__"),
            _domain_arg(),
        ],
        "cli_cmd": ["artmind", "ingest", "sync"],
        "views": {},
    },
    "ingest.async": {
        "stage": 3,
        "label": "async",
        "description": (
            "Submits a file or folder to the background ingestion queue (non-blocking). "
            "Returns a job_id immediately — use 'ingest jobs' or 'ingest job-status' to track progress. "
            "The background worker must be running for jobs to process."
        ),
        "args": [
            _arg("file_path", "File or folder path", "file", True,
                 placeholder="/path/to/document.pdf", sample_value="__FIXTURE__"),
            _domain_arg(),
        ],
        "cli_cmd": ["artmind", "ingest", "async"],
        "views": {
            "Job ID": ".job_id",
        },
    },
    "ingest.jobs": {
        "stage": 3,
        "label": "jobs",
        "description": (
            "Lists recent ingestion jobs with their status (queued, processing, completed, failed). "
            "Pass --status to filter by state. "
            "Use job_id from this list with 'ingest job-status' for per-file detail."
        ),
        "args": [
            _arg("--status", "Filter by status", "text", False,
                 placeholder="completed", sample_value=None),
        ],
        "cli_cmd": ["artmind", "ingest", "jobs"],
        "views": {
            "Status counts": "group_by(.status) | map({status: .[0].status, count: length})",
            "Failed only": "[.[] | select(.status == \"failed\")]",
        },
    },
    "ingest.job-status": {
        "stage": 3,
        "label": "job-status",
        "description": (
            "Shows status and per-file progress for a specific ingestion job. "
            "Provide the job_id from 'ingest jobs'. "
            "Use this to track an async job through to completion."
        ),
        "args": [
            _arg("job_id", "Job ID", "text", True,
                 placeholder="abc123-...", sample_value=None),
        ],
        "cli_cmd": ["artmind", "ingest", "job-status"],
        "views": {
            "Summary": "{status: .status, total: .file_count, done: .completed_count}",
        },
    },
    "ingest.embed-entities": {
        "stage": 3,
        "label": "embed-entities",
        "description": (
            "Backfills vector embeddings for entities that were ingested without embeddings. "
            "Run this after bulk-importing KG JSON via 'ingest write-to-graph'. "
            "Embeddings are required for vector-text search to find entities by semantic similarity."
        ),
        "args": [
            _domain_arg(),
        ],
        "cli_cmd": ["artmind", "ingest", "embed-entities"],
        "views": {
            "Summary": "{domain: .domain, embedded: .entities_embedded}",
        },
    },

    # ── Stage 3: Ingest — Staged Pipeline (Path B) ──────────────────────────
    "ingest.extract-kg": {
        "stage": 3,
        "label": "extract-kg",
        "description": (
            "Runs LLM extraction for an already-ingested document, writing 5 JSON files to disk "
            "without touching Neo4j. This is step 1 of the staged pipeline (Path B). "
            "The output files can be reviewed, edited, or committed to git before being written to the graph."
        ),
        "args": [
            _arg("document_name", "Document name", "text", True,
                 placeholder="my_document.pdf", sample_value="sample_fiction.md"),
            _domain_arg(),
        ],
        "cli_cmd": ["artmind", "ingest", "extract-kg"],
        "views": {
            "Summary": "{document: .document_name, chunks: .chunk_count}",
        },
    },
    "ingest.write-to-graph": {
        "stage": 3,
        "label": "write-to-graph",
        "description": (
            "Writes already-extracted KG JSON files to Neo4j — step 2 of the staged pipeline. "
            "Point it at a document name (single) or a folder path (batch mode). "
            "Also used after 'ingest pull-kg' to push pulled KG data into the graph."
        ),
        "args": [
            _arg("--folder", "Folder path (batch mode)", "file", False,
                 placeholder="data/kg/fiction", sample_value=None),
            _arg("document_name", "Document name (single mode)", "text", False,
                 placeholder="my_document.pdf", sample_value=None),
            _domain_arg(),
        ],
        "cli_cmd": ["artmind", "ingest", "write-to-graph"],
        "views": {
            "Summary": "{written: .written_count, skipped: .skipped_count}",
        },
    },
    "ingest.pull-kg": {
        "stage": 3,
        "label": "pull-kg",
        "description": (
            "Pulls pre-extracted KG JSON files from an external GitHub repository using sparse checkout. "
            "Teammates who have already run extraction can share their KG JSON via git, "
            "letting you skip LLM extraction entirely. After pulling, run 'ingest write-to-graph'."
        ),
        "args": [
            _arg("--repo", "GitHub repo (SSH URL)", "text", True,
                 placeholder="git@github.com:org/kg-store.git", sample_value=None),
            _arg("--repo-path", "Path within repo", "text", True,
                 placeholder="data/kg/fiction", sample_value=None),
            _domain_arg(),
        ],
        "cli_cmd": ["artmind", "ingest", "pull-kg"],
        "views": {
            "Pulled docs": "[.[] | .document_name]",
        },
    },

    # ── Stage 4: Refine ─────────────────────────────────────────────────────
    "refine.dry-run": {
        "stage": 4,
        "label": "refine-graph (preview)",
        "description": (
            "Scans the graph for entity names that are likely duplicates "
            "(e.g., 'Holmes' and 'Sherlock Holmes') using string similarity and LLM validation. "
            "Use --dry-run first to review proposals as a JSON file before applying any merges."
        ),
        "args": [
            _domain_arg(),
            _arg("--dry-run", "Preview only (no writes)", "bool", False,
                 placeholder="", sample_value="true"),
            _arg("--output", "Save proposals to file", "text", False,
                 placeholder="data/refine/proposals.json", sample_value=None),
        ],
        "cli_cmd": ["artmind", "ingest", "refine-graph"],
        "views": {},
    },
    "refine.apply": {
        "stage": 4,
        "label": "refine-graph (apply)",
        "description": (
            "Applies a previously generated merge proposal file to the graph. "
            "Each merge combines duplicate entity nodes, updating all relationships. "
            "Review the proposals JSON carefully — this modifies the graph."
        ),
        "args": [
            _arg("--from-file", "Proposals JSON file", "file", True,
                 placeholder="data/refine/proposals.json", sample_value=None),
            _domain_arg(),
        ],
        "cli_cmd": ["artmind", "ingest", "refine-graph"],
        "views": {},
    },

    # ── Stage 5: Query ──────────────────────────────────────────────────────
    "query.metadata": {
        "stage": 5,
        "label": "graph metadata",
        "description": (
            "Returns the schema of the knowledge graph: entity types, relationship types, and counts. "
            "Start here to understand what's in the graph before running pattern queries. "
            "Output is JSON with entity_types and relationship_types arrays."
        ),
        "args": [_domain_arg()],
        "cli_cmd": ["artmind", "query", "graph", "metadata"],
        "views": {
            "Entity types": ".rows | map(select(.category == \"nodes\")) | map(.name)",
            "Rel types": ".rows | map(select(.category == \"relationships\")) | map(.name)",
        },
    },
    "query.pattern1": {
        "stage": 5,
        "label": "pattern1 — list entities",
        "description": (
            "Lists all entities of a given class (e.g., all CHARACTER nodes). "
            "Use this to browse what's in the graph after ingestion. "
            "Limit controls how many are returned."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityClass", "Entity class", "text", True,
                 placeholder="CHARACTER", sample_value="CHARACTER"),
            _arg("--limit", "Limit", "text", False,
                 placeholder="20", sample_value="20"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern1"],
        "views": {
            "Names": "[.rows[] | .entityData.name]",
            "Names + class": "[.rows[] | {name: .entityData.name, entity_class: .entityData.entity_class}]",
        },
    },
    "query.pattern2": {
        "stage": 5,
        "label": "pattern2 — entity info",
        "description": (
            "Returns full info for one or more named entities: description, aliases, properties, and source chunks. "
            "Use entity names from pattern1. "
            "Pass multiple names as a comma-separated list."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityNameList", "Entity names (comma-sep)", "text", True,
                 placeholder="Sherlock Holmes,Watson",
                 sample_value="Sherlock Holmes,Watson", multi=True),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern2"],
        "views": {
            "Names + desc": "[.rows[] | {name: .entityData.name, description: .entityData.description}]",
        },
    },
    "query.pattern3": {
        "stage": 5,
        "label": "pattern3 — entity + rel summary",
        "description": (
            "Returns an entity plus a lightweight summary of its relationships (types and counts only). "
            "Useful for a quick overview of how an entity connects to others without the full neighborhood data. "
            "Faster than pattern4 for large, highly-connected entities."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityNameList", "Entity names (comma-sep)", "text", True,
                 placeholder="Sherlock Holmes", sample_value="Sherlock Holmes", multi=True),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern3"],
        "views": {
            "Rel types": "[.rows[] | {name: .entityData.name, rel_summary: [.connections[]? | select(. != null) | .type]}]",
        },
    },
    "query.pattern4": {
        "stage": 5,
        "label": "pattern4 — full neighborhood",
        "description": (
            "Returns an entity plus its full 1-hop neighborhood: all connected entities and relationship details. "
            "The richest single-entity query — use it for deep dives. "
            "For highly-connected entities, use pattern3 for a lighter summary first."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityClass", "Entity class", "text", True,
                 placeholder="CHARACTER", sample_value="CHARACTER"),
            _arg("--entityName", "Entity name", "text", True,
                 placeholder="Sherlock Holmes", sample_value="Sherlock Holmes"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern4"],
        "views": {
            "Entity + rels": "[.rows[] | {name: .entityData.name, rels: [.connections[]? | select(. != null) | {type: .rel_type, target: .connected_to.data.name}]}]",
            "Flat neighbors": "[.rows[] | .connections[]? | select(. != null) | .connected_to.data.name]",
        },
    },
    "query.pattern5": {
        "stage": 5,
        "label": "pattern5 — paths between entities",
        "description": (
            "Finds shortest path(s) between two entities across the graph. "
            "Use this to understand how two entities are connected through intermediate nodes. "
            "Pass --mode all to get every path (can be slow on large graphs)."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityClass1", "Entity 1 class", "text", True,
                 placeholder="CHARACTER", sample_value="CHARACTER"),
            _arg("--entityName1", "Entity 1 name", "text", True,
                 placeholder="Sherlock Holmes", sample_value="Sherlock Holmes"),
            _arg("--entityClass2", "Entity 2 class", "text", True,
                 placeholder="CHARACTER", sample_value="CHARACTER"),
            _arg("--entityName2", "Entity 2 name", "text", True,
                 placeholder="Watson", sample_value="Watson"),
            _arg("--mode", "Mode (shortest/all)", "text", False,
                 placeholder="shortest", sample_value="shortest"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern5"],
        "views": {
            "Path summary": "[.rows[] | {length: ((.interleavedPath | length) - 1) / 2, nodes: [.interleavedPath[] | select(has(\"label\")) | .data.name]}]",
        },
    },
    "query.pattern6": {
        "stage": 5,
        "label": "pattern6 — direct relationships",
        "description": (
            "Returns all direct relationships between two specific entities. "
            "Use this when you know two entities are connected and want to see all the relationship types. "
            "Faster than pattern5 when you don't need path traversal."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityName1", "Entity 1 name", "text", True,
                 placeholder="Sherlock Holmes", sample_value="Sherlock Holmes"),
            _arg("--entityName2", "Entity 2 name", "text", True,
                 placeholder="Watson", sample_value="Watson"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern6"],
        "views": {
            "Rel types": "[.rows[] | .relType]",
        },
    },
    "query.pattern7": {
        "stage": 5,
        "label": "pattern7 — search by name/desc",
        "description": (
            "Searches for entities whose name or description matches a term (Lucene BM25 fulltext search). "
            "Use this when you know part of an entity name but not the exact string. "
            "Returns ranked results — most relevant first."
        ),
        "args": [
            _domain_arg(),
            _arg("--searchTerm", "Search term", "text", True,
                 placeholder="Baker", sample_value="Baker"),
            _arg("--limit", "Limit", "text", False,
                 placeholder="10", sample_value="10"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern7"],
        "views": {
            "Names": "[.rows[] | .entityData.name]",
        },
    },
    "query.pattern8": {
        "stage": 5,
        "label": "pattern8 — connected to entity",
        "description": (
            "Finds all entities of class X that are connected to a specific entity Y. "
            "Example: all LOCATIONs connected to Sherlock Holmes. "
            "Great for 'where does this entity appear / who does this entity know' questions."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityClass", "Class to find", "text", True,
                 placeholder="LOCATION", sample_value="LOCATION"),
            _arg("--entityName", "Connected to entity", "text", True,
                 placeholder="Sherlock Holmes", sample_value="Sherlock Holmes"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern8"],
        "views": {
            "Names": "[.rows[] | .entityData.name]",
        },
    },
    "query.pattern9": {
        "stage": 5,
        "label": "pattern9 — top N by connections",
        "description": (
            "Returns the top N most-connected entities of a given class. "
            "Use this to find the 'hubs' — the entities that appear most frequently across documents. "
            "degreeMode controls what counts as a connection: 'relations' (entity-entity edges), "
            "'mentions' (how often source documents mention the entity), or 'all' (every edge)."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityClass", "Entity class", "text", True,
                 placeholder="CHARACTER", sample_value="CHARACTER"),
            _arg("--topN", "Top N", "text", False,
                 placeholder="10", sample_value="10"),
            _arg("--degreeMode", "Degree mode (relations/mentions/all)", "text", False,
                 placeholder="relations", sample_value="relations"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern9"],
        "views": {
            "Ranked": "[.rows[] | {name: .entityData.name, degree: .entityData.degree}]",
        },
    },
    "query.pattern10": {
        "stage": 5,
        "label": "pattern10 — document chunks",
        "description": (
            "Retrieves all text chunks for a specific document as stored in Neo4j. "
            "Use this to read back the raw text that was ingested and chunked. "
            "Useful for verifying ingestion quality or debugging extraction issues."
        ),
        "args": [
            _domain_arg(),
            _arg("--documentName", "Document name", "text", True,
                 placeholder="sample_fiction.md", sample_value="sample_fiction.md"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern10"],
        "views": {
            "Chunk texts": "[.rows[] | .chunk.text]",
        },
    },
    "query.vector-text": {
        "stage": 5,
        "label": "vector-text",
        "description": (
            "Semantic + keyword hybrid search using Reciprocal Rank Fusion (RRF). "
            "Combines vector embedding similarity with Lucene BM25 keyword matching. "
            "Use this for natural-language questions against document content."
        ),
        "args": [
            _domain_arg(),
            _arg("question", "Question", "text", True,
                 placeholder="Where did Holmes first meet Watson?",
                 sample_value="Where did Holmes first meet Watson?"),
            _arg("--topK", "Top K results", "text", False,
                 placeholder="5", sample_value="5"),
        ],
        "cli_cmd": ["artmind", "query", "vector-text"],
        "views": {
            "Excerpts": (
                "[.rows[] | (.chunk.text // .chat.raw_text // \"\") as $t | "
                "{score, text: (if ($t | length) > 500 then ($t[0:500] + \"...\") else $t end)}]"
            ),
        },
    },
    "query.entity-resolve": {
        "stage": 5,
        "label": "entity-resolve",
        "description": (
            "Resolves a name fragment or alias to canonical entity records in the graph. "
            "Uses RRF over fulltext and embedding similarity to find the best matches. "
            "Use this when you have a partial name and need the canonical entity for other queries."
        ),
        "args": [
            _domain_arg(),
            _arg("reference", "Name fragment", "text", True,
                 placeholder="the detective", sample_value="the detective"),
            _arg("--topK", "Top K", "text", False,
                 placeholder="3", sample_value="3"),
        ],
        "cli_cmd": ["artmind", "query", "entity-resolve"],
        "views": {
            "Candidates": "[.rows[] | {name: .entity.name, score, entity_class: .entity.entity_class}]",
        },
    },
    "query.text2cypher": {
        "stage": 5,
        "label": "text2cypher",
        "description": (
            "Generates and runs a Cypher query from a natural-language question using the LLM. "
            "Use this for queries that don't fit any of the numbered patterns. "
            "Pass --dry-run to see the generated Cypher without executing it."
        ),
        "args": [
            _domain_arg(),
            _arg("question", "Natural-language question", "text", True,
                 placeholder="Which characters live in Baker Street?",
                 sample_value="Which characters live in Baker Street?"),
            _arg("--dry-run", "Show Cypher, don't run", "bool", False,
                 placeholder="", sample_value=None),
        ],
        "cli_cmd": ["artmind", "query", "graph", "text2cypher"],
        "views": {
            "Results": ". // .",
        },
    },

    # ── Stage 6: Update ─────────────────────────────────────────────────────
    "update.draft": {
        "stage": 6,
        "label": "draft",
        "description": (
            "Submits raw text to the LLM, which extracts entities and relationships, "
            "then finds candidate matches for each in the existing graph. "
            "The returned session_id is automatically pre-populated in the 'confirm' step."
        ),
        "args": [
            _domain_arg(),
            _arg("--text", "Text to add", "text", True,
                 placeholder="Holmes and Watson met at St. Bart's in 1878.",
                 sample_value="Holmes and Watson met at St. Bartholomew's Hospital in 1878."),
        ],
        "cli_cmd": ["artmind", "update", "draft"],
        "views": {
            "Summary": "{session_id: .session_id, entities: (.extracted_entities | length)}",
            "Entities": "[.extracted_entities[] | {name, entity_class}]",
        },
    },
    "update.confirm": {
        "stage": 6,
        "label": "confirm",
        "description": (
            "Writes the confirmed facts from a draft session to Neo4j. "
            "The --session field is pre-filled from the previous 'draft' run. "
            "The --resolutions JSON specifies for each entity: link to existing, create new, or skip."
        ),
        "args": [
            _arg("--session", "Session ID (auto-filled from draft)", "text", True,
                 placeholder="<filled from draft>", sample_value=None),
            _arg("--resolutions", "Resolutions JSON array", "text", True,
                 placeholder='[{"entity_id": "...", "action": "link", "target_id": "..."}]',
                 sample_value=None),
        ],
        "cli_cmd": ["artmind", "update", "confirm"],
        "views": {
            "Written": "{created: .created_count, linked: .linked_count}",
        },
    },
    "update.history": {
        "stage": 6,
        "label": "history",
        "description": (
            "Lists recent update sessions with their status, domain, and creation time. "
            "Use this to audit what facts have been added conversationally. "
            "Filter by domain or user with optional flags."
        ),
        "args": [
            _domain_arg(),
            _arg("--limit", "Limit", "text", False,
                 placeholder="10", sample_value=None),
        ],
        "cli_cmd": ["artmind", "update", "history"],
        "views": {},
    },
    "update.export": {
        "stage": 6,
        "label": "export",
        "description": (
            "Exports all UserChat nodes (conversational fact additions) to a markdown file. "
            "Format can be 'sequential' (chronological) or 'by-entity' (grouped by entity). "
            "Builds a human-readable audit trail of what was added and when."
        ),
        "args": [
            _domain_arg(),
            _arg("--format", "Format (sequential/by-entity)", "text", False,
                 placeholder="sequential", sample_value=None),
            _arg("--output", "Output file path", "text", False,
                 placeholder="output/updates.md", sample_value=None),
        ],
        "cli_cmd": ["artmind", "update", "export"],
        "views": {},
    },

    # ── Stage 7: Session ────────────────────────────────────────────────────
    "session.close": {
        "stage": 7,
        "label": "close",
        "description": (
            "Exports the full Neo4j graph to a compressed snapshot in data/graph_snapshot/. "
            "Use this at the end of a session when running Neo4j ephemerally (e.g., Docker). "
            "The snapshot can be restored with 'session initiate'."
        ),
        "args": [],
        "cli_cmd": ["artmind", "session", "close"],
        "views": {},
    },
    "session.initiate": {
        "stage": 7,
        "label": "initiate",
        "description": (
            "Wipes the current Neo4j graph and restores from a snapshot. "
            "Pass --snapshot to specify a file; otherwise the latest in data/graph_snapshot/ is used. "
            "This is destructive — pass --yes to confirm."
        ),
        "args": [
            _arg("--snapshot", "Snapshot file (optional)", "file", False,
                 placeholder="data/graph_snapshot/snapshot.gz", sample_value=None),
            _arg("--yes", "Confirm destructive wipe", "bool", False,
                 placeholder="", sample_value=None),
        ],
        "cli_cmd": ["artmind", "session", "initiate"],
        "views": {},
    },
}

# Informational-only nodes — no form, no execution; teaching/callout panels only
INFO_NODES: dict[str, dict] = {
    "ingest.inspect-files": {
        "stage": 3,
        "label": "inspect extracted files",
        "description": (
            "After 'extract-kg' (or 'sync') runs, artmind writes 5 JSON files to "
            "data/kg/{domain}/{doc_stem}/. "
            "These represent the complete extracted knowledge: entities, properties, relationships, "
            "chunks, and document metadata. Review them here — this is your QA checkpoint before "
            "the data enters Neo4j."
        ),
        "files_to_show": [
            "entities.json",
            "relationships.json",
            "document.json",
            "properties.json",
            "chunks.json",
        ],
    },
    "ingest.git-callout": {
        "stage": 3,
        "label": "commit KG files to git",
        "description": (
            "The 5 extracted JSON files in data/kg/{domain}/{doc_stem}/ are safe to commit to git. "
            "Teammates can pull them with 'ingest pull-kg' and run 'ingest write-to-graph' — "
            "skipping LLM extraction entirely. This enables collaborative KG building without "
            "re-running expensive LLM calls."
        ),
        "git_commands": [
            "git add data/kg/{domain}/{doc_stem}/",
            'git commit -m "feat: add extracted KG for {doc_stem}"',
            "git push",
        ],
    },
    "ingest.dashboard-info": {
        "stage": 3,
        "label": "dashboard (external)",
        "description": (
            "The ingestion dashboard is a live TUI showing async job progress — "
            "but it cannot run inside the wizard since both apps own the terminal. "
            "Run it in a separate terminal window while async jobs are processing."
        ),
        "external_command": "artmind ingest dashboard",
    },
    "refine.skip": {
        "stage": 4,
        "label": "skip refinement",
        "description": (
            "Refinement is optional. Skip it if your domain has clean entity names with few duplicates. "
            "You can always run refinement later — it operates on the live graph and is safe in "
            "--dry-run mode. Proceed to the Query stage."
        ),
    },
}
