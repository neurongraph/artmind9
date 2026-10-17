"""Host-header allow-list on the chat-ui / admin-ui servers (DNS-rebinding guard)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from artmind.webui import app as webui_app
from artmind.webui.hosts import exposure_warning, guard_app, hostname_of, is_loopback_bind


def _client(host: str = "127.0.0.1", **create_kwargs) -> tuple[TestClient, list[str]]:
    app = webui_app.create_app(**create_kwargs)
    warnings: list[str] = []
    guard_app(app, host, 8378, echo=warnings.append)
    return TestClient(app), warnings


@pytest.mark.parametrize("host", ["127.0.0.1:8378", "localhost:8378", "localhost", "[::1]:8378", "LOCALHOST:8378"])
def test_loopback_hosts_allowed(host):
    client, warnings = _client()
    assert client.get("/api/health", headers={"Host": host}).status_code == 200
    assert warnings == []


@pytest.mark.parametrize(
    "host",
    ["evil.example", "evil.example:8378", "127.0.0.1.evil.example", "localhost.evil.example", "192.168.1.5:8378", ""],
)
def test_foreign_host_rejected(host):
    client, _ = _client()
    for path in ("/api/health", "/"):
        response = client.get(path, headers={"Host": host})
        assert response.status_code == 400, (host, path)


def test_foreign_host_rejected_on_admin_ui_routes():
    client, _ = _client(template_name="admin.html", admin_routes=True)
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "localhost:8379"}).json()["app"] == "admin-ui"


def test_plugin_probe_urls_work():
    """obsidian/artmind-obsidian probes /api/health and /?prompt= at http://127.0.0.1:<port> (or localhost)."""
    client, _ = _client(template_name="admin.html", admin_routes=True)
    for host in ("127.0.0.1:8379", "localhost:8379"):
        assert client.get("/api/health", headers={"Host": host}).status_code == 200
        assert client.get("/?prompt=hello", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.5", "::", "myhost.local"])
def test_non_loopback_bind_allows_all_hosts_and_warns(bind):
    client, warnings = _client(host=bind)
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 200
    assert len(warnings) == 1
    assert bind in warnings[0] and "no authentication" in warnings[0] and "conversation history" in warnings[0]


@pytest.mark.parametrize("bind", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.2"])
def test_loopback_bind_detected(bind):
    assert is_loopback_bind(bind)
    assert exposure_warning(bind, 1) is None


def test_hostname_of():
    assert hostname_of("localhost:8378") == "localhost"
    assert hostname_of("[::1]:8378") == "[::1]"
    assert hostname_of("[::1]") == "[::1]"
    assert hostname_of("Localhost.") == "localhost"
    assert hostname_of("") == ""


@pytest.mark.parametrize("runner", ["run_chat_ui", "run_admin_ui"])
def test_runners_guard_the_app_they_serve(runner):
    served = {}
    with (
        patch("uvicorn.run", side_effect=lambda app, **kw: served.update(app=app, **kw)),
        patch.object(webui_app, "SessionRegistry"),
        patch.object(webui_app, "backend_factory"),
    ):
        getattr(webui_app, runner)(host="127.0.0.1", port=9999)
    client = TestClient(served["app"])
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "127.0.0.1:9999"}).status_code == 200


def test_runner_non_loopback_bind_warns_on_stderr(capsys):
    with patch("uvicorn.run"):
        webui_app.run_chat_ui(host="0.0.0.0", port=9999)
    err = capsys.readouterr().err
    assert "0.0.0.0:9999" in err and "no authentication" in err
