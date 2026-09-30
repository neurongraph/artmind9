import { FileSystemAdapter, Plugin, parseYaml } from "obsidian";
import { ObsidianGitBridge, type CommandsLike } from "./bridge";
import { ArtmindCli, defaultDetectDeps, detectArtmind } from "./cli";
import { Controller, type Snapshot } from "./controller";
import { GitReader } from "./git";
import { type Mapping, VAULT_YAML, promptsOnArrival, readMappings } from "./manifest";
import { type ArtmindSettings, mergeSettings } from "./settings";
import type { PanelSection } from "./state";
import { Watchers } from "./watchers";
import { ObsidianNotifier } from "./views/notices";
import { ArtmindView, VIEW_TYPE } from "./views/panel";
import { ResolveModal } from "./views/resolveModal";
import { ArtmindSettingTab } from "./views/settingsTab";
import { StatusBarItem } from "./views/statusBar";
import { TableReviewModal } from "./views/tableReview";

/** Obsidian's private `app.commands` and `app.setting`, typed as far as used. */
interface AppInternals {
  commands?: CommandsLike;
  setting?: { open(): void; openTabById(id: string): void };
}

export default class ArtmindPlugin extends Plugin {
  artmindSettings!: ArtmindSettings;
  private controller: Controller | null = null;
  private watchers: Watchers | null = null;
  private statusBar: StatusBarItem | null = null;
  private mappings: Mapping[] = [];
  private last: Snapshot | null = null;

  override async onload(): Promise<void> {
    this.artmindSettings = mergeSettings(await this.loadData());
    // Dormant outside an artmind vault (spec §7): no status bar, no polls.
    if (!(await this.app.vault.adapter.exists(VAULT_YAML))) return;
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) return;
    const root = adapter.getBasePath();
    await this.loadMappings();

    const internals = this.app as unknown as AppInternals;
    const cli = new ArtmindCli({ path: this.artmindSettings.artmindPath || "artmind", cwd: root });
    const bridge = new ObsidianGitBridge(() => internals.commands);
    const controller = new Controller({
      cli,
      git: new GitReader(root),
      bridge,
      notifier: new ObsidianNotifier(),
      views: {
        render: (snapshot) => this.render(snapshot),
        openPanel: (section) => void this.openPanel(section),
        openResolve: () => this.openResolve(),
        openTableReview: (table) => this.openTableReview(table),
      },
      settings: () => this.artmindSettings,
      detect: () => detectArtmind(this.artmindSettings.artmindPath, defaultDetectDeps()),
    });
    this.controller = controller;
    const watchers = new Watchers(
      {
        readHead: () => new GitReader(root).head(),
        onHeadSettled: () => void controller.onHeadSettled(),
        onNewFiles: (paths) => controller.onNewFiles(paths),
        promptsOnArrival: (path) => promptsOnArrival(this.mappings, path),
        refresh: () => {
          // Obsidian does not index `.artmind/`, so no event says the
          // manifest changed: re-read it whenever state is refreshed.
          void this.loadMappings();
          controller.scheduleRefresh();
        },
        pollJob: () => controller.pollJob(),
      },
      {
        headPollMs: this.artmindSettings.headPollSeconds * 1000,
        newFileGroupMs: this.artmindSettings.newFileGroupSeconds * 1000,
        jobPollMs: this.artmindSettings.jobPollSeconds * 1000,
      },
    );
    this.watchers = watchers;
    controller.attachWatchers(watchers);

    this.statusBar = new StatusBarItem(this.addStatusBarItem(), (action) => controller.perform(action));
    this.registerView(VIEW_TYPE, (leaf) => new ArtmindView(leaf, this.panelHandlers()));
    this.addSettingTab(new ArtmindSettingTab(this.app, this));
    this.addCommands();

    this.app.workspace.onLayoutReady(async () => {
      // Registered only now: Obsidian fires `create` for every file while the
      // vault loads, and none of those is an arrival.
      this.registerEvent(this.app.vault.on("create", (file) => watchers.fileCreated(file.path)));
      this.registerDomEvent(window, "focus", () => watchers.focusRegained());
      const problem = await controller.checkArtmind();
      if (!problem) {
        await watchers.start();
        await controller.refresh();
      }
    });
  }

  override onunload(): void {
    this.watchers?.stop();
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.artmindSettings);
    const controller = this.controller;
    if (!controller) return;
    if (!(await controller.checkArtmind())) await controller.refresh();
  }

  /** `.artmind/` is a dot-folder Obsidian does not index: read it through the adapter. */
  private async loadMappings(): Promise<void> {
    try {
      this.mappings = readMappings(await this.app.vault.adapter.read(VAULT_YAML), parseYaml);
    } catch {
      this.mappings = [];
    }
  }

  private render(snapshot: Snapshot): void {
    this.last = snapshot;
    this.statusBar?.render(snapshot.state);
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
      if (leaf.view instanceof ArtmindView) leaf.view.update(snapshot);
    }
  }

  private async openPanel(section: PanelSection = "status"): Promise<void> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) ?? this.app.workspace.getLeaf(true);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
    if (leaf.view instanceof ArtmindView) {
      if (this.last) leaf.view.update(this.last);
      leaf.view.focus(section);
    }
  }

  private openResolve(): void {
    const controller = this.controller;
    if (!controller) return;
    new ResolveModal(this.app, {
      preview: () => controller.resolvePreview(),
      apply: () => controller.resolveApply(),
      open: (path) => this.openVaultPath(path),
    }).open();
  }

  private openTableReview(table: string | null): void {
    const controller = this.controller;
    if (!controller) return;
    new TableReviewModal(this.app, table, {
      dryRun: (t) => controller.tableDryRun(t),
      project: (t) => controller.tableProject(t),
      openMapping: (path) => this.openExternal(path),
      askAdminUi: () => window.open(this.artmindSettings.adminUiUrl),
    }).open();
  }

  /** A note opens in Obsidian; a file under a dot-folder (hidden from
   * Obsidian) opens in the system's editor. */
  private openVaultPath(path: string): void {
    if (path.split("/").some((part) => part.startsWith("."))) {
      const adapter = this.app.vault.adapter as FileSystemAdapter;
      this.openExternal(adapter.getFullPath(path));
      return;
    }
    void this.app.workspace.openLinkText(path, "", true);
  }

  private openExternal(fullPath: string): void {
    // Desktop only (isDesktopOnly): Electron's shell is always there.
    const { shell } = require("electron") as { shell: { openPath(path: string): Promise<string> } };
    void shell.openPath(fullPath);
  }

  private panelHandlers() {
    return {
      sync: () => void this.controller?.sync(),
      ingest: () => void this.controller?.ingestWhatChanged(),
      resolve: () => this.openResolve(),
      doctor: () => void this.controller?.runDoctor(),
      reviewTable: (table: string) => this.openTableReview(table),
      commitAndSync: () => void this.controller?.gitAction("commitAndSync"),
      setPath: () => {
        const setting = (this.app as unknown as AppInternals).setting;
        setting?.open();
        setting?.openTabById(this.manifest.id);
      },
      copy: (text: string) => void navigator.clipboard.writeText(text),
    };
  }

  /** Every action is also a command (spec §3.4). */
  private addCommands(): void {
    const commands: Array<[string, string, () => void]> = [
      ["sync", "Sync", () => void this.controller?.sync()],
      ["ingest-what-changed", "Ingest what changed", () => void this.controller?.ingestWhatChanged()],
      ["show-status", "Show status", () => void this.controller?.refresh().then(() => this.openPanel("status"))],
      ["resolve", "Resolve artmind conflicts", () => this.openResolve()],
      ["doctor", "Run doctor", () => void this.controller?.runDoctor()],
      ["review-tables", "Review tables for graph", () => this.controller?.reviewTables()],
      ["open-panel", "Open side panel", () => void this.openPanel("status")],
    ];
    for (const [id, name, callback] of commands) this.addCommand({ id, name, callback });
  }
}
