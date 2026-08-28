"""Relationship provenance is carried by the immutable, chunk-scoped
`ASSERTS_RELATION` edge between the two `:Observation` nodes a relationship
connects (Phase 4) — never a direct `:Entity`-`:Entity` write, and never
accumulated onto a shared edge at write time. The projection rebuild is what
aggregates these into `RELATES_TO {rel_type, observation_count, chunk_ids,
doc_ids}` (see `test_projection_merge.py`); this file covers only the raw
write. We assert the actual Cypher and params sent, never summary counts — a
recording session returns truthy for any query (per CLAUDE.md).
"""
import artmind.ingest as ing
from artmind.observations import relation_observation_id


class _Tx:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))

    def rel_calls(self):
        return [(c, kw) for c, kw in self.calls if "ASSERTS_RELATION" in c and "MERGE" in c]


def _observations():
    from artmind.observations import aggregate_key, key_string

    return [
        {
            "id": "obs-bank", "name": "Bank", "canonical_name": "Bank", "chunk_id": "docA_002",
            "key": key_string(aggregate_key("Bank", "ORG", "banking")),
        },
        {
            "id": "obs-cust", "name": "Customer", "canonical_name": "Customer", "chunk_id": "docA_002",
            "key": key_string(aggregate_key("Customer", "ORG", "banking")),
        },
    ]


def test_edge_carries_structural_fields_and_flattened_extras():
    tx = _Tx()
    rel = {
        "source_name": "Bank", "target_name": "Customer", "rel_type": "SERVES",
        "chunk_id": "docA_002", "doc_id": "docA",
        "description": "the bank serves the customer",
    }
    written = ing._write_relation_observations(tx, [rel], {"id": "docA"}, _observations())
    assert written == 1

    cypher, kwargs = tx.rel_calls()[0]
    assert kwargs["src"] == "obs-bank"
    assert kwargs["tgt"] == "obs-cust"
    assert kwargs["rel_type"] == "SERVES"
    assert kwargs["doc_id"] == "docA"
    assert kwargs["chunk_id"] == "docA_002"
    assert kwargs["id"] == relation_observation_id("docA_002", "obs-bank", "SERVES", "obs-cust")

    # Extracted extras flatten onto the edge, never smuggled into the
    # structural kwargs and never JSON-blobbed.
    assert kwargs["props"] == {"description": "the bank serves the customer"}
    assert "description" not in {"src", "tgt", "id", "rel_type", "doc_id", "chunk_id"}


def test_bidirectional_edge_writes_both_directions_with_the_same_props():
    tx = _Tx()
    rel = {
        "source_name": "Bank", "target_name": "Customer", "rel_type": "RELATED",
        "chunk_id": "docA_001", "doc_id": "docA", "bidirectional": True,
    }
    written = ing._write_relation_observations(tx, [rel], {"id": "docA"}, _observations())
    assert written == 2

    pairs = {(kw["src"], kw["tgt"]) for _, kw in tx.rel_calls()}
    assert pairs == {("obs-bank", "obs-cust"), ("obs-cust", "obs-bank")}
    for _, kw in tx.rel_calls():
        assert kw["doc_id"] == "docA"
        assert kw["chunk_id"] == "docA_001"


def test_falls_back_to_the_documents_own_id_when_the_rel_has_none():
    tx = _Tx()
    rel = {"source_name": "Bank", "target_name": "Customer", "rel_type": "RELATED", "chunk_id": "docA_001"}
    ing._write_relation_observations(tx, [rel], {"id": "docA"}, _observations())

    _, kwargs = tx.rel_calls()[0]
    assert kwargs["doc_id"] == "docA"


def test_a_nested_object_extra_is_dropped_not_json_encoded():
    tx = _Tx()
    rel = {
        "source_name": "Bank", "target_name": "Customer", "rel_type": "RELATED",
        "chunk_id": "docA_001", "doc_id": "docA",
        "weird_nested": {"a": 1},
    }
    ing._write_relation_observations(tx, [rel], {"id": "docA"}, _observations())

    _, kwargs = tx.rel_calls()[0]
    assert "weird_nested" not in kwargs["props"]


def test_the_properties_object_the_prompt_asks_for_is_unwrapped_onto_the_edge():
    """The `relationships` prompt template (artmind/domains/meta.yaml) tells the
    extractor to return a nested `properties: object` per relationship — the
    rel_type-specific detail, same shape as the entities prompt's own
    `properties` step. Regression for a real bug found live during the Phase 8
    cutover re-ingest: passing `rel.items()` straight to `_flatten_props`
    without unwrapping `properties` first meant `_neo4j_value` correctly (but
    silently) dropped it as an unrecognized nested object on every single
    relationship, every ingest, since this write path was introduced — 100%
    loss of per-instance relationship detail. Must be unwrapped exactly like
    entity `domain_props` are in `_build_observations`.
    """
    tx = _Tx()
    rel = {
        "source_name": "Bank", "target_name": "Customer", "rel_type": "SERVES",
        "chunk_id": "docA_002", "doc_id": "docA",
        "description": "the bank serves the customer",
        "properties": {"role": "primary account holder", "since": "2024-01-01"},
    }
    ing._write_relation_observations(tx, [rel], {"id": "docA"}, _observations())

    _, kwargs = tx.rel_calls()[0]
    assert kwargs["props"] == {
        "description": "the bank serves the customer",
        "role": "primary account holder",
        "since": "2024-01-01",
    }
    assert "properties" not in kwargs["props"]


def test_a_non_dict_properties_value_is_tolerated_not_crashed_on():
    tx = _Tx()
    rel = {
        "source_name": "Bank", "target_name": "Customer", "rel_type": "SERVES",
        "chunk_id": "docA_002", "doc_id": "docA",
        "properties": "not an object",
    }
    ing._write_relation_observations(tx, [rel], {"id": "docA"}, _observations())

    _, kwargs = tx.rel_calls()[0]
    assert kwargs["props"] == {}
