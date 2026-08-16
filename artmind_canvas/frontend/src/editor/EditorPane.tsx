import { useCallback, useEffect, useRef, useState } from "react";
import CodeMirrorEditor from "./CodeMirrorEditor";
import SavePicker from "./SavePicker";
import type { DocTab } from "./workspace";
import { readVaultFile, writeVaultFile } from "../vault/api";

type Buffer = {
  text: string;
  savedText: string;
  version: string | null; // on-disk version for conditional write; null = never saved
  status: "loading" | "ready" | "error" | "saving";
  message?: string;
  conflict: { currentVersion: string | null } | null;
  staleOnDisk: boolean; // file changed on disk while we hold unsaved edits
  reset: number; // bump to force CodeMirror to reload the doc text
};

type Props = {
  tabs: DocTab[];
  activeTabId: string | null;
  onSelectTab: (id: string) => void;
  onCloseTab: (id: string) => void;
  onDraftSaved: (id: string, path: string) => void;
  onCollapse: () => void;
};

const newBuffer = (over: Partial<Buffer>): Buffer => ({
  text: "",
  savedText: "",
  version: null,
  status: "ready",
  conflict: null,
  staleOnDisk: false,
  reset: 0,
  ...over,
});

// The Editor pane (ADR 0015): a tab strip over open Vault docs + a CodeMirror
// source editor. Reads are versioned; saves are conditional (409 → resolvable
// conflict). Cards stay read-only; editing happens only here. Drafts (promoted
// output) become real files on first save via the "save to…" picker.
export default function EditorPane({
  tabs,
  activeTabId,
  onSelectTab,
  onCloseTab,
  onDraftSaved,
  onCollapse,
}: Props) {
  const [buffers, setBuffers] = useState<Record<string, Buffer>>({});
  const [saveAsFor, setSaveAsFor] = useState<string | null>(null);

  // Refs so the once-registered focus listener / editor callbacks read latest state.
  const buffersRef = useRef(buffers);
  buffersRef.current = buffers;
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const activeIdRef = useRef(activeTabId);
  activeIdRef.current = activeTabId;
  const loadingRef = useRef<Set<string>>(new Set());

  const tabIdsKey = tabs.map((t) => `${t.id}:${t.path ?? ""}`).join("|");

  // Create placeholders for newly-opened tabs; prune closed ones.
  useEffect(() => {
    setBuffers((prev) => {
      let next = prev;
      let changed = false;
      const touch = () => {
        if (!changed) {
          next = { ...prev };
          changed = true;
        }
      };
      for (const tab of tabs) {
        if (next[tab.id]) continue;
        touch();
        next[tab.id] = tab.path
          ? newBuffer({ status: "loading" })
          : newBuffer({ text: tab.seed ?? "" });
      }
      const ids = new Set(tabs.map((t) => t.id));
      for (const k of Object.keys(next)) {
        if (!ids.has(k)) {
          touch();
          delete next[k];
        }
      }
      return changed ? next : prev;
    });
  }, [tabIdsKey, tabs]);

  const loadFile = useCallback(async (id: string, path: string) => {
    try {
      const f = await readVaultFile(path);
      setBuffers((prev) =>
        prev[id]
          ? {
              ...prev,
              [id]: newBuffer({
                text: f.content,
                savedText: f.content,
                version: f.version,
                reset: prev[id].reset + 1,
              }),
            }
          : prev,
      );
    } catch (e) {
      setBuffers((prev) =>
        prev[id] ? { ...prev, [id]: { ...prev[id], status: "error", message: String(e) } } : prev,
      );
    } finally {
      loadingRef.current.delete(id);
    }
  }, []);

  // Fetch content for any file tab still in the "loading" placeholder state.
  useEffect(() => {
    for (const tab of tabs) {
      const buf = buffers[tab.id];
      if (tab.path && buf?.status === "loading" && !loadingRef.current.has(tab.id)) {
        loadingRef.current.add(tab.id);
        void loadFile(tab.id, tab.path);
      }
    }
  }, [buffers, tabs, loadFile]);

  // Clobber-safety, independent of SSE (ADR 0015): on window focus, re-read the
  // active file. Unchanged → nothing; changed & clean → silently refresh;
  // changed & dirty → flag stale so the user can reload or overwrite.
  const revalidateActive = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    const tab = tabsRef.current.find((t) => t.id === id);
    if (!tab?.path) return;
    const buf = buffersRef.current[id];
    if (!buf || buf.status !== "ready") return;
    try {
      const f = await readVaultFile(tab.path);
      if (f.version === buf.version) return;
      setBuffers((prev) => {
        const b = prev[id];
        if (!b) return prev;
        const dirty = b.text !== b.savedText;
        if (!dirty) {
          return {
            ...prev,
            [id]: {
              ...b,
              text: f.content,
              savedText: f.content,
              version: f.version,
              reset: b.reset + 1,
              staleOnDisk: false,
            },
          };
        }
        return { ...prev, [id]: { ...b, staleOnDisk: true, conflict: { currentVersion: f.version } } };
      });
    } catch {
      /* transient read failure — leave the buffer as-is */
    }
  }, []);

  useEffect(() => {
    const onFocus = () => void revalidateActive();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [revalidateActive]);

  const doWrite = useCallback(
    async (id: string, path: string, text: string, baseVersion: string | null) => {
      setBuffers((prev) =>
        prev[id] ? { ...prev, [id]: { ...prev[id], status: "saving", message: undefined } } : prev,
      );
      const res = await writeVaultFile(path, text, baseVersion);
      setBuffers((prev) => {
        const b = prev[id];
        if (!b) return prev;
        if (res.ok) {
          return {
            ...prev,
            [id]: { ...b, status: "ready", version: res.version, savedText: text, conflict: null, staleOnDisk: false },
          };
        }
        if (res.conflict) {
          return { ...prev, [id]: { ...b, status: "ready", conflict: { currentVersion: res.currentVersion } } };
        }
        return { ...prev, [id]: { ...b, status: "error", message: res.message } };
      });
    },
    [],
  );

  const save = useCallback(() => {
    const id = activeIdRef.current;
    if (!id) return;
    const tab = tabsRef.current.find((t) => t.id === id);
    const buf = buffersRef.current[id];
    if (!tab || !buf || buf.status === "saving") return;
    if (!tab.path) {
      setSaveAsFor(id); // draft → pick a target path
      return;
    }
    void doWrite(id, tab.path, buf.text, buf.version);
  }, [doWrite]);

  const onEditorChange = useCallback((text: string) => {
    const id = activeIdRef.current;
    if (!id) return;
    setBuffers((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], text } } : prev));
  }, []);

  const reloadFromDisk = useCallback(
    async (id: string, path: string) => {
      loadingRef.current.add(id);
      setBuffers((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], status: "loading" } } : prev));
      await loadFile(id, path);
    },
    [loadFile],
  );

  const confirmSaveAs = useCallback(
    async (id: string, path: string): Promise<string | null> => {
      const buf = buffersRef.current[id];
      if (!buf) return "buffer gone";
      const res = await writeVaultFile(path, buf.text, null);
      if (res.ok) {
        setBuffers((prev) =>
          prev[id]
            ? { ...prev, [id]: { ...prev[id], status: "ready", version: res.version, savedText: buf.text, conflict: null } }
            : prev,
        );
        onDraftSaved(id, path);
        setSaveAsFor(null);
        return null;
      }
      if (res.conflict) return "a file already exists there";
      return res.message;
    },
    [onDraftSaved],
  );

  const active = activeTabId ? buffers[activeTabId] : undefined;
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null;
  const dirty = !!active && active.text !== active.savedText;

  return (
    <div className="editor-pane">
      <div className="editor-head">
        <div className="editor-tabs">
          {tabs.map((t) => {
            const b = buffers[t.id];
            const tabDirty = b && b.text !== b.savedText;
            return (
              <div
                key={t.id}
                className={`editor-tab${t.id === activeTabId ? " active" : ""}`}
                onClick={() => onSelectTab(t.id)}
                title={t.path ?? "unsaved draft"}
              >
                <span className="editor-tab-title">
                  {tabDirty ? "• " : ""}
                  {t.title}
                </span>
                <button
                  className="editor-tab-close"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCloseTab(t.id);
                  }}
                  aria-label={`Close ${t.title}`}
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>
        <button className="editor-collapse" onClick={onCollapse} aria-label="Collapse editor">
          ›
        </button>
      </div>

      {activeTab && active ? (
        <>
          <div className="editor-toolbar">
            <button
              className="editor-save"
              onClick={save}
              disabled={active.status === "saving" || (!!activeTab.path && !dirty)}
            >
              {active.status === "saving"
                ? "Saving…"
                : !activeTab.path
                  ? "Save as…"
                  : dirty
                    ? "Save"
                    : "Saved"}
            </button>
            <span className="editor-status">
              {activeTab.path ?? "unsaved draft"}
              {active.status === "error" && active.message ? ` — ${active.message}` : ""}
            </span>
          </div>

          {active.conflict && activeTab.path && (
            <div className="editor-conflict">
              {active.staleOnDisk
                ? "This file changed on disk while you were editing."
                : "Save rejected — the file changed on disk."}
              <button onClick={() => void reloadFromDisk(activeTab.id, activeTab.path!)}>
                Reload (discard mine)
              </button>
              <button
                onClick={() =>
                  void doWrite(activeTab.id, activeTab.path!, active.text, active.conflict!.currentVersion)
                }
              >
                Overwrite
              </button>
            </div>
          )}

          {active.status === "loading" ? (
            <div className="editor-empty">loading…</div>
          ) : active.status === "error" ? (
            <div className="editor-empty editor-err">{active.message}</div>
          ) : (
            <CodeMirrorEditor
              docKey={`${activeTab.id}:${active.reset}`}
              initialValue={active.text}
              onChange={onEditorChange}
              onSave={save}
            />
          )}
        </>
      ) : (
        <div className="editor-empty">
          No document open. Click ✎ on a document Card, or promote chat output here.
        </div>
      )}

      {saveAsFor && (
        <SavePicker
          onCancel={() => setSaveAsFor(null)}
          onConfirm={(path) => confirmSaveAs(saveAsFor, path)}
        />
      )}
    </div>
  );
}
