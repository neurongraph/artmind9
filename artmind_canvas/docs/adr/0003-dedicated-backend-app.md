# A dedicated backend app, reusing artmind's modules

artmind_canvas needs capabilities that do not exist today: a graph node/edge
(node-link JSON) endpoint, live skill authoring, an inline "re-ingest this edited
document" trigger, and canvas-state persistence. We will build a new dedicated
FastAPI backend — a sibling to `chat-ui` and `admin-ui` — that reuses artmind's
Python modules (`graph_query`, `ingest`, …) and the neutral agent-backend contract,
and adds the net-new endpoints.

Why: this satisfies "independent and more powerful" while standing on the proven
Python layer. Rejected alternatives: a pure client on existing endpoints (can't do
graph-viz, skill authoring, or re-ingest — insufficient), and extending admin-ui
(violates the independence requirement).
