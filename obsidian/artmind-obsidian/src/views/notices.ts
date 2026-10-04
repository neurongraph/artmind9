import { Notice } from "obsidian";
import type { NoticeLevel, Notifier } from "../controller";
import type { PanelTarget } from "../state";
import { el } from "./dom";

/** Text-only notices (checklist spec §5, D7). Each fades: 10 s for an
 * error, 6 s otherwise. Clicking one opens the panel at its row. */
export class ObsidianNotifier implements Notifier {
  private openTarget: (target: PanelTarget) => void;

  constructor(openTarget: (target: PanelTarget) => void) {
    this.openTarget = openTarget;
  }

  show(kind: NoticeLevel, text: string, target: PanelTarget | null = null): void {
    const fragment = document.createDocumentFragment();
    const body = el(fragment, "div", { cls: `artmind-notice artmind-notice-${kind}`, text });
    const notice = new Notice(fragment, kind === "error" ? 10_000 : 6_000);
    if (!target) return;
    body.classList.add("artmind-notice-link");
    body.addEventListener("click", () => {
      notice.hide();
      this.openTarget(target);
    });
  }
}
