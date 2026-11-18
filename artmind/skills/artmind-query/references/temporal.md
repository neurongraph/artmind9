# Temporal: `--asOf`, `--knownAsOf`, `entity-history`, `timeline`, `_temporal_props`

Read this for a temporal question — "as of <date>", "history of…", "what was X
before it changed", "what happened in order" — or when an entity carries
`_temporal_props`.

- **timeline vs entity-history**: `timeline` is domain-scoped, not entity-scoped — it lists every entity of an `identity: occasion` class ordered by `valid_from` (undated last), for "what happened in this domain, in order". For ONE entity's own history — "what was X's value before it changed", "what did X look like on date D" — use `entity-history` instead: it reads every observation behind that entity, fact-level (`_valid_from`), spanning both current and retired (`docs retire`/superseded) sources. `--property P` narrows to one property's value at each point.
- **`_temporal_props` is the signal to drill into `entity-history`.** An entity carrying
  `_temporal_props: ["rate_value", ...]` means that property genuinely varies across
  instants — the value on the entity itself is only the current winner (latest
  `valid_from`). If the question asks about change over time ("has this changed",
  "what was it before") and the property is listed there, don't stop at the entity's own
  value; call `entity-history --entityId <id> --property <p>` to get every instant.
- **No `--asOf` on entity commands, and none needed.** The projection (`:Entity`) is
  current by construction — a retired document's contributions are relabelled out of it
  entirely (see `docs retire` / the History labels in SKILL.md), so there is nothing stale left
  to filter out. None of the ten `pattern*` commands, `entity-listing`, `entity-resolve`,
  `entity-context`, or `graph metadata` accept it — don't pass it.
- **`--asOf` is a point in valid time** on the graph commands that keep it:
  `vector-text`, `chunks`, `graph filing-listing`, `entity-history` (on the structured-store
  commands `db timeline` and `text2sql` it is a floor; `db sql` takes no `--asOf`; see their help). A row is kept when
  its own date window contains the date (`_valid_from <= asOf < _valid_to`; a year or
  month means its last day); undated rows are always shown. Chunks, passages and summaries
  carry their document's stated end, or its supersession's end; a row with neither is open and
  satisfies any `--asOf` at or after its `valid_from`. `entity-history --asOf D` returns the facts
  whose own window contains D: a fact closed before D is not shown. On `pattern10` use
  **`--includeHistory`** instead: it additionally matches
  `:DocumentHistory`/`:DocChunkHistory` for that document, surfacing retired chunks
  alongside current ones; omitting it matches only the current document. Use
  `--includeHistory` when the question is explicitly historical ("what did the policy
  say in January", "history of…", "previous version", "what changed"); omit it
  otherwise. (`--asOf` still works on `pattern10` as an alias, but it is a switch, not a
  date filter.)
- **Two time axes, one set of fields.** Valid time (when a thing was true):
  `_valid_from`, `_valid_to`, `_valid_precision` (`day|half|month|quarter|year`, or `none` =
  undated: the ingestion default, shown by every date filter and never grouped as an
  instant), `_valid_time_of_day`, `_valid_to_by` (what set an end: a stated end carries
  none, a supersession its record id), `_due`, `_review`, `_revised_at` (a document's
  "last updated", not its effective date); documents, chunks, passages and summaries also
  carry `_doc_valid_to` (the document's own stated end; chunks, passages and summaries
  `_doc_valid_from` too). Record time
  (when artmind learned it, UTC): `_recorded_at`, `_retired_at`, `_retired_by`
  (`version:<n>` = an edited note re-ingested, `retire`, `supersede:<id>`, `removed` = the note left the vault, `retract:<observation id>` = a fact the projection retracted, `resplit` = a
  chunk dropped by re-chunking), `_revived_at`, `_record_time_approx` (backfilled).
  Superseding a document ends its facts' validity at the effective date (`_valid_to`;
  a document, chunk, passage or summary keeps the earlier of that and its stated end, a
  fact keeps its own end); retiring one closes nothing; `ingest unsupersede` reopens what
  the supersession closed (a stated end comes back).
- **`--knownAsOf T` reads what the graph held at T** (record time, not valid time) on
  `entity-history`, `document-history` and `graph pattern10`: only content recorded by
  T and not yet retired then, from both current and history nodes, with each row's
  `status` (and `document-history`'s `retired`) as of T. A date means the end of that
  local day; `now` and ISO datetimes work too. Use it for "what did we know on D",
  "what did the graph say before the last re-ingest". `--asOf` asks when a fact was
  true; `--knownAsOf` asks when artmind learned it. Nodes the migration pack backfilled
  carry approximate record time (`_record_time_approx`, and a warning says how many
  rows): a History node backfilled with the epoch counts as known since before the
  backfill, so an earlier `--knownAsOf` may still show it.
  Limits: a re-committed chunk keeps its first `_recorded_at` but holds the new text
  (`pattern10 --knownAsOf` shows current text); a retire-then-revive window is invisible
  (a revive clears `_retired_at`); `document-history --knownAsOf` does not filter Document
  nodes (a document first ingested after T appears with `observations: []`).
- **`happenings`** lists dated occasions of an `identity: occasion` class. Example:
  `artmind query happenings --domain personal_journal --match "tennis" --by year --with <person id> --shape brief`
  gives one row per day (`type`, participants, places, `chunk_ids`) and per-year counts;
  `coverage` larger than the rows means `survey` for the rest. Undated rows sort last and
  never fall in a `--from/--to` window; `graph timeline` instead keeps undated rows in
  windows and has no row cap. An occasion's key is `name~<date>`, the date at its own
  precision (`~2024-03-30`, `~2024-03`, `~2024-Q1`, `~2024-H1`, `~2024`): "tennis" on two
  days is two entities, and a month-only mention ("tennis in March 2024") is its own entity,
  not the one on 1 March. Its row's `precision` says which; an undated mention has no date.

## Edited documents: what a re-ingest keeps

When a note changes and is ingested again (the Obsidian plugin's Ingest, `ingest
sync`/`async`), its version goes up and its facts are re-extracted. Nothing is deleted:

- Every observation of the previous version is relabelled `:ObservationHistory`, then
  the new version is written. An observation id is deterministic per chunk + canonical
  name + class + domain, so a fact the new version **still states is revived in place**
  (back to `:Observation`, `doc_version` bumped). Only a fact the new version **no
  longer states** stays history, carrying the `doc_version` that last asserted it.
- An entity left with no current observation anywhere (e.g. a misspelled person a
  corrected note no longer mentions) is removed by the projection rebuild — so
  `entity-resolve` and `entity-history --entityId` cannot find it any more. Reach its
  history with `entity-history --name "<name>"` or `--key "<name>|<CLASS>|<domain>"`.
- **Chunk text is not versioned.** Chunk ids are `{doc_id}_{seq}`, so the new
  version's chunk takes over the old id and its text. `:DocChunkHistory` exists only
  when a version has fewer chunks than the one before, and a history observation's
  `chunk_id` points at a chunk that now holds the **current** text. Don't quote that
  text as what the old version said. The earlier wording lives only in the vault's git
  history (the note itself and its `kg/<domain>/<folder>/` staging files) — say so.

`document-history --documentName "<name or path>"` shows it per document: `summary`
(current / history counts, `history_versions`), `dropped_keys` (entities an earlier
version stated that the current one no longer does — a corrected typo shows up here) and
every observation with its `status` and `doc_version`. Which current keys are *new* is
not recoverable: a kept fact's revived observation looks the same as a new one. A name missing its extension (`.md`, `.pdf`, …) is matched when exactly one document fits (`matched_name` plus a warning); a miss returns `closest` names and a runnable `next`.

See `references/structured-store.md` for how `--asOf` threads through the hybrid
chain (`vector-text`/`text2sql`/`db timeline`) when a question is both temporal and
structured-store-involving.
