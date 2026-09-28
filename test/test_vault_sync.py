"""`vault sync`: git-diff-driven, CDC-style replay (spec
docs/superpowers/specs/2026-09-25-vault-sync-design.md)."""
import re
import subprocess
from pathlib import Path

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


@pytest.fixture(autouse=True)
def _no_worker(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")


VAULT_ID = "vault-under-test"


class _FakeGraphState:
    """The graph side of `artmind.sync_state`, in memory: what one Neo4j
    holds between runs. Two `sync()` calls sharing one instance model two
    runs against the same graph -- or two machines sharing one AuraDB."""

    def __init__(self):
        self.bookmarks: dict[str, str] = {}
        self.fingerprints: dict[str, str | None] = {}
        self.fingerprint_reads: list[list[str]] = []

    def read_graph_bookmark(self, vault_id, *, timeout=None):
        return self.bookmarks.get(vault_id)

    def write_graph_bookmark(self, vault_id, commit):
        self.bookmarks[vault_id] = commit

    def read_document_fingerprints(self, doc_ids, *, timeout=None):
        self.fingerprint_reads.append(sorted(set(doc_ids)))
        return {d: self.fingerprints[d] for d in doc_ids if d in self.fingerprints}


@pytest.fixture(autouse=True)
def graph(tmp_path, monkeypatch):
    """Every test's vault has a `vault_id`, and its graph bookmark and
    fingerprints live in a `_FakeGraphState` rather than Neo4j."""
    from artmind import sync_state

    (tmp_path / ".artmind").mkdir(exist_ok=True)
    (tmp_path / ".artmind" / "vault.yaml").write_text(f'vault_id: "{VAULT_ID}"\n')
    fake = _FakeGraphState()
    monkeypatch.setattr(sync_state, "read_graph_bookmark", fake.read_graph_bookmark)
    monkeypatch.setattr(sync_state, "write_graph_bookmark", fake.write_graph_bookmark)
    monkeypatch.setattr(sync_state, "read_document_fingerprints", fake.read_document_fingerprints)
    return fake


def _bookmarks(repo):
    """`(graph, structured)` -- each store's OWN bookmark, never the legacy
    cursor, so an assertion on it proves the run wrote it."""
    marks = vs.read_bookmarks(repo, vault_id=VAULT_ID)
    return marks.graph, marks.structured


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

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]
    assert plan.retract == []


def test_classify_diff_replays_a_modified_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "observations.json").write_text('[{"key": "x"}]')
    _commit_all(repo, "edit doc1")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]


def test_classify_diff_retracts_a_removed_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "doc1")
    _commit_all(repo, "remove doc1")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == [("banking", "docid-1")]


def test_classify_diff_does_not_retract_a_moved_documents_id(repo, monkeypatch):
    """A moved/renamed note keeps its doc id; its staging folder's name
    changes, so git sees the old folder deleted and a new one added in the
    same commit. Retracting the id (read from the old folder's document.json
    at base) would demote the document `sync()` just replayed under its new
    folder name (item 2, spec 2026-09-27)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "old-name", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "old-name")
    _write_doc_folder(kg_dir, "banking", "new-name", "docid-1")
    _commit_all(repo, "rename doc1's folder")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "new-name")]
    assert plan.retract == []


def test_classify_diff_does_not_retract_when_the_new_folder_already_exists_at_head(repo, monkeypatch):
    """The new folder was added (and already replayed) in an earlier sync;
    this run's diff range only contains the old folder's removal. The id is
    still live at `head` under the new folder, so it must not be retracted
    even though this diff range has no add for it (item 2's two-run case)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "old-name", "docid-1")
    _commit_all(repo, "add under old name")
    _write_doc_folder(kg_dir, "banking", "new-name", "docid-1")
    _commit_all(repo, "add under new name (an earlier sync already replayed this)")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "old-name")
    _commit_all(repo, "remove the old folder")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == []


# ── _document_ids_at_head: never sends a path through cat-file (re-review item 1) ─


def test_document_ids_at_head_handles_a_missing_document_json_in_a_spaced_folder_name(repo, monkeypatch):
    """A folder name with a space, whose observations.json is present but
    document.json is not: `git show head:.../x y/document.json` (or any
    path-based cat-file spec) replies '... missing', and the old parser's
    `int(b"missing")` blew up with ValueError. The fixed parser never sends
    a path to cat-file at all -- only oids ls-tree already proved exist --
    so this folder simply contributes no id, and does not crash."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    folder = kg_dir / "banking" / "x y"
    folder.mkdir(parents=True)
    (folder / "observations.json").write_text("[]")
    _commit_all(repo, "folder with a space, no document.json")

    kg_rel = kg_dir.relative_to(repo)
    result = vs._document_ids_at_head(repo, vs.head_sha(repo), kg_rel, ["banking"])

    assert result.get("banking", set()) == set()


def test_document_ids_at_head_skips_a_non_object_document_json(repo, monkeypatch):
    """document.json that parses as valid JSON but isn't an object (e.g. a
    bare list) must be skipped, not crash on `.get`/`[...]`."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    folder = kg_dir / "banking" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text("[]")
    (folder / "observations.json").write_text("[]")
    _commit_all(repo, "document.json is a list, not an object")

    kg_rel = kg_dir.relative_to(repo)
    result = vs._document_ids_at_head(repo, vs.head_sha(repo), kg_rel, ["banking"])

    assert result.get("banking", set()) == set()


def test_document_ids_at_head_reads_non_ascii_folders_and_multiline_content(repo, monkeypatch):
    """Regression pin: a non-ASCII folder name and a document.json blob
    containing embedded newlines (pretty-printed JSON) must both be read
    correctly -- the parser reads exactly `size` bytes by byte length, never
    by line, and oids (not paths) survive non-ASCII names untouched."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    folder = kg_dir / "banking" / "café"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text('{\n  "id": "docid-café"\n}\n')
    (folder / "observations.json").write_text("[]")
    _commit_all(repo, "non-ascii folder, multiline document.json")

    kg_rel = kg_dir.relative_to(repo)
    result = vs._document_ids_at_head(repo, vs.head_sha(repo), kg_rel, ["banking"])

    assert result == {"banking": {"docid-café"}}


def test_classify_diff_ignores_a_removed_table_folder(repo, monkeypatch):
    """Spec 2026-09-26 §5 R4: `.artmind/data/kg/*/table__*/` is gitignored,
    and "Track A of the sync spec no longer sees them; track B regenerates
    them locally." A table__<name> folder removed directly -- e.g. by the
    `git rm -r --cached` that `vault doctor` prints as the fix for a
    newly-ignored path that's still tracked -- must not be replayed or
    retracted by track A; only track B's structured-text diff may retract a
    table now."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "table__accounts", "table:banking:accounts")
    _commit_all(repo, "add table")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "table__accounts")
    _commit_all(repo, "remove table folder")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.retract == []
    assert plan.replay_docs == []


def test_classify_diff_ignores_an_added_table_folder(repo, monkeypatch):
    """Symmetric with the removal case above (spec §5 R4): a newly-committed
    table__<name> folder must not be replayed by track A either -- it's a
    pure function of the structured-text CSV/mapping, regenerated locally by
    track B."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "table__accounts", "table:banking:accounts")
    _commit_all(repo, "add table")

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == []


def test_classify_diff_replays_a_folder_when_only_another_file_changed(repo, monkeypatch):
    """Spec 2026-09-26 §14 A4: any file changing inside a document folder
    replays it, not only observations.json."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "chunks.json").write_text('[{"id": "c1"}]')
    _commit_all(repo, "edit chunks only")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]
    assert plan.retract == []


def test_classify_diff_replays_a_folder_committed_in_two_halves(repo, monkeypatch):
    """The timer commit caught document.json + observations.json; the next
    commit brings chunks.json and relationships.json. The second commit must
    replay the folder again, or a local-Neo4j receiver never sees them."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1", extra_files=())
    _commit_all(repo, "first half")
    base = vs.head_sha(repo)
    for name in ("chunks.json", "relationships.json"):
        (kg_dir / "banking" / "doc1" / name).write_text("[]")
    _commit_all(repo, "second half")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]


def test_classify_diff_replays_on_a_nested_chunk_cache_change(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    cache = kg_dir / "banking" / "doc1" / "chunks" / "sha1"
    cache.mkdir(parents=True)
    (cache / "0.json").write_text("{}")
    _commit_all(repo, "chunk cache")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]


def test_classify_diff_waits_for_observations_before_replaying(repo, monkeypatch):
    """A folder committed with document.json but no observations.json yet
    has nothing to replay; the commit that adds observations.json will."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    folder = kg_dir / "banking" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text('{"id": "docid-1"}')
    _commit_all(repo, "half a folder")

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == []


def test_classify_diff_ignores_tracked_atomic_write_scratch(repo, monkeypatch):
    """`.artmind-tmp`/`.artmind-old` are gitignored (R4); one that got tracked
    anyway is never a document of its own."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1.artmind-tmp", "docid-1")
    _commit_all(repo, "scratch got committed")

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == []


def test_classify_diff_retracts_when_observations_deleted_but_other_files_remain(repo, monkeypatch):
    """observations.json gone since `base` means retract, even when a
    sibling file in the same folder changed in the same range (grouping by
    folder must not turn a deletion into a replay)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "observations.json").unlink()
    (kg_dir / "banking" / "doc1" / "chunks.json").write_text('[{"id": "x"}]')
    _commit_all(repo, "remove observations, edit chunks")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == [("banking", "docid-1")]


def test_classify_diff_scopes_to_requested_domains(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _write_doc_folder(kg_dir, "legal", "doc2", "docid-2")
    _commit_all(repo, "add both")

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo), domains=["banking"])

    assert plan.replay_docs == [("banking", "doc1")]


# ── classify_diff: track B (.artmind/data/structured_text/**) ───────────────


def test_classify_diff_regenerates_an_added_table_csv(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo))

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

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

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

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.regenerate_tables == []


def test_classify_diff_dedupes_a_table_retracted_from_both_tracks(repo, monkeypatch):
    """If a table__* folder AND its structured-text CSV are both removed in
    the same diff, the table is retracted once, not twice -- track A now
    ignores the table__* folder's own removal entirely (spec §5 R4), so
    track B's CSV deletion is the sole source of the retraction; this test
    guards against it somehow being counted twice via that one path."""
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

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

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

    plan = vs.classify_diff(repo, base, head)

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

    removed_path = str((kg_dir / "banking" / "doc1" / "observations.json").relative_to(tmp_path))
    monkeypatch.setattr(vs, "_diff_name_status", lambda *a, **k: [("D", removed_path)])
    monkeypatch.setattr(vs, "_present_at", lambda *a, **k: [])
    monkeypatch.setattr(vs, "_show", lambda *a, **k: None)

    with pytest.raises(vs.VaultSyncError, match="wasn't present"):
        vs.classify_diff(tmp_path, "base-sha", "head-sha")


def test_classify_diff_scopes_to_a_domain_ancestor(repo, monkeypatch):
    """Requesting domain "banking" must also match a folder at the
    hierarchical child domain "banking.retail" (descendant matching,
    consistent with domain scoping elsewhere in this codebase)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking.retail", "doc1", "docid-1")
    _commit_all(repo, "add doc1")

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo), domains=["banking"])

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

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

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
    result = vs.sync(repo, bootstrap_synced=True)

    head = vs.head_sha(repo)
    assert (result["graph_bookmark"], result["structured_bookmark"]) == (head, head)
    assert _bookmarks(repo) == (head, head)


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

    assert [(Path(p).name, d) for p, d in calls["write_to_neo4j"]] == [("doc1", "banking")]
    assert not calls["write_to_neo4j"][0][0].startswith(str(kg_dir)), "replays from a scratch copy of HEAD"
    assert calls["rebuild_in_batches"] == [[("Acme", "ORG", "banking")]]
    assert ("entities", "banking") in calls["sweeps"]
    assert ("chunks", "banking") in calls["sweeps"]
    head = vs.head_sha(repo)
    assert (result["graph_bookmark"], result["structured_bookmark"]) == (head, head)
    assert _bookmarks(repo) == (head, head)


def test_domain_scoped_sync_does_not_advance_the_cursor(repo, monkeypatch):
    """A `--domain`-scoped sync must not write `last_synced_commit`: doing so
    would leave changes to OTHER domains in base..head permanently
    unapplied on this machine -- silent data loss (item 3). A later
    unscoped sync must still see, and apply, the other domain's change."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add banking doc")
    calls = _patch_ingest_and_projection(monkeypatch)

    first = vs.sync(repo, bootstrap_empty=True)
    assert first["cursor_advanced"] is True
    checkpoint = vs.head_sha(repo)
    assert _bookmarks(repo) == (checkpoint, checkpoint)

    _write_doc_folder(kg_dir, "banking", "doc2", "docid-2")
    _write_doc_folder(kg_dir, "legal", "doc3", "docid-3")
    _commit_all(repo, "add banking doc2 and legal doc3")
    calls["write_to_neo4j"].clear()

    scoped = vs.sync(repo, domains=["banking"])

    assert [Path(p).name for p, _ in calls["write_to_neo4j"]] == ["doc2"]
    assert scoped["cursor_advanced"] is False
    assert (scoped["graph_bookmark"], scoped["structured_bookmark"]) == (checkpoint, checkpoint)
    assert "note" in scoped and scoped["note"]
    assert _bookmarks(repo) == (checkpoint, checkpoint)

    calls["write_to_neo4j"].clear()
    full = vs.sync(repo)

    # Re-applies doc2 too -- idempotent -- since the cursor never moved past
    # `checkpoint`; doc3 is the change that would otherwise have been lost.
    assert {Path(p).name for p, _ in calls["write_to_neo4j"]} == {"doc2", "doc3"}
    assert full["cursor_advanced"] is True
    assert _bookmarks(repo) == (vs.head_sha(repo), vs.head_sha(repo))


def test_sync_rejects_bootstrap_synced_combined_with_domain(repo, monkeypatch):
    """--bootstrapSynced stamps the WHOLE vault's cursor at head with no
    replay at all -- there is no such thing as a domain-scoped stamp, so the
    combination must be refused up front rather than silently stamping the
    cursor as if every domain were current (re-review item 3)."""
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    with pytest.raises(vs.VaultSyncError, match="bootstrapSynced"):
        vs.sync(repo, bootstrap_synced=True, domains=["banking"])

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {}


def test_domain_scoped_bootstrap_empty_note_says_to_rerun_bootstrap_empty(repo, monkeypatch):
    """When there is no prior cursor at all (`last_synced` is None), the
    follow-up sync that will pick up the other domains isn't just "a later
    unscoped `vault sync`" -- an unscoped sync with no --bootstrapEmpty would
    itself raise "no last_synced_commit recorded yet". The note must say to
    re-run with --bootstrapEmpty (re-review item 3)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _write_doc_folder(kg_dir, "legal", "doc2", "docid-2")
    _commit_all(repo, "add both domains, never synced before")
    _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo, bootstrap_empty=True, domains=["banking"])

    assert result["cursor_advanced"] is False
    assert "--bootstrapEmpty" in result["note"]


def test_sync_dry_run_reports_cursor_would_advance(repo, monkeypatch):
    """A scoped dry run must say the cursor would NOT advance if run for
    real; an unscoped one must say it would (re-review item 3)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")

    scoped = vs.sync(repo, bootstrap_empty=True, domains=["banking"], dry_run=True)
    assert scoped["cursor_would_advance"] is False

    unscoped = vs.sync(repo, bootstrap_empty=True, dry_run=True)
    assert unscoped["cursor_would_advance"] is True


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

    assert result["graph_bookmark"] == expected_head
    assert _bookmarks(repo) == (expected_head, expected_head)


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
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {"name": domain})
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: calls["table_to_graph"].append(k) or {
            "commit": {"deferred_keys": [("Checking", "ACCOUNT", "banking")]}
        },
    )
    calls2 = _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo, bootstrap_empty=True)

    assert calls["import_structured_text"] == [[("banking", "accounts")]]
    assert calls["table_to_graph"][0]["defer_rebuild"] is True
    assert calls2["rebuild_in_batches"] == [[("Checking", "ACCOUNT", "banking")]]
    assert result["regenerated_tables"] == 1


def test_sync_leaves_cursor_untouched_when_track_a_fails_after_track_b_succeeded(repo, monkeypatch):
    """The subtlest interaction risk in this function: track B's work
    (registry rows, staged JSON on disk) happens BEFORE track A runs in the
    same call. A failure in track A after track B already "succeeded" must
    still leave the cursor untouched -- proving §9's all-or-nothing
    guarantee holds regardless of which track is the one that fails."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add doc1 and table text")

    import artmind.ingest as ing
    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {"name": domain})
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: {"commit": {"deferred_keys": [("Checking", "ACCOUNT", "banking")]}},
    )
    monkeypatch.setattr(
        ing, "_write_to_neo4j", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("track A boom"))
    )

    with pytest.raises(RuntimeError, match="track A boom"):
        vs.sync(repo, bootstrap_empty=True)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {}


# ── preflight (spec 2026-09-26 §6 A5) ────────────────────────────────────────


def _conflicting_merge(repo, relpath="a.txt"):
    """Leave `repo` mid-merge with `relpath` conflicted."""
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("base\n")
    _commit_all(repo, "base")
    subprocess.run(["git", "checkout", "-qb", "other"], cwd=repo, check=True)
    path.write_text("theirs\n")
    _commit_all(repo, "theirs")
    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo, check=True)
    path.write_text("ours\n")
    _commit_all(repo, "ours")
    subprocess.run(["git", "merge", "other"], cwd=repo, capture_output=True)  # exits 1: conflict


def test_preflight_refuses_during_a_merge(repo, monkeypatch):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _conflicting_merge(repo)

    with pytest.raises(vs.VaultSyncError, match="merge is in progress"):
        vs.sync(repo, bootstrap_empty=True)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {}


def test_preflight_refuses_bootstrap_synced_during_a_merge_too(repo, monkeypatch):
    """Stamping the cursor mid-merge would record a HEAD the user is about to move."""
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _conflicting_merge(repo)

    with pytest.raises(vs.VaultSyncError, match="merge is in progress"):
        vs.sync(repo, bootstrap_synced=True)


def test_preflight_refuses_with_unmerged_paths_even_without_merge_head(repo, monkeypatch):
    """`git merge --abort` never run and MERGE_HEAD deleted by hand (or a
    stash pop conflict): the index still has unmerged entries under
    .artmind/, and a path with a space is named whole (review finding 3:
    the old `.split()` cut it in two)."""
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _conflicting_merge(repo, ".artmind/data/kg/banking/doc 1/observations.json")
    (repo / ".git" / "MERGE_HEAD").unlink()

    with pytest.raises(
        vs.VaultSyncError, match=r"unresolved conflicts .*\.artmind/data/kg/banking/doc 1/observations\.json"
    ):
        vs.sync(repo, bootstrap_empty=True)


def test_preflight_ignores_an_unresolved_conflict_outside_artmind(repo, monkeypatch):
    """Spec 2026-09-26 §14 A7: a conflicted note of the user's does not change
    what sync applies, and Obsidian shows it to them."""
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _conflicting_merge(repo, "notes/My note.md")
    (repo / ".git" / "MERGE_HEAD").unlink()

    result = vs.sync(repo, bootstrap_synced=True)

    assert result["bootstrap"] == "synced"


def test_preflight_refuses_while_the_ingest_worker_is_running(repo, monkeypatch, tmp_path):
    import fcntl
    import os

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    pid_file = repo / ".artmind" / "worker.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    # Liveness is the pid file's flock, not the pid number (a recycled pid
    # must not read as alive) -- so hold it the way a real worker would,
    # via a second open file description in this same process.
    holder = open(pid_file, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(vs.VaultSyncError, match="ingest worker is running"):
            vs.sync(repo, bootstrap_empty=True)
    finally:
        holder.close()


def test_preflight_ignores_a_stale_worker_pid(repo, monkeypatch, tmp_path):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    pid_file = repo / ".artmind" / "worker.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("999999999")  # no such process

    result = vs.sync(repo, bootstrap_synced=True)
    assert result["bootstrap"] == "synced"


def test_preflight_reads_the_vaults_own_pid_file(repo, monkeypatch, tmp_path):
    """Item 1, second half (phase 1 review finding 7): preflight must check
    `vault_dir`'s own pid file, not only the process-wide
    `paths.WORKER_PID_FILE` -- a live worker recorded under *this vault's
    own* `.artmind/worker.pid` must block sync even when the (irrelevant,
    patched-away) `paths.WORKER_PID_FILE` is unlocked."""
    import fcntl
    import os

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    import paths
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "unrelated-worker.pid")

    vault_pid_file = repo / ".artmind" / "worker.pid"
    vault_pid_file.parent.mkdir(parents=True, exist_ok=True)
    vault_pid_file.write_text(str(os.getpid()))
    vault_holder = open(vault_pid_file, "r+")
    fcntl.flock(vault_holder.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(vs.VaultSyncError, match="ingest worker is running"):
            vs.sync(repo, bootstrap_synced=True)
    finally:
        vault_holder.close()


def test_preflight_also_checks_the_process_wide_pid_file(repo, monkeypatch, tmp_path):
    """Re-review item 2: with an explicit ARTMIND_HOME outside the vault (a
    supported setup), the worker for THIS vault writes its pid file at the
    process-wide `paths.WORKER_PID_FILE`, not under `vault_dir/.artmind/`.
    Preflight must still catch it -- checking only the vault's own location
    (item 1's fix alone) would miss a live worker entirely in that setup."""
    import fcntl
    import os

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    import paths
    elsewhere_pid_file = tmp_path / "elsewhere-worker.pid"
    monkeypatch.setattr(paths, "WORKER_PID_FILE", elsewhere_pid_file)
    elsewhere_pid_file.write_text(str(os.getpid()))
    elsewhere_holder = open(elsewhere_pid_file, "r+")
    fcntl.flock(elsewhere_holder.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(vs.VaultSyncError, match="ingest worker is running"):
            vs.sync(repo, bootstrap_synced=True)
    finally:
        elsewhere_holder.close()


def test_preflight_also_checks_the_legacy_data_dir_pid_file(repo, monkeypatch, tmp_path):
    """Re-review item 2: for one release after item 1 moved the pid file
    from DATA_DIR to ARTMIND_HOME, a worker started before the upgrade (and
    still running old code) holds the OLD `DATA_DIR/worker.pid` location.
    Preflight must still catch it."""
    import fcntl
    import os

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    import paths
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "unrelated-worker.pid")
    legacy_dir = tmp_path / "legacy_data"
    legacy_dir.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", legacy_dir)
    legacy_pid_file = legacy_dir / "worker.pid"
    legacy_pid_file.write_text(str(os.getpid()))
    legacy_holder = open(legacy_pid_file, "r+")
    fcntl.flock(legacy_holder.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(vs.VaultSyncError, match="ingest worker is running"):
            vs.sync(repo, bootstrap_synced=True)
    finally:
        legacy_holder.close()


# ── apply reads committed content only (spec 2026-09-26 §6 A1) ───────────────


def test_sync_replays_the_committed_observations_not_the_working_tree(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    (kg_dir / "banking" / "doc1" / "observations.json").write_text('[{"v": "committed"}]')
    _commit_all(repo, "add doc1")
    # An uncommitted edit on disk (a half-finished ingest, or a merge in the
    # working tree) must not be what gets applied.
    (kg_dir / "banking" / "doc1" / "observations.json").write_text('[{"v": "uncommitted"}]')

    import json

    import artmind.ingest as ing

    seen = []

    def _record(doc_kg_dir, domain, defer_rebuild=False):
        seen.append((doc_kg_dir.name, domain, json.loads((doc_kg_dir / "observations.json").read_text())))
        return {"deferred_keys": [], "unembedded_chunk_ids": []}

    _patch_ingest_and_projection(monkeypatch)
    monkeypatch.setattr(ing, "_write_to_neo4j", _record)

    vs.sync(repo, bootstrap_empty=True)

    assert seen == [("doc1", "banking", [{"v": "committed"}])]


def test_sync_carries_the_embedding_sidecar_when_chunks_match_head(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    # The sidecar is gitignored in real vaults; here it's simply never committed.
    (kg_dir / "banking" / "doc1" / "embeddings.json").write_text('{"d1_001": [0.1]}')

    import artmind.ingest as ing

    seen = []
    _patch_ingest_and_projection(monkeypatch)
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda d, domain, defer_rebuild=False: seen.append((d / "embeddings.json").is_file())
        or {"deferred_keys": [], "unembedded_chunk_ids": []},
    )

    vs.sync(repo, bootstrap_empty=True)

    assert seen == [True]


def test_sync_drops_the_sidecar_when_chunks_differ_from_head(repo, monkeypatch):
    """Vectors on disk belong to the on-disk chunks; if those differ from the
    committed chunks, reusing them would attach the wrong vector to a chunk id."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    (kg_dir / "banking" / "doc1" / "chunks.json").write_text('[{"id": "d1_001", "text": "edited"}]')
    (kg_dir / "banking" / "doc1" / "embeddings.json").write_text('{"d1_001": [0.1]}')

    import artmind.ingest as ing

    seen = []
    _patch_ingest_and_projection(monkeypatch)
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda d, domain, defer_rebuild=False: seen.append((d / "embeddings.json").is_file())
        or {"deferred_keys": [], "unembedded_chunk_ids": []},
    )

    vs.sync(repo, bootstrap_empty=True)

    assert seen == [False]


def test_sync_imports_structured_text_from_the_committed_version(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text('{"tables": []}')
    _commit_all(repo, "add table text")
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n2\n")  # uncommitted

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    seen = {}

    def _fake_import(src_dir=None, *, tables=None):
        seen["csv"] = (src_dir / "banking" / "accounts.csv").read_text()
        seen["manifest"] = (src_dir / "manifest.json").is_file()
        seen["src_dir"] = src_dir
        return {"tables_loaded": 1}

    monkeypatch.setattr("artmind.structured.text_export.import_structured_text", _fake_import)
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {"name": domain})
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: {"commit": {"deferred_keys": []}},
    )
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert seen["csv"] == "id\n1\n"
    assert seen["manifest"] is True
    assert seen["src_dir"] != st_dir, "must not read the live structured_text dir"


def test_sync_never_commits(repo, monkeypatch):
    """Spec 2026-09-26 D1: regenerated table__* output stays in the working
    tree; HEAD does not move and nothing is staged."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")
    head_before = vs.head_sha(repo)

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    staged_dir = kg_dir / "banking" / "table__accounts"

    def _fake_table_to_graph(row, mapping, **k):
        staged_dir.mkdir(parents=True, exist_ok=True)
        (staged_dir / "document.json").write_text('{"id": "table:banking:accounts"}')
        return {"commit": {"deferred_keys": []}}

    monkeypatch.setattr("artmind.structured.text_export.import_structured_text", lambda *a, **k: {})
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {"name": domain})
    monkeypatch.setattr(t2g, "table_to_graph", _fake_table_to_graph)
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert vs.head_sha(repo) == head_before
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert staged.strip() == ""
    assert (staged_dir / "document.json").is_file(), "regenerated output is left on disk"


def test_sync_raises_a_clear_error_when_materialize_cannot_archive_the_plan(repo, monkeypatch):
    """`_materialize`'s failure branch: `git archive` exits non-zero when a
    pathspec never existed at `head` (a stale/rewritten plan, or a bug in a
    classifier) -- this must propagate as a `VaultSyncError` naming the
    archive failure, not be silently swallowed or misattributed to some
    other step. Drives it through the public `sync()` API by monkeypatching
    `classify_diff` to return a plan referencing a replay folder that was
    never actually committed, rather than fighting real git diff output to
    construct the scenario."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")

    bogus_plan = vs.SyncPlan(base=vs.EMPTY_TREE_SHA, head=vs.head_sha(repo))
    bogus_plan.replay_docs = [("banking", "does-not-exist")]
    monkeypatch.setattr(vs, "classify_diff", lambda *a, **k: bogus_plan)

    with pytest.raises(vs.VaultSyncError, match="git archive"):
        vs.sync(repo, bootstrap_empty=True)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {}


# ── per-table meta (spec 2026-09-26 §5 R5, §6 A1) ─────────────────────────────


def test_meta_suffix_matches_text_export():
    from artmind.structured.text_export import META_SUFFIX

    assert vs._META_SUFFIX == META_SUFFIX


def test_classify_diff_regenerates_a_table_whose_meta_changed(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text('{"v": 1}')
    _commit_all(repo, "add table text")
    base = vs.head_sha(repo)
    (st_dir / "banking" / "accounts.meta.json").write_text('{"v": 2}')
    _commit_all(repo, "edit meta only")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.regenerate_tables == [("banking", "accounts")]


def test_classify_diff_regenerates_a_table_once_when_csv_and_meta_both_change(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text("{}")
    _commit_all(repo, "add table text")

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.regenerate_tables == [("banking", "accounts")]


def test_classify_diff_a_removed_table_is_retracted_not_regenerated(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text("{}")
    _commit_all(repo, "add table text")
    base = vs.head_sha(repo)
    (st_dir / "banking" / "accounts.csv").unlink()
    (st_dir / "banking" / "accounts.meta.json").write_text('{"v": 2}')
    _commit_all(repo, "drop csv, touch meta")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.regenerate_tables == []
    assert plan.retract == [("banking", "table:banking:accounts")]


def test_sync_materializes_committed_meta_without_a_legacy_manifest(repo, monkeypatch):
    """A1: the import sees the COMMITTED meta, not an uncommitted edit, and a
    vault with no legacy manifest.json at head does not trip `git archive`."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text('{"committed": true}')
    _commit_all(repo, "add table text")
    (st_dir / "banking" / "accounts.meta.json").write_text('{"committed": false}')

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    seen = {}

    def _fake_import(src_dir, tables=None):
        seen["tables"] = tables
        seen["meta"] = (src_dir / "banking" / "accounts.meta.json").read_text()
        seen["csv"] = (src_dir / "banking" / "accounts.csv").read_text()
        seen["manifest"] = (src_dir / "manifest.json").exists()
        return {"tables_loaded": 1}

    monkeypatch.setattr("artmind.structured.text_export.import_structured_text", _fake_import)
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {"name": domain})
    monkeypatch.setattr(t2g, "table_to_graph", lambda row, mapping, **k: {"commit": {"deferred_keys": []}})
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert seen == {
        "tables": [("banking", "accounts")],
        "meta": '{"committed": true}',
        "csv": "id\n1\n",
        "manifest": False,
    }


# ── NUL-separated git listings (spec 2026-09-26 §14 A5) ─────────────────────


def _cafe_fixture(repo, monkeypatch):
    """A document folder and a table whose names git would C-quote (non-ASCII)
    and a shell would split (a space)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "Café notes 2026", "docid-cafe")
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "café accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "café accounts.meta.json").write_text("{}")
    _commit_all(repo, "add café")
    return kg_dir, st_dir


def test_classify_diff_reads_non_ascii_and_space_names_verbatim(repo, monkeypatch):
    _cafe_fixture(repo, monkeypatch)

    plan = vs.classify_diff(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "Café notes 2026")]
    assert plan.regenerate_tables == [("banking", "café accounts")]


def test_classify_diff_retracts_a_removed_non_ascii_folder(repo, monkeypatch):
    kg_dir, _ = _cafe_fixture(repo, monkeypatch)
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "Café notes 2026")
    _commit_all(repo, "remove café notes")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert plan.retract == [("banking", "docid-cafe")]


def test_sync_materializes_non_ascii_names_from_head(repo, monkeypatch):
    _cafe_fixture(repo, monkeypatch)

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    seen = {}

    def _fake_import(src_dir=None, *, tables=None):
        seen["files"] = sorted(p.name for p in (src_dir / "banking").iterdir())
        return {"tables_loaded": 1}

    monkeypatch.setattr("artmind.structured.text_export.import_structured_text", _fake_import)
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "café accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {"name": domain})
    monkeypatch.setattr(t2g, "table_to_graph", lambda row, mapping, **k: {"commit": {"deferred_keys": []}})
    calls = _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert seen["files"] == ["café accounts.csv", "café accounts.meta.json"]
    assert [Path(p).name for p, _ in calls["write_to_neo4j"]] == ["Café notes 2026"]


def test_sync_replays_a_folder_whose_chunks_changed_with_the_committed_chunks(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    chunks = kg_dir / "banking" / "doc1" / "chunks.json"
    chunks.write_text('[{"id": "c1", "text": "committed"}]')
    _commit_all(repo, "edit chunks only")
    chunks.write_text('[{"id": "c1", "text": "uncommitted"}]')
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})  # after the last commit: state.json stays uncommitted

    import json

    import artmind.ingest as ing

    seen = []
    _patch_ingest_and_projection(monkeypatch)
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda d, domain, defer_rebuild=False: seen.append((d.name, json.loads((d / "chunks.json").read_text())))
        or {"deferred_keys": [], "unembedded_chunk_ids": []},
    )

    vs.sync(repo)

    assert seen == [("doc1", [{"id": "c1", "text": "committed"}])]


# ── unmapped tables (spec 2026-09-26 §14 A2) ────────────────────────────────


def test_sync_restores_an_unmapped_table_to_the_structured_store_only(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text("{}")
    _commit_all(repo, "add an unmapped table")

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    calls = {"import": [], "table_to_graph": []}
    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: calls["import"].append(k.get("tables")) or {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [])
    monkeypatch.setattr(t2g, "table_to_graph", lambda *a, **k: calls["table_to_graph"].append(a) or {})
    projection = _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo, bootstrap_empty=True)

    assert calls["import"] == [[("banking", "accounts")]]
    assert calls["table_to_graph"] == []
    assert projection["rebuild_in_batches"] == []
    assert result["structured_only_tables"] == ["banking/accounts"]
    assert _bookmarks(repo) == (vs.head_sha(repo), vs.head_sha(repo))


def test_sync_ambiguous_mapping_still_raises_and_holds_the_cursor(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    from artmind.vault import VaultLayout, read_state, write_state

    (repo / "README.md").write_text("init")
    _commit_all(repo, "init")
    base = vs.head_sha(repo)
    write_state(VaultLayout(repo), {"last_synced_commit": base})

    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text("{}")
    _commit_all(repo, "add a table with an ambiguous mapping")

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object(), object()])
    _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError, match="ambiguous"):
        vs.sync(repo)

    assert read_state(VaultLayout(repo))["last_synced_commit"] == base


def test_sync_mapped_table_with_no_schema_still_raises(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    from artmind.vault import VaultLayout, read_state, write_state

    (repo / "README.md").write_text("init")
    _commit_all(repo, "init")
    base = vs.head_sha(repo)
    write_state(VaultLayout(repo), {"last_synced_commit": base})

    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text("{}")
    _commit_all(repo, "add a mapped table whose domain has no schema")

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {})
    _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError, match="no schema"):
        vs.sync(repo)

    assert read_state(VaultLayout(repo))["last_synced_commit"] == base


def test_sync_dry_run_refuses_a_mapped_table_with_no_schema(repo, monkeypatch):
    """Item 4: the --dryRun branch checked mappings but never called
    load_schema, so a mapped table whose domain schema was deleted passed
    the dry run and then failed the real run -- exactly the case
    `test_sync_mapped_table_with_no_schema_still_raises` above covers for a
    real sync. dry_run must raise the identical VaultSyncError, not just
    silently under-report."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    from artmind.vault import VaultLayout, read_state, write_state

    (repo / "README.md").write_text("init")
    _commit_all(repo, "init")
    base = vs.head_sha(repo)
    write_state(VaultLayout(repo), {"last_synced_commit": base})

    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text("{}")
    _commit_all(repo, "add a mapped table whose domain has no schema")

    import artmind.table2graph as t2g

    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {})

    with pytest.raises(vs.VaultSyncError, match="no schema"):
        vs.sync(repo, dry_run=True)

    assert read_state(VaultLayout(repo))["last_synced_commit"] == base


# ── table sources come from head (review finding 1, spec §14 A1) ─────────────


def _patch_table_sources(monkeypatch, vault_dir):
    """Point TABLE_MAPPINGS_DIR/DOMAIN_SCHEMAS_DIR inside the repo, where a
    real vault has them (`<vault>/.artmind/domains/`)."""
    import paths

    domains = vault_dir / ".artmind" / "domains"
    monkeypatch.setattr(paths, "TABLE_MAPPINGS_DIR", domains / "table_mappings")
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", domains / "schemas")
    return domains / "table_mappings", domains / "schemas"


def _mapping_yaml(pattern, domain=None):
    lines = [f'table: "{pattern}"']
    if domain:
        lines.append(f"domain: {domain}")
    lines += ["entities:", "  acct:", "    class: ACCOUNT", '    name: "{id}"']
    return "\n".join(lines) + "\n"


def _add_table(st_dir, domain, table):
    (st_dir / domain).mkdir(parents=True, exist_ok=True)
    (st_dir / domain / f"{table}.csv").write_text("id\n1\n")
    (st_dir / domain / f"{table}.meta.json").write_text("{}")


def test_sync_projects_a_table_with_the_committed_mapping_and_schema(repo, monkeypatch):
    """Uncommitted edits to the mapping and the schema must not be what track
    B projects with: `head` is the only input (spec §6 A1)."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    maps, schemas = _patch_table_sources(monkeypatch, repo)
    _add_table(st_dir, "banking", "accounts")
    maps.mkdir(parents=True)
    (maps / "accounts.yaml").write_text(_mapping_yaml("acc*"))
    schemas.mkdir(parents=True)
    (schemas / "banking_schema.yaml").write_text("entity_types:\n  ACCOUNT: {kind: recurrent}\n")
    _commit_all(repo, "table, mapping, schema")
    (maps / "accounts.yaml").write_text(_mapping_yaml("nothing_matches_this"))
    (schemas / "banking_schema.yaml").write_text("entity_types:\n  ACCOUNT: {kind: occurrent}\n")

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    seen = []
    monkeypatch.setattr("artmind.structured.text_export.import_structured_text", lambda *a, **k: {"tables_loaded": 1})
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: seen.append((mapping.tables, k["schema"])) or {"commit": {"deferred_keys": []}},
    )
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert seen == [(["acc*"], {"entity_types": {"ACCOUNT": {"kind": "recurrent"}}})]


def test_sync_reports_a_broken_mapping_with_a_vault_relative_path(repo, monkeypatch):
    """A mapping that fails to parse at `head` must not name the scratch
    copy `find_mappings` was pointed at -- that tempdir is gone by the time
    the user reads the error (Task 9 review follow-up 1)."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    maps, schemas = _patch_table_sources(monkeypatch, repo)
    from artmind.vault import VaultLayout, read_state, write_state

    (repo / "README.md").write_text("init")
    _commit_all(repo, "init")
    base = vs.head_sha(repo)
    write_state(VaultLayout(repo), {"last_synced_commit": base})

    _add_table(st_dir, "banking", "accounts")
    maps.mkdir(parents=True)
    (maps / "accounts.yaml").write_text("table: [\n")
    _commit_all(repo, "add a table with a broken mapping")

    from artmind.structured import registry as structured_registry

    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError) as exc_info:
        vs.sync(repo)

    message = str(exc_info.value)
    head = vs.head_sha(repo)
    assert ".artmind/domains/table_mappings/accounts.yaml" in message
    assert "artmind-sync-" not in message
    assert f"(at {head[:12]})" in message
    assert read_state(VaultLayout(repo))["last_synced_commit"] == base


def test_sync_projects_a_table_with_mappings_and_schemas_outside_the_vault(repo, monkeypatch, tmp_path_factory):
    """A mappings/schemas dir outside the vault (an explicit ARTMIND_HOME
    elsewhere) is not versioned with it, so `_dir_at` reads it as is instead
    of materialising a copy from `head` (§14 A1's stated fallback)."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    import paths

    external = tmp_path_factory.mktemp("external_domains")
    maps = external / "table_mappings"
    schemas = external / "schemas"
    maps.mkdir()
    schemas.mkdir()
    monkeypatch.setattr(paths, "TABLE_MAPPINGS_DIR", maps)
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", schemas)

    _add_table(st_dir, "banking", "accounts")
    (maps / "accounts.yaml").write_text(_mapping_yaml("acc*"))
    (schemas / "banking_schema.yaml").write_text("entity_types:\n  ACCOUNT: {kind: recurrent}\n")
    _commit_all(repo, "table only -- mapping/schema live outside the vault")

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    seen = []
    monkeypatch.setattr("artmind.structured.text_export.import_structured_text", lambda *a, **k: {"tables_loaded": 1})
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: seen.append((mapping.tables, k["schema"])) or {"commit": {"deferred_keys": []}},
    )
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert seen == [(["acc*"], {"entity_types": {"ACCOUNT": {"kind": "recurrent"}}})]


def test_sync_dry_run_lists_an_unmapped_table_as_structured_only(repo, monkeypatch):
    """`--dryRun` must report `structured_only_tables` using the mapping
    committed at `head`, the same source the real run uses (Task 8 review
    Minor 1, deferred until Task 9 landed head-sourced mappings)."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    maps, schemas = _patch_table_sources(monkeypatch, repo)
    maps.mkdir(parents=True)
    schemas.mkdir(parents=True)
    (maps / "accounts.yaml").write_text(_mapping_yaml("nothing_matches_this"))
    _add_table(st_dir, "banking", "accounts")
    _commit_all(repo, "an unmapped table")

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry
    from artmind.vault import VaultLayout, read_state

    called = []
    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: called.append("import_structured_text") or {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: called.append("get_table")
        or {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: called.append("table_to_graph") or {"commit": {"deferred_keys": []}},
    )

    result = vs.sync(repo, bootstrap_empty=True, dry_run=True)

    assert result["structured_only_tables"] == ["banking/accounts"]
    assert called == []
    assert "last_synced_commit" not in read_state(VaultLayout(repo))


# ── mappings and schemas as sync input (spec 2026-09-26 §14 A1) ─────────────


def _classify(repo, base, domains=None):

    return vs.classify_diff(repo, base, vs.head_sha(repo), domains)


def _mapped_tables_base(repo, monkeypatch, tables=("accounts", "loans"), pattern="acc*"):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    maps, schemas = _patch_table_sources(monkeypatch, repo)
    for table in tables:
        _add_table(st_dir, "banking", table)
    maps.mkdir(parents=True)
    (maps / "accounts.yaml").write_text(_mapping_yaml(pattern))
    _commit_all(repo, "base")
    return st_dir, maps, schemas, vs.head_sha(repo)


def test_classify_diff_regenerates_tables_whose_mapping_changed(repo, monkeypatch):
    _, maps, _, base = _mapped_tables_base(repo, monkeypatch)
    (maps / "accounts.yaml").write_text(_mapping_yaml("acc*") + "description: edited\n")
    _commit_all(repo, "edit mapping only")

    plan = _classify(repo, base)

    assert plan.regenerate_tables == [("banking", "accounts")]
    assert plan.retract == []


def test_classify_diff_regenerates_tables_an_added_mapping_matches(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    maps, _ = _patch_table_sources(monkeypatch, repo)
    _add_table(st_dir, "banking", "accounts")
    _commit_all(repo, "table only")
    base = vs.head_sha(repo)
    maps.mkdir(parents=True)
    (maps / "accounts.yaml").write_text(_mapping_yaml("accounts"))
    _commit_all(repo, "add mapping")

    assert _classify(repo, base).regenerate_tables == [("banking", "accounts")]


def test_classify_diff_retracts_tables_a_removed_mapping_covered(repo, monkeypatch):
    _, maps, _, base = _mapped_tables_base(repo, monkeypatch, tables=("accounts",))
    (maps / "accounts.yaml").unlink()
    _commit_all(repo, "drop mapping")

    plan = _classify(repo, base)

    assert plan.regenerate_tables == []
    assert plan.retract == [("banking", "table:banking:accounts")]


def test_classify_diff_retracts_tables_a_narrowed_mapping_no_longer_covers(repo, monkeypatch):
    _, maps, _, base = _mapped_tables_base(repo, monkeypatch, tables=("accounts", "accruals"))
    (maps / "accounts.yaml").write_text(_mapping_yaml("accounts"))
    _commit_all(repo, "narrow the pattern")

    plan = _classify(repo, base)

    assert plan.regenerate_tables == [("banking", "accounts")]
    assert plan.retract == [("banking", "table:banking:accruals")]


def test_classify_diff_regenerates_the_mapped_tables_of_a_changed_schema(repo, monkeypatch):
    """Its own domain and dotted children (temporal inheritance); not an
    unmapped table, not another domain."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    maps, schemas = _patch_table_sources(monkeypatch, repo)
    _add_table(st_dir, "banking", "accounts")
    _add_table(st_dir, "banking", "loans")               # unmapped
    _add_table(st_dir, "banking.retail", "accounts_retail")
    _add_table(st_dir, "legal", "accounts_legal")        # other domain
    maps.mkdir(parents=True)
    (maps / "accounts.yaml").write_text(_mapping_yaml("acc*"))
    schemas.mkdir(parents=True)
    (schemas / "banking_schema.yaml").write_text("entity_types: {}\n")
    _commit_all(repo, "base")
    base = vs.head_sha(repo)
    (schemas / "banking_schema.yaml").write_text("entity_types:\n  ACCOUNT: {kind: recurrent}\n")
    _commit_all(repo, "edit schema")

    plan = _classify(repo, base)

    assert plan.regenerate_tables == [("banking", "accounts"), ("banking.retail", "accounts_retail")]
    assert plan.retract == []


def test_classify_diff_reads_mappings_from_git_not_the_working_tree(repo, monkeypatch):
    _, maps, _, base = _mapped_tables_base(repo, monkeypatch)
    (maps / "accounts.yaml").write_text(_mapping_yaml("acc*") + "description: edited\n")
    _commit_all(repo, "edit mapping")
    (maps / "accounts.yaml").unlink()  # uncommitted

    assert _classify(repo, base).regenerate_tables == [("banking", "accounts")]


def test_classify_diff_regenerates_once_when_csv_and_mapping_both_change(repo, monkeypatch):
    st_dir, maps, _, base = _mapped_tables_base(repo, monkeypatch)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n2\n")
    (maps / "accounts.yaml").write_text(_mapping_yaml("acc*") + "description: edited\n")
    _commit_all(repo, "both")

    assert _classify(repo, base).regenerate_tables == [("banking", "accounts")]


def test_classify_diff_scopes_mapping_changes_to_requested_domains(repo, monkeypatch):
    _, maps, _, base = _mapped_tables_base(repo, monkeypatch)
    (maps / "accounts.yaml").unlink()
    _commit_all(repo, "drop mapping")

    plan = _classify(repo, base, domains=["legal"])

    assert plan.regenerate_tables == []
    assert plan.retract == []


def test_sync_retracts_a_table_whose_mapping_was_removed(repo, monkeypatch):
    _patch_kg_dir(monkeypatch, repo)
    _, maps, _, base = _mapped_tables_base(repo, monkeypatch, tables=("accounts",))
    (maps / "accounts.yaml").unlink()
    _commit_all(repo, "drop mapping")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})  # after the last commit: state.json stays uncommitted

    import artmind.table2graph as t2g

    calls = _patch_ingest_and_projection(
        monkeypatch, retract_results={"table:banking:accounts": {"affected_keys": [["1", "ACCOUNT", "banking"]]}}
    )
    monkeypatch.setattr(t2g, "table_to_graph", lambda *a, **k: pytest.fail("a table no mapping names is never projected"))

    result = vs.sync(repo)

    assert calls["retract_document"] == [("table:banking:accounts", "banking")]
    assert calls["rebuild_in_batches"] == [[("1", "ACCOUNT", "banking")]]
    assert result["retracted"] == 1
    assert result["regenerated_tables"] == 0


# ── dry run raises the same ambiguity error the real run does ───────────────


def test_sync_dry_run_raises_on_an_ambiguous_mapping(repo, monkeypatch):
    """Two mappings at `head` matching one table must raise, in dry-run too
    -- the real run already refuses; a dry run that reports the table as
    normal instead of surfacing the ambiguity would mislead the user into
    thinking the sync is safe (Task 9 re-review)."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    maps, schemas = _patch_table_sources(monkeypatch, repo)
    _add_table(st_dir, "banking", "accounts")
    maps.mkdir(parents=True)
    (maps / "acc.yaml").write_text(_mapping_yaml("acc*"))
    (maps / "all.yaml").write_text(_mapping_yaml("*"))
    _commit_all(repo, "two mappings match the same table")

    with pytest.raises(vs.VaultSyncError, match="ambiguous"):
        vs.sync(repo, bootstrap_empty=True, dry_run=True)


# ── pinning the approved failure rules (Task 10 review) ─────────────────────


def test_classify_diff_raises_on_a_mapping_broken_at_head(repo, monkeypatch):
    """A mapping edited into invalid YAML fails the classifier itself (not
    just the later `find_mappings` call in `sync()`'s regenerate loop) --
    the message names the vault-relative path once and the `head` sha once,
    never the scratch tempdir. (A PyYAML error is itself multi-line, so the
    match uses DOTALL rather than pytest.raises' plain `re.search`.)"""
    _, maps, _, base = _mapped_tables_base(repo, monkeypatch, tables=("accounts",))
    (maps / "accounts.yaml").write_text("table: [\n")
    _commit_all(repo, "break the mapping")

    with pytest.raises(
        vs.VaultSyncError, match=re.compile(r"accounts\.yaml.*\(at [0-9a-f]{12}\)", re.DOTALL)
    ) as ei:
        _classify(repo, base)
    assert str(ei.value).count(vs.head_sha(repo)[:12]) == 1


def test_classify_diff_skips_a_mapping_broken_at_base_once_it_is_deleted(repo, monkeypatch):
    """A mapping that never parsed at `base` cannot be checked against the
    tables it used to match there -- refusing outright would stall sync on
    history no edit can fix, so it is logged and treated as if it matched
    nothing (§14 A1's stated skip). Loguru doesn't feed pytest's `caplog`
    without a bridge (see test_embed_sweep.py), so this test adds its own
    sink for the duration of the call."""
    from loguru import logger

    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    maps, _ = _patch_table_sources(monkeypatch, repo)
    _add_table(st_dir, "banking", "accounts")
    maps.mkdir(parents=True)
    (maps / "accounts.yaml").write_text("table: [\n")
    _commit_all(repo, "base: a mapping that never parsed")
    base = vs.head_sha(repo)
    (maps / "accounts.yaml").unlink()
    _commit_all(repo, "delete the broken mapping")

    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="WARNING")
    try:
        plan = _classify(repo, base)
    finally:
        logger.remove(sink_id)

    assert plan.regenerate_tables == []
    assert plan.retract == []
    assert any("does not parse" in m for m in messages)


def test_classify_diff_raises_on_a_pre_existing_broken_mapping_when_a_schema_changes(repo, monkeypatch):
    """`_mappings_at_head`'s full listing of every mapping at `head` -- not just
    the ones the diff touched -- must also fail loudly on one that doesn't
    parse; unwrapped, that call raised a bare `MappingError` without the
    "(at <sha>)" suffix every other broken-mapping report carries."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    maps, schemas = _patch_table_sources(monkeypatch, repo)
    _add_table(st_dir, "banking", "accounts")
    maps.mkdir(parents=True)
    (maps / "broken.yaml").write_text("table: [\n")
    schemas.mkdir(parents=True)
    (schemas / "banking_schema.yaml").write_text("entity_types: {}\n")
    _commit_all(repo, "base: table + already-broken mapping + schema")
    base = vs.head_sha(repo)
    (schemas / "banking_schema.yaml").write_text("entity_types:\n  ACCOUNT: {kind: recurrent}\n")
    _commit_all(repo, "edit schema only -- the broken mapping is untouched")

    with pytest.raises(
        vs.VaultSyncError, match=re.compile(r"broken\.yaml.*\(at [0-9a-f]{12}\)", re.DOTALL)
    ):
        _classify(repo, base)


def test_sync_raises_no_schema_when_a_mapped_tables_schema_is_deleted(repo, monkeypatch):
    """A mapping still covers the table at `head`, but its domain's schema
    was deleted -- `load_schema` returns `{}`, and `sync()` must refuse
    rather than project without one, leaving the cursor untouched."""
    _patch_kg_dir(monkeypatch, repo)
    st_dir, maps, schemas, base = _mapped_tables_base(repo, monkeypatch, tables=("accounts",))
    schemas.mkdir(parents=True, exist_ok=True)
    (schemas / "banking_schema.yaml").write_text("entity_types:\n  ACCOUNT: {kind: recurrent}\n")
    _commit_all(repo, "add the schema")
    synced_base = vs.head_sha(repo)
    (schemas / "banking_schema.yaml").unlink()
    _commit_all(repo, "delete the schema -- the mapping still covers accounts")

    from artmind.vault import VaultLayout, read_state, write_state

    write_state(VaultLayout(repo), {"last_synced_commit": synced_base})

    from artmind.structured import registry as structured_registry

    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError, match="no schema"):
        vs.sync(repo)

    assert read_state(VaultLayout(repo))["last_synced_commit"] == synced_base


def test_sync_restores_but_never_projects_when_csv_changes_and_mapping_is_removed(repo, monkeypatch):
    """The CSV change makes track B want to regenerate the table (restore to
    DuckDB); the mapping's removal makes the same table's old projection
    retracted from the graph. Both fire in one run, on the same table --
    `classify_diff`'s docstring now says so -- and it must never be
    projected with a mapping that no longer names it."""
    _patch_kg_dir(monkeypatch, repo)
    st_dir, maps, _, base = _mapped_tables_base(repo, monkeypatch, tables=("accounts",))
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n2\n")
    (maps / "accounts.yaml").unlink()
    _commit_all(repo, "csv changed, mapping removed, in the same commit")

    from artmind.vault import VaultLayout, write_state

    write_state(VaultLayout(repo), {"last_synced_commit": base})

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    seen_import: list = []
    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda scratch_dir, tables: seen_import.append(tables) or {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda *a, **k: pytest.fail("a table no mapping names any more is never projected"),
    )
    calls = _patch_ingest_and_projection(
        monkeypatch, retract_results={"table:banking:accounts": {"affected_keys": []}}
    )

    vs.sync(repo)

    assert seen_import == [[("banking", "accounts")]]
    assert calls["retract_document"] == [("table:banking:accounts", "banking")]


# ── per-store bookmarks (spec 2026-09-26 §6 A2) ───────────────────────────────


def _patch_track_b(monkeypatch):
    """Track B's seams: records which tables were restored into DuckDB and
    which were projected into the graph, with what."""
    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry

    calls = {"import": [], "table_to_graph": []}
    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: calls["import"].append(list(k.get("tables"))) or {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": domain, "table_name": table_name},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain, mappings_dir=None: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain, schemas_dir=None: {"name": domain})
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: calls["table_to_graph"].append(row["table_name"]) or {"commit": {"deferred_keys": []}},
    )
    return calls


def _three_commits(repo, monkeypatch):
    """c1: doc1. c2: doc2 + accounts table. c3: doc3 + loans table."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    shas = []
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "c1")
    shas.append(vs.head_sha(repo))
    _write_doc_folder(kg_dir, "banking", "doc2", "docid-2")
    _add_table(st_dir, "banking", "accounts")
    _commit_all(repo, "c2")
    shas.append(vs.head_sha(repo))
    _write_doc_folder(kg_dir, "banking", "doc3", "docid-3")
    _add_table(st_dir, "banking", "loans")
    _commit_all(repo, "c3")
    shas.append(vs.head_sha(repo))
    return shas


def test_the_legacy_cursor_seeds_both_bookmarks_then_is_retired(repo, monkeypatch, graph):
    c1, _, c3 = _three_commits(repo, monkeypatch)
    from artmind.vault import VaultLayout, read_state, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": c1})
    calls = _patch_ingest_and_projection(monkeypatch)
    track_b = _patch_track_b(monkeypatch)

    result = vs.sync(repo)

    assert result["bases"] == {"graph": c1, "structured": c1}
    assert sorted(Path(p).name for p, _ in calls["write_to_neo4j"]) == ["doc2", "doc3"]
    assert track_b["import"] == [[("banking", "accounts"), ("banking", "loans")]]
    assert _bookmarks(repo) == (c3, c3)
    assert "last_synced_commit" not in read_state(VaultLayout(repo))


def test_the_graphs_own_bookmark_wins_over_a_differing_legacy_cursor(repo, monkeypatch, graph):
    """Shared AuraDB: the other machine already applied up to c2, and wrote
    that into the graph. This machine's legacy cursor says c1 -- right for
    its own DuckDB, too old for the shared graph."""
    c1, c2, c3 = _three_commits(repo, monkeypatch)
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": c1})
    graph.bookmarks[VAULT_ID] = c2
    calls = _patch_ingest_and_projection(monkeypatch)
    track_b = _patch_track_b(monkeypatch)

    result = vs.sync(repo)

    assert result["bases"] == {"graph": c2, "structured": c1}
    assert [Path(p).name for p, _ in calls["write_to_neo4j"]] == ["doc3"]
    assert track_b["table_to_graph"] == ["loans"], "only the graph's own range is projected"
    assert track_b["import"] == [[("banking", "loans"), ("banking", "accounts")]], (
        "DuckDB also restores what its own, older range covers"
    )
    assert _bookmarks(repo) == (c3, c3)


def test_a_graph_ahead_of_the_structured_store_replays_nothing_into_the_graph(repo, monkeypatch, graph):
    c1, _, c3 = _three_commits(repo, monkeypatch)
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_structured_commit": c1})
    graph.bookmarks[VAULT_ID] = c3
    calls = _patch_ingest_and_projection(monkeypatch)
    track_b = _patch_track_b(monkeypatch)

    vs.sync(repo)

    assert calls["write_to_neo4j"] == []
    assert track_b["table_to_graph"] == []
    assert track_b["import"] == [[("banking", "accounts"), ("banking", "loans")]]
    assert _bookmarks(repo) == (c3, c3)


def test_equal_bases_classify_the_structured_tables_once(repo, monkeypatch, graph):
    """Per-store ranges must not re-read git when both stores start from the
    same commit (the common case)."""
    c1, c2, _ = _three_commits(repo, monkeypatch)
    from artmind.vault import VaultLayout, write_state
    _patch_ingest_and_projection(monkeypatch)
    _patch_track_b(monkeypatch)
    real = vs._classify_structured_text_diff
    seen = []
    monkeypatch.setattr(vs, "_classify_structured_text_diff", lambda v, b, h, d: seen.append(b) or real(v, b, h, d))

    write_state(VaultLayout(repo), {"last_structured_commit": c1})
    graph.bookmarks[VAULT_ID] = c1
    vs.sync(repo, dry_run=True)
    assert seen == [c1]

    seen.clear()
    graph.bookmarks[VAULT_ID] = c2
    vs.sync(repo, dry_run=True)
    assert seen == [c2, c1]


def test_a_missing_structured_bookmark_is_refused_and_names_the_store(repo, monkeypatch, graph):
    c1, _, _ = _three_commits(repo, monkeypatch)
    graph.bookmarks[VAULT_ID] = c1
    calls = _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError, match=r"no structured bookmark.*--store structured"):
        vs.sync(repo)

    assert calls["write_to_neo4j"] == []
    assert graph.bookmarks == {VAULT_ID: c1}


def test_store_structured_bootstraps_duckdb_alone_without_touching_the_graph(repo, monkeypatch, graph):
    _, _, c3 = _three_commits(repo, monkeypatch)
    calls = _patch_ingest_and_projection(monkeypatch)
    track_b = _patch_track_b(monkeypatch)

    def _no_graph(*a, **k):
        raise AssertionError("a structured-only run must never read the graph")

    from artmind import sync_state
    monkeypatch.setattr(sync_state, "read_graph_bookmark", _no_graph)

    result = vs.sync(repo, store="structured", bootstrap_empty=True)

    assert result["stores"] == ["structured"]
    assert track_b["import"] == [[("banking", "accounts"), ("banking", "loans")]]
    assert track_b["table_to_graph"] == []
    assert calls["write_to_neo4j"] == []
    assert graph.bookmarks == {}
    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {"last_structured_commit": c3}


def test_store_graph_bootstrap_synced_stamps_only_the_graph(repo, monkeypatch, graph):
    _, _, c3 = _three_commits(repo, monkeypatch)

    result = vs.sync(repo, store="graph", bootstrap_synced=True)

    assert result == {"bootstrap": "synced", "stores": ["graph"], "graph_bookmark": c3}
    assert _bookmarks(repo) == (c3, None)


def test_the_legacy_cursor_survives_until_both_stores_have_their_own(repo, monkeypatch, graph):
    c1, _, c3 = _three_commits(repo, monkeypatch)
    from artmind.vault import VaultLayout, read_state, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": c1})
    _patch_ingest_and_projection(monkeypatch)
    _patch_track_b(monkeypatch)

    vs.sync(repo, store="structured")

    assert read_state(VaultLayout(repo)) == {"last_synced_commit": c1, "last_structured_commit": c3}
    vs.sync(repo, store="graph")
    assert read_state(VaultLayout(repo)) == {"last_structured_commit": c3}
    assert _bookmarks(repo) == (c3, c3)


def test_the_legacy_cursor_survives_until_both_stores_have_their_own_graph_first(repo, monkeypatch, graph):
    """Mirror of the test above with the orderings swapped -- graph bootstraps
    first (e.g. right after `session initiate`, which only sets the graph
    bookmark), then structured. `read_bookmarks` only queries the graph
    `if "graph" in stores`, so the second (structured-only) run never reads
    the graph's bookmark itself -- it must still see that the graph already
    has one, and retire the legacy cursor, rather than mistaking "didn't
    check" for "doesn't exist"."""
    c1, _, c3 = _three_commits(repo, monkeypatch)
    from artmind.vault import VaultLayout, read_state, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": c1})
    _patch_ingest_and_projection(monkeypatch)
    _patch_track_b(monkeypatch)

    vs.sync(repo, store="graph")

    assert read_state(VaultLayout(repo)) == {"last_synced_commit": c1}
    assert _bookmarks(repo) == (c3, None)

    vs.sync(repo, store="structured")

    assert read_state(VaultLayout(repo)) == {"last_structured_commit": c3}, (
        "both stores now have their own bookmark -- the legacy cursor must be retired "
        "even though this run never itself read the graph bookmark"
    )
    assert _bookmarks(repo) == (c3, c3)


def test_a_graph_bookmark_this_clone_has_not_pulled_is_refused(repo, monkeypatch, graph):
    """The other machine sharing the graph applied a commit this clone does
    not have. Diffing from it would retract that machine's documents."""
    c1, _, _ = _three_commits(repo, monkeypatch)
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_structured_commit": c1})
    graph.bookmarks[VAULT_ID] = "0123456789abcdef0123456789abcdef01234567"
    calls = _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError, match="not a commit in this clone.*pull"):
        vs.sync(repo)

    assert calls["write_to_neo4j"] == [] and calls["retract_document"] == []


def test_a_graph_bookmark_ahead_of_head_is_refused(repo, monkeypatch, graph):
    c1, c2, c3 = _three_commits(repo, monkeypatch)
    graph.bookmarks[VAULT_ID] = c3
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_structured_commit": c1})
    subprocess.run(["git", "checkout", "-q", "-b", "behind", c2], cwd=repo, check=True)
    calls = _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError, match="not an ancestor of HEAD"):
        vs.sync(repo)

    assert calls["retract_document"] == [], "doc3 must not be retracted from the shared graph"
    assert graph.bookmarks == {VAULT_ID: c3}


def test_the_not_an_ancestor_refusal_names_both_bootstrap_flags(repo, monkeypatch, graph):
    """Pulling only fixes an unpulled bookmark. A history rewrite leaves
    nothing to pull, so the message must also say how to recover without one --
    otherwise a user stuck here has no way forward."""
    c1, c2, c3 = _three_commits(repo, monkeypatch)
    graph.bookmarks[VAULT_ID] = c3
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_structured_commit": c1})
    subprocess.run(["git", "checkout", "-q", "-b", "behind", c2], cwd=repo, check=True)
    _patch_ingest_and_projection(monkeypatch)

    with pytest.raises(vs.VaultSyncError, match=r"--store graph --bootstrapSynced.*--store graph --bootstrapEmpty"):
        vs.sync(repo)


def test_a_vault_without_a_vault_id_is_told_to_run_init(repo, monkeypatch):
    _three_commits(repo, monkeypatch)
    (repo / ".artmind" / "vault.yaml").write_text("ingest:\n  trigger: manual\n")

    with pytest.raises(vs.VaultSyncError, match="no vault_id.*artmind init"):
        vs.sync(repo, bootstrap_empty=True)


def test_a_failed_run_leaves_both_bookmarks_and_the_legacy_cursor_alone(repo, monkeypatch, graph):
    c1, _, _ = _three_commits(repo, monkeypatch)
    from artmind.vault import VaultLayout, read_state, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": c1})
    _patch_track_b(monkeypatch)
    import artmind.ingest as ing
    monkeypatch.setattr(ing, "_write_to_neo4j", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        vs.sync(repo)

    assert graph.bookmarks == {}
    assert read_state(VaultLayout(repo)) == {"last_synced_commit": c1}


# ── fingerprints make a shared graph cheap (spec 2026-09-26 §6 A3) ───────────


def _fp(folder):
    from artmind.sync_state import folder_fingerprint
    return folder_fingerprint(folder)


def test_a_folder_whose_fingerprint_the_graph_already_has_is_not_replayed(repo, monkeypatch, graph):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _write_doc_folder(kg_dir, "banking", "doc2", "docid-2")
    _commit_all(repo, "add two docs")
    graph.fingerprints["docid-1"] = _fp(kg_dir / "banking" / "doc1")
    calls = _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo, bootstrap_empty=True)

    assert [Path(p).name for p, _ in calls["write_to_neo4j"]] == ["doc2"]
    assert (result["replayed"], result["unchanged"]) == (1, 1)
    assert graph.fingerprint_reads == [["docid-1", "docid-2"]], "one Cypher read for the whole plan"
    assert _bookmarks(repo) == (vs.head_sha(repo), vs.head_sha(repo))


def test_a_different_or_missing_fingerprint_is_replayed(repo, monkeypatch, graph):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _write_doc_folder(kg_dir, "banking", "doc2", "docid-2")
    _commit_all(repo, "add two docs")
    graph.fingerprints["docid-1"] = "0" * 64
    graph.fingerprints["docid-2"] = None  # committed before fingerprints existed
    calls = _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert sorted(Path(p).name for p, _ in calls["write_to_neo4j"]) == ["doc1", "doc2"]


def test_the_committed_bytes_are_compared_not_the_working_tree(repo, monkeypatch, graph):
    """The graph holds what ingest wrote; the commit holds the same bytes.
    An uncommitted edit on disk must neither cause a replay nor mask one."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    folder = kg_dir / "banking" / "doc1"
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    graph.fingerprints["docid-1"] = _fp(folder)
    (folder / "observations.json").write_text('[{"v": "uncommitted"}]')
    calls = _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True, dry_run=False)

    assert calls["write_to_neo4j"] == []


def test_a_dry_run_reports_unchanged_folders(repo, monkeypatch, graph):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    graph.fingerprints["docid-1"] = _fp(kg_dir / "banking" / "doc1")

    result = vs.sync(repo, bootstrap_empty=True, dry_run=True)

    assert (result["replay"], result["unchanged"]) == (0, 1)


def test_nothing_to_replay_reads_no_fingerprints(repo, monkeypatch, graph):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "note.md").write_text("x")
    _commit_all(repo, "a note")
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert graph.fingerprint_reads == []


def test_shared_graph_ingest_on_one_machine_is_a_no_op_apply_on_the_other(repo, monkeypatch, graph):
    """Spec 2026-09-26 §11 "Bookmarks": machine A ingests (its real commit
    path records the fingerprint), Obsidian Git commits the folder, machine B
    -- same graph -- applies nothing and just advances the bookmark."""
    from contextlib import contextmanager

    import artmind.graph_query as graph_query
    import artmind.ingest as ing

    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    folder = kg_dir / "banking" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text('{"id": "docid-1", "name": "a.md"}')
    (folder / "observations.json").write_text('[{"id": "o1", "key": "Acme|ORG|banking"}]')
    (folder / "chunks.json").write_text("[]")
    (folder / "relationships.json").write_text("[]")

    sent = []

    class _Session:
        def run(self, cypher, **params):
            if "SET d.fingerprint" in cypher:
                sent.append(params)
            return type("R", (), {"single": lambda s: None, "data": lambda s: [], "consume": lambda s: None})()

        def execute_write(self, fn, *a, **k):
            return fn(self, *a, **k)

    @contextmanager
    def _fake(*a, **k):
        yield _Session()

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)   # machine A's ingest
    graph.fingerprints[sent[0]["doc_id"]] = sent[0]["fingerprint"]
    _commit_all(repo, "Obsidian Git auto-commit")

    calls = _patch_ingest_and_projection(monkeypatch)
    result = vs.sync(repo, bootstrap_empty=True)             # machine B's apply

    assert calls["write_to_neo4j"] == []
    assert result["unchanged"] == 1
    assert graph.bookmarks[VAULT_ID] == vs.head_sha(repo)


def test_committed_fingerprints_handles_a_folder_missing_one_fingerprinted_file(repo, monkeypatch, graph):
    """A folder that never got one of the four `FINGERPRINTED_FILES` (e.g.
    ingest wrote no relationships for a document with none) must fingerprint
    at `head` exactly the way `folder_fingerprint` fingerprints an on-disk
    folder with that same file absent -- and `drop_unchanged` must still
    skip it once the graph carries that fingerprint. Every other test here
    uses `_write_doc_folder`'s default `extra_files`, which always writes
    all four files, so this is the only coverage of a genuinely partial
    committed folder."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1", extra_files=("chunks.json",))
    _commit_all(repo, "add doc1, relationships.json never written")
    head = vs.head_sha(repo)
    folder = kg_dir / "banking" / "doc1"
    assert not (folder / "relationships.json").exists()

    committed = vs.committed_fingerprints(repo, head, [("banking", "doc1")])

    assert committed == {("banking", "doc1"): ("docid-1", _fp(folder))}

    graph.fingerprints["docid-1"] = _fp(folder)
    plan = vs.SyncPlan(base=vs.EMPTY_TREE_SHA, head=head, replay_docs=[("banking", "doc1")])

    unchanged = vs.drop_unchanged(repo, plan)

    assert unchanged == [("banking", "doc1")]
    assert plan.replay_docs == []


def test_committed_fingerprints_resolves_both_folders_sharing_a_deduped_blob(repo, monkeypatch):
    """`_cat_blobs` dedupes the oid list (`list(dict.fromkeys(oids))`) before
    the one `git cat-file --batch` call, then looks each folder's files back
    up by oid. Two folders here share identical bytes -- and so one git blob
    oid -- in `observations.json`, `chunks.json` and `relationships.json`
    (only `document.json` differs, carrying each folder's own id); confirm
    `committed_fingerprints` still resolves the correct, distinct fingerprint
    for BOTH folders rather than mixing them up or dropping one."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _write_doc_folder(kg_dir, "banking", "doc2", "docid-2")
    _commit_all(repo, "add two docs sharing identical observations/chunks/relationships blobs")
    head = vs.head_sha(repo)

    # Confirm the premise: the two folders' observations.json really do share one oid.
    def _blob_oid(docdir, name):
        return subprocess.run(
            ["git", "rev-parse", f"{head}:.artmind/data/kg/banking/{docdir}/{name}"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

    oid1, oid2 = _blob_oid("doc1", "observations.json"), _blob_oid("doc2", "observations.json")
    assert oid1 == oid2 and oid1

    committed = vs.committed_fingerprints(repo, head, [("banking", "doc1"), ("banking", "doc2")])

    assert committed == {
        ("banking", "doc1"): ("docid-1", _fp(kg_dir / "banking" / "doc1")),
        ("banking", "doc2"): ("docid-2", _fp(kg_dir / "banking" / "doc2")),
    }
    assert committed[("banking", "doc1")][1] != committed[("banking", "doc2")][1]
