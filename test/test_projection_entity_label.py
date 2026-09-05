"""projection.rebuild_key must never emit a redundant `:ENTITY` label.

`ENTITY` is general_schema.yaml's generic fallback class name, and Neo4j
labels are case-sensitive, so it coexists with (rather than merging into) the
structural `:Entity` label every node already carries from the MERGE --
a node whose class is the generic fallback ends up wearing what looks like
the same label twice in the browser.

Per CLAUDE.md: assert on the query text and parameters actually sent, never
on a mocked session's truthy-for-anything default.
"""
from artmind.observations import entity_id, key_string
from artmind.projection import rebuild_key


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
    """Answers only `read_latest_observations` and `entity exists`; every
    other query (provenance rewire, conflicts, relates_to sync) gets an empty
    result, which is all `rebuild_key` needs to complete without error."""

    def __init__(self, observations_by_key):
        self.observations_by_key = observations_by_key
        self.calls: list[tuple[str, dict]] = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        cy = " ".join(cypher.split())
        if "MATCH (o:Observation {key: $key}) RETURN properties(o) AS p" in cy:
            rows = [{"p": o} for o in self.observations_by_key.get(params["key"], [])]
            return _Result(rows)
        return _Result()

    def calls_matching(self, needle: str):
        return [(cy, p) for cy, p in self.calls if needle in " ".join(cy.split())]


def _o(**kw):
    base = {
        "id": "o1", "name": "n", "canonical_name": "n", "entity_class": "ENTITY",
        "domain": "general", "_kind": "occurrent", "doc_version": 1,
        "_doc_valid_from": "2026-01-01", "_valid_from": "2026-01-01",
    }
    base.update(kw)
    return base


def _merge_call(tx):
    calls = tx.calls_matching("MERGE (e:Entity {_id: $id})")
    assert len(calls) == 1
    return calls[0]


def test_the_generic_entity_class_adds_no_dynamic_label():
    key = ("acme corp", "ENTITY", "general")
    tx = FakeTx({key_string(key): [_o()]})

    rebuild_key(tx, key)

    cy, params = _merge_call(tx)
    assert "apoc.create.addLabels(e, [])" in cy


def test_the_generic_entity_class_still_unconditionally_strips_a_stale_label():
    """Self-heals a node written before this fix, or reclassified away from
    ENTITY on a later ingest -- REMOVE is a no-op if the label isn't there."""
    key = ("acme corp", "PERSON", "general")
    tx = FakeTx({key_string(key): [_o(entity_class="PERSON")]})

    rebuild_key(tx, key)

    cy, params = _merge_call(tx)
    assert "apoc.create.addLabels(e, ['PERSON'])" in cy
    assert "REMOVE node:ENTITY" in cy


def test_a_specific_class_still_gets_its_own_dynamic_label():
    key = ("river thames", "LOCATION", "general")
    tx = FakeTx({key_string(key): [_o(entity_class="LOCATION")]})

    rebuild_key(tx, key)

    cy, params = _merge_call(tx)
    assert "apoc.create.addLabels(e, ['LOCATION'])" in cy
    assert params["id"] == entity_id(key)
