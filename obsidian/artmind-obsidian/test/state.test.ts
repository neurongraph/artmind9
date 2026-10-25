import { describe, expect, it } from "vitest";
import { errorText } from "../src/cli";
import { BUSY_KEYS, EMPTY_INPUTS, classifyRefusal, failureTag } from "../src/state";
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
      curate: null,
      lastJob: null,
      lastApply: null,
      lastRebuild: null,
      busy: Object.fromEntries(BUSY_KEYS.map((key) => [key, false])),
      writeQueue: { running: null, waiting: [] },
      synthRun: null,
      actionErrors: {},
      obsidianGit: null,
    });
  });
});

describe("failureTag", () => {
  it("names the failed chunks when the job reports them", () => {
    expect(failureTag({ error_message: "KG extraction incomplete: ...", chunk_progress: { failed_chunks: [3, 7] } })).toBe("chunks 3, 7 failed");
    expect(failureTag({ chunk_progress: { failed_chunks: [2] } })).toBe("chunk 2 failed");
  });

  it("falls back to the message's first sentence, cut to fit", () => {
    expect(failureTag({ error_message: "Domain schema not found: x_schema.yaml" })).toBe("Domain schema not found: x_schema.yaml");
    const long = `KG extraction incomplete ${"x".repeat(100)}`;
    const tag = failureTag({ error_message: long });
    expect(tag.length).toBe(60);
    expect(tag.endsWith("…")).toBe(true);
    expect(failureTag({ error_message: null })).toBe("failed");
  });
});
