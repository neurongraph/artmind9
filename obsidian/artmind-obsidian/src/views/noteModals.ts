import { type App, Modal } from "obsidian";
import type { NoteScope, Older } from "../noteCommands";
import { LABELS } from "../texts";
import { el } from "./dom";

const MAX_SHOWN = 50;

/** The older note: a filter over `candidates`, a scope and (supersede) an effective date. */
export function renderOlderPicker(
  root: HTMLElement,
  candidates: string[],
  withEffective: boolean,
  done: (choice: Older | null) => void,
): void {
  root.replaceChildren();
  el(root, "h3", { text: withEffective ? "This note supersedes…" : "Undo a supersession of this note" });
  el(root, "p", { cls: "artmind-muted", text: "Choose the older note." });
  const filter = el(root, "input", { attr: { type: "text", placeholder: "Filter by name" } });
  const list = el(root, "div", { cls: "artmind-synth-domains" });
  const scopeLine = el(root, "label", { cls: "artmind-synth-cap" });
  el(scopeLine, "span", { text: "Scope: " });
  const scope = el(scopeLine, "select");
  for (const s of ["document", "section", "clause"]) el(scope, "option", { text: s, attr: { value: s } });
  const dateLine = el(withEffective ? root : null, "label", { cls: "artmind-synth-cap" });
  el(dateLine, "span", { text: "Effective date (optional): " });
  const date = el(dateLine, "input", { attr: { type: "date" } });
  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  el(buttons, "button", { text: LABELS.cancel }).addEventListener("click", () => done(null));

  const draw = () => {
    list.replaceChildren();
    const q = filter.value.trim().toLowerCase();
    for (const name of candidates.filter((c) => c.toLowerCase().includes(q)).slice(0, MAX_SHOWN)) {
      el(list, "button", { text: name }).addEventListener("click", () =>
        done({ older: name, scope: scope.value as NoteScope, effective: withEffective ? date.value : "" }),
      );
    }
  };
  filter.addEventListener("input", draw);
  draw();
}

export class OlderPickerModal extends Modal {
  private settled = false;
  constructor(app: App, private candidates: string[], private withEffective: boolean, private done: (c: Older | null) => void) {
    super(app);
  }
  override onOpen(): void {
    renderOlderPicker(this.contentEl, this.candidates, this.withEffective, (choice) => {
      this.settled = true;
      this.close();
      this.done(choice);
    });
  }
  override onClose(): void {
    this.contentEl.replaceChildren();
    if (!this.settled) this.done(null);
  }
}

/** The history payload, read only. */
export class HistoryModal extends Modal {
  constructor(app: App, private title: string, private json: unknown) {
    super(app);
  }
  override onOpen(): void {
    el(this.contentEl, "h3", { text: this.title });
    el(this.contentEl, "pre", { text: JSON.stringify(this.json, null, 2) });
    el(this.contentEl, "button", { text: "Close" }).addEventListener("click", () => this.close());
  }
  override onClose(): void {
    this.contentEl.replaceChildren();
  }
}
