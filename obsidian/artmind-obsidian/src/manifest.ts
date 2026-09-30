/** Which vault paths `.artmind/vault.yaml` maps (docs/vault.md, "The ingest
 * manifest"), so a file landing outside every mapping never prompts. */

export const VAULT_YAML = ".artmind/vault.yaml";

/** File types whose arrival prompts "N new files … [Ingest now]" (spec §4.3).
 * Notes never prompt: Obsidian saves them every few seconds. */
export const PROMPTING_EXTENSIONS = [".pdf", ".pptx", ".docx", ".xlsx", ".csv"];

export interface Mapping {
  path: string;
  domain: string;
}

/** `ingest.mappings` from the manifest's text; `parse` is Obsidian's
 * `parseYaml`. A manifest that does not parse maps nothing. */
export function readMappings(text: string, parse: (text: string) => unknown): Mapping[] {
  let data: unknown;
  try {
    data = parse(text);
  } catch {
    return [];
  }
  const ingest = (data as { ingest?: { mappings?: unknown } } | null)?.ingest;
  const raw = Array.isArray(ingest?.mappings) ? ingest.mappings : [];
  return raw
    .filter((m): m is Mapping => Boolean(m && typeof m.path === "string" && typeof m.domain === "string"))
    .map((m) => ({ path: m.path, domain: m.domain }));
}

/** `PurePosixPath.full_match` semantics, which artmind's manifest uses: `*`
 * and `?` stay inside one path segment, a `**` segment matches any number
 * of segments (zero included). */
export function globToRegExp(glob: string): RegExp {
  const segments = glob.split("/");
  let pattern = "";
  segments.forEach((segment, index) => {
    const last = index === segments.length - 1;
    if (segment === "**") {
      pattern += last ? ".*" : "(?:[^/]+/)*";
      return;
    }
    for (const ch of segment) {
      if (ch === "*") pattern += "[^/]*";
      else if (ch === "?") pattern += "[^/]";
      else pattern += ch.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
    if (!last) pattern += "/";
  });
  return new RegExp(`^${pattern}$`);
}

/** The first mapping covering `relpath` (vault-relative), as artmind picks it. */
export function mappingFor(mappings: Mapping[], relpath: string): Mapping | null {
  return mappings.find((m) => globToRegExp(m.path).test(relpath)) ?? null;
}

/** A file whose arrival should prompt: a binary or table type, in a mapped
 * folder, not under a dot-folder or `_Inbox` (never walked by ingest). */
export function promptsOnArrival(mappings: Mapping[], relpath: string): boolean {
  const lower = relpath.toLowerCase();
  if (!PROMPTING_EXTENSIONS.some((ext) => lower.endsWith(ext))) return false;
  const parts = relpath.split("/");
  if (parts.some((part) => part.startsWith(".") || part === "_Inbox")) return false;
  return mappingFor(mappings, relpath) !== null;
}
