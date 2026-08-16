# Canvas layout is application state: persisted in the backend, multiple named boards

Card positions and sizes, which Cards are open, each Card's filter spec, and how Cards
are grouped — is that *knowledge* (belongs with the Vault/graph) or *application state*
(belongs to the UI)? And where is it persisted?

We decided (Q16): layout is **application state, not authoritative knowledge**. It is
persisted in the **canvas backend store** — never in the Vault and never in the graph —
and the user can keep **multiple named boards** (distinct saved arrangements).

Why: the Vault is the source of truth for knowledge (ADR 0002); polluting it with UI
coordinates would blur that line and make diffs/re-ingest noisy. Named boards let the
user hold several task-scoped arrangements of the same underlying knowledge. Losing a
layout is non-catastrophic (re-arrangeable), unlike losing knowledge — so it earns a
separate, lighter-weight store.

Consequences:
- The backend owns a canvas-state store: boards, the Card instances on each, their
  positions/sizes, and their filter specs. This is distinct from both the Vault and the
  graph.
- A board is a first-class saved entity (see the **Board** glossary term).
- **Extended by ADR 0015.** A board also records the Editor pane's **open documents**
  (`openDocuments[]` + `activeDocument`) — part of a board's working arrangement, like its
  Cards. Panel *geometry* (sidebar widths / collapsed state) is deliberately **not** board
  state: it is global UI chrome, kept in a separate `$ARTMIND_CANVAS_STATE_DIR/settings.json`
  so layout does not change when the user switches boards.
