# 22 — P2-1 `query orient`: built, baselines re-graded (2026-11-09)

Spec `docs/superpowers/specs/2026-11-09-p2-1-orient-design.md`, plan
`docs/superpowers/plans/2026-11-09-p2-1-orient.md`. Counts, run ids and question ids only (README privacy rule).

## Built

`artmind query orient` (peel 1: corpus + one card per domain) and `orient --domain d` (peel 2: classes with
schema definitions, measured types and dated share; measured relation shapes; structured tables; views; topics;
insights; superseded documents). Catalogue parts `shapes`, `types`, `versions`, content-dated `time`
(format 4); `artmind/domain_config.py` replaces the catalogue's planner import. Skill variant
`artmind-query-orient` (465 lines; classic 550), linked into both vaults. Full suite 6,506 passed.

Live sizes (compact characters): banking peel 1 8,458 (10 cards, no collapse); `--domain banking.policy`
12,241 with 19/19 definitions; brain01 peel 1 1,093; `--domain personal_journal` ~10,300 with all 10 schema
definitions. Proxied and in-process payloads are identical.

## Baselines re-graded under the 2026-11-09 tiers

Judge and grader unchanged: `claude-haiku-5-5` via `ibm_ica` (the same model that graded note 20), so no model
re-grade was needed; only the tier edits (banking: citation-only items to Optional; brain01 Q11 trimmed) required
it.

| Vault / group | Must items | A | D | G / G1 | G2 |
|---|---|---|---|---|---|
| banking (36) | 214 (was 221) | 151 (71 %) | 162 (76 %) | 157 (73 %) | — |
| brain01 Q01–Q14 | 101 (was 117) | 64 (63 %) | 65 (64 %) | 65 (64 %) | 62 (61 %) |
| brain01 Q15–Q29 | 75 | — | — | 53 (71 %) | 50 (67 %) |

Correct / partial / wrong: banking A 6/29/1, D 8/28/0, G 9/26/1; brain01 A 2/11/1, D 0/14/0, G1 5/24/0,
G2 2/27/0. Must-not hits on Q01–Q14: A 5, D 9, G1 8, G2 5; on Q15–Q29: G1 3, G2 4.

**Gate floors for the orient variant** (spec §3.4, lowest baseline run): banking 71 %; brain01 Q01–Q14 61 %,
Q15–Q29 67 %.

## Sonnet re-judge of one baseline pair (spec §3.4)

banking D `15837ded` vs G `f03056a8`, 36 questions, same tiers:

| Judge | Pair balance | Materially different | D must found | G must found | D c/p/w | G c/p/w |
|---|---|---|---|---|---|---|
| `claude-haiku-5-5` | 4 | 19 | 162 (76 %) | 157 (73 %) | 8/28/0 | 9/26/1 |
| `claude-sonnet-5-5` | 0 | 14 | 151 (71 %) | 150 (70 %) | 8/28/0 | 8/27/1 |

Sonnet calls 5 fewer questions material on this pair and sees no direction between two runs of the same skill;
it also grades stricter (about 3–5 points lower) and the two runs' rates sit closer (1 point against 3). By the
spec rule (§3.4: "if the band narrows, every gate uses that model") the gate model becomes Sonnet, which means
re-judging and re-grading every baseline run in both vaults with it before the first variant gate; the Haiku
floors above then stop being the floors.

## Still to do before the variant gate

1. Decide on the gate model; if Sonnet, re-judge and re-grade all baseline runs with `--model claude-sonnet-5-5`
   in both vaults and replace the floors above.
2. Restart the admin consoles; the user runs one benchmark per vault with Skill variant `orient`
   (banking_corpus first).
3. `traces compare --baseline <all> --variant <id> --questions <file> --judge --grade [--model ...]` and
   `traces misses --run <id>`.
