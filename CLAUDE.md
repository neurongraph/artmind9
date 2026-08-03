# CLAUDE.md

Guidance for AI agents working in this repo. `AGENT.md` and `AGENTS.md` are symlinks
to this file — edit only `CLAUDE.md`.

## What artmind is

A knowledge-graph system: it ingests documents, extracts entities/relationships with
an LLM, stores them in Neo4j, and answers natural-language questions over the result.
The surface is a single Click CLI, `artmind`, plus FastAPI web UIs: an end-user
chat UI (`chat-ui`) and an operator admin console (`admin-ui`, agent console +
ingest dashboard).

## Repo layout

| Path | Holds |
|---|---|
| `artmind/cli.py` | The whole Click CLI. Command groups: `query` (+ nested `query graph`), `ingest`, `domains`, `docs`, `update`, `session`, plus top-level `init`/`setup`/`serve`/`chat-ui`/`admin-ui`. |
| `artmind/_entry.py` | Console-script entry point. **Proxies `query` calls to the `serve` daemon** — stdlib-only by design. See "Testing implications". |
| `artmind/graph_query.py`, `vector_query.py`, `text2cypher.py` | Query layer: templated Cypher patterns, RRF vector+fulltext search, LLM-generated Cypher. |
| `artmind/ingest.py`, `extraction.py`, `jobs.py`, `worker.py` | Ingestion pipeline and its background worker. |
| `artmind/refine_pipeline.py`, `refine_graph.py`, `conflicts.py`, `consolidate.py`, `temporal.py`, `harmonizer.py` | Graph maintenance: merging, conflict detection, temporal normalization. |
| `artmind/skills/` | **Source of truth for agent skills.** Shipped in the wheel and seeded into the run folder. |
| `artmind/domains/schemas/` | Default domain schemas (YAML), also seeded. |
| `artmind/webui/` | Chat UI (`index.html`), admin console (`admin.html` + Lane A agent chat + Lane B `dashboard.html`/`dashboard_routes.py`), and the generated help/concept catalogue (`help.py`). |
| `artmind/cli_guide.py` | Renders the CLI as an HTML **fragment** for the admin-ui's "CLI guide" tab (`GET /api/cli-guide`), styled by `dashboard.css`. Ordering comes from `COMMAND_GROUPS` in `cli.py`; `test/test_cli_guide.py` fails if a command isn't routed there. There is no checked-in HTML copy. |
| `artmind/schema_reference.py` | Parses `domains/schemas/*_schema.yaml` prompts and renders an HTML **fragment** for the admin-ui's "Schemas" tab (`GET /api/schema-reference?prefix=`), reading the run folder's live schemas on every request. Grouped by domain family — the part of a filename before its first `.`. No checked-in copy. |
| `artmind/server.py` | The warm `serve` daemon. |
| `artmind/opencode/` | opencode/ACP persona, seeded into the run folder. |
| `artmind/setup.py` | `scaffold_run_folder()` (the `init` command) and Neo4j constraint/index setup. |
| `paths.py` | **Root-level module** (not inside the package). Resolves `ARTMIND_HOME` / `ARTMIND_DATA_DIR` and loads `.env`. Packaged via `py-modules`. |
| `utils/` | Shared helpers; packaged alongside `artmind`. |
| `test/` | The test suite — **singular**. |
| `docs/INSTALL.md` | Authoritative install/runtime reference. Read it before changing packaging. |
| `justfile` | Task runner; the entry point for every routine operation. |

Not source, do not edit: `build/`, `artmind9.egg-info/`, `__pycache__/`.
`tests/` (plural) is a **vestigial directory containing only stale `__pycache__`** —
the real suite is `test/`. Add tests to `test/`.

## Installed, not run from the checkout

This is the part that most often breaks assumptions. `artmind` is a **globally
installed command**, not something you invoke from the source tree.

```
just dev-install
```

expands to: stop daemons → `uv tool install --force --editable .` → `artmind init`.

Two roots, both **decoupled from this checkout** (see `paths.py`, `docs/INSTALL.md`):

- **Run folder** — `$ARTMIND_HOME`, default `~/.artmind`. Holds `.env`,
  `.claude/skills/`, `.opencode/`, `domains/schemas/`, `logs/`. Every command reads it.
  Resolved *before* `.env` is loaded, so it can only be overridden by a real env var.
- **Data dir** — `$ARTMIND_DATA_DIR`, default `~/artmind_data`. Ingestion artifacts
  only (originals, markdowns, registry DB, jobs, KG staging, snapshots). Query-only
  hosts never touch it.

The install is **editable**, so Python code edits are live everywhere immediately.
The mental model that matters: *editable covers the code, but not the two caches
in front of it* — long-running daemons, and run-folder copies.

## Testing implications

Read this before concluding a change works.

### 1. A running daemon serves stale code

`artmind serve` (port `$ARTMIND_SERVE_PORT`, default 8377) imports the code **once at
start**. `artmind/_entry.py` proxies every `query` call to it when it's alive. So after
editing the query layer, a running daemon happily answers with the **old** build and
your verification proves nothing.

This catches `uv run` too — `uv run artmind query ...` uses the same entry point and
**also proxies**. There is no invocation route that escapes it by accident.

```bash
ARTMIND_NO_PROXY=1 artmind query ...   # force in-process; what you almost always want when testing
just dev-stop-daemons                      # or kill it (also finds the ingestion worker)
```

To tell whether a daemon is masking your change, compare the two — if they disagree,
the daemon is stale:

```bash
artmind query --help
ARTMIND_NO_PROXY=1 artmind query --help
curl -s http://127.0.0.1:8377/health
```

`just dev-stop-daemons` identifies `serve` by the port it holds and verifies the process is
artmind before killing, so it won't touch an unrelated process on that port.

### 2. Skills reach the chat UI only through the run folder

`artmind/skills/` is the **single source of truth**. It reaches consumers two ways:

- **Checkout** — `.claude/skills/<name>` and `.pi/skills/<name>` are **symlinks** into
  `artmind/skills/` (`just dev-refresh-skills` regenerates them; both dirs are gitignored, so
  a fresh clone lacks them). Editing through a symlink edits the source. Always live.
- **Run folder** — `~/.artmind/.claude/skills/<name>` is a **copy**, written by
  `artmind init`. The chat UI agent's `cwd` is the run folder
  (`artmind/webui/agent.py`), so this copy is what it actually reads.

`init` (`_seed_tree()` in `artmind/setup.py`) **overwrites** skills and `.opencode/` on
every run — they're package assets — while seeding `.env` and `domains/schemas/` only
when absent, so user data survives. So a skill edit reaches the chat UI via:

```bash
artmind init      # or just dev-install, which runs it
```

Editing a skill and testing only in the checkout does **not** exercise what the chat UI
runs. If they disagree, the run folder wasn't re-seeded. Do not edit
`~/.artmind/.claude/skills/` — `init` will overwrite it.

### 3. Green tests do not mean the CLI works

```bash
just dev-test        # uv run --group dev pytest test/ -v
```

923 tests run in ~9s with **no Neo4j and no network** — they import modules directly
(`from artmind.cli import cli`) and drive Click via `CliRunner`, with external services
mocked. That makes them fast and hermetic, but it means they:

- bypass the `_entry` proxy entirely, so they can never catch a stale-daemon problem;
- never touch a real Neo4j, so they can't validate Cypher against actual data.

Unit tests are the right first check, but end-to-end behaviour needs a real invocation
against a live Neo4j with `ARTMIND_NO_PROXY=1` (or a freshly restarted daemon).

**A mocked graph session answers every query successfully.** A bare `MagicMock()` session
returns a truthy result for *any* Cypher, so a test passes identically whether the query
matched the right node, the wrong node, or nothing at all — and an empty Neo4j `MATCH`
raises nothing, it just does no work. That combination hid a real defect in
`update confirm`: it matched entities by the LLM's extracted name instead of the node id
the user picked, so every write silently no-opped while the returned counts still reported
success (no test exercised the `link` path at all). When testing a graph write, assert on
the **parameters actually sent** and on which query ran — `test/test_update.py`'s
`run_side_effect` recorders are the pattern — never on summary counts alone, and never
trust a count the code increments outside the branch that did the work.

### 4. Docs and code drift in both directions

The CLI's own help text is prose that can go stale — e.g. the `graph` group docstring
listed "pattern1–pattern9" long after `pattern10` existed, which actively misled readers
into thinking `pattern10` wasn't under `graph`. When adding a command, update the group
docstring, the relevant skill in `artmind/skills/`, and the `justfile` recipe together.

`just dev-cli-help` dumps the real command hierarchy — trust it over any prose.

## Command routing quick reference

Two levels under `query`, and mixing them up is a common error:

- `artmind query graph <cmd>` — `metadata`, `structural-metadata`, `entity-listing`,
  `pattern1`–`pattern10`, `text2cypher`, `conflicts`, `timeline`
- `artmind query <cmd>` — `domains-overview`, `vector-text`, `entity-resolve`,
  `chunks`, `entity-context`, `text2sql`, `resolve-key`

`text2sql`/`resolve-key` query the structured (SQL) store, not the graph — see
`artmind db <cmd>` (`list`, `schema`, `sql`, `mappings`, `catalogue`, `refresh`,
`connect`, `backup`, `restore`) for managing/reading that store directly.

## Conventions

- **Use the justfile.** It wraps every routine operation; recipes use `uv run artmind`
  (project venv) rather than the globally installed command.
- Python `>=3.14.4`, managed with `uv`. Don't hand-edit `uv.lock`.
- CLI options are `camelCase` on the command line (`--entityClass`, `--documentName`)
  mapped to `snake_case` Python params. Match the existing style.
- Commands take repeatable, comma-splittable `--domain` and support `--compact` JSON.
- Skills live in `artmind/skills/` and the opencode persona in `artmind/opencode/` —
  edit there, never in `.claude/skills/` (a symlink), `~/.artmind/` (overwritten by
  `init`), or `build/` (an artifact). Run `artmind init` to push edits to the run folder.
