"""Markup and asset wiring for Explore -> Graph. The admin chat's per-message
"View as graph" link was removed (spec 2026-11-14 §1): graphs are drawn in the chat."""

import re

from fastapi.testclient import TestClient

from artmind.webui.app import create_app


def _admin() -> TestClient:
    return TestClient(create_app(admin_routes=True))


def test_dashboard_has_graph_subtab_and_script():
    html = _admin().get("/dashboard").text
    assert 'data-tab="graph"' in html and 'data-tab-panel="graph"' in html
    assert "graph.js" in html
    for el_id in ("graph-domain", "graph-question", "graph-generate-btn", "graph-cypher", "graph-open-btn", "graph-run-btn", "graph-canvas"):
        assert f'id="{el_id}"' in html
    assert html.index("/static/graph-render.js?v=") < html.index("/static/graph.js?v=")


def test_no_page_loads_the_removed_chat_link_script():
    console = TestClient(create_app(template_name="admin.html", admin_routes=True))
    assert "admin-graph.js" not in console.get("/").text
    assert "admin-graph.js" not in TestClient(create_app(admin_routes=False)).get("/").text
    assert console.get("/static/admin-graph.js").status_code == 404


def test_explore_route_is_admin_only():
    body = {"question": "q", "domains": ["d"]}
    assert TestClient(create_app(admin_routes=False)).post("/api/explore/cypher", json=body).status_code == 404
    assert _admin().post("/api/explore/cypher", json={"question": " ", "domains": ["d"]}).status_code == 422


def test_static_assets_served():
    assert _admin().get("/static/graph.js").status_code == 200


def test_graph_js_never_assigns_innerhtml_from_data():
    text = _admin().get("/static/graph.js").text
    for match in re.finditer(r"innerHTML\s*[+]?=(?!=)\s*(.*)", text):
        assert match.group(1).startswith('"";'), match.group(0)


def test_graph_run_route_is_on_the_admin_console_only():
    body = {"domain": "d"}  # incomplete: 422 proves the route exists without touching Neo4j
    assert _admin().post("/api/graph/run", json=body).status_code == 422
    assert TestClient(create_app(admin_routes=False)).post("/api/graph/run", json=body).status_code == 404


def test_admin_console_has_the_graph_drawer():
    html = TestClient(create_app(template_name="admin.html", admin_routes=True)).get("/").text
    for el_id in ("graph-drawer-toggle", "graph-drawer", "gd-domain", "gd-question", "gd-generate",
                  "gd-cypher", "gd-run", "gd-browser", "gd-status", "gd-canvas"):
        assert f'id="{el_id}"' in html, el_id
    assert html.index("/static/graph-render.js?v=") < html.index("/static/graph-drawer.js?v=") < html.index("/static/app.js?v=")
    assert "graph-drawer.js" not in TestClient(create_app(admin_routes=False)).get("/").text


def test_graph_drawer_never_sends_a_chat_turn_and_never_uses_innerhtml():
    source = _admin().get("/static/graph-drawer.js").text
    assert "innerHTML" not in source
    assert "innerHTML" not in _admin().get("/static/graph.js").text
    assert "/api/chat" not in source and ".submit(" not in source and "requestSubmit" not in source
    assert "window.ArtmindGraphDrawer" in source and '"/api/explore/cypher"' in source
