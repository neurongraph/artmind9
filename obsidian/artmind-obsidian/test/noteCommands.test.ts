// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { type CliResult, isWrite } from "../src/cli";
import { type NoteAsk, type NoteDeps, NoteCommands, type Older } from "../src/noteCommands";
import { renderOlderPicker } from "../src/views/noteModals";

const MAPPINGS = [{ path: "Notes/**", domain: "general" }];
const NOTE = "Notes/new-note.md";

function res(args: string[], ok = true, json: unknown = { effect: { summary: "would do it" } }): CliResult {
  return { args, exitCode: ok ? 0 : 1, ok, json, stdout: "", stderr: "", error: ok ? null : "boom", timedOut: false, startedAt: 0, durationMs: 1 };
}

function setup(opts: { answers?: boolean[]; older?: Older | null; failDry?: boolean } = {}) {
  const log: string[] = [];
  const runs: string[][] = [];
  const asks: NoteAsk[] = [];
  const answers = [...(opts.answers ?? [true, true])];
  const notices: string[] = [];
  const history: Array<[string, unknown]> = [];
  const deps: NoteDeps = {
    run: async (args) => {
      runs.push(args);
      log.push(args.includes("--dryRun") ? "dry" : "real");
      return res(args, !(opts.failDry && args.includes("--dryRun")));
    },
    record: () => {},
    ask: async (request) => {
      asks.push(request);
      log.push("ask");
      return answers.shift() ?? false;
    },
    pickOlder: async () => (opts.older === undefined ? { older: "old-note.md", scope: "document", effective: "" } : opts.older),
    showHistory: (title, json) => history.push([title, json]),
    notify: (_k, text) => notices.push(text),
    refresh: async () => {
      log.push("refresh");
    },
    candidates: () => ["old-note.md", "other.md"],
  };
  return { cmds: new NoteCommands(deps, () => MAPPINGS), log, runs, asks, notices, history };
}

describe("note commands: argument lists", () => {
  it("supersede, with scope and an effective date", async () => {
    const t = setup({ older: { older: "old-note.md", scope: "document", effective: "2026-01-02" } });
    await t.cmds.supersedes(NOTE);
    const args = ["ingest", "supersede", "--domain", "general", "--newer", "new-note.md", "--older", "old-note.md", "--scope", "document", "--effective", "2026-01-02"];
    expect(t.runs).toEqual([[...args, "--dryRun"], args]);
  });

  it("supersede without a date sends no --effective", async () => {
    const t = setup();
    await t.cmds.supersedes(NOTE);
    expect(t.runs[1]).not.toContain("--effective");
  });

  it("undo a supersession", async () => {
    const t = setup();
    await t.cmds.undoSupersession(NOTE);
    const args = ["ingest", "unsupersede", "--domain", "general", "--newer", "new-note.md", "--older", "old-note.md", "--scope", "document"];
    expect(t.runs).toEqual([[...args, "--dryRun"], args]);
  });

  it.each([
    ["retire", ["docs", "retire"]],
    ["restore", ["docs", "restore"]],
  ] as const)("%s", async (name, cmd) => {
    const t = setup();
    await t.cmds[name](NOTE);
    const args = [...cmd, "--domain", "general", "--documentName", "new-note.md"];
    expect(t.runs).toEqual([[...args, "--dryRun"], args]);
  });

  it("archive adds --yes only to the real run", async () => {
    const t = setup();
    await t.cmds.archive(NOTE);
    const args = ["docs", "archive", "--domain", "general", "--documentName", "new-note.md"];
    expect(t.runs).toEqual([[...args, "--dryRun"], [...args, "--yes"]]);
  });

  it("history is one read-only call shown in a modal", async () => {
    const t = setup();
    await t.cmds.history(NOTE);
    expect(t.runs).toEqual([["query", "document-history", "--domain", "general", "--documentName", "new-note.md"]]);
    expect(t.history).toEqual([["History of new-note.md", { effect: { summary: "would do it" } }]]);
    expect(t.asks).toEqual([]);
  });
});

describe("note commands: dry run first, confirm, then the real run", () => {
  it("dry run, the effect in the question, confirm, real run, refresh", async () => {
    const t = setup();
    await t.cmds.retire(NOTE);
    expect(t.log).toEqual(["dry", "ask", "real", "refresh"]);
    expect(t.asks[0].text).toContain("would do it");
  });

  it("cancel runs nothing real", async () => {
    const t = setup({ answers: [false] });
    await t.cmds.retire(NOTE);
    expect(t.log).toEqual(["dry", "ask"]);
  });

  it("cancelling the picker runs nothing at all", async () => {
    const t = setup({ older: null });
    await t.cmds.supersedes(NOTE);
    expect(t.runs).toEqual([]);
  });

  it("a failed dry run stops there with a notice", async () => {
    const t = setup({ failDry: true });
    await t.cmds.retire(NOTE);
    expect(t.log).toEqual(["dry"]);
    expect(t.notices[0]).toMatch(/Couldn't retire/);
  });

  it("archive asks twice, the second naming the vault file and the way back", async () => {
    const t = setup();
    await t.cmds.archive(NOTE);
    expect(t.log).toEqual(["dry", "ask", "ask", "real", "refresh"]);
    expect(t.asks[1].text).toContain(NOTE);
    expect(t.asks[1].text).toContain("restore-from-archive");
  });

  it("archive stops after a cancelled second confirmation", async () => {
    const t = setup({ answers: [true, false] });
    await t.cmds.archive(NOTE);
    expect(t.log).toEqual(["dry", "ask", "ask"]);
  });

  it("archive stops after a cancelled first confirmation", async () => {
    const t = setup({ answers: [false] });
    await t.cmds.archive(NOTE);
    expect(t.log).toEqual(["dry", "ask"]);
  });

  it("nothing runs without an active note or outside a mapped folder", async () => {
    const t = setup();
    await t.cmds.retire(null);
    await t.cmds.retire("Elsewhere/x.md");
    expect(t.runs).toEqual([]);
    expect(t.notices).toHaveLength(2);
  });
});

describe("the CLI treats the new writes as writes, their dry runs as reads", () => {
  it("queues the real run only", () => {
    expect(isWrite(["docs", "archive", "--domain", "d", "--yes"])).toBe(true);
    expect(isWrite(["docs", "archive", "--domain", "d", "--dryRun"])).toBe(false);
    expect(isWrite(["ingest", "supersede", "--dryRun"])).toBe(false);
    expect(isWrite(["query", "document-history"])).toBe(false);
  });
});

describe("the older-note picker", () => {
  it("filters, and returns the name with scope and date", () => {
    const root = document.createElement("div");
    let got: Older | null | undefined;
    renderOlderPicker(root, ["old-note.md", "other.md"], true, (c) => (got = c));
    const filter = root.querySelector("input[type=text]") as HTMLInputElement;
    filter.value = "oth";
    filter.dispatchEvent(new Event("input"));
    expect([...root.querySelectorAll(".artmind-synth-domains button")].map((b) => b.textContent)).toEqual(["other.md"]);
    (root.querySelector("input[type=date]") as HTMLInputElement).value = "2026-03-04";
    (root.querySelector(".artmind-synth-domains button") as HTMLButtonElement).click();
    expect(got).toEqual({ older: "other.md", scope: "document", effective: "2026-03-04" });
  });
});
