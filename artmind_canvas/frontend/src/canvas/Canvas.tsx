import {
  Background,
  Controls,
  ReactFlow,
  type Node,
  type NodeChange,
  type Viewport,
} from "@xyflow/react";
import { nodeTypes } from "../cards/registry";

type Props = {
  nodes: Node[];
  onNodesChange: (changes: NodeChange[]) => void;
  defaultViewport?: Viewport;
  onMoveEnd?: (viewport: Viewport) => void;
};

// The spatial substrate: a pan/zoom canvas hosting custom-node Cards, one per
// registered cardType (see cards/registry). App owns node state (so the Chat
// dock can spawn Cards and boards can hydrate/persist them); Canvas just
// renders. When a board carries a saved viewport we restore it and skip
// fitView; otherwise we frame the hydrated cards.
export default function Canvas({
  nodes,
  onNodesChange,
  defaultViewport,
  onMoveEnd,
}: Props) {
  return (
    <div className="canvas">
      <ReactFlow
        nodes={nodes}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        defaultViewport={defaultViewport}
        fitView={!defaultViewport}
        onMoveEnd={(_, viewport) => onMoveEnd?.(viewport)}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
