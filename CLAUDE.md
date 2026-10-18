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
| `artmind/ingest.py` `--reextract` | `ingest sync|async --reextract [--match GLOB]` forces the `content` tier (`NotePlan.reextract`) for unchanged vault notes so they are re-extracted under the current schema; the version is not bumped. Travels as `ingestion_jobs.reextract` → `worker._process_job` → `ingest_file`; `extract_kg` then clears the file-hash-keyed chunk cache and `kg_chunk_status` rows (`_reset_extraction_cache`), else it would resume as already done. Binaries/tables ignore it. The cache is set aside under `logs/reextract-stash/`, not deleted; `extract_kg` refuses to stage (returns None, file failed) while a chunk step is still `failed` after the retry, or when a re-extraction of an unchanged note comes back with no observations over a non-empty result — then it restores the cache and rows (spec `docs/superpowers/specs/2026-10-18-extraction-loss-guard-design.md`). |
| `artmind/retime.py` | `ingest retime`: re-derives already-staged documents' valid time from the current schema (`temporal.document` labels, then `temporal.document_patterns` — filename / first line) with no LLM, also applying `property_aliases` / number-variant folding and `exclusive:` properties (`observations.normalize_property_keys`), commits changed folders, rebuilds once. `--redoProperties` re-runs only the properties step, only for entities of `temporal:`-tagged classes, with the document date in the prompt (`temporal.relative_anchor`), keeping canonical names. |
| `artmind/table2graph.py` | `ingest table2graph`: projects structured-table rows into entities/relationships via a declarative mapping in the run folder's `domains/table_mappings/*.yaml` (no LLM), committed through the same `_commit_document_tx` as a document. Mapping format: `artmind/skills/artmind-create-schema/references/table_mapping.md`. |
| `artmind/staging_audit.py` | `ingest empty-extractions`: read-only audit of staging folders, listing notes whose `observations.json` is empty although their chunk text is not. No registry, Neo4j or LLM. |
| `artmind/kg_views/` | Saved, parameterised, read-only graph queries ("views"). `model` (view.yaml contract), `render` (markdown per presentation format), `params` (entity auto-resolve), `store` (reads `<ARTMIND_HOME>/domains/views/<name>/{view.yaml,SKILL.md}` from disk on every call; never seeded by `setup`; refuses a changed view without a `version` bump and refuses a version downgrade; save is atomic), `runner` (60 s server-side query timeout). CLI: `artmind query views list/show/run` (proxied by `serve`) and `artmind views validate/test/save/delete` (`views test` reports zero rows, truncation and ambiguous/no-match entities in a JSON `warnings` list). Format reference: `artmind/skills/artmind-create-view/references/view_format.md`. |
| `artmind/insights/` | Unprompted findings over a domain's graph (spec `docs/superpowers/specs/2026-10-17-insights-layer-design.md`). `projections` (named read-only `$domains`-scoped Cypher, memoised), `profile` (shape profile from content-dated documents only, effective kind, anchors), `detectors/` (salience/type trends, dormancy, communities, brokers, outcome_lift (FDR-controlled), transitions, open_loops, same_as_candidates + split_signal, graph health incl. attribution_suspects/fragments; pure Python + networkx, no numpy; `aliases.yaml` kinship names), `config_check`, `prompts` + `narrate` (LLM captions and circle names after the diff, post-checked, cached by content hash; any LLM failure leaves template titles and is retried by the next refresh; prompts send entity names and stats to the configured provider and log them in `llm_calls.log`, `--no-narrate` keeps them out), `diff`, `store` (snapshots at `paths.INSIGHTS_DIR`, `<vault>/.artmind/insights/<domain>/`, committed; atomic writes via `logs/`), `runner`. Tuning: `domains/insights/<domain>.yaml`, never seeded, outside `schemas/` so it never moves `schema_set_hash()`. **Snapshots hold private corpus data** — never copy them into code, tests or commits. CLI: `artmind insights refresh/list/show/profile/runs/same-as/config check` (not proxied; `refresh` short-circuits when graph, schemas, curation and config are unchanged; `same-as --export` writes `ingest refine-graph --from-file` input for the same-as review queue; `refresh --no-narrate` skips the LLM). |
| `artmind/traces/` | Query traces (spec `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`). Every admin-console turn and benchmark question → `trace_turns`/`trace_events` in the registry DB. Every `artmind query` call made under a trace → a `trace_spans` row (via `TracedGroup` on the `query` group; evidence read from the payload `_echo_json` prints) plus its `trace_llm_calls`. The trace id reaches the agent's Bash through a per-session file named by `ARTMIND_TRACE_CONTEXT`, which `_entry` forwards to `serve`. `path` derives graph→vector fall-through, answer basis and the views-first check (`views_check`; `traces stats` rates and `views_skipped`/`views_late` triage signals). CLI: `artmind traces list/show/triage/feedback/stats/prune` (not proxied). |
| `artmind/conversations/` | Chat history for chat-ui and admin-ui (spec `docs/superpowers/specs/2026-10-16-chat-conversations-design.md`). `store` (the `conversations` / `conversation_turns` tables in the registry DB: ensure/get/list/update/delete, begin/finish/list turns, profile-scoped startup sweep), `recorder` (`ConversationRecorder`: folds a turn's neutral UI events into ordered `blocks` and writes the turn; wraps `/api/chat`), `replay` (`build_context`: user questions + final answers within `ARTMIND_REPLAY_BUDGET_CHARS`, default 24000, prepended to the prompt whenever `SessionRegistry.get_or_create` reports a fresh agent; a turn that did not reach `turn_done` drops the live agent so the next turn replays). The URL `/?c=<id>` is the UI state; `static/conversations.js` is the sidebar. Conversations hold private corpus content: local DB only, and tests use synthetic text. A running `chat-ui` / `admin-ui` serves stale code, so restart it after editing. |
| `artmind/reviews/` | Review dossiers (spec `docs/superpowers/specs/2026-10-11-query-review-loop-design.md` §7-§8): one markdown file per diagnosed query failure under `paths.REVIEWS_DIR` (`<vault>/.artmind/reviews/`, committed with the vault's private repo; never ingested). `dossier` (format, atomic save — temp files go to `logs/`, never the committed folder), `validate`, `scaffold` (from a trace), `index` (`trace_reviews` is a rebuildable (review, trace) index), `leakcheck` (scans a checkout diff for a dossier's `private_terms` and quoted text), `shape` (shape-audit bundle: sentences, graph subgraph via AGGREGATES/EXTRACTED_FROM + one RELATES_TO hop, staging, schema), `reextract` (dry-run re-extraction of evidence chunks under a proposed schema/meta — shape diff + answerability, writes nothing), `answerability` (pattern matcher), `aggregate` (findings by schema element), `sample` (random chunks with lost-at-commit / isolated signals), `verify` (regressions file; re-run a regression question as a one-question benchmark and compare traces — never auto-verifies). Driven by the `artmind-review` skill from Claude Code in the checkout with `ARTMIND_VAULT=<vault>`. **Dossiers hold private corpus data; the checkout is public** — never copy dossier content into code, tests or commits. CLI: `artmind review new/list/show/status/validate/reindex/leakcheck/shape/reextract/shape-report/attach/regressions/verify/prompt` (not proxied). |
| `artmind/refine_pipeline.py`, `refine_graph.py`, `conflicts.py`, `consolidate.py`, `temporal.py`, `harmonizer.py` | Graph maintenance: merging, conflict detection, temporal normalization. |
| `artmind/skills/` | **Source of truth for agent skills.** Shipped in the wheel and seeded into the run folder. |
| `artmind/domains/schemas/` | Default domain schemas (YAML), also seeded. |
| `artmind/webui/` | Chat UI (`index.html`), admin console (`admin.html` + Lane A agent chat + Lane B `dashboard.html`/`dashboard_routes.py`; its Insights and read-only Views tabs are `insights_routes.py` + `static/insights.js`/`views.js`, rendering snapshot text via textContent only; the console pre-fills its input from `/?ask=`), and the generated help/concept catalogue (`help.py`). `hosts.py` is the Host-header allow-list (DNS-rebinding guard): `run_chat_ui`/`run_admin_ui` apply it on a loopback `--host` (400 unless Host is 127.0.0.1/localhost/[::1]); a non-loopback bind allows all hosts and warns that the unauthenticated UIs are open. `create_app()` itself is unguarded so `TestClient` (Host `testserver`) works. |
| `artmind/cli_guide.py` | Renders the CLI as an HTML **fragment** for the admin-ui's "CLI guide" tab (`GET /api/cli-guide`), styled by `dashboard.css`. Ordering comes from `COMMAND_GROUPS` in `cli.py`; `test/test_cli_guide.py` fails if a command isn't routed there. There is no checked-in HTML copy. |
| `artmind/schema_reference.py` | Parses `domains/schemas/*_schema.yaml` prompts and renders an HTML **fragment** for the admin-ui's "Schemas" tab (`GET /api/schema-reference?prefix=`), reading the run folder's live schemas on every request. Grouped by domain family — the part of a filename before its first `.`. No checked-in copy. |
| `artmind/server.py` | The warm `serve` daemon. |
| `artmind/opencode/` | opencode/ACP persona, seeded into the run folder. |
| `obsidian/artmind-obsidian/` | The Obsidian plugin (TypeScript, esbuild, vitest). It only runs the `artmind` CLI with `--compact`; `test/fixtures/*.json` are that JSON, regenerated from the real CLI by `test/test_plugin_fixtures.py` (`ARTMIND_REGEN_PLUGIN_FIXTURES=1`). A CLI output change fails that test first: regenerate, then run `just obsidian-plugin-test`. `artmind init` installs it into a vault (copies the build staged in `artmind/obsidian_plugin/` -- a gitignored build output written by `just obsidian-plugin-build`, which `just dev-install` runs -- and enables it); `just obsidian-plugin-install <vault>` is the quick loop while iterating on the plugin. Spec: `docs/superpowers/specs/2026-09-30-obsidian-plugin-design.md`. |
| `artmind/setup.py` | `scaffold_run_folder()` (called by `setup`, via `setup_all()`), `scaffold_vault()` / `_symlink_skills()` (the `init` command; refuses the source checkout, `is_artmind_source()`), and Neo4j constraint/index setup. |
| `paths.py` | **Root-level module** (not inside the package). Resolves `ARTMIND_HOME` / `ARTMIND_DATA_DIR` and loads `.env`. Packaged via `py-modules`. |
| `utils/` | Shared helpers; packaged alongside `artmind`. |
| `test/` | The test suite — **singular**. |
| `docs/INSTALL.md` | Authoritative install/runtime reference. Read it before changing packaging. |
| `justfile` | Task runner; the entry point for every routine operation. |

Not source, do not edit: `build/`, `artmind9.egg-info/`, `__pycache__/`,
`obsidian/artmind-obsidian/node_modules/` and its build output `main.js`.
`tests/` (plural) is a **vestigial directory containing only stale `__pycache__`** —
the real suite is `test/`. Add tests to `test/`.

## Installed, not run from the checkout

This is the part that most often breaks assumptions. `artmind` is a **globally
installed command**, not something you invoke from the source tree.

```
just dev-install
```

expands to: stop daemons → `uv tool install --force --editable '.[ingest]'`. It does
**not** run `artmind init` and does **not** restart the daemons it stopped: run `init`
yourself where it's needed (see §2 below), and start `serve` again from inside the vault.

Two roots, both **decoupled from this checkout** (see `paths.py`, `docs/INSTALL.md`),
and both chosen by **the directory a command runs in**: `paths.py` walks up from the
cwd for `.artmind/vault.yaml` (or takes `ARTMIND_VAULT`).

- **Run folder** (`ARTMIND_HOME`) — inside a vault, the vault's own `<vault>/.artmind/`;
  outside any vault, `~/.artmind`. Holds `config.env`, `domains/schemas/`, `logs/`, and
  (outside a vault only) `.claude/skills/` and `.opencode/`. Machine-wide secrets always
  come from `~/.artmind/config.env`. A real `ARTMIND_HOME` env var overrides discovery.
- **Data dir** (`ARTMIND_DATA_DIR`) — inside a vault, `<vault>/.artmind/data/`; outside,
  default `~/artmind_data`. Ingestion artifacts only (originals, markdowns, registry DB,
  jobs, KG staging, snapshots). Query-only hosts never touch it.

The install is **editable**, so Python code edits are live everywhere immediately.
The mental model that matters: *editable covers the code, but not the two caches
in front of it* — long-running daemons, and run-folder copies.

**Core vs `[ingest]` extra.** The heavy ingestion ML stack (`docling` →
torch/CUDA/transformers, plus `langchain-text-splitters` and `openpyxl`) lives
behind an optional extra so query-only/pure-client consumers
stay lean. `just dev-install` installs `.[ingest]` — the dev box is full-featured.
A core-only install (`uv tool install artmind9`) omits document ingestion:
`import artmind.cli` still loads (the three libs are imported lazily — langchain
inside `ingest._split_markdown`, openpyxl inside `duckdb_adapter._excel_sheet_to_csv`),
and the chunking commands (`ingest sync`/`async`/`extract-kg`) raise a friendly
`click.ClickException` via `_require_ingest_extra()` naming the fix. The dev
dependency-group mirrors the extra so the hermetic suite still exercises the
chunking/xlsx paths. See `docs/INSTALL.md`.

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

Trace capture has the same trap: a `serve` daemon started before `artmind/traces`
shipped answers proxied queries without recording spans. `artmind traces show`
warns when a turn ran `artmind query` but has no spans.

### 2. Skills reach the chat UI through the vault or the run folder

`artmind/skills/` is the **single source of truth**. It reaches consumers three ways,
and an **edit** to an existing skill and a **new** skill travel differently:

| Where | What is there | Edit to an existing skill | New (or removed) skill |
|---|---|---|---|
| Checkout `.claude/skills/`, `.pi/skills/` | symlinks into `artmind/skills/` (gitignored; a fresh clone lacks them) | live | `just dev-refresh-skills` |
| Vault `<vault>/.claude/skills/` | symlinks written by `artmind init` (`_symlink_skills()`); with the editable install they point straight at this checkout's `artmind/skills/<name>` | live | `artmind init`, run **inside the vault** |
| Run folder `~/.artmind/.claude/skills/` | **copies** written by `artmind setup` (`scaffold_run_folder()`) | `artmind setup`, run **from `~`** | `artmind setup` adds it; a removed skill's copy is never pruned (delete it by hand) |

Inside a vault the chat agent reads the vault's symlinks. The two backends start it in
different places: ACP at `ARTMIND_AGENT_CWD` (the vault root), the Claude SDK at
`RUN_FOLDER` = `ARTMIND_HOME` (`<vault>/.artmind`, `artmind/webui/agent.py`), and the
SDK agent still loads the vault-root skills from there (traces show `Skill:artmind-query`).
Only a chat UI started **outside** any vault reads the `~/.artmind` copies. A vault has
no `.artmind/.claude/skills/`, and `setup` deliberately writes none there.

**Never run `artmind setup` or `artmind init` from this checkout.** `init` refuses it
(`is_artmind_source()`); for `setup`, run it from `~` when you mean to refresh
`~/.artmind`. Inside a vault `setup` targets that vault's `.artmind/` instead and does
**not** refresh any skills. `just cli-setup` (runs from `~`) and `just cli-init <vault>`
(runs inside the vault) wrap the first two commands below.

```bash
cd ~ && artmind setup                    # refresh ~/.artmind copies (skills, .opencode/, schemas)
cd <vault> && artmind init               # link skills added since the vault was made
just dev-refresh-skills                  # link new skills into the checkout's .claude/ and .pi/
```

`just dev-install` runs none of these.

**Schemas follow the same split.** Outside a vault, `setup` **overwrites**
`~/.artmind/domains/schemas/` (and `.opencode/`) from the package on every run —
deliberately, since schemas carry the extraction prompts — while `config.env` is
seeded only when absent. A vault's schemas are the user's committed files: `init` and
`setup` seed them only when absent, so a packaged schema fix reaches a vault only via
`artmind init --force [--schema NAME]` inside it (refused unless git can restore every
file it would change). Keep schema edits in `artmind/domains/schemas/`.

Do not edit `~/.artmind/.claude/skills/` — `setup` overwrites it. Editing a skill and
testing only in the checkout exercises the vault path too (both are symlinks), but not a
run-folder chat UI: if they disagree, the run folder wasn't re-seeded.

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
  `pattern1`–`pattern10`, `text2cypher`, `conflicts`, `timeline`, `hierarchy`
- `artmind query <cmd>` — `domains-overview`, `vector-text`, `entity-resolve`,
  `chunks`, `entity-context`, `text2sql`, `resolve-key`, `views list|show|run`, `insights list|show`
  (reads the latest insights snapshot only; guided by artmind-query's Insights section; operators use the `artmind-insights` skill)
- `artmind views <cmd>` — authoring: `validate`, `test`, `save`, `delete` (not proxied)
- `artmind insights <cmd>` — `refresh` (needs Neo4j), `list`, `show`, `profile`, `runs`, `same-as [--export]`, `config check` (not proxied)
- `artmind traces <cmd>` — recorded query traces: `list`, `show`, `triage`, `feedback`, `stats`, `prune` (not proxied)
- `artmind review <cmd>` — review dossiers: `new`, `list`, `show`, `status`, `validate`, `reindex`, `leakcheck`, `shape`, `reextract`, `shape-report`, `attach`, `regressions`, `verify`, `prompt` (not proxied)

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
  `setup`), or `build/` (an artifact). How an edit or a new skill reaches the checkout,
  vaults and `~/.artmind` is in Testing implications §2.
