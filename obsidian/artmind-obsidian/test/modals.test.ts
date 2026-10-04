// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { DomainSynthesis } from "../src/state";

// The test mock (test/mocks/obsidian.ts) exposes `isOpen` on Modal, but the
// published obsidian typings don't. Augment just enough for type-checking.
declare module "obsidian" {
  interface Modal {
    isOpen: boolean;
  }
}
import { ConfirmModal, renderConfirm } from "../src/views/confirmModal";
import { SynthesizeModal, llmCalls, renderSynthesize } from "../src/views/synthesizeModal";

function button(root: HTMLElement, label: string): HTMLButtonElement {
  const found = [...root.querySelectorAll("button")].find((b) => b.textContent === label);
  if (!found) throw new Error(`no button "${label}"`);
  return found;
}

describe("the confirm modal (checklist spec §3.3)", () => {
  it("asks, offers the choices with the first filled, and runs the one clicked after closing", () => {
    const root = document.createElement("div");
    const order: string[] = [];
    renderConfirm(
      root,
      {
        text: "The other laptop has changes not applied to your graph.",
        choices: [
          { label: "Apply first", run: () => order.push("apply") },
          { label: "Ingest anyway", run: () => order.push("ingest") },
        ],
      },
      () => order.push("close"),
    );

    expect(root.querySelector("p")!.textContent).toBe("The other laptop has changes not applied to your graph.");
    expect([...root.querySelectorAll("button")].map((b) => [b.textContent, b.classList.contains("mod-cta")])).toEqual([
      ["Apply first", true],
      ["Ingest anyway", false],
    ]);
    button(root, "Ingest anyway").click();
    expect(order).toEqual(["close", "ingest"]);
  });

  it("the modal runs nothing until a choice is clicked", () => {
    const run = vi.fn();
    const modal = new ConfirmModal({} as never, { text: "Pull from GitHub first?", choices: [{ label: "Pull, then apply", run }] });

    modal.open();
    expect(run).not.toHaveBeenCalled();
    button(modal.contentEl, "Pull, then apply").click();
    expect(run).toHaveBeenCalledOnce();
    expect(modal.isOpen).toBe(false);
  });
});

describe("the Synthesize… modal (checklist spec §3.4)", () => {
  const plan: DomainSynthesis[] = [
    { domain: "general", count: 41, model: "ministral-3:14b" },
    { domain: "habits", count: 5, model: "ministral-3:14b" },
    { domain: "empty", count: 0, model: "ministral-3:14b" },
  ];

  it("counts the LLM calls of the chosen domains, under the cap", () => {
    expect(llmCalls(plan, { domains: new Set(["general", "habits"]), limit: null })).toBe(46);
    expect(llmCalls(plan, { domains: new Set(["general", "habits"]), limit: 10 })).toBe(15);
    expect(llmCalls(plan, { domains: new Set(["habits"]), limit: null })).toBe(5);
  });

  it("lists each domain with its count and the model, and updates the total", () => {
    const root = document.createElement("div");
    const run = vi.fn();
    renderSynthesize(root, plan, { run, cancel: vi.fn() });

    expect([...root.querySelectorAll(".artmind-synth-domain")].map((l) => l.textContent)).toEqual([
      " general: 41 descriptions",
      " habits: 5 descriptions",
      " empty: 0 descriptions",
    ]);
    expect(root.textContent).toContain("Model: ministral-3:14b");
    expect(root.querySelector(".artmind-synth-total")!.textContent).toBe("46 LLM calls in total");

    const boxes = [...root.querySelectorAll<HTMLInputElement>("input[type=checkbox]")];
    expect(boxes.map((b) => [b.checked, b.disabled])).toEqual([[true, false], [true, false], [false, true]]);
    boxes[1].checked = false;
    boxes[1].dispatchEvent(new Event("change"));
    const cap = root.querySelector<HTMLInputElement>("input[type=number]")!;
    cap.value = "20";
    cap.dispatchEvent(new Event("input"));
    expect(root.querySelector(".artmind-synth-total")!.textContent).toBe("20 LLM calls in total");

    button(root, "Synthesize").click();
    expect(run).toHaveBeenCalledWith(["general"], 20);
  });

  it("can't run with nothing chosen", () => {
    const root = document.createElement("div");
    renderSynthesize(root, plan, { run: vi.fn(), cancel: vi.fn() });
    for (const box of root.querySelectorAll<HTMLInputElement>("input[type=checkbox]")) {
      box.checked = false;
      box.dispatchEvent(new Event("change"));
    }
    expect(button(root, "Synthesize").disabled).toBe(true);
  });

  it("the modal writes nothing until Synthesize is clicked, and closes first", () => {
    const run = vi.fn();
    const modal = new SynthesizeModal({} as never, { plan: () => plan, run });

    modal.open();
    expect(run).not.toHaveBeenCalled();
    button(modal.contentEl, "Synthesize").click();
    expect(modal.isOpen).toBe(false);
    expect(run).toHaveBeenCalledWith(["general", "habits"], null);
  });
});
