import { describe, expect, it } from "vitest";
import { type CommandsLike, INSTRUCTIONS, KNOWN_IDS, ObsidianGitBridge } from "../src/bridge";

/** `app.commands` as Obsidian holds it, recording what ran. */
function commands(entries: Array<[string, string]>): CommandsLike & { ran: string[] } {
  const ran: string[] = [];
  return {
    ran,
    commands: Object.fromEntries(entries.map(([id, name]) => [id, { id, name }])),
    executeCommandById(id: string) {
      ran.push(id);
      return true;
    },
  };
}

/** What obsidian-git 2.40.0 registers for the three actions. */
const CURRENT: Array<[string, string]> = [
  ["obsidian-git:pull", "Git: Pull"],
  ["obsidian-git:push", "Git: Commit-and-sync"],
  ["obsidian-git:commit", "Git: Commit all changes"],
  ["obsidian-git:commit-smart", "Git: Commit"],
];

describe("ObsidianGitBridge", () => {
  it("finds the known ids and runs them", () => {
    const registry = commands(CURRENT);
    const bridge = new ObsidianGitBridge(() => registry);

    expect(bridge.lookup()).toEqual({
      pull: "obsidian-git:pull",
      commitAndSync: "obsidian-git:push",
      commit: "obsidian-git:commit",
    });
    expect(bridge.run("pull")).toBe(true);
    expect(bridge.run("commitAndSync")).toBe(true);
    expect(bridge.run("commit")).toBe(true);
    expect(registry.ran).toEqual(["obsidian-git:pull", "obsidian-git:push", "obsidian-git:commit"]);
    expect(bridge.warning()).toBeNull();
  });

  it("follows a renamed id by the command's name", () => {
    const registry = commands([
      ["obsidian-git:pull-from-remote", "Obsidian Git: Pull"],
      ["obsidian-git:backup", "Obsidian Git: Create backup"],
      ["obsidian-git:commit-all", "Obsidian Git: Commit all changes"],
    ]);
    const bridge = new ObsidianGitBridge(() => registry);

    expect(bridge.lookup()).toEqual({
      pull: "obsidian-git:pull-from-remote",
      commitAndSync: "obsidian-git:backup",
      commit: "obsidian-git:commit-all",
    });
  });

  it("never takes another plugin's command of the same name", () => {
    const bridge = new ObsidianGitBridge(() => commands([["other-sync:pull", "Other: Pull"]]));
    expect(bridge.lookup().pull).toBeNull();
  });

  it("turns a missing command into an instruction and one warning line", () => {
    const registry = commands([["obsidian-git:pull", "Git: Pull"]]);
    const bridge = new ObsidianGitBridge(() => registry);

    expect(bridge.missing()).toEqual(["commitAndSync", "commit"]);
    expect(bridge.run("commit")).toBe(false);
    expect(registry.ran).toEqual([]);
    expect(bridge.instruction("commitAndSync")).toBe(INSTRUCTIONS.commitAndSync);
    expect(bridge.warning()).toBe(
      "Obsidian Git has no Commit-and-sync, Commit command (renamed?): those buttons show what to run instead.",
    );
  });

  it("says when Obsidian Git is not there at all", () => {
    const bridge = new ObsidianGitBridge(() => undefined);
    expect(bridge.missing()).toEqual(["pull", "commitAndSync", "commit"]);
    expect(bridge.warning()).toMatch(/not installed or enabled/);
    expect(bridge.run("pull")).toBe(false);
  });

  it("tries only obsidian-git ids", () => {
    for (const ids of Object.values(KNOWN_IDS)) for (const id of ids) expect(id.startsWith("obsidian-git:")).toBe(true);
  });
});
