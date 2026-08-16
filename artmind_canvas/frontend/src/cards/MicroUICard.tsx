import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { MicroUICardProps } from "../render/types";

// A `micro-ui` Card (tier c, ADR 0014): the escape hatch for arbitrary
// interactive agent-authored HTML/JS. The markup renders inside a strictly
// sandboxed iframe — `sandbox="allow-scripts"` WITHOUT `allow-same-origin`, so
// the frame runs on a unique opaque origin: scripts execute but the content
// cannot reach the app DOM, cookies, storage, or make same-origin requests.
// That is the whole security contract; the payload itself is schema-free.
export default function MicroUICard({ data }: NodeProps) {
  const props = data as unknown as MicroUICardProps;
  const heading = props.title ?? "micro-ui";

  return (
    <div className="doc-card micro-card">
      <Handle type="target" position={Position.Left} />
      <div className="doc-card-title">
        <span className="doc-card-name">🧩 {heading}</span>
      </div>
      <div className="doc-card-body micro-body nodrag nowheel">
        {props.html ? (
          <iframe
            className="micro-frame"
            title={heading}
            sandbox="allow-scripts"
            srcDoc={props.html}
          />
        ) : (
          <div className="doc-card-muted">no micro-UI content</div>
        )}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
