import { useCallback, useEffect, useRef, useState } from "react";
import {
  ReactFlowProvider,
  useNodesState,
  type Node,
  type NodeChange,
  type Viewport,
} from "@xyflow/react";
import Canvas from "./canvas/Canvas";
import ChatDock from "./chat/ChatDock";
import BoardBar from "./boards/BoardBar";
import { isKnownCardType } from "./cards/registry";
import { cardToNode, nodeToCard } from "./boards/mapping";
import { createBoard, getBoard, listBoards, saveBoard } from "./boards/api";
import type { BoardSummary, CardSpec } from "./render/types";

const SAVE_DEBOUNCE_MS = 600;

function AppInner() {
  const sessionId = useRef(crypto.randomUUID()).current;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [boardId, setBoardId] = useState<string | null>(null);
  const [boardName, setBoardName] = useState("");
  const [viewport, setViewport] = useState<Viewport | undefined>(undefined);

  // Refs mirror state for use inside the debounced save (which fires later,
  // when the closure's captured state would be stale).
  const spawnCount = useRef(0);
  const nodesRef = useRef<Node[]>(nodes);
  nodesRef.current = nodes;
  const boardIdRef = useRef<string | null>(null);
  boardIdRef.current = boardId;
  const viewportRef = useRef<Viewport | undefined>(undefined);
  const saveTimer = useRef<number | null>(null);

  const refreshBoards = useCallback(async () => {
    setBoards(await listBoards());
  }, []);

  // Persist the active board's arrangement. Debounced: drags/zooms fire many
  // changes, but we only PUT once they settle.
  const flushSave = useCallback(() => {
    const id = boardIdRef.current;
    if (!id) return;
    void saveBoard(id, {
      cards: nodesRef.current.map(nodeToCard),
      viewport: viewportRef.current,
    }).catch((err) => console.error("board save failed", err));
  }, []);

  const scheduleSave = useCallback(() => {
    if (!boardIdRef.current) return;
    if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(flushSave, SAVE_DEBOUNCE_MS);
  }, [flushSave]);

  const loadBoard = useCallback(
    async (id: string) => {
      const board = await getBoard(id);
      setBoardId(board.id);
      setBoardName(board.name);
      setNodes(board.cards.map(cardToNode));
      const vp = board.viewport ?? undefined;
      viewportRef.current = vp;
      setViewport(vp);
      spawnCount.current = board.cards.length;
    },
    [setNodes],
  );

  // Bootstrap: open the most-recently-updated board, creating one if none exist.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        let list = await listBoards();
        if (list.length === 0) {
          const created = await createBoard("My board");
          list = [{ id: created.id, name: created.name, updatedAt: created.updatedAt }];
        }
        if (cancelled) return;
        await loadBoard(list[0].id);
        if (!cancelled) setBoards(list);
      } catch (err) {
        console.error("board bootstrap failed", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadBoard]);

  // React Flow user interactions (drag/select/resize) → apply + persist.
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      onNodesChange(changes);
      scheduleSave();
    },
    [onNodesChange, scheduleSave],
  );

  const handleMoveEnd = useCallback(
    (vp: Viewport) => {
      viewportRef.current = vp;
      scheduleSave();
    },
    [scheduleSave],
  );

  // A `render` event → a Card on the Canvas. Any registered cardType is
  // spawned; unknown types are ignored (later phases register more).
  const handleRender = useCallback(
    (card: CardSpec) => {
      if (!isKnownCardType(card.cardType)) return;
      const n = spawnCount.current++;
      const node: Node = {
        id: crypto.randomUUID(),
        type: card.cardType,
        position: { x: 80 + (n % 6) * 44, y: 80 + (n % 6) * 44 },
        data: { ...(card.props ?? {}) },
      };
      setNodes((prev) => [...prev, node]);
      scheduleSave();
    },
    [setNodes, scheduleSave],
  );

  const handleCreateBoard = useCallback(async () => {
    const created = await createBoard("Untitled board");
    await loadBoard(created.id);
    await refreshBoards();
  }, [loadBoard, refreshBoards]);

  const handleRename = useCallback(
    async (name: string) => {
      const id = boardIdRef.current;
      if (!id || !name.trim()) return;
      setBoardName(name);
      await saveBoard(id, { name });
      await refreshBoards();
    },
    [refreshBoards],
  );

  return (
    <div className="app">
      <ChatDock sessionId={sessionId} onRender={handleRender} />
      <div className="canvas-col">
        <BoardBar
          boards={boards}
          boardId={boardId}
          boardName={boardName}
          onSwitch={loadBoard}
          onCreate={handleCreateBoard}
          onRename={handleRename}
        />
        <Canvas
          key={boardId ?? "none"}
          nodes={nodes}
          onNodesChange={handleNodesChange}
          defaultViewport={viewport}
          onMoveEnd={handleMoveEnd}
        />
      </div>
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
