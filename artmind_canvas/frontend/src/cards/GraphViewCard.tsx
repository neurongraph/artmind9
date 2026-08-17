import { useEffect, useRef, useState } from "react";
import { type NodeProps } from "@xyflow/react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import type { GraphViewCardProps, GraphViewResult, GraphNode, GraphEdge } from "../render/types";
import CardShell from "./CardShell";
import CardError from "./CardError";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: GraphViewResult };

// Cytoscape can't read CSS variables, so the theme tokens (styles.css :root) are
// mirrored here as literals. Keep in sync with --panel/--border/--text/--muted/
// --accent/--bg.
const CY_STYLE: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      "background-color": "#171a21",
      "border-color": "#262b36",
      "border-width": 1,
      label: "data(label)",
      color: "#e6e8ec",
      "font-size": 10,
      "text-valign": "center",
      "text-halign": "center",
      "text-wrap": "wrap",
      "text-max-width": "84px",
      width: 42,
      height: 42,
      "text-outline-color": "#0f1115",
      "text-outline-width": 2,
    },
  },
  {
    selector: "node.anchor",
    style: {
      "background-color": "#5b8def",
      "border-color": "#5b8def",
      color: "#ffffff",
      "font-weight": "bold",
      width: 54,
      height: 54,
    },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "#3a4150",
      "curve-style": "bezier",
      label: "data(label)",
      "font-size": 8,
      color: "#8b93a1",
      "text-rotation": "autorotate",
      "text-background-color": "#0f1115",
      "text-background-opacity": 1,
      "text-background-padding": "2px",
    },
  },
];

const LAYOUT: cytoscape.LayoutOptions = { name: "cose", animate: false, fit: true, padding: 24 };

function nodeElement(n: GraphNode): ElementDefinition {
  return {
    data: { id: n.id, label: n.name ?? n.id, entityClass: n.entityClass ?? null },
    classes: n.anchor ? "anchor" : undefined,
  };
}

function edgeElement(e: GraphEdge): ElementDefinition {
  return { data: { id: e.id, source: e.source, target: e.target, label: e.type, reltype: e.type } };
}

function distinctTypes(edges: { type: string }[]): string[] {
  return Array.from(new Set(edges.map((e) => e.type))).sort();
}

// Hide edges whose relationship type is toggled off, then hide any non-anchor
// node left with no visible edge. Purely client-side — no extra query.
function applyFilter(cy: Core, hidden: Set<string>): void {
  cy.batch(() => {
    cy.edges().forEach((e) => {
      e.style("display", hidden.has(String(e.data("reltype"))) ? "none" : "element");
    });
    cy.nodes().forEach((n) => {
      if (n.hasClass("anchor")) {
        n.style("display", "element");
        return;
      }
      const anyVisible = n.connectedEdges().filter((e) => e.style("display") !== "none").length > 0;
      n.style("display", anyVisible ? "element" : "none");
    });
  });
}

// An interactive, filtered one-hop neighbourhood of the knowledge graph (ADRs
// 0004/0008), drawn with Cytoscape.js. The node-link JSON is assembled by the
// backend's /api/graph from artmind's `query entity-context` (through the serve
// daemon) — the canvas never touches Neo4j. Relationship-type chips filter the
// view client-side; tapping a node expands its neighbourhood (another serve read).
export default function GraphViewCard({ id, data }: NodeProps) {
  const props = data as unknown as GraphViewCardProps;
  const [state, setState] = useState<State>({ status: "loading" });
  const [relTypes, setRelTypes] = useState<string[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [stats, setStats] = useState<{ nodes: number; edges: number }>({ nodes: 0, edges: 0 });

  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;

  const domainsKey = (props.domains ?? []).join(",");

  // Fetch the subgraph on mount / when the selectors change.
  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams();
    for (const d of props.domains ?? []) params.append("domain", d);
    if (props.entityId) params.set("entityId", props.entityId);
    else if (props.reference) params.set("reference", props.reference);

    setState({ status: "loading" });
    fetch(`/api/graph?${params.toString()}`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
        }
        return res.json() as Promise<GraphViewResult>;
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
  }, [domainsKey, props.entityId, props.reference]);

  // Tapping a neighbour expands its own one-hop neighbourhood (another serve
  // read via /api/graph), merged into the live instance. Read-only-through-serve.
  async function expand(nodeId: string): Promise<void> {
    const cy = cyRef.current;
    if (!cy) return;
    const params = new URLSearchParams();
    for (const d of props.domains ?? []) params.append("domain", d);
    params.set("entityId", nodeId);
    const res = await fetch(`/api/graph?${params.toString()}`);
    if (!res.ok) return;
    const body = (await res.json()) as GraphViewResult;
    const additions: ElementDefinition[] = [];
    for (const n of body.nodes) {
      // Never re-anchor an existing node; only add genuinely new neighbours.
      if (cy.getElementById(n.id).empty()) additions.push(nodeElement({ ...n, anchor: false }));
    }
    for (const e of body.edges) {
      if (cy.getElementById(e.id).empty()) additions.push(edgeElement(e));
    }
    if (additions.length === 0) return;
    cy.add(additions);
    cy.layout(LAYOUT).run();
    setRelTypes((prev) => Array.from(new Set([...prev, ...body.edges.map((e) => e.type)])).sort());
    setStats({ nodes: cy.nodes().length, edges: cy.edges().length });
    applyFilter(cy, hiddenRef.current);
  }

  // Build (and tear down) the Cytoscape instance when a fresh result arrives.
  const ready = state.status === "ready" ? state.data : null;
  useEffect(() => {
    if (!ready || !containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [...ready.nodes.map(nodeElement), ...ready.edges.map(edgeElement)],
      style: CY_STYLE,
      layout: LAYOUT,
      minZoom: 0.2,
      maxZoom: 3,
    });
    cyRef.current = cy;
    cy.on("tap", "node", (evt) => {
      void expand(evt.target.id());
    });
    setRelTypes(distinctTypes(ready.edges));
    setHidden(new Set());
    setStats({ nodes: cy.nodes().length, edges: cy.edges().length });

    // The frame is user-resizable (Phase B, NodeResizer). Cytoscape sizes itself
    // to the container once and won't notice the box changing, so re-fit on any
    // container resize. rAF-debounced to coalesce the resize-drag storm.
    const container = containerRef.current;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        cy.resize();
        cy.fit(undefined, 24);
      });
    });
    ro.observe(container);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf);
      cy.destroy();
      cyRef.current = null;
    };
    // expand is stable enough (reads props/refs); rebuild only on a new result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // Re-apply the relationship-type filter whenever the toggles change.
  useEffect(() => {
    const cy = cyRef.current;
    if (cy) applyFilter(cy, hidden);
  }, [hidden]);

  function toggleType(t: string): void {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  const anchorClass = ready?.anchor?.entityClass ?? null;
  const heading =
    props.title ?? ready?.anchor?.name ?? props.reference ?? props.entityId ?? "graph";

  return (
    <CardShell
      id={id}
      cardType="graph-view"
      icon="🕸"
      title={heading}
      badge={anchorClass ? <span className="prov-badge">{anchorClass}</span> : undefined}
      subheader={
        state.status === "ready" ? (
          <div className="gv-strip nodrag">
            <span className="gv-counts">
              {stats.nodes} nodes · {stats.edges} edges
              {state.data.truncated ? " · truncated" : ""}
            </span>
            {relTypes.length > 0 && (
              <div className="gv-legend">
                {relTypes.map((t) => (
                  <button
                    key={t}
                    type="button"
                    className={`gv-chip${hidden.has(t) ? " gv-chip-off" : ""}`}
                    onClick={() => toggleType(t)}
                    title={hidden.has(t) ? `show ${t}` : `hide ${t}`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : undefined
      }
    >
      {state.status === "loading" && <div className="doc-card-muted">loading graph…</div>}
      {state.status === "error" && <CardError id={id} message={state.message} />}
      {state.status === "ready" && <div ref={containerRef} className="gv-canvas" />}
    </CardShell>
  );
}
