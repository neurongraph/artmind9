// @vitest-environment jsdom
// The same module `obsidian` resolves to in tests (vitest.config.ts), imported
// by path for its test-only record of the notices shown.
import { Notice } from "./mocks/obsidian";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { checklist } from "../src/checklist";
import type { Snapshot } from "../src/controller";
import { EMPTY_INPUTS, type StateInputs } from "../src/state";
import { ObsidianNotifier } from "../src/views/notices";
import { ArtmindView, type PanelHandlers, newPanelUi, renderPanel } from "../src/views/panel";
import { ResolveModal, renderResolve, resolveBlockedReason } from "../src/views/resolveModal";
import { StatusBarItem } from "../src/views/statusBar";
import { LOOKUP_PREVIEW, TableReviewModal, renderTableProblem, renderTableReview } from "../src/views/tableReview";
import { fixture } from "./fixtures";

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

function snapshot(overrides: Partial<StateInputs> = {}, extra: Partial<Snapshot> = {}): Snapshot {
  const inputs = readyInputs(overrides);
  return {
    inputs,
    checklist: checklist(inputs),
    activity: [],
    doctor: null,
    readErrors: [],
    bridgeWarning: null,
    gitMissing: {},
    looked: [],
    ...extra,
  };
}

function handlers(): PanelHandlers & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    refresh: () => void calls.push("refresh"),
    run: (action) => void calls.push(`run:${JSON.stringify(action)}`),
    openAdmin: (path) => void calls.push(path === undefined ? "openAdmin" : `openAdmin:${path}`),
    doctor: () => void calls.push("doctor"),
    setPath: () => void calls.push("setPath"),
    copy: (text) => void calls.push(`copy:${text}`),
  };
}

function text(root: Element): string {
  return root.textContent ?? "";
}

function button(root: Element, label: string): HTMLButtonElement {
  const found = [...root.querySelectorAll("button")].find((b) => b.textContent === label);
  if (!found) throw new Error(`no button "${label}"`);
  return found;
}

function rowEl(root: HTMLElement, id: string): HTMLElement {
  const found = root.querySelector<HTMLElement>(`[data-row="${id}"]`);
  if (!found) throw new Error(`no row ${id}`);
  return found;
}

const RUNNING_JOB = { activeJob: fixture("ingest-job-status.running").json };
const FAILED_LAST_JOB = { lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1 } };

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

describe("the side panel: the readiness checklist (checklist spec §3)", () => {
  it("the header names the vault, says how far the graph is from ready, and ↻ re-reads", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json }), h);

    expect(root.querySelector(".artmind-header-vault")!.textContent).toBe("artmind · vault");
    expect(root.querySelector(".artmind-readiness")!.textContent).toBe("2 steps to a ready graph");
    button(root, "↻").click();
    expect(h.calls).toEqual(["refresh"]);

    renderPanel(root, snapshot(), h);
    expect(root.querySelector(".artmind-readiness")!.textContent).toBe("Graph ready ✓");
  });

  it("lists the five rows, then the footer, each with its glyph, title and summary", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ projection: fixture("projection-status.needs-rebuild").json }), handlers());

    expect([...root.querySelectorAll("[data-row]")].map((r) => r.getAttribute("data-row"))).toEqual([
      "remote",
      "documents",
      "tables",
      "graph",
      "descriptions",
      "vault",
    ]);
    const graph = rowEl(root, "graph");
    expect(graph.querySelector(".artmind-step-line")!.textContent).toBe("!Graphneeds rebuild");
    expect([...graph.querySelectorAll(".artmind-step-detail")].map((d) => d.textContent)).toEqual([
      "same_as edited",
      "2,813 entities not embedded",
      "310 chunks not embedded",
      "40 observation keys not projected",
    ]);
    expect(rowEl(root, "vault").parentElement!.classList.contains("artmind-footer")).toBe(true);
  });

  it("only the current step's button is filled; the others are outline and still work", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json }), h);

    const filled = [...root.querySelectorAll("button.mod-cta")];
    expect(filled.map((b) => b.textContent)).toEqual(["Apply to graph"]);
    expect(rowEl(root, "remote").contains(filled[0])).toBe(true);
    const ingest = button(rowEl(root, "documents"), "Ingest 4 files");
    expect(ingest.classList.contains("artmind-outline")).toBe(true);
    expect(ingest.disabled).toBe(false);
    ingest.click();
    expect(h.calls).toEqual(['run:{"type":"ingest"}']);
  });

  it("a blocked button is disabled, with its reason", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ ...RUNNING_JOB, status: fixture("vault-status.behind").json }), handlers());

    const apply = button(rowEl(root, "remote"), "Apply to graph");
    expect(apply.disabled).toBe(true);
    expect(apply.title).toBe("An ingest job is running — apply after it finishes");
  });

  it("row 2 expands to its paths, and stays open across renders", () => {
    const root = document.createElement("div");
    const ui = newPanelUi();
    const s = snapshot({ pending: fixture("ingest-pending").json });

    renderPanel(root, s, handlers(), ui);

    const items = root.querySelector<HTMLDetailsElement>('[data-items="documents"]')!;
    expect(items.querySelector("summary")!.textContent).toBe("new_idea.md (new) · welcome.md (new) · org_chart.pdf (new) · +1");
    expect([...items.querySelectorAll(".artmind-step-item")].map((i) => i.textContent)).toEqual([
      "Notes/new_idea.md (new)",
      "Notes/welcome.md (new)",
      "Team/org_chart.pdf (new)",
      "Team/team.csv (new)",
    ]);
    expect(items.open).toBe(false);
    items.open = true;
    items.dispatchEvent(new Event("toggle"));
    renderPanel(root, s, handlers(), ui);
    expect(root.querySelector<HTMLDetailsElement>('[data-items="documents"]')!.open).toBe(true);
  });

  it("row 3 deep-links into the admin console's chat", () => {
    const root = document.createElement("div");
    const h = handlers();
    const review = fixture("db-review").json;

    renderPanel(root, snapshot({ tablesReview: review }), h);
    button(rowEl(root, "tables"), "Review in admin console ↗").click();

    const prompt = `Review the proposed classifications for table ${review.tables[0].table} (domain ${review.tables[0].domain})`;
    expect(h.calls).toEqual([`run:${JSON.stringify({ type: "adminPrompt", prompt })}`]);
  });

  it("the footer is hidden during a job, and filled only once rows 1-4 are done", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ ...RUNNING_JOB, git: { ahead: 2, artmindChanges: [] } }), handlers());
    expect(root.querySelector('[data-row="vault"]')).toBeNull();

    renderPanel(root, snapshot({ git: { ahead: 2, artmindChanges: [] } }), handlers());
    expect(button(rowEl(root, "vault"), "Commit & push").classList.contains("mod-cta")).toBe(true);

    renderPanel(root, snapshot({ pending: fixture("ingest-pending").json, git: { ahead: 2, artmindChanges: [] } }), handlers());
    expect(button(rowEl(root, "vault"), "Commit & push").classList.contains("artmind-outline")).toBe(true);
  });

  it("row 2 holds a running job's card: file states, per-file counts scaled to the largest, chunk progress", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot(RUNNING_JOB), handlers());

    const card = rowEl(root, "documents").querySelector('[data-section="job"]')!;
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
    expect(meters(rows[0])).toEqual([["12 E", "55%"], ["7 R", "54%"]]);
    expect(meters(rows[1])).toEqual([["22 E", "100%"], ["13 R", "100%"]]);
    const chunks = rows[2].querySelector(".artmind-meter-chunks")!;
    expect(chunks.getAttribute("title")).toBe("entities 4/7 · properties 0/7 · relationships 3/7");
    expect(chunks.textContent).toBe("7 chunks");
    expect(rows[3].textContent).toBe("…file4.mdqueued");
  });

  it("keeps the last job up as Last ingest job, each failure with its reason, and Retry failed on row 2", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot(FAILED_LAST_JOB), h);

    const card = root.querySelector('[data-section="job"]')!;
    expect(card.querySelector("summary")!.textContent).toBe("Last ingest job2/2 files");
    expect(card.querySelector(".artmind-badge")!.classList.contains("artmind-tone-red")).toBe(true);
    const rows = [...card.querySelectorAll(".artmind-file")];
    expect(rows[0].textContent).toBe("✓file1.md12 E7 R");
    expect(rows[1].textContent).toBe("✗file2.mdKG ingestion failed");
    expect(card.querySelector(".artmind-retry")).toBeNull();
    button(rowEl(root, "documents"), "Retry failed").click();
    expect(h.calls).toEqual(['run:{"type":"retryJob","jobId":"00000000-0000-4000-8000-000000000001"}']);
  });

  it("marks a stalled job, and row 2 offers Retry job", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ activeJob: fixture("ingest-job-status.stalled").json }), h);

    const card = root.querySelector('[data-section="job"]')!;
    expect(card.querySelector(".artmind-section-title")!.textContent).toBe("Ingest job (stalled)");
    expect(card.querySelector(".artmind-badge")!.classList.contains("artmind-tone-amber")).toBe(true);
    button(rowEl(root, "documents"), "Retry job").click();
    expect(h.calls).toEqual(['run:{"type":"retryJob","jobId":"00000000-0000-4000-8000-000000000001"}']);
  });

  it("opens the admin console from the tools, and its dashboard from the job card", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot(RUNNING_JOB), h);
    button(root, "Admin ↗").click();
    (root.querySelector(".artmind-admin-link") as HTMLAnchorElement).click();

    expect(h.calls).toEqual(["openAdmin:/", "openAdmin:/dashboard"]);
  });

  it("collapses Stores and Activity by default, and remembers a toggle across renders", () => {
    const root = document.createElement("div");
    const ui = newPanelUi();
    const open = (id: string) => (root.querySelector(`[data-section="${id}"]`) as HTMLDetailsElement).open;

    renderPanel(root, snapshot(), handlers(), ui);
    expect([open("status"), open("activity")]).toEqual([false, false]);
    expect(root.querySelector('[data-section="status"] .artmind-section-title')!.textContent).toBe("Stores");

    const status = root.querySelector('[data-section="status"]') as HTMLDetailsElement;
    status.open = true;
    status.dispatchEvent(new Event("toggle"));
    renderPanel(root, snapshot(), handlers(), ui);
    expect(open("status")).toBe(true);
  });

  it("shows Neo4j unreachable and failed reads in the problems callout", () => {
    const root = document.createElement("div");

    renderPanel(root, snapshot({ status: fixture("vault-status.neo4j-unreachable").json }, { readErrors: ["artmind db review --compact: boom"] }), handlers());

    const problems = root.querySelector('[data-section="problems"]')!;
    expect(text(problems)).toContain("Neo4j unreachable (neo4j://127.0.0.1:7687)");
    expect(text(problems)).toContain("artmind db review --compact: boom");
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

  it("explains a missing artmind and offers Set path", () => {
    const root = document.createElement("div");
    const h = handlers();

    renderPanel(root, snapshot({ problem: { kind: "missing", looked: ["/Users/me/.local/bin/artmind"] } }), h);

    expect(text(root)).toContain("Looked in: /Users/me/.local/bin/artmind");
    button(root, "Set path").click();
    expect(h.calls).toEqual(["setPath"]);
  });

  it("shows the bootstrap step in row 1 when this laptop never applied the vault", () => {
    const root = document.createElement("div");
    renderPanel(root, snapshot({ status: fixture("vault-status.no-bookmark").json }), handlers());
    expect(text(rowEl(root, "remote"))).toContain("artmind vault sync --bootstrapEmpty");
  });

  it("lists the activity, each run expanding to its raw output", () => {
    const root = document.createElement("div");
    const activity = [{ command: "artmind vault sync --compact", at: 0, ok: false, summary: "failed: boom", output: "raw stderr" }];

    renderPanel(root, snapshot({}, { activity }), handlers());

    const details = root.querySelector("details.artmind-run-failed")!;
    expect(details.querySelector("summary")!.textContent).toContain("artmind vault sync --compact — failed: boom");
    expect(details.querySelector("pre")!.textContent).toBe("raw stderr");
  });

  it("focusing a row expands its list", () => {
    const view = new ArtmindView({} as never, handlers());
    view.update(snapshot({ pending: fixture("ingest-pending").json }));

    view.focus("documents");

    expect(view.contentEl.querySelector<HTMLDetailsElement>('[data-items="documents"]')!.open).toBe(true);
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
