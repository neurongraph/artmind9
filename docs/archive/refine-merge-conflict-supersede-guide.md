# artmind: Refining, Merging, Conflict Detection & Supersession — Field Guide

> ⚠️ **STALE — Pre-redesign (July 2026).** This field guide documents the refine/conflict/supersession machinery **before** the observation/projection architectural redesign (Phase 8, Aug 2026). It is source material for the `artmind-refine` skill but does not reflect the current `:Observation` node model. Current reference docs: [`projection-pipeline.md`](../projection-pipeline.md), [`document-identity.md`](../document-identity.md). Read this only for historical context on how merge/conflict/supersession were designed.

This doc explains the cross-domain refinement/conflict/temporality machinery added in
`docs/superpowers/plans/2026-07-04-cross-domain-conflicts-and-temporality.md`, in plain
terms, with real examples pulled from the live `banking-corpus` graph. It's written to
double as source material for the `artmind-refine` skill (see §4).

**Status as of this writing:** Phase 1 (cross-domain retrieval), Phase T1 (temporal
mechanics: `--asOf`, canonical `valid_from`/`valid_to`, per-document normalization),
Phase 2 (materialized conflicts), and Phase T2 (document supersession) are all complete.
Phase 3 (banking_* → banking.* migration tooling) and Phase T3 (STATE_CHANGE reification)
remain unbuilt — both are explicitly marked optional/later in the plan and were
deliberately skipped for this implementation pass.

---

## 1. Concepts

### 1.1 Why any of this exists

Document extraction runs per-chunk, mostly independently. When two different chunks (or
two different documents) both mention "the Complaints Handling Policy," or "the
customer," or "MFA," the extractor has no memory of a prior mention — it mints a new
`Entity` node from whatever phrasing appears in that chunk. Across ~150 policy/SOP
documents this produces heavy fragmentation: dozens of near-duplicate nodes for what is
really one real-world concept. Two independent problems then need solving:

1. **Within a domain**, the same concept is fragmented across many entity nodes —
   solved by **refine-graph** (clustering + LLM-decided merging).
2. **Across domains**, two *genuinely distinct* entities (e.g. a policy's approval-limit
   rule and a separate SOP's approval-limit rule) may describe the same real-world thing
   but disagree — this is not fragmentation to merge away, it's a **conflict** to
   surface, never silently resolved. Solved by **detect-conflicts**.

A third axis, **time**, cuts across both: a document can simply be a newer *revision* of
an older one (supersession — not a conflict, just history), and facts can have a
validity window (`valid_from`/`valid_to`) so queries can ask "what's true right now" vs.
"what was true historically." This is **Phase T1/T2** (temporality).

### 1.2 Domain scoping (`domain_predicate`)

Every query-side function accepts a **list** of domains (not a single domain string).
`normalize_domains()` flattens comma-separated/repeated `--domain` flags into a deduped
list; `domain_predicate(var)` builds a Cypher `WHERE` fragment that matches a node's
`.domain` property against that list, with rollup support (`banking` matches
`banking.policy`, `banking.sop_guides`, etc. via `STARTS WITH`). This is the foundation
everything else in Phase 2/T1/T2 is built on top of.

### 1.3 Refine-graph: entity deduplication (merging & aliasing)

`artmind ingest refine-graph` (pre-existing command, extended in this plan):

1. Fetches distinct entity names in the target domain(s).
2. Clusters them by string similarity (`cluster_entities`, `difflib`-based).
3. For each multi-entity cluster, asks an LLM (`llm_merge_cluster`) which names are
   really the same real-world thing and which canonical name to merge them under.
4. **Applies** the merge via Neo4j APOC: the *alias* entity's name is appended to the
   *canonical* entity's `aliases` list property, all of the alias's relationships are
   re-wired onto the canonical node, and the alias node is **deleted**.

This is a genuinely destructive operation (old nodes disappear) — hence the two-phase
`--dry-run --output x.json` → review → `--from-file x.json` workflow, mirrored later by
`detect-conflicts`.

**Cross-domain merge guard** (new in this plan): when running refine-graph across *all*
domains at once (no `--domain` filter) with no `--allow-cross-domain-merge` flag, any
proposed merge whose two names span more than one domain is **dropped and reported**
under `skipped_cross_domain` instead of applied. This protects the exact same-named
entities that `detect-conflicts` needs to compare across domains — if refine-graph
silently merged "Fee Reversal" (banking_policy) into "Fee Reversal" (banking_sop_guides),
there would be nothing left to compare for a cross-domain conflict.

**`RefineRun` marker** (new in this plan): every successful apply (whether via direct
compute-then-apply, or via `--from-file`) writes a `(:RefineRun {domain, at})` node per
domain touched. This is a precondition signal `detect-conflicts` checks before running —
it warns (doesn't block) if a target domain has never had refine-graph run, since
candidate pairing works much better against deduplicated entities.

### 1.4 Detect-conflicts: cross-domain conflict detection

`artmind ingest detect-conflicts` (new command, `artmind/conflicts.py`) is the mirror
image of refine-graph for the "genuinely different, don't merge" case. Four stages:

1. **Candidate pairing** (`candidate_pairs`) — NOT a brute-force cross-product. Entities
   are blocked by `entity_class` (only compare POLICYs to POLICYs, CUSTOMERs to
   CUSTOMERs, etc.), then for each entity the Neo4j `entity_embedding` vector index is
   ANN-queried for its nearest neighbors restricted to the *other* domain(s). A `difflib`
   name-similarity ratio is used only as a secondary tie-break/sort key on the ANN
   shortlist — never as the primary generator. Deduped by `(min_id, max_id)`, capped at
   `--maxPairs`.
2. **Evidence gathering** (`gather_evidence`) — for each candidate pair, fetches the top
   few source chunks each entity was extracted from (`(Entity)-[:EXTRACTED_FROM]->
   (DocChunk)`), truncated to bound LLM cost.
3. **LLM adjudication** (`llm_adjudicate` / `_verdict_from_raw`) — given both entities'
   evidence text (plus each side's Document `valid_from`/`version`), an LLM decides:
   `same_entity_consistent` | `conflicting_claims` | `unrelated` | `superseded`.
   `conflicting_claims` and `superseded` both proceed to materialization;
   `same_entity_consistent`/`unrelated` are dropped.
4. **Materialization** (`materialize`) — MERGE-only (never destructive), and branches on
   the verdict:
   - `conflicting_claims` writes a `(:Conflict {id, aspect, claim_a, claim_b, severity,
     status, domains, detected_at, detected_by_model})` node, `(:Conflict)-[:CONFLICT_OF]->
     (:Entity)` to both sides, a bidirectional `(:Entity)-[:CONFLICTS_WITH {conflict_id,
     aspect}]->(:Entity)` shortcut, and `(:Conflict)-[:EVIDENCE {side}]->(:DocChunk)`
     pointing at the actual supporting text. `Conflict.id` is a deterministic hash of
     `(sorted entity ids, aspect)`, so re-running detection is idempotent — it never
     creates duplicate Conflict nodes for the same pair + aspect (though see §3's caveat
     about aspect-phrasing drift).
   - `superseded` resolves both entities to their source Documents, determines which is
     newer by `valid_from`, and calls `apply_supersession()` (§1.6) instead — no Conflict
     node is written for same-lineage document revisions.

`detect_conflicts()` (the orchestrator) mirrors refine-graph's exact workflow shape:
`--dry-run --output x.json` → human review → `--from-file x.json` to materialize.

**Important, hard-won lesson (see git history, commit `c3ec22e`):** `gather_evidence`
originally queried a `:MENTIONS` relationship that is *never actually created* for
document chunks in this codebase (it only exists for `UserChat`→`Entity`, from the
`artmind-update` chat path). This silently returned **zero evidence** for every single
candidate pair, so the LLM was adjudicating "conflicts" from entity names/domains alone
— producing a plausible-looking but ungrounded false positive. Fixed to use the real,
populated `(Entity)-[:EXTRACTED_FROM]->(DocChunk)` relationship. **Any future work in
this area should always spot-check that `evidence_a`/`evidence_b` in a dry-run's output
JSON are non-empty before trusting the verdicts.** (Tracked more broadly for other call
sites in [GitHub issue #3](https://github.com/neurongraph/artmind9/issues/3).)

### 1.5 Temporality: `valid_from` / `valid_to` / `event_at` / `--asOf`

Orthogonal to merge/conflict: every domain schema can declare a `temporal:` block
mapping domain-specific date properties (e.g. `effective_date`) onto canonical
`valid_from`/`valid_to`/`event_at` properties. `artmind ingest normalize-time --domain X`
backfills these; the same normalization also runs automatically, per-document, right
after every `ingest sync`/`ingest async` write (but `refine-graph`/`detect-conflicts`
never auto-run — they stay explicit-call-only, verified by regression tests).
`--asOf <date>` can be added to any read query to filter to nodes whose validity window
covers that date (nodes with no temporal data are always shown — the filter is
null-safe). This lets a question like "who can approve a fee reversal **right now**"
exclude superseded/expired rules without deleting anything.

### 1.6 Supersession (Phase T2 — complete)

`(:Document)-[:SUPERSEDES {scope, effective}]->(:Document)`, set either manually
(`artmind ingest supersede`) or automatically by scanning a document's markdown for an
explicit "Supersession Notice" section (`artmind ingest detect-supersession`). Applying
a supersession sets `valid_to`/`superseded_by` on the older document (and, for
document-scope supersessions, its chunks too), so `--asOf` queries naturally stop
surfacing stale content. It also teaches the conflict adjudicator a `superseded` verdict:
two same-lineage document revisions (a v2 and v3 of the same policy) shouldn't be
flagged as a live conflict — they route to a `SUPERSEDES` edge instead (see §1.4 step 4).

Verified end-to-end against the real `banking-corpus` graph (§3.5): the automated
notice-scanning path (`detect-supersession`) couldn't complete for the actual
`policy_complaints.md`/`policy_complaints_v3.md` pair in this corpus, because the older
document's markdown source isn't present under its registered name in
`data/documents/markdowns/` (a corpus data-layout gap, not a code bug — the Supersession
Notice text itself parses correctly when tested directly). The manual path
(`ingest supersede`) was used instead and confirmed fully correct: `SUPERSEDES` edge
written with the right properties, `valid_to`/`superseded_by` correctly stamped on the
older document, and `--asOf` correctly excludes the older document's chunks afterward.

**Bug found and fixed during this verification:** the `detect_conflicts()` dry-run loop
originally only added `conflicting_claims` verdicts to its `proposals` list, silently
dropping every `superseded` verdict the LLM returned — meaning the standard
dry-run/`--output`/review/`--from-file` workflow could never actually reach
`materialize()`'s `superseded` branch, no matter how many genuine same-lineage document
pairs the adjudicator correctly identified. Fixed in `artmind/conflicts.py` to collect
both verdict types into `proposals`, since `materialize()` already discriminates them
correctly (writes a `Conflict` node for one, calls `apply_supersession()` for the other).

---

## 2. CLI Commands Reference

### 2.1 Implemented

#### `artmind ingest refine-pipeline` — the one-command orchestrator

Runs every refinement step in dependency order — `time → supersession → merge →
conflicts → consolidate → embed` — with a propose/apply gate:

```
--domain TEXT              Domain to refine (repeatable; 2+ domains add a
                           cross-domain conflicts pass after all per-domain steps)
--apply                    One-shot compute AND apply (skips the review gate)
--from-file PATH           Apply vetted proposals from a prior propose report
--steps TEXT               Comma subset of the six steps (canonical order enforced)
--mergeThreshold FLOAT     Merge clustering threshold [default: 0.7]
--simThreshold FLOAT       Conflict candidate threshold [default: 0.75]
--maxPairs INT             Conflict candidate cap per detection pass [default: 200]
--sampleConsolidations INT Consolidation previews per domain in propose mode [default: 3]
--consolidateLimit INT     Cap consolidations per domain in apply mode (default: all)
```

Propose mode runs time/supersession for real (additive, idempotent), produces
reviewable `merges_<domain>.json` / `conflicts_<domain>.json` /
`conflicts_cross.json` / consolidation samples, and writes one report under
`data/refine/pipeline/`. Apply (`--from-file <report>`) materializes the (possibly
edited) proposals in order and finishes with an embedding sweep that also refreshes
merged canonicals. With multiple domains the cross-domain conflicts pass runs only
after every domain's merge step — the §1 precondition holds by construction. The
guided review workflow lives in `skills/artmind-refine/SKILL.md`. The individual
commands below remain available for targeted runs; the pipeline exists so their
ordering constraints (see §1) are enforced by code rather than operator memory.

#### `artmind ingest refine-graph`
```
--domain TEXT               Restrict to entities in this domain (default: all domains)
--filter TEXT                Filter entities by name (comma-separated). Default: all entities in domain
--model TEXT                 LLM model for merge decisions (default: env var)
--threshold FLOAT             String similarity threshold for clustering (0-1) [default: 0.7]
--dry-run                    Compute and write proposals only; do NOT apply
--output PATH                 Write proposals JSON here (default: data/refine/proposed_merges.json)
--from-file PATH              Load proposals from a prior dry-run and apply them
--allow-cross-domain-merge    Allow merging same-named entities across domains (default:
                              skip + report). Only affects the clustering path — has no
                              effect combined with --from-file.
```
**Destructive on apply** (deletes alias nodes). Always dry-run first on a domain you
haven't refined before.

#### `artmind ingest normalize-time`
```
--domain TEXT   Domain to backfill canonical temporal properties for [required]
--dry-run       Compute counts only; do not write
--compact       Emit compact JSON
```
Additive/idempotent — safe to re-run.

#### `artmind ingest detect-conflicts`
```
--domain TEXT               Target domain(s), repeatable (1=intra-domain, 2+=cross-domain) [required]
--nameFilter TEXT             Restrict to entities whose name contains this
--simThreshold FLOAT           Min cosine similarity for a candidate pair [default: 0.75]
--maxPairs INTEGER             Hard cap on candidate pairs — bounds LLM cost directly:
                              this many pairs = this many LLM calls [default: 200]
--maxChunksPerSide INTEGER     Evidence chunks fetched per side [default: 2]
--model TEXT                   Adjudication LLM model (default: env)
--dry-run                     Compute proposals + optionally write --output; do NOT materialize
--output PATH                  Write proposals JSON here
--from-file PATH               Materialize proposals from a prior dry-run file
--compact                     Emit compact JSON
```
**Expensive**: `llm_calls == candidates` — with defaults, up to 200 LLM adjudication
calls per invocation. Observed real-world timing: ~200 candidates → ~65-75 minutes of
LLM time on this setup (`qwen3.6:35b-mlx`). `candidate_seconds` (ANN pairing) is cheap
(~12s even against the full corpus) — the cost is entirely in the LLM loop. Precondition:
run intra-domain `refine-graph` on each target domain first (a warning fires if you
haven't). **Non-destructive on apply** (MERGE-only) — but still run dry-run first to
review proposals before spending materialization writes.

#### `artmind query graph conflicts`
```
--domain TEXT                          Domain(s), repeatable [required]
--entityId TEXT                         Filter to conflicts touching this entity id, repeatable
--entityName TEXT                       Filter to conflicts touching an entity whose name contains this
--status [open|resolved|dismissed|all]  [default: open]
--compact                              Emit compact JSON
```
Read-only.

#### `artmind query domains-overview`
```
--compact   Emit compact JSON
```
Cheap, read-only. The recommended first call before routing any cross-domain question —
returns per-domain document/entity counts and top entity classes.

#### Existing query commands extended with cross-domain + temporal support
All of `query graph metadata`, `structural-metadata`, `entity-listing`, `pattern1`–
`pattern10`, `text2cypher`, `vector-text`, `entity-resolve` now accept **repeatable**
`--domain` (comma-splittable) instead of a single domain, and an optional `--asOf
<ISO-date>` filter.

### 2.2 Supersession commands (Phase T2)

#### `artmind ingest supersede`
```
--domain TEXT                     Domain of both documents [required]
--newer TEXT                      Newer document name [required]
--older TEXT                      Superseded document name [required]
--scope [document|section|clause] [default: document]
--effective TEXT                  ISO date the supersession takes effect
--compact                         Emit compact JSON
```
Manually asserts one document supersedes another. Raises a clear error if either name
doesn't resolve to exactly one Document node in the domain — including when a name
resolves to *more than one* (re-ingesting an edited file produces a second Document node
sharing the old one's name; this is detected and reported rather than silently picking
one arbitrarily).

#### `artmind ingest detect-supersession`
```
--domain TEXT   Domain to scan for explicit Supersession Notice sections [required]
--dry-run       Report matches without writing
--compact       Emit compact JSON
```
Scans document markdown for an explicit "Supersession Notice" section and auto-applies.
Matches the superseded version number against another document's already-lifted
`version` property — so this only finds a pairing if BOTH documents' markdown sources
are locatable in `data/documents/markdowns/` under their registered names (see §1.6 for
a real-corpus case where this didn't hold and the manual command was used instead).

#### `artmind query graph timeline`
```
--domain TEXT      Domain(s) [required]
--entityId TEXT     Entity id whose timeline to render [required]
--compact          Emit compact JSON
```
Renders an entity's events/state-changes/supersessions ordered by `event_at`/`valid_from`.

---

## 3. Worked Examples (from the live `banking-corpus` graph)

### 3.1 Merging & aliasing — three tiers, three lessons

**Large, correct merge — "Complaints Handling Policy" (banking_policy, `POLICY`), 83
aliases.** Traced to a *single* source document (COM-POL-006), split into ~52 chunks
across 4 doc_ids. Each chunk covers one clause (recording, response timeframes,
escalation tiers, audit) and extraction minted a fresh policy-shaped name per clause
(e.g. "Complaint Response Deadline," "Frontline Complaint Resolution Timeline"). Merging
these into one canonical POLICY node is correct — they're all the same governing
document. *Minor blur:* a few aliases (e.g. "CRM Complaint Logging," "Account Balance
Issues Policy") describe systems/sub-procedures the policy *references*, arguably
deserving their own linked entity rather than being folded in as an alias — a precision
nit, not a correctness error.

**Mixed merge, one real over-merge — "Multi-Factor Authentication" (banking_policy,
`CONTROL_MEASURE`), 18 aliases.** Core aliases ("MFA," "OTP," "MFA for VPN access") are
legitimately the same control in different contexts. But **"Biometric Authentication"**
was wrongly folded in — the source text explicitly lists it as an *alternative* factor
("Mobile App: Biometric + PIN" vs. "Online Banking: password + OTP"), not a synonym.
**Lesson: high alias counts should be spot-checked, not assumed correct** — the LLM
merge decision can conflate "related" with "the same."

**Large merge, working as intended — "Customer" (banking_sop_guides, `ROLE_ACTOR`), 14
aliases** including "You," "Your," "User," "Applicant," "Account Holder," "Claimant,"
even a placeholder name "James Wilson." Spans 11 documents, 176 chunks, all written in
second-person instructional SOP style. All of these really are the same generic
customer/counterparty role, addressed differently per sub-process (complaint handling,
KYC, direct debit). This is the intended behavior of refine-graph for role-type entities
— a KG that wants "who does this SOP step apply to" queries to work needs exactly this
kind of pronoun/role normalization.

**Structural takeaway:** a high alias count is, by itself, evidence the *extraction*
pass over-fragmented one concept into many nodes (one per document/chunk mention) — not
necessarily evidence the *merge* pass got it wrong. Always look at the actual alias list
and a couple of source chunks before trusting a large number either way.

### 3.2 Conflicts — real cross-domain findings (`banking_policy` vs `banking_sop_guides`)

29 `Conflict` nodes materialized from a 200-candidate dry-run. Two categories stood out:

**One root cause fanning out into ~14 pairwise conflicts:** `banking_policy` classifies
Mortgage Statement as a **Secondary** address-verification document; `banking_sop_guides`
classifies it as **Primary/Preferred**. Because both documents group several other
documents (Tenancy Agreement, Insurance Policy, Council Tax Document, HMRC
correspondence) into the *same* tier bucket, every cross-tier pair gets flagged
independently — really one underlying policy disagreement, not 14 separate ones. (This
is a known, accepted characteristic of the design — see §3.3's caveat on aspect-phrasing
and duplicate Conflict nodes.)

**Distinct, high-severity findings worth a human's attention:**
- Risk threshold: policy says medium-risk **>£100k**, SOP guide says **>£50k**.
- Anti-tipping-off vs. transparency: policy in places says "interview the customer
  within 24h" about a freeze/suspicious activity; SOP guide (correctly, per AML law)
  says disclosure is **prohibited** to avoid tipping off a suspect.
- Foreign ID acceptance: policy allows EU driving licenses/foreign passports for
  non-UK customers; SOP guide says non-UK documents are unacceptable (or need secondary
  verification).
- Breach notification: policy cites GDPR's "without undue delay"; SOP guide gives a
  concrete **30-day** notice window — the looser SOP deadline could put the bank in
  breach of GDPR if followed literally.

Example JSON shape (one conflict, abbreviated):
```json
{
  "conflict": {
    "id": "404d87f6...",
    "aspect": "Mortgage statement tier for address verification",
    "claim_a": "Classified as a Secondary document",
    "claim_b": "Classified as a Primary document",
    "severity": "medium",
    "status": "open",
    "domains": ["banking_policy", "banking_sop_guides"],
    "detected_at": "2026-07-05T..."
  },
  "entities": [
    {"name": "Insurance Policy", "domain": "banking_policy", "entity_class": "IDENTIFICATION_DOCUMENT"},
    {"name": "Tenancy Agreement", "domain": "banking_sop_guides", "entity_class": "IDENTIFICATION_DOCUMENT"}
  ],
  "evidence": [
    {"side": "a", "chunk_id": "...", "domain": "banking_policy", "text": "...Secondary (If Primary Unavailable): ... Tenancy agreement..."},
    {"side": "b", "chunk_id": "...", "domain": "banking_sop_guides", "text": "...Primary: ... Tenancy Agreement (signed)..."}
  ]
}
```

### 3.3 Known caveats worth remembering (from code review during implementation)

- **Aspect-phrasing drift → duplicate Conflict nodes.** `Conflict.id` is a hash of
  `(entity ids, slug(aspect))`. Because `aspect` is free-text LLM output, re-running
  detection with slightly different phrasing of the same dispute ("fee reversal
  approval limit" vs "fee reversal limit") produces a *new* Conflict node rather than
  updating the old one — re-detection is not a guaranteed no-op long-term, just within a
  single run's phrasing. Not fixed as of this writing; worth watching if you re-run
  detect-conflicts repeatedly over time on the same domain pair.
- **Evidence accumulation has no pruning.** `EVIDENCE` edges are added via `MERGE`
  (never duplicated for the same chunk), but if an entity's top-k evidence chunks change
  between runs (new documents ingested), old evidence edges are never removed — they
  just accumulate.
- **`--allow-cross-domain-merge` only affects the clustering path**, not `--from-file`
  applies (documented in the CLI help text as of this plan).
- **200 LLM calls/run is a real cost cliff**, not currently bounded by a
  cost/rate-limit guard beyond `--maxPairs`.

### 3.4 Cross-domain merge guard example

Confirmed live, at full scale: running `artmind ingest refine-graph --dry-run --output
all.json` with **no `--domain` filter** across all 7 domains (5,887 distinct entity
names, 280 clusters) proposed **160** merges total, of which **83 were correctly
skipped** as cross-domain and only **77** were kept for same-domain application. A
sample of the skipped clusters:

| Alias | Would-be canonical | Why this matters |
|---|---|---|
| `Mobile App` | `Mobile Banking App` | Same product name used differently in a technical-architecture domain vs. a customer-facing domain — merging would blend genuinely distinct documentation contexts. |
| `System Availability` (13+ variants: `System Unavailability`, `Branch Availability`, `Mobile App Availability Target`, `System Availability — SLA 99.8%`, `Screening System Availability Check`, ...) | `System Availability` | The single biggest fan-in cluster — SLA/uptime language recurs across risk-governance, operations, and product domains. Merging all of these across domains would erase which domain's specific SLA target (e.g. 99.5% vs 99.8%) applies where. |
| `Arranged Overdraft Interest Rate — 15.00% AER`, `Current Account Arranged Overdraft Rate — 15.00% AER`, `Overdraft Interest Rate — 15.00% AER` | `Arranged Overdraft Interest Rate` | Rate-table entries phrased differently in a product-reference domain vs. a policy domain — exactly the kind of pair `detect-conflicts` should compare, not silently merge. |
| `Financial Ombudsman Service (FOS)` | `Financial Ombudsman Service` | A near-identical name (just an abbreviation) that still spans two domains — the guard doesn't special-case "obviously the same," it holds back anything spanning >1 domain by design. |

This is the mechanism working exactly as intended: every one of these pairs is now
available, un-merged, for `detect-conflicts` to evaluate on its own merits (same entity
with consistent claims, genuinely conflicting claims, or unrelated) rather than being
silently collapsed by refine-graph before a conflict check ever got the chance to run.

### 3.5 Supersession example (real, verified against `banking-corpus`)

`policy_complaints_v3.md` (the current, in-force revision) and
`policy_complaints_20260702_142822.md` (an earlier revision of the same policy,
originally sourced from `banking_document_corpus/policies/policy_complaints.md`) are the
same document lineage. `policy_complaints_v3.md`'s markdown contains a genuine
"## Supersession Notice" section, verified to parse correctly:

> **This policy (Version 3.0, effective 2026-06-01) supersedes and replaces Version 2.0
> (effective 2026-01-15) in full.** The prior version's Escalation Matrix and
> Compensation Framework thresholds no longer apply as of the effective date above...

`detect-supersession`'s automated version-matching couldn't complete for this pair (the
older document's markdown source wasn't locatable under its registered Neo4j name — a
corpus data-layout gap, not a parsing bug), so the manual command was used instead:

```bash
uv run artmind ingest supersede --domain banking_policy \
  --newer "policy_complaints_v3.md" --older "policy_complaints_20260702_142822.md" \
  --effective 2026-06-01 --compact
```

Result, confirmed directly against the graph:

| Document | valid_from | valid_to | superseded_by |
|---|---|---|---|
| `policy_complaints_20260702_142822.md` (older) | — | `2026-06-01` | `<v3 doc id>` |
| `policy_complaints_v3.md` (newer) | `2026-06-01` | — | — |

Plus a `(newer)-[:SUPERSEDES {scope: "document", effective: "2026-06-01", detected_by:
"manual"}]->(older)` edge.

**`--asOf` currency verified end-to-end.** Both documents contain conflicting
approval-threshold text ("Management: Approve complaints >£500" in the older doc vs.
"Management: Approve complaints >£2,000" in v3.0). Querying
`vector-text --domain banking_policy --asOf 2026-07-04` for a related question returned
chunks **only** from `policy_complaints_v3.md` — the older document's chunks were
correctly excluded because their `valid_to` (2026-06-01) precedes the `asOf` date.
Without `--asOf`, both documents' chunks appear, exactly as the design intends: history
is still queryable, just not surfaced by default for present-tense questions.

**Non-destructiveness verified.** Applying this supersession did not touch any of the 29
pre-existing materialized `Conflict` nodes (count unchanged, 29 before and after) —
`apply_supersession()` only writes to `:Document`/`:DocChunk`, never `:Conflict`. And
since `detect-conflicts` only ever pairs entities across *different* domains, no
same-domain Conflict between these two same-lineage, same-domain documents could ever
have been created in the first place — the "supersession doesn't get mistaken for a
cross-domain conflict" property holds structurally here, not just by the `superseded`
verdict's routing logic.

**Skill-level check (Adjudicate step, real question):** asking "Who can approve a £700
fee reversal after a customer complaint?" via the `artmind-query` skill correctly
identified the older document's ">£500" claim as **superseded/historical**
(excluded it from the live answer), correctly surfaced the current in-force
`policy_complaints_v3.md` claim (">£2,000") alongside `escalation_matrix.md`'s Level 1
frontline cap ("<£500", `banking_sop_guides`) with both provenances, and — per the
Grounding Rule — honestly stated that the retrieved evidence didn't cover the specific
middle escalation tier for a £700 case, rather than inventing a specific approver name
not present in the data. This is a less crisp result than the plan's illustrative
"CEO >£500 vs Manager £1,000" example (that exact wording isn't in this real corpus),
but it demonstrates the mechanism's actual behavior faithfully: time-qualify claims,
never blend disagreeing sources, never fabricate past what's grounded.

---

## 4. Notes toward a future `artmind-refine-graph` skill

> **Now implemented** as the single `skills/artmind-refine/SKILL.md`: it drives
> the `ingest refine-pipeline` orchestrator (§2.1, including the cross-domain
> conflicts pass for multi-domain runs) and absorbs the targeted workflows
> sketched below (dedup review, focused merges, merge/conflict forensics,
> supersession-vs-conflict triage). An earlier `artmind-refine-graph` skill
> covering only the targeted workflows was folded into it. The notes below are
> kept as the original design rationale.

A skill built from this guide should probably encapsulate the following workflows as
distinct, guided procedures (not just a command reference):

1. **"Clean up a domain's duplicate entities"** — run refine-graph dry-run, surface the
   proposed merge list for human review (esp. flagging large/surprising alias clusters
   the way §3.1 did), only then apply via `--from-file`. Should teach the skill to
   spot-check a handful of merges against source chunks before recommending apply,
   the way this session's investigation did — don't just trust cluster size.
2. **"Find and report cross-domain conflicts between domain A and domain B"** — check
   `RefineRun` exists for both domains first (offer to run refine-graph if not), then run
   `detect-conflicts --dry-run`, warn the user about the expected LLM-call cost/time
   *before* running (this is the single most important UX point — a 200-candidate run
   can take over an hour), review proposals, then apply. After applying, use
   `query graph conflicts` to produce a human-readable summary grouped by root cause
   (the way §3.2 collapsed 14 pairwise conflicts into "one real disagreement"), not just
   a flat list.
3. **"Investigate a surprising merge or conflict"** — given an entity name or Conflict
   id, pull its full alias list / evidence chunks and render a judgment (reasonable /
   over-merged / needs human review), the way this session's two sub-investigations did.
4. **"Guard against destructive/expensive mistakes"** — the skill should always require
   `--dry-run` first for both refine-graph and detect-conflicts, and should never
   silently run `--from-file` apply without the user having reviewed the dry-run JSON.
5. **"Reconcile version history vs. real conflicts"** — teach it to run
   `detect-supersession` before `detect-conflicts` so that same-lineage document
   revisions don't get flagged as live disagreements, and to use `--asOf` to answer
   present-tense questions correctly.

This document (and the two sub-investigation reports it's drawn from) should be enough
raw material to draft that skill's `SKILL.md` without re-deriving any of the above from
scratch.
