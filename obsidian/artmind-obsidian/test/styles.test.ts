import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(import.meta.dirname, "..", "styles.css"), "utf8");

describe("styles.css", () => {
  it("styles every class the checklist views add", () => {
    const classes = [
      "artmind-header-top",
      "artmind-header-vault",
      "artmind-refresh",
      "artmind-readiness",
      "artmind-checklist",
      "artmind-step",
      "artmind-step-current",
      "artmind-step-line",
      "artmind-step-glyph",
      "artmind-step-title",
      "artmind-step-summary",
      "artmind-step-buttons",
      "artmind-step-detail",
      "artmind-step-items",
      "artmind-step-item",
      "artmind-outline",
      "artmind-raised",
      "artmind-pending",
      "artmind-spinner",
      "artmind-stepper",
      "artmind-stepper-item",
      "artmind-stepper-done",
      "artmind-stepper-last",
      "artmind-step-marker",
      "artmind-step-marker-running",
      "artmind-step-optional",
      "artmind-progress",
      "artmind-progress-line",
      "artmind-progress-block",
      "artmind-progress-done",
      "artmind-footer",
      "artmind-notice-link",
      "artmind-confirm-text",
      "artmind-synth-domain",
      "artmind-synth-cap",
      "artmind-synth-total",
    ];
    expect(classes.filter((cls) => !CSS.includes(`.${cls}`))).toEqual([]);
  });

  it("drops the styles of the views that went", () => {
    for (const gone of ["artmind-health", "artmind-primary", "artmind-hint", "artmind-notice-buttons", ".artmind-state "]) {
      expect(CSS.includes(gone)).toBe(false);
    }
  });
});
