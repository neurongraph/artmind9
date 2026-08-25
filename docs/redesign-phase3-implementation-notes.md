# Phase 3 implementation notes

What actually landed for Phase 3 (observations and the projection), against the
spec in [projection-pipeline.md](./projection-pipeline.md). Read that first —
this is implementation scope and decisions, not the design.

Every later phase reads this file. The sections that matter most for them are
**Deferred, on purpose** and **What later phases now inherit**.

---

## What changed

### New modules

| Module | Holds |
|---|---|
| `artmind/observations.py` | The pure key function, deterministic observation/entity ids, and `build_observation`. No I/O. |
| `artmind/projection.py` | `merge_observations` and `affected_keys` (pure), plus the rebuild I/O: `rebuild_key`, `rebuild`, `full_rebuild`, `keys_for_document`, `all_keys`. |
| `artmind/canonicalize.py` | Retrieved name vocabulary (ANN, recurrent classes only) and the one-per-document canonicalization pass. |
| `artmind/same_as.py` | The curation seam — groups, not pairs. Loads nothing until Phase 6. |
| `artmind/lifecycle.py` | `retire_document` / `restore_document`. Phase 5 work pulled forward; see below. |

**The pure/I-O split is load-bearing, not stylistic.** Every judgment the
projection makes — who wins, what unions, what is a conflict and what is
history, which keys get swept — is a pure function over dicts. Logic expressed
inside a Cypher query is logic that a `MagicMock()` session cannot test, because
it returns a truthy result for any query. `merge_observations` has 36 unit tests
that check values, not counts.

### `ingest.py`

- `_write_to_neo4j` is now one `session.execute_write(_commit_document_tx, ...)`:
  prior-version demotion → document → chunks → observations → **projection
  rebuild** → relationships. A failure anywhere raises and the whole commit
  fails.
- `_retract_document_from_neo4j` became `_retract_prior_version(tx, ...)`, an
  assertion-time status transition (`latest` → `history`). The session-level
  wrapper survives for `_purge_from_neo4j` and friends until Phase 5 deletes
  them.
- `_document_valid_time()` derives `_valid_from`/`_valid_to`/
  `_valid_time_source` at ingest, before observations are written.
- `_build_observations()` turns extraction output into observation property
  maps, applying the canonicalization mapping and the class's `kind`.
- `embed_missing_entity_embeddings` became the sweep: matches
  `embedding IS NULL OR embedding_stale`, clears the flag as it writes, scoped
  to affected keys.
- `rebuild_projection()` — the deferred/recovery entry point.
- **Deleted:** `_upsert_entity`, `_merge_prop_value`, `_merge_props_dicts`,
  `_parse_prop_sources`, `_ledger_upsert`, `_fold_ledger`,
  `_rollback_property_ledger`, `_reassert_superseding_properties`,
  `_incoming_property_values`.

### Extraction

- `meta.yaml` gained `kind_naming_rules` and a `canonicalization` prompt
  template, plus a `{{NAME_VOCABULARY}}` slot in the entities template.
- `prompt_builder._render_class_block` renders the class's `kind` as a naming
  instruction, before the class's own `guidance` so a schema still gets the
  last word.
- `build_entities_prompt(text, schema, vocabulary=None)`.

### Elsewhere

- `setup.py` — the `:Observation` zone (id constraint; indexes on
  `(key, _status)`, `doc_id`, `domain`), plus `Entity.key` and
  `Entity.embedding_stale`.
- `temporal.py` — `apply_supersession` retires the older document instead of
  reaching into the entity layer. **Deleted:** `_retire_orphaned_entities`,
  `_stamp_chunk_valid_from`, `apply_node_supersession`, `normalize_time`,
  `_normalize_time_one_domain`, `normalize_ingested_document`.
- `update.py` — `write_user_chat` records a UserChat and its Observations, then
  rebuilds. `_link_entity_in_session` (a write) became
  `_resolve_target_identity` (a lookup).
- `refine_pipeline.py` — **deleted.** All six of its steps have evaporated.
- `graph_snapshot.py` — `Observation` added to `BASE_LABELS` (stopgap; see
  Deferred).
- CLI — added `projection rebuild`, `docs retire`, `docs restore`; removed
  `ingest normalize-time`, `ingest refine-pipeline`, `update supersede`.

---

## Decisions taken, and why

### `Entity.name` comes from `canonical_name`

Confirmed with you before coding. The merge table says "longest, tie-broken by
frequency" without naming the field; taking the longest *raw* name would
surface exactly the measurement-laden strings the key function exists to strip
(`"SmartSaver Account Tier 2 Rate — 4.70% AER (£10,001–£50,000), effective
2026-01-15"`), and the gate would fail on its own first assertion. Raw wordings
are preserved on the observations and unioned into `aliases`.

**Aliases exclude the chosen name by exact match, not by normalized key.** The
first implementation excluded anything sharing the key, which silently dropped
January's long form — the most informative wording in the set. Caught by
reading the gate's output rather than by a test.

### Two valid-time axes on every observation

Confirmed with you before coding.

| Property | Level | Decides |
|---|---|---|
| `_valid_from` / `_valid_to` | fact | conflict vs. temporal variation |
| `_doc_valid_from` | document | **the winner** |

A fact carrying its own dates (a `RATE_ENTRY`'s `effective_date`, tagged
`temporal: valid_from` in the schema) overrides the document's for the
fact-level axis only. In the vertical slice the two coincide; everywhere else
they do not, and conflating them would make the winner rule depend on whichever
schema happened to declare a date property.

### `kind` defaults to `occurrent` for an undeclared class

An extractor drifting off the enumerated class list produces a class the schema
never declared. Occurrent is the conservative default: two observations that
disagree raise a `:Conflict` rather than being silently recorded as history. A
missed conflict is invisible; a spurious one is reviewable.

### Conflict and temporal variation are independent, and both are recorded

Your call, after the live run surfaced it. The spec's table reads as a 2×2
because it describes *a pair* of observations; with three or more, a recurrent
property can be both at once.

The first implementation made them exclusive (`if/else`), and the live run
showed why that is wrong: `rate_value` across January (4.70, plus a
mis-extracted 5.25 that was really SmartSaver *Plus*), February (4.60) and
March (4.50) both varies over time and is disputed within January. Under
exclusivity the conflict won and `_temporal_props` lost `rate_value` entirely —
so a single bad extraction inside one document erased the property's whole
temporal history, and "does this rate change over time?" answered no.

Now: **"varies" is decided across instants, "conflicts" within one.** A
property lands in `_temporal_props` when a recurrent class takes different
values at different instants, and raises a `:Conflict` when any single instant
carries more than one value. Occurrent classes never get `_temporal_props` — a
completed event's attributes do not drift.

A conflicted property still gets the winner's value on the `:Entity` — a
resolvable answer beats no answer, and the `:Conflict` node carries the dispute
with `EVIDENCE` edges to every contributing observation.

### Four schemas were instructing the extractor to break the naming rule

Also your call, and the second half of the same live finding. `meta.yaml`'s
recurrent naming rule reaches the prompt now, but a class's own `guidance` is
rendered *after* it and gets the last word — and four recurrent classes were
using that last word to demand the opposite, with worked `RIGHT:` examples:

| Schema | Class | Was |
|---|---|---|
| `banking.reference` | `RATE_ENTRY` | *"names include product, tier, rate value, and effective date"* |
| `banking.reference` | `SEVERITY_LEVEL` | *"names include severity number and key threshold"* |
| `banking.products` | `INTEREST_RATE_TIER` | *"include the rate value AND the balance range in the name"* |
| `banking.risk_governance` | `RISK_METRIC` | *"names include both target and actual values"* |

Each now names the thing and puts the value in properties, with a RIGHT/WRONG
pair — models copy examples more readily than they follow prose.
`test/test_schema_naming_guidance.py` reads the shipped schemas and fails if the
contradiction returns, so a future schema edit is caught here rather than in a
corpus six weeks later. It found `INTEREST_RATE_TIER` and `SEVERITY_LEVEL`,
which a first hand-written scan had missed.

### Projection conflicts are marked `_source: 'projection'`

`artmind/conflicts.py` already owns a `:Conflict` label from a different
mechanism (LLM adjudication of entity *pairs*). The rebuild only ever deletes
conflicts it marked itself, so the pairwise adjudicator's nodes survive
untouched until Phase 6 retires them. There is a live test for this.

### The rebuild clears properties it no longer asserts

`SET e += $props` alone would leave a withdrawn property on the node forever,
and the projection would stop being a projection. The rebuild uses
`apoc.create.removeProperties` for everything outside the computed set, with
`embedding` and `embedding_stale` explicitly preserved.

### `update confirm`'s defect is closed structurally

The write is no longer "find the Entity and patch it", so there is no matching
step left to get wrong. A `link` resolution takes the **chosen node's**
identity and records the observation under that canonical name; the aggregate
key lands it on that entity by construction.

Entity-level supersession is **reported, not applied**. `Entity.superseded_by`
and `status='superseded'` are projection-owned now, so a chat setting them
would have them wiped by the next rebuild. The warning names the alternatives
(`docs retire`, or a same-as group in Phase 6). **This is a capability
regression and worth a decision in Phase 6** — see Open questions.

---

## Phase 5 work pulled forward: `docs retire` / `restore`

Not in the Phase 3 bullets, and pulled in deliberately after checking with you.

The reason is a gap that would otherwise open: superseding a document marked
`Document.valid_to` but did **not** demote its observations, so a superseded
document's entities would have stayed `latest` forever — reintroducing scorecard
row 3 (235 unretired orphans) in a new form the moment supersession's old
entity-layer code was removed.

`artmind/lifecycle.py` is the shared primitive; `apply_supersession` calls it.
Phase 5 still owns `archive`, `restore-from-archive`, `archived` and the
registry shrink.

Verified live, in sequence, on the three rate schedules:

| Action | Tier 2 rate | `_temporal_props` | entities |
|---|---|---|---|
| start | 4.50 | `[effective_date, rate_value]` | 3 |
| retire March | 4.60 | `[effective_date, rate_value]` | 3 |
| retire February | 4.70 | — | 3 |
| retire January | — | — | **0** |
| restore March | 4.50 | — | 3 |

The last two rows are the point: entities disappear because nothing asserts
them, and come back with the same deterministic ids.

---

## Bugs the gate caught

**1. Document dates never reached the observations.** The first live run
produced `{'_status': 'latest'}` and nothing else for all three documents —
every `_doc_valid_from` would have been null, every observation tied, and the
winner picked by dict iteration order. The gate would have passed or failed at
random.

The proximate cause was environmental (`load_schema` reads the run folder, and
this machine had no `~/.artmind`), but it exposed a real design consequence:
**the winner rule makes date lifting a hard dependency of the commit, not a
best-effort afterthought.** The old `normalize_ingested_document` hook ran
*after* the write and swallowed its own exceptions, so this exact failure was
invisible by construction. `_document_valid_time` now runs before observations
are built, and the slice script fails loudly with a pointer to `artmind init`
if the domain has no temporal mapping.

**2. A `MagicMock` session skips `execute_write` bodies entirely.** A rewritten
test in `test_update.py` passed while asserting on an empty list, because
`MagicMock().execute_write(fn)` returns a mock *without ever calling `fn`* — so
the whole transaction body silently did not run. This is the same class of trap
`CLAUDE.md` documents for `session.run`, and it is worse: with `run` you at
least get a truthy result, here you get no execution at all. Every fake session
in the suite now implements
`execute_write = lambda fn, *a, **k: fn(self, *a, **k)`.

**3. The live tests could not have reached AuraDB.** They opened a bare
`GraphDatabase.driver(uri)` with no auth, which only ever works against an
unauthenticated local instance. They now go through artmind's own
`neo4j_session()`, so whatever `ARTMIND_KG_NEO4J_*` points at — local, Docker,
Aura — is what they test, with the right scheme and credentials.

Routing through it exposed a second trap immediately: `conftest`'s autouse
`_no_live_neo4j` fixture replaces `graph_query.neo4j_session` with a null
session for every test, so the live module's first run reported all 14 tests
*skipped* with "APOC missing" — the null session answers `SHOW PROCEDURES` with
an empty list. The module now binds the real callable at import time, before
the per-test patch can reach it, and says why in a comment. The autouse guard
itself is right and stays.

Their cleanup was also unsafe against a real corpus: it deleted
`:Conflict` nodes matching `c.domain IS NULL`, which on a populated graph means
the pairwise adjudicator's own nodes. Now scoped to the test domain and a
module-specific `_test` tag.

**4. The gate's own invariant checks were scoped wrong**, and only a graph
holding the Phase 0 baseline could show it. Two checks — accreted `" | "`
descriptions, and un-embedded-but-unflagged entities — matched every
`:Entity` in `banking.reference`, including the pre-cutover entities written by
the old accretive upsert. On a fresh graph both read 0; on the real graph the
first reported 17 and failed the gate, while every assertion about the
projection itself passed.

The script's own `_clean` already had the right rule — it preserves entities
with no `key` precisely because they are the baseline the scorecard measures —
and the checks contradicted it. Both are now scoped to `e.key IS NOT NULL`, and
the legacy count is *reported* rather than judged: scorecard row 2 clears at the
Phase 8 re-ingest, not here. Verified both ways: seeding 17 legacy entities
reproduces the original failure and now passes with a note, while a *projected*
entity carrying `" | "` still fails the gate.

**5. A Phase 2 leftover made every vault-native metadata-only re-ingest fail,
and the failure was silently destructive.** `ingest_to_kg`'s back-compat branch
built `MARKDOWNS_DIR / f"{stem}.md"` by hand. Phase 2 stopped copying
vault-native markdown into the data dir — the vault file *is* the markdown, and
`markdown_path_for` exists to be the one place that knows — but this call site
was missed.

The chain on the live run: an unchanged vault file returns the `metadata_only`
tier with no `chunks_dir` → the back-compat branch → "Markdown not found" →
no observations written. The deferred full rebuild then found every key those
documents fed with zero `latest` observations and **correctly deleted 25
entities**. The projection behaved exactly as specified; the run emptied it and
refilled nothing.

Two guards, because the projection cannot defend against this and should not
try:

- the gate script now **aborts before the rebuild** if any document failed to
  commit, since a rebuild there is guaranteed destructive;
- and it resets `_content_sha256` on the vault files it is about to re-ingest.
  Deleting a document's observations while leaving frontmatter asserting the
  content is unchanged is an inconsistency the script itself created — the fast
  path is right to skip extraction when the graph *does* hold the prior
  version's observations.

**6. A leftover `:Conflict` broke test isolation** once the real
`observation_id`/`conflict_id` constraints were applied to the harness. The
fixture cleaned by domain, and pairwise conflicts carry none. Fixed by cleaning
`c.domain IS NULL` too — and the live fixture now applies the **real**
`_setup_neo4j` schema, so these tests run against production constraints rather
than a permissive subset.

---

## Exit gate

Run with:

```bash
python scripts/phase3_vertical_slice.py --fixtures   # deterministic half
python scripts/phase3_vertical_slice.py --full --vault ~/vault   # + both LLM steps
```

**Result (`--fixtures`, live against a real Neo4j 5.26): PASSED.**

```
PASS  exactly ONE :Entity for the aggregate key                     found 1
PASS  its id is the hash of the key
PASS  its name normalizes to the Tier 2 rate                        'SmartSaver Account Tier 2 Rate'
PASS  rate_value is 4.50 (March, the latest valid_from)             4.5
PASS  _temporal_props includes "rate_value"                         ['effective_date', 'rate_value']
PASS  three :Observation nodes behind it via AGGREGATES             rates=[4.5, 4.7, 4.6]
PASS  no :Conflict (the three windows are disjoint)
PASS  observations carry no :Entity and no class label              labels=['Observation']
PASS  no accreted ' | ' descriptions                                0 entities
PASS  no entity is both un-embedded and unflagged                   0 entities
```

`_temporal_props` correctly contains `effective_date` as well as `rate_value` —
both are scalars that differ across the three disjoint windows.

The fixture names are chosen so the canonicalization pass has to earn its place:
the key function alone maps January's name to `smartsaver account tier 2 rate`
but February's `"SmartSaver Tier 2 — 4.60% AER"` to `smartsaver tier 2`. Without
the pass they are two entities and the gate fails.

Also verified live, outside the gate script:

- **Re-ingest** of an unchanged document: entity count unchanged, no
  `elementId` churn, observations rewritten in place under their deterministic
  ids.
- **Retire/restore** — the table above.

### What the gate did *not* exercise here

`--fixtures` stubs **only the model call**. Real: the canonicalization pass
(`collect_names`, prompt assembly, the mapping fold), date lifting, observation
building, the observation write, the affected-key union, the rebuild, the GC,
the conflict/temporal decision, and the deferred directory rebuild.

Not exercised, because this machine has no reachable LLM or embedding service:

- **chunk extraction** — entities/properties/relationships from real text;
- **the vocabulary ANN's model half** — `retrieve_vocabulary` is unit-tested
  (recurrent-class filtering asserted on the parameters actually sent, and both
  failure paths), and the vector index round-trip was verified live, but no run
  has embedded a real entity and retrieved it;
- **the embed sweep end to end** — the query and flag-clearing are covered
  live; the embedding call itself is not.

`--full` covers all three on a machine with Ollama. **Run it before Phase 8.**

---

## Deferred, on purpose

| Deferred | To | Why |
|---|---|---|
| `projection status` and `:ProjectionState` | **Phase 6** | Drift detection hashes `same_as.yaml`, which does not exist until Phase 6. `projection rebuild` ships now because the deferred directory path needs it. |
| `projection synthesize`, `:Synthesis` nodes | **Phase 6** | The rebuild's **read** side is complete and tested (`_resolve_description`: current → use, grew → keep + `_description_stale`, shrank → discard). Nothing writes a synthesis yet. |
| Applying same-as groups during the rebuild | **Phase 6** | `affected_keys` already unions set 3, and `same_as.load_groups()` returns `[]`. The seam is tested; the merge-across-keys is not built. |
| `graph_snapshot.py` inversion | **Phase 5** | Stopgap applied: `Observation` added to `BASE_LABELS` so a snapshot round-trip is no longer destructive. The proper fix — export sources, rebuild on import — is Phase 5's. |
| `entity_history.py` and `query graph entity-versions` | **Phase 4** | No longer called from `commit_to_graph`. The module and its command go with the `:ObservationHistory` label swap. |
| `_neo4j_value`'s dict→JSON branch | **Phase 4** | Observation properties already flatten-or-drop (`flatten_domain_props`, with a warning). The branch survives only for **relationship** props, which Phase 4 rewrites when 249 types collapse to `RELATES_TO {rel_type}`. |
| `:DocChunkHistory` / `:ObservationHistory` label swaps | **Phase 4** | Phase 3 uses `_status` on the same label. Retired chunks are still deleted rather than relabelled, as before. |
| Consistent `_`-prefixing on `:Entity` | **Phase 4** | Observations use `_status`/`_valid_from`/`_kind` etc. The projection writes `_temporal_props`, `_kind`, `_observation_set_hash`, `_description_source` — but leaves `name`, `domain`, `entity_class`, `type`, `description` unprefixed, since the whole query layer reads them. |
| `ingest async` deferral | **Phase 4** | `worker.py` commits per file and never defers, so an async directory ingest rebuilds incrementally. Correct, just slower. |
| Binary-source identity | **Phase 5** | Unchanged from Phase 2. Both identity paths work: `doc_id` is `_artmind_id` for vault-native, `_resolve_doc_identity` for binaries, and neither reaches the projection — observations key off `doc_id` whatever produced it. |

---

## What later phases now inherit

**The rebuild is not optional and not best-effort.** If you add an operation
that dirties the projection, it must compute its affected keys and call
`projection.rebuild` **inside its own transaction**. Wrapping that call in a
`try/except` restores the exact failure mode this phase removed —
`test_ingest_hooks.py` has a test that parses the AST of `_commit_document_tx`
and fails if `projection` appears inside a `try`.

**Never write `:Entity` properties directly.** They are recomputed from
observations on every rebuild, and anything the merge did not produce is
actively removed. Two write paths learned this the hard way (`update.py`,
`apply_node_supersession`). Assert something new? Write an observation.

**Never null an embedding.** Set `embedding_stale = true` and let the sweep fix
it. A null is absent from `entity_embedding`, which makes the entity invisible
to `entity-resolve`'s vector leg — not merely less accurate.

**Entities are `MERGE`d on a deterministic id.** Never delete-and-recreate:
`elementId` churn breaks every external reference. A full rebuild from an empty
projection reproduces byte-identical ids, which is what makes Phase 8's cutover
safe and Phase 5's snapshot inversion possible.

**Test on parameters sent, not on counts.** And give every fake session an
`execute_write` that actually calls its argument.

---

## Open questions for later phases

1. **Entity-level supersession has no replacement.** `update confirm` can no
   longer express "this fact replaced that one" at the entity level. Retiring
   the source document is the closest equivalent and is often wrong (the older
   fact may share a document with facts that are still current). Phase 6 should
   decide whether a same-as group covers it, or whether observations need an
   explicit retraction mechanism.

2. **`banking.reference`'s RATE_ENTRY guidance still contradicts the recurrent
   naming rule** — it instructs the extractor to put the rate value and
   effective date *in the name*, with a worked example. The meta-schema's rule
   is now injected before it, so the class guidance gets the last word. The key
   function and the canonicalization pass both repair the damage, so this is a
   quality cost rather than a correctness one, but it makes the extractor work
   against itself. Per your Phase 1 scope call the 16 schemas were left alone;
   worth a sweep in Phase 7 alongside the skills.

3. **Scorecard row 12 (property-key hygiene) is unmeasured** on the new model.
   Row 3 (unretired orphans) is demonstrably 0 by construction, and row 2
   (accreted descriptions) is 0 by construction. The rest need the Phase 8
   re-ingest.
