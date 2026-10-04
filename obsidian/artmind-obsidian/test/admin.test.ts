import { describe, expect, it } from "vitest";
import {
  type AdminDeps,
  type AdminHealth,
  AdminConsole,
  type Probe,
  START_POLL_MS,
  START_TIMEOUT_MS,
  adminOutcomeText,
  hostPort,
  promptPath,
  stopOutcomeText,
} from "../src/admin";

const VAULT = "/Users/me/vault";
const health = (over: Partial<AdminHealth> = {}): AdminHealth => ({ app: "admin-ui", vault: VAULT, version: "0.9.0", pid: 4242, ...over });

/** Probes answer from a script, one per call (the last repeats). */
function setup(probes: Probe[], opts: { exitCode?: number | null; startedPid?: number; log?: string } = {}) {
  const calls: string[] = [];
  const opened: string[] = [];
  const killed: number[] = [];
  let pid = opts.startedPid ?? 0;
  let i = 0;
  const deps: AdminDeps = {
    probe: async (base) => {
      calls.push(`probe ${base}`);
      return probes[Math.min(i++, probes.length - 1)];
    },
    spawn: (host, port) => {
      calls.push(`spawn ${host}:${port}`);
      return { pid: 4242, exited: opts.exitCode === undefined ? new Promise(() => {}) : Promise.resolve(opts.exitCode) };
    },
    open: (url) => opened.push(url),
    kill: (p) => (killed.push(p), true),
    logTail: async () => opts.log ?? "",
    sleep: async () => {},
  };
  const admin = new AdminConsole(deps, {
    url: () => "http://127.0.0.1:8379",
    vaultRoot: VAULT,
    startedPid: () => pid,
    saveStartedPid: async (p) => {
      pid = p;
    },
  });
  return { admin, calls, opened, killed, pid: () => pid };
}

describe("opening the admin console", () => {
  it("opens one already serving this vault, starting nothing", async () => {
    const t = setup([{ kind: "admin", health: health({ vault: `${VAULT}/` }) }]);

    expect(await t.admin.open("/dashboard")).toEqual({ kind: "opened", url: "http://127.0.0.1:8379/dashboard", started: false });
    expect(t.opened).toEqual(["http://127.0.0.1:8379/dashboard"]);
    expect(t.calls).toEqual(["probe http://127.0.0.1:8379"]);
  });

  it("starts one when nothing is on the port, waits for it, remembers its pid, then opens", async () => {
    const t = setup([{ kind: "down" }, { kind: "down" }, { kind: "admin", health: health({ pid: 777 }) }]);

    const outcome = await t.admin.open();

    expect(outcome).toEqual({ kind: "opened", url: "http://127.0.0.1:8379/", started: true });
    expect(t.calls.filter((c) => c.startsWith("spawn"))).toEqual(["spawn 127.0.0.1:8379"]);
    expect(t.pid()).toBe(777);
    expect(adminOutcomeText(outcome)).toBe("Started the admin console.");
  });

  it("never opens another vault's admin console", async () => {
    const t = setup([{ kind: "admin", health: health({ vault: "/Users/me/other" }) }]);

    const outcome = await t.admin.open();

    expect(outcome.kind).toBe("other_vault");
    expect(t.opened).toEqual([]);
    expect(adminOutcomeText(outcome)).toContain("serving another vault (/Users/me/other)");
  });

  it("starts nothing over something else on the port", async () => {
    const t = setup([{ kind: "foreign" }]);
    expect(await t.admin.open()).toEqual({ kind: "foreign", port: 8379 });
    expect(t.calls.some((c) => c.startsWith("spawn"))).toBe(false);
  });

  it("reports a start that exits, with the log's tail", async () => {
    const t = setup([{ kind: "down" }], { exitCode: 1, log: "Address already in use\n" });

    const outcome = await t.admin.open();

    expect(outcome).toEqual({ kind: "failed", message: "The admin console did not start: it exited while starting.\nAddress already in use" });
    expect(t.opened).toEqual([]);
  });

  it("gives up on a start that never answers", async () => {
    const t = setup([{ kind: "down" }]);
    const outcome = await t.admin.open();
    expect(outcome).toEqual({ kind: "failed", message: `The admin console did not start: it did not answer within ${START_TIMEOUT_MS / 1000} s.` });
    expect(t.calls.filter((c) => c.startsWith("probe"))).toHaveLength(1 + START_TIMEOUT_MS / START_POLL_MS);
  });

  it("says so when the URL setting is not a URL", async () => {
    const t = setup([{ kind: "down" }]);
    const admin = new AdminConsole(
      { probe: async () => ({ kind: "down" }), spawn: () => ({ pid: null, exited: Promise.resolve(0) }), open: () => {}, kill: () => true, logTail: async () => "", sleep: async () => {} },
      { url: () => "not a url", vaultRoot: VAULT, startedPid: () => 0, saveStartedPid: async () => {} },
    );
    expect((await admin.open()).kind).toBe("failed");
    expect(t.calls).toEqual([]);
  });
});

describe("stopping the admin console", () => {
  it("stops the one this plugin started, and forgets it", async () => {
    const t = setup([{ kind: "admin", health: health({ pid: 777 }) }], { startedPid: 777 });

    const outcome = await t.admin.stop();

    expect(outcome).toEqual({ kind: "stopped" });
    expect(t.killed).toEqual([777]);
    expect(t.pid()).toBe(0);
  });

  it("never stops one it did not start", async () => {
    const t = setup([{ kind: "admin", health: health({ pid: 999 }) }], { startedPid: 777 });

    const outcome = await t.admin.stop();

    expect(outcome).toEqual({ kind: "not_ours", pid: 999 });
    expect(t.killed).toEqual([]);
    expect(stopOutcomeText(outcome)).toContain("was not started from Obsidian");
  });

  it("forgets a pid whose console is gone", async () => {
    const t = setup([{ kind: "down" }], { startedPid: 777 });
    expect(await t.admin.stop()).toEqual({ kind: "not_running" });
    expect(t.pid()).toBe(0);
    expect(t.killed).toEqual([]);
  });
});

describe("hostPort", () => {
  it("reads host, port and base from the setting", () => {
    expect(hostPort("http://127.0.0.1:8379/")).toEqual({ host: "127.0.0.1", port: 8379, base: "http://127.0.0.1:8379" });
    expect(hostPort("http://localhost")).toEqual({ host: "localhost", port: 80, base: "http://localhost" });
  });
});

describe("promptPath (Plan 1, B7)", () => {
  it("prefills the admin console's chat through /?prompt=, encoded", () => {
    expect(promptPath("Review the proposed classifications for table team (domain general)")).toBe(
      "/?prompt=Review%20the%20proposed%20classifications%20for%20table%20team%20(domain%20general)",
    );
    expect(promptPath("a&b=c")).toBe("/?prompt=a%26b%3Dc");
  });
});
