# artmind_canvas

A new, standalone single-user desktop UX for interacting with artmind — a spatial
Canvas plus a persistent Chat dock, in which the agent and the user co-create
knowledge: reading it, understanding how it was retrieved, editing it, re-ingesting
it, and authoring skills to act on it. Deliberately separate from and more capable
than the existing `chat-ui` and `admin-ui`.

Presumed code home: `artmind_canvas/` at the repo root (a sibling to `artmind/`),
confirmable when we settle backend structure.

## Language

**artmind_canvas**:
The whole new application — the Canvas, the Chat dock, and everything spawned into it.
artmind is its "brain"; artmind_canvas is the surface.
_Avoid_: workbench, dashboard, console, IDE.

**Canvas**:
The spatial work surface where Cards are placed and freely rearranged. The primary
region of artmind_canvas.
_Avoid_: board, workspace, desktop.

**Card**:
A movable unit on the Canvas holding one focused thing. Both the agent and the user
can spawn Cards. There is a small fixed set of first-class types plus one open escape
hatch:
- `graph-view` — a *filtered* visual node/edge subview of the graph (scoped by
  query/domain/entity — never the whole graph at once)
- `document` — a **read-only** markdown viewer over a Vault file or block (editing
  happens in the Editor pane, ADR 0015)
- `provenance` — the source blocks/chunks a piece of knowledge was retrieved from
- `skill` — authoring and running a skill
- `micro-UI` — an open escape hatch the agent fills with arbitrary rendered content
_Avoid_: widget, panel, window, tile.

**Chat dock**:
The persistent conversational region (not a Card) that anchors artmind_canvas. Talking
to the agent here is what causes Cards to be spawned onto the Canvas. Collapsible and
width-adjustable (ADR 0015), but still the dock — not a generic sidebar.
_Avoid_: sidebar, chat panel.

**Editor pane**:
The persistent right-hand region (not a Card) holding a **source-markdown editor** over a
Vault file — the single editing surface (ADR 0015). Cards are read-only; opening one for
edit, or promoting agent output, opens the file here. Tabbed (several open docs, one
active), collapsible/width-adjustable; open docs persist per-board, panel geometry is
global. Together with the Chat dock (left) and Canvas (center) it forms the three-pane
workspace.
_Avoid_: IDE, code editor, document Card (it is neither a Card nor an IDE).

**Board**:
A named, saved arrangement of Cards on the Canvas — layout, which Cards are open, and
each Card's filter spec. Application state, not knowledge (ADR 0009). The user can keep
several boards for different tasks over the same underlying knowledge.
_Avoid_: tab, view, session.

**Card contract**:
The schema of a first-class Card type's declarative payload (`props`) — what the Card
renders and what the agent/backend must emit (in a `render` event) to populate it. Owned
client-side by the Card component, published to the agent (ADR 0014). The `micro-UI` Card
is the exception: no schema, just sandboxed HTML.
_Avoid_: interface, API (too generic).

**Placement Card**:
The review surface where the agent's proposed classification and target —
`{domain, area, project, tags, target file/section}` — is shown for the user to
accept/adjust before frontmatter is written and the doc re-ingested (propose → review →
confirm, ADR 0012). Never a silent auto-file.
_Avoid_: dialog, prompt.

**artmind (the brain)**:
The existing knowledge-graph system — CLI, `serve` daemon, agent harnesses, Neo4j.
artmind_canvas is a client of it, never a reimplementation. The `artmind` CLI is the
composable grammar the "act" leg and skills are built on. Capabilities artmind_canvas
needs but artmind lacks (e.g. block-level provenance, idempotent re-ingest — ADR 0006)
are built *in artmind itself*; the client (UI and its thin backend) only ever consumes
them.
_Avoid_: backend, server (too generic).

## Data locations

Knowledge lives in three distinct places; keeping them straight is central to the
whole design.

**Knowledge Graph**:
The graph in Neo4j (remote AuraDB). A *projection* derived from ingested documents —
not an independent source of truth.
_Avoid_: the database, the store.

**Data dir**:
`$ARTMIND_DATA_DIR` (default `~/artmind_data`). artmind's own ingestion artifacts:
originals, chunks, the graph-JSON staging representation, registry DB, snapshots. Owned
by the ingestion pipeline; not where the user works.
_Avoid_: corpus, cache.

**Vault**:
The collection of files the user actually works in — the authoritative source of
knowledge (ADR 0002) and the origin of the ingest round-trip. It is a **first-class
configured root directory** that artmind_canvas is pointed at, distinct from both
`ARTMIND_HOME` and `ARTMIND_DATA_DIR` (the web UI never touches the Data dir). *Still
being modeled*: its content model (markdown-first, plus how embedded images and binary
artifacts like pdf/pptx are held) and the rule for where chat-authored knowledge lands.
_Avoid_: notes, workspace, docs folder.

**Vault root**:
The configured directory that *is* the Vault, supplied to artmind_canvas at launch
(anticipated env var, e.g. `ARTMIND_VAULT_DIR`). All Vault paths and attachment
references resolve relative to it.

**Filing metadata**:
A note's organizational labels — `project`, `area`, `tags` — plus always-present
baseline fields (`created_on`, `modified_on`, `title`). Authored in Vault frontmatter
(source of truth) and projected into the graph as properties on Document (authoritative)
and DocChunk (denormalized), never on shared Entity nodes (ADR 0010). Distinct from
**domain**, which selects the extraction schema rather than labelling the note.
_Avoid_: category, folder, label (as a catch-all).

## Source of truth

Documents (the Vault) are authoritative; the Knowledge Graph is a projection derived
by ingestion. All knowledge writes — including chat-authored ones — land as a
document/block first, then re-ingest. See ADR 0002.
