# Document context for chunk extraction — design

Date: 2026-10-25. Status: approved for implementation (design session, 2026-10-25).
Follows study item #3 of the contracts extraction study (commit cf787bd, which
added the first-800-characters header this replaces).

## 1. Problem

Chunks extract in isolation. An entity or property whose meaning depends on the
whole document is named or filled differently in each chunk that mentions it:

- a 40-page amendment's clause "this Notice supersedes…" becomes a new AMENDMENT
  per chunk, each named after whatever label that chunk used;
- a journal entry's "today" resolves against the note's date only if the chunk
  knows the note's date;
- a fiction chapter's "the detective" means nothing without the book's cast.

The first-800-characters header of cf787bd helps names, but it only shows the
cover page and the chunk's section. It is not a document-level signal, and it
is not contract-specific in intent: the problem is in every schema.

## 2. Goal and non-goals

Goal: every chunk's extraction prompts see a short, schema-derived picture of
the whole document (what it is, how it is organised, what identifies the things
in it), so that document-dependent names and properties come out consistent.
It must help all schemas without any schema authoring.

Non-goals:
- Feeding the document context to `canonicalize_document` (a follow-up; see §8).
- Map-reduce per-chunk summaries (rejected, §7).
- Changing extraction output shapes, the staging layout or the graph model.

## 3. What a chunk prompt receives

`build_document_context` renders one block, placed before the passage in each
entities, properties and relationships prompt (`with_document_context`). Three
parts, in this order:

1. **Metadata** (no LLM). Title, source file name, source type, the document's
   resolved date (`_valid_from` / `_valid_time_source`, when present), author,
   project and area, tags, and the folder path. Taken from the document-level
   fields `extract_kg` already resolves for the staged `document.json`. They are
   resolved before the chunk fan-out, not during the merge.
2. **Outline** (no LLM). The heading tree from `chunks_meta.json`, with the
   current chunk's position marked: `You are in: Schedule 2 > Rates (chunk 14 of
   57)`. Chunks without headings get only the position.
3. **Summary** (one LLM call per document, §4). A few sentences on what the
   document is, plus an identifiers line listing names, numbers and dates that
   recur and identify the things in it.

The block closes with the existing instruction, reworded: "This is context for
naming and identifying things consistently. Do not extract from this block; the
passage that follows is the text to extract from." Chunk 1 is not given its own
opening twice: the summary covers the opening, and the passage carries it.

The rendered block is capped at about 2,000 characters, so the three parts fit
in every prompt without crowding the passage. Metadata and outline are trimmed
first if the cap is reached; the summary is never cut mid-sentence.

## 4. How the summary is made

- **When:** once per document, in `extract_kg`, after the schema and the
  anchor date are loaded and before the chunk fan-out.
- **Input:** the document's outline, its opening and its closing (the summary
  is the one place the opening is read whole-document-wide; the chunk passage
  still carries it in its own right), and the first paragraph under each
  top-level heading (or, with no headings, the first
  paragraph of each of ten evenly spaced chunks). Capped at about 12,000
  characters. This makes it one call whatever the document's length.
- **Prompt:** built from the schema, never hand-written per domain. It says:
  "Say what this document is in two or three sentences. List, as identifiers,
  the names, numbers, dates and parties that recur and that identify instances
  of these classes: {class names and their one-line descriptions}." The schema's
  `subject` and each class's `description` supply the wording. The schema may
  add `document_context: {focus: "..."}`: free text appended to the prompt.
- **Output:** JSON `{"summary": str, "identifiers": [str]}`. Capped at 1,200
  characters for the summary and 25 identifiers. Parsed with the existing
  `_llm_extract` path; a malformed answer is retried once, like any step.
- **Cache:** written into `document.json` as `context` with a `key` field, the
  hash of (content hash, hash of this domain's own schema file, model name,
  prompt version). The per-domain file, not `schema_set_hash`: an edit to one
  schema must not regenerate every other domain's summaries. A resumed run, or a `--reextract` of unchanged text under an
  unchanged schema, reuses it and makes no call. Any change to the key
  regenerates it.
- **Failure:** a summary call that fails (after its retry) leaves the context
  with metadata and outline only, logs a warning naming the document, and
  does not fail the file. The extraction is not refused for want of context.

## 5. Schema controls

- Default: on, derived as above. No schema changes are needed.
- `document_context: false`: off. Metadata and outline are also off; the block
  is empty.
- `document_context: {focus: "...", summary: false}`: `focus` appends free text
  to the summary prompt; `summary: false` keeps metadata and outline, with no
  LLM call.
- The existing `document_header` key (cf787bd) is accepted as an alias:
  `false` maps to `document_context: false`, and `{chars: N}` sets the cap in
  §3. It is documented as deprecated.

## 6. Components and boundaries

- `artmind/document_context.py` (new): the pure part. `build_outline(meta,
  chunk_seq)`, `build_metadata(document)`, `summary_prompt(schema, sample)`,
  `sample_for_summary(outline, chunks)`, `render_context(metadata, outline,
  summary, cap)`, `context_key(...)`. No I/O, no LLM. Unit-tested alone.
- `artmind/ingest.py`: `extract_kg` resolves the metadata, asks for the summary
  through a narrow `_summarise_document` (which owns the cache read/write and
  the failure rule), and passes `with_document_context(block, passage)` into
  the prompt builders. It replaces `build_document_header`,
  `with_document_header`, `_document_header_settings` and `_document_header`
  (cf787bd). `strip_document_header` becomes `strip_document_context`.
- `artmind/prompt_builder.py`: unchanged. The context is passed in front of the
  passage as today, so no template changes.
- Existing consumers of the header (`test_vault_chunk_cache_merge`'s seam and
  the truncation split test) move to `strip_document_context`.

## 7. Alternatives considered

- **A. Metadata plus opening lines (cf787bd).** Cheap, but a cover page is not
  a summary, and it says nothing about the body. Rejected: it is the problem.
- **B. Map-reduce per-chunk notes, then one merge.** Sees every chunk, at one
  call per chunk before extraction. For the 57-chunk SoW that roughly doubles
  the LLM spend. Rejected for cost; the sampled input covers most of the value.
- **C. Whole document under a budget, else sampled.** Best fidelity for short
  notes. Adds a threshold setting and two code paths. Deferred: the sampled
  path must work for every document anyway, so it is built first, and the
  whole-document branch can be added later as a single setting.
- **Chosen: sampled summary plus metadata and outline, schema-derived prompt.**
  One call per document, bounded input, nothing to author per schema.

## 8. Follow-ups (not in this change)

- Give `canonicalize_document` the same summary, so it can fold "Change Notice
  CCN-001" into "CCN001". It sees only names today.
- Option C above, as a threshold setting, if sampled summaries miss facts in
  short documents.

## 9. Error handling and privacy

- Summary failures never fail a file (§4).
- The summary sends sampled document text to the configured provider, in one
  call per document. This is the same exposure as chunk extraction; the docs
  say so. Notes with `document_context: false` send no extra text.
- Logging: the summary and identifiers are logged at debug level only, with
  the same redaction as chunk prompts.

## 10. Testing

Hermetic: no LLM, no Neo4j. The LLM is replaced by the same seam the
extraction tests use (`_llm_extract`), and assertions are on the prompts
actually sent and the files actually written.

- `document_context.py`: outline with and without headings; chunk position
  marking; metadata rendering with missing fields; sampling caps; cap trimming
  order (metadata and outline before summary); key stability and change.
- Summary cache: one call on first extraction; no call on resume; no call on
  `--reextract` of unchanged text and schema; a call after a schema change;
  a call after a content change.
- Failure: a failing summary call leaves chunks extracting with metadata and
  outline, logs a warning, and the file still stages.
- Schema controls: `false` removes the block and the call; `focus` text is
  present in the summary prompt; `summary: false` makes no call; the legacy
  `document_header` keys still work.
- Behaviour across schemas: the context reaches the prompts for at least
  three shipped schemas of different shape (contracts, a journal-style
  general, a banking schema), asserted on prompt text.
- Regression: the cf787bd tests keep passing once their helpers are renamed.

## 11. Open questions for review

- The summary prompt's identifiers line names instances of the schema's
  classes. For a class with thousands of instances (a rate table), the cap of
  25 identifiers means the list is a sample. This is acceptable for naming
  consistency, but it is worth a look once run on the SoW.
- The 12,000-character sample is a guess. It should be a setting
  (`ARTMIND_KG_SUMMARY_SAMPLE_CHARS`) so it can be tuned without a code change.
