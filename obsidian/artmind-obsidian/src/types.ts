/** The `--compact` JSON the plugin reads, as the fixtures under
 * test/fixtures/ capture it (regenerated from the real CLI by
 * test/test_plugin_fixtures.py in the artmind repo). */

export type StoreName = "graph" | "structured";

export type StoreState = "current" | "behind" | "no_bookmark" | "not_ancestor" | "error" | "no_commits";

export interface StoreReport {
  bookmark: string | null;
  state: StoreState;
  docs?: number;
  tables?: Array<[string, string]>;
  curation?: number;
  same_as_groups?: number;
  detail?: string | null;
}

/** `artmind vault status --compact`. */
export interface VaultStatus {
  vault: string;
  manifest: string | null;
  graph: { uri: string; database: string };
  sync: {
    head: string | null;
    vault_id: string | null;
    operation_in_progress: string | null;
    unresolved_conflicts: string[];
    legacy_cursor: string | null;
    graph_error: string | null;
    stores: Partial<Record<StoreName, StoreReport>>;
    message: string | null;
  };
}

/** `artmind vault sync --compact`, on success. */
export interface SyncResult {
  replayed?: number;
  retracted?: number;
  regenerated_tables?: number;
  curated_tables?: number;
  curation?: Record<string, Record<string, number>>;
  same_as_groups?: number;
  graph_bookmark?: string;
  structured_bookmark?: string;
}

/** What a file's extraction staged; null until it has been extracted (and
 * for a skipped, failed, or no-op file, which never is). */
export interface KgCounts {
  entities?: number | null;
  relationships?: number | null;
}

/** Per-chunk extraction progress, present while a file is in `extract_kg`. */
export interface ChunkProgress {
  total_chunks: number;
  entities_done: number;
  properties_done: number;
  relationships_done: number;
}

export interface JobFile extends KgCounts {
  filename: string;
  status: string;
  current_step: string | null;
  error_message?: string | null;
  chunk_progress?: ChunkProgress;
}

/** `artmind ingest job-status <id> --compact` (and each `jobs-active` row). */
export interface JobStatus {
  job_id: string;
  status: "queued" | "processing" | "completed" | "failed" | string;
  file_count: number;
  processed_count: number;
  files: JobFile[];
  error_message?: string | null;
  /** `processing`, but no worker holds the lock: it died mid-job. */
  stalled?: boolean;
}

/** `artmind ingest retry-job <id> --compact`. */
export interface RetryResult {
  job_id: string;
  domain: string;
  retried: number;
  deregistered: number;
  files: string[];
  stalled: boolean;
  /** Whether the job went back to the queue (and a worker was started). */
  requeued: boolean;
}

/** `artmind ingest job-results <id> --compact`. */
export interface JobResults {
  job_id: string;
  status: string;
  file_count: number;
  files: Array<{ filename: string; status: string; error_message: string | null } & KgCounts>;
}

/** `artmind ingest async [--pending] --compact`. */
export interface Submitted {
  job_id: string | null;
  file_count: number;
}

export interface PendingEntry {
  path: string;
  reason: "new" | "changed";
}

/** `artmind ingest pending --compact`. */
export interface IngestPending {
  notes: PendingEntry[];
  binaries: PendingEntry[];
  tables: PendingEntry[];
  errors: Array<{ path: string; error: string }>;
}

/** One row of `artmind ingest table2graph --pending --compact`. */
export interface TablePending {
  table: string;
  domain: string;
  mapping: string | null;
  reason: "missing" | "table_refreshed" | "mapping_changed" | "ambiguous" | string;
}

/** One table of `artmind ingest table2graph <t> --dryRun --compact`. */
export interface TableReport {
  table: string;
  domain: string;
  mapping: string | null;
  rows: number;
  entities: Record<string, { class: string; built: number; kept: number; skipped: Record<string, number> }>;
  lookups: Record<
    string,
    {
      resolved: number;
      stubbed: number;
      skipped_unresolved?: number;
      skipped_ambiguous?: number;
      empty?: number;
      unresolved_values: string[];
      ambiguous_values: string[];
    }
  >;
  relationships?: Record<string, { written: number; skipped: Record<string, number> }>;
  warnings: string[];
  dry_run?: boolean;
}

/** `artmind vault resolve [--dryRun] --compact`. */
export interface ResolveReport {
  head: string;
  merge_head: string;
  resolved: Array<{ unit: string; kind: string; paths: string[]; side: "ours" | "theirs"; rule: string }>;
  pending: Array<{ unit: string; kind: string; paths: string[]; why: string }>;
  reported: Array<{ path: string; why: string }>;
  remaining: number;
  dry_run?: boolean;
}

/** `artmind vault doctor --compact` (exit 1 when a check fails, JSON either way). */
export interface DoctorReport {
  vault: string;
  ok: boolean;
  checks: Array<{ name: string; status: "ok" | "warn" | "fail" | "unknown"; detail: string; fix: string | null }>;
}
