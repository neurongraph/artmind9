import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/** What one child process did. `spawnError` is set when it never started
 * (ENOENT: the path is wrong). */
export interface ProcessOutcome {
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  spawnError: string | null;
}

export interface SpawnOptions {
  cwd: string;
  timeoutMs: number;
  env: NodeJS.ProcessEnv;
  /** Writes run detached: if Obsidian quits mid-sync the child finishes on
   * its own (spec §8 -- `vault sync` only moves its bookmarks at the end). */
  detached: boolean;
}

export type Runner = (file: string, args: string[], options: SpawnOptions) => Promise<ProcessOutcome>;

/** One `artmind` run, as the rest of the plugin sees it. */
export interface CliResult {
  args: string[];
  exitCode: number | null;
  ok: boolean;
  /** stdout parsed as JSON; null when stdout is empty or not JSON. */
  json: unknown;
  stdout: string;
  stderr: string;
  /** The short reason on failure (rich-click's error box, unboxed), else null. */
  error: string | null;
  timedOut: boolean;
  startedAt: number;
  durationMs: number;
}

export const spawnRunner: Runner = (file, args, options) =>
  new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const child = spawn(file, args, {
      cwd: options.cwd,
      env: options.env,
      detached: options.detached,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
    }, options.timeoutMs);
    child.stdout?.setEncoding("utf8").on("data", (chunk: string) => (stdout += chunk));
    child.stderr?.setEncoding("utf8").on("data", (chunk: string) => (stderr += chunk));
    const finish = (code: number | null, spawnError: string | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code, stdout, stderr, timedOut, spawnError });
    };
    child.on("error", (error) => finish(null, error.message));
    child.on("close", (code) => finish(code, null));
  });

/** Per-command limits. A sync or projection of a big vault is slow; a
 * status read is not, and must never hold the status bar up for long. */
export const TIMEOUTS_MS: Record<string, number> = {
  "--version": 15_000,
  "vault status": 60_000,
  "vault sync": 60 * 60_000,
  "vault resolve": 5 * 60_000,
  "vault doctor": 60_000,
  "ingest pending": 2 * 60_000,
  "ingest async": 2 * 60_000,
  "ingest job-status": 30_000,
  "ingest job-results": 30_000,
  "ingest jobs-active": 30_000,
  "ingest table2graph": 60 * 60_000,
};
export const DEFAULT_TIMEOUT_MS = 5 * 60_000;

const WRITE_COMMANDS = new Set(["vault sync", "vault resolve", "ingest async", "ingest table2graph"]);

export function commandKey(args: string[]): string {
  return args[0] === "--version" ? "--version" : args.slice(0, 2).join(" ");
}

/** Writes go through one queue, one at a time (spec §7): sync, ingest
 * submission, table2graph and resolve. Their dry runs, and
 * `table2graph --pending`, only read. */
export function isWrite(args: string[]): boolean {
  const key = commandKey(args);
  if (!WRITE_COMMANDS.has(key) || args.includes("--dryRun")) return false;
  return !(key === "ingest table2graph" && args.includes("--pending"));
}

/** The message inside rich-click's error box (what `artmind` prints on
 * stderr when a command refuses), on one line. Falls back to a plain
 * `Error: ...` line, then to the last line of stderr. Log lines above the
 * box are ignored. */
export function errorText(stderr: string): string | null {
  const lines = stderr.split(/\r?\n/);
  let start = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].startsWith("╭─ Error")) {
      start = i;
      break;
    }
  }
  if (start >= 0) {
    const body: string[] = [];
    for (const line of lines.slice(start + 1)) {
      if (line.startsWith("╰")) break;
      body.push(line.replace(/^│\s?/, "").replace(/\s*│\s*$/, ""));
    }
    const text = body.join(" ").replace(/\s+/g, " ").trim();
    return text || null;
  }
  const trimmed = lines.map((line) => line.trim()).filter(Boolean);
  const plain = trimmed.filter((line) => line.startsWith("Error:")).pop();
  if (plain) return plain.slice("Error:".length).trim();
  return trimmed.pop() ?? null;
}

/** Dock-launched apps get a bare PATH (spec §7). artmind itself runs `git`
 * and `uv` (the worker converts documents with `uv run docling`), so the
 * usual install locations are appended. */
export function childEnv(base: NodeJS.ProcessEnv, home: string): NodeJS.ProcessEnv {
  const extra = [join(home, ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"];
  const parts = (base.PATH ?? "").split(":").filter(Boolean);
  for (const dir of extra) if (!parts.includes(dir)) parts.push(dir);
  return { ...base, PATH: parts.join(":"), NO_COLOR: "1" };
}

export interface DetectDeps {
  home: string;
  exists(path: string): boolean;
  /** `uv tool dir` (bin=false) or `uv tool dir --bin` (bin=true), or null. */
  uvToolDir(bin: boolean): Promise<string | null>;
}

export interface Detected {
  path: string | null;
  looked: string[];
}

/** Where `artmind` is: the setting when set (and only there), else
 * `~/.local/bin/artmind`, else `uv tool dir --bin`, else the tool's own
 * venv under `uv tool dir`. `looked` lists every place tried, for the
 * "artmind not found" panel. */
export async function detectArtmind(setting: string, deps: DetectDeps): Promise<Detected> {
  const looked: string[] = [];
  const configured = setting.trim();
  if (configured) {
    looked.push(configured);
    return { path: deps.exists(configured) ? configured : null, looked };
  }
  const candidates: Array<() => Promise<string | null>> = [
    async () => join(deps.home, ".local", "bin", "artmind"),
    async () => {
      const bin = await deps.uvToolDir(true);
      return bin ? join(bin, "artmind") : null;
    },
    async () => {
      const tools = await deps.uvToolDir(false);
      return tools ? join(tools, "artmind9", "bin", "artmind") : null;
    },
  ];
  for (const candidate of candidates) {
    const path = await candidate();
    if (!path || looked.includes(path)) continue;
    looked.push(path);
    if (deps.exists(path)) return { path, looked };
  }
  return { path: null, looked };
}

export function defaultDetectDeps(runner: Runner = spawnRunner): DetectDeps {
  const home = homedir();
  const env = childEnv(process.env, home);
  return {
    home,
    exists: existsSync,
    async uvToolDir(bin) {
      const outcome = await runner("uv", bin ? ["tool", "dir", "--bin"] : ["tool", "dir"], {
        cwd: home,
        timeoutMs: 10_000,
        env,
        detached: false,
      });
      const out = outcome.stdout.trim();
      return outcome.code === 0 && out ? out : null;
    },
  };
}

export interface ArtmindCliOptions {
  path: string;
  /** The vault root: every command anchors to the vault it runs in. */
  cwd: string;
  runner?: Runner;
  env?: NodeJS.ProcessEnv;
  timeouts?: Record<string, number>;
  /** Called with every result, for the Activity list. */
  onResult?: (result: CliResult) => void;
}

/** Runs `artmind <args> --compact` in the vault and parses its JSON. */
export class ArtmindCli {
  private writeTail: Promise<unknown> = Promise.resolve();
  private options: ArtmindCliOptions;

  constructor(options: ArtmindCliOptions) {
    this.options = options;
  }

  get path(): string {
    return this.options.path;
  }

  setPath(path: string): void {
    this.options.path = path;
  }

  /** `--compact` is added unless the args carry it, or it is `--version`.
   * Reads start at once; writes wait for every earlier write to finish. */
  run(args: string[], opts: { timeoutMs?: number } = {}): Promise<CliResult> {
    const full = args[0] === "--version" || args.includes("--compact") ? args : [...args, "--compact"];
    const timeoutMs =
      opts.timeoutMs ?? this.options.timeouts?.[commandKey(full)] ?? TIMEOUTS_MS[commandKey(full)] ?? DEFAULT_TIMEOUT_MS;
    const task = () => this.exec(full, timeoutMs);
    if (!isWrite(full)) return task();
    const result = this.writeTail.then(task, task);
    this.writeTail = result.catch(() => undefined);
    return result;
  }

  private async exec(args: string[], timeoutMs: number): Promise<CliResult> {
    const runner = this.options.runner ?? spawnRunner;
    const startedAt = Date.now();
    const outcome = await runner(this.options.path, args, {
      cwd: this.options.cwd,
      timeoutMs,
      env: this.options.env ?? childEnv(process.env, homedir()),
      detached: isWrite(args),
    });
    const stdout = outcome.stdout.trim();
    let json: unknown = null;
    let parseError: string | null = null;
    if (stdout && args[0] !== "--version") {
      try {
        json = JSON.parse(stdout);
      } catch {
        parseError = "artmind printed something that is not JSON";
      }
    }
    const ok = outcome.code === 0 && !outcome.timedOut && !outcome.spawnError && !parseError;
    let error: string | null = null;
    if (outcome.spawnError) error = `could not run ${this.options.path}: ${outcome.spawnError}`;
    else if (outcome.timedOut) error = `timed out after ${timeoutMs / 1000} s`;
    else if (outcome.code !== 0) error = errorText(outcome.stderr) ?? `exited with code ${outcome.code}`;
    else if (parseError) error = parseError;
    const result: CliResult = {
      args,
      exitCode: outcome.code,
      ok,
      json,
      stdout: outcome.stdout,
      stderr: outcome.stderr,
      error,
      timedOut: outcome.timedOut,
      startedAt,
      durationMs: Date.now() - startedAt,
    };
    this.options.onResult?.(result);
    return result;
  }

  version() { return this.run(["--version"]); }
  status() { return this.run(["vault", "status"]); }
  sync() { return this.run(["vault", "sync"]); }
  resolve(dryRun: boolean) { return this.run(dryRun ? ["vault", "resolve", "--dryRun"] : ["vault", "resolve"]); }
  doctor() { return this.run(["vault", "doctor"]); }
  ingestPending() { return this.run(["ingest", "pending"]); }
  ingestAsyncPending() { return this.run(["ingest", "async", "--pending"]); }
  jobStatus(jobId: string) { return this.run(["ingest", "job-status", jobId]); }
  jobResults(jobId: string) { return this.run(["ingest", "job-results", jobId]); }
  jobsActive() { return this.run(["ingest", "jobs-active"]); }
  tablesPending() { return this.run(["ingest", "table2graph", "--pending"]); }
  tableDryRun(table: string) { return this.run(["ingest", "table2graph", table, "--dryRun"]); }
  tableProject(table: string) { return this.run(["ingest", "table2graph", table]); }
}
