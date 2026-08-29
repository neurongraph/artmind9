# Workspaces

**Status: partially implemented** (branch `feat/workspaces`). Resolution, the
registry, guardrails 1, 2 and 4, and `artmind workspace` / `list` / `use` /
`env` have landed. Still to build: `workspace create` and `adopt`, the schema
seeding split (guardrail 3), the vault ignore rules (guardrail 6), the snapshot
default split, and the topbar chip. The specification for switching artmind
between independent knowledge bases. Stores in
[stores-and-repos.md](./stores-and-repos.md); identity in
[document-identity.md](./document-identity.md); vocabulary in
[CONTEXT.md](../CONTEXT.md).

## The problem

artmind already supports two knowledge bases. `ARTMIND_HOME` selects a run folder,
and every other root — data dir, vault, archive, graph — is named in that run
folder's `.env`. So `ARTMIND_HOME=~/.artmind/personal artmind …` is, in principle,
a complete context switch in one variable.

In practice nobody can use it safely, because **one `.env` file mixes three
different lifetimes**:

| Lifetime | What it holds | Changes | Same across knowledge bases? |
|---|---|---|---|
| **Identity** | LLM provider, API keys, embedding model, agent model | rarely | **always** |
| **Workspace** | vault, data dir, archive, graph connection | per knowledge base | never — this *is* the distinction |
| **Runtime** | ports, `ARTMIND_NO_PROXY`, log level | per invocation | never |

Creating a second knowledge base therefore means copying all three. Rotating one
API key means editing every copy. And every new run folder starts life as a clone
of an existing one, which is how the failure below happens.

Splitting config by lifetime is the design. Once identity is shared and runtime is
derived, the only thing left to name per knowledge base is small enough to put in a
registry — and switching becomes a lookup rather than a file-copying ritual.

## The workspace

**Workspace**: one knowledge base and everything scoped to it — its vault, its
derived data, its archive, its graph, its curation, its logs. The unit a user
names, switches to, and reasons about. Exactly one is active per process.
_Avoid_: context, profile, project, instance.

A workspace is **not** a filter over shared state. Two workspaces share no
documents, no observations, and no entities. They share only identity config and
the artmind installation itself.

## Layout

```
~/.artmind/
├── config.env              identity — provider, keys, embedding + agent models
├── workspaces.yaml         the registry
├── current                 pointer file: the active workspace name
└── workspaces/
    ├── banking/            ← a run folder; ARTMIND_HOME points here
    │   ├── .env              workspace-local overrides only, usually empty
    │   ├── same_as.yaml
    │   ├── domains/schemas/
    │   ├── .claude/skills/
    │   ├── .opencode/
    │   └── logs/
    └── personal/
```

The run folder's internals are **unchanged**. `ARTMIND_HOME` still means what it
means today; workspaces add a layer above it, they do not restructure it.

### Config precedence

`config.env` is loaded first, then the workspace's own `.env` on top. A workspace
`.env` exists to override — pinning a cheaper extraction model for a low-value
vault, say — and is empty for most workspaces. Real environment variables continue
to beat both.

### The registry

```yaml
version: 1
default: personal
workspaces:
  banking:
    vaults:
      - name: corpus
        path: ~/Projects/artmind-corpus
    data_dir: ~/artmind_data
    archive_dir: ~/artmind_archive
    graph:
      uri: neo4j+s://e94695dd.databases.neo4j.io
      database: e94695dd
    ports: {serve: 8377, chat_ui: 8378, admin_ui: 8379}
    schemas: [banking, banking.*]
    frozen: true
```

`vaults` is **a list from day one**, though the implementation accepts exactly one
entry and errors on more. See "Toward many vaults" for why this shape is fixed now.

`frozen: true` marks a workspace as preserved rather than live. Ingest,
`projection rebuild`, and `snapshot restore` refuse to run against it without
`--thaw`. This is what "keep the banking corpus for later work" should mean
operationally, rather than a note in someone's head.

## Resolution

Highest wins:

1. `--workspace <name>` on the command line
2. `ARTMIND_WORKSPACE=<name>`
3. `~/.artmind/current`
4. `default:` in the registry

`ARTMIND_HOME` remains supported as a raw escape hatch that bypasses the registry
entirely — it is what CLAUDE.md documents and what the test suite uses. When set,
it wins over all four and the CLI reports the workspace as `(unregistered)` rather
than guessing a name.

The resolution happens in `paths.py`, before `.env` is loaded, for the same reason
`ARTMIND_HOME` does today: it is how we *find* the config.

## Command surface

Modelled on `kubectl config` — the same problem shape (one CLI, several isolated
backends, expensive to act on the wrong one) and vocabulary users already have.

| Command | Does |
|---|---|
| `artmind workspace` | Current workspace: resolved paths, **the `.env` files actually loaded**, graph URI + database, vault git HEAD + dirty state, daemon status |
| `artmind workspace list` | All registered, active one marked, frozen ones flagged |
| `artmind workspace use <name>` | Writes `~/.artmind/current`. Warns if a daemon or ingest worker is live in the outgoing workspace |
| `artmind workspace create <name>` | Guided; see below |
| `artmind workspace env <name>` | Emits `export …` lines for `eval "$(…)"`, for per-shell switching with no global state |
| `artmind workspace prompt` | One-line summary for `PS1` |
| `artmind --workspace <name> <cmd>` | One-off, no state change |

`artmind workspace create` is a guided flow, not a flag soup, because the decisions
are real: vault path (must exist; offer `git init` if it is not a repo), graph
target, which schemas to seed, port assignment. It ends by **querying the target
graph and showing the result** — an empty `domains-overview` is the proof that the
new workspace is not silently pointed at an existing one.

## Ambient awareness

Knowing which workspace you are in must never require asking. Four touchpoints:

- **`artmind workspace`** — the deliberate check.
- **Destructive commands name it.** `snapshot restore` today confirms with "delete
  all data in Neo4j database 'e94695dd'". It becomes "workspace **banking**
  (frozen) → Neo4j 'e94695dd'". Same for `ingest`, `projection rebuild`,
  `docs archive`.
- **A workspace chip in the topbar** of all three web surfaces. `index.html`,
  `admin.html` and `dashboard.html` share one `topbar` → `brand` → `brand-badge`
  markup, so this is one component in three places. Colour-derived-from-name so it
  reads pre-attentively.
- **`artmind workspace prompt`** for shell prompts.

## Guardrails

Four defects make workspace switching unsafe today. Each has a fix, and the first
two are prerequisites for the rest of this design being worth anything.

### 1. The `.env` fallback silently reuses another workspace's config

`paths.py` tries `$ARTMIND_HOME/.env`, then falls back to the **checkout's own
`.env`**. Pointing `ARTMIND_HOME` at a new folder before writing its `.env` loads
whatever the checkout holds — in the current install, a byte-identical copy of the
banking config, Aura credentials included. The new workspace then writes into the
old workspace's graph and data dir, and everything appears to work.

**Fix:** remove the `_SELF_DIR / ".env"` candidate, or gate it behind an explicit
`ARTMIND_ALLOW_REPO_ENV=1`. Record which files were actually loaded so
`artmind workspace` can report them.

### 2. The `serve` daemon is workspace-blind

`_entry.py:22` resolves the daemon port from `ARTMIND_SERVE_PORT` alone, and
`/health` (`server.py:49`) returns only `{service, status, version}`. A daemon
started in one workspace will answer `query` calls issued from another. CLAUDE.md
already warns that a running daemon serves stale *code*; serving the wrong
*workspace* is worse, because the answers are confidently about a different brain.

**Fix:** `/health` returns a workspace fingerprint — `sha256(run_folder ||
neo4j_database)`. `_entry._daemon_alive` computes the caller's own and compares.
On mismatch, fall through to in-process rather than proxying. `_entry.py` is
stdlib-only by design, and hashing two strings respects that. Ports default per
workspace from the registry so the collision is rare in the first place.

### 3. `init` seeds every packaged schema into every workspace

`setup.py:114` seeds schemas with `overwrite=True`. A personal-journal workspace
receives all eight `banking.*` schemas. `_get_available_domains` (`cli.py:123`)
globs that directory, so `domains-overview` and the chat agent's domain routing
offer domains that hold no data. `artmind domains delete` works and the next
`artmind init` undoes it.

**Fix:** split packaged schemas into *starter* (`general`, `personal_journal`, …)
and *example* (`banking.*`). `workspace create --schemas` chooses what to seed;
examples are opted into with `artmind domains add`. **Keep `overwrite=True` for
schemas a run folder already has** — that behaviour is deliberate and load-bearing
(a prompt fix that never reached the run folder looks like a model failure). Only
which schemas get *created* changes.

### 4. Two workspaces can claim one vault

`canonical_path` (`document_identity.py:77`) keys the registry on vault-relative
paths. Two workspaces sharing a vault would write conflicting identity rows against
the same keys.

**Fix:** `workspace create` and `workspace use` refuse a vault path already claimed
by another registry entry.

### 5. `ingest sync` writes frontmatter long before it commits

`ingest_file` writes frontmatter into the vault file per document and returns
`touched_path` (`ingest.py:592`), which the CLI accumulates (`cli.py:624`). But
`commit_paths` is called **once, after the whole batch finishes** (`cli.py:659`) —
past every chunk split and every LLM extraction call. Any interruption in between
(Ctrl-C, a crash, a provider timeout cascading) leaves every file in that batch
written-but-uncommitted.

This is not hypothetical: the ten uncommitted policy files in
`~/Projects/artmind-corpus` are exactly this, from an interrupted 2026-08-28 run.
The async worker does not have the bug — it commits per file
(`worker.py:132`).

**Fix:** commit per document, as the worker already does. The batch commit buys
tidy history and pays for it in durability, which is the wrong trade. Squash into
a batch message afterwards if the history matters.

This is a **defect, not a design question**, and it is a prerequisite for the
snapshot rule below: refusing to snapshot a dirty vault while artmind is the thing
dirtying it would block the user on our own bug.

### 6. Whole-vault ingest attempts every file type

`collect_ingest_files` (`ingest.py:70`) skips only dot-prefixed paths, and
`ingest_file` (`ingest.py:481`) routes every non-`.md` to docling as a subprocess
(`ingest.py:648`). Pointed at an Obsidian vault, that means `.png` attachments run
through image description at full LLM cost, and `.canvas` / `.excalidraw` files —
which are JSON — are handed to a document converter that cannot read them.

**Fix: three layers, each doing a different job.**

| Layer | Mechanism | Job |
|---|---|---|
| Supported-type allowlist | `.md`; `.pdf/.docx/.pptx` via docling; `.csv/.xlsx` as structured | Safety net. Unknown types are **skipped and reported**, never attempted. |
| `.artmindignore` | gitignore syntax, read from the vault root, applied in `collect_ingest_files` | User intent — `Templates/`, `Daily/` before you are ready for it. |
| Built-in walk exclusions | `_derived/`, `_meta/` | artmind's own output. |

The walk exclusion needs the reason stated, because it is not "these are invalid":
`_derived/<domain>/<stem>.md` files are *genuine vault-native documents* with their
own `_artmind_id`, editable and committed like any other
([document-identity.md](./document-identity.md), "Derived-markdown promotion"). They
are excluded from directory walks because they are already reachable through their
binary source, so walking them re-extracts identical content at full cost. Naming
one explicitly still ingests it.

An embedded attachment is context *for a note*, not a document. Ingesting one mints
a junk `:Document` with no useful observations. Skipping by default is the correct
behaviour, not a limitation.

## Toward many vaults

Model A — one workspace, one vault, one graph — is what the primitives give today
and what this design implements. It cannot answer a question spanning two vaults,
and each workspace costs a Neo4j instance.

Model B — several vaults in one workspace, sharing a graph, separated by domain
namespace — is the better second-brain shape. Much of it already exists: domains
are namespaced (`banking.policy`), queries take `--domain`, and KG staging is
partitioned at `kg/<domain>/<doc>/`. Two things genuinely block it:

| Blocker | Why | Shape of the fix |
|---|---|---|
| Snapshot is whole-database | `export_graph` (`graph_snapshot.py:143`) exports every node with no filter; `import_graph` wipes with `MATCH (n) … DETACH DELETE n`. There is no per-vault backup. | Nodes carry indexed `domain` / `_domain`. Scope the export by domain set; make import "delete these domains, then load". |
| One vault root is assumed | `canonical_path` resolves relative to a single `ARTMIND_VAULT_DIR`. | Registry keys become `<vault-name>/<relative-path>`. |

Neither is required now. Both are **additive** — which is the whole reason
`vaults` is a list in the registry from the first commit. Model B then arrives as
"accept more than one entry", not as a redefinition of what a workspace is.

## Config classification

Which of the three lifetimes each existing variable belongs to. This is the table
`workspace adopt` implements.

| Lifetime | Variables |
|---|---|
| **Identity** — `config.env` | `ARTMIND_USER`, `ARTMIND_KG_LLM_PROVIDER`, `ARTMIND_KG_LLM_MODEL`, `ARTMIND_IMAGE_MODEL`, `ARTMIND_KG_LLM_URL`, `ARTMIND_OPENROUTER_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ARTMIND_OLLAMA_TIMEOUT`, `ARTMIND_KG_EMBEDDINGS_*`, `ARTMIND_SDK_*`, `ARTMIND_ACP_MODEL` |
| **Workspace** — registry | `ARTMIND_DATA_DIR`, `ARTMIND_VAULT_DIR`, `ARTMIND_ARCHIVE_DIR`, `ARTMIND_KG_NEO4J_*`, `ARTMIND_VAULT_GIT_PUSH` |
| **Runtime** — derived or per-invocation | `ARTMIND_SERVE_PORT`, `ARTMIND_NO_PROXY`, `ARTMIND_INGEST_MAX_WORKERS` |

Two resist clean classification and need explicit handling:

- **`ARTMIND_KG_EMBEDDING_DIMENSIONS`** reads like identity — it follows from the
  embedding model — but it is **baked into the Neo4j vector indexes at
  `artmind setup`** (`setup.py:360`, `:366`, `:372`). A shared `config.env` saying
  768 against a workspace whose graph was built at 1024 gives silently wrong vector
  search, not an error. It lives in `config.env`, and `workspace use` must read the
  target graph's actual index dimension and **refuse on mismatch**.
- **`ARTMIND_KG_CHUNK_SIZE`** changes extraction shape; a journal wants smaller
  chunks than a forty-page policy PDF. Identity by default, workspace-overridable.

## Snapshot defaults

`create` and `restore` currently share one component set (`DEFAULT_COMPONENTS`,
`unified_snapshot.py:148`), and on restore an unspecified set means *everything in
the zip* (`unified_snapshot.py:368`). But the two verbs have **opposite risk
profiles**, which is why one shared default cannot be right for both:

- Omitting `curation` on **create** risks losing `same_as.yaml` — human merge
  adjudication that costs real review time and is rebuildable only by redoing it.
- Including `curation` on **restore** *overwrites the live* `same_as.yaml` and every
  domain schema with the snapshot's.

So split them:

| | Default set |
|---|---|
| `DEFAULT_CREATE_COMPONENTS` | `graph`, `structured`, `kg_staging`, `originals`, **`curation`** |
| `DEFAULT_RESTORE_COMPONENTS` | `graph`, `structured`, `kg_staging`, `originals` |

Restoring curation becomes explicit (`--with-curation`). This also explains the
original exclusion — the comment at `unified_snapshot.py:145` reasons about not
shipping curation in a baseline config, which is a restore-safety instinct applied
to the wrong verb. Curation is cheap to capture: 53 KB in the 2026-08-29 snapshot.

**Dirty vaults.** `_create_manifest` (`unified_snapshot.py:120`) records
`vault_dirty` and nothing acts on it. Once guardrail 5 is fixed:

- **Warn by default, do not refuse.** A dirty vault at snapshot time is sometimes
  legitimate — taking a graph backup before an experiment, mid-edit. Refusing by
  default makes the safe action annoying, which trains people to pass
  `--allow-dirty` reflexively and defeats the check.
- **Refuse for `frozen: true` workspaces.** Freezing is precisely the moment the
  vault must be consistent. Strictness attaches to workspace state, not to a global
  flag.
- **Record `vault_dirty_paths`, not just the boolean.** `vault_dirty: true` is
  unactionable a year later; the path list tells a restorer exactly which documents'
  frontmatter is unaccounted for.

**Cross-workspace restore.** The manifest gains a workspace fingerprint, and restore
refuses a mismatch without `--force`. Each workspace has its own `same_as.yaml`, so
restoring a banking snapshot into a personal workspace would otherwise import
banking's merge decisions.

## Adopting the current install

`artmind workspace adopt <name>` migrates an existing single-workspace install.

**Non-destructive by construction.** It **copies** `~/.artmind/` to
`~/.artmind/workspaces/<name>/`, splits the keys per the classification table above,
writes `config.env` and the registry entry, then runs a verification query and
prints what it found. Only after the user confirms does it tell them to remove the
old layout. It never moves a live run folder — a half-migrated run folder is
indistinguishable from a corrupt one.

Secrets move to `config.env` with `0600`; the workspace `.env` it writes contains
only genuine overrides, which for a first adopt is usually nothing at all.

## What this does not change

- Run folder internals. `same_as.yaml`, `domains/schemas/`, `.claude/skills/`,
  `.opencode/`, `logs/` keep their names and meanings.
- `ARTMIND_HOME` semantics, including "resolved before `.env`, so only a real
  environment variable overrides it".
- The always-overwrite rule for package assets already present in a run folder.
- Any store's authority. [stores-and-repos.md](./stores-and-repos.md)'s
  authoritative/derived column is untouched — workspaces partition those stores,
  they do not reclassify them.

## Deferred

Decided out of scope for the first implementation, recorded so they are not
rediscovered as bugs:

- **Domain-scoped snapshot** — the Model B blocker. Not needed while one workspace
  means one graph.
- **Multi-vault registry keys** — the other Model B blocker. `vaults` is already a
  list; the resolver rejects a second entry until this lands.
- **Per-workspace identity overrides** beyond `.env` — no case for a workspace
  needing a different API *key*, only a different model, which `.env` already covers.
- **Attachment ingestion as first-class documents** — deliberately skipped, per
  guardrail 6. Revisit only if a real use case appears for an image as a standalone
  document rather than as context for its note.
