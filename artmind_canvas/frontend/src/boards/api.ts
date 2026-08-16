import type { Board, BoardSummary, Viewport, CardInstance } from "../render/types";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

export async function listBoards(): Promise<BoardSummary[]> {
  const body = await asJson<{ boards: BoardSummary[] }>(await fetch("/api/boards"));
  return body.boards;
}

export async function createBoard(name: string): Promise<Board> {
  return asJson<Board>(
    await fetch("/api/boards", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export async function getBoard(id: string): Promise<Board> {
  return asJson<Board>(await fetch(`/api/boards/${encodeURIComponent(id)}`));
}

type BoardPatch = {
  name?: string;
  cards?: CardInstance[];
  viewport?: Viewport;
  openDocuments?: string[];
  activeDocument?: string | null;
};

export async function saveBoard(id: string, patch: BoardPatch): Promise<Board> {
  return asJson<Board>(
    await fetch(`/api/boards/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  );
}

export async function deleteBoard(id: string): Promise<void> {
  const res = await fetch(`/api/boards/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}
