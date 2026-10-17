import { type RowId, behindParts, plural } from "./state";
import type { JobStatus, ProjectionStatus, RebuildResult, SyncResult, TableReport, VaultStatus } from "./types";
import type { Checklist } from "./checklist";

/** The words of every notice (spec §3.3), from the JSON the CLI printed. */

export function pulledText(status: VaultStatus): string {
  const parts = behindParts(status.sync.stores);
  return `Pulled: ${parts.join(", ") || "changes"} to apply to graph.`;
}

export function newFilesText(paths: string[]): string {
  const folders = [...new Set(paths.map((p) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "the vault root")))];
  const where = folders.length === 1 ? folders[0].split("/").pop()! : plural(folders.length, "folder");
  return `${plural(paths.length, "new file")} in ${where}, ready to ingest.`;
}

export function ingestDoneText(job: JobStatus): string {
  const failed = job.files.filter((f) => f.status === "failed").length;
  const done = job.files.filter((f) => f.status === "completed").length;
  const base = `Ingested ${plural(done, "file")}${failed ? `, ${failed} failed` : ""}`;
  // A failed finalize is an error notice (spec §5): one line, then the panel.
  return job.finalize?.state === "failed" ? `${base}, but building the graph failed — details in the artmind panel.` : `${base}.`;
}

export function stalledText(job: JobStatus): string {
  return `Ingest stalled at ${job.processed_count} of ${plural(job.file_count, "file")} — details in the artmind panel.`;
}

export function retriedText(retried: number, stalled: boolean): string {
  if (stalled) return retried ? `Restarted the stalled job: retrying ${plural(retried, "file")}.` : "Restarted the stalled job.";
  return `Retrying ${plural(retried, "failed file")}.`;
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
  reviewSameAs: "Review in admin console ↗",
  adjudicate: "Adjudicate in admin console ↗",
  findDuplicates: "Find duplicates…",
  detectConflicts: "Detect conflicts…",
  findDuplicatesRun: "Find duplicates",
  detectConflictsRun: "Detect conflicts",
  cancel: "Cancel",
} as const;

/** A button's label from the click until its action ends (it is disabled,
 * with a spinner). Actions that only open a modal or the admin console have none. */
export const PENDING_LABELS = {
  pull: "Pulling…",
  apply: "Applying…",
  resolve: "Resolving…",
  completeMerge: "Completing…",
  ingest: "Starting ingest…",
  retry: "Retrying…",
  rebuild: "Rebuilding…",
  synthesize: "Synthesizing…",
  findDuplicates: "Finding duplicates…",
  detectConflicts: "Detecting conflicts…",
  commitPush: "Committing…",
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
  findDuplicates: "Find duplicate entities…",
  detectConflicts: "Detect conflicts…",
  commitPush: "Commit & push",
  reviewTables: "Review tables for graph",
  resolve: "Resolve artmind conflicts",
  doctor: "Run doctor",
  openPanel: "Open side panel",
  openAdmin: "Open admin console",
  stopAdmin: "Stop admin console",
} as const;

export const ROW_TITLES: Record<RowId, string> = {
  remote: "Commits to apply",
  documents: "Documents",
  tables: "Tables",
  graph: "Graph",
  curate: "Curate (optional)",
  descriptions: "Descriptions (optional)",
  vault: "Share",
};

/** The confirm modal's questions (checklist spec §3.3). */
export const CONFIRM = {
  applyFirst: "Some committed changes are not applied to your graph yet.",
  pullFirst: "Pull from GitHub first?",
} as const;

/** Notices whose words don't depend on a result. */
export const NOTICE = {
  applyQueued: "Ingest in progress — will apply to graph when it finishes.",
  resolveFirst: "Resolve the artmind conflicts first.",
  pullFirst: "Pull first: some commits recorded for this vault aren't in this clone.",
  noBookmark: "This laptop has never applied this vault — see the first step in the artmind panel.",
  nothingToIngest: "Nothing new or changed to ingest.",
  nothingToRetry: "Nothing to retry in that job.",
  neo4jIngest: "Neo4j is unreachable: extraction will run, the graph write will fail.",
} as const;

/** Row 1's extra line when Obsidian Git's auto pull is off (spec D10). */
export const AUTO_PULL_HINT = "Turn on Obsidian Git's auto pull to see commits pushed from another laptop";

/** The admin-console prompts of the Curate row (the judgment steps). */
export function sameAsPrompt(open: number): string {
  return `Review the ${plural(open, "open same-as proposal")}: show each with its evidence and help me approve or reject it.`;
}

export function conflictsPrompt(open: number): string {
  return `Adjudicate the ${plural(open, "open conflict")}: show each pair of claims with its evidence and help me resolve or dismiss it.`;
}

export function curateSummary(sameAs: number, conflicts: number): string {
  const parts = [
    sameAs ? `${plural(sameAs, "same-as proposal")} to review` : null,
    conflicts ? `${plural(conflicts, "open conflict")}` : null,
  ].filter((part): part is string => part !== null);
  return parts.join(" · ") || "nothing to review";
}

export function proposedText(report: { candidates: number; same_as_proposals: string[] }): string {
  return `Compared ${plural(report.candidates, "pair")}: ${plural(report.same_as_proposals.length, "same-as proposal")} to review.`;
}

export function detectedText(report: { candidates: number; conflicts: string[]; same_as_proposals: string[] }): string {
  return `Compared ${plural(report.candidates, "pair")}: ${plural(report.conflicts.length, "conflict")}, ${plural(report.same_as_proposals.length, "same-as proposal")}.`;
}

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

/** `3 of 6 steps done`: the header's progress line, over the numbered steps. */
export function progressText(cl: Checklist): string {
  const steps = cl.rows.filter((r) => r.id !== "vault");
  return `${steps.filter((r) => r.state === "done").length} of ${steps.length} steps done`;
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
