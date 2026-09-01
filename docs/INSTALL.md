# Installing artmind

artmind installs as a global `artmind` command and anchors to whichever **vault**
you are standing in.

## The vault

A vault is a directory containing `.artmind/`. It is your Obsidian vault, your
git repo and your artmind knowledge base at once. Commands walk up from the
current directory to find it, exactly as git walks up for `.git/`:

```bash
cd ~/Notes         && artmind query …     # this vault
cd ~/work-research && artmind admin-ui    # that vault
```

Two terminals, two vaults, at once. There is no "current vault" setting to get
wrong. Outside any vault, commands that need one fail with guidance rather than
guessing.

| Inside the vault | Holds | In git |
|---|---|---|
| `.artmind/vault.yaml` | the ingest manifest: folder→domain mapping | yes |
| `.artmind/domains/` | schemas + meta-schema | yes |
| `.artmind/same_as.yaml` | curation | yes |
| `.artmind/config.env` | this vault's Neo4j connection | no |
| `.artmind/data/markdowns/` | converted markdown, extracted images, chunks | yes |
| `.artmind/data/kg/` | KG extraction staging (JSON) | yes |
| `.artmind/data/kg/**/embeddings.json` | chunk embedding sidecar | no |
| `.artmind/data/document_registry.db` | path↔id registry (rebuildable via `docs reindex`) | no |
| `.artmind/data/*.zip`, `*.tgz`, `*.tar.gz` | snapshots | no |
| `.artmind/logs/`, `state.json`, `serve.json`, `worker.pid` | machine-local runtime state | no |
| `.claude/skills/` | artmind's (symlinked) + your own | only yours |
| `_external_docs/` | copies of sources ingested from outside the vault | yes |
| `_Inbox/` | drafts; never ingested (no gitignore treatment — an ordinary directory) | yes |

One file stays global: `~/.artmind/config.env`, holding the LLM provider, API
keys and models. Secrets must not live in a vault you may push. Config loads
most-specific-first, so a vault's `config.env` overrides the machine's, and real
environment variables beat both.

Resolution precedence: `ARTMIND_VAULT` (for cron and anything with no
meaningful cwd), then the walk up from the current directory. A `--vault` CLI
flag is planned (see `docs/vault.md`) but not yet wired into any command — for
now, `ARTMIND_VAULT` is the only way to point at a vault other than the one
`cwd` is inside.

## Prerequisites

- Python (see `.python-version`) and [`uv`](https://docs.astral.sh/uv/).
- A running **Neo4j** with vector-index support.
- LLM/embeddings access: local **Ollama**, or an **OpenRouter** API key.
- `git` — a vault is a git repo.

## Install

```bash
just dev-install
```

This is the single install path for both development and running. It is
editable, so code edits are live, and the `artmind` command runs from any
directory. It does **not** create anything: installing the CLI and creating a
vault are separate acts.

## Create a vault

```bash
mkdir ~/MyVault && cd ~/MyVault
artmind init
```

`artmind init` runs `git init` if needed, creates `.artmind/`, writes a
`.gitignore` that commits derived output (converted markdown, chunks, KG
staging) by default and excludes only a short list — secrets, churning
binaries, and machine-local state (see the table above) — seeds the starter
domain schemas, symlinks artmind's skills into `.claude/skills/`, and writes a
starter `vault.yaml`. It is idempotent and needs no Neo4j, and it **never
overwrites** a schema or config file you have edited.

Then:

```bash
$EDITOR ~/.artmind/config.env          # provider, API keys, models (machine-wide)
$EDITOR ~/MyVault/.artmind/config.env  # this vault's Neo4j connection
artmind setup                          # Neo4j constraints/indexes + SQLite tables
```

## Run

```bash
cd ~/MyVault
artmind query graph metadata --domain <domain> --compact
artmind serve
artmind admin-ui
```

The chat agent's working directory is the vault, so it can read your documents
and finds artmind's skills at `.claude/skills/`.

### Core vs the `[ingest]` extra

Document ingestion (`artmind ingest sync`/`async`/`extract-kg`) pulls a heavy ML
stack — `docling` alone brings torch + CUDA + transformers (well over a GB).
That stack lives behind an **optional extra** so hosts that only query, serve, or
run the chat/admin UIs stay lean:

| Install | Command | Gets you |
|---|---|---|
| **Core** | `uv tool install artmind9` | query, `serve`, `chat-ui`, `admin-ui`, SQL querying (`db *`, `query text2sql`). |
| **Full** | `uv tool install 'artmind9[ingest]'` | core **plus** document ingestion (docling conversion, chunking, xlsx). |

`just dev-install` installs the full variant — a dev machine is full-featured. A
core-only install still lists the ingest commands in `--help`, but invoking one
prints a hint to add the extra rather than a cryptic import error. `docling` must
also be on `PATH` for non-markdown conversion. Query-only and pure-client hosts
(e.g. the canvas backend) want core; the machine that ingests wants the extra.

## Daemons

`artmind serve` and the background ingestion worker load code at **start** time,
so one left running keeps serving the *old* build after a reinstall — and a
lingering `serve` holds its port, so the next `artmind serve` fails to bind with
`[Errno 48] address already in use`.

`just dev-install` therefore stops them first. To do it on its own:

```bash
just dev-stop-daemons
```

It finds `serve` by the port it actually holds (`$ARTMIND_SERVE_PORT`, default
8377) and confirms the process is artmind before killing it, so an unrelated
process on that port is left alone.

Note that a running daemon can mask whether your changes took effect at all,
since `artmind query` proxies to it. To force in-process execution:

```bash
ARTMIND_NO_PROXY=1 artmind query ...
```

## Upgrade / uninstall

```bash
just dev-install                 # stops daemons, re-installs artmind; does not touch any vault
just dev-uninstall               # removes the `artmind` command (leaves every vault intact)
```

Upgrading refreshes code immediately (the install is editable) and reaches
already-created vaults through the symlinked `.claude/skills/` — no
per-vault re-seeding needed. Schemas are the exception: `artmind init` seeds
them only when absent, so an upgraded schema in the package does not
overwrite one you have already edited in a vault.
