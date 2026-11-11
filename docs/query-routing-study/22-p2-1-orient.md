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

## Topic pages built from benchmark questions (banking_corpus, found 2026-11-10)

Ten banking topic pages (`_Topics/cpd-r051` … `cpd-r060`, built 2026-11-01) were built from the survey questions
in `benchmarking/retrieval_labels.yaml`. Six of those questions are, word for word, benchmark questions:
cpd-r051 Q01, cpd-r052 Q25, cpd-r053 Q16, cpd-r054 Q17, cpd-r055 Q20, cpd-r056 Q23. A page is never evidence
(no `:TopicPage` in any retrieval index), but `survey`'s `topics` block lists it as a lead, and a page written
for the exact question names its sources. Reads: baselines A, D, G and run H each opened the page for Q16, Q23
and Q25 (two of them also Q01's); run I opened Q17's and Q23's. Banking Q25's correct baseline answer (D) went
through cpd-r052.

Deleted the six with `artmind topic delete` (vault git keeps them); the topic index agrees (5 pages left).
cpd-r057 … r060 match no benchmark question and stay. Must-item rates without the six questions (30 questions,
174 must items, Sonnet, cached grades):

| Run | Must found |
|---|---|
| A `61cf661f` | 112 (64 %) |
| D `15837ded` | 118 (68 %) |
| G `f03056a8` | 120 (69 %) |
| I `7fe027c0` | 122 (70 %) |

The banking floor on the clean set is 64 % (was 67 % over 36). Run J should re-run the baselines with the pages
gone, or gate banking on the clean 30-question set. brain01 has no topic pages.

## Topic plans for breadth (2026-11-10, code at `5f80e4b8`)

Spec `2026-11-10-topic-breadth-design.md`, steps 1–6. Counts only; plans, pages and grades stay in the vaults.
"With plan" runs are not like-for-like baseline comparisons: the plan was built in the vault before the rerun.

| Measure (§11) | brain01 Q08 | banking Q15 |
|---|---|---|
| Gather, default grid, no LLM | 16 of 22 dated events (68 documents; target ≥ 19 not met) | 5 of 5 key documents (51 documents, 8 child-domain cells) |
| Plan run under the 24k output budget | 10 of 22 events in 37 of 68 rows (rest via `query page`) | 4 of 5 key documents; 5 of 5 over per-subject runs |
| Agent with the plan, Sonnet grade | partial, 3 of 7 must missed (run I 5; baselines 6–7) | partial, 3 of 6 missed (baselines 3–6) |
| Page text alone, Sonnet grade | 3 of 7 found | not graded (150k characters, above the 40k ceiling) |
| No plan: the offer | absent in 2 reruns (counts reported, no offer) | — |
| Build cost | 71 map + 3 reduce calls | 150 map + 41 reduce; refresh: 0 map, 40 reduce |

- Q08's gather misses are journal entries that do not use the subjects' words (§13 keyword undercount). Keyword
  counts depend on wording: one-word subjects matched 23 documents, question-style subjects nearly all of them.
- `query topics run` orders rows by rank across cells so a budget cut keeps every cell's top documents; that kept
  the account-closure SOP visible in Q15 (it was cut when rows were grouped by cell) and cost Q08 two events.
- Members (§6.4), brain01 Q05, no LLM: discovery from three anchors lists 94 candidates holding 6 of the 8
  expected places besides the anchor city; the gather covers 9 of 16 dated visits with the 5 discovered members
  and 13 of 16 with all 8 (target ≥ 14 not met; the 3 misses are follow-on days that do not name the place).
- The `:TopicPage` graph copy is dropped in both graphs (banking: 5 nodes); pages no longer reach agents.

Follow-up runs the same day (brain01, approved):

| Measure | Result |
|---|---|
| Members plan, Q05 (19 members: 16 kept from 94 candidates, 3 added) | gather 16 of 16 dated visits (108 documents, 170 LLM calls); plan run first page 8 of 16 in 34 of 108 rows |
| Agent with the members plan, Q05 | **correct**, 0 of 9 must missed, no must-not (baselines miss 3–7, each with a must-not) |
| Discovery, Q11 (4 anchors, ORGANIZATION) | 60 candidates: 2 of the 3 non-anchor must clients, 7 of 20 optional |
| Discovery, Q16 (1 anchor, PERSON) | 171 candidates: 6 of 6 must people, 9 of 10 optional |
| Freshness, a synthetic test day added for Q08 | `stale`, `new_documents: 1`, one grown cell; refresh 2 map + 3 reduce calls (the vector leg ranked the day into the second subject's cell too), then `fresh` |

## Run J (2026-11-10): every enhancement since run I, judged and graded with Sonnet

Code at `fdf3f842` (orient variant with the turn order and "Plan the parts", `outside_scope` with passages,
survey `coverage`, topic plans and their surfaces, the console endpoint fix; data: banking supersession repaired,
leaked banking pages removed, brain01 curation and one alias fixed). Consoles restarted with a clean environment;
contract `orient`. Topic plans built during the topic-breadth measurements (brain01 Q05, Q08; banking Q15) were left
in; no J turn ran `query topics run` (`topic_plan_turns` empty). Banking is compared on all 36 questions; baseline
answers to Q01, Q16, Q17, Q20, Q23, Q25 had read leaked pages.

Runs: banking `27ad936d` (36/36, median 29.5 s, $5.02); brain01 `94d5e851` (29/29, median 31.9 s, $4.35).

| Vault / group | Baselines | I | **J** |
|---|---|---|---|
| banking (214 must) | 143–151 (67–71 %) | 152 (71 %) | **144 (67 %)** |
| brain01 Q01–Q14 (101) | 56–62 (55–61 %) | 66 (65 %) | **75 (74 %)** |
| brain01 Q15–Q29 (75) | 57–59 (76–79 %) | 61 (81 %) | **56 (75 %)** |
| brain01 all (176) | 116–120 (66–68 %) | 127 (72 %) | **131 (74 %)** |

Correct / partial / wrong: banking 7/29/0 (I 8/27/1); brain01 11/18/0 (I 8/20/1). Must-not hits: banking 4 (I 4),
brain01 4 (I 3).

Against run I, per question (must items missed):
- banking: better on 8 (Q03, Q14, Q18, Q19, Q24, Q25, Q32, Q33: the backlog fixes held), worse on 10 (largest:
  Q05 1→5, Q22 1→4, Q36 1→4, Q21 2→4, Q12 0→2, Q13 0→2).
- brain01: better on 8 (Q04, Q05 4→0, Q06, Q08 5→3, Q11, Q14, Q25, Q28), worse on 6 (Q01, Q10, Q15 4→6, Q16, Q20
  1→4, Q27).

Gate rows: banking balance +8, +4, +3 against A, D, G (band 3: out of band, the baselines better on balance);
brain01 −2, −2, −7, −10 (within band, J better on balance against every run). Lost-must and worse-than-all fail in
both, as they do for every baseline run (worse than all: banking Q05, Q06, Q22, Q23, Q36; brain01 Q01, Q09, Q20).

Reading: brain01 J is the best run so far. banking J sits at the bottom of the baseline range and 8 must items below
I: the questions fixed in the backlog pass improved, while a different set regressed. Q36 used `text2sql` where I
used `db sql` and got the aggregation wrong; Q05, Q22, Q21 split into several narrower surveys and dropped
specifics. One run per vault; I and every baseline differ from each other by about 4 points, so a second banking run
is needed before calling the regression real.

### Banking J2 (same code as J)

Run `182a3564` (36/36, started by the user), Sonnet: **163 / 214 must items (76 %)**, 11/25/0 correct / partial /
wrong; balance −4, −4, −2 against A, D, G (within band, J2 better on balance against every run). One answer came
back ungraded on the first pass and was graded on a retry.

| Run | Must found | Correct |
|---|---|---|
| baselines A / D / G | 143 / 151 / 150 | 7 / 8 / 8 |
| I | 152 (71 %) | 8 |
| J | 144 (67 %) | 7 |
| **J2** | **163 (76 %)** | **11** |

J and J2 ran the same code and differ by 19 must items (9 points), more than the spread across the three baselines
(8 items). The mean of J and J2 (153.5) is level with I. Per question against I:
- worse in both J and J2: Q05 (1 → 5, 3), Q09 (2 → 3, 3), Q13 (0 → 2, 1), Q22 (1 → 4, 3), Q36 (1 → 4, 3);
- better in both: Q03, Q24 (5 → 4, 1), Q25 (3 → 1, 1), Q32 (3 → 1, 2), Q33 (4 → 1, 1), the backlog-pass questions;
- J's other regressions (Q12, Q16, Q21, Q23, Q34) recovered in J2: run-to-run noise.

Reading: banking did not regress overall; one run of 36 questions moves by up to 9 points with unchanged code, so a
variant needs at least two runs before a gate reading means anything (work plan item H, "gate noise allowance").
Five questions are worse in both runs and go back to the backlog (Q05, Q09, Q13, Q22, Q36; Q36 is a `text2sql`
aggregation error, a case for the structured-table enhancement).

## text2sql delegated to sql-browser (2026-11-10)

sql-browser `94643f4` adds an OpenAI-compatible provider (artmind's ibm_ica endpoint and model through
`SQL_BROWSER_*` environment variables); artmind `46ddb05b` delegates `text2sql` to `sql-browser query --json`
(`ARTMIND_TEXT2SQL_ENGINE=auto`, a per-call scoped DuckDB file, merged `domains/metadata/<domain>.meta.yaml`,
built-in fallback), and `3bf2d074` adds `artmind db metadata init|validate|regenerate`. banking's metadata file was
scaffolded with five joins (complaints, CSAT and vulnerable customers to customers; CSAT and complaints to agents)
and validates. Full suite 6,761 passed.

Live, direct calls (banking): the open-complaint CSAT question now selects `status = 'Open'` (2 customers, 1 survey);
the built-in path had treated every non-Resolved status as open (13 customers). The largest Account Takeover payout
and the Fee Dispute average agree between engines.

Q32–Q36 rerun alone (`8aa968a7`, console restarted): Q32 and Q35 ran `text2sql` through sql-browser (2 calls each),
and their remaining misses are policy items, not SQL. Q33, Q34 and Q36 wrote their own SQL with `db sql`, so the new
engine was not used; Q36 is still partial with 3 missed (the open group and the non-open comparison), the same as J2.
Must items missed (I / J / J2 / rerun): Q32 3/1/2/2, Q33 4/1/1/1, Q34 1/2/1/2, Q35 1/1/1/1, Q36 1/4/3/3.

After the text2sql-first skill line (`a1cefb88`), Q32–Q36 again (`5e3b41ff`): Q33, Q34, Q35 and Q36 now ask
`text2sql` (all answered by sql-browser); Q32 still wrote `db sql`. Must items missed (I / J / J2 / before the line /
after): Q32 3/1/2/2/2, Q33 4/1/1/1/1, Q34 1/2/1/2/4, Q35 1/1/1/1/**0** (correct), Q36 1/4/3/3/3. Q34's answer gave the
average without the customers and survey rows behind it; Q36 compared open complaints against everyone else
(including customers with no complaint) instead of against non-open complaints, and did not name the two open
customers. One run each; the SQL layer now does what it is asked, and the remaining misses are what the agent asks and
reports.

## The mart, first build on brain01 (2026-11-11)

Built per `docs/superpowers/plans/2026-11-10-mart.md` (tasks 1–14). brain01 mart: 2,728 happenings, 6,180 mentions,
about 130 trips and 390 life events. Enrichment: rules decide 810 of 1,085 people and 14 type values; `location`
seeds 317 places (only when the text names a known city); the LLM (Haiku via ibm_ica, about 1,000 calls in all,
8 at a time) and 34 user records from the vault owner place the most-mentioned places. 2,048 of 2,728 happenings still
have no home/away, mostly because they link to no place.

Wholes scored against the Q09 and Q15 expected answers (hand-written tiers used as labels):
- Q09 life events: 6 of 6 deaths found, each anchored on the right person, rites included where the tier lists them.
- Q15 trips with one family member: 6 of 9 trips found, 4 with exact start and end days. Of the 25 trips the mart links
  to that person, 6 match a labelled trip (the others are not all wrong: the tier lists trips outside the home city
  only). The three misses have no place with a city and no mention of the person on those days.

Changes made while measuring, each generic and set per domain in the declaration: trips at day level; seeds set a city
only from a known city name; type families by name keyword; away by place type; trip companions from participant
names and collective names; a trip run splits where companions change.

### Target-question reruns through the agent (2026-11-11)

Ten brain01 questions the mart should help (Q02, Q05, Q09, Q11, Q15, Q16, Q17, Q20, Q25, Q26), graded with Sonnet 5.5
against tiered answers. Correct / must items missed in all: run I 0 / 22, run J 2 / 22, rerun 1 (`32284021`) 3 / 23,
rerun 2 (`056b22f9`) 3 / 19. One run each; run-to-run variance is about this size.

Rerun 1 used the mart on 5 turns, but its SQL ignored the wholes: trips were rebuilt through the bridges with a
`city <> home` filter (dropping places with no city), and deaths were looked for in happening types, which never hold
them. The generated metadata named only joins. Fixed in `db9d6635` and `7ff72aea`: every mart table has its grain,
the wholes columns are described (anchor, participants with `list_contains`, places, part names matched with `ILIKE`),
whole rows carry `part_names` and `families`, a rule says a whole row is already the answer, and `dim_person.aliases`
is described so a person whose display name is a relation is matched and named by a merged alias. Live: trips with
the family member 25 rows straight from the trip table; family deaths 0 → 19 rows.

Rerun 2 (mart on 5 turns): Q15 missed 7 → 5 (two more trips named), Q02 3 → 2, Q11 2 → 1; Q09 still misses 2 — one
rite links to another family member, not the deceased, and a rite's place is not in the answer. Q15's remaining misses
are trips whose days link to no place with a city or do not mention the family member (data, not SQL).

Then the trip builder and its data (`d546eb7a`, `c83735a0`, vault curation): the day just before or after a trip
joins it when it holds an outing and shares a companion (one day each side, never chained); trip rows carry
`place_names` and `cities`, and the metadata says never to filter trips by city (an agent's `city <> home` dropped
every trip whose places have no city). Curation: one nickname mapped to its person, 18 user place records (generic
place names merged across notes had their cities cleared), two away place types added, two notes re-extracted.
Letting a city-less day never split a run was tried and reverted: it merged separate trips (recall 9/9, exact 1/9).
Kept: Q15 trips 9 of 9 found (was 6), 3 exact, 6 a day off; 30 trips link the family member, 9 match a label.

Reruns 3 (`c4017940`) and 4 (`c33485b4`): correct / missed 2 / 22 and 3 / 19. Q15 4 and 5 missed: every trip is
now in the answer, but trips whose days link to no named place come without a destination — the next gap is
places mentioned in a day's note but not linked to its happenings (extraction, plan item G). Q09 unchanged at 2.

## Run K (2026-11-11): every enhancement to date, two runs per vault, judged and graded with Sonnet

Code at `e2220f6d` (topic plans, `text2sql` through sql-browser, the brain01 mart with wholes and its metadata;
brain01 curation from the mart reruns). Consoles restarted with a clean environment; contract `orient`. Banking has
no mart built, so banking measures only the sql-browser delegation and noise.

| Vault / group | Floor | Baselines | I | J | J2 | **K1** | **K2** |
|---|---|---|---|---|---|---|---|
| banking (214) | 67 % | 143–151 | 152 | 144 | 163 | **152 (71 %)** | **145 (68 %)** |
| brain01 Q01–Q14 (101) | 55 % | 56–62 | 66 | 75 | — | **68 (67 %)** | **77 (76 %)** |
| brain01 Q15–Q29 (75) | 76 % | 57–59 | 61 | 56 | — | **54 (72 %)** | **50 (67 %)** |
| brain01 all (176) | — | 116–120 | 127 | 131 | — | **122 (69 %)** | **127 (72 %)** |

Runs: banking `376d9499`, `421dc021`; brain01 `f7f7b46d`, `9d7ad522`. Correct / partial / wrong: banking 10/26/0 and
9/27/0; brain01 9/20/0 and 10/18/1. Must-not hits: banking 2 and 4; brain01 4 and 3.

Banking: both runs above the floor and inside the I–J2 spread; Q05, Q09, Q13, Q22, Q36 (worse in J and J2) are not
fixed (Q36 3 missed in both). Brain01 Q01–Q14: K2 is the best run so far. Brain01 Q15–Q29: **below the floor in both
runs**. Worse than J in both K runs: Q18 (0 → 2, 5), Q24 (1 → 3, 3), Q21 (0 → 1, 2); Q15 stays at 6 (single-question
reruns 4–5).

Cause (traces): the agent now goes to `text2sql` first on Q15, Q17, Q18, Q24 (J used survey, patterns or
`text2cypher`) and takes the mart result as the whole answer. Q18 ranked workplaces by the number of mart rows naming
them; Q24 counted happenings whose name matches an activity (half the sessions); Q15 listed all trips with the
person (30, the label has 9) and the grader called the extras doubtful. No turn checked a mart count or ranking
against a document search. The mart's rows are a lower bound on what the notes say, and nothing on the surface says
so.

### Run L (2026-11-11): the mart lower-bound fix, brain01 Q15–Q29 twice

Fix `7f60f730`: a `text2sql` result read from `<d>__mart_*` carries a warning (mart rows hold only what extraction
found; a count, ranking or list from them is a lower bound) and a `next` that surveys the question; the orient skill
says to answer from both. Runs `c160f6ed`, `224f7daf` (15 questions each), Sonnet, same tiers.

| Run | I | J | K1 | K2 | **L1** | **L2** |
|---|---|---|---|---|---|---|
| Q15–Q29 must found (75; floor 76 %) | 61 | 56 | 54 | 50 | **59 (79 %)** | **62 (83 %)** |

Correct: L1 8, L2 7 of 15 (K 7 and 8 on these questions). Per question, missed (J, K1, K2 → L1, L2): Q15 6, 6, 6 →
3, 2; Q18 0, 2, 5 → 1, 0; Q21 0, 1, 2 → 0, 0; Q24 1, 3, 3 → 2, 2; Q20 stays at 3–4 in every run. A survey followed
the mart answer on 4 of 7 `text2sql` turns in L1 and 3 of 5 in L2. Both runs above the floor; L2 is the best run on
this group so far. Q24's count is still short of J's in both runs.

## Run M (2026-11-11): the check-off list, two runs per vault

Skill `0af992d5`: before answering, the agent writes `Parts:` again with a status per line and ticks a part only
with its own evidence, every `next` run or rejected, no partial result relied on, mart counts checked against the
notes and nothing unread. Code otherwise as L (mart lower-bound fix). Runs: banking `9a027799`, `ca13f53b`; brain01
`c3ffa221`, `fc8862be`. Sonnet, same tiers and baselines.

| Vault / group | Floor | K1 | K2 | L1 | L2 | **M1** | **M2** |
|---|---|---|---|---|---|---|---|
| banking (214) | 67 % | 152 | 145 | — | — | **155 (72 %)** | **149 (70 %)** |
| brain01 Q01–Q14 (101) | 55 % | 68 | 77 | — | — | **78 (77 %)** | **67 (66 %)** |
| brain01 Q15–Q29 (75) | 76 % | 54 | 50 | 59 | 62 | **63 (84 %)** | **61 (81 %)** |
| brain01 all (176) | — | 122 | 127 | — | — | **141 (80 %)** | **128 (73 %)** |

Correct: banking 9 and 7 (K 10, 9); brain01 12 and 11 (K 9, 10). Every group is above its floor in both runs; brain01
M1 is the best brain01 run so far. Against K the brain01 gain is on Q15–Q29, which L (no checklist) already showed;
means against the run before the checklist: banking 152 vs 148.5, brain01 Q01–Q14 72.5 vs 72.5, Q15–Q29 62 vs 60.5
(L). Within the run-to-run spread (brain01 M1 vs M2 differ by 13 items; M2's Q07 alone lost 5).

Behaviour: the status lines reach the final answer in 17 and 19 of 36 banking turns and 8 and 15 of 29 brain01 turns
(never before). Mean turn time banking 44.9 / 43.8 s (K 40.6 / 37.0), brain01 63.1 / 49.9 s (K 60.7 / 53.5); cost
about the same.

Reading: the mart lower-bound fix is the measured gain. The check-off list moves no group beyond noise and shows
its status lines to the user in about half the answers (the slower banking turns were not the list: run N, without
it, took 44.7 s). Reverted in `81f9f588`; the idea moves to a trace-based check enforced by a Stop hook (spec
`2026-11-11-turn-check-design.md`, work plan item E2).

## Run N (2026-11-11): the L code again (checklist reverted), one run per vault

Runs: banking `70560495`, brain01 `d6744aa5`. Sonnet, same tiers and baselines.

| Vault / group | Floor | K1 / K2 | L1 / L2 | M1 / M2 | **N** |
|---|---|---|---|---|---|
| banking (214) | 67 % | 152 / 145 | — | 155 / 149 | **155 (72 %)** |
| brain01 Q01–Q14 (101) | 55 % | 68 / 77 | — | 78 / 67 | **68 (67 %)** |
| brain01 Q15–Q29 (75) | 76 % | 54 / 50 | 59 / 62 | 63 / 61 | **58 (77 %)** |
| brain01 all (176) | — | 122 / 127 | — | 141 / 128 | **126 (72 %)** |

Correct: banking 9, brain01 8. Mean turn time banking 44.7 s, brain01 51.4 s; cost $5.16 and $3.86.

Reading across K–N: since the mart lower-bound fix, brain01 Q15–Q29 is above its floor in all five runs (58–63, mean
60.6; K 54 and 50). Brain01 Q01–Q14 (67–78) and banking (145–155) did not change with it and stay above their floors
in every run. Every group of every run since L passes the floors, which is the condition for work plan item C
(promote the orient variant).

## Orient promoted (work plan item C, 2026-11-11)

The orient text is now `artmind/skills/artmind-query/SKILL.md` (frontmatter `name: artmind-query`); the classic text
every baseline ran with is archived at `docs/archive/artmind-query-classic/`. The benchmark prompt names `orient`
instead of `domains-overview`, and the opencode persona describes Orient → Parts → Views. From here a benchmark with
contract `classic` runs the orient text; runs A–N keep their recorded contracts. Live check: banking Q11 through the
restarted console, contract `classic`, loaded `artmind-query`, ran `orient` then `survey`.
