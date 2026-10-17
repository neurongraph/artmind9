import { type App, Modal } from "obsidian";
import { type CurateKind, DEFAULT_MAX_PAIRS } from "../controller";
import { type CurateReading, plural } from "../state";
import { LABELS } from "../texts";
import { el } from "./dom";

export interface CurateChoice {
  domains: Set<string>;
  maxPairs: number;
}

export interface CurateHandlers {
  run(domains: string[], maxPairs: number): void;
  cancel(): void;
}

const TEXTS = {
  duplicates: {
    title: "Find duplicate entities",
    intro: "Compares entities with similar names inside each domain, one LLM call per pair, and proposes the ones that look like the same thing. Nothing is merged: each proposal waits for your review in the admin console.",
    run: LABELS.findDuplicatesRun,
    min: 1,
  },
  conflicts: {
    title: "Detect conflicts",
    intro: "Compares entities that appear in more than one of the chosen domains, one LLM call per pair, and records contradicting claims as conflicts (and same-entity pairs as same-as proposals). Nothing is resolved: you adjudicate in the admin console.",
    run: LABELS.detectConflictsRun,
    min: 2,
  },
} as const;

/** Domains to compare and a cap on the pairs (`--maxPairs`): at most one
 * LLM call per pair, per run. Nothing runs until the run button (D1). */
export function renderCurate(root: HTMLElement, kind: CurateKind, reading: CurateReading, handlers: CurateHandlers): void {
  const text = TEXTS[kind];
  root.replaceChildren();
  el(root, "h3", { text: text.title });
  el(root, "p", { cls: "artmind-muted", text: text.intro });
  const choice: CurateChoice = { domains: new Set(reading.domains), maxPairs: DEFAULT_MAX_PAIRS };

  const list = el(root, "div", { cls: "artmind-synth-domains" });
  const capLine = el(root, "label", { cls: "artmind-synth-cap" });
  const total = el(root, "p", { cls: "artmind-synth-total" });
  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  const runButton = el(buttons, "button", { cls: "mod-cta", text: text.run });
  el(buttons, "button", { text: LABELS.cancel }).addEventListener("click", () => handlers.cancel());

  const update = () => {
    const chosen = choice.domains.size;
    // Duplicates run once per domain, each with its own cap; conflicts run once.
    const calls = kind === "duplicates" ? chosen * choice.maxPairs : choice.maxPairs;
    total.textContent = `Up to ${plural(calls, "LLM call")} in total`;
    runButton.disabled = chosen < text.min;
    runButton.title = chosen < text.min ? `Choose at least ${plural(text.min, "domain")}` : "";
  };

  for (const domain of reading.domains) {
    const label = el(list, "label", { cls: "artmind-synth-domain" });
    const box = el(label, "input", { attr: { type: "checkbox" } });
    box.checked = true;
    box.addEventListener("change", () => {
      if (box.checked) choice.domains.add(domain);
      else choice.domains.delete(domain);
      update();
    });
    el(label, "span", { text: ` ${domain}` });
  }

  el(capLine, "span", { text: kind === "duplicates" ? "Cap on pairs per domain: " : "Cap on pairs: " });
  const cap = el(capLine, "input", { attr: { type: "number", min: "1", value: String(DEFAULT_MAX_PAIRS) } });
  cap.addEventListener("input", () => {
    const n = Number.parseInt(cap.value, 10);
    choice.maxPairs = Number.isFinite(n) && n > 0 ? n : DEFAULT_MAX_PAIRS;
    update();
  });

  runButton.addEventListener("click", () => handlers.run(reading.domains.filter((d) => choice.domains.has(d)), choice.maxPairs));
  update();
}

export interface CurateDeps {
  kind: CurateKind;
  reading(): CurateReading | null;
  run(domains: string[], maxPairs: number): void;
}

export class CurateModal extends Modal {
  private deps: CurateDeps;

  constructor(app: App, deps: CurateDeps) {
    super(app);
    this.deps = deps;
  }

  override onOpen(): void {
    const reading = this.deps.reading();
    if (!reading) {
      this.close();
      return;
    }
    renderCurate(this.contentEl, this.deps.kind, reading, {
      run: (domains, maxPairs) => {
        this.close();
        this.deps.run(domains, maxPairs);
      },
      cancel: () => this.close(),
    });
  }

  override onClose(): void {
    this.contentEl.replaceChildren();
  }
}
