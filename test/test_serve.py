import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

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
    query.assert_called_once_with(["fiction"], as_of=None)
    assert json.loads(body["output"]) == payload


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
