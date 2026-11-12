# Classic baseline and skill simplification

Date: 2026-11-08. Status: **closed 2026-11-12** — query skill (orient text), retrieval (`survey`, `find`, topic plans, the mart) and the planner removal are settled; what is still open is listed in §0.1 as carried-over items, not as this spec's work. Built on
`baseline/v0.11-classic` (cut from `v0.11.0`), since merged to master.

Inputs: study note 19 (`docs/query-routing-study/19-retrospective-2026-11-08.md`, carried onto this branch), the
original study objective (note 03 §A–§D, note 08), and the frozen P3 work on `claude/p3-design-a9f633` (to be
tagged `archive/p3-bundle`). Specs named below that are not on this branch are read from that tag:
`2026-11-06-assembly-pass-episodes-design.md`, `2026-11-07-p3-executor-design.md`.

Privacy as in the study folder: this checkout is public. brain01 appears by counts and question ids only;
banking_corpus is a public sample corpus. Benchmark questions, expected answers, judge reasons and miss records
stay in each vault.

---

## 0. In one page

The deterministic planner and the bundle contract are frozen (note 19): they did not beat the classic agent,
and none of their work ever shaped a classic answer. The redesign returns to its **original objective**: a
classic `artmind-query` skill that is short and does not confuse the agent, because the CLI carries the
mechanical rules.

The principle for everything below: **code decides what is mechanical** (syntax, budgets, paging, complete
counts, identity facts, honest outputs, derived structure); **the agent decides what to look for** (vocabulary,
decomposition, iterating on results). A command may report facts and suggest a next command. It never picks the
route.

Order of work:

| Phase | Content |
|---|---|
| 0 | Freeze the P3 branch; shadow planning off |
| 1 | New baseline: carry-over list, the no-entanglement rule, 15 new brain01 questions, tiered expected answers, the measured classic band |
| 2 | P2-0 miss records → P2-1 `orient` → P2-2 `next` hints → P2-2b superseded history (graph-first) → P2-3 identity in `entity-resolve` → P2-4 complete counts → P2-4b enumeration across documents → P2-5 `entity-context` hubs → **composites (episode)** → P2b skill restructure (§4.3a) |
| 3 | Planner removal |
| 4 | Answerability audit, schema and extraction guidance, the second composite kind |
| 5 | DSL thinking (a design note only) |

Every step is sequential and measured against the classic band (§3) before the next starts.

### 0.1 Work plan — closed 2026-11-12

The query skill, extraction and retrieval work this spec started is settled; active work moves to curation
(`2026-11-12-curation-surfaces-design.md`). Final state of each item:

| # | Item | Final state |
|---|---|---|
| — | Phases 0–1, P2-0, P2-1 `orient`, the P2 batch | done; batch run I (study note 22), then the backlog fixes listed in note 23 |
| A | Defect backlog (run I) | worked per question until 2026-11-11 (brain01 Q14, Q28 fixed and others since); not continued as a pass. Each vault's `benchmarking/defect_backlog_runI.md` stays as a record |
| B | Benchmark run J | not run as such: superseded by runs L, M, N (orient promotion), the P2b runs, F/F2 (find) and the ACP run (note 22) |
| C | Promote the orient variant | **done 2026-11-11**: the orient text is `artmind-query`; classic archived at `docs/archive/artmind-query-classic/` |
| D0 | Topic plans for breadth | **done** (spec `2026-11-10-topic-breadth-design.md`, note 24) |
| D | The mart | **done**: brain01 built, banking declared (spec `2026-11-10-mart-design.md`) |
| E | P2b skill restructure | **measured, not promoted 2026-11-12**; `artmind-query-p2b` kept as a variant |
| F | Planner removal | **done 2026-11-12** (dd2bdae4) |
| H | Retrieval gaps | `query find` **merged 2026-11-12** (banking F/F2 54/56 against 59–63); survey passage depth **parked** on `dev/survey-passage-depth` |

**Carried over, not scheduled** (pick up from here, each on its own):

- **E2 turn check** (spec `2026-11-11-turn-check-design.md`): not built; would now be measured on `artmind-query`.
- **G extraction wave** (§6): relationship types the text implies but does not state, missing person-to-person
  links, entity fragmentation, date property projection, the answerability audit. Not started.
- **H remainder**: `vector-text --asOf` cannot narrow (passages carry no date); depth for broad multi-document
  questions; a gate noise allowance for the rows that fail every baseline run.
- **I DSL note** (§7): not written.
- **Topic plans** (note 24, "What is left"): the offer rule does not fire; keyword counts undercount cell depth;
  long split pages; dead code `topics/anchor.py` and the old `gather.gather`; a synthetic test day left in brain01.
- **Agent search persistence**: the ACP run (deepseek flash) ran ~13 commands per turn against Claude's ~5 and
  missed 44 against 54–56 (note 22). Unexplored; one run, no ACP baseline without `find`.
- **Branches kept**: `dev/survey-passage-depth` (parked), `dev/artmind-code-refactor-skill` (on hold; its
  hotspot report and ratchet baseline need regenerating).

## 1. Goals and non-goals

**Goals**
1. A baseline that is exactly `v0.11.0` plus a named list of fixes, with a measured classic band.
2. A classic skill of about 150 lines whose remaining rules are judgement, not mechanics.
3. Graph structure across observations (composites), exposed through existing commands.
4. Miss records that tell the DSL design what domain knowledge was missing.
5. No new dependency on planner code, so phase 3 is a clean cut.

**Non-goals**
- Any routing decision in code: no route, no plan confidence, no executor.
- Lower cost or latency as goals. They are reported, never gates.
- The DSL itself (phase 5 is a design note).
- Changes to the chat UI beyond what the skill-variant switch needs.

## 2. Phases 0 and 1: freeze and baseline

### 2.1 Phase 0: freeze

- Tag `claude/p3-design-a9f633` as `archive/p3-bundle` and push the tag. No further commits on that branch.
- In both vaults' `.artmind/config.env`: `ARTMIND_AGENT_CONTRACT=classic`, `ARTMIND_PLAN_SHADOW=0`, and remove
  `ARTMIND_PLAN_SHADOW_RUN`. Shadow planning with `ARTMIND_DECISION_PROVIDER=openrouter` sends every traced
  question off the machine.
- Done when: the tag exists on the remote; both vault configs show shadow off; a traced admin turn has no
  `plan_shadow` event.

### 2.2 Carry-over list

Applied with `git cherry-pick` unless stated. Commit ids are on `archive/p3-bundle`.

| Item | Commits | Notes |
|---|---|---|
| B9: `views` / `insights` per domain in `domains-overview`; classic skips `views list` at `views: 0` | `5b3f7853` | `graph_query.py`, classic skill, `CLAUDE.md` |
| B8: spans for read-only `db` subcommands | `d5a6e7de` | traces, `cli.py` |
| A `SystemExit` span records its own exit code | `96e28582` | `traces/span.py` |
| Registry migrations under one write lock | `ab1f0879` | `db.py`; conflicts with the contract columns, resolved by hand |
| Skill-variant switch: benchmark runs and trace turns record it; a profile picks the skill by it | `c63afc7e`, `f32001c9`, `41e478d9` | Column keeps the name `contract`; values are skill-variant names (`classic`, `classic-p2-1`, ...). The bundle skill is not carried; a variant names a skill folder |
| `traces compare` and the answer judge | `d0c85b21`, `9187448f`, `fa934078`, `21c46a04`, `4fa83d4a`, `3ce95bf5` | Adapted: see §2.3 and §3 |
| `query survey --passages` cap 3 → 8 | by hand, from `b63aee3a` | one option line and its test |

**Not carried:** P2b and B1–B7; the needle stats (`9ae85129`); nested parent spans; `query run` (including
`--each`); S5–S8; the survey leg; document opening; the bundle skill; the 28,000 budget. The output budget stays
24,000. The survey default of 50 documents stays as in `v0.11.0` (runs A and D used it); lowering it is a phase-2
decision, measured.

### 2.3 Changes made while carrying over

1. **`artmind/benchmark_labels.py`** (new). The label loader (`Label`, the labels file path, `vault_dir`) moves
   here from `query_plan/labels.py`, taken from the **archive's** version: the vaults' `plan_labels.yaml` were
   written by later builds and may carry keys (`follows`, planned routes) that the `v0.11.0` loader rejects.
   Document matching (`match_kind`, `reached_documents`) is taken from the archive's `query_plan/reach.py`, which
   `v0.11.0` does not have. `traces compare` and `retrieval_eval/labels.py` import only this module; the
   frozen `query_plan/labels.py` keeps its own loader unchanged (it is deleted in phase 3).
2. **`ARTMIND_PLAN_SHADOW` defaults to off in code.** A vault that forgets the setting no longer plans in shadow.
3. **`test/test_no_planner_entanglement.py`** (new). It parses every module under `artmind/` and fails when
   one outside `artmind/query_plan/` imports from it, except the seams that existed at `v0.11.0`, pinned by name:
   `cli.py`, `server.py`, `webui/app.py`, `traces/recorder.py`, `catalogue/cache.py`, `retrieval_eval/labels.py`.
   The list may only shrink.
4. **Gate rows** in `traces/gate.py` become the baseline-variant rows of §3.4; cost and latency are reported, not
   gated.

### 2.4 New brain01 questions

- 15 new questions, drafted by Claude from brain01's graph and notes, confirmed by the user, written only to the
  vault's `benchmarking/` files.
- At least two suit `query graph pattern8` (entities of class X connected to entity Y) and two suit `pattern9`
  (top N of a class by connections). The rest target where classic was weak: multi-part questions, counts and
  histories, event windows, follow-ups, first-person questions.
- Drafting checks each expected answer against the graph (`text2cypher`, patterns) and the notes, through
  artmind's own config from inside the vault.

### 2.5 Expected answers in tiers

Every baseline question carries an expected answer with **must**, **optional** and **must-not** items (note 12,
lesson 38). For the 15 new questions Claude drafts them. For existing questions Claude lists notes that are empty
or untiered and drafts only those. The user confirms all of them. Stored with the questions in the vault.

## 3. Measurement

### 3.1 Runs that make the baseline

| Vault | Questions | Classic runs |
|---|---|---|
| banking_corpus | 36 | A `61cf661f`, D `15837ded`, plus 1 fresh run on this branch |
| brain01, original | 14 | A `49bc9ef6`, D `d6c41e2c`, plus 2 fresh runs |
| brain01, new | 15 | the 2 fresh runs |

Recorded with the baseline: A and D ran with the 24,000 budget and the classic skill, which after B9 is the
skill on this branch. Their build differed only in code the classic agent never calls.

### 3.2 Two judgements per answer

1. **Pairwise** (existing judge): for each pair of runs, per question, `same` / `minor` / `material` and which is
   better, read from the two answers against the expected answer.
2. **Absolute** (new): one judge call per answer against its tiers: each must item found or missing, each
   must-not item present or absent, and `correct` / `partial` / `wrong`. Cached in its own table,
   `trace_grades`, keyed by model, prompt version, question, tiers and answer.

Answers are judged whole: the 8,000-character cut is replaced by a ceiling of 40,000, and an answer over it is
reported as unjudged, never cut.

### 3.3 The band

Per vault, over all pairs of baseline runs: the widest worse-minus-better balance and the highest count of
materially different questions. brain01 reports the original-14 band (six pairs) and the new-15 band (one pair);
the vault's band is the wider. Reported beside it: must-item coverage per run, answer reach on labelled banking
questions, median commands, latency and cost.

### 3.4 Judging a variant

**Revised 2026-11-09 after the baseline (study note 20).** Three classic runs of the same skill differed
"materially" on 19–21 questions per pair with no direction, while their must-item rates agreed within 5 points
(banking 69–74 %; brain01 original 55–58 %, new 67–71 %). So the **must-item rate is the primary gate**, per vault
and per question group, against the range the baseline runs span; the pairwise judgement flags answers for the
user to read and supplies the balance check below. Before the first variant gate one baseline pair is re-judged
and re-graded with a Sonnet model; if the band narrows, every gate uses that model. The baseline runs are
re-graded whenever the tiers change, so variant and baseline are always graded against the same tiers.

A step runs its skill variant once per vault, judged against each baseline run. It **passes** when:
- its must-item rate, per vault and per question group, is at or above the lowest baseline run's rate;
- its balance against every baseline run is within the band;
- it loses no must item that every baseline run found;
- no question is materially worse than all baseline runs, or each such question has a dossier.

**Above the band** means its balance against every baseline run is outside the band on the better side
(below −band).

### 3.5 The user reads the flagged answers

Each judging run lists the questions marked material, worse than all, or with a lost must item. The user reads
those answers. The user's rating overrides the judge; disagreements adjust the judge prompt (a version bump).

### 3.6 What the benchmark cannot measure

Each benchmark question runs in a fresh agent, so follow-ups, corrections ("you missed X") and multi-turn
context are not measured; questions that need them are written as self-contained multi-part questions. The
user-stated miss kind (§4.1) therefore comes from admin-console turns only. Multi-turn benchmark entries
(a scripted follow-up after an answer) are a later item, not part of any phase-2 gate.

## 4. Phase 2: tools absorb rules

Each slice adds command behaviour, deletes the skill text it replaces, and is measured as a skill variant
(§3.4). Skill text lives in `artmind/skills/artmind-query/`; a variant is a copy under its own folder until it
passes, then replaces the classic text.

### 4.1 P2-0: miss records

`artmind traces misses [--since] [--run]`: derives miss records from stored turns, one per miss, written to the
vault's `.artmind/logs/misses/` (private) and summarised on stdout by counts only.

| Kind | Signal |
|---|---|
| vocabulary | a search phrase holds terms the question lacks, and an answer source came from that span |
| reformulation | several `vector-text` / `survey` phrasings before the one that produced a cited source; the winning phrasing |
| identity | `entity-resolve` no match, low score or twins; a name the user corrected in a follow-up |
| scope | a re-run with sibling domains; an unknown-domain warning; a cited source outside the first `--domain` set |
| structure | answered from text only while the schema has a class for it; a count from `complete: false` (after P2-4) |
| answer | must items missed (§3.2) |
| user-stated | feedback notes and "you missed X" follow-ups, with the item named |

Two capture additions: each cited source is linked to the span that returned it (provenance already selects
evidence spans; it records the span id), and a follow-up that names a missed item stores the item. No new LLM
call. Not measured as a variant (it changes no answer); its gate is the hermetic suite plus one live derivation
per vault.

### 4.2 P2-1: `artmind query orient`

A fact sheet, not a plan. It replaces, at the start of a conversation: `domains-overview`, `views list` per
domain, the chunks-only check, `db bridge`, the usual `structural-metadata`, and the routing sub-agent: about 90
lines of skill text (the first two commands, the chunks-only exception, Route, the listing half of Views).

Per domain:

| Part | Content |
|---|---|
| identity | name, family parent, siblings |
| kind | `graph` or `chunks-only`, with what that rules out |
| stores | `graph`, `chunks`, `structured`; for structured: tables, bridge classes, store kind |
| shape | document count, date range, top classes with counts, top relation types |
| views | each valid view's name, summary, params, examples, up to a cap; past it, names and summaries, with `views show` for the rest |
| assets | insights snapshot date, topic page count, summaries present or missing |
| composites | the kinds present and their counts (after the composite step) |

Rules:
1. **No question input** in this slice. A later P2-1b may add question candidates (views by lexical overlap
   with the score shown, mention candidates, matching titles), kept only if it measures better. Matching stays
   the agent's.
2. **Own budget**, about 8,000 characters. Domains group by family; over budget, family rows collapse to one
   line and `--domain` expands one. A cut is reported, never silent.
3. **Freshness** from the `v0.11.0` catalogue cache (generation + disk stamp), so one call per conversation is
   cheap; the skill calls it again when the user names a new area.
4. **Honest parts**: an unreadable part is named in `warnings` and absent, never empty. "No views" and "views
   unreadable" never look alike.
5. Read-only; proxied by `serve` like other `query` commands; traced; envelope per CLI conventions;
   `domains-overview` stays unchanged for the plugin.
6. It reads `artmind/catalogue/`, not `artmind/query_plan/`. `catalogue/cache.py`'s one planner import
   (`is_domain_name`) moves into `artmind/domain_config.py` here, shrinking the pinned seam list.

### 4.3 P2-2 to P2-5

| Slice | Command behaviour | Skill text it deletes |
|---|---|---|
| P2-2 | `next` on empty or thin results: `pattern6` → `pattern5 --mode shortest`; 0-row `text2cypher` / `text2sql` → the dry-run comparison; empty `hierarchy` → the `pattern1` property fallback; weak `resolve-key` → wider `--topK`; thin `entity-context` chunks → `chunks --idList` | Fallback ladder rungs 2, 3, 6, 7, 9, 10, 11. Rungs 1, 4, 5, 8 are judgement and stay, shortened |
| P2-3 | `entity-resolve` maps first person to the domain's narrator and returns same-class twins as one `twins` group. The per-domain config loader moves to `artmind/domain_config.py` first (reads the vault's existing `domains/plan/<domain>.yaml`) | the Resolve procedure, the narrator and twin rules |
| P2-4 | `pattern7`, `pattern8`, `entity-listing` take `--all` (page to `rows_total` up to a cap) and return `complete: true/false` | enumeration and count rules, reduced to "answer a count only from `complete: true`, else say N of M" |
| P2-5 | `entity-context` ranks by the question by default and says when a hub's cut hides parts | the Hub entities section |

P2-2 to P2-5 may be measured in pairs; a failed pair is bisected.

**Revised 2026-11-09 (user decision, after variant run H):** P2-2, P2-2b, P2-3, P2-4, P2-4b and P2-5 are built
as **one batch** on top of P2-1 and measured with **one** benchmark run per vault; a benchmark per fix is too
slow. Each slice still has its hermetic tests and a live check in banking_corpus, and deletes its skill text in
the `artmind-query-orient` variant. A failed batch is bisected by switching slices off. P2-4b builds only the
`survey --by` candidate. Composites and P2b stay separate measured steps.

### 4.3a Additions from the baseline (2026-11-09, study note 20)

The baseline's shared misses named five gaps the slices above did not cover.

| Item | Where | What changes |
|---|---|---|
| **P2-2b superseded history, graph-first** | own slice after P2-2 | A dated or "what changed" question is answered through the graph: supersession edges, `document-history`, `pattern10 --includeHistory`, `entity-history`, valid-time filters. Retired documents stay out of vector search by design; a `vector-text` / `survey` result whose topic has a superseded predecessor may *signal* it (`next`: the graph command for the dated version), never return retired text. The slice also checks how supersession reaches the graph: a document the authority register marks superseded but that is still current (KYC v1, runs D and G applied it) is a data defect the slice reports and fixes in ingestion, not in the skill. Evidence: banking Q07, Q09, Q10 (wrong in every run), Q11, Q13, Q25. |
| **"Connected to" routing** | P2-2 `next` hint + P2b routing table | `entity-context` on an entity, when the question lists a class connected to it, hints `pattern8 --entityClass <CLASS>`; the routing table names the shape "CLASS connected to ENTITY → `pattern8`". Evidence: brain01 Q15 (3/9, 2/9 without `pattern8`) against Q16 (7/7, 6/7 with it). |
| **P2-4b enumeration across documents** | after P2-4, measured | "Every year" / "each time" questions answered from text need one result per period, not a sample. Two candidates, measured against each other: `survey --by year\|month` (best documents per period, every period with a hit reported) and routing such questions to the graph's `timeline`. Evidence: brain01 Q01, Q12, Q20. |
| **Depth for broad multi-document questions** | a measured setting, not a slice | Answers keep the shape and drop thresholds, timings and roles. Variables: `survey --passages` (up to 8) and `--expand` of the top documents; one variant per setting. Evidence: banking Q16, Q18, Q22, Q23, Q24. |
| **Near-miss inclusion** | P2b judgement rule; composites | "An event counts for a person only when the notes put them in it" as a judgement rule; episodes give the attendee a member role so the graph answers it. Evidence: brain01 Q01, Q05 must-not hits. |

Order in phase 2: P2-0 → P2-1 → P2-2 (with the `pattern8` hint) → P2-2b → P2-3 → P2-4 → P2-4b → P2-5 →
composites → P2b. The depth setting is measured whenever a variant run is due anyway.

### 4.4 Composites (first kind: episode)

A composite is a derived whole that spans parts: an episode spans events; later kinds may be a trip and its legs
and stays, a festival and its days, a policy family and its versions.

- **Record:** `kind`, name, members (entities or observations) each with a role, evidence pointing at the
  members. For kinds that happen in time, start, end and duration are **computed in code** from the members' own
  dates, never extracted.
- **Declaration per domain**, in the domain schema: `composites: {<KIND>: {members: [CLASS, ...], blocking:
  {...}}}`: which classes may be members and the blocking rule (shared participants, a date window, a title stem).
  `domains validate` checks it.
- **Build:** the assembly-pass spec (`2026-11-06-assembly-pass-episodes-design.md`, on `archive/p3-bundle`) §3–§6
  and §9–§10 as written: code blocking, one bounded LLM confirmation per candidate group, incremental runs,
  curation records under `.artmind/data/curation/` reproduced by `projection rebuild` and carried by `vault sync`.
  **Its §7 (routing) is replaced:** composites are graph nodes the existing commands return. `entity-context`
  shows an entity's composites; `pattern8` lists composites of a kind connected to an entity; `orient` reports
  kinds and counts; recurring shapes become saved views. No route, no step bindings.
- **One kind first** (episode). The record format and pipeline are generic; the second kind is chosen by the
  phase-4 audit.
- Every LLM step shows its estimated cost in its dry run (tier-1 `--dryRun`, CLI conventions).
- **Measured** as its own step: the data change applied to the vault, one classic run per vault, judged against
  the band. Then P2b.

### 4.5 P2b: skill restructure

`SKILL.md` of about 150 lines: grounding rule, `orient` and how to read it, one routing table (question shape →
command → reference), the remaining judgement rules (sibling domains on thin results, search before concluding
absence, survey for spans of documents, when to say the data does not answer), answer style and sources.
Everything else moves to references read on demand: `views.md`, `entity-and-hubs.md`, `composites.md`,
`insights.md`, a `commands.md` generated from the Click tree, and the existing temporal, conflicts, hierarchy and
structured-store references. Prose-pinning tests are replaced by data checks: every command and option the skill
names exists in the Click tree. Measured as a variant; passes when inside or above the band with fewer
commands per turn.

## 5. Phase 3: planner removal

1. Check the entanglement test's seam list: only the `v0.11.0` seams that the cut removes remain.
2. Delete: `artmind/query_plan/`, `query plan`, the `plan` CLI group, the shadow hook in `traces/recorder.py`, the
   decision warm-up in `server.py` and `webui/app.py`, `ARTMIND_PLAN_*` / `ARTMIND_DECISION_*`, the
   `query_plan/*.yaml` package data, the planner tests.
3. Remove the `query plan` special cases in `traces/path.py`, `traces/evidence.py`, `provenance.py`, with a test
   that stored traces holding `query plan` spans still render.
4. Update `CLAUDE.md`, `justfile`, `docs/question-routing.md` (marked historical), CLI guide groups, plugin
   fixtures if any change.
5. Kept: `artmind/catalogue/` and assets, `artmind/benchmark_labels.py`, `artmind/domain_config.py`, the vaults'
   `domains/plan/*.yaml` files, the `contract` column.
6. Gate: full suite green; a restarted `serve` and a traced benchmark turn with spans; one classic run per vault
   inside the band.

## 6. Phase 4: answerability audit, schema and guidance

1. **Audit.** For each baseline question that lost must items, the `artmind-review` shape audit (`review shape`,
   `review reextract` dry run, the answerability matcher) sorts it: (a) the graph holds it and the agent missed it
   (back to phase 2's kind of fix); (b) only text holds it; (c) a schema gap: class, property or relation missing,
   or a relation without direction or participants (note 12, lesson 40); (d) a cross-observation gap (a composite
   kind). Miss records (§4.1) feed it.
2. **Schema and guidance.** Fix (c) in each domain's schema and extraction guidance; re-extract only affected
   documents with `ingest sync --reextract --match`. The loss guard protects against empty re-extraction. Packaged
   schemas change in `artmind/domains/schemas/`; a vault's committed schemas change in the vault.
3. **Second composite kind**, if (d) shows one.
4. Gate: the share of baseline questions whose must items the graph holds rises; the benchmark stays inside or
   above the band; LLM cost reported for each step.

## 7. Phase 5: DSL thinking

A design note only. Inputs: the miss records (which knowledge was missing, by kind), the composite declarations,
and where domain knowledge lives today (`domains/plan/*.yaml`, schemas, same-as files, views). Question to answer:
what a declared domain model would hold, who writes it, and which commands and extraction steps read it. No
commitment to build.

## 8. Risks

| Risk | Mitigation |
|---|---|
| The band is wide, so real changes hide in noise | absolute must-item grading beside pairwise; the user reads flagged answers; 15 more brain01 questions |
| `orient` grows into a planner | no question input in P2-1; P2-1b only by measurement; "facts, not a plan" written into its help |
| `orient` is too large for many-domain vaults | its own budget, family collapse, reported cuts |
| Cherry-picks drag planner code along | the entanglement test; `benchmark_labels.py` and `domain_config.py` extractions |
| Composites change answers in ways that blur skill measurements | applied to vault data as its own measured step, between P2-5 and P2b |
| Composite confirmation sends text to the provider | bounded per group, shown in the dry run; the vault's provider choice applies |
| Stale daemons hide changes | every live check with `ARTMIND_NO_PROXY=1` or a restarted `serve`; UIs restarted before each benchmark run |
| The main checkout drives the global editable install | this branch lives in its own worktree; the user switches the main checkout when ready to run benchmarks on it |

## 9. Decisions taken (2026-11-08)

| # | Decision |
|---|---|
| D1 | Baseline from `v0.11.0` plus the carry-over list; planner removal later as its own phase (A-then-C) |
| D2 | No new code depends on planner code; enforced by a test |
| D3 | Baseline runs: reuse classic A and D; one fresh banking run; two fresh brain01 runs |
| D4 | 15 new brain01 questions drafted by Claude, confirmed by the user, including `pattern8` and `pattern9` shapes |
| D5 | Material changes judged on the answers: pairwise plus absolute against tiered expected answers; the user reads flagged answers |
| D6 | Phase 2 is "tools absorb rules", then the restructure; `orient` first and question-free |
| D7 | Composites generalise episodes; one kind first; sequential, after P2-5 and before P2b; no route |
| D8 | Miss records (P2-0) collected from the start of phase 2 for the DSL |
| D9 | Order: freeze → baseline → phase 2 → planner removal → audit and schema → DSL |
| D10 | (2026-11-09) Must-item rate is the primary gate; pairwise flags answers. Superseded history is answered through the graph, vector output only signals it. Five baseline gaps added as §4.3a |
