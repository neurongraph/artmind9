"""OBJECT's declared type list and the happening move (spec 2026-11-15 §5.3). Invented names."""
import json
from pathlib import Path

import yaml

from artmind import property_audit as pa
from artmind import schema_validate as sv
from artmind.prompt_builder import _render_class_block

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = yaml.safe_load((ROOT / "artmind" / "domains" / "schemas" / "personal_journal_schema.yaml")
                         .read_text(encoding="utf-8"))
ASSET = yaml.safe_load((ROOT / "artmind" / "skills" / "artmind-create-schema" / "assets"
                        / "personal_journal_schema.yaml").read_text(encoding="utf-8"))
META = {"kinds": {"recurrent": "", "occurrent": ""}}


def _errors(decl):
    return sv.validate_schema({"entity_types": {"OBJECT": {"kind": "recurrent", **decl}}}, META)


def test_object_declares_a_closed_type_list():
    for schema in (JOURNAL, ASSET):
        obj = schema["entity_types"]["OBJECT"]
        assert "type_examples" not in obj and "medication" in obj["types"] and "flight" not in obj["types"]
        assert "not an object" in obj["description"]
    assert JOURNAL["entity_types"]["OBJECT"] == ASSET["entity_types"]["OBJECT"]
    assert not any("OBJECT" in e for e in sv.validate_schema(JOURNAL, META))


def test_types_render_as_a_closed_choice():
    block = _render_class_block("OBJECT", {"kind": "recurrent", "types": ["device", "food"]}, {})
    assert "type: exactly one of: device | food" in block
    old = _render_class_block("OBJECT", {"kind": "recurrent", "type_examples": ["device"]}, {})
    assert "example type values: device" in old


def test_empty_or_malformed_types_is_an_error():
    for bad in ([], "device", ["device", ""], None):
        assert any("types" in e for e in _errors({"types": bad})), bad
    assert _errors({"types": ["device"]}) == []


def test_types_beside_type_examples_is_an_error():
    assert any("both" in e for e in _errors({"types": ["device"], "type_examples": ["gift"]}))


def test_class_types_prefers_the_closed_list():
    assert sv.class_types({"types": ["a"], "type_examples": ["b"]}) == ["a"]
    assert sv.class_types({"type_examples": ["b"]}) == ["b"]
    assert sv.class_types({}) == [] and sv.class_types(None) == []


def test_property_audit_counts_types_outside_the_list(tmp_path):
    folder = tmp_path / "kg" / "d" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc1", "name": "doc1.md"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "c1", "text": "a kettle and a gadget"}]))
    types = ["device", "food", "household_item", "device", "vehicle_part", "medical specimen", "Household-Item"]
    (folder / "observations.json").write_text(json.dumps(
        [{"id": f"o{i}", "entity_class": "OBJECT", "chunk_id": "c1", "type": t} for i, t in enumerate(types)]))
    schema = {"entity_types": {"OBJECT": {"kind": "recurrent", "types": ["device", "food", "household_item"],
                                          "properties": {"what_it_is": {}}}}}
    audit = pa.audit("d", schema, kg_dir=tmp_path / "kg", depended_on={})
    row = {(r["class"], r["key"]): r for r in audit["rows"]}[("OBJECT", "type")]
    assert row["outside_types"] == 2 and row["outside_distinct"] == 2 and row["fill"] == 1.0
    assert "vehicle_part" not in json.dumps(audit)  # counts, never values


def test_a_declared_type_property_row_gains_the_counts(tmp_path):
    folder = tmp_path / "kg" / "d" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc1", "name": "doc1.md"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "c1", "text": "x"}]))
    (folder / "observations.json").write_text(json.dumps(
        [{"id": f"o{i}", "entity_class": "OBJECT", "chunk_id": "c1", "type": t} for i, t in enumerate(["device", "odd"])]))
    schema = {"entity_types": {"OBJECT": {"kind": "recurrent", "types": ["device"], "properties": {"type": {}}}}}
    rows = [r for r in pa.audit("d", schema, kg_dir=tmp_path / "kg", depended_on={})["rows"] if r["key"] == "type"]
    assert len(rows) == 1 and rows[0]["outside_types"] == 1
