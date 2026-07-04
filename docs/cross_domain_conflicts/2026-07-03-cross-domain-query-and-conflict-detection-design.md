# Cross-Domain Query + Conflict Detection for artmind

**Status:** Draft — pending review
**Date:** 2026-07-03
**Related:** docs/superpowers/specs/2026-05-09-poole-hierarchical-domains-design.md · `2026-07-03-temporality-design.md` (sibling spec — temporality is the conflict model's main resolver; see its §8)

## Context

artmind restricts every query to a single `--domain`. The fee-reversal example shows two failures at once:

1. **Missed evidence.** "Who can decide a fee reversal after a complaint?" asked against `banking_policy` retrieves `policy_complaints.md` but never sees `escalation_matrix.md`, which lives in `banking_sop_guides`. The banking domains are flat siblings, so the existing hierarchical rollup (`fiction` → `fiction.thriller`) can't bridge them.
2. **Undetected contradiction.** The two documents genuinely disagree: `policy_complaints.md` says <£100 = CS Manager, £100–£500 = Director, **>£500 = CEO**; `escalation_matrix.md` says **frontline staff approve fee reversals <£500** and a **Manager up to £1,000** (Director only >£1k). A £700 reversal is a Manager decision in one source and a CEO decision in the other. Today nothing detects or surfaces this.

Verified ground truth: domain is a **node/rel property** in one Neo4j DB, enforced by the predicate `(x.domain = $domain OR x.domain STARTS WITH ($domain + '.'))` repeated ~20× in `artmind/graph_query.py`, 6× in `artmind/vector_query.py`, and as a prompt rule in `artmind/text2cypher.py:101`. 16 query commands in `artmind/cli.py` (lines 711–962) take a required single `--domain`. So cross-domain is a CLI/skill-layer change, not a storage change. The existing `refine-graph` ER (`artmind/refine_graph.py`) destructively merges same-named entities (`apoc.refactor.mergeNodes` with `properties:'discard'`) — the opposite of what conflicts need; conflict handling must be non-destructive annotation.

## Design decisions (the three open questions)

**Q1 — Cross-domain queries → multi-domain CLI on a centralized predicate + a `domains-overview` router command.**
Make `--domain` repeatable (Click `multiple=True`, each value also comma-splittable) on all query commands. All Cypher goes through one new predicate builder; a one-element list is semantically identical to today, so single-domain behavior is unchanged. Add `artmind query domains-overview` (no args) returning per-domain doc names/counts, entity counts, top classes — the cheap routing input that maps "banking" to concrete sibling domains.
*Rejected:* sub-agent-only orchestration (retrieval patterns would still be single-domain — pattern5/6/vector-text could never join across domains); renaming `banking_*` → `banking.*` for rollup (data migration; only fixes pre-declared hierarchies — kept as optional Phase 3); `--all-domains` (invites unscoped noisy queries; redundant once overview + repeatable `--domain` exist).

**Q2 — Discover-as-sub-agent → yes, but in SKILL.md only.** The CLI stays deterministic; orchestration belongs to the skill. SKILL.md gains a "Route & Scout" step: when the user names an area rather than an exact domain, or multiple domains are plausible, or listings are large, launch ONE sub-agent that runs `domains-overview` + per-domain `metadata --compact` + `entity-resolve`, and returns only a compact routing report `{domains, resolved_entities:[{id,name,class,domain}], relevant_classes, relevant_rel_types}`. Main context never sees raw listings. Single known small domain: inline Discover unchanged.

**Q3 — Conflict detection → both, phased: materialized at refine time (primary) + free query-time adjudication in the skill.**
- Refine time: new non-destructive `artmind ingest detect-conflicts` (new module `artmind/conflicts.py`). Cheap candidate pairing (name similarity + entity-embedding cosine across domains) → LLM adjudicates only candidate pairs with bounded evidence chunks → materialize `Conflict` nodes + `CONFLICTS_WITH` edges. Two-phase `--dry-run --output` / `--from-file`, mirroring refine-graph's workflow but only ever CREATEs annotations.
- Query time: SKILL.md "Adjudicate" step — after Ground, compare retrieved claims across documents/domains (no extra LLM calls; evidence already in context) and check `query graph conflicts` for resolved entity ids. Catches conflicts introduced by new docs between detect-conflicts runs.
- Mandated surfacing format: *"Sources disagree: policy_complaints.md (banking_policy) says X; escalation_matrix.md (banking_sop_guides) says Y"* — both claims, both provenances, never silently pick or blend.
*Rejected:* query-time-only (re-derives every time and misses conflicts when retrieval returns only one side — today's exact failure); refine-time-only (stale between runs; skill check is free); reusing merge machinery (destructive; a conflict is evidence two nodes must NOT merge).

## Data model (Neo4j)

```cypher
(:Conflict {
  id,              // sha1(min(idA,idB)+'|'+max(idA,idB)+'|'+aspect_slug) → idempotent MERGE
  aspect,          // "fee reversal approval limit"
  claim_a, claim_b, severity,      // 'high'|'medium'|'low'
  status,          // 'open'|'resolved'|'dismissed' (detect writes 'open')
  domains, detected_at, detected_by_model
})
(:Conflict)-[:CONFLICT_OF]->(:Entity)             // both sides
(:Conflict)-[:EVIDENCE {side:'a'|'b'}]->(:DocChunk)
(:Entity)-[:CONFLICTS_WITH {conflict_id, aspect}]->(:Entity)  // so patterns 3/4/6/8 surface it for free
```

**Decided (review 2026-07-03):** conflicts are **entity-anchored, chunk-evidenced**. The `Conflict` anchors on entities (stable, resolvable via `entity-resolve`) and carries the real competing claim in the `EVIDENCE` chunk text — *not* anchored on a relationship or a specific extracted property. This is robust whether the extractor captured a claim as a property or as a relationship.

**Temporal interaction (see sibling spec `2026-07-03-temporality-design.md` §8):** once temporality lands, `llm_adjudicate()` gains a `superseded` verdict (newer revision of the same authority → `SUPERSEDES` edge + `valid_to`, not an open Conflict), `status: resolved` gains `resolution: 'superseded'|'precedence'|'manual'`, and the conflict definition sharpens to *claims whose valid-time intervals overlap and where neither supersedes the other*. Without the `superseded` verdict, `detect-conflicts` would fill with false positives that are really version history — the sibling spec's Phase T2 should land with or just after Phase 2 here.

## CLI surface

Changed — all 16 query commands (`metadata`, `structural-metadata`, `entity-listing`, `pattern1–10`, `text2cypher`, `vector-text`, `entity-resolve`):
```
--domain TEXT   [repeatable; comma-splittable; at least one required]
```
JSON output keeps `"domain": "<d>"` when exactly one domain (parser back-compat), always adds `"domains": [...]`.

Changed — `artmind ingest refine-graph` gains a guard:
```
--allow-cross-domain-merge   [default: off]
```
When `--domain` is omitted, refine-graph still processes all domains but by default only merges **within-domain** clusters; a cluster spanning two domains is skipped (and reported). Passing `--allow-cross-domain-merge` restores today's behavior of collapsing same-named entities across domains. Rationale: cross-domain same-named entities are exactly the pairs the conflict pipeline needs to keep separate — an accidental merge destroys materialized/undetected conflicts. Decided (review 2026-07-03) in favour of an explicit opt-in flag over a warning.

New:
```
artmind query domains-overview [--compact]
artmind query graph conflicts --domain A [--domain B ...] [--entityId ID]... [--entityName TEXT]
    [--status open|resolved|dismissed|all] [--compact]
artmind ingest detect-conflicts --domain A [--domain B ...]
    [--nameFilter TEXT] [--simThreshold 0.75] [--maxPairs 200] [--maxChunksPerSide 2]
    [--model TEXT] [--dry-run] [--output conflicts.json] [--from-file conflicts.json]
```
One domain to `detect-conflicts` = intra-domain contradictions; two+ = cross-domain pairing. LLM cost hard-bounded by `--maxPairs`.

## Changes per file

**`artmind/graph_query.py`**
- Add `normalize_domains(str | Sequence[str]) -> list[str]` (comma-split, strip, dedupe) and
  `domain_predicate(var, param="domains")` → `"({var}.domain IN $domains OR any(d IN $domains WHERE {var}.domain STARTS WITH (d + '.')))"`.
- Replace all inline predicates (lines 151–550) with `domain_predicate(...)`; param becomes `domains` list. `execute_pattern` + public functions accept `str | list[str]`.
- Add `.domain` to chunk/document projections in patterns 2/3/4/10 so multi-domain rows are attributable.
- New `domains_overview()` (one aggregation grouped by `n.domain`) and `list_conflicts(domains, entity_ids, entity_name, status)`.

**`artmind/vector_query.py`** — replace the 6 predicates (lines 97–259) with `domain_predicate`; accept domain lists; scale `candidateK` by `len(domains)` so post-filtering doesn't starve a domain.

**`artmind/text2cypher.py`** — accept domains list; prompt rule (~line 101) becomes the `IN $domains` form with `"domains"` in parameters. Phase 2: add Conflict/CONFLICTS_WITH/CONFLICT_OF/EVIDENCE to the structural schema in the prompt.

**`artmind/cli.py`** — helper `_parse_domains(values)`; flip the 16 query `--domain` options to `multiple=True`; register `query domains-overview`, `query graph conflicts`, `ingest detect-conflicts` (mirror `ingest refine-graph`'s dry-run/output/from-file wiring at cli.py:588–681).

**`artmind/refine_graph.py` + `artmind/cli.py`** — add the `--allow-cross-domain-merge` flag (default off) to `refine-graph`. When `--domain` is omitted, tag each cluster with the set of domains it spans; skip (and report in the run summary) any multi-domain cluster unless the flag is set. Single-domain runs are unaffected. Protects the conflict pipeline from destructive cross-domain merges.

**`artmind/setup.py`** — `Conflict.id` uniqueness constraint + `Conflict.status` index; include in setup summary.

**`artmind/conflicts.py` (new, ~250 lines)** — `candidate_pairs()` (difflib name ratio OR embedding cosine ≥ threshold, cross-domain pairing, ranked, truncated to maxPairs), `gather_evidence()` (top-k MENTIONS chunks, truncated), `llm_adjudicate()` (JSON verdict: `same_entity_consistent | conflicting_claims | unrelated`, aspect, claims, severity — reuse `_call_llm_text`/`_parse_json_response` from `artmind.ingest` as refine_graph does), `materialize()` (MERGE-only writes), `detect_conflicts()` orchestrator. No shared code path with the merge machinery.

**`skills/artmind-query/SKILL.md`** — protocol becomes **Route → Discover → Resolve → Retrieve → Ground → Adjudicate**:
- *Route (new):* run `domains-overview`; if the user names an area or >1 domain is plausible, select all relevant sibling domains (policies and SOPs about the same subject live in different domains by design). Sub-agent rule per Q2.
- *Retrieve/Ground:* pass `--domain` once per selected domain; every answer attributes facts to document AND domain.
- *Adjudicate (new):* run `query graph conflicts` for resolved entity ids; independently compare quantitative/authority claims across retrieved documents; surface disagreements with the mandated both-sides format. Never average or silently drop one side.
- Fallback ladder addition: thin results in the chosen domain → re-run with sibling domains from Route before concluding data is absent.

## Phasing

- **Phase 1 — cross-domain retrieval (no LLM cost, highest value):** predicate builder + graph_query/vector_query/text2cypher rewrite + CLI `multiple=True` + `domains-overview` + SKILL.md Route/sub-agent/attribution/query-time Adjudicate. This alone fixes the motivating query.
- **Phase 2 — materialized conflicts:** `conflicts.py`, `detect-conflicts`, Conflict model + setup constraint, `query graph conflicts`, SKILL Adjudicate consults materialized conflicts, text2cypher schema mention. Includes the `refine-graph --allow-cross-domain-merge` guard (ships here so an accidental cross-domain merge can't silently erase conflicts once they can exist).
- **Phase 3 (optional, later):** `banking_*` → `banking.*` migration tooling to exploit the existing rollup.

## Verification

1. **Single-domain regression:** existing query CLI tests pass (additive `"domains"` key only); unit test: `domain_predicate` with a one-element list matches the same node set as the old predicate.
2. **Multi-domain retrieval:** `uv run artmind query vector-text --domain banking_policy --domain banking_sop_guides --topK 8 --compact "who can approve a fee reversal after a customer complaint"` → chunks from BOTH `policy_complaints.md` and `escalation_matrix.md`. `entity-resolve` across both domains returns fee-reversal entities from both; `pattern2` with mixed-domain ids returns correct per-row `domain`.
3. **Conflict pipeline:** `detect-conflicts --domain banking_policy --domain banking_sop_guides --dry-run --output conflicts.json` proposes a fee-reversal-authority conflict with claim_a = policy tiers (>£500 CEO) and claim_b = matrix (Manager £1,000); apply via `--from-file`; `query graph conflicts` returns it with EVIDENCE chunks from both docs; re-run creates no duplicates (deterministic id).
4. **End-to-end skill acceptance:** the original question via artmind-query must (a) cite both documents with domains, (b) state both threshold schemes, (c) explicitly flag the contradiction rather than blending.
5. **Cost check:** detect-conflicts reports `llm_calls ≤ maxPairs`; expect <50 candidate pairs on the banking corpus at threshold 0.75.
6. **Merge guard:** `refine-graph` (no `--domain`, no flag) leaves a same-named "Fee Reversal" entity in both banking domains distinct and reports the skipped cross-domain cluster; adding `--allow-cross-domain-merge` merges them (restoring prior behavior).
