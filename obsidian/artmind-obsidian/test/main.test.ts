// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import ArtmindPlugin from "../src/main";
import { FileSystemAdapter } from "./mocks/obsidian";

/** An Obsidian `app` whose vault does (or does not) hold `.artmind/vault.yaml`.
 * `onLayoutReady` callbacks are kept, not run: nothing here spawns artmind. */
function app(isArtmindVault: boolean) {
  const adapter = Object.assign(new FileSystemAdapter(), {
    exists: async (path: string) => isArtmindVault && path === ".artmind/vault.yaml",
    read: async () => 'ingest:\n  mappings:\n    - path: "Notes/**"\n      domain: general\n',
    getBasePath: () => "/vault",
  });
  const layoutReady: Array<() => unknown> = [];
  return {
    layoutReady,
    vault: { adapter, on: (name: string) => ({ name }) },
    workspace: { onLayoutReady: (fn: () => unknown) => layoutReady.push(fn), getLeavesOfType: () => [] },
  };
}

describe("ArtmindPlugin", () => {
  it("stays dormant outside an artmind vault: no status bar, no commands, no polls (spec §7)", async () => {
    const a = app(false);
    const plugin = new ArtmindPlugin(a as never, { id: "artmind" } as never) as any;

    await plugin.onload();

    expect(plugin.statusBarItems).toEqual([]);
    expect(plugin.ribbonIcons).toEqual([]);
    expect(plugin.commands).toEqual([]);
    expect(a.layoutReady).toEqual([]);
  });

  it("in an artmind vault, adds the status bar, the panel and every command (checklist spec §4)", async () => {
    const a = app(true);
    const plugin = new ArtmindPlugin(a as never, { id: "artmind" } as never) as any;

    await plugin.onload();

    expect(plugin.statusBarItems).toHaveLength(1);
    expect(plugin.ribbonIcons.map((r: { icon: string; title: string }) => [r.icon, r.title])).toEqual([["brain-circuit", "artmind"]]);
    expect(plugin.views).toEqual(["artmind-view"]);
    expect(plugin.commands.map((c: { name: string }) => c.name)).toEqual([
      "Pull",
      "Apply to graph",
      "Ingest what changed",
      "Rebuild graph",
      "Synthesize…",
      "Commit & push",
      "Review tables for graph",
      "Resolve artmind conflicts",
      "Run doctor",
      "Open side panel",
      "Open admin console",
      "Stop admin console",
    ]);
    expect(a.layoutReady).toHaveLength(1);
    expect(plugin.events).toEqual([]);
    plugin.onunload();
  });

  it("saving the admin console's pid keeps the persisted state (checklist spec §4)", async () => {
    const a = app(true);
    const plugin = new ArtmindPlugin(a as never, { id: "artmind" } as never) as any;
    const lastApply = { at: 2_000, ok: true, summary: "Applied to graph: 2 docs, 1 table." };
    plugin.data = { state: { lastJob: null, lastApply, lastRebuild: null } };
    await plugin.onload();

    await plugin.saveStartedPid(4242);

    expect(plugin.data.adminUiPid).toBe(4242);
    expect(plugin.data.state.lastApply).toEqual(lastApply);
    plugin.onunload();
  });

  it("reads Obsidian Git's auto pull from the vault's config dir, through the tested parser (spec D10)", async () => {
    const a = app(true);
    const reads: string[] = [];
    (a.vault.adapter as any).read = async (path: string) => {
      reads.push(path);
      return path.endsWith("obsidian-git/data.json") ? '{"autoPullInterval":"5"}' : 'ingest:\n  mappings: []\n';
    };
    (a.vault as any).configDir = ".config-obsidian";
    const plugin = new ArtmindPlugin(a as never, { id: "artmind" } as never) as any;
    await plugin.onload();

    expect(await plugin.readObsidianGit()).toEqual({ autoPullMinutes: 0 });
    expect(reads).toContain(".config-obsidian/plugins/obsidian-git/data.json");
    plugin.onunload();
  });
});
