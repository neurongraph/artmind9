import { describe, expect, it } from "vitest";
import { errorText } from "../src/cli";
import { EMPTY_INPUTS, classifyRefusal } from "../src/state";
import { fixture } from "./fixtures";

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
});

describe("StateInputs", () => {
  it("starts empty: nothing read, nothing running, nothing remembered", () => {
    expect(EMPTY_INPUTS).toEqual({
      status: null,
      activeJob: null,
      pending: null,
      tablesPending: null,
      git: { ahead: null, artmindChanges: [] },
      problem: null,
      projection: null,
      tablesReview: null,
      synthesis: null,
      lastJob: null,
      lastApply: null,
      lastRebuild: null,
      busy: { apply: false, rebuild: false, synthesize: false },
      actionErrors: {},
      obsidianGit: null,
    });
  });
});
