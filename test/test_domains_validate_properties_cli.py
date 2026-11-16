"""`artmind domains validate` wiring for the property checks (spec 2026-11-15 §2.6). Synthetic schemas only."""
import json

import pytest
import yaml
from click.testing import CliRunner

import artmind.cli as cli_module
import paths
from artmind import schema_validate as sv
from artmind.cli import cli
from artmind.mart import validate as mv


@pytest.fixture()
def schemas(tmp_path, monkeypatch):
    d = tmp_path / "schemas"
    d.mkdir()
    monkeypatch.setattr(cli_module, "DOMAIN_SCHEMAS_DIR", d)
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", d)
    monkeypatch.setattr(sv, "validate_all", lambda: {})
    monkeypatch.setattr(sv, "warn_all", lambda: {})
    monkeypatch.setattr(mv, "validate_all", lambda: {})
    monkeypatch.setattr(sv, "meta_template_warnings", lambda: [])
    return d


def _write(d, name, data):
    (d / f"{name}_schema.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _payload(result):
    return json.loads(next(line for line in result.stdout.splitlines() if line.startswith("{")))


def test_clean_run_carries_property_references_and_meta_warnings(schemas):
    _write(schemas, "d", {"name": "d", "entity_types": {"EVENT": {"kind": "occurrent"}}})
    r = CliRunner().invoke(cli, ["domains", "validate", "--compact"])
    assert r.exit_code == 0, r.output
    p = _payload(r)
    assert p["ok"] is True and p["property_references"] == {} and p["meta_warnings"] == []


def test_only_a_reference_error_fails_with_an_error_line(schemas, monkeypatch):
    _write(schemas, "d", {"name": "d", "entity_types": {"EVENT": {"kind": "occurrent"}}})
    monkeypatch.setattr(sv, "property_reference_report",
                        lambda name, schema: {"errors": [f"mart/{name} x: EVENT.mood is not declared"], "warnings": []})
    r = CliRunner().invoke(cli, ["domains", "validate", "--compact"])
    assert r.exit_code == 1
    assert "ERROR  mart/d x: EVENT.mood is not declared" in r.stderr
    assert "1 domain(s) with undeclared property references failed validation" in r.stderr
    assert "schema(s)," not in r.stderr
    p = _payload(r)
    assert p["ok"] is False and p["property_references"]["d"]["errors"]


def test_the_meta_template_warning_is_printed_and_in_the_json(schemas, monkeypatch):
    _write(schemas, "d", {"name": "d", "entity_types": {"EVENT": {"kind": "occurrent"}}})
    monkeypatch.setattr(sv, "meta_template_warnings", lambda: ["meta.yaml: old template; run `artmind init --force`"])
    r = CliRunner().invoke(cli, ["domains", "validate", "--compact"])
    assert r.exit_code == 0
    assert "WARN   meta.yaml: old template" in r.stderr
    assert _payload(r)["meta_warnings"] == ["meta.yaml: old template; run `artmind init --force`"]


def test_a_schema_in_violations_is_skipped_for_reference_checks(schemas, monkeypatch):
    # The file stem and its `name:` differ: violations are keyed by `name:`.
    _write(schemas, "file_stem", {"name": "named", "entity_types": {"EVENT": {"kind": "occurrent"}}})
    _write(schemas, "ok", {"name": "ok", "entity_types": {"EVENT": {"kind": "occurrent"}}})
    monkeypatch.setattr(sv, "validate_all", lambda: {"named": ["named: broken"]})
    seen = []
    monkeypatch.setattr(sv, "property_reference_report",
                        lambda name, schema: seen.append(name) or {"errors": [], "warnings": []})
    r = CliRunner().invoke(cli, ["domains", "validate", "--compact"])
    assert r.exit_code == 1
    assert seen == ["ok"]
    assert "1 schema(s) failed validation" in r.stderr
