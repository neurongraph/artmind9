"""Schema-only property keys (spec 2026-11-15 §2.2). Synthetic data only."""
from artmind import ingest as ing
from artmind.observations import declared_property_keys, declared_property_order, filter_declared

SCHEMA = {
    "property_aliases": {"outcome_or_result": "outcome"},
    "entity_types": {
        "EVENT": {"kind": "occurrent", "properties": {
            "date_or_time": {"temporal": "valid_from"}, "outcome": {"required": True}, "cause": {}}},
        "PERSON": {"kind": "recurrent", "properties": {"role": {}}},
        "PLACE": {"kind": "recurrent"},
    },
}


def test_declared_property_keys_per_class():
    assert declared_property_keys(SCHEMA) == {
        "EVENT": {"date_or_time", "outcome", "cause"}, "PERSON": {"role"}, "PLACE": set()}


def test_declared_order_puts_required_first_and_can_drop_temporal():
    assert declared_property_order(SCHEMA)["EVENT"] == ["outcome", "date_or_time", "cause"]
    assert declared_property_order(SCHEMA, include_temporal=False)["EVENT"] == ["outcome", "cause"]


def test_filter_keeps_declared_structural_and_reserved_keys_and_returns_the_rest():
    obs = {"id": "o1", "name": "N", "entity_class": "EVENT", "_valid_from": "2026-01-07",
           "outcome": "won", "mood": "calm", "role": "x"}
    kept, dropped = filter_declared(obs, "EVENT", declared_property_keys(SCHEMA))
    assert kept == {"id": "o1", "name": "N", "entity_class": "EVENT", "_valid_from": "2026-01-07", "outcome": "won"}
    assert dropped == {"mood": "calm", "role": "x"}


def test_build_observations_drops_undeclared_keys_after_alias_folding():
    entities = [{"id": "e1", "name": "Call", "entity_class": "EVENT", "chunk_id": "d_001", "_domain": "j"}]
    props = [{"id": "e1", "properties": {"outcome_or_result": "agreed", "sentiment": "warm"}}]
    document = {"id": "d", "version": 1, "_valid_from": "2026-01-07"}
    obs, dropped = ing._build_observations(entities, props, {}, SCHEMA, document)
    assert obs[0]["outcome"] == "agreed"
    assert "sentiment" not in obs[0] and "outcome_or_result" not in obs[0]
    assert dropped == {"EVENT": {"sentiment": 1}}
