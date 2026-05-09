# CLI Hyphen Convention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename 7 CLI sub-commands from underscore to hyphen convention so the entire CLI uses hyphens consistently.

**Architecture:** Pure string replacement in `@command()` decorator arguments and their corresponding invocation strings in the justfile, README, and tests. No Python function names, YAML schema keys, or internal variable names change — only the CLI-facing command name strings.

**Tech Stack:** Click CLI (artmind/cli.py), just (justfile), pytest

---

## Commands being renamed

| Old name | New name | Group |
|---|---|---|
| `entities_prompt` | `entities-prompt` | `domains` |
| `properties_prompt` | `properties-prompt` | `domains` |
| `relationships_prompt` | `relationships-prompt` | `domains` |
| `extract_kg` | `extract-kg` | `ingest` |
| `write_to_graph` | `write-to-graph` | `ingest` |
| `entity_listing` | `entity-listing` | `query graph` |
| `vector_text` | `vector-text` | `query` |

## Files being changed

| File | What changes |
|---|---|
| `artmind/cli.py` | 7 `@command("...")` decorator string arguments |
| `justfile` | 8 `uv run artmind ...` invocation strings |
| `README.md` | 7 example command strings |
| `test/test_query_cli.py` | 4 CLI runner invocation strings |

**Not changing:** YAML schema keys (`entities_prompt` etc. in `.yaml` files are data field names). Python function names (`extract_kg`, `write_to_graph`, `entity_listing`, `vector_text_search` in `ingest.py`, `graph_query.py`, `vector_query.py`). Mock payload fields like `"command": "entity_listing"` (returned by Python functions, not the CLI).

---

### Task 1: Update cli.py command decorators

**Files:**
- Modify: `artmind/cli.py:203`, `211`, `219`, `391`, `422`, `669`, `817`

- [ ] **Step 1: Apply the 7 decorator renames**

Make these exact string replacements in `artmind/cli.py`:

```python
# Line 203
@domains.command("entities-prompt")   # was "entities_prompt"

# Line 211
@domains.command("properties-prompt")  # was "properties_prompt"

# Line 219
@domains.command("relationships-prompt")  # was "relationships_prompt"

# Line ~391
@ingest.command("extract-kg")   # was "extract_kg"

# Line ~422
@ingest.command("write-to-graph")   # was "write_to_graph"

# Line ~669
@graph.command("entity-listing")   # was "entity_listing"

# Line ~817
@query.command("vector-text")   # was "vector_text"
```

- [ ] **Step 2: Verify the CLI help renders correctly**

```bash
uv run artmind domains --help
uv run artmind ingest --help
uv run artmind query --help
uv run artmind query graph --help
```

Expected: each renamed command appears with a hyphen in the help output.

- [ ] **Step 3: Spot-check one renamed command end-to-end (requires a running domain)**

```bash
uv run artmind domains entities-prompt fiction
```

Expected: prints the entities prompt (same output as before).

- [ ] **Step 4: Commit**

```bash
git add artmind/cli.py
git commit -m "refactor: rename CLI sub-commands to use hyphens"
```

---

### Task 2: Update justfile invocations

**Files:**
- Modify: `justfile` (lines ~34, ~38, ~42, ~76, ~80, ~84, ~112, ~152)

- [ ] **Step 1: Apply the 8 invocation renames**

Make these exact string replacements in `justfile` (the just recipe names stay unchanged — only the `uv run artmind` argument strings inside each recipe change):

```just
# Line ~34 (domains-entities-prompt recipe body)
    uv run artmind domains entities-prompt {{ domain }}
# was: uv run artmind domains entities_prompt {{ domain }}

# Line ~38 (domains-properties-prompt recipe body)
    uv run artmind domains properties-prompt {{ domain }}
# was: uv run artmind domains properties_prompt {{ domain }}

# Line ~42 (domains-relationships-prompt recipe body)
    uv run artmind domains relationships-prompt {{ domain }}
# was: uv run artmind domains relationships_prompt {{ domain }}

# Line ~76 (ingest-extract-kg recipe body)
    uv run artmind ingest extract-kg {{ document }} --domain {{ domain }}
# was: uv run artmind ingest extract_kg {{ document }} --domain {{ domain }}

# Line ~80 (ingest-write-to-graph recipe body)
    uv run artmind ingest write-to-graph {{ document }} --domain {{ domain }}
# was: uv run artmind ingest write_to_graph {{ document }} --domain {{ domain }}

# Line ~84 (ingest-write-to-graph-folder recipe body)
    uv run artmind ingest write-to-graph --folder '{{ folder }}' {{ if domain != "" { "--domain " + domain } else { "" } }}
# was: uv run artmind ingest write_to_graph --folder ...

# Line ~112 (query-graph-entities recipe body)
    uv run artmind query graph entity-listing --domain {{ domain }}
# was: uv run artmind query graph entity_listing --domain {{ domain }}

# Line ~152 (query-text recipe body)
    uv run artmind query vector-text --domain {{ domain }} --topK {{ top_k }} "{{ question }}"
# was: uv run artmind query vector_text ...
```

- [ ] **Step 2: Verify justfile recipes still work**

```bash
just domains-entities-prompt fiction
just query-graph-entities fiction
```

Expected: same output as before (commands now dispatch to hyphenated CLI names).

- [ ] **Step 3: Commit**

```bash
git add justfile
git commit -m "refactor: update justfile to use hyphenated CLI command names"
```

---

### Task 3: Update README.md examples

**Files:**
- Modify: `README.md` (lines ~253, ~260, ~263, ~462, ~463, ~511)

- [ ] **Step 1: Apply the 7 README example renames**

Make these exact string replacements in `README.md`:

```bash
# Line ~253
uv run artmind ingest extract-kg document_name --domain fiction
# was: uv run artmind ingest extract_kg document_name --domain fiction

# Line ~260
uv run artmind ingest write-to-graph document_name --domain fiction
# was: uv run artmind ingest write_to_graph document_name --domain fiction

# Line ~263
uv run artmind ingest write-to-graph --folder data/kg/fiction
# was: uv run artmind ingest write_to_graph --folder data/kg/fiction

# Line ~462
uv run artmind query graph entity-listing --domain fiction
# was: uv run artmind query graph entity_listing --domain fiction

# Line ~463
uv run artmind query graph entity-listing --domain fiction --nameFilter "Holmes"
# was: uv run artmind query graph entity_listing --domain fiction --nameFilter "Holmes"

# Line ~511
uv run artmind query vector-text --domain fiction --topK 5 "Where did Holmes first meet Irene Adler?"
# was: uv run artmind query vector_text ...
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README command examples to use hyphenated names"
```

---

### Task 4: Update test CLI invocations

**Files:**
- Modify: `test/test_query_cli.py` (lines ~41, ~60, ~79, ~247)

- [ ] **Step 1: Apply the 4 test runner invocation renames**

In `test/test_query_cli.py`, change the strings passed to `runner.invoke(cli, [...])`:

```python
# test_graph_entity_listing_cli_outputs_json (~line 41)
result = runner.invoke(
    cli, ["query", "graph", "entity-listing", "--domain", "fiction"]
)
# was: ["query", "graph", "entity_listing", "--domain", "fiction"]

# test_graph_entity_listing_cli_passes_name_filter (~line 60)
result = runner.invoke(
    cli,
    ["query", "graph", "entity-listing", "--domain", "fiction", "--nameFilter", "holmes"],
)
# was: ["query", "graph", "entity_listing", ...]

# test_graph_entity_listing_cli_passes_count_all (~line 79)
result = runner.invoke(
    cli,
    ["query", "graph", "entity-listing", "--domain", "fiction", "--countAll"],
)
# was: ["query", "graph", "entity_listing", ...]

# test_vector_text_cli_dispatches_and_outputs_json (~line 247)
result = runner.invoke(
    cli,
    [
        "query",
        "vector-text",    # was "vector_text"
        "--domain",
        "fiction",
        "--topK",
        "3",
        "Where did Holmes go?",
    ],
)
```

**Important — do NOT change:**
- `"command": "entity_listing"` in mock payload dicts (these are Python function return values, not CLI command strings)
- `"query_type": "vector_text"` in mock payload dicts (same reason)
- Any calls to Python functions like `graph_query.entity_listing(...)` in `test_graph_query.py` or `vector_query.vector_text_search(...)` in `test_vector_query.py`

- [ ] **Step 2: Run the CLI tests to verify all pass**

```bash
uv run pytest test/test_query_cli.py -v
```

Expected: all tests pass (no failures related to command not found).

- [ ] **Step 3: Run the full test suite to confirm no regressions**

```bash
uv run pytest test/ -v
```

Expected: all previously-passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add test/test_query_cli.py
git commit -m "test: update CLI invocations to use hyphenated command names"
```
