import { Menu } from "obsidian";
import type { Checklist } from "../checklist";
import type { Tone } from "../state";
import { COMMANDS } from "../texts";

export interface RibbonHandlers {
  openPanel(): void;
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

/** The right-click menu (checklist spec §4): no row action is here; they
 * are the panel's and the palette's. */
export function ribbonItems(cl: Checklist, handlers: RibbonHandlers): RibbonItem[] {
  return [
    { title: COMMANDS.openPanel, icon: "panel-right", blocked: null, run: handlers.openPanel },
    { title: COMMANDS.openAdmin, icon: "external-link", blocked: cl.blocked.admin, run: handlers.openAdmin },
    { title: COMMANDS.doctor, icon: "stethoscope", blocked: cl.blocked.doctor, run: handlers.doctor },
  ];
}

/** The dot on the icon: the current step's tone, none when nothing is left. */
export function attentionTone(cl: Checklist): Tone | null {
  return cl.rows.find((r) => r.id === cl.current)?.tone ?? null;
}

const TONES: Tone[] = ["red", "amber", "blue", "grey", "spinner"];

/** The left ribbon's artmind icon. A click opens the panel (D8); right-click
 * offers the panel, the admin console and the doctor. */
export class RibbonIcon {
  private el: HTMLElement;
  private state: Checklist | null = null;
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
      menu.addItem((item) => item.setTitle(COMMANDS.openPanel).setIcon("panel-right").onClick(() => this.handlers.openPanel()));
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

  render(cl: Checklist): void {
    this.state = cl;
    const tone = attentionTone(cl);
    this.el.classList.toggle("artmind-ribbon-attention", tone !== null);
    for (const t of TONES) this.el.classList.toggle(`artmind-ribbon-${t}`, t === tone);
    const current = cl.rows.find((r) => r.id === cl.current);
    this.el.setAttribute("aria-label", current ? `artmind: ${current.summary}` : "artmind");
  }
}
