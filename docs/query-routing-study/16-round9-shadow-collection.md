# 16 — Round 9: shadow collection on the merged planner v2

Written 2026-11-07. Round 9 of the collection (`10-collection-sop.md`), run as benchmark runs on the
master build after `integration/p2b` was merged (`6f66640`): planner v2 with P2b and B1–B5, B7–B9. The
decision model is jev-1.14 on OpenRouter. Same privacy rule as the rest of this folder: brain01 is private,
so only counts and label ids appear for it. banking_corpus is public, but this note keeps to ids as well.
Per-turn detail is in each vault's `benchmarking/` and in the analysis session's report.

## 1. What was collected

| Vault | Source | Turns | Shadow plans | Labels before → after |
|---|---|---|---|---|
| brain01 | benchmark run `d642954e` (10 questions) + 8 admin-console turns | 18 | 18, all `ok` | 35 → 49 (L036–L049 new) |
| banking_corpus | benchmark run `b387642f` (the same 36 questions as `191dbda1`, 2026-11-06) | 36 | 36, all `ok` | 34 → 36 (L037, L038: 2026-11-06 drafts never merged) |

No shadow plan was lost, including the last question of each benchmark run. The decision model never fell
back to lexical and never timed out; its latency was 0.5–1.3 s.

## 2. Shadow agreement on lookup turns

Agreement means the agent ran a command of the plan's first node (`traces.analysis.agreement`).

| Vault | High plans | Low plans | All |
|---|---|---|---|
| brain01 (benchmark) | 1 of 2 | 5 of 8 | 6 of 10 |
| brain01 (admin turns with a lookup) | 0 of 1 | 2 of 6 | 2 of 7 |
| banking_corpus, 2026-11-07 | 2 of 3 | 26 of 33 | **28 of 36** |
| banking_corpus, 2026-11-06 (before B2–B9) | 2 of 8 | 8 of 28 | 10 of 36 |

- **Banking agreement rose from 10 to 28 of 36.** It comes mostly from B2: the 16 plans that were
  `procedure` are now `search`, whose first node (survey + vector-text) is what the agent runs.
- **High plans are rare:** 3 of 36 in banking (8 before), 3 of 18 in brain01. The weakest stage of a
  banking low plan is slot quality (14), domain (10) or route (12). In brain01 it is mostly route.
- **The structured route matches.** All 5 structured banking questions planned `structured`, and the agent ran
  `db sql` on each, including the two that were `superlative` before B5. The only gap is that the agent skips
  `db bridge` and opens with `db schema`.
- **Follow-ups ignore the previous turn.** In 0 of 4 brain01 follow-ups did the plan use the previous turn
  (`previous_used` false), including a bare "yes, run a broader sweep".

## 3. Confident plans that are wrong

These matter most for P3: on the fast path a wrong high plan answers from the wrong evidence, with no
judgement.

| Label | Plan | What the evidence needed |
|---|---|---|
| brain01 L038 | `path`, high, between two group entities | two separate relationships of the narrator (entity-context on each) |
| brain01 L047 | `follow_up_why`, high, a survey of the whole sentence | the four entities it names, resolved one by one |
| banking L037 | `conflicts`, high, `graph conflicts --status all` | the superseding version of a policy (closer to `time_history`) |

All three are decision-model plans. Lexical plans them low.

## 4. Low-plan disagreements

The agent's choice was the better one on almost all of them:

- **Entity routes cite only the entity's own documents** (B11): banking L004 and L012. The agent went to survey.
- **Recurring-date questions** (brain01 L041) have no route. The agent read the note for that date in each year.
- **Counts of happenings that are not a class** (brain01 L037) planned text2cypher. The agent searched the text.
- **`entity_history` chosen for a topic over time** in a journal: brain01 L044, L048.
- **A weak entity match anchoring the plan**: brain01 L045, L049; banking L010, where the document slot was unfilled.

## 5. Replay gates (`--classifier both --repeat 4`)

Correct / abstained / correct abstain / wrong:

| Vault | Lexical | Decision model |
|---|---|---|
| brain01, 49 labels | 16 / 14 / 17 / 1 (L045), gate passes | 19 / 11 / 16 / 2 (L038, L047), gate fails: flips L001, L012, L017, L035, L040 (level only) |
| banking_corpus, 36 labels | 0 / 10 / 26 / 0, gate passes | 2 / 8 / 25 / 1 (L037), gate passes, no flips |

- **No wrong view anywhere**, and no non-retrieval label was planned as a confident lookup.
- **The decision model's wrong labels are exactly the three confident plans of §3.** Lexical's only wrong
  label is brain01 L045 (`survey` at high where the label says `enumeration`, low).
- **The decision model's flips are level-only and vary between runs.** Banking L007 and L028 flipped in one
  replay of the same labels and were stable in the next.

## 6. What it means for next steps

1. **Before P3's fast path relies on the decision model, guard the three confident shapes in §3** (a proposed
   B12): `path` between two non-named group entities, `follow_up_why` whose question names entities, and
   `conflicts` where the question is about a superseded version.
2. **P3's value is mostly the reasoning path.** With high plans this rare, running a low plan's program as a
   bundle for the agent matters more than skipping the agent.
3. **B11 is now confirmed live**, not only in replay (banking L004, L012). It waits on P3's step bindings, as
   decided.
4. **Smaller items:**
   - a recurring-date route (brain01 L041);
   - follow-ups that use the previous turn;
   - the structured program opening with `db schema`, as the agent does, instead of `db bridge`.
