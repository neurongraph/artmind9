"""projection.rebuild's group-aware path (same-as merge/link) and
apply_retractions — I/O level, via a dispatch-based fake transaction.

Per CLAUDE.md: assert on the parameters actually sent and which query ran,
never on counts, and never trust a bare MagicMock's truthy-for-anything
default. `FakeTx` below answers only the specific queries these tests care
about with real data; everything else answers empty/falsy.
"""
from artmind.observations import aggregate_key, entity_id, key_string
from artmind.projection import apply_retractions, rebuild


class _Result:
    def __init__(self, rows=None, single_row=None):
        self._rows = rows or []
        self._single = single_row

    def data(self):
        return self._rows

    def single(self):
        return self._single

    def __iter__(self):
        return iter(self._rows)


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


def _o(**kw):
    base = {
        "id": "o1", "name": "n", "canonical_name": "n", "entity_class": "REGULATOR",
        "_domain": "banking.reference", "_kind": "occurrent", "doc_version": 1,
        "_doc_valid_from": "2026-01-01", "_valid_from": "2026-01-01",
    }
    base.update(kw)
    return base


# ── rebuild_key: dynamic label/property Cypher, not APOC ────────────────────


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

    # Exact equality holds for this fixture: no folding (same_as_groups=[]),
    # no conflicts, no relationships, every key has one observation — all
    # batch operations scale O(1), not O(n), and the empty-set guards skip.
    assert len(large_tx.calls) == len(small_tx.calls), (
        f"call count scaled with key count: {len(small_tx.calls)} calls for "
        f"{len(small_keys)} keys vs {len(large_tx.calls)} calls for {len(large_keys)} keys"
    )


# ── merge: same (class, domain) as canonical ─────────────────────────────────


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


# ── link: different class or domain from canonical ──────────────────────────


def test_link_keeps_both_entities_and_syncs_same_as_both_directions():
    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("fca", "AUTHORITY", "banking.risk_governance")  # different class+domain
    tx = FakeTx(observations_by_key={
        key_string(canonical): [_o(id="o1", entity_class="REGULATOR", domain="banking.reference")],
        key_string(member): [_o(id="o2", entity_class="AUTHORITY", domain="banking.risk_governance")],
    })

    rebuild(tx, {canonical, member}, same_as_groups=[[canonical, member]])

    merge_calls = tx.calls_matching("MERGE (e:Entity {_id: row.id})")
    assert len(merge_calls) == 1
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


# ── apply_retractions ─────────────────────────────────────────────────────────


def test_apply_retractions_demotes_the_target_observation():
    tx = FakeTx(observation_keys={"old-obs-1": "smartsaver tier 2|RATE_ENTRY|banking.reference"})
    retracted = apply_retractions(tx, [_o(id="new-obs-1", _retracts="old-obs-1")])
    assert retracted == {("smartsaver tier 2", "RATE_ENTRY", "banking.reference")}

    demote_calls = tx.calls_matching("REMOVE o:Observation SET o:ObservationHistory")
    assert any(p["id"] == "old-obs-1" for _, p in demote_calls)


def test_apply_retractions_deletes_a_relationship_edge_when_no_observation_matches():
    class RelTx(FakeTx):
        def run(self, cypher, **params):
            self.calls.append((cypher, params))
            cy = " ".join(cypher.split())
            if cy.startswith("MATCH (o:Observation {id: $id})") and "ObservationHistory" in cy:
                return _Result(single_row=None)  # no such Observation
            if "ASSERTS_RELATION {id: $id}]->() RETURN count(r) AS n" in cy:
                return _Result(single_row={"n": 1})  # the edge DOES exist
            return _Result()

    tx = RelTx()
    retracted = apply_retractions(tx, [_o(id="new-obs-1", _retracts="rel-edge-1")])
    assert retracted == set()  # a relationship edge has no aggregate key of its own

    delete_calls = tx.calls_matching("ASSERTS_RELATION {id: $id}]->() DELETE r")
    assert any(p["id"] == "rel-edge-1" for _, p in delete_calls)


def test_apply_retractions_is_tolerant_of_an_unmatched_target():
    tx = FakeTx()  # nothing matches anything
    retracted = apply_retractions(tx, [_o(id="new-obs-1", _retracts="nowhere")])
    assert retracted == set()


def test_apply_retractions_ignores_observations_with_no_retracts_field():
    tx = FakeTx()
    retracted = apply_retractions(tx, [_o(id="new-obs-1")])
    assert retracted == set()
    assert tx.calls == []
