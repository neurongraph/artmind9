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

/** What a job still owes once its files are done: the deferred projection
 * rebuild and embed sweeps for the domains it touched (Plan 1, B2).
 * `pending` while the worker runs them; `failed` with the error otherwise. */
export interface JobFinalize {
  state: "none" | "pending" | "done" | "failed";
  domains: string[];
  error: string | null;
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
  /** Absent from an artmind older than Plan 1. */
  finalize?: JobFinalize;
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
  finalize?: JobFinalize;
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

export interface ProjectionDomainGaps {
  unembedded_entities: number;
  unprojected_keys: number;
  unembedded_chunks: number;
}

/** `artmind projection status --compact` (Plan 1, B3). `known` is false
 * before the first full rebuild; then the recorded hashes are absent. */
export interface ProjectionStatus {
  known: boolean;
  drift: boolean;
  last_rebuilt_at?: string | null;
  same_as_drift: boolean;
  schema_drift: boolean;
  recorded_same_as_hash?: string | null;
  current_same_as_hash: string;
  recorded_schema_hash?: string | null;
  current_schema_hash: string;
  unembedded_entities: number;
  unprojected_keys: number;
  unembedded_chunks: number;
  domains: Record<string, ProjectionDomainGaps>;
}

/** `artmind projection rebuild --sweep --compact` (Plan 1, B4).
 * `sweep_errors` are `"<domain>: <message>"`; empty when every sweep ran. */
export interface RebuildResult {
  rebuilt: number;
  deleted: number;
  absent: number;
  keys: number;
  batches: number;
  domains?: string[];
  recorded?: boolean;
  domains_swept: string[];
  embedded: number;
  chunks_embedded: number;
  sweep_errors: string[];
  finalize_resolved: string[];
}

/** `artmind projection synthesize --domain D --dry-run --compact`.
 * `counts.synthesize` is how many descriptions a real run would write. */
export interface SynthesizeDryRun {
  domain: string;
  command: string;
  model: string;
  dry_run: true;
  examined: number;
  counts: Record<string, number>;
  candidates?: Array<{ key: string; name: string }>;
}

/** `artmind projection synthesize --domain D [--limit N] --compact`. */
export interface SynthesizeResult {
  domain: string;
  command: string;
  model: string;
  dry_run: false;
  examined: number;
  synthesized: number;
  counts: Record<string, number>;
  results: unknown[];
}

/** One table of `artmind db review --compact`: what is still unconfirmed. */
export interface TableToReview {
  table: string;
  domain: string;
  grain: string | null;
  grain_confirmed: boolean;
  grain_status: string | null;
  bridge_status: string | null;
  mapping_status: string | null;
  mappings: Array<{ column: string; entity_class: string; confidence: number | null }>;
  bridge_columns: Array<{ column: string; confidence: number | null }>;
}

/** `artmind db review --compact`. An empty `tables` means nothing to review. */
export interface DbReview {
  query_type: string;
  command: string;
  pending_count: number;
  tables: TableToReview[];
}
