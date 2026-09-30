import { describe, expect, it } from "vitest";
import { MIN_ARTMIND_VERSION, atLeast, formatVersion, parseVersion } from "../src/version";

describe("version", () => {
  it("parses what `artmind --version` prints", () => {
    expect(parseVersion("artmind 0.9.0\n")).toEqual([0, 9, 0]);
    expect(parseVersion("artmind 12.3.45")).toEqual([12, 3, 45]);
  });

  it("rejects anything else", () => {
    expect(parseVersion("")).toBeNull();
    expect(parseVersion("Usage: artmind [OPTIONS]")).toBeNull();
    expect(parseVersion("artmind, version 0.9.0")).toBeNull();
  });

  it("compares against the minimum numerically, not as text", () => {
    expect(atLeast([0, 9, 0], "0.9.0")).toBe(true);
    expect(atLeast([0, 10, 0], "0.9.0")).toBe(true);
    expect(atLeast([1, 0, 0], "0.9.0")).toBe(true);
    expect(atLeast([0, 8, 9], "0.9.0")).toBe(false);
    expect(atLeast([0, 9, 0], "0.9.1")).toBe(false);
  });

  it("needs the artmind that has --version and the pending commands", () => {
    expect(MIN_ARTMIND_VERSION).toBe("0.9.0");
    expect(formatVersion([0, 9, 0])).toBe("0.9.0");
  });
});
