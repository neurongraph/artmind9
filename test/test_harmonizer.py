"""Tests for artmind.harmonizer's dict-merge sync (Phase 1: entity_types is a map)."""

import pytest
import yaml

from artmind.harmonizer import harmonize_schema


def _write(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture()
def schemas_dir(tmp_path, monkeypatch):
    import artmind.harmonizer as harmonizer_module

    monkeypatch.setattr(harmonizer_module, "DOMAIN_SCHEMAS_DIR", tmp_path)
    return tmp_path


PERSON = {
    "kind": "recurrent",
    "description": "A named individual.",
    "type_examples": ["author", "subject"],
    "properties": {"role": {"hint": "their role"}},
    "relates_to": {"LOCATION": ["visited", "lives_in"]},
}
LOCATION = {
    "kind": "recurrent",
    "description": "A place.",
    "type_examples": ["country", "city"],
}
EVENT = {
    "kind": "occurrent",
    "description": "A named happening.",
    "type_examples": ["meeting"],
}


def test_harmonize_copies_missing_class_whole(schemas_dir):
    _write(schemas_dir / "fixture_schema.yaml", {
        "name": "fixture",
        "entity_types": {"PERSON": PERSON, "LOCATION": LOCATION},
    })
    _write(schemas_dir / "fixture.child_schema.yaml", {
        "name": "fixture.child",
        "entity_types": {"PERSON": PERSON},
    })

    result = harmonize_schema("fixture.child")

    assert result.status == "updated"
    assert result.added == ["LOCATION"]
    child = yaml.safe_load((schemas_dir / "fixture.child_schema.yaml").read_text())
    assert child["entity_types"]["LOCATION"] == LOCATION
    # untouched
    assert child["entity_types"]["PERSON"] == PERSON


def test_harmonize_in_sync_when_nothing_missing(schemas_dir):
    _write(schemas_dir / "fixture_schema.yaml", {
        "name": "fixture", "entity_types": {"PERSON": PERSON},
    })
    _write(schemas_dir / "fixture.child_schema.yaml", {
        "name": "fixture.child", "entity_types": {"PERSON": PERSON},
    })

    result = harmonize_schema("fixture.child")
    assert result.status == "in_sync"


def test_harmonize_never_removes_child_specific_extras(schemas_dir):
    _write(schemas_dir / "fixture_schema.yaml", {
        "name": "fixture", "entity_types": {"PERSON": PERSON},
    })
    _write(schemas_dir / "fixture.child_schema.yaml", {
        "name": "fixture.child", "entity_types": {"PERSON": PERSON, "EVENT": EVENT},
    })

    result = harmonize_schema("fixture.child")
    assert result.status == "in_sync"
    child = yaml.safe_load((schemas_dir / "fixture.child_schema.yaml").read_text())
    assert "EVENT" in child["entity_types"]


def test_harmonize_dry_run_does_not_write(schemas_dir):
    _write(schemas_dir / "fixture_schema.yaml", {
        "name": "fixture", "entity_types": {"PERSON": PERSON, "LOCATION": LOCATION},
    })
    _write(schemas_dir / "fixture.child_schema.yaml", {
        "name": "fixture.child", "entity_types": {"PERSON": PERSON},
    })

    before = (schemas_dir / "fixture.child_schema.yaml").read_text()
    result = harmonize_schema("fixture.child", dry_run=True)

    assert result.status == "dry_run"
    assert result.added == ["LOCATION"]
    assert (schemas_dir / "fixture.child_schema.yaml").read_text() == before


def test_harmonize_errors_on_missing_child(schemas_dir):
    _write(schemas_dir / "fixture_schema.yaml", {"name": "fixture", "entity_types": {}})
    result = harmonize_schema("fixture.child")
    assert result.status == "error"
    assert "not found" in result.error


def test_harmonize_errors_on_missing_parent(schemas_dir):
    _write(schemas_dir / "fixture.child_schema.yaml", {"name": "fixture.child", "entity_types": {}})
    result = harmonize_schema("fixture.child")
    assert result.status == "error"
    assert "not found" in result.error


def test_harmonize_rejects_pre_redesign_list_format(schemas_dir):
    _write(schemas_dir / "fixture_schema.yaml", {"name": "fixture", "entity_types": ["PERSON"]})
    _write(schemas_dir / "fixture.child_schema.yaml", {"name": "fixture.child", "entity_types": ["PERSON"]})

    result = harmonize_schema("fixture.child")
    assert result.status == "error"
    assert "map" in result.error


def test_harmonize_reports_validation_error_and_does_not_write(schemas_dir):
    """A parent class missing `kind` must fail loudly rather than propagate silently."""
    broken_person = {k: v for k, v in PERSON.items() if k != "kind"}
    _write(schemas_dir / "fixture_schema.yaml", {
        "name": "fixture", "entity_types": {"PERSON": broken_person, "LOCATION": LOCATION},
    })
    _write(schemas_dir / "fixture.child_schema.yaml", {
        "name": "fixture.child", "entity_types": {"PERSON": broken_person},
    })

    before = (schemas_dir / "fixture.child_schema.yaml").read_text()
    result = harmonize_schema("fixture.child")

    assert result.status == "error"
    assert "kind" in result.error
    assert (schemas_dir / "fixture.child_schema.yaml").read_text() == before
