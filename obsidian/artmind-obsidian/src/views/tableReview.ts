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
