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

See `references/structured-store.md` for how `--asOf` threads through the hybrid
chain (`vector-text`/`text2sql`/`db timeline`) when a question is both temporal and
structured-store-involving.
