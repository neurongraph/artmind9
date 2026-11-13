import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from artmind.webui import explore_routes

CYPHER = "MATCH (a:Entity)-[r]->(b) WHERE a._domain IN $domains RETURN a, r, b\nLIMIT 100"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(explore_routes, "_generate",
                        lambda question, domains: {"cypher": CYPHER, "parameters": {"domains": domains}})
    monkeypatch.setattr(explore_routes, "_browser_url", lambda: "http://127.0.0.1:7476/browser/")
    app = FastAPI()
    explore_routes.register_explore_routes(app)
    return TestClient(app)


def test_cypher_returns_inlined_query_and_deep_link(client):
    r = client.post("/api/explore/cypher", json={"question": " who reports to whom? ", "domains": ["bank, ops"]})
    assert r.status_code == 200
    body = r.json()
    assert body["question"] == "who reports to whom?" and body["domains"] == ["bank", "ops"]
    assert body["cypher"] == CYPHER and "$domains" in body["cypher"]
    assert "$" not in body["standalone_cypher"] and "['bank', 'ops']" in body["standalone_cypher"]
    assert body["browser_url"].startswith("http://127.0.0.1:7476/browser/?cmd=edit&arg=")


def test_no_browser_url_configured_gives_null_link(client, monkeypatch):
    monkeypatch.setattr(explore_routes, "_browser_url", lambda: None)
    body = client.post("/api/explore/cypher", json={"question": "q", "domains": ["d"]}).json()
    assert body["browser_url"] is None and body["standalone_cypher"]


def test_bad_browser_url_config_is_a_400(client, monkeypatch):
    monkeypatch.setattr(explore_routes, "_browser_url", lambda: "javascript:alert(1)")
    r = client.post("/api/explore/cypher", json={"question": "q", "domains": ["d"]})
    assert r.status_code == 400 and "http(s)" in r.json()["detail"]


@pytest.mark.parametrize("payload", [
    {"question": "   ", "domains": ["d"]},
    {"question": "x" * 2001, "domains": ["d"]},
    {"domains": ["d"]},
])
def test_invalid_question_is_422(client, payload):
    assert client.post("/api/explore/cypher", json=payload).status_code == 422


def test_empty_domains_is_400(client):
    r = client.post("/api/explore/cypher", json={"question": "q", "domains": [" , "]})
    assert r.status_code == 400


def test_generation_refusal_is_400_and_other_failures_502(client, monkeypatch):
    def refuse(question, domains):
        raise ValueError("Generated Cypher does not reference $domains")
    monkeypatch.setattr(explore_routes, "_generate", refuse)
    assert client.post("/api/explore/cypher", json={"question": "q", "domains": ["d"]}).status_code == 400

    def boom(question, domains):
        raise RuntimeError("llm down")
    monkeypatch.setattr(explore_routes, "_generate", boom)
    r = client.post("/api/explore/cypher", json={"question": "q", "domains": ["d"]})
    assert r.status_code == 502 and "llm down" in r.json()["detail"]


def test_link_for_edited_cypher(client):
    r = client.post("/api/explore/cypher/link", json={"cypher": "MATCH (n) RETURN n LIMIT 5"})
    assert r.status_code == 200
    assert r.json()["browser_url"].startswith("http://127.0.0.1:7476/browser/?cmd=edit&arg=")


@pytest.mark.parametrize("cypher", [
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) SET n.x = 1 RETURN n",
    "CALL db.labels() YIELD label RETURN label",
    "MATCH (n) WHERE n.x = $x RETURN n",   # parameters can't be supplied for an edited query
])
def test_link_refuses_writes_calls_and_unbound_params(client, cypher):
    assert client.post("/api/explore/cypher/link", json={"cypher": cypher}).status_code == 400


def test_link_empty_cypher_is_422(client):
    assert client.post("/api/explore/cypher/link", json={"cypher": "  "}).status_code == 422
