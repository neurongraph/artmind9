import { BOOTSTRAP_STEP, type RowId, type StateInputs, type Tone, behindParts, plural, problemText } from "./state";
import { AUTO_PULL_HINT, LABELS, NOTICE, ROW_TITLES, count, countOf, ingestLabel } from "./texts";
import type { JobStatus, TablePending, TableToReview } from "./types";

export type { RowId } from "./state";

/** `done` ✓ · `todo` ● · `running` ◌ · `attention` ! ✗ ⚠ · `optional` ◇ ·
 * `waiting` ○ (on a row above, or not read yet) · `hidden` (the footer during a job). */
export type RowState = "done" | "todo" | "running" | "attention" | "optional" | "waiting" | "hidden";
export type Glyph = "✓" | "●" | "◌" | "!" | "✗" | "⚠" | "◇" | "○";

/** What a row's button does. The controller's `run` performs it. */
export type RowAction =
  | { type: "pull" }
  | { type: "apply" }
  | { type: "resolve" }
  | { type: "completeMerge" }
  | { type: "ingest" }
  | { type: "retryJob"; jobId: string }
  | { type: "adminPrompt"; prompt: string }
  | { type: "reviewTable"; table: string }
  | { type: "rebuild" }
  | { type: "synthesize" }
  | { type: "commitPush" };

export interface RowButton {
  action: RowAction;
  label: string;
  /** Why it can't run now (the button is disabled with this as its title), or null. */
  blockedReason: string | null;
}

/** One expandable line: a path, a table, a failed file. */
export interface RowItem {
  text: string;
  tag: string | null;
  title: string | null;
}

export interface Row {
  id: RowId;
  state: RowState;
  glyph: Glyph;
  tone: Tone;
  title: string;
  /** The one line beside the title; also the status bar's label. */
  summary: string;
  /** Lines under it: reasons, warnings, the last error. */
  detail: string[];
  items: RowItem[];
  /** The first is the row's main action: filled on the current row only. */
  buttons: RowButton[];
  optional: boolean;
}

/** Every action the palette offers, with why it is blocked. */
export type CommandId = "pull" | "apply" | "ingest" | "rebuild" | "synthesize" | "commitPush" | "tables" | "resolve" | "doctor" | "admin";

export interface Checklist {
  /** Rows 1–5, then the footer (`vault`). */
  rows: Row[];
  /** The first of rows 1–4 not done; the footer once they all are and it has work; else null. */
  current: RowId | null;
  /** Rows 1–4 all done. */
  ready: boolean;
  stepsLeft: number;
  blocked: Record<CommandId, string | null>;
  neo4jUnreachable: boolean;
  jobRunning: boolean;
}

/** The rows that decide `ready` (D4, D5: not Descriptions, not the footer). */
export const STEP_IDS: RowId[] = ["remote", "documents", "tables", "graph"];

/** Queued, processing, or still owing its deferred rebuild; never stalled. */
export function jobRunning(job: JobStatus | null): boolean {
  if (!job || job.stalled) return false;
  return ["queued", "processing"].includes(job.status) || job.finalize?.state === "pending";
}

/** Every file done and the deferred rebuild running. */
export function jobFinishing(job: JobStatus): boolean {
  return job.finalize?.state === "pending" && (job.processed_count >= job.file_count || !["queued", "processing"].includes(job.status));
}

/** What uncommitted `.artmind/` paths came from: docs an ingest wrote
 * (`data/kg/<domain>/<doc>/`, once per doc), syntheses, anything else. */
export function changeSources(paths: string[]): { ingest: number; syntheses: number; other: number } {
  const docs = new Set<string>();
  let syntheses = 0;
  let other = 0;
  for (const path of paths) {
    const parts = path.split("/");
    if (parts[0] === ".artmind" && parts[1] === "data" && parts[2] === "kg" && parts.length >= 6) docs.add(`${parts[3]}/${parts[4]}`);
    else if (parts[0] === ".artmind" && parts[1] === "data" && parts[2] === "curation" && parts[3] === "syntheses") syntheses++;
    else other++;
  }
  return { ingest: docs.size, syntheses, other };
}

/** The admin console prompt for a table's unconfirmed classifications (B7). */
export function reviewPrompt(table: TableToReview): string {
  return `Review the proposed classifications for table ${table.table} (domain ${table.domain})`;
}

export function ambiguousPrompt(table: TablePending): string {
  return `Table ${table.table} (domain ${table.domain}) matches more than one table mapping (${table.mapping}). Help me narrow one so table2graph can project it.`;
}

function unconfirmed(table: TableToReview): string {
  const parts: string[] = [];
  if (!table.grain_confirmed) parts.push("grain");
  if (table.mappings.length) parts.push(plural(table.mappings.length, "column mapping"));
  if (table.bridge_columns.length) parts.push(plural(table.bridge_columns.length, "bridge column"));
  return `${parts.join(", ") || "classification"} unconfirmed`;
}

/** `10:02`, local time. */
function clock(at: number): string {
  const d = new Date(at);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Why row 4 needs a rebuild, one line each (checklist spec §3.2, decision 9). */
export function rebuildReasons(inputs: StateInputs): string[] {
  const reasons: string[] = [];
  const p = inputs.projection;
  if (p) {
    if (!p.known) reasons.push("never rebuilt on this laptop");
    else {
      if (p.schema_drift) reasons.push("schema changed");
      if (p.same_as_drift) reasons.push("same_as edited");
    }
    if (p.unembedded_entities) reasons.push(`${countOf(p.unembedded_entities, "entity", "entities")} not embedded`);
    if (p.unembedded_chunks) reasons.push(`${countOf(p.unembedded_chunks, "chunk", "chunks")} not embedded`);
    if (p.unprojected_keys) reasons.push(`${countOf(p.unprojected_keys, "observation key", "observation keys")} not projected`);
  }
  const last = inputs.lastRebuild;
  const job = inputs.lastJob;
  const finalize = job?.status.finalize;
  const superseded = Boolean(last && last.ok && !last.errors.length && job && last.at > job.at);
  if (finalize?.state === "failed" && !superseded) reasons.push(`last job's rebuild failed: ${finalize.error ?? "unknown error"}`);
  if (last && !last.ok) reasons.push(`last rebuild failed: ${last.summary}`);
  if (last?.ok) for (const error of last.errors) reasons.push(`sweep failed: ${error}`);
  return reasons;
}

type RowFields = Pick<Row, "state" | "glyph" | "tone" | "summary"> & Partial<Pick<Row, "detail" | "items" | "buttons">>;

function makeRow(id: RowId, fields: RowFields): Row {
  return { id, title: ROW_TITLES[id], optional: id === "descriptions", detail: [], items: [], buttons: [], ...fields };
}

function button(action: RowAction, label: string, blockedReason: string | null): RowButton {
  return { action, label, blockedReason };
}

function first(...reasons: Array<string | null>): string | null {
  return reasons.find((reason) => reason !== null) ?? null;
}

/** The readiness checklist (checklist spec §3, §5.1), as a pure function of
 * what was read. The views render it; the controller gates on `blocked`. */
export function checklist(inputs: StateInputs): Checklist {
  const sync = inputs.status?.sync;
  const stores = sync?.stores ?? {};
  const storeStates = Object.values(stores).map((report) => report?.state);
  const graphError = sync?.graph_error ?? null;
  const neo4jUnreachable = Boolean(graphError && graphError.startsWith("graph unreachable"));
  const merge = sync?.operation_in_progress ?? null;
  const conflicts = sync?.unresolved_conflicts ?? [];
  const storeErrors = Object.entries(stores)
    .filter(([, report]) => report?.state === "error")
    .map(([name, report]) => `the ${name} store: ${report?.detail ?? "unknown error"}`);
  const behind = storeStates.includes("behind");
  const noBookmark = storeStates.includes("no_bookmark");
  const notAncestor = storeStates.includes("not_ancestor");

  const job = inputs.activeJob;
  const running = jobRunning(job);
  const pending = inputs.pending;
  const entries = pending ? [...pending.notes, ...pending.binaries, ...pending.tables] : [];
  const pendingErrors = (pending?.errors ?? []).map((e) => `${e.path}: ${e.error}`);
  const lastJob = inputs.lastJob;
  const failed = (lastJob?.results?.files ?? []).filter((f) => f.status === "failed");

  const review = inputs.tablesReview?.tables ?? [];
  const tablesPending = inputs.tablesPending ?? [];
  const readyTables = tablesPending.filter((t) => t.reason !== "ambiguous");
  const ambiguous = tablesPending.filter((t) => t.reason === "ambiguous");

  const reasons = rebuildReasons(inputs);
  const graphDone = Boolean(inputs.projection) && reasons.length === 0;
  const synthesis = inputs.synthesis;
  const toSynthesize = (synthesis ?? []).reduce((sum, d) => sum + d.count, 0);
  const changes = inputs.git.artmindChanges;
  const ahead = inputs.git.ahead;

  const cannotRun = inputs.problem ? problemText(inputs.problem) : null;
  const mergeText = merge ? `${merge[0].toUpperCase()}${merge.slice(1)} is in progress — finish it first` : null;
  const blocked: Record<CommandId, string | null> = {
    pull: mergeText,
    apply: first(
      cannotRun,
      inputs.busy.apply ? "Already applying" : null,
      running ? "An ingest job is running — apply after it finishes" : null,
      mergeText,
      conflicts.length ? "Resolve the artmind conflicts first" : null,
      neo4jUnreachable ? "Neo4j unreachable" : null,
      noBookmark ? "This laptop has never applied this vault: see the first step" : null,
      notAncestor ? "Pull first: the other laptop's commits aren't in this clone" : null,
      behind ? null : "Nothing to apply: the graph has every commit",
    ),
    ingest: first(
      cannotRun,
      running ? "An ingest job is already running" : null,
      mergeText,
      pending && !entries.length ? "Nothing new or changed to ingest" : null,
    ),
    rebuild: first(
      cannotRun,
      inputs.busy.rebuild ? "Already rebuilding" : null,
      running ? "Rebuild after the ingest finishes" : null,
      neo4jUnreachable ? "Neo4j unreachable" : null,
      graphDone ? "The graph is up to date" : null,
    ),
    synthesize: first(
      cannotRun,
      inputs.busy.synthesize ? "Already synthesizing" : null,
      running ? "Synthesize after the ingest finishes" : null,
      neo4jUnreachable ? "Neo4j unreachable" : null,
      synthesis === null ? "Not counted yet: press ↻" : null,
      toSynthesize ? null : "Nothing to synthesize",
    ),
    commitPush: first(
      running ? "Files are still being written — commit after the ingest finishes" : null,
      changes.length || ahead ? null : "Nothing to commit or push",
    ),
    tables: first(cannotRun, neo4jUnreachable ? "Neo4j unreachable" : null, readyTables.length ? null : "No tables waiting"),
    resolve: first(cannotRun, merge === "a merge" ? null : "No merge in progress"),
    doctor: cannotRun,
    admin: cannotRun,
  };

  // ── 1 From the other laptop ──
  // Pull stays available as an outline button wherever pulling makes sense;
  // it is the main button only when a bookmark isn't in this clone (D10).
  const pullButton = button({ type: "pull" }, LABELS.pull, blocked.pull);
  let remote: Row;
  if (cannotRun) remote = makeRow("remote", { state: "attention", glyph: "✗", tone: "red", summary: "artmind can't run", detail: [cannotRun] });
  else if (!inputs.status) remote = makeRow("remote", { state: "waiting", glyph: "○", tone: "grey", summary: "checking…" });
  else if (inputs.busy.apply) remote = makeRow("remote", { state: "running", glyph: "◌", tone: "spinner", summary: "applying to graph…" });
  else if (conflicts.length) {
    remote = makeRow("remote", {
      state: "attention",
      glyph: "⚠",
      tone: "red",
      summary: `${plural(conflicts.length, "artmind file")} in conflict`,
      items: conflicts.map((path) => ({ text: path, tag: null, title: null })),
      buttons: [button({ type: "resolve" }, LABELS.resolve, blocked.resolve)],
    });
  } else if (merge) {
    remote = makeRow("remote", {
      state: "todo",
      glyph: "●",
      tone: "amber",
      summary: "resolved, complete the merge",
      detail: ["Resolve any note Obsidian still shows in conflict, then complete the merge."],
      buttons: [button({ type: "completeMerge" }, LABELS.completeMerge, null)],
    });
  } else if (graphError) {
    remote = makeRow("remote", {
      state: "attention",
      glyph: "!",
      tone: "red",
      summary: neo4jUnreachable ? "Neo4j unreachable" : "can't read the graph",
      detail: [graphError],
      buttons: [pullButton],
    });
  } else if (storeErrors.length) {
    remote = makeRow("remote", { state: "attention", glyph: "!", tone: "red", summary: "an apply would fail", detail: storeErrors, buttons: [pullButton] });
  } else if (notAncestor) {
    remote = makeRow("remote", { state: "todo", glyph: "●", tone: "amber", summary: "the other laptop's commits aren't pulled", buttons: [pullButton] });
  } else if (noBookmark) {
    remote = makeRow("remote", { state: "todo", glyph: "●", tone: "amber", summary: "first sync on this laptop", detail: [BOOTSTRAP_STEP], buttons: [pullButton] });
  } else if (behind) {
    remote = makeRow("remote", {
      state: "todo",
      glyph: "●",
      tone: "amber",
      summary: `${behindParts(stores).join(" · ") || "changes"} to apply`,
      buttons: [button({ type: "apply" }, LABELS.apply, blocked.apply), pullButton],
    });
  } else {
    const applied = inputs.lastApply?.ok ? ` · applied ${clock(inputs.lastApply.at)}` : "";
    remote = makeRow("remote", { state: "done", glyph: "✓", tone: "grey", summary: `up to date${applied}`, buttons: [pullButton] });
  }
  // With auto pull off, nothing tells row 1 the other laptop pushed (D10).
  if (inputs.obsidianGit?.autoPullMinutes === 0 && remote.state !== "waiting" && !cannotRun) {
    remote = { ...remote, detail: [...remote.detail, AUTO_PULL_HINT] };
  }

  // ── 2 Documents ──
  const retryFailed = failed.length && lastJob ? [button({ type: "retryJob", jobId: lastJob.status.job_id }, LABELS.retryFailed, cannotRun)] : [];
  let documents: Row;
  if (job?.stalled) {
    documents = makeRow("documents", {
      state: "attention",
      glyph: "⚠",
      tone: "amber",
      summary: `stalled at ${job.processed_count}/${job.file_count}`,
      detail: ["The worker stopped mid-job; nothing will finish it."],
      buttons: [button({ type: "retryJob", jobId: job.job_id }, LABELS.retryJob, cannotRun)],
    });
  } else if (job && running) {
    documents = makeRow("documents", {
      state: "running",
      glyph: "◌",
      tone: "spinner",
      summary: jobFinishing(job) ? "finishing: building the graph" : `ingesting ${job.processed_count}/${job.file_count}`,
    });
  } else if (entries.length) {
    documents = makeRow("documents", {
      state: "todo",
      glyph: "●",
      tone: "blue",
      summary: `${entries.length} to ingest`,
      detail: [
        ...(neo4jUnreachable ? [NOTICE.neo4jIngest] : []),
        ...(failed.length ? [`${plural(failed.length, "file")} failed in the last job`] : []),
        ...pendingErrors,
      ],
      items: entries.map((e) => ({ text: e.path, tag: e.reason, title: null })),
      buttons: [button({ type: "ingest" }, ingestLabel(entries.length), blocked.ingest), ...retryFailed],
    });
  } else if (failed.length) {
    documents = makeRow("documents", {
      state: "attention",
      glyph: "✗",
      tone: "red",
      summary: `${failed.length} failed`,
      detail: pendingErrors,
      items: failed.map((f) => ({ text: f.filename, tag: f.error_message ?? "failed", title: f.filename })),
      buttons: retryFailed,
    });
  } else if (pendingErrors.length) {
    documents = makeRow("documents", { state: "attention", glyph: "!", tone: "amber", summary: `${plural(pendingErrors.length, "file")} can't be read`, detail: pendingErrors });
  } else if (!pending) {
    documents = makeRow("documents", { state: "waiting", glyph: "○", tone: "grey", summary: "checking…" });
  } else {
    documents = makeRow("documents", { state: "done", glyph: "✓", tone: "grey", summary: "all ingested" });
  }

  // ── 3 Tables ──
  const tableParts = [
    review.length ? `${review.length} need${review.length === 1 ? "s" : ""} review` : null,
    readyTables.length ? `${readyTables.length} ready for the graph` : null,
    ambiguous.length ? `${ambiguous.length} with two mappings` : null,
  ].filter((part): part is string => part !== null);
  let tables: Row;
  if (tableParts.length) {
    tables = makeRow("tables", {
      state: "todo",
      glyph: "●",
      tone: "blue",
      summary: tableParts.join(" · "),
      items: [
        ...review.map((t) => ({ text: `${t.table} (${t.domain})`, tag: unconfirmed(t), title: null })),
        ...readyTables.map((t) => ({ text: `${t.table} (${t.domain})`, tag: t.reason.replace(/_/g, " "), title: null })),
        ...ambiguous.map((t) => ({ text: `${t.table} (${t.domain})`, tag: `matches ${t.mapping}`, title: null })),
      ],
      buttons: [
        ...(review.length ? [button({ type: "adminPrompt", prompt: reviewPrompt(review[0]) }, LABELS.reviewInAdmin, blocked.admin)] : []),
        ...(readyTables.length ? [button({ type: "reviewTable", table: readyTables[0].table }, LABELS.reviewAndProject, blocked.tables)] : []),
        ...(ambiguous.length ? [button({ type: "adminPrompt", prompt: ambiguousPrompt(ambiguous[0]) }, LABELS.askAdmin, blocked.admin)] : []),
      ],
    });
  } else if (inputs.tablesReview === null && inputs.tablesPending === null) {
    tables = makeRow("tables", { state: "waiting", glyph: "○", tone: "grey", summary: "checking…" });
  } else {
    tables = makeRow("tables", { state: "done", glyph: "✓", tone: "grey", summary: "nothing waiting" });
  }

  // ── 4 Graph ──
  const rebuildButton = button({ type: "rebuild" }, LABELS.rebuild, blocked.rebuild);
  let graph: Row;
  if (running) graph = makeRow("graph", { state: "waiting", glyph: "○", tone: "grey", summary: "after ingest finishes" });
  else if (inputs.busy.rebuild) graph = makeRow("graph", { state: "running", glyph: "◌", tone: "spinner", summary: "rebuilding…" });
  else if (reasons.length) graph = makeRow("graph", { state: "attention", glyph: "!", tone: "amber", summary: "needs rebuild", detail: reasons, buttons: [rebuildButton] });
  else if (!inputs.projection) graph = makeRow("graph", { state: "waiting", glyph: "○", tone: "grey", summary: "not checked yet", buttons: [rebuildButton] });
  else {
    const at = Date.parse(inputs.projection.last_rebuilt_at ?? "");
    graph = makeRow("graph", { state: "done", glyph: "✓", tone: "grey", summary: `built · embedded${Number.isNaN(at) ? "" : ` · rebuilt ${clock(at)}`}` });
  }

  // ── 5 Descriptions (optional: never amber, never current) ──
  let descriptions: Row;
  if (inputs.busy.synthesize) {
    const written = changeSources(changes).syntheses;
    const progress = written > 0 && toSynthesize > 0 ? ` ${count(written)}/${count(toSynthesize)}` : "";
    descriptions = makeRow("descriptions", { state: "running", glyph: "◌", tone: "spinner", summary: `synthesizing…${progress}` });
  }
  else if (synthesis === null) descriptions = makeRow("descriptions", { state: "optional", glyph: "◇", tone: "grey", summary: "not counted yet" });
  else if (!toSynthesize) descriptions = makeRow("descriptions", { state: "done", glyph: "✓", tone: "grey", summary: "up to date" });
  else {
    const models = [...new Set(synthesis.map((d) => d.model))].join(", ");
    descriptions = makeRow("descriptions", {
      state: "optional",
      glyph: "◇",
      tone: "blue",
      summary: `${count(toSynthesize)} could be synthesized · ~${countOf(toSynthesize, "LLM call", "LLM calls")} (${models})`,
      items: synthesis.filter((d) => d.count > 0).map((d) => ({ text: d.domain, tag: count(d.count), title: null })),
      buttons: [button({ type: "synthesize" }, LABELS.synthesize, blocked.synthesize)],
    });
  }

  // ── Footer: Vault ──
  const commitButton = button({ type: "commitPush" }, LABELS.commitPush, blocked.commitPush);
  let vault: Row;
  if (running) vault = makeRow("vault", { state: "hidden", glyph: "○", tone: "grey", summary: "after ingest finishes" });
  else if (changes.length) {
    const from = changeSources(changes);
    const parts = [
      from.ingest ? `ingest (${plural(from.ingest, "file")})` : null,
      from.syntheses ? `syntheses (${from.syntheses})` : null,
      from.other ? `other (${from.other})` : null,
    ].filter((part): part is string => part !== null);
    vault = makeRow("vault", {
      state: "todo",
      glyph: "●",
      tone: "blue",
      summary: `✎ ${plural(changes.length, "artmind file")} to commit · ↑ ${ahead ?? 0}`,
      detail: [`from: ${parts.join(" · ")}`],
      buttons: [commitButton],
    });
  } else if (ahead) {
    vault = makeRow("vault", { state: "todo", glyph: "●", tone: "blue", summary: `↑ ${ahead} to push`, buttons: [commitButton] });
  } else {
    vault = makeRow("vault", { state: "done", glyph: "✓", tone: "grey", summary: ahead === null ? "nothing to share (no upstream)" : "shared" });
  }

  const rows = [remote, documents, tables, graph, descriptions, vault].map((r) => {
    const error = inputs.actionErrors[r.id];
    return error ? { ...r, detail: [...r.detail, error] } : r;
  });
  const left = rows.filter((r) => STEP_IDS.includes(r.id) && r.state !== "done");
  const ready = left.length === 0;
  const current = left[0]?.id ?? (vault.state === "todo" ? "vault" : null);
  return { rows, current, ready, stepsLeft: left.length, blocked, neo4jUnreachable, jobRunning: running };
}
