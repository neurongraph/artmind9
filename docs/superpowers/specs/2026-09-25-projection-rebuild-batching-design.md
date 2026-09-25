# Projection rebuild: batch the Cypher, don't change the semantics

**Status:** Implemented
**Date:** 2026-09-25
**Owner:** Surjit Das
**Precedes:** a follow-on spec for a git-diff-driven `vault sync` (CDC-style) command,
which depends on this work to make large sync batches fast enough to be usable. That
spec is written separately and treats `projection.rebuild` as a black box — it does not
need this work to be *correct*, only to be *fast*.

## 1. Purpose

`artmind/projection.py`'s `rebuild(tx, keys, ...)` is the one place derived `:Entity`
nodes (and their `AGGREGATES`/`RELATES_TO`/`SAME_AS`/`CONFLICT_OF` edges) get built from
`:Observation` nodes already committed to Neo4j. It is called after every document
commit, after every `table2graph` run, and by `import_graph`'s full-rebuild fallback.

It is correct today — see §2 — but it is needlessly slow at scale, and that slowness is
about to matter more: a future `vault sync` command will call it with batches of
thousands of affected keys (a large `git pull` backlog, or a big `table2graph` run),
where today's implementation would take tens of minutes to hours.

**Goal:** cut round-trips per rebuilt key from ~6 to a small constant *per batch*
(independent of batch size), while producing **byte-identical graph state** to today's
implementation. This is a pure internal rewrite: `rebuild()`'s signature, its return
value shape, and its transactional contract with callers are unchanged.

## 2. Why it's slow today (not why you might think)

Read in full during design: `rebuild()`, `rebuild_key()`, `_write_conflicts()`,
`_sync_relates_to()`, `_plan_groups()` (`artmind/projection.py`).

**It is not an O(n²) algorithm.** `rebuild(tx, keys)` is a plain Python
`for key in sorted(keys): rebuild_key(...)` loop — one call per affected key, never a
pairwise comparison across keys. Every lookup inside `rebuild_key` is an indexed
point-operation:

- `MATCH (o:Observation {key: $key})` is backed by `CREATE INDEX observation_key ...
  FOR (n:Observation) ON (n.key)` (`setup.py:564`).
- `MERGE (e:Entity {_id: $id})` is backed by `CREATE CONSTRAINT entity_id ... FOR
  (n:Entity) REQUIRE n._id IS UNIQUE` (`setup.py:603`).

So the cost is O(K) in the number of affected keys, each doing O(1)-ish indexed graph
work. It is not proportional to total graph size.

**It is latency-bound.** `rebuild_key` issues roughly six separate Cypher statements per
key, sequentially, each a network round-trip to (in this deployment) a remote AuraDB
instance, all inside one Python loop inside one transaction:

1. (in `rebuild()`, before the per-key loop) clear stale `SAME_AS` edges — one `tx.run`
   per key that keeps its own entity this pass.
2. Entity `MERGE` + dynamic label (`SET e:$($label)`) + property set/removal.
3. Rewire `AGGREGATES` edges to the entity's current observations.
4. `_write_conflicts`: one clear + one write per conflict (usually 0-1, but still a
   round-trip floor).
5. `_sync_relates_to`: two deletes (outgoing/incoming `RELATES_TO`), then one `tx.run`
   **per distinct `(rel_type, other_key)` pair** the entity participates in — this is the
   least bounded of the six, since an entity can have many relationship types/targets.
6. (after the main loop) one `tx.run` per same-as `link` pair, to `MERGE` the `SAME_AS`
   edge.

The code already documents the real-world cost of this: `rebuild()`'s own progress-log
comment records "an 860-key rebuild against AuraDB went 8+ minutes with nothing printed"
— roughly 0.56s/key. Extrapolated linearly: 10,000 keys ≈ 90+ minutes; 100,000 ≈ 15+
hours. This is a WAN-round-trip cost, not a compute cost — more CPU doesn't help; fewer
round-trips does.

**Why this matters more now than it used to:** `rebuild()` runs entirely inside the
caller's one transaction, deliberately (`_commit_document_tx`'s own docstring: "a failure
anywhere fails the whole commit, deliberately"). A future large sync batch sitting inside
one multi-hour transaction risks AuraDB transaction-timeout limits and holds locks
against concurrent reads/writes for the whole duration. (Chunking *across* transactions
for very large batches — the way `table2graph.py`'s `REBUILD_BATCH = 400` already does,
for a different reason: Neo4j's transaction-memory cap — stays the caller's
responsibility and is explicitly **not** part of this spec; see §5.)

## 3. Design

Split `rebuild()`'s work, for a given batch of keys, into a pure-Python planning phase
(unchanged logic, just gathered across the whole batch instead of key-by-key) and a
batched-Cypher write phase (a small fixed number of `UNWIND`-based statements, each
covering every key in the batch in one round-trip).

### 3.1 Phase 1 — planning (Python, in-memory, no behavior change)

- **Batch-read observations**: replace the per-key `read_latest_observations` loop with
  one query: `UNWIND $keys AS key MATCH (o:Observation {key: key}) RETURN key,
  properties(o) AS p`, grouped back into per-key lists in Python.
- **`_plan_groups(keys, groups)`**: already computed once for the whole `keys` set today
  — no change needed.
- For each key (or same-as merge unit), run the existing pure functions exactly as
  today — `merge_observations`, conflict detection inside it — to produce one write-row:
  `{entity_id, label, props, observation_ids, conflicts}`.
- **Batch-read relationship rows**: `_relation_groups(tx, keys)` today takes the *whole*
  `keys` list already (called once per `rebuild_key` invocation with that key's
  `member_keys`, not per relationship) — extend it to accept the *entire batch's* key set
  in one call, grouping results back per-entity in Python, the same shape
  `_sync_relates_to` already builds today.

Output of phase 1: a flat list of write-rows, one per entity, plus a flat list of
resolved `RELATES_TO` rows across every entity in the batch, plus the existing `links`
list (same-as cross-domain pairs) — all pure Python, no I/O beyond the two batch reads
above.

### 3.2 Phase 2 — batched writes (Cypher, one `UNWIND` statement per concern)

Each of the following replaces a per-key loop with one statement parameterized by the
whole batch's rows:

1. **SAME_AS clear**: `UNWIND $ids AS id MATCH (e:Entity {_id: id}) OPTIONAL MATCH
   (e)-[r:SAME_AS]-(:Entity) DELETE r`.
2. **Entity MERGE + label + props**: `UNWIND $rows AS row MERGE (e:Entity {_id: row.id})
   ON CREATE SET e.embedding_stale = true WITH e, row, e.description AS prior_description
   SET e:$(row.label) ...` — see §4 for the one open question this statement raises.
3. **AGGREGATES rewire**: `UNWIND $rows AS row MATCH (e:Entity {_id: row.id}) OPTIONAL
   MATCH (e)-[r:AGGREGATES]->(:Observation) DELETE r WITH e, row UNWIND row.observation_ids
   AS oid MATCH (o:Observation {id: oid}) MERGE (e)-[:AGGREGATES]->(o)`.
4. **Conflicts**: one `UNWIND $ids AS id MATCH (c:Conflict {_source:'projection'})-
   [:CONFLICT_OF]->(:Entity {_id: id}) DETACH DELETE c` to clear, then one `UNWIND $rows
   AS row ...` over the flattened list of every conflict across every entity in the batch
   to (re)write them.
5. **RELATES_TO**: two `UNWIND $ids AS id MATCH (e:Entity {_id: id})-[r:RELATES_TO]-...
   DELETE r` (outgoing/incoming) to clear, then one `UNWIND $rows AS row ...` over the
   flattened, already-resolved list of every `(src, rel_type, tgt, props)` row across the
   batch to write them.
6. **SAME_AS links**: `UNWIND $rows AS row MATCH (m:Entity {_id: row.member}), (c:Entity
   {_id: row.canonical}) MERGE (m)-[:SAME_AS]->(c) MERGE (c)-[:SAME_AS]->(m)`.

Net: 6 statements total per batch (plus the 1 batched observation-read from phase 1),
regardless of whether the batch has 10 keys or several hundred — replacing what is today
roughly `6 × K` round-trips.

## 4. Open risk to spike before writing the rest

Statement 2 above needs a **per-row** dynamic label — `SET e:$(row.label)` where `label`
varies by row within one `UNWIND`, not a single top-level query parameter the way
today's single-key `SET e:$($label)` uses it. This needs to be confirmed against the
Neo4j version in use (5.24+ dynamic label/property syntax) before the rest of the
implementation proceeds, because it's the one statement where per-row variation inside
`UNWIND` is load-bearing rather than cosmetic.

**Fallback if unsupported:** group phase-1 rows by `label` first, and issue one `UNWIND`
per distinct label present in the batch instead of one for the whole batch. Still a large
win — a batch's distinct-label count is typically far smaller than K — just not a single
statement. The implementation plan should treat confirming this as its first task, before
any other phase-2 statement is written, since a negative answer changes the shape of
statement 2 (and only statement 2).

## 5. Non-goals

- **No change to cross-transaction chunking.** `table2graph.py`'s `REBUILD_BATCH = 400`
  (a transaction-*memory* cap workaround, not a round-trip workaround) stays exactly as
  it is, and is still the caller's responsibility. This spec only changes what happens
  *inside* one `rebuild(tx, keys)` call for whatever `keys` its caller hands it.
- **No feature flag.** Straight replacement of the implementation, gated on the test
  suite in §6 proving behavioral equivalence — consistent with this project's stated
  convention against compatibility shims (`CLAUDE.md`).
- **No changes to** `conflicts.py`'s LLM adjudication, `sameas propose`/`approve`, or
  `import_graph`'s rebuild-vs-trust drift decision. All of those call `rebuild()` /
  `full_rebuild()` as a black box and are unaffected by what happens inside it.
- **Does not build the `vault sync` command.** That's the follow-on spec this one
  unblocks; this spec's only externally-visible promise to it is "the same `rebuild(tx,
  keys)` call, much faster for large `keys`."

## 6. Testing

Per this codebase's existing testing philosophy (`CLAUDE.md`: assert on the parameters
actually sent and on which query ran, never on summary counts alone):

1. **Behavioral equivalence.** Every existing `projection.py` test keeps passing
   unchanged — same `Entity` properties, same `AGGREGATES`/`RELATES_TO`/`SAME_AS`/
   `CONFLICT_OF` edges as today's per-key implementation — covering same-as merges
   (same-class and cross-domain link cases), conflicting observations, GC/absent/deleted
   keys (an entity with zero remaining observations), and folded relationship endpoints
   (an edge whose other side was itself merged into a different canonical this same
   batch). No new test should be needed to describe *new* behavior, because there isn't
   any — only new tests asserting the *old* behavior still holds under the new
   implementation.
2. **Round-trip regression test.** A `_RecordingSession`-style test (mirroring
   `test/test_update.py`'s `run_side_effect` pattern) asserting that rebuilding N keys
   issues a bounded, small, non-N-scaling number of `tx.run` calls — so a future change
   can't silently reintroduce per-key chattiness without a test failing on it.

## 7. Rollout

Replace `rebuild()`'s internals directly once §4's spike is resolved and §6's tests pass.
No dark-launch, no flag, no dual-path — every caller (ordinary per-document commits,
`table2graph`, `import_graph`, manual `projection rebuild`) gets the faster path
immediately and identically.
