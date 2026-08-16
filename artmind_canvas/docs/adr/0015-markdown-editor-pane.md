# Markdown editor pane: read-only Cards, a source editor bound to the Vault file

The canvas needs an authoring surface — artmind emits markdown (tables, long answers),
and the doc-first write → re-ingest loop (ADR 0002) needs somewhere to write. Three
questions: does editing live *in* the `document` Card (ADR 0005's original call) or in a
dedicated pane; what does it edit and how does it save; and how does it stay consistent
with a Vault that is also edited externally (ADR 0011)?

We decided: a **three-pane workspace** — a collapsible **Chat** (left) │ the **Canvas**
(center) │ a collapsible **Editor pane** (right), on a push/squeeze CSS grid (never an
overlay). **Cards become read-only rendered views**; the **Editor pane is the single
editing surface** — a **CodeMirror 6 raw-markdown source editor** bound to a **Vault file
path** (not a Card instance). This **supersedes ADR 0005**'s "a `document` Card edits
markdown" (see the amendment there).

The model:

- **Read vs. edit split.** A Card renders markdown read-only (`marked` → `dompurify` — the
  "pretty" view); the pane edits the literal `.md` text. Bytes change *only* in the source
  editor, so A1 block/offset provenance and external (Obsidian/vim) edits stay
  byte-faithful. A WYSIWYG editor was **rejected**: lossy markdown serialization rewrites
  untouched bytes and drifts block-ids.
- **File-path binding.** The pane targets a Vault path, so it can open files that have no
  Card (promoted agent output, a file picker); a Card is merely one view of the same file.
  The read-only Card re-renders once a save re-ingests (via ADR 0008, when Phase 1b exists).
- **Content routing by intent, not size.** An *answer* → wide Chat; something to
  *keep/arrange* → a read-only **Card**; something to *edit* → the **pane** (explicit
  "edit / promote" action). A long input surfaces an *"open in editor?"* affordance; length
  never silently moves content.
- **Explicit save; the editor never re-ingests.** `Cmd-S` writes the file once; ADR 0011's
  watcher picks it up and re-ingests — preserving the single doc-first write path. The
  editor never calls the graph or ingest directly.
- **Clobber-safety is file-level, not graph-level.** A **conditional write** (hash/mtime
  captured at load; on mismatch, refuse and offer reload / overwrite / diff) plus a
  **re-read on focus**. This is independent of SSE. ADR 0008's *post-ingest* change event
  serves the other half — the read-only Card re-rendering after a save re-ingests — not the
  editor's clobber-guard.
- **New docs need a location, not a classifier.** Creating or promoting a doc uses a manual
  **"save to…"** picker now; A5/A6 (ADR 0012) later pre-fill it with a *suggested* location.
  Placement is an assist, not a gate.
- **Tabs.** The pane holds multiple open docs, one active.

Consequences:

- **Net-new backend:** a path-guarded Vault **write** endpoint (create + overwrite) with a
  conditional-write version check — today only `GET /api/vault/file` exists (read-only).
- **State (extends ADR 0009):** open tabs persist **per-board** (`openDocuments[]` +
  `activeDocument`); **panel geometry** (sidebar widths / collapsed) persists **globally**
  in a new `$ARTMIND_CANVAS_STATE_DIR/settings.json`, *not* the board — so layout does not
  lurch when switching boards.
- **Net-new frontend:** CodeMirror 6 (the first editor dependency), a tab strip, the
  three-pane grid + collapsible sidebars, edit/promote affordances, the "save to…" picker,
  and dirty/conflict UI.
- **Sequencing:** decoupled from Phase 1b — ships in either order. 1b only upgrades the
  Card-auto-refresh-after-save nicety; without it the editor still works and the Card
  refreshes on board reload.
- **Track-A touchpoints (not blockers):** A5/A6 upgrade manual → suggested placement;
  ADR 0011's watcher (ROADMAP: "partial") — if unbuilt, re-ingest is manual/deferred and the
  editor still works.
