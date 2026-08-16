import { useEffect, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { DocumentCardProps } from "../render/types";
import { useWorkspace } from "../editor/workspace";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; html: string };

// A read-only markdown Card. Fetches a Vault file through the backend's
// path-guarded /api/vault/file and renders it (marked → DOMPurify). This is
// the one Card type Phase 0 spawns from a `render` event.
export default function DocumentCard({ data }: NodeProps) {
  const props = data as unknown as DocumentCardProps;
  const [state, setState] = useState<State>({ status: "loading" });
  const { openDocument } = useWorkspace();

  useEffect(() => {
    let cancelled = false;
    const url = `/api/vault/file?path=${encodeURIComponent(props.vaultPath)}`;
    fetch(url)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail ?? `HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((body: { content: string }) => {
        if (cancelled) return;
        const rendered = marked.parse(body.content, { async: false }) as string;
        setState({ status: "ready", html: DOMPurify.sanitize(rendered) });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ status: "error", message: String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [props.vaultPath]);

  return (
    <div className="doc-card">
      <Handle type="target" position={Position.Left} />
      <div className="doc-card-title">
        <span className="doc-card-name">📄 {props.vaultPath}</span>
        <button
          className="doc-card-edit nodrag"
          onClick={() => openDocument(props.vaultPath)}
          title="Edit in the editor pane"
          aria-label="Edit document"
        >
          ✎
        </button>
      </div>
      <div className="doc-card-body nodrag nowheel">
        {state.status === "loading" && <div className="doc-card-muted">loading…</div>}
        {state.status === "error" && (
          <div className="doc-card-error">{state.message}</div>
        )}
        {state.status === "ready" && (
          <div dangerouslySetInnerHTML={{ __html: state.html }} />
        )}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
