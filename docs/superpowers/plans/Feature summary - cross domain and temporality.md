Here's the feature summary across the whole plan, phase by phase:

Phase 1 — Cross-Domain Retrieval
Artmind's queries were single-domain only. Now:

Every query command (metadata, structural-metadata, entity-listing, pattern1–pattern10, text2cypher, vector-text, entity-resolve) takes a repeatable --domain flag — you can query multiple domains in one call, and sub-domains roll up automatically (banking matches banking.policy, etc.).
New query domains-overview command — a cheap routing summary (doc/entity counts, top classes per domain) to figure out which domains to target before running anything expensive.
artmind-query skill gained a Route step (pick domains first) and an Adjudicate step (surface disagreements across domains instead of silently blending them).
Phase T1 — Temporal Mechanics
Artmind had no notion of time. Now:

Canonical valid_from / valid_to / event_at / ingested_at properties, normalized automatically per-document at ingest time (and backfillable via ingest normalize-time).
--asOf <date> filter on query commands — ask "what was true on this date" instead of only ever seeing the full history mixed together.
7 domain schemas got a temporal: block declaring which of their properties map onto this canonical timeline.
Phase 2 — Materialized Conflicts
Previously, if two domains disagreed (e.g. a policy and its SOP guide), nothing detected it. Now:

ingest detect-conflicts --domain A --domain B — finds candidate entity pairs across domains (via embedding similarity, class-blocked), has an LLM adjudicate whether they genuinely conflict, and materializes Conflict nodes as evidence-backed graph data (non-destructive, dry-run/apply workflow).
query graph conflicts — read back materialized conflicts with both sides' claims and source chunks.
refine-graph (entity deduplication) got a cross-domain merge guard: it won't silently merge same-named entities across domains, because those are exactly the pairs detect-conflicts needs to compare.
Phase T2 — Supersession
Without this, an updated policy document looked like a "conflict" with its own older version. Now:

SUPERSEDES relationship + ingest supersede (manual) and ingest detect-supersession (auto, parses "Supersession Notice" sections) — marks one document as replacing another, setting valid_to on the old one.
Conflict adjudication now has a superseded verdict — a same-lineage version difference routes to supersession, not a Conflict node.
query graph timeline — renders an entity's/document's ordered history of events and supersessions.
Combined with --asOf, present-tense questions now correctly see only the current document version.
Phase 3 — Migration Tooling (optional)
One-off script to migrate flat banking_* domain names to a hierarchical banking.* naming scheme, for anyone who wants the sub-domain rollup behavior on existing data.
Phase T3 — State-Change Reification (optional, done this session)
Added a canonical STATE_CHANGE entity class (with event_at temporal mapping) to the journaling/fiction/governance schemas, so narrative state shifts (mood, status, relationship changes) get first-class graph nodes instead of being bolted onto unrelated entities.
artmind-create-schema skill now has a temporal-design step so new schemas get this treatment by default.
Plus, this session: artmind-refine-graph skill
A new guided skill wrapping all of the above maintenance operations (dedup cleanup, cross-domain conflict detection with cost warnings, merge/conflict investigation, supersession reconciliation) — previously these were CLI commands with no skill-level guidance on sequencing or safety.