// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { EMPTY_INPUTS, type StateInputs, vaultState } from "../src/state";
import { RibbonIcon, attentionTone, ribbonItems } from "../src/views/ribbon";
import { fixture } from "./fixtures";
import { Menu } from "./mocks/obsidian";

function state(overrides: Partial<StateInputs> = {}) {
  return vaultState({ ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    activeJob: null,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    git: { ahead: 0, artmindChanges: [] },
    problem: null,
    ...overrides,
  });
}

function recorder() {
  const calls: string[] = [];
  const r = (name: string) => () => void calls.push(name);
  return { calls, handlers: { openPanel: r("openPanel"), sync: r("sync"), ingest: r("ingest"), openAdmin: r("openAdmin"), doctor: r("doctor") } };
}

beforeEach(() => {
  Menu.shown.length = 0;
});

describe("the ribbon icon", () => {
  it("has no dot when in sync, and the top state's tone otherwise", () => {
    expect(attentionTone(state())).toBeNull();
    expect(attentionTone(state({ status: fixture("vault-status.behind").json }))).toBe("amber");
    expect(attentionTone(state({ status: fixture("vault-status.merge-conflict").json }))).toBe("red");
    expect(attentionTone(state({ activeJob: fixture("ingest-job-status.running").json }))).toBe("spinner");
  });

  it("shows the dot as classes, and clears them", () => {
    const el = document.createElement("div");
    const icon = new RibbonIcon(el, recorder().handlers);

    icon.render(state({ status: fixture("vault-status.behind").json }));
    expect([...el.classList]).toEqual(["artmind-ribbon", "artmind-ribbon-attention", "artmind-ribbon-amber"]);
    expect(el.getAttribute("aria-label")).toBe("artmind: ◉ 2 docs · 1 table behind");

    icon.render(state());
    expect([...el.classList]).toEqual(["artmind-ribbon"]);
  });

  it("right-click offers the actions, each disabled with its reason as the toolbar does", () => {
    const el = document.createElement("div");
    const r = recorder();
    const icon = new RibbonIcon(el, r.handlers);
    icon.render(state({ status: fixture("vault-status.merge-conflict").json }));

    el.dispatchEvent(new MouseEvent("contextmenu", { cancelable: true }));

    const items = Menu.shown[0].items;
    expect(items.map((i) => [i.title, i.disabled])).toEqual([
      ["Open side panel", false],
      ["Sync — A merge is in progress — finish it first", true],
      ["Ingest what changed — A merge is in progress — finish it first", true],
      ["Open admin console", false],
      ["Run doctor", false],
    ]);
    items[3].onClickFn!();
    expect(r.calls).toEqual(["openAdmin"]);
  });

  it("blocks the admin console with everything else when artmind itself is missing", () => {
    const blocked = ribbonItems(state({ problem: { kind: "missing", looked: [] } }), recorder().handlers).find((i) => i.title === "Open admin console")!;
    expect(blocked.blocked).toBe("artmind not found (looked in nowhere)");
  });
});
