# B2: one text search, cross-document by default, and scope by evidence in families of domains

Date: 2026-11-07. Status: **design draft for review**. Not yet planned or built.
Parent: `2026-11-02-retrieval-curation-architecture-design.md` (§9, row B2) and planner v2
(`2026-11-05-planner-v2-design.md`, §5.5 and §14.4). Builds on B1, the domain decision as a set
(`artmind/query_plan/domain_set.py`, merged on `integration/p2b` at 5e40812;
`docs/question-routing.md` §3.1). Evidence: the banking benchmark of 2026-11-06
(`<banking vault>/benchmarking/routing_findings_20261106.md`, findings B1, B2, B6) and replays
run for this spec on today's code. Related later fixes: B3 (intent and confidence for
instructions to the assistant, and calibration of the decision model), B4 (time questions that
name a topic, not a document).

Privacy rule as everywhere in this checkout: brain01 and nbs_audit_2026 appear by counts and label
ids only. banking_corpus is a public sample corpus: its file names and question ids (`Qnn`,
`Lnnn`) may appear, never answer text. Domain names in examples (`X`, `X.a`) are invented.

---

## 0. In one page

On the banking benchmark one `survey --shape brief --domain banking` reaches **84 of the 108**
documents the answer key expects. The decision model's shadow plans reach **35**, the agent's
whole traced path **75**. The plans lose on two things: they run one narrow text step, and they
run it in one child domain while the answer spans two to four. B2 fixes both, in four changes:

1. **The `procedure` route is removed.** It ran the same `vector-text --topK 10` as `search`
   and only added a confusable option: the decision model chose `procedure` for 14 banking questions
   labelled `search`, `survey` or `time_history`. Its triggers and evidence move to `search`.
2. **`search` runs the cross-document program in every domain, not only chunks-only ones.** A needle
   `vector-text --topK 10` and a `survey --shape brief` run **in parallel**. The needle answers alone
   only when its ranking has a clear winner (`score_drop >= NEEDLE_MARGIN`) **and** its top ten sit in
   few documents (`distinct_documents < NEEDLE_SPREAD`, default 5); otherwise the two are merged.
   The number of hits is no signal: `vector-text` always returns `topK` rows. Until the executor
   (P3 `query run`) applies the rule, the plan lists both commands and states the rule.
3. **`search` and `survey` are graded as one route in replay.** They run the same commands; labels
   split them inconsistently. `survey` stays a route (recommended, §5): its program always merges and
   never lets the needle answer alone, for questions that ask across documents by their wording.
4. **Scope by evidence in families of domains** (`X`, `X.a`, `X.b`). After B1 picks its set, a
   second step re-scopes by route: text routes search the **family root** (`--domain X` rolls the
   children up); entity and document routes resolve their subject across the family and search
   **where it lives** (`_domain` of the resolved candidates); `structured` takes the domains that
   **hold the tables** (catalogue `stores`). The classifier's child choice becomes a tie-breaker.
   New `set_rule` values `family`, `resolved`, `stores`; B1's own set is kept in the payload as
   `domains.classifier`. A named domain, given domains and chunks-only-all sets are untouched.

Expected effect, from today's replay re-scored by hand (§2.3): on banking the decision model's
`domains` mismatches fall from 17 to at most 6 and `route` mismatches from 24 to 8. Its **wrong**
count barely moves (9), because 8 of the 9 are `confidence` mismatches (plans high where the label
expects low); that is B3's work, and B2 says so rather than hiding it. The gate that measures what
B2 is for is **document reach** on banking: the plan's own steps must reach at least 75 of the 108
expected documents (the agent's level), target 84 (the family survey). nbs_audit_2026 and brain01
have no families; for them B2 changes only the route list, the grading of `search` vs `survey` (two
nbs decision-model wrongs become correct) and the graph-domain `search` program, and replay must not
regress.

## 1. Scope

| In | Out |
|---|---|
| Removing route `procedure`; its triggers and evidence move to `search` | Confidence calibration of the decision model, overconfident plans (B3) |
| `search` and `survey` programs in graph domains; the `NEEDLE_SPREAD` constant; `or` in step predicates | Time questions with no named document (B4); `time_history` keeps its text fallback, now scoped to the family |
| Replay: `search` ≡ `survey` | Asking the decision model differently (multi-select domains; B1 finding 2), see §11 Q5 |
| Family scope rules for text, entity, document and structured routes | Scope of `existence`, `enumeration`, `aggregate`, `superlative`, `conflicts`, `inventory`, `insights`, `filing` (B1 as is) |
| Shadow agreement that counts any command of a parallel first node | The executor itself (P3); skill changes (P3 diet) |
| Document reach as a measured gate | New retrievers or ranking changes; survey depth stays the brief default (50) |

## 2. Evidence

### 2.1 The banking benchmark, 2026-11-06

36 questions (`Q01`–`Q36`), 108 expected documents in the answer key, admin console, Claude SDK
agent, shadow plans by the decision model.

| What reached the expected documents | Of 108 |
|---|---|
| shadow plans' own steps (decision model) | 35 |
| lexical plans' steps | 25 |
| the agent's traced calls (4.7 per question; `artmind db` calls are not traced) | 75 |
| one `survey --shape brief --domain banking` | **84** |
| one `vector-text --topK 10 --domain banking` | 53 |

- 18 of the 36 answers need two to four child domains. The decision plan chose one child in 32 of
  36 (full coverage 11, partial 15, miss 9), and named `domain` its weakest stage in 23.
- The agent's **first** step was `survey` in 26 of 36 turns, `vector-text` in 6 and a graph command
  in 4; no turn fell through from graph to text. On 21 October, before `survey` existed, the first
  step was graph in 19, text in 12 and SQL in 5, and about 51 % of the expected documents were
  reached. The agent has already moved to "survey first"; the plans have not.
- `Q36` routed to `structured` but in `banking.policy`, while the tables sit on the parent `banking`
  (finding B6).

### 2.2 Replay on today's code

`ARTMIND_NO_PROXY=1 ARTMIND_PLAN_SHADOW=0 artmind plan replay --classifier both --repeat 4 --compact`
from inside each vault, confirmed labels, code at 5e40812 (B1 merged). Outcomes are correct /
abstained / correct abstain / wrong.

| Vault | Labels | Classifier | Outcomes | Mismatches | Unstable | Gate |
|---|---|---|---|---|---|---|
| banking_corpus | 34 | decision | 0 / 7 / 18 / 9 | domains 17, route 24, confidence 8 | 10 | fails |
| banking_corpus | 34 | lexical | 0 / 8 / 26 / 0 | domains 5, route 25 (every plan low) | 0 | passes |
| nbs_audit_2026 | 27 | decision | 13 / 0 / 3 / 11 | route 6, intent 4, confidence 5, lookup 1 | 2 | fails |
| nbs_audit_2026 | 27 | lexical | 16 / 0 / 0 / 11 | route 1, confidence 6, lookup 5 | 0 | fails |
| brain01 | 35 (1 planned) | decision | 23 / 4 / 6 / 1 | route 4, intent 1, lookup 1 | 2 | fails |
| brain01 | 35 (1 planned) | lexical | 15 / 13 / 6 / 0 | route 6, view 2 | 0 | passes |

The nbs and brain01 failures are intent and confidence (B1 hand-over finding 1; B3), not B2's.
The parent session's run of the banking labels the same day gave decision 8 wrong, domains 24, route
23, confidence 7: the decision model is not deterministic (10 labels flip between repeats), so §8
gates on differences measured in one run each, before and after, on the same day.

In this run the decision model chose `procedure` for 14 banking labels (7 labelled `search`, 6
`survey`, 1 `time_history`) and confused `survey` with `search` 3 times; on nbs_audit_2026 it chose
`procedure` 5 times and confused the two 2 times (labels `L010`, `L018`, both wrong today on the
route alone); on brain01 4 and 1.

Confirmed banking label routes: `survey` 10, `search` 9, `time_history` 7, `structured` 5,
`entity_profile` 2, `entity_history` 1. 20 of the 34 name two or more child domains; 14 of the 19
`search`/`survey` labels do. No confirmed label in any of the three vaults names `procedure` (the
banking draft `plan_labels.draft.pre-b2.yaml` has 6). nbs_audit_2026 has 21 multi-domain
`search`/`survey` labels over its chunks-only domains, already served by B1's `chunks_only_all`.

| Vault | Domain schemas | Dotted (family) domains | Retrieval labels (needle / survey) |
|---|---|---|---|
| banking_corpus | 11 | 8 children of `banking` | 50 / 10 |
| nbs_audit_2026 | 5 | 0 | 126 / 10 |
| brain01 | 2 | 0 | none |

### 2.3 What B2 alone would change in that replay

Re-scoring the banking decision run's per-label detail with (a) `procedure` read as `search`, (b)
`search` ≡ `survey`, (c) every text-route plan scoped to the family root `banking`:

| | Today | With B2 |
|---|---|---|
| `route` mismatches | 24 | 8 |
| `domains` mismatches | 17 | ≤ 6 (11 were on text-route plans; the other 6 are on entity, time, document, superlative and structured plans, which the §6 rules may also fix) |
| wrong | 9 | 9 (8 carry `confidence`: high plans for labels that expect low) |

So replay will undersell B2 on banking: the plans become right in scope and route and stay
overconfident. Document reach (§8.3) is the gate that measures the change B2 is for.

## 3. Remove the `procedure` route

`procedure` (`routes.yaml`) is `one_step`: `vector-text --shape brief --topK 10 {question}`, guarded by
`rows == 0 → escalate`. `search`'s default program is the same command (default `--topK` 10). The
route adds a labelling decision and a classifier option that change nothing in the plan, and the
decision model uses it as a sink for operational questions (14 of 34 banking labels).

**Change.**

- Delete the `procedure` route. `search` gains its triggers (`how do i`, `how do we`, `how to`,
  `steps to`, `procedure for`, `process for`, `what should`, `what happens when`, `what are the
  steps`) and its evidence (`S19`, `P14`). `search` had no triggers, so lexical scoring picked it
  only as the default (confidence 0.5); with triggers a "how do I" question scores as `search`
  directly. Their weight against the other routes' triggers is checked by replay (lexical route
  mismatches must not rise).
- `search`'s description becomes: "Any other factual question, or how to do something (steps,
  checks, escalation), answered from the text of the documents." It is what the decision model reads.

**What still names it.** Nothing needs a mechanism of its own:

- **Labels.** Once the route is gone from `routes.yaml`, `artmind plan labels validate` rejects a label
  naming it as it rejects any unknown route. No confirmed label in the three vaults names `procedure`;
  only banking's unconfirmed draft `plan_labels.draft.pre-b2.yaml` does, and it is rewritten as
  `search` when it is confirmed.
- **Plan config.** No `scope_rules` entry in the three vaults names it.
- **Recorded traces.** Shadow plans recorded before B2 did plan `procedure`; historical stats count
  them as such, which is what happened. Route parity (before vs after B2) shows `procedure → search`
  as the intended change (§8.2).

## 4. The text program in graph domains

### 4.1 Program

`search` drops its `one_step` program. Its two programs today become one, used in every scope:

```yaml
- id: search
  kind: cross_document
  program:
    - id: p
      par:
        - {id: needle, run: "artmind query vector-text --shape brief --domain {domains} --topK 10 {search_term} --compact"}
        - {id: wide, run: "artmind query survey --shape brief --domain {domains} {question} --compact"}
    - {id: u, pick: {from: p, how: union},
       when: "needle.score_drop < NEEDLE_MARGIN or needle.distinct_documents >= NEEDLE_SPREAD"}
  guards:
    - {on: wide, if: "truncated", then: {page: {until: k, k: 50}}}
    - {on: u, if: "distinct_documents == 0", then: escalate}
  evidence: [S2, S19, P14, P43]
```

- **In parallel, no extra round trip.** The user agreed: the survey's brief shape is cheap (rows carry
  id, title, date, status and a gist; the output budget cuts the rest,
  `2026-11-05-bounded-output-design.md`), and the two commands share no input. Wall time is the slower
  of the two, not the sum. The executor latency gate (P3a: ≤ 3 s for one to four steps) covers it.
- **When `u` does not run**, the evidence is the needle's alone; `wide` is in the bundle's `path` (it
  ran) but not in its evidence. The chunks-only program already reads this way; the build states it
  in the executor's contract (planner v2 §6.2).
- `{search_term}` is the slot (mentions, else the question minus filler); an empty term renders as
  the chunks-only program renders it today.
- The program kind is renamed `chunks_cross_document` → `cross_document` (§11 Q2). The old name stays
  readable in `traces stats` and in labels' `program_kind` (recorded, never graded).
- The planner's chunks-only special case ("a chunks-only scope plans only `survey` or `search`") is
  unchanged; it now plans the same program as every other scope.

### 4.2 When the needle answers alone

Two conditions, both required:

| Signal | Predicate | Why |
|---|---|---|
| a clear winner | `needle.score_drop >= NEEDLE_MARGIN` (0.03, planner v2 §11) | the first passage stands out from the second: one passage holds the answer |
| few documents | `needle.distinct_documents < NEEDLE_SPREAD` (new, default 5) | a clear winner inside a top ten that touches many documents is one hit among many: the topic is spread, and the survey's breadth is needed |

`distinct_documents` on the needle step is the number of distinct document ids among its ten rows.
`score_drop` is the relevance (best passage cosine) of rank 1 minus rank 2; when rank 1 or 2 has no
relevance (found by the keyword leg only) there is no clear winner, and the merge runs.

**The number of hits is the wrong signal.** `vector-text` returns `topK` rows whenever the scope
holds that many passages, so `rows` is 10 for almost every question and says nothing about whether
the answer sits in one place. The guard that `procedure` carried (`rows == 0 → escalate`) moves to the
merged node, where it means "no document at all".

**Grammar.** The rule needs `or`. Step predicates today join with `and` and `not` only, without
parentheses. B2 adds `or` with the lowest precedence (`a and b or c` = `(a and b) or c`), still without
parentheses; `not` binds tightest, as now. `PREDICATE_TYPES` is unchanged; `program.CONSTANTS` gains
`NEEDLE_SPREAD: 5`.

### 4.3 Calibrating `NEEDLE_SPREAD`

From the retrieval eval, which already holds labelled needles (one known chunk answers) and surveys
(several documents answer): nbs_audit_2026 126 / 10, banking_corpus 50 / 10.

1. `artmind retrieval eval` gains a per-question record of the needle's `distinct_documents` and
   `score_drop` (the top-10 `vector-text` it already runs; no new retrieval; written to the private
   run folder with the other per-question rows).
2. For each candidate `s` in 2..10, count the survey labels the rule would answer by the needle alone
   (must be 0), and the precision of the shortcut: needle labels answered alone whose relevant chunk is
   in the needle's top ten (must be ≥ 95 %).
3. Take the largest `s` that meets both in both vaults (the most questions answered by one command);
   on a tie, or a miss in either vault, the smaller `s` (merging more is the safe direction: it adds
   evidence, never removes it).

The default 5 is the prior: half the needle's rows come from different documents. Calibration is a
build step (S4), and the number goes into planner v2 §11 beside `NEEDLE_MARGIN`.

### 4.4 Until the executor runs it

Nothing executes plans before P3. Until then the plan is data:

- `steps` (or `suggested_steps`) list both commands, needle first; `program` holds the `par`, the
  `pick` and its `when` text, so an agent or a reader can apply the rule by hand.
- **Shadow agreement** (`traces.analysis.agreement`) compares the agent's spans with the plan's first
  traced step. With a parallel first node that is the needle, so an agent that started with `survey`
  (26 of 36 banking turns) would count as disagreeing with a plan that contains `survey`. B2 changes
  agreement to "the agent ran any command of the plan's first node" (the children of a `par`).
- The `artmind-query` skill is not changed by B2: the P3 diet replaces its text-search guidance with
  the bundle contract.

## 5. `search` and `survey`: one grade, two routes

Once both run the same two commands, the only difference between them is the merge: `search` may let
a clear needle answer alone, `survey` never does. Labellers and the decision model split them
inconsistently, and the split no longer changes which commands run.

**Replay grades them as one.** `replay.ROUTE_GROUPS = {"search": "text", "survey": "text"}`: a route
mismatch counts only when the groups differ. The same mapping applies in `decision_signature`, so a
decision model that flips between `search` and `survey` across repeats is not unstable (the steps are
identical; the program kind is the same `cross_document`). `per_route` stays keyed by the label's own
route, and the summary adds `route_exact` (labels matched without the group) so the split stays
visible.

**Recommendation: keep `survey` as a route** with this program:

```yaml
- id: survey
  kind: cross_document
  program:
    - id: p
      par:
        - {id: needle, run: "artmind query vector-text --shape brief --domain {domains} --topK 10 {search_term} --compact"}
        - {id: wide, run: "artmind query survey --shape brief --domain {domains} {question} --compact"}
    - {id: u, pick: {from: p, how: union}}
  guards:
    - {on: wide, if: "truncated", then: {page: {until: k, k: 50}}}
    - {on: u, if: "distinct_documents == 0", then: escalate}
```

Why keep it rather than merge it too:

1. **The wording decides it, deterministically.** "Compare", "across", "what do the contracts say" ask
   for breadth. For those a clear needle is still not an answer, and `survey`'s triggers make that a
   program choice in code. The opposite error is harmless: a needle question planned as `survey`
   merges, which adds evidence.
2. **Labels and vocabulary stay.** 16 confirmed labels across the three vaults name `survey`; the
   command, the skill and the labels cheat sheet use the word. Merging would rewrite all of them for no
   change in commands.
3. **Keeping it costs nothing in grading.** With the two graded as one, a confusion between them is no
   longer an error anywhere.

The alternative, one route `search` with a plan fact `cross_document` (from the survey triggers) that
selects the always-merge program, behaves the same and removes a classifier option. It is the fallback
if shadow data shows the two routes never differ in the bundle (§11 Q1).

## 6. Scope by evidence in families of domains

### 6.1 The problem B1 leaves

B1 turns the classifier's probabilities into a set, so a spread decision selects several children or
the family parent. On banking the decision model rarely spreads: it puts 0.70–0.99 on one child for
questions whose labels span two to four, so B1's `margin` and `family_parent` rules never fire (B1
hand-over, finding 2). Its answer to "which child holds it?" is a guess it is sure of. The evidence of
where things live is in the catalogue: names and documents per domain, `_domain` on every resolved
candidate, `stores` per domain. B2 scopes from that evidence.

A **family** is the set of domains sharing a root: the part of a domain name before its first `.`
(`X` for `X.a` and `X.a.b`), the same grouping the schema reference uses. The root `X` is always a
domain option (B1 offers each family parent), may be a real domain itself (on banking the parent
`banking` holds the structured tables), and `--domain X` reaches every child: every command a template
names takes a comma-split `--domain` and rolls sub-domains up under a parent.

### 6.2 Rules

B2 adds one step, `domain_set.rescope(dset, route, ...)`, after B1's decision. It applies only to a
set **the classifier decided**: B1 rules `margin`, `family_parent`, and `single` when the vault had
more than one option. It never touches `given`, `named`, `chunks_only_all`, `all`, or a vault with one
option. To tell the two kinds of `single` apart, `DomainSet` gains `decided: bool` (the classifier
chose it); `set_rule` values of B1 keep their meaning.

Within a decided set, each selected domain that belongs to a family is handled by the route:

| Route | Re-scope | `set_rule` |
|---|---|---|
| `search`, `survey` | the selected domain is lifted to its family root; other selected domains stay | `family` |
| `entity_profile`, `entity_history`, `hierarchy`, `path` | resolve the entity (both, for `path`) over the family root's name index; select the minimal cover of the `_domain`s of the chosen candidates and their twins | `resolved` |
| `document`, `time_history` | match the document over the family's documents; select the domains holding the matched document (and its version family, for `latest`) | `resolved` |
| either row above, when nothing resolves | the family root, so the route's text fallback searches the whole family | `family` |
| `structured` | the family's domains whose catalogue `stores.structured_tables > 0` (minimal cover), plus the family root for a `records_plus_guidance` store kind, whose text step needs the policy side | `stores` |
| every other route | unchanged (B1's set) | B1's rule |
| a view matched | unchanged: a view is used only when it covers every selected domain, and is checked against B1's set before any re-scope | B1's rule |

Rules in words:

- **A named child still wins.** "In X.a, ..." is B1 `named` and is never re-scoped.
- **The classifier's child choice is a tie-breaker only.** When a name resolves to different entities
  in several children and the route takes one entity, the candidate in the child with the highest
  share wins, and `domains.tie_break: "classifier"` is recorded. When it resolves to the same entity
  (same id) or to twins the program unions, every child it lives in is selected.
- **Resolution uses what the planner already builds.** The family's merged name index
  (`_merged_names` over the cover's catalogues, built today for the gazetteer) and the document pool
  over the scope (`document_pool`, already computed over the cover). No new graph query on the plan's
  path.
- **Store domains get their own placeholder.** The `structured` variants render `db bridge` and
  `text2sql` with `{store_domains}` (the table-holding domains) and their `vector-text` step with
  `{domains}`. Today both use `{domains}`, which put `Q36`'s SQL in `banking.policy`.
- **A guard's `replace` keeps the scope.** An entity route whose step comes back empty and is replaced
  by `search` (`rows == 0 → replace: search`) runs the search over the plan's set; for a `resolved`
  set that is where the entity lives, which is the right place to read about it.

### 6.3 Payload

`domains` keeps every B1 key and adds:

```json
{"selected": ["X"], "set_rule": "family", "family": "X",
 "classifier": {"selected": ["X.b"], "set_rule": "single", "coverage": 0.91},
 "resolution": null, "tie_break": null}
```

`classifier` is B1's set, kept for measurement: shadow stats can count how often evidence moved the
scope, and replay can grade B1's set beside B2's (recorded only, never gating). `resolution` is
`{"entity": <name>, "domains": [...]}` or `{"document": <name>, "domains": [...]}` for `resolved`.

### 6.4 Confidence

The `domain` stage keeps B1's meaning, *how much of the classifier's mass the searched scope covers*,
measured on the family the rule searched rather than on the narrowed set:

- `family` and `stores`: the coverage of the family root (B1 `covered_mass`). It is at least the
  coverage of the child the classifier chose, so lifting never lowers the stage.
- `resolved`: also the coverage of the family root. The narrowed set comes from evidence, not from the
  classifier, so the classifier's mass on another child must not lower it. How well the entity
  resolved is the slot-quality `mention` factor (0.7 for a fuzzy top match), as today.
- **Whole vault** (B1): when the family root covers every option of a vault of graph domains (banking:
  every domain is `banking.*`), searching it is no domain decision; the stage is the classifier's
  confidence in its own top choice, exactly as B1 decided. B2 does not change that rule.

So on banking B2 makes plans neither more nor less confident: the stage is the decision model's own
confidence before and after. Overconfidence (8 of 9 wrong) stays B3's to fix.

### 6.5 Examples (invented)

| Question | B1 set | B2 set |
|---|---|---|
| "What steps apply when a card is reported stolen?" (search) | `X.sop` (single, 0.93) | `X` (family) |
| "Who is Ann Lee?" (entity_profile); Ann resolves in `X.org` and `X.cases` | `X.policy` (single) | `X.cases, X.org` (resolved) |
| "How many accounts are overdue per region?" (structured); tables on `X` | `X.policy` (single) | `X` (stores) |
| "In X.policy, what is the notice period?" | `X.policy` (named) | unchanged |
| a vault with `X.a`, `X.b`, `Y`; search; B1 margin `X.a, Y` | `X.a, Y` | `X, Y` (family) |

## 7. Changes to existing pieces

| File | Change |
|---|---|
| `artmind/query_plan/routes.yaml` | delete `procedure`; `search` gains its triggers, evidence and description; `search` and `survey` carry the `cross_document` programs of §4.1 and §5; `structured` variants use `{store_domains}` |
| `artmind/query_plan/program.py` | `or` in predicates; `CONSTANTS["NEEDLE_SPREAD"] = 5`; kind `cross_document` (`chunks_cross_document` read as it) |
| `artmind/query_plan/domain_set.py` | `family_root`, `DomainSet.decided`, `rescope()` with rules `family`, `resolved`, `stores`; the docstring table |
| `artmind/query_plan/planner.py` | call `rescope` after the route answer; resolve entities and documents over the family for the `resolved` routes, then narrow; `store_domains` in the template context; the payload keys of §6.3 |
| `artmind/query_plan/replay.py` | `ROUTE_GROUPS` in `score` and `decision_signature`; `route_exact` in the summary |
| `artmind/traces/analysis.py` | `agreement` over the first node's children |
| `artmind/retrieval_eval/runner.py` | per-question needle `distinct_documents` and `score_drop` (§4.3) |
| `docs/question-routing.md`, `docs/question-routing-labels.md` | route table without `procedure`; `search` cross-document everywhere; §3.1 gains the B2 rules; labels: `search`/`survey` graded as one |

## 8. Measurement and gates

### 8.1 Replay (three vaults, confirmed labels)

The command of §2.2, from inside each vault, once on the B1 merge (5e40812, baseline in §2.2) and once
on the B2 build, the same day.

| Vault | Gate |
|---|---|
| banking_corpus, decision | `domains` mismatches ≤ 6 and `route` mismatches ≤ 8 (from 17 and 24); no label wrong after B2 that was correct or abstained before |
| banking_corpus, lexical | still 0 wrong; `route` mismatches no higher than 25 (the moved `procedure` triggers must not pull other shapes into `search`) |
| nbs_audit_2026, brain01 | per label, no outcome worse than the baseline (correct or abstained → wrong); `L010` and `L018` (nbs, decision) no longer route mismatches; brain01 lexical gate still passes; unstable counts no higher |

### 8.2 Route parity

`~/artmind-private/scripts/p2a_plan_parity.py plan` on each vault's recorded questions with the B1
merge and with B2, then `compare`. Gate: routes identical except `procedure → search` (the intended
change); on nbs_audit_2026 and brain01, which have no families, `domains` identical
for every question; on banking, `domains` changes only for plans on the routes of §6.2.

### 8.3 Document reach (banking)

The measure B2 is for: how many of the answer key's 108 expected documents the plan's **own** steps
reach on the 36 benchmark questions. A document is reached when it appears in any step's evidence
(survey `documents[].id`, a `vector-text` row's document, a graph row's source document), every child
of a `par` counted, the needle-or-merge rule applied as §4.2 states.

| Plans | Today | Gate | Target |
|---|---|---|---|
| decision model, `steps` + `suggested_steps` | 35 | ≥ 75 (the agent's whole path) | ≥ 84 (one family survey) |
| lexical, `suggested_steps` | 25 | ≥ 75 | ≥ 84 |

The 2026-11-06 analysis ran from scratch scripts. The build makes it repeatable (§11 Q4): banking's
labels gain `expected_documents` (the field exists in the label format, unused today; banking is
public, so the answer key's document list may be written there), and `artmind plan replay --reach`
executes each label's first plan's read-only steps in process and reports reached / expected per label
and in total (counts and ids only; detail to the private logs folder, as replay does). It needs Neo4j
and is slow, so it is a flag, never the default.

### 8.4 Needle rule, latency, suite

- Retrieval eval, both vaults (§4.3): on survey labels the rule answers by the needle alone 0 times
  (of 20); on needle labels the shortcut's precision is ≥ 95 %.
- Plan latency: p50 and p95 per vault within 10 % of the baseline (the `resolved` rules read indexes
  the planner already builds).
- The hermetic suite is green, including the route, program, domain-set, labels, replay and traces
  tests named in §9.

## 9. Implementation slices

Each slice is one commit (or a short series) on a branch off `integration/p2b`, test-first.

| Slice | Contents | Tests |
|---|---|---|
| **S1 Routes** | §3, §4.1 and §5 in `routes.yaml`; `or` and `NEEDLE_SPREAD` in `program.py`; kind `cross_document`; the planner's chunks-only branch reads the new program | `test_query_plan_routes.py` (route list without `procedure`), `test_query_plan_authored_routes.py` (kinds), a predicate test for `or` precedence, a planner test: a graph-domain `search` plans both commands with the rule |
| **S2 Grading** (lands with S1) | replay `ROUTE_GROUPS` and `route_exact`; `agreement` over the first node | replay scores `search` vs `survey` as a match and not as a flip; `agreement` true when the agent ran the `survey` child |
| **S3 Family scope** | §6 in `domain_set.py` and `planner.py`; `{store_domains}`; payload keys | `test_query_plan_domain_set.py`: each rule and each exclusion (named, given, chunks-only-all, all, view) with invented `X`, `X.a` domains; planner tests with a mocked family catalogue: entity resolved in a child the classifier did not pick, tie-break, unresolved → `family`, structured to the table domain; replay `_domains_match` unchanged |
| **S4 Measurement** | retrieval-eval needle stats; `expected_documents` + `plan replay --reach`; calibration of `NEEDLE_SPREAD`; the §8 runs; numbers into planner v2 §11 and `docs/question-routing.md` | a hermetic `--reach` test over a stub plan function and canned step payloads |

Order: S1+S2, then S3, then S4. S4's runs gate the merge into `integration/p2b`.

## 10. Risks

- **Two commands where one ran.** Every graph-domain text question now also runs a survey. It is the
  agent's own first step in 26 of 36 banking turns already, brief output is bounded, and the P3a
  latency gate covers the executor.
- **The family root is too wide for a deep or large family.** The root may hold many children that are
  irrelevant to the question. The survey ranks across them, and a named child still narrows. If a vault
  grows a family where this hurts, the root can move to the deepest common parent of the classifier's
  top options (§11 Q3).
- **Replay looks unchanged.** The decision model's wrongs on banking are confidence wrongs, so the
  headline number stays near 9. §2.3 and §8.3 say so in advance; reach is the gate.
- **Entity names shared across children.** A common name resolving in several children widens the set.
  That is the intent (the answer may span them); the tie-break applies only when the route needs one
  entity and the candidates differ.
- **`search` ≡ `survey` hides a real split.** `route_exact` keeps it visible; if shadow data shows the
  two never produce different bundles, merge them (§11 Q1).

## 11. Open questions, each with a recommendation

1. **Keep `survey` as a route?** Recommendation: keep it, with the always-merge program, graded with
   `search` as one (§5). Revisit after round 9: if no `search` plan's needle-alone shortcut fires where
   `survey` would have merged, fold `survey` into `search` with a `cross_document` plan fact.
2. **Rename the program kind to `cross_document`?** Recommendation: yes, reading the old name in stats
   and labels. "Chunks" stops being true once graph domains use it.
3. **Family = the root before the first `.`, or the deepest common parent?** Recommendation: the root,
   as the schema reference groups families today; every vault in use has one level of dots. Change it
   when a vault with nested families exists.
4. **Where document reach lives.** Recommendation: in the repo as `plan replay --reach` reading labels'
   `expected_documents`, not a private script: later fixes (B4, P3) need the same gate, and its output
   is counts and ids. The labels with expected documents stay in the vault.
5. **Should B2 also change how the domain question is asked** (multi-select, or "which domains hold
   part of the answer" with per-option probabilities; B1 finding 2)? Recommendation: no. Scope by
   evidence makes the classifier's child choice a tie-breaker for the routes that matter on banking;
   asking differently changes the decision model's prompt for every route and belongs with B3's
   calibration work, measured on its own.
6. **Family scope for `existence`, `enumeration` and `aggregate` too?** Recommendation: not in B2.
   Their labels in a family vault are few (banking has none), so the change could not be measured. Add
   them when labels show the miss.
7. **`NEEDLE_SPREAD` default before calibration.** Recommendation: 5; calibration (§4.3) replaces it
   in S4 before merge, and the measured value is recorded in planner v2 §11.
