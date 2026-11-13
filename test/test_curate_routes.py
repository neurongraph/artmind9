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


# ── conflicts (step 2) ───────────────────────────────────────────────
def conflict(cid="c1", docs=("d1", "d2")):
    return {"conflict": {"id": cid, "aspect": "rate", "status": "open", "severity": "high", "claim_a": "5%", "claim_b": "6%"},
            "entities": [{"_id": "e1", "name": "Rate", "entity_class": "TERM", "_domain": "demo.one"}],
            "evidence": [{"side": "a", "doc_id": docs[0], "text": "five"}, {"side": "b", "doc_id": docs[1], "text": "six"}]}


def test_conflicts_list_groups_and_flags_older_version(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(curate_routes, "_list_conflicts", lambda status, domain: seen.update(s=status, d=domain) or [conflict(), conflict("c2")])
    monkeypatch.setattr(curate_routes, "_closed_documents", lambda ids: {"d1"})
    body = client.get("/api/curate/conflicts", params={"status": "open", "domain": "demo.one"}).json()
    assert seen == {"s": "open", "d": "demo.one"}
    assert body["groups"][0]["aspect"] == "rate" and len(body["groups"][0]["rows"]) == 2
    assert body["groups"][0]["rows"][0]["older_side_docs"] == ["d1"]


def test_conflict_close_passes_parameters(client, monkeypatch):
    calls = []
    monkeypatch.setattr(curate_routes, "_resolve_conflict",
                        lambda cid, status, reason, dry_run: calls.append((cid, status, reason, dry_run)) or {"effect": {"summary": "s"}})
    assert client.post("/api/curate/conflicts/c1/close", json={"status": "dismissed", "reason": "same fact", "dry_run": True}).json()["effect"] == {"summary": "s"}
    client.post("/api/curate/conflicts/c1/close", json={"status": "resolved", "reason": "newer wins", "dry_run": False})
    assert calls == [("c1", "dismissed", "same fact", True), ("c1", "resolved", "newer wins", False)]
    assert client.post("/api/curate/conflicts/c1/close", json={"status": "resolved", "reason": "", "dry_run": True}).status_code == 400


# ── tables (step 4) ──────────────────────────────────────────────────
def test_table_mapping_dry_run_states_change_and_writes_nothing(client, monkeypatch):
    writes = []
    monkeypatch.setattr(curate_routes, "_table_write", lambda *a: writes.append(a))
    r = client.post("/api/curate/tables/t1/mapping", json={"domain": "demo", "action": "clear", "column": "c", "dry_run": True}).json()
    assert "every class" in r["effect"]["summary"] and not writes


def test_table_mapping_real_write_parameters(client, monkeypatch):
    writes = []
    monkeypatch.setattr(curate_routes, "_table_write", lambda *a: writes.append(a) or {"ok": True})
    client.post("/api/curate/tables/t1/mapping", json={"domain": "demo", "action": "confirm", "column": "c", "entity_class": "ORG", "dry_run": False})
    client.post("/api/curate/tables/t1/grain", json={"domain": "demo", "grain": "lookup", "dry_run": False})
    client.post("/api/curate/tables/t1/bridge", json={"domain": "demo", "action": "clear", "column": "c", "dry_run": False})
    assert writes == [("mapping", "t1", "demo", "confirm", "c", "ORG"), ("grain", "t1", "demo", "set", None, "lookup"),
                      ("bridge", "t1", "demo", "clear", "c", None)]


def test_table_bad_action_is_400(client):
    assert client.post("/api/curate/tables/t1/mapping", json={"domain": "d", "action": "nuke", "column": "c", "dry_run": True}).status_code == 400
    assert client.post("/api/curate/tables/t1/mapping", json={"domain": "d", "action": "confirm", "column": "c", "dry_run": True}).status_code == 400  # needs entity_class


def test_tables_list_joins_samples(client, monkeypatch):
    monkeypatch.setattr(curate_routes, "_cli", lambda args: {"tables": [{"table": "t1", "domain": "demo", "mappings": [{"column": "c"}]}]} if args[:2] == ["db", "review"]
                        else {"tables": [{"table_name": "t1", "columns": [{"name": "c", "profile_json": "{\"distinct_sample\": [\"x\", \"y\"]}"}]}]})
    body = client.get("/api/curate/tables", params={"domain": "demo"}).json()
    assert body["tables"][0]["samples"] == {"c": ["x", "y"]}


def test_catalogue_runs_cli(client, monkeypatch):
    seen = []
    monkeypatch.setattr(curate_routes, "_cli", lambda args: seen.append(args) or {"ok": 1})
    client.post("/api/curate/catalogue", json={"domain": "demo"})
    assert seen == [["db", "catalogue", "--domain", "demo"]]


# ── mart (step 5) ───────────────────────────────────────────
def test_mart_status_and_member_set(client, monkeypatch):
    seen = []
    monkeypatch.setattr(curate_routes, "_cli", lambda args: seen.append(args) or {"effect": {"summary": "e"}, "rows": []})
    client.get("/api/curate/mart", params={"domain": "demo"})
    client.post("/api/curate/mart/member", json={"domain": "demo", "dimension": "place", "member": "Paris|PLACE|demo", "levels": {"country": "FR"}, "dry_run": True})
    client.post("/api/curate/mart/build", json={"domain": "demo", "dry_run": False})
    assert seen[:2] == [["mart", "status", "--domain", "demo"], ["mart", "review", "list", "--domain", "demo"]]
    assert seen[2] == ["mart", "review", "set", "--domain", "demo", "--dimension", "place", "--member", "Paris|PLACE|demo",
                       "--level", "country=FR", "--dryRun"]
    assert seen[3] == ["mart", "build", "--domain", "demo"]
