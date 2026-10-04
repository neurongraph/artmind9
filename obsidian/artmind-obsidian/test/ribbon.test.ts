// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { checklist } from "../src/checklist";
import { EMPTY_INPUTS, type StateInputs } from "../src/state";
import { RibbonIcon, attentionTone, ribbonItems } from "../src/views/ribbon";
import { fixture } from "./fixtures";
import { Menu } from "./mocks/obsidian";

function state(overrides: Partial<StateInputs> = {}) {
  return checklist({
    ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    tablesReview: { query_type: "structured", command: "db review", pending_count: 0, tables: [] },
    projection: fixture("projection-status.ready").json,
    synthesis: [],
    git: { ahead: 0, artmindChanges: [] },
    ...overrides,
  });
}

function recorder() {
  const calls: string[] = [];
  const r = (name: string) => () => void calls.push(name);
  return { calls, handlers: { openPanel: r("openPanel"), openAdmin: r("openAdmin"), doctor: r("doctor") } };
}

beforeEach(() => {
  Menu.shown.length = 0;
});

describe("the ribbon icon (checklist spec §4)", () => {
  it("has no dot when nothing is left, else the current step's tone", () => {
    expect(attentionTone(state())).toBeNull();
    expect(attentionTone(state({ status: fixture("vault-status.behind").json }))).toBe("amber");
    expect(attentionTone(state({ status: fixture("vault-status.merge-conflict").json }))).toBe("red");
    expect(attentionTone(state({ activeJob: fixture("ingest-job-status.running").json }))).toBe("spinner");
    expect(attentionTone(state({ git: { ahead: 1, artmindChanges: [] } }))).toBe("blue");
  });

  it("shows the dot as classes, and clears them", () => {
    const el = document.createElement("div");
    const icon = new RibbonIcon(el, recorder().handlers);

    icon.render(state({ status: fixture("vault-status.behind").json }));
    expect([...el.classList]).toEqual(["artmind-ribbon", "artmind-ribbon-attention", "artmind-ribbon-amber"]);
    expect(el.getAttribute("aria-label")).toBe("artmind: 2 docs · 1 table to apply");

    icon.render(state());
    expect([...el.classList]).toEqual(["artmind-ribbon"]);
  });

  it("right-click offers only the panel, the admin console and the doctor: never an action", () => {
    const el = document.createElement("div");
    const r = recorder();
    const icon = new RibbonIcon(el, r.handlers);
    icon.render(state({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json }));

    el.dispatchEvent(new MouseEvent("contextmenu", { cancelable: true }));

    const items = Menu.shown[0].items;
    expect(items.map((i) => [i.title, i.disabled])).toEqual([
      ["Open side panel", false],
      ["Open admin console", false],
      ["Run doctor", false],
    ]);
    items[1].onClickFn!();
    items[2].onClickFn!();
    expect(r.calls).toEqual(["openAdmin", "doctor"]);
  });

  it("blocks the admin console and the doctor when artmind itself is missing", () => {
    const items = ribbonItems(state({ problem: { kind: "missing", looked: [] } }), recorder().handlers);
    expect(items.map((i) => i.blocked)).toEqual([null, "artmind not found (looked in nowhere)", "artmind not found (looked in nowhere)"]);
  });
});
