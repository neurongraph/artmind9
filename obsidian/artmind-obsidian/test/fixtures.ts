import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** One captured CLI run (artmind's test/test_plugin_fixtures.py writes these). */
export interface Fixture {
  args: string[];
  exit_code: number;
  json: any;
  stderr: string | null;
}

/** A path, not a URL: under jsdom, `import.meta.url` is not a file URL. */
const DIR = join(import.meta.dirname, "fixtures");

export function fixture(name: string): Fixture {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8"));
}

export function fixtureNames(): string[] {
  return readdirSync(DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.slice(0, -".json".length))
    .sort();
}
