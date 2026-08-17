# artmind_canvas — Roadmap

This file is the **durable sequencing index**. The [ADRs](adr/) hold the *why* (the
design decisions and their rejected alternatives); this file holds the *when* — the
phase order, current position, and the dependency links between the canvas client and
the artmind package. It exists because the phasing previously lived only in a chat
session and an ephemeral plan file, and was nearly lost. Keep it committed and current.

## The standing constraint (do not violate)

> **The artmind_canvas UI is a pure CLIENT. Surgery on artmind's ingestion pipeline is
> not part of the client code.**

Every capability artmind lacks (block provenance, idempotent re-ingest, metadata
ingestion, delta classifier, placement classifier, vocabulary, graph indices, SDK
resume) is built in the **artmind package** (Track A below), never in `artmind_canvas/`.
The canvas backend may add net-new *client* endpoints (graph node-link JSON, skill
authoring, re-ingest trigger, canvas-state persistence — ADR 0003) that *call* artmind's
modules; it must not embed ingestion/extraction/placement logic.

## Two workstreams

- **Canvas client** — `artmind_canvas/` (React+Vite frontend + thin FastAPI backend).
  A pure client. Phases 0, 1, 2, … below.
- **Track A** — capability work *inside the `artmind` package* that unblocks richer
  Cards. Delivered independently of the client; the client consumes it once shipped.

## Track A — artmind-package capabilities

| ID | Capability | ADR | Status | Unblocks in the client |
|----|-----------|-----|--------|------------------------|
| A0 | Packaging split: lean core + optional `[ingest]` extra | — | ✅ done | Lean canvas-backend venv (no torch/docling) |
| A1 | Block-level provenance + idempotent re-ingest | 0006 | ✅ done | `provenance` Card; document edit→re-ingest round-trip (partial); watched-vault sync (0011) |
| A2 | Filing taxonomy as ingested metadata | 0010 | ✅ done | `graph-view` filtered by filing metadata; placement |
| A3 | Graph indices | 0004/0008 | ✅ done | Performant scoped `graph-view` re-query at scale |
| A4 | Delta classifier (re-extract only changed blocks) | 0006 | ✅ done (metadata-only tier; block-level reuse deferred) | Full editable-`document` → re-ingest loop |
| A5 | Vocabulary command (controlled filing vocabulary) | 0012 | ✅ done | Placement Card (needs the vocabulary to suggest against) |
| A6 | Placement classifier | 0012 | ✅ done | Placement Card |
| A7 | Agent SDK resume (durable session lifecycle) | 0007/0013 | ✅ done | `skill` Card: author + run skills, resume across turns |

A1 sub-parts (all done): A1a block/offset ids on chunks · A1b edge provenance
(doc_ids/chunk_ids) · A1c stable path-based logical identity · A1d idempotent replace /
tombstone / purge · A1e property provenance ledger.

## Canvas client phases

### Phase 0 — walking skeleton ✅ done
Chat turn → `render` event → **one read-only `document` Card**. Establishes the
seam: dedicated backend (`CanvasBackend` wrapping artmind's `AgentBackend`), the
unclipped `render` event (ADR 0014), the path-guarded Vault read endpoint, and a React
Flow canvas hosting a custom `document` node. No persistence, one card type.

### Phase 1 — application backbone & reactivity (pure-client; no Track-A gate)
- **1a — App-state backbone** ✅ done (live-verified in-browser 2026-08-16). ADRs 0004 / 0014 / 0009 / 0003.
  - Generalize the canvas substrate from one hard-coded card type to a **card
    registry** (cardType → component); generalize `handleRender` to spawn any
    registered type.
  - Formalize the **Card contract** surface (ADR 0014): the `render` event already
    carries `{cardType, props}`; publish typed `props` schemas per card type,
    client-owned.
  - **Board persistence store** in the backend (ADR 0009): named boards holding card
    instances (id, cardType, props, position, size) + viewport + each card's filter
    spec. Stored in a canvas-owned state dir (`ARTMIND_CANVAS_STATE_DIR`, default
    `~/.artmind_canvas/`), never in the Vault or graph. CRUD endpoints; board
    load/save/switch in the UI.
- **1b — Graph reactivity** ✅ done (live-verified in-browser 2026-08-16). ADR 0008.
  SSE change-event broadcast (`GET /api/events`) + per-Card scoped re-query. Vault writes
  publish a `{type:change,resource:document,path}` event; the read-only `document` Card
  subscribes and auto-refetches when its path changes. Builds on 1a's board/card-instance
  model. (0009's store and 0008's re-query are coupled — 1b lands on top of 1a.)
- **1c — Markdown editor pane & three-pane workspace** ✅ done (committed `6e182e3`,
  live-verified in-browser 2026-08-16). ADR 0015 (supersedes 0005,
  extends 0009). Read-only Cards + a **CodeMirror 6 source editor** bound to a Vault file
  path; collapsible Chat │ Canvas │ Editor push/squeeze layout; explicit save → 0011
  watcher re-ingest (editor never re-ingests directly); conditional-write clobber guard +
  re-read on focus; manual **"save to…"** placement (A5/A6 upgrade it to *suggested* later);
  tabbed open docs persisted **per-board** (`openDocuments[]`/`activeDocument`), panel
  geometry **global** (`settings.json`). Net-new: a path-guarded Vault **write** endpoint +
  app-settings store (backend), CodeMirror + panes (frontend). **Decoupled from 1b** — ships
  in either order; 1b only adds the Card-auto-refresh-after-save nicety.

### Phase 2 — Card types unblocked now (no Track-A gate) ✅ done
- **`provenance` Card** ✅ done (live-verified in-browser 2026-08-16, branch `canvas-phase1`).
  ADRs 0007/0014. Read-only "where did this come from?" Card. Backend `GET /api/provenance`
  routes through the `artmind serve` daemon (`artmind_query.py` seam, stdlib HTTP) → `artmind
  query entity-context`/`entity-resolve` — **the canvas never opens its own Neo4j driver**.
  Given an `entityId` (or a free-text `reference` the backend resolves first), it lists the
  source documents/chunks the fact was extracted from (`EXTRACTED_FROM` provenance, A1).
- **`micro-UI` Card** ✅ done (live-verified in-browser 2026-08-16, branch `canvas-phase1`).
  ADR 0014, tier (c). Arbitrary agent-authored HTML/JS in a **strictly sandboxed iframe**:
  `srcdoc` + `sandbox="allow-scripts"` *without* `allow-same-origin`, so the frame runs on a
  unique opaque origin — scripts execute but cannot reach the app DOM, cookies, storage, or
  same-origin requests. Schema-free (`props.html`, carried on the declarative props path so it
  round-trips through board persistence). Canned `/microui-test` hook for offline verification.

### Phase 3+ — Track-A-gated Cards (order follows Track A delivery)
- **`graph-view` Card** — needs a graph node-link endpoint (0003) + a new frontend graph
  lib (Cytoscape/sigma, ADR 0004) + A3 indices (perf) + A2 (filing filters).
- **Editable `document`** — the editor itself ships in **Phase 1c** (ADR 0015),
  **decoupled from A4**. A4 (delta classifier) is an *optimization* — it makes re-ingest
  re-extract only changed blocks instead of a full idempotent replace — not a gate for
  editing.
- **`skill` Card** — needs A7 SDK resume. ADRs 0007/0013.
- **Placement Card** — propose→review→confirm; needs A5 vocabulary + A6 classifier + A3. ADR 0012.

## Card-type → dependency matrix

| Card | Client work | Track-A / other gate | Earliest phase |
|------|-------------|----------------------|----------------|
| `document` (read-only) | done | — | 0 ✅ |
| `document` (editable) | Editor pane (ADR 0015) ✅ | A1 ✅ (A4 only optimizes re-ingest) | 1c ✅ |
| `provenance` | card + backend read endpoint ✅ | A1 ✅ | 2 ✅ |
| `micro-UI` | sandboxed iframe ✅ | — | 2 ✅ |
| `graph-view` | card + graph lib + node-link endpoint | **A2, A3** (+ new npm dep) | 3 |
| `skill` | authoring UI + run | **A7 ✅** | 3 |
| Placement Card | propose/review/confirm UI | **A5, A6, A3** | 3 |

## Current position

Phase 0 complete and committed (`08f0834`). **Phase 1a complete and committed (`092cef6`,
branch `canvas-phase1`), live-verified in-browser 2026-08-16.** **Phase 1c complete and
committed (`6e182e3`, same branch), live-verified in-browser 2026-08-16** — editor pane,
conditional-write clobber guard, three-pane workspace. **Phase 1b complete (graph
reactivity, ADR 0008), live-verified in-browser 2026-08-16** — SSE change stream +
Card auto-refresh on Vault write. **Phase 1 is done.** **Phase 2 is done (branch
`canvas-phase1`, live-verified in-browser 2026-08-16):** `provenance` Card (reads via the
`artmind serve` → CLI front door, never Neo4j directly) and `micro-UI` Card (strictly
sandboxed iframe). **Track A: A0, A1, A2, A3, A4, A5 done** — A2 delivered filing taxonomy
(project/area/tags/title/created_on/modified_on) as first-class Document + DocChunk
properties, with `artmind query graph filing-listing` CLI + Neo4j indexes on filing fields.
A3 added composite/fulltext indexes for scoped graph-view re-queries
(entity_class, entity_class+domain, project+domain, area+domain on Document & DocChunk;
chunk (doc_id, id); document_name; document_name+title fulltext).
A4 landed the three-tier delta classifier (ADR 0006 (f)) with the metadata-only fast
path fully wired into `ingest_to_kg`: a re-ingest whose body block-hashes match the
prior version's takes a bare Cypher SET on Document + DocChunk filing props, skipping
chunking, LLM extraction, and supersede entirely. `artmind ingest classify-reingest`
inspects the tier without touching the graph. Block-level reuse (dedup entity extraction
across unchanged chunks on content changes) is deferred — content-tier reingests still
go through the A1d idempotent-replace pipeline.
A5 shipped the controlled filing vocabulary command: `artmind query graph vocabulary`
returns distinct project/area/tags/domain values with document counts, scoped optionally
by --domain and gated by --minCount. Grounds the A6 placement classifier in labels
already in use so proposals stay consistent (`Alpha`, not a fresh `proj-alpha`).
A6 shipped the placement classifier: `artmind query propose-placement` reads text from
--text/stdin, pulls the A5 vocabulary, prompts the LLM with the existing labels + counts,
and returns a normalized proposal `{domain, area, project, tags, target_file_hint}` with
per-facet confidence and a `known` flag per label (existing vs invented). Never writes —
the canvas placement Card renders the proposal for user review, and only then does the
doc-first path (ADR 0002) write frontmatter and trigger re-ingest. Domain is treated as
always-confirmed since a domain change forces re-extraction (ADR 0006 (f)). Together with
A2/A5, the propose→review→confirm flow (ADR 0012) is now end-to-end in artmind.
A7 shipped the durable session lifecycle (ADR 0007/0013): the Claude SDK chat
backend now captures the SDK `session_id` off the streamed messages and gained
`restart(preserve_context=True)`, which tears down and rebuilds its client with
`ClaudeAgentOptions(resume=<id>)` so the conversation survives a refresh —
the mechanism that lets a freshly-authored `SKILL.md` be discovered (skills are
read only at session start, no hot-reload). `SessionRegistry.refresh()` drives it
under the registry lock, and `POST /api/session/{id}/refresh` exposes it, reporting
whether context was preserved. The ACP backend restarts clean (context lost) and
reports `supports_resume=False`, which ADR 0007 explicitly accepts. The *when/where*
(write `SKILL.md`, into which canvas-owned skills dir) stays client-side per ADR
0007's boundary — the canvas calls `refresh` after authoring.

**Track A is complete (A0–A7).** Every artmind-package capability the Phase 3 Cards
need is delivered. Next is Phase 3 itself — the Track-A-gated Cards, built in
`artmind_canvas/` as a pure client: `graph-view` (A2/A3 + a node-link endpoint +
graph lib), the Placement Card (A5/A6 propose→review→confirm UI), and the `skill`
Card (A7 authoring + run + refresh).
