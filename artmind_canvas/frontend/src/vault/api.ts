// Vault file client (ADR 0015). The editor reads a markdown file with its
// version (a sha256 of the bytes), and writes it back *conditionally*: the
// PUT carries the baseVersion it read, and the backend rejects the write with
// 409 if the file changed underneath (clobber-safety). Creating a new file
// sends baseVersion=null; the backend 409s if something already lives there.

export type VaultFile = { path: string; content: string; version: string };

export type WriteResult =
  | { ok: true; version: string }
  | { ok: false; conflict: true; currentVersion: string | null }
  | { ok: false; conflict: false; message: string };

export type VaultListing = { dirs: string[]; files: string[] };

export async function readVaultFile(path: string): Promise<VaultFile> {
  const res = await fetch(`/api/vault/file?path=${encodeURIComponent(path)}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as VaultFile;
}

// Conditional write. `baseVersion` is the version last read (null to create a
// new file). Returns a discriminated result rather than throwing on 409, so
// callers can surface the conflict as a resolvable UI state.
export async function writeVaultFile(
  path: string,
  content: string,
  baseVersion: string | null,
): Promise<WriteResult> {
  const res = await fetch("/api/vault/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content, baseVersion }),
  });
  if (res.ok) {
    const body = (await res.json()) as { version: string };
    return { ok: true, version: body.version };
  }
  const body = await res.json().catch(() => ({}));
  if (res.status === 409) {
    const detail = (body.detail ?? {}) as { currentVersion?: string | null };
    return { ok: false, conflict: true, currentVersion: detail.currentVersion ?? null };
  }
  const message =
    typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
  return { ok: false, conflict: false, message };
}

export async function listVaultDir(dir = ""): Promise<VaultListing> {
  const res = await fetch(`/api/vault/list?dir=${encodeURIComponent(dir)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as VaultListing;
}
