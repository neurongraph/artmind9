"""Route tests for the Lane B admin dashboard JSON endpoints."""

import io
import json
import zipfile

from fastapi.testclient import TestClient

from artmind.webui import dashboard_routes
from artmind.webui.app import create_app


def _client() -> TestClient:
    return TestClient(create_app(admin_routes=True))


def test_admin_routes_absent_from_qa_app():
    client = TestClient(create_app())
    assert client.get("/api/jobs").status_code == 404
    assert client.get("/dashboard").status_code == 404


def test_dashboard_page_serves_html():
    response = _client().get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_list_jobs_camelizes_keys(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes,
        "_list_jobs",
        lambda status_filter=None: [
            {"job_id": "j1", "status": "queued", "file_count": 2, "processed_count": 0, "queued_at": "t"}
        ],
    )
    response = _client().get("/api/jobs")
    assert response.status_code == 200
    assert response.json() == [
        {"jobId": "j1", "status": "queued", "fileCount": 2, "processedCount": 0, "queuedAt": "t"}
    ]


def test_jobs_active(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_fetch_active_jobs", lambda: [{"job_id": "j1"}])
    response = _client().get("/api/jobs/active")
    assert response.status_code == 200
    assert response.json() == [{"jobId": "j1"}]


def test_jobs_completed_passes_limit(monkeypatch):
    seen = {}

    def fake(limit=100):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(dashboard_routes, "_fetch_completed_jobs", fake)
    response = _client().get("/api/jobs/completed?limit=5")
    assert response.status_code == 200
    assert seen["limit"] == 5


def test_job_status_found(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_get_job_status", lambda job_id: {"job_id": job_id, "status": "processing"})
    response = _client().get("/api/jobs/abc")
    assert response.status_code == 200
    assert response.json() == {"jobId": "abc", "status": "processing"}


def test_job_status_404(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_get_job_status", lambda job_id: None)
    response = _client().get("/api/jobs/nope")
    assert response.status_code == 404


def test_job_results_found(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_get_job_results", lambda job_id: {"job_id": job_id, "files": []})
    response = _client().get("/api/jobs/abc/results")
    assert response.status_code == 200
    assert response.json() == {"jobId": "abc", "files": []}


def test_job_results_404(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_get_job_results", lambda job_id: None)
    response = _client().get("/api/jobs/nope/results")
    assert response.status_code == 404


def test_retry_job_success_starts_worker(monkeypatch):
    started = {"called": False}
    monkeypatch.setattr(
        dashboard_routes,
        "_retry_job",
        lambda job_id, include_skipped=False: {
            "job_id": job_id, "domain": "general", "retried": 2, "deregistered": 1, "files": ["a"],
        },
    )
    monkeypatch.setattr(dashboard_routes, "_ensure_worker_running", lambda: started.__setitem__("called", True))
    response = _client().post("/api/jobs/abc/retry", json={"includeSkipped": True})
    assert response.status_code == 200
    assert response.json()["retried"] == 2
    assert started["called"] is True


def test_retry_job_not_found_is_400(monkeypatch):
    def fake(job_id, include_skipped=False):
        raise ValueError(f"Job '{job_id}' not found")

    monkeypatch.setattr(dashboard_routes, "_retry_job", fake)
    response = _client().post("/api/jobs/nope/retry", json={})
    assert response.status_code == 400


def test_ingest_missing_path_is_400():
    response = _client().post("/api/ingest", json={"domain": "general", "path": "/no/such/path"})
    assert response.status_code == 400


def test_ingest_creates_job(monkeypatch, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    monkeypatch.setattr(
        dashboard_routes, "_create_job", lambda batch_files, domain, stage_only=False: "job-123"
    )
    monkeypatch.setattr(dashboard_routes, "_ensure_worker_running", lambda: None)
    response = _client().post("/api/ingest", json={"domain": "general", "path": str(f)})
    assert response.status_code == 200
    body = response.json()
    assert body["jobId"] == "job-123"
    assert body["fileCount"] == 1


def test_ingest_rejects_traversal_domain(monkeypatch, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    called = {}
    monkeypatch.setattr(
        dashboard_routes, "_create_job",
        lambda batch_files, domain: called.setdefault("called", True) or "job-123",
    )
    monkeypatch.setattr(dashboard_routes, "_ensure_worker_running", lambda: None)
    response = _client().post("/api/ingest", json={"domain": "..", "path": str(f)})
    assert response.status_code == 400
    assert "called" not in called


def test_ingest_passes_stage_only_to_create_job(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes

    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")

    seen = {}

    def fake_create_job(batch_files, domain="general", force=False, stage_only=False):
        seen["stage_only"] = stage_only
        seen["domain"] = domain
        return "job-1"

    monkeypatch.setattr(dashboard_routes, "_create_job", fake_create_job)
    monkeypatch.setattr(dashboard_routes, "_ensure_worker_running", lambda: None)

    resp = _client().post(
        "/api/ingest",
        json={"domain": "mydomain", "path": str(f), "stageOnly": True},
    )
    assert resp.status_code == 200
    assert seen["stage_only"] is True
    assert seen["domain"] == "mydomain"


def test_ingest_defaults_stage_only_false(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes

    f = tmp_path / "b.txt"
    f.write_text("x", encoding="utf-8")

    seen = {}

    def fake_create_job(batch_files, domain="general", force=False, stage_only=False):
        seen["stage_only"] = stage_only
        return "job-2"

    monkeypatch.setattr(dashboard_routes, "_create_job", fake_create_job)
    monkeypatch.setattr(dashboard_routes, "_ensure_worker_running", lambda: None)

    resp = _client().post("/api/ingest", json={"domain": "mydomain", "path": str(f)})
    assert resp.status_code == 200
    assert seen["stage_only"] is False


def test_domains(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_get_available_domains", lambda: ["general", "banking"])
    response = _client().get("/api/domains")
    assert response.status_code == 200
    assert response.json() == ["general", "banking"]


def test_stats_splits_comma_domains(monkeypatch):
    seen = {}

    def fake(domains):
        seen["domains"] = domains
        return {"Document": 3}

    monkeypatch.setattr(dashboard_routes, "structural_metadata", fake)
    response = _client().get("/api/stats?domain=general,banking")
    assert response.status_code == 200
    assert seen["domains"] == ["general", "banking"]
    assert response.json() == {"Document": 3}


def test_embed_entities(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "embed_entities_backfill", lambda domain: {"domain": domain, "embedded_count": 5})
    response = _client().post("/api/embed-entities", json={"domain": "general"})
    assert response.status_code == 200
    assert response.json() == {"domain": "general", "embeddedCount": 5}


def test_job_chunks_returns_grid(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_get_job_status", lambda job_id: {"job_id": job_id, "domain": "general"})
    monkeypatch.setattr(dashboard_routes, "_build_file_result_from_db", lambda doc, domain: {"sha256": "abc123"})
    monkeypatch.setattr(dashboard_routes, "_fetch_chunks", lambda sha: [{"seq": 1, "e": "ok", "p": "ok", "r": "failed"}])
    response = _client().get("/api/jobs/j1/chunks?doc=myfile.pdf")
    assert response.status_code == 200
    assert response.json() == [{"seq": 1, "e": "ok", "p": "ok", "r": "failed"}]


def test_job_chunks_job_not_found(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_get_job_status", lambda job_id: None)
    response = _client().get("/api/jobs/nope/chunks?doc=myfile.pdf")
    assert response.status_code == 404


def test_job_chunks_doc_not_registered(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_get_job_status", lambda job_id: {"job_id": job_id, "domain": "general"})
    monkeypatch.setattr(dashboard_routes, "_build_file_result_from_db", lambda doc, domain: None)
    response = _client().get("/api/jobs/j1/chunks?doc=nope.pdf")
    assert response.status_code == 404


def test_resume_extract_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_routes,
        "_build_file_result_from_db",
        lambda doc, domain: {"sha256": "abc", "chunks_dir": str(tmp_path), "chunk_count": 3},
    )
    monkeypatch.setattr(dashboard_routes, "load_env", lambda: {})
    monkeypatch.setattr(dashboard_routes, "resolve_llm_model", lambda env: "some-model")
    monkeypatch.setattr(dashboard_routes, "extract_kg", lambda file_result, domain, text_model, embed_model: tmp_path)
    response = _client().post("/api/documents/myfile.pdf/resume-extract", json={"domain": "general"})
    assert response.status_code == 200
    body = response.json()
    assert body["doc"] == "myfile.pdf"
    assert body["kgDir"] == str(tmp_path)


def test_resume_extract_doc_not_found(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "_build_file_result_from_db", lambda doc, domain: None)
    response = _client().post("/api/documents/nope.pdf/resume-extract", json={"domain": "general"})
    assert response.status_code == 404


def test_resume_extract_rejects_traversal_domain(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(
        dashboard_routes, "_build_file_result_from_db",
        lambda doc, domain: called.setdefault("called", True) or None,
    )
    monkeypatch.setattr(
        dashboard_routes, "extract_kg",
        lambda file_result, domain, text_model, embed_model: called.setdefault("extracted", True) or tmp_path,
    )
    response = _client().post("/api/documents/myfile.pdf/resume-extract", json={"domain": ".."})
    assert response.status_code == 400
    assert "called" not in called
    assert "extracted" not in called


def test_resume_extract_rejects_traversal_doc(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(
        dashboard_routes, "_build_file_result_from_db",
        lambda doc, domain: called.setdefault("called", True) or None,
    )
    monkeypatch.setattr(
        dashboard_routes, "extract_kg",
        lambda file_result, domain, text_model, embed_model: called.setdefault("extracted", True) or tmp_path,
    )
    response = _client().post("/api/documents/%2e%2e/resume-extract", json={"domain": "general"})
    assert response.status_code == 400
    assert "called" not in called
    assert "extracted" not in called


def test_resume_extract_no_chunks_is_400(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes,
        "_build_file_result_from_db",
        lambda doc, domain: {"sha256": "abc", "chunks_dir": "/nowhere", "chunk_count": 0},
    )
    response = _client().post("/api/documents/myfile.pdf/resume-extract", json={"domain": "general"})
    assert response.status_code == 400


def test_resume_extract_failure_is_400(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_routes,
        "_build_file_result_from_db",
        lambda doc, domain: {"sha256": "abc", "chunks_dir": str(tmp_path), "chunk_count": 1},
    )
    monkeypatch.setattr(dashboard_routes, "load_env", lambda: {})
    monkeypatch.setattr(dashboard_routes, "resolve_llm_model", lambda env: "some-model")
    monkeypatch.setattr(dashboard_routes, "extract_kg", lambda file_result, domain, text_model, embed_model: None)
    response = _client().post("/api/documents/myfile.pdf/resume-extract", json={"domain": "general"})
    assert response.status_code == 400


def _write_doc_kg_dir(base, domain, doc, name, entities=0, properties=0, relationships=0):
    doc_dir = base / domain / doc
    doc_dir.mkdir(parents=True)
    (doc_dir / "document.json").write_text(json.dumps({"name": name}))
    (doc_dir / "entities.json").write_text(json.dumps([{}] * entities))
    (doc_dir / "properties.json").write_text(json.dumps([{}] * properties))
    (doc_dir / "relationships.json").write_text(json.dumps([{}] * relationships))
    return doc_dir


def test_artifacts_missing_domain_dir_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    response = _client().get("/api/artifacts?domain=nope")
    assert response.status_code == 200
    assert response.json() == []


def test_artifacts_lists_docs_with_counts_and_in_graph_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    _write_doc_kg_dir(tmp_path, "general", "doc1", "doc1.pdf", entities=2, properties=1, relationships=3)
    monkeypatch.setattr(
        dashboard_routes,
        "structural_metadata",
        lambda domains: {"rows": [{"label": "Document", "count": 1, "names": ["DOC1.PDF"]}]},
    )
    response = _client().get("/api/artifacts?domain=general")
    assert response.status_code == 200
    assert response.json() == [{
        "doc": "doc1", "name": "doc1.pdf",
        "entityCount": 2, "propertyCount": 1, "relationshipCount": 3,
        "inGraph": True,
    }]


def test_artifacts_rejects_traversal_domain(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    response = _client().get("/api/artifacts?domain=..")
    assert response.status_code == 400


def test_artifact_bundle_downloads_zip(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    doc_dir = _write_doc_kg_dir(tmp_path, "general", "doc1", "doc1.pdf")
    response = _client().get("/api/artifacts/general/doc1/bundle")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(response.content))
    assert "document.json" in zf.namelist()


def test_artifact_bundle_404_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    response = _client().get("/api/artifacts/general/nope/bundle")
    assert response.status_code == 404


def test_artifact_bundle_rejects_traversal_domain(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    # ".." submitted as a URL-encoded path segment (%2e%2e) reaches the handler
    # as domain=".." — a literal ".." in the request path gets normalized away
    # before routing, so this is the realistic attack shape to test.
    response = _client().get("/api/artifacts/%2e%2e/doc1/bundle")
    assert response.status_code == 400


def test_artifact_bundle_rejects_traversal_doc(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    response = _client().get("/api/artifacts/general/%2e%2e/bundle")
    assert response.status_code == 400


def test_artifact_import_writes_graph(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    monkeypatch.setattr(dashboard_routes, "commit_to_graph", lambda doc_dir, domain: True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("document.json", json.dumps({"name": "doc1.pdf"}))
    buf.seek(0)
    response = _client().post(
        "/api/artifacts/import",
        data={"domain": "general", "doc": "doc1"},
        files={"file": ("bundle.zip", buf, "application/zip")},
    )
    assert response.status_code == 200
    assert response.json() == {"domain": "general", "doc": "doc1", "written": True}
    assert (tmp_path / "general" / "doc1" / "document.json").exists()


def test_bundle_import_uses_commit_to_graph(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)

    def fake_commit(doc_kg_dir, domain):
        seen["domain"] = domain
        return True

    monkeypatch.setattr(dashboard_routes, "commit_to_graph", fake_commit)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("document.json", json.dumps({"id": "d1", "name": "f.md"}))
    buf.seek(0)

    response = _client().post(
        "/api/artifacts/import",
        data={"domain": "mydomain", "doc": "f"},
        files={"file": ("bundle.zip", buf, "application/zip")},
    )
    assert response.status_code == 200
    assert seen["domain"] == "mydomain"


def test_artifact_import_write_failure_is_400(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    monkeypatch.setattr(dashboard_routes, "commit_to_graph", lambda doc_dir, domain: False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("document.json", "{}")
    buf.seek(0)
    response = _client().post(
        "/api/artifacts/import",
        data={"domain": "general", "doc": "doc1"},
        files={"file": ("bundle.zip", buf, "application/zip")},
    )
    assert response.status_code == 400


def test_artifact_import_bad_zip_is_400(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    response = _client().post(
        "/api/artifacts/import",
        data={"domain": "general", "doc": "doc1"},
        files={"file": ("bundle.zip", io.BytesIO(b"not a zip"), "application/zip")},
    )
    assert response.status_code == 400


def test_artifact_import_rejects_zip_slip(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.txt", "pwned")
    buf.seek(0)
    response = _client().post(
        "/api/artifacts/import",
        data={"domain": "general", "doc": "doc1"},
        files={"file": ("bundle.zip", buf, "application/zip")},
    )
    assert response.status_code == 400
    assert not (tmp_path / "evil.txt").exists()


def test_artifact_import_rejects_traversal_domain(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    called = {}
    monkeypatch.setattr(
        dashboard_routes, "commit_to_graph",
        lambda doc_dir, domain: called.setdefault("called", True) or True,
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("document.json", json.dumps({"name": "doc1.pdf"}))
    buf.seek(0)
    response = _client().post(
        "/api/artifacts/import",
        data={"domain": "..", "doc": "doc1"},
        files={"file": ("bundle.zip", buf, "application/zip")},
    )
    assert response.status_code == 400
    assert "called" not in called
    assert not (tmp_path.parent / "doc1" / "document.json").exists()


def test_commit_artifact_endpoint(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes

    domain_dir = tmp_path / "d" / "mydoc"
    domain_dir.mkdir(parents=True)
    (domain_dir / "document.json").write_text('{"id":"d1","name":"f.md"}', encoding="utf-8")
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)

    called = {}
    monkeypatch.setattr(dashboard_routes, "commit_to_graph",
                        lambda p, dom: called.setdefault("dom", dom) or True, raising=False)

    resp = _client().post("/api/artifacts/d/mydoc/write-to-graph")
    assert resp.status_code == 200
    assert resp.json() == {"domain": "d", "doc": "mydoc", "written": True}
    assert called["dom"] == "d"


def test_commit_artifact_missing_returns_404(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    resp = _client().post("/api/artifacts/d/nope/write-to-graph")
    assert resp.status_code == 404


def test_commit_artifact_rejects_traversal_domain(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes

    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    called = {}
    monkeypatch.setattr(
        dashboard_routes, "commit_to_graph",
        lambda p, dom: called.setdefault("called", True) or True, raising=False,
    )
    resp = _client().post("/api/artifacts/%2e%2e/mydoc/write-to-graph")
    assert resp.status_code == 400
    assert "called" not in called


def test_commit_artifact_rejects_traversal_doc(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes

    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    called = {}
    monkeypatch.setattr(
        dashboard_routes, "commit_to_graph",
        lambda p, dom: called.setdefault("called", True) or True, raising=False,
    )
    resp = _client().post("/api/artifacts/d/%2e%2e/write-to-graph")
    assert resp.status_code == 400
    assert "called" not in called


def test_artifacts_pull_success(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes,
        "pull_kg_fn",
        lambda repo, repo_path, domain: {"pulled_count": 2, "domain": domain, "repo_url": repo, "conflicts": []},
    )
    response = _client().post(
        "/api/artifacts/pull",
        json={"repo": "git@github.com:acme/kg.git", "repoPath": "data/kg/general", "domain": "general"},
    )
    assert response.status_code == 200
    assert response.json()["pulledCount"] == 2


def test_artifacts_pull_failure_is_400(monkeypatch):
    def fake(repo, repo_path, domain):
        raise RuntimeError("conflict detected")

    monkeypatch.setattr(dashboard_routes, "pull_kg_fn", fake)
    response = _client().post(
        "/api/artifacts/pull",
        json={"repo": "git@github.com:acme/kg.git", "repoPath": "data/kg/general", "domain": "general"},
    )
    assert response.status_code == 400


def test_artifacts_pull_rejects_traversal_domain(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(
        dashboard_routes, "pull_kg_fn",
        lambda repo, repo_path, domain: called.setdefault("called", True) or {},
    )
    response = _client().post(
        "/api/artifacts/pull",
        json={"repo": "git@github.com:acme/kg.git", "repoPath": "data/kg/general", "domain": ".."},
    )
    assert response.status_code == 400
    assert "called" not in called


def test_list_snapshots_empty_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path / "nope")
    response = _client().get("/api/snapshots")
    assert response.status_code == 200
    assert response.json() == []


def test_list_snapshots_returns_name_size_date(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snapshot_2026-01-01_000000.tar.gz").write_bytes(b"fake-tarball")
    response = _client().get("/api/snapshots")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "snapshot_2026-01-01_000000.tar.gz"
    assert body[0]["sizeBytes"] == len(b"fake-tarball")
    assert "createdAt" in body[0]


def test_create_snapshot_calls_export_graph(monkeypatch, tmp_path):
    snap = tmp_path / "snapshot_new.tar.gz"
    snap.write_bytes(b"data")
    monkeypatch.setattr(dashboard_routes, "export_graph", lambda: snap)
    response = _client().post("/api/snapshots")
    assert response.status_code == 200
    assert response.json()["name"] == "snapshot_new.tar.gz"


def test_download_snapshot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snap.tar.gz").write_bytes(b"content")
    response = _client().get("/api/snapshots/snap.tar.gz")
    assert response.status_code == 200
    assert response.content == b"content"


def test_download_snapshot_404(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    response = _client().get("/api/snapshots/nope.tar.gz")
    assert response.status_code == 404


def test_download_snapshot_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path / "snapshots")
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "secret.txt").write_text("nope")
    response = _client().get("/api/snapshots/..%2Fsecret.txt")
    assert response.status_code in (400, 404)


def test_restore_snapshot_requires_confirm(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snap.tar.gz").write_bytes(b"data")
    response = _client().post("/api/snapshots/snap.tar.gz/restore", json={"confirm": False})
    assert response.status_code == 400


def test_restore_snapshot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snap.tar.gz").write_bytes(b"data")
    monkeypatch.setattr(
        dashboard_routes, "import_graph",
        lambda path: {"snapshot": path.name, "node_counts": {"Entity": 3}, "relationship_count": 2, "elapsed_seconds": 0.1},
    )
    response = _client().post("/api/snapshots/snap.tar.gz/restore", json={"confirm": True})
    assert response.status_code == 200
    assert response.json()["snapshot"] == "snap.tar.gz"


def test_restore_snapshot_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    response = _client().post("/api/snapshots/nope.tar.gz/restore", json={"confirm": True})
    assert response.status_code == 404


def test_restore_snapshot_import_graph_failure_is_400(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snap.tar.gz").write_bytes(b"data")

    def fake(path):
        raise ValueError("corrupt snapshot")

    monkeypatch.setattr(dashboard_routes, "import_graph", fake)
    response = _client().post("/api/snapshots/snap.tar.gz/restore", json={"confirm": True})
    assert response.status_code == 400


def test_import_snapshot_requires_confirm(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    response = _client().post(
        "/api/snapshots/import",
        data={"confirm": "false"},
        files={"file": ("snap.tar.gz", io.BytesIO(b"data"), "application/gzip")},
    )
    assert response.status_code == 400


def test_import_snapshot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "GRAPH_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(
        dashboard_routes, "import_graph",
        lambda path: {"snapshot": path.name, "node_counts": {}, "relationship_count": 0, "elapsed_seconds": 0.1},
    )
    response = _client().post(
        "/api/snapshots/import",
        data={"confirm": "true"},
        files={"file": ("uploaded.tar.gz", io.BytesIO(b"data"), "application/gzip")},
    )
    assert response.status_code == 200
    assert (tmp_path / "uploaded.tar.gz").exists()


def test_help_concepts_camelizes_and_wraps_generator(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes,
        "get_concepts",
        lambda: [{"key": "artmind-refine", "title": "refine", "description": "d", "source": "skill", "destructive": True}],
    )
    response = _client().get("/api/help/concepts")
    assert response.status_code == 200
    assert response.json() == [
        {"key": "artmind-refine", "title": "refine", "description": "d", "source": "skill", "destructive": True}
    ]


def test_structured_tables_lists_all(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes.structured_registry, "list_tables",
        lambda domains=None: [{"table_name": "products", "domain": "banking", "row_count": 2}],
    )
    response = _client().get("/api/structured/tables")
    assert response.status_code == 200
    assert response.json() == {
        "tables": [{"tableName": "products", "domain": "banking", "rowCount": 2}]
    }


def test_structured_tables_splits_comma_domains(monkeypatch):
    seen = {}

    def fake(domains=None):
        seen["domains"] = domains
        return []

    monkeypatch.setattr(dashboard_routes.structured_registry, "list_tables", fake)
    response = _client().get("/api/structured/tables?domain=general,banking")
    assert response.status_code == 200
    assert seen["domains"] == ["general", "banking"]


def test_structured_table_schema_found(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes.structured_registry, "get_table",
        lambda table, domain=None: {"id": 1, "table_name": "products", "domain": "banking"},
    )
    monkeypatch.setattr(
        dashboard_routes.structured_registry, "get_columns",
        lambda table_id: [{"name": "id", "dtype": "BIGINT"}],
    )
    monkeypatch.setattr(dashboard_routes.structured_registry, "list_mappings", lambda table_id: [])
    response = _client().get("/api/structured/tables/products/schema?domain=banking")
    assert response.status_code == 200
    body = response.json()
    assert body["tableName"] == "products"
    assert body["columns"] == [{"name": "id", "dtype": "BIGINT"}]
    assert body["mappings"] == []


def test_structured_table_schema_404(monkeypatch):
    monkeypatch.setattr(dashboard_routes.structured_registry, "get_table", lambda table, domain=None: None)
    response = _client().get("/api/structured/tables/nope/schema")
    assert response.status_code == 404


def test_structured_ingest_writes_table(monkeypatch, tmp_path):
    seen = {}

    def fake_ingest(source, domain, *, table=None, sheet=None, header_row=0, force=False):
        seen["source_suffix"] = source.suffix
        seen["domain"] = domain
        seen["header_row"] = header_row
        seen["force"] = force
        return {"status": "ok", "tables": [{"table_name": "data", "domain": domain, "row_count": 2}]}

    monkeypatch.setattr(dashboard_routes, "ingest_structured_file", fake_ingest)
    response = _client().post(
        "/api/structured/ingest",
        data={"domain": "banking"},
        files={"file": ("data.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "tables": [{"tableName": "data", "domain": "banking", "rowCount": 2}]
    }
    assert seen["source_suffix"] == ".csv"
    assert seen["domain"] == "banking"
    assert seen["header_row"] == 0
    assert seen["force"] is False


def test_structured_ingest_passes_advanced_fields(monkeypatch, tmp_path):
    seen = {}

    def fake_ingest(source, domain, *, table=None, sheet=None, header_row=0, force=False):
        seen.update(table=table, sheet=sheet, header_row=header_row, force=force)
        return {"status": "ok", "tables": []}

    monkeypatch.setattr(dashboard_routes, "ingest_structured_file", fake_ingest)
    response = _client().post(
        "/api/structured/ingest",
        data={"domain": "banking", "table": "custom", "sheet": "Sheet2", "headerRow": "2", "force": "true"},
        files={"file": ("data.xlsx", io.BytesIO(b"fake"), "application/vnd.openxmlformats")},
    )
    assert response.status_code == 200
    assert seen == {"table": "custom", "sheet": "Sheet2", "header_row": 2, "force": True}


def test_structured_ingest_rejects_unsupported_extension():
    response = _client().post(
        "/api/structured/ingest",
        data={"domain": "banking"},
        files={"file": ("data.txt", io.BytesIO(b"not tabular"), "text/plain")},
    )
    assert response.status_code == 400


def test_structured_ingest_rejects_traversal_domain(monkeypatch):
    called = {}
    monkeypatch.setattr(
        dashboard_routes, "ingest_structured_file",
        lambda *a, **k: called.setdefault("called", True) or {"status": "ok", "tables": []},
    )
    response = _client().post(
        "/api/structured/ingest",
        data={"domain": ".."},
        files={"file": ("data.csv", io.BytesIO(b"a\n1\n"), "text/csv")},
    )
    assert response.status_code == 400
    assert "called" not in called


def test_structured_ingest_pipeline_error_is_400(monkeypatch):
    import click

    def fake_ingest(source, domain, *, table=None, sheet=None, header_row=0, force=False):
        raise click.ClickException("sheet 'Sheet9' not found (or empty/hidden) in 'data.xlsx'")

    monkeypatch.setattr(dashboard_routes, "ingest_structured_file", fake_ingest)
    response = _client().post(
        "/api/structured/ingest",
        data={"domain": "banking", "sheet": "Sheet9"},
        files={"file": ("data.xlsx", io.BytesIO(b"fake"), "application/vnd.openxmlformats")},
    )
    assert response.status_code == 400
    assert "Sheet9" in response.json()["detail"]


def test_structured_sql_returns_rows(monkeypatch):
    monkeypatch.setattr(dashboard_routes.structured_registry, "list_tables", lambda domains=None: [])

    class FakeDs:
        def ensure_views(self, tables):
            pass

        def run_sql(self, sql):
            return [{"n": 2}]

    monkeypatch.setattr(dashboard_routes, "DuckDBDatasource", lambda: FakeDs())
    response = _client().post("/api/structured/sql", json={"sql": "SELECT count(*) AS n FROM products"})
    assert response.status_code == 200
    assert response.json() == {"query_type": "sql", "command": "db sql", "rows": [{"n": 2}]}


def test_structured_sql_rejects_write_statement():
    response = _client().post("/api/structured/sql", json={"sql": "DELETE FROM products"})
    assert response.status_code == 400


def test_structured_sql_error_is_400(monkeypatch):
    monkeypatch.setattr(dashboard_routes.structured_registry, "list_tables", lambda domains=None: [])

    class FakeDs:
        def ensure_views(self, tables):
            pass

        def run_sql(self, sql):
            raise RuntimeError("no such table: nope")

    monkeypatch.setattr(dashboard_routes, "DuckDBDatasource", lambda: FakeDs())
    response = _client().post("/api/structured/sql", json={"sql": "SELECT * FROM nope"})
    assert response.status_code == 400
    assert "no such table: nope" in response.json()["detail"]


def test_list_structured_snapshots_empty_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "nope")
    response = _client().get("/api/structured/snapshots")
    assert response.status_code == 200
    assert response.json() == []


def test_list_structured_snapshots_returns_name_size_date(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "structured_snapshot_2026-01-01_000000.tar.gz").write_bytes(b"fake-tarball")
    response = _client().get("/api/structured/snapshots")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "structured_snapshot_2026-01-01_000000.tar.gz"
    assert body[0]["sizeBytes"] == len(b"fake-tarball")
    assert "createdAt" in body[0]


def test_create_structured_snapshot_calls_export_structured(monkeypatch, tmp_path):
    snap = tmp_path / "structured_snapshot_new.tar.gz"
    snap.write_bytes(b"data")
    monkeypatch.setattr(dashboard_routes, "export_structured", lambda: snap)
    response = _client().post("/api/structured/snapshots")
    assert response.status_code == 200
    assert response.json()["name"] == "structured_snapshot_new.tar.gz"


def test_download_structured_snapshot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snap.tar.gz").write_bytes(b"content")
    response = _client().get("/api/structured/snapshots/snap.tar.gz")
    assert response.status_code == 200
    assert response.content == b"content"


def test_download_structured_snapshot_404(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    response = _client().get("/api/structured/snapshots/nope.tar.gz")
    assert response.status_code == 404


def test_download_structured_snapshot_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "snapshots")
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "secret.txt").write_text("nope")
    response = _client().get("/api/structured/snapshots/..%2Fsecret.txt")
    assert response.status_code in (400, 404)


def test_restore_structured_snapshot_requires_confirm(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snap.tar.gz").write_bytes(b"data")
    response = _client().post("/api/structured/snapshots/snap.tar.gz/restore", json={"confirm": False})
    assert response.status_code == 400


def test_restore_structured_snapshot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snap.tar.gz").write_bytes(b"data")
    monkeypatch.setattr(
        dashboard_routes, "import_structured",
        lambda path: {"snapshot": path.name, "table_count": 1, "elapsed_seconds": 0.1},
    )
    response = _client().post("/api/structured/snapshots/snap.tar.gz/restore", json={"confirm": True})
    assert response.status_code == 200
    assert response.json()["snapshot"] == "snap.tar.gz"


def test_restore_structured_snapshot_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    response = _client().post("/api/structured/snapshots/nope.tar.gz/restore", json={"confirm": True})
    assert response.status_code == 404


def test_restore_structured_snapshot_import_failure_is_400(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snap.tar.gz").write_bytes(b"data")

    def fake(path):
        raise ValueError("corrupt snapshot")

    monkeypatch.setattr(dashboard_routes, "import_structured", fake)
    response = _client().post("/api/structured/snapshots/snap.tar.gz/restore", json={"confirm": True})
    assert response.status_code == 400


def test_import_structured_snapshot_requires_confirm(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    response = _client().post(
        "/api/structured/snapshots/import",
        data={"confirm": "false"},
        files={"file": ("snap.tar.gz", io.BytesIO(b"data"), "application/gzip")},
    )
    assert response.status_code == 400


def test_import_structured_snapshot_success(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_routes, "STRUCTURED_SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(
        dashboard_routes, "import_structured",
        lambda path: {"snapshot": path.name, "table_count": 0, "elapsed_seconds": 0.1},
    )
    response = _client().post(
        "/api/structured/snapshots/import",
        data={"confirm": "true"},
        files={"file": ("uploaded.tar.gz", io.BytesIO(b"data"), "application/gzip")},
    )
    assert response.status_code == 200
    assert (tmp_path / "uploaded.tar.gz").exists()
