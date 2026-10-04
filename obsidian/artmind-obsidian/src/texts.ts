import { type RowId, behindParts, plural } from "./state";
import type { JobStatus, ProjectionStatus, RebuildResult, SyncResult, TableReport, VaultStatus } from "./types";
import type { Checklist } from "./checklist";

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

export function stalledText(job: JobStatus): string {
  return `Ingest stalled at ${job.processed_count} of ${plural(job.file_count, "file")}: the worker stopped.`;
}

export function retriedText(retried: number, stalled: boolean): string {
  if (stalled) return retried ? `Restarted the stalled job: retrying ${plural(retried, "file")}.` : "Restarted the stalled job.";
  return `Retrying ${plural(retried, "failed file")}.`;
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

// ── the readiness checklist (checklist spec D6, §3, §5) ─────────────────────

/** Every button and modal choice. "sync" is in none of them (D6). */
export const LABELS = {
  pull: "Pull",
  apply: "Apply to graph",
  resolve: "Resolve…",
  completeMerge: "Complete the merge",
  retryFailed: "Retry failed",
  retryJob: "Retry job",
  reviewInAdmin: "Review in admin console ↗",
  reviewAndProject: "Review & project…",
  askAdmin: "Ask admin-ui",
  rebuild: "Rebuild graph",
  synthesize: "Synthesize…",
  commitPush: "Commit & push",
  refresh: "↻",
  doctor: "Doctor",
  admin: "Admin ↗",
  applyFirst: "Apply first",
  ingestAnyway: "Ingest anyway",
  pullThenApply: "Pull, then apply",
  applyWithoutPulling: "Apply without pulling",
  synthesizeRun: "Synthesize",
  cancel: "Cancel",
} as const;

export function ingestLabel(files: number): string {
  return `Ingest ${plural(files, "file")}`;
}

/** The command palette's names (checklist spec §4), also the ribbon menu's. */
export const COMMANDS = {
  pull: "Pull",
  apply: "Apply to graph",
  ingest: "Ingest what changed",
  rebuild: "Rebuild graph",
  synthesize: "Synthesize…",
  commitPush: "Commit & push",
  reviewTables: "Review tables for graph",
  resolve: "Resolve artmind conflicts",
  doctor: "Run doctor",
  openPanel: "Open side panel",
  openAdmin: "Open admin console",
  stopAdmin: "Stop admin console",
} as const;

export const ROW_TITLES: Record<RowId, string> = {
  remote: "From the other laptop",
  documents: "Documents",
  tables: "Tables",
  graph: "Graph",
  descriptions: "Descriptions (optional)",
  vault: "Vault",
};

/** The confirm modal's questions (checklist spec §3.3). */
export const CONFIRM = {
  applyFirst: "The other laptop has changes not applied to your graph.",
  pullFirst: "Pull from GitHub first?",
} as const;

/** Notices whose words don't depend on a result. */
export const NOTICE = {
  applyQueued: "Ingest in progress — will apply to graph when it finishes.",
  resolveFirst: "Resolve the artmind conflicts first.",
  pullFirst: "Pull first: the other laptop's commits aren't in this clone.",
  noBookmark: "This laptop has never applied this vault — see the first step in the artmind panel.",
  nothingToIngest: "Nothing new or changed to ingest.",
  nothingToRetry: "Nothing to retry in that job.",
  neo4jIngest: "Neo4j is unreachable: extraction will run, the graph write will fail.",
} as const;

/** Row 1's extra line when Obsidian Git's auto pull is off (spec D10). */
export const AUTO_PULL_HINT = "Turn on Obsidian Git's auto pull to see the other laptop's changes";

/** `4,332`: counts in the checklist and its notices. */
export function count(n: number): string {
  return n.toLocaleString("en-US");
}

export function countOf(n: number, one: string, many: string): string {
  return `${count(n)} ${n === 1 ? one : many}`;
}

/** An action that failed: one friendly line, never the raw error (which
 * goes to the row's detail and the Activity list). */
export function failedText(what: string, error: string | null): string {
  const e = error ?? "";
  let why = "";
  if (/graph unreachable|ServiceUnavailable|Connection refused|Couldn't connect/i.test(e)) why = ": Neo4j is unreachable";
  else if (/^timed out/.test(e)) why = ": it timed out";
  else if (/MemoryLimitExceeded|OutOfMemory/i.test(e)) why = ": Neo4j ran out of memory";
  return `Couldn't ${what}${why} — details in the artmind panel.`;
}

export function applyDoneText(result: SyncResult): string {
  const curation = Object.values(result.curation ?? {}).reduce(
    (sum, counts) => sum + Object.values(counts).reduce((a, b) => a + b, 0),
    0,
  );
  const parts = [
    plural((result.replayed ?? 0) + (result.retracted ?? 0), "doc"),
    plural((result.regenerated_tables ?? 0) + (result.curated_tables ?? 0), "table"),
  ];
  if (curation) parts.push(plural(curation, "curation record"));
  return `Applied to graph: ${parts.join(", ")}.`;
}

/** After `projection rebuild --sweep`, with `projection status` read again. */
export function rebuiltText(result: RebuildResult, projection: ProjectionStatus | null): string {
  const entities = countOf(result.rebuilt, "entity", "entities");
  if (result.sweep_errors.length) {
    return `Graph rebuilt: ${entities}, but ${plural(result.sweep_errors.length, "embed sweep")} failed — details in the artmind panel.`;
  }
  if (!projection) return `Graph rebuilt: ${entities}.`;
  const left = projection.unembedded_entities;
  return `Graph rebuilt: ${entities}, ${left ? `${countOf(left, "entity", "entities")} still not embedded` : "all embedded"}.`;
}

export function synthesizedText(n: number): string {
  return `Synthesized ${countOf(n, "description", "descriptions")}.`;
}

/** The panel header (checklist spec §3.2). */
export function readinessText(cl: Checklist): string {
  return cl.ready ? "Graph ready ✓" : `${plural(cl.stepsLeft, "step")} to a ready graph`;
}

/** `Next: rebuild graph.` from the current row's main button, or "" (spec §5). */
export function nextStepText(cl: Checklist): string {
  const label = cl.rows.find((r) => r.id === cl.current)?.buttons[0]?.label;
  if (!label) return "";
  const bare = label.replace(/…$/, "").replace(/ ↗$/, "");
  return `Next: ${bare[0].toLowerCase()}${bare.slice(1)}.`;
}

export function withNext(text: string, cl: Checklist): string {
  const next = nextStepText(cl);
  return next ? `${text} ${next}` : text;
}

/** The status bar's label when no step is left. */
export const STATUS_READY = "graph ready";
