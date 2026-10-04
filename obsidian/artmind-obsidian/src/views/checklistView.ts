import type { Row, RowAction, RowItem } from "../checklist";
import type { Snapshot } from "../controller";
import { LABELS, readinessText } from "../texts";
import { actionButton, el } from "./dom";
import { jobCard } from "./jobCard";
import { type PanelUi, basename } from "./sections";

export interface ChecklistHandlers {
  /** ↻: every read again, heavy ones included. */
  refresh(): void;
  run(action: RowAction): void;
  openAdmin(path?: string): void;
}

/** Items shown in a collapsed row's preview line. */
export const ITEM_PREVIEW = 3;

function itemText(item: RowItem, short: boolean): string {
  const name = short ? basename(item.text) : item.text;
  return item.tag ? `${name} (${item.tag})` : name;
}

/** `2024-12-31.md (changed) · Ideas.md (new) · +5`, opening to every item. */
function items(box: HTMLElement, row: Row, ui: PanelUi): void {
  const details = el(box, "details", { cls: "artmind-step-items", attr: { "data-items": row.id } });
  details.open = ui.expanded.has(row.id);
  details.addEventListener("toggle", () => {
    if (details.open) ui.expanded.add(row.id);
    else ui.expanded.delete(row.id);
  });
  const preview = row.items.slice(0, ITEM_PREVIEW).map((item) => itemText(item, true)).join(" · ");
  const more = row.items.length - ITEM_PREVIEW;
  el(details, "summary", { text: more > 0 ? `${preview} · +${more}` : preview });
  const list = el(details, "div", { cls: "artmind-step-item-list" });
  for (const item of row.items) {
    const line = el(list, "div", { cls: "artmind-step-item", text: itemText(item, false) });
    if (item.title) line.title = item.title;
  }
}

/** One row: glyph, title, summary, its buttons (the first filled on the
 * current row only, D3), its detail lines, its expandable items; row 2
 * also holds the job card. */
function renderRow(parent: HTMLElement, row: Row, current: boolean, snapshot: Snapshot, handlers: ChecklistHandlers, ui: PanelUi): void {
  const box = el(parent, "div", {
    cls: `artmind-step artmind-step-${row.state}${current ? " artmind-step-current" : ""}`,
    attr: { "data-row": row.id },
  });
  const line = el(box, "div", { cls: "artmind-step-line" });
  el(line, "span", { cls: `artmind-step-glyph artmind-tone-${row.tone}`, text: row.glyph });
  el(line, "span", { cls: "artmind-step-title", text: row.title });
  el(line, "span", { cls: "artmind-step-summary", text: row.summary });
  if (row.buttons.length) {
    const bar = el(box, "div", { cls: "artmind-step-buttons" });
    row.buttons.forEach((b, index) => {
      const button = actionButton(bar, b.label, b.blockedReason, () => handlers.run(b.action));
      button.classList.add(current && index === 0 ? "mod-cta" : "artmind-outline");
    });
  }
  for (const text of row.detail) el(box, "div", { cls: "artmind-step-detail", text });
  if (row.items.length) items(box, row, ui);
  if (row.id === "documents") {
    const active = snapshot.inputs.activeJob;
    const last = snapshot.inputs.lastJob;
    if (active) jobCard(box, ui, active, true, handlers);
    else if (last) jobCard(box, ui, last.results ?? last.status, false, handlers);
  }
}

/** The readiness checklist (checklist spec §3.1): header with ↻, rows 1–5,
 * then the Vault footer (hidden during a job). */
export function renderChecklist(root: HTMLElement, snapshot: Snapshot, handlers: ChecklistHandlers, ui: PanelUi): void {
  const cl = snapshot.checklist;
  const head = el(root, "div", { cls: "artmind-header" });
  const top = el(head, "div", { cls: "artmind-header-top" });
  const vault = snapshot.inputs.status?.vault;
  el(top, "span", { cls: "artmind-header-vault", text: vault ? `artmind · ${basename(vault)}` : "artmind" });
  const refresh = el(top, "button", { cls: "artmind-refresh clickable-icon", text: LABELS.refresh, attr: { "aria-label": "Re-read everything" } });
  refresh.addEventListener("click", () => handlers.refresh());
  const current = cl.rows.find((r) => r.id === cl.current);
  el(head, "div", { cls: `artmind-readiness artmind-tone-${cl.ready ? "grey" : (current?.tone ?? "grey")}`, text: readinessText(cl) });
  if (snapshot.bridgeWarning) el(head, "p", { cls: "artmind-warning", text: snapshot.bridgeWarning });

  const list = el(root, "div", { cls: "artmind-checklist" });
  for (const row of cl.rows) {
    if (row.id !== "vault") renderRow(list, row, row.id === cl.current, snapshot, handlers, ui);
  }
  const footer = cl.rows.find((r) => r.id === "vault");
  if (footer && footer.state !== "hidden") renderRow(el(root, "div", { cls: "artmind-footer" }), footer, cl.current === "vault", snapshot, handlers, ui);
}
