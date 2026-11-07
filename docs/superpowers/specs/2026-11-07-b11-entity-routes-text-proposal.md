# B11 (proposal): entity routes cite only the entity's own documents

Date: 2026-11-07. Status: **proposed, to be analysed before any design or build.** Found by B2's
document-reach measurement (`2026-11-07-b2-search-and-scope-design.md` §8.3, §12). Evidence is from
banking_corpus, a public sample corpus, so its questions and file names appear here. brain01 and
nbs_audit_2026 have no entity-route labels, so they add no evidence either way.

## The observation

`entity_profile` runs one `entity-context` step; `entity_history` runs one `entity-history` step
(`artmind/query_plan/routes.yaml`). Both return what the graph holds about the resolved entity, with the
documents that entity was extracted from. A question that names an entity often asks about something
beside it, which sits in other documents. On B2's final build (`plan replay --reach`, decision model):

| Label | Question (banking `questions.md`) | Expected documents | Plan | Reached |
|---|---|---|---|---|
| `L004` (Q04) | What are the key features of the SmartSaver account, and how does its interest rate vary by balance? | `products/smartsaver_terms_conditions.md`, `reference/interest_rate_schedule_2026.md` | `entity_profile`: `entity-context` over four children | 0 of 2 |
| `L012` (Q12) | What was FirstUK's status for enhanced KYC for high-risk customers before 1 March 2026, and what did the Board Risk Committee report later in Q1? | `regulations/regulatory_circulars_2026.md`, `governance/board_risk_committee_minutes_q1_2026.md` | `entity_history`: `entity-history` on the circulars' REGULATORY_UPDATE entity | 0 of 2 measured (1 really, see below) |

Lexical plans show the same where they choose `entity_profile`: `L003` reaches 1 of 2 and `L021` 1 of 7.
Where a plan for the same question was a `search` instead, it reached them all: decision `L003` 2 of 2,
decision `L021` 7 of 7, lexical `L012` 2 of 2.

## What the first look found (not yet the analysis)

1. **Part of L012 is measurement.** `entity-history` rows carry the source document as `doc_id` (a UUID).
   `--reach` matches by path, file name or title; for a dict with a `doc_id`, `reach.step_documents` takes
   the dict's own `id` (here the observation's), so it cannot see that the step did reach
   `regulations/regulatory_circulars_2026.md`. L012 truly misses one document (the committee minutes),
   not two.
2. **Part of L004 is entity identity.** The graph holds about 12 entities named "SmartSaver" or "SmartSaver
   Account" across 6 domains (products, reference, communications, organization, sop_guides,
   risk_governance). The plan resolved one, `SmartSaver` (PRODUCT), whose only source is `website_faq.md`.
   The `banking.products` and `banking.reference` entities cite the two expected documents. Twins are
   unioned only for same-class, same-name entities (`slots.twins_for`), and "SmartSaver" vs "SmartSaver
   Account" is neither.
3. **The rest is the question's second half.** The committee minutes (L012) and the rate schedule (L004)
   answer the part of the question that is not about the entity.

## Candidate changes (to compare, not chosen)

| Candidate | What it does | Cost / risk |
|---|---|---|
| A. A text step beside the entity step | `entity_profile` and `entity_history` gain a `vector-text` (or brief `survey`) step over the plan's `resolved` set or the family root, merged with the entity rows, as `entity_visits` already does with its text check | a second command on every entity plan; for "who is X" questions the text step adds noise |
| B. A text step only when the question has a second part | a plan fact (a conjunction after the entity mention, a second question clause, or a content word outside the entity's own name) selects A's program; otherwise one step | needs a reliable cue; replay must show it does not fire on plain profile questions |
| C. Split the question (P3) | the executor plans the second clause as its own sub-question | waits for P3; most general |
| D. Fix identity first | widen twins to name variants across classes and children (or rely on same-as curation), so the resolved entity carries all its documents | addresses L004 only; touches resolution for every route |
| E. Measurement | `--reach` resolves a step's `doc_id` to the document's path (one read query per run) | needed in any case to measure A–D |

## To analyse before choosing

- Size: how many benchmark and shadow-plan questions are entity routes with a second part. Banking has 3
  entity-route labels; round 9 shadow data may add more.
- Whether B's cue can be written so that replay stays at no label newly wrong, and route parity unchanged.
- Latency: one more command per entity plan, against the P3a gate (≤ 3 s for one to four steps).
- How much of L004's miss D alone removes, and whether that belongs to curation (`artmind-curate`) rather
  than the planner.
- E first, so that the numbers above are measured, not estimated.

Not in scope: changing `entity-context` or `entity-history` themselves.
