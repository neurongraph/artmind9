// The client-side mirror of the canvas backend's event + board contracts.
//
// artmind's shared backends emit 7 string-only events (base.py); the canvas
// backend adds one unclipped `render` event (ADR 0014) carrying a Card spec.
// We keep UIEvent loose (`[key: string]: unknown`) because the wire is JSON and
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

// ---- Card contracts (ADR 0014) ----------------------------------------
// Each first-class Card type publishes the shape of the `props` it accepts.
// Client-owned, but the shape is what the agent/backend emit.
export type DocumentCardProps = {
  vaultPath: string;
  blockId?: string | null;
};

// `provenance` Card — shows where a graph fact came from. Supply `entityId`
// when known, else a free-text `reference` the backend resolves first. `domains`
// scopes the read (artmind requires at least one). ADR 0014.
export type ProvenanceCardProps = {
  domains: string[];
  entityId?: string | null;
  reference?: string | null;
  includeChunks?: number | null;
  title?: string | null;
};

// The backend's normalized provenance payload (routes/provenance.py `_shape`).
export type ProvenanceSource = {
  chunkId: string | null;
  chunkName: string | null;
  docId: string | null;
  domain: string | null;
  validTo: string | null;
  snippet: string | null;
  document: {
    id: string | null;
    name: string | null;
    domain: string | null;
    supersededBy: string | null;
  };
};

export type ProvenanceResult = {
  entity: {
    id: string | null;
    name: string | null;
    entityClass: string | null;
    type: string | null;
    description: string | null;
    domain: string | null;
  };
  sources: ProvenanceSource[];
  moreCount: number;
  chatSources: { id: string | null; snippet: string | null; createdAt: string | null }[];
  resolvedFrom: string | null;
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

// ---- Board / application state (ADR 0009) ------------------------------
// A Board is a named saved arrangement: which Cards are open, their
// positions/sizes, each Card's filter spec, and the viewport. This mirrors
// the backend board_store models; it is application state, not knowledge.

export type XY = { x: number; y: number };
export type Size = { width: number; height: number };
export type Viewport = { x: number; y: number; zoom: number };

export type CardInstance = {
  id: string;
  cardType: string;
  props?: Record<string, unknown>;
  position: XY;
  size?: Size | null;
  filterSpec?: Record<string, unknown> | null;
};

export type BoardSummary = {
  id: string;
  name: string;
  updatedAt: string;
};

export type Board = {
  id: string;
  name: string;
  cards: CardInstance[];
  viewport?: Viewport | null;
  // Editor pane state (ADR 0015): Vault docs open on this board + active tab.
  openDocuments: string[];
  activeDocument: string | null;
  createdAt: string;
  updatedAt: string;
};
