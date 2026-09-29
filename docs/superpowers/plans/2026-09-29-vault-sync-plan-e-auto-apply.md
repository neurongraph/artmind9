# Vault sync — Plan E (opt-in automatic apply) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With `ARTMIND_VAULT_AUTO_APPLY=1`, a running `artmind serve` applies what Obsidian Git pulls — the same full `vault sync` a person would run — once `HEAD` has settled, the preflight passes, a store is behind and every bookmark is an ancestor of `HEAD`; it never runs two applies at once, never crashes `serve`, backs off after a failure, and `vault status` says what it did.

**Architecture:** Spec [2026-09-26-vault-git-transport-design.md](../specs/2026-09-26-vault-git-transport-design.md) §14 A8 (amending D4 and §12 item 6). A new stdlib-only `artmind/vault_lock.py` holds a per-vault `flock` the way the ingest worker's pid file does (`worker_pid.live_pid`); `vault_sync.sync()` takes `.artmind/logs/vault-sync.lock` for every applying run, so a manual sync and an automatic one exclude each other. A new `artmind/auto_apply.py` holds the loop: `tick()` is one poll — a function of git state, the graph's bookmarks, the loop's `LoopState` and an injected time — `run()` repeats it with an injected clock and sleep, `Runner` owns the daemon thread and the per-vault `auto-apply.lock`, and the apply itself is `vault_sync.sync(vault)` in a child process (`python -m artmind.auto_apply <vault>`). `serve` starts the `Runner` in the FastAPI lifespan; `vault status` reads the lock holder and the loop's report file `.artmind/logs/auto-apply.json`.

**Verified:** every code block in this plan was applied, in task order, by a script that parses this file, to a scratch worktree of `planC2-applied` (the code as it stands after Plans C and C2), and the full suite was run after each task — see "Verification record" at the end. The "Expected: FAIL" claims come from reverting each task's non-test files and re-running its tests.

**Tech Stack:** Python 3.14, Click (rich-click), FastAPI lifespan, `fcntl.flock`, `subprocess`, pytest (`uv run --group dev pytest test/ -q`, ~80 s, `2560 passed, 14 skipped` at `planC2-applied`), real throwaway git repos in `tmp_path`, recording fakes for the graph bookmark and the apply.

---

## Context for the implementer

Read before starting:

- `CLAUDE.md`, "Testing implications" §1 (a running `serve` daemon keeps the code it imported at start — this plan puts a writer inside it) and §3 (assert on what was applied, never on counts).
- The spec: §6 (apply: A2 bookmarks, A5 preflight), §12 item 6, §14 A8.
- `artmind/vault_sync.py`: `preflight`, `operation_in_progress`, `vault_id_or_raise`, `read_bookmarks`, `Bookmarks.effective`, `check_bookmark` (Plan B's "bookmark must be an ancestor of HEAD" refusal), `sync`, `status_report`.
- `artmind/worker.py` `_acquire_pid_file`/`_release_pid_file` and `artmind/worker_pid.py` `live_pid`: the flock pattern `vault_lock` reuses — liveness is the lock, never the pid; the file is never unlinked.
- `artmind/server.py` (`create_app`, `run_cli_args`, `_invoke_lock`) and `artmind/cli.py` `serve`.
- `test/conftest.py`: an autouse fixture swaps `graph_query.neo4j_session` for a null session and points `ARTMIND_HOME`/`HOME` at temp dirs. Every new test here also replaces `sync_state.read_graph_bookmark` with an in-memory fake, and repoints `paths.WORKER_PID_FILE`/`paths.KG_DIR`/`paths.STRUCTURED_TEXT_DIR` inside its throwaway vault.

This plan runs **after Plans C, C2 and D**. It is written against `planC2-applied`; Plan D (frontmatter slimming, `document.json` identity, `vault resolve`, a `.gitignore` block bump) was planned in parallel, and this plan stays out of its way:

- no change to `GITIGNORE_BLOCK` — every new file lives under `.artmind/logs/`, which the block already ignores;
- no change to the `vault` group docstring, frontmatter, ingest identity, the spec, or any existing test file — every test is in a new file;
- the prose anchors (Task 8) are headings and lines Plan D has no reason to touch.

**Line numbers** are those of each file on `planC2-applied`; Plan D may shift them. Every replacement quotes the exact old code — match on the text, not the number. If a quoted block no longer matches because Plan D edited the same lines, apply the same change to Plan D's version of them and say so in the task report.

Run the suite with:

```bash
uv run --group dev pytest test/ -q
```

Do **not** run `just dev-install`, `artmind serve`, `artmind init` outside `tmp_path`, or anything against a live Neo4j. The user tests live afterwards ("Live verification (user)").

Terminology:

- **Apply**: one `vault_sync.sync()` run that writes (not a dry run).
- **Settled HEAD**: the same `HEAD` on two polls in a row, with git not mid-operation.
- **Behind**: a store's bookmark (`Bookmarks.effective(store)`) is not `HEAD`.

## Decisions this plan makes (the spec left them open)

Each is argued under "Self-review → Design decisions the spec did not settle".

1. **`serve` hosts the loop, and only `serve`.** Spec A8 names it; it is the one headless, long-running daemon, and `just serve-ui`/`serve-admin-ui` (re)start it anyway. `chat-ui`/`admin-ui` do not host it. A per-vault instance lock (`.artmind/logs/auto-apply.lock`, held by `Runner`) still guarantees one loop per vault if two `serve`s run for the same vault.
2. **The apply runs in a child process**, `python -m artmind.auto_apply <vault>`, which calls `vault_sync.sync(vault)` and prints one JSON line. Not in-process: `serve` answers queries by swapping the process-wide stdout under `_invoke_lock`, so anything an apply printed could land in a query's answer; a crash or an embedding model loaded by the sweep must not take `serve` down or stay resident; and the child runs the code installed now. The decision loop itself stays in `serve`.
3. **One apply per vault: `vault_sync.sync()` itself takes `.artmind/logs/vault-sync.lock`** for every run except a dry run, so the CLI, the auto-apply child and any future caller all exclude each other. A second apply is refused with a `VaultSyncError` naming the holder's pid. Both lock files and the report are in `.artmind/logs/`, already gitignored — no `.gitignore` change (Plan D owns the block).
4. **Poll every 30 s** (`ARTMIND_VAULT_AUTO_APPLY_INTERVAL`, floor 5 s). A poll with nothing new costs two quiet `git rev-parse` calls and a few `stat`s — no `run_command` debug line, no Neo4j.
5. **Debounce:** `HEAD` must be the same on two consecutive polls, with no `MERGE_HEAD`/`rebase-merge`/`rebase-apply`/`CHERRY_PICK_HEAD` and no `index.lock`; any of those restarts the wait. This is not what makes an apply correct (`vault sync` reads committed objects at one fixed `HEAD`); it only avoids applying a commit the next pull is about to supersede.
6. **Backoff:** a failed apply — or an unreachable graph — is retried after 2× the interval, doubling, capped at 30 min; a new `HEAD` is tried at once (the pull may be the fix). Preflight refusals (worker running, conflicts) are *waiting*, not failures: retried every poll, no backoff, no Neo4j read. A store with no bookmark, or one not an ancestor of `HEAD`, is *blocked*: parked until `HEAD` moves or 10 min pass.
7. **Idle recheck every 10 min:** a `HEAD` found current is trusted without reading the graph for 10 minutes, then re-read once — a `session initiate` can move the graph bookmark back without moving `HEAD`.
8. **Never bootstraps, never scoped:** the child calls `sync(vault)` with no options — both stores, no `--domain`, so the bookmarks advance; a store with no bookmark waits for a person.
9. **Visibility:** the loop writes `.artmind/logs/auto-apply.json` (atomically) after every poll; `vault status` reports `running`/`pid` from the instance lock's holder, `configured` from its own environment, and the loop's last check and last apply (`sync.auto_apply` in `--compact`, one `Auto-apply:` line otherwise). The last apply survives a `serve` restart.
10. **Opt-in lives in the vault's `.artmind/config.env`** (recommended; gitignored, per machine), or `~/.artmind/config.env`/the environment. Both templates document it, commented out.
11. **Stale code:** documented in `docs/vault.md` and `docs/INSTALL.md`: `just dev-install` stops `serve` and does not restart it; with auto-apply on, restart it inside the vault after an upgrade. The child's apply always runs current code; the loop's decisions run the daemon's.

## File map

| File | Change |
|---|---|
| `artmind/vault_lock.py` | new: `HeldLock`, `LockHeld`, `lock_path`, `holder`, the two lock names (Task 1) |
| `artmind/vault_sync.py` | `sync()` holds the apply lock, body moved to `_sync()` (Task 2); `OPERATION_MARKERS`, `operation_in_progress_at` (Task 3); `status_report` carries `auto_apply` (Task 7) |
| `artmind/auto_apply.py` | new: config, `Outcome`, `LoopState`, `tick` (Task 3); `default_apply`, `child_main`, `__main__` (Task 4); status file, `run`, `Runner` (Task 5); `status`, `describe` (Task 7) |
| `artmind/server.py` | `create_app(auto_apply=None)` starts/stops a `Runner` in the lifespan (Task 6) |
| `artmind/cli.py` | `serve` builds the `Runner` (`_auto_apply_runner`) (Task 6); `vault status` prints `Auto-apply:` (Task 7); `vault sync` docstring (Task 8) |
| `artmind/setup.py`, `artmind/env.example` | the opt-in, commented out, in the vault and machine config templates (Task 8) |
| `docs/vault.md`, `docs/INSTALL.md`, `justfile`, `artmind/skills/artmind-ingestion-helper/SKILL.md`, `artmind/skills/artmind-query/SKILL.md` | prose (Task 8) |
| `test/test_vault_lock.py`, `test/test_vault_sync_lock.py`, `test/test_auto_apply.py`, `test/test_auto_apply_child.py`, `test/test_auto_apply_loop.py`, `test/test_serve_auto_apply.py`, `test/test_vault_status_auto_apply.py` | new |

No `COMMAND_GROUPS` or CLI-guide change: no command is added. `serve`'s help grows a paragraph.

---

### Task 1: `vault_lock` — a per-vault `flock` (the worker pid-file pattern)

Both locks this plan needs — one apply at a time, one loop per vault — are the same thing: an exclusive, non-blocking `flock` on a file under the vault's `.artmind/logs/`, held for as long as the work runs, with this process's pid written inside for messages. `worker._acquire_pid_file` already does exactly this for the ingest worker (including the brief retry, because `live_pid` probes by taking the lock for an instant); this task lifts it into a small reusable class. `holder()` is `worker_pid.live_pid`. The file is never unlinked, for the reason `worker._release_pid_file` gives.

`flock` belongs to the open file, not the process, so a second `HeldLock` on the same path in the *same* process is refused just like another process would be — which is what makes the refusal testable without spawning anything.

**Files:**
- Create: `artmind/vault_lock.py`
- Test: `test/test_vault_lock.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_vault_lock.py`:

```python
"""artmind.vault_lock: the per-vault flock that keeps two applies -- a
`vault sync` someone ran and `serve`'s auto-apply -- from ever running at
once, and one auto-apply loop per vault (spec 2026-09-26 §14 A8)."""
import os

import pytest

from artmind import vault_lock


def test_lock_files_live_in_the_vaults_gitignored_logs_dir(tmp_path):
    assert vault_lock.lock_path(tmp_path, vault_lock.SYNC_LOCK_NAME) == (
        tmp_path / ".artmind" / "logs" / "vault-sync.lock"
    )
    assert vault_lock.lock_path(tmp_path, vault_lock.AUTO_APPLY_LOCK_NAME) == (
        tmp_path / ".artmind" / "logs" / "auto-apply.lock"
    )


def test_acquire_creates_the_file_and_names_this_process_as_the_holder(tmp_path):
    path = tmp_path / ".artmind" / "logs" / "vault-sync.lock"
    lock = vault_lock.HeldLock(path).acquire()
    try:
        assert lock.held
        assert path.read_text() == str(os.getpid())
        assert vault_lock.holder(path) == os.getpid()
    finally:
        lock.release()
    assert vault_lock.holder(path) is None


def test_a_second_lock_on_the_same_file_is_refused_with_the_holders_pid(tmp_path):
    """flock belongs to the open file, not the process, so a second
    `HeldLock` in this same process is refused exactly like another process
    would be -- which is what lets these tests exercise the refusal."""
    path = tmp_path / "vault-sync.lock"
    first = vault_lock.HeldLock(path).acquire()
    try:
        with pytest.raises(vault_lock.LockHeld) as info:
            vault_lock.HeldLock(path).acquire()
        assert info.value.pid == os.getpid()
        assert info.value.path == path
    finally:
        first.release()


def test_release_keeps_the_file_and_the_lock_can_be_taken_again(tmp_path):
    path = tmp_path / "vault-sync.lock"
    vault_lock.HeldLock(path).acquire().release()

    assert path.exists(), "never unlinked: a lock lives on the inode"
    again = vault_lock.HeldLock(path).acquire()
    assert again.held
    again.release()


def test_a_stale_unlocked_file_is_taken_over_and_its_pid_overwritten(tmp_path):
    path = tmp_path / "vault-sync.lock"
    path.write_text("999999")

    with vault_lock.HeldLock(path):
        assert path.read_text() == str(os.getpid())


def test_the_with_block_releases_even_when_the_body_raises(tmp_path):
    path = tmp_path / "vault-sync.lock"
    with pytest.raises(RuntimeError):
        with vault_lock.HeldLock(path):
            raise RuntimeError("boom")

    assert vault_lock.holder(path) is None


def test_releasing_a_lock_never_acquired_is_a_no_op(tmp_path):
    lock = vault_lock.HeldLock(tmp_path / "x.lock")
    lock.release()
    assert not lock.held
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_lock.py -q`
Expected: collection error — `ImportError: cannot import name 'vault_lock' from 'artmind'`.

- [ ] **Step 3: Write the module**

**Create** `artmind/vault_lock.py`:

```python
"""One process at a time per vault: an exclusive `flock` held for as long
as the work runs (spec 2026-09-26 §14 A8).

Two locks use it, both files under the vault's gitignored `.artmind/logs/`:

- `vault-sync.lock` -- held by every applying `vault sync` run
  (`vault_sync.sync`), whether a person started it or `serve`'s auto-apply
  did, so two applies never write the same graph keys at once;
- `auto-apply.lock` -- held by the one process running the auto-apply loop
  for this vault (`auto_apply.Runner`), for as long as that loop runs.

The same pattern as the ingest worker's pid file (`worker._acquire_pid_file`,
`worker_pid.live_pid`): liveness is the lock, never the pid number, and the
file is never unlinked -- a lock lives on the inode, so unlinking would let a
newcomer lock a fresh inode at the same path while an old holder still held
the orphaned one. The pid written inside is only for messages. Stdlib only.
"""
from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

from artmind.worker_pid import live_pid

SYNC_LOCK_NAME = "vault-sync.lock"
AUTO_APPLY_LOCK_NAME = "auto-apply.lock"

# `live_pid` briefly takes the same lock (LOCK_EX, then releases it) to probe
# whether anyone holds it, so a single LOCK_NB attempt can land inside that
# window and see a transient "held" -- retry briefly before believing it
# (same numbers as the worker's own pid file).
_ATTEMPTS = 6
_RETRY_DELAY_S = 0.05


class LockHeld(Exception):
    """Another open file holds the lock. `pid` is what its holder wrote into
    the file, or None when that is missing or unreadable."""

    def __init__(self, path: Path, pid: int | None):
        self.path = Path(path)
        self.pid = pid
        super().__init__(f"{path} is held by pid {pid if pid is not None else '?'}")


def lock_path(vault_dir: Path, name: str) -> Path:
    """Where lock `name` lives for `vault_dir`: its `.artmind/logs/`,
    machine-local and gitignored by artmind's `.gitignore` block."""
    from artmind.vault import VaultLayout

    return VaultLayout(Path(vault_dir)).logs_dir / name


def holder(path: Path) -> int | None:
    """The pid holding the lock at `path`, else None (`worker_pid.live_pid`)."""
    return live_pid(Path(path))


class HeldLock:
    """An exclusive, non-blocking `flock` on one file, held from `acquire()`
    until `release()` (or for a `with` block). A second `HeldLock` on the
    same path -- in another process, or in this one: `flock` belongs to the
    open file, not the process -- raises `LockHeld`."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> "HeldLock":
        if self._fd is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(self.path, "a+")
        for attempt in range(_ATTEMPTS):
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if attempt == _ATTEMPTS - 1:
                    fd.seek(0)
                    try:
                        pid = int(fd.read().strip())
                    except (OSError, ValueError, OverflowError):
                        pid = None
                    fd.close()
                    raise LockHeld(self.path, pid) from None
                time.sleep(_RETRY_DELAY_S)
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
        self._fd = fd  # hold the lock until release(): closing the fd drops it
        return self

    def release(self) -> None:
        """Drop the lock. Never unlinks the file (see the module docstring)."""
        if self._fd is not None:
            self._fd.close()
            self._fd = None

    def __enter__(self) -> "HeldLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
```

- [ ] **Step 4: Run the tests, then the suite**

Run: `uv run --group dev pytest test/test_vault_lock.py -q` → `7 passed`.
Run: `uv run --group dev pytest test/ -q` → all pass (2568 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_lock.py test/test_vault_lock.py
git commit -m "feat(vault-sync): vault_lock -- a per-vault flock for applies and the auto-apply loop

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: an applying `vault sync` holds the vault's apply lock

Decision 3. `sync()` keeps its signature and docstring (plus a paragraph); its body moves unchanged into `_sync()`. A dry run writes nothing, so it runs unlocked. Otherwise `sync()` acquires `.artmind/logs/vault-sync.lock`, runs `_sync()`, and releases in a `finally` — so an exception (every existing failure path propagates unchanged) never leaves the lock held. A refusal is a `VaultSyncError`, so `vault sync` on the CLI prints it through its existing `ClickException` path and the auto-apply child reports it like any other refusal.

The lock is taken inside `sync()` rather than in the CLI command so that the CLI, the auto-apply child (Task 4) and any later caller all exclude each other without having to remember to.

**Files:**
- Modify: `artmind/vault_sync.py`
- Test: `test/test_vault_sync_lock.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_vault_sync_lock.py`:

```python
"""`vault sync` holds the vault's apply lock (spec 2026-09-26 §14 A8): a
second apply -- one someone started, or `serve`'s auto-apply -- is refused
before it reads or writes anything, never interleaved with the first."""
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import artmind.vault_sync as vs
from artmind import vault_lock

VAULT_ID = "vault-under-test"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A git repo that is a vault (`vault_id` in `.artmind/vault.yaml`), with
    its KG staging and structured text inside it, the worker pid file out of
    the way, and the graph bookmark in memory."""
    import paths
    from artmind import sync_state

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text(f'vault_id: "{VAULT_ID}"\n')
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / ".artmind" / "data" / "kg")
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", tmp_path / ".artmind" / "data" / "structured_text")
    bookmarks: dict[str, str] = {}
    monkeypatch.setattr(sync_state, "read_graph_bookmark", lambda vault_id, *, timeout=None: bookmarks.get(vault_id))
    monkeypatch.setattr(sync_state, "write_graph_bookmark", lambda vault_id, commit: bookmarks.__setitem__(vault_id, commit))
    monkeypatch.setattr(sync_state, "read_document_fingerprints", lambda doc_ids, *, timeout=None: {})
    return tmp_path


def _commit_doc(repo, docdir, doc_id):
    folder = repo / ".artmind" / "data" / "kg" / "banking" / docdir
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(f'{{"id": "{doc_id}"}}')
    (folder / "observations.json").write_text("[]")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"add {docdir}")


def _record_graph_writes(monkeypatch, *, fail=False, on_write=None):
    """The graph-touching seams `sync()` calls, recorded: which staging
    folders reached `_write_to_neo4j` is what an apply applied."""
    import artmind.ingest as ing
    import artmind.table2graph as t2g

    written: list[tuple[str, str]] = []

    def _write(doc_kg_dir, domain, defer_rebuild=False):
        if on_write is not None:
            on_write()
        if fail:
            raise RuntimeError("neo4j went away mid-apply")
        written.append((Path(doc_kg_dir).name, domain))
        return {"deferred_keys": [], "unembedded_chunk_ids": []}

    monkeypatch.setattr(ing, "_write_to_neo4j", _write)
    monkeypatch.setattr(ing, "retract_document", lambda doc_id, domain: {"affected_keys": []})
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: 0)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda **kw: 0)
    return written


def _sync_lock(repo):
    return vault_lock.lock_path(repo, vault_lock.SYNC_LOCK_NAME)


def test_sync_is_refused_while_another_apply_holds_the_vault_lock(repo, monkeypatch):
    _commit_doc(repo, "doc1", "docid-1")
    written = _record_graph_writes(monkeypatch)

    with vault_lock.HeldLock(_sync_lock(repo)):
        with pytest.raises(vs.VaultSyncError, match="already applying to this vault") as info:
            vs.sync(repo, bootstrap_empty=True)

    assert f"pid {os.getpid()}" in str(info.value)
    assert written == [], "refused before writing anything"
    assert vs.read_bookmarks(repo, vault_id=VAULT_ID).graph is None
    assert vs.read_bookmarks(repo, vault_id=VAULT_ID).structured is None


def test_sync_holds_the_vault_lock_while_it_applies_and_releases_it_after(repo, monkeypatch):
    _commit_doc(repo, "doc1", "docid-1")
    holders: list[int | None] = []
    written = _record_graph_writes(monkeypatch, on_write=lambda: holders.append(vault_lock.holder(_sync_lock(repo))))

    vs.sync(repo, bootstrap_empty=True)

    assert written == [("doc1", "banking")]
    assert holders == [os.getpid()], "held while the graph was written"
    assert vault_lock.holder(_sync_lock(repo)) is None


def test_a_failed_sync_releases_the_vault_lock_so_the_next_run_can_apply(repo, monkeypatch):
    _commit_doc(repo, "doc1", "docid-1")
    _record_graph_writes(monkeypatch, fail=True)
    with pytest.raises(RuntimeError, match="went away"):
        vs.sync(repo, bootstrap_empty=True)
    assert vault_lock.holder(_sync_lock(repo)) is None

    written = _record_graph_writes(monkeypatch)
    vs.sync(repo, bootstrap_empty=True)

    assert written == [("doc1", "banking")]


def test_a_dry_run_does_not_need_the_vault_lock(repo, monkeypatch):
    _commit_doc(repo, "doc1", "docid-1")
    written = _record_graph_writes(monkeypatch)

    with vault_lock.HeldLock(_sync_lock(repo)):
        report = vs.sync(repo, bootstrap_empty=True, dry_run=True)

    assert report["dry_run"] is True
    assert written == []


def test_vault_sync_cli_reports_the_running_apply(repo, monkeypatch):
    from artmind.cli import cli

    _commit_doc(repo, "doc1", "docid-1")
    written = _record_graph_writes(monkeypatch)
    monkeypatch.chdir(repo)

    with vault_lock.HeldLock(_sync_lock(repo)):
        result = CliRunner().invoke(cli, ["vault", "sync", "--bootstrapEmpty"])

    assert result.exit_code != 0
    assert "already applying to this vault" in result.output
    assert written == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync_lock.py -q`
Expected: `3 failed, 2 passed` — the refusal, the held-while-applying and the CLI tests fail (nothing takes the lock yet); `test_a_failed_sync_releases_the_vault_lock_so_the_next_run_can_apply` and `test_a_dry_run_does_not_need_the_vault_lock` pass on arrival and guard the release and the dry-run exemption.

- [ ] **Step 3: Take the lock in `sync()`**

**Replace in** `artmind/vault_sync.py` (lines 1286-1288):

```python
    exception propagates unchanged, leaving them untouched, so the next
    invocation recomputes the identical ranges. A `--domain`-scoped run never
    advances a bookmark."""
```

**with:**

```python
    exception propagates unchanged, leaving them untouched, so the next
    invocation recomputes the identical ranges. A `--domain`-scoped run never
    advances a bookmark.

    One apply at a time per vault (spec §14 A8): every run but a dry run
    holds the vault's apply lock (`vault_lock`, `.artmind/logs/vault-sync.lock`)
    from start to finish, so a second one -- a `vault sync` someone started,
    or `serve`'s auto-apply -- is refused, never interleaved."""
    run = dict(
        domains=domains, bootstrap_empty=bootstrap_empty, bootstrap_synced=bootstrap_synced,
        dry_run=dry_run, store=store,
    )
    if dry_run:
        return _sync(vault_dir, **run)
    from artmind import vault_lock

    lock = vault_lock.HeldLock(vault_lock.lock_path(vault_dir, vault_lock.SYNC_LOCK_NAME))
    try:
        lock.acquire()
    except vault_lock.LockHeld as e:
        raise VaultSyncError(
            f"another `vault sync` is already applying to this vault (pid {e.pid or '?'}) -- "
            "one you started, or `artmind serve`'s auto-apply; wait for it to finish, then "
            "re-run `vault sync`"
        ) from None
    try:
        return _sync(vault_dir, **run)
    finally:
        lock.release()


def _sync(
    vault_dir: Path,
    *,
    domains: list[str] | None,
    bootstrap_empty: bool,
    bootstrap_synced: bool,
    dry_run: bool,
    store: str | None,
) -> dict:
    """`sync`'s body: run with the vault's apply lock held, or for a dry run."""
```

- [ ] **Step 4: Run the tests, then the suite**

Run: `uv run --group dev pytest test/test_vault_sync_lock.py test/test_vault_sync.py test/test_vault_cli.py -q` → all pass.
Run: `uv run --group dev pytest test/ -q` → all pass (2573 passed). Existing `test_vault_sync.py` tests now create `.artmind/logs/vault-sync.lock` inside their throwaway repos; nothing they classify is under `logs/`, so none of them changes.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync_lock.py
git commit -m "feat(vault-sync): an applying vault sync holds the vault's apply lock; a second one is refused

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `auto_apply.tick` — one poll, as a function

Decisions 4–8. `tick(vault_dir, state, *, now, interval, apply)` decides and applies at most once; it never sleeps and never starts a thread, so tests drive it poll by poll with a made-up `now` and a recording `apply`. In order, stopping at the first reason not to go on:

1. `git rev-parse --verify -q HEAD` (quiet — `_git_quiet` bypasses `run_command`'s per-call debug log, since this runs every 30 s forever) → `no_commits`.
2. `git rev-parse --absolute-git-dir`, then `git_busy`: a merge/rebase/cherry-pick marker or `index.lock` → `busy`, and the settle wait restarts.
3. `HEAD` differs from the previous poll → `settling`.
4. `HEAD` already found current or blocked less than `RECHECK_S` (10 min) ago → that outcome again, no graph read.
5. The last apply of this `HEAD` failed and its retry is not due → `backoff`.
6. `vault_sync.preflight` + `vault_id_or_raise` → `waiting` on a refusal (no backoff).
7. One bookmark read (`read_bookmarks`, 2 s connect cap). Unreachable → `failed` (backs off).
8. Per store: no bookmark → `blocked` (auto-apply never bootstraps); bookmark ≠ `HEAD` and not an ancestor (`check_bookmark`) → `blocked`.
9. Nothing behind → `current`. Otherwise `apply(vault_dir)` → `applied` (and `HEAD` becomes idle-current), or any exception → `failed`, `failures += 1`, `next_attempt_at = now + backoff_s(failures)`; a different `HEAD` resets the count.

`git_busy` needs the operation markers `vault_sync.operation_in_progress` already checks, without its extra `git` call and debug log, so they become a module constant `OPERATION_MARKERS` and a helper `operation_in_progress_at(git_dir)`; `operation_in_progress` keeps its behaviour.

The last test drives `tick` with the real `vault_sync.sync` as `apply` (the graph seams recorded, as in `test_vault_sync.py`) and asserts the pulled document folder is what reached `_write_to_neo4j`, and that both bookmarks moved to `HEAD`.

**Files:**
- Modify: `artmind/vault_sync.py`
- Create: `artmind/auto_apply.py`
- Test: `test/test_auto_apply.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_auto_apply.py`:

```python
"""Opt-in automatic apply (spec 2026-09-26 §14 A8): `tick()` is one poll of
the loop `artmind serve` runs with `ARTMIND_VAULT_AUTO_APPLY=1`. Driven here
with an injected clock and apply -- no thread, no timer -- against real
throwaway git repos, with the graph's bookmark held in memory."""
import fcntl
import subprocess
from pathlib import Path

import pytest

import artmind.vault_sync as vs
from artmind import auto_apply
from artmind.vault import VaultLayout, write_state

VAULT_ID = "vault-under-test"
INTERVAL = 30.0


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit_all(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


class _Graph:
    """The graph's bookmark, in memory, counting reads -- a poll that reads
    the graph is one that could not decide from git alone."""

    def __init__(self):
        self.bookmarks: dict[str, str] = {}
        self.reads = 0
        self.unreachable = False

    def read_graph_bookmark(self, vault_id, *, timeout=None):
        self.reads += 1
        if self.unreachable:
            raise OSError("connection refused")
        return self.bookmarks.get(vault_id)

    def write_graph_bookmark(self, vault_id, commit):
        self.bookmarks[vault_id] = commit


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    import paths

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text(f'vault_id: "{VAULT_ID}"\n')
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / ".artmind" / "data" / "kg")
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", tmp_path / ".artmind" / "data" / "structured_text")
    return tmp_path


@pytest.fixture()
def graph(monkeypatch):
    from artmind import sync_state

    fake = _Graph()
    monkeypatch.setattr(sync_state, "read_graph_bookmark", fake.read_graph_bookmark)
    monkeypatch.setattr(sync_state, "write_graph_bookmark", fake.write_graph_bookmark)
    monkeypatch.setattr(sync_state, "read_document_fingerprints", lambda doc_ids, *, timeout=None: {})
    return fake


def _write_doc(repo, docdir, doc_id):
    folder = repo / ".artmind" / "data" / "kg" / "banking" / docdir
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(f'{{"id": "{doc_id}"}}')
    (folder / "observations.json").write_text("[]")


def _bookmark_both(repo, graph, commit):
    graph.bookmarks[VAULT_ID] = commit
    write_state(VaultLayout(repo), {vs.STRUCTURED_BOOKMARK_KEY: commit})


class _Apply:
    """A stand-in for `default_apply`: records which vault it was asked to
    apply, and answers like `vault sync` -- or fails."""

    def __init__(self, *, error=None, head=None):
        self.calls: list[Path] = []
        self.error = error
        self.head = head

    def __call__(self, vault_dir):
        self.calls.append(Path(vault_dir))
        if self.error is not None:
            raise self.error
        return {"head": self.head or _git(vault_dir, "rev-parse", "HEAD"), "replayed": 1}


def _behind_vault(repo, graph):
    """Both stores bookmarked at one commit, and a newer HEAD with a doc."""
    (repo / "a.txt").write_text("x")
    base = _commit_all(repo, "base")
    _bookmark_both(repo, graph, base)
    _write_doc(repo, "doc1", "docid-1")
    head = _commit_all(repo, "pulled doc1")
    return base, head


def _poll(repo, state, now, apply):
    return auto_apply.tick(repo, state, now=now, interval=INTERVAL, apply=apply)


# ── configuration ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_enabled_by_a_truthy_value(value):
    assert auto_apply.enabled({"ARTMIND_VAULT_AUTO_APPLY": value})


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false"])
def test_disabled_by_default_and_by_a_falsy_value(value):
    assert not auto_apply.enabled({"ARTMIND_VAULT_AUTO_APPLY": value})
    assert not auto_apply.enabled({})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 30.0), ("", 30.0), ("120", 120.0), ("1", 5.0), ("abc", 30.0), ("nan", 30.0)],
)
def test_interval_defaults_and_has_a_floor(raw, expected):
    env = {} if raw is None else {"ARTMIND_VAULT_AUTO_APPLY_INTERVAL": raw}
    assert auto_apply.interval_s(env) == expected


def test_backoff_doubles_from_twice_the_interval_and_is_capped():
    assert [auto_apply.backoff_s(n, 30.0) for n in range(1, 9)] == [
        60.0, 120.0, 240.0, 480.0, 960.0, 1800.0, 1800.0, 1800.0,
    ]


# ── tick: when it applies ────────────────────────────────────────────────────


def test_a_new_head_is_only_applied_once_it_is_the_same_on_two_polls(repo, graph):
    _, head = _behind_vault(repo, graph)
    apply = _Apply()
    state = auto_apply.LoopState()

    first = _poll(repo, state, 1000.0, apply)
    assert (first.kind, first.head) == ("settling", head)
    assert apply.calls == []
    assert graph.reads == 0, "a moving HEAD costs no graph read"

    second = _poll(repo, state, 1030.0, apply)
    assert (second.kind, second.head) == ("applied", head)
    assert apply.calls == [repo], "the whole vault, unscoped"
    assert second.result == {"head": head, "replayed": 1}


def test_a_head_that_moves_between_polls_restarts_the_wait(repo, graph):
    _behind_vault(repo, graph)
    apply = _Apply()
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)
    _write_doc(repo, "doc2", "docid-2")
    newer = _commit_all(repo, "pulled doc2")

    moved = _poll(repo, state, 1030.0, apply)

    assert (moved.kind, moved.head) == ("settling", newer)
    assert apply.calls == []
    assert _poll(repo, state, 1060.0, apply).kind == "applied"
    assert apply.calls == [repo]


def test_a_current_vault_is_not_applied_and_not_reread_until_the_recheck(repo, graph):
    (repo / "a.txt").write_text("x")
    head = _commit_all(repo, "base")
    _bookmark_both(repo, graph, head)
    apply = _Apply()
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)

    assert _poll(repo, state, 1030.0, apply).kind == "current"
    reads = graph.reads
    assert _poll(repo, state, 1060.0, apply).kind == "current"
    assert graph.reads == reads, "a quiet vault costs no graph read"
    assert _poll(repo, state, 1030.0 + auto_apply.RECHECK_S, apply).kind == "current"
    assert graph.reads == reads + 1, "re-read once the recheck is due"
    assert apply.calls == []


def test_only_the_structured_store_behind_still_applies(repo, graph):
    base, head = _behind_vault(repo, graph)
    graph.bookmarks[VAULT_ID] = head
    apply = _Apply()
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)

    assert _poll(repo, state, 1030.0, apply).kind == "applied"
    assert apply.calls == [repo]


def test_after_an_apply_the_same_head_is_current(repo, graph):
    _, head = _behind_vault(repo, graph)
    apply = _Apply()
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)
    _poll(repo, state, 1030.0, apply)

    after = _poll(repo, state, 1060.0, apply)

    assert (after.kind, after.head) == ("current", head)
    assert apply.calls == [repo]


# ── tick: when it waits ──────────────────────────────────────────────────────


def test_a_repo_with_no_commits_waits(repo, graph):
    outcome = _poll(repo, auto_apply.LoopState(), 1000.0, _Apply())
    assert outcome.kind == "no_commits"


def test_a_merge_in_progress_is_busy_and_restarts_the_settle_wait(repo, graph):
    _behind_vault(repo, graph)
    apply = _Apply()
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)
    merge_head = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "MERGE_HEAD"
    merge_head.write_text(_git(repo, "rev-parse", "HEAD") + "\n")

    busy = _poll(repo, state, 1030.0, apply)
    assert (busy.kind, busy.reason) == ("busy", "a merge is in progress")
    merge_head.unlink()

    assert _poll(repo, state, 1060.0, apply).kind == "settling", "the merge concluding moves nothing, but the wait restarts"
    assert _poll(repo, state, 1090.0, apply).kind == "applied"
    assert apply.calls == [repo]


def test_an_index_lock_is_busy(repo, graph):
    _behind_vault(repo, graph)
    (Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "index.lock").write_text("")
    apply = _Apply()
    state = auto_apply.LoopState()

    outcomes = [_poll(repo, state, t, apply).kind for t in (1000.0, 1030.0)]

    assert outcomes == ["busy", "busy"]
    assert apply.calls == []


def test_a_running_ingest_worker_makes_it_wait_without_backing_off(repo, graph):
    _behind_vault(repo, graph)
    pid_file = VaultLayout(repo).worker_pid
    pid_file.write_text("12345")
    holder = open(pid_file, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    apply = _Apply()
    state = auto_apply.LoopState()
    try:
        _poll(repo, state, 1000.0, apply)
        waiting = _poll(repo, state, 1030.0, apply)
    finally:
        holder.close()

    assert waiting.kind == "waiting"
    assert "ingest worker is running" in waiting.reason
    assert apply.calls == []
    assert _poll(repo, state, 1060.0, apply).kind == "applied", "no backoff: applied the next poll after the worker stopped"
    assert apply.calls == [repo]


def test_a_store_with_no_bookmark_is_blocked_never_bootstrapped(repo, graph):
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "base")
    apply = _Apply()
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)

    blocked = _poll(repo, state, 1030.0, apply)

    assert blocked.kind == "blocked"
    assert "--bootstrapEmpty" in blocked.reason
    reads = graph.reads
    assert _poll(repo, state, 1060.0, apply).kind == "blocked"
    assert graph.reads == reads, "parked until HEAD moves or the recheck is due"
    assert apply.calls == []


def test_a_bookmark_that_is_not_an_ancestor_of_head_is_blocked(repo, graph):
    """Plan B's refusal rule: another machine sharing the graph applied a
    commit this clone has not pulled. Applying would diff from it and
    retract that machine's documents."""
    (repo / "a.txt").write_text("x")
    base = _commit_all(repo, "base")
    _git(repo, "checkout", "-qb", "other")
    (repo / "b.txt").write_text("y")
    elsewhere = _commit_all(repo, "applied elsewhere, not pulled here")
    _git(repo, "checkout", "-q", "-")
    (repo / "c.txt").write_text("z")
    _commit_all(repo, "local")
    graph.bookmarks[VAULT_ID] = elsewhere
    write_state(VaultLayout(repo), {vs.STRUCTURED_BOOKMARK_KEY: base})
    apply = _Apply()
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)

    blocked = _poll(repo, state, 1030.0, apply)

    assert blocked.kind == "blocked"
    assert "not an ancestor of HEAD" in blocked.reason
    assert apply.calls == []


# ── tick: failures ───────────────────────────────────────────────────────────


def test_a_failed_apply_backs_off_and_retries_with_a_growing_wait(repo, graph):
    _, head = _behind_vault(repo, graph)
    apply = _Apply(error=RuntimeError("neo4j went away\nmid-apply"))
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)

    failed = _poll(repo, state, 1030.0, apply)
    assert (failed.kind, failed.head, failed.reason) == ("failed", head, "neo4j went away mid-apply")
    assert state.next_attempt_at == 1030.0 + 60.0

    assert _poll(repo, state, 1060.0, apply).kind == "backoff"
    assert len(apply.calls) == 1, "no retry before the backoff is up"
    assert _poll(repo, state, 1090.0, apply).kind == "failed"
    assert len(apply.calls) == 2
    assert state.next_attempt_at == 1090.0 + 120.0, "the wait doubles"
    assert state.failures == 2


def test_a_new_head_after_a_failure_is_tried_without_waiting_out_the_backoff(repo, graph):
    _behind_vault(repo, graph)
    apply = _Apply(error=RuntimeError("broken mapping"))
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)
    _poll(repo, state, 1030.0, apply)
    _write_doc(repo, "doc2", "docid-2")
    _commit_all(repo, "the fix, pulled")
    apply.error = None

    assert _poll(repo, state, 1040.0, apply).kind == "settling"
    assert _poll(repo, state, 1050.0, apply).kind == "applied"
    assert (state.failures, state.failed_head) == (0, None)


def test_an_unreachable_graph_is_a_failure_that_backs_off(repo, graph):
    _behind_vault(repo, graph)
    graph.unreachable = True
    apply = _Apply()
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, apply)

    failed = _poll(repo, state, 1030.0, apply)
    reads = graph.reads
    backoff = _poll(repo, state, 1060.0, apply)

    assert failed.kind == "failed"
    assert failed.reason.startswith("graph unreachable")
    assert backoff.kind == "backoff"
    assert graph.reads == reads, "the backoff spares the graph too"
    assert apply.calls == []


# ── tick with the real apply: what it applied ────────────────────────────────


def test_tick_with_vault_sync_applies_the_pulled_document_and_advances_both_bookmarks(repo, graph, monkeypatch):
    import artmind.ingest as ing
    import artmind.table2graph as t2g

    written: list[tuple[str, str]] = []

    def _write(doc_kg_dir, domain, defer_rebuild=False):
        written.append((Path(doc_kg_dir).name, domain))
        return {"deferred_keys": [], "unembedded_chunk_ids": []}

    monkeypatch.setattr(ing, "_write_to_neo4j", _write)
    monkeypatch.setattr(ing, "retract_document", lambda doc_id, domain: {"affected_keys": []})
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: 0)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda **kw: 0)
    _, head = _behind_vault(repo, graph)
    state = auto_apply.LoopState()
    _poll(repo, state, 1000.0, vs.sync)

    outcome = _poll(repo, state, 1030.0, vs.sync)

    assert outcome.kind == "applied"
    assert written == [("doc1", "banking")]
    marks = vs.read_bookmarks(repo, vault_id=VAULT_ID)
    assert (marks.graph, marks.structured) == (head, head)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_auto_apply.py -q`
Expected: collection error — `ImportError: cannot import name 'auto_apply' from 'artmind'`.

- [ ] **Step 3: Factor the operation markers out of `operation_in_progress`**

**Replace in** `artmind/vault_sync.py` (lines 81-91):

```python
def operation_in_progress(vault_dir: Path) -> str | None:
    """`"a merge"`, `"a rebase"` or `"a cherry-pick"` when one is in progress
    in the vault's repo, else None."""
    git_dir = Path(_git(vault_dir, ["rev-parse", "--absolute-git-dir"]).strip())
    for marker, what in (
        ("MERGE_HEAD", "a merge"),
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ):
        if (git_dir / marker).exists():
```

**with:**

```python
#: What git keeps in its directory while an operation is in progress, and
#: what `operation_in_progress` calls it. `auto_apply` checks the same markers
#: on every poll.
OPERATION_MARKERS = (
    ("MERGE_HEAD", "a merge"),
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
)


def operation_in_progress(vault_dir: Path) -> str | None:
    """`"a merge"`, `"a rebase"` or `"a cherry-pick"` when one is in progress
    in the vault's repo, else None."""
    git_dir = Path(_git(vault_dir, ["rev-parse", "--absolute-git-dir"]).strip())
    return operation_in_progress_at(git_dir)


def operation_in_progress_at(git_dir: Path) -> str | None:
    """`operation_in_progress` for a git directory already located."""
    for marker, what in OPERATION_MARKERS:
        if (Path(git_dir) / marker).exists():
```

- [ ] **Step 4: Create the module with `tick`**

**Create** `artmind/auto_apply.py`:

```python
"""Opt-in automatic apply (spec 2026-09-26 §14 A8, amending D4).

With `ARTMIND_VAULT_AUTO_APPLY=1`, `artmind serve` polls the vault's `HEAD`
and runs the same apply as `artmind vault sync` -- a full, unscoped sync of
both stores -- once Obsidian Git has brought new commits in, so a pull
reaches this machine's graph and structured store without anyone running
sync. It applies only when all of these hold:

- `HEAD` is settled: the same on two polls in a row, with no merge, rebase
  or cherry-pick in progress and no `index.lock` (Obsidian Git mid-pull);
- `vault sync`'s own preflight passes (no merge, no unresolved conflicts
  under `.artmind/`, no ingest worker running);
- a store is behind (its bookmark is not `HEAD`), and every store's
  bookmark is an ancestor of `HEAD` -- the refusal rule `vault sync`
  applies (Plan B). Auto-apply never bootstraps a store with no bookmark.

One poll is `tick()`: a function of the vault's git state, the graph's
bookmarks, the loop's own `LoopState` and the time it is handed, so tests
drive it with no thread and no timer.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ENV_VAR = "ARTMIND_VAULT_AUTO_APPLY"
INTERVAL_ENV_VAR = "ARTMIND_VAULT_AUTO_APPLY_INTERVAL"

#: Seconds between polls. A poll with nothing new costs two `git rev-parse`
#: calls and a few `stat`s -- no Neo4j.
DEFAULT_INTERVAL_S = 30.0
#: The floor for `ARTMIND_VAULT_AUTO_APPLY_INTERVAL`: polling faster buys
#: nothing (Obsidian Git commits and pulls on a minutes-long timer).
MIN_INTERVAL_S = 5.0
#: The longest wait between retries of a failing apply (see `backoff_s`).
MAX_BACKOFF_S = 1800.0
#: How long a `HEAD` found current (or blocked) is trusted without reading
#: the graph's bookmarks again. The bookmarks can move without `HEAD`
#: moving -- `session initiate` restores an older one -- and this is how
#: long such a change can go unnoticed.
RECHECK_S = 600.0

_TRUTHY = {"1", "true", "yes", "on"}


def enabled(environ=None) -> bool:
    """Is `ARTMIND_VAULT_AUTO_APPLY` switched on (`1`, `true`, `yes`, `on`)?"""
    env = os.environ if environ is None else environ
    return env.get(ENV_VAR, "").strip().lower() in _TRUTHY


def interval_s(environ=None) -> float:
    """`ARTMIND_VAULT_AUTO_APPLY_INTERVAL` in seconds, never below
    `MIN_INTERVAL_S`; the default when unset or not a number."""
    env = os.environ if environ is None else environ
    raw = env.get(INTERVAL_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_INTERVAL_S
    if value != value:  # NaN
        return DEFAULT_INTERVAL_S
    return max(value, MIN_INTERVAL_S)


def backoff_s(failures: int, interval: float) -> float:
    """Seconds to wait before retrying after `failures` failed applies of
    the same `HEAD` in a row: twice the poll interval, doubling each time,
    capped at `MAX_BACKOFF_S`."""
    return min(interval * 2 ** max(failures, 1), MAX_BACKOFF_S)


def iso(ts: float | None) -> str | None:
    """`ts` (seconds since the epoch) as an ISO-8601 UTC timestamp."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


@dataclass
class Outcome:
    """What one poll did.

    `kind` is one of: `no_commits`; `busy` (git is mid-operation); `settling`
    (`HEAD` moved since the last poll); `current` (every store's bookmark is
    `HEAD`); `blocked` (a store has no bookmark, or one that is not an
    ancestor of `HEAD` -- it waits for `HEAD` to move); `waiting` (the
    preflight refused, e.g. the ingest worker is running); `backoff` (the
    last apply of this `HEAD` failed, and its retry is not due yet);
    `applied`; `failed`. `result` is the apply's result on `applied`."""

    kind: str
    head: str | None
    reason: str | None = None
    result: dict | None = None


@dataclass
class LoopState:
    """What the loop remembers between polls."""

    #: `HEAD` at the previous poll: an apply needs it the same twice running.
    seen_head: str | None = None
    #: A `HEAD` with nothing to do -- current, or blocked -- trusted until
    #: `idle_until`, so a quiet vault costs no graph read.
    idle_head: str | None = None
    idle_until: float = 0.0
    idle_outcome: Outcome | None = None
    #: The `HEAD` whose apply last failed, how many times in a row, and
    #: when the next attempt is due.
    failed_head: str | None = None
    failures: int = 0
    next_attempt_at: float = 0.0


def _git_quiet(vault_dir: Path, args: list[str]) -> tuple[int, str]:
    """`git <args>` without `run_command`'s debug log line: this runs on
    every poll, and a daemon's log should not grow while nothing happens."""
    proc = subprocess.run(["git", *args], cwd=vault_dir, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def git_busy(git_dir: Path) -> str | None:
    """Why git is mid-operation in `git_dir` -- a merge, rebase or
    cherry-pick in progress, or a live `index.lock` -- else None."""
    from artmind.vault_sync import operation_in_progress_at

    what = operation_in_progress_at(git_dir)
    if what is not None:
        return f"{what} is in progress"
    if (Path(git_dir) / "index.lock").exists():
        return "git is writing the index (.git/index.lock exists)"
    return None


def _idle(state: LoopState, outcome: Outcome, now: float) -> Outcome:
    state.idle_head = outcome.head
    state.idle_until = now + RECHECK_S
    state.idle_outcome = outcome
    return outcome


def _failed(state: LoopState, head: str | None, now: float, interval: float, error: str) -> Outcome:
    if head != state.failed_head:
        state.failures = 0
    state.failures += 1
    state.failed_head = head
    state.next_attempt_at = now + backoff_s(state.failures, interval)
    return Outcome("failed", head, error)


def _one_line(detail: object) -> str:
    return " ".join(str(detail).split())


def tick(
    vault_dir: Path,
    state: LoopState,
    *,
    now: float,
    interval: float,
    apply: Callable[[Path], dict],
) -> Outcome:
    """One poll: decide, and apply at most once.

    Cheap by construction, stopping at the first reason not to go on: one
    `git rev-parse HEAD` and one `git rev-parse --absolute-git-dir` (no
    commits; git mid-operation; `HEAD` moved since the last poll; `HEAD`
    already found current, or blocked, less than `RECHECK_S` ago; a failed
    apply of this `HEAD` whose retry is not due). Only then `vault sync`'s
    preflight (git, and the worker's pid-file locks), and one Cypher read
    of the graph's bookmark. `apply(vault_dir)` runs only when a store is
    behind and every bookmark is an ancestor of `HEAD`; it returns the sync
    result, or raises, which counts as a failure and backs off
    (`backoff_s`) -- until `HEAD` moves, which retries at once. Never raises
    for anything `vault sync` would refuse."""
    from artmind import vault_sync as vs

    rc, head = _git_quiet(vault_dir, ["rev-parse", "--verify", "-q", "HEAD"])
    if rc != 0 or not head:
        state.seen_head = None
        return Outcome("no_commits", None, "this vault's git repo has no commits yet")
    rc, git_dir = _git_quiet(vault_dir, ["rev-parse", "--absolute-git-dir"])
    busy = git_busy(Path(git_dir)) if rc == 0 else "git could not locate this vault's git directory"
    if busy is not None:
        state.seen_head = None
        return Outcome("busy", head, busy)
    if head != state.seen_head:
        state.seen_head = head
        return Outcome("settling", head, "HEAD moved; waiting one more poll for it to settle")
    if head == state.idle_head and now < state.idle_until and state.idle_outcome is not None:
        return replace(state.idle_outcome)
    if head == state.failed_head and now < state.next_attempt_at:
        return Outcome(
            "backoff", head,
            f"the last apply of this HEAD failed ({state.failures} in a row); "
            f"next attempt at {iso(state.next_attempt_at)}",
        )

    try:
        vs.preflight(vault_dir)
        vault_id = vs.vault_id_or_raise(vault_dir)
    except vs.VaultSyncError as e:
        return Outcome("waiting", head, _one_line(e))
    from neo4j.exceptions import GqlError

    try:
        marks = vs.read_bookmarks(vault_dir, vault_id=vault_id, timeout=vs.ADVISORY_TIMEOUT)
    except (GqlError, OSError) as e:
        return _failed(state, head, now, interval, _one_line(f"graph unreachable: {e}"))

    behind: list[str] = []
    for store in vs.STORES:
        bookmark = marks.effective(store)
        if bookmark is None:
            return _idle(state, Outcome(
                "blocked", head,
                f"no {store} bookmark yet -- auto-apply never bootstraps; run `artmind vault "
                f"sync --store {store} --bootstrapEmpty` (or `--bootstrapSynced`) once by hand",
            ), now)
        if bookmark == head:
            continue
        try:
            vs.check_bookmark(vault_dir, store, bookmark, head)
        except vs.VaultSyncError as e:
            return _idle(state, Outcome("blocked", head, _one_line(e)), now)
        behind.append(store)
    if not behind:
        return _idle(state, Outcome("current", head), now)

    try:
        result = apply(vault_dir)
    except Exception as e:  # any failure of the apply is reported, never raised
        return _failed(state, head, now, interval, _one_line(e) or type(e).__name__)
    state.failures = 0
    state.failed_head = None
    state.next_attempt_at = 0.0
    applied = result.get("head") or head
    _idle(state, Outcome("current", applied), now)
    return Outcome("applied", applied, None, result)
```

- [ ] **Step 5: Run the tests, then the suite**

Run: `uv run --group dev pytest test/test_auto_apply.py -q` → `31 passed`.
Run: `uv run --group dev pytest test/ -q` → all pass (2605 passed).

- [ ] **Step 6: Commit**

```bash
git add artmind/vault_sync.py artmind/auto_apply.py test/test_auto_apply.py
git commit -m "feat(vault-sync): auto_apply.tick -- one poll of opt-in automatic apply

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: the apply is `vault sync`, in a child process

Decision 2. `default_apply(vault_dir)` runs `[sys.executable, "-m", "artmind.auto_apply", str(vault_dir)]` with `cwd=vault_dir` and `ARTMIND_VAULT` set (so the child's `paths` resolves this vault and loads its `config.env`, whatever else is in the environment), captures its output, and reads the **last** JSON line of stdout — anything else an import or a sweep prints comes before it. `{"ok": true, "result": …}` returns the result; `{"ok": false, "error": …}` raises `ApplyError(error)`; no JSON at all (a crash) raises `ApplyError` with the exit code and the last stderr line; a run longer than `APPLY_TIMEOUT_S` (3 h) is killed by `subprocess.run` and raises too.

`child_main(argv, out)` is the child: `vault_sync.sync(Path(argv[0]))` with **no options** (decision 8), one JSON line out, exit 0/1 (2 on bad usage). It is a plain function so tests call it in-process; only the `__main__` block sets the child's loguru sink to the vault's ingestion log (`_log_to_vault`), so a test never reconfigures the suite's logger. The last test runs a real child through the module entry on a repo with no commits — `vault sync` refuses before touching any store, so it needs no Neo4j.

**Files:**
- Modify: `artmind/auto_apply.py`
- Test: `test/test_auto_apply_child.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_auto_apply_child.py`:

```python
"""Auto-apply's apply is `vault sync` itself, run in a child process
(`python -m artmind.auto_apply <vault>`) so nothing it prints, loads or
crashes on reaches `serve` (spec 2026-09-26 §14 A8)."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import artmind.vault_sync as vs
from artmind import auto_apply


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture()
def spawned(monkeypatch):
    """`subprocess.run` recorded: what `default_apply` would have started."""
    calls: list[dict] = []
    reply = {"value": _completed(stdout='{"ok": true, "result": {"head": "abc"}}\n')}

    def _run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        if isinstance(reply["value"], BaseException):
            raise reply["value"]
        return reply["value"]

    monkeypatch.setattr(subprocess, "run", _run)
    return calls, reply


def test_default_apply_runs_this_interpreters_auto_apply_child_in_the_vault(tmp_path, spawned):
    calls, _ = spawned

    result = auto_apply.default_apply(tmp_path)

    assert result == {"head": "abc"}
    (call,) = calls
    assert call["argv"] == [sys.executable, "-m", "artmind.auto_apply", str(tmp_path)]
    assert call["cwd"] == tmp_path
    assert call["env"]["ARTMIND_VAULT"] == str(tmp_path), "the child resolves this vault, whatever its cwd"
    assert call["timeout"] == auto_apply.APPLY_TIMEOUT_S


def test_default_apply_reads_the_last_json_line_past_other_output(tmp_path, spawned):
    _, reply = spawned
    reply["value"] = _completed(stdout='progress 1/2\n{"ok": true, "result": {"head": "def"}}\n')

    assert auto_apply.default_apply(tmp_path) == {"head": "def"}


def test_default_apply_raises_the_childs_error(tmp_path, spawned):
    _, reply = spawned
    reply["value"] = _completed(
        returncode=1, stdout='{"ok": false, "error": "the ingest worker is running for this vault"}\n',
    )

    with pytest.raises(auto_apply.ApplyError, match="^the ingest worker is running for this vault$"):
        auto_apply.default_apply(tmp_path)


def test_default_apply_reports_a_child_that_died_without_a_reply(tmp_path, spawned):
    _, reply = spawned
    reply["value"] = _completed(returncode=1, stderr="Traceback (most recent call last):\nModuleNotFoundError: no x\n")

    with pytest.raises(auto_apply.ApplyError, match="exited 1 without a result: ModuleNotFoundError: no x"):
        auto_apply.default_apply(tmp_path)


def test_default_apply_reports_a_child_that_timed_out(tmp_path, spawned):
    _, reply = spawned
    reply["value"] = subprocess.TimeoutExpired(cmd="x", timeout=1)

    with pytest.raises(auto_apply.ApplyError, match="did not finish"):
        auto_apply.default_apply(tmp_path)


def test_the_child_runs_a_full_unscoped_sync_and_prints_its_result(tmp_path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(vs, "sync", lambda *args, **kwargs: calls.append((args, kwargs)) or {"head": "abc", "replayed": 2})
    out = io.StringIO()

    status = auto_apply.child_main([str(tmp_path)], out=out)

    assert status == 0
    assert calls == [((tmp_path,), {})], "every store, no domain, no bootstrap: the bookmarks advance"
    assert json.loads(out.getvalue()) == {"ok": True, "result": {"head": "abc", "replayed": 2}}


def test_the_child_reports_a_refusal(tmp_path, monkeypatch):
    def _refuse(*args, **kwargs):
        raise vs.VaultSyncError("a merge is in progress in this vault\n-- finish it")

    monkeypatch.setattr(vs, "sync", _refuse)
    out = io.StringIO()

    assert auto_apply.child_main([str(tmp_path)], out=out) == 1
    assert json.loads(out.getvalue()) == {"ok": False, "error": "a merge is in progress in this vault -- finish it"}


def test_the_child_reports_an_unexpected_error_with_its_type(tmp_path, monkeypatch):
    def _crash(*args, **kwargs):
        raise KeyError("head")

    monkeypatch.setattr(vs, "sync", _crash)
    out = io.StringIO()

    assert auto_apply.child_main([str(tmp_path)], out=out) == 1
    assert json.loads(out.getvalue())["error"] == "KeyError: 'head'"


def test_the_child_needs_exactly_one_vault_argument():
    out = io.StringIO()
    assert auto_apply.child_main([], out=out) == 2
    assert "usage" in json.loads(out.getvalue())["error"]


def test_a_real_child_process_answers_through_the_module_entry(tmp_path):
    """End to end through `python -m artmind.auto_apply`, on a vault whose
    repo has no commits yet: `vault sync` refuses before touching any store,
    so this needs no Neo4j."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text('vault_id: "v"\n')

    with pytest.raises(auto_apply.ApplyError, match="no commits yet"):
        auto_apply.default_apply(tmp_path)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_auto_apply_child.py -q`
Expected: `10 failed` — `AttributeError: module 'artmind.auto_apply' has no attribute 'default_apply'` (and `'child_main'`).

- [ ] **Step 3: Add the child and `default_apply`**

**Replace in** `artmind/auto_apply.py` (lines 22-24):

```python

import os
import subprocess
```

**with:**

```python

import json
import os
import subprocess
import sys
```

**Replace in** `artmind/auto_apply.py` (lines 244-246):

```python
    applied = result.get("head") or head
    _idle(state, Outcome("current", applied), now)
    return Outcome("applied", applied, None, result)
```

**with:**

```python
    applied = result.get("head") or head
    _idle(state, Outcome("current", applied), now)
    return Outcome("applied", applied, None, result)


# ── the apply: `vault sync`, in a child process ──────────────────────────────

#: A child `vault sync` still running after this long is stopped and
#: counted as a failure (a bootstrap-sized replay with local embeddings can
#: take a while; a hung one must not block the loop forever).
APPLY_TIMEOUT_S = 3 * 3600


class ApplyError(Exception):
    """The child `vault sync` failed; the message is its error."""


def default_apply(vault_dir: Path) -> dict:
    """Apply `vault_dir` exactly as `artmind vault sync` does -- every store,
    no domain scope, so both bookmarks advance -- in a child process
    (`python -m artmind.auto_apply <vault>`), and return its result.

    A child, not a call inside `serve`: `serve` answers each query by
    redirecting the process-wide stdout (`server._invoke_lock`), so anything
    an apply printed could land inside a query's answer; an apply that
    crashes, or loads a local embedding model, must not take `serve` down or
    keep the model resident; and the child runs the code installed now, not
    the code `serve` imported when it started. Raises `ApplyError`."""
    argv = [sys.executable, "-m", "artmind.auto_apply", str(vault_dir)]
    env = {**os.environ, "ARTMIND_VAULT": str(vault_dir)}
    try:
        proc = subprocess.run(
            argv, cwd=vault_dir, env=env, capture_output=True, text=True, timeout=APPLY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise ApplyError(
            f"`vault sync` did not finish within {APPLY_TIMEOUT_S // 60} minutes and was stopped"
        ) from None
    reply = _last_json_line(proc.stdout)
    if reply is None:
        lines = proc.stderr.strip().splitlines()
        tail = f": {_one_line(lines[-1])}" if lines else ""
        raise ApplyError(f"`vault sync` exited {proc.returncode} without a result{tail}")
    if not reply.get("ok"):
        raise ApplyError(reply.get("error") or f"`vault sync` exited {proc.returncode}")
    return reply.get("result") or {}


def _last_json_line(text: str) -> dict | None:
    """The last stdout line that is a JSON object -- the child's reply --
    or None. Earlier lines are whatever else reached stdout."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def child_main(argv: list[str], out=None) -> int:
    """The child `default_apply` starts: `vault_sync.sync(vault)` with no
    options, then ONE line of JSON on stdout -- `{"ok": true, "result":
    {...}}` or `{"ok": false, "error": "..."}`. Exit status 0 on success, 1
    when the sync failed, 2 on a usage error."""
    out = sys.stdout if out is None else out
    if len(argv) != 1:
        out.write(json.dumps({"ok": False, "error": "usage: python -m artmind.auto_apply <vault>"}) + "\n")
        return 2
    from artmind import vault_sync as vs

    try:
        reply = {"ok": True, "result": vs.sync(Path(argv[0]))}
    except vs.VaultSyncError as e:
        reply = {"ok": False, "error": _one_line(e)}
    except Exception as e:  # reported to the loop, which logs and backs off
        from loguru import logger

        logger.exception("auto-apply: vault sync failed")
        reply = {"ok": False, "error": _one_line(f"{type(e).__name__}: {e}")}
    out.write(json.dumps(reply, default=str) + "\n")
    out.flush()
    return 0 if reply["ok"] else 1


def _log_to_vault() -> None:
    """The child logs where `vault sync` does: the vault's ingestion log."""
    from loguru import logger

    import paths

    paths.INGEST_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        paths.INGEST_LOG_FILE,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} [{level:<7}] auto-apply: {message}",
        level="DEBUG",
        rotation="10 MB",
        retention=5,
    )


if __name__ == "__main__":
    _log_to_vault()
    sys.exit(child_main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests, then the suite**

Run: `uv run --group dev pytest test/test_auto_apply_child.py -q` → `10 passed`.
Run: `uv run --group dev pytest test/ -q` → all pass (2615 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/auto_apply.py test/test_auto_apply_child.py
git commit -m "feat(vault-sync): auto-apply applies through a child vault sync process

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: the loop, its status file, and the `Runner` thread

`run(vault_dir, *, interval, should_stop, sleep, clock=time.time, apply=default_apply)` repeats `tick` until `should_stop()`: after each poll it writes the report, then `sleep(interval)` — always the poll interval, so even a failing vault is polled at that pace while `tick`'s backoff gates the apply. It never raises: an exception escaping `tick` is logged and counted as a failure (so it backs off too). It logs on every apply or failure and whenever the outcome changes, so a quiet vault leaves `serve`'s log quiet.

The report, `.artmind/logs/auto-apply.json` (written to a temp file and `os.replace`d, so a reader never sees half of it): `pid`, `vault`, `started_at`, `interval_s`, `last_check` (`at`, `kind`, `head`, `reason`), `last_apply` (`at`, `kind` = `applied`/`failed`, `head`, `error`, and on success `result` — the apply's `head`, `replayed`, `unchanged`, `retracted`, `regenerated_tables`, `curated_tables`, `same_as_groups`, `domains_swept`), `consecutive_failures`, `next_attempt_at`. `last_apply` is carried over from the previous report when the loop starts, so a restart of `serve` does not forget it.

`Runner(vault_dir, *, interval=None, apply=default_apply, thread_factory=threading.Thread)` claims the vault's `auto-apply.lock` in `start()` (raising `LockHeld` when another process runs the loop), starts `run` on a daemon thread named `artmind-auto-apply` with `should_stop=event.is_set` and `sleep=event.wait` (so `stop()` interrupts the sleep at once), and in `stop(timeout=5.0)` sets the event, joins, and releases the lock. An apply in flight finishes in its own child, which holds and releases the apply lock itself. Tests pass a fake `thread_factory` and never start a thread.

The tests drive `run` with a fake clock whose `sleep` records the requested wait and advances time. With a 30 s interval and an apply that always fails, the attempts land at t+30, t+90, t+210 (60 s, then 120 s) while every sleep is 30 s — asserted on the times `apply` was called, not on a count.

**Files:**
- Modify: `artmind/auto_apply.py`
- Test: `test/test_auto_apply_loop.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_auto_apply_loop.py`:

```python
"""The auto-apply loop (`auto_apply.run`) and the thread `serve` runs it in
(`auto_apply.Runner`), with an injected clock and sleep: no real thread,
no real timer. Real throwaway git repos; the graph bookmark in memory."""
import json
import os
import subprocess
from pathlib import Path

import pytest

import artmind.vault_sync as vs
from artmind import auto_apply, vault_lock
from artmind.vault import VaultLayout, write_state

VAULT_ID = "vault-under-test"


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit_all(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A vault one pull ahead of both stores' bookmarks."""
    import paths
    from artmind import sync_state

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text(f'vault_id: "{VAULT_ID}"\n')
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    (tmp_path / "a.txt").write_text("x")
    base = _commit_all(tmp_path, "base")
    bookmarks = {VAULT_ID: base}
    monkeypatch.setattr(sync_state, "read_graph_bookmark", lambda vault_id, *, timeout=None: bookmarks.get(vault_id))
    write_state(VaultLayout(tmp_path), {vs.STRUCTURED_BOOKMARK_KEY: base})
    (tmp_path / "b.txt").write_text("y")
    _commit_all(tmp_path, "pulled")
    return tmp_path


class _Clock:
    """Fake time: `sleep` records how long the loop asked to wait and moves
    the clock on by exactly that."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class _Apply:
    def __init__(self, clock, *, error=None):
        self.clock = clock
        self.error = error
        self.at: list[float] = []

    def __call__(self, vault_dir):
        self.at.append(self.clock.now)
        if self.error is not None:
            raise self.error
        return {"head": _git(vault_dir, "rev-parse", "HEAD"), "replayed": 1, "bases": {"graph": "x"}}


def _run(repo, clock, apply, polls):
    auto_apply.run(
        repo, interval=30.0, should_stop=lambda: len(clock.sleeps) >= polls,
        sleep=clock.sleep, clock=clock, apply=apply,
    )


def test_run_applies_a_pulled_head_on_the_second_poll_and_records_it(repo):
    clock = _Clock()
    apply = _Apply(clock)
    head = _git(repo, "rev-parse", "HEAD")

    _run(repo, clock, apply, polls=2)

    assert apply.at == [1030.0]
    assert clock.sleeps == [30.0, 30.0]
    status = auto_apply.read_status_file(repo)
    assert status["pid"] == os.getpid()
    assert status["interval_s"] == 30.0
    assert status["started_at"] == auto_apply.iso(1000.0)
    assert status["last_apply"] == {
        "at": auto_apply.iso(1030.0), "kind": "applied", "head": head, "error": None,
        "result": {"head": head, "replayed": 1},
    }
    assert status["last_check"]["kind"] == "applied"
    assert (status["consecutive_failures"], status["next_attempt_at"]) == (0, None)


def test_a_failing_apply_is_retried_on_a_growing_schedule_never_in_a_tight_loop(repo):
    clock = _Clock()
    apply = _Apply(clock, error=RuntimeError("neo4j went away"))

    _run(repo, clock, apply, polls=9)

    assert apply.at == [1030.0, 1090.0, 1210.0], "60 s, then 120 s after each failure"
    assert set(clock.sleeps) == {30.0}, "the loop still sleeps the poll interval every time"
    status = auto_apply.read_status_file(repo)
    assert status["last_apply"]["kind"] == "failed"
    assert status["last_apply"]["error"] == "neo4j went away"
    assert status["consecutive_failures"] == 3
    assert status["next_attempt_at"] == auto_apply.iso(1210.0 + 240.0)


def test_a_poll_that_raises_is_recorded_and_the_loop_goes_on(repo, monkeypatch):
    clock = _Clock()
    apply = _Apply(clock)
    real_tick = auto_apply.tick
    calls = {"n": 0}

    def _tick(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_tick(*args, **kwargs)

    monkeypatch.setattr(auto_apply, "tick", _tick)
    _run(repo, clock, apply, polls=1)
    status = auto_apply.read_status_file(repo)
    assert (status["last_check"]["kind"], status["last_check"]["reason"]) == ("failed", "RuntimeError: boom")

    _run(repo, clock, apply, polls=3)
    assert calls["n"] == 3, "polling went on after the exception"


def test_the_last_apply_survives_a_restart_of_the_loop(repo):
    previous = {"at": "2026-09-28T10:00:00+00:00", "kind": "applied", "head": "abc", "error": None, "result": None}
    auto_apply.write_status_file(repo, {"last_apply": previous})
    clock = _Clock()

    _run(repo, clock, _Apply(clock), polls=1)

    status = auto_apply.read_status_file(repo)
    assert status["last_apply"] == previous, "the first poll only settles"
    assert status["last_check"]["kind"] == "settling"


def test_a_loop_asked_to_stop_polls_nothing(repo):
    clock = _Clock()
    apply = _Apply(clock)

    auto_apply.run(repo, interval=30.0, should_stop=lambda: True, sleep=clock.sleep, clock=clock, apply=apply)

    assert (apply.at, clock.sleeps) == ([], [])
    assert not auto_apply.status_path(repo).exists()


def test_status_file_is_read_as_empty_when_missing_or_corrupt(tmp_path):
    assert auto_apply.read_status_file(tmp_path) == {}
    auto_apply.status_path(tmp_path).parent.mkdir(parents=True)
    auto_apply.status_path(tmp_path).write_text("{not json")
    assert auto_apply.read_status_file(tmp_path) == {}


# ── Runner: the thread `serve` runs the loop in ─────────────────────────────


class _FakeThread:
    """Records how the loop's thread was made, and never runs it."""

    made: list["_FakeThread"] = []

    def __init__(self, target, name, daemon):
        self.target, self.name, self.daemon = target, name, daemon
        self.started = False
        self.joined = None
        _FakeThread.made.append(self)

    def start(self):
        self.started = True

    def join(self, timeout=None):
        self.joined = timeout


@pytest.fixture(autouse=True)
def _fresh_threads():
    _FakeThread.made = []


def test_runner_claims_the_vaults_auto_apply_lock_and_starts_a_daemon_thread(tmp_path):
    runner = auto_apply.Runner(tmp_path, interval=30.0, thread_factory=_FakeThread)

    runner.start()
    try:
        (thread,) = _FakeThread.made
        assert (thread.started, thread.daemon, thread.name) == (True, True, "artmind-auto-apply")
        assert thread.target == runner._run
        lock = vault_lock.lock_path(tmp_path, vault_lock.AUTO_APPLY_LOCK_NAME)
        assert vault_lock.holder(lock) == os.getpid()
    finally:
        runner.stop()


def test_a_second_runner_for_the_same_vault_is_refused_and_starts_nothing(tmp_path):
    first = auto_apply.Runner(tmp_path, interval=30.0, thread_factory=_FakeThread)
    first.start()
    try:
        with pytest.raises(vault_lock.LockHeld):
            auto_apply.Runner(tmp_path, interval=30.0, thread_factory=_FakeThread).start()
        assert len(_FakeThread.made) == 1
    finally:
        first.stop()


def test_stopping_a_runner_joins_its_thread_releases_the_lock_and_ends_the_loop(tmp_path):
    runner = auto_apply.Runner(tmp_path, interval=30.0, thread_factory=_FakeThread)
    runner.start()
    (thread,) = _FakeThread.made

    runner.stop(timeout=2.0)

    assert thread.joined == 2.0
    assert vault_lock.holder(vault_lock.lock_path(tmp_path, vault_lock.AUTO_APPLY_LOCK_NAME)) is None
    thread.target()  # the loop body, run now: it sees the stop and polls nothing
    assert not auto_apply.status_path(tmp_path).exists()


def test_runner_interval_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTMIND_VAULT_AUTO_APPLY_INTERVAL", "45")
    assert auto_apply.Runner(tmp_path).interval == 45.0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_auto_apply_loop.py -q`
Expected: `10 failed` — `AttributeError: module 'artmind.auto_apply' has no attribute 'run'` (and `'read_status_file'`, `'status_path'`, `'Runner'`).

- [ ] **Step 3: Add the loop, the report and `Runner`**

**Replace in** `artmind/auto_apply.py` (lines 27-30):

```python
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
```

**with:**

```python
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from artmind import vault_lock
```

**Replace in** `artmind/auto_apply.py` (lines 348-350):

```python
    )


```

**with:**

```python
    )


# ── the loop, its status file, and the thread `serve` runs it in ────────────

#: The loop's report for `vault status`, beside the locks in `.artmind/logs/`.
STATUS_NAME = "auto-apply.json"

#: The apply result fields kept in the status file -- what was applied.
_SUMMARY_KEYS = (
    "head", "replayed", "unchanged", "retracted", "regenerated_tables",
    "curated_tables", "same_as_groups", "domains_swept",
)


def status_path(vault_dir: Path) -> Path:
    from artmind.vault import VaultLayout

    return VaultLayout(Path(vault_dir)).logs_dir / STATUS_NAME


def read_status_file(vault_dir: Path) -> dict:
    """The loop's last report for `vault_dir`, or `{}` when there is none
    (or it is unreadable -- it is advisory)."""
    try:
        data = json.loads(status_path(vault_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_status_file(vault_dir: Path, status: dict) -> None:
    """Replace the report atomically: `vault status` never reads half a file."""
    path = status_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(
    vault_dir: Path,
    *,
    interval: float,
    should_stop: Callable[[], bool],
    sleep: Callable[[float], object],
    clock: Callable[[], float] = time.time,
    apply: Callable[[Path], dict] = default_apply,
) -> None:
    """Poll until `should_stop()`: `tick`, write the status file, then
    `sleep(interval)` -- always the poll interval, so even a failing vault
    is polled at that pace, and the apply itself waits out `backoff_s`.
    Never raises: an exception from a poll is logged and counted as a
    failure, and the loop goes on. Logs a line when the outcome changes,
    and on every apply or failure, so a quiet vault leaves a quiet log."""
    from loguru import logger

    vault_dir = Path(vault_dir)
    state = LoopState()
    status = {
        "pid": os.getpid(),
        "vault": str(vault_dir),
        "started_at": iso(clock()),
        "interval_s": interval,
        "last_check": None,
        # Kept across a restart of `serve`: the last apply is still the last.
        "last_apply": read_status_file(vault_dir).get("last_apply"),
        "consecutive_failures": 0,
        "next_attempt_at": None,
    }
    last_kind = None
    while not should_stop():
        now = clock()
        try:
            outcome = tick(vault_dir, state, now=now, interval=interval, apply=apply)
        except Exception as e:  # a bug in a poll must not end the loop
            logger.exception("auto-apply: a poll of {} failed", vault_dir)
            outcome = _failed(state, state.seen_head, now, interval, _one_line(f"{type(e).__name__}: {e}"))
        status["last_check"] = {"at": iso(now), "kind": outcome.kind, "head": outcome.head, "reason": outcome.reason}
        if outcome.kind in ("applied", "failed"):
            status["last_apply"] = {
                "at": iso(now),
                "kind": outcome.kind,
                "head": outcome.head,
                "error": outcome.reason if outcome.kind == "failed" else None,
                "result": {k: outcome.result[k] for k in _SUMMARY_KEYS if k in outcome.result}
                if outcome.kind == "applied" and outcome.result else None,
            }
        status["consecutive_failures"] = state.failures
        status["next_attempt_at"] = iso(state.next_attempt_at) if state.failures else None
        if outcome.kind == "applied":
            logger.info("auto-apply: applied {} to {}", (outcome.head or "")[:12], vault_dir)
        elif outcome.kind == "failed":
            logger.warning(
                "auto-apply: apply of {} failed ({} in a row; next attempt at {}): {}",
                vault_dir, state.failures, status["next_attempt_at"], outcome.reason,
            )
        elif outcome.kind != last_kind:
            logger.info("auto-apply: {} -- {}", outcome.kind, outcome.reason or (outcome.head or "")[:12])
        last_kind = outcome.kind
        try:
            write_status_file(vault_dir, status)
        except OSError as e:
            logger.warning("auto-apply: could not write {}: {}", status_path(vault_dir), e)
        sleep(interval)


class Runner:
    """`run` for one vault, on a daemon thread of a long-running process
    (`artmind serve`), holding the vault's auto-apply lock for as long as
    the loop runs -- so a second `serve` for the same vault (another port,
    another terminal) runs no second loop."""

    def __init__(
        self,
        vault_dir: Path,
        *,
        interval: float | None = None,
        apply: Callable[[Path], dict] = default_apply,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ):
        self.vault_dir = Path(vault_dir)
        self.interval = interval_s() if interval is None else interval
        self._apply = apply
        self._thread_factory = thread_factory
        self._stop = threading.Event()
        self._lock = vault_lock.HeldLock(vault_lock.lock_path(self.vault_dir, vault_lock.AUTO_APPLY_LOCK_NAME))
        self._thread = None

    def start(self) -> None:
        """Claim this vault's auto-apply lock and start the loop. Raises
        `vault_lock.LockHeld` when another process already runs it."""
        self._lock.acquire()
        self._stop.clear()
        self._thread = self._thread_factory(target=self._run, name="artmind-auto-apply", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        run(
            self.vault_dir, interval=self.interval, should_stop=self._stop.is_set,
            sleep=self._stop.wait, apply=self._apply,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop polling, wait up to `timeout` for the thread, release the
        lock. An apply already running finishes in its own child process,
        which holds -- and then releases -- the apply lock by itself."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self._lock.release()


```

- [ ] **Step 4: Run the tests, then the suite**

Run: `uv run --group dev pytest test/test_auto_apply_loop.py -q` → `10 passed`.
Run: `uv run --group dev pytest test/ -q` → all pass (2625 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/auto_apply.py test/test_auto_apply_loop.py
git commit -m "feat(vault-sync): the auto-apply loop, its status file, and the Runner thread

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `serve` hosts the loop

Decision 1. `create_app(auto_apply=None)` gains a FastAPI lifespan only when a runner is given: it calls `runner.start()` on startup — a `LockHeld` (another process already runs auto-apply for this vault) is logged and `serve` carries on serving queries without it — and `runner.stop()` on shutdown if it started. `create_app()` with no argument is exactly what it was, so `test_serve.py` is untouched. The module docstring's "the daemon never writes" becomes "no request ever writes", plus a paragraph on auto-apply.

`serve` builds the runner in a new helper, `_auto_apply_runner()`: `None` unless `auto_apply.enabled()` (read after `paths` has loaded the vault's `config.env`); then the vault from `vault.resolve_vault()` (cwd walk-up, or `ARTMIND_VAULT`) — outside a vault it says on stderr that auto-apply is off and serves anyway. On, it prints the vault and the interval.

**Files:**
- Modify: `artmind/server.py`
- Modify: `artmind/cli.py`
- Test: `test/test_serve_auto_apply.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_serve_auto_apply.py`:

```python
"""`artmind serve` hosts the auto-apply loop when ARTMIND_VAULT_AUTO_APPLY
is on (spec 2026-09-26 §14 A8): started with the app, stopped with it, and
never a reason for `serve` itself to fail."""
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from artmind import auto_apply, vault_lock
from artmind.server import create_app


class _FakeRunner:
    def __init__(self, vault_dir, *, refuse=False):
        self.vault_dir = vault_dir
        self.refuse = refuse
        self.events: list[str] = []

    def start(self):
        self.events.append("start")
        if self.refuse:
            raise vault_lock.LockHeld(Path(self.vault_dir) / "auto-apply.lock", 4242)

    def stop(self):
        self.events.append("stop")


def test_the_app_starts_the_runner_with_itself_and_stops_it_on_shutdown(tmp_path):
    runner = _FakeRunner(tmp_path)

    with TestClient(create_app(auto_apply=runner)) as client:
        assert runner.events == ["start"]
        assert client.get("/health").json()["status"] == "ok"

    assert runner.events == ["start", "stop"]


def test_a_vault_whose_auto_apply_runs_elsewhere_still_gets_queries_served(tmp_path):
    runner = _FakeRunner(tmp_path, refuse=True)

    with TestClient(create_app(auto_apply=runner)) as client:
        assert client.get("/health").json()["status"] == "ok"

    assert runner.events == ["start"], "never started here, so nothing to stop"


def test_the_app_without_auto_apply_starts_nothing(tmp_path):
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200


@pytest.fixture()
def served(monkeypatch):
    """`serve` run with uvicorn and the app factory recorded, not started."""
    import uvicorn

    import artmind.server as server

    made: list = []
    monkeypatch.setattr(server, "create_app", lambda auto_apply=None: made.append(auto_apply) or object())
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    return made


def _vault(path):
    (path / ".artmind").mkdir()
    (path / ".artmind" / "vault.yaml").write_text('vault_id: "v"\n')
    return path


def test_serve_hosts_the_loop_for_the_vault_it_starts_in(tmp_path, monkeypatch, served):
    from artmind.cli import cli

    vault = _vault(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setenv("ARTMIND_VAULT_AUTO_APPLY", "1")
    monkeypatch.setenv("ARTMIND_VAULT_AUTO_APPLY_INTERVAL", "60")

    result = CliRunner().invoke(cli, ["serve"])

    assert result.exit_code == 0, result.output
    (runner,) = served
    assert isinstance(runner, auto_apply.Runner)
    assert (runner.vault_dir, runner.interval) == (vault.resolve(), 60.0)
    assert f"auto-apply on for {vault.resolve()} (polls every 60s" in result.output


def test_serve_outside_a_vault_says_auto_apply_is_off_and_serves_anyway(tmp_path, monkeypatch, served):
    from artmind.cli import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARTMIND_VAULT_AUTO_APPLY", "1")

    result = CliRunner().invoke(cli, ["serve"])

    assert result.exit_code == 0, result.output
    assert served == [None]
    assert "not inside a vault -- auto-apply is off" in result.stderr


def test_serve_without_the_opt_in_hosts_no_loop(tmp_path, monkeypatch, served):
    from artmind.cli import cli

    monkeypatch.chdir(_vault(tmp_path))
    monkeypatch.delenv("ARTMIND_VAULT_AUTO_APPLY", raising=False)

    result = CliRunner().invoke(cli, ["serve"])

    assert result.exit_code == 0, result.output
    assert served == [None]
    assert "auto-apply" not in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_serve_auto_apply.py -q`
Expected: `4 failed, 2 passed` — `create_app()` takes no `auto_apply` (`TypeError`), and `serve` never builds a runner (`served == [None]`); `test_the_app_without_auto_apply_starts_nothing` and `test_serve_without_the_opt_in_hosts_no_loop` pass on arrival and guard the default.

- [ ] **Step 3: The lifespan in `server.py`**

**Replace in** `artmind/server.py` (lines 6-9):

```python
reimplementation. Non-query commands are rejected; the daemon never writes.
"""

import threading
```

**with:**

```python
reimplementation. Non-query commands are rejected; no request ever writes.

Opt-in, and separate from the requests: with `ARTMIND_VAULT_AUTO_APPLY=1`,
`artmind serve` also hosts the vault's auto-apply loop (`artmind.auto_apply`),
which applies what Obsidian Git pulls by running `vault sync` in a child
process.
"""

import threading
from contextlib import asynccontextmanager
```

**Replace in** `artmind/server.py` (lines 45-47):

```python

def create_app() -> FastAPI:
    app = FastAPI(title="artmind serve", version=_service_version())
```

**with:**

```python

def create_app(auto_apply=None) -> FastAPI:
    """The `serve` app. `auto_apply`, when given, is an `auto_apply.Runner`
    started with the app and stopped with it; if another process already
    runs auto-apply for that vault, this one serves queries without it."""
    lifespan = None
    if auto_apply is not None:

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            from loguru import logger

            from artmind import vault_lock

            started = False
            try:
                auto_apply.start()
                started = True
            except vault_lock.LockHeld as e:
                logger.warning(
                    "auto-apply: not started for {} -- another process (pid {}) already runs it",
                    auto_apply.vault_dir, e.pid or "?",
                )
            try:
                yield
            finally:
                if started:
                    auto_apply.stop()

    app = FastAPI(title="artmind serve", version=_service_version(), lifespan=lifespan)
```

- [ ] **Step 4: `serve` builds the runner**

**Replace in** `artmind/cli.py` (lines 303-305):

```python
)
def serve(host: str, port: int | None) -> None:
    """Run the warm query server; `artmind query` calls are proxied to it."""
```

**with:**

```python
)
def serve(host: str, port: int | None) -> None:
    """Run the warm query server; `artmind query` calls are proxied to it.

    With ARTMIND_VAULT_AUTO_APPLY=1 (in the vault's .artmind/config.env, or
    the environment) it also applies what Obsidian Git pulls: it polls the
    vault it was started in -- the current directory's, or $ARTMIND_VAULT --
    and runs `vault sync` itself once HEAD has settled and a store is behind.
    `artmind vault status` shows what it did. Like every daemon it runs the
    code it started with: restart it after an upgrade.
    """
```

**Replace in** `artmind/cli.py` (lines 312-314):

```python
        port = int(os.environ.get("ARTMIND_SERVE_PORT", DEFAULT_PORT))
    click.echo(f"artmind serve listening on http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
```

**with:**

```python
        port = int(os.environ.get("ARTMIND_SERVE_PORT", DEFAULT_PORT))
    runner = _auto_apply_runner()
    click.echo(f"artmind serve listening on http://{host}:{port}")
    uvicorn.run(create_app(auto_apply=runner), host=host, port=port, log_level="warning")


def _auto_apply_runner():
    """The auto-apply loop `serve` hosts (spec 2026-09-26 §14 A8), or None:
    off unless ARTMIND_VAULT_AUTO_APPLY is set, and only inside a vault."""
    from artmind import auto_apply
    from artmind import vault as vault_mod

    if not auto_apply.enabled():
        return None
    try:
        vault_dir = vault_mod.resolve_vault()
    except vault_mod.VaultError as e:
        raise click.ClickException(str(e))
    if vault_dir is None:
        click.echo(
            "artmind serve: ARTMIND_VAULT_AUTO_APPLY is set, but this is not inside a vault -- "
            "auto-apply is off. Start `artmind serve` inside the vault, or set ARTMIND_VAULT.",
            err=True,
        )
        return None
    runner = auto_apply.Runner(vault_dir)
    click.echo(
        f"artmind serve: auto-apply on for {vault_dir} (polls every {runner.interval:g}s; "
        "`artmind vault status` shows what it did)"
    )
    return runner
```

- [ ] **Step 5: Run the tests, then the suite**

Run: `uv run --group dev pytest test/test_serve_auto_apply.py test/test_serve.py -q` → all pass.
Run: `uv run --group dev pytest test/ -q` → all pass (2631 passed).

- [ ] **Step 6: Commit**

```bash
git add artmind/server.py artmind/cli.py test/test_serve_auto_apply.py
git commit -m "feat(serve): host the vault's auto-apply loop when ARTMIND_VAULT_AUTO_APPLY is on

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `vault status` shows auto-apply

Decision 9. `auto_apply.status(vault_dir, environ=None)` reads two local files and never git or the graph: `running`/`pid` from the `auto-apply.lock` holder (`vault_lock.holder` — a live `serve`, by its lock, not by a pid that may be recycled), `configured` from this process's environment (the CLI's may differ from `serve`'s, hence both), and `interval_s`, `last_check`, `last_apply`, `consecutive_failures`, `next_attempt_at` from the report. `describe(report)` is the one line `vault status` prints after `Auto-apply: `:

- `on (pid 4242, every 30s); last applied 3f2a1b9c0d12 at 2026-09-29T10:00:00+00:00`
- `on (pid 4242, every 30s); last apply FAILED at … (2 in a row): graph unreachable: refused; next attempt at …`
- `…; now blocked: no graph bookmark yet -- auto-apply never bootstraps; …` when a running loop is holding off
- `off -- ARTMIND_VAULT_AUTO_APPLY is set, but no \`artmind serve\` is running it for this vault`
- `off (opt in: ARTMIND_VAULT_AUTO_APPLY=1, then \`artmind serve\` in this vault)`

`status_report` puts `auto_apply.status(vault_dir)` in its report from the start, so it is present on every early return too (no commits, unreachable graph). `_echo_sync_status` prints the line only when the key is present, so `test_vault_cli.py`'s hand-built reports need no change.

**Files:**
- Modify: `artmind/auto_apply.py`
- Modify: `artmind/vault_sync.py`
- Modify: `artmind/cli.py`
- Test: `test/test_vault_status_auto_apply.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_vault_status_auto_apply.py`:

```python
"""`vault status` shows auto-apply (spec 2026-09-26 §14 A8): whether a
`serve` runs it for this vault, and what it last applied -- or why it
failed and when it tries again."""
import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from artmind import auto_apply, vault_lock


def _report(**overrides):
    report = {
        "configured": False, "running": False, "pid": None, "interval_s": None,
        "last_check": None, "last_apply": None, "consecutive_failures": 0, "next_attempt_at": None,
    }
    report.update(overrides)
    return report


APPLIED = {"at": "2026-09-29T10:00:00+00:00", "kind": "applied", "head": "3f2a1b9c0d12e4f5", "error": None, "result": None}
FAILED = {"at": "2026-09-29T10:05:00+00:00", "kind": "failed", "head": "3f2a1b9c0d12e4f5", "error": "graph unreachable: refused", "result": None}


def test_describe_off_names_the_opt_in():
    assert auto_apply.describe(_report()) == (
        "off (opt in: ARTMIND_VAULT_AUTO_APPLY=1, then `artmind serve` in this vault)"
    )


def test_describe_configured_but_not_running_says_serve_is_missing():
    assert auto_apply.describe(_report(configured=True)) == (
        "off -- ARTMIND_VAULT_AUTO_APPLY is set, but no `artmind serve` is running it for this vault"
    )


def test_describe_running_with_nothing_applied_yet():
    assert auto_apply.describe(_report(running=True, pid=4242, interval_s=30.0)) == (
        "on (pid 4242, every 30s); nothing applied yet"
    )


def test_describe_running_after_an_apply():
    assert auto_apply.describe(_report(running=True, pid=4242, interval_s=30.0, last_apply=APPLIED)) == (
        "on (pid 4242, every 30s); last applied 3f2a1b9c0d12 at 2026-09-29T10:00:00+00:00"
    )


def test_describe_a_failure_with_its_error_count_and_next_attempt():
    report = _report(
        running=True, pid=4242, interval_s=30.0, last_apply=FAILED,
        consecutive_failures=2, next_attempt_at="2026-09-29T10:07:00+00:00",
    )
    assert auto_apply.describe(report) == (
        "on (pid 4242, every 30s); last apply FAILED at 2026-09-29T10:05:00+00:00 (2 in a row): "
        "graph unreachable: refused; next attempt at 2026-09-29T10:07:00+00:00"
    )


def test_describe_a_stopped_loops_failure_promises_no_next_attempt():
    report = _report(last_apply=FAILED, consecutive_failures=1, next_attempt_at="2026-09-29T10:07:00+00:00")
    assert "next attempt" not in auto_apply.describe(report)
    assert "last apply FAILED" in auto_apply.describe(report)


def test_describe_says_why_a_running_loop_is_not_applying():
    check = {"at": "t", "kind": "blocked", "head": "abc", "reason": "no graph bookmark yet --\nauto-apply never bootstraps"}
    assert auto_apply.describe(_report(running=True, pid=1, interval_s=30.0, last_apply=APPLIED, last_check=check)).endswith(
        "; now blocked: no graph bookmark yet -- auto-apply never bootstraps"
    )


def test_status_with_no_loop_ever_run(tmp_path):
    assert auto_apply.status(tmp_path, {"ARTMIND_VAULT_AUTO_APPLY": "1"}) == _report(configured=True)


def test_status_reads_the_lock_holder_and_the_loops_report(tmp_path):
    auto_apply.write_status_file(tmp_path, {"interval_s": 30.0, "last_apply": APPLIED, "consecutive_failures": 0})

    with vault_lock.HeldLock(vault_lock.lock_path(tmp_path, vault_lock.AUTO_APPLY_LOCK_NAME)):
        report = auto_apply.status(tmp_path, {})

    assert report == _report(running=True, pid=os.getpid(), interval_s=30.0, last_apply=APPLIED)


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """A committed vault whose stores are both current, the graph in memory."""
    from artmind import sync_state
    from artmind.vault import VaultLayout, write_state

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text('vault_id: "v"\n')
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    write_state(VaultLayout(tmp_path), {"last_structured_commit": head})
    monkeypatch.setattr(sync_state, "read_graph_bookmark", lambda vault_id, *, timeout=None: head)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARTMIND_VAULT_AUTO_APPLY", raising=False)
    return tmp_path


def test_vault_status_json_carries_auto_apply(vault):
    from artmind.cli import cli

    auto_apply.write_status_file(vault, {"interval_s": 30.0, "last_apply": APPLIED})
    with vault_lock.HeldLock(vault_lock.lock_path(vault, vault_lock.AUTO_APPLY_LOCK_NAME)):
        result = CliRunner().invoke(cli, ["vault", "status", "--compact"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)["sync"]["auto_apply"]
    assert (report["running"], report["pid"], report["last_apply"]) == (True, os.getpid(), APPLIED)


def test_vault_status_prints_an_auto_apply_line(vault):
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["vault", "status"])

    assert result.exit_code == 0, result.output
    assert "Auto-apply: off (opt in: ARTMIND_VAULT_AUTO_APPLY=1, then `artmind serve` in this vault)" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_status_auto_apply.py -q`
Expected: `11 failed` — `AttributeError: module 'artmind.auto_apply' has no attribute 'describe'` (and `'status'`), and no `auto_apply` key in `vault status --compact`.

- [ ] **Step 3: `status` and `describe`**

**Replace in** `artmind/auto_apply.py` (lines 503-505):

```python
        self._lock.release()


```

**with:**

```python
        self._lock.release()


# ── what `vault status` shows ────────────────────────────────────────────────


def status(vault_dir: Path, environ=None) -> dict:
    """Auto-apply as `vault status` reports it. `configured`: is
    ARTMIND_VAULT_AUTO_APPLY on in THIS process's environment (the loop runs
    in `serve`'s, which can differ). `running`/`pid`: a process holds this
    vault's auto-apply lock. Then the loop's last report: `interval_s`,
    `last_check`, `last_apply`, `consecutive_failures`, `next_attempt_at`.
    The report outlives the loop, so a stopped loop still shows what it last
    did. Reads two local files; never touches git or the graph."""
    pid = vault_lock.holder(vault_lock.lock_path(vault_dir, vault_lock.AUTO_APPLY_LOCK_NAME))
    report = read_status_file(vault_dir)
    return {
        "configured": enabled(environ),
        "running": pid is not None,
        "pid": pid,
        "interval_s": report.get("interval_s"),
        "last_check": report.get("last_check"),
        "last_apply": report.get("last_apply"),
        "consecutive_failures": report.get("consecutive_failures") or 0,
        "next_attempt_at": report.get("next_attempt_at"),
    }


def describe(report: dict) -> str:
    """`status()` as the one line `vault status` prints after `Auto-apply: `."""
    running = bool(report.get("running"))
    if running:
        interval = report.get("interval_s")
        line = f"on (pid {report.get('pid')}" + (f", every {interval:g}s)" if interval else ")")
    elif report.get("configured"):
        line = "off -- ARTMIND_VAULT_AUTO_APPLY is set, but no `artmind serve` is running it for this vault"
    else:
        line = "off (opt in: ARTMIND_VAULT_AUTO_APPLY=1, then `artmind serve` in this vault)"
    last = report.get("last_apply") or {}
    if last.get("kind") == "applied":
        line += f"; last applied {(last.get('head') or '?')[:12]} at {last.get('at')}"
    elif last.get("kind") == "failed":
        failures = report.get("consecutive_failures") or 1
        line += f"; last apply FAILED at {last.get('at')} ({failures} in a row): {_one_line(last.get('error'))}"
        if running and report.get("next_attempt_at"):
            line += f"; next attempt at {report['next_attempt_at']}"
    elif running:
        line += "; nothing applied yet"
    check = report.get("last_check") or {}
    if running and check.get("kind") in ("blocked", "waiting", "busy"):
        line += f"; now {check['kind']}: {_one_line(check.get('reason'))}"
    return line


```

- [ ] **Step 4: `status_report` carries it**

**Replace in** `artmind/vault_sync.py` (lines 1836-1838):

```python
    """Everything `vault status` shows about sync. Never raises for an
    unreachable graph, a repo with no commits, or a vault with no vault_id:
    each becomes a field the caller prints."""
```

**with:**

```python
    """Everything `vault status` shows about sync. Never raises for an
    unreachable graph, a repo with no commits, or a vault with no vault_id:
    each becomes a field the caller prints. `auto_apply` is
    `auto_apply.status`: whether `serve` runs auto-apply for this vault, and
    what it last did (spec §14 A8)."""
    from artmind import auto_apply
```

**Replace in** `artmind/vault_sync.py` (lines 1849-1851):

```python
        "graph_error": None,
        "stores": {},
        "message": None,
```

**with:**

```python
        "graph_error": None,
        "stores": {},
        "message": None,
        "auto_apply": auto_apply.status(vault_dir),
```

- [ ] **Step 5: `vault status` prints it**

**Replace in** `artmind/cli.py` (lines 3615-3617):

```python
    if sync.get("message"):
        click.echo(sync["message"])

```

**with:**

```python
    if sync.get("auto_apply") is not None:
        from artmind.auto_apply import describe

        click.echo(f"Auto-apply: {describe(sync['auto_apply'])}")
    if sync.get("message"):
        click.echo(sync["message"])

```

**Replace in** `artmind/cli.py` (lines 3634-3636):

```python
    still to apply (after the fingerprint check), a merge/rebase in progress,
    and unresolved conflicts under .artmind/. An unreachable graph is
    reported, not an error. Read-only. --compact emits all of it as JSON.
```

**with:**

```python
    still to apply (after the fingerprint check), a merge/rebase in progress,
    unresolved conflicts under .artmind/, and whether `artmind serve`'s
    auto-apply (ARTMIND_VAULT_AUTO_APPLY) runs for this vault and what it
    last applied or why it failed. An unreachable graph is reported, not an
    error. Read-only. --compact emits all of it as JSON.
```

- [ ] **Step 6: Run the tests, then the suite**

Run: `uv run --group dev pytest test/test_vault_status_auto_apply.py test/test_vault_cli.py test/test_vault_sync.py -q` → all pass.
Run: `uv run --group dev pytest test/ -q` → all pass (2642 passed).

- [ ] **Step 7: Commit**

```bash
git add artmind/auto_apply.py artmind/vault_sync.py artmind/cli.py test/test_vault_status_auto_apply.py
git commit -m "feat(vault-sync): vault status shows auto-apply -- running, last apply, last error

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: docs, config templates, skills, CLI help; full suite and CLI help

Prose only. `docs/vault.md` gets a new subsection, "Automatic apply (opt-in)", right after the sync subsections (before "## Skills, agent modes, and schemas"): what it applies and when, one apply at a time, failures and backoff, what `vault status` shows, **cost** (no LLM calls; the embedding sweep for every touched key runs through the configured Ollama embedder — a near no-op on a shared AuraDB, a local re-embed of every pulled document with a local Neo4j) and **stale code** (`just dev-install` stops `serve` and does not restart it). Its config table lists the two keys in the vault row. `docs/INSTALL.md` gets an "Automatic apply" paragraph in "Daemons". The vault `config.env` template (`setup._CONFIG_ENV_TEMPLATE`) and the machine template (`artmind/env.example`) carry the two keys, commented out. The `vault sync` docstring gets a paragraph on the new refusal (a paragraph of its own, not an edit of the "Refuses while…" sentence Plan D rewrites). The `justfile`'s `serve-start` comment says it runs from the checkout, so `ARTMIND_VAULT` must be exported for auto-apply. The ingestion-helper skill (where sync is described) gets an "Automatic apply" paragraph and two troubleshooting rows; the query skill's advisory-line rule says a "behind" line clears by itself under auto-apply.

Not touched, on purpose: the `vault` group docstring and the spec (Plan D edits both), `GITIGNORE_BLOCK`, `CLAUDE.md`.

**Files:**
- Modify: `artmind/cli.py`
- Modify: `artmind/env.example`
- Modify: `artmind/setup.py`
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md`
- Modify: `artmind/skills/artmind-query/SKILL.md`
- Modify: `docs/INSTALL.md`
- Modify: `docs/vault.md`
- Modify: `justfile`

- [ ] **Step 1: Apply the prose changes**

**Replace in** `docs/vault.md` (lines 511-511):

```markdown
## Skills, agent modes, and schemas
```

**with:**

```markdown
#### Automatic apply (opt-in)

`vault sync` is manual by default: a query only warns. To have the graph and
structured store follow each pull on their own, set
`ARTMIND_VAULT_AUTO_APPLY=1` -- in the vault's `.artmind/config.env` (per
machine, gitignored) or the environment -- and keep `artmind serve` running
inside the vault (or with `ARTMIND_VAULT` pointing at it). `serve` then polls
the vault's `HEAD` every 30 seconds (`ARTMIND_VAULT_AUTO_APPLY_INTERVAL`,
at least 5) and runs the same apply as `artmind vault sync` -- every store,
no `--domain`, so both bookmarks advance -- when:

- `HEAD` is the same on two polls in a row, with no merge, rebase or
  cherry-pick in progress and no `.git/index.lock`, so Obsidian Git's commit,
  pull and merge have settled;
- `vault sync`'s preflight passes: no unresolved conflicts under `.artmind/`,
  and the ingest worker is not running (it waits, then applies once the
  worker is done);
- a store is behind, and each store's bookmark is an ancestor of `HEAD`.

It never bootstraps: a store with no bookmark, or one not in `HEAD`'s history,
waits for you to run `vault sync` by hand, as the refusal says. The settle
wait is not what makes an apply correct -- `vault sync` applies committed
content at one fixed `HEAD`, never the working tree -- it only avoids applying
a commit the next pull is about to supersede.

**One apply at a time.** Every applying `vault sync` holds
`.artmind/logs/vault-sync.lock`, so a `vault sync` you start while auto-apply
is applying (or the reverse) is refused -- "another `vault sync` is already
applying to this vault" -- never interleaved. One loop per vault, too: the
loop holds `.artmind/logs/auto-apply.lock`, so a second `serve` for the same
vault serves queries without a second loop. Both files, and the loop's report
`.artmind/logs/auto-apply.json`, sit in the gitignored `logs/`.

**Failures.** The apply runs in a child process (`python -m
artmind.auto_apply`), not inside `serve`: nothing it prints can reach a
query's answer, a crash in it cannot take `serve` down, and it runs the code
installed now. A failed apply is logged -- `serve`'s own output, and the
vault's `.artmind/logs/artmind_ingestion.log` for the apply itself -- and
retried after 60 seconds, then 2, 4, 8 ... minutes, capped at 30; a new pull
that moves `HEAD` is tried at once (it may be the fix). A quiet vault costs
two `git rev-parse` calls per poll and no Neo4j at all: the graph's bookmark is
read when `HEAD` moves, and re-read every 10 minutes (a `session initiate` can
move it back without `HEAD` moving).

`artmind vault status` shows it on one line:

    Auto-apply: on (pid 51234, every 30s); last applied 3f2a1b9c0d12 at 2026-09-29T10:00:00+00:00

or `last apply FAILED at ... (2 in a row): <error>; next attempt at ...`, or
why it is holding off (`now blocked: no graph bookmark yet ...`). `--compact`
carries the same under `sync.auto_apply`.

**Cost.** An apply makes no LLM calls: extraction was paid for once, on the
machine that ingested, and a synthesis travels as a file. What it does run is
the embedding sweep for every entity and chunk it touched, through the
configured embedder (`ARTMIND_KG_EMBEDDINGS_URL`, Ollama) -- local CPU/GPU
time, no API cost, but real work on a large pull. On a shared AuraDB the
ingesting machine already embedded everything and the fingerprints match, so
the apply is a near no-op; with a local Neo4j per machine every pulled
document is re-embedded here -- the same work a manual `vault sync` would do,
just sooner, and possibly while you are using the machine.

**Stale code.** The loop that decides when to apply runs inside `serve`,
which imports artmind once, at start (CLAUDE.md, "A running daemon serves
stale code"). `just dev-install` stops `serve` along with every other daemon
and does not start it again: with auto-apply on, start it again after an
upgrade, inside the vault, or nothing is applied until you do. `just
serve-start` runs from the checkout, which is not a vault -- export
`ARTMIND_VAULT` first.

## Skills, agent modes, and schemas
```

**Replace in** `docs/vault.md` (lines 635-635):

```markdown
| **Vault** — `<vault>/.artmind/config.env` | `ARTMIND_KG_NEO4J_*` |
```

**with:**

```markdown
| **Vault** — `<vault>/.artmind/config.env` | `ARTMIND_KG_NEO4J_*`; `ARTMIND_VAULT_AUTO_APPLY`, `ARTMIND_VAULT_AUTO_APPLY_INTERVAL` (opt-in automatic apply by `serve` -- see "Automatic apply") |
```

**Replace in** `docs/INSTALL.md` (lines 142-143):

````markdown
ARTMIND_NO_PROXY=1 artmind query ...
```
````

**with:**

````markdown
ARTMIND_NO_PROXY=1 artmind query ...
```

### Automatic apply

With `ARTMIND_VAULT_AUTO_APPLY=1` (opt-in; docs/vault.md, "Automatic apply"),
`artmind serve` also applies what Obsidian Git pulls into this machine's
graph and structured store -- a daemon that writes, so the rule above
matters twice over. `just dev-install` stops it, and nothing is applied until
you start it again **inside the vault**:

```bash
cd ~/MyVault && artmind serve                  # or:
ARTMIND_VAULT=~/MyVault just serve-start
```

Each apply runs in a child process on the installed code; the loop that
decides when to apply is the daemon's own. `artmind vault status` shows
whether it is running and what it last applied.
````

**Replace in** `artmind/env.example` (lines 147-147):

```bash
# ARTMIND_IMPORT_MAX_BYTES=536870912
```

**with:**

```bash
# ARTMIND_IMPORT_MAX_BYTES=536870912

# Opt-in automatic apply (docs/vault.md, "Automatic apply"): while `artmind
# serve` runs inside a vault, it applies what Obsidian Git pulls, without
# `artmind vault sync` by hand. Better set per vault, in
# <vault>/.artmind/config.env: set here, it applies to every vault a `serve`
# is started in. Seconds between polls: default 30, minimum 5.
# ARTMIND_VAULT_AUTO_APPLY=1
# ARTMIND_VAULT_AUTO_APPLY_INTERVAL=30
```

**Replace in** `artmind/setup.py` (lines 349-351):

```python
# sync service that would choke on KG staging and snapshots; the default is
# <vault>/.artmind/data.
# ARTMIND_DATA_DIR=/somewhere/else
```

**with:**

```python
# sync service that would choke on KG staging and snapshots; the default is
# <vault>/.artmind/data.
# ARTMIND_DATA_DIR=/somewhere/else

# Opt-in automatic apply: while `artmind serve` runs in this vault, apply what
# Obsidian Git pulls without running `artmind vault sync` by hand
# (docs/vault.md, "Automatic apply"). Seconds between polls: default 30.
# ARTMIND_VAULT_AUTO_APPLY=1
# ARTMIND_VAULT_AUTO_APPLY_INTERVAL=30
```

**Replace in** `artmind/cli.py` (lines 3717-3719):

```python
    sync` still starts from the same bookmarks, applies whatever was missed,
    and idempotently re-applies what the scoped run already did.

```

**with:**

```python
    sync` still starts from the same bookmarks, applies whatever was missed,
    and idempotently re-applies what the scoped run already did.

    One apply at a time per vault: while another applying `vault sync` holds
    this vault's lock (.artmind/logs/vault-sync.lock) -- one you started, or
    `artmind serve`'s opt-in auto-apply (ARTMIND_VAULT_AUTO_APPLY=1), which
    runs this same sync by itself after each pull -- this one is refused.

```

**Replace in** `justfile` (lines 463-463):

```just
# start the warm query daemon in the background if not already up (logs to logs/serve.log)
```

**with:**

```just
# start the warm query daemon in the background if not already up (logs to logs/serve.log).
# With ARTMIND_VAULT_AUTO_APPLY=1 it also applies what Obsidian Git pulls -- but only
# for a vault, and this recipe runs from the checkout: export ARTMIND_VAULT=<vault>
# first (docs/vault.md, "Automatic apply").
```

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (lines 466-466):

```markdown
`artmind vault sync --store structured --bootstrapEmpty` once, then plain `vault sync`.
```

**with:**

```markdown
`artmind vault sync --store structured --bootstrapEmpty` once, then plain `vault sync`.

**Automatic apply (opt-in).** With `ARTMIND_VAULT_AUTO_APPLY=1` in the vault's
`.artmind/config.env` and `artmind serve` running inside the vault, `serve` runs that same
plain `vault sync` by itself once a pull has settled (HEAD unchanged across two polls, 30 s
apart by default) and a store is behind. It never bootstraps and never applies past a refusal:
a missing bookmark, a bookmark not in HEAD's history, a merge in progress or a running ingest
worker makes it wait. `artmind vault status` has an `Auto-apply:` line -- on or off, what it
last applied, or the error of a failed apply and when it retries (60 s, doubling, capped at
30 min). While it applies, a `vault sync` run by hand is refused ("already applying"), and the
reverse. After `just dev-install` restart `serve` inside the vault: until then nothing applies.
```

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (lines 531-531):

```markdown
| `vault sync`: "the graph bookmark ... is not a commit in this clone" / "not an ancestor of HEAD" | Another machine sharing the graph synced commits this clone has not pulled — or, if pulling doesn't clear it, history was rewritten | Let Obsidian Git pull (merge), then re-run `vault sync`. Still refused after pulling? Follow the message: `vault sync --store <name> --bootstrapSynced` if that store is known-current, else `--store <name> --bootstrapEmpty` |
```

**with:**

```markdown
| `vault sync`: "the graph bookmark ... is not a commit in this clone" / "not an ancestor of HEAD" | Another machine sharing the graph synced commits this clone has not pulled — or, if pulling doesn't clear it, history was rewritten | Let Obsidian Git pull (merge), then re-run `vault sync`. Still refused after pulling? Follow the message: `vault sync --store <name> --bootstrapSynced` if that store is known-current, else `--store <name> --bootstrapEmpty` |
| `vault sync`: "another `vault sync` is already applying to this vault" | `artmind serve`'s auto-apply (or a sync in another terminal) is applying right now | Wait for it; `artmind vault status` shows the auto-apply line. Nothing to fix |
| `vault status`: "Auto-apply: ... last apply FAILED" | The automatic `vault sync` hit an error (the message follows); it retries on a backoff, or at once after the next pull | Run `artmind vault sync` by hand to see the full error, fix it, and let the next attempt (or your run) apply |
```

**Replace in** `artmind/skills/artmind-query/SKILL.md` (lines 14-14):

```markdown
Any stderr line starting `artmind: ` about a store being behind, unreachable, not pulled, or unable to sync — e.g. `artmind: graph is N docs / M tables behind the vault`, `artmind: structured store is M tables behind the vault`, `artmind: the graph store's sync bookmark is not in this clone's history -- let Obsidian Git pull...`, or `artmind: the graph store's sync would fail -- ...` — is advisory, not an error and not query data: the answer may miss recently pulled documents, or the vault owner may need to pull or fix something before syncing. Tell the user once, in your own words; never run `vault sync` (or any of its flags) yourself.
```

**with:**

```markdown
Any stderr line starting `artmind: ` about a store being behind, unreachable, not pulled, or unable to sync — e.g. `artmind: graph is N docs / M tables behind the vault`, `artmind: structured store is M tables behind the vault`, `artmind: the graph store's sync bookmark is not in this clone's history -- let Obsidian Git pull...`, or `artmind: the graph store's sync would fail -- ...` — is advisory, not an error and not query data: the answer may miss recently pulled documents, or the vault owner may need to pull or fix something before syncing. Tell the user once, in your own words; never run `vault sync` (or any of its flags) yourself. If the vault owner runs `artmind serve` with auto-apply on (`ARTMIND_VAULT_AUTO_APPLY=1`), a "behind" line right after a pull clears by itself within a minute or two -- say that rather than asking them to sync.
```

- [ ] **Step 2: Full suite**

Run: `uv run --group dev pytest test/ -q`
Expected: all pass (2642 passed) — the same count as after Task 7; `test_cli_guide.py`, `test_query_skill_structural_schema.py` and the scaffold tests included.

- [ ] **Step 3: CLI help**

Run: `just dev-cli-help | grep -n -A8 "COMMAND: cli serve"` — the `serve` help carries the ARTMIND_VAULT_AUTO_APPLY paragraph.
Run: `ARTMIND_NO_PROXY=1 uv run artmind vault sync --help` — a paragraph "One apply at a time per vault" names the lock and auto-apply.
Run: `ARTMIND_NO_PROXY=1 uv run artmind vault status --help` — mentions `artmind serve`'s auto-apply.

- [ ] **Step 4: Commit**

```bash
git add artmind/cli.py artmind/env.example artmind/setup.py artmind/skills/artmind-ingestion-helper/SKILL.md artmind/skills/artmind-query/SKILL.md docs/INSTALL.md docs/vault.md justfile
git commit -m "docs(vault-sync): opt-in auto-apply -- vault.md, INSTALL.md, env.example, config template, skills, CLI help

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Live verification (user)

Hermetic tests cannot see a real Neo4j or a real `serve` (CLAUDE.md §1, §3). Run this against your **local** Neo4j, in throwaway vaults, after Plans C, C2, D and this plan are merged and installed. Queries run in-process (`ARTMIND_NO_PROXY=1`) so no stale daemon answers them; the one daemon here is B's `serve`, started fresh on this code. Ingest calls your LLM; auto-apply does not.

Two machines, each with **its own** Neo4j database: in Neo4j Browser, `:use system`, `CREATE DATABASE planea;` and `CREATE DATABASE planeb;` (Desktop/Enterprise). On Community edition, two throwaway servers instead: `docker run -d --name plane-a -p 7688:7687 -e NEO4J_AUTH=neo4j/planepass neo4j:5` and `docker run -d --name plane-b -p 7689:7687 -e NEO4J_AUTH=neo4j/planepass neo4j:5`, URIs `neo4j://127.0.0.1:7688` / `:7689`, database `neo4j`.

### 0. Install and set up two clones

```bash
cd ~/projects/artmind9 && just dev-install          # also stops any running serve
export ARTMIND_NO_PROXY=1
export LIVE=$HOME/plane-live && rm -rf "$LIVE" && mkdir -p "$LIVE" && cd "$LIVE"
git init -q --bare --initial-branch=main remote.git
git clone -q remote.git A && cd "$LIVE/A" && git config user.email a@example.com && git config user.name A
artmind init && $EDITOR .artmind/config.env     # point at planea
artmind setup
mkdir -p notes && printf '# Acme\n\nAcme Bank is headquartered in Pune. Its CEO is Priya Rao.\n' > notes/acme.md
artmind ingest sync notes/acme.md --domain banking.organization
git add -A && git commit -qm "A: acme" && git push -q origin HEAD:main
artmind vault sync --bootstrapSynced --compact
cd "$LIVE" && git clone -q remote.git B && cd "$LIVE/B" && git config user.email b@example.com && git config user.name B
artmind init && $EDITOR .artmind/config.env     # point at planeb
artmind setup
artmind vault sync --bootstrapEmpty --compact    # B's own graph now has acme.md
```

Obsidian Git is simulated with plain git: `git add -A && git commit -qm MSG && git pull -q --no-rebase origin main && git push -q origin HEAD:main` on A, and `git pull -q --no-rebase origin main` on B.

### 1. Turn auto-apply on in B only

```bash
cd "$LIVE/B"
printf '\nARTMIND_VAULT_AUTO_APPLY=1\nARTMIND_VAULT_AUTO_APPLY_INTERVAL=5\n' >> .artmind/config.env
artmind serve > "$LIVE/serve-b.log" 2>&1 &     # from INSIDE B
sleep 3 && head -3 "$LIVE/serve-b.log"          # "auto-apply on for .../B (polls every 5s; ..."
artmind vault status                            # "Auto-apply: on (pid N, every 5s); nothing applied yet"
cd "$LIVE/A" && artmind vault status            # "Auto-apply: off (opt in: ...)" -- A never opted in
```

### 2. A pull in B reaches B's graph with no `vault sync`

```bash
cd "$LIVE/A"
printf '# Acme Pune\n\nAcme Bank opened a Pune branch in 2024, led by Ravi Menon.\n' > notes/acme-pune.md
artmind ingest sync notes/acme-pune.md --domain banking.organization
git add -A && git commit -qm "A: pune" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main
artmind vault status                            # if you are quick: "graph is 1 docs / 0 tables behind" -- nothing applied yet
sleep 20
artmind vault status                            # both stores "current"; "Auto-apply: on (...); last applied <HEAD[:12]> at <time>"
grep "auto-apply" "$LIVE/serve-b.log"           # settling -> applied <sha> -> current
```

Cypher on `planeb`:

```cypher
MATCH (d:Document) RETURN d.id, d.fingerprint IS NOT NULL AS fp ORDER BY d.id;
MATCH (e:Entity {domain: 'banking.organization'}) WHERE e.name CONTAINS 'Ravi' RETURN e.name, e.embedding IS NOT NULL AS embedded;
```

Both notes' documents with `fp` true, and Ravi Menon embedded — B's graph caught up without anyone running `vault sync`. `ARTMIND_NO_PROXY=1 artmind query vector-text --domain banking.organization "who leads the Pune branch" --compact` in B answers from it.

### 3. One apply at a time; the worker makes it wait

Hold B's apply lock for 30 s the way a long apply would, from artmind's own interpreter:

```bash
cd "$LIVE/B"
PY="$(uv tool dir)/artmind9/bin/python"
"$PY" -c "import time; from artmind import vault_lock as v; l = v.HeldLock(v.lock_path('.', v.SYNC_LOCK_NAME)).acquire(); time.sleep(30)" &
sleep 1 && artmind vault sync --compact
#   refused: "another `vault sync` is already applying to this vault (pid N) -- one you started, or `artmind serve`'s auto-apply ..."
wait
```

(If a pull lands while the lock is held, auto-apply's own attempt is refused the same way, counts as one failure, and is retried 10 s later — `vault status` shows it.)

To see *waiting*, start an ingest worker in B (`artmind ingest async notes/ --domain banking.organization`) and pull a new commit from A while it runs: `vault status` shows `now waiting: the ingest worker is running for this vault ...`, and the apply follows within a poll of the worker finishing.

### 4. A failure backs off and does not stop `serve`

Stop B's database (`docker stop plane-b`, or stop `planeb` in Desktop), then push another note from A and pull it in B. Within a minute:

```bash
artmind vault status        # "last apply FAILED at ... (1 in a row): graph unreachable: ...; next attempt at <+10 s>"
sleep 60 && artmind vault status   # "(3 in a row)"; next attempt 40 s, then 80 s out -- doubling from 2x the 5 s interval
curl -s http://127.0.0.1:8377/health   # {"service":"artmind","status":"ok",...} -- serve is still up
```

Start the database again: the next attempt applies, and `vault status` shows `last applied`.

### 5. After an upgrade, restart `serve`

```bash
cd ~/projects/artmind9 && just dev-install      # stops B's serve
cd "$LIVE/B" && artmind vault status            # "Auto-apply: off -- ARTMIND_VAULT_AUTO_APPLY is set, but no `artmind serve` is running it"
artmind serve > "$LIVE/serve-b.log" 2>&1 &     # back on, on the new code
```

### Clean up

```bash
cd ~/projects/artmind9 && just dev-stop-daemons && rm -rf "$LIVE"
# Neo4j Browser: :use system; DROP DATABASE planea; DROP DATABASE planeb;   (or: docker rm -f plane-a plane-b)
```

---

## Self-review

### Scope → tasks

| Scope item (spec §14 A8 and the brief) | Task(s) |
|---|---|
| Opt in with `ARTMIND_VAULT_AUTO_APPLY=1`, off by default | 3 (`enabled`), 6 (`serve` reads it), 8 (templates, docs) |
| A long-running process polls HEAD cheaply | 3 (`tick`: two quiet `rev-parse`, no Neo4j when idle), 5 (`run`), 6 (`serve`) |
| Runs the same apply as `vault sync` | 4 (child calls `vault_sync.sync(vault)`), 3's last test (real `sync` as `apply`) |
| …when the preflight passes | 3 (`waiting`; worker test) |
| …when a store is behind | 3 (`current` vs `applied`; structured-only-behind test) |
| …when the bookmark is an ancestor of HEAD (Plan B's rule) | 3 (`blocked` via `check_bookmark`) |
| Which process hosts the loop; single instance | decision 1; 5 (`Runner` + `auto-apply.lock`), 6 |
| Per-vault lock, flock pattern from `worker_pid.py` | 1 (`vault_lock`), 2 |
| Poll interval, backoff; must not hammer git or Neo4j | decisions 4, 6, 7; 3 (backoff tests, `graph.reads`), 5 (attempt times) |
| Debounce while Obsidian Git is mid-pull/merge | decision 5; 3 (settle, merge, index.lock tests) |
| Never apply while the ingest worker runs | 3 (`waiting`, via the existing preflight) |
| Never concurrently with a manual `vault sync`; same lock | 2 (lock inside `sync()`), 4 (child goes through `sync()`) |
| A failed apply must not crash the daemon; log it; surface it; no tight retry | 3 (`failed`), 4 (every child failure is `ApplyError`), 5 (`run` never raises; logs; status file), 6 (`LockHeld` tolerated), 7 |
| `vault status` shows enabled, last result, time, error | 7 |
| Stale-daemon problem documented | decision 11; 8 (`docs/vault.md` "Stale code", `docs/INSTALL.md`, `serve --help`) |
| No domain scoping: full sync, cursors advance | decision 8; 4 (`test_the_child_runs_a_full_unscoped_sync_and_prints_its_result`) |
| Cost implications (no LLM; embedding sweeps) | 8 (`docs/vault.md` "Cost") |
| Config docs: `docs/vault.md`, `docs/INSTALL.md`, `artmind/env.example`; the sync skill | 8 |
| Final task: full suite plus CLI help | 8 |
| Tests hermetic; `tick` pure with injected clock/sleep; no threads/timers; real git repos; mock only Neo4j seams and the sync call; assert what was applied | 3, 4, 5, 6, 7 (fake `thread_factory`; `_Clock`; `_Apply` records the vault; `_write_to_neo4j` recorder) |

### Placeholder scan

No "TBD", "similar to Task N", or step without its code. Every code block was generated from the verified commits and re-applied by script (see "Verification record"); every replacement's old text was checked to occur exactly once in its file on `planC2-applied`.

### Type and name consistency

`vault_lock`: `HeldLock(path).acquire()/.release()/.held`, `LockHeld(path, pid)`, `lock_path(vault_dir, name)`, `holder(path)`, `SYNC_LOCK_NAME`, `AUTO_APPLY_LOCK_NAME` (1) → `vault_sync.sync` (2), `auto_apply.Runner` (5), `auto_apply.status` (7), `server.create_app` (6). `vault_sync`: `_sync` (2), `OPERATION_MARKERS`, `operation_in_progress_at` (3) → `auto_apply.git_busy`. `auto_apply`: `ENV_VAR`, `INTERVAL_ENV_VAR`, `DEFAULT_INTERVAL_S`, `MIN_INTERVAL_S`, `MAX_BACKOFF_S`, `RECHECK_S`, `enabled`, `interval_s`, `backoff_s`, `iso`, `Outcome(kind, head, reason, result)`, `LoopState`, `git_busy`, `tick(vault_dir, state, *, now, interval, apply)` (3); `APPLY_TIMEOUT_S`, `ApplyError`, `default_apply`, `child_main(argv, out=None)` (4); `STATUS_NAME`, `status_path`, `read_status_file`, `write_status_file`, `run(vault_dir, *, interval, should_stop, sleep, clock, apply)`, `Runner(vault_dir, *, interval, apply, thread_factory)` with `start/stop/_run/vault_dir/interval` (5) → `server.create_app(auto_apply=)`, `cli._auto_apply_runner` (6); `status(vault_dir, environ=None)`, `describe(report)` (7) → `vault_sync.status_report`, `cli._echo_sync_status`. Outcome kinds (`no_commits`, `busy`, `settling`, `current`, `blocked`, `waiting`, `backoff`, `applied`, `failed`) are the same in code, tests and `describe`. Report keys (`last_check`, `last_apply`, `consecutive_failures`, `next_attempt_at`, `interval_s`) match between `run`, `status` and the tests.

### Ordering and green suite

Each task leaves `uv run --group dev pytest test/ -q` green; the verification record has the count after each. No existing test changes in any task. Tasks 4, 5 and 7 each extend `auto_apply.py` below what the previous task wrote and above the `if __name__ == "__main__":` block (Task 4 adds that block; Tasks 5 and 7 anchor on it).

### Design decisions the spec did not settle (for review)

1. **Host: `serve` only.** The UIs are interactive front-ends started and stopped at will; a writer inside them would tie applying to whether a browser tab's server is up, and hosting in all three needs a leader election for no gain (`just serve-ui`/`serve-admin-ui` restart `serve` anyway). The instance lock covers the one real multi-host case: two `serve`s (different ports) for one vault.
2. **Child process for the apply.** In-process would share `serve`'s stdout, which `run_cli_args` swaps process-wide under `_invoke_lock` while answering a query — any `print` in a sweep or a restore would land in a query's answer — and would keep a sweep's embedding client and memory in `serve`. The child costs one interpreter start (~1–2 s) per apply, which is rare. A side effect worth having: the apply always runs current code even in an old `serve`.
3. **The lock lives in `sync()`**, not the CLI command, so every caller is covered; dry runs are exempt (they write nothing). The files are in `.artmind/logs/` — gitignored since the ownership-rule block — so Plan D's `.gitignore` bump is untouched.
4. **30 s poll, 5 s floor.** Obsidian Git works on minutes; 30 s keeps the latency after a pull at 30–60 s and the cost at two `git rev-parse` per poll. The per-poll `git` calls bypass `run_command` so `serve`'s log does not grow by a debug line every 30 s.
5. **Debounce = two equal polls + no git operation markers + no `index.lock`.** A stale `index.lock` left by a crashed git keeps auto-apply `busy` indefinitely (and `vault status` says why); Obsidian Git is blocked by the same file, so the user sees it either way.
6. **Backoff: 2×interval, doubling, 30-min cap, reset by a new HEAD;** graph-unreachable counts as a failure; preflight refusals and blocked bookmarks do not. A refused apply caused by a manual `vault sync` starting between the loop's lock check and the child's (a narrow race) counts as one failure and is retried after 60 s.
7. **10-minute idle recheck** so a bookmark moved back by `session initiate` is noticed without a pull.
8. **Never bootstraps; never domain-scoped.** Bootstrapping is a judgement call (`--bootstrapEmpty` vs `--bootstrapSynced`) only a person can make.
9. **`vault status` visibility from files, not a port:** the lock holder says whether the loop runs, the report says what it did. `configured` reflects the CLI's own environment, so `vault status` can say "set here, but no serve runs it".
10. **The opt-in belongs in the vault's `config.env`**; the machine-wide file works too (documented) but turns it on for every vault a `serve` starts in.
11. **The spec is not amended here.** §14 A8 already records the opt-in; a new amendment would collide with Plan D's spec edits. If wanted, add after both land: "A11: auto-apply runs in `serve` only, applies through a child `vault sync`, holds `.artmind/logs/vault-sync.lock`; decisions 4–9 of Plan E."
12. **`just dev-install` stops daemons but does not restart them** (CLAUDE.md's "expands to … `artmind init`" is itself stale — the recipe no longer runs `init`). This plan documents the restart instead of changing the recipe.

### Accepted residual risk

- **A foreground `ingest sync` is not guarded.** The preflight refuses while the *worker* runs; `artmind ingest sync` in a terminal takes no lock, so auto-apply can apply while it writes — as a manual `vault sync` already can. Graph writes are idempotent and fingerprinted, so the likely cost is one redundant replay; a lock for foreground ingest is a separate change.
- **Two clones of one vault on one machine sharing one AuraDB** have two lock files, so their applies can overlap. Every graph write is a MERGE/idempotent replay and the bookmark write is last-writer-wins on the same commit range.
- **`serve` stopping mid-apply** leaves the child running to completion (holding, then releasing, the apply lock). `Runner.stop` waits 5 s for the thread, not for the child.
- **Timestamps** in the report and `vault status` are UTC ISO-8601; `serve`'s log lines are local time.

## Verification record

- Worktree: `git worktree add --detach <scratchpad>/verifyE planC2-applied`. A script parsed this file and applied, in task order, every **Create** and **Replace in** … **with:** block (each replacement's old text must match its file exactly once), running `uv run --group dev pytest test/ -q` after each task and committing it.
- Baseline at `planC2-applied`: `2560 passed, 14 skipped`.
- Results after each task (all `… passed, 14 skipped`): Task 1: `2568`; Task 2: `2573`; Task 3: `2605`; Task 4: `2615`; Task 5: `2625`; Task 6: `2631`; Task 7: `2642`; Task 8: `2642`.
- The applied tree is identical to branch `planE-applied` (`git diff --quiet planE-applied HEAD` → exit 0). That branch holds the same code as one commit per task on top of `planC2-applied`, kept for later planners and for review.
- Expected-failure claims: for every task, the task's non-test files were reverted to the previous task's state (new modules deleted) and the task's tests re-run. Results, as stated in each Step 2: Task 1 collection error (`ImportError`); Task 2 `3 failed, 2 passed`; Task 3 collection error (`ImportError`); Task 4 `10 failed`; Task 5 `10 failed`; Task 6 `4 failed, 2 passed`; Task 7 `11 failed`. Task 8 is prose only.
- Every replacement's old text was also checked against Plan D's work-in-progress branch (`planD-applied` at `cc5c766`): each still matches exactly once there, so the plan applies on top of Plan D as it stood.
- A throwaway smoke run (not part of the suite) started a real `Runner` thread with a 0.2 s interval on a scratch repo: it settled, applied the one new commit once, went back to `current`, and `stop()` returned in 0.02 s.
- The verification worktrees were removed afterwards; only this plan file was committed to `vault-sync-cde`.
