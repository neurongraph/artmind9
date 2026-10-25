/** The real world behind `ServeManager`: Obsidian's `requestUrl` and a
 * detached `artmind serve`. Thin on purpose; the decisions live in
 * serve.ts, where they are tested. */
import { spawn } from "node:child_process";
import { closeSync, mkdirSync, openSync, promises as fs } from "node:fs";
import { createServer } from "node:net";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { requestUrl } from "obsidian";
import { childEnv } from "./cli";
import type { ServeDeps, ServeHealth, ServeProbe } from "./serve";

export function serveLogPath(vaultRoot: string): string {
  return join(vaultRoot, ".artmind", "logs", "serve.log");
}

/** `/health` as `ServeManager` reads it. An artmind serve without `pid` and
 * `vault` predates them and cannot be told apart from another vault's. */
export function classifyServeHealth(status: number, body: unknown): ServeProbe {
  const b = (body && typeof body === "object" ? body : {}) as Partial<ServeHealth>;
  if (status !== 200 || b.service !== "artmind") return { kind: "foreign" };
  if (typeof b.pid !== "number" || !("vault" in b)) return { kind: "old" };
  return { kind: "serve", health: { service: "artmind", version: b.version ?? null, vault: b.vault ?? null, pid: b.pid } };
}

export function realServeDeps(vaultRoot: string, artmindPath: () => string): ServeDeps {
  const log = serveLogPath(vaultRoot);
  return {
    async probe(base: string): Promise<ServeProbe> {
      try {
        const response = await requestUrl({ url: `${base}/health`, throw: false });
        let body: unknown = null;
        try {
          body = response.json;
        } catch {
          body = null;
        }
        return classifyServeHealth(response.status, body);
      } catch {
        return { kind: "down" };
      }
    },
    isFree(port: number): Promise<boolean> {
      return new Promise((resolve) => {
        const server = createServer();
        server.once("error", () => resolve(false));
        server.listen(port, "127.0.0.1", () => server.close(() => resolve(true)));
      });
    },
    spawn(port: number) {
      mkdirSync(dirname(log), { recursive: true });
      const fd = openSync(log, "a");
      // Detached and unref'd, so Obsidian never waits on it; the vault root
      // as cwd, since serve finds its vault from where it runs.
      const child = spawn(artmindPath(), ["serve", "--port", String(port)], {
        cwd: vaultRoot,
        detached: true,
        stdio: ["ignore", fd, fd],
        env: { ...childEnv(process.env, homedir()), ARTMIND_SERVE_PORT: String(port) },
      });
      closeSync(fd);
      const exited = new Promise<number | null>((resolve) => {
        child.once("exit", (code) => resolve(code));
        child.once("error", () => resolve(null));
      });
      child.unref();
      return { pid: child.pid ?? null, exited };
    },
    kill(pid: number) {
      try {
        process.kill(pid, "SIGTERM");
        return true;
      } catch {
        return false;
      }
    },
    async logTail() {
      try {
        return (await fs.readFile(log, "utf8")).trimEnd().split(/\r?\n/).slice(-8).join("\n");
      } catch {
        return "";
      }
    },
    sleep: (ms: number) => new Promise((resolve) => setTimeout(resolve, ms)),
  };
}
