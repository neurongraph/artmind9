import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, NOTICE_LABELS, mergeSettings, validPort } from "../src/settings";

describe("settings (spec §7)", () => {
  it("has no commit offer any more: the footer replaces it (checklist spec §4)", () => {
    expect("offerCommitAndSync" in DEFAULT_SETTINGS).toBe(false);
    expect(DEFAULT_SETTINGS.adminInObsidianTab).toBe(true);
    expect(DEFAULT_SETTINGS.headPollSeconds).toBe(15);
    expect(DEFAULT_SETTINGS.jobPollSeconds).toBe(3);
    expect(DEFAULT_SETTINGS.newFileGroupSeconds).toBe(10);
    expect(Object.keys(DEFAULT_SETTINGS.notices).sort()).toEqual([
      "applyDone",
      "curateDone",
      "ingestDone",
      "newFiles",
      "pulled",
      "rebuildDone",
      "resolveDone",
      "synthesizeDone",
      "table2graphDone",
    ]);
    expect(Object.values(DEFAULT_SETTINGS.notices).every(Boolean)).toBe(true);
  });

  it("has a toggle label for every notice kind", () => {
    expect(Object.keys(NOTICE_LABELS).sort()).toEqual(Object.keys(DEFAULT_SETTINGS.notices).sort());
  });

  it("keeps saved values and defaults the rest", () => {
    const merged = mergeSettings({ artmindPath: "/opt/artmind", notices: { newFiles: false, rebuildDone: false }, offerCommitAndSync: false });
    expect(merged.artmindPath).toBe("/opt/artmind");
    expect(merged.notices.newFiles).toBe(false);
    expect(merged.notices.rebuildDone).toBe(false);
    expect(merged.notices.synthesizeDone).toBe(true);
    expect("offerCommitAndSync" in merged).toBe(false);
    expect(mergeSettings({ adminInObsidianTab: false }).adminInObsidianTab).toBe(false);
    expect(mergeSettings({ adminInObsidianTab: "yes" }).adminInObsidianTab).toBe(true);
    expect(merged.headPollSeconds).toBe(15);
  });

  it("leaves serve off by default, on an automatic port, with no pid claimed", () => {
    expect(DEFAULT_SETTINGS.serveEnabled).toBe(false);
    expect(DEFAULT_SETTINGS.servePort).toBe(0);
    expect(DEFAULT_SETTINGS.servePid).toBe(0);
    const merged = mergeSettings({ serveEnabled: true, servePort: 8390, servePid: 77 });
    expect([merged.serveEnabled, merged.servePort, merged.servePid]).toEqual([true, 8390, 77]);
  });

  it("refuses a serve port that is not a usable TCP port", () => {
    for (const port of [0, 80, 8377.5, 70000, "8390", -1]) expect(mergeSettings({ servePort: port }).servePort).toBe(0);
    expect(validPort(1024)).toBe(true);
    expect(validPort(65535)).toBe(true);
    expect(mergeSettings({ servePid: -3 }).servePid).toBe(0);
  });

  it("treats the old 8377 default, saved into every vault, as automatic", () => {
    expect(mergeSettings({ servePort: 8377 }).servePort).toBe(0);
    expect(mergeSettings({ servePort: 8390 }).servePort).toBe(8390);
  });

  it("ignores malformed data", () => {
    expect(mergeSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(mergeSettings({ headPollSeconds: "fast", notices: { pulled: "yes" } })).toEqual(DEFAULT_SETTINGS);
    expect(mergeSettings({ headPollSeconds: 1 }).headPollSeconds).toBe(5);
  });
});
