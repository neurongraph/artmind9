# artmind for Obsidian: a state-driven plugin for everyday vault use

**Status:** Design — approved in brainstorming
**Date:** 2026-09-30
**Owner:** Surjit Das
**Builds on:** [2026-09-26-vault-git-transport-design.md](./2026-09-26-vault-git-transport-design.md)
(Obsidian Git owns git; artmind reads it and applies it) and
[docs/USER_GUIDE_TWO_LAPTOPS.md](../../USER_GUIDE_TWO_LAPTOPS.md) (the everyday workflow
this plugin turns into clicks).

## 1. Purpose

The two-laptop workflow has one habit: *pull → `vault sync` → work → push*. It also has a
handful of commands: `ingest sync`/`async`, `table2graph`, `vault resolve`, `vault status`
and `vault doctor`. Today all of them are run from a terminal, beside Obsidian. The plugin
brings them into Obsidian.

It is built around **state, not commands**. It always shows where the vault stands, and it
offers the one action that state calls for. Most days that's a single click, not a choice
among six buttons.

Four moments to take the friction out of:
1. syncing after a pull;
2. ingesting new files;
3. knowing the state;
4. moving a table into the graph.

## 2. Decisions

| # | Decision |
|---|---|
| P1 | **A native Obsidian plugin that runs the `artmind` CLI** (`--compact` JSON). There's no HTTP service and no daemon dependency, and it behaves exactly like the terminal. Rejected alternatives: talking to `admin-ui`/`serve` over HTTP, which needs new endpoints plus a running, non-stale daemon; and Shell-commands buttons, which can't show state. |
| P2 | **After a pull, the plugin nudges; it never syncs on its own.** It shows an amber status bar and a *"[Sync now]"* notice. Automatic apply remains Plan E (issue #26). |
| P3 | **Ingest runs as a background job** (`ingest async`, artmind's worker). It survives Obsidian quitting, and the plugin shows its progress. |
| P4 | **Ingest starts from "Ingest what changed"** (exactly the new or changed mapped files) **and from a notice when a binary or table lands in a mapped folder.** Note edits never prompt; they only count. |
| P5 | **The plugin never writes to git.** Pull, commit-and-sync and commit are Obsidian Git's commands, which the plugin triggers. The plugin itself only reads git (`rev-parse`, `status`, commits not yet pushed). |
| P6 | **Offer to commit-and-sync after artmind writes: on by default** (a setting). |
| P7 | **Nothing writes to the graph without a click.** Unprompted, the plugin only reads. |
| P8 | **Desktop only** (`isDesktopOnly: true`). Mobile never runs artmind (vault spec D3). |
| P9 | **The plugin lives in this repo** (`obsidian/artmind-obsidian/`), so a CLI JSON change and the plugin code reading it land in the same commit. |

## 3. UI surfaces

### 3.1 Status bar item

It shows one state at a time, and clicking it performs that state's action:

| State | Shows | Click does |
|---|---|---|
| Merge conflict under `.artmind/` | `⚠ resolve artmind conflicts` (red) | opens the resolve preview (§5.2) |
| Error (artmind missing or too old, Neo4j unreachable) | `⚠ artmind` (red) | opens the side panel at the error and its fix |
| Ingest job running | `◌ ingesting 2/5` (spinner) | opens the side panel at the job |
| Stores behind HEAD | `◉ 3 docs · 1 table behind` (amber) | runs Sync (§4.2) |
| Tables waiting for the graph | `◉ 1 table → graph` (blue) | opens the `table2graph` review (§5.1) |
| Files to ingest | `◉ 2 to ingest` (blue) | runs Ingest what changed (§4.4) |
| In sync | `◉ artmind` (grey) | opens the side panel |

**Priority, highest first:** conflict > error > job running > behind > table → graph > to
ingest > in sync. The side panel shows every state that applies.

**Secondary indicators**, shown after the main state:
- `↑ 3 to push`: local commits not yet pushed;
- `✎ artmind changes to share`: uncommitted changes under `.artmind/` (§4.5).

### 3.2 Side panel (`ArtmindView`, right sidebar)

Top to bottom, most urgent first:

- **Header:** the status bar's state and its detail, the secondary indicators, any other
  states that apply, and **one primary button** for whatever the top state asks for: Sync
  when behind, Ingest what changed when files are waiting, Resolve during a conflict,
  Review `<table>` when tables are waiting. Below it, a small toolbar with Sync · Ingest ·
  Resolve · Doctor (plus Commit-and-sync when there are artmind changes to share). Each is
  disabled whenever it can't run, with the reason as its tooltip. For example: Sync while an
  ingest job runs or a merge is in progress; Resolve when there's no merge. A blocked
  primary button shows its reason inline.
- **Callouts**, only when they apply: problems (artmind missing or too old, Neo4j
  unreachable, failed background reads) and the first-sync bootstrap step.
- **Ingest job:** the running job, or the last job once it finishes (its `job-results`
  are fetched when the poll sees it end). It shows:
  - a segmented bar of file states (done · running · queued · skipped · failed), with a
    legend;
  - totals of entities and relationships;
  - one row per file. An extracted file shows two compact bars, entities (E) and
    relationships (R), each scaled to the largest file in the job. A file in `extract_kg`
    shows its chunk progress, with per-step counts in the tooltip. A failed file shows its
    error.
  - **Retry:** a finished job with failed files shows **Retry failed**; a stalled job
    (`processing`, but its worker is gone, X7) shows **Retry job**. Both run
    `ingest retry-job`, then follow the job again. A stalled job is also its own state
    (`⚠ ingest stalled n/m`, amber, after errors): its poll stops, a notice offers Retry once,
    and it blocks nothing a running job would.
  - The counts are `entity_count`/`relationship_count` on `ingestion_job_files` (§6):
    distinct entities and relationships the file's extraction staged. They are null for a
    file never extracted (skipped, failed, a no-op or metadata-only re-ingest).

Then collapsible sections. Each header carries a one-line badge, so the panel reads with
everything collapsed. The panel remembers which sections are open across re-renders, and
opening the panel at a section expands it. Each header has a chevron twisty, turning down
when the section is open.

- **Sync** (collapsed by default; badge `graph ✓ · structured ⚠`): HEAD, the graph and
  structured bookmarks, pending counts (documents, tables, curation records, same-as
  groups), files to ingest, tables waiting for the graph, commits waiting to push, and the
  last sync's time and result.
- **Tables for the graph:** the tables waiting, each opening the review (§5.1).
- **Doctor** (after a run): each finding with its fix command and a **[Copy]** button. The
  plugin never runs a fix, because the doctor's fixes are `git` commands (P5).
- **Activity** (collapsed by default): the last 20 runs (command, time, one-line summary),
  each expanding to its raw output.

### 3.3 Notices

| When | Text | Buttons |
|---|---|---|
| A pull left a store behind | *"Pulled from the other laptop — graph 3 docs behind."* | [Sync now] |
| A binary or table lands in a mapped folder | *"2 new files in Team_performance_management."* | [Ingest now] |
| An ingest job finishes | *"Ingested 4, 1 failed."* | [Details], plus [Commit-and-sync] under P6 |
| Sync finishes | *"Synced: 3 docs, 1 table, 2 curation records."* | — |
| `table2graph` finishes | *"hercules_output_20260921 projected: 412 entities."* | [Commit-and-sync] (P6) |
| `vault resolve` finishes | *"Artmind files resolved."* | [Complete the merge] |

### 3.3a Ribbon icon and admin console

- **Ribbon:** one `brain-circuit` icon in the left ribbon. Click opens the side panel.
  Right-click offers Open side panel · Sync · Ingest what changed · Open admin console ·
  Run doctor, each disabled with the reason the toolbar gives. A dot in the top state's tone
  shows whenever the vault needs something (none when in sync).
- **Admin console:** **Admin ↗** in the toolbar, the ribbon menu, the command palette, the
  table review's "Ask admin-ui", and "Chunks and artifacts in the admin console" on the
  job card (`/dashboard`). Each one:
  1. probes `GET {adminUiUrl}/api/health` (X8) through `requestUrl`;
  2. opens it if it serves this vault;
  3. refuses another vault's console, or a port held by something else, and says which;
  4. otherwise starts `artmind admin-ui --host H --port P` detached from the vault root,
     logging to `.artmind/logs/admin-ui.log`, waits up to 20 s for the probe, then opens.
     A start that fails shows the log's tail.
- **Lifecycle:** a started console outlives Obsidian, like one started in a terminal, so
  its chat sessions survive. Its pid is saved in the plugin's settings. **Stop admin
  console** stops it only when `/api/health` still reports that pid, never one started
  elsewhere.

### 3.4 Command palette

Every action is also a command: *artmind: Sync*, *Ingest what changed*, *Show status*,
*Resolve artmind conflicts*, *Run doctor*, *Review tables for graph*, *Open side panel*.

## 4. Flows

### 4.1 Detecting a pull

- Every 15 s, the plugin runs `git rev-parse HEAD` (read-only, milliseconds).
- When HEAD changes, it waits for one more unchanged reading before acting, so a merge still
  in progress isn't read half-done.
- It then runs `vault status --compact`. If a store is behind, it shows the amber state and
  the *"Pulled…"* notice.
- Status is also refreshed after every artmind action the plugin runs, and when Obsidian
  regains focus.

### 4.2 Sync

- **Pull first.** If the last pull (as seen by the HEAD poll) is older than 5 minutes, the
  plugin first offers *"Pull from GitHub first? [Pull, then sync] [Sync without pulling]"*.
  [Pull, then sync] runs Obsidian Git's Pull, waits for HEAD to settle, then syncs.
- **Run.** `artmind vault sync --compact`, then a result notice (§3.3).
- **Refusals become actions.** A refusal is never shown as a raw error:

| Refusal | Shown as |
|---|---|
| ingest worker running | *"Ingest in progress — will sync when it finishes"*: the sync is queued behind the job |
| merge or rebase in progress, or `.artmind` conflicts | *"Resolve first"* [Resolve] |
| a bookmark not in HEAD's history | *"Pull first"* [Pull] |
| no bookmark yet | *"This laptop hasn't synced this vault before"*, showing the guide's `--bootstrapEmpty` step; the plugin does not run it itself |

### 4.3 New files

- Obsidian's file-created event, filtered to mapped folders. The plugin reads
  `.artmind/vault.yaml` through the vault adapter, because Obsidian doesn't index
  dot-folders.
- A new `.pdf`, `.pptx`, `.docx`, `.xlsx` or `.csv` in a mapped folder → the *"new files"*
  notice. Several arrivals within 10 s are grouped into one notice.
- Notes (`.md`) never prompt. Obsidian saves every few seconds, so a changed note only
  counts towards *"N to ingest"*, recomputed from `ingest pending` (§6).

### 4.4 Ingest what changed

1. **Sync first.** If a store is behind, the plugin asks *"Sync first? The other laptop's
   changes aren't applied yet. [Sync, then ingest] [Ingest anyway]"*. This enforces the
   guide's *sync before you ingest* rule.
2. **Submit.** `artmind ingest async --pending` (§6) creates one background job for exactly
   the pending files.
3. **Track.** `ingest job-status <id> --compact` every 3 s while the job runs → *"ingesting 2/5"*.
4. **Finish.** A notice with [Details] (the failed files and their errors) and, under P6,
   [Commit-and-sync]. If the job registered a table that a mapping matches, the state
   becomes *"1 table → graph"*.

### 4.5 Obsidian Git hand-offs

The plugin triggers three Obsidian Git commands:

| Moment | Command | Offered as |
|---|---|---|
| Before Sync or Ingest, when the last pull is old | Pull | [Pull, then sync] / [Pull, then ingest] |
| After an ingest, `table2graph` or resolve finishes (P6) | Commit-and-sync | [Commit-and-sync] in the result notice |
| After resolve, once no note conflicts remain | Commit (concludes the merge) | [Complete the merge] |
| artmind files changed by something else (admin-ui curation, a terminal command) | Commit-and-sync | Secondary status `✎ artmind changes to share` and a panel button |

**Noticing writes the plugin didn't make.** Each status refresh also runs
`git status --porcelain -z -- .artmind` (read-only). Uncommitted changes there that no
plugin action of the last 30 s produced (e.g. curation done in the admin-ui, or a terminal
ingest) show as the secondary `✎ artmind changes to share`. Under P6 the panel then offers
[Commit-and-sync]. Obsidian Git's timer would commit these anyway; this just makes sharing
immediate.

- Command ids are looked up at startup from `app.commands.commands` (`obsidian-git:*`),
  trying the known ids for each action. For example, *"Create backup"* became
  *"Commit-and-sync"* in later versions.
- Anything missing turns its button into an instruction (*"Run Obsidian Git: Pull"*), plus
  one warning line in the panel.

## 5. Review screens

### 5.1 `table2graph` review (modal)

It runs `artmind ingest table2graph <table> --dryRun --compact` and shows:

- **Header:** the table, its domain, the mapping file that matched, and the row count.
- **Per entity class:** built, kept, and every skip reason with its count.
- **Lookups:** resolved, stubbed, and the distinct unresolved or ambiguous values (the first
  ten, then "show all").
- **Warnings:** properties or relationship types the mapping writes that the schema doesn't
  declare, highlighted.

Buttons:
- **[Project to graph]** runs the real `table2graph`, then the result notice from §3.3.
- **[Open mapping]** opens the mapping YAML directly (it's under `.artmind/`, which Obsidian
  hides).
- **[Cancel]**.

With no matching mapping, the modal says so and offers **[Ask admin-ui to create one]**,
which opens the admin-ui URL in the browser. Writing a mapping is agent work.

### 5.2 `vault resolve` preview (modal)

It runs `artmind vault resolve --dryRun --compact` and groups the units:

- **Resolved:** the side taken, and the rule in plain words.
- **Pending:** waiting on a note you still have to resolve.
- **Reported:** your notes, `same_as.yaml`, schemas and mappings, each with a link. Resolve
  these in Obsidian.

Buttons:
- **[Resolve]** is disabled while anything is pending, with the reason shown. It runs
  `vault resolve`, then offers **[Complete the merge]** (Obsidian Git: Commit).

## 6. Additions to artmind

Everything else the plugin calls already exists and returns JSON:
- `vault status`, `vault sync`, `vault resolve` and `vault doctor` with `--compact`;
- `ingest job-status` and `jobs-active` with `--compact`;
- `ingest table2graph` with `--dryRun` and `--compact`;
- `ingest async`, which prints its job as JSON.

| # | Addition | Contract |
|---|---|---|
| X1 | `artmind --version` | Prints the package version. The plugin requires a minimum and warns otherwise. |
| X2 | `artmind ingest pending [--compact]` | Read-only, no LLM. Lists mapped files that are new, or whose content changed since their last ingest: `{notes: [...], binaries: [...], tables: [...]}`, with vault-relative paths and a reason (`new` / `changed`). It uses the same identity and version rules as ingest (body hash for notes, source hash for binaries, bytes for tables), so what it lists is exactly what an ingest would do work on. |
| X3 | `artmind ingest async --pending` | Enqueues exactly X2's list as one job. Its output is the same JSON as `ingest async`. With nothing pending, it returns `{"job_id": null, "file_count": 0}`. |
| X4 | `artmind ingest table2graph --pending [--compact]` | Lists each registered table that a mapping matches but whose graph projection is missing, or older than the table's last refresh or the mapping's last change: `[{table, domain, mapping, reason}]`. Read-only. |
| X5 | Per-file KG counts on jobs | `ingestion_job_files` gains `entity_count` and `relationship_count` (nullable; an additive migration in `db.py`). `ingest_to_kg` counts what it staged (distinct observation keys; relationships) and the worker records it. `job-status`, `job-results`, `jobs-active` and `jobs-completed` return them per file as `entities`/`relationships`, null until the file is extracted. |
| X6 | `ingest job-chunks` lookup | Reads the file's `doc_sha256` from the job's own row first, falling back to the registry. Before this, a converted binary (registered under its derived markdown) was always "not found in registry". The admin console's `/api/jobs/{id}/chunks` uses the same lookup. |
| X7 | Stalled jobs and `retry-job --compact` | `job-status` and `jobs-active` report `stalled`: `processing`, but no worker holds the lock (the same check `vault sync` refuses on). `retry-job` on a stalled job also re-queues the file left at `processing`, and re-queues the job even when nothing failed. It refuses a job a live worker still runs. `--compact` prints `{job_id, domain, retried, deregistered, files, stalled, requeued}`. |
| X8 | `GET /api/health` (admin-ui and chat-ui) | `{app, vault, version, pid}`: which app is on the port, for which vault (null outside one), at which version, in which process. The plugin probes it before opening, and matches `pid` before stopping. |
| X9 | `artmind init` installs the plugin | With `.obsidian/` present: copies the packaged build into `.obsidian/plugins/artmind/` (only files whose bytes differ; `data.json` untouched) and adds `artmind` to `community-plugins.json` (an unreadable one is left alone). Without `.obsidian/`: skipped, with the reason. `init` prints `installed` / `a -> b` / `current`. |

Each gets pytest coverage in `test/`, on real throwaway vaults, in the existing style.
Documentation goes in `docs/vault.md` and the ingestion-helper skill, and each new command
in the `justfile`'s CLI help (CLAUDE.md §4).

## 7. Plugin architecture

Location: `obsidian/artmind-obsidian/`. TypeScript, bundled with esbuild, using the Obsidian
API typings.

| Unit | Responsibility | Depends on |
|---|---|---|
| `ArtmindCli` | Runs `artmind <args> --compact` with `cwd` set to the vault root, parses the JSON, and applies per-command timeouts. **Write commands run one at a time from a queue** (sync, ingest submission, table2graph, resolve); read commands run concurrently. Locates `artmind` from the setting, auto-detected from `~/.local/bin/artmind` and `uv tool dir`, because Dock-launched apps don't see the shell's PATH. | Node `child_process` |
| `VaultState` | **A pure function:** `(status JSON, active job, ingest pending, tables pending, git facts, errors) → {states[], primary}`, with the §3.1 priority rules and the §4.2 refusal→action mapping. | — |
| `Watchers` | The HEAD poll (15 s), the file-created listener (mapped folders, 10 s grouping), focus regained, and the job poll (3 s while a job runs). Each schedules a debounced status refresh. | `ArtmindCli`, Obsidian events |
| `ObsidianGitBridge` | Looks up and runs the Pull, Commit-and-sync and Commit commands, and reports which are missing. | `app.commands` |
| Views | `StatusBarItem`, `ArtmindView`, `TableReviewModal`, `ResolveModal`, and the notices. They render `VaultState` and call actions; they hold no logic. | the above |
| `Settings` | The artmind path; poll intervals; notice toggles per kind; "offer to commit-and-sync after artmind writes" (**on**). | — |

**Install:** `artmind init` (X9). `just obsidian-plugin-build` stages `main.js`,
`manifest.json` and `styles.css` in the package (`artmind/obsidian_plugin/`, gitignored;
`just dev-install` runs it, and a wheel must be built through the justfile to carry it).
`init` copies them into `<vault>/.obsidian/plugins/artmind/` and lists `artmind` in
`.obsidian/community-plugins.json`. The folder is gitignored (block v5), so each machine
runs the build matching its own artmind, and its settings stay per machine.
`just obsidian-plugin-install <vault>` remains the quick loop while developing.

**Dormancy:** in a vault with no `.artmind/`, the plugin adds no status bar item and runs no
polls.

## 8. Error handling

| Situation | Behaviour |
|---|---|
| `artmind` not found, or older than the minimum | Status bar `⚠ artmind`. The panel says where it looked, or which version it needs, with **[Set path]**. Every action is disabled. |
| Not an artmind vault | Dormant (§7). |
| Neo4j unreachable (`vault status` reports it) | *"Neo4j unreachable"*, showing the URI from the vault's `config.env` (never the password). Sync and table2graph are disabled; ingest is allowed, with a warning that the graph write will fail. |
| A command fails or times out | A notice with the short reason; the full output goes to Activity. Nothing is retried automatically. |
| A refusal | Mapped to its fixing action (§4.2); never shown as a raw error. |
| Obsidian Git missing, or its commands renamed | The buttons become instructions, plus one warning line (§4.5). |
| Obsidian quits during a sync | The child process is left to finish. `vault sync` only moves its bookmarks once everything succeeds, so an interrupted run is simply redone. Ingest jobs run in artmind's worker, independent of Obsidian. |

## 9. Testing

- **`VaultState` unit tests:** table-driven, covering every state, every priority clash and
  every refusal→action mapping. The inputs are **real `--compact` JSON captured from the
  CLI**, checked in as fixtures. A test in `test/` regenerates them against a throwaway
  vault and fails on any shape drift, so a CLI JSON change fails there before it breaks the
  plugin.
- **`ArtmindCli`:** run against a fake `artmind` script that returns fixture JSON, non-zero
  exits, stderr and delays. Covers the write-queue order, timeouts and path detection.
- **`ObsidianGitBridge`:** with `app.commands` stubbed for present, renamed and missing
  commands.
- **artmind additions X1–X4:** pytest in `test/`, on real throwaway git vaults. X2 and X4
  are checked against what a following ingest or `table2graph` actually does.
- **Manual checklist:** two vaults on one laptop standing in for laptops A and B (two local
  Neo4j databases, one bare git remote). Exercise each status-bar state, each notice, both
  modals, the Pull / Commit-and-sync / Commit hand-offs, dormancy in a non-artmind vault,
  and the artmind-missing error.

## 10. Out of scope

- Automatic `vault sync` (Plan E, issue #26).
- Querying from Obsidian. `admin-ui` and chat-ui cover that; a later version could add an
  "ask artmind" view.
- Curation actions (same-as, conflicts, supersession). The admin-ui agent does those;
  the plugin only offers to commit-and-sync after them (P6).
- Mobile (P8).
