"""Property-key drift: one concept, one key (no Neo4j, no LLM).

Measured on the live personal_journal graph: PERSON carried
`notable_characteristic` 752x beside the declared `notable_characteristics`
351x (the prompt's rule 8 says singular; the schema declared plural), and
classes used `narrative_role` / `narrative_function`,
`relationship_to_diarist` / `relationship_to_writer` for the same thing.
"""
from artmind.observations import normalize_property_keys, property_key_rules

SCHEMA = {
    "property_aliases": {"narrative_function": "narrative_role", "relationship_to_writer": "relationship_to_diarist"},
    "entity_types": {
        "PERSON": {"kind": "recurrent", "properties": {"notable_characteristic": {}, "relationship_to_diarist": {}}},
        "OBJECT": {"kind": "recurrent", "properties": {"narrative_role": {}, "outcome": {}}},
    },
}


def _norm(props):
    return normalize_property_keys(props, *property_key_rules(SCHEMA))


def test_a_number_variant_folds_onto_the_declared_key():
    assert _norm({"notable_characteristics": "kind"}) == {"notable_characteristic": "kind"}
    assert _norm({"outcomes": ["won"]}) == {"outcome": ["won"]}


def test_a_declared_alias_maps_to_its_canonical_key():
    assert _norm({"narrative_function": "host", "relationship_to_writer": "employer"}) == {
        "narrative_role": "host", "relationship_to_diarist": "employer"}


def test_both_keys_present_merge_their_values():
    assert _norm({"notable_characteristic": "kind", "notable_characteristics": ["kind", "funny"]}) == {
        "notable_characteristic": ["kind", "funny"]}
    assert _norm({"narrative_role": "host", "narrative_function": "host"}) == {"narrative_role": "host"}


def test_undeclared_unrelated_and_structural_keys_are_untouched():
    props = {"recent_activity": "x", "status": "y", "name": "Srinath", "_valid_from": "2026-01-07",
             "description": "d"}
    assert _norm(props) == props


def test_retrieved_vocabulary_drops_keys_that_would_be_renamed(monkeypatch):
    from unittest.mock import MagicMock

    from artmind.canonicalize import retrieve_property_vocabulary

    session = MagicMock()
    session.run.return_value.data.return_value = [
        {"entity_class": "PERSON", "key": "notable_characteristics", "uses": 9},
        {"entity_class": "PERSON", "key": "notable_characteristic", "uses": 5},
        {"entity_class": "OBJECT", "key": "narrative_function", "uses": 4},
        {"entity_class": "OBJECT", "key": "narrative_role", "uses": 3},
    ]
    vocabulary = retrieve_property_vocabulary(session, domain="journal", schema=SCHEMA)
    # A drifted key ranked high by use is exactly what entrenched the drift.
    assert vocabulary == {"PERSON": ["notable_characteristic"], "OBJECT": ["narrative_role"]}


def test_validate_rejects_bad_property_aliases():
    from artmind.schema_validate import validate_schema
    from artmind.prompt_builder import load_meta

    base = {"name": "j", "description": "d", "entity_types": SCHEMA["entity_types"]}
    assert not [e for e in validate_schema({**base, "property_aliases": SCHEMA["property_aliases"]}, load_meta())
                if "property_aliases" in e]
    errors = " ".join(validate_schema({**base, "property_aliases": {
        "notable_characteristic": "x",     # a declared key cannot be an alias
        "a": "b", "b": "c",                # no chains
        "c2": "name",                      # never onto a structural key
    }}, load_meta()))
    assert "notable_characteristic" in errors and "chain" in errors and "name" in errors


def test_a_child_domain_inherits_its_parents_property_aliases(tmp_path):
    from artmind.temporal import load_schema

    (tmp_path / "bank_schema.yaml").write_text("property_aliases:\n  a: b\n  c: d\n", encoding="utf-8")
    (tmp_path / "bank.cases_schema.yaml").write_text("property_aliases:\n  c: e\n", encoding="utf-8")
    assert load_schema("bank.cases", schemas_dir=tmp_path)["property_aliases"] == {"a": "b", "c": "e"}
