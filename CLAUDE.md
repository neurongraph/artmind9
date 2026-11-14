# CLAUDE.md

Guidance for AI agents working in this repo. `AGENT.md` and `AGENTS.md` are symlinks
to this file — edit only `CLAUDE.md`.

## What artmind is

A knowledge-graph system: it ingests documents, extracts entities/relationships with
an LLM, stores them in Neo4j, and answers natural-language questions over the result.
The surface is a single Click CLI, `artmind`, plus FastAPI web UIs: an end-user
chat UI (`chat-ui`) and an operator admin console (`admin-ui`, agent console +
ingest dashboard).

## Repo layout

| Path | Holds |
|---|---|
| `artmind/cli.py` | The whole Click CLI. Command groups: `query` (+ nested `query graph`), `ingest`, `domains`, `docs`, `update`, `session`, plus top-level `init`/`setup`/`serve`/`chat-ui`/`admin-ui`. |
| `artmind/cli_conventions.py` | The CLI's conventions in one stdlib+Click module (spec `docs/superpowers/specs/2026-11-02-cli-uniformity-design.md`): `envelope`, `compact_option`, `dry_run_option` / `unbuilt_dry_run_option`, `aliased_option`, `Effect` / `Change` / `with_effect`, `WRITE_COMMANDS` (path → tier), `SERVER_COMMANDS`, `INTERACTIVE_COMMANDS`, `DEPRECATED_ALIASES`, `EXIT_*`. `test/test_cli_conventions.py` walks the Click tree and fails a command that breaks them. `artmind/effects.py` (`topics_touching`) and `artmind/ingest_plan.py` (the `ingest sync --dryRun` plan) feed the `effect` blocks. |
| `artmind/_entry.py` | Console-script entry point. **Proxies `query` calls to the `serve` daemon** — only one whose `/health` `vault` is the caller's vault (or none for both); another vault's serve, or one too old to name its vault, means in-process (with an `artmind:` note on stderr for the former). Stdlib-only by design (`artmind.vault` is too). See "Testing implications". |
| `artmind/graph_query.py`, `vector_query.py`, `text2cypher.py` | Query layer: templated Cypher patterns, RRF vector+fulltext search, LLM-generated Cypher. |
| `artmind/domain_guard.py` | Unknown-`--domain` warning (query-routing study P9). `_parse_domains` flags a name no schema, structured table or graph data sits under (graph asked only if the local sources leave it unexplained; no warning when it can't be asked); `_echo_json` adds it to the payload's `warnings` and `artmind:` goes to stderr. Never fails a command. Tests silence it by default (`conftest._quiet_domain_guard`); `test_domain_guard.py` opts in with `real_domain_guard`. |
| `artmind/ingest.py`, `extraction.py`, `jobs.py`, `worker.py` | Ingestion pipeline and its background worker. |
| `artmind/ingest.py` `--reextract` | `ingest sync|async --reextract [--match GLOB]` forces the `content` tier (`NotePlan.reextract`) for unchanged vault notes so they are re-extracted under the current schema; the version is not bumped. Travels as `ingestion_jobs.reextract` → `worker._process_job` → `ingest_file`; `extract_kg` then clears the file-hash-keyed chunk cache and `kg_chunk_status` rows (`_reset_extraction_cache`), else it would resume as already done. A converted pdf/pptx is re-extracted from its stored markdown, no docling run and no version bump (`_reextract_converted_binary`); if that markdown is no longer the body last extracted, the source is converted again instead. A .docx is always converted again (cheap; image descriptions reused from `<image>_desc.md`), so an older conversion gains what `docx_convert` adds; the version bumps only if the body changed. Tables ignore it. The document summary (`document_context.json`) is reused when text, schema file, model and sample size are unchanged, so a re-extraction makes no summary call unless the schema changed. The cache is set aside under `logs/reextract-stash/`, not deleted; `extract_kg` refuses to stage (returns None, file failed) while a chunk step is still `failed` after the retry, or when a re-extraction of an unchanged note comes back with no observations over a non-empty result — then it restores the cache and rows (spec `docs/superpowers/specs/2026-10-18-extraction-loss-guard-design.md`), so a re-run of a refused `--reextract` redoes every chunk. Failed chunk numbers reach `kg_error`, the job file row (`failed_chunks`) and `job-status` `chunk_progress.failed_chunks`. |
| `artmind/document_context.py` | The document-context block every chunk's extraction prompts carry (spec `docs/superpowers/specs/2026-10-25-document-context-design.md`): `context_settings` (`document_context:`, legacy `document_header:` whose `{chars: N}` caps the whole block), `build_metadata`, `build_outline` (chunk position + windowed heading outline), `sample_for_summary` (bounded by `ARTMIND_KG_SUMMARY_SAMPLE_CHARS`, default 12000), `summary_prompt`/`parse_summary`, `render_context` (cap), `context_key`. No file I/O, no LLM; `ingest.extract_kg` makes the one summary call per multi-chunk document (`_summarise_document`). The sampled text goes to the configured provider. |
| `artmind/docling_render.py` | The body of every docling conversion, rendered from docling's model the same way for pdf, pptx and docx: `tidy_tables` (body cells merged across columns written once, header spans still repeated, a row made only of cells merged from above dropped, empty rows/columns dropped), `compact_tables` export, `normalise_tables` (every table a top-level block), pptx speaker notes (`ContentLayer.NOTES`) under `**Speaker notes:**` after their slide, and per-page bodies (`page_markdowns`, `render_body(replacements=)`) trusted only when the per-page exports rejoin to the whole. `RENDER_VERSION` goes to `conversion.json`. pdf/pptx run `docling --to md --to json` as a subprocess (`_docling_command`); the body is rendered from the JSON, which is then deleted (unusable JSON: docling's markdown, padding stripped). |
| `artmind/page_check.py`, `page_repair.py` | After a pdf/pptx conversion, each page's own text (`pypdfium2`, `python-pptx` with notes) is compared with its rendered page and a page below 0.97 shingle coverage, or with a long cell ending in `:`, is flagged; text docling filed under an earlier page (`spilled_text`) counts as present. `ARTMIND_PAGE_REPAIR=off|detect|repair` (default `detect`). `repair` sends each flagged **pdf** page image to `ARTMIND_IMAGE_MODEL` through `extraction.describe_image` (shared with image description) and keeps the transcription only if it covers the page and every number; cached in `<data dir>/page_repair_cache/`. **Page images leave the machine only in `repair`.** Findings: `conversion.json` `conversion.flagged/repaired/unrepaired` (no document text), one warning per document, `job-status` `page_check`. Spec `docs/superpowers/specs/2026-10-31-conversion-page-check-design.md`. Slow real-docling tests: `just dev-test-docling` (marker `slow_docling`, golden pptx bodies in `test/data/golden/`). |
| `artmind/docx_prepare.py`, `docx_convert.py` | A .docx converts in-process through docling's Python API, not the CLI (`_convert_docx_in_process`). `docx_prepare` writes a copy under `LOGS_DIR` with tracked changes accepted (docling drops insertions in table cells) and a letters-only marker run after each comment's range (docling links few comments to their text: none in numbered clauses), reading author/date/text/highlighted words and replies (`commentsExtended.xml`); a mark inside deleted text is kept. `docx_convert` tidies each table in docling's model (body cells merged across columns written once, header spans still repeated, empty rows/columns dropped), saves with `compact_tables`, makes every table a top-level block with blank lines around it, and replaces each marker with a `> [!comment]` callout after its paragraph, inside its list item, or after its table naming the row; unplaced ones go under `## Document comments`. The chunker never ends a chunk on a bare table header (`_carry_trailing_table_headers`). |
| `artmind/document_entity.py` | The document's own entity, named once in code: a schema's `document_entity: {classes: [...]}` opts classes in; `extract_kg` folds, after `canonicalize_document`, every name of those classes that carries the document's reference (letters+digits from its filename/title, normalised `CN012` = `CN12`) and otherwise only title words, document-type words, ordinals and dates into the variant most chunks used. No LLM; validated by `domains validate`. |
| `artmind/document_identity.py` `document_key` | Derived files and staging folders are named by the document key (`<stem>~<12 hex of the vault path>`), chosen once by `ingest._resolve_binary_key` / `_note_staging_dir`; documents staged before keys keep bare-stem names. Build these paths only with `ingest._markdown_path/_artifacts_dir/_chunks_dir/_staging_dir` (`test_document_key.py` enforces it). Spec `docs/superpowers/specs/2026-10-31-document-key-collisions-design.md`. |
| `<kg>/<domain>/<doc>/document_context.json` | Staging sidecar holding the document summary, its identifiers and its cache key (content sha, domain schema file bytes, model, sample size, `PROMPT_VERSION`). Committed with staging and written in the same atomic swap as the extraction; reused while the key matches, normalised on read, regenerated otherwise. A cache, never read by the graph. Failed/cut-off steps' raw answers go to `logs/extraction-debug/`, never staging. |
| `artmind/retime.py` | `ingest retime`: re-derives already-staged documents' valid time from the current schema (`temporal.document` labels, then `temporal.document_patterns` — filename / first line) with no LLM, also applying `property_aliases` / number-variant folding and `exclusive:` properties (`observations.normalize_property_keys`), commits changed folders, rebuilds once. `--redoProperties` re-runs only the properties step, only for entities of `temporal:`-tagged classes, with the document date in the prompt (`temporal.relative_anchor`), keeping canonical names. |
| `artmind/table2graph.py` | `ingest table2graph`: projects structured-table rows into entities/relationships via a declarative mapping in the run folder's `domains/table_mappings/*.yaml` (no LLM), committed through the same `_commit_document_tx` as a document. Mapping format: `artmind/skills/artmind-create-schema/references/table_mapping.md`. |
| `artmind/staging_audit.py` | `ingest empty-extractions`: read-only audit of staging folders, listing notes whose `observations.json` is empty although their chunk text is not. No registry, Neo4j or LLM. Skips chunks-only domains. |
| `graph_extraction: false` (domain schema) | Makes the domain **chunks-only** (spec `docs/superpowers/specs/2026-10-30-chunks-only-domains-design.md`): `extract_kg` makes no LLM call (vocabularies, document summary, entities/properties/relationships, canonicalisation and the document-entity fold are all skipped; the three chunk steps are recorded `skipped`), stages chunks + embeddings with empty `observations.json`/`relationships.json`, and bypasses the empty-result loss guard, so `ingest sync --reextract` after switching a domain retires its old entities. Read only through `schema_validate.graph_extraction_enabled` / `domain_graph_extraction(domain)`; a dotted child does **not** inherit it; `domains validate` errors on a non-boolean and warns on ignored `entity_types`/`document_entity`/`property_aliases`. `domains-overview` reports `graph_extraction: false` and store `chunks` (read from this machine's schema); `artmind-query` goes straight to `vector-text`. `ingest table2graph` does not read the flag. |
| `artmind/kg_views/` | Saved, parameterised, read-only graph queries ("views"). `model` (view.yaml contract), `render` (markdown per presentation format), `params` (entity auto-resolve), `store` (reads `<ARTMIND_HOME>/domains/views/<name>/{view.yaml,SKILL.md}` from disk on every call; never seeded by `setup`; refuses a changed view without a `version` bump and refuses a version downgrade; save is atomic), `runner` (60 s server-side query timeout). `query domains-overview` rows carry `views` (valid saved views covering the domain, 0 lets the skill skip `views list`) and `insights` (a latest snapshot exists); on a store read failure the key is omitted and `warnings` says so. CLI: `artmind query views list/show/run` (proxied by `serve`) and `artmind views validate/test/save/delete` (`views test` reports zero rows, truncation and ambiguous/no-match entities in a JSON `warnings` list). Format reference: `artmind/skills/artmind-create-view/references/view_format.md`. |
| `artmind/retrieval_eval/` | Retrieval eval for `vector-text` (spec `docs/superpowers/specs/2026-10-31-vector-retrieval-passages-design.md` §6): `labels` (`<vault>/benchmarking/retrieval_labels.yaml`, private; needle labels name chunk ids, survey labels document ids), `generate` (samples chunks, one LLM question each, sends text to the configured provider), `metrics`, `runner` (in-process, runs in `<vault>/.artmind/logs/retrieval-eval/`). CLI: `artmind retrieval labels generate/validate`, `artmind retrieval eval [--compare RUN]` (not proxied). |
| `artmind/insights/` | Unprompted findings over a domain's graph (spec `docs/superpowers/specs/2026-10-17-insights-layer-design.md`). `projections` (named read-only `$domains`-scoped Cypher, memoised), `profile` (shape profile from content-dated documents only, effective kind, anchors), `detectors/` (salience/type trends, dormancy, communities, brokers, outcome_lift (FDR-controlled), transitions, open_loops, same_as_candidates + split_signal, graph health incl. attribution_suspects/fragments; pure Python + networkx, no numpy; `aliases.yaml` kinship names), `config_check`, `prompts` + `narrate` (LLM captions and circle names after the diff, post-checked, cached by content hash; any LLM failure leaves template titles and is retried by the next refresh; prompts send entity names and stats to the configured provider and log them in `llm_calls.log`, `--noNarrate` keeps them out), `diff`, `store` (snapshots at `paths.INSIGHTS_DIR`, `<vault>/.artmind/insights/<domain>/`, committed; atomic writes via `logs/`), `runner`. Tuning: `domains/insights/<domain>.yaml`, never seeded, outside `schemas/` so it never moves `schema_set_hash()`. **Snapshots hold private corpus data** — never copy them into code, tests or commits. CLI: `artmind insights refresh/list/show/profile/runs/same-as/config check` (not proxied; `refresh` short-circuits when graph, schemas, curation and config are unchanged; `same-as --export` writes `ingest refine-graph --fromFile` input for the same-as review queue; `refresh --noNarrate` skips the LLM). |
| `artmind/traces/` | Query traces (spec `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`). Every admin-console turn and benchmark question → `trace_turns`/`trace_events` in the registry DB. Every `artmind query` call made under a trace → a `trace_spans` row (via `TracedGroup` on the `query` group; evidence read from the payload `_echo_json` prints) plus its `trace_llm_calls`. The trace id reaches the agent's Bash through a per-session file named by `ARTMIND_TRACE_CONTEXT`, which `_entry` forwards to `serve`. A background sub-agent keeps its starting turn's id: `webui/subagent_trace.py` binds `agent_id` at `SubagentStart` and prefixes `ARTMIND_TRACE_ID=<id>` (`-` = untraced) onto its `artmind` Bash calls (Claude SDK backend only). `path` derives graph→vector fall-through, answer basis and the views-first check (`views_check`; `traces stats` rates and `views_skipped`/`views_late` triage signals). The read-only `db` subcommands (`bridge` default action, `list`, `schema`, `sql`, `timeline`, `review`) also leave spans (`TracedDbGroup`; `db sql` is the `sql` layer, the rest discovery; `db` runs in-process, never proxied); `mappings`, `grain`, `catalogue` and every other `db` write are untraced. Stored `query plan` spans (the planner was removed in 0.13) count as discovery, and stored `plan_shadow` events still show in `traces show`, the `stats` block and the `plan_disagreed_high` triage signal; no new ones are written. `compare --baseline A,D[,…] [--variant V] [--questions FILE] [--judge] [--grade]`: pairs runs by normalised question text, judges pairs (`judge.py`, whole answers to a 40,000 ceiling), grades each answer against tiered expected answers (`tiers.py`, `grade.py`, cached in `trace_grades`), prints the classic band and the variant gate (`gate.py`); stdout holds qids and counts only, text goes to `logs/compare-*.json`; needs `ARTMIND_COMPARE_JUDGE=1` or `--allowJudge`. `compare` also lists `topic_plan_turns` ({run, qid}; slugs in the detail file only) for benchmark turns that ran `query topics run` for a plan whose topic or a subject matches the question (equal, or containment at 40+ characters; `traces/topic_flags.py`, spec 2026-11-10 §10) and adds them to `flagged`. `misses [--since] [--run] [--source]`: miss records derived from stored turns (`misses.py`, spec 2026-11-08 §4.1; kinds vocabulary, reformulation, identity, scope, structure, answer, user-stated) from spans, the latest `trace_grades` row, feedback and the next turn of the conversation; a cited document is a `### Sources` link or, when the answer has none, a document whose file stem or title the answer mentions; one JSON line per miss to `logs/misses/` (private), counts only on stdout, no LLM call. Spans keep a command's `no_match` / `needs_disambiguation` status and its warnings for it. CLI: `artmind traces list/show/triage/feedback/stats/compare/misses/prune` (not proxied). |
| `artmind/benchmark_labels.py` | Benchmark labels (`question`, `expected_documents` from `<vault>/benchmarking/plan_labels.yaml`) and document matching. |
| `artmind/conversations/` | Chat history for chat-ui and admin-ui (spec `docs/superpowers/specs/2026-10-16-chat-conversations-design.md`). `store` (the `conversations` / `conversation_turns` tables in the registry DB: ensure/get/list/update/delete, begin/finish/list turns, profile-scoped startup sweep), `recorder` (`ConversationRecorder`: folds a turn's neutral UI events into ordered `blocks` and writes the turn; wraps `/api/chat`), `replay` (`build_context`: user questions + final answers within `ARTMIND_REPLAY_BUDGET_CHARS`, default 24000, prepended to the prompt whenever `SessionRegistry.get_or_create` reports a fresh agent; a turn that did not reach `turn_done` drops the live agent so the next turn replays). The URL `/?c=<id>` is the UI state; `static/conversations.js` is the sidebar. Conversations hold private corpus content: local DB only, and tests use synthetic text. A running `chat-ui` / `admin-ui` serves stale code, so restart it after editing. |
| `artmind/reviews/` | Review dossiers (spec `docs/superpowers/specs/2026-10-11-query-review-loop-design.md` §7-§8): one markdown file per diagnosed query failure under `paths.REVIEWS_DIR` (`<vault>/.artmind/reviews/`, committed with the vault's private repo; never ingested). `dossier` (format, atomic save — temp files go to `logs/`, never the committed folder), `validate`, `scaffold` (from a trace), `index` (`trace_reviews` is a rebuildable (review, trace) index), `leakcheck` (scans a checkout diff for a dossier's `private_terms` and quoted text), `shape` (shape-audit bundle: sentences, graph subgraph via AGGREGATES/EXTRACTED_FROM + one RELATES_TO hop, staging, schema), `reextract` (dry-run re-extraction of evidence chunks under a proposed schema/meta — shape diff + answerability, writes nothing), `answerability` (pattern matcher), `aggregate` (findings by schema element), `sample` (random chunks with lost-at-commit / isolated signals), `verify` (regressions file; re-run a regression question as a one-question benchmark and compare traces — never auto-verifies). Driven by the `artmind-review` skill from Claude Code in the checkout with `ARTMIND_VAULT=<vault>`. **Dossiers hold private corpus data; the checkout is public** — never copy dossier content into code, tests or commits. CLI: `artmind review new/list/show/status/validate/reindex/leakcheck/shape/reextract/shape-report/attach/regressions/verify/prompt` (not proxied). |
| `artmind/refine_pipeline.py`, `refine_graph.py`, `conflicts.py`, `consolidate.py`, `temporal.py`, `harmonizer.py` | Graph maintenance: merging, conflict detection, temporal normalization. |
| `artmind/passages.py`, `artmind/passage_store.py` | Passages: ~1200-char retrieval units under each DocChunk; derived at commit, reconciled by `embed_sweep.reconcile_passages` (`ingest embed-passages`); carry every text vector; `_split_key`, `_embed_key`. |
| `artmind/embedding_cache.py` | Embedding cache at `<data>/embedding_cache.sqlite`, read by the passage sweep before it calls the embedder. Never committed. |
| `artmind/retrieval_rank.py` | Weighted RRF (vector 2:1 keyword) + MaxP passage fusion for `vector-text` and `entity-context`. `survey_leg_weights()` reads `ARTMIND_SURVEY_LEG_WEIGHTS` (e.g. `D=1,C=1,P=1`; bad value is a `ClickException` naming the variable). |
| `artmind/summary_tree.py`, `summary_build.py`, `summary_store.py` | The summary tree (spec `docs/superpowers/specs/2026-11-01-summaries-survey-topics-design.md` §1): one summary per DocChunk + one per document reduced from those, every domain, built in `extract_kg` after extraction, staged in `<kg>/<domain>/<doc>/summaries.json` (cached by chunk text sha + `SUMMARY_PROMPT_VERSION` + model; a missing entry is retried by the next run; a one-chunk document's summary is its chunk summary). Committed as `:DocSummary {level: chunk|document}` (`SUMMARIZES`), swapped to `:DocSummaryHistory` with their document on retire/supersede, embedded by the passage sweep (prefix `search_document: `). `DOC_PROMPT_VERSION` versions the document-level reduce prompt separately from `SUMMARY_PROMPT_VERSION`, so a doc-prompt change re-runs only document reduces. `ingest summarise [--domain] [--match] [--dryRun]` backfills already-staged documents. Sends every chunk's text to the provider once. `summaries.json` enters the vault-sync fingerprint only when present. Prompts carry a hard character budget (~85 % of the cap, derived from `CHUNK_SUMMARY_CHARS` / `DOC_SUMMARY_CHARS`) so the LLM stops before the limit; `parse_summary` trims at the last sentence boundary (`. `, `! `, `? `) within the allowed window, or at the last whitespace, rather than cutting mid-sentence. `SUMMARY_PROMPT_VERSION = 2`, `DOC_PROMPT_VERSION = 3` (bumped when the budgets were added). |
| `artmind/survey.py` | `query survey` — ranks documents for cross-document questions: RRF over document summaries (D), chunk summaries (C) and passages (P); per-leg pool `POOL = 300`; survey strips stopwords from its keyword legs (`sanitize_lucene_query(drop_stopwords=True)`); `vector-text` does not. Default depth by shape: 50 for `--shape brief`, 10 for `full`; `ARTMIND_SURVEY_TOP_DOCS` (1-50) overrides both; bad value is a `ClickException`. Weights from `retrieval_rank.survey_leg_weights()`. `warnings` names domains with missing summaries; `coverage` (`matching_documents`: distinct documents of the keyword passage leg, or with `--by` the sum of the periods' keyword hits; `shown`; `at_least` when the pool is full) is a fact, present whenever any document matches. `retrieval eval --retriever survey` measures it. |
| `artmind/brief.py`, `artmind/result_store.py` | Bounded output (spec `docs/superpowers/specs/2026-11-05-bounded-output-design.md`). `--shape brief|full` (default `full`) on `survey`, `vector-text`, `chunks`, `entity-context`: brief rows carry id, title, domain, date, status; a brief survey lists every document as a passage, gist or line row (`brief.allocate`: passage slots to the needle set first, never past `--passageDocs` or the budget), the `gist` an extractive, question-matched sentence (passage first, then summaries; `gist_source` says which); `survey --expand <doc_id>` opens documents. `ARTMIND_OUTPUT_BUDGET` (characters, unset/0 off; the web UI agents get 24000) makes `_echo_json` cut an over-budget payload's main list (`cli_conventions.LIST_KEY`) and store the whole under `logs/results/<handle>.json` (private, pruned after 24 h and by `traces prune`); `artmind query page <handle> --from N` reads the rest. `serve` applies the caller's budget per request. Spans record `payload_chars`/`shown_chars`/`handle`. `retrieval eval --compareShapes` is the visibility gate (`evidence_visible`: brief survey(50) vs full survey(10)). The skill passes `--shape brief`; terminal and plugin output is unchanged. |
| `artmind/topics/` | Topic pages (spec §3; faceted since spec 2026-11-10 §6–§7): `shape` (`--by/--split auto` from orient peel-1 facts: year when ≥ 80 % content-dated over > 24 months, month for 3–24 months and ≥ 60 documents; domain split when matches fall in ≥ 2 children; cost estimate), `build` (`build_topic`: grid → one survey per cell → map per (document, subject), `ARTMIND_TOPIC_MAP_CONCURRENCY` default 8, through `mapcache` (`logs/topic-map-cache/`, key content sha + subject + `MAP_PROMPT_VERSION` + model) → one reduce per subject (and child domain, nested `###`) + one overview → `check` → `render`; page `## Overview`, one section per subject, `## Timeline` when dated, `## Where sources differ`; writes the plan folder beside the page, first SKILL.md without an LLM; `topic refresh` rebuilds from the plan with its built model), `suggest` (level 2: per-domain n = p90 of `_observation_count`, min 5; anchors matching the topic are the dry run's `suggested_subjects`), `gather` (`Material`, `links_for`), `members` (spec §6.4 set questions: `--discover members --anchor E --entityClass C` lists co-mentioned candidates in the dry run; `--member` keeps one subject per member searched by name + graph aliases, no period slice, plus a cell for the anchors; `--drop` is remembered; the plan stores `members`/`discover`/`dropped`; freshness reports `new_candidates`, never adds them), `store` (`<vault>/_Topics/<slug>.md`, frontmatter + `## My notes` kept on rebuild), `stale`. CLI `artmind topic build|refresh|list|show|delete` (not proxied; build/refresh make LLM calls). Pages are for people and never on an agent surface (agents run topic plans). `_Topics/` is in `ingest.NEVER_INGESTED` — never ingested whatever the mappings say. `ARTMIND_TOPIC_MAX_DOCS` (1-300, default 150) overrides `--maxDocs`; bad value is a `ClickException` naming the variable. Pages hold private corpus content and are committed with the vault. |
| `artmind/catalogue/` | The knowledge catalogue (spec `docs/superpowers/specs/2026-11-02-catalogue-and-answer-assets-design.md`), read by `orient`, the UIs and the agent. `state` (`:DomainState {_domain, generation, changed_at}`; **every write path that changes what a catalogue reads calls `state.bump(tx, domains)` inside its own transaction** — `test_catalogue_bump_sites.py` enforces it; `generations` sums children by prefix), `stamp` (disk stamp of views, topic pages, insight snapshots, schemas), `model` (`DomainCatalogue`, `PART_FIELDS`, `CATALOGUE_FORMAT = 5`; an older cache file is discarded), `parts` (one builder per part: classes, relations, shapes, types, documents, versions, names, stores, time, assets, health, levels (n, focused/anchor entities, passages, summaries; orient peel 2's `levels` block with the domain's topic plans); `shapes`/`types`/`versions` and `time`'s `from_content`/`content_*` feed `orient`; `part_key` is what each is revalidated against), `cache` (memory + `logs/plan-catalog/<domain>.json`, fingerprint `(generation, entities, documents, history, disk stamp)`, per-part rebuild, single flight, `MAX_AGE_S` 3600, `ARTMIND_PLAN_CATALOG=0` builds live), `health` (samples capped at 10; `ARTMIND_CATALOGUE_HEALTH_TTL_S`, default 3600), `refresh` (the background health rebuild, **only in serve, chat-ui and admin-ui**), `report` (payloads). **`health` is never built on a query's critical path** — only by `catalogue refresh` or the background job. CLI: `artmind query catalogue` (proxied, read-only) and `artmind catalogue refresh` (`--dryRun`). Cache files, asset records and health samples are private and live only in gitignored `logs/`. |
| `artmind/orient/` | `artmind query orient` (spec `docs/superpowers/specs/2026-11-09-p2-1-orient-design.md`): a question-free, layered description of the corpus in two peels. `orient` (peel 1) = corpus block + one card per domain (purpose, unit, voice, time rule/dated/span/versions, stores, vocabulary, asset counts, coverage; a family root `rolls_up` its children); `orient --domain d` (peel 2; a root opens its children) = classes (schema definition, measured `types`, `dated` share), measured relation shapes, structured tables, views, topics, insights, superseded documents. `schema_facts` (schema files only), `card`, `domain`, `budget` (own budget: peel 1 9,000, peel 2 12,000 per domain / 24,000 per call; ordered cuts, definitions last, each in `cuts[]` with `rest`; never behind an `ARTMIND_OUTPUT_BUDGET` handle). Honest parts: an unread part is absent and named in `warnings`; `null` = declared absent. Reads the catalogue and `artmind/domain_config.py` (narrator). `orient --domain` counts as the views check in traces. `artmind-query` opens every turn with it (the orient text, promoted 2026-11-11; the classic text is archived at `docs/archive/artmind-query-classic/`, and a benchmark `contract` other than `classic` needs its own `artmind-query-<name>` skill). |
| `artmind/sql_browser.py` | `text2sql` delegated to the external `sql-browser` command (spec `2026-11-07-structured-table-relationships-and-sql-retrieval-design.md` §8; install with `uv tool install --editable ~/projects/sql-browser`). `ARTMIND_TEXT2SQL_ENGINE=auto|sql-browser|builtin` (default auto: sql-browser when installed). Per call: a scoped DuckDB file under `logs/sql/` with only the in-scope `<domain>__<table>` views, the domains' metadata merged from `domains/metadata/<domain>.meta.yaml` (+ generated `<domain>.mart.meta.yaml`), and artmind's LLM passed as `SQL_BROWSER_*` env vars (ibm_ica/openrouter → its OpenAI-compatible provider; secrets never on the command line). Same payload plus `engine`, `corrections`, sql-browser `warnings`; in auto, a failure falls back to the built-in path with a warning. Tests pin `builtin` (`conftest._builtin_text2sql`). sql-browser's LLM calls are not in `llm_calls.log`. |
| `artmind/mart/` | The mart (spec `docs/superpowers/specs/2026-11-10-mart-design.md`, plan `docs/superpowers/plans/2026-11-10-mart.md`): facts, dimensions and wholes over a domain's graph, as structured-store tables `STRUCTURED_DIR/<domain>/mart_<table>.parquet` (views `<domain>__mart_*`). Declaration `<ARTMIND_HOME>/domains/mart/<domain>.yaml` (`declaration`, checked by `domains validate`; a family root validates against its children's schemas). `mart enrich` (rules → `location` seeds incl. a locality chain → `--llm` for the rest; rule/seed records are recomputed each run, user and LLM records stay) writes curation records `CURATION_DIR/mart_dimension|mart_calendar/<id>.json`; `mart review list|set`, `mart calendar set`. `mart build` (no LLM; also best-effort after `projection rebuild` and `vault sync`, `hooks.build_after`) writes fact_happening, fact_mention, bridges, dim_time/person/place/type and whole_<kind> + fact_whole (trips by day-level away runs; life events per person by edge/name within a growing window), plus `domains/metadata/<domain>.mart.meta.yaml` for text2sql. `mart status` reports coverage. Orient folds `mart_*` tables into one card line `mart`. |
| `artmind/scope_check.py` | Out-of-scope check for `vector-text` and `survey`: when `--domain` is narrower than its domain root (the part before the first `.`), the vector leg also runs over the rest of that root (embedding reused, pool 50) and documents whose best passage beats the best returned row's relevance are reported as `outside_scope` (≤ 3: doc_id, title, domain, relevance, passage_id, chunk_id, best passage ≤ 600 chars) with a warning; `next` (first) reruns the search on the root. Key absent when nothing beats it, the search is on the root, or (survey) `--expand` / `--from` / `--to` are set. A fact, no routing (banking Q33). |
| `artmind/hints.py` | `next` signposts (orient spec §1.1): pure functions returning one runnable, shell-quoted (`shlex`) `artmind ...` command when a result is empty, thin or cut — entity-context evidence chunks cut → `survey` on the name, survey `coverage` partial and a topic plan sharing ≥ 2 question words → `query topics run` (spec 2026-11-10 §9.1), pattern6 → pattern5 shortest, empty pattern1/hierarchy/resolve-key/text2*, thin or cut entity-context, `--all` when a count is incomplete, a predecessor → `document-history`, an unranked `survey --by` period → `graph timeline`. Facts about the result, never about the question; joined with an existing `next` by `join_next`. `text2*` failures carry `next:` in the error text. `TracedGroup.resolve_command` (traces/span.py) names `artmind db …` for a misplaced `artmind query db …`. |
| `artmind/identity.py` | `entity-resolve` identity: a whole first-person reference resolves to the domain's narrator (`domain_config.narrator`; payload `narrator`, rows `matched_by: narrator`); same-class rows with the same normalised name are grouped in `twins` with a warning (never merged). |
| `artmind/supersession_direction.py` | `decide(a, b)`: which of two documents supersedes the other — explicit `Supersedes` / `Superseded By` links, then `Status`, then `_valid_from`; never version strings; `None` when ambiguous. Used by the conflict adjudicator and `ingest detect-supersession`; `ingest unsupersede` (tier-1 dry run) removes a wrong supersession record and restores the older document. |
| `artmind/assets.py` | Answer assets: what a domain already answers, one record per saved view, topic plan (`topic_plan`), insight detector and summary set (`kind`, `id`, `title`, `answers`, `stale` + reasons, `open`). `build_assets` fills the catalogue's `assets` part; `list_assets` merges domains; `like` ranks by meaning with the configured embedder (query vs `search_document:` text, through the embedding cache). CLI: `artmind query assets [--kind] [--like] [--stale]` (read-only). |
| `artmind/topics/plan.py`, `plan_store.py`, `grid.py`, `run.py` | Topic plans (spec `docs/superpowers/specs/2026-11-10-topic-breadth-design.md`): `<ARTMIND_HOME>/domains/topic_plans/<slug>/{plan.yaml,SKILL.md}` (`paths.DOMAIN_TOPIC_PLANS_DIR`), shaped like a saved view: read from disk on every call, never seeded, saved atomically. Version guard on the files: `built.version` + `built.fingerprint` (definition + SKILL.md); a change without a `version` bump or a downgrade is refused on load. `grid`: one keyword count query per subject (`_MATCHING`, `passage_text_ft`, `HIT_LIMIT` 5000) gives every subject × domain × period cell its `matching`; `take = min(matching, per_cell)`, cut round-robin under `max_docs`; undated cells are counted, never gathered; `gather` runs one `survey_search` per cell (subject as question, cell domain, period window). `query topics plan [SLUG | --list]` and `query topics run SLUG [--subject N] [--cell P|D] [--shape brief]` (proxied, traced as `survey`; `rows` is the budgeted list). `artmind topic plan validate`. `freshness` (spec §8.1, no LLM): generation short-circuit against `built.generations`, else a grid re-count (`new_documents`, `cells_grown`) and changed/retired sources; stored under `logs/topic-freshness/<slug>.json`; shown by `query topics plan [--list]`, used by `topic refresh --stale`. `schedule` (§8.3): the daily check started by `catalogue/refresh.start_topic_checks` in serve, chat-ui and admin-ui; only the admin console refreshes plans with `refresh: on_stale|weekly`, within `ARTMIND_TOPIC_MAX_COST_USD` (default 1.0). Incremental refresh: the map cache plus a section reduce cache (`r…` keys) — only subjects whose notes changed are reduced again, the overview always. Admin console Topics tab: `webui/topics_routes.py` + `static/topics.js` (plans, stored freshness, pages from disk). Agents read plans, never pages. The `:TopicPage` graph copy is gone; `artmind/migrations/drop_topic_page_graph_copy.py` (run by `setup`) drops old nodes and indexes. |
| `artmind/migrations/` | Temporary one-off migrations (e.g. legacy chunk vectors to passages). Delete when every graph's `setup` reports `legacy chunk vectors: none`. |
| `artmind/skills/` | **Source of truth for agent skills.** Shipped in the wheel and seeded into the run folder. |
| `artmind/domains/schemas/` | Default domain schemas (YAML), also seeded. |
| `artmind/webui/` | Chat UI (`index.html`), admin console (`admin.html` + Lane A agent chat + Lane B `dashboard.html`/`dashboard_routes.py`; its Insights and read-only Views tabs are `insights_routes.py` + `static/insights.js`/`views.js`, rendering snapshot text via textContent only; the console pre-fills its input from `/?ask=`; markdown is passed to `renderMermaid` and `renderArtmindGraph` (`mermaid-render.js`, `graph-render.js`) wherever it is injected), and the generated help/concept catalogue (`help.py`). `hosts.py` is the Host-header allow-list (DNS-rebinding guard): `run_chat_ui`/`run_admin_ui` apply it on a loopback `--host` (400 unless Host is 127.0.0.1/localhost/[::1]); a non-loopback bind allows all hosts and warns that the unauthenticated UIs are open. `create_app()` itself is unguarded so `TestClient` (Host `testserver`) works. `tool_gate` refuses `artmind topic show|list` for the gated agents (pages are for people; agents run topic plans). The admin profile adds the `artmind-build-topic` skill (propose → dry run → explicit yes → build → run the plan). |
| `artmind/webui/templates/dashboard.html` tab layout | Seven top-level tabs in workflow order: Ingest (documents, structured files, jobs, Add KG), Curate, Explore (sub-tabs Insights, Views, Topics), Data (Run SQL, table list), Evaluate (Benchmark), Reference (sub-tabs Schemas, CLI guide), System (snapshots, embeddings backfill). `dashboard.js` `initTabs` opens a group on its active sub-tab (firing its lazy loader); `?tab=` accepts any `data-tab` value and opens the parent group first. Insights shows findings; the Curate Health actions pane acts on them (`window.cpOpenHealth`). |
| `artmind/webui/explore_routes.py` | The admin console's **Explore → Graph** sub-tab (`static/graph.js`). `POST /api/explore/cypher` runs `text2cypher.generate_cypher(graph_mode=True)` (returns nodes/relationships/paths, refuses `CALL`, adds `LIMIT 100`) and returns the parameterised query, a self-contained one (`inline_parameters`/`cypher_literal`: Neo4j Browser can't take parameters) and `browser_url`, a deep link `<ARTMIND_KG_NEO4J_BROWSER_URL>?cmd=edit&arg=<cypher>` (http(s) only; the key is optional and per vault, its HTTP port is not derivable from the bolt URI). `POST /api/explore/cypher/link` re-checks hand-edited Cypher (read-only, no `CALL`, no `$params`). The tab's Run draws in place through `/api/graph/run`; Neo4j Browser is the secondary action. |
| `artmind/webui/graph_routes.py` | `POST /api/graph/run` (spec `docs/superpowers/specs/2026-11-14-interactive-graph-design.md`): runs an ```` ```artmind-graph ```` fence's `{domain, cypher}` in a `read_session()` with only `$domains` bound (another `$name` is a 400). `expand: <elementId>` instead of `cypher` runs the server's one-hop `EXPAND_CYPHER`: the centre node and its neighbours are `$domains`-scoped, `OPTIONAL MATCH` so a node with no in-domain neighbour still returns itself. `prepare_cypher` refuses comments, `LOAD`, `CALL`, a write and any `$param` but `$domains` (qa also needs `$domains`), running these textual checks on the text with string literals and backtick identifiers masked (`_mask_literals`), so `'Set up account'` passes; an unterminated literal is a 400; `LIMIT 100` is appended when absent. Connect timeout 5 s (`CONNECT_TIMEOUT_S`), query 15 s; an unreachable Neo4j is 502 "Neo4j is not reachable", `Neo.ClientError.Statement.*` is 400, a timed-out query 504. 2000 rows, 500 nodes (`truncated`/`omitted` say what was cut); relationship endpoints the query did not return are fetched by element id and scoped to the requested domains (`ENDPOINT_CYPHER` uses `domain_predicate`); nodes trimmed to `id, labels, name, _id, class, observation_count, _valid_from` + 40 capped properties. `mode="admin"` is registered on the admin console; `mode="qa"` (chat-ui: `$domains` required, 10 runs/minute per client) is implemented but chat-ui does not register it yet. Not traced; logs domains and length, never the Cypher. Drawn by `static/graph-render.js` (vendored Cytoscape + fcose, `static/vendor/VENDORED.md`, loaded on the first block; full-window view shared with mermaid via `static/viewer.js`). Admin: `static/graph-drawer.js` (topbar drawer; chat blocks' "edit Cypher" opens it; "Ask about this" fills the input, never sends) and Explore → Graph both mount the same component; `/api/explore/cypher` returns `run_cypher` (all but `$domains` inlined) and `/api/explore/cypher/link` takes `domains`. The agent writes the fence by the `artmind-present` skill. |
| `artmind/webui/curate_routes.py` | The admin console's **Curate** tab (spec `docs/superpowers/specs/2026-11-12-curation-surfaces-design.md`, plan `docs/superpowers/plans/2026-11-12-curation-surfaces.md`): `/api/curate/*` for the same-as queue (list with Merge/Link, evidence, batch approve/reject, full sweep job), conflicts (close), structured-table review (mapping/bridge/grain/catalogue), mart review (graph-health actions work from the Insights snapshot via `/api/insights`). Every write takes `dry_run` and returns the library's `effect`; commands whose logic sits in a Click body run in process through `_cli` (output unchanged). `db mappings/bridge/grain` have no dry run, so their preview is a sentence. Page code `static/curate.js` (same-as, queue strip) and `static/curate_panes.js` (other panes, `?tab=curate&queue=` deep link, which the plugin's Curate row uses); text via `textContent` only. The plugin's note commands (supersede, retire, restore, archive, history) are `src/noteCommands.ts`. |
| `artmind/cli_guide.py` | Renders the CLI as an HTML **fragment** for the admin-ui's "CLI guide" tab (`GET /api/cli-guide`), styled by `dashboard.css`. Ordering comes from `COMMAND_GROUPS` in `cli.py`; `test/test_cli_guide.py` fails if a command isn't routed there. There is no checked-in HTML copy. |
| `artmind/schema_reference.py` | Parses `domains/schemas/*_schema.yaml` prompts and renders an HTML **fragment** for the admin-ui's "Schemas" tab (`GET /api/schema-reference?prefix=`), reading the run folder's live schemas on every request. Grouped by domain family — the part of a filename before its first `.`. No checked-in copy. |
| `artmind/server.py` | The warm `serve` daemon. |
| `artmind/opencode/` | opencode/ACP persona, seeded into the run folder. |
| `obsidian/artmind-obsidian/` | The Obsidian plugin (TypeScript, esbuild, vitest). It only runs the `artmind` CLI with `--compact`; `test/fixtures/*.json` are that JSON, regenerated from the real CLI by `test/test_plugin_fixtures.py` (`ARTMIND_REGEN_PLUGIN_FIXTURES=1`). A CLI output change fails that test first: regenerate, then run `just obsidian-plugin-test`. `artmind init` installs it into a vault (copies the build staged in `artmind/obsidian_plugin/` -- a gitignored build output written by `just obsidian-plugin-build`, which `just dev-install` runs -- and enables it); `just obsidian-plugin-install <vault>` is the quick loop while iterating on the plugin. `serve.ts`/`serveDeps.ts`: optional warm `artmind serve` for the open vault (setting off by default, one port per vault, passed as `ARTMIND_SERVE_PORT` to the plugin's runs and the admin-ui it starts); reuses one only when serve's `/health` `vault` is this vault, stops only the pid it started; command "Restart serve". Spec: `docs/superpowers/specs/2026-09-30-obsidian-plugin-design.md`. |
| `artmind/setup.py` | `scaffold_run_folder()` (called by `setup`, via `setup_all()`), `scaffold_vault()` / `_symlink_skills()` (the `init` command; refuses the source checkout, `is_artmind_source()`), and Neo4j constraint/index setup. |
| `paths.py` | **Root-level module** (not inside the package). Resolves `ARTMIND_HOME` / `ARTMIND_DATA_DIR` and loads `.env`. Packaged via `py-modules`. |
| `utils/` | Shared helpers; packaged alongside `artmind`. |
| `test/` | The test suite — **singular**. |
| `docs/INSTALL.md` | Authoritative install/runtime reference. Read it before changing packaging. |
| `justfile` | Task runner; the entry point for every routine operation. |

Not source, do not edit: `build/`, `artmind9.egg-info/`, `__pycache__/`,
`obsidian/artmind-obsidian/node_modules/` and its build output `main.js`.
`tests/` (plural) is a **vestigial directory containing only stale `__pycache__`** —
the real suite is `test/`. Add tests to `test/`.

## Installed, not run from the checkout

This is the part that most often breaks assumptions. `artmind` is a **globally
installed command**, not something you invoke from the source tree.

```
just dev-install
```

expands to: stop daemons → `uv tool install --force --editable '.[ingest]'`. It does
**not** run `artmind init` and does **not** restart the daemons it stopped: run `init`
yourself where it's needed (see §2 below), and start `serve` again from inside the vault.

Two roots, both **decoupled from this checkout** (see `paths.py`, `docs/INSTALL.md`),
and both chosen by **the directory a command runs in**: `paths.py` walks up from the
cwd for `.artmind/vault.yaml` (or takes `ARTMIND_VAULT`).

- **Run folder** (`ARTMIND_HOME`) — inside a vault, the vault's own `<vault>/.artmind/`;
  outside any vault, `~/.artmind`. Holds `config.env`, `domains/schemas/`, `logs/`, and
  (outside a vault only) `.claude/skills/` and `.opencode/`. Machine-wide secrets always
  come from `~/.artmind/config.env`. A real `ARTMIND_HOME` env var overrides discovery.
- **Data dir** (`ARTMIND_DATA_DIR`) — inside a vault, `<vault>/.artmind/data/`; outside,
  default `~/artmind_data`. Ingestion artifacts only (originals, markdowns, registry DB,
  jobs, KG staging, snapshots). Query-only hosts never touch it.

The install is **editable**, so Python code edits are live everywhere immediately.
The mental model that matters: *editable covers the code, but not the two caches
in front of it* — long-running daemons, and run-folder copies.

**Core vs `[ingest]` extra.** The heavy ingestion ML stack (`docling` →
torch/CUDA/transformers, plus `langchain-text-splitters` and `openpyxl`) lives
behind an optional extra so query-only/pure-client consumers
stay lean. `just dev-install` installs `.[ingest]` — the dev box is full-featured.
A core-only install (`uv tool install artmind9`) omits document ingestion:
`import artmind.cli` still loads (the three libs are imported lazily — langchain
inside `ingest._split_markdown`, openpyxl inside `duckdb_adapter._excel_sheet_to_csv`),
and the chunking commands (`ingest sync`/`async`/`extract-kg`) raise a friendly
`click.ClickException` via `_require_ingest_extra()` naming the fix. The dev
dependency-group mirrors the extra so the hermetic suite still exercises the
chunking/xlsx paths. See `docs/INSTALL.md`.

## Testing implications

Read this before concluding a change works.

### 1. A running daemon serves stale code

`artmind serve` (port `$ARTMIND_SERVE_PORT`, default 8377) imports the code **once at
start**. `artmind/_entry.py` proxies every `query` call to it when it's alive. So after
editing the query layer, a running daemon happily answers with the **old** build and
your verification proves nothing.

This catches `uv run` too — `uv run artmind query ...` uses the same entry point and
**also proxies**. There is no invocation route that escapes it by accident.

```bash
ARTMIND_NO_PROXY=1 artmind query ...   # force in-process; what you almost always want when testing
just dev-stop-daemons                      # or kill it (also finds the ingestion worker)
```

To tell whether a daemon is masking your change, compare the two — if they disagree,
the daemon is stale:

```bash
artmind query --help
ARTMIND_NO_PROXY=1 artmind query --help
curl -s http://127.0.0.1:8377/health
```

`just dev-stop-daemons` identifies `serve` by the port it holds and verifies the process is
artmind before killing, so it won't touch an unrelated process on that port.

Trace capture has the same trap: a `serve` daemon started before `artmind/traces`
shipped answers proxied queries without recording spans. `artmind traces show`
warns when a turn ran `artmind query` but has no spans.

### 2. Skills reach the chat UI through the vault or the run folder

`artmind/skills/` is the **single source of truth**. It reaches consumers three ways,
and an **edit** to an existing skill and a **new** skill travel differently:

| Where | What is there | Edit to an existing skill | New (or removed) skill |
|---|---|---|---|
| Checkout `.claude/skills/`, `.pi/skills/` | symlinks into `artmind/skills/` (gitignored; a fresh clone lacks them) | live | `just dev-refresh-skills` |
| Vault `<vault>/.claude/skills/` | symlinks written by `artmind init` (`_symlink_skills()`); with the editable install they point straight at this checkout's `artmind/skills/<name>` | live | `artmind init`, run **inside the vault** |
| Run folder `~/.artmind/.claude/skills/` | **copies** written by `artmind setup` (`scaffold_run_folder()`) | `artmind setup`, run **from `~`** | `artmind setup` adds it; a removed skill's copy is never pruned (delete it by hand) |

Inside a vault the chat agent reads the vault's symlinks. The two backends start it in
different places: ACP at `ARTMIND_AGENT_CWD` (the vault root), the Claude SDK at
`RUN_FOLDER` = `ARTMIND_HOME` (`<vault>/.artmind`, `artmind/webui/agent.py`), and the
SDK agent still loads the vault-root skills from there (traces show `Skill:artmind-query`).
Only a chat UI started **outside** any vault reads the `~/.artmind` copies. A vault has
no `.artmind/.claude/skills/`, and `setup` deliberately writes none there.

**Never run `artmind setup` or `artmind init` from this checkout.** `init` refuses it
(`is_artmind_source()`); for `setup`, run it from `~` when you mean to refresh
`~/.artmind`. Inside a vault `setup` targets that vault's `.artmind/` instead and does
**not** refresh any skills. `just cli-setup` (runs from `~`) and `just cli-init <vault>`
(runs inside the vault) wrap the first two commands below.

```bash
cd ~ && artmind setup                    # refresh ~/.artmind copies (skills, .opencode/, schemas)
cd <vault> && artmind init               # link skills added since the vault was made
just dev-refresh-skills                  # link new skills into the checkout's .claude/ and .pi/
```

`just dev-install` runs none of these.

**Schemas follow the same split.** Outside a vault, `setup` **overwrites**
`~/.artmind/domains/schemas/` (and `.opencode/`) from the package on every run —
deliberately, since schemas carry the extraction prompts — while `config.env` is
seeded only when absent. A vault's schemas are the user's committed files: `init` and
`setup` seed them only when absent, so a packaged schema fix reaches a vault only via
`artmind init --force [--schema NAME]` inside it (refused unless git can restore every
file it would change). Keep schema edits in `artmind/domains/schemas/`.

Do not edit `~/.artmind/.claude/skills/` — `setup` overwrites it. Editing a skill and
testing only in the checkout exercises the vault path too (both are symlinks), but not a
run-folder chat UI: if they disagree, the run folder wasn't re-seeded.

### 3. Green tests do not mean the CLI works

```bash
just dev-test        # uv run --group dev pytest test/ -v
```

About 5,000 tests run in ~3 minutes with **no Neo4j and no network** — they import modules directly
(`from artmind.cli import cli`) and drive Click via `CliRunner`, with external services
mocked. That makes them fast and hermetic, but it means they:

- bypass the `_entry` proxy entirely, so they can never catch a stale-daemon problem;
- never touch a real Neo4j, so they can't validate Cypher against actual data.

Unit tests are the right first check, but end-to-end behaviour needs a real invocation
against a live Neo4j with `ARTMIND_NO_PROXY=1` (or a freshly restarted daemon).

**A mocked graph session answers every query successfully.** A bare `MagicMock()` session
returns a truthy result for *any* Cypher, so a test passes identically whether the query
matched the right node, the wrong node, or nothing at all — and an empty Neo4j `MATCH`
raises nothing, it just does no work. That combination hid a real defect in
`update confirm`: it matched entities by the LLM's extracted name instead of the node id
the user picked, so every write silently no-opped while the returned counts still reported
success (no test exercised the `link` path at all). When testing a graph write, assert on
the **parameters actually sent** and on which query ran — `test/test_update.py`'s
`run_side_effect` recorders are the pattern — never on summary counts alone, and never
trust a count the code increments outside the branch that did the work.

### 4. Docs and code drift in both directions

The CLI's own help text is prose that can go stale — e.g. the `graph` group docstring
listed "pattern1–pattern9" long after `pattern10` existed, which actively misled readers
into thinking `pattern10` wasn't under `graph`. When adding a command, update the group
docstring, the relevant skill in `artmind/skills/`, and the `justfile` recipe together.

`just dev-cli-help` dumps the real command hierarchy — trust it over any prose.

### 5. Claude Code's `ANTHROPIC_BASE_URL` hijacks LLM calls run from its shell

artmind loads `config.env` **without** overriding real environment variables. Depending
on how Claude Code is signed in (e.g. through a custom endpoint or gateway), it exports
its own `ANTHROPIC_BASE_URL` into every shell it runs. Any `artmind` command run from a
Claude Code shell (an agent's Bash, `just`, `uv run`) then sends `ibm_ica` extraction
requests to **Claude Code's** endpoint instead of the configured one, and every LLM step
fails with **`Error code: 404`** within seconds. The log says so once per process
(`extraction.ibm_ica_shadow_hint`): "ANTHROPIC_BASE_URL from the process environment
overrides the one in …/config.env".

Before the extraction loss guard
(`docs/superpowers/specs/2026-10-18-extraction-loss-guard-design.md`), that failure was
staged as an **empty extraction** and committed: a note silently lost its facts. Now the
file is marked failed and the previous staging is kept, but the run still does nothing.

- **One command:** `env -u ANTHROPIC_BASE_URL artmind ingest sync "<path>" --reextract`.
- **Permanently:** set `ARTMIND_KG_ANTHROPIC_BASE_URL` in `~/.artmind/config.env` to the
  ibm_ica endpoint. `ibm_ica_client_env` checks it before `ANTHROPIC_BASE_URL`, so an
  exported value no longer matters.
- **Check:** `env | grep ^ANTHROPIC_` in the shell you're about to ingest from.

Hermetic tests mock the LLM, so they never see this; only a real ingest does.

## Command routing quick reference

Two levels under `query`, and mixing them up is a common error:

- `artmind query graph <cmd>` — `metadata`, `structural-metadata`, `entity-listing`,
  `pattern1`–`pattern10`, `text2cypher`, `conflicts`, `timeline`, `hierarchy`
- `artmind query <cmd>` — `orient` (peel 1 without `--domain`, peel 2 with; read-only, proxied, traced), `domains-overview`, `vector-text` (`--context`), `entity-resolve` (`narrator`, `twins`),
  `chunks`, `entity-context` (positional question ranks; `hub`/`cut`), `entity-history` (`--entityId`, or `--key`/`--name` for an
  entity the projection already removed), `document-history` (current vs history
  observations of one document's versions), `text2sql`, `resolve-key`, `views list|show|run`, `insights list|show`
  (reads the latest insights snapshot only; guided by artmind-query's Insights section; operators use the `artmind-insights` skill), `survey` (cross-document ranking, `coverage`; proxied and traced), `topics plan|run` (saved topic plans, run live, no LLM; proxied and traced)
- `artmind topic <cmd>` — `build`, `refresh`, `list`, `show`, `delete`, `plan validate` (not proxied; build/refresh make LLM calls; write `<vault>/_Topics/` and the plan folder)
- `artmind views <cmd>` — authoring: `validate`, `test`, `save`, `delete` (not proxied)
- `artmind insights <cmd>` — `refresh` (needs Neo4j), `list`, `show`, `profile`, `runs`, `same-as [--export]`, `config check` (not proxied)
- `artmind traces <cmd>` — recorded query traces: `list`, `show`, `triage`, `feedback`, `stats`, `compare`, `misses`, `prune` (not proxied)
- `artmind review <cmd>` — review dossiers: `new`, `list`, `show`, `status`, `validate`, `reindex`, `leakcheck`, `shape`, `reextract`, `shape-report`, `attach`, `regressions`, `verify`, `prompt` (not proxied)
- `artmind retrieval <cmd>` — `labels generate|validate`, `eval` (not proxied; labels and runs hold real questions and stay in the vault)

Command notes: `pattern7`, `pattern8`, `entity-listing` carry `complete` and take `--all` (pattern8 pages to 2000 rows in a total order); `survey --by year|month [--perPeriod N]` reports `periods: [{period, keyword_hits, ranked, documents}]`; Document/Entity dates are read from `_valid_from` (passages and chunks carry no date, so `vector-text --asOf` does not narrow). `query graph pattern10 --includeHistory` (`--asOf` is kept as an alias, not a date filter); `pattern7` returns `rows_total`, `truncated` and `documents` (`--limit` 1–500); `document-history` matches a missing extension and suggests close names; `text2cypher`/`text2sql --dryRun` return no rows and a `next` step.

`text2sql`/`resolve-key` query the structured (SQL) store, not the graph — see
`artmind db <cmd>` (`list`, `schema`, `sql`, `mappings`, `catalogue`, `refresh`,
`connect`, `backup`, `restore`) for managing/reading that store directly.

## Conventions

- **Use the justfile.** It wraps every routine operation; recipes use `uv run artmind`
  (project venv) rather than the globally installed command.
- Python `>=3.14.4`, managed with `uv`. Don't hand-edit `uv.lock`.
- CLI options are `camelCase` on the command line (`--entityClass`, `--documentName`)
  mapped to `snake_case` Python params. Match the existing style.
- Commands take repeatable, comma-splittable `--domain` and support `--compact` JSON.
- **CLI uniformity** (`artmind/cli_conventions.py`, enforced by `test/test_cli_conventions.py`):
  - *Envelope.* Every JSON result printed through `_echo_json` carries `command` (the
    canonical Click path), `warnings` (a list, `[]` when none) and `next` (string or `null`)
    first; every other key is untouched. Never rename or remove a payload key. Commands whose
    text is the point (`init`, `setup`, `domains *-prompt`, `review prompt`, ...) keep that text
    on **stderr** and print the JSON summary on stdout. Capped listings carry `rows_total` and
    `truncated`; bare-list job commands and ranked top-K retrievals do not (their shape is
    read by the plugin / has no meaningful total).
  - *Spelling.* Options are camelCase. A renamed option keeps its old spelling as a hidden
    alias (`aliased_option`, listed in `DEPRECATED_ALIASES`) that prints one `artmind:` line on
    stderr. In-repo callers (skills, justfile, plugin, web UI, living docs) must
    use the new spelling; the contract test scans them. Remove an alias only in its own commit
    after every caller moved and 30 days passed.
  - *Writes.* Every command in `WRITE_COMMANDS` has `--dryRun`. **Tier 1** has a real one:
    it reads, plans, sends no write Cypher, writes no file and makes no LLM call, and returns
    `dry_run: true` plus an `effect` block (`summary`, `changes[{kind,count,sample}]`,
    `stale{topics,views}`, `llm{calls,model,est_cost_usd}`, `reversible`, `undo`, `travels`,
    `records`). The real run carries the same `effect`, computed from the plan before the work
    starts, so it describes the plan, not outcomes. **Tier 2** commands take `--dryRun` too,
    but it fails with "not available ... yet; nothing was changed" (`unbuilt_dry_run_option`)
    until someone builds it; a tier-1 command needs `test/test_dry_run_<command>.py` asserting
    on the Cypher and params sent (`test/_dry_run.py`, `test/_neo4j_recorder.py`). A new
    command that writes the graph, staging or vault files goes in `WRITE_COMMANDS` (or, if the
    check misfires, in `_READ_ONLY` in the contract test with the reason).
  - *Exit codes.* 0 ok, 1 bad input (`ClickException`), 2 usage error, 3 a store (Neo4j /
    registry) failure, mapped in `_RootGroup` (`exit_on_store_failure`).
  - Changing a command's JSON also changes `obsidian/artmind-obsidian/test/fixtures/`:
    regenerate with `ARTMIND_REGEN_PLUGIN_FIXTURES=1`, then `just obsidian-plugin-test`.
- Skills live in `artmind/skills/` and the opencode persona in `artmind/opencode/` —
  edit there, never in `.claude/skills/` (a symlink), `~/.artmind/` (overwritten by
  `setup`), or `build/` (an artifact). How an edit or a new skill reaches the checkout,
  vaults and `~/.artmind` is in Testing implications §2.
