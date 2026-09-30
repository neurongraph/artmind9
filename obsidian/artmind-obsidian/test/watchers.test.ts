import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type WatcherDeps, Watchers } from "../src/watchers";

/** Deps whose HEAD readings come from `heads` (the last one repeats). */
function deps(heads: Array<string | null>, overrides: Partial<WatcherDeps> = {}) {
  const calls = { settled: [] as Array<[string, string | null]>, newFiles: [] as string[][], refresh: [] as string[] };
  let i = 0;
  const d: WatcherDeps = {
    readHead: async () => heads[Math.min(i++, heads.length - 1)],
    onHeadSettled: (head, previous) => calls.settled.push([head, previous]),
    onNewFiles: (paths) => calls.newFiles.push(paths),
    promptsOnArrival: (path) => path.endsWith(".pdf"),
    refresh: (reason) => calls.refresh.push(reason),
    pollJob: async () => false,
    ...overrides,
  };
  return { d, calls };
}

const INTERVALS = { headPollMs: 15_000, newFileGroupMs: 10_000, jobPollMs: 3_000 };

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-30T12:00:00Z"));
});
afterEach(() => vi.useRealTimers());

describe("the HEAD poll (spec §4.1)", () => {
  it("acts on a new HEAD only after one more unchanged reading", async () => {
    const { d, calls } = deps(["a", "b", "b"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();

    await vi.advanceTimersByTimeAsync(15_000);
    expect(calls.settled).toEqual([]);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(calls.settled).toEqual([["b", "a"]]);
    w.stop();
  });

  it("waits out a HEAD that is still moving", async () => {
    const { d, calls } = deps(["a", "b", "c", "c"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();

    await vi.advanceTimersByTimeAsync(45_000);
    expect(calls.settled).toEqual([["c", "a"]]);
    w.stop();
  });

  it("ignores an unchanged HEAD and a failed read", async () => {
    const { d, calls } = deps(["a", "a", null, null, "a"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();

    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls.settled).toEqual([]);
    w.stop();
  });

  it("remembers when a pull landed, for 'pull first' (spec §4.2)", async () => {
    const { d } = deps(["a", "b", "b"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();
    expect(w.pullIsOld()).toBe(true);

    await vi.advanceTimersByTimeAsync(30_000);
    expect(w.pullIsOld()).toBe(false);
    await vi.advanceTimersByTimeAsync(5 * 60_000 + 1);
    expect(w.pullIsOld()).toBe(true);
    w.stop();
  });

  it("waits for the HEAD a plugin-run Pull brings", async () => {
    const { d } = deps(["a", "a", "b", "b"]);
    const w = new Watchers(d, { ...INTERVALS, headPollMs: 1_000_000 });
    await w.start();

    const settled = w.waitForPull(60_000);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await settled).toBe("b");
    expect(w.pullIsOld()).toBe(false);
    w.stop();
  });

  it("gives up waiting on a Pull that brought nothing, and still counts it", async () => {
    const { d } = deps(["a"]);
    const w = new Watchers(d, { ...INTERVALS, headPollMs: 1_000_000 });
    await w.start();

    const settled = w.waitForPull(20_000);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(await settled).toBe("a");
    expect(w.pullIsOld()).toBe(false);
    w.stop();
  });
});

describe("new files (spec §4.3)", () => {
  it("groups arrivals within 10 s into one notice, then refreshes", async () => {
    const { d, calls } = deps(["a"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();

    w.fileCreated("Team/a.pdf");
    await vi.advanceTimersByTimeAsync(5_000);
    w.fileCreated("Team/b.pdf");
    w.fileCreated("Team/note.md");
    await vi.advanceTimersByTimeAsync(9_999);
    expect(calls.newFiles).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls.newFiles).toEqual([["Team/a.pdf", "Team/b.pdf"]]);
    expect(calls.refresh).toEqual(["new files"]);

    w.fileCreated("Team/c.pdf");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(calls.newFiles).toEqual([["Team/a.pdf", "Team/b.pdf"], ["Team/c.pdf"]]);
    w.stop();
  });

  it("does nothing before start (dormant) or for files that never prompt", async () => {
    const { d, calls } = deps(["a"]);
    const w = new Watchers(d, INTERVALS);
    w.fileCreated("Team/a.pdf");
    await w.start();
    w.fileCreated("Team/note.md");
    await vi.advanceTimersByTimeAsync(20_000);
    expect(calls.newFiles).toEqual([]);
    w.stop();
  });
});

describe("focus and the job poll", () => {
  it("refreshes when Obsidian regains focus", async () => {
    const { d, calls } = deps(["a"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();
    w.focusRegained();
    expect(calls.refresh).toEqual(["focus"]);
    w.stop();
  });

  it("polls the job every 3 s until it stops running", async () => {
    const answers = [true, true, false];
    const pollJob = vi.fn(async () => answers.shift() ?? false);
    const { d } = deps(["a"], { pollJob });
    const w = new Watchers(d, INTERVALS);
    await w.start();

    w.trackJob();
    w.trackJob();
    expect(w.trackingJob).toBe(true);
    await vi.advanceTimersByTimeAsync(9_000);
    expect(pollJob).toHaveBeenCalledTimes(3);
    expect(w.trackingJob).toBe(false);
    await vi.advanceTimersByTimeAsync(9_000);
    expect(pollJob).toHaveBeenCalledTimes(3);
    w.stop();
  });

  it("stop() clears every timer", async () => {
    const { d, calls } = deps(["a", "b", "b"]);
    const w = new Watchers(d, INTERVALS);
    await w.start();
    w.fileCreated("Team/a.pdf");
    w.stop();

    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls.settled).toEqual([]);
    expect(calls.newFiles).toEqual([]);
  });
});
