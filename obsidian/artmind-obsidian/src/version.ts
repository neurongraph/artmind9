/** The oldest artmind this plugin works with: the first with `--version`,
 * `ingest pending`, `ingest async --pending` and `table2graph --pending`
 * (spec 2026-09-30 §6 X1–X4). */
export const MIN_ARTMIND_VERSION = "0.9.0";

export type Version = [number, number, number];

/** `artmind 0.9.0` (what `artmind --version` prints) -> [0, 9, 0]. */
export function parseVersion(stdout: string): Version | null {
  const match = /^artmind (\d+)\.(\d+)\.(\d+)\s*$/m.exec(stdout);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

export function atLeast(found: Version, minimum: string): boolean {
  const need = minimum.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if (found[i] !== need[i]) return found[i] > need[i];
  }
  return true;
}

export function formatVersion(version: Version): string {
  return version.join(".");
}
