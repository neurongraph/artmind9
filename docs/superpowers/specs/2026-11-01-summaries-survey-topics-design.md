# Summary tree, survey retrieval and topic pages (Spec B)

Status: approved design, 2026-11-01. Builds on Spec A
(`2026-10-31-vector-retrieval-passages-design.md`, merged 7de26fe): `:DocPassage` vectors,
2:1 vector/keyword RRF, MaxP to chunks, the embedding cache and `artmind retrieval`.
Pre-design notes: `2026-10-31-topic-pages-raptor-notes.md`.

## Goal

Answer **survey** questions — "what do the contracts say about termination", "compare the
approaches to X", "overview of Y" — which need coverage across documents. Spec A serves
**needle** questions (one passage answers it); its top 10 clusters in one or two
documents and cannot answer a survey. Chunks-only domains (`graph_extraction: false`)
have no graph to fall back on, so they need this most, but every domain gets it.

Three parts, built and measured in order:

1. A **summary tree** (RAPTOR's core idea, two levels above passages): one summary per
   `DocChunk`, one per document reduced from those, stored in staging and the graph.
2. **`artmind query survey`**: document-level retrieval fusing document summaries, chunk
   summaries and passages.
3. **Topic pages**: `artmind topic build` writes a cited page for a topic into the vault's
   visible, never-ingested `_Topics/` folder; `artmind query topics list|show` reads them.

## Non-goals

- A third tree level (clustered cross-document topic summaries). Deferred until the two
  levels are measured.
- Maintaining topic pages at ingest time. Pages are built on demand; keeping a taxonomy
  current per document is graph extraction again.
- Changing `vector-text`, the extraction summary (`document_context.json`) or entity
  synthesis (`projection synthesize`).
- A reranker (Spec A §9: no gain).
- The agent building or refreshing pages.

## Decisions

| Question | Decision |
|---|---|
| Which domains | All domains, always (no schema key). |
| Summary source | A chunk + document tree covering the whole document, not the 12,000-char extraction sample. |
| When and where | Built at ingest, staged in `summaries.json` (committed with the vault), written to the graph at commit, embedded by the permanent sweep. |
| Retrieval command | `artmind query survey`, separate from `vector-text` and from `query topics`. |
| Page commands | `artmind topic build|refresh|delete` (operator, not proxied); `artmind query topics list|show` (read-only, proxied, used by people and the agent). |
| Page location | `<vault>/_Topics/<slug>.md`, visible in Obsidian, never ingested (hard exclusion like `_Inbox/`), committed with the vault. |
| Graph domains | A topic that resolves to one entity is built *on* that entity (its synthesis + `entity-context` evidence), not from scratch. |
| Agent use | Pages and summaries guide the answer; answers cite passages. The agent never reads files directly: every read goes through an `artmind query` command, which also works for chat-ui through `serve`. |

## 1. Summary tree

### 1.1 Prompts (`artmind/summary_tree.py`, pure: prompts, parsing, keys, no I/O, no LLM)

- **Chunk summary**, one call per `DocChunk` with non-blank text. The prompt carries the
  document metadata and the chunk's position in the heading outline
  (`document_context.build_metadata`, `build_outline`) and the chunk text inside
  `<document>` tags with closing tags defused (as `summary_prompt` does). It asks for 3-6
  factual sentences and includes a hard character budget of `int(CHUNK_SUMMARY_CHARS * 0.85)`
  characters, so the LLM is instructed to stop before the cap. Keeping names, numbers and
  dates exactly as written, no preamble. Answer JSON `{"summary": "..."}`;
  `parse_summary` normalises to one line and, if longer than `CHUNK_SUMMARY_CHARS = 1200`,
  trims at the last sentence boundary (`. `, `! `, `? `) at or after 50 % of the limit, or
  at the last whitespace before the limit; never returns a fragment shorter than 50 % of
  the limit. The subject line comes from the schema (`subject` or the first sentence of
  `description`), so a schema without `entity_types` reads well.
- **Document summary**, reduced from the chunk summaries in chunk order. One call;
  answer `{"summary": "..."}` capped at `DOC_SUMMARY_CHARS = 2000` with the same
  sentence-boundary trim. The prompt includes a hard budget of `int(DOC_SUMMARY_CHARS * 0.85)`.
  - A one-chunk document: the document summary is its chunk summary, no call.
  - Chunk summaries longer than `REDUCE_INPUT_CHARS = 40000` together are reduced in
    consecutive groups first (each group to a ≤2000-char summary), then the group
    summaries are reduced. Recursion depth is whatever the length needs.
- `SUMMARY_PROMPT_VERSION = 3`, bumped on any prompt change (invalidates chunk summaries and,
  through `document_key`, document summaries). `DOC_PROMPT_VERSION = 4`, bumped when only
  the document-level reduce prompt changes.
- `chunk_summary_key(chunk_text, model) = sha1(SUMMARY_PROMPT_VERSION|model|sha256(text))`.
  `document_summary_key` = sha1 over the ordered chunk keys + model + `DOC_PROMPT_VERSION`.

### 1.2 Staging (`<kg>/<domain>/<doc>/summaries.json`)

```json
{
  "version": 1,
  "model": "<text model>",
  "prompt_version": 1,
  "document": {"key": "...", "summary": "..."},
  "chunks": [{"chunk_id": "...", "key": "...", "summary": "..."}]
}
```

- Written by `extract_kg` in the same atomic staging swap as the rest of the document,
  for graph and chunks-only domains alike, after the chunk files exist. In a graph domain
  it runs after the extraction steps; in a chunks-only domain it is the only LLM work.
- Reuse: a chunk entry whose `key` matches is kept without a call; the document summary
  is regenerated only when its key changes. Re-ingesting an edited document re-summarises
  the changed chunks and the document.
- Chunk calls run in the same pool and concurrency as extraction (`--maxWorkers`).
- Failure never fails the file: a chunk whose call fails or answers unusably gets no
  entry; the missing entry is the retry marker: the next `extract_kg` or
  `ingest summarise` re-asks exactly those chunks. Failures are reported in the file
  result (`summary_failed_chunks`) and the log. `document` is written only when every
  non-blank chunk has its entry (else `null`). The extraction loss guard does not look
  at summaries.
- `summaries.json` joins the vault-sync fingerprint only when present, so existing
  staging folders keep their fingerprint unchanged.
- `--reextract` keeps matching summaries (keys are content-based), so it costs no
  summary calls unless chunk text or the prompt changed.
- Unaffected: `document_context.json` (the extraction summary must exist before chunk
  extraction; this tree is built after).

### 1.3 Backfill: `artmind ingest summarise [--domain D] [--match GLOB] [--dry-run] [--compact]`

For already-staged documents: builds missing or outdated entries in `summaries.json`
from staged chunk text, writes the file atomically, commits those documents
(`_commit_document_tx`, idempotent) and runs the embedding sweep. `--dry-run` reports
per domain the documents and the number of calls it would make, and writes nothing.
Resumable. It reads staged chunk text, so it needs no `[ingest]` extra.

### 1.4 Graph

- `:DocSummary {id, level: 'chunk'|'document', doc_id, chunk_id (chunk level only),
  _domain, text, valid_from, valid_to, key}`; ids `"<chunk_id>:summary"` and
  `"<doc_id>:summary"`, deterministic.
- `(s:DocSummary)-[:SUMMARIZES]->(c:DocChunk)` or `->(d:Document)`.
- Written from staging in the document's commit transaction (`summary_store.write_document_summaries`,
  same retire-then-MERGE shape as `passage_store`): an unchanged `key` keeps the node and
  its vector; a changed one replaces `text` and clears `embedding`.
- Lifecycle: when a document is superseded or retired, its summaries swap to
  `:DocSummaryHistory` with it (and back on restore), keeping their vectors, as passages
  do (`lifecycle._transition`, `_retract_prior_version`).
- Embedding: the permanent sweep behind `ingest embed-passages` also embeds summaries
  whose `embedding` is null or whose `_embed_key` differs, with the `search_document: `
  prefix, through the embedding cache. Its payload gains `summaries_embedded`.
- Indexes (`setup._setup_neo4j`): `summary_embedding` (vector, `WITH [s._domain, s.level]`)
  and `summary_text_ft` (fulltext on `text`), plus a uniqueness constraint on
  `DocSummary.id` and `DocSummaryHistory.id`.
- Not in `graph_snapshot` (staging is the source of truth, as for passages).

### 1.5 `pattern10` carries document summaries

`query graph pattern10` adds `summaries` (a list of `{doc_id, name, summary}`, one per
matched document; null summary when none is stored) to its payload. The Obsidian plugin
fixtures are regenerated.

### 1.6 Privacy

Summaries are corpus content: committed with the vault's private repo like
`observations.json`, never copied into this checkout's code, tests or commits. Building
them sends every chunk's text to the configured provider once more; calls are logged in
`llm_calls.log`.

## 2. `artmind query survey`

### 2.1 Command

`artmind query survey --domain D [--domain ...] [--topDocs 10] [--passages 1] [--compact] "<question>"`.
Proxied by `serve`, traced as its own `survey` span layer. `--topDocs` 1-50;
`--passages` 1-3.

### 2.2 Ranking (`artmind/survey.py`; fusion in `retrieval_rank`)

Three legs, each producing a document ranking, all scoped by in-index `WHERE _domain IN
$domains` (expanded with `expand_domain_family`, as Spec A):

| Leg | Searches | To a document |
|---|---|---|
| D | `:DocSummary` level `document`, vector + keyword fused 2:1 | one per document |
| C | `:DocSummary` level `chunk`, fused 2:1, pool 100 | best hit per document (MaxP) |
| P | `:DocPassage`, Spec A's fused ranking, pool 100 | best hit per document (MaxP), plus hit count |

The final document order is RRF (k=60) over the three leg rankings, equal weights
(`SURVEY_LEG_WEIGHTS = {"D": 1, "C": 1, "P": 1}`). Weights change only on eval evidence
(§4.2).

### 2.3 Payload

```json
{
  "parameters": {"topDocs": 10, "passages": 1, "pool": 100, "domains": ["..."]},
  "documents": [{
    "doc_id": "...", "title": "...", "domain": "...", "summary": "...",
    "ranks": {"D": 1, "C": 3, "P": 2}, "hits": {"passages": 7, "chunk_summaries": 2},
    "passages": [{"id": "...", "chunk_id": "...", "text": "...", "relevance": 0.71}],
    "chunk_summary": {"chunk_id": "...", "text": "..."}
  }],
  "topics": [{"slug": "...", "topic": "...", "built": "...", "stale": false, "overlap": 4}],
  "warnings": []
}
```

- `topics`: pages under `_Topics/` whose `sources` include at least 2 of the returned
  documents and whose `domains` overlap the scope, by overlap descending. Read from page
  frontmatter only (`topics.store.list_pages`); `stale` computed as §3.4.
- A document's `summary` is null when it has none.

### 2.4 Missing summaries

Documents without summaries simply have no D/C entries; a domain never summarised ranks by
leg P alone. `warnings` then holds `summaries missing for N of M documents in scope; run
artmind ingest summarise --domain D` (one line per domain). Missing indexes raise the
Spec A §4.5-style error naming `artmind setup`.

### 2.5 `query plan`

`routes.yaml` gains a `survey` route, chosen by lexical cues ("across", "compare", "what
do the … say", "overview", "all the", "which … mention") and by the decision model when
configured. A wholly chunks-only scope plans `survey` or `search` from the same cues
(today: always `search`).

### 2.6 How `artmind-query` uses the new commands

Route and Views still come first for graph domains. Summaries are never queried
directly; they arrive through `query survey` and `pattern10`.

| The question asks for | Graph domain | Chunks-only domain |
|---|---|---|
| A fact (who, when, a figure, a quote) | unchanged: `entity-context` / patterns, grounded with `vector-text` | `vector-text --topK 10` |
| One document ("summarise X", "what does X say about Y") | `pattern10` (now with the summary); read chunks only for detail | same |
| One entity, broadly ("tell me about Acme") | unchanged: `entity-context`; if `query topics list` has a page for it, use the page to organise | — |
| A topic across documents ("what do the contracts say about…", "compare", "overview") | `query survey` → `query topics show <slug>` if listed → `query chunks --idList` for passages to cite | same |
| Patterns over time | unchanged: insights | — |

Rules written into the skill:

- Survey vs vector-text: several documents or coverage needed → `query survey`; one
  passage could answer → `vector-text`.
- New fallback rung: a multi-document question whose `vector-text` rows come from one or
  two documents → re-run as `query survey`.
- Summaries are LLM text, not evidence: use them to choose documents and sketch an
  overview, quote facts from passages. For "summarise X" the stored summary may be the
  answer, labelled as artmind's summary of X, with X cited.
- Topic pages: read one when `query survey` lists it or the user asks for it; cite its
  passages, not the page; say when it is stale; never run `topic build` or `refresh`.
- Mixed domain sets: one `query survey` call over every domain.

## 3. Topic pages

### 3.1 Commands

- `artmind topic build "<topic>" --domain D [--domain ...] [--maxDocs 12] [--slug S] [--out DIR] [--model M] [--dry-run] [--compact]`
- `artmind topic refresh <slug> | --stale | --all [--dry-run] [--compact]`
- `artmind topic delete <slug>`
- `artmind query topics list [--domain D] [--stale] [--compact]` and
  `artmind query topics show <slug> [--compact]`: read-only, proxied, traced.

`topic *` is not proxied (writes files, makes LLM calls). `--dry-run` prints the anchor,
the documents it would read and the number of calls, and writes nothing. `--out`
defaults to `<vault>/_Topics/`; outside a vault there is no default and `--out` is
required.

### 3.2 Pipeline (`artmind/topics/`)

1. **Anchor** (`anchor`). Graph domains only: `entity-resolve` the topic text. One match
   above `ANCHOR_MIN_SCORE` and ahead of the next by `ANCHOR_MARGIN` (constants in
   `anchor.py`, set in the plan from `entity-resolve`'s score scale) → entity anchor
   (key, name, synthesis `description`, `_observation_set_hash`, and its `entity-context`
   evidence chunk ids). Otherwise, and in chunks-only domains, a topic anchor.
2. **Gather** (`gather`). The survey ranking (`survey.rank_documents`, the function behind
   §2) picks the top `--maxDocs` documents with their matched passages. An entity anchor
   adds the documents behind its evidence chunks, keeping the cap.
3. **Map** (`build`, prompts in `prompts`). One call per document: the topic, the entity
   card when anchored, the document's title, date and summary, and its matched passages
   (up to `MAP_INPUT_CHARS = 10000`), each tagged with its passage id. Answer
   `{"relevant": bool, "notes": [{"claim": "...", "passages": ["<id>", ...]}]}`.
   `relevant: false` drops the document.
4. **Reduce** (`build`). One call: the topic, the entity card, all notes with their ids →
   the page body in sections: an overview; up to 6 sections with headings the model
   chooses; **Where sources differ** (both claims kept, never blended); **Timeline** when
   the sources carry dates. Every sentence ends with citation markers naming passage ids.
   Notes longer than `REDUCE_INPUT_CHARS` are reduced in groups first, as in §1.1.
5. **Check** (`check`, pure). Citations naming an id not supplied to the build are removed;
   paragraphs with no citation are counted; numbers in a sentence that appear in none of
   its cited passages' text are counted. Counts go into frontmatter `checks` and the
   command's output.
6. **Render and write** (`render`, `store`). Citations become footnotes: a vault note →
   `[[<note path>#<heading>|<title>]]` using the chunk's `header_path`; a converted
   binary → `[[<source file>|<title>]]`; each footnote names its passage id. Written
   atomically (temp file in `.artmind/logs/`). An existing `## My notes` section is
   carried over verbatim.

Cost per page: at most `--maxDocs` map calls plus one or two reduce calls. Calls are
logged in `llm_calls.log`.

### 3.3 Page format

```markdown
---
artmind_topic: 1
topic: "Termination rights"
slug: termination-rights
domains: [contracts_vector]
anchor: {kind: topic}
built: 2026-11-02T10:00:00Z
model: <text model>
prompt_version: 1
sources:
  - {doc_id: "...", name: "MSA-Acme.pdf", content_sha: "...", passages: ["...", "..."]}
checks: {uncited_paragraphs: 0, unsupported_numbers: 0, removed_citations: 0}
---
> Generated by `artmind topic build`. `artmind topic refresh` rewrites everything above
> "My notes".

## Overview
... [^1][^2]

## Where sources differ
...

[^1]: [[contracts/MSA-Acme.pdf|MSA Acme]] §12.2 — passage `<id>`

## My notes
```

An entity anchor records `anchor: {kind: entity, key: "...", observation_set_hash: "..."}`.

### 3.4 Staleness (`stale`)

A page is stale when a source document's current content sha differs from the recorded
one, a source document is retired or superseded, or, for an entity anchor, the entity's
`_observation_set_hash` changed. `query topics list` checks every listed page with one
graph query. `refresh` re-runs the whole pipeline, survey included, so new documents on
the topic are picked up.

### 3.5 Vault rules

- `_Topics` joins `NEVER_WALKED` in `ingest.py`, so it is never ingested whatever the
  mappings say (a `**` mapping included).
- Pages are committed with the vault (Obsidian Git); nothing writes them to `.gitignore`.
- `paths.TOPICS_DIR = <vault>/_Topics`.

### 3.6 Code layout

`artmind/topics/`: `anchor`, `gather`, `prompts`, `build` (map and reduce), `check`,
`render`, `store` (frontmatter, list, atomic write, `## My notes`), `stale`. CLI in
`cli.py` (`topic` group, `query topics` subgroup), routed in `COMMAND_GROUPS`.

### 3.7 Privacy

Pages hold private corpus content: vault only, never copied into this checkout's code,
tests or commits; tests use synthetic text. A build sends the matched passages to the
configured provider.

## 4. Testing, evaluation and rollout

### 4.1 Hermetic tests (mocked LLM, synthetic text, no Neo4j)

- Summary tree: cache hits and misses by chunk sha, prompt version and model; one-chunk
  documents; grouped reduce; a failed chunk summary leaves the file staged and a
  `summary` step `failed`, retried next run; `--reextract` makes no summary calls on
  unchanged chunks.
- Graph writes through `test/_neo4j_recorder.py`: assert the Cypher and parameters sent
  for the `:DocSummary` commit, the history label swap and the sweep — never counts alone.
- Survey: three-leg RRF as pure functions in `retrieval_rank`; the missing-summary
  fallback and its warning; `topics` overlap rule.
- Topics: citation removal, uncited paragraphs, unsupported numbers, `## My notes`
  carried over, each staleness rule, frontmatter round-trip, `_Topics/` excluded from
  ingest under a `**` mapping, `--out` required outside a vault.
- Contracts already in the suite: `cli_guide` routing, `routes.yaml` templates against
  the Click tree, Obsidian plugin fixtures (regenerated for `pattern10.summary`).

### 4.2 Checkpoint C (after slices 1-2)

1. In `banking_corpus` and `nbs_audit_2026`: `artmind setup`, then
   `env -u ANTHROPIC_BASE_URL artmind ingest summarise --dry-run`, then without
   `--dry-run` (about 1,444 and 1,401 chunk calls).
2. Add about 10 more hand-written survey labels per vault (private, in
   `<vault>/benchmarking/retrieval_labels.yaml`).
3. `artmind retrieval eval` scores survey labels with `query survey` by documents
   covered @10, against `vector-text` passages grouped by document, and reports each leg
   alone and in pairs.
4. Pass: `query survey` docs@10 ≥ the grouped baseline + 0.10 on both vaults, with needle
   metrics unchanged. Otherwise tune leg weights on the ablation, or stop before topic
   pages.

### 4.3 Checkpoint D (after slice 3)

Build one page per survey label in both vaults. Measure the share of each label's
`relevant_docs` the page cites, the `checks` counts (target: 0 unsupported numbers, at
most 1 uncited paragraph per page) and removed citations; the user reads three pages.
Results stay in the vault.

### 4.4 Rollout (additive; no migration)

1. `just dev-install`; restart `serve`, `chat-ui`, `admin-ui`.
2. In each vault: `artmind setup` (summary indexes), then
   `env -u ANTHROPIC_BASE_URL artmind ingest summarise --dry-run`, then without it.
3. Skills reach vaults through the existing symlinks; the `_Topics/` exclusion is code,
   so no `artmind init` is needed.

### 4.5 Docs and skills, same change

`CLAUDE.md` rows (`summary_tree`, `summaries.json`, `survey`, `topics/`, `_Topics/`);
artmind-query (§2.6 table, rules, fallback rung); group docstrings for `query`, `ingest`,
`topic`; `justfile` recipes; `docs/vault.md` (`_Topics/`, `summaries.json`).

### 4.6 Build slices

1. Summary tree: `summary_tree`, staging in `extract_kg`, `summary_store` at commit,
   lifecycle swap, sweep, indexes, `ingest summarise`.
2. `query survey`, its `retrieval eval` mode, `pattern10.summary`, the `query plan`
   route, the artmind-query changes. **CHECKPOINT C.**
3. Topic pages: `artmind/topics/`, `topic` commands, `query topics`, `_Topics` exclusion,
   `topics` in the survey payload. **CHECKPOINT D.**
4. Docs and fixtures (§4.5).

The plan is written on Opus; the build runs in a Sonnet session with Sonnet and Haiku
subagents, stopping at both checkpoints.
