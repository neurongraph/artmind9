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
