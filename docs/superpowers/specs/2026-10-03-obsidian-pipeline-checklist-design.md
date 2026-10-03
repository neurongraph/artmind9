# Obsidian plugin: the readiness checklist — design

**Status:** approved design, 2026-10-03. Builds on
`2026-09-30-obsidian-plugin-design.md` (the plugin spec). Where this spec and that one
disagree, this one wins.

## 1. Why

Today the plugin shows **commands**: Sync, Ingest, Resolve, Doctor, plus one
status-bar label for the highest-priority state. The user cannot see where their
content is on the way to a graph that is queryable and shared, so nothing tells
them that a step is waiting. Four specific gaps:

1. **The projection rebuild is invisible, and it can fail silently.** On 2026-10-03
   the `my_second_brain` vault ran two large jobs, and both reported `completed`.
   - **Job `5f5c2d11`, 365 files.** The deferred full rebuild ran as one transaction
     of 4,332 keys. That ran colima's 2 GB VM out of memory and Neo4j was killed. The
     uncaught error killed the worker too. A retry re-ran the job with 0 files: the
     worker only rebuilds for files processed in the current run, so no rebuild ran,
     and the job was marked `completed`.
   - **Job `0c616a6f`, 363 files.** The rebuild committed, but the Neo4j connection
     was dead for both embed sweeps. The job was marked `completed` with
     `embedded: 0` for 2,801 entities.
   - **Why nothing flagged it.** `projection status` doesn't count un-embedded or
     un-projected entities, so it reported the graph as healthy.
2. **"Which files?" is never answered.** The panel shows counts only.
3. **The plugin uses "sync" for two different things.** Obsidian Git's
   commit-and-sync means commit, pull, push. artmind's `vault sync` means apply
   committed files to this machine's stores. One can trigger the other.
4. **Every action appears twice: as a button and in a notice.**

## 2. Decisions

| # | Decision |
|---|---|
| D1 | Every graph-writing step stays an explicit click (plugin spec P7 holds). That includes **Rebuild graph** and **Synthesize**. |
| D2 | The side panel is a **readiness checklist**: a top-down list of stages, each with a status, a one-line explanation and its own button. Rows expand to list their files. |
| D3 | The **current step** is the first row that is neither done nor optional. Only it is highlighted, with a filled button. Other rows with work to do get outline buttons, still clickable. |
| D4 | **Synthesize is optional.** It shows a count and an estimated cost, is never amber, and is never the current step. |
| D5 | **Commit & push is a footer, not a step.** Shareable files come from ingest and synthesize. The graph rebuild is local to each machine and is never shared: the other laptop's `vault sync` rebuilds its own projection. |
| D6 | **Labels:** **Pull** (`obsidian-git:pull`) · **Apply to graph** (`artmind vault sync`) · **Commit & push** (`obsidian-git:push`, i.e. commit, pull, push). The word "sync" appears in no label. Every label lives in `texts.ts`. |
| D7 | **Notices give advice and carry no buttons.** Clicking a notice's text opens the panel at the relevant row. |
| D8 | **The status bar and ribbon open the panel; they never run an action.** |
| D9 | The backend and CLI fixes below (§6) are part of this work, not a separate task. |

## 3. The checklist

### 3.1 Layout

```
artmind · my_second_brain                         ↻
Graph ready ✓           (or: "3 steps to a ready graph")
──────────────────────────────────────────────────
✓  From the other laptop      up to date · applied 10:02
●  Documents                  7 to ingest            [Ingest 7 files]
     ▸ 2024-12-31.md (changed) · Ideas.md (new) · +5
●  Tables                     1 needs review         [Review in admin console ↗]
                              1 ready for the graph  [Review & project…]
!  Graph                      needs rebuild          [Rebuild graph]
     2,801 entities not embedded
◇  Descriptions (optional)    41 could be synthesized · ~41 LLM calls
                                                     [Synthesize…]
──────────────────────────────────────────────────
Vault   ✎ 12 artmind files to commit · ↑ 0         [Commit & push]
        from: ingest (365 files) · syntheses (41)
```

The header's **↻** re-reads all state. Today's panel has no refresh control. The
plugin's existing callouts (problems, bootstrap, doctor, activity) stay, below the
footer.

### 3.2 Rows

Each row is computed as a pure function of the state inputs (§5.1). Glyphs: `✓` done ·
`●` work to do · `◌` in progress · `!` / `✗` / `⚠` attention · `◇` optional ·
`○` waiting on a row above.

| Row | States → button | Reads |
|---|---|---|
| **1 From the other laptop** | ✓ *up to date* · ● *N commits on GitHub* → **Pull** · ● *3 docs · 1 table to apply* → **Apply to graph** · ⚠ *conflicts* → **Resolve…** · ● *resolved, complete the merge* → **Complete the merge** (`obsidian-git:commit`) · ● *first sync on this laptop* (today's bootstrap callout text) | `vault status`, git upstream |
| **2 Documents** | ✓ *all ingested* · ● *N to ingest*, expanding to paths with `new`/`changed` → **Ingest N files** · ◌ *ingesting n/m*, expanding to today's job card · ◌ ***finishing: building the graph*** (when `finalize.state == pending`) · ✗ *N failed*, expanding to errors → **Retry failed** · ⚠ *stalled* → **Retry job** · plus any `ingest pending` errors | `ingest pending`, `job-status` |
| **3 Tables** | ✓ · ● *N need review* (classifications unconfirmed) → **Review in admin console ↗** · ● *N ready for the graph* → **Review & project…** (today's `TableReviewModal`) · ● *no mapping* → **Ask admin-ui** | `db review` (new to the plugin), `table2graph --pending` |
| **4 Graph** | ✓ *built · embedded · rebuilt 10:02* · ! *needs rebuild*, with each reason on its own line: *schema changed* · *same_as edited* · *N entities not embedded* · *N chunks not embedded* · *N observation keys not projected* · *last job's rebuild failed: …* → **Rebuild graph** · ○ *after ingest finishes* (while row 2 is ◌) | `projection status` (extended, B3), `job-status.finalize` (B2) |
| **5 Descriptions** *(optional)* | ✓ *up to date* · ◇ *N could be synthesized · ~N LLM calls (model)* → **Synthesize…** | `projection synthesize --dry-run` per mapped domain (B5) |
| **Footer: Vault** | ✓ *shared* · ● *✎ N artmind files to commit*, with what they came from → **Commit & push** · ● *↑ N to push* → **Commit & push** | git reads (today's) |

Rules:
- **Why rows run in this order.** Apply the other laptop's changes before ingesting,
  to avoid conflicts. Ingest before rebuilding, because the rebuild needs what ingest
  wrote. Synthesize after rebuilding.
- **When the header says "Graph ready ✓".** Rows 1–4 are all ✓. Row 5 and the footer
  don't affect it.
- **When the footer is the current step.** Its button is filled only when rows 1–4 are
  all ✓. It is hidden during a job, as today (files are still being written).
- **No commit offer after table2graph.** Its output (`table__*`) is gitignored, so the
  plugin no longer offers Commit & push after it.

### 3.3 Out-of-order clicks: the confirm modal

Clicking an outline button whose prerequisite row isn't ✓ opens a small confirm modal.
Following the current step never opens it. It replaces today's notices #9 and #17.

- **Ingest while row 1 is ●:** "The other laptop has changes not applied to your
  graph." [Apply first] [Ingest anyway]
- **Apply to graph when the last pull is older than 5 minutes:** "Pull from GitHub
  first?" [Pull, then apply] [Apply without pulling]

### 3.4 Synthesize… modal

The modal shows:
- each mapped domain with its count;
- the model;
- an optional **cap** (`--limit`);
- the total LLM calls.

Buttons: [Synthesize] [Cancel]. It runs `projection synthesize --domain D [--limit N]`
for each selected domain, one after another, through the write queue. When it
finishes, it re-reads row 5 and the footer: syntheses are vault files.

## 4. Other surfaces

**Status bar.**
- Format: `artmind ●●○○○ <current step's short label>`. The five dots are rows 1–4
  and the footer, filled when done.
- The tone is the current step's.
- A click opens the panel scrolled to the current row. The tooltip is that row's
  one-line explanation.

**Ribbon.**
- A click opens the panel.
- The right-click menu keeps only *Open side panel*, *Open admin console* and *Run
  doctor*.
- The tone dot stays.

**Command palette.**
- One command per row action: *Pull*, *Apply to graph*, *Ingest what changed*,
  *Rebuild graph*, *Synthesize…*, *Commit & push*, *Review tables for graph*,
  *Resolve artmind conflicts*.
- Plus today's *Run doctor*, *Open side panel*, *Open admin console* and *Stop admin
  console*.
- The same gates and confirm modals apply. A blocked palette command shows its reason
  as a text-only notice, since the palette can't show a disabled reason.

**Settings.**
- Remove "Offer to commit-and-sync after artmind writes"; the footer replaces it.
- Keep the per-kind notice toggles, and add `rebuildDone` and `synthesizeDone`.

**Persistence.** The last job card, the last apply summary and the last rebuild
summary are saved with `saveData`, so they survive a reload.

## 5. Notices

Every notice is text only and fades after 6 s, or 10 s for an error. Clicking a notice
opens the panel at its row. `ObsidianNotifier` takes a `row` instead of `buttons`.

| Kind | Example text |
|---|---|
| Outside event | "Pulled 4 commits: 3 docs, 1 table to apply to graph." · "3 new files in Research, ready to ingest." |
| Action finished: names the next step | "Ingested 365 files, 3 failed. Next: rebuild graph." · "Applied to graph: 3 docs, 1 table." · "habits projected: 212 entities." · "Graph rebuilt: 4,332 entities, all embedded." · "Synthesized 41 descriptions. Next: commit & push." |
| Error or stall | One friendly line plus "details in the artmind panel". The row shows the error and its Retry button. "Sync failed: \<raw stderr\>" becomes a mapped message; unmapped stderr goes to the row's expandable detail, never into the notice. |
| Blocked | Only for command-palette runs. Text only. |

Every one of today's notices #1–#28 maps to a row above or to the confirm modal
(§3.3). None keeps a button.

### 5.1 State model changes (`state.ts`)

- **`StateInputs` gains:**
  - `projection` (B3's `projection status`)
  - `tablesReview` (`db review`)
  - `synthesis` (per-domain dry-run counts, or null when not read yet)
  - `lastJob`, persisted (§4)
- **A new pure `checklist(inputs)`** returns ordered `Row[]`. Each row has `id`,
  `state`, `glyph`, `tone`, `title`, `detail[]`, `items[]` (expandable paths),
  `action | null`, `blockedReason | null` and `optional`. It also returns
  `current: RowId | null` and `ready: boolean`.
- **`vaultState()` is replaced** by `checklist()`. `primary` becomes the current row,
  and `blocked` is derived from the rows' prerequisites.
- **Read cadence.**
  - `projection status`, `db review` and the synthesize dry-runs touch Neo4j and
    DuckDB, so they run on ↻, after any write action and after a job finishes. On
    focus they run at most once every 60 s.
  - The existing reads keep today's triggers.

## 6. Backend and CLI fixes

| # | Fix | Detail | Verification |
|---|---|---|---|
| **B1** | **Batched full rebuild** | `projection.full_rebuild` commits through `table2graph.batch_keys` / `_rebuild_in_batches` (`REBUILD_BATCH = 400`; it keeps a same-as group within one batch), not one transaction. `record_rebuild` runs only after the last batch succeeds, so a mid-way failure leaves drift visible. `ingest.rebuild_projection` and the worker's deferred path both use it. | Unit test: N transactions of 400 keys or fewer, with `:ProjectionState` written last and not written after a failed batch (assert on queries and parameters sent). Before/after on a **disposable** Neo4j container with a low `db.memory.transaction.total.max` and ~4,300 synthetic keys: the old path gets `MemoryLimitExceeded` and the new path passes. Never on the user's container. |
| **B2** | **The job remembers it owes a rebuild** | Persist the domains owed a rebuild and sweep on the job row: new `ingestion_jobs` columns `finalize_domains` (JSON list, appended as each file's KG commit defers), `finalize_state` and `finalize_error`, added by the existing schema migration path. A resumed or retried job finishes them even with 0 queued files. Catch exceptions around the rebuild and sweeps. `job-status` / `jobs-active` gain `finalize: {state: "none"\|"pending"\|"done"\|"failed", domains: [...], error: str\|null}`. A sweep skipped for a dead connection counts as `failed`. | Unit tests: a resumed job with 0 queued files and owed domains rebuilds; a failing rebuild leaves `finalize.failed` with the error; a failing sweep does too. |
| **B3** | **`projection status` counts what's missing** | Add `unembedded_entities` (`embedding IS NULL OR embedding_stale`), `unprojected_keys` (observation keys with no `:Entity`) and `domains: {d: {unembedded_entities, unprojected_keys, unembedded_chunks}}`. | Unit test on the Cypher sent; live check on `my_second_brain`. |
| **B4** | **`projection rebuild --sweep`** | A full (unscoped) rebuild through B1, which clears drift, then entity and chunk embed sweeps for every domain it rebuilt. Output adds `domains_swept`. This is what **Rebuild graph** runs. | Unit test that both sweeps run per domain. |
| **B5** | **Synthesize count** | The plugin runs `projection synthesize --domain D --dry-run --compact` for each mapped domain and uses `examined` / `counts`. If planning measures a dry-run as slow on a large domain, add a `--countOnly` that returns just the count. | Plugin fixture for the dry-run shape. |
| **B6** | **Own tables don't show as "to apply"** | Under `vault sync`, the structured store's table plan checks fingerprints as the document plan does (`_classify_structured_text_diff`, `vault_sync.py:566-606` and `:1726`). | **Write the failing test first:** ingest a table, commit, run `vault status`, expect `current`. If it already passes, drop B6. |
| **B7** | **Deep link for table review** | The admin console accepts `?prompt=<text>`, which prefills (never auto-sends) the agent chat. Row 3 links to `?prompt=Review the proposed classifications for table <t> (domain <d>)`. Classification review is agent work (`artmind-curate`). | Browser check in the admin-ui preview. |
| **B8** | **Plugin fixtures** | Regenerate the fixtures for `job-status`, `jobs-active`, `projection status`, `projection rebuild --sweep`, `projection synthesize --dry-run` and `db review` with `ARTMIND_REGEN_PLUGIN_FIXTURES=1`. | `test_plugin_fixtures.py`, `just obsidian-plugin-test` |
| **B9** | **Docs** | `docs/INSTALL.md`: recommended Neo4j heap and transaction-memory settings (`server.memory.heap.max_size`, `db.memory.transaction.total.max`) and the colima/Docker VM minimum, so an oversized transaction fails cleanly rather than killing the JVM. `docs/projection-pipeline.md` §4: batched full rebuild, `--sweep`, `finalize`. The `artmind-ingestion-helper` and `artmind-curate` skills: `projection rebuild --sweep`. `cli.py` help text for `projection rebuild` and `projection status`. | `just dev-cli-help` |

**Repairing the live graph** happens after B1–B4 pass. Run, in `my_second_brain`:

```bash
ARTMIND_NO_PROXY=1 artmind projection rebuild --sweep
```

Then run `projection status`, expecting 0 unembedded, 0 unprojected and no drift.

## 7. Testing

- **Plugin:**
  - `checklist()` is pure. Vitest gets one table-driven case per row state, plus
    current-step selection, `ready`, and footer emphasis.
  - Notifier: no notice has buttons; each one's click target is its row.
  - Confirm modal: opens only for out-of-order clicks.
  - Panel render test: only the current row's button is filled.
- **Python:** hermetic tests per B1–B6, asserting on the queries and parameters sent
  (CLAUDE.md §3).
- **End to end:**
  - Dev-install, then `artmind init` in the vault, so the plugin build is copied in.
  - Restart `serve` from inside the vault.
  - In Obsidian, walk the full loop: Pull → Apply to graph → Ingest → Rebuild graph →
    Synthesize… → Commit & push.
  - Check the panel, status bar and notices at each step.

## 8. Out of scope

- Automatic ingest, rebuild or synthesize (D1).
- Status dots in the file explorer.
- Auto-apply (Plan E, issue #26).
- An admin-console UI for confirming classifications; the agent does that.
- Mobile.
