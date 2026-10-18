# Extraction loss guard — design

Date: 2026-10-18. Status: approved for implementation.

## 1. Incident

A two-machine vault (one laptop ingests, the other pulls through Obsidian Git)
lost every fact of three daily notes overnight:

1. Machine B ran `ingest … --reextract` over about 110 unchanged notes.
   `--reextract` first deletes each note's chunk cache
   (`_reset_extraction_cache`: the `kg_chunk_status` rows and
   `<kg>/<domain>/<stem>/chunks/<sha>/`, which is **committed** with the vault).
2. For three notes every LLM entity step came back empty, after the one
   pre-merge retry. The skill's error table already names the likely cause
   ("Extraction completes in seconds with 0 entities and all chunks failed" —
   provider rate limiting). 107 notes in the same run extracted normally.
3. `extract_kg` wrote the new staging anyway (`write_staging`, one atomic
   swap), replacing non-empty `observations.json` / `relationships.json`
   (13–33 KB each) with `[]`. The only signal was `chunks_with_failures=N` in a
   log line; the file was reported `completed`.
4. Obsidian Git committed the empty folders; machine A pulled them and its
   graph rebuild wrote `0 observation(s)` for each note, replacing the 18 it had.

The notes' text never changed. Nothing anywhere reported the loss; it was
found because a query answer missed a known fact.

## 2. Problems exposed

| # | Problem | Where |
|---|---|---|
| P1 | A re-extraction with chunks still `failed` after the retry overwrites the previous staging. | `ingest.extract_kg` |
| P2 | `--reextract` deletes the previous chunk cache before extracting, so even a refused write leaves the committed cache gone. | `ingest._reset_extraction_cache` |
| P3 | A re-extraction of an unchanged body whose steps all report `ok` but yield zero observations (a model returning `[]`) also replaces a non-empty previous result. | `ingest.extract_kg` |
| P4 | No way to list notes whose staged extraction is empty although their text is not. | — |
| P5 | `extract-kg` / `write-to-graph` say "not found in registry" for a note that is staged and in the graph but was ingested on another machine (the registry is per-machine). The ingestion-helper skill's error table explains that as "never ingested". | `cli.py`, `artmind-ingestion-helper` |
| P6 | The ingestion-helper skill steers re-extraction of a single note to `extract-kg DOCUMENT_NAME`, which only works on the machine that ingested it; `ingest sync <path> --reextract` works everywhere. | `artmind-ingestion-helper` |
| P7 | An agent that finds an answer in a note but not in the graph (admin console: file reads allowed) answers from the note without saying the graph lacks it. | `artmind-query` |
| P8 | `traces list` omits `session_id`, so an operator cannot see which turns share a conversation (it is stored; `traces show` has it). | `cli.py` `traces list` |

Out of scope: recovering the three notes (an operator re-runs them after this
ships, §5), and the query-routing roadmap from the 2026-10-18 artmind-query
review (views in `domains-overview`, a `query route` command).

## 3. Design

### 3.1 Never replace staging with an incomplete extraction (P1, P2)

- `_reset_extraction_cache` **moves** the chunk cache aside instead of deleting
  it and returns a stash: `{"dir": <moved dir or None>, "rows": [<kg_chunk_status
  rows>]}`. The stash directory lives under `LOGS_DIR / "reextract-stash"`
  (gitignored, like every other artmind temp file), never under the committed
  staging folder.
- `_restore_extraction_cache(stash, chunk_data_dir, doc_sha256)` puts both back:
  the directory (replacing whatever the failed run wrote) and the rows (replacing
  the rows the failed run wrote). `_drop_extraction_stash(stash)` deletes the
  stash after a successful write. Both accept `None` (no `--reextract`) as a
  no-op.
- After the pre-merge retry pass, if any chunk still has a `failed` step,
  `extract_kg` logs an error naming the note and the failed chunk numbers,
  restores the stash, **does not call `write_staging`**, and returns `None`.
  `ingest_to_kg` already turns `None` into `False`, which the worker records as a
  failed file; `ingest extract-kg` prints the failure and exits non-zero.
- An exception raised during extraction also restores the stash before
  propagating.
- Without `--reextract` (first ingest, content change, resume) there is no
  stash; the failed chunks' `kg_chunk_status` rows stay `failed`, so
  `extract-kg` resumes exactly those steps as today. The only behavioural
  change is that no staging is written while any step is failed — a previous
  version's staging, if any, stays in place.

### 3.2 An unchanged note may not shrink to nothing (P3)

When `file_result["reextract"]` is set and the new extraction has zero
observations while the staged `observations.json` it would replace is
non-empty, `extract_kg` refuses: error log ("re-extraction of an unchanged note
produced no observations; previous N kept"), restore the stash, return `None`.
Not applied to a changed body: deleting the text of a note legitimately empties
it.

### 3.3 `artmind ingest empty-extractions` (P4)

Read-only, no Neo4j, no LLM. Walks `KG_DIR/<domain>/<stem>/` staging folders and
lists the ones whose `observations.json` is an empty list while their
`chunks.json` text totals at least `--minChars` (default 200) characters.

```
artmind ingest empty-extractions [--domain D]... [--minChars N] [--compact]
→ {"notes": [{"path", "domain", "chars", "chunks"}], "scanned": <folders read>}
```

`--domain` is repeatable and comma-splittable and matches a domain family
(`d` or `d.*`). `path` is the staged document's `path`, so the operator can pass
it straight to `ingest sync <path> --reextract`. Routed under "Graph building"
in `COMMAND_GROUPS`. Implemented in a new module `artmind/staging_audit.py`.

### 3.4 Registry miss hint (P5)

`extract-kg` and `write-to-graph` keep their exit status and add one line when
`KG_DIR/<domain>/<stem>/document.json` exists for the name:

> It is staged (`<folder>`) but not in this machine's registry — it was probably
> ingested on another machine. Re-extract it here with
> `artmind ingest sync "<path>" --reextract`.

### 3.5 Skills (P6, P7)

- `artmind-ingestion-helper`: Situation D says `extract-kg` works only on the
  machine that ingested the note and points to `ingest sync <path> --reextract`
  for a note staged elsewhere; the error-table rows for "not found in registry"
  and "0 entities and all chunks failed" are corrected (the second now says the
  previous staging is kept and the file is marked failed); `ingest
  empty-extractions` is listed as the check for silently empty notes.
- `artmind-query`, Answer Style: if the answer came from reading a note directly
  rather than from `artmind query` output, say the graph does not hold it and
  suggest `artmind ingest empty-extractions` / a re-extract to the operator.

### 3.6 `traces list` shows `session_id` (P8)

Add `session_id` to each `traces list` row.

## 4. Testing

Hermetic, no Neo4j and no LLM, using the full-extraction seams of
`test/test_vault_chunk_cache_merge.py` (`_seams`: `_llm_extract`,
`_embed_text`, prompt builders mocked) on an `ingest_env` vault:

- a re-extraction whose entity step fails on every attempt leaves
  `observations.json`, `relationships.json`, `document.json` byte-identical,
  restores the chunk cache directory and the `kg_chunk_status` rows, and returns
  `None`;
- the same with the model returning `([], True)` for a note whose previous
  staging was non-empty (§3.2) — and a *changed* note may go empty;
- a successful re-extraction removes the stash and writes new staging;
- an exception inside extraction restores the stash;
- a first ingest whose steps fail writes no staging and leaves the rows
  `failed` for resume;
- `empty-extractions` on synthetic staging folders: empty-with-text listed,
  empty-but-short not listed, non-empty not listed, domain family filter;
- the registry-miss hint appears only when a staging folder exists;
- `traces list` rows carry `session_id`.

Assert on files, rows and the arguments passed — never on summary counts
(CLAUDE.md, "A mocked graph session answers every query successfully").
Synthetic text only: no vault content in tests.

## 5. Operator follow-up (not part of the implementation)

After this ships, from inside the affected vault: `artmind ingest
empty-extractions --domain <d>` to list emptied notes, then `artmind ingest
sync "<path>" --reextract` for each one the operator confirms.
