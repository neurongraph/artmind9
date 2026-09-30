import type { IngestPending, JobStatus, StoreReport, TablePending, VaultStatus } from "./types";

/** Why artmind itself cannot be used (spec §8). */
export type ArtmindProblem =
  | { kind: "missing"; looked: string[] }
  | { kind: "too_old"; found: string; need: string }
  | { kind: "failed"; command: string; message: string };

/** Everything `vaultState` decides from. All of it is read, never written. */
export interface StateInputs {
  /** `vault status --compact`, or null before the first read. */
  status: VaultStatus | null;
  /** The ingest job being tracked, while it is queued or processing. */
  activeJob: JobStatus | null;
  /** `ingest pending --compact`. */
  pending: IngestPending | null;
  /** `ingest table2graph --pending --compact`. */
  tablesPending: TablePending[] | null;
  /** Read-only git facts. `unsharedArtmindChanges` already excludes what
   * the plugin itself wrote in the last 30 s (spec §4.5). */
  git: { ahead: number | null; unsharedArtmindChanges: number };
  problem: ArtmindProblem | null;
}

export type StateKind = "conflict" | "error" | "job" | "behind" | "tables" | "ingest" | "in_sync";
export type Tone = "red" | "amber" | "blue" | "grey" | "spinner";
export type PanelSection = "status" | "error" | "job" | "tables" | "doctor" | "bootstrap";

export type Action =
  | { type: "openResolve" }
  | { type: "openPanel"; section: PanelSection }
  | { type: "sync" }
  | { type: "pull" }
  | { type: "reviewTables" }
  | { type: "ingest" };

export interface StateItem {
  kind: StateKind;
  label: string;
  tone: Tone;
  action: Action;
  /** One line for the panel. */
  detail: string;
}

export type ActionName = "sync" | "ingest" | "resolve" | "doctor" | "tables";

export interface VaultStateResult {
  /** Every state that applies, highest priority first (spec §3.1). */
  states: StateItem[];
  /** What the status bar shows. */
  primary: StateItem;
  /** `↑ 3 to push`, `✎ artmind changes to share`. */
  secondary: string[];
  /** Why each action cannot run right now, or null when it can (spec §3.2). */
  blocked: Record<ActionName, string | null>;
  /** Shown beside Ingest when it may run but its graph write will fail. */
  warnings: Partial<Record<ActionName, string>>;
  neo4jUnreachable: boolean;
}

const PRIORITY: StateKind[] = ["conflict", "error", "job", "behind", "tables", "ingest", "in_sync"];

export const IN_SYNC: StateItem = {
  kind: "in_sync",
  label: "◉ artmind",
  tone: "grey",
  action: { type: "openPanel", section: "status" },
  detail: "Every store is at HEAD and nothing is waiting.",
};

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** `3 docs · 1 table · 2 curation records`: what the stores still have to
 * apply. Tables either store still has to apply count once. */
export function behindParts(stores: Partial<Record<"graph" | "structured", StoreReport>>): string[] {
  const graph = stores.graph;
  const structured = stores.structured;
  const tables = new Set<string>();
  for (const report of [graph, structured]) {
    for (const [domain, table] of report?.tables ?? []) tables.add(`${domain}/${table}`);
  }
  const parts: string[] = [];
  if (graph?.docs) parts.push(plural(graph.docs, "doc"));
  if (tables.size) parts.push(plural(tables.size, "table"));
  if (graph?.curation) parts.push(plural(graph.curation, "curation record"));
  if (graph?.same_as_groups) parts.push(plural(graph.same_as_groups, "same-as group"));
  return parts;
}

function problemText(problem: ArtmindProblem): string {
  switch (problem.kind) {
    case "missing":
      return `artmind not found (looked in ${problem.looked.join(", ") || "nowhere"})`;
    case "too_old":
      return `artmind ${problem.found} is too old: this plugin needs ${problem.need} or newer`;
    case "failed":
      return `\`artmind ${problem.command}\` failed: ${problem.message}`;
  }
}

/** The whole of the plugin's judgement, as a pure function: which states
 * apply (spec §3.1, highest priority first), which one the status bar
 * shows, what each action is blocked by (§3.2, §4.2) and the secondary
 * indicators. The views render this; they decide nothing. */
export function vaultState(inputs: StateInputs): VaultStateResult {
  const states: StateItem[] = [];
  const sync = inputs.status?.sync;
  const stores = sync?.stores ?? {};
  const graphError = sync?.graph_error ?? null;
  const neo4jUnreachable = Boolean(graphError && graphError.startsWith("graph unreachable"));
  const merge = sync?.operation_in_progress ?? null;
  const conflicts = sync?.unresolved_conflicts ?? [];
  const jobRunning = Boolean(inputs.activeJob && ["queued", "processing"].includes(inputs.activeJob.status));
  const storeStates = Object.values(stores).map((report) => report?.state);

  if (conflicts.length) {
    states.push({
      kind: "conflict",
      label: "⚠ resolve artmind conflicts",
      tone: "red",
      action: { type: "openResolve" },
      detail: `${plural(conflicts.length, "file")} under .artmind/ ${conflicts.length === 1 ? "has" : "have"} unresolved conflicts.`,
    });
  }

  const errors: string[] = [];
  if (inputs.problem) errors.push(problemText(inputs.problem));
  if (neo4jUnreachable) errors.push(`Neo4j unreachable (${inputs.status?.graph.uri || "no URI configured"})`);
  else if (graphError) errors.push(graphError);
  for (const [name, report] of Object.entries(stores)) {
    if (report?.state === "error") errors.push(`the ${name} store's sync would fail: ${report.detail ?? "unknown error"}`);
  }
  if (errors.length) {
    states.push({
      kind: "error",
      label: "⚠ artmind",
      tone: "red",
      action: { type: "openPanel", section: "error" },
      detail: errors.join("; "),
    });
  }

  if (jobRunning && inputs.activeJob) {
    const job = inputs.activeJob;
    states.push({
      kind: "job",
      label: `◌ ingesting ${job.processed_count}/${job.file_count}`,
      tone: "spinner",
      action: { type: "openPanel", section: "job" },
      detail: `Ingest job ${job.job_id}: ${job.processed_count} of ${plural(job.file_count, "file")} done.`,
    });
  }

  if (storeStates.includes("not_ancestor")) {
    states.push({
      kind: "behind",
      label: "◉ pull first",
      tone: "amber",
      action: { type: "pull" },
      detail: "A store's sync bookmark is ahead of this clone: pull from GitHub, then sync.",
    });
  } else if (storeStates.includes("no_bookmark")) {
    states.push({
      kind: "behind",
      label: "◉ sync not set up",
      tone: "amber",
      action: { type: "openPanel", section: "bootstrap" },
      detail: "This laptop hasn't synced this vault before.",
    });
  } else if (storeStates.includes("behind")) {
    const parts = behindParts(stores);
    states.push({
      kind: "behind",
      label: `◉ ${parts.join(" · ") || "stores"} behind`,
      tone: "amber",
      action: { type: "sync" },
      detail: `The other laptop's changes are not applied here yet: ${parts.join(", ")}.`,
    });
  }

  const tables = (inputs.tablesPending ?? []).filter((t) => t.reason !== "ambiguous");
  if (tables.length) {
    states.push({
      kind: "tables",
      label: `◉ ${plural(tables.length, "table")} → graph`,
      tone: "blue",
      action: { type: "reviewTables" },
      detail: tables.map((t) => `${t.table} (${t.reason.replace("_", " ")})`).join(", "),
    });
  }

  const pending = inputs.pending;
  const toIngest = pending ? pending.notes.length + pending.binaries.length + pending.tables.length : 0;
  if (toIngest) {
    states.push({
      kind: "ingest",
      label: `◉ ${toIngest} to ingest`,
      tone: "blue",
      action: { type: "ingest" },
      detail: `${plural(pending!.notes.length, "note")}, ${plural(pending!.binaries.length, "binary", "binaries")}, ${plural(pending!.tables.length, "table")} new or changed.`,
    });
  }

  states.sort((a, b) => PRIORITY.indexOf(a.kind) - PRIORITY.indexOf(b.kind));
  const primary = states[0] ?? IN_SYNC;

  const secondary: string[] = [];
  if (inputs.git.ahead) secondary.push(`↑ ${inputs.git.ahead} to push`);
  if (inputs.git.unsharedArtmindChanges) secondary.push("✎ artmind changes to share");

  const cannotRun = inputs.problem ? problemText(inputs.problem) : null;
  const blocked: Record<ActionName, string | null> = {
    sync:
      cannotRun ??
      (jobRunning ? "An ingest job is running — sync after it finishes" : null) ??
      (merge ? `${merge[0].toUpperCase()}${merge.slice(1)} is in progress — finish it first` : null) ??
      (conflicts.length ? "Resolve the artmind conflicts first" : null) ??
      (neo4jUnreachable ? "Neo4j unreachable" : null) ??
      (storeStates.includes("no_bookmark") ? "This laptop hasn't synced this vault before" : null),
    ingest:
      cannotRun ??
      (jobRunning ? "An ingest job is already running" : null) ??
      (merge ? `${merge[0].toUpperCase()}${merge.slice(1)} is in progress — finish it first` : null) ??
      (toIngest ? null : "Nothing new or changed to ingest"),
    resolve: cannotRun ?? (merge === "a merge" ? null : "No merge in progress"),
    doctor: cannotRun,
    tables: cannotRun ?? (neo4jUnreachable ? "Neo4j unreachable" : null) ?? (tables.length ? null : "No tables waiting"),
  };
  const warnings: Partial<Record<ActionName, string>> = {};
  if (neo4jUnreachable) warnings.ingest = "Neo4j is unreachable: extraction will run, the graph write will fail";

  return { states, primary, secondary, blocked, warnings, neo4jUnreachable };
}

/** What a `vault sync` refusal becomes (spec §4.2): never a raw error. */
export type Refusal =
  | { kind: "worker_running"; text: string }
  | { kind: "resolve_first"; text: string }
  | { kind: "pull_first"; text: string }
  | { kind: "no_bookmark"; text: string }
  | { kind: "other"; text: string };

export const BOOTSTRAP_STEP =
  "Run once in a terminal, in the vault: artmind vault sync --bootstrapEmpty " +
  "(an empty Neo4j and DuckDB) — see docs/USER_GUIDE_TWO_LAPTOPS.md §2.3.";

/** Classify `vault sync`'s error message (`errorText` of its stderr). */
export function classifyRefusal(message: string): Refusal {
  const text = message.replace(/\s+/g, " ");
  if (/ingest worker is running/.test(text)) {
    return { kind: "worker_running", text: "Ingest in progress — will sync when it finishes" };
  }
  if (/is in progress in this vault|unresolved conflicts in/.test(text)) {
    return { kind: "resolve_first", text: "Resolve first" };
  }
  if (/is not a commit in this clone|is not an ancestor of HEAD/.test(text)) {
    return { kind: "pull_first", text: "Pull first" };
  }
  if (/bookmark recorded yet/.test(text)) {
    return { kind: "no_bookmark", text: `This laptop hasn't synced this vault before. ${BOOTSTRAP_STEP}` };
  }
  return { kind: "other", text: message };
}
