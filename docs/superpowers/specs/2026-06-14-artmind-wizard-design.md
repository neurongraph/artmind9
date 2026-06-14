# artmind wizard — TUI Design Spec

**Date:** 2026-06-14
**Status:** Draft

## Context

artmind has a rich CLI with ~35 commands spanning a 7-stage lifecycle (Setup → Domains → Ingest → Refine → Query → Update → Session). New users face a steep learning curve because the lifecycle order, the purpose of each command, and the relationship between stages are not obvious from the CLI alone.

This spec defines `artmind wizard`: an interactive terminal UI (Textual) that serves three purposes simultaneously — teaching the lifecycle, testing commands against real data, and demonstrating artmind's capabilities to stakeholders.

---

## Goals

1. **Teaching** — walk a new user through the full artmind lifecycle step by step, with explanation at each stage.
2. **Testing** — run real CLI commands against a chosen domain and documents, verify output interactively.
3. **Demo** — show stakeholders the full capability set with live results and polished output views.

---

## Non-Goals

- No web UI (pure terminal, Textual-based).
- No mocking or stubbing of commands — all invocations go through the real `artmind` CLI.
- No git operations performed by the wizard itself (git workflow is explained, not executed).

---

## Architecture

### Entry Point

New Click command added to `artmind/cli.py`:

```
artmind wizard
```

Launches `WizardApp` defined in `artmind/wizard.py`.

### New Files

```
artmind/
  wizard.py              # WizardApp (Textual App, layout, mode logic)
  wizard_commands.py     # Per-command config: teaching text, form spec, jq views, CLI arg builder
wizard_fixtures/
  sample_fiction.md      # Bundled Sherlock Holmes excerpt (public domain, ~500 words)
```

### Dependencies Added to pyproject.toml

- `jq` — Python binding for libjq; used for output view filtering (no external binary required)

---

## Layout

```
┌─────────────────────────────────────────────────────────────┐
│  artmind wizard   [● Guided]  [○ Free]   Data: [Sample ▾]  │  ← header
├────────────────┬────────────────────────────────────────────┤
│ LIFECYCLE      │  STAGE TITLE                               │
│                │  ─────────────────────────────────────────│
│ ▶ 1. Setup  ✓ │  Teaching text (2-3 sentences)             │
│ ▶ 2. Domains  │                                            │
│   ▶ list      │  FORM                                       │
│   ▶ add       │  ┌──────────────────────────────────────┐  │
│ ▶ 3. Ingest   │  │  --domain:  [fiction           ]     │  │
│   ▶ sync      │  │  --text:    [Holmes met Watson ]     │  │
│   ▶ staged    │  └──────────────────────────────────────┘  │
│ ▶ 4. Refine   │                                            │
│ ▶ 5. Query    │  $ artmind update draft --domain fiction   │  ← CLI preview
│   ▶ pattern1  │    --text "Holmes met Watson"              │
│   ▶ ...       │  ──────────── OUTPUT ────────────────────  │
│ ▶ 6. Update   │  [Raw ●]  [Summary]  [Entities]  [jq ▸]   │  ← view tabs
│   ▶ draft     │                                            │
│   ▶ confirm   │  { ... scrollable output ... }             │
│ ▶ 7. Session  │                                            │
│               │  [Run ▶]  [Copy CLI]  [Clear]              │  ← action bar
├───────────────┴────────────────────────────────────────────┤
│  Tab: next field  Enter: run  F1: help  Q: quit            │  ← key hints
└─────────────────────────────────────────────────────────────┘
```

### Left Panel — Lifecycle Tree

- Textual `Tree` widget.
- Each stage is a collapsible node; commands are leaf nodes.
- **Guided mode:** stages unlock sequentially. Completed stages show ✓ and remain accessible for review. Future stages are dimmed and non-interactive.
- **Free mode:** all nodes immediately accessible, no locking.
- Stage 4 (Refine) shows a "Skip" option in Guided mode since not all workflows require entity resolution.

### Right Panel — Stage/Command Panel

Three zones stacked vertically:

1. **Teaching text** — 2-3 sentences: what this command does, when to use it in the lifecycle, what to expect in the output.
2. **Form** — input fields auto-generated from `wizard_commands.py` spec. `--domain` pre-fills from the active data source. Required fields marked. Optional fields collapsed by default. Live-updates the CLI preview bar as you type.
3. **Output panel** — scrollable `RichLog`. Has a view tab strip above it (see Output Views below). Exit code shown in action bar as ✓ (green) or ✗ (red). stderr shown in a distinct colour below stdout.

### Header Bar

- **Mode toggle:** Guided / Free (radio buttons).
- **Data source toggle:** Sample / Real (dropdown). Sample uses the bundled `fiction` fixture. Real connects to the user's live Neo4j/artmind instance.

---

## Lifecycle Stages & Commands

| Stage | Commands |
|---|---|
| **1. Setup** | `setup` |
| **2. Domains** | `list`, `add` (with file browser), `delete`, `harmonize`, `entities-prompt` |
| **3. Ingest** | `sync` (with file browser), `async` (with file browser), `jobs`, `job-status`, `embed-entities`; **Staged Pipeline** sub-path (see below) |
| **4. Refine** | `refine-graph --dry-run` (preview), `refine-graph --from-file` (apply) |
| **5. Query** | `graph metadata`, `pattern1`–`pattern10`, `vector-text`, `entity-resolve`, `text2cypher` |
| **6. Update** | `draft`, `confirm` (pre-populated from prior draft session), `history`, `export` |
| **7. Session** | `close`, `initiate` |

`ingest dashboard` and `docs clean` are available in Free mode only. `dashboard` shows an informational node with the command to run in a separate terminal — it cannot be launched from within the wizard since both are Textual apps that own the terminal. `docs clean` is treated as destructive — requires a confirmation prompt in the wizard before running.

---

## File & Domain Selection (Testing Workflow)

For commands that take a file path (`ingest sync`, `ingest async`, `domains add`, `ingest extract-kg`), the form includes:

- **Domain selector:** dropdown populated live from `artmind domains list`.
- **File/folder browser:** Textual `DirectoryTree` widget embedded in the form, letting the user navigate and select from anywhere on disk.

In Sample mode, the file path is pre-filled with `wizard_fixtures/sample_fiction.md` and the domain pre-set to `fiction`.

---

## Staged Ingestion Pipeline (Teaching Element)

A dedicated sub-path under Ingest exposes the two-step pipeline that `ingest sync` performs internally:

```
Path A: Full Pipeline
  sync / async  →  (extract + write happen internally)  →  Neo4j

Path B: Staged Pipeline
  1. extract-kg       →  5 JSON files written to data/kg/{domain}/{doc_stem}/
  2. Inspect files    →  view entities.json, relationships.json in the output panel
  3. [Callout]        →  these files are safe to commit to git for team sharing
  4a. write-to-graph  →  push local JSONs to Neo4j
  OR
  4b. pull-kg → write-to-graph  →  pull from GitHub, then push to Neo4j
```

### The 5 Intermediate Files

After `extract-kg` runs, the wizard highlights these files in `data/kg/{domain}/{doc_stem}/`:

| File | Contains |
|---|---|
| `document.json` | Document node metadata (id, name, path, domain) |
| `chunks.json` | All text chunks with embeddings |
| `entities.json` | Deduplicated entities extracted across all chunks |
| `properties.json` | Entity properties indexed by entity id |
| `relationships.json` | All relationships + EXTRACTED_FROM edges |

**Step 2 (Inspect files):** A dedicated wizard node reads these files and displays them in the output panel with view switching (raw JSON, entities-only jq view, relationships-only jq view). This is where the user sees what the LLM extracted before it enters the graph — a key QA and teaching moment.

**Step 3 (Git callout):** An informational panel explains that these 5 files can be committed to git. Shows the git commands to do so. The wizard does not execute git operations.

Per-chunk working files in `data/kg/{domain}/{doc_stem}/chunks/` are excluded from this view — they are resumption intermediates, not meant for sharing.

---

## Output Views

Every command's output panel has a tab strip:

```
[Raw ●]  [Summary]  [<command-specific view>]  [Custom jq ▸]
```

- **Raw:** full output, pretty-printed JSON if parseable, else plain text via `RichLog`.
- **Summary:** a jq expression that extracts key fields (e.g., `{entity_count, session_id}` for `update draft`).
- **Command-specific views:** 1-2 named views per command, defined in `wizard_commands.py` as jq expressions.
- **Custom jq:** always present; user types a jq expression interactively, result updates in the output panel.

Views are defined alongside the command config in `wizard_commands.py`. The `jq` Python library (wraps libjq) handles all filtering — no external binary needed.

Example views for selected commands:

| Command | Named views |
|---|---|
| `update draft` | Summary `{entity_count, session_id}`, Entities (names + classes) |
| `query graph pattern4` | Entity + relationships list, Flat connected names |
| `ingest jobs` | Status summary by state, Failed files only |
| `entities.json` (inspect) | Entity names by class, Relationship types count |

---

## Command Invocation

All commands run via `subprocess.run(["artmind", ...args], capture_output=True)` in a Textual worker thread so the UI never blocks. This ensures:

- The CLI remains the canonical surface — internal function changes are automatically reflected.
- The CLI preview bar and the actual invocation are identical.
- Full CLI middleware (logging, argument validation, error formatting, output formatting) is exercised exactly as in real use.

stdout → output panel. stderr → distinct colour below stdout. Exit code → action bar indicator.

---

## Update Confirm Flow

`update draft` stores its `session_id` in wizard state. When the user navigates to `update confirm`, the `--session` field is pre-populated and `--resolutions` is pre-filled with a template derived from the draft output. This makes the two-step draft→confirm flow feel connected rather than requiring the user to manually copy values.

---

## Bundled Sample Data

- Domain: `fiction` (ships as a built-in artmind domain).
- Fixture document: `wizard_fixtures/sample_fiction.md` — a short Sherlock Holmes excerpt (~500 words, public domain).
- In Sample mode, all query form fields include placeholder hints for entity names that exist in the sample graph (e.g., `Sherlock Holmes`, `Watson`, `Baker Street`).

---

## Verification

1. Run `artmind wizard` — confirm TUI launches with header, tree, and right panel.
2. Guided mode: complete Setup, verify ✓ appears and Domains unlocks.
3. Sample mode: run `ingest sync` with pre-filled fixture path — confirm JSON files created in `data/kg/fiction/`.
4. Staged pipeline: run `extract-kg`, navigate to Inspect files node, verify entities.json displays with view tabs.
5. Query `pattern1` with `--entityClass CHARACTER` against sample domain — confirm scrollable output and jq Summary view works.
6. Custom jq tab: type `.entities | length` — confirm result updates.
7. Free mode: confirm all tree nodes accessible immediately.
8. Real mode: switch data source, pick a real document with file browser, run `ingest sync`, verify it ingests.
9. `update draft` → `update confirm` — confirm session_id pre-populates in confirm form.
