import { type App, Modal } from "obsidian";
import { el } from "./dom";

export interface ConfirmChoice {
  label: string;
  run: () => void;
}

/** An out-of-order click's question (checklist spec §3.3). The first
 * choice is the recommended one. */
export interface ConfirmRequest {
  text: string;
  choices: ConfirmChoice[];
}

export function renderConfirm(root: HTMLElement, request: ConfirmRequest, close: () => void): void {
  root.replaceChildren();
  el(root, "p", { cls: "artmind-confirm-text", text: request.text });
  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  request.choices.forEach((choice, index) => {
    el(buttons, "button", { cls: index === 0 ? "mod-cta" : "", text: choice.label }).addEventListener("click", () => {
      close();
      choice.run();
    });
  });
}

export class ConfirmModal extends Modal {
  private request: ConfirmRequest;

  constructor(app: App, request: ConfirmRequest) {
    super(app);
    this.request = request;
  }

  override onOpen(): void {
    renderConfirm(this.contentEl, this.request, () => this.close());
  }

  override onClose(): void {
    this.contentEl.replaceChildren();
  }
}
