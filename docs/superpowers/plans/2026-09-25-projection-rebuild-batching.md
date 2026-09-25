# Projection Rebuild Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut `projection.rebuild(tx, keys)`'s Cypher round-trips from ~6 per key to a
small constant per batch, by replacing its per-key `for key in keys: rebuild_key(...)`
loop with a two-phase (pure-Python planning, then batched `UNWIND` writes) rewrite —
producing byte-identical graph state to today's implementation.

**Architecture:** `artmind/projection.py` gains a set of new private `_*_batch` helpers,
each a batched sibling of an existing per-key Cypher statement (entity merge, AGGREGATES
rewire, conflict write, RELATES_TO sync, SAME_AS clear/link). `rebuild()`'s body is
rewritten to: (1) read every affected key's observations in one round-trip, (2) run the
existing pure `merge_observations`/`_plan_groups` logic unchanged, (3) issue the batched
writes. `rebuild_key(tx, key, ...)` — the single-key entry point `synthesize.py` calls
directly — is left **completely untouched**: it's a different caller with a different
shape (one key, no benefit from batching), and touching it would be scope creep this
spec explicitly doesn't need. `read_latest_observations`, `merge_observations`,
`affected_keys`, `_plan_groups`, `_group_relations`, and every function in
`test_projection_merge.py`'s test surface are pure and require zero changes.

**Tech Stack:** Python 3.14, Neo4j 5.24+ (`CYPHER 25` dynamic label/property syntax),
pytest, `uv`.

**Spike already run and confirmed (this session, against a throwaway local Neo4j):**
`UNWIND $rows AS row MERGE (n:Label {_id: row.id}) SET n:$(row.label)` correctly applies
a *different* dynamic label per row within one statement — the one open risk the design
spec flagged is resolved. No label-grouping fallback is needed anywhere in this plan.

**Read before starting:**
[docs/superpowers/specs/2026-09-25-projection-rebuild-batching-design.md](../specs/2026-09-25-projection-rebuild-batching-design.md)
— the full design and rationale. `CLAUDE.md`'s testing section, specifically: assert on
the parameters actually sent and which query ran, never on summary counts alone.

**Not in scope:** `rebuild_key`, `read_latest_observations`, `apply_retractions`, and
`synthesize.py`'s call path — none of these change. Cross-transaction chunking (how many
keys go into one `rebuild()` call before a caller starts a new transaction) is unchanged;
that stays the caller's job (e.g. `table2graph.py`'s `REBUILD_BATCH`).

---

## File Structure

| File | Responsibility |
|---|---|
| `artmind/projection.py` (modify) | Add `_read_observations_for_keys`, `_entity_ids_that_exist`, `_delete_entities_batch`, `_clear_same_as_batch`, `_merge_entities_batch`, `_rewire_aggregates_batch`, `_write_conflicts_batch`, `_relation_groups_batch`, `_delete_relates_to_batch`, `_write_relates_to_batch`, `_sync_same_as_links_batch`. Rewrite `rebuild()`'s body to use them. |
| `test/test_projection_group_rebuild.py` (modify) | Extend `FakeTx` to answer the new batched query shapes. Rewrite the label/merge/link/delete tests (lines 75-236 today) for the batched call shape. Remove the two now-obsolete per-key progress-logging tests. Add a round-trip-count regression test. `apply_retractions` tests (lines 241-279) are untouched — that function isn't part of this rewrite. |
| `test/test_projection_live.py` (verify only, no edits expected) | The real-Neo4j behavioral-equivalence suite. Every existing test should keep passing unchanged — that's the proof this rewrite produces the same graph state as today. |

---

## Task 1: Write the new test suite first (red)

**Files:**
- Modify: `test/test_projection_group_rebuild.py`

This task only changes the test file, against the **current, unmodified**
`projection.py` — every new/changed assertion here is expected to fail until Task 2 lands.

- [ ] **Step 1: Extend `FakeTx` to answer the batched query shapes**

Replace the whole `FakeTx` class (lines 28-59 today) with this extended version, which
answers both the old single-key shape (for tests you're not touching, if any survive —
none do after this task, but keeping both shapes costs nothing) and the new batched
shapes:

```python
class FakeTx:
    def __init__(self, observations_by_key=None, entity_exists=None, observation_keys=None):
        self.observations_by_key = observations_by_key or {}
        self.entity_exists = entity_exists or set()
        # id -> key, for a retraction target's `RETURN o.key AS key`
        self.observation_keys = observation_keys or {}
        self.calls: list[tuple[str, dict]] = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        cy = " ".join(cypher.split())

        if "UNWIND $keys AS key MATCH (o:Observation {key: key}) RETURN key, properties(o) AS p" in cy:
            rows = [
                {"key": k, "p": o}
                for k in params["keys"]
                for o in self.observations_by_key.get(k, [])
            ]
            return _Result(rows)

        if "UNWIND $ids AS id MATCH (e:Entity {_id: id}) RETURN id" in cy:
            rows = [{"id": i} for i in params["ids"] if i in self.entity_exists]
            return _Result(rows)

        if cy.startswith("UNWIND $keys AS key") and "ASSERTS_RELATION]->(t:Observation)" in cy:
            return _Result([])  # no relationships in these fixtures
        if cy.startswith("UNWIND $keys AS key") and "ASSERTS_RELATION]->(o:Observation {key: key})" in cy:
            return _Result([])

        if cy.startswith("MATCH (o:Observation {id: $id})") and "ObservationHistory" in cy:
            target_key = self.observation_keys.get(params["id"])
            if target_key is None:
                return _Result(single_row=None)
            return _Result(single_row={"key": target_key})

        if "ASSERTS_RELATION {id: $id}]->() RETURN count(r) AS n" in cy:
            return _Result(single_row={"n": 0})

        return _Result()

    def calls_matching(self, needle: str):
        return [(cy, p) for cy, p in self.calls if needle in " ".join(cy.split())]
```

- [ ] **Step 2: Rewrite the dynamic-label test for the batched shape**

Replace `test_rebuild_key_uses_dynamic_label_and_property_cypher_not_apoc` (lines 75-99
today) with:

```python
def test_rebuild_merges_entities_via_one_batched_unwind_statement():
    """`SET e:$(row.label)` per row inside one UNWIND (Neo4j 5.24+) replaced
    the old apoc.create.addLabels/removeProperties pair AND the old
    one-call-per-key MERGE — assert the query text and the bound per-row
    `label`/`keep` values inside the single `rows` list, not a per-key call."""
    key = ("fca", "REGULATOR", "banking.reference")
    tx = FakeTx(observations_by_key={
        key_string(key): [_o(id="o1", canonical_name="FCA")],
    })

    rebuild(tx, {key}, same_as_groups=[])

    merge_calls = tx.calls_matching("MERGE (e:Entity {_id: row.id})")
    assert len(merge_calls) == 1, "one batched call, not one per key"
    cypher, params = merge_calls[0]

    assert "apoc.create.addLabels" not in cypher
    assert "apoc.create.removeProperties" not in cypher
    assert "SET e:$(row.label)" in cypher
    assert "REMOVE node[staleKey]" in cypher

    assert len(params["rows"]) == 1
    row = params["rows"][0]
    assert row["label"] == "REGULATOR"
    assert row["id"] == entity_id(key)
    assert "embedding" in row["keep"], "keep must protect embedding from the property sweep"
    assert "embedding_stale" in row["keep"]
```

- [ ] **Step 3: Replace the two obsolete per-key progress-logging tests**

Delete `test_rebuild_logs_progress_past_fifty_keys` and
`test_rebuild_stays_quiet_under_the_progress_threshold` (lines 110-145 today) along with
the comment block above them (lines 102-108) explaining the old per-key heartbeat. That
heartbeat existed because a per-key round trip against remote AuraDB was slow enough
(~0.5s/key, measured) that a 60+ key rebuild could run silently for minutes. Batching
removes the per-key round trip entirely — there's no longer a "still working, not stuck"
gap to bridge with a checkpoint log line, because the whole batch now costs a small
constant number of round trips regardless of key count.

Replace that whole block with a regression test proving the thing that made the old
heartbeat necessary can't silently come back — a bounded, non-scaling call count for a
large batch:

```python
# ── round-trip count: must stay flat as the batch grows, not scale with K ──
# This is the property the whole rewrite exists for. A regression back to a
# per-key call anywhere in `rebuild` would make this test fail long before
# anyone notices an 8-minute rebuild against AuraDB again.


def test_rebuild_call_count_does_not_scale_with_key_count():
    small_keys = {("entity", "REGULATOR", f"banking.d{i}") for i in range(2)}
    large_keys = {("entity", "REGULATOR", f"banking.d{i}") for i in range(200)}

    small_tx = FakeTx(observations_by_key={
        key_string(k): [_o(id=f"o{i}", canonical_name="entity", _domain=k[2])]
        for i, k in enumerate(small_keys)
    })
    large_tx = FakeTx(observations_by_key={
        key_string(k): [_o(id=f"o{i}", canonical_name="entity", _domain=k[2])]
        for i, k in enumerate(large_keys)
    })

    rebuild(small_tx, small_keys, same_as_groups=[])
    rebuild(large_tx, large_keys, same_as_groups=[])

    # 100x the keys must not mean anywhere near 100x the round trips.
    assert len(large_tx.calls) <= len(small_tx.calls) + 2, (
        f"call count scaled with key count: {len(small_tx.calls)} calls for "
        f"{len(small_keys)} keys vs {len(large_tx.calls)} calls for {len(large_keys)} keys"
    )
```

- [ ] **Step 4: Rewrite the merge/link/delete tests for the batched call shape**

Replace `test_merge_unions_both_members_observations_into_the_canonical_entity` through
`test_link_clears_stale_same_as_edges_before_resyncing` (lines 151-236 today) with:

```python
def test_merge_unions_both_members_observations_into_the_canonical_entity():
    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("financial conduct authority", "REGULATOR", "banking.reference")
    tx = FakeTx(observations_by_key={
        key_string(canonical): [_o(id="o1", canonical_name="FCA", scope="banking")],
        key_string(member): [_o(id="o2", canonical_name="Financial Conduct Authority", scope="uk")],
    })

    rebuild(tx, {canonical, member}, same_as_groups=[[canonical, member]])

    merge_calls = tx.calls_matching("MERGE (e:Entity {_id: row.id})")
    assert len(merge_calls) == 1
    _, params = merge_calls[0]
    assert len(params["rows"]) == 1, "ONE entity written, not two"
    row = params["rows"][0]
    assert row["id"] == entity_id(canonical)
    assert row["props"]["_id"] == entity_id(canonical)


def test_merge_deletes_the_folded_members_own_entity():
    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("financial conduct authority", "REGULATOR", "banking.reference")
    tx = FakeTx(observations_by_key={
        key_string(canonical): [_o(id="o1")],
        key_string(member): [_o(id="o2")],
    })

    rebuild(tx, {canonical, member}, same_as_groups=[[canonical, member]])

    detach_deletes = tx.calls_matching("DETACH DELETE c, e")
    deleted_ids = {i for _, p in detach_deletes for i in p.get("ids", [])}
    assert entity_id(member) in deleted_ids
    # the canonical's own id must NEVER be deleted by the same pass
    assert entity_id(canonical) not in deleted_ids


def test_removing_the_group_lets_both_keys_rebuild_independently():
    """The un-merge gate: with no group, each key gets its OWN entity at its
    OWN deterministic id -- proving a merge never mutates what a standalone
    rebuild of either key would produce."""
    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("financial conduct authority", "REGULATOR", "banking.reference")
    tx = FakeTx(observations_by_key={
        key_string(canonical): [_o(id="o1")],
        key_string(member): [_o(id="o2")],
    })

    rebuild(tx, {canonical, member}, same_as_groups=[])  # group removed

    merge_calls = tx.calls_matching("MERGE (e:Entity {_id: row.id})")
    assert len(merge_calls) == 1, "still one batched call"
    ids = {row["id"] for row in merge_calls[0][1]["rows"]}
    assert ids == {entity_id(canonical), entity_id(member)}


def test_link_keeps_both_entities_and_syncs_same_as_both_directions():
    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("fca", "AUTHORITY", "banking.risk_governance")  # different class+domain
    tx = FakeTx(observations_by_key={
        key_string(canonical): [_o(id="o1", entity_class="REGULATOR", domain="banking.reference")],
        key_string(member): [_o(id="o2", entity_class="AUTHORITY", domain="banking.risk_governance")],
    })

    rebuild(tx, {canonical, member}, same_as_groups=[[canonical, member]])

    merge_calls = tx.calls_matching("MERGE (e:Entity {_id: row.id})")
    ids = {row["id"] for row in merge_calls[0][1]["rows"]}
    assert ids == {entity_id(canonical), entity_id(member)}, "LINK keeps both entities -- no fold"

    same_as_calls = tx.calls_matching("MERGE (m)-[:SAME_AS]->(c)")
    assert len(same_as_calls) == 1
    _, params = same_as_calls[0]
    assert len(params["rows"]) == 1
    assert params["rows"][0]["member"] == entity_id(member)
    assert params["rows"][0]["canonical"] == entity_id(canonical)


def test_link_clears_stale_same_as_edges_before_resyncing():
    canonical = ("fca", "REGULATOR", "banking.reference")
    tx = FakeTx(observations_by_key={key_string(canonical): [_o(id="o1")]})

    # No group this pass -- any previously-linked SAME_AS edge must be cleared.
    rebuild(tx, {canonical}, same_as_groups=[])

    clears = tx.calls_matching("OPTIONAL MATCH (e)-[r:SAME_AS]-(:Entity) DELETE r")
    assert len(clears) == 1, "one batched clear, not one per key"
    assert entity_id(canonical) in clears[0][1]["ids"]
```

- [ ] **Step 5: Run the suite and confirm the new/changed tests fail**

Run: `uv run --group dev pytest test/test_projection_group_rebuild.py -v`

Expected: every test from Steps 2-4 FAILS (the old `rebuild()` still issues one call per
key with `{_id: $id}`-shaped single-row Cypher, not the batched `{_id: row.id}`/`rows`
shape these tests now expect). `test_rebuild_call_count_does_not_scale_with_key_count`
should also fail, since today's call count scales linearly with key count. The four
`apply_retractions` tests at the bottom of the file are untouched and should still PASS.

- [ ] **Step 6: Commit the test changes on their own**

```bash
git add test/test_projection_group_rebuild.py
git commit -m "$(cat <<'EOF'
test(projection): assert batched rebuild call shape, not per-key

Rewrites the FakeTx-based rebuild tests for the UNWIND-batched Entity
merge/delete/same-as calls the next commit introduces, and replaces the
two per-key progress-heartbeat tests with a round-trip-count regression
test -- the heartbeat's premise (a slow per-key round trip worth
narrating) goes away once rebuild is batched.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement the batched rebuild (green)

**Files:**
- Modify: `artmind/projection.py:479-1027` (from `read_latest_observations` through the
  end of `rebuild()`)

- [ ] **Step 1: Add the batched observation-read and existence-check helpers**

Insert these two functions immediately after `read_latest_observations` (after line 491
today, before `_parse_key`):

```python
def _read_observations_for_keys(tx, keys: set[tuple[str, str, str]]) -> dict[str, list[dict]]:
    """Batch-read every `latest` observation for a whole set of aggregate
    keys in one round-trip, keyed by `key_string`. The batched sibling of
    `read_latest_observations`, used only by `rebuild`'s multi-key path --
    `read_latest_observations` itself stays a single-key function for
    `synthesize.py`'s own per-entity call, which gets no benefit from
    batching a single key.
    """
    if not keys:
        return {}
    key_strings = [key_string(k) for k in keys]
    rows = tx.run(
        "UNWIND $keys AS key MATCH (o:Observation {key: key}) RETURN key, properties(o) AS p",
        keys=key_strings,
    ).data()
    by_key: dict[str, list[dict]] = {k: [] for k in key_strings}
    for row in rows:
        by_key[row["key"]].append(row["p"])
    return by_key


def _entity_ids_that_exist(tx, ids: list[str]) -> set[str]:
    """Which of `ids` currently have an `:Entity` node -- batched sibling of
    `rebuild_key`'s single-id existence check, used to decide `deleted` vs
    `absent` for every zero-observation key in one round-trip instead of one
    per key."""
    if not ids:
        return set()
    rows = tx.run(
        "UNWIND $ids AS id MATCH (e:Entity {_id: id}) RETURN id", ids=ids
    ).data()
    return {row["id"] for row in rows}
```

- [ ] **Step 2: Add the batched delete and same-as-clear helpers**

Insert immediately after `_delete_entity` (after line 522 today, before `_write_conflicts`):

```python
def _delete_entities_batch(tx, ids: list[str]) -> None:
    """Batched sibling of `_delete_entity` -- one DETACH DELETE covering
    every id in `ids`, whether it's a same-as-folded key or a key with zero
    remaining observations."""
    if not ids:
        return
    tx.run(
        """
        UNWIND $ids AS id
        MATCH (e:Entity {_id: id})
        OPTIONAL MATCH (c:Conflict {_source: 'projection'})-[:CONFLICT_OF]->(e)
        DETACH DELETE c, e
        """,
        ids=ids,
    )


def _clear_same_as_batch(tx, ids: list[str]) -> None:
    """Batched sibling of `rebuild`'s old per-key SAME_AS-clear loop."""
    if not ids:
        return
    tx.run(
        "UNWIND $ids AS id MATCH (e:Entity {_id: id}) OPTIONAL MATCH (e)-[r:SAME_AS]-(:Entity) DELETE r",
        ids=ids,
    )
```

- [ ] **Step 3: Add the batched entity-merge and AGGREGATES-rewire helpers**

Insert immediately after the new `_clear_same_as_batch`, before `_write_conflicts`:

```python
def _merge_entities_batch(tx, rows: list[dict]) -> None:
    """Batched sibling of `rebuild_key`'s Entity MERGE/label/property-sweep
    Cypher. One statement covering every entity in `rows`, each carrying its
    own `label` -- Neo4j 5.24+'s dynamic-label syntax accepts a per-row
    expression under UNWIND (confirmed live before writing this), not just a
    single top-level parameter.

    `rows`: `[{"id": eid, "label": str, "keep": [str, ...], "props": dict,
    "description": str | None}, ...]`.
    """
    if not rows:
        return
    tx.run(
        """
        CYPHER 25
        UNWIND $rows AS row
        MERGE (e:Entity {_id: row.id})
        ON CREATE SET e.embedding_stale = true
        WITH e, row, e.description AS prior_description
        SET e:$(row.label)
        WITH e AS node, row, prior_description
        FOREACH (staleKey IN [k IN keys(node) WHERE NOT k IN row.keep] | REMOVE node[staleKey])
        SET node += row.props
        SET node.embedding_stale = CASE
              WHEN node.embedding IS NULL THEN true
              WHEN prior_description IS NULL AND row.description IS NULL THEN coalesce(node.embedding_stale, false)
              WHEN prior_description IS NULL OR prior_description <> row.description THEN true
              ELSE coalesce(node.embedding_stale, false)
            END
        """,
        rows=rows,
    )


def _rewire_aggregates_batch(tx, rows: list[dict]) -> None:
    """Batched sibling of `rebuild_key`'s AGGREGATES-rewire Cypher.

    `rows`: `[{"id": eid, "observation_ids": [str, ...]}, ...]`.
    """
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (e:Entity {_id: row.id})
        OPTIONAL MATCH (e)-[r:AGGREGATES]->(:Observation)
        DELETE r
        WITH e, row
        UNWIND row.observation_ids AS oid
        MATCH (o:Observation {id: oid})
        MERGE (e)-[:AGGREGATES]->(o)
        """,
        rows=rows,
    )
```

- [ ] **Step 4: Add the batched conflict-writing helper, alongside the existing one**

`_write_conflicts` (lines 525-570 today) is **not removed or modified** — `rebuild_key`
still calls it, and `rebuild_key` is untouched by this plan (see the plan header). Insert
this new function immediately after it:

```python
def _write_conflicts_batch(tx, clear_ids: list[str], conflict_rows: list[dict]) -> None:
    """Batched sibling of `_write_conflicts`. `clear_ids` is every entity id
    being rebuilt this pass -- conflicts are always cleared before
    (re)writing, matching the per-key function's own clear-then-write order,
    whether or not that entity ends up with any conflicts this time.

    `conflict_rows`: one row per (entity, conflicting property) pair --
    `[{"id": conflict_id, "entity_id": eid, "property": str, "domain": str,
    "kind": str, "values": [str, ...], "observation_ids": [str, ...]}, ...]`.
    """
    if clear_ids:
        tx.run(
            """
            UNWIND $ids AS id
            MATCH (c:Conflict {_source: 'projection'})-[:CONFLICT_OF]->(:Entity {_id: id})
            DETACH DELETE c
            """,
            ids=clear_ids,
        )
    if not conflict_rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (e:Entity {_id: row.entity_id})
        MERGE (c:Conflict {id: row.id})
        SET c._source = 'projection',
            c.property = row.property,
            c.entity_id = row.entity_id,
            c._domain = row.domain,
            c.kind = row.kind,
            c.status = 'open',
            c.values = row.values,
            c.detected_by = 'projection_rebuild'
        MERGE (c)-[:CONFLICT_OF]->(e)
        WITH c, row
        UNWIND row.observation_ids AS oid
        MATCH (o:Observation {id: oid})
        MERGE (c)-[:EVIDENCE]->(o)
        """,
        rows=conflict_rows,
    )
```

- [ ] **Step 5: Add the batched relationship helpers, alongside the existing ones**

`_relation_groups` (lines 573-609 today) and `_sync_relates_to` (lines 631-701 today) are
**not removed or modified** — both are still called by `rebuild_key`, which this plan does
not touch. Insert these new functions after `_group_relations` (which is pure, already
takes a list of rows, and needs no batched sibling of its own):

```python
def _relation_groups_batch(tx, keys: list[tuple[str, str, str]]) -> tuple[list[dict], list[dict]]:
    """Every raw `ASSERTS_RELATION` edge touching any of `keys`' `latest`
    observations, across the WHOLE batch in two round-trips total (one
    outgoing, one incoming) -- the batched sibling of `_relation_groups`,
    which scoped to one entity's own `member_keys` per call. Each row is
    tagged with the raw key it came from (`src_key`/`tgt_key`) so the caller
    can regroup rows back per-entity in Python -- the same grouping
    `_relation_groups` used to hand back pre-scoped for a single entity.
    """
    if not keys:
        return [], []
    key_strings = [key_string(k) for k in keys]
    outgoing = tx.run(
        """
        UNWIND $keys AS key
        MATCH (o:Observation {key: key})-[r:ASSERTS_RELATION]->(t:Observation)
        RETURN key AS src_key, r.rel_type AS rel_type, t.key AS other_key,
               r.doc_id AS doc_id, r.chunk_id AS chunk_id
        """,
        keys=key_strings,
    ).data()
    incoming = tx.run(
        """
        UNWIND $keys AS key
        MATCH (s:Observation)-[r:ASSERTS_RELATION]->(o:Observation {key: key})
        RETURN key AS tgt_key, r.rel_type AS rel_type, s.key AS other_key,
               r.doc_id AS doc_id, r.chunk_id AS chunk_id
        """,
        keys=key_strings,
    ).data()
    return outgoing, incoming


def _delete_relates_to_batch(tx, ids: list[str]) -> None:
    if not ids:
        return
    tx.run("UNWIND $ids AS id MATCH (e:Entity {_id: id})-[r:RELATES_TO]->(:Entity) DELETE r", ids=ids)
    tx.run("UNWIND $ids AS id MATCH (:Entity)-[r:RELATES_TO]->(e:Entity {_id: id}) DELETE r", ids=ids)


def _write_relates_to_batch(tx, rows: list[dict]) -> None:
    """Batched sibling of `_sync_relates_to`'s per-edge write. `rows`: one
    per resolved RELATES_TO edge across the whole batch --
    `[{"src": eid, "tgt": eid, "rel_type": str, "observation_count": int,
    "chunk_ids": [...], "doc_ids": [...]}, ...]`.
    """
    if not rows:
        return
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (e:Entity {_id: row.src})
        MATCH (t:Entity {_id: row.tgt})
        MERGE (e)-[r:RELATES_TO {rel_type: row.rel_type}]->(t)
        SET r.observation_count = row.observation_count, r.chunk_ids = row.chunk_ids, r.doc_ids = row.doc_ids
        """,
        rows=rows,
    )
```

- [ ] **Step 6: Add the batched SAME_AS-links helper**

Insert immediately after `_sync_same_as` (after line 885 today, before `apply_retractions`):

```python
def _sync_same_as_links_batch(tx, links: list[tuple[tuple[str, str, str], tuple[str, str, str]]]) -> None:
    """Batched sibling of `_sync_same_as`, called once for every link pair
    in `rebuild`'s pass instead of once per pair."""
    if not links:
        return
    rows = [{"member": entity_id(member), "canonical": entity_id(canonical)} for member, canonical in links]
    tx.run(
        """
        UNWIND $rows AS row
        MATCH (m:Entity {_id: row.member}), (c:Entity {_id: row.canonical})
        MERGE (m)-[:SAME_AS]->(c)
        MERGE (c)-[:SAME_AS]->(m)
        """,
        rows=rows,
    )
```

- [ ] **Step 7: Rewrite `rebuild()`'s body**

Replace the entire `rebuild` function (lines 953-1027 today) with:

```python
def rebuild(tx, keys, *, same_as_groups: list[list[tuple[str, str, str]]] | None = None, synthesis_loader=None) -> dict:
    """Rebuild the given aggregate keys inside the caller's transaction.

    `same_as_groups` defaults to `same_as.load_groups()` -- pass an explicit
    (possibly filtered) list only when the caller has one in hand already.
    `synthesis_loader(key) -> dict | None` is the synthesize seam; absent,
    every description falls back to the winner observation's.

    Batched: every phase below issues one Cypher statement covering the
    WHOLE `keys` batch, not one per key -- see
    `docs/superpowers/specs/2026-09-25-projection-rebuild-batching-design.md`.
    The outcome (which entities exist, their properties, their edges) is
    identical to the old per-key implementation; only the round-trip count
    changes.
    """
    if same_as_groups is None:
        from artmind import same_as

        same_as_groups = same_as.load_groups()

    unit_of, members_of, links = _plan_groups(keys, same_as_groups)

    sorted_keys = sorted(keys)
    effective_keys = [k for k in sorted_keys if unit_of.get(k) in (None, k)]
    folded_keys = [k for k in sorted_keys if unit_of.get(k) not in (None, k)]

    # Clear stale SAME_AS edges up front, for every key keeping its own
    # Entity this pass -- re-synced from `links` at the very end.
    _clear_same_as_batch(tx, [entity_id(k) for k in effective_keys])

    # ── read every member key's observations in ONE round-trip ─────────────
    all_member_keys: set[tuple[str, str, str]] = set()
    for k in effective_keys:
        all_member_keys.update(members_of.get(k, [k]))
    obs_by_key = _read_observations_for_keys(tx, all_member_keys)

    to_write: list[tuple[tuple[str, str, str], list[tuple[str, str, str]], dict]] = []
    no_observation_keys: list[tuple[str, str, str]] = []
    for k in effective_keys:
        member_keys = members_of.get(k, [k])
        observations = [o for mk in member_keys for o in obs_by_key.get(key_string(mk), [])]
        if not observations:
            no_observation_keys.append(k)
            continue
        synthesis = synthesis_loader(k) if synthesis_loader else None
        merged = merge_observations(observations, synthesis=synthesis, override_key=k)
        to_write.append((k, member_keys, merged))

    # ── zero-observation keys: GC if an Entity exists, else "absent" ───────
    no_obs_ids = [entity_id(k) for k in no_observation_keys]
    existing_ids = _entity_ids_that_exist(tx, no_obs_ids)
    to_delete_ids = [eid for eid in no_obs_ids if eid in existing_ids]

    summary = {
        "rebuilt": len(to_write),
        "deleted": len(to_delete_ids) + len(folded_keys),
        "absent": len(no_obs_ids) - len(to_delete_ids),
        "keys": len(effective_keys) + len(folded_keys),
    }

    # folded-away keys are always deleted, unconditionally -- never their own entity.
    _delete_entities_batch(tx, to_delete_ids + [entity_id(k) for k in folded_keys])

    # ── write every "to_write" entity's props/labels in ONE statement ──────
    merge_rows = []
    aggregates_rows = []
    conflict_rows = []
    relates_to_context = []  # (eid, member_keys)
    for key, member_keys, merged in to_write:
        eid = entity_id(key)
        props = merged["props"]
        label = _sanitize_label(props.get("entity_class") or "")
        keep = list(props.keys()) + list(_PRESERVED_ENTITY_KEYS)
        merge_rows.append({
            "id": eid, "label": label, "keep": keep, "props": props,
            "description": props.get("description"),
        })
        aggregates_rows.append({"id": eid, "observation_ids": merged["observation_ids"]})
        for conflict in merged["conflicts"]:
            conflict_rows.append({
                "id": hashlib.sha256(f"{eid}|{conflict['property']}".encode("utf-8")).hexdigest(),
                "entity_id": eid,
                "property": conflict["property"],
                "domain": props.get("_domain") or "",
                "kind": conflict["kind"],
                "values": [str(v["value"]) for v in conflict["values"]],
                "observation_ids": [v["observation_id"] for v in conflict["values"] if v.get("observation_id")],
            })
        relates_to_context.append((eid, member_keys))

    _merge_entities_batch(tx, merge_rows)
    _rewire_aggregates_batch(tx, aggregates_rows)
    _write_conflicts_batch(tx, [r["id"] for r in merge_rows], conflict_rows)

    # ── RELATES_TO: one batched read, delete, and write for the WHOLE pass ─
    all_relates_keys = [mk for _, member_keys in relates_to_context for mk in member_keys]
    outgoing_rows, incoming_rows = _relation_groups_batch(tx, all_relates_keys)
    _delete_relates_to_batch(tx, [eid for eid, _ in relates_to_context])

    relates_to_rows = []
    for eid, member_keys in relates_to_context:
        member_key_strings = {key_string(mk) for mk in member_keys}
        outgoing = _group_relations([r for r in outgoing_rows if r["src_key"] in member_key_strings])
        incoming = _group_relations([r for r in incoming_rows if r["tgt_key"] in member_key_strings])
        for (rel_type, other_key), agg in outgoing.items():
            parsed = _parse_key(other_key)
            if not parsed:
                continue
            tgt = entity_id(unit_of.get(parsed, parsed))
            if tgt == eid:
                continue
            relates_to_rows.append({"src": eid, "tgt": tgt, "rel_type": rel_type, **agg})
        for (rel_type, other_key), agg in incoming.items():
            parsed = _parse_key(other_key)
            if not parsed:
                continue
            src = entity_id(unit_of.get(parsed, parsed))
            if src == eid:
                continue
            relates_to_rows.append({"src": src, "tgt": eid, "rel_type": rel_type, **agg})
    _write_relates_to_batch(tx, relates_to_rows)

    _sync_same_as_links_batch(tx, links)

    logger.info(
        "Projection rebuild: {} key(s) -- rebuilt={} deleted={} absent={}",
        summary["keys"], summary["rebuilt"], summary["deleted"], summary["absent"],
    )
    return summary
```

Note what's deliberately gone from the old body: the per-key `sorted(keys)` main loop, the
per-50-keys progress checkpoint (Task 1, Step 3 already removed its tests), and the call
into `rebuild_key`. `rebuild_key` itself is untouched and still exists, still calling
`read_latest_observations`, `_write_conflicts`, and `_sync_relates_to` exactly as before,
for `synthesize.py`'s sole benefit.

- [ ] **Step 8: Run the projection test suite and confirm everything passes**

Run: `uv run --group dev pytest test/test_projection_group_rebuild.py test/test_projection_merge.py test/test_projection_key.py test/test_projection_status.py -v`

Expected: every test PASSES, including all of Task 1's new/rewritten tests, the
untouched `apply_retractions` tests, and every test in `test_projection_merge.py` (pure
functions, never touched).

- [ ] **Step 9: Run `synthesize.py`'s own test suite**

Run: `uv run --group dev pytest test/test_synthesize.py -v`

Expected: PASSES unchanged — `rebuild_key` was never modified, so its only other caller
is unaffected.

- [ ] **Step 10: Commit**

```bash
git add artmind/projection.py
git commit -m "$(cat <<'EOF'
perf(projection): batch rebuild's Cypher into a small constant per key set

rebuild(tx, keys) issued roughly 6 sequential round-trips per key --
measured at ~0.5s/key against remote AuraDB (an 860-key rebuild ran 8+
minutes). Restructures it into a pure-Python planning phase (unchanged
logic) followed by a handful of UNWIND-batched writes covering the
whole key set in one round-trip per concern, instead of one per key.
rebuild_key (synthesize.py's single-key entry point) is untouched.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Verify byte-identical behavior against a real Neo4j

**Files:** none (verification only)

- [ ] **Step 1: Start a Neo4j 5.24+ instance and point a `.env` at it**

If you don't already have one running, start a throwaway local Neo4j:

```bash
docker run -d --name projection-batching-verify \
  -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/<pick-a-password> \
  neo4j:5.26
```

In whichever `.env`/`config.env` `paths.load_env()` resolves for this checkout (the run
folder's `~/.artmind/config.env`, or `ARTMIND_VAULT`'s `.artmind/config.env` if you're
running from inside a vault), set:

```
ARTMIND_KG_NEO4J_URI=bolt://localhost:7687
ARTMIND_KG_NEO4J_USER=neo4j
ARTMIND_KG_NEO4J_PASSWORD=<the password you picked above>
ARTMIND_KG_NEO4J_DATABASE=neo4j
```

- [ ] **Step 2: Run the live behavioral-equivalence suite BEFORE checking out this
      branch's changes, as a baseline**

```bash
git stash
ARTMIND_TEST_LIVE_NEO4J=1 uv run --group dev pytest test/test_projection_live.py -v
git stash pop
```

Expected: every test in the file PASSES against the old, unmodified `rebuild()`. This is
the baseline the batched version must match exactly.

- [ ] **Step 3: Run the same suite against the new batched implementation**

```bash
ARTMIND_TEST_LIVE_NEO4J=1 uv run --group dev pytest test/test_projection_live.py -v
```

Expected: every test PASSES — identically to Step 2, with no test file changes. This is
the proof that batching produces the same graph state as the old per-key
implementation: same Entity properties, same `AGGREGATES`/`RELATES_TO`/`SAME_AS`/
`CONFLICT_OF` edges, same idempotence, same conflict materialization/resolution, same
embedding-staleness flagging, same zero-observation GC behavior.

If any test fails here, do not proceed to Task 4 — the failure means the batched
rewrite diverged from the old behavior for that scenario, and the discrepancy needs to
be root-caused against Task 2's code before moving on.

- [ ] **Step 4: Tear down the throwaway Neo4j instance and revert the `.env` change**

```bash
docker rm -f projection-batching-verify
```

Restore whatever `ARTMIND_KG_NEO4J_*` values were in the `.env`/`config.env` before Step
1 (or remove the lines you added, if this config file didn't have them before).

---

## Task 4: Full regression pass and cleanup

**Files:** none (verification only)

- [ ] **Step 1: Run the full hermetic suite**

Run: `just dev-test` (equivalently: `uv run --group dev pytest test/ -v`)

Expected: all tests pass — in particular, nothing under `test_ingest_*`,
`test_reserved_relationships.py`, `test_query_layer.py`, or `test_graph_snapshot.py`
should reference `rebuild`'s internal Cypher shape directly (they exercise it through
`commit_to_graph`/`write_to_graph` with a mocked session, asserting on the *outcome* or
on document/chunk-level Cypher, not on `rebuild`'s own per-key statements), so none of
them should need changes. If any of them fail, read the failure before editing — it
means that test *was* coupled to `rebuild`'s old per-key call shape in a way this plan's
earlier exploration didn't find, and it needs the same kind of batched-shape update Task
1 gave `test_projection_group_rebuild.py`.

- [ ] **Step 2: Confirm no dead code remains**

Run: `grep -n "rebuild_key(tx, key)" artmind/projection.py` and confirm `rebuild_key`
still exists, is called only from `synthesize.py`, and its body is byte-for-byte what it
was before this plan (it was never edited). Run:
`grep -rn "_sync_relates_to\|_write_conflicts\b" artmind/*.py` and confirm both are still
called (from `rebuild_key`) and neither is now unused.

- [ ] **Step 3: Update the design spec's status**

Edit
[docs/superpowers/specs/2026-09-25-projection-rebuild-batching-design.md](../specs/2026-09-25-projection-rebuild-batching-design.md),
changing the header's `**Status:** Design — approved in brainstorming` to
`**Status:** Implemented`.

```bash
git add docs/superpowers/specs/2026-09-25-projection-rebuild-batching-design.md
git commit -m "$(cat <<'EOF'
docs: mark projection-rebuild-batching spec implemented

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
