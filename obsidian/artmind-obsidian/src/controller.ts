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
  schedule?: (fn: () => void, ms?: number) => void;
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
    const schedule = this.deps.schedule ?? ((fn, ms) => void setTimeout(fn, ms ?? 500));
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
