import type { NodeTypes } from "@xyflow/react";
import DocumentCard from "./DocumentCard";

// The card registry: cardType → the React Flow node component that renders it.
// This is the single place a Card type is wired in; the substrate (Canvas) and
// the render sink (App.handleRender) both read from it, so adding a Card type
// is one entry here plus its props contract in render/types. Phase 1a ships
// `document`; provenance / micro-UI / graph-view land in later phases.
export const nodeTypes = {
  document: DocumentCard,
} satisfies NodeTypes;

export type KnownCardType = keyof typeof nodeTypes;

export function isKnownCardType(cardType: string): cardType is KnownCardType {
  return cardType in nodeTypes;
}
