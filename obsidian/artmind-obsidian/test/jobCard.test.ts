// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { jobCard, type JobCardJob } from "../src/views/jobCard";
import { fileState, jobGroups } from "../src/views/jobGroups";
import { newPanelUi } from "../src/views/sections";

function largeJob(): JobCardJob {
  const rows = (status: string, n: number) => Array.from({ length: n }, (_, i) => ({ filename: `${status}-${i}.md`, status }));
  return {
    job_id: "synthetic-job", status: "processing", file_count: 32,
    files: [
      ...rows("completed", 12).map((file, i) => ({ ...file, entities: i + 1, relationships: 2 })),
      ...rows("processing", 2), ...rows("queued", 9),
      ...rows("failed", 7).map((file) => ({ ...file, error_message: "Synthetic failure" })),
      ...rows("skipped", 2),
    ],
  };
}

describe("compact ingest groups", () => {
  it("groups without mutating queue order and keeps unknown statuses queued", () => {
    const job = largeJob();
    const before = JSON.stringify(job.files);
    expect(jobGroups(job.files).map((group) => group.key)).toEqual(["running", "queued", "failed", "done", "skipped"]);
    expect(jobGroups([])).toEqual([]);
    expect(fileState("future-state")).toBe("queued");
    expect(jobGroups([{ filename: "old.md", status: "future-state" }])[0].key).toBe("queued");
    expect(JSON.stringify(job.files)).toBe(before);
  });

  it("previews five queued/completed, shows all running/failures and scales totals over hidden files", () => {
    const root = document.createElement("div");
    jobCard(root, newPanelUi(), largeJob(), true, { openAdmin() {} });
    const group = (key: string) => root.querySelector(`[data-job-group="${key}"]`)!;
    expect([...root.querySelectorAll(".artmind-job-group-heading")].map((h) => h.textContent)).toEqual([
      "Running · 2", "Up next · 9", "Needs attention · 7", "Recently completed · 12", "Skipped · 2",
    ]);
    for (const [key, n] of [["running", 2], ["queued", 5], ["failed", 7], ["done", 5]] as const) {
      expect(group(key).querySelectorAll(".artmind-file")).toHaveLength(n);
    }
    expect([...group("done").querySelectorAll(".artmind-file-name")].map((f) => f.textContent)).toEqual([
      "completed-11.md", "completed-10.md", "completed-9.md", "completed-8.md", "completed-7.md",
    ]);
    expect(group("queued").querySelector(".artmind-file-name")!.textContent).toBe("queued-0.md");
    expect((group("skipped") as HTMLDetailsElement).open).toBe(false);
    expect(root.querySelector(".artmind-totals")!.textContent).toBe("78 entities · 24 relationships");
    expect((group("done").querySelector(".artmind-meter-fill") as HTMLElement).style.width).toBe("100%");
  });

  it("remembers expansions for the same job and finished card, but resets for a new job", () => {
    const root = document.createElement("div");
    const ui = newPanelUi();
    const job = largeJob();
    const render = (live = true) => {
      root.replaceChildren();
      jobCard(root, ui, job, live, { openAdmin() {} });
    };
    const queued = () => root.querySelector<HTMLButtonElement>('[data-job-control="queued"]')!;
    render();
    queued().click();
    const skipped = root.querySelector<HTMLDetailsElement>('[data-job-group="skipped"]')!;
    skipped.open = true;
    skipped.dispatchEvent(new Event("toggle"));
    render();
    expect(queued().textContent).toBe("Show fewer");
    expect(queued().getAttribute("aria-expanded")).toBe("true");
    expect(root.querySelectorAll('[data-job-group="queued"] .artmind-file')).toHaveLength(9);
    expect(root.querySelector<HTMLDetailsElement>('[data-job-group="skipped"]')!.open).toBe(true);
    // A delayed event from an obsolete disclosure must not undo current state.
    skipped.open = false;
    skipped.dispatchEvent(new Event("toggle"));
    render(false);
    expect(queued().textContent).toBe("Show fewer");
    expect(root.querySelector<HTMLDetailsElement>('[data-job-group="skipped"]')!.open).toBe(true);
    queued().click();
    expect(root.querySelectorAll('[data-job-group="queued"] .artmind-file')).toHaveLength(5);
    queued().click();
    job.job_id = "different-job";
    render();
    expect(queued().textContent).toBe("Show all 9");
    expect(root.querySelector<HTMLDetailsElement>('[data-job-group="skipped"]')!.open).toBe(false);
  });
});
