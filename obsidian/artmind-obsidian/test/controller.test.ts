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
    files: (cleanStatus.files as Array<{ filename: string; status: string; entities: number; relationships: number }>).map((f) => ({ filename: f.filename, status: f.status, error_message: null, entities: f.entities, relationships: f.relationships })),
  };

  it("names the next step, keeps the job for the panel and saves it", async () => {
    const t = setup({ script: { projectionStatus: replay("projection-status.needs-rebuild") } });
    t.controller.inputs = { ...t.controller.inputs, activeJob: fixture("ingest-job-status.running").json };

    expect(await t.controller.pollJob()).toBe(false);

    expect(t.shown).toEqual([{ kind: "ingestDone", text: "Ingested 4 files, 1 failed. Next: retry failed.", target: "documents" }]);
    expect(t.cli.calls).toContain("jobResults 00000000-0000-4000-8000-000000000001");
    expect(t.controller.inputs.lastJob?.results?.files[0].entities).toBe(12);
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
    expect(t.controller.inputs.lastJob?.results?.job_id).toBe("00000000-0000-4000-8000-000000000001");
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
