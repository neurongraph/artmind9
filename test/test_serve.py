import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from _envelope import plain
from artmind.server import create_app, run_cli_args


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_health_reports_service(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "artmind"
    assert body["status"] == "ok"


def test_health_names_its_vault_and_pid(client, tmp_path):
    with patch("paths.ARTMIND_VAULT_DIR", tmp_path):
        body = client.get("/health").json()

    assert body["vault"] == str(tmp_path)
    assert body["pid"] == os.getpid()


def test_health_vault_is_null_outside_a_vault(client):
    with patch("paths.ARTMIND_VAULT_DIR", None):
        assert client.get("/health").json()["vault"] is None


def test_query_dispatches_through_cli(client):
    payload = {
        "domain": "fiction",
        "query_type": "graph",
        "command": "metadata",
        "rows": [],
    }
    with patch("artmind.cli.graph_query.graph_metadata", return_value=payload) as query:
        resp = client.post(
            "/query",
            json={"args": ["query", "graph", "metadata", "--domain", "fiction"]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["exit_code"] == 0, body
    query.assert_called_once_with(["fiction"])
    assert plain(json.loads(body["output"])) == plain(payload)


def test_non_query_args_rejected(client):
    resp = client.post("/query", json={"args": ["domains", "delete", "fiction"]})

    assert resp.status_code == 400


def test_empty_args_rejected():
    with pytest.raises(ValueError):
        run_cli_args([])


def test_backend_exception_reported_in_stderr():
    with patch(
        "artmind.cli.graph_query.graph_metadata",
        side_effect=RuntimeError("neo4j down"),
    ):
        body = run_cli_args(["query", "graph", "metadata", "--domain", "fiction"])

    assert body["exit_code"] != 0
    assert "neo4j down" in body["stderr"]


def test_previous_override_applies_to_one_request_only(monkeypatch):
    import json as _json

    from artmind.query_plan import previous as previous_mod

    seen = []

    def fake_invoke(self, cli_, args):
        seen.append(previous_mod._context_previous())
        class R:
            exit_code = 0
            stdout = "{}"
            stderr = ""
            exception = None
        return R()

    monkeypatch.setattr("artmind.server.CliRunner.invoke", fake_invoke)
    monkeypatch.setenv("ARTMIND_PREVIOUS_CONTEXT", "/nonexistent")
    run_cli_args(["query", "x"], previous=_json.dumps({"question": "earlier"}))
    run_cli_args(["query", "x"])
    assert seen[0].question == "earlier"
    assert seen[1] is None
    assert previous_mod._previous_override.get() is None
