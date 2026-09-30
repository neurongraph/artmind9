import { describe, expect, it } from "vitest";
import { errorText } from "../src/cli";
import { type ArtmindProblem, type StateInputs, type StateKind, classifyRefusal, vaultState } from "../src/state";
import type { IngestPending } from "../src/types";
import { fixture } from "./fixtures";

const NOTHING_PENDING: IngestPending = { notes: [], binaries: [], tables: [], errors: [] };

/** In sync, nothing waiting: every case changes one thing from here. */
function inputs(overrides: Partial<StateInputs> = {}): StateInputs {
  return {
    status: fixture("vault-status.in-sync").json,
    activeJob: null,
    pending: NOTHING_PENDING,
    tablesPending: [],
    git: { ahead: 0, unsharedArtmindChanges: 0 },
    problem: null,
    ...overrides,
  };
}

/** One input per state, each from a real fixture where the CLI has one. */
const TRIGGERS: Record<Exclude<StateKind, "in_sync">, Partial<StateInputs>> = {
  conflict: { status: fixture("vault-status.merge-conflict").json },
  error: { status: fixture("vault-status.neo4j-unreachable").json },
  job: { activeJob: fixture("ingest-job-status.running").json },
  behind: { status: fixture("vault-status.behind").json },
  tables: { tablesPending: fixture("table2graph.pending").json },
  ingest: { pending: fixture("ingest-pending").json },
};

describe("vaultState: each state alone (spec §3.1)", () => {
  it.each([
    ["in_sync", {}, "◉ artmind", "grey", { type: "openPanel", section: "status" }],
    ["conflict", TRIGGERS.conflict, "⚠ resolve artmind conflicts", "red", { type: "openResolve" }],
    ["error", TRIGGERS.error, "⚠ artmind", "red", { type: "openPanel", section: "error" }],
    ["job", TRIGGERS.job, "◌ ingesting 2/5", "spinner", { type: "openPanel", section: "job" }],
    ["behind", TRIGGERS.behind, "◉ 2 docs · 1 table behind", "amber", { type: "sync" }],
    ["tables", TRIGGERS.tables, "◉ 1 table → graph", "blue", { type: "reviewTables" }],
    ["ingest", TRIGGERS.ingest, "◉ 4 to ingest", "blue", { type: "ingest" }],
  ] as const)("%s", (kind, overrides, label, tone, action) => {
    const state = vaultState(inputs(overrides as Partial<StateInputs>));

    expect(state.primary.kind).toBe(kind);
    expect(state.primary.label).toBe(label);
    expect(state.primary.tone).toBe(tone);
    expect(state.primary.action).toEqual(action);
  });

  it("a sync that was never set up asks for the bootstrap step", () => {
    const state = vaultState(inputs({ status: fixture("vault-status.no-bookmark").json }));

    expect(state.primary.label).toBe("◉ sync not set up");
    expect(state.primary.action).toEqual({ type: "openPanel", section: "bootstrap" });
    expect(state.blocked.sync).toBe("This laptop hasn't synced this vault before");
  });

  it("a bookmark not in this clone's history asks for a pull", () => {
    const status = structuredClone(fixture("vault-status.behind").json);
    status.sync.stores.graph.state = "not_ancestor";

    const state = vaultState(inputs({ status }));

    expect(state.primary.label).toBe("◉ pull first");
    expect(state.primary.action).toEqual({ type: "pull" });
  });

  it("artmind missing or too old is the error state, and blocks every action", () => {
    const problems: ArtmindProblem[] = [
      { kind: "missing", looked: ["/Users/me/.local/bin/artmind"] },
      { kind: "too_old", found: "0.8.0", need: "0.9.0" },
    ];
    for (const problem of problems) {
      const state = vaultState(inputs({ problem }));
      expect(state.primary.kind).toBe("error");
      expect(Object.values(state.blocked).every((reason) => reason !== null)).toBe(true);
    }
  });

  it("ambiguous tables do not count as waiting", () => {
    const tablesPending = [{ table: "t", domain: "d", mapping: "a.yaml, b.yaml", reason: "ambiguous" }];
    expect(vaultState(inputs({ tablesPending })).primary.kind).toBe("in_sync");
  });
});

describe("vaultState: priority clashes", () => {
  const order = Object.keys(TRIGGERS) as Array<keyof typeof TRIGGERS>;
  const pairs = order.flatMap((higher, i) => order.slice(i + 1).map((lower) => [higher, lower] as const));

  /** In-sync inputs with each named state's fixture facts added. */
  function combine(kinds: Array<keyof typeof TRIGGERS>): StateInputs {
    const status = structuredClone(fixture("vault-status.in-sync").json);
    const out = inputs({ status });
    for (const kind of kinds) {
      if (kind === "conflict") {
        const sync = fixture("vault-status.merge-conflict").json.sync;
        status.sync.operation_in_progress = sync.operation_in_progress;
        status.sync.unresolved_conflicts = sync.unresolved_conflicts;
      } else if (kind === "error") {
        status.sync.graph_error = fixture("vault-status.neo4j-unreachable").json.sync.graph_error;
      } else if (kind === "behind") {
        status.sync.stores = structuredClone(fixture("vault-status.behind").json.sync.stores);
      } else {
        Object.assign(out, TRIGGERS[kind]);
      }
    }
    return out;
  }

  it.each(pairs)("%s beats %s", (higher, lower) => {
    const state = vaultState(combine([higher, lower]));

    expect(state.primary.kind).toBe(higher);
    expect(state.states.map((s) => s.kind)).toEqual([higher, lower]);
  });

  it("lists every applying state in priority order for the panel", () => {
    const status = structuredClone(fixture("vault-status.behind").json);
    status.sync.unresolved_conflicts = [".artmind/same_as.yaml"];
    status.sync.operation_in_progress = "a merge";

    const state = vaultState(
      inputs({ ...TRIGGERS.job, ...TRIGGERS.tables, ...TRIGGERS.ingest, status, problem: { kind: "failed", command: "x", message: "y" } }),
    );

    expect(state.states.map((s) => s.kind)).toEqual(["conflict", "error", "job", "behind", "tables", "ingest"]);
  });
});

describe("vaultState: secondary indicators and blocked actions", () => {
  it("shows commits to push and artmind changes to share", () => {
    const state = vaultState(inputs({ git: { ahead: 3, unsharedArtmindChanges: 2 } }));

    expect(state.secondary).toEqual(["↑ 3 to push", "✎ artmind changes to share"]);
  });

  it("blocks sync while a job runs or a merge is in progress; resolve without a merge", () => {
    expect(vaultState(inputs(TRIGGERS.job)).blocked.sync).toBe("An ingest job is running — sync after it finishes");
    const merge = vaultState(inputs(TRIGGERS.conflict));
    expect(merge.blocked.sync).toBe("A merge is in progress — finish it first");
    expect(merge.blocked.resolve).toBeNull();
    expect(vaultState(inputs()).blocked.resolve).toBe("No merge in progress");
  });

  it("with Neo4j unreachable, blocks sync and tables but lets ingest run with a warning (spec §8)", () => {
    const state = vaultState(inputs({ ...TRIGGERS.error, ...TRIGGERS.ingest, ...TRIGGERS.tables }));

    expect(state.neo4jUnreachable).toBe(true);
    expect(state.primary.detail).toContain("Neo4j unreachable (neo4j://127.0.0.1:7687)");
    expect(state.blocked.sync).toBe("Neo4j unreachable");
    expect(state.blocked.tables).toBe("Neo4j unreachable");
    expect(state.blocked.ingest).toBeNull();
    expect(state.warnings.ingest).toMatch(/graph write will fail/);
  });

  it("says why Ingest has nothing to do", () => {
    expect(vaultState(inputs()).blocked.ingest).toBe("Nothing new or changed to ingest");
  });
});

describe("classifyRefusal: every vault sync refusal becomes an action (spec §4.2)", () => {
  it.each([
    ["vault-sync.refusal-worker-running", "worker_running"],
    ["vault-sync.refusal-merge-in-progress", "resolve_first"],
    ["vault-sync.refusal-artmind-conflicts", "resolve_first"],
    ["vault-sync.refusal-bookmark-unpulled", "pull_first"],
    ["vault-sync.refusal-bookmark-not-ancestor", "pull_first"],
    ["vault-sync.refusal-no-bookmark", "no_bookmark"],
  ])("%s -> %s", (name, kind) => {
    const f = fixture(name);
    expect(f.exit_code).toBe(1);
    expect(classifyRefusal(errorText(f.stderr!)!).kind).toBe(kind);
  });

  it("anything else stays a plain failure", () => {
    expect(classifyRefusal("git diff failed: fatal")).toEqual({ kind: "other", text: "git diff failed: fatal" });
  });

  it("shows the bootstrap step for a first sync, and never runs it", () => {
    expect(classifyRefusal(errorText(fixture("vault-sync.refusal-no-bookmark").stderr!)!).text).toContain(
      "artmind vault sync --bootstrapEmpty",
    );
  });
});
