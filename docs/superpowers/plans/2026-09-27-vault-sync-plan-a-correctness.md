# Vault sync — Plan A (correctness) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `artmind vault sync` applies exactly what git brought in, and nothing else: table mappings and domain schemas become sync inputs read from `HEAD`, unmapped tables stop stalling the cursor, table ids stop colliding across machines, every staging writer swaps its folder in atomically, a document folder replays on any file change, git listings are NUL-separated, and only conflicts under `.artmind/` block a sync.

**Architecture:** Spec [2026-09-26-vault-git-transport-design.md](../specs/2026-09-26-vault-git-transport-design.md) §14 amendments A1–A5 and A7, plus the leftovers of the phase 1 review ([2026-09-26-vault-git-transport-phase1-review.md](../reviews/2026-09-26-vault-git-transport-phase1-review.md), findings 1, 3, 6, 7, 9) and the phase 2 "Execution notes" follow-ups 1–6 ([2026-09-26-vault-git-transport-phase2.md](./2026-09-26-vault-git-transport-phase2.md)). Nearly all of the work is in `artmind/vault_sync.py`: its classifier learns a third input (mappings + schemas), reads every git listing with `-z`, and groups track A by folder instead of by `observations.json`. Around it: `artmind/structured/{text_export,registry}.py` remap table ids by `(domain, table_name)`, `artmind/atomic_dir.py` gains a wholesale-replace mode used by archive restore, and the dashboard import and `kg_pull` go through the same swap. A new `artmind/worker_pid.py` replaces three copies of the worker-liveness check. `artmind/vault.py`'s block writer is hardened (exact header, CRLF, duplicate blocks).

**Verified:** every code block in this plan was applied, in task order, to a scratch worktree of `a4a0760` and the full suite run after it: `2277 passed, 14 skipped`. The "expected FAIL" claims were spot-checked for Tasks 1, 7, 9, 10, 11 and 13 by reverting that task's source change.

**Tech Stack:** Python 3.14, Click, pytest (`uv run --group dev pytest test/ -q`, ~40 s, 2230 passed / 14 skipped at the start of this plan), real throwaway git repos in `tmp_path` for anything git decides, mocked Neo4j seams that record the parameters sent.

---

## Context for the implementer

Read these before starting:

- `CLAUDE.md`, "Testing implications" §1–§4. §3 matters most here: when a test mocks a graph write, assert on **what was sent** (which doc id, which mapping, which bytes), never on a count.
- The spec above: §5 R2/R4/R5, §6 A1/A5, and all of §14.
- `test/test_vault_sync.py` top to bottom. Its helpers are reused by name in this plan: `repo` (a `git init`ed `tmp_path`), `_commit_all`, `_write_doc_folder`, `_patch_kg_dir`, `_patch_structured_text_dir`, `_patch_ingest_and_projection` (records `write_to_neo4j`, `retract_document`, `rebuild_in_batches`, `sweeps`), `_conflicting_merge`.
- `test/test_no_git_writes.py`: fails the suite if any module under `artmind/` or `utils/` contains a git write call. Nothing in this plan adds one.

Terminology:

- **Vault**: the user's Obsidian vault, which is also a git repo. artmind's files live in `<vault>/.artmind/`. In a vault, `paths.ARTMIND_HOME` is `<vault>/.artmind`, so `paths.TABLE_MAPPINGS_DIR` is `<vault>/.artmind/domains/table_mappings` and `paths.DOMAIN_SCHEMAS_DIR` is `<vault>/.artmind/domains/schemas`. Both are committed.
- **Track A**: `vault sync` replaying KG document folders (`.artmind/data/kg/<domain>/<docdir>/`).
- **Track B**: `vault sync` restoring structured tables (`.artmind/data/structured_text/<domain>/<table>.csv` + `<table>.meta.json`) into DuckDB, then projecting them into the graph with `table2graph` when a table mapping names them. Their graph document id is `table:<domain>:<table>`, staged in the gitignored `.artmind/data/kg/<domain>/table__<table>/`.
- **`base..head`**: the fixed commit range one sync run applies. `head` is `git rev-parse HEAD` at the start; every input is read at `head` through `git show` / `git ls-tree` / `git archive`, never from the working tree.

**Test isolation you will hit.** `test/conftest.py` points `ARTMIND_HOME` at a temp directory **outside** every test repo, so by default `paths.TABLE_MAPPINGS_DIR`, `paths.DOMAIN_SCHEMAS_DIR` and `paths.KG_DIR` are not under `repo`. `vault_sync` treats "not under the vault" as "not versioned with it" and skips or falls back (the `ValueError` guards in the classifiers). Tests that exercise a path patch it to live under `repo` first, as `_patch_kg_dir` does.

**Line numbers** below are as of commit `a4a0760` (the start of this plan). Earlier tasks shift later line numbers in the same file, so every replacement quotes the exact old code: match on the text.

Run the suite with:

```bash
uv run --group dev pytest test/ -q
```

Do **not** run `just dev-install`, `artmind init` outside `tmp_path`, or anything against a live Neo4j. The user tests live afterwards.

## Already fixed (no task)

| Item | Evidence on this branch |
|---|---|
| Review finding 4 — stale `justfile` comment | `justfile:141` now reads `# archive a document: bundle it, then remove it from the graph AND delete it from the vault (Obsidian Git commits the deletion) ...`. No `git rm`/commit wording remains (`grep -n "git rm\|commit" justfile` only hits line 141's "Obsidian Git commits the deletion"). |
| Review finding 5 — `manifest.json` missing at `head` gives a raw `git archive` error | Phase 2 added `_present_at` (`artmind/vault_sync.py:180-187`), so only paths that exist at `head` are archived; pinned by `test_sync_materializes_committed_meta_without_a_legacy_manifest` (`test/test_vault_sync.py:999`). Not in this plan's scope list; listed so nobody re-opens it. |
| Review finding 8 — interim `table__*` churn | Phase 2 gitignored `table__*` (`artmind/vault.py:279`) and track A skips them (`artmind/vault_sync.py:265`). |
| Review finding 9, first bullet's premise | Still open — see Task 14. (Listed here only because the annotation line moved: it is `utils/functions.py:76-77`, not `:77`.) |
| Phase 2 follow-up 6, "folder briefly absent between the two renames" | Accepted by the spec (phase 2 execution notes); deliberately not addressed. |

Everything else in scope is still open on this branch; each has a task below.

## Out of scope (noticed while planning)

- `_rebuild_in_batches` (`artmind/table2graph.py:1246`) reads `same_as.yaml` from the working tree through `same_as.load_groups()`. That is spec §7 track D (phase 4), not Plan A.
- Spec §14 A6 (conflict records travel) and A8 (automatic apply) are separate plans.

## File map

| File | Change |
|---|---|
| `artmind/vault_sync.py` | `-z` listings (Task 1); preflight scoped to `.artmind/` (Task 2); track A grouped by folder (Task 3); unmapped tables restore-only (Task 8); mappings/schemas read from `head` (Task 9); mapping/schema diff classifier (Task 10); shared worker check (Task 11); `_carry_sidecar` moved out (Task 14) |
| `artmind/vault_doctor.py` | `ls-files -z` (Task 1); `duplicate` block status (Task 13) |
| `artmind/atomic_dir.py` | `write_dir_atomic(..., carry_over=False)` (Task 4) |
| `artmind/archive.py` | restore swaps the KG folder in atomically (Task 4) |
| `artmind/webui/dashboard_routes.py` | artifact import swaps atomically (Task 5) |
| `artmind/kg_pull.py` | pulled folders swap atomically; symlinks skipped (Task 6) |
| `artmind/structured/text_export.py` | `load_structured_dump` gives every table a unique id (Task 7) |
| `artmind/structured/registry.py` | `restore_tables` keeps local ids, allocates on collision (Task 7) |
| `artmind/temporal.py` | `load_schema(domain, schemas_dir=None)` (Task 9) |
| `artmind/worker_pid.py` | new: `live_pid()` (Task 11) |
| `artmind/worker.py`, `artmind/cli.py` | use `live_pid()` (Task 11); `vault sync` docstring (Task 15) |
| `artmind/vault.py` | exact header regex, CRLF-preserving writes, duplicate-block detection (Task 13) |
| `artmind/ingest.py` | `carry_embedding_sidecar()` beside `EMBEDDING_SIDECAR` (Task 14) |
| `utils/functions.py` | `run_command` annotation (Task 14) |
| `test/test_vault_sync.py`, `test/test_vault_doctor.py`, `test/test_atomic_dir.py`, `test/test_archive.py`, `test/test_webui_admin_api.py`, `test/test_kg_pull.py`, `test/test_structured_text_export.py`, `test/test_worker_manifest.py`, `test/test_vault_scaffold.py`, `test/test_ingest_staging_atomic.py`, `test/test_ingest_sync_cli.py`, `test/test_ingest_vault_native.py` | extended / comments fixed |
| `test/test_worker_pid.py`, `test/test_temporal_load_schema.py` | new |
| `docs/vault.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md` | prose (Task 15) |

---

### Task 1: NUL-separated git listings (spec §14 A5, phase 2 follow-up 4)

Without `-z`, git C-quotes any path containing a non-ASCII byte (`"Caf\303\251 notes/..."`), so `_diff_name_status` hands the classifier a path that starts with `"` and `Path.relative_to` raises; `_present_at` silently drops the quoted name, so a table's `.meta.json` never reaches the scratch dir. `vault doctor`'s tracked-but-ignored check has the same bug and prints a broken `git rm` line. Every later task builds on these two helpers, so this goes first.

**Files:**
- Modify: `artmind/vault_sync.py:117-132` (`_diff_name_status`), `:180-187` (`_present_at`)
- Modify: `artmind/vault_doctor.py:84-86` (`check_tracked_ignored`)
- Test: `test/test_vault_sync.py`, `test/test_vault_doctor.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_sync.py`:

```python
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

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "Café notes 2026")]
    assert plan.regenerate_tables == [("banking", "café accounts")]


def test_classify_diff_retracts_a_removed_non_ascii_folder(repo, monkeypatch):
    kg_dir, _ = _cafe_fixture(repo, monkeypatch)
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "Café notes 2026")
    _commit_all(repo, "remove café notes")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

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
```

Append to `test/test_vault_doctor.py`:

```python
def test_a_tracked_table_folder_with_a_non_ascii_name_gets_an_exact_untrack_command(repo):
    """Spec 2026-09-26 §14 A5: without `ls-files -z`, git C-quotes the name and
    the printed command names a path that does not exist."""
    table = repo / ".artmind" / "data" / "kg" / "banking" / "table__Café"
    table.mkdir(parents=True)
    (table / "document.json").write_text("{}")
    _git(repo, "add", "-f", "-A")
    _git(repo, "commit", "-qm", "old commit")

    check = _by_name(doc.run(repo))["ignored paths still tracked"]

    assert check["fix"] == "git rm -r --cached -- '.artmind/data/kg/banking/table__Café'"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -k "non_ascii" test/test_vault_doctor.py::test_a_tracked_table_folder_with_a_non_ascii_name_gets_an_exact_untrack_command -v`
Expected: the three `test_vault_sync` tests FAIL with `ValueError: '"...Caf\\303\\251 notes 2026/...' is not in the subpath of '.artmind/data/kg'`; the doctor test FAILs because `check["fix"]` contains `'"'` and `\\303\\251`.

- [ ] **Step 3: Implement**

In `artmind/vault_sync.py`, replace `_diff_name_status` (lines 117-132):

```python
def _diff_name_status(vault_dir: Path, base: str, head: str, scope: Path) -> list[tuple[str, str]]:
    """`[(status, path), ...]` for `scope` between `base` and `head`, paths
    relative to `vault_dir`. `status` is git's own single-letter code
    (A/M/D); `--no-renames` guarantees a delete+add pair rather than a
    single "R100" line regardless of the user's global git config, which
    the per-path classification in `_classify_kg_diff`/
    `_classify_structured_text_diff` depends on."""
    rel_scope = scope.relative_to(vault_dir)
    out = _git(vault_dir, ["diff", "--no-renames", "--name-status", base, head, "--", str(rel_scope)])
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        rows.append((status[0], path))
    return rows
```

with:

```python
def _diff_name_status(vault_dir: Path, base: str, head: str, scope: Path) -> list[tuple[str, str]]:
    """`[(status, path), ...]` for `scope` between `base` and `head`, paths
    relative to `vault_dir`. `status` is git's own single-letter code
    (A/M/D); `--no-renames` guarantees a delete+add pair rather than a
    single "R100" line regardless of the user's global git config, which
    the per-path classification in `_classify_kg_diff`/
    `_classify_structured_text_diff` depends on.

    `-z` (spec 2026-09-26 §14 A5): the output is `status NUL path NUL ...`
    with every path verbatim. Without it git C-quotes a non-ASCII path
    (`"Caf\\303\\251/..."`), which no caller can match."""
    rel_scope = scope.relative_to(vault_dir)
    out = _git(vault_dir, ["diff", "--no-renames", "--name-status", "-z", base, head, "--", str(rel_scope)])
    fields = out.split("\0")
    return [(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2) if fields[i]]
```

Replace `_present_at` (lines 180-187):

```python
def _present_at(vault_dir: Path, rev: str, relpaths: list[str]) -> list[str]:
    """The subset of file paths `relpaths` that exist at `rev`, in order.
    `git archive` refuses a pathspec that matches nothing, and a table's
    `.meta.json` (or the legacy `manifest.json`) may legitimately be absent."""
    if not relpaths:
        return []
    present = set(_git(vault_dir, ["ls-tree", "-r", "--name-only", rev, "--", *relpaths]).splitlines())
    return [p for p in relpaths if p in present]
```

with:

```python
def _present_at(vault_dir: Path, rev: str, relpaths: list[str]) -> list[str]:
    """The subset of file paths `relpaths` that exist at `rev`, in order.
    `git archive` refuses a pathspec that matches nothing, and a table's
    `.meta.json` (or the legacy `manifest.json`) may legitimately be absent.
    NUL-separated (`-z`, §14 A5), so a non-ASCII name is not quoted away."""
    if not relpaths:
        return []
    present = set(_git(vault_dir, ["ls-tree", "-r", "-z", "--name-only", rev, "--", *relpaths]).split("\0"))
    return [p for p in relpaths if p in present]
```

In `artmind/vault_doctor.py`, `check_tracked_ignored` (lines 84-86), replace:

```python
    rc, out, err = _git(vault_dir, ["ls-files", "-c", "-i", "--exclude-standard", "--", ".artmind"])
    if rc != 0:
        return Check(name, UNKNOWN, f"git ls-files failed: {(err or out).strip()}")
    paths = _collapse([p for p in out.splitlines() if p.strip()])
```

with:

```python
    # -z: paths verbatim, never C-quoted (spec 2026-09-26 §14 A5).
    rc, out, err = _git(vault_dir, ["ls-files", "-z", "-c", "-i", "--exclude-standard", "--", ".artmind"])
    if rc != 0:
        return Check(name, UNKNOWN, f"git ls-files failed: {(err or out).strip()}")
    paths = _collapse([p for p in out.split("\0") if p])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py test/test_vault_doctor.py -q`
Expected: all PASS (the existing `test_diff_name_status_*` tests still pass: the `(status, path)` shape is unchanged).

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py artmind/vault_doctor.py test/test_vault_sync.py test/test_vault_doctor.py
git commit -m "fix(vault-sync): read git path listings NUL-separated" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: preflight blocks only on conflicts under `.artmind/` (spec §14 A7, review finding 3)

Today `preflight` refuses on a conflict in any file, and splits `git diff --name-only` on whitespace, so a conflicted `doc 1/observations.json` is reported as two paths. A7: only unresolved conflicts under `.artmind/` block a sync; a conflicted note of the user's does not change what sync applies. A merge **in progress** (`MERGE_HEAD`) still refuses whatever conflicts — spec A5 is unchanged there — so A7 matters for unmerged index entries without a merge in progress (a `git stash pop` conflict, or a merge whose `MERGE_HEAD` was removed by hand).

**Files:**
- Modify: `artmind/vault_sync.py:25` (import), `:104-109` (unmerged check)
- Test: `test/test_vault_sync.py:685-730`

- [ ] **Step 1: Write the failing tests and retarget the existing one**

In `test/test_vault_sync.py`, replace `_conflicting_merge` (lines 685-695):

```python
def _conflicting_merge(repo):
    """Leave `repo` mid-merge with a.txt conflicted."""
    (repo / "a.txt").write_text("base\n")
    _commit_all(repo, "base")
    subprocess.run(["git", "checkout", "-qb", "other"], cwd=repo, check=True)
    (repo / "a.txt").write_text("theirs\n")
    _commit_all(repo, "theirs")
    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo, check=True)
    (repo / "a.txt").write_text("ours\n")
    _commit_all(repo, "ours")
    subprocess.run(["git", "merge", "other"], cwd=repo, capture_output=True)  # exits 1: conflict
```

with:

```python
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
```

Replace the existing `test_preflight_refuses_with_unmerged_paths_even_without_merge_head` (lines 720-730):

```python
def test_preflight_refuses_with_unmerged_paths_even_without_merge_head(repo, monkeypatch):
    """`git merge --abort` never run and MERGE_HEAD deleted by hand (or a
    stash pop conflict): the index still has unmerged entries."""
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _conflicting_merge(repo)
    git_dir = repo / ".git"
    (git_dir / "MERGE_HEAD").unlink()

    with pytest.raises(vs.VaultSyncError, match="unresolved conflicts"):
        vs.sync(repo, bootstrap_empty=True)
```

with:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -k "preflight" -v`
Expected: `test_preflight_refuses_with_unmerged_paths_even_without_merge_head` FAILs (`Regex pattern did not match`: the message lists `.artmind/data/kg/banking/doc, 1/observations.json`); `test_preflight_ignores_an_unresolved_conflict_outside_artmind` FAILs with `VaultSyncError: unresolved conflicts in 2 file(s) (notes/My, note.md)`. The other preflight tests PASS.

- [ ] **Step 3: Implement**

In `artmind/vault_sync.py`, below `from utils.functions import run_command` (line 25), add:

```python
from artmind.vault import MARKER
```

(`artmind.vault` is deliberately import-light; see its module docstring.)

Replace (lines 104-109):

```python
    unmerged = _git(vault_dir, ["diff", "--name-only", "--diff-filter=U"]).split()
    if unmerged:
        raise VaultSyncError(
            f"unresolved conflicts in {len(unmerged)} file(s) ({', '.join(unmerged[:5])}"
            f"{', ...' if len(unmerged) > 5 else ''}) -- resolve them first"
        )
```

with:

```python
    # Only artmind's own files (spec §14 A7): a conflicted human note does not
    # change what sync applies, and Obsidian shows it to the user. `-z` keeps a
    # name with a space (or a non-ASCII byte) whole (§14 A5).
    unmerged = [
        p for p in _git(vault_dir, ["diff", "--name-only", "-z", "--diff-filter=U", "--", MARKER]).split("\0") if p
    ]
    if unmerged:
        raise VaultSyncError(
            f"unresolved conflicts in {len(unmerged)} file(s) under {MARKER}/ ({', '.join(unmerged[:5])}"
            f"{', ...' if len(unmerged) > 5 else ''}) -- resolve them first"
        )
```

Update the `preflight` docstring's second sentence (lines 88-91) from:

```python
    """Refuse -- before reading or writing anything -- when the vault is in a
    state `sync` must not apply from (spec 2026-09-26 §6 A5): a merge, rebase
    or cherry-pick in progress, unresolved conflicts in the index, or a running
    ingest worker. Each message says what to do next."""
```

to:

```python
    """Refuse -- before reading or writing anything -- when the vault is in a
    state `sync` must not apply from (spec 2026-09-26 §6 A5, §14 A7): a merge,
    rebase or cherry-pick in progress, unresolved conflicts under `.artmind/`,
    or a running ingest worker. Each message says what to do next."""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: all PASS (`test_preflight_refuses_during_a_merge` still refuses on `MERGE_HEAD`).

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "fix(vault-sync): only conflicts under .artmind/ block a sync" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: track A replays a folder when any file in it changes (spec §14 A4, phase 2 follow-up 5)

Track A reacts only to `observations.json`. A timer commit that catches a folder in two halves (`observations.json` first, `chunks.json`/`relationships.json` in the next commit) replays the first half on a local-Neo4j receiver and never the second. The fix groups diff rows by folder: a folder with any change is replayed once if its `observations.json` exists at `head`, retracted if its `observations.json` was deleted in the range, and otherwise left alone (a folder that does not yet have `observations.json` has nothing to replay; the commit that adds it will). `table__*` folders and `.artmind-tmp`/`.artmind-old` scratch folders stay excluded.

**Files:**
- Modify: `artmind/vault_sync.py:225-286` (`_classify_kg_diff`)
- Test: `test/test_vault_sync.py:223-238` (replaced), `:361-381` (updated), new tests

- [ ] **Step 1: Write the failing tests**

In `test/test_vault_sync.py`, replace `test_classify_diff_ignores_a_folder_with_no_observations_json_change` (lines 223-238):

```python
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
```

with:

```python
def test_classify_diff_replays_a_folder_when_only_another_file_changed(repo, monkeypatch):
    """Spec 2026-09-26 §14 A4: any file changing inside a document folder
    replays it, not only observations.json."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "chunks.json").write_text('[{"id": "c1"}]')
    _commit_all(repo, "edit chunks only")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

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

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

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

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]


def test_classify_diff_waits_for_observations_before_replaying(repo, monkeypatch):
    """A folder committed with document.json but no observations.json yet
    has nothing to replay; the commit that adds observations.json will."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    folder = kg_dir / "banking" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text('{"id": "docid-1"}')
    _commit_all(repo, "half a folder")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == []


def test_classify_diff_ignores_tracked_atomic_write_scratch(repo, monkeypatch):
    """`.artmind-tmp`/`.artmind-old` are gitignored (R4); one that got tracked
    anyway is never a document of its own."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1.artmind-tmp", "docid-1")
    _commit_all(repo, "scratch got committed")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == []
```

In `test_classify_diff_raises_if_removed_folder_never_existed_at_base` (lines 361-381), the new classifier also asks git which folders still have `observations.json` at `head`; that test's `head` is the fake string `"head-sha"`. Replace:

```python
    monkeypatch.setattr(vs, "_diff_name_status", lambda *a, **k: [("D", removed_path)])
    monkeypatch.setattr(vs, "_show", lambda *a, **k: None)
```

with:

```python
    monkeypatch.setattr(vs, "_diff_name_status", lambda *a, **k: [("D", removed_path)])
    monkeypatch.setattr(vs, "_present_at", lambda *a, **k: [])
    monkeypatch.setattr(vs, "_show", lambda *a, **k: None)
```

Append a sync-level test at the end of the file:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -k "classify_diff or chunks_changed" -v`
Expected: `..._when_only_another_file_changed`, `..._in_two_halves`, `..._nested_chunk_cache_change` and `test_sync_replays_a_folder_whose_chunks_changed_...` FAIL (`replay_docs == []` / `seen == []`); `..._ignores_tracked_atomic_write_scratch` FAILs (`[('banking', 'doc1.artmind-tmp')]`). `..._waits_for_observations_before_replaying` PASSes already (a guard for the new code).

- [ ] **Step 3: Implement**

In `artmind/vault_sync.py`, replace the whole `_classify_kg_diff` function (lines 225-286, from `def _classify_kg_diff(` through `    return replay, retract`) with:

```python
def _classify_kg_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Track A (spec §4.A): replay/retract for every document folder under
    `.artmind/data/kg/**`.

    Grouped by folder (spec 2026-09-26 §14 A4): a folder in which ANY file
    changed between `base` and `head` is replayed once, if its
    `observations.json` exists at `head` -- a timer commit can catch a folder
    in two halves, and the commit carrying the second half must replay it
    again. A folder whose `observations.json` was removed is retracted, by the
    doc id its `document.json` had at `base`. A folder with changes but no
    `observations.json` at either end (a half-written folder from a
    non-atomic writer) has nothing to replay yet.

    Skipped entirely: `table__<name>` folders -- spec 2026-09-26 §5 R4:
    gitignored, a pure function of a table's CSV/mapping, regenerated by
    track B (without this, the `git rm -r --cached` that `vault doctor`
    recommends would retract that table on every receiving machine) -- and
    `.artmind-tmp`/`.artmind-old` atomic-write scratch.

    Reads committed history only (`git diff`/`ls-tree`/`show` between two
    fixed revisions), never the live filesystem, which track B may already
    have written fresh, uncommitted content into by the time this runs."""
    import json

    import paths
    from artmind.atomic_dir import is_scratch

    kg_dir = paths.KG_DIR
    try:
        rows = _diff_name_status(vault_dir, base, head, kg_dir)
    except ValueError:
        # Only unreachable in production, where KG_DIR/STRUCTURED_TEXT_DIR
        # are always under the same vault root -- some tests intentionally
        # patch only one of the two (test/conftest.py's autouse fixture
        # patches STRUCTURED_TEXT_DIR for every test but has no equivalent
        # for KG_DIR), leaving this one pointed outside `vault_dir`.
        return [], []
    kg_rel = kg_dir.relative_to(vault_dir)

    # (domain, docdir) -> [(status, path inside the folder)], in git's order.
    folders: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for status, path in rows:
        parts = Path(path).relative_to(kg_rel).parts
        if len(parts) < 3:
            continue
        domain, docdir = parts[0], parts[1]
        if docdir.startswith("table__") or is_scratch(Path(docdir)):
            continue
        if not _in_scope(domain, domains):
            continue
        folders.setdefault((domain, docdir), []).append((status, "/".join(parts[2:])))
    if not folders:
        return [], []

    obs_path = {key: str(kg_rel / key[0] / key[1] / "observations.json") for key in folders}
    at_head = set(_present_at(vault_dir, head, list(obs_path.values())))

    replay: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for (domain, docdir), changes in folders.items():
        if obs_path[(domain, docdir)] in at_head:
            replay.append((domain, docdir))
        elif ("D", "observations.json") in changes:
            # The doc's id lives in the sibling `document.json`, not in
            # `observations.json` itself (which is a bare list) -- read that
            # file's content as it stood at `base`, right before removal.
            base_content = _show(vault_dir, base, str(kg_rel / domain / docdir / "document.json"))
            if base_content is None:
                raise VaultSyncError(
                    f"{obs_path[(domain, docdir)]} is marked removed since {base}, but "
                    f"wasn't present at {base} either -- diff and history disagree, refusing to guess"
                )
            retract.append((domain, json.loads(base_content)["id"]))
    return replay, retract
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: all PASS. `test_classify_diff_retracts_a_removed_document_folder` still reports one retraction although four files were deleted (grouping dedupes); `test_classify_diff_ignores_document_json_changing_alongside_observations` still reports one replay.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "fix(vault-sync): replay a document folder when any file in it changes" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: archive restore swaps its KG folder in atomically (spec §14 A4, phase 2 follow-up 3)

`restore_from_archive` does `rmtree` + `copytree` + an in-place rewrite of `document.json` (`artmind/archive.py:319-343`). An Obsidian Git timer commit can land between any two of those. `write_dir_atomic` carries every entry of the old folder over, which is right for ingest (the chunk cache, the sidecar) but wrong here: a restore replaces the folder wholesale, as `rmtree` did. So `write_dir_atomic` gains `carry_over=False`.

**Files:**
- Modify: `artmind/atomic_dir.py:68-103` (`write_dir_atomic`)
- Modify: `artmind/archive.py:319-343`
- Test: `test/test_atomic_dir.py`, `test/test_archive.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_atomic_dir.py`:

```python
def test_carry_over_false_replaces_the_folder_wholesale(tmp_path):
    """A restore or import rebuilds the whole folder from its source; nothing
    from the old folder may survive (spec 2026-09-26 §14 A4)."""
    target = tmp_path / "doc"
    (target / "chunks").mkdir(parents=True)
    (target / "chunks" / "0.json").write_text("stale cache")
    (target / "observations.json").write_text("v1")

    write_dir_atomic(target, {"observations.json": "v2", "chunks/1.json": "new"}, carry_over=False)

    files = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert files == ["chunks/1.json", "observations.json"]
    assert (target / "observations.json").read_text() == "v2"
    assert _siblings(target) == ["doc"]
```

Append to `test/test_archive.py`:

```python
def test_restore_from_archive_swaps_the_kg_folder_in_atomically(env, monkeypatch):
    """Spec 2026-09-26 §14 A4: the restored folder -- with document.json
    already re-pointed at the new id -- lands in ONE swap that replaces
    whatever stale folder was there; a timer commit never sees a half-copied
    folder or the bundle's old identity."""
    from pathlib import Path

    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    doc_kg = bundle_dir / "kg" / "general" / "policy"
    (doc_kg / "chunks" / "sha").mkdir(parents=True)
    (doc_kg / "document.json").write_text(json.dumps({"id": "doc-1", "name": "policy.md"}), encoding="utf-8")
    (doc_kg / "observations.json").write_text("[]", encoding="utf-8")
    (doc_kg / "chunks" / "sha" / "0.json").write_text("cached", encoding="utf-8")
    stale = kg_dir / "general" / "policy-restored"
    stale.mkdir(parents=True)
    (stale / "stale.json").write_text("from an earlier life", encoding="utf-8")

    import artmind.atomic_dir as atomic_dir

    swaps = []
    real_swap = atomic_dir.write_dir_atomic

    def spy(target, files, **kw):
        swaps.append((Path(target), json.loads(files["document.json"]), kw))
        return real_swap(target, files, **kw)

    monkeypatch.setattr(atomic_dir, "write_dir_atomic", spy)
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})
    monkeypatch.setattr("artmind.ingest.commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, domain=None: {})

    archive.restore_from_archive("doc-1", to_path="notes/policy-restored.md", new_id="doc-2")

    assert [(t, d["id"], kw) for t, d, kw in swaps] == [(stale, "doc-2", {"carry_over": False})]
    files = sorted(p.relative_to(stale).as_posix() for p in stale.rglob("*") if p.is_file())
    assert files == ["chunks/sha/0.json", "document.json", "observations.json"]
    assert json.loads((stale / "document.json").read_text(encoding="utf-8"))["id"] == "doc-2"
    assert sorted(p.name for p in (kg_dir / "general").iterdir()) == ["policy-restored"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_atomic_dir.py::test_carry_over_false_replaces_the_folder_wholesale test/test_archive.py::test_restore_from_archive_swaps_the_kg_folder_in_atomically -v`
Expected: the atomic_dir test FAILs with `TypeError: write_dir_atomic() got an unexpected keyword argument 'carry_over'`; the archive test FAILs on `swaps == [...]` (`swaps` is `[]`).

- [ ] **Step 3: Implement**

In `artmind/atomic_dir.py`, replace the signature, docstring and the copy step of `write_dir_atomic` (lines 68-86):

```python
def write_dir_atomic(target: Path, files: dict[str, str | bytes]) -> None:
    """Replace `files` (name -> content) inside `target` as one swap.

    Every other entry already in `target` -- the per-chunk extraction cache,
    the gitignored embedding sidecar, debug output -- is carried over
    unchanged, as hardlinks. A replaced file is unlinked in `.artmind-tmp` before
    it is written, so the write never reaches the old folder's inode."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    recover(target)
    for name in files:
        parts = Path(name).parts
        if Path(name).is_absolute() or ".." in parts or not parts:
            raise ValueError(f"{name!r}: a staging file name must be a relative path inside the folder")
    tmp, old = _siblings(target)
    if target.exists():
        shutil.copytree(target, tmp, symlinks=True, copy_function=_link_or_copy)
    else:
        tmp.mkdir()
```

with:

```python
def write_dir_atomic(target: Path, files: dict[str, str | bytes], *, carry_over: bool = True) -> None:
    """Replace `files` (name -> content) inside `target` as one swap.

    With `carry_over=True` (the default, what ingest wants) every other entry
    already in `target` -- the per-chunk extraction cache, the gitignored
    embedding sidecar, debug output -- is carried over unchanged, as
    hardlinks. A replaced file is unlinked in `.artmind-tmp` before it is
    written, so the write never reaches the old folder's inode.

    `carry_over=False` replaces the folder wholesale: `files` is its entire
    new content. Archive restore wants this -- the bundle is the whole truth
    about the folder (spec 2026-09-26 §14 A4)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    recover(target)
    for name in files:
        parts = Path(name).parts
        if Path(name).is_absolute() or ".." in parts or not parts:
            raise ValueError(f"{name!r}: a staging file name must be a relative path inside the folder")
    tmp, old = _siblings(target)
    if carry_over and target.exists():
        shutil.copytree(target, tmp, symlinks=True, copy_function=_link_or_copy)
    else:
        tmp.mkdir()
```

(The rest of the function — the write loop and the three-step swap — is unchanged.)

In `artmind/archive.py`, replace (lines 322-343):

```python
    if bundle_kg_dir.exists():
        if doc_kg_dir.exists():
            shutil.rmtree(doc_kg_dir)
        shutil.copytree(bundle_kg_dir, doc_kg_dir)
        restored_kg = True
        if new_id or to_path:
            # A fork/rename restore -- the staged JSON's own identity fields
            # still point at the archived original; re-point them so the
            # commit below writes under `restore_id`/`target_path`, not a
            # collision with the id/path this bundle was archived from.
            doc_json_path = doc_kg_dir / "document.json"
            if doc_json_path.exists():
                doc_json = json.loads(doc_json_path.read_text(encoding="utf-8"))
                doc_json["id"] = restore_id
                if "artmind_id" in doc_json:
                    doc_json["artmind_id"] = restore_id
                doc_json["path"] = str(target_path)
                doc_json["name"] = target_path.name
                doc_json_path.write_text(
                    json.dumps(doc_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )
```

with:

```python
    if bundle_kg_dir.exists():
        from artmind.atomic_dir import write_dir_atomic

        # The whole folder is rebuilt from the bundle and swapped in at once
        # (spec 2026-09-26 §5 R2, §14 A4): an Obsidian Git timer commit never
        # captures a half-copied folder, nor the bundle's document.json before
        # its identity is re-pointed below. Wholesale (`carry_over=False`), as
        # the rmtree + copytree this replaces was.
        files: dict[str, bytes] = {
            p.relative_to(bundle_kg_dir).as_posix(): p.read_bytes()
            for p in sorted(bundle_kg_dir.rglob("*"))
            if p.is_file()
        }
        if (new_id or to_path) and "document.json" in files:
            # A fork/rename restore -- the staged JSON's own identity fields
            # still point at the archived original; re-point them so the
            # commit below writes under `restore_id`/`target_path`, not a
            # collision with the id/path this bundle was archived from.
            doc_json = json.loads(files["document.json"].decode("utf-8"))
            doc_json["id"] = restore_id
            if "artmind_id" in doc_json:
                doc_json["artmind_id"] = restore_id
            doc_json["path"] = str(target_path)
            doc_json["name"] = target_path.name
            files["document.json"] = json.dumps(doc_json, ensure_ascii=False, indent=2).encode("utf-8")
        write_dir_atomic(doc_kg_dir, files, carry_over=False)
        restored_kg = True
```

(`shutil` stays imported: `restore_from_archive` still uses `shutil.copy2` for the note and the original binary.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_atomic_dir.py test/test_archive.py test/test_ingest_staging_atomic.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/atomic_dir.py artmind/archive.py test/test_atomic_dir.py test/test_archive.py
git commit -m "fix(archive): restore swaps the KG staging folder in atomically" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: dashboard artifact import swaps atomically (spec §14 A4, phase 2 follow-up 3)

`POST /api/artifacts/import` extracts a zip straight into the staging folder (`artmind/webui/dashboard_routes.py:390-401`). The zip becomes one `write_dir_atomic` call instead. The default `carry_over=True` keeps today's overlay semantics: `extractall` left files the zip does not carry (the chunk cache, the embedding sidecar) in place, and so does this. `write_dir_atomic` rejects `..` names itself, so a name that resolves inside the folder but contains `..` becomes a 400, not a 500.

**Files:**
- Modify: `artmind/webui/dashboard_routes.py:22` (import), `:390-401`
- Test: `test/test_webui_admin_api.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_webui_admin_api.py`:

```python
def test_artifact_import_swaps_the_folder_in_atomically_keeping_the_sidecar(monkeypatch, tmp_path):
    """Spec 2026-09-26 §14 A4: one swap, so Obsidian Git never commits a
    half-extracted folder. Files the zip does not carry are kept, as the old
    extractall kept them."""
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    monkeypatch.setattr(dashboard_routes, "commit_to_graph", lambda doc_dir, domain: True)
    dest = tmp_path / "general" / "doc1"
    dest.mkdir(parents=True)
    (dest / "observations.json").write_text('["old"]')
    (dest / "embeddings.json").write_text("{}")

    swaps = []
    real_swap = dashboard_routes.write_dir_atomic
    monkeypatch.setattr(
        dashboard_routes, "write_dir_atomic",
        lambda target, files, **kw: swaps.append((target, sorted(files))) or real_swap(target, files, **kw),
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("document.json", json.dumps({"name": "doc1.pdf"}))
        zf.writestr("observations.json", '["new"]')
        zf.writestr("chunks/sha/0.json", "{}")
    buf.seek(0)

    response = _client().post(
        "/api/artifacts/import",
        data={"domain": "general", "doc": "doc1"},
        files={"file": ("bundle.zip", buf, "application/zip")},
    )

    assert response.status_code == 200, response.text
    assert swaps == [(dest, ["chunks/sha/0.json", "document.json", "observations.json"])]
    assert (dest / "observations.json").read_text() == '["new"]'
    assert (dest / "embeddings.json").read_text() == "{}"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["doc1"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_webui_admin_api.py::test_artifact_import_swaps_the_folder_in_atomically_keeping_the_sidecar -v`
Expected: FAIL with `AttributeError: <module 'artmind.webui.dashboard_routes' ...> has no attribute 'write_dir_atomic'`.

- [ ] **Step 3: Implement**

In `artmind/webui/dashboard_routes.py`, replace line 22:

```python
from artmind.atomic_dir import is_scratch
```

with:

```python
from artmind.atomic_dir import is_scratch, write_dir_atomic
```

Replace (lines 390-403):

```python
        dest_dir = KG_DIR / domain / doc
        dest_dir.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                dest_resolved = dest_dir.resolve()
                for member in zf.infolist():
                    member_path = (dest_dir / member.filename).resolve()
                    if dest_resolved != member_path and dest_resolved not in member_path.parents:
                        raise HTTPException(status_code=400, detail=f"Unsafe zip entry: {member.filename}")
                zf.extractall(dest_dir)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")
```

with:

```python
        dest_dir = KG_DIR / domain / doc
        content = await file.read()
        files: dict[str, bytes] = {}
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                dest_resolved = dest_dir.resolve()
                for member in zf.infolist():
                    member_path = (dest_dir / member.filename).resolve()
                    if dest_resolved != member_path and dest_resolved not in member_path.parents:
                        raise HTTPException(status_code=400, detail=f"Unsafe zip entry: {member.filename}")
                    if not member.is_dir():
                        files[member.filename] = zf.read(member)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")
        # One swap (spec 2026-09-26 §5 R2, §14 A4), so an Obsidian Git timer
        # commit never captures a half-extracted folder. Files the bundle does
        # not carry (the chunk cache, the embedding sidecar) are kept, exactly
        # as the extractall this replaces kept them.
        try:
            write_dir_atomic(dest_dir, files)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_webui_admin_api.py -q -k "artifact"`
Expected: all PASS, including the existing `test_artifact_import_rejects_zip_slip`, `..._bad_zip_is_400` and `..._write_failure_is_400`.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/dashboard_routes.py test/test_webui_admin_api.py
git commit -m "fix(dashboard): artifact import swaps the staging folder in atomically" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `kg_pull` swaps each pulled folder in atomically (spec §14 A4, phase 2 follow-up 3)

`pull_kg` copies each document folder with `shutil.copytree` (`artmind/kg_pull.py:148-152`), so a timer commit can capture a half-copied folder. Each folder becomes one `write_dir_atomic` call. The pulled repo is external and untrusted: `copytree` follows symlinks and would copy whatever file a symlink points at on this machine into the vault, which Obsidian Git then pushes. Symlinks are skipped instead (a behaviour change, flagged in the self-review).

**Files:**
- Modify: `artmind/kg_pull.py:1-11` (import), `:147-152`
- Test: `test/test_kg_pull.py` (class `TestPullKg`)

- [ ] **Step 1: Write the failing test**

Add this method to `class TestPullKg` in `test/test_kg_pull.py` (after `test_error_when_no_documents_found`):

```python
    @patch("artmind.kg_pull._sparse_clone")
    def test_each_document_is_swapped_in_atomically_and_symlinks_are_not_followed(
        self, mock_clone, tmp_path, monkeypatch
    ):
        import artmind.kg_pull as mod
        kg_dir = tmp_path / "kg"
        monkeypatch.setattr(mod, "KG_DIR", kg_dir)
        fake_content = self._make_fake_repo(tmp_path, ["doc_a"])
        (fake_content / "doc_a" / "chunks" / "sha").mkdir(parents=True)
        (fake_content / "doc_a" / "chunks" / "sha" / "0.json").write_text("cached")
        secret = tmp_path / "secret.txt"
        secret.write_text("private")
        (fake_content / "doc_a" / "leak.json").symlink_to(secret)
        cleanup_dir = tmp_path / "cleanup"
        cleanup_dir.mkdir()
        mock_clone.return_value = (fake_content, cleanup_dir)

        swaps = []
        real_swap = mod.write_dir_atomic
        monkeypatch.setattr(
            mod, "write_dir_atomic",
            lambda target, files, **kw: swaps.append((target, sorted(files))) or real_swap(target, files, **kw),
        )

        pull_kg("https://github.com/acme/repo.git", "data/kg/sales", "sales")

        assert swaps == [(kg_dir / "sales" / "doc_a", ["chunks/sha/0.json", "document.json", "entities.json"])]
        assert not (kg_dir / "sales" / "doc_a" / "leak.json").exists()
        assert sorted(p.name for p in (kg_dir / "sales").iterdir()) == ["doc_a"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_kg_pull.py -k "atomically" -v`
Expected: FAIL with `AttributeError: <module 'artmind.kg_pull' ...> has no attribute 'write_dir_atomic'`.

- [ ] **Step 3: Implement**

In `artmind/kg_pull.py`, replace:

```python
from loguru import logger

from paths import KG_DIR
```

with:

```python
from loguru import logger

from artmind.atomic_dir import write_dir_atomic
from paths import KG_DIR
```

Replace (lines 147-152):

```python
        # Copy
        target_dir.mkdir(parents=True, exist_ok=True)
        for doc_dir in doc_dirs:
            dest = target_dir / doc_dir.name
            shutil.copytree(doc_dir, dest)
            logger.info("  Copied {}", doc_dir.name)
```

with:

```python
        # Each folder lands in one swap (spec 2026-09-26 §5 R2, §14 A4), so an
        # Obsidian Git timer commit never captures a half-copied document.
        # Symlinks are skipped, not followed: the repo is external, and
        # following one would copy whatever file it points at on this machine
        # into the vault, which Obsidian Git then pushes.
        for doc_dir in doc_dirs:
            files = {
                p.relative_to(doc_dir).as_posix(): p.read_bytes()
                for p in sorted(doc_dir.rglob("*"))
                if p.is_file() and not p.is_symlink()
            }
            write_dir_atomic(target_dir / doc_dir.name, files)
            logger.info("  Copied {}", doc_dir.name)
```

(`shutil` stays imported: the `finally` block still `rmtree`s the clone.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_kg_pull.py -q`
Expected: all PASS (`test_aborts_on_conflict` still copies nothing: the conflict check runs before the loop).

- [ ] **Step 5: Commit**

```bash
git add artmind/kg_pull.py test/test_kg_pull.py
git commit -m "fix(kg-pull): swap pulled folders in atomically, never follow symlinks" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: table ids are local; import remaps them by `(domain, table_name)` (spec §14 A3, phase 2 follow-up 1)

Each `.meta.json` carries SQLite's autoincrement `tables.id`. Two machines that each register a new table can both hand out id 7; after a clean merge, `load_structured_dump` returns two tables with id 7 whose `columns`/`column_mappings`/`column_roles` rows are then indistinguishable, `restore_all` fails with `UNIQUE constraint failed: tables.id`, and `restore_tables` collides with whichever local table already owns the id.

The fix has two halves:
1. **`load_structured_dump`** gives every table in the dump a unique id. Meta files are read in sorted path order; the first table keeps its recorded id, a later duplicate gets `max(all ids) + 1, + 2, ...`. Each file's child rows are rewritten to its table's final id (a meta file's children always belong to its own table). Deterministic, and it leaves ids untouched whenever nothing collides.
2. **`restore_tables`** (scoped, `vault sync`'s path) keeps a table's **local** id when this machine already has `(domain, table_name)`; otherwise it uses the dump's id when that is free here, and a fresh autoincrement id when it is not. Child rows follow their table.

Nothing outside the registry persists a table id (the Neo4j catalogue and text2sql key by id only within one read of the registry), so a remapped id is safe.

**Files:**
- Modify: `artmind/structured/text_export.py:130-174` (`load_structured_dump`)
- Modify: `artmind/structured/registry.py:626-721` (`restore_tables`)
- Test: `test/test_structured_text_export.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_structured_text_export.py`:

```python
# ── table ids are local (spec 2026-09-26 §14 A3) ──────────────────────────────


def _give_meta_id(table: str, new_id: int) -> None:
    """Rewrite `table`'s .meta.json as if another machine had registered it
    under `new_id` -- the table row and every child row."""
    import json

    import paths

    path = paths.STRUCTURED_TEXT_DIR / "banking" / f"{table}.meta.json"
    meta = json.loads(path.read_text())
    meta["table"]["id"] = new_id
    for key in ("columns", "column_mappings", "column_roles"):
        for row in meta[key]:
            row["table_id"] = new_id
    path.write_text(json.dumps(meta))


def _column_names(table_id):
    from artmind.structured import registry

    return {c["name"] for c in registry.get_columns(table_id)}


def test_import_remaps_a_table_id_two_meta_files_share(tmp_path, monkeypatch):
    """Two machines each registered a new table and both got the same SQLite
    id; after a clean merge the two .meta.json files carry it twice."""
    _two_tables(tmp_path, monkeypatch)
    from artmind.structured import registry
    from artmind.structured.text_export import export_structured_text, import_structured_text

    export_structured_text()
    _give_meta_id("customers", _meta("products")["table"]["id"])
    _wipe_stores()

    summary = import_structured_text()

    assert summary["tables_loaded"] == 2
    products = registry.get_table("products", domain="banking")
    customers = registry.get_table("customers", domain="banking")
    assert products["id"] != customers["id"]
    assert _column_names(products["id"]) == {"id", "name"}
    assert _column_names(customers["id"]) == {"id", "city"}


def test_scoped_import_gives_a_new_table_a_fresh_id_when_its_meta_id_is_taken(tmp_path, monkeypatch):
    _two_tables(tmp_path, monkeypatch)
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import import_structured_text

    branches_csv = tmp_path / "branches.csv"
    _write_csv(branches_csv, [["id", "region"], [1, "West"]])
    ingest_structured_file(branches_csv, "banking")  # also exports branches.meta.json
    products_id = registry.get_table("products", domain="banking")["id"]
    # Another machine registered "branches" under the id products has here,
    # and this machine has never seen "branches".
    _give_meta_id("branches", products_id)
    registry.delete_table(registry.get_table("branches", domain="banking")["id"])

    import_structured_text(tables=[("banking", "branches")])

    branches = registry.get_table("branches", domain="banking")
    assert branches["id"] != products_id
    assert _column_names(branches["id"]) == {"id", "region"}
    assert registry.get_table("products", domain="banking")["id"] == products_id
    assert _column_names(products_id) == {"id", "name"}


def test_scoped_import_keeps_the_local_id_of_an_already_registered_table(tmp_path, monkeypatch):
    _two_tables(tmp_path, monkeypatch)
    from artmind.structured import registry
    from artmind.structured.text_export import import_structured_text

    products_id = registry.get_table("products", domain="banking")["id"]
    customers_id = registry.get_table("customers", domain="banking")["id"]
    _give_meta_id("products", customers_id)  # recorded under the id customers has here

    import_structured_text(tables=[("banking", "products")])

    assert registry.get_table("products", domain="banking")["id"] == products_id
    assert _column_names(products_id) == {"id", "name"}
    assert registry.get_table("customers", domain="banking")["id"] == customers_id
    assert _column_names(customers_id) == {"id", "city"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_structured_text_export.py -k "id" -v`
Expected: the three new tests FAIL with `sqlite3.IntegrityError: UNIQUE constraint failed: tables.id`.

- [ ] **Step 3: Implement `load_structured_dump`**

In `artmind/structured/text_export.py`, replace `load_structured_dump` (lines 130-174) with:

```python
def load_structured_dump(src_dir: Path) -> dict:
    """A `registry.dump_all()`-shaped dict assembled from `src_dir`'s
    per-table `<domain>/<table>.meta.json` files. A legacy `manifest.json`
    fills in any table that has no meta file (R5 read-only compatibility).
    Every table row gets a `parquet_path` key (empty when the meta omitted
    it); `import_structured_text` overwrites it for this machine.

    Table ids are local to the machine that registered the table (spec
    2026-09-26 §14 A3): two machines can each give a different table the same
    SQLite id, and both `.meta.json` files then merge cleanly. Every table in
    the returned dump has a unique id -- the first (in sorted path order)
    keeps its recorded id, a later duplicate gets the next id above every
    recorded one -- and each file's child rows follow their own table's id."""
    legacy_path = src_dir / MANIFEST_NAME
    meta_paths = sorted(src_dir.glob(f"*/*{META_SUFFIX}"))
    if not meta_paths and not legacy_path.is_file():
        raise FileNotFoundError(
            f"No structured text export found at {src_dir} "
            f"(no <domain>/<table>{META_SUFFIX} and no legacy {MANIFEST_NAME})"
        )

    dump: dict = {"datasources": [], "tables": [], **{key: [] for key in _DUMP_KEYS}}

    def _add_datasource(ds: dict | None) -> None:
        if ds and all(d["name"] != ds["name"] for d in dump["datasources"]):
            dump["datasources"].append(ds)

    # (table row, {child key: that table's child rows}), in a stable order.
    sources: list[tuple[dict, dict[str, list[dict]]]] = []
    have: set[tuple[str, str]] = set()
    for path in meta_paths:
        meta = json.loads(path.read_text(encoding="utf-8"))
        table = dict(meta["table"])
        have.add((table["domain"], table["table_name"]))
        sources.append((table, {key: meta.get(key, []) for key in _DUMP_KEYS}))
        _add_datasource(meta.get("datasource"))

    if legacy_path.is_file():
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        for row in legacy.get("tables", []):
            if (row["domain"], row["table_name"]) not in have:
                children = {key: [r for r in legacy.get(key, []) if r["table_id"] == row["id"]] for key in _DUMP_KEYS}
                sources.append((dict(row), children))
        for ds in legacy.get("datasources", []):
            _add_datasource(ds)

    next_id = max((int(table["id"]) for table, _ in sources), default=0) + 1
    used: set[int] = set()
    for table, children in sources:
        table_id = int(table["id"])
        if table_id in used:
            logger.warning(
                "structured text: {}/{} records table id {}, already used by another table's "
                "metadata; importing it as {} (table ids are local, spec 2026-09-26 §14 A3)",
                table["domain"], table["table_name"], table_id, next_id,
            )
            table_id, next_id = next_id, next_id + 1
        used.add(table_id)
        table["id"] = table_id
        dump["tables"].append(table)
        for key in _DUMP_KEYS:
            dump[key].extend({**r, "table_id": table_id} for r in children[key])

    for row in dump["tables"]:
        row.setdefault("parquet_path", "")
    return dump
```

- [ ] **Step 4: Implement `restore_tables`**

In `artmind/structured/registry.py`, replace `restore_tables` (lines 626-721, the whole function) with:

```python
def restore_tables(dump: dict, table_keys: list[tuple[str, str]]) -> None:
    """Scoped sibling of `restore_all`: wipe and reinsert rows for exactly the
    `(domain, table_name)` pairs in `table_keys`, leaving every other
    registered table's rows and ids untouched. `datasources` is left alone
    unless a restored table's own datasource row doesn't currently exist.

    Raises `ValueError` if a requested pair isn't in `dump["tables"]`. Since
    spec 2026-09-26 §5 R5, `dump` is assembled from one `.meta.json` per
    table (falling back to a legacy full `manifest.json` when no per-table
    meta exists) rather than always being a single full-registry dump, so a
    requested table missing from it means the diff and that metadata have
    drifted, which should fail loudly rather than silently skip the table.

    Table ids are local to each machine (spec 2026-09-26 §14 A3): the id a
    `.meta.json` records is informational. A table already registered here
    keeps its local id; a new one takes the recorded id when it is free here,
    and a fresh autoincrement id when another table already holds it. Child
    rows (`columns`, `column_mappings`, `column_roles`) are matched to their
    table by the dump's id and written under the table's final id.
    """
    dump_rows_by_key = {(r["domain"], r["table_name"]): r for r in dump.get("tables", [])}
    missing = set(table_keys) - dump_rows_by_key.keys()
    if missing:
        raise ValueError(f"table(s) not found in the manifest: {sorted(missing)}")

    conn = _get_db()
    cursor = conn.cursor()
    try:
        for domain, table_name in table_keys:
            row = dump_rows_by_key[(domain, table_name)]
            dump_id = row["id"]
            existing = cursor.execute(
                'SELECT id FROM "tables" WHERE domain = ? AND table_name = ?',
                (domain, table_name),
            ).fetchone()
            if existing:
                table_id = existing[0]
                cursor.execute("DELETE FROM column_roles WHERE table_id = ?", (table_id,))
                cursor.execute("DELETE FROM column_mappings WHERE table_id = ?", (table_id,))
                cursor.execute('DELETE FROM "columns" WHERE table_id = ?', (table_id,))
                cursor.execute('DELETE FROM "tables" WHERE id = ?', (table_id,))
            else:
                taken = cursor.execute('SELECT 1 FROM "tables" WHERE id = ?', (dump_id,)).fetchone()
                table_id = None if taken else dump_id  # None: SQLite assigns a fresh id

            cursor.execute(
                'INSERT INTO "tables" (id, datasource, table_name, domain, source_file, sheet,'
                " parquet_path, version, row_count, refresh_mode, business_key,"
                " effective_date_column, grain, grain_confirmed, grain_status,"
                " bridge_status, mapping_status, ingested_at, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    table_id, row["datasource"], row["table_name"], row["domain"],
                    row["source_file"], row["sheet"], row["parquet_path"], row["version"],
                    row["row_count"], row["refresh_mode"], row["business_key"],
                    row["effective_date_column"], row.get("grain") or "instance",
                    int(row.get("grain_confirmed") or 0),
                    row.get("grain_status") or "pending",
                    row.get("bridge_status") or "pending",
                    row.get("mapping_status") or "pending",
                    row["ingested_at"], row["sha256"],
                ),
            )
            if table_id is None:
                table_id = cursor.lastrowid

            datasource_exists = cursor.execute(
                "SELECT 1 FROM datasources WHERE name = ?", (row["datasource"],)
            ).fetchone()
            if not datasource_exists:
                ds_row = next(
                    (d for d in dump.get("datasources", []) if d["name"] == row["datasource"]), None
                )
                if ds_row:
                    cursor.execute(
                        "INSERT INTO datasources (name, type, path_or_dsn, created_at) VALUES (?, ?, ?, ?)",
                        (ds_row["name"], ds_row["type"], ds_row["path_or_dsn"], ds_row["created_at"]),
                    )

            for col in dump.get("columns", []):
                if col["table_id"] == dump_id:
                    cursor.execute(
                        'INSERT INTO "columns" (table_id, name, dtype, profile_json) VALUES (?, ?, ?, ?)',
                        (table_id, col["name"], col["dtype"], col["profile_json"]),
                    )
            for m in dump.get("column_mappings", []):
                if m["table_id"] == dump_id:
                    cursor.execute(
                        'INSERT INTO column_mappings (table_id, "column", entity_class, confirmed,'
                        " confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (table_id, m["column"], m["entity_class"], m["confirmed"], m["confidence"], m["updated_at"]),
                    )
            for r in dump.get("column_roles", []):
                if r["table_id"] == dump_id:
                    cursor.execute(
                        'INSERT INTO column_roles (table_id, "column", bridge_role, confirmed,'
                        " confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (table_id, r["column"], r["bridge_role"], r["confirmed"], r["confidence"], r["updated_at"]),
                    )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_text_export.py test/test_structured_registry.py test/test_structured_snapshot.py test/test_db_cli.py -q`
Expected: all PASS (`test_structured_registry.py` exercises `restore_all`/`restore_tables` directly; the snapshot and `db` CLI tests exercise `db restore-text`).

- [ ] **Step 6: Commit**

```bash
git add artmind/structured/text_export.py artmind/structured/registry.py test/test_structured_text_export.py
git commit -m "fix(structured): table ids are local; import remaps them by domain and name" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: a table with no mapping is restored to the structured store only (spec §14 A2, phase 2 follow-up 2)

Track B raises `no table mapping under domains/table_mappings/` for any regenerated table no mapping names (`artmind/vault_sync.py:437-439`). The first export after upgrading migrates every table to `.meta.json` in one commit, so every vault with an unmapped table stalls its cursor forever. A2: restore it into DuckDB (already done by `import_structured_text` above the loop), skip `table2graph`, report it.

**Files:**
- Modify: `artmind/vault_sync.py:414`, `:437-439`, `:490-498`
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_vault_sync.py`:

```python
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
    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_vault_sync.py::test_sync_restores_an_unmapped_table_to_the_structured_store_only -v`
Expected: FAIL with `VaultSyncError: banking/accounts: no table mapping under domains/table_mappings/`.

- [ ] **Step 3: Implement**

In `artmind/vault_sync.py` `sync()`, replace (line 414):

```python
    all_keys: set[tuple[str, str, str]] = set()
```

with:

```python
    all_keys: set[tuple[str, str, str]] = set()
    structured_only: list[str] = []  # tables restored to DuckDB, not projected (§14 A2)
```

Replace (lines 437-439):

```python
                found = find_mappings(table_name, domain)
                if not found:
                    raise VaultSyncError(f"{domain}/{table_name}: no table mapping under domains/table_mappings/")
```

with:

```python
                found = find_mappings(table_name, domain)
                if not found:
                    # Spec 2026-09-26 §14 A2: a table no mapping names is a
                    # structured-store table only -- restored above, never
                    # projected. Raising here stalled the cursor forever.
                    structured_only.append(f"{domain}/{table_name}")
                    continue
```

In the returned dict at the end of `sync()` (lines 490-498), replace:

```python
        "regenerated_tables": len(plan.regenerate_tables),
        "projection": projection_summary,
```

with:

```python
        "regenerated_tables": len(plan.regenerate_tables),
        "structured_only_tables": structured_only,
        "projection": projection_summary,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "fix(vault-sync): restore an unmapped table to the structured store only" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: track B projects with the mapping and schema committed at `head` (review finding 1, spec §14 A1 last sentence)

Track B calls `find_mappings(table_name, domain)` and `load_schema(domain)`, which read `paths.TABLE_MAPPINGS_DIR` and `DOMAIN_SCHEMAS_DIR` from the working tree. Inside a vault those are `.artmind/domains/…`, committed — so an uncommitted or half-merged mapping edit gets applied under a `head` that does not contain it. Both directories are materialised from `head` into the run's scratch dir, and the two functions are pointed at the copies:

- `find_mappings` already takes `mappings_dir` (`artmind/table2graph.py:515`). No change.
- `temporal.load_schema` gains `schemas_dir: Path | None = None`; `None` keeps every existing caller on `DOMAIN_SCHEMAS_DIR`.

A mapping or schema directory outside the vault (only when `ARTMIND_HOME` is set explicitly to a run folder elsewhere) is not versioned with the vault, so it is used as is — there is no committed copy to prefer. That is also what the hermetic suite hits by default (see "Test isolation" above).

**Files:**
- Modify: `artmind/temporal.py:161-182` (`load_schema`)
- Modify: `artmind/vault_sync.py` (new `_files_at`, `_dir_at` after `_present_at`; `sync()` track B)
- Modify: `test/test_vault_sync.py` (existing `find_mappings`/`load_schema` fakes accept the new keyword)
- Test: `test/test_temporal_load_schema.py` (create), `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_temporal_load_schema.py`:

```python
"""temporal.load_schema(domain, schemas_dir=...): `vault sync` reads schemas
committed at HEAD from a scratch copy, not DOMAIN_SCHEMAS_DIR (phase 1
review finding 1, spec 2026-09-26 §14 A1)."""
from artmind.temporal import load_schema


def test_load_schema_reads_from_the_given_directory(tmp_path):
    (tmp_path / "banking_schema.yaml").write_text("entity_types:\n  ACCOUNT: {kind: recurrent}\n")

    assert load_schema("banking", schemas_dir=tmp_path) == {"entity_types": {"ACCOUNT": {"kind": "recurrent"}}}


def test_load_schema_merges_the_parent_temporal_block_from_the_same_directory(tmp_path):
    (tmp_path / "banking_schema.yaml").write_text("temporal:\n  defaults: {supersede_on_title_family: true}\n")
    (tmp_path / "banking.policy_schema.yaml").write_text("entity_types: {}\n")

    schema = load_schema("banking.policy", schemas_dir=tmp_path)

    assert schema["temporal"]["defaults"] == {"supersede_on_title_family": True}


def test_load_schema_with_a_missing_file_in_the_given_directory_is_empty(tmp_path):
    assert load_schema("banking", schemas_dir=tmp_path) == {}
```

In `test/test_vault_sync.py`, the existing fakes must accept the keywords `sync()` will now pass. Use replace-all edits:

- every `lambda table_name, domain: [object()]` → `lambda table_name, domain, mappings_dir=None: [object()]` (5 occurrences, lines ≈621, 665, 862, 903, 1027);
- every `lambda domain: {"name": domain}` → `lambda domain, schemas_dir=None: {"name": domain}` (5 occurrences, same tests).

Then append:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_temporal_load_schema.py test/test_vault_sync.py::test_sync_projects_a_table_with_the_committed_mapping_and_schema -v`
Expected: the three `load_schema` tests FAIL with `TypeError: load_schema() got an unexpected keyword argument 'schemas_dir'`; the sync test FAILs with `seen == []` — the working-tree mapping matches nothing, so after Task 8 the table is silently structured-only.

- [ ] **Step 3: Implement `load_schema(schemas_dir=)`**

In `artmind/temporal.py`, replace (lines 161-182):

```python
def load_schema(domain: str) -> dict:
    """Load a domain's schema, deep-merging inherited `temporal:` from a dotted parent.

    For a leaf domain like `banking.policy`, also loads `banking_schema.yaml` and
    merges its `temporal:` block underneath the child's (see `_deep_merge_temporal`).
    Non-temporal keys (prompts, entity_types, etc.) are NOT inherited here — that
    composition happens at build time via `artmind domains harmonize`.
    """
    path = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    import yaml
    if not path.exists():
        return {}
    schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "." in domain:
        parent_name = domain.rsplit(".", 1)[0]
        parent_path = DOMAIN_SCHEMAS_DIR / f"{parent_name}_schema.yaml"
```

with:

```python
def load_schema(domain: str, schemas_dir: Path | None = None) -> dict:
    """Load a domain's schema, deep-merging inherited `temporal:` from a dotted parent.

    For a leaf domain like `banking.policy`, also loads `banking_schema.yaml` and
    merges its `temporal:` block underneath the child's (see `_deep_merge_temporal`).
    Non-temporal keys (prompts, entity_types, etc.) are NOT inherited here — that
    composition happens at build time via `artmind domains harmonize`.

    `schemas_dir` defaults to `DOMAIN_SCHEMAS_DIR`. `vault sync` passes a scratch
    copy of the schemas committed at HEAD, never the working tree (spec
    2026-09-26 §14 A1).
    """
    schemas_dir = Path(schemas_dir) if schemas_dir is not None else DOMAIN_SCHEMAS_DIR
    path = schemas_dir / f"{domain}_schema.yaml"
    import yaml
    if not path.exists():
        return {}
    schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "." in domain:
        parent_name = domain.rsplit(".", 1)[0]
        parent_path = schemas_dir / f"{parent_name}_schema.yaml"
```

(The remaining lines of the function are unchanged.)

- [ ] **Step 4: Implement the head copies in `vault_sync`**

In `artmind/vault_sync.py`, directly after `_present_at`, add:

```python
def _files_at(vault_dir: Path, rev: str, scope: Path) -> list[str]:
    """Every file under `scope` at `rev`, vault-relative, from a NUL-separated
    listing (§14 A5). Raises `ValueError` when `scope` is outside
    `vault_dir`, like `_diff_name_status`."""
    rel_scope = scope.relative_to(vault_dir)
    out = _git(vault_dir, ["ls-tree", "-r", "-z", "--name-only", rev, "--", str(rel_scope)])
    return [p for p in out.split("\0") if p]


def _dir_at(vault_dir: Path, rev: str, live_dir: Path, scratch: Path) -> Path:
    """`live_dir` as committed at `rev`, materialised under `scratch` -- the
    table mappings and domain schemas track B projects with (phase 1 review
    finding 1, spec 2026-09-26 §14 A1). The returned path may not exist (no
    such files at `rev`); `find_mappings` then finds none and `load_schema`
    returns `{}`.

    A `live_dir` outside the vault (a run folder named by an explicit
    `ARTMIND_HOME`) is not versioned with it, so it is returned unchanged:
    there is no committed copy to prefer."""
    try:
        rel = live_dir.relative_to(vault_dir)
    except ValueError:
        return live_dir
    _materialize(vault_dir, rev, _files_at(vault_dir, rev, live_dir), scratch)
    return scratch / rel
```

In `sync()`, replace:

```python
    from paths import KG_DIR, STRUCTURED_TEXT_DIR
```

with:

```python
    import paths
    from paths import KG_DIR, STRUCTURED_TEXT_DIR
```

Replace (inside the `if plan.regenerate_tables:` block):

```python
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            import_structured_text(scratch / st_rel, tables=plan.regenerate_tables)
```

with:

```python
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            import_structured_text(scratch / st_rel, tables=plan.regenerate_tables)
            mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, scratch)
            schemas_at_head = _dir_at(vault_dir, head, paths.DOMAIN_SCHEMAS_DIR, scratch)
```

Replace:

```python
                found = find_mappings(table_name, domain)
```

with:

```python
                found = find_mappings(table_name, domain, mappings_dir=mappings_at_head)
```

Replace:

```python
                schema = load_schema(domain)
```

with:

```python
                schema = load_schema(domain, schemas_dir=schemas_at_head)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_temporal_load_schema.py test/test_vault_sync.py test/test_table2graph.py test/test_supersession.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/temporal.py artmind/vault_sync.py test/test_temporal_load_schema.py test/test_vault_sync.py
git commit -m "fix(vault-sync): project tables with the mapping and schema committed at head" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: mapping and schema changes are a sync input (spec §14 A1)

`table__*` is gitignored (R4), so a table whose CSV did not change but whose **mapping** or **domain schema** did keeps its old projection on every other machine, silently. The classifier gains a third input:

- **Which files.** `.yaml` files directly in `TABLE_MAPPINGS_DIR` (what `find_mappings` globs) and `*_schema.yaml` directly in `DOMAIN_SCHEMAS_DIR`, diffed `base..head`.
- **Which tables they govern.** The tables that have a CSV committed at `head` under `structured_text/` — the tables this vault can restore. (Not this machine's SQLite registry: that is local, may be empty on a fresh host, and track B restores from `head` anyway.)
- **Mapping added / changed / removed.** Affected = every head table that the mapping's `table:` pattern (and `domain:`, via `TableMapping.matches`) matched at **either** end: the head version for A/M, the base version for M/D. So a narrowed pattern also reaches the tables it stopped covering.
- **Schema added / changed / removed** for domain `d`: affected = head tables in `d` or in a dotted child of `d` (a child inherits the parent's `temporal:` block, `temporal.load_schema`).
- **Decision per affected table.** If any mapping at `head` matches it → regenerate (track B: restore from `head`, then project). Else, if a mapping change affected it → retract `table:<domain>:<table>` from the graph. Else (only a schema change touched an unmapped table) → nothing; it is structured-store only (§14 A2).
- **Every mapping is read from git** (`git show <rev>:<path>`), never the working tree. A mapping that fails to parse at `head` fails the sync (as `find_mappings` does); one that fails to parse at `base` is logged and skipped — refusing would stall sync on history no edit can fix.
- **Dedupe** with track B's own list: a table whose CSV and mapping both changed is regenerated once.

**Files:**
- Modify: `artmind/vault_sync.py` (new `_mapping_at`, `_mappings_at`, `_tables_at`, `_classify_table_sources` after `_classify_structured_text_diff`; `classify_diff`)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_sync.py` (it reuses `_patch_table_sources`, `_mapping_yaml` and `_add_table` from Task 9):

```python
# ── mappings and schemas as sync input (spec 2026-09-26 §14 A1) ─────────────


def _classify(repo, base, domains=None):
    from artmind.vault import VaultLayout

    return vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo), domains)


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
    _, maps, _, base = _mapped_tables_base(repo, monkeypatch)
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -k "mapping or schema" -v`
Expected: 7 FAIL — the six classifier tests for changed/added/removed/narrowed mappings, the changed schema and reading from git (`regenerate_tables == []` / `retract == []`), and `test_sync_retracts_a_table_whose_mapping_was_removed` (`calls["retract_document"] == []`). 3 PASS already: `..._regenerates_once_when_csv_and_mapping_both_change` (track B already regenerates on the CSV change) and `..._scopes_mapping_changes_to_requested_domains` are guards for the new code, and Task 9's `test_sync_projects_a_table_with_the_committed_mapping_and_schema` matches the `-k` too.

- [ ] **Step 3: Implement**

In `artmind/vault_sync.py`, directly after `_classify_structured_text_diff` (before `def classify_diff`), add:

```python
def _mapping_at(vault_dir: Path, rev: str, relpath: str):
    """The table mapping at `relpath` as committed at `rev` -- never the
    working tree (spec 2026-09-26 §14 A1). Raises `MappingError` (a
    `ValueError`) for a mapping that does not parse."""
    import yaml

    from artmind.table2graph import MappingError, parse_mapping

    text = _show(vault_dir, rev, relpath)
    if text is None:
        raise VaultSyncError(f"{relpath} is not in {rev}")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise MappingError(f"{relpath} at {rev[:12]}: invalid YAML: {e}") from None
    return parse_mapping(data, Path(relpath))


def _mappings_at(vault_dir: Path, rev: str) -> list:
    """Every table mapping `find_mappings` would see, as committed at `rev`.
    A mappings dir outside the vault is not versioned with it (see
    `_dir_at`); its live copy is all there is."""
    import paths
    from artmind.table2graph import load_mapping

    live = paths.TABLE_MAPPINGS_DIR
    try:
        rel = live.relative_to(vault_dir)
    except ValueError:
        return [load_mapping(p) for p in sorted(live.glob("*.yaml"))] if live.is_dir() else []
    return [
        _mapping_at(vault_dir, rev, p)
        for p in _files_at(vault_dir, rev, live)
        if Path(p).parent == rel and p.endswith(".yaml")
    ]


def _tables_at(vault_dir: Path, rev: str) -> list[tuple[str, str]]:
    """`(domain, table_name)` for every structured-text CSV committed at
    `rev`: the tables this vault can restore, and so the tables a mapping or
    schema change can re-project."""
    import paths

    st_dir = paths.STRUCTURED_TEXT_DIR
    try:
        files = _files_at(vault_dir, rev, st_dir)
    except ValueError:
        return []
    st_rel = st_dir.relative_to(vault_dir)
    tables = []
    for path in files:
        parts = Path(path).relative_to(st_rel).parts
        if len(parts) == 2 and parts[1].endswith(".csv"):
            tables.append((parts[0], parts[1][: -len(".csv")]))
    return tables


def _classify_table_sources(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Spec 2026-09-26 §14 A1: `table__*` folders are gitignored (R4), so a
    table whose CSV did not change but whose mapping or domain schema did
    would otherwise keep its old projection on every other machine.

    - A mapping (`TABLE_MAPPINGS_DIR/*.yaml`) added, changed or removed
      between `base` and `head` affects every table at `head` its `table:`
      pattern matched at either end -- so a narrowed pattern reaches the
      tables it stopped covering.
    - A domain schema (`DOMAIN_SCHEMAS_DIR/<d>_schema.yaml`) added, changed
      or removed affects the tables of `d` and of its dotted children (a
      child inherits the parent's `temporal:` block, `temporal.load_schema`).

    An affected table some mapping at `head` matches is regenerated. One that
    only a mapping change affected, and that no mapping at `head` matches, is
    retracted (`table:<domain>:<table>`). A schema-affected table no mapping
    matches is a structured-store table only (§14 A2): nothing to do.

    Every mapping is read from git, never the working tree. One that does not
    parse at `head` fails the sync; one that does not parse at `base` is
    skipped with a warning -- refusing would stall sync on history no edit
    can fix."""
    import paths
    from artmind.table2graph import MappingError

    def _rows(live_dir: Path, suffix: str) -> list[tuple[str, str]]:
        try:
            rel = live_dir.relative_to(vault_dir)
        except ValueError:
            return []  # not versioned with the vault: nothing in the diff
        rows = _diff_name_status(vault_dir, base, head, live_dir)
        return [(s, p) for s, p in rows if Path(p).parent == rel and p.endswith(suffix)]

    mapping_rows = _rows(paths.TABLE_MAPPINGS_DIR, ".yaml")
    schema_rows = _rows(paths.DOMAIN_SCHEMAS_DIR, "_schema.yaml")
    if not mapping_rows and not schema_rows:
        return [], []

    tables = _tables_at(vault_dir, head)

    by_mapping: set[tuple[str, str]] = set()
    for status, path in mapping_rows:
        versions = []
        if status in ("A", "M"):
            versions.append(_mapping_at(vault_dir, head, path))
        if status in ("M", "D"):
            try:
                versions.append(_mapping_at(vault_dir, base, path))
            except MappingError as e:
                logger.warning(
                    "vault sync: {} at {} does not parse, so tables only it matched are not retracted: {}",
                    path, base[:12], e,
                )
        for mapping in versions:
            by_mapping.update(key for key in tables if mapping.matches(key[1], key[0]))

    schema_domains = {Path(p).name[: -len("_schema.yaml")] for _, p in schema_rows}
    by_schema = {
        key for key in tables if any(key[0] == d or key[0].startswith(d + ".") for d in schema_domains)
    }

    head_mappings = _mappings_at(vault_dir, head)
    regenerate: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for domain, table_name in sorted(by_mapping | by_schema):
        if not _in_scope(domain, domains):
            continue
        if any(m.matches(table_name, domain) for m in head_mappings):
            regenerate.append((domain, table_name))
        elif (domain, table_name) in by_mapping:
            retract.append((domain, f"table:{domain}:{table_name}"))
    return regenerate, retract
```

Replace the body of `classify_diff`:

```python
    plan = SyncPlan(base=base, head=head)
    plan.replay_docs, kg_retract = _classify_kg_diff(vault_dir, base, head, domains)
    plan.regenerate_tables, table_retract = _classify_structured_text_diff(vault_dir, base, head, domains)

    seen: set[tuple[str, str]] = set()
    for domain, doc_id in kg_retract + table_retract:
        if (domain, doc_id) not in seen:
            seen.add((domain, doc_id))
            plan.retract.append((domain, doc_id))
    return plan
```

with:

```python
    plan = SyncPlan(base=base, head=head)
    plan.replay_docs, kg_retract = _classify_kg_diff(vault_dir, base, head, domains)
    plan.regenerate_tables, table_retract = _classify_structured_text_diff(vault_dir, base, head, domains)
    source_regenerate, source_retract = _classify_table_sources(vault_dir, base, head, domains)
    for key in source_regenerate:
        if key not in plan.regenerate_tables:
            plan.regenerate_tables.append(key)

    seen: set[tuple[str, str]] = set()
    for domain, doc_id in kg_retract + table_retract + source_retract:
        if (domain, doc_id) not in seen:
            seen.add((domain, doc_id))
            plan.retract.append((domain, doc_id))
    return plan
```

(A table whose CSV was deleted is not at `head`, so it can never be in `source_regenerate`; track B's own "retracted, not regenerated" rule still holds.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py test/test_vault_cli.py -q`
Expected: all PASS. (`test_vault_cli.py`'s `vault sync --bootstrapEmpty --dryRun` runs against a real `artmind init` vault whose committed schemas are **not** under the conftest `ARTMIND_HOME`, so the new classifier sees nothing there — expected.)

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault-sync): table mapping and schema changes re-project their tables" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---
### Task 11: one worker-liveness helper (review finding 7)

Three copies of "is the ingest worker alive" have drifted: `vault_sync._worker_running` (`artmind/vault_sync.py:66-84`) treats `PermissionError` (a live pid owned by another user) as alive; `worker._acquire_pid_file` (`artmind/worker.py:24-34`) and `cli._ensure_worker_running` (`artmind/cli.py:109-115`) do not catch it at all, so they crash with `PermissionError`. A new, import-light module holds the one check. Each caller keeps its own pid-file reference (vault_sync reads `paths.WORKER_PID_FILE` at call time, which the suite patches; worker and cli keep their module-level `WORKER_PID_FILE`) and its own behaviour, except that `PermissionError` now means "alive" everywhere: the worker declines to start a second copy, and the CLI declines to spawn one.

**Files:**
- Create: `artmind/worker_pid.py`
- Modify: `artmind/vault_sync.py:66-84`, `:110-114`
- Modify: `artmind/worker.py:17` (import), `:24-34`
- Modify: `artmind/cli.py:109-115`
- Test: `test/test_worker_pid.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_worker_pid.py`:

```python
"""artmind.worker_pid.live_pid: one liveness check for the ingest worker's
pid file, shared by `vault sync`, the worker and the CLI (phase 1 review
finding 7)."""
import os

from artmind.worker_pid import live_pid


def _refuse(pid, sig):
    raise PermissionError(1, "Operation not permitted")


def test_no_pid_file_means_no_worker(tmp_path):
    assert live_pid(tmp_path / "worker.pid") is None


def test_an_unparseable_pid_file_means_no_worker(tmp_path):
    (tmp_path / "worker.pid").write_text("not a pid")
    assert live_pid(tmp_path / "worker.pid") is None


def test_a_live_pid_is_returned(tmp_path):
    (tmp_path / "worker.pid").write_text(f"{os.getpid()}\n")
    assert live_pid(tmp_path / "worker.pid") == os.getpid()


def test_a_dead_pid_means_no_worker(tmp_path):
    (tmp_path / "worker.pid").write_text("999999999")
    assert live_pid(tmp_path / "worker.pid") is None


def test_a_pid_owned_by_another_user_counts_as_alive(tmp_path, monkeypatch):
    (tmp_path / "worker.pid").write_text("4242")
    monkeypatch.setattr(os, "kill", _refuse)
    assert live_pid(tmp_path / "worker.pid") == 4242


def test_the_worker_does_not_start_beside_another_users_worker(tmp_path, monkeypatch):
    import artmind.worker as worker

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("4242")
    monkeypatch.setattr(worker, "WORKER_PID_FILE", pid_file)
    monkeypatch.setattr(os, "kill", _refuse)

    assert worker._acquire_pid_file() is False
    assert pid_file.read_text() == "4242"


def test_the_worker_overwrites_a_stale_pid_file(tmp_path, monkeypatch):
    import artmind.worker as worker

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("999999999")
    monkeypatch.setattr(worker, "WORKER_PID_FILE", pid_file)

    assert worker._acquire_pid_file() is True
    assert pid_file.read_text() == str(os.getpid())


def test_the_cli_does_not_spawn_beside_another_users_worker(tmp_path, monkeypatch):
    import artmind.cli as cli

    pid_file = tmp_path / "worker.pid"
    pid_file.write_text("4242")
    monkeypatch.setattr(cli, "WORKER_PID_FILE", pid_file)
    monkeypatch.setattr(os, "kill", _refuse)
    spawned = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: spawned.append(a))

    cli._ensure_worker_running()

    assert spawned == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_worker_pid.py -v`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'artmind.worker_pid'`.

- [ ] **Step 3: Create the helper**

Create `artmind/worker_pid.py`:

```python
"""Is the ingest worker alive? One answer for `vault sync`'s preflight, the
worker's own start-up, and the CLI that spawns it (phase 1 review finding 7:
three copies had drifted). Stdlib only, so `vault_sync` can import it
without pulling in the worker's ingestion stack."""
from __future__ import annotations

import os
from pathlib import Path


def live_pid(pid_file: Path) -> int | None:
    """The pid in `pid_file` when that process is alive, else None.

    A missing or unparseable file, or a pid with no process behind it, is no
    worker (a stale file is safe to overwrite). A process owned by another
    user (`PermissionError` from `kill(pid, 0)`) is alive."""
    try:
        pid = int(Path(pid_file).read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    return pid
```

- [ ] **Step 4: Use it in the three callers**

In `artmind/vault_sync.py`, delete `_worker_running` (lines 66-84, from `def _worker_running() -> bool:` through its final `    return True`), and in `preflight` replace:

```python
    if _worker_running():
```

with:

```python
    import paths
    from artmind.worker_pid import live_pid

    # The worker writes the same graph keys `sync` replays, and its staging
    # writes are what `sync` would be reading.
    if live_pid(paths.WORKER_PID_FILE) is not None:
```

In `artmind/worker.py`, replace:

```python
from artmind.structured.pipeline import ingest_structured_file
```

with:

```python
from artmind.structured.pipeline import ingest_structured_file
from artmind.worker_pid import live_pid
```

and replace `_acquire_pid_file` (lines 24-34):

```python
def _acquire_pid_file() -> bool:
    if WORKER_PID_FILE.exists():
        try:
            pid = int(WORKER_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            logger.warning("Worker already running (PID {}), exiting", pid)
            return False
        except (ProcessLookupError, ValueError):
            logger.info("Stale PID file found, overwriting")
    WORKER_PID_FILE.write_text(str(os.getpid()))
    return True
```

with:

```python
def _acquire_pid_file() -> bool:
    pid = live_pid(WORKER_PID_FILE)
    if pid is not None:
        logger.warning("Worker already running (PID {}), exiting", pid)
        return False
    if WORKER_PID_FILE.exists():
        logger.info("Stale PID file found, overwriting")
    WORKER_PID_FILE.write_text(str(os.getpid()))
    return True
```

In `artmind/cli.py`, replace (lines 109-115):

```python
def _ensure_worker_running() -> None:
    if WORKER_PID_FILE.exists():
        try:
            pid = int(WORKER_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return  # live worker found
        except (ProcessLookupError, ValueError):
            pass  # stale PID
```

with:

```python
def _ensure_worker_running() -> None:
    from artmind.worker_pid import live_pid

    if live_pid(WORKER_PID_FILE) is not None:
        return  # live worker found (a stale pid file is simply overwritten by the new worker)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_worker_pid.py test/test_vault_sync.py test/test_worker_manifest.py test/test_webui_admin_api.py -q`
Expected: all PASS (`test_preflight_refuses_while_the_ingest_worker_is_running` and `test_preflight_ignores_a_stale_worker_pid` still pass through `paths.WORKER_PID_FILE`).

- [ ] **Step 6: Commit**

```bash
git add artmind/worker_pid.py artmind/vault_sync.py artmind/worker.py artmind/cli.py test/test_worker_pid.py
git commit -m "refactor(worker): one worker-liveness check for sync, worker and CLI" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: real-repo "HEAD unchanged" pins for structured export and the worker (review finding 6)

Spec §11: after ingest, archive, structured export and `vault sync`, `HEAD` and the index are unchanged. Ingest sync, archive and `vault sync` have real-repo tests; structured export and the worker were covered only by the source-scan guard. These two tests **pin existing behaviour** — they are expected to PASS on their first run. If either fails, that is a real D1 regression (artmind writing to the vault's git): stop and report it rather than adjusting the test.

**Files:**
- Test: `test/test_structured_text_export.py`, `test/test_worker_manifest.py`

- [ ] **Step 1: Write the tests**

Append to `test/test_structured_text_export.py`:

```python
# ── artmind never writes to the vault's git (spec 2026-09-26 D1, §11) ────────


def _git_out(repo, *args):
    import subprocess

    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def test_structured_ingest_and_export_leave_git_history_and_index_untouched(tmp_path, monkeypatch):
    """Phase 1 review finding 6: the structured pipeline's auto-export and an
    explicit `export_structured_text()` write CSV + .meta.json into the
    working tree and nothing else -- Obsidian Git commits them."""
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text

    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"]):
        _git_out(tmp_path, *args)
    (tmp_path / "seed.txt").write_text("seed\n")
    _git_out(tmp_path, "add", "seed.txt")
    _git_out(tmp_path, "commit", "-qm", "seed")
    head_before = _git_out(tmp_path, "rev-parse", "HEAD").strip()
    # Any git call artmind makes against "the vault" lands in this repo.
    monkeypatch.setattr("artmind.vault_git.ARTMIND_VAULT_DIR", tmp_path)

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(csv_path, "banking")
    export_structured_text()

    assert _git_out(tmp_path, "rev-parse", "HEAD").strip() == head_before, "artmind must not create commits"
    assert _git_out(tmp_path, "diff", "--cached", "--name-only") == "", "artmind must not stage anything"
    untracked = set(_git_out(tmp_path, "ls-files", "-z", "--others", "--exclude-standard").split("\0"))
    assert {"structured_text/banking/products.csv", "structured_text/banking/products.meta.json"} <= untracked
```

Append to `test/test_worker_manifest.py`:

```python
def test_the_worker_leaves_the_vaults_git_history_and_index_untouched(vault, recorded, monkeypatch):
    """Phase 1 review finding 6 / spec 2026-09-26 §11: a worker job that
    rewrites a note's frontmatter leaves HEAD where it was and nothing staged;
    the note is simply modified on disk for Obsidian Git to commit."""
    import subprocess

    def git(*args):
        return subprocess.run(["git", *args], cwd=vault, capture_output=True, text=True, check=True).stdout

    (vault / "notes").mkdir()
    note = vault / "notes" / "n.md"
    note.write_text("# n\n")
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"],
                 ["add", "-A"], ["commit", "-qm", "seed"]):
        git(*args)
    head_before = git("rev-parse", "HEAD").strip()
    monkeypatch.setattr("artmind.vault_git.ARTMIND_VAULT_DIR", vault)

    def writes_frontmatter(source, image_model, domain=None, **kwargs):
        Path(source).write_text("---\n_artmind_id: id-n\n---\n\n# n\n")
        return {"status": "ok", "domain": domain}

    monkeypatch.setattr(worker_module, "ingest_file", writes_frontmatter)
    monkeypatch.setattr(worker_module, "_get_queued_files", lambda job_id: [str(note)])

    worker_module._process_job(job_id="job-1", domain="general", env={})

    assert git("rev-parse", "HEAD").strip() == head_before, "artmind must not create commits"
    assert git("diff", "--cached", "--name-only") == "", "artmind must not stage anything"
    assert git("diff", "--name-only").split() == ["notes/n.md"], "the frontmatter write is on disk"
```

- [ ] **Step 2: Run them (expected to pass immediately)**

Run: `uv run --group dev pytest test/test_structured_text_export.py::test_structured_ingest_and_export_leave_git_history_and_index_untouched test/test_worker_manifest.py::test_the_worker_leaves_the_vaults_git_history_and_index_untouched -v`
Expected: both PASS. A failure here is a D1 regression — stop and report it.

- [ ] **Step 3: Commit**

```bash
git add test/test_structured_text_export.py test/test_worker_manifest.py
git commit -m "test: structured export and the worker never write the vault's git" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 13: versioned blocks — exact header, CRLF kept, duplicate detected (phase 2 follow-up 6)

Three minors in `artmind/vault.py`'s block writer:

1. **Header shape.** `_BLOCK_START` (`artmind/vault.py:309`) matches any line that *starts* `# ── artmind ─`, so a user's own `# ── artmind ─ my rules` comment is taken for the block start (and, with no end marker after it, `init` refuses the whole file as malformed). The header is now the whole line: `# ── artmind (vN) ` or legacy `# ── artmind `, then a rule of at least three `─` and nothing else. The two existing malformed-block tests used a header with trailing prose (`# ── artmind ─── no end marker`); they are rewritten to a real header with no end line.
2. **CRLF.** `_write_block` reads with `Path.read_text` (universal newlines) and writes LF, silently converting a Windows user's CRLF `.gitignore`. It now reads raw, works in LF, and writes back CRLF when the file had CRLF.
3. **Duplicate blocks.** Only the first block is ever seen, so a second, stale artmind block (a bad hand merge) goes unnoticed and keeps its rules active. `block_status` returns `"duplicate"`, `_write_block` refuses with a message saying what to delete, and `vault doctor` reports it with a hand-edit fix (running `init` would not help: it refuses).

**Files:**
- Modify: `artmind/vault.py:308-358`
- Modify: `artmind/vault_doctor.py:50-64` (`check_block`)
- Test: `test/test_vault_scaffold.py:475-492`, new tests; `test/test_vault_doctor.py`

- [ ] **Step 1: Write the failing tests and fix the two outdated fixtures**

In `test/test_vault_scaffold.py`, in `test_block_status_reports_missing_outdated_current_and_malformed`, replace:

```python
    path.write_text("# ── artmind (v2) ─── no end marker\n.artmind/config.env\n")
```

with:

```python
    path.write_text("# ── artmind (v2) ───\n.artmind/config.env\n")
```

Replace `test_a_malformed_block_is_refused_not_guessed` (lines 486-492):

```python
def test_a_malformed_block_is_refused_not_guessed(tmp_path):
    (tmp_path / ".gitignore").write_text("# ── artmind ─── no end marker\nmine\n")

    with pytest.raises(vault.VaultError, match="end artmind"):
        vault.write_gitignore(tmp_path)

    assert (tmp_path / ".gitignore").read_text() == "# ── artmind ─── no end marker\nmine\n"
```

with:

```python
def test_a_malformed_block_is_refused_not_guessed(tmp_path):
    (tmp_path / ".gitignore").write_text("# ── artmind ───\nmine\n")

    with pytest.raises(vault.VaultError, match="end artmind"):
        vault.write_gitignore(tmp_path)

    assert (tmp_path / ".gitignore").read_text() == "# ── artmind ───\nmine\n"


def test_a_user_comment_that_starts_like_the_header_is_not_the_block(tmp_path):
    original = "# ── artmind ─ my own rules below\nmine\n"
    (tmp_path / ".gitignore").write_text(original)

    assert vault.block_status(tmp_path / ".gitignore", vault.GITIGNORE_BLOCK) == "missing"
    assert vault.write_gitignore(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text() == original + "\n" + vault.GITIGNORE_BLOCK


def test_crlf_line_endings_are_preserved(tmp_path):
    path = tmp_path / ".gitignore"
    path.write_bytes(("user-before\n\n" + _V1_BLOCK + "user-after\n").replace("\n", "\r\n").encode())

    assert vault.write_gitignore(tmp_path) is True

    expected = ("user-before\n\n" + vault.GITIGNORE_BLOCK + "user-after\n").replace("\n", "\r\n")
    assert path.read_bytes() == expected.encode()
    assert vault.block_status(path, vault.GITIGNORE_BLOCK) == "current"
    assert vault.write_gitignore(tmp_path) is False


def test_a_second_artmind_block_is_reported_and_refused(tmp_path):
    path = tmp_path / ".gitignore"
    original = vault.GITIGNORE_BLOCK + "\nmine\n\n" + _V1_BLOCK
    path.write_text(original)

    assert vault.block_status(path, vault.GITIGNORE_BLOCK) == "duplicate"
    with pytest.raises(vault.VaultError, match="2 artmind blocks"):
        vault.write_gitignore(tmp_path)
    assert path.read_text() == original
```

Append to `test/test_vault_doctor.py`:

```python
def test_a_duplicate_artmind_block_fails_with_a_hand_edit_fix(repo):
    text = (repo / ".gitignore").read_text()
    (repo / ".gitignore").write_text(text + "\n" + vault.GITIGNORE_BLOCK)

    check = _by_name(doc.run(repo))[".gitignore artmind block"]

    assert check["status"] == "fail"
    assert "more than one" in check["detail"]
    assert "exactly one" in check["fix"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_scaffold.py test/test_vault_doctor.py -k "block or header or crlf" -v`
Expected: `..._user_comment_that_starts_like_the_header_...` FAILs (`'malformed' == 'missing'`); `test_crlf_line_endings_are_preserved` FAILs (bytes written with `\n`); `test_a_second_artmind_block_...` FAILs (`'current' == 'duplicate'`); the doctor test FAILs (`status == "ok"`). The two edited malformed-block tests PASS.

- [ ] **Step 3: Implement in `artmind/vault.py`**

Replace lines 308-358 (from `# The unversioned original header ("# ── artmind ───") is version 1.` through the end of `_write_block`):

```python
# The unversioned original header ("# ── artmind ───") is version 1.
_BLOCK_START = re.compile(r"^# ── artmind(?: \(v(\d+)\))? ─", re.MULTILINE)
_BLOCK_END = re.compile(r"^# ── end artmind ─.*(?:\n|$)", re.MULTILINE)


def _find_block(text: str) -> tuple[int, int] | None:
    """`(start, end)` offsets of the artmind block in `text`, `end` = -1
    when the end marker is missing; None when there is no block."""
    start = _BLOCK_START.search(text)
    if start is None:
        return None
    end = _BLOCK_END.search(text, start.end())
    return start.start(), (end.end() if end else -1)


def block_status(path: Path, block: str) -> str:
    """`"current"`, `"missing"`, `"outdated"` (an older or edited artmind
    block) or `"malformed"` (a start marker with no end marker) --
    `vault doctor`'s check (spec R8)."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    found = _find_block(text)
    if found is None:
        return "missing"
    start, end = found
    if end == -1:
        return "malformed"
    return "current" if text[start:end] == block else "outdated"


def _write_block(path: Path, block: str) -> bool:
    """Append `block`, or replace an existing artmind block in place.
    Lines outside the block are the user's and are never touched. Returns
    True when the file changed."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    found = _find_block(text)
    if found is None:
        prefix = text if text.endswith("\n") or not text else text + "\n"
        new = prefix + ("\n" if prefix else "") + block
    else:
        start, end = found
        if end == -1:
            raise VaultError(
                f"{path}: the artmind block has no '# ── end artmind' line, so artmind "
                "cannot tell where its rules stop and yours begin -- fix it by hand, "
                "then re-run `artmind init`"
            )
        new = text[:start] + block + text[end:]
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True
```

with:

```python
# A header is the whole line: "# ── artmind (vN) ───…", or before versioning
# (= version 1) "# ── artmind ───…" -- a rule of box-drawing dashes and nothing
# after it. Anchoring the full line keeps a user's own "# ── artmind ─ notes"
# comment from being read as artmind's block. `\r` tolerates CRLF files.
_BLOCK_START = re.compile(r"^# ── artmind(?: \(v(\d+)\))? ─{3,}[ \t\r]*$", re.MULTILINE)
_BLOCK_END = re.compile(r"^# ── end artmind ─.*(?:\n|$)", re.MULTILINE)


def _find_blocks(text: str) -> list[tuple[int, int]]:
    """`(start, end)` offsets of every artmind block in `text`, in order;
    `end` = -1 for a start marker with no end marker after it (always the
    last one found)."""
    blocks: list[tuple[int, int]] = []
    pos = 0
    while (start := _BLOCK_START.search(text, pos)) is not None:
        end = _BLOCK_END.search(text, start.end())
        if end is None:
            blocks.append((start.start(), -1))
            break
        blocks.append((start.start(), end.end()))
        pos = end.end()
    return blocks


def _read_raw(path: Path) -> str:
    """The file as written -- no newline translation, so CRLF survives."""
    return path.read_bytes().decode("utf-8") if path.is_file() else ""


def block_status(path: Path, block: str) -> str:
    """`"current"`, `"missing"`, `"outdated"` (an older or edited artmind
    block), `"malformed"` (a start marker with no end marker) or
    `"duplicate"` (more than one artmind block) -- `vault doctor`'s check
    (spec R8). Line endings do not matter: a CRLF file holding the current
    block is current."""
    text = _read_raw(path).replace("\r\n", "\n")
    blocks = _find_blocks(text)
    if not blocks:
        return "missing"
    if len(blocks) > 1:
        return "duplicate"
    start, end = blocks[0]
    if end == -1:
        return "malformed"
    return "current" if text[start:end] == block else "outdated"


def _write_block(path: Path, block: str) -> bool:
    """Append `block`, or replace an existing artmind block in place.
    Lines outside the block are the user's and are never touched, and a file
    with CRLF line endings keeps them. Returns True when the file changed."""
    raw = _read_raw(path)
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    blocks = _find_blocks(text)
    if len(blocks) > 1:
        raise VaultError(
            f"{path}: has {len(blocks)} artmind blocks, so artmind cannot tell which one is "
            "its own -- delete all but one (from its '# ── artmind' line through its "
            "'# ── end artmind' line), then re-run `artmind init`"
        )
    if not blocks:
        prefix = text if text.endswith("\n") or not text else text + "\n"
        new = prefix + ("\n" if prefix else "") + block
    else:
        start, end = blocks[0]
        if end == -1:
            raise VaultError(
                f"{path}: the artmind block has no '# ── end artmind' line, so artmind "
                "cannot tell where its rules stop and yours begin -- fix it by hand, "
                "then re-run `artmind init`"
            )
        new = text[:start] + block + text[end:]
    if crlf:
        new = new.replace("\n", "\r\n")
    if new == raw:
        return False
    path.write_bytes(new.encode("utf-8"))
    return True
```

- [ ] **Step 4: Report duplicates in `vault doctor`**

In `artmind/vault_doctor.py` `check_block`, replace:

```python
    if status == "malformed":
```

with:

```python
    if status == "duplicate":
        return Check(
            name, FAIL, "more than one artmind block",
            f"edit {filename} by hand so it has exactly one artmind block (keep the newest, "
            "delete the others from their '# ── artmind' line through their '# ── end artmind' "
            "line), then run `artmind init` in the vault root",
        )
    if status == "malformed":
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_scaffold.py test/test_vault_doctor.py test/test_vault_cli.py -q`
Expected: all PASS (`test_an_older_artmind_block_is_replaced_in_place_keeping_user_rules`'s full-rule `_V1_BLOCK` header and the doctor's `# ── artmind ───` fixture both still match the tightened header).

- [ ] **Step 6: Commit**

```bash
git add artmind/vault.py artmind/vault_doctor.py test/test_vault_scaffold.py test/test_vault_doctor.py
git commit -m "fix(vault): exact block header, keep CRLF, refuse duplicate artmind blocks" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 14: review nits (review finding 9)

Three small leftovers:

1. `run_command`'s annotation says `"str | list[str]"`, but `test/test_run_command.py::test_argv_list_accepts_a_path_element` passes a `Path` (the body `str()`s every element for logging).
2. Two test comments still explain themselves by `commit_paths`, which phase 1 deleted: `test/test_ingest_sync_cli.py:355-356` and the docstring of `test_reingest_truly_unchanged_does_not_rewrite_or_commit` in `test/test_ingest_vault_native.py:62-72`.
3. `_carry_sidecar` (`artmind/vault_sync.py:190-204`) encodes ingest's staging layout (the sidecar name, `chunks.json`); it moves to `artmind/ingest.py` beside `EMBEDDING_SIDECAR` as `carry_embedding_sidecar`.

**Files:**
- Modify: `utils/functions.py:1-12` (import), `:76-77`
- Modify: `test/test_ingest_sync_cli.py:355-356`, `test/test_ingest_vault_native.py:62-72`
- Modify: `artmind/ingest.py` (after `read_embedding_sidecar`, ≈line 1430), `artmind/vault_sync.py:16-18`, `:190-204`, the `_carry_sidecar(` call in `sync()`
- Test: `test/test_ingest_staging_atomic.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_ingest_staging_atomic.py`:

```python
def test_carry_embedding_sidecar_copies_only_when_chunks_match(tmp_path):
    """Vectors belong to the on-disk chunks: carried into a snapshot only when
    its chunks.json is byte-identical (moved from vault_sync, review finding 9)."""
    from artmind.ingest import carry_embedding_sidecar

    live, same, other = tmp_path / "live", tmp_path / "same", tmp_path / "other"
    for d in (live, same, other):
        d.mkdir()
    (live / "chunks.json").write_text("[1]")
    (live / "embeddings.json").write_text('{"c1": [0.1]}')
    (same / "chunks.json").write_text("[1]")
    (other / "chunks.json").write_text("[2]")

    carry_embedding_sidecar(live, same)
    carry_embedding_sidecar(live, other)

    assert (same / "embeddings.json").read_text() == '{"c1": [0.1]}'
    assert not (other / "embeddings.json").exists()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_ingest_staging_atomic.py::test_carry_embedding_sidecar_copies_only_when_chunks_match -v`
Expected: FAIL with `ImportError: cannot import name 'carry_embedding_sidecar' from 'artmind.ingest'`.

- [ ] **Step 3: Move `_carry_sidecar` into `ingest.py`**

In `artmind/ingest.py`, directly after `read_embedding_sidecar` (which ends `        return {}`, ≈line 1430) and before `def write_staging(`, add:

```python
def carry_embedding_sidecar(live_dir: Path, snap_dir: Path) -> None:
    """Copy the gitignored embedding sidecar from a live staging folder into
    a snapshot of it (`vault sync` replays from a copy of HEAD) -- only when
    the live `chunks.json` is byte-identical to the snapshot's. The sidecar's
    vectors belong to the on-disk chunks; attaching them to different chunks
    would pair a chunk id with the wrong vector. Without it, the chunk embed
    sweep recomputes locally."""
    sidecar = live_dir / EMBEDDING_SIDECAR
    live_chunks = live_dir / "chunks.json"
    snap_chunks = snap_dir / "chunks.json"
    if not (sidecar.is_file() and live_chunks.is_file() and snap_chunks.is_file()):
        return
    if live_chunks.read_bytes() == snap_chunks.read_bytes():
        shutil.copy2(sidecar, snap_dir / EMBEDDING_SIDECAR)
```

(`shutil` and `Path` are already imported at the top of `ingest.py`.)

In `artmind/vault_sync.py`, delete `_carry_sidecar` (lines 190-204, from `def _carry_sidecar(live_dir: Path, snap_dir: Path) -> None:` through `        shutil.copy2(sidecar, snap_dir / EMBEDDING_SIDECAR)`), then replace in `sync()`:

```python
                _carry_sidecar(KG_DIR / domain / docdir, snap_dir)
```

with:

```python
                ingest.carry_embedding_sidecar(KG_DIR / domain / docdir, snap_dir)
```

and, since nothing else in the module uses it, remove `import shutil` from the imports (lines 16-18 become):

```python
import io
import subprocess
import tarfile
```

- [ ] **Step 4: Widen the `run_command` annotation**

In `utils/functions.py`, replace:

```python
import threading
import time
from pathlib import Path
```

with:

```python
import threading
import time
from collections.abc import Sequence
from pathlib import Path
```

and replace (lines 76-77):

```python
def run_command(
    cmd: "str | list[str]",
```

with:

```python
def run_command(
    cmd: "str | Sequence[str | os.PathLike]",
```

- [ ] **Step 5: Fix the two stale comments**

In `test/test_ingest_sync_cli.py`, replace:

```python
    # Point vault_git at this repo so, before the fix, commit_paths really
    # commits here (and never touches whatever vault the developer has active).
```

with:

```python
    # Point vault_git at this repo, so any git write artmind made during the
    # batch would land here, where the assertions below catch it (and never
    # in whatever vault the developer has active).
```

In `test/test_ingest_vault_native.py`, in the docstring of `test_reingest_truly_unchanged_does_not_rewrite_or_commit`, replace:

```python
    tier stays "metadata_only" either way (decide_version's own vocabulary is
    unchanged) -- what changes is that a truly no-op touch no longer sets
    touched_path, so the caller never calls commit_paths for it."""
```

with:

```python
    tier stays "metadata_only" either way (decide_version's own vocabulary is
    unchanged) -- what changes is that a truly no-op touch no longer sets
    touched_path, so the note is not rewritten on disk at all (and Obsidian
    Git has nothing to commit)."""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_ingest_staging_atomic.py test/test_vault_sync.py test/test_run_command.py test/test_ingest_sync_cli.py test/test_ingest_vault_native.py test/test_no_git_writes.py -q`
Expected: all PASS (`test_sync_carries_the_embedding_sidecar_when_chunks_match_head` and `test_sync_drops_the_sidecar_when_chunks_differ_from_head` now go through `ingest.carry_embedding_sidecar`).

- [ ] **Step 7: Commit**

```bash
git add utils/functions.py artmind/ingest.py artmind/vault_sync.py test/test_ingest_staging_atomic.py test/test_ingest_sync_cli.py test/test_ingest_vault_native.py
git commit -m "chore: review nits -- run_command annotation, stale comments, sidecar helper moves to ingest" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 15: docs, full suite, CLI help

CLAUDE.md §4: the command docstring, the skill and the docs move together. No `justfile` recipe changes (there is no `vault sync` recipe; `vault-doctor`'s comment is still accurate).

**Files:**
- Modify: `artmind/cli.py:3440-3452` (`vault sync` docstring)
- Modify: `artmind/vault_sync.py:1-13` (module docstring)
- Modify: `docs/vault.md` (§"What is in git, and what is not" table row; §"Git: Obsidian Git owns transport"; §"Schemas — vault-level, committed, yours" table-mappings paragraph)
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md` (Situation J)

- [ ] **Step 1: `vault sync` command docstring**

In `artmind/cli.py`, replace the docstring of `vault_sync_cmd` (lines 3440-3452):

```python
    """Replay committed KG-staging and structured-text changes into Neo4j/DuckDB since the last sync.

    Detects exactly which document folders under .artmind/data/kg/** and which
    structured-store tables under .artmind/data/structured_text/** changed in
    git since the last `vault sync`, and replays only those — incremental,
    CDC-like, and git-native. Complements (does not replace) `session close`/
    `session initiate`'s whole-graph snapshot. Run with no marker yet? pass
    --bootstrapEmpty or --bootstrapSynced (see each flag's own help).

    Applies committed content only (never the working tree) and never
    commits. Refuses while a merge/rebase is in progress, while conflicts are
    unresolved, or while the ingest worker is running.
    """
```

with:

```python
    """Replay committed KG-staging, structured-text, table-mapping and schema changes into Neo4j/DuckDB since the last sync.

    Detects exactly which document folders under .artmind/data/kg/**, which
    structured-store tables under .artmind/data/structured_text/**, and which
    table mappings (.artmind/domains/table_mappings/*.yaml) and domain schemas
    (.artmind/domains/schemas/*_schema.yaml) changed in git since the last
    `vault sync`, and replays only those — incremental, CDC-like, and
    git-native. A document folder replays when any file in it changed. A
    changed mapping or schema re-projects the tables it governs; a table a
    removed mapping no longer covers is retracted from the graph; a table no
    mapping names is restored to the structured store only. Complements (does
    not replace) `session close`/`session initiate`'s whole-graph snapshot.
    Run with no marker yet? pass --bootstrapEmpty or --bootstrapSynced (see
    each flag's own help).

    Applies committed content only (never the working tree) and never
    commits. Refuses while a merge/rebase is in progress, while files under
    .artmind/ have unresolved conflicts, or while the ingest worker is running.
    """
```

- [ ] **Step 2: `vault_sync` module docstring**

In `artmind/vault_sync.py`, replace lines 1-13:

```python
"""`vault sync`: git-diff-driven, CDC-style replay into Neo4j and the
structured store (docs/superpowers/specs/2026-09-25-vault-sync-design.md).

Detects exactly which committed KG-staging document folders
(`.artmind/data/kg/**`) and committed structured-store text
(`.artmind/data/structured_text/**`) changed since the last sync, and
replays only those changes -- incremental and git-native, complementing
(not replacing) the whole-graph `session close`/`session initiate` snapshot.

Applies committed content only: every input is materialised from the fixed
`head` via `git archive`, never read from the working tree, and `sync` never
commits (spec 2026-09-26-vault-git-transport-design.md, §6 A1, D1).
"""
```

with:

```python
"""`vault sync`: git-diff-driven, CDC-style replay into Neo4j and the
structured store (docs/superpowers/specs/2026-09-25-vault-sync-design.md).

Detects exactly which committed KG-staging document folders
(`.artmind/data/kg/**`), committed structured-store text
(`.artmind/data/structured_text/**`), table mappings and domain schemas
(`.artmind/domains/**`) changed since the last sync, and replays only those
changes -- incremental and git-native, complementing (not replacing) the
whole-graph `session close`/`session initiate` snapshot.

Applies committed content only: every input -- mappings and schemas included
-- is read from the fixed `head` (`git show`/`git ls-tree -z`/`git archive`),
never from the working tree, and `sync` never commits (spec
2026-09-26-vault-git-transport-design.md, §6 A1, D1, §14).
"""
```

- [ ] **Step 3: `docs/vault.md`**

In the "What is in git, and what is not" table, replace:

```markdown
| `.artmind/data/kg/*/table__*/` | regenerated by `vault sync` from the table's CSV + mapping, like parquet; committing them made sync create commits |
```

with:

```markdown
| `.artmind/data/kg/*/table__*/` | regenerated by `vault sync` from the table's CSV, its table mapping and its domain schema, like parquet; committing them made sync create commits |
```

In "Git: Obsidian Git owns transport", replace:

```markdown
After the plugin pulls changes made on another machine, run
`artmind vault sync` to apply them to this machine's Neo4j and structured
store. It applies committed content only, and refuses while a merge is in
progress, while conflicts are unresolved, or while the ingest worker is
running. A machine with no Obsidian that must still follow the remote (e.g. a
```

with:

```markdown
After the plugin pulls changes made on another machine, run
`artmind vault sync` to apply them to this machine's Neo4j and structured
store. It applies committed content only — every input, table mappings and
domain schemas included, is read from `HEAD`, never the working tree — and it
never commits. What it replays:

- a KG document folder in which **any** file changed, so a folder that an
  auto-commit caught in two halves still converges; a removed folder is
  retracted from the graph;
- a structured table whose CSV or `.meta.json` changed: restored into DuckDB,
  and re-projected into the graph when a table mapping names it. A table no
  mapping names stays a structured-store table only;
- every table a changed, added or removed **table mapping** matches, and the
  mapped tables of a domain whose **schema** changed (and of its dotted child
  domains). A table that a removed or narrowed mapping no longer covers is
  retracted from the graph.

Table ids are local to each machine: the id in a `.meta.json` is
informational, and a restore matches tables by domain and name, so two
machines that each register a new table never collide.

`vault sync` refuses while a merge, rebase or cherry-pick is in progress,
while a file under `.artmind/` has unresolved conflicts (a conflicted note of
yours does not block it), or while the ingest worker is running. A machine
with no Obsidian that must still follow the remote (e.g. a
```

(Keep the rest of that paragraph — `query-only host) uses a plain git pull --no-rebase ...` — as it is.)

In "Schemas — vault-level, committed, yours", replace:

```markdown
**Table mappings** (`ingest table2graph`) live beside them in
`.artmind/domains/table_mappings/`, committed. They are never seeded or
overwritten by `init` — a mapping names a specific table, so it is always
vault data — and `snapshot` curation archives them with the schemas.
```

with:

```markdown
**Table mappings** (`ingest table2graph`) live beside them in
`.artmind/domains/table_mappings/`, committed. They are never seeded or
overwritten by `init` — a mapping names a specific table, so it is always
vault data — and `snapshot` curation archives them with the schemas. An edited
mapping or schema reaches another machine's graph through `artmind vault sync`
once Obsidian Git has committed and pulled it.
```

- [ ] **Step 4: ingestion-helper skill, Situation J**

In `artmind/skills/artmind-ingestion-helper/SKILL.md`, after the paragraph that ends ``so `write-to-graph --folder data/kg/DOMAIN` replays it.`` (the "**Re-running replaces.**" paragraph in Situation J), add:

```markdown
**Other machines.** `table__*` staging is gitignored, so another machine that pulls the vault
re-projects the table itself with `artmind vault sync` — when the table's CSV, its mapping or
its domain schema changed in git. Edit the mapping, let Obsidian Git commit it, and the other
machine follows on its next `vault sync`; remove the mapping and the table's graph document is
retracted there. A table no mapping names is restored to the structured store only.
```

- [ ] **Step 5: Full suite**

Run: `uv run --group dev pytest test/ -q`
Expected: `2277 passed, 14 skipped` (2230 at the start of this plan + 47 new tests), no failures. A dry run of this whole plan in a scratch worktree produced exactly that; any failure is a regression to fix before committing.

- [ ] **Step 6: CLI help sanity**

Run: `uv run artmind vault sync --help | head -20`
Expected: the new first line, `Replay committed KG-staging, structured-text, table-mapping and schema changes into Neo4j/DuckDB since the last sync.`, and the unchanged `--bootstrapEmpty`/`--bootstrapSynced`/`--domain`/`--dryRun`/`--compact` options. (`vault sync` is not a `query` command, so the `serve` daemon proxy in `_entry.py` does not apply.)

Run: `just dev-cli-help 2>/dev/null | grep "COMMAND: cli vault"`
Expected exactly four lines — `COMMAND: cli vault`, then `COMMAND: cli vault status`, `COMMAND: cli vault sync`, `COMMAND: cli vault doctor` — no command added or removed.

- [ ] **Step 7: Commit**

```bash
git add artmind/cli.py artmind/vault_sync.py docs/vault.md artmind/skills/artmind-ingestion-helper/SKILL.md
git commit -m "docs(vault-sync): mappings, schemas and whole-folder replay as sync inputs" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

After this plan: the user runs `just dev-install` (re-seeds the skill into the run folder, CLAUDE.md §2) and tests live against their Neo4j.

---

## Self-review

### Scope → tasks

| Scope item | Task(s) |
|---|---|
| §14 A1 — mapping/schema diff as sync input; changed/added mapping regenerates matching tables (via `TableMapping.matches`, the same `fnmatchcase` + `domain` test `find_mappings` uses); removed mapping retracts `table:<domain>:<table>`; changed schema regenerates the domain's mapped tables; dedupe with track B | 10 |
| §14 A1 / review finding 1 — mappings and schemas read from `head` (materialised into scratch), not the working tree; `find_mappings(mappings_dir=)` (existing) and `load_schema(schemas_dir=)` (new, default unchanged) | 9 (track B), 10 (classifier reads via `git show`) |
| §14 A2 — unmapped table → structured-store restore only | 8 |
| §14 A3 — table-id collisions remapped by `(domain, table_name)`, with a two-`.meta.json`-same-id test | 7 |
| §14 A4 — archive restore, dashboard import, `kg_pull` atomic via `atomic_dir` | 4, 5, 6 |
| §14 A4 — track A replays on any file change; deleted folder still retracts; partial change replays once; `table__*` still excluded | 3 |
| §14 A5 — `-z` for `diff --name-status`, `ls-tree` (`_present_at`, new `_files_at`), `diff --name-only` (preflight), and `vault doctor`'s `ls-files`; non-ASCII + space test | 1 (+2 for preflight, 9 for `_files_at`) |
| §14 A7 / review finding 3 — preflight blocks only on conflicts under `.artmind/`, NUL-separated | 2 |
| Review finding 4 — justfile comment | Already fixed (see top) |
| Review finding 6 — real-repo HEAD-unchanged tests: structured export and worker | 12 |
| Review finding 7 — one worker-alive helper; `PermissionError` → alive everywhere | 11 |
| Review finding 9 — `run_command` annotation, stale test comment(s), `_carry_sidecar` → `ingest.py` | 14 |
| Phase 2 follow-up 6 — CRLF preserved; exact header regex; duplicate block → `"duplicate"`, write refuses, doctor reports | 13 |
| Phase 2 follow-up 6 — "folder briefly absent" | Skipped per the spec (see top) |
| Docs: `docs/vault.md`, `vault sync` docstring, ingestion-helper skill, full suite, CLI help | 15 |

### Ordering and green suite

Each task leaves `uv run --group dev pytest test/ -q` green. The ones that change shared seams update the existing tests in the same task: Task 2 retargets `_conflicting_merge`'s unmerged-path test to a path under `.artmind/`; Task 3 replaces the "observations.json only" test and patches `_present_at` in the mocked-rows test; Task 9 widens every `find_mappings`/`load_schema` fake to accept the new keyword; Task 13 rewrites the two malformed-block fixtures whose header carried trailing prose.

### Consistency check

Names used across tasks: `_diff_name_status`, `_present_at` (Task 1) → used by Task 3; `_files_at`, `_dir_at` (Task 9) → `_files_at` used by Task 10's `_tables_at`/`_mappings_at`; `structured_only` / `"structured_only_tables"` (Task 8) unchanged by Task 9; test helpers `_patch_table_sources`, `_mapping_yaml`, `_add_table` (Task 9) reused by Task 10; `write_dir_atomic(..., carry_over=)` (Task 4) used by Tasks 4–6; `live_pid` (Task 11); `carry_embedding_sidecar` (Task 14).

### Design decisions the spec did not settle (for review)

1. **"Registered table" in A1 means "has a CSV committed at `head`"**, not "is in this machine's SQLite registry". The registry is local and may be empty on a fresh host; `head` is what the vault can restore.
2. **A changed mapping affects the union of the tables its base and head versions matched.** A table that a narrowed or removed mapping stops covering, and that no other head mapping covers, is **retracted** from the graph (its DuckDB table is kept).
3. **A mapping-triggered regeneration re-restores the table into DuckDB** (it goes through track B's existing restore + project path) even though the CSV did not change. Idempotent, simpler than a projection-only path; costs one scoped restore per affected table.
4. **A schema change cascades to dotted child domains** (`banking` → `banking.retail`), because `load_schema` merges the parent's `temporal:` block into the child.
5. **A mapping that does not parse at `head` fails the sync; one that does not parse at `base` is skipped with a warning** (tables only it matched are then not retracted).
6. **A deleted schema of a domain with mapped tables** makes track B fail with its existing `no schema for domain` error until the schema is restored or the mappings removed — not silently skipped.
7. **Mappings/schemas outside the vault** (explicit `ARTMIND_HOME` elsewhere) are read live, since they are not versioned with the vault.
8. **A3 id policy**: in a dump, the first table in sorted meta-path order keeps a duplicated id and later ones get `max+1…`; in a scoped restore, an already-registered table keeps its **local** id, a new one takes its recorded id if free, else SQLite's next id. `restore_all` (wholesale) keeps using the (now unique) dump ids.
9. **`write_dir_atomic` gained `carry_over=False`**: archive restore replaces the folder wholesale (as its `rmtree` did); dashboard import keeps overlay semantics (as `extractall` did); `kg_pull` writes new folders only.
10. **`kg_pull` now skips symlinks** in the pulled repo instead of copying their targets' content. A behaviour change, made because the repo is external and the vault is pushed.
11. **Track A: a folder with changes but no `observations.json` at `head` (and none deleted) is skipped**, waiting for the commit that adds it.
12. **A7 narrows only the unmerged-index check.** A merge in progress (`MERGE_HEAD`) still refuses regardless of which files conflict, per spec §6 A5.
13. **Block header regex** now requires ≥3 `─` and nothing else on the line; the end-marker regex is unchanged.
14. **`live_pid` returns the pid, not a bool**, so the worker can keep logging it.

### Accepted residual risk (vault-sync-completion review, phase 1 review finding 2)

Finding 2 ("`git archive` is not byte-identical to `git show`") was closed above as
harmless *because artmind writes no `export-ignore`/`export-subst`/`text`/`eol`/`filter=`
attributes for `.artmind/data/**`* -- not because `git archive` can't apply such rules at
all. That premise holds only for artmind's own `.gitattributes` block; it says nothing
about the user's.

`git archive` (used by `_materialize` in `artmind/vault_sync.py` to read every sync input
at `head`) applies whatever `.gitattributes`/`core.autocrlf`/LFS smudge filters are in
effect for the *user's own repo*, same as a checkout would. artmind's own block
(`GITATTRIBUTES_BLOCK`, `artmind/vault.py`) sets `.artmind/data/** merge=binary` and
deliberately adds no `text=auto`/`eol=`/`filter=` for that tree -- but a user's own root
`.gitattributes` rule, e.g. a blanket `* text=auto eol=crlf`, is not something artmind
controls or overrides, and it still applies to everything under `.artmind/data/**` unless
the user scopes it away themselves.

**Accepted, not fixed:** the practical blast radius today is narrow. KG staging JSON
(`document.json`, `observations.json`, `chunks.json`, `relationships.json`) round-trips
through `eol=crlf` line-ending conversion without semantic change (JSON parses the same
either way), and the one place a byte-for-byte mismatch would matter -- the embedding
sidecar -- degrades to a cache miss, not a wrong answer: `carry_embedding_sidecar`
(`artmind/ingest.py`) compares the live folder's `chunks.json` bytes against the
materialized snapshot's `chunks.json` bytes and only copies the sidecar over when they
match exactly, so an `eol=crlf` conversion of either copy is read as "changed" and the
sidecar is simply dropped rather than mismatched with the wrong chunk. The embedding is
then recomputed through whichever embedding provider this vault is configured with
(`ARTMIND_KG_EMBEDDINGS_PROVIDER` -- Ollama by default, so typically no external API
cost, but a cost does apply if the vault is configured against a paid provider) rather
than carried over. No `filter=lfs` risk exists unless the user opts into LFS for
`.artmind/data/**` themselves, which nothing in this project suggests. Revisit if
`vault doctor` ever needs to detect and warn about a user rule that shadows artmind's
own attributes for that tree.
