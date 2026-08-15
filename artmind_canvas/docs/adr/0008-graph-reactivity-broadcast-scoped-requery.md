# Graph reactivity: backend broadcast + scoped re-query

When knowledge changes — an edit → re-ingest, or a chat-authored write that lands as a
document and re-ingests — the graph-view and provenance Cards already open on the Canvas
must reflect it. Options considered: client polling, a full graph re-fetch, optimistic
client-side patching of the rendered graph, and server push.

We decided (Q15): the canvas backend **broadcasts a change event** (over the existing
SSE channel) when an ingest/write completes, and each affected Card responds with a
**scoped re-query** — it re-fetches just its own filtered slice from artmind, rather than
reloading the whole graph.

Why: graph-view Cards are always a *filtered* subview (ADR 0004), so a scoped re-query is
cheap and leaves each Card authoritative — re-derived from artmind rather than patched
client-side, which avoids drift between the rendered graph and the real one. Broadcast
beats polling on latency and wasted work.

Consequences:
- The backend needs a change-notification channel carrying enough of *what changed*
  (domain / document / entity) for each Card to decide whether it is affected.
- Every graph-view / provenance Card must be re-queryable from its own filter spec (the
  same spec that first populated it), which reinforces treating that spec as Card state
  (ADR 0009).
