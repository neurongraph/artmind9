import type { JobCardJob } from "./jobCard";

export type FileState = "done" | "running" | "queued" | "skipped" | "failed";

export function fileState(status: string): FileState {
  switch (status) {
    case "completed": return "done";
    case "processing": return "running";
    case "failed": return "failed";
    case "skipped": return "skipped";
    default: return "queued";
  }
}

/** Queue order is stable; completed rows use reverse queue order, not timestamps. */
export function jobGroups(files: JobCardJob["files"]) {
  const definitions: Array<{ key: FileState; label: string; preview?: number }> = [
    { key: "running", label: "Running" },
    { key: "queued", label: "Up next", preview: 5 },
    { key: "failed", label: "Needs attention" },
    { key: "done", label: "Recently completed", preview: 5 },
    { key: "skipped", label: "Skipped" },
  ];
  return definitions.map((group) => {
    const rows = files.filter((file) => fileState(file.status) === group.key);
    if (group.key === "done") rows.reverse();
    return { ...group, files: rows };
  }).filter((group) => group.files.length);
}
