import { describe, expect, it } from "vitest";
import { type WebViewerApp, openInWebViewer, webViewerAvailable } from "../src/webviewer";

class FakeLeaf {
  states: unknown[] = [];
  constructor(public url: string | null = null, private fail = false) {}
  getViewState() {
    return { type: "webviewer", state: this.url ? { url: this.url } : {} };
  }
  async setViewState(state: { state?: Record<string, unknown> }) {
    if (this.fail) throw new Error("no such view");
    this.states.push(state);
    this.url = (state.state?.url as string) ?? null;
  }
}

function app(opts: { enabled?: boolean; registered?: boolean; leaves?: FakeLeaf[]; newLeaf?: FakeLeaf } = {}) {
  const revealed: FakeLeaf[] = [];
  const created: FakeLeaf[] = [];
  const a: WebViewerApp & { revealed: FakeLeaf[]; created: FakeLeaf[] } = {
    revealed,
    created,
    internalPlugins: { getEnabledPluginById: (id) => (opts.enabled !== false && id === "webviewer" ? {} : null) },
    viewRegistry: { viewByType: opts.registered === false ? {} : { webviewer: () => null } },
    workspace: {
      getLeavesOfType: () => opts.leaves ?? [],
      getLeaf: () => {
        const leaf = opts.newLeaf ?? new FakeLeaf();
        created.push(leaf);
        return leaf;
      },
      revealLeaf: (leaf) => void revealed.push(leaf as FakeLeaf),
    },
  };
  return a;
}

describe("the Web viewer", () => {
  it("is available only when the core plugin is on and its view is registered", () => {
    expect(webViewerAvailable(app())).toBe(true);
    expect(webViewerAvailable(app({ enabled: false }))).toBe(false);
    expect(webViewerAvailable(app({ registered: false }))).toBe(false);
  });

  it("reads the older plugins map too, and treats missing internals as unavailable", () => {
    const older = { ...app(), internalPlugins: { plugins: { webviewer: { enabled: true } } } };
    expect(webViewerAvailable(older)).toBe(true);
    expect(webViewerAvailable({ workspace: app().workspace })).toBe(false);
  });

  it("opens a new tab at the URL", async () => {
    const a = app();

    expect(await openInWebViewer(a, "http://127.0.0.1:8379/dashboard", "http://127.0.0.1:8379")).toBe(true);

    expect(a.created).toHaveLength(1);
    expect(a.created[0].states).toEqual([{ type: "webviewer", active: true, state: { url: "http://127.0.0.1:8379/dashboard", navigate: true } }]);
    expect(a.revealed).toEqual(a.created);
  });

  it("reuses the tab already showing the admin console, not one showing another site", async () => {
    const other = new FakeLeaf("https://example.com/");
    const admin = new FakeLeaf("http://127.0.0.1:8379/");
    const a = app({ leaves: [other, admin] });

    await openInWebViewer(a, "http://127.0.0.1:8379/dashboard", "http://127.0.0.1:8379");

    expect(a.created).toEqual([]);
    expect(admin.url).toBe("http://127.0.0.1:8379/dashboard");
    expect(other.states).toEqual([]);
  });

  it("reports failure, so the caller can fall back to the browser", async () => {
    expect(await openInWebViewer(app({ newLeaf: new FakeLeaf(null, true) }), "http://x", "http://x")).toBe(false);
  });
});
