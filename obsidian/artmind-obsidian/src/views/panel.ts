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
