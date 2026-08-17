import type { ReactNode } from "react";
import { Handle, NodeResizer, Position } from "@xyflow/react";
import type { KnownCardType } from "./registry";
import { minSize } from "./sizes";
import { useCardActions } from "./cardActions";

// The shared Card frame every card type wears (Phase B). It owns the chrome that
// must be identical across cards — the connection Handles, the draggable title
// bar, the close button, and the NodeResizer — so individual cards only supply
// their heading and body. Critical: the Handles and NodeResizer live on the
// FRAME, never the body, because card bodies keep `nodrag nowheel` (GraphViewCard
// owns Cytoscape zoom, MicroUICard hosts a sandboxed iframe) and would otherwise
// swallow the resize/connect interactions.
const ROOT_CLASS: Record<KnownCardType, string> = {
  document: "",
  provenance: "prov-card",
  "micro-ui": "micro-card",
  placement: "placement-card",
  "graph-view": "graph-view-card",
};

type Props = {
  // The React Flow node id — the close action filters by it (App.handleClose).
  id: string;
  cardType: KnownCardType;
  icon: ReactNode;
  title: ReactNode;
  // Optional trailing chips (e.g. entity class) shown after the heading.
  badge?: ReactNode;
  // Optional card-specific action buttons (e.g. Document's ✎ edit), left of close.
  actions?: ReactNode;
  // Optional fixed region between the title bar and the scrolling body (e.g.
  // GraphViewCard's relationship-type legend), outside `doc-card-body`.
  subheader?: ReactNode;
  // Extra class on the body wrapper (e.g. `micro-body` to drop padding).
  bodyClassName?: string;
  children: ReactNode;
};

export default function CardShell({
  id,
  cardType,
  icon,
  title,
  badge,
  actions,
  subheader,
  bodyClassName,
  children,
}: Props) {
  const { onClose } = useCardActions();
  const min = minSize(cardType);
  const rootClass = `doc-card ${ROOT_CLASS[cardType]}`.trim();
  const bodyClass = `doc-card-body nodrag nowheel${bodyClassName ? ` ${bodyClassName}` : ""}`;

  return (
    <div className={rootClass}>
      <NodeResizer minWidth={min.width} minHeight={min.height} />
      <Handle type="target" position={Position.Left} />
      <div className="doc-card-title">
        <span className="doc-card-name">
          {icon} {title}
        </span>
        {badge}
        {actions}
        <button
          className="doc-card-close nodrag"
          onClick={() => onClose(id)}
          title="Close card"
          aria-label="Close card"
        >
          ×
        </button>
      </div>
      {subheader}
      <div className={bodyClass}>{children}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
