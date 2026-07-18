"""ingest_to_kg auto-chains normalize-time but NOT refine-graph/detect-conflicts."""
import inspect
import artmind.ingest as ing


def test_ingest_to_kg_commits_via_commit_to_graph():
    src = inspect.getsource(ing.ingest_to_kg)
    assert "extract_kg" in src
    assert "commit_to_graph" in src
    assert src.index("extract_kg") < src.index("commit_to_graph")


def test_ingest_to_kg_still_does_not_call_refine_or_detect():
    src = inspect.getsource(ing.ingest_to_kg) + inspect.getsource(ing.commit_to_graph)
    assert "refine_graph" not in src
    assert "detect_conflicts" not in src


def test_ingest_sync_and_async_do_not_auto_detect_conflicts():
    import artmind.cli as cli
    # ingest_sync/ingest_async are Click Commands, not plain functions — .callback
    # reaches the wrapped function so inspect.getsource sees the real body.
    src = inspect.getsource(cli.ingest_sync.callback) + inspect.getsource(cli.ingest_async.callback)
    assert "detect_conflicts" not in src
    assert "detect-conflicts" not in src
    assert "refine_graph(" not in src


def test_ingest_to_kg_stage_only_skips_commit(monkeypatch, tmp_path):
    import artmind.ingest as ing

    calls = []
    file_result = {"chunks_dir": str(tmp_path), "chunk_count": 1}
    monkeypatch.setattr(ing, "extract_kg", lambda fr, d, tm, em: tmp_path)
    monkeypatch.setattr(ing, "commit_to_graph", lambda p, d: calls.append((p, d)) or True)

    ok = ing.ingest_to_kg(file_result, "mydomain", stage_only=True)

    assert ok is True
    assert calls == []


def test_ingest_to_kg_commits_when_not_stage_only(monkeypatch, tmp_path):
    import artmind.ingest as ing

    calls = []
    file_result = {"chunks_dir": str(tmp_path), "chunk_count": 1}
    monkeypatch.setattr(ing, "extract_kg", lambda fr, d, tm, em: tmp_path)
    monkeypatch.setattr(ing, "commit_to_graph", lambda p, d: calls.append((p, d)) or "sentinel")

    ok = ing.ingest_to_kg(file_result, "mydomain", stage_only=False)

    assert calls == [(tmp_path, "mydomain")]
    assert ok == "sentinel"


def test_commit_to_graph_runs_write_then_temporal_then_supersession(monkeypatch, tmp_path):
    import json
    import artmind.ingest as ing

    calls = []
    (tmp_path / "document.json").write_text(json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8")

    monkeypatch.setattr(ing, "write_to_graph", lambda p: calls.append("write") or True)

    import artmind.temporal as temporal
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: calls.append("temporal"))
    monkeypatch.setattr(temporal, "detect_supersession",
                        lambda d, only_doc_name=None: calls.append(f"super:{only_doc_name}"))

    ok = ing.commit_to_graph(tmp_path, "mydomain")
    assert ok is True
    assert calls == ["write", "temporal", "super:f.md"]


def test_commit_to_graph_skips_hooks_when_write_fails(monkeypatch, tmp_path):
    import artmind.ingest as ing
    calls = []
    monkeypatch.setattr(ing, "write_to_graph", lambda p: calls.append("write") or False)
    import artmind.temporal as temporal
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: calls.append("temporal"))
    monkeypatch.setattr(temporal, "detect_supersession", lambda d, only_doc_name=None: calls.append("super"))

    ok = ing.commit_to_graph(tmp_path, "mydomain")
    assert ok is False
    assert calls == ["write"]


def test_worker_threads_stage_only_into_ingest_to_kg():
    """Full worker integration needs Neo4j; assert the plumbing structurally instead."""
    import artmind.worker as worker

    assert "stage_only" in inspect.signature(worker._process_job).parameters

    process_src = inspect.getsource(worker._process_job)
    assert "stage_only=stage_only" in process_src

    loop_src = inspect.getsource(worker._worker_loop)
    assert "stage_only=bool(row[3])" in loop_src


def test_commit_to_graph_survives_temporal_hook_exception_and_still_runs_supersession(monkeypatch, tmp_path):
    import json
    import artmind.ingest as ing

    calls = []
    (tmp_path / "document.json").write_text(json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8")

    monkeypatch.setattr(ing, "write_to_graph", lambda p: calls.append("write") or True)

    import artmind.temporal as temporal
    def boom(p, d):
        calls.append("temporal")
        raise RuntimeError("temporal hook down")
    monkeypatch.setattr(temporal, "normalize_ingested_document", boom)
    monkeypatch.setattr(temporal, "detect_supersession",
                        lambda d, only_doc_name=None: calls.append(f"super:{only_doc_name}"))

    ok = ing.commit_to_graph(tmp_path, "mydomain")
    assert ok is True
    assert calls == ["write", "temporal", "super:f.md"]
