# B3, B4, B5: instructions, decision-model confidence, time questions, superlatives

Status: **proposed, 2026-11-07** (needs the user's approval before build). Branch `integration/p2b`.
Findings: banking routing findings B3, B4, B5 (`<banking vault>/benchmarking/routing_findings_20261106.md`);
handover 15, "Next steps" 2–4. B6 is done (B2 S3); B7, B8, B9 are built (no routing design change:
B7 generic lexical triggers, B8 `db` spans, B9 `views`/`insights` on `domains-overview` rows). B11 is
deferred until after P3.

Privacy: brain01 and nbs_audit_2026 appear by label ids and counts only. Banking examples are quoted
(public corpus).

## 0. In one page

| Item | Change | Why (evidence) | Expected effect |
|---|---|---|---|
| B3a | an **instruction cue** (write it into a file, save as a topic, change a parameter, add a column, "don't do anything", "just a test") caps a lookup plan below the threshold | nbs: 5 non-retrieval labels and 2 `expect: low` retrieval labels are planned as confident lookups by the lexical classifier | nbs lexical wrong 11 → 5; nothing moves in banking or brain01 |
| B3b | a **non-retrieval intent the decision model is unsure of** (p < threshold) is planned as a *low* lookup, not as a high non-retrieval plan nor a low one with no steps | nbs `L020`, `L025` (retrieval labels the model calls `update`/`ops` at p ≈ 0.6) are wrong today; brain01 `L035` (`follow_up_format` at p 0.35) flips | nbs decision wrong 9 → 5 with B3a; brain01 decision wrong 1 → 0 (one level flip, `L035`, may remain) |
| B3c | decision plans need a **margin over the threshold** (`DECISION_MARGIN` = 0.05) | decision scores move ±0.03 between identical calls (p90 of per-label sd 0.033); `L022` (0.63–0.70) and `L028` (0.71–0.74) sit on the threshold; banking `L022` flips | banking decision wrong 4 → 2, flips 1 → 0; brain01 `L004`, `L025` move to abstain |
| B4 | `time_history` without a named document **resolves the document family by topic**: catalogue documents whose title words match the question and that have versions (a `SUPERSEDES` link, a sibling with the same stem and a version or date suffix, or history copies), then runs the history program over that family | banking `L007`, `L009`, `L010`, `L011` reach 0 or 1 of their expected documents: the plan names no `{document}`, so it falls back to the text search, which misses the superseded versions | banking reach +3 to +5 (time labels reach 6 of 11 today) |
| B5 | `superlative` is guarded by `needs: insights_snapshot` (skip `insights list` when the domain has no snapshot); a superlative over **records the structured store holds** routes to `structured` | `insights list` exits 1 in banking (`L022`, `L035`: 0 of 6 reached); "largest compensation amount", "reviewed the most cases" are table questions | banking reach +2 to +4; no exit-1 steps in plans |

The two remaining banking decision wrongs (`L015`, `L023`: `search` planned high, labelled low) are a
labelling question, not a planner one (§1.4). The gate in banking stays failed until the user decides them.

## 1. B3: instructions, unsure intents, and the decision model's margin

### 1.1 What the replay shows (baseline at `e4cd9ca`, `--repeat 4`)

| Vault | Classifier | c / a / ca / w | wrong labels by cause |
|---|---|---|---|
| nbs_audit_2026 | lexical | 16 / 0 / 0 / 11 | 5 confident lookups for non-lookup messages; 6 `expect: low` planned high (chunks-only `search` at confidence 1.0) |
| nbs_audit_2026 | decision | 15 / 0 / 3 / 9 | 2 retrieval labels planned as non-retrieval (`update`, `ops`); 6 overconfident; 1 confident lookup |
| banking_corpus | decision | 0 / 8 / 22 / 4 | `L022` superlative 0.63–0.70, `L028` aggregate 0.71–0.74 (on the threshold); `L015`, `L023` search ≥ 0.83 |
| brain01 | decision | 21 / 6 / 6 / 1 | `L035` intent flips (`follow_up_format` p 0.35 vs `retrieve`) |

Four repeats of every label through the decision model (`~/artmind-private/b3/stages.*`) put the noise at:
median per-label sd of the plan score 0.006, p90 0.033, max 0.236 (an intent flip). Labels whose score straddles
0.70 flip between `high` and `low` from one call to the next.

### 1.2 B3a: an instruction to the assistant caps the plan

The agent decides whether a turn needs a lookup (decision 1 of the handover). The planner's only duty is not
to plan a confident lookup for a turn that needs none. The cheapest reliable signal is in the words: a request to
**do** something with an answer, a file, a setting or the conversation, rather than to find something.

`intents.yaml` gains a list `instructions:` of cues (same trigger syntax). Proposed, from the five nbs cases
and invented paraphrases (no nbs text):

```yaml
instructions:
  - into a file
  - to a file
  - as a file
  - save this
  - save that
  - save it
  - as a topic
  - write the table
  - write it to
  - the parameter
  - the setting
  - increase the
  - decrease the
  - don't do anything
  - do not do anything
  - just a test
  - add a column
  - add a description column
  - remove the column
  - create a table
```

Rule (`intents.decide_intent` → `IntentResult.instruction: tuple[str, ...]`): when the message carries an
instruction cue and the plan is a lookup, the `intent` stage is capped at `INSTRUCTION_CAP` = 0.5 (below every
threshold). The plan keeps its route and steps as `suggested_steps`, so the agent still has them; `intent.instruction`
lists the cues. A matched cue does **not** change the intent: "add a description column … based on whatever you
find in each document" (nbs `L003`) is a lookup *and* an instruction; the label says `expect: low`, which is what
the cap gives.

Measured counterfactually on the recorded plans: nbs lexical 16/0/0/11 → 16/0/6/5 (the five non-lookup labels and
`L002`, `L003` become correct abstains; nothing correct is lost); nbs decision unchanged by B3a alone; no cue fires
on any banking or brain01 label. Remaining nbs lexical wrongs (`L005`, `L017`, `L022`, `L024`, `L027`) are
chunks-only `search` plans at confidence 1.0 labelled `expect: low` (and `L027`, a bare "go ahead" with no
previous turn). See §1.5.

### 1.3 B3b: an unsure non-retrieval intent plans a low lookup

Today a non-retrieval intent below the threshold produces a plan with **no steps** (`program: []`) at low
confidence. If the intent is wrong (a retrieval question the model misread as `update` or `ops`), the plan is wrong
on every repeat and gives the agent nothing. Change: when the intent is not `retrieve`/`follow_up_why` and its
confidence is below the threshold, plan it as `retrieve` (routes, slots, steps) with the intent stage carried at its
low confidence, and record `intent.unsure: {id, p}`. A confident non-retrieval intent keeps today's behaviour.

Effect: nbs decision 15/0/3/9 → 14/2/6/5 with B3a+B3b+B3c (`L020`, `L025` become abstains instead of wrong; the
non-lookup labels stay safe because a low lookup is harmless by the grading rule); brain01 decision wrong 1 → 0
(`L035`'s `follow_up_format` at p 0.35 becomes a low lookup instead of a wrong non-retrieval plan; its level may still flip between repeats where the model says `retrieve` with high confidence).

### 1.4 B3c: decision plans need a margin over the threshold

A decision-model plan is `high` only when its score is at least `threshold + DECISION_MARGIN`, with
`DECISION_MARGIN` = 0.05, a named constant in `planner.py`, overridable per domain in `domains/plan/<domain>.yaml`
(`decision_margin:`). Lexical plans are deterministic and keep the bare threshold. The payload records
`confidence.margin`.

Why a margin and not a higher threshold: the noise is a property of the backend, not of the domain; lexical plans
in brain01 cluster at 0.70–0.83 and are correct, so raising the threshold would cost them. Sweep on the recorded
decision plans (worst case over four repeats; c / a / ca / w):

| Margin | banking | nbs | brain01 | level flips (b / n / b01) |
|---|---|---|---|---|
| 0 (today) | 0 / 8 / 22 / 4 | 14 / 2 / 6 / 5 | 15 / 12 / 6 / 1 | 1 / 0 / 2 |
| **0.05** | **0 / 8 / 24 / 2** | **14 / 2 / 6 / 5** | **15 / 13 / 6 / 0** | **0 / 0 / 1** |
| 0.10 | 0 / 8 / 24 / 2 | 14 / 2 / 6 / 5 | 11 / 17 / 6 / 0 | 0 / 0 / 5 |
| 0.15 | 0 / 8 / 25 / 1 | 14 / 2 / 6 / 5 | 11 / 17 / 6 / 0 | 0 / 0 / 1 |

(Counterfactual over the four recorded decision runs, worst case per label; nbs and brain01 columns include B3a+B3b.
The one brain01 flip left at 0.05 is `L035`, whose intent the model calls `follow_up_format` in some calls and
`retrieve` in others: B3b makes both a lookup, but one is low and one high. The simulator reproduces lexical
banking and nbs exactly and is approximate for brain01 lexical; the real replay decides.) 0.05 is the smallest margin that removes the banking flips; larger
margins cost brain01 correct plans or bring new straddlers. It also clears banking `L028` (`aggregate` 0.71–0.74,
labelled `entity_profile`, `expect: low`) and `L022` (`superlative`, B5 also reroutes it).

**Not solved by B3, a question for the user:** banking `L015` and `L023` are `search` plans at 0.83–0.97 where the
labels say `expect: low` with route `search` (`L023`'s note: "I think search will work here … how do we know that
before we execute the query?"). The plan's route and domains match the label; only the confidence differs. Since
B2, `search` runs the needle and the family survey side by side, so the plan is what the agent should run. Two ways
out: relabel them `expect: high` (the plan is right and obvious), or keep `low` and accept that the gate counts them
as overconfident. A rule that would lower them (e.g. multi-part questions, `asks ≥ 2`) also lowers nbs `L016`, a
correct high plan, and leaves the two nbs chunks-only `expect: low` searches untouched, so it is not proposed.

### 1.5 What B3 does not change

- nbs chunks-only `search` labels marked `expect: low` (`L005`, `L017`, `L022`, `L024`): the plan is the only plan
  a chunks-only vault has (needle + survey over both domains) and its route and domains match. Same question as
  §1.4: relabel or accept.
- nbs `L027` ("go ahead" with no previous turn): replay passes a previous turn only when a label `follows:` one or
  comes from a trace in the same session. Fix in the label (`follows: L026`), not the planner.
- The decision model is not replaced or retrained; no LLM classifier is added (decision 6).

## 2. B4: time questions resolve the document family by topic

### 2.1 Evidence

Banking time labels (`time_history`, all `expect: low`): `L007`–`L011`, `L025`, `L027`, 11 expected documents.
Today none names a document, so `time_history`'s `{document}` slot is unfilled and the plan falls back to the text
search (needle + survey), which reaches 6 of 11. The misses are the documents a later version superseded or that
only a dated schedule holds:

| Label | Question (public) | Expected | Reached today |
|---|---|---|---|
| L007 | On 15 May 2026, who could approve £300 compensation for a complaint, and why? | `policy_complaints.md` (superseded by `policy_complaints_v3.md` on 2026-06-01) | 0 |
| L009 | How did complaint-compensation approval rules change in 2026, and what issue prompted the change? | both complaints policies | 1 |
| L010 | What SmartSaver Tier 1 rate applied on 15 October 2025, and how did it compare with the rate on 15 January 2026? | `interest_rate_schedule_2026.md` (history copy) | 0 |
| L011 | A rate review happens around the 15th of a month. When would the revised rate normally take effect …? | `interest_rate_schedule_2026.md` | 0 |
| L025 | An entity customer applied on 10 March 2026 … Which policy and procedure control …? | `policy_customer_identification_v2.md` (history) + 2 others | 2 |

The banking graph already holds the facts: `policy_complaints_v3.md -SUPERSEDES-> policy_complaints.md`,
`interest_rate_schedule_2026_02.md -SUPERSEDES-> interest_rate_schedule_2026.md`, history copies
(`:DocumentHistory`), and `_valid_from` on every document. `candidates.family_documents` already walks
`SUPERSEDES` (B2, for `latest`).

### 2.2 Change

1. **Topic families from the catalogue** (`artmind/query_plan/candidates.py`, `topic_families(question, records,
   names)`, no graph query: the `documents` part already holds names, `valid_from` and live/history). A *versioned
   family* is a set of documents that share a stem once version and date suffixes are stripped (`_v2`, `_v3`,
   `_2026_02`, `_2026`), or that a `SUPERSEDES` link joins (one bounded read, `family_documents`, only for the
   families picked in step 2). Only families with two or more members count: a time question is about versions.
2. **Match by title words.** Score each family by the share of its stem words (`policy complaints`,
   `interest rate schedule`, `policy customer identification`) that the question names, stems compared as in
   `lexical._stem` (`complaint` ≈ `complaints`); plus the catalogue's class and entity hits are not used (they
   pull in generic words). Keep families scoring ≥ 0.5, at most `TOPIC_FAMILIES_MAX` = 2, best first. Generic
   stem words (`policy`, `sop`, `guide`, `schedule`) count half.
3. **Route behaviour.** When `time_history` (or `document` with `latest`) has no `{document}` and a topic family is
   found, the plan fills a new list slot `topic_documents` (source `topic`, quality 0.8) and uses a new program
   `topic_history`:

   ```yaml
   - kind: topic_history
     when: "not document and topic_family"
     program:
       - id: p
         par:
           - id: versions
             map: {over: topic_documents, as: doc, max: 6,
                   do: {id: version, run: "artmind query graph pattern10 --domain {domains} --documentName {doc} --includeHistory --compact"}}
           - {id: needle, run: "artmind query vector-text --shape brief --domain {domains} --topK 10 {search_term} --compact"}
       - {id: u, pick: {from: p, how: union}}
     guards:
       - {on: u, if: "distinct_documents == 0", then: escalate}
   ```

   The text needle stays beside the versions because a time question often also asks "why" (L007, L009). The
   date window slot (already filled for these questions) is recorded but not passed: `pattern10 --asOf` is an
   alias, not a filter (CLAUDE.md, command notes).
4. **Scope.** `domains.resolution` gains `{topic_family: [...], domains: [...]}`: B2's `resolved` rule applies to
   the domains the family's documents live in (banking `L007` → `banking.policy`; `L010` → `banking.reference`).
5. **Confidence.** The topic slot's quality (0.8) is a slot-quality factor (`topic`), so a topic-resolved time plan is
   at most 0.8 × the route's confidence; labels expect `low` for every banking time question today, and the
   margin (§1.4) keeps them there unless the route is sure.
6. **No family found** → today's behaviour (the text search).

Plan fact `topic_family` (count of families found) joins the fixed vocabulary (`question-routing.md` §6.3).

### 2.3 Expected effect and risks

Reach (by hand, on the commands the program would run): `L007` +1 (`policy_complaints.md` via its family),
`L009` +1, `L010` +1 and `L011` +1 (the 2026 schedule family holds the history copy), `L025` +1 if
`policy customer identification` matches ("Which policy … control" names only `policy`: probably not; counted 0).
So +4, reach 71 → about 75. Routes do not change (parity: no route moves; program kinds change on time plans).

Risk: a stem shared by unrelated documents (`sop_account_opening` and `sop_account_closure` share `sop account`).
The family rule needs the *whole* stem after suffix stripping to match, not a prefix, and the title-word share ≥ 0.5,
so `account opening` and `account closure` are different families.

## 3. B5: superlatives need a snapshot, and records belong to the store

### 3.1 Evidence

`superlative` runs `insights list --domain {domain}` then `text2cypher`. banking has no insights snapshot, so the
first step exits 1 in every plan (`L022`, `L035`; reach 0 of 6). nbs_audit_2026 has none either; brain01 has one.
"What is the largest compensation amount recorded for an Account Takeover complaint" (`L033`) and "Which branch manager
has reviewed the most vulnerable-customer cases on record" (`L035`) are maxima over table rows (`complaints`,
`vulnerable_customers`); "Which controls should Internal Audit prioritise … the most material … risks" (`L022`) is a
survey that happens to say "the most".

### 3.2 Change

1. `needs: [insights_snapshot]` on the `insights list` step of `superlative` (and the `insights` route). The fact is
   B9's new `insights` flag on `domains-overview` rows (already read by the planner as `overview`): a step whose
   need is unmet is skipped like an `optional` one (no `unfilled`, no confidence factor), so the plan keeps its
   `text2cypher` step. The `insights` route with no snapshot is `unfilled` and falls back to search (its whole answer
   is the snapshot).
2. **Superlative over records → structured.** When the route is `superlative`, the scope holds structured tables, and
   the question names a bridged class or a table column word (`candidates.store_facts` already returns
   `bridge_classes` and is computed for every plan), the route becomes `structured` (`route.adjusted = {from:
   superlative, by: store_records}`) with the store kind from `store_facts` (B2's `records_plus_guidance` when the
   question also asks what policy requires: `L033`, `L035`).
3. **Superlative with no store and no snapshot** keeps `text2cypher` and adds the text needle (`vector-text`), as a
   survey-like fallback: an inferred ranking is rarely in the graph's numbers alone (`L022`).

### 3.3 Expected effect

`L035` and `L033` (decision) → `structured` with `records_plus_guidance`: bridge + SQL + policy text; `L035` reaches
both expected documents by hand (`vulnerable_customers.csv` via `source_tables`, `branch_operations_training.md` via the
text step). `L022` → `text2cypher` + needle; the needle reaches 1–2 of 4. Reach +2 to +4. brain01's `superlative` labels
(snapshot present, no tables) are unchanged. Route parity: banking `superlative → structured` moves (count reported).

## 4. Measurement and gates

Before and after each item, in every vault: `artmind plan replay --classifier both --repeat 4 --compact`, route parity
(`p2a_plan_parity.py`), and on banking `--reach --classifier both`. Gates for the merge to master: no wrong view; under
10 % wrong; no flips; no non-retrieval label planned as a confident lookup; banking decision reach ≥ 71.

Expected after B3–B5 (decision / lexical, wrong): banking 2 / 0 (`L015`, `L023`, pending §1.4), nbs 5 / 5
(chunks-only `expect: low` and `L027`, pending §1.5), brain01 0 / 0. Non-retrieval labels planned as confident lookups:
nbs lexical 5 → 0 (`L027` becomes a low plan only once it `follows` `L026`; until then it is the one left), decision 1 → 0.

## 5. Slices

| Slice | Files | Model |
|---|---|---|
| S1 B3 (a, b, c) | `intents.yaml`, `intents.py`, `planner.py` (intent stage, margin), `config.py` (`decision_margin`), tests | Sonnet |
| S2 B4 | `candidates.py` (`topic_families`), `slots.py` (`topic_documents`, factor `topic`), `planner.py` (`_plan_facts`, `_list_slots`, resolution), `program.py` (fact name), `routes.yaml` (`time_history`, `document`), tests | Sonnet |
| S3 B5 | `routes.yaml` (`superlative`, `insights`), `routes.py` (step `needs`), `planner.py` (store-records adjustment), tests | Sonnet |

S2 and S3 both touch `planner.py` and `routes.yaml` in different blocks; S1 touches only `planner.py`'s confidence block.
Run S1 first, then S2 and S3 in parallel worktrees, merging S3 after S2 with conflicts resolved by the parent.

## 6. Open questions for the user

1. B3c margin: 0.05 for the decision backend only (recommended), or a single higher threshold?
2. Banking `L015`, `L023` and nbs chunks-only `L005`, `L017`, `L022`, `L024`: relabel `expect: high` (the plan matches
   the label's route and domains and is what the agent should run) or keep `low`? Recommendation: relabel; the gate is
   otherwise unreachable without making the planner abstain on plans it gets right.
3. nbs `L027` ("go ahead"): add `follows: L026` to the label (recommended).
4. B4's version-suffix rule (`_v2`, `_2026_02`): is the stem rule acceptable, or should families come only from
   `SUPERSEDES` links (stricter; misses `sop_account_opening_v3.md`, which has no link)?
