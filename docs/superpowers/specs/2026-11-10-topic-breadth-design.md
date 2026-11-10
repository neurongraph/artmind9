# Topic pages for breadth: coverage fact, offer, faceted gather

Date: 2026-11-10. Status: **design for review**. Parent spec:
`2026-11-08-classic-baseline-and-skill-simplification-design.md` (work plan §0.1; this is new work beside item D,
composites). Builds on topic pages from `2026-11-01-summaries-survey-topics-design.md` §3. Public checkout:
counts, ids and generic examples only; brain01 questions are named by id, never by content.

## 1. Problem

Some questions are answered by many documents, not a few. An overview or trend question ("what do I do in
this hobby and how has it changed", "what do our policies say about X across the bank") needs every matching
document read, and an agent turn reads ten or twenty of them.

Evidence, brain01 Q08 (an overview of two subjects over five years; run I and the 2026-11-10 rerun):

- Every run, every baseline included, misses 5–7 of its 7 must items. The tier asks for about 22 dated events.
- `survey --by year` for one of the two subjects reports 90 keyword-matching documents over five years and
  shows 10 (2 per year). The agent read those, noted in its answer that the years were partial, and stopped.
- An agent cannot read 90 documents in one turn, and should not try.

Banking shows the same gap differently: "depth for broad multi-document questions" (parent §4.3a; banking
Q15, Q18, Q25), where the matching documents sit across eight sibling domains, not across years.

Topic pages exist for exactly this (map per document, reduce, cited page), but two things stop them helping:

1. **Nothing suggests one.** The agent never runs `topic build` (skill rule: LLM cost), and nothing tells the
   user a page would answer better.
2. **The gather is one survey.** `topic build` runs one `survey` of the topic text, capped at 50 documents.
   Measured on Q08 (dry runs and survey calls, no LLM; "covered" = the event's journal day is among the
   gathered documents, necessary for the page to carry it, not sufficient):

   | Gather | Documents | Dated events covered |
   |---|---|---|
   | `topic build`, the question as topic, `--maxDocs 50` | 50 | 11 of 22 |
   | the same, first subject only | 50 | 13 of 22 |
   | the same, second subject only | 50 | 7 of 22 |
   | 2 subjects × each year, top 15 per year | 144 | 19 of 22 |
   | 2 subjects × each year, top 25 per year | 230 | 21 of 22 |

   Breadth comes from gathering **per facet**, not from a bigger single pass.

A third problem appeared on the way: the banking `cpd-r051…056` pages were built from benchmark questions
verbatim and read by baseline runs (study note 22, "Topic pages built from benchmark questions"). A feature
that invites question-shaped topics must not recreate that leak.

## 2. Who does what

The parent spec's principle holds: code decides what is mechanical, the agent decides what to look for, the
user decides what costs money.

| Part | Does | Never does |
|---|---|---|
| `survey` | Reports how many documents match and how many the result shows (`coverage`) | Suggest a topic, or judge whether the question needs more |
| Agent (query skill) | Reads `coverage`; for an overview or trend question offers a page, naming its facets | Run `topic build` |
| User | Says yes or no, after seeing the dry run (documents, calls, cost, what leaves the machine) | — |
| Agent (build skill, admin console only) | Proposes subjects; runs the dry run and, after the yes, the build; answers from the page's cited passages | Build without an explicit yes in this conversation |
| `topic build` | Plans the facet grid from orient's facts, gathers per cell, maps, reduces, checks, reports coverage | Guess subjects (it takes them from the caller), or build from a benchmark question |

## 3. Part A — the `coverage` fact

`survey` (both shapes) adds one block:

```json
"coverage": {"matching_documents": 90, "shown": 10, "by": "year"}
```

- `matching_documents`: distinct documents with a keyword hit in the searched scope (the keyword passage leg
  already computes them, `POOL` 300 deep; with `--by`, the sum of the periods' `keyword_hits`). When the pool is
  exhausted, the value is a floor and the block says `"at_least": true`.
- `shown`: documents the payload gives evidence for (passage or gist rows in brief; `documents` in full).
- `by`: the period when `--by` was used, else absent.
- Always present when `matching_documents > 0`. No threshold in code; the field is a fact.

`vector-text` gets no `coverage`: it ranks passages for a fact, and its keyword leg counts passages, not
documents. A question that needs breadth goes through `survey` already (skill: "a topic across documents").

## 4. Part B — offering a page (skill, one rule)

One rule in the orient variant's "Read every result" list (and in classic `artmind-query` at promotion):

> **`coverage`:** when the answer rests on a small share of `matching_documents` and the question asks for an
> overview, a trend or "everything about", answer from what you read, say how many more documents match, and
> offer a topic page: name the topic, its subjects (your parts) and its span. Do not build it.

- Chat-ui: the offer names the command for the vault owner (`artmind topic build …`); end users cannot build.
- Admin console: the user can answer "yes" and the build skill (Part F) takes over.
- Benchmarks: the run cannot answer, so no page is built; the answer still carries the offer, which the grader
  sees as text. A run never builds a page.

## 5. Part C — facets and the corpus's shape

A page's gather is a grid: **subjects × slices**. Subjects come from the caller; slices come from the corpus's
shape, read from orient peel 1 by code.

### 5.1 Facet kinds

| Kind | From | Used when |
|---|---|---|
| **subject** | `--facet "<question>"` (repeatable); default: the topic text alone | always (at least one) |
| **period** | `--by year\|month\|none\|auto` | the scope's documents are dated by content and span long enough |
| **domain** | `--split domain\|none\|auto` | the scope is a family root whose children hold matching documents |

Subjects are judgement (the agent's parts, or the user's words). Period and domain slices are mechanical.

### 5.2 `auto`: the shape rule (code, deterministic)

Read from orient peel 1's card for each scoped domain (`time.dated.from_content`, `time.span`, `unit.documents`,
`family` / `corpus.families`):

- **period = year** when at least 80 % of documents are dated from content and the span exceeds 24 months;
  **month** when the span is 3–24 months and documents number at least 60; otherwise **none**.
- **domain split** when the scope is a family root with two or more children holding at least one keyword
  match for some subject; otherwise **none**.
- Period and domain may both apply (a dated multi-domain family); the grid is then subject × domain × period,
  bounded by §6.2.

On the two vaults: brain01 (one domain, 1,710 of 1,711 dated, 59 months) → year, no split. banking (a family of
eight children, 58 documents, 6 months, mostly one date) → no period, domain split.

### 5.3 The dry run shows the grid

`topic build --dryRun` reports the plan before any call:

```json
"grid": {"subjects": 2, "slices": {"by": "year", "split": null},
         "cells": [{"subject": 1, "period": "2023", "matching": 16, "take": 15}, …]},
"documents": 144, "calls": {"map": 144, "reduce": 4}, "effect": {…, "llm": {"calls": 148, "est_cost_usd": …}}
```

`matching` per cell is the corpus's shape for this topic: the user sees where the material is before paying,
and an empty subject (0 matches everywhere) is visible as a wrong facet.

## 6. Part D — the faceted gather

### 6.1 Per cell

For each cell: `survey_search(domains=<cell domain or scope>, question=<subject>, top_docs=<take>,
passages=MAX_PASSAGES_PER_DOC, date_from/date_to=<period bounds>)`. Documents are merged across cells by
`doc_id`; a document found by several cells keeps every cell's passages (still capped at `MAP_INPUT_CHARS`) and
records the cells it came from (for the reduce, §7).

### 6.2 Depth and caps

- `take` per cell: `min(matching, --perCell)`, default `--perCell 15`; a cell whose `matching` is larger than
  `take` is marked `partial` in the report.
- Document cap `--maxDocs`, default 150, maximum 300 (`ARTMIND_TOPIC_MAX_DOCS` keeps its role; the old 1–50 range
  widens). When the grid asks for more, cells are cut fairly (round-robin by rank), and the dry run says so.
- No cell is skipped silently: the report lists every cell with `matching`, `taken`, `partial`.

### 6.3 Cost and time

One map call per document. The map step runs concurrently (`ARTMIND_TOPIC_MAP_CONCURRENCY`, default 8), as
extraction's chunk fan-out does; 150 documents at about 3 s per call then take about a minute. The model is
the configured topic model (`--model`, else the KG model).

## 7. Part E — map and reduce keep the specifics

The map prompt is unchanged (claims with dates and names exactly as written, cited). The reduce changes:

- **Hierarchical reduce by facet:** notes are grouped by subject (and domain when split), reduced per group into
  a section, then merged. A group above `REDUCE_INPUT_CHARS` is reduced per period first. This replaces the
  size-only grouping, so related claims meet in the same call.
- **Page shape:** `## Overview`; one section per subject (heading = the subject in plain words); `## Timeline`
  required when the cells are dated (one dated line per claim that carries a date, ordered); `## Where sources
  differ` as now. The "up to 6 sections" cap goes; the per-subject sections replace it.
- **Coverage in the page's frontmatter:** the grid, per-cell `matching` / `taken` / `partial`, documents
  gathered, documents the map found relevant, documents cited. `topic stale` and `query topics show` report it;
  a later `topic refresh` re-plans the grid (new documents in a period count).

`check` (uncited paragraphs, unsupported numbers, removed citations) is unchanged.

## 8. Part F — building from the admin console

New skill **`artmind-build-topic`**, loaded only by the admin console's agent (not chat-ui, not benchmarks; the
web UI loads it by profile). Steps:

1. **Propose.** From the conversation: the topic, the subjects (the parts of the question that need breadth),
   the domains. Slices default to `auto`.
2. **Dry run.** `artmind topic build "<topic>" --domain <d> --facet "<s1>" --facet "<s2>" --dryRun --compact`.
   Show the user: the grid (where the matches are), documents, LLM calls, estimated cost, the model, and that
   the gathered text is sent to that provider.
3. **Wait for an explicit yes** in this conversation. Changing facets means a new dry run.
4. **Build** the same command without `--dryRun`. The page is written to `<vault>/_Topics/` and indexed.
5. **Answer** from the page as the query skill says for topic pages: a guide, not evidence; cite its footnoted
   passages (`query chunks --idList …`), and report the page's coverage (partial cells).

The query skill's "never run `topic build`" stays for every other surface.

## 9. Part G — the benchmark leak guard

`topic build` refuses (exit 1, nothing written, no LLM call) when the topic or any facet matches a question in
the vault's benchmark material: `benchmarking/*.md` question lines, `plan_labels.yaml`, `retrieval_labels.yaml`.
Match = equal after normalising case, punctuation and whitespace, or one contains the other when both are at
least 40 characters. The message names the file, never the question text. No override flag: a benchmark
question's page belongs in a scratch `--out` folder (never indexed, §10).

## 10. Measurement

Benchmarks cannot measure a page by reading it (that is the leak). Measure the page itself:

1. **Gather coverage (no LLM):** for Q08, the share of the tier's dated events whose documents are gathered, as in
   §1's table; target ≥ 19 of 22 with the defaults.
2. **Page coverage (LLM, scratch folder):** build the Q08 page with `--out <scratchpad>` (not indexed, not in the
   vault), then grade the page text as an answer against Q08's tiers with the Sonnet grader. Compare with the
   best baseline answer (6 of 7 missed) and run I (5 of 7).
3. **Second shape:** banking Q15 (domain split, no period), the same two checks; the page goes to scratch too.
4. **The offer:** rerun Q08 alone (orient variant) after Part B; check the answer carries the offer with sensible
   subjects and span, and that no build ran.

brain01 pages and their grades stay in the vault or the scratchpad (private); only counts reach the repo.

## 11. Order of work

1. Part G (guard) and Part A (`coverage`), with tests: small, and the guard must exist before any page is built.
2. Parts C–E (grid, faceted gather, reduce): `topic build --facet/--by/--split/--perCell`, dry-run grid,
   concurrency. Measure §10.1–10.3.
3. Part B (one skill rule) and Part F (build skill). Measure §10.4; a live admin-console build by the user.

Each step is tested hermetically (gather calls asserted on the parameters sent, `test/_neo4j_recorder.py`), then
run live; new Cypher is run live before a step is called done.

## 12. Risks and open questions

- **Cost on large corpora.** A 300-document page is about 300 map calls. The dry run is the guard; the default
  cap is 150. Open: should the admin console show a cost ceiling (`ARTMIND_TOPIC_MAX_COST_USD`)?
- **Subject quality.** A subject the corpus does not use (0 matches) produces an empty section; the dry-run grid
  shows it first. Subjects stay the agent's and user's judgement.
- **Keyword counts undercount meaning.** `matching` comes from the keyword leg; a subject the documents describe in
  other words shows low counts. The vector leg ranks within each cell, so gathering is not limited to keyword
  matches, but the grid's `matching` is.
- **Pages go stale** as new journal days arrive; `topic stale` already reports retired sources; the grid gives a
  second reason (a period gained documents).
- **Open: month slices for a dated corpus over more than 24 months** when a subject is dense (more than
  `--perCell` matches in most years). `auto` could step down to months per subject; left out until a question
  needs it.
