# Pipeline Checklist — Plan 2: Obsidian Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plugin's command-centred side panel, status bar and button-carrying notices with the readiness checklist of spec [2026-10-03-obsidian-pipeline-checklist-design.md](../specs/2026-10-03-obsidian-pipeline-checklist-design.md): five rows plus a Vault footer, one current step, text-only notices that open the panel at their row, and the new Rebuild graph / Synthesize… / Commit & push actions.

**Architecture:** A new pure `checklist(inputs)` (`src/checklist.ts`) replaces `vaultState()`. It turns the CLI's JSON (now including `projection status`, `db review`, the synthesize dry-runs and the persisted last job / apply / rebuild) into ordered rows, the current step, `ready`, and why each command is blocked. `Controller` keeps the flows; it reads the new state on a slower cadence, runs the new writes through `ArtmindCli`'s write queue, asks a confirm modal for out-of-order clicks, and shows notices with a target row instead of buttons. The views render the checklist: `checklistView.ts` (rows and footer), `jobCard.ts` (moved out of `panel.ts`), `confirmModal.ts`, `synthesizeModal.ts`, the status bar dots and the trimmed ribbon menu.

**Tech Stack:** TypeScript 6.0, esbuild 0.28, the `obsidian` 1.13.1 typings, vitest 5 (Node; jsdom for view tests), Node 22. Fixtures are the real CLI's `--compact` JSON under `obsidian/artmind-obsidian/test/fixtures/`.

---

## Depends on Plan 1

**Plan 1 ([2026-10-03-pipeline-checklist-plan-1-backend.md](2026-10-03-pipeline-checklist-plan-1-backend.md)) must be merged first.** This plan reads the CLI shapes Plan 1 adds and the fixtures its Task 12 regenerates:

| Fixture (`test/fixtures/<name>.json`) | Shape this plan relies on |
|---|---|
| `ingest-job-status.finishing` | `status: "processing"`, every file done, `finalize: {state: "pending", domains: ["general"], error: null}` |
| `ingest-job-status.finalize-failed` | `status: "completed"`, `finalize: {state: "failed", domains: ["general"], error: "<message>"}` |
| `ingest-job-status.{done,running,stalled}`, `ingest-jobs-active`, `ingest-job-results.done` | each gains `finalize` (`state: "none"` when nothing is owed) |
| `projection-status.ready` | `known: true`, `drift: false`, `same_as_drift: false`, `schema_drift: false`, totals 0, `domains: {}`, `last_rebuilt_at` set |
| `projection-status.needs-rebuild` | `drift: true`, `same_as_drift: true`, `unembedded_entities: 2813`, `unprojected_keys: 40`, `unembedded_chunks: 310`, `domains.general`, `domains.habits` |
| `projection-rebuild.sweep` | `rebuilt`, `deleted`, `absent`, `keys`, `batches`, `domains`, `recorded: true`, `domains_swept: ["general","habits"]`, `embedded: 3`, `chunks_embedded: 6`, `sweep_errors: []`, `finalize_resolved: []` |
| `projection-synthesize.dry-run` | `domain`, `model`, `dry_run: true`, `examined: 3`, `counts: {synthesize: 1, skipped_unchanged: 1, skipped_too_few_observations: 1}`, `candidates` |
| `db-review` | `{query_type, command: "db review", pending_count ≥ 1, tables: [{table: "team", domain: "general", ...}]}` |

The CLI commands: `artmind projection status --compact`, `artmind projection rebuild --sweep --compact` (a write), `artmind projection synthesize --domain D --dry-run --compact` (a read; note `--dry-run`, not `--dryRun`), `artmind projection synthesize --domain D [--limit N] --compact` (a write), `artmind db review --compact`. The admin console prefills its agent chat from `/?prompt=<text>` and never sends it.

Task 0 checks that these fixtures exist with these shapes before anything else is written.

## Context for the implementer

Read before starting:

- The spec above, all of it. Every label, row text and notice text below comes from it.
- `CLAUDE.md` §"Testing implications". The plugin rule that matters: test what was actually sent. `test/controller.test.ts`'s `FakeCli` records every command the controller runs, and `test/cli.test.ts` logs every run of a fake `artmind`. Assert on those records.
- The existing plugin (`obsidian/artmind-obsidian/src/`) and its plan [2026-09-30-obsidian-plugin-plan-2-plugin.md](2026-09-30-obsidian-plugin-plan-2-plugin.md) for conventions: plain-DOM views via `el()` (`src/views/dom.ts`), `Snapshot` rendering, the `obsidian` stand-in in `test/mocks/obsidian.ts`.

Run the plugin's checks with:

```bash
cd obsidian/artmind-obsidian && npx vitest run            # all tests
cd obsidian/artmind-obsidian && npx vitest run test/<file> # one file
cd obsidian/artmind-obsidian && npm run typecheck          # tsc --noEmit (src AND test)
just obsidian-plugin-test                                   # npm test && npm run typecheck
```

`tsconfig.json` has `noUnusedLocals`, and it type-checks `test/` too: an unused import fails `npm run typecheck`. Each task ends with `just obsidian-plugin-test` passing. Never commit `node_modules/` or `main.js`.

## Decisions this plan makes (the spec left them open)

1. **Rows carry `buttons: RowButton[]`, not one `action`.** Row 3 can show [Review in admin console ↗] and [Review & project…] at once, and row 2 can show [Ingest N files] and [Retry failed]. The first button is the row's main action; only the current row's first button is filled (`mod-cta`).
2. **`RowId`s:** `remote` (1 From the other laptop), `documents` (2), `tables` (3), `graph` (4), `descriptions` (5), `vault` (footer). A notice or the status bar opens the panel at a `PanelTarget`: a `RowId`, or `problems` / `doctor` / `activity`.
3. **Obsidian Git's auto pull keeps row 1 current (spec D10).** The plugin never fetches (plugin spec P5), so it can't count commits not yet pulled, and row 1 has no "N commits on GitHub" state. On every refresh the plugin reads `autoPullInterval` (minutes) from `<configDir>/plugins/obsidian-git/data.json` through `app.vault.adapter.read`, the same way it re-reads the manifest. A missing file, bad JSON, or a missing or non-positive key counts as 0, Obsidian Git's default. (The user's vault today has `{"syncMethod":"merge","autoSaveInterval":10,"autoPullOnBoot":true}`, so 0.) `StateInputs.obsidianGit` is `{ autoPullMinutes }`, or null before the first read. When it is 0, row 1 adds the line "Turn on Obsidian Git's auto pull to see the other laptop's changes", and **Apply to graph** asks "Pull from GitHub first?" when the last pull seen is older than 5 minutes. When it is above 0 that modal never opens. **Pull** stays on row 1 as an outline button in every state where pulling makes sense (not during a merge or conflict); it is the main button only when a bookmark is not in this clone's history (`not_ancestor`).
4. **Ingest no longer asks to pull.** Its confirm modal asks only "apply first" (row 1 ●). [Apply first] goes through Apply's own pull check.
5. **A job is running while `finalize.state == "pending"`**, even after its files are done: the job poll continues, row 2 says *finishing: building the graph*, row 4 waits, and the footer stays hidden.
6. **The 30 s own-write grace is removed.** The footer exists to show what the plugin's own ingest and synthesize wrote, so those changes count at once. The footer is hidden while a job runs.
7. **`git status` lists every untracked file** (`--untracked-files=all`), so the footer can say "from: ingest (365 files) · syntheses (41)": a path under `.artmind/data/kg/<domain>/<doc>/` counts once per doc, a path under `.artmind/data/curation/syntheses/` as one synthesis.
8. **Synthesize counts come from `counts.synthesize`** of each mapped domain's dry run. The mapped domains are the distinct `domain`s of `.artmind/vault.yaml`'s `ingest.mappings`. A domain whose dry run fails is left out (its error goes to the problems callout). The modal's cap is `--limit`, per domain; the total is `Σ min(count, cap)`.
9. **A rebuild whose `sweep_errors` is non-empty** leaves row 4 at *needs rebuild*, each sweep error a reason line, and its notice is an error. A clean `--sweep` (Plan 1 marks failed jobs `done`) also clears the "last job's rebuild failed" reason: that reason shows only while no successful, error-free rebuild is newer than the last job.
10. **Row 3 lists ambiguous tables** (two mappings match) as "● N with two mappings → Ask admin-ui", with a `/?prompt=` asking to narrow one. `table2graph --pending` never lists a table with no mapping; that case stays inside today's `TableReviewModal` (its [Ask admin-ui to create one]).
11. **The bootstrap text moves into row 1's detail** (the spec calls row 1's state "today's bootstrap callout text"), and the separate bootstrap callout goes. The problems, doctor and activity callouts stay below the footer, plus a collapsed "Stores" section (today's "Sync" section, renamed: no label says "sync").
12. **Obsidian Git's own command names stay in instructions.** When its command is missing, the button reads "Run Obsidian Git: Commit-and-sync": that is the name to search for in its palette. Every artmind label is from `texts.ts`.
13. **Heavy reads** (`projection status`, `db review`, the dry runs) run on the first refresh, on ↻, after every write action and job finish, and on focus when the last heavy read is at least 60 s old. Other triggers (HEAD settled, new files) run only today's reads.
14. **Persistence:** `saveData` stores `{...settings, state: {lastJob, lastApply, lastRebuild}}`. `mergeSettings` ignores `state`; `readPersisted` validates it field by field.
15. **"Pulled 4 commits: …" becomes "Pulled: 3 docs, 1 table to apply to graph."** The commit count would need another git read for a number the row does not use.

## File structure

| File | Task | Responsibility |
|---|---|---|
| `src/types.ts` | 1 | `JobFinalize`, `ProjectionStatus`, `RebuildResult`, `SynthesizeDryRun`, `SynthesizeResult`, `TableToReview`, `DbReview` |
| `src/cli.ts` | 1 | `projectionStatus`, `projectionRebuildSweep`, `synthesizeDryRun`, `synthesize`, `dbReview`; timeouts; `projection rebuild`/`synthesize` are writes, `--dry-run` reads |
| `src/state.ts` | 2, 9, 10 | `StateInputs` (extended), `EMPTY_INPUTS`, `RowId`, `PanelTarget`, `LastJob`, `Outcome`, `RebuildOutcome`, `Busy`; `classifyRefusal`; `vaultState` deleted in Task 10 |
| `src/git.ts` | 3 | `--untracked-files=all` |
| `src/bridge.ts` | 3b | `obsidianGitDataPath`, `autoPullMinutes`: Obsidian Git's `autoPullInterval` from its `data.json` |
| `src/texts.ts` | 4 | every label (`LABELS`, `COMMANDS`, `ROW_TITLES`, `CONFIRM`, `NOTICE`), notice texts, `failedText`, `nextStepText`, `withNext`, `readinessText` |
| `src/checklist.ts` (new) | 5 | pure `checklist(inputs)`, `jobRunning`, `changeSources`, `rebuildReasons` |
| `src/persist.ts` (new) | 6 | `Persisted`, `readPersisted`, `withPersisted` |
| `src/admin.ts` | 6 | `promptPath(prompt)` |
| `src/views/statusBar.ts` | 7 | `artmind ●●○○○ <current>`; click opens the panel |
| `src/views/ribbon.ts` | 8 | three menu items; tone from the current row |
| `src/views/confirmModal.ts` (new) | 9 | `ConfirmRequest`, `renderConfirm`, `ConfirmModal` |
| `src/views/synthesizeModal.ts` (new) | 9 | `llmCalls`, `renderSynthesize`, `SynthesizeModal` |
| `src/settings.ts`, `src/views/settingsTab.ts` | 10 | drop `offerCommitAndSync`; notice kinds `applyDone`, `rebuildDone`, `synthesizeDone` |
| `src/views/notices.ts` | 10 | text-only notices; click opens the target |
| `src/controller.ts` | 10 | the flows, rewritten around the checklist |
| `src/views/sections.ts` (new) | 11 | `PanelUi` (collapsed sections, expanded rows), `section()`, `basename()` |
| `src/views/jobCard.ts` (new) | 11 | the job card, moved out of `panel.ts` |
| `src/views/checklistView.ts` (new) | 11 | header, rows, footer |
| `src/views/panel.ts` | 11 | `renderPanel` = checklist + tools + callouts + Stores + Activity; `ArtmindView.focus(target)` |
| `src/main.ts` | 7, 8, 10, 11 | wiring; palette commands; persistence; reads Obsidian Git's `data.json` on refresh |
| `styles.css` | 12 | checklist styles |
| `docs/USER_GUIDE_TWO_LAPTOPS.md` | 13 | §3.1 rewritten for the checklist |
| `test/*.test.ts` | each task | as named per task; new `test/checklist.test.ts`, `test/persist.test.ts`, `test/modals.test.ts`, `test/styles.test.ts` |

`panel.ts` drops from 433 lines to about 210; `checklistView.ts`, `jobCard.ts` and `sections.ts` take the rest.

## Every notice today, and what it becomes

Line numbers are `src/controller.ts` / `src/main.ts` before this plan. "→ row" is the notice's click target. No notice keeps a button.

| # | Where | Today | Becomes |
|---|---|---|---|
| 1 | controller 311 | "Pulled from the other laptop — … behind." [Sync now] | `pulled`: "Pulled: 2 docs, 1 table to apply to graph." → remote |
| 2 | controller 316 | "N new files in X." [Ingest now] | `newFiles`: "2 new files in Team, ready to ingest." → documents |
| 3 | controller 335 | "Ingest stalled at …" [Retry] | `error`: "Ingest stalled at 2 of 5 files — details in the artmind panel." → documents (row has [Retry job]) |
| 4 | controller 351 | "Ingested 4, 1 failed." [Details][Commit-and-sync] | `ingestDone`: "Ingested 4 files, 1 failed. Next: rebuild graph." → documents |
| 5 | controller 379 | "Retry failed: <raw>" | `error`: "Couldn't retry the job — details in the artmind panel." → documents; raw in the row detail |
| 6 | controller 384 | "Nothing to retry in that job." | unchanged text, `prompt` → documents |
| 7 | controller 400 | "Retrying N failed files." | unchanged, `prompt` → documents |
| 8 | controller 433 | Obsidian Git instruction | unchanged text, `error` → the row whose button it was |
| 9 | controller 449 | "Pull from GitHub first?" [Pull, then sync][Sync without pulling] | confirm modal: "Pull from GitHub first?" [Pull, then apply][Apply without pulling], only when Obsidian Git's auto pull is off and the last pull seen is older than 5 minutes (decision 3) |
| 10 | controller 466 | "Synced: …" | `applyDone`: "Applied to graph: 2 docs, 1 table." (+ " Next: …") → remote |
| 11 | controller 500 | "Ingest in progress — will sync when it finishes" | `prompt`: "Ingest in progress — will apply to graph when it finishes." → remote |
| 12 | controller 478 | "Resolve first" [Resolve] | `prompt`: "Resolve the artmind conflicts first." → remote (row has [Resolve…]) |
| 13 | controller 481 | "Pull first" [Pull] | `prompt`: "Pull first: the other laptop's commits aren't in this clone." → remote |
| 14 | controller 488 | bootstrap text [Open panel] | `prompt`: "This laptop has never applied this vault — see the first step in the artmind panel." → remote |
| 15 | controller 491 | "Sync failed: <raw>" [Details] | `error`: "Couldn't apply to graph — details in the artmind panel." (or ": Neo4j is unreachable" / ": it timed out") → remote; raw in the row detail |
| 16 | controller 510 | the blocked reason | `blocked`: the reason, text only → documents |
| 17 | controller 514 | "Pull from GitHub first?" [Pull, then ingest][Ingest without pulling] | removed (decision 4) |
| 18 | controller 522 | "Sync first? …" [Sync, then ingest][Ingest anyway] | confirm modal: "The other laptop has changes not applied to your graph." [Apply first][Ingest anyway] |
| 19 | controller 528 | Neo4j-unreachable warning | unchanged text, `prompt` → documents |
| 20 | controller 533 | "Ingest failed: <raw>" | `error`: "Couldn't start the ingest — details in the artmind panel." → documents |
| 21 | controller 538 | "Nothing new or changed to ingest." | unchanged, `prompt` → documents |
| 22 | controller 576 | "table2graph failed: <raw>" | `error`: "Couldn't project team — details in the artmind panel." → tables |
| 23 | controller 580 | "team projected: 7 entities." [Commit-and-sync] | `table2graphDone`: "team projected: 7 entities." (+ " Next: …"), no commit offer → tables |
| 24 | controller 599 | "vault resolve failed: <raw>" | `error`: "Couldn't resolve the artmind files — details in the artmind panel." → remote |
| 25 | controller 604 | "Artmind files resolved." [Complete the merge] | `resolveDone`: "Artmind files resolved. Next: complete the merge." → remote (row has [Complete the merge]) |
| 26 | controller 617 | "vault doctor failed: <raw>" | `error`: "Couldn't run the doctor — details in the artmind panel." → doctor |
| 27 | main 207 | "Opening the admin console…" | unchanged, `prompt`, no target |
| 28 | main 210, 225 | admin start / stop outcomes | unchanged, no target |
| new | — | — | `rebuildDone`: "Graph rebuilt: 4,332 entities, all embedded." (+ " Next: …") → graph; a failure or sweep errors → `error` → graph |
| new | — | — | `synthesizeDone`: "Synthesized 41 descriptions. Next: commit & push." → descriptions |
| new | — | — | `blocked`: a palette command's reason, text only → its row |

Notices fade after 6 s, or 10 s for an error.

---

### Task 0: Plan 1's fixtures are present

Nothing below works without them. This task writes no code.

**Files:**
- Read: `obsidian/artmind-obsidian/test/fixtures/*.json`

- [ ] **Step 1: Check the fixtures exist and have the shapes this plan reads**

Run from the repo root:

```bash
cd obsidian/artmind-obsidian/test/fixtures && python3 - <<'PY'
import json
def j(name): return json.load(open(f"{name}.json"))["json"]
fin = j("ingest-job-status.finishing")
assert fin["status"] == "processing" and fin["finalize"]["state"] == "pending", fin["finalize"]
failed = j("ingest-job-status.finalize-failed")
assert failed["status"] == "completed" and failed["finalize"]["state"] == "failed" and failed["finalize"]["error"]
for name in ("ingest-job-status.done", "ingest-job-status.running", "ingest-job-status.stalled", "ingest-job-results.done"):
    assert j(name)["finalize"]["state"] == "none", name
assert j("ingest-jobs-active")[0]["finalize"]["state"] == "none"
ready = j("projection-status.ready")
assert ready["known"] and not ready["drift"] and ready["unembedded_entities"] == 0 and ready["domains"] == {} and ready["last_rebuilt_at"]
nr = j("projection-status.needs-rebuild")
assert nr["drift"] and nr["same_as_drift"] and not nr["schema_drift"]
assert (nr["unembedded_entities"], nr["unembedded_chunks"], nr["unprojected_keys"]) == (2813, 310, 40)
sweep = j("projection-rebuild.sweep")
assert sweep["domains_swept"] == ["general", "habits"] and sweep["sweep_errors"] == [] and sweep["finalize_resolved"] == []
assert isinstance(sweep["rebuilt"], int)
dry = j("projection-synthesize.dry-run")
assert dry["dry_run"] is True and dry["domain"] == "general" and dry["counts"]["synthesize"] == 1 and dry["model"]
review = j("db-review")
assert review["command"] == "db review" and review["pending_count"] >= 1 and review["tables"][0]["table"] == "team"
print("Plan 1 fixtures: OK")
PY
```

Expected: `Plan 1 fixtures: OK`. If a file is missing, Plan 1's Task 12 has not been merged: stop and merge it first. If an assertion fails, Plan 1 changed a shape: fix the type in Task 1 and every expectation that names that value before going on.

- [ ] **Step 2: Check the suite is green before starting**

Run: `just obsidian-plugin-test`
Expected: PASS (every vitest file, then `tsc --noEmit`).

---

### Task 1: The new CLI shapes and commands (`types.ts`, `cli.ts`)

**Files:**
- Modify: `obsidian/artmind-obsidian/src/types.ts`
- Modify: `obsidian/artmind-obsidian/src/cli.ts:71-104` (timeouts, writes) and `:288-301` (methods)
- Test: `obsidian/artmind-obsidian/test/cli.test.ts`

- [ ] **Step 1: Write the failing tests**

In `test/cli.test.ts`, change the import from `../src/cli` to:

```ts
import {
  ArtmindCli,
  type DetectDeps,
  SYNTHESIZE_DRY_RUN_TIMEOUT_MS,
  TIMEOUTS_MS,
  childEnv,
  commandKey,
  detectArtmind,
  errorText,
  isWrite,
} from "../src/cli";
```

In `describe("which commands write", ...)`, add these rows to the `it.each([...])` list, after `[["--version"], false],`:

```ts
    [["projection", "rebuild", "--sweep", "--compact"], true],
    [["projection", "synthesize", "--domain", "general", "--compact"], true],
    [["projection", "synthesize", "--domain", "general", "--limit", "5", "--compact"], true],
    [["projection", "synthesize", "--domain", "general", "--dry-run", "--compact"], false],
    [["projection", "status", "--compact"], false],
    [["db", "review", "--compact"], false],
```

Append to the file:

```ts
describe("the readiness checklist's commands (Plan 1)", () => {
  it("sends exactly these args, with --compact added", async () => {
    const { cli, lines } = fakeCli([{ match: "", stdout: "{}" }]);

    await cli.projectionStatus();
    await cli.projectionRebuildSweep();
    await cli.synthesizeDryRun("general");
    await cli.synthesize("general", null);
    await cli.synthesize("habits", 20);
    await cli.dbReview();

    expect(lines().filter((l) => l.startsWith("start "))).toEqual([
      "start projection status --compact",
      "start projection rebuild --sweep --compact",
      "start projection synthesize --domain general --dry-run --compact",
      "start projection synthesize --domain general --compact",
      "start projection synthesize --domain habits --limit 20 --compact",
      "start db review --compact",
    ]);
  });

  it("gives the long writes long timeouts, and the reads short ones", () => {
    expect(TIMEOUTS_MS["projection rebuild"]).toBe(2 * 60 * 60_000);
    expect(TIMEOUTS_MS["projection synthesize"]).toBe(60 * 60_000);
    expect(TIMEOUTS_MS["projection status"]).toBe(2 * 60_000);
    expect(TIMEOUTS_MS["db review"]).toBe(60_000);
    expect(SYNTHESIZE_DRY_RUN_TIMEOUT_MS).toBe(5 * 60_000);
  });

  it("queues a rebuild behind a running apply, as every write is", async () => {
    const { cli, lines } = fakeCli([
      { match: "vault sync", stdout: "{}", delayMs: 300 },
      { match: "projection rebuild", stdout: "{}" },
    ]);

    await Promise.all([cli.sync(), cli.projectionRebuildSweep()]);

    expect(lines()).toEqual([
      "start vault sync --compact",
      "end vault sync --compact",
      "start projection rebuild --sweep --compact",
      "end projection rebuild --sweep --compact",
    ]);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/cli.test.ts`
Expected: FAIL. The new `isWrite` rows for `projection rebuild` and `projection synthesize` fail (`expected false to be true`); the new describe fails with `TypeError: cli.projectionStatus is not a function`; the timeout test fails on `undefined`.

- [ ] **Step 3: Add the types**

In `src/types.ts`, insert before `export interface JobStatus {`:

```ts
/** What a job still owes once its files are done: the deferred projection
 * rebuild and embed sweeps for the domains it touched (Plan 1, B2).
 * `pending` while the worker runs them; `failed` with the error otherwise. */
export interface JobFinalize {
  state: "none" | "pending" | "done" | "failed";
  domains: string[];
  error: string | null;
}
```

In `JobStatus`, after `stalled?: boolean;` add:

```ts
  /** Absent from an artmind older than Plan 1. */
  finalize?: JobFinalize;
```

In `JobResults`, after `file_count: number;` add:

```ts
  finalize?: JobFinalize;
```

Append to `src/types.ts`:

```ts
export interface ProjectionDomainGaps {
  unembedded_entities: number;
  unprojected_keys: number;
  unembedded_chunks: number;
}

/** `artmind projection status --compact` (Plan 1, B3). `known` is false
 * before the first full rebuild; then the recorded hashes are absent. */
export interface ProjectionStatus {
  known: boolean;
  drift: boolean;
  last_rebuilt_at?: string | null;
  same_as_drift: boolean;
  schema_drift: boolean;
  recorded_same_as_hash?: string | null;
  current_same_as_hash: string;
  recorded_schema_hash?: string | null;
  current_schema_hash: string;
  unembedded_entities: number;
  unprojected_keys: number;
  unembedded_chunks: number;
  domains: Record<string, ProjectionDomainGaps>;
}

/** `artmind projection rebuild --sweep --compact` (Plan 1, B4).
 * `sweep_errors` are `"<domain>: <message>"`; empty when every sweep ran. */
export interface RebuildResult {
  rebuilt: number;
  deleted: number;
  absent: number;
  keys: number;
  batches: number;
  domains?: string[];
  recorded?: boolean;
  domains_swept: string[];
  embedded: number;
  chunks_embedded: number;
  sweep_errors: string[];
  finalize_resolved: string[];
}

/** `artmind projection synthesize --domain D --dry-run --compact`.
 * `counts.synthesize` is how many descriptions a real run would write. */
export interface SynthesizeDryRun {
  domain: string;
  command: string;
  model: string;
  dry_run: true;
  examined: number;
  counts: Record<string, number>;
  candidates?: Array<{ key: string; name: string }>;
}

/** `artmind projection synthesize --domain D [--limit N] --compact`. */
export interface SynthesizeResult {
  domain: string;
  command: string;
  model: string;
  dry_run: false;
  examined: number;
  synthesized: number;
  counts: Record<string, number>;
  results: unknown[];
}

/** One table of `artmind db review --compact`: what is still unconfirmed. */
export interface TableToReview {
  table: string;
  domain: string;
  grain: string | null;
  grain_confirmed: boolean;
  grain_status: string | null;
  bridge_status: string | null;
  mapping_status: string | null;
  mappings: Array<{ column: string; entity_class: string; confidence: number | null }>;
  bridge_columns: Array<{ column: string; confidence: number | null }>;
}

/** `artmind db review --compact`. An empty `tables` means nothing to review. */
export interface DbReview {
  query_type: string;
  command: string;
  pending_count: number;
  tables: TableToReview[];
}
```

- [ ] **Step 4: Add the commands to `ArtmindCli`**

In `src/cli.ts`, replace `TIMEOUTS_MS` and `WRITE_COMMANDS` (lines 71–91) with:

```ts
/** Per-command limits. A sync or projection of a big vault is slow; a
 * status read is not, and must never hold the status bar up for long. */
export const TIMEOUTS_MS: Record<string, number> = {
  "--version": 15_000,
  "vault status": 60_000,
  "vault sync": 60 * 60_000,
  "vault resolve": 5 * 60_000,
  "vault doctor": 60_000,
  "ingest pending": 2 * 60_000,
  "ingest async": 2 * 60_000,
  "ingest job-status": 30_000,
  "ingest job-results": 30_000,
  "ingest jobs-active": 30_000,
  "ingest retry-job": 30_000,
  "ingest table2graph": 60 * 60_000,
  "projection status": 2 * 60_000,
  // A full rebuild of a large vault, in batches, then both embed sweeps.
  "projection rebuild": 2 * 60 * 60_000,
  "projection synthesize": 60 * 60_000,
  "db review": 60_000,
};
export const DEFAULT_TIMEOUT_MS = 5 * 60_000;
/** `projection synthesize --dry-run` shares its key with the real run, but
 * makes one read and no LLM call. */
export const SYNTHESIZE_DRY_RUN_TIMEOUT_MS = 5 * 60_000;

// `ingest retry-job` starts a worker, as `ingest async` does: detached, so
// the worker outlives the call.
const WRITE_COMMANDS = new Set([
  "vault sync",
  "vault resolve",
  "ingest async",
  "ingest retry-job",
  "ingest table2graph",
  "projection rebuild",
  "projection synthesize",
]);
```

Replace `isWrite` (lines 97–104) with:

```ts
/** Writes go through one queue, one at a time (spec §7): sync, ingest
 * submission, table2graph, resolve, rebuild and synthesize. Their dry runs
 * (`--dryRun`, or synthesize's `--dry-run`), and `table2graph --pending`,
 * only read. */
export function isWrite(args: string[]): boolean {
  const key = commandKey(args);
  if (!WRITE_COMMANDS.has(key) || args.includes("--dryRun") || args.includes("--dry-run")) return false;
  return !(key === "ingest table2graph" && args.includes("--pending"));
}
```

After `tableProject(table: string) { ... }` (line 301), before the class's closing `}`, add:

```ts
  projectionStatus() { return this.run(["projection", "status"]); }
  projectionRebuildSweep() { return this.run(["projection", "rebuild", "--sweep"]); }
  synthesizeDryRun(domain: string) {
    return this.run(["projection", "synthesize", "--domain", domain, "--dry-run"], { timeoutMs: SYNTHESIZE_DRY_RUN_TIMEOUT_MS });
  }
  synthesize(domain: string, limit: number | null) {
    return this.run(["projection", "synthesize", "--domain", domain, ...(limit === null ? [] : ["--limit", String(limit)])]);
  }
  dbReview() { return this.run(["db", "review"]); }
```

- [ ] **Step 5: Run the tests**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/cli.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add obsidian/artmind-obsidian/src/types.ts obsidian/artmind-obsidian/src/cli.ts obsidian/artmind-obsidian/test/cli.test.ts
git commit -m "feat(obsidian): types and CLI calls for projection status/rebuild/synthesize and db review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `StateInputs` carries everything the checklist reads

`vaultState` keeps working unchanged until Task 11 deletes it; this task only widens its input. `git.unsharedArtmindChanges: number` becomes `git.artmindChanges: string[]`, because the footer says where the changes came from.

**Files:**
- Modify: `obsidian/artmind-obsidian/src/state.ts:1-23` (imports, `StateInputs`), `:250` (`secondary`)
- Modify: `obsidian/artmind-obsidian/src/controller.ts:136-143` (initial inputs), `:292-299` (refresh)
- Modify: `obsidian/artmind-obsidian/src/views/panel.ts:127`
- Test: `obsidian/artmind-obsidian/test/state.test.ts`, `test/views.test.ts`, `test/ribbon.test.ts`

- [ ] **Step 1: Write the failing test**

In `test/state.test.ts`, change the import from `../src/state` to:

```ts
import { type ArtmindProblem, EMPTY_INPUTS, type StateInputs, type StateKind, classifyRefusal, vaultState } from "../src/state";
```

and append:

```ts
describe("StateInputs", () => {
  it("starts empty: nothing read, nothing running, nothing remembered", () => {
    expect(EMPTY_INPUTS).toEqual({
      status: null,
      activeJob: null,
      pending: null,
      tablesPending: null,
      git: { ahead: null, artmindChanges: [] },
      problem: null,
      projection: null,
      tablesReview: null,
      synthesis: null,
      lastJob: null,
      lastApply: null,
      lastRebuild: null,
      busy: { apply: false, rebuild: false, synthesize: false },
      actionErrors: {},
      obsidianGit: null,
    });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/state.test.ts`
Expected: FAIL: `expected undefined to deeply equal {...}` (`EMPTY_INPUTS` does not exist).

- [ ] **Step 3: Widen `StateInputs`**

In `src/state.ts`, replace lines 1–23 (the import through the end of `interface StateInputs`) with:

```ts
import type {
  DbReview,
  IngestPending,
  JobResults,
  JobStatus,
  ProjectionStatus,
  StoreReport,
  TablePending,
  VaultStatus,
} from "./types";

/** Why artmind itself cannot be used (spec §8). */
export type ArtmindProblem =
  | { kind: "missing"; looked: string[] }
  | { kind: "too_old"; found: string; need: string }
  | { kind: "failed"; command: string; message: string };

/** The checklist's rows (checklist spec §3.2), top to bottom; `vault` is the footer. */
export const ROW_IDS = ["remote", "documents", "tables", "graph", "descriptions", "vault"] as const;
export type RowId = (typeof ROW_IDS)[number];

/** Where a notice or the status bar opens the panel: a row, or a callout. */
export type PanelTarget = RowId | "problems" | "doctor" | "activity";

/** The last ingest job that finished, saved with `saveData` so its card
 * and its failures survive a reload. `results` is null when
 * `job-results` failed. */
export interface LastJob {
  status: JobStatus;
  results: JobResults | null;
  at: number;
}

/** How a write action ended. `summary` is the notice text on success, the
 * raw error on failure (shown only in its row's detail). */
export interface Outcome {
  at: number;
  ok: boolean;
  summary: string;
}

/** A rebuild's outcome, with the embed sweeps that could not run. */
export interface RebuildOutcome extends Outcome {
  errors: string[];
}

/** One mapped domain's `projection synthesize --dry-run`. */
export interface DomainSynthesis {
  domain: string;
  count: number;
  model: string;
}

/** A plugin write still running: its row shows ◌, its button is blocked. */
export interface Busy {
  apply: boolean;
  rebuild: boolean;
  synthesize: boolean;
}

/** Everything `checklist` (and, until Task 11, `vaultState`) decides from. */
export interface StateInputs {
  /** `vault status --compact`, or null before the first read. */
  status: VaultStatus | null;
  /** The ingest job being tracked, while it is queued, processing or finalizing. */
  activeJob: JobStatus | null;
  /** `ingest pending --compact`. */
  pending: IngestPending | null;
  /** `ingest table2graph --pending --compact`. */
  tablesPending: TablePending[] | null;
  /** Read-only git facts: commits not pushed (null: no upstream) and the
   * uncommitted paths under `.artmind/`. */
  git: { ahead: number | null; artmindChanges: string[] };
  problem: ArtmindProblem | null;
  /** `projection status --compact`. */
  projection: ProjectionStatus | null;
  /** `db review --compact`. */
  tablesReview: DbReview | null;
  /** Per mapped domain, or null when not counted yet. */
  synthesis: DomainSynthesis[] | null;
  lastJob: LastJob | null;
  lastApply: Outcome | null;
  lastRebuild: RebuildOutcome | null;
  busy: Busy;
  /** The raw error of each row's last failed action: its row's detail, never a notice. */
  actionErrors: Partial<Record<RowId, string>>;
  /** Obsidian Git's `autoPullInterval` (0: off), or null before the first read (D10). */
  obsidianGit: { autoPullMinutes: number } | null;
}

export const NOT_BUSY: Busy = { apply: false, rebuild: false, synthesize: false };

export const EMPTY_INPUTS: StateInputs = {
  status: null,
  activeJob: null,
  pending: null,
  tablesPending: null,
  git: { ahead: null, artmindChanges: [] },
  problem: null,
  projection: null,
  tablesReview: null,
  synthesis: null,
  lastJob: null,
  lastApply: null,
  lastRebuild: null,
  busy: NOT_BUSY,
  actionErrors: {},
  obsidianGit: null,
};
```

In `vaultState`, replace

```ts
  if (inputs.git.unsharedArtmindChanges) secondary.push("✎ artmind changes to share");
```

with

```ts
  if (inputs.git.artmindChanges.length) secondary.push("✎ artmind changes to share");
```

- [ ] **Step 4: Follow the rename in the controller and the panel**

In `src/controller.ts`, add `EMPTY_INPUTS,` to the import list from `./state`, and replace the initial `inputs` (lines 136–143) with:

```ts
  inputs: StateInputs = { ...EMPTY_INPUTS };
```

In `refresh()`, replace the `this.inputs = { ... };` assignment (lines 292–299) with:

```ts
    this.inputs = {
      ...this.inputs,
      status: status.ok ? (status.json as VaultStatus) : this.inputs.status,
      activeJob,
      pending: pending.ok ? (pending.json as IngestPending) : null,
      tablesPending: tables.ok ? (tables.json as TablePending[]) : null,
      git: { ahead, artmindChanges: ownWrite ? [] : changes },
      problem: status.ok ? null : { kind: "failed", command: "vault status", message: status.error ?? "failed" },
    };
```

In `src/views/panel.ts`, replace line 127

```ts
  if (inputs.git.unsharedArtmindChanges) {
```

with

```ts
  if (inputs.git.artmindChanges.length) {
```

- [ ] **Step 5: Follow the rename in the tests**

Run from the repo root:

```bash
cd obsidian/artmind-obsidian && perl -pi -e 's/unsharedArtmindChanges: 0\b/artmindChanges: []/g; s/unsharedArtmindChanges: 1\b/artmindChanges: [".artmind\/a.json"]/g; s/unsharedArtmindChanges: 2\b/artmindChanges: [".artmind\/a.json", ".artmind\/b.json"]/g' test/state.test.ts test/views.test.ts test/ribbon.test.ts
grep -rn unsharedArtmindChanges src test
```

Expected: `grep` prints nothing.

Then give each test helper the new fields. In `test/state.test.ts`, in `function inputs(...)`, change `return {` to `return { ...EMPTY_INPUTS,`. In `test/views.test.ts`, change the import from `../src/state` to `import { EMPTY_INPUTS, type StateInputs, vaultState } from "../src/state";` and in `function snapshot(...)` change `const inputs: StateInputs = {` to `const inputs: StateInputs = { ...EMPTY_INPUTS,`. In `test/ribbon.test.ts`, change the import to `import { EMPTY_INPUTS, type StateInputs, vaultState } from "../src/state";` and change `return vaultState({` to `return vaultState({ ...EMPTY_INPUTS,`.

- [ ] **Step 6: Run the tests**

Run: `just obsidian-plugin-test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add obsidian/artmind-obsidian/src/state.ts obsidian/artmind-obsidian/src/controller.ts obsidian/artmind-obsidian/src/views/panel.ts obsidian/artmind-obsidian/test/state.test.ts obsidian/artmind-obsidian/test/views.test.ts obsidian/artmind-obsidian/test/ribbon.test.ts
git commit -m "refactor(obsidian): StateInputs carries projection, table review, synthesis, last job/apply/rebuild

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `git status` lists every untracked file under `.artmind/`

Without `--untracked-files=all`, a new doc folder shows as one line (`.artmind/data/kg/general/`), and the footer could not say how many docs an ingest wrote.

**Files:**
- Modify: `obsidian/artmind-obsidian/src/git.ts:7-11`
- Test: `obsidian/artmind-obsidian/test/git.test.ts`

- [ ] **Step 1: Write the failing test**

In `test/git.test.ts`, in `it("lists uncommitted changes under .artmind only, names with spaces whole", ...)`, replace

```ts
    expect(await new GitReader(clone).artmindChanges()).toEqual([".artmind/"]);
```

with

```ts
    expect(await new GitReader(clone).artmindChanges()).toEqual([".artmind/data/a record.json"]);
```

and append inside `describe("GitReader", ...)`:

```ts
  it("lists each untracked file of a new doc folder, so the footer can count docs", async () => {
    const { clone } = repo();
    const doc = join(clone, ".artmind", "data", "kg", "general", "doc1");
    mkdirSync(doc, { recursive: true });
    writeFileSync(join(doc, "document.json"), "{}");
    writeFileSync(join(doc, "chunks.json"), "[]");

    expect((await new GitReader(clone).artmindChanges()).sort()).toEqual([
      ".artmind/data/kg/general/doc1/chunks.json",
      ".artmind/data/kg/general/doc1/document.json",
    ]);
  });
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/git.test.ts`
Expected: FAIL: `expected [ '.artmind/' ] to deeply equal [ '.artmind/data/a record.json' ]`, and the new test gets `[ '.artmind/' ]`.

- [ ] **Step 3: Implement**

In `src/git.ts`, replace `GIT_READS` (lines 7–11) with:

```ts
export const GIT_READS = [
  ["rev-parse", "HEAD"],
  ["rev-list", "--count", "@{upstream}..HEAD"],
  // Every untracked file, not its folder: the footer counts docs.
  ["status", "--porcelain", "-z", "--untracked-files=all", "--", ".artmind"],
] as const;
```

- [ ] **Step 4: Run the tests**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/git.test.ts`
Expected: PASS (including "only ever reads": `status` is not a write).

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/src/git.ts obsidian/artmind-obsidian/test/git.test.ts
git commit -m "feat(obsidian): list every untracked .artmind file, for the footer's counts

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3b: Obsidian Git's auto pull interval (spec D10)

Obsidian Git keeps its settings in `<configDir>/plugins/obsidian-git/data.json`, and `autoPullInterval` (minutes; 0 when absent) is what this plan needs. Task 10 has `main.ts` read the file on every refresh through `app.vault.adapter.read`, since Obsidian does not index `.obsidian/`. This task adds only the pure parser.

**Files:**
- Modify: `obsidian/artmind-obsidian/src/bridge.ts` (append)
- Test: `obsidian/artmind-obsidian/test/bridge.test.ts`

- [ ] **Step 1: Write the failing test**

In `test/bridge.test.ts`, change the import from `../src/bridge` to:

```ts
import { type CommandsLike, INSTRUCTIONS, KNOWN_IDS, ObsidianGitBridge, autoPullMinutes, obsidianGitDataPath } from "../src/bridge";
```

and append:

```ts
describe("Obsidian Git's auto pull (checklist spec D10)", () => {
  it("lives in the vault's config dir", () => {
    expect(obsidianGitDataPath(".obsidian")).toBe(".obsidian/plugins/obsidian-git/data.json");
  });

  it("reads autoPullInterval in minutes", () => {
    expect(autoPullMinutes('{"syncMethod":"merge","autoPullInterval":10}')).toBe(10);
  });

  it("counts as off when the key, the file or the JSON is missing or wrong", () => {
    // The user's vault today (Obsidian Git 2.41.1): no autoPullInterval, so its default, 0.
    expect(autoPullMinutes('{"syncMethod":"merge","autoSaveInterval":10,"autoPullOnBoot":true}')).toBe(0);
    expect(autoPullMinutes(null)).toBe(0);
    expect(autoPullMinutes("")).toBe(0);
    expect(autoPullMinutes("{not json")).toBe(0);
    expect(autoPullMinutes("null")).toBe(0);
    expect(autoPullMinutes('{"autoPullInterval":"10"}')).toBe(0);
    expect(autoPullMinutes('{"autoPullInterval":-5}')).toBe(0);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/bridge.test.ts`
Expected: FAIL: `autoPullMinutes is not a function`.

- [ ] **Step 3: Implement**

Append to `src/bridge.ts`:

```ts
/** Obsidian Git's settings file, under the vault's config dir (`app.vault.configDir`). */
export function obsidianGitDataPath(configDir: string): string {
  return `${configDir}/plugins/obsidian-git/data.json`;
}

/** `autoPullInterval` (minutes) from Obsidian Git's `data.json`: 0, its
 * default, when the file is missing, isn't JSON, or holds no positive
 * number there. Above 0, pulls reach row 1 by themselves (spec D10). */
export function autoPullMinutes(text: string | null): number {
  if (!text) return 0;
  try {
    const value = (JSON.parse(text) as { autoPullInterval?: unknown } | null)?.autoPullInterval;
    return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}
```

- [ ] **Step 4: Run the tests**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/bridge.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/src/bridge.ts obsidian/artmind-obsidian/test/bridge.test.ts
git commit -m "feat(obsidian): read Obsidian Git's autoPullInterval from its data.json

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Every label, and the new notice texts (`texts.ts`)

Additive: today's `pulledText`, `newFilesText`, `ingestDoneText`, `stalledText` and `syncDoneText` stay until Task 10 rewrites them with the controller.

**Files:**
- Modify: `obsidian/artmind-obsidian/src/texts.ts`
- Test: `obsidian/artmind-obsidian/test/texts.test.ts`

- [ ] **Step 1: Write the failing tests**

In `test/texts.test.ts`, replace the import line from `../src/texts` with:

```ts
import {
  AUTO_PULL_HINT,
  COMMANDS,
  CONFIRM,
  LABELS,
  NOTICE,
  ROW_TITLES,
  applyDoneText,
  count,
  failedText,
  ingestDoneText,
  ingestLabel,
  newFilesText,
  projectedText,
  pulledText,
  rebuiltText,
  resolvedText,
  synthesizedText,
  syncDoneText,
} from "../src/texts";
```

Append:

```ts
describe("labels (checklist spec D6)", () => {
  it("never say sync", () => {
    const all = [...Object.values(LABELS), ...Object.values(COMMANDS), ...Object.values(ROW_TITLES), ...Object.values(CONFIRM), ingestLabel(3)];
    expect(all.filter((label) => /sync/i.test(label))).toEqual([]);
  });

  it("name the three git-facing actions as the spec does", () => {
    expect([LABELS.pull, LABELS.apply, LABELS.commitPush]).toEqual(["Pull", "Apply to graph", "Commit & push"]);
    expect(ingestLabel(1)).toBe("Ingest 1 file");
    expect(ingestLabel(7)).toBe("Ingest 7 files");
  });
});

describe("the checklist's notice texts (checklist spec §5)", () => {
  it("formats counts with thousands separators", () => {
    expect(count(4332)).toBe("4,332");
  });

  it("an apply, naming only what it applied", () => {
    expect(applyDoneText(fixture("vault-sync.success").json)).toBe("Applied to graph: 2 docs, 1 table.");
    expect(applyDoneText({ replayed: 3, regenerated_tables: 1, curation: { conflict: { apply: 2 } } })).toBe(
      "Applied to graph: 3 docs, 1 table, 2 curation records.",
    );
  });

  it("a rebuild: all embedded, some still not, or sweeps that failed", () => {
    const sweep = fixture("projection-rebuild.sweep").json;
    const ready = fixture("projection-status.ready").json;
    const gaps = fixture("projection-status.needs-rebuild").json;
    expect(rebuiltText({ ...sweep, rebuilt: 4332 }, ready)).toBe("Graph rebuilt: 4,332 entities, all embedded.");
    expect(rebuiltText({ ...sweep, rebuilt: 1 }, gaps)).toBe("Graph rebuilt: 1 entity, 2,813 entities still not embedded.");
    expect(rebuiltText({ ...sweep, rebuilt: 10, sweep_errors: ["general: refused", "habits: refused"] }, ready)).toBe(
      "Graph rebuilt: 10 entities, but 2 embed sweeps failed — details in the artmind panel.",
    );
    expect(rebuiltText({ ...sweep, rebuilt: 10 }, null)).toBe("Graph rebuilt: 10 entities.");
  });

  it("a synthesis", () => {
    expect(synthesizedText(41)).toBe("Synthesized 41 descriptions.");
    expect(synthesizedText(1)).toBe("Synthesized 1 description.");
  });

  it("a failure: one friendly line, never the raw error", () => {
    expect(failedText("apply to graph", "git diff failed: fatal: bad object 1234")).toBe(
      "Couldn't apply to graph — details in the artmind panel.",
    );
    expect(failedText("rebuild the graph", "graph unreachable: Couldn't connect to 127.0.0.1:7687")).toBe(
      "Couldn't rebuild the graph: Neo4j is unreachable — details in the artmind panel.",
    );
    expect(failedText("apply to graph", "timed out after 3600 s")).toBe("Couldn't apply to graph: it timed out — details in the artmind panel.");
    expect(failedText("rebuild the graph", "Neo.TransientError.General.MemoryPoolOutOfMemoryError")).toBe(
      "Couldn't rebuild the graph: Neo4j ran out of memory — details in the artmind panel.",
    );
    expect(failedText("start the ingest", null)).toBe("Couldn't start the ingest — details in the artmind panel.");
  });

  it("refusals and prompts", () => {
    expect(NOTICE.applyQueued).toBe("Ingest in progress — will apply to graph when it finishes.");
    expect(CONFIRM.applyFirst).toBe("The other laptop has changes not applied to your graph.");
    expect(CONFIRM.pullFirst).toBe("Pull from GitHub first?");
    expect(AUTO_PULL_HINT).toBe("Turn on Obsidian Git's auto pull to see the other laptop's changes");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/texts.test.ts`
Expected: FAIL: `LABELS` / `applyDoneText` / `rebuiltText` are undefined (`TypeError: Cannot convert undefined or null to object`, `... is not a function`).

- [ ] **Step 3: Implement**

In `src/texts.ts`, replace the first two import lines with:

```ts
import { type RowId, behindParts, plural } from "./state";
import type { JobStatus, ProjectionStatus, RebuildResult, SyncResult, TableReport, VaultStatus } from "./types";
```

Append to `src/texts.ts`:

```ts
// ── the readiness checklist (checklist spec D6, §3, §5) ─────────────────────

/** Every button and modal choice. "sync" is in none of them (D6). */
export const LABELS = {
  pull: "Pull",
  apply: "Apply to graph",
  resolve: "Resolve…",
  completeMerge: "Complete the merge",
  retryFailed: "Retry failed",
  retryJob: "Retry job",
  reviewInAdmin: "Review in admin console ↗",
  reviewAndProject: "Review & project…",
  askAdmin: "Ask admin-ui",
  rebuild: "Rebuild graph",
  synthesize: "Synthesize…",
  commitPush: "Commit & push",
  refresh: "↻",
  doctor: "Doctor",
  admin: "Admin ↗",
  applyFirst: "Apply first",
  ingestAnyway: "Ingest anyway",
  pullThenApply: "Pull, then apply",
  applyWithoutPulling: "Apply without pulling",
  synthesizeRun: "Synthesize",
  cancel: "Cancel",
} as const;

export function ingestLabel(files: number): string {
  return `Ingest ${plural(files, "file")}`;
}

/** The command palette's names (checklist spec §4), also the ribbon menu's. */
export const COMMANDS = {
  pull: "Pull",
  apply: "Apply to graph",
  ingest: "Ingest what changed",
  rebuild: "Rebuild graph",
  synthesize: "Synthesize…",
  commitPush: "Commit & push",
  reviewTables: "Review tables for graph",
  resolve: "Resolve artmind conflicts",
  doctor: "Run doctor",
  openPanel: "Open side panel",
  openAdmin: "Open admin console",
  stopAdmin: "Stop admin console",
} as const;

export const ROW_TITLES: Record<RowId, string> = {
  remote: "From the other laptop",
  documents: "Documents",
  tables: "Tables",
  graph: "Graph",
  descriptions: "Descriptions (optional)",
  vault: "Vault",
};

/** The confirm modal's questions (checklist spec §3.3). */
export const CONFIRM = {
  applyFirst: "The other laptop has changes not applied to your graph.",
  pullFirst: "Pull from GitHub first?",
} as const;

/** Notices whose words don't depend on a result. */
export const NOTICE = {
  applyQueued: "Ingest in progress — will apply to graph when it finishes.",
  resolveFirst: "Resolve the artmind conflicts first.",
  pullFirst: "Pull first: the other laptop's commits aren't in this clone.",
  noBookmark: "This laptop has never applied this vault — see the first step in the artmind panel.",
  nothingToIngest: "Nothing new or changed to ingest.",
  nothingToRetry: "Nothing to retry in that job.",
  neo4jIngest: "Neo4j is unreachable: extraction will run, the graph write will fail.",
} as const;

/** Row 1's extra line when Obsidian Git's auto pull is off (spec D10). */
export const AUTO_PULL_HINT = "Turn on Obsidian Git's auto pull to see the other laptop's changes";

/** `4,332`: counts in the checklist and its notices. */
export function count(n: number): string {
  return n.toLocaleString("en-US");
}

export function countOf(n: number, one: string, many: string): string {
  return `${count(n)} ${n === 1 ? one : many}`;
}

/** An action that failed: one friendly line, never the raw error (which
 * goes to the row's detail and the Activity list). */
export function failedText(what: string, error: string | null): string {
  const e = error ?? "";
  let why = "";
  if (/graph unreachable|ServiceUnavailable|Connection refused|Couldn't connect/i.test(e)) why = ": Neo4j is unreachable";
  else if (/^timed out/.test(e)) why = ": it timed out";
  else if (/MemoryLimitExceeded|OutOfMemory/i.test(e)) why = ": Neo4j ran out of memory";
  return `Couldn't ${what}${why} — details in the artmind panel.`;
}

export function applyDoneText(result: SyncResult): string {
  const curation = Object.values(result.curation ?? {}).reduce(
    (sum, counts) => sum + Object.values(counts).reduce((a, b) => a + b, 0),
    0,
  );
  const parts = [
    plural((result.replayed ?? 0) + (result.retracted ?? 0), "doc"),
    plural((result.regenerated_tables ?? 0) + (result.curated_tables ?? 0), "table"),
  ];
  if (curation) parts.push(plural(curation, "curation record"));
  return `Applied to graph: ${parts.join(", ")}.`;
}

/** After `projection rebuild --sweep`, with `projection status` read again. */
export function rebuiltText(result: RebuildResult, projection: ProjectionStatus | null): string {
  const entities = countOf(result.rebuilt, "entity", "entities");
  if (result.sweep_errors.length) {
    return `Graph rebuilt: ${entities}, but ${plural(result.sweep_errors.length, "embed sweep")} failed — details in the artmind panel.`;
  }
  if (!projection) return `Graph rebuilt: ${entities}.`;
  const left = projection.unembedded_entities;
  return `Graph rebuilt: ${entities}, ${left ? `${countOf(left, "entity", "entities")} still not embedded` : "all embedded"}.`;
}

export function synthesizedText(n: number): string {
  return `Synthesized ${countOf(n, "description", "descriptions")}.`;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/texts.test.ts && npm run typecheck`
Expected: PASS. (`syncDoneText` and the other old texts are still imported by the controller and still tested; Task 10 removes them.)

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/src/texts.ts obsidian/artmind-obsidian/test/texts.test.ts
git commit -m "feat(obsidian): every checklist label and the new notice texts in texts.ts

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The pure `checklist(inputs)` (`src/checklist.ts`)

The whole judgement of checklist spec §3.2 and §5.1, as one pure function: rows, the current step, `ready`, and why each command is blocked. Views and the controller only read it.

**Files:**
- Create: `obsidian/artmind-obsidian/src/checklist.ts`
- Modify: `obsidian/artmind-obsidian/src/state.ts` (export `problemText`)
- Modify: `obsidian/artmind-obsidian/src/texts.ts` (append `readinessText`, `nextStepText`, `withNext`)
- Test: `obsidian/artmind-obsidian/test/checklist.test.ts` (create), `test/texts.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `test/checklist.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { type Row, type RowId, changeSources, checklist, jobRunning } from "../src/checklist";
import { EMPTY_INPUTS, type StateInputs } from "../src/state";
import type { DbReview, ProjectionStatus } from "../src/types";
import { fixture } from "./fixtures";

const READY: ProjectionStatus = fixture("projection-status.ready").json;
const NO_REVIEW: DbReview = { query_type: "structured", command: "db review", pending_count: 0, tables: [] };

/** Every row done: each case changes one thing from here. */
function inputs(overrides: Partial<StateInputs> = {}): StateInputs {
  return {
    ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    tablesReview: NO_REVIEW,
    projection: READY,
    synthesis: [],
    git: { ahead: 0, artmindChanges: [] },
    obsidianGit: { autoPullMinutes: 10 },
    ...overrides,
  };
}

function row(overrides: Partial<StateInputs>, id: RowId): Row {
  return checklist(inputs(overrides)).rows.find((r) => r.id === id)!;
}

/** [state, glyph, summary, button labels]. */
function shape(r: Row): [string, string, string, string[]] {
  return [r.state, r.glyph, r.summary, r.buttons.map((b) => b.label)];
}

function withStatus(edit: (status: any) => void): Partial<StateInputs> {
  const status = structuredClone(fixture("vault-status.merge-conflict").json);
  edit(status);
  return { status };
}

const lastJobOf = (name: string, at = 1_000) => ({ status: fixture(name).json, results: null, at });

describe("checklist: each row state (checklist spec §3.2)", () => {
  it.each<[string, RowId, Partial<StateInputs>, [string, string, string, string[]]]>([
    ["remote up to date", "remote", {}, ["done", "✓", "up to date", ["Pull"]]],
    ["remote behind", "remote", { status: fixture("vault-status.behind").json }, ["todo", "●", "2 docs · 1 table to apply", ["Apply to graph", "Pull"]]],
    ["remote in conflict", "remote", { status: fixture("vault-status.merge-conflict").json }, ["attention", "⚠", "1 artmind file in conflict", ["Resolve…"]]],
    ["remote merge left to complete", "remote", withStatus((s) => (s.sync.unresolved_conflicts = [])), ["todo", "●", "resolved, complete the merge", ["Complete the merge"]]],
    ["remote not pulled", "remote", withStatus((s) => {
      s.sync.operation_in_progress = null;
      s.sync.unresolved_conflicts = [];
      s.sync.stores.graph.state = "not_ancestor";
    }), ["todo", "●", "the other laptop's commits aren't pulled", ["Pull"]]],
    ["remote first time", "remote", { status: fixture("vault-status.no-bookmark").json }, ["todo", "●", "first sync on this laptop", ["Pull"]]],
    ["remote Neo4j down", "remote", { status: fixture("vault-status.neo4j-unreachable").json }, ["attention", "!", "Neo4j unreachable", ["Pull"]]],
    ["remote artmind missing", "remote", { problem: { kind: "missing", looked: [] } }, ["attention", "✗", "artmind can't run", []]],
    ["remote applying", "remote", { busy: { apply: true, rebuild: false, synthesize: false } }, ["running", "◌", "applying to graph…", []]],
    ["remote not read yet", "remote", { status: null }, ["waiting", "○", "checking…", []]],

    ["documents all ingested", "documents", {}, ["done", "✓", "all ingested", []]],
    ["documents to ingest", "documents", { pending: fixture("ingest-pending").json }, ["todo", "●", "4 to ingest", ["Ingest 4 files"]]],
    ["documents ingesting", "documents", { activeJob: fixture("ingest-job-status.running").json }, ["running", "◌", "ingesting 2/5", []]],
    ["documents finishing", "documents", { activeJob: fixture("ingest-job-status.finishing").json }, ["running", "◌", "finishing: building the graph", []]],
    ["documents stalled", "documents", { activeJob: fixture("ingest-job-status.stalled").json }, ["attention", "⚠", "stalled at 2/5", ["Retry job"]]],
    ["documents failed", "documents", { lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1 } }, ["attention", "✗", "1 failed", ["Retry failed"]]],
    ["documents not read", "documents", { pending: null }, ["waiting", "○", "checking…", []]],

    ["tables nothing waiting", "tables", {}, ["done", "✓", "nothing waiting", []]],
    ["tables ready for the graph", "tables", { tablesPending: fixture("table2graph.pending").json }, ["todo", "●", "1 ready for the graph", ["Review & project…"]]],
    ["tables with two mappings", "tables", { tablesPending: [{ table: "t", domain: "d", mapping: "a.yaml, b.yaml", reason: "ambiguous" }] }, ["todo", "●", "1 with two mappings", ["Ask admin-ui"]]],
    ["tables not read", "tables", { tablesPending: null, tablesReview: null }, ["waiting", "○", "checking…", []]],

    ["graph needs rebuild", "graph", { projection: fixture("projection-status.needs-rebuild").json }, ["attention", "!", "needs rebuild", ["Rebuild graph"]]],
    ["graph waits for the ingest", "graph", { activeJob: fixture("ingest-job-status.running").json }, ["waiting", "○", "after ingest finishes", []]],
    ["graph waits for the finishing job too", "graph", { activeJob: fixture("ingest-job-status.finishing").json }, ["waiting", "○", "after ingest finishes", []]],
    ["graph not checked", "graph", { projection: null }, ["waiting", "○", "not checked yet", ["Rebuild graph"]]],
    ["graph rebuilding", "graph", { busy: { apply: false, rebuild: true, synthesize: false } }, ["running", "◌", "rebuilding…", []]],

    ["descriptions up to date", "descriptions", {}, ["done", "✓", "up to date", []]],
    ["descriptions to synthesize", "descriptions", { synthesis: [{ domain: "general", count: 41, model: "ministral-3:14b" }] }, ["optional", "◇", "41 could be synthesized · ~41 LLM calls (ministral-3:14b)", ["Synthesize…"]]],
    ["descriptions not counted", "descriptions", { synthesis: null }, ["optional", "◇", "not counted yet", []]],
    ["descriptions synthesizing", "descriptions", { busy: { apply: false, rebuild: false, synthesize: true } }, ["running", "◌", "synthesizing…", []]],

    ["vault shared", "vault", {}, ["done", "✓", "shared", []]],
    ["vault to push", "vault", { git: { ahead: 2, artmindChanges: [] } }, ["todo", "●", "↑ 2 to push", ["Commit & push"]]],
    ["vault no upstream", "vault", { git: { ahead: null, artmindChanges: [] } }, ["done", "✓", "nothing to share (no upstream)", []]],
    ["vault hidden during a job", "vault", { activeJob: fixture("ingest-job-status.running").json, git: { ahead: 2, artmindChanges: [] } }, ["hidden", "○", "after ingest finishes", []]],
  ])("%s", (_name, id, overrides, expected) => {
    expect(shape(row(overrides, id))).toEqual(expected);
  });

  it("row 1 says when it last applied", () => {
    expect(row({ lastApply: { at: Date.now(), ok: true, summary: "x" } }, "remote").summary).toMatch(/^up to date · applied \d\d:\d\d$/);
  });

  it("row 1 asks for Obsidian Git's auto pull when it is off, and only then (D10)", () => {
    const hint = "Turn on Obsidian Git's auto pull to see the other laptop's changes";
    expect(row({ obsidianGit: { autoPullMinutes: 0 } }, "remote").detail).toEqual([hint]);
    expect(row({ obsidianGit: { autoPullMinutes: 0 }, status: fixture("vault-status.behind").json }, "remote").detail).toEqual([hint]);
    expect(row({ obsidianGit: { autoPullMinutes: 10 } }, "remote").detail).toEqual([]);
    expect(row({ obsidianGit: null }, "remote").detail).toEqual([]);
    expect(row({ obsidianGit: { autoPullMinutes: 0 }, status: null }, "remote").detail).toEqual([]);
  });

  it("row 1 keeps Pull as an outline button, never during a merge", () => {
    const behind = checklist(inputs({ status: fixture("vault-status.behind").json }));
    expect(behind.current).toBe("remote");
    expect(behind.rows[0].buttons.map((b) => [b.label, b.action.type])).toEqual([["Apply to graph", "apply"], ["Pull", "pull"]]);
    expect(row({ status: fixture("vault-status.merge-conflict").json }, "remote").buttons.map((b) => b.label)).toEqual(["Resolve…"]);
  });

  it("row 1 shows the bootstrap step for a first time on this laptop", () => {
    expect(row({ status: fixture("vault-status.no-bookmark").json }, "remote").detail.join(" ")).toContain("artmind vault sync --bootstrapEmpty");
  });

  it("row 2 expands to each path with new or changed", () => {
    const documents = row({ pending: fixture("ingest-pending").json }, "documents");
    expect(documents.items.map((i) => [i.text, i.tag])).toEqual([
      ["Notes/new_idea.md", "new"],
      ["Notes/welcome.md", "new"],
      ["Team/org_chart.pdf", "new"],
      ["Team/team.csv", "new"],
    ]);
  });

  it("row 2 expands a failed job to each failure and its error", () => {
    const documents = row({ lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1 } }, "documents");
    expect(documents.items.map((i) => [i.text, i.tag])).toEqual([["/vault/Notes/file2.md", "KG ingestion failed"]]);
    expect(documents.buttons[0].action).toEqual({ type: "retryJob", jobId: "00000000-0000-4000-8000-000000000001" });
  });

  it("row 2 warns before an ingest whose graph write will fail", () => {
    const documents = row({ status: fixture("vault-status.neo4j-unreachable").json, pending: fixture("ingest-pending").json }, "documents");
    expect(documents.detail).toContain("Neo4j is unreachable: extraction will run, the graph write will fail.");
    expect(documents.buttons[0].blockedReason).toBeNull();
  });

  it("row 3 asks the admin console to review unconfirmed classifications, with a prefilled prompt", () => {
    const review: DbReview = fixture("db-review").json;
    const n = review.tables.length;
    const tables = row({ tablesReview: review }, "tables");
    expect(shape(tables)).toEqual(["todo", "●", `${n} need${n === 1 ? "s" : ""} review`, ["Review in admin console ↗"]]);
    expect(tables.buttons[0].action).toEqual({
      type: "adminPrompt",
      prompt: `Review the proposed classifications for table ${review.tables[0].table} (domain ${review.tables[0].domain})`,
    });
    expect(tables.items[0].text).toBe(`${review.tables[0].table} (${review.tables[0].domain})`);
    expect(tables.items[0].tag).toMatch(/unconfirmed$/);
  });

  it("row 3 shows review and projection side by side", () => {
    const tables = row({ tablesReview: fixture("db-review").json, tablesPending: fixture("table2graph.pending").json }, "tables");
    expect(tables.buttons.map((b) => b.label)).toEqual(["Review in admin console ↗", "Review & project…"]);
    expect(tables.buttons[1].action).toEqual({ type: "reviewTable", table: "team" });
  });

  it("row 4 gives each reason a line", () => {
    expect(row({ projection: fixture("projection-status.needs-rebuild").json }, "graph").detail).toEqual([
      "same_as edited",
      "2,813 entities not embedded",
      "310 chunks not embedded",
      "40 observation keys not projected",
    ]);
    const never = { ...READY, known: false, drift: true, same_as_drift: true, schema_drift: true };
    expect(row({ projection: never }, "graph").detail).toEqual(["never rebuilt on this laptop"]);
  });

  it("row 4 is built, embedded, and says when it was rebuilt", () => {
    expect(row({}, "graph").summary).toMatch(/^built · embedded · rebuilt \d\d:\d\d$/);
  });

  it("row 4 names a job's failed rebuild until a clean rebuild supersedes it", () => {
    const job = lastJobOf("ingest-job-status.finalize-failed", 1_000);
    const error = fixture("ingest-job-status.finalize-failed").json.finalize.error;

    const failed = row({ lastJob: job }, "graph");
    expect(shape(failed)).toEqual(["attention", "!", "needs rebuild", ["Rebuild graph"]]);
    expect(failed.detail).toEqual([`last job's rebuild failed: ${error}`]);

    expect(row({ lastJob: job, lastRebuild: { at: 2_000, ok: true, summary: "Graph rebuilt", errors: [] } }, "graph").state).toBe("done");
    expect(row({ lastJob: job, lastRebuild: { at: 500, ok: true, summary: "older", errors: [] } }, "graph").state).toBe("attention");
  });

  it("row 4 keeps a rebuild's sweep errors, and its failure, as reasons", () => {
    const swept = row({ lastRebuild: { at: 1, ok: true, summary: "x", errors: ["general: Connection refused"] } }, "graph");
    expect(swept.detail).toEqual(["sweep failed: general: Connection refused"]);
    const failed = row({ lastRebuild: { at: 1, ok: false, summary: "MemoryPoolOutOfMemoryError", errors: [] } }, "graph");
    expect(failed.detail).toEqual(["last rebuild failed: MemoryPoolOutOfMemoryError"]);
  });

  it("the footer says what the artmind files came from", () => {
    const vault = row({
      git: {
        ahead: 0,
        artmindChanges: [
          ".artmind/data/kg/general/doc1/document.json",
          ".artmind/data/kg/general/doc1/chunks.json",
          ".artmind/data/curation/syntheses/e1.json",
          ".artmind/same_as.yaml",
        ],
      },
    }, "vault");
    expect(shape(vault)).toEqual(["todo", "●", "✎ 4 artmind files to commit · ↑ 0", ["Commit & push"]]);
    expect(vault.detail).toEqual(["from: ingest (1 file) · syntheses (1) · other (1)"]);
  });

  it("a failed action's raw error goes to its row's detail", () => {
    expect(row({ status: fixture("vault-status.behind").json, actionErrors: { remote: "git diff failed: fatal" } }, "remote").detail).toEqual([
      "git diff failed: fatal",
    ]);
  });
});

describe("checklist: the current step, ready, and the footer (checklist spec §3.2, D3, D4)", () => {
  it("everything done is a ready graph with no current step", () => {
    const cl = checklist(inputs());
    expect([cl.ready, cl.current, cl.stepsLeft]).toEqual([true, null, 0]);
  });

  it("the current step is the first row that is not done", () => {
    const cl = checklist(inputs({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json }));
    expect([cl.ready, cl.current, cl.stepsLeft]).toEqual([false, "remote", 2]);
  });

  it("Descriptions is never the current step, and never stops the graph being ready", () => {
    const cl = checklist(inputs({ synthesis: [{ domain: "general", count: 41, model: "m" }] }));
    expect([cl.ready, cl.current]).toEqual([true, null]);
  });

  it("the footer is the current step only once rows 1-4 are done", () => {
    const changes = { ahead: 1, artmindChanges: [] };
    expect(checklist(inputs({ git: changes })).current).toBe("vault");
    expect(checklist(inputs({ git: changes, projection: fixture("projection-status.needs-rebuild").json })).current).toBe("graph");
  });

  it("a running job is the current step", () => {
    expect(checklist(inputs({ activeJob: fixture("ingest-job-status.finishing").json })).current).toBe("documents");
  });
});

describe("checklist: blocked commands (checklist spec §4, palette)", () => {
  it("a running job blocks every write but resolve, and the footer", () => {
    const { blocked } = checklist(inputs({ activeJob: fixture("ingest-job-status.running").json, status: fixture("vault-status.behind").json, git: { ahead: 1, artmindChanges: [] } }));
    expect(blocked.apply).toBe("An ingest job is running — apply after it finishes");
    expect(blocked.ingest).toBe("An ingest job is already running");
    expect(blocked.rebuild).toBe("Rebuild after the ingest finishes");
    expect(blocked.commitPush).toBe("Files are still being written — commit after the ingest finishes");
  });

  it("a stalled job blocks nothing: its worker is gone", () => {
    const { blocked } = checklist(inputs({ activeJob: fixture("ingest-job-status.stalled").json, pending: fixture("ingest-pending").json }));
    expect(blocked.ingest).toBeNull();
    expect(checklist(inputs({ activeJob: fixture("ingest-job-status.stalled").json })).jobRunning).toBe(false);
  });

  it("artmind missing blocks everything that runs artmind", () => {
    const { blocked } = checklist(inputs({ problem: { kind: "missing", looked: [] } }));
    for (const command of ["apply", "ingest", "rebuild", "synthesize", "tables", "resolve", "doctor", "admin"] as const) {
      expect(blocked[command]).toBe("artmind not found (looked in nowhere)");
    }
    expect(blocked.pull).toBeNull();
  });

  it("Neo4j unreachable blocks the graph writes but lets ingest run", () => {
    const { blocked, neo4jUnreachable } = checklist(inputs({ status: fixture("vault-status.neo4j-unreachable").json, pending: fixture("ingest-pending").json, projection: fixture("projection-status.needs-rebuild").json, synthesis: [{ domain: "general", count: 2, model: "m" }] }));
    expect(neo4jUnreachable).toBe(true);
    expect([blocked.apply, blocked.rebuild, blocked.synthesize, blocked.ingest]).toEqual(["Neo4j unreachable", "Neo4j unreachable", "Neo4j unreachable", null]);
  });

  it("says why there is nothing to do", () => {
    const { blocked } = checklist(inputs());
    expect(blocked.apply).toBe("Nothing to apply: the graph has every commit");
    expect(blocked.ingest).toBe("Nothing new or changed to ingest");
    expect(blocked.rebuild).toBe("The graph is up to date");
    expect(blocked.synthesize).toBe("Nothing to synthesize");
    expect(blocked.commitPush).toBe("Nothing to commit or push");
    expect(blocked.tables).toBe("No tables waiting");
    expect(blocked.resolve).toBe("No merge in progress");
    expect(checklist(inputs({ pending: null })).blocked.ingest).toBeNull();
    expect(checklist(inputs({ synthesis: null })).blocked.synthesize).toBe("Not counted yet: press ↻");
  });

  it("a merge in progress blocks apply and ingest", () => {
    const { blocked } = checklist(inputs({ status: fixture("vault-status.merge-conflict").json, pending: fixture("ingest-pending").json }));
    expect(blocked.ingest).toBe("A merge is in progress — finish it first");
    expect(blocked.pull).toBe("A merge is in progress — finish it first");
    expect(blocked.resolve).toBeNull();
  });
});

describe("helpers", () => {
  it("a job runs while it is queued, processing or finalizing, never stalled", () => {
    expect(jobRunning(fixture("ingest-job-status.running").json)).toBe(true);
    expect(jobRunning(fixture("ingest-job-status.finishing").json)).toBe(true);
    expect(jobRunning(fixture("ingest-job-status.stalled").json)).toBe(false);
    expect(jobRunning(fixture("ingest-job-status.done").json)).toBe(false);
    expect(jobRunning({ ...fixture("ingest-job-status.done").json, finalize: { state: "pending", domains: ["general"], error: null } })).toBe(true);
    expect(jobRunning(null)).toBe(false);
  });

  it("counts docs once however many of their files changed", () => {
    expect(changeSources([
      ".artmind/data/kg/general/a/document.json",
      ".artmind/data/kg/general/a/observations.json",
      ".artmind/data/kg/habits/b/document.json",
      ".artmind/data/curation/syntheses/x.json",
      ".artmind/data/curation/syntheses/y.json",
      ".artmind/vault.yaml",
    ])).toEqual({ ingest: 2, syntheses: 2, other: 1 });
  });
});
```

Append to `test/texts.test.ts` (and add `nextStepText, readinessText, withNext` to its import from `../src/texts`, plus `import { checklist } from "../src/checklist";` and `import { EMPTY_INPUTS } from "../src/state";`):

```ts
describe("next steps and readiness (checklist spec §3.1, §5)", () => {
  const done = {
    ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    tablesReview: { query_type: "structured", command: "db review", pending_count: 0, tables: [] },
    projection: fixture("projection-status.ready").json,
    synthesis: [],
    git: { ahead: 0, artmindChanges: [] },
  };

  it("names the current step's action", () => {
    expect(nextStepText(checklist({ ...done, projection: fixture("projection-status.needs-rebuild").json }))).toBe("Next: rebuild graph.");
    expect(nextStepText(checklist({ ...done, git: { ahead: 1, artmindChanges: [] } }))).toBe("Next: commit & push.");
    expect(nextStepText(checklist({ ...done, pending: fixture("ingest-pending").json }))).toBe("Next: ingest 4 files.");
    expect(nextStepText(checklist({ ...done, tablesPending: fixture("table2graph.pending").json }))).toBe("Next: review & project.");
    expect(nextStepText(checklist(done))).toBe("");
  });

  it("appends it to a notice, or leaves the notice alone", () => {
    expect(withNext("Applied to graph: 2 docs, 1 table.", checklist({ ...done, pending: fixture("ingest-pending").json }))).toBe(
      "Applied to graph: 2 docs, 1 table. Next: ingest 4 files.",
    );
    expect(withNext("Applied to graph: 2 docs, 1 table.", checklist(done))).toBe("Applied to graph: 2 docs, 1 table.");
  });

  it("says how far the graph is from ready", () => {
    expect(readinessText(checklist(done))).toBe("Graph ready ✓");
    expect(readinessText(checklist({ ...done, pending: fixture("ingest-pending").json }))).toBe("1 step to a ready graph");
    expect(readinessText(checklist({ ...done, pending: fixture("ingest-pending").json, status: fixture("vault-status.behind").json }))).toBe(
      "2 steps to a ready graph",
    );
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/checklist.test.ts test/texts.test.ts`
Expected: FAIL: `Failed to load url ../src/checklist` (the module does not exist).

- [ ] **Step 3: Export `problemText`**

In `src/state.ts`, change `function problemText(problem: ArtmindProblem): string {` to `export function problemText(problem: ArtmindProblem): string {`.

- [ ] **Step 4: Write `src/checklist.ts`**

```ts
import { BOOTSTRAP_STEP, type RowId, type StateInputs, type Tone, behindParts, plural, problemText } from "./state";
import { AUTO_PULL_HINT, LABELS, NOTICE, ROW_TITLES, count, countOf, ingestLabel } from "./texts";
import type { JobStatus, TablePending, TableToReview } from "./types";

export type { RowId } from "./state";

/** `done` ✓ · `todo` ● · `running` ◌ · `attention` ! ✗ ⚠ · `optional` ◇ ·
 * `waiting` ○ (on a row above, or not read yet) · `hidden` (the footer during a job). */
export type RowState = "done" | "todo" | "running" | "attention" | "optional" | "waiting" | "hidden";
export type Glyph = "✓" | "●" | "◌" | "!" | "✗" | "⚠" | "◇" | "○";

/** What a row's button does. The controller's `run` performs it. */
export type RowAction =
  | { type: "pull" }
  | { type: "apply" }
  | { type: "resolve" }
  | { type: "completeMerge" }
  | { type: "ingest" }
  | { type: "retryJob"; jobId: string }
  | { type: "adminPrompt"; prompt: string }
  | { type: "reviewTable"; table: string }
  | { type: "rebuild" }
  | { type: "synthesize" }
  | { type: "commitPush" };

export interface RowButton {
  action: RowAction;
  label: string;
  /** Why it can't run now (the button is disabled with this as its title), or null. */
  blockedReason: string | null;
}

/** One expandable line: a path, a table, a failed file. */
export interface RowItem {
  text: string;
  tag: string | null;
  title: string | null;
}

export interface Row {
  id: RowId;
  state: RowState;
  glyph: Glyph;
  tone: Tone;
  title: string;
  /** The one line beside the title; also the status bar's label. */
  summary: string;
  /** Lines under it: reasons, warnings, the last error. */
  detail: string[];
  items: RowItem[];
  /** The first is the row's main action: filled on the current row only. */
  buttons: RowButton[];
  optional: boolean;
}

/** Every action the palette offers, with why it is blocked. */
export type CommandId = "pull" | "apply" | "ingest" | "rebuild" | "synthesize" | "commitPush" | "tables" | "resolve" | "doctor" | "admin";

export interface Checklist {
  /** Rows 1–5, then the footer (`vault`). */
  rows: Row[];
  /** The first of rows 1–4 not done; the footer once they all are and it has work; else null. */
  current: RowId | null;
  /** Rows 1–4 all done. */
  ready: boolean;
  stepsLeft: number;
  blocked: Record<CommandId, string | null>;
  neo4jUnreachable: boolean;
  jobRunning: boolean;
}

/** The rows that decide `ready` (D4, D5: not Descriptions, not the footer). */
export const STEP_IDS: RowId[] = ["remote", "documents", "tables", "graph"];

/** Queued, processing, or still owing its deferred rebuild; never stalled. */
export function jobRunning(job: JobStatus | null): boolean {
  if (!job || job.stalled) return false;
  return ["queued", "processing"].includes(job.status) || job.finalize?.state === "pending";
}

/** Every file done and the deferred rebuild running. */
export function jobFinishing(job: JobStatus): boolean {
  return job.finalize?.state === "pending" && (job.processed_count >= job.file_count || !["queued", "processing"].includes(job.status));
}

/** What uncommitted `.artmind/` paths came from: docs an ingest wrote
 * (`data/kg/<domain>/<doc>/`, once per doc), syntheses, anything else. */
export function changeSources(paths: string[]): { ingest: number; syntheses: number; other: number } {
  const docs = new Set<string>();
  let syntheses = 0;
  let other = 0;
  for (const path of paths) {
    const parts = path.split("/");
    if (parts[0] === ".artmind" && parts[1] === "data" && parts[2] === "kg" && parts.length >= 6) docs.add(`${parts[3]}/${parts[4]}`);
    else if (parts[0] === ".artmind" && parts[1] === "data" && parts[2] === "curation" && parts[3] === "syntheses") syntheses++;
    else other++;
  }
  return { ingest: docs.size, syntheses, other };
}

/** The admin console prompt for a table's unconfirmed classifications (B7). */
export function reviewPrompt(table: TableToReview): string {
  return `Review the proposed classifications for table ${table.table} (domain ${table.domain})`;
}

export function ambiguousPrompt(table: TablePending): string {
  return `Table ${table.table} (domain ${table.domain}) matches more than one table mapping (${table.mapping}). Help me narrow one so table2graph can project it.`;
}

function unconfirmed(table: TableToReview): string {
  const parts: string[] = [];
  if (!table.grain_confirmed) parts.push("grain");
  if (table.mappings.length) parts.push(plural(table.mappings.length, "column mapping"));
  if (table.bridge_columns.length) parts.push(plural(table.bridge_columns.length, "bridge column"));
  return `${parts.join(", ") || "classification"} unconfirmed`;
}

/** `10:02`, local time. */
function clock(at: number): string {
  const d = new Date(at);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Why row 4 needs a rebuild, one line each (checklist spec §3.2, decision 9). */
export function rebuildReasons(inputs: StateInputs): string[] {
  const reasons: string[] = [];
  const p = inputs.projection;
  if (p) {
    if (!p.known) reasons.push("never rebuilt on this laptop");
    else {
      if (p.schema_drift) reasons.push("schema changed");
      if (p.same_as_drift) reasons.push("same_as edited");
    }
    if (p.unembedded_entities) reasons.push(`${countOf(p.unembedded_entities, "entity", "entities")} not embedded`);
    if (p.unembedded_chunks) reasons.push(`${countOf(p.unembedded_chunks, "chunk", "chunks")} not embedded`);
    if (p.unprojected_keys) reasons.push(`${countOf(p.unprojected_keys, "observation key", "observation keys")} not projected`);
  }
  const last = inputs.lastRebuild;
  const job = inputs.lastJob;
  const finalize = job?.status.finalize;
  const superseded = Boolean(last && last.ok && !last.errors.length && job && last.at > job.at);
  if (finalize?.state === "failed" && !superseded) reasons.push(`last job's rebuild failed: ${finalize.error ?? "unknown error"}`);
  if (last && !last.ok) reasons.push(`last rebuild failed: ${last.summary}`);
  if (last?.ok) for (const error of last.errors) reasons.push(`sweep failed: ${error}`);
  return reasons;
}

type RowFields = Pick<Row, "state" | "glyph" | "tone" | "summary"> & Partial<Pick<Row, "detail" | "items" | "buttons">>;

function makeRow(id: RowId, fields: RowFields): Row {
  return { id, title: ROW_TITLES[id], optional: id === "descriptions", detail: [], items: [], buttons: [], ...fields };
}

function button(action: RowAction, label: string, blockedReason: string | null): RowButton {
  return { action, label, blockedReason };
}

function first(...reasons: Array<string | null>): string | null {
  return reasons.find((reason) => reason !== null) ?? null;
}

/** The readiness checklist (checklist spec §3, §5.1), as a pure function of
 * what was read. The views render it; the controller gates on `blocked`. */
export function checklist(inputs: StateInputs): Checklist {
  const sync = inputs.status?.sync;
  const stores = sync?.stores ?? {};
  const storeStates = Object.values(stores).map((report) => report?.state);
  const graphError = sync?.graph_error ?? null;
  const neo4jUnreachable = Boolean(graphError && graphError.startsWith("graph unreachable"));
  const merge = sync?.operation_in_progress ?? null;
  const conflicts = sync?.unresolved_conflicts ?? [];
  const storeErrors = Object.entries(stores)
    .filter(([, report]) => report?.state === "error")
    .map(([name, report]) => `the ${name} store: ${report?.detail ?? "unknown error"}`);
  const behind = storeStates.includes("behind");
  const noBookmark = storeStates.includes("no_bookmark");
  const notAncestor = storeStates.includes("not_ancestor");

  const job = inputs.activeJob;
  const running = jobRunning(job);
  const pending = inputs.pending;
  const entries = pending ? [...pending.notes, ...pending.binaries, ...pending.tables] : [];
  const pendingErrors = (pending?.errors ?? []).map((e) => `${e.path}: ${e.error}`);
  const lastJob = inputs.lastJob;
  const failed = (lastJob?.results?.files ?? []).filter((f) => f.status === "failed");

  const review = inputs.tablesReview?.tables ?? [];
  const tablesPending = inputs.tablesPending ?? [];
  const readyTables = tablesPending.filter((t) => t.reason !== "ambiguous");
  const ambiguous = tablesPending.filter((t) => t.reason === "ambiguous");

  const reasons = rebuildReasons(inputs);
  const graphDone = Boolean(inputs.projection) && reasons.length === 0;
  const synthesis = inputs.synthesis;
  const toSynthesize = (synthesis ?? []).reduce((sum, d) => sum + d.count, 0);
  const changes = inputs.git.artmindChanges;
  const ahead = inputs.git.ahead;

  const cannotRun = inputs.problem ? problemText(inputs.problem) : null;
  const mergeText = merge ? `${merge[0].toUpperCase()}${merge.slice(1)} is in progress — finish it first` : null;
  const blocked: Record<CommandId, string | null> = {
    pull: mergeText,
    apply: first(
      cannotRun,
      inputs.busy.apply ? "Already applying" : null,
      running ? "An ingest job is running — apply after it finishes" : null,
      mergeText,
      conflicts.length ? "Resolve the artmind conflicts first" : null,
      neo4jUnreachable ? "Neo4j unreachable" : null,
      noBookmark ? "This laptop has never applied this vault: see the first step" : null,
      notAncestor ? "Pull first: the other laptop's commits aren't in this clone" : null,
      behind ? null : "Nothing to apply: the graph has every commit",
    ),
    ingest: first(
      cannotRun,
      running ? "An ingest job is already running" : null,
      mergeText,
      pending && !entries.length ? "Nothing new or changed to ingest" : null,
    ),
    rebuild: first(
      cannotRun,
      inputs.busy.rebuild ? "Already rebuilding" : null,
      running ? "Rebuild after the ingest finishes" : null,
      neo4jUnreachable ? "Neo4j unreachable" : null,
      graphDone ? "The graph is up to date" : null,
    ),
    synthesize: first(
      cannotRun,
      inputs.busy.synthesize ? "Already synthesizing" : null,
      running ? "Synthesize after the ingest finishes" : null,
      neo4jUnreachable ? "Neo4j unreachable" : null,
      synthesis === null ? "Not counted yet: press ↻" : null,
      toSynthesize ? null : "Nothing to synthesize",
    ),
    commitPush: first(
      running ? "Files are still being written — commit after the ingest finishes" : null,
      changes.length || ahead ? null : "Nothing to commit or push",
    ),
    tables: first(cannotRun, neo4jUnreachable ? "Neo4j unreachable" : null, readyTables.length ? null : "No tables waiting"),
    resolve: first(cannotRun, merge === "a merge" ? null : "No merge in progress"),
    doctor: cannotRun,
    admin: cannotRun,
  };

  // ── 1 From the other laptop ──
  // Pull stays available as an outline button wherever pulling makes sense;
  // it is the main button only when a bookmark isn't in this clone (D10).
  const pullButton = button({ type: "pull" }, LABELS.pull, blocked.pull);
  let remote: Row;
  if (cannotRun) remote = makeRow("remote", { state: "attention", glyph: "✗", tone: "red", summary: "artmind can't run", detail: [cannotRun] });
  else if (!inputs.status) remote = makeRow("remote", { state: "waiting", glyph: "○", tone: "grey", summary: "checking…" });
  else if (inputs.busy.apply) remote = makeRow("remote", { state: "running", glyph: "◌", tone: "spinner", summary: "applying to graph…" });
  else if (conflicts.length) {
    remote = makeRow("remote", {
      state: "attention",
      glyph: "⚠",
      tone: "red",
      summary: `${plural(conflicts.length, "artmind file")} in conflict`,
      items: conflicts.map((path) => ({ text: path, tag: null, title: null })),
      buttons: [button({ type: "resolve" }, LABELS.resolve, blocked.resolve)],
    });
  } else if (merge) {
    remote = makeRow("remote", {
      state: "todo",
      glyph: "●",
      tone: "amber",
      summary: "resolved, complete the merge",
      detail: ["Resolve any note Obsidian still shows in conflict, then complete the merge."],
      buttons: [button({ type: "completeMerge" }, LABELS.completeMerge, null)],
    });
  } else if (graphError) {
    remote = makeRow("remote", {
      state: "attention",
      glyph: "!",
      tone: "red",
      summary: neo4jUnreachable ? "Neo4j unreachable" : "can't read the graph",
      detail: [graphError],
      buttons: [pullButton],
    });
  } else if (storeErrors.length) {
    remote = makeRow("remote", { state: "attention", glyph: "!", tone: "red", summary: "an apply would fail", detail: storeErrors, buttons: [pullButton] });
  } else if (notAncestor) {
    remote = makeRow("remote", { state: "todo", glyph: "●", tone: "amber", summary: "the other laptop's commits aren't pulled", buttons: [pullButton] });
  } else if (noBookmark) {
    remote = makeRow("remote", { state: "todo", glyph: "●", tone: "amber", summary: "first sync on this laptop", detail: [BOOTSTRAP_STEP], buttons: [pullButton] });
  } else if (behind) {
    remote = makeRow("remote", {
      state: "todo",
      glyph: "●",
      tone: "amber",
      summary: `${behindParts(stores).join(" · ") || "changes"} to apply`,
      buttons: [button({ type: "apply" }, LABELS.apply, blocked.apply), pullButton],
    });
  } else {
    const applied = inputs.lastApply?.ok ? ` · applied ${clock(inputs.lastApply.at)}` : "";
    remote = makeRow("remote", { state: "done", glyph: "✓", tone: "grey", summary: `up to date${applied}`, buttons: [pullButton] });
  }
  // With auto pull off, nothing tells row 1 the other laptop pushed (D10).
  if (inputs.obsidianGit?.autoPullMinutes === 0 && remote.state !== "waiting" && !cannotRun) {
    remote = { ...remote, detail: [...remote.detail, AUTO_PULL_HINT] };
  }

  // ── 2 Documents ──
  const retryFailed = failed.length && lastJob ? [button({ type: "retryJob", jobId: lastJob.status.job_id }, LABELS.retryFailed, cannotRun)] : [];
  let documents: Row;
  if (job?.stalled) {
    documents = makeRow("documents", {
      state: "attention",
      glyph: "⚠",
      tone: "amber",
      summary: `stalled at ${job.processed_count}/${job.file_count}`,
      detail: ["The worker stopped mid-job; nothing will finish it."],
      buttons: [button({ type: "retryJob", jobId: job.job_id }, LABELS.retryJob, cannotRun)],
    });
  } else if (job && running) {
    documents = makeRow("documents", {
      state: "running",
      glyph: "◌",
      tone: "spinner",
      summary: jobFinishing(job) ? "finishing: building the graph" : `ingesting ${job.processed_count}/${job.file_count}`,
    });
  } else if (entries.length) {
    documents = makeRow("documents", {
      state: "todo",
      glyph: "●",
      tone: "blue",
      summary: `${entries.length} to ingest`,
      detail: [
        ...(neo4jUnreachable ? [NOTICE.neo4jIngest] : []),
        ...(failed.length ? [`${plural(failed.length, "file")} failed in the last job`] : []),
        ...pendingErrors,
      ],
      items: entries.map((e) => ({ text: e.path, tag: e.reason, title: null })),
      buttons: [button({ type: "ingest" }, ingestLabel(entries.length), blocked.ingest), ...retryFailed],
    });
  } else if (failed.length) {
    documents = makeRow("documents", {
      state: "attention",
      glyph: "✗",
      tone: "red",
      summary: `${failed.length} failed`,
      detail: pendingErrors,
      items: failed.map((f) => ({ text: f.filename, tag: f.error_message ?? "failed", title: f.filename })),
      buttons: retryFailed,
    });
  } else if (pendingErrors.length) {
    documents = makeRow("documents", { state: "attention", glyph: "!", tone: "amber", summary: `${plural(pendingErrors.length, "file")} can't be read`, detail: pendingErrors });
  } else if (!pending) {
    documents = makeRow("documents", { state: "waiting", glyph: "○", tone: "grey", summary: "checking…" });
  } else {
    documents = makeRow("documents", { state: "done", glyph: "✓", tone: "grey", summary: "all ingested" });
  }

  // ── 3 Tables ──
  const tableParts = [
    review.length ? `${review.length} need${review.length === 1 ? "s" : ""} review` : null,
    readyTables.length ? `${readyTables.length} ready for the graph` : null,
    ambiguous.length ? `${ambiguous.length} with two mappings` : null,
  ].filter((part): part is string => part !== null);
  let tables: Row;
  if (tableParts.length) {
    tables = makeRow("tables", {
      state: "todo",
      glyph: "●",
      tone: "blue",
      summary: tableParts.join(" · "),
      items: [
        ...review.map((t) => ({ text: `${t.table} (${t.domain})`, tag: unconfirmed(t), title: null })),
        ...readyTables.map((t) => ({ text: `${t.table} (${t.domain})`, tag: t.reason.replace(/_/g, " "), title: null })),
        ...ambiguous.map((t) => ({ text: `${t.table} (${t.domain})`, tag: `matches ${t.mapping}`, title: null })),
      ],
      buttons: [
        ...(review.length ? [button({ type: "adminPrompt", prompt: reviewPrompt(review[0]) }, LABELS.reviewInAdmin, blocked.admin)] : []),
        ...(readyTables.length ? [button({ type: "reviewTable", table: readyTables[0].table }, LABELS.reviewAndProject, blocked.tables)] : []),
        ...(ambiguous.length ? [button({ type: "adminPrompt", prompt: ambiguousPrompt(ambiguous[0]) }, LABELS.askAdmin, blocked.admin)] : []),
      ],
    });
  } else if (inputs.tablesReview === null && inputs.tablesPending === null) {
    tables = makeRow("tables", { state: "waiting", glyph: "○", tone: "grey", summary: "checking…" });
  } else {
    tables = makeRow("tables", { state: "done", glyph: "✓", tone: "grey", summary: "nothing waiting" });
  }

  // ── 4 Graph ──
  const rebuildButton = button({ type: "rebuild" }, LABELS.rebuild, blocked.rebuild);
  let graph: Row;
  if (running) graph = makeRow("graph", { state: "waiting", glyph: "○", tone: "grey", summary: "after ingest finishes" });
  else if (inputs.busy.rebuild) graph = makeRow("graph", { state: "running", glyph: "◌", tone: "spinner", summary: "rebuilding…" });
  else if (reasons.length) graph = makeRow("graph", { state: "attention", glyph: "!", tone: "amber", summary: "needs rebuild", detail: reasons, buttons: [rebuildButton] });
  else if (!inputs.projection) graph = makeRow("graph", { state: "waiting", glyph: "○", tone: "grey", summary: "not checked yet", buttons: [rebuildButton] });
  else {
    const at = Date.parse(inputs.projection.last_rebuilt_at ?? "");
    graph = makeRow("graph", { state: "done", glyph: "✓", tone: "grey", summary: `built · embedded${Number.isNaN(at) ? "" : ` · rebuilt ${clock(at)}`}` });
  }

  // ── 5 Descriptions (optional: never amber, never current) ──
  let descriptions: Row;
  if (inputs.busy.synthesize) descriptions = makeRow("descriptions", { state: "running", glyph: "◌", tone: "spinner", summary: "synthesizing…" });
  else if (synthesis === null) descriptions = makeRow("descriptions", { state: "optional", glyph: "◇", tone: "grey", summary: "not counted yet" });
  else if (!toSynthesize) descriptions = makeRow("descriptions", { state: "done", glyph: "✓", tone: "grey", summary: "up to date" });
  else {
    const models = [...new Set(synthesis.map((d) => d.model))].join(", ");
    descriptions = makeRow("descriptions", {
      state: "optional",
      glyph: "◇",
      tone: "blue",
      summary: `${count(toSynthesize)} could be synthesized · ~${countOf(toSynthesize, "LLM call", "LLM calls")} (${models})`,
      items: synthesis.filter((d) => d.count > 0).map((d) => ({ text: d.domain, tag: count(d.count), title: null })),
      buttons: [button({ type: "synthesize" }, LABELS.synthesize, blocked.synthesize)],
    });
  }

  // ── Footer: Vault ──
  const commitButton = button({ type: "commitPush" }, LABELS.commitPush, blocked.commitPush);
  let vault: Row;
  if (running) vault = makeRow("vault", { state: "hidden", glyph: "○", tone: "grey", summary: "after ingest finishes" });
  else if (changes.length) {
    const from = changeSources(changes);
    const parts = [
      from.ingest ? `ingest (${plural(from.ingest, "file")})` : null,
      from.syntheses ? `syntheses (${from.syntheses})` : null,
      from.other ? `other (${from.other})` : null,
    ].filter((part): part is string => part !== null);
    vault = makeRow("vault", {
      state: "todo",
      glyph: "●",
      tone: "blue",
      summary: `✎ ${plural(changes.length, "artmind file")} to commit · ↑ ${ahead ?? 0}`,
      detail: [`from: ${parts.join(" · ")}`],
      buttons: [commitButton],
    });
  } else if (ahead) {
    vault = makeRow("vault", { state: "todo", glyph: "●", tone: "blue", summary: `↑ ${ahead} to push`, buttons: [commitButton] });
  } else {
    vault = makeRow("vault", { state: "done", glyph: "✓", tone: "grey", summary: ahead === null ? "nothing to share (no upstream)" : "shared" });
  }

  const rows = [remote, documents, tables, graph, descriptions, vault].map((r) => {
    const error = inputs.actionErrors[r.id];
    return error ? { ...r, detail: [...r.detail, error] } : r;
  });
  const left = rows.filter((r) => STEP_IDS.includes(r.id) && r.state !== "done");
  const ready = left.length === 0;
  const current = left[0]?.id ?? (vault.state === "todo" ? "vault" : null);
  return { rows, current, ready, stepsLeft: left.length, blocked, neo4jUnreachable, jobRunning: running };
}
```

- [ ] **Step 5: Add the readiness and next-step texts**

In `src/texts.ts`, add after the imports:

```ts
import type { Checklist } from "./checklist";
```

and append:

```ts
/** The panel header (checklist spec §3.2). */
export function readinessText(cl: Checklist): string {
  return cl.ready ? "Graph ready ✓" : `${plural(cl.stepsLeft, "step")} to a ready graph`;
}

/** `Next: rebuild graph.` from the current row's main button, or "" (spec §5). */
export function nextStepText(cl: Checklist): string {
  const label = cl.rows.find((r) => r.id === cl.current)?.buttons[0]?.label;
  if (!label) return "";
  const bare = label.replace(/…$/, "").replace(/ ↗$/, "");
  return `Next: ${bare[0].toLowerCase()}${bare.slice(1)}.`;
}

export function withNext(text: string, cl: Checklist): string {
  const next = nextStepText(cl);
  return next ? `${text} ${next}` : text;
}
```

- [ ] **Step 6: Run the tests**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/checklist.test.ts test/texts.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add obsidian/artmind-obsidian/src/checklist.ts obsidian/artmind-obsidian/src/state.ts obsidian/artmind-obsidian/src/texts.ts obsidian/artmind-obsidian/test/checklist.test.ts obsidian/artmind-obsidian/test/texts.test.ts
git commit -m "feat(obsidian): pure checklist(inputs) -- rows, current step, ready, blocked commands

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: What survives a reload (`persist.ts`), and the admin prompt link

**Files:**
- Create: `obsidian/artmind-obsidian/src/persist.ts`
- Modify: `obsidian/artmind-obsidian/src/admin.ts` (append `promptPath`)
- Test: `obsidian/artmind-obsidian/test/persist.test.ts` (create), `test/admin.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `test/persist.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { NOTHING_PERSISTED, type Persisted, readPersisted, withPersisted } from "../src/persist";
import { DEFAULT_SETTINGS, mergeSettings } from "../src/settings";
import { fixture } from "./fixtures";

const SAVED: Persisted = {
  lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1_000 },
  lastApply: { at: 2_000, ok: true, summary: "Applied to graph: 2 docs, 1 table." },
  lastRebuild: { at: 3_000, ok: true, summary: "Graph rebuilt: 3 entities, all embedded.", errors: [] },
};

describe("persisted state (checklist spec §4)", () => {
  it("round-trips through data.json, beside the settings", () => {
    const data = JSON.parse(JSON.stringify(withPersisted(DEFAULT_SETTINGS, SAVED)));

    expect(readPersisted(data)).toEqual(SAVED);
    expect(mergeSettings(data)).toEqual(DEFAULT_SETTINGS);
  });

  it("is empty when nothing was saved", () => {
    expect(readPersisted(null)).toEqual(NOTHING_PERSISTED);
    expect(readPersisted({ artmindPath: "" })).toEqual(NOTHING_PERSISTED);
  });

  it("drops a malformed field, keeping the rest", () => {
    const data = {
      state: {
        lastJob: { status: { job_id: 7 }, results: null, at: 1 },
        lastApply: { at: "noon", ok: true, summary: "x" },
        lastRebuild: { at: 3, ok: false, summary: "boom" },
      },
    };
    expect(readPersisted(data)).toEqual({ lastJob: null, lastApply: null, lastRebuild: { at: 3, ok: false, summary: "boom", errors: [] } });
  });
});
```

Append to `test/admin.test.ts` (and add `promptPath` to its import from `../src/admin`):

```ts
describe("promptPath (Plan 1, B7)", () => {
  it("prefills the admin console's chat through /?prompt=, encoded", () => {
    expect(promptPath("Review the proposed classifications for table team (domain general)")).toBe(
      "/?prompt=Review%20the%20proposed%20classifications%20for%20table%20team%20(domain%20general)",
    );
    expect(promptPath("a&b=c")).toBe("/?prompt=a%26b%3Dc");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/persist.test.ts test/admin.test.ts`
Expected: FAIL: `Failed to load url ../src/persist`, and `promptPath is not a function`.

- [ ] **Step 3: Implement**

Create `src/persist.ts`:

```ts
import type { ArtmindSettings } from "./settings";
import type { LastJob, Outcome, RebuildOutcome } from "./state";
import type { JobResults, JobStatus } from "./types";

/** What the panel shows that no CLI read gives back after a reload
 * (checklist spec §4): the last job card, the last apply and rebuild. */
export interface Persisted {
  lastJob: LastJob | null;
  lastApply: Outcome | null;
  lastRebuild: RebuildOutcome | null;
}

export const NOTHING_PERSISTED: Persisted = { lastJob: null, lastApply: null, lastRebuild: null };

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function outcome(value: unknown): Outcome | null {
  const o = record(value);
  if (!o || typeof o.at !== "number" || typeof o.ok !== "boolean" || typeof o.summary !== "string") return null;
  return { at: o.at, ok: o.ok, summary: o.summary };
}

function rebuildOutcome(value: unknown): RebuildOutcome | null {
  const base = outcome(value);
  if (!base) return null;
  const errors = record(value)?.errors;
  return { ...base, errors: Array.isArray(errors) ? errors.filter((e): e is string => typeof e === "string") : [] };
}

function lastJob(value: unknown): LastJob | null {
  const o = record(value);
  const status = record(o?.status);
  const results = o?.results === null ? null : record(o?.results);
  if (!o || typeof o.at !== "number" || !status || typeof status.job_id !== "string" || !Array.isArray(status.files)) return null;
  if (o.results !== null && (!results || !Array.isArray(results.files))) return null;
  return { status: status as unknown as JobStatus, results: results as unknown as JobResults | null, at: o.at };
}

/** `state` from `data.json`; a missing or malformed field is null. */
export function readPersisted(data: unknown): Persisted {
  const state = record(record(data)?.state);
  if (!state) return NOTHING_PERSISTED;
  return { lastJob: lastJob(state.lastJob), lastApply: outcome(state.lastApply), lastRebuild: rebuildOutcome(state.lastRebuild) };
}

/** What `saveData` writes: the settings, and the persisted state under `state`
 * (`mergeSettings` ignores it). */
export function withPersisted(settings: ArtmindSettings, persisted: Persisted): Record<string, unknown> {
  return { ...settings, state: persisted };
}
```

Append to `src/admin.ts`:

```ts
/** The admin console page that prefills its agent chat with `prompt`
 * (never sends it): the deep link of checklist spec B7. */
export function promptPath(prompt: string): string {
  return `/?prompt=${encodeURIComponent(prompt)}`;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/persist.test.ts test/admin.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/src/persist.ts obsidian/artmind-obsidian/src/admin.ts obsidian/artmind-obsidian/test/persist.test.ts obsidian/artmind-obsidian/test/admin.test.ts
git commit -m "feat(obsidian): persist last job/apply/rebuild beside the settings; admin prompt link

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The status bar — `artmind ●●○○○ <current step>`, a click opens the panel

**Files:**
- Modify: `obsidian/artmind-obsidian/src/views/statusBar.ts` (whole file)
- Modify: `obsidian/artmind-obsidian/src/texts.ts` (append `STATUS_READY`)
- Modify: `obsidian/artmind-obsidian/src/main.ts:105`, `:145`
- Test: `obsidian/artmind-obsidian/test/views.test.ts` (the `StatusBarItem` describe)

- [ ] **Step 1: Write the failing test**

In `test/views.test.ts`, add `import { checklist } from "../src/checklist";` to the imports, and add this helper after `function snapshot(...) { ... }`:

```ts
/** Every row of the checklist done: each view test changes one thing. */
function readyInputs(overrides: Partial<StateInputs> = {}): StateInputs {
  return {
    ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    tablesReview: { query_type: "structured", command: "db review", pending_count: 0, tables: [] },
    projection: fixture("projection-status.ready").json,
    synthesis: [],
    git: { ahead: 0, artmindChanges: [] },
    ...overrides,
  };
}
```

Replace the whole `describe("StatusBarItem (spec §3.1)", ...)` block with:

```ts
describe("StatusBarItem (checklist spec §4)", () => {
  it("shows a dot per row and footer, the current step and its tone; a click only opens the panel", () => {
    const el = document.createElement("div");
    el.classList.add("status-bar-item");
    let opened = 0;
    const item = new StatusBarItem(el, () => opened++);

    item.render(checklist(readyInputs({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json })));

    expect(el.textContent).toBe("artmind ○○●●● 2 docs · 1 table to apply");
    expect(el.classList.contains("artmind-tone-amber")).toBe(true);
    expect(el.classList.contains("status-bar-item")).toBe(true);
    expect(el.getAttribute("aria-label")).toBe("From the other laptop — 2 docs · 1 table to apply");
    el.click();
    expect(opened).toBe(1);

    item.render(checklist(readyInputs()));
    expect(el.textContent).toBe("artmind ●●●●● graph ready");
    expect(el.classList.contains("artmind-tone-amber")).toBe(false);
    expect(el.classList.contains("artmind-tone-grey")).toBe(true);
    expect(el.getAttribute("aria-label")).toBe("Graph ready ✓");
  });

  it("names the footer once it is the only step left", () => {
    const el = document.createElement("div");
    new StatusBarItem(el, () => undefined).render(checklist(readyInputs({ git: { ahead: 2, artmindChanges: [] } })));
    expect(el.textContent).toBe("artmind ●●●●○ ↑ 2 to push");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/views.test.ts -t StatusBarItem`
Expected: FAIL: `TypeError: Cannot read properties of undefined (reading 'label')` (`render` still expects a `VaultStateResult`).

- [ ] **Step 3: Implement**

Append to `src/texts.ts`:

```ts
/** The status bar's label when no step is left. */
export const STATUS_READY = "graph ready";
```

Replace `src/views/statusBar.ts` with:

```ts
import type { Checklist } from "../checklist";
import type { Tone } from "../state";
import { STATUS_READY, readinessText } from "../texts";

const TONES: Tone[] = ["red", "amber", "blue", "grey", "spinner"];

/** `artmind ●●○○○ 7 to ingest`: one dot per row 1–4 and the footer, filled
 * when done, then the current step's summary (checklist spec §4). */
export function statusText(cl: Checklist): string {
  const dots = cl.rows.filter((r) => !r.optional).map((r) => (r.state === "done" ? "●" : "○")).join("");
  const current = cl.rows.find((r) => r.id === cl.current);
  return `artmind ${dots} ${current ? current.summary : STATUS_READY}`;
}

/** The status bar item. A click opens the panel at the current row; it
 * never runs an action (D8). */
export class StatusBarItem {
  private el: HTMLElement;

  constructor(el: HTMLElement, onClick: () => void) {
    this.el = el;
    el.classList.add("artmind-status");
    el.addEventListener("click", () => onClick());
  }

  render(cl: Checklist): void {
    const current = cl.rows.find((r) => r.id === cl.current);
    this.el.textContent = statusText(cl);
    for (const tone of TONES) this.el.classList.remove(`artmind-tone-${tone}`);
    this.el.classList.add(`artmind-tone-${current?.tone ?? "grey"}`);
    this.el.setAttribute("aria-label", current ? [current.title, current.summary, ...current.detail.slice(0, 1)].join(" — ") : readinessText(cl));
  }
}
```

In `src/main.ts`, add `import { checklist } from "./checklist";` after the `./cli` import. Replace line 105 with:

```ts
    // Opens the panel; Task 10 makes it open at the current row.
    this.statusBar = new StatusBarItem(this.addStatusBarItem(), () => void this.openPanel());
```

and line 145 with:

```ts
    this.statusBar?.render(checklist(snapshot.inputs));
```

- [ ] **Step 4: Run the tests**

Run: `just obsidian-plugin-test`
Expected: PASS. (`controller.perform` is no longer called by `main.ts`, but its tests still use it until Task 10.)

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/src/views/statusBar.ts obsidian/artmind-obsidian/src/texts.ts obsidian/artmind-obsidian/src/main.ts obsidian/artmind-obsidian/test/views.test.ts
git commit -m "feat(obsidian): status bar shows the checklist's dots and current step, opens the panel

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The ribbon — click opens the panel; the menu keeps three items

**Files:**
- Modify: `obsidian/artmind-obsidian/src/views/ribbon.ts` (whole file)
- Modify: `obsidian/artmind-obsidian/src/main.ts:98-104`, `:143-150`
- Test: `obsidian/artmind-obsidian/test/ribbon.test.ts` (whole file)

- [ ] **Step 1: Write the failing test**

Replace `test/ribbon.test.ts` with:

```ts
// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { checklist } from "../src/checklist";
import { EMPTY_INPUTS, type StateInputs } from "../src/state";
import { RibbonIcon, attentionTone, ribbonItems } from "../src/views/ribbon";
import { fixture } from "./fixtures";
import { Menu } from "./mocks/obsidian";

function state(overrides: Partial<StateInputs> = {}) {
  return checklist({
    ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    tablesReview: { query_type: "structured", command: "db review", pending_count: 0, tables: [] },
    projection: fixture("projection-status.ready").json,
    synthesis: [],
    git: { ahead: 0, artmindChanges: [] },
    ...overrides,
  });
}

function recorder() {
  const calls: string[] = [];
  const r = (name: string) => () => void calls.push(name);
  return { calls, handlers: { openPanel: r("openPanel"), openAdmin: r("openAdmin"), doctor: r("doctor") } };
}

beforeEach(() => {
  Menu.shown.length = 0;
});

describe("the ribbon icon (checklist spec §4)", () => {
  it("has no dot when nothing is left, else the current step's tone", () => {
    expect(attentionTone(state())).toBeNull();
    expect(attentionTone(state({ status: fixture("vault-status.behind").json }))).toBe("amber");
    expect(attentionTone(state({ status: fixture("vault-status.merge-conflict").json }))).toBe("red");
    expect(attentionTone(state({ activeJob: fixture("ingest-job-status.running").json }))).toBe("spinner");
    expect(attentionTone(state({ git: { ahead: 1, artmindChanges: [] } }))).toBe("blue");
  });

  it("shows the dot as classes, and clears them", () => {
    const el = document.createElement("div");
    const icon = new RibbonIcon(el, recorder().handlers);

    icon.render(state({ status: fixture("vault-status.behind").json }));
    expect([...el.classList]).toEqual(["artmind-ribbon", "artmind-ribbon-attention", "artmind-ribbon-amber"]);
    expect(el.getAttribute("aria-label")).toBe("artmind: 2 docs · 1 table to apply");

    icon.render(state());
    expect([...el.classList]).toEqual(["artmind-ribbon"]);
  });

  it("right-click offers only the panel, the admin console and the doctor: never an action", () => {
    const el = document.createElement("div");
    const r = recorder();
    const icon = new RibbonIcon(el, r.handlers);
    icon.render(state({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json }));

    el.dispatchEvent(new MouseEvent("contextmenu", { cancelable: true }));

    const items = Menu.shown[0].items;
    expect(items.map((i) => [i.title, i.disabled])).toEqual([
      ["Open side panel", false],
      ["Open admin console", false],
      ["Run doctor", false],
    ]);
    items[1].onClickFn!();
    items[2].onClickFn!();
    expect(r.calls).toEqual(["openAdmin", "doctor"]);
  });

  it("blocks the admin console and the doctor when artmind itself is missing", () => {
    const items = ribbonItems(state({ problem: { kind: "missing", looked: [] } }), recorder().handlers);
    expect(items.map((i) => i.blocked)).toEqual([null, "artmind not found (looked in nowhere)", "artmind not found (looked in nowhere)"]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/ribbon.test.ts`
Expected: FAIL: `attentionTone` reads `state.primary` (`TypeError: Cannot read properties of undefined (reading 'kind')`), and the menu still lists Sync and Ingest.

- [ ] **Step 3: Implement**

Replace `src/views/ribbon.ts` with:

```ts
import { Menu } from "obsidian";
import type { Checklist } from "../checklist";
import type { Tone } from "../state";
import { COMMANDS } from "../texts";

export interface RibbonHandlers {
  openPanel(): void;
  openAdmin(): void;
  doctor(): void;
}

export interface RibbonItem {
  title: string;
  icon: string;
  /** Why it can't run now, or null; shown in the title, the item disabled. */
  blocked: string | null;
  run: () => void;
}

/** The right-click menu (checklist spec §4): no row action is here; they
 * are the panel's and the palette's. */
export function ribbonItems(cl: Checklist, handlers: RibbonHandlers): RibbonItem[] {
  return [
    { title: COMMANDS.openPanel, icon: "panel-right", blocked: null, run: handlers.openPanel },
    { title: COMMANDS.openAdmin, icon: "external-link", blocked: cl.blocked.admin, run: handlers.openAdmin },
    { title: COMMANDS.doctor, icon: "stethoscope", blocked: cl.blocked.doctor, run: handlers.doctor },
  ];
}

/** The dot on the icon: the current step's tone, none when nothing is left. */
export function attentionTone(cl: Checklist): Tone | null {
  return cl.rows.find((r) => r.id === cl.current)?.tone ?? null;
}

const TONES: Tone[] = ["red", "amber", "blue", "grey", "spinner"];

/** The left ribbon's artmind icon. A click opens the panel (D8); right-click
 * offers the panel, the admin console and the doctor. */
export class RibbonIcon {
  private el: HTMLElement;
  private state: Checklist | null = null;
  private handlers: RibbonHandlers;

  constructor(el: HTMLElement, handlers: RibbonHandlers) {
    this.el = el;
    this.handlers = handlers;
    el.classList.add("artmind-ribbon");
    el.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      this.menu().showAtMouseEvent(event);
    });
  }

  menu(): Menu {
    const menu = new Menu();
    if (!this.state) {
      menu.addItem((item) => item.setTitle(COMMANDS.openPanel).setIcon("panel-right").onClick(() => this.handlers.openPanel()));
      return menu;
    }
    for (const entry of ribbonItems(this.state, this.handlers)) {
      menu.addItem((item) => {
        item.setTitle(entry.blocked ? `${entry.title} — ${entry.blocked}` : entry.title).setIcon(entry.icon);
        if (entry.blocked) item.setDisabled(true);
        else item.onClick(() => entry.run());
      });
    }
    return menu;
  }

  render(cl: Checklist): void {
    this.state = cl;
    const tone = attentionTone(cl);
    this.el.classList.toggle("artmind-ribbon-attention", tone !== null);
    for (const t of TONES) this.el.classList.toggle(`artmind-ribbon-${t}`, t === tone);
    const current = cl.rows.find((r) => r.id === cl.current);
    this.el.setAttribute("aria-label", current ? `artmind: ${current.summary}` : "artmind");
  }
}
```

In `src/main.ts`, replace the `this.ribbon = new RibbonIcon(...)` statement (lines 98–104) with:

```ts
    this.ribbon = new RibbonIcon(this.addRibbonIcon("brain-circuit", "artmind", () => void this.openPanel()), {
      openPanel: () => void this.openPanel(),
      openAdmin: () => void this.openAdmin(),
      doctor: () => void controller.runDoctor(),
    });
```

and in `render(snapshot)`, replace the two lines `this.statusBar?.render(checklist(snapshot.inputs));` and `this.ribbon?.render(snapshot.state);` with:

```ts
    const cl = checklist(snapshot.inputs);
    this.statusBar?.render(cl);
    this.ribbon?.render(cl);
```

- [ ] **Step 4: Run the tests**

Run: `just obsidian-plugin-test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/src/views/ribbon.ts obsidian/artmind-obsidian/src/main.ts obsidian/artmind-obsidian/test/ribbon.test.ts
git commit -m "feat(obsidian): ribbon opens the panel; its menu keeps panel, admin console, doctor

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The confirm modal and the Synthesize… modal

Standalone views; Task 10 has the controller open them.

**Files:**
- Create: `obsidian/artmind-obsidian/src/views/confirmModal.ts`
- Create: `obsidian/artmind-obsidian/src/views/synthesizeModal.ts`
- Test: `obsidian/artmind-obsidian/test/modals.test.ts` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/modals.test.ts`:

```ts
// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { DomainSynthesis } from "../src/state";
import { ConfirmModal, renderConfirm } from "../src/views/confirmModal";
import { SynthesizeModal, llmCalls, renderSynthesize } from "../src/views/synthesizeModal";

function button(root: HTMLElement, label: string): HTMLButtonElement {
  const found = [...root.querySelectorAll("button")].find((b) => b.textContent === label);
  if (!found) throw new Error(`no button "${label}"`);
  return found;
}

describe("the confirm modal (checklist spec §3.3)", () => {
  it("asks, offers the choices with the first filled, and runs the one clicked after closing", () => {
    const root = document.createElement("div");
    const order: string[] = [];
    renderConfirm(
      root,
      {
        text: "The other laptop has changes not applied to your graph.",
        choices: [
          { label: "Apply first", run: () => order.push("apply") },
          { label: "Ingest anyway", run: () => order.push("ingest") },
        ],
      },
      () => order.push("close"),
    );

    expect(root.querySelector("p")!.textContent).toBe("The other laptop has changes not applied to your graph.");
    expect([...root.querySelectorAll("button")].map((b) => [b.textContent, b.classList.contains("mod-cta")])).toEqual([
      ["Apply first", true],
      ["Ingest anyway", false],
    ]);
    button(root, "Ingest anyway").click();
    expect(order).toEqual(["close", "ingest"]);
  });

  it("the modal runs nothing until a choice is clicked", () => {
    const run = vi.fn();
    const modal = new ConfirmModal({} as never, { text: "Pull from GitHub first?", choices: [{ label: "Pull, then apply", run }] });

    modal.open();
    expect(run).not.toHaveBeenCalled();
    button(modal.contentEl, "Pull, then apply").click();
    expect(run).toHaveBeenCalledOnce();
    expect(modal.isOpen).toBe(false);
  });
});

describe("the Synthesize… modal (checklist spec §3.4)", () => {
  const plan: DomainSynthesis[] = [
    { domain: "general", count: 41, model: "ministral-3:14b" },
    { domain: "habits", count: 5, model: "ministral-3:14b" },
    { domain: "empty", count: 0, model: "ministral-3:14b" },
  ];

  it("counts the LLM calls of the chosen domains, under the cap", () => {
    expect(llmCalls(plan, { domains: new Set(["general", "habits"]), limit: null })).toBe(46);
    expect(llmCalls(plan, { domains: new Set(["general", "habits"]), limit: 10 })).toBe(15);
    expect(llmCalls(plan, { domains: new Set(["habits"]), limit: null })).toBe(5);
  });

  it("lists each domain with its count and the model, and updates the total", () => {
    const root = document.createElement("div");
    const run = vi.fn();
    renderSynthesize(root, plan, { run, cancel: vi.fn() });

    expect([...root.querySelectorAll(".artmind-synth-domain")].map((l) => l.textContent)).toEqual([
      " general: 41 descriptions",
      " habits: 5 descriptions",
      " empty: 0 descriptions",
    ]);
    expect(root.textContent).toContain("Model: ministral-3:14b");
    expect(root.querySelector(".artmind-synth-total")!.textContent).toBe("46 LLM calls in total");

    const boxes = [...root.querySelectorAll<HTMLInputElement>("input[type=checkbox]")];
    expect(boxes.map((b) => [b.checked, b.disabled])).toEqual([[true, false], [true, false], [false, true]]);
    boxes[1].checked = false;
    boxes[1].dispatchEvent(new Event("change"));
    const cap = root.querySelector<HTMLInputElement>("input[type=number]")!;
    cap.value = "20";
    cap.dispatchEvent(new Event("input"));
    expect(root.querySelector(".artmind-synth-total")!.textContent).toBe("20 LLM calls in total");

    button(root, "Synthesize").click();
    expect(run).toHaveBeenCalledWith(["general"], 20);
  });

  it("can't run with nothing chosen", () => {
    const root = document.createElement("div");
    renderSynthesize(root, plan, { run: vi.fn(), cancel: vi.fn() });
    for (const box of root.querySelectorAll<HTMLInputElement>("input[type=checkbox]")) {
      box.checked = false;
      box.dispatchEvent(new Event("change"));
    }
    expect(button(root, "Synthesize").disabled).toBe(true);
  });

  it("the modal writes nothing until Synthesize is clicked, and closes first", () => {
    const run = vi.fn();
    const modal = new SynthesizeModal({} as never, { plan: () => plan, run });

    modal.open();
    expect(run).not.toHaveBeenCalled();
    button(modal.contentEl, "Synthesize").click();
    expect(modal.isOpen).toBe(false);
    expect(run).toHaveBeenCalledWith(["general", "habits"], null);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/modals.test.ts`
Expected: FAIL: `Failed to load url ../src/views/confirmModal`.

- [ ] **Step 3: Implement**

Create `src/views/confirmModal.ts`:

```ts
import { type App, Modal } from "obsidian";
import { el } from "./dom";

export interface ConfirmChoice {
  label: string;
  run: () => void;
}

/** An out-of-order click's question (checklist spec §3.3). The first
 * choice is the recommended one. */
export interface ConfirmRequest {
  text: string;
  choices: ConfirmChoice[];
}

export function renderConfirm(root: HTMLElement, request: ConfirmRequest, close: () => void): void {
  root.replaceChildren();
  el(root, "p", { cls: "artmind-confirm-text", text: request.text });
  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  request.choices.forEach((choice, index) => {
    el(buttons, "button", { cls: index === 0 ? "mod-cta" : "", text: choice.label }).addEventListener("click", () => {
      close();
      choice.run();
    });
  });
}

export class ConfirmModal extends Modal {
  private request: ConfirmRequest;

  constructor(app: App, request: ConfirmRequest) {
    super(app);
    this.request = request;
  }

  override onOpen(): void {
    renderConfirm(this.contentEl, this.request, () => this.close());
  }

  override onClose(): void {
    this.contentEl.replaceChildren();
  }
}
```

Create `src/views/synthesizeModal.ts`:

```ts
import { type App, Modal } from "obsidian";
import { type DomainSynthesis, plural } from "../state";
import { LABELS } from "../texts";
import { el } from "./dom";

export interface SynthesizeChoice {
  domains: Set<string>;
  /** `--limit`, per domain; null for no cap. */
  limit: number | null;
}

/** One LLM call per description: the chosen domains' counts, each capped. */
export function llmCalls(plan: DomainSynthesis[], choice: SynthesizeChoice): number {
  return plan
    .filter((d) => choice.domains.has(d.domain))
    .reduce((sum, d) => sum + (choice.limit === null ? d.count : Math.min(d.count, choice.limit)), 0);
}

export interface SynthesizeHandlers {
  run(domains: string[], limit: number | null): void;
  cancel(): void;
}

/** Each mapped domain with its count, the model, an optional cap, the
 * total (checklist spec §3.4). Domains with nothing to do can't be chosen. */
export function renderSynthesize(root: HTMLElement, plan: DomainSynthesis[], handlers: SynthesizeHandlers): void {
  root.replaceChildren();
  el(root, "h3", { text: "Synthesize descriptions" });
  const models = [...new Set(plan.map((d) => d.model))].join(", ");
  el(root, "p", { cls: "artmind-muted", text: `Model: ${models || "unknown"}` });
  const choice: SynthesizeChoice = { domains: new Set(plan.filter((d) => d.count > 0).map((d) => d.domain)), limit: null };

  const list = el(root, "div", { cls: "artmind-synth-domains" });
  const capLine = el(root, "label", { cls: "artmind-synth-cap" });
  const total = el(root, "p", { cls: "artmind-synth-total" });
  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  const runButton = el(buttons, "button", { cls: "mod-cta", text: LABELS.synthesizeRun });
  el(buttons, "button", { text: LABELS.cancel }).addEventListener("click", () => handlers.cancel());

  const update = () => {
    const calls = llmCalls(plan, choice);
    total.textContent = `${plural(calls, "LLM call")} in total`;
    runButton.disabled = calls === 0;
  };

  for (const d of plan) {
    const label = el(list, "label", { cls: "artmind-synth-domain" });
    const box = el(label, "input", { attr: { type: "checkbox" } });
    box.checked = choice.domains.has(d.domain);
    box.disabled = d.count === 0;
    box.addEventListener("change", () => {
      if (box.checked) choice.domains.add(d.domain);
      else choice.domains.delete(d.domain);
      update();
    });
    el(label, "span", { text: ` ${d.domain}: ${plural(d.count, "description")}` });
  }

  el(capLine, "span", { text: "Cap per domain (optional): " });
  const cap = el(capLine, "input", { attr: { type: "number", min: "1", placeholder: "no cap" } });
  cap.addEventListener("input", () => {
    const n = Number.parseInt(cap.value, 10);
    choice.limit = Number.isFinite(n) && n > 0 ? n : null;
    update();
  });

  runButton.addEventListener("click", () => {
    handlers.run(plan.filter((d) => choice.domains.has(d.domain)).map((d) => d.domain), choice.limit);
  });
  update();
}

export interface SynthesizeDeps {
  plan(): DomainSynthesis[];
  run(domains: string[], limit: number | null): void;
}

/** Synthesize… : nothing is written until [Synthesize] (D1). */
export class SynthesizeModal extends Modal {
  private deps: SynthesizeDeps;

  constructor(app: App, deps: SynthesizeDeps) {
    super(app);
    this.deps = deps;
  }

  override onOpen(): void {
    renderSynthesize(this.contentEl, this.deps.plan(), {
      run: (domains, limit) => {
        this.close();
        this.deps.run(domains, limit);
      },
      cancel: () => this.close(),
    });
  }

  override onClose(): void {
    this.contentEl.replaceChildren();
  }
}
```

- [ ] **Step 4: Run the tests**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/modals.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/src/views/confirmModal.ts obsidian/artmind-obsidian/src/views/synthesizeModal.ts obsidian/artmind-obsidian/test/modals.test.ts
git commit -m "feat(obsidian): confirm modal for out-of-order clicks; Synthesize… modal with per-domain counts and cap

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The controller runs on the checklist — no buttons in notices, confirm modals, Rebuild graph, Synthesize…, Commit & push, the palette

The switch. Notices lose their buttons and gain a target row; every refresh also reads Obsidian Git's auto pull interval (Task 3b's parser, through `app.vault.adapter.read`); out-of-order clicks open the confirm modal; the new actions run through the write queue; the heavy reads get their cadence; the last job, apply and rebuild are saved. `main.ts` is rewritten in full here, palette included. The panel keeps rendering from `snapshot.state` / `lastSync` / `jobResults` (kept in `Snapshot` for one task) until Task 11 rewrites it.

**Files:**
- Modify: `obsidian/artmind-obsidian/src/settings.ts`, `src/views/settingsTab.ts:53-61`
- Modify: `obsidian/artmind-obsidian/src/texts.ts` (`pulledText`, `newFilesText`, `ingestDoneText`, `stalledText`; delete `syncDoneText`)
- Modify: `obsidian/artmind-obsidian/src/views/notices.ts` (whole file)
- Modify: `obsidian/artmind-obsidian/src/controller.ts` (whole file)
- Modify: `obsidian/artmind-obsidian/src/main.ts` (whole file)
- Modify: `obsidian/artmind-obsidian/src/views/panel.ts:425-428` (`focus`)
- Test: `test/settings.test.ts`, `test/texts.test.ts`, `test/views.test.ts` (the notices describe), `test/controller.test.ts` (whole file), `test/main.test.ts`

- [ ] **Step 1: Write the failing settings tests**

In `test/settings.test.ts`, replace the first and third `it(...)` blocks with:

```ts
  it("has no commit offer any more: the footer replaces it (checklist spec §4)", () => {
    expect("offerCommitAndSync" in DEFAULT_SETTINGS).toBe(false);
    expect(DEFAULT_SETTINGS.adminInObsidianTab).toBe(true);
    expect(DEFAULT_SETTINGS.headPollSeconds).toBe(15);
    expect(DEFAULT_SETTINGS.jobPollSeconds).toBe(3);
    expect(DEFAULT_SETTINGS.newFileGroupSeconds).toBe(10);
    expect(Object.keys(DEFAULT_SETTINGS.notices).sort()).toEqual([
      "applyDone",
      "ingestDone",
      "newFiles",
      "pulled",
      "rebuildDone",
      "resolveDone",
      "synthesizeDone",
      "table2graphDone",
    ]);
    expect(Object.values(DEFAULT_SETTINGS.notices).every(Boolean)).toBe(true);
  });
```

```ts
  it("keeps saved values and defaults the rest", () => {
    const merged = mergeSettings({ artmindPath: "/opt/artmind", notices: { newFiles: false, rebuildDone: false }, offerCommitAndSync: false });
    expect(merged.artmindPath).toBe("/opt/artmind");
    expect(merged.notices.newFiles).toBe(false);
    expect(merged.notices.rebuildDone).toBe(false);
    expect(merged.notices.synthesizeDone).toBe(true);
    expect("offerCommitAndSync" in merged).toBe(false);
    expect(mergeSettings({ adminInObsidianTab: false }).adminInObsidianTab).toBe(false);
    expect(mergeSettings({ adminInObsidianTab: "yes" }).adminInObsidianTab).toBe(true);
    expect(merged.headPollSeconds).toBe(15);
  });
```

- [ ] **Step 2: Write the failing texts tests**

In `test/texts.test.ts`, remove `syncDoneText,` from the import list, and replace the whole `describe("notice texts (spec §3.3)", ...)` block with:

```ts
describe("notice texts (checklist spec §5)", () => {
  it("a pull that left changes to apply", () => {
    expect(pulledText(fixture("vault-status.behind").json)).toBe("Pulled: 2 docs, 1 table to apply to graph.");
  });

  it("new files, named by their folder", () => {
    expect(newFilesText(["Team_performance_management/a.pdf", "Team_performance_management/b.xlsx"])).toBe(
      "2 new files in Team_performance_management, ready to ingest.",
    );
    expect(newFilesText(["A/x.pdf", "B/y.pdf"])).toBe("2 new files in 2 folders, ready to ingest.");
    expect(newFilesText(["deep/er/x.csv"])).toBe("1 new file in er, ready to ingest.");
  });

  it("an ingest job that finished", () => {
    expect(ingestDoneText(fixture("ingest-job-status.done").json)).toBe("Ingested 4 files, 1 failed.");
    const allGood = { ...fixture("ingest-job-status.done").json, files: [{ filename: "a", status: "completed", current_step: null }] };
    expect(ingestDoneText(allGood)).toBe("Ingested 1 file.");
    expect(ingestDoneText(fixture("ingest-job-status.finalize-failed").json)).toBe("Ingested 2 files; building the graph failed.");
  });

  it("a stalled job: one line, details in the panel", () => {
    expect(stalledText(fixture("ingest-job-status.stalled").json)).toBe("Ingest stalled at 2 of 5 files — details in the artmind panel.");
  });

  it("a table projected", () => {
    expect(projectedText(fixture("table2graph.dry-run").json.tables[0])).toBe("team projected: 7 entities.");
  });

  it("resolve, with and without files left for the user", () => {
    expect(resolvedText(0)).toBe("Artmind files resolved.");
    expect(resolvedText(2)).toBe(
      'Artmind files resolved. Resolve the 2 files listed under "Reported" in Obsidian, then complete the merge.',
    );
  });
});
```

and add `stalledText,` to the import list from `../src/texts`.

- [ ] **Step 3: Write the failing notifier test**

In `test/views.test.ts`, replace the whole `describe("notices (spec §3.3)", ...)` block with:

```ts
describe("notices (checklist spec §5, D7)", () => {
  it("are text only, fade after 6 s, and a click opens the panel at their row", () => {
    const opened: string[] = [];
    new ObsidianNotifier((target) => opened.push(target)).show("pulled", "Pulled: 2 docs to apply to graph.", "remote");

    const notice = Notice.shown[0];
    expect(notice.duration).toBe(6_000);
    const holder = document.createElement("div");
    holder.appendChild(notice.message as DocumentFragment);
    expect(holder.querySelectorAll("button")).toHaveLength(0);
    expect(text(holder)).toBe("Pulled: 2 docs to apply to graph.");
    (holder.querySelector(".artmind-notice") as HTMLElement).click();
    expect(opened).toEqual(["remote"]);
    expect(notice.hidden).toBe(true);
  });

  it("an error stays 10 s; a notice with no row opens nothing", () => {
    const opened: string[] = [];
    const notifier = new ObsidianNotifier((target) => opened.push(target));
    notifier.show("error", "Couldn't apply to graph — details in the artmind panel.", "remote");
    notifier.show("prompt", "Opening the admin console…");

    expect(Notice.shown.map((n) => n.duration)).toEqual([10_000, 6_000]);
    const holder = document.createElement("div");
    holder.appendChild(Notice.shown[1].message as DocumentFragment);
    (holder.querySelector(".artmind-notice") as HTMLElement).click();
    expect(opened).toEqual([]);
  });
});
```

- [ ] **Step 4: Run them to verify they fail**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/settings.test.ts test/texts.test.ts test/views.test.ts -t "settings|notice texts|notices"`
Expected: FAIL: `offerCommitAndSync` is still in the defaults; the texts are today's ("Pulled from the other laptop — …"); the notifier is constructed with no argument and calls `undefined`.

- [ ] **Step 5: Settings**

In `src/settings.ts`, replace

```ts
export type NoticeKind = "pulled" | "newFiles" | "ingestDone" | "syncDone" | "table2graphDone" | "resolveDone";
```

with

```ts
export type NoticeKind =
  | "pulled"
  | "newFiles"
  | "ingestDone"
  | "applyDone"
  | "table2graphDone"
  | "resolveDone"
  | "rebuildDone"
  | "synthesizeDone";
```

Delete these lines from `ArtmindSettings`:

```ts
  /** Offer Obsidian Git's Commit-and-sync after artmind writes (spec P6). */
  offerCommitAndSync: boolean;
```

In `DEFAULT_SETTINGS`, replace the `notices: {...}` object and the `offerCommitAndSync: true,` line with:

```ts
  notices: {
    pulled: true,
    newFiles: true,
    ingestDone: true,
    applyDone: true,
    table2graphDone: true,
    resolveDone: true,
    rebuildDone: true,
    synthesizeDone: true,
  },
```

Replace `NOTICE_LABELS` with:

```ts
export const NOTICE_LABELS: Record<NoticeKind, string> = {
  pulled: "A pull leaves changes to apply to graph",
  newFiles: "A binary or table lands in a mapped folder",
  ingestDone: "An ingest job finishes",
  applyDone: "Apply to graph finishes",
  table2graphDone: "A table is projected to the graph",
  resolveDone: "Resolving artmind conflicts finishes",
  rebuildDone: "Rebuild graph finishes",
  synthesizeDone: "Synthesize finishes",
};
```

In `mergeSettings`, delete the line `    offerCommitAndSync: pick("offerCommitAndSync"),`.

In `src/views/settingsTab.ts`, delete the `new Setting(containerEl).setName("Offer to commit-and-sync after artmind writes")...;` statement (lines 53–61), and change the class comment to:

```ts
/** Settings → artmind (spec §7): the artmind path, poll intervals, the admin
 * console, and a toggle per notice kind. */
```

- [ ] **Step 6: Texts**

In `src/texts.ts`, replace `pulledText`, `newFilesText`, `ingestDoneText` and `stalledText` with:

```ts
export function pulledText(status: VaultStatus): string {
  const parts = behindParts(status.sync.stores);
  return `Pulled: ${parts.join(", ") || "changes"} to apply to graph.`;
}

export function newFilesText(paths: string[]): string {
  const folders = [...new Set(paths.map((p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "the vault root")))];
  const where = folders.length === 1 ? folders[0].split("/").pop()! : plural(folders.length, "folder");
  return `${plural(paths.length, "new file")} in ${where}, ready to ingest.`;
}

export function ingestDoneText(job: JobStatus): string {
  const failed = job.files.filter((f) => f.status === "failed").length;
  const done = job.files.filter((f) => f.status === "completed").length;
  const base = `Ingested ${plural(done, "file")}${failed ? `, ${failed} failed` : ""}`;
  return job.finalize?.state === "failed" ? `${base}; building the graph failed.` : `${base}.`;
}

export function stalledText(job: JobStatus): string {
  return `Ingest stalled at ${job.processed_count} of ${plural(job.file_count, "file")} — details in the artmind panel.`;
}
```

Delete `syncDoneText` (its replacement is `applyDoneText`).

- [ ] **Step 7: The notifier**

Replace `src/views/notices.ts` with:

```ts
import { Notice } from "obsidian";
import type { NoticeLevel, Notifier } from "../controller";
import type { PanelTarget } from "../state";
import { el } from "./dom";

/** Text-only notices (checklist spec §5, D7). Each fades: 10 s for an
 * error, 6 s otherwise. Clicking one opens the panel at its row. */
export class ObsidianNotifier implements Notifier {
  private openTarget: (target: PanelTarget) => void;

  constructor(openTarget: (target: PanelTarget) => void) {
    this.openTarget = openTarget;
  }

  show(kind: NoticeLevel, text: string, target: PanelTarget | null = null): void {
    const fragment = document.createDocumentFragment();
    const body = el(fragment, "div", { cls: `artmind-notice artmind-notice-${kind}`, text });
    const notice = new Notice(fragment, kind === "error" ? 10_000 : 6_000);
    if (!target) return;
    body.classList.add("artmind-notice-link");
    body.addEventListener("click", () => {
      notice.hide();
      this.openTarget(target);
    });
  }
}
```

(`NoticeLevel` is exported by the controller in Step 10; this file type-checks once Step 10 is in.)

- [ ] **Step 8: Write the failing controller tests**

Replace `test/controller.test.ts` with:

```ts
import { describe, expect, it } from "vitest";
import type { GitAction } from "../src/bridge";
import type { CliResult } from "../src/cli";
import { errorText } from "../src/cli";
import { type CliLike, Controller, type Snapshot, type ViewsLike } from "../src/controller";
import type { Persisted } from "../src/persist";
import { DEFAULT_SETTINGS, type ArtmindSettings } from "../src/settings";
import type { PanelTarget, RowId } from "../src/state";
import { CONFIRM, NOTICE, countOf } from "../src/texts";
import type { ConfirmRequest } from "../src/views/confirmModal";
import { fixture } from "./fixtures";

function result(args: string[], exitCode: number, json: unknown, stderr = "", stdout?: string): CliResult {
  return {
    args,
    exitCode,
    ok: exitCode === 0,
    json,
    stdout: stdout ?? (json === null ? "" : JSON.stringify(json)),
    stderr,
    error: exitCode === 0 ? null : errorText(stderr),
    timedOut: false,
    startedAt: 0,
    durationMs: 10,
  };
}

/** A CliResult replayed from a captured fixture. */
function replay(name: string): CliResult {
  const f = fixture(name);
  return result(f.args, f.exit_code, f.json, f.stderr ?? "");
}

function failure(args: string[], message: string): CliResult {
  return result(args, 1, null, `╭─ Error ─╮\n│ ${message} │\n╰─╯`);
}

function dryRun(domain: string): CliResult {
  const f = fixture("projection-synthesize.dry-run");
  return result(["projection", "synthesize", "--domain", domain, "--dry-run", "--compact"], 0, { ...f.json, domain });
}

const NO_REVIEW = { query_type: "structured", command: "db review", pending_count: 0, tables: [] };
const MODEL: string = fixture("projection-synthesize.dry-run").json.model;
const REBUILT: number = fixture("projection-rebuild.sweep").json.rebuilt;

/** A scripted answer: a result, or a function of the call's detail (the
 * job id, table, domain…). */
type Script = Partial<Record<keyof CliLike, CliResult | ((detail: string) => CliResult)>>;

/** A CliLike answering from `script`, recording every call. Unscripted
 * reads answer "every row done". */
class FakeCli implements CliLike {
  path = "artmind";
  calls: string[] = [];
  script: Script;

  constructor(script: Script = {}) {
    this.script = script;
  }

  private answer(name: keyof CliLike, fallback: () => CliResult, detail = ""): Promise<CliResult> {
    this.calls.push(detail ? `${name} ${detail}` : name);
    const scripted = this.script[name];
    return Promise.resolve(typeof scripted === "function" ? scripted(detail) : (scripted ?? fallback()));
  }

  setPath(path: string) {
    this.path = path;
  }
  version() {
    return this.answer("version", () => result(["--version"], 0, null, "", "artmind 0.9.0\n"));
  }
  status() {
    return this.answer("status", () => replay("vault-status.in-sync"));
  }
  sync() {
    return this.answer("sync", () => replay("vault-sync.success"));
  }
  resolve(dryRun: boolean) {
    return this.answer("resolve", () => replay("vault-resolve.dry-run"), dryRun ? "--dryRun" : "");
  }
  doctor() {
    return this.answer("doctor", () => replay("vault-doctor"));
  }
  ingestPending() {
    return this.answer("ingestPending", () => result(["ingest", "pending", "--compact"], 0, { notes: [], binaries: [], tables: [], errors: [] }));
  }
  ingestAsyncPending() {
    return this.answer("ingestAsyncPending", () => replay("ingest-async.pending"));
  }
  jobStatus(jobId: string) {
    return this.answer("jobStatus", () => replay("ingest-job-status.done"), jobId);
  }
  jobResults(jobId: string) {
    return this.answer("jobResults", () => replay("ingest-job-results.done"), jobId);
  }
  retryJob(jobId: string) {
    return this.answer("retryJob", () => replay("ingest-retry-job.failed"), jobId);
  }
  jobsActive() {
    return this.answer("jobsActive", () => result(["ingest", "jobs-active", "--compact"], 0, []));
  }
  tablesPending() {
    return this.answer("tablesPending", () => result(["ingest", "table2graph", "--pending", "--compact"], 0, []));
  }
  tableDryRun(table: string) {
    return this.answer("tableDryRun", () => replay("table2graph.dry-run"), table);
  }
  tableProject(table: string) {
    return this.answer("tableProject", () => replay("table2graph.dry-run"), table);
  }
  projectionStatus() {
    return this.answer("projectionStatus", () => replay("projection-status.ready"));
  }
  projectionRebuildSweep() {
    return this.answer("projectionRebuildSweep", () => replay("projection-rebuild.sweep"));
  }
  synthesizeDryRun(domain: string) {
    return this.answer("synthesizeDryRun", () => dryRun(domain), domain);
  }
  synthesize(domain: string, limit: number | null) {
    const args = ["projection", "synthesize", "--domain", domain, ...(limit === null ? [] : ["--limit", String(limit)]), "--compact"];
    return this.answer(
      "synthesize",
      () => result(args, 0, { domain, command: "projection_synthesize", model: MODEL, dry_run: false, examined: 3, synthesized: 1, counts: {}, results: [] }),
      limit === null ? domain : `${domain} --limit ${limit}`,
    );
  }
  dbReview() {
    return this.answer("dbReview", () => result(["db", "review", "--compact"], 0, NO_REVIEW));
  }
}

interface Shown {
  kind: string;
  text: string;
  target: PanelTarget | null;
}

function setup(options: {
  script?: Script;
  settings?: Partial<ArtmindSettings>;
  gitCommands?: GitAction[];
  pullIsOld?: boolean;
  /** Obsidian Git's autoPullInterval; 0 (its default) unless a test turns it on. */
  autoPull?: number;
  changes?: string[];
  ahead?: number | null;
  domains?: string[];
  persisted?: Persisted;
  detected?: { path: string | null; looked: string[] };
} = {}) {
  const cli = new FakeCli(options.script);
  const shown: Shown[] = [];
  const views = { renders: [] as Snapshot[], opened: [] as string[], confirms: [] as ConfirmRequest[] };
  const ran: GitAction[] = [];
  const tracked: number[] = [];
  const saved: Persisted[] = [];
  const available = options.gitCommands ?? ["pull", "commitAndSync", "commit"];
  let clock = 1_000_000;
  const viewsLike: ViewsLike = {
    render: (snapshot) => views.renders.push(snapshot),
    openPanel: (target) => views.opened.push(`panel:${target}`),
    openResolve: () => views.opened.push("resolve"),
    openTableReview: (table) => views.opened.push(`table:${table}`),
    confirm: (request) => views.confirms.push(request),
    openSynthesize: () => views.opened.push("synthesize"),
    openAdmin: (path) => views.opened.push(`admin:${path}`),
  };
  const controller = new Controller({
    cli,
    git: { ahead: async () => (options.ahead === undefined ? 0 : options.ahead), artmindChanges: async () => options.changes ?? [] },
    bridge: {
      has: (action) => available.includes(action),
      run: (action) => {
        if (!available.includes(action)) return false;
        ran.push(action);
        return true;
      },
      instruction: (action) => `Run Obsidian Git: ${action}`,
      warning: () => null,
    },
    notifier: { show: (kind, text, target = null) => shown.push({ kind, text, target }) },
    views: viewsLike,
    settings: () => ({ ...DEFAULT_SETTINGS, ...options.settings }),
    detect: async () => options.detected ?? { path: "/Users/me/.local/bin/artmind", looked: ["/Users/me/.local/bin/artmind"] },
    domains: () => options.domains ?? ["general"],
    readObsidianGit: async () => ({ autoPullMinutes: options.autoPull ?? 0 }),
    persisted: options.persisted,
    persist: (p) => saved.push(p),
    now: () => clock,
    schedule: (fn) => fn(),
  });
  controller.attachWatchers({
    trackJob: () => tracked.push(clock),
    pullIsOld: () => options.pullIsOld ?? false,
    waitForPull: async () => "head",
  });
  const choose = (text: string, label: string) => {
    const request = views.confirms.find((r) => r.text === text);
    if (!request) throw new Error(`no confirm "${text}" in ${JSON.stringify(views.confirms.map((r) => r.text))}`);
    const choice = request.choices.find((c) => c.label === label);
    if (!choice) throw new Error(`no choice "${label}" on "${text}"`);
    choice.run();
  };
  const flush = async () => {
    for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  };
  const rowOf = (id: RowId) => controller.checklist.rows.find((r) => r.id === id)!;
  return { cli, controller, shown, views, ran, tracked, saved, choose, flush, rowOf, advance: (ms: number) => (clock += ms) };
}

const WRITES = ["sync", "ingestAsyncPending", "tableProject", "resolve", "projectionRebuildSweep", "synthesize", "retryJob"];

describe("reading state (checklist spec §5.1)", () => {
  it("the first refresh runs every read, heavy ones included, and writes nothing", async () => {
    const t = setup({ domains: ["general", "habits", "general"] });

    await t.controller.refresh();

    expect(t.cli.calls.sort()).toEqual([
      "dbReview",
      "ingestPending",
      "jobsActive",
      "projectionStatus",
      "status",
      "synthesizeDryRun general",
      "synthesizeDryRun habits",
      "tablesPending",
    ]);
    expect(t.cli.calls.some((c) => WRITES.includes(c.split(" ")[0]))).toBe(false);
    const last = t.views.renders.at(-1)!;
    expect(last.checklist.ready).toBe(true);
    expect(last.inputs.synthesis).toEqual([
      { domain: "general", count: 1, model: MODEL },
      { domain: "habits", count: 1, model: MODEL },
    ]);
  });

  it("later refreshes skip the heavy reads unless asked", async () => {
    const t = setup();
    await t.controller.refresh();
    t.cli.calls.length = 0;

    await t.controller.refresh();
    expect(t.cli.calls).not.toContain("projectionStatus");

    await t.controller.refresh({ heavy: true });
    expect(t.cli.calls).toEqual(expect.arrayContaining(["projectionStatus", "dbReview", "synthesizeDryRun general"]));
  });

  it("on focus, the heavy reads run at most once a minute", async () => {
    const t = setup();
    await t.controller.refresh();
    t.cli.calls.length = 0;

    t.advance(30_000);
    t.controller.scheduleRefresh("focus");
    await t.flush();
    expect(t.cli.calls).toContain("status");
    expect(t.cli.calls).not.toContain("projectionStatus");

    t.advance(30_000);
    t.controller.scheduleRefresh("focus");
    await t.flush();
    expect(t.cli.calls).toContain("projectionStatus");
  });

  it("keeps a failed heavy read visible, and still counts the other domains", async () => {
    const t = setup({
      domains: ["general", "habits"],
      script: {
        synthesizeDryRun: (domain) =>
          domain === "habits" ? failure(["projection", "synthesize", "--domain", "habits", "--dry-run", "--compact"], "graph unreachable") : dryRun(domain),
      },
    });

    await t.controller.refresh();

    expect(t.controller.inputs.synthesis).toEqual([{ domain: "general", count: 1, model: MODEL }]);
    expect(t.controller.snapshot().readErrors).toEqual(["artmind projection synthesize --domain habits --dry-run --compact: graph unreachable"]);
  });

  it("keeps a failed background read visible, not silent", async () => {
    const t = setup({ script: { tablesPending: failure(["ingest", "table2graph", "--pending", "--compact"], "bad.yaml: invalid YAML") } });

    await t.controller.refresh();

    expect(t.controller.readErrors).toEqual(["artmind ingest table2graph --pending --compact: bad.yaml: invalid YAML"]);
  });

  it("a failing vault status is a problem, shown in row 1", async () => {
    const t = setup({ script: { status: failure(["vault", "status", "--compact"], "Not inside an artmind vault.") } });

    await t.controller.refresh();

    expect(t.controller.inputs.problem).toEqual({ kind: "failed", command: "vault status", message: "Not inside an artmind vault." });
    expect(t.rowOf("remote").summary).toBe("artmind can't run");
  });

  it("picks up a job started elsewhere and follows it", async () => {
    const t = setup({ script: { jobsActive: replay("ingest-jobs-active") } });

    await t.controller.refresh();

    expect(t.controller.inputs.activeJob?.job_id).toBe(fixture("ingest-jobs-active").json[0].job_id);
    expect(t.tracked.length).toBe(1);
  });

  it("the footer counts every artmind change, the plugin's own included (decision 6)", async () => {
    const t = setup({ changes: [".artmind/data/kg/general/doc/document.json"] });

    await t.controller.refresh();

    expect(t.rowOf("vault").summary).toBe("✎ 1 artmind file to commit · ↑ 0");
  });
});

describe("after a pull (spec P2)", () => {
  it("says what there is to apply, with no button, and applies nothing", async () => {
    const t = setup({ script: { status: replay("vault-status.behind") } });

    await t.controller.onHeadSettled();

    expect(t.shown).toEqual([{ kind: "pulled", text: "Pulled: 2 docs, 1 table to apply to graph.", target: "remote" }]);
    expect(t.cli.calls).not.toContain("sync");
  });

  it("says nothing when nothing is left to apply, or the notice is off", async () => {
    const quiet = setup();
    await quiet.controller.onHeadSettled();
    expect(quiet.shown).toEqual([]);

    const off = setup({ script: { status: replay("vault-status.behind") }, settings: { notices: { ...DEFAULT_SETTINGS.notices, pulled: false } } });
    await off.controller.onHeadSettled();
    expect(off.shown).toEqual([]);
  });

  it("new files: a text-only notice that opens row 2", () => {
    const t = setup();
    t.controller.onNewFiles(["Team/a.pdf"]);
    expect(t.shown).toEqual([{ kind: "newFiles", text: "1 new file in Team, ready to ingest.", target: "documents" }]);
  });
});

describe("Apply to graph (checklist spec §3.3)", () => {
  async function behind(options: Parameters<typeof setup>[0] = {}) {
    const t = setup({ ...options, script: { status: replay("vault-status.behind"), ...options.script } });
    await t.controller.refresh();
    return t;
  }

  it("asks to pull first when auto pull is off and the last pull is old, in the confirm modal", async () => {
    const t = await behind({ pullIsOld: true });

    await t.controller.apply();

    expect(t.views.confirms.map((r) => [r.text, r.choices.map((c) => c.label)])).toEqual([
      [CONFIRM.pullFirst, ["Pull, then apply", "Apply without pulling"]],
    ]);
    expect(t.cli.calls).not.toContain("sync");
    expect(t.shown).toEqual([]);

    t.choose(CONFIRM.pullFirst, "Pull, then apply");
    await t.flush();
    expect(t.ran).toEqual(["pull"]);
    expect(t.cli.calls).toContain("sync");
  });

  it("never asks to pull when Obsidian Git's auto pull is on (D10)", async () => {
    const t = await behind({ pullIsOld: true, autoPull: 10 });
    t.cli.calls.length = 0;

    await t.controller.apply();

    expect(t.views.confirms).toEqual([]);
    expect(t.cli.calls[0]).toBe("sync");
    expect(t.controller.inputs.obsidianGit).toEqual({ autoPullMinutes: 10 });
  });

  it("with auto pull off, row 1 says to turn it on", async () => {
    const t = await behind();
    expect(t.rowOf("remote").detail).toContain("Turn on Obsidian Git's auto pull to see the other laptop's changes");
  });

  it("applies straight away when Obsidian Git has no Pull command", async () => {
    const t = await behind({ pullIsOld: true, gitCommands: ["commit"] });
    t.cli.calls.length = 0;
    await t.controller.apply();
    expect(t.cli.calls[0]).toBe("sync");
  });

  it("says what it applied and names the next step; remembers it", async () => {
    const t = await behind({ script: { ingestPending: replay("ingest-pending") } });
    t.cli.script.status = replay("vault-status.in-sync");

    await t.controller.apply();

    expect(t.shown).toEqual([{ kind: "applyDone", text: "Applied to graph: 2 docs, 1 table. Next: ingest 4 files.", target: "remote" }]);
    expect(t.controller.inputs.lastApply).toMatchObject({ ok: true, summary: "Applied to graph: 2 docs, 1 table." });
    expect(t.saved.at(-1)!.lastApply?.summary).toBe("Applied to graph: 2 docs, 1 table.");
    expect(t.cli.calls).toEqual(expect.arrayContaining(["projectionStatus"]));
  });

  it("shows row 1 as applying while it runs", async () => {
    const t = await behind();
    await t.controller.apply();
    expect(t.views.renders.some((s) => s.checklist.rows[0].summary === "applying to graph…")).toBe(true);
  });

  it.each([
    ["vault-sync.refusal-merge-in-progress", NOTICE.resolveFirst],
    ["vault-sync.refusal-artmind-conflicts", NOTICE.resolveFirst],
    ["vault-sync.refusal-bookmark-unpulled", NOTICE.pullFirst],
    ["vault-sync.refusal-bookmark-not-ancestor", NOTICE.pullFirst],
    ["vault-sync.refusal-no-bookmark", NOTICE.noBookmark],
  ])("turns %s into a text-only notice", async (name, text) => {
    const t = await behind({ script: { sync: replay(name) } });

    await t.controller.apply();

    expect(t.shown).toEqual([{ kind: "prompt", text, target: "remote" }]);
  });

  it("an unknown failure is one friendly line; the raw error goes to row 1", async () => {
    const t = await behind({ script: { sync: failure(["vault", "sync", "--compact"], "git diff failed: fatal: bad object") } });

    await t.controller.apply();

    expect(t.shown).toEqual([{ kind: "error", text: "Couldn't apply to graph — details in the artmind panel.", target: "remote" }]);
    expect(t.rowOf("remote").detail).toContain("git diff failed: fatal: bad object");
    expect(t.controller.inputs.lastApply?.ok).toBe(false);
  });

  it("queues behind a job it didn't start, and applies when that job finishes", async () => {
    const t = await behind({ script: { sync: replay("vault-sync.refusal-worker-running") } });

    await t.controller.apply();
    expect(t.shown.map((s) => s.text)).toEqual([NOTICE.applyQueued]);

    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };
    t.cli.script.sync = replay("vault-sync.success");
    await t.controller.pollJob();
    await t.flush();
    expect(t.cli.calls.filter((c) => c === "sync")).toHaveLength(2);
    expect(t.shown.map((s) => s.kind)).toEqual(["prompt", "ingestDone", "applyDone"]);
  });

  it("is blocked while the plugin follows a job: says why, runs nothing", async () => {
    const t = await behind();
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    await t.controller.apply();

    expect(t.shown).toEqual([{ kind: "blocked", text: "An ingest job is running — apply after it finishes", target: "remote" }]);
    expect(t.cli.calls).not.toContain("sync");
  });
});

describe("Ingest what changed (checklist spec §3.3)", () => {
  it("asks to apply first when the other laptop has changes", async () => {
    const t = setup({ script: { status: replay("vault-status.behind"), ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();

    await t.controller.ingestWhatChanged();

    expect(t.views.confirms.map((r) => [r.text, r.choices.map((c) => c.label)])).toEqual([[CONFIRM.applyFirst, ["Apply first", "Ingest anyway"]]]);
    expect(t.cli.calls).not.toContain("ingestAsyncPending");
  });

  it("[Apply first] applies, then submits exactly the pending files", async () => {
    const t = setup({ script: { status: replay("vault-status.behind"), ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();
    await t.controller.ingestWhatChanged();
    t.cli.script.status = replay("vault-status.in-sync");

    t.choose(CONFIRM.applyFirst, "Apply first");
    await t.flush();

    expect(t.cli.calls.filter((c) => c === "sync" || c === "ingestAsyncPending")).toEqual(["sync", "ingestAsyncPending"]);
    expect(t.controller.inputs.activeJob?.job_id).toBe(fixture("ingest-async.pending").json.job_id);
    expect(t.tracked.length).toBeGreaterThan(0);
  });

  it("[Ingest anyway] ingests without applying", async () => {
    const t = setup({ script: { status: replay("vault-status.behind"), ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();
    await t.controller.ingestWhatChanged();

    t.choose(CONFIRM.applyFirst, "Ingest anyway");
    await t.flush();

    expect(t.cli.calls).toContain("ingestAsyncPending");
    expect(t.cli.calls).not.toContain("sync");
  });

  it("following the current step never asks, and never asks about pulling (decision 4)", async () => {
    const t = setup({ pullIsOld: true, script: { ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();

    await t.controller.ingestWhatChanged();

    expect(t.views.confirms).toEqual([]);
    expect(t.cli.calls).toContain("ingestAsyncPending");
  });

  it("says so when nothing is pending, and starts no job", async () => {
    const t = setup({ script: { ingestAsyncPending: replay("ingest-async.nothing-pending") } });

    await t.controller.ingestWhatChanged();

    expect(t.shown).toEqual([{ kind: "prompt", text: NOTICE.nothingToIngest, target: "documents" }]);
    expect(t.controller.inputs.activeJob).toBeNull();
    expect(t.tracked).toEqual([]);
  });

  it("warns, and still ingests, when Neo4j is unreachable", async () => {
    const t = setup({ script: { status: replay("vault-status.neo4j-unreachable"), ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();

    await t.controller.ingestWhatChanged();

    expect(t.shown[0]).toEqual({ kind: "prompt", text: NOTICE.neo4jIngest, target: "documents" });
    expect(t.cli.calls).toContain("ingestAsyncPending");
  });

  it("is blocked while a job already runs", async () => {
    const t = setup();
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    await t.controller.ingestWhatChanged();

    expect(t.shown).toEqual([{ kind: "blocked", text: "An ingest job is already running", target: "documents" }]);
    expect(t.cli.calls).not.toContain("ingestAsyncPending");
  });

  it("a failure to start is one friendly line", async () => {
    const t = setup({ script: { ingestAsyncPending: failure(["ingest", "async", "--pending", "--compact"], "registry locked") } });

    await t.controller.ingestWhatChanged();

    expect(t.shown).toEqual([{ kind: "error", text: "Couldn't start the ingest — details in the artmind panel.", target: "documents" }]);
    expect(t.rowOf("documents").detail).toContain("registry locked");
  });
});

describe("a job finishing (checklist spec §5)", () => {
  const cleanStatus = {
    ...fixture("ingest-job-status.done").json,
    status: "completed",
    file_count: 2,
    processed_count: 2,
    files: [
      { filename: "/vault/Notes/a.md", status: "completed", current_step: null, entities: 3, relationships: 1 },
      { filename: "/vault/Notes/b.md", status: "completed", current_step: null, entities: 2, relationships: 1 },
    ],
  };
  const cleanResults = {
    ...fixture("ingest-job-results.done").json,
    status: "completed",
    files: cleanStatus.files.map((f) => ({ filename: f.filename, status: f.status, error_message: null, entities: f.entities, relationships: f.relationships })),
  };

  it("names the next step, keeps the job for the panel and saves it", async () => {
    const t = setup({ script: { projectionStatus: replay("projection-status.needs-rebuild") } });
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    expect(await t.controller.pollJob()).toBe(false);

    expect(t.shown).toEqual([{ kind: "ingestDone", text: "Ingested 4 files, 1 failed. Next: retry failed.", target: "documents" }]);
    expect(t.cli.calls).toContain("jobResults 00000000-0000-4000-8000-000000000001");
    expect(t.controller.snapshot().jobResults?.files[0].entities).toBe(12);
    expect(t.saved.at(-1)!.lastJob?.status.job_id).toBe("00000000-0000-4000-8000-000000000001");
    expect(t.views.opened).toEqual([]);
  });

  it("a clean job's next step is the rebuild", async () => {
    const t = setup({
      script: {
        jobStatus: result(["ingest", "job-status", "j", "--compact"], 0, cleanStatus),
        jobResults: result(["ingest", "job-results", "j", "--compact"], 0, cleanResults),
        projectionStatus: replay("projection-status.needs-rebuild"),
      },
    });
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    await t.controller.pollJob();

    expect(t.shown.map((s) => s.text)).toEqual(["Ingested 2 files. Next: rebuild graph."]);
  });

  it("keeps polling while the deferred rebuild runs", async () => {
    const t = setup({ script: { jobStatus: replay("ingest-job-status.finishing") } });
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    expect(await t.controller.pollJob()).toBe(true);

    expect(t.shown).toEqual([]);
    expect(t.rowOf("documents").summary).toBe("finishing: building the graph");
    expect(t.rowOf("graph").summary).toBe("after ingest finishes");
  });

  it("a job whose rebuild failed says so, and row 4 names the error", async () => {
    const failed = fixture("ingest-job-status.finalize-failed").json;
    const t = setup({
      script: {
        jobStatus: replay("ingest-job-status.finalize-failed"),
        jobResults: result(["ingest", "job-results", failed.job_id, "--compact"], 0, cleanResults),
      },
    });
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    await t.controller.pollJob();

    expect(t.shown.map((s) => s.text)).toEqual(["Ingested 2 files; building the graph failed. Next: rebuild graph."]);
    expect(t.rowOf("graph").detail).toContain(`last job's rebuild failed: ${failed.finalize.error}`);
  });

  it("stops polling a stalled job and says so once, with no button", async () => {
    const t = setup({ script: { jobStatus: replay("ingest-job-status.stalled"), retryJob: replay("ingest-retry-job.stalled") } });
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    expect(await t.controller.pollJob()).toBe(false);
    expect(t.shown).toEqual([{ kind: "error", text: "Ingest stalled at 2 of 5 files — details in the artmind panel.", target: "documents" }]);
    await t.controller.pollJob();
    expect(t.shown).toHaveLength(1);

    t.controller.run({ type: "retryJob", jobId: "00000000-0000-4000-8000-000000000001" });
    await t.flush();
    expect(t.cli.calls).toContain("retryJob 00000000-0000-4000-8000-000000000001");
    expect(t.controller.inputs.activeJob).toMatchObject({ job_id: "00000000-0000-4000-8000-000000000001", status: "queued" });
    expect(t.shown.at(-1)!.text).toBe("Restarted the stalled job: retrying 1 file.");
  });

  it("retries a finished job's failed files and follows it again", async () => {
    const t = setup();
    t.controller.inputs = {
      ...t.controller.inputs,
      lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1 },
    };

    await t.controller.retryJob("00000000-0000-4000-8000-000000000001");

    expect(t.controller.inputs.lastJob).toBeNull();
    expect(t.controller.inputs.activeJob).toMatchObject({ status: "queued", file_count: 2, processed_count: 1 });
    expect(t.shown.at(-1)).toEqual({ kind: "prompt", text: "Retrying 1 failed file.", target: "documents" });
  });

  it("a refused retry is one friendly line; the reason goes to row 2", async () => {
    const t = setup({ script: { retryJob: failure(["ingest", "retry-job", "x", "--compact"], "Job 'x' is still running; retry it once it finishes") } });

    await t.controller.retryJob("x");

    expect(t.shown.at(-1)).toEqual({ kind: "error", text: "Couldn't retry the job — details in the artmind panel.", target: "documents" });
    expect(t.controller.inputs.actionErrors.documents).toBe("Job 'x' is still running; retry it once it finishes");
    expect(t.controller.inputs.activeJob).toBeNull();
  });
});

describe("Rebuild graph (checklist spec §3.2 row 4, B4)", () => {
  async function needsRebuild(script: Script = {}) {
    const t = setup({ script: { projectionStatus: replay("projection-status.needs-rebuild"), ...script } });
    await t.controller.refresh();
    t.cli.script.projectionStatus = replay("projection-status.ready");
    t.cli.calls.length = 0;
    return t;
  }

  it("runs projection rebuild --sweep, re-reads the graph, and says it is all embedded", async () => {
    const t = await needsRebuild();

    await t.controller.rebuild();

    expect(t.cli.calls[0]).toBe("projectionRebuildSweep");
    expect(t.cli.calls).toContain("projectionStatus");
    expect(t.shown).toEqual([{ kind: "rebuildDone", text: `Graph rebuilt: ${countOf(REBUILT, "entity", "entities")}, all embedded.`, target: "graph" }]);
    expect(t.rowOf("graph").state).toBe("done");
    expect(t.saved.at(-1)!.lastRebuild).toMatchObject({ ok: true, errors: [] });
  });

  it("shows row 4 as rebuilding while it runs", async () => {
    const t = await needsRebuild();
    await t.controller.rebuild();
    expect(t.views.renders.some((s) => s.checklist.rows[3].summary === "rebuilding…")).toBe(true);
  });

  it("sweep errors keep row 4 at needs rebuild, each a reason, and the notice is an error", async () => {
    const sweep = fixture("projection-rebuild.sweep");
    const t = await needsRebuild({
      projectionRebuildSweep: result(sweep.args, 0, { ...sweep.json, sweep_errors: ["general: chunk embed sweep skipped (Connection refused)"] }),
    });

    await t.controller.rebuild();

    expect(t.shown).toEqual([
      { kind: "error", text: `Graph rebuilt: ${countOf(REBUILT, "entity", "entities")}, but 1 embed sweep failed — details in the artmind panel.`, target: "graph" },
    ]);
    expect(t.rowOf("graph").state).toBe("attention");
    expect(t.rowOf("graph").detail).toEqual(["sweep failed: general: chunk embed sweep skipped (Connection refused)"]);
  });

  it("a failed rebuild is one friendly line; the raw error is row 4's reason", async () => {
    const t = await needsRebuild({
      projectionRebuildSweep: result(["projection", "rebuild", "--sweep", "--compact"], 1, null, "Neo.TransientError.General.MemoryPoolOutOfMemoryError\n"),
    });

    await t.controller.rebuild();

    expect(t.shown).toEqual([{ kind: "error", text: "Couldn't rebuild the graph: Neo4j ran out of memory — details in the artmind panel.", target: "graph" }]);
    expect(t.rowOf("graph").detail).toEqual(["last rebuild failed: Neo.TransientError.General.MemoryPoolOutOfMemoryError"]);
  });

  it("is blocked when the graph is up to date", async () => {
    const t = setup();
    await t.controller.refresh();

    await t.controller.rebuild();

    expect(t.shown).toEqual([{ kind: "blocked", text: "The graph is up to date", target: "graph" }]);
    expect(t.cli.calls).not.toContain("projectionRebuildSweep");
  });
});

describe("Synthesize… (checklist spec §3.4)", () => {
  it("opens the modal with each mapped domain's count", async () => {
    const t = setup({ domains: ["general", "habits"] });
    await t.controller.refresh();

    t.controller.openSynthesize();

    expect(t.views.opened).toEqual(["synthesize"]);
    expect(t.controller.synthesisPlan()).toEqual([
      { domain: "general", count: 1, model: MODEL },
      { domain: "habits", count: 1, model: MODEL },
    ]);
  });

  it("runs each chosen domain in turn, with the cap, then names the next step", async () => {
    const t = setup({ domains: ["general", "habits"], changes: [".artmind/data/curation/syntheses/e1.json"] });
    await t.controller.refresh();

    await t.controller.synthesize(["general", "habits"], 20);

    expect(t.cli.calls.filter((c) => c.startsWith("synthesize "))).toEqual(["synthesize general --limit 20", "synthesize habits --limit 20"]);
    expect(t.shown).toEqual([{ kind: "synthesizeDone", text: "Synthesized 2 descriptions. Next: commit & push.", target: "descriptions" }]);
  });

  it("a failed domain is one friendly line; the others still count", async () => {
    const t = setup({
      domains: ["general", "habits"],
      script: {
        synthesize: (detail) =>
          detail === "habits"
            ? failure(["projection", "synthesize", "--domain", "habits", "--compact"], "boom")
            : result(["projection", "synthesize", "--domain", detail, "--compact"], 0, { domain: detail, model: MODEL, dry_run: false, examined: 1, synthesized: 1, counts: {}, results: [] }),
      },
    });
    await t.controller.refresh();

    await t.controller.synthesize(["general", "habits"], null);

    expect(t.shown.map((s) => [s.kind, s.text])).toEqual([
      ["error", "Couldn't synthesize 1 domain — details in the artmind panel."],
      ["synthesizeDone", "Synthesized 1 description."],
    ]);
    expect(t.rowOf("descriptions").detail).toContain("habits: boom");
  });
});

describe("Commit & push (checklist spec D5)", () => {
  it("runs Obsidian Git's commit-and-sync", async () => {
    const t = setup({ changes: [".artmind/data/kg/general/doc/document.json"] });
    await t.controller.refresh();

    t.controller.commitPush();

    expect(t.ran).toEqual(["commitAndSync"]);
  });

  it("is blocked while a job is still writing files", async () => {
    const t = setup({ changes: [".artmind/data/kg/general/doc/document.json"] });
    await t.controller.refresh();
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    t.controller.commitPush();

    expect(t.shown).toEqual([{ kind: "blocked", text: "Files are still being written — commit after the ingest finishes", target: "vault" }]);
    expect(t.ran).toEqual([]);
  });

  it("says what to run when Obsidian Git lacks the command", async () => {
    const t = setup({ gitCommands: ["pull"], ahead: 1 });
    await t.controller.refresh();

    t.controller.commitPush();

    expect(t.shown).toEqual([{ kind: "error", text: "Run Obsidian Git: commitAndSync", target: "vault" }]);
  });
});

describe("review screens", () => {
  it("projects a table only on the click, and offers no commit after it", async () => {
    const t = setup();

    const preview = await t.controller.tableDryRun("team");
    expect(preview.report?.table).toBe("team");
    expect(t.cli.calls).toEqual(["tableDryRun team"]);

    await t.controller.tableProject("team");
    expect(t.shown).toEqual([{ kind: "table2graphDone", text: "team projected: 7 entities.", target: "tables" }]);
    expect(t.ran).toEqual([]);
  });

  it("reports a table with no mapping", async () => {
    const t = setup({ script: { tableDryRun: replay("table2graph.dry-run-no-mapping") } });

    const preview = await t.controller.tableDryRun("budget");

    expect(preview.report).toBeNull();
    expect(preview.error).toMatch(/^no table mapping matches 'budget'/);
  });

  it("after resolve, names Complete the merge as the next step", async () => {
    const merging = structuredClone(fixture("vault-status.merge-conflict").json);
    merging.sync.unresolved_conflicts = [];
    const clean = { ...fixture("vault-resolve.dry-run").json, pending: [], reported: [], remaining: 0, dry_run: false };
    const t = setup({ script: { status: result(["vault", "status", "--compact"], 0, merging), resolve: result(["vault", "resolve", "--compact"], 0, clean) } });

    await t.controller.resolveApply();

    expect(t.shown).toEqual([{ kind: "resolveDone", text: "Artmind files resolved. Next: complete the merge.", target: "remote" }]);
    t.controller.run({ type: "completeMerge" });
    expect(t.ran).toEqual(["commit"]);

    const left = setup();
    await left.controller.resolveApply();
    expect(left.shown[0].text).toMatch(/Resolve the 3 files listed under "Reported"/);
  });

  it("keeps the doctor's report even though it exits 1", async () => {
    const t = setup();

    await t.controller.runDoctor();

    expect(t.controller.doctor?.ok).toBe(false);
    expect(t.views.opened).toEqual(["panel:doctor"]);
    expect(t.shown).toEqual([]);
  });

  it("Review tables for graph is blocked with nothing waiting", async () => {
    const t = setup();
    await t.controller.refresh();

    t.controller.reviewTables();

    expect(t.shown).toEqual([{ kind: "blocked", text: "No tables waiting", target: "tables" }]);
    expect(t.views.opened).toEqual([]);
  });
});

describe("row buttons (run)", () => {
  it("each action does what its button says", async () => {
    const t = setup({
      script: {
        status: replay("vault-status.behind"),
        projectionStatus: replay("projection-status.needs-rebuild"),
        tablesPending: replay("table2graph.pending"),
      },
    });
    await t.controller.refresh();

    t.controller.run({ type: "pull" });
    t.controller.run({ type: "completeMerge" });
    t.controller.run({ type: "adminPrompt", prompt: "Review x" });
    t.controller.run({ type: "reviewTable", table: "team" });
    t.controller.run({ type: "synthesize" });
    t.controller.run({ type: "resolve" });
    t.controller.run({ type: "rebuild" });
    await t.flush();

    expect(t.ran).toEqual(["pull", "commit"]);
    expect(t.views.opened).toEqual(["admin:/?prompt=Review%20x", "table:team", "synthesize"]);
    expect(t.shown).toContainEqual({ kind: "blocked", text: "No merge in progress", target: "remote" });
    expect(t.cli.calls).toContain("projectionRebuildSweep");
  });
});

describe("persistence (checklist spec §4)", () => {
  it("starts from what was saved", () => {
    const persisted: Persisted = {
      lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1 },
      lastApply: { at: 2, ok: true, summary: "Applied to graph: 2 docs, 1 table." },
      lastRebuild: null,
    };
    const t = setup({ persisted });

    expect(t.controller.persisted()).toEqual(persisted);
    expect(t.controller.snapshot().jobResults?.job_id).toBe("00000000-0000-4000-8000-000000000001");
  });
});

describe("artmind itself (spec §8)", () => {
  it("is missing when no path is found", async () => {
    const t = setup({ detected: { path: null, looked: ["/Users/me/.local/bin/artmind"] } });

    const problem = await t.controller.checkArtmind();

    expect(problem).toEqual({ kind: "missing", looked: ["/Users/me/.local/bin/artmind"] });
    expect(t.rowOf("remote").summary).toBe("artmind can't run");
  });

  it("is too old below the minimum, or without --version", async () => {
    const old = setup({ script: { version: result(["--version"], 0, null, "", "artmind 0.8.5\n") } });
    expect(await old.controller.checkArtmind()).toEqual({ kind: "too_old", found: "0.8.5", need: "0.9.0" });

    const ancient = setup({ script: { version: failure(["--version"], "No such option: --version") } });
    expect((await ancient.controller.checkArtmind())?.kind).toBe("too_old");
  });

  it("uses the path it found", async () => {
    const t = setup();
    expect(await t.controller.checkArtmind()).toBeNull();
    expect(t.cli.path).toBe("/Users/me/.local/bin/artmind");
  });

  it("does not run commands while artmind is missing", async () => {
    const t = setup({ detected: { path: null, looked: [] } });
    await t.controller.checkArtmind();
    t.cli.calls.length = 0;

    await t.controller.refresh();

    expect(t.cli.calls).toEqual([]);
  });
});

describe("activity", () => {
  it("keeps the last 20 runs, newest first", async () => {
    const t = setup();
    for (let i = 0; i < 25; i++) await t.controller.tableDryRun(`t${i}`);
    expect(t.controller.activity).toHaveLength(20);
    expect(t.controller.activity[0].command).toBe("artmind ingest table2graph team --dryRun --compact");
  });
});
```

- [ ] **Step 9: Run them to verify they fail**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/controller.test.ts`
Expected: FAIL: `TypeError: t.controller.apply is not a function` and similar (`rebuild`, `synthesize`, `commitPush`, `run`, `checklist` do not exist), and notices recorded with a buttons array instead of a target.

- [ ] **Step 10: Rewrite the controller**

Replace `src/controller.ts` with:

```ts
import { promptPath } from "./admin";
import type { GitAction } from "./bridge";
import { type Checklist, type CommandId, type RowAction, checklist, jobRunning } from "./checklist";
import type { CliResult } from "./cli";
import type { Persisted } from "./persist";
import type { ArtmindSettings, NoticeKind } from "./settings";
import {
  type ArtmindProblem,
  type Busy,
  type DomainSynthesis,
  EMPTY_INPUTS,
  type Outcome,
  type PanelTarget,
  type RowId,
  type StateInputs,
  type VaultStateResult,
  classifyRefusal,
  plural,
  vaultState,
} from "./state";
import {
  CONFIRM,
  LABELS,
  NOTICE,
  applyDoneText,
  failedText,
  ingestDoneText,
  newFilesText,
  projectedText,
  pulledText,
  rebuiltText,
  resolvedText,
  retriedText,
  stalledText,
  synthesizedText,
  withNext,
} from "./texts";
import type {
  DbReview,
  DoctorReport,
  IngestPending,
  JobResults,
  JobStatus,
  ProjectionStatus,
  RebuildResult,
  ResolveReport,
  RetryResult,
  Submitted,
  SyncResult,
  SynthesizeDryRun,
  SynthesizeResult,
  TablePending,
  TableReport,
  VaultStatus,
} from "./types";
import { MIN_ARTMIND_VERSION, atLeast, formatVersion, parseVersion } from "./version";
import type { ConfirmRequest } from "./views/confirmModal";

/** The slices of the other units the flows use -- narrow, so tests can
 * stand them in. */
export interface CliLike {
  path: string;
  setPath(path: string): void;
  version(): Promise<CliResult>;
  status(): Promise<CliResult>;
  sync(): Promise<CliResult>;
  resolve(dryRun: boolean): Promise<CliResult>;
  doctor(): Promise<CliResult>;
  ingestPending(): Promise<CliResult>;
  ingestAsyncPending(): Promise<CliResult>;
  jobStatus(jobId: string): Promise<CliResult>;
  jobResults(jobId: string): Promise<CliResult>;
  jobsActive(): Promise<CliResult>;
  retryJob(jobId: string): Promise<CliResult>;
  tablesPending(): Promise<CliResult>;
  tableDryRun(table: string): Promise<CliResult>;
  tableProject(table: string): Promise<CliResult>;
  projectionStatus(): Promise<CliResult>;
  projectionRebuildSweep(): Promise<CliResult>;
  synthesizeDryRun(domain: string): Promise<CliResult>;
  synthesize(domain: string, limit: number | null): Promise<CliResult>;
  dbReview(): Promise<CliResult>;
}

export interface GitLike {
  ahead(): Promise<number | null>;
  artmindChanges(): Promise<string[]>;
}

export interface BridgeLike {
  has(action: GitAction): boolean;
  run(action: GitAction): boolean;
  instruction(action: GitAction): string;
  warning(): string | null;
}

export interface WatchersLike {
  trackJob(): void;
  pullIsOld(maxAgeMs?: number): boolean;
  waitForPull(timeoutMs?: number): Promise<string | null>;
}

/** A notice kind the settings can turn off, or one that always shows. */
export type NoticeLevel = NoticeKind | "prompt" | "error" | "blocked";

/** Text-only notices (checklist spec D7). `target` is the panel row a click opens. */
export interface Notifier {
  show(kind: NoticeLevel, text: string, target?: PanelTarget | null): void;
}

export interface ViewsLike {
  render(snapshot: Snapshot): void;
  openPanel(target: PanelTarget): void;
  openResolve(): void;
  openTableReview(table: string | null): void;
  /** The confirm modal for an out-of-order click (checklist spec §3.3). */
  confirm(request: ConfirmRequest): void;
  openSynthesize(): void;
  /** A page of this vault's admin console, starting it if need be. */
  openAdmin(path: string): void;
}

export interface ActivityEntry {
  command: string;
  at: number;
  ok: boolean;
  summary: string;
  output: string;
}

export interface Snapshot {
  inputs: StateInputs;
  checklist: Checklist;
  /** Today's panel reads these three; Task 11 removes them. */
  state: VaultStateResult;
  lastSync: Outcome | null;
  jobResults: JobResults | null;
  activity: ActivityEntry[];
  doctor: DoctorReport | null;
  /** Failures of the background reads, heavy ones included. */
  readErrors: string[];
  bridgeWarning: string | null;
  /** The instruction shown instead of each missing Obsidian Git command. */
  gitMissing: Partial<Record<GitAction, string>>;
  looked: string[];
}

export interface ControllerDeps {
  cli: CliLike;
  git: GitLike;
  bridge: BridgeLike;
  notifier: Notifier;
  views: ViewsLike;
  settings: () => ArtmindSettings;
  detect: () => Promise<{ path: string | null; looked: string[] }>;
  /** The domains `.artmind/vault.yaml` maps: the synthesize dry runs' domains. */
  domains: () => string[];
  /** Obsidian Git's auto pull interval, read from its `data.json` on every
   * refresh (spec D10); an unreadable file reads as 0. */
  readObsidianGit: () => Promise<{ autoPullMinutes: number }>;
  /** What `saveData` kept from the last session. */
  persisted?: Persisted;
  /** Called whenever the last job, apply or rebuild changes. */
  persist?: (persisted: Persisted) => void;
  now?: () => number;
  /** Debounces refresh requests; the default runs them after 500 ms. */
  schedule?: (fn: () => void, ms?: number) => void;
}

export const ACTIVITY_LIMIT = 20;
/** Focus re-runs the heavy reads (Neo4j, DuckDB) at most this often (spec §5.1). */
export const HEAVY_READ_EVERY_MS = 60_000;

/** The flows: what each click does, in order (checklist spec §3, §4, §5).
 * Holds the latest inputs, hands views a snapshot with the checklist, and
 * gates every action on `checklist(inputs).blocked`. */
export class Controller {
  private deps: ControllerDeps;
  private watchers: WatchersLike | null = null;
  private refreshPending = false;
  private heavyPending = false;
  private lastHeavyAt = Number.NEGATIVE_INFINITY;
  private applyQueued: (() => void) | null = null;
  private heavyErrors: string[] = [];
  inputs: StateInputs = { ...EMPTY_INPUTS };
  activity: ActivityEntry[] = [];
  doctor: DoctorReport | null = null;
  /** Failures of the light background reads (ingest pending, table2graph --pending). */
  readErrors: string[] = [];
  looked: string[] = [];

  constructor(deps: ControllerDeps) {
    this.deps = deps;
    if (deps.persisted) this.inputs = { ...this.inputs, ...deps.persisted };
  }

  attachWatchers(watchers: WatchersLike): void {
    this.watchers = watchers;
  }

  private now(): number {
    return (this.deps.now ?? Date.now)();
  }

  get checklist(): Checklist {
    return checklist(this.inputs);
  }

  persisted(): Persisted {
    const { lastJob, lastApply, lastRebuild } = this.inputs;
    return { lastJob, lastApply, lastRebuild };
  }

  snapshot(): Snapshot {
    return {
      inputs: this.inputs,
      checklist: this.checklist,
      state: vaultState(this.inputs),
      lastSync: this.inputs.lastApply,
      jobResults: this.inputs.lastJob?.results ?? null,
      activity: this.activity,
      doctor: this.doctor,
      readErrors: [...this.readErrors, ...this.heavyErrors],
      bridgeWarning: this.deps.bridge.warning(),
      gitMissing: this.gitMissing(),
      looked: this.looked,
    };
  }

  private gitMissing(): Partial<Record<GitAction, string>> {
    const missing: Partial<Record<GitAction, string>> = {};
    for (const action of ["pull", "commitAndSync", "commit"] as GitAction[]) {
      if (!this.deps.bridge.has(action)) missing[action] = this.deps.bridge.instruction(action);
    }
    return missing;
  }

  private render(): void {
    this.deps.views.render(this.snapshot());
  }

  /** New inputs, rendered; saved when they touch what persists. */
  private update(patch: Partial<StateInputs>): void {
    this.inputs = { ...this.inputs, ...patch };
    if ("lastJob" in patch || "lastApply" in patch || "lastRebuild" in patch) this.deps.persist?.(this.persisted());
    this.render();
  }

  private setBusy(key: keyof Busy, on: boolean): void {
    this.update({ busy: { ...this.inputs.busy, [key]: on } });
  }

  private withError(row: RowId, error: string | null): Partial<Record<RowId, string>> {
    const errors = { ...this.inputs.actionErrors };
    if (error) errors[row] = error;
    else delete errors[row];
    return errors;
  }

  private setActionError(row: RowId, error: string | null): void {
    this.update({ actionErrors: this.withError(row, error) });
  }

  /** Every CLI result, for the Activity list (last 20). */
  record(result: CliResult): void {
    this.activity.unshift({
      command: `artmind ${result.args.join(" ")}`,
      at: result.startedAt,
      ok: result.ok,
      summary: result.ok ? `done in ${(result.durationMs / 1000).toFixed(1)} s` : `failed: ${result.error}`,
      output: [result.stdout, result.stderr].filter(Boolean).join("\n"),
    });
    this.activity.length = Math.min(this.activity.length, ACTIVITY_LIMIT);
  }

  private notify(kind: NoticeLevel, text: string, target: PanelTarget | null = null): void {
    if (kind !== "prompt" && kind !== "error" && kind !== "blocked" && !this.deps.settings().notices[kind]) return;
    this.deps.notifier.show(kind, text, target);
  }

  /** False, with the reason as a text-only notice, when `command` can't run
   * now. The panel disables its buttons the same way; this is for the
   * palette, which can't (checklist spec §4). */
  private guard(command: CommandId, target: PanelTarget): boolean {
    const reason = this.checklist.blocked[command];
    if (reason === null) return true;
    this.notify("blocked", reason, target);
    return false;
  }

  // ── artmind itself ─────────────────────────────────────────────────────────

  /** Find `artmind` and check its version (spec §8). */
  async checkArtmind(): Promise<ArtmindProblem | null> {
    const detected = await this.deps.detect();
    this.looked = detected.looked;
    let problem: ArtmindProblem | null = null;
    if (!detected.path) {
      problem = { kind: "missing", looked: detected.looked };
    } else {
      this.deps.cli.setPath(detected.path);
      const result = await this.deps.cli.version();
      this.record(result);
      const version = result.ok ? parseVersion(result.stdout) : null;
      if (!result.ok && /could not run/.test(result.error ?? "")) problem = { kind: "missing", looked: detected.looked };
      else if (!version) problem = { kind: "too_old", found: "unknown (no --version)", need: MIN_ARTMIND_VERSION };
      else if (!atLeast(version, MIN_ARTMIND_VERSION))
        problem = { kind: "too_old", found: formatVersion(version), need: MIN_ARTMIND_VERSION };
    }
    this.update({ problem });
    return problem;
  }

  // ── reading state ──────────────────────────────────────────────────────────

  /** A refresh soon, however many ask for one meanwhile. On focus it
   * includes the heavy reads when they are a minute old. */
  scheduleRefresh(reason = ""): void {
    if (reason === "focus" && this.now() - this.lastHeavyAt >= HEAVY_READ_EVERY_MS) this.heavyPending = true;
    if (this.refreshPending) return;
    this.refreshPending = true;
    const schedule = this.deps.schedule ?? ((fn, ms) => void setTimeout(fn, ms ?? 500));
    schedule(() => {
      this.refreshPending = false;
      const heavy = this.heavyPending;
      this.heavyPending = false;
      void this.refresh({ heavy });
    }, 500);
  }

  /** Every read at once: status, pending files and tables, active jobs, the
   * git facts, Obsidian Git's auto pull setting and, when `heavy` (or never
   * yet), projection status, db review and the synthesize dry runs.
   * Nothing here writes. */
  async refresh(opts: { heavy?: boolean } = {}): Promise<void> {
    const problem = this.inputs.problem;
    if (problem && problem.kind !== "failed") {
      this.render();
      return;
    }
    const heavy = Boolean(opts.heavy) || this.lastHeavyAt === Number.NEGATIVE_INFINITY;
    const { cli, git } = this.deps;
    const [status, pending, tables, active, ahead, changes, obsidianGit, heavyReads] = await Promise.all([
      cli.status(),
      cli.ingestPending(),
      cli.tablesPending(),
      this.inputs.activeJob ? Promise.resolve(null) : cli.jobsActive(),
      git.ahead(),
      git.artmindChanges(),
      this.deps.readObsidianGit(),
      heavy ? this.readHeavy() : Promise.resolve(null),
    ]);
    const readErrors: string[] = [];
    for (const result of [pending, tables]) {
      if (!result.ok) readErrors.push(`artmind ${result.args.join(" ")}: ${result.error}`);
    }
    for (const result of [status, pending, tables, active]) if (result && !result.ok) this.record(result);
    let activeJob = this.inputs.activeJob;
    if (!activeJob && active?.ok && Array.isArray(active.json) && active.json.length) {
      activeJob = active.json[0] as JobStatus;
      this.watchers?.trackJob();
    }
    this.readErrors = readErrors;
    this.update({
      status: status.ok ? (status.json as VaultStatus) : this.inputs.status,
      activeJob,
      pending: pending.ok ? (pending.json as IngestPending) : null,
      tablesPending: tables.ok ? (tables.json as TablePending[]) : null,
      git: { ahead, artmindChanges: changes },
      problem: status.ok ? null : { kind: "failed", command: "vault status", message: status.error ?? "failed" },
      obsidianGit,
      ...(heavyReads ?? {}),
    });
  }

  /** The reads that touch Neo4j and DuckDB (checklist spec §5.1). */
  private async readHeavy(): Promise<Pick<StateInputs, "projection" | "tablesReview" | "synthesis">> {
    this.lastHeavyAt = this.now();
    const { cli } = this.deps;
    const domains = [...new Set(this.deps.domains())].sort();
    const [projection, review, ...dryRuns] = await Promise.all([
      cli.projectionStatus(),
      cli.dbReview(),
      ...domains.map((domain) => cli.synthesizeDryRun(domain)),
    ]);
    const errors: string[] = [];
    for (const result of [projection, review, ...dryRuns]) {
      if (result.ok) continue;
      errors.push(`artmind ${result.args.join(" ")}: ${result.error}`);
      this.record(result);
    }
    this.heavyErrors = errors;
    const counted: DomainSynthesis[] = dryRuns
      .filter((r) => r.ok)
      .map((r) => {
        const run = r.json as SynthesizeDryRun;
        return { domain: run.domain, count: run.counts.synthesize ?? 0, model: run.model };
      });
    return {
      projection: projection.ok ? (projection.json as ProjectionStatus) : null,
      tablesReview: review.ok ? (review.json as DbReview) : null,
      synthesis: domains.length && !counted.length ? null : counted,
    };
  }

  // ── watchers' events ───────────────────────────────────────────────────────

  /** HEAD moved (a pull landed): refresh, then say what there is to apply (P2). */
  async onHeadSettled(): Promise<void> {
    await this.refresh();
    const status = this.inputs.status;
    const remote = this.checklist.rows.find((r) => r.id === "remote");
    if (status && remote?.buttons.some((b) => b.action.type === "apply")) this.notify("pulled", pulledText(status), "remote");
  }

  onNewFiles(paths: string[]): void {
    this.notify("newFiles", newFilesText(paths), "documents");
  }

  /** One job poll. False once the job is done (and the notice shown). A job
   * still finalizing (`finalize.state == pending`) is still running. */
  async pollJob(): Promise<boolean> {
    const job = this.inputs.activeJob;
    if (!job) return false;
    const result = await this.deps.cli.jobStatus(job.job_id);
    if (!result.ok) {
      this.record(result);
      return true;
    }
    const current = result.json as JobStatus;
    if (current.stalled) {
      // Nothing will move it on; stop polling and say so, once.
      const wasStalled = job.stalled;
      this.update({ activeJob: current });
      if (!wasStalled) this.notify("error", stalledText(current), "documents");
      return false;
    }
    if (jobRunning(current)) {
      this.update({ activeJob: current });
      return true;
    }
    // The job card stays up as "Last ingest job" until the next one starts,
    // across reloads; job-status alone carries no error messages.
    const results = await this.deps.cli.jobResults(current.job_id);
    this.record(results);
    this.update({ activeJob: null, lastJob: { status: current, results: results.ok ? (results.json as JobResults) : null, at: this.now() } });
    await this.refresh({ heavy: true });
    this.notify("ingestDone", withNext(ingestDoneText(current), this.checklist), "documents");
    const queued = this.applyQueued;
    this.applyQueued = null;
    if (queued) queued();
    return false;
  }

  /** Re-queue a job's failed files, or a stalled job (its worker gone),
   * and follow it again. `retry-job` refuses a job a live worker still runs. */
  async retryJob(jobId: string): Promise<void> {
    const before = this.inputs.activeJob?.job_id === jobId ? this.inputs.activeJob : null;
    const finished = this.inputs.lastJob?.status.job_id === jobId ? this.inputs.lastJob : null;
    const result = await this.deps.cli.retryJob(jobId);
    this.record(result);
    if (!result.ok) {
      this.setActionError("documents", result.error);
      this.notify("error", failedText("retry the job", result.error), "documents");
      return;
    }
    const retry = result.json as RetryResult;
    if (!retry.requeued) {
      this.notify("prompt", NOTICE.nothingToRetry, "documents");
      return;
    }
    const fileCount = before?.file_count ?? finished?.results?.file_count ?? finished?.status.file_count ?? retry.retried;
    this.update({
      lastJob: null,
      actionErrors: this.withError("documents", null),
      activeJob: {
        job_id: jobId,
        status: "queued",
        file_count: fileCount,
        // Until the first poll replaces it: what was done before stays done.
        processed_count: before?.processed_count ?? Math.max(0, fileCount - retry.retried),
        files: [],
      },
    });
    this.notify("prompt", retriedText(retry.retried, retry.stalled), "documents");
    this.watchers?.trackJob();
  }

  // ── actions ────────────────────────────────────────────────────────────────

  /** A row button's action (checklist spec §3.2). */
  run(action: RowAction): void {
    switch (action.type) {
      case "pull":
        this.pull();
        return;
      case "apply":
        void this.apply();
        return;
      case "resolve":
        this.openResolve();
        return;
      case "completeMerge":
        this.gitAction("commit", "remote");
        return;
      case "ingest":
        void this.ingestWhatChanged();
        return;
      case "retryJob":
        void this.retryJob(action.jobId);
        return;
      case "adminPrompt":
        this.deps.views.openAdmin(promptPath(action.prompt));
        return;
      case "reviewTable":
        this.reviewTables(action.table);
        return;
      case "rebuild":
        void this.rebuild();
        return;
      case "synthesize":
        this.openSynthesize();
        return;
      case "commitPush":
        this.commitPush();
        return;
    }
  }

  /** An Obsidian Git command, or its instruction when it is missing. */
  gitAction(action: GitAction, target: PanelTarget | null = null): boolean {
    if (this.deps.bridge.run(action)) return true;
    this.notify("error", this.deps.bridge.instruction(action), target);
    return false;
  }

  pull(): boolean {
    return this.gitAction("pull", "remote");
  }

  /** Obsidian Git's Pull, then `next` once HEAD settles. */
  async pullThen(next: () => void): Promise<void> {
    if (!this.pull()) return;
    await this.watchers?.waitForPull();
    await this.refresh();
    next();
  }

  /** Whether Apply should ask "Pull from GitHub first?" (spec §3.3, D10):
   * only with Obsidian Git's auto pull off (or not read yet) and the last
   * pull seen more than 5 minutes ago. */
  private shouldOfferPull(): boolean {
    const autoPull = this.inputs.obsidianGit?.autoPullMinutes ?? 0;
    return autoPull === 0 && Boolean(this.watchers?.pullIsOld()) && this.deps.bridge.has("pull");
  }

  /** Apply to graph (`vault sync`): asks to pull first when auto pull is off
   * and the last pull is old; a refusal becomes a text-only notice. */
  async apply(opts: { skipPull?: boolean; then?: () => void } = {}): Promise<void> {
    if (!this.guard("apply", "remote")) return;
    if (!opts.skipPull && this.shouldOfferPull()) {
      this.deps.views.confirm({
        text: CONFIRM.pullFirst,
        choices: [
          { label: LABELS.pullThenApply, run: () => void this.pullThen(() => void this.apply({ ...opts, skipPull: true })) },
          { label: LABELS.applyWithoutPulling, run: () => void this.apply({ ...opts, skipPull: true }) },
        ],
      });
      return;
    }
    this.setBusy("apply", true);
    const result = await this.deps.cli.sync();
    this.record(result);
    this.setBusy("apply", false);
    if (result.ok) {
      const summary = applyDoneText(result.json as SyncResult);
      this.update({ lastApply: { at: this.now(), ok: true, summary }, actionErrors: this.withError("remote", null) });
      await this.refresh({ heavy: true });
      this.notify("applyDone", withNext(summary, this.checklist), "remote");
      opts.then?.();
      return;
    }
    this.update({ lastApply: { at: this.now(), ok: false, summary: result.error ?? "failed" } });
    const refusal = classifyRefusal(result.error ?? "");
    switch (refusal.kind) {
      case "worker_running":
        this.queueApply(opts);
        break;
      case "resolve_first":
        this.notify("prompt", NOTICE.resolveFirst, "remote");
        break;
      case "pull_first":
        this.notify("prompt", NOTICE.pullFirst, "remote");
        break;
      case "no_bookmark":
        this.notify("prompt", NOTICE.noBookmark, "remote");
        break;
      default:
        this.setActionError("remote", refusal.text);
        this.notify("error", failedText("apply to graph", refusal.text), "remote");
    }
    await this.refresh();
  }

  /** A job this plugin didn't start holds the worker: apply once it ends. */
  private queueApply(opts: { then?: () => void }): void {
    this.applyQueued = () => void this.apply({ skipPull: true, then: opts.then });
    this.notify("prompt", NOTICE.applyQueued, "remote");
    this.watchers?.trackJob();
  }

  /** Ingest what changed: asks to apply first when row 1 has changes
   * (spec §3.3), then one background job for `ingest pending`'s files. */
  async ingestWhatChanged(opts: { skipApply?: boolean } = {}): Promise<void> {
    if (!this.guard("ingest", "documents")) return;
    const cl = this.checklist;
    const remote = cl.rows.find((r) => r.id === "remote");
    if (!opts.skipApply && remote?.buttons.some((b) => b.action.type === "apply")) {
      this.deps.views.confirm({
        text: CONFIRM.applyFirst,
        choices: [
          { label: LABELS.applyFirst, run: () => void this.apply({ then: () => void this.ingestWhatChanged({ skipApply: true }) }) },
          { label: LABELS.ingestAnyway, run: () => void this.ingestWhatChanged({ skipApply: true }) },
        ],
      });
      return;
    }
    if (cl.neo4jUnreachable) this.notify("prompt", NOTICE.neo4jIngest, "documents");
    const result = await this.deps.cli.ingestAsyncPending();
    this.record(result);
    if (!result.ok) {
      this.setActionError("documents", result.error);
      this.notify("error", failedText("start the ingest", result.error), "documents");
      return;
    }
    const submitted = result.json as Submitted;
    if (!submitted.job_id) {
      this.notify("prompt", NOTICE.nothingToIngest, "documents");
      await this.refresh();
      return;
    }
    this.update({
      lastJob: null,
      actionErrors: this.withError("documents", null),
      activeJob: { job_id: submitted.job_id, status: "queued", file_count: submitted.file_count, processed_count: 0, files: [] },
    });
    this.watchers?.trackJob();
  }

  /** Rebuild graph: `projection rebuild --sweep` (B4), then every heavy read again. */
  async rebuild(): Promise<void> {
    if (!this.guard("rebuild", "graph")) return;
    this.setBusy("rebuild", true);
    const result = await this.deps.cli.projectionRebuildSweep();
    this.record(result);
    this.setBusy("rebuild", false);
    if (!result.ok) {
      this.update({ lastRebuild: { at: this.now(), ok: false, summary: result.error ?? "failed", errors: [] } });
      this.notify("error", failedText("rebuild the graph", result.error), "graph");
      await this.refresh({ heavy: true });
      return;
    }
    const rebuilt = result.json as RebuildResult;
    await this.refresh({ heavy: true });
    const text = rebuiltText(rebuilt, this.inputs.projection);
    this.update({ lastRebuild: { at: this.now(), ok: true, summary: text, errors: rebuilt.sweep_errors } });
    if (rebuilt.sweep_errors.length) this.notify("error", text, "graph");
    else this.notify("rebuildDone", withNext(text, this.checklist), "graph");
  }

  /** What the Synthesize… modal offers: each mapped domain's count. */
  synthesisPlan(): DomainSynthesis[] {
    return this.inputs.synthesis ?? [];
  }

  openSynthesize(): void {
    if (this.guard("synthesize", "descriptions")) this.deps.views.openSynthesize();
  }

  /** `projection synthesize --domain D [--limit N]` for each domain, one
   * after another through the write queue (spec §3.4). */
  async synthesize(domains: string[], limit: number | null): Promise<void> {
    if (!this.guard("synthesize", "descriptions")) return;
    this.setBusy("synthesize", true);
    let written = 0;
    const failures: string[] = [];
    for (const domain of domains) {
      const result = await this.deps.cli.synthesize(domain, limit);
      this.record(result);
      if (result.ok) written += (result.json as SynthesizeResult).synthesized ?? 0;
      else failures.push(`${domain}: ${result.error}`);
    }
    this.setBusy("synthesize", false);
    this.setActionError("descriptions", failures.length ? failures.join("; ") : null);
    // Syntheses are vault files: the footer changes too.
    await this.refresh({ heavy: true });
    if (failures.length) this.notify("error", failedText(`synthesize ${plural(failures.length, "domain")}`, failures[0]), "descriptions");
    if (written || !failures.length) this.notify("synthesizeDone", withNext(synthesizedText(written), this.checklist), "descriptions");
  }

  /** Commit & push: Obsidian Git's commit, pull, push (D5, D6). */
  commitPush(): void {
    if (this.guard("commitPush", "vault")) this.gitAction("commitAndSync", "vault");
  }

  // ── review screens ─────────────────────────────────────────────────────────

  openResolve(): void {
    if (this.guard("resolve", "remote")) this.deps.views.openResolve();
  }

  reviewTables(table: string | null = null): void {
    if (!this.guard("tables", "tables")) return;
    const first = (this.inputs.tablesPending ?? []).find((t) => t.reason !== "ambiguous");
    this.deps.views.openTableReview(table ?? first?.table ?? null);
  }

  async tableDryRun(table: string): Promise<{ report: TableReport | null; error: string | null }> {
    const result = await this.deps.cli.tableDryRun(table);
    this.record(result);
    const report = result.ok ? ((result.json as { tables: TableReport[] }).tables[0] ?? null) : null;
    return { report, error: result.ok ? null : result.error };
  }

  /** `table2graph <table>`: its output (`table__*`) is gitignored, so no
   * Commit & push follows it (checklist spec §3.2). */
  async tableProject(table: string): Promise<boolean> {
    const result = await this.deps.cli.tableProject(table);
    this.record(result);
    if (!result.ok) {
      this.setActionError("tables", result.error);
      this.notify("error", failedText(`project ${table}`, result.error), "tables");
      return false;
    }
    const report = (result.json as { tables: TableReport[] }).tables[0];
    this.setActionError("tables", null);
    await this.refresh({ heavy: true });
    this.notify("table2graphDone", withNext(projectedText(report), this.checklist), "tables");
    return true;
  }

  async resolvePreview(): Promise<{ report: ResolveReport | null; error: string | null }> {
    const result = await this.deps.cli.resolve(true);
    this.record(result);
    return { report: result.ok ? (result.json as ResolveReport) : null, error: result.ok ? null : result.error };
  }

  /** `vault resolve`; row 1 then offers [Complete the merge]. */
  async resolveApply(): Promise<boolean> {
    const result = await this.deps.cli.resolve(false);
    this.record(result);
    if (!result.ok) {
      this.setActionError("remote", result.error);
      this.notify("error", failedText("resolve the artmind files", result.error), "remote");
      return false;
    }
    const report = result.json as ResolveReport;
    const left = report.reported.length + report.pending.length;
    await this.refresh();
    this.notify("resolveDone", left ? resolvedText(left) : withNext(resolvedText(0), this.checklist), "remote");
    return true;
  }

  async runDoctor(): Promise<void> {
    const result = await this.deps.cli.doctor();
    this.record(result);
    this.doctor = result.json && typeof result.json === "object" ? (result.json as DoctorReport) : null;
    if (!this.doctor) this.notify("error", failedText("run the doctor", result.error), "doctor");
    this.render();
    this.deps.views.openPanel("doctor");
  }
}
```

Two things to check while typing it in: `refresh` spreads `heavyReads ?? {}` so a light refresh leaves the last heavy values in place; and `Busy`, `Outcome`, `plural` are now imported from `./state` (all exported since Task 2 / today).

- [ ] **Step 11: Rewrite `main.ts` — wiring, persistence, the palette**

Replace `src/main.ts` with:

```ts
import { FileSystemAdapter, Plugin, parseYaml } from "obsidian";
import { AdminConsole, adminOutcomeText, hostPort, stopOutcomeText } from "./admin";
import { openInBrowser, realAdminDeps } from "./adminDeps";
import { ObsidianGitBridge, type CommandsLike, autoPullMinutes, obsidianGitDataPath } from "./bridge";
import { ArtmindCli, defaultDetectDeps, detectArtmind } from "./cli";
import { Controller, type Snapshot } from "./controller";
import { GitReader } from "./git";
import { type Mapping, VAULT_YAML, promptsOnArrival, readMappings } from "./manifest";
import { NOTHING_PERSISTED, type Persisted, readPersisted, withPersisted } from "./persist";
import { type ArtmindSettings, mergeSettings } from "./settings";
import type { PanelTarget } from "./state";
import { COMMANDS } from "./texts";
import { Watchers } from "./watchers";
import { type WebViewerApp, openInWebViewer, webViewerAvailable } from "./webviewer";
import { ConfirmModal } from "./views/confirmModal";
import { ObsidianNotifier } from "./views/notices";
import { ArtmindView, VIEW_TYPE } from "./views/panel";
import { ResolveModal } from "./views/resolveModal";
import { RibbonIcon } from "./views/ribbon";
import { ArtmindSettingTab } from "./views/settingsTab";
import { StatusBarItem } from "./views/statusBar";
import { SynthesizeModal } from "./views/synthesizeModal";
import { TableReviewModal } from "./views/tableReview";

/** Obsidian's private `app.commands` and `app.setting`, typed as far as used. */
interface AppInternals {
  commands?: CommandsLike;
  setting?: { open(): void; openTabById(id: string): void };
}

export default class ArtmindPlugin extends Plugin {
  artmindSettings!: ArtmindSettings;
  private persisted: Persisted = NOTHING_PERSISTED;
  private controller: Controller | null = null;
  private watchers: Watchers | null = null;
  private statusBar: StatusBarItem | null = null;
  private ribbon: RibbonIcon | null = null;
  private admin: AdminConsole | null = null;
  /** A notice's click opens the panel at its row (checklist spec D7). */
  private notifier = new ObsidianNotifier((target) => void this.openPanel(target));
  private mappings: Mapping[] = [];
  private last: Snapshot | null = null;

  override async onload(): Promise<void> {
    const data = await this.loadData();
    this.artmindSettings = mergeSettings(data);
    this.persisted = readPersisted(data);
    // Dormant outside an artmind vault (spec §7): no status bar, no polls.
    if (!(await this.app.vault.adapter.exists(VAULT_YAML))) return;
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) return;
    const root = adapter.getBasePath();
    await this.loadMappings();

    const internals = this.app as unknown as AppInternals;
    const cli = new ArtmindCli({ path: this.artmindSettings.artmindPath || "artmind", cwd: root });
    const bridge = new ObsidianGitBridge(() => internals.commands);
    const controller = new Controller({
      cli,
      git: new GitReader(root),
      bridge,
      notifier: this.notifier,
      views: {
        render: (snapshot) => this.render(snapshot),
        openPanel: (target) => void this.openPanel(target),
        openResolve: () => this.openResolve(),
        openTableReview: (table) => this.openTableReview(table),
        confirm: (request) => new ConfirmModal(this.app, request).open(),
        openSynthesize: () => this.openSynthesize(),
        openAdmin: (path) => void this.openAdmin(path),
      },
      settings: () => this.artmindSettings,
      detect: () => detectArtmind(this.artmindSettings.artmindPath, defaultDetectDeps()),
      domains: () => this.mappings.map((m) => m.domain),
      readObsidianGit: () => this.readObsidianGit(),
      persisted: this.persisted,
      persist: (persisted) => {
        this.persisted = persisted;
        void this.saveAll();
      },
    });
    this.controller = controller;
    const watchers = new Watchers(
      {
        readHead: () => new GitReader(root).head(),
        onHeadSettled: () => void controller.onHeadSettled(),
        onNewFiles: (paths) => controller.onNewFiles(paths),
        promptsOnArrival: (path) => promptsOnArrival(this.mappings, path),
        refresh: (reason) => {
          // Obsidian does not index `.artmind/`, so no event says the
          // manifest changed: re-read it whenever state is refreshed.
          void this.loadMappings();
          controller.scheduleRefresh(reason);
        },
        pollJob: () => controller.pollJob(),
      },
      {
        headPollMs: this.artmindSettings.headPollSeconds * 1000,
        newFileGroupMs: this.artmindSettings.newFileGroupSeconds * 1000,
        jobPollMs: this.artmindSettings.jobPollSeconds * 1000,
      },
    );
    this.watchers = watchers;
    controller.attachWatchers(watchers);

    this.admin = new AdminConsole(realAdminDeps(root, () => cli.path, (url) => void this.openAdminPage(url)), {
      url: () => this.artmindSettings.adminUiUrl,
      vaultRoot: root,
      startedPid: () => this.artmindSettings.adminUiPid,
      // Not saveSettings: a pid changes nothing artmind needs re-checked.
      saveStartedPid: async (pid) => {
        this.artmindSettings.adminUiPid = pid;
        await this.saveAll();
      },
    });
    // The ribbon and the status bar open the panel; they never run an action (D8).
    this.ribbon = new RibbonIcon(this.addRibbonIcon("brain-circuit", "artmind", () => void this.openPanel()), {
      openPanel: () => void this.openPanel(),
      openAdmin: () => void this.openAdmin(),
      doctor: () => void controller.runDoctor(),
    });
    this.statusBar = new StatusBarItem(this.addStatusBarItem(), () => void this.openPanel());
    this.registerView(VIEW_TYPE, (leaf) => new ArtmindView(leaf, this.panelHandlers()));
    this.addSettingTab(new ArtmindSettingTab(this.app, this));
    this.addCommands();

    this.app.workspace.onLayoutReady(async () => {
      // Registered only now: Obsidian fires `create` for every file while the
      // vault loads, and none of those is an arrival.
      this.registerEvent(this.app.vault.on("create", (file) => watchers.fileCreated(file.path)));
      this.registerDomEvent(window, "focus", () => watchers.focusRegained());
      const problem = await controller.checkArtmind();
      if (!problem) {
        await watchers.start();
        await controller.refresh({ heavy: true });
      }
    });
  }

  override onunload(): void {
    this.watchers?.stop();
  }

  async saveSettings(): Promise<void> {
    await this.saveAll();
    const controller = this.controller;
    if (!controller) return;
    if (!(await controller.checkArtmind())) await controller.refresh();
  }

  /** The settings and the persisted panel state, in one `data.json`. */
  private async saveAll(): Promise<void> {
    await this.saveData(withPersisted(this.artmindSettings, this.persisted));
  }

  /** Obsidian Git's `autoPullInterval` (spec D10). `.obsidian/` is not
   * indexed either: read through the adapter, on every refresh like the
   * manifest. A missing or unreadable file counts as 0 (auto pull off). */
  private async readObsidianGit(): Promise<{ autoPullMinutes: number }> {
    let text: string | null = null;
    try {
      text = await this.app.vault.adapter.read(obsidianGitDataPath(this.app.vault.configDir));
    } catch {
      text = null;
    }
    return { autoPullMinutes: autoPullMinutes(text) };
  }

  /** `.artmind/` is a dot-folder Obsidian does not index: read it through the adapter. */
  private async loadMappings(): Promise<void> {
    try {
      this.mappings = readMappings(await this.app.vault.adapter.read(VAULT_YAML), parseYaml);
    } catch {
      this.mappings = [];
    }
  }

  private render(snapshot: Snapshot): void {
    this.last = snapshot;
    this.statusBar?.render(snapshot.checklist);
    this.ribbon?.render(snapshot.checklist);
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
      if (leaf.view instanceof ArtmindView) leaf.view.update(snapshot);
    }
  }

  /** The side panel, scrolled to `target`: by default the current step. */
  private async openPanel(target: PanelTarget | null = null): Promise<void> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) ?? this.app.workspace.getLeaf(true);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
    if (leaf.view instanceof ArtmindView) {
      if (this.last) leaf.view.update(this.last);
      leaf.view.focus(target ?? this.last?.checklist.current ?? "remote");
    }
  }

  private openResolve(): void {
    const controller = this.controller;
    if (!controller) return;
    new ResolveModal(this.app, {
      preview: () => controller.resolvePreview(),
      apply: () => controller.resolveApply(),
      open: (path) => this.openVaultPath(path),
    }).open();
  }

  private openTableReview(table: string | null): void {
    const controller = this.controller;
    if (!controller) return;
    new TableReviewModal(this.app, table, {
      dryRun: (t) => controller.tableDryRun(t),
      project: (t) => controller.tableProject(t),
      openMapping: (path) => this.openExternal(path),
      askAdminUi: () => void this.openAdmin(),
    }).open();
  }

  private openSynthesize(): void {
    const controller = this.controller;
    if (!controller) return;
    new SynthesizeModal(this.app, {
      plan: () => controller.synthesisPlan(),
      run: (domains, limit) => void controller.synthesize(domains, limit),
    }).open();
  }

  /** A note opens in Obsidian; a file under a dot-folder (hidden from
   * Obsidian) opens in the system's editor. */
  private openVaultPath(path: string): void {
    if (path.split("/").some((part) => part.startsWith("."))) {
      const adapter = this.app.vault.adapter as FileSystemAdapter;
      this.openExternal(adapter.getFullPath(path));
      return;
    }
    void this.app.workspace.openLinkText(path, "", true);
  }

  private openExternal(fullPath: string): void {
    // Desktop only (isDesktopOnly): Electron's shell is always there.
    const { shell } = require("electron") as { shell: { openPath(path: string): Promise<string> } };
    void shell.openPath(fullPath);
  }

  /** Open `path` on this vault's admin console, starting it if need be. */
  private async openAdmin(path = "/"): Promise<void> {
    const admin = this.admin;
    if (!admin) return;
    if (!admin.isStarting) this.notifier.show("prompt", "Opening the admin console…");
    const outcome = await admin.open(path);
    const text = adminOutcomeText(outcome);
    if (text) this.notifier.show(outcome.kind === "opened" || outcome.kind === "busy" ? "prompt" : "error", text);
  }

  /** In an Obsidian tab when the setting asks and the Web viewer is on;
   * the browser otherwise, including when the Web viewer refuses. */
  private async openAdminPage(url: string): Promise<void> {
    const app = this.app as unknown as WebViewerApp;
    if (this.artmindSettings.adminInObsidianTab && webViewerAvailable(app)) {
      if (await openInWebViewer(app, url, hostPort(url).base)) return;
    }
    openInBrowser(url);
  }

  private async stopAdmin(): Promise<void> {
    const outcome = await this.admin?.stop();
    if (outcome) this.notifier.show(outcome.kind === "stopped" || outcome.kind === "not_running" ? "prompt" : "error", stopOutcomeText(outcome));
  }

  /** Today's panel's handlers, mapped onto the new flows; Task 11 replaces them. */
  private panelHandlers() {
    return {
      sync: () => void this.controller?.apply(),
      ingest: () => void this.controller?.ingestWhatChanged(),
      retryJob: (jobId: string) => void this.controller?.retryJob(jobId),
      resolve: () => this.controller?.openResolve(),
      doctor: () => void this.controller?.runDoctor(),
      reviewTable: (table: string) => this.controller?.reviewTables(table),
      openAdmin: (path?: string) => void this.openAdmin(path),
      commitAndSync: () => this.controller?.commitPush(),
      setPath: () => {
        const setting = (this.app as unknown as AppInternals).setting;
        setting?.open();
        setting?.openTabById(this.manifest.id);
      },
      copy: (text: string) => void navigator.clipboard.writeText(text),
    };
  }

  /** One command per row action, plus the panel and the admin console
   * (checklist spec §4). Each runs under the same gates as its button; a
   * blocked one says why in a text-only notice. */
  private addCommands(): void {
    const c = () => this.controller;
    const commands: Array<[string, string, () => void]> = [
      ["pull", COMMANDS.pull, () => void c()?.pull()],
      ["apply-to-graph", COMMANDS.apply, () => void c()?.apply()],
      ["ingest-what-changed", COMMANDS.ingest, () => void c()?.ingestWhatChanged()],
      ["rebuild-graph", COMMANDS.rebuild, () => void c()?.rebuild()],
      ["synthesize", COMMANDS.synthesize, () => c()?.openSynthesize()],
      ["commit-and-push", COMMANDS.commitPush, () => c()?.commitPush()],
      ["review-tables", COMMANDS.reviewTables, () => c()?.reviewTables()],
      ["resolve", COMMANDS.resolve, () => c()?.openResolve()],
      ["doctor", COMMANDS.doctor, () => void c()?.runDoctor()],
      ["open-panel", COMMANDS.openPanel, () => void this.openPanel()],
      ["open-admin-console", COMMANDS.openAdmin, () => void this.openAdmin()],
      ["stop-admin-console", COMMANDS.stopAdmin, () => void this.stopAdmin()],
    ];
    for (const [id, name, callback] of commands) this.addCommand({ id, name, callback });
  }
}
```

- [ ] **Step 12: Let the panel scroll to a row**

In `src/views/panel.ts`, replace `focus` (lines 425–428) with:

```ts
  /** Scroll to a row (`data-row`) or a section (`data-section`), opening a
   * collapsed section first. */
  focus(target: string): void {
    if (this.ui.collapsed.delete(target) && this.last) this.update(this.last);
    this.contentEl.querySelector(`[data-row="${target}"], [data-section="${target}"]`)?.scrollIntoView({ block: "start" });
  }
```

In `test/views.test.ts`, in `function snapshot(...)`, add `checklist: checklist(inputs),` after `inputs,` in the returned object (a `Snapshot` now carries it).

- [ ] **Step 13: The palette test**

In `test/main.test.ts`, replace the `expect(plugin.commands.map(...)).toEqual([...])` list with:

```ts
    expect(plugin.commands.map((c: { name: string }) => c.name)).toEqual([
      "Pull",
      "Apply to graph",
      "Ingest what changed",
      "Rebuild graph",
      "Synthesize…",
      "Commit & push",
      "Review tables for graph",
      "Resolve artmind conflicts",
      "Run doctor",
      "Open side panel",
      "Open admin console",
      "Stop admin console",
    ]);
```

and change the test's title to `"in an artmind vault, adds the status bar, the panel and every command (checklist spec §4)"`.

- [ ] **Step 14: Run everything**

Run: `just obsidian-plugin-test`
Expected: PASS. `test/state.test.ts` still tests `vaultState` (deleted in Task 11); `test/views.test.ts`'s panel tests still pass against today's panel, which reads `snapshot.state`.

- [ ] **Step 15: Commit**

```bash
git add obsidian/artmind-obsidian/src/settings.ts obsidian/artmind-obsidian/src/views/settingsTab.ts obsidian/artmind-obsidian/src/texts.ts obsidian/artmind-obsidian/src/views/notices.ts obsidian/artmind-obsidian/src/controller.ts obsidian/artmind-obsidian/src/main.ts obsidian/artmind-obsidian/src/views/panel.ts obsidian/artmind-obsidian/test/settings.test.ts obsidian/artmind-obsidian/test/texts.test.ts obsidian/artmind-obsidian/test/views.test.ts obsidian/artmind-obsidian/test/controller.test.ts obsidian/artmind-obsidian/test/main.test.ts
git commit -m "feat(obsidian): controller on the checklist -- text-only notices, confirm modals, rebuild, synthesize, commit & push

Notices carry a target row instead of buttons; out-of-order clicks open
a confirm modal; Rebuild graph and Synthesize… run through the write
queue; projection status, db review and the synthesize dry runs are read
on refresh, after writes and on focus at most once a minute; the last
job, apply and rebuild survive a reload. The palette has one command per
row action.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: The side panel is the checklist

`panel.ts` (433 lines) splits into `sections.ts` (the collapsible section and the panel's remembered UI state), `jobCard.ts` (the job card, moved unchanged except that its retry bar goes: row 2's [Retry failed] / [Retry job] replace it), `checklistView.ts` (header, rows, footer) and a shorter `panel.ts` (checklist, tools, callouts, Stores, Activity, the view). `vaultState` and the `Snapshot` shims go.

**Files:**
- Create: `obsidian/artmind-obsidian/src/views/sections.ts`, `src/views/jobCard.ts`, `src/views/checklistView.ts`
- Modify: `obsidian/artmind-obsidian/src/views/panel.ts` (whole file)
- Modify: `obsidian/artmind-obsidian/src/state.ts` (whole file: `vaultState` and its types deleted)
- Modify: `obsidian/artmind-obsidian/src/controller.ts` (`Snapshot` and `snapshot()`; imports)
- Modify: `obsidian/artmind-obsidian/src/main.ts` (`panelHandlers`)
- Test: `test/views.test.ts` (top of file through the side-panel describe), `test/state.test.ts` (whole file), `test/controller.test.ts` (two lines)

- [ ] **Step 1: Write the failing panel tests**

In `test/views.test.ts`, replace everything from the first line down to (not including) the line `describe("the table2graph review (spec §5.1)", () => {` with:

```ts
// @vitest-environment jsdom
// The same module `obsidian` resolves to in tests (vitest.config.ts), imported
// by path for its test-only record of the notices shown.
import { Notice } from "./mocks/obsidian";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { checklist } from "../src/checklist";
import type { Snapshot } from "../src/controller";
import { EMPTY_INPUTS, type StateInputs } from "../src/state";
import { ObsidianNotifier } from "../src/views/notices";
import { ArtmindView, type PanelHandlers, newPanelUi, renderPanel } from "../src/views/panel";
import { ResolveModal, renderResolve, resolveBlockedReason } from "../src/views/resolveModal";
import { StatusBarItem } from "../src/views/statusBar";
import { LOOKUP_PREVIEW, TableReviewModal, renderTableProblem, renderTableReview } from "../src/views/tableReview";
import { fixture } from "./fixtures";

/** Every row of the checklist done: each view test changes one thing. */
function readyInputs(overrides: Partial<StateInputs> = {}): StateInputs {
  return {
    ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    tablesReview: { query_type: "structured", command: "db review", pending_count: 0, tables: [] },
    projection: fixture("projection-status.ready").json,
    synthesis: [],
    git: { ahead: 0, artmindChanges: [] },
    ...overrides,
  };
}

function snapshot(overrides: Partial<StateInputs> = {}, extra: Partial<Snapshot> = {}): Snapshot {
  const inputs = readyInputs(overrides);
  return {
    inputs,
    checklist: checklist(inputs),
    activity: [],
    doctor: null,
    readErrors: [],
    bridgeWarning: null,
    gitMissing: {},
    looked: [],
    ...extra,
  };
}

function handlers(): PanelHandlers & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    refresh: () => void calls.push("refresh"),
    run: (action) => void calls.push(`run:${JSON.stringify(action)}`),
    openAdmin: (path) => void calls.push(path === undefined ? "openAdmin" : `openAdmin:${path}`),
    doctor: () => void calls.push("doctor"),
    setPath: () => void calls.push("setPath"),
    copy: (text) => void calls.push(`copy:${text}`),
  };
}

function text(root: Element): string {
  return root.textContent ?? "";
}

function button(root: Element, label: string): HTMLButtonElement {
  const found = [...root.querySelectorAll("button")].find((b) => b.textContent === label);
  if (!found) throw new Error(`no button "${label}"`);
  return found;
}

function rowEl(root: HTMLElement, id: string): HTMLElement {
  const found = root.querySelector<HTMLElement>(`[data-row="${id}"]`);
  if (!found) throw new Error(`no row ${id}`);
  return found;
}

const RUNNING_JOB = { activeJob: fixture("ingest-job-status.running").json };
const FAILED_LAST_JOB = { lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1 } };

beforeEach(() => {
  Notice.shown.length = 0;
});

describe("StatusBarItem (checklist spec §4)", () => {
  it("shows a dot per row and footer, the current step and its tone; a click only opens the panel", () => {
    const el = document.createElement("div");
    el.classList.add("status-bar-item");
    let opened = 0;
    const item = new StatusBarItem(el, () => opened++);

    item.render(checklist(readyInputs({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json })));

    expect(el.textContent).toBe("artmind ○○●●● 2 docs · 1 table to apply");
    expect(el.classList.contains("artmind-tone-amber")).toBe(true);
    expect(el.classList.contains("status-bar-item")).toBe(true);
    expect(el.getAttribute("aria-label")).toBe("From the other laptop — 2 docs · 1 table to apply");
    el.click();
    expect(opened).toBe(1);

    item.render(checklist(readyInputs()));
    expect(el.textContent).toBe("artmind ●●●●● graph ready");
    expect(el.classList.contains("artmind-tone-amber")).toBe(false);
    expect(el.classList.contains("artmind-tone-grey")).toBe(true);
    expect(el.getAttribute("aria-label")).toBe("Graph ready ✓");
  });

  it("names the footer once it is the only step left", () => {
    const el = document.createElement("div");
    new StatusBarItem(el, () => undefined).render(checklist(readyInputs({ git: { ahead: 2, artmindChanges: [] } })));
    expect(el.textContent).toBe("artmind ●●●●○ ↑ 2 to push");
  });
});

describe("notices (checklist spec §5, D7)", () => {
  it("are text only, fade after 6 s, and a click opens the panel at their row", () => {
    const opened: string[] = [];
    new ObsidianNotifier((target) => opened.push(target)).show("pulled", "Pulled: 2 docs to apply to graph.", "remote");

    const notice = Notice.shown[0];
    expect(notice.duration).toBe(6_000);
    const holder = document.createElement("div");
    holder.appendChild(notice.message as DocumentFragment);
    expect(holder.querySelectorAll("button")).toHaveLength(0);
    expect(text(holder)).toBe("Pulled: 2 docs to apply to graph.");
    (holder.querySelector(".artmind-notice") as HTMLElement).click();
    expect(opened).toEqual(["remote"]);
    expect(notice.hidden).toBe(true);
  });

  it("an error stays 10 s; a notice with no row opens nothing", () => {
    const opened: string[] = [];
    const notifier = new ObsidianNotifier((target) => opened.push(target));
    notifier.show("error", "Couldn't apply to graph — details in the artmind panel.", "remote");
    notifier.show("prompt", "Opening the admin console…");

    expect(Notice.shown.map((n) => n.duration)).toEqual([10_000, 6_000]);
    const holder = document.createElement("div");
    holder.appendChild(Notice.shown[1].message as DocumentFragment);
    (holder.querySelector(".artmind-notice") as HTMLElement).click();
    expect(opened).toEqual([]);
  });
});

describe("the side panel: the readiness checklist (checklist spec §3)", () => {
  it("the header names the vault, says how far the graph is from ready, and ↻ re-reads", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json }), h);

    expect(root.querySelector(".artmind-header-vault")!.textContent).toBe("artmind · vault");
    expect(root.querySelector(".artmind-readiness")!.textContent).toBe("2 steps to a ready graph");
    button(root, "↻").click();
    expect(h.calls).toEqual(["refresh"]);

    renderPanel(root, snapshot(), h);
    expect(root.querySelector(".artmind-readiness")!.textContent).toBe("Graph ready ✓");
  });

  it("lists the five rows, then the footer, each with its glyph, title and summary", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ projection: fixture("projection-status.needs-rebuild").json }), handlers());

    expect([...root.querySelectorAll("[data-row]")].map((r) => r.getAttribute("data-row"))).toEqual([
      "remote",
      "documents",
      "tables",
      "graph",
      "descriptions",
      "vault",
    ]);
    const graph = rowEl(root, "graph");
    expect(graph.querySelector(".artmind-step-line")!.textContent).toBe("!Graphneeds rebuild");
    expect([...graph.querySelectorAll(".artmind-step-detail")].map((d) => d.textContent)).toEqual([
      "same_as edited",
      "2,813 entities not embedded",
      "310 chunks not embedded",
      "40 observation keys not projected",
    ]);
    expect(rowEl(root, "vault").parentElement!.classList.contains("artmind-footer")).toBe(true);
  });

  it("only the current step's button is filled; the others are outline and still work", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json }), h);

    const filled = [...root.querySelectorAll("button.mod-cta")];
    expect(filled.map((b) => b.textContent)).toEqual(["Apply to graph"]);
    expect(rowEl(root, "remote").contains(filled[0])).toBe(true);
    const ingest = button(rowEl(root, "documents"), "Ingest 4 files");
    expect(ingest.classList.contains("artmind-outline")).toBe(true);
    expect(ingest.disabled).toBe(false);
    ingest.click();
    expect(h.calls).toEqual(['run:{"type":"ingest"}']);
  });

  it("a blocked button is disabled, with its reason", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ ...RUNNING_JOB, status: fixture("vault-status.behind").json }), handlers());

    const apply = button(rowEl(root, "remote"), "Apply to graph");
    expect(apply.disabled).toBe(true);
    expect(apply.title).toBe("An ingest job is running — apply after it finishes");
  });

  it("row 2 expands to its paths, and stays open across renders", () => {
    const root = document.createElement("div");
    const ui = newPanelUi();
    const s = snapshot({ pending: fixture("ingest-pending").json });

    renderPanel(root, s, handlers(), ui);

    const items = root.querySelector<HTMLDetailsElement>('[data-items="documents"]')!;
    expect(items.querySelector("summary")!.textContent).toBe("new_idea.md (new) · welcome.md (new) · org_chart.pdf (new) · +1");
    expect([...items.querySelectorAll(".artmind-step-item")].map((i) => i.textContent)).toEqual([
      "Notes/new_idea.md (new)",
      "Notes/welcome.md (new)",
      "Team/org_chart.pdf (new)",
      "Team/team.csv (new)",
    ]);
    expect(items.open).toBe(false);
    items.open = true;
    items.dispatchEvent(new Event("toggle"));
    renderPanel(root, s, handlers(), ui);
    expect(root.querySelector<HTMLDetailsElement>('[data-items="documents"]')!.open).toBe(true);
  });

  it("row 3 deep-links into the admin console's chat", () => {
    const root = document.createElement("div");
    const h = handlers();
    const review = fixture("db-review").json;

    renderPanel(root, snapshot({ tablesReview: review }), h);
    button(rowEl(root, "tables"), "Review in admin console ↗").click();

    const prompt = `Review the proposed classifications for table ${review.tables[0].table} (domain ${review.tables[0].domain})`;
    expect(h.calls).toEqual([`run:${JSON.stringify({ type: "adminPrompt", prompt })}`]);
  });

  it("the footer is hidden during a job, and filled only once rows 1-4 are done", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ ...RUNNING_JOB, git: { ahead: 2, artmindChanges: [] } }), handlers());
    expect(root.querySelector('[data-row="vault"]')).toBeNull();

    renderPanel(root, snapshot({ git: { ahead: 2, artmindChanges: [] } }), handlers());
    expect(button(rowEl(root, "vault"), "Commit & push").classList.contains("mod-cta")).toBe(true);

    renderPanel(root, snapshot({ pending: fixture("ingest-pending").json, git: { ahead: 2, artmindChanges: [] } }), handlers());
    expect(button(rowEl(root, "vault"), "Commit & push").classList.contains("artmind-outline")).toBe(true);
  });

  it("row 2 holds a running job's card: file states, per-file counts scaled to the largest, chunk progress", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot(RUNNING_JOB), handlers());

    const card = rowEl(root, "documents").querySelector('[data-section="job"]')!;
    expect(card.querySelector("summary")!.textContent).toBe("Ingest job2/5 files");
    expect(card.querySelector(".artmind-legend")!.textContent).toBe("2 done · 1 running · 2 queued");
    expect([...card.querySelectorAll(".artmind-segment")].map((s) => [s.className.split("-").pop(), (s as HTMLElement).style.flexGrow]))
      .toEqual([["done", "2"], ["running", "1"], ["queued", "2"]]);
    expect(card.querySelector(".artmind-totals")!.textContent).toBe("34 entities · 20 relationships");

    const rows = [...card.querySelectorAll(".artmind-file")];
    const meters = (row: Element) =>
      [...row.querySelectorAll(".artmind-meter")].map((m) => [
        m.querySelector(".artmind-meter-label")!.textContent,
        (m.querySelector(".artmind-meter-fill") as HTMLElement).style.width,
      ]);
    expect(meters(rows[0])).toEqual([["12 E", "55%"], ["7 R", "54%"]]);
    expect(meters(rows[1])).toEqual([["22 E", "100%"], ["13 R", "100%"]]);
    const chunks = rows[2].querySelector(".artmind-meter-chunks")!;
    expect(chunks.getAttribute("title")).toBe("entities 4/7 · properties 0/7 · relationships 3/7");
    expect(chunks.textContent).toBe("7 chunks");
    expect(rows[3].textContent).toBe("…file4.mdqueued");
  });

  it("keeps the last job up as Last ingest job, each failure with its reason, and Retry failed on row 2", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot(FAILED_LAST_JOB), h);

    const card = root.querySelector('[data-section="job"]')!;
    expect(card.querySelector("summary")!.textContent).toBe("Last ingest job2/2 files");
    expect(card.querySelector(".artmind-badge")!.classList.contains("artmind-tone-red")).toBe(true);
    const rows = [...card.querySelectorAll(".artmind-file")];
    expect(rows[0].textContent).toBe("✓file1.md12 E7 R");
    expect(rows[1].textContent).toBe("✗file2.mdKG ingestion failed");
    expect(card.querySelector(".artmind-retry")).toBeNull();
    button(rowEl(root, "documents"), "Retry failed").click();
    expect(h.calls).toEqual(['run:{"type":"retryJob","jobId":"00000000-0000-4000-8000-000000000001"}']);
  });

  it("marks a stalled job, and row 2 offers Retry job", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ activeJob: fixture("ingest-job-status.stalled").json }), h);

    const card = root.querySelector('[data-section="job"]')!;
    expect(card.querySelector(".artmind-section-title")!.textContent).toBe("Ingest job (stalled)");
    expect(card.querySelector(".artmind-badge")!.classList.contains("artmind-tone-amber")).toBe(true);
    button(rowEl(root, "documents"), "Retry job").click();
    expect(h.calls).toEqual(['run:{"type":"retryJob","jobId":"00000000-0000-4000-8000-000000000001"}']);
  });

  it("opens the admin console from the tools, and its dashboard from the job card", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot(RUNNING_JOB), h);
    button(root, "Admin ↗").click();
    (root.querySelector(".artmind-admin-link") as HTMLAnchorElement).click();

    expect(h.calls).toEqual(["openAdmin:/", "openAdmin:/dashboard"]);
  });

  it("collapses Stores and Activity by default, and remembers a toggle across renders", () => {
    const root = document.createElement("div");
    const ui = newPanelUi();
    const open = (id: string) => (root.querySelector(`[data-section="${id}"]`) as HTMLDetailsElement).open;

    renderPanel(root, snapshot(), handlers(), ui);
    expect([open("status"), open("activity")]).toEqual([false, false]);
    expect(root.querySelector('[data-section="status"] .artmind-section-title')!.textContent).toBe("Stores");

    const status = root.querySelector('[data-section="status"]') as HTMLDetailsElement;
    status.open = true;
    status.dispatchEvent(new Event("toggle"));
    renderPanel(root, snapshot(), handlers(), ui);
    expect(open("status")).toBe(true);
  });

  it("shows Neo4j unreachable and failed reads in the problems callout", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ status: fixture("vault-status.neo4j-unreachable").json }, { readErrors: ["artmind db review --compact: boom"] }), handlers());

    const problems = root.querySelector('[data-section="problems"]')!;
    expect(text(problems)).toContain("Neo4j unreachable (neo4j://127.0.0.1:7687)");
    expect(text(problems)).toContain("artmind db review --compact: boom");
  });

  it("shows each doctor finding with its fix and a Copy button, and never runs it", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({}, { doctor: fixture("vault-doctor").json }), h);

    expect(text(root)).toContain("FAIL pull.rebase");
    const copies = [...root.querySelectorAll("button")].filter((b) => b.textContent === "Copy");
    copies[0].click();
    expect(h.calls).toEqual(["copy:git config pull.rebase false"]);
  });

  it("explains a missing artmind and offers Set path", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ problem: { kind: "missing", looked: ["/Users/me/.local/bin/artmind"] } }), h);

    expect(text(root)).toContain("Looked in: /Users/me/.local/bin/artmind");
    button(root, "Set path").click();
    expect(h.calls).toEqual(["setPath"]);
  });

  it("shows the bootstrap step in row 1 when this laptop never applied the vault", () => {
    const root = document.createElement("div");
    renderPanel(root, snapshot({ status: fixture("vault-status.no-bookmark").json }), handlers());
    expect(text(rowEl(root, "remote"))).toContain("artmind vault sync --bootstrapEmpty");
  });

  it("lists the activity, each run expanding to its raw output", () => {
    const root = document.createElement("div");
    const activity = [{ command: "artmind vault sync --compact", at: 0, ok: false, summary: "failed: boom", output: "raw stderr" }];

    renderPanel(root, snapshot({}, { activity }), handlers());

    const details = root.querySelector("details.artmind-run-failed")!;
    expect(details.querySelector("summary")!.textContent).toContain("artmind vault sync --compact — failed: boom");
    expect(details.querySelector("pre")!.textContent).toBe("raw stderr");
  });

  it("focusing a row expands its list", () => {
    const view = new ArtmindView({} as never, handlers());
    view.update(snapshot({ pending: fixture("ingest-pending").json }));

    view.focus("documents");

    expect(view.contentEl.querySelector<HTMLDetailsElement>('[data-items="documents"]')!.open).toBe(true);
  });
});

(The `describe("the table2graph review (spec §5.1)", ...)` and `describe("the resolve preview (spec §5.2)", ...)` blocks below it stay as they are.)

Replace `test/state.test.ts` with (the `vaultState` tests go with `vaultState`; `checklist.test.ts` covers the rows):

```ts
import { describe, expect, it } from "vitest";
import { errorText } from "../src/cli";
import { EMPTY_INPUTS, classifyRefusal } from "../src/state";
import { fixture } from "./fixtures";

describe("classifyRefusal: every vault sync refusal becomes an action (spec §4.2)", () => {
  it.each([
    ["vault-sync.refusal-worker-running", "worker_running"],
    ["vault-sync.refusal-merge-in-progress", "resolve_first"],
    ["vault-sync.refusal-artmind-conflicts", "resolve_first"],
    ["vault-sync.refusal-bookmark-unpulled", "pull_first"],
    ["vault-sync.refusal-bookmark-not-ancestor", "pull_first"],
    ["vault-sync.refusal-no-bookmark", "no_bookmark"],
  ])("%s -> %s", (name, kind) => {
    const f = fixture(name);
    expect(f.exit_code).toBe(1);
    expect(classifyRefusal(errorText(f.stderr!)!).kind).toBe(kind);
  });

  it("anything else stays a plain failure", () => {
    expect(classifyRefusal("git diff failed: fatal")).toEqual({ kind: "other", text: "git diff failed: fatal" });
  });
});

describe("StateInputs", () => {
  it("starts empty: nothing read, nothing running, nothing remembered", () => {
    expect(EMPTY_INPUTS).toEqual({
      status: null,
      activeJob: null,
      pending: null,
      tablesPending: null,
      git: { ahead: null, artmindChanges: [] },
      problem: null,
      projection: null,
      tablesReview: null,
      synthesis: null,
      lastJob: null,
      lastApply: null,
      lastRebuild: null,
      busy: { apply: false, rebuild: false, synthesize: false },
      actionErrors: {},
      obsidianGit: null,
    });
  });
});
```

In `test/controller.test.ts`, run:

```bash
cd obsidian/artmind-obsidian && perl -pi -e 's/t\.controller\.snapshot\(\)\.jobResults\?\./t.controller.inputs.lastJob?.results?./g' test/controller.test.ts
grep -n "snapshot().jobResults" test/controller.test.ts
```

Expected: `grep` prints nothing.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/views.test.ts test/state.test.ts`
Expected: FAIL: the panel tests find no `[data-row]` (`no row graph`), no `.artmind-readiness`, and `PanelHandlers` has no `run`; `state.test.ts` passes already.

- [ ] **Step 3: `src/views/sections.ts`**

```ts
import { el } from "./dom";

/** What the panel remembers between renders: which sections are collapsed
 * and which rows' lists are open. Every snapshot re-renders the panel, so
 * `<details>` alone would forget. */
export interface PanelUi {
  collapsed: Set<string>;
  expanded: Set<string>;
}

/** Collapsed until opened: the detail most visits don't need. */
export const DEFAULT_COLLAPSED = ["status", "activity"];

export function newPanelUi(): PanelUi {
  return { collapsed: new Set(DEFAULT_COLLAPSED), expanded: new Set() };
}

/** A collapsible section whose header carries a one-line summary (a count,
 * a badge), so the panel reads even with everything collapsed. */
export function section(root: HTMLElement, ui: PanelUi, id: string, title: string, badge = "", badgeTone = "grey"): HTMLElement {
  const box = el(root, "details", { cls: `artmind-section artmind-section-${id}`, attr: { "data-section": id } });
  box.open = !ui.collapsed.has(id);
  const summary = el(box, "summary");
  el(summary, "span", { cls: "artmind-section-title", text: title });
  if (badge) el(summary, "span", { cls: `artmind-badge artmind-tone-${badgeTone}`, text: badge });
  box.addEventListener("toggle", () => {
    if (box.open) ui.collapsed.delete(id);
    else ui.collapsed.add(id);
  });
  return el(box, "div", { cls: "artmind-section-body" });
}

export function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}
```

- [ ] **Step 4: `src/views/jobCard.ts`**

Moved from `panel.ts` (lines 178–303) without its retry bar:

```ts
import { plural } from "../state";
import type { ChunkProgress, KgCounts } from "../types";
import { el } from "./dom";
import { type PanelUi, basename, section } from "./sections";

type CardFile = KgCounts & {
  filename: string;
  status: string;
  current_step?: string | null;
  error_message?: string | null;
  chunk_progress?: ChunkProgress;
};

export interface JobCardJob {
  job_id: string;
  status: string;
  file_count: number;
  files: CardFile[];
  stalled?: boolean;
}

/** Order and labels of the file states in the bar and its legend. */
const FILE_STATES: Array<{ key: string; label: string; match: (status: string) => boolean }> = [
  { key: "done", label: "done", match: (s) => s === "completed" },
  { key: "running", label: "running", match: (s) => s === "processing" },
  { key: "queued", label: "queued", match: (s) => s === "queued" },
  { key: "skipped", label: "skipped", match: (s) => s === "skipped" },
  { key: "failed", label: "failed", match: (s) => s === "failed" },
];

function fileState(status: string): string {
  return FILE_STATES.find((s) => s.match(status))?.key ?? "queued";
}

const GLYPH: Record<string, string> = { done: "✓", running: "◌", queued: "…", skipped: "–", failed: "✗" };

function meter(parent: HTMLElement, cls: string, fraction: number, label: string, title: string): void {
  const box = el(parent, "span", { cls: `artmind-meter ${cls}`, attr: { title } });
  const track = el(box, "span", { cls: "artmind-meter-track" });
  const fill = el(track, "span", { cls: "artmind-meter-fill" });
  fill.style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;
  el(box, "span", { cls: "artmind-meter-label", text: label });
}

function chunkFraction(p: ChunkProgress): number {
  if (!p.total_chunks) return 0;
  return (p.entities_done + p.properties_done + p.relationships_done) / (3 * p.total_chunks);
}

/** One job, live or finished: a segmented bar of file states, the job's
 * totals, and a row per file with its entity and relationship counts (bars
 * scaled to the job's largest file) or, while extracting, its chunk
 * progress. Retrying is row 2's button, not the card's. */
export function jobCard(root: HTMLElement, ui: PanelUi, job: JobCardJob, live: boolean, handlers: { openAdmin(path?: string): void }): void {
  const files = job.files;
  const counts = new Map(FILE_STATES.map((s) => [s.key, 0]));
  for (const f of files) counts.set(fileState(f.status), (counts.get(fileState(f.status)) ?? 0) + 1);
  const finished = (counts.get("done") ?? 0) + (counts.get("failed") ?? 0) + (counts.get("skipped") ?? 0);
  const failed = counts.get("failed") ?? 0;
  const stalled = live && Boolean(job.stalled);
  const title = stalled ? "Ingest job (stalled)" : live ? "Ingest job" : "Last ingest job";
  const tone = failed ? "red" : stalled ? "amber" : live ? "spinner" : "grey";
  const box = section(root, ui, "job", title, `${finished}/${plural(job.file_count, "file")}`, tone);

  const bar = el(box, "div", { cls: "artmind-segments", attr: { title: `Job ${job.job_id}: ${job.status}` } });
  for (const s of FILE_STATES) {
    const n = counts.get(s.key) ?? 0;
    if (!n) continue;
    const seg = el(bar, "span", { cls: `artmind-segment artmind-segment-${s.key}` });
    seg.style.flexGrow = String(n);
  }
  el(box, "div", {
    cls: "artmind-legend",
    text: FILE_STATES.filter((s) => counts.get(s.key)).map((s) => `${counts.get(s.key)} ${s.label}`).join(" · "),
  });

  const counted = files.filter((f) => typeof f.entities === "number");
  const maxE = Math.max(1, ...counted.map((f) => f.entities ?? 0));
  const maxR = Math.max(1, ...counted.map((f) => f.relationships ?? 0));
  if (counted.length) {
    const e = counted.reduce((sum, f) => sum + (f.entities ?? 0), 0);
    const r = counted.reduce((sum, f) => sum + (f.relationships ?? 0), 0);
    el(box, "div", { cls: "artmind-totals", text: `${plural(e, "entity", "entities")} · ${plural(r, "relationship")}` });
  }

  const list = el(box, "div", { cls: "artmind-files" });
  const drill = el(box, "a", { cls: "artmind-admin-link", text: "Chunks and artifacts in the admin console ↗", attr: { href: "#" } });
  drill.addEventListener("click", (event) => {
    event.preventDefault();
    handlers.openAdmin("/dashboard");
  });
  for (const f of files) {
    const key = fileState(f.status);
    const line = el(list, "div", { cls: `artmind-file artmind-file-${key}` });
    el(line, "span", { cls: "artmind-file-glyph", text: GLYPH[key] });
    el(line, "span", { cls: "artmind-file-name", text: basename(f.filename), attr: { title: f.filename } });
    const detail = el(line, "span", { cls: "artmind-file-detail" });
    if (key === "failed") {
      el(detail, "span", { cls: "artmind-error", text: f.error_message ?? "failed" });
    } else if (typeof f.entities === "number") {
      const e = f.entities;
      const r = f.relationships ?? 0;
      meter(detail, "artmind-meter-entities", e / maxE, `${e} E`, plural(e, "entity", "entities"));
      meter(detail, "artmind-meter-relationships", r / maxR, `${r} R`, plural(r, "relationship"));
    } else if (f.chunk_progress) {
      const p = f.chunk_progress;
      meter(
        detail,
        "artmind-meter-chunks",
        chunkFraction(p),
        `${p.total_chunks} chunks`,
        `entities ${p.entities_done}/${p.total_chunks} · properties ${p.properties_done}/${p.total_chunks} · relationships ${p.relationships_done}/${p.total_chunks}`,
      );
    } else {
      el(detail, "span", { cls: "artmind-muted", text: key === "done" ? "no new extraction" : (f.current_step ?? key).replace(/_/g, " ") });
    }
  }
}
```

- [ ] **Step 5: `src/views/checklistView.ts`**

```ts
import type { Row, RowAction, RowItem } from "../checklist";
import type { Snapshot } from "../controller";
import { LABELS, readinessText } from "../texts";
import { actionButton, el } from "./dom";
import { jobCard } from "./jobCard";
import { type PanelUi, basename } from "./sections";

export interface ChecklistHandlers {
  /** ↻: every read again, heavy ones included. */
  refresh(): void;
  run(action: RowAction): void;
  openAdmin(path?: string): void;
}

/** Items shown in a collapsed row's preview line. */
export const ITEM_PREVIEW = 3;

function itemText(item: RowItem, short: boolean): string {
  const name = short ? basename(item.text) : item.text;
  return item.tag ? `${name} (${item.tag})` : name;
}

/** `2024-12-31.md (changed) · Ideas.md (new) · +5`, opening to every item. */
function items(box: HTMLElement, row: Row, ui: PanelUi): void {
  const details = el(box, "details", { cls: "artmind-step-items", attr: { "data-items": row.id } });
  details.open = ui.expanded.has(row.id);
  details.addEventListener("toggle", () => {
    if (details.open) ui.expanded.add(row.id);
    else ui.expanded.delete(row.id);
  });
  const preview = row.items.slice(0, ITEM_PREVIEW).map((item) => itemText(item, true)).join(" · ");
  const more = row.items.length - ITEM_PREVIEW;
  el(details, "summary", { text: more > 0 ? `${preview} · +${more}` : preview });
  const list = el(details, "div", { cls: "artmind-step-item-list" });
  for (const item of row.items) {
    const line = el(list, "div", { cls: "artmind-step-item", text: itemText(item, false) });
    if (item.title) line.title = item.title;
  }
}

/** One row: glyph, title, summary, its buttons (the first filled on the
 * current row only, D3), its detail lines, its expandable items; row 2
 * also holds the job card. */
function renderRow(parent: HTMLElement, row: Row, current: boolean, snapshot: Snapshot, handlers: ChecklistHandlers, ui: PanelUi): void {
  const box = el(parent, "div", {
    cls: `artmind-step artmind-step-${row.state}${current ? " artmind-step-current" : ""}`,
    attr: { "data-row": row.id },
  });
  const line = el(box, "div", { cls: "artmind-step-line" });
  el(line, "span", { cls: `artmind-step-glyph artmind-tone-${row.tone}`, text: row.glyph });
  el(line, "span", { cls: "artmind-step-title", text: row.title });
  el(line, "span", { cls: "artmind-step-summary", text: row.summary });
  if (row.buttons.length) {
    const bar = el(box, "div", { cls: "artmind-step-buttons" });
    row.buttons.forEach((b, index) => {
      const button = actionButton(bar, b.label, b.blockedReason, () => handlers.run(b.action));
      button.classList.add(current && index === 0 ? "mod-cta" : "artmind-outline");
    });
  }
  for (const text of row.detail) el(box, "div", { cls: "artmind-step-detail", text });
  if (row.items.length) items(box, row, ui);
  if (row.id === "documents") {
    const active = snapshot.inputs.activeJob;
    const last = snapshot.inputs.lastJob;
    if (active) jobCard(box, ui, active, true, handlers);
    else if (last) jobCard(box, ui, last.results ?? last.status, false, handlers);
  }
}

/** The readiness checklist (checklist spec §3.1): header with ↻, rows 1–5,
 * then the Vault footer (hidden during a job). */
export function renderChecklist(root: HTMLElement, snapshot: Snapshot, handlers: ChecklistHandlers, ui: PanelUi): void {
  const cl = snapshot.checklist;
  const head = el(root, "div", { cls: "artmind-header" });
  const top = el(head, "div", { cls: "artmind-header-top" });
  const vault = snapshot.inputs.status?.vault;
  el(top, "span", { cls: "artmind-header-vault", text: vault ? `artmind · ${basename(vault)}` : "artmind" });
  const refresh = el(top, "button", { cls: "artmind-refresh clickable-icon", text: LABELS.refresh, attr: { "aria-label": "Re-read everything" } });
  refresh.addEventListener("click", () => handlers.refresh());
  const current = cl.rows.find((r) => r.id === cl.current);
  el(head, "div", { cls: `artmind-readiness artmind-tone-${cl.ready ? "grey" : (current?.tone ?? "grey")}`, text: readinessText(cl) });
  if (snapshot.bridgeWarning) el(head, "p", { cls: "artmind-warning", text: snapshot.bridgeWarning });

  const list = el(root, "div", { cls: "artmind-checklist" });
  for (const row of cl.rows) {
    if (row.id !== "vault") renderRow(list, row, row.id === cl.current, snapshot, handlers, ui);
  }
  const footer = cl.rows.find((r) => r.id === "vault");
  if (footer && footer.state !== "hidden") renderRow(el(root, "div", { cls: "artmind-footer" }), footer, cl.current === "vault", snapshot, handlers, ui);
}
```

- [ ] **Step 6: `src/views/panel.ts`**

Replace the file with:

```ts
import { ItemView, type WorkspaceLeaf } from "obsidian";
import type { Snapshot } from "../controller";
import { type PanelTarget, ROW_IDS, behindParts, problemText } from "../state";
import { LABELS } from "../texts";
import type { StoreReport } from "../types";
import { type ChecklistHandlers, renderChecklist } from "./checklistView";
import { actionButton, el } from "./dom";
import { type PanelUi, newPanelUi, section } from "./sections";

export { newPanelUi } from "./sections";

export const VIEW_TYPE = "artmind-view";

export interface PanelHandlers extends ChecklistHandlers {
  doctor(): void;
  setPath(): void;
  copy(text: string): void;
}

function short(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : "none";
}

function callout(root: HTMLElement, id: string, tone: "red" | "amber"): HTMLElement {
  return el(root, "div", { cls: `artmind-callout artmind-callout-${tone}`, attr: { "data-section": id } });
}

function row(parent: HTMLElement, label: string, value: string): void {
  const line = el(parent, "div", { cls: "artmind-row" });
  el(line, "span", { cls: "artmind-row-label", text: label });
  el(line, "span", { cls: "artmind-row-value", text: value });
}

/** Doctor and the admin console: not steps, so not rows. */
function tools(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers): void {
  const bar = el(root, "div", { cls: "artmind-toolbar" });
  actionButton(bar, LABELS.doctor, snapshot.checklist.blocked.doctor, () => handlers.doctor()).classList.add("artmind-tool");
  const admin = actionButton(bar, LABELS.admin, snapshot.checklist.blocked.admin, () => handlers.openAdmin("/"));
  admin.classList.add("artmind-tool");
  admin.title ||= "Open the admin console (starts it if need be)";
}

function problems(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers): void {
  const { inputs, checklist: cl } = snapshot;
  const problem = inputs.problem;
  const errors: string[] = [];
  if (problem) errors.push(problemText(problem));
  const graphError = inputs.status?.sync.graph_error ?? null;
  if (cl.neo4jUnreachable) errors.push(`Neo4j unreachable (${inputs.status?.graph.uri || "no URI configured"})`);
  else if (graphError) errors.push(graphError);
  errors.push(...snapshot.readErrors);
  if (!errors.length) return;
  const box = callout(root, "problems", "red");
  for (const error of errors) el(box, "p", { cls: "artmind-error", text: error });
  if (problem?.kind === "missing") {
    el(box, "p", { text: `Looked in: ${problem.looked.join(", ") || "nowhere"}. Install artmind (uv tool install), or set its path.` });
  }
  if (problem?.kind === "too_old") {
    el(box, "p", { text: `Upgrade artmind to ${problem.need} or newer (just dev-install in the artmind checkout).` });
  }
  if (problem && problem.kind !== "failed") el(box, "button", { text: "Set path" }).addEventListener("click", () => handlers.setPath());
}

function storeBadge(name: string, report: StoreReport | undefined): string {
  if (!report) return `${name} ?`;
  return `${name} ${report.state === "current" ? "✓" : "⚠"}`;
}

/** The bookmarks behind row 1, collapsed by default. */
function stores(root: HTMLElement, ui: PanelUi, snapshot: Snapshot): void {
  const { inputs } = snapshot;
  const sync = inputs.status?.sync;
  const stores = sync?.stores ?? {};
  const behind = [stores.graph, stores.structured].some((s) => s && s.state !== "current");
  const box = section(root, ui, "status", "Stores", `${storeBadge("graph", stores.graph)} · ${storeBadge("structured", stores.structured)}`, behind ? "amber" : "grey");
  row(box, "HEAD", short(sync?.head));
  row(box, "Graph bookmark", stores.graph ? `${short(stores.graph.bookmark)} (${stores.graph.state})` : "unknown");
  row(box, "Structured bookmark", stores.structured ? `${short(stores.structured.bookmark)} (${stores.structured.state})` : "unknown");
  row(box, "To apply", behindParts(stores).join(" · ") || "nothing");
  row(box, "Commits to push", inputs.git.ahead === null ? "no upstream" : String(inputs.git.ahead));
  const last = inputs.lastApply;
  row(box, "Last apply", last ? `${new Date(last.at).toLocaleString()} — ${last.ok ? last.summary : `failed: ${last.summary}`}` : "never");
}

function doctor(root: HTMLElement, ui: PanelUi, snapshot: Snapshot, handlers: PanelHandlers): void {
  const report = snapshot.doctor;
  if (!report) return;
  const findings = report.checks.filter((c) => c.status !== "ok");
  const failing = findings.some((c) => c.status === "fail");
  const box = section(root, ui, "doctor", "Doctor", findings.length ? `${findings.length} finding${findings.length === 1 ? "" : "s"}` : "all ok", failing ? "red" : findings.length ? "amber" : "grey");
  for (const check of findings) {
    const finding = el(box, "div", { cls: `artmind-finding artmind-finding-${check.status}` });
    el(finding, "p", { text: `${check.status.toUpperCase()} ${check.name}: ${check.detail}` });
    if (check.fix) {
      const fix = check.fix;
      el(finding, "code", { text: fix });
      el(finding, "button", { text: "Copy" }).addEventListener("click", () => handlers.copy(fix));
    }
  }
  if (!findings.length) el(box, "p", { text: "Every check passed." });
}

function activity(root: HTMLElement, ui: PanelUi, snapshot: Snapshot): void {
  const failed = snapshot.activity.filter((e) => !e.ok).length;
  const badge = snapshot.activity.length ? `${snapshot.activity.length}${failed ? ` · ${failed} failed` : ""}` : "";
  const box = section(root, ui, "activity", "Activity", badge, failed ? "red" : "grey");
  if (!snapshot.activity.length) el(box, "p", { cls: "artmind-muted", text: "No artmind runs yet." });
  for (const entry of snapshot.activity) {
    const details = el(box, "details", { cls: entry.ok ? "artmind-run" : "artmind-run artmind-run-failed" });
    el(details, "summary", { text: `${new Date(entry.at).toLocaleTimeString()} ${entry.command} — ${entry.summary}` });
    el(details, "pre", { text: entry.output || "(no output)" });
  }
}

/** The side panel (checklist spec §3): the readiness checklist, then the
 * tools, the callouts (problems, doctor), the Stores detail and Activity. */
export function renderPanel(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers, ui: PanelUi = newPanelUi()): void {
  root.replaceChildren();
  root.classList.add("artmind-panel");
  renderChecklist(root, snapshot, handlers, ui);
  tools(root, snapshot, handlers);
  problems(root, snapshot, handlers);
  doctor(root, ui, snapshot, handlers);
  stores(root, ui, snapshot);
  activity(root, ui, snapshot);
}

/** The right-sidebar view. It renders snapshots; it decides nothing. */
export class ArtmindView extends ItemView {
  private handlers: PanelHandlers;
  private last: Snapshot | null = null;
  private ui: PanelUi = newPanelUi();

  constructor(leaf: WorkspaceLeaf, handlers: PanelHandlers) {
    super(leaf);
    this.handlers = handlers;
  }

  getViewType(): string {
    return VIEW_TYPE;
  }

  getDisplayText(): string {
    return "artmind";
  }

  override getIcon(): string {
    return "brain-circuit";
  }

  update(snapshot: Snapshot): void {
    this.last = snapshot;
    renderPanel(this.contentEl, snapshot, this.handlers, this.ui);
  }

  /** Scroll to a row, its list opened, or to a callout or section. */
  focus(target: PanelTarget): void {
    const isRow = (ROW_IDS as readonly string[]).includes(target);
    let changed: boolean;
    if (isRow) {
      changed = !this.ui.expanded.has(target);
      this.ui.expanded.add(target);
    } else {
      changed = this.ui.collapsed.delete(target);
    }
    if (changed && this.last) this.update(this.last);
    const selector = isRow ? `[data-row="${target}"]` : `[data-section="${target}"]`;
    this.contentEl.querySelector(selector)?.scrollIntoView?.({ block: "start" });
  }

  override async onOpen(): Promise<void> {
    if (this.last) this.update(this.last);
  }
}
```

- [ ] **Step 7: `vaultState` goes**

Replace `src/state.ts` with (everything `vaultState` used and nothing else used is deleted: `StateKind`, `PanelSection`, `Action`, `StateItem`, `ActionName`, `VaultStateResult`, `PRIORITY`, `IN_SYNC`, `vaultState`):

```ts
import type {
  DbReview,
  IngestPending,
  JobResults,
  JobStatus,
  ProjectionStatus,
  StoreReport,
  TablePending,
  VaultStatus,
} from "./types";

/** Why artmind itself cannot be used (spec §8). */
export type ArtmindProblem =
  | { kind: "missing"; looked: string[] }
  | { kind: "too_old"; found: string; need: string }
  | { kind: "failed"; command: string; message: string };

/** The checklist's rows (checklist spec §3.2), top to bottom; `vault` is the footer. */
export const ROW_IDS = ["remote", "documents", "tables", "graph", "descriptions", "vault"] as const;
export type RowId = (typeof ROW_IDS)[number];

/** Where a notice or the status bar opens the panel: a row, or a callout. */
export type PanelTarget = RowId | "problems" | "doctor" | "activity";

export type Tone = "red" | "amber" | "blue" | "grey" | "spinner";

/** The last ingest job that finished, saved with `saveData` so its card
 * and its failures survive a reload. `results` is null when
 * `job-results` failed. */
export interface LastJob {
  status: JobStatus;
  results: JobResults | null;
  at: number;
}

/** How a write action ended. `summary` is the notice text on success, the
 * raw error on failure (shown only in its row's detail). */
export interface Outcome {
  at: number;
  ok: boolean;
  summary: string;
}

/** A rebuild's outcome, with the embed sweeps that could not run. */
export interface RebuildOutcome extends Outcome {
  errors: string[];
}

/** One mapped domain's `projection synthesize --dry-run`. */
export interface DomainSynthesis {
  domain: string;
  count: number;
  model: string;
}

/** A plugin write still running: its row shows ◌, its button is blocked. */
export interface Busy {
  apply: boolean;
  rebuild: boolean;
  synthesize: boolean;
}

/** Everything `checklist` decides from. All of it is read, never written. */
export interface StateInputs {
  /** `vault status --compact`, or null before the first read. */
  status: VaultStatus | null;
  /** The ingest job being tracked, while it is queued, processing or finalizing. */
  activeJob: JobStatus | null;
  /** `ingest pending --compact`. */
  pending: IngestPending | null;
  /** `ingest table2graph --pending --compact`. */
  tablesPending: TablePending[] | null;
  /** Read-only git facts: commits not pushed (null: no upstream) and the
   * uncommitted paths under `.artmind/`. */
  git: { ahead: number | null; artmindChanges: string[] };
  problem: ArtmindProblem | null;
  /** `projection status --compact`. */
  projection: ProjectionStatus | null;
  /** `db review --compact`. */
  tablesReview: DbReview | null;
  /** Per mapped domain, or null when not counted yet. */
  synthesis: DomainSynthesis[] | null;
  lastJob: LastJob | null;
  lastApply: Outcome | null;
  lastRebuild: RebuildOutcome | null;
  busy: Busy;
  /** The raw error of each row's last failed action: its row's detail, never a notice. */
  actionErrors: Partial<Record<RowId, string>>;
  /** Obsidian Git's `autoPullInterval` (0: off), or null before the first read (D10). */
  obsidianGit: { autoPullMinutes: number } | null;
}

export const NOT_BUSY: Busy = { apply: false, rebuild: false, synthesize: false };

export const EMPTY_INPUTS: StateInputs = {
  status: null,
  activeJob: null,
  pending: null,
  tablesPending: null,
  git: { ahead: null, artmindChanges: [] },
  problem: null,
  projection: null,
  tablesReview: null,
  synthesis: null,
  lastJob: null,
  lastApply: null,
  lastRebuild: null,
  busy: NOT_BUSY,
  actionErrors: {},
  obsidianGit: null,
};

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** `3 docs · 1 table · 2 curation records`: what the stores still have to
 * apply. Tables either store still has to apply count once. */
export function behindParts(stores: Partial<Record<"graph" | "structured", StoreReport>>): string[] {
  const graph = stores.graph;
  const structured = stores.structured;
  const tables = new Set<string>();
  for (const report of [graph, structured]) {
    for (const [domain, table] of report?.tables ?? []) tables.add(`${domain}/${table}`);
  }
  const parts: string[] = [];
  if (graph?.docs) parts.push(plural(graph.docs, "doc"));
  if (tables.size) parts.push(plural(tables.size, "table"));
  if (graph?.curation) parts.push(plural(graph.curation, "curation record"));
  if (graph?.same_as_groups) parts.push(plural(graph.same_as_groups, "same-as group"));
  return parts;
}

export function problemText(problem: ArtmindProblem): string {
  switch (problem.kind) {
    case "missing":
      return `artmind not found (looked in ${problem.looked.join(", ") || "nowhere"})`;
    case "too_old":
      return `artmind ${problem.found} is too old: this plugin needs ${problem.need} or newer`;
    case "failed":
      return `\`artmind ${problem.command}\` failed: ${problem.message}`;
  }
}

/** What a `vault sync` refusal becomes (spec §4.2): never a raw error. The
 * controller words each kind's notice (texts.ts `NOTICE`); `text` is the
 * message itself for `other`. */
export type Refusal =
  | { kind: "worker_running"; text: string }
  | { kind: "resolve_first"; text: string }
  | { kind: "pull_first"; text: string }
  | { kind: "no_bookmark"; text: string }
  | { kind: "other"; text: string };

export const BOOTSTRAP_STEP =
  "Run once in a terminal, in the vault: artmind vault sync --bootstrapEmpty " +
  "(an empty Neo4j and DuckDB) — see docs/USER_GUIDE_TWO_LAPTOPS.md §2.3.";

/** Classify `vault sync`'s error message (`errorText` of its stderr). */
export function classifyRefusal(message: string): Refusal {
  const text = message.replace(/\s+/g, " ");
  if (/ingest worker is running/.test(text)) return { kind: "worker_running", text };
  if (/is in progress in this vault|unresolved conflicts in/.test(text)) return { kind: "resolve_first", text };
  if (/is not a commit in this clone|is not an ancestor of HEAD/.test(text)) return { kind: "pull_first", text };
  if (/bookmark recorded yet/.test(text)) return { kind: "no_bookmark", text };
  return { kind: "other", text: message };
}
```

- [ ] **Step 8: The `Snapshot` shims go**

In `src/controller.ts`:
- Remove `type Outcome,`, `type VaultStateResult,` and `vaultState,` from the import list from `./state`.
- In `interface Snapshot`, delete the three lines from `/** Today's panel reads these three; Task 11 removes them. */` through `jobResults: JobResults | null;`.
- In `snapshot()`, delete the three lines `state: vaultState(this.inputs),`, `lastSync: this.inputs.lastApply,` and `jobResults: this.inputs.lastJob?.results ?? null,`.

In `src/main.ts`, add `type PanelHandlers,` to the import from `./views/panel` (`import { ArtmindView, type PanelHandlers, VIEW_TYPE } from "./views/panel";`) and replace `panelHandlers()` with:

```ts
  private panelHandlers(): PanelHandlers {
    return {
      refresh: () => void this.controller?.refresh({ heavy: true }),
      run: (action) => this.controller?.run(action),
      openAdmin: (path?: string) => void this.openAdmin(path),
      doctor: () => void this.controller?.runDoctor(),
      setPath: () => {
        const setting = (this.app as unknown as AppInternals).setting;
        setting?.open();
        setting?.openTabById(this.manifest.id);
      },
      copy: (text: string) => void navigator.clipboard.writeText(text),
    };
  }
```

- [ ] **Step 9: Run everything**

Run: `just obsidian-plugin-test`
Expected: PASS, `tsc` included. If `tsc` reports an unused import in `controller.ts`, it is `JobResults` only if you deleted its last use: it is still used by `pollJob` (`results.json as JobResults`), so keep it.

- [ ] **Step 10: Commit**

```bash
git add obsidian/artmind-obsidian/src/views/sections.ts obsidian/artmind-obsidian/src/views/jobCard.ts obsidian/artmind-obsidian/src/views/checklistView.ts obsidian/artmind-obsidian/src/views/panel.ts obsidian/artmind-obsidian/src/state.ts obsidian/artmind-obsidian/src/controller.ts obsidian/artmind-obsidian/src/main.ts obsidian/artmind-obsidian/test/views.test.ts obsidian/artmind-obsidian/test/state.test.ts obsidian/artmind-obsidian/test/controller.test.ts
git commit -m "feat(obsidian): the side panel is the readiness checklist; vaultState goes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Styles for the checklist

**Files:**
- Modify: `obsidian/artmind-obsidian/styles.css`
- Test: `obsidian/artmind-obsidian/test/styles.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `test/styles.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(import.meta.dirname, "..", "styles.css"), "utf8");

describe("styles.css", () => {
  it("styles every class the checklist views add", () => {
    const classes = [
      "artmind-header-top",
      "artmind-header-vault",
      "artmind-refresh",
      "artmind-readiness",
      "artmind-checklist",
      "artmind-step",
      "artmind-step-current",
      "artmind-step-line",
      "artmind-step-glyph",
      "artmind-step-title",
      "artmind-step-summary",
      "artmind-step-buttons",
      "artmind-step-detail",
      "artmind-step-items",
      "artmind-step-item",
      "artmind-outline",
      "artmind-footer",
      "artmind-notice-link",
      "artmind-confirm-text",
      "artmind-synth-domain",
      "artmind-synth-cap",
      "artmind-synth-total",
    ];
    expect(classes.filter((cls) => !CSS.includes(`.${cls}`))).toEqual([]);
  });

  it("drops the styles of the views that went", () => {
    for (const gone of ["artmind-health", "artmind-primary", "artmind-hint", "artmind-notice-buttons", ".artmind-state "]) {
      expect(CSS.includes(gone)).toBe(false);
    }
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd obsidian/artmind-obsidian && npx vitest run test/styles.test.ts`
Expected: FAIL: every new class is listed as missing; the old ones are still there.

- [ ] **Step 3: Implement**

In `styles.css`, delete these lines: `.artmind-notice-buttons { ... }`, `.artmind-health { ... }`, `.artmind-health-secondary { ... }`, `.artmind-health-detail { ... }`, `.artmind-state { ... }`, `.artmind-primary { ... }`, `.artmind-primary .artmind-action { ... }`, `.artmind-hint { ... }`. Then append:

```css
/* ── the readiness checklist (checklist spec §3) ───────────────────────── */
.artmind-header-top { display: flex; align-items: center; justify-content: space-between; gap: 0.5em; color: var(--text-muted); font-size: var(--font-smaller); }
.artmind-refresh { font-size: var(--font-ui-medium); padding: 0 0.4em; height: 1.6em; }
.artmind-readiness { font-weight: var(--font-semibold); margin-top: 0.2em; }
.artmind-checklist { display: flex; flex-direction: column; gap: 0.15em; }
.artmind-step { padding: 0.35em 0.5em; border-radius: var(--radius-s); border-left: 3px solid transparent; }
.artmind-step-current { background: var(--background-secondary); border-left-color: var(--interactive-accent); }
.artmind-step-line { display: grid; grid-template-columns: 1.2em auto minmax(0, 1fr); align-items: baseline; gap: 0.4em; }
.artmind-step-glyph { text-align: center; }
.artmind-step-title { font-weight: var(--font-semibold); white-space: nowrap; }
.artmind-step-summary { color: var(--text-muted); font-size: var(--font-smaller); text-align: right; overflow: hidden; text-overflow: ellipsis; }
.artmind-step-done .artmind-step-title { color: var(--text-muted); font-weight: normal; }
.artmind-step-buttons { display: flex; flex-wrap: wrap; gap: 0.3em; justify-content: flex-end; margin-top: 0.3em; }
.artmind-step-buttons .artmind-action { margin: 0; }
.artmind-outline { background: transparent; box-shadow: inset 0 0 0 1px var(--background-modifier-border); }
.artmind-step-detail { font-size: var(--font-smaller); color: var(--text-muted); padding-left: 1.6em; }
.artmind-step-items { font-size: var(--font-smaller); padding-left: 1.6em; margin-top: 0.15em; }
.artmind-step-items > summary { color: var(--text-muted); cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.artmind-step-item { padding: 0.05em 0; overflow-wrap: anywhere; }
.artmind-step .artmind-section-job { margin-top: 0.4em; }
.artmind-footer { border-top: 1px solid var(--background-modifier-border); padding-top: 0.4em; }

/* ── notices, modals ───────────────────────────────────────────────────── */
.artmind-notice-link { cursor: pointer; }
.artmind-confirm-text { margin: 0 0 0.5em; }
.artmind-synth-domains { display: flex; flex-direction: column; gap: 0.2em; margin: 0.5em 0; }
.artmind-synth-domain { display: flex; align-items: center; gap: 0.3em; }
.artmind-synth-cap { display: flex; align-items: center; gap: 0.4em; }
.artmind-synth-cap input { width: 6em; }
.artmind-synth-total { font-weight: var(--font-semibold); }
```

- [ ] **Step 4: Run the tests and the build**

Run: `just obsidian-plugin-test && cd obsidian/artmind-obsidian && npm run build`
Expected: PASS, and `main.js` is written.

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/styles.css obsidian/artmind-obsidian/test/styles.test.ts
git commit -m "style(obsidian): checklist rows, footer, notices and modals

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: The user guide describes the checklist

**Files:**
- Modify: `docs/USER_GUIDE_TWO_LAPTOPS.md` §3.1 (from "The plugin adds an **artmind icon to the left ribbon**" to the end of the section, before `---`)

- [ ] **Step 1: Rewrite the plugin part of §3.1**

Replace the text from the paragraph starting "The plugin adds an **artmind icon to the left ribbon**" down to (not including) the `---` that ends §3 with:

```markdown
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
| From the other laptop | ✓ up to date, or what there is to apply, or conflicts; a hint when Obsidian Git's auto pull is off | **Apply to graph** (`artmind vault sync`) · **Resolve…** · **Complete the merge** · **Pull** (always there) |
| Documents | how many files to ingest (expand for the paths), the running job, failures | **Ingest N files** · **Retry failed** · **Retry job** |
| Tables | tables whose classifications need review; tables ready for the graph | **Review in admin console ↗** (opens the chat with the request filled in; you send it) · **Review & project…** |
| Graph | ✓ built and embedded, or *needs rebuild* with each reason | **Rebuild graph** (`artmind projection rebuild --sweep`) |
| Descriptions (optional) | how many entity descriptions could be synthesized, and the LLM calls that costs | **Synthesize…** (pick domains and an optional cap first) |
| Vault (footer) | artmind files to commit and commits to push | **Commit & push** (Obsidian Git's commit, pull, push) |

The header says **Graph ready ✓** when the first four rows are done, or how many
steps are left; **↻** re-reads everything. The **current step** (the first row
not done) is highlighted and has the only filled button. Other buttons still
work; clicking one out of order asks first (for example, *Ingest* while the
other laptop's changes aren't applied offers [Apply first] [Ingest anyway]).

Row 1 stays current by itself when **Obsidian Git's auto pull** is on (Obsidian
Git settings → "Auto pull interval", in minutes): the plugin reads that setting
and never fetches on its own. While auto pull is off, row 1 says "Turn on
Obsidian Git's auto pull to see the other laptop's changes", and *Apply to
graph* asks "Pull from GitHub first?" when the last pull is more than five
minutes old. **Pull** is always on row 1 for a pull by hand.

The status bar shows `artmind ●●○○○` and the current step: one dot per row
plus the footer, filled when done. Clicking it opens the panel at that row.

Notices are text only: they say what finished and what is next ("Ingested 365
files, 3 failed. Next: retry failed."), and clicking one opens the panel at its
row. Every action is also a command: *Pull*, *Apply to graph*, *Ingest what
changed*, *Rebuild graph*, *Synthesize…*, *Commit & push*, *Review tables for
graph*, *Resolve artmind conflicts*, *Run doctor*. A command that can't run
right now says why.

The plugin never ingests, rebuilds, synthesizes or writes to the graph without
a click, never writes to git (Pull, Commit & push and Complete the merge run
Obsidian Git's own commands), and never runs `--bootstrapEmpty` for you: §2.3 is
still a terminal step, and row 1 shows it. If Obsidian was started from the Dock
and cannot find `artmind`, set its path in the plugin's settings (`which
artmind` in a terminal).
```

- [ ] **Step 2: Check no stale label is left**

Run: `grep -n "Commit-and-sync\]\|Sync now\|◉ artmind\|Show status" docs/USER_GUIDE_TWO_LAPTOPS.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docs/USER_GUIDE_TWO_LAPTOPS.md
git commit -m "docs(guide): the Obsidian plugin's readiness checklist

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: End-to-end in Obsidian (manual)

The hermetic tests never start Obsidian, never touch Neo4j, and replay fixtures. This task does all three, in the user's vault (`~/artmind_vaults/my_second_brain`, or any vault with a mapped folder and a second clone or laptop to pull from). Nothing here is committed.

**Files:** none.

- [ ] **Step 1: Install the build and seed the vault**

```bash
cd ~/projects/artmind9 && just dev-install          # stops daemons, builds the plugin, installs artmind editable
cd ~/artmind_vaults/my_second_brain && artmind init  # copies the plugin build into .obsidian/plugins/artmind/
```

Expected: `init` reports the plugin installed. `dev-install` stopped `serve`; start it again **from inside the vault**, in its own terminal:

```bash
cd ~/artmind_vaults/my_second_brain && artmind serve
```

Check it is the fresh build: `curl -s http://127.0.0.1:8377/health` answers, and `artmind projection status --compact` prints `drift` and `unembedded_entities` (Plan 1's keys).

- [ ] **Step 2: Reload the plugin**

In Obsidian: Settings → Community plugins → artmind off, then on (or reload the app). Expected:
- the status bar shows `artmind` and five dots, then the current step's summary;
- the ribbon icon's right-click offers only *Open side panel*, *Open admin console*, *Run doctor*;
- the palette lists the twelve commands of Task 10 Step 13; none contains "sync".

- [ ] **Step 3: Pull, with Obsidian Git's auto pull off**

Check `.obsidian/plugins/obsidian-git/data.json` has no `autoPullInterval` (or 0); the user's vault today has `{"syncMethod":"merge","autoSaveInterval":10,"autoPullOnBoot":true}`. Expected: row 1 carries the line "Turn on Obsidian Git's auto pull to see the other laptop's changes" and an outline **Pull**.

On the other laptop (or a second clone), commit and push one note change. Here, run the palette's **Pull**. Expected: a text-only notice "Pulled: 1 doc to apply to graph." with no button; clicking it opens the panel at row 1, which shows `● 1 doc to apply` with a filled **Apply to graph**. Status bar: `artmind ○…  1 doc to apply`.

- [ ] **Step 4: Apply to graph**

Wait more than five minutes after the pull, then click **Apply to graph**. Expected: the confirm modal "Pull from GitHub first?" [Pull, then apply] [Apply without pulling]. Choose **Apply without pulling**. Row 1 shows `◌ applying to graph…`, then `✓ up to date · applied HH:MM`. Notice: "Applied to graph: 1 doc, 0 tables." plus " Next: …" naming the new current step.

Now turn auto pull on: Obsidian Git settings → Auto pull interval → 5. Press ↻. Expected: the hint line on row 1 is gone. Push another note from the other laptop and wait for Obsidian Git's auto pull: the "Pulled: 1 doc to apply to graph." notice arrives with no click here. Wait more than five minutes, then click **Apply to graph**: it applies straight away, with no "Pull from GitHub first?" modal. Leave auto pull as you prefer afterwards.

- [ ] **Step 5: Ingest**

Add two notes in a mapped folder. Press ↻. Expected: row 2 `● 2 to ingest`, its list showing both paths with `(new)`. Click **Ingest 2 files**. Expected: row 2 `◌ ingesting 0/2`, then `ingesting 1/2`, then `◌ finishing: building the graph` while `finalize.state` is `pending`; row 4 says `○ after ingest finishes`; the footer is hidden. When it ends: notice "Ingested 2 files. Next: …"; the job card stays under row 2 as "Last ingest job". Reload Obsidian: the job card is still there (persisted).

Out-of-order check: on the other laptop push another note, Pull here, then click **Ingest** on row 2 while row 1 is ●. Expected: "The other laptop has changes not applied to your graph." [Apply first] [Ingest anyway].

- [ ] **Step 6: Rebuild graph**

If row 4 is ✓, make it need a rebuild: add a comment line to `.artmind/same_as.yaml` (its hash changes, its meaning does not) and press ↻. Expected: row 4 `! needs rebuild` with the line "same_as edited" (and any un-embedded counts). Click **Rebuild graph**. Expected: row 4 `◌ rebuilding…`, then `✓ built · embedded · rebuilt HH:MM`; notice "Graph rebuilt: N entities, all embedded." Cross-check in a terminal: `ARTMIND_NO_PROXY=1 artmind projection status --compact` shows `drift: false`, `unembedded_entities: 0`, `unprojected_keys: 0`.

- [ ] **Step 7: Synthesize…**

Row 5 shows `◇ N could be synthesized · ~N LLM calls (<model>)`, never amber, never highlighted. Click **Synthesize…**. Expected: the modal lists each mapped domain with its count, the model, a cap field and the total. Set the cap to 2, keep one domain, click **Synthesize**. Expected: row 5 `◌ synthesizing…`, then a lower count; notice "Synthesized 2 descriptions. Next: commit & push."

- [ ] **Step 8: Commit & push**

Expected: the footer `● ✎ N artmind files to commit · ↑ 0` with "from: ingest (2 files) · syntheses (2)", and its **Commit & push** button is filled (rows 1–4 are ✓). The header says **Graph ready ✓**. Click it: Obsidian Git commits, pulls and pushes. Press ↻: the footer is `✓ shared`; the status bar shows `artmind ●●●●● graph ready`.

- [ ] **Step 9: Errors stay out of notices**

Stop Neo4j (`colima stop` or `docker stop <neo4j container>`), press ↻ and click **Rebuild graph** from the palette. Expected: a text-only notice "Neo4j unreachable" (the command is blocked), row 1 `! Neo4j unreachable`, and the problems callout below the footer names the URI. No notice contains a stack trace or stderr. Start Neo4j again and press ↻.

---

## Self-review against the spec

**§2 Decisions**
- D1 every graph write is a click: Rebuild graph (Task 10 `rebuild`), Synthesize… (Task 9 modal, Task 10 `synthesize`), Apply, Ingest, table projection — nothing runs from a read, a notice or the status bar (Task 7, 8, 10).
- D2 checklist with expandable rows: Task 5 (`items`), Task 11 (`checklistView.ts`, `data-items` details, `PanelUi.expanded`).
- D3 current step, filled button only there: Task 5 (`current`), Task 11 (`mod-cta` vs `artmind-outline`; test "only the current step's button is filled").
- D4 Synthesize optional, never amber or current: Task 5 (`optional: true`, tone blue/grey; test "Descriptions is never the current step").
- D5 Commit & push is a footer, filled only when rows 1–4 are ✓, hidden during a job: Task 5 (`vault` row, `hidden`), Task 11 (footer test).
- D6 labels Pull / Apply to graph / Commit & push, no "sync", all in `texts.ts`: Task 4 (`LABELS`, `COMMANDS`, `ROW_TITLES`; test "never say sync"); Obsidian Git's own command names remain only in its missing-command instructions (decision 12).
- D7 notices advise, no buttons, click opens the row: Task 10 (`Notifier.show(kind, text, target)`, `ObsidianNotifier`), notice map above.
- D8 status bar and ribbon open the panel, never act: Tasks 7, 8, 10 (`main.ts`).
- D9 backend: Plan 1.
- D10 auto pull keeps row 1 current: Task 3b (`autoPullMinutes`, `obsidianGitDataPath`), Task 2 (`StateInputs.obsidianGit`), Task 5 (row 1's hint line, Pull as an outline button; no "N commits on GitHub" state), Task 10 (`readObsidianGit` on every refresh through `app.vault.adapter.read`; `shouldOfferPull` asks only with auto pull off and an old pull; tests "never asks to pull when Obsidian Git's auto pull is on"), Task 13 (guide), Task 14 Steps 3–4.

**§3 The checklist**
- §3.1 layout, header "Graph ready ✓" / "N steps to a ready graph", ↻: Task 5 (`readinessText`), Task 11 (header test); callouts below the footer (problems, doctor) plus Stores and Activity: Task 11.
- §3.2 rows: row 1 (up to date · applied, Apply, Resolve…, Complete the merge, first sync, Pull always as an outline button, the auto-pull hint) — Task 5 cases; row 2 (N to ingest with new/changed paths, ingesting n/m, finishing: building the graph, N failed → Retry failed, stalled → Retry job, pending errors, Neo4j warning) — Task 5, Task 11 job card; row 3 (N need review → Review in admin console ↗ via `/?prompt=`, N ready → Review & project…, two mappings → Ask admin-ui) — Tasks 5, 6, 11; row 4 (built · embedded · rebuilt, needs rebuild with each reason incl. schema/same_as/entities/chunks/keys/last job's rebuild failed/sweep errors, after ingest finishes) — Task 5; row 5 (count · ~N LLM calls (model) → Synthesize…) — Task 5; footer (✎ N artmind files to commit · ↑ N, from: ingest · syntheses) — Tasks 3, 5. Order rules and `ready` — Task 5. No commit offer after table2graph — Task 10 (`tableProject`; test "offers no commit after it").
- §3.3 confirm modal: Ingest while row 1 ● → [Apply first][Ingest anyway]; Apply with auto pull off and an old pull → [Pull, then apply][Apply without pulling], never with auto pull on — Task 9 (modal), Task 10 (flows and tests); Ingest never asks about pulling (decision 4); replaces notices #9, #17, #18.
- §3.4 Synthesize… modal: per-domain counts, model, cap, total, [Synthesize][Cancel], runs each domain in turn through the write queue, re-reads row 5 and the footer — Task 9, Task 10 (`synthesize`, `refresh({ heavy: true })`), Task 1 (`synthesize` is a write).

**§4 Other surfaces**
- Status bar `artmind ●●○○○ <current>`, tone, tooltip, click opens at the current row — Task 7, Task 10 (`openPanel()` defaults to `checklist.current`).
- Ribbon: click opens the panel; menu keeps Open side panel / Open admin console / Run doctor; tone dot — Task 8.
- Palette: Pull, Apply to graph, Ingest what changed, Rebuild graph, Synthesize…, Commit & push, Review tables for graph, Resolve artmind conflicts, Run doctor, Open side panel, Open admin console, Stop admin console; blocked → text-only notice — Task 10 (`addCommands`, `guard`; main test).
- Settings: `offerCommitAndSync` removed; `rebuildDone`, `synthesizeDone` added (and `syncDone` renamed `applyDone`) — Task 10.
- Persistence of last job card, last apply, last rebuild — Task 6 (`persist.ts`), Task 10 (`update` → `persist`, `main.saveAll`).

**§5 Notices** — text only, 6 s / 10 s, click opens the row, action-finished notices name the next step (`withNext`), raw stderr never in a notice (`failedText`, raw error to `actionErrors` → row detail), blocked only for palette runs: Tasks 4, 5, 10; every notice #1–#28 mapped in the table above.

**§5.1 State model** — `StateInputs` gains `projection`, `tablesReview`, `synthesis`, `lastJob` (and `lastApply`, `lastRebuild`, `busy`, `actionErrors`, `obsidianGit`): Task 2; pure `checklist(inputs)` returning rows with `id`, `state`, `glyph`, `tone`, `title`, `detail[]`, `items[]`, buttons with `blockedReason`, `optional`, plus `current`, `ready`: Task 5 (`buttons[]` instead of one `action`: decision 1); `vaultState` replaced: Task 11; read cadence (↻, after writes, after a job, focus ≤ 1/min): Task 10.

**§7 Plugin testing** — `checklist()` table-driven per row state, current step, ready, footer emphasis: Task 5. Notifier has no buttons and clicks open its row: Task 10/11 views tests. Confirm modal only for out-of-order clicks (and the pull check): Task 10 ("following the current step never asks"). Panel render: only the current row's button filled: Task 11. End to end: Task 14.

**Placeholder scan** — every code step has its code; the only "if" step is Task 0's stop condition. **Type consistency** — `RowId`/`PanelTarget`/`StateInputs` in `state.ts` (Task 2, kept in Task 11); `Checklist`/`Row`/`RowAction`/`CommandId` in `checklist.ts` (Task 5); `autoPullMinutes`/`obsidianGitDataPath` in `bridge.ts` (Task 3b), `readObsidianGit` in `ControllerDeps` and `main.ts` (Task 10), `AUTO_PULL_HINT` in `texts.ts` (Task 4); `ConfirmRequest` in `confirmModal.ts` (Task 9); `NoticeLevel`/`Notifier`/`ViewsLike` in `controller.ts` (Task 10); `PanelUi`/`section` in `sections.ts` (Task 11); controller methods `apply`, `ingestWhatChanged`, `rebuild`, `openSynthesize`, `synthesize`, `synthesisPlan`, `commitPush`, `pull`, `openResolve`, `reviewTables`, `run`, `refresh({ heavy })`, `scheduleRefresh(reason)`, `persisted()` are the names main and the tests call.
