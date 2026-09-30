import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import {
  ArtmindCli,
  type DetectDeps,
  childEnv,
  commandKey,
  detectArtmind,
  errorText,
  isWrite,
} from "../src/cli";
import { fixture } from "./fixtures";

const FAKE = fileURLToPath(new URL("./fake-artmind.mjs", import.meta.url));

interface Rule {
  match: string;
  stdout?: string;
  stderr?: string;
  exit?: number;
  delayMs?: number;
}

/** An ArtmindCli running the fake script with `rules`, logging to a file. */
function fakeCli(rules: Rule[], timeouts?: Record<string, number>) {
  const dir = mkdtempSync(join(tmpdir(), "artmind-cli-"));
  const rulesFile = join(dir, "rules.json");
  const log = join(dir, "log.txt");
  writeFileSync(rulesFile, JSON.stringify(rules));
  writeFileSync(log, "");
  const cli = new ArtmindCli({
    path: FAKE,
    cwd: dir,
    timeouts,
    env: { ...process.env, FAKE_ARTMIND_RULES: rulesFile, FAKE_ARTMIND_LOG: log },
  });
  return { cli, lines: () => readFileSync(log, "utf8").trim().split("\n").filter(Boolean) };
}

beforeAll(() => chmodSync(FAKE, 0o755));

describe("ArtmindCli.run", () => {
  it("adds --compact and parses the JSON", async () => {
    const status = fixture("vault-status.in-sync");
    const { cli, lines } = fakeCli([{ match: "vault status --compact", stdout: JSON.stringify(status.json) }]);

    const result = await cli.status();

    expect(result.ok).toBe(true);
    expect(result.args).toEqual(["vault", "status", "--compact"]);
    expect(result.json).toEqual(status.json);
    expect(result.error).toBeNull();
    expect(lines()).toEqual(["start vault status --compact", "end vault status --compact"]);
  });

  it("runs --version without --compact and keeps its text", async () => {
    const { cli } = fakeCli([{ match: "--version", stdout: "artmind 0.9.0\n" }]);

    const result = await cli.version();

    expect(result.args).toEqual(["--version"]);
    expect(result.ok).toBe(true);
    expect(result.stdout).toBe("artmind 0.9.0\n");
    expect(result.json).toBeNull();
  });

  it("turns a refusal's error box into one line", async () => {
    const refusal = fixture("vault-sync.refusal-worker-running");
    const { cli } = fakeCli([
      { match: "vault sync", stderr: `2026-09-30 12:00:00 [INFO   ] starting\n${refusal.stderr}\n`, exit: 1 },
    ]);

    const result = await cli.sync();

    expect(result.ok).toBe(false);
    expect(result.exitCode).toBe(1);
    expect(result.error).toBe(
      "the ingest worker is running for this vault -- wait for it to finish (`artmind ingest job-status`), then re-run `vault sync`",
    );
  });

  it("keeps the JSON of a command that exits 1 with a report (vault doctor)", async () => {
    const doctor = fixture("vault-doctor");
    const { cli } = fakeCli([{ match: "vault doctor", stdout: JSON.stringify(doctor.json), exit: doctor.exit_code }]);

    const result = await cli.doctor();

    expect(result.ok).toBe(false);
    expect(result.json).toEqual(doctor.json);
  });

  it("times out, kills the child and says so", async () => {
    const { cli } = fakeCli([{ match: "vault status", stdout: "{}", delayMs: 5_000 }], { "vault status": 300 });

    const result = await cli.status();

    expect(result.timedOut).toBe(true);
    expect(result.ok).toBe(false);
    expect(result.error).toBe("timed out after 0.3 s");
  });

  it("reports a path that does not exist", async () => {
    const cli = new ArtmindCli({ path: "/nonexistent/artmind", cwd: tmpdir() });

    const result = await cli.status();

    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/^could not run \/nonexistent\/artmind: /);
  });

  it("flags output that is not JSON", async () => {
    const { cli } = fakeCli([{ match: "ingest pending", stdout: "not json" }]);

    const result = await cli.ingestPending();

    expect(result.ok).toBe(false);
    expect(result.error).toBe("artmind printed something that is not JSON");
  });

  it("runs writes one at a time, in the order asked", async () => {
    const { cli, lines } = fakeCli([
      { match: "vault sync", stdout: "{}", delayMs: 400 },
      { match: "ingest async", stdout: '{"job_id": null, "file_count": 0}' },
      { match: "ingest table2graph team --compact", stdout: '{"tables": []}' },
    ]);

    await Promise.all([cli.sync(), cli.ingestAsyncPending(), cli.tableProject("team")]);

    expect(lines()).toEqual([
      "start vault sync --compact",
      "end vault sync --compact",
      "start ingest async --pending --compact",
      "end ingest async --pending --compact",
      "start ingest table2graph team --compact",
      "end ingest table2graph team --compact",
    ]);
  });

  it("keeps the queue moving after a failed write", async () => {
    const { cli, lines } = fakeCli([
      { match: "vault sync", stderr: "╭─ Error ─╮\n│ boom │\n╰─╯", exit: 1, delayMs: 100 },
      { match: "vault resolve", stdout: "{}" },
    ]);

    const [sync, resolve] = await Promise.all([cli.sync(), cli.resolve(false)]);

    expect(sync.error).toBe("boom");
    expect(resolve.ok).toBe(true);
    expect(lines()).toEqual([
      "start vault sync --compact",
      "end vault sync --compact",
      "start vault resolve --compact",
      "end vault resolve --compact",
    ]);
  });

  it("runs reads concurrently, even while a write runs", async () => {
    const { cli, lines } = fakeCli([
      { match: "vault sync", stdout: "{}", delayMs: 600 },
      { match: "vault status", stdout: "{}", delayMs: 200 },
      { match: "ingest pending", stdout: "{}", delayMs: 200 },
    ]);

    await Promise.all([cli.sync(), cli.status(), cli.ingestPending()]);

    const log = lines();
    const ends = log.filter((l) => l.startsWith("end"));
    expect(log.slice(0, 3).every((l) => l.startsWith("start"))).toBe(true);
    expect(ends[ends.length - 1]).toBe("end vault sync --compact");
  });
});

describe("which commands write", () => {
  it.each([
    [["vault", "sync", "--compact"], true],
    [["vault", "resolve", "--compact"], true],
    [["vault", "resolve", "--dryRun", "--compact"], false],
    [["ingest", "async", "--pending", "--compact"], true],
    [["ingest", "table2graph", "team", "--compact"], true],
    [["ingest", "table2graph", "team", "--dryRun", "--compact"], false],
    [["ingest", "table2graph", "--pending", "--compact"], false],
    [["vault", "status", "--compact"], false],
    [["ingest", "pending", "--compact"], false],
    [["--version"], false],
  ])("%j -> %s", (args, write) => {
    expect(isWrite(args as string[])).toBe(write);
  });

  it("keys timeouts by the first two words", () => {
    expect(commandKey(["ingest", "job-status", "abc", "--compact"])).toBe("ingest job-status");
    expect(commandKey(["--version"])).toBe("--version");
  });
});

describe("errorText", () => {
  it.each([
    "vault-sync.refusal-worker-running",
    "vault-sync.refusal-merge-in-progress",
    "vault-sync.refusal-artmind-conflicts",
    "vault-sync.refusal-bookmark-unpulled",
    "vault-sync.refusal-bookmark-not-ancestor",
    "vault-sync.refusal-no-bookmark",
    "table2graph.dry-run-no-mapping",
  ])("unboxes %s into one line with no box characters", (name) => {
    const text = errorText(fixture(name).stderr!)!;
    expect(text).not.toMatch(/[│╭╰─]{2}|\n/);
    expect(text.length).toBeGreaterThan(20);
  });

  it("falls back to a plain Error: line, then the last line", () => {
    expect(errorText("log line\nError: plain click error\n")).toBe("plain click error");
    expect(errorText("Traceback...\nValueError: bad\n")).toBe("ValueError: bad");
    expect(errorText("")).toBeNull();
  });
});

describe("childEnv", () => {
  it("appends the usual install locations to a Dock app's bare PATH", () => {
    const env = childEnv({ PATH: "/usr/bin:/bin" }, "/Users/me");
    expect(env.PATH!.split(":")).toEqual([
      "/usr/bin",
      "/bin",
      "/Users/me/.local/bin",
      "/opt/homebrew/bin",
      "/usr/local/bin",
    ]);
    expect(env.NO_COLOR).toBe("1");
  });
});

describe("detectArtmind", () => {
  function deps(existing: string[], toolDir: string | null = null, toolBin: string | null = null): DetectDeps {
    return {
      home: "/Users/me",
      exists: (p) => existing.includes(p),
      uvToolDir: async (bin) => (bin ? toolBin : toolDir),
    };
  }

  it("uses the setting, and only the setting, when it is set", async () => {
    expect(await detectArtmind("/opt/artmind", deps(["/opt/artmind"]))).toEqual({
      path: "/opt/artmind",
      looked: ["/opt/artmind"],
    });
    expect(await detectArtmind("/opt/artmind", deps(["/Users/me/.local/bin/artmind"]))).toEqual({
      path: null,
      looked: ["/opt/artmind"],
    });
  });

  it("finds ~/.local/bin/artmind first", async () => {
    const found = await detectArtmind("", deps(["/Users/me/.local/bin/artmind"]));
    expect(found.path).toBe("/Users/me/.local/bin/artmind");
  });

  it("falls back to `uv tool dir --bin`, then the tool's own venv", async () => {
    expect((await detectArtmind("", deps(["/custom/bin/artmind"], null, "/custom/bin"))).path).toBe("/custom/bin/artmind");
    const venv = "/Users/me/.local/share/uv/tools/artmind9/bin/artmind";
    expect((await detectArtmind("", deps([venv], "/Users/me/.local/share/uv/tools"))).path).toBe(venv);
  });

  it("lists every place it looked when nothing is there", async () => {
    const found = await detectArtmind("", deps([], "/tools", "/Users/me/.local/bin"));
    expect(found).toEqual({ path: null, looked: ["/Users/me/.local/bin/artmind", "/tools/artmind9/bin/artmind"] });
  });
});
