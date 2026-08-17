import { useEffect, useState } from "react";
import { type NodeProps } from "@xyflow/react";
import type {
  PlacementCardProps,
  PlacementProposal,
  ProposeResult,
} from "../render/types";
import CardShell from "./CardShell";
import CardError from "./CardError";

// The propose→review→confirm filing surface (ADR 0012, A5/A6). On mount it asks
// the backend to propose a placement for the document at `vaultPath` (which runs
// artmind's A6 classifier through the serve daemon — the canvas never touches
// Neo4j or the LLM directly). The user reviews/adjusts the domain (context only),
// area, project and tags, then "Confirm & file" writes the accepted facets into
// the doc's frontmatter. Never a silent auto-file.

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; result: ProposeResult };

type Filed =
  | { phase: "idle" }
  | { phase: "saving" }
  | { phase: "done" }
  | { phase: "error"; message: string };

export default function PlacementCard({ id, data }: NodeProps) {
  const props = data as unknown as PlacementCardProps;
  const [state, setState] = useState<State>({ status: "loading" });

  // Editable review fields (populated once the proposal arrives).
  const [area, setArea] = useState("");
  const [project, setProject] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [tagDraft, setTagDraft] = useState("");
  const [filed, setFiled] = useState<Filed>({ phase: "idle" });

  const domainsKey = (props.domains ?? []).join(",");

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    setFiled({ phase: "idle" });

    fetch("/api/placement/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vaultPath: props.vaultPath, domains: props.domains ?? [] }),
    })
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
        }
        return res.json() as Promise<ProposeResult>;
      })
      .then((result) => {
        if (cancelled) return;
        const p = result.proposal.proposals;
        setArea(p.area.value ?? "");
        setProject(p.project.value ?? "");
        setTags(p.tags.value ?? []);
        setState({ status: "ready", result });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ status: "error", message: String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [props.vaultPath, domainsKey]);

  const addTag = () => {
    const t = tagDraft.trim();
    if (t && !tags.includes(t)) setTags([...tags, t]);
    setTagDraft("");
  };

  const confirm = async () => {
    if (state.status !== "ready") return;
    setFiled({ phase: "saving" });
    try {
      const res = await fetch("/api/placement/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: props.vaultPath,
          baseVersion: state.result.version,
          placement: {
            area: area.trim() || null,
            project: project.trim() || null,
            tags,
          },
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = body.detail;
        const message =
          res.status === 409
            ? "file changed on disk — reopen to re-propose"
            : typeof detail === "string"
              ? detail
              : `HTTP ${res.status}`;
        throw new Error(message);
      }
      setFiled({ phase: "done" });
    } catch (err: unknown) {
      setFiled({ phase: "error", message: String(err) });
    }
  };

  const heading = props.title ?? props.vaultPath ?? "placement";

  return (
    <CardShell id={id} cardType="placement" icon="🗂️" title={heading}>
      {state.status === "loading" && <div className="doc-card-muted">proposing placement…</div>}
      {state.status === "error" && <CardError id={id} message={state.message} />}
      {state.status === "ready" && (
        <ReviewForm
          proposal={state.result.proposal}
          area={area}
          project={project}
          tags={tags}
          tagDraft={tagDraft}
          filed={filed}
          onArea={setArea}
          onProject={setProject}
          onTagDraft={setTagDraft}
          onAddTag={addTag}
          onRemoveTag={(t) => setTags(tags.filter((x) => x !== t))}
          onConfirm={confirm}
        />
      )}
    </CardShell>
  );
}

function Confidence({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <span className="pl-confidence" title={`confidence ${pct}%`}>
      <span className="pl-confidence-bar" style={{ width: `${pct}%` }} />
    </span>
  );
}

function Badge({ known }: { known: boolean }) {
  return (
    <span className={known ? "pl-badge pl-badge-known" : "pl-badge pl-badge-new"}>
      {known ? "known" : "new"}
    </span>
  );
}

function ReviewForm({
  proposal,
  area,
  project,
  tags,
  tagDraft,
  filed,
  onArea,
  onProject,
  onTagDraft,
  onAddTag,
  onRemoveTag,
  onConfirm,
}: {
  proposal: PlacementProposal;
  area: string;
  project: string;
  tags: string[];
  tagDraft: string;
  filed: Filed;
  onArea: (v: string) => void;
  onProject: (v: string) => void;
  onTagDraft: (v: string) => void;
  onAddTag: () => void;
  onRemoveTag: (t: string) => void;
  onConfirm: () => void;
}) {
  const p = proposal.proposals;
  const busy = filed.phase === "saving";
  const done = filed.phase === "done";
  return (
    <>
      {p.reason && <p className="pl-reason">{p.reason}</p>}

      {/* Domain — context only; ingestion sources it from --domain, not frontmatter. */}
      <div className="pl-facet">
        <div className="pl-facet-head">
          <span className="pl-facet-label">domain</span>
          <Badge known={p.domain.known} />
          <Confidence value={p.domain.confidence} />
        </div>
        <div className="pl-domain">
          {p.domain.value ?? "—"} <span className="pl-note">(not written to frontmatter)</span>
        </div>
      </div>

      <div className="pl-facet">
        <div className="pl-facet-head">
          <span className="pl-facet-label">area</span>
          <Badge known={p.area.known} />
          <Confidence value={p.area.confidence} />
        </div>
        <input
          className="pl-input"
          value={area}
          placeholder="(none)"
          disabled={busy || done}
          onChange={(e) => onArea(e.target.value)}
        />
      </div>

      <div className="pl-facet">
        <div className="pl-facet-head">
          <span className="pl-facet-label">project</span>
          <Badge known={p.project.known} />
          <Confidence value={p.project.confidence} />
        </div>
        <input
          className="pl-input"
          value={project}
          placeholder="(none)"
          disabled={busy || done}
          onChange={(e) => onProject(e.target.value)}
        />
      </div>

      <div className="pl-facet">
        <div className="pl-facet-head">
          <span className="pl-facet-label">tags</span>
          <Confidence value={p.tags.confidence} />
        </div>
        <div className="pl-tags">
          {tags.map((t) => {
            const known = p.tags.value.includes(t) ? p.tags.known[p.tags.value.indexOf(t)] : false;
            return (
              <span key={t} className={known ? "pl-tag pl-tag-known" : "pl-tag pl-tag-new"}>
                {t}
                {!done && (
                  <button className="pl-tag-x" onClick={() => onRemoveTag(t)} title="remove">
                    ×
                  </button>
                )}
              </span>
            );
          })}
        </div>
        {!done && (
          <input
            className="pl-input"
            value={tagDraft}
            placeholder="add a tag, Enter"
            disabled={busy}
            onChange={(e) => onTagDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onAddTag();
              }
            }}
          />
        )}
      </div>

      {p.target_file_hint && (
        <div className="pl-hint">suggested filename: {p.target_file_hint}</div>
      )}

      <div className="pl-actions">
        {done ? (
          <span className="pl-done">filed ✓ · re-ingest pending</span>
        ) : (
          <button className="pl-confirm" onClick={onConfirm} disabled={busy}>
            {busy ? "filing…" : "Confirm & file"}
          </button>
        )}
      </div>
      {filed.phase === "error" && <div className="doc-card-error">{filed.message}</div>}
    </>
  );
}
