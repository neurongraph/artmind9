# artmind for Obsidian — Plan 2: the plugin

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Plan 1 must be implemented first.** This plan calls `artmind --version`, `ingest pending`, `ingest async --pending --compact` and `ingest table2graph --pending`, requires artmind 0.9.0, and its tests read the JSON fixtures Plan 1's Task 6 writes under `obsidian/artmind-obsidian/test/fixtures/`. Start from a branch that contains Plan 1.

**Goal:** A desktop-only Obsidian plugin, in this repo at `obsidian/artmind-obsidian/`, that always shows where the vault stands with artmind — merge conflicts, errors, a running ingest, stores behind HEAD, tables waiting for the graph, files to ingest — and offers the one action that state calls for, by running the `artmind` CLI and triggering Obsidian Git's own commands.

**Architecture:** Spec [2026-09-30-obsidian-plugin-design.md](../specs/2026-09-30-obsidian-plugin-design.md) §3–§5, §7–§9. Six units, as the spec names them, plus two small helpers:

- `ArtmindCli` (`src/cli.ts`) runs `artmind <args> --compact` in the vault, parses the JSON, unboxes rich-click's error box, applies per-command timeouts, runs writes one at a time from a queue, and finds `artmind` when Obsidian was started from the Dock.
- `VaultState` (`src/state.ts`) is a pure function from the CLI's JSON and the git facts to the states, the primary one, the secondary indicators, and why each action is blocked; `classifyRefusal` turns a `vault sync` refusal into its fixing action.
- `Watchers` (`src/watchers.ts`): the HEAD poll, new files in mapped folders (with `src/manifest.ts`), focus regained, the job poll.
- `ObsidianGitBridge` (`src/bridge.ts`) finds and runs Obsidian Git's Pull, Commit-and-sync and Commit.
- Views (`src/views/`): status bar item, side panel, `table2graph` review, resolve preview, notices, settings tab. They render and call; they decide nothing.
- `Settings` (`src/settings.ts`).
- Helpers: `GitReader` (`src/git.ts`, the three read-only git commands), notice texts (`src/texts.ts`).
- The flows of spec §4 live in `Controller` (`src/controller.ts`); `src/main.ts` only wires the units to Obsidian.

**Verified:** every code block in this plan was applied, in task order, by a script that parses this file, on top of Plan 1 on branch `obsidian-plans-applied`, one commit per task; after each task the plugin's `npm test` and `npm run build` were run — see "Verification record". Each "Expected: FAIL" is what the task's new tests printed when run before that task's source files existed.

**Tech Stack:** TypeScript 6.0, esbuild 0.28 (bundle to CommonJS `main.js`), the `obsidian` 1.13.1 typings, vitest 5 (Node; jsdom for the view tests), `yaml` (the test stand-in for Obsidian's `parseYaml`). Node 22.

---

## Context for the implementer

Read before starting:

- The spec above, all of it. It is short, and every label, notice text and button name below comes from it.
- `docs/USER_GUIDE_TWO_LAPTOPS.md` §3 (the everyday habit the plugin automates) and `docs/vault.md` "Git: Obsidian Git owns transport" (why the plugin never writes git).
- Plan 1's fixtures, `obsidian/artmind-obsidian/test/fixtures/*.json`: one file per CLI command and state, `{"args", "exit_code", "json", "stderr"}`. Read `vault-status.behind.json` and `vault-sync.refusal-worker-running.json` before Task 2. They are the ground truth for every type in `src/types.ts`; if one of them changes, `test/test_plugin_fixtures.py` in the Python suite says so first.
- `CLAUDE.md`: the plugin is new territory for this repo, but its rules still apply — test what was actually sent (`test/cli.test.ts` records every run of a fake `artmind` in a log file; the controller tests record every command), and a mock that answers everything proves nothing.

**Why vitest.** It runs TypeScript directly (no build step before tests), has fake timers that also advance promises (`vi.advanceTimersByTimeAsync`, which the HEAD-settle and grouping logic need), a per-file jsdom environment for the four DOM-rendering views, and `resolve.alias` to stand `obsidian` — which only exists inside the app — in with `test/mocks/obsidian.ts`. Jest would need ts-jest or Babel for the same; `node:test` has no module aliasing.

**Obsidian Git's command ids**, read from obsidian-git 2.40.0's `main.js` (`addCommand({ id: ..., name: ... })`; Obsidian prefixes the plugin id): Pull `obsidian-git:pull` ("Pull"); Commit-and-sync `obsidian-git:push` ("Commit-and-sync"; the same command was named "Create backup" in older releases); Commit `obsidian-git:commit` ("Commit all changes" — stages everything and commits, which is what concludes a merge; `obsidian-git:commit-smart`, "Commit", commits only what is staged when anything is). Fallbacks, when a known id is not registered: any `obsidian-git:*` command whose name (after "Git: " / "Obsidian Git: ") is "Pull"; "Commit-and-sync" or "Create backup"; "Commit all changes". When none is found, the button shows the instruction instead ("Run Obsidian Git: Pull") and the panel shows one warning line.

**rich-click errors.** A refusing `artmind` command prints its message in a box on stderr (`╭─ Error ─…╮ │ message │ ╰─…╯`), wrapped to the terminal width, below any log lines. `errorText` takes the last box, strips the borders and joins the lines. See `vault-sync.refusal-*.json`.

Run the plugin's checks from the repo root with:

```bash
cd obsidian/artmind-obsidian && npm test && npm run build
```

`npm run build` type-checks (`tsc --noEmit`) before bundling. Never commit `node_modules/` or `main.js` (Task 1's `.gitignore`). Do not install into a real vault; the user does that ("Live verification (user)").

## Decisions this plan makes (the spec left them open)

Each is argued under "Self-review → Design decisions the spec did not settle".

1. **A `Controller` holds the flows** [1] (spec §4); `main.ts` only wires. The spec's unit table has no home for "what Sync does, step by step".
2. **A `GitReader` does the three read-only git commands** [2] (`rev-parse HEAD`, `rev-list --count @{upstream}..HEAD`, `status --porcelain -z -- .artmind`), so "the plugin only reads git" is one file and one test.
3. **Refusals are classified from the error text** [3]: stable phrases of `vault sync`'s messages, tested on the real fixtures. The panel also *predicts* most of them from `vault status` (merge in progress, conflicts, no bookmark, a running job) and disables Sync with the reason, so a refusal is rare.
4. **States beyond the spec's table** [4]: a bookmark not in HEAD's history shows `◉ pull first` (amber, click runs Obsidian Git's Pull); a store with no bookmark shows `◉ sync not set up` (amber, click opens the panel at the bootstrap step). Both rank as "behind". A failing `vault status`, or a store whose sync would fail, is the error state.
5. **Ambiguous tables do not count as "→ graph"** [5]; they are listed in the panel, and their review shows `table2graph`'s own refusal.
6. **Timeouts** [6]: `--version` 15 s, `vault status`/`doctor` 60 s, `ingest pending`/`async` 2 min, job reads 30 s, `vault resolve` 5 min, `vault sync` and `table2graph` 60 min, anything else 5 min. Writes run detached (spec §8: Obsidian quitting leaves them to finish).
7. **The child's PATH** [7] gets `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin` appended: artmind runs `git`, and its worker runs `uv run docling`, which a Dock-launched Obsidian's PATH does not reach. `NO_COLOR=1`.
8. **"Last pull"** [8] is when the HEAD poll last saw HEAD move, or when a plugin-run Pull settled; never seen counts as old. A pull that moved nothing is invisible to the poll, so "Pull first?" can be offered after a no-op pull made outside the plugin.
9. **"Writes the plugin didn't make"** [9]: uncommitted `.artmind/` changes count unless a plugin write finished less than 30 s ago or a plugin-tracked ingest job is running.
10. **New-file grouping is a sliding 10 s window** [10]: the notice fires 10 s after the last arrival. With no mappings in `vault.yaml`, nothing prompts.
11. **[Complete the merge] only when nothing is left for the user** [11]: after `vault resolve`, only when its report has no pending and no reported paths; otherwise the notice says what to resolve by hand. Committing with a note still conflicted would commit conflict markers.
12. **[Open mapping] opens the YAML in the system editor** [12] (Electron's `shell.openPath`): Obsidian cannot open a file under `.artmind/`, which it does not index. A reported path under a dot-folder in the resolve preview opens the same way; a note opens in Obsidian.
13. **The minimum artmind is 0.9.0** [13] (Plan 1's version). Without `--version` at all, artmind is "too old".
14. **New files are listened for only once Obsidian's layout is ready** [14]: Obsidian fires `create` for every file while a vault loads.
15. **The manifest is re-read on every refresh** [15]: no Obsidian event reports a change under `.artmind/`.

## File map

| File | Task | Responsibility |
|---|---|---|
| `obsidian/artmind-obsidian/package.json`, `package-lock.json`, `tsconfig.json`, `esbuild.config.mjs`, `vitest.config.ts`, `manifest.json`, `styles.css`, `.gitignore` | 1 | toolchain, Obsidian manifest (`isDesktopOnly: true`), styles |
| `obsidian/artmind-obsidian/src/version.ts` | 1 | `MIN_ARTMIND_VERSION`, parse/compare `artmind --version` |
| `obsidian/artmind-obsidian/src/main.ts` | 1 (a no-op plugin), 10 (the wiring) | the Obsidian `Plugin` |
| `obsidian/artmind-obsidian/test/mocks/obsidian.ts` | 1 | the `obsidian` module for tests |
| `justfile` | 1 | `obsidian-plugin-deps`, `-test`, `-build`, `-install <vault>` |
| `obsidian/artmind-obsidian/src/cli.ts` | 2 | `ArtmindCli`, `errorText`, `isWrite`, `detectArtmind`, `childEnv`, `spawnRunner` |
| `obsidian/artmind-obsidian/test/fake-artmind.mjs`, `test/fixtures.ts` | 2 | a scripted `artmind`; the fixture loader |
| `obsidian/artmind-obsidian/src/types.ts`, `src/state.ts` | 3 | the CLI's JSON types; `vaultState`, `classifyRefusal` |
| `obsidian/artmind-obsidian/src/git.ts` | 4 | `GitReader` |
| `obsidian/artmind-obsidian/src/bridge.ts` | 5 | `ObsidianGitBridge` |
| `obsidian/artmind-obsidian/src/manifest.ts`, `src/watchers.ts` | 6 | mapped folders; `Watchers` |
| `obsidian/artmind-obsidian/src/settings.ts`, `src/texts.ts` | 7 | settings and their defaults; the notice texts |
| `obsidian/artmind-obsidian/src/controller.ts` | 8 | the flows |
| `obsidian/artmind-obsidian/src/views/*.ts` | 9 | status bar, panel, review modals, notices, settings tab |
| `CLAUDE.md`, `docs/USER_GUIDE_TWO_LAPTOPS.md` | 11 | where the plugin lives; how to use it |

Every `test/*.test.ts` belongs to the task of the unit it tests.

---

### Task 1: scaffold — toolchain, manifest, the `obsidian` stand-in, `just` recipes

A plugin that builds, loads and does nothing yet, with the test runner working. `package.json` pins exact versions; `npm install` writes `package-lock.json`, which is committed so `npm ci` (the `just` recipe) is reproducible. `manifest.json` sets `isDesktopOnly: true` (spec P8). esbuild bundles `src/main.ts` to CommonJS `main.js`, leaving `obsidian`, `electron` and Node's own modules external: Obsidian provides them. `vitest.config.ts` points `obsidian` at `test/mocks/obsidian.ts`, a stand-in covering what the views import and recording what they did (the notices shown, a plugin's status bar items, commands and views).

**Files:**
- Create: `obsidian/artmind-obsidian/package.json`, `.gitignore`, `manifest.json`, `tsconfig.json`, `esbuild.config.mjs`, `vitest.config.ts`, `styles.css`, `src/main.ts`, `src/version.ts`, `test/mocks/obsidian.ts`, `test/version.test.ts`
- Modify: `justfile`

- [ ] **Step 1: The toolchain**

**Create** `obsidian/artmind-obsidian/package.json`:

```json
{
  "name": "artmind-obsidian",
  "version": "0.1.0",
  "private": true,
  "description": "artmind for Obsidian: vault sync, ingest and table projection state, one click away.",
  "type": "module",
  "scripts": {
    "build": "tsc --noEmit && node esbuild.config.mjs production",
    "dev": "node esbuild.config.mjs",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "devDependencies": {
    "@types/node": "22.20.4",
    "esbuild": "0.28.2",
    "jsdom": "30.1.1",
    "obsidian": "1.13.1",
    "typescript": "6.0.3",
    "vitest": "5.0.2",
    "yaml": "2.9.1"
  }
}
```

**Create** `obsidian/artmind-obsidian/.gitignore`:

```gitignore
# npm installs these; `npm ci` restores them from package-lock.json
node_modules/
# build output (`npm run build`); `just obsidian-plugin-install` copies it into a vault
main.js
```

**Create** `obsidian/artmind-obsidian/manifest.json`:

```json
{
  "id": "artmind",
  "name": "artmind",
  "version": "0.1.0",
  "minAppVersion": "1.5.0",
  "description": "Shows where this vault stands with artmind (sync, ingest, tables for the graph) and offers the one action that state calls for.",
  "author": "Surjit Das",
  "isDesktopOnly": true
}
```

**Create** `obsidian/artmind-obsidian/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["DOM", "ES2022"],
    "types": ["node"],
    "strict": true,
    "noImplicitOverride": true,
    "noUnusedLocals": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "noEmit": true,
    "resolveJsonModule": true
  },
  "include": ["src/**/*.ts", "test/**/*.ts"]
}
```

**Create** `obsidian/artmind-obsidian/esbuild.config.mjs`:

```js
// Bundles src/main.ts into main.js, the file Obsidian loads. `obsidian`,
// `electron` and Node's own modules are provided by Obsidian at runtime.
import esbuild from "esbuild";
import { builtinModules } from "node:module";

const production = process.argv[2] === "production";

const context = await esbuild.context({
  entryPoints: ["src/main.ts"],
  bundle: true,
  external: [
    "obsidian",
    "electron",
    "@codemirror/*",
    "@lezer/*",
    ...builtinModules,
    ...builtinModules.map((m) => `node:${m}`),
  ],
  format: "cjs",
  target: "es2022",
  platform: "node",
  logLevel: "info",
  sourcemap: production ? false : "inline",
  treeShaking: true,
  outfile: "main.js",
});

if (production) {
  await context.rebuild();
  await context.dispose();
} else {
  await context.watch();
}
```

**Create** `obsidian/artmind-obsidian/vitest.config.ts`:

```ts
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Tests run in plain Node. Obsidian's API only exists inside the app, so
// `obsidian` resolves to a small stand-in (test/mocks/obsidian.ts).
export default defineConfig({
  resolve: {
    alias: { obsidian: fileURLToPath(new URL("./test/mocks/obsidian.ts", import.meta.url)) },
  },
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
  },
});
```

**Create** `obsidian/artmind-obsidian/styles.css`:

```css
/* artmind for Obsidian. Colours come from Obsidian's theme variables. */
.artmind-status { cursor: pointer; }
.artmind-tone-red { color: var(--text-error); }
.artmind-tone-amber { color: var(--color-orange); }
.artmind-tone-blue { color: var(--color-blue); }
.artmind-tone-grey { color: var(--text-muted); }
.artmind-tone-spinner { color: var(--text-accent); }
.artmind-section { margin-bottom: 1em; }
.artmind-row { display: flex; justify-content: space-between; gap: 0.5em; }
.artmind-row-label { color: var(--text-muted); }
.artmind-error { color: var(--text-error); }
.artmind-warning { color: var(--color-orange); }
.artmind-blocked { color: var(--text-muted); font-size: var(--font-smaller); margin: 0 0.5em; }
.artmind-action { margin: 0.25em 0.25em 0.25em 0; }
.artmind-notice-buttons { display: flex; gap: 0.5em; margin-top: 0.5em; }
.artmind-run pre { white-space: pre-wrap; font-size: var(--font-smaller); }
.artmind-run-failed summary { color: var(--text-error); }
.artmind-finding code { display: block; margin: 0.25em 0; }
.artmind-review-buttons { display: flex; gap: 0.5em; margin-top: 1em; align-items: center; }
.artmind-skip { color: var(--text-muted); margin-left: 1em; }
```

**Create** `obsidian/artmind-obsidian/src/main.ts`:

```ts
import { Plugin } from "obsidian";

/** artmind for Obsidian (spec 2026-09-30). Task 10 of the plugin plan wires
 * it up; until then it loads and does nothing. */
export default class ArtmindPlugin extends Plugin {}
```

**Create** `obsidian/artmind-obsidian/test/mocks/obsidian.ts`:

```ts
/** Stand-in for the `obsidian` module, which only exists inside the app.
 * It covers what the plugin's views import, and records what they did. */
import { parse } from "yaml";

export class Notice {
  static shown: Notice[] = [];
  message: string | DocumentFragment;
  duration: number | undefined;
  hidden = false;

  constructor(message: string | DocumentFragment, duration?: number) {
    this.message = message;
    this.duration = duration;
    Notice.shown.push(this);
  }

  hide(): void {
    this.hidden = true;
  }
}

export class Modal {
  app: unknown;
  contentEl: HTMLElement;
  titleEl: HTMLElement;
  isOpen = false;

  constructor(app: unknown) {
    this.app = app;
    this.contentEl = document.createElement("div");
    this.titleEl = document.createElement("div");
  }

  open(): void {
    this.isOpen = true;
    void (this as unknown as { onOpen?: () => unknown }).onOpen?.();
  }

  close(): void {
    this.isOpen = false;
    (this as unknown as { onClose?: () => void }).onClose?.();
  }
}

export class ItemView {
  leaf: unknown;
  contentEl: HTMLElement;

  constructor(leaf: unknown) {
    this.leaf = leaf;
    this.contentEl = document.createElement("div");
  }
}

export class WorkspaceLeaf {}

/** Records what a plugin registers; `app` and `manifest` come from the test. */
export class Plugin {
  app: any;
  manifest: { id: string };
  statusBarItems: HTMLElement[] = [];
  commands: Array<{ id: string; name: string }> = [];
  views: string[] = [];
  events: unknown[] = [];
  data: unknown = null;

  constructor(app: any, manifest: { id: string }) {
    this.app = app;
    this.manifest = manifest;
  }

  async loadData(): Promise<unknown> {
    return this.data;
  }

  async saveData(data: unknown): Promise<void> {
    this.data = data;
  }

  addStatusBarItem(): HTMLElement {
    const el = document.createElement("div");
    this.statusBarItems.push(el);
    return el;
  }

  addCommand(command: { id: string; name: string }): void {
    this.commands.push(command);
  }

  registerView(type: string): void {
    this.views.push(type);
  }

  addSettingTab(): void {}

  registerEvent(ref: unknown): void {
    this.events.push(ref);
  }

  registerDomEvent(): void {}
}

export class PluginSettingTab {
  app: unknown;
  containerEl: HTMLElement;

  constructor(app: unknown, _plugin: unknown) {
    this.app = app;
    this.containerEl = document.createElement("div");
  }
}

export class Setting {
  constructor(_containerEl: HTMLElement) {}
  setName(): this {
    return this;
  }
  setDesc(): this {
    return this;
  }
  addText(): this {
    return this;
  }
  addToggle(): this {
    return this;
  }
  addButton(): this {
    return this;
  }
}

export class FileSystemAdapter {}

export function parseYaml(text: string): unknown {
  return parse(text);
}

export function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+/g, "/").replace(/^\/|\/$/g, "");
}
```

Run: `cd obsidian/artmind-obsidian && npm install`
Expected: `added … packages`, a new `package-lock.json`, and `npm audit` warnings from the toolchain's own dependencies (dev-only; nothing ships but `main.js`).

- [ ] **Step 2: Write the failing test**

**Create** `obsidian/artmind-obsidian/test/version.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { MIN_ARTMIND_VERSION, atLeast, formatVersion, parseVersion } from "../src/version";

describe("version", () => {
  it("parses what `artmind --version` prints", () => {
    expect(parseVersion("artmind 0.9.0\n")).toEqual([0, 9, 0]);
    expect(parseVersion("artmind 12.3.45")).toEqual([12, 3, 45]);
  });

  it("rejects anything else", () => {
    expect(parseVersion("")).toBeNull();
    expect(parseVersion("Usage: artmind [OPTIONS]")).toBeNull();
    expect(parseVersion("artmind, version 0.9.0")).toBeNull();
  });

  it("compares against the minimum numerically, not as text", () => {
    expect(atLeast([0, 9, 0], "0.9.0")).toBe(true);
    expect(atLeast([0, 10, 0], "0.9.0")).toBe(true);
    expect(atLeast([1, 0, 0], "0.9.0")).toBe(true);
    expect(atLeast([0, 8, 9], "0.9.0")).toBe(false);
    expect(atLeast([0, 9, 0], "0.9.1")).toBe(false);
  });

  it("needs the artmind that has --version and the pending commands", () => {
    expect(MIN_ARTMIND_VERSION).toBe("0.9.0");
    expect(formatVersion([0, 9, 0])).toBe("0.9.0");
  });
});
```

Run: `cd obsidian/artmind-obsidian && npx vitest run test/version.test.ts`
Expected: the file fails to load — `Error: Cannot find module '../src/version' imported from …/test/version.test.ts` (`Test Files  1 failed (1)`, `Tests  no tests`).

- [ ] **Step 3: Implement**

**Create** `obsidian/artmind-obsidian/src/version.ts`:

```ts
/** The oldest artmind this plugin works with: the first with `--version`,
 * `ingest pending`, `ingest async --pending` and `table2graph --pending`
 * (spec 2026-09-30 §6 X1–X4). */
export const MIN_ARTMIND_VERSION = "0.9.0";

export type Version = [number, number, number];

/** `artmind 0.9.0` (what `artmind --version` prints) -> [0, 9, 0]. */
export function parseVersion(stdout: string): Version | null {
  const match = /^artmind (\d+)\.(\d+)\.(\d+)\s*$/m.exec(stdout);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

export function atLeast(found: Version, minimum: string): boolean {
  const need = minimum.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if (found[i] !== need[i]) return found[i] > need[i];
  }
  return true;
}

export function formatVersion(version: Version): string {
  return version.join(".");
}
```

- [ ] **Step 4: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  1 passed (1); Tests  4 passed (4)`; the build prints nothing after its two commands and writes `main.js`.

Run: `git status --short --ignored obsidian/artmind-obsidian | grep -E "node_modules|main.js"`
Expected: `!! obsidian/artmind-obsidian/main.js` and `!! obsidian/artmind-obsidian/node_modules/` (ignored).

- [ ] **Step 5: The `just` recipes**

**Replace in** `justfile` (the header's list of repo-only prefixes):

```just
#   canvas- — the artmind_canvas client (a separate FastAPI backend + Vite
#             frontend under artmind_canvas/, not an `artmind` subcommand)
```

**with:**

```just
#   canvas- — the artmind_canvas client (a separate FastAPI backend + Vite
#             frontend under artmind_canvas/, not an `artmind` subcommand)
#   obsidian- — the artmind Obsidian plugin (TypeScript, under
#             obsidian/artmind-obsidian/, not an `artmind` subcommand)
```

**Replace in** `justfile` (before the shared primitive):

```just
# ── shared primitive ─────────────────────────────────────────────────────────
```

**with:**

```just
# ── obsidian (the artmind Obsidian plugin, obsidian/artmind-obsidian/) ───────
#
# A TypeScript plugin that runs the `artmind` CLI with --compact (spec
# docs/superpowers/specs/2026-09-30-obsidian-plugin-design.md). Its toolchain
# is pinned in package-lock.json; its tests run in plain Node (vitest), with
# the CLI's JSON fixtures regenerated by test/test_plugin_fixtures.py.

# install the plugin's pinned dev dependencies, once (npm ci from package-lock.json)
obsidian-plugin-deps:
    cd obsidian/artmind-obsidian && (test -d node_modules || npm ci)

# run the plugin's tests (vitest) and its type check
obsidian-plugin-test: obsidian-plugin-deps
    cd obsidian/artmind-obsidian && npm test && npm run typecheck

# build the plugin's main.js (type check, then esbuild)
obsidian-plugin-build: obsidian-plugin-deps
    cd obsidian/artmind-obsidian && npm run build

# build, then copy main.js, manifest.json and styles.css into <vault>/.obsidian/plugins/artmind/  (usage: just obsidian-plugin-install ~/artmind_vaults/my_vault)
obsidian-plugin-install vault: obsidian-plugin-build
    #!/usr/bin/env bash
    set -euo pipefail
    vault="{{ vault }}"
    vault="${vault/#\~/$HOME}"
    if [ ! -d "$vault/.obsidian" ]; then
        echo "$vault is not an Obsidian vault (no .obsidian/ folder)" >&2
        exit 1
    fi
    dest="$vault/.obsidian/plugins/artmind"
    mkdir -p "$dest"
    cp obsidian/artmind-obsidian/main.js obsidian/artmind-obsidian/manifest.json obsidian/artmind-obsidian/styles.css "$dest/"
    echo "Installed into $dest"
    echo "In Obsidian: Settings -> Community plugins -> enable 'artmind'."
    echo "Obsidian Git commits these files; the other laptop gets the plugin with its next pull."

# ── shared primitive ─────────────────────────────────────────────────────────
```

Run: `just obsidian-plugin-test`
Expected: the vitest summary, then `> tsc --noEmit` with no errors.

Run: `mkdir -p /tmp/fakevault/.obsidian && just obsidian-plugin-install /tmp/fakevault && ls /tmp/fakevault/.obsidian/plugins/artmind && rm -rf /tmp/fakevault`
Expected: `Installed into /tmp/fakevault/.obsidian/plugins/artmind`, then `main.js  manifest.json  styles.css`.

Run: `just obsidian-plugin-install /nonexistent`
Expected: `/nonexistent is not an Obsidian vault (no .obsidian/ folder)`, exit code 1.

- [ ] **Step 6: Commit**

```bash
git add obsidian/artmind-obsidian/package.json obsidian/artmind-obsidian/package-lock.json obsidian/artmind-obsidian/.gitignore obsidian/artmind-obsidian/manifest.json obsidian/artmind-obsidian/tsconfig.json obsidian/artmind-obsidian/esbuild.config.mjs obsidian/artmind-obsidian/vitest.config.ts obsidian/artmind-obsidian/styles.css obsidian/artmind-obsidian/src obsidian/artmind-obsidian/test/mocks obsidian/artmind-obsidian/test/version.test.ts justfile
git commit -m "feat(obsidian): plugin scaffold -- toolchain, manifest, test runner, just recipes" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `ArtmindCli` — run, parse, unbox, time out, queue writes, find `artmind` (spec §7, §8, §9)

`run(args)` appends `--compact` (not to `--version`), picks the timeout by the first two words, and returns a `CliResult`: `ok` (exit 0, no timeout, JSON parsed), `json` (stdout parsed even on a non-zero exit — `vault doctor` exits 1 with a report), and `error`, the one-line reason (`errorText` of stderr; "could not run …" when the path is wrong; "timed out after N s"). **Writes** — `vault sync`, `vault resolve`, `ingest async`, `ingest table2graph` without `--dryRun`/`--pending` — chain onto one promise, so each starts after the previous one settled (a failure does not jam the queue); reads start at once.

`detectArtmind(setting, deps)` looks where a Dock-launched Obsidian would not: the setting when set (and only there), `~/.local/bin/artmind`, `uv tool dir --bin`, then the tool's own venv under `uv tool dir`; it reports every place it looked.

The tests run `ArtmindCli` against `test/fake-artmind.mjs`, a Node script that answers each command from a rules file (stdout, stderr, exit code, delay) and logs `start …`/`end …` lines — the log is what the queue tests assert on. Refusal stderr comes from Plan 1's fixtures.

**Files:**
- Create: `obsidian/artmind-obsidian/src/cli.ts`, `test/fake-artmind.mjs`, `test/fixtures.ts`, `test/cli.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/fake-artmind.mjs` (executable):

```js
#!/usr/bin/env node
// A stand-in `artmind` for ArtmindCli's tests. FAKE_ARTMIND_RULES names a
// JSON file of [{match, stdout, stderr, exit, delayMs}]: the first rule
// whose `match` starts the joined args answers. FAKE_ARTMIND_LOG, when set,
// gets a "start <args>" and an "end <args>" line per run.
import { appendFileSync, readFileSync } from "node:fs";

const key = process.argv.slice(2).join(" ");
const rules = JSON.parse(readFileSync(process.env.FAKE_ARTMIND_RULES, "utf8"));
const rule = rules.find((r) => key.startsWith(r.match)) ?? { stderr: `no rule for ${key}`, exit: 99 };
const log = process.env.FAKE_ARTMIND_LOG;
if (log) appendFileSync(log, `start ${key}\n`);
setTimeout(() => {
  if (log) appendFileSync(log, `end ${key}\n`);
  process.stdout.write(rule.stdout ?? "");
  process.stderr.write(rule.stderr ?? "");
  process.exit(rule.exit ?? 0);
}, rule.delayMs ?? 0);
```

**Create** `obsidian/artmind-obsidian/test/fixtures.ts`:

```ts
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** One captured CLI run (artmind's test/test_plugin_fixtures.py writes these). */
export interface Fixture {
  args: string[];
  exit_code: number;
  json: any;
  stderr: string | null;
}

/** A path, not a URL: under jsdom, `import.meta.url` is not a file URL. */
const DIR = join(import.meta.dirname, "fixtures");

export function fixture(name: string): Fixture {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8"));
}

export function fixtureNames(): string[] {
  return readdirSync(DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.slice(0, -".json".length))
    .sort();
}
```

**Create** `obsidian/artmind-obsidian/test/cli.test.ts`:

```ts
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import {
  ArtmindCli,
  type DetectDeps,
  childEnv,
  commandKey,
  detectArtmind,
  errorText,
  isWrite,
} from "../src/cli";
import { fixture } from "./fixtures";

const FAKE = fileURLToPath(new URL("./fake-artmind.mjs", import.meta.url));

interface Rule {
  match: string;
  stdout?: string;
  stderr?: string;
  exit?: number;
  delayMs?: number;
}

/** An ArtmindCli running the fake script with `rules`, logging to a file. */
function fakeCli(rules: Rule[], timeouts?: Record<string, number>) {
  const dir = mkdtempSync(join(tmpdir(), "artmind-cli-"));
  const rulesFile = join(dir, "rules.json");
  const log = join(dir, "log.txt");
  writeFileSync(rulesFile, JSON.stringify(rules));
  writeFileSync(log, "");
  const cli = new ArtmindCli({
    path: FAKE,
    cwd: dir,
    timeouts,
    env: { ...process.env, FAKE_ARTMIND_RULES: rulesFile, FAKE_ARTMIND_LOG: log },
  });
  return { cli, lines: () => readFileSync(log, "utf8").trim().split("\n").filter(Boolean) };
}

beforeAll(() => chmodSync(FAKE, 0o755));

describe("ArtmindCli.run", () => {
  it("adds --compact and parses the JSON", async () => {
    const status = fixture("vault-status.in-sync");
    const { cli, lines } = fakeCli([{ match: "vault status --compact", stdout: JSON.stringify(status.json) }]);

    const result = await cli.status();

    expect(result.ok).toBe(true);
    expect(result.args).toEqual(["vault", "status", "--compact"]);
    expect(result.json).toEqual(status.json);
    expect(result.error).toBeNull();
    expect(lines()).toEqual(["start vault status --compact", "end vault status --compact"]);
  });

  it("runs --version without --compact and keeps its text", async () => {
    const { cli } = fakeCli([{ match: "--version", stdout: "artmind 0.9.0\n" }]);

    const result = await cli.version();

    expect(result.args).toEqual(["--version"]);
    expect(result.ok).toBe(true);
    expect(result.stdout).toBe("artmind 0.9.0\n");
    expect(result.json).toBeNull();
  });

  it("turns a refusal's error box into one line", async () => {
    const refusal = fixture("vault-sync.refusal-worker-running");
    const { cli } = fakeCli([
      { match: "vault sync", stderr: `2026-09-30 12:00:00 [INFO   ] starting\n${refusal.stderr}\n`, exit: 1 },
    ]);

    const result = await cli.sync();

    expect(result.ok).toBe(false);
    expect(result.exitCode).toBe(1);
    expect(result.error).toBe(
      "the ingest worker is running for this vault -- wait for it to finish (`artmind ingest job-status`), then re-run `vault sync`",
    );
  });

  it("keeps the JSON of a command that exits 1 with a report (vault doctor)", async () => {
    const doctor = fixture("vault-doctor");
    const { cli } = fakeCli([{ match: "vault doctor", stdout: JSON.stringify(doctor.json), exit: doctor.exit_code }]);

    const result = await cli.doctor();

    expect(result.ok).toBe(false);
    expect(result.json).toEqual(doctor.json);
  });

  it("times out, kills the child and says so", async () => {
    const { cli } = fakeCli([{ match: "vault status", stdout: "{}", delayMs: 5_000 }], { "vault status": 300 });

    const result = await cli.status();

    expect(result.timedOut).toBe(true);
    expect(result.ok).toBe(false);
    expect(result.error).toBe("timed out after 0.3 s");
  });

  it("reports a path that does not exist", async () => {
    const cli = new ArtmindCli({ path: "/nonexistent/artmind", cwd: tmpdir() });

    const result = await cli.status();

    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/^could not run \/nonexistent\/artmind: /);
  });

  it("flags output that is not JSON", async () => {
    const { cli } = fakeCli([{ match: "ingest pending", stdout: "not json" }]);

    const result = await cli.ingestPending();

    expect(result.ok).toBe(false);
    expect(result.error).toBe("artmind printed something that is not JSON");
  });

  it("runs writes one at a time, in the order asked", async () => {
    const { cli, lines } = fakeCli([
      { match: "vault sync", stdout: "{}", delayMs: 400 },
      { match: "ingest async", stdout: '{"job_id": null, "file_count": 0}' },
      { match: "ingest table2graph team --compact", stdout: '{"tables": []}' },
    ]);

    await Promise.all([cli.sync(), cli.ingestAsyncPending(), cli.tableProject("team")]);

    expect(lines()).toEqual([
      "start vault sync --compact",
      "end vault sync --compact",
      "start ingest async --pending --compact",
      "end ingest async --pending --compact",
      "start ingest table2graph team --compact",
      "end ingest table2graph team --compact",
    ]);
  });

  it("keeps the queue moving after a failed write", async () => {
    const { cli, lines } = fakeCli([
      { match: "vault sync", stderr: "╭─ Error ─╮\n│ boom │\n╰─╯", exit: 1, delayMs: 100 },
      { match: "vault resolve", stdout: "{}" },
    ]);

    const [sync, resolve] = await Promise.all([cli.sync(), cli.resolve(false)]);

    expect(sync.error).toBe("boom");
    expect(resolve.ok).toBe(true);
    expect(lines()).toEqual([
      "start vault sync --compact",
      "end vault sync --compact",
      "start vault resolve --compact",
      "end vault resolve --compact",
    ]);
  });

  it("runs reads concurrently, even while a write runs", async () => {
    const { cli, lines } = fakeCli([
      { match: "vault sync", stdout: "{}", delayMs: 600 },
      { match: "vault status", stdout: "{}", delayMs: 200 },
      { match: "ingest pending", stdout: "{}", delayMs: 200 },
    ]);

    await Promise.all([cli.sync(), cli.status(), cli.ingestPending()]);

    const log = lines();
    const ends = log.filter((l) => l.startsWith("end"));
    expect(log.slice(0, 3).every((l) => l.startsWith("start"))).toBe(true);
    expect(ends[ends.length - 1]).toBe("end vault sync --compact");
  });
});

describe("which commands write", () => {
  it.each([
    [["vault", "sync", "--compact"], true],
    [["vault", "resolve", "--compact"], true],
    [["vault", "resolve", "--dryRun", "--compact"], false],
    [["ingest", "async", "--pending", "--compact"], true],
    [["ingest", "table2graph", "team", "--compact"], true],
    [["ingest", "table2graph", "team", "--dryRun", "--compact"], false],
    [["ingest", "table2graph", "--pending", "--compact"], false],
    [["vault", "status", "--compact"], false],
    [["ingest", "pending", "--compact"], false],
    [["--version"], false],
  ])("%j -> %s", (args, write) => {
    expect(isWrite(args as string[])).toBe(write);
  });

  it("keys timeouts by the first two words", () => {
    expect(commandKey(["ingest", "job-status", "abc", "--compact"])).toBe("ingest job-status");
    expect(commandKey(["--version"])).toBe("--version");
  });
});

describe("errorText", () => {
  it.each([
    "vault-sync.refusal-worker-running",
    "vault-sync.refusal-merge-in-progress",
    "vault-sync.refusal-artmind-conflicts",
    "vault-sync.refusal-bookmark-unpulled",
    "vault-sync.refusal-bookmark-not-ancestor",
    "vault-sync.refusal-no-bookmark",
    "table2graph.dry-run-no-mapping",
  ])("unboxes %s into one line with no box characters", (name) => {
    const text = errorText(fixture(name).stderr!)!;
    expect(text).not.toMatch(/[│╭╰─]{2}|\n/);
    expect(text.length).toBeGreaterThan(20);
  });

  it("falls back to a plain Error: line, then the last line", () => {
    expect(errorText("log line\nError: plain click error\n")).toBe("plain click error");
    expect(errorText("Traceback...\nValueError: bad\n")).toBe("ValueError: bad");
    expect(errorText("")).toBeNull();
  });
});

describe("childEnv", () => {
  it("appends the usual install locations to a Dock app's bare PATH", () => {
    const env = childEnv({ PATH: "/usr/bin:/bin" }, "/Users/me");
    expect(env.PATH!.split(":")).toEqual([
      "/usr/bin",
      "/bin",
      "/Users/me/.local/bin",
      "/opt/homebrew/bin",
      "/usr/local/bin",
    ]);
    expect(env.NO_COLOR).toBe("1");
  });
});

describe("detectArtmind", () => {
  function deps(existing: string[], toolDir: string | null = null, toolBin: string | null = null): DetectDeps {
    return {
      home: "/Users/me",
      exists: (p) => existing.includes(p),
      uvToolDir: async (bin) => (bin ? toolBin : toolDir),
    };
  }

  it("uses the setting, and only the setting, when it is set", async () => {
    expect(await detectArtmind("/opt/artmind", deps(["/opt/artmind"]))).toEqual({
      path: "/opt/artmind",
      looked: ["/opt/artmind"],
    });
    expect(await detectArtmind("/opt/artmind", deps(["/Users/me/.local/bin/artmind"]))).toEqual({
      path: null,
      looked: ["/opt/artmind"],
    });
  });

  it("finds ~/.local/bin/artmind first", async () => {
    const found = await detectArtmind("", deps(["/Users/me/.local/bin/artmind"]));
    expect(found.path).toBe("/Users/me/.local/bin/artmind");
  });

  it("falls back to `uv tool dir --bin`, then the tool's own venv", async () => {
    expect((await detectArtmind("", deps(["/custom/bin/artmind"], null, "/custom/bin"))).path).toBe("/custom/bin/artmind");
    const venv = "/Users/me/.local/share/uv/tools/artmind9/bin/artmind";
    expect((await detectArtmind("", deps([venv], "/Users/me/.local/share/uv/tools"))).path).toBe(venv);
  });

  it("lists every place it looked when nothing is there", async () => {
    const found = await detectArtmind("", deps([], "/tools", "/Users/me/.local/bin"));
    expect(found).toEqual({ path: null, looked: ["/Users/me/.local/bin/artmind", "/tools/artmind9/bin/artmind"] });
  });
});
```

Run: `cd obsidian/artmind-obsidian && npx vitest run test/cli.test.ts`
Expected: the file fails to load — `Error: Cannot find module '../src/cli' imported from …/test/cli.test.ts` (`Test Files  1 failed (1)`, `Tests  no tests`).

- [ ] **Step 2: Implement**

**Create** `obsidian/artmind-obsidian/src/cli.ts`:

```ts
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/** What one child process did. `spawnError` is set when it never started
 * (ENOENT: the path is wrong). */
export interface ProcessOutcome {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  spawnError: string | null;
}

export interface SpawnOptions {
  cwd: string;
  timeoutMs: number;
  env: NodeJS.ProcessEnv;
  /** Writes run detached: if Obsidian quits mid-sync the child finishes on
   * its own (spec §8 -- `vault sync` only moves its bookmarks at the end). */
  detached: boolean;
}

export type Runner = (file: string, args: string[], options: SpawnOptions) => Promise<ProcessOutcome>;

/** One `artmind` run, as the rest of the plugin sees it. */
export interface CliResult {
  args: string[];
  exitCode: number | null;
  ok: boolean;
  /** stdout parsed as JSON; null when stdout is empty or not JSON. */
  json: unknown;
  stdout: string;
  stderr: string;
  /** The short reason on failure (rich-click's error box, unboxed), else null. */
  error: string | null;
  timedOut: boolean;
  startedAt: number;
  durationMs: number;
}

export const spawnRunner: Runner = (file, args, options) =>
  new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const child = spawn(file, args, {
      cwd: options.cwd,
      env: options.env,
      detached: options.detached,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, options.timeoutMs);
    child.stdout?.setEncoding("utf8").on("data", (chunk: string) => (stdout += chunk));
    child.stderr?.setEncoding("utf8").on("data", (chunk: string) => (stderr += chunk));
    const finish = (code: number | null, spawnError: string | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code, stdout, stderr, timedOut, spawnError });
    };
    child.on("error", (error) => finish(null, error.message));
    child.on("close", (code) => finish(code, null));
  });

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
  "ingest table2graph": 60 * 60_000,
};
export const DEFAULT_TIMEOUT_MS = 5 * 60_000;

const WRITE_COMMANDS = new Set(["vault sync", "vault resolve", "ingest async", "ingest table2graph"]);

export function commandKey(args: string[]): string {
  return args[0] === "--version" ? "--version" : args.slice(0, 2).join(" ");
}

/** Writes go through one queue, one at a time (spec §7): sync, ingest
 * submission, table2graph and resolve. Their dry runs, and
 * `table2graph --pending`, only read. */
export function isWrite(args: string[]): boolean {
  const key = commandKey(args);
  if (!WRITE_COMMANDS.has(key) || args.includes("--dryRun")) return false;
  return !(key === "ingest table2graph" && args.includes("--pending"));
}

/** The message inside rich-click's error box (what `artmind` prints on
 * stderr when a command refuses), on one line. Falls back to a plain
 * `Error: ...` line, then to the last line of stderr. Log lines above the
 * box are ignored. */
export function errorText(stderr: string): string | null {
  const lines = stderr.split(/\r?\n/);
  let start = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].startsWith("╭─ Error")) {
      start = i;
      break;
    }
  }
  if (start >= 0) {
    const body: string[] = [];
    for (const line of lines.slice(start + 1)) {
      if (line.startsWith("╰")) break;
      body.push(line.replace(/^│\s?/, "").replace(/\s*│\s*$/, ""));
    }
    const text = body.join(" ").replace(/\s+/g, " ").trim();
    return text || null;
  }
  const trimmed = lines.map((line) => line.trim()).filter(Boolean);
  const plain = trimmed.filter((line) => line.startsWith("Error:")).pop();
  if (plain) return plain.slice("Error:".length).trim();
  return trimmed.pop() ?? null;
}

/** Dock-launched apps get a bare PATH (spec §7). artmind itself runs `git`
 * and `uv` (the worker converts documents with `uv run docling`), so the
 * usual install locations are appended. */
export function childEnv(base: NodeJS.ProcessEnv, home: string): NodeJS.ProcessEnv {
  const extra = [join(home, ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"];
  const parts = (base.PATH ?? "").split(":").filter(Boolean);
  for (const dir of extra) if (!parts.includes(dir)) parts.push(dir);
  return { ...base, PATH: parts.join(":"), NO_COLOR: "1" };
}

export interface DetectDeps {
  home: string;
  exists(path: string): boolean;
  /** `uv tool dir` (bin=false) or `uv tool dir --bin` (bin=true), or null. */
  uvToolDir(bin: boolean): Promise<string | null>;
}

export interface Detected {
  path: string | null;
  looked: string[];
}

/** Where `artmind` is: the setting when set (and only there), else
 * `~/.local/bin/artmind`, else `uv tool dir --bin`, else the tool's own
 * venv under `uv tool dir`. `looked` lists every place tried, for the
 * "artmind not found" panel. */
export async function detectArtmind(setting: string, deps: DetectDeps): Promise<Detected> {
  const looked: string[] = [];
  const configured = setting.trim();
  if (configured) {
    looked.push(configured);
    return { path: deps.exists(configured) ? configured : null, looked };
  }
  const candidates: Array<() => Promise<string | null>> = [
    async () => join(deps.home, ".local", "bin", "artmind"),
    async () => {
      const bin = await deps.uvToolDir(true);
      return bin ? join(bin, "artmind") : null;
    },
    async () => {
      const tools = await deps.uvToolDir(false);
      return tools ? join(tools, "artmind9", "bin", "artmind") : null;
    },
  ];
  for (const candidate of candidates) {
    const path = await candidate();
    if (!path || looked.includes(path)) continue;
    looked.push(path);
    if (deps.exists(path)) return { path, looked };
  }
  return { path: null, looked };
}

export function defaultDetectDeps(runner: Runner = spawnRunner): DetectDeps {
  const home = homedir();
  const env = childEnv(process.env, home);
  return {
    home,
    exists: existsSync,
    async uvToolDir(bin) {
      const outcome = await runner("uv", bin ? ["tool", "dir", "--bin"] : ["tool", "dir"], {
        cwd: home,
        timeoutMs: 10_000,
        env,
        detached: false,
      });
      const out = outcome.stdout.trim();
      return outcome.code === 0 && out ? out : null;
    },
  };
}

export interface ArtmindCliOptions {
  path: string;
  /** The vault root: every command anchors to the vault it runs in. */
  cwd: string;
  runner?: Runner;
  env?: NodeJS.ProcessEnv;
  timeouts?: Record<string, number>;
  /** Called with every result, for the Activity list. */
  onResult?: (result: CliResult) => void;
}

/** Runs `artmind <args> --compact` in the vault and parses its JSON. */
export class ArtmindCli {
  private writeTail: Promise<unknown> = Promise.resolve();
  private options: ArtmindCliOptions;

  constructor(options: ArtmindCliOptions) {
    this.options = options;
  }

  get path(): string {
    return this.options.path;
  }

  setPath(path: string): void {
    this.options.path = path;
  }

  /** `--compact` is added unless the args carry it, or it is `--version`.
   * Reads start at once; writes wait for every earlier write to finish. */
  run(args: string[], opts: { timeoutMs?: number } = {}): Promise<CliResult> {
    const full = args[0] === "--version" || args.includes("--compact") ? args : [...args, "--compact"];
    const timeoutMs =
      opts.timeoutMs ?? this.options.timeouts?.[commandKey(full)] ?? TIMEOUTS_MS[commandKey(full)] ?? DEFAULT_TIMEOUT_MS;
    const task = () => this.exec(full, timeoutMs);
    if (!isWrite(full)) return task();
    const result = this.writeTail.then(task, task);
    this.writeTail = result.catch(() => undefined);
    return result;
  }

  private async exec(args: string[], timeoutMs: number): Promise<CliResult> {
    const runner = this.options.runner ?? spawnRunner;
    const startedAt = Date.now();
    const outcome = await runner(this.options.path, args, {
      cwd: this.options.cwd,
      timeoutMs,
      env: this.options.env ?? childEnv(process.env, homedir()),
      detached: isWrite(args),
    });
    const stdout = outcome.stdout.trim();
    let json: unknown = null;
    let parseError: string | null = null;
    if (stdout && args[0] !== "--version") {
      try {
        json = JSON.parse(stdout);
      } catch {
        parseError = "artmind printed something that is not JSON";
      }
    }
    const ok = outcome.code === 0 && !outcome.timedOut && !outcome.spawnError && !parseError;
    let error: string | null = null;
    if (outcome.spawnError) error = `could not run ${this.options.path}: ${outcome.spawnError}`;
    else if (outcome.timedOut) error = `timed out after ${timeoutMs / 1000} s`;
    else if (outcome.code !== 0) error = errorText(outcome.stderr) ?? `exited with code ${outcome.code}`;
    else if (parseError) error = parseError;
    const result: CliResult = {
      args,
      exitCode: outcome.code,
      ok,
      json,
      stdout: outcome.stdout,
      stderr: outcome.stderr,
      error,
      timedOut: outcome.timedOut,
      startedAt,
      durationMs: Date.now() - startedAt,
    };
    this.options.onResult?.(result);
    return result;
  }

  version() {
    return this.run(["--version"]);
  }
  status() {
    return this.run(["vault", "status"]);
  }
  sync() {
    return this.run(["vault", "sync"]);
  }
  resolve(dryRun: boolean) {
    return this.run(dryRun ? ["vault", "resolve", "--dryRun"] : ["vault", "resolve"]);
  }
  doctor() {
    return this.run(["vault", "doctor"]);
  }
  ingestPending() {
    return this.run(["ingest", "pending"]);
  }
  ingestAsyncPending() {
    return this.run(["ingest", "async", "--pending"]);
  }
  jobStatus(jobId: string) {
    return this.run(["ingest", "job-status", jobId]);
  }
  jobResults(jobId: string) {
    return this.run(["ingest", "job-results", jobId]);
  }
  jobsActive() {
    return this.run(["ingest", "jobs-active"]);
  }
  tablesPending() {
    return this.run(["ingest", "table2graph", "--pending"]);
  }
  tableDryRun(table: string) {
    return this.run(["ingest", "table2graph", table, "--dryRun"]);
  }
  tableProject(table: string) {
    return this.run(["ingest", "table2graph", table]);
  }
}
```

- [ ] **Step 3: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  2 passed (2); Tests  38 passed (38)`; the build succeeds.

- [ ] **Step 4: Commit**

```bash
git add obsidian/artmind-obsidian/src/cli.ts obsidian/artmind-obsidian/test/fake-artmind.mjs obsidian/artmind-obsidian/test/fixtures.ts obsidian/artmind-obsidian/test/cli.test.ts
git commit -m "feat(obsidian): ArtmindCli -- compact JSON, error boxes, timeouts, a write queue, path detection" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `VaultState` — the pure function, table-driven on the real fixtures (spec §3.1, §3.2, §4.2, §8)

`vaultState(inputs)` returns every state that applies, sorted by the spec's priority (conflict > error > job > behind > tables → graph > to ingest > in sync), the primary one, the secondary indicators, why each panel action is blocked, and the ingest warning when Neo4j is unreachable. The labels, tones and click actions are the spec's table. `classifyRefusal` maps a `vault sync` error message to `worker_running` ("Ingest in progress — will sync when it finishes"), `resolve_first`, `pull_first`, `no_bookmark` (with the guide's bootstrap step, never run by the plugin) or `other`.

The tests build their inputs from the fixtures: every state alone, every pair of states (the higher one wins, both listed), all six at once, the blocked reasons, the secondary indicators, and every refusal fixture through `errorText` then `classifyRefusal`.

**Files:**
- Create: `obsidian/artmind-obsidian/src/types.ts`, `src/state.ts`, `test/state.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/state.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { errorText } from "../src/cli";
import { type ArtmindProblem, type StateInputs, type StateKind, classifyRefusal, vaultState } from "../src/state";
import type { IngestPending } from "../src/types";
import { fixture } from "./fixtures";

const NOTHING_PENDING: IngestPending = { notes: [], binaries: [], tables: [], errors: [] };

/** In sync, nothing waiting: every case changes one thing from here. */
function inputs(overrides: Partial<StateInputs> = {}): StateInputs {
  return {
    status: fixture("vault-status.in-sync").json,
    activeJob: null,
    pending: NOTHING_PENDING,
    tablesPending: [],
    git: { ahead: 0, unsharedArtmindChanges: 0 },
    problem: null,
    ...overrides,
  };
}

/** One input per state, each from a real fixture where the CLI has one. */
const TRIGGERS: Record<Exclude<StateKind, "in_sync">, Partial<StateInputs>> = {
  conflict: { status: fixture("vault-status.merge-conflict").json },
  error: { status: fixture("vault-status.neo4j-unreachable").json },
  job: { activeJob: fixture("ingest-job-status.running").json },
  behind: { status: fixture("vault-status.behind").json },
  tables: { tablesPending: fixture("table2graph.pending").json },
  ingest: { pending: fixture("ingest-pending").json },
};

describe("vaultState: each state alone (spec §3.1)", () => {
  it.each([
    ["in_sync", {}, "◉ artmind", "grey", { type: "openPanel", section: "status" }],
    ["conflict", TRIGGERS.conflict, "⚠ resolve artmind conflicts", "red", { type: "openResolve" }],
    ["error", TRIGGERS.error, "⚠ artmind", "red", { type: "openPanel", section: "error" }],
    ["job", TRIGGERS.job, "◌ ingesting 2/5", "spinner", { type: "openPanel", section: "job" }],
    ["behind", TRIGGERS.behind, "◉ 2 docs · 1 table behind", "amber", { type: "sync" }],
    ["tables", TRIGGERS.tables, "◉ 1 table → graph", "blue", { type: "reviewTables" }],
    ["ingest", TRIGGERS.ingest, "◉ 4 to ingest", "blue", { type: "ingest" }],
  ] as const)("%s", (kind, overrides, label, tone, action) => {
    const state = vaultState(inputs(overrides as Partial<StateInputs>));

    expect(state.primary.kind).toBe(kind);
    expect(state.primary.label).toBe(label);
    expect(state.primary.tone).toBe(tone);
    expect(state.primary.action).toEqual(action);
  });

  it("a sync that was never set up asks for the bootstrap step", () => {
    const state = vaultState(inputs({ status: fixture("vault-status.no-bookmark").json }));

    expect(state.primary.label).toBe("◉ sync not set up");
    expect(state.primary.action).toEqual({ type: "openPanel", section: "bootstrap" });
    expect(state.blocked.sync).toBe("This laptop hasn't synced this vault before");
  });

  it("a bookmark not in this clone's history asks for a pull", () => {
    const status = structuredClone(fixture("vault-status.behind").json);
    status.sync.stores.graph.state = "not_ancestor";

    const state = vaultState(inputs({ status }));

    expect(state.primary.label).toBe("◉ pull first");
    expect(state.primary.action).toEqual({ type: "pull" });
  });

  it("artmind missing or too old is the error state, and blocks every action", () => {
    const problems: ArtmindProblem[] = [
      { kind: "missing", looked: ["/Users/me/.local/bin/artmind"] },
      { kind: "too_old", found: "0.8.0", need: "0.9.0" },
    ];
    for (const problem of problems) {
      const state = vaultState(inputs({ problem }));
      expect(state.primary.kind).toBe("error");
      expect(Object.values(state.blocked).every((reason) => reason !== null)).toBe(true);
    }
  });

  it("ambiguous tables do not count as waiting", () => {
    const tablesPending = [{ table: "t", domain: "d", mapping: "a.yaml, b.yaml", reason: "ambiguous" }];
    expect(vaultState(inputs({ tablesPending })).primary.kind).toBe("in_sync");
  });
});

describe("vaultState: priority clashes", () => {
  const order = Object.keys(TRIGGERS) as Array<keyof typeof TRIGGERS>;
  const pairs = order.flatMap((higher, i) => order.slice(i + 1).map((lower) => [higher, lower] as const));

  /** In-sync inputs with each named state's fixture facts added. */
  function combine(kinds: Array<keyof typeof TRIGGERS>): StateInputs {
    const status = structuredClone(fixture("vault-status.in-sync").json);
    const out = inputs({ status });
    for (const kind of kinds) {
      if (kind === "conflict") {
        const sync = fixture("vault-status.merge-conflict").json.sync;
        status.sync.operation_in_progress = sync.operation_in_progress;
        status.sync.unresolved_conflicts = sync.unresolved_conflicts;
      } else if (kind === "error") {
        status.sync.graph_error = fixture("vault-status.neo4j-unreachable").json.sync.graph_error;
      } else if (kind === "behind") {
        status.sync.stores = structuredClone(fixture("vault-status.behind").json.sync.stores);
      } else {
        Object.assign(out, TRIGGERS[kind]);
      }
    }
    return out;
  }

  it.each(pairs)("%s beats %s", (higher, lower) => {
    const state = vaultState(combine([higher, lower]));

    expect(state.primary.kind).toBe(higher);
    expect(state.states.map((s) => s.kind)).toEqual([higher, lower]);
  });

  it("lists every applying state in priority order for the panel", () => {
    const status = structuredClone(fixture("vault-status.behind").json);
    status.sync.unresolved_conflicts = [".artmind/same_as.yaml"];
    status.sync.operation_in_progress = "a merge";

    const state = vaultState(
      inputs({ ...TRIGGERS.job, ...TRIGGERS.tables, ...TRIGGERS.ingest, status, problem: { kind: "failed", command: "x", message: "y" } }),
    );

    expect(state.states.map((s) => s.kind)).toEqual(["conflict", "error", "job", "behind", "tables", "ingest"]);
  });
});

describe("vaultState: secondary indicators and blocked actions", () => {
  it("shows commits to push and artmind changes to share", () => {
    const state = vaultState(inputs({ git: { ahead: 3, unsharedArtmindChanges: 2 } }));

    expect(state.secondary).toEqual(["↑ 3 to push", "✎ artmind changes to share"]);
  });

  it("blocks sync while a job runs or a merge is in progress; resolve without a merge", () => {
    expect(vaultState(inputs(TRIGGERS.job)).blocked.sync).toBe("An ingest job is running — sync after it finishes");
    const merge = vaultState(inputs(TRIGGERS.conflict));
    expect(merge.blocked.sync).toBe("A merge is in progress — finish it first");
    expect(merge.blocked.resolve).toBeNull();
    expect(vaultState(inputs()).blocked.resolve).toBe("No merge in progress");
  });

  it("with Neo4j unreachable, blocks sync and tables but lets ingest run with a warning (spec §8)", () => {
    const state = vaultState(inputs({ ...TRIGGERS.error, ...TRIGGERS.ingest, ...TRIGGERS.tables }));

    expect(state.neo4jUnreachable).toBe(true);
    expect(state.primary.detail).toContain("Neo4j unreachable (neo4j://127.0.0.1:7687)");
    expect(state.blocked.sync).toBe("Neo4j unreachable");
    expect(state.blocked.tables).toBe("Neo4j unreachable");
    expect(state.blocked.ingest).toBeNull();
    expect(state.warnings.ingest).toMatch(/graph write will fail/);
  });

  it("says why Ingest has nothing to do", () => {
    expect(vaultState(inputs()).blocked.ingest).toBe("Nothing new or changed to ingest");
  });
});

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

  it("shows the bootstrap step for a first sync, and never runs it", () => {
    expect(classifyRefusal(errorText(fixture("vault-sync.refusal-no-bookmark").stderr!)!).text).toContain(
      "artmind vault sync --bootstrapEmpty",
    );
  });
});
```

Run: `cd obsidian/artmind-obsidian && npx vitest run test/state.test.ts`
Expected: the file fails to load — `Error: Cannot find module '../src/state' imported from …/test/state.test.ts` (`Test Files  1 failed (1)`, `Tests  no tests`).

- [ ] **Step 2: The JSON types**

**Create** `obsidian/artmind-obsidian/src/types.ts`:

```ts
/** The `--compact` JSON the plugin reads, as the fixtures under
 * test/fixtures/ capture it (regenerated from the real CLI by
 * test/test_plugin_fixtures.py in the artmind repo). */

export type StoreName = "graph" | "structured";

export type StoreState = "current" | "behind" | "no_bookmark" | "not_ancestor" | "error" | "no_commits";

export interface StoreReport {
  bookmark: string | null;
  state: StoreState;
  docs?: number;
  tables?: Array<[string, string]>;
  curation?: number;
  same_as_groups?: number;
  detail?: string | null;
}

/** `artmind vault status --compact`. */
export interface VaultStatus {
  vault: string;
  manifest: string | null;
  graph: { uri: string; database: string };
  sync: {
    head: string | null;
    vault_id: string | null;
    operation_in_progress: string | null;
    unresolved_conflicts: string[];
    legacy_cursor: string | null;
    graph_error: string | null;
    stores: Partial<Record<StoreName, StoreReport>>;
    message: string | null;
  };
}

/** `artmind vault sync --compact`, on success. */
export interface SyncResult {
  replayed?: number;
  retracted?: number;
  regenerated_tables?: number;
  curated_tables?: number;
  curation?: Record<string, Record<string, number>>;
  same_as_groups?: number;
  graph_bookmark?: string;
  structured_bookmark?: string;
}

export interface JobFile {
  filename: string;
  status: string;
  current_step: string | null;
  error_message?: string | null;
}

/** `artmind ingest job-status <id> --compact` (and each `jobs-active` row). */
export interface JobStatus {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed" | string;
  file_count: number;
  processed_count: number;
  files: JobFile[];
  error_message?: string | null;
}

/** `artmind ingest job-results <id> --compact`. */
export interface JobResults {
  job_id: string;
  status: string;
  file_count: number;
  files: Array<{ filename: string; status: string; error_message: string | null }>;
}

/** `artmind ingest async [--pending] --compact`. */
export interface Submitted {
  job_id: string | null;
  file_count: number;
}

export interface PendingEntry {
  path: string;
  reason: "new" | "changed";
}

/** `artmind ingest pending --compact`. */
export interface IngestPending {
  notes: PendingEntry[];
  binaries: PendingEntry[];
  tables: PendingEntry[];
  errors: Array<{ path: string; error: string }>;
}

/** One row of `artmind ingest table2graph --pending --compact`. */
export interface TablePending {
  table: string;
  domain: string;
  mapping: string | null;
  reason: "missing" | "table_refreshed" | "mapping_changed" | "ambiguous" | string;
}

/** One table of `artmind ingest table2graph <t> --dryRun --compact`. */
export interface TableReport {
  table: string;
  domain: string;
  mapping: string | null;
  rows: number;
  entities: Record<string, { class: string; built: number; kept: number; skipped: Record<string, number> }>;
  lookups: Record<
    string,
    {
      resolved: number;
      stubbed: number;
      skipped_unresolved?: number;
      skipped_ambiguous?: number;
      empty?: number;
      unresolved_values: string[];
      ambiguous_values: string[];
    }
  >;
  relationships?: Record<string, { written: number; skipped: Record<string, number> }>;
  warnings: string[];
  dry_run?: boolean;
}

/** `artmind vault resolve [--dryRun] --compact`. */
export interface ResolveReport {
  head: string;
  merge_head: string;
  resolved: Array<{ unit: string; kind: string; paths: string[]; side: "ours" | "theirs"; rule: string }>;
  pending: Array<{ unit: string; kind: string; paths: string[]; why: string }>;
  reported: Array<{ path: string; why: string }>;
  remaining: number;
  dry_run?: boolean;
}

/** `artmind vault doctor --compact` (exit 1 when a check fails, JSON either way). */
export interface DoctorReport {
  vault: string;
  ok: boolean;
  checks: Array<{ name: string; status: "ok" | "warn" | "fail" | "unknown"; detail: string; fix: string | null }>;
}
```

- [ ] **Step 3: Implement**

**Create** `obsidian/artmind-obsidian/src/state.ts`:

```ts
import type { IngestPending, JobStatus, StoreReport, TablePending, VaultStatus } from "./types";

/** Why artmind itself cannot be used (spec §8). */
export type ArtmindProblem =
  | { kind: "missing"; looked: string[] }
  | { kind: "too_old"; found: string; need: string }
  | { kind: "failed"; command: string; message: string };

/** Everything `vaultState` decides from. All of it is read, never written. */
export interface StateInputs {
  /** `vault status --compact`, or null before the first read. */
  status: VaultStatus | null;
  /** The ingest job being tracked, while it is queued or processing. */
  activeJob: JobStatus | null;
  /** `ingest pending --compact`. */
  pending: IngestPending | null;
  /** `ingest table2graph --pending --compact`. */
  tablesPending: TablePending[] | null;
  /** Read-only git facts. `unsharedArtmindChanges` already excludes what
   * the plugin itself wrote in the last 30 s (spec §4.5). */
  git: { ahead: number | null; unsharedArtmindChanges: number };
  problem: ArtmindProblem | null;
}

export type StateKind = "conflict" | "error" | "job" | "behind" | "tables" | "ingest" | "in_sync";
export type Tone = "red" | "amber" | "blue" | "grey" | "spinner";
export type PanelSection = "status" | "error" | "job" | "tables" | "doctor" | "bootstrap";

export type Action =
  | { type: "openResolve" }
  | { type: "openPanel"; section: PanelSection }
  | { type: "sync" }
  | { type: "pull" }
  | { type: "reviewTables" }
  | { type: "ingest" };

export interface StateItem {
  kind: StateKind;
  label: string;
  tone: Tone;
  action: Action;
  /** One line for the panel. */
  detail: string;
}

export type ActionName = "sync" | "ingest" | "resolve" | "doctor" | "tables";

export interface VaultStateResult {
  /** Every state that applies, highest priority first (spec §3.1). */
  states: StateItem[];
  /** What the status bar shows. */
  primary: StateItem;
  /** `↑ 3 to push`, `✎ artmind changes to share`. */
  secondary: string[];
  /** Why each action cannot run right now, or null when it can (spec §3.2). */
  blocked: Record<ActionName, string | null>;
  /** Shown beside Ingest when it may run but its graph write will fail. */
  warnings: Partial<Record<ActionName, string>>;
  neo4jUnreachable: boolean;
}

const PRIORITY: StateKind[] = ["conflict", "error", "job", "behind", "tables", "ingest", "in_sync"];

export const IN_SYNC: StateItem = {
  kind: "in_sync",
  label: "◉ artmind",
  tone: "grey",
  action: { type: "openPanel", section: "status" },
  detail: "Every store is at HEAD and nothing is waiting.",
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

function problemText(problem: ArtmindProblem): string {
  switch (problem.kind) {
    case "missing":
      return `artmind not found (looked in ${problem.looked.join(", ") || "nowhere"})`;
    case "too_old":
      return `artmind ${problem.found} is too old: this plugin needs ${problem.need} or newer`;
    case "failed":
      return `\`artmind ${problem.command}\` failed: ${problem.message}`;
  }
}

/** The whole of the plugin's judgement, as a pure function: which states
 * apply (spec §3.1, highest priority first), which one the status bar
 * shows, what each action is blocked by (§3.2, §4.2) and the secondary
 * indicators. The views render this; they decide nothing. */
export function vaultState(inputs: StateInputs): VaultStateResult {
  const states: StateItem[] = [];
  const sync = inputs.status?.sync;
  const stores = sync?.stores ?? {};
  const graphError = sync?.graph_error ?? null;
  const neo4jUnreachable = Boolean(graphError && graphError.startsWith("graph unreachable"));
  const merge = sync?.operation_in_progress ?? null;
  const conflicts = sync?.unresolved_conflicts ?? [];
  const jobRunning = Boolean(inputs.activeJob && ["queued", "processing"].includes(inputs.activeJob.status));
  const storeStates = Object.values(stores).map((report) => report?.state);

  if (conflicts.length) {
    states.push({
      kind: "conflict",
      label: "⚠ resolve artmind conflicts",
      tone: "red",
      action: { type: "openResolve" },
      detail: `${plural(conflicts.length, "file")} under .artmind/ ${conflicts.length === 1 ? "has" : "have"} unresolved conflicts.`,
    });
  }

  const errors: string[] = [];
  if (inputs.problem) errors.push(problemText(inputs.problem));
  if (neo4jUnreachable) errors.push(`Neo4j unreachable (${inputs.status?.graph.uri || "no URI configured"})`);
  else if (graphError) errors.push(graphError);
  for (const [name, report] of Object.entries(stores)) {
    if (report?.state === "error") errors.push(`the ${name} store's sync would fail: ${report.detail ?? "unknown error"}`);
  }
  if (errors.length) {
    states.push({
      kind: "error",
      label: "⚠ artmind",
      tone: "red",
      action: { type: "openPanel", section: "error" },
      detail: errors.join("; "),
    });
  }

  if (jobRunning && inputs.activeJob) {
    const job = inputs.activeJob;
    states.push({
      kind: "job",
      label: `◌ ingesting ${job.processed_count}/${job.file_count}`,
      tone: "spinner",
      action: { type: "openPanel", section: "job" },
      detail: `Ingest job ${job.job_id}: ${job.processed_count} of ${plural(job.file_count, "file")} done.`,
    });
  }

  if (storeStates.includes("not_ancestor")) {
    states.push({
      kind: "behind",
      label: "◉ pull first",
      tone: "amber",
      action: { type: "pull" },
      detail: "A store's sync bookmark is ahead of this clone: pull from GitHub, then sync.",
    });
  } else if (storeStates.includes("no_bookmark")) {
    states.push({
      kind: "behind",
      label: "◉ sync not set up",
      tone: "amber",
      action: { type: "openPanel", section: "bootstrap" },
      detail: "This laptop hasn't synced this vault before.",
    });
  } else if (storeStates.includes("behind")) {
    const parts = behindParts(stores);
    states.push({
      kind: "behind",
      label: `◉ ${parts.join(" · ") || "stores"} behind`,
      tone: "amber",
      action: { type: "sync" },
      detail: `The other laptop's changes are not applied here yet: ${parts.join(", ")}.`,
    });
  }

  const tables = (inputs.tablesPending ?? []).filter((t) => t.reason !== "ambiguous");
  if (tables.length) {
    states.push({
      kind: "tables",
      label: `◉ ${plural(tables.length, "table")} → graph`,
      tone: "blue",
      action: { type: "reviewTables" },
      detail: tables.map((t) => `${t.table} (${t.reason.replace("_", " ")})`).join(", "),
    });
  }

  const pending = inputs.pending;
  const toIngest = pending ? pending.notes.length + pending.binaries.length + pending.tables.length : 0;
  if (toIngest) {
    states.push({
      kind: "ingest",
      label: `◉ ${toIngest} to ingest`,
      tone: "blue",
      action: { type: "ingest" },
      detail: `${plural(pending!.notes.length, "note")}, ${plural(pending!.binaries.length, "binary", "binaries")}, ${plural(pending!.tables.length, "table")} new or changed.`,
    });
  }

  states.sort((a, b) => PRIORITY.indexOf(a.kind) - PRIORITY.indexOf(b.kind));
  const primary = states[0] ?? IN_SYNC;

  const secondary: string[] = [];
  if (inputs.git.ahead) secondary.push(`↑ ${inputs.git.ahead} to push`);
  if (inputs.git.unsharedArtmindChanges) secondary.push("✎ artmind changes to share");

  const cannotRun = inputs.problem ? problemText(inputs.problem) : null;
  const blocked: Record<ActionName, string | null> = {
    sync:
      cannotRun ??
      (jobRunning ? "An ingest job is running — sync after it finishes" : null) ??
      (merge ? `${merge[0].toUpperCase()}${merge.slice(1)} is in progress — finish it first` : null) ??
      (conflicts.length ? "Resolve the artmind conflicts first" : null) ??
      (neo4jUnreachable ? "Neo4j unreachable" : null) ??
      (storeStates.includes("no_bookmark") ? "This laptop hasn't synced this vault before" : null),
    ingest:
      cannotRun ??
      (jobRunning ? "An ingest job is already running" : null) ??
      (merge ? `${merge[0].toUpperCase()}${merge.slice(1)} is in progress — finish it first` : null) ??
      (toIngest ? null : "Nothing new or changed to ingest"),
    resolve: cannotRun ?? (merge === "a merge" ? null : "No merge in progress"),
    doctor: cannotRun,
    tables: cannotRun ?? (neo4jUnreachable ? "Neo4j unreachable" : null) ?? (tables.length ? null : "No tables waiting"),
  };
  const warnings: Partial<Record<ActionName, string>> = {};
  if (neo4jUnreachable) warnings.ingest = "Neo4j is unreachable: extraction will run, the graph write will fail";

  return { states, primary, secondary, blocked, warnings, neo4jUnreachable };
}

/** What a `vault sync` refusal becomes (spec §4.2): never a raw error. */
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
  if (/ingest worker is running/.test(text)) {
    return { kind: "worker_running", text: "Ingest in progress — will sync when it finishes" };
  }
  if (/is in progress in this vault|unresolved conflicts in/.test(text)) {
    return { kind: "resolve_first", text: "Resolve first" };
  }
  if (/is not a commit in this clone|is not an ancestor of HEAD/.test(text)) {
    return { kind: "pull_first", text: "Pull first" };
  }
  if (/bookmark recorded yet/.test(text)) {
    return { kind: "no_bookmark", text: `This laptop hasn't synced this vault before. ${BOOTSTRAP_STEP}` };
  }
  return { kind: "other", text: message };
}
```

- [ ] **Step 4: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  3 passed (3); Tests  77 passed (77)`; the build succeeds.

- [ ] **Step 5: Commit**

```bash
git add obsidian/artmind-obsidian/src/types.ts obsidian/artmind-obsidian/src/state.ts obsidian/artmind-obsidian/test/state.test.ts
git commit -m "feat(obsidian): VaultState -- states, priority, blocked actions, refusals" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `GitReader` — the only git the plugin runs, and it only reads (spec P5, §4.1, §4.5)

`head()` (`git rev-parse HEAD`), `ahead()` (`git rev-list --count @{upstream}..HEAD`, null without an upstream) and `artmindChanges()` (`git status --porcelain -z -- .artmind`, names with spaces kept whole, a rename's second path skipped). `GIT_READS` lists the three commands so a test can assert none is a write. The tests use a real clone of a bare repo in a temp dir.

**Files:**
- Create: `obsidian/artmind-obsidian/src/git.ts`, `test/git.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/git.test.ts`:

```ts
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { GIT_READS, GitReader } from "../src/git";

function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
}

/** A clone of a bare "GitHub" repo, as Obsidian Git leaves one. */
function repo(): { clone: string; remote: string } {
  const root = mkdtempSync(join(tmpdir(), "artmind-git-"));
  const remote = join(root, "remote.git");
  const clone = join(root, "clone");
  git(root, "init", "-q", "--bare", "-b", "main", remote);
  git(root, "clone", "-q", remote, clone);
  git(clone, "config", "user.email", "t@example.com");
  git(clone, "config", "user.name", "T");
  writeFileSync(join(clone, "note.md"), "hello\n");
  git(clone, "add", "-A");
  git(clone, "commit", "-qm", "first");
  git(clone, "push", "-q", "origin", "main");
  return { clone, remote };
}

describe("GitReader", () => {
  it("reads HEAD", async () => {
    const { clone } = repo();
    expect(await new GitReader(clone).head()).toBe(git(clone, "rev-parse", "HEAD"));
  });

  it("returns null for HEAD in a repo with no commits", async () => {
    const empty = mkdtempSync(join(tmpdir(), "artmind-git-empty-"));
    git(empty, "init", "-q");
    expect(await new GitReader(empty).head()).toBeNull();
  });

  it("counts commits not yet pushed", async () => {
    const { clone } = repo();
    const reader = new GitReader(clone);
    expect(await reader.ahead()).toBe(0);
    writeFileSync(join(clone, "b.md"), "b\n");
    git(clone, "add", "-A");
    git(clone, "commit", "-qm", "second");
    expect(await reader.ahead()).toBe(1);
  });

  it("has no count without an upstream", async () => {
    const empty = mkdtempSync(join(tmpdir(), "artmind-git-noup-"));
    git(empty, "init", "-q");
    expect(await new GitReader(empty).ahead()).toBeNull();
  });

  it("lists uncommitted changes under .artmind only, names with spaces whole", async () => {
    const { clone } = repo();
    mkdirSync(join(clone, ".artmind", "data"), { recursive: true });
    writeFileSync(join(clone, ".artmind", "data", "a record.json"), "{}");
    writeFileSync(join(clone, "outside.md"), "not artmind\n");

    expect(await new GitReader(clone).artmindChanges()).toEqual([".artmind/"]);
    git(clone, "add", ".artmind");
    expect(await new GitReader(clone).artmindChanges()).toEqual([".artmind/data/a record.json"]);
  });

  it("only ever reads (spec P5)", () => {
    const writes = ["commit", "push", "pull", "add", "merge", "checkout", "reset", "fetch", "update-ref"];
    for (const args of GIT_READS) expect(writes).not.toContain(args[0]);
  });
});
```

Run: `cd obsidian/artmind-obsidian && npx vitest run test/git.test.ts`
Expected: the file fails to load — `Error: Cannot find module '../src/git' imported from …/test/git.test.ts` (`Test Files  1 failed (1)`, `Tests  no tests`).

- [ ] **Step 2: Implement**

**Create** `obsidian/artmind-obsidian/src/git.ts`:

```ts
import { homedir } from "node:os";
import { childEnv, type Runner, spawnRunner } from "./cli";

/** The only git the plugin runs, and all of it reads (spec P5): HEAD, the
 * commits not yet pushed, and uncommitted changes under `.artmind/`.
 * Writing to git is Obsidian Git's job (`ObsidianGitBridge`). */
export const GIT_READS = [
  ["rev-parse", "HEAD"],
  ["rev-list", "--count", "@{upstream}..HEAD"],
  ["status", "--porcelain", "-z", "--", ".artmind"],
] as const;

export class GitReader {
  private cwd: string;
  private runner: Runner;
  private env: NodeJS.ProcessEnv;

  constructor(cwd: string, runner: Runner = spawnRunner, env: NodeJS.ProcessEnv = childEnv(process.env, homedir())) {
    this.cwd = cwd;
    this.runner = runner;
    this.env = env;
  }

  private async git(args: readonly string[]): Promise<string | null> {
    const outcome = await this.runner("git", [...args], {
      cwd: this.cwd,
      timeoutMs: 15_000,
      env: this.env,
      detached: false,
    });
    return outcome.code === 0 ? outcome.stdout : null;
  }

  /** `git rev-parse HEAD`, or null (no commits, not a repo). */
  async head(): Promise<string | null> {
    const out = await this.git(GIT_READS[0]);
    return out ? out.trim() : null;
  }

  /** Local commits not yet pushed, or null when there is no upstream. */
  async ahead(): Promise<number | null> {
    const out = await this.git(GIT_READS[1]);
    if (out === null) return null;
    const n = Number.parseInt(out.trim(), 10);
    return Number.isNaN(n) ? null : n;
  }

  /** Paths under `.artmind/` with uncommitted changes (`-z`: a name with a
   * space stays whole; a rename's second, original path is skipped). */
  async artmindChanges(): Promise<string[]> {
    const out = await this.git(GIT_READS[2]);
    if (!out) return [];
    const fields = out.split("\0");
    const paths: string[] = [];
    for (let i = 0; i < fields.length; i++) {
      const field = fields[i];
      if (field.length < 4) continue;
      paths.push(field.slice(3));
      if (field[0] === "R" || field[0] === "C") i++;
    }
    return paths;
  }
}
```

- [ ] **Step 3: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  4 passed (4); Tests  83 passed (83)`; the build succeeds.

- [ ] **Step 4: Commit**

```bash
git add obsidian/artmind-obsidian/src/git.ts obsidian/artmind-obsidian/test/git.test.ts
git commit -m "feat(obsidian): GitReader -- HEAD, commits to push, .artmind changes (reads only)" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `ObsidianGitBridge` — Pull, Commit-and-sync, Commit (spec §4.5, §8)

Looks the three actions up in `app.commands.commands` on every use (Obsidian Git may be enabled after this plugin), known ids first, then an `obsidian-git:*` command with a known name (see "Obsidian Git's command ids" above). `run` returns false and runs nothing when an action is missing; `instruction` and `warning` give the text that replaces a missing button. The tests stub `app.commands` for the current ids, renamed ids, another plugin's same-named command, a partial set, and no Obsidian Git at all.

**Files:**
- Create: `obsidian/artmind-obsidian/src/bridge.ts`, `test/bridge.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/bridge.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { type CommandsLike, INSTRUCTIONS, KNOWN_IDS, ObsidianGitBridge } from "../src/bridge";

/** `app.commands` as Obsidian holds it, recording what ran. */
function commands(entries: Array<[string, string]>): CommandsLike & { ran: string[] } {
  const ran: string[] = [];
  return {
    ran,
    commands: Object.fromEntries(entries.map(([id, name]) => [id, { id, name }])),
    executeCommandById(id: string) {
      ran.push(id);
      return true;
    },
  };
}

/** What obsidian-git 2.40.0 registers for the three actions. */
const CURRENT: Array<[string, string]> = [
  ["obsidian-git:pull", "Git: Pull"],
  ["obsidian-git:push", "Git: Commit-and-sync"],
  ["obsidian-git:commit", "Git: Commit all changes"],
  ["obsidian-git:commit-smart", "Git: Commit"],
];

describe("ObsidianGitBridge", () => {
  it("finds the known ids and runs them", () => {
    const registry = commands(CURRENT);
    const bridge = new ObsidianGitBridge(() => registry);

    expect(bridge.lookup()).toEqual({
      pull: "obsidian-git:pull",
      commitAndSync: "obsidian-git:push",
      commit: "obsidian-git:commit",
    });
    expect(bridge.run("pull")).toBe(true);
    expect(bridge.run("commitAndSync")).toBe(true);
    expect(bridge.run("commit")).toBe(true);
    expect(registry.ran).toEqual(["obsidian-git:pull", "obsidian-git:push", "obsidian-git:commit"]);
    expect(bridge.warning()).toBeNull();
  });

  it("follows a renamed id by the command's name", () => {
    const registry = commands([
      ["obsidian-git:pull-from-remote", "Obsidian Git: Pull"],
      ["obsidian-git:backup", "Obsidian Git: Create backup"],
      ["obsidian-git:commit-all", "Obsidian Git: Commit all changes"],
    ]);
    const bridge = new ObsidianGitBridge(() => registry);

    expect(bridge.lookup()).toEqual({
      pull: "obsidian-git:pull-from-remote",
      commitAndSync: "obsidian-git:backup",
      commit: "obsidian-git:commit-all",
    });
  });

  it("never takes another plugin's command of the same name", () => {
    const bridge = new ObsidianGitBridge(() => commands([["other-sync:pull", "Other: Pull"]]));
    expect(bridge.lookup().pull).toBeNull();
  });

  it("turns a missing command into an instruction and one warning line", () => {
    const registry = commands([["obsidian-git:pull", "Git: Pull"]]);
    const bridge = new ObsidianGitBridge(() => registry);

    expect(bridge.missing()).toEqual(["commitAndSync", "commit"]);
    expect(bridge.run("commit")).toBe(false);
    expect(registry.ran).toEqual([]);
    expect(bridge.instruction("commitAndSync")).toBe(INSTRUCTIONS.commitAndSync);
    expect(bridge.warning()).toBe(
      "Obsidian Git has no Commit-and-sync, Commit command (renamed?): those buttons show what to run instead.",
    );
  });

  it("says when Obsidian Git is not there at all", () => {
    const bridge = new ObsidianGitBridge(() => undefined);
    expect(bridge.missing()).toEqual(["pull", "commitAndSync", "commit"]);
    expect(bridge.warning()).toMatch(/not installed or enabled/);
    expect(bridge.run("pull")).toBe(false);
  });

  it("tries only obsidian-git ids", () => {
    for (const ids of Object.values(KNOWN_IDS)) for (const id of ids) expect(id.startsWith("obsidian-git:")).toBe(true);
  });
});
```

Run: `cd obsidian/artmind-obsidian && npx vitest run test/bridge.test.ts`
Expected: the file fails to load — `Error: Cannot find module '../src/bridge' imported from …/test/bridge.test.ts` (`Test Files  1 failed (1)`, `Tests  no tests`).

- [ ] **Step 2: Implement**

**Create** `obsidian/artmind-obsidian/src/bridge.ts`:

```ts
/** Triggers Obsidian Git's own commands: the plugin never writes to git
 * itself (spec P5, §4.5). */

export type GitAction = "pull" | "commitAndSync" | "commit";

/** Command ids tried in order, read from obsidian-git 2.40.0's `main.js`:
 * `pull` ("Pull"), `push` ("Commit-and-sync"; the same id was named
 * "Create backup" in older releases) and `commit` ("Commit all changes",
 * which also concludes a merge: it stages everything and commits). */
export const KNOWN_IDS: Record<GitAction, string[]> = {
  pull: ["obsidian-git:pull"],
  commitAndSync: ["obsidian-git:push"],
  commit: ["obsidian-git:commit"],
};

/** When no known id is registered (a rename in a later release), a command
 * of the obsidian-git plugin whose name is one of these is used instead. */
export const KNOWN_NAMES: Record<GitAction, string[]> = {
  pull: ["Pull"],
  commitAndSync: ["Commit-and-sync", "Create backup"],
  commit: ["Commit all changes"],
};

export const INSTRUCTIONS: Record<GitAction, string> = {
  pull: "Run Obsidian Git: Pull",
  commitAndSync: "Run Obsidian Git: Commit-and-sync",
  commit: "Run Obsidian Git: Commit all changes",
};

const LABELS: Record<GitAction, string> = { pull: "Pull", commitAndSync: "Commit-and-sync", commit: "Commit" };

/** The slice of Obsidian's `app.commands` this needs. */
export interface CommandsLike {
  commands: Record<string, { id: string; name: string }>;
  executeCommandById(id: string): boolean;
}

export class ObsidianGitBridge {
  private getCommands: () => CommandsLike | undefined;

  constructor(getCommands: () => CommandsLike | undefined) {
    this.getCommands = getCommands;
  }

  /** The command id each action runs, or null when none is registered. */
  lookup(): Record<GitAction, string | null> {
    const registry = this.getCommands()?.commands ?? {};
    const find = (action: GitAction): string | null => {
      for (const id of KNOWN_IDS[action]) if (registry[id]) return id;
      const names = KNOWN_NAMES[action].map((n) => n.toLowerCase());
      for (const command of Object.values(registry)) {
        if (!command.id.startsWith("obsidian-git:")) continue;
        const bare = command.name.includes(": ") ? command.name.slice(command.name.indexOf(": ") + 2) : command.name;
        if (names.includes(bare.toLowerCase())) return command.id;
      }
      return null;
    };
    return { pull: find("pull"), commitAndSync: find("commitAndSync"), commit: find("commit") };
  }

  missing(): GitAction[] {
    const ids = this.lookup();
    return (Object.keys(ids) as GitAction[]).filter((action) => ids[action] === null);
  }

  has(action: GitAction): boolean {
    return this.lookup()[action] !== null;
  }

  /** Runs the action's command; false (and nothing runs) when it is missing. */
  run(action: GitAction): boolean {
    const id = this.lookup()[action];
    const commands = this.getCommands();
    if (!id || !commands) return false;
    return commands.executeCommandById(id);
  }

  instruction(action: GitAction): string {
    return INSTRUCTIONS[action];
  }

  /** The one warning line for the panel, or null when every command is there. */
  warning(): string | null {
    const missing = this.missing();
    if (!missing.length) return null;
    if (missing.length === 3) return "Obsidian Git is not installed or enabled: its buttons show what to run instead.";
    return `Obsidian Git has no ${missing.map((a) => LABELS[a]).join(", ")} command (renamed?): those buttons show what to run instead.`;
  }
}
```

- [ ] **Step 3: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  5 passed (5); Tests  89 passed (89)`; the build succeeds.

- [ ] **Step 4: Commit**

```bash
git add obsidian/artmind-obsidian/src/bridge.ts obsidian/artmind-obsidian/test/bridge.test.ts
git commit -m "feat(obsidian): ObsidianGitBridge -- known ids, name fallbacks, instructions when missing" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `Watchers` and the mapped folders (spec §4.1, §4.3, §4.4)

`manifest.ts` reads `ingest.mappings` from `.artmind/vault.yaml` (the plugin reads it through the vault adapter, since Obsidian does not index dot-folders) and matches paths with `PurePosixPath.full_match`'s glob rules, as artmind does. `promptsOnArrival` is true for a `.pdf`/`.pptx`/`.docx`/`.xlsx`/`.csv` in a mapped folder, never under a dot-folder or `_Inbox`; notes never prompt.

`Watchers` schedules, and nothing else. The HEAD poll (15 s) reads HEAD and calls `onHeadSettled` only when a new HEAD is read twice in a row; `lastPullAt` records when, for `pullIsOld()` (5 minutes). `waitForPull` follows a plugin-run Pull every 2 s until HEAD settles, or gives up after a minute (a pull with nothing new) — either way the pull counts as seen. `fileCreated` groups prompting arrivals in a sliding 10 s window, then asks for one notice and a refresh. `trackJob` polls every 3 s until `pollJob` resolves false. Nothing runs before `start()` (dormancy).

**Files:**
- Create: `obsidian/artmind-obsidian/src/manifest.ts`, `src/watchers.ts`, `test/manifest.test.ts`, `test/watchers.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/manifest.test.ts`:

```ts
import { parse } from "yaml";
import { describe, expect, it } from "vitest";
import { globToRegExp, mappingFor, promptsOnArrival, readMappings } from "../src/manifest";

const YAML = `
vault_id: "abc"
ingest:
  trigger: manual
  mappings:
    - path: "Team_performance_management/**"
      domain: banking
    - path: "Notes/*.md"
      domain: general
`;

describe("manifest", () => {
  const mappings = readMappings(YAML, parse);

  it("reads ingest.mappings", () => {
    expect(mappings).toEqual([
      { path: "Team_performance_management/**", domain: "banking" },
      { path: "Notes/*.md", domain: "general" },
    ]);
  });

  it("maps nothing when the manifest does not parse or has no mappings", () => {
    expect(readMappings("ingest: [", parse)).toEqual([]);
    expect(readMappings("vault_id: x\n", parse)).toEqual([]);
  });

  it("matches like PurePosixPath.full_match", () => {
    expect(globToRegExp("a/**").test("a/b.pdf")).toBe(true);
    expect(globToRegExp("a/**").test("a/b/c.pdf")).toBe(true);
    expect(globToRegExp("a/*.md").test("a/b.md")).toBe(true);
    expect(globToRegExp("a/*.md").test("a/b/c.md")).toBe(false);
    expect(globToRegExp("a/**/x.csv").test("a/x.csv")).toBe(true);
    expect(globToRegExp("a/**/x.csv").test("a/b/c/x.csv")).toBe(true);
    expect(globToRegExp("a.b/?.md").test("a.b/c.md")).toBe(true);
    expect(globToRegExp("a.b/?.md").test("aXb/c.md")).toBe(false);
  });

  it("first match wins", () => {
    expect(mappingFor(mappings, "Team_performance_management/q3/report.pdf")?.domain).toBe("banking");
    expect(mappingFor(mappings, "Elsewhere/report.pdf")).toBeNull();
  });

  it("prompts only for a binary or table in a mapped folder", () => {
    expect(promptsOnArrival(mappings, "Team_performance_management/deck.PPTX")).toBe(true);
    expect(promptsOnArrival(mappings, "Team_performance_management/export.xlsx")).toBe(true);
    expect(promptsOnArrival(mappings, "Team_performance_management/notes.md")).toBe(false);
    expect(promptsOnArrival(mappings, "Team_performance_management/image.png")).toBe(false);
    expect(promptsOnArrival(mappings, "Unmapped/deck.pdf")).toBe(false);
    expect(promptsOnArrival(mappings, "Team_performance_management/_Inbox/deck.pdf")).toBe(false);
    expect(promptsOnArrival(mappings, "Team_performance_management/.hidden/deck.pdf")).toBe(false);
  });
});
```

**Create** `obsidian/artmind-obsidian/test/watchers.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type WatcherDeps, Watchers } from "../src/watchers";

/** Deps whose HEAD readings come from `heads` (the last one repeats). */
function deps(heads: Array<string | null>, overrides: Partial<WatcherDeps> = {}) {
  const calls = { settled: [] as Array<[string, string | null]>, newFiles: [] as string[][], refresh: [] as string[] };
  let i = 0;
  const d: WatcherDeps = {
    readHead: async () => heads[Math.min(i++, heads.length - 1)],
    onHeadSettled: (head, previous) => calls.settled.push([head, previous]),
    onNewFiles: (paths) => calls.newFiles.push(paths),
    promptsOnArrival: (path) => path.endsWith(".pdf"),
    refresh: (reason) => calls.refresh.push(reason),
    pollJob: async () => false,
    ...overrides,
  };
  return { d, calls };
}

const INTERVALS = { headPollMs: 15_000, newFileGroupMs: 10_000, jobPollMs: 3_000 };

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-30T12:00:00Z"));
});
afterEach(() => vi.useRealTimers());

describe("the HEAD poll (spec §4.1)", () => {
  it("acts on a new HEAD only after one more unchanged reading", async () => {
    const { d, calls } = deps(["a", "b", "b"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();

    await vi.advanceTimersByTimeAsync(15_000);
    expect(calls.settled).toEqual([]);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(calls.settled).toEqual([["b", "a"]]);
    w.stop();
  });

  it("waits out a HEAD that is still moving", async () => {
    const { d, calls } = deps(["a", "b", "c", "c"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();

    await vi.advanceTimersByTimeAsync(45_000);
    expect(calls.settled).toEqual([["c", "a"]]);
    w.stop();
  });

  it("ignores an unchanged HEAD and a failed read", async () => {
    const { d, calls } = deps(["a", "a", null, null, "a"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();

    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls.settled).toEqual([]);
    w.stop();
  });

  it("remembers when a pull landed, for 'pull first' (spec §4.2)", async () => {
    const { d } = deps(["a", "b", "b"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();
    expect(w.pullIsOld()).toBe(true);

    await vi.advanceTimersByTimeAsync(30_000);
    expect(w.pullIsOld()).toBe(false);
    await vi.advanceTimersByTimeAsync(5 * 60_000 + 1);
    expect(w.pullIsOld()).toBe(true);
    w.stop();
  });

  it("waits for the HEAD a plugin-run Pull brings", async () => {
    const { d } = deps(["a", "a", "b", "b"]);
    const w = new Watchers(d, { ...INTERVALS, headPollMs: 1_000_000 });
    await w.start();

    const settled = w.waitForPull(60_000);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await settled).toBe("b");
    expect(w.pullIsOld()).toBe(false);
    w.stop();
  });

  it("gives up waiting on a Pull that brought nothing, and still counts it", async () => {
    const { d } = deps(["a"]);
    const w = new Watchers(d, { ...INTERVALS, headPollMs: 1_000_000 });
    await w.start();

    const settled = w.waitForPull(20_000);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(await settled).toBe("a");
    expect(w.pullIsOld()).toBe(false);
    w.stop();
  });
});

describe("new files (spec §4.3)", () => {
  it("groups arrivals within 10 s into one notice, then refreshes", async () => {
    const { d, calls } = deps(["a"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();

    w.fileCreated("Team/a.pdf");
    await vi.advanceTimersByTimeAsync(5_000);
    w.fileCreated("Team/b.pdf");
    w.fileCreated("Team/note.md");
    await vi.advanceTimersByTimeAsync(9_999);
    expect(calls.newFiles).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls.newFiles).toEqual([["Team/a.pdf", "Team/b.pdf"]]);
    expect(calls.refresh).toEqual(["new files"]);

    w.fileCreated("Team/c.pdf");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls.newFiles).toEqual([["Team/a.pdf", "Team/b.pdf"], ["Team/c.pdf"]]);
    w.stop();
  });

  it("does nothing before start (dormant) or for files that never prompt", async () => {
    const { d, calls } = deps(["a"]);
    const w = new Watchers(d, INTERVALS);
    w.fileCreated("Team/a.pdf");
    await w.start();
    w.fileCreated("Team/note.md");
    await vi.advanceTimersByTimeAsync(20_000);
    expect(calls.newFiles).toEqual([]);
    w.stop();
  });
});

describe("focus and the job poll", () => {
  it("refreshes when Obsidian regains focus", async () => {
    const { d, calls } = deps(["a"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();
    w.focusRegained();
    expect(calls.refresh).toEqual(["focus"]);
    w.stop();
  });

  it("polls the job every 3 s until it stops running", async () => {
    const answers = [true, true, false];
    const pollJob = vi.fn(async () => answers.shift() ?? false);
    const { d } = deps(["a"], { pollJob });
    const w = new Watchers(d, INTERVALS);
    await w.start();

    w.trackJob();
    w.trackJob();
    expect(w.trackingJob).toBe(true);
    await vi.advanceTimersByTimeAsync(9_000);
    expect(pollJob).toHaveBeenCalledTimes(3);
    expect(w.trackingJob).toBe(false);
    await vi.advanceTimersByTimeAsync(9_000);
    expect(pollJob).toHaveBeenCalledTimes(3);
    w.stop();
  });

  it("stop() clears every timer", async () => {
    const { d, calls } = deps(["a", "b", "b"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();
    w.fileCreated("Team/a.pdf");
    w.stop();

    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls.settled).toEqual([]);
    expect(calls.newFiles).toEqual([]);
  });
});
```

Run: `cd obsidian/artmind-obsidian && npx vitest run test/manifest.test.ts test/watchers.test.ts`
Expected: both files fail to load — `Error: Cannot find module '../src/manifest'` and `Cannot find module '../src/watchers'` (`Test Files  2 failed (2)`, `Tests  no tests`).

- [ ] **Step 2: Implement**

**Create** `obsidian/artmind-obsidian/src/manifest.ts`:

```ts
/** Which vault paths `.artmind/vault.yaml` maps (docs/vault.md, "The ingest
 * manifest"), so a file landing outside every mapping never prompts. */

export const VAULT_YAML = ".artmind/vault.yaml";

/** File types whose arrival prompts "N new files … [Ingest now]" (spec §4.3).
 * Notes never prompt: Obsidian saves them every few seconds. */
export const PROMPTING_EXTENSIONS = [".pdf", ".pptx", ".docx", ".xlsx", ".csv"];

export interface Mapping {
  path: string;
  domain: string;
}

/** `ingest.mappings` from the manifest's text; `parse` is Obsidian's
 * `parseYaml`. A manifest that does not parse maps nothing. */
export function readMappings(text: string, parse: (text: string) => unknown): Mapping[] {
  let data: unknown;
  try {
    data = parse(text);
  } catch {
    return [];
  }
  const ingest = (data as { ingest?: { mappings?: unknown } } | null)?.ingest;
  const raw = Array.isArray(ingest?.mappings) ? ingest.mappings : [];
  return raw
    .filter((m): m is Mapping => Boolean(m && typeof m.path === "string" && typeof m.domain === "string"))
    .map((m) => ({ path: m.path, domain: m.domain }));
}

/** `PurePosixPath.full_match` semantics, which artmind's manifest uses: `*`
 * and `?` stay inside one path segment, a `**` segment matches any number
 * of segments (zero included). */
export function globToRegExp(glob: string): RegExp {
  const segments = glob.split("/");
  let pattern = "";
  segments.forEach((segment, index) => {
    const last = index === segments.length - 1;
    if (segment === "**") {
      pattern += last ? ".*" : "(?:[^/]+/)*";
      return;
    }
    for (const ch of segment) {
      if (ch === "*") pattern += "[^/]*";
      else if (ch === "?") pattern += "[^/]";
      else pattern += ch.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
    if (!last) pattern += "/";
  });
  return new RegExp(`^${pattern}$`);
}

/** The first mapping covering `relpath` (vault-relative), as artmind picks it. */
export function mappingFor(mappings: Mapping[], relpath: string): Mapping | null {
  return mappings.find((m) => globToRegExp(m.path).test(relpath)) ?? null;
}

/** A file whose arrival should prompt: a binary or table type, in a mapped
 * folder, not under a dot-folder or `_Inbox` (never walked by ingest). */
export function promptsOnArrival(mappings: Mapping[], relpath: string): boolean {
  const lower = relpath.toLowerCase();
  if (!PROMPTING_EXTENSIONS.some((ext) => lower.endsWith(ext))) return false;
  const parts = relpath.split("/");
  if (parts.some((part) => part.startsWith(".") || part === "_Inbox")) return false;
  return mappingFor(mappings, relpath) !== null;
}
```

**Create** `obsidian/artmind-obsidian/src/watchers.ts`:

```ts
/** The plugin's clocks and listeners (spec §4.1, §4.3, §4.4): the HEAD poll,
 * new files in mapped folders, focus regained, and the job poll. Each only
 * schedules work; what the work is belongs to the controller. */

export interface Clock {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

export const realClock: Clock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (handle) => clearInterval(handle as ReturnType<typeof setInterval>),
};

export interface WatcherDeps {
  readHead(): Promise<string | null>;
  /** HEAD moved and then held still for one more reading. */
  onHeadSettled(head: string, previous: string | null): void;
  /** Files that prompt on arrival (already filtered), grouped. */
  onNewFiles(paths: string[]): void;
  promptsOnArrival(path: string): boolean;
  /** Schedule a status refresh (the controller debounces it). */
  refresh(reason: string): void;
  /** Read the tracked job once; resolves false when it is no longer running. */
  pollJob(): Promise<boolean>;
}

export interface Intervals {
  headPollMs: number;
  newFileGroupMs: number;
  jobPollMs: number;
}

export const DEFAULT_INTERVALS: Intervals = { headPollMs: 15_000, newFileGroupMs: 10_000, jobPollMs: 3_000 };

export class Watchers {
  private deps: WatcherDeps;
  private clock: Clock;
  private intervals: Intervals;
  private headTimer: unknown = null;
  private jobTimer: unknown = null;
  private groupTimer: unknown = null;
  private arrivals: string[] = [];
  private stable: string | null = null;
  private candidate: string | null = null;
  private settleWaiters: Array<(head: string | null) => void> = [];
  private started = false;
  /** When the HEAD poll last saw a pull land (or the plugin ran one). */
  lastPullAt: number | null = null;

  constructor(deps: WatcherDeps, intervals: Intervals = DEFAULT_INTERVALS, clock: Clock = realClock) {
    this.deps = deps;
    this.intervals = intervals;
    this.clock = clock;
  }

  /** Reads HEAD once as the baseline (not a pull), then polls. */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.stable = await this.deps.readHead();
    this.headTimer = this.clock.setInterval(() => void this.pollHead(), this.intervals.headPollMs);
  }

  stop(): void {
    this.started = false;
    if (this.headTimer !== null) this.clock.clearInterval(this.headTimer);
    if (this.jobTimer !== null) this.clock.clearInterval(this.jobTimer);
    if (this.groupTimer !== null) this.clock.clearTimeout(this.groupTimer);
    this.headTimer = this.jobTimer = this.groupTimer = null;
    this.arrivals = [];
  }

  /** One HEAD reading. A new HEAD must be read twice in a row before it
   * counts, so a merge still being written is never read half-done. */
  async pollHead(): Promise<void> {
    const head = await this.deps.readHead();
    if (head === null || head === this.stable) {
      this.candidate = null;
      return;
    }
    if (head !== this.candidate) {
      this.candidate = head;
      return;
    }
    const previous = this.stable;
    this.stable = head;
    this.candidate = null;
    this.lastPullAt = this.clock.now();
    for (const resolve of this.settleWaiters.splice(0)) resolve(head);
    this.deps.onHeadSettled(head, previous);
  }

  /** After the plugin ran Obsidian Git's Pull: resolves with the settled
   * HEAD once it moved, or with the unchanged HEAD after `timeoutMs` (a
   * pull with nothing new). Either way the pull counts as seen now. */
  waitForPull(timeoutMs = 60_000): Promise<string | null> {
    return new Promise((resolve) => {
      let done = false;
      const finish = (head: string | null) => {
        if (done) return;
        done = true;
        this.lastPullAt = this.clock.now();
        resolve(head);
      };
      this.settleWaiters.push(finish);
      this.clock.setTimeout(() => finish(this.stable), timeoutMs);
      const poll = () => {
        if (done) return;
        void this.pollHead().then(() => {
          if (!done) this.clock.setTimeout(poll, 2_000);
        });
      };
      this.clock.setTimeout(poll, 2_000);
    });
  }

  /** Obsidian's `create` event, for every new file (vault-relative path). */
  fileCreated(path: string): void {
    if (!this.started || !this.deps.promptsOnArrival(path)) return;
    this.arrivals.push(path);
    if (this.groupTimer !== null) this.clock.clearTimeout(this.groupTimer);
    this.groupTimer = this.clock.setTimeout(() => {
      this.groupTimer = null;
      const paths = this.arrivals.splice(0);
      if (paths.length) this.deps.onNewFiles(paths);
      this.deps.refresh("new files");
    }, this.intervals.newFileGroupMs);
  }

  focusRegained(): void {
    if (this.started) this.deps.refresh("focus");
  }

  /** Poll the tracked job every few seconds until it stops running. */
  trackJob(): void {
    if (this.jobTimer !== null || !this.started) return;
    this.jobTimer = this.clock.setInterval(() => {
      void this.deps.pollJob().then((running) => {
        if (!running && this.jobTimer !== null) {
          this.clock.clearInterval(this.jobTimer);
          this.jobTimer = null;
        }
      });
    }, this.intervals.jobPollMs);
  }

  get trackingJob(): boolean {
    return this.jobTimer !== null;
  }

  /** Whether the last pull is older than `maxAgeMs` (or never seen). */
  pullIsOld(maxAgeMs = 5 * 60_000): boolean {
    return this.lastPullAt === null || this.clock.now() - this.lastPullAt > maxAgeMs;
  }
}
```

- [ ] **Step 3: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  7 passed (7); Tests  105 passed (105)`; the build succeeds.

- [ ] **Step 4: Commit**

```bash
git add obsidian/artmind-obsidian/src/manifest.ts obsidian/artmind-obsidian/src/watchers.ts obsidian/artmind-obsidian/test/manifest.test.ts obsidian/artmind-obsidian/test/watchers.test.ts
git commit -m "feat(obsidian): Watchers -- HEAD settle, new files in mapped folders, focus, job poll" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `Settings` and the notice texts (spec §3.3, §7)

`ArtmindSettings`: the artmind path (empty = auto-detect), the three intervals, a toggle per notice kind, the commit-and-sync offer (**on**, P6) and the admin-ui URL for "Ask admin-ui to create one". `mergeSettings` lays saved data over the defaults, keeping a default wherever the saved value is missing or of the wrong type. `texts.ts` builds every notice's words from the CLI's JSON — "Pulled from the other laptop — 2 docs · 1 table behind.", "2 new files in Team_performance_management.", "Ingested 4, 1 failed.", "Synced: 3 docs, 1 table, 2 curation records.", "team projected: 7 entities.", "Artmind files resolved." — tested on the fixtures.

**Files:**
- Create: `obsidian/artmind-obsidian/src/settings.ts`, `src/texts.ts`, `test/settings.test.ts`, `test/texts.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/settings.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, NOTICE_LABELS, mergeSettings } from "../src/settings";

describe("settings (spec §7)", () => {
  it("offers commit-and-sync after artmind writes by default (P6)", () => {
    expect(DEFAULT_SETTINGS.offerCommitAndSync).toBe(true);
    expect(DEFAULT_SETTINGS.headPollSeconds).toBe(15);
    expect(DEFAULT_SETTINGS.jobPollSeconds).toBe(3);
    expect(DEFAULT_SETTINGS.newFileGroupSeconds).toBe(10);
    expect(Object.values(DEFAULT_SETTINGS.notices).every(Boolean)).toBe(true);
  });

  it("has a toggle label for every notice kind", () => {
    expect(Object.keys(NOTICE_LABELS).sort()).toEqual(Object.keys(DEFAULT_SETTINGS.notices).sort());
  });

  it("keeps saved values and defaults the rest", () => {
    const merged = mergeSettings({ artmindPath: "/opt/artmind", notices: { newFiles: false }, offerCommitAndSync: false });
    expect(merged.artmindPath).toBe("/opt/artmind");
    expect(merged.notices.newFiles).toBe(false);
    expect(merged.notices.syncDone).toBe(true);
    expect(merged.offerCommitAndSync).toBe(false);
    expect(merged.headPollSeconds).toBe(15);
  });

  it("ignores malformed data", () => {
    expect(mergeSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(mergeSettings({ headPollSeconds: "fast", notices: { pulled: "yes" } })).toEqual(DEFAULT_SETTINGS);
    expect(mergeSettings({ headPollSeconds: 1 }).headPollSeconds).toBe(5);
  });
});
```

**Create** `obsidian/artmind-obsidian/test/texts.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { ingestDoneText, newFilesText, projectedText, pulledText, resolvedText, syncDoneText } from "../src/texts";
import { fixture } from "./fixtures";

describe("notice texts (spec §3.3)", () => {
  it("a pull that left the stores behind", () => {
    expect(pulledText(fixture("vault-status.behind").json)).toBe("Pulled from the other laptop — 2 docs · 1 table behind.");
  });

  it("new files, named by their folder", () => {
    expect(newFilesText(["Team_performance_management/a.pdf", "Team_performance_management/b.xlsx"])).toBe(
      "2 new files in Team_performance_management.",
    );
    expect(newFilesText(["A/x.pdf", "B/y.pdf"])).toBe("2 new files in 2 folders.");
    expect(newFilesText(["deep/er/x.csv"])).toBe("1 new file in er.");
  });

  it("an ingest job that finished", () => {
    expect(ingestDoneText(fixture("ingest-job-status.done").json)).toBe("Ingested 4, 1 failed.");
    const allGood = { ...fixture("ingest-job-status.done").json, files: [{ filename: "a", status: "completed", current_step: null }] };
    expect(ingestDoneText(allGood)).toBe("Ingested 1.");
  });

  it("a sync that finished", () => {
    expect(syncDoneText(fixture("vault-sync.success").json)).toBe("Synced: 2 docs, 1 table, 0 curation records.");
    expect(syncDoneText({ replayed: 3, regenerated_tables: 1, curation: { conflict: { apply: 2 } } })).toBe(
      "Synced: 3 docs, 1 table, 2 curation records.",
    );
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

Run: `cd obsidian/artmind-obsidian && npx vitest run test/settings.test.ts test/texts.test.ts`
Expected: both files fail to load — `Error: Cannot find module '../src/settings'` and `Cannot find module '../src/texts'` (`Test Files  2 failed (2)`, `Tests  no tests`).

- [ ] **Step 2: Implement**

**Create** `obsidian/artmind-obsidian/src/settings.ts`:

```ts
/** The plugin's settings (spec §7), stored by Obsidian in
 * `.obsidian/plugins/artmind/data.json`. */

export type NoticeKind = "pulled" | "newFiles" | "ingestDone" | "syncDone" | "table2graphDone" | "resolveDone";

export interface ArtmindSettings {
  /** Path to the `artmind` executable; empty means auto-detect. */
  artmindPath: string;
  headPollSeconds: number;
  jobPollSeconds: number;
  newFileGroupSeconds: number;
  notices: Record<NoticeKind, boolean>;
  /** Offer Obsidian Git's Commit-and-sync after artmind writes (spec P6). */
  offerCommitAndSync: boolean;
  /** Where "Ask admin-ui to create one" opens (`artmind admin-ui`'s default port). */
  adminUiUrl: string;
}

export const DEFAULT_SETTINGS: ArtmindSettings = {
  artmindPath: "",
  headPollSeconds: 15,
  jobPollSeconds: 3,
  newFileGroupSeconds: 10,
  notices: {
    pulled: true,
    newFiles: true,
    ingestDone: true,
    syncDone: true,
    table2graphDone: true,
    resolveDone: true,
  },
  offerCommitAndSync: true,
  adminUiUrl: "http://127.0.0.1:8379",
};

export const NOTICE_LABELS: Record<NoticeKind, string> = {
  pulled: "A pull left a store behind",
  newFiles: "A binary or table lands in a mapped folder",
  ingestDone: "An ingest job finishes",
  syncDone: "Sync finishes",
  table2graphDone: "table2graph finishes",
  resolveDone: "vault resolve finishes",
};

/** Saved data over the defaults; a missing or malformed field keeps its default. */
export function mergeSettings(saved: unknown): ArtmindSettings {
  const data = (saved && typeof saved === "object" ? saved : {}) as Partial<ArtmindSettings>;
  const pick = <K extends keyof ArtmindSettings>(key: K): ArtmindSettings[K] =>
    typeof data[key] === typeof DEFAULT_SETTINGS[key] ? (data[key] as ArtmindSettings[K]) : DEFAULT_SETTINGS[key];
  const notices = { ...DEFAULT_SETTINGS.notices };
  for (const kind of Object.keys(notices) as NoticeKind[]) {
    const value = (data.notices as Partial<Record<NoticeKind, unknown>> | undefined)?.[kind];
    if (typeof value === "boolean") notices[kind] = value;
  }
  return {
    artmindPath: pick("artmindPath"),
    headPollSeconds: Math.max(5, pick("headPollSeconds")),
    jobPollSeconds: Math.max(1, pick("jobPollSeconds")),
    newFileGroupSeconds: Math.max(1, pick("newFileGroupSeconds")),
    notices,
    offerCommitAndSync: pick("offerCommitAndSync"),
    adminUiUrl: pick("adminUiUrl"),
  };
}
```

**Create** `obsidian/artmind-obsidian/src/texts.ts`:

```ts
import { behindParts, plural } from "./state";
import type { JobStatus, SyncResult, TableReport, VaultStatus } from "./types";

/** The words of every notice (spec §3.3), from the JSON the CLI printed. */

export function pulledText(status: VaultStatus): string {
  const parts = behindParts(status.sync.stores);
  return `Pulled from the other laptop — ${parts.join(" · ") || "stores"} behind.`;
}

export function newFilesText(paths: string[]): string {
  const folders = [...new Set(paths.map((p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "the vault root")))];
  const where = folders.length === 1 ? folders[0].split("/").pop()! : plural(folders.length, "folder");
  return `${plural(paths.length, "new file")} in ${where}.`;
}

export function ingestDoneText(job: JobStatus): string {
  const failed = job.files.filter((f) => f.status === "failed").length;
  const done = job.files.filter((f) => f.status === "completed").length;
  return failed ? `Ingested ${done}, ${failed} failed.` : `Ingested ${done}.`;
}

export function syncDoneText(result: SyncResult): string {
  const curation = Object.values(result.curation ?? {}).reduce(
    (sum, counts) => sum + Object.values(counts).reduce((a, b) => a + b, 0),
    0,
  );
  const parts = [
    plural((result.replayed ?? 0) + (result.retracted ?? 0), "doc"),
    plural((result.regenerated_tables ?? 0) + (result.curated_tables ?? 0), "table"),
    plural(curation, "curation record"),
  ];
  return `Synced: ${parts.join(", ")}.`;
}

export function projectedText(report: TableReport): string {
  const entities = Object.values(report.entities).reduce((sum, e) => sum + e.kept, 0);
  return `${report.table} projected: ${plural(entities, "entity", "entities")}.`;
}

export const RESOLVED_TEXT = "Artmind files resolved.";

export function resolvedText(reportedLeft: number): string {
  return reportedLeft
    ? `${RESOLVED_TEXT} Resolve the ${plural(reportedLeft, "file")} listed under "Reported" in Obsidian, then complete the merge.`
    : RESOLVED_TEXT;
}
```

- [ ] **Step 3: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  9 passed (9); Tests  115 passed (115)`; the build succeeds.

- [ ] **Step 4: Commit**

```bash
git add obsidian/artmind-obsidian/src/settings.ts obsidian/artmind-obsidian/src/texts.ts obsidian/artmind-obsidian/test/settings.test.ts obsidian/artmind-obsidian/test/texts.test.ts
git commit -m "feat(obsidian): settings and notice texts" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: the flows — `Controller` (spec §4, §3.3, §8, P2, P6, P7)

The controller keeps the latest inputs, recomputes `vaultState` after every read and hands the views a `Snapshot`. Its methods are the spec's flows:

- `checkArtmind()` finds `artmind` and checks `--version` against `MIN_ARTMIND_VERSION`: missing, too old (or no `--version` at all), or fine.
- `refresh()` runs only reads, at once: `vault status`, `ingest pending`, `table2graph --pending`, `ingest jobs-active` (a job started elsewhere is picked up and followed), and the git facts. A plugin write in the last 30 s, or a running plugin-tracked job, keeps `.artmind/` changes from counting as "to share". A failing background read is kept for the panel, not dropped.
- `onHeadSettled()` refreshes, then — if a store is behind — shows "Pulled from the other laptop — … behind." with [Sync now]. It never syncs by itself (P2).
- `sync()` offers [Pull, then sync] / [Sync without pulling] when the last pull is old and Obsidian Git has Pull; queues behind a running ingest; runs `vault sync`; reports what it applied; and turns each refusal into its action (§4.2): [Resolve], [Pull], the bootstrap step, or the queue.
- `ingestWhatChanged()` refuses with the reason while a job runs or a merge is in progress; offers the pull; offers [Sync, then ingest] / [Ingest anyway] when a store is behind; warns (and continues) when Neo4j is unreachable; submits `ingest async --pending`; says "Nothing new or changed to ingest." when there was nothing; else tracks the job.
- `pollJob()` follows the job; when it finishes: "Ingested N, M failed." with [Details] (the failed files and their errors, from `ingest job-results`) and, under P6, [Commit-and-sync]; then any queued sync runs.
- `tableDryRun`/`tableProject`, `resolvePreview`/`resolveApply` (with [Complete the merge] only when nothing is left for the user), `runDoctor` (keeps the report though it exits 1), `perform(action)` for the status bar's click, and `gitAction`/`gitButton` (a missing Obsidian Git command becomes its instruction).

Nothing writes to the graph unless one of `sync`, `ingestWhatChanged`, `tableProject` or `resolveApply` is called — which only a click does (P7). The tests stand every dependency in with recorders and replay fixtures as CLI results; the first test asserts `refresh()` runs no write.

**Files:**
- Create: `obsidian/artmind-obsidian/src/controller.ts`, `test/controller.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/controller.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { GitAction } from "../src/bridge";
import type { CliResult } from "../src/cli";
import { errorText } from "../src/cli";
import {
  type CliLike,
  Controller,
  type NoticeButton,
  OWN_WRITE_GRACE_MS,
  type Snapshot,
  type ViewsLike,
} from "../src/controller";
import { DEFAULT_SETTINGS, type ArtmindSettings } from "../src/settings";
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

type Script = Partial<Record<keyof CliLike, CliResult | (() => CliResult)>>;

/** A CliLike answering from `script`, recording every call. Unscripted
 * reads answer "in sync, nothing waiting". */
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
    return Promise.resolve(typeof scripted === "function" ? scripted() : (scripted ?? fallback()));
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
}

interface Shown {
  kind: string;
  text: string;
  buttons: NoticeButton[];
}

function setup(options: {
  script?: Script;
  settings?: Partial<ArtmindSettings>;
  gitCommands?: GitAction[];
  pullIsOld?: boolean;
  changes?: string[];
  detected?: { path: string | null; looked: string[] };
} = {}) {
  const cli = new FakeCli(options.script);
  const shown: Shown[] = [];
  const views = { renders: [] as Snapshot[], opened: [] as string[] };
  const ran: GitAction[] = [];
  const tracked: number[] = [];
  const available = options.gitCommands ?? ["pull", "commitAndSync", "commit"];
  let clock = 1_000_000;
  const viewsLike: ViewsLike = {
    render: (snapshot) => views.renders.push(snapshot),
    openPanel: (section) => views.opened.push(`panel:${section}`),
    openResolve: () => views.opened.push("resolve"),
    openTableReview: (table) => views.opened.push(`table:${table}`),
  };
  const controller = new Controller({
    cli,
    git: { ahead: async () => 0, artmindChanges: async () => options.changes ?? [] },
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
    notifier: { show: (kind, text, buttons = []) => shown.push({ kind, text, buttons }) },
    views: viewsLike,
    settings: () => ({ ...DEFAULT_SETTINGS, ...options.settings }),
    detect: async () => options.detected ?? { path: "/Users/me/.local/bin/artmind", looked: ["/Users/me/.local/bin/artmind"] },
    now: () => clock,
    schedule: (fn) => fn(),
  });
  controller.attachWatchers({
    trackJob: () => tracked.push(clock),
    pullIsOld: () => options.pullIsOld ?? false,
    waitForPull: async () => "head",
  });
  const click = (text: string, label: string) => {
    const notice = shown.find((s) => s.text === text);
    if (!notice) throw new Error(`no notice "${text}" in ${JSON.stringify(shown.map((s) => s.text))}`);
    const button = notice.buttons.find((b) => b.label === label);
    if (!button) throw new Error(`no button "${label}" on "${text}"`);
    button.run();
  };
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
  return {
    cli,
    controller,
    shown,
    views,
    ran,
    tracked,
    click,
    settle,
    advance: (ms: number) => (clock += ms),
  };
}

const WRITES = ["sync", "ingestAsyncPending", "tableProject", "resolve"];

describe("reading state", () => {
  it("refresh runs only reads, and renders the state they give", async () => {
    const t = setup({ script: { status: replay("vault-status.behind"), ingestPending: replay("ingest-pending") } });

    await t.controller.refresh();

    expect(t.cli.calls.sort()).toEqual(["ingestPending", "jobsActive", "status", "tablesPending"]);
    expect(t.cli.calls.some((c) => WRITES.includes(c.split(" ")[0]))).toBe(false);
    const last = t.views.renders.at(-1)!;
    expect(last.state.states.map((s) => s.kind)).toEqual(["behind", "ingest"]);
  });

  it("picks up a job started elsewhere and follows it", async () => {
    const t = setup({ script: { jobsActive: replay("ingest-jobs-active") } });

    await t.controller.refresh();

    expect(t.controller.inputs.activeJob?.job_id).toBe(fixture("ingest-jobs-active").json[0].job_id);
    expect(t.tracked.length).toBe(1);
  });

  it("keeps a failed background read visible, not silent", async () => {
    const t = setup({ script: { tablesPending: result(["ingest", "table2graph", "--pending", "--compact"], 1, null, "╭─ Error ─╮\n│ bad.yaml: invalid YAML │\n╰─╯") } });

    await t.controller.refresh();

    expect(t.controller.readErrors).toEqual(["artmind ingest table2graph --pending --compact: bad.yaml: invalid YAML"]);
  });

  it("a failing vault status is the error state", async () => {
    const t = setup({ script: { status: result(["vault", "status", "--compact"], 1, null, "╭─ Error ─╮\n│ Not inside an artmind vault. │\n╰─╯") } });

    await t.controller.refresh();

    expect(t.controller.state.primary.kind).toBe("error");
    expect(t.controller.inputs.problem).toEqual({ kind: "failed", command: "vault status", message: "Not inside an artmind vault." });
  });
});

describe("noticing writes the plugin didn't make (spec §4.5)", () => {
  it("shows uncommitted .artmind changes no plugin action of the last 30 s produced", async () => {
    const t = setup({ changes: [".artmind/data/curation/conflict/c1.json"] });

    await t.controller.refresh();

    expect(t.controller.state.secondary).toEqual(["✎ artmind changes to share"]);
  });

  it("does not count the plugin's own write as someone else's, for 30 s", async () => {
    const t = setup({ changes: [".artmind/data/kg/general/doc/document.json"] });
    await t.controller.sync({ skipPull: true });
    expect(t.controller.state.secondary).toEqual([]);

    t.advance(OWN_WRITE_GRACE_MS);
    await t.controller.refresh();
    expect(t.controller.state.secondary).toEqual(["✎ artmind changes to share"]);
  });
});

describe("after a pull (spec §4.1, P2)", () => {
  it("nudges with [Sync now] when a store is behind, and never syncs by itself", async () => {
    const t = setup({ script: { status: replay("vault-status.behind") } });

    await t.controller.onHeadSettled();

    expect(t.shown.map((s) => [s.kind, s.text, s.buttons.map((b) => b.label)])).toEqual([
      ["pulled", "Pulled from the other laptop — 2 docs · 1 table behind.", ["Sync now"]],
    ]);
    expect(t.cli.calls).not.toContain("sync");
    t.click("Pulled from the other laptop — 2 docs · 1 table behind.", "Sync now");
    await t.settle();
    expect(t.cli.calls).toContain("sync");
  });

  it("says nothing when the pull left everything in sync, or the notice is off", async () => {
    const quiet = setup();
    await quiet.controller.onHeadSettled();
    expect(quiet.shown).toEqual([]);

    const off = setup({ script: { status: replay("vault-status.behind") }, settings: { notices: { ...DEFAULT_SETTINGS.notices, pulled: false } } });
    await off.controller.onHeadSettled();
    expect(off.shown).toEqual([]);
  });
});

describe("Sync (spec §4.2)", () => {
  it("offers a pull first when the last one is old", async () => {
    const t = setup({ pullIsOld: true });

    await t.controller.sync();

    expect(t.shown[0].text).toBe("Pull from GitHub first?");
    expect(t.shown[0].buttons.map((b) => b.label)).toEqual(["Pull, then sync", "Sync without pulling"]);
    expect(t.cli.calls).not.toContain("sync");

    t.click("Pull from GitHub first?", "Pull, then sync");
    await t.settle();
    await t.settle();
    expect(t.ran).toEqual(["pull"]);
    expect(t.cli.calls).toContain("sync");
  });

  it("syncs straight away when Obsidian Git has no Pull command", async () => {
    const t = setup({ pullIsOld: true, gitCommands: ["commit"] });
    await t.controller.sync();
    expect(t.cli.calls[0]).toBe("sync");
  });

  it("reports what a sync applied", async () => {
    const t = setup();

    await t.controller.sync({ skipPull: true });

    expect(t.shown).toEqual([{ kind: "syncDone", text: "Synced: 2 docs, 1 table, 0 curation records.", buttons: [] }]);
    expect(t.controller.lastSync?.ok).toBe(true);
    expect(t.cli.calls[0]).toBe("sync");
    expect(t.cli.calls).toContain("status");
  });

  it.each([
    ["vault-sync.refusal-merge-in-progress", "Resolve first", ["Resolve"]],
    ["vault-sync.refusal-artmind-conflicts", "Resolve first", ["Resolve"]],
    ["vault-sync.refusal-bookmark-unpulled", "Pull first", ["Pull"]],
    ["vault-sync.refusal-bookmark-not-ancestor", "Pull first", ["Pull"]],
  ])("turns %s into [%s]", async (name, text, buttons) => {
    const t = setup({ script: { sync: replay(name) } });

    await t.controller.sync({ skipPull: true });

    expect(t.shown.map((s) => [s.text, s.buttons.map((b) => b.label)])).toEqual([[text, buttons]]);
  });

  it("[Resolve] on a refusal opens the resolve preview", async () => {
    const t = setup({ script: { sync: replay("vault-sync.refusal-merge-in-progress") } });
    await t.controller.sync({ skipPull: true });
    t.click("Resolve first", "Resolve");
    expect(t.views.opened).toEqual(["resolve"]);
  });

  it("shows the bootstrap step for a first sync, and does not run it", async () => {
    const t = setup({ script: { sync: replay("vault-sync.refusal-no-bookmark") } });

    await t.controller.sync({ skipPull: true });

    expect(t.shown[0].text).toMatch(/^This laptop hasn't synced this vault before\. .*--bootstrapEmpty/);
    expect(t.cli.calls.filter((c) => c === "sync")).toHaveLength(1);
  });

  it("queues behind a running ingest and syncs when it finishes", async () => {
    const t = setup({ script: { sync: replay("vault-sync.refusal-worker-running") } });

    await t.controller.sync({ skipPull: true });
    expect(t.shown.map((s) => s.text)).toEqual(["Ingest in progress — will sync when it finishes"]);

    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };
    t.cli.script.sync = replay("vault-sync.success");
    await t.controller.pollJob();
    await t.settle();
    expect(t.cli.calls.filter((c) => c === "sync")).toHaveLength(2);
    expect(t.shown.map((s) => s.kind)).toEqual(["prompt", "ingestDone", "syncDone"]);
  });

  it("queues without running when the plugin already tracks a job", async () => {
    const t = setup();
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    await t.controller.sync({ skipPull: true });

    expect(t.cli.calls).not.toContain("sync");
    expect(t.shown[0].text).toBe("Ingest in progress — will sync when it finishes");
  });
});

describe("Ingest what changed (spec §4.4)", () => {
  it("asks to sync first when a store is behind", async () => {
    const t = setup({ script: { status: replay("vault-status.behind"), ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();

    await t.controller.ingestWhatChanged();

    expect(t.shown.at(-1)!.text).toBe("Sync first? The other laptop's changes aren't applied yet.");
    expect(t.shown.at(-1)!.buttons.map((b) => b.label)).toEqual(["Sync, then ingest", "Ingest anyway"]);
    expect(t.cli.calls).not.toContain("ingestAsyncPending");
  });

  it("[Sync, then ingest] syncs, then submits exactly the pending files", async () => {
    const t = setup({ script: { status: replay("vault-status.behind"), ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();
    await t.controller.ingestWhatChanged();
    t.cli.script.status = replay("vault-status.in-sync");

    t.click("Sync first? The other laptop's changes aren't applied yet.", "Sync, then ingest");
    for (let i = 0; i < 5; i++) await t.settle();

    const order = t.cli.calls.filter((c) => c === "sync" || c === "ingestAsyncPending");
    expect(order).toEqual(["sync", "ingestAsyncPending"]);
    expect(t.controller.inputs.activeJob?.job_id).toBe(fixture("ingest-async.pending").json.job_id);
    expect(t.tracked.length).toBeGreaterThan(0);
  });

  it("offers a pull first when the last one is old", async () => {
    const t = setup({ pullIsOld: true, script: { ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();

    await t.controller.ingestWhatChanged();

    expect(t.shown.at(-1)!.buttons.map((b) => b.label)).toEqual(["Pull, then ingest", "Ingest without pulling"]);
  });

  it("says so when nothing is pending, and starts no job", async () => {
    const t = setup({ script: { ingestAsyncPending: replay("ingest-async.nothing-pending") } });

    await t.controller.ingestWhatChanged({ skipPull: true });

    expect(t.shown.map((s) => s.text)).toEqual(["Nothing new or changed to ingest."]);
    expect(t.controller.inputs.activeJob).toBeNull();
    expect(t.tracked).toEqual([]);
  });

  it("warns, and still ingests, when Neo4j is unreachable (spec §8)", async () => {
    const t = setup({ script: { status: replay("vault-status.neo4j-unreachable"), ingestPending: replay("ingest-pending") } });
    await t.controller.refresh();

    await t.controller.ingestWhatChanged({ skipPull: true });

    expect(t.shown[0].text).toMatch(/graph write will fail/);
    expect(t.cli.calls).toContain("ingestAsyncPending");
  });

  it("refuses while a job already runs", async () => {
    const t = setup();
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    await t.controller.ingestWhatChanged();

    expect(t.shown.map((s) => s.text)).toEqual(["An ingest job is already running"]);
    expect(t.cli.calls).not.toContain("ingestAsyncPending");
  });
});

describe("a job finishing", () => {
  it("shows the result with [Details] and, under P6, [Commit-and-sync]", async () => {
    const t = setup();
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    expect(await t.controller.pollJob()).toBe(false);

    expect(t.shown.map((s) => [s.kind, s.text, s.buttons.map((b) => b.label)])).toEqual([
      ["ingestDone", "Ingested 4, 1 failed.", ["Details", "Commit-and-sync"]],
    ]);
    t.click("Ingested 4, 1 failed.", "Commit-and-sync");
    expect(t.ran).toEqual(["commitAndSync"]);
    t.click("Ingested 4, 1 failed.", "Details");
    await t.settle();
    expect(t.controller.jobResults?.files.find((f) => f.status === "failed")?.error_message).toBe("KG ingestion failed");
    expect(t.views.opened).toEqual(["panel:job"]);
  });

  it("keeps polling while the job runs", async () => {
    const t = setup({ script: { jobStatus: replay("ingest-job-status.running") } });
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    expect(await t.controller.pollJob()).toBe(true);
    expect(t.shown).toEqual([]);
    expect(t.controller.state.primary.label).toBe("◌ ingesting 2/5");
  });

  it("offers no commit when the setting is off, and an instruction when Obsidian Git lacks it", async () => {
    const off = setup({ settings: { offerCommitAndSync: false } });
    off.controller.inputs = { ...off.controller.inputs, activeJob: fixture("ingest-job-status.running").json };
    await off.controller.pollJob();
    expect(off.shown[0].buttons.map((b) => b.label)).toEqual(["Details"]);

    const missing = setup({ gitCommands: ["pull"] });
    missing.controller.inputs = { ...missing.controller.inputs, activeJob: fixture("ingest-job-status.running").json };
    await missing.controller.pollJob();
    expect(missing.shown[0].buttons.map((b) => b.label)).toEqual(["Details", "Run Obsidian Git: commitAndSync"]);
  });
});

describe("review screens", () => {
  it("projects a table only on the click, then offers commit-and-sync", async () => {
    const t = setup();

    const preview = await t.controller.tableDryRun("team");
    expect(preview.report?.table).toBe("team");
    expect(t.cli.calls).toEqual(["tableDryRun team"]);

    await t.controller.tableProject("team");
    expect(t.shown.map((s) => [s.kind, s.text, s.buttons.map((b) => b.label)])).toEqual([
      ["table2graphDone", "team projected: 7 entities.", ["Commit-and-sync"]],
    ]);
  });

  it("reports a table with no mapping", async () => {
    const t = setup({ script: { tableDryRun: replay("table2graph.dry-run-no-mapping") } });

    const preview = await t.controller.tableDryRun("budget");

    expect(preview.report).toBeNull();
    expect(preview.error).toMatch(/^no table mapping matches 'budget'/);
  });

  it("offers [Complete the merge] after resolve only when nothing is left for the user", async () => {
    const clean = { ...fixture("vault-resolve.dry-run").json, pending: [], reported: [], remaining: 0, dry_run: false };
    const t = setup({ script: { resolve: result(["vault", "resolve", "--compact"], 0, clean) } });

    await t.controller.resolveApply();
    expect(t.shown.map((s) => [s.text, s.buttons.map((b) => b.label)])).toEqual([["Artmind files resolved.", ["Complete the merge"]]]);
    t.click("Artmind files resolved.", "Complete the merge");
    expect(t.ran).toEqual(["commit"]);

    const left = setup();
    await left.controller.resolveApply();
    expect(left.shown[0].buttons).toEqual([]);
    expect(left.shown[0].text).toMatch(/Resolve the 3 files listed under "Reported"/);
  });

  it("keeps the doctor's report even though it exits 1", async () => {
    const t = setup();

    await t.controller.runDoctor();

    expect(t.controller.doctor?.ok).toBe(false);
    expect(t.views.opened).toEqual(["panel:doctor"]);
    expect(t.shown).toEqual([]);
  });
});

describe("artmind itself (spec §8)", () => {
  it("is missing when no path is found", async () => {
    const t = setup({ detected: { path: null, looked: ["/Users/me/.local/bin/artmind"] } });

    const problem = await t.controller.checkArtmind();

    expect(problem).toEqual({ kind: "missing", looked: ["/Users/me/.local/bin/artmind"] });
    expect(t.controller.state.primary.label).toBe("⚠ artmind");
  });

  it("is too old below the minimum, or without --version", async () => {
    const old = setup({ script: { version: result(["--version"], 0, null, "", "artmind 0.8.5\n") } });
    expect(await old.controller.checkArtmind()).toEqual({ kind: "too_old", found: "0.8.5", need: "0.9.0" });

    const ancient = setup({ script: { version: result(["--version"], 2, null, "╭─ Error ─╮\n│ No such option: --version │\n╰─╯") } });
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

describe("the status bar's click", () => {
  it.each([
    [{ type: "sync" }, "sync"],
    [{ type: "ingest" }, "ingestAsyncPending"],
  ] as const)("%j runs its flow", async (action, call) => {
    const t = setup();
    t.controller.perform(action);
    await t.settle();
    expect(t.cli.calls).toContain(call);
  });

  it("opens what it names", () => {
    const t = setup();
    t.controller.inputs = { ...t.controller.inputs, tablesPending: fixture("table2graph.pending").json };
    t.controller.perform({ type: "openResolve" });
    t.controller.perform({ type: "reviewTables" });
    t.controller.perform({ type: "openPanel", section: "error" });
    t.controller.perform({ type: "pull" });
    expect(t.views.opened).toEqual(["resolve", "table:team", "panel:error"]);
    expect(t.ran).toEqual(["pull"]);
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

Run: `cd obsidian/artmind-obsidian && npx vitest run test/controller.test.ts`
Expected: the file fails to load — `Error: Cannot find module '../src/controller' imported from …/test/controller.test.ts` (`Test Files  1 failed (1)`, `Tests  no tests`).

- [ ] **Step 2: Implement**

**Create** `obsidian/artmind-obsidian/src/controller.ts`:

```ts
import type { GitAction } from "./bridge";
import type { CliResult } from "./cli";
import type { ArtmindSettings, NoticeKind } from "./settings";
import {
  type Action,
  type ArtmindProblem,
  type PanelSection,
  type StateInputs,
  type VaultStateResult,
  classifyRefusal,
  vaultState,
} from "./state";
import { ingestDoneText, newFilesText, projectedText, pulledText, resolvedText, syncDoneText } from "./texts";
import type {
  DoctorReport,
  IngestPending,
  JobResults,
  JobStatus,
  ResolveReport,
  Submitted,
  SyncResult,
  TablePending,
  TableReport,
  VaultStatus,
} from "./types";
import { MIN_ARTMIND_VERSION, atLeast, formatVersion, parseVersion } from "./version";

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
  tablesPending(): Promise<CliResult>;
  tableDryRun(table: string): Promise<CliResult>;
  tableProject(table: string): Promise<CliResult>;
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

export interface NoticeButton {
  label: string;
  run: () => void;
}

/** Shows notices. `kind` is a notice the settings can turn off; prompts
 * and errors always show. */
export interface Notifier {
  show(kind: NoticeKind | "prompt" | "error", text: string, buttons?: NoticeButton[]): void;
}

export interface ViewsLike {
  render(snapshot: Snapshot): void;
  openPanel(section: PanelSection): void;
  openResolve(): void;
  openTableReview(table: string | null): void;
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
  state: VaultStateResult;
  activity: ActivityEntry[];
  lastSync: { at: number; ok: boolean; summary: string } | null;
  doctor: DoctorReport | null;
  jobResults: JobResults | null;
  /** Failures of the background reads (ingest pending, table2graph --pending). */
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
  now?: () => number;
  /** Debounces refresh requests; the default runs them after 500 ms. */
  schedule?: (fn: () => void, ms: number) => void;
}

/** A plugin write's own changes under `.artmind/` are not "changes to
 * share" for this long (spec §4.5). */
export const OWN_WRITE_GRACE_MS = 30_000;
export const ACTIVITY_LIMIT = 20;

/** The flows of spec §4: what each click does, in order. Holds the latest
 * inputs, recomputes `vaultState` after every read, and hands views a
 * snapshot to render. */
export class Controller {
  private deps: ControllerDeps;
  private watchers: WatchersLike | null = null;
  private refreshPending = false;
  private lastWriteAt = Number.NEGATIVE_INFINITY;
  private syncQueued: (() => void) | null = null;
  inputs: StateInputs = {
    status: null,
    activeJob: null,
    pending: null,
    tablesPending: null,
    git: { ahead: null, unsharedArtmindChanges: 0 },
    problem: null,
  };
  activity: ActivityEntry[] = [];
  lastSync: Snapshot["lastSync"] = null;
  doctor: DoctorReport | null = null;
  jobResults: JobResults | null = null;
  readErrors: string[] = [];
  looked: string[] = [];

  constructor(deps: ControllerDeps) {
    this.deps = deps;
  }

  attachWatchers(watchers: WatchersLike): void {
    this.watchers = watchers;
  }

  private now(): number {
    return (this.deps.now ?? Date.now)();
  }

  get state(): VaultStateResult {
    return vaultState(this.inputs);
  }

  snapshot(): Snapshot {
    return {
      inputs: this.inputs,
      state: this.state,
      activity: this.activity,
      lastSync: this.lastSync,
      doctor: this.doctor,
      jobResults: this.jobResults,
      readErrors: this.readErrors,
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

  /** A button for an Obsidian Git action; when the command is missing, its
   * label is the instruction instead (spec §4.5). */
  gitButton(action: GitAction, label: string): NoticeButton {
    const text = this.deps.bridge.has(action) ? label : this.deps.bridge.instruction(action);
    return { label: text, run: () => void this.gitAction(action) };
  }

  private render(): void {
    this.deps.views.render(this.snapshot());
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

  private markWrite(): void {
    this.lastWriteAt = this.now();
  }

  private notify(kind: NoticeKind | "prompt" | "error", text: string, buttons: NoticeButton[] = []): void {
    if (kind !== "prompt" && kind !== "error" && !this.deps.settings().notices[kind]) return;
    this.deps.notifier.show(kind, text, buttons);
  }

  /** [Commit-and-sync] after an artmind write, when the setting offers it (P6). */
  private commitButtons(): NoticeButton[] {
    return this.deps.settings().offerCommitAndSync ? [this.gitButton("commitAndSync", "Commit-and-sync")] : [];
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
    this.inputs = { ...this.inputs, problem };
    this.render();
    return problem;
  }

  // ── reading state ──────────────────────────────────────────────────────────

  /** A refresh soon, however many ask for one meanwhile. */
  scheduleRefresh(): void {
    if (this.refreshPending) return;
    this.refreshPending = true;
    const schedule = this.deps.schedule ?? ((fn, ms) => void setTimeout(fn, ms));
    schedule(() => {
      this.refreshPending = false;
      void this.refresh();
    }, 500);
  }

  /** Every read at once: status, pending files, pending tables, active
   * jobs, and the git facts. Nothing here writes. */
  async refresh(): Promise<void> {
    const problem = this.inputs.problem;
    if (problem && problem.kind !== "failed") {
      this.render();
      return;
    }
    const { cli, git } = this.deps;
    const [status, pending, tables, active, ahead, changes] = await Promise.all([
      cli.status(),
      cli.ingestPending(),
      cli.tablesPending(),
      this.inputs.activeJob ? Promise.resolve(null) : cli.jobsActive(),
      git.ahead(),
      git.artmindChanges(),
    ]);
    const readErrors: string[] = [];
    for (const result of [pending, tables]) {
      if (!result.ok) readErrors.push(`artmind ${result.args.join(" ")}: ${result.error}`);
    }
    for (const result of [status, pending, tables, active]) if (result && !result.ok) this.record(result);
    const ownWrite = this.now() - this.lastWriteAt < OWN_WRITE_GRACE_MS || this.inputs.activeJob !== null;
    let activeJob = this.inputs.activeJob;
    if (!activeJob && active?.ok && Array.isArray(active.json) && active.json.length) {
      activeJob = active.json[0] as JobStatus;
      this.watchers?.trackJob();
    }
    this.inputs = {
      status: status.ok ? (status.json as VaultStatus) : this.inputs.status,
      activeJob,
      pending: pending.ok ? (pending.json as IngestPending) : null,
      tablesPending: tables.ok ? (tables.json as TablePending[]) : null,
      git: { ahead, unsharedArtmindChanges: ownWrite ? 0 : changes.length },
      problem: status.ok ? null : { kind: "failed", command: "vault status", message: status.error ?? "failed" },
    };
    this.readErrors = readErrors;
    this.render();
  }

  // ── watchers' events ───────────────────────────────────────────────────────

  /** HEAD moved (a pull landed): refresh, then nudge if a store is behind (P2). */
  async onHeadSettled(): Promise<void> {
    await this.refresh();
    const status = this.inputs.status;
    if (status && this.state.states.some((s) => s.kind === "behind" && s.action.type === "sync")) {
      this.notify("pulled", pulledText(status), [{ label: "Sync now", run: () => void this.sync({ skipPull: true }) }]);
    }
  }

  onNewFiles(paths: string[]): void {
    this.notify("newFiles", newFilesText(paths), [{ label: "Ingest now", run: () => void this.ingestWhatChanged() }]);
  }

  /** One job poll. False once the job is done (and the notice shown). */
  async pollJob(): Promise<boolean> {
    const job = this.inputs.activeJob;
    if (!job) return false;
    const result = await this.deps.cli.jobStatus(job.job_id);
    if (!result.ok) {
      this.record(result);
      return true;
    }
    const current = result.json as JobStatus;
    if (["queued", "processing"].includes(current.status)) {
      this.inputs = { ...this.inputs, activeJob: current };
      this.render();
      return true;
    }
    this.inputs = { ...this.inputs, activeJob: null };
    this.markWrite();
    this.notify("ingestDone", ingestDoneText(current), [
      { label: "Details", run: () => void this.showJobDetails(current.job_id) },
      ...this.commitButtons(),
    ]);
    const queued = this.syncQueued;
    this.syncQueued = null;
    await this.refresh();
    if (queued) queued();
    return false;
  }

  async showJobDetails(jobId: string): Promise<void> {
    const result = await this.deps.cli.jobResults(jobId);
    this.record(result);
    this.jobResults = result.ok ? (result.json as JobResults) : null;
    this.render();
    this.deps.views.openPanel("job");
  }

  // ── actions ────────────────────────────────────────────────────────────────

  perform(action: Action): void {
    switch (action.type) {
      case "sync":
        void this.sync();
        return;
      case "ingest":
        void this.ingestWhatChanged();
        return;
      case "pull":
        this.gitAction("pull");
        return;
      case "openResolve":
        this.deps.views.openResolve();
        return;
      case "reviewTables":
        this.reviewTables();
        return;
      case "openPanel":
        this.deps.views.openPanel(action.section);
        return;
    }
  }

  /** An Obsidian Git command, or its instruction when it is missing. */
  gitAction(action: GitAction): boolean {
    if (this.deps.bridge.run(action)) return true;
    this.notify("error", this.deps.bridge.instruction(action));
    return false;
  }

  /** Obsidian Git's Pull, then `next` once HEAD settles. */
  async pullThen(next: () => void): Promise<void> {
    if (!this.gitAction("pull")) return;
    await this.watchers?.waitForPull();
    await this.refresh();
    next();
  }

  /** Sync (spec §4.2): offer a pull when the last one is old, then
   * `vault sync`; a refusal becomes the action that fixes it. */
  async sync(opts: { skipPull?: boolean; then?: () => void } = {}): Promise<void> {
    if (!opts.skipPull && this.watchers?.pullIsOld() && this.deps.bridge.has("pull")) {
      this.notify("prompt", "Pull from GitHub first?", [
        { label: "Pull, then sync", run: () => void this.pullThen(() => void this.sync({ ...opts, skipPull: true })) },
        { label: "Sync without pulling", run: () => void this.sync({ ...opts, skipPull: true }) },
      ]);
      return;
    }
    if (this.inputs.activeJob) {
      this.queueSync(opts);
      return;
    }
    this.markWrite();
    const result = await this.deps.cli.sync();
    this.record(result);
    this.markWrite();
    if (result.ok) {
      const summary = syncDoneText(result.json as SyncResult);
      this.lastSync = { at: this.now(), ok: true, summary };
      this.notify("syncDone", summary);
      await this.refresh();
      opts.then?.();
      return;
    }
    this.lastSync = { at: this.now(), ok: false, summary: result.error ?? "failed" };
    const refusal = classifyRefusal(result.error ?? "");
    switch (refusal.kind) {
      case "worker_running":
        this.queueSync(opts);
        break;
      case "resolve_first":
        this.notify("prompt", refusal.text, [{ label: "Resolve", run: () => this.deps.views.openResolve() }]);
        break;
      case "pull_first":
        this.notify("prompt", refusal.text, [
          this.deps.bridge.has("pull")
            ? { label: "Pull", run: () => void this.pullThen(() => void this.refresh()) }
            : this.gitButton("pull", "Pull"),
        ]);
        break;
      case "no_bookmark":
        this.notify("prompt", refusal.text, [{ label: "Open panel", run: () => this.deps.views.openPanel("bootstrap") }]);
        break;
      default:
        this.notify("error", `Sync failed: ${refusal.text}`, [
          { label: "Details", run: () => this.deps.views.openPanel("status") },
        ]);
    }
    await this.refresh();
  }

  private queueSync(opts: { then?: () => void }): void {
    this.syncQueued = () => void this.sync({ skipPull: true, then: opts.then });
    this.notify("prompt", "Ingest in progress — will sync when it finishes");
    this.watchers?.trackJob();
  }

  /** Ingest what changed (spec §4.4): pull and sync first when needed, then
   * one background job for exactly `ingest pending`'s files. */
  async ingestWhatChanged(opts: { skipPull?: boolean; skipSync?: boolean } = {}): Promise<void> {
    const state = this.state;
    const blocked = state.blocked.ingest;
    if (blocked && blocked !== "Nothing new or changed to ingest") {
      this.notify("prompt", blocked);
      return;
    }
    if (!opts.skipPull && this.watchers?.pullIsOld() && this.deps.bridge.has("pull")) {
      this.notify("prompt", "Pull from GitHub first?", [
        { label: "Pull, then ingest", run: () => void this.pullThen(() => void this.ingestWhatChanged({ ...opts, skipPull: true })) },
        { label: "Ingest without pulling", run: () => void this.ingestWhatChanged({ ...opts, skipPull: true }) },
      ]);
      return;
    }
    if (!opts.skipSync && state.states.some((s) => s.kind === "behind" && s.action.type === "sync")) {
      const next = { skipPull: true, skipSync: true };
      this.notify("prompt", "Sync first? The other laptop's changes aren't applied yet.", [
        { label: "Sync, then ingest", run: () => void this.sync({ skipPull: true, then: () => void this.ingestWhatChanged(next) }) },
        { label: "Ingest anyway", run: () => void this.ingestWhatChanged(next) },
      ]);
      return;
    }
    if (state.warnings.ingest) this.notify("prompt", state.warnings.ingest);
    this.markWrite();
    const result = await this.deps.cli.ingestAsyncPending();
    this.record(result);
    if (!result.ok) {
      this.notify("error", `Ingest failed: ${result.error}`);
      return;
    }
    const submitted = result.json as Submitted;
    if (!submitted.job_id) {
      this.notify("prompt", "Nothing new or changed to ingest.");
      await this.refresh();
      return;
    }
    this.inputs = {
      ...this.inputs,
      activeJob: {
        job_id: submitted.job_id,
        status: "queued",
        file_count: submitted.file_count,
        processed_count: 0,
        files: [],
      },
    };
    this.watchers?.trackJob();
    this.render();
  }

  // ── review screens ─────────────────────────────────────────────────────────

  reviewTables(table: string | null = null): void {
    const first = (this.inputs.tablesPending ?? []).find((t) => t.reason !== "ambiguous");
    this.deps.views.openTableReview(table ?? first?.table ?? null);
  }

  async tableDryRun(table: string): Promise<{ report: TableReport | null; error: string | null }> {
    const result = await this.deps.cli.tableDryRun(table);
    this.record(result);
    const report = result.ok ? ((result.json as { tables: TableReport[] }).tables[0] ?? null) : null;
    return { report, error: result.ok ? null : result.error };
  }

  async tableProject(table: string): Promise<boolean> {
    this.markWrite();
    const result = await this.deps.cli.tableProject(table);
    this.record(result);
    this.markWrite();
    if (!result.ok) {
      this.notify("error", `table2graph failed: ${result.error}`);
      return false;
    }
    const report = (result.json as { tables: TableReport[] }).tables[0];
    this.notify("table2graphDone", projectedText(report), this.commitButtons());
    await this.refresh();
    return true;
  }

  async resolvePreview(): Promise<{ report: ResolveReport | null; error: string | null }> {
    const result = await this.deps.cli.resolve(true);
    this.record(result);
    return { report: result.ok ? (result.json as ResolveReport) : null, error: result.ok ? null : result.error };
  }

  /** `vault resolve`, then [Complete the merge] once nothing is left for
   * the user to resolve by hand (spec §4.5). */
  async resolveApply(): Promise<boolean> {
    this.markWrite();
    const result = await this.deps.cli.resolve(false);
    this.record(result);
    this.markWrite();
    if (!result.ok) {
      this.notify("error", `vault resolve failed: ${result.error}`);
      return false;
    }
    const report = result.json as ResolveReport;
    const left = report.reported.length + report.pending.length;
    this.notify(
      "resolveDone",
      resolvedText(left),
      left ? [] : [this.gitButton("commit", "Complete the merge")],
    );
    await this.refresh();
    return true;
  }

  async runDoctor(): Promise<void> {
    const result = await this.deps.cli.doctor();
    this.record(result);
    this.doctor = result.json && typeof result.json === "object" ? (result.json as DoctorReport) : null;
    if (!this.doctor) this.notify("error", `vault doctor failed: ${result.error}`);
    this.render();
    this.deps.views.openPanel("doctor");
  }
}
```

- [ ] **Step 3: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  10 passed (10); Tests  155 passed (155)`; the build succeeds.

- [ ] **Step 4: Commit**

```bash
git add obsidian/artmind-obsidian/src/controller.ts obsidian/artmind-obsidian/test/controller.test.ts
git commit -m "feat(obsidian): the flows -- sync, ingest what changed, jobs, reviews, refusals as actions" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: the views — status bar, side panel, review modals, notices, settings tab (spec §3, §5)

Every view renders what it is given and calls back; none decides anything. They build DOM with the standard API (`views/dom.ts`), not Obsidian's `createEl` helpers, so the same code renders in jsdom for the tests.

- `StatusBarItem`: the primary label, then the secondary indicators; a tone class; the click performs the primary action. It keeps Obsidian's own `status-bar-item` class.
- `ObsidianNotifier`: a notice with buttons stays until a button (which closes it) or a dismissal; a plain one fades (6 s; errors 10 s).
- `renderPanel`/`ArtmindView`: the problem (where artmind was looked for, or the version needed, with [Set path]); status (every applying state, HEAD, both bookmarks, pending counts, files to ingest, tables for the graph, commits to push, the last sync); the bootstrap step when there is no bookmark; the actions, each disabled with its reason, [Commit-and-sync] when there are changes to share (or its instruction), and Obsidian Git's warning line; the job and the last job's failures; the tables waiting, each opening its review; the doctor's findings, each with its fix and [Copy] (never run: they are git commands, P5); the last 20 runs, each expanding to its raw output.
- `renderTableReview`/`TableReviewModal` (§5.1): header (table, domain, mapping, rows), per entity class (built, kept, each skip reason and count), lookups (resolved, stubbed, the first ten unresolved or ambiguous values, then "show all"), warnings; [Project to graph], [Open mapping], [Cancel]. With no mapping (or any dry-run failure): the reason, and [Ask admin-ui to create one].
- `renderResolve`/`ResolveModal` (§5.2): resolved (the side, in words, and the rule), pending, reported (each a link); [Resolve] disabled while anything is pending, with the reason.
- `ArtmindSettingTab`: the settings of Task 7.

**Files:**
- Create: `obsidian/artmind-obsidian/src/views/dom.ts`, `statusBar.ts`, `notices.ts`, `panel.ts`, `tableReview.ts`, `resolveModal.ts`, `settingsTab.ts`; `test/views.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/views.test.ts`:

```ts
// @vitest-environment jsdom
// The same module `obsidian` resolves to in tests (vitest.config.ts), imported
// by path for its test-only record of the notices shown.
import { Notice } from "./mocks/obsidian";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Snapshot } from "../src/controller";
import { type StateInputs, vaultState } from "../src/state";
import { ObsidianNotifier } from "../src/views/notices";
import { type PanelHandlers, renderPanel } from "../src/views/panel";
import { ResolveModal, renderResolve, resolveBlockedReason } from "../src/views/resolveModal";
import { StatusBarItem } from "../src/views/statusBar";
import { LOOKUP_PREVIEW, TableReviewModal, renderTableProblem, renderTableReview } from "../src/views/tableReview";
import { fixture } from "./fixtures";

function snapshot(overrides: Partial<StateInputs> = {}, extra: Partial<Snapshot> = {}): Snapshot {
  const inputs: StateInputs = {
    status: fixture("vault-status.in-sync").json,
    activeJob: null,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    git: { ahead: 0, unsharedArtmindChanges: 0 },
    problem: null,
    ...overrides,
  };
  return {
    inputs,
    state: vaultState(inputs),
    activity: [],
    lastSync: null,
    doctor: null,
    jobResults: null,
    readErrors: [],
    bridgeWarning: null,
    gitMissing: {},
    looked: [],
    ...extra,
  };
}

function handlers(): PanelHandlers & { calls: string[] } {
  const calls: string[] = [];
  const record = (name: string) => (arg?: string) => calls.push(arg === undefined ? name : `${name}:${arg}`);
  return {
    calls,
    sync: record("sync"),
    ingest: record("ingest"),
    resolve: record("resolve"),
    doctor: record("doctor"),
    reviewTable: record("reviewTable"),
    commitAndSync: record("commitAndSync"),
    setPath: record("setPath"),
    copy: record("copy"),
  };
}

function text(root: HTMLElement): string {
  return root.textContent ?? "";
}

function button(root: HTMLElement, label: string): HTMLButtonElement {
  const found = [...root.querySelectorAll("button")].find((b) => b.textContent === label);
  if (!found) throw new Error(`no button "${label}"`);
  return found;
}

beforeEach(() => {
  Notice.shown.length = 0;
});

describe("StatusBarItem (spec §3.1)", () => {
  it("shows the primary state, its tone and the secondary indicators; a click runs its action", () => {
    const el = document.createElement("div");
    el.classList.add("status-bar-item");
    const performed: unknown[] = [];
    const item = new StatusBarItem(el, (action) => performed.push(action));

    item.render(snapshot({ status: fixture("vault-status.behind").json, git: { ahead: 3, unsharedArtmindChanges: 1 } }).state);

    expect(el.textContent).toBe("◉ 2 docs · 1 table behind  ↑ 3 to push  ✎ artmind changes to share");
    expect(el.classList.contains("artmind-tone-amber")).toBe(true);
    expect(el.classList.contains("status-bar-item")).toBe(true);
    el.click();
    expect(performed).toEqual([{ type: "sync" }]);

    item.render(snapshot().state);
    expect(el.textContent).toBe("◉ artmind");
    expect(el.classList.contains("artmind-tone-amber")).toBe(false);
    expect(el.classList.contains("artmind-tone-grey")).toBe(true);
  });
});

describe("notices (spec §3.3)", () => {
  it("a notice with buttons stays until a button closes it and runs it", () => {
    const run = vi.fn();

    new ObsidianNotifier().show("pulled", "Pulled from the other laptop — 2 docs behind.", [{ label: "Sync now", run }]);

    const notice = Notice.shown[0];
    expect(notice.duration).toBe(0);
    const holder = document.createElement("div");
    holder.appendChild(notice.message as DocumentFragment);
    expect(text(holder)).toBe("Pulled from the other laptop — 2 docs behind.Sync now");
    button(holder, "Sync now").click();
    expect(run).toHaveBeenCalledOnce();
    expect(notice.hidden).toBe(true);
  });

  it("a plain notice fades", () => {
    new ObsidianNotifier().show("syncDone", "Synced: 3 docs, 1 table, 2 curation records.");
    expect(Notice.shown[0].duration).toBe(6_000);
  });
});

describe("the side panel (spec §3.2)", () => {
  it("shows status, pending counts, and disables actions with the reason", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ status: fixture("vault-status.merge-conflict").json, git: { ahead: 2, unsharedArtmindChanges: 0 } }), h);

    expect(text(root)).toContain("⚠ resolve artmind conflicts");
    expect(text(root)).toContain("Commits to push2");
    expect(button(root, "Sync").disabled).toBe(true);
    expect(text(root)).toContain("A merge is in progress — finish it first");
    button(root, "Resolve").click();
    expect(h.calls).toEqual(["resolve"]);
  });

  it("lists the tables waiting, each opening its review", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ tablesPending: fixture("table2graph.pending").json }), h);

    const link = [...root.querySelectorAll("a")].find((a) => a.textContent === "team (general)")!;
    link.click();
    expect(h.calls).toEqual(["reviewTable:team"]);
    expect(text(root)).toContain("missing");
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

  it("offers Commit-and-sync for artmind changes to share, or the instruction when it is missing", () => {
    const root = document.createElement("div");
    const h = handlers();
    renderPanel(root, snapshot({ git: { ahead: 0, unsharedArtmindChanges: 2 } }), h);
    button(root, "Commit-and-sync").click();
    expect(h.calls).toEqual(["commitAndSync"]);

    renderPanel(
      root,
      snapshot({ git: { ahead: 0, unsharedArtmindChanges: 2 } }, { gitMissing: { commitAndSync: "Run Obsidian Git: Commit-and-sync" }, bridgeWarning: "Obsidian Git has no Commit-and-sync command" }),
      h,
    );
    expect(button(root, "Run Obsidian Git: Commit-and-sync").disabled).toBe(true);
    expect(text(root)).toContain("Obsidian Git has no Commit-and-sync command");
  });

  it("explains a missing artmind and offers Set path", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ problem: { kind: "missing", looked: ["/Users/me/.local/bin/artmind"] } }), h);

    expect(text(root)).toContain("Looked in: /Users/me/.local/bin/artmind");
    button(root, "Set path").click();
    expect(h.calls).toEqual(["setPath"]);
  });

  it("shows the bootstrap step when this laptop never synced", () => {
    const root = document.createElement("div");
    renderPanel(root, snapshot({ status: fixture("vault-status.no-bookmark").json }), handlers());
    expect(text(root)).toContain("artmind vault sync --bootstrapEmpty");
  });

  it("lists the activity, each run expanding to its raw output", () => {
    const root = document.createElement("div");
    const activity = [{ command: "artmind vault sync --compact", at: 0, ok: false, summary: "failed: boom", output: "raw stderr" }];

    renderPanel(root, snapshot({}, { activity }), handlers());

    const details = root.querySelector("details.artmind-run-failed")!;
    expect(details.querySelector("summary")!.textContent).toContain("artmind vault sync --compact — failed: boom");
    expect(details.querySelector("pre")!.textContent).toBe("raw stderr");
  });
});

describe("the table2graph review (spec §5.1)", () => {
  const report = fixture("table2graph.dry-run").json.tables[0];

  it("shows header, entity classes with skip reasons, lookups and warnings", () => {
    const root = document.createElement("div");
    renderTableReview(root, report, { project: vi.fn(), openMapping: vi.fn(), askAdminUi: vi.fn(), cancel: vi.fn() });

    expect(text(root)).toContain("Domain general · mapping /vault/.artmind/domains/table_mappings/team.yaml · 4 rows");
    expect(text(root)).toContain("person (PERSON): built 3, kept 3");
    expect(text(root)).toContain("skipped 1: required property 'full_name' is empty");
    expect(text(root)).toContain("manager: resolved 1, stubbed 1");
    expect(text(root)).toContain("unresolved: Zed Unknown");
    expect(root.querySelectorAll(".artmind-review-warnings .artmind-warning")).toHaveLength(report.warnings.length);
  });

  it("shows the first ten unresolved values, then all on request", () => {
    const root = document.createElement("div");
    const many = Array.from({ length: 13 }, (_, i) => `name ${i}`);
    const big = { ...report, lookups: { manager: { ...report.lookups.manager, unresolved_values: many } } };

    renderTableReview(root, big, { project: vi.fn(), openMapping: vi.fn(), askAdminUi: vi.fn(), cancel: vi.fn() });

    expect(text(root)).toContain(many.slice(0, LOOKUP_PREVIEW).join(", "));
    expect(text(root)).not.toContain("name 12");
    button(root, "show all 13").click();
    expect(text(root)).toContain("name 12");
  });

  it("projects, opens the mapping, or cancels", () => {
    const root = document.createElement("div");
    const h = { project: vi.fn(), openMapping: vi.fn(), askAdminUi: vi.fn(), cancel: vi.fn() };
    renderTableReview(root, report, h);

    button(root, "Project to graph").click();
    button(root, "Open mapping").click();
    button(root, "Cancel").click();

    expect(h.project).toHaveBeenCalledOnce();
    expect(h.openMapping).toHaveBeenCalledWith(report.mapping);
    expect(h.cancel).toHaveBeenCalledOnce();
  });

  it("with no mapping, says so and offers admin-ui", async () => {
    const root = document.createElement("div");
    const h = { project: vi.fn(), openMapping: vi.fn(), askAdminUi: vi.fn(), cancel: vi.fn() };
    const { errorText } = await import("../src/cli");

    renderTableProblem(root, "budget", errorText(fixture("table2graph.dry-run-no-mapping").stderr!)!, h);

    expect(text(root)).toContain("No table mapping matches budget.");
    button(root, "Ask admin-ui to create one").click();
    expect(h.askAdminUi).toHaveBeenCalledOnce();
  });

  it("the modal runs the dry run, and projects only on the click", async () => {
    const project = vi.fn(async () => true);
    const modal = new TableReviewModal({} as never, "team", {
      dryRun: async () => ({ report, error: null }),
      project,
      openMapping: vi.fn(),
      askAdminUi: vi.fn(),
    });

    await modal.onOpen();
    expect(project).not.toHaveBeenCalled();
    button(modal.contentEl, "Project to graph").click();
    expect(project).toHaveBeenCalledWith("team");
  });
});

describe("the resolve preview (spec §5.2)", () => {
  const report = fixture("vault-resolve.dry-run").json;

  it("groups resolved, pending and reported units", () => {
    const root = document.createElement("div");
    const open = vi.fn();
    renderResolve(root, report, { resolve: vi.fn(), open, cancel: vi.fn() });

    expect(text(root)).toContain("Resolved (1)");
    expect(text(root)).toContain(".artmind/data/kg/general/policy: takes the other laptop's side — the side extracted from the merged note's body");
    expect(text(root)).toContain("Pending (1)");
    expect(text(root)).toContain("Reported — resolve these in Obsidian (2)");
    [...root.querySelectorAll("a")].find((a) => a.textContent === "Notes/memo.md")!.click();
    expect(open).toHaveBeenCalledWith("Notes/memo.md");
  });

  it("disables Resolve while anything is pending, with the reason", () => {
    const root = document.createElement("div");
    renderResolve(root, report, { resolve: vi.fn(), open: vi.fn(), cancel: vi.fn() });

    expect(button(root, "Resolve").disabled).toBe(true);
    expect(text(root)).toContain("Waiting on 1 note you still have to resolve");
    expect(resolveBlockedReason({ ...report, pending: [] })).toBeNull();
  });

  it("the modal applies only on the click", async () => {
    const apply = vi.fn(async () => true);
    const clean = { ...report, pending: [] };
    const modal = new ResolveModal({} as never, { preview: async () => ({ report: clean, error: null }), apply, open: vi.fn() });

    await modal.onOpen();
    expect(apply).not.toHaveBeenCalled();
    button(modal.contentEl, "Resolve").click();
    expect(apply).toHaveBeenCalledOnce();
  });
});
```

Run: `cd obsidian/artmind-obsidian && npx vitest run test/views.test.ts`
Expected: the file fails to load — `Error: Failed to resolve import "../src/views/notices" from "test/views.test.ts". Does the file exist?` (`Test Files  1 failed (1)`).

- [ ] **Step 2: Implement**

**Create** `obsidian/artmind-obsidian/src/views/dom.ts`:

```ts
/** Plain DOM, so every view renders the same inside Obsidian and in jsdom. */
export function el<K extends keyof HTMLElementTagNameMap>(
  parent: HTMLElement | DocumentFragment | null,
  tag: K,
  options: { cls?: string; text?: string; attr?: Record<string, string> } = {},
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.cls) node.className = options.cls;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attr ?? {})) node.setAttribute(name, value);
  parent?.appendChild(node);
  return node;
}

/** A button that shows why it is disabled, when it is. */
export function actionButton(
  parent: HTMLElement,
  label: string,
  blockedReason: string | null,
  onClick: () => void,
): HTMLButtonElement {
  const button = el(parent, "button", { cls: "artmind-action", text: label });
  if (blockedReason) {
    button.disabled = true;
    button.title = blockedReason;
    el(parent, "span", { cls: "artmind-blocked", text: blockedReason });
  } else {
    button.addEventListener("click", () => onClick());
  }
  return button;
}
```

**Create** `obsidian/artmind-obsidian/src/views/statusBar.ts`:

```ts
import type { Action, Tone, VaultStateResult } from "../state";

const TONES: Tone[] = ["red", "amber", "blue", "grey", "spinner"];

/** The status bar item (spec §3.1): one state at a time; a click performs
 * that state's action. Secondary indicators follow the label. */
export class StatusBarItem {
  private el: HTMLElement;
  private action: Action | null = null;

  constructor(el: HTMLElement, onAction: (action: Action) => void) {
    this.el = el;
    el.classList.add("artmind-status");
    el.addEventListener("click", () => {
      if (this.action) onAction(this.action);
    });
  }

  render(state: VaultStateResult): void {
    this.el.textContent = [state.primary.label, ...state.secondary].join("  ");
    for (const tone of TONES) this.el.classList.remove(`artmind-tone-${tone}`);
    this.el.classList.add(`artmind-tone-${state.primary.tone}`);
    this.el.setAttribute("aria-label", state.primary.detail);
    this.action = state.primary.action;
  }
}
```

**Create** `obsidian/artmind-obsidian/src/views/notices.ts`:

```ts
import { Notice } from "obsidian";
import type { NoticeButton, Notifier } from "../controller";
import type { NoticeKind } from "../settings";
import { el } from "./dom";

/** Notices with buttons (spec §3.3). One with buttons stays until clicked
 * or dismissed; a plain one fades. Clicking a button closes its notice. */
export class ObsidianNotifier implements Notifier {
  show(kind: NoticeKind | "prompt" | "error", text: string, buttons: NoticeButton[] = []): void {
    const fragment = document.createDocumentFragment();
    el(fragment, "div", { cls: `artmind-notice artmind-notice-${kind}`, text });
    const row = buttons.length ? el(fragment, "div", { cls: "artmind-notice-buttons" }) : null;
    const notice = new Notice(fragment, buttons.length ? 0 : kind === "error" ? 10_000 : 6_000);
    for (const button of buttons) {
      el(row, "button", { text: button.label }).addEventListener("click", (event) => {
        event.stopPropagation();
        notice.hide();
        button.run();
      });
    }
  }
}
```

**Create** `obsidian/artmind-obsidian/src/views/panel.ts`:

```ts
import { ItemView, type WorkspaceLeaf } from "obsidian";
import type { Snapshot } from "../controller";
import { BOOTSTRAP_STEP, type PanelSection, behindParts, plural } from "../state";
import { actionButton, el } from "./dom";

export const VIEW_TYPE = "artmind-view";

export interface PanelHandlers {
  sync(): void;
  ingest(): void;
  resolve(): void;
  doctor(): void;
  reviewTable(table: string): void;
  commitAndSync(): void;
  setPath(): void;
  copy(text: string): void;
}

function short(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : "none";
}

function when(at: number): string {
  return new Date(at).toLocaleString();
}

function section(root: HTMLElement, id: PanelSection | "actions" | "activity", title: string): HTMLElement {
  const box = el(root, "section", { cls: `artmind-section artmind-section-${id}`, attr: { "data-section": id } });
  el(box, "h4", { text: title });
  return box;
}

function row(parent: HTMLElement, label: string, value: string): void {
  const line = el(parent, "div", { cls: "artmind-row" });
  el(line, "span", { cls: "artmind-row-label", text: label });
  el(line, "span", { cls: "artmind-row-value", text: value });
}

/** The side panel's content (spec §3.2), from a snapshot. Every state that
 * applies is shown; actions carry the reason they are disabled. */
export function renderPanel(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers): void {
  root.replaceChildren();
  const { inputs, state } = snapshot;
  const sync = inputs.status?.sync;
  const stores = sync?.stores ?? {};

  const problem = inputs.problem;
  if (problem || state.neo4jUnreachable || state.states.some((s) => s.kind === "error")) {
    const box = section(root, "error", "Problem");
    for (const item of state.states.filter((s) => s.kind === "error")) el(box, "p", { cls: "artmind-error", text: item.detail });
    if (problem?.kind === "missing") {
      el(box, "p", { text: `Looked in: ${problem.looked.join(", ") || "nowhere"}. Install artmind (uv tool install), or set its path.` });
    }
    if (problem?.kind === "too_old") {
      el(box, "p", { text: `Upgrade artmind to ${problem.need} or newer (just dev-install in the artmind checkout).` });
    }
    if (problem && problem.kind !== "failed") el(box, "button", { text: "Set path" }).addEventListener("click", () => handlers.setPath());
  }

  const status = section(root, "status", "Status");
  for (const item of state.states) el(status, "p", { cls: `artmind-state artmind-tone-${item.tone}`, text: `${item.label} — ${item.detail}` });
  if (!state.states.length) el(status, "p", { cls: "artmind-state artmind-tone-grey", text: `${state.primary.label} — ${state.primary.detail}` });
  row(status, "HEAD", short(sync?.head));
  row(status, "Graph bookmark", stores.graph ? `${short(stores.graph.bookmark)} (${stores.graph.state})` : "unknown");
  row(status, "Structured bookmark", stores.structured ? `${short(stores.structured.bookmark)} (${stores.structured.state})` : "unknown");
  row(status, "Pending", behindParts(stores).join(" · ") || "nothing");
  const pending = inputs.pending;
  row(status, "Files to ingest", pending ? String(pending.notes.length + pending.binaries.length + pending.tables.length) : "unknown");
  row(status, "Tables for the graph", inputs.tablesPending ? String(inputs.tablesPending.length) : "unknown");
  row(status, "Commits to push", inputs.git.ahead === null ? "no upstream" : String(inputs.git.ahead));
  row(status, "Last sync", snapshot.lastSync ? `${when(snapshot.lastSync.at)} — ${snapshot.lastSync.summary}` : "not this session");
  for (const error of snapshot.readErrors) el(status, "p", { cls: "artmind-error", text: error });
  for (const error of pending?.errors ?? []) el(status, "p", { cls: "artmind-error", text: `${error.path}: ${error.error}` });

  if (stores.graph?.state === "no_bookmark" || stores.structured?.state === "no_bookmark") {
    const box = section(root, "bootstrap", "First sync on this laptop");
    el(box, "p", { text: BOOTSTRAP_STEP });
  }

  const actions = section(root, "actions", "Actions");
  actionButton(actions, "Sync", state.blocked.sync, handlers.sync);
  actionButton(actions, "Ingest what changed", state.blocked.ingest === "Nothing new or changed to ingest" ? null : state.blocked.ingest, handlers.ingest);
  if (state.warnings.ingest) el(actions, "span", { cls: "artmind-warning", text: state.warnings.ingest });
  actionButton(actions, "Resolve", state.blocked.resolve, handlers.resolve);
  actionButton(actions, "Doctor", state.blocked.doctor, handlers.doctor);
  if (inputs.git.unsharedArtmindChanges) {
    const missing = snapshot.gitMissing.commitAndSync;
    actionButton(actions, missing ?? "Commit-and-sync", missing ? "Obsidian Git's Commit-and-sync is missing" : null, handlers.commitAndSync);
  }
  if (snapshot.bridgeWarning) el(actions, "p", { cls: "artmind-warning", text: snapshot.bridgeWarning });

  if (inputs.activeJob || snapshot.jobResults) {
    const box = section(root, "job", "Ingest job");
    const job = inputs.activeJob;
    if (job) el(box, "p", { text: `${job.job_id}: ${job.status}, ${job.processed_count} of ${plural(job.file_count, "file")} done` });
    const results = snapshot.jobResults;
    if (results) {
      el(box, "p", { text: `Last job ${results.job_id}: ${results.status}` });
      for (const file of results.files.filter((f) => f.status === "failed")) {
        el(box, "p", { cls: "artmind-error", text: `${file.filename}: ${file.error_message ?? "failed"}` });
      }
    }
  }

  const tables = section(root, "tables", "Tables for the graph");
  const waiting = inputs.tablesPending ?? [];
  if (!waiting.length) el(tables, "p", { text: "None waiting." });
  for (const table of waiting) {
    const line = el(tables, "div", { cls: "artmind-row" });
    const link = el(line, "a", { text: `${table.table} (${table.domain})`, attr: { href: "#" } });
    el(line, "span", { cls: "artmind-row-value", text: table.reason.replace(/_/g, " ") });
    link.addEventListener("click", (event) => {
      event.preventDefault();
      handlers.reviewTable(table.table);
    });
  }

  if (snapshot.doctor) {
    const box = section(root, "doctor", "Doctor findings");
    for (const check of snapshot.doctor.checks.filter((c) => c.status !== "ok")) {
      const finding = el(box, "div", { cls: `artmind-finding artmind-finding-${check.status}` });
      el(finding, "p", { text: `${check.status.toUpperCase()} ${check.name}: ${check.detail}` });
      if (check.fix) {
        const fix = check.fix;
        el(finding, "code", { text: fix });
        el(finding, "button", { text: "Copy" }).addEventListener("click", () => handlers.copy(fix));
      }
    }
    if (snapshot.doctor.checks.every((c) => c.status === "ok")) el(box, "p", { text: "Every check passed." });
  }

  const activity = section(root, "activity", "Activity");
  if (!snapshot.activity.length) el(activity, "p", { text: "No artmind runs yet." });
  for (const entry of snapshot.activity) {
    const details = el(activity, "details", { cls: entry.ok ? "artmind-run" : "artmind-run artmind-run-failed" });
    el(details, "summary", { text: `${new Date(entry.at).toLocaleTimeString()} ${entry.command} — ${entry.summary}` });
    el(details, "pre", { text: entry.output || "(no output)" });
  }
}

/** The right-sidebar view. It renders snapshots; it decides nothing. */
export class ArtmindView extends ItemView {
  private handlers: PanelHandlers;
  private last: Snapshot | null = null;

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
    renderPanel(this.contentEl, snapshot, this.handlers);
  }

  focus(section: PanelSection): void {
    this.contentEl.querySelector(`[data-section="${section}"]`)?.scrollIntoView({ block: "start" });
  }

  override async onOpen(): Promise<void> {
    if (this.last) this.update(this.last);
  }
}
```

**Create** `obsidian/artmind-obsidian/src/views/tableReview.ts`:

```ts
import { type App, Modal } from "obsidian";
import type { TableReport } from "../types";
import { plural } from "../state";
import { el } from "./dom";

export const LOOKUP_PREVIEW = 10;

export interface TableReviewHandlers {
  project(): void;
  openMapping(path: string): void;
  askAdminUi(): void;
  cancel(): void;
}

function list(parent: HTMLElement, values: string[], label: string): void {
  if (!values.length) return;
  const line = el(parent, "div", { cls: "artmind-values" });
  el(line, "span", { text: `${label}: ` });
  const shown = el(line, "span", { text: values.slice(0, LOOKUP_PREVIEW).join(", ") });
  if (values.length > LOOKUP_PREVIEW) {
    const more = el(line, "button", { cls: "artmind-show-all", text: `show all ${values.length}` });
    more.addEventListener("click", () => {
      shown.textContent = values.join(", ");
      more.remove();
    });
  }
}

/** The dry-run report (spec §5.1): header, per entity class, lookups,
 * warnings, and the three buttons. */
export function renderTableReview(root: HTMLElement, report: TableReport, handlers: TableReviewHandlers): void {
  root.replaceChildren();
  const header = el(root, "div", { cls: "artmind-review-header" });
  el(header, "h3", { text: report.table });
  el(header, "p", { text: `Domain ${report.domain} · mapping ${report.mapping ?? "(none)"} · ${plural(report.rows, "row")}` });

  const classes = el(root, "section", { cls: "artmind-review-entities" });
  el(classes, "h4", { text: "Entities" });
  for (const [alias, entity] of Object.entries(report.entities)) {
    const block = el(classes, "div", { cls: "artmind-review-entity" });
    el(block, "p", { text: `${alias} (${entity.class}): built ${entity.built}, kept ${entity.kept}` });
    for (const [reason, count] of Object.entries(entity.skipped)) {
      el(block, "p", { cls: "artmind-skip", text: `skipped ${count}: ${reason}` });
    }
  }

  const lookups = Object.entries(report.lookups ?? {});
  if (lookups.length) {
    const box = el(root, "section", { cls: "artmind-review-lookups" });
    el(box, "h4", { text: "Lookups" });
    for (const [alias, lookup] of lookups) {
      const block = el(box, "div", { cls: "artmind-review-lookup" });
      el(block, "p", { text: `${alias}: resolved ${lookup.resolved}, stubbed ${lookup.stubbed}` });
      list(block, lookup.unresolved_values, "unresolved");
      list(block, lookup.ambiguous_values, "ambiguous");
    }
  }

  if (report.warnings.length) {
    const box = el(root, "section", { cls: "artmind-review-warnings" });
    el(box, "h4", { text: "Not declared in the schema" });
    for (const warning of report.warnings) el(box, "p", { cls: "artmind-warning", text: warning });
  }

  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  el(buttons, "button", { cls: "mod-cta", text: "Project to graph" }).addEventListener("click", () => handlers.project());
  if (report.mapping) {
    const mapping = report.mapping;
    el(buttons, "button", { text: "Open mapping" }).addEventListener("click", () => handlers.openMapping(mapping));
  }
  el(buttons, "button", { text: "Cancel" }).addEventListener("click", () => handlers.cancel());
}

/** No mapping matches (or the dry run failed): say so, and offer admin-ui. */
export function renderTableProblem(root: HTMLElement, table: string, error: string, handlers: TableReviewHandlers): void {
  root.replaceChildren();
  el(root, "h3", { text: table });
  const noMapping = /no table mapping matches/.test(error);
  el(root, "p", {
    cls: noMapping ? "" : "artmind-error",
    text: noMapping ? `No table mapping matches ${table}. Writing one is agent work.` : error,
  });
  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  if (noMapping) el(buttons, "button", { cls: "mod-cta", text: "Ask admin-ui to create one" }).addEventListener("click", () => handlers.askAdminUi());
  el(buttons, "button", { text: "Cancel" }).addEventListener("click", () => handlers.cancel());
}

export interface TableReviewDeps {
  dryRun(table: string): Promise<{ report: TableReport | null; error: string | null }>;
  project(table: string): Promise<boolean>;
  openMapping(path: string): void;
  askAdminUi(): void;
}

/** `table2graph <table> --dryRun` shown before anything is written (P7). */
export class TableReviewModal extends Modal {
  private table: string | null;
  private deps: TableReviewDeps;

  constructor(app: App, table: string | null, deps: TableReviewDeps) {
    super(app);
    this.table = table;
    this.deps = deps;
  }

  override async onOpen(): Promise<void> {
    const table = this.table;
    if (!table) {
      el(this.contentEl, "p", { text: "No tables are waiting for the graph." });
      return;
    }
    el(this.contentEl, "p", { text: `Checking ${table} (dry run)…` });
    const { report, error } = await this.deps.dryRun(table);
    const handlers: TableReviewHandlers = {
      project: () => {
        this.close();
        void this.deps.project(table);
      },
      openMapping: (path) => this.deps.openMapping(path),
      askAdminUi: () => this.deps.askAdminUi(),
      cancel: () => this.close(),
    };
    if (report) renderTableReview(this.contentEl, report, handlers);
    else renderTableProblem(this.contentEl, table, error ?? "the dry run failed", handlers);
  }

  override onClose(): void {
    this.contentEl.replaceChildren();
  }
}
```

**Create** `obsidian/artmind-obsidian/src/views/resolveModal.ts`:

```ts
import { type App, Modal } from "obsidian";
import type { ResolveReport } from "../types";
import { plural } from "../state";
import { el } from "./dom";

export interface ResolveHandlers {
  resolve(): void;
  open(path: string): void;
  cancel(): void;
}

const SIDES = { ours: "this laptop's side", theirs: "the other laptop's side" } as const;

/** Why [Resolve] cannot run yet, or null (spec §5.2). */
export function resolveBlockedReason(report: ResolveReport): string | null {
  if (report.pending.length) return `Waiting on ${plural(report.pending.length, "note")} you still have to resolve`;
  if (!report.resolved.length) return "Nothing for artmind to resolve";
  return null;
}

/** `vault resolve --dryRun`, grouped: resolved, pending, reported. */
export function renderResolve(root: HTMLElement, report: ResolveReport, handlers: ResolveHandlers): void {
  root.replaceChildren();
  el(root, "h3", { text: "Resolve artmind conflicts" });

  const resolved = el(root, "section", { cls: "artmind-resolve-resolved" });
  el(resolved, "h4", { text: `Resolved (${report.resolved.length})` });
  for (const unit of report.resolved) {
    el(resolved, "p", { text: `${unit.unit}: takes ${SIDES[unit.side]} — ${unit.rule}` });
  }

  const pending = el(root, "section", { cls: "artmind-resolve-pending" });
  el(pending, "h4", { text: `Pending (${report.pending.length})` });
  for (const unit of report.pending) el(pending, "p", { text: `${unit.unit}: ${unit.why}` });

  const reported = el(root, "section", { cls: "artmind-resolve-reported" });
  el(reported, "h4", { text: `Reported — resolve these in Obsidian (${report.reported.length})` });
  for (const item of report.reported) {
    const line = el(reported, "p");
    const link = el(line, "a", { text: item.path, attr: { href: "#" } });
    link.addEventListener("click", (event) => {
      event.preventDefault();
      handlers.open(item.path);
    });
    el(line, "span", { text: ` — ${item.why}` });
  }

  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  const blocked = resolveBlockedReason(report);
  const resolve = el(buttons, "button", { cls: "mod-cta", text: "Resolve" });
  if (blocked) {
    resolve.disabled = true;
    el(buttons, "span", { cls: "artmind-blocked", text: blocked });
  } else {
    resolve.addEventListener("click", () => handlers.resolve());
  }
  el(buttons, "button", { text: "Cancel" }).addEventListener("click", () => handlers.cancel());
}

export interface ResolveDeps {
  preview(): Promise<{ report: ResolveReport | null; error: string | null }>;
  apply(): Promise<boolean>;
  open(path: string): void;
}

export class ResolveModal extends Modal {
  private deps: ResolveDeps;

  constructor(app: App, deps: ResolveDeps) {
    super(app);
    this.deps = deps;
  }

  override async onOpen(): Promise<void> {
    el(this.contentEl, "p", { text: "Reading the conflicts (dry run)…" });
    const { report, error } = await this.deps.preview();
    if (!report) {
      this.contentEl.replaceChildren();
      el(this.contentEl, "p", { cls: "artmind-error", text: error ?? "vault resolve --dryRun failed" });
      return;
    }
    renderResolve(this.contentEl, report, {
      resolve: () => {
        this.close();
        void this.deps.apply();
      },
      open: (path) => this.deps.open(path),
      cancel: () => this.close(),
    });
  }

  override onClose(): void {
    this.contentEl.replaceChildren();
  }
}
```

**Create** `obsidian/artmind-obsidian/src/views/settingsTab.ts`:

```ts
import { type App, type Plugin, PluginSettingTab, Setting } from "obsidian";
import { type ArtmindSettings, NOTICE_LABELS, type NoticeKind } from "../settings";
import { el } from "./dom";

export interface SettingsHost extends Plugin {
  artmindSettings: ArtmindSettings;
  saveSettings(): Promise<void>;
}

/** Settings → artmind (spec §7): the artmind path, poll intervals, a toggle
 * per notice kind, and the commit-and-sync offer (on by default, P6). */
export class ArtmindSettingTab extends PluginSettingTab {
  private host: SettingsHost;

  constructor(app: App, host: SettingsHost) {
    super(app, host);
    this.host = host;
  }

  override display(): void {
    const { containerEl } = this;
    containerEl.replaceChildren();
    const settings = this.host.artmindSettings;
    const save = () => void this.host.saveSettings();

    new Setting(containerEl)
      .setName("artmind path")
      .setDesc("The artmind executable. Empty: ~/.local/bin/artmind, then `uv tool dir`. Obsidian started from the Dock does not see your shell's PATH.")
      .addText((text) =>
        text.setPlaceholder("auto-detect").setValue(settings.artmindPath).onChange((value) => {
          settings.artmindPath = value.trim();
          save();
        }),
      );

    const seconds = (name: string, desc: string, key: "headPollSeconds" | "jobPollSeconds" | "newFileGroupSeconds") =>
      new Setting(containerEl)
        .setName(name)
        .setDesc(desc)
        .addText((text) =>
          text.setValue(String(settings[key])).onChange((value) => {
            const n = Number.parseInt(value, 10);
            if (Number.isFinite(n) && n > 0) {
              settings[key] = n;
              save();
            }
          }),
        );
    seconds("HEAD poll (seconds)", "How often to check whether a pull moved HEAD. Takes effect after a reload.", "headPollSeconds");
    seconds("Job poll (seconds)", "How often to read a running ingest job. Takes effect after a reload.", "jobPollSeconds");
    seconds("New-file grouping (seconds)", "Arrivals this close together share one notice. Takes effect after a reload.", "newFileGroupSeconds");

    new Setting(containerEl)
      .setName("Offer to commit-and-sync after artmind writes")
      .setDesc("After an ingest, table2graph or resolve, the result notice offers Obsidian Git's Commit-and-sync.")
      .addToggle((toggle) =>
        toggle.setValue(settings.offerCommitAndSync).onChange((value) => {
          settings.offerCommitAndSync = value;
          save();
        }),
      );

    new Setting(containerEl)
      .setName("admin-ui URL")
      .setDesc("Opened by \"Ask admin-ui to create one\" when no table mapping matches.")
      .addText((text) =>
        text.setValue(settings.adminUiUrl).onChange((value) => {
          settings.adminUiUrl = value.trim();
          save();
        }),
      );

    el(containerEl, "h3", { text: "Notices" });
    for (const kind of Object.keys(NOTICE_LABELS) as NoticeKind[]) {
      new Setting(containerEl).setName(NOTICE_LABELS[kind]).addToggle((toggle) =>
        toggle.setValue(settings.notices[kind]).onChange((value) => {
          settings.notices[kind] = value;
          save();
        }),
      );
    }
  }
}
```

- [ ] **Step 3: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  11 passed (11); Tests  173 passed (173)`; the build succeeds.

- [ ] **Step 4: Commit**

```bash
git add obsidian/artmind-obsidian/src/views obsidian/artmind-obsidian/test/views.test.ts
git commit -m "feat(obsidian): status bar, side panel, table2graph review, resolve preview, notices, settings tab" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: wiring — `main.ts` (spec §3.4, §7 dormancy, §8)

`onload` loads the settings, and stays **dormant** — no status bar, no commands, no polls — unless `.artmind/vault.yaml` exists (spec §7). Otherwise it builds the units, adds the status bar item, registers the panel view, the settings tab and the seven commands of §3.4, and waits for Obsidian's layout to be ready before it listens for new files (Obsidian fires `create` for every file while a vault loads), watches window focus, checks artmind and starts the polls. The manifest is re-read on every refresh, since no Obsidian event reports a change under `.artmind/`.

**Files:**
- Modify: `obsidian/artmind-obsidian/src/main.ts`
- Create: `obsidian/artmind-obsidian/test/main.test.ts`

- [ ] **Step 1: Write the failing tests**

**Create** `obsidian/artmind-obsidian/test/main.test.ts`:

```ts
// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import ArtmindPlugin from "../src/main";
import { FileSystemAdapter } from "./mocks/obsidian";

/** An Obsidian `app` whose vault does (or does not) hold `.artmind/vault.yaml`.
 * `onLayoutReady` callbacks are kept, not run: nothing here spawns artmind. */
function app(isArtmindVault: boolean) {
  const adapter = Object.assign(new FileSystemAdapter(), {
    exists: async (path: string) => isArtmindVault && path === ".artmind/vault.yaml",
    read: async () => 'ingest:\n  mappings:\n    - path: "Notes/**"\n      domain: general\n',
    getBasePath: () => "/vault",
  });
  const layoutReady: Array<() => unknown> = [];
  return {
    layoutReady,
    vault: { adapter, on: (name: string) => ({ name }) },
    workspace: { onLayoutReady: (fn: () => unknown) => layoutReady.push(fn), getLeavesOfType: () => [] },
  };
}

describe("ArtmindPlugin", () => {
  it("stays dormant outside an artmind vault: no status bar, no commands, no polls (spec §7)", async () => {
    const a = app(false);
    const plugin = new ArtmindPlugin(a as never, { id: "artmind" } as never) as any;

    await plugin.onload();

    expect(plugin.statusBarItems).toEqual([]);
    expect(plugin.commands).toEqual([]);
    expect(a.layoutReady).toEqual([]);
  });

  it("in an artmind vault, adds the status bar, the panel and every command (spec §3.4)", async () => {
    const a = app(true);
    const plugin = new ArtmindPlugin(a as never, { id: "artmind" } as never) as any;

    await plugin.onload();

    expect(plugin.statusBarItems).toHaveLength(1);
    expect(plugin.views).toEqual(["artmind-view"]);
    expect(plugin.commands.map((c: { name: string }) => c.name)).toEqual([
      "Sync",
      "Ingest what changed",
      "Show status",
      "Resolve artmind conflicts",
      "Run doctor",
      "Review tables for graph",
      "Open side panel",
    ]);
    expect(a.layoutReady).toHaveLength(1);
    expect(plugin.events).toEqual([]);
    plugin.onunload();
  });
});
```

Run: `cd obsidian/artmind-obsidian && npx vitest run test/main.test.ts`
Expected: `Tests  2 failed (2)` — `TypeError: plugin.onload is not a function`: Task 1's `main.ts` is an empty `Plugin` subclass (the test's `Plugin` stand-in has no `onload`).

- [ ] **Step 2: Implement**

**Replace in** `obsidian/artmind-obsidian/src/main.ts` (the whole file):

```ts
import { Plugin } from "obsidian";

/** artmind for Obsidian (spec 2026-09-30). Task 10 of the plugin plan wires
 * it up; until then it loads and does nothing. */
export default class ArtmindPlugin extends Plugin {}
```

**with:**

```ts
import { FileSystemAdapter, Plugin, parseYaml } from "obsidian";
import { ObsidianGitBridge, type CommandsLike } from "./bridge";
import { ArtmindCli, defaultDetectDeps, detectArtmind } from "./cli";
import { Controller, type Snapshot } from "./controller";
import { GitReader } from "./git";
import { type Mapping, VAULT_YAML, promptsOnArrival, readMappings } from "./manifest";
import { type ArtmindSettings, mergeSettings } from "./settings";
import type { PanelSection } from "./state";
import { Watchers } from "./watchers";
import { ObsidianNotifier } from "./views/notices";
import { ArtmindView, VIEW_TYPE } from "./views/panel";
import { ResolveModal } from "./views/resolveModal";
import { ArtmindSettingTab } from "./views/settingsTab";
import { StatusBarItem } from "./views/statusBar";
import { TableReviewModal } from "./views/tableReview";

/** Obsidian's private `app.commands` and `app.setting`, typed as far as used. */
interface AppInternals {
  commands?: CommandsLike;
  setting?: { open(): void; openTabById(id: string): void };
}

export default class ArtmindPlugin extends Plugin {
  artmindSettings!: ArtmindSettings;
  private controller: Controller | null = null;
  private watchers: Watchers | null = null;
  private statusBar: StatusBarItem | null = null;
  private mappings: Mapping[] = [];
  private last: Snapshot | null = null;

  override async onload(): Promise<void> {
    this.artmindSettings = mergeSettings(await this.loadData());
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
      notifier: new ObsidianNotifier(),
      views: {
        render: (snapshot) => this.render(snapshot),
        openPanel: (section) => void this.openPanel(section),
        openResolve: () => this.openResolve(),
        openTableReview: (table) => this.openTableReview(table),
      },
      settings: () => this.artmindSettings,
      detect: () => detectArtmind(this.artmindSettings.artmindPath, defaultDetectDeps()),
    });
    this.controller = controller;
    const watchers = new Watchers(
      {
        readHead: () => new GitReader(root).head(),
        onHeadSettled: () => void controller.onHeadSettled(),
        onNewFiles: (paths) => controller.onNewFiles(paths),
        promptsOnArrival: (path) => promptsOnArrival(this.mappings, path),
        refresh: () => {
          // Obsidian does not index `.artmind/`, so no event says the
          // manifest changed: re-read it whenever state is refreshed.
          void this.loadMappings();
          controller.scheduleRefresh();
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

    this.statusBar = new StatusBarItem(this.addStatusBarItem(), (action) => controller.perform(action));
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
        await controller.refresh();
      }
    });
  }

  override onunload(): void {
    this.watchers?.stop();
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.artmindSettings);
    const controller = this.controller;
    if (!controller) return;
    if (!(await controller.checkArtmind())) await controller.refresh();
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
    this.statusBar?.render(snapshot.state);
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
      if (leaf.view instanceof ArtmindView) leaf.view.update(snapshot);
    }
  }

  private async openPanel(section: PanelSection = "status"): Promise<void> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) ?? this.app.workspace.getLeaf(true);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
    if (leaf.view instanceof ArtmindView) {
      if (this.last) leaf.view.update(this.last);
      leaf.view.focus(section);
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
      askAdminUi: () => window.open(this.artmindSettings.adminUiUrl),
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

  private panelHandlers() {
    return {
      sync: () => void this.controller?.sync(),
      ingest: () => void this.controller?.ingestWhatChanged(),
      resolve: () => this.openResolve(),
      doctor: () => void this.controller?.runDoctor(),
      reviewTable: (table: string) => this.openTableReview(table),
      commitAndSync: () => void this.controller?.gitAction("commitAndSync"),
      setPath: () => {
        const setting = (this.app as unknown as AppInternals).setting;
        setting?.open();
        setting?.openTabById(this.manifest.id);
      },
      copy: (text: string) => void navigator.clipboard.writeText(text),
    };
  }

  /** Every action is also a command (spec §3.4). */
  private addCommands(): void {
    const commands: Array<[string, string, () => void]> = [
      ["sync", "Sync", () => void this.controller?.sync()],
      ["ingest-what-changed", "Ingest what changed", () => void this.controller?.ingestWhatChanged()],
      ["show-status", "Show status", () => void this.controller?.refresh().then(() => this.openPanel("status"))],
      ["resolve", "Resolve artmind conflicts", () => this.openResolve()],
      ["doctor", "Run doctor", () => void this.controller?.runDoctor()],
      ["review-tables", "Review tables for graph", () => this.controller?.reviewTables()],
      ["open-panel", "Open side panel", () => void this.openPanel("status")],
    ];
    for (const [id, name, callback] of commands) this.addCommand({ id, name, callback });
  }
}
```

- [ ] **Step 3: Run the tests and the build**

Run: `cd obsidian/artmind-obsidian && npm test && npm run build`
Expected: `Test Files  12 passed (12); Tests  175 passed (175)`; the build succeeds, and `grep -c 'require("obsidian")' main.js` prints `1` or more (external, provided by Obsidian).

- [ ] **Step 4: Commit**

```bash
git add obsidian/artmind-obsidian/src/main.ts obsidian/artmind-obsidian/test/main.test.ts
git commit -m "feat(obsidian): wire the plugin -- dormancy, status bar, panel, commands, watchers" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: docs, and everything once more

**Files:**
- Modify: `CLAUDE.md`, `docs/USER_GUIDE_TWO_LAPTOPS.md`

- [ ] **Step 1: `CLAUDE.md`**

**Replace in** `CLAUDE.md` (the repo layout table):

```markdown
| `artmind/opencode/` | opencode/ACP persona, seeded into the run folder. |
```

**with:**

```markdown
| `artmind/opencode/` | opencode/ACP persona, seeded into the run folder. |
| `obsidian/artmind-obsidian/` | The Obsidian plugin (TypeScript, esbuild, vitest). It only runs the `artmind` CLI with `--compact`; `test/fixtures/*.json` are that JSON, regenerated from the real CLI by `test/test_plugin_fixtures.py` (`ARTMIND_REGEN_PLUGIN_FIXTURES=1`). A CLI output change fails that test first: regenerate, then run `just obsidian-plugin-test`. Install with `just obsidian-plugin-install <vault>`. Spec: `docs/superpowers/specs/2026-09-30-obsidian-plugin-design.md`. |
```

**Replace in** `CLAUDE.md`:

```markdown
Not source, do not edit: `build/`, `artmind9.egg-info/`, `__pycache__/`.
```

**with:**

```markdown
Not source, do not edit: `build/`, `artmind9.egg-info/`, `__pycache__/`,
`obsidian/artmind-obsidian/node_modules/` and its build output `main.js`.
```

- [ ] **Step 2: The user guide**

**Replace in** `docs/USER_GUIDE_TWO_LAPTOPS.md` (the end of §3):

```markdown
---

## 4. Ingest and curation: the ideas you need
```

**with:**

````markdown
### 3.1 The same, from Obsidian

The artmind Obsidian plugin turns this section into clicks. Install it once,
from the artmind checkout, into the vault (Obsidian Git then carries it to the
other laptop):

```bash
just obsidian-plugin-install ~/artmind_vaults/my_vault
```

and enable **artmind** under Settings → Community plugins. Its status bar item
always shows where the vault stands and does the one thing that state needs
when clicked:

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
````

- [ ] **Step 3: Everything**

Run: `just obsidian-plugin-test && just obsidian-plugin-build`
Expected: `Test Files  12 passed (12); Tests  175 passed (175)`, no type errors, `main.js` written.

Run: `uv run --group dev pytest test/ -q`
Expected: `2951 passed, 14 skipped, 1 warning` — Plan 1's count; the fixture test still matches, since this plan changes no CLI output.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/USER_GUIDE_TWO_LAPTOPS.md
git commit -m "docs: the Obsidian plugin -- where it lives, how to use it" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---
## Live verification (user)

The spec's §9 checklist. Obsidian itself cannot be driven by a test; this is where the status bar, the notices, both modals and the Obsidian Git hand-offs are seen for real. Two vaults on one laptop stand in for laptops A and B, each with its own local Neo4j database, sharing one bare git remote. Ingesting costs LLM calls (a few small files).

### 0. Two laptops on one machine

```bash
cd ~/projects/artmind9 && just dev-install          # artmind 0.9.0 with Plan 1
artmind --version                                    # artmind 0.9.0
export LIVE=$HOME/plugin-live && rm -rf "$LIVE" && mkdir -p "$LIVE" && cd "$LIVE"
git init -q --bare -b main remote.git
```

Create two databases that hold nothing you need, `plugina` and `pluginb` (Neo4j Browser: `:use system`, `CREATE DATABASE plugina;`, `CREATE DATABASE pluginb;` — or two throwaway servers, as in Plan B's live verification).

Vault A (follow `docs/USER_GUIDE_TWO_LAPTOPS.md` §2.2, with `plugina`):

```bash
git clone -q "$LIVE/remote.git" "$LIVE/A" 2>/dev/null; cd "$LIVE/A"
git config user.email a@example.com && git config user.name A
artmind init --interactive          # plugina; no remote (origin is already the bare repo)
artmind setup
printf 'ingest:\n  mappings:\n    - path: "Notes/**"\n      domain: general\n    - path: "Team/**"\n      domain: general\n' >> .artmind/vault.yaml
mkdir Notes Team && echo "Acme's CEO is Ann Lee." > Notes/acme.md
git add -A && git commit -qm init && git push -q origin HEAD:main
artmind vault sync --bootstrapEmpty
```

Open `$LIVE/A` as a vault in Obsidian, install and enable Obsidian Git (sync method **merge**, pull on startup on), then:

```bash
cd ~/projects/artmind9 && just obsidian-plugin-install "$LIVE/A"
```

and enable **artmind** in Settings → Community plugins. Vault B: clone `remote.git` to `$LIVE/B`, `artmind init --interactive` (`pluginb`), `artmind setup`, `artmind vault sync --bootstrapEmpty`, open it in Obsidian as a second vault (Obsidian Git came with the clone's `.obsidian/`; the plugin arrives with A's next push — or install it the same way).

### 1. Status bar states (spec §3.1) — check each label, colour and click

- [ ] **To ingest**: A has synced but ingested nothing yet → `◉ 1 to ingest` (blue; `Notes/acme.md`). Create `Notes/beta.md`, switch away from Obsidian and back (a refresh) → `◉ 2 to ingest`. Click → *"Pull from GitHub first?"* (no pull seen yet): [Ingest without pulling] → `◌ ingesting 0/2` (spinner) → `◌ ingesting 1/2` → notice *"Ingested 2."* with [Details] and [Commit-and-sync].
- [ ] **Commit-and-sync hand-off (P6)**: click [Commit-and-sync] on that notice → Obsidian Git commits and pushes (its own notice).
- [ ] **In sync**: after that push → `◉ artmind` (grey), no secondary indicator. Click → the side panel opens at Status.
- [ ] **Behind + the "Pulled…" notice (P2)**: in B, run Obsidian Git: Pull → within ~30 s (two HEAD readings) `◉ 2 docs behind` (amber) and the notice *"Pulled from the other laptop — 2 docs behind."* with [Sync now]. Nothing syncs until clicked. Click [Sync now] → *"Synced: 2 docs, 0 tables, 0 curation records."*, then `◉ artmind`.
- [ ] **Pull, then sync (§4.2)**: wait five minutes in B, make another change in A and push; in B click the amber item (or run *artmind: Sync*) → *"Pull from GitHub first?"* → [Pull, then sync] runs Obsidian Git's Pull, waits for HEAD, then syncs.
- [ ] **New-file notice (§4.3) and table → graph**: in A, copy a small `team.csv` (columns `name,employer`) into `Team/` → within 10 s *"1 new file in Team."* with [Ingest now]. Click it → the job runs and loads the table. Then add `.artmind/domains/table_mappings/team.yaml` (as in Plan 1's live step 4) and switch away and back → `◉ 1 table → graph` (blue). Click → the review modal (§5.1): header, entity classes, lookups, warnings. [Open mapping] opens the YAML in your editor. [Project to graph] → *"team projected: N entities."* with [Commit-and-sync].
- [ ] **No mapping**: rename the mapping's `table:` to `nothing` and open *artmind: Review tables for graph* on the table from the panel → "No table mapping matches team." and [Ask admin-ui to create one] opens the admin-ui URL in the browser.
- [ ] **Ingest running blocks Sync**: start an ingest of several files in A, then click Sync in the panel → disabled with "An ingest job is running — sync after it finishes"; run *artmind: Sync* from the palette → *"Ingest in progress — will sync when it finishes"*, and the sync runs by itself when the job ends.
- [ ] **Merge conflict under `.artmind/` (§5.2)**: make both A and B ingest a change to the same note without syncing in between (the guide's "don't do this"), commit both, pull in B → Obsidian Git reports a conflict → `⚠ resolve artmind conflicts` (red). Click → the resolve preview: resolved units with their side and rule, the note under Reported with a link, [Resolve] disabled while a folder waits on its note. Resolve the note in Obsidian, reopen → [Resolve] enabled → *"Artmind files resolved."* with [Complete the merge] → Obsidian Git commits the merge.
- [ ] **Changes to share**: in A, run a curation in the admin-ui (or `artmind ingest sync Notes/acme.md` in a terminal after an edit) → after 30 s, `✎ artmind changes to share`; the panel offers [Commit-and-sync].
- [ ] **Error — Neo4j unreachable (§8)**: stop A's Neo4j → switch back to Obsidian → `⚠ artmind` (red); the panel shows "Neo4j unreachable (neo4j://…)" (the URI, never the password); Sync and Review tables are disabled with that reason; Ingest what changed still runs, with a warning. Start Neo4j again.
- [ ] **Error — artmind missing (§8)**: in the plugin's settings set the artmind path to `/nope` → `⚠ artmind`; the panel says where it looked, with [Set path]; every action is disabled. Clear the setting → back to normal.
- [ ] **Error — too old**: point the setting at a script that prints `artmind 0.8.0` (`printf '#!/bin/sh\necho artmind 0.8.0\n' > /tmp/old-artmind && chmod +x /tmp/old-artmind`) → "artmind 0.8.0 is too old: this plugin needs 0.9.0 or newer". Clear the setting.

### 2. The rest of the surfaces

- [ ] **Command palette (§3.4)**: all seven *artmind:* commands exist and work: Sync, Ingest what changed, Show status, Resolve artmind conflicts, Run doctor, Review tables for graph, Open side panel.
- [ ] **Doctor**: `git config pull.rebase true` in A, *artmind: Run doctor* → the panel lists "FAIL pull.rebase" with `git config pull.rebase false` and [Copy]; the plugin does not run it. Run it yourself, doctor again → fixed.
- [ ] **Activity**: the panel's Activity lists the last runs; expanding one shows its raw output.
- [ ] **Obsidian Git missing**: disable Obsidian Git → the panel shows the warning line and buttons read "Run Obsidian Git: Pull" etc. Re-enable.
- [ ] **Dormancy (§7)**: open a vault with no `.artmind/` (any plain Obsidian vault) with the plugin enabled → no artmind status bar item, and no `git`/`artmind` processes from Obsidian (`ps aux | grep -E "artmind|git rev-parse"` while it sits idle).
- [ ] **Quitting mid-sync (§8)**: start a sync of a big change and quit Obsidian → `ps aux | grep "artmind vault sync"` shows it still running to completion; reopen → `◉ artmind`.

### Clean up

```bash
rm -rf "$LIVE"      # and drop the plugina / pluginb databases
```

---
## Self-review

### Spec → tasks

| Spec item | Task(s) |
|---|---|
| P1 — a native plugin running the `artmind` CLI with `--compact` | 2 (`ArtmindCli`), 10 |
| P2 — after a pull, nudge, never sync by itself | 6 (HEAD settle), 8 (`onHeadSettled`, test "never syncs by itself") |
| P3/P4 — ingest as a background job, from "Ingest what changed" and the new-file notice; notes only count | 6 (`promptsOnArrival`, grouping), 8 (`ingestWhatChanged`, `pollJob`) |
| P5 — never writes git; triggers Obsidian Git | 4 (`GIT_READS`, test "only ever reads"), 5, 8 (`gitAction`) |
| P6 — offer Commit-and-sync after artmind writes, on by default | 7 (default), 8 (`commitButtons`, tests) |
| P7 — nothing writes to the graph without a click | 8 (refresh runs only reads; writes only from click handlers), 9 (modals project/resolve only on the button) |
| P8 — desktop only | 1 (`isDesktopOnly: true`) |
| P9 — lives in this repo | 1 (`obsidian/artmind-obsidian/`) |
| §3.1 — status bar: the seven states, labels, colours, clicks, priority | 3 (`vaultState`, pairwise priority tests), 9 (`StatusBarItem`) |
| §3.1 — secondary indicators `↑ N to push`, `✎ artmind changes to share` | 3, 4 (`ahead`, `artmindChanges`), 8 (the 30 s rule) |
| §3.2 — side panel: status, pending counts, actions disabled with reasons, tables, doctor findings with [Copy], activity (last 20, expandable) | 3 (`blocked`), 8 (activity), 9 (`renderPanel`) |
| §3.3 — the six notices and their buttons | 7 (texts), 8 (when and with which buttons), 9 (`ObsidianNotifier`) |
| §3.4 — the seven commands | 10 |
| §4.1 — HEAD poll every 15 s, one more unchanged reading, then status; refresh after every action and on focus | 6, 8, 10 |
| §4.2 — pull first when the last pull is older than 5 minutes; `vault sync`; refusals become actions (queue, Resolve, Pull, bootstrap step not run) | 3 (`classifyRefusal`), 6 (`pullIsOld`, `waitForPull`), 8 |
| §4.3 — create events filtered to mapped folders, `vault.yaml` read through the adapter, binary/table types prompt, 10 s grouping, notes never prompt | 6, 10 (`loadMappings`, `create` after layout ready) |
| §4.4 — sync first, `ingest async --pending`, job-status every 3 s, finish notice, tables → graph afterwards | 6, 8 |
| §4.5 — the Obsidian Git hand-offs; noticing writes the plugin didn't make; ids looked up (with fallbacks), missing → instruction + warning | 4, 5, 8, 9 |
| §5.1 — the `table2graph` review: header, per class, lookups (first ten, show all), warnings, three buttons, the no-mapping case | 8 (`tableDryRun`, `tableProject`), 9 |
| §5.2 — the resolve preview: resolved/pending/reported, [Resolve] disabled while pending, then [Complete the merge] | 8 (`resolveApply`), 9 |
| §7 — units: `ArtmindCli` (queue, timeouts, path detection), `VaultState` (pure), `Watchers`, `ObsidianGitBridge`, Views, `Settings` | 2, 3, 6, 5, 9, 7 |
| §7 — `just obsidian-plugin-install <vault>` (plus build and test recipes) | 1 |
| §7 — dormancy outside an artmind vault | 10 (`main.test.ts`), 6 (nothing before `start()`) |
| §8 — artmind missing/too old, Neo4j unreachable (URI shown, sync and tables disabled, ingest warned), failures and timeouts to Activity, no automatic retry, refusals mapped, Obsidian Git missing, quitting mid-sync | 2 (timeouts, detached writes), 3, 8, 9, 10 |
| §9 — `VaultState` table-driven on real fixtures | 3 (fixtures from Plan 1 Task 6) |
| §9 — `ArtmindCli` against a fake `artmind`: fixture JSON, non-zero exits, stderr, delays; queue order, timeouts, path detection | 2 |
| §9 — `ObsidianGitBridge` with `app.commands` stubbed: present, renamed, missing | 5 |
| §9 — manual checklist | "Live verification (user)" |

### Placeholder scan

No "TBD", no "similar to Task N", no step without its code or its command. Every file is given whole; the replacements (`justfile`, `main.ts`, `CLAUDE.md`, the guide) quote their exact old text and were matched by the verification script.

### Type and name consistency

`CliResult` (`args`, `exitCode`, `ok`, `json`, `stdout`, `stderr`, `error`, `timedOut`, `startedAt`, `durationMs`) and `ArtmindCli`'s named methods (Task 2) are exactly `CliLike` in `controller.ts` (Task 8), which the fake in `controller.test.ts` implements. `StateInputs`, `VaultStateResult`, `Action`, `PanelSection`, `ArtmindProblem`, `behindParts`, `plural`, `BOOTSTRAP_STEP` (Task 3) are used by `texts.ts` (7), `controller.ts` (8) and the views (9). `GitAction`, `CommandsLike` (5) are used by the controller's `BridgeLike` and `main.ts`. `Snapshot` (8) is what `renderPanel` (9) takes; `NoticeButton`/`Notifier` (8) is what `ObsidianNotifier` (9) implements. `types.ts` field names are the fixtures' keys.

### Design decisions the spec did not settle (for review)

1. **`Controller`.** Spec §7's table has `Views` "call actions" and `VaultState` decide states, but the flows of §4 (pull-first prompts, queues, refusal handling, P6 buttons) need state across calls and the other units. Putting them in `main.ts` would make them untestable without Obsidian; a controller with narrow interfaces (`CliLike`, `GitLike`, `BridgeLike`, `Notifier`, `ViewsLike`, `WatchersLike`) is tested with recorders.
2. **`GitReader`** as its own file: the spec lists git reads under several units; one reader with a test that none of its commands writes is the cheapest proof of P5.
3. **Refusals from text.** Classified by phrases of `vault sync`'s messages ("ingest worker is running", "is in progress in this vault" / "unresolved conflicts in", "is not a commit in this clone" / "is not an ancestor of HEAD", "bookmark recorded yet"), tested on the real boxed stderr. Rewording a message fails Plan 1's fixture test first. The panel predicts most refusals from `vault status` and disables Sync with the reason.
4. **Two states the spec's table lacks.** A bookmark not in HEAD's history (`◉ pull first` → Obsidian Git's Pull) and no bookmark (`◉ sync not set up` → the panel's bootstrap step). Both are amber and rank with "behind": they are the reasons Sync cannot run yet. A failing `vault status` and a store in state `error` are the error state.
5. **Ambiguous tables** (two mappings match) are shown in the panel but do not make `◉ N table → graph`: clicking could only show `table2graph`'s refusal.
6. **Timeouts** (see decision 6 in the header list). Nothing retries automatically (§8); a timed-out write is killed, and the next action starts from a fresh status.
7. **PATH for the child**: appended, never replacing the user's own; `NO_COLOR=1` keeps rich-click plain.
8. **"The last pull"** is what the HEAD poll saw or a plugin-run Pull; a pull outside the plugin that moved nothing is invisible, so "Pull first?" may be offered once more than strictly needed. It never skips a needed pull.
9. **30 s ownership window** for `.artmind/` changes, and a running plugin-tracked job counts as the plugin's write: the worker writes for minutes after the submission.
10. **Sliding 10 s grouping**, so copying a folder of files over 30 s gives one notice. With no mappings at all nothing prompts (artmind itself would ingest everything; the spec says "filtered to mapped folders").
11. **[Complete the merge]** is offered only when `vault resolve` left nothing pending or reported: Obsidian Git's "Commit all changes" would otherwise commit conflict markers. Otherwise the notice says how many files to resolve in Obsidian, and the user runs Obsidian Git's Commit when done.
12. **[Open mapping] uses the system editor** (Electron `shell.openPath`): `.artmind/` is invisible to Obsidian, and a YAML editor is where a mapping is edited anyway.
13. **Minimum artmind 0.9.0**; an artmind without `--version` is "too old", not "missing".
14. **Notes and the `create` event.** Obsidian fires `create` for every file while a vault loads, so the listener is registered in `onLayoutReady`. A binary added while Obsidian is closed does not prompt; it is counted by `ingest pending` on the next refresh.
15. **The manifest is re-read on every refresh**, since Obsidian does not report changes under `.artmind/`.

### Accepted residual risk

- **Obsidian's private APIs.** `app.commands` and `app.setting` are not in the public typings; they have been stable for years, and a missing one only disables the Git buttons (instructions shown) or the [Set path] shortcut.
- **Obsidian Git's command semantics.** "Commit all changes" concluding a merge, and Pull settling HEAD within a minute, are verified only by the live checklist.
- **`npm audit`** reports advisories in the dev toolchain; nothing from `node_modules` ships (esbuild bundles only `src/`).

---

## Verification record

- Worktree: the same `obsidian-plans-applied` worktree, with Plan 1 already applied (commits `84c4584`…`cb4ca91`). A script parsed this file and applied, per task, every `**Create**` (with `(executable)` setting the mode bit), `**Append to**` and `**Replace in** … **with:**` block (a replacement must match exactly once). For each task it first applied only the non-`src/` blocks (tests, config) and ran the new tests — the "Expected: FAIL" text above is that output — then applied the source files, ran `npm test` and `npm run build` in `obsidian/artmind-obsidian`, and committed with the task's own message. Task 1 ran `npm install` after its toolchain step. Task 11 also ran the full Python suite.
- Results after each task (`npm test`; every `npm run build` succeeded): Task 1: `4 passed (4)` (`fc0fbcc`); Task 2: `38 passed (38)` (`2237002`); Task 3: `77 passed (77)` (`abe8d8b`); Task 4: `83 passed (83)` (`af3c6e9`); Task 5: `89 passed (89)` (`4c96df3`); Task 6: `105 passed (105)` (`5beda90`); Task 7: `115 passed (115)` (`4ebed5c`); Task 8: `155 passed (155)` (`3ee0f3a`); Task 9: `173 passed (173)` (`f8eaaba`); Task 10: `175 passed (175)` (`63f82e6`); Task 11: `175 passed (175)` (`6d0c43a`). Task 11's Python suite: `2951 passed, 14 skipped, 1 warning`.
- `git show --stat fc0fbcc` lists no `node_modules/` or `main.js` (the plugin's `.gitignore`), and `test/fake-artmind.mjs` is committed with mode `100755`.
- Fail-first, re-checked on the fully applied tree by removing one task's source and re-running its tests, then restoring it: `src/state.ts`, `src/bridge.ts`, `src/controller.ts` removed → each test file fails to load (`Cannot find module '../src/…'`). Sabotaging one refusal phrase in `classifyRefusal` (`ingest worker is running` → `ingest worker runs`) → `Tests  1 failed | 38 passed (39)` in `state.test.ts`: the fixture-driven refusal table catches a drifted message.
- The worktree was removed afterwards; the branch `obsidian-plans-applied` was kept. Only the two plan files were committed to `master`.
