import { describe, expect, it } from "vitest";
import { ingestDoneText, newFilesText, projectedText, pulledText, resolvedText, syncDoneText } from "../src/texts";
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
