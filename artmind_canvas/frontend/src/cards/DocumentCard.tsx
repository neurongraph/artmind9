import { useEffect, useState } from "react";
import { type NodeProps } from "@xyflow/react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { DocumentCardProps } from "../render/types";
import { useWorkspace } from "../editor/workspace";
import { useChangeStream } from "../events/changeStream";
import CardShell from "./CardShell";
import CardError from "./CardError";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; html: string };

// A read-only markdown Card. Fetches a Vault file through the backend's
// path-guarded /api/vault/file and renders it (marked → DOMPurify). This is
// the one Card type Phase 0 spawns from a `render` event.
export default function DocumentCard({ id, data }: NodeProps) {
  const props = data as unknown as DocumentCardProps;
  const [state, setState] = useState<State>({ status: "loading" });
  const [reloadNonce, setReloadNonce] = useState(0);
  const { openDocument } = useWorkspace();
  const subscribe = useChangeStream();

  // Graph reactivity (ADR 0008): when this document is written (e.g. saved in the
  // editor pane), the backend broadcasts a change; re-query to reflect it.
  useEffect(
    () =>
      subscribe((e) => {
        if (e.type === "change" && e.resource === "document" && e.path === props.vaultPath) {
          setReloadNonce((n) => n + 1);
        }
      }),
    [subscribe, props.vaultPath],
  );

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
  }, [props.vaultPath, reloadNonce]);

  return (
    <CardShell
      id={id}
      cardType="document"
      icon="📄"
      title={props.vaultPath}
      actions={
        <button
          className="doc-card-edit nodrag"
          onClick={() => openDocument(props.vaultPath)}
          title="Edit in the editor pane"
          aria-label="Edit document"
        >
          ✎
        </button>
      }
    >
      {state.status === "loading" && <div className="doc-card-muted">loading…</div>}
      {state.status === "error" && <CardError id={id} message={state.message} />}
      {state.status === "ready" && <div dangerouslySetInnerHTML={{ __html: state.html }} />}
    </CardShell>
  );
}
