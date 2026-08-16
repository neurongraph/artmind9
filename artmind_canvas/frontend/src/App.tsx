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
import EditorPane from "./editor/EditorPane";
import { WorkspaceContext, basename, type DocTab } from "./editor/workspace";
import { isKnownCardType } from "./cards/registry";
import { cardToNode, nodeToCard } from "./boards/mapping";
import { createBoard, getBoard, listBoards, saveBoard } from "./boards/api";
import { getSettings, saveSettings, type PanelState } from "./settings/api";
import type { BoardSummary, CardSpec } from "./render/types";

const SAVE_DEBOUNCE_MS = 600;
const SETTINGS_DEBOUNCE_MS = 400;

const CHAT_DEFAULT = 380;
const CHAT_MIN = 280;
const CHAT_MAX = 640;
const EDITOR_DEFAULT = 520;
const EDITOR_MIN = 360;
const EDITOR_MAX = 960;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

function AppInner() {
  const sessionId = useRef(crypto.randomUUID()).current;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [boardId, setBoardId] = useState<string | null>(null);
  const [boardName, setBoardName] = useState("");
  const [viewport, setViewport] = useState<Viewport | undefined>(undefined);

  // Editor pane tabs (per-board application state, ADR 0015).
  const [tabs, setTabs] = useState<DocTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);

  // Panel geometry (global chrome, ADR 0015 — settings.json, not the board).
  const [chatWidth, setChatWidth] = useState(CHAT_DEFAULT);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  const [editorWidth, setEditorWidth] = useState(EDITOR_DEFAULT);
  const [editorCollapsed, setEditorCollapsed] = useState(true);

  // Refs mirror state for the debounced savers (they fire later, when the
  // closure's captured state would be stale).
  const spawnCount = useRef(0);
  const nodesRef = useRef<Node[]>(nodes);
  nodesRef.current = nodes;
  const tabsRef = useRef<DocTab[]>(tabs);
  tabsRef.current = tabs;
  const activeTabIdRef = useRef<string | null>(activeTabId);
  activeTabIdRef.current = activeTabId;
  const boardIdRef = useRef<string | null>(null);
  boardIdRef.current = boardId;
  const viewportRef = useRef<Viewport | undefined>(undefined);
  const saveTimer = useRef<number | null>(null);
  const settingsTimer = useRef<number | null>(null);

  const refreshBoards = useCallback(async () => {
    setBoards(await listBoards());
  }, []);

  // Persist the active board's arrangement (cards + viewport + open docs).
  // Debounced: drags/zooms/tab changes fire many events; we PUT once settled.
  const flushSave = useCallback(() => {
    const id = boardIdRef.current;
    if (!id) return;
    const activeTab = tabsRef.current.find((t) => t.id === activeTabIdRef.current);
    void saveBoard(id, {
      cards: nodesRef.current.map(nodeToCard),
      viewport: viewportRef.current,
      openDocuments: tabsRef.current.filter((t) => t.path).map((t) => t.path as string),
      activeDocument: activeTab?.path ?? null,
    }).catch((err) => console.error("board save failed", err));
  }, []);

  const scheduleSave = useCallback(() => {
    if (!boardIdRef.current) return;
    if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(flushSave, SAVE_DEBOUNCE_MS);
  }, [flushSave]);

  // Panel geometry saver (global settings.json).
  const chatRef = useRef<PanelState>({ width: chatWidth, collapsed: chatCollapsed });
  chatRef.current = { width: chatWidth, collapsed: chatCollapsed };
  const editorRef = useRef<PanelState>({ width: editorWidth, collapsed: editorCollapsed });
  editorRef.current = { width: editorWidth, collapsed: editorCollapsed };
  const settingsReady = useRef(false);

  const scheduleSettingsSave = useCallback(() => {
    if (!settingsReady.current) return; // don't echo back the values we just loaded
    if (settingsTimer.current != null) window.clearTimeout(settingsTimer.current);
    settingsTimer.current = window.setTimeout(() => {
      void saveSettings({ chat: chatRef.current, editor: editorRef.current }).catch((err) =>
        console.error("settings save failed", err),
      );
    }, SETTINGS_DEBOUNCE_MS);
  }, []);

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
      const openDocs = board.openDocuments ?? [];
      setTabs(openDocs.map((p) => ({ id: p, path: p, title: basename(p) })));
      setActiveTabId(board.activeDocument ?? (openDocs.length ? openDocs[openDocs.length - 1] : null));
    },
    [setNodes],
  );

  // Bootstrap: load global settings + open the most-recent board (create if none).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const settings = await getSettings();
        if (!cancelled) {
          setChatWidth(settings.chat.width ?? CHAT_DEFAULT);
          setChatCollapsed(settings.chat.collapsed);
          setEditorWidth(settings.editor.width ?? EDITOR_DEFAULT);
          setEditorCollapsed(settings.editor.collapsed);
        }
      } catch (err) {
        console.error("settings load failed", err);
      } finally {
        settingsReady.current = true;
      }
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

  // ---- Editor workspace surface (WorkspaceContext) ----------------------
  const expandEditor = useCallback(() => {
    setEditorCollapsed(false);
    scheduleSettingsSave();
  }, [scheduleSettingsSave]);

  const openDocument = useCallback(
    (path: string) => {
      const existing = tabsRef.current.find((t) => t.path === path);
      if (!existing) {
        setTabs((prev) => [...prev, { id: path, path, title: basename(path) }]);
      }
      setActiveTabId(existing ? existing.id : path);
      expandEditor();
      scheduleSave();
    },
    [expandEditor, scheduleSave],
  );

  const openDraft = useCallback(
    (seed: string, title?: string) => {
      const id = `draft:${crypto.randomUUID()}`;
      setTabs((prev) => [...prev, { id, path: null, title: title ?? "Untitled", seed }]);
      setActiveTabId(id);
      expandEditor();
    },
    [expandEditor],
  );

  const handleSelectTab = useCallback(
    (id: string) => {
      setActiveTabId(id);
      scheduleSave();
    },
    [scheduleSave],
  );

  const handleCloseTab = useCallback(
    (id: string) => {
      setTabs((prev) => {
        const next = prev.filter((t) => t.id !== id);
        setActiveTabId((cur) =>
          cur === id ? (next.length ? next[next.length - 1].id : null) : cur,
        );
        return next;
      });
      scheduleSave();
    },
    [scheduleSave],
  );

  // A draft was saved to a real Vault path: promote the tab to a file tab. If
  // that path is already open elsewhere, drop the draft and focus the original.
  const handleDraftSaved = useCallback(
    (id: string, path: string) => {
      setTabs((prev) => {
        const existing = prev.find((t) => t.path === path && t.id !== id);
        if (existing) {
          setActiveTabId(existing.id);
          return prev.filter((t) => t.id !== id);
        }
        return prev.map((t) => (t.id === id ? { ...t, path, title: basename(path) } : t));
      });
      scheduleSave();
    },
    [scheduleSave],
  );

  // ---- Panel resize (pointer drag on the inner border) ------------------
  const startResize = useCallback(
    (e: React.PointerEvent, panel: "chat" | "editor") => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = panel === "chat" ? chatRef.current.width ?? CHAT_DEFAULT : editorRef.current.width ?? EDITOR_DEFAULT;
      const dir = panel === "chat" ? 1 : -1; // editor grows as the handle drags left
      const onMove = (ev: PointerEvent) => {
        const raw = startW + dir * (ev.clientX - startX);
        if (panel === "chat") setChatWidth(clamp(raw, CHAT_MIN, CHAT_MAX));
        else setEditorWidth(clamp(raw, EDITOR_MIN, EDITOR_MAX));
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.classList.remove("resizing");
        scheduleSettingsSave();
      };
      document.body.classList.add("resizing");
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [scheduleSettingsSave],
  );

  const toggleChat = useCallback(() => {
    setChatCollapsed((c) => !c);
    scheduleSettingsSave();
  }, [scheduleSettingsSave]);

  const toggleEditor = useCallback(() => {
    setEditorCollapsed((c) => !c);
    scheduleSettingsSave();
  }, [scheduleSettingsSave]);

  const chatCol = chatCollapsed ? "40px" : `${chatWidth}px`;
  const editorCol = editorCollapsed ? "40px" : `${editorWidth}px`;

  return (
    <WorkspaceContext.Provider value={{ openDocument, openDraft }}>
      <div className="app" style={{ gridTemplateColumns: `${chatCol} 1fr ${editorCol}` }}>
        {chatCollapsed ? (
          <button className="rail rail-left" onClick={toggleChat} aria-label="Expand chat">
            ›
          </button>
        ) : (
          <div className="panel-col chat-col">
            <ChatDock
              sessionId={sessionId}
              onRender={handleRender}
              onPromote={openDraft}
              onCollapse={toggleChat}
            />
            <div
              className="resizer resizer-right"
              onPointerDown={(e) => startResize(e, "chat")}
            />
          </div>
        )}

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

        {editorCollapsed ? (
          <button className="rail rail-right" onClick={toggleEditor} aria-label="Expand editor">
            ‹
          </button>
        ) : (
          <div className="panel-col editor-col">
            <div
              className="resizer resizer-left"
              onPointerDown={(e) => startResize(e, "editor")}
            />
            <EditorPane
              key={boardId ?? "none"}
              tabs={tabs}
              activeTabId={activeTabId}
              onSelectTab={handleSelectTab}
              onCloseTab={handleCloseTab}
              onDraftSaved={handleDraftSaved}
              onCollapse={toggleEditor}
            />
          </div>
        )}
      </div>
    </WorkspaceContext.Provider>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <AppInner />
    </ReactFlowProvider>
  );
}
