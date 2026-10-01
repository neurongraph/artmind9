/** The admin console (`artmind admin-ui`): find it, start it, open it, stop
 * the one this plugin started. A port answering is not proof it is artmind,
 * let alone this vault's, so every step starts from `GET /api/health`. */

/** `GET /api/health` from an artmind web UI. */
export interface AdminHealth {
  app: string;
  vault: string | null;
  version: string | null;
  pid: number;
}

/** What is on the admin console's port. */
export type Probe =
  | { kind: "admin"; health: AdminHealth }
  /** Answering, but not an artmind admin-ui: another app, or an artmind too
   * old to have /api/health. */
  | { kind: "foreign" }
  | { kind: "down" };

export interface Spawned {
  pid: number | null;
  /** Resolves when the process exits; a start that never comes up exits early. */
  exited: Promise<number | null>;
}

export interface AdminDeps {
  probe(baseUrl: string): Promise<Probe>;
  /** `artmind admin-ui --host H --port P`, detached, from the vault root. */
  spawn(host: string, port: number): Spawned;
  open(url: string): void;
  /** SIGTERM; false when there was no such process. */
  kill(pid: number): boolean;
  /** The last lines of the admin console's log, for a start that failed. */
  logTail(): Promise<string>;
  sleep(ms: number): Promise<void>;
}

export interface AdminContext {
  url: () => string;
  vaultRoot: string;
  /** The pid of the admin console this plugin started, or 0. */
  startedPid: () => number;
  saveStartedPid: (pid: number) => Promise<void>;
}

export type AdminOutcome =
  | { kind: "opened"; url: string; started: boolean }
  | { kind: "other_vault"; vault: string | null; url: string }
  | { kind: "foreign"; port: number }
  | { kind: "failed"; message: string }
  | { kind: "busy" };

export type StopOutcome =
  | { kind: "stopped" }
  | { kind: "not_running" }
  | { kind: "not_ours"; pid: number }
  | { kind: "failed"; message: string };

export const START_TIMEOUT_MS = 20_000;
export const START_POLL_MS = 500;

function samePath(a: string | null, b: string): boolean {
  const norm = (p: string) => p.replace(/[\\/]+$/, "");
  return a !== null && norm(a) === norm(b);
}

export function hostPort(url: string): { host: string; port: number; base: string } {
  const parsed = new URL(url);
  const port = parsed.port ? Number(parsed.port) : parsed.protocol === "https:" ? 443 : 80;
  return { host: parsed.hostname, port, base: `${parsed.protocol}//${parsed.host}` };
}

export class AdminConsole {
  private deps: AdminDeps;
  private ctx: AdminContext;
  private starting = false;

  constructor(deps: AdminDeps, ctx: AdminContext) {
    this.deps = deps;
    this.ctx = ctx;
  }

  get isStarting(): boolean {
    return this.starting;
  }

  /** Open `path` on this vault's admin console, starting it first if
   * nothing is on its port. */
  async open(path = "/"): Promise<AdminOutcome> {
    if (this.starting) return { kind: "busy" };
    let target: ReturnType<typeof hostPort>;
    try {
      target = hostPort(this.ctx.url());
    } catch {
      return { kind: "failed", message: `"${this.ctx.url()}" is not a URL: fix the admin console URL in settings.` };
    }
    const url = `${target.base}${path}`;
    const found = await this.deps.probe(target.base);
    if (found.kind === "admin") {
      if (!samePath(found.health.vault, this.ctx.vaultRoot)) return { kind: "other_vault", vault: found.health.vault, url };
      this.deps.open(url);
      return { kind: "opened", url, started: false };
    }
    if (found.kind === "foreign") return { kind: "foreign", port: target.port };

    this.starting = true;
    try {
      const spawned = this.deps.spawn(target.host, target.port);
      let exited = false;
      void spawned.exited.then(() => (exited = true));
      for (let waited = 0; waited < START_TIMEOUT_MS; waited += START_POLL_MS) {
        await this.deps.sleep(START_POLL_MS);
        const up = await this.deps.probe(target.base);
        if (up.kind === "admin") {
          await this.ctx.saveStartedPid(up.health.pid);
          this.deps.open(url);
          return { kind: "opened", url, started: true };
        }
        if (exited) break;
      }
      const tail = (await this.deps.logTail()).trim();
      const why = exited ? "it exited while starting" : `it did not answer within ${START_TIMEOUT_MS / 1000} s`;
      return { kind: "failed", message: `The admin console did not start: ${why}.${tail ? `\n${tail}` : ""}` };
    } finally {
      this.starting = false;
    }
  }

  /** Stop the admin console this plugin started; never another one. */
  async stop(): Promise<StopOutcome> {
    const ours = this.ctx.startedPid();
    let target: ReturnType<typeof hostPort>;
    try {
      target = hostPort(this.ctx.url());
    } catch {
      return { kind: "failed", message: `"${this.ctx.url()}" is not a URL.` };
    }
    const found = await this.deps.probe(target.base);
    if (found.kind !== "admin") {
      if (ours) await this.ctx.saveStartedPid(0);
      return { kind: "not_running" };
    }
    if (!ours || found.health.pid !== ours) return { kind: "not_ours", pid: found.health.pid };
    if (!this.deps.kill(ours)) return { kind: "failed", message: `Could not stop process ${ours}.` };
    await this.ctx.saveStartedPid(0);
    return { kind: "stopped" };
  }
}

/** The words for each outcome; null when nothing needs saying. */
export function adminOutcomeText(outcome: AdminOutcome): string | null {
  switch (outcome.kind) {
    case "opened":
      return outcome.started ? "Started the admin console." : null;
    case "other_vault":
      return `The admin console on this port is serving another vault${outcome.vault ? ` (${outcome.vault})` : ""}. Stop it there, or give this vault another port in settings.`;
    case "foreign":
      return `Something other than artmind's admin console is on port ${outcome.port} (or an artmind too old to identify itself). Free the port, or change the admin console URL in settings.`;
    case "failed":
      return outcome.message;
    case "busy":
      return "The admin console is still starting.";
  }
}

export function stopOutcomeText(outcome: StopOutcome): string {
  switch (outcome.kind) {
    case "stopped":
      return "Stopped the admin console.";
    case "not_running":
      return "The admin console is not running.";
    case "not_ours":
      return `The admin console (process ${outcome.pid}) was not started from Obsidian: stop it where it was started.`;
    case "failed":
      return outcome.message;
  }
}
