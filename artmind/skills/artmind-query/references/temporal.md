# Temporal: `--asOf`, `entity-history`, `timeline`, `_temporal_props`

Read this for a temporal question — "as of <date>", "history of…", "what was X
before it changed", "what happened in order" — or when an entity carries
`_temporal_props`.

- **timeline vs entity-history**: `timeline` is domain-scoped, not entity-scoped — it lists every entity of an occurrent class ordered by `valid_from`, for "what happened in this domain, in order". For ONE entity's own history — "what was X's value before it changed", "what did X look like on date D" — use `entity-history` instead: it reads every observation behind that entity, fact-level (`_valid_from`), spanning both current and retired (`docs retire`/superseded) sources. `--property P` narrows to one property's value at each point.
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
- **`--asOf` still exists, with a narrower meaning, on the commands that keep it**:
  `vector-text`, `chunks`, `docs list`, `pattern10`, `db timeline`, `db sql`. On all of
  these it is a **floor** ("in force by this date"), not a point-in-time snapshot —
  `valid_to` is rarely set, so a still-open row satisfies any `--asOf` at or after its
  `valid_from`. On `pattern10` specifically it is a **presence flag**, not a date value:
  passing it (any value) additionally matches `:DocumentHistory`/`:DocChunkHistory` for
  that document, surfacing retired chunks alongside current ones; omitting it matches
  only the current document. Use it when the question is explicitly historical ("what
  did the policy say in January", "history of…", "previous version", "what changed");
  omit it otherwise. `entity-history` is the one command with a genuine
  point-in-time `--asOf`, because it's reading the fact-level valid-time axis directly.

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
not recoverable: a kept fact's revived observation looks the same as a new one.

See `references/structured-store.md` for how `--asOf` threads through the hybrid
chain (`vector-text`/`text2sql`/`db timeline`) when a question is both temporal and
structured-store-involving.
