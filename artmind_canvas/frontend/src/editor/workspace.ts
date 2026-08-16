import { createContext, useContext } from "react";

// One open Editor tab. A `path` of null is an unsaved draft (promoted output /
// long input); it carries `seed` text until its first save assigns a real path.
export type DocTab = {
  id: string;
  path: string | null;
  title: string;
  seed?: string;
};

export function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

// The editor workspace surface exposed to any component that wants to open a
// document in the Editor pane — notably the React-Flow-rendered DocumentCard,
// which can't receive App props directly (ADR 0015). App provides the impl.
export type Workspace = {
  // Open (or focus, if already open) an existing Vault file in the Editor pane.
  openDocument: (path: string) => void;
  // Open a new *unsaved* draft seeded with text (promoted chat output / long
  // input). It becomes a real file on first save via the "save to…" picker.
  openDraft: (seed: string, title?: string) => void;
};

const noop = () => {};

export const WorkspaceContext = createContext<Workspace>({
  openDocument: noop,
  openDraft: noop,
});

export function useWorkspace(): Workspace {
  return useContext(WorkspaceContext);
}
