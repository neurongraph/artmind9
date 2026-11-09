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
   record questions to them. One-question check on banking Q32 (run `6ece7ab9`, restarted console, Sonnet):
   the agent now reaches the tables (`db schema` → `db sql`) and names the customer and complaint; graded
   partial with 2 must items missed (same as every baseline: A, D, G each missed 2), judged materially better
   than D and G and equal to A. Q32 is no longer worse than all baselines. A full H2 run is still the measure.
3. Decide whether the gate's lost-must / worse-than-all rows need a noise allowance (they fail every baseline
   run).

## P2 batch built (P2-2 … P2-5), 2026-11-09

Plan `docs/superpowers/plans/2026-11-09-p2-batch.md`; built on top of P2-1 and measured as one batch (parent spec
§4.3 revision). Full suite 6,654 passed. Live checks in banking_corpus through a temporary `serve`, and
brain01 (counts and key names only):

- `next` signposts on empty / thin / cut results (`artmind/hints.py`, shell-safe); `artmind query db …` names
  `artmind db …`.
- Supersession direction from document metadata, then `_valid_from` (`artmind/supersession_direction.py`). Root
  cause of the banking KYC inversion: the adjudicator compared `valid_from`, which Document nodes never carry.
  Repaired live with the new `ingest unsupersede` (dry runs first); `policy_customer_identification_v2.md` is
  current, v1 retired; restored passages re-embedded. The vault's curation files changed (uncommitted, for the
  user).
- Retrieval rows show `doc.replaced` for a document that replaced an older version; `next` → `document-history`.
- **Dates were null in every retrieval payload** (vector-text, survey, pattern10, entity-context source
  documents): queries read `valid_from`, nodes store `_valid_from`. Fixed; graph `--asOf` and timeline windows
  now filter. The baseline runs and H answered with no document dates. Known gap left: passages and chunks
  carry no date, so `vector-text --asOf` still cannot narrow.
- `entity-resolve`: first person → narrator; same-class twins grouped.
- `pattern7` / `pattern8` / `entity-listing`: `complete` everywhere, `--all` (pattern8 in a total order, cap
  2000 with a warning).
- `survey --by year|month`: every period with a hit, fair cap, `keyword_hits` vs `ranked`.
- `entity-context`: positional question ranks by default; `hub` / `cut` say what a cut hides (brain01 narrator:
  25 of 8,290 connections, 5 of 2,011 chunks, 18 properties).
- Skill variant `artmind-query-orient`: 440 lines (from 469 after P2-1's fixes; classic 550).

Next: restart both admin consoles; the user runs one benchmark per vault with Skill variant `orient` (run `I`),
judged and graded with Sonnet against the same baseline runs and floors.

## Batch run I (orient + P2-2 … P2-5), judged and graded with Sonnet

Runs: banking `7fe027c0` (36/36), brain01 `343e5203` (29/29); consoles restarted after the last code commit;
every turn contract `orient`. Two brain01 answers came back ungraded on two attempts: the grader wrote bracketed
reasoning (`[1] found`) before its JSON and `json_repair` returned a list. Fixed in `99414bda` (regression test
fails on the old code); all 65 answers graded.

### Must items

| Vault / group | Floor | Baselines | H | **I** |
|---|---|---|---|---|
| banking (214) | 67 % | 67–71 % | 70 % | **152 (71 %)** |
| brain01 Q01–Q14 (101) | 55 % | 55–61 % | 63 % | **66 (65 %)** |
| brain01 Q15–Q29 (75) | 76 % | 76–79 % | 77 % | **61 (81 %)** |

I is at the top of the banking range and above every baseline run in both brain01 groups. Correct / partial /
wrong: banking 8/27/1 (baselines 7–8 correct); brain01 Q01–Q14 1/12/1, Q15–Q29 7/8/0 (baselines at most 6
correct on Q15–Q29). Must-not hits brain01: Q01–Q14 2 (baselines 5–8), Q15–Q29 1 (baselines 2–3).

### Gate rows

| Row | banking | brain01 |
|---|---|---|
| balance within band (3 / 9) | ok: 0, −5, −1 (H: +1, 0, +1) | ok: 0, −4, +1, −5 |
| lost must items | 6 over 5 q (Q02, Q08, Q14, Q25×2, Q32) — baselines lose 9–14 against each other | 9 over 8 q (Q02, Q03, Q05, Q14×2, Q16, Q20, Q25, Q28) — baselines 0–7 |
| worse than all | Q14 (H: 4 questions) — baselines 3–6 | Q01, Q14, Q28 — baselines 1–2 |

On the two noise rows I sits inside the baseline range in banking (fewer losses and fewer worse-than-all
questions than any baseline run, and than H). In brain01 it loses more must items than the baselines lose
against each other (9 vs 0–7) and has one more worse-than-all question than the worst baseline, while its
must-item rate is above every baseline: it finds more items in total but not always the same ones.

### Superseded history (banking, the P2-2b target)

Grade as `<c|p|w><missed>/<must-not hits>` per run A, D, G, H, I:

| q | A | D | G | H | I | I's path |
|---|---|---|---|---|---|---|
| Q07 | p3/2 | p3/2 | w5/1 | p4/1 | p3/1 | orient → survey → pattern10 |
| Q09 | p3 | p4 | p2 | p3 | p2 | … → vector-text → document-history |
| Q10 | p2 | p2 | p2 | c0 | **c0** | survey → chunks → pattern10 |
| Q11 | p3 | p3 | p2 | p3 | p3 | orient → survey |
| Q13 | p4 | p1 | p1 | p3 | **c0** | orient ×2 → pattern10 |
| Q25 | p1 | c0 | p2/1 | p1 | p3 | orient → survey → chunks |

Q10 (wrong in every baseline in note 20, before tier edits) and Q13 are now correct; the agent reached history
through `pattern10` and `document-history`. **Q25 regressed** (3 must items missed): with KYC v1 now correctly
retired, the answer did not cite the controlling CID-POL-001 v2.0 / ACCT-OPEN-SOP-001 v3.0 versions or the
document-authority register, and said it did not check whether SOP v3.0 supersedes v2.1. `ACCT-OPEN-SOP` v2.1 →
v3.0 is a supersession detection never ran for (signal 2 only, study note 22 P2-2b mapping) — a data gap, not a
tool gap. Q32 (structured tables) reached `db bridge → db schema → db sql` again.

### Behaviour and cost

Median commands per turn unchanged (banking 4, brain01 5). Median latency banking 28.4 s (H 25.3), brain01
29.7 s (H 27.5); run cost banking $4.40 (H $4.55), brain01 $4.04 (H $3.85). Failed spans banking 2 (H 6),
brain01 3 (H 2). Miss records: banking 36 turns, 32 with misses, scope 0 (H 3), reformulation 1; brain01 29
turns, 25 with misses, identity 21 (17 low score, 4 twins).

## Reading for the user

`traces compare` flags (material vs some baseline, worse than all, or a lost must item), ids only:
- banking: Q01, Q02, Q03, Q04, Q05, Q06, Q07, Q08, Q11, Q13, Q14, Q15, Q18, Q19, Q20, Q21, Q22, Q24, Q25, Q26,
  Q32, Q33, Q36 — first **Q14** (worse than all) and **Q25** (lost must items).
- brain01: Q01, Q02, Q03, Q04, Q05, Q06, Q07, Q08, Q09, Q10, Q11, Q12, Q14, Q15, Q16, Q18, Q20, Q21, Q25, Q27,
  Q28 — first **Q01, Q14, Q28** (worse than all).

## Supersession data repaired after run I (banking_corpus)

`ingest detect-supersession --dryRun` over every banking domain proposed two pairs, both matching the documents'
own `Supersedes` / `Superseded By` / `Status` rows; applied:
- `sop_account_opening_v3.md` supersedes `sop_account_opening.md` (v2.1, effective 2026-03-01) — the Q25 gap.
- `interest_rate_schedule_2026_03.md` supersedes `interest_rate_schedule_2026_02.md`.

One wrong old adjudicator edge found and removed: `interest_rate_schedule_2026_02.md` "superseded"
`welcome_pack.md` (Active, `Superseded By: None`), which had retired the welcome pack. `decide` now refuses a
pair where one document names no successor and the other names a different one (`0b7e24ab`, test fails on the
old code); `ingest unsupersede` restored the welcome pack (dry run first); 23 passages and 24 summaries
re-embedded. Audit: all 5 remaining banking SUPERSEDES edges agree with `decide`. Vault curation files changed
(uncommitted, for the user).

Q25 rerun alone (`b1a99a90`, orient variant, Sonnet): partial, 2 must items missed (run I: 3), no must-not hit;
judged materially better than A and about equal to D and G. It still omits the version numbers and the
DOC-AUTH-001 policy-over-SOP rule: the agent's path (`orient → survey → orient → chunks`) did not open the
authority register — a depth / retrieval issue, not a data one.
