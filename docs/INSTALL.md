# Installing artmind (run from anywhere)

artmind installs as a global `artmind` CLI and runs from a dedicated **run
folder** — it does not need to be launched from this source checkout.

## Layout

| Location | Default | Holds | Used by |
|---|---|---|---|
| **Run folder** — `$ARTMIND_HOME` | `~/.artmind` | `.env`, `.claude/skills/`, `.opencode/agent/`, `domains/schemas/`, `logs/` | every command (query / serve / chat-ui / ingest) |
| **Data dir** — `$ARTMIND_DATA_DIR` | `~/artmind-data` | originals, markdowns, `document_registry.db`, jobs, kg staging, snapshots | ingestion only |

Query / `serve` / `chat-ui` read almost nothing from disk (config + Neo4j);
the corpus and all ingestion artifacts live under the separate data dir, so a
query-only host can leave `$ARTMIND_DATA_DIR` empty or unset.

`$ARTMIND_HOME` is resolved *before* `.env` is read, so override it only via a
real environment variable (e.g. `export ARTMIND_HOME=/opt/artmind`). Set
`ARTMIND_DATA_DIR` in the environment or in `~/.artmind/.env`.

## Prerequisites

- Python (see `.python-version`) and [`uv`](https://docs.astral.sh/uv/).
- A running **Neo4j** with vector-index support.
- LLM/embeddings access: local **Ollama**, or an **OpenRouter** API key.

## Install

```bash
just dev-install          # puts `artmind` on PATH (uv tool, editable) + `artmind init`
```

This is the single install path for both development and running. It is
editable, so code edits are live, and because paths are decoupled from the
checkout the `artmind` command runs from any directory. (For a deploy where the
checkout won't stay in place, drop `--editable` in the `install` recipe.)

`artmind init` scaffolds `~/.artmind` and `~/artmind-data`, seeds
`~/.artmind/.env` from the bundled template, and copies the skills, opencode
persona, and default domain schemas into the run folder. It is idempotent and
needs no Neo4j.

Seeding follows two policies, by what the tree holds:

| Tree | Policy | Why |
|---|---|---|
| `.claude/skills/`, `.opencode/` | **Overwritten every run** | Package assets — `artmind/skills/` and `artmind/opencode/` are their source of truth. Edit there; a reinstall ships the current version. |
| `.env`, `domains/schemas/` | **Seeded only when absent** | User data — your credentials, edits, and added domains survive. |

Entries are replaced wholesale, so a file dropped from a skill also disappears
from the run folder. Names the package doesn't ship are never pruned, so a
hand-written skill or domain in the run folder is left alone.

Then:

```bash
$EDITOR ~/.artmind/.env      # Neo4j URI/creds, LLM provider/keys, optional ARTMIND_DATA_DIR
artmind setup                # create Neo4j constraints/indexes + SQLite tables
```

## Run (from any directory)

```bash
cd ~                         # nothing special about this dir
artmind query graph metadata --domain <domain> --compact
artmind serve                # warm query daemon (query calls proxy to it)
artmind chat-ui              # web UI at http://127.0.0.1:8378
```

The chat agent runs with its working directory set to the run folder
(`~/.artmind`), which contains only skills, schemas, and logs — the source tree
and document corpus are not present there.

## Keeping data in the checkout (optional)

By default ingestion data lives at `~/artmind-data`. To keep it (and config)
inside the repo during development, point the two roots at repo-local paths and
re-run init:

```bash
export ARTMIND_HOME="$PWD/.artmind-dev"
export ARTMIND_DATA_DIR="$PWD/data"
artmind init
```

A repo-root `.env` is also auto-loaded as a fallback when `$ARTMIND_HOME/.env`
is absent, so an existing checkout `.env` keeps working.

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
just dev-install                 # stops daemons, re-installs; refreshes skills, keeps your .env
just dev-uninstall               # removes the `artmind` command (leaves ~/.artmind and data intact)
```
