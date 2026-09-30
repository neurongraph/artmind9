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
    expect(plugin.commands).toEqual([]);
    expect(a.layoutReady).toEqual([]);
  });

  it("in an artmind vault, adds the status bar, the panel and every command (spec §3.4)", async () => {
    const a = app(true);
    const plugin = new ArtmindPlugin(a as never, { id: "artmind" } as never) as any;

    await plugin.onload();

    expect(plugin.statusBarItems).toHaveLength(1);
    expect(plugin.views).toEqual(["artmind-view"]);
    expect(plugin.commands.map((c: { name: string }) => c.name)).toEqual([
      "Sync",
      "Ingest what changed",
      "Show status",
      "Resolve artmind conflicts",
      "Run doctor",
      "Review tables for graph",
      "Open side panel",
    ]);
    expect(a.layoutReady).toHaveLength(1);
    expect(plugin.events).toEqual([]);
    plugin.onunload();
  });
});
