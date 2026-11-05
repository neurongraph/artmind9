# Retrieval and curation architecture: the layer above the CLI

Date: 2026-11-02, revised 2026-11-05 from round 7 (`docs/query-routing-study/12-round7-lessons.md`;
revisions are marked "R7" with the lesson number, and the bounded-output mechanism is its own
design, `2026-11-05-bounded-output-design.md`). Status: **architecture draft for review**. Not an implementation spec: it
sets the target shape, splits the work into sub-specs (§9), and lists the decisions that need
the user (§11). Inputs: the query-routing study (`docs/query-routing-study/`, rounds 1–6),
the specs from 2026-10-09 to 2026-11-01 (views, review loop, insights, query plan, document
context, chunks-only, passages, summaries/survey/topics), the four operator skills, the admin
UI and Obsidian plugin as built, and outside work (§12).

Constraint from the brief, as relaxed on 2026-11-02: the CLI's **behaviour** stays as it is.
New commands are added (§6.1), and existing commands may change where that makes the surface
**uniform**: one spelling per option, `--compact` and `--dryRun` where they belong, one result
envelope (§6.2). Every such change keeps the old form working as a hidden alias for one release.
Everything else here is a new data file or a change to the layer above the CLI (skills, admin
UI, plugin).

---

## 0. In one page

Retrieval in artmind is now strong in its parts and weak at the joins. The stores and
retrievers are good: graph patterns, passages with weighted RRF, a summary tree, survey,
views, insights, topic pages. But an LLM agent still chooses among about 40 commands by
reading 9,400 words of skill prose, and the traces show it choosing differently each time
(P1, P26, P31), stopping early (P14, P30), and missing what it was never told to check for
(P21, P29). Curation runs through the same chat, item by item, at minutes and dollars per batch
(P28).

The design makes seven moves:

1. **One catalogue of what the system knows and can answer** (§5.1). It extends
   `query_plan/catalogue.py`: domains, classes, documents, assets, freshness and data health,
   rebuilt when the data changes, not on an hourly clock.
2. **Answer assets as one concept** (§5.2). Views, topic pages, insight findings and stored
   summaries are all precomputed answers with provenance and a staleness rule. The planner
   matches a question against all of them before it falls back to raw retrieval. Topic pages
   gain a graph index (`:TopicPage`) without being ingested as evidence.
3. **Plans become programs, and code runs them** (§5.3, §5.4). `query plan` already
   decides. v2 adds an intent layer, emits a small step graph (sequence, parallel,
   best-of, guarded fallbacks), and a new `query run` executes the deterministic part and
   returns one **evidence bundle**. The agent then reasons over evidence instead of choosing
   commands. This is the fast path; the agent with the routing table stays as the reasoning
   path, now entered only when the plan abstains or a guard escalates.
4. **Every turn leaves something behind** (§5.6). A gap classifier reads the turn (plan,
   evidence, rating, follow-up) and files proposals: a view for a recurring shape, a topic for
   a recurring survey, a same-as for twins met in an answer, a re-extraction for a fact found
   only in text, a label for the planner.
5. **The admin UI is organised around operator jobs, not CLI groups** (§5.7): Ask, Inbox,
   Knowledge, Pipeline, Quality. Every proposal and every write shows a structured preview of
   its effect, and every operation carries the same four badges: LLM cost, reversible,
   scope, travels to other machines.
6. **One question bank per vault** (§5.9) feeds plan replay, retrieval eval and the benchmark,
   and its labels are filled from ratings in the UI, so the gates in §9 can be measured.
7. **A uniform CLI underneath** (§6.2): every command answers in one JSON envelope with
   `--compact`, every write takes `--dryRun` and reports the same `effect` block, and each
   option has one camelCase spelling (old ones kept as aliases). The executor's guards, the
   Inbox previews and the plugin all read that one shape.

The schema builder and ingest skill get a "what kind of domain is this" step that covers
graph, chunks-only, structured, and table-to-graph domains (§5.8).

---

## 1. Where we are (as built on master, 2026-11-02)

| Layer | Built | State |
|---|---|---|
| Stores | Graph (Entity projection over Observations), DocChunk ~7k chars for extraction, DocPassage ~1.2k for retrieval, DocSummary chunk + document levels, structured DuckDB store | Solid. Passages and summaries are new (11-01) and need `embed-passages`/`summarise` per vault |
| Retrievers | 10 graph patterns, `entity-context`, `vector-text` (2:1 RRF + MaxP), `survey` (D/C/P legs), `text2cypher`, `text2sql`, histories, conflicts, hierarchy, timeline | Many overlaps the agent must choose between (study 01) |
| Precomputed answers | Views (2 in one vault), insights snapshots, topic pages (banking_corpus has a set), stored summaries | Each with its own commands and rules; the planner knows views only |
| Routing | `query plan` v1: routes.yaml (18 routes), lexical + Jev decision model, catalogue cache, shadow mode | Shadow only. The skill (9,427 words incl. references) is not wired to it |
| Agent | artmind-query skill, three profiles (qa, admin, benchmark), two backends | The skill still lists views, then improvises |
| Learning loop | Traces (admin, benchmark, chat since 11-01), `traces triage/stats`, review dossiers, plan replay, retrieval eval | Tools exist; data is thin (2 ratings of 49 turns on this machine; P32) |
| Curation | same-as queue, conflicts, synthesis, table classifications; plugin checklist and modals; curate skill (3,333 words) | Works; slow through chat (P28); no previews on approve/resolve/retire/supersede/synthesize/rebuild |
| Ingest | sync/async, chunks-only, table2graph, summarise, retime, reextract; ingestion-helper skill (6,257 words) | Broad; the skill is a situation map, not state-driven |
| Surfaces | Admin UI: chat lane + dashboard tabs (Ingest & browse, Benchmark, Maintenance, Structured, CLI guide, Schemas, Insights, Views); chat UI; Obsidian plugin checklist | Tabs mirror CLI groups; traces, topics, plan, review and the inboxes are CLI-only |

## 2. Evidence that shapes the design

Pattern numbers are from `docs/query-routing-study/02-trace-evidence.md`.

| # | Finding | Patterns | Consequence |
|---|---|---|---|
| E1 | Telling the agent the route does not make it take the route | P1, P26, P31 | Run the deterministic part in code (§5.4) |
| E2 | Coverage is the main gap in the answers: 49 % of expected documents cited in the banking benchmark; survey-shaped questions answered from one search | P14, P16, P30; round 3 table | Coverage guards and survey/topic assets in the plan (§5.3) |
| E3 | Facts the agent cannot know are guessed: domain, narrator, twins, retired versions | P8, P12, P19, P22 | Catalogue facts drive the plan (§5.1) |
| E4 | Identity defects decide answers, and are found in Q&A, not in curation | P19, P22, P29 | Gap classifier → same-as proposal; inline fix (§5.6, §5.7) |
| E5 | Not every turn is a question | P7, P27, P28 | Intent layer above routes (§5.3) |
| E6 | Curation by chat is slow and costly | P28 | Inbox cards, agent for hard cases only (§5.7) |
| E7 | Commands that report misleadingly mislead plans too | P20, P21, P24 | Done in query-plan H1–H4; keep as a rule for new commands |
| E8 | Correctness is unmeasured | P32, open question 17 | Question bank, ratings in the flow (§5.9) |
| E9 (R7) | The agent cannot see large results: 158/158 default `vector-text` and 34/35 survey(50) outputs were persisted by the harness and mostly unread | P34, P35 | Bounded output (§3.8, §5.4; `2026-11-05-bounded-output-design.md`) |
| E10 (R7) | Most struggle is mechanical: loops over lists (87 % of struggle turns), wrong search slots, silent wrong-node resolution | P35–P39 | `map` programs, slot logic in code, identity expansion (§5.3) |

## 3. Principles

1. **Decide in code what code can decide.** The LLM classifies (when lexical rules cannot),
   reasons over evidence, and writes the answer. It does not pick commands or walk a fallback ladder.
2. **Precomputed before raw.** A reviewed view beats a pattern; a fresh topic page beats a
   survey; a survey beats ten `vector-text` calls. The planner checks assets first.
3. **Guides are not evidence.** Summaries, topic pages and insight narrations steer
   retrieval. Every claim in an answer resolves to a passage, chunk, row or observation (the Spec B rule, kept).
4. **Every write is previewed, recorded and travels.** The existing curation-as-files design
   (`.artmind/data/curation/`, `same_as.yaml`) is the model for anything new (topic and view
   proposals included).
5. **Measure before switching on.** Each phase ships in shadow first, with a gate (§9).
6. **No new prose without data behind it.** A routing rule lives in `routes.yaml` or the plan
   programs, with an evidence id, never as a new paragraph in a skill.
7. **Private by default.** Questions, traces, labels, topic pages and proposals stay in the
   vault or the machine; nothing from them enters this checkout. A hosted model sees what the
   operator chose to send (§11, D3).
8. **Bounded output (R7, lessons 2, 37).** No planned step hands the agent more than its
   budget (16,000 characters a step, 24,000 a bundle), and no cut is silent: what did not fit
   is named, counted and reachable through a result handle. Evidence rows always carry
   document id, title, domain and date. Mechanism: `2026-11-05-bounded-output-design.md`.
9. **Exhaustive before counting (R7, lessons 26, 19).** A count, an enumeration or "the
   latest" is emitted only from evidence paged to `rows_total`; otherwise the answer says "N
   found in M of K documents read".
10. **Scope is an explicit input (R7, lessons 35, 39).** Domains, excluded documents and the
    domain's default scope rules are plan inputs the bundle shows, never something the agent
    infers.

## 4. Target architecture

```
                         ┌──────────────────────────── Surfaces ─────────────────────────────┐
                         │  Admin UI: Ask · Inbox · Knowledge · Pipeline · Quality            │
                         │  Chat UI (Ask only) · Obsidian plugin (Pipeline + Inbox subset)    │
                         └───────────────┬───────────────────────────────▲───────────────────┘
                                         │ question / decision           │ answer, previews, proposals
                ┌────────────────────────▼──────────────┐       ┌────────┴──────────────────────┐
                │ Agent (artmind-query, ~150 lines)      │       │ Learning loop                  │
                │  fast path: synthesise the bundle      │       │  gap classifier → proposals    │
                │  reasoning path: plan abstained or a   │──────►│  question bank ← ratings       │
                │  guard escalated → routing table       │ trace │  triage, review dossiers       │
                └───────┬──────────────────▲────────────┘       └────────┬──────────────────────┘
                        │ query run        │ evidence bundle             │ build / approve
                ┌───────▼──────────────────┴────────────┐       ┌────────▼──────────────────────┐
                │ Planner v2 (query plan)                │       │ Answer assets                  │
                │  intent → domains → asset match →      │◄──────│  views · topic pages ·         │
                │  route → plan program (DAG + guards)   │       │  insight findings · summaries  │
                │ Executor (query run)                   │       │  one registry, one staleness   │
                └───────┬───────────────────────────────┘       └────────▲──────────────────────┘
                        │ reads                                           │ built from
                ┌───────▼─────────────────────────────────────────────────┴──────────────────────┐
                │ Knowledge catalogue: per domain classes, relations, documents, names, assets,   │
                │ freshness, health; fingerprinted, rebuilt on change                             │
                └───────┬────────────────────────────────────────────────────────────────────────┘
                        │
                ┌───────▼────────────────────────────────────────────────────────────────────────┐
                │ Stores: graph (entities/observations) · chunks · passages · summaries · tables  │
                └────────────────────────────────────────────────────────────────────────────────┘
```

The database analogy holds throughout, and it is a useful check on each component. The
catalogue is the statistics catalogue a query optimiser reads. Answer assets are
materialised views with invalidation. The plan program is a physical query plan. Guards are
adaptive execution: re-plan when a step's actual cardinality differs from the estimate. The
question bank is the regression workload.

## 5. Components

### 5.1 Knowledge catalogue (the "meta-metadata")

**What exists.** `query_plan/catalogue.py` (spec addendum §11) already builds, per domain,
classes with counts and schema descriptions, top relation types, live and retired document
names, and a name index (with aliases and observation counts). It is fingerprinted (entity,
document and history counts plus `ProjectionState.last_rebuilt_at`), cached in memory and on
disk, checked at most every 10 s, and rebuilt after at most 15 minutes.

**What it lacks for the planner and the UI:**

| Part | Content | Source |
|---|---|---|
| `stores` | graph / chunks-only / structured, per domain (today read from schema files ad hoc) | schema, `db bridge` |
| `assets` | per domain: views (name, summary, examples, params), topic pages (slug, topic, anchor, stale), insight snapshot (`as_of`, detectors present), summary coverage (documents with a document summary / all) | `kg_views.store`, `topics.store`, `insights.store`, a count query |
| `health` | open same-as proposals, twin groups (same class and casefolded name), open conflicts, empty-extraction rate, unembedded passages/summaries, staleness against the vault | existing audits (`projection status`, `staging_audit`, insights health detectors) |
| `time` | per domain, the date range of documents with content dates, and whether `valid_from` is populated (open question 20) | one aggregate query |
| `route_stats` | per route and domain, from traces: turns, S2+ (struggle) share, escalations, ratings, payload characters, agreement (R7, N8) | `traces.analysis` |
| `terms` (R7, lessons 16, 29) | a gazetteer the planner's mention matching reads with the name index: document title words, initialisms (a two-letter "X&Y" ↔ the two title words), folder path segments, code→name index tables, and document-target alias records | titles, vault paths, index tables, `.artmind/data/curation/aliases/` (§5.6) |
| `calendar` (R7, lesson 41) | per domain, recurring events with their date windows, so an event question gets a date range, not only a keyword | `domains/plan/<domain>.yaml` (operator-written) plus the `time` part |

**Freshness: event-driven, not hourly.** The brief proposes hourly monitoring of Neo4j. The
fingerprint already makes staleness detection cheap and immediate, so an hourly job would be
both later and costlier. Proposal: keep the fingerprint, and add a **generation counter**
that every commit, rebuild, same-as approval, view save and topic build increments
(`:ProjectionState.generation`, plus a file stamp for disk-side assets). The catalogue
rebuilds only the parts whose inputs changed. `health` and `route_stats` are slower; they
refresh when `catalogue refresh` runs. The `serve` daemon runs it in the background after a
generation change, debounced to one run per 5 minutes.

**Contrarian check.** For a 20,000-name domain, the name index is the big part, and it
changes only on rebuild. Everything else is small aggregates. The cost risk the brief names
is real only for `health`, which is why `health` refreshes on its own schedule.

**Surfaces.** `artmind query catalogue [--domain] [--part]` (new, proxied, read-only) is
what the agent, the UI's Knowledge area and `domains-overview` (unchanged) read.
`domains-overview` stays as it is for existing callers.

### 5.2 Answer assets

Four things in artmind are already precomputed answers. Each has its own commands and rules,
and only one (views) is known to the planner.

| Asset | Answers | Built by | Stale when | Evidence it points to |
|---|---|---|---|---|
| View | a parameterised recurring question, exactly | operator (create-view skill) | never (live query); invalid when the schema drops a class | rows with ids |
| Topic page | a survey topic, or one hub entity broadly | `topic build` | a source changes or is retired; the anchor's observation set changes | passage ids in footnotes |
| Insight finding | a pattern over time (salience, dormancy, lift…) | `insights refresh` | a new snapshot | subjects' ids |
| Stored summary | "summarise document D" | ingest / `ingest summarise` | the chunk text changes (key) | the document |

**One registry, read-only, derived.** `artmind query assets [--domain] [--kind]` (new) lists
every asset with a common record: `{kind, id, domains, title, answers (summary + examples
or topic text), anchor (entity id or none), built_at, stale, stale_reasons, evidence_kind,
open_cmd}`. It reads the existing stores. It adds no new storage, except the topic index below.

**Topic pages in the graph, as an index, not as evidence.** The brief notes that topic pages
are not ingested (they are in `NEVER_INGESTED`) and therefore cannot be found by search or
traversal. Ingesting them as documents would be wrong: an LLM summary would then come back as
evidence, and every page would cite itself. Instead, a derived index like passages:

- `:TopicPage {slug, topic, _domain, built, stale, text_embedding}` (embedding of the topic
  plus the overview section);
- `(:TopicPage)-[:CITES]->(:Document)` and `->(:DocPassage)` from frontmatter `sources`;
- `(:TopicPage)-[:ABOUT]->(:Entity)` for entity anchors.

The files stay the source of truth (in `_Topics/`, with `## My notes` kept). The index is
rebuilt from them by `topic build`/`refresh` and by a sweep (`topic index`, new), and
dropped and rebuilt like any derived layer. `query survey`'s `topics` block and the planner's
asset match read it. `entity-context` gains nothing (CLI unchanged); the plan adds a
`topics show` step when an entity anchor has a fresh page.

**Topic dedup.** A proposed topic is a duplicate of an existing page when its embedding is
within τ of the page's and their expected source documents overlap (Jaccard ≥ 0.5 over the
survey's top documents). Then the proposal becomes "refresh or extend page X" instead of a
new page. This settles the brief's "AI in healthcare vs ML for disease prediction" worry with
data the system already has.

**Rejected: auto-expire topics after N queries.** Content-hash staleness (Spec B §3.4) is
precise. A use count says nothing about whether a page is still true.

### 5.3 Planner v2: intent, assets, plan programs

`query plan` keeps its contract (`plan_version` bumps to 2, fields added only). Four
additions.

**(a) Intent above route (E5, P27).** A first classifier question, with lexical rules and the
decision model as today:

| Intent | Examples (shapes) | What the plan does |
|---|---|---|
| `retrieve` | any question about the corpus | the rest of the plan |
| `follow_up_format` | reformat, shorten, add a column to the last answer | no retrieval; `steps` empty, `reason` |
| `follow_up_why` | why was X missed, are you sure | re-run the previous plan's guards with X as a must-find; flag for triage (open question 26) |
| `curate` | X and Y are the same; this is wrong; dismiss these | an Inbox action proposal (§5.6), not retrieval |
| `update` | remember that…, add this fact | route to artmind-update |
| `meta` | which model, what can you do | no retrieval |
| `status` (R7, lesson 6) | is it done yet, how far is the job (a background job is open) | the job/status read, no retrieval |
| `ops` (R7, lesson 6) | ingest this, re-extract that | an `ops` proposal with its `--dryRun` effect, no retrieval |

Today all of these get a retrieval route, three of them at high confidence (P27). In round 7,
17 of 17 non-retrieval turns in the chunks-only vault were planned `search` at confidence 1.0
(P41).

**The previous turn is an input (R7, lesson 6).** 89 of 181 round-7 turns were follow-ups. The
planner receives the previous turn's intent, plan and any offer it made ("want me to run a
full survey?"); a context-only reply ("yes", "go on") inherits that offer rather than being
classified alone.

**Slots are filled in code (R7, lessons 7, 9, 28).** First person maps to the narrator from
`domains/plan/<domain>.yaml`, and the owner's own name resolves to the narrator too; the agent
never resolves "I". The search-term slot strips question filler, uses the user's mention
rather than the top entity's name, and expands a category to its members from the catalogue
(class members, sub-types: "doctors" takes in every practitioner type, R7-2/R7-3). Plan
confidence is the minimum of route, domain and **slot quality** (mention used, twins handled,
search term non-empty after filler removal), since route confidence alone did not predict a
good first step (8/8 routes right, 4/8 first steps adequate; lesson 11).

**Domain scope rules (R7, lesson 39).** `domains/plan/<domain>.yaml` holds, beside the narrator,
default scope rules the plan applies as filters and the bundle states: who "we" means; which
event kinds are out unless asked (work events in a family question); that a relationship
question does not take in friction between two other people. Granularity defaults to the
widest the question allows, grouped by level, narrowed only when the question names a level
(lesson 42).

**(b) Asset match before route.** The view question generalises to "which asset answers
this, or none". It offers views, fresh topic pages, and the insight detectors present, from
the catalogue's `assets`. A matched topic page yields `topics show <slug>` followed by
`chunks --idList` for the passages it cites. A matched insight yields `insights show`
followed by `entity-context` per subject. Views keep their own parameter filling.
Thresholds from round 7 (R7, lesson 17, N2): `assets --like` ≥ 0.80 is a match (the page goes
into the bundle's `guides`); 0.70–0.80 is "offer the page" beside retrieval; below 0.70, none.
Survey's `topics` overlap is normalised by page size, since hub pages dominate it today.

**(c) Plan programs.** Today a plan is a list of steps plus fallbacks. v2 emits a small
program. The brief's example, "do A, try B and C, take the best, then D", is directly
expressible:

```yaml
program:
  - id: a
    run: "artmind query entity-resolve --domain {domains} {mention}"
  - id: bc
    par:                                   # run together
      - {id: b, run: "artmind query graph text2cypher --domain {domains} {question}"}
      - {id: c, run: "artmind query vector-text --domain {domains} --topK 10 {question}"}
    pick: best_of                          # or: union, first_ok
    score: [rows_nonzero, not_truncated, relevance]
  - id: d
    run: "artmind query chunks --domain {domains} --idList {bc.chunk_ids}"
    when: "bc.kind == 'graph'"            # ground graph rows in text
guards:
  - {on: bc, if: "rows == 0", then: escalate}           # reasoning path
  - {on: c,  if: "distinct_documents < 3 and route == 'survey'", then: {replace: survey}}
```

- Operators: `run`, `par`, `map` (R7), `pick` (`best_of`, `union` with discrepancy notes,
  `first_ok`), `when`, `guards` (`escalate`, `replace`, `retry_with`, `widen_domains`, `page`).
- **`map` is the main program shape (R7, lesson 4).** 87 % of round-7 struggle turns looped one
  command over a list: `pattern10` per document (up to 40 times), `vector-text` per phrasing,
  `entity-resolve` per name. `map` runs a step over a list (documents, years, twins,
  phrasings, claims) under a call and character budget, and is usually followed by `union`.
  `best_of` is secondary.
- Guard predicates come from a fixed vocabulary computed by code from command output:
  `rows`, `rows_total`, `truncated`, `distinct_documents`, `max_relevance`, `twins`,
  `stale`, `history_matches`, `view_status`, and from round 7 `payload_chars`, `persisted`,
  `entity_docs`, `docs_cited`, `rows_below_k`, `score_drop` (lessons 2, 9, 13, 15). No free
  expressions.
- **Program kinds round 7 needs (R7, N5; lessons 20, 26, 30, 33, 36).** View counts (union over
  years plus a text check); kinship aggregates (union over twins); enumerations and counts
  (page to `rows_total` plus a text union; refuse a count from truncated evidence); entity
  visits (document coverage: `entity_docs` vs `docs_cited`); chunks-only cross-document
  (`par(passage search, survey)` + union); `latest` (collect every amendment of the target,
  order by document date and the supersedes chain, newest first, with executed/draft status
  per document family); `entity_history`/`time_history` (the history commands, never
  text2cypher); absence (one exhaustive exact-term sweep, not repeated searches); batch claims
  (`map` with a rewrite step from source wording to corpus wording, excluding the claim's own
  source, §5.4). One step suffices for an entity profile, an existence check and a named
  document.
- Programs are templates in `routes.yaml`, one per route (and per asset kind), validated by
  the existing tests (every command and option exists in the Click tree), each with its
  evidence ids.
- The P29 aggregate case becomes a `union` of the graph count and a text check over the
  class words, with discrepancies reported. That is the "completeness branch".

**(d) Coverage and identity guards (E2, E4).** Two guards apply to every program:
*coverage* (a survey-, procedure- or enumeration-shaped question whose evidence comes from
fewer than k documents escalates to `survey`), and *identity* (any resolved entity with
twins, or with an open same-as proposal touching it, adds every twin to the step and a
`warnings` line that also files an identity gap, §5.6).

**Identity expands, it does not ask (R7, lessons 8, 28; N4).** Twins decided about 15 % of
brain01 answers, often silently. The guard expands same-class twins (casefolded name, name
with and without surname, kinship variants from `insights/aliases.yaml` and the domain's
extension) into a `union`, reports the split in `warnings`, fills the plan's `twins` field
(empty today) and files a same-as proposal. It never asks the user about the narrator.
Identity is seeded before retrieval from the vault's self-description note and the kinship
table.

**Coverage from evidence shape, not wording (added 2026-11-03; superseded by round 7 below).** The findings report
`docs/superpowers/reports/2026-11-03-summaries-and-retrieval-findings.md` (§4.6, idea C)
shows that trigger words miss cross-document questions, and proposes choosing the depth from
the retrieval result itself. That makes the coverage guard a predicate over evidence shape,
which code computes from what `vector-text`/`survey` already return:
- concentration (share of top passages from one document);
- agreement between levels (strong passages inside the top document-summary hits mean a needle);
- score shape (a sharp drop versus a flat tail);
- how many documents clear a relevance floor.

The guard vocabulary in (c) gains these as `concentration`, `level_agreement`, `score_drop`
and `docs_over_floor`. Wording remains one input among several. The report's idea E,
answering from the needle result and offering the deeper pass, is the bundle's `escalate`
(§5.4) shown to the user as a one-click "run a full survey" (§5.7 Ask). Its idea B (a
chunk-summary vote in `vector-text`) and the leg-C question are retrieval experiments that
run before P2 and inform these thresholds. Its idea A (merge the context and summary passes)
is ingest-side and outside this design.

**Coverage rule replaced (R7, lessons 13–15; N3).** On round-7 labels the evidence-shape
signals did not separate needle from survey: concentration and level agreement were flat
across classes, and only a sharp score drop was informative, and only as "this is a needle".
So the rule becomes:
- **chunks-only scopes:** run `par(vector-text passage, survey)` and `union` by default. It
  costs needles little (the needle document sat in survey's top 10 in 8 of 10) and lifted
  coverage at depth 10 from 27 % to 43 %. Stay needle-only when relevance(1) − relevance(2)
  ≥ 0.03 (`score_drop`). Survey depth comes from the brief row shape (50 documents as line rows,
  bounded-output design §4), not from a deeper full result.
- **graph domains:** coverage is entity-anchored. The guard compares the entity's own
  document count (`entity_docs`) with the documents the evidence cites (`docs_cited`) and pages
  to `rows_total`.
- Survey cues for the lexical classifier cover user wording (lesson 10): "survey", "various",
  "all the …", "which documents", plural document-type subjects, "across", responsibility and
  obligation shapes.

`concentration` and `level_agreement` stay in the vocabulary for measurement only.

The decision model stays optional (D3). Replay decides lexical vs model per vault, as today.
Round 7 (lesson 12, N7): lexical is the default on both vaults (0 wrong on the benchmark labels,
against 1 right of 4 high-confidence plans for the model, with 60 s latency spikes); the model
stays in shadow until replay on confirmed labels shows it ahead.

### 5.4 Executor and evidence bundle (the fast path)

**`artmind query run "<question>" [--domain] [--plan FILE|-] [--maxSteps 12] [--compact]`**
(new, proxied, traced, read-only). It plans (or takes a plan), executes the program in
process, applies the guards, and returns one bundle:

```json
{
  "bundle_version": 1,
  "plan": { "intent": "retrieve", "route": "survey", "asset": null, "confidence": {…} },
  "path": [ {"id": "c", "cmd": "…", "rows": 10, "ms": 640, "guard": "coverage→survey"} ],
  "evidence": [
    {"eid": "e1", "kind": "passage", "text": "…", "doc": {"id": "…", "name": "…", "domain": "…",
     "valid_from": "…", "superseded_by": null}, "chunk_id": "…", "relevance": 0.71}
  ],
  "facts":  [ {"eid": "f1", "kind": "graph_row", "row": {…}, "evidence": ["e3"]} ],
  "guides": [ {"kind": "topic_page", "slug": "…", "stale": false, "cites": ["e1", "e4"]} ],
  "coverage": {"documents": 7, "domains": ["…"], "domains_without_hits": ["…"]},
  "gaps":   [ {"kind": "identity", "entities": ["…", "…"]} ],
  "escalate": null | {"reason": "rows == 0 on b and c", "suggested": ["…"]},
  "sources": "…ready provenance block…"
}
```

- `evidence` is the only thing an answer may cite. `guides` steer the answer. `facts` are
  graph and SQL rows, linked to evidence where the command returned ids.
- **Bounded (R7, lessons 2, 14, 37).** The bundle stays within 24,000 characters. Every
  evidence row carries document id, title, domain, date and status (`current` / `superseded`;
  executed or draft where the document family has both, lesson 33), whatever command produced it.
  Survey depth arrives as one row per document, with the text shown chosen for the question
  (best passage or best-matching sentence, bounded-output design §4.1) and full passages for
  the strongest matches;
  the rest is reachable by a result handle (`query page`). The bundle records
  `payload_chars` per step. Mechanism: `2026-11-05-bounded-output-design.md`.
- **Scope is shown (R7, lessons 35, 39).** The bundle gains `scope: {domains, excluded_documents,
  rules}`, the domain scope rules applied (§5.3 a) and any documents excluded (a claim's own
  source, non-independent documents). The answer states it when it narrows the result.
- **Counts are exhaustive or labelled (R7, lesson 26).** The executor refuses to return a count
  or enumeration from truncated evidence; it pages to `rows_total`, or returns `coverage:
  {found, documents_read, documents_total}` and the answer says "N found in M of K documents
  read".
- **Suspect values are marked (R7, lesson 31).** A value on a page the conversion check flagged
  reaches the bundle with `suspect_conversion: true`, and the answer never presents it as "the
  source says".
- **Batch programs (R7, lessons 23, 32–37).** `query run --each <claims file>` runs a fixed
  program per claim (rewrite to corpus wording from `terms`, passage search + survey, scan
  inside the top documents, `latest` per family) under a call and character cap, one span per
  claim. Measured offline at ~1.4 s and an estimated USD 0.10–0.15 per claim, against 0.53
  recorded for the sub-agent sessions.
- **Dual path, made explicit:** `escalate` null means the fast path (the agent writes the
  answer from the bundle). `escalate` set, or plan confidence low, means the reasoning path
  (the agent reads `suggested` and the routing table, and may run commands itself, as today).
  Both paths are traced. The plan span records which path was taken.
- **Latency target** for the fast path: plan ≤ 1.5 s with the decision model, execution
  ≤ 3 s for one to four steps, then one synthesis call. Today's retrieval turns run from about
  20 s to over 15 minutes (round 6), with a median near a minute.
- **Contrarian check: code execution reduces the agent's recovery ability.** Mitigations:
  guards escalate rather than fail; the bundle carries `path`, so the agent sees what ran;
  the agent may always deviate (traced as `overridden`); and `artmind-review` turns
  overridden-and-right traces into route fixes.

### 5.5 Agent contract

The study's W3 skill diet, retargeted to the executor:

1. `artmind-query` SKILL.md to about 150 lines: grounding contract; **call `query run` for
   every new question** (follow-ups included, which settles P4/P25); how to read a bundle;
   evidence vs guides; when `escalate` is set, the generated routing table (from
   `routes.yaml`) and the references; answer style; provenance is appended by the UI.
2. References keep the deep material (temporal, conflicts, hierarchy, structured store,
   hubs, views) and a generated `commands.md`.
3. Profiles: `ADMIN_SYSTEM_APPEND` and `QA_SYSTEM_APPEND` stop restating steps. The admin
   profile lacks `artmind-insights`; it gains it only if the Inbox and Knowledge areas do not
   cover its jobs (D5). `artmind-review` stays out on purpose: it runs from Claude Code in the
   checkout, never from the chat or admin UI (its own rule).
4. Tests check the skill against data (routes, Click tree), never prose phrases.
5. **Answer contract (R7, lessons 19, 40, 42).** When sources are dated or superseding, state the
   latest applicable one first. Residual incompleteness is stated with numbers, never hedged
   ("12 of 26 documents read", not "at least"). Separate stated from implied occurrences (an
   event implied by presence counts, R7-1, and is labelled implied). For a relation counted
   by direction ("a fight with me"), check who each episode was directed at until the schema
   carries participant and target. Take the widest granularity the question allows, grouped
   by level.
6. **The tool gate stays out of planned steps (R7, lesson 3).** The executor runs commands in
   process; on the reasoning path the gate strips a leading `cd <vault> &&` and allows reading
   the agent's own persisted results (bounded-output design §7).

### 5.6 Learning loop: gaps and proposals

After each traced turn, a deterministic **gap classifier** (new module, e.g.
`artmind/gaps/`) reads the plan, the bundle, the rating if any, and the next turn's intent.
It files zero or more proposals.

| Gap | Signal | Proposal | Applied by |
|---|---|---|---|
| Asset: recurring shape | ≥ 3 turns in 30 days, same route and class set, no asset matched, at least one 3+ step program | **view candidate** (route, classes, example questions) | create-view skill / Inbox |
| Asset: recurring survey | ≥ 2 survey turns whose top-10 documents overlap ≥ 0.5 | **topic candidate** (paraphrased topic from the questions, domains, expected sources), deduped against pages (§5.2) | `topic build` from the Inbox |
| Asset: hub with no page | insights salience top-N entity, or catalogue degree top-N, without a fresh page | **topic candidate**, entity anchor | same |
| Asset: stale page used | a stale page in `guides` | **refresh** | `topic refresh` |
| Identity | twins or open same-as touched by the bundle; `follow_up_why`; `curate` intent naming entities | **same-as candidate** (pre-filled group, evidence chunk ids) | same-as queue |
| Extraction | the fact came only from `evidence` text while the route expected graph rows, on a graph domain | **re-extract candidate** (document, chunk), or schema review | review dossier / `ingest sync --reextract` |
| Routing | plan high and agent overrode it, rated good; or plan high and rated wrong | **label candidate** (question → route the trace took) | question bank (§5.9), user confirms |
| Data health | `domains_without_hits`, missing summaries, unembedded passages | **maintenance task** | Pipeline |
| Alias (R7, lessons 16, 22; R7-5) | a `follow_up_why` naming a missed item found under another word; a term in the question that matched no mention | graph domain: **same-as** (both names exist) or an **`update` draft** adding the alias to the entity (only one exists); any domain with document targets: an **alias record** `.artmind/data/curation/aliases/<id>.json` `{term, domain, targets: [doc ids], source: trace id, decided_by}` | Inbox; read by the catalogue's `terms` part |
| Corrected fact (R7, lesson 43) | the user settles in a follow-up what the notes leave ambiguous (which of two places, an organisation the notes do not name, a person's role a description got wrong) | **proposed fact** (`update` draft) or alias record | Inbox |

**`follow_up_why` re-runs with a must-find (R7, lesson 22; N9).** A why-turn that names the
missed item re-runs the previous plan with that item as a must-find; in round 7 this would
have closed 6 of 8 brain01 and 2 of 3 nbs corrections. When it still does not find it, the
gap is identity or alias, and the row above files the proposal.

**Accept means verified (R7, lesson 21).** Every Inbox accept runs the command, then re-reads the
graph and shows the result on the card. Round 7 had two merge claims made in chat that the
graph did not bear out.

Proposals are files, like curation records:
`<vault>/.artmind/data/proposals/<kind>/<id>.json`, with `{kind, created_at, evidence
(trace ids, entity ids, doc ids), draft (the command or the YAML), status
open|accepted|rejected|deferred, decided_by, decided_at}`. They are committed with the vault,
so a decision made on laptop A is not proposed again on laptop B. Proposals hold private
content and never leave the vault.

**Topic triggers (the brief's three, plus one):** insights-driven (hub and salience) ·
explicit ("make a page on X" is the `curate` intent, or a button) · query promotion
(recurring survey) · stale-page refresh. In each case the user approves before any LLM call.
Topic builds cost map-reduce calls, so the proposal shows the dry-run cost (`topic build
--dry-run` exists).

**"Missing primitives" (the brief's open item).** The loop is how they are found: a view
candidate is a missing view; a recurring program that no single command expresses is a
candidate *composite command* (study D, "composite commands for observed loops"); repeated
escalations on one route mark a route whose program is wrong. `traces stats` gains a per-route
escalation rate.

### 5.7 Admin UI redesign

**Diagnosis.** The dashboard's tabs mirror CLI groups (Ingest & browse, Maintenance,
Structured, Schemas, Insights, Views, Benchmark, CLI guide), and judgement work happens in
the chat lane. The operator's real jobs are different: *ask*, *decide what the system
proposes*, *see what is known*, *keep the pipeline healthy*, *check quality*.

**Information architecture** (one app; the chat UI is "Ask" alone, for end users):

| Area | Holds | Replaces / adds |
|---|---|---|
| **Ask** | Chat (Lane A), with a **plan card** per turn (intent, domains, asset used, route, path taken, fast or reasoning) and an **evidence panel** (bundle evidence and guides; provenance already rendered). Per answer: rate (good/partial/wrong + a reason tag: missed document, wrong entity, wrong version, made up), **"same thing?"** on any two entities in the answer, **"save as view"**, **"make a topic page"** | Chat lane; adds rating reasons, inline proposals |
| **Inbox** | Every open proposal and review queue as cards, by kind: same-as, conflicts, table classifications (today in the plugin and the skill), topic candidates, view candidates, stale pages, re-extract candidates, label candidates. Each card shows the evidence, a **preview** (below), and approve / reject / defer. A batch bar for the 140-item queues (P28): filter, approve all shown, with one combined preview | Same-as and conflicts move out of chat; agent only via "ask about this card" |
| **Knowledge** | The catalogue browsed: domains → stores, classes, documents, assets (views, topic pages, insights), health and freshness. Topic page reader (rendered page, footnotes linked, stale reasons, refresh) | Insights and Views tabs fold in; Schemas reference moves here |
| **Pipeline** | The plugin's checklist state machine (pull → apply → ingest → rebuild → embed → summarise → synthesize → curate → commit), jobs, structured ingest, snapshots | Ingest & browse, Maintenance, Structured |
| **Quality** | Traces (list, show with plan vs path), stats, triage, benchmark runs, plan replay, retrieval eval, review dossiers, question bank | Benchmark tab; adds the traces/review CLI-only tools |
| Reference | CLI guide, glossary | CLI guide |

**The operation taxonomy (the curate confusion).** Users confuse rebuild, synthesize,
re-extract, sync and curate because the names say nothing about cost or risk. Every action,
in the UI, the plugin and the skills, carries the same four badges and one sentence:

| Operation | Sentence | LLM | Reversible | Scope | Travels |
|---|---|---|---|---|---|
| Rebuild | recompute entities from the facts already extracted | none | yes (rerun) | domain | no (each machine rebuilds) |
| Synthesize | rewrite entity descriptions from their facts | yes, per entity | yes (`--force` again) | domain or filter | yes (record files) |
| Re-extract | read documents again under the current schema | yes, per chunk | yes (cache is stashed) | documents | yes (staging) |
| Summarise | write chunk and document summaries | yes, per chunk | yes | documents | yes |
| Sync | replay what other machines committed into this machine's stores | none | yes | vault | n/a |
| Same-as approve | declare these keys one thing | none | yes (remove group, rebuild) | entities | yes |
| Resolve conflict | record a human decision on a disagreement | none | yes (re-resolve) | one conflict | yes |
| Retire / supersede | take a document out of current knowledge | none | yes (`docs restore`) | document | yes |
| Topic build | write a cited page | yes, map-reduce | yes (delete) | one page | yes (file) |

The glossary is generated from one data file. The UI, the plugin's `texts.ts` and the skills
read it, so the wording cannot drift.

**Change preview contract.** The decisions an operator makes most often have no preview
today: `sameas approve`/`reject`, `ingest resolve-conflict`, `docs retire`/`restore`,
`ingest supersede`, `projection rebuild` and `views save` have no dry-run. The LLM-spending
and bulk commands do (`projection synthesize`, `ingest detect-conflicts`, `topic
build`/`refresh`, `ingest summarise`, `ingest table2graph`/`retime`, `vault sync`), each in its
own output shape and under two spellings.
The fix is in the commands themselves (§6.2 C3): **every write command takes `--dryRun`**, and
every dry run returns the same `effect` block: `{entities_affected, observations_moved,
documents, topics_made_stale, views_affected, llm_calls, est_cost, reversible, travels}`. Each
command computes it from its own planning step, so the preview is exactly what the real run
would do. The UI and plugin show it on every card and button, and an Inbox card's approve runs
the same command line without `--dryRun`. The brief's example, "this will merge 12 Project
nodes with missing dates", is exactly this output. (The first draft proposed a separate
`artmind preview <command line>` wrapper to avoid touching existing commands. With the
constraint relaxed, putting the flag on the commands is better: one code path for the preview
and the run, so they cannot drift.)

**Plugin.** The plugin keeps Pipeline and gains an Inbox subset (same-as, conflicts, stale
pages, topic candidates) using the same preview and badges. The modals that exist
(`curateModal`, `synthesizeModal`, `resolveModal`, `tableReview`) are the starting point.
Every checklist row shows its badge line, and "what does this do" opens the glossary entry.

**Chat UI (end users).** Ask only: the plan card is collapsed into a "how I found this"
line, ratings without reason tags, and "this is wrong" files an extraction or identity gap
for the operator (no direct writes; QA profile unchanged).

### 5.8 Ingest and the schema builder

**A domain-mode step first.** `artmind-create-schema` today designs a graph schema and,
separately, table mappings and the chunks-only switch. Add a first step that decides the
domain's mode from the samples, with the reason stated to the user:

| Evidence in the samples | Mode | Schema |
|---|---|---|
| Narrative or relational text where people, events and links matter (journals, cases, meetings) | graph | classes, relations, temporal |
| Reference text read for lookup and coverage (contracts, papers, manuals) where the questions are survey or needle | chunks-only (`graph_extraction: false`), summaries on | subject + description only |
| Tables whose rows are facts to aggregate | structured | grain, bridge, mappings (curate D) |
| Tables whose rows are entities (people, products) to link to documents | structured + table2graph | mapping YAML under a graph schema |
| Mixed | a family: `X.docs` (graph or chunks-only) + `X` tables | one per child |

The deciding questions are about the expected *questions*, not the documents: "will you ask
who/when/how-connected, or what-does-it-say/compare?" The step also seeds the domain's first
question-bank entries (§5.9) from the user's answers. That gives the planner labels from day
one.

**Non-graph sources** (JSON APIs, CAD and so on; brief, contrarian view 4). Out of scope. Anything
not markdown, pdf, docx, pptx or a table is converted outside artmind to one of those, and the
skill says so. A plugin hook is YAGNI until a real corpus needs one.

**Skill diets.** `artmind-ingestion-helper` (6,257 words) and `artmind-curate` (3,333) follow
the query skill's pattern. A state command answers "where am I", and the skill routes from that
state instead of a ten-situation map. Today the plugin derives its checklist in TypeScript
(`checklist.ts`) from `vault status`, job and curation reads. Moving that derivation into one
read-only command, `artmind pipeline status` (new), lets the plugin, the admin UI's Pipeline area
and the skill share one state machine. Ingest coverage to verify in
the sub-spec: graph chunks, chunks-only, structured, table2graph, summarise and re-extract,
each with one path in the skill.

### 5.9 Question bank and measurement

Three labelled sets exist with three formats: `benchmarking/questions.md` (benchmark),
`plan_labels.yaml` (route), `retrieval_labels.yaml` (documents/chunks). Merge them into one
**question bank** (`<vault>/benchmarking/questions.yaml`, private) where each entry carries
whichever levels are known: intent, domains, asset, route, expected documents, expected
answer notes. The three tools read the levels they need. Existing files are imported once and
kept readable.

Entries come from: the domain-mode step (§5.8), ratings with reason tags (§5.7), label
candidates (§5.6), and the SOP (`docs/query-routing-study/10-collection-sop.md`).

**Expected answers have three tiers (R7, lesson 38).** Each entry's expected answer lists
`must` (required for credit), `optional` (credit if present, no penalty if absent) and
`must_not` (an item that makes the answer wrong). Four of sixteen decisions the user made
writing round 7's expected answers were "optional", which a right/wrong pair cannot grade.
The benchmark grader and the bundle's self-checks both read the tiers. A rating of "good"
means correct only (R7-7); efficiency is measured separately (struggle score, calls,
characters), and struggle selects turns for review but is never a grade (lesson 25).

**Instrumentation first (R7, lesson 24).** The P2 gate cannot be measured without: per-turn
cost (not the session's cumulative `cost_usd`); model and cost on ACP turns; errors as
`status: error`; survey and topics evidence ids in spans; `payload_chars` and `persisted` per
span; spans for sub-agent calls; intent and turn index on the turn; feedback as one upserted
row per trace with its note; `review verify` failing a run with no answer or no spans. These
move ahead of P2 (§9).

Metrics, per vault and route, in `traces stats` and the Quality area: fast-path share,
escalation rate, plan `wrong` rate on the bank (replay), documents covered @10 (retrieval
eval), rated-good share, p50/p95 turn latency, cost per turn (P33 must be fixed first for ACP
turns).

## 6. CLI changes

### 6.1 New commands

| Command | Proxied | Writes | Purpose |
|---|---|---|---|
| `query catalogue [--domain] [--part]` | yes | no | §5.1 |
| `query assets [--domain] [--kind]` | yes | no | §5.2 |
| `query run "<q>" [--domain] [--plan]` | yes | no | §5.4 executor + bundle |
| `query page <handle> [--from] [--rows]` (R7) | yes | no | page a result the output budget cut (bounded-output design §5) |
| `catalogue refresh [--part health\|route_stats]` | no | cache only | §5.1 |
| `topic index [--domain]` | no | graph index | §5.2 `:TopicPage` sweep |
| `proposals list\|show\|accept\|reject\|defer` | no | proposal files (accept runs the drafted command after preview) | §5.6 |
| `gaps scan [--since]` | no | proposal files | §5.6, batch run of the classifier over traces |
| `pipeline status` | no | no | §5.8 |
| `traces export [--since]` | no | a JSONL file | Optional: a shareable subset of traces. The SOP (§4) copies the whole registry DB instead, which needs no new code |
| `bank import\|validate` | no | `questions.yaml` | §5.9 |

`query plan` gains fields (intent, asset, program) under `plan_version: 2`. Existing fields
keep their meaning.

### 6.2 Uniformity changes to existing commands

An audit of the Click tree (2026-11-02, 161 leaf commands) found the following. Each change
makes one rule hold everywhere, and a hermetic contract test walks the Click tree to keep it
holding (the `test_cli_guide.py` technique).

| # | Rule | Today | Change |
|---|---|---|---|
| C1 | Every command except the three servers (`serve`, `chat-ui`, `admin-ui`) prints one JSON result and takes `--compact` | 32 commands lack `--compact`, among them `ingest sync`, `ingest extract-kg`, `ingest write-to-graph`, `ingest pull-kg`, `ingest refine-graph`, `domains validate`, `domains harmonize`, `update draft`/`confirm`/`history`/`export`, `traces feedback`, `topic delete`, `review status`/`attach`/`regressions`/`prompt`, `init`, `setup` | Add JSON output and `--compact`. A command whose output is for people (`init`, `setup`, `review prompt`) keeps its text on stderr and prints a JSON summary on stdout |
| C2 | One spelling per option, camelCase (the repo convention) | `--dry-run` on 11 commands, `--dryRun` on 5; 10 other kebab-case options (`--from-file`, `--repo-path`, `--min-score`, `--no-narrate`, `--stage-only`, `--include-skipped`, `--allow-cross-domain-merge`, `--rebuild-projection`, `--acp-cmd`/`--base-url`) | camelCase everywhere (`--dryRun`, `--fromFile`, …). Old spellings stay as hidden aliases for one release, with an `artmind:` stderr note naming the new one |
| C3 | Every command that writes the graph, staging, curation records, views, topics or proposals takes `--dryRun`, and every dry run returns the `effect` block (§5.7) and no `rows` | Missing on `sameas approve`/`reject`, `ingest resolve-conflict`, `docs retire`/`restore`, `ingest supersede`, `projection rebuild`, `views save`/`delete`, `topic delete`, `ingest sync`, `update confirm`; present on 16 in five output shapes | Add where missing; reshape the existing dry runs to carry `effect` beside their current fields (fields kept) |
| C4 | One result envelope: every JSON result has `command`, `warnings` (list, possibly empty) and `next` (string or null); a listing also has `rows_total` and `truncated` | Partly true (H1–H4 added these to the commands `query plan` emits; `domain_guard` adds `warnings`) | Add the missing keys; never rename existing keys |
| C5 | `--domain` is accepted wherever a result can be scoped, and is optional only where the id is global | `query domains-overview` (by design), `query views show` and `query topics show` take none | `views show` and `topics show` accept an optional `--domain` that warns when the named object is outside it; `domains-overview` stays the documented exception |
| C6 | Exit codes mean one thing: 0 = ran (empty results included), 1 = bad input, 2 = usage, 3 = a store unreachable | Not audited; at least Neo4j-down and bad-input share 1 today | Audit in the sub-spec; adopt the table; the plugin and `query run`'s guards read them |
| C7 | A command pair the plan keeps choosing wrongly is merged, with the old names as aliases | Study W5: patterns 2/3/4 vs `entity-context`, 6 → 5, the three resolvers | Allowed now, but only for pairs that replay or shadow data show the plan choosing wrongly |

**Compatibility.** Aliases keep old command lines working. The Obsidian plugin reads only
`--compact` JSON, and its fixtures are regenerated from the real CLI
(`test_plugin_fixtures.py`), so any envelope change fails that test first and is fixed there.
Skills and `routes.yaml` templates are checked against the Click tree by existing tests, which
catch a renamed option. A `serve` daemon started before the change still answers old
spellings (CLAUDE.md §1), so the release notes say to restart it.

**Order.** C1–C4 land first (phase P1a, §9): the executor's guards and the Inbox previews
read the envelope and the `effect` block, so they depend on it. C5 and C6 land with P3; C7
only on evidence.

## 7. Contrarian views and how the design answers them

| View | Answer |
|---|---|
| Hourly meta-metadata is needed | Fingerprint + generation counter is fresher and cheaper; only `health` and `route_stats` are periodic (§5.1) |
| Fallbacks are needed for robustness | Kept, but run by code as guards; escalation hands the agent the rest (§5.4) |
| Dual paths are over-engineering | The fallback ladder is already a second path, written in prose. This makes it explicit, testable and measured (§5.3) |
| Topics in the graph will be read as evidence | Indexed as `:TopicPage` with `CITES`/`ABOUT`, never as `:Document`; bundles put them in `guides` (§5.2) |
| Topics go stale | Content-hash staleness exists; the loop proposes refresh when a stale page is used (§5.6) |
| An executor makes answers rigid | `escalate`, agent override, override traces feed route fixes (§5.4) |
| The UI duplicates the CLI | It runs only the CLI; previews and proposals are CLI commands; the UI adds layout and batching |
| Schema builder should cover every format | Mode step covers the five real modes; other formats convert outside (§5.8) |
| The decision model is a privacy risk | Lexical works with nothing leaving the machine; per-vault provider choice; shadow can be off (D3) |

## 8. Risks

- **The plan program becomes the new long skill.** One template per route, each with
  evidence ids; no route without a scenario; programs capped at 12 steps.
- **The gap classifier floods the Inbox.** Thresholds per kind; dedupe against open and
  rejected proposals; a rejected proposal's signature is not re-proposed for 90 days.
- **Thin evidence (E8) means tuning blind.** Phases P1–P2 ship in shadow; switching on waits
  for the SOP's "done when" (§9).
- **Two laptops, one queue.** Proposals as committed files with Obsidian Git merge rules, as
  curation records already do (`vault resolve` gains the proposal kinds).
- **Stale daemons.** `query run` and `query catalogue` are proxied; every probe uses
  `ARTMIND_NO_PROXY=1` or a restarted `serve` (CLAUDE.md §1).
- **Cost.** Topic and view candidates never build anything without approval; every LLM
  operation shows its preview cost.

## 9. Phasing and sub-specs

Each phase is its own spec, plan and build. Gates are measured on the question bank and
traces.

| Phase | Sub-spec | Contents | Gate to the next |
|---|---|---|---|
| P0 | (SOP, no code) | Run `10-collection-sop.md` on laptops A, B and the desktop; import labels | Every SOP shape ≥ 3 rated turns in two vaults; ≥ 40 labels |
| P1a | CLI uniformity (`2026-11-02-cli-uniformity-design.md`) | §6.2 C1–C4: JSON + `--compact` everywhere, camelCase with aliases, `--dryRun` + `effect` on every write, the result envelope; contract tests over the Click tree; plugin fixtures regenerated | Contract tests green; plugin tests green; no skill or route template names a removed spelling |
| P1 | Catalogue + assets (`2026-11-02-catalogue-and-answer-assets-design.md`) | §5.1 parts, generation counter, `query catalogue`, `query assets`, `:TopicPage` index, `topic index` | Plan replay unchanged or better with assets in the catalogue |
| P1b (R7) | Bounded output + round-7 fixes now (`2026-11-05-bounded-output-design.md`; `12-round7-lessons.md` §5; the lesson-24 instrumentation of §5.9) | Brief row shape, output budget and `query page`, survey default by shape, tool-gate repairs, spans with `payload_chars`/`persisted`, per-turn cost, feedback upsert | Visibility gate before merge (bounded-output design §4.3: brief survey(50) shows answer-bearing text at least as often as full survey(10)); round 8: persisted share of retrieval steps < 5 % (96 % in round 7); instrumentation fields present on every span |
| P2 | Planner v2 (waits for P0 and P1b; what it needs: `docs/query-routing-study/11-p2-evidence-needs.md`, answered in `12-round7-lessons.md` §3) | Intent layer, asset match, programs in `routes.yaml`, coverage/identity guards; shadow only | Replay: zero `wrong` on view/asset labels, `wrong` < 10 %, intent right on all non-retrieval labels |
| P3 | Executor + skill diet | `query run`, bundle, skill to ~150 lines, profiles; shadow bundles first, then on in the admin console | Targeted agent runs (study 08 §4) no worse, fewer calls; fast-path share ≥ 50 % of rated-good turns |
| P4 | Admin UI: Ask + Quality | Plan card, evidence panel, rating reasons; traces, replay, bank views | Ratings on ≥ 70 % of admin turns for two weeks |
| P5 | Learning loop + Inbox | `gaps`, `proposals`, `preview`, Inbox cards for same-as and conflicts first, then topics/views/labels; plugin Inbox subset | A same-as batch of 100 reviewed in under 15 minutes without the agent; < 20 % of proposals rejected as noise |
| P6 | Knowledge + Pipeline areas | Catalogue browser, topic reader, Pipeline from `pipeline status`, glossary data file | Plugin and UI read one glossary; checklist parity tests |
| P7 | Ingest + schema builder | Domain-mode step, ingestion-helper and curate diets | A new vault of each mode set up by the skill with no manual YAML |

P0 is the critical path: its evidence gates switching the planner on (P2 onward), and only
the user can run it, so it starts first. P1a and P1 do not wait for it, because what justifies
them is structural (the Click-tree audit, the catalogue's missing parts), not usage data that
traces would supply. One rule keeps them from spoiling the evidence: **no code that changes
CLI output or routing merges during a collection round.** Each round runs on one build
(recorded in every trace as `graph_state.artmind_version`), and P1a/P1 merge between rounds.
C7 (merging commands) is the exception: it waits for P0's data. P4 depends only on P2's plan card fields and can
start as soon as P2 ships in shadow.

## 10. What this design does not change

Beyond the uniformity changes in §6.2, which keep every old form as an alias:

The stores, extraction, projection and curation-record formats; `vector-text`, `survey` and
pattern semantics (ranking unchanged; the brief row shape and the output budget, R7, change
only what an agent-facing caller is shown); views' format; topic page format (index only added); the domain-guard
behaviour; vault sync. The decision-model integration stays as built.

## 11. Decisions for the user

| # | Decision | Recommendation |
|---|---|---|
| D1 | Executor in code (`query run`) as the fast path, or keep the agent choosing commands with a better plan | Executor: E1 says the agent does not follow plans reliably |
| D2 | Topic pages indexed in the graph as `:TopicPage` (not evidence) | Yes |
| D3 | Decision model per vault: hosted (OpenRouter/jev) for public corpora, local or lexical for personal vaults | Yes; lexical as the floor everywhere |
| D4 | Proposals as committed vault files (travel between laptops) | Yes, same as curation records |
| D5 | Admin UI IA (Ask, Inbox, Knowledge, Pipeline, Quality) replacing the CLI-shaped tabs | Yes, built in P4–P6 order |
| D6 | One question bank replacing three label formats | Yes, with import of the existing files |
| D7 | P0 collection before P2 is switched on (P1 can start now) | Yes |
| D8 | CLI uniformity (§6.2): camelCase `--dryRun` (not `--dry-run`) as the one spelling, old spellings as hidden aliases for one release, then removed | Yes; camelCase is the repo convention and 5 commands already use `--dryRun` |
| D9 (R7) | Survey depth: keep 50 for the agent as one-line rows (brief shape), revert the full shape's default to 10 | Yes (bounded-output design §6); the revert lands with the brief shape |

## 12. References

- Query-routing study, `docs/query-routing-study/` (rounds 1–6; round 6 added 2026-11-02).
- Specs: kg-views (10-09), query review loop (10-11), insights (10-17), query plan (10-22),
  chunks-only (10-30), passages (10-31), summaries/survey/topics (11-01).
- Adaptive-RAG: Jeong et al., NAACL 2024, routing by predicted question complexity
  ([arXiv 2403.14403](https://arxiv.org/abs/2403.14403)). The intent and route classifier is
  the same idea with routes in place of complexity levels.
- Corrective RAG: Yan et al., 2024, a retrieval evaluator that triggers corrective retrieval.
  This is the guard idea, done here with deterministic predicates instead of a learned evaluator.
- LLMCompiler (Kim et al., 2023) and ReWOO (Xu et al., 2023): plan a tool DAG up front, run
  independent calls in parallel, reason once over the results. This is the plan program and executor.
- GraphRAG local/global search (Edge et al., 2024) and DRIFT search (Microsoft Research,
  [blog](https://www.microsoft.com/en-us/research/blog/introducing-drift-search-combining-global-and-local-search-methods-to-improve-quality-and-efficiency)):
  start from precomputed community reports, then drill into local search. Here: topic pages and
  summaries first, then passages.
- RAPTOR (Sarthi et al., 2024): the summary tree behind `survey`.
- The "LLM wiki" pattern (Karpathy, 2026; e.g. [overview](https://www.noze.it/en/insights/llm-wiki/)):
  an LLM-maintained, interlinked markdown wiki over immutable sources, with ingest, query and lint
  operations. Topic pages plus the learning loop are artmind's version, with one difference
  kept on purpose: pages guide, passages are evidence.
