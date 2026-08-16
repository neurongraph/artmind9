// Global UI settings client (ADR 0015). Panel geometry (width + collapsed) is
// *global chrome*, not per-board — switching boards must not make the sidebars
// lurch. `width: null` means "use the client default".

export type PanelState = { width: number | null; collapsed: boolean };
export type Settings = { chat: PanelState; editor: PanelState };
export type SettingsPatch = { chat?: PanelState; editor?: PanelState };

export async function getSettings(): Promise<Settings> {
  const res = await fetch("/api/settings");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Settings;
}

export async function saveSettings(patch: SettingsPatch): Promise<Settings> {
  const res = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Settings;
}
