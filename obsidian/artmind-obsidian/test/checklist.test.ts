import { describe, expect, it } from "vitest";
import { type Row, type RowId, changeSources, checklist, jobRunning } from "../src/checklist";
import { EMPTY_INPUTS, type StateInputs } from "../src/state";
import type { DbReview, ProjectionStatus } from "../src/types";
import { fixture } from "./fixtures";

const READY: ProjectionStatus = fixture("projection-status.ready").json;
const NO_REVIEW: DbReview = { query_type: "structured", command: "db review", pending_count: 0, tables: [] };

/** Every row done: each case changes one thing from here. */
function inputs(overrides: Partial<StateInputs> = {}): StateInputs {
  return {
    ...EMPTY_INPUTS,
    status: fixture("vault-status.in-sync").json,
    pending: { notes: [], binaries: [], tables: [], errors: [] },
    tablesPending: [],
    tablesReview: NO_REVIEW,
    projection: READY,
    synthesis: [],
    git: { ahead: 0, artmindChanges: [] },
    obsidianGit: { autoPullMinutes: 10 },
    ...overrides,
  };
}

function row(overrides: Partial<StateInputs>, id: RowId): Row {
  return checklist(inputs(overrides)).rows.find((r) => r.id === id)!;
}

/** [state, glyph, summary, button labels]. */
function shape(r: Row): [string, string, string, string[]] {
  return [r.state, r.glyph, r.summary, r.buttons.map((b) => b.label)];
}

function withStatus(edit: (status: any) => void): Partial<StateInputs> {
  const status = structuredClone(fixture("vault-status.merge-conflict").json);
  edit(status);
  return { status };
}

const lastJobOf = (name: string, at = 1_000) => ({ status: fixture(name).json, results: null, at });

describe("checklist: each row state (checklist spec §3.2)", () => {
  it.each<[string, RowId, Partial<StateInputs>, [string, string, string, string[]]]>([
    ["remote up to date", "remote", {}, ["done", "✓", "up to date", ["Pull"]]],
    ["remote behind", "remote", { status: fixture("vault-status.behind").json }, ["todo", "●", "2 docs · 1 table to apply", ["Apply to graph", "Pull"]]],
    ["remote in conflict", "remote", { status: fixture("vault-status.merge-conflict").json }, ["attention", "⚠", "1 artmind file in conflict", ["Resolve…"]]],
    ["remote merge left to complete", "remote", withStatus((s) => (s.sync.unresolved_conflicts = [])), ["todo", "●", "resolved, complete the merge", ["Complete the merge"]]],
    ["remote not pulled", "remote", withStatus((s) => {
      s.sync.operation_in_progress = null;
      s.sync.unresolved_conflicts = [];
      s.sync.stores.graph.state = "not_ancestor";
    }), ["todo", "●", "the other laptop's commits aren't pulled", ["Pull"]]],
    ["remote first time", "remote", { status: fixture("vault-status.no-bookmark").json }, ["todo", "●", "first sync on this laptop", ["Pull"]]],
    ["remote Neo4j down", "remote", { status: fixture("vault-status.neo4j-unreachable").json }, ["attention", "!", "Neo4j unreachable", ["Pull"]]],
    ["remote artmind missing", "remote", { problem: { kind: "missing", looked: [] } }, ["attention", "✗", "artmind can't run", []]],
    ["remote applying", "remote", { busy: { apply: true, rebuild: false, synthesize: false } }, ["running", "◌", "applying to graph…", []]],
    ["remote not read yet", "remote", { status: null }, ["waiting", "○", "checking…", []]],

    ["documents all ingested", "documents", {}, ["done", "✓", "all ingested", []]],
    ["documents to ingest", "documents", { pending: fixture("ingest-pending").json }, ["todo", "●", "4 to ingest", ["Ingest 4 files"]]],
    ["documents ingesting", "documents", { activeJob: fixture("ingest-job-status.running").json }, ["running", "◌", "ingesting 2/5", []]],
    ["documents finishing", "documents", { activeJob: fixture("ingest-job-status.finishing").json }, ["running", "◌", "finishing: building the graph", []]],
    ["documents stalled", "documents", { activeJob: fixture("ingest-job-status.stalled").json }, ["attention", "⚠", "stalled at 2/5", ["Retry job"]]],
    ["documents failed", "documents", { lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1 } }, ["attention", "✗", "1 failed", ["Retry failed"]]],
    ["documents not read", "documents", { pending: null }, ["waiting", "○", "checking…", []]],

    ["tables nothing waiting", "tables", {}, ["done", "✓", "nothing waiting", []]],
    ["tables ready for the graph", "tables", { tablesPending: fixture("table2graph.pending").json }, ["todo", "●", "1 ready for the graph", ["Review & project…"]]],
    ["tables with two mappings", "tables", { tablesPending: [{ table: "t", domain: "d", mapping: "a.yaml, b.yaml", reason: "ambiguous" }] }, ["todo", "●", "1 with two mappings", ["Ask admin-ui"]]],
    ["tables not read", "tables", { tablesPending: null, tablesReview: null }, ["waiting", "○", "checking…", []]],

    ["graph needs rebuild", "graph", { projection: fixture("projection-status.needs-rebuild").json }, ["attention", "!", "needs rebuild", ["Rebuild graph"]]],
    ["graph waits for the ingest", "graph", { activeJob: fixture("ingest-job-status.running").json }, ["waiting", "○", "after ingest finishes", []]],
    ["graph waits for the finishing job too", "graph", { activeJob: fixture("ingest-job-status.finishing").json }, ["waiting", "○", "after ingest finishes", []]],
    ["graph not checked", "graph", { projection: null }, ["waiting", "○", "not checked yet", ["Rebuild graph"]]],
    ["graph rebuilding", "graph", { busy: { apply: false, rebuild: true, synthesize: false } }, ["running", "◌", "rebuilding…", []]],

    ["descriptions up to date", "descriptions", {}, ["done", "✓", "up to date", []]],
    ["descriptions to synthesize", "descriptions", { synthesis: [{ domain: "general", count: 41, model: "ministral-3:14b" }] }, ["optional", "◇", "41 could be synthesized · ~41 LLM calls (ministral-3:14b)", ["Synthesize…"]]],
    ["descriptions not counted", "descriptions", { synthesis: null }, ["optional", "◇", "not counted yet", []]],
    ["descriptions synthesizing", "descriptions", { busy: { apply: false, rebuild: false, synthesize: true } }, ["running", "◌", "synthesizing…", []]],

    ["vault shared", "vault", {}, ["done", "✓", "shared", []]],
    ["vault to push", "vault", { git: { ahead: 2, artmindChanges: [] } }, ["todo", "●", "↑ 2 to push", ["Commit & push"]]],
    ["vault no upstream", "vault", { git: { ahead: null, artmindChanges: [] } }, ["done", "✓", "nothing to share (no upstream)", []]],
    ["vault hidden during a job", "vault", { activeJob: fixture("ingest-job-status.running").json, git: { ahead: 2, artmindChanges: [] } }, ["hidden", "○", "after ingest finishes", []]],
  ])("%s", (_name, id, overrides, expected) => {
    expect(shape(row(overrides, id))).toEqual(expected);
  });

  it("row 1 says when it last applied", () => {
    expect(row({ lastApply: { at: Date.now(), ok: true, summary: "x" } }, "remote").summary).toMatch(/^up to date · applied \d\d:\d\d$/);
  });

  it("row 1 asks for Obsidian Git's auto pull when it is off, and only then (D10)", () => {
    const hint = "Turn on Obsidian Git's auto pull to see the other laptop's changes";
    expect(row({ obsidianGit: { autoPullMinutes: 0 } }, "remote").detail).toEqual([hint]);
    expect(row({ obsidianGit: { autoPullMinutes: 0 }, status: fixture("vault-status.behind").json }, "remote").detail).toEqual([hint]);
    expect(row({ obsidianGit: { autoPullMinutes: 10 } }, "remote").detail).toEqual([]);
    expect(row({ obsidianGit: null }, "remote").detail).toEqual([]);
    expect(row({ obsidianGit: { autoPullMinutes: 0 }, status: null }, "remote").detail).toEqual([]);
  });

  it("row 1 keeps Pull as an outline button, never during a merge", () => {
    const behind = checklist(inputs({ status: fixture("vault-status.behind").json }));
    expect(behind.current).toBe("remote");
    expect(behind.rows[0].buttons.map((b) => [b.label, b.action.type])).toEqual([["Apply to graph", "apply"], ["Pull", "pull"]]);
    expect(row({ status: fixture("vault-status.merge-conflict").json }, "remote").buttons.map((b) => b.label)).toEqual(["Resolve…"]);
  });

  it("row 1 shows the bootstrap step for a first time on this laptop", () => {
    expect(row({ status: fixture("vault-status.no-bookmark").json }, "remote").detail.join(" ")).toContain("artmind vault sync --bootstrapEmpty");
  });

  it("row 2 expands to each path with new or changed", () => {
    const documents = row({ pending: fixture("ingest-pending").json }, "documents");
    expect(documents.items.map((i) => [i.text, i.tag])).toEqual([
      ["Notes/new_idea.md", "new"],
      ["Notes/welcome.md", "new"],
      ["Team/org_chart.pdf", "new"],
      ["Team/team.csv", "new"],
    ]);
  });

  it("row 2 expands a failed job to each failure and its error", () => {
    const documents = row({ lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1 } }, "documents");
    expect(documents.items.map((i) => [i.text, i.tag])).toEqual([["/vault/Notes/file2.md", "KG ingestion failed"]]);
    expect(documents.buttons[0].action).toEqual({ type: "retryJob", jobId: "00000000-0000-4000-8000-000000000001" });
  });

  it("row 2 warns before an ingest whose graph write will fail", () => {
    const documents = row({ status: fixture("vault-status.neo4j-unreachable").json, pending: fixture("ingest-pending").json }, "documents");
    expect(documents.detail).toContain("Neo4j is unreachable: extraction will run, the graph write will fail.");
    expect(documents.buttons[0].blockedReason).toBeNull();
  });

  it("row 3 asks the admin console to review unconfirmed classifications, with a prefilled prompt", () => {
    const review: DbReview = fixture("db-review").json;
    const n = review.tables.length;
    const tables = row({ tablesReview: review }, "tables");
    expect(shape(tables)).toEqual(["todo", "●", `${n} need${n === 1 ? "s" : ""} review`, ["Review in admin console ↗"]]);
    expect(tables.buttons[0].action).toEqual({
      type: "adminPrompt",
      prompt: `Review the proposed classifications for table ${review.tables[0].table} (domain ${review.tables[0].domain})`,
    });
    expect(tables.items[0].text).toBe(`${review.tables[0].table} (${review.tables[0].domain})`);
    expect(tables.items[0].tag).toMatch(/unconfirmed$/);
  });

  it("row 3 shows review and projection side by side", () => {
    const tables = row({ tablesReview: fixture("db-review").json, tablesPending: fixture("table2graph.pending").json }, "tables");
    expect(tables.buttons.map((b) => b.label)).toEqual(["Review in admin console ↗", "Review & project…"]);
    expect(tables.buttons[1].action).toEqual({ type: "reviewTable", table: "team" });
  });

  it("row 4 gives each reason a line", () => {
    expect(row({ projection: fixture("projection-status.needs-rebuild").json }, "graph").detail).toEqual([
      "same_as edited",
      "2,813 entities not embedded",
      "310 chunks not embedded",
      "40 observation keys not projected",
    ]);
    const never = { ...READY, known: false, drift: true, same_as_drift: true, schema_drift: true };
    expect(row({ projection: never }, "graph").detail).toEqual(["never rebuilt on this laptop"]);
  });

  it("row 4 is built, embedded, and says when it was rebuilt", () => {
    expect(row({}, "graph").summary).toMatch(/^built · embedded · rebuilt \d\d:\d\d$/);
  });

  it("row 4 names a job's failed rebuild until a clean rebuild supersedes it", () => {
    const job = lastJobOf("ingest-job-status.finalize-failed", 1_000);
    const error = fixture("ingest-job-status.finalize-failed").json.finalize.error;

    const failed = row({ lastJob: job }, "graph");
    expect(shape(failed)).toEqual(["attention", "!", "needs rebuild", ["Rebuild graph"]]);
    expect(failed.detail).toEqual([`last job's rebuild failed: ${error}`]);

    expect(row({ lastJob: job, lastRebuild: { at: 2_000, ok: true, summary: "Graph rebuilt", errors: [] } }, "graph").state).toBe("done");
    expect(row({ lastJob: job, lastRebuild: { at: 500, ok: true, summary: "older", errors: [] } }, "graph").state).toBe("attention");
  });

  it("row 4 keeps a rebuild's sweep errors, and its failure, as reasons", () => {
    const swept = row({ lastRebuild: { at: 1, ok: true, summary: "x", errors: ["general: Connection refused"] } }, "graph");
    expect(swept.detail).toEqual(["sweep failed: general: Connection refused"]);
    const failed = row({ lastRebuild: { at: 1, ok: false, summary: "MemoryPoolOutOfMemoryError", errors: [] } }, "graph");
    expect(failed.detail).toEqual(["last rebuild failed: MemoryPoolOutOfMemoryError"]);
  });

  it("the footer says what the artmind files came from", () => {
    const vault = row({
      git: {
        ahead: 0,
        artmindChanges: [
          ".artmind/data/kg/general/doc1/document.json",
          ".artmind/data/kg/general/doc1/chunks.json",
          ".artmind/data/curation/syntheses/e1.json",
          ".artmind/same_as.yaml",
        ],
      },
    }, "vault");
    expect(shape(vault)).toEqual(["todo", "●", "✎ 4 artmind files to commit · ↑ 0", ["Commit & push"]]);
    expect(vault.detail).toEqual(["from: ingest (1 file) · syntheses (1) · other (1)"]);
  });

  it("a failed action's raw error goes to its row's detail", () => {
    expect(row({ status: fixture("vault-status.behind").json, actionErrors: { remote: "git diff failed: fatal" } }, "remote").detail).toEqual([
      "git diff failed: fatal",
    ]);
  });
});

describe("checklist: the current step, ready, and the footer (checklist spec §3.2, D3, D4)", () => {
  it("everything done is a ready graph with no current step", () => {
    const cl = checklist(inputs());
    expect([cl.ready, cl.current, cl.stepsLeft]).toEqual([true, null, 0]);
  });

  it("the current step is the first row that is not done", () => {
    const cl = checklist(inputs({ status: fixture("vault-status.behind").json, pending: fixture("ingest-pending").json }));
    expect([cl.ready, cl.current, cl.stepsLeft]).toEqual([false, "remote", 2]);
  });

  it("Descriptions is never the current step, and never stops the graph being ready", () => {
    const cl = checklist(inputs({ synthesis: [{ domain: "general", count: 41, model: "m" }] }));
    expect([cl.ready, cl.current]).toEqual([true, null]);
  });

  it("the footer is the current step only once rows 1-4 are done", () => {
    const changes = { ahead: 1, artmindChanges: [] };
    expect(checklist(inputs({ git: changes })).current).toBe("vault");
    expect(checklist(inputs({ git: changes, projection: fixture("projection-status.needs-rebuild").json })).current).toBe("graph");
  });

  it("a running job is the current step", () => {
    expect(checklist(inputs({ activeJob: fixture("ingest-job-status.finishing").json })).current).toBe("documents");
  });
});

describe("checklist: blocked commands (checklist spec §4, palette)", () => {
  it("a running job blocks every write but resolve, and the footer", () => {
    const { blocked } = checklist(inputs({ activeJob: fixture("ingest-job-status.running").json, status: fixture("vault-status.behind").json, git: { ahead: 1, artmindChanges: [] } }));
    expect(blocked.apply).toBe("An ingest job is running — apply after it finishes");
    expect(blocked.ingest).toBe("An ingest job is already running");
    expect(blocked.rebuild).toBe("Rebuild after the ingest finishes");
    expect(blocked.commitPush).toBe("Files are still being written — commit after the ingest finishes");
  });

  it("a stalled job blocks nothing: its worker is gone", () => {
    const { blocked } = checklist(inputs({ activeJob: fixture("ingest-job-status.stalled").json, pending: fixture("ingest-pending").json }));
    expect(blocked.ingest).toBeNull();
    expect(checklist(inputs({ activeJob: fixture("ingest-job-status.stalled").json })).jobRunning).toBe(false);
  });

  it("artmind missing blocks everything that runs artmind", () => {
    const { blocked } = checklist(inputs({ problem: { kind: "missing", looked: [] } }));
    for (const command of ["apply", "ingest", "rebuild", "synthesize", "tables", "resolve", "doctor", "admin"] as const) {
      expect(blocked[command]).toBe("artmind not found (looked in nowhere)");
    }
    expect(blocked.pull).toBeNull();
  });

  it("Neo4j unreachable blocks the graph writes but lets ingest run", () => {
    const { blocked, neo4jUnreachable } = checklist(inputs({ status: fixture("vault-status.neo4j-unreachable").json, pending: fixture("ingest-pending").json, projection: fixture("projection-status.needs-rebuild").json, synthesis: [{ domain: "general", count: 2, model: "m" }] }));
    expect(neo4jUnreachable).toBe(true);
    expect([blocked.apply, blocked.rebuild, blocked.synthesize, blocked.ingest]).toEqual(["Neo4j unreachable", "Neo4j unreachable", "Neo4j unreachable", null]);
  });

  it("says why there is nothing to do", () => {
    const { blocked } = checklist(inputs());
    expect(blocked.apply).toBe("Nothing to apply: the graph has every commit");
    expect(blocked.ingest).toBe("Nothing new or changed to ingest");
    expect(blocked.rebuild).toBe("The graph is up to date");
    expect(blocked.synthesize).toBe("Nothing to synthesize");
    expect(blocked.commitPush).toBe("Nothing to commit or push");
    expect(blocked.tables).toBe("No tables waiting");
    expect(blocked.resolve).toBe("No merge in progress");
    expect(checklist(inputs({ pending: null })).blocked.ingest).toBeNull();
    expect(checklist(inputs({ synthesis: null })).blocked.synthesize).toBe("Not counted yet: press ↻");
  });

  it("a merge in progress blocks apply and ingest", () => {
    const { blocked } = checklist(inputs({ status: fixture("vault-status.merge-conflict").json, pending: fixture("ingest-pending").json }));
    expect(blocked.ingest).toBe("A merge is in progress — finish it first");
    expect(blocked.pull).toBe("A merge is in progress — finish it first");
    expect(blocked.resolve).toBeNull();
  });
});

describe("helpers", () => {
  it("a job runs while it is queued, processing or finalizing, never stalled", () => {
    expect(jobRunning(fixture("ingest-job-status.running").json)).toBe(true);
    expect(jobRunning(fixture("ingest-job-status.finishing").json)).toBe(true);
    expect(jobRunning(fixture("ingest-job-status.stalled").json)).toBe(false);
    expect(jobRunning(fixture("ingest-job-status.done").json)).toBe(false);
    expect(jobRunning({ ...fixture("ingest-job-status.done").json, finalize: { state: "pending", domains: ["general"], error: null } })).toBe(true);
    expect(jobRunning(null)).toBe(false);
  });

  it("counts docs once however many of their files changed", () => {
    expect(changeSources([
      ".artmind/data/kg/general/a/document.json",
      ".artmind/data/kg/general/a/observations.json",
      ".artmind/data/kg/habits/b/document.json",
      ".artmind/data/curation/syntheses/x.json",
      ".artmind/data/curation/syntheses/y.json",
      ".artmind/vault.yaml",
    ])).toEqual({ ingest: 2, syntheses: 2, other: 1 });
  });
});
