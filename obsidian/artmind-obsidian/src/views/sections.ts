import { el } from "./dom";

/** What the panel remembers between renders: which sections are collapsed
 * and which rows' lists are open. Every snapshot re-renders the panel, so
 * `<details>` alone would forget. */
export interface PanelUi {
  collapsed: Set<string>;
  expanded: Set<string>;
}

/** Collapsed until opened: the detail most visits don't need. */
export const DEFAULT_COLLAPSED = ["status", "activity"];

export function newPanelUi(): PanelUi {
  return { collapsed: new Set(DEFAULT_COLLAPSED), expanded: new Set() };
}

/** A collapsible section whose header carries a one-line summary (a count,
 * a badge), so the panel reads even with everything collapsed. */
export function section(root: HTMLElement, ui: PanelUi, id: string, title: string, badge = "", badgeTone = "grey"): HTMLElement {
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

export function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}
