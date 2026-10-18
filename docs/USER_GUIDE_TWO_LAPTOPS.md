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
2. **Install Ollama and pull the embedding model.** `bootstrap.sh` doesn't
   install Ollama, but embeddings always run on it (OpenRouter has no
   embeddings API here). Install it from [ollama.com](https://ollama.com), make
   sure it's running, and pull the model named in your config, by default:

   ```bash
   ollama pull nomic-embed-text
   ```

   Laptop B needs it before the join: the rebuild in §2.3 embeds every chunk.
3. Edit `~/.artmind/config.env`. It holds your models and API keys, is private
   to the machine, and is never committed. **Use the same embedding model
   (`ARTMIND_KG_EMBEDDINGS_MODEL`) and `ARTMIND_KG_EMBEDDING_DIMENSIONS` on
   both laptops.** The dimension is baked into Neo4j's vector index when
   `artmind setup` runs. The two laptops embed independently, so a mismatch
   makes the two graphs answer differently. The easiest way to get this right
   on B is to copy A's values for those two keys.

   The LLM provider settings are checked before `vault new` creates anything,
   so they must be filled in on B as well, even though a join makes no LLM
   calls:

   | `ARTMIND_KG_LLM_PROVIDER` | Must be set |
   |---|---|
   | `ollama` | nothing |
   | `openrouter` | `ARTMIND_OPENROUTER_API_KEY` |
   | `ibm_ica` | `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`. Also set `ARTMIND_KG_ANTHROPIC_BASE_URL` to the same endpoint if you run artmind from a tool that exports its own `ANTHROPIC_BASE_URL` (Claude Code does): the environment variable would otherwise beat the file and ibm_ica requests go to the wrong endpoint. |

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

Joining means: clone the vault's private repo, give B its own Neo4j, and
rebuild B's graph from what is committed. Nothing is copied from A's Neo4j.

**The short version**, when the repo belongs to the GitHub account that `gh` is
logged in as on B:

```bash
artmind vault new my_vault --join
artmind vault doctor
```

If the repo belongs to a **different** GitHub account than the one you
normally use on B, follow all the steps below, including §2.4.

#### Before you start: on laptop A

1. **Let Obsidian Git push everything.** The join clones what is on GitHub,
   so a commit that is still only on A (a status change, a new note, an
   ingest) won't reach B. Trigger *Obsidian Git: Commit-and-sync*, then check
   that nothing is waiting:

   ```bash
   git -C ~/artmind_vaults/my_vault status -sb    # the first line must not say "ahead"
   ```
2. **Note three things** to type on B: the vault name (`my_vault`), the GitHub
   account that owns the repo (the part before the `/` in
   `github.com/<owner>/my_vault`), and A's embedding model and dimension (§2.1).

#### On laptop B

1. **Machine setup.** Do §2.1 in full: bootstrap, Ollama with the embedding
   model pulled, and `~/.artmind/config.env` with the same embedding model and
   dimension as A and the credentials your LLM provider needs. `vault new`
   refuses to start if the provider's credentials are missing. The vault's own
   `.artmind/config.env`, which the join writes, holds only B's Neo4j
   connection.
2. **Check the GitHub account `gh` is using.** The join runs `gh` to look up
   and clone the repo, and `gh` acts as its *active* account.

   ```bash
   gh auth status
   ```

   If the owner of the repo is not logged in on B, log in as them
   (`gh auth login`). If it is logged in but not active, switch:

   ```bash
   gh auth switch -u <owner>
   ```

   With one GitHub account there is nothing to do here.
3. **Join the vault:**

   ```bash
   artmind vault new my_vault --join                      # the repo is <your active gh account>/my_vault
   artmind vault new my_vault --join --githubOwner <owner>   # the repo is <owner>/my_vault
   ```

   Use `--githubOwner` whenever the owner isn't the active `gh` account, or is
   an organisation. It prints what it is about to do and asks for
   confirmation (`--yes` skips that). In order, it:
   - checks everything first: the repo exists and `gh` can see it, the folder
     and the Neo4j instance don't exist yet, the tools are on `PATH`, and the
     LLM credentials are set. Every problem is listed at once and nothing has
     been created;
   - creates B's Neo4j instance through neo4j-manager (to use a Neo4j you
     already run there, add `--neo4jUri`, `--neo4jUser` and `--neo4jPassword`);
   - clones the repo to `~/artmind_vaults/my_vault` (`--dir` to put it
     elsewhere);
   - writes the vault's `.artmind/config.env` with B's Neo4j connection and
     installs the artmind and community plugins;
   - runs `artmind setup`, then `vault sync --bootstrapEmpty`, which replays
     every committed document and table into B's graph.

   The rebuild makes no LLM calls, but it embeds every chunk on B's Ollama, so
   a vault with thousands of notes takes a while. Neo4j needs a 4 GiB colima VM
   or it can be killed during the rebuild (see *Neo4j memory* in
   [`INSTALL.md`](./INSTALL.md)).
4. **If you switched accounts in step 2, switch back** now, so the rest of
   your work on B uses your usual account:

   ```bash
   gh auth switch -u <your usual account>
   ```
5. **Make Obsidian Git able to push.** With a single GitHub account the
   default setup usually works, and you can go to step 6. If `gh` was
   switched in step 2, or Obsidian Git can't push, do §2.4 now.
6. **Check it:**

   ```bash
   artmind vault status                                      # HEAD and each store's bookmark; nothing pending
   artmind vault doctor                                      # read-only readiness checks
   git -C ~/artmind_vaults/my_vault push --dry-run           # credentials work, nothing is sent
   ```

   `vault doctor` flags a second sync tool on the folder (iCloud, Dropbox) and
   Obsidian settings that would make merges unsafe. Fix what it reports before
   you work.
7. **Open it in Obsidian.** Choose **Open folder as vault**, pick
   `~/artmind_vaults/my_vault`, and **Turn on community plugins**. Leave
   **Obsidian Sync** off for this vault, as on A: Obsidian Git is the only
   thing that moves the files. The join already installed the artmind
   plugin; §3.1 describes it.

From now on B follows §3: let Obsidian Git pull, then `artmind vault sync`.

#### If the join fails partway

Re-run **the same command**. Finished steps are skipped and the unfinished one
resumes; progress is kept in `~/.artmind/vault_new/my_vault.json`. Re-running
with different options is refused, so use the same flags. To start over from
nothing, delete that file and the folder, and `neo4j-manager remove my_vault`.

| The message says | Usually means | Do this |
|---|---|---|
| `GitHub repo <owner>/my_vault does not exist — nothing to join` | The active `gh` account can't see the repo (a private repo looks missing to the wrong account), or the owner or name is wrong | `gh auth status`; switch to the owner (step 2), or pass the right `--githubOwner` |
| `gh is not logged in` | No `gh` login on B | `gh auth login` |
| `… needs ARTMIND_OPENROUTER_API_KEY` (or the ibm_ica keys), `… does not exist — run scripts/bootstrap.sh` | `~/.artmind/config.env` on B is missing or incomplete | Fill it in (§2.1) |
| `… already exists and is not empty` | The target folder is taken | A different `--dir`, or move the old folder away |
| `neo4j-manager instance 'my_vault' already exists` | B already has an instance of that name that may hold another vault's data | `neo4j-manager remove my_vault` if it is disposable. The instance name always equals the vault name, so there is no other name to pick |
| `an unfinished … used different options` | A previous run used other flags | Re-run with those flags, or delete the state file named in the message |
| A `step 'setup'` or `step 'bookmark'` failure | Neo4j not up yet, Ollama not running, or the embedding model not pulled | Start Ollama, `ollama pull` the model, then re-run |

### 2.4 Laptop B: Obsidian Git pushing as a specific GitHub account

Do this on B if the vault's repo belongs to a GitHub account other than your
usual one, or if Obsidian Git can't push. `bootstrap.sh` sets git to use `gh`
for https credentials (`gh auth setup-git`). That asks the *active* `gh`
account, which is the wrong one when you've switched back (step 4 above). It
can also fail outright inside Obsidian, which is usually launched from the Dock
with a smaller `PATH` than your terminal. So give this one repo its own
credential instead, stored in the macOS Keychain. Everything here is
**local to this repo's `.git/config`**; your other repos are unaffected.

Run these inside the vault, with `<owner>` the repo's GitHub account:

```bash
cd ~/artmind_vaults/my_vault
```

Put the account name into the remote, so git knows which login to use:

```bash
git remote set-url origin https://<owner>@github.com/<owner>/my_vault.git
```

Make Keychain the only credential helper for GitHub in this repo. The empty
value clears the `gh` helper inherited from your global config:

```bash
git config --local --replace-all credential.https://github.com.helper ""
```

```bash
git config --local --add credential.https://github.com.helper osxkeychain
```

```bash
git config --local credential.https://github.com.usehttppath false
```

Store `<owner>`'s token in the Keychain, once. This reads it from `gh`, so
`<owner>` must be logged in to `gh` on B, though it needn't be the active
account:

```bash
printf "protocol=https\nhost=github.com\nusername=<owner>\npassword=%s\n" "$(gh auth token -u <owner>)" | git credential-osxkeychain store
```

Check it, then let Obsidian Git do a commit-and-sync once:

```bash
git push --dry-run
```

If pushes start failing with an authentication error later, the token `gh`
holds has changed (for example after `gh auth logout` or `gh auth refresh`).
Re-run the `printf … | git credential-osxkeychain store` line.

With the owner in the remote URL, `vault doctor`'s check that review dossiers
aren't in a public repo asks `gh` *as that account*, so it still works when
another account is active. It reports `unknown` only if `gh` holds no login for
the owner.

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

The plugin adds an **artmind icon to the left ribbon**. Click it for the side
panel; right-click for *Open side panel*, *Open admin console* and *Run
doctor*. A dot on it means a step is waiting. **Admin ↗** in the panel (or the
command "Open admin console") opens this vault's admin console, starting
`artmind admin-ui` first if nothing is running. It opens as an Obsidian tab when
the Web viewer core plugin is on, otherwise in your browser (see the plugin's
settings). It keeps running after Obsidian quits; "Stop admin console" stops
one the plugin started.

The side panel is a **readiness checklist**, top to bottom:

| Row | What it says | Its button |
|---|---|---|
| Commits to apply | ✓ up to date, or what there is to apply (from the other laptop or from local files), or conflicts; a hint when Obsidian Git's auto pull is off | **Apply to graph** (`artmind vault sync`) · **Resolve…** · **Complete the merge** · **Pull** (always there) |
| Documents | how many files to ingest (expand for the paths), the running job, failures | **Ingest N files** · **Retry failed** · **Retry job** |
| Tables | tables whose classifications need review; tables ready for the graph | **Review in admin console ↗** (opens the chat with the request filled in; you send it) · **Review & project…** |
| Graph | ✓ built and embedded, or *needs rebuild* with each reason | **Rebuild graph** (`artmind projection rebuild --sweep`) |
| Curate (optional) | how many same-as proposals and open conflicts are waiting for review, or the duplicate and conflict checks you can run | **Review in admin console ↗** and **Adjudicate in admin console ↗** (open the chat with the request filled in; you send it) · **Find duplicates…** (`sameas propose`, one run per domain you pick) · **Detect conflicts…** (`ingest detect-conflicts`, needs two or more domains). Both checks ask for a cap on pairs first (default 200) and show the worst-case LLM calls |
| Descriptions (optional) | how many entity descriptions could be synthesized, and the LLM calls that costs | **Synthesize…** (pick domains and an optional cap first) |
| Vault (footer) | artmind files to commit and commits to push | **Commit & push** (Obsidian Git's commit, pull, push) |

The header shows a progress bar and **N of M steps done**, or **Graph ready ✓** when
the first four rows are done; **↻** re-reads everything. Steps are numbered, with ✓
for done. The **current step** (the first row not done) is highlighted and its
first button is the accent button. Every other available action is a raised
button, and extra actions have an outline. Curate and Descriptions are optional:
dashed, and they never hold up **Graph ready**. Other buttons still
work; clicking one out of order asks first (for example, *Ingest* while the
other laptop's changes aren't applied offers [Apply first] [Ingest anyway]).

Row 1 stays current by itself when **Obsidian Git's auto pull** is on (Obsidian
Git settings → "Auto pull interval", in minutes): the plugin reads that setting
and never fetches on its own. While auto pull is off, row 1 says "Turn on
Obsidian Git's auto pull to see the other laptop's changes", and *Apply to
graph* asks "Pull from GitHub first?" when the last pull is more than five
minutes old. **Pull** is always on row 1 for a pull by hand.

The status bar shows `artmind ●●○○○` and the current step: one dot per required row
plus the footer, filled when done. Curate and Descriptions have no dot. Clicking it opens the panel at that row.

Notices are text only: they say what finished and what is next ("Ingested 365
files, 3 failed. Next: retry failed."), and clicking one opens the panel at its
row. Every action is also a command: *Pull*, *Apply to graph*, *Ingest what
changed*, *Rebuild graph*, *Synthesize…*, *Find duplicates*, *Detect conflicts*,
*Commit & push*, *Review tables for graph*, *Resolve artmind conflicts*, *Run doctor*. A command that can't run
right now says why.

The plugin never ingests, rebuilds, synthesizes, finds duplicates, detects conflicts
or writes to the graph without a click. Each action's button shows a spinner and
a pending label (for example "Starting ingest…") the moment you click it, and it
ignores further clicks until the action ends. never writes to git (Pull, Commit & push and Complete the merge run
Obsidian Git's own commands), and never runs `--bootstrapEmpty` for you: §2.3 is
still a terminal step, and row 1 shows it. If Obsidian was started from the Dock
and cannot find `artmind`, set its path in the plugin's settings (`which
artmind` in a terminal).

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
# joining a vault on a new laptop (§2.3; add §2.4 if the repo is another GitHub account's)
gh auth status                          # which GitHub account is active
gh auth switch -u <owner>               # only if the repo's owner is not the active one
artmind vault new my_vault --join [--githubOwner <owner>]
git -C ~/artmind_vaults/my_vault push --dry-run

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
