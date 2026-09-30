/** Triggers Obsidian Git's own commands: the plugin never writes to git
 * itself (spec P5, §4.5). */

export type GitAction = "pull" | "commitAndSync" | "commit";

/** Command ids tried in order, read from obsidian-git 2.40.0's `main.js`. */
export const KNOWN_IDS: Record<GitAction, string[]> = {
  pull: ["obsidian-git:pull"],
  commitAndSync: ["obsidian-git:push"],
  commit: ["obsidian-git:commit"],
};

/** When no known id is registered (a rename in a later release), a command
 * of the obsidian-git plugin whose name is one of these is used instead. */
export const KNOWN_NAMES: Record<GitAction, string[]> = {
  pull: ["Pull"],
  commitAndSync: ["Commit-and-sync", "Create backup"],
  commit: ["Commit all changes"],
};

export const INSTRUCTIONS: Record<GitAction, string> = {
  pull: "Run Obsidian Git: Pull",
  commitAndSync: "Run Obsidian Git: Commit-and-sync",
  commit: "Run Obsidian Git: Commit all changes",
};

const LABELS: Record<GitAction, string> = { pull: "Pull", commitAndSync: "Commit-and-sync", commit: "Commit" };

/** The slice of Obsidian's `app.commands` this needs. */
export interface CommandsLike {
  commands: Record<string, { id: string; name: string }>;
  executeCommandById(id: string): boolean;
}

export class ObsidianGitBridge {
  private getCommands: () => CommandsLike | undefined;

  constructor(getCommands: () => CommandsLike | undefined) {
    this.getCommands = getCommands;
  }

  /** The command id each action runs, or null when none is registered. */
  lookup(): Record<GitAction, string | null> {
    const registry = this.getCommands()?.commands ?? {};
    const find = (action: GitAction): string | null => {
      for (const id of KNOWN_IDS[action]) if (registry[id]) return id;
      const names = KNOWN_NAMES[action].map((n) => n.toLowerCase());
      for (const command of Object.values(registry)) {
        if (!command.id.startsWith("obsidian-git:")) continue;
        const bare = command.name.includes(": ") ? command.name.slice(command.name.indexOf(": ") + 2) : command.name;
        if (names.includes(bare.toLowerCase())) return command.id;
      }
      return null;
    };
    return { pull: find("pull"), commitAndSync: find("commitAndSync"), commit: find("commit") };
  }

  missing(): GitAction[] {
    const ids = this.lookup();
    return (Object.keys(ids) as GitAction[]).filter((action) => ids[action] === null);
  }

  has(action: GitAction): boolean {
    return this.lookup()[action] !== null;
  }

  /** Runs the action's command; false (and nothing runs) when it is missing. */
  run(action: GitAction): boolean {
    const id = this.lookup()[action];
    const commands = this.getCommands();
    if (!id || !commands) return false;
    return commands.executeCommandById(id);
  }

  instruction(action: GitAction): string {
    return INSTRUCTIONS[action];
  }

  /** The one warning line for the panel, or null when every command is there. */
  warning(): string | null {
    const missing = this.missing();
    if (!missing.length) return null;
    if (missing.length === 3) return "Obsidian Git is not installed or enabled: its buttons show what to run instead.";
    return `Obsidian Git has no ${missing.map((a) => LABELS[a]).join(", ")} command (renamed?): those buttons show what to run instead.`;
  }
}
