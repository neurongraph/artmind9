# Phase 1 migration — for review

The LLM-assisted first pass of the 16-schema × 103-class migration (see
[redesign-phase-plan.md](./redesign-phase-plan.md) Phase 1). Mechanism landed
and is covered by tests; **this document is the human-review step** the phase
plan calls for separately from the mechanism work.

## What changed, mechanically

- `artmind/domains/meta.yaml` — new package asset: the meta-schema contract
  (`reserved_prefix`, `kinds`, `default_persona`) plus the shared prompt
  boilerplate (banners, output format, universal extraction rules, checklist)
  as templates with `{{DOUBLE_BRACE}}` placeholders.
- `entity_types` is now a map everywhere: `CLASS: {kind, description,
  type_examples, properties, relates_to, guidance}`. The old list form is
  rejected by the validator.
- `entities_prompt`/`properties_prompt`/`relationships_prompt` literal fields
  are gone from every schema file — `artmind/prompt_builder.py` assembles them
  at runtime from `meta.yaml` + `entity_types`.
- `temporal.entities` folded into each property's own `temporal:` key (e.g.
  `effective_date: {temporal: valid_from}`). `temporal.document`/`defaults`/
  `relative_anchor` are untouched.
- `artmind/harmonizer.py` is a dict merge now (copies a missing class's whole
  declaration from parent to child), not regex block surgery.
- `artmind/schema_reference.py` reads `entity_types` directly — no more regex
  parsing of prose back into structure.
- New: `artmind domains validate`, run automatically by `artmind init`.

## Simplifications made along the way (accepted, not flagged for review)

- Per-class relationship prompt lines are now `- A → B: type1, type2` (one
  line, no separate header) with an explicit "never a class name" rule —
  fixes the header-leak bug (21% of edges mistyped) by construction.
- The canonical-name RIGHT/WRONG example and id-abbreviation examples in
  OUTPUT FORMAT are now one generic illustration shared by every domain,
  rather than a domain-specific one per schema.
- Rich per-domain quality checklists (e.g. banking.products' 9-item
  checklist) and per-class canonical-naming worked examples collapsed to one
  universal checklist + one generic naming example. **This was not safe as
  first done** — see the exit gate below, which caught a real entity-collapse
  regression from it. Fixed by mechanically routing every dropped bullet and
  naming example back into `guidance` (per-class or schema-level) rather than
  discarding the substantive ones; only a denylist of genuinely generic,
  already-covered reminders ("Bidirectional flag is correct?", "No
  hallucinated properties?", etc.) was actually dropped.
- ~45 property bullets that were prose fragments rather than atomic names
  (e.g. `"term_end_date or termination_conditions"`, `"archetype if clear"`)
  were split into a clean snake_case key + a `hint` carrying the qualifier.
  Four (all on fiction's CONCEPT) were pure conditional guidance with no
  atomic name at all and were moved into CONCEPT's `guidance` field instead.
- One genuine bug fixed in passing: `banking.products` CARD→FEE's
  `incurs_fee_for (replacement, foreign transaction)` had been split into two
  bogus rel_types by the old parser's naive comma-split; restored as one.

## `kind` assignment — please review

This is the one judgment call worth a second pair of eyes: getting it wrong
changes whether disagreeing observations become `_temporal_props` (recurrent)
or a `:Conflict` (occurrent) once Phase 3 lands. 91 of 103 classes came out
`recurrent`, 12 `occurrent`. The `occurrent` calls:

| Class | Domain(s) | Why |
|---|---|---|
| `TRANSACTION` | banking (all) | a specific deposit/withdrawal/transfer — fixed once recorded |
| `SAR` | banking (all) | a filed report |
| `INCIDENT_EVENT` | banking.cases | "a discrete, timestamped step" |
| `IMPACT_ASSESSMENT` | banking.cases | a stated finding, attributed to a team, at a point in time |
| `RETENTION_DECISION` | banking.cases | a specific ruling made once |
| `AUDIT_FINDING` | banking.risk_governance | a filed finding (canonical occurrent example) |
| `GOVERNANCE_DECISION` | banking.risk_governance | a decision made at a point in time |
| `REGULATORY_UPDATE` | banking.risk_governance | a specific bulletin/regulation issued |
| `EVENT` | fiction, general, personal_journal, sales_collateral | a happening, not a persisting thing |
| `CLAIM` | general | a specific stated assertion |
| `STATE_CHANGE` | fiction, personal_journal, project_governance | *is* a transition record by definition |
| `DECISION` | project_governance | a decision made at a point in time |
| `FINDING` | technical_paper | a specific empirical result |

Two borderline calls made by heuristic (worth a deliberate look rather than
trusting the default):

- **Lifecycle-status entities kept `recurrent`** — `CASE`, `CUSTOMER_COMPLAINT`,
  `FRAUD_ALERT`, `REMEDIATION_ACTION`, `ACTION_ITEM`, `ISSUE`, `DELIVERABLE`,
  `MILESTONE` (contracts + project_governance). Reasoning: their defining
  property is a status that legitimately progresses (open → investigating →
  closed) across documents describing the same tracked item over time — that's
  temporal variation, not conflict. `SAR`/`AUDIT_FINDING`/`GOVERNANCE_DECISION`
  went the other way despite superficially similar shapes, on the theory that
  the report/decision itself is fixed once made even if a *separate* tracked
  item (a case, an action item) records what happened to it afterward.
- **`METRIC`** (general, technical_paper), **`RATE_ENTRY`** (banking.reference),
  **`RISK_METRIC`**/**`METRIC_TARGET`** (banking) kept `recurrent` — treated as
  "the same named measurement, value changes over time," matching
  `INTEREST_RATE_TIER`'s pattern, rather than "this specific number was
  reported once."

## Exit gate

Re-extraction diff against the pre-redesign prompts — same real chunk (the
rate-tier table from `interest_rate_schedule_2026`, domain `banking.reference`),
same local model (`qwen3.6:35b-mlx`), both run just now, no Neo4j/registry
involved (direct calls to `extraction.build_*_prompt` + `extract_with_retry`).

**First run surfaced a real regression**, which is exactly what this gate is
for: collapsing per-domain quality checklists and canonical-naming worked
examples into one universal boilerplate (a simplification I'd made without
flagging it above) had silently dropped the instructions that stop three
distinct rate tiers from being collapsed into one `RATE_ENTRY` with
array-valued properties (`rate_value: [4.5, 4.7, 4.8]`) — precisely the
"scalars are not unioned" anti-pattern `projection-pipeline.md` calls out as
the whole reason properties merge by shape. Root cause: two class-specific
instructions never made it into any `guidance` field —
`RATE_ENTRY names include product, tier, rate value, and effective date`
(a checklist bullet) and its `RIGHT: {...tier-specific name...}` naming
example. Fixed by mechanically routing every dropped checklist bullet and
naming example across **all 16 schemas** (not just the one caught here) into
`guidance` — per-class where a bullet names exactly one class, schema-level
where it names several, dropped only where a fixed denylist of purely
structural reminders (already covered by the universal checklist) matched.

**Second run, after the fix:**

| | OLD (pre-redesign) | NEW (fixed) |
|---|---|---|
| Entities | 4 (1 PRODUCT + 3 RATE_ENTRY, one per tier) | 4 (1 PRODUCT + 3 RATE_ENTRY, one per tier) |
| `rate_value` shape | scalar per entity (4.5 / 4.7 / 4.8) | scalar per entity (4.5 / 4.7 / 4.8) — no regression |
| Relationships | 5 (`applies_to` ×3, `higher_tier_than` ×2) | 9 (`applies_to` ×3, `higher_tier_than` ×2, `adjacent_to` ×2, `applies_same_basis_as` ×2) |
| Class-name-typed (leaked) relationships | 0 | 0 |

The fixed run matches OLD's entity/property structure exactly and finds
*more* of the declared relationship types (the fuller `relates_to` pair
declaration surfaces tier-adjacency chains OLD's prose happened not to
elicit this run). Property-key hygiene holds — no new non-atomic keys
introduced. Relationship-type leakage is at 0 in both runs for this chunk;
the structural fix (one line per pair, explicit "never a class name" rule)
should matter most on the classes that produced the original 21% leakage
(scorecard row 4), which a single-chunk spot check can't measure directly —
that needs the full benchmark re-run at Phase 8 cutover.
