# Vault↔graph sync: a watched Vault, no local find-index

With filing metadata folded into the graph (ADR 0010), the graph becomes the single
find-index — "show me project Alpha" is a graph filter, not a query against a parallel
local index. But the Vault is deliberately externally-editable (ADR 0002, Q20), so it can
run *ahead* of the graph in three cases: the write→ingest window, an ingest failure, and
out-of-band edits (Obsidian/vim/`git pull`). If the graph is the only find-surface, those
gaps are blind spots. The choice: a **closed Vault** (the canvas is the sole writer) or a
**watched Vault** (a filesystem watcher keeps the graph converged).

We decided (Q22): **no standing local search index — the graph is the find-surface — and
the Vault↔graph invariant is *maintained* by a backend filesystem watcher.** The watcher
detects out-of-band creates/edits and auto-enqueues re-ingest so the graph converges back
to "everything ingested." A lightweight **pending / failed ingest status** is surfaced so
the write→ingest window and failures are visible rather than silent, and just-authored
content can display optimistically until its ingest lands.

Why: a watched Vault is the only model consistent with Q20's externally-editable Vault,
and it *preserves* the "no un-ingested document" goal by driving convergence rather than
abandoning it. Editing always reads the actual file on disk (the graph only supplies a
path + block-id pointer), so no index is ever needed for the editing path — the dropped
local index costs nothing there.

Consequences:
- The backend runs a watcher over the Vault root, with debounce / delta detection to
  avoid re-ingest storms.
- "Un-ingested document" is reframed as a *timing / failure / external-edit* condition
  with a visible status, not a storage case needing a fallback index.
- A completed watched-ingest emits the same change event as any other write, so open
  Cards scoped-requery (ADR 0008).
- Pure-client boundary holds: the watcher triggers artmind's ingest, it does not
  reimplement it (ADR 0003).
