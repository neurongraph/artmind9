"""What commit_to_graph does, and — just as importantly — what it no longer does.

Phase 3 removed the best-effort post-write hooks (temporal normalization,
supersession detection, property re-assertion, prior-value capture). Each of
them caught its own exceptions and logged a warning, which is precisely why a
broken projection used to look exactly like a healthy one. The projection
rebuild replaces them and runs INSIDE the write transaction.
"""
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
    monkeypatch.setattr(ing, "commit_to_graph", lambda p, d, replace=False: calls.append((p, d)) or "sentinel")

    ok = ing.ingest_to_kg(file_result, "mydomain", stage_only=False)

    assert calls == [(tmp_path, "mydomain")]
    assert ok == "sentinel"






def test_worker_threads_stage_only_into_ingest_to_kg():
    """Full worker integration needs Neo4j; assert the plumbing structurally instead."""
    import artmind.worker as worker

    assert "stage_only" in inspect.signature(worker._process_job).parameters

    process_src = inspect.getsource(worker._process_job)
    assert "stage_only=stage_only" in process_src

    loop_src = inspect.getsource(worker._worker_loop)
    assert "stage_only=bool(row[3])" in loop_src


# ── the hooks are gone, and the rebuild took their place ────────────────────


def test_commit_to_graph_no_longer_runs_the_best_effort_post_write_hooks():
    """Each of these swallowed its own exceptions. A silently-skipped step in
    the central pipeline is the failure class this redesign exists to remove."""
    src = inspect.getsource(ing.commit_to_graph) + inspect.getsource(ing._write_to_neo4j)
    for gone in (
        "normalize_ingested_document",
        "detect_supersession",
        "_reassert_superseding_properties",
        "capture_prior_values",
        "entity_history",
    ):
        assert gone not in src, f"{gone} should have been removed in Phase 3"


def test_the_projection_rebuild_runs_inside_the_write_transaction():
    """Not after it, and not in a `try` that logs a warning."""
    tx_src = inspect.getsource(ing._commit_document_tx)
    assert "projection.rebuild" in tx_src
    assert "projection.affected_keys" in tx_src
    # the unit of work is handed to execute_write as one transaction
    assert "execute_write(_commit_document_tx" in inspect.getsource(ing._write_to_neo4j)


def test_the_rebuild_is_not_wrapped_in_a_swallow():
    """A bare `except: logger.warning(...)` around the rebuild would restore
    exactly the behaviour Phase 3 removed."""
    import ast

    tree = ast.parse(inspect.getsource(ing._commit_document_tx).lstrip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            body = ast.dump(node)
            assert "projection" not in body, "the projection rebuild must not sit inside a try/except"


def test_the_accretive_merge_and_its_ledger_are_gone():
    """`"A | B"` string accretion produced 512 self-repeating descriptions;
    the ledger existed only to un-merge it."""
    for gone in (
        "_upsert_entity",
        "_merge_prop_value",
        "_merge_props_dicts",
        "_parse_prop_sources",
        "_ledger_upsert",
        "_fold_ledger",
        "_rollback_property_ledger",
    ):
        assert not hasattr(ing, gone), f"ingest.{gone} should have been deleted in Phase 3"
