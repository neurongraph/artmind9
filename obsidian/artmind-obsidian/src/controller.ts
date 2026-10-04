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
  type PanelTarget,
  type RowId,
  type StateInputs,
  classifyRefusal,
  plural,
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
    if (current.finalize?.state === "failed") this.notify("error", ingestDoneText(current), "graph");
    else this.notify("ingestDone", withNext(ingestDoneText(current), this.checklist), "documents");
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
    return this.guard("pull", "remote") && this.gitAction("pull", "remote");
  }

  /** The status bar and ribbon: the panel, at the current step (spec §4). */
  openPanelAtCurrent(): void {
    this.deps.views.openPanel(this.checklist.current ?? "remote");
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
    // Its output is gitignored: never point at Commit & push from here.
    const text = projectedText(report);
    this.notify("table2graphDone", this.checklist.current === "vault" ? text : withNext(text, this.checklist), "tables");
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
