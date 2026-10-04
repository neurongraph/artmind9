import { type App, type Plugin, PluginSettingTab, Setting } from "obsidian";
import { type ArtmindSettings, NOTICE_LABELS, type NoticeKind } from "../settings";
import { el } from "./dom";

export interface SettingsHost extends Plugin {
  artmindSettings: ArtmindSettings;
  saveSettings(): Promise<void>;
}

/** Settings → artmind (spec §7): the artmind path, poll intervals, the admin console, and a toggle per notice kind. */
export class ArtmindSettingTab extends PluginSettingTab {
  private host: SettingsHost;

  constructor(app: App, host: SettingsHost) {
    super(app, host);
    this.host = host;
  }

  override display(): void {
    const { containerEl } = this;
    containerEl.replaceChildren();
    const settings = this.host.artmindSettings;
    const save = () => void this.host.saveSettings();

    new Setting(containerEl)
      .setName("artmind path")
      .setDesc("The artmind executable. Empty: ~/.local/bin/artmind, then `uv tool dir`. Obsidian started from the Dock does not see your shell's PATH.")
      .addText((text) =>
        text.setPlaceholder("auto-detect").setValue(settings.artmindPath).onChange((value) => {
          settings.artmindPath = value.trim();
          save();
        }),
      );

    const seconds = (name: string, desc: string, key: "headPollSeconds" | "jobPollSeconds" | "newFileGroupSeconds") =>
      new Setting(containerEl)
        .setName(name)
        .setDesc(desc)
        .addText((text) =>
          text.setValue(String(settings[key])).onChange((value) => {
            const n = Number.parseInt(value, 10);
            if (Number.isFinite(n) && n > 0) {
              settings[key] = n;
              save();
            }
          }),
        );
    seconds("HEAD poll (seconds)", "How often to check whether a pull moved HEAD. Takes effect after a reload.", "headPollSeconds");
    seconds("Job poll (seconds)", "How often to read a running ingest job. Takes effect after a reload.", "jobPollSeconds");
    seconds("New-file grouping (seconds)", "Arrivals this close together share one notice. Takes effect after a reload.", "newFileGroupSeconds");

    new Setting(containerEl)
      .setName("Admin console URL")
      .setDesc(
        "Where this vault's admin console (artmind admin-ui) is found, or started when nothing answers there. " +
          "Give each vault its own port to run several at once.",
      )
      .addText((text) =>
        text.setValue(settings.adminUiUrl).onChange((value) => {
          settings.adminUiUrl = value.trim();
          save();
        }),
      );

    new Setting(containerEl)
      .setName("Open the admin console in an Obsidian tab")
      .setDesc("Uses the Web viewer core plugin. When it's off, the admin console opens in your browser.")
      .addToggle((toggle) =>
        toggle.setValue(settings.adminInObsidianTab).onChange((value) => {
          settings.adminInObsidianTab = value;
          save();
        }),
      );

    el(containerEl, "h3", { text: "Notices" });
    for (const kind of Object.keys(NOTICE_LABELS) as NoticeKind[]) {
      new Setting(containerEl).setName(NOTICE_LABELS[kind]).addToggle((toggle) =>
        toggle.setValue(settings.notices[kind]).onChange((value) => {
          settings.notices[kind] = value;
          save();
        }),
      );
    }
  }
}
