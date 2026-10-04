// @vitest-environment jsdom
// The same module `obsidian` resolves to in tests (vitest.config.ts), imported
// by path for its test-only record of the notices shown.
import { Notice } from "./mocks/obsidian";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Snapshot } from "../src/controller";
import { EMPTY_INPUTS, type StateInputs, vaultState } from "../src/state";
import { checklist } from "../src/checklist";
import { ObsidianNotifier } from "../src/views/notices";
import { type PanelHandlers, newPanelUi, renderPanel } from "../src/views/panel";
import { ResolveModal, renderResolve, resolveBlockedReason } from "../src/views/resolveModal";
import { StatusBarItem } from "../src/views/statusBar";
import { LOOKUP_PREVIEW, TableReviewModal, renderTableProblem, renderTableReview } from "../src/views/tableReview";
import { fixture } from "./fixtures";

function snapshot(overrides: Partial<StateInputs> = {}, extra: Partial<Snapshot> = {}): Snapshot {
  const inputs: StateInputs = { ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    activeJob: null,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    git: { ahead: 0, artmindChanges: [] },
    problem: null,
    ...overrides,
  };
  return {
    inputs,
    checklist: checklist(inputs),
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

/** Every row of the checklist done: each view test changes one thing. */
function readyInputs(overrides: Partial<StateInputs> = {}): StateInputs {
  return {
    ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    tablesReview: { query_type: "structured", command: "db review", pending_count: 0, tables: [] },
    projection: fixture("projection-status.ready").json,
    synthesis: [],
    git: { ahead: 0, artmindChanges: [] },
    ...overrides,
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
    retryJob: record("retryJob"),
    openAdmin: record("openAdmin"),
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

describe("StatusBarItem (checklist spec §4)", () => {
  it("shows a dot per row and footer, the current step and its tone; a click only opens the panel", () => {
    const el = document.createElement("div");
    el.classList.add("status-bar-item");
    let opened = 0;
    const item = new StatusBarItem(el, () => opened++);

    item.render(checklist(readyInputs({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json })));

    expect(el.textContent).toBe("artmind ○○●●● 2 docs · 1 table to apply");
    expect(el.classList.contains("artmind-tone-amber")).toBe(true);
    expect(el.classList.contains("status-bar-item")).toBe(true);
    expect(el.getAttribute("aria-label")).toBe("From the other laptop — 2 docs · 1 table to apply");
    el.click();
    expect(opened).toBe(1);

    item.render(checklist(readyInputs()));
    expect(el.textContent).toBe("artmind ●●●●● graph ready");
    expect(el.classList.contains("artmind-tone-amber")).toBe(false);
    expect(el.classList.contains("artmind-tone-grey")).toBe(true);
    expect(el.getAttribute("aria-label")).toBe("Graph ready ✓");
  });

  it("names the footer once it is the only step left", () => {
    const el = document.createElement("div");
    new StatusBarItem(el, () => undefined).render(checklist(readyInputs({ git: { ahead: 2, artmindChanges: [] } })));
    expect(el.textContent).toBe("artmind ●●●●○ ↑ 2 to push");
  });
});

describe("notices (checklist spec §5, D7)", () => {
  it("are text only, fade after 6 s, and a click opens the panel at their row", () => {
    const opened: string[] = [];
    new ObsidianNotifier((target) => opened.push(target)).show("pulled", "Pulled: 2 docs to apply to graph.", "remote");

    const notice = Notice.shown[0];
    expect(notice.duration).toBe(6_000);
    const holder = document.createElement("div");
    holder.appendChild(notice.message as DocumentFragment);
    expect(holder.querySelectorAll("button")).toHaveLength(0);
    expect(text(holder)).toBe("Pulled: 2 docs to apply to graph.");
    (holder.querySelector(".artmind-notice") as HTMLElement).click();
    expect(opened).toEqual(["remote"]);
    expect(notice.hidden).toBe(true);
  });

  it("an error stays 10 s; a notice with no row opens nothing", () => {
    const opened: string[] = [];
    const notifier = new ObsidianNotifier((target) => opened.push(target));
    notifier.show("error", "Couldn't apply to graph — details in the artmind panel.", "remote");
    notifier.show("prompt", "Opening the admin console…");

    expect(Notice.shown.map((n) => n.duration)).toEqual([10_000, 6_000]);
    const holder = document.createElement("div");
    holder.appendChild(Notice.shown[1].message as DocumentFragment);
    (holder.querySelector(".artmind-notice") as HTMLElement).click();
    expect(opened).toEqual([]);
  });
});

describe("the side panel (spec §3.2)", () => {
  it("shows status, pending counts, and disables actions with the reason", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ status: fixture("vault-status.merge-conflict").json, git: { ahead: 2, artmindChanges: [] } }), h);

    expect(text(root)).toContain("⚠ resolve artmind conflicts");
    expect(text(root)).toContain("Commits to push2");
    const sync = button(root, "Sync");
    expect(sync.disabled).toBe(true);
    expect(sync.title).toBe("A merge is in progress — finish it first");
    button(root, "Resolve").click();
    expect(h.calls).toEqual(["resolve"]);
  });

  it("leads with the top state and offers its action as the one primary button", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ status: fixture("vault-status.behind").json, git: { ahead: 3, artmindChanges: [] } }), h);

    const header = root.querySelector(".artmind-header")!;
    expect(header.querySelector(".artmind-health")!.textContent).toBe("◉ 2 docs · 1 table behind↑ 3 to push");
    const primary = root.querySelector(".artmind-primary button")!;
    expect(primary.textContent).toBe("Sync");
    (primary as HTMLButtonElement).click();
    expect(h.calls).toEqual(["sync"]);
  });

  it("collapses Sync and Activity by default, and remembers a toggle across renders", () => {
    const root = document.createElement("div");
    const ui = newPanelUi();
    const open = (id: string) => (root.querySelector(`[data-section="${id}"]`) as HTMLDetailsElement).open;

    renderPanel(root, snapshot({ tablesPending: fixture("table2graph.pending").json }), handlers(), ui);
    expect([open("status"), open("tables"), open("activity")]).toEqual([false, true, false]);

    const status = root.querySelector('[data-section="status"]') as HTMLDetailsElement;
    status.open = true;
    status.dispatchEvent(new Event("toggle"));
    renderPanel(root, snapshot(), handlers(), ui);
    expect(open("status")).toBe(true);
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
    renderPanel(root, snapshot({ git: { ahead: 0, artmindChanges: [".artmind/a.json", ".artmind/b.json"] } }), h);
    button(root, "Commit-and-sync").click();
    expect(h.calls).toEqual(["commitAndSync"]);

    renderPanel(
      root,
      snapshot({ git: { ahead: 0, artmindChanges: [".artmind/a.json", ".artmind/b.json"] } }, { gitMissing: { commitAndSync: "Run Obsidian Git: Commit-and-sync" }, bridgeWarning: "Obsidian Git has no Commit-and-sync command" }),
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

  it("draws a running job: file states, per-file counts scaled to the largest, chunk progress", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ activeJob: fixture("ingest-job-status.running").json }), handlers());

    const card = root.querySelector('[data-section="job"]')!;
    expect(card.querySelector("summary")!.textContent).toBe("Ingest job2/5 files");
    expect(card.querySelector(".artmind-legend")!.textContent).toBe("2 done · 1 running · 2 queued");
    expect([...card.querySelectorAll(".artmind-segment")].map((s) => [s.className.split("-").pop(), (s as HTMLElement).style.flexGrow]))
      .toEqual([["done", "2"], ["running", "1"], ["queued", "2"]]);
    expect(card.querySelector(".artmind-totals")!.textContent).toBe("34 entities · 20 relationships");

    const rows = [...card.querySelectorAll(".artmind-file")];
    const meters = (row: Element) =>
      [...row.querySelectorAll(".artmind-meter")].map((m) => [
        m.querySelector(".artmind-meter-label")!.textContent,
        (m.querySelector(".artmind-meter-fill") as HTMLElement).style.width,
      ]);
    // file2 has the most of both, so its bars are full and file1's are relative to it.
    expect(meters(rows[0])).toEqual([["12 E", "55%"], ["7 R", "54%"]]);
    expect(meters(rows[1])).toEqual([["22 E", "100%"], ["13 R", "100%"]]);
    const chunks = rows[2].querySelector(".artmind-meter-chunks")!;
    expect(chunks.getAttribute("title")).toBe("entities 4/7 · properties 0/7 · relationships 3/7");
    expect(chunks.textContent).toBe("7 chunks");
    expect(rows[3].textContent).toBe("…file4.mdqueued");
  });

  it("keeps the finished job up as Last ingest job, with each failure's reason", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({}, { jobResults: fixture("ingest-job-results.done").json }), handlers());

    const card = root.querySelector('[data-section="job"]')!;
    expect(card.querySelector("summary")!.textContent).toBe("Last ingest job2/2 files");
    expect(card.querySelector(".artmind-badge")!.classList.contains("artmind-tone-red")).toBe(true);
    const rows = [...card.querySelectorAll(".artmind-file")];
    expect(rows[0].textContent).toBe("✓file1.md12 E7 R");
    expect(rows[1].textContent).toBe("✗file2.mdKG ingestion failed");
  });

  it("offers Retry failed on a finished job's failures, and nothing on a clean or running job", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({}, { jobResults: fixture("ingest-job-results.done").json }), h);
    expect(root.querySelector(".artmind-retry")!.textContent).toBe("1 file failed.Retry failed");
    button(root, "Retry failed").click();
    expect(h.calls).toEqual(["retryJob:00000000-0000-4000-8000-000000000001"]);

    renderPanel(root, snapshot({ activeJob: fixture("ingest-job-status.running").json }), h);
    expect(root.querySelector(".artmind-retry")).toBeNull();
  });

  it("marks a stalled job and offers Retry job", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ activeJob: fixture("ingest-job-status.stalled").json }), h);

    const card = root.querySelector('[data-section="job"]')!;
    expect(card.querySelector(".artmind-section-title")!.textContent).toBe("Ingest job (stalled)");
    expect(card.querySelector(".artmind-badge")!.classList.contains("artmind-tone-amber")).toBe(true);
    button(root, "Retry job").click();
    expect(h.calls).toEqual(["retryJob:00000000-0000-4000-8000-000000000001"]);
    expect(button(root, "Retry job").disabled).toBe(true);
  });

  it("opens the admin console from the toolbar, and its dashboard from the job card", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ activeJob: fixture("ingest-job-status.running").json }), h);
    button(root, "Admin ↗").click();
    (root.querySelector(".artmind-admin-link") as HTMLAnchorElement).click();

    expect(h.calls).toEqual(["openAdmin:/", "openAdmin:/dashboard"]);
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
