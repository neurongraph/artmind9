# Frontend: React + Vite, React Flow canvas, dedicated graph lib inside graph cards

The Canvas hosts React-component Cards you pan/zoom/rearrange — a node-hosting
surface, not a freeform drawing whiteboard. We chose React + Vite as the framework,
React Flow as the Canvas substrate (Cards are custom nodes), and a dedicated graph
library — Cytoscape.js or sigma.js — for rendering the actual Neo4j graph *inside*
`graph-view` Cards.

Why: React has first-class bindings for every widget needed (canvas, graph, block
editor), which best serves "best widgets, fastest." React Flow is purpose-built for
panning/zooming canvases of custom nodes. React Flow is *not* built for large graphs,
so graph rendering inside a Card uses Cytoscape/sigma instead — and `graph-view` Cards
are always a **filtered** subview (scoped by query/domain/entity), never the whole
graph, which bounds the rendering load. Rejected: tldraw (whiteboard-centric, heavier
than needed) and a hand-rolled pan/zoom canvas (rebuilds selection/layout/persistence).

## Addendum (2026-08-17): graph lib resolved to Cytoscape.js

The `graph-view` Card is built, resolving the "Cytoscape.js or sigma.js" choice to
**Cytoscape.js** (`cytoscape` ^3.34.1 + `@types/cytoscape`), driven directly via a
container ref in a `useEffect` — no React wrapper, one runtime dependency. It wins for
built-in force-directed layout (`cose`) plus interactive exploration on the **bounded,
filtered** subviews we render; sigma's WebGL strength is for graphs far larger than a
one-hop neighbourhood, so it is rejected for this cut. The instance is destroyed on
cleanup/before rebuild; relationship-type filtering and node-tap expansion are done on the
live instance without re-mounting.
