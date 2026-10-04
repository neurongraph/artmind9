import { FileSystemAdapter, Plugin, parseYaml } from "obsidian";
import { AdminConsole, adminOutcomeText, hostPort, stopOutcomeText } from "./admin";
import { openInBrowser, realAdminDeps } from "./adminDeps";
import { ObsidianGitBridge, type CommandsLike, autoPullMinutes, obsidianGitDataPath } from "./bridge";
import { ArtmindCli, defaultDetectDeps, detectArtmind } from "./cli";
import { Controller, type Snapshot } from "./controller";
import { GitReader } from "./git";
import { type Mapping, VAULT_YAML, promptsOnArrival, readMappings } from "./manifest";
import { NOTHING_PERSISTED, type Persisted, readPersisted, withPersisted } from "./persist";
import { type ArtmindSettings, mergeSettings } from "./settings";
import type { PanelTarget } from "./state";
import { COMMANDS } from "./texts";
import { Watchers } from "./watchers";
import { type WebViewerApp, openInWebViewer, webViewerAvailable } from "./webviewer";
import { ConfirmModal } from "./views/confirmModal";
import { ObsidianNotifier } from "./views/notices";
import { ArtmindView, type PanelHandlers, VIEW_TYPE } from "./views/panel";
import { ResolveModal } from "./views/resolveModal";
import { RibbonIcon } from "./views/ribbon";
import { ArtmindSettingTab } from "./views/settingsTab";
import { StatusBarItem } from "./views/statusBar";
import { SynthesizeModal } from "./views/synthesizeModal";
import { TableReviewModal } from "./views/tableReview";

/** Obsidian's private `app.commands` and `app.setting`, typed as far as used. */
interface AppInternals {
  commands?: CommandsLike;
  setting?: { open(): void; openTabById(id: string): void };
}

export default class ArtmindPlugin extends Plugin {
  artmindSettings!: ArtmindSettings;
  private persisted: Persisted = NOTHING_PERSISTED;
  private controller: Controller | null = null;
  private watchers: Watchers | null = null;
  private statusBar: StatusBarItem | null = null;
  private ribbon: RibbonIcon | null = null;
  private admin: AdminConsole | null = null;
  private notifier = new ObsidianNotifier((target) => void this.openPanel(target));
  private mappings: Mapping[] = [];
  private last: Snapshot | null = null;

  override async onload(): Promise<void> {
    const data = await this.loadData();
    this.artmindSettings = mergeSettings(data);
    this.persisted = readPersisted(data);
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
      notifier: this.notifier,
      views: {
        render: (snapshot) => this.render(snapshot),
        openPanel: (target) => void this.openPanel(target),
        openResolve: () => this.openResolve(),
        openTableReview: (table) => this.openTableReview(table),
        confirm: (request) => new ConfirmModal(this.app, request).open(),
        openSynthesize: () => this.openSynthesize(),
        openAdmin: (path) => void this.openAdmin(path),
      },
      settings: () => this.artmindSettings,
      detect: () => detectArtmind(this.artmindSettings.artmindPath, defaultDetectDeps()),
      domains: () => this.mappings.map((m) => m.domain),
      readObsidianGit: () => this.readObsidianGit(),
      persisted: this.persisted,
      persist: (p) => {
        this.persisted = p;
        void this.saveAll();
      },
    });
    this.controller = controller;
    const watchers = new Watchers(
      {
        readHead: () => new GitReader(root).head(),
        onHeadSettled: () => void controller.onHeadSettled(),
        onNewFiles: (paths) => controller.onNewFiles(paths),
        promptsOnArrival: (path) => promptsOnArrival(this.mappings, path),
        refresh: (reason) => {
          // Obsidian does not index `.artmind/`, so no event says the
          // manifest changed: re-read it whenever state is refreshed.
          void this.loadMappings();
          controller.scheduleRefresh(reason);
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

    this.admin = new AdminConsole(realAdminDeps(root, () => cli.path, (url) => void this.openAdminPage(url)), {
      url: () => this.artmindSettings.adminUiUrl,
      vaultRoot: root,
      startedPid: () => this.artmindSettings.adminUiPid,
      saveStartedPid: (pid) => this.saveStartedPid(pid),
    });
    this.ribbon = new RibbonIcon(this.addRibbonIcon("brain-circuit", "artmind", () => controller.openPanelAtCurrent()), {
      openPanel: () => void this.openPanel(),
      openAdmin: () => void this.openAdmin(),
      doctor: () => void controller.runDoctor(),
    });
    // Opens the panel at the current row; never runs an action (spec D8).
    this.statusBar = new StatusBarItem(this.addStatusBarItem(), () => controller.openPanelAtCurrent());
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
    await this.saveAll();
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

  /** Obsidian Git's settings live under the config dir, which Obsidian does
   * not index: read through the adapter. Unreadable counts as off (D10). */
  private async readObsidianGit(): Promise<{ autoPullMinutes: number }> {
    let text: string | null = null;
    try {
      text = await this.app.vault.adapter.read(obsidianGitDataPath(this.app.vault.configDir));
    } catch {
      text = null;
    }
    return { autoPullMinutes: autoPullMinutes(text) };
  }

  /** Not saveSettings: a pid changes nothing artmind needs re-checked. But
   * saveAll, so the persisted state is written back with it. */
  private async saveStartedPid(pid: number): Promise<void> {
    this.artmindSettings.adminUiPid = pid;
    await this.saveAll();
  }

  /** Saves settings and the persisted controller state together. */
  private async saveAll(): Promise<void> {
    const persisted = this.controller?.persisted() ?? this.persisted;
    await this.saveData(withPersisted(this.artmindSettings, persisted));
  }

  private render(snapshot: Snapshot): void {
    this.last = snapshot;
    this.statusBar?.render(snapshot.checklist);
    this.ribbon?.render(snapshot.checklist);
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
      if (leaf.view instanceof ArtmindView) leaf.view.update(snapshot);
    }
  }

  private async openPanel(target: PanelTarget = "remote"): Promise<void> {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false) ?? this.app.workspace.getLeaf(true);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
    if (leaf.view instanceof ArtmindView) {
      if (this.last) leaf.view.update(this.last);
      leaf.view.focus(target);
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
      askAdminUi: () => void this.openAdmin(),
    }).open();
  }

  private openSynthesize(): void {
    const controller = this.controller;
    if (!controller) return;
    new SynthesizeModal(this.app, {
      plan: () => controller.synthesisPlan(),
      run: (domains, limit) => void controller.synthesize(domains, limit),
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

  /** Open `path` on this vault's admin console, starting it if need be. */
  private async openAdmin(path = "/"): Promise<void> {
    const admin = this.admin;
    if (!admin) return;
    if (!admin.isStarting) this.notifier.show("prompt", "Opening the admin console…");
    const outcome = await admin.open(path);
    const text = adminOutcomeText(outcome);
    if (text) this.notifier.show(outcome.kind === "opened" || outcome.kind === "busy" ? "prompt" : "error", text);
  }

  /** In an Obsidian tab when the setting asks and the Web viewer is on;
   * the browser otherwise, including when the Web viewer refuses. */
  private async openAdminPage(url: string): Promise<void> {
    const app = this.app as unknown as WebViewerApp;
    if (this.artmindSettings.adminInObsidianTab && webViewerAvailable(app)) {
      if (await openInWebViewer(app, url, hostPort(url).base)) return;
    }
    openInBrowser(url);
  }

  private async stopAdmin(): Promise<void> {
    const outcome = await this.admin?.stop();
    if (outcome) this.notifier.show(outcome.kind === "stopped" || outcome.kind === "not_running" ? "prompt" : "error", stopOutcomeText(outcome));
  }

  private panelHandlers(): PanelHandlers {
    return {
      refresh: () => void this.controller?.refresh({ heavy: true }),
      run: (action) => this.controller?.run(action),
      openAdmin: (path?: string) => void this.openAdmin(path),
      doctor: () => void this.controller?.runDoctor(),
      setPath: () => {
        const setting = (this.app as unknown as AppInternals).setting;
        setting?.open();
        setting?.openTabById(this.manifest.id);
      },
      copy: (text: string) => void navigator.clipboard.writeText(text),
    };
  }

  /** Every action is also a command (checklist spec §4). */
  private addCommands(): void {
    const c = () => this.controller;
    const commands: Array<[string, string, () => void]> = [
      ["pull", COMMANDS.pull, () => void c()?.run({ type: "pull" })],
      ["apply", COMMANDS.apply, () => void c()?.apply()],
      ["ingest-what-changed", COMMANDS.ingest, () => void c()?.ingestWhatChanged()],
      ["rebuild", COMMANDS.rebuild, () => void c()?.rebuild()],
      ["synthesize", COMMANDS.synthesize, () => c()?.openSynthesize()],
      ["commit-push", COMMANDS.commitPush, () => c()?.commitPush()],
      ["review-tables", COMMANDS.reviewTables, () => c()?.reviewTables()],
      ["resolve", COMMANDS.resolve, () => c()?.openResolve()],
      ["doctor", COMMANDS.doctor, () => void c()?.runDoctor()],
      ["open-panel", COMMANDS.openPanel, () => void this.openPanel()],
      ["open-admin-console", COMMANDS.openAdmin, () => void this.openAdmin()],
      ["stop-admin-console", COMMANDS.stopAdmin, () => void this.stopAdmin()],
    ];
    for (const [id, name, callback] of commands) this.addCommand({ id, name, callback });
  }
}
