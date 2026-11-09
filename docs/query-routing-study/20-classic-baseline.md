# 20 — Classic baseline (2026-11-09)

The phase-1 baseline from the spec
`docs/superpowers/specs/2026-11-08-classic-baseline-and-skill-simplification-design.md`:
fresh runs of the classic `artmind-query` skill on branch `baseline/v0.11-classic`, judged
and graded with `artmind traces compare` against the classic runs of 2026-11-08 and against
tiered expected answers (`Must:` / `Optional:` / `Must-not:` in each evaluation comment).
Counts, run ids and question ids only (privacy rule in the README).

Judge and grader: `claude-haiku-5-5` (the vault's `ARTMIND_KG_LLM_MODEL`). Expected answers
came from the current questions files (`--questions`), so the 2026-11-08 runs were graded
against the same tiers as the new ones.

## Runs

| Vault | Run | Id | Questions | Skill | Median s | Median $ | Median commands |
|---|---|---|---|---|---|---|---|
| banking_corpus | A (2026-11-08) | `61cf661f` | 36 | classic | 26.0 | 0.125 | 4 |
| banking_corpus | D (2026-11-08) | `15837ded` | 36 | classic | 23.0 | 0.121 | 4 |
| banking_corpus | G | `f03056a8` | 36 | classic | 27* | 0.12* | 4* |
| brain01 | A (2026-11-08) | `49bc9ef6` | 14 | classic | 27.4 | 0.120 | 5.5 |
| brain01 | D (2026-11-08) | `d6c41e2c` | 14 | classic | 28.3 | 0.128 | 5.5 |
| brain01 | G1 | `7a1d832d` | 29 | classic | 23.3 | 0.121 | 5 |
| brain01 | G2 (G1 + 1 h) | `a984341d` | 29 | classic | 24.7 | 0.118 | 5 |

\* banking G: mean 27 s per question, $4.42 for the run.

brain01 grew from 14 to 29 questions (Q15–Q29 new: list-by-connection, top-N-by-connection,
recurring-event windows, multi-part histories, existence checks with a distractor, one
single-fact control). Every question in both files now carries tiers.

## Banking (36 questions, 221 must items)

| Run | Correct | Partial | Wrong | Must found |
|---|---|---|---|---|
| A | 5 | 30 | 1 | 152 (69 %) |
| D | 8 | 27 | 1 | 163 (74 %) |
| G | 7 | 28 | 1 | 160 (72 %) |

**Band** (three classic runs, three pairs): balance 2, material 21. Pair verdicts:
A–D 11/10 material right/left, A–G 10/8, D–G 10/10. Two runs of the same skill differ
"materially" on ~20 of 36 questions with no direction — the pairwise judge is noisy at
this level; the must-item rate is the steadier signal (spread 5 points).

Shared misses (all three runs miss the item), ~40 items, in four groups:

1. **Fully superseded documents are out of reach.** The v2.0 complaints policy and the
   January rate schedule are retired (their successors supersede them in full), so their
   content is history only. Q10 is `wrong` in all three runs (the earlier rate is in the
   retired schedule's history table; every answer says the corpus lacks it); Q07, Q09, Q13
   miss the v2.0 tables; Q11 misses the notification channels. The agent never calls
   `document-history` / `pattern10 --includeHistory` because nothing tells it a superseded
   version exists. Retirement out of vector search is by design: the fix is a graph route to
   history (supersession, document history, valid time), with vector output at most
   *signalling* that a predecessor exists — not retired text in search results.
   Superseded-but-not-retired KYC v1 documents stay current, and Q25 applies them in D and G
   (a must-not hit).
2. **Document identifiers not cited** (Q03, Q04, Q06, Q10): answers cite file names; these
   must items are too strict and move to Optional before the next grading.
3. **Broad multi-document questions lose specifics** (Q16, Q18, Q22, Q23, Q24): the shape is
   right, thresholds/timings/roles are dropped.
4. **Single facts** (Q32, Q35, Q36).

## brain01

| Run | Q01–Q14 must found (117) | Q01–Q14 correct | Q01–Q14 must-not hits | Q15–Q29 must found (75) | Q15–Q29 correct | Q15–Q29 must-not hits |
|---|---|---|---|---|---|---|
| A | 64 (55 %) | 2 | 5 | — | — | — |
| D | 68 (58 %) | 0 | 10 | — | — | — |
| G1 | 67 (57 %) | 1 | 9 | 53 (71 %) | 4 | 3 |
| G2 | 68 (58 %) | 0 | 5 | 50 (67 %) | 2 | 4 |

**Band** (four classic runs, six pairs; pairs with A or D cover Q01–Q14 only): balance 4,
material 19. G1–G2 on 29 questions: 11/8 material left/right, 10 minor or equal.

Wrong grades: A Q11 only. The four runs are within 4 must items of each other on Q01–Q14.

Failure groups:

1. **Wrong facts from the graph, repeated by every run.** Must-not hits shared by all four
   runs (Q03, Q04) and by three (Q01, Q05) state what the extracted graph says (a wrong
   relationship type, a wrong role, an event filed under the wrong category). Reasoning
   cannot fix these; they are extraction-wave material.
2. **Twin entities.** Q19 (both G runs) lists two nodes of one organisation separately; Q11
   misses one twin. Same-as curation or a twin-aware ranking.
3. **Enumeration coverage.** "Every year" and "list all" questions (Q01, Q05, Q11, Q12, Q15,
   Q20) miss whole years or members: `survey` + `vector-text` return a sample, not the set.
4. **Route not taken.** Q15 (LOCATION connected to PERSON) scores 3/9 and 2/9: neither run
   used `pattern8` (entity-resolve → entity-context → vector-text instead). Q16, the same
   shape on another class, used `pattern8` three times and scored 7/7 and 6/7. Q17–Q19 used
   `pattern9` in both runs.
5. **Tier too strict.** Q11 has 22 must items; it becomes "at least 20" plus the five most
   frequent, the rest Optional.

## What this sets for phase 2

- Gate measure: must-item rate per vault and per question group, with the bands above
  (banking 69–74 %; brain01 Q01–Q14 55–58 %, Q15–Q29 67–71 %). The pairwise band is wide;
  re-judge one pair with `--model claude-sonnet-5-5` before the first variant gate to see
  whether the width is the judge or the answers.
- Candidate slice targets, in evidence order: a graph route to superseded history
  (banking B-section), enumeration completeness (brain01 group 3), choosing `pattern8` for
  connected-to questions (Q15), twin handling (Q11, Q19). Extraction errors (brain01 group 1)
  wait for the extraction wave.
- Tier edits before the next grading: document-id items to Optional (banking); Q11 trimmed
  (brain01).

## Miss records (P2-0)

`artmind traces misses --run <id>` (no LLM call; records in each vault's private `logs/misses/`).

| Run | Turns | Turns with misses | vocabulary | reformulation | identity | scope | structure | answer | user-stated |
|---|---|---|---|---|---|---|---|---|---|
| G (banking) | 36 | 29 | 32 | 0 | 0 | 1 (widened) | 0 | 65 | 0 |
| G1 (brain01) | 29 | 28 | 27 | 0 | 17 (15 low score, 2 twins) | 0 | 0 | 84 | 0 |

`answer` equals each run's missed must items plus must-not hits from the grades. Reformulation is 0: the
classic agent's first search already returned a document it cited. User-stated is 0 by construction for
benchmark turns (one fresh agent per question, no feedback). Answers carry no `### Sources` section; citations
are matched inline by file stem or title.
