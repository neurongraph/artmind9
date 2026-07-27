"""Tests for artmind.schema_reference: prompt parsing and fragment rendering."""

from pathlib import Path

from artmind.schema_reference import (
    build_schema_dict,
    find_family_schemas,
    list_schema_families,
    parse_entities,
    parse_properties,
    parse_relationships,
    render_fragment,
)

FIXTURE = Path(__file__).parent / "schemas" / "test_prompt_schema.yaml"
SCHEMAS_DIR = FIXTURE.parent


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


def test_render_fragment_includes_entities_and_relationships():
    data = build_schema_dict(FIXTURE)
    frag = render_fragment([data], prefix="test_prompt")

    assert "WIDGET" in frag
    assert "GADGET" in frag
    assert "attached_to" in frag
    assert "widget_id" in frag
    # self-contained: no external network resources
    assert "http://" not in frag
    assert "https://" not in frag


def test_render_fragment_is_a_fragment_not_a_page():
    """Must carry no page chrome, styling, or behaviour of its own.

    It's injected into the dashboard's DOM (like the CLI guide fragment), so a
    stray <style> would leak across the whole admin-ui and a <script> would
    never execute via innerHTML in the first place.
    """
    data = build_schema_dict(FIXTURE)
    frag = render_fragment([data], prefix="test_prompt")
    # Precise tag boundaries: the fragment legitimately uses <header> for
    # entity-card headers, which a bare "<head" substring check would flag.
    for forbidden in ("<!DOCTYPE", "<html", "<head>", "<body", "<style", "<script", "<title", ":root"):
        assert forbidden not in frag, f"fragment must not contain {forbidden!r}"


def test_render_fragment_sections_are_collapsible():
    """Each schema section needs a clickable head and a foldable body.

    dashboard.js delegates a click on .sr-schema-head to toggle .collapsed on
    the parent .sr-schema (CSS then hides .sr-schema-body) -- this locks in
    the class names and structure that wiring depends on.
    """
    data = build_schema_dict(FIXTURE)
    frag = render_fragment([data], prefix="test_prompt")
    assert '<button type="button" class="sr-schema-head">' in frag
    assert '<div class="sr-schema-body">' in frag


def test_list_schema_families_derives_prefix_before_first_dot():
    families = list_schema_families(SCHEMAS_DIR)
    assert "test_prompt" in families


def test_find_family_schemas_matches_dotted_children():
    matches = find_family_schemas(SCHEMAS_DIR, "test_prompt")
    assert FIXTURE in matches


def test_list_schema_families_empty_dir_returns_empty(tmp_path):
    assert list_schema_families(tmp_path / "does_not_exist") == []


# ── /api/schema-reference route ─────────────────────────────────────────────


def _client(monkeypatch, schemas_dir, admin_routes=True):
    import shutil

    import artmind.webui.dashboard_routes as dashboard_routes
    from artmind.webui.app import create_app
    from fastapi.testclient import TestClient

    shutil.copy(FIXTURE, schemas_dir / FIXTURE.name)
    monkeypatch.setattr(dashboard_routes, "DOMAIN_SCHEMAS_DIR", schemas_dir)
    return TestClient(create_app(admin_routes=admin_routes))


def test_families_route_lists_the_fixture_family(tmp_path, monkeypatch):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/api/schema-reference/families")
    assert response.status_code == 200
    assert "test_prompt" in response.json()


def test_schema_reference_route_serves_the_fragment(tmp_path, monkeypatch):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/api/schema-reference", params={"prefix": "test_prompt"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "WIDGET" in response.text


def test_schema_reference_route_404s_on_unknown_family(tmp_path, monkeypatch):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/api/schema-reference", params={"prefix": "no_such_family"})
    assert response.status_code == 404


def test_schema_reference_routes_are_admin_only(tmp_path, monkeypatch):
    """The chat UI is the end-user surface — operator tooling stays off it."""
    client = _client(monkeypatch, tmp_path, admin_routes=False)
    assert client.get("/api/schema-reference/families").status_code == 404
    assert client.get("/api/schema-reference", params={"prefix": "test_prompt"}).status_code == 404
