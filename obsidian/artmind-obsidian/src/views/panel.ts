import { ItemView, type WorkspaceLeaf } from "obsidian";
import type { Snapshot } from "../controller";
import { type Action, BOOTSTRAP_STEP, type PanelSection, behindParts, plural } from "../state";
import type { ChunkProgress, JobResults, JobStatus, KgCounts, StoreReport } from "../types";
import { actionButton, el } from "./dom";

export const VIEW_TYPE = "artmind-view";

export interface PanelHandlers {
  sync(): void;
  ingest(): void;
  resolve(): void;
  doctor(): void;
  reviewTable(table: string): void;
  retryJob(jobId: string): void;
  /** Open a page of this vault's admin console, starting it if need be. */
  openAdmin(path?: string): void;
  commitAndSync(): void;
  setPath(): void;
  copy(text: string): void;
}

/** What the panel remembers between renders: which sections are collapsed.
 * Every snapshot re-renders the panel, so `<details>` alone would forget. */
export interface PanelUi {
  collapsed: Set<string>;
}

/** Collapsed until opened: the detail most visits don't need. */
export const DEFAULT_COLLAPSED = ["status", "activity"];

export function newPanelUi(): PanelUi {
  return { collapsed: new Set(DEFAULT_COLLAPSED) };
}

function short(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : "none";
}

function when(at: number): string {
  return new Date(at).toLocaleString();
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** A collapsible section whose header carries a one-line summary (a count,
 * a badge), so the panel reads even with everything collapsed. */
function section(
  root: HTMLElement,
  ui: PanelUi,
  id: PanelSection | "activity",
  title: string,
  badge = "",
  badgeTone = "grey",
): HTMLElement {
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

function callout(root: HTMLElement, id: PanelSection, tone: "red" | "amber"): HTMLElement {
  return el(root, "div", { cls: `artmind-callout artmind-callout-${tone}`, attr: { "data-section": id } });
}

function row(parent: HTMLElement, label: string, value: string): void {
  const line = el(parent, "div", { cls: "artmind-row" });
  el(line, "span", { cls: "artmind-row-label", text: label });
  el(line, "span", { cls: "artmind-row-value", text: value });
}

/** The one button the header offers: whatever the top state asks for. */
function primaryAction(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers): void {
  const { state, inputs } = snapshot;
  const action: Action = state.primary.action;
  const box = el(root, "div", { cls: "artmind-primary" });
  const cta = (label: string, blocked: string | null, run: () => void) => {
    actionButton(box, label, blocked, run).classList.add("mod-cta");
  };
  switch (action.type) {
    case "sync":
      return cta("Sync", state.blocked.sync, handlers.sync);
    case "ingest":
      return cta("Ingest what changed", state.blocked.ingest, handlers.ingest);
    case "openResolve":
      return cta("Resolve", state.blocked.resolve, handlers.resolve);
    case "reviewTables": {
      const first = (inputs.tablesPending ?? []).find((t) => t.reason !== "ambiguous");
      if (first) cta(`Review ${first.table}`, state.blocked.tables, () => handlers.reviewTable(first.table));
      return;
    }
    case "pull":
      el(box, "span", { cls: "artmind-hint", text: "Pull from GitHub (Obsidian Git), then sync." });
      return;
    default:
      box.remove();
  }
}

/** The rest of the actions, small, each disabled with its reason as a tooltip. */
function toolbar(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers): void {
  const { state, inputs } = snapshot;
  const bar = el(root, "div", { cls: "artmind-toolbar" });
  const tool = (label: string, blocked: string | null, run: () => void) => {
    const button = el(bar, "button", { cls: "artmind-tool", text: label });
    if (blocked) {
      button.disabled = true;
      button.title = blocked;
    } else {
      button.addEventListener("click", () => run());
    }
    return button;
  };
  tool("Sync", state.blocked.sync, handlers.sync);
  tool("Ingest", state.blocked.ingest, handlers.ingest);
  tool("Resolve", state.blocked.resolve, handlers.resolve);
  tool("Doctor", state.blocked.doctor, handlers.doctor);
  tool("Admin ↗", state.blocked.admin, () => handlers.openAdmin("/")).title ||= "Open the admin console (starts it if need be)";
  if (inputs.git.artmindChanges.length) {
    const missing = snapshot.gitMissing.commitAndSync;
    tool(missing ?? "Commit-and-sync", missing ? "Obsidian Git's Commit-and-sync is missing" : null, handlers.commitAndSync);
  }
}

function header(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers): void {
  const { state } = snapshot;
  const box = el(root, "div", { cls: "artmind-header" });
  const health = el(box, "div", { cls: `artmind-health artmind-tone-${state.primary.tone}` });
  el(health, "span", { cls: "artmind-health-label", text: state.primary.label });
  if (state.secondary.length) el(health, "span", { cls: "artmind-health-secondary", text: state.secondary.join("  ") });
  el(box, "div", { cls: "artmind-health-detail", text: state.primary.detail });
  // The other states that apply, below the one the header leads with.
  for (const item of state.states.slice(1)) {
    el(box, "div", { cls: `artmind-state artmind-tone-${item.tone}`, text: `${item.label} — ${item.detail}` });
  }
  primaryAction(box, snapshot, handlers);
  if (state.warnings.ingest) el(box, "p", { cls: "artmind-warning", text: state.warnings.ingest });
  toolbar(box, snapshot, handlers);
  if (snapshot.bridgeWarning) el(box, "p", { cls: "artmind-warning", text: snapshot.bridgeWarning });
}

function problems(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers): void {
  const { inputs, state } = snapshot;
  const problem = inputs.problem;
  const errors = state.states.filter((s) => s.kind === "error");
  const readErrors = [
    ...snapshot.readErrors,
    ...(inputs.pending?.errors ?? []).map((e) => `${e.path}: ${e.error}`),
  ];
  if (problem || state.neo4jUnreachable || errors.length || readErrors.length) {
    const box = callout(root, "error", "red");
    for (const item of errors) el(box, "p", { cls: "artmind-error", text: item.detail });
    for (const error of readErrors) el(box, "p", { cls: "artmind-error", text: error });
    if (problem?.kind === "missing") {
      el(box, "p", { text: `Looked in: ${problem.looked.join(", ") || "nowhere"}. Install artmind (uv tool install), or set its path.` });
    }
    if (problem?.kind === "too_old") {
      el(box, "p", { text: `Upgrade artmind to ${problem.need} or newer (just dev-install in the artmind checkout).` });
    }
    if (problem && problem.kind !== "failed") el(box, "button", { text: "Set path" }).addEventListener("click", () => handlers.setPath());
  }
  const stores = inputs.status?.sync.stores ?? {};
  if (stores.graph?.state === "no_bookmark" || stores.structured?.state === "no_bookmark") {
    const box = callout(root, "bootstrap", "amber");
    el(box, "p", { cls: "artmind-callout-title", text: "First sync on this laptop" });
    el(box, "p", { text: BOOTSTRAP_STEP });
  }
}

// ── the ingest job card ──────────────────────────────────────────────────────

type CardFile = KgCounts & {
  filename: string;
  status: string;
  current_step?: string | null;
  error_message?: string | null;
  chunk_progress?: ChunkProgress;
};

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
 * scaled to the job's largest file) or, while extracting, its chunk progress. */
function jobCard(
  root: HTMLElement,
  ui: PanelUi,
  job: { job_id: string; status: string; file_count: number; files: CardFile[]; stalled?: boolean },
  live: boolean,
  handlers: PanelHandlers,
): void {
  const files = job.files;
  const counts = new Map(FILE_STATES.map((s) => [s.key, 0]));
  for (const f of files) counts.set(fileState(f.status), (counts.get(fileState(f.status)) ?? 0) + 1);
  const finished = (counts.get("done") ?? 0) + (counts.get("failed") ?? 0) + (counts.get("skipped") ?? 0);
  const failed = counts.get("failed") ?? 0;
  const stalled = live && Boolean(job.stalled);
  const title = stalled ? "Ingest job (stalled)" : live ? "Ingest job" : "Last ingest job";
  const tone = failed ? "red" : stalled ? "amber" : live ? "spinner" : "grey";
  const box = section(root, ui, "job", title, `${finished}/${plural(job.file_count, "file")}`, tone);

  // Retry: a stalled job (nothing will finish it), or a finished job's
  // failures. Never a job still running: retry-job would refuse it.
  if (stalled || (!live && failed)) {
    const bar = el(box, "div", { cls: `artmind-retry artmind-callout artmind-callout-${stalled ? "amber" : "red"}` });
    el(bar, "span", {
      text: stalled ? "The worker stopped mid-job; nothing will finish it." : `${plural(failed, "file")} failed.`,
    });
    const button = el(bar, "button", { cls: "mod-cta", text: stalled ? "Retry job" : "Retry failed" });
    button.addEventListener("click", () => {
      button.disabled = true;
      handlers.retryJob(job.job_id);
    });
  }

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

function job(root: HTMLElement, ui: PanelUi, snapshot: Snapshot, handlers: PanelHandlers): void {
  const active: JobStatus | null = snapshot.inputs.activeJob;
  if (active) return jobCard(root, ui, active, true, handlers);
  const results: JobResults | null = snapshot.jobResults;
  if (results) jobCard(root, ui, results, false, handlers);
}

// ── the collapsible detail ───────────────────────────────────────────────────

function storeBadge(name: string, report: StoreReport | undefined): string {
  if (!report) return `${name} ?`;
  return `${name} ${report.state === "current" ? "✓" : "⚠"}`;
}

function status(root: HTMLElement, ui: PanelUi, snapshot: Snapshot): void {
  const { inputs } = snapshot;
  const sync = inputs.status?.sync;
  const stores = sync?.stores ?? {};
  const behind = [stores.graph, stores.structured].some((s) => s && s.state !== "current");
  const box = section(root, ui, "status", "Sync", `${storeBadge("graph", stores.graph)} · ${storeBadge("structured", stores.structured)}`, behind ? "amber" : "grey");
  row(box, "HEAD", short(sync?.head));
  row(box, "Graph bookmark", stores.graph ? `${short(stores.graph.bookmark)} (${stores.graph.state})` : "unknown");
  row(box, "Structured bookmark", stores.structured ? `${short(stores.structured.bookmark)} (${stores.structured.state})` : "unknown");
  row(box, "Pending", behindParts(stores).join(" · ") || "nothing");
  const pending = inputs.pending;
  row(box, "Files to ingest", pending ? String(pending.notes.length + pending.binaries.length + pending.tables.length) : "unknown");
  row(box, "Tables for the graph", inputs.tablesPending ? String(inputs.tablesPending.length) : "unknown");
  row(box, "Commits to push", inputs.git.ahead === null ? "no upstream" : String(inputs.git.ahead));
  row(box, "Last sync", snapshot.lastSync ? `${when(snapshot.lastSync.at)} — ${snapshot.lastSync.summary}` : "not this session");
}

function tables(root: HTMLElement, ui: PanelUi, snapshot: Snapshot, handlers: PanelHandlers): void {
  const waiting = snapshot.inputs.tablesPending ?? [];
  const box = section(root, ui, "tables", "Tables for the graph", waiting.length ? String(waiting.length) : "none", waiting.length ? "blue" : "grey");
  if (!waiting.length) el(box, "p", { cls: "artmind-muted", text: "None waiting." });
  for (const table of waiting) {
    const line = el(box, "div", { cls: "artmind-row" });
    const link = el(line, "a", { text: `${table.table} (${table.domain})`, attr: { href: "#" } });
    el(line, "span", { cls: "artmind-row-value", text: table.reason.replace(/_/g, " ") });
    link.addEventListener("click", (event) => {
      event.preventDefault();
      handlers.reviewTable(table.table);
    });
  }
}

function doctor(root: HTMLElement, ui: PanelUi, snapshot: Snapshot, handlers: PanelHandlers): void {
  const report = snapshot.doctor;
  if (!report) return;
  const findings = report.checks.filter((c) => c.status !== "ok");
  const failing = findings.some((c) => c.status === "fail");
  const box = section(root, ui, "doctor", "Doctor", findings.length ? plural(findings.length, "finding") : "all ok", failing ? "red" : findings.length ? "amber" : "grey");
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

/** The side panel's content (spec §3.2), from a snapshot: a header that says
 * how things stand and offers the one action that matters, callouts only for
 * problems, the ingest job, then collapsible detail. Every state that
 * applies is shown; actions carry the reason they are disabled. */
export function renderPanel(root: HTMLElement, snapshot: Snapshot, handlers: PanelHandlers, ui: PanelUi = newPanelUi()): void {
  root.replaceChildren();
  root.classList.add("artmind-panel");
  header(root, snapshot, handlers);
  problems(root, snapshot, handlers);
  job(root, ui, snapshot, handlers);
  status(root, ui, snapshot);
  tables(root, ui, snapshot, handlers);
  doctor(root, ui, snapshot, handlers);
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

  /** Scroll to a row (`data-row`) or a section (`data-section`), opening a
   * collapsed section first. */
  focus(target: string): void {
    if (this.ui.collapsed.delete(target) && this.last) this.update(this.last);
    this.contentEl.querySelector(`[data-row="${target}"], [data-section="${target}"]`)?.scrollIntoView({ block: "start" });
  }

  override async onOpen(): Promise<void> {
    if (this.last) this.update(this.last);
  }
}
