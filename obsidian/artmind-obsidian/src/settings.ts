/** The plugin's settings (spec §7), stored by Obsidian in
 * `.obsidian/plugins/artmind/data.json`. */

export type NoticeKind = "pulled" | "newFiles" | "ingestDone" | "syncDone" | "table2graphDone" | "resolveDone";

export interface ArtmindSettings {
  /** Path to the `artmind` executable; empty means auto-detect. */
  artmindPath: string;
  headPollSeconds: number;
  jobPollSeconds: number;
  newFileGroupSeconds: number;
  notices: Record<NoticeKind, boolean>;
  /** Offer Obsidian Git's Commit-and-sync after artmind writes (spec P6). */
  offerCommitAndSync: boolean;
  /** Where the admin console (`artmind admin-ui`) is found or started; its default port. */
  adminUiUrl: string;
  /** The admin console this plugin started (0: none), so Stop never stops
   * one started elsewhere. Per machine, like the rest of this file. */
  adminUiPid: number;
}

export const DEFAULT_SETTINGS: ArtmindSettings = {
  artmindPath: "",
  headPollSeconds: 15,
  jobPollSeconds: 3,
  newFileGroupSeconds: 10,
  notices: {
    pulled: true,
    newFiles: true,
    ingestDone: true,
    syncDone: true,
    table2graphDone: true,
    resolveDone: true,
  },
  offerCommitAndSync: true,
  adminUiUrl: "http://127.0.0.1:8379",
  adminUiPid: 0,
};

export const NOTICE_LABELS: Record<NoticeKind, string> = {
  pulled: "A pull left a store behind",
  newFiles: "A binary or table lands in a mapped folder",
  ingestDone: "An ingest job finishes",
  syncDone: "Sync finishes",
  table2graphDone: "table2graph finishes",
  resolveDone: "vault resolve finishes",
};

/** Saved data over the defaults; a missing or malformed field keeps its default. */
export function mergeSettings(saved: unknown): ArtmindSettings {
  const data = (saved && typeof saved === "object" ? saved : {}) as Partial<ArtmindSettings>;
  const pick = <K extends keyof ArtmindSettings>(key: K): ArtmindSettings[K] =>
    typeof data[key] === typeof DEFAULT_SETTINGS[key] ? (data[key] as ArtmindSettings[K]) : DEFAULT_SETTINGS[key];
  const notices = { ...DEFAULT_SETTINGS.notices };
  for (const kind of Object.keys(notices) as NoticeKind[]) {
    const value = (data.notices as Partial<Record<NoticeKind, unknown>> | undefined)?.[kind];
    if (typeof value === "boolean") notices[kind] = value;
  }
  return {
    artmindPath: pick("artmindPath"),
    headPollSeconds: Math.max(5, pick("headPollSeconds")),
    jobPollSeconds: Math.max(1, pick("jobPollSeconds")),
    newFileGroupSeconds: Math.max(1, pick("newFileGroupSeconds")),
    notices,
    offerCommitAndSync: pick("offerCommitAndSync"),
    adminUiUrl: pick("adminUiUrl"),
    adminUiPid: Math.max(0, Math.trunc(pick("adminUiPid"))),
  };
}
