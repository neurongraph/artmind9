import { useState } from "react";
import type { BoardSummary } from "../render/types";

type Props = {
  boards: BoardSummary[];
  boardId: string | null;
  boardName: string;
  onSwitch: (id: string) => void;
  onCreate: () => void;
  onRename: (name: string) => void;
};

// The board switcher: pick a saved board, rename the active one, or create a
// new one. Boards are named saved arrangements (ADR 0009).
export default function BoardBar({
  boards,
  boardId,
  boardName,
  onSwitch,
  onCreate,
  onRename,
}: Props) {
  const [draft, setDraft] = useState(boardName);
  // Re-sync the local name draft when the active board changes (derived state).
  const [lastId, setLastId] = useState(boardId);
  if (boardId !== lastId) {
    setLastId(boardId);
    setDraft(boardName);
  }

  const commit = () => {
    const name = draft.trim();
    if (name && name !== boardName) onRename(name);
  };

  return (
    <div className="board-bar">
      <select
        className="board-select"
        value={boardId ?? ""}
        onChange={(e) => onSwitch(e.target.value)}
        aria-label="Switch board"
      >
        {boards.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </select>
      <input
        className="board-name"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
        placeholder="Board name"
        aria-label="Board name"
      />
      <button className="board-new" onClick={() => onCreate()}>
        + New board
      </button>
    </div>
  );
}
