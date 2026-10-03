# Adjudicate — surface disagreements, never blend

Read this when a question involves comparing claims across documents or entities,
or you suspect two sources disagree.

`:Conflict` now has two shapes sharing one label, told apart by `_source` (see
SKILL.md's structural schema) — check for both before concluding nothing disagrees:

**Adjudicator-produced (`_source: 'adjudicator'`)** — a pairwise, cross-entity
disagreement found by a detection pass (`ingest detect-conflicts`, artmind-curate's
territory), linked via a `CONFLICTS_WITH` edge between the two entities.

This edge is its own Neo4j relationship type, `:CONFLICTS_WITH` — **not** a
`RELATES_TO` edge — so `entity-context`/`pattern3`/`pattern4` never return it: their
`connections` traverse `RELATES_TO` only. A `connections` entry whose `rel_type` reads
`CONFLICTS_WITH` is something different: an *extracted* relationship (the source text
said the two clash — an argument, a rivalry), not a detected disagreement between
sources. Don't report it as one. The only way to reach the adjudicator shape is the
dedicated lookup:

```bash
artmind query graph conflicts --domain <d1> --domain <d2> --entityId <id> --compact
```

Resolve to the *specific claim-bearing entity* (e.g. "Mortgage Statement"), not an
umbrella container (e.g. the policy or process that mentions it) — CONFLICTS_WITH sits
on the concrete entities being compared, not their containers.

This matches the `CONFLICTS_WITH` edge between entities directly and reaches only this
adjudicator shape — it has no visibility into a projection conflict (below). Each row
carries a `materialized` flag: `true` means `claim_a`/`claim_b`/`evidence`/`severity` are
populated straight from the `Conflict` node; `false` means only `aspect` and the two
entities are known (the edge survives independently of the node), so pull grounding
yourself via `chunks`/`vector-text` on those entities before stating the claims. Either
way, surface `aspect` and both entities' `name`/`domain`; never assert `claim_a`/`claim_b`
text that isn't actually present on a `materialized: true` row. This shape is a detection
pass's output — a snapshot, not a live guarantee — so also independently compare the
claims you actually retrieved (below) to catch a disagreement introduced by a document
ingested since the last `detect-conflicts` run.

**Projection-produced (`_source: 'projection'`)** — one entity's own property disputed
*within a single instant* (two observations with the same `valid_from` disagreeing;
disagreement across different instants is temporal variation, not a conflict — see
`_temporal_props` in `references/temporal.md`). Raised automatically by every rebuild via `CONFLICT_OF` to that
one entity and `EVIDENCE` to the disputing `:Observation`s — never orphaned, since it's
recomputed from scratch every time, and never a snapshot to go stale. **There is no
dedicated command for this shape yet** — `query graph conflicts` cannot reach it (no
`CONFLICTS_WITH` edge exists for a single-entity dispute). The way to notice one: pull
`entity-history --entityId <id> --property <p>` and look for two rows sharing the same
`_valid_from` with different values; if you see that, report it as an open dispute
directly from those rows rather than expecting `query graph conflicts` to surface it.

**Fan-out caveat** (adjudicator shape): one real disagreement (e.g. a document-tier
reclassification) can produce many pairwise conflict rows sharing the same root cause
across different document pairs in the same tier bucket — group rows by shared
`aspect`/entity-class pattern and report the *underlying* disagreement once, not each
pairwise row separately.

After grounding, compare quantitative/authority claims across the retrieved
documents and domains (no extra LLM calls — the evidence is already in context).
When two sources disagree, surface BOTH claims with BOTH provenances in this format:

> Sources disagree: policy_complaints.md (banking_policy) says X; escalation_matrix.md
> (banking_sop_guides) says Y.

Never average, reconcile silently, or drop one side. If retrieval returned only one
side, re-run Ground with the sibling domains from Route before concluding.

Qualify claims by time: report present-tense answers "as of <date>, source A says X".
A claim whose document is superseded (has `superseded_by` / a `valid_to` in the past)
is HISTORY, not a live disagreement — say so. Before treating a materialized Conflict
as live, verify both documents' valid-time windows overlap and neither supersedes the
other — detect-conflicts' LLM adjudication tries to catch this at detection time, but
it isn't a structural guarantee, so re-check at query time using each side's
`valid_to`/`superseded_by`.
