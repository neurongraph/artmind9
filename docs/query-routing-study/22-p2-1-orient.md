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

## Variant run H (skill variant `orient`), judged and graded with Sonnet

Runs: banking `34d38b0b` (36/36), brain01 `42bf7363` (29/29). Admin consoles restarted after the last code
commit; every turn recorded contract `orient`.

### Must items (primary gate)

| Vault / group | Floor (lowest baseline) | Baseline range | H |
|---|---|---|---|
| banking (214) | 67 % | 67–71 % | 149 (70 %) |
| brain01 Q01–Q14 (101) | 55 % | 55–61 % | 64 (63 %) |
| brain01 Q15–Q29 (75) | 76 % | 76–79 % | 58 (77 %) |

H is at or above every floor; on brain01 Q01–Q14 it is above every baseline run. Correct / partial / wrong:
banking 9/27/0 (baselines 7–8 correct); brain01 Q01–Q14 2/12/0, Q15–Q29 6/8/1. Must-not hits: banking —;
brain01 Q01–Q14 4 (baselines 5–8), Q15–Q29 1 (baselines 2–3).

### Gate rows (`traces compare`)

| Row | banking | brain01 |
|---|---|---|
| balance within band (3 / 9) | ok: +1, 0, +1 | ok: −2, −6, −3, −8 (H better on balance against every run) |
| no must item lost that every baseline found | **fail**: 8 items over 6 q (Q02, Q13, Q14, Q16, Q19, Q32×3) | **fail**: 4 items over 4 q (Q02, Q03, Q15, Q16) |
| no question materially worse than all baselines | **fail**: Q01, Q15, Q18, Q32 | ok: none |

**The two failing rows fail every baseline run too.** Applied to each baseline run against the other baselines
only (H left out): banking A loses 14 items / 9 q and is worse-than-all on 3 q, D 9 / 8 and 6, G 11 / 8 and 4;
brain01 A 5 / 4 and 1, D 0 / 0 and 2, G1 4 / 3 and 1, G2 7 / 6 and 2. H's banking 8 / 6 and 4, and brain01 4 / 4
and 0, sit inside those ranges. With three or four runs at ~70 % per item, "every other run found it" is common
noise, so these rows cannot separate a variant from a baseline as written. The spec's rule is that each
worse-than-all question needs a dossier or the user's reading; the flagged lists below are for that.

### Behaviour

- Orient was the first call in 65 of 65 turns. banking: peel 1 then straight to retrieval in 29 of 36 turns,
  peel 2 in 7 (5 of them for two domains); brain01: peel 1 + peel 2 in 28 of 29.
- Orientation calls replaced: no `domains-overview`, `views list` or `db bridge`; `structural-metadata` twice in
  banking.
- Payload sizes: banking peel 1 8,428 characters, peel 2 median 19,914 (two domains); brain01 peel 1 1,093,
  peel 2 10,273.
- Median commands per turn unchanged (banking 4, brain01 5); median latency banking 25.5 s (G 28.1 s), brain01
  27.7 s (G1 23.5 s).
- Miss records: banking 36 turns, 31 with misses, vocabulary 35, scope 3 (outside the first scope; G had 1),
  answer 1; brain01 29 turns, 23 with misses, vocabulary 23, identity 17 (12 low score, 5 twins; G1 17).

### What the banking losses share

Three of the four worse-than-all questions point at a specific cause, read from the grades (public corpus) and
the spans:

- **Q32** (who is the open-complaint vulnerable customer): every baseline run used the structured store
  (`db bridge` → `db schema` → `db sql`); H never touched it and said no customer can be named. The variant
  dropped `db bridge`, and peel 1 shows `structured` on every banking card (the tables sit at the `banking`
  root and every child inherits them), so it no longer tells which question needs the tables. H still used
  the store for Q33–Q36, whose wording names records.
- **Q01, Q15, Q18** (multi-document procedures): H searched the same domains as the baselines but dropped
  specifics (thresholds, SOP ids, retention periods), the "depth for broad multi-document questions" gap of
  study note 20, not an orientation change. Q15's grade also notes H asserting no rule blocks closure.

## Reading for the user

Flagged by `traces compare` (material vs some baseline, worse than all, or a lost must item), ids only:

- banking: Q01, Q02, Q03, Q04, Q05, Q06, Q07, Q11, Q13, Q14, Q15, Q16, Q18, Q19, Q20, Q21, Q22, Q24, Q26, Q32,
  Q33, Q36 — first Q32, Q01, Q15, Q18 (worse than all).
- brain01: Q02, Q03, Q05, Q06, Q07, Q08, Q09, Q10, Q11, Q12, Q15, Q16, Q18, Q21, Q27, Q28 — first Q02, Q03, Q15,
  Q16 (lost must items).

## Next

1. The user reads the flagged answers (first the worse-than-all and lost-must questions above).
2. ~~Structured-store signal on cards~~: done after H (commits a15350c9 and the skill line): the `banking` root
   card lists its 5 tables (rows, bridge classes), each child says `tables_at: banking`, and the skill sends
   record questions to them. Needs a second variant run (H2) to measure.
3. Decide whether the gate's lost-must / worse-than-all rows need a noise allowance (they fail every baseline
   run).
