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


# ── reserved relationship names (study item 4) ───────────────────────────────
#
# `supersedes`, `relates_to`, ... are artmind's own edge types. The write path
# drops an extracted relationship of that name ("reserved rel_type skipped"), so
# a schema that asks the model for one silently loses every such edge. The
# schema author hears about it at validation and at extraction start instead.

from artmind.schema_validate import (  # noqa: E402
    RESERVED_REL_TYPES,
    reserved_relationship_warnings,
    warn_all,
)


def _contracts(rel_types, target="AGREEMENT"):
    return {
        "name": "contracts",
        "entity_types": {
            "AGREEMENT": {"kind": "recurrent", "relates_to": {target: rel_types}},
            "PARTY": {"kind": "recurrent"},
        },
    }


def test_a_reserved_relationship_is_warned_about_naming_class_relationship_and_a_replacement():
    warnings = reserved_relationship_warnings(_contracts(["amends", "supersedes"]), "contracts")

    assert len(warnings) == 1
    text = warnings[0]
    assert "contracts.entity_types.AGREEMENT.relates_to.AGREEMENT" in text
    assert "'supersedes'" in text
    assert "replaces_agreement" in text  # a suggestion built from the target class
    for reserved in sorted(RESERVED_REL_TYPES):
        assert reserved.lower() in text, "the message carries the full reserved list, as an author writes them"


def test_the_match_is_case_insensitive_and_ignores_an_inline_properties_hint():
    warnings = reserved_relationship_warnings(
        _contracts(["Supersedes (properties: {effective: date})", "RELATES_TO"]), "contracts"
    )
    assert len(warnings) == 2
    assert any("Supersedes" in w for w in warnings) and any("RELATES_TO" in w for w in warnings)


def test_a_schema_without_reserved_relationships_has_no_warnings():
    assert reserved_relationship_warnings(_contracts(["amends", "replaces_contract"]), "contracts") == []


def test_part_of_is_not_reserved():
    assert reserved_relationship_warnings(_contracts(["part_of"]), "contracts") == []


def test_a_reserved_name_is_a_warning_not_a_validation_error():
    """Shipped schemas declare some (banking's `supersedes`); `init` validates
    with `validate_all_or_raise`, which must keep passing."""
    assert validate_schema(_contracts(["supersedes"]), META) == []


def test_warn_all_walks_a_schemas_directory(tmp_path):
    import yaml

    (tmp_path / "contracts_schema.yaml").write_text(yaml.safe_dump(_contracts(["supersedes"])))
    (tmp_path / "clean_schema.yaml").write_text(yaml.safe_dump({**_contracts(["amends"]), "name": "clean"}))

    found = warn_all(tmp_path)

    assert list(found) == ["contracts"]
    assert "'supersedes'" in found["contracts"][0]


def test_domains_validate_prints_the_warning_and_still_passes(tmp_path, monkeypatch):
    import yaml
    from click.testing import CliRunner

    import artmind.cli as cli_module
    import artmind.schema_validate as sv

    (tmp_path / "contracts_schema.yaml").write_text(yaml.safe_dump(_contracts(["supersedes"])))
    monkeypatch.setattr(cli_module, "DOMAIN_SCHEMAS_DIR", tmp_path)
    monkeypatch.setattr(sv, "DOMAIN_SCHEMAS_DIR", tmp_path)
    monkeypatch.setattr(sv, "load_meta", lambda *a, **k: META)

    result = CliRunner().invoke(cli_module.cli, ["domains", "validate", "--domain", "contracts"])

    assert result.exit_code == 0, result.output
    assert "WARN" in result.output and "'supersedes'" in result.output and "AGREEMENT" in result.output
    assert "All schemas valid" in result.output


def test_extraction_warns_once_when_the_schema_it_loads_has_a_reserved_relationship(monkeypatch, tmp_path):
    """The author running `ingest` without `domains validate` still hears it."""
    from loguru import logger

    from test_ingest_concurrency import _setup_extract_env
    import artmind.ingest as ing
    import yaml

    file_result = _setup_extract_env(monkeypatch, tmp_path, 1)
    (ing.DOMAIN_SCHEMAS_DIR / "testdom_schema.yaml").write_text(
        yaml.safe_dump({**_contracts(["supersedes"]), "name": "testdom"})
    )
    messages = []
    handle = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        ing.extract_kg(file_result, "testdom", max_workers=1)
    finally:
        logger.remove(handle)

    hits = [m for m in messages if "reserved" in m and "'supersedes'" in m]
    assert len(hits) == 1


# ── a schema that does not parse is reported with file and line (study item 6) ──

BROKEN = "name: broken\nentity_types:\n  A:\n    kind: recurrent\n    description: one: two\n"


def _write_broken(tmp_path, name="broken", text=BROKEN):
    (tmp_path / f"{name}_schema.yaml").write_text(text)


def test_load_error_names_the_file_line_and_column(tmp_path):
    from artmind.schema_validate import schema_parse_error

    _write_broken(tmp_path)

    message = schema_parse_error(tmp_path / "broken_schema.yaml")

    assert "broken_schema.yaml" in message
    assert "line 5" in message and "column" in message
    assert "mapping values are not allowed" in message
    assert "description: one: two" in message  # the offending line, quoted


def test_a_valid_file_has_no_parse_error(tmp_path):
    from artmind.schema_validate import schema_parse_error

    (tmp_path / "ok_schema.yaml").write_text("name: ok\nentity_types: {}\n")
    assert schema_parse_error(tmp_path / "ok_schema.yaml") is None


def test_validate_all_reports_a_broken_schema_instead_of_raising(tmp_path):
    _write_broken(tmp_path)
    (tmp_path / "fine_schema.yaml").write_text("name: fine\nentity_types:\n  A: {kind: recurrent}\n")

    found = validate_all(tmp_path, meta_path=_meta_file(tmp_path))

    assert list(found) == ["broken"]
    assert "line 5" in found["broken"][0]


def _meta_file(tmp_path):
    import yaml

    path = tmp_path / "meta.yaml"
    path.write_text(yaml.safe_dump(META))
    return path


def test_domains_validate_prints_the_line_for_one_domain(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import artmind.cli as cli_module
    import artmind.schema_validate as sv

    _write_broken(tmp_path)
    monkeypatch.setattr(cli_module, "DOMAIN_SCHEMAS_DIR", tmp_path)
    monkeypatch.setattr(sv, "DOMAIN_SCHEMAS_DIR", tmp_path)
    monkeypatch.setattr(sv, "load_meta", lambda *a, **k: META)

    result = CliRunner().invoke(cli_module.cli, ["domains", "validate", "--domain", "broken"])

    assert result.exit_code != 0
    assert "ERROR" in result.output and "broken_schema.yaml" in result.output and "line 5" in result.output
    assert "Traceback" not in result.output


def test_domains_validate_names_every_broken_file_in_a_full_run(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import artmind.cli as cli_module
    import artmind.schema_validate as sv

    _write_broken(tmp_path, "one")
    _write_broken(tmp_path, "two")
    real_validate_all, real_warn_all = sv.validate_all, sv.warn_all
    # validate_all()/warn_all() bind their directory default at import time
    monkeypatch.setattr(sv, "validate_all", lambda *a, **k: real_validate_all(tmp_path, meta_path=_meta_file(tmp_path)))
    monkeypatch.setattr(sv, "warn_all", lambda *a, **k: real_warn_all(tmp_path))
    monkeypatch.setattr(sv, "load_meta", lambda *a, **k: META)

    result = CliRunner().invoke(cli_module.cli, ["domains", "validate"])

    assert result.exit_code != 0
    assert "one_schema.yaml" in result.output and "two_schema.yaml" in result.output


def test_a_missing_domain_still_says_so(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import artmind.cli as cli_module

    monkeypatch.setattr(cli_module, "DOMAIN_SCHEMAS_DIR", tmp_path)
    result = CliRunner().invoke(cli_module.cli, ["domains", "validate", "--domain", "nope"])
    assert result.exit_code != 0 and "not found" in result.output.lower()


def test_ingest_stops_with_the_line_when_the_schema_does_not_parse(monkeypatch, tmp_path):
    """Before ingesting a note, not as a traceback in the middle of it."""
    from test_ingest_concurrency import _setup_extract_env

    import artmind.ingest as ing

    file_result = _setup_extract_env(monkeypatch, tmp_path, 1)
    (ing.DOMAIN_SCHEMAS_DIR / "testdom_schema.yaml").write_text(BROKEN)
    messages = []
    from loguru import logger

    handle = logger.add(lambda m: messages.append(str(m)), level="ERROR")
    try:
        assert ing.extract_kg(file_result, "testdom", max_workers=1) is None
    finally:
        logger.remove(handle)

    assert any("testdom_schema.yaml" in m and "line 5" in m for m in messages)
