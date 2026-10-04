import { type App, Modal } from "obsidian";
import { type DomainSynthesis, plural } from "../state";
import { LABELS } from "../texts";
import { el } from "./dom";

export interface SynthesizeChoice {
  domains: Set<string>;
  /** `--limit`, per domain; null for no cap. */
  limit: number | null;
}

/** One LLM call per description: the chosen domains' counts, each capped. */
export function llmCalls(plan: DomainSynthesis[], choice: SynthesizeChoice): number {
  return plan
    .filter((d) => choice.domains.has(d.domain))
    .reduce((sum, d) => sum + (choice.limit === null ? d.count : Math.min(d.count, choice.limit)), 0);
}

export interface SynthesizeHandlers {
  run(domains: string[], limit: number | null): void;
  cancel(): void;
}

/** Each mapped domain with its count, the model, an optional cap, the
 * total (checklist spec §3.4). Domains with nothing to do can't be chosen. */
export function renderSynthesize(root: HTMLElement, plan: DomainSynthesis[], handlers: SynthesizeHandlers): void {
  root.replaceChildren();
  el(root, "h3", { text: "Synthesize descriptions" });
  const models = [...new Set(plan.map((d) => d.model))].join(", ");
  el(root, "p", { cls: "artmind-muted", text: `Model: ${models || "unknown"}` });
  const choice: SynthesizeChoice = { domains: new Set(plan.filter((d) => d.count > 0).map((d) => d.domain)), limit: null };

  const list = el(root, "div", { cls: "artmind-synth-domains" });
  const capLine = el(root, "label", { cls: "artmind-synth-cap" });
  const total = el(root, "p", { cls: "artmind-synth-total" });
  const buttons = el(root, "div", { cls: "artmind-review-buttons" });
  const runButton = el(buttons, "button", { cls: "mod-cta", text: LABELS.synthesizeRun });
  el(buttons, "button", { text: LABELS.cancel }).addEventListener("click", () => handlers.cancel());

  const update = () => {
    const calls = llmCalls(plan, choice);
    total.textContent = `${plural(calls, "LLM call")} in total`;
    runButton.disabled = calls === 0;
  };

  for (const d of plan) {
    const label = el(list, "label", { cls: "artmind-synth-domain" });
    const box = el(label, "input", { attr: { type: "checkbox" } });
    box.checked = choice.domains.has(d.domain);
    box.disabled = d.count === 0;
    box.addEventListener("change", () => {
      if (box.checked) choice.domains.add(d.domain);
      else choice.domains.delete(d.domain);
      update();
    });
    el(label, "span", { text: ` ${d.domain}: ${plural(d.count, "description")}` });
  }

  el(capLine, "span", { text: "Cap per domain (optional): " });
  const cap = el(capLine, "input", { attr: { type: "number", min: "1", placeholder: "no cap" } });
  cap.addEventListener("input", () => {
    const n = Number.parseInt(cap.value, 10);
    choice.limit = Number.isFinite(n) && n > 0 ? n : null;
    update();
  });

  runButton.addEventListener("click", () => {
    handlers.run(plan.filter((d) => choice.domains.has(d.domain)).map((d) => d.domain), choice.limit);
  });
  update();
}

export interface SynthesizeDeps {
  plan(): DomainSynthesis[];
  run(domains: string[], limit: number | null): void;
}

/** Synthesize… : nothing is written until [Synthesize] (D1). */
export class SynthesizeModal extends Modal {
  private deps: SynthesizeDeps;

  constructor(app: App, deps: SynthesizeDeps) {
    super(app);
    this.deps = deps;
  }

  override onOpen(): void {
    renderSynthesize(this.contentEl, this.deps.plan(), {
      run: (domains, limit) => {
        this.close();
        this.deps.run(domains, limit);
      },
      cancel: () => this.close(),
    });
  }

  override onClose(): void {
    this.contentEl.replaceChildren();
  }
}
