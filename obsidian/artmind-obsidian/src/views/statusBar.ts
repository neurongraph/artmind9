import type { Action, Tone, VaultStateResult } from "../state";

const TONES: Tone[] = ["red", "amber", "blue", "grey", "spinner"];

/** The status bar item (spec §3.1): one state at a time; a click performs
 * that state's action. Secondary indicators follow the label. */
export class StatusBarItem {
  private el: HTMLElement;
  private action: Action | null = null;

  constructor(el: HTMLElement, onAction: (action: Action) => void) {
    this.el = el;
    el.classList.add("artmind-status");
    el.addEventListener("click", () => {
      if (this.action) onAction(this.action);
    });
  }

  render(state: VaultStateResult): void {
    this.el.textContent = [state.primary.label, ...state.secondary].join("  ");
    for (const tone of TONES) this.el.classList.remove(`artmind-tone-${tone}`);
    this.el.classList.add(`artmind-tone-${state.primary.tone}`);
    this.el.setAttribute("aria-label", state.primary.detail);
    this.action = state.primary.action;
  }
}
