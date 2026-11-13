"""Markup and asset wiring for Explore -> Graph and the admin chat's "View as graph" link."""

import re

from fastapi.testclient import TestClient

from artmind.webui.app import create_app


def _admin() -> TestClient:
    return TestClient(create_app(admin_routes=True))


def test_dashboard_has_graph_subtab_and_script():
    html = _admin().get("/dashboard").text
    assert 'data-tab="graph"' in html and 'data-tab-panel="graph"' in html
    assert "graph.js" in html
    for el_id in ("graph-domain", "graph-question", "graph-generate-btn", "graph-cypher", "graph-open-btn"):
        assert f'id="{el_id}"' in html


def test_admin_console_loads_chat_link_script_but_chat_ui_does_not():
    console = TestClient(create_app(template_name="admin.html", admin_routes=True))
    assert "admin-graph.js" in console.get("/").text
    assert "admin-graph.js" not in TestClient(create_app(admin_routes=False)).get("/").text


def test_explore_route_is_admin_only():
    body = {"question": "q", "domains": ["d"]}
    assert TestClient(create_app(admin_routes=False)).post("/api/explore/cypher", json=body).status_code == 404
    assert _admin().post("/api/explore/cypher", json={"question": " ", "domains": ["d"]}).status_code == 422


def test_static_assets_served():
    client = _admin()
    for name in ("graph.js", "admin-graph.js"):
        assert client.get(f"/static/{name}").status_code == 200


def test_graph_js_never_assigns_innerhtml_from_data():
    client = _admin()
    for name in ("graph.js", "admin-graph.js"):
        for match in re.finditer(r"innerHTML\s*[+]?=(?!=)\s*(.*)", client.get(f"/static/{name}").text):
            assert match.group(1).startswith('"";'), (name, match.group(0))
