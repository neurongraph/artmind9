# Vaults

**Status: design — not yet implemented.** How artmind decides which knowledge
base it is working on. Supersedes `docs/workspaces.md` (deleted; see "The
rejected design" below for why). Stores in
[stores-and-repos.md](./stores-and-repos.md); identity in
[document-identity.md](./document-identity.md).

## The model

**A vault is a directory.** It is your Obsidian vault, your git repo, and your
artmind knowledge base — one thing, not three that have to be kept pointing at
each other. Everything artmind knows about it lives in `.artmind/` inside it.

You do not select a vault. You *are in one*, or you are not, exactly as with a
git repo:

```
cd ~/Notes         && artmind query …     → this vault
cd ~/work-research && artmind admin-ui    → that vault
```

Two terminals, two vaults, at the same time, with no switch command and nothing
to keep in sync.

## The rejected design

The previous specification made a **workspace** the unit: a named entry in
`~/.artmind/workspaces.yaml`, selected by a pointer file, switched with
`artmind workspace use`. It is superseded because a global "current workspace"
is a **mode**, and it failed on contact with long-running processes.

`paths.py` resolves at import, so `admin-ui` pins its workspace at launch — but
the `claude` agent it spawns inherits no workspace variable and re-reads the
pointer on every call. Switching in another terminal put the console and its own
agent on different knowledge bases: banking's skills and schemas, personal's
graph, one header claiming banking. The fix on offer was a warning banner and
"restart to follow", which is an apology, not a flow.

Anchoring to the directory removes the failure mode instead of reporting it.
There is no global state left to drift.

## Layout

```
~/MyVault/                    ← Obsidian vault, git repo, artmind vault
├── .git/  .obsidian/
├── .claude/skills/           ← so opening the vault in Claude Code just works
├── .artmind/
│   ├── .gitignore            ← written by init: ignores data/ and logs/
│   ├── config.env            ← this vault's graph + any overrides
│   ├── same_as.yaml          ← curation — COMMITTED
│   ├── domains/
│   │   ├── meta.yaml         ← COMMITTED
│   │   └── schemas/          ← COMMITTED
│   ├── data/                 ← derived — IGNORED
│   └── logs/                 ← IGNORED
└── notes/  policies/  …      ← your documents
```

The authoritative/derived split in [stores-and-repos.md](./stores-and-repos.md)
stops being prose and becomes a `.gitignore`. Curation and schemas travel with
the vault in git; derived data does not. The table's central distinction is now
enforced by a mechanism rather than documented as a rule people must remember.

| Path | Holds | In git |
|---|---|---|
| `.artmind/config.env` | this vault's graph connection, per-vault overrides | no — may hold a graph password |
| `.artmind/same_as.yaml` | curation: merge adjudication | **yes** — authoritative, expensive to recreate |
| `.artmind/domains/` | schemas + meta-schema | **yes** — hand-edited schemas are authoritative |
| `.artmind/data/` | originals, markdowns, chunks, KG staging, registry, structured, snapshots | no — derived, and large |
| `.artmind/logs/` | ingestion, query, LLM-call logs | no |

## Resolution

Walk up from the current directory looking for `.artmind/`, exactly as git walks
up for `.git/`. Innermost wins, so nested vaults behave the way nested repos do.

Precedence, highest first:

1. `--vault PATH`
2. `ARTMIND_VAULT` — for scripts, cron, and anything with no meaningful cwd
3. the walk up from cwd

Outside any vault, a command that needs one **fails with guidance** rather than
guessing — `git status` outside a repo, not a silent default. This is the one
behaviour that surprises people exactly once.

## Machine-level config — the only global state

Secrets cannot live in the vault. A vault is a git repo you may push and an
Obsidian vault you may sync, so an API key inside it is a footgun even when
gitignored. One file stays global:

```
~/.artmind/config.env     ← LLM provider, API keys, embedding + agent models
```

The line is: **secrets and models belong to the machine; knowledge belongs to
the vault.** Loading order is most-specific-first — the vault's `config.env`
overrides the machine's, and real environment variables beat both.

Which variable goes where. Anything absent from both lists stays in the vault
and is reported rather than guessed at — promoting an unrecognised key to the
machine file would leak a vault-specific value into every vault.

| Scope | Variables |
|---|---|
| **Machine** — `~/.artmind/config.env` | `ARTMIND_USER`, `ARTMIND_KG_LLM_PROVIDER`, `ARTMIND_KG_LLM_MODEL`, `ARTMIND_KG_LLM_URL`, `ARTMIND_IMAGE_MODEL`, `ARTMIND_OLLAMA_TIMEOUT`, `ARTMIND_OPENROUTER_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ARTMIND_KG_EMBEDDINGS_*`, `ARTMIND_KG_EMBEDDING_DIMENSIONS`, `ARTMIND_SDK_*`, `ARTMIND_ACP_MODEL`, `ARTMIND_KG_CHUNK_SIZE`, `ARTMIND_INGEST_MAX_WORKERS` |
| **Vault** — `<vault>/.artmind/config.env` | `ARTMIND_KG_NEO4J_*`, `ARTMIND_VAULT_GIT_PUSH`, and a `data_dir` override |
| **Runtime** — per invocation | `ARTMIND_NO_PROXY`, `--vault` |

`ARTMIND_DATA_DIR`, `ARTMIND_VAULT_DIR` and `ARTMIND_ARCHIVE_DIR` disappear as
concepts: all three are now positions inside the vault. `ARTMIND_HOME` goes with
them — there is no run folder separate from the vault any more.

One key resists the split. `ARTMIND_KG_EMBEDDING_DIMENSIONS` follows from the
embedding model, so it is machine-level — but it is also baked into the Neo4j
vector indexes at `artmind setup`, so a machine-wide value against a vault whose
graph was built at another dimension degrades vector search *silently* rather
than erroring. It belongs in the machine file, and `init` should validate it
against the vault's graph.

The Neo4j credential is the awkward case: per-vault *and* secret. It lives in
`<vault>/.artmind/config.env`, gitignored by the file `init` writes. A later
refinement, if vaults ever need to be portable across machines: the vault names
a graph (`graph: personal`) and `~/.artmind/graphs.yaml` holds the URI and
password, so the vault carries a reference and never a credential. Additive —
not needed now.

## `artmind init`

Changes meaning: from "scaffold `~/.artmind`" to **"make this directory a
vault"**, the way `git init` makes one a repo. Run inside the directory you want
to become a vault:

1. `git init` if it is not already a repo
2. create `.artmind/` and write `.artmind/.gitignore` (`data/`, `logs/`)
3. seed `.artmind/domains/` with the **starter** schemas only, not all sixteen —
   a personal vault has no use for the banking demo corpus's domains, and
   offering domains with no data behind them degrades the agent's routing
4. seed `.claude/skills/`
5. write `.artmind/config.env` from the template
6. print what to do next

`just dev-install` must **stop running `artmind init`**. Installing the CLI and
creating a vault are now separate acts, and at install time there is no vault.

## Data, including snapshots

Everything derived lives under `.artmind/data/` — originals, markdowns, chunks,
KG staging, the registry, the structured store, and snapshots. Obsidian ignores
dotfolders, so none of it is indexed or shown.

Snapshots are the one component that grows without bound: today's install holds
467 MB of them against 177 MB of KG staging. Keeping them in the vault is the
right default for self-containment, but it needs a way out, so:

> **The admin-ui snapshot list gains a per-entry delete button.** The workflow is
> download → store it somewhere durable → delete it from the vault. Without
> this, "snapshots live in the vault" is a slow leak with no supported remedy.

A vault on Obsidian Sync or iCloud will carry all of this. `data_dir` stays
overridable in `.artmind/config.env` for that case.

## The daemon

Fixed ports do not survive multiple vaults — two `admin-ui` instances both want
8379. Rather than assigning ports per vault, drop fixed ports:

- bind port 0 and let the OS choose
- write the chosen port and pid to `<vault>/.artmind/serve.json`
- `artmind/_entry.py` reads that file to find the daemon

The daemon is then **discovered through the vault it serves**, so a daemon for
one vault is not reachable from another by construction. The workspace
fingerprint built for the previous design becomes unnecessary — the mechanism
disappears rather than getting more careful.

## What this deletes

Relative to `docs/workspaces.md`, all of this goes:

- the registry (`workspaces.yaml`) and workspace **names**
- the pointer file and `artmind workspace use` / `list` / `env`
- `workspace create` — collapses into `artmind init` run in a directory
- the `/health` workspace fingerprint and its stdlib mirror
- guardrail 3 (schema seeding) — per-vault seeding is now automatic
- guardrail 4 (two workspaces claiming one vault) — impossible by construction
- ports-per-vault, the drift banner, and "restart to follow"

`workspace adopt` survives, simplified: migrate `~/.artmind` + `~/artmind_data`
into a chosen directory's `.artmind/`, copying and leaving the original in place.

## Guardrails that survive

**No implicit checkout-local `.env` fallback.** It silently loaded another
knowledge base's config, credentials and graph included, whenever a run folder
had none of its own. It matters more here, not less: there are now many
config files. `ARTMIND_ALLOW_REPO_ENV=1` opts back in for a dev clone.

**Whole-vault ingest must not attempt every file type.** Hidden directories are
*already* handled — `collect_ingest_files` skips any path with a dot-prefixed
part, so `.artmind/`, `.obsidian/`, `.git/` and `.claude/` cost nothing. What is
still missing is a supported-type allowlist: `ingest_file` routes every non-`.md`
file to docling, so an Obsidian vault's `.png` attachments run through image
description at full LLM cost and its `.canvas` files are handed to a document
converter that cannot read JSON. Unknown types must be skipped and reported.

**Snapshot defaults split by verb.** Omitting `curation` on create risks losing
merge adjudication; including it on restore overwrites live curation. Create
defaults with it, restore defaults without.

## Open

- **`_derived/` and `_meta/`.** `_meta/` is three hand-authored notes about the
  corpus (`index.md`, `schema_mapping.md`, `README.md`) that no code reads or
  writes and that have never been ingested — under this model there is no
  "not corpus content" distinction left to signal, so it can simply be a normal
  folder. `_derived/` holds genuinely editable documents awaiting promotion and
  must stay visible, but the name reads as machine junk in an Obsidian sidebar.
- **`docs/INSTALL.md`** describes the old two-root flow (`~/.artmind` +
  `~/artmind_data`) and needs rewriting against this one.
- **Migrating the existing install.** The banking vault at
  `~/Projects/artmind-corpus` becomes self-contained: `~/.artmind` and
  `~/artmind_data` fold into `<vault>/.artmind/`.
- **Query-only consumers** (the canvas backend) currently need no vault. They
  will need `--vault` or `ARTMIND_VAULT`.
