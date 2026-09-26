# Vault git transport — Phase 1 ("stop the damage") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** artmind stops writing to the vault's git repo (no add/commit/rm/push anywhere), and `vault sync` applies only committed content, refusing to run mid-merge or while the ingest worker is running.

**Architecture:** Spec [2026-09-26-vault-git-transport-design.md](../specs/2026-09-26-vault-git-transport-design.md), phase 1 = §5 R1 + §6 A1 + §6 A5. Every git-writing call site is deleted (Obsidian Git owns commits and transport). `vault_git.py` shrinks to read-only helpers. `vault_sync.sync()` gains a preflight and materialises each input from `git archive <head>` into a scratch directory instead of reading the working tree.

**Tech Stack:** Python 3.14, Click, pytest (`just dev-test`), real throwaway git repos in `tmp_path` (never a mocked `run_command` when the behaviour under test is git's).

---

## Context for the implementer

Read these before starting:

- `CLAUDE.md` — especially "Testing implications" §1 (stale `serve` daemon) and §3 (mocked graph sessions: assert on the **parameters sent**, never on counts).
- The spec linked above, sections 1, 2, 5 (R1) and 6 (A1, A5).
- `artmind/vault_sync.py` end to end (≈350 lines). It is the only file with non-trivial new logic.

Terminology:

- **Vault** — a user's Obsidian vault that is also a git repo; artmind's own files live in `<vault>/.artmind/`.
- **KG staging folder** — `.artmind/data/kg/<domain>/<docdir>/` holding `document.json`, `observations.json`, `chunks.json`, `relationships.json`, plus a **gitignored** `embeddings.json` sidecar (chunk vectors).
- **Structured text** — `.artmind/data/structured_text/manifest.json` + `<domain>/<table>.csv`, the git-committable form of the DuckDB structured store.
- **Cursor** — `last_synced_commit` in `.artmind/state.json` (unchanged in phase 1; moves into Neo4j in phase 3).

Run the suite with:

```bash
just dev-test
```

(`uv run --group dev pytest test/ -v`; ~9s, no Neo4j, no network.) For a single file: `uv run --group dev pytest test/test_vault_sync.py -v`.

Known interim state after phase 1 (resolved in phase 2, do **not** fix here): `vault sync` track B still writes `table__*` staging folders into the working tree. They are no longer committed by artmind, so Obsidian Git will commit them until phase 2 gitignores them.

## File map

| File | Change |
|---|---|
| `utils/functions.py` | `run_command` accepts an argv list as well as a string |
| `artmind/ingest.py` | stop committing converted/stamped markdown |
| `artmind/cli.py` | `ingest sync`: no per-file commit, no push; `init --interactive`: no push prompt; `docs archive` help text |
| `artmind/worker.py` | no per-file commit + push |
| `artmind/structured/pipeline.py` | export text, no commit + push |
| `artmind/archive.py` | archive = plain delete; restore = no commit; result keys change |
| `artmind/setup.py` | config.env template: drop `ARTMIND_VAULT_GIT_PUSH` |
| `artmind/vault_git.py` | delete `commit_paths`, `remove_paths`, `maybe_push`; argv for the rest |
| `artmind/vault_sync.py` | argv `_git`; `preflight()`; materialise inputs from `head`; no commit of regenerated tables |
| `test/test_run_command.py` | new |
| `test/test_no_git_writes.py` | new — guard that no artmind module writes to git |
| `test/test_ingest_sync_cli.py`, `test/test_ingest_binary_derived.py`, `test/test_archive.py`, `test/test_vault_cli.py`, `test/test_vault_scaffold.py`, `test/test_vault_git.py`, `test/test_vault_sync.py` | updated |
| `docs/vault.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md` | prose updated |

---

### Task 1: `run_command` accepts an argv list

Every git call built as an f-string goes through `shlex.split`, so a `"` in a path breaks it. Callers move to argv lists in later tasks; this task only makes that possible, keeping the string form working.

**Files:**
- Modify: `utils/functions.py` (`run_command`, ≈line 75)
- Test: `test/test_run_command.py` (create)

- [ ] **Step 1: Write the failing test**

Create `test/test_run_command.py`:

```python
"""utils.functions.run_command: argv-list form (vault git transport, phase 1)."""
import sys

from utils.functions import run_command


def test_argv_list_passes_arguments_verbatim(tmp_path):
    # A double quote and a space in one argument: the f-string + shlex.split
    # form cannot express this without escaping; an argv list needs none.
    arg = 'he said "hi" there'
    rc, out, _ = run_command([sys.executable, "-c", "import sys; print(sys.argv[1])", arg])
    assert rc == 0
    assert out.strip() == arg


def test_string_form_still_works():
    rc, out, _ = run_command(f'{sys.executable} -c "print(42)"')
    assert rc == 0
    assert out.strip() == "42"


def test_argv_list_honours_cwd(tmp_path):
    rc, out, _ = run_command([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
    assert rc == 0
    assert out.strip() == str(tmp_path.resolve())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_run_command.py -v`
Expected: `test_argv_list_passes_arguments_verbatim` and `test_argv_list_honours_cwd` FAIL (`shlex.split` raises `AttributeError: 'list' object has no attribute 'read'` or similar); `test_string_form_still_works` PASSES.

- [ ] **Step 3: Implement**

In `utils/functions.py`, replace the signature and the first lines of `run_command`'s body. The full new function head (docstring kept, one paragraph added):

```python
def run_command(
    cmd: "str | list[str]",
    timeout: int | None = None,
    cwd: Path | None = None,
    extra_env: dict | None = None,
    expected_codes: "tuple[int, ...]" = (),
) -> tuple[int, str, str]:
    """Run `cmd`, returning (returncode, stdout, stderr).

    `cmd` is either an argv list (preferred: arguments pass through verbatim,
    so a path or message containing quotes or spaces needs no escaping) or a
    shell-like string, split with `shlex.split` (kept for existing callers).

    ``expected_codes`` names non-zero exits that are a normal outcome rather
    than a failure, so they log at debug instead of error. Some tools use the
    exit code as an *answer*: `git rev-parse HEAD` exits 128 in a
    freshly-`init`ed repo that has no commits yet -- the state `artmind init`
    deliberately leaves a new vault in.

    Logging those at ERROR is worse than noise: it teaches a reader to ignore
    the level, and sends anyone debugging a real problem after a non-problem.
    """
    argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    logger.debug("CMD: {}", cmd if isinstance(cmd, str) else shlex.join(argv))
    if timeout is not None:
        logger.debug("CMD timeout: {}s", timeout)
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    if extra_env:
        env.update(extra_env)
    t0 = time.monotonic()
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
```

Everything after the `subprocess.run` line is unchanged. Remove the old `logger.debug("CMD: {}", cmd_str)` and `cmd = shlex.split(cmd_str)` lines. (The docstring's example about `git diff --cached --quiet` is removed on purpose — that caller is deleted in Task 7.)

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_run_command.py test/test_vault_git.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/functions.py test/test_run_command.py
git commit -m "feat(utils): run_command accepts an argv list"
```

---

### Task 2: `ingest_file` stops committing the stamped/converted markdown

**Files:**
- Modify: `artmind/ingest.py:29` (import) and `:1048-1052` (call)
- Test: `test/test_ingest_binary_derived.py:36-37`

- [ ] **Step 1: Change the test to the new contract**

In `test/test_ingest_binary_derived.py`, in the first test (the one ending with `assert "convert" in log`), replace the last two lines:

```python
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "convert" in log
```

with:

```python
    # artmind never commits (spec 2026-09-26 D1): the derived markdown is left
    # for Obsidian Git to commit, so history still holds only the fixture's seed.
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "convert" not in log
    status = subprocess.run(["git", "status", "--porcelain"], cwd=vault, capture_output=True, text=True).stdout
    assert status.strip(), "the derived markdown is on disk, uncommitted"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_ingest_binary_derived.py -v`
Expected: that test FAILS on `assert "convert" not in log`.

- [ ] **Step 3: Implement**

In `artmind/ingest.py` delete line 29:

```python
from artmind.vault_git import commit_paths as _vault_commit_paths
```

and delete the call after `write_document(registered_path, new_meta, body)` (≈line 1048):

```python
    _vault_commit_paths(
        [registered_path],
        f"artmind: {'convert' if not existing_meta else 'reconvert'} "
        f"{stem} ({effective_domain})",
    )
```

Leave `_vault_current_commit` (line 30) alone — it only reads.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_ingest_binary_derived.py test/test_ingest_vault_native.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_ingest_binary_derived.py
git commit -m "fix(ingest): stop committing converted markdown to the vault"
```

---

### Task 3: `ingest sync` and the worker stop committing and pushing

**Files:**
- Modify: `artmind/cli.py:718` (`committed_any`), `:750-761` (per-file commit), `:799-804` (push)
- Modify: `artmind/worker.py:157-161`
- Test: `test/test_ingest_sync_cli.py` (≈lines 316-497)

- [ ] **Step 1: Replace the commit/push tests**

In `test/test_ingest_sync_cli.py`, delete these five tests entirely:
`test_each_document_is_committed_before_the_next_is_extracted`,
`test_an_interruption_mid_batch_leaves_earlier_documents_committed`,
`test_nothing_is_committed_or_pushed_when_no_vault_file_was_touched`,
`test_push_is_still_batched_to_one_call_after_the_loop`,
`test_interrupted_batch_leaves_a_real_vault_clean_for_the_files_it_touched`.

Keep `_stub_batch`, `_seeded_md` and `_init_git_repo`, but remove the line `monkeypatch.setattr("artmind.vault_git.load_env", lambda: {})` from `_stub_batch` (the function it patches no longer reads env after Task 7; leaving it is harmless but misleading).

Append in their place:

```python
# ── artmind never writes to the vault's git (spec 2026-09-26, D1) ───────────


def test_ingest_sync_leaves_git_history_and_index_untouched(monkeypatch, tmp_path):
    """Obsidian Git owns commits and pushes. A batch that writes frontmatter
    into three files must leave HEAD where it was and nothing staged -- the
    files are simply modified on disk for the plugin to pick up."""
    import subprocess

    import artmind.cli as cli_mod

    vault = tmp_path / "vault"
    vault.mkdir()
    files = [_seeded_md(vault, n) for n in ("a.md", "b.md", "c.md")]
    _init_git_repo(vault)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=vault, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Point vault_git at this repo so, before the fix, commit_paths really
    # commits here (and never touches whatever vault the developer has active).
    monkeypatch.setattr("artmind.vault_git.ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(cli_mod, "collect_ingest_files", lambda p: files)
    monkeypatch.setattr("artmind.ingest.rebuild_projection", lambda d: {})

    def fake_ingest_file(f, *a, **k):
        f.write_text(f"---\n_artmind_id: id-{f.stem}\n---\n\n# {f.name}\n", encoding="utf-8")
        return {"status": "ok", "domain": "general", "touched_path": f}

    monkeypatch.setattr(cli_mod, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(cli_mod, "ingest_to_kg", lambda *a, **k: True)

    result = CliRunner().invoke(cli, ["ingest", "sync", str(vault), "--domain", "general"])
    assert result.exit_code == 0, result.output

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=vault, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_after == head_before, "artmind must not create commits"
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=vault, capture_output=True, text=True, check=True
    ).stdout
    assert staged.strip() == "", "artmind must not stage anything"
    modified = subprocess.run(
        ["git", "diff", "--name-only"], cwd=vault, capture_output=True, text=True, check=True
    ).stdout.split()
    assert sorted(modified) == ["a.md", "b.md", "c.md"], "the frontmatter writes are on disk"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_ingest_sync_cli.py::test_ingest_sync_leaves_git_history_and_index_untouched -v`
Expected: FAIL on `assert head_after == head_before` — `commit_paths` still commits each file.

- [ ] **Step 3: Implement in `artmind/cli.py`**

Delete `committed_any = False` (line 718).

Replace the block (≈lines 748-761):

```python
            if result.get("status") == "ok":
                # Commit the frontmatter write NOW, before chunk splitting and
                # ... (comment continues) ...
                if result.get("touched_path"):
                    from artmind.vault_git import commit_paths

                    touched = Path(result["touched_path"])
                    if commit_paths([touched], f"artmind: ingest {touched.name}"):
                        committed_any = True
                effective_domain = result.get("domain", domain)
```

with:

```python
            if result.get("status") == "ok":
                # artmind never commits (spec 2026-09-26, D1): the frontmatter
                # write stays on disk for Obsidian Git to commit.
                effective_domain = result.get("domain", domain)
```

Delete the push block (≈lines 799-804):

```python
    # Push is a network courtesy, not the durable write (see vault_git), so it
    # stays batched: one push after the loop rather than one per document.
    if committed_any:
        from artmind.vault_git import maybe_push

        maybe_push()
```

- [ ] **Step 4: Implement in `artmind/worker.py`**

Delete (≈lines 157-161):

```python
                    if result.get("touched_path"):
                        from artmind.vault_git import commit_paths, maybe_push

                        if commit_paths([Path(result["touched_path"])], f"artmind: ingest {Path(file_path).name}"):
                            maybe_push()
```

If `Path` is now unused in `worker.py`, leave the import — it is used elsewhere in the file (check with `grep -n "Path(" artmind/worker.py`).

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_ingest_sync_cli.py test/test_worker_manifest.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py artmind/worker.py test/test_ingest_sync_cli.py
git commit -m "fix(ingest): stop committing and pushing from ingest sync and the worker

Obsidian Git owns vault commits and transport. The bare git push here was
rejected whenever the other machine had pushed first."
```

---

### Task 4: structured pipeline exports text without committing or pushing

**Files:**
- Modify: `artmind/structured/pipeline.py:342-362` (`_export_text_best_effort`)

No new test: the function's only change is deleting two calls, and Task 7's guard test fails if they remain. Existing tests cover the export.

- [ ] **Step 1: Implement**

Replace the whole function with:

```python
def _export_text_best_effort(domain: str, table_names: list[str]) -> None:
    """Re-export ``table_names``' CSV + manifest.json into the vault's
    structured_text dir (``structured/text_export.py`` -- the git-commitable
    text ``db reindex`` rebuilds parquet + registry rows from). Best-effort for
    the same reason as ``_project_catalogue_best_effort``: a write to the
    vault's working tree must never fail the ingest that produced it. artmind
    does not commit it (spec 2026-09-26, D1) -- Obsidian Git does."""
    try:
        from artmind.structured import text_export

        tables = [t for name in table_names if (t := registry.get_table(name, domain=domain)) is not None]
        if not tables:
            return
        text_export.export_structured_text(tables=tables)
    except Exception as e:
        logger.warning("structured pipeline: text export failed for domain '{}': {}", domain, e)
```

If `Path` becomes unused in `pipeline.py`, check with `grep -n "Path" artmind/structured/pipeline.py` before removing its import.

- [ ] **Step 2: Reword `export_structured_text`'s docstring**

In `artmind/structured/text_export.py` (≈lines 79-88) the docstring names `vault_git.commit_paths` twice; Task 7's guard test flags it. Change

```
    produces a byte-identical CSV and `vault_git.commit_paths` correctly
    no-ops instead of committing a spurious reordering.
```

to

```
    produces a byte-identical CSV and git sees no change, instead of a
    spurious reordering for Obsidian Git to commit.
```

and

```
    unusable. Returns `{"tables": <count exported>, "files": [<paths written>]}`,
    for the caller to hand straight to `vault_git.commit_paths`.
```

to

```
    unusable. Returns `{"tables": <count exported>, "files": [<paths written>]}`.
```

- [ ] **Step 3: Run the tests**

Run: `uv run --group dev pytest test/ -k "structured" -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add artmind/structured/pipeline.py artmind/structured/text_export.py
git commit -m "fix(structured): export structured text without committing or pushing"
```

---

### Task 5: archive deletes the vault file without `git rm`; restore does not commit

**Files:**
- Modify: `artmind/archive.py:224-240` (archive), `:256` (result), `:323` (restore) and the restore result
- Modify: `artmind/cli.py:2776-2798` (`docs archive` docstring + confirm prompt)
- Test: `test/test_archive.py:157-162`, `:249-254`

- [ ] **Step 1: Update the tests**

In `test_archive_document_bundles_removes_and_indexes`, replace:

```python
    # vault file is gone, and it was a real git commit
    assert not doc.exists()
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "archive policy" in log

    assert result["git_committed"] is True
```

with:

```python
    # vault file is gone from disk; the deletion is left for Obsidian Git to
    # commit (spec 2026-09-26, D1) -- artmind makes no commit of its own.
    assert not doc.exists()
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "archive policy" not in log
    status = subprocess.run(["git", "status", "--porcelain"], cwd=vault, capture_output=True, text=True).stdout
    assert " D " in status or status.startswith(" D"), status
    assert "git_committed" not in result
```

In `test_restore_from_archive_writes_vault_file_and_retires`, replace:

```python
    assert result["committed"] is True
    assert retire_calls == [("doc-1", "general")]

    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "restore-from-archive doc-1" in log
```

with:

```python
    assert "committed" not in result
    assert retire_calls == [("doc-1", "general")]

    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "restore-from-archive" not in log
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --group dev pytest test/test_archive.py -v`
Expected: the two tests FAIL.

- [ ] **Step 3: Implement the archive side**

In `artmind/archive.py`, replace (≈lines 224-240):

```python
    git_committed = False
    if vault_path is not None and vault_path.exists():
        git_committed = vault_git.remove_paths([vault_path], f"artmind: archive {stem} ({domain})")
        if not git_committed:
            # ...
            vault_path.unlink(missing_ok=True)
            logger.warning(
                ...
            )
```

with:

```python
    if vault_path is not None and vault_path.exists():
        # A plain delete: Obsidian Git commits the removal (spec 2026-09-26,
        # D1). The bundle written above is the recovery path.
        vault_path.unlink(missing_ok=True)
```

and remove the `"git_committed": git_committed,` line from the returned dict (≈line 256).

- [ ] **Step 4: Implement the restore side**

Delete (≈line 323):

```python
    vault_git.commit_paths([target_path], f"artmind: restore-from-archive {archive_id}")
```

Then find the `return {...}` of `restore_from_archive` (`grep -n '"committed"' artmind/archive.py`) and remove its `"committed": ...` entry, plus any local variable that only fed it.

`vault_git` is still imported for `current_commit()` (manifest `vault_commit`) — keep the import.

- [ ] **Step 5: Update the CLI prose**

In `artmind/cli.py` `docs_archive`, change the docstring sentence

```
    then removes it from the graph AND from the vault — a real `git rm` +
    commit, the one operation where artmind deletes human-authored content
    from your repo.
```

to

```
    then removes it from the graph AND deletes its file from the vault — the
    one operation where artmind deletes human-authored content. The deletion
    is committed by Obsidian Git like any other change; artmind makes no
    commit.
```

and the confirm prompt text `"delete its file from the vault (git rm + commit). Continue?"` to `"delete its file from the vault. Continue?"`. Also check `grep -n "committed" artmind/cli.py` near `docs restore-from-archive` / `docs archive` for any echo of the removed keys and drop it.

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_archive.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add artmind/archive.py artmind/cli.py test/test_archive.py
git commit -m "fix(archive): delete and restore vault files without committing"
```

---

### Task 6: `init --interactive` stops offering push-after-ingest

**Files:**
- Modify: `artmind/setup.py:340-376` (`_CONFIG_ENV_TEMPLATE`, `_render_config_env`)
- Modify: `artmind/cli.py:3544-3546` (the `git_push` confirm)
- Test: `test/test_vault_cli.py:131,143`, `test/test_vault_scaffold.py:413,420`

- [ ] **Step 1: Update the tests**

In `test/test_vault_cli.py` `test_init_interactive_prompts_for_neo4j_connection_and_remote`: delete the answer line `"y",  # push after each ingest?` and replace
`assert "ARTMIND_VAULT_GIT_PUSH=1" in config.splitlines()` with
`assert "ARTMIND_VAULT_GIT_PUSH" not in config`.

In `test/test_vault_scaffold.py` `test_scaffold_writes_supplied_config_answers`: delete `"git_push": True,` from the dict and replace
`assert "ARTMIND_VAULT_GIT_PUSH=1" in config.splitlines()` with
`assert "ARTMIND_VAULT_GIT_PUSH" not in config`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --group dev pytest test/test_vault_cli.py test/test_vault_scaffold.py -v`
Expected: both FAIL (the template still contains the commented `# ARTMIND_VAULT_GIT_PUSH=1` line; the interactive one also misreads the remote URL as the push answer).

- [ ] **Step 3: Implement**

In `artmind/setup.py` `_CONFIG_ENV_TEMPLATE`, replace:

```
# ── optional ──────────────────────────────────────────────────────────────────
# Push the vault's git repo after artmind commits frontmatter. Leave unset if
# something else (e.g. the Obsidian Git plugin) already owns pushing.
{git_push_line}

```

with:

```
# ── optional ──────────────────────────────────────────────────────────────────
# artmind never commits, pulls or pushes this vault's git repo -- the Obsidian
# Git plugin owns that (docs/vault.md, "Git: Obsidian Git owns transport").

```

In `_render_config_env`, remove the `git_push: bool = False,` parameter and the `git_push_line=...` format argument.

In `artmind/cli.py`, delete from `config_answers`:

```python
            "git_push": click.confirm(
                "  Push this vault's git repo after each ingest?", default=False
            ),
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_vault_cli.py test/test_vault_scaffold.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/setup.py artmind/cli.py test/test_vault_cli.py test/test_vault_scaffold.py
git commit -m "fix(init): stop offering push-after-ingest; Obsidian Git owns transport"
```

---

### Task 7: `vault_git` becomes read-only, with a guard test

**Files:**
- Modify: `artmind/vault_git.py`
- Test: `test/test_vault_git.py`, `test/test_no_git_writes.py` (create)

- [ ] **Step 1: Write the guard test**

Create `test/test_no_git_writes.py`:

```python
"""Guard for spec 2026-09-26 D1: artmind never writes to the vault's git repo.

Obsidian Git owns commit/pull/push. A source scan is deliberately blunt: any
reintroduced write call fails here even if no behavioural test exercises the
code path that makes it (the worker's per-file commit was exactly such a path).
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SOURCES = sorted(
    p for root in ("artmind", "utils") for p in (REPO / root).rglob("*.py")
)

# git subcommands that write history, the index, refs on a remote, or the tree
WRITE_SUBCOMMANDS = ("add", "commit", "rm", "push", "pull", "merge", "rebase", "reset", "checkout", "stash")
PATTERNS = [
    re.compile(r"""["']git\s+(%s)\b""" % "|".join(WRITE_SUBCOMMANDS)),          # "git commit ..."
    re.compile(r"""["']git["']\s*,\s*["'](%s)["']""" % "|".join(WRITE_SUBCOMMANDS)),  # ["git", "commit", ...]
    re.compile(r"\b(commit_paths|remove_paths|maybe_push)\b"),
]

# Writes against a repo that is NOT the user's vault, or (phase 5) `vault
# resolve`'s scoped staging. Each entry needs a one-line reason. Empty today.
ALLOWED: set[str] = set()


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_git_write_calls(path):
    rel = str(path.relative_to(REPO))
    if rel in ALLOWED:
        pytest.skip("writes to a non-vault repo; see ALLOWED")
    hits = [
        f"{rel}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(p.search(line) for p in PATTERNS)
    ]
    assert not hits, "artmind must not write to the vault's git repo:\n" + "\n".join(hits)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_no_git_writes.py -v`
Expected: FAIL for exactly two files — `artmind/vault_git.py` (still defines `commit_paths`/`remove_paths`/`maybe_push` and runs `git add`/`commit`/`rm`/`push`) and `artmind/vault_sync.py:331` (`vault_git.commit_paths(`). Every other hit was removed by Tasks 1-5 (this regex was dry-run against the pre-phase-1 tree: its only hits were the call sites those tasks delete, plus two docstrings in `text_export.py` reworded in Task 4 and one in `utils/functions.py` reworded in Task 1). `cli.py`'s `["git", "init", "-q"]` does not match — `init` is not a write subcommand here. If any other file fails, read it: it is a real write to remove, not something to allowlist.

- [ ] **Step 3: Rewrite `artmind/vault_git.py`**

Replace the whole file with:

```python
"""Read-only git helpers for the vault (spec 2026-09-26, D1/D2).

artmind never writes to the vault's git repo -- the Obsidian Git plugin owns
commit, pull and push. What remains here only reads: provenance (`HEAD`),
dirtiness for snapshot manifests, and configuring a remote at `init` time.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from paths import ARTMIND_VAULT_DIR
from utils.functions import run_command


def _vault_root() -> Path | None:
    if ARTMIND_VAULT_DIR is None or not ARTMIND_VAULT_DIR.is_dir():
        return None
    if not (ARTMIND_VAULT_DIR / ".git").exists():
        logger.debug("vault_git: {} is not a git repo", ARTMIND_VAULT_DIR)
        return None
    return ARTMIND_VAULT_DIR


def current_commit() -> str | None:
    """The vault's git HEAD sha at this moment — provenance (`_source_commit`),
    never identity. None when there's no vault, no git repo, or no commits yet."""
    vault = _vault_root()
    if vault is None:
        return None
    # 128 is "no commits yet", which is exactly the state `artmind init` leaves
    # a new vault in -- expected, not a failure worth an ERROR line.
    rc, out, _ = run_command(["git", "rev-parse", "HEAD"], cwd=vault, expected_codes=(128,))
    return out.strip() if rc == 0 else None


def is_dirty() -> bool | None:
    """Whether the vault has uncommitted changes right now. `None` (not
    `False`) when there's no vault, no git repo, or the check itself fails --
    a snapshot manifest recording `vault_dirty: false` for a vault that was
    never checked would read as a real guarantee it isn't."""
    vault = _vault_root()
    if vault is None:
        return None
    rc, out, _ = run_command(["git", "status", "--porcelain"], cwd=vault)
    if rc != 0:
        return None
    return bool(out.strip())


def add_remote(root: Path, url: str, name: str = "origin") -> str:
    """Configure a git remote for `root` (`artmind init --interactive`/`--remote`).
    Configuration, not transport: artmind still never pushes to it.

    Takes an explicit `root` rather than going through `_vault_root()`, since
    this runs at scaffold time -- before `root` is necessarily the process's
    resolved "active" vault.

    Returns "added", "exists" (a `name` remote is already configured -- left
    alone rather than silently repointed), or "failed" (not a git repo yet, or
    the git command itself errored). Never raises.
    """
    root = Path(root)
    if not (root / ".git").exists():
        return "failed"
    rc, out, _ = run_command(["git", "remote"], cwd=root)
    if rc == 0 and name in out.split():
        return "exists"
    rc, out, err = run_command(["git", "remote", "add", name, url], cwd=root)
    if rc != 0:
        logger.warning("vault_git: git remote add failed ({}): {}", rc, err or out)
        return "failed"
    logger.info("vault_git: added remote {} -> {}", name, url)
    return "added"
```

(`load_env` is no longer imported.)

- [ ] **Step 4: Update `test/test_vault_git.py`**

- Change the module docstring to `"""Tests for artmind.vault_git: read-only helpers (spec 2026-09-26, D1)."""`.
- Delete every test whose name starts with `test_commit_paths_`, `test_maybe_push_`, `test_remove_paths_`.
- In `test_is_dirty_false_on_a_clean_vault` and `test_is_dirty_true_with_uncommitted_changes`, replace `vault_git.commit_paths([f], "seed")` with a real commit via a helper added at the top of the file:

```python
def _commit(path, message):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=path, check=True)
```

so the line becomes `_commit(vault, "seed")`.
- In `class TestExpectedExitCodesAreNotErrors`, delete `test_having_changes_to_commit_is_not_logged_as_a_failure` and `test_no_configured_remote_is_not_logged_as_a_failure`; update the class docstring's first line to `"""`git rev-parse HEAD` answers with its exit status rather than failing.`. Change `_recording`'s parameter from `cmd_str` to `cmd`.

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_vault_git.py test/test_no_git_writes.py test/test_unified_snapshot_phase5.py -v`
Expected: `test_vault_git.py` and `test_unified_snapshot_phase5.py` PASS; `test_no_git_writes.py` still FAILS only for `artmind/vault_sync.py` (fixed in Task 9). If it fails anywhere else, fix that before moving on.

- [ ] **Step 6: Commit**

```bash
git add artmind/vault_git.py test/test_vault_git.py test/test_no_git_writes.py
git commit -m "refactor(vault_git): read-only helpers only; guard against git writes"
```

---

### Task 8: `vault sync` preflight — refuse mid-merge, with conflicts, or while the worker runs

**Files:**
- Modify: `artmind/vault_sync.py` (`_git`, `head_sha`, `_diff_name_status`, `_show`, new `preflight`, `sync`)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

At the top of `test/test_vault_sync.py`, after the `repo` fixture, add an autouse fixture so a real ingest worker on the developer's machine can never make these tests flaky:

```python
@pytest.fixture(autouse=True)
def _no_worker(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
```

Append a new section:

```python
# ── preflight (spec 2026-09-26 §6 A5) ────────────────────────────────────────


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
    stash pop conflict): the index still has unmerged entries."""
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _conflicting_merge(repo)
    git_dir = repo / ".git"
    (git_dir / "MERGE_HEAD").unlink()

    with pytest.raises(vs.VaultSyncError, match="unresolved conflicts"):
        vs.sync(repo, bootstrap_empty=True)


def test_preflight_refuses_while_the_ingest_worker_is_running(repo, monkeypatch, tmp_path):
    import os
    import paths

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    (tmp_path / "worker.pid").write_text(str(os.getpid()))  # a live pid: this test process

    with pytest.raises(vs.VaultSyncError, match="ingest worker is running"):
        vs.sync(repo, bootstrap_empty=True)


def test_preflight_ignores_a_stale_worker_pid(repo, monkeypatch, tmp_path):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    (tmp_path / "worker.pid").write_text("999999999")  # no such process

    result = vs.sync(repo, bootstrap_synced=True)
    assert result["bootstrap"] == "synced"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -k preflight -v`
Expected: the four refusal tests FAIL (no `VaultSyncError` raised); `test_preflight_ignores_a_stale_worker_pid` PASSES.

- [ ] **Step 3: Move `vault_sync.py`'s git calls to argv**

Replace `_git` with:

```python
def _git(vault_dir: Path, args: list[str]) -> str:
    rc, out, err = run_command(["git", *args], cwd=vault_dir)
    if rc != 0:
        raise VaultSyncError(f"git {' '.join(args)} failed: {err or out}")
    return out
```

In `head_sha`: `run_command(["git", "rev-parse", "HEAD"], cwd=vault_dir, expected_codes=(128,))`.

In `_diff_name_status`: `out = _git(vault_dir, ["diff", "--no-renames", "--name-status", base, head, "--", str(rel_scope)])`.

In `_show`:

```python
    rc, _, _ = run_command(["git", "cat-file", "-e", f"{rev}^{{commit}}"], cwd=vault_dir, expected_codes=(128,))
    ...
    rc, out, _ = run_command(["git", "show", f"{rev}:{relpath}"], cwd=vault_dir, expected_codes=(128,))
```

(`f"{rev}^{{commit}}"` renders as `<sha>^{commit}` — the doubled braces are f-string escaping, same as today.)

- [ ] **Step 4: Add `preflight` and call it**

Add after `head_sha`:

```python
def _worker_running() -> bool:
    """Whether this vault's ingest worker is alive (its pid file names a live
    process). The worker writes the same graph keys `sync` replays, and its
    staging writes are what `sync` would be reading."""
    import os

    import paths

    try:
        pid = int(paths.WORKER_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, owned by someone else
    return True


def preflight(vault_dir: Path) -> None:
    """Refuse -- before reading or writing anything -- when the vault is in a
    state `sync` must not apply from (spec 2026-09-26 §6 A5): a merge, rebase
    or cherry-pick in progress, unresolved conflicts in the index, or a running
    ingest worker. Each message says what to do next."""
    git_dir = Path(_git(vault_dir, ["rev-parse", "--absolute-git-dir"]).strip())
    for marker, what in (
        ("MERGE_HEAD", "a merge"),
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ):
        if (git_dir / marker).exists():
            raise VaultSyncError(
                f"{what} is in progress in this vault -- finish it in Obsidian "
                "(or git) first, then re-run `vault sync`"
            )
    unmerged = _git(vault_dir, ["diff", "--name-only", "--diff-filter=U"]).split()
    if unmerged:
        raise VaultSyncError(
            f"unresolved conflicts in {len(unmerged)} file(s) ({', '.join(unmerged[:5])}"
            f"{', ...' if len(unmerged) > 5 else ''}) -- resolve them first"
        )
    if _worker_running():
        raise VaultSyncError(
            "the ingest worker is running for this vault -- wait for it to finish "
            "(`artmind ingest job-status`), then re-run `vault sync`"
        )
```

In `sync()`, directly after `head = head_sha(vault_dir)` add:

```python
    preflight(vault_dir)
```

(It runs for every mode, including `--dryRun` and `--bootstrapSynced`: a dry run that reports a plan it would then refuse to apply is misleading.)

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_vault_sync.py -v`
Expected: all PASS except the two commit-related tests (`test_sync_commits_regenerated_tables_to_the_vaults_git_history`, `test_sync_raises_if_committing_regenerated_tables_fails`), which are replaced in Task 9 — they should still pass at this point too; if not, note why.

- [ ] **Step 6: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault sync): refuse mid-merge, with conflicts, or while the worker runs"
```

---

### Task 9: `vault sync` applies committed content only, and never commits

Today classification uses `git diff base..head` but replay reads `KG_DIR/<domain>/<docdir>` from disk, and track B imports structured text from disk — so uncommitted or half-written content can be applied and stamped as `head`. This task materialises every input from `git archive <head>` into a scratch directory, and deletes the commit of regenerated tables.

**Files:**
- Modify: `artmind/vault_sync.py` (`sync`, new `_materialize`, new `_carry_sidecar`)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_sync.py`:

```python
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
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain: {"name": domain})
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
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain: {"name": domain})
    monkeypatch.setattr(t2g, "table_to_graph", _fake_table_to_graph)
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert vs.head_sha(repo) == head_before
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert staged.strip() == ""
    assert (staged_dir / "document.json").is_file(), "regenerated output is left on disk"
```

- [ ] **Step 2: Delete the obsolete tests and adjust two existing ones**

Delete `test_sync_commits_regenerated_tables_to_the_vaults_git_history` and `test_sync_raises_if_committing_regenerated_tables_fails` (their contract is replaced by `test_sync_never_commits`).

In `test_sync_regenerates_a_table_from_structured_text_diff`, delete the comment block and the line `monkeypatch.setattr("artmind.vault_git.commit_paths", lambda *a, **k: True)`, and delete the `monkeypatch.setattr(t2g, "stage_dir", ...)` line (sync no longer calls `stage_dir`).

In `test_sync_leaves_cursor_untouched_when_track_a_fails_after_track_b_succeeded`, delete its `monkeypatch.setattr(t2g, "stage_dir", ...)` line too.

In `test_sync_replays_an_added_document_folder_and_advances_the_cursor`, the replay now receives a scratch path, so replace

```python
    assert calls["write_to_neo4j"] == [(str(kg_dir / "banking" / "doc1"), "banking")]
```

with

```python
    assert [(Path(p).name, d) for p, d in calls["write_to_neo4j"]] == [("doc1", "banking")]
    assert not calls["write_to_neo4j"][0][0].startswith(str(kg_dir)), "replays from a scratch copy of HEAD"
```

and add `from pathlib import Path` to the file's imports.

- [ ] **Step 3: Run to verify the new tests fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -v`
Expected: `test_sync_replays_the_committed_observations_not_the_working_tree`, `test_sync_drops_the_sidecar_when_chunks_differ_from_head`, `test_sync_imports_structured_text_from_the_committed_version`, `test_sync_never_commits`, and the adjusted `test_sync_replays_an_added_document_folder_and_advances_the_cursor` FAIL. `test_sync_carries_the_embedding_sidecar_when_chunks_match_head` may PASS already (it reads the live dir today) — that's fine.

- [ ] **Step 4: Implement the helpers**

At the top of `artmind/vault_sync.py`, extend imports:

```python
import io
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
```

Add after `_show`:

```python
def _materialize(vault_dir: Path, rev: str, relpaths: list[str], dest: Path) -> None:
    """Extract `relpaths` (files or folders, relative to the vault root) as
    they stand at `rev` into `dest`, keeping their vault-relative layout.

    This is what makes `sync` apply committed content only (spec 2026-09-26
    §6 A1): the working tree may hold uncommitted edits, a half-written
    staging folder, or a merge in progress, none of which is `rev`.
    `git archive` reads blobs straight from the object store, as bytes.
    """
    if not relpaths:
        return
    proc = subprocess.run(
        ["git", "archive", "--format=tar", rev, "--", *relpaths],
        cwd=vault_dir, capture_output=True,
    )
    if proc.returncode != 0:
        raise VaultSyncError(
            f"git archive {rev} failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")


def _carry_sidecar(live_dir: Path, snap_dir: Path) -> None:
    """Copy the gitignored embedding sidecar from the live staging folder into
    its HEAD snapshot -- only when the live `chunks.json` is byte-identical to
    the committed one. The sidecar's vectors belong to the on-disk chunks;
    attaching them to different committed chunks would pair a chunk id with
    the wrong vector. Without it, the chunk embed sweep recomputes locally."""
    from artmind.ingest import EMBEDDING_SIDECAR

    sidecar = live_dir / EMBEDDING_SIDECAR
    live_chunks = live_dir / "chunks.json"
    snap_chunks = snap_dir / "chunks.json"
    if not (sidecar.is_file() and live_chunks.is_file() and snap_chunks.is_file()):
        return
    if live_chunks.read_bytes() == snap_chunks.read_bytes():
        shutil.copy2(sidecar, snap_dir / EMBEDDING_SIDECAR)
```

- [ ] **Step 5: Rewire `sync()`'s apply section**

In `sync()`, replace everything from the `from artmind import ingest` import block down to (and including) the `# ── commit track B's freshly regenerated table__* folders` block — i.e. the imports, `all_keys`/`regenerated_dirs`, track B, track A, the union rebuild, the embed sweeps, and the commit block — with:

```python
    from artmind import ingest
    from artmind.structured import registry as structured_registry
    from artmind.structured.text_export import MANIFEST_NAME, import_structured_text
    from artmind.table2graph import find_mappings, table_to_graph, _rebuild_in_batches
    from artmind.temporal import load_schema
    from paths import KG_DIR, STRUCTURED_TEXT_DIR

    all_keys: set[tuple[str, str, str]] = set()

    with tempfile.TemporaryDirectory(prefix="artmind-sync-") as scratch_str:
        scratch = Path(scratch_str)

        # ── track B: regenerate (§5 step 2) -- BEFORE track A, so its output
        #    (freshly regenerated table__* JSON in the working tree) can never
        #    itself be mistaken for a track-A input in this same run (the
        #    diff_range above is already fixed from `plan`). Inputs come from
        #    `head`, never the live structured_text dir.
        if plan.regenerate_tables:
            st_rel = STRUCTURED_TEXT_DIR.relative_to(vault_dir)
            _materialize(
                vault_dir, head,
                [str(st_rel / MANIFEST_NAME)]
                + [str(st_rel / domain / f"{table_name}.csv") for domain, table_name in plan.regenerate_tables],
                scratch,
            )
            import_structured_text(scratch / st_rel, tables=plan.regenerate_tables)
            for domain, table_name in plan.regenerate_tables:
                row = structured_registry.get_table(table_name, domain=domain)
                if row is None:
                    raise VaultSyncError(f"{domain}/{table_name}: not found in the registry after restore")
                found = find_mappings(table_name, domain)
                if not found:
                    raise VaultSyncError(f"{domain}/{table_name}: no table mapping under domains/table_mappings/")
                if len(found) > 1:
                    raise VaultSyncError(f"{domain}/{table_name}: ambiguous, {len(found)} mappings match")
                schema = load_schema(domain)
                if not schema:
                    raise VaultSyncError(f"{domain}/{table_name}: no schema for domain {domain!r}")
                report = table_to_graph(row, found[0], schema=schema, embed=False, defer_rebuild=True)
                all_keys.update(tuple(k) for k in report["commit"].get("deferred_keys") or [])

        # ── track A: replay (§5 step 3), from `head` ─────────────────────────
        #    Guarded by `if`: some tests patch only STRUCTURED_TEXT_DIR, leaving
        #    KG_DIR outside `vault_dir` (same reason as the ValueError guards in
        #    the classifiers), and relative_to would raise on an empty plan.
        if plan.replay_docs:
            kg_rel = KG_DIR.relative_to(vault_dir)
            _materialize(
                vault_dir, head,
                [str(kg_rel / domain / docdir) for domain, docdir in plan.replay_docs],
                scratch,
            )
            for domain, docdir in plan.replay_docs:
                snap_dir = scratch / kg_rel / domain / docdir
                _carry_sidecar(KG_DIR / domain / docdir, snap_dir)
                summary = ingest._write_to_neo4j(snap_dir, domain, defer_rebuild=True)
                if summary is None:
                    raise VaultSyncError(f"{kg_rel / domain / docdir}: staged KG JSON at {head} could not be read")
                all_keys.update(tuple(k) for k in summary.get("deferred_keys") or [])

    for domain, doc_id in plan.retract:
        result = ingest.retract_document(doc_id, domain)
        all_keys.update(tuple(k) for k in result.get("affected_keys") or [])

    # ── the union rebuild (§5 step 4), chunked exactly like table2graph's own ─
    if all_keys:
        projection_summary = _rebuild_in_batches(sorted(all_keys))
    else:
        projection_summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0}

    # ── domain-scoped embed sweeps (§5 step 5) ──────────────────────────────
    touched_domains = sorted({k[2] for k in all_keys})
    for d in touched_domains:
        domain_keys = [k for k in all_keys if k[2] == d]
        ingest._sweep_embeddings(d, domain_keys)
        ingest._sweep_chunk_embeddings(domain=d)

    # artmind never commits (spec 2026-09-26, D1): track B's regenerated
    # table__* folders stay in the working tree. (Phase 2 gitignores them.)
```

Leave the `write_state(...)` cursor advance and the `return {...}` after it unchanged.

Note: `tests/test_vault_sync.py`'s tests patch `paths.KG_DIR`/`paths.STRUCTURED_TEXT_DIR` and this function imports them at call time (`from paths import ...` inside `sync`), so the patches apply. Do **not** hoist those imports to module level.

- [ ] **Step 6: Update the module docstring**

Append to `artmind/vault_sync.py`'s module docstring:

```
Applies committed content only: every input is materialised from the fixed
`head` via `git archive`, never read from the working tree, and `sync` never
commits (spec 2026-09-26-vault-git-transport-design.md, §6 A1, D1).
```

- [ ] **Step 7: Run the tests**

Run: `uv run --group dev pytest test/test_vault_sync.py test/test_no_git_writes.py -v`
Expected: all PASS, including the guard test for `artmind/vault_sync.py`.

- [ ] **Step 8: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "fix(vault sync): apply committed content only, and never commit

Inputs are materialised from HEAD with git archive; the embedding sidecar
is reused only when the live chunks match the committed ones. Regenerated
table__* output is left for Obsidian Git."
```

---

### Task 10: docs, skill, full suite, live check

**Files:**
- Modify: `docs/vault.md` (sections "Running alongside the Obsidian Git plugin" ≈line 308, `init --interactive` ≈line 414, config table ≈line 442)
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md:44-48`
- Modify: `artmind/cli.py` `vault_sync_cmd` docstring (≈line 3446)

- [ ] **Step 1: Rewrite the Obsidian Git section of `docs/vault.md`**

Replace the section headed `### Running alongside the Obsidian Git plugin` (up to, not including, the paragraph starting "The loop terminates by construction") with:

```markdown
### Git: Obsidian Git owns transport

artmind never commits, pulls or pushes the vault. It writes files — note
frontmatter, KG staging under `.artmind/data/kg/`, structured text under
`.artmind/data/structured_text/` — and the Obsidian Git plugin commits and
syncs them like any other change. One writer per repo means no `index.lock`
races and no rejected pushes.

Recommended plugin settings (names vary by plugin version): sync method
**merge** (not rebase — commit shas are provenance and the sync cursor),
auto commit-and-sync every 5–10 minutes, pull on startup. Mobile uses the same
settings and never runs artmind.

After the plugin pulls changes made on another machine, run
`artmind vault sync` to apply them to this machine's Neo4j and structured
store. It applies committed content only, and refuses while a merge is in
progress, while conflicts are unresolved, or while the ingest worker is
running. Design: `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`.
```

In the "The loop terminates by construction" paragraph, change "artmind writes frontmatter → someone commits it" to "artmind writes frontmatter → Obsidian Git commits it".

In the `init --interactive` paragraph, change "(URI, username, password, database), whether to push after every ingest (`ARTMIND_VAULT_GIT_PUSH`), and a git remote URL" to "(URI, username, password, database) and a git remote URL (configured for Obsidian Git to push to; artmind itself never pushes)".

In the config table row `| **Vault** — ... | ARTMIND_KG_NEO4J_*, ARTMIND_VAULT_GIT_PUSH |`, drop `, ARTMIND_VAULT_GIT_PUSH`.

Check for leftovers: `grep -n "commit_paths\|GIT_PUSH\|artmind makes a git commit\|git rm" docs/vault.md` — reword any hit the same way.

- [ ] **Step 2: Update the ingestion-helper skill**

In `artmind/skills/artmind-ingestion-helper/SKILL.md`, change "are written into the file itself, and artmind makes a git commit in the vault recording it." to "are written into the file itself; the Obsidian Git plugin commits the change (artmind never commits)."

- [ ] **Step 3: Update the `vault sync` command docstring**

In `artmind/cli.py` `vault_sync_cmd`, append to the docstring:

```
    Applies committed content only (never the working tree) and never
    commits. Refuses while a merge/rebase is in progress, while conflicts are
    unresolved, or while the ingest worker is running.
```

- [ ] **Step 4: Full suite**

Run: `just dev-test`
Expected: all PASS. If anything outside the files touched here fails, read it before changing it — it may be another caller of a deleted function (`grep -rn "commit_paths\|maybe_push\|remove_paths\|git_committed" artmind test`).

- [ ] **Step 5: Push skill/docs to the run folder and restart daemons**

```bash
just dev-install
```

(Stops `serve`/worker, reinstalls editable, runs `artmind init` so the edited skill reaches the run folder.)

- [ ] **Step 6: Live check against a throwaway vault**

Real Neo4j is AuraDB, reached through artmind's own config; do not probe localhost. Use a scratch copy, never the user's vault:

```bash
SCRATCH=$(mktemp -d) && git clone --no-hardlinks ~/path/to/user/vault "$SCRATCH/v" && cd "$SCRATCH/v"
```

(Ask the user for the vault path; do not guess.) Then:

```bash
ARTMIND_NO_PROXY=1 artmind vault sync --dryRun
git rev-parse HEAD
```

Expected: a JSON plan, and HEAD unchanged afterwards. Then start a merge conflict (`git checkout -b x && echo a >> README.md && git commit -qam a && git checkout - && echo b >> README.md && git commit -qam b && git merge x`) and re-run `ARTMIND_NO_PROXY=1 artmind vault sync --dryRun` — expected: `Error: a merge is in progress in this vault ...`. Clean up with `git merge --abort` and `rm -rf "$SCRATCH"`.

- [ ] **Step 7: Commit**

```bash
git add docs/vault.md artmind/skills/artmind-ingestion-helper/SKILL.md artmind/cli.py
git commit -m "docs: Obsidian Git owns vault transport; vault sync applies committed content"
```

---

## Self-review notes

- Spec coverage (phase 1 = R1, A1, A5): R1 call-site table → Tasks 2 (ingest.py), 3 (cli.py, worker.py), 4 (pipeline.py), 5 (archive.py ×2), 9 (vault_sync.py commit), 7 (`vault_git` shrink), 1 + 7 + 8 (argv `run_command`), 6 (`ARTMIND_VAULT_GIT_PUSH` no longer offered). A1 → Task 9 (KG via `git archive`, structured text via `src_dir`, sidecar rule). A5 → Task 8 (merge/rebase/cherry-pick markers, unmerged paths, worker). Deferred by design: `vault doctor` reporting a set `ARTMIND_VAULT_GIT_PUSH` (phase 2, R8); `.gitignore` for `table__*` (phase 2, R4).
- The interim `table__*`-in-working-tree state is called out in "Context" and in Task 9's closing comment.
