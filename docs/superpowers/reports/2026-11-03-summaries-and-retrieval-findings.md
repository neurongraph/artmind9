# Summaries and retrieval: findings and design ideas

Session report, 2026-11-03. Branch `design/document-context-summary`. Input to the wider
skills and retrieval analysis. This is a findings report, not a spec: nothing here is built,
and no code was changed in the session.

## 1. Question that started it

Is `document_context.json` redundant with the summary tree (`summaries.json`, the
`:DocSummary` nodes)? Follow-ups: do chunk-level `DocSummary` embeddings add noise to
vector search, why should vector search not use summaries, and can the retrieval design be
unified so that the system picks the right depth of search for a question?

## 2. How the system works today (verified in code)

### Two summarisation passes over the same document

| | Context summary | Summary tree |
|---|---|---|
| Code | `document_context.py`, `ingest._summarise_document` (`ingest.py:2504`) | `summary_tree.py`, `summary_build.py`, called at `ingest.py:4350` |
| Input | Bounded sample of the document, up to 12,000 chars (opening, closing, first paragraph of each section) | Every chunk, then a reduce over the chunk summaries |
| Calls | One per multi-chunk document | One per chunk, plus one reduce per document |
| Output | `summary` (1,200 chars max) and up to 15 `identifiers` | One summary per chunk (1,200 chars max), one per document (2,000 chars max) |
| When | Before the chunk fan-out; every chunk's extraction prompt carries it | After extraction |
| Consumer | Extraction prompts only (`render_context`, capped at 2,000 chars) | Retrieval: `survey` legs D and C, passage sweep, topic pages |
| Staged in | `document_context.json`, written in the same swap as the extraction | `summaries.json`, in the vault-sync fingerprint |
| Cache key | content sha, schema bytes, model, sample size, `PROMPT_VERSION` | chunk text sha, `SUMMARY_PROMPT_VERSION`; `DOC_PROMPT_VERSION` for the reduce |

Dependents found: nothing outside `ingest.py` reads `document_context.json`. Tests that pin it:
`test_document_context_cache.py`, `test_document_context_wiring.py`,
`test_extraction_loss_guard.py`, `test_chunks_only_extract.py`, `test_document_dates.py`.
`summaries.json` is also read at commit (`_load_staged`) and enters the sync fingerprint.

The context summary is the only source of **identifiers** (names, references, dates as the
document writes them), which exist to keep entity names consistent across chunks.
`document_context.focus` in a schema steers it (a contracts schema can ask for the contract
reference, effective date and legal names).

### Which index each retriever reads

| Retriever | Reads |
|---|---|
| `vector-text` | `:DocPassage` only: `passage_embedding` and `passage_text_ft`, weighted RRF (vector 2 : keyword 1), MaxP over a chunk's passages |
| `survey` leg P | The same passage ranking as `vector-text` |
| `survey` leg D | `:DocSummary {level: document}`: `summary_embedding` and `summary_text_ft`, 2 : 1 |
| `survey` leg C | `:DocSummary {level: chunk}`: the same indexes, best hit per document |

So the chunk-summary embeddings do not touch `vector-text`; they sit behind their own index and
only reach `survey` through leg C. `survey` is a superset of `vector-text` (it runs P), not a
parallel pipeline. The earlier whole-chunk vectors were removed in favour of passages
(`legacy chunk vectors: none`), so "embed DocChunk instead" is not on the table.

### How a retrieval depth is chosen today

`query plan` picks `survey` from trigger words (`across`, `compare`, `overview of`, ...;
`routes.yaml:202`), otherwise `vector-text`. The `artmind-query` skill has one escalation rule
(fallback ladder rung 5): if `vector-text` rows come from one or two documents and the question
spans documents, re-run as `survey`. That rule is prose the agent must remember.

## 3. Measurements

Data caveat: no single vault has both artefacts. `n*_audit_2026` has 384 `DocSummary`
nodes (chunks-only domain `contracts_vector`, so no `document_context.json`).
`s*_audit_2026` has 27 `document_context.json` and no `summaries.json`. Twenty-two
documents appear in both, matched by name; model and document version may differ, so the
comparison is indicative. All measurements ran through the `artmind` CLI (`text2cypher` for
the graph side, `retrieval eval` for retrieval); no direct database access.

### 3.1 Context summary vs document-level DocSummary (22 paired documents)

| | Context summary | DocSummary |
|---|---|---|
| Mean length | 384 chars | 1,560 chars |
| Numbers per summary | 3 | 20 |
| Identifiers found verbatim in the text | 35% | 50% |

- Context summaries are generic (they typically open "This is a <document type> between <party>
  and <party>"), with no title, reference or date.
- The DocSummary names the document (its title, reference and date) and carries further
  reference codes. It had reference IDs the context summary lacked in 13 of 22 documents; the
  reverse held in 4, mostly spelling variants of the same reference (zero-padding).
- Identifier recall is lexical presence only, so 50% is not 50% lost.

Corpus shape in `n*_audit_2026`: 1,550 chunk summaries (mean 878 chars), 384 document
summaries (mean 1,218 chars), 188 of 384 documents multi-chunk.

### 3.2 `survey` leg ablation (10 survey labels, `docs_covered`)

| Legs | @5 | @20 |
|---|---|---|
| D, P | 0.444 | 0.697 |
| D only | 0.430 | 0.698 |
| D, C, P (default) | 0.410 | 0.690 |
| D, C | 0.409 | 0.723 |
| C, P | 0.328 | 0.681 |
| C only | 0.336 | 0.677 |
| P only | 0.275 | 0.674 |

Reading: D is the strongest single leg. C is weak alone and adds recall at @20 only when paired
with D; adding C to D, P lowers @5 by 0.03. Ten questions cannot separate those differences.

`vector-text` on 126 needle labels: recall@5 0.635, recall@20 0.770, MRR 0.521.
The passage design's own measurements (spec 2026-10-31) show passages lifting long-chunk
vector-only recall@20 from 0.52 to 0.63 on paraphrased questions, 0.70 to 0.87 on original ones.

### 3.3 Cost

The context call is roughly 10% of the summary-pass call count (about 188 calls against 1,550
chunk summaries plus 384 reduces, for `n*_audit_2026`). In graph domains, per-chunk
entity, property and relationship extraction dominates the bill, so the duplicate call is a
small saving. The case for merging the passes rests on quality and simplicity, not cost.

## 4. Findings

1. **The two summaries are not redundant.** They serve different jobs (extraction input vs
   retrieval output) and hold different content (identifiers vs none). They overlap in cost, not
   in function.
2. **The document summary matters for retrieval.** Leg D is the strongest leg and is built from
   chunk summaries. Building it instead from the 12k-char sample would risk that leg; untested.
3. **The context summary is the weaker document description** (shorter, generic, fewer
   references) even though it feeds every extraction prompt. Quality is going into the output
   that matters less to the user.
4. **Chunk summaries do not add noise to `vector-text`.** They live in a separate index read
   only by `survey`. Whether leg C earns its place is not established by the data.
5. **The retrieval paths are not two parallel pipelines.** They share the passage store and one
   embedding sweep. The duplicated machinery is the `DocSummary` layer: its LLM pass, index
   pair, staging file and fingerprint entry.
6. **Depth selection is the weak point.** Routing by trigger words misses cross-document
   questions that do not use them, and escalation is an instruction the agent has to follow.

## 5. Design ideas

### A. Tree first, then extraction (merge the two passes)

Chunk summaries depend only on chunk text, not on extraction, so running them first is a choice,
not a data dependency.

1. Run chunk summaries and the reduce before the chunk fan-out.
2. Extend the reduce to return `{summary, identifiers}`, folding in the schema's
   `document_context.focus`.
3. Build `render_context` from it, still capped at 2,000 chars (the DocSummary averages about
   1,560, so the cap would trim it).
4. Store identifiers in `summaries.json`; remove `document_context.json`, `sample_for_summary`,
   `context_key` and `PROMPT_VERSION`.

Costs: extraction waits on all chunk summaries (latency); the tree's document key needs the
schema bytes, so `DOC_PROMPT_VERSION` bumps and every document summary regenerates once; the
tests above need rewriting. Open risk: whether identifiers from the reduce keep entity names as
consistent as identifiers from the 12k-char sample. Test with `ingest sync --reextract` on a
graph domain, comparing entity counts and merges.

Rejected: building the document summary from the sample (quality of D at risk); keeping the
tree after extraction but adding identifiers to it (still two passes).

### B. Chunk summaries as a candidate-chunk leg in `vector-text` (cheap experiment)

A chunk-summary hit maps to its `chunk_id` and casts a low-weight vote beside the passage vector
(2) and keyword (1) legs in `retrieval_rank`; MaxP still picks the passage, so output stays
quotable evidence. A summary can never be the returned evidence itself: the agent cites
passages, and a summary drops exactly the clause, figure or date a needle question asks for.
Where it could help is paraphrased and topic-style questions. The 126 needle labels name chunk
ids, so `retrieval eval` scores it with no new labels. The result also settles leg C: if it
helps neither path, it is the layer to reconsider, and D would then need a different input.

### C. One retrieval command that reads its own evidence shape

Keep all three indexes (passage, chunk summary, document summary) as three zoom levels. One
command queries all of them; every hit carries its document and chunk ids, so results nest:
document, chunk, passage. `vector-text` and `survey` become two views of one result set.

Decide the depth from retrieval metrics, not question wording:

- evidence concentration: the share of top passages from one document, or entropy across
  documents;
- level agreement: strong passage hits inside the top D documents suggest a needle; many
  documents with similar D and C scores and no standout passage suggest a cross-document question;
- score shape: a sharp drop after the first hit versus a flat tail;
- coverage: how many documents clear a relevance floor.

Output a `mode` (`needle`, `survey`, `broad`) with the evidence and `next` steps, as
`query plan` already does. Question wording stays as one input among several.

### D. Iterative broad mode

For broad questions, go through 100+ documents deliberately: rank documents by D, read their
summaries, pick the relevant ones, drill into their passages, repeat on gaps. Summaries first,
text second. `topic build` already does map per document then reduce, so this is partly a matter
of exposing that as a retrieval mode. It needs a budget (documents read, rounds), because of
latency and LLM cost.

### E. User-facing escalation

Answer from the needle result and say when the evidence shape suggests more exists ("this draws
on 2 documents; 14 others mention the topic. Run a full survey?"). A deeper pass is a click, not
a re-asked question. This replaces ladder rung 5 with something the system, not the agent's
memory, produces.

## 6. Open questions

- **Calibration data.** There are 10 survey labels. The signals in C need many more, including
  mixed questions ("what did each contract say about the exit fee") that are needle-per-document
  across documents. Survey and needle labels for the same corpus would let the classifier be
  scored directly. Generating them was offered and deferred.
- **Do the signals separate the classes at all?** Untested; this is the main risk to idea C.
- **Identifier consistency** under idea A (above).
- **Leg C's value**, per mode, once there are enough labels.
- **Reproducing the 22-document comparison on one vault** with both artefacts, to remove the
  cross-vault caveat.
- **Chunks-only domains** have no context summary by design; idea A must keep that exemption.

## 7. Suggested order of work

1. B: the `vector-text` chunk-summary vote, behind an env var, scored on the 126 needle labels.
   Cheapest, and informs leg C.
2. Generate more survey labels (including mixed questions); re-run the leg ablation.
3. Prototype the evidence-shape metrics from C offline against the labelled runs, without
   changing any command, to see whether they separate needle from survey.
4. Only then A (merge the passes) and D/E (iteration and escalation), which have the largest
   blast radius.

## 8. Provenance and caveats

- Code read: `document_context.py`, `summary_tree.py`, `summary_build.py`, `survey.py`,
  `retrieval_rank.py`, `vector_query.py`, `setup.py` (index definitions), `ingest.py` call sites,
  `sync_state.py`, `query_plan/routes.yaml`, `skills/artmind-query/SKILL.md`, and the passage
  retrieval spec.
- Vault runs used `ARTMIND_NO_PROXY=1` and `env -u ANTHROPIC_BASE_URL`. The comparison used
  `n*_audit_2026` (graph side via `text2cypher`, retrieval eval) and `s*_audit_2026`
  (staged `document_context.json` files). Intermediate files are in `/tmp` only. The vaults hold
  private corpus data: this report quotes no document text, names or references, only counts
  and generic phrasing.
- Side effects: `retrieval eval` wrote run files under
  `n*_audit_2026/.artmind/logs/retrieval-eval/`. Nothing else in the vaults changed; no code
  changed in the repo.
