# Topic plans for breadth: coverage, saved plans, faceted gather, freshness

Date: 2026-11-10 (revised twice the same day after review: plan-only agents; plans shaped like views, no graph copy of pages). Status: **built and measured 2026-11-10** (study note 24; the open items are listed there under "What is left"). Parent spec:
`2026-11-08-classic-baseline-and-skill-simplification-design.md` (work plan §0.1 item D0). Builds on topic pages
from `2026-11-01-summaries-survey-topics-design.md` §3. Public checkout: counts, ids and generic examples only;
brain01 questions are named by id, never by content.

## 1. Problem

Some questions are answered by many documents. An overview or trend question ("what do I do in this hobby and
how has it changed", "what do our policies say about X across the bank") needs every matching document, and an
agent turn reads ten or twenty.

Evidence, brain01 Q08 (an overview of two subjects over five years):

- Every run, every baseline included, misses 5–7 of its 7 must items; the tier asks for about 22 dated events.
- `survey --by year` for one of the two subjects reports 90 keyword-matching documents and shows 10 (2 per
  year). The agent read those, said the years were partial, and stopped.

Banking shows the same gap across domains instead of years: "depth for broad multi-document questions" (parent
§4.3a; banking Q15, Q18, Q25).

Topic pages were built for this, but nothing suggests one, and `topic build` gathers with **one** survey capped at
50 documents. Measured on Q08 (dry runs and survey calls, no LLM; "covered" = the event's journal day is among
the gathered documents, which is necessary for any answer to carry it, not sufficient):

| Gather | Documents | Dated events covered |
|---|---|---|
| `topic build`, the question as topic, `--maxDocs 50` | 50 | 11 of 22 |
| the same, first subject only | 50 | 13 of 22 |
| the same, second subject only | 50 | 7 of 22 |
| 2 subjects × each year, top 15 per year | 144 | 19 of 22 |
| 2 subjects × each year, top 25 per year | 230 | 21 of 22 |

Breadth comes from gathering **per facet**, not from a bigger single pass.

A second problem: the banking `cpd-r051…056` pages were built from benchmark questions verbatim and read by
baseline runs (study note 22). Pages are answer sheets for the question they were built from.

## 2. The granularity ladder

The corpus answers at six levels of breadth:

| # | Unit | Breadth | Made by | Fresh |
|---|---|---|---|---|
| 1 | Focused entity (`observation_count` < n) | one fact | extraction | always (recomputed at commit) |
| 2 | Anchor entity (`observation_count` ≥ n) | one subject, many mentions | extraction + aggregation | always |
| 3 | Saved view | many entities, one shape | operator (saved Cypher) | always (a query) |
| 4 | Passage | one span of text | split + embedding | always |
| 5 | Survey | many passages over many documents | a query | always |
| 6 | Topic page | many surveys, synthesised | LLM, once | **no: a snapshot** |

Levels 1–5 are corpus data or queries over it. Level 6 is the only derived, stored result: it costs time and
money and goes stale. A view is the model to copy: a saved **query** stays fresh because the query is saved, not
its result. So a topic is saved twice:

- the **topic plan**: the retrieval program (subjects, slices, cells, depth), saved like a view, **available to
  agents**, run live;
- the **topic page**: the synthesis, for people (Obsidian), and the cache a refresh starts from; **never shown to
  agents** (decision 2026-11-10: plan only).

**n** is not fixed: per domain it is the 90th percentile of `observation_count` (at least 5), computed with the
catalogue and reported by orient (§9.2).

## 3. Who does what

| Part | Does | Never does |
|---|---|---|
| `survey` | Reports how many documents match and how many it shows (`coverage`) | Suggest a topic, or judge what the question needs |
| `query topics plan` / `run` | Shows a saved plan; runs its surveys live | Show the page, call an LLM |
| Agent (query skill) | Runs a plan that fits; when none fits and `coverage` is low, offers one | Build or refresh |
| User | Says yes or no after the dry run (documents, calls, cost, what leaves the machine) | — |
| Agent (build skill, admin console only) | Proposes subjects; dry run; after the yes, builds | Build without an explicit yes in this conversation |
| `topic build` | Plans the grid from orient's facts, gathers per cell, maps, reduces, checks, writes plan + page | Guess subjects (the caller gives them) |
| Background job | Checks each plan's freshness (no LLM) | Refresh unless the plan says so |

## 4. Part A — the `coverage` fact

`survey` (both shapes) adds:

```json
"coverage": {"matching_documents": 90, "shown": 10, "by": "year"}
```

- `matching_documents`: distinct documents with a keyword hit in scope (the keyword passage leg, `POOL` 300
  deep; with `--by`, the sum of the periods' `keyword_hits`). When the pool is exhausted it is a floor:
  `"at_least": true`.
- `shown`: documents the payload gives evidence for (passage or gist rows in brief; `documents` in full).
- Present whenever `matching_documents > 0`. No threshold in code.

`vector-text` gets none: its keyword leg counts passages, and breadth questions already go through `survey`.

## 5. Part B — the topic plan

### 5.1 What a plan holds

`<vault>/.artmind/domains/topic_plans/<slug>/{plan.yaml,SKILL.md}`, the same shape as a saved view
(`domains/views/<name>/{view.yaml,SKILL.md}`): committed with the vault, read from disk on every call, never
seeded by `setup`. `plan.yaml`:

```yaml
format: 1
slug: <slug>
topic: "<topic in plain words>"
version: 1                      # bumped by an edit; refused on downgrade (as views)
domains: [<domain>, …]
subjects:                       # facets the caller gave
  - "<subject question 1>"
  - "<subject question 2>"
slices: {by: year, split: none} # resolved from `auto` at build (§6.2); stored resolved
per_cell: 15
max_docs: 150
built:                          # the last build, for freshness (§8)
  at: "<UTC timestamp>"
  generations: {<domain>: <catalogue generation>}
  grid: [{subject: 1, period: "2023", domain: null, matching: 16, taken: 15}, …]
  model: <model>
  prompt_version: <n>
refresh: manual                 # manual | on_stale | weekly
```

`SKILL.md` is the agent guidance, as for a view: when the plan applies, which subject answers which kind of
question, what it does not cover. `topic build` writes a first version from the topic and subjects (no LLM);
the operator edits it. As with views, a changed `plan.yaml` or `SKILL.md` without a `version` bump is refused,
and so is a version downgrade; saves are atomic. `artmind topic plan validate <slug>` checks both files (the
`views validate` counterpart).

The plan holds no corpus text, but its subjects are written in the user's words (for brain01, private: the
plan stays in the vault, like views and dossiers).

### 5.2 Commands (read-only, proxied, traced)

- `artmind query topics plan <slug>`: the plan, its `SKILL.md`, its freshness (§8) and, per cell, `matching` at build vs now.
- `artmind query topics plan --list [--domain d]`: every plan in scope (slug, topic, subjects, freshness).
- `artmind query topics run <slug> [--subject N] [--cell <period|domain>] [--shape brief]`: runs the plan's
  surveys **live** with the plan's domains, subjects, slices and depth; returns brief rows grouped by cell under the
  output budget (`ARTMIND_OUTPUT_BUDGET`; the rest via `query page <handle>`). No LLM. Each row is ordinary survey
  evidence, cited as such.
- `query topics list|show` (pages) are removed from the agent's surfaces: the query skill no longer names them,
  and `survey`'s `topics` block (pages citing returned documents) is dropped. The page files stay for people;
  `assets` lists plans, not pages.
- **The graph copy of pages goes.** `:TopicPage` nodes (with `CITES`, `CITES_PASSAGE`, `ABOUT`, the embedding,
  `topic_embedding` / `topic_text_ft` and the slug constraint) existed only so that `survey`'s `topics` block and
  `assets` could find pages; with plan-only agents nothing reads them. Removed: `artmind/topics/index.py`, `artmind
  topic index`, its sweeps in `vault sync` and `projection rebuild --sweep`, the index/unindex calls after `topic
  build|refresh|delete`, the constraint and indexes in `setup` (a migration drops existing nodes and indexes), and
  their tests. The admin console's Topics tab reads the page files and the plans from disk.

### 5.3 How the agent uses a plan

Orient peel 2 lists a domain's plans (slug, topic, subjects; §9.2). The query skill, one rule:

> A topic plan whose topic and subjects fit the question is a reviewed way to cover it: run
> `query topics run <slug>` (one subject or cell at a time if the question needs only part), and answer from those
> rows. When none fits and `survey`'s `coverage` shows the answer rests on a small share of `matching_documents`
> for an overview or trend question, answer from what you read, say how many more documents match, and offer to
> build a plan, naming its subjects and span. Never build it yourself.

Benchmarks: a run may run a plan (it is retrieval, done live). A plan built *from* a benchmark question still
encodes its decomposition; §10 handles that at grading time.

## 6. Part C — facets and the corpus's shape

### 6.1 Facet kinds

| Kind | From | Used when |
|---|---|---|
| **subject** | `--facet "<question>"` (repeatable); default the topic text | always (at least one) |
| **period** | `--by year\|month\|none\|auto` | the scope is dated by content over a long enough span |
| **domain** | `--split domain\|none\|auto` | the scope is a family root whose children hold matches |
| **member** | `--member "<name>"` (repeatable), usually chosen from `--discover members` (§6.4) | the question asks for a set of distinct things (places, people, companies), not a theme |

Subjects and members are judgement (the agent's parts, the user's words, anchor entities §9.3, discovered
candidates §6.4). Slices are mechanical.

Two shapes of breadth question, measured on brain01 (§11): a **series** (one theme over time: subject × period
covers it) and a **set** (distinct members: a theme search does not name them, so they are discovered first,
then gathered one by one).

### 6.2 `auto`: the shape rule (code, deterministic)

From orient peel 1 for each scoped domain (`time.dated.from_content`, `time.span`, `unit.documents`, family):

- **year** when at least 80 % of documents are dated from content and the span exceeds 24 months; **month**
  when the span is 3–24 months and there are at least 60 documents; otherwise **none**.
- **domain split** when the scope is a family root with two or more children holding a keyword match for some
  subject; otherwise **none**.
- Both may apply; the grid is then subject × domain × period, bounded by §7.2.

brain01 (one domain, 1,710 of 1,711 dated, 59 months) → year, no split. banking (a family of eight children, 58
documents over 6 months, mostly one date) → no period, domain split.

### 6.3 The dry run shows the grid

`topic build --dryRun` writes nothing and calls no LLM:

```json
"grid": {"slices": {"by": "year", "split": null},
         "cells": [{"subject": 1, "period": "2023", "matching": 16, "take": 15}, …]},
"documents": 144, "calls": {"map": 144, "reduce": 4},
"effect": {…, "llm": {"calls": 148, "model": "…", "est_cost_usd": …}}
```

`matching` per cell is the corpus's shape for this topic; a subject with 0 everywhere is a wrong facet, visible
before any cost.

### 6.4 Members: discover, choose, gather (added 2026-11-10; build after §12 steps 1–5)

A set question ("which places have I visited in country X", "which client companies") is answered by members the
question does not name. A theme search misses them: on brain01 Q05 the notes of a visit name the place, not the
set it belongs to. Measured (no LLM): subjects × year gather 6–9 of 16 dated visits; one subject per member, 11 of 16 with
the members discovery found, 14 of 16 with all of them.

1. **Discover (code, no LLM).** `topic build --dryRun --discover members --anchor "<entity>" [--anchor …]
   --entityClass <CLASS>` resolves each anchor (`entity-resolve`, exact or alias first) and lists the entities of
   that class co-mentioned with any anchor (`pattern8 --via mentions --all`), each with its mention count, first
   and last dated mention, and its domain. brain01 Q05: three anchor entities, class LOCATION → 125 candidates,
   5 of the 8 expected places among them.
2. **Choose (judgement).** Candidates mix in-set and out-of-set entities (the graph has no "located in" links), so
   code does not filter them. The build skill shows the list; the agent proposes the in-set ones with a reason,
   the user keeps, drops or adds members (an added member need not be an entity).
3. **Gather (code).** Each kept member is a subject `"<member>"` with its aliases from the graph, gathered
   without a period slice (`take` per member, default `--perCell`), and a plan-level cell for the anchors
   themselves (the parent set: places inside the anchor itself). The grid reports `matching` per member, so a member with 0
   is visible before any cost.
4. **Store.** The plan records `members: [{name, entity_key?, aliases}]` and the discovery that produced the
   candidates (`discover: {anchors, entity_class}`). A freshness check (§8) also re-runs discovery and reports
   **new candidates** (co-mentioned since the build, not yet kept or dropped), never adding them on its own;
   dropped candidates are remembered (`dropped: […]`) so they are not offered again.
5. **Page.** One section per member (or a table: member, dates, what happened), plus `## Timeline`.

Not in scope: deciding set membership from world knowledge in code (a place lies inside a country), or extracting
containment links (the extraction wave, item G, may add `LOCATED_IN`; discovery would then read it).

## 7. Part D — the faceted gather and the page

### 7.1 Per cell

For each cell: `survey_search(domains=<cell domain or scope>, question=<subject>, top_docs=<take>,
passages=MAX_PASSAGES_PER_DOC, date_from/date_to=<period bounds>)`. Documents merge by `doc_id`; a document found
by several cells keeps each cell's passages (capped at `MAP_INPUT_CHARS`) and records its cells. `query topics
run` uses the same function, so the agent sees what the build saw.

### 7.2 Depth and caps

- `take` per cell = `min(matching, per_cell)`, default 15; a cell with more matches is `partial` in the report.
- `--maxDocs` default 150, maximum 300 (`ARTMIND_TOPIC_MAX_DOCS` widens to 1–300). Over the cap, cells are cut
  fairly (round-robin by rank); the dry run says so. Every cell is listed with `matching`, `taken`, `partial`.

### 7.3 Map, cache, reduce

- **Map** per document, prompt unchanged (claims with names and dates exactly as written, cited), run concurrently
  (`ARTMIND_TOPIC_MAP_CONCURRENCY`, default 8).
- **Map cache**: each result is cached by (document content sha, subject text, map prompt version, model) under
  `logs/topic-map-cache/` (private, gitignored). A refresh maps only new or changed documents (§8.2).
- **Reduce by facet**: notes are grouped per subject (and domain when split), reduced per group into a section,
  then merged; a group over `REDUCE_INPUT_CHARS` is reduced per period first.
- **Page shape**: `## Overview`; one section per subject; `## Timeline` when cells are dated (one dated line per
  claim that carries a date); `## Where sources differ`. The page's frontmatter repeats the plan's `built` block.
  `check` is unchanged.

The page is written to `<vault>/_Topics/<slug>.md` (still `NEVER_INGESTED`), never mirrored into the graph
(§5.2); the plan's folder is written beside it in the same build.

## 8. Part E — freshness and refresh

### 8.1 The freshness check (no LLM)

For each plan:

1. **Short-circuit:** if every scoped domain's catalogue generation (`:DomainState.generation`, bumped by every
   write path) equals `built.generations`, the plan is **fresh**.
2. Otherwise re-count the grid (each cell's keyword `matching`, the same query as the dry run) and compare the
   gathered documents' content sha with the page's sources:

```json
"freshness": {"status": "stale", "new_documents": 12, "changed": 3, "retired": 1,
              "cells_grown": ["subject 1 / 2026"], "checked_at": "…"}
```

`status`: `fresh`, `stale` (any new, changed or retired document, or a grown cell), or `unknown` (the graph was
not reachable). `topic stale` (today: retired and changed sources only) is replaced by this check.

### 8.2 Incremental refresh

`artmind topic refresh <slug>` (tier-1 dry run, LLM calls): re-runs the gather; maps only documents not in the map
cache; re-reduces only subjects whose cells changed; rewrites the page and the plan's `built` block. Cost follows
how much changed. `--full` ignores the cache.

### 8.3 Schedule

- The freshness check runs in the background job that serve, chat-ui and admin-ui already run for catalogue health
  (`catalogue/refresh.py`), daily, no LLM, and stores its result beside the plan under `logs/` (private).
- The admin console's Topics tab lists plans with their freshness and what changed.
- `refresh: manual` (default) never refreshes on its own. `on_stale` refreshes after a check finds the plan stale;
  `weekly` refreshes at most once a week when stale. Both are opt-in per plan, run only in the admin console
  process, and respect `ARTMIND_TOPIC_MAX_COST_USD` per refresh (no refresh above it; the tab says why). For
  brain01 a refresh sends journal text to the configured provider, as ingestion does; opting in is that consent.

## 9. Part F — using the ladder elsewhere

### 9.1 Signposts that climb one level

The existing `next` hints (facts about the result) gain one rung each, never routing:

- a focused entity's co-mentions are cut → `entity-context` (exists: `hub`, `cut`);
- an anchor entity's evidence is cut → `survey` on its name (new hint in `entity-context`);
- `survey`'s `coverage` is low and a plan in scope matches its question terms → `query topics run <slug>`.

### 9.2 Orient

Peel 2 adds, per domain, a `levels` block: `n`, focused and anchor entity counts, views, passages, document and
chunk summaries, topic plans (slug, topic, subjects, freshness). Peel 1's card adds `topic_plans: <count>` beside
`views`.

### 9.3 Proposing subjects

`topic build --dryRun` lists anchor entities in scope whose names or aliases match the topic's terms (level 2)
as **suggested subjects**, with their `observation_count`; the build skill shows them and the user keeps or drops
them. The caller's `--facet` list is still what is built.

### 9.4 Answerability (phase 4 input)

For each must item in a benchmark tier, the lowest level that holds it says what a miss needs: a fact the graph
lacks → extraction; an item only a survey reaches → depth; only a plan reaches → breadth. The phase-4 audit
records this level per item.

## 10. Part G — benchmarks and topic material

Pages are not on any agent surface (§5.2), which removes the page leak. Plans remain, so:

- **At grading time:** `traces compare` flags a benchmark turn that ran `query topics run` for a plan whose topic
  or subjects match the turn's question (normalised equality or containment for texts of 40+ characters), in the
  per-question detail and in the flagged list. A fact about the run; no refusal anywhere.
- **At run time:** none. Building a plan from a question is legitimate use; the flag tells the reader which runs
  had that help.

## 11. Measurement

1. **Gather coverage (no LLM):** Q08's dated events whose documents the default grid gathers; target ≥ 19 of 22.
2. **Plan-run coverage (no LLM):** the same count over the rows `query topics run` returns within the output budget
   (what the agent can actually read), per subject.
3. **Agent with the plan:** a Q08 plan built in the vault, then Q08 rerun alone (orient variant): grade with
   Sonnet; compare with run I (5 of 7 missed) and the best baseline (6 of 7). The turn is flagged by §10, and the
   result is reported as "with plan" (it is not a like-for-like baseline comparison).
4. **Page coverage (LLM, people-facing):** grade the page text against Q08's tiers, to see what the synthesis
   keeps.
5. **Second shape:** banking Q15 (domain split), steps 1–3.
6. **The offer:** Q08 rerun with no plan in the vault after Part B; the answer carries the offer with sensible
   subjects and span; nothing is built.
7. **Members (§6.4), brain01 Q05:** discovery lists the candidates; with the members the user keeps, the gather
   covers ≥ 14 of 16 dated visits; then the Q05 plan run and a Q05 rerun with the plan, as in item 3. Also check
   Q11 (client companies) and Q16 (people at one employer), which share the shape.
8. **Freshness:** after adding a journal day that matches a subject, the check reports `stale` with
   `new_documents: 1` and the cell; an incremental refresh makes one map call plus the reduces for that subject.

brain01 plans, pages and grades stay in the vault or the scratchpad; only counts reach the repo.

## 12. Order of work

1. Part A (`coverage`) and the plan folder format + `query topics plan|run` + `topic plan validate` (§5), with
   tests on the parameters sent; runs live. Remove the `:TopicPage` graph copy (§5.2) with its migration.
2. Part C–D: the grid, faceted gather, dry run, concurrency, map cache, facet reduce; `topic build` writes plan +
   page. Measure §11.1–11.2, 11.4.
3. Agent surfaces: the skill rule (§5.3), page surfaces removed (§5.2), orient `levels` (§9.2), suggested subjects
   (§9.3), the climb hints (§9.1), the build skill (`artmind-build-topic`, admin console only: propose → dry run →
   explicit yes → build → run the plan). Measure §11.3, 11.5, 11.6.
4. Part E: freshness check, incremental refresh, background schedule, Topics tab. Measure §11.7.
5. Part G: the `traces compare` flag.
6. Members (§6.4): discovery in the dry run, member subjects, plan fields, new-candidate freshness, the
   build skill's choose step. Measure §11.7.

## 13. Risks and open questions

- **Cost on large corpora**: 300 documents ≈ 300 map calls; the dry run and `ARTMIND_TOPIC_MAX_COST_USD` guard it.
- **Subject quality**: a subject the corpus never uses shows 0 in the grid before any cost.
- **Keyword counts undercount meaning**: `matching` comes from the keyword leg; the vector leg still ranks inside
  each cell, but the grid's counts can understate a subject described in other words.
- **Plan output vs budget**: Q08's grid is 144 documents; `topics run` must page well (brief rows, per-cell runs).
  §11.2 measures what fits.
- **Open:** month slices inside a long dated span when a subject is dense in most years.
