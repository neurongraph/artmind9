import { describe, expect, it } from "vitest";
import { NOTHING_PERSISTED, type Persisted, readPersisted, withPersisted } from "../src/persist";
import { DEFAULT_SETTINGS, mergeSettings } from "../src/settings";
import { fixture } from "./fixtures";

const SAVED: Persisted = {
  lastJob: { status: fixture("ingest-job-status.done").json, results: fixture("ingest-job-results.done").json, at: 1_000 },
  lastApply: { at: 2_000, ok: true, summary: "Applied to graph: 2 docs, 1 table." },
  lastRebuild: { at: 3_000, ok: true, summary: "Graph rebuilt: 3 entities, all embedded.", errors: [] },
};

describe("persisted state (checklist spec §4)", () => {
  it("round-trips through data.json, beside the settings", () => {
    const data = JSON.parse(JSON.stringify(withPersisted(DEFAULT_SETTINGS, SAVED)));

    expect(readPersisted(data)).toEqual(SAVED);
    expect(mergeSettings(data)).toEqual(DEFAULT_SETTINGS);
  });

  it("is empty when nothing was saved", () => {
    expect(readPersisted(null)).toEqual(NOTHING_PERSISTED);
    expect(readPersisted({ artmindPath: "" })).toEqual(NOTHING_PERSISTED);
  });

  it("drops a malformed field, keeping the rest", () => {
    const data = {
      state: {
        lastJob: { status: { job_id: 7 }, results: null, at: 1 },
        lastApply: { at: "noon", ok: true, summary: "x" },
        lastRebuild: { at: 3, ok: false, summary: "boom" },
      },
    };
    expect(readPersisted(data)).toEqual({ lastJob: null, lastApply: null, lastRebuild: { at: 3, ok: false, summary: "boom", errors: [] } });
  });
});
