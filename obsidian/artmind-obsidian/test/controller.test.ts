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
