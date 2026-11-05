# 12 — Round 7 lessons for the retrieval redesign

Written 2026-11-04 from round 7 (`02-trace-evidence.md`, patterns P34–P49): 181 turns from
laptop B and the desktop, brain01 (graph domain) and nbs_audit_2026 (chunks-only), every
turn judged from its event stream for struggle, key answers checked against the vault data.
This file is the input to the P2 spec and to revisions of the architecture design
(`docs/superpowers/specs/2026-11-02-retrieval-curation-architecture-design.md`). Same privacy
rule as the rest of this folder.

## 1. The picture in one paragraph

About half of all retrieval turns struggle, and most of that struggle is mechanical, not
intellectual. The agent cannot see large outputs (P34), composes wrong command lines or gets
refused (P35), loops one command over a list it could have fanned out (87 % of struggle
turns), fills search slots with the wrong words (P39), and resolves names to the wrong node
without noticing (P36, P37). A deterministic route would have avoided most of it: 66–91 % in
brain01, 72 % in the chunks-only vault, 6 of 7 in the benchmark. What remains is genuinely
hard: unresolved terms and aliases (P38), coverage of cross-document questions (P43), and
synthesis that hedges or buries the latest clause (P44). Correctness itself is still thinly
measured (P49).

## 2. Lessons, by design component

Each lesson: claim · evidence · consequence for the design · confidence.

### 2.1 The executor and the evidence bundle (design §5.4) — confirmed, and sharpened

1. **Run the plan; don't suggest it.** High-confidence plans were the agent's first call in
   19 of 37 first turns; a seen view was not run (P47); 29 calls failed on syntax (P35) ·
   the executor owns command syntax and runs the deterministic part; the agent never composes
   CLI lines for a planned route · high.
2. **A byte budget is a hard requirement, not an optimisation.** 158/158 default
   `vector-text` and 34/35 survey(50) outputs were persisted and mostly unread (P34) · the
   bundle has a per-step and per-bundle byte budget (well under 32 KB to the agent), returns
   one compact row per document plus passages only for the top k, and pages the rest on
   request; `vector-text` defaults to passage context inside the executor; `payload_bytes`
   and `persisted` join the guard vocabulary · high.
3. **The tool gate must not be in the way of planned steps.** Refusals of `cd <vault> &&`,
   pipes, and reads of persisted output broke turns (P35) · the executor calls commands
   in-process, so the gate only governs the agent's free-form reasoning path; on that path
   strip a leading `cd <vault> &&` and allow reading the agent's own persisted results ·
   high.
4. **Fan-out is the main program shape, not best-of.** Loops were per-document `pattern10`
   (up to 40×), per-phrasing `vector-text`, per-name `entity-resolve` · plan programs need
   `map` over a list (documents, years, twins, phrasings) with `union`, and a budget; `best_of`
   is secondary · medium-high.
5. **Rows must carry what the agent needs to cite.** `chunks` rows carry no document title,
   so the agent ran surveys just to map ids to names · every evidence row in the bundle
   carries document id, title, domain and date · high.

**Harness facts behind lessons 2–3 (checked against the Claude Code / Agent SDK docs,
2026-11-04; values to re-verify before use):**
- A successful Bash result is shown inline up to about 30,000 characters. Beyond that the
  agent gets a 2,000-character preview and a path to a file under `~/.claude/projects/…`,
  which the tool gate refuses to read. A **failed** command (non-zero exit) gets only about
  10,000 characters, head and tail, and no file.
- `bashOutputMaxChars` (settings, 4,000–128,000) raises the inline ceiling;
  `BASH_MAX_OUTPUT_LENGTH` (env, up to 150,000) raises only the read-back window and is ignored
  when the setting is present. Both can be passed from the SDK backend
  (`ClaudeAgentOptions(settings=…, env=…)`), not yet tested end to end.
- An in-process SDK tool (`@tool`) is limited to about 25,000 tokens (`MAX_MCP_OUTPUT_TOKENS`)
  and a 50,000-character persistence threshold, raised per tool with
  `ToolAnnotations(maxResultSizeChars=…)` (up to 500,000). No built-in paging exists for
  tool results.

Options for R7-10, cheapest first:
1. Allow the agent to read its own persisted-result files in the tool gate, and raise
   `bashOutputMaxChars` (for example 60,000) in the SDK backend.
2. Compact survey output: one row per document for all 50, passages for the top 10.
3. Expose `query run` (and the read commands) as an in-process tool that returns a bounded
   page plus a result handle, with a second tool to fetch more pages by handle. This removes
   the file round-trip, and it is where the executor's byte budget lives.

Every result stays in context for the rest of the conversation, so (1) alone raises cost on
every later turn. (2) and (3) are the lasting fix.

### 2.2 The planner (design §5.3)

6. **Intent needs the previous turn.** 89 of 181 turns are follow-ups; 12 context-only replies;
   8 status polls; 17 of 17 non-retrieval turns in the chunks-only vault were planned
   `search` at confidence 1.0 (P41) · intents: retrieve, follow_up_format, follow_up_why,
   curate, update, meta, plus **status** (a background job is open) and **ops** (ingest,
   re-extract); the planner takes the previous turn's intent and any offer it made as input;
   a context-only reply inherits the offer · high.
7. **First person is mapped in code.** P36 · the narrator mapping from `domains/plan/<domain>.yaml`
   fills person slots in views and entity steps; the agent never resolves "I" itself; the
   owner-named stub is flagged as an identity gap (and merged by curation) · high.
8. **The identity guard expands, it does not ask.** P37: twins decided 10 of 65 answers, often
   silently · expand same-class twins (casefolded name, name with/without surname,
   kinship variants via the `aliases.yaml` kinship list) into a `union`, report the split in
   `warnings`, and file a same-as proposal; populate the plan's `twins` field (empty today)
   · high.
9. **The search-term slot needs its own logic.** P39 · strip question filler; use the user's
   mention, not the top entity's name; expand a category to its members from the catalogue
   (class members, relation targets); a guard "rows < k → retry with expansion" · high.
10. **Survey cues must cover user wording.** P42 · add "survey", "various", "all the …",
    "which documents", plural document-type subjects ("the contracts say"), "across", and the
    responsibilities/obligations shapes; in chunks-only scopes, see lesson 13 · high.
11. **Route confidence is not plan quality.** High-confidence plans: route right 8/8, first
    step adequate 4/8; in chunks-only domains route confidence is fixed at 1.0 (P48) · the
    plan's confidence is the minimum of route, domain and **slot quality** (filled from the
    mention, twins handled, search term non-empty after filler removal) · medium.
12. **Lexical first; the decision model is not yet earning its place.** Lexical replay 0
    wrong on the benchmark labels; decision model 1 right of 4 high-confidence plans; 60 s
    latency spikes (P48) · keep lexical as the default classifier per vault until replay on
    confirmed labels shows the model ahead; keep the model in shadow · medium (small n).

### 2.3 Coverage (design §5.3 d, as amended 2026-11-03)

13. **Evidence-shape signals do not separate needle from survey here.** Concentration and level
    agreement are flat across classes; only a sharp score drop is informative, and only as
    "this is a needle" (P43) · the coverage guard cannot rely on concentration. Instead:
    in chunks-only scopes run `par(vector-text passage, survey)` and union, which costs
    needles little (needle document in survey top 10 for 8/10) and lifts coverage at depth 10
    from 27 % to 43 %; stay needle-only when relevance(1) − relevance(2) ≥ 0.03 · medium
    (10 + 10 labels; single-chunk-heavy corpus).
14. **Depth matters, but the agent can't read depth.** Survey coverage: 43 % at 10, 57 % at
    20, 82 % at 50 (P43), yet survey(50) was never read whole (P34) · the bundle returns all
    50 as one-line rows (title, date, one-sentence summary, best passage id) and full passages
    for the top 10; the agent asks for more by id · high.
15. **In graph domains coverage is entity-anchored.** brain01's coverage misses were "all
    visits / all illnesses of X", where the entity's own document count is known · add
    `entity_docs` vs `docs_cited` and paging to `rows_total` to the guard vocabulary · medium.

### 2.4 The catalogue and answer assets (P1, built)

16. **A title gazetteer with acronym expansion and an alias table are missing catalogue
    parts.** P38 · catalogue gains `terms`: document titles split into words with initialisms
    (a two-letter "X&Y" initialism ↔ the two title words it abbreviates), and a per-domain alias record (`domains/plan/<domain>.yaml`
    or a curation file) filled from follow_up_why turns through a proposal; the planner's
    mention matching reads both · high.
17. **Asset match thresholds from real phrasings.** `assets --like` on user wording: ≥ 0.80
    always relevant, 0.70–0.80 mixed (highest irrelevant 0.768), < 0.70 mostly not · N2:
    ≥ 0.80 is a match (the page is a guide in the bundle); 0.70–0.80 is "offer the page"
    beside retrieval; survey's `topics` overlap is normalised by page size (hub documents
    dominate it today) · medium.
18. **Topic pages help when they are reached.** Both turns that used a page were rated good;
    in 6 more a matching page existed and was not used · the asset match in the plan is worth
    building · medium.

### 2.5 Synthesis and the answer contract

19. **Lead with the latest, broadest clause; page instead of hedging.** P44 · the answer
    contract (skill + bundle) says: when sources are dated or superseding, state the latest
    applicable one first; when a list is truncated or coverage is thin, the executor pages or
    widens before the agent writes, and any residual incompleteness is stated with numbers
    ("12 of 26 documents read") · medium-high.
20. **History questions are a route, not a search.** P40 · `entity_history`/`time_history`
    programs run the history commands; text2cypher is never the history route · high.

### 2.6 Curation (design §5.6–5.7)

21. **Inbox, with post-apply verification.** P45: 24 % of brain01 admin turns; two false merge
    claims · Inbox cards; every accept runs the command, then re-reads the graph and shows the
    result; chat only hands off to a card · high.
22. **Gaps found in Q&A feed curation.** Identity misses (P37) and alias misses (P38) were
    found by the user in follow-ups (8 of 12 why-turns named the missed item) · follow_up_why
    with a named item re-runs the plan with a must-find, and files an identity or alias
    proposal (N9) · high.

### 2.7 Batch and background work

23. **Batch claim testing needs a program, not sub-agents** (evaluated in §2.11). P46: USD 9–14 per session,
    about 300 untraced calls · a budgeted batch program (`query run --each <claims file>`
    shape): per claim a fixed program (passage search + survey), a cap on calls and bytes,
    results traced as one span per claim · medium.

### 2.8 Measurement (design §5.9) — the gaps block the gates

24. **Instrumentation the redesign needs first** (P49): per-turn cost (not cumulative);
    model and cost on ACP turns; errors as `status: error`; survey/topics evidence ids in
    spans; `payload_bytes` and `persisted` per span; spans for sub-agent calls; intent and
    turn index on the turn; feedback as one upserted row with the note; `review verify`
    fails a run with no answer · high.
25. **Struggle is a review signal, not a grade.** The mechanical score agrees only weakly
    with ratings, and a fast wrong answer scores low · use it to pick turns for review and for
    regression tracking ("turns at S2+ per week"), never as correctness · medium.

### 2.9 From the verification against the data (P50–P54)

26. **A count is only emitted from exhaustive evidence.** P50: 3 of 3 verified counts
    undercounted behind a "floor" hedge · count and enumeration programs page to `rows_total`,
    filter by subject (the narrator vs family), and the executor refuses to return a count
    from truncated evidence; it returns "N found in M of K documents read" instead · high.
27. **Event views pair departure and return; they do not tune a merge window.** The trips view
    gives the narrated count only at one merge window; others split trips; an implied trip has
    no edge · view definitions model the event (departure→return), and answers separate
    stated from implied events · medium.
28. **Identity is resolved before retrieval, seeded from the vault.** P51 · seed same-as from the
    vault's self-description note and a kinship alias table; a resolve rule maps the owner's name
    to the narrator; the misnamed node is a curation item · high.
29. **The gazetteer reads folders and index tables, not only titles.** P52 · the catalogue's
    `terms` part indexes folder paths and code→name index tables as well as titles (lesson 16) ·
    high.
30. **"Latest" is a program: collect, order by date, answer from the newest.** P53 · a `latest`
    program gathers every amendment of the target, orders by document date and the supersedes
    chain, and puts the newest first in the bundle · medium.
31. **Conversion QA covers image tables, and the agent is told when a value is suspect.** P54 ·
    the page check flags reversed-glyph numeric tokens; a flagged value reaches the bundle as
    "suspect conversion", never as "the source says" · high.

### 2.10 Aliases: what exists, and the gap (user decision R7-5)

What artmind already has (checked 2026-11-04):

| Mechanism | What it maps | Scope | Read by |
|---|---|---|---|
| Same-as groups (`.artmind/same_as.yaml`, the curate skill) | several entity keys → one entity | graph domains, entities that already exist | the projection; merged names become the entity's `aliases` |
| Entity `aliases` property | every raw name an entity was observed under, plus declared aliases | graph domains | the catalogue name index, hence the planner's gazetteer |
| `artmind update` (draft/confirm) | a user-stated fact, which can carry aliases for an entity | graph domains | the projection |
| Schema `property_aliases` | property-key variants → one key | extraction, per schema | extraction and `ingest retime` |
| `insights/aliases.yaml` | kinship words (dad/daddy/father …) | all personal vaults | only the insights same-as detector |

The gaps the traces hit:
1. **A nickname that never appears in the corpus** (P38, P52: an acronym or a contract nickname used
   only by the user). Same-as cannot hold it: there is no second entity to merge. In a
   graph domain, `update` can attach it as an alias of the entity; nothing does that
   automatically from a correction.
2. **Chunks-only domains have no entities**, so none of the entity mechanisms apply; the alias's
   target is a document or a set of documents.
3. **The kinship list is not used by retrieval**, only by an insights detector.

Design, reusing the above rather than adding a parallel store:
- **Entity targets (graph domains):** an alias proposal from a `follow_up_why` or `curate`
  turn becomes either a same-as proposal (both names exist as entities) or an `update` draft
  that adds the alias to the entity (only one exists). Both already travel between machines
  and feed the catalogue name index.
- **Document targets (any domain):** a new curation record kind,
  `.artmind/data/curation/aliases/<id>.json`: `{term, domain, targets: [doc ids], source:
  trace id, decided_by}`, written through the Inbox, travelling by vault sync like other
  curation records, and read by the catalogue's `terms` part (lessons 16, 29) together with
  titles, folder paths and index tables.
- **Kinship words:** the planner's identity guard reads `insights/aliases.yaml` (and the
  per-domain extension) when expanding twins (lesson 8).

### 2.11 Batch claim testing, evaluated (refines lesson 23)

Two sessions tested one deck's 17 claims (about 44 and 57 assertions) through background
sub-agents. On a sample of 17 assertions checked exhaustively against the corpus: session 1
had 9 right, 1 partial, 6 wrong; session 2 had 15 right, 2 partial, 0 wrong; a re-run of 6
had 4 right, 1 partial, 1 wrong (an over-correction). Errors: missed document 5,
superseded version or version chain 3, synthesis or citation 4, alias 1; draft-versus-executed
status mattered in 5 of 11. Between the sessions, 41 non-independent documents (the deck
itself, sibling decks, the vendor's own responses, a register) moved out of the domain; with
them in, they became session 1's "support".

A fixed program run read-only (passage search top 10 + survey top 10 per assertion, no LLM,
about 1.4 s) reached 13 of 17 with contract wording and 9 of 17 with the deck's own wording.
That is an upper bound, since the queries were written after reading the sessions. It still
missed the two cases decided by a later amendment. The estimated full-program cost is about
USD 0.10–0.15 per claim, against a recorded 0.53 (judge calls not run, so not measured).

32. **The batch program needs a rewrite step** (deck wording → corpus wording, using the
    catalogue's aliases and terms): 7/15 verbatim vs 11/15 rewritten · medium.
33. **Evidence carries executed/draft status, and the program has a "latest executed /
    latest draft" step per document family** · 5 of 11 errors · medium-high.
34. **Scan inside the top documents before judging:** twice the right document ranked first,
    but the passage shown was not the clause · medium.
35. **Exclude the claim's own source and flag non-independent documents:** 6 of 17 wrong with
    them in the domain, 0 without · medium.
36. **Absence claims need an exhaustive exact-term sweep**, one call over the full text, not
    10–40 searches ending at "medium confidence" · medium.
37. **Bytes, not queries, were the waste.** 23 of 23 surveys went unread in context, plus 72
    re-parses and 8 re-reads of the deck; one capped bundle and one span per assertion · high.

## 3. Answers to the P2 evidence needs (`11-p2-evidence-needs.md`)

| # | Answer from round 7 | Confidence |
|---|---|---|
| N1 | Six intents hold; add `status` and `ops`; context-only replies inherit the previous turn's offer; lexical cues catch about 15 of 17 non-retrieval turns in nbs, cues listed in the private analysis | medium-high |
| N2 | ≥ 0.80 match, 0.70–0.80 offer, < 0.70 none (user phrasings) | medium |
| N3 | Not concentration. Chunks-only: `par(passage search, survey)` union by default; needle-only on a sharp score drop. Graph: entity-document coverage and paging | medium |
| N4 | Identity decided ~15 % of brain01 answers; expand twins into a union, warn, file a proposal; never ask about the narrator | high |
| N5 | Programs needed: view counts (union over years + text check), kinship aggregates (union over twins), enumerations (page to `rows_total` + text union), entity visits (document coverage), chunks-only cross-document (`par` + union), batch claims (map with budget). One step suffices: entity profile, existence, named document | medium-high |
| N6 | Route confidence alone is uninformative; include slot quality; threshold unchanged until confirmed labels exist | medium |
| N7 | Lexical default on both vaults; decision model stays in shadow | medium (small n) |
| N8 | `route_stats` needs: turns, S2+ share, escalations, ratings, payload bytes, agreement per route | medium |
| N9 | Yes: must-find re-run closes 6 of 8 brain01 and 2 of 3 nbs corrections; the rest need an identity or alias fix | medium |

## 4. Changes this implies for the architecture design

- §3 principles gain **"bounded output"**: no planned step may hand the agent more than its
  byte budget.
- §5.3 (c) gains a `map` operator and the guard predicates `payload_bytes`, `persisted`,
  `entity_docs`, `docs_cited`, `rows_below_k`; (d) replaces the evidence-shape coverage rule
  with lesson 13.
- §5.3 (a) gains intents `status` and `ops`, and the previous turn as input.
- §5.1 catalogue gains a `terms` part (title gazetteer, initialisms, alias records).
- §5.4 bundle rows always carry document title and date; survey depth as compact rows.
- §5.9 / P1a: instrumentation items of lesson 24 move **before** P2, since the P2 gate needs them.

## 5. Fixes worth doing now (independent of P2)

| Fix | Why | Size |
|---|---|---|
| `vector-text` default to `--context passage` for the agent (skill line and the planner's templates) | P34, lesson 2 | small |
| Revert the survey default from 50 to 10 (or 15), keep `--topDocs` for depth | P34; the observation it was set for is done (coverage-by-depth measured) | small |
| `chunks` rows carry document title | lesson 5 | small |
| Tool gate: accept a leading `cd <vault> &&`; allow reading the agent's own persisted result | P35 | small |
| "survey" and the user-wording cues as survey triggers; anchor "did we/did I" existence triggers at the start | P42, P39 | small |
| `review verify` fails a run with no answer or no spans | P49 | small |
| Feedback: one row per trace, note updates it; per-turn cost delta | P49 | small–medium |
| Map first person to the narrator in brain01's plan config, and curate the owner-named stub | P36 | config + curation |
| Build brain01's summary tree (`ingest summarise`), so survey has its document and chunk legs there | survey runs on one leg of three in brain01 | operator run (LLM calls) |
| Page check: flag reversed-glyph numeric tokens in converted image tables | P54 | small |
| Curate brain01 identities: the owner stub → narrator, the parents' 5+5 nodes, the misnamed node, the friend's 2 nodes | P51 | curation |
| Correct the review dossier whose expected answer drops a narrated event | P50 note | vault edit |

## 6. Questions still open

Questions the data could not settle are put to the user in the session that wrote this file.
Answers are recorded in `06-open-questions.md`, never with private names in this folder.
