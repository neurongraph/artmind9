/** The plugin's clocks and listeners (spec §4.1, §4.3, §4.4): the HEAD poll,
 * new files in mapped folders, focus regained, and the job poll. Each only
 * schedules work; what the work is belongs to the controller. */

export interface Clock {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

export const realClock: Clock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  setInterval: (fn, ms) => setInterval(fn, ms),
  clearInterval: (handle) => clearInterval(handle as ReturnType<typeof setInterval>),
};

export interface WatcherDeps {
  readHead(): Promise<string | null>;
  /** HEAD moved and then held still for one more reading. */
  onHeadSettled(head: string, previous: string | null): void;
  /** Files that prompt on arrival (already filtered), grouped. */
  onNewFiles(paths: string[]): void;
  promptsOnArrival(path: string): boolean;
  /** Schedule a status refresh (the controller debounces it). */
  refresh(reason: string): void;
  /** Read the tracked job once; resolves false when it is no longer running. */
  pollJob(): Promise<boolean>;
}

export interface Intervals {
  headPollMs: number;
  newFileGroupMs: number;
  jobPollMs: number;
}

export const DEFAULT_INTERVALS: Intervals = { headPollMs: 15_000, newFileGroupMs: 10_000, jobPollMs: 3_000 };

export class Watchers {
  private deps: WatcherDeps;
  private clock: Clock;
  private intervals: Intervals;
  private headTimer: unknown = null;
  private jobTimer: unknown = null;
  private groupTimer: unknown = null;
  private arrivals: string[] = [];
  private stable: string | null = null;
  private candidate: string | null = null;
  private settleWaiters: Array<(head: string | null) => void> = [];
  private started = false;
  /** When the HEAD poll last saw a pull land (or the plugin ran one). */
  lastPullAt: number | null = null;

  constructor(deps: WatcherDeps, intervals: Intervals = DEFAULT_INTERVALS, clock: Clock = realClock) {
    this.deps = deps;
    this.intervals = intervals;
    this.clock = clock;
  }

  /** Reads HEAD once as the baseline (not a pull), then polls. */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.stable = await this.deps.readHead();
    this.headTimer = this.clock.setInterval(() => void this.pollHead(), this.intervals.headPollMs);
  }

  stop(): void {
    this.started = false;
    if (this.headTimer !== null) this.clock.clearInterval(this.headTimer);
    if (this.jobTimer !== null) this.clock.clearInterval(this.jobTimer);
    if (this.groupTimer !== null) this.clock.clearTimeout(this.groupTimer);
    this.headTimer = this.jobTimer = this.groupTimer = null;
    this.arrivals = [];
  }

  /** One HEAD reading. A new HEAD must be read twice in a row before it
   * counts, so a merge still being written is never read half-done. */
  async pollHead(): Promise<void> {
    const head = await this.deps.readHead();
    if (head === null || head === this.stable) {
      this.candidate = null;
      return;
    }
    if (head !== this.candidate) {
      this.candidate = head;
      return;
    }
    const previous = this.stable;
    this.stable = head;
    this.candidate = null;
    this.lastPullAt = this.clock.now();
    for (const resolve of this.settleWaiters.splice(0)) resolve(head);
    this.deps.onHeadSettled(head, previous);
  }

  /** After the plugin ran Obsidian Git's Pull: resolves with the settled
   * HEAD once it moved, or with the unchanged HEAD after `timeoutMs` (a
   * pull with nothing new). Either way the pull counts as seen now. */
  waitForPull(timeoutMs = 60_000): Promise<string | null> {
    return new Promise((resolve) => {
      let done = false;
      const finish = (head: string | null) => {
        if (done) return;
        done = true;
        this.lastPullAt = this.clock.now();
        resolve(head);
      };
      this.settleWaiters.push(finish);
      this.clock.setTimeout(() => finish(this.stable), timeoutMs);
      const poll = () => {
        if (done) return;
        void this.pollHead().then(() => {
          if (!done) this.clock.setTimeout(poll, 2_000);
        });
      };
      this.clock.setTimeout(poll, 2_000);
    });
  }

  /** Obsidian's `create` event, for every new file (vault-relative path). */
  fileCreated(path: string): void {
    if (!this.started || !this.deps.promptsOnArrival(path)) return;
    this.arrivals.push(path);
    if (this.groupTimer !== null) this.clock.clearTimeout(this.groupTimer);
    this.groupTimer = this.clock.setTimeout(() => {
      this.groupTimer = null;
      const paths = this.arrivals.splice(0);
      if (paths.length) this.deps.onNewFiles(paths);
      this.deps.refresh("new files");
    }, this.intervals.newFileGroupMs);
  }

  focusRegained(): void {
    if (this.started) this.deps.refresh("focus");
  }

  /** Poll the tracked job every few seconds until it stops running. */
  trackJob(): void {
    if (this.jobTimer !== null || !this.started) return;
    this.jobTimer = this.clock.setInterval(() => {
      void this.deps.pollJob().then((running) => {
        if (!running && this.jobTimer !== null) {
          this.clock.clearInterval(this.jobTimer);
          this.jobTimer = null;
        }
      });
    }, this.intervals.jobPollMs);
  }

  get trackingJob(): boolean {
    return this.jobTimer !== null;
  }

  /** Whether the last pull is older than `maxAgeMs` (or never seen). */
  pullIsOld(maxAgeMs = 5 * 60_000): boolean {
    return this.lastPullAt === null || this.clock.now() - this.lastPullAt > maxAgeMs;
  }
}
