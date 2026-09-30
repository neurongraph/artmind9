import { Notice } from "obsidian";
import type { NoticeButton, Notifier } from "../controller";
import type { NoticeKind } from "../settings";
import { el } from "./dom";

/** Notices with buttons (spec §3.3). One with buttons stays until clicked
 * or dismissed; a plain one fades. Clicking a button closes its notice. */
export class ObsidianNotifier implements Notifier {
  show(kind: NoticeKind | "prompt" | "error", text: string, buttons: NoticeButton[] = []): void {
    const fragment = document.createDocumentFragment();
    el(fragment, "div", { cls: `artmind-notice artmind-notice-${kind}`, text });
    const row = buttons.length ? el(fragment, "div", { cls: "artmind-notice-buttons" }) : null;
    const notice = new Notice(fragment, buttons.length ? 0 : kind === "error" ? 10_000 : 6_000);
    for (const button of buttons) {
      el(row, "button", { text: button.label }).addEventListener("click", (event) => {
        event.stopPropagation();
        notice.hide();
        button.run();
      });
    }
  }
}
