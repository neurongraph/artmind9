import { useEffect, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ProvenanceCardProps, ProvenanceResult } from "../render/types";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: ProvenanceResult };

// A read-only "where did this come from?" Card (ADR 0014). It reads provenance
// through the backend's /api/provenance, which itself goes through artmind's
// serve daemon — the canvas never touches Neo4j. Given an entityId (or a
// free-text reference the backend resolves), it lists the source documents and
// chunks the fact was extracted from.
export default function ProvenanceCard({ data }: NodeProps) {
  const props = data as unknown as ProvenanceCardProps;
  const [state, setState] = useState<State>({ status: "loading" });

  const domainsKey = (props.domains ?? []).join(",");

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams();
    for (const d of props.domains ?? []) params.append("domain", d);
    if (props.entityId) params.set("entityId", props.entityId);
    else if (props.reference) params.set("reference", props.reference);
    if (props.includeChunks) params.set("includeChunks", String(props.includeChunks));

    fetch(`/api/provenance?${params.toString()}`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
        }
        return res.json() as Promise<ProvenanceResult>;
      })
      .then((body) => {
        if (!cancelled) setState({ status: "ready", data: body });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ status: "error", message: String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [domainsKey, props.entityId, props.reference, props.includeChunks]);

  const heading =
    props.title ??
    (state.status === "ready" ? state.data.entity.name : null) ??
    props.reference ??
    props.entityId ??
    "provenance";

  return (
    <div className="doc-card prov-card">
      <Handle type="target" position={Position.Left} />
      <div className="doc-card-title">
        <span className="doc-card-name">🔎 {heading}</span>
        {state.status === "ready" && state.data.entity.entityClass && (
          <span className="prov-badge">{state.data.entity.entityClass}</span>
        )}
      </div>
      <div className="doc-card-body nodrag nowheel">
        {state.status === "loading" && <div className="doc-card-muted">loading provenance…</div>}
        {state.status === "error" && <div className="doc-card-error">{state.message}</div>}
        {state.status === "ready" && <ProvenanceBody data={state.data} />}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function ProvenanceBody({ data }: { data: ProvenanceResult }) {
  const { entity, sources, moreCount, chatSources } = data;
  return (
    <>
      {entity.description && <p className="prov-desc">{entity.description}</p>}
      <div className="prov-meta">
        {entity.domain && <span className="prov-chip">domain: {entity.domain}</span>}
        {entity.type && <span className="prov-chip">type: {entity.type}</span>}
      </div>

      <div className="prov-section-head">
        Sources{sources.length ? ` · ${sources.length}${moreCount ? `+${moreCount}` : ""}` : ""}
      </div>
      {sources.length === 0 && <div className="doc-card-muted">no extracted-from sources</div>}
      <ul className="prov-list">
        {sources.map((s, i) => (
          <li key={s.chunkId ?? i} className="prov-source">
            <div className="prov-source-head">
              <span className="prov-doc">{s.document.name ?? s.docId ?? "unknown document"}</span>
              {s.chunkName && <span className="prov-chunk">{s.chunkName}</span>}
              {(s.validTo || s.document.supersededBy) && (
                <span className="prov-super" title="superseded / historical">
                  superseded
                </span>
              )}
            </div>
            {s.snippet && <p className="prov-snippet">{s.snippet}</p>}
          </li>
        ))}
      </ul>

      {moreCount > 0 && <div className="doc-card-muted prov-more">+{moreCount} more source chunks</div>}

      {chatSources.length > 0 && (
        <>
          <div className="prov-section-head">Chat mentions · {chatSources.length}</div>
          <ul className="prov-list">
            {chatSources.map((c, i) => (
              <li key={c.id ?? i} className="prov-source">
                {c.snippet && <p className="prov-snippet">{c.snippet}</p>}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
