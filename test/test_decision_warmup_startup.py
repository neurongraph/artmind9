"""serve, chat-ui and admin-ui warm the decision model up once at start-up (spec 2026-10-22 §7)."""

import pytest
from fastapi.testclient import TestClient

from artmind import server
from artmind.query_plan import classify
from artmind.webui import app as webui_app
from artmind.webui.sessions import SessionRegistry


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


@pytest.fixture
def warm_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(classify, "warm_up", lambda env=None: calls.append(env))
    return calls


def test_serve_warms_up_once_at_start(warm_calls):
    with TestClient(server.create_app()) as client:
        client.get("/health")
        client.get("/health")
    assert warm_calls == [None]


def test_the_web_uis_warm_up_once_at_start(warm_calls):
    app = webui_app.create_app(registry=SessionRegistry(client_factory=object))
    with TestClient(app):
        pass
    assert warm_calls == [None]


def test_a_failing_warm_up_never_stops_start_up(monkeypatch):
    def boom(env=None):
        raise RuntimeError("no endpoint")

    monkeypatch.setattr(classify, "warm_up", boom)
    with TestClient(server.create_app()) as client:
        assert client.get("/health").status_code == 200
    with TestClient(webui_app.create_app(registry=SessionRegistry(client_factory=object))):
        pass
