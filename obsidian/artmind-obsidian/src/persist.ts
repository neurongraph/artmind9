import type { ArtmindSettings } from "./settings";
import type { LastJob, Outcome, RebuildOutcome } from "./state";
import type { JobResults, JobStatus } from "./types";

/** What the panel shows that no CLI read gives back after a reload
 * (checklist spec §4): the last job card, the last apply and rebuild. */
export interface Persisted {
  lastJob: LastJob | null;
  lastApply: Outcome | null;
  lastRebuild: RebuildOutcome | null;
}

export const NOTHING_PERSISTED: Persisted = { lastJob: null, lastApply: null, lastRebuild: null };

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function outcome(value: unknown): Outcome | null {
  const o = record(value);
  if (!o || typeof o.at !== "number" || typeof o.ok !== "boolean" || typeof o.summary !== "string") return null;
  return { at: o.at, ok: o.ok, summary: o.summary };
}

function rebuildOutcome(value: unknown): RebuildOutcome | null {
  const base = outcome(value);
  if (!base) return null;
  const errors = record(value)?.errors;
  return { ...base, errors: Array.isArray(errors) ? errors.filter((e): e is string => typeof e === "string") : [] };
}

function lastJob(value: unknown): LastJob | null {
  const o = record(value);
  const status = record(o?.status);
  const results = o?.results === null ? null : record(o?.results);
  if (!o || typeof o.at !== "number" || !status || typeof status.job_id !== "string" || !Array.isArray(status.files)) return null;
  if (o.results !== null && (!results || !Array.isArray(results.files))) return null;
  return { status: status as unknown as JobStatus, results: results as unknown as JobResults | null, at: o.at };
}

/** `state` from `data.json`; a missing or malformed field is null. */
export function readPersisted(data: unknown): Persisted {
  const state = record(record(data)?.state);
  if (!state) return NOTHING_PERSISTED;
  return { lastJob: lastJob(state.lastJob), lastApply: outcome(state.lastApply), lastRebuild: rebuildOutcome(state.lastRebuild) };
}

/** What `saveData` writes: the settings, and the persisted state under `state`
 * (`mergeSettings` ignores it). */
export function withPersisted(settings: ArtmindSettings, persisted: Persisted): Record<string, unknown> {
  return { ...settings, state: persisted };
}
