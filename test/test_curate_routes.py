"""Curate tab routes (spec 2026-11-12 §2): library calls mocked, parameters asserted. Synthetic data only."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from artmind.webui import curate_routes


def prop(pid, canonical, members, source="adjudicator", status="open", reason="same thing"):
    return {"id": pid, "canonical": canonical, "members": members, "source": source, "status": status,
            "reason": reason, "detected_at": "2026-11-01T00:00:00Z"}


A = "Acme Ltd|ORG|demo.one"
B = "ACME Limited|ORG|demo.one"
C = "Acme|ORG|demo.two"
D = "Acme Ltd|PRODUCT|demo.one"


@pytest.fixture
def client():
    app = FastAPI()
    curate_routes.register_curate_routes(app)
    return TestClient(app)


def test_classify_merge_vs_link():
    assert curate_routes.classify_proposal(prop("1", A, [A, B]))["action"] == "merge"
    assert curate_routes.classify_proposal(prop("2", A, [A, C]))["action"] == "link"
    assert curate_routes.classify_proposal(prop("3", A, [A, D]))["action"] == "link"


def test_parse_member_and_domain_match():
    assert curate_routes.parse_member(A) == {"key": A, "name": "Acme Ltd", "class": "ORG", "domain": "demo.one"}
    assert curate_routes.domain_matches("banking.a", "banking")
    assert curate_routes.domain_matches("banking", "banking")
    assert not curate_routes.domain_matches("bankingx", "banking")


def test_list_filters_by_domain_and_source(client, monkeypatch):
    rows = [prop("1", A, [A, B]), prop("2", C, [C, "Other|ORG|elsewhere"], source="refine_graph")]
    seen = {}
    monkeypatch.setattr(curate_routes, "_list_proposals", lambda status: seen.setdefault("status", status) and rows)
    body = client.get("/api/curate/sameas", params={"status": "open", "domain": "demo.one"}).json()
    assert seen["status"] == "open"
    assert [r["id"] for r in body["rows"]] == ["1"]
    assert body["rows"][0]["action"] == "merge" and body["rows"][0]["members"][0]["name"] == "Acme Ltd"
    body = client.get("/api/curate/sameas", params={"source": "refine_graph"}).json()
    assert [r["id"] for r in body["rows"]] == ["2"] and body["rows"][0]["action"] == "link"


def test_approve_passes_parameters(client, monkeypatch):
    calls = []
    monkeypatch.setattr(curate_routes, "_approve_many",
                        lambda ids, canonical, dry_run: calls.append((ids, canonical, dry_run)) or
                        {"approved": [], "skipped": [], "projection": None, "effect": {"summary": "x"}})
    r = client.post("/api/curate/sameas/approve", json={"ids": ["a", "b"], "dry_run": True})
    assert r.status_code == 200 and r.json()["effect"] == {"summary": "x"}
    client.post("/api/curate/sameas/approve", json={"ids": ["a"], "canonical": A, "dry_run": False})
    assert calls == [(["a", "b"], None, True), (["a"], A, False)]


def test_canonical_needs_one_id(client):
    r = client.post("/api/curate/sameas/approve", json={"ids": ["a", "b"], "canonical": A, "dry_run": True})
    assert r.status_code == 400


def test_approve_value_error_is_400(client, monkeypatch):
    def boom(ids, canonical, dry_run):
        raise ValueError("already approved")
    monkeypatch.setattr(curate_routes, "_approve_many", boom)
    r = client.post("/api/curate/sameas/approve", json={"ids": ["a"], "dry_run": True})
    assert r.status_code == 400 and "already approved" in r.json()["detail"]


def test_reject_each_id_with_reason(client, monkeypatch):
    calls = []
    monkeypatch.setattr(curate_routes, "_reject",
                        lambda pid, reason, dry_run: calls.append((pid, reason, dry_run)) or
                        {"id": pid, "status": "rejected", "effect": {"summary": "r"}})
    r = client.post("/api/curate/sameas/reject", json={"ids": ["a", "b"], "reason": "different firms", "dry_run": False})
    assert r.status_code == 200 and len(r.json()["results"]) == 2
    assert calls == [("a", "different firms", False), ("b", "different firms", False)]
    assert client.post("/api/curate/sameas/reject", json={"ids": ["a"], "reason": " ", "dry_run": True}).status_code == 400


def test_evidence_per_member(client, monkeypatch):
    monkeypatch.setattr(curate_routes, "_proposal", lambda pid: prop(pid, A, [A, B]))
    monkeypatch.setattr(curate_routes, "_evidence", lambda key: [{"doc_id": "d", "text": f"about {key}"}])
    body = client.get("/api/curate/sameas/p1/evidence").json()
    assert [m["key"] for m in body["members"]] == [A, B]
    assert body["members"][0]["evidence"][0]["text"] == f"about {A}"
    monkeypatch.setattr(curate_routes, "_proposal", lambda pid: None)
    assert client.get("/api/curate/sameas/zz/evidence").status_code == 404


def test_sweep_dry_run_plans_only(client, monkeypatch):
    monkeypatch.setattr(curate_routes, "_plan_sweep", lambda: {"keys": 3, "rebuilt": 3, "deleted": 0, "absent": 0})
    started = []
    monkeypatch.setattr(curate_routes, "_run_sweep", lambda: started.append(1) or {"keys": 3})
    r = client.post("/api/curate/sweep", json={"dry_run": True})
    assert r.json()["dry_run"] is True and r.json()["plan"]["keys"] == 3 and not started


def test_overview_fails_soft(client, monkeypatch):
    monkeypatch.setattr(curate_routes, "_list_proposals", lambda status: [prop("1", A, [A, B])])

    def bad():
        raise RuntimeError("neo4j down")
    monkeypatch.setattr(curate_routes, "_open_conflicts", bad)
    monkeypatch.setattr(curate_routes, "_projection_status", lambda: {"drift": True})
    body = client.get("/api/curate/overview").json()
    assert body["sameas_open"] == 1 and body["conflicts_open"] is None and body["drift"] is True
    assert any("conflicts" in w for w in body["warnings"])


def test_registered_only_on_admin_app():
    from artmind.webui.app import create_app

    admin = TestClient(create_app(admin_routes=True))
    chat = TestClient(create_app())
    paths = {r.path for r in admin.app.routes}
    assert "/api/curate/overview" in paths
    assert "/api/curate/overview" not in {r.path for r in chat.app.routes}
