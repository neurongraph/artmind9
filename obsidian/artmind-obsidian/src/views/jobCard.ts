import { plural } from "../state";
import type { ChunkProgress, KgCounts } from "../types";
import { el } from "./dom";
import { type PanelUi, basename, section } from "./sections";

type CardFile = KgCounts & {
  filename: string;
  status: string;
  current_step?: string | null;
  error_message?: string | null;
  chunk_progress?: ChunkProgress;
};

export interface JobCardJob {
  job_id: string;
  status: string;
  file_count: number;
  files: CardFile[];
  stalled?: boolean;
}

/** Order and labels of the file states in the bar and its legend. */
const FILE_STATES: Array<{ key: string; label: string; match: (status: string) => boolean }> = [
  { key: "done", label: "done", match: (s) => s === "completed" },
  { key: "running", label: "running", match: (s) => s === "processing" },
  { key: "queued", label: "queued", match: (s) => s === "queued" },
  { key: "skipped", label: "skipped", match: (s) => s === "skipped" },
  { key: "failed", label: "failed", match: (s) => s === "failed" },
];

function fileState(status: string): string {
  return FILE_STATES.find((s) => s.match(status))?.key ?? "queued";
}

const GLYPH: Record<string, string> = { done: "✓", running: "◌", queued: "…", skipped: "–", failed: "✗" };

function meter(parent: HTMLElement, cls: string, fraction: number, label: string, title: string): void {
  const box = el(parent, "span", { cls: `artmind-meter ${cls}`, attr: { title } });
  const track = el(box, "span", { cls: "artmind-meter-track" });
  const fill = el(track, "span", { cls: "artmind-meter-fill" });
  fill.style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;
  el(box, "span", { cls: "artmind-meter-label", text: label });
}

function chunkFraction(p: ChunkProgress): number {
  if (!p.total_chunks) return 0;
  return (p.entities_done + p.properties_done + p.relationships_done) / (3 * p.total_chunks);
}

/** One job, live or finished: a segmented bar of file states, the job's
 * totals, and a row per file with its entity and relationship counts (bars
 * scaled to the job's largest file) or, while extracting, its chunk
 * progress. Retrying is row 2's button, not the card's. */
export function jobCard(root: HTMLElement, ui: PanelUi, job: JobCardJob, live: boolean, handlers: { openAdmin(path?: string): void }): void {
  const files = job.files;
  const counts = new Map(FILE_STATES.map((s) => [s.key, 0]));
  for (const f of files) counts.set(fileState(f.status), (counts.get(fileState(f.status)) ?? 0) + 1);
  const finished = (counts.get("done") ?? 0) + (counts.get("failed") ?? 0) + (counts.get("skipped") ?? 0);
  const failed = counts.get("failed") ?? 0;
  const stalled = live && Boolean(job.stalled);
  const title = stalled ? "Ingest job (stalled)" : live ? "Ingest job" : "Last ingest job";
  const tone = failed ? "red" : stalled ? "amber" : live ? "spinner" : "grey";
  const box = section(root, ui, "job", title, `${finished}/${plural(job.file_count, "file")}`, tone);

  const bar = el(box, "div", { cls: "artmind-segments", attr: { title: `Job ${job.job_id}: ${job.status}` } });
  for (const s of FILE_STATES) {
    const n = counts.get(s.key) ?? 0;
    if (!n) continue;
    const seg = el(bar, "span", { cls: `artmind-segment artmind-segment-${s.key}` });
    seg.style.flexGrow = String(n);
  }
  el(box, "div", {
    cls: "artmind-legend",
    text: FILE_STATES.filter((s) => counts.get(s.key)).map((s) => `${counts.get(s.key)} ${s.label}`).join(" · "),
  });

  const counted = files.filter((f) => typeof f.entities === "number");
  const maxE = Math.max(1, ...counted.map((f) => f.entities ?? 0));
  const maxR = Math.max(1, ...counted.map((f) => f.relationships ?? 0));
  if (counted.length) {
    const e = counted.reduce((sum, f) => sum + (f.entities ?? 0), 0);
    const r = counted.reduce((sum, f) => sum + (f.relationships ?? 0), 0);
    el(box, "div", { cls: "artmind-totals", text: `${plural(e, "entity", "entities")} · ${plural(r, "relationship")}` });
  }

  const list = el(box, "div", { cls: "artmind-files" });
  const drill = el(box, "a", { cls: "artmind-admin-link", text: "Chunks and artifacts in the admin console ↗", attr: { href: "#" } });
  drill.addEventListener("click", (event) => {
    event.preventDefault();
    handlers.openAdmin("/dashboard");
  });

  for (const f of files) {
    const key = fileState(f.status);
    const line = el(list, "div", { cls: `artmind-file artmind-file-${key}` });
    el(line, "span", { cls: "artmind-file-glyph", text: GLYPH[key] });
    el(line, "span", { cls: "artmind-file-name", text: basename(f.filename), attr: { title: f.filename } });
    const detail = el(line, "span", { cls: "artmind-file-detail" });
    if (key === "failed") {
      el(detail, "span", { cls: "artmind-error", text: f.error_message ?? "failed" });
    } else if (typeof f.entities === "number") {
      const e = f.entities;
      const r = f.relationships ?? 0;
      meter(detail, "artmind-meter-entities", e / maxE, `${e} E`, plural(e, "entity", "entities"));
      meter(detail, "artmind-meter-relationships", r / maxR, `${r} R`, plural(r, "relationship"));
    } else if (f.chunk_progress) {
      const p = f.chunk_progress;
      meter(
        detail,
        "artmind-meter-chunks",
        chunkFraction(p),
        `${p.total_chunks} chunks`,
        `entities ${p.entities_done}/${p.total_chunks} · properties ${p.properties_done}/${p.total_chunks} · relationships ${p.relationships_done}/${p.total_chunks}`,
      );
    } else {
      el(detail, "span", { cls: "artmind-muted", text: key === "done" ? "no new extraction" : (f.current_step ?? key).replace(/_/g, " ") });
    }
  }
}
