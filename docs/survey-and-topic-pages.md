# Survey and topic pages: how they search, what they return

A reference comparing `artmind query survey` with `artmind topic build` (and
`topic refresh`). Design: [2026-11-01-summaries-survey-topics-design.md](superpowers/specs/2026-11-01-summaries-survey-topics-design.md)
(§2 survey, §3 topic pages, §6 the topic index); bounded output:
[2026-11-05-bounded-output-design.md](superpowers/specs/2026-11-05-bounded-output-design.md).

**In one line:** survey *ranks documents and returns evidence*; a topic build
*runs survey as its first step*, then has the LLM read those documents and write
a cited page.

## `artmind query survey` — a ranker, no LLM

Code: `artmind/survey.py` (`survey_search`).

### Mechanism

The question is embedded once, and also turned into a stopword-stripped keyword
query (`sanitize_lucene_query(drop_stopwords=True)`). Three legs then run, each
over a pool of `POOL = 300` hits:

| Leg | Searches over | How |
|---|---|---|
| **D** | `:DocSummary {level: document}` — one summary per document | vector + fulltext |
| **C** | `:DocSummary {level: chunk}` — one summary per chunk | vector + fulltext, first hit per document |
| **P** | `DocPassage` — ~1200-char raw text units | vector + fulltext, MaxP fused (`retrieval_rank.passage_fuse`), grouped by document |

Each leg collapses to a ranking of documents; the three rankings are fused with
weighted RRF (`retrieval_rank.survey_fuse`, weights from
`ARTMIND_SURVEY_LEG_WEIGHTS`). Modifiers:

- `--from` / `--to` — a date window on the document's `_valid_from`.
- `--by year|month [--perPeriod N]` — bucket the ranking by period (`periods` block).
- `--expand <doc_id>` — open named documents, same question, no re-ranking.
- `--legs` — run a subset of D, C, P.

### Output

A ranked list of **documents** — top 10 for `--shape full`, 50 for
`--shape brief` (`ARTMIND_SURVEY_TOP_DOCS` overrides both, 1-50). Per document:

- id, title, name, domain, date, status (current/superseded), `supersedes`
- document summary
- fused `score` and per-leg `ranks`; `hits` (passages, chunk summaries)
- best `passages` — verbatim source text with relevance
- best `chunk_summary`

In brief shape a row is a passage, a gist (an extractive sentence matched to the
question; `gist_source` says where it came from) or a line, allocated by
`brief.allocate` within the output budget.

Also in the payload:

- `topics` — existing `_Topics/` pages that cite at least two of the returned
  documents (via `CITES`; `stale.survey_topics`).
- `warnings` — e.g. domains with missing summaries, naming
  `artmind ingest summarise --domain <d>`.

**Nothing is generated.** Survey is read-only, proxied by `serve` and traced. The
caller (the agent or you) does the synthesis.

## `artmind topic build` — anchor, gather, map, reduce

Code: `artmind/topics/` — `anchor`, `gather`, `build`, `prompts`, `check`,
`render`, `store`, `index`, `stale`.

### Mechanism

1. **Anchor** (`anchor.py`). In graph domains the topic text goes through
   `entity-resolve`. A *clear* match — a single exact-name hit, or a top RRF score
   ≥ `ANCHOR_MIN_SCORE` (0.03) and ≥ `ANCHOR_MARGIN` (1.5) × the runner-up — anchors
   the page on that entity, bringing its description, its `_observation_set_hash`
   (for staleness) and up to `ENTITY_CHUNKS` (8) `entity-context` evidence chunks.
   Otherwise, and in chunks-only scopes, the page is anchored on the topic text.
2. **Gather** (`gather.py`). Calls
   `survey_search(topic, top_docs=max_docs, passages=8)` — survey is the retrieval.
   `max_docs` defaults to 12 (`--maxDocs`, or `ARTMIND_TOPIC_MAX_DOCS`, 1-50).
   Then the anchor's evidence chunks are added; their documents join while under
   the cap. Each document's material is capped at `MAP_INPUT_CHARS` (10,000).
3. **Map** (`prompts.map_prompt`). One LLM call per document. It answers
   `relevant: false`, or short factual claims each citing the passage ids they came
   from; ids that were not supplied are dropped (`parse_map`).
4. **Reduce** (`prompts.reduce_prompt`). One LLM call writes the page body from
   all the claims. Above `REDUCE_INPUT_CHARS` (40,000) the claims are reduced in
   groups, then merged (`merge_prompt`).
5. **Check** (`check.py`). Dates, numbers and identifiers in the body are verified
   against the passages they cite; results go to the frontmatter `checks`.
6. **Render and store** (`render.py`, `store.py`). Written to
   `<vault>/_Topics/<slug>.md`: frontmatter holds topic, domains, anchor, model,
   `prompt_version`, `max_docs`, `sources` (doc id, content sha, cited passages)
   and `checks`; a `## My notes` section is kept on rebuild. The page is then
   indexed as a `:TopicPage` (`index.py`).

If no document says anything about the topic, no page is written.

### Output

A synthesised, footnoted markdown page about the topic, built from about 12
documents; documents judged irrelevant in the map step are dropped and only cited
sources are recorded. The command's JSON reports `slug`, `path`, `anchor`,
`documents`, `calls`, `checks` and the `effect` block (`--dryRun` reports the plan
and makes no LLM call).

Cost: roughly one LLM call per gathered document plus one or more reduce calls.
The passage text is sent to the configured provider. Not proxied.

### Staleness

A page is stale (`stale.py`) when a cited document's version (content sha, else
staging fingerprint) differs from the recorded one, a cited document is no
longer a live `:Document`, or an entity anchor's `_observation_set_hash` changed.
`artmind topic refresh` rebuilds it.

## Side by side

| | `query survey` | `topic build` |
|---|---|---|
| Input | a question | a topic name |
| Retrieval | D + C + P legs, RRF-fused | the same survey (top 12, 8 passages each) + entity evidence when anchored |
| Uses the graph? | no, apart from document metadata and dates | yes: entity-resolve, entity description, entity-context chunks |
| LLM | none | per-document map + reduce |
| Output | ranked documents with raw passages and summaries | a synthesised, cited markdown page |
| Writes | nothing (read-only, proxied, traced) | `_Topics/<slug>.md` + `:TopicPage` index (not proxied) |
| Freshness | live on every call | a snapshot; flagged stale, rebuilt by `topic refresh` |
| Breadth | up to 50 documents | ~12 documents (max 50) |

## A topic page is never evidence

A `:TopicPage` carries no `Document` / `DocChunk` / `DocPassage` label and no
`PART_OF`, and sits in none of the vector-text, survey or entity-resolve indexes
(`test/test_topic_page_never_evidence.py`). Later queries never retrieve a page
as a source; survey's `topics` block only *points* to existing pages. `_Topics/`
is in `ingest.NEVER_INGESTED`.

## When to use which

- **Survey** — "which documents talk about X?", cross-document questions, and
  checkable evidence for an answer composed now.
- **Topic page** — a durable, cited write-up of one subject you will come back
  to, read with `artmind query topics show <slug>`.
