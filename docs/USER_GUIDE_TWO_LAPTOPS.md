# User guide: one vault, two laptops, a local Neo4j on each

This guide sets up an artmind vault that you work on from two laptops, **A** and
**B**. Each laptop has its own local Neo4j. Obsidian Git keeps the vault itself in
step through a private GitHub repo, and `artmind vault sync` brings each
laptop's graph and table store up to date with what arrived.

For the design and every edge case, see [`docs/vault.md`](./vault.md). This guide
covers what to do and when.

---

## 1. How the pieces fit

```
  Laptop A                                   Laptop B
  ────────                                   ────────
  Obsidian + your files                      Obsidian + your files
     │  artmind ingest / curate                 │
     ▼                                          │
  .artmind/data/…  (graph JSON, tables,         │
     │              curation records)           │
     ▼                                          ▼
  Obsidian Git ── commit + push ──► GitHub ──► pull ── Obsidian Git
                                                        │
  Neo4j A  (A's own)                            artmind vault sync
                                                        ▼
                                                 Neo4j B + DuckDB B
```

Three rules explain everything below:

1. **Obsidian Git owns git.** artmind never commits, pulls or pushes. It only
   writes files in the vault. The one exception is `vault resolve`, which stages
   a file during a merge but still never commits.
2. **The vault is the source of truth; each Neo4j is a copy built from it.**
   Everything artmind learns (extracted graph data, tables, your curation
   decisions) is saved as files under `.artmind/`. Any laptop can rebuild its
   graph from those files alone.
3. **LLM work happens once, on the laptop that ingests.** The other laptop
   replays the result with `vault sync`. That costs no LLM calls; it's just graph
   writes plus local embeddings.

---

## 2. One-time setup

### 2.1 On both laptops: install artmind and set the machine config

1. Install [Homebrew](https://brew.sh), clone artmind
   (`git clone https://github.com/neurongraph/artmind9.git ~/projects/artmind9`),
   and run `bash scripts/bootstrap.sh` from inside the checkout. It installs
   the tools (uv, just, gh, node, colima, docker, Obsidian), logs you in to
   GitHub, installs this checkout and neo4j-manager (cloned next to it), puts
   Homebrew and `~/.local/bin` on your zsh `PATH`, and creates
   `~/.artmind/config.env`. Safe to re-run. Open a new terminal afterwards.
2. Edit `~/.artmind/config.env`. It holds your models and API keys, is private
   to the machine, and is never committed. **Use the same embedding model and
   `ARTMIND_KG_EMBEDDING_DIMENSIONS` on both laptops.** The dimension is baked
   into Neo4j's vector index when `artmind setup` runs. The two laptops embed
   independently, so a mismatch makes the two graphs answer differently.

### 2.2 Laptop A: create the vault

```bash
artmind vault new my_vault      # Neo4j instance, ~/artmind_vaults/my_vault, private repo <you>/my_vault, plugins, first commit + push
```

It creates a neo4j-manager instance for A, the vault folder, `.artmind/`
(including the `vault_id` every clone shares, and `.artmind/config.env` with
A's Neo4j password — gitignored, stays on A), the Obsidian Git, Unhide,
VSCode Editor and Ghostty Terminal plugins with Obsidian Git set to merge,
commit-and-sync every 10 minutes and pull on startup, Obsidian's core Web
Viewer plugin switched on, the graph schema, a
first commit pushed to a new private GitHub repo, and A's sync bookmark. It
ends by opening Obsidian: choose **Open folder as vault**, pick the folder,
and **Turn on community plugins**.

Then, still on A:

1. **Tell artmind which folders to ingest.** Edit `.artmind/vault.yaml`:
   ```yaml
   ingest:
     trigger: manual
     mappings:
       - path: Team_performance_management/**
         domain: performance_management
   ```
   Only mapped folders are ingested, and a file's folder decides its domain.
   Unmapped folders (`_Inbox/`, `attachments/`) are never ingested. Moving a
   note into a mapped folder is how you say it's ready.
2. **Add your schemas and table mappings.** Schemas go in
   `.artmind/domains/schemas/<domain>_schema.yaml`, table mappings in
   `.artmind/domains/table_mappings/*.yaml`. The easiest way is to ask the
   admin-ui agent to create them (it uses the create-schema skill).
Don't also sync this folder with iCloud, Dropbox or Obsidian Sync.

### 2.3 Laptop B: join the vault

```bash
artmind vault new my_vault --join   # clones <you>/my_vault, B's own Neo4j instance, rebuilds B's graph
artmind vault doctor
```

`--join` clones the repo, creates a neo4j-manager instance for B, writes B's
own `config.env`, installs the plugins and runs `setup`. Then it runs
`vault sync --bootstrapEmpty`, which replays every document and table into
B's graph. That makes no LLM calls, but embeddings are computed locally, so a
big vault takes a while. Open the folder in Obsidian the same way as on A.

---

## 3. Daily work: which command when

**The habit:** *pull → `vault sync` → work → let Obsidian Git push.*
When you sit down at a laptop, let Obsidian Git pull, then run
`artmind vault sync`, **before** you ingest or curate anything. If you forget,
every `artmind query` reminds you on stderr, for example:
`graph is 3 docs / 1 tables behind the vault — run artmind vault sync`.

| You did this | Then run | Notes |
|---|---|---|
| Opened a laptop or pulled changes | `artmind vault sync` | Replays the other laptop's ingests, tables and curation. No LLM cost. |
| Wrote or edited a **markdown note** in a mapped folder | `artmind ingest sync <note or folder>` | Extraction runs only if the **body** changed. Frontmatter-only edits (tags, title) cost nothing. |
| Copied in a **PDF / PPTX** | `artmind ingest sync <file>` | Converts the file to markdown (docling), then extracts. It costs LLM calls, so do it once, on one laptop. For big batches use `artmind ingest async` or the admin-ui dashboard. |
| Copied in an **xlsx / csv** | `artmind ingest sync <file>` | Loads it as a **table** in the structured store (DuckDB). Its folder mapping sets the domain. It classifies the columns automatically; review that (section 4). |
| Want a table's rows **in the graph** | `artmind ingest table2graph <table> --dryRun`, then without `--dryRun` | Needs a table mapping that matches the table name (e.g. `hercules_output_*`). No LLM. Read the dry-run report first. |
| A new dated export of the same table | `ingest sync <new file>`, then `table2graph <new table>` | The mapping's glob matches the new name. |
| Edited a schema or table mapping | Nothing extra here. On the **other** laptop, `vault sync` rebuilds the affected tables | Sync reads mappings and schemas as committed. Let Obsidian Git commit your edit first. |
| Obsidian Git reports a **merge conflict** | Notes: fix in Obsidian. Files under `.artmind/data/`: `artmind vault resolve --dryRun`, then `artmind vault resolve` | `resolve` picks one whole side the same way on both laptops, then Obsidian Git commits the merge. Sync refuses until it's settled. |
| Something looks off | `artmind vault status` (and `vault doctor`) | Shows HEAD, each store's bookmark, what's pending, and any merge or conflicts. |
| Upgraded artmind | `just dev-install`, then `artmind init` in the vault, then restart `admin-ui` / `serve` from inside the vault, and reload Obsidian | `init` upgrades the `.gitignore` block and the Obsidian plugin. `dev-install` stops daemons but doesn't restart them. |

**Rules for ingesting on both laptops:**

- **Sync before you ingest**, as above. Then you're building on the latest state.
- **Don't ingest the same new file on both laptops before they've synced.**
  You'd pay for extraction twice. If both laptops ingest the same new note
  before syncing, it gets two different ids, and git conflicts on its
  `_artmind_id` line. Keep one, then re-ingest. Pick one laptop for each new
  batch.
- **After a big ingest, trigger Obsidian Git's commit-and-sync** rather than
  waiting for the timer, so the other laptop gets it soon.
- `vault sync` refuses while the ingest worker is running (an admin-ui or
  `ingest async` job). Let the job finish first; `artmind ingest jobs-active`
  shows it.
- Renaming or moving notes is safe: identity follows `_artmind_id`, not the
  file name.

### 3.1 The same, from Obsidian

The artmind Obsidian plugin turns this section into clicks. `artmind init`
installs it, on **each laptop**: it copies the plugin into
`.obsidian/plugins/artmind/` and enables it. The folder is gitignored, so each
laptop runs the plugin matching its own artmind, and an upgrade
(`artmind init` again) replaces it.

```bash
cd ~/artmind_vaults/my_vault && artmind init
```

Then reload Obsidian. The first time, Obsidian asks you to turn on community
plugins for the vault. (Open the folder in Obsidian once before `init`: with no
`.obsidian/` yet, `init` skips the plugin and says so.)

The plugin adds an **artmind icon to the left ribbon**: click it for the side
panel, right-click for Sync, Ingest, the admin console and Doctor. A dot on it
means the vault needs something. **Admin ↗** in the panel (or the command
"Open admin console") opens this vault's admin console, starting
`artmind admin-ui` first if nothing is running. It opens as an Obsidian tab when
the Web viewer core plugin is on, otherwise in your browser (see the plugin's
settings). It keeps running after Obsidian
quits; "Stop admin console" stops one the plugin started.

Its status bar item always shows where the vault stands and does the one thing
that state needs when clicked:

| Status bar | Click |
|---|---|
| `⚠ resolve artmind conflicts` | the `vault resolve` preview, then [Resolve] |
| `⚠ artmind` | the side panel, at the problem and its fix |
| `◌ ingesting 2/5` | the side panel, at the job |
| `◉ 3 docs · 1 table behind` | Sync (offers Obsidian Git's Pull first when the last pull is old) |
| `◉ 1 table → graph` | the `table2graph` review, then [Project to graph] |
| `◉ 2 to ingest` | Ingest what changed (offers Sync first when a store is behind) |
| `◉ artmind` | the side panel |

It never syncs, ingests or writes to the graph without a click, never writes
to git (Pull, Commit-and-sync and Commit are Obsidian Git's, which it
triggers), and never runs `--bootstrapEmpty` for you: §2.3 is still a terminal
step. After an ingest, a `table2graph` or a resolve, its notice offers
[Commit-and-sync] so the other laptop gets the result sooner (turn that off in
its settings). If Obsidian was started from the Dock and cannot find
`artmind`, set its path in the plugin's settings (`which artmind` in a
terminal).

---

## 4. Ingest and curation: the ideas you need

### 4.1 From documents to entities

```
Document ──► chunks ──► observations ──► entities (and relationships)
 (a note,   (pieces of   (per-chunk claims:     (what you query: one node per
  a PDF)     its text)    "Priya is a Manager",   name + class + domain, built
                          "reports to Arun")      from ALL its observations)
```

- **Extraction** (LLM) turns each chunk into **observations**, following the
  domain's schema: which entity classes, properties and relationships exist.
- **Projection** (no LLM) groups observations by entity key, `name | class |
  domain`, and builds the **entity** with its merged properties and
  relationships. An entity is never edited directly; it's always rebuilt from its
  observations.
- **Versions.** When a note's body changes and you re-ingest, the new version's
  observations become current and the old ones become history. That's why a
  question can have a current answer and a timeline.
- **Tables** skip the LLM: `table2graph` turns rows into observations through
  your table mapping, and projection builds entities from them in the same
  way.

### 4.2 When curation is needed

Most of the time you ingest and query. Curate when the graph shows one of these
situations. In the admin-ui, ask the agent in plain words; the commands are
listed for reference.

| Situation | What it means | Ask the admin-ui agent / command |
|---|---|---|
| The same thing appears as **two entities** ("Acme Corp" vs "ACME") | They're one real thing under two keys | "Find and review duplicate entities in *domain*". `artmind sameas propose --domain X`, `sameas list`, `sameas approve <id>` / `reject`. Approving writes `.artmind/same_as.yaml`, and the members are rebuilt as one. |
| **Two sources disagree** (different values for the same fact) | A **conflict**: detected by an LLM comparing evidence from both sides | "Find conflicts in *domain*". `artmind ingest refine-graph` first, then `ingest detect-conflicts --domain X`; list with `query graph conflicts`. Close with `ingest resolve-conflict <id> --status resolved\|dismissed --reason "…"`. |
| A **newer document replaces an older one** (new policy, new org chart) | **Supersession**: the older document gets a `valid_to` date, and answers favour the newer one | "Mark *new* as superseding *old*". `artmind ingest supersede …`, or `ingest detect-supersession`. Conflict review can also end with a supersession verdict. |
| A document is **obsolete but should stay as history** | **Retire** it: out of "latest", kept for timelines | `artmind docs retire …`; `docs restore` brings it back. A retirement sticks: re-ingesting doesn't undo it. |
| A document should be **gone** | **Archive** it: bundled away, removed from the vault and graph | `artmind docs archive …` (asks for confirmation; `docs restore-from-archive` undoes it). |
| A table's **classification** looks wrong (grain, which columns are keys or map to entities) | Proposed by an LLM at ingest, **unconfirmed** until you check it | "Review the proposed mappings for *table*". `artmind db review --domain X`, `db mappings …`, `db grain …`. |
| You want better **entity descriptions** | **Synthesis**: an LLM writes a summary from an entity's observations | `artmind projection synthesize --domain X` |
| You told the chat a **fact** | An **update**: saved as its own small document (a `UserChat`) | Chat/`artmind update draft` → `update confirm`; take it back with `update retract --session …` |

**All of these travel between laptops.** Each decision is saved as a file in
the vault:

| Decision | File |
|---|---|
| Same-as | `.artmind/same_as.yaml` |
| Conflicts, supersessions, retirements, syntheses | One file each under `.artmind/data/curation/` |
| Chat updates | `kg/<domain>/update__…/` |
| Table classifications | The table's `.meta.json` |

Obsidian Git carries them across, and `vault sync` applies them on the other
laptop.

Two exceptions:

- **Same-as *proposals* stay on the laptop that made them.** They're a review
  queue. Only approved groups travel, so review proposals on the laptop that
  generated them.
- **Curation made before this version of artmind** (conflicts, supersessions,
  retirements, syntheses) has no file, so it doesn't travel until you redo it.
  A fresh vault has nothing to worry about.

---

## 5. Quick reference

```bash
# every session
artmind vault sync                      # after Obsidian Git pulls
artmind vault status                    # where each store stands

# adding content
artmind ingest sync <file|folder>       # notes, PDFs, PPTX (LLM), xlsx/csv (table)
artmind ingest table2graph <table> --dryRun
artmind ingest table2graph <table>

# when git stops on a conflict under .artmind/data/
artmind vault resolve --dryRun
artmind vault resolve                   # then let Obsidian Git commit the merge

# health
artmind vault doctor

# admin console (run from inside the vault)
artmind admin-ui
```

Not needed for a fresh vault:
- `artmind vault migrate-frontmatter` is only for vaults ingested by an older
  artmind.
- Automatic apply isn't implemented yet ([issue #26](https://github.com/neurongraph/artmind9/issues/26)),
  so `vault sync` is always a manual step.
