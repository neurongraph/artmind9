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

## Every baseline run under Sonnet (the gate model from here on)

The narrower band on the one pair above made Sonnet the gate model (spec §3.4). Every baseline run in both vaults
was re-judged pairwise and re-graded with `claude-sonnet-5-5` (via `ibm_ica`), same tiers. **These replace the
Haiku floors above.**

| Vault / group | Must items | A | D | G / G1 | G2 |
|---|---|---|---|---|---|
| banking (36) | 214 | 143 (67 %) | 151 (71 %) | 150 (70 %) | — |
| brain01 Q01–Q14 | 101 | 56 (55 %) | 62 (61 %) | 61 (60 %) | 59 (58 %) |
| brain01 Q15–Q29 | 75 | — | — | 59 (79 %) | 57 (76 %) |

Correct / partial / wrong: banking A 7/29/0, D 8/28/0, G 8/27/1; brain01 A 2/11/1, D 1/13/0, G1 8/20/1, G2 5/23/1.
Must-not hits Q01–Q14: A 5, D 8, G1 6, G2 5; Q15–Q29: G1 2, G2 3.

Pairwise (balance = left-better minus right-better over every judged question, minor included; `traces/gate.py`):

| Pair | Balance | Material |
|---|---|---|
| banking A–D | −3 | 16 |
| banking A–G | +1 | 14 |
| banking D–G | 0 | 14 |
| brain01 A–D | −4 | 7 |
| brain01 A–G1 | −3 | 10 |
| brain01 A–G2 | 0 | 8 |
| brain01 D–G1 | −9 | 8 |
| brain01 D–G2 | −2 | 9 |
| brain01 G1–G2 (29 q) | +1 | 12 |

**Bands (Sonnet):** banking balance 3, material 16 (Haiku: 2, 21); brain01 balance 9, material 12 (Haiku: 4, 19).
Material narrows in both vaults. brain01's balance widens only because of one pair, D–G1 on Q01–Q14, where Sonnet
prefers G1 on nine more questions than D; the other five pairs are within 4.

**Gate floors for the orient variant (Sonnet, lowest baseline run):** banking 67 %; brain01 Q01–Q14 55 %,
Q15–Q29 76 %. Judge and grade the variant with `--model claude-sonnet-5-5`.

## Still to do before the variant gate

1. ~~Gate model~~: Sonnet, all baseline runs re-judged and re-graded (above).
2. Restart the admin consoles; the user runs one benchmark per vault with Skill variant `orient`
   (banking_corpus first).
3. `traces compare --baseline <all> --variant <id> --questions <file> --judge --grade --model claude-sonnet-5-5` and
   `traces misses --run <id>`.
