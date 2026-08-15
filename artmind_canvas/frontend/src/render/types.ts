// The client-side mirror of the canvas backend's event contract.
//
// artmind's shared backends emit 7 string-only events (base.py); the canvas
// backend adds one unclipped `render` event (ADR 0014) carrying a Card spec.
// We keep this loose (`[key: string]: unknown`) because the wire is JSON and
// the switch in ChatDock only reads the fields it knows.

export type UIEvent = {
  type: string;
  [key: string]: unknown;
};

// A `render` event's payload. `cardType` selects the client-owned Card
// contract; `props` is that Card's typed input. Phase 0 ships `document`.
export type CardSpec = {
  cardType: string;
  props?: Record<string, unknown>;
  html?: string;
};

export type DocumentCardProps = {
  vaultPath: string;
  blockId?: string | null;
};

export function isRenderEvent(
  event: UIEvent,
): event is UIEvent & { card: CardSpec } {
  return (
    event.type === "render" &&
    typeof event.card === "object" &&
    event.card !== null
  );
}
