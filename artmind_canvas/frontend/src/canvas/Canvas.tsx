import {
  Background,
  Controls,
  ReactFlow,
  type Node,
  type NodeChange,
  type NodeTypes,
} from "@xyflow/react";
import DocumentCard from "../cards/DocumentCard";

// Registered once at module scope — React Flow warns (and re-mounts nodes)
// if nodeTypes is a fresh object each render.
const nodeTypes: NodeTypes = { document: DocumentCard };

type Props = {
  nodes: Node[];
  onNodesChange: (changes: NodeChange[]) => void;
};

// The spatial substrate: a pan/zoom canvas hosting custom-node Cards. App
// owns node state so the Chat dock can spawn Cards; Canvas just renders.
export default function Canvas({ nodes, onNodesChange }: Props) {
  return (
    <div className="canvas">
      <ReactFlow
        nodes={nodes}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
