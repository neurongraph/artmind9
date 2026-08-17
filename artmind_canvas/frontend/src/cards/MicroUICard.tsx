import { type NodeProps } from "@xyflow/react";
import type { MicroUICardProps } from "../render/types";
import CardShell from "./CardShell";

// A `micro-ui` Card (tier c, ADR 0014): the escape hatch for arbitrary
// interactive agent-authored HTML/JS. The markup renders inside a strictly
// sandboxed iframe — `sandbox="allow-scripts"` WITHOUT `allow-same-origin`, so
// the frame runs on a unique opaque origin: scripts execute but the content
// cannot reach the app DOM, cookies, storage, or make same-origin requests.
// That is the whole security contract; the payload itself is schema-free.
export default function MicroUICard({ id, data }: NodeProps) {
  const props = data as unknown as MicroUICardProps;
  const heading = props.title ?? "micro-ui";

  return (
    <CardShell id={id} cardType="micro-ui" icon="🧩" title={heading} bodyClassName="micro-body">
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
    </CardShell>
  );
}
