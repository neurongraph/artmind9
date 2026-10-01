import { Menu } from "obsidian";
import type { Tone, VaultStateResult } from "../state";

export interface RibbonHandlers {
  openPanel(): void;
  sync(): void;
  ingest(): void;
  openAdmin(): void;
  doctor(): void;
}

export interface RibbonItem {
  title: string;
  icon: string;
  /** Why it can't run now, or null; shown in the title, the item disabled. */
  blocked: string | null;
  run: () => void;
}

/** The right-click menu: the panel, then the everyday actions, each under
 * the same rules as the panel's toolbar. */
export function ribbonItems(state: VaultStateResult, handlers: RibbonHandlers): RibbonItem[] {
  return [
    { title: "Open side panel", icon: "panel-right", blocked: null, run: handlers.openPanel },
    { title: "Sync", icon: "refresh-cw", blocked: state.blocked.sync, run: handlers.sync },
    { title: "Ingest what changed", icon: "file-input", blocked: state.blocked.ingest, run: handlers.ingest },
    { title: "Open admin console", icon: "external-link", blocked: state.blocked.admin, run: handlers.openAdmin },
    { title: "Run doctor", icon: "stethoscope", blocked: state.blocked.doctor, run: handlers.doctor },
  ];
}

/** The dot on the icon: the top state's tone whenever it asks for
 * something, none when every store is at HEAD and nothing waits. */
export function attentionTone(state: VaultStateResult): Tone | null {
  return state.primary.kind === "in_sync" ? null : state.primary.tone;
}

const TONES: Tone[] = ["red", "amber", "blue", "grey", "spinner"];

/** The left ribbon's artmind icon. Click opens the panel; right-click offers
 * the actions; a dot says the vault needs something. */
export class RibbonIcon {
  private el: HTMLElement;
  private state: VaultStateResult | null = null;
  private handlers: RibbonHandlers;

  constructor(el: HTMLElement, handlers: RibbonHandlers) {
    this.el = el;
    this.handlers = handlers;
    el.classList.add("artmind-ribbon");
    el.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      this.menu().showAtMouseEvent(event);
    });
  }

  menu(): Menu {
    const menu = new Menu();
    if (!this.state) {
      menu.addItem((item) => item.setTitle("Open side panel").setIcon("panel-right").onClick(() => this.handlers.openPanel()));
      return menu;
    }
    for (const entry of ribbonItems(this.state, this.handlers)) {
      menu.addItem((item) => {
        item.setTitle(entry.blocked ? `${entry.title} — ${entry.blocked}` : entry.title).setIcon(entry.icon);
        if (entry.blocked) item.setDisabled(true);
        else item.onClick(() => entry.run());
      });
    }
    return menu;
  }

  render(state: VaultStateResult): void {
    this.state = state;
    const tone = attentionTone(state);
    this.el.classList.toggle("artmind-ribbon-attention", tone !== null);
    for (const t of TONES) this.el.classList.toggle(`artmind-ribbon-${t}`, t === tone);
    this.el.setAttribute("aria-label", tone ? `artmind: ${state.primary.label}` : "artmind");
  }
}
