import type { NodeTypes } from "@xyflow/react";
import DocumentCard from "./DocumentCard";
import ProvenanceCard from "./ProvenanceCard";

// The card registry: cardType → the React Flow node component that renders it.
// This is the single place a Card type is wired in; the substrate (Canvas) and
// the render sink (App.handleRender) both read from it, so adding a Card type
// is one entry here plus its props contract in render/types. Phase 1a ships
// `document`; Phase 2 adds `provenance`; micro-UI / graph-view land later.
export const nodeTypes = {
  document: DocumentCard,
  provenance: ProvenanceCard,
} satisfies NodeTypes;

export type KnownCardType = keyof typeof nodeTypes;

export function isKnownCardType(cardType: string): cardType is KnownCardType {
  return cardType in nodeTypes;
}
