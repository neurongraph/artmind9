import type {
  DbReview,
  IngestPending,
  JobResults,
  JobStatus,
  ProjectionStatus,
  StoreReport,
  TablePending,
  VaultStatus,
} from "./types";

/** Why artmind itself cannot be used (spec §8). */
export type ArtmindProblem =
  | { kind: "missing"; looked: string[] }
  | { kind: "too_old"; found: string; need: string }
  | { kind: "failed"; command: string; message: string };

/** The checklist's rows (checklist spec §3.2), top to bottom; `vault` is the footer. */
export const ROW_IDS = ["remote", "documents", "tables", "graph", "descriptions", "vault"] as const;
export type RowId = (typeof ROW_IDS)[number];

/** Where a notice or the status bar opens the panel: a row, or a callout. */
export type PanelTarget = RowId | "problems" | "doctor" | "activity";

/** The last ingest job that finished, saved with `saveData` so its card
 * and its failures survive a reload. `results` is null when
 * `job-results` failed. */
export interface LastJob {
  status: JobStatus;
  results: JobResults | null;
  at: number;
}

/** How a write action ended. `summary` is the notice text on success, the
 * raw error on failure (shown only in its row's detail). */
export interface Outcome {
  at: number;
  ok: boolean;
  summary: string;
}

/** A rebuild's outcome, with the embed sweeps that could not run. */
export interface RebuildOutcome extends Outcome {
  errors: string[];
}

/** One mapped domain's `projection synthesize --dry-run`. */
export interface DomainSynthesis {
  domain: string;
  count: number;
  model: string;
}

/** A plugin write still running: its row shows ◌, its button is blocked. */
export interface Busy {
  apply: boolean;
  rebuild: boolean;
  synthesize: boolean;
}

/** Everything `checklist` decides from. */
export interface StateInputs {
  /** `vault status --compact`, or null before the first read. */
  status: VaultStatus | null;
  /** The ingest job being tracked, while it is queued, processing or finalizing. */
  activeJob: JobStatus | null;
  /** `ingest pending --compact`. */
  pending: IngestPending | null;
  /** `ingest table2graph --pending --compact`. */
  tablesPending: TablePending[] | null;
  /** Read-only git facts: commits not pushed (null: no upstream) and the
   * uncommitted paths under `.artmind/`. */
  git: { ahead: number | null; artmindChanges: string[] };
  problem: ArtmindProblem | null;
  /** `projection status --compact`. */
  projection: ProjectionStatus | null;
  /** `db review --compact`. */
  tablesReview: DbReview | null;
  /** Per mapped domain, or null when not counted yet. */
  synthesis: DomainSynthesis[] | null;
  lastJob: LastJob | null;
  lastApply: Outcome | null;
  lastRebuild: RebuildOutcome | null;
  busy: Busy;
  /** The raw error of each row's last failed action: its row's detail, never a notice. */
  actionErrors: Partial<Record<RowId, string>>;
  /** Obsidian Git's `autoPullInterval` (0: off), or null before the first read (D10). */
  obsidianGit: { autoPullMinutes: number } | null;
}

export const NOT_BUSY: Busy = { apply: false, rebuild: false, synthesize: false };

export const EMPTY_INPUTS: StateInputs = {
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
  busy: NOT_BUSY,
  actionErrors: {},
  obsidianGit: null,
};

export type Tone = "red" | "amber" | "blue" | "grey" | "spinner";

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

export function problemText(problem: ArtmindProblem): string {
  switch (problem.kind) {
    case "missing":
      return `artmind not found (looked in ${problem.looked.join(", ") || "nowhere"})`;
    case "too_old":
      return `artmind ${problem.found} is too old: this plugin needs ${problem.need} or newer`;
    case "failed":
      return `\`artmind ${problem.command}\` failed: ${problem.message}`;
  }
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
