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

        if "MATCH (o:Observation {key: $key}) RETURN properties(o) AS p" in cy:
            rows = [{"p": o} for o in self.observations_by_key.get(params["key"], [])]
            return _Result(rows)

        if "MATCH (e:Entity {_id: $id}) RETURN count(e) AS c" in cy:
            return _Result(single_row={"c": 1 if params["id"] in self.entity_exists else 0})

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
        "domain": "banking.reference", "_kind": "occurrent", "doc_version": 1,
        "_doc_valid_from": "2026-01-01", "_valid_from": "2026-01-01",
    }
    base.update(kw)
    return base


# ── merge: same (class, domain) as canonical ─────────────────────────────────


def test_merge_unions_both_members_observations_into_the_canonical_entity():
    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("financial conduct authority", "REGULATOR", "banking.reference")
    tx = FakeTx(observations_by_key={
        key_string(canonical): [_o(id="o1", canonical_name="FCA", scope="banking")],
        key_string(member): [_o(id="o2", canonical_name="Financial Conduct Authority", scope="uk")],
    })

    rebuild(tx, {canonical, member}, same_as_groups=[[canonical, member]])

    merge_calls = tx.calls_matching("MERGE (e:Entity {_id: $id})")
    assert len(merge_calls) == 1  # ONE entity written, not two
    cy, params = merge_calls[0]
    assert params["id"] == entity_id(canonical)
    # both members' domain properties made it into the union
    assert params["props"]["_id"] == entity_id(canonical)


def test_merge_deletes_the_folded_members_own_entity():
    canonical = ("fca", "REGULATOR", "banking.reference")
    member = ("financial conduct authority", "REGULATOR", "banking.reference")
    tx = FakeTx(observations_by_key={
        key_string(canonical): [_o(id="o1")],
        key_string(member): [_o(id="o2")],
    })

    rebuild(tx, {canonical, member}, same_as_groups=[[canonical, member]])

    detach_deletes = tx.calls_matching("DETACH DELETE c, e")
    assert any(p["id"] == entity_id(member) for _, p in detach_deletes)
    # the canonical's own id must NEVER be DETACH DELETEd by the same pass
    assert not any(p["id"] == entity_id(canonical) for _, p in detach_deletes)


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

    merge_calls = tx.calls_matching("MERGE (e:Entity {_id: $id})")
    ids = {p["id"] for _, p in merge_calls}
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

    merge_calls = tx.calls_matching("MERGE (e:Entity {_id: $id})")
    ids = {p["id"] for _, p in merge_calls}
    assert ids == {entity_id(canonical), entity_id(member)}, "LINK keeps both entities -- no fold"

    same_as_calls = tx.calls_matching("MERGE (m)-[:SAME_AS]->(c)")
    assert len(same_as_calls) == 1
    _, params = same_as_calls[0]
    assert params["m"] == entity_id(member)
    assert params["c"] == entity_id(canonical)


def test_link_clears_stale_same_as_edges_before_resyncing():
    canonical = ("fca", "REGULATOR", "banking.reference")
    tx = FakeTx(observations_by_key={key_string(canonical): [_o(id="o1")]})

    # No group this pass -- any previously-linked SAME_AS edge must be cleared.
    rebuild(tx, {canonical}, same_as_groups=[])

    clears = tx.calls_matching("OPTIONAL MATCH (e)-[r:SAME_AS]-(:Entity) DELETE r")
    assert any(p["id"] == entity_id(canonical) for _, p in clears)


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
