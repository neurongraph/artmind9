import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import paths
from artmind.insights import store
from artmind.insights.model import Finding, Run
from artmind.webui import insights_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "DOMAIN_VIEWS_DIR", tmp_path / "views")
    app = FastAPI()
    insights_routes.register_insights_routes(app)
    return TestClient(app)


def write_run(created="2026-10-17T09:00:00Z"):
    cands = [Finding(key="same_as_candidates:PERSON:a:b", detector="same_as_candidates", kind="health", score=0.9,
                     title="t", action="curate", stats={"canonical": {"_id": "a", "name": "Dad", "class": "PERSON"},
                                                        "alias": {"_id": "b", "name": "Daddy", "class": "PERSON"},
                                                        "rule": "kinship"})]
    run = Run(domain="demo", created_at=created, as_of="2026-10-01", fingerprint={}, anchors=[], profile={"big": 1},
              plan=[], circles=[{"id": "PERSON:a", "name": "Family", "name_source": "llm", "members": ["a", "b"]}],
              diff={"new": 1}, findings=cands + [Finding(key="g", detector="dormancy", kind="corpus", score=0.1,
                                                         title="gone", diff="gone")])
    store.write_run(run)


def test_insights_snapshot_is_trimmed(client):
    write_run()
    r = client.get("/api/insights", params={"domain": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == "2026-10-01" and "profile" not in body
    assert [f["key"] for f in body["findings"]] == ["same_as_candidates:PERSON:a:b"]   # gone excluded
    assert body["circles"][0]["name"] == "Family" and body["diff"] == {"new": 1}


def test_missing_snapshot_and_bad_domain(client):
    assert client.get("/api/insights", params={"domain": "demo"}).status_code == 404
    assert client.get("/api/insights", params={"domain": "../x"}).status_code == 400


def test_runs(client):
    write_run()
    assert client.get("/api/insights/runs", params={"domain": "demo"}).json()["runs"][0]["created_at"] == "2026-10-17T09:00:00Z"


def test_refresh_runs_in_background_and_reports_progress(client, monkeypatch):
    calls = []
    monkeypatch.setattr(insights_routes, "_refresh", lambda domain, force: calls.append((domain, force)) or {"status": "written"})
    r = client.post("/api/insights/refresh", json={"domain": "demo", "force": True})
    assert r.status_code == 202
    insights_routes._join_refresh("demo")
    assert calls == [("demo", True)]
    assert client.get("/api/insights/refresh/progress", params={"domain": "demo"}).json()["state"] == "done"


def test_send_to_curation_proposes_guarded_merges(client, monkeypatch):
    write_run()
    seen = {}
    monkeypatch.setattr(insights_routes, "_propose", lambda merges, domain: seen.update(merges=merges, domain=domain)
                        or {"stats": {"proposed": 1}})
    r = client.post("/api/insights/same-as/propose", json={"domain": "demo", "keys": ["same_as_candidates:PERSON:a:b"]})
    assert r.status_code == 200 and seen == {"merges": {"Daddy": "Dad"}, "domain": "demo"}
    assert r.json()["stats"] == {"proposed": 1}


def test_views_catalogue_detail_and_run(client, tmp_path, monkeypatch):
    v = tmp_path / "views" / "people"
    v.mkdir(parents=True)
    (v / "view.yaml").write_text(
        "name: people\nversion: 1\ndomains: [demo]\nsummary: People\nexamples: [\"who\"]\nparams: {}\n"
        "cypher: |\n  MATCH (e:Entity) WHERE e._domain IN $domains RETURN e.name AS name\n"
        "presentation:\n  format: list\n  list: {label: name}\n"
        "provenance: {created: 2026-10-18, origin: authored, source_question: null}\n")
    (v / "SKILL.md").write_text("---\nname: people\ndescription: d\n---\n# people\n")
    cat = client.get("/api/views", params={"domain": "demo"}).json()
    assert [x["name"] for x in cat["views"]] == ["people"]
    assert client.get("/api/views/people").json()["skill_md"].startswith("---")
    monkeypatch.setattr(insights_routes, "_run_view", lambda spec, domains, params: {"status": "ok", "rendered": "- A"})
    out = client.post("/api/views/people/run", json={"domain": "demo", "params": {}}).json()
    assert out == {"status": "ok", "rendered": "- A"}
    assert client.get("/api/views/nope").status_code == 404


def test_refresh_errors_are_reported_and_running_blocks_a_second(client, monkeypatch):
    import threading

    gate = threading.Event()
    def slow(domain, force):
        gate.wait(5)
        raise RuntimeError("graph unavailable")
    monkeypatch.setattr(insights_routes, "_refresh", slow)
    assert client.post("/api/insights/refresh", json={"domain": "demo2"}).status_code == 202
    assert client.post("/api/insights/refresh", json={"domain": "demo2"}).status_code == 409
    gate.set()
    insights_routes._join_refresh("demo2")
    state = client.get("/api/insights/refresh/progress", params={"domain": "demo2"}).json()
    assert state == {"state": "error", "detail": "graph unavailable"}


def test_refresh_rejects_bad_domain_names(client):
    assert client.post("/api/insights/refresh", json={"domain": "../etc"}).status_code == 400


def test_propose_checks_ambiguous_names_across_the_whole_snapshot(client, monkeypatch):
    def cand(key, canonical, alias, alias_id):
        return Finding(key=key, detector="same_as_candidates", kind="health", score=0.9, title="t", action="curate",
                       stats={"canonical": {"_id": canonical.lower(), "name": canonical, "class": "PERSON"},
                              "alias": {"_id": alias_id, "name": alias, "class": "PERSON"}, "rule": "token_subset"})
    run = Run(domain="demo", created_at="2026-10-17T09:00:00Z", as_of="2026-10-01", fingerprint={}, anchors=[],
              profile={}, plan=[], circles=[], diff={},
              findings=[cand("k1", "Mark Lee", "Mark", "m1"), cand("k2", "Mark Ode", "Mark", "m2")])
    store.write_run(run)
    called = []
    monkeypatch.setattr(insights_routes, "_propose", lambda merges, domain: called.append(merges) or {"stats": {}})
    out = client.post("/api/insights/same-as/propose", json={"domain": "demo", "keys": ["k1"]}).json()
    assert called == [] and out["skipped"]["ambiguous_name"] == 1
