"""Tests for artmind.schema_validate: the meta-schema contract (Phase 1)."""

import pytest

from artmind.schema_validate import (
    SchemaValidationError,
    load_meta,
    validate_all,
    validate_all_or_raise,
    validate_schema,
)

META = {
    "reserved_prefix": "_",
    "kinds": {"recurrent": "...", "occurrent": "..."},
}


def test_valid_schema_has_no_violations():
    schema = {
        "name": "fixture",
        "entity_types": {
            "PERSON": {"kind": "recurrent", "description": "A person.", "properties": {"role": {}}},
        },
    }
    assert validate_schema(schema, META) == []


def test_missing_kind_is_reported():
    schema = {"name": "fixture", "entity_types": {"PERSON": {"description": "A person."}}}
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("kind" in e and "PERSON" in e for e in errors)


def test_invalid_kind_is_reported():
    schema = {"name": "fixture", "entity_types": {"PERSON": {"kind": "eternal"}}}
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("eternal" in e for e in errors)


def test_pre_redesign_list_format_is_rejected():
    schema = {"name": "fixture", "entity_types": ["PERSON", "LOCATION"]}
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("map" in e for e in errors)


def test_schema_with_no_entity_types_key_is_skipped_not_flagged():
    """A pure stub schema (no entity_types at all) is out of scope, not invalid."""
    assert validate_schema({"name": "fixture"}, META) == []


def test_reserved_prefix_on_class_name_is_flagged():
    schema = {"name": "fixture", "entity_types": {"_SYSTEM": {"kind": "recurrent"}}}
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("_SYSTEM" in e and "reserved" in e for e in errors)


def test_reserved_prefix_on_property_name_is_flagged():
    schema = {
        "name": "fixture",
        "entity_types": {"PERSON": {"kind": "recurrent", "properties": {"_id": {}}}},
    }
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("_id" in e and "reserved" in e for e in errors)


# ── a class name colliding with the structural :Entity label ────────────────
# The bug this catches (general_schema.yaml's original "ENTITY" fallback):
# MERGE (e:Entity {_id: ...}) gives every node a structural `:Entity` label,
# then a dynamic label is added from the sanitized class name. Neo4j labels
# are case-sensitive, so a class that sanitizes to "ENTITY" adds a SECOND,
# visually-identical-looking label instead of matching the first.


def test_a_class_named_entity_is_flagged():
    schema = {"name": "fixture", "entity_types": {"ENTITY": {"kind": "recurrent"}}}
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("ENTITY" in e and "reserved label" in e for e in errors)


def test_a_class_name_that_sanitizes_to_entity_is_also_flagged():
    """Case and punctuation differences don't dodge the check -- the same
    sanitize step (uppercase, non-alnum -> underscore) runs before either
    ends up on a Neo4j node."""
    schema = {"name": "fixture", "entity_types": {"entity": {"kind": "recurrent"}}}
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("reserved label" in e for e in errors)


def test_a_more_specific_class_name_is_not_flagged():
    schema = {"name": "fixture", "entity_types": {"THING": {"kind": "recurrent"}}}
    assert validate_schema(schema, META, schema_name="fixture") == []


def test_no_shipped_schema_defines_a_class_colliding_with_entity():
    """Regression: this exact collision shipped in general_schema.yaml until
    it was renamed to THING. Runs against the real package schemas, not a
    fixture, so a future schema can't reintroduce it unnoticed."""
    from paths import PACKAGE_SCHEMAS_DIR

    violations = validate_all(schemas_dir=PACKAGE_SCHEMAS_DIR)
    for name, errors in violations.items():
        assert not any("reserved label" in e for e in errors), (name, errors)


def test_reserved_prefix_on_relates_to_target_is_flagged():
    schema = {
        "name": "fixture",
        "entity_types": {"PERSON": {"kind": "recurrent", "relates_to": {"_HIDDEN": ["knows"]}}},
    }
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("_HIDDEN" in e and "reserved" in e for e in errors)


def test_class_declaration_must_be_a_map():
    schema = {"name": "fixture", "entity_types": {"PERSON": "not a map"}}
    errors = validate_schema(schema, META, schema_name="fixture")
    assert any("must be a map" in e for e in errors)


def test_load_meta_missing_file_raises(tmp_path):
    with pytest.raises(SchemaValidationError, match="not found"):
        load_meta(tmp_path / "does_not_exist.yaml")


def test_validate_all_only_reports_schemas_with_violations(tmp_path):
    meta_path = tmp_path / "meta.yaml"
    meta_path.write_text(
        "reserved_prefix: '_'\nkinds:\n  recurrent: x\n  occurrent: y\n", encoding="utf-8"
    )
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "good_schema.yaml").write_text(
        "name: good\nentity_types:\n  PERSON:\n    kind: recurrent\n", encoding="utf-8"
    )
    (schemas_dir / "bad_schema.yaml").write_text(
        "name: bad\nentity_types:\n  PERSON:\n    description: no kind here\n", encoding="utf-8"
    )

    violations = validate_all(schemas_dir, meta_path)
    assert list(violations.keys()) == ["bad"]


def test_validate_all_or_raise_raises_with_every_violation(tmp_path):
    meta_path = tmp_path / "meta.yaml"
    meta_path.write_text(
        "reserved_prefix: '_'\nkinds:\n  recurrent: x\n  occurrent: y\n", encoding="utf-8"
    )
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "bad_schema.yaml").write_text(
        "name: bad\nentity_types:\n  PERSON:\n    description: no kind here\n", encoding="utf-8"
    )

    with pytest.raises(SchemaValidationError, match="PERSON"):
        validate_all_or_raise(schemas_dir, meta_path)


def test_validate_all_or_raise_is_silent_when_everything_passes(tmp_path):
    meta_path = tmp_path / "meta.yaml"
    meta_path.write_text(
        "reserved_prefix: '_'\nkinds:\n  recurrent: x\n  occurrent: y\n", encoding="utf-8"
    )
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "good_schema.yaml").write_text(
        "name: good\nentity_types:\n  PERSON:\n    kind: recurrent\n", encoding="utf-8"
    )

    validate_all_or_raise(schemas_dir, meta_path)  # must not raise
