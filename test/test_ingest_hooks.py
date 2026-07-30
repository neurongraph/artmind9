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


def test_reassert_superseding_properties_overwrites_by_entity_key(monkeypatch, tmp_path):
    import json
    import artmind.ingest as ing

    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Standard Fee", "entity_class": "FEE", "domain": "banking.reference"},
        {"id": "c1_e2", "name": "No Props", "entity_class": "FEE", "domain": "banking.reference"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text(json.dumps([
        {"id": "c1_e1", "properties": {"monthly_amount": 6.0, "effective_date": "2026-06-01"}},
    ]), encoding="utf-8")

    runs = []

    class FakeResult:
        def single(self):
            return {"matched": 1}

    class FakeSession:
        def run(self, cypher, **kwargs):
            runs.append((cypher, kwargs))
            return FakeResult()

    class FakeCtx:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *exc):
            return False

    import artmind.graph_query as gq
    monkeypatch.setattr(gq, "neo4j_session", lambda: FakeCtx())

    out = ing._reassert_superseding_properties(tmp_path, "banking.reference")

    assert out == {"entities_reasserted": 1}
    assert len(runs) == 1  # the props-less entity is skipped entirely
    cypher, kwargs = runs[0]
    # Must match by the same key _upsert_entity merges on — never by the
    # staged chunk-scoped extraction id (graph nodes carry uuid ids).
    assert "{id:" not in cypher
    assert "name:$name" in cypher and "entity_class:$ec" in cypher and "domain:$domain" in cypher
    assert "SET n += $props" in cypher
    assert kwargs["name"] == "Standard Fee"
    assert kwargs["ec"] == "FEE"
    assert kwargs["domain"] == "banking.reference"
    assert kwargs["props"]["monthly_amount"] == 6.0
    assert kwargs["props"]["effective_date"] == "2026-06-01"


def test_commit_to_graph_reasserts_props_only_when_supersession_applied(monkeypatch, tmp_path):
    import json
    import artmind.ingest as ing
    import artmind.temporal as temporal

    (tmp_path / "document.json").write_text(json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8")
    monkeypatch.setattr(ing, "write_to_graph", lambda p: True)
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: None)

    reasserts = []
    monkeypatch.setattr(
        ing, "_reassert_superseding_properties",
        lambda p, d: reasserts.append((p, d)) or {"entities_reasserted": 1},
    )

    # Supersession applied something → re-assert runs.
    monkeypatch.setattr(
        temporal, "detect_supersession",
        lambda d, only_doc_name=None: {"applied": [{"newer": "d1", "older": "d0"}]},
    )
    assert ing.commit_to_graph(tmp_path, "mydomain") is True
    assert reasserts == [(tmp_path, "mydomain")]

    # Nothing applied → no re-assert.
    reasserts.clear()
    monkeypatch.setattr(
        temporal, "detect_supersession",
        lambda d, only_doc_name=None: {"applied": []},
    )
    assert ing.commit_to_graph(tmp_path, "mydomain") is True
    assert reasserts == []


def test_commit_to_graph_skips_capture_when_supersession_impossible(monkeypatch, tmp_path):
    """The gate saves a Neo4j read on the vast majority of ingests."""
    import json
    import artmind.ingest as ing
    import artmind.entity_history as eh
    import artmind.temporal as temporal

    (tmp_path / "document.json").write_text(
        json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8"
    )
    monkeypatch.setattr(ing, "write_to_graph", lambda p: True)
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: None)
    monkeypatch.setattr(temporal, "detect_supersession", lambda d, only_doc_name=None: {"applied": []})
    monkeypatch.setattr(eh, "supersession_possible", lambda name, domain: False)

    captured = []
    monkeypatch.setattr(eh, "capture_prior_values", lambda p, d: captured.append(1) or {})

    assert ing.commit_to_graph(tmp_path, "banking.policy") is True
    assert captured == [], "capture must not run when the gate is closed"


def test_commit_to_graph_captures_before_write_and_snapshots_after(monkeypatch, tmp_path):
    """Ordering is the whole correctness story: capture, then write, then snapshot.

    Capturing after write_to_graph would record _upsert_entity's accretive
    "old | new" concatenation instead of the clean prior value.
    """
    import json
    import artmind.ingest as ing
    import artmind.entity_history as eh
    import artmind.temporal as temporal

    (tmp_path / "document.json").write_text(
        json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8"
    )
    order = []
    monkeypatch.setattr(eh, "supersession_possible", lambda name, domain: True)
    monkeypatch.setattr(
        eh, "capture_prior_values",
        lambda p, d: order.append("capture") or {"k": {"entity_id": "e1"}},
    )
    monkeypatch.setattr(ing, "write_to_graph", lambda p: order.append("write") or True)
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: None)
    monkeypatch.setattr(
        temporal, "detect_supersession",
        lambda d, only_doc_name=None: {"applied": [{"newer": "d1", "older": "d0", "effective": "2026-06-01"}]},
    )
    monkeypatch.setattr(ing, "_reassert_superseding_properties", lambda *a, **k: {"entities_reasserted": 0})

    snapshots = []
    monkeypatch.setattr(
        eh, "snapshot_changed_values",
        lambda prior, incoming, effective, newer_doc_id: snapshots.append(
            (effective, newer_doc_id)
        ) or len(snapshots),
    )

    assert ing.commit_to_graph(tmp_path, "banking.policy") is True
    assert order == ["capture", "write"], f"wrong ordering: {order}"
    assert snapshots == [("2026-06-01", "d1")]


def test_incoming_property_values_merges_across_chunks_for_the_same_entity(tmp_path):
    """entities.json ids are chunk-scoped: one logical entity mentioned in two

    chunks appears as two rows sharing (name, entity_class, domain) but with
    different chunk-scoped ids and different asserted property subsets. The
    incoming picture must union those keys into a single row, not silently drop
    one chunk's contribution — otherwise snapshot_changed_values compares the
    prior state against an incomplete "incoming" side.
    """
    import json
    import artmind.ingest as ing

    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
        {"id": "c7_e3", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text(json.dumps([
        {"id": "c1_e1", "properties": {"approval_limit": "£2,000"}},
        {"id": "c7_e3", "properties": {"effective_date": "2026-01-01"}},
    ]), encoding="utf-8")

    out = ing._incoming_property_values(tmp_path, "banking.policy")

    key = ("Fee Policy", "POLICY", "banking.policy")
    assert out[key] == {
        "approval_limit": "£2,000",
        "effective_date": "2026-01-01",
    }


def test_incoming_property_values_last_chunk_wins_on_key_collision(tmp_path):
    """Two chunks asserting the SAME key for the same entity: the later
    entities.json entry wins, mirroring _reassert_superseding_properties'
    sequential unconditional `SET n += $props` (last-processed chunk lands on
    the node), not _upsert_entity's merge (which keeps the existing scalar).
    """
    import json
    import artmind.ingest as ing

    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
        {"id": "c7_e3", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text(json.dumps([
        {"id": "c1_e1", "properties": {"approval_limit": "£500"}},
        {"id": "c7_e3", "properties": {"approval_limit": "£2,000"}},
    ]), encoding="utf-8")

    out = ing._incoming_property_values(tmp_path, "banking.policy")

    key = ("Fee Policy", "POLICY", "banking.policy")
    assert out[key] == {"approval_limit": "£2,000"}


def test_commit_to_graph_capture_failure_does_not_block_commit(monkeypatch, tmp_path):
    """Capture is best-effort: if the gate or the capture itself blows up,
    commit_to_graph must still write and return True, with prior defaulting
    to {} rather than propagating the exception.
    """
    import json
    import artmind.ingest as ing
    import artmind.entity_history as eh
    import artmind.temporal as temporal

    (tmp_path / "document.json").write_text(
        json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8"
    )

    def boom(name, domain):
        raise RuntimeError("gate blew up")

    monkeypatch.setattr(eh, "supersession_possible", boom)
    monkeypatch.setattr(ing, "write_to_graph", lambda p: True)
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: None)
    monkeypatch.setattr(temporal, "detect_supersession", lambda d, only_doc_name=None: {"applied": []})

    assert ing.commit_to_graph(tmp_path, "banking.policy") is True
