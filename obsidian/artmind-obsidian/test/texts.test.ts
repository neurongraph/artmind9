import { describe, expect, it } from "vitest";
import {
  AUTO_PULL_HINT,
  COMMANDS,
  CONFIRM,
  LABELS,
  NOTICE,
  ROW_TITLES,
  applyDoneText,
  count,
  failedText,
  ingestDoneText,
  ingestLabel,
  newFilesText,
  projectedText,
  pulledText,
  rebuiltText,
  resolvedText,
  synthesizedText,
  syncDoneText,
} from "../src/texts";
import { fixture } from "./fixtures";

describe("notice texts (spec §3.3)", () => {
  it("a pull that left the stores behind", () => {
    expect(pulledText(fixture("vault-status.behind").json)).toBe("Pulled from the other laptop — 2 docs · 1 table behind.");
  });

  it("new files, named by their folder", () => {
    expect(newFilesText(["Team_performance_management/a.pdf", "Team_performance_management/b.xlsx"])).toBe(
      "2 new files in Team_performance_management.",
    );
    expect(newFilesText(["A/x.pdf", "B/y.pdf"])).toBe("2 new files in 2 folders.");
    expect(newFilesText(["deep/er/x.csv"])).toBe("1 new file in er.");
  });

  it("an ingest job that finished", () => {
    expect(ingestDoneText(fixture("ingest-job-status.done").json)).toBe("Ingested 4, 1 failed.");
    const allGood = { ...fixture("ingest-job-status.done").json, files: [{ filename: "a", status: "completed", current_step: null }] };
    expect(ingestDoneText(allGood)).toBe("Ingested 1.");
  });

  it("a sync that finished", () => {
    expect(syncDoneText(fixture("vault-sync.success").json)).toBe("Synced: 2 docs, 1 table, 0 curation records.");
    expect(syncDoneText({ replayed: 3, regenerated_tables: 1, curation: { conflict: { apply: 2 } } })).toBe(
      "Synced: 3 docs, 1 table, 2 curation records.",
    );
  });

  it("a table projected", () => {
    expect(projectedText(fixture("table2graph.dry-run").json.tables[0])).toBe("team projected: 7 entities.");
  });

  it("resolve, with and without files left for the user", () => {
    expect(resolvedText(0)).toBe("Artmind files resolved.");
    expect(resolvedText(2)).toBe(
      'Artmind files resolved. Resolve the 2 files listed under "Reported" in Obsidian, then complete the merge.',
    );
  });
});

describe("labels (checklist spec D6)", () => {
  it("never say sync", () => {
    const all = [...Object.values(LABELS), ...Object.values(COMMANDS), ...Object.values(ROW_TITLES), ...Object.values(CONFIRM), ingestLabel(3)];
    expect(all.filter((label) => /sync/i.test(label))).toEqual([]);
  });

  it("name the three git-facing actions as the spec does", () => {
    expect([LABELS.pull, LABELS.apply, LABELS.commitPush]).toEqual(["Pull", "Apply to graph", "Commit & push"]);
    expect(ingestLabel(1)).toBe("Ingest 1 file");
    expect(ingestLabel(7)).toBe("Ingest 7 files");
  });
});

describe("the checklist's notice texts (checklist spec §5)", () => {
  it("formats counts with thousands separators", () => {
    expect(count(4332)).toBe("4,332");
  });

  it("an apply, naming only what it applied", () => {
    expect(applyDoneText(fixture("vault-sync.success").json)).toBe("Applied to graph: 2 docs, 1 table.");
    expect(applyDoneText({ replayed: 3, regenerated_tables: 1, curation: { conflict: { apply: 2 } } })).toBe(
      "Applied to graph: 3 docs, 1 table, 2 curation records.",
    );
  });

  it("a rebuild: all embedded, some still not, or sweeps that failed", () => {
    const sweep = fixture("projection-rebuild.sweep").json;
    const ready = fixture("projection-status.ready").json;
    const gaps = fixture("projection-status.needs-rebuild").json;
    expect(rebuiltText({ ...sweep, rebuilt: 4332 }, ready)).toBe("Graph rebuilt: 4,332 entities, all embedded.");
    expect(rebuiltText({ ...sweep, rebuilt: 1 }, gaps)).toBe("Graph rebuilt: 1 entity, 2,813 entities still not embedded.");
    expect(rebuiltText({ ...sweep, rebuilt: 10, sweep_errors: ["general: refused", "habits: refused"] }, ready)).toBe(
      "Graph rebuilt: 10 entities, but 2 embed sweeps failed — details in the artmind panel.",
    );
    expect(rebuiltText({ ...sweep, rebuilt: 10 }, null)).toBe("Graph rebuilt: 10 entities.");
  });

  it("a synthesis", () => {
    expect(synthesizedText(41)).toBe("Synthesized 41 descriptions.");
    expect(synthesizedText(1)).toBe("Synthesized 1 description.");
  });

  it("a failure: one friendly line, never the raw error", () => {
    expect(failedText("apply to graph", "git diff failed: fatal: bad object 1234")).toBe(
      "Couldn't apply to graph — details in the artmind panel.",
    );
    expect(failedText("rebuild the graph", "graph unreachable: Couldn't connect to 127.0.0.1:7687")).toBe(
      "Couldn't rebuild the graph: Neo4j is unreachable — details in the artmind panel.",
    );
    expect(failedText("apply to graph", "timed out after 3600 s")).toBe("Couldn't apply to graph: it timed out — details in the artmind panel.");
    expect(failedText("rebuild the graph", "Neo.TransientError.General.MemoryPoolOutOfMemoryError")).toBe(
      "Couldn't rebuild the graph: Neo4j ran out of memory — details in the artmind panel.",
    );
    expect(failedText("start the ingest", null)).toBe("Couldn't start the ingest — details in the artmind panel.");
  });

  it("refusals and prompts", () => {
    expect(NOTICE.applyQueued).toBe("Ingest in progress — will apply to graph when it finishes.");
    expect(CONFIRM.applyFirst).toBe("The other laptop has changes not applied to your graph.");
    expect(CONFIRM.pullFirst).toBe("Pull from GitHub first?");
    expect(AUTO_PULL_HINT).toBe("Turn on Obsidian Git's auto pull to see the other laptop's changes");
  });
});
