/** A warm `artmind serve` for this vault: reuse one already serving it,
 * start one when its port is free, stop only the one this plugin started.
 * `artmind query` (the plugin's and the chat agent's) proxies to whatever
 * artmind answers `/health` on `ARTMIND_SERVE_PORT`, so every step starts
 * from that probe -- and a serve of another vault is never reused. */
import type { Spawned } from "./admin";

/** `GET /health` from `artmind serve`. */
export interface ServeHealth {
  service: "artmind";
  version: string | null;
  vault: string | null;
  pid: number;
}

/** What is on serve's port. */
export type ServeProbe =
  | { kind: "serve"; health: ServeHealth }
  /** artmind serve, but too old to say which vault it serves. */
  | { kind: "old" }
  /** Answering, but not artmind serve. */
  | { kind: "foreign" }
  | { kind: "down" };

export interface ServeDeps {
  probe(baseUrl: string): Promise<ServeProbe>;
  /** Nothing listens on 127.0.0.1:P (a probe can't tell a non-HTTP listener from none). */
  isFree(port: number): Promise<boolean>;
  /** `artmind serve --port P`, detached, from the vault root. */
  spawn(port: number): Spawned;
  /** SIGTERM; false when there was no such process. */
  kill(pid: number): boolean;
  /** The last lines of serve's log, for a start that failed. */
  logTail(): Promise<string>;
  sleep(ms: number): Promise<void>;
}

export interface ServeContext {
  enabled: () => boolean;
  /** The port set in settings; 0 picks one per vault (`autoPorts`). */
  port: () => number;
  vaultRoot: string;
  /** The pid of the serve this plugin started, or 0. */
  startedPid: () => number;
  saveStartedPid: (pid: number) => Promise<void>;
}

export type ServeState =
  | { kind: "off" }
  | { kind: "starting"; port: number }
  | { kind: "running"; port: number; pid: number; started: boolean }
  | { kind: "other_vault"; port: number; vault: string | null }
  | { kind: "old"; port: number }
  | { kind: "foreign"; port: number }
  | { kind: "failed"; message: string };

export type ServeStop =
  | { kind: "stopped" }
  | { kind: "not_running" }
  | { kind: "not_ours"; pid: number; vault: string | null }
  | { kind: "failed"; message: string };

export const SERVE_START_TIMEOUT_MS = 30_000;
export const SERVE_STOP_TIMEOUT_MS = 10_000;
export const SERVE_POLL_MS = 500;

/** Automatic serve ports: away from 8377-8379 (serve, chat-ui and admin-ui's
 * defaults) and the rest of 83xx, where a second admin-ui tends to land. */
export const AUTO_PORT_MIN = 18400;
export const AUTO_PORT_SPAN = 500;
/** How many ports of the range one start tries before giving up. */
export const AUTO_PORT_TRIES = 20;

const normPath = (p: string) => p.replace(/[\\/]+$/, "");

function samePath(a: string | null, b: string): boolean {
  return a !== null && normPath(a) === normPath(b);
}

/** The ports an automatic serve tries, in order: from a hash of the vault's
 * path, so a vault comes back to the same port after a reload and two vaults
 * rarely start on the same one. */
export function autoPorts(vaultRoot: string): number[] {
  let hash = 0x811c9dc5;
  for (const ch of normPath(vaultRoot)) {
    hash ^= ch.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  const first = hash % AUTO_PORT_SPAN;
  return Array.from({ length: AUTO_PORT_TRIES }, (_, i) => AUTO_PORT_MIN + ((first + i) % AUTO_PORT_SPAN));
}

const base = (port: number) => `http://127.0.0.1:${port}`;

export class ServeManager {
  private deps: ServeDeps;
  private ctx: ServeContext;
  private current: ServeState = { kind: "off" };
  private busy = false;
  /** The port the running serve was found or started on: a port changed in
   * settings since must not strand the one this plugin started. */
  private activePort: number | null = null;
  private listener: ((state: ServeState) => void) | null = null;

  constructor(deps: ServeDeps, ctx: ServeContext) {
    this.deps = deps;
    this.ctx = ctx;
  }

  get state(): ServeState {
    return this.current;
  }

  onChange(listener: (state: ServeState) => void): void {
    this.listener = listener;
  }

  /** The ports serve may be on: the one set in settings, or (0) this vault's automatic ones. */
  private candidates(): number[] {
    const port = this.ctx.port();
    return port ? [port] : autoPorts(this.ctx.vaultRoot);
  }

  private set(state: ServeState): ServeState {
    this.current = state;
    this.listener?.(state);
    return state;
  }

  /** What the plugin's own `artmind` runs (and the admin console it starts)
   * add to their environment: serve's port, and no proxying at all when the
   * artmind on that port is not this vault's serve. Nothing when off. */
  env(): Record<string, string> {
    if (!this.ctx.enabled()) return {};
    const env: Record<string, string> = { ARTMIND_SERVE_PORT: String(this.activePort ?? this.candidates()[0]) };
    if (this.current.kind === "other_vault" || this.current.kind === "old") env.ARTMIND_NO_PROXY = "1";
    return env;
  }

  /** On load and when the setting is turned on: reuse or start. Off: nothing. */
  async ensure(): Promise<ServeState> {
    if (!this.ctx.enabled()) return this.set({ kind: "off" });
    if (this.busy) return this.current;
    this.busy = true;
    try {
      return await this.start();
    } finally {
      this.busy = false;
    }
  }

  /** Stop the one this plugin started (waiting until its port is free),
   * then reuse or start on the configured port. A serve started elsewhere
   * is never stopped. */
  async restart(): Promise<ServeState | ServeStop> {
    if (!this.ctx.enabled()) return this.set({ kind: "off" });
    if (this.busy) return this.current;
    this.busy = true;
    try {
      const stopped = await this.stopOurs();
      if (stopped.kind === "failed") return stopped;
      // Another vault's serve is not this restart's to refuse: start says so.
      if (stopped.kind === "not_ours" && samePath(stopped.vault, this.ctx.vaultRoot)) return stopped;
      if (stopped.kind === "stopped") {
        const port = this.activePort ?? this.candidates()[0];
        for (let waited = 0; waited < SERVE_STOP_TIMEOUT_MS; waited += SERVE_POLL_MS) {
          if ((await this.deps.probe(base(port))).kind === "down") break;
          await this.deps.sleep(SERVE_POLL_MS);
        }
      }
      return await this.start();
    } finally {
      this.busy = false;
    }
  }

  /** On unload and when the setting is turned off. */
  async stop(): Promise<ServeStop> {
    const outcome = await this.stopOurs();
    if (outcome.kind === "stopped" || outcome.kind === "not_running") this.set({ kind: "off" });
    return outcome;
  }

  private async stopOurs(): Promise<ServeStop> {
    const ours = this.ctx.startedPid();
    const { found } = await this.locate();
    if (found.kind !== "serve") {
      if (ours) await this.ctx.saveStartedPid(0);
      return { kind: "not_running" };
    }
    if (!ours || found.health.pid !== ours) return { kind: "not_ours", pid: found.health.pid, vault: found.health.vault };
    if (!this.deps.kill(ours)) return { kind: "failed", message: `Could not stop artmind serve (process ${ours}).` };
    await this.ctx.saveStartedPid(0);
    this.activePort = null;
    return { kind: "stopped" };
  }

  /** What is on the port this vault's serve is on: where it was found or
   * started, else the first of this vault's ports with a serve of this vault
   * on it (a reload forgets the port an automatic start fell back to). */
  private async locate(): Promise<{ port: number; found: ServeProbe }> {
    if (this.activePort !== null) return { port: this.activePort, found: await this.deps.probe(base(this.activePort)) };
    const ports = this.candidates();
    let first: ServeProbe | null = null;
    for (const port of ports) {
      const found = await this.deps.probe(base(port));
      if (found.kind === "serve" && samePath(found.health.vault, this.ctx.vaultRoot)) return { port, found };
      first ??= found;
    }
    return { port: ports[0], found: first ?? { kind: "down" } };
  }

  private async start(): Promise<ServeState> {
    const port = this.ctx.port();
    if (port) return this.startOn(port);
    // Automatic: this vault's own serve on any of its ports (one that took a
    // later port while an earlier one was busy is still found), else the first
    // port that is free. Another vault's serve, or anything else listening, is skipped.
    const ports = autoPorts(this.ctx.vaultRoot);
    const probes: ServeProbe[] = [];
    for (const candidate of ports) {
      const found = await this.deps.probe(base(candidate));
      if (found.kind === "serve" && samePath(found.health.vault, this.ctx.vaultRoot)) return this.startOn(candidate, found);
      probes.push(found);
    }
    for (const [i, candidate] of ports.entries()) {
      if (probes[i].kind === "down" && (await this.deps.isFree(candidate))) return this.startOn(candidate, probes[i]);
    }
    return this.set({ kind: "failed", message: `No free port for artmind serve (tried ${AUTO_PORT_TRIES} from ${ports[0]}). Set a serve port in settings.` });
  }

  private async startOn(port: number, probed?: ServeProbe): Promise<ServeState> {
    const found = probed ?? (await this.deps.probe(base(port)));
    if (found.kind === "serve") {
      if (!samePath(found.health.vault, this.ctx.vaultRoot)) return this.set({ kind: "other_vault", port, vault: found.health.vault });
      const ours = this.ctx.startedPid();
      if (ours && ours !== found.health.pid) await this.ctx.saveStartedPid(0);
      this.activePort = port;
      return this.set({ kind: "running", port, pid: found.health.pid, started: false });
    }
    if (found.kind === "old") return this.set({ kind: "old", port });
    if (found.kind === "foreign") return this.set({ kind: "foreign", port });

    this.set({ kind: "starting", port });
    const spawned = this.deps.spawn(port);
    let exited = false;
    void spawned.exited.then(() => (exited = true));
    for (let waited = 0; waited < SERVE_START_TIMEOUT_MS; waited += SERVE_POLL_MS) {
      await this.deps.sleep(SERVE_POLL_MS);
      const up = await this.deps.probe(base(port));
      if (up.kind === "serve") {
        // Another vault's serve took the port while ours was starting.
        if (!samePath(up.health.vault, this.ctx.vaultRoot)) return this.set({ kind: "other_vault", port, vault: up.health.vault });
        await this.ctx.saveStartedPid(up.health.pid);
        this.activePort = port;
        return this.set({ kind: "running", port, pid: up.health.pid, started: true });
      }
      if (exited) break;
    }
    const tail = (await this.deps.logTail()).trim();
    const why = exited ? "it exited while starting" : `it did not answer within ${SERVE_START_TIMEOUT_MS / 1000} s`;
    return this.set({ kind: "failed", message: `artmind serve did not start on port ${port}: ${why}.${tail ? `\n${tail}` : ""}` });
  }
}

/** The words for a state or a stop; null when nothing needs saying. */
export function serveText(outcome: ServeState | ServeStop): string | null {
  switch (outcome.kind) {
    case "off":
      return null;
    case "starting":
      return "artmind serve is still starting.";
    case "running":
      return outcome.started ? `Started artmind serve on port ${outcome.port}.` : null;
    case "other_vault":
      return `Port ${outcome.port} is held by another vault's artmind serve${outcome.vault ? ` (${outcome.vault})` : ""}. Give this vault another serve port in settings; until then its queries run without serve.`;
    case "old":
      return `The artmind serve on port ${outcome.port} is too old to say which vault it serves, so it is not used. Restart it from its vault, or give this vault another serve port in settings.`;
    case "foreign":
      return `Something other than artmind serve is on port ${outcome.port}. Free the port, or change the serve port in settings.`;
    case "failed":
      return outcome.message;
    case "stopped":
      return "Stopped artmind serve.";
    case "not_running":
      return "artmind serve is not running.";
    case "not_ours":
      return `artmind serve (process ${outcome.pid}) was not started from Obsidian: restart it where it was started.`;
  }
}

/** The status bar's words: empty while off (the item is hidden). */
export function serveStatusText(state: ServeState): string {
  switch (state.kind) {
    case "off":
      return "";
    case "starting":
      return "serve …";
    case "running":
      return `serve :${state.port}`;
    default:
      return "serve ✕";
  }
}
