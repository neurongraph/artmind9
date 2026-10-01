/** Opening a page in Obsidian's Web viewer core plugin (a tab in the main
 * area) instead of the browser. The Web viewer has no public API: its view
 * type and state shape are Obsidian internals, so everything here is probed,
 * and any miss means "not available" -- the caller falls back to the browser
 * rather than breaking when Obsidian renames something. */

export const WEB_VIEWER = "webviewer";

interface LeafLike {
  getViewState(): { type: string; state?: Record<string, unknown> };
  setViewState(state: { type: string; active?: boolean; state?: Record<string, unknown> }): Promise<void>;
}

/** The slice of Obsidian's `app` used here, internals included. */
export interface WebViewerApp {
  internalPlugins?: {
    getEnabledPluginById?(id: string): unknown;
    plugins?: Record<string, { enabled?: boolean } | undefined>;
  };
  viewRegistry?: {
    viewByType?: Record<string, unknown>;
    getViewCreatorByType?(type: string): unknown;
  };
  workspace: {
    getLeavesOfType(type: string): LeafLike[];
    getLeaf(newLeaf: "tab"): LeafLike;
    revealLeaf(leaf: LeafLike): Promise<void> | void;
  };
}

/** The core plugin is on and its view type is registered. */
export function webViewerAvailable(app: WebViewerApp): boolean {
  try {
    const plugins = app.internalPlugins;
    const enabled = Boolean(plugins?.getEnabledPluginById?.(WEB_VIEWER) ?? plugins?.plugins?.[WEB_VIEWER]?.enabled);
    const registry = app.viewRegistry;
    const registered = Boolean(registry?.viewByType?.[WEB_VIEWER] ?? registry?.getViewCreatorByType?.(WEB_VIEWER));
    return enabled && registered;
  } catch {
    return false;
  }
}

function leafUrl(leaf: LeafLike): string | null {
  try {
    const url = leaf.getViewState().state?.url;
    return typeof url === "string" ? url : null;
  } catch {
    return null;
  }
}

/** Open `url` in a Web viewer tab, reusing one already showing a page from
 * `origin` (so repeated clicks don't pile up tabs). False on any failure. */
export async function openInWebViewer(app: WebViewerApp, url: string, origin: string): Promise<boolean> {
  try {
    const existing = app.workspace.getLeavesOfType(WEB_VIEWER).find((leaf) => leafUrl(leaf)?.startsWith(origin));
    const leaf = existing ?? app.workspace.getLeaf("tab");
    await leaf.setViewState({ type: WEB_VIEWER, active: true, state: { url, navigate: true } });
    await app.workspace.revealLeaf(leaf);
    return true;
  } catch {
    return false;
  }
}
