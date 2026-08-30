# Vaults

**Status: specification — not yet implemented.** How artmind decides which
knowledge base it is working on, and what lives where inside it. Supersedes the
deleted `docs/workspaces.md`. Topology in
[stores-and-repos.md](./stores-and-repos.md); identity in
[document-identity.md](./document-identity.md).

## The model

**A vault is a directory.** It is your Obsidian vault, your git repo and your
artmind knowledge base — one thing, not three kept pointing at each other.
Everything artmind knows about it lives in `.artmind/` inside it.

You do not select a vault. You *are in one*, or you are not, exactly as with a
git repo:

```
cd ~/Notes         && artmind query …     → this vault
cd ~/work-research && artmind admin-ui    → that vault
```

Two terminals, two vaults, at once, with no switch command and nothing to keep
in sync.

## The rejected design

The previous specification made a **workspace** the unit: a named registry entry
selected by a global pointer file and switched with `artmind workspace use`. It
is superseded because a global "current workspace" is a **mode**, and it broke on
contact with long-running processes.

`paths.py` resolves at import, so `admin-ui` pinned its workspace at launch while
the `claude` agent it spawns inherited no workspace variable and re-read the
pointer on every call — console and agent on different knowledge bases, one
header claiming the wrong one. The remedy on offer was a warning banner and
"restart to follow", which is an apology rather than a flow. Anchoring to the
directory removes the failure mode instead of reporting it. There is no global
state left to drift.

## Layout

```
~/MyVault/                          ← Obsidian vault, git repo, artmind vault
├── .git/  .obsidian/
├── .claude/skills/                 ← artmind's (symlinked, ignored) + yours (committed)
├── .artmind/
│   ├── .gitignore                  ← written by init
│   ├── config.env                  ← this vault's graph; ignored
│   ├── vault.yaml                  ← folder→domain mapping + settings; COMMITTED
│   ├── state.json                  ← ingest cursor; ignored
│   ├── same_as.yaml                ← curation; COMMITTED
│   ├── domains/                    ← schemas + meta-schema; COMMITTED
│   ├── data/                       ← everything derived; ignored
│   ├── logs/                       ← ignored
│   └── serve.json  worker.pid      ← daemon discovery; ignored
├── _derived/<domain>/              ← binary-derived markdown + its images; COMMITTED
├── sources/                        ← your pdfs, decks; ignored (see below)
├── Inbox/                          ← drafts; unmapped, so never ingested
└── notes/  policies/  …            ← your documents; COMMITTED
```

Inside `.artmind/data/`: `originals/` (only sources from *outside* the vault),
`chunks/`, `kg/` (staging — the expensive layer), `document_registry.db`,
`structured/`, `snapshots/`, `jobs/`, `refine/`.

## Resolution

Walk up from the current directory for `.artmind/`, exactly as git walks up for
`.git/`. Innermost wins, so nested vaults behave like nested repos.

Precedence, highest first: `--vault PATH`, then `ARTMIND_VAULT` (for cron and
anything with no meaningful cwd), then the walk up from cwd.

Outside any vault, a command that needs one **fails with guidance** rather than
guessing — `git status` outside a repo, not a silent default. This is the one
behaviour that surprises people exactly once.

## What is in git, and what is not

The authoritative/derived split in [stores-and-repos.md](./stores-and-repos.md)
stops being prose and becomes a `.gitignore` that `init` writes. The rule:

> **Git holds what git can meaningfully version.** Text that diffs. Not opaque
> binaries, not regenerable derivatives.

| In git | Why |
|---|---|
| your documents | the point |
| `_derived/<domain>/*.md` | the readable rendering of a binary — a `.pptx` diff tells you nothing, its markdown diff is the actual change |
| `_derived/**/*_artifacts/*` | images extracted during conversion; the markdown references them, so without these Obsidian renders broken |
| `.artmind/vault.yaml` | the ingest manifest — reviewable, and it *is* the corpus's structure |
| `.artmind/domains/` | schemas are your ontology, hand-edited and authoritative |
| `.artmind/same_as.yaml` | curation: merge adjudication, expensive to recreate |

| Not in git | Why |
|---|---|
| `*.pdf .pptx .docx .png .jpg` outside `_derived/` | opaque and large; git versions their markdown instead |
| `.artmind/data/` | derived, and unbounded |
| `.artmind/config.env` | may hold a graph password |
| `.artmind/logs/`, `state.json`, `serve.json` | machine-local |

Binary attachments need a negation so extracted images survive the extension
rules:

```gitignore
*.pdf
*.pptx
*.png
!_derived/**
```

**The consequence, stated plainly:** a gitignored binary in the vault has **no
version history and no second copy**. Today `documents/originals/` is
authoritative precisely because it is the only copy artmind keeps; that inverts.
Backing up vault binaries becomes the user's job — Time Machine, a backup disk,
anything. What survives regardless is the markdown in `_derived/`, so the
*content* is never lost, only the original formatting, which stops being the
source of truth the moment the derived markdown is edited (promotion — see
[document-identity.md](./document-identity.md)).

### Two duplications this removes

**Binaries.** A source that already lives in the vault is **never copied**.
That is exactly what Phase 2 did for vault-native markdown; binaries are the
symmetric case. `.artmind/data/originals/` keeps only sources ingested from
*outside* the vault, where artmind genuinely is the only keeper.

**Markdown.** `documents/markdowns/` disappears. The vault's
`_derived/<domain>/<stem>.md` **is** the markdown; `.artmind/data/` keeps only
the split chunks. This closes the duplication `stores-and-repos.md` has flagged
since the redesign.

## The ingest manifest — `.artmind/vault.yaml`

`_meta/schema_mapping.md` in the banking corpus is a feature request written as
prose: a table of which schema governs which folder, executed by hand as one
`ingest sync` per folder. It becomes configuration:

```yaml
ingest:
  trigger: manual              # manual | commit | schedule
  mappings:
    - path: policies/**
      domain: banking.policy
    - path: sop_procedures/**
      domain: banking.sop_guides
    - path: scans/**
      domain: banking.reference
```

The mapping does two jobs, and the second is what makes it worth building:

1. **Which domain** governs a path's extraction.
2. **Whether to ingest it at all.** `artmind ingest sync .` ingests mapped paths
   and skips everything else. An `attachments/` folder is simply not mapped, so
   it is never handed to docling — no separate ignore mechanism, one concept
   instead of two. A `scans/` folder of images *is* mapped, so images ingest
   without an `--includeImages` flag.

It also gives drafting a natural home. An unmapped `Inbox/` is never ingested;
**moving a note into a mapped folder is what says "this is ready"** — which is
already how PARA and Zettelkasten workflows behave in Obsidian, and it is the
answer to timer-based commits capturing half-written notes.

Domain precedence, highest first: `--setDomain` (force + re-extract) → the file's
own `_domain` frontmatter → the folder mapping → `--domain` as the fallback for
unmapped files → prompt.

## Ingest triggers

Every trigger today is manual. For a corpus ingested once that is fine; for a
journal written in daily, "remember to run ingest" is what kills the habit.

The constraint that shapes the design: **ingestion costs real money** — LLM
extraction per chunk — and Obsidian autosaves every few seconds. So the trigger
must track *"this note is worth extracting"*, never *"this file changed"*.

### One operation, several pokes

Ingestion is always: **enqueue everything that changed between the cursor and
`HEAD`.** `.artmind/state.json` holds `last_ingested_commit`.

Deliberately a cursor and **not** a `post-commit` hook. Obsidian Git runs on
isomorphic-git (which is how it works on mobile), so whether a hook fires depends
on platform and version — a hook-based trigger would work on the desktop and
silently stop on the phone. The cursor is also better on its own merits:

- **writer-agnostic** — Obsidian Git, CLI git and artmind's own commits are alike
- **idempotent** — re-running with `HEAD` unmoved does nothing
- **catches up** — away a week, one command ingests the backlog
- **testable** — no daemon, no filesystem watcher

Triggers reduce to pokes at that one operation: `manual` (`artmind ingest sync`),
`commit` (a hook where one fires), `schedule` (a timer), or the admin-ui button.
All the same code path.

Every trigger **enqueues** into the existing job system rather than ingesting
inline, so a burst of edits cannot spawn N processes — `jobs.py` and `worker.py`
already do this, and the worker is already per-vault via its pid file.

Default is `manual`. Nobody should discover automatic LLM spend by surprise;
`init` offers to change it.

### Running alongside the Obsidian Git plugin

Two writers on one repo is fine here. `vault_git.commit_paths` already fails
non-fatally on `index.lock`, and with a cursor a lost commit is harmless — the
next sweep includes it. Leave `ARTMIND_VAULT_GIT_PUSH` unset and let the plugin
own pushing.

The loop terminates by construction: artmind writes frontmatter → someone commits
it → the cursor sees a change → but `compute_content_sha256` hashes the **body
only**, so the version decision is `metadata_only`, minting no observations at no
LLM cost.

## Skills and schemas

They go opposite ways, and the line is: **skills are code, schemas are content.**

### Skills — machine-level, symlinked in

`ClaudeAgentOptions.skills` takes skill *names* (`list[str] | 'all' | None`),
resolved from `.claude/skills/` relative to the agent's cwd. The agent's cwd is
the vault, so the skills must be present there.

Canonical copy: `~/.artmind/skills/`, installed and updated with the CLI.
`artmind init` symlinks them into `<vault>/.claude/skills/`, gitignored. Updates
then propagate through the symlink — no re-seeding, and no N-copies-to-update
problem. The checkout already proves the pattern: CLAUDE.md documents
`.claude/skills/<name>` as symlinks into `artmind/skills/`.

So `<vault>/.claude/skills/` holds artmind's skills (symlinked, ignored) beside
any you write yourself (committed), and opening the vault in Claude Code directly
gets you both. Where symlinks are unavailable — Windows without privileges, some
sync services — the fallback is a copy refreshed by an explicit `artmind update`.

### Schemas — vault-level, committed, yours

The repo keeps the shipped library; the vault keeps what it actually uses, in
`.artmind/domains/schemas/`, committed.

**`init` must stop overwriting them.** Overwrite-always was safe when one run
folder was reseeded from the package; it would now clobber hand-authored vault
schemas. But never overwriting recreates the problem CLAUDE.md warns about — a
prompt fix that never reaches the vault looks like a model failure. So:

- seeded schemas carry provenance (`_source: package` plus a hash)
- `init` seeds only the **starter** set, and only what is missing
- `artmind domains update` refreshes package-derived schemas you have not
  modified, and **reports** the ones that diverged for you to merge
- `artmind domains add` stays, for vault-local schemas

## `artmind init`

Changes meaning: from "scaffold `~/.artmind`" to **"make this directory a
vault"**, the way `git init` makes one a repo.

1. `git init` if not already a repo
2. create `.artmind/`, write `.artmind/.gitignore`
3. seed starter schemas into `.artmind/domains/`
4. symlink skills into `.claude/skills/`
5. write `.artmind/config.env` from the template, and a starter `vault.yaml`
6. print next steps

`just dev-install` must **stop running `artmind init`** — installing the CLI and
creating a vault are separate acts, and at install time there is no vault.

## Machine-level config — the only global state

Secrets cannot live in the vault: it is a repo you may push. One file stays
global — `~/.artmind/config.env`. The line is **secrets and models belong to the
machine; knowledge belongs to the vault.** Loading is most-specific-first, so the
vault's `config.env` overrides the machine's, and real environment variables beat
both.

| Scope | Variables |
|---|---|
| **Machine** — `~/.artmind/config.env` | `ARTMIND_USER`, `ARTMIND_KG_LLM_*`, `ARTMIND_IMAGE_MODEL`, `ARTMIND_OLLAMA_TIMEOUT`, `ARTMIND_OPENROUTER_API_KEY`, `ANTHROPIC_*`, `ARTMIND_KG_EMBEDDINGS_*`, `ARTMIND_KG_EMBEDDING_DIMENSIONS`, `ARTMIND_SDK_*`, `ARTMIND_ACP_MODEL`, `ARTMIND_KG_CHUNK_SIZE`, `ARTMIND_INGEST_MAX_WORKERS` |
| **Vault** — `<vault>/.artmind/config.env` | `ARTMIND_KG_NEO4J_*`, `ARTMIND_VAULT_GIT_PUSH` |
| **Runtime** | `ARTMIND_NO_PROXY`, `--vault` |

`ARTMIND_HOME`, `ARTMIND_DATA_DIR`, `ARTMIND_VAULT_DIR` and
`ARTMIND_ARCHIVE_DIR` all disappear as concepts: every one of them is now a
position inside the vault.

One key resists the split. `ARTMIND_KG_EMBEDDING_DIMENSIONS` follows from the
embedding model, so it is machine-level — but it is baked into the Neo4j vector
indexes at `artmind setup`, so a machine-wide value against a vault whose graph
was built at another dimension degrades vector search **silently** rather than
erroring. It stays machine-level, and `init` validates it against the vault's
graph.

## Snapshots

Snapshots live in `.artmind/data/snapshots/` with everything else derived, which
keeps the vault self-contained. They are also the one component that grows
without bound — today's install holds 467 MB of them against 177 MB of KG
staging. So:

> **The admin-ui snapshot list gains a per-entry delete button.** Download →
> store somewhere durable → delete from the vault. Without it, "snapshots live in
> the vault" is a slow leak with no supported remedy.

## The daemon

Fixed ports do not survive multiple vaults — two `admin-ui` instances both want
8379. Rather than assigning ports per vault, drop fixed ports: bind port 0, write
the chosen port and pid to `<vault>/.artmind/serve.json`, and have
`artmind/_entry.py` read that file.

The daemon is then **discovered through the vault it serves**, so a daemon for one
vault is unreachable from another by construction, and the workspace fingerprint
the previous design needed becomes unnecessary.

## What this deletes

- the workspace registry, workspace **names**, the pointer file, and
  `artmind workspace use` / `list` / `env` / `create`
- the `/health` workspace fingerprint and its stdlib mirror
- `ARTMIND_HOME` / `ARTMIND_DATA_DIR` / `ARTMIND_VAULT_DIR` / `ARTMIND_ARCHIVE_DIR`
- `documents/markdowns/`, and `originals/` for anything already in the vault
- per-vault schema-seeding guardrails (automatic now) and the "two workspaces,
  one vault" check (impossible now)
- ports-per-vault, the drift banner, "restart to follow"

`workspace adopt` survives as `artmind vault adopt`: fold `~/.artmind` and
`~/artmind_data` into a directory's `.artmind/`, copying and leaving the original.

## Guardrails that survive

**No implicit checkout-local `.env` fallback.** It silently loaded another
knowledge base's config — credentials and graph included — whenever a run folder
had none of its own. It matters more here: there are now more config files.
`ARTMIND_ALLOW_REPO_ENV=1` opts back in for a dev clone.

**A supported-type allowlist.** Hidden directories are *already* handled —
`collect_ingest_files` skips any dot-prefixed path component, so `.artmind/`,
`.obsidian/`, `.git/` and `.claude/` cost nothing. What is missing is type
checking: `ingest_file` routes every non-`.md` file to docling, so a `.canvas`
file (JSON) is handed to a document converter that cannot read it. Unknown types
must be skipped and reported.

**Snapshot defaults split by verb.** Omitting `curation` on create risks losing
merge adjudication; including it on restore overwrites live curation. Create
defaults with it; restore defaults without.

## Resolved

- **`_meta/`** — three hand-authored notes (`index.md`, `schema_mapping.md`,
  `README.md`) that no code reads and that were never ingested. Its useful half
  becomes `vault.yaml`; the rest is just notes, living anywhere unmapped.
- **`_derived/`** — stays visible in the vault and committed, since it holds
  genuinely editable documents. Its images are committed with it.

## Open

- **`docs/INSTALL.md`** describes the old two-root flow and needs rewriting.
- **Query-only consumers** (the canvas backend) need `--vault` or `ARTMIND_VAULT`.
- **`_derived/` is an awkward name** in an Obsidian sidebar. Renaming it is
  cosmetic and can wait.
