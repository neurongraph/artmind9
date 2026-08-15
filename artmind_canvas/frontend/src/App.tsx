import { useCallback, useRef } from "react";
import { ReactFlowProvider, useNodesState, type Node } from "@xyflow/react";
import Canvas from "./canvas/Canvas";
import ChatDock from "./chat/ChatDock";
import type { CardSpec, DocumentCardProps } from "./render/types";

function AppInner() {
  const sessionId = useRef(crypto.randomUUID()).current;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const spawnCount = useRef(0);

  // A `render` event → a Card on the Canvas. Phase 0 knows one cardType;
  // unknown types are ignored (later phases register more).
  const handleRender = useCallback(
    (card: CardSpec) => {
      if (card.cardType !== "document") return;
      const props = card.props as DocumentCardProps | undefined;
      if (!props?.vaultPath) return;
      const n = spawnCount.current++;
      const node: Node = {
        id: `document-${n}-${props.vaultPath}`,
        type: "document",
        position: { x: 80 + n * 48, y: 80 + n * 48 },
        data: { ...props },
      };
      setNodes((prev) => [...prev, node]);
    },
    [setNodes],
  );

  return (
    <div className="app">
      <ChatDock sessionId={sessionId} onRender={handleRender} />
      <Canvas nodes={nodes} onNodesChange={onNodesChange} />
    </div>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <AppInner />
    </ReactFlowProvider>
  );
}
