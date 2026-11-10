"""The admin console's Topics tab routes (spec 2026-11-10 §8.3): plans and pages from disk."""
from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from artmind.topics import plan as model
from artmind.topics import plan_store, store
from artmind.webui.topics_routes import register_topics_routes


def test_lists_plans_with_stored_freshness_and_pages(tmp_path, monkeypatch):
    import json

    import paths

    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path / "_Topics")
    spec = model.PlanSpec(slug="garden", topic="Garden", version=1, domains=("j",), subjects=("tomato planting",))
    plan_store.write_plan(replace(spec, built={}), model.default_skill_md(spec))
    (tmp_path / "logs" / "topic-freshness").mkdir(parents=True)
    (tmp_path / "logs" / "topic-freshness" / "garden.json").write_text(json.dumps(
        {"status": "stale", "auto_refresh": {"status": "skipped", "reason": "above the cap"}}))
    store.write_page(tmp_path / "_Topics", "garden", {"artmind_topic": 1, "topic": "Garden", "slug": "garden",
                                                      "built": {"at": "t"}, "sources": [{"doc_id": "D1"}]}, "## Overview\n\nx")
    client = TestClient(register_topics_routes(FastAPI()))
    data = client.get("/api/topics").json()
    assert data["plans"][0]["slug"] == "garden" and data["plans"][0]["freshness"]["auto_refresh"]["status"] == "skipped"
    assert data["pages"] == [{"slug": "garden", "topic": "Garden", "domains": [], "built_at": "t", "sources": 1}]
    assert client.get("/api/topics/garden/page").json()["body"].startswith("## Overview")
    assert client.get("/api/topics/missing/page").status_code == 404
    assert client.get("/api/topics/..%2Fx/page").status_code == 404
