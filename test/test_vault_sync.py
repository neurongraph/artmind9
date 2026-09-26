"""`vault sync`: git-diff-driven, CDC-style replay (spec
docs/superpowers/specs/2026-09-25-vault-sync-design.md)."""
import subprocess

import pytest

import artmind.vault_sync as vs


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit_all(path, message):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=path, check=True)


@pytest.fixture()
def repo(tmp_path):
    _init_git_repo(tmp_path)
    return tmp_path


def test_head_sha_returns_the_current_commit(repo):
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    sha = vs.head_sha(repo)

    log = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert sha == log


def test_head_sha_raises_a_clear_error_when_the_repo_has_no_commits_yet(repo):
    """Exactly the state `artmind init` leaves a fresh vault in (`git init`,
    never a commit) -- must raise a specific, actionable VaultSyncError, not
    a generic git-command-failed message."""
    with pytest.raises(vs.VaultSyncError, match="no commits yet"):
        vs.head_sha(repo)


def test_diff_name_status_reports_added_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")

    rows = vs._diff_name_status(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo), scope)

    assert rows == [("A", "sub/f.txt")]


def test_diff_name_status_reports_removed_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (scope / "f.txt").unlink()
    _commit_all(repo, "second")

    rows = vs._diff_name_status(repo, base, vs.head_sha(repo), scope)

    assert rows == [("D", "sub/f.txt")]


def test_diff_name_status_reports_modified_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (scope / "f.txt").write_text("y")
    _commit_all(repo, "second")

    rows = vs._diff_name_status(repo, base, vs.head_sha(repo), scope)

    assert rows == [("M", "sub/f.txt")]


def test_show_returns_none_for_a_path_absent_at_that_revision(repo):
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)

    assert vs._show(repo, base, "never_existed.txt") is None


def test_show_returns_the_content_at_that_revision(repo):
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (repo / "a.txt").write_text("changed\n")
    _commit_all(repo, "second")

    assert vs._show(repo, base, "a.txt") == "hello\n"


def test_show_raises_when_the_revision_itself_does_not_resolve(repo):
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    with pytest.raises(vs.VaultSyncError, match="does not resolve"):
        vs._show(repo, "0000000000000000000000000000000000000000", "a.txt")


# ── classify_diff: track A (.artmind/data/kg/**) ────────────────────────────


def _write_doc_folder(kg_dir, domain, docdir, doc_id, extra_files=("chunks.json", "relationships.json")):
    folder = kg_dir / domain / docdir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "document.json").write_text(f'{{"id": "{doc_id}"}}')
    (folder / "observations.json").write_text("[]")
    for name in extra_files:
        (folder / name).write_text("[]")


def _patch_kg_dir(monkeypatch, vault_dir):
    import paths
    kg_dir = vault_dir / ".artmind" / "data" / "kg"
    monkeypatch.setattr(paths, "KG_DIR", kg_dir)
    return kg_dir


def _patch_structured_text_dir(monkeypatch, vault_dir):
    import paths
    st_dir = vault_dir / ".artmind" / "data" / "structured_text"
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", st_dir)
    return st_dir


def test_classify_diff_replays_an_added_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    from artmind.vault import VaultLayout

    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]
    assert plan.retract == []


def test_classify_diff_replays_a_modified_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "observations.json").write_text('[{"key": "x"}]')
    _commit_all(repo, "edit doc1")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]


def test_classify_diff_retracts_a_removed_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "doc1")
    _commit_all(repo, "remove doc1")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == [("banking", "docid-1")]


def test_classify_diff_retracts_a_removed_table_folder_directly(repo, monkeypatch):
    """A table__<name> folder removed directly (out-of-band, not via a
    structured-text change) uses the identical retraction path (spec §4)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "table__accounts", "table:banking:accounts")
    _commit_all(repo, "add table")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "table__accounts")
    _commit_all(repo, "remove table folder")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.retract == [("banking", "table:banking:accounts")]


def test_classify_diff_ignores_a_folder_with_no_observations_json_change(repo, monkeypatch):
    """Only some OTHER file (e.g. table2graph_report.json) changed --
    neither replay nor retract triggers (spec §4's own conditioning on
    observations.json specifically)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1", extra_files=("chunks.json", "table2graph_report.json"))
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "table2graph_report.json").write_text('{"n": 1}')
    _commit_all(repo, "edit report only")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == []


def test_classify_diff_scopes_to_requested_domains(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _write_doc_folder(kg_dir, "legal", "doc2", "docid-2")
    _commit_all(repo, "add both")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo), domains=["banking"])

    assert plan.replay_docs == [("banking", "doc1")]


# ── classify_diff: track B (.artmind/data/structured_text/**) ───────────────


def test_classify_diff_regenerates_an_added_table_csv(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.regenerate_tables == [("banking", "accounts")]


def test_classify_diff_retracts_a_removed_table_csv(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")
    base = vs.head_sha(repo)
    (st_dir / "banking" / "accounts.csv").unlink()
    _commit_all(repo, "remove table text")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.regenerate_tables == []
    assert plan.retract == [("banking", "table:banking:accounts")]


def test_classify_diff_manifest_only_change_triggers_nothing(repo, monkeypatch):
    """Documented scoping decision (not an oversight): manifest.json is a
    single shared file, not "under a given table's export" -- a
    manifest-only change with no accompanying CSV diff regenerates nothing
    in this version."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")
    base = vs.head_sha(repo)
    (st_dir / "manifest.json").write_text('{"changed": true}')
    _commit_all(repo, "edit manifest only")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.regenerate_tables == []


def test_classify_diff_dedupes_a_table_retracted_from_both_tracks(repo, monkeypatch):
    """If a table__* folder AND its structured-text CSV are both removed in
    the same diff, the table is retracted once, not twice."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "table__accounts", "table:banking:accounts")
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add both")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "table__accounts")
    (st_dir / "banking" / "accounts.csv").unlink()
    _commit_all(repo, "remove both")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.retract == [("banking", "table:banking:accounts")]


def test_classify_diff_never_sees_track_bs_uncommitted_output_in_the_same_run(repo, monkeypatch):
    """§5's core sequencing correctness property (spec §12's second test
    case): `classify_diff` reads committed git history only (`git diff`
    between two fixed revisions) -- it is structurally incapable of seeing
    files track B has written to the working tree but not yet committed,
    regardless of when in a `sync()` run it's called. This test proves the
    property directly: write a FRESH, uncommitted table__* folder (exactly
    what track B's regenerate step produces mid-run -- see `sync()`), and
    confirm classify_diff's track-A pass does not report it as an added
    document folder -- an uncommitted file simply cannot appear in any
    `base..head` diff."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    head = vs.head_sha(repo)  # no new commit -- this run's diff_range is fixed and empty

    # Simulate track B having just written fresh output mid-run, uncommitted.
    _write_doc_folder(kg_dir, "banking", "table__accounts", "table:banking:accounts")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, head)

    assert plan.replay_docs == [], (
        "an uncommitted table__* folder must never be picked up as a track-A "
        "replay within the same run that just wrote it"
    )


def test_classify_diff_raises_if_removed_folder_never_existed_at_base(monkeypatch, tmp_path):
    """Defensive: should be unreachable in practice -- git cannot report a
    `D` status for a path that was absent at BOTH diff endpoints, so this
    monkeypatches `_diff_name_status`/`_show` directly to simulate the
    impossible case, rather than trying (and failing) to construct it via
    real git commands. Fail loud rather than silently guess if it ever
    somehow occurs.

    `paths.KG_DIR` is patched like every other test that touches it, so the
    mocked `_diff_name_status` row below is a realistic vault_dir-relative
    path (matching what git itself would return -- see its docstring) rather
    than leaning on a since-removed "unscoped root" fallback."""
    kg_dir = _patch_kg_dir(monkeypatch, tmp_path)
    from artmind.vault import VaultLayout

    removed_path = str((kg_dir / "banking" / "doc1" / "observations.json").relative_to(tmp_path))
    monkeypatch.setattr(vs, "_diff_name_status", lambda *a, **k: [("D", removed_path)])
    monkeypatch.setattr(vs, "_show", lambda *a, **k: None)

    with pytest.raises(vs.VaultSyncError, match="wasn't present"):
        vs.classify_diff(tmp_path, VaultLayout(tmp_path), "base-sha", "head-sha")


def test_classify_diff_scopes_to_a_domain_ancestor(repo, monkeypatch):
    """Requesting domain "banking" must also match a folder at the
    hierarchical child domain "banking.retail" (descendant matching,
    consistent with domain scoping elsewhere in this codebase)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking.retail", "doc1", "docid-1")
    _commit_all(repo, "add doc1")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo), domains=["banking"])

    assert plan.replay_docs == [("banking.retail", "doc1")]


def test_classify_diff_ignores_document_json_changing_alongside_observations(repo, monkeypatch):
    """document.json's own diff row must never be double-processed as a
    second replay entry -- only observations.json's status decides."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "document.json").write_text('{"id": "docid-1", "title": "renamed"}')
    (kg_dir / "banking" / "doc1" / "observations.json").write_text('[{"key": "y"}]')
    _commit_all(repo, "edit both document.json and observations.json")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]


# ── sync(): bootstrap ────────────────────────────────────────────────────────


def test_sync_refuses_without_marker_or_bootstrap_flag(repo, monkeypatch):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    with pytest.raises(vs.VaultSyncError, match="bootstrapEmpty"):
        vs.sync(repo)


def test_sync_bootstrap_synced_stamps_the_cursor_without_replaying(repo, monkeypatch):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    from artmind.vault import VaultLayout, read_state

    result = vs.sync(repo, bootstrap_synced=True)

    assert result["last_synced_commit"] == vs.head_sha(repo)
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)


def test_sync_bootstrap_synced_dry_run_writes_nothing(repo, monkeypatch):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    from artmind.vault import VaultLayout, read_state

    vs.sync(repo, bootstrap_synced=True, dry_run=True)

    assert read_state(VaultLayout(repo)) == {}


def test_sync_dry_run_reports_counts_and_writes_nothing(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    from artmind.vault import VaultLayout, read_state

    result = vs.sync(repo, bootstrap_empty=True, dry_run=True)

    assert result["replay"] == 1
    assert read_state(VaultLayout(repo)) == {}


# ── sync(): the full sequencing, with Neo4j/table2graph mocked ──────────────


def _patch_ingest_and_projection(monkeypatch, deferred_keys_by_doc=None, retract_results=None):
    """Mocks the graph-touching seams `sync()` calls, so these tests stay
    hermetic (no real Neo4j) while still asserting on what was actually
    called and with what -- never on a summary count alone (CLAUDE.md)."""
    import artmind.ingest as ing
    import artmind.table2graph as t2g

    calls = {"write_to_neo4j": [], "retract_document": [], "rebuild_in_batches": [], "sweeps": []}

    def _fake_write(doc_kg_dir, domain, defer_rebuild=False):
        calls["write_to_neo4j"].append((str(doc_kg_dir), domain))
        keys = (deferred_keys_by_doc or {}).get(doc_kg_dir.name, [])
        return {"deferred_keys": keys, "unembedded_chunk_ids": []}

    def _fake_retract(doc_id, domain):
        calls["retract_document"].append((doc_id, domain))
        result = (retract_results or {}).get(doc_id, {"affected_keys": []})
        return {"doc_id": doc_id, "domain": domain, **result}

    def _fake_rebuild_in_batches(keys):
        calls["rebuild_in_batches"].append(sorted(keys))
        return {"rebuilt": len(keys), "deleted": 0, "absent": 0, "keys": len(keys), "batches": 1}

    monkeypatch.setattr(ing, "_write_to_neo4j", _fake_write)
    monkeypatch.setattr(ing, "retract_document", _fake_retract)
    monkeypatch.setattr(t2g, "_rebuild_in_batches", _fake_rebuild_in_batches)
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: calls["sweeps"].append(("entities", domain)) or 0)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda **kw: calls["sweeps"].append(("chunks", kw.get("domain"))) or 0)
    return calls


def test_sync_replays_an_added_document_folder_and_advances_the_cursor(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    calls = _patch_ingest_and_projection(
        monkeypatch, deferred_keys_by_doc={"doc1": [["Acme", "ORG", "banking"]]}
    )

    result = vs.sync(repo, bootstrap_empty=True)

    assert calls["write_to_neo4j"] == [(str(kg_dir / "banking" / "doc1"), "banking")]
    assert calls["rebuild_in_batches"] == [[("Acme", "ORG", "banking")]]
    assert ("entities", "banking") in calls["sweeps"]
    assert ("chunks", "banking") in calls["sweeps"]
    assert result["last_synced_commit"] == vs.head_sha(repo)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)


def test_sync_retracts_a_removed_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "doc1")
    _commit_all(repo, "remove doc1")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})

    calls = _patch_ingest_and_projection(
        monkeypatch, retract_results={"docid-1": {"affected_keys": [["Acme", "ORG", "banking"]]}}
    )

    result = vs.sync(repo)

    assert calls["retract_document"] == [("docid-1", "banking")]
    assert calls["rebuild_in_batches"] == [[("Acme", "ORG", "banking")]]
    assert result["retracted"] == 1


def test_sync_leaves_the_cursor_untouched_on_failure(repo, monkeypatch):
    """§9's all-or-nothing: a transient failure mid-run must not advance
    last_synced_commit -- the next invocation recomputes the identical
    diff_range from scratch."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")

    import artmind.ingest as ing

    def _boom(*a, **k):
        raise RuntimeError("neo4j connection dropped")

    monkeypatch.setattr(ing, "_write_to_neo4j", _boom)

    with pytest.raises(RuntimeError, match="neo4j connection dropped"):
        vs.sync(repo, bootstrap_empty=True)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {}


def test_sync_retry_after_a_failure_converges_to_the_same_end_state(repo, monkeypatch):
    """§9/§12's idempotent-retry property: a simulated mid-run failure,
    followed by a second run, converges to the same end state as an
    uninterrupted run would have -- the identical diff_range is recomputed
    from scratch and this time succeeds, advancing the cursor to the same
    HEAD an uninterrupted first attempt would have reached. (The
    redundant-History-version cost §9 names -- a document that had already
    committed once before the failure gets re-demoted-and-rewritten on
    retry -- is a real Neo4j-write-level effect this mocked test cannot
    observe; it is accepted per §9, not asserted away, and not re-proven
    here beyond this convergence property.)"""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    expected_head = vs.head_sha(repo)

    import artmind.ingest as ing
    monkeypatch.setattr(ing, "_write_to_neo4j", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        vs.sync(repo, bootstrap_empty=True)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {}

    _patch_ingest_and_projection(monkeypatch, deferred_keys_by_doc={"doc1": [["Acme", "ORG", "banking"]]})
    result = vs.sync(repo, bootstrap_empty=True)

    assert result["last_synced_commit"] == expected_head
    assert read_state(VaultLayout(repo))["last_synced_commit"] == expected_head


def test_sync_regenerates_a_table_from_structured_text_diff(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    calls = {"import_structured_text": [], "table_to_graph": []}
    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: calls["import_structured_text"].append(k.get("tables")) or {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain: {"name": domain})
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: calls["table_to_graph"].append(k) or {
            "commit": {"deferred_keys": [("Checking", "ACCOUNT", "banking")]}
        },
    )
    monkeypatch.setattr(t2g, "stage_dir", lambda domain, table_name: repo / "staged" / domain / table_name)
    calls2 = _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo, bootstrap_empty=True)

    assert calls["import_structured_text"] == [[("banking", "accounts")]]
    assert calls["table_to_graph"][0]["defer_rebuild"] is True
    assert calls2["rebuild_in_batches"] == [[("Checking", "ACCOUNT", "banking")]]
    assert result["regenerated_tables"] == 1
