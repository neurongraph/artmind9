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

## Round 6 — 2026-11-02 (this desktop: brain01, nbs_audit_2026, my_work)

Probed from the Mac Studio, read-only, for the retrieval and curation architecture design
(`docs/superpowers/specs/2026-11-02-retrieval-curation-architecture-design.md`). Shapes,
counts and command sequences only.

### Coverage

| Vault | Turns on this machine | Rated | Shadow plans | Notes |
|---|---|---|---|---|
| `brain01` | 36 (35 admin, 1 benchmark), 10-13 → 10-24 | 2 (1 good, 1 partial) | 13 (`ok`) | 10-18: two long operator conversations (same-as review, conflict adjudication) |
| `nbs_audit_2026` | 12 admin, 10-31 → 11-01 | 0 | 12 (`ok`) | one chunks-only domain; every turn before `query survey` merged (11-01 23:11) |
| `my_work` | 1 admin, 10-25 | 0 | 1 | a table-classification review |
| `banking_corpus`, `shadow_audit_2026` | 0 | — | — | the round-3 and later banking traces are not on this machine |

`traces stats --by all`: brain01's busiest week had 9 shadow plans, 56 % high confidence,
agreement 0.6 on high-confidence plans (0.33 on first turns, 1.0 on follow-ups);
nbs_audit_2026 had 12 plans, all high confidence, all route `search`, and agreement 1.0
(the agent ran `vector-text` too). The shadow collection asked for in `09` §3 (about 50
rated turns across `banking_corpus` and `brain01`) is therefore **not complete** on this
machine: 26 shadow plans, 2 ratings, no banking turns.

### Patterns observed

**P27. Non-questions are planned as questions.** Every shadow plan picks a retrieval route,
even for turns that are not retrieval: a formatting follow-up on the previous answer's table,
a request to write the previous answer to a file, a question about the agent's own model, and
curation instructions (two names are one person; fix a set of name variants). In the chunks-only vault three such turns were planned `search` at
**high** confidence; in brain01 they came out `conflicts`, `path` or `search` at low
confidence. The planner has no notion of intent above the route. → design §5.3 (intent
layer).

**P28. Curation through chat is slow and expensive.** One same-as review (about 140
proposals) and one conflict adjudication (about 40 conflicts) took about 25 agent turns over
two conversations; single turns ran up to about 10 minutes, and a conversation's running
cost reached about USD 3 (Sonnet). Most turns were the operator approving or rejecting
items one batch at a time, plus requests to see the source text around a pair. Each item's decision
needed the same three things: both names, the evidence chunks, approve/reject. → design
§5.7 (Inbox cards with a deterministic review flow; the agent only for hard cases).

**P29. Identity defects surface in Q&A and are fixed by instruction.** A count over a
class anchored on a family member undercounted because the anchor existed under two kinship
variants and a person under two name variants; the user found the gap in the next turn (asking why one
person was missed), then told the agent to curate it in the same chat (one turn about 18
minutes). The plan routed the count to `aggregate` at high confidence; the agent did not
follow it (entity-context, pattern6, pattern8 instead). → design §5.3 (variants and twins
in the plan, a completeness branch), §5.6 (identity gap → same-as proposal), §5.7 (inline
"same thing?" action).

**P30. Survey-shaped questions in a chunks-only domain go to one `vector-text` call.**
Questions asking for one obligation or term across every contract were planned
`search` at high confidence and answered from one or two `vector-text` calls. One repeat of
the same question took about 8 minutes. These predate `query survey`, so they are evidence of
need, not of a survey failure; the survey route's cues must catch "across every document of a
kind" phrasings, not only "compare" and "overview".
→ design §5.3 (document-coverage guard).

**P31. A high-confidence plan is no guarantee the agent follows it.** brain01's
high-confidence plans were followed 3 times in 5; first turns 1 in 3. Numbers are tiny, but
they agree with P26: telling the agent the route is weaker than running it. → design §5.4
(executor).

**P32. Correctness is unmeasured.** 2 of 49 turns on this machine carry a rating. Shadow
agreement says whether the agent ran the plan's first step, not whether either was right.

**P33. Some turns record no cost or model.** brain01's 10-24 turns on the ACP backend
record `turns = 1`, cost 0 and no model, so cost and call-count comparisons across backends
are not possible from traces yet.

### Scenario ids

S3 gains P29 (aggregate with identity variants); S19 gains P30; S24 gains P29; new
operator-intent turns (P27, P28) have no scenario row yet, because they are not retrieval.

## Round 7 — 2026-11-04 (laptop B + desktop: brain01, nbs_audit_2026)

The first round from the collection SOP (`10-collection-sop.md`), analysed by four parallel
agents: per vault, the benchmark runs, and a cross-vault quantitative pass. A fifth agent
checked key answers against the vault data. Each turn was judged from its ordered event
stream for **struggle** (score 0 = direct, 1 = detour, 2 = struggled and got there,
3 = struggled and failed or gave a weak answer), not only from labels. Shapes and counts
only; per-turn tables stay in the analysis scratchpad.

### Coverage

| Machine · vault | Turns | Rated | Shadow plans | Notes |
|---|---|---|---|---|
| desktop · brain01 | 36 (35 admin, 1 benchmark), 10-13 → 10-24 | 2 | 11 | same turns as round 6 |
| laptop B · brain01 | 94 (81 admin, 13 benchmark), 10-16 → 11-04 | 2 | 16 | 6 benchmark runs of 20 questions; 7 never ran |
| desktop · nbs_audit_2026 | 29 admin, 10-31 → 11-04 | 8 (all with notes on partial/wrong) | 29 | chunks-only domain |
| laptop B · nbs_audit_2026 | 22 admin, 11-02 → 11-04 | 0 | 22 | two batch claim-testing sessions with sub-agents |
| desktop · shadow_audit_2026 | 0 | — | — | no traces |

181 turns: 167 admin, 14 benchmark; 92 first turns, 89 follow-ups; 12 rated (5 good,
6 partial, 1 wrong). 53 turns ran no `artmind query`. The SOP's "done when" is **not** met:
ratings cover 7 % of turns, and only one benchmark question has an expected answer.

### Struggle

Retrieval turns, mechanical score (131 turns, all vaults): 0 = 54, 1 = 34, 2 = 33, 3 = 10.
Judged per turn from the events (the per-vault agents, 118 turns): brain01 70 turns
(S2–S3: 35), nbs_audit_2026 34 turns (S2–S3: 18), benchmark 14 (S2–S3: 7). **Roughly
half of retrieval turns struggle clearly.**

| Where the S2–S3 turns struggled | brain01 | nbs | bench |
|---|---|---|---|
| choosing the command / its form | 15 | 3 | 3 |
| resolving the entity or term | 10 | 7 | 5 |
| getting enough coverage | 7 | 15 | 7 |
| handling tool output | (11 unread dumps) | 6 | (6 turns) |
| finding the domain | 3 | 0 | 0 |
| data not there | 3 | 0 | 2 |

**Avoidable by a deterministic route:** brain01 23 of 35 with today's commands plus a few
rules, 32 of 35 with plan programs; nbs 13 of 18; benchmark 6 of 7. Across vaults, 87 % of
the non-curation struggle turns contain a loop of three or more on one command, which a
fan-out or guarded plan step replaces.

### Patterns observed

**P34. Large outputs never reach the agent intact.** The SDK harness persists any tool
result over about 32 KB to a file and shows a 2 KB preview. `vector-text --topK 10` with
the default chunk context is 41–110 KB: in the chunks-only vault 158 of 158 such calls were
persisted, against 0 of 15 with `--context passage` (about 20 KB). `survey` at the
temporary 50-document default is 188–214 KB: 34 of 35 persisted, and in 0 of 9 main-agent
calls was the whole payload read (the agent read about 15 documents, grepped names, or never
opened it; once the user then asked for "a survey"). `graph metadata` is about 85 KB every
time; class listings and timelines reach 0.5–1.1 MB. Reading the persisted file is often
refused by the tool gate. Each persisted result cost 1–5 extra calls. → the largest single
driver of struggle in the chunks-only vault.

**P35. The tool gate causes failures.** 11 refusals in 7 benchmark turns, more in admin
turns: a leading `cd <vault> &&`, pipes, and reads of persisted results. One turn produced
no answer. 29 calls exited non-zero, mostly the agent composing wrong CLI forms (a missing
required option, `query pattern10` without `graph`).

**P36. First person resolves to a name stub.** In 6 brain01 turns "I" or the owner's own name
resolved to a one-observation node named after the owner, not the narrator (about 2,000
observations); the view returned 0 rows and the user corrected it four times. The stub
still ranks first in resolution. The shadow planner mapped I/my to the narrator correctly in
5 of 5 plans. Extends P19.

**P37. Identity misses are silent.** Twins (kinship variants, a name with and without a
surname, a country and its abbreviation) decided 10 of 65 brain01 retrieval answers (6 wrong
or partial) and 3 of 9 recent benchmark answers, often at struggle 0–1, so nothing in the
trace flags them. The shadow plan's `twins` field was empty in every plan. Extends P19/P29.

**P38. Term and alias resolution is the main gap in the chunks-only corpus.** A two-letter
acronym for a document's subject never matched the expanded words in its title (5 turns,
both machines: 2 failed, 2 answered from a proposal instead of the signed amendment, 1
recovered after a hint). A user's nickname for a contract appears in neither title nor text
(3 turns: one false "not in corpus", one recovery by dumping 500 document names and matching
dates, one alias the user supplied).

**P39. The planner's search-term slot is weak.** 6 of 9 planned `pattern7` steps kept filler
words or did not expand a category (the category word found 1 row; its member names found 97). Both planners
fill the term with the top candidate's entity name instead of the user's mention; for a
named recurring event, keyword search on the mention covers 17 of 17 relevant notes, while
`survey` reaches 12 of 17 in its top 50.

**P40. History questions go astray without the history route.** 10 brain01 turns over two
days ended "not found" or answered something else; one user-typed history command solved it
(P5 recurs with the skill loaded).

**P41. Half of the traffic is not a fresh question.** 89 of 181 turns are follow-ups; 12
brain01 turns are context-only replies ("yes please", "run as is") that can only be read with
the previous turn; status polls on background jobs (8 in nbs). 21–38 non-retrieval turns got
a retrieval plan, 17 of 17 in the chunks-only vault at confidence 1.0. P27 at about three
times the round-6 count.

**P42. Survey is under-planned and its trigger list misses user wording.** Survey ran in
15 turns but was planned in 4; 8 plans said `search` where survey was right, including a
question that literally asked for "a survey" (the word is not a trigger). In chunks-only
domains route confidence is forced to 1.0 and carries no information. P30 continues.

**P43. Evidence-shape signals do not separate needle from survey on this corpus.** Probed
on 10 survey labels, 10 needle labels and 16 user phrasings (read-only, live vault):
concentration (top-1 document's share of the top 10) is 0.1 for 9/10 surveys and 8/10
needles; level agreement (survey top-10 also in `vector-text` top-10) has median 5 for both;
documents over 0.75 × top score: needle median 13.5, survey 15.5. Only a sharp score drop is
informative, and only one way: relevance(1) − relevance(2) ≥ 0.03 for 5/10 needles and
0/10 surveys. Coverage of expected documents by depth: `vector-text` top 10: 27 %;
survey top 10: 43 %, top 20: 57 %, top 50: 82 %. The needle document was in survey's top 10
for 8 of 10 needle labels. (Most documents in this corpus are one chunk, which is why
concentration is flat.)

**P44. Answers that hedge instead of fixing.** The newest model is cheap and short but states
"not complete" or "top passages only" instead of paging to `rows_total` or cross-checking (4
brain01 turns, 6 of 7 single-`vector-text` survey answers in nbs). Synthesis also buried the
latest, broadest clause under older specifics (2 rated turns).

**P45. Curation in chat, again.** 28 of 116 brain01 admin turns (24 %), about an hour, and
the chat twice claimed a same-as merge that had not been applied; propose/refine timed out at
120 s. P28 continues.

**P46. Batch work through sub-agents is expensive and untraced.** Two claim-testing sessions
(USD 9 and 14) ran about 300 `artmind query` calls in background sub-agents; 92 % of
sub-agent calls have no span, and the status turns that collected them show 0 spans.

**P47. The same question still takes different routes.** The benchmark trip-count question,
five runs: four routes, three answers, 0–17 calls, 26–177 s. Only the run that ran the saved
view was cheap and right (P26 continues). Another run saw the view, pre-resolved the person
itself, failed, and never ran it.

**P48. Planners.** On brain01, high-confidence decision-model plans had the right route in 8
of 8 admin turns, but an adequate first step in only 4 (search terms, twins). On the
benchmark questions the lexical-only replay was 2 correct, 5 correct-abstain, 3 abstained,
**0 wrong**; the decision-model shadow plans were right on 1 of 4 high-confidence plans.
Decision-model latency was 60–64 s on one evening (one timeout, one unreachable). Domain
choice was never the problem: plan and agent agreed on 47 of 48.

**P49. Instrumentation gaps.** Ratings on 7 % of turns; feedback rows appended (up to 6 per
trace); `cost_usd` is **cumulative within an SDK session**, so summing it overcounts long
conversations (round-level totals are upper bounds); ACP turns record no model or cost;
model errors stored as `status: done`; survey and `topics show` spans store `{}` as
evidence; no record that a result was persisted or how big it was; agreement checks only the
first command name; no intent or turn-index field; `review verify` logged "mechanical checks
pass" for a run that gave no answer.

### Verified against the data

A verification agent checked 11 items (27 agent answers) against the vault notes, source
files and graphs on the desktop, which are synced with laptop B. All 11 were answerable from the data; three needed a definitional choice, which the user settled on 2026-11-04: an implied event counts, every practitioner type counts, and every ailment counts. Under those definitions the verdicts below stand, and the true counts are 4 trips, 9 practitioners and 13 illness episodes.

| Verdict | Answers |
|---|---|
| correct | 8 (one with minor errors) |
| partial | 6 |
| wrong | 13 |

One review dossier's expected answer was itself wrong (it left out an explicitly narrated
event), so a benchmark grade built on it would mislead.

**P50. Counts undercount behind a "floor" hedge.** 3 of 3 verified count questions reached only
40–80 % of the truth (episodes, practitioners, trips) while saying the number was a floor.
Two other answers overcounted: one counted an incoming plane's diversion as the narrator's own
trip, one padded the list with vague entity hits.

**P51. Identity fragmentation is deeper than the traces showed.** One parent is spread over
5 nodes, the other over 5, a friend over 2; one node carries the wrong relation in its name;
the owner-named stub outranks the narrator although a note in the vault states that the owner
is the narrator.

**P52. The right document was present every time an acronym or alias missed.** 5 of 5 acronym
misses on two machines had the signed document in the corpus (one trace listed it four
minutes before the miss). The answers came from a pre-contract proposal, about three months
off. One alias exists only in index tables, the other only in folder names and body text,
never in a title.

**P53. "Latest version" questions answered from a superseded document.** 2 turns: one gave a
superseded named-site list as current; one buried the current blanket grant and got the
purpose limits of the other grants wrong.

**P54. A conversion artifact read as a source error.** Image-table OCR turned a percentage into a
reversed-glyph token in 5 converted documents; the agent told the user the contract "has a
typo". The signed PDF shows the right figure. The page check does not flag it.

**Benchmark run without expected answers, drafted from the notes.** All 8 recorded answers
are partial (0 correct, 0 wrong): coverage 4 (a yearly event searched by meaning missed years;
a synonym for the event was missed; a hub person's history answered from a slice that lacked
the final outcome; an open topic left out sub-areas), identity 2 (a short-name twin reported
as "no such entity"; kinship questions answered from thin role nodes instead of the
well-populated named nodes), data 1 (no place containment, so a hand-made city list included a
place only watched on TV and one a colleague visited), synthesis 1 (notes paired with the
wrong year), plus one work/personal scope mix-up. Every question was answerable from the notes
alone. The drafted expected answers carry 18 definition or scope points for the user to
confirm before they are used as a benchmark.

Needle questions in the chunks-only domain held up: the 4 unrated needle turns were all
correct.

### Scenario ids

S1 gains P47; S3/S24 gain P37, P39; S5 gains P40; S19 gains P42, P43; S23 gains P36; new
scenarios S26 (acronym or alias for a document in a chunks-only domain), S27 (batch claim
testing over a deck), S28 (status poll on a background job), S29 (named recurring event
across years).

## Round 8 — 2026-11-05 (brain01, one conversation, 3 admin turns; a single user-found issue)

Not a full collection round. The user spotted a miss in one conversation (graph domain,
admin console) and asked for it to be recorded here as evidence for the planner / executor
design. Probed read-only with `ARTMIND_NO_PROXY=1`; the trace ids are in the vault's
registry and are deliberately not copied here.

### The sequence

1. **Turn 1, count question** (count of conflict episodes between the narrator and PERSON_A, plus what happened): 12 agent turns; one `entity-context` (hub entity, over the output
   budget), three `vector-text` searches. The answer counted 3 and found a different set of
   notes than the graph holds.
2. **Turn 2, existence follow-up** (why were conflict episodes not EVENT entities?): `entity-resolve` on a phrase built from the user's word
   (`--topK 10`), then two `pattern7` name searches (`TERM_E`; `TERM_A OR TERM_B OR TERM_C OR
   TERM_D`). Reported four conflict EVENT entities and said the fifth (a TERM_X event on one date)
   "may have been extracted as something other than an event, or not at all". **Wrong:** the
   entity existed.
3. **Turn 3, user correction**: user pointed at the source note; `pattern10` + `text2cypher`
   found the EVENT. The agent then said it had missed it because its "name and description
   searches didn't use those words". The user asked whether the entity-description
   embedding search should have caught it. It exists and ran (see P55).

### Patterns observed

**P55. A rare-word query misses an existing entity whose name and description use common
synonyms; the semantic leg does not cover the gap, and the agent cannot tell.**
Verified against the data (entity embedding present and current: `embedding_stale` false,
current `_embed_key`; entity is in `entity_embedding`). `entity-resolve` = fulltext + vector
(RRF) + exact. Rank of the target entity (type `conflict`, name "Conflict with PERSON_A on
DATE", description "a disagreement between … regarding household autonomy …") in a
topK-100 fused list, by query:

| Query shape | Rank | Legs that matched |
|---|---|---|
| paraphrase of the description ("a disagreement between the narrator and PERSON_A") | 1 | vector, fulltext |
| three concept words (argument conflict disagreement) | 3 | vector, fulltext |
| description-like phrase with a kinship noun and a topic ("quarrel with wife about household freedom") | 2 | vector, fulltext |
| the agent's query (`TERM_E with PERSON_A`) | 7 | vector, fulltext |
| the user's own word + PERSON_A (`TERM_X with PERSON_A`) | 39 | fulltext only (via the name) |
| the user's own word alone | not in the top 100; vector hits are lexical look-alikes (a surname, a food-service word, a metal) | — |

So: (a) the index and the embedding are fine; (b) the embedding model does not place a rare
or informal word (TERM_X) near its common synonyms, and the person's name dominates the
vector of a name-plus-word query, so the nearest vectors are other entities about that
person; (c) with `--topK 10` the target sat at about rank 7–10 of the fused list, the edge of
what the agent read; (d) the agent used the user's literal word and one near-synonym, never a
paraphrase of the concept, and **reasoned from absence** ("not in the name or description
searches") to "not extracted". Nothing in the output said the search was weak.

**P56. Absence is asserted from a thin probe.** The turn-2 answer stated a hypothesis about
the extraction (framed as a grievance, "may not have been extracted") for something it had not
looked for properly. Two more calls (a paraphrase query; or `pattern10` on the dated note)
would have settled it. Related to P36/P37 (silent wrong resolution) and P50 (floor hedges),
but here the claim is about the graph's contents, which is checkable with one call.

### Consequences for the design (proposals, not decided)

1. **Existence and recurrence of a conceptual thing is a fan-out.** The executor should
   run the concept in several phrasings (the user's word, 2–3 common synonyms, a
   description-style paraphrase with the entity class and no person name), union the
   results, and rank by best-of. This is lesson 2.1(4) of `12-round7-lessons.md`
   (fan-out is the main program shape) applied to `entity-resolve`; scenario S30.
2. **Existence checks read deeper than top 10.** Raise `topK` for an existence step (25–30
   costs a few KB) or report the margin to the cut-off.
3. **Strip the participant's name from the semantic probe.** Run the concept without the
   person (the person scopes the result afterwards, via the relation), or run both and
   union.
4. **A negative must carry its probe.** The plan's evidence bundle should state which
   phrasings and depth were tried, so "not found" reads as "not found under these probes"
   and the agent cannot upgrade it to "not extracted". Fits the evidence-bundle / guard
   vocabulary of design §5.4.
5. **Out of scope for now, worth measuring:** a different embedding model, or adding the
   entity's `context` (the source sentence) to the entity embedding text
   (`ingest.entity_embedding_text` embeds `name: description` only). Here the source
   sentence uses the user's own word, so it would likely have matched. A retrieval-eval
   candidate (`retrieval eval`), not a quick fix: it changes every entity vector and
   `PREFIX_VERSION`.

### Scenario ids

New S30 (existence of a conceptual thing named with a rare or informal word, graph domain).
S25 (existence/recurrence) stays ✅ for exact names, but is ◐ for conceptual words: P55.
S24 gains P55 for completeness checks. Open for the labelled set (W0): add this shape,
with the paraphrase and the literal-word variants as separate labels, to
`plan_labels.yaml` / `retrieval_labels.yaml` in the vault, not here.

## Round 8 (collection) — 2026-11-05 (desktop: brain01, nbs_audit_2026), first round on P1b

The first round on the bounded-output build (P1b, merged `123de45`; the `--shape` convention
fix `4a15e86` came after most turns). Same desktop, same vaults as round 7; all turns on the
`claude-sdk` backend, brain01 on Haiku 5.5 and nbs_audit_2026 on Sonnet 5.5, each vault's
admin UI wired to its own `serve` (checked by port and `/health`). Analysed per turn from the
events with scripts that print counts only; the per-turn table and the notes stay in the private
analysis folder. The P55/P56 turns above are part of this round.

### Coverage

| Vault | Turns | Conversations | Rated | Notes | Cost | Median turn |
|---|---|---|---|---|---|---|
| brain01 (graph) | 15 | 9 | 10 (5 good, 5 partial) | 5 | USD 0.25 | 42 s |
| nbs_audit_2026 (chunks-only) | 32 | 19 | 12 (12 good) | 0 | USD 9.55 | 30 s |

47 turns, 22 rated (47 %, from 7 % in round 7). Per-turn cost is now the turn's own (no turn
shows the session total, and none is negative). The nbs cost is dominated by one batch
conversation (8 turns, USD 4.4).

### Bounded output: the P1b gate

| Measure (agent's own calls) | Round 7 | Round 8 |
|---|---|---|
| retrieval results the harness saved to a file (`persisted`) | 158/158 `vector-text`, 34/35 survey | **0 of 449** tool results |
| results over 30,000 characters | most | **0** (largest 28,128, an `insights show`) |
| tool-gate refusals | 29 syntax failures, `cd` refusals | **0** (4 `cd`-wrapped calls rewritten) |
| `--shape brief` on the four retrieval commands | — | **270 of 270** calls |
| `query page` after a cut | — | 6 calls (brain01 4, nbs 2) |
| `survey --expand` | — | 21 calls (nbs) |
| median calls per retrieval turn | brain01 ~8, nbs ~6 | brain01 5, nbs 4 |
| retrieval turns with mechanical struggle ≥ 1 | ~59 % (77 of 131) | brain01 6 of 13, nbs 4 of 21 |

The gate holds: nothing the agent ran was hidden from it, and the agent used the brief shape on
every retrieval call, paged when told to, and opened documents by `--expand`. Struggle fell most
in the chunks-only vault, where the brief survey replaced repeated searches.

### Patterns observed

**P57. Brief survey cut under `serve` (defect, fixed).** 22 of 46 surveys in nbs came back
`truncated`, showing 41–49 of 50 documents. Under `serve`, the survey's row allocation read
the daemon's own environment (no budget) while `_echo_json` applied the caller's 24,000, so
the allocation planned for no limit and the echo cut the tail. Fixed in `14537ea`: the
allocation reads the per-request budget. Rechecked through a test `serve`: 50 rows, about
23,000 characters, no cut. Each cut was announced and pageable, so no answer was silently
short, but the deepest documents were not shown and the agent never paged a survey.

**P58. Graph commands, not retrieval, now fill the budget.** In brain01 the cut results were
`pattern1`, `pattern2`, `pattern7`, `entity-context`, `insights list/show` and `text2cypher`
(20 cut results, 14 of them over 20,000 characters). They have no brief shape, so one row can
be large, and a "draw all my friends" question paged through 206 rows in two calls. The two
Partial ratings on that conversation said the graph was smaller than expected: the agent stopped
paging and drew from what it had (P44 again, at a larger scale). Consequence: graph-row
compaction (an entity row's id, name, class, one-line description) or a `--shape brief` for the
graph patterns; and an enumeration program that pages to `rows_total` (P2 spec §5.5).

**P59. A coverage miss on a profile question, and a budget cut to zero rows (defect, partly
fixed).** One brain01 Partial: an entity's career progression missed one event.
`entity-context` for that hub entity returned **zero rows**: its payload is one row of about
25,000 characters (entity data, 78 connection summaries, 25 connections, 8 chunks plus 20 more,
10 source documents, none over 6,000), larger than the 24,000 budget, and the cut dropped the
whole row. The agent did not page and fell back to two `vector-text` searches that did not reach
the event. Fixed in `7753a83`: the cut never answers with zero rows when the payload
had some (it shortens long strings and keeps at least the first row); the hub row now comes
back whole at about 25,700 characters, over the budget but under the 30,000 inline limit, with a
warning. The real fix is a brief shape for `entity-context`'s connections and `more_chunks`
(today only its chunks are shortened) and, in P2, an entity-profile program that checks
`entity_docs` vs `docs_cited` (P50 family).

**P60. Event windows (lesson 41, again).** One brain01 Partial: a "did we go to PLACE during
FESTIVAL" question was answered yes, but without naming the festival weekend. The trip note is
dated inside the festival window but does not name the festival; the answer could not say why
it counted. The calendar slot (P2 spec §4.7) gives the window and the reason.

**P61. CLI usage errors are now the only errors.** 7 in 449 results: `chunks --shape brief
--idList` without `--domain` (6) and `query page … --domain` (1). Both are the agent adding
or omitting an option the skill example did not show. `chunks` needs `--domain` while ids are
global; `query page` takes no domain. The chunk query does not filter on domain at all (`graph_query._chunks_query` matches by id;
`--domain` only labels the output), so the required option is pure friction. Consequence: make
`--domain` optional on `chunks` (it is derived from the chunks), and the skill's paging line
should say to copy `next` verbatim.

**P62. Background sub-agents escape tracing.** The batch claim conversation (nbs, 8 turns) ran
7 background sub-agents in one turn, then 105 calls in a turn that lasted 4 s: the sub-agents'
calls, recorded under a later turn while it ran. 4 turns show calls but no spans, because the
trace context file is cleared when a turn finishes, while background agents keep calling. 210
sub-agent calls; spans exist only for those made while their own turn was open. This is round
7's P46 (batch claims via sub-agents), unchanged by P1b: the batch program (`query run --each`)
remains the fix, and until then a background agent should inherit the turn's trace id rather
than the session's file.

**P63. Status and context-only turns are frequent and still planned as search.** nbs had 9
turns of one to five words ("done?", "status", a bare acknowledgement), 7 in the batch
conversation; all were shadow-planned `search` (6 at low confidence). Intent `status` and
context-only replies (P2 spec §3) cover them.

### Shadow plans

brain01: routes enumeration 5, procedure 4, existence 2, others 4; 5 high, 10 low. nbs: 29 search,
3 survey; 9 high, 23 low. High-confidence agreement with the agent's first call (as `traces
stats` computes it): nbs 0.89 (contracts) and 0.75 (team responses), brain01 0.0 (the one high
turn that week disagreed). Too few rated turns per route to move the confidence threshold.

### What this round settles and what it does not

- **Settled:** the bounded-output mechanism works on both vaults and both models; brief rows,
  paging and `--expand` are used without prompting beyond the skill.
- **Not settled:** P2's numeric settings (spec §11). 22 ratings, 17 of them on first turns, none
  with an expected answer in nbs; the needle margin has no new labels; the asset thresholds no
  new topic-page turns rated. Round 8 keeps every §11 default.

