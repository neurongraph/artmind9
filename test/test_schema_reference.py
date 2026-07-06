"""Tests for artmind.schema_reference: prompt parsing and HTML rendering."""

from pathlib import Path

from artmind.schema_reference import (
    build_schema_dict,
    parse_entities,
    parse_properties,
    parse_relationships,
    render_html,
)

FIXTURE = Path(__file__).parent / "schemas" / "test_prompt_schema.yaml"


def test_parse_entities_extracts_classes_and_types():
    data = build_schema_dict(FIXTURE)
    classes = {e["class"] for e in data["entities"]}
    assert classes == {"WIDGET", "GADGET"}

    widget = next(e for e in data["entities"] if e["class"] == "WIDGET")
    assert widget["types"] == ["small_widget", "large_widget"]
    assert "widget produced by the fixture domain" in widget["description"]


def test_parse_properties_attaches_to_matching_entity():
    data = build_schema_dict(FIXTURE)
    widget = next(e for e in data["entities"] if e["class"] == "WIDGET")
    gadget = next(e for e in data["entities"] if e["class"] == "GADGET")

    assert {p["name"] for p in widget["properties"]} == {"widget_id", "color"}
    assert {p["name"] for p in gadget["properties"]} == {"gadget_id", "capacity"}

    widget_id = next(p for p in widget["properties"] if p["name"] == "widget_id")
    assert widget_id["hint"] == "e.g., W-001"


def test_parse_relationships_extracts_pair_and_types():
    data = build_schema_dict(FIXTURE)
    assert len(data["relationships"]) == 1
    rel = data["relationships"][0]
    assert rel["a"] == "WIDGET"
    assert rel["b"] == "GADGET"
    assert rel["types"] == ["attached_to", "compatible_with"]


def test_render_html_includes_entities_and_relationships():
    data = build_schema_dict(FIXTURE)
    doc = render_html([data], title="Fixture Schemas", prefix="test_prompt")

    assert "<title>Fixture Schemas</title>" in doc
    assert "WIDGET" in doc
    assert "GADGET" in doc
    assert "attached_to" in doc
    assert "widget_id" in doc
    # self-contained: no external network resources
    assert "http://" not in doc
    assert "https://" not in doc
