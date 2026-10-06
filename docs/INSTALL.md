# Installing artmind

artmind installs as a global `artmind` command and anchors to whichever **vault**
you are standing in. Two commands take a clean Mac to a ready Obsidian vault:
`scripts/bootstrap.sh` once per machine, then `artmind vault new` once per vault.

## Quick start (macOS)

1. **Install [Homebrew](https://brew.sh).** It also installs Apple's command-line
   tools, which provide `git`.

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **Clone artmind.** The repo is public, so this needs no GitHub login.

   ```bash
   git clone https://github.com/neurongraph/artmind9.git ~/projects/artmind9
   ```

3. **Bootstrap the machine** from inside the checkout:

   ```bash
   cd ~/projects/artmind9 && bash scripts/bootstrap.sh
   ```

   Run it with `bash`, whatever your login shell is. It's written for the
   `/bin/bash` that ships with macOS. See [What `bootstrap.sh` does](#what-bootstrapsh-does).

4. **Open a new terminal**, so that `PATH` includes Homebrew and
   `~/.local/bin`. Then edit the machine config, which sets the LLM provider,
   model, API key and embeddings for every vault on this machine:

   ```bash
   $EDITOR ~/.artmind/config.env
   ```

5. **Create a vault:**

   ```bash
   artmind vault new my_vault
   ```

   On a second machine, join the same vault instead:

   ```bash
   artmind vault new my_vault --join
   ```

6. **Finish in Obsidian.** Obsidian opens at the end. Choose **Open folder as
   vault**, pick `~/artmind_vaults/my_vault`, and click **Turn on community
   plugins**. Obsidian keeps that consent to itself, so it's the one step
   artmind can't do for you.

### What `bootstrap.sh` does

It's safe to re-run: every step checks first and skips what is already done.

- `brew install uv just gh node colima docker`, plus Obsidian (`brew install
  --cask obsidian`) if it isn't in `/Applications`.
- `gh auth login` if you aren't logged in, and `gh auth setup-git`, so `git
  push` over https uses gh's credentials. It warns if `git config user.name`
  or `user.email` is unset; `vault new`'s first commit needs both.
- `colima start`. colima is the Docker runtime neo4j-manager runs Neo4j in.
- Installs **this checkout** of artmind (`just dev-install`). It clones
  [neo4j-manager](https://github.com/neurongraph/neo4j-manager) next to the
  checkout and installs it (`just install`). Run on its own, outside a
  checkout, the script clones both repos into `$ARTMIND_SRC` (default
  `~/projects`).
- Puts Homebrew (`brew shellenv` in `~/.zprofile`) and `~/.local/bin` (`uv
  tool update-shell`) on your shell's `PATH`. Both changes are skipped if
  they're already there.
- Creates `~/.artmind/config.env` from the shipped defaults, if it doesn't exist.

### What `artmind vault new` does

`NAME` is the vault's folder name, its **private** GitHub repo
`<your gh account>/NAME`, and its neo4j-manager instance name, all at once.
It never changes the name, so the three always match. In order, it:

1. creates the Neo4j instance (`neo4j-manager create NAME --json --wait`) and
   writes its bolt URL and password into the vault's own `.artmind/config.env`;
2. creates `$ARTMIND_VAULTS_DIR/NAME` (`~/artmind_vaults/NAME` by default), runs `git init`, and creates `.obsidian/`;
3. runs the same scaffold as `artmind init`, which installs and enables the
   artmind Obsidian plugin;
4. installs and enables the community plugins (Obsidian Git, Unhide, VSCode
   Editor, Ghostty Terminal) and switches on Obsidian's core Web Viewer;
5. runs `artmind setup` (graph constraints and indexes);
6. makes the first commit, creates the private GitHub repo, and pushes;
7. records the first sync bookmark (`vault sync --bootstrapSynced`).

With `--join`, it clones `<owner>/NAME` instead of steps 2 and 6, and rebuilds
this machine's graph from the commits (`vault sync --bootstrapEmpty`).

**Checks first, resumes after a failure.** Nothing is created until every
check passes:
- the GitHub repo, the folder and the Neo4j instance must not exist yet (with
  `--join`, the repo must exist);
- the tools must be on `PATH`;
- `~/.artmind/config.env` must have the credential its LLM provider needs.

Every problem is reported at once. If a step fails, fix the cause and re-run
the same command: finished steps are skipped (progress is kept in
`~/.artmind/vault_new/NAME.json`).

| Option | Use it for |
|---|---|
| `--join` | A second machine: clone the existing vault and rebuild its graph locally |
| `--dir PATH` | Put the vault somewhere other than `$ARTMIND_VAULTS_DIR/NAME` (default `~/artmind_vaults/NAME`) |
| `--githubOwner ORG` | Create the repo under an organisation instead of your gh account |
| `--localOnly` | No GitHub at all: commit locally only (throwaway and test vaults) |
| `--neo4jUri URI --neo4jUser U --neo4jPassword P [--neo4jDatabase D]` | Use an existing Neo4j, for example AuraDB, instead of creating a neo4j-manager instance |
| `--noPlugins` | Skip the Obsidian plugins |
| `--noOpen` | Don't open Obsidian at the end |
| `--yes` | Don't ask for confirmation |

### `vault new`, `init`, `setup` and `vault sync`: which one when

| Command | Job |
|---|---|
| `artmind vault new NAME [--join]` | Create a vault from nothing, or join an existing one |
| `artmind init` | Bring an existing folder or vault up to date: the `.gitignore` block, skills and Obsidian plugin. Re-run it inside each vault after upgrading artmind. It never prompts and never overwrites a schema or config you have edited |
| `artmind setup` | (Re)apply the graph's constraints and indexes, for example after pointing a vault at a different Neo4j |
| `artmind vault sync` | Replay committed changes into this machine's graph and structured store. `--bootstrapEmpty`/`--bootstrapSynced` set a store's first bookmark |

To make an existing folder a vault by hand, without neo4j-manager or GitHub:

```bash
cd ~/MyVault && artmind init
$EDITOR ~/MyVault/.artmind/config.env  # this vault's Neo4j connection
artmind setup                          # Neo4j constraints/indexes + SQLite tables (never overwrites vault schemas)
```

## The vault

A vault is a directory containing `.artmind/`. It's your Obsidian vault, your
git repo and your artmind knowledge base at once. Commands find it by walking
up from the current directory, the same way git finds `.git/`:

```bash
cd ~/Notes         && artmind query …     # this vault
cd ~/work-research && artmind admin-ui    # that vault
```

So you can work in two vaults at once from two terminals. There is no "current
vault" setting to get wrong. Outside any vault, commands that need one fail
with guidance rather than guessing.

| Inside the vault | Holds | In git |
|---|---|---|
| `.artmind/vault.yaml` | the ingest manifest: folder→domain mapping, and the `vault_id` every clone shares | yes |
| `.artmind/domains/schemas/`, `meta.yaml` | domain schemas + meta-schema | yes |
| `.artmind/domains/table_mappings/` | `ingest table2graph` mappings | yes |
| `.artmind/same_as.yaml` | same-as curation | yes |
| `.artmind/config.env` | this vault's Neo4j connection (holds a password) | no |
| `.artmind/data/documents/markdowns/` | converted markdown, extracted images | yes |
| `.artmind/data/kg/` | KG extraction staging (JSON) | yes, except `embeddings.json` |
| `.artmind/data/structured_text/` | the structured store as text (CSV + manifest) | yes |
| `.artmind/data/curation/` | conflict, supersession, retirement and synthesis records | yes |
| `.artmind/data/document_registry.db` | path↔id registry (rebuildable via `docs reindex`) | no |
| `.artmind/data/graph_snapshot/`, `structured_snapshot/` | snapshots (`*.tar.gz`) | no |
| `.artmind/logs/`, `state.json`, `serve.json`, `worker.pid` | machine-local runtime state | no |
| `.claude/skills/`, `.opencode/agent/` | artmind's skills and agent modes (symlinked) + your own | only yours |
| `.obsidian/` | Obsidian settings and community plugins | yes, except `workspace*.json` and `plugins/artmind/` |
| `_external_docs/` | copies of sources ingested from outside the vault | yes |
| `_Inbox/` | drafts; never ingested (an ordinary directory, no gitignore treatment) | yes |

The chunk embedding sidecar (`.artmind/data/kg/**/embeddings.json`) is never
committed. See `docs/vault.md`, "Embeddings".

**Machine config.** One file is global: `~/.artmind/config.env`, holding the
LLM provider, API keys and models. Secrets must not live in a vault you may
push. Config loads most-specific-first, so a vault's `config.env` overrides
the machine's, and real environment variables beat both.

**Resolution precedence.** `ARTMIND_VAULT` comes first (for cron and anything
with no meaningful working directory), then the walk up from the current
directory. A `--vault` CLI flag is planned (see `docs/vault.md`) but isn't
wired into any command yet.

## Manual install (without `bootstrap.sh`)

Prerequisites:

- Python (see `.python-version`) and [`uv`](https://docs.astral.sh/uv/).
  `uv` fetches the right Python itself.
- A **Neo4j** with vector-index support and the APOC plugin.
  [neo4j-manager](https://github.com/neurongraph/neo4j-manager) runs one in
  Docker/colima per vault, or you can use your own, or AuraDB.
- LLM/embeddings access: local **Ollama**, or an **OpenRouter** API key.
- `git`, because a vault is a git repo. Also `gh`, if `vault new` should
  create the GitHub repo.
- Node.js + npm. `just dev-install` builds the Obsidian plugin (`just
  obsidian-plugin-build`) and stages it in the package for `artmind init` to
  install. A wheel built outside the justfile ships without the plugin.

Then, from the checkout:

```bash
just dev-install
```

This is the single install path for both development and running. It's
editable, so code edits are live, and the `artmind` command runs from any
directory. It doesn't create anything: installing the CLI and creating a vault
are separate steps. It also doesn't restart the daemons it stops (see
[Daemons](#daemons)).

## Run

```bash
cd ~/artmind_vaults/my_vault
artmind query graph metadata --domain <domain> --compact
artmind serve          # warm query daemon (port 8377)
artmind chat-ui        # end-user chat UI (port 8378)
artmind admin-ui       # operator console: agent chat, ingest dashboard, CLI guide (port 8379)
```

The chat agent's working directory is the vault, so it can read your documents
and find artmind's skills at `.claude/skills/`.

### Neo4j memory

Give Neo4j a bounded heap and a transaction-memory cap. With a cap, a
transaction that is too large fails cleanly with
`MemoryPoolOutOfMemoryError`. Without one, it grows until the JVM, or the
Docker VM it runs in, is killed.

| Setting | Recommended | Docker env var |
|---|---|---|
| `server.memory.heap.max_size` (and `initial_size`) | `1G` | `NEO4J_server_memory_heap_max__size=1G` |
| `server.memory.pagecache.size` | `512M` | `NEO4J_server_memory_pagecache_size=512M` |
| `db.memory.transaction.total.max` | `512M` (must stay below the heap) | `NEO4J_db_memory_transaction_total_max=512M` |

**colima / Docker VM: at least 4 GiB** (`colima stop && colima start --memory 4`).
On 2026-10-03, a 2 GiB VM was run out of memory by one 4,332-key rebuild
transaction, and Neo4j was killed.

artmind keeps its own transactions small. A full projection rebuild commits
400 keys per transaction (`projection.REBUILD_BATCH`), and `table2graph` uses
the same batches. To see the cap and the batching work, run
`just dev-rebuild-memcheck` from the checkout. It uses a disposable Neo4j
container on its own port and never touches yours.

### Core vs the `[ingest]` extra

Document ingestion (`artmind ingest sync`/`async`/`extract-kg`) needs a heavy ML
stack: `docling` alone brings torch, CUDA and transformers, well over a GB.
That stack is an **optional extra**, so hosts that only query, serve, or run
the chat/admin UIs stay lean. artmind isn't published on PyPI, so install it
from git (or from a checkout with `uv tool install .`):

| Install | Command | Gets you |
|---|---|---|
| **Core** | `uv tool install git+https://github.com/neurongraph/artmind9` | query, `serve`, `chat-ui`, `admin-ui`, SQL querying (`db *`, `query text2sql`) |
| **Full** | `uv tool install 'artmind9[ingest] @ git+https://github.com/neurongraph/artmind9'` | core **plus** document ingestion (docling conversion, chunking, xlsx) |

`just dev-install` installs the full variant, so a dev machine has everything.
A core-only install still lists the ingest commands in `--help`, but running
one prints a hint to add the extra rather than an import error. `docling`
must also be on `PATH` for non-markdown conversion. Query-only and pure-client
hosts (e.g. the canvas backend) want core; the machine that ingests wants the
extra. Neither git install includes the Obsidian plugin, which is a build
output (see the prerequisites).

## Daemons

`artmind serve` and the background ingestion worker load code at **start**
time. One left running after a reinstall keeps serving the *old* build. A
lingering `serve` also holds its port, so the next `artmind serve` fails to
bind with `[Errno 48] address already in use`.

That's why `just dev-install` stops them first. To do it on its own:

```bash
just dev-stop-daemons
```

It finds `serve`, `chat-ui` and `admin-ui` by the ports they actually hold
(`$ARTMIND_SERVE_PORT`, default 8377; 8378; 8379), and confirms each process
is artmind before killing it, so an unrelated process on those ports is left
alone.

A running daemon can also mask whether your changes took effect at all, since
`artmind query` proxies to it. To force in-process execution:

```bash
ARTMIND_NO_PROXY=1 artmind query ...
```

## Upgrade / uninstall

```bash
cd ~/projects/artmind9 && git pull
just dev-install                 # stops daemons, re-installs artmind; touches no vault
cd ~/artmind_vaults/my_vault && artmind init   # in each vault: .gitignore block, Obsidian plugin
just dev-uninstall               # removes the `artmind` command (leaves every vault intact)
```

After upgrading:
- **Code** is live immediately, because the install is editable.
- **Skills** reach every vault through the symlinked `.claude/skills/`, with no
  per-vault step.
- **Each vault** needs `artmind init` re-run inside it, for the `.gitignore`
  block and the Obsidian plugin. Then restart `serve` / `admin-ui` from inside
  the vault, and reload Obsidian.
- **Schemas** are the exception. `artmind init` and `artmind setup` only seed
  ones that are missing, so a newer schema in the package never overwrites
  one you have edited. There's no `domains update` command yet (`docs/vault.md`
  describes it as planned). To take the packaged version of a schema you
  haven't edited, delete the vault's copy and re-run `artmind init`.
