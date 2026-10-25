import { describe, expect, it } from "vitest";
import { ArtmindCli, type Runner } from "../src/cli";
import {
  AUTO_PORT_MIN,
  AUTO_PORT_SPAN,
  AUTO_PORT_TRIES,
  SERVE_POLL_MS,
  SERVE_START_TIMEOUT_MS,
  type ServeDeps,
  type ServeHealth,
  ServeManager,
  type ServeProbe,
  autoPorts,
  serveStatusText,
  serveText,
} from "../src/serve";
import { classifyServeHealth, serveLogPath } from "../src/serveDeps";

const VAULT = "/Users/me/vault";
const serve = (over: Partial<ServeHealth> = {}): ServeProbe => ({
  kind: "serve",
  health: { service: "artmind", version: "0.9.0", vault: VAULT, pid: 4242, ...over },
});

/** Probes answer from a script, one per call (the last repeats). */
function setup(probes: ServeProbe[], opts: { enabled?: boolean; port?: number; busyPorts?: number[]; afterSpawn?: ServeProbe; startedPid?: number; exitCode?: number | null; log?: string } = {}) {
  const calls: string[] = [];
  const killed: number[] = [];
  let pid = opts.startedPid ?? 0;
  let port = opts.port ?? 8377;
  let enabled = opts.enabled ?? true;
  let i = 0;
  const deps: ServeDeps = {
    probe: async (base) => {
      calls.push(`probe ${base}`);
      if (opts.afterSpawn && calls.some((c) => c.startsWith("spawn"))) return opts.afterSpawn;
      return probes[Math.min(i++, probes.length - 1)];
    },
    isFree: async (p) => !(opts.busyPorts ?? []).includes(p),
    spawn: (p) => {
      calls.push(`spawn ${p}`);
      return { pid: 4242, exited: opts.exitCode === undefined ? new Promise(() => {}) : Promise.resolve(opts.exitCode) };
    },
    kill: (p) => (killed.push(p), true),
    logTail: async () => opts.log ?? "",
    sleep: async () => {},
  };
  const manager = new ServeManager(deps, {
    enabled: () => enabled,
    port: () => port,
    vaultRoot: VAULT,
    startedPid: () => pid,
    saveStartedPid: async (p) => {
      pid = p;
    },
  });
  return {
    manager,
    calls,
    killed,
    pid: () => pid,
    setPort: (p: number) => (port = p),
    setEnabled: (on: boolean) => (enabled = on),
  };
}

describe("serve: reuse or start", () => {
  it("does nothing at all while the setting is off", async () => {
    const t = setup([{ kind: "down" }], { enabled: false });

    expect(await t.manager.ensure()).toEqual({ kind: "off" });
    expect(t.calls).toEqual([]);
    expect(t.manager.env()).toEqual({});
  });

  it("reuses a serve already serving this vault, starting nothing and claiming nothing", async () => {
    const t = setup([serve({ vault: `${VAULT}/`, pid: 900 })]);

    expect(await t.manager.ensure()).toEqual({ kind: "running", port: 8377, pid: 900, started: false });
    expect(t.calls).toEqual(["probe http://127.0.0.1:8377"]);
    expect(t.pid()).toBe(0);
    expect(t.manager.env()).toEqual({ ARTMIND_SERVE_PORT: "8377" });
  });

  it("forgets a remembered pid that is no longer the serve on the port", async () => {
    const t = setup([serve({ pid: 900 })], { startedPid: 555 });

    await t.manager.ensure();

    expect(t.pid()).toBe(0);
  });

  it("starts one when the port is free, from the configured port, and remembers its pid", async () => {
    const t = setup([{ kind: "down" }, { kind: "down" }, serve({ pid: 777 })], { port: 8390 });
    const seen: string[] = [];
    t.manager.onChange((s) => seen.push(s.kind));

    const state = await t.manager.ensure();

    expect(state).toEqual({ kind: "running", port: 8390, pid: 777, started: true });
    expect(t.calls.filter((c) => c.startsWith("spawn"))).toEqual(["spawn 8390"]);
    expect(t.calls[0]).toBe("probe http://127.0.0.1:8390");
    expect(t.pid()).toBe(777);
    expect(seen).toEqual(["starting", "running"]);
    expect(serveText(state)).toBe("Started artmind serve on port 8390.");
  });

  it("never reuses another vault's serve, and stops proxying to it", async () => {
    const t = setup([serve({ vault: "/Users/me/other" })]);

    const state = await t.manager.ensure();

    expect(state).toEqual({ kind: "other_vault", port: 8377, vault: "/Users/me/other" });
    expect(t.calls.some((c) => c.startsWith("spawn"))).toBe(false);
    expect(t.manager.env()).toEqual({ ARTMIND_SERVE_PORT: "8377", ARTMIND_NO_PROXY: "1" });
    expect(serveText(state)).toContain("held by another vault's artmind serve (/Users/me/other)");
  });

  it("treats a serve too old to name its vault as not this vault's", async () => {
    const t = setup([{ kind: "old" }]);

    expect(await t.manager.ensure()).toEqual({ kind: "old", port: 8377 });
    expect(t.calls.some((c) => c.startsWith("spawn"))).toBe(false);
    expect(t.manager.env().ARTMIND_NO_PROXY).toBe("1");
  });

  it("starts nothing over something else on the port", async () => {
    const t = setup([{ kind: "foreign" }]);

    const state = await t.manager.ensure();

    expect(state).toEqual({ kind: "foreign", port: 8377 });
    expect(t.calls.some((c) => c.startsWith("spawn"))).toBe(false);
    expect(serveStatusText(state)).toBe("serve ✕");
  });

  it("reports a start that exits, with the log's last lines", async () => {
    const t = setup([{ kind: "down" }], { exitCode: 1, log: "Error: address already in use\n" });

    const state = await t.manager.ensure();

    expect(state.kind).toBe("failed");
    expect(serveText(state)).toBe("artmind serve did not start on port 8377: it exited while starting.\nError: address already in use");
    expect(t.pid()).toBe(0);
  });

  it("gives up on a start that never answers", async () => {
    const t = setup([{ kind: "down" }]);

    const state = await t.manager.ensure();

    expect(serveText(state)).toContain(`did not answer within ${SERVE_START_TIMEOUT_MS / 1000} s`);
    expect(t.calls.filter((c) => c.startsWith("probe"))).toHaveLength(1 + SERVE_START_TIMEOUT_MS / SERVE_POLL_MS);
  });

  it("starts at most one at a time", async () => {
    const t = setup([{ kind: "down" }, serve({ pid: 777 })]);

    const [a, b] = await Promise.all([t.manager.ensure(), t.manager.ensure()]);

    expect(t.calls.filter((c) => c.startsWith("spawn"))).toHaveLength(1);
    expect(a.kind).toBe("running");
    expect(b.kind).toBe("off");
  });
});

describe("serve: automatic port", () => {
  const ports = autoPorts(VAULT);

  it("picks the same ports for a vault every time, outside 83xx", () => {
    expect(autoPorts(`${VAULT}/`)).toEqual(ports);
    expect(ports).toHaveLength(AUTO_PORT_TRIES);
    expect(new Set(ports).size).toBe(AUTO_PORT_TRIES);
    for (const p of ports) {
      expect(p).toBeGreaterThanOrEqual(AUTO_PORT_MIN);
      expect(p).toBeLessThan(AUTO_PORT_MIN + AUTO_PORT_SPAN);
    }
    expect(autoPorts("/Users/me/other")).not.toEqual(ports);
  });

  it("starts on the vault's first port when it is free, and runs artmind with it", async () => {
    const t = setup([{ kind: "down" }], { port: 0, afterSpawn: serve({ pid: 777 }) });
    expect(t.manager.env()).toEqual({ ARTMIND_SERVE_PORT: String(ports[0]) });

    const state = await t.manager.ensure();

    expect(state).toEqual({ kind: "running", port: ports[0], pid: 777, started: true });
    expect(t.calls.filter((c) => c.startsWith("spawn"))).toEqual([`spawn ${ports[0]}`]);
    expect(t.manager.env()).toEqual({ ARTMIND_SERVE_PORT: String(ports[0]) });
  });

  it("reuses this vault's serve found on its first port", async () => {
    const t = setup([serve({ pid: 900 })], { port: 0 });

    expect(await t.manager.ensure()).toEqual({ kind: "running", port: ports[0], pid: 900, started: false });
    expect(t.calls.some((c) => c.startsWith("spawn"))).toBe(false);
  });

  it("skips another vault's serve, anything foreign, and a port something else listens on", async () => {
    const t = setup([serve({ vault: "/Users/me/other" }), { kind: "foreign" }, { kind: "down" }], {
      port: 0,
      busyPorts: [ports[2]],
      afterSpawn: serve({ pid: 777 }),
    });

    const state = await t.manager.ensure();

    expect(state).toEqual({ kind: "running", port: ports[3], pid: 777, started: true });
    expect(t.calls.filter((c) => c.startsWith("spawn"))).toEqual([`spawn ${ports[3]}`]);
    expect(t.manager.env()).toEqual({ ARTMIND_SERVE_PORT: String(ports[3]) });
  });

  it("finds this vault's serve on a later port after a reload, though an earlier port has since freed up", async () => {
    const t = setup([{ kind: "down" }, serve({ pid: 777 })], { port: 0 });

    expect(await t.manager.ensure()).toEqual({ kind: "running", port: ports[1], pid: 777, started: false });
    expect(t.calls.some((c) => c.startsWith("spawn"))).toBe(false);
  });

  it("fails, saying so, when every port it tries is taken", async () => {
    const t = setup([{ kind: "foreign" }], { port: 0 });

    const state = await t.manager.ensure();

    expect(state.kind).toBe("failed");
    expect(serveText(state)).toContain(`tried ${AUTO_PORT_TRIES} from ${ports[0]}`);
    expect(t.calls.some((c) => c.startsWith("spawn"))).toBe(false);
  });

  it("stops ours on the port it fell back to", async () => {
    const t = setup([{ kind: "down" }], { port: 0, busyPorts: [ports[0]], afterSpawn: serve({ pid: 777 }) });
    await t.manager.ensure();
    expect(t.manager.state).toMatchObject({ kind: "running", port: ports[1], started: true });

    expect(await t.manager.stop()).toEqual({ kind: "stopped" });
    expect(t.killed).toEqual([777]);
  });

  it("stops the serve it started after a reload, found on its automatic port", async () => {
    const t = setup([serve({ pid: 777 })], { port: 0, startedPid: 777 });

    expect(await t.manager.stop()).toEqual({ kind: "stopped" });
    expect(t.killed).toEqual([777]);
  });

  it("keeps an explicit port in settings exactly as before", async () => {
    const t = setup([{ kind: "down" }, { kind: "down" }, serve({ pid: 777 })], { port: 8390 });

    expect((await t.manager.ensure()).kind).toBe("running");
    expect(t.calls.filter((c) => c.startsWith("spawn"))).toEqual(["spawn 8390"]);
  });
});

describe("serve: stop only our own", () => {
  it("stops the serve it started", async () => {
    const t = setup([serve({ pid: 777 })], { startedPid: 777 });

    expect(await t.manager.stop()).toEqual({ kind: "stopped" });
    expect(t.killed).toEqual([777]);
    expect(t.pid()).toBe(0);
    expect(t.manager.state).toEqual({ kind: "off" });
  });

  it("never stops one started elsewhere", async () => {
    const t = setup([serve({ pid: 900 })], { startedPid: 0 });

    const outcome = await t.manager.stop();

    expect(outcome).toEqual({ kind: "not_ours", pid: 900, vault: VAULT });
    expect(t.killed).toEqual([]);
    expect(serveText(outcome)).toContain("was not started from Obsidian");
  });

  it("never stops another serve that reused a remembered pid's port", async () => {
    const t = setup([serve({ pid: 900 })], { startedPid: 777 });

    expect((await t.manager.stop()).kind).toBe("not_ours");
    expect(t.killed).toEqual([]);
  });

  it("forgets its pid when nothing is on the port any more", async () => {
    const t = setup([{ kind: "down" }], { startedPid: 777 });

    expect(await t.manager.stop()).toEqual({ kind: "not_running" });
    expect(t.killed).toEqual([]);
    expect(t.pid()).toBe(0);
  });

  it("stops ours on the port it started on, after the port setting changed", async () => {
    const t = setup([{ kind: "down" }, serve({ pid: 777 })], { port: 8377 });
    await t.manager.ensure();
    t.setPort(8390);

    await t.manager.stop();

    expect(t.calls.at(-1)).toBe("probe http://127.0.0.1:8377");
    expect(t.killed).toEqual([777]);
  });
});

describe("serve: restart", () => {
  it("stops ours, waits for the port to free, then starts a fresh one", async () => {
    const t = setup([serve({ pid: 777 }), serve({ pid: 777 }), { kind: "down" }, { kind: "down" }, serve({ pid: 778 })], { startedPid: 777 });

    const outcome = await t.manager.restart();

    expect(t.killed).toEqual([777]);
    expect(outcome).toEqual({ kind: "running", port: 8377, pid: 778, started: true });
    expect(t.pid()).toBe(778);
  });

  it("refuses to restart this vault's serve started elsewhere", async () => {
    const t = setup([serve({ pid: 900 })]);

    expect((await t.manager.restart()).kind).toBe("not_ours");
    expect(t.killed).toEqual([]);
    expect(t.calls.some((c) => c.startsWith("spawn"))).toBe(false);
  });

  it("says another vault holds the port rather than refusing", async () => {
    const t = setup([serve({ vault: "/Users/me/other", pid: 900 })]);

    expect((await t.manager.restart()).kind).toBe("other_vault");
    expect(t.killed).toEqual([]);
  });

  it("starts one when none is running", async () => {
    const t = setup([{ kind: "down" }, { kind: "down" }, serve({ pid: 777 })]);

    expect((await t.manager.restart()).kind).toBe("running");
    expect(t.calls.filter((c) => c.startsWith("spawn"))).toEqual(["spawn 8377"]);
  });

  it("does nothing while the setting is off", async () => {
    const t = setup([serve({ pid: 777 })], { startedPid: 777, enabled: false });

    expect(await t.manager.restart()).toEqual({ kind: "off" });
    expect(t.calls).toEqual([]);
  });
});

describe("serve's /health", () => {
  it("is this vault's serve only with a pid and a vault field", () => {
    expect(classifyServeHealth(200, { service: "artmind", status: "ok", version: "1", vault: VAULT, pid: 12 })).toEqual({
      kind: "serve",
      health: { service: "artmind", version: "1", vault: VAULT, pid: 12 },
    });
    expect(classifyServeHealth(200, { service: "artmind", status: "ok", vault: null, pid: 12 })).toMatchObject({ kind: "serve" });
    expect(classifyServeHealth(200, { service: "artmind", status: "ok", version: "1" })).toEqual({ kind: "old" });
    expect(classifyServeHealth(200, { app: "admin-ui" })).toEqual({ kind: "foreign" });
    expect(classifyServeHealth(404, { service: "artmind", vault: VAULT, pid: 1 })).toEqual({ kind: "foreign" });
    expect(classifyServeHealth(200, null)).toEqual({ kind: "foreign" });
  });

  it("logs to the vault's .artmind/logs", () => {
    expect(serveLogPath(VAULT)).toBe(`${VAULT}/.artmind/logs/serve.log`);
  });
});

describe("serve's status bar", () => {
  it("is hidden while off and names the port while running", () => {
    expect(serveStatusText({ kind: "off" })).toBe("");
    expect(serveStatusText({ kind: "starting", port: 8377 })).toBe("serve …");
    expect(serveStatusText({ kind: "running", port: 8390, pid: 1, started: false })).toBe("serve :8390");
    expect(serveStatusText({ kind: "failed", message: "x" })).toBe("serve ✕");
  });
});

describe("the plugin's own artmind runs", () => {
  it("carry serve's env, read at each run", async () => {
    const seen: Array<string | undefined> = [];
    const runner: Runner = async (_file, _args, options) => {
      seen.push(options.env.ARTMIND_SERVE_PORT);
      return { code: 0, stdout: "{}", stderr: "", timedOut: false, spawnError: null };
    };
    let port = "8390";
    const cli = new ArtmindCli({ path: "artmind", cwd: VAULT, runner, env: { PATH: "/bin" }, extraEnv: () => ({ ARTMIND_SERVE_PORT: port }) });

    await cli.status();
    port = "8391";
    await cli.status();

    expect(seen).toEqual(["8390", "8391"]);
  });
});
