import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { GIT_READS, GitReader } from "../src/git";

function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
}

/** A clone of a bare "GitHub" repo, as Obsidian Git leaves one. */
function repo(): { clone: string; remote: string } {
  const root = mkdtempSync(join(tmpdir(), "artmind-git-"));
  const remote = join(root, "remote.git");
  const clone = join(root, "clone");
  git(root, "init", "-q", "--bare", "-b", "main", remote);
  git(root, "clone", "-q", remote, clone);
  git(clone, "config", "user.email", "t@example.com");
  git(clone, "config", "user.name", "T");
  writeFileSync(join(clone, "note.md"), "hello\n");
  git(clone, "add", "-A");
  git(clone, "commit", "-qm", "first");
  git(clone, "push", "-q", "origin", "main");
  return { clone, remote };
}

describe("GitReader", () => {
  it("reads HEAD", async () => {
    const { clone } = repo();
    expect(await new GitReader(clone).head()).toBe(git(clone, "rev-parse", "HEAD"));
  });

  it("returns null for HEAD in a repo with no commits", async () => {
    const empty = mkdtempSync(join(tmpdir(), "artmind-git-empty-"));
    git(empty, "init", "-q");
    expect(await new GitReader(empty).head()).toBeNull();
  });

  it("counts commits not yet pushed", async () => {
    const { clone } = repo();
    const reader = new GitReader(clone);
    expect(await reader.ahead()).toBe(0);
    writeFileSync(join(clone, "b.md"), "b\n");
    git(clone, "add", "-A");
    git(clone, "commit", "-qm", "second");
    expect(await reader.ahead()).toBe(1);
  });

  it("has no count without an upstream", async () => {
    const empty = mkdtempSync(join(tmpdir(), "artmind-git-noup-"));
    git(empty, "init", "-q");
    expect(await new GitReader(empty).ahead()).toBeNull();
  });

  it("lists uncommitted changes under .artmind only, names with spaces whole", async () => {
    const { clone } = repo();
    mkdirSync(join(clone, ".artmind", "data"), { recursive: true });
    writeFileSync(join(clone, ".artmind", "data", "a record.json"), "{}");
    writeFileSync(join(clone, "outside.md"), "not artmind\n");

    expect(await new GitReader(clone).artmindChanges()).toEqual([".artmind/"]);
    git(clone, "add", ".artmind");
    expect(await new GitReader(clone).artmindChanges()).toEqual([".artmind/data/a record.json"]);
  });

  it("only ever reads (spec P5)", () => {
    const writes = ["commit", "push", "pull", "add", "merge", "checkout", "reset", "fetch", "update-ref"];
    for (const args of GIT_READS) expect(writes).not.toContain(args[0]);
  });
});
