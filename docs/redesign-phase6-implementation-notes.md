# Phase 6 implementation notes

What actually landed for Phase 6 (curation: same-as groups, the proposer, and
synthesis), against the plan's bullets in
[redesign-phase-plan.md](./redesign-phase-plan.md) and the detailed scope
given at the start of the phase. Read
[projection-pipeline.md](./projection-pipeline.md) §2–3 and
[redesign-phase3-implementation-notes.md](./redesign-phase3-implementation-notes.md)
first — this phase inherits more from Phase 3 than from any other, and
assumes its model (observations, the projection, the pure/IO split in
`projection.py`) as background.

---

## What changed

### New modules

| Module | Holds |
|---|---|
| `artmind/sameas.py` | The same-as **proposal** review queue: `propose` / `list_proposals` / `get_proposal` / `approve` / `reject`. Never generates candidates itself. |
| `artmind/synthesize.py` | `projection synthesize` — replaces `consolidate.py` entirely (deleted). Entity + its observations, embed-before-write, applies its own result. |

### A. `same_as.yaml` and `sameas propose / list / approve / reject`

`same_as.py`'s `load_groups()` now parses a `canonical:` field per group (the
YAML gained this field; Phase 3 shipped the seam without it) and enforces
**`group[0]` is always the canonical member** — every consumer in this phase
relies on that ordering rather than carrying a second parallel value. New
`save_groups()` (round-trips the same shape) and `validate_groups()` (drops
overlap between groups deterministically, first-group-wins — **no
union-find**, matching the module's own "no closure" design).

`:SameAsProposal` is the review queue — a graph node, not a file, because a
proposal is pre-curation and doesn't belong in the curated file yet. Shape
mirrors `same_as.yaml`'s own (`canonical` + `members`, canonical included),
so `sameas approve` is a direct append to `same_as.yaml`, not a translation
step. `sameas.propose()`'s id is deterministic (`sha256(canonical +
sorted(members))`), so re-proposing an identical group `MERGE`s in place.

`sameas approve <id> [--canonical <key>]`: appends the group to
`same_as.yaml`, marks the proposal approved, and runs a **full** rebuild
scoped to the touched top-level domain families (not an incremental one —
see "Decisions taken" below for why). `sameas reject <id> [--reason]`: status
only, no graph write beyond the proposal node itself. `sameas list
[--status]`: renders the queue directly, matching the F bullet's "no
intermediate report file to drift from this command's own shape."

### B. Applying groups during the rebuild — the core of this phase

`projection.py` gained a planning layer above the existing per-key
`rebuild_key`:

- `_plan_groups(keys, groups)` (pure): for each group touching `keys`, splits
  members against the group's own canonical — never against each other, so a
  group stays a bounded, canonical-centric assertion. Same `(entity_class,
  domain)` as canonical → **merge unit**; different → **link**. Returns
  `unit_of` (raw key → the key its Entity is written under),
  `members_of` (canonical → every raw key folded into it), and `links`
  (`(member, canonical)` pairs to `SAME_AS`).
- `rebuild()` uses the plan: a folded (non-canonical merge) key's own Entity
  is **always** deleted (`_delete_entity`, `DETACH DELETE` — clears
  `AGGREGATES`, `RELATES_TO`, `SAME_AS`, everything, in one shot); a merge
  unit's canonical is rebuilt via `rebuild_key` with `member_keys` = every
  raw key in the unit, unioning `read_latest_observations` across all of
  them; a link member rebuilds normally (its own key, own Entity) and then
  gets a `SAME_AS` edge synced to its canonical.
- `SAME_AS` edges are cleared (for every key keeping its own Entity this
  pass) before being re-synced from `links` — the same delete-then-recreate
  idiom `_sync_relates_to` already used for `RELATES_TO`, so removing a group
  from `same_as.yaml` cleanly drops the edge on the next rebuild.
- `_sync_relates_to` and `_relation_groups` now take a **list** of raw keys
  (not one) and a `unit_of` resolver, so a `RELATES_TO` edge whose other
  endpoint was folded into some canonical resolves to *that* canonical's
  Entity, never to a since-deleted alias Entity — and a self-loop introduced
  purely by the merge (two aliases that happened to assert a relationship to
  each other) is dropped rather than written.

**A bug caught before any test ran, not by one**: `merge_observations`
recomputes the Entity's `_id`/`key` from the merged set's own `_choose_name`
(longest `canonical_name` wins). For a merge unit spanning two raw keys with
*different* names by definition, that heuristic can diverge from the group's
curated canonical — the Entity would end up written under the wrong id. Fixed
with `merge_observations(..., override_key=...)`: `rebuild_key` always passes
its own `key` (the canonical, for a merge unit) as the override, so identity
is never re-derived from content once a human has asserted it. Harmless for
an ordinary single-key rebuild (every observation in the set already shares
one stored key, so the override and the heuristic always agree there).
`test_projection_merge.py::test_override_key_wins_over_the_unioned_sets_own_name_choice`
pins this.

### C. One proposer, two outcomes

`conflicts.py`: `check_refine_precondition` and the `RefineRun` gate/writes
are deleted outright (it blocked conflict detection since day one — the live
corpus's `RefineRun` count was 0). `candidate_pairs`'s fetch and ANN queries
now also return each entity's `key` property, so a pair carries `key_a`/
`key_b` alongside the entity ids `materialize` already used. The adjudication
prompt already had a `"same_entity_consistent"` verdict that was silently
discarded before this phase; `materialize()` now turns it into a
`sameas.propose()` call instead (canonical = whichever side has the longer
name, a cheap default a human can override at approval time) — the other two
verdicts (`conflicting_claims`, `superseded`) are unchanged. `detect_conflicts`
now also captures `same_entity_consistent` into its `proposals` list and
reports materialized ids split into `report["conflicts"]` vs.
`report["same_as_proposals"]`.

Every adjudicator-produced `:Conflict` is now tagged `_source: 'adjudicator'`
on write (symmetric with the rebuild's own `_source: 'projection'` tagging
since Phase 3). A one-time backfill in `setup.py` (`MATCH (c:Conflict) WHERE
c._source IS NULL SET c._source = 'adjudicator'`) retags every pre-Phase-6
adjudicator conflict — the only other producer that ever existed — so "unify
on one :Conflict producer" means every `:Conflict` now carries a recognized
`_source`, not that old nodes were deleted.

`refine_graph.py`: `apply_merges`, `_merge_entity_pair`, `_record_refine_run`
and the destructive `apoc.refactor.mergeNodes` call are gone. The clustering
(`cluster_entities_by_class`, `cluster_entities`) and the merge-resolution
LLM prompt (`llm_merge_cluster`) survive unchanged as the **other** same-as
proposer — a different, cheaper candidate source than the ANN one (intra-class
name-similarity rather than cross-domain semantic neighbours), which is why
both survive per the phase plan's "refine_graph's clustering survives as
proposer." New `propose_merges()` groups the LLM's `{alias: canonical}`
output BY canonical (a whole cluster becomes one group proposal, not N
pairs), resolves each name to its aggregate key via a new `_entity_keys()`
helper, and calls `sameas.propose()` per canonical — landing in the **same**
queue the adjudicator feeds. `refine_graph()`'s orchestrator (`--dry-run`,
`--output`, `--from-file`) is otherwise unchanged; only the terminal "apply"
step changed from mutating the graph to writing proposals.

### D. `projection synthesize`

`artmind/synthesize.py` replaces `consolidate.py` (deleted), retargeted from
"entity + its chunks" to "entity + its `:Observation`s" per the phase's
naming ("consolidate" is retired — see `projection-pipeline.md` §3). No
HISTORICAL-marking prompt logic survives: `read_latest_observations` only
ever reads the `latest` label, so history is structurally excluded, not
filtered at prompt-build time.

`classify_key` selects candidates: skips an entity with an open **projection**
conflict, skips one below `--minObservations`, skips one whose current
`_observation_set_hash` already matches its `:Synthesis` node's recorded hash
(unless `--force`). `synthesize_key` is the write cycle: LLM call → embed
call → **one transaction** that `MERGE`s the `:Synthesis` node, calls
`projection.rebuild_key(tx, key, synthesis=...)` (reusing the tested
`_resolve_description` logic unchanged), then immediately overwrites
`e.embedding`/`e.embedding_stale = false` in the **same** transaction. The
embedding is computed **before** any write, in Python, outside any
transaction — so there is no readable window, ever, where the embedding is
null or even transiently stale (G1's invariant). If the embed service fails,
the whole entity is skipped and reported (`failed_embedding`); nothing is
written for it. `synthesize` (the domain-scoped orchestrator) mirrors
`consolidate_descriptions`'s old shape (`--nameFilter`, `--limit`, `--model`,
`--force`, `--dry-run`) so the CLI surface is familiar.

**`projection.load_synthesis(tx, key)` is the real `synthesis_loader`**,
threaded through **all six** existing `rebuild`/`full_rebuild` call sites —
`ingest.py` (×3: `_commit_document_tx`, both branches of `rebuild_projection`),
`archive.py`, `lifecycle.py`, `graph_snapshot.py`, `update.py`. None of them
passed a loader before this phase, which means a synthesis has never actually
survived a rebuild in practice until now — Phase 3 built and tested the read
side (`_resolve_description`) but nothing ever called it with real data. The
exit gate's "synthesize, then rebuild, and confirm the description survives"
check is what actually proves this wiring, not just the function's own unit
tests.

### E. `projection status` and `:ProjectionState`

A singleton (`id: 'singleton'`) recording `same_as.yaml`'s content hash
(existing `same_as.content_hash()`), a new `schema_set_hash()` (sha256 over
every `domains/schemas/*.yaml` file's name + content, sorted), and
`last_rebuilt_at`. `record_rebuild()` is called **only** from `full_rebuild`
when `domains is None` — a scoped, one-domain-family full rebuild (which
`sameas approve` and `docs/lifecycle` paths always are) cannot honestly claim
the *whole* projection has caught up with a *global* file like
`same_as.yaml`. `projection.status(tx)` is read-only (queries stay
`READ_ACCESS`; see `docs/projection-pipeline.md` §4 — "queries cannot
self-heal") and reports `same_as_drift`/`schema_drift` by comparing the
recorded hashes against the current ones. New CLI: `projection status`.

**Consequence, not a bug**: `sameas approve`/`reject` never clear the drift
flag by themselves (their rebuild is domain-scoped). See "Open questions."

### F. Deletions

`artmind/consolidate.py`, `artmind/skills/artmind-refine/scripts/summarize_gates.py`
(+ its one dead reference in `artmind-refine/SKILL.md`), `ingest
consolidate-descriptions` (CLI command + import), `refine_graph.apply_merges`
/ `_merge_entity_pair` / `_record_refine_run`, `conflicts.check_refine_precondition`,
every `RefineRun` read/write.

### G1. Never-null-an-embedding, upheld in the new write path

Covered under D above. Verified by grep at the end of this phase (see "Exit
gate"): the only two places in the whole codebase that `SET e.embedding =
...` are the existing embed sweep (`ingest.py`) and this phase's
`synthesize.py`, and both always pair it with a real, already-computed value
plus `embedding_stale = false` in the same statement — never a null, never a
window where the two properties disagree.

### G2. `lifecycle.resolve_document_id` widened

One-line fix, Phase 5's open question 4: `MATCH (d:Document)` →
`MATCH (d) WHERE (d:Document OR d:DocumentHistory)`, so `docs restore
--documentName <anything>` can resolve a document `restore-from-archive` just
placed in history — exactly the state a human would want to promote next.

### H. Entity/edge-level retraction (built, per your decision)

Phase 3's open question 1, closed as you suggested: a chat observation can
carry `_retracts: <observation_id>` (or an `ASSERTS_RELATION` edge id, for a
relationship fact). `observations.build_observation` gained a `retracts`
kwarg writing `_retracts` explicitly (like every other `_`-prefixed system
field); `_retracts` joins `projection._OBSERVATION_SYSTEM_KEYS` so it's never
mistaken for a domain property.

`projection.apply_retractions(tx, observations)` scans a batch of just-written
observations for `_retracts` pointers and, per target: if it names an
`Observation.id`, relabels that node to `:ObservationHistory` (same demotion
mechanism `lifecycle._transition` uses per-document, here scoped to one
fact — the retracted observation is never mutated, only removed from the
`latest` pool); if it names an `ASSERTS_RELATION.id`, deletes that edge
outright (edges carry no history label, and `RELATES_TO` is already
recomputed from scratch every rebuild, so deleting the raw edge is enough).
Tolerant of an unmatched target (log + skip), matching `same_as.py`'s own
"a missed merge is recoverable, a failed commit is not" philosophy. Returns
the retracted targets' own aggregate keys, which the caller unions into the
affected-key set — a retraction's target can belong to a different entity
than the retracting observation's own key.

Wired into both write paths that build observations: `ingest._commit_document_tx`
(step 4b, right after the observation write, before the relationship write)
and `update.write_user_chat` (inside the same transaction as the rebuild).
`update.py`'s dead `supersedes`-is-reported-not-applied code (a
Phase-3-era stopgap that logged a warning and did nothing) is gone; a
resolution's new `retracts` field becomes one thin observation per target,
under the **same** aggregate key as that resolution's own entity (never a
synthetic entity of its own) — `_retraction_target_ids()` normalizes the
field (bare id, list of ids, or the dict shape below) into a flat list.

`find_supersession_candidates` (the auto-detection heuristic surfaced via
`update draft`'s `supersession_candidates`) used to return an **entity**
`node_id` — the old, now-gone entity-level mechanism's target. It now walks
`AGGREGATES`/`ASSERTS_RELATION` to resolve down to the specific relationship
edge id(s) behind the aggregate `RELATES_TO`, since that is what `retracts`
can actually act on; a candidate entry gained `relation_observation_ids`.
`artmind-update/SKILL.md`'s worked example (§ "Step 2b") is rewritten to
match — `supersedes` → `retracts`, `nodes_superseded` → `nodes_retracted`.

**Deliberately narrow**: only the two cases the design named ("entity or an
edge") are covered. A pure property-level retraction ("this specific fact is
wrong, no replacement fact") has no automatic candidate detection — a human
finds the observation id by hand (e.g. via `query entity-history`, which
already returns `observation_id` per fact) and passes it directly. See "Open
questions."

---

## Decisions taken, and why

### `sameas approve` runs a full, domain-scoped rebuild — not an incremental one

`sameas approve`/`reject` are rare, human-triggered operations, not
per-document commits, so correctness was chosen over incrementality. An
incremental rebuild seeded from a hand-picked key set risks missing a group
member whose own document commit predates the group (nothing would have put
it in an affected-key set at approval time). `full_rebuild` scoped to the
group's touched top-level domain families is cheap enough at this
frequency and never misses a member.

### `_plan_groups` never chases transitive closure, even within one rebuild pass

Every split is against the group's own canonical, never member-to-member.
Confirmed correct even for a "mixed" group (some members merge, one links) in
`test_plan_groups_splits_a_mixed_group_by_member` — the merge and link
members are independent per-member decisions relative to canonical, not to
each other.

### `refine_graph.propose_merges` drops an ambiguous name rather than guessing

If a clustered name resolves to more than one `Entity.key` (the same string
legitimately denotes different things across domains/classes), the proposal
for it is skipped (`skipped_ambiguous`) rather than picking one. A dropped
proposal is a missed merge — visible, recoverable, and the human can always
propose it by hand; a wrong guess baked into `same_as.yaml` is a curation
error with no comparably easy fix.

---

## Bugs the gate caught

**None from the live run itself** — every live check passed on the first
run once the one design bug (`override_key`, caught during implementation
before any test executed — see "What changed", B) was already fixed. This
phase's live testing was a single focused session, not the multi-day
iteration Phases 3–5 report; see "What this phase's exit gate did and did
not exercise" below for the corresponding gap in coverage.

**One test-script miscalibration, not a code defect**, worth recording so it
isn't mistaken for one: the live gate's cross-domain LINK check asserted
"exactly two entities exist in these two domains," which failed because an
earlier step in the *same script* had left a third, unrelated entity (the
non-canonical member from the merge/un-merge check, still present after
being un-merged back to its own id) sharing one of the two domains. The
underlying mechanism was correct — verified by reading the actual row data
the failing assertion printed: the `SAME_AS` edge existed between exactly the
intended pair (`count=1`), and both entities' `_domain` values were plain
scalar strings, not lists, exactly as required. Fixed by narrowing what the
assertion checks; not re-run (would have cost another LLM+embedding round
trip for no new signal — the data already visible in the first run settles
it).

---

## Exit gate

`just dev-test`: **1653 passed, 14 skipped, 0 failed** (baseline before this
phase: 1612). New test files: `test_same_as.py`, `test_sameas.py`,
`test_synthesize.py` (replacing `test_consolidate.py`),
`test_projection_group_rebuild.py`, `test_lifecycle.py`; additions to
`test_projection_merge.py` (`override_key`, `_plan_groups`),
`test_conflicts.py` (RefineRun-is-gone), `test_domain_family.py`
(precondition-check removal), `test_update.py` (retraction replaces the dead
supersedes-reporting test).

Live, against real AuraDB (`ARTMIND_NO_PROXY=1`), after `just dev-stop-daemons
&& just dev-install && artmind setup` (the new constraints — `synthesis_id`,
`sameas_proposal_id`, plus the `sameas_proposal_status`/`projection_state_id`
indexes — applied cleanly against the existing production graph). Throwaway
domains (`zzztest6.reference`, `zzztest6.governance`), observations written
directly (not through full document ingest — see below), real
`qwen3.6:35b-mlx` for synthesis and real `nomic-embed-text` for the
embedding; `same_as.yaml` did not exist before this run and was removed
after. Cleaned up in full (verified: 0 leftover `zzztest6`/`Synthesis` nodes,
0 `ProjectionState` nodes, `same_as.yaml` absent again).

```
PASS  two separate entities before any group exists
PASS  same (class,domain) group -> ONE Entity, keyed on canonical
PASS  delete group, rebuild -> original two entities return with original ids
PASS  SAME_AS edge present between exactly the intended cross-domain pair
      (both entities' _domain confirmed scalar strings in the raw row dump)
PASS  projection synthesize -> :Synthesis node written
PASS  Entity.description updated, _description_source = synthesis
PASS  non-null embedding present, embedding_stale = false
PASS  projection rebuild afterward -> synthesized description SURVIVES
      (proves the :Synthesis store is a real input, not a side effect)
PASS  projection status: no drift immediately after record_rebuild
PASS  projection status: reports drift after hand-editing same_as.yaml
```

**Confirmed no code path nulls an embedding**: `grep -rn "embedding = null\|
embedding: None\|embedding=None" artmind/*.py` returns nothing; the only two
statements anywhere that `SET e.embedding = $embedding` are the existing
embed sweep (`ingest.py`, pre-Phase-6) and `synthesize.py`'s new write, both
always alongside a real computed value and `embedding_stale = false` in the
same statement.

### What this phase's exit gate did and did not exercise

The hermetic suite verifies every pure decision (`_plan_groups`,
`merge_observations` with `override_key`, `same_as.py`'s load/save/validate,
`classify_key`, the retraction-target normalization) and every I/O shape
(which Cypher runs, with which parameters, via the dispatch-based `FakeTx` in
`test_projection_group_rebuild.py` and the recording-`MagicMock` pattern
elsewhere) — per CLAUDE.md, never on summary counts, never trusting a bare
`MagicMock`'s truthy-for-anything default.

**Not exercised live**, unlike Phases 3–5's gates:

- A full document going through real extraction and landing observations
  that then get same-as'd — this run wrote `:Observation` nodes directly via
  Cypher rather than through `ingest_to_kg`, to keep the session to one
  focused pass rather than a multi-day iteration. The group-merge/link
  mechanism itself is exactly the same code path either way (`rebuild()`
  doesn't know or care how its observations arrived), but the interaction
  with real chunk extraction, canonicalization, and `_write_relation_observations`
  was not re-proven this phase.
- `sameas propose` against the real cross-domain adjudicator (an actual LLM
  `same_entity_consistent` verdict producing a real `:SameAsProposal`), and
  `ingest refine-graph`'s clustering → `propose_merges` path end-to-end.
  Both are covered by hermetic tests asserting the *shape* of what would be
  sent/written; neither has produced a real proposal from a real model
  response yet.
- `sameas approve`'s own function against live AuraDB (its logic is unit
  tested with a mocked session in `test_sameas.py`; the live gate exercised
  the same underlying `projection.full_rebuild` call directly, not through
  `sameas.approve()` itself).
- Entity/edge retraction (H) end-to-end through a real `update confirm` call
  against live Neo4j — covered by `test_update.py`'s observation-building
  test and `test_projection_group_rebuild.py`'s `apply_retractions` tests
  separately, not chained together live.
- A snapshot `curation`/`graph` export+restore round-trip touching the new
  `:SameAsProposal`/`:ProjectionState` labels. `:Synthesis` was already in
  `BASE_LABELS` (Phase 5, pre-emptive) and is unaffected by this phase.
  `:SameAsProposal` and `:ProjectionState` are deliberately **not** added to
  `BASE_LABELS` — pre-curation working state and derived drift-tracking
  state, respectively, same category as `:Conflict`/`:Entity` which are
  already excluded (sources-only snapshot).

If any of the above turns out to matter in practice, it's worth a dedicated
live pass before Phase 8's cutover.

---

## Deferred, on purpose

| Deferred | To | Why |
|---|---|---|
| Full rewrite of `artmind-refine` → `artmind-curate`, and the rest of `artmind-query`/`artmind-update`'s surrounding narrative | **Phase 7** | Already the plan's assignment. This phase patched only what would now hard-fail or actively mislead if followed literally (the retraction section of `artmind-update/SKILL.md`; `summarize_gates.py`'s one dead reference) — not a full pass. |
| `CAPABILITIES.md` restructure | **Phase 7** | Already the plan's assignment ("restructured around sources → observations → projection → query"). Only row 8.3, which this phase's own change directly falsified, was patched here. |
| justfile-wide drift audit | **Phase 7** | `ingest-refine-pipeline`/`ingest-normalize-time` reference two commands Phase 3 already deleted — pre-existing, not introduced here. Commented out with a note (not silently left runnable) since I was already touching adjacent recipes; a full sweep is Phase 7's job per the docs-drift precedent Phase 4 set. |
| Property-level retraction candidate detection | Not currently planned | H's design named "entity or an edge"; a bare fact retraction with no automatic surfacing still works (pass an observation id by hand), it just has no `update draft`-side heuristic pointing at it yet. Revisit if this turns out to be a common need. |
| A live pass covering the gaps listed under "What this phase's exit gate did and did not exercise" | **Before Phase 8** if it matters | See that section for the specific list. |

---

## Open questions for later phases

1. **`sameas approve`/`reject` never clear `:ProjectionState`'s drift flag.**
   Deliberate (their rebuild is domain-scoped, and `same_as.yaml`/the schema
   set are both global signals a partial rebuild can't honestly clear), but
   it means every `sameas approve` leaves `projection status` reporting
   `same_as_drift: true` until a bare `projection rebuild` runs afterward.
   Workable, but easy to miss — Phase 7's skill rewrite should document the
   expected two-step workflow explicitly rather than leaving it implicit in
   this file.

2. **`refine_graph.propose_merges`'s `skipped_ambiguous` path has no
   disambiguation, unlike the cross-domain guard a few lines away.** A
   clustered name resolving to more than one `Entity.key` is dropped outright
   rather than narrowed by domain/class the way `_entity_domains`/
   `_entity_classes` already do for `allow_cross_domain_merge`. Not a
   correctness bug — see "Decisions taken" — but worth revisiting if this
   count turns out to be material on the real corpus; the signal to watch is
   `propose_merges`'s own `stats["skipped_ambiguous"]`.

3. **Live coverage gaps** — see "What this phase's exit gate did and did not
   exercise" above for the itemized list (full-ingest-to-same-as chaining,
   `sameas propose`/`sameas approve` against a real live LLM+AuraDB run,
   retraction through a real `update confirm`, and a snapshot round-trip
   touching the new labels). None of these are believed to be at risk — the
   underlying mechanisms are shared with code paths that *were* exercised
   live this phase or in prior phases — but none of them were proven this
   session either.

4. **Phase 3's open question 2** (property hints across the 14
   never-audited schemas) and **question 3** (scorecard row 12) are both
   still open and unrelated to this phase's scope — restated here only so
   the pointer isn't lost, per Phase 3's own notes' instruction.
