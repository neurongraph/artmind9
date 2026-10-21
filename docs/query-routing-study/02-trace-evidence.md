# 02 — Trace evidence

One section per probe round, newest last. Each round: vault, window, backend/model if
known, headline numbers, observed failure patterns (anonymised — see the README's privacy
rule), and the scenario ids they feed in `04-scenarios.md`.

---

## Round 1 — 2026-10-19

**Vaults:** `brain01` (traces), `my_work` (structured store only; no traces, Neo4j down).
**Window:** brain01 admin-console turns 2026-10-12 → 2026-10-19, about 60 turns. Backend
mostly `acp`.
**Skill baseline:** commit `89da8fa`.

### brain01 — `artmind traces stats`

| Week | Turns | views_first | views_skipped | view_run_rate | views_listed_unused | fall_through | mean graph attempts before fall-through |
|---|---|---|---|---|---|---|---|
| 2026-10-12 | 40 | 0.85 | 0.15 | 0.25 | 13 | 0.10 | 7.5 |
| 2026-10-19 | 5 | 1.0 | 0.0 | 0.0 | 2 | 0.0 | — |

Answer basis (week of 10-12): graph 26, vector 4, mixed 5, none 5.
Two saved views existed in the domain: `trips_by_year` (params PERSON, LOCATION, year,
merge window) and `meetings_in_range`.

### Patterns observed

**P1. The same question routed five different ways.** "How many trips did PERSON make to
LOCATION_A [in YEAR]" was asked five times across sessions. A view (`trips_by_year`)
answers exactly this shape. Routes actually taken:

- `domains-overview → views list → views show ×2 → entity-resolve ×2 → views run` (right)
- `domains-overview → views list → entity-resolve ×2 → views run → entity-resolve → views run` (right, with a retry)
- `domains-overview → structural-metadata → entity-resolve → entity-context → chunks → pattern8` (views skipped)
- `domains-overview → entity-resolve → text2cypher → pattern1 → text2cypher ×2` (views skipped)
- a follow-up turn going straight to `text2cypher → entity-context`

The user had to intervene twice ("doesn't the skill say views first?", "why did you not use
the view"). → S1, S2.

**P2. Long tails when no view fits or the view returns empty.** Count-of-visits questions
for other PERSON/LOCATION pairs and years took 14, 21, 21, 23 and 28 calls. Typical
shape: `domains-overview → [views list] → structural-metadata/metadata → entity-resolve ×2–3
→ pattern7/pattern1 → text2cypher ×2–4 → vector-text ×2–4 → pattern10 ×N → Read note
files`. One investigative follow-up ran `pattern10` more than a dozen times, once per daily
note. → S3, S4.

**P3. A view run that returns empty sends the agent off-protocol.** When `trips_by_year`
returned no rows for a pair the user believed had trips, the agent fanned out across
`timeline`, `pattern10`, `vector-text` and note reads instead of reporting the empty view
result and offering one targeted check. → S4.

**P4. Follow-up turns skip the skill.** Many follow-ups don't reload the skill and call a
remembered command directly (`entity-history`, `pattern10`, `text2cypher`), never matching
the new question against the view catalogue. Views-first only holds when the skill is
freshly loaded. → S2.

**P5. History/provenance questions land on text2cypher.** "History of entity X",
"observations for document D", "what got created by the latest ingest": several turns used
`text2cypher` or `metadata`, even though `entity-history --name` and `document-history`
exist (and the skill warns against text2cypher for history). Some turns produced no
command at all. → S5.

**P6. Insights questions are well-routed.** Turns seeded by the console's "about the
insight …" prompt reliably ran `views list → insights list/show → entity-context`, about
5–9 calls. Detours: `pattern5` ×2 on same-as pairs, `query graph conflicts --help` (the
agent unsure of the option shape). → S6.

**P7. Operator turns mix skills.** Curate, update and same-as turns load other skills and
use `query` commands only to check results. They are outside artmind-query's routing, but
show `entity-resolve`/`pattern6` used as quick verification probes. Not a routing target;
noted so later rounds don't count them as misroutes.

### my_work — structured store (no traces)

- `db bridge`: one table (`TABLE_1`, domain `performance_management`, grain `instance`),
  four entity classes mapped (three unconfirmed, one confirmed), twelve `term` bridge
  columns, all unconfirmed. Several mapped columns are PERSON-valued (manager chains).
- Implication: every structured question depends on the agent reading `db bridge` and
  then the 1,500-word `structured-store.md` to classify it. No evidence yet of how agents
  actually route there. **Needs a round of Q&A in my_work** — see `06-open-questions.md`.

---

## Round 2 — 2026-10-20 (short note)

**Vault:** `banking_corpus` (new): eight sibling document domains `FAMILY.*` plus one literal
`FAMILY` domain holding five structured tables. No saved views in any domain.
**Window:** one Q&A turn (the vault's latest admin turn; the other recorded turn is an
operator prompt, out of scope per P7). **Backend/model:** `claude-sdk`, `claude-haiku-4-5`,
the first non-`acp` evidence (see `06-open-questions.md` Q1). **Skill baseline:** commit
`9304704`, `artmind_version` 0.10.0.

### Patterns observed

**P8. The Route step is skipped when the question contains a noun phrase that sounds like a
domain.** Question shape: "list the main DOC_KIND present in the CORPUS_NAME corpus". The
agent took CORPUS_NAME (the vault's folder name) as the domain, reasoned "I have a specific
domain, so I can use it directly", and loaded the skill with that domain. `domains-overview`
was never run, although the skill requires it unless the user named an exact domain. The
route taken was `views list → entity-listing → structural-metadata → db bridge`, every call
against the invented domain.

**P9. The CLI answers an unknown domain with an empty success.** All four calls in P8
exited 0 with `rows: []`, `total_entities: 0`, `tables: []`. Nothing said the domain does
not exist, so the agent read "no rows" as "no data" and told the user the corpus had no
ingested data and to ingest documents. The data was there: the real DOC_KIND domain held 9
documents and about 1.5k entities, and `domains-overview` lists them in one call. The
answer was confidently wrong, `answer_basis: none`, and cost 7 turns, 28 s, about $0.08
(221k input tokens). The skill's "thin results → re-run with sibling domains from Route"
rule could not fire, since Route never ran.

### Scenario ids

S15 (a name that is an area, not a domain; ◐), new S18 (inventory: what documents of kind
K exist).

---

## Round 3 — 2026-10-21 (banking benchmark, 36 questions)

**Vault:** `banking_corpus`, the benchmark run recorded in the vault's
`benchmark_20261021.md`. **Window:** 36 benchmark turns, one per question in
`benchmarking/questions.md` (sections A baseline, B time/authority, C conflicts,
D cross-cutting, E structured+graph). **Backend/model:** `claude-sdk`; 35 turns on
`claude-haiku-4-5`, Q10 on `claude-sonnet-5`. **Skill baseline:** commit `9304704`,
`artmind_version` 0.10.0, with the unknown-domain guard of 2026-10-20 active. Per-question
grades, routes and causes: [07-benchmark-round3.md](07-benchmark-round3.md).

**Method.** Each answer was graded against its evaluation comment, the vault's own
documents and the trace (six parallel graders, one rubric; the graders' document claims
were re-checked read-only with `ARTMIND_VAULT=… ARTMIND_NO_PROXY=1 artmind query
document-history`). Grades do not use the benchmark file's own verdicts.

### Headline numbers

| | |
|---|---|
| Grades | 9 correct, 24 partial, 3 wrong (Q07, Q10, Q13) |
| By section | A 2/4/0 · B 3/4/2 · C 2/3/1 · D 1/9/0 · E 1/4/0 (correct/partial/wrong) |
| Primary cause of the 27 non-correct | routing 14, synthesis 6, retrieval 5, data 2 |
| Expected documents cited | 52 of 107 (49%) |
| Artmind calls | `vector-text` 55, `views list` 50, `pattern10` 46, `domains-overview` 33, `chunks` 20, `db sql` 15, `entity-resolve` 12, `entity-context` 11, `text2sql` 7, `text2cypher` 4, `db bridge` 4, `document-history` 2, `entity-history` 1; `query graph conflicts` 0, any `--asOf` 0 |
| Time | mean 156 s, median 108 s per turn; 10.9 turns; total cost $3.67 |

### Patterns observed

**P10. Views-first is wasted work in a vault that has no views.** All 36 turns ran
`views list` (50 calls; 14 turns ran it twice or more, 5 ran it before
`domains-overview`). The vault has no saved views in any domain, so every call returned
nothing. The skill's rule ("run `views list` once per domain; stop if you have not seen it")
is obeyed to the letter, at a cost of about one call per sibling domain per turn.
`traces stats` reports a `views_skipped_rate` of 23–62% for these turns, which is
meaningless here. `domains-overview` does not say how many views a domain holds, so the
agent cannot know it can skip the step.

**P11. An invented domain is still tried first (guard works, call wasted).** Six turns
passed the vault name or an invented `banking-…` name as `--domain` once. The 2026-10-20
guard fired every time, and the next call was `domains-overview`: no "no data" answer this
time (contrast P9). Cost: about one call per affected turn. P8 (Route skipped) remains
the root cause; the guard turns it from a wrong answer into a detour.

**P12. Point-in-time and retired documents are not found by the search commands.**
*Corrected 2026-10-21: the first write-up said the search commands "read current labels
only", which blamed a currency predicate. There is none.* Retirement is a label swap
(`artmind/lifecycle.py` `_transition`): `:Document`, `:DocChunk` and `:Observation` become
`:DocumentHistory`, `:DocChunkHistory` and `:ObservationHistory`. The `chunk_embedding`
(vector) and `chunk_text_ft` (fulltext) indexes are defined only `FOR (c:DocChunk)`, so
retired chunks are structurally absent from both. Nothing filters them out; they are simply
not in the index. Consequences:
- `vector-text` and `chunks` can never return retired text, with or without `--asOf`.
  `asof_predicate` filters `valid_from`/`valid_to` only on chunks already in the index, and
  acts as a floor filter, not a snapshot.
- `entity-resolve` and `entity-context` read the current projection by design.
- Nothing in search output says a retired predecessor exists. No turn used `--asOf`.
- The graph does reach history: `pattern10 --documentName <file>.md --asOf <any value>`
  matches `:DocumentHistory`/`:DocChunkHistory` (the value is a presence flag, not a date
  filter) and its document row carries `valid_from`, `valid_to` and `superseded_by`;
  `document-history --domain D --documentName <file>.md` returns the current and history
  observations per version. The skill mentions only the first (SKILL.md line 87) and frames
  `--asOf` as a date. The current (newer) document row has no backward `supersedes` pointer,
  so an agent holding the current version cannot learn its predecessor's name.

Which questions actually need retired text (checked against the benchmark's hints): Q07,
Q09, Q10, Q11, Q13. Q21 and Q25 do not; they are a data error (anomaly below).
- Q07 ("which limit applied on date D") answered from the current version for a date when
  the previous version was in force (wrong). The previous version also disagrees with
  itself between two tables, which the question was designed to expose.
- Q09, Q11, Q13 hit the same wall (partial or wrong).
- Q10 (Sonnet 5): 22 turns, 953 s, $0.65. `pattern10`/`document-history` were called three
  times with `--documentName` lacking the `.md` suffix, which returns zero rows without
  saying why; the agent concluded no rate history existed. With the suffix the history
  holds the answer.

Design reading: the questions are answerable through the graph (supersession edge,
`superseded_by`, `pattern10`, `document-history`), so history needs to be discoverable from
the graph side, not added to the vector index. See `03-proposal.md` D and open question 13.

**P13. Conflict questions never touch the conflicts route.** C-section questions (Q13–Q16,
Q28–Q29) made zero `query graph conflicts` calls and none read `references/conflicts.md`.
Q13 (wrong): never fetched the supersession-notice chunk that states the fix, then called
the current tiers the old policy. Q14/Q15/Q16 (partial): the decisive chunks were retrieved
but the answer quoted one bullet of two (Q15), over-generalised an exception (Q14) or
stopped after the first document when the search had shown more (Q16). Only Q08 and Q28 (both correct) disclosed a
conflict, and neither used the conflicts route either.

**P14. Domain narrowing hides sibling domains.** `vector-text --domain A --domain B …` was
scoped to a hand-picked subset of the eight siblings in Q04, Q05, Q06, Q18, Q19, Q22,
Q24, Q32, Q33, leaving out `banking.reference` and `banking.policy` where the answer
lived. The parent name `banking` already scopes to all siblings (checked: one call with
`--domain banking` returns the top hit the narrowed calls never saw), but nothing tells the
agent so. Related early stopping: Q06, Q17, Q20 did not follow `[[wikilinks]]` or
the further `pattern10` documents that their first retrieval named.

**P15. Structured store: routing mostly right, command surface wrong.** First evidence for
S7–S9. `db bridge` was read in Q32–Q34 and Q36; the agents separated SQL facts from
policy text in all five E answers. But: invalid commands were tried (`query db sql`,
`query graph entity-resolve`, `db sql --help`); `text2sql` ran six times in Q32, once for
72 s; Q33 and Q32 never fetched the complaints policy even though the question named
payout authority; Q36 ignored a group of n=1 when claiming an effect; Q35 never mapped a
driver per customer to the guidance bullets. The weak point is completeness of the policy
side, not the store choice.

**P16. Synthesis embellishment.** Q01, Q14, Q31 add details no source holds (deadlines,
exceptions), Q29 states "indefinitely" without support. These are model behaviour with
evidence in hand; routing cannot fix them, but a ready `sources` block and a "state only
what a retrieved chunk says" rule might (S19 below).

**P17. Retrieval is document-centric; `entity-context` is rare.** `pattern10` (46),
`vector-text` (55) and `chunks` (20) carry the run; `entity-context` ran 11 times,
`entity-history` once, hierarchy/relate never. This corpus is documents, so this is
expected, but it means S10–S12 are still unseen.

### Light comparison with earlier benchmark runs (same 36 questions)

| Run | Mean s | Median s | Mean turns | Total cost | Note |
|---|---|---|---|---|---|
| Aug 2026 (`baseline-2026-08-23.md`) | 65 | – | 9.9 | $11.70 | different, pricier setup; Q33–Q36 were one-turn failures before the structured store |
| Sep 2026 (`after-cutover.md`) | 48 | 45 | 10.2 | $3.53 | |
| Oct 2026 (this run) | 156 | 108 | 10.9 | $3.67 | Claude SDK / Haiku 4.5 |

Turn counts and cost are flat; wall time is three times the September run. The CLI is not
the cause: summed command time was 269 s of 5,606 s total (about 5%). Slowest commands:
`text2sql` (median 3.7 s, max 72 s) and `text2cypher` (median 6.5 s). The regression is on
the model/API side. Grade quality is not comparable across runs (earlier runs were not
graded with this rubric), so only speed and cost are compared.

### Data anomalies surfaced (not routing; do not fix in this study)

- **Supersession recorded in the wrong direction.** `policy_customer_identification.md` (v1)
  is current with its observations and `policy_customer_identification_v2.md` is retired,
  although the v2 file says it is effective 2026-03-01, "Supersedes Version 1.0" and
  "Superseded By None". Root cause found in the curation record: the adjudicator wrote
  v1 -> v2 (newer = v1). Q21 and Q25 follow from it. All four supersession records in this
  vault are `detected_by: adjudicator`, scope `document`, no `effective` date. Open question 15.
- **Second suspicious record (unverified).** The February rate schedule supersedes
  `welcome_pack.md`, which retires that document and its 92 observations. Check the file
  before treating it as a defect.
- **Possible gap (unverified).** The February rate-schedule file says it is superseded by
  the March schedule from 2026-03-01, yet both are current and no March -> February record
  exists.
- Two grading notes are themselves stale: Q04's note cites a schedule that later
  schedules supersede, and Q03's note says both cancellations are immediate while the SOP
  table says notice is typical. Re-check before using them as pass/fail.

### Scenario ids

S7–S9, S13, S14 now seen (see `04-scenarios.md`). New: S19 (multi-document procedures and
"how do I" answers), S20 (which version applied on date D), S21 (vault with no views).
