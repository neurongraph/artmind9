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
  no configuration, scaffolded by `artmind init` so it's there from the start.
  (Any unmapped path is equally safe — see the manifest — but `_Inbox/` is the
  conventional one.)
- **If a conversion comes out wrong**, you do not fix it in `.artmind/`. Copy the
  markdown out into the vault as an ordinary note, move the binary to `_Inbox/`,
  and ingest the note. That is the supported workflow, and it needs no machinery
  because the note is then just a note.

## Layout

```
~/MyVault/                              ← Obsidian vault, git repo, artmind vault
├── .git/  .obsidian/
├── .claude/skills/                     ← artmind's (symlinked, ignored) + yours
├── .opencode/agent/                    ← ACP persona modes (symlinked, ignored)
├── _Inbox/                             ← drafts; never ingested
├── _external_docs/                     ← copies of sources from outside the vault
├── area1/  notes/  …                   ← your documents, your binaries
└── .artmind/                           ← ARTMIND-OWNED. Do not edit.
    ├── vault.yaml                      ← the ingest manifest + vault_id; COMMITTED
    ├── config.env                      ← this vault's graph; NOT committed
    ├── same_as.yaml                    ← curation; COMMITTED
    ├── domains/                        ← schemas, meta-schema, table_mappings/; COMMITTED
    ├── logs/  state.json  serve.json   ← machine-local; NOT committed
    └── data/
        ├── documents/markdowns/
        │   ├── a_deck.md               ← converted; COMMITTED
        │   ├── a_deck_artifacts/       ← extracted images + their descriptions
        │   └── a_deck_chunks/          ← chunk_001.md, chunks_meta.json
        ├── kg/<domain>/<doc>/          ← extraction output; COMMITTED
        ├── kg/<domain>/update__<session>__<draft>/  ← an `artmind update` fact; COMMITTED
        ├── curation/<kind>/<id>.json   ← curation records (conflicts, supersessions,
        │                                  lifecycle, syntheses); COMMITTED
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
| `.artmind/logs/`, `state.json`, `serve.json`, `worker.pid` | machine-local runtime state, meaningless on another machine (`state.json` holds this machine's structured-store sync bookmark) |
| `*.zip`, `*.tgz`, `*.tar.gz` anywhere | snapshots. Large, opaque, and already a complete copy of what git is versioning |
| embeddings inside committed KG staging | see "Embeddings" below |
| `.artmind/data/kg/*/table__*/` | regenerated by `vault sync` from the table's CSV, its table mapping and its domain schema, like parquet; committing them made sync create commits |
| `*.artmind-tmp`, `*.artmind-old` (folders and files) under `.artmind/data/` | scratch from an atomic write — a staging folder's swap, a curation record's temp file, a note rewrite's temp file; a crash can leave one, the next write removes it. This is `.gitignore` block **v4**: v3 ignored scratch folders only. A vault on v3 shows `artmind block outdated (v3; this artmind writes v4)` in `vault doctor`, which prints the fix (`artmind init`, which rewrites only artmind's block); `vault doctor` also prints the `git rm --cached` for a scratch file that already got committed |
| `.artmind/.claude-sdk-auth/` | the chat/admin UI agent's config dir: auth and full conversation transcripts. Private, and per machine |
| `.DS_Store`, `Thumbs.db`, `.obsidian/workspace.json`, `.obsidian/workspace-mobile.json` | not artmind's, but a synced vault needs them out: OS litter, and Obsidian's per-device pane layout, which changes on every click and would conflict between machines. The rest of `.obsidian/` stays committed |

The artmind sections of `.gitignore` and `.gitattributes` are versioned
blocks. `artmind init` replaces an older block in place and never touches
your own rules. `.gitattributes` marks `.artmind/data/**` as `merge=binary`:
two extraction runs of one document conflict as whole files instead of
line-merging into a mix. Structured-store metadata is one
`<domain>/<table>.meta.json` per table, beside its CSV. A legacy
`manifest.json` is still read, and is split up on the next export.

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

`artmind init` also appends a `vault_id: "<uuid>"` line (with a comment) the
first time it runs in a vault — and in a vault initialised before it existed,
without touching anything else in the file. It names this vault's sync
bookmark in the graph (see "Sync bookmarks" below), so two vaults sharing one
AuraDB never share a bookmark. Commit it; every clone must carry the same
value. If two machines each run `init` before either has pulled the other's
`vault_id`, the merge conflicts on that line — keep either one.

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

- **writer-agnostic** — it doesn't matter who committed: Obsidian Git or a manual `git commit` look the same
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

### What an ingest would do: `ingest pending`

Until the triggers above exist, the question "what do I need to ingest?" has a
read-only answer. `artmind ingest pending` walks the vault as `ingest async .`
would (mapped folders only, no `_Inbox`, no dot-folders) and applies the
ingest's own decisions to every file -- the same functions, not a copy of them:

| Kind | Listed when | Decided by |
|---|---|---|
| note (`.md`) | its body differs from the one its last extraction staged in `document.json`, or its `_domain` changed | `ingest.plan_vault_native` (`decide_version`) |
| binary (pdf/pptx/docx, images) | its bytes differ from the ones last converted | `ingest.plan_binary` (`source_sha256`) |
| table (csv/xlsx) | its bytes differ from the ones registered | `structured.pipeline.is_unchanged` |

Each entry is a vault-relative `path` with a `reason`, `new` or `changed`. A
note that only moved is not listed: re-ingesting it extracts nothing. Mapped
files the ingest would refuse as they stand (an `_artmind_id` collision, a
`_domain` that is not a folder name) are listed under `errors`. `artmind
ingest async --pending` submits exactly the listed files as one job (and
prints `{"job_id": null, "file_count": 0}` when there are none), so a file
is ingested only when it changed -- the cost rule above, kept by hand.

Tables have a second step. `artmind ingest table2graph --pending` lists every
registered table a mapping matches whose projection on this machine is
`missing`, `table_refreshed` (the registry's `version` moved past the one the
projection recorded), `mapping_changed` (the projection recorded another
mapping file, or another `table_mapping_sha256` -- a digest of the parsed
mapping, so a comment edit does not count), or `ambiguous` (two mappings
match). The projection it compares with is the staged
`kg/<domain>/table__<table>/document.json` that every `table2graph` run, and
every `vault sync` that re-projects the table, rewrites.

The Obsidian plugin (`obsidian/artmind-obsidian/`,
`docs/superpowers/specs/2026-09-30-obsidian-plugin-design.md`) runs these,
`vault status`/`sync`/`resolve`/`doctor` and `artmind --version`, always
with `--compact`. The JSON it reads is pinned by fixtures under
`obsidian/artmind-obsidian/test/fixtures/`, regenerated from the real CLI by
`test/test_plugin_fixtures.py` -- change a command's output and that test
fails until the fixtures (and the plugin) follow.

### Git: Obsidian Git owns transport

artmind never commits, pulls or pushes the vault. It writes files — a
note's identity (once; see "Frontmatter: identity only" below), KG staging
under `.artmind/data/kg/`, structured text under
`.artmind/data/structured_text/` — and the Obsidian Git plugin commits and
syncs them like any other change. One writer per repo means no `index.lock`
races and no rejected pushes. There are two exceptions. `artmind vault
resolve` stages its pick for a conflicted generated file during a merge
(below) and still never commits. `artmind vault new` makes the vault's
first commit and its first push to the new GitHub repo, once, at creation
(before Obsidian has opened it, so there is no second writer yet). Every
later commit and push belongs to Obsidian Git.

Recommended plugin settings (names vary by plugin version): sync method
**merge** (not rebase — commit shas are provenance and the sync bookmarks),
auto commit-and-sync every 5–10 minutes, pull on startup. Mobile uses the same
settings and never runs artmind. Do not also sync the vault folder with
iCloud / Obsidian Sync / Dropbox — a second sync layer racing Obsidian Git
over the same working tree risks corrupting it.

After the plugin pulls changes made on another machine, run
`artmind vault sync` to apply them to this machine's Neo4j and structured
store. It applies committed content only — every input, table mappings and
domain schemas included, is read from `HEAD`, never the working tree — and it
never commits. What it replays:

- a KG document folder in which **any** file changed, so a folder that an
  auto-commit caught in two halves still converges; a removed folder is
  retracted from the graph -- unless its document id is still carried by a
  folder at `HEAD`, in the same domain (a renamed note) or another (a domain
  change: `ingest sync --setDomain` writes the folder under the new domain and
  removes the old one). That is a move, not a retraction, and retracting the
  old folder's id would demote the document just replayed under the new one;
- a structured table whose CSV or `.meta.json` changed: restored into DuckDB,
  and re-projected into the graph when a table mapping names it. A table no
  mapping names stays a structured-store table only. A table whose CSV is
  unchanged and whose `.meta.json` changed only in grain, bridge columns or
  column mappings is restored into the registry alone (see "Curation that
  travels");
- every table a changed, added or removed **table mapping** matches, and the
  mapped tables of a domain whose **schema** changed (and of its dotted child
  domains). A table that a removed or narrowed mapping no longer covers is
  retracted from the graph;
- an **`artmind update`** — each confirmed draft is a staging folder,
  `kg/<domain>/update__<session>__<draft>/`, replayed like any document folder
  (as a `UserChat`, not a `Document`); a deleted one is retracted;
- every **same-as group** added, removed or changed in `.artmind/same_as.yaml`:
  exactly its members are rebuilt, with the groups as committed at `HEAD`;
- every **curation record** added, changed or deleted under
  `.artmind/data/curation/` (see "Curation that travels" below).

A table's identity is `(domain, table_name)`, not a SQLite id: `.meta.json`
carries no machine-local id at all — neither the table's own nor any child
row's — so a restore matches tables by domain and name, and two machines that
each register a new table never collide on an id that was never shared truth
(spec `2026-09-26-vault-git-transport-design.md` §14 A3).

`vault sync` refuses while a merge, rebase or cherry-pick is in progress,
while a file under `.artmind/` has unresolved conflicts (a conflicted note of
yours does not block it; `artmind vault resolve` settles those under
`.artmind/data/`), while the ingest worker is running, or while a
store's bookmark is not in `HEAD`'s history (pull first). A machine
with no Obsidian that must still follow the remote (e.g. a
query-only host) uses a plain `git pull --no-rebase` on a launchd/cron timer
instead — documented, not built into artmind. Design:
`docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`.

#### Curation that travels

The graph must be rebuildable from the vault alone: a machine with its own
Neo4j never sees a write that exists only in another machine's graph. So
every curation write is also a vault file:

| Curation | Vault file | Written by |
|---|---|---|
| a fact added with `artmind update` | `kg/<domain>/update__<session>__<draft>/` (a staging folder) | `update confirm`; `update retract` deletes it |
| a detected conflict, and its resolution | `curation/conflicts/<id>.json` | `ingest detect-conflicts`, `ingest resolve-conflict` |
| a supersession | `curation/supersessions/<id>.json` | `ingest supersede`, `ingest detect-supersession`, the adjudicator |
| a retired document | `curation/lifecycle/<id>.json` (exists while retired) | `docs retire`; `docs restore` deletes it |
| an LLM-synthesised description | `curation/syntheses/<entity id>.json` | `projection synthesize` |
| a table's grain, bridge columns, column mappings | the table's `.meta.json` | `db grain`, `db mappings`, `db bridge`, `db propose` |
| a same-as group | `.artmind/same_as.yaml` | `sameas approve`, or you |

`docs archive` bundles a document (its staging folder, the note, the original
binary) into `ARTMIND_ARCHIVE_DIR` and then removes the note, the original and
the graph/registry rows. It **copies** the `kg/<domain>/<doc>/` staging folder
into the bundle but does **not** remove it: the folder stays in the vault, so
it is still committed and synced until you delete it yourself (or `git rm` it).
`docs restore-from-archive` rewrites it from the bundle.

Curation records are **one file per record**, never one shared file: every
path under `.artmind/data/` merges as a whole file, so a shared file would
make any two machines' curation a git conflict. A conflict's id is
deterministic, so two machines detecting the same conflict name the same
file; re-detecting a conflict whose file exists never rewrites it (a
resolved conflict is not reopened). If two machines both detect one before
either pulls, git reports a conflict on that one file, and `artmind vault
resolve` settles it: a decision (resolved, dismissed) beats open, then the
later change wins — the same pick on both machines.

A **retirement sticks**: re-ingesting a retired document, or `vault sync`
replaying it, commits the new version and leaves it retired — only `docs
restore` brings it back. A supersession sets the older document's `valid_to`
and `superseded_by` from **all** the supersessions pointing at it, so deleting
one supersession's file restores what the others say (nothing, when none is
left); it does not un-retire the older document. A synthesis carries no
embedding: `vault sync` re-embeds the entity locally. A table whose rows did
not change but whose classifications did is restored into the registry alone —
no parquet rewrite, no re-projection — and the graph's table catalogue is
re-projected for its domain. That projection is not best-effort: if it fails
the sync stops before any bookmark advances and the same range is retried.

A sync **re-applies** every retirement, supersession and synthesis record in
the range (their applies are idempotent), so their entities always rejoin the
rebuild, even when a previous run applied the record and then failed before
its rebuild. Only a conflict record is skipped when the graph already carries
its fingerprint (a machine sharing this graph already applied it).

Two limits. The catalogue follows only in a run that includes both the graph
and the structured store: `vault sync --store graph` does not restore a
curation-only table change, so run `artmind db catalogue --domain <d>` there.
And syntheses made before records existed have no file, so they travel only
after `artmind projection synthesize --domain <d> --force` re-runs them.

Same-as **proposals** (`sameas propose`, `ingest refine-graph`, and the
adjudicator's "same entity" verdicts) stay on the machine that made them: they
are a review queue, and an approved group travels as `same_as.yaml`.

#### Sync bookmarks, fingerprints, and the two topologies

A bookmark means "this store reflects the vault's commits up to X", and each
store has its own:

| Store | Bookmark | Why there |
|---|---|---|
| Graph | a `(:ArtmindSyncState {vault_id, last_applied_commit, applied_at})` node in Neo4j | a shared AuraDB carries one bookmark that every machine using it sees; a separate local Neo4j carries its own — same code for both |
| Structured store | `last_structured_commit` in `.artmind/state.json` | DuckDB is always a local file |

Each store applies its own range (`bookmark..HEAD`), so a graph ahead of the
structured store, or the reverse, is normal. `vault sync --store graph` or
`--store structured` runs one store alone (a structured-only run never
connects to Neo4j); `--bootstrapEmpty`/`--bootstrapSynced` apply to every
store in the run. A vault that synced before bookmarks existed has a single
`last_synced_commit` in `state.json`: it seeds any store with no bookmark of
its own and is removed once both stores have one. Where the graph already has
a bookmark (another machine sharing it wrote one), the graph's own bookmark
wins.

Every graph write of a staging folder — ingest, `table2graph`,
`write-to-graph`, `pull-kg`, archive restore, dashboard import, `vault sync`
itself — records a **fingerprint** on the `Document`: sha256 over the raw
bytes of `document.json`, `chunks.json`, `observations.json` and
`relationships.json`. `vault sync` computes the same digest from the
committed blobs and skips a folder whose fingerprint the graph already
carries, with one Cypher read for the whole run.

This is what makes the same code correct in both topologies — a graph shared
by every machine on one AuraDB, or a separate local Neo4j per machine:

| | Shared AuraDB | Separate local Neo4j per machine |
|---|---|---|
| Machine B's apply, graph side | the ingesting machine already wrote it: fingerprints match, near no-op, the bookmark advances | full replay of `bookmark..HEAD` |
| Machine B's apply, structured side | regenerate changed tables into B's DuckDB | same |
| First `vault sync` on a new machine | the graph bookmark is already there; `vault sync --store structured --bootstrapEmpty` fills DuckDB, then plain `vault sync` | `vault sync --bootstrapEmpty` |

artmind never writes to this vault's git — no commit, no ref — apart from
`vault resolve` staging a conflicted generated file's chosen side. Obsidian
Git merges (never rebases), so a bookmarked commit stays reachable on its
own; if history is ever rewritten anyway, the next section covers what
`vault sync` does about it.

A bookmark that is not an ancestor of `HEAD` is refused, never diffed:
diffing from it would read the other machine's new documents as removals.
Pulling (Obsidian Git merges) usually fixes this — the bookmark's commit
was there all along, just not yet in this clone's history. If it doesn't
(the commit was rewritten away, not merely unpulled), the refusal also
names the way out: `vault sync --store <name> --bootstrapSynced` if that
store is already known-current, or `--store <name> --bootstrapEmpty` to
replay everything committed.

`artmind vault status` shows HEAD, both bookmarks, how many documents and
tables each store is behind (after the fingerprint check) — and, for the
graph, how many curation records and same-as groups (every retirement,
supersession and synthesis record sync would re-apply counts, including the
retirements a replay re-applies; a conflict counts only if the graph lacks its
fingerprint) — a merge in progress,
and unresolved conflicts under `.artmind/`; `--compact` gives the same as
JSON. After a successful `artmind query ...` in a vault this machine
has synced, a line such as

    artmind: graph is 3 docs / 1 tables behind the vault — run `artmind vault sync`

goes to **stderr** — never stdout, so `--compact` JSON stays clean. It is
advisory: queries never apply anything. It costs a `git rev-parse` and one
Cypher read when nothing is pending (capped at 2 s to connect), and nothing
at all on a machine that has never run `vault sync`. `ARTMIND_NO_STALENESS_CHECK=1`
turns it off. Through the `serve` daemon the line arrives the same way, once
the daemon has been restarted on this code.

`artmind vault doctor` checks, without changing anything, that the vault is
safe for Obsidian Git to auto-commit and merge. It checks that the artmind
`.gitignore`/`.gitattributes` blocks are current, that no newly ignored path
is still tracked, that `pull.rebase` is off, Obsidian Git's stored settings,
a leftover `ARTMIND_VAULT_GIT_PUSH`, and whether iCloud, Dropbox or Obsidian
Sync also syncs the folder. Each problem comes with the exact command to fix
it, for example:

```bash
git rm -r --cached -- .artmind/data/kg/banking/table__accounts
```

artmind never runs these itself. A vault initialised by an older artmind
shows its `.gitignore` block as outdated, naming both versions (for example
`v3; this artmind writes v4`); `artmind init` upgrades it in place.

#### Merge conflicts in artmind's files: `vault resolve`

Everything under `.artmind/data/` merges as a whole file, so two machines
that regenerated the same file stop the merge on a conflict. While Obsidian
Git's merge is stopped, run:

```bash
artmind vault resolve --dryRun   # which side each folder, table and record takes, and why
artmind vault resolve            # stage those sides; then let Obsidian Git commit the merge
```

It takes **one whole side** per unit, never a mix:

| Unit | Side taken |
|---|---|
| a KG document folder (`kg/<domain>/<note>/`, every file in it) | the one extracted from the merged note's body; if the merge deleted the note, the side without the folder; otherwise the greater fingerprint |
| a chunk cache (`documents/markdowns/<note>_chunks/`, `chunk_NNN.md` + `chunks_meta.json`) | the side whose `_body_sha256` stamp is the merged note's body hash, so it follows the note like the KG folder; no stamp or no single matching note: the greater digest of the folder, whole. `extract-kg` re-checks the cache against the note and re-splits a stale one, so a wrong pick costs a re-split |
| a structured table (`<table>.csv` + `.meta.json`) | the side that kept it, then the later refresh, then the greater digest |
| a curation record (`curation/<kind>/<id>.json`) | the side that kept it, then — a conflict: a decision beats open, then the later change; a supersession: a manual assertion beats a detected one; a synthesis: the later one; a lifecycle record: no rule, its content is fixed by its id — then the greater fingerprint |
| any other file under `.artmind/data/` | the side that kept it, then the greater content hash |

Every rule reads content, never which side is "ours", so both machines pick
the same bytes. The output lists each unit under `resolved` (with its `side`
and `rule`), `pending` or `reported`; `remaining` counts the last two. A path with a symlinked parent directory inside the vault is `reported` and its
unit skipped (deleting or staging through a symlink writes outside the vault). Your
notes, `same_as.yaml`, schemas and table mappings are `reported` and never
touched: resolve those in Obsidian. A folder whose note is still conflicted is
`pending`; resolve the note, then run `vault resolve` again (a note you have
resolved in Obsidian but not yet staged already counts as resolved).

It works only while a merge is stopped: with no merge in progress, or during a
rebase, cherry-pick or octopus merge, it refuses and writes nothing. It also
refuses (apart from `--dryRun`) while the ingest worker is running. This is the
one place artmind writes to git — `git checkout` of the chosen side and `git
add`, only under `.artmind/data/`, never a commit. A side resolved away costs
a re-extraction at most, never knowledge: the note is yours and always merges
by hand.

#### Frontmatter: identity only

artmind writes two fields into a note — `_artmind_id` and `_domain` — once,
at its first ingest, and again only if you change its domain. It edits those
lines alone: the body, your own keys and their order, comments and line
endings are untouched. Everything that changes per ingest (the version, the
body hash, the source commit, when and from where it was ingested) lives in
the note's staging folder, `.artmind/data/kg/<domain>/<note>/document.json`,
written by the extraction. Paths recorded there (and in an archive bundle's
`manifest.json`, and a table's `.meta.json`) are **vault-relative**, so a
committed tree is identical on every machine and leaks no local layout; older,
absolute values are still read. A `git mv` of an unchanged note updates the
path in that `document.json` *and* on the graph's `:Document` (a machine that
replays gets it from the folder). So re-ingesting a note writes nothing to it,
Obsidian Git has nothing of artmind's to commit, and two machines never
conflict on artmind's lines in your notes.

A vault ingested by older artmind still carries those fields in every note;
they keep working. To move them out, once:

```bash
# pause Obsidian Git's auto commit-and-sync first (the command reminds you)
artmind vault migrate-frontmatter --dryRun
artmind vault migrate-frontmatter
# commit the result as one change, then turn auto commit-and-sync back on
```

It copies each field into the note's `document.json` where that key is
missing (a newer value there always wins), then rewrites the note without
them; a converted binary's markdown under `.artmind/data/documents/markdowns/`
is migrated the same way. It is idempotent — re-run it after an interruption —
and refuses (a dry run too) during a merge/rebase or while the ingest worker
is running: stop the worker first (`artmind ingest job-status` shows it). The
output lists `migrated`, `skipped` (with the reason) and `version_mismatch`.
A note never extracted has no staging folder; it keeps its fields and is
listed under `skipped`, as is any note it cannot rewrite safely — frontmatter
that does not parse or sits behind a BOM, a copy sharing another note's
`_artmind_id`, a symlink out of the vault; those are left byte-for-byte as
they were and the run carries on. Each migrated folder's fingerprint changes,
so every machine's next `vault sync` replays those documents once (no LLM
cost).

The loop terminates by construction: artmind stamps a note's identity once →
Obsidian Git commits it → the cursor sees a change → but
`compute_content_sha256` hashes the **body only**, so the version decision is
`metadata_only`, minting no observations at no LLM cost, and writing nothing.

## Skills, agent modes, and schemas

They go opposite ways, and the line is: **skills and agent modes are code,
schemas are content.**

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

### ACP agent modes — machine-level, symlinked in, same as skills

The chat/admin UIs' `acp` backend (opencode driving the `artmind`/`artmind-admin`
persona) resolves its custom agents from `.opencode/agent/*.md`, the same way:
relative to the `cwd` the ACP session is opened with, which must be the vault
root for the same reason skills must.

Canonical copy: `artmind/opencode/agent/*.md` in the package. `artmind init`
symlinks each `.md` file into `<vault>/.opencode/agent/`, gitignored
(`.opencode/agent/artmind*.md`) — identical treatment to skills, including the
copy fallback where symlinks are unavailable.

This used to be genuinely different from skills: `.opencode/` lived only at
the machine home (`~/.artmind/.opencode/`), on the theory that ACP mode
selection was somehow independent of the vault. It is not — an ACP session's
`cwd` governs both skill discovery and agent-mode discovery identically, so
whichever directory that `cwd` points at needs both. `ARTMIND_AGENT_CWD`
(`paths.py`) is that directory: the vault root inside a vault, the machine
home otherwise — never `ARTMIND_HOME` unconditionally, which is one level
below the vault root (`<vault>/.artmind/`) and holds neither.

### Schemas — vault-level, committed, yours

The repo keeps the shipped library; the vault keeps what it actually uses, in
`.artmind/domains/schemas/`, committed.

**`init` must stop overwriting them.** Overwrite-always was safe when one run
folder was reseeded from the package; it would now clobber hand-authored vault
schemas. But never overwriting recreates the problem CLAUDE.md warns about — a
prompt fix that never reaches the vault looks like a model failure. So:

- seeded schemas carry provenance (`_source: package` plus a hash)
- `init` seeds only the **starter** set, and only what is missing — and so does
  `artmind setup` run inside a vault (both go through `setup._seed_vault_domains`;
  `setup` used to overwrite every same-named package schema and `meta.yaml`).
  Only a plain run folder outside any vault still has its schemas refreshed
  from the package on every `setup`
- `artmind domains update` refreshes package-derived schemas you have not
  modified, and **reports** the ones that diverged for you to merge
- `artmind domains add` stays, for vault-local schemas

**Table mappings** (`ingest table2graph`) live beside them in
`.artmind/domains/table_mappings/`, committed. They are never seeded or
overwritten by `init` — a mapping names a specific table, so it is always
vault data — and `snapshot` curation archives them with the schemas. An edited
mapping or schema reaches another machine's graph through `artmind vault sync`
once Obsidian Git has committed and pulled it.

## `artmind init`

Changes meaning: from "scaffold `~/.artmind`" to **"make this directory a
vault"**, the way `git init` makes one a repo.

1. `git init` if not already a repo
2. create `.artmind/`, write `.artmind/.gitignore`
3. seed starter schemas into `.artmind/domains/`
4. symlink skills into `.claude/skills/`
5. symlink ACP agent-mode personas into `.opencode/agent/`
6. write `.artmind/config.env` (placeholders, or real answers when called by
   `vault new`) and a starter `vault.yaml`
7. install and enable the artmind Obsidian plugin, if `.obsidian/` exists
8. print next steps

`just dev-install` must **stop running `artmind init`** — installing the CLI and
creating a vault are separate acts, and at install time there is no vault.

### `artmind vault new` — creating or joining a vault

`init` never prompts and never provisions anything outside the folder: it is
the idempotent "converge this folder into a vault" step, re-run after every
upgrade. Creating a vault from nothing is `artmind vault new NAME`
(spec `docs/superpowers/specs/2026-10-02-vault-new-design.md`):

- a neo4j-manager instance `NAME` (`neo4j-manager create NAME --json --wait`),
  whose bolt URL and password go straight into `.artmind/config.env`;
- the folder (`~/artmind_vaults/NAME` by default) with `git init` and
  `.obsidian/`, then `scaffold_vault` — the same code `init` runs;
- the Obsidian community plugins in `artmind/obsidian/community_plugins.yaml`;
- `artmind setup`, a bootstrap commit, a **private** GitHub repo
  `<active gh account>/NAME` (`gh repo create`) and the push;
- the first bookmark: `vault sync --bootstrapSynced`.

`vault new NAME --join` is the second-machine variant: it clones
`<owner>/NAME`, creates this machine's own neo4j-manager instance, and
bookmarks with `--bootstrapEmpty`, rebuilding the graph from the commits.

All pre-flight checks run before anything is created: the GitHub repo, the
folder and the neo4j instance must not exist yet (the repo must exist for
`--join`), and every clash is reported at once. Progress is recorded in
`~/.artmind/vault_new/NAME.json`, so a failed run resumes when re-run.
`--localOnly` skips GitHub; `--neo4jUri/--neo4jUser/--neo4jPassword` use an
existing Neo4j (e.g. AuraDB) instead of neo4j-manager.

## Machine-level config — the only global state

Secrets cannot live in the vault: it is a repo you may push. One file stays
global — `~/.artmind/config.env`. The line is **secrets and models belong to the
machine; knowledge belongs to the vault.** Loading is most-specific-first, so the
vault's `config.env` overrides the machine's, and real environment variables beat
both.

| Scope | Variables |
|---|---|
| **Machine** — `~/.artmind/config.env` | `ARTMIND_USER`, `ARTMIND_KG_LLM_*`, `ARTMIND_IMAGE_MODEL`, `ARTMIND_OLLAMA_TIMEOUT`, `ARTMIND_OPENROUTER_API_KEY`, `ANTHROPIC_*`, `ARTMIND_KG_EMBEDDINGS_*`, `ARTMIND_KG_EMBEDDING_DIMENSIONS`, `ARTMIND_SDK_*`, `ARTMIND_ACP_MODEL`, `ARTMIND_KG_CHUNK_SIZE`, `ARTMIND_INGEST_MAX_WORKERS` |
| **Vault** — `<vault>/.artmind/config.env` | `ARTMIND_KG_NEO4J_*` |
| **Runtime** | `ARTMIND_NO_PROXY`, `ARTMIND_NO_STALENESS_CHECK` (silence the `query` staleness line), `--vault`, `ARTMIND_IMPORT_MAX_BYTES` (cap on `POST /api/artifacts/import`'s uploaded zip size, admin-ui only — not `vault sync` or snapshot import/restore; default 512 MiB) |

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
`scaffold_run_folder` (`artmind setup`), `scripts/bootstrap.sh`, and
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

A graph snapshot (`session close`, and the graph component of `snapshot
create`) carries the graph's `:ArtmindSyncState` bookmarks, so `session
initiate` puts back exactly the bookmark the graph had when it was saved —
never one newer than the restored content. A snapshot taken before bookmarks
existed carries none; the next `vault sync` then asks for `--bootstrapSynced`
or `--bootstrapEmpty`, and `session initiate` says so.

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
