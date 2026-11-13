import type { CliResult } from "./cli";
import { type Mapping, mappingFor } from "./manifest";
import { failedText } from "./texts";

/** The active note's commands (curation-surfaces spec §3.2): each resolves
 * the note to its domain and document name, shows the CLI's own dry run
 * (`effect.summary`) and runs the real command only after the user confirms. */

export type NoteScope = "document" | "section" | "clause";

export interface Older {
  /** The older note's document name (its file name, extension included). */
  older: string;
  scope: NoteScope;
  /** ISO date, or "" for none (supersede only). */
  effective: string;
}

export interface NoteAsk {
  text: string;
  confirm: string;
}

export interface NoteDeps {
  /** Runs `artmind <args> --compact`; the real call of a write is queued by the CLI. */
  run(args: string[]): Promise<CliResult>;
  record(result: CliResult): void;
  /** Resolves true on the confirm button, false on cancel. */
  ask(request: NoteAsk): Promise<boolean>;
  /** The picker over `candidates`; null when cancelled. */
  pickOlder(candidates: string[], withEffective: boolean): Promise<Older | null>;
  showHistory(title: string, json: unknown): void;
  notify(kind: "prompt" | "error", text: string): void;
  /** Re-reads the checklist after a write. */
  refresh(): Promise<void>;
  /** The domain's documents other than `exclude`, as document names. */
  candidates(domain: string, exclude: string): string[];
}

export interface NoteRef {
  /** Vault-relative path. */
  path: string;
  /** File name with extension: the document name artmind stores. */
  name: string;
}

export function noteRef(path: string): NoteRef {
  return { path, name: path.slice(path.lastIndexOf("/") + 1) };
}

const base = (cmd: string[], domain: string, name: string): string[] => [...cmd, "--domain", domain, "--documentName", name];

export function supersedeArgs(domain: string, newer: string, o: Older): string[] {
  return [
    "ingest", "supersede", "--domain", domain, "--newer", newer, "--older", o.older, "--scope", o.scope,
    ...(o.effective ? ["--effective", o.effective] : []),
  ];
}

export function unsupersedeArgs(domain: string, newer: string, o: Older): string[] {
  return ["ingest", "unsupersede", "--domain", domain, "--newer", newer, "--older", o.older, "--scope", o.scope];
}

export const retireArgs = (domain: string, name: string) => base(["docs", "retire"], domain, name);
export const restoreArgs = (domain: string, name: string) => base(["docs", "restore"], domain, name);
export const archiveArgs = (domain: string, name: string) => base(["docs", "archive"], domain, name);
export const historyArgs = (domain: string, name: string) => base(["query", "document-history"], domain, name);

export function effectSummary(json: unknown): string | null {
  const s = (json as { effect?: { summary?: unknown } } | null)?.effect?.summary;
  return typeof s === "string" && s ? s : null;
}

export const NOTE_TEXT = {
  noNote: "Open a note first.",
  unmapped: (path: string) => `${path} is not in a folder this vault ingests.`,
  noOthers: "No other document of this domain to choose from.",
  archiveSecond: (path: string) =>
    `This deletes ${path} from your vault (a copy goes to the archive bundle). Obsidian Git will commit the deletion. ` +
    "To get it back, run `artmind docs restore-from-archive`. Delete the file?",
};

export class NoteCommands {
  constructor(
    private deps: NoteDeps,
    private mappings: () => Mapping[],
  ) {}

  private resolve(path: string | null): { ref: NoteRef; domain: string } | null {
    if (!path) {
      this.deps.notify("prompt", NOTE_TEXT.noNote);
      return null;
    }
    const domain = mappingFor(this.mappings(), path)?.domain;
    if (!domain) {
      this.deps.notify("prompt", NOTE_TEXT.unmapped(path));
      return null;
    }
    return { ref: noteRef(path), domain };
  }

  /** Dry run, show its effect, confirm (twice for `second`), then the real run. */
  private async write(
    what: string,
    args: string[],
    realArgs: string[],
    ask: { confirm: string; second?: string },
  ): Promise<void> {
    const dry = await this.deps.run([...args, "--dryRun"]);
    this.deps.record(dry);
    if (!dry.ok) {
      this.deps.notify("error", failedText(what, dry.error));
      return;
    }
    const summary = effectSummary(dry.json) ?? "No change reported.";
    if (!(await this.deps.ask({ text: `${what[0].toUpperCase()}${what.slice(1)}: ${summary}`, confirm: ask.confirm }))) return;
    if (ask.second && !(await this.deps.ask({ text: ask.second, confirm: ask.confirm }))) return;
    const result = await this.deps.run(realArgs);
    this.deps.record(result);
    if (!result.ok) {
      this.deps.notify("error", failedText(what, result.error));
      return;
    }
    this.deps.notify("prompt", `Done: ${what}.`);
    await this.deps.refresh();
  }

  async supersedes(path: string | null): Promise<void> {
    await this.pair(path, true);
  }

  async undoSupersession(path: string | null): Promise<void> {
    await this.pair(path, false);
  }

  private async pair(path: string | null, assert: boolean): Promise<void> {
    const r = this.resolve(path);
    if (!r) return;
    const others = this.deps.candidates(r.domain, r.ref.name);
    if (!others.length) {
      this.deps.notify("prompt", NOTE_TEXT.noOthers);
      return;
    }
    const older = await this.deps.pickOlder(others, assert);
    if (!older) return;
    const args = (assert ? supersedeArgs : unsupersedeArgs)(r.domain, r.ref.name, older);
    const what = assert ? `${r.ref.name} supersedes ${older.older}` : `undo that ${r.ref.name} supersedes ${older.older}`;
    await this.write(what, args, args, { confirm: assert ? "Supersede" : "Undo" });
  }

  async retire(path: string | null): Promise<void> {
    const r = this.resolve(path);
    if (r) await this.write(`retire ${r.ref.name}`, retireArgs(r.domain, r.ref.name), retireArgs(r.domain, r.ref.name), { confirm: "Retire" });
  }

  async restore(path: string | null): Promise<void> {
    const r = this.resolve(path);
    if (r) await this.write(`restore ${r.ref.name}`, restoreArgs(r.domain, r.ref.name), restoreArgs(r.domain, r.ref.name), { confirm: "Restore" });
  }

  async archive(path: string | null): Promise<void> {
    const r = this.resolve(path);
    if (!r) return;
    const args = archiveArgs(r.domain, r.ref.name);
    // `--yes`: the CLI's own prompt would abort with no terminal; the two modals are the confirmation.
    await this.write(`archive ${r.ref.name}`, args, [...args, "--yes"], { confirm: "Archive", second: NOTE_TEXT.archiveSecond(r.ref.path) });
  }

  async history(path: string | null): Promise<void> {
    const r = this.resolve(path);
    if (!r) return;
    const result = await this.deps.run(historyArgs(r.domain, r.ref.name));
    this.deps.record(result);
    if (!result.ok) {
      this.deps.notify("error", failedText(`read the history of ${r.ref.name}`, result.error));
      return;
    }
    this.deps.showHistory(`History of ${r.ref.name}`, result.json);
  }
}
