/** Stand-in for the `obsidian` module, which only exists inside the app.
 * It covers what the plugin's views import, and records what they did. */
import { parse } from "yaml";

export class Notice {
  static shown: Notice[] = [];
  message: string | DocumentFragment;
  duration: number | undefined;
  hidden = false;

  constructor(message: string | DocumentFragment, duration?: number) {
    this.message = message;
    this.duration = duration;
    Notice.shown.push(this);
  }

  hide(): void {
    this.hidden = true;
  }
}

export class Modal {
  app: unknown;
  contentEl: HTMLElement;
  titleEl: HTMLElement;
  isOpen = false;

  constructor(app: unknown) {
    this.app = app;
    this.contentEl = document.createElement("div");
    this.titleEl = document.createElement("div");
  }

  open(): void {
    this.isOpen = true;
    void (this as unknown as { onOpen?: () => unknown }).onOpen?.();
  }

  close(): void {
    this.isOpen = false;
    (this as unknown as { onClose?: () => void }).onClose?.();
  }
}

export class ItemView {
  leaf: unknown;
  contentEl: HTMLElement;

  constructor(leaf: unknown) {
    this.leaf = leaf;
    this.contentEl = document.createElement("div");
  }
}

export class WorkspaceLeaf {}

/** Records what a plugin registers; `app` and `manifest` come from the test. */
export class Plugin {
  app: any;
  manifest: { id: string };
  statusBarItems: HTMLElement[] = [];
  commands: Array<{ id: string; name: string }> = [];
  views: string[] = [];
  events: unknown[] = [];
  data: unknown = null;

  constructor(app: any, manifest: { id: string }) {
    this.app = app;
    this.manifest = manifest;
  }

  async loadData(): Promise<unknown> {
    return this.data;
  }

  async saveData(data: unknown): Promise<void> {
    this.data = data;
  }

  ribbonIcons: Array<{ icon: string; title: string; el: HTMLElement; onClick: (evt: MouseEvent) => unknown }> = [];

  addRibbonIcon(icon: string, title: string, onClick: (evt: MouseEvent) => unknown): HTMLElement {
    const el = document.createElement("div");
    this.ribbonIcons.push({ icon, title, el, onClick });
    return el;
  }

  addStatusBarItem(): HTMLElement {
    const el = document.createElement("div");
    this.statusBarItems.push(el);
    return el;
  }

  addCommand(command: { id: string; name: string }): void {
    this.commands.push(command);
  }

  registerView(type: string): void {
    this.views.push(type);
  }

  addSettingTab(): void {}

  registerEvent(ref: unknown): void {
    this.events.push(ref);
  }

  registerDomEvent(): void {}
}

export class PluginSettingTab {
  app: unknown;
  containerEl: HTMLElement;

  constructor(app: unknown, _plugin: unknown) {
    this.app = app;
    this.containerEl = document.createElement("div");
  }
}

export class Setting {
  constructor(_containerEl: HTMLElement) {}
  setName(): this {
    return this;
  }
  setDesc(): this {
    return this;
  }
  addText(): this {
    return this;
  }
  addToggle(): this {
    return this;
  }
  addButton(): this {
    return this;
  }
}

export class FileSystemAdapter {}

export function parseYaml(text: string): unknown {
  return parse(text);
}

export function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+/g, "/").replace(/^\/|\/$/g, "");
}

/** Records the items a menu was built with, and where it was shown. */
export class MenuItem {
  title = "";
  icon = "";
  disabled = false;
  onClickFn: (() => unknown) | null = null;
  setTitle(title: string): this {
    this.title = title;
    return this;
  }
  setIcon(icon: string): this {
    this.icon = icon;
    return this;
  }
  setDisabled(disabled: boolean): this {
    this.disabled = disabled;
    return this;
  }
  onClick(fn: () => unknown): this {
    this.onClickFn = fn;
    return this;
  }
}

export class Menu {
  static shown: Menu[] = [];
  items: MenuItem[] = [];
  addItem(build: (item: MenuItem) => unknown): this {
    const item = new MenuItem();
    build(item);
    this.items.push(item);
    return this;
  }
  addSeparator(): this {
    return this;
  }
  showAtMouseEvent(): void {
    Menu.shown.push(this);
  }
}

export function setIcon(el: HTMLElement, icon: string): void {
  el.setAttribute("data-icon", icon);
}

/** Tests that reach the network replace this. */
export async function requestUrl(_request: unknown): Promise<never> {
  throw new Error("requestUrl is not available in tests");
}
