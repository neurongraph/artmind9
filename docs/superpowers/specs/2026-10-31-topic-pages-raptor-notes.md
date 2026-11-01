# Spec B notes: document summaries (RAPTOR-style) and topic pages

Status: **pre-design notes**, captured 2026-10-31 while Spec A (vector-text retrieval
fixes) is being designed. Spec B is designed after Spec A is built and measured; it
builds on Spec A's passages, `--context passage` and the `relevance` score. Not a spec
yet: no decision below is final until the brainstorming pass for Spec B.

## Problem

Two question classes need different retrieval:

| Class | Example | Needs |
|---|---|---|
| Needle | "what dataset did paper X use" | precision: hybrid search, top 5 (Spec A) |
| Survey | "what do the papers say about X", "compare approaches to X" | coverage across documents: top-5 chunks cluster in one or two documents and cannot answer it |

The survey class is today left to the agent looping `vector-text` by hand, with no
document-level view of the corpus. Chunks-only domains (`graph_extraction: false`,
spec `2026-10-30-chunks-only-domains-design.md`) have no graph to fall back on, so they
feel this most.

## Direction agreed so far

1. **Per-document summaries for chunks-only domains (opt-in).** Reuse the existing
   document-summary machinery rather than a new one:
   - `artmind/document_context.py`: `sample_for_summary`, `summary_prompt`,
     `parse_summary`, `context_key`, `PROMPT_VERSION`.
   - `artmind/ingest.py` `_summarise_document` and the `document_context.json` staging
     sidecar (cached by content sha, schema bytes, model, sample size).
   - Today `extract_kg` skips the summary for chunks-only domains
     (`chunks_only = not graph_extraction_enabled(schema)`). Spec B makes it opt-in
     there (a schema key; name TBD), one LLM call per multi-chunk document. The
     summary prompt is schema-driven; check it reads well for a schema with no
     `entity_types` (only `description`/`subject`).
2. **RAPTOR core idea, two levels first.** RAPTOR (Sarthi et al., 2024) retrieves over
   a tree of recursive summaries. Here: level 1 = passages (Spec A), level 2 = one
   embedded summary per document. A survey query finds documents by summary, then
   passages inside those documents. A third level (clustered topic summaries) is
   deferred until the two-level version is measured.
   - Storage to decide: a `:DocSummary` node with its own vector index vs. `summary` +
     `embedding` on `:Document`. Lifecycle must follow the document's versioning
     (`Document` → `DocumentHistory`), like passages follow chunks in Spec A.
3. **Survey retrieval in code, not left to the agent** (same philosophy as
   `query plan`): a large pool (~100), grouped by document, returning a per-document
   list (hit count, best 1-2 passages, document summary). Either a `query survey`
   command or `vector-text --groupBy document`.
4. **Topic page CLI command.** Builds a cited page for a topic over one or more domains:
   - Retrieval: the survey step above, then a per-document read of the relevant
     documents (`query graph pattern10` / `query chunks --expand`), then map-reduce
     synthesis (per-document notes → one page) with citations to chunk/passage ids.
   - Saved by default in a **non-ingested** vault area, alongside reviews and insights:
     `<vault>/.artmind/topics/<slug>.md` (a `paths.TOPICS_DIR`, like
     `paths.REVIEWS_DIR` / `INSIGHTS_DIR`). Atomic writes via `logs/`.
   - Frontmatter records the source documents and their content sha; a page is stale
     when any cited document changes. Sub-commands to consider: `build`, `list`,
     `show`, `refresh` (rebuild stale pages).
   - Built on demand and cached; **not** maintained at ingest time. Keeping topic pages
     current at ingest needs a topic taxonomy and LLM calls per document, which is
     graph extraction again; a corpus that needs that should not be chunks-only.
5. **Privacy.** Topic pages and summaries hold private corpus content: vault only,
   never copied into code, tests or commits of this public checkout; tests use
   synthetic text. Summaries and synthesis send document text to the configured LLM
   provider (as extraction already does) and are logged in `llm_calls.log`.

## Open questions for the Spec B brainstorm

- Command name and group (`artmind topic ...` vs. under `query`); proxied by `serve` or not.
- Whether topic pages are committed with the vault's private repo (as reviews and
  insights are) or gitignored.
- Whether a topic page should also be readable by `artmind-query` as a source (a cached
  answer), and how its staleness is shown to the agent.
- Summary opt-in key name and whether graph domains also get the embedded summary level.
- How the survey step and the topic-page build are measured: Spec A's eval set holds
  10-20 hand-written multi-document questions; add documents-covered / citation recall.

## Deferred items from the same review (not Spec B)

- **Cross-encoder reranker:** deferred until Spec A's fixes are measured. If added, an
  optional stage (`ARTMIND_RERANK_PROVIDER=none|local|<api>`, default none; local model
  in the `[ingest]` extra; a hosted API sends text off the machine, opt-in only).
- **Extraction chunk size experiment (a small Spec C):** once DocChunk no longer carries
  a vector (Spec A), its size is an extraction-only choice. Measure extraction recall vs.
  LLM calls at 6000 / 9000 / 12000 chars on the contracts eval vault before changing the
  6000 default. Evidence to weigh: Microsoft GraphRAG (Edge et al., 2024) reported
  600-token chunks extracting about twice the entity references of 2400-token chunks
  (seen via secondary sources; check the paper before citing figures).

## References

- RAPTOR: Sarthi et al., "RAPTOR: Recursive Abstractive Processing for Tree-Organized
  Retrieval", 2024.
- GraphRAG: Edge et al., "From Local to Global: A Graph RAG Approach to Query-Focused
  Summarization", 2024.
- MaxP passage aggregation: Dai & Callan, "Deeper Text Understanding for IR with
  Contextual Neural Language Modeling", SIGIR 2019.
- The "LLM wiki" pattern: an LLM-maintained, cited, interlinked set of topic pages
  compiled from sources.
