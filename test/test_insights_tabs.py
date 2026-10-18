"""Markup and asset wiring for the admin console's Insights and Views tabs."""

import re

from fastapi.testclient import TestClient

from artmind.webui.app import create_app


def _client() -> TestClient:
    return TestClient(create_app(admin_routes=True))


def test_dashboard_has_tabs_and_scripts():
    html = _client().get("/dashboard").text
    assert 'data-tab="insights"' in html
    assert 'data-tab="views"' in html
    assert 'data-tab-panel="insights"' in html
    assert 'data-tab-panel="views"' in html
    assert "insights.js" in html
    assert "views.js" in html
    assert "vendor/marked.min.js" in html
    assert "vendor/purify.min.js" in html


def test_static_assets_served():
    client = _client()
    for name in ("insights.js", "views.js"):
        assert client.get(f"/static/{name}").status_code == 200


def test_insights_js_never_assigns_innerhtml_from_data():
    text = _client().get("/static/insights.js").text
    for match in re.finditer(r"innerHTML\s*[+]?=(?!=)\s*(.*)", text):
        assert match.group(1).startswith('"";'), match.group(0)


def test_views_js_only_sanitized_innerhtml():
    text = _client().get("/static/views.js").text
    for match in re.finditer(r"innerHTML\s*[+]?=(?!=)\s*(.*)", text):
        rhs = match.group(1)
        assert rhs.startswith('"";') or "DOMPurify.sanitize" in rhs, match.group(0)
