import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, NOTICE_LABELS, mergeSettings } from "../src/settings";

describe("settings (spec §7)", () => {
  it("offers commit-and-sync after artmind writes by default (P6)", () => {
    expect(DEFAULT_SETTINGS.offerCommitAndSync).toBe(true);
    expect(DEFAULT_SETTINGS.headPollSeconds).toBe(15);
    expect(DEFAULT_SETTINGS.jobPollSeconds).toBe(3);
    expect(DEFAULT_SETTINGS.newFileGroupSeconds).toBe(10);
    expect(Object.values(DEFAULT_SETTINGS.notices).every(Boolean)).toBe(true);
  });

  it("has a toggle label for every notice kind", () => {
    expect(Object.keys(NOTICE_LABELS).sort()).toEqual(Object.keys(DEFAULT_SETTINGS.notices).sort());
  });

  it("keeps saved values and defaults the rest", () => {
    const merged = mergeSettings({ artmindPath: "/opt/artmind", notices: { newFiles: false }, offerCommitAndSync: false });
    expect(merged.artmindPath).toBe("/opt/artmind");
    expect(merged.notices.newFiles).toBe(false);
    expect(merged.notices.syncDone).toBe(true);
    expect(merged.offerCommitAndSync).toBe(false);
    expect(merged.headPollSeconds).toBe(15);
  });

  it("ignores malformed data", () => {
    expect(mergeSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(mergeSettings({ headPollSeconds: "fast", notices: { pulled: "yes" } })).toEqual(DEFAULT_SETTINGS);
    expect(mergeSettings({ headPollSeconds: 1 }).headPollSeconds).toBe(5);
  });
});
