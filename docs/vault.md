# Vaults

**Status: partially implemented** on branch `feat/vault`. Landed: discovery,
resolution precedence, the `VaultLayout` class, the machine/vault config split,
`artmind init`, and the ingest manifest. **This document was substantially
revised on 2026-08-30** to withdraw the `_derived/` promotion model in favour
of the ownership rule below. Since then the ownership rule has landed: the
embedding sidecar, the resumable chunk-embed sweep, the inverted `.gitignore`
(derived output committed by default), `_Inbox`/archive exclusion,
`_external_docs/` for externally-sourced documents, and deletion of the
`_derived/` promotion model entirely. See "What this replaces".

How artmind decides which knowledge base it is working on, and what lives where
inside it. Topology in [stores-and-repos.md](./stores-and-repos.md); identity in
[document-identity.md](./document-identity.md).

## The model

**A vault is a directory whose `.artmind/` holds a `vault.yaml` manifest.** It is
your Obsidian vault, your git repo and your artmind knowledge base — one thing,
not three kept pointing at each other.

The manifest rather than the directory is the marker because `~/.artmind` is
*also* the machine-wide config directory: keying on the directory alone made
`$HOME` itself resolve as a vault, so any command run from anywhere beneath it
would key document identity off `$HOME`. It also stops a half-created
`.artmind/` being mistaken for a vault.

You do not select a vault. You *are in one*, or you are not, exactly as with a
git repo:

```
cd ~/Notes         && artmind query …     → this vault
cd ~/work-research && artmind admin-ui    → that vault
```

Two terminals, two vaults, at once, with no switch command and nothing to keep
in sync.

## The ownership rule

This is the rule everything else follows from:

> **`.artmind/` belongs to artmind. You never edit it; artmind never guesses.**
> Everything artmind derives lives there, and — with a short exclusion list — is
> committed to git.

Outside `.artmind/` is yours: your notes, your folders, your binaries. Inside it
is artmind's working output, versioned so you can see and share it, but not a
place to edit. If you do edit it, artmind cannot guarantee the resulting state.

The rule is worth stating because of how much it *deletes*. The previous model
put converted markdown in a user-visible `_derived/` folder, which meant artmind
had to detect your edits to it and defer to them. That single fact produced the
whole promotion machinery: `_derived_sha256`, `markdown_edited`, a four-outcome
decision table, a collision case artmind refuses to resolve, and a `git mv` that
relocates a document mid-life. With the ownership rule, a document has one
location for its whole life and the pipeline is **convert → chunk → extract**.

Two corollaries:

- **`_Inbox/` at the vault root is never ingested.** A drafting area that needs
  no configuration. (Any unmapped path is equally safe — see the manifest — but
  `_Inbox/` is the conventional one.)
- **If a conversion comes out wrong**, you do not fix it in `.artmind/`. Copy the
  markdown out into the vault as an ordinary note, move the binary to `_Inbox/`,
  and ingest the note. That is the supported workflow, and it needs no machinery
  because the note is then just a note.

## Layout

```
~/MyVault/                              ← Obsidian vault, git repo, artmind vault
├── .git/  .obsidian/
├── .claude/skills/                     ← artmind's (symlinked, ignored) + yours
├── _Inbox/                             ← drafts; never ingested
├── _external_docs/                     ← copies of sources from outside the vault
├── area1/  notes/  …                   ← your documents, your binaries
└── .artmind/                           ← ARTMIND-OWNED. Do not edit.
    ├── vault.yaml                      ← the ingest manifest; COMMITTED
    ├── config.env                      ← this vault's graph; NOT committed
    ├── same_as.yaml                    ← curation; COMMITTED
    ├── domains/                        ← schemas + meta-schema; COMMITTED
    ├── logs/  state.json  serve.json   ← machine-local; NOT committed
    └── data/
        ├── documents/markdowns/
        │   ├── a_deck.md               ← converted; COMMITTED
        │   ├── a_deck_artifacts/       ← extracted images + their descriptions
        │   └── a_deck_chunks/          ← chunk_001.md, chunks_meta.json
        ├── kg/<domain>/<doc>/          ← extraction output; COMMITTED
        ├── document_registry.db        ← path↔id cache; NOT committed
        ├── graph_snapshot/             ← *.tar.gz; NOT committed
        └── structured_snapshot/        ← *.tar.gz; NOT committed
```

## Resolution

Walk up from the current directory for `.artmind/vault.yaml`, exactly as git
walks up for `.git/`. Innermost wins, so nested vaults behave like nested repos.

Precedence, highest first: `--vault PATH`, then `ARTMIND_VAULT` (for cron and
anything with no meaningful cwd), then the walk up from cwd.

Outside any vault, a command that needs one **fails with guidance** rather than
guessing — `git status` outside a repo, not a silent default. This is the one
behaviour that surprises people exactly once.

## What is in git, and what is not

Everything under `.artmind/` is committed **except** a short list, and the
exclusions are not arbitrary — each is either a secret, a churning binary, or
machine-local state:

| Not committed | Why |
|---|---|
| `.artmind/config.env` | holds `ARTMIND_KG_NEO4J_PASSWORD`. A vault is a repo you may push. |
| `.artmind/data/document_registry.db` | a SQLite binary rewritten on every ingest; merges catastrophically, and `docs reindex` rebuilds it |
| `.artmind/logs/`, `state.json`, `serve.json`, `worker.pid` | machine-local runtime state, meaningless on another machine |
| `*.zip`, `*.tgz`, `*.tar.gz` anywhere | snapshots. Large, opaque, and already a complete copy of what git is versioning |
| embeddings inside committed KG staging | see "Embeddings" below |

Everything else is committed, including things previous versions of this
document kept out: the converted markdown, the extracted images and their
descriptions, the chunk files, and the KG staging JSON.

**Committing KG staging is deliberate, not incidental.** It is the expensive
layer — hours and real money of LLM extraction — and putting it in git means a
clone reproduces the graph at zero API cost. This is not a novel idea here:
`artmind ingest pull-kg` already exists to fetch KG JSON from a git repo, so
KG-in-git is a workflow the system was designed for.

Binaries are committed too, which reverses the previous model. The consequence
that used to need stating — "a gitignored binary has no version history and no
second copy" — is gone: `_external_docs/` and vault-resident binaries are both
versioned like anything else.

### Sizing, honestly

Git does **not** store diffs. It stores each version as a compressed snapshot,
and delta-compresses only later, at pack time, between objects it guesses are
similar. That works well for prose and for JSON whose keys are stable. It works
badly for two things you will be committing:

- **`.pptx` and `.png`** — a `.pptx` is a ZIP, so changing one slide scrambles
  the compressed stream and the delta is poor.
- **Embeddings** — edit one word and all 768 floats change, so there is nothing
  to delta against. Measured on this corpus: ten versions of one `chunks.json`
  cost **60 KB** of git objects with embeddings and **20 KB** without.

Hence the exclusion below. What remains — markdown, chunk text, extraction JSON
— deltas well, and git never forgetting is the point rather than the problem.

## Embeddings

A chunk embedding is a pure function of `(text, embedding model)`. It is
**derived**, deterministic, and reproducible locally at no API cost — so it is
the one thing inside committed KG staging that git should not carry.

| Where | Embeddings | Why |
|---|---|---|
| committed KG staging (`data/kg/**/chunks.json`) | **stripped** | random floats, no useful delta, fully re-derivable |
| the graph (Neo4j) | present | the vector index is the point |
| snapshots (`*.tar.gz`) | present | not in git anyway, and their whole job is *fast* restore |

That split gives each layer the property it should have: the git-committed layer
stays small and diffable, the snapshot layer stays fat and instant.

**Restoring, therefore, has an embedding step.** `write-to-graph` writes chunks
with no vectors, and a resumable sweep fills them in — mirroring how entity
embeddings already work (`artmind ingest embed-entities`, plus the
`embedding_stale` flag). `write-to-graph` runs the sweep by default; `--noEmbed`
skips it.

**Why the sweep is separate rather than inline:** a graph write should be fast
and predictable. Re-embedding a large vault is minutes of local work, and
folding it into `write-to-graph` makes that command sometimes-instant and
sometimes-not, with a Ctrl-C that leaves you unsure what landed. As a sweep it is
resumable and interruptible at no cost.

**And it must be reported, because the failure is silent.** A null embedding is
absent from the vector index, so an unembedded chunk is simply invisible to
semantic search — no error, just quietly worse answers. Three channels:

1. an `embedded` count in the summary `write-to-graph` already returns, so JSON
   consumers and the admin UI see it;
2. a line before a long run saying what is about to happen and that it is local
   and one-off, so a fresh clone does not look hung;
3. **a standing count of unembedded chunks in `artmind projection status`** —
   the important one. Narrating work while it happens does not help with the
   dangerous state, which is the one where the work did not happen and nobody
   noticed.

## Where a document lands

Four cases, distinguished by where the source lives and what it is. In every
case the converted markdown, its artifacts, its chunks and its KG staging land
in the same place — the uniformity is the point.

| Source | The source ends up | Converted markdown | Identity |
|---|---|---|---|
| binary from outside the vault | copied to `_external_docs/`, committed | `data/documents/markdowns/<stem>.md` | the **source path** |
| binary already in the vault | stays where you put it, committed | `data/documents/markdowns/<stem>.md` | `_artmind_id` on… see below |
| markdown from outside the vault | copied to `_external_docs/`, committed | `data/documents/markdowns/<stem>.md` | the **source path** |
| markdown already in the vault | stays where you put it | `data/documents/markdowns/<stem>.md` | `_artmind_id` in its frontmatter |

**Identity for vault-resident files is `_artmind_id`, written into the vault
file** — not into the copy under `data/documents/markdowns/`. That way renaming
or moving your note keeps its history, which is the whole point of
[document-identity.md](./document-identity.md). The `data/documents/markdowns/`
copy is the *ingested snapshot*: immutable, matching the KG staging beside it,
and therefore genuine provenance rather than redundancy.

**Identity for external files is the source path.** Two different decks both
named `deck.pptx`, from different folders, are different documents — not
versions of each other. Same path with changed bytes is a new version; a
different path with the same basename is a different document and is stored
distinctly under `_external_docs/`. Name-based identity is precisely the problem
`_artmind_id` was introduced to solve, and it must not creep back in here.

### Standalone images

An image that is not an attachment inside a note — a diagram, a screenshot, a
scan — is treated as a binary source like any other: copied if external,
described by the vision model, and the description stored as its markdown. It is
**not** put through OCR by default.

The exception worth allowing: a scan of a page of text is exactly what OCR is
for, and a vision description of it is a poor substitute. So OCR is opt-in per
mapping in `vault.yaml` rather than never available.

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

> **Not yet implemented.** `trigger:` is read and validated, and an unknown
> value is refused, but only `manual` does anything today. The cursor and the
> commit/schedule pokes are their own plan.

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

**Getting `~/.artmind/config.env` populated in the first place** used to be a
gap: `scaffold_run_folder` (`just dev-install`) used to seed `~/.artmind/.env`
instead — the *legacy* run-folder path, only ever loaded when `ARTMIND_HOME`
resolves to the true machine home, i.e. outside any vault. The moment you `cd`
into a vault, `ARTMIND_HOME` becomes `<vault>/.artmind` and that seeded `.env`
dropped out of `paths.py`'s load order entirely, so the first vault anyone
created had Neo4j placeholders and nothing else: no provider, no API key, no
model, and no explanation.

`setup.ensure_machine_config()` closes this now, called from both
`scaffold_run_folder` (`just dev-install` / `artmind setup`) and
`scaffold_vault` (`artmind init`), whichever runs first:

1. `~/.artmind/config.env` already exists → left alone.
2. it's missing but an older install's legacy `~/.artmind/.env` holds real
   settings → migrated (stripping the vault-scoped keys above, whose home is
   now a vault's own `config.env`), so a returning user's actual settings
   always win over a bare template.
3. neither exists → **seeded fresh from the package's `env.example`**
   (filtered the same way), so a bare `just dev-install` alone leaves a
   working, if default-filled, `~/.artmind/config.env` — no `artmind init`
   step required first, and nothing left to fail silently later.

**A second, deeper layer of the same fix:** `ensure_machine_config()`'s filter
above only protects the file it *generates*. A live ingest still hit this bug
because the reporter's `~/.artmind/config.env` had an uncommented
`ARTMIND_DATA_DIR` — hand-carried over from an older, pre-vault `.env` — and
the vault's own `config.env` correctly leaves that key commented out by
default (deferring to the vault-relative default), so nothing in the normal
"vault overrides machine" load order ever caught it. Two more changes close
this for good, not just for files this code writes:

- `artmind/env.example` itself now carries **no** vault-scoped key at all —
  no `ARTMIND_DATA_DIR`, `ARTMIND_VAULT_DIR`, `ARTMIND_ARCHIVE_DIR`, or
  `ARTMIND_KG_NEO4J_*` — so there is nothing left to copy-paste into a
  machine config by hand in the first place.
- `paths.py` now enforces the boundary at **load time**, not just at
  generation time: when it loads `MACHINE_CONFIG_ENV` specifically (never a
  vault's own `config.env`, which is exactly where these belong) while a real
  vault is in play, it ignores `ARTMIND_KG_NEO4J_*`/`ARTMIND_DATA_DIR`/
  `ARTMIND_VAULT_DIR`/`ARTMIND_ARCHIVE_DIR` outright and prints a one-line
  warning naming exactly which key it ignored — so a hand-edited or
  pre-existing machine config.env can no longer silently redirect a vault's
  data outside itself, on this machine or any other.

## Snapshots

Snapshots live in `.artmind/data/graph_snapshot/` (and structured-store
snapshots in `.artmind/data/structured_snapshot/`) as `*.tar.gz`, and are the one
part of `.artmind/` that is **not** committed — they are large, opaque, and a
complete duplicate of what git is already versioning. They are also the reason
the exclusion list names archive extensions rather than a single path: a
snapshot dropped anywhere in the vault should stay out of both git and
ingestion.

Because they are excluded from git, they are also the one derived artifact with
no version history — which makes deleting them safe but losing them permanent.
So:

> **The admin-ui snapshot list needs a per-entry delete button.** Download →
> store somewhere durable → delete from the vault. Without it, snapshots are a
> slow leak with no supported remedy, since nothing else prunes them.

## The daemon

Fixed ports do not survive multiple vaults — two `admin-ui` instances both want
8379. Rather than assigning ports per vault, drop fixed ports: bind port 0, write
the chosen port and pid to `<vault>/.artmind/serve.json`, and have
`artmind/_entry.py` read that file.

The daemon is then **discovered through the vault it serves**, so a daemon for one
vault is unreachable from another by construction, and the workspace fingerprint
the previous design needed becomes unnecessary.

## What this replaces

Two models preceded this one, and both are recorded here for context even
though the code no longer implements either.

**The workspace model** (deleted `docs/workspaces.md`) made a named registry
entry the unit, selected by a global pointer file. A global "current workspace"
is a mode, and it broke on contact with long-running processes: `paths.py`
resolves at import, so `admin-ui` pinned its workspace at launch while the agent
it spawns re-read the pointer on every call — console and agent on different
knowledge bases. Anchoring to the directory removed the failure mode instead of
reporting it.

**The `_derived/` promotion model** (this document, before 2026-08-30) put
converted markdown in a user-visible `_derived/<domain>/` folder so you could fix
a mangled conversion by hand. That single affordance required artmind to detect
your edits and decide between them and the binary, producing `_derived_sha256`,
`markdown_edited`, `_decide_promotion`, a collision case it refuses to resolve,
`_is_promoted`, and a mid-life `git mv` — which in turn broke the relative links
to a document's extracted images, since promotion moved the markdown and not the
images.

The ownership rule replaces all of it with a documented manual workflow: copy the
markdown out, ingest it as an ordinary note. Everything listed above has since
been deleted, including `artmind/derived_markdown.py` in its entirety.

## Guardrails that survive

**No implicit checkout-local `.env` fallback.** It silently loaded another
knowledge base's config — credentials and graph included — whenever a run folder
had none of its own. `ARTMIND_ALLOW_REPO_ENV=1` opts back in for a dev clone.

**A supported-type allowlist.** Hidden directories are already skipped by
`collect_ingest_files`, so `.artmind/`, `.obsidian/`, `.git/` and `.claude/` cost
nothing. The allowlist is derived from the sets that define what each pipeline
handles, so a type added to one cannot silently vanish from directory walks —
which is exactly what happened to `.xlsm`.

**Snapshot defaults split by verb.** Omitting `curation` on create risks losing
merge adjudication; including it on restore overwrites live curation. Create
defaults with it; restore defaults without.

## Known gaps in what has shipped

- **`--vault` is not a real flag.** `resolve_vault()` accepts an explicit path
  but no command passes one; only `ARTMIND_VAULT` and the walk-up work.
- **`load_env()` returns `dict(os.environ)`**, not one file's values — it had to,
  or a vault config holding only the graph would hide the machine's models from
  the ~33 call sites that read them from its return value.
- **A command needing the vault should call `resolve_vault()` fresh**, not read
  `paths.ARTMIND_VAULT_DIR`: that module global is frozen at first import and
  cannot see a `chdir` within one process.

## Open

- **Query-only consumers** (the canvas backend) need `--vault` or `ARTMIND_VAULT`.
- **Whether `data/kg/<doc>/chunks/chunk_NNN.json` is redundant** with the
  aggregated `chunks.json`. If it is, committing both doubles the largest
  committed artifact for nothing.
