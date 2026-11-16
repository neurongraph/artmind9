"""Shipped schemas point the extractor only at keys their classes declare (spec 2026-11-15 §2.5)."""
from pathlib import Path

import pytest
import yaml

from artmind import schema_validate as sv

SCHEMAS = Path(__file__).resolve().parent.parent / "artmind" / "domains" / "schemas"


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_guidance_names_only_declared_keys(path):
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert sv.guidance_property_warnings(schema, path.stem) == []


@pytest.mark.parametrize("path", sorted(SCHEMAS.glob("*_schema.yaml")), ids=lambda p: p.stem)
def test_every_alias_targets_a_declared_key(path):
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert sv.property_alias_warnings(schema, path.stem) == []


def test_charge_guidance_does_not_invite_column_named_keys():
    schema = yaml.safe_load((SCHEMAS / "contracts_schema.yaml").read_text(encoding="utf-8"))
    assert "table's own column names" not in str(schema["entity_types"]["CHARGE"]["guidance"])


def test_shipped_own_key_declarations_are_warnings_not_errors():
    """Eight shipped schemas declare `context`/`domain`/`description` as properties: `domains validate`
    warns about them but `setup` (validate_all_or_raise) must not fail."""
    found = [w for path in sorted(SCHEMAS.glob("*_schema.yaml"))
             for w in sv.own_key_property_warnings(yaml.safe_load(path.read_text(encoding="utf-8")), path.stem)]
    assert found
    for path in sorted(SCHEMAS.glob("*_schema.yaml")):
        schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert not any("observation field" in e for e in sv.validate_schema(schema, sv.load_meta(), path.stem))
