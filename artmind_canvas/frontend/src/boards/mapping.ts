import type { Node } from "@xyflow/react";
import type { CardInstance } from "../render/types";
import { defaultSize } from "../cards/sizes";

// Translate between React Flow's `Node` (what the Canvas renders) and the
// board store's `CardInstance` (what we persist, ADR 0009). Card `props` ride
// on `node.data`; `filterSpec` is kept as a first-class field but stashed in
// `data` on the node so the Card component can read it.

export function nodeToCard(node: Node): CardInstance {
  const data = (node.data ?? {}) as Record<string, unknown>;
  const { filterSpec, ...props } = data;
  // Prefer the explicit dimensions NodeResizer writes (node.width/height, Phase
  // B) over the auto-measured box, so a user's resize round-trips exactly.
  const width = node.width ?? node.measured?.width;
  const height = node.height ?? node.measured?.height;
  return {
    id: node.id,
    cardType: node.type ?? "document",
    props: props as Record<string, unknown>,
    position: { x: node.position.x, y: node.position.y },
    size: width != null && height != null ? { width, height } : null,
    filterSpec: (filterSpec as Record<string, unknown> | undefined) ?? null,
  };
}

export function cardToNode(card: CardInstance): Node {
  // Restore the saved frame size onto node.width/height (the fields NodeResizer
  // and the node wrapper read); fall back to the per-type default so a legacy
  // card saved before Phase B still hydrates at a sensible size.
  const size = card.size ?? defaultSize(card.cardType);
  return {
    id: card.id,
    type: card.cardType,
    position: { x: card.position.x, y: card.position.y },
    width: size.width,
    height: size.height,
    data: {
      ...(card.props ?? {}),
      ...(card.filterSpec ? { filterSpec: card.filterSpec } : {}),
    },
  };
}
