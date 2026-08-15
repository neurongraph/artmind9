# Invest in artmind's ingestion pipeline for block-level provenance and idempotent re-ingest

artmind_canvas's core loop — trace a graph fact to its source, edit that source, and
re-ingest in place — exceeds what artmind supports today. As built: provenance is
chunk-level only (~6000-char chunks, no offsets), relationship→chunk provenance is
dropped when edges are written to Neo4j, documents have no stable identity across
edits (filename + hash only), and re-ingest is accretive-append (an edit creates a
*duplicate* Document rather than updating the original). Even the `docs clean` +
re-ingest workaround is unsafe because entities are merged across documents.

We decided to invest in the artmind ingestion pipeline itself, adding: (a) block/offset
ids on chunks, (b) retained relationship→chunk provenance on graph edges, (c) a stable
logical document identity decoupled from filename/hash, and (d) an idempotent
"re-ingest → replace this document's derived nodes" operation (with safe handling of
entities shared across documents).

Two further additions come from the filing-metadata decision (ADR 0010): (e) **ingest
document metadata** — project / area / tags, plus always-present baseline fields
(`created_on`, `modified_on`, `title`, defaulted from filesystem timestamps or
first-ingest time when frontmatter omits them) — onto Document (authoritative) and
DocChunk (denormalized), never onto shared Entity nodes; and (f) a **three-tier delta
classifier** on re-ingest (Q23): a metadata-only change → in-place property `SET`, no
re-extraction and no supersede; a content change → full idempotent re-ingest + supersede;
and a **domain change counts as content**, because it re-selects the extraction schema
and so forces re-extraction.

Why: without these the round-trip is coarse and shared-entity retraction is unsafe;
and these are core improvements every artmind consumer benefits from, not canvas-only
hacks.

Boundary (this is the key point): these upgrades are built **in the shared `artmind`
package** (Q17) as first-class artmind capabilities. artmind_canvas — both its client
UI and its thin backend — stays a **pure client** that consumes them; it does not
implement pipeline logic. ADR 0003 stands unchanged. This defines two distinct
workstreams: (1) artmind pipeline upgrades, a prerequisite dependency delivered inside
artmind; (2) the artmind_canvas client that consumes them.

Consequence: a substantially larger overall effort, but the client stays thin and the
pipeline work benefits every artmind consumer, not just the canvas.
