import type { KnownCardType } from "./registry";

// Per-type Card dimensions, the single source of truth shared by the spawn path
// (App.handleRender seeds `node.width/height`) and the persistence round-trip
// (boards/mapping falls back here when a saved instance has no size). Kept out of
// CSS so resize (which writes `node.width/height`) and the stylesheet never fight
// over the frame size — the shell fills the node wrapper at 100%.
export type CardSize = { width: number; height: number };

const DEFAULT_SIZE: Record<KnownCardType, CardSize> = {
  document: { width: 340, height: 420 },
  provenance: { width: 360, height: 420 },
  "micro-ui": { width: 360, height: 300 },
  placement: { width: 380, height: 480 },
  "graph-view": { width: 520, height: 460 },
};

const MIN_SIZE: Record<KnownCardType, CardSize> = {
  document: { width: 240, height: 160 },
  provenance: { width: 260, height: 180 },
  "micro-ui": { width: 240, height: 160 },
  placement: { width: 300, height: 220 },
  "graph-view": { width: 340, height: 300 },
};

const FALLBACK: CardSize = { width: 340, height: 420 };
const FALLBACK_MIN: CardSize = { width: 220, height: 140 };

export function defaultSize(cardType: string): CardSize {
  return DEFAULT_SIZE[cardType as KnownCardType] ?? FALLBACK;
}

export function minSize(cardType: string): CardSize {
  return MIN_SIZE[cardType as KnownCardType] ?? FALLBACK_MIN;
}
