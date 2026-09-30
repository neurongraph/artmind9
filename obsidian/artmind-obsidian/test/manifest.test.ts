import { parse } from "yaml";
import { describe, expect, it } from "vitest";
import { globToRegExp, mappingFor, promptsOnArrival, readMappings } from "../src/manifest";

const YAML = `
vault_id: "abc"
ingest:
  trigger: manual
  mappings:
    - path: "Team_performance_management/**"
      domain: banking
    - path: "Notes/*.md"
      domain: general
`;

describe("manifest", () => {
  const mappings = readMappings(YAML, parse);

  it("reads ingest.mappings", () => {
    expect(mappings).toEqual([
      { path: "Team_performance_management/**", domain: "banking" },
      { path: "Notes/*.md", domain: "general" },
    ]);
  });

  it("maps nothing when the manifest does not parse or has no mappings", () => {
    expect(readMappings("ingest: [", parse)).toEqual([]);
    expect(readMappings("vault_id: x\n", parse)).toEqual([]);
  });

  it("matches like PurePosixPath.full_match", () => {
    expect(globToRegExp("a/**").test("a/b.pdf")).toBe(true);
    expect(globToRegExp("a/**").test("a/b/c.pdf")).toBe(true);
    expect(globToRegExp("a/*.md").test("a/b.md")).toBe(true);
    expect(globToRegExp("a/*.md").test("a/b/c.md")).toBe(false);
    expect(globToRegExp("a/**/x.csv").test("a/x.csv")).toBe(true);
    expect(globToRegExp("a/**/x.csv").test("a/b/c/x.csv")).toBe(true);
    expect(globToRegExp("a.b/?.md").test("a.b/c.md")).toBe(true);
    expect(globToRegExp("a.b/?.md").test("aXb/c.md")).toBe(false);
  });

  it("first match wins", () => {
    expect(mappingFor(mappings, "Team_performance_management/q3/report.pdf")?.domain).toBe("banking");
    expect(mappingFor(mappings, "Elsewhere/report.pdf")).toBeNull();
  });

  it("prompts only for a binary or table in a mapped folder", () => {
    expect(promptsOnArrival(mappings, "Team_performance_management/deck.PPTX")).toBe(true);
    expect(promptsOnArrival(mappings, "Team_performance_management/export.xlsx")).toBe(true);
    expect(promptsOnArrival(mappings, "Team_performance_management/notes.md")).toBe(false);
    expect(promptsOnArrival(mappings, "Team_performance_management/image.png")).toBe(false);
    expect(promptsOnArrival(mappings, "Unmapped/deck.pdf")).toBe(false);
    expect(promptsOnArrival(mappings, "Team_performance_management/_Inbox/deck.pdf")).toBe(false);
    expect(promptsOnArrival(mappings, "Team_performance_management/.hidden/deck.pdf")).toBe(false);
  });
});
