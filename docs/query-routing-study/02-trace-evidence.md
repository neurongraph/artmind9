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
  "Superseded By None". Root cause (2026-10-22): the adjudicator
  picks the newer side by comparing `valid_from`, which is empty on these documents, so a tie
  goes to side B regardless of content. Q21 and Q25 follow from it. All four supersession
  records in this vault are `detected_by: adjudicator`, scope `document` (the code hardcodes
  it), no `effective` date. Open question 15.
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

---

## Round 4 — 2026-10-22 (brain01, 11 admin turns)

**Vault:** `brain01` (`personal_journal`, 1,687 documents, two saved views: `trips_by_year`,
`meetings_in_range`). **Window:** the 11 admin turns recorded on 2026-10-22, four
conversations. **Backend/model:** `claude-sdk`, `claude-haiku-4-5` on all 11. **Skill baseline:**
commit `9304704` (`artmind_version` 0.10.0). Turns are labelled T1–T11 in time order; the
8-character prefix is the trace id for `artmind traces show`. Questions are shapes only.

| T | Trace | Conversation | Shape | Skill loaded | Commands | Turns / s / $ |
|---|---|---|---|---|---|---|
| T1 | `ed40da48` | A, first | highlights of my trip to CITY_A in MONTH YEAR_A | no | none | 1 / – / 0.05 |
| T2 | `ce2f1ecb` | A, 2nd ("query artmind") | same question, re-sent | yes | `domains-overview`, `views list`, `entity-resolve` ×2, `views run` (0 rows), `vector-text`, `pattern10` ×4 | 12 / 72 / 0.13 |
| T3 | `8757a789` | A, 3rd | did I visit SHOP_A again? | follow-up | `pattern7` | 2 / 11 / 0.17 |
| T4 | `a25a6226` | B, first | recorded deaths in my family | yes | `domains-overview`, `views list`, `pattern7` | 6 / 30 / 0.05 |
| T5 | `a74bce51` | B, 2nd | any reference to PERSON_B, another of my uncles | follow-up | `entity-resolve` | 2 / 16 / 0.07 |
| T6 | `e9ab150d` | C, first | who is my favourite ROLE? | no | none | 1 / – / 0.01 |
| T7 | `751511e1` | C, 2nd | "query artmind DOMAIN" (no question) | no | none | 1 / – / 0.01 |
| T8 | `e6b42ab3` | C, 3rd | T6 re-asked | yes | `views list`, `structural-metadata`, `entity-resolve`, `vector-text`, `text2cypher --dry-run`, `pattern7` | 9 / 55 / 0.07 |
| T9 | `c1a07629` | C, 4th | "use frequency methods, not just the word favourite" | follow-up | `text2cypher --dry-run`, `vector-text` ×2 | 4 / 37 / 0.12 |
| T10 | `820dff75` | D, first | my hobbies and how I spend time outside work | no | `Read` of the agent's memory index (file absent) | 2 / – / 0.01 |
| T11 | `eb3143d0` | D, 2nd | "you need to query artmind DOMAIN for the above" | yes | `domains-overview`, `views list`, `metadata`, `pattern1`, `pattern7` ×2, `vector-text` ×2 | 11 / 59 / 0.10 |

Total about $0.81 for 11 turns. Mean skill-turn cost is about $0.09; the four no-skill
turns cost about a cent each and answered nothing.

### `traces stats` for the day

| Bucket | Turns | fall_through | graph_miss | graph_productivity | views_first / skipped | view_run | views_listed_unused | answer_basis |
|---|---|---|---|---|---|---|---|---|
| `personal_journal` | 7 | 0.29 | 0.29 | 0.77 | 1.0 / 0.0 | 0.14 | 3 | graph 3, vector 2, mixed 1, none 1 |
| `(none)` | 4 | 0 | 0 | – | – | 0 | 0 | none 4 |

The `(none)` bucket is the four turns that ran no `artmind` command (T1, T6, T7, T10): no
domain is ever attributed, so the stats cannot show that they were Q&A turns the skill
should have handled. See P20 for why 2 of the 3 unproductive graph spans behind the
`personal_journal` miss rate are not misses.

Backend mix of the whole brain01 registry (72 turns since 2026-10-16): `claude-sdk` 61
(57 on `claude-haiku-4-5`, 4 with no model recorded), `acp` 11. Round 1 describes this
vault's turns as "mostly `acp`"; the registry metadata says otherwise. See open question 1.

### Patterns observed

**P18. Personal-memory framing skips the skill on the first turn.** Three of the four
first turns of the day (T1, T6, T10) loaded no skill and ran no `artmind` command. All three
are first-person questions about the user's own life or work ("my trip", "my favourite",
"my hobbies"). The visible reasoning of T6 and T10 treats them as recall questions for the
agent's own auto-memory: T10 reads the memory index (absent) and replies that it knows
nothing yet and offers to save the answer; T6 says the same; T1 says it cannot see personal
history and offers to query artmind "if documents were ingested". The system prompt
(`ADMIN_SYSTEM_APPEND`) describes artmind-query as the tool "for inspecting the graph", an
operator framing, while the Claude Code preset carries its own memory instructions and the
agent's memory directory exists under the run folder. Recovery cost the user one or two
extra turns each time (T2; T7→T8; T11): T7 was the user typing only the domain name, and
the agent asked what to query instead of asking the earlier question again, so the next
turn restated the question. Every recovery turn then loaded the skill and answered.
Base rate: across all `claude-sdk` first turns in this vault, 21 of 26 loaded the skill. Before
today 20 of 22 did (the two misses were operator-style prompts); today 1 of 4. First-person
phrasing among earlier first turns: 5 of 6 loaded the skill, so phrasing alone is not
decisive, and n is small. The QA profile (`chat-ui`) uses different wording ("questions")
and was not exercised. → S23.

**P19. An empty view result is an entity-resolution miss, twice over (T2).** The agent
resolved "my"/"me" to a person node carrying the user's real name (observation_count 1,
connection_count 12, description about an unrelated everyday event) instead of the journal's
author entity `Diarist` (public schema guidance names the narrator that way; 1,991
observations, 8,149 connections). It then picked the `LOCATION` node whose description
mentioned the study tour over a same-city twin (3 vs 3 observations, 9 vs 3 connections).
`views run trips_by_year` returned zero rows. Read-only re-check, all four combinations
against the live graph:

| person \ place | place node 1 | place node 2 |
|---|---|---|
| real-name PERSON | 0 | 0 |
| `Diarist` | 0 | **1** (one trip, 2 notes) |

Only one of four combinations returns the trip. The view and its Cypher are correct. The
agent then recovered (one `vector-text`, four `pattern10` on date-named notes) and answered
the question, but its date range stops one day before the view row's last note. This answers
open question 6 for this case: an empty view is a resolution miss plus a data gap (two
`LOCATION` nodes for one city; neither the two city nodes nor the organisation twins of P22
appear in the 22-candidate `insights same-as` queue). `entity-resolve` already prints
`observation_count`/`connection_count`, and nothing in the skill says that "I/my" resolves to
the narrator entity: a hub rule fixes the person. It would not fix the place, because there
the node with the trip is the *lower*-degree twin (3 connections against 9), so the right
behaviour for twin nodes of the right class is to run the view for each candidate (or fix
the duplicate at the same-as level), not to prefer one. → S4; C3.

**P20. `--dry-run` is read as a zero-row result (T8, T9).** Both `text2cypher` calls of the
conversation used `--dry-run`, which by design prints the Cypher and `"rows": [], "dry_run":
true` and executes nothing. T8 then wrote that the generated Cypher "found no results" and
moved on; T9 did the same. In neither turn was the Cypher run. The skill says "run
`--dry-run` first to inspect the Cypher" (line 294) and never says to run it afterwards. Side
effects: (a) `traces` counts the dry-run as an unproductive graph attempt, so 2 of the 3
zero-row graph spans in the day's `personal_journal` bucket, and T9's `fell_through` flag, are
artefacts (the third, the empty view in T2, is real); (b) with stderr merged, each
`text2cypher` call prints a WARNING that the configured model rejects `temperature=0` and
retries, so the agent's tool result starts with two log lines before the JSON. Executed
for real, a one-call text2cypher ("organisations by observation_count") returned a ranking
immediately (see P22). → S3, S22; D (`next`).

**P21. Entity search returns at most 10 rows with no truncation signal (T4, T5).** "Recorded
deaths in my family" ran one `pattern7 --searchTerm "death died deceased"` at the default
`--limit 10` and listed ten people as the family's recorded deaths. With `--limit 50` the same
search returns 44 rows of which 12 are death events; two family deaths absent from T4's answer
are among them. The user then asked about one of them by name (T5); the agent found it with
`entity-resolve`, said it "was missing from my initial search results", and re-presented
a "complete" list of five that still lacks the other missing family death. `pattern7`'s JSON has
no `rows_total` or `truncated` field (unlike `pattern8`, `pattern10`, `text2cypher`), and its
top-10 mixes `EVENT`, `PERSON`, `EMOTION` and `LOCATION` nodes by relevance, so the cap
silently drops events. Completeness language ("recorded deaths", "complete") was supplied by
the agent, not asked for by the user. → S24; D (`next`, truncation).

**P22. A preference question is answered by sampling, then by inference (T8, T9).** "Who is
my favourite ROLE?" has no stored fact. T8 searched for the word (entity-resolve,
`vector-text`, a never-executed text2cypher, `pattern7 "favourite"`) and honestly answered
"the graph has no explicit favourite", offering a frequency analysis. The user asked for it
in T9; the agent ran a dry-run (P20), then two `vector-text` queries ("positive", "good")
and answered "your favourite is ORG_A, appearing far more often than any other". No count was
computed. A real count (executed text2cypher, one call) gives `ORG_A` 67 observations,
`ORG_B` 67, a twin node of ORG_A 64, then 54; so the top is a tie, ORG_A is split across two
nodes (neither in the same-as queue), and the role of `ORG_B` (client vs other) was not
checked. The sentiment evidence is positive words in chunks that the retrieval query itself
asked for. The `insights` route (salience, "who matters most") was not tried; the
snapshot is nine days old. → S22; C4 (a lexical row for "favourite / most / top").

**P23. Discovery commands chosen by name, not by size (T11).** For "hobbies and how I spend
time" the agent ran `graph metadata` (88 KB) and `pattern1 --entityClass ACTIVITY --limit 100 |
head -100` (110 KB; `head` does nothing to one-line compact JSON). Both were persisted to disk and
the model saw a 2 KB preview of each and never read the files. The answer came from two
`pattern7` calls and two `vector-text` calls. `structural-metadata` returns the same
orientation in under 2 KB, and the skill documents it. 502k input tokens for the turn. The
final answer is a broad, well-sourced synthesis with two filler bullets ("various
activities"). → S2-like exploration; D (output-size guard with `next`).

**P24. Entity-search rows carry no provenance, so Sources go vague (T4, T5).** `pattern7`
rows hold `entityData` only: no document name or path. T3 could cite two documents because
the shop entity's `_valid_from` equals the date-named note; T4 and T5 could only cite the
journal as a whole, with no document, for a list of ten events. Of the 6 turns that found
data, 2 had no document-level source (T8's empty Sources block is the honest "no data"
case). The proposal's ready `sources` block (D) needs entity rows to name their source
documents. → S24; D.

**P25. Existence and follow-up questions are well routed (T3, T5).** "Did I visit SHOP_A again?"
(`pattern7`, one call, two real notes, both cited, 11 s) and "any reference to PERSON_B?"
(`entity-resolve`, one call) skipped the skill and the views check legitimately: the catalogue
was already in the conversation and no view fits. Both answers were correct. A skipped
skill reload is therefore not itself a failure (contrast P4); the question is whether the
cached catalogue was consulted, which `views_first` does count. → S2, S25.

### Verification notes (read-only)

- The trips view, its parameters and its Cypher are correct; the 2×2 above used
  `ARTMIND_NO_PROXY=1 artmind query views run trips_by_year`.
- Executed text2cypher from this checkout needs `env -u ANTHROPIC_BASE_URL` (CLAUDE.md §5);
  without it the call fails with a 404, which is not what the agent's shell sees.
- Skill, views and entities were checked against the live graph; no note text, answer text or
  names are recorded here.

### Scenario ids

New: S22 (superlative / preference over a class), S23 (first-person question with no domain),
S24 (enumeration with a completeness expectation), S25 (existence / recurrence of a named
thing). S2, S4 gain evidence.

---

## Round 5 — 2026-10-22 (coverage audit of every recorded turn)

**Vaults:** `brain01` (72 turns), `banking_corpus` (54 turns), `my_work` (0 turns).
**Method:** every turn's command sequence dumped with the §2 snippet of `05-probing.md` (to the
scratchpad, not this folder), then compared with what rounds 1–4 analysed.

### Coverage

| Vault | Batch | Turns | Analysed before | Finding |
|---|---|---|---|---|
| brain01 | admin 10-16 → 10-19 | 57 | Round 1 | Re-aggregated: 60 query-side admin turns overall, 26 conversations, `views list` in 22, `views run` in 10, calls per turn median 5, tail of 20, 21, 27, 40. Consistent with round 1. 8 turns loaded operator skills (curate, update, ingestion-helper): P7. |
| brain01 | **benchmark 10-17** | 4 | **no** | P26 below. |
| brain01 | admin 10-22 | 11 | Round 4 | — |
| banking_corpus | admin 10-20 | 6 | Round 2 (one turn) | The other 5 are operator turns (table-classification review, same-as review): P7. |
| banking_corpus | admin 10-21 | 12 | **no** | All operator turns: conflict adjudication (two conversations, 54 then 3 conflicts), a projection synthesize/rebuild, ingestion-job help. One adjudication turn loaded `artmind-query` as well, only to read conflicts. P7; caveat below. |
| banking_corpus | benchmark 10-21 | 36 | Round 3 | — |

The study's Q&A coverage is therefore complete. Unseen still: `my_work` (no traces at all),
hierarchy, path and filing questions.

### Patterns observed

**P26. One question, one profile, one model: four routes and three different answers.**
On 2026-10-17 the same count question (PERSON to LOCATION in YEAR, the `trips_by_year`
shape) ran four times as a one-question benchmark (benchmark profile, Claude SDK / Haiku 4.5).
The view was saved at 10:55, between the first and second runs.

| Run | View saved | Route | Calls | Outcome |
|---|---|---|---|---|
| 1 | no | metadata → resolve ×2 → text2cypher → vector-text → pattern10 ×2 → text2cypher (dry-run) → vector-text ×2 | 16 | count 2 (an undercount) |
| 2 | yes | `views list` → resolve ×2 → pattern1 → pattern7 → … text2cypher ×3 … vector-text ×5, chunks ×3 | 23 | count 2 (an undercount) |
| 3 | yes | `views list` → skill reloaded → stopped while narrating the protocol | 7 | no answer |
| 4 | yes | `views list` → `views run` | 5 | count 3, the view's answer |

Three readings. (a) In runs 2–3 the catalogue was already in the agent's context, on a
profile whose system prompt says "follow the protocol exactly", and the agent still did
not match the question to the view. Knowing that views exist (P10's view count) is not
enough; the missing step is matching the question to a view and filling its params.
(b) The route the agent writes for itself undercounts the same fact the view counts. (c)
Run 3 spent its budget restating the skill's steps. Supports C2 (deterministic view
match and param fill) over an entry call that only lists facts. → S1.

### Caveat on round 3 (banking)

The benchmark ran 14:34 → 16:05 on 10-21, after that morning's conflict adjudication
(07:30 → 13:11) and projection rebuild. So the graph the benchmark saw is the
post-adjudication graph, which is also what the graders re-checked against. P13's "the conflicts
route was never used" stands, but how many conflicts were still *open* at benchmark time is
not recorded, so whether `query graph conflicts` would have returned the C-section's pairs is
unverified. See open question 27.

### Scenario ids

S1 gains P26.
