/** The real world behind `AdminConsole`: Obsidian's `requestUrl` (no CORS),
 * a detached `artmind admin-ui`, Electron's shell. Thin on purpose; the
 * decisions live in admin.ts, where they are tested. */
import { spawn } from "node:child_process";
import { closeSync, mkdirSync, openSync, promises as fs } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { requestUrl } from "obsidian";
import type { AdminDeps, AdminHealth, Probe } from "./admin";
import { childEnv } from "./cli";

export function adminLogPath(vaultRoot: string): string {
  return join(vaultRoot, ".artmind", "logs", "admin-ui.log");
}

/** The system browser. Desktop only (isDesktopOnly): Electron's shell is always there. */
export function openInBrowser(url: string): void {
  const { shell } = require("electron") as { shell: { openExternal(url: string): Promise<void> } };
  void shell.openExternal(url);
}

/** `open` decides where a page goes (an Obsidian tab or the browser);
 * the browser when none is given. */
export function realAdminDeps(vaultRoot: string, artmindPath: () => string, open: (url: string) => void = openInBrowser): AdminDeps {
  const log = adminLogPath(vaultRoot);
  return {
    async probe(base: string): Promise<Probe> {
      try {
        const response = await requestUrl({ url: `${base}/api/health`, throw: false });
        let body: Partial<AdminHealth> | null = null;
        try {
          body = response.json as Partial<AdminHealth>;
        } catch {
          body = null;
        }
        if (response.status === 200 && body?.app === "admin-ui" && typeof body.pid === "number") {
          return { kind: "admin", health: body as AdminHealth };
        }
        return { kind: "foreign" };
      } catch {
        return { kind: "down" };
      }
    },
    spawn(host: string, port: number) {
      mkdirSync(dirname(log), { recursive: true });
      const fd = openSync(log, "a");
      // Detached and unref'd: the admin console outlives Obsidian, like one
      // started in a terminal (its chat sessions survive a restart).
      const child = spawn(artmindPath(), ["admin-ui", "--host", host, "--port", String(port)], {
        cwd: vaultRoot,
        detached: true,
        stdio: ["ignore", fd, fd],
        env: childEnv(process.env, homedir()),
      });
      closeSync(fd);
      const exited = new Promise<number | null>((resolve) => {
        child.once("exit", (code) => resolve(code));
        child.once("error", () => resolve(null));
      });
      child.unref();
      return { pid: child.pid ?? null, exited };
    },
    open,
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
