import { behindParts, plural } from "./state";
import type { JobStatus, SyncResult, TableReport, VaultStatus } from "./types";

/** The words of every notice (spec §3.3), from the JSON the CLI printed. */

export function pulledText(status: VaultStatus): string {
  const parts = behindParts(status.sync.stores);
  return `Pulled from the other laptop — ${parts.join(" · ") || "stores"} behind.`;
}

export function newFilesText(paths: string[]): string {
  const folders = [...new Set(paths.map((p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "the vault root")))];
  const where = folders.length === 1 ? folders[0].split("/").pop()! : plural(folders.length, "folder");
  return `${plural(paths.length, "new file")} in ${where}.`;
}

export function ingestDoneText(job: JobStatus): string {
  const failed = job.files.filter((f) => f.status === "failed").length;
  const done = job.files.filter((f) => f.status === "completed").length;
  return failed ? `Ingested ${done}, ${failed} failed.` : `Ingested ${done}.`;
}

export function syncDoneText(result: SyncResult): string {
  const curation = Object.values(result.curation ?? {}).reduce(
    (sum, counts) => sum + Object.values(counts).reduce((a, b) => a + b, 0),
    0,
  );
  const parts = [
    plural((result.replayed ?? 0) + (result.retracted ?? 0), "doc"),
    plural((result.regenerated_tables ?? 0) + (result.curated_tables ?? 0), "table"),
    plural(curation, "curation record"),
  ];
  return `Synced: ${parts.join(", ")}.`;
}

export function projectedText(report: TableReport): string {
  const entities = Object.values(report.entities).reduce((sum, e) => sum + e.kept, 0);
  return `${report.table} projected: ${plural(entities, "entity", "entities")}.`;
}

export const RESOLVED_TEXT = "Artmind files resolved.";

export function resolvedText(reportedLeft: number): string {
  return reportedLeft
    ? `${RESOLVED_TEXT} Resolve the ${plural(reportedLeft, "file")} listed under "Reported" in Obsidian, then complete the merge.`
    : RESOLVED_TEXT;
}
