import type { Node } from "@xyflow/react";
import type { CardInstance } from "../render/types";

// Translate between React Flow's `Node` (what the Canvas renders) and the
// board store's `CardInstance` (what we persist, ADR 0009). Card `props` ride
// on `node.data`; `filterSpec` is kept as a first-class field but stashed in
// `data` on the node so the Card component can read it.

export function nodeToCard(node: Node): CardInstance {
  const data = (node.data ?? {}) as Record<string, unknown>;
  const { filterSpec, ...props } = data;
  const measured = node.measured;
  return {
    id: node.id,
    cardType: node.type ?? "document",
    props: props as Record<string, unknown>,
    position: { x: node.position.x, y: node.position.y },
    size:
      measured?.width != null && measured?.height != null
        ? { width: measured.width, height: measured.height }
        : null,
    filterSpec: (filterSpec as Record<string, unknown> | undefined) ?? null,
  };
}

export function cardToNode(card: CardInstance): Node {
  return {
    id: card.id,
    type: card.cardType,
    position: { x: card.position.x, y: card.position.y },
    data: {
      ...(card.props ?? {}),
      ...(card.filterSpec ? { filterSpec: card.filterSpec } : {}),
    },
  };
}
