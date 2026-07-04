# Temporality in the artmind Knowledge Graph

**Status:** Draft — pending review
**Date:** 2026-07-03
**Related:** `2026-07-03-cross-domain-query-and-conflict-detection-design.md` (sibling spec — conflicts interact with temporality; see §7)

## 1. Context

Two scenarios motivate adding time to the graph:

1. **Supersession.** Newer documents (revised policies, change notes) override entire documents, sections, or individual clauses of older ones. Today a 2024 policy and its 2026 revision would both sit in the graph as equally-current facts — indistinguishable from a genuine conflict.
2. **State over time.** Journaling-type domains (`personal_journal`, `fiction`, `project_governance`) record entities whose state changes across entries and events. Today those changes land as undifferentiated properties/edges with free-text date strings, unqueryable by time.

What exists today (verified):
- `ingest.py:1369-1373` — `Document.last_modified` (file mtime, an unreliable authorship proxy) and `Document.date` (only when front-matter supplies it). Registry `documents.added_at` = ingestion time.
- Schemas prompt for temporal properties as free-text extraction guidance (full review in §5), but nothing normalizes, types, indexes, or queries them.
- No supersession concept, no `valid_to` anywhere (nothing ever expires), no time filter in any query pattern or in text2cypher.

## 2. Core design decision: mechanics in the metastructure, semantics in the schemas

**Answer to "schemas or metastructure?": both, with a strict split.**

- **Metastructure (uniform, pipeline-enforced, domain-agnostic):** the two timelines, canonical time properties, range indexes, `--asOf` filtering, and `SUPERSEDES` edges. Schemas never define these; the pipeline stamps and normalizes them.
- **Schemas (domain-specific):** *what* dates mean in each domain, declared via a small `temporal:` mapping block (§6), plus domain modeling of state changes as reified event entities. Schemas keep prompting for domain-natural property names; they additionally declare which of those map onto the canonical timeline.

Rejected: schema-only (15 different property names across 13 schemas → no uniform querying, ever); metastructure-only (only the domain knows whether a date is a validity start, an event moment, or a deadline); full temporal property-graph versioning of every entity property (rabbit hole; reified per-domain events + canonical properties cover the real scenarios).

## 3. Data model

### Two timelines (bitemporal split)

| Timeline | Property | Meaning | Who writes it |
|---|---|---|---|
| Transaction time | `ingested_at` | when artmind learned it | pipeline, always, every node/rel |
| Valid time (interval) | `valid_from` / `valid_to` | when true in the world (policy in force, rate applicable) | normalization step, from schema `temporal:` mapping |
| Valid time (point) | `event_at` | when it happened (journal event, meeting, state change) | normalization step, from schema `temporal:` mapping |

All canonical values ISO-8601 strings (date or datetime); partial dates permitted (`2026`, `2026-07`) and compared lexically — ISO-8601 sorts correctly as text. Original domain-named properties are kept untouched; canonical properties are *additive copies* (same non-destructive principle as the sibling spec's Conflict model).

### Supersession (scenario 1)

```cypher
(:Document)-[:SUPERSEDES {scope: 'document'|'section'|'clause',
                          effective, evidence_chunk_id, detected_by}]->(:Document)
(:DocChunk)-[:SUPERSEDES {scope, effective, ...}]->(:DocChunk)   // clause/section-level, e.g. change notes
```

Applying a supersession also sets `valid_to = effective` on the superseded side (Document, its chunks for document scope; the target chunk for clause scope). That single write is what makes `--asOf` queries exclude stale content automatically. Sources of SUPERSEDES edges: (a) explicit statements in documents ("this policy replaces v2.1", change-note references) detected at extraction/refine time; (b) the conflict adjudicator's `superseded` verdict (§7); (c) manual assertion via a CLI command.

### State over time (scenario 2)

Schema-level, not metastructure: journaling-type schemas reify changes as event entities (`EVENT`, `STATE_CHANGE` classes) carrying `event_at`, e.g. `(:Entity)<-[:STATE_OF]-(:StateChange {event_at, from_state, to_state})`. Precedent already in the corpus: `fiction_schema.yaml:287` instructs "if a relationship changes over time, use multiple edges with different [properties]" — the convention exists; what's missing is the canonical `event_at` property that makes those edges time-queryable. Relationship-level: extracted rels may carry `valid_from`/`valid_to`/`event_at` too (the normalization step maps rel properties the same way).

### Time indexing

Neo4j **range indexes** on the canonical properties — that is all "time indexing" needs to be:

```
entity_valid_from / entity_valid_to / entity_event_at   ON :Entity
chunk_valid_to, document_valid_from / document_valid_to ON :DocChunk / :Document
```

(added in `artmind/setup.py` alongside the existing domain indexes).

## 4. Query surface

- All `query graph` patterns, `vector-text`, and `entity-resolve` gain an optional `--asOf DATE`. Filter, centralized in one builder next to `domain_predicate()` (sibling spec):
  `(x.valid_from IS NULL OR x.valid_from <= $asOf) AND (x.valid_to IS NULL OR x.valid_to > $asOf)` — nodes with no valid-time are always visible (untimed knowledge never disappears).
- Default remains no filter (full history). `--asOf today` is the "current truth" view; the artmind-query skill should use it for present-tense questions ("who *can* approve…") and drop it for historical ones ("what *was* the limit in 2024?").
- `pattern10` (doc chunks) and `metadata` outputs include `valid_from`/`valid_to`/`superseded_by` so an agent can see document currency at Discover time.
- text2cypher prompt gains the `--asOf` rule and `SUPERSEDES` in the structural schema.
- New: `artmind query graph timeline --domain D --entityId ID [--compact]` — an entity's events/state changes/supersessions ordered by `event_at`/`valid_from` (the scenario-2 read path).

## 5. Review of current schemas for temporality

Survey of all 13 schemas in `domains/schemas/` (2026-07-03). Temporal guidance exists in 11 of 13 — but as ~15 differently-named free-text properties, no format guidance, no point-vs-interval distinction, and no `valid_to`-like concept anywhere.

| Schema | Temporal guidance today | Timeline it implies | Gap |
|---|---|---|---|
| banking_policy | `effective_date`, `review_date` (POLICY); `effective_date` (REGULATION) | valid-time interval start | no end/supersession; format unspecified |
| banking_reference | RATE_ENTRY is explicitly date-stamped; `effective_date` ("when this rate applies from"); names embed dates ("… effective 2026-01-15") | valid-time — strongest schema; rate schedules are classic slowly-changing data | date trapped in name/description strings; no `valid_to` when a newer rate lands |
| banking_risk_governance | REGULATORY_UPDATE class (circulars, regulatory deadlines) | event + deadline time | deadlines not extracted as a named date property |
| banking_sop_guides | response times, `timeout_check` types | durations/SLAs only | **no document validity at all — yet SOPs/escalation matrices are exactly the versioned docs in the fee-reversal conflict** |
| banking_products | — | — | none; products have launch/withdrawal/rate-change dates in reality |
| banking_organization | `meeting_frequency`, `frequency` | recurrence only | no point/interval dates |
| banking_communications | `date_field` template variable, "send time" | none (mentions of dates as data) | template versioning uncaptured |
| personal_journal | `date_or_time` (EVENT), `timeframe` ("today, this week, vague future"), `duration`, `frequency` | event-time rich | all free-text; **relative dates never anchored to the entry's own date**; entry date itself not captured as document metadata |
| project_governance | `timeline (start_date, end_date)`, `target_date or relative_timing ("t0 + 2 weeks")`, `due_date`, `date_raised` | mixed point + interval, richest variety | relative timing ("t0 + 2 weeks") unresolvable without an anchor date |
| fiction | "date and/or time" (EVENT); §287: relationship changes over time → multiple edges with different properties | event-time; state-change convention already present | the multi-edge convention has no canonical time property, so the edges aren't orderable |
| sales_collateral | EVENT class incl. `deadline` types; `date_or_period` | event/deadline | free-text |
| general | `date_or_period`, `time_period`, "date or recency" | vague | catch-all, weakest |
| technical_paper | none | — | publication date not even prompted |

Cross-cutting findings:
1. **Naming chaos**: `effective_date`, `date_or_time`, `date_or_period`, `target_date`, `due_date`, `date_raised`, `start_date`/`end_date`, `timeframe`, `time_period` — uniform time querying is impossible without normalization.
2. **No interval ends**: nothing ever expires; `valid_to` does not exist in any schema. Supersession therefore cannot be represented even manually.
3. **Relative dates**: journal ("today") and governance ("t0 + 2 weeks") schemas invite values that are meaningless without anchoring to the document's own date at normalization time.
4. **Document-level dates are the biggest hole**: only front-matter `date` is captured; the corpus's policy/SOP headers ("Effective Date:", "Version:", "Last Updated:") are never lifted onto the `Document` node.
5. **Existing precedent to build on**: fiction's multi-edge state-change rule and banking_reference's date-stamped RATE_ENTRY show the schemas already *want* temporality — they lack only the canonical target to map into.

## 6. Schema changes: the `temporal:` block

Optional new block per schema; absent block = no valid-time mapping (transaction time still stamped):

```yaml
temporal:
  document:                       # lifted onto the Document node
    valid_from: [Effective Date, effective_date]     # header labels / front-matter keys to look for
    version: [Version]
  entities:
    POLICY:        { valid_from: effective_date }
    RATE_ENTRY:    { valid_from: effective_date }
    EVENT:         { event_at: date_or_time }
    MILESTONE:     { event_at: target_date }
  relative_anchor: document.valid_from   # what "today"/"t0" resolve against (default: Document.date)
```

- Backfill the block for: banking_policy, banking_reference, banking_sop_guides (add an `effective_date`/`version` prompt first — closing its gap), personal_journal, project_governance, fiction, sales_collateral. Others as needed.
- `artmind-create-schema` skill: add a step that asks about the domain's temporal semantics and emits the `temporal:` block; `domains harmonize` propagates parent `temporal:` blocks to children.

## 7. Normalization stage (where canonical properties get written)

New pipeline stage `artmind ingest normalize-time --domain D [--dry-run]`, also runnable standalone as backfill over existing graphs:

1. **Document dates**: parse header/front-matter per `temporal.document` mapping ("Effective Date: 15 March 2026", "Version: 3.2") → `Document.valid_from`, `Document.version`. Fall back to front-matter `date`; `last_modified` is a last resort, flagged low-confidence (`time_source: 'mtime'`).
2. **Entity/rel dates**: copy schema-mapped domain properties → canonical `valid_from`/`valid_to`/`event_at`, parsing to ISO-8601. Deterministic parsing (`dateutil`-style) first; **LLM only for leftovers** — vague/relative strings ("early spring", "t0 + 2 weeks"), resolved against `relative_anchor`, batched, bounded (same cost discipline as `detect-conflicts` in the sibling spec).
3. **Provenance**: every canonical write records `time_source: 'header'|'property'|'llm'|'mtime'` — grounded answers can qualify low-confidence dates.
4. Non-destructive throughout: original properties untouched; re-runs idempotent (recompute + overwrite canonical props only).

## 8. Interaction with the conflicts spec (sibling doc)

Temporality is the conflict model's most important **resolver**:

1. **New adjudicator verdict `superseded`**: `llm_adjudicate()` receives both documents' `valid_from`/version in the evidence; when one side is a newer revision of the same authority, verdict = `superseded` (not `conflicting_claims`) → materialize `SUPERSEDES` + set `valid_to`, instead of an open Conflict. Without this, `detect-conflicts` fills with false positives that are really version history.
2. **Conflict resolution reason**: `Conflict.status: resolved` gains `resolution: 'superseded' | 'precedence' | 'manual'` — supersession becomes the first automatic resolution path.
3. **Sharper conflict definition**: two claims conflict only if their valid-time intervals **overlap** and neither supersedes the other. The fee-reversal case remains a true conflict precisely because both documents are currently in force.
4. **Answer format**: the skill's Adjudicate step becomes time-qualified — "as of <date>, source A says X…"; superseded claims are reported as history, not disagreement.

## 9. Phasing (relative to the sibling spec's phases)

- **Phase T1 (independent of cross-domain Phase 1):** canonical properties + range indexes (`setup.py`), `normalize-time` stage + document-date lifting, `temporal:` block in 5–7 schemas, `--asOf` on query commands + skill guidance. No LLM cost except bounded relative-date parsing.
- **Phase T2 (lands with or just after sibling Phase 2 / `detect-conflicts`):** `SUPERSEDES` model + explicit-supersession detection, `superseded` adjudicator verdict + `resolution` field, `query graph timeline`, text2cypher schema additions.
- **Phase T3:** state-change reification guidance in journaling-type schemas (`STATE_CHANGE` classes), `artmind-create-schema` temporal step, harmonizer propagation.

## 10. Verification

1. **Document lifting**: ingest a banking policy with "Effective Date:" header → `Document.valid_from` set with `time_source:'header'`; a dateless doc falls back to mtime flagged low-confidence.
2. **`--asOf` regression**: no `--asOf` → identical results to today (all tests unchanged); `--asOf` on a domain with no temporal data → also identical (NULL-safe filter).
3. **Supersession acceptance**: ingest a v2 of `policy_complaints.md` with revised thresholds → explicit-supersession detection (or adjudicator verdict) creates `SUPERSEDES`, sets `valid_to` on v1; the fee-reversal question `--asOf today` retrieves only v2 thresholds; without `--asOf`, both appear with v1 marked superseded — and **no open Conflict is created between v1 and v2**, while the genuine cross-domain conflict (policy vs escalation matrix, both in force) stays open.
4. **Journaling**: two journal entries where an entity's state changes → `query graph timeline` returns ordered state changes with resolved absolute `event_at` (relative "today" anchored to each entry's date).
5. **Cost check**: `normalize-time` reports deterministic-vs-LLM parse counts; LLM calls bounded and batched.
