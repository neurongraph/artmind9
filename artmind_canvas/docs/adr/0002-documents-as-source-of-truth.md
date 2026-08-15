# Documents (the Vault) are the source of truth; the graph is a projection

Knowledge lives in three places: the Neo4j Knowledge Graph, artmind's Data dir
(ingestion artifacts), and the Vault (the markdown/files the user works in). artmind
has two physical write paths — `ingest` (document → extraction → graph) and `update`
(write the graph directly). We decided the Vault is authoritative and the Knowledge
Graph is a projection derived by ingestion. All knowledge writes, including
chat-authored ones, land as a document/block in the Vault first, then re-ingest.

Why: the entire artmind_canvas experience is provenance-centric — "see the articles/
blocks the info came from." If the graph could hold sourceless facts, provenance
breaks and "re-ingest" becomes ambiguous.

Consequences:
- To make "edit → re-ingest → the same nodes update" real (rather than the current
  accretive-append behaviour), we chose to invest in artmind's pipeline — block-level
  provenance, stable document identity, idempotent re-ingest. See ADR 0006.
- Direction settled (Q14): chat-authored knowledge is **doc-first with agent-proposed,
  user-confirmed placement** — the agent proposes which Vault file/section (and
  classification) a new block lands in, the user confirms, then it writes + re-ingests.
  The direct-write-to-graph path (`update`) is *not* used by the canvas. The *mechanism*
  of that proposal (how domain / area / project / tags are inferred) is under active
  modeling in the knowledge routing/retrieval branch.
