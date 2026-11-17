"""`schema_validate.class_identity` and its checks (spec 2026-11-15 §5.3)."""
from pathlib import Path

import pytest
import yaml

from artmind import schema_validate as sv

META = {"kinds": {"recurrent": "", "occurrent": ""}}


@pytest.mark.parametrize("decl, expected", [
    ({"kind": "recurrent"}, "name"),
    ({"kind": "occurrent"}, "occasion"),
    ({"kind": "recurrent", "identity": "category"}, "category"),
    ({"kind": "occurrent", "identity": "name"}, "name"),
])
def test_identity_defaults_from_kind(decl, expected):
    assert sv.class_identity({"entity_types": {"X": decl}}, "X") == expected


def test_an_undeclared_class_is_an_occasion():
    assert sv.class_identity({"entity_types": {}}, "HEALTH_INCIDENT") == "occasion"


def test_unknown_identity_is_an_error():
    schema = {"entity_types": {"X": {"kind": "recurrent", "identity": "person"}}}
    assert any("identity" in e for e in sv.validate_schema(schema, META))


def test_known_identity_is_not_an_error():
    schema = {"entity_types": {"X": {"kind": "recurrent", "identity": "category"}}}
    assert sv.validate_schema(schema, META) == []


def test_occasion_with_recurrent_kind_warns():
    schema = {"entity_types": {"X": {"kind": "recurrent", "identity": "occasion"}}}
    assert any("never history" in w for w in sv.identity_warnings(schema, "s"))


def test_name_class_with_a_valid_from_property_warns_unless_document_entity():
    props = {"properties": {"d": {"temporal": "valid_from"}}}
    schema = {"entity_types": {"X": {"kind": "occurrent", "identity": "name", **props},
                               "Y": {"kind": "occurrent", "identity": "name", **props},
                               "Z": {"kind": "recurrent", "identity": "name", **props}},
              "document_entity": {"classes": ["Y"]}}
    warnings = sv.identity_warnings(schema, "s")
    assert any(".X" in w for w in warnings)
    assert not any(".Y" in w or ".Z" in w for w in warnings)


def test_classes_with_identity():
    schema = {"entity_types": {"A": {"kind": "recurrent"}, "B": {"kind": "occurrent"},
                               "C": {"kind": "recurrent", "identity": "category"}}}
    assert sv.classes_with_identity(schema, "name", "category") == {"A", "C"}


def test_meta_identities_block_names_the_three_values():
    meta = yaml.safe_load((Path(sv.__file__).parent / "domains" / "meta.yaml").read_text(encoding="utf-8"))
    assert tuple(meta["identities"]) == sv.IDENTITIES


def test_domains_validate_reports_identity_warnings(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from artmind import cli as cli_mod

    schema = {"name": "s", "entity_types": {"X": {"kind": "recurrent", "identity": "occasion",
                                                  "properties": {"d": {"temporal": "valid_from"}}}}}
    (tmp_path / "s_schema.yaml").write_text(yaml.safe_dump(schema), encoding="utf-8")
    monkeypatch.setattr(cli_mod, "DOMAIN_SCHEMAS_DIR", tmp_path)
    monkeypatch.setattr(sv, "DOMAIN_SCHEMAS_DIR", tmp_path)
    result = CliRunner().invoke(cli_mod.cli, ["domains", "validate", "--domain", "s", "--compact"])
    assert "never history" in result.output
