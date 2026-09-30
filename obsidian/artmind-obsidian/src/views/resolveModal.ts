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
