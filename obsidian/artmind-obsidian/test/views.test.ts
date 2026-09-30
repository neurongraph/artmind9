// @vitest-environment jsdom
// The same module `obsidian` resolves to in tests (vitest.config.ts), imported
// by path for its test-only record of the notices shown.
import { Notice } from "./mocks/obsidian";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Snapshot } from "../src/controller";
import { type StateInputs, vaultState } from "../src/state";
import { ObsidianNotifier } from "../src/views/notices";
import { type PanelHandlers, renderPanel } from "../src/views/panel";
import { ResolveModal, renderResolve, resolveBlockedReason } from "../src/views/resolveModal";
import { StatusBarItem } from "../src/views/statusBar";
import { LOOKUP_PREVIEW, TableReviewModal, renderTableProblem, renderTableReview } from "../src/views/tableReview";
import { fixture } from "./fixtures";

function snapshot(overrides: Partial<StateInputs> = {}, extra: Partial<Snapshot> = {}): Snapshot {
  const inputs: StateInputs = {
    status: fixture("vault-status.in-sync").json,
    activeJob: null,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    git: { ahead: 0, unsharedArtmindChanges: 0 },
    problem: null,
    ...overrides,
  };
  return {
    inputs,
    state: vaultState(inputs),
    activity: [],
    lastSync: null,
    doctor: null,
    jobResults: null,
    readErrors: [],
    bridgeWarning: null,
    gitMissing: {},
    looked: [],
    ...extra,
  };
}

function handlers(): PanelHandlers & { calls: string[] } {
  const calls: string[] = [];
  const record = (name: string) => (arg?: string) => calls.push(arg === undefined ? name : `${name}:${arg}`);
  return {
    calls,
    sync: record("sync"),
    ingest: record("ingest"),
    resolve: record("resolve"),
    doctor: record("doctor"),
    reviewTable: record("reviewTable"),
    commitAndSync: record("commitAndSync"),
    setPath: record("setPath"),
    copy: record("copy"),
  };
}

function text(root: HTMLElement): string {
  return root.textContent ?? "";
}

function button(root: HTMLElement, label: string): HTMLButtonElement {
  const found = [...root.querySelectorAll("button")].find((b) => b.textContent === label);
  if (!found) throw new Error(`no button "${label}"`);
  return found;
}

beforeEach(() => {
  Notice.shown.length = 0;
});

describe("StatusBarItem (spec §3.1)", () => {
  it("shows the primary state, its tone and the secondary indicators; a click runs its action", () => {
    const el = document.createElement("div");
    el.classList.add("status-bar-item");
    const performed: unknown[] = [];
    const item = new StatusBarItem(el, (action) => performed.push(action));

    item.render(snapshot({ status: fixture("vault-status.behind").json, git: { ahead: 3, unsharedArtmindChanges: 1 } }).state);

    expect(el.textContent).toBe("◉ 2 docs · 1 table behind  ↑ 3 to push  ✎ artmind changes to share");
    expect(el.classList.contains("artmind-tone-amber")).toBe(true);
    expect(el.classList.contains("status-bar-item")).toBe(true);
    el.click();
    expect(performed).toEqual([{ type: "sync" }]);

    item.render(snapshot().state);
    expect(el.textContent).toBe("◉ artmind");
    expect(el.classList.contains("artmind-tone-amber")).toBe(false);
    expect(el.classList.contains("artmind-tone-grey")).toBe(true);
  });
});

describe("notices (spec §3.3)", () => {
  it("a notice with buttons stays until a button closes it and runs it", () => {
    const run = vi.fn();

    new ObsidianNotifier().show("pulled", "Pulled from the other laptop — 2 docs behind.", [{ label: "Sync now", run }]);

    const notice = Notice.shown[0];
    expect(notice.duration).toBe(0);
    const holder = document.createElement("div");
    holder.appendChild(notice.message as DocumentFragment);
    expect(text(holder)).toBe("Pulled from the other laptop — 2 docs behind.Sync now");
    button(holder, "Sync now").click();
    expect(run).toHaveBeenCalledOnce();
    expect(notice.hidden).toBe(true);
  });

  it("a plain notice fades", () => {
    new ObsidianNotifier().show("syncDone", "Synced: 3 docs, 1 table, 2 curation records.");
    expect(Notice.shown[0].duration).toBe(6_000);
  });
});

describe("the side panel (spec §3.2)", () => {
  it("shows status, pending counts, and disables actions with the reason", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ status: fixture("vault-status.merge-conflict").json, git: { ahead: 2, unsharedArtmindChanges: 0 } }), h);

    expect(text(root)).toContain("⚠ resolve artmind conflicts");
    expect(text(root)).toContain("Commits to push2");
    expect(button(root, "Sync").disabled).toBe(true);
    expect(text(root)).toContain("A merge is in progress — finish it first");
    button(root, "Resolve").click();
    expect(h.calls).toEqual(["resolve"]);
  });

  it("lists the tables waiting, each opening its review", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ tablesPending: fixture("table2graph.pending").json }), h);

    const link = [...root.querySelectorAll("a")].find((a) => a.textContent === "team (general)")!;
    link.click();
    expect(h.calls).toEqual(["reviewTable:team"]);
    expect(text(root)).toContain("missing");
  });

  it("shows each doctor finding with its fix and a Copy button, and never runs it", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({}, { doctor: fixture("vault-doctor").json }), h);

    expect(text(root)).toContain("FAIL pull.rebase");
    const copies = [...root.querySelectorAll("button")].filter((b) => b.textContent === "Copy");
    copies[0].click();
    expect(h.calls).toEqual(["copy:git config pull.rebase false"]);
  });

  it("offers Commit-and-sync for artmind changes to share, or the instruction when it is missing", () => {
    const root = document.createElement("div");
    const h = handlers();
    renderPanel(root, snapshot({ git: { ahead: 0, unsharedArtmindChanges: 2 } }), h);
    button(root, "Commit-and-sync").click();
    expect(h.calls).toEqual(["commitAndSync"]);

    renderPanel(
      root,
      snapshot({ git: { ahead: 0, unsharedArtmindChanges: 2 } }, { gitMissing: { commitAndSync: "Run Obsidian Git: Commit-and-sync" }, bridgeWarning: "Obsidian Git has no Commit-and-sync command" }),
      h,
    );
    expect(button(root, "Run Obsidian Git: Commit-and-sync").disabled).toBe(true);
    expect(text(root)).toContain("Obsidian Git has no Commit-and-sync command");
  });

  it("explains a missing artmind and offers Set path", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ problem: { kind: "missing", looked: ["/Users/me/.local/bin/artmind"] } }), h);

    expect(text(root)).toContain("Looked in: /Users/me/.local/bin/artmind");
    button(root, "Set path").click();
    expect(h.calls).toEqual(["setPath"]);
  });

  it("shows the bootstrap step when this laptop never synced", () => {
    const root = document.createElement("div");
    renderPanel(root, snapshot({ status: fixture("vault-status.no-bookmark").json }), handlers());
    expect(text(root)).toContain("artmind vault sync --bootstrapEmpty");
  });

  it("lists the activity, each run expanding to its raw output", () => {
    const root = document.createElement("div");
    const activity = [{ command: "artmind vault sync --compact", at: 0, ok: false, summary: "failed: boom", output: "raw stderr" }];

    renderPanel(root, snapshot({}, { activity }), handlers());

    const details = root.querySelector("details.artmind-run-failed")!;
    expect(details.querySelector("summary")!.textContent).toContain("artmind vault sync --compact — failed: boom");
    expect(details.querySelector("pre")!.textContent).toBe("raw stderr");
  });
});

describe("the table2graph review (spec §5.1)", () => {
  const report = fixture("table2graph.dry-run").json.tables[0];

  it("shows header, entity classes with skip reasons, lookups and warnings", () => {
    const root = document.createElement("div");
    renderTableReview(root, report, { project: vi.fn(), openMapping: vi.fn(), askAdminUi: vi.fn(), cancel: vi.fn() });

    expect(text(root)).toContain("Domain general · mapping /vault/.artmind/domains/table_mappings/team.yaml · 4 rows");
    expect(text(root)).toContain("person (PERSON): built 3, kept 3");
    expect(text(root)).toContain("skipped 1: required property 'full_name' is empty");
    expect(text(root)).toContain("manager: resolved 1, stubbed 1");
    expect(text(root)).toContain("unresolved: Zed Unknown");
    expect(root.querySelectorAll(".artmind-review-warnings .artmind-warning")).toHaveLength(report.warnings.length);
  });

  it("shows the first ten unresolved values, then all on request", () => {
    const root = document.createElement("div");
    const many = Array.from({ length: 13 }, (_, i) => `name ${i}`);
    const big = { ...report, lookups: { manager: { ...report.lookups.manager, unresolved_values: many } } };

    renderTableReview(root, big, { project: vi.fn(), openMapping: vi.fn(), askAdminUi: vi.fn(), cancel: vi.fn() });

    expect(text(root)).toContain(many.slice(0, LOOKUP_PREVIEW).join(", "));
    expect(text(root)).not.toContain("name 12");
    button(root, "show all 13").click();
    expect(text(root)).toContain("name 12");
  });

  it("projects, opens the mapping, or cancels", () => {
    const root = document.createElement("div");
    const h = { project: vi.fn(), openMapping: vi.fn(), askAdminUi: vi.fn(), cancel: vi.fn() };
    renderTableReview(root, report, h);

    button(root, "Project to graph").click();
    button(root, "Open mapping").click();
    button(root, "Cancel").click();

    expect(h.project).toHaveBeenCalledOnce();
    expect(h.openMapping).toHaveBeenCalledWith(report.mapping);
    expect(h.cancel).toHaveBeenCalledOnce();
  });

  it("with no mapping, says so and offers admin-ui", async () => {
    const root = document.createElement("div");
    const h = { project: vi.fn(), openMapping: vi.fn(), askAdminUi: vi.fn(), cancel: vi.fn() };
    const { errorText } = await import("../src/cli");

    renderTableProblem(root, "budget", errorText(fixture("table2graph.dry-run-no-mapping").stderr!)!, h);

    expect(text(root)).toContain("No table mapping matches budget.");
    button(root, "Ask admin-ui to create one").click();
    expect(h.askAdminUi).toHaveBeenCalledOnce();
  });

  it("the modal runs the dry run, and projects only on the click", async () => {
    const project = vi.fn(async () => true);
    const modal = new TableReviewModal({} as never, "team", {
      dryRun: async () => ({ report, error: null }),
      project,
      openMapping: vi.fn(),
      askAdminUi: vi.fn(),
    });

    await modal.onOpen();
    expect(project).not.toHaveBeenCalled();
    button(modal.contentEl, "Project to graph").click();
    expect(project).toHaveBeenCalledWith("team");
  });
});

describe("the resolve preview (spec §5.2)", () => {
  const report = fixture("vault-resolve.dry-run").json;

  it("groups resolved, pending and reported units", () => {
    const root = document.createElement("div");
    const open = vi.fn();
    renderResolve(root, report, { resolve: vi.fn(), open, cancel: vi.fn() });

    expect(text(root)).toContain("Resolved (1)");
    expect(text(root)).toContain(".artmind/data/kg/general/policy: takes the other laptop's side — the side extracted from the merged note's body");
    expect(text(root)).toContain("Pending (1)");
    expect(text(root)).toContain("Reported — resolve these in Obsidian (2)");
    [...root.querySelectorAll("a")].find((a) => a.textContent === "Notes/memo.md")!.click();
    expect(open).toHaveBeenCalledWith("Notes/memo.md");
  });

  it("disables Resolve while anything is pending, with the reason", () => {
    const root = document.createElement("div");
    renderResolve(root, report, { resolve: vi.fn(), open: vi.fn(), cancel: vi.fn() });

    expect(button(root, "Resolve").disabled).toBe(true);
    expect(text(root)).toContain("Waiting on 1 note you still have to resolve");
    expect(resolveBlockedReason({ ...report, pending: [] })).toBeNull();
  });

  it("the modal applies only on the click", async () => {
    const apply = vi.fn(async () => true);
    const clean = { ...report, pending: [] };
    const modal = new ResolveModal({} as never, { preview: async () => ({ report: clean, error: null }), apply, open: vi.fn() });

    await modal.onOpen();
    expect(apply).not.toHaveBeenCalled();
    button(modal.contentEl, "Resolve").click();
    expect(apply).toHaveBeenCalledOnce();
  });
});
