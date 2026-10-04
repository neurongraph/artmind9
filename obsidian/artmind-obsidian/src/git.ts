import { homedir } from "node:os";
import { childEnv, type Runner, spawnRunner } from "./cli";

/** The only git the plugin runs, and all of it reads (spec P5): HEAD, the
 * commits not yet pushed, and uncommitted changes under `.artmind/`.
 * Writing to git is Obsidian Git's job (`ObsidianGitBridge`). */
export const GIT_READS = [
  ["rev-parse", "HEAD"],
  ["rev-list", "--count", "@{upstream}..HEAD"],
  // Every untracked file, not its folder: the footer counts docs.
  ["status", "--porcelain", "-z", "--untracked-files=all", "--", ".artmind"],
] as const;

export class GitReader {
  private cwd: string;
  private runner: Runner;
  private env: NodeJS.ProcessEnv;

  constructor(cwd: string, runner: Runner = spawnRunner, env: NodeJS.ProcessEnv = childEnv(process.env, homedir())) {
    this.cwd = cwd;
    this.runner = runner;
    this.env = env;
  }

  private async git(args: readonly string[]): Promise<string | null> {
    const outcome = await this.runner("git", [...args], {
      cwd: this.cwd,
      timeoutMs: 15_000,
      env: this.env,
      detached: false,
    });
    return outcome.code === 0 ? outcome.stdout : null;
  }

  /** `git rev-parse HEAD`, or null (no commits, not a repo). */
  async head(): Promise<string | null> {
    const out = await this.git(GIT_READS[0]);
    return out ? out.trim() : null;
  }

  /** Local commits not yet pushed, or null when there is no upstream. */
  async ahead(): Promise<number | null> {
    const out = await this.git(GIT_READS[1]);
    if (out === null) return null;
    const n = Number.parseInt(out.trim(), 10);
    return Number.isNaN(n) ? null : n;
  }

  /** Paths under `.artmind/` with uncommitted changes (`-z`: a name with a
   * space stays whole; a rename's second, original path is skipped). */
  async artmindChanges(): Promise<string[]> {
    const out = await this.git(GIT_READS[2]);
    if (!out) return [];
    const fields = out.split("\0");
    const paths: string[] = [];
    for (let i = 0; i < fields.length; i++) {
      const field = fields[i];
      if (field.length < 4) continue;
      paths.push(field.slice(3));
      if (field[0] === "R" || field[0] === "C") i++;
    }
    return paths;
  }
}
