import type { Checklist } from "../checklist";
import type { Tone } from "../state";
import { STATUS_READY, readinessText } from "../texts";

const TONES: Tone[] = ["red", "amber", "blue", "grey", "spinner"];

/** `artmind ●●○○○ 7 to ingest`: one dot per row 1–4 and the footer, filled
 * when done, then the current step's summary (checklist spec §4). */
export function statusText(cl: Checklist): string {
  const dots = cl.rows.filter((r) => !r.optional).map((r) => (r.state === "done" ? "●" : "○")).join("");
  const current = cl.rows.find((r) => r.id === cl.current);
  return `artmind ${dots} ${current ? current.summary : STATUS_READY}`;
}

/** The status bar item. A click opens the panel at the current row; it
 * never runs an action (D8). */
export class StatusBarItem {
  private el: HTMLElement;

  constructor(el: HTMLElement, onClick: () => void) {
    this.el = el;
    el.classList.add("artmind-status");
    el.addEventListener("click", () => onClick());
  }

  render(cl: Checklist): void {
    const current = cl.rows.find((r) => r.id === cl.current);
    this.el.textContent = statusText(cl);
    for (const tone of TONES) this.el.classList.remove(`artmind-tone-${tone}`);
    this.el.classList.add(`artmind-tone-${current?.tone ?? "grey"}`);
    this.el.setAttribute("aria-label", current ? [current.title, current.summary, ...current.detail.slice(0, 1)].join(" — ") : readinessText(cl));
  }
}
