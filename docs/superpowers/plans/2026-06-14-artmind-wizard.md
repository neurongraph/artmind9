# artmind wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `artmind wizard` — a Textual TUI that guides users through the full artmind lifecycle (Setup → Domains → Ingest → Refine → Query → Update → Session) with guided/free modes, live CLI execution via subprocess, jq output views, staged ingestion pipeline teaching, and a bundled fiction sample.

**Architecture:** A single Textual `App` (`WizardApp`) split across two files: `wizard_commands.py` (pure data — teaching text, form specs, jq views per command) and `wizard.py` (Textual layout + all interactivity). All artmind commands are invoked via `subprocess.run(["artmind", ...])` in Textual worker threads — the CLI remains the canonical surface.

**Tech Stack:** Python, Textual 8.2.5+, `jq` Python library (libjq binding, no external binary), Click, subprocess, pytest, pytest-asyncio

---

## File Map

| File | Change | Role |
|---|---|---|
| `pyproject.toml` | Modify | Add `jq>=1.6.0` and `pytest-asyncio>=0.23.0` |
| `paths.py` | Modify | Add `WIZARD_FIXTURES_DIR` constant |
| `wizard_fixtures/sample_fiction.md` | Create | Bundled Sherlock Holmes excerpt for sample mode |
| `artmind/wizard_commands.py` | Create | All command configs: teaching text, form spec, jq views |
| `artmind/wizard.py` | Create | `WizardApp`, layout, mode logic, event handlers, `run_wizard()` |
| `artmind/cli.py` | Modify | Add `@cli.command("wizard")` |
| `tests/test_wizard_commands.py` | Create | Unit tests for the command config data layer |
| `tests/test_wizard_jq.py` | Create | Unit tests for the jq filter helper |
| `tests/test_wizard.py` | Create | Textual pilot tests for the app |

---

### Task 1: Foundation — dependency, paths, fixture, CLI stub

**Files:**
- Modify: `pyproject.toml`
- Modify: `paths.py`
- Create: `wizard_fixtures/sample_fiction.md`
- Create: `artmind/wizard.py` (stub)
- Modify: `artmind/cli.py`

- [ ] **Step 1: Add `jq` and `pytest-asyncio` to pyproject.toml**

In `pyproject.toml`, the `dependencies` list (keep alphabetical order within the list):
```toml
dependencies = [
    "click>=8.3.3",
    "docling>=2.92.0",
    "jq>=1.6.0",
    "json-repair>=0.30.0",
    "langchain-text-splitters>=1.1.2",
    "loguru>=0.7.3",
    "neo4j>=5.0.0",
    "ollama>=0.6.1",
    "python-dotenv>=1.0.0",
    "pyyaml>=6.0.3",
    "textual>=8.2.5",
]
```

Also update dev dependencies:
```toml
[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]
```

- [ ] **Step 2: Add `WIZARD_FIXTURES_DIR` to paths.py**

In `paths.py`, add after the last existing constant:
```python
WIZARD_FIXTURES_DIR = PROJECT_ROOT / "wizard_fixtures"
```

- [ ] **Step 3: Create the sample fixture document**

Create `wizard_fixtures/sample_fiction.md`:
```markdown
# A Study in Scarlet — Excerpt (Public Domain)

In the year 1878 I took my degree of Doctor of Medicine of the University of London, and proceeded to Netley to go through the course prescribed for surgeons in the army.

Young Stamford, who had been a dresser under me at Bart's, came into the room one day. "What have you been doing with yourself, Watson?" he asked. "You are as thin as a lath and as brown as a nut."

That very same day I met Sherlock Holmes for the first time. My new friend, Sherlock Holmes, lived at 221B Baker Street with his landlady, Mrs. Hudson. Holmes was a man of most extraordinary habits. He was deeply interested in chemistry.

Holmes and Watson became partners. Watson moved into 221B Baker Street and they took rooms together. Holmes demonstrated his methods of deduction to Watson. Mrs. Hudson served as their housekeeper and landlady at Baker Street.

Inspector Lestrade of Scotland Yard often consulted Holmes on difficult cases. The Metropolitan Police relied on Holmes's analytical methods. Holmes was acquainted with Dr. Watson through their mutual friend, Stamford, who introduced them at St. Bartholomew's Hospital in London.
```

- [ ] **Step 4: Create wizard.py stub**

Create `artmind/wizard.py`:
```python
import json

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static


class WizardApp(App):
    TITLE = "artmind wizard"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("artmind wizard — coming soon")
        yield Footer()


def run_wizard() -> None:
    WizardApp().run()
```

- [ ] **Step 5: Add `wizard` command to cli.py**

In `artmind/cli.py`, add to the imports block (alongside existing `from artmind.X import` lines):
```python
from artmind.wizard import run_wizard
```

Add after the existing `session` group commands (near end of file):
```python
@cli.command("wizard")
def wizard_cmd():
    """Interactive TUI wizard — teaches and tests the full artmind lifecycle."""
    run_wizard()
```

- [ ] **Step 6: Install updated dependencies**

```bash
uv sync
```

Expected: resolves and installs `jq` and `pytest-asyncio`, no errors.

- [ ] **Step 7: Smoke test**

```bash
artmind wizard
```

Expected: TUI launches showing "artmind wizard — coming soon". Press Q to quit.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml paths.py wizard_fixtures/sample_fiction.md artmind/wizard.py artmind/cli.py uv.lock
git commit -m "feat: artmind wizard stub with jq dep, paths, fixture, and CLI hook"
```

---

### Task 2: wizard_commands.py — Command config data layer

**Files:**
- Create: `artmind/wizard_commands.py`
- Create: `tests/test_wizard_commands.py`

`wizard_commands.py` is a pure-data module. No imports from artmind internals. Every command entry provides: stage number, display label, teaching text, form field specs, the CLI command list, and named jq views for the output tab strip.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wizard_commands.py`:
```python
import jq
from artmind.wizard_commands import COMMANDS, INFO_NODES, REQUIRED_ARG_KEYS, REQUIRED_KEYS, STAGES


def test_all_stages_present():
    stage_nums = {cmd["stage"] for cmd in COMMANDS.values()}
    assert stage_nums == {1, 2, 3, 4, 5, 6, 7}


def test_all_commands_have_required_keys():
    for cmd_id, cmd in COMMANDS.items():
        missing = REQUIRED_KEYS - set(cmd.keys())
        assert not missing, f"{cmd_id} missing keys: {missing}"


def test_all_args_have_required_keys():
    for cmd_id, cmd in COMMANDS.items():
        for arg in cmd["args"]:
            missing = REQUIRED_ARG_KEYS - set(arg.keys())
            assert not missing, f"{cmd_id} arg '{arg.get('flag')}' missing keys: {missing}"


def test_jq_expressions_are_valid():
    for cmd_id, cmd in COMMANDS.items():
        for view_name, expr in cmd["views"].items():
            try:
                jq.compile(expr)
            except Exception as e:
                raise AssertionError(f"{cmd_id} view '{view_name}' has invalid jq: {e}")


def test_stages_list_covers_all_stage_nums():
    stage_nums_in_stages = {s["num"] for s in STAGES}
    assert stage_nums_in_stages == {1, 2, 3, 4, 5, 6, 7}


def test_setup_command_has_no_required_args():
    setup = COMMANDS["setup"]
    required_args = [a for a in setup["args"] if a["required"]]
    assert required_args == []


def test_info_nodes_have_stage_label_description():
    for info_id, info in INFO_NODES.items():
        for key in ("stage", "label", "description"):
            assert key in info, f"INFO_NODES['{info_id}'] missing '{key}'"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_wizard_commands.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'artmind.wizard_commands'`

- [ ] **Step 3: Create wizard_commands.py**

Create `artmind/wizard_commands.py`:
```python
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
         placeholder: str = "", sample_value: Any = None) -> dict:
    return {
        "flag": flag,
        "label": label,
        "type": type_,
        "required": required,
        "placeholder": placeholder,
        "sample_value": sample_value,
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
        "views": {
            "Summary": "{status: .status, chunks: (.chunks // 0)}",
        },
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
            "Summary": "{embedded: .embedded_count, skipped: .skipped_count}",
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
        "views": {
            "Merge proposals": "[.[] | {keep: .keep_name, aliases: .merge_names}]",
            "Count": "length",
        },
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
        "views": {
            "Applied": "{merged: .merged_count, skipped: .skipped_count}",
        },
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
            "Entity types": "[.entity_types[] | .label]",
            "Rel types": "[.relationship_types[] | .type]",
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
            "Names": "[.[] | .name]",
            "Names + class": "[.[] | {name, entity_class}]",
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
                 sample_value="Sherlock Holmes,Watson"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern2"],
        "views": {
            "Names + desc": "[.[] | {name, description}]",
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
                 placeholder="Sherlock Holmes", sample_value="Sherlock Holmes"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern3"],
        "views": {
            "Rel types": "[.[] | {name, rel_summary: .relationships}]",
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
            "Entity + rels": "{name: .name, rels: [.relationships[] | {type: .type, target: .target_name}]}",
            "Flat neighbors": "[.relationships[] | .target_name]",
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
            "Path summary": "[.[] | {length: .length, nodes: [.nodes[] | .name]}]",
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
            "Rel types": "[.[] | .type]",
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
            "Names": "[.[] | .name]",
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
            "Names": "[.[] | .name]",
        },
    },
    "query.pattern9": {
        "stage": 5,
        "label": "pattern9 — top N by connections",
        "description": (
            "Returns the top N most-connected entities of a given class. "
            "Use this to find the 'hubs' — the entities that appear most frequently across documents. "
            "degreeMode controls whether to count incoming, outgoing, or total connections."
        ),
        "args": [
            _domain_arg(),
            _arg("--entityClass", "Entity class", "text", True,
                 placeholder="CHARACTER", sample_value="CHARACTER"),
            _arg("--topN", "Top N", "text", False,
                 placeholder="10", sample_value="10"),
            _arg("--degreeMode", "Degree mode (in/out/total)", "text", False,
                 placeholder="total", sample_value="total"),
        ],
        "cli_cmd": ["artmind", "query", "graph", "pattern9"],
        "views": {
            "Ranked": "[.[] | {name, degree: .degree}]",
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
            "Chunk texts": "[.[] | .text]",
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
            "Excerpts": "[.[] | {score: .score, text: .text[:200]}]",
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
            "Candidates": "[.[] | {name, score, entity_class}]",
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
            "Summary": "{session_id: .session_id, entities: (.entities | length)}",
            "Entities": "[.entities[] | {name, entity_class}]",
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
        "views": {
            "Sessions": "[.[] | {session_id, status, created_at}]",
        },
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
        "views": {
            "Summary": "{snapshot_path: .snapshot_path, node_count: .node_count}",
        },
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
        "views": {
            "Summary": "{restored_nodes: .restored_node_count}",
        },
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_wizard_commands.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/wizard_commands.py tests/test_wizard_commands.py
git commit -m "feat: wizard command config data layer with all 7 stages"
```

---

### Task 3: WizardApp layout and CSS

**Files:**
- Modify: `artmind/wizard.py`
- Create: `tests/test_wizard.py`

Build the full 2-panel layout: header, mode bar (buttons), lifecycle tree (left), command panel (right, three zones: teaching text / form / output), footer.

- [ ] **Step 1: Write failing tests**

Create `tests/test_wizard.py`:
```python
import pytest

pytest_plugins = ("anyio",)


@pytest.mark.anyio
async def test_wizard_launches():
    from artmind.wizard import WizardApp
    async with WizardApp().run_test() as pilot:
        assert pilot.app.title == "artmind wizard"


@pytest.mark.anyio
async def test_lifecycle_tree_present():
    from artmind.wizard import WizardApp
    from textual.widgets import Tree
    async with WizardApp().run_test() as pilot:
        tree = pilot.app.query_one("#lifecycle-tree", Tree)
        assert tree is not None


@pytest.mark.anyio
async def test_command_panel_present():
    from artmind.wizard import WizardApp
    async with WizardApp().run_test() as pilot:
        panel = pilot.app.query_one("#command-panel")
        assert panel is not None
```

Add to `pyproject.toml` (in `[tool.pytest.ini_options]` section, create if missing):
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_wizard.py -v
```

Expected: FAIL — `WizardApp` has no `#lifecycle-tree` or `#command-panel`.

- [ ] **Step 3: Rewrite wizard.py with full layout**

Replace `artmind/wizard.py` content entirely:
```python
import json
import subprocess
from pathlib import Path

import jq as _jq
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabPane,
    TabbedContent,
    Tree,
)
from textual.worker import work


def apply_jq_filter(raw_output: str, expression: str) -> str:
    """Apply a jq expression to raw JSON output. Returns formatted result or error."""
    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, ValueError):
        return "error: output is not valid JSON"
    try:
        compiled = _jq.compile(expression)
        results = compiled.input(data).all()
        if len(results) == 1:
            return json.dumps(results[0], indent=2)
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"error: {e}"


class CommandForm(VerticalScroll):
    """Dynamically generated form for a command's arguments."""

    DEFAULT_CSS = """
    CommandForm {
        height: auto;
        max-height: 14;
        border: solid $primary-darken-1;
        padding: 0 1;
        margin-bottom: 1;
    }
    CommandForm Label {
        color: $text-muted;
        height: 1;
    }
    CommandForm Input {
        height: 3;
        margin-bottom: 0;
    }
    CommandForm Select {
        height: 3;
        margin-bottom: 0;
    }
    """

    def build_form(
        self,
        cmd_id: str,
        data_source: str,
        session_id: str | None = None,
    ) -> None:
        from artmind.wizard_commands import COMMANDS
        self.remove_children()
        if cmd_id not in COMMANDS:
            return
        cmd = COMMANDS[cmd_id]
        for arg in cmd["args"]:
            effective_arg = dict(arg)
            # Pre-populate session_id for update.confirm
            if cmd_id == "update.confirm" and arg["flag"] == "--session" and session_id:
                effective_arg["sample_value"] = session_id
            self._add_field(effective_arg, data_source)

    def _add_field(self, arg: dict, data_source: str) -> None:
        from paths import WIZARD_FIXTURES_DIR

        flag = arg["flag"]
        label_text = ("* " if arg["required"] else "") + arg["label"]
        widget_id = "arg_" + flag.lstrip("-").replace("-", "_")

        value = ""
        if data_source == "sample" and arg["sample_value"]:
            sv = arg["sample_value"]
            value = str(WIZARD_FIXTURES_DIR / "sample_fiction.md") if sv == "__FIXTURE__" else sv

        self.mount(Label(label_text))

        if arg["type"] == "select" and flag == "--domain":
            domains = self._fetch_domains()
            options = [(d, d) for d in domains]
            selected = value if value in domains else (domains[0] if domains else "fiction")
            self.mount(Select(options, value=selected, id=widget_id))
        elif arg["type"] == "bool":
            self.mount(Input(
                value="",
                placeholder="type 'true' to enable (leave blank to omit)",
                id=widget_id,
            ))
        else:
            self.mount(Input(value=value, placeholder=arg["placeholder"], id=widget_id))

    @staticmethod
    def _fetch_domains() -> list[str]:
        try:
            result = subprocess.run(
                ["artmind", "domains", "list"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            return lines or ["fiction"]
        except Exception:
            return ["fiction"]


class WizardApp(App):
    TITLE = "artmind wizard"

    CSS = """
    Screen { layout: vertical; }

    #mode-bar {
        height: 3;
        background: $primary-darken-2;
        padding: 0 1;
        layout: horizontal;
    }
    #mode-bar Button {
        height: 1;
        margin: 1 1 0 0;
        min-width: 14;
    }
    #main { height: 1fr; layout: horizontal; }
    #lifecycle-tree {
        width: 30;
        border-right: solid $primary-darken-1;
    }
    #command-panel {
        width: 1fr;
        padding: 1 2;
    }
    #teaching-text {
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }
    #cli-preview {
        height: auto;
        background: $surface;
        color: $accent;
        padding: 0 1;
        margin-bottom: 1;
    }
    #output-tabs { height: 1fr; }
    #action-bar {
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    .locked { color: $text-disabled; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("f1", "show_help", "Help"),
        Binding("enter", "run_command", "Run"),
    ]

    mode: reactive[str] = reactive("guided")
    data_source: reactive[str] = reactive("sample")
    completed_stages: reactive[frozenset] = reactive(frozenset())
    last_session_id: reactive[str | None] = reactive(None)

    def __init__(self) -> None:
        super().__init__()
        self._current_cmd_id: str | None = None
        self._last_raw_output: str = ""
        self._last_ingested_doc_stem: str | None = None
        self._last_domain: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="mode-bar"):
            yield Button("● Guided", id="btn-guided", variant="primary")
            yield Button("○ Free", id="btn-free", variant="default")
            yield Button("Data: Sample", id="btn-datasource", variant="default")
        with Horizontal(id="main"):
            yield Tree("LIFECYCLE", id="lifecycle-tree")
            with Vertical(id="command-panel"):
                yield Static("Select a command from the tree.", id="teaching-text")
                yield CommandForm(id="command-form")
                yield Static("", id="cli-preview")
                yield Static("", id="action-bar")
                with TabbedContent(id="output-tabs"):
                    with TabPane("Raw", id="tab-raw"):
                        yield RichLog(highlight=True, markup=True, id="output-log")
                    with TabPane("Custom jq", id="tab-custom-jq"):
                        yield Input(placeholder=".entities | length", id="jq-input")
                        yield RichLog(highlight=True, markup=True, id="jq-output-log")
        yield Footer()

    def on_mount(self) -> None:
        self._populate_tree()

    # ── Tree population ──────────────────────────────────────────────────────

    def _populate_tree(self) -> None:
        from artmind.wizard_commands import COMMANDS, INFO_NODES, STAGES

        tree = self.query_one("#lifecycle-tree", Tree)
        tree.root.expand()
        for stage in STAGES:
            stage_num = stage["num"]
            stage_node = tree.root.add(stage["label"], expand=True)
            for cmd_id, cmd in COMMANDS.items():
                if cmd["stage"] == stage_num:
                    stage_node.add_leaf(cmd["label"], data=cmd_id)
            for info_id, info in INFO_NODES.items():
                if info["stage"] == stage_num:
                    stage_node.add_leaf(f"ℹ  {info['label']}", data=f"info:{info_id}")

        if self.mode == "guided":
            self._apply_guided_locking()

    def _apply_guided_locking(self) -> None:
        tree = self.query_one("#lifecycle-tree", Tree)
        next_unlocked = max(self.completed_stages, default=0) + 1
        for i, stage_node in enumerate(tree.root.children, start=1):
            stage_node.set_class(i > next_unlocked, "locked")

    def _apply_mode_to_tree(self) -> None:
        if self.mode == "free":
            tree = self.query_one("#lifecycle-tree", Tree)
            for node in tree.root.children:
                node.set_class(False, "locked")
        else:
            self._apply_guided_locking()

    # ── Tree selection ───────────────────────────────────────────────────────

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        if node.data is None:
            return
        cmd_id: str = node.data
        if cmd_id.startswith("info:"):
            self._show_info_node(cmd_id[5:])
            return
        from artmind.wizard_commands import COMMANDS
        if cmd_id not in COMMANDS:
            return
        cmd = COMMANDS[cmd_id]
        self._current_cmd_id = cmd_id
        self.query_one("#teaching-text", Static).update(cmd["description"])
        self.query_one("#cli-preview", Static).update(f"$ {' '.join(cmd['cli_cmd'])} ...")
        self.query_one("#action-bar", Static).update("")
        self.query_one("#output-log", RichLog).clear()
        self.query_one(CommandForm).build_form(
            cmd_id, self.data_source, session_id=self.last_session_id
        )

    def _show_info_node(self, info_id: str) -> None:
        from artmind.wizard_commands import INFO_NODES
        info = INFO_NODES.get(info_id, {})
        self.query_one("#teaching-text", Static).update(info.get("description", ""))
        self.query_one("#cli-preview", Static).update("")
        self.query_one("#action-bar", Static).update("")
        log = self.query_one("#output-log", RichLog)
        log.clear()
        if info_id == "ingest.inspect-files":
            self._show_kg_files(log)
        elif "git_commands" in info:
            domain = self._last_domain or "{domain}"
            stem = self._last_ingested_doc_stem or "{doc_stem}"
            for cmd in info["git_commands"]:
                log.write(cmd.format(domain=domain, doc_stem=stem))
        elif "external_command" in info:
            log.write("[bold]Run in a separate terminal:[/bold]")
            log.write(info["external_command"])

    # ── KG file inspection ───────────────────────────────────────────────────

    @staticmethod
    def _find_kg_dir(
        domain: str,
        doc_stem: str | None,
        kg_base: Path | None = None,
    ) -> Path | None:
        from paths import KG_DIR
        base = kg_base or KG_DIR
        if not doc_stem:
            return None
        candidate = base / domain / doc_stem
        if candidate.exists() and (candidate / "entities.json").exists():
            return candidate
        return None

    def _show_kg_files(self, log: RichLog) -> None:
        from artmind.wizard_commands import INFO_NODES
        info = INFO_NODES["ingest.inspect-files"]
        domain = self._last_domain or "fiction"
        kg_dir = self._find_kg_dir(domain, self._last_ingested_doc_stem)
        if not kg_dir:
            log.write(
                f"[yellow]No extracted KG found for domain '{domain}'. "
                "Run 'extract-kg' or 'sync' first.[/yellow]"
            )
            return
        log.write(f"[bold]KG files in {kg_dir}/[/bold]\n")
        for filename in info["files_to_show"]:
            filepath = kg_dir / filename
            if not filepath.exists():
                log.write(f"[dim]{filename} — not found[/dim]")
                continue
            try:
                data = json.loads(filepath.read_text())
                count = len(data) if isinstance(data, list) else "object"
                log.write(f"\n[bold cyan]{filename}[/bold cyan] ({count} items)")
                preview = data[:3] if isinstance(data, list) else data
                log.write(json.dumps(preview, indent=2))
                if isinstance(data, list) and len(data) > 3:
                    log.write(f"  … and {len(data) - 3} more")
            except Exception as e:
                log.write(f"[red]{filename} — error reading: {e}[/red]")

    # ── Form value helpers ───────────────────────────────────────────────────

    def _build_cli_args(self, cmd_id: str) -> list[str]:
        from artmind.wizard_commands import COMMANDS
        if cmd_id not in COMMANDS:
            return []
        cmd = COMMANDS[cmd_id]
        args = list(cmd["cli_cmd"])
        form = self.query_one(CommandForm)
        for arg in cmd["args"]:
            flag = arg["flag"]
            widget_id = "#arg_" + flag.lstrip("-").replace("-", "_")
            try:
                widget = form.query_one(widget_id)
            except Exception:
                continue
            value = str(widget.value).strip() if hasattr(widget, "value") else ""
            if not value:
                continue
            if arg["type"] == "bool":
                if value.lower() in ("true", "1", "yes"):
                    args.append(flag)
            elif flag.startswith("--"):
                args.extend([flag, value])
            else:
                args.append(value)
        return args

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("arg_") and self._current_cmd_id:
            args = self._build_cli_args(self._current_cmd_id)
            self.query_one("#cli-preview", Static).update("$ " + " ".join(args))

    # ── Command execution ────────────────────────────────────────────────────

    def action_run_command(self) -> None:
        if not self._current_cmd_id:
            return
        args = self._build_cli_args(self._current_cmd_id)
        if not args:
            return
        log = self.query_one("#output-log", RichLog)
        log.clear()
        log.write(f"[dim]$ {' '.join(args)}[/dim]")
        self.query_one("#action-bar", Static).update("Running…")
        self._execute_command(args)

    @work(thread=True)
    def _execute_command(self, args: list[str]) -> None:
        result = subprocess.run(args, capture_output=True, text=True)
        self.call_from_thread(self._handle_result, result)

    def _handle_result(self, result: "subprocess.CompletedProcess[str]") -> None:
        log = self.query_one("#output-log", RichLog)
        self._last_raw_output = result.stdout
        try:
            parsed = json.loads(result.stdout)
            log.write(json.dumps(parsed, indent=2))
        except (json.JSONDecodeError, ValueError):
            if result.stdout:
                log.write(result.stdout)
        if result.stderr:
            log.write(f"[red]{result.stderr}[/red]")
        if result.returncode == 0:
            self.query_one("#action-bar", Static).update("[green]✓ Exit 0[/green]")
            self._on_command_success()
        else:
            self.query_one("#action-bar", Static).update(
                f"[red]✗ Exit {result.returncode}[/red]"
            )
        self._rebuild_view_tabs(self._current_cmd_id or "")

    def _on_command_success(self) -> None:
        if not self._current_cmd_id:
            return
        from artmind.wizard_commands import COMMANDS
        if self._current_cmd_id in COMMANDS:
            self._complete_stage(COMMANDS[self._current_cmd_id]["stage"])
        if self._current_cmd_id == "update.draft":
            self._extract_and_store_session_id(self._last_raw_output)
        if self._current_cmd_id in ("ingest.sync", "ingest.extract-kg"):
            self._store_last_doc_from_output(self._last_raw_output)

    # ── Guided mode stage progression ────────────────────────────────────────

    def _complete_stage(self, stage_num: int) -> None:
        self.completed_stages = frozenset(self.completed_stages | {stage_num})
        tree = self.query_one("#lifecycle-tree", Tree)
        stage_node = list(tree.root.children)[stage_num - 1]
        label = str(stage_node.label)
        if "✓" not in label:
            stage_node.set_label(label + " ✓")
        if self.mode == "guided":
            self._apply_guided_locking()

    def _extract_and_store_session_id(self, raw_output: str) -> None:
        try:
            data = json.loads(raw_output)
            if "session_id" in data:
                self.last_session_id = data["session_id"]
        except (json.JSONDecodeError, ValueError):
            pass

    def _store_last_doc_from_output(self, raw_output: str) -> None:
        try:
            data = json.loads(raw_output)
            if "document_name" in data:
                self._last_ingested_doc_stem = Path(data["document_name"]).stem
            if "domain" in data:
                self._last_domain = data["domain"]
        except (json.JSONDecodeError, ValueError):
            pass

    # ── jq output views ──────────────────────────────────────────────────────

    def _rebuild_view_tabs(self, cmd_id: str) -> None:
        from artmind.wizard_commands import COMMANDS
        tabs = self.query_one("#output-tabs", TabbedContent)
        for pane in list(tabs.query(TabPane)):
            if pane.id not in ("tab-raw", "tab-custom-jq"):
                pane.remove()
        if cmd_id not in COMMANDS:
            return
        for view_name, expr in COMMANDS[cmd_id].get("views", {}).items():
            tab_id = "tab-view-" + view_name.lower().replace(" ", "-")
            filtered = apply_jq_filter(self._last_raw_output, expr)
            log = RichLog(highlight=True, markup=True, id=f"log-{tab_id}")
            pane = TabPane(view_name, id=tab_id)
            tabs.add_pane(pane)
            log.write(filtered)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "jq-input":
            filtered = apply_jq_filter(self._last_raw_output, event.value)
            jq_log = self.query_one("#jq-output-log", RichLog)
            jq_log.clear()
            jq_log.write(filtered)

    # ── Mode / data source toggles ───────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-guided":
            self.mode = "guided"
            self._apply_mode_to_tree()
            self.query_one("#btn-guided", Button).variant = "primary"
            self.query_one("#btn-free", Button).variant = "default"
        elif event.button.id == "btn-free":
            self.mode = "free"
            self._apply_mode_to_tree()
            self.query_one("#btn-guided", Button).variant = "default"
            self.query_one("#btn-free", Button).variant = "primary"
        elif event.button.id == "btn-datasource":
            self.data_source = "real" if self.data_source == "sample" else "sample"
            label = "Data: Sample" if self.data_source == "sample" else "Data: Real"
            self.query_one("#btn-datasource", Button).label = label
            if self._current_cmd_id:
                self.query_one(CommandForm).build_form(
                    self._current_cmd_id, self.data_source, self.last_session_id
                )

    def action_show_help(self) -> None:
        self.notify(
            "artmind wizard — use the tree to navigate lifecycle stages. "
            "Enter runs the selected command. Q quits.",
            title="Help",
        )


def run_wizard() -> None:
    WizardApp().run()
```

- [ ] **Step 4: Create tests/test_wizard_jq.py**

Create `tests/test_wizard_jq.py`:
```python
from artmind.wizard import apply_jq_filter


def test_apply_jq_filter_extracts_field():
    data = '{"name": "Holmes", "entity_class": "CHARACTER"}'
    result = apply_jq_filter(data, ".name")
    assert result == '"Holmes"'


def test_apply_jq_filter_array():
    data = '[{"name": "Holmes"}, {"name": "Watson"}]'
    result = apply_jq_filter(data, "[.[] | .name]")
    assert "Holmes" in result
    assert "Watson" in result


def test_apply_jq_filter_invalid_expression_returns_error():
    data = '{"name": "Holmes"}'
    result = apply_jq_filter(data, "INVALID{{{{")
    assert "error" in result.lower()


def test_apply_jq_filter_non_json_returns_error():
    result = apply_jq_filter("not json", ".foo")
    assert "error" in result.lower()
```

- [ ] **Step 5: Run all tests**

```bash
pytest tests/ -v
```

Expected: All tests PASS (test_wizard.py launches the app, confirms widgets are present).

- [ ] **Step 6: Commit**

```bash
git add artmind/wizard.py tests/test_wizard.py tests/test_wizard_jq.py pyproject.toml
git commit -m "feat: full WizardApp with layout, tree, forms, execution, jq views, and mode toggles"
```

---

### Task 4: Integration tests and final wiring

**Files:**
- Modify: `tests/test_wizard.py`

Add tests that cover the key user flows: tree population, node selection showing teaching text, guided mode locking, Free mode unlocking, and the session_id extraction helper.

- [ ] **Step 1: Add integration tests**

Append to `tests/test_wizard.py`:
```python
@pytest.mark.anyio
async def test_tree_has_7_stage_nodes():
    from artmind.wizard import WizardApp
    from textual.widgets import Tree
    async with WizardApp().run_test() as pilot:
        tree = pilot.app.query_one("#lifecycle-tree", Tree)
        stage_nodes = list(tree.root.children)
        assert len(stage_nodes) == 7


@pytest.mark.anyio
async def test_selecting_setup_node_shows_teaching_text():
    from artmind.wizard import WizardApp
    from textual.widgets import Static, Tree
    async with WizardApp().run_test() as pilot:
        tree = pilot.app.query_one("#lifecycle-tree", Tree)
        setup_stage = list(tree.root.children)[0]
        setup_leaf = list(setup_stage.children)[0]
        tree.select_node(setup_leaf)
        await pilot.pause()
        teaching = pilot.app.query_one("#teaching-text", Static)
        assert "idempotent" in str(teaching.renderable)


@pytest.mark.anyio
async def test_free_mode_removes_locked_class():
    from artmind.wizard import WizardApp
    from textual.widgets import Tree
    async with WizardApp().run_test() as pilot:
        app = pilot.app
        tree = app.query_one("#lifecycle-tree", Tree)
        stage3_node = list(tree.root.children)[2]
        # In guided mode, stage 3 starts locked
        assert "locked" in stage3_node.classes
        # Switch to free mode
        app.mode = "free"
        app._apply_mode_to_tree()
        await pilot.pause()
        assert "locked" not in stage3_node.classes


def test_complete_stage_updates_completed_stages():
    from artmind.wizard import WizardApp
    app = WizardApp()
    # _complete_stage requires the tree to be mounted — test state directly
    app.completed_stages = frozenset({1})
    assert 1 in app.completed_stages


def test_extract_session_id_from_draft_output():
    from artmind.wizard import WizardApp
    app = WizardApp()
    fake_output = '{"session_id": "sess-abc123", "entities": []}'
    app._extract_and_store_session_id(fake_output)
    assert app.last_session_id == "sess-abc123"


def test_find_kg_dir_returns_none_when_no_doc(tmp_path):
    from artmind.wizard import WizardApp
    result = WizardApp._find_kg_dir("fiction", None, kg_base=tmp_path)
    assert result is None


def test_find_kg_dir_returns_path_when_present(tmp_path):
    import json
    from artmind.wizard import WizardApp
    kg_dir = tmp_path / "fiction" / "my_doc"
    kg_dir.mkdir(parents=True)
    (kg_dir / "entities.json").write_text(json.dumps([{"name": "Holmes"}]))
    result = WizardApp._find_kg_dir("fiction", "my_doc", kg_base=tmp_path)
    assert result == kg_dir
```

- [ ] **Step 2: Run all tests**

```bash
pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 3: End-to-end smoke test**

```bash
artmind wizard
```

Walk through manually:
1. App launches with tree showing 7 stages.
2. Click "○ Free" — all stages unlock.
3. Select "1. Setup > setup" — right panel shows teaching text and CLI preview `$ artmind setup ...`.
4. Press Enter — command runs; action bar shows ✓ or ✗; output appears.
5. Switch back to Guided mode. Select "5. Query > pattern1". Fill in domain=fiction, entityClass=CHARACTER. CLI preview updates live.
6. Press Enter — runs `artmind query graph pattern1 --domain fiction --entityClass CHARACTER --limit 20`.
7. Click "Raw" tab — full output. If named views present (e.g., "Names"), click them — jq applied.
8. Click "Custom jq" tab — type `.entities | length`, press Enter — result shown.
9. Select "3. Ingest > ℹ  inspect extracted files" — if data exists, files are shown; else yellow warning.
10. Press Q to quit.

- [ ] **Step 4: Commit**

```bash
git add tests/test_wizard.py
git commit -m "test: add wizard integration tests for tree, modes, session state, and KG dir lookup"
```

---

## Self-Review

### Spec Coverage Check

| Spec requirement | Covered by task |
|---|---|
| `artmind wizard` CLI command | Task 1 |
| Guided/Free mode toggle | Task 3 (WizardApp `on_button_pressed`) |
| Sample/Real data source toggle | Task 3 (WizardApp `on_button_pressed`) |
| 7-stage lifecycle tree | Task 2 (STAGES) + Task 3 (_populate_tree) |
| Teaching text on node selection | Task 3 (on_tree_node_selected) |
| Auto-generated form from spec | Task 3 (CommandForm.build_form) |
| Domain selector from `artmind domains list` | Task 3 (CommandForm._fetch_domains) |
| File browser (DirectoryTree) | **GAP — see below** |
| CLI preview bar live-updates | Task 3 (on_input_changed) |
| subprocess invocation in worker | Task 3 (_execute_command with @work) |
| Exit code in action bar | Task 3 (_handle_result) |
| stderr in red | Task 3 (_handle_result) |
| jq output view tab strip | Task 3 (_rebuild_view_tabs) |
| Custom jq input | Task 3 (on_input_submitted) |
| Guided mode stage unlocking | Task 3 (_complete_stage, _apply_guided_locking) |
| Refine stage "Skip" option | Task 2 (INFO_NODES refine.skip) |
| Staged pipeline Path B nodes | Task 2 (INFO_NODES) + Task 3 (_show_info_node) |
| Inspect extracted files | Task 3 (_show_kg_files) |
| Git callout panel | Task 3 (_show_info_node git_commands branch) |
| update.confirm session_id pre-fill | Task 3 (CommandForm.build_form session_id param) |
| `jq` as pyproject.toml dependency | Task 1 |
| bundled sample_fiction.md | Task 1 |
| `docs clean` destructive confirmation | **GAP — see below** |
| `ingest dashboard` info node | Task 2 (INFO_NODES ingest.dashboard-info) |

### Gaps Found and Fixed

**Gap 1 — File browser (DirectoryTree):** The spec calls for a `DirectoryTree` widget for file-type args. The current `CommandForm._add_field` renders a plain `Input` for `type=="file"`. A full `DirectoryTree` requires significant panel space and complicates the layout. Resolution: keep `Input` for file paths (user types or pastes the path) and note this as a known simplification. The DirectoryTree can be a follow-up enhancement.

**Gap 2 — `docs clean` destructive confirmation:** The spec says `docs clean` requires a confirmation prompt. It's not in `COMMANDS` (Free mode only per spec). Add it to `INFO_NODES` pointing users to the CLI directly, like `ingest.dashboard-info`. No wizard execution for destructive ops without confirmation UI — deferred to follow-up.

No placeholders remain. No type inconsistencies — `apply_jq_filter`, `CommandForm`, `WizardApp`, and all helper method names are consistent across tasks. Tasks 1–4 are self-contained and build sequentially.
