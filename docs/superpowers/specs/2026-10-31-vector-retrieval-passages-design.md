# Vector retrieval on passages (Spec A)

Status: design, 2026-10-31.

Follow-on: Spec B (document summaries, RAPTOR-style retrieval, topic pages) is
designed after this one is built and measured. Its pre-design notes are in
`2026-10-31-topic-pages-raptor-notes.md`.

## Goal

Make `artmind query vector-text` (and `entity-context`'s chunk ranking) as good as
it can be without a cross-encoder reranker, and measure it. Chunks-only domains
(`2026-10-30-chunks-only-domains-design.md`) depend on vector-text alone, so its
defects are their answer quality.

## Defects fixed

| # | Defect | Where today | Fix |
|---|---|---|---|
| 1 | RRF fuses only `topK` (default 5) rows per leg | `vector_query.vector_text_search` passes `topK` to both legs | Each leg fetches a deep pool; fuse, then cut (§4.2) |
| 2 | Domain filter runs after ANN over the whole graph (`candidateK = topK*5*n`), so a small domain returns 0-2 rows | `chunk_embedding` has no filter properties; `WHERE` is outside `SEARCH` | `passage_embedding` declares `WITH [p._domain]`; the domain filter goes inside `SEARCH` (§3.4, §4.2) |
| 3 | Returned `score` mixes cosine (0-1) and BM25 (unbounded) | `_rrf_combine(..., annotate=False)` | Fused `score`, absolute `relevance`, `matched_by` (§4.3) |
| 4 | Embeddings ignore the model's task prefixes, carry no title/heading context, and are one vector per 6000-char chunk | `ingest.py` embeds raw chunk text; `embed_question` raw question; `header_path` (already on `DocChunk`) is never embedded | Passages (~1200 chars) embedded as `search_document: <title> > <headings>\n<text>`; questions as `search_query: ...` (§3) |
| 5 | Top-K can be K chunks of one document | no diversity step | Left open: MMR was built and removed, no measured harm (§9.1) |

## Non-goals

- A cross-encoder (or LLM) reranker. Revisit only if the eval (§6) stays below the
  thresholds in §6.4 after this change.
- Changing the extraction chunk size. `DocChunk` stays at `ARTMIND_KG_CHUNK_SIZE`
  (default 6000). Once it no longer carries a vector, its size is an extraction-only
  choice, measured separately (Spec C, noted in the Spec B notes file).
- Document summaries, survey retrieval, topic pages (Spec B).
- Supporting Neo4j older than 2026.02. Every instance in use runs 2026.02 or later,
  where filtered vector search (`SEARCH ... WHERE` inside the index) is GA.
- `entity-resolve` and `entity_name_candidates`: unchanged (they search `Entity`
  embeddings and names, not text).

## 1. Data model

```
(:DocPassage {id: "<chunk_id>_p01", chunk_id, doc_id, _domain, seq,
              char_start, char_end,   -- offsets inside the parent chunk's text
              text, embedding, _embed_key})
  -[:PART_OF]->(:DocChunk {id, doc_id, _domain, text, header_path, ...})
  -[:PART_OF]->(:Document)
```

- `:DocPassage` carries every text-retrieval vector. `:DocChunk` keeps its id, text
  and provenance role (observations' `EXTRACTED_FROM`, `chunks --idList`) and **loses
  its `embedding`**.
- `DocChunk.header_path` already reaches the graph (staged from `chunks_meta.json`); the
  passage's embedded context reads it from there.
- History: `:DocPassageHistory`. A history passage keeps whatever vector it had: the
  label swap alone takes it out of every index, exactly as for chunks, so a restore
  needs no re-embedding.
- A Neo4j vector index covers one vector per node, so passages must be nodes; a list of
  vectors on `DocChunk` cannot be indexed.

## 2. Splitting and embedding text

### 2.1 `artmind/passages.py` (new, pure)

`split(chunk_text: str) -> list[Passage]` with `Passage(seq, char_start, char_end, text)`:

- Target `ARTMIND_KG_PASSAGE_CHARS` (default 1200), overlap 150 chars.
- Splits on paragraph boundaries, then line breaks, then sentence boundaries, then
  whitespace; never mid-word. The next passage starts at a line start inside the
  overlap when there is one.
- A table cut across passages gets its header row reattached to each later passage,
  reusing `ingest._reattach_table_headers` (moved, with its two row helpers, to a new
  `artmind/md_tables.py` that both modules import).
- `char_start`/`char_end` slice the parent chunk's text exactly
  (`chunk_text[start:end]` is the passage text before header reattachment).
- A chunk shorter than the target yields one passage spanning it.
- `SPLIT_VERSION` (int constant) is bumped whenever the rules or defaults change.
- No I/O, no Neo4j, no embedding calls.

### 2.2 Prefix table (`embed_prefix`)

A small per-model table, keyed by the embedding model's base name:

| Model | document prefix | query prefix |
|---|---|---|
| `nomic-embed-text` | `search_document: ` | `search_query: ` |
| any other | none | none |

`PREFIX_VERSION` (int) is bumped when the table changes.

### 2.3 What is embedded

```
search_document: <document title> > <h1> > <h2>
<passage text>
```

The title is the Document's `title` (else `name`); the heading path is the parent
chunk's `header_path`. The prefix line is embedded only; `DocPassage.text` stores the
passage text alone (what the fulltext index and the agent see).

**Amended 2026-11-01:** passages are embedded as `search_document: <passage text>`;
the `<title> > <headings>` line is stored as `DocPassage.context` for display but not
embedded (`PREFIX_VERSION` 2). On banking_corpus it was a median 23% of the embedded
text, shared by every passage of a document and repeating the heading the text opens
with, so siblings embedded alike: vector-only needle recall@5 0.82 without it, 0.72
with it.

Every stored vector uses the document prefix: passages, `UserChat` (no title line) and
`Entity` (name + description, as today). Every search uses the query prefix:
`embed_question` (vector-text, entity-resolve, entity-context) and the name-vocabulary
lookup in `canonicalize.py`. Helpers in `extraction.py`: `embed_document`,
`embed_query`, `embed_prefixes`, `embed_key`.

### 2.4 `_embed_key` and `_split_key`

`_embed_key = sha1(model, PREFIX_VERSION)`, stored on every passage, `UserChat` and
`Entity` vector. A vector whose key differs from the current one is stale and is
re-embedded by its sweep (passages and chats: §3.2; entities: the existing entity
sweep, which also selects on the key).

Passages also carry `_split_key = "<SPLIT_VERSION>:<passage chars>"`, what they were cut
under, so a change of the rules or of `ARTMIND_KG_PASSAGE_CHARS` re-splits them; an
`_embed_key` change only re-embeds.

## 3. Ingest, sweep and lifecycle

### 3.1 Passages are derived at commit, not staged

`_commit_document_tx` writes each chunk's passages right after the chunk (same
transaction), from the chunk's own text, with `embedding` absent. Committed staging is
unchanged: a passage is a pure function of chunk text and `SPLIT_VERSION`, so every
machine running `vault sync` or `pull-kg` derives identical passages. Vectors stay
gitignored and machine-local, as chunk vectors are today (`docs/vault.md`,
"Embeddings").

During `extract_kg`, the chunk embedding step (`_embed_text(embed_model, chunk_text)`)
is removed; passages are embedded by the sweep that already runs after every commit
(§3.2). Vectors are cached in one machine-local, content-addressed SQLite file,
`<data dir>/embedding_cache.sqlite`, keyed `sha1(_embed_key, embedded text)`: the sweep
reads it before calling the model and writes to it after, so a re-commit, a new version
with unchanged passages, or a rebuilt graph never re-embeds identical text on that
machine. The per-document chunk-vector sidecar (`embeddings.json`) and its read, write
and carry helpers are removed; the gitignore line stays so old files are never
committed.

A commit writes each passage holding exactly its new properties (no vector); the
post-commit sweep then fills vectors from the cache.

### 3.2 The passage sweep (permanent)

`embed_sweep.embed_missing_chunk_embeddings` becomes `reconcile_passages(session, *,
chunk_ids=None, domain=None, doc_ids=None)`:

1. Build passages for every non-blank `DocChunk` with none, or whose passages carry an
   old `_split_key`; replaces that chunk's passages in one write. A chunk with blank
   text has no passages and is never selected.
2. Embed passages where `embedding IS NULL OR _embed_key <> $key`.
3. On unscoped or domain-scoped runs: re-embed `UserChat` nodes with a stale or missing
   `_embed_key`.

Status fields follow the rename: `unembedded_chunks` → `unembedded_passages`
(`projection` status, the Obsidian plugin's checklist), `chunks_embedded` →
`passages_embedded`.

Same resumable loop, progress logging and `strict` behaviour as today. Every caller of
the chunk sweep (`_sweep_chunk_embeddings`, `write-to-graph`, `vault sync`,
`table2graph`, `projection.py`'s unembedded count, the CLI) moves to it. The CLI
command `ingest embed-chunks` is renamed `ingest embed-passages` (no alias); the
`justfile` and docs follow.

This step is permanent: a later passage-size or prefix change is rolled out by the same
sweep.

### 3.3 Lifecycle

Passages follow their chunk through every label swap:

- **Version supersede** (`ingest._retract_prior_version`, `DocChunk` →
  `DocChunkHistory`): the document's passages swap to `DocPassageHistory` in the same
  transaction. The new version's passages reuse a node when their deterministic id
  matches (`<chunk_id>_pNN`), like chunks.
- **Retire / restore** (`lifecycle._transition`): the same swap in both directions; a
  restored passage still has its vector.
- There is no hard delete of document chunks in the codebase (retraction is a label
  swap); nothing to add there.

### 3.4 Indexes (`setup._setup_neo4j`)

Created:

```
CREATE VECTOR INDEX passage_embedding IF NOT EXISTS
  FOR (p:DocPassage) ON p.embedding WITH [p._domain]   -- exact syntax confirmed in the live run (§7)
  OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}
CREATE FULLTEXT INDEX passage_text_ft IF NOT EXISTS FOR (p:DocPassage) ON EACH [p.text]
CREATE INDEX passage_chunk_id IF NOT EXISTS FOR (p:DocPassage) ON (p.chunk_id)
CREATE CONSTRAINT passage_id IF NOT EXISTS FOR (p:DocPassage) REQUIRE p.id IS UNIQUE
```

No longer created: `chunk_embedding`, `chunk_text_ft`. Their only readers
(`full_text_search`, `entity-context`'s `chunk_keyword`) move to passages; the rest of
the references (setup's summary list, `graph_snapshot` schema block, `lifecycle` and
`ingest` docstrings) are updated. `user_chat_embedding` and `user_chat_text_ft` stay.

### 3.5 Graph snapshot

`graph_snapshot` export omits `DocPassage`/`DocPassageHistory` nodes (derivable) and
lists the passage indexes in its `schema` block. Restore runs `reconcile_passages`,
as it re-embeds today. Snapshot files keep today's size.

### 3.6 Legacy cleanup (temporary, one file)

`artmind/migrations/chunk_vectors_to_passages.py`, called once from `setup_all`;
nothing else imports it. Its tests are `test/test_migration_chunk_vectors.py`.

- Does nothing unless `chunk_embedding` or `chunk_text_ft` exists.
- If any `DocChunk` has no `DocPassage`: drops nothing; reports the domains still
  lacking passages and the command to run (`artmind vault sync` or
  `artmind ingest embed-passages`).
- Otherwise: drops `chunk_embedding` and `chunk_text_ft`, removes `DocChunk.embedding`
  and `DocChunkHistory.embedding` in batches, and reports `legacy chunk vectors: none`.
- `setup`'s output always carries the report line (`Legacy chunk vectors: ...`).

The module docstring states the removal condition: every graph in use reports `none`.
A GitHub issue is filed at merge with that condition; removal deletes the module, its
call site and its test file. A fresh graph never needs it, since the permanent setup no
longer creates the legacy indexes.

## 4. Query path

### 4.1 Scope

Each `--domain` is expanded to an explicit list with `graph_query.expand_domain_family`
(a domain plus every descendant holding data), so the in-index filter is a plain
`p._domain IN $domains`; in-index `WHERE` supports only a subset of predicates and
`domain_predicate`'s `STARTS WITH` rollup is not in it.

### 4.2 `vector_text_search`

1. **Pool.** `pool = max(50, 10 * topK)`.
2. **Vector leg:**
   ```
   CYPHER 25
   MATCH (p:DocPassage)
     SEARCH p IN (VECTOR INDEX passage_embedding FOR $q
                  WHERE p._domain IN $domains LIMIT $pool)
   ```
   **Fulltext leg:** `db.index.fulltext.queryNodes('passage_text_ft', $ft)` filtered to
   `$domains`, `LIMIT $pool`. `--asOf` stays a post-filter on both legs (rare; the deep
   pool keeps results from coming back short). `UserChat` keeps its own two legs
   (`user_chat_embedding`, `user_chat_text_ft`) at the same pool size; a chat is one
   unit, with no passages.
3. **Fuse** (`passage_fuse`): RRF, k=60, per passage (and per chat) over the two
   rankings, the vector leg weighted 2 and the fulltext leg 1 (`LEG_WEIGHTS`,
   amended 2026-11-01: an equal vote let BM25 matches on generic question words
   outrank vector hits).
4. **MaxP** (`passage_fuse`): passages grouped by `chunk_id`; a chunk's score is its
   best passage's fused score; the chunk records that passage and `passages_matched`.
5. **Diversity**: removed 2026-11-01 (§9.1). MMR with a same-document floor moved
   no metric on either eval vault; units stay in fused-score order.
6. **Return** the top `topK` units: the parent chunk (small-to-big) or the passage
   (`--context passage`).

### 4.3 Output

Existing keys keep their place (traces read `chunk.id`; the plugin and skill read
`chunk`/`document`):

```json
{
  "domains": ["..."], "query_type": "vector_text", "question": "...",
  "parameters": {"topK": 5, "pool": 50, "context": "chunk"},
  "warnings": [],
  "rows": [{
    "chunk": {"id", "name", "doc_id", "header_path", "text"},
    "document": {"id", "name", "path", "source_path", "_domain", "superseded_by"},
    "source_type": "document",
    "score": 0.0327,
    "relevance": 0.71,
    "matched_by": ["vector", "fulltext"],
    "passage": {"id", "char_start", "char_end"},
    "passages_matched": 3
  }]
}
```

- `score`: fused rank score; comparable within one result only.
- `relevance`: the highest cosine of any of the unit's passages against the question,
  0-1, comparable across queries; `null` when only the fulltext leg found the unit.
- `--context passage`: `chunk.text` is replaced by the passage text; `passage` offsets
  still refer to the parent chunk.
- Chat rows keep their `chat` key and gain `score`, `relevance`, `matched_by`.

CLI: `query vector-text` gains `--context chunk|passage` (`--diversity` removed, §9.1);
`--topK` is capped at 50.

`vector_search` and `full_text_search` are replaced by private leg queries inside
`vector_text_search`; nothing outside `vector_query.py` calls them.

### 4.4 `entity-context` chunk ranking

In `_entity_context_queries`:

- `chunk_refs`: each of the entity's chunks scores
  `max(vector.similarity.cosine(p.embedding, $queryVector))` over its passages (MaxP);
  `null` when the chunk has no embedded passage (ranks after scored ones, as today).
- `chunk_keyword`: `passage_text_ft`, restricted to passages whose `chunk_id` is in the
  entity's chunk ids; the best rank per chunk is kept.
- `_rank_chunk_refs`, connection ranking (by `Entity` embeddings) and the output shape
  are unchanged.

### 4.5 Errors

- Passage index missing (graph not migrated): `ClickException` naming
  `artmind ingest embed-passages` (or `vault sync`), then `artmind setup`.
- Embedding service unavailable: fulltext-only results with
  `warnings: ["vector leg unavailable: <reason>"]`, `relevance: null`; never a silent
  empty result.
- Question empty after Lucene sanitising: vector leg alone.
- `--topK` outside 1-50: `ClickException`.

### 4.6 Code layout

`vector_query.py` keeps its public functions. New pure helpers, unit-testable with no
Neo4j: `passages.split`, `embed_prefix` (in `extraction.py` beside `embed_text`),
`passage_fuse` (in `retrieval_rank.py`).

## 5. Rollout per graph

0. **Baseline**, on the commit that adds the eval (§6) and nothing else: `artmind
   retrieval labels generate`, add 10-20 hand-written questions, `artmind retrieval
   eval` → baseline run.
1. `just dev-install`; restart `serve`, `chat-ui`, `admin-ui` (stale daemons serve old
   code).
2. Inside the vault: `artmind vault sync` (or `artmind ingest embed-passages`) builds
   passages and vectors. Indexes need not exist for the writes. Then
   `artmind ingest embed-entities --domain <d>` for each graph domain, so entity vectors
   carry the document prefix (§2.3).
3. `artmind setup`: creates the passage indexes (Neo4j populates them from the
   passages already written); the migration drops the legacy indexes and vectors and
   prints `legacy chunk vectors: none`.
4. `artmind retrieval eval --compare <baseline>`; decide on a reranker (§6.4).
5. When every graph reports `none`: delete the migration (§3.6).

Between steps 1 and 3 `vector-text` and `entity-context --query` fail with the §4.5
message rather than returning silently empty results. Running `setup` before step 2 is
safe: it creates the new indexes, keeps the legacy ones and reports what is missing; a
second `setup` after the sweep completes the cleanup.

## 6. Retrieval eval

### 6.1 Labels

`<vault>/benchmarking/retrieval_labels.yaml` (private, vault only):

```yaml
labels:
  - id: r001
    question: "..."
    domains: [research_papers]
    kind: needle            # or survey
    relevant: [<chunk id>, ...]        # needle
    relevant_docs: [<doc id>, ...]     # survey
    source: synthetic       # or hand
```

Labels name chunk ids (stable across this change), never passage ids, so runs before
and after are comparable.

### 6.2 Commands (new group `artmind retrieval`, not proxied)

- `retrieval labels generate --domain D --count N [--seed S]`: samples chunks (seeded),
  asks the configured LLM for one question per sample answerable from that text, with
  an instruction to paraphrase rather than reuse distinctive wording; appends entries
  with `source: synthetic`. Sends the sampled text to the provider, logged in
  `llm_calls.log`. Run with `env -u ANTHROPIC_BASE_URL` from a Claude Code shell
  (CLAUDE.md §5).
- `retrieval labels validate [--labels F]`: shape, ids exist in the graph, domains known.
- `retrieval eval [--labels F] [--topK 5,20] [--compare RUN]`: runs
  `vector_text_search` in-process per question; reports, per `kind`, recall@K
  (needle: any relevant chunk in the top K), MRR, documents covered@K (survey:
  fraction of `relevant_docs` with a chunk in the top K) and latency. Writes the run to
  `<vault>/.artmind/logs/retrieval-eval/<timestamp>.json`; `--compare` prints the
  per-metric difference.

### 6.3 Privacy

Labels and runs hold real questions and corpus ids: vault only, never in this checkout,
tests or commits. Tests use synthetic text.

### 6.4 Reranker decision rule

Revisit a reranker if, after this change, needle recall@5 < 0.8 or needle MRR < 0.6.
The thresholds are the operator's to change; the eval only reports.

Revisited 2026-11-01 (§9): paraphrased needles sit below both thresholds, and neither
reranker tried closes the gap at top 5. No reranker.

## 7. Testing

Hermetic (`test/`):

- `passages.split`: paragraph/sentence boundaries, overlap, table header reattachment,
  offsets slice the parent exactly, short chunk → one passage, determinism.
- `embed_prefix`: nomic and unknown model; `_embed_key` changes with model,
  `PREFIX_VERSION`, `SPLIT_VERSION`.
- `passage_fuse`: RRF per passage, MaxP grouping, `passages_matched`, chat units.
- Graph writes, asserting the parameters sent and which query ran
  (`test/test_update.py`'s `run_side_effect` recorder pattern), never counts alone:
  commit writes passages with the right `chunk_id`/`_domain`; supersede and
  retire/restore swap passages (vectors kept); the sweep selects stale `_embed_key`
  rows, rebuilds passages on an old `_split_key` and skips blank chunks; entity and
  chat vectors carry the document prefix and `_embed_key`.
- Query Cypher: `SEARCH ... WHERE p._domain IN $domains` with the expanded list,
  `pool` sent, keyword-only fallback with its warning, the missing-index error.
- `entity-context`: MaxP and passage keyword leg feed `_rank_chunk_refs` unchanged.
- Migration: refuses while chunks lack passages; otherwise drops both indexes, removes
  the property, reports `none`; no-op when the legacy indexes are absent.
- Output shape: `test_vector_query`; trace spans still read evidence (`chunk.id`);
  plugin fixtures regenerated (`ARTMIND_REGEN_PLUGIN_FIXTURES=1`), then
  `just obsidian-plugin-test`.
- Retrieval eval: metrics on a synthetic labels file with a mocked search.

Live (required; mocks accept any Cypher): the §5 rollout on the contracts eval vault
against Neo4j ≥ 2026.02 with `ARTMIND_NO_PROXY=1`, proving the `SEARCH ... WHERE`
syntax, index options, the migration and the eval end to end.

## 8. Docs and skills, same change

- `artmind/skills/artmind-query/SKILL.md`: new output fields; judge "sparse or weak" by
  `relevance` and `matched_by`; `--context`.
- `CLAUDE.md`: repo layout (`passages.py`, `migrations/`, `artmind retrieval`), command
  routing quick reference, `ingest embed-passages`.
- `docs/vault.md` "Embeddings": passage vectors, the sweep, `_embed_key`.
- `cli.py`: group docstrings; `COMMAND_GROUPS` (the CLI guide test fails otherwise).
- `justfile`: `retrieval-eval` recipe; `embed-chunks` recipe renamed.

## 9. Evaluation and decisions (2026-11-01)

Two vaults. A synthetic banking vault: short chunks (median 337 chars, 98% a single
passage), 50 needles and 10 surveys. A private contracts vault: long chunks (median
6634 chars, ~1400 tokens; 73 of 1401 over the embedding model's 2048-token context),
63 needles (54 in multi-passage chunks), the same 63 reworded to avoid the source's
words (rare-word overlap with the target chunk 0.17, against 0.55), and 10 surveys.
Contracts-vault numbers are offline: the same `retrieval_rank` code over locally
computed vectors and a Python BM25 standing in for the fulltext index. Labels and runs
stay in each vault (§6.3).

Paraphrased needles, contracts vault (recall@5 / recall@20 / MRR):

| Retrieval | All 63 | Long-chunk 54 |
|---|---|---|
| Whole chunk, vector only | 0.41 / 0.57 / 0.28 | 0.35 / 0.52 / 0.20 |
| Whole chunk, fused 2:1 | 0.40 / 0.62 / 0.32 | 0.37 / 0.57 / 0.29 |
| Passage, vector only | 0.43 / 0.65 / 0.30 | 0.41 / 0.63 / 0.28 |
| Passage, fused 2:1 | 0.43 / 0.68 / 0.34 | 0.39 / 0.65 / 0.31 |
| Passage, prefixes, fused 2:1 | 0.51 / 0.60 / 0.38 | 0.50 / 0.59 / 0.37 |
| Keyword only | 0.21 / 0.43 / 0.15 | 0.20 / 0.41 / 0.15 |

Findings:

- **Passages** are the one change that wins on both question sets of the contracts
  vault (long-chunk vector-only recall@20 0.52 -> 0.63 paraphrased, 0.70 -> 0.87
  original) and they remove the silent truncation of over-long chunks. On the banking
  vault a passage is the chunk, so they can only show no harm, which they do.
- **Fusion** already existed (whole chunks, equal votes). Weighting the vector leg 2:1
  fixed the banking regression (keyword matches on generic question words) and never
  cost more than 0.02 on paraphrased questions (it added 0.03-0.04 recall@20 and MRR
  without prefixes). Keyword search alone is strong only when the question reuses the
  document's words.
- **Task prefixes** trade top ranks for depth: with fusion, recall@5 0.43 -> 0.51 but
  recall@20 0.68 -> 0.60 (paraphrased); on the banking vault they cost ~0.07 MRR.
  Within noise at this sample size; kept, since changing them re-embeds everything.
- **Diversity (MMR)**, at 0.15 or 0.3, moved no needle metric by more than ~0.03 and
  passage survey docs@20 by -0.02 to +0.03 on either vault. Its first live runs were
  broken by a serializer bug (no vectors, so only the same-document floor applied):
  banking survey docs@20 reached 0.77 that way, but needle recall@5 fell to 0.62.
  Removed (§9.1).
- **Reranker.** Re-ranking the top 100 fused passages with `ms-marco-MiniLM-L-6-v2`
  (0.15 s/query) or `bge-reranker-v2-m3` (2.5-5 s/query, Apple GPU) lifted
  paraphrased recall@5 by at most 0.03 and MRR by 0.02-0.08; neither beat the prefixed
  fused ranking at top 5 (0.51). The answer is in the top 100 for 83% of paraphrased
  needles, but the missed ones rank low for every scorer. Not worth a torch dependency
  in the query install.
- **Top 10 instead of 5** adds 0.08-0.13 recall on paraphrased needles at no code cost.

### 9.1 Changes that follow

- Remove the diversity step: `mmr_select`, `SAME_DOC_SIMILARITY`, the passage `vector`
  the vector Cypher returns only for it, `DEFAULT_DIVERSITY`, `--diversity` on
  `query vector-text` and `retrieval eval`, and `diversity` in the payload's
  `parameters`. Defect 5 (top K from one document) is left open: no measured harm.
- `artmind-query` calls `vector-text` with `--topK 10`. The CLI default stays 5.
- No reranker; prefixes, passages and 2:1 fusion stay as built.
