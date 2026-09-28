# Vault sync — Plan B (phase 3: Neo4j topologies) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `artmind vault sync` works the same against a shared AuraDB and a separate local Neo4j per machine: each store keeps its own bookmark (the graph's in the graph, keyed by a committed `vault_id`; DuckDB's in `state.json`), every graph write records a staging-folder fingerprint so a shared graph's apply is a near no-op, and `vault status` plus every `query` command report how far behind the vault each store is.

**Architecture:** Spec [2026-09-26-vault-git-transport-design.md](../specs/2026-09-26-vault-git-transport-design.md) §6 A2 (bookmarks), A3 (fingerprints), A4 (staleness), §8 (topologies), §10 (`vault status`), §12 item 3. A new module `artmind/sync_state.py` holds every Cypher statement for the `:ArtmindSyncState` bookmark node and `Document.fingerprint`, plus the fingerprint's byte definition. `artmind/ingest.py`'s single commit path records the fingerprint. `artmind/vault_sync.py` gains per-store bookmarks (read, migrate, check, advance), the fingerprint skip, and `pending_work`/`status_report`/`query_staleness_warning`. `artmind/cli.py` gets `vault sync --store`, a fuller `vault status`, and a `query` result callback that prints the staleness line on stderr. `artmind/manifest.py` mints `vault_id` into `.artmind/vault.yaml`; `artmind/graph_snapshot.py` carries the bookmark inside graph snapshots.

**Verified:** every code block in this plan was applied, in task order, by a script that parses this file, to a scratch worktree of `290303d`, and the full suite was run after each task: see "Verification record" at the end. The "Expected: FAIL" claims come from reverting each task's source change and re-running its tests.

**Tech Stack:** Python 3.14, Click (rich-click), neo4j driver, pytest (`uv run --group dev pytest test/ -q`, ~60 s, `2334 passed, 14 skipped` at `290303d`), real throwaway git repos in `tmp_path` for anything git decides, recording fakes for every Neo4j seam.

---

## Context for the implementer

Read before starting:

- `CLAUDE.md`, "Testing implications" §1–§4. §1: `artmind/_entry.py` proxies `artmind query ...` to a running `serve` daemon, which imported the code once at start — so a query-time change is invisible through a stale daemon (Task 10 accounts for this). §3: when a test fakes a graph write, assert on **what was sent** (which Cypher, which parameters), never on a count.
- The spec above: §6 A2, A3, A4, §8, §10, §12 item 3, §14.
- `test/test_vault_sync.py` top to bottom. Its helpers are reused by name: `repo` (a `git init`ed `tmp_path`), `_commit_all`, `_write_doc_folder`, `_patch_kg_dir`, `_patch_structured_text_dir`, `_patch_ingest_and_projection` (records `write_to_neo4j`, `retract_document`, `rebuild_in_batches`, `sweeps`), `_patch_table_sources`, `_add_table`.
- `test/conftest.py`: an autouse fixture replaces `artmind.graph_query.neo4j_session` with a null session for every test (every query returns no rows). Code that must be hermetic-by-default therefore resolves the session **at call time** through the `graph_query` module (`graph_query.neo4j_session(...)`), never `from artmind.graph_query import neo4j_session` at import.
- `test/test_no_git_writes.py`: fails the suite if a module under `artmind/` or `utils/` contains a git write. It allows none: Task 6 (which would have added one `update-ref` exception) was dropped, and the final review added `update-ref`/`symbolic-ref`/`gc`/`prune` and `_git(...)` wrapper calls to what it scans for.

Terminology:

- **Store**: `graph` (Neo4j) or `structured` (DuckDB). Each has its own **bookmark** — "this store reflects the vault's commits up to X".
- **Graph bookmark**: a `(:ArtmindSyncState {vault_id, last_applied_commit, applied_at})` node, one per vault. Lives in the graph, so on a shared AuraDB every machine sees the same one.
- **Structured bookmark**: `last_structured_commit` in `<vault>/.artmind/state.json` (machine-local, gitignored).
- **Legacy cursor**: `last_synced_commit` in `state.json`, the single cursor `vault sync` used before this plan.
- **`vault_id`**: a uuid in the committed `.artmind/vault.yaml`, keying the graph bookmark.
- **Fingerprint**: `Document.fingerprint`, a sha256 over a staging folder's committed files (Task 1 defines the bytes).
- **Track A / Track B**: `vault sync` replaying KG document folders / restoring structured tables (and projecting them with `table2graph`), as in Plan A.

**Line numbers** are those of each file as it stands when the task starts — for the first task that touches a file, that is commit `290303d`. Every replacement quotes the exact old code: match on the text, not the number.

Run the suite with:

```bash
uv run --group dev pytest test/ -q
```

Do **not** run `just dev-install`, `artmind init` outside `tmp_path`, or anything against a live Neo4j. The user tests live afterwards ("Live verification (user)" at the end).

## Decisions this plan makes (the spec left them open)

Each is argued in full under "Self-review → Design decisions the spec did not settle"; the number in brackets is its number there.

1. **Migration conflict rule** [1]. The graph's own `:ArtmindSyncState` bookmark always wins for the graph. The legacy `last_synced_commit` only seeds a store that has no bookmark of its own, and is removed once both stores have one.
2. **Per-store ranges without re-reading git** [2]. One `classify_diff(graph_bookmark, HEAD)` for the graph. The structured store reuses that plan's tables when its bookmark equals the graph's (the common case: zero extra git); otherwise one extra `_classify_structured_text_diff` (a single scoped `git diff`). A table the graph projects is always restored into DuckDB first, so `table2graph` reads HEAD's rows whatever the structured bookmark says.
3. **Fingerprint bytes** [3]. sha256 over, for each of `document.json`, `chunks.json`, `observations.json`, `relationships.json` in that order: the name, NUL, then `absent` NUL or the byte length in decimal, NUL, the raw bytes. All four files, not only `observations.json`, because track A replays a folder when any of them changes (spec §14 A4). Apply hashes the committed **blobs** (`git cat-file --batch`, no filters), ingest hashes the bytes it wrote; a mismatch can only cause a replay, never skip one.
4. **Staleness hook** [4, 5]. A `@query.result_callback()` in `cli.py`: it runs only after a query succeeded, never for `--help`, prints on stderr only, and runs identically in-process and inside the `serve` daemon (whose `CliRunner` captures stderr; `_entry._proxy` already writes it back). It is gated on `state.json` holding a bookmark (no graph round trip for a machine that never synced), caps the graph connection at 2 s, swallows every exception, and `ARTMIND_NO_STALENESS_CHECK=1` turns it off.
5. **`--store graph|structured`** [6]. A new `vault sync` option runs one store alone; `--bootstrapEmpty`/`--bootstrapSynced` apply to every store in the run. That is how one store is bootstrapped while the other keeps its bookmark.
6. **A bookmark must be an ancestor of HEAD** [7]. Otherwise `vault sync` refuses ("pull first") instead of diffing: diffing from a shared graph's bookmark that this clone has not pulled would read the other machine's new documents as removals and retract them. The refusal also names the recovery when pulling won't help — `--store <name> --bootstrapSynced`/`--bootstrapEmpty` — since a history rewrite leaves nothing to pull.
7. **No pinned ref** [9]. Task 6 (originally: pin each advanced bookmark under `refs/artmind/` with `git update-ref`) is dropped. Spec D2 says artmind only *reads* git; a private ref is still a git write, and it bought nothing real — with Obsidian Git merging (never rebasing), a bookmarked commit stays reachable, and decision 6's ancestor-of-HEAD check already refuses sync after any history rewrite whether or not the commit was pinned. `test/test_no_git_writes.py` keeps no `update-ref` exception.
8. **`session initiate` restores the bookmark the graph had** [8], by exporting `:ArtmindSyncState` inside the graph snapshot — not from the unified snapshot manifest's `vault_commit`. **This deviates from spec §6 A2**; see Self-review, decision 8.

## File map

| File | Change |
|---|---|
| `artmind/sync_state.py` | new: fingerprint byte definition; all Cypher for `:ArtmindSyncState` and `Document.fingerprint` (Task 1) |
| `artmind/graph_query.py` | `neo4j_session(..., timeout=)` caps connect time (Task 1) |
| `artmind/setup.py` | constraint on `ArtmindSyncState.vault_id` (Task 1); mint `vault_id` in `scaffold_vault` (Task 3) |
| `artmind/ingest.py` | `_load_staged` computes the fingerprint; `_commit_document_tx` records it (Task 2) |
| `artmind/manifest.py` | `read_vault_id`, `ensure_vault_id` (Task 3) |
| `artmind/vault.py` | `write_state(..., remove=)` (Task 4) |
| `artmind/vault_sync.py` | bookmarks, per-store ranges, `--store` (Task 4); fingerprint skip (Task 5); the ancestor refusal names the bootstrap-flag recovery (Task 4 follow-up); `pending_work`, `query_staleness_warning` (Task 8); `status_report` (Task 9); module docstring (Task 11) |
| `artmind/graph_snapshot.py` | export `:ArtmindSyncState` with the graph (Task 7) |
| `artmind/cli.py` | `init` prints `vault_id` (Task 3); `vault sync --store` (Task 4); `session initiate` bookmark line (Task 7); fuller `vault status` (Task 9); `query` staleness callback (Task 10); docstrings (Task 11) |
| `test/test_sync_state.py`, `test/test_ingest_fingerprint.py`, `test/test_vault_id.py`, `test/test_query_staleness.py` | new |
| `test/test_vault_sync.py`, `test/test_vault_cli.py`, `test/test_vault.py`, `test/test_graph_snapshot.py`, `test/test_no_git_writes.py` | extended; existing cursor assertions retargeted to bookmarks (Task 4) |
| `docs/vault.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md`, `artmind/skills/artmind-query/SKILL.md` | prose (Task 11) |

No `justfile` change: no command is added or removed (`vault sync` gains an option), and the justfile has no `vault sync`/`vault status` recipe to update — `vault-doctor`'s is unaffected.

---

### Task 1: `artmind/sync_state.py` — the fingerprint's bytes and every Cypher for bookmarks and fingerprints (spec §6 A2, A3)

One module owns the graph side of `vault sync`: the `:ArtmindSyncState` bookmark node (read, write, uniqueness constraint) and `Document.fingerprint` (the byte definition, the write inside the commit transaction, the batched read). Nothing else in the codebase may spell this Cypher. `graph_query.neo4j_session` gains a `timeout` so advisory reads (Task 8) can cap how long an unreachable graph holds a command up. Nothing calls the new module yet.

**Files:**
- Create: `artmind/sync_state.py`
- Modify: `artmind/graph_query.py:195-200` (`neo4j_session`)
- Modify: `artmind/setup.py:808-810` (`_setup_neo4j`), `:857-859` (`setup_all`'s constraint list)
- Test: `test/test_sync_state.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_sync_state.py`:

```python
"""The graph bookmark and document fingerprints (spec 2026-09-26 §6 A2, A3).

Neo4j is never reached: a recording session stands in for it, and every test
asserts on the Cypher and parameters actually sent (CLAUDE.md §3), never on a
count the fake invents."""
import hashlib
from contextlib import contextmanager

import pytest

from artmind import sync_state


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _RecordingSession:
    """Answers each `run` with the next canned row list and records
    `(cypher, params)`."""

    def __init__(self, answers=()):
        self.calls = []
        self._answers = list(answers)

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return _Result(self._answers.pop(0) if self._answers else [])


@pytest.fixture()
def session(monkeypatch):
    """Patches `graph_query.neo4j_session`, which `sync_state` resolves at call
    time, and records the `timeout` each session was opened with."""
    import artmind.graph_query as graph_query

    fake = _RecordingSession()
    fake.timeouts = []

    @contextmanager
    def _fake_session(access_mode=None, *, timeout=None):
        fake.timeouts.append(timeout)
        yield fake

    monkeypatch.setattr(graph_query, "neo4j_session", _fake_session)
    return fake


# ── the fingerprint's byte definition ─────────────────────────────────────────


def _folder(tmp_path, **files):
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    return tmp_path


def test_fingerprint_is_sha256_over_named_length_prefixed_files_in_fixed_order(tmp_path):
    doc = _folder(
        tmp_path,
        **{"document.json": b'{"id": "d1"}', "observations.json": b"[]", "chunks.json": b"[1]"},
    )

    expected = hashlib.sha256(
        b"document.json\0" + b"12\0" + b'{"id": "d1"}'
        + b"chunks.json\0" + b"3\0" + b"[1]"
        + b"observations.json\0" + b"2\0" + b"[]"
        + b"relationships.json\0" + b"absent\0"
    ).hexdigest()
    assert sync_state.folder_fingerprint(doc) == expected


def test_fingerprint_is_none_without_observations(tmp_path):
    doc = _folder(tmp_path, **{"document.json": b"{}"})

    assert sync_state.folder_fingerprint(doc) is None


@pytest.mark.parametrize("name", sync_state.FINGERPRINTED_FILES)
def test_a_change_to_any_committed_file_changes_the_fingerprint(tmp_path, name):
    """Track A replays a folder when ANY of its files changed (spec §14 A4);
    a fingerprint that ignored one of them would skip that replay."""
    base = {n: b"[]" for n in sync_state.FINGERPRINTED_FILES}
    before = sync_state.staging_fingerprint(lambda n: base.get(n))
    changed = dict(base, **{name: b"[ ]"})

    assert sync_state.staging_fingerprint(lambda n: changed.get(n)) != before


def test_files_outside_the_fingerprinted_set_do_not_change_it(tmp_path):
    """The sidecar and the chunk cache never reach the graph."""
    doc = _folder(tmp_path, **{"document.json": b"{}", "observations.json": b"[]"})
    before = sync_state.folder_fingerprint(doc)
    (doc / "embeddings.json").write_bytes(b'{"c1": [0.1]}')
    (doc / "chunks").mkdir()
    (doc / "chunks" / "0.json").write_bytes(b"{}")

    assert sync_state.folder_fingerprint(doc) == before


def test_an_absent_file_and_an_empty_file_differ():
    present = {"observations.json": b"[]", "relationships.json": b""}
    absent = {"observations.json": b"[]"}

    assert sync_state.staging_fingerprint(present.get) != sync_state.staging_fingerprint(absent.get)


# ── the Cypher, asserted on what is sent ──────────────────────────────────────


class _Tx:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))


def test_set_document_fingerprint_targets_the_live_document_by_id():
    tx = _Tx()

    sync_state.set_document_fingerprint(tx, "doc-1", "abc")

    assert tx.calls == [
        ("MATCH (d:Document {id: $doc_id}) SET d.fingerprint = $fingerprint",
         {"doc_id": "doc-1", "fingerprint": "abc"}),
    ]


def test_read_document_fingerprints_is_one_query_over_every_id(session):
    session._answers = [[{"id": "d1", "fingerprint": "f1"}, {"id": "d2", "fingerprint": None}]]

    got = sync_state.read_document_fingerprints(["d2", "d1", "d3", "d1"], timeout=2.0)

    assert got == {"d1": "f1", "d2": None}
    assert len(session.calls) == 1
    cypher, params = session.calls[0]
    assert "MATCH (d:Document) WHERE d.id IN $doc_ids" in cypher
    assert params == {"doc_ids": ["d1", "d2", "d3"]}
    assert session.timeouts == [2.0]


def test_read_document_fingerprints_of_nothing_never_connects(session):
    assert sync_state.read_document_fingerprints([]) == {}
    assert session.calls == []


def test_read_graph_bookmark_matches_on_vault_id(session):
    session._answers = [[{"commit": "c0ffee"}]]

    assert sync_state.read_graph_bookmark("vault-a", timeout=1.5) == "c0ffee"
    cypher, params = session.calls[0]
    assert cypher.startswith("MATCH (s:ArtmindSyncState {vault_id: $vault_id})")
    assert params == {"vault_id": "vault-a"}
    assert session.timeouts == [1.5]


def test_read_graph_bookmark_is_none_when_the_vault_has_no_node(session):
    assert sync_state.read_graph_bookmark("vault-a") is None


def test_write_graph_bookmark_merges_one_node_per_vault(session):
    sync_state.write_graph_bookmark("vault-a", "c0ffee")

    cypher, params = session.calls[0]
    assert cypher.startswith("MERGE (s:ArtmindSyncState {vault_id: $vault_id})")
    assert "SET s.last_applied_commit = $commit, s.applied_at = $applied_at" in cypher
    assert params["vault_id"] == "vault-a"
    assert params["commit"] == "c0ffee"
    assert params["applied_at"].endswith("+00:00")


def test_the_setup_creates_the_vault_id_constraint():
    from artmind.setup import _setup_neo4j

    tx = _Tx()
    _setup_neo4j(tx, 768)

    assert (sync_state.CONSTRAINT_CYPHER, {}) in tx.calls
    assert "FOR (n:ArtmindSyncState) REQUIRE n.vault_id IS UNIQUE" in sync_state.CONSTRAINT_CYPHER
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_sync_state.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'artmind.sync_state'`.

- [ ] **Step 3: Implement**

The fingerprint hashes raw bytes, never parsed JSON, so ingest (which hashes the files it just wrote) and `vault sync` (which hashes the committed blobs via `git cat-file`, Task 5) agree byte for byte. All four files `ingest._load_staged` reads are covered, because track A replays a folder when any of them changes (spec §14 A4) — a digest of `observations.json` alone would let a `chunks.json`-only change be skipped on a local Neo4j.

**Create** `artmind/sync_state.py`:

```python
"""What the graph records about `vault sync` (spec 2026-09-26 §6 A2, A3).

Two things live in Neo4j so that every machine sharing one graph sees them:

- **The graph bookmark** -- one `(:ArtmindSyncState {vault_id,
  last_applied_commit, applied_at})` node per vault: "this graph reflects that
  vault's commits up to `last_applied_commit`". Keyed by the `vault_id` in the
  committed `.artmind/vault.yaml`, so two vaults sharing one AuraDB never share
  a bookmark, and a separate local Neo4j per machine carries its own.
- **Document fingerprints** -- `Document.fingerprint`, a digest of the staging
  folder a document was committed from (`staging_fingerprint`). `vault sync`
  skips a folder whose committed fingerprint already equals the graph's: on a
  shared AuraDB the ingesting machine already wrote it.

Every Cypher statement that touches either lives in this module and nowhere
else. The structured store's bookmark is machine-local (DuckDB is always a
local file) and lives in `state.json` -- see `artmind.vault_sync`.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

#: Created by `setup._setup_neo4j` (so by `artmind setup`, ingest's schema
#: check and every graph restore). One node per vault; the MERGE below keys on it.
CONSTRAINT_CYPHER = (
    "CREATE CONSTRAINT artmind_sync_state_vault_id IF NOT EXISTS "
    "FOR (n:ArtmindSyncState) REQUIRE n.vault_id IS UNIQUE"
)
CONSTRAINT_NAME = "artmind_sync_state_vault_id"

#: The staging files `ingest._load_staged` reads into a graph write, in the
#: fixed order they are hashed. The embedding sidecar is excluded (gitignored,
#: derived); so is anything else in the folder (the chunk cache, debug output),
#: since none of it reaches the graph.
FINGERPRINTED_FILES = ("document.json", "chunks.json", "observations.json", "relationships.json")

_READ_BOOKMARK = (
    "MATCH (s:ArtmindSyncState {vault_id: $vault_id}) "
    "RETURN s.last_applied_commit AS commit"
)
_WRITE_BOOKMARK = (
    "MERGE (s:ArtmindSyncState {vault_id: $vault_id}) "
    "SET s.last_applied_commit = $commit, s.applied_at = $applied_at"
)
_SET_FINGERPRINT = "MATCH (d:Document {id: $doc_id}) SET d.fingerprint = $fingerprint"
_READ_FINGERPRINTS = (
    "MATCH (d:Document) WHERE d.id IN $doc_ids "
    "RETURN d.id AS id, d.fingerprint AS fingerprint"
)


def staging_fingerprint(read: Callable[[str], bytes | None]) -> str | None:
    """The fingerprint of one staging folder, or None when it has no
    `observations.json` (nothing to commit, so nothing can match).

    `read(name)` returns a file's raw bytes, or None when it is absent. The
    digest is sha256 over, for each name in `FINGERPRINTED_FILES` in order:
    the name, a NUL, then either `b"absent"` and a NUL, or the byte length in
    ASCII decimal, a NUL and the bytes themselves. Raw bytes, never parsed
    JSON: ingest hashes exactly what it wrote to disk, and `vault sync` hashes
    the committed blobs (`git cat-file`, no filters applied), which are the
    same bytes -- artmind's `.gitattributes` sets no text/eol/filter rule for
    `.artmind/data/**`. When a user's own rule does change them, the two
    digests differ and the folder is replayed: a mismatch can only cost a
    replay, never skip one."""
    if read("observations.json") is None:
        return None
    digest = hashlib.sha256()
    for name in FINGERPRINTED_FILES:
        digest.update(name.encode("ascii") + b"\0")
        data = read(name)
        if data is None:
            digest.update(b"absent\0")
        else:
            digest.update(str(len(data)).encode("ascii") + b"\0")
            digest.update(data)
    return digest.hexdigest()


def folder_fingerprint(doc_dir: Path) -> str | None:
    """`staging_fingerprint` of a staging folder on disk."""
    def _read(name: str) -> bytes | None:
        path = Path(doc_dir) / name
        return path.read_bytes() if path.is_file() else None

    return staging_fingerprint(_read)


def set_document_fingerprint(tx, doc_id: str, fingerprint: str | None) -> None:
    """Record `fingerprint` on `(:Document {id: doc_id})`, inside the commit
    transaction (`ingest._commit_document_tx`). None removes the property, so
    a document committed without one can never be skipped by `vault sync`."""
    tx.run(_SET_FINGERPRINT, doc_id=doc_id, fingerprint=fingerprint)


def _session(timeout: float | None):
    from artmind import graph_query

    return graph_query.neo4j_session(timeout=timeout)


def read_document_fingerprints(doc_ids: list[str], *, timeout: float | None = None) -> dict[str, str | None]:
    """`{doc_id: fingerprint}` for every id in `doc_ids` that is a live
    `:Document` (not `:DocumentHistory`), in ONE query. A document the graph
    does not have is absent from the result; one committed before fingerprints
    existed maps to None."""
    if not doc_ids:
        return {}
    with _session(timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, doc_ids=sorted(set(doc_ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def read_graph_bookmark(vault_id: str, *, timeout: float | None = None) -> str | None:
    """The commit this graph reflects for vault `vault_id`, or None."""
    with _session(timeout) as session:
        record = session.run(_READ_BOOKMARK, vault_id=vault_id).single()
    return record["commit"] if record else None


def write_graph_bookmark(vault_id: str, commit: str) -> None:
    """Advance (or create) vault `vault_id`'s graph bookmark to `commit`."""
    applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _session(None) as session:
        session.run(_WRITE_BOOKMARK, vault_id=vault_id, commit=commit, applied_at=applied_at)
```


`sync_state` resolves the session through `graph_query` at call time, so conftest's null session covers it. Give `neo4j_session` its `timeout`:

**Replace in** `artmind/graph_query.py` (lines 195-200):

```python
@contextmanager
def neo4j_session(access_mode: str | None = None):
    settings = _connection_settings()
    driver = GraphDatabase.driver(
        settings["uri"], auth=(settings["user"], settings["password"])
    )
```

**with:**

```python
@contextmanager
def neo4j_session(access_mode: str | None = None, *, timeout: float | None = None):
    """A session on this vault's graph. `timeout` (seconds), when given, caps
    how long connecting may take -- for advisory reads (the `vault sync`
    staleness check) that must never hold up the command they ride along
    with when the graph is unreachable."""
    settings = _connection_settings()
    driver_kwargs: dict = {}
    if timeout is not None:
        driver_kwargs = {"connection_timeout": timeout, "connection_acquisition_timeout": timeout}
    driver = GraphDatabase.driver(
        settings["uri"], auth=(settings["user"], settings["password"]), **driver_kwargs
    )
```


Create the constraint wherever the repo creates its constraints (`artmind setup`, ingest's schema check and every graph restore all go through `_setup_neo4j`). The statement itself stays in `sync_state`:

**Replace in** `artmind/setup.py` (lines 808-810):

```python
    session.run(
        "CREATE INDEX projection_state_id IF NOT EXISTS FOR (n:ProjectionState) ON (n.id)"
    )
```

**with:**

```python
    session.run(
        "CREATE INDEX projection_state_id IF NOT EXISTS FOR (n:ProjectionState) ON (n.id)"
    )
    # :ArtmindSyncState -- one node per vault, the graph's `vault sync`
    # bookmark (spec 2026-09-26 §6 A2). The statement lives with the rest of
    # that node's Cypher, in artmind/sync_state.py.
    from artmind.sync_state import CONSTRAINT_CYPHER as sync_state_constraint

    session.run(sync_state_constraint)
```


**Replace in** `artmind/setup.py` (lines 857-859):

```python
            "synthesis_id",
            "sameas_proposal_id",
        ],
```

**with:**

```python
            "synthesis_id",
            "sameas_proposal_id",
            "artmind_sync_state_vault_id",
        ],
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_sync_state.py -q`
Expected: `15 passed`.

Run: `uv run --group dev pytest test/ -q`
Expected: `2350 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/sync_state.py artmind/graph_query.py artmind/setup.py test/test_sync_state.py
git commit -m "feat(vault-sync): sync_state module for the graph bookmark and document fingerprints" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: every graph write of a staging folder records its fingerprint (spec §6 A3)

Every path that commits a staged folder — `ingest sync`/`async`, `ingest table2graph`, `ingest write-to-graph`, `ingest pull-kg`, archive restore, the admin dashboard's artifact import, and `vault sync` itself — goes through `ingest._write_to_neo4j` → `_load_staged` → `_commit_document_tx` (`grep -rn "_commit_document_tx\|_write_to_neo4j\|commit_to_graph" artmind` shows no other caller of the transaction). So the fingerprint is computed once, in `_load_staged`, from the files on disk, and written once, in `_commit_document_tx`, right after the `Document` node is merged.

**Files:**
- Modify: `artmind/ingest.py:2717-2720` (`_commit_document_tx`'s import), `:2736-2737` (after the Document merge), `:2829-2833` (`_load_staged`'s docstring), `:2859-2861` (its return value)
- Test: `test/test_ingest_fingerprint.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_ingest_fingerprint.py`:

```python
"""Every graph write of a staging folder records its fingerprint on the
Document (spec 2026-09-26 §6 A3). All commit paths -- ingest, table2graph,
write-to-graph, pull-kg, archive restore, dashboard import, vault sync --
reach the graph through `ingest._write_to_neo4j`, so these tests drive that
function (and table2graph's own writer) and assert on the Cypher parameters
actually sent (CLAUDE.md §3)."""
import json
from contextlib import contextmanager

import artmind.ingest as ing
from artmind import sync_state

_SET_FP = "MATCH (d:Document {id: $doc_id}) SET d.fingerprint = $fingerprint"


class _Result:
    def single(self):
        return None

    def data(self):
        return []

    def consume(self):
        return None

    def __iter__(self):
        return iter(())


class _Session:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return _Result()

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    execute_read = execute_write

    def fingerprint_writes(self):
        return [params for cypher, params in self.calls if cypher == _SET_FP]


def _patch(monkeypatch):
    import artmind.graph_query as graph_query

    session = _Session()

    @contextmanager
    def _fake(*args, **kwargs):
        yield session

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    return session


def _stage(folder, observations='[{"id": "o1", "key": "Acme|ORG|banking"}]'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "a.md"}), encoding="utf-8")
    (folder / "chunks.json").write_text("[]", encoding="utf-8")
    (folder / "relationships.json").write_text("[]", encoding="utf-8")
    (folder / "observations.json").write_text(observations, encoding="utf-8")
    return folder


def test_load_staged_carries_the_folder_fingerprint(tmp_path):
    folder = _stage(tmp_path / "doc")

    staged = ing._load_staged(folder, "banking")

    assert staged["fingerprint"] == sync_state.folder_fingerprint(folder)
    assert staged["fingerprint"] is not None


def test_a_graph_write_records_the_fingerprint_of_the_bytes_on_disk(tmp_path, monkeypatch):
    session = _patch(monkeypatch)
    folder = _stage(tmp_path / "doc")

    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)

    assert session.fingerprint_writes() == [
        {"doc_id": "doc-1", "fingerprint": sync_state.folder_fingerprint(folder)}
    ]


def test_the_fingerprint_is_written_after_the_document_node_exists(tmp_path, monkeypatch):
    """`SET d.fingerprint` matches on `(:Document {id})`; run before the
    Document MERGE, it would silently match nothing on a first ingest."""
    session = _patch(monkeypatch)
    folder = _stage(tmp_path / "doc")

    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)

    cyphers = [c for c, _ in session.calls]
    merge_doc = next(i for i, c in enumerate(cyphers) if "CREATE (n:Document {id: $id})" in c)
    assert cyphers.index(_SET_FP) > merge_doc


def test_a_changed_folder_records_a_changed_fingerprint(tmp_path, monkeypatch):
    session = _patch(monkeypatch)
    folder = _stage(tmp_path / "doc")
    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)
    _stage(folder, observations='[{"id": "o2", "key": "Beta|ORG|banking"}]')
    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)

    first, second = session.fingerprint_writes()
    assert first["fingerprint"] != second["fingerprint"]


def test_table2graph_staging_records_its_fingerprint_too(tmp_path, monkeypatch):
    """table2graph writes its staging with `write_staged` (which adds a
    `table2graph_report.json` outside the fingerprinted set) and commits it
    through `_write_to_neo4j`; the fingerprint must match the four files."""
    from artmind.table2graph import write_staged

    session = _patch(monkeypatch)
    folder = tmp_path / "table__accounts"
    staged = {
        "document": {"id": "table:banking:accounts", "name": "accounts"},
        "chunks": [],
        "observations": [{"id": "o1", "key": "Acme|ORG|banking"}],
        "relationships": [],
    }
    write_staged(staged, {"rows": 1}, folder)

    ing._write_to_neo4j(folder, "banking", defer_rebuild=True)

    assert session.fingerprint_writes() == [
        {"doc_id": "table:banking:accounts", "fingerprint": sync_state.folder_fingerprint(folder)}
    ]
    assert (folder / "table2graph_report.json").is_file()
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_fingerprint.py -q`
Expected: `5 failed`. `test_load_staged_carries_the_folder_fingerprint` with `KeyError: 'fingerprint'`; the other four because no `SET d.fingerprint` was sent (`assert [] == [{'doc_id': ...}]`, `ValueError: list.index(x): x not in list`, `ValueError: not enough values to unpack (expected 2, got 0)`).

- [ ] **Step 3: Implement**

**Replace in** `artmind/ingest.py` (lines 2717-2720):

```python
    from artmind import projection, same_as
    from artmind.observations import key_string

    document = staged["document"]
```

**with:**

```python
    from artmind import projection, same_as, sync_state
    from artmind.observations import key_string

    document = staged["document"]
```


**Replace in** `artmind/ingest.py` (lines 2736-2737):

```python
    _merge_relabeled(tx, "Document", "DocumentHistory", doc_id, _flatten_props(document), replace=False)
    for chunk in staged["chunks"]:
```

**with:**

```python
    _merge_relabeled(tx, "Document", "DocumentHistory", doc_id, _flatten_props(document), replace=False)
    # 3b. The staging folder's fingerprint (spec 2026-09-26 §6 A3): `vault
    #     sync` skips a committed folder whose fingerprint the graph already
    #     carries -- on a shared graph, the machine that ingested it wrote it.
    #     Every path that commits a staged folder (ingest, table2graph,
    #     write-to-graph, pull-kg, archive restore, dashboard import, vault
    #     sync) reaches this line through `_write_to_neo4j`/`_load_staged`.
    sync_state.set_document_fingerprint(tx, doc_id, staged.get("fingerprint"))
    for chunk in staged["chunks"]:
```


**Replace in** `artmind/ingest.py` (lines 2829-2833):

```python
    `chunks.json` carries no vectors (see `strip_embeddings`) -- the sidecar
    beside it, if present, supplies them here so the graph write below still
    gets a vector without recomputing it. A fresh clone has no sidecar, so
    its chunks come back with none; a later sweep (Task 2) fills those in.
    """
```

**with:**

```python
    `chunks.json` carries no vectors (see `strip_embeddings`) -- the sidecar
    beside it, if present, supplies them here so the graph write below still
    gets a vector without recomputing it. A fresh clone has no sidecar, so
    its chunks come back with none; a later sweep (Task 2) fills those in.

    `fingerprint` is taken from the files' bytes as they are on disk, before
    any parsing (`sync_state.staging_fingerprint`), so it equals what `vault
    sync` computes from the same files' committed blobs.
    """
    from artmind import sync_state

```


**Replace in** `artmind/ingest.py` (lines 2859-2861):

```python
            "relationships": _load("relationships.json", []),
        }
    except Exception as e:
```

**with:**

```python
            "relationships": _load("relationships.json", []),
            "fingerprint": sync_state.folder_fingerprint(doc_kg_dir),
        }
    except Exception as e:
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_ingest_fingerprint.py test/test_reingest.py test/test_ingest_hooks.py test/test_embed_sweep.py test/test_table2graph.py -q`
Expected: all pass (`test_ingest_hooks.py` inspects `_commit_document_tx`'s source: the fingerprint write is a plain call, not a `try`, so its no-swallow checks still hold).

Run: `uv run --group dev pytest test/ -q`
Expected: `2355 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_ingest_fingerprint.py
git commit -m "feat(ingest): record the staging-folder fingerprint on every Document write" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `vault_id` minted once into `.artmind/vault.yaml` (spec §6 A2)

The graph bookmark is keyed by a `vault_id` that is committed with the vault, so every clone of one vault shares a bookmark on a shared AuraDB and two different vaults on the same AuraDB never do. `artmind init` mints it on a fresh vault and appends it to an existing one. It is appended as text — never by re-serialising the YAML — so the user's comments and mappings are untouched, and quoted so YAML can never read it as a number. A present-but-empty `vault_id:` is refused rather than duplicated (YAML would silently let the second key win).

**Files:**
- Modify: `artmind/manifest.py:19-20` (imports), and append after `load()` (the file ends at line 186)
- Modify: `artmind/setup.py:419-420` (`scaffold_vault`), `:441-443` (its return value)
- Modify: `artmind/cli.py:3624` (`init` output)
- Test: `test/test_vault_id.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_vault_id.py`:

```python
"""`vault_id` in `.artmind/vault.yaml` (spec 2026-09-26 §6 A2): minted once by
`artmind init`, committed, and added to an existing vault without disturbing
the rest of the file."""
import uuid

import pytest
from click.testing import CliRunner

from artmind import manifest
from artmind.vault import VaultLayout


def _vault_yaml(root):
    return VaultLayout(root).vault_yaml


def test_scaffold_mints_a_quoted_uuid(tmp_path):
    from artmind.setup import scaffold_vault

    summary = scaffold_vault(tmp_path)

    vault_id = manifest.read_vault_id(tmp_path)
    assert str(uuid.UUID(vault_id)) == vault_id
    assert summary["vault_id"] == vault_id
    assert summary["vault_id_minted"] is True
    assert f'vault_id: "{vault_id}"' in _vault_yaml(tmp_path).read_text()


def test_rescaffolding_keeps_the_id_and_the_file_byte_identical(tmp_path):
    from artmind.setup import scaffold_vault

    first = scaffold_vault(tmp_path)
    before = _vault_yaml(tmp_path).read_bytes()

    second = scaffold_vault(tmp_path)

    assert second["vault_id"] == first["vault_id"]
    assert second["vault_id_minted"] is False
    assert _vault_yaml(tmp_path).read_bytes() == before


def test_an_existing_vault_gets_an_id_appended_and_nothing_else_changes(tmp_path):
    path = _vault_yaml(tmp_path)
    path.parent.mkdir(parents=True)
    original = (
        "# my notes about this vault\n"
        "ingest:\n"
        "  trigger: manual\n"
        "  mappings:\n"
        "    - path: notes/**   # keep this comment\n"
        "      domain: general\n"
    )
    path.write_text(original)

    vault_id, minted = manifest.ensure_vault_id(tmp_path)

    assert minted is True
    text = path.read_text()
    assert text.startswith(original)
    assert text.count("vault_id:") == 1
    assert manifest.read_vault_id(tmp_path) == vault_id
    assert manifest.load(tmp_path).mappings[0].domain == "general"


def test_a_file_without_a_trailing_newline_is_not_glued_to_the_id(tmp_path):
    path = _vault_yaml(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("ingest:\n  trigger: manual")

    vault_id, _ = manifest.ensure_vault_id(tmp_path)

    assert path.read_text().startswith("ingest:\n  trigger: manual\n")
    assert manifest.load(tmp_path).trigger == "manual"
    assert manifest.read_vault_id(tmp_path) == vault_id


def test_an_empty_vault_id_is_refused_not_duplicated(tmp_path):
    path = _vault_yaml(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("vault_id:\n")

    with pytest.raises(manifest.ManifestError, match="present but empty"):
        manifest.ensure_vault_id(tmp_path)
    assert path.read_text() == "vault_id:\n"


def test_read_vault_id_is_none_without_a_manifest_or_a_key(tmp_path):
    assert manifest.read_vault_id(tmp_path) is None
    _vault_yaml(tmp_path).parent.mkdir(parents=True)
    _vault_yaml(tmp_path).write_text("ingest:\n  trigger: manual\n")
    assert manifest.read_vault_id(tmp_path) is None


def test_init_prints_the_vault_id(tmp_path, monkeypatch):
    from artmind.cli import cli

    monkeypatch.chdir(tmp_path)

    first = CliRunner().invoke(cli, ["init"])
    second = CliRunner().invoke(cli, ["init"])

    vault_id = manifest.read_vault_id(tmp_path)
    assert first.exit_code == 0, first.output
    assert f"Vault id: {vault_id} (new" in first.output
    assert f"Vault id: {vault_id}\n" in second.output
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_id.py -q`
Expected: `7 failed`. `AttributeError: module 'artmind.manifest' has no attribute 'read_vault_id'` (or `'ensure_vault_id'`), and `KeyError: 'vault_id'` from `scaffold_vault`'s summary.

- [ ] **Step 3: Implement**

**Replace in** `artmind/manifest.py` (lines 19-20):

```python
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
```

**with:**

```python
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
```


**Append to** `artmind/manifest.py` (after its last line, leaving two blank lines):

```python
# ── vault_id (spec 2026-09-26 §6 A2) ─────────────────────────────────────────

#: Appended to vault.yaml by `ensure_vault_id` -- as text, so the user's own
#: comments, mappings and layout are never re-serialised. Quoted, so YAML can
#: never read the id as a number.
_VAULT_ID_BLOCK = """
# Names this vault in the graph's `vault sync` bookmark, so two vaults that
# share one Neo4j never share a bookmark. Minted once by `artmind init` and
# committed: every clone of this vault carries the same value. Do not edit it,
# and do not copy it into another vault.
vault_id: "{vault_id}"
"""


def _read_yaml_dict(path: Path) -> dict | None:
    """vault.yaml parsed, `{}` when empty, None when absent."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: not valid YAML -- {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: expected a mapping at the top level")
    return data


def read_vault_id(vault_root: Path) -> str | None:
    """This vault's `vault_id`, or None when vault.yaml has none yet (a vault
    initialised before `vault sync` kept its bookmark in the graph)."""
    data = _read_yaml_dict(Path(vault_root) / MARKER / MANIFEST)
    if not data or data.get("vault_id") in (None, ""):
        return None
    return str(data["vault_id"])


def ensure_vault_id(vault_root: Path) -> tuple[str, bool]:
    """`(vault_id, minted)`. Mints a new id into vault.yaml only when it has
    none, by appending one block to the end of the file -- nothing else in
    the file changes. Idempotent: an existing id is returned as-is.

    A `vault_id:` key that is present but empty is refused rather than
    overwritten: appending a second key would leave YAML's duplicate-key
    behaviour to decide which one counts."""
    path = Path(vault_root) / MARKER / MANIFEST
    data = _read_yaml_dict(path)
    if data and "vault_id" in data:
        if data["vault_id"] in (None, ""):
            raise ManifestError(
                f"{path}: 'vault_id' is present but empty -- delete that line and re-run "
                "`artmind init` to mint one, or restore the value another clone of this vault has"
            )
        return str(data["vault_id"]), False
    vault_id = str(uuid.uuid4())
    raw = path.read_text(encoding="utf-8") if path.is_file() else ""
    if raw and not raw.endswith("\n"):
        raw += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw + _VAULT_ID_BLOCK.format(vault_id=vault_id), encoding="utf-8")
    return vault_id, True
```


**Replace in** `artmind/setup.py` (lines 419-420):

```python
    if not layout.vault_yaml.exists():
        layout.vault_yaml.write_text(_STARTER_VAULT_YAML, encoding="utf-8")
```

**with:**

```python
    if not layout.vault_yaml.exists():
        layout.vault_yaml.write_text(_STARTER_VAULT_YAML, encoding="utf-8")
    # Spec 2026-09-26 §6 A2: minted once, committed, and added to a vault
    # initialised before it existed -- appended, the rest of the file untouched.
    from artmind.manifest import ensure_vault_id

    vault_id, vault_id_minted = ensure_vault_id(root)
```


**Replace in** `artmind/setup.py` (lines 441-443):

```python
        "machine_config": machine_config,
        "git_remote": git_remote_status,
    }
```

**with:**

```python
        "machine_config": machine_config,
        "git_remote": git_remote_status,
        "vault_id": vault_id,
        "vault_id_minted": vault_id_minted,
    }
```


`init` prints it, with a reminder on first mint (a mocked `scaffold_vault` in older tests returns no `vault_id`, hence `.get`):

**Replace in** `artmind/cli.py` (line 3624):

```python
    click.echo(f"Manifest: {root / '.artmind' / 'vault.yaml'}")
```

**with:**

```python
    click.echo(f"Manifest: {root / '.artmind' / 'vault.yaml'}")
    vault_id = summary.get("vault_id")
    if vault_id and summary.get("vault_id_minted"):
        click.echo(
            f"Vault id: {vault_id} (new -- let Obsidian Git commit "
            ".artmind/vault.yaml before running `artmind init` in another clone)"
        )
    elif vault_id:
        click.echo(f"Vault id: {vault_id}")
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_id.py test/test_vault_scaffold.py test/test_vault_cli.py test/test_manifest.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2362 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/manifest.py artmind/setup.py artmind/cli.py test/test_vault_id.py
git commit -m "feat(vault): mint a committed vault_id in vault.yaml" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: per-store bookmarks — graph in Neo4j, structured in `state.json`, legacy cursor migrated (spec §6 A2)

`vault sync` stops using the single `last_synced_commit` and keeps one bookmark per store:

- **Reading.** `read_bookmarks` returns the graph's own bookmark (`sync_state.read_graph_bookmark(vault_id)`, only when the graph is in the run), `last_structured_commit` from `state.json`, and the legacy `last_synced_commit`. `Bookmarks.effective(store)` is a store's own bookmark, else the legacy cursor.
- **Migration conflict rule.** The graph's own bookmark always wins over a differing legacy cursor. On a shared AuraDB the other machine set it and it describes the store itself; the legacy cursor only records what *this* machine last applied. Starting the graph from a bookmark that is too old re-applies idempotent work (and Task 5's fingerprints make that cheap); starting it from one that is too new would skip work. For DuckDB the legacy cursor is exactly right: it is this machine's record of what it applied locally. The legacy key is removed by the first run after which both stores have their own bookmark.
- **Ranges.** Each store applies `bookmark..HEAD`. The graph gets the full `classify_diff`. The structured store reuses the graph plan's tables when both bases are equal (git is read once) and otherwise runs only `_classify_structured_text_diff` from its own base (one scoped `git diff`). A table the graph projects is always restored into DuckDB first — `table2graph` reads DuckDB — so the restore set is `project_tables` plus the structured-only extras.
- **Ancestry check.** A bookmark that is not an ancestor of HEAD is refused before anything is applied. On a shared graph the other machine may have synced commits this clone has not pulled; diffing from that bookmark would read the other machine's new documents as removals and retract them from the shared graph.
- **`--store graph|structured`** runs one store alone (apply and bookmark). `--bootstrapEmpty`/`--bootstrapSynced` apply to every store in the run, so `--store structured --bootstrapEmpty` bootstraps DuckDB on a machine whose shared graph already has a bookmark. A structured-only run never connects to Neo4j.
- **`vault_id`.** A run that includes the graph needs the vault's `vault_id` (Task 3) and refuses without it.
- **Unchanged:** a `--domain`-scoped run advances neither bookmark (Plan A's rule); bookmarks only advance on full success.

The result dict reports `stores`, `bases` (per store), `<store>_bookmark` for each store in the run, and keeps `cursor_advanced`/`cursor_would_advance` (now meaning "the bookmarks of this run's stores advanced"); `last_synced_commit` and `base` are gone from it.

**Test seam.** Every test in `test/test_vault_sync.py` now gets an autouse `graph` fixture: it writes `.artmind/vault.yaml` with a known `vault_id` into `tmp_path` (which is the `repo`), and swaps `sync_state`'s three graph functions for an in-memory `_FakeGraphState` — the state one Neo4j keeps between runs. Existing tests that seed `write_state(..., {"last_synced_commit": base})` keep working unchanged: they now exercise the migration path. Existing assertions on `state.json`'s `last_synced_commit` after a *successful* run become assertions on both stores' own bookmarks (`_bookmarks(repo)`); assertions after a *failed* run are unchanged (nothing is written).

**Files:**
- Modify: `test/test_vault_sync.py:29-32` (add the fixture after `_no_worker`), `:594-599`, `:677-680`, `:694-698`, `:708-711`, `:719-721`, `:848-851`, `:1533-1535`, and append
- Modify: `test/test_vault_cli.py:222-223`, `test/test_vault.py:189`
- Modify: `artmind/vault.py:461-466` (`read_state`'s docstring), `:480-488` (`write_state`)
- Modify: `artmind/vault_sync.py:657-699` (bookmark helpers + the start of `sync`), `:702-703`, `:712-713`, `:728-737` (dry run), `:758-770` (track B), `:830-871` (the end of `sync`)
- Modify: `artmind/cli.py:3430-3450` (`vault sync` options), `:3496-3498` (the `sync()` call)

- [ ] **Step 1: Retarget the existing tests and write the failing new ones**

Add the fake graph state and the `vault_id` fixture right after `_no_worker`:

**Replace in** `test/test_vault_sync.py` (lines 29-32):

```python
@pytest.fixture(autouse=True)
def _no_worker(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
```

**with:**

```python
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
```


Retarget the assertions on the old cursor. In `test_sync_bootstrap_synced_stamps_the_cursor_without_replaying`:

**Replace in** `test/test_vault_sync.py` (lines 594-599):

```python
    from artmind.vault import VaultLayout, read_state

    result = vs.sync(repo, bootstrap_synced=True)

    assert result["last_synced_commit"] == vs.head_sha(repo)
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)
```

**with:**

```python
    result = vs.sync(repo, bootstrap_synced=True)

    head = vs.head_sha(repo)
    assert (result["graph_bookmark"], result["structured_bookmark"]) == (head, head)
    assert _bookmarks(repo) == (head, head)
```


In `test_sync_replays_an_added_document_folder_and_advances_the_cursor`:

**Replace in** `test/test_vault_sync.py` (lines 677-680):

```python
    assert result["last_synced_commit"] == vs.head_sha(repo)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)
```

**with:**

```python
    head = vs.head_sha(repo)
    assert (result["graph_bookmark"], result["structured_bookmark"]) == (head, head)
    assert _bookmarks(repo) == (head, head)
```


In `test_domain_scoped_sync_does_not_advance_the_cursor`, three places:

**Replace in** `test/test_vault_sync.py` (lines 694-698):

```python
    first = vs.sync(repo, bootstrap_empty=True)
    assert first["cursor_advanced"] is True
    assert first["last_synced_commit"] == vs.head_sha(repo)
    from artmind.vault import VaultLayout, read_state
    checkpoint = vs.head_sha(repo)
```

**with:**

```python
    first = vs.sync(repo, bootstrap_empty=True)
    assert first["cursor_advanced"] is True
    checkpoint = vs.head_sha(repo)
    assert _bookmarks(repo) == (checkpoint, checkpoint)
```


**Replace in** `test/test_vault_sync.py` (lines 708-711):

```python
    assert scoped["cursor_advanced"] is False
    assert scoped["last_synced_commit"] == checkpoint
    assert "note" in scoped and scoped["note"]
    assert read_state(VaultLayout(repo))["last_synced_commit"] == checkpoint
```

**with:**

```python
    assert scoped["cursor_advanced"] is False
    assert (scoped["graph_bookmark"], scoped["structured_bookmark"]) == (checkpoint, checkpoint)
    assert "note" in scoped and scoped["note"]
    assert _bookmarks(repo) == (checkpoint, checkpoint)
```


**Replace in** `test/test_vault_sync.py` (lines 719-721):

```python
    assert full["cursor_advanced"] is True
    assert full["last_synced_commit"] == vs.head_sha(repo)
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)
```

**with:**

```python
    assert full["cursor_advanced"] is True
    assert _bookmarks(repo) == (vs.head_sha(repo), vs.head_sha(repo))
```


In `test_sync_retry_after_a_failure_converges_to_the_same_end_state`:

**Replace in** `test/test_vault_sync.py` (lines 848-851):

```python
    result = vs.sync(repo, bootstrap_empty=True)

    assert result["last_synced_commit"] == expected_head
    assert read_state(VaultLayout(repo))["last_synced_commit"] == expected_head
```

**with:**

```python
    result = vs.sync(repo, bootstrap_empty=True)

    assert result["graph_bookmark"] == expected_head
    assert _bookmarks(repo) == (expected_head, expected_head)
```


In `test_sync_restores_an_unmapped_table_to_the_structured_store_only`:

**Replace in** `test/test_vault_sync.py` (lines 1533-1535):

```python
    assert result["structured_only_tables"] == ["banking/accounts"]
    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)
```

**with:**

```python
    assert result["structured_only_tables"] == ["banking/accounts"]
    assert _bookmarks(repo) == (vs.head_sha(repo), vs.head_sha(repo))
```


Every other `last_synced_commit` in this file is either a `write_state` seed (now the migration path) or an assertion after a failed run (still true: nothing is written). Leave them.

Append the new tests:

**Append to** `test/test_vault_sync.py` (after its last line, leaving two blank lines):

```python
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
```


In `test/test_vault_cli.py`, `test_vault_sync_bootstrap_synced_stamps_the_cursor` asserts on the old key; retarget it and add a `--store` test after it:

**Replace in** `test/test_vault_cli.py` (lines 222-223):

```python
    assert payload["bootstrap"] == "synced"
    assert "last_synced_commit" in payload
```

**with:**

```python
    assert payload["bootstrap"] == "synced"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert payload["graph_bookmark"] == head
    assert payload["structured_bookmark"] == head
    state = vault.read_state(vault.VaultLayout(tmp_path))
    assert state == {"last_structured_commit": head}


def test_vault_sync_store_option_reaches_sync(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(
        cli, ["vault", "sync", "--store", "structured", "--bootstrapSynced", "--compact"]
    )

    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["stores"] == ["structured"]
    assert "graph_bookmark" not in payload
```


(These CLI tests run the real `init` — which now mints a `vault_id` — against conftest's null Neo4j session: the graph bookmark reads as absent and its write is a no-op.)

In `test/test_vault.py`, before `test_read_state_tolerates_corrupt_json`:

**Replace in** `test/test_vault.py` (line 189):

```python
def test_read_state_tolerates_corrupt_json(tmp_path):
```

**with:**

```python
def test_write_state_removes_named_keys_and_keeps_the_rest(tmp_path):
    layout = vault.VaultLayout(tmp_path)
    vault.write_state(layout, {"last_synced_commit": "old", "last_ingested_commit": "keep"})

    vault.write_state(layout, {"last_structured_commit": "new"}, remove=("last_synced_commit",))

    assert vault.read_state(layout) == {"last_ingested_commit": "keep", "last_structured_commit": "new"}


def test_read_state_tolerates_corrupt_json(tmp_path):
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py test/test_vault_cli.py test/test_vault.py -q`
Expected: `19 failed`. Among them: `TypeError: write_state() got an unexpected keyword argument 'remove'`; `KeyError: 'graph_bookmark'` / `KeyError: 'bases'` from the retargeted and new assertions; `AttributeError: module 'artmind.vault_sync' has no attribute 'read_bookmarks'`; `TypeError: sync() got an unexpected keyword argument 'store'`; the old `no last_synced_commit recorded yet` refusal where a graph bookmark now suffices; `Regex pattern did not match` for the new refusals. The other 127 already pass.

- [ ] **Step 3: Implement**

`state.json` gains a way to drop a key:

**Replace in** `artmind/vault.py` (lines 461-466):

```python
def read_state(layout: VaultLayout) -> dict:
    """The machine-local cursor file (`state.json`), or `{}` if absent.

    Several independent cursors share this one file -- `last_ingested_commit`
    (documented, not yet implemented) and `last_synced_commit` (`vault
    sync`'s own cursor) -- see `VaultLayout.state_json`'s docstring. Corrupt
```

**with:**

```python
def read_state(layout: VaultLayout) -> dict:
    """The machine-local cursor file (`state.json`), or `{}` if absent.

    Several independent cursors share this one file -- `last_ingested_commit`
    (documented, not yet implemented), `last_structured_commit` (`vault
    sync`'s structured-store bookmark; the graph's lives in the graph) and,
    until a vault's first sync after the bookmark split, the retired
    `last_synced_commit` -- see `VaultLayout.state_json`'s docstring. Corrupt
```


**Replace in** `artmind/vault.py` (lines 480-488):

```python
def write_state(layout: VaultLayout, updates: dict) -> None:
    """Merge `updates` into `state.json`, preserving every other key already
    there. Multiple cursors coexist in this one file (see `read_state`) --
    a naive whole-file overwrite would silently erase whichever this call
    doesn't mention.
    """
    path = layout.state_json
    state = read_state(layout)
    state.update(updates)
```

**with:**

```python
def write_state(layout: VaultLayout, updates: dict, *, remove: tuple[str, ...] = ()) -> None:
    """Merge `updates` into `state.json`, preserving every other key already
    there, and drop the keys named in `remove`. Multiple cursors coexist in
    this one file (see `read_state`) -- a naive whole-file overwrite would
    silently erase whichever this call doesn't mention.
    """
    path = layout.state_json
    state = read_state(layout)
    state.update(updates)
    for key in remove:
        state.pop(key, None)
```


In `artmind/vault_sync.py`, replace the start of `sync()` — from `def sync(` through the `classify_diff` call — with the bookmark helpers and the new start:

**Replace in** `artmind/vault_sync.py` (lines 657-699):

```python
def sync(
    vault_dir: Path,
    *,
    domains: list[str] | None = None,
    bootstrap_empty: bool = False,
    bootstrap_synced: bool = False,
    dry_run: bool = False,
) -> dict:
    """Replay committed KG-staging and structured-text changes into
    Neo4j/DuckDB since the last sync (spec §5). All-or-nothing: the cursor
    only advances on full success (§9); any exception here propagates
    unchanged, leaving `last_synced_commit` untouched so the next
    invocation recomputes the identical diff_range from scratch.
    """
    from artmind.vault import VaultLayout, read_state, write_state

    if bootstrap_empty and bootstrap_synced:
        raise VaultSyncError("pass at most one of --bootstrapEmpty / --bootstrapSynced")
    if bootstrap_synced and domains:
        raise VaultSyncError("--bootstrapSynced stamps the whole vault; it can't be domain-scoped")

    layout = VaultLayout(vault_dir)
    state = read_state(layout)
    last_synced = state.get("last_synced_commit")
    head = head_sha(vault_dir)
    preflight(vault_dir)

    if bootstrap_synced:
        if dry_run:
            return {"bootstrap": "synced", "dry_run": True, "would_stamp": head}
        write_state(layout, {"last_synced_commit": head})
        return {"bootstrap": "synced", "last_synced_commit": head}

    if last_synced is None and not bootstrap_empty:
        raise VaultSyncError(
            "no last_synced_commit recorded yet -- pass --bootstrapEmpty for a full "
            "replay of everything (correct for an empty Neo4j/DuckDB), or "
            "--bootstrapSynced if this machine's graph is already known-current "
            "(e.g. right after `session initiate`/`db restore`)"
        )
    base = EMPTY_TREE_SHA if bootstrap_empty else last_synced

    plan = classify_diff(vault_dir, base, head, domains)
```

**with:**

```python
# ── bookmarks (spec 2026-09-26 §6 A2) ────────────────────────────────────────

#: The stores `vault sync` applies to. Each has its own bookmark ("this store
#: reflects commits up to X"), because a shared AuraDB and a local DuckDB can
#: be at different commits.
STORES = ("graph", "structured")

#: `state.json` key of the structured store's bookmark. DuckDB is always a
#: local file, so its bookmark is machine-local too. The graph's lives in the
#: graph (`artmind.sync_state`).
STRUCTURED_BOOKMARK_KEY = "last_structured_commit"

#: The single pre-bookmark cursor. Read as the starting point of any store
#: that has no bookmark of its own yet; removed from `state.json` by the first
#: run after which both stores have one.
LEGACY_CURSOR_KEY = "last_synced_commit"


@dataclass
class Bookmarks:
    """Each store's own bookmark as read, plus the legacy cursor."""

    graph: str | None = None
    structured: str | None = None
    legacy: str | None = None

    def own(self, store: str) -> str | None:
        return self.graph if store == "graph" else self.structured

    def effective(self, store: str) -> str | None:
        """Where `store` starts from: its own bookmark, else the legacy cursor.

        The graph's own bookmark always wins over a legacy cursor, even when
        they differ -- on a shared graph the other machine set it, and it
        describes the store itself, whereas the legacy cursor only recorded
        what THIS machine last applied. Starting the graph from a bookmark
        that is too old only re-applies idempotent work (fingerprints make it
        cheap); starting it from one that is too new would skip work. The
        legacy cursor remains exactly right for this machine's DuckDB."""
        own = self.own(store)
        return own if own is not None else self.legacy


def vault_id_or_raise(vault_dir: Path) -> str:
    """This vault's `vault_id` (`.artmind/vault.yaml`), which keys the graph
    bookmark. Raises when the vault predates it."""
    from artmind.manifest import read_vault_id

    vault_id = read_vault_id(vault_dir)
    if vault_id is None:
        raise VaultSyncError(
            "this vault has no vault_id in .artmind/vault.yaml yet -- run `artmind init` "
            "once (it appends one and changes nothing else), let Obsidian Git commit it, "
            "then re-run `vault sync`"
        )
    return vault_id


def read_bookmarks(
    vault_dir: Path, *, vault_id: str | None, stores: tuple[str, ...] = STORES, timeout: float | None = None
) -> Bookmarks:
    """Both stores' bookmarks and the legacy cursor. The graph is read only
    when `"graph"` is in `stores` -- a structured-only run never needs Neo4j."""
    from artmind import sync_state
    from artmind.vault import VaultLayout, read_state

    state = read_state(VaultLayout(vault_dir))
    graph = sync_state.read_graph_bookmark(vault_id, timeout=timeout) if "graph" in stores else None
    return Bookmarks(
        graph=graph,
        structured=state.get(STRUCTURED_BOOKMARK_KEY),
        legacy=state.get(LEGACY_CURSOR_KEY),
    )


def check_bookmark(vault_dir: Path, store: str, commit: str, head: str) -> None:
    """Refuse a bookmark that is not an ancestor of `head`.

    Diffing from a commit that is not in HEAD's history would read everything
    the bookmark has and HEAD lacks as removals: on a shared graph, where the
    other machine set the bookmark past this clone, that retracts the other
    machine's documents. Pulling first (Obsidian Git merges) makes the
    bookmark an ancestor again."""
    rc, _, _ = run_command(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=vault_dir, expected_codes=(1, 128)
    )
    if rc != 0:
        if store == "graph":
            raise VaultSyncError(
                f"the graph bookmark {commit[:12]} is not a commit in this clone -- another "
                "machine sharing this graph has applied commits this clone has not pulled "
                "yet. Let Obsidian Git pull, then re-run `vault sync`."
            )
        raise VaultSyncError(
            f"the structured bookmark {commit[:12]} is not a commit in this clone (history "
            "was rewritten) -- re-run with `--store structured --bootstrapEmpty`"
        )
    rc, _, _ = run_command(
        ["git", "merge-base", "--is-ancestor", commit, head], cwd=vault_dir, expected_codes=(1,)
    )
    if rc != 0:
        raise VaultSyncError(
            f"the {store} bookmark {commit[:12]} is not an ancestor of HEAD {head[:12]} -- "
            "this clone is behind (or has diverged from) what that store already reflects. "
            "Let Obsidian Git pull and merge, then re-run `vault sync`."
        )


def _no_bookmark_message(missing: list[str], stores: tuple[str, ...]) -> str:
    message = (
        f"no {' or '.join(missing)} bookmark recorded yet for this vault -- pass "
        "--bootstrapEmpty to replay everything committed (correct for an empty "
        "Neo4j/DuckDB), or --bootstrapSynced if it is already known-current (e.g. "
        "right after `session initiate`/`db restore`)"
    )
    if len(missing) == 1 and len(stores) > 1:
        message += f"; add `--store {missing[0]}` to bootstrap that store alone"
    return message


def _advance_bookmarks(
    vault_dir: Path, vault_id: str | None, stores: tuple[str, ...], commit: str, marks: Bookmarks
) -> Bookmarks:
    """Move every store in `stores` to `commit`: the graph's node first, then
    `state.json`. Retires the legacy cursor once both stores have their own."""
    from artmind import sync_state
    from artmind.vault import VaultLayout, write_state

    new = Bookmarks(graph=marks.graph, structured=marks.structured, legacy=marks.legacy)
    if "graph" in stores:
        sync_state.write_graph_bookmark(vault_id, commit)
        new.graph = commit
    updates: dict = {}
    if "structured" in stores:
        updates[STRUCTURED_BOOKMARK_KEY] = commit
        new.structured = commit
    remove: tuple[str, ...] = ()
    if new.legacy is not None and new.graph is not None and new.structured is not None:
        remove = (LEGACY_CURSOR_KEY,)
        new.legacy = None
    if updates or remove:
        write_state(VaultLayout(vault_dir), updates, remove=remove)
    return new


def _bookmark_fields(marks: Bookmarks, stores: tuple[str, ...]) -> dict:
    return {f"{store}_bookmark": marks.effective(store) for store in stores}


def sync(
    vault_dir: Path,
    *,
    domains: list[str] | None = None,
    bootstrap_empty: bool = False,
    bootstrap_synced: bool = False,
    dry_run: bool = False,
    store: str | None = None,
) -> dict:
    """Replay committed KG-staging and structured-text changes into
    Neo4j/DuckDB since each store's bookmark (spec §5, spec 2026-09-26 §6 A2).

    `store` limits the run -- apply and bookmark -- to `"graph"` or
    `"structured"`; None runs both. Each store applies its own range
    (`bookmark..HEAD`); a graph ahead of the structured store (or the reverse)
    is normal. The bootstrap flags apply to every store in the run.

    All-or-nothing: bookmarks only advance on full success (§9); any
    exception propagates unchanged, leaving them untouched, so the next
    invocation recomputes the identical ranges. A `--domain`-scoped run never
    advances a bookmark."""
    if bootstrap_empty and bootstrap_synced:
        raise VaultSyncError("pass at most one of --bootstrapEmpty / --bootstrapSynced")
    if bootstrap_synced and domains:
        raise VaultSyncError("--bootstrapSynced stamps the whole vault; it can't be domain-scoped")
    if store is not None and store not in STORES:
        raise VaultSyncError(f"unknown store {store!r} -- one of: {', '.join(STORES)}")
    stores = (store,) if store else STORES

    head = head_sha(vault_dir)
    preflight(vault_dir)
    vault_id = vault_id_or_raise(vault_dir) if "graph" in stores else None
    marks = read_bookmarks(vault_dir, vault_id=vault_id, stores=stores)

    if bootstrap_synced:
        if dry_run:
            return {"bootstrap": "synced", "dry_run": True, "would_stamp": head, "stores": list(stores)}
        new = _advance_bookmarks(vault_dir, vault_id, stores, head, marks)
        return {"bootstrap": "synced", "stores": list(stores), **_bookmark_fields(new, stores)}

    bases: dict[str, str] = {}
    missing: list[str] = []
    for name in stores:
        base = EMPTY_TREE_SHA if bootstrap_empty else marks.effective(name)
        if base is None:
            missing.append(name)
        else:
            bases[name] = base
    if missing:
        raise VaultSyncError(_no_bookmark_message(missing, stores))
    for name, base in bases.items():
        if base != EMPTY_TREE_SHA:
            check_bookmark(vault_dir, name, base, head)

    # One classification per distinct base: the graph needs the full plan;
    # the structured store needs only its tables. When both stores start from
    # the same commit (the common case) the graph plan's tables ARE the
    # structured store's, so git is read once.
    if "graph" in stores:
        plan = classify_diff(vault_dir, bases["graph"], head, domains)
    else:
        plan = SyncPlan(base=bases["structured"], head=head)
    project_tables = list(plan.regenerate_tables)
    if "structured" not in stores:
        structured_tables: list[tuple[str, str]] = []
    elif "graph" in stores and bases["structured"] == bases["graph"]:
        structured_tables = list(project_tables)
    else:
        structured_tables, _ = _classify_structured_text_diff(vault_dir, bases["structured"], head, domains)
    # A projected table is restored too, so table2graph reads DuckDB at
    # `head` whatever the structured bookmark says (idempotent).
    restore_tables = project_tables + [key for key in structured_tables if key not in project_tables]
```


The dry run checks mappings only for the tables the graph will project:

**Replace in** `artmind/vault_sync.py` (lines 702-703):

```python
        structured_only_dry: list[str] = []
        if plan.regenerate_tables:
```

**with:**

```python
        structured_only_dry: list[str] = []
        if project_tables:
```


**Replace in** `artmind/vault_sync.py` (lines 712-713):

```python
                for domain, table_name in plan.regenerate_tables:
                    try:
```

**with:**

```python
                for domain, table_name in project_tables:
                    try:
```


**Replace in** `artmind/vault_sync.py` (lines 728-737):

```python
        return {
            "dry_run": True,
            "base": base,
            "head": head,
            "replay": len(plan.replay_docs),
            "retract": len(plan.retract),
            "regenerate_tables": len(plan.regenerate_tables),
            "structured_only_tables": structured_only_dry,
            "cursor_would_advance": not domains,
        }
```

**with:**

```python
        return {
            "dry_run": True,
            "stores": list(stores),
            "bases": bases,
            "head": head,
            "replay": len(plan.replay_docs),
            "retract": len(plan.retract),
            "regenerate_tables": len(restore_tables),
            "structured_only_tables": structured_only_dry,
            "cursor_would_advance": not domains,
        }
```


Track B restores `restore_tables` and projects only `project_tables`:

**Replace in** `artmind/vault_sync.py` (lines 758-770):

```python
        if plan.regenerate_tables:
            st_rel = STRUCTURED_TEXT_DIR.relative_to(vault_dir)
            wanted = [str(st_rel / MANIFEST_NAME)]
            for domain, table_name in plan.regenerate_tables:
                wanted.append(str(st_rel / domain / f"{table_name}.csv"))
                wanted.append(str(st_rel / domain / f"{table_name}{META_SUFFIX}"))
            _materialize(vault_dir, head, _present_at(vault_dir, head, wanted), scratch)
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            import_structured_text(scratch / st_rel, tables=plan.regenerate_tables)
            mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, scratch)
            schemas_at_head = _dir_at(vault_dir, head, paths.DOMAIN_SCHEMAS_DIR, scratch)
            for domain, table_name in plan.regenerate_tables:
                row = structured_registry.get_table(table_name, domain=domain)
```

**with:**

```python
        if restore_tables:
            st_rel = STRUCTURED_TEXT_DIR.relative_to(vault_dir)
            wanted = [str(st_rel / MANIFEST_NAME)]
            for domain, table_name in restore_tables:
                wanted.append(str(st_rel / domain / f"{table_name}.csv"))
                wanted.append(str(st_rel / domain / f"{table_name}{META_SUFFIX}"))
            _materialize(vault_dir, head, _present_at(vault_dir, head, wanted), scratch)
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            import_structured_text(scratch / st_rel, tables=restore_tables)
        if project_tables:
            mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, scratch)
            schemas_at_head = _dir_at(vault_dir, head, paths.DOMAIN_SCHEMAS_DIR, scratch)
            for domain, table_name in project_tables:
                row = structured_registry.get_table(table_name, domain=domain)
```


And the end of `sync()` advances the bookmarks instead of the cursor:

**Replace in** `artmind/vault_sync.py` (lines 830-871):

```python
    # ── only on full success, advance the cursor (§5 step 7) ────────────────
    # A `--domain`-scoped run only classified and applied ITS domain(s) --
    # writing the single `last_synced_commit` anyway would make every other
    # domain's change in base..head permanently unapplied on this machine
    # (item 3, vault-sync-completion review): the next unscoped sync would
    # start its diff from `head`, never seeing them. Leave the cursor where
    # it was so a later unscoped sync still covers this same range; re-
    # applying this domain's changes then is a no-op-shaped replay, not a
    # problem (track A/B/§7 are all idempotent by id/key).
    cursor_advanced = not domains
    if cursor_advanced:
        write_state(layout, {"last_synced_commit": head})

    result = {
        "base": base,
        "last_synced_commit": head if cursor_advanced else last_synced,
        "cursor_advanced": cursor_advanced,
        "replayed": len(plan.replay_docs),
        "retracted": len(plan.retract),
        "regenerated_tables": len(plan.regenerate_tables),
        "structured_only_tables": structured_only,
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
    if not cursor_advanced:
        # `last_synced` is the cursor as it stood at the START of this run
        # (read before `base` was computed) -- when it was None, this run
        # only got past the "no last_synced_commit recorded yet" check
        # because --bootstrapEmpty was passed. An unscoped follow-up run
        # with no flag would hit that same check and refuse, so the note
        # must say to repeat --bootstrapEmpty, not just "a later sync"
        # (re-review item 3).
        follow_up = (
            "re-run `vault sync --bootstrapEmpty` (unscoped) to apply them"
            if last_synced is None
            else "a later unscoped `vault sync` will apply them (and idempotently re-apply this one)"
        )
        result["note"] = (
            f"domain-scoped sync (--domain {','.join(domains)}): applied but did not advance "
            f"last_synced_commit -- other domains changed in this range are still pending; {follow_up}"
        )
    return result
```

**with:**

```python
    # ── only on full success, advance the bookmarks (§5 step 7) ─────────────
    # A `--domain`-scoped run only classified and applied ITS domain(s) --
    # advancing a bookmark anyway would make every other domain's change in
    # base..head permanently unapplied (item 3, vault-sync-completion review):
    # the next unscoped sync would start its diff from `head`, never seeing
    # them. Leave both where they were so a later unscoped sync still covers
    # this same range; re-applying this domain's changes then is a
    # no-op-shaped replay, not a problem (track A/B/§7 are idempotent by id/key).
    cursor_advanced = not domains
    final = _advance_bookmarks(vault_dir, vault_id, stores, head, marks) if cursor_advanced else marks

    result = {
        "stores": list(stores),
        "bases": bases,
        "head": head,
        **_bookmark_fields(final, stores),
        "cursor_advanced": cursor_advanced,
        "replayed": len(plan.replay_docs),
        "retracted": len(plan.retract),
        "regenerated_tables": len(restore_tables),
        "structured_only_tables": structured_only,
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
    if not cursor_advanced:
        # A store with no bookmark at the START of this run only got past the
        # "no bookmark recorded yet" check because --bootstrapEmpty was
        # passed. An unscoped follow-up with no flag would hit that same check
        # and refuse, so the note must say to repeat --bootstrapEmpty, not just
        # "a later sync" (re-review item 3).
        follow_up = (
            "re-run `vault sync --bootstrapEmpty` (unscoped) to apply them"
            if any(marks.effective(name) is None for name in stores)
            else "a later unscoped `vault sync` will apply them (and idempotently re-apply this one)"
        )
        result["note"] = (
            f"domain-scoped sync (--domain {','.join(domains)}): applied but did not advance "
            f"the {' or '.join(stores)} bookmark -- other domains changed in this range are "
            f"still pending; {follow_up}"
        )
    return result
```


In `artmind/cli.py`, add `--store` and pass it through:

**Replace in** `artmind/cli.py` (lines 3430-3450):

```python
@click.option(
    "--bootstrapEmpty", "bootstrap_empty", is_flag=True,
    help="First sync ever: replay everything committed, from the vault's very first commit. "
    "Slow for a large vault, but correct for a genuinely empty Neo4j/DuckDB.",
)
@click.option(
    "--bootstrapSynced", "bootstrap_synced", is_flag=True,
    help="Stamp the cursor at HEAD with no replay at all -- for right after a full "
    "`session initiate`/`db restore`, where the graph is already known-current. "
    "Stamps the whole vault; cannot be combined with --domain.",
)
@click.option(
    "--domain", "domain", multiple=True,
    help="Domain(s) to scope the sync (repeatable; comma-splittable). Default: every domain. "
    "A scoped sync applies only that domain's changes and does NOT advance last_synced_commit "
    "-- other domains changed in the same range stay pending, so a later unscoped sync still "
    "sees (and idempotently re-applies) this one too.",
)
@click.option("--dryRun", "dry_run", is_flag=True, help="Report the classified diff (documents to replay/retract, tables to regenerate) without writing anything.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_sync_cmd(bootstrap_empty, bootstrap_synced, domain, dry_run, compact):
```

**with:**

```python
@click.option(
    "--bootstrapEmpty", "bootstrap_empty", is_flag=True,
    help="First sync of a store: replay everything committed, from the vault's very first "
    "commit, into every store in the run (see --store). Slow for a large vault, but correct "
    "for a genuinely empty Neo4j/DuckDB; cheap on a shared graph whose documents already "
    "carry matching fingerprints.",
)
@click.option(
    "--bootstrapSynced", "bootstrap_synced", is_flag=True,
    help="Stamp the bookmark of every store in the run (see --store) at HEAD with no replay "
    "at all -- for right after a full `session initiate`/`db restore`, where that store is "
    "already known-current. Cannot be combined with --domain.",
)
@click.option(
    "--store", "store", type=click.Choice(["graph", "structured"]), default=None,
    help="Apply to, and advance the bookmark of, one store only: `graph` (Neo4j; its "
    "bookmark lives in the graph) or `structured` (DuckDB; its bookmark lives in "
    ".artmind/state.json, and it never connects to Neo4j). Default: both. Use it to "
    "bootstrap a store the other already has a bookmark for.",
)
@click.option(
    "--domain", "domain", multiple=True,
    help="Domain(s) to scope the sync (repeatable; comma-splittable). Default: every domain. "
    "A scoped sync applies only that domain's changes and does NOT advance either bookmark "
    "-- other domains changed in the same range stay pending, so a later unscoped sync still "
    "sees (and idempotently re-applies) this one too.",
)
@click.option("--dryRun", "dry_run", is_flag=True, help="Report the classified diff (documents to replay/retract, tables to regenerate) without writing anything.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_sync_cmd(bootstrap_empty, bootstrap_synced, store, domain, dry_run, compact):
```


**Replace in** `artmind/cli.py` (lines 3496-3498):

```python
            bootstrap_synced=bootstrap_synced,
            dry_run=dry_run,
        )
```

**with:**

```python
            bootstrap_synced=bootstrap_synced,
            dry_run=dry_run,
            store=store,
        )
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py test/test_vault_cli.py test/test_vault.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2376 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault.py artmind/vault_sync.py artmind/cli.py test/test_vault_sync.py test/test_vault_cli.py test/test_vault.py
git commit -m "feat(vault-sync): per-store bookmarks, graph bookmark in Neo4j, --store" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: skip a folder whose committed fingerprint the graph already carries (spec §6 A3, §8)

After classification, `drop_unchanged` computes each replay folder's fingerprint from its **committed blobs** — one `git ls-tree` for all folders, one `git cat-file --batch` for all their blobs, never the working tree and never `git archive` (which applies eol/smudge filters) — reads the graph's fingerprints for all their doc ids in **one** Cypher read, and removes the folders whose fingerprint matches. On a shared AuraDB the ingesting machine already wrote them, so the other machine's apply is a near no-op; on a separate local Neo4j nothing matches and everything is replayed. `_document_ids_at_head`'s own `cat-file` parser moves into a shared `_cat_blobs`/`_document_id` pair (same parsing; its three existing tests pin it). Retractions are not skipped: retracting an absent document is already idempotent. Table projections are not skipped either — `table__*` staging is gitignored, so there is no committed fingerprint to compare.

**Files:**
- Modify: `artmind/vault_sync.py:267-274` (`_document_ids_at_head`'s import), `:306-341` (its `cat-file` block, followed by the new helpers), `:864-867`, `:911-912`, `:1027-1028` (`sync()`)
- Test: `test/test_vault_sync.py` (append)

- [ ] **Step 1: Write the failing tests**

**Append to** `test/test_vault_sync.py` (after its last line, leaving two blank lines):

```python
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
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: `4 failed`. `assert ['doc1', 'doc2'] == ['doc2']` (the matching folder is still replayed), `KeyError: 'unchanged'`, and `write_to_neo4j` called where the graph already had the fingerprint. (`test_a_different_or_missing_fingerprint_is_replayed` and `test_nothing_to_replay_reads_no_fingerprints` already pass: they pin the replay direction.)

- [ ] **Step 3: Implement**

**Replace in** `artmind/vault_sync.py` (lines 267-274):

```python
    """
    import json

    from artmind.atomic_dir import is_scratch

    if not domains:
        return {}
    rel_scopes
```

**with:**

```python
    """
    from artmind.atomic_dir import is_scratch

    if not domains:
        return {}
    rel_scopes
```


**Replace in** `artmind/vault_sync.py` (lines 306-341):

```python
    specs = "".join(f"{oid}\n" for _, _, oid in targets)
    proc = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=vault_dir, input=specs.encode(), capture_output=True,
    )
    if proc.returncode != 0:
        raise VaultSyncError(f"git cat-file --batch failed: {proc.stderr.decode(errors='replace').strip()}")
    out = proc.stdout

    ids_by_domain: dict[str, set[str]] = {}
    pos = 0
    for domain, _docdir, oid in targets:
        nl = out.index(b"\n", pos)
        header = out[pos:nl]
        pos = nl + 1
        fields = header.split(b" ")
        if len(fields) != 3:
            continue
        _oid, typ, size_field = fields
        if typ != b"blob":
            continue
        try:
            size = int(size_field)
        except ValueError:
            continue
        content, pos = out[pos:pos + size], pos + size + 1  # +1: cat-file's own trailing "\n"
        try:
            obj = json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(obj, dict):
            continue  # e.g. a document.json that is a bare list -- not ours to read
        doc_id = obj.get("id")
        if doc_id is None:
            continue
        ids_by_domain.setdefault(domain, set()).add(doc_id)
    return ids_by_domain
```

**with:**

```python
    blobs = _cat_blobs(vault_dir, [oid for _, _, oid in targets])
    ids_by_domain: dict[str, set[str]] = {}
    for domain, _docdir, oid in targets:
        doc_id = _document_id(blobs.get(oid))
        if doc_id is not None:
            ids_by_domain.setdefault(domain, set()).add(doc_id)
    return ids_by_domain


def _cat_blobs(vault_dir: Path, oids: list[str]) -> dict[str, bytes]:
    """`{oid: raw bytes}` for blob `oids`, in ONE `git cat-file --batch`.
    Raw object bytes: no smudge, eol or LFS filter is applied (unlike
    `git archive`/checkout). Callers pass oids `git ls-tree` just listed, so
    "missing" cannot occur; a non-blob answer is skipped."""
    wanted = list(dict.fromkeys(oids))
    if not wanted:
        return {}
    proc = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=vault_dir,
        input="".join(f"{oid}\n" for oid in wanted).encode(), capture_output=True,
    )
    if proc.returncode != 0:
        raise VaultSyncError(f"git cat-file --batch failed: {proc.stderr.decode(errors='replace').strip()}")
    out = proc.stdout
    blobs: dict[str, bytes] = {}
    pos = 0
    for oid in wanted:
        nl = out.index(b"\n", pos)
        fields = out[pos:nl].split(b" ")
        pos = nl + 1
        if len(fields) != 3:
            continue
        _oid, typ, size_field = fields
        try:
            size = int(size_field)
        except ValueError:
            continue
        content, pos = out[pos:pos + size], pos + size + 1  # +1: cat-file's own trailing "\n"
        if typ == b"blob":
            blobs[oid] = content
    return blobs


def _document_id(content: bytes | None) -> str | None:
    """`id` from a document.json's bytes, or None when it has none -- not
    JSON, not UTF-8, or not an object (e.g. a bare list: not ours to read)."""
    import json

    if content is None:
        return None
    try:
        obj = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return obj.get("id") if isinstance(obj, dict) else None


def committed_fingerprints(
    vault_dir: Path, head: str, folders: list[tuple[str, str]]
) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """`{(domain, docdir): (doc_id, fingerprint)}` for KG staging folders as
    committed at `head` -- `sync_state.staging_fingerprint` over the committed
    blobs, the same bytes ingest hashed when it wrote them. One `git ls-tree`
    and one `git cat-file --batch` for every folder together."""
    import paths
    from artmind.sync_state import FINGERPRINTED_FILES, staging_fingerprint

    if not folders:
        return {}
    kg_rel = paths.KG_DIR.relative_to(vault_dir)
    scopes = [str(kg_rel / domain / docdir) for domain, docdir in folders]
    listing = _git(vault_dir, ["ls-tree", "-r", "-z", head, "--", *scopes])
    oid_of: dict[tuple[str, str, str], str] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = Path(path).relative_to(kg_rel).parts
        fields = meta.split(" ")
        if len(parts) == 3 and parts[2] in FINGERPRINTED_FILES and len(fields) == 3 and fields[1] == "blob":
            oid_of[parts] = fields[2]
    blobs = _cat_blobs(vault_dir, list(oid_of.values()))

    result = {}
    for domain, docdir in folders:
        def _read(name, _d=domain, _f=docdir):
            oid = oid_of.get((_d, _f, name))
            return blobs.get(oid) if oid else None

        result[(domain, docdir)] = (_document_id(_read("document.json")), staging_fingerprint(_read))
    return result


def drop_unchanged(
    vault_dir: Path, plan: "SyncPlan", *, timeout: float | None = None
) -> list[tuple[str, str]]:
    """Spec 2026-09-26 §6 A3: remove from `plan.replay_docs` every folder
    whose committed fingerprint the graph's `Document` already carries, and
    return them. ONE Cypher read for the whole plan. On a shared graph the
    machine that ingested a document already wrote it, so this is what makes
    the other machine's apply a near no-op; on a separate local Neo4j nothing
    matches and everything is replayed."""
    from artmind import sync_state

    committed = committed_fingerprints(vault_dir, plan.head, plan.replay_docs)
    doc_ids = [doc_id for doc_id, fp in committed.values() if doc_id is not None and fp is not None]
    if not doc_ids:
        return []
    in_graph = sync_state.read_document_fingerprints(doc_ids, timeout=timeout)
    unchanged, replay = [], []
    for key in plan.replay_docs:
        doc_id, fp = committed.get(key, (None, None))
        if fp is not None and doc_id in in_graph and in_graph[doc_id] == fp:
            unchanged.append(key)
        else:
            replay.append(key)
    plan.replay_docs = replay
    return unchanged
```


In `sync()`, drop the unchanged folders right after classifying, and report how many:

**Replace in** `artmind/vault_sync.py` (lines 864-867):

```python
    if "graph" in stores:
        plan = classify_diff(vault_dir, bases["graph"], head, domains)
    else:
        plan = SyncPlan(base=bases["structured"], head=head)
```

**with:**

```python
    if "graph" in stores:
        plan = classify_diff(vault_dir, bases["graph"], head, domains)
        unchanged = drop_unchanged(vault_dir, plan)
    else:
        plan = SyncPlan(base=bases["structured"], head=head)
        unchanged = []
```


**Replace in** `artmind/vault_sync.py` (lines 911-912):

```python
            "replay": len(plan.replay_docs),
            "retract": len(plan.retract),
```

**with:**

```python
            "replay": len(plan.replay_docs),
            "unchanged": len(unchanged),
            "retract": len(plan.retract),
```


**Replace in** `artmind/vault_sync.py` (lines 1027-1028):

```python
        "replayed": len(plan.replay_docs),
        "retracted": len(plan.retract),
```

**with:**

```python
        "replayed": len(plan.replay_docs),
        "unchanged": len(unchanged),
        "retracted": len(plan.retract),
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: all pass (including the three `_document_ids_at_head` tests).

Run: `uv run --group dev pytest test/ -q`
Expected: `2382 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault-sync): skip documents whose committed fingerprint the graph already has" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: SKIPPED — do not pin bookmarks under `refs/artmind/`

**This task is intentionally not implemented.** The original plan for this task pinned each
advanced bookmark with `git update-ref refs/artmind/last-synced/<store> <sha>`, reasoning that
a rebase or reset followed by `git gc` could make an old bookmark's commit unreachable. On
review this was dropped:

- **Spec D2 says artmind only *reads* git** (`rev-parse`, `diff`, `show`, `cat-file`, `status`) —
  never a write, including `update-ref` on a ref of its own. A private ref is still a write to
  `.git`, which is exactly what D1/D2 exist to rule out.
- **The pin was redundant.** Obsidian Git *merges*, never rebases (D5); a bookmarked commit stays
  reachable through ordinary history without anything pinning it.
- **A history rewrite is already caught.** Decision 6 (`check_bookmark`, Task 4) refuses `vault
  sync` whenever a store's bookmark is not an ancestor of `HEAD` — whether or not that commit was
  pinned. The refusal names the recovery directly: `--store <name> --bootstrapSynced` if that
  store is already known-current, or `--store <name> --bootstrapEmpty` to replay everything
  committed (see the Task 4 follow-up fix that added this wording to the refusal message).

`test/test_no_git_writes.py` keeps **no** `update-ref` exception — it still fails the suite on
any git write from `artmind/vault_sync.py` (or any other module under `artmind/`/`utils/`),
`update-ref` included. Nothing in this codebase writes `refs/artmind/*`.

---

### Task 7: the graph bookmark travels with graph snapshots (`session close` / `session initiate`)

`session initiate` (and a unified `snapshot restore` of the graph) wipes Neo4j — including the `:ArtmindSyncState` node — and restores a snapshot. The bookmark describes the graph's content, so it is exported **with** that content: `export_graph` adds the `ArtmindSyncState` label to what it exports, and `import_graph`'s generic `_restore_nodes` recreates it (after `_setup_neo4j` has recreated its constraint). A restore therefore brings back exactly the bookmark the graph had when it was saved. A snapshot taken before this plan carries none, so the graph has no bookmark afterwards and the next `vault sync` asks for `--bootstrapSynced`/`--bootstrapEmpty`; `session initiate` now says which case applies.

This replaces the spec's "set the graph bookmark from the snapshot manifest's `vault_commit` when `vault_dirty` is false". `vault_commit` is the vault's HEAD at export time, which can be *ahead* of the graph: a clean vault whose Obsidian Git pulled another machine's commits that were never `vault sync`ed has those commits in HEAD but not in the graph, and a bookmark set to HEAD would skip them forever. (Also, `session close`'s graph snapshot has no manifest at all — only `snapshot create`'s zip does.) See Self-review, decision 8.

**Files:**
- Modify: `artmind/graph_snapshot.py:49` (after `PROJECTED_LABELS`), `:169-170` (`export_graph`)
- Modify: `artmind/cli.py:3201-3209` (the end of `session_initiate`)
- Test: `test/test_graph_snapshot.py` (append)

- [ ] **Step 1: Write the failing tests**

**Append to** `test/test_graph_snapshot.py` (after its last line, leaving two blank lines):

```python
# ── the vault-sync bookmark travels with the graph (spec 2026-09-26 §6 A2) ──


def test_session_close_exports_the_sync_bookmark_and_initiate_recreates_it(tmp_path, monkeypatch):
    from contextlib import contextmanager

    import artmind.graph_snapshot as gs

    bookmark = {"vault_id": "v1", "last_applied_commit": "c0ffee", "applied_at": "2026-09-27T00:00:00+00:00"}

    class _Session:
        def run(self, cypher, **params):
            if cypher.startswith("MATCH (n:ArtmindSyncState)"):
                return FakeResult([{"props": bookmark, "labels": ["ArtmindSyncState"]}])
            return FakeResult([])

    @contextmanager
    def _fake(*a, **k):
        yield _Session()

    monkeypatch.setattr(gs, "neo4j_session", _fake)
    monkeypatch.setattr(gs, "GRAPH_SNAPSHOT_DIR", tmp_path)

    data = _read_snapshot(gs.export_graph())

    assert data["nodes"]["ArtmindSyncState"] == [{**bookmark, "labels": ["ArtmindSyncState"]}]
    restore = FakeSession()
    _restore_nodes(restore, data["nodes"])
    assert ("CREATE (n:ArtmindSyncState) SET n = $props", {"props": bookmark}) in restore.calls


def test_session_initiate_says_whether_the_bookmark_came_back(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", tmp_path)
    base = {"snapshot": "s.tar.gz", "relationship_count": 0, "elapsed_seconds": 0.1}

    with patch("artmind.cli.import_graph", return_value={**base, "node_counts": {"Document": 1}}):
        without = CliRunner().invoke(cli, ["session", "initiate", "--yes"])
    with patch("artmind.cli.import_graph", return_value={**base, "node_counts": {"ArtmindSyncState": 1}}):
        with_bookmark = CliRunner().invoke(cli, ["session", "initiate", "--yes"])

    assert "Sync bookmark: none in this snapshot" in without.output
    assert "--bootstrapSynced" in without.output
    assert "Sync bookmark: restored with the graph" in with_bookmark.output
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_graph_snapshot.py -q`
Expected: `2 failed`. `KeyError: 'ArtmindSyncState'` (the export does not carry the node) and `assert 'Sync bookmark: none in this snapshot' in ...`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/graph_snapshot.py` (line 49):

```python
PROJECTED_LABELS = ("Entity", "Conflict", "ProjectionState")
```

**with:**

```python
PROJECTED_LABELS = ("Entity", "Conflict", "ProjectionState")

# The graph's `vault sync` bookmarks, one `:ArtmindSyncState` per vault (spec
# 2026-09-26 §6 A2). A bookmark describes THIS graph's content, so it travels
# with it: `session initiate` (and `snapshot restore`) bring back exactly the
# bookmark the graph had when it was exported -- never newer than what the
# restored graph holds. A snapshot from before bookmarks existed carries none,
# and the next `vault sync` then asks for --bootstrapEmpty/--bootstrapSynced.
# No relationships, so `_export_relationships` never needs them.
SYNC_STATE_LABELS = ("ArtmindSyncState",)
```


**Replace in** `artmind/graph_snapshot.py` (lines 169-170):

```python
        nodes = _export_nodes(session, labels=BASE_LABELS)
        nodes.update(_export_nodes(session, labels=PROJECTED_LABELS))
```

**with:**

```python
        nodes = _export_nodes(session, labels=BASE_LABELS)
        nodes.update(_export_nodes(session, labels=PROJECTED_LABELS))
        nodes.update(_export_nodes(session, labels=SYNC_STATE_LABELS))
```


**Replace in** `artmind/cli.py` (lines 3201-3209):

```python
        click.echo(f"  Relationships: {summary['relationship_count']}")
        click.echo(f"  Elapsed: {summary['elapsed_seconds']}s")
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))


# ── artmind snapshot ───────────────────────────────────────────────────────────
```

**with:**

```python
        click.echo(f"  Relationships: {summary['relationship_count']}")
        click.echo(f"  Elapsed: {summary['elapsed_seconds']}s")
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))
    import paths

    if paths.ARTMIND_VAULT_DIR is not None:
        if node_counts.get("ArtmindSyncState"):
            click.echo("  Sync bookmark: restored with the graph (`artmind vault status` shows it)")
        else:
            click.echo(
                "  Sync bookmark: none in this snapshot -- the next `artmind vault sync` needs "
                "--bootstrapSynced (graph known-current) or --bootstrapEmpty (replay everything)"
            )


# ── artmind snapshot ───────────────────────────────────────────────────────────
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_graph_snapshot.py test/test_unified_snapshot_phase5.py -q`
Expected: all pass (the existing `test_base_labels_still_sources_only...` and relationship-scope tests are unaffected: the new label is neither in `BASE_LABELS` nor in the relationship scope).

Run: `uv run --group dev pytest test/ -q`
Expected: `2391 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_snapshot.py artmind/cli.py test/test_graph_snapshot.py
git commit -m "feat(session): the vault-sync bookmark travels with graph snapshots" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: pending work per store and the staleness line (spec §6 A4)

`pending_work(vault_dir, head, marks)` reports, per store, `bookmark`, `state` (`current`, `behind`, `no_bookmark`, `not_ancestor`, or `error` when the range cannot be classified — e.g. a mapping broken at HEAD), `docs` and `tables`. For the graph it reuses `classify_diff` and `committed_fingerprints`, then **one** Cypher read of the fingerprints of every document to replay or retract: a replay counts unless the graph already carries its committed fingerprint, a retraction counts only while the graph still has the document (on a shared graph the other machine may already have retracted it).

`staleness_message` turns that into the one stderr line: `artmind: graph is N docs / M tables behind the vault — run \`artmind vault sync\``, where M counts every table either store still has to apply (a restored table is also what the graph projects); `artmind: structured store is M tables behind the vault — ...` when only DuckDB is behind; a "pull first" line when a bookmark is not in this clone's history; nothing otherwise.

`query_staleness_warning(vault_dir)` is what Task 10 calls on every query, ordered so the common cases cost nothing: no vault → nothing; no bookmark in `state.json` (this machine never ran `vault sync`) → nothing, **without** touching git or Neo4j; no `vault_id` → nothing; `git rev-parse HEAD` fails (no git, no commits) → nothing; both bookmarks equal HEAD (one bookmark read, connect capped at `ADVISORY_TIMEOUT` = 2 s) → nothing. Only then git names/blobs and the one fingerprint read. It may raise; its caller swallows.

`drop_unchanged` switches to the shared `_is_unchanged` predicate so the sync and the report can never disagree about what "unchanged" means.

**Files:**
- Modify: `artmind/vault_sync.py:412-421` (`drop_unchanged`'s tail); append the pending-work section
- Test: `test/test_vault_sync.py` (append)

- [ ] **Step 1: Write the failing tests**

**Append to** `test/test_vault_sync.py` (after its last line, leaving two blank lines):

```python
# ── staleness: pending work and the warning line (spec 2026-09-26 §6 A4) ─────


def _synced_at(repo, graph, graph_commit, structured_commit):
    from artmind.vault import VaultLayout, write_state
    graph.bookmarks[VAULT_ID] = graph_commit
    write_state(VaultLayout(repo), {"last_structured_commit": structured_commit})


def test_no_warning_and_no_graph_read_on_a_machine_that_never_synced(repo, monkeypatch, graph):
    _three_commits(repo, monkeypatch)
    from artmind import sync_state

    def _no_graph(*a, **k):
        raise AssertionError("must not touch the graph")

    monkeypatch.setattr(sync_state, "read_graph_bookmark", _no_graph)

    assert vs.query_staleness_warning(repo) is None


def test_no_warning_and_no_fingerprint_read_when_both_bookmarks_are_head(repo, monkeypatch, graph):
    _, _, c3 = _three_commits(repo, monkeypatch)
    _synced_at(repo, graph, c3, c3)

    assert vs.query_staleness_warning(repo) is None
    assert graph.fingerprint_reads == []


def test_the_warning_counts_only_documents_the_graph_lacks(repo, monkeypatch, graph):
    c1, _, _ = _three_commits(repo, monkeypatch)
    _synced_at(repo, graph, c1, c1)
    import paths
    graph.fingerprints["docid-2"] = _fp(paths.KG_DIR / "banking" / "doc2")

    message = vs.query_staleness_warning(repo)

    assert message == "artmind: graph is 1 docs / 2 tables behind the vault — run `artmind vault sync`"
    assert graph.fingerprint_reads == [["docid-2", "docid-3"]], "one Cypher read"


def test_a_structured_store_behind_alone_is_named(repo, monkeypatch, graph):
    c1, _, c3 = _three_commits(repo, monkeypatch)
    _synced_at(repo, graph, c3, c1)

    assert vs.query_staleness_warning(repo) == (
        "artmind: structured store is 2 tables behind the vault — run `artmind vault sync`"
    )


def test_a_removal_counts_only_while_the_graph_still_has_the_document(repo, monkeypatch, graph):
    import shutil

    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    shutil.rmtree(kg_dir / "banking" / "doc1")
    _commit_all(repo, "remove doc1")
    _synced_at(repo, graph, base, vs.head_sha(repo))

    graph.fingerprints["docid-1"] = "whatever"
    assert vs.query_staleness_warning(repo) == (
        "artmind: graph is 1 docs / 0 tables behind the vault — run `artmind vault sync`"
    )
    del graph.fingerprints["docid-1"]   # the other machine already retracted it
    assert vs.query_staleness_warning(repo) is None


def test_a_bookmark_outside_this_clones_history_says_pull(repo, monkeypatch, graph):
    _, _, c3 = _three_commits(repo, monkeypatch)
    _synced_at(repo, graph, "0123456789abcdef0123456789abcdef01234567", c3)

    assert "let Obsidian Git pull" in vs.query_staleness_warning(repo)


def test_no_warning_without_git(tmp_path, graph):
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(tmp_path), {"last_structured_commit": "abc"})

    assert vs.query_staleness_warning(tmp_path) is None


def test_pending_work_reports_each_store(repo, monkeypatch, graph):
    c1, c2, c3 = _three_commits(repo, monkeypatch)
    marks = vs.Bookmarks(graph=c2, structured=c1)

    pending = vs.pending_work(repo, c3, marks)

    assert pending["graph"]["state"] == "behind"
    assert (pending["graph"]["docs"], pending["graph"]["tables"]) == (1, [("banking", "loans")])
    assert pending["structured"]["tables"] == [("banking", "accounts"), ("banking", "loans")]


def test_pending_work_reports_an_unclassifiable_range_instead_of_raising(repo, monkeypatch, graph):
    c1, _, c3 = _three_commits(repo, monkeypatch)
    _synced_at(repo, graph, c1, c1)

    def _broken(*a, **k):
        raise vs.VaultSyncError("mappings/x.yaml: invalid YAML (at abc)")

    monkeypatch.setattr(vs, "classify_diff", _broken)

    pending = vs.pending_work(repo, c3, vs.read_bookmarks(repo, vault_id=VAULT_ID))

    assert pending["graph"]["state"] == "error"
    assert "invalid YAML" in pending["graph"]["detail"]
    assert vs.staleness_message(pending) == (
        "artmind: structured store is 2 tables behind the vault — run `artmind vault sync`"
    )
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: `9 failed`. `AttributeError: module 'artmind.vault_sync' has no attribute 'query_staleness_warning'` (or `'pending_work'`).

- [ ] **Step 3: Implement**

**Replace in** `artmind/vault_sync.py` (lines 412-421):

```python
    in_graph = sync_state.read_document_fingerprints(doc_ids, timeout=timeout)
    unchanged, replay = [], []
    for key in plan.replay_docs:
        doc_id, fp = committed.get(key, (None, None))
        if fp is not None and doc_id in in_graph and in_graph[doc_id] == fp:
            unchanged.append(key)
        else:
            replay.append(key)
    plan.replay_docs = replay
    return unchanged
```

**with:**

```python
    in_graph = sync_state.read_document_fingerprints(doc_ids, timeout=timeout)
    unchanged = [key for key in plan.replay_docs if _is_unchanged(committed.get(key), in_graph)]
    plan.replay_docs = [key for key in plan.replay_docs if key not in unchanged]
    return unchanged


def _is_unchanged(committed: tuple[str | None, str | None] | None, in_graph: dict) -> bool:
    """Does the graph's live Document already carry this committed folder's
    fingerprint?"""
    if committed is None:
        return False
    doc_id, fingerprint = committed
    return fingerprint is not None and doc_id in in_graph and in_graph[doc_id] == fingerprint
```


**Append to** `artmind/vault_sync.py` (after its last line, leaving two blank lines):

```python
# ── pending work and the staleness warning (spec 2026-09-26 §6 A4) ───────────

#: Seconds an advisory graph read may spend connecting -- the staleness check
#: that rides along with every `query` command, and `vault status` -- so an
#: unreachable graph never holds a command up.
ADVISORY_TIMEOUT = 2.0


def _store_pending(
    vault_dir: Path, store: str, bookmark: str | None, head: str, *, timeout: float | None
) -> dict:
    """`{"bookmark", "state", "docs", "tables", "detail"}` for one store.
    `state` is `current`, `behind`, `no_bookmark`, `not_ancestor` (the
    bookmark is not in HEAD's history: pull first) or `error` (the range
    cannot be classified -- e.g. a table mapping broken at HEAD; `detail`
    says why, and `vault sync` would refuse with the same message). `docs`
    counts documents still to replay or retract after the fingerprint check
    (graph only); `tables` lists `(domain, table)` still to restore/project."""
    report = {"bookmark": bookmark, "state": "current", "docs": 0, "tables": [], "detail": None}
    if bookmark is None:
        report["state"] = "no_bookmark"
        return report
    if bookmark == head:
        return report
    try:
        check_bookmark(vault_dir, store, bookmark, head)
    except VaultSyncError as e:
        report.update(state="not_ancestor", detail=str(e))
        return report
    try:
        if store == "structured":
            tables, _ = _classify_structured_text_diff(vault_dir, bookmark, head, None)
            docs = 0
        else:
            docs, tables = _graph_pending(vault_dir, bookmark, head, timeout=timeout)
    except VaultSyncError as e:
        report.update(state="error", detail=str(e))
        return report
    report.update(state="behind" if docs or tables else "current", docs=docs, tables=sorted(tables))
    return report


def _graph_pending(
    vault_dir: Path, bookmark: str, head: str, *, timeout: float | None
) -> tuple[int, set[tuple[str, str]]]:
    """`(docs, tables)` the graph still has to apply between `bookmark` and
    `head`: a replay counts unless the graph already carries its committed
    fingerprint; a retraction counts only while the graph still has the
    document. ONE Cypher read covers both."""
    from artmind import sync_state

    plan = classify_diff(vault_dir, bookmark, head)
    committed = committed_fingerprints(vault_dir, head, plan.replay_docs)
    ids = [doc_id for doc_id, fp in committed.values() if doc_id is not None and fp is not None]
    ids += [doc_id for _, doc_id in plan.retract]
    in_graph = sync_state.read_document_fingerprints(ids, timeout=timeout) if ids else {}
    replay = [key for key in plan.replay_docs if not _is_unchanged(committed.get(key), in_graph)]
    retract = [doc_id for _, doc_id in plan.retract if doc_id in in_graph]
    tables = set(plan.regenerate_tables)
    tables |= {tuple(doc_id.split(":", 2)[1:]) for doc_id in retract if doc_id.startswith("table:")}
    docs = len(replay) + sum(1 for doc_id in retract if not doc_id.startswith("table:"))
    return docs, tables


def pending_work(
    vault_dir: Path, head: str, marks: Bookmarks, *, stores: tuple[str, ...] = STORES,
    timeout: float | None = ADVISORY_TIMEOUT,
) -> dict:
    """What `vault sync` would still apply, per store: `{store: {...}}` as
    `_store_pending` reports it. Reads git (names and committed blobs only)
    and, for the graph, one Cypher read of the affected documents'
    fingerprints."""
    return {
        store: _store_pending(vault_dir, store, marks.effective(store), head, timeout=timeout)
        for store in stores
    }


def staleness_message(pending: dict) -> str | None:
    """The one advisory line for stderr, or None when nothing is pending.
    `M tables` counts every table either store still has to apply -- a table
    restored into DuckDB is also what the graph projects."""
    graph = pending.get("graph", {})
    structured = pending.get("structured", {})
    for name, report in (("graph", graph), ("structured", structured)):
        if report.get("state") == "not_ancestor":
            return (
                f"artmind: the {name} store's sync bookmark is not in this clone's history "
                "-- let Obsidian Git pull, then run `artmind vault sync`"
            )
    tables = {tuple(t) for t in graph.get("tables", [])} | {tuple(t) for t in structured.get("tables", [])}
    docs = graph.get("docs", 0)
    if not docs and not tables:
        return None
    if not docs and not graph.get("tables"):
        return f"artmind: structured store is {len(tables)} tables behind the vault — run `artmind vault sync`"
    return f"artmind: graph is {docs} docs / {len(tables)} tables behind the vault — run `artmind vault sync`"


def query_staleness_warning(vault_dir: Path | None) -> str | None:
    """The staleness line a `query` command prints on stderr, or None.

    Cheap by construction, in this order, stopping at the first "nothing to
    say": no vault; this machine has never run `vault sync` (no bookmark in
    `state.json` -- a local file read, so a single-machine user never pays
    for a graph round trip); no vault_id; `git rev-parse HEAD` fails (no git,
    no commits); both bookmarks equal HEAD (one Cypher read, capped at
    `ADVISORY_TIMEOUT`). Only then: git names/blobs and one fingerprint read.
    Raises whatever those raise -- the caller swallows every exception, so a
    query is never broken by its own warning."""
    from artmind.manifest import read_vault_id
    from artmind.vault import VaultLayout, read_state

    if vault_dir is None:
        return None
    state = read_state(VaultLayout(vault_dir))
    if STRUCTURED_BOOKMARK_KEY not in state and LEGACY_CURSOR_KEY not in state:
        return None
    vault_id = read_vault_id(vault_dir)
    if vault_id is None:
        return None
    rc, out, _ = run_command(["git", "rev-parse", "HEAD"], cwd=vault_dir, expected_codes=(128,))
    if rc != 0:
        return None
    head = out.strip()
    marks = read_bookmarks(vault_dir, vault_id=vault_id, timeout=ADVISORY_TIMEOUT)
    if all(marks.effective(store) == head for store in STORES):
        return None
    return staleness_message(pending_work(vault_dir, head, marks))

```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2400 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault-sync): pending work per store and the staleness message" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: a fuller `artmind vault status`, with `--compact` JSON (spec §10)

`vault status` keeps its existing lines (vault, data, manifest, config, graph) and adds `vault sync`'s view: HEAD (or "no commits yet"), each store's bookmark and state (`current`, `N docs / M tables behind`, `no bookmark`, `not in this clone's history`, `cannot tell — <why>`), a still-present legacy cursor, a merge/rebase/cherry-pick in progress, unresolved conflicts under `.artmind/`, and the staleness line. `--compact` emits the same under a `"sync"` key. An unreachable graph (or a vault with no `vault_id`) is reported and the structured store is still shown — status never fails because the graph is down. It is not a `query` command, so the `serve` proxy never applies.

`preflight` is split into `operation_in_progress` and `unresolved_artmind_conflicts` so status reports exactly what `vault sync` would refuse on; `preflight`'s messages are unchanged (its tests pin them).

**Files:**
- Modify: `artmind/vault_sync.py:69-92` (`preflight`); append `status_report`
- Modify: `artmind/cli.py:3416-3423` (`_vault_status_impl`'s output, new `_echo_sync_status`), `:3434-3436` (`vault status`' docstring, plus `_setup_logger()` so `run_command`'s DEBUG lines stay off the terminal)
- Test: `test/test_vault_cli.py` (append)

- [ ] **Step 1: Write the failing tests**

**Append to** `test/test_vault_cli.py` (after its last line, leaving two blank lines):

```python
# ── `vault status`: the sync half (spec 2026-09-26 §10) ──────────────────────


def _fake_graph(monkeypatch, bookmark=None, fingerprints=None, unreachable=False):
    from artmind import sync_state

    def _read_bookmark(vault_id, *, timeout=None):
        if unreachable:
            raise OSError("connection refused")
        return bookmark

    monkeypatch.setattr(sync_state, "read_graph_bookmark", _read_bookmark)
    monkeypatch.setattr(
        sync_state, "read_document_fingerprints",
        lambda doc_ids, *, timeout=None: {d: (fingerprints or {})[d] for d in doc_ids if d in (fingerprints or {})},
    )


def _vault_with_docs(tmp_path, monkeypatch):
    """An init'd vault whose KG staging dir is the vault's own, with one
    committed document and a structured bookmark at that commit."""
    import paths

    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)
    kg = tmp_path / ".artmind" / "data" / "kg"
    monkeypatch.setattr(paths, "KG_DIR", kg)
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", tmp_path / ".artmind" / "data" / "structured_text")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    folder = kg / "banking" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text('{"id": "docid-1"}')
    (folder / "observations.json").write_text("[]")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "doc1"], cwd=tmp_path, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    vault.write_state(vault.VaultLayout(tmp_path), {"last_structured_commit": base})
    return base, head


def test_vault_status_reports_head_bookmarks_and_pending_work_as_json(tmp_path, monkeypatch):
    import json

    base, head = _vault_with_docs(tmp_path, monkeypatch)
    _fake_graph(monkeypatch, bookmark=base)

    result = CliRunner().invoke(cli, ["vault", "status", "--compact"])

    assert result.exit_code == 0, result.output
    sync = json.loads(result.stdout)["sync"]
    assert sync["head"] == head
    from artmind.manifest import read_vault_id
    assert sync["vault_id"] == read_vault_id(tmp_path)
    assert sync["stores"]["graph"]["bookmark"] == base
    assert (sync["stores"]["graph"]["state"], sync["stores"]["graph"]["docs"]) == ("behind", 1)
    assert sync["stores"]["structured"]["state"] == "current"
    assert sync["operation_in_progress"] is None
    assert sync["unresolved_conflicts"] == []
    assert sync["message"] == "artmind: graph is 1 docs / 0 tables behind the vault — run `artmind vault sync`"


def test_vault_status_human_output_names_each_store(tmp_path, monkeypatch):
    base, head = _vault_with_docs(tmp_path, monkeypatch)
    _fake_graph(monkeypatch, bookmark=head)

    result = CliRunner().invoke(cli, ["vault", "status"])

    assert result.exit_code == 0, result.output
    assert f"HEAD:     {head}" in result.output
    assert f"Sync:     graph      {head}  current" in result.output
    assert f"Sync:     structured {base}  current" in result.output
    assert "Merge:    none in progress" in result.output
    assert "Conflicts: none under .artmind/" in result.output


def test_vault_status_survives_an_unreachable_graph(tmp_path, monkeypatch):
    import json

    _vault_with_docs(tmp_path, monkeypatch)
    _fake_graph(monkeypatch, unreachable=True)

    result = CliRunner().invoke(cli, ["vault", "status", "--compact"])

    assert result.exit_code == 0, result.output
    sync = json.loads(result.stdout)["sync"]
    assert sync["graph_error"].startswith("graph unreachable")
    assert list(sync["stores"]) == ["structured"]


def test_vault_status_shows_a_merge_and_artmind_conflicts(tmp_path, monkeypatch):
    import json

    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)
    _fake_graph(monkeypatch)
    path = tmp_path / ".artmind" / "same_as.yaml"
    path.write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-qb", "other"], cwd=tmp_path, check=True)
    path.write_text("theirs\n")
    subprocess.run(["git", "commit", "-qam", "theirs"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-"], cwd=tmp_path, check=True)
    path.write_text("ours\n")
    subprocess.run(["git", "commit", "-qam", "ours"], cwd=tmp_path, check=True)
    subprocess.run(["git", "merge", "other"], cwd=tmp_path, capture_output=True)

    result = CliRunner().invoke(cli, ["vault", "status", "--compact"])

    sync = json.loads(result.stdout)["sync"]
    assert sync["operation_in_progress"] == "a merge"
    assert sync["unresolved_conflicts"] == [".artmind/same_as.yaml"]


def test_vault_status_in_a_vault_with_no_commits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])
    _fake_graph(monkeypatch)

    result = CliRunner().invoke(cli, ["vault", "status"])

    assert result.exit_code == 0, result.output
    assert "HEAD:     (no commits yet)" in result.output
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_cli.py -q`
Expected: `5 failed`. `KeyError: 'sync'` for the JSON tests, `assert 'HEAD:     ...' in ...` for the human-readable ones.

- [ ] **Step 3: Implement**

**Replace in** `artmind/vault_sync.py` (lines 69-92):

```python
def preflight(vault_dir: Path) -> None:
    """Refuse -- before reading or writing anything -- when the vault is in a
    state `sync` must not apply from (spec 2026-09-26 §6 A5, §14 A7): a merge,
    rebase or cherry-pick in progress, unresolved conflicts under `.artmind/`,
    or a running ingest worker. Each message says what to do next."""
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
    # Only artmind's own files (spec §14 A7): a conflicted human note does not
    # change what sync applies, and Obsidian shows it to the user. `-z` keeps a
    # name with a space (or a non-ASCII byte) whole (§14 A5).
    unmerged = [
        p for p in _git(vault_dir, ["diff", "--name-only", "-z", "--diff-filter=U", "--", MARKER]).split("\0") if p
    ]
    if unmerged:
```

**with:**

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
            return what
    return None


def unresolved_artmind_conflicts(vault_dir: Path) -> list[str]:
    """Paths under `.artmind/` with unresolved conflicts. Only artmind's own
    files (spec §14 A7): a conflicted human note does not change what sync
    applies, and Obsidian shows it to the user. `-z` keeps a name with a
    space (or a non-ASCII byte) whole (§14 A5)."""
    out = _git(vault_dir, ["diff", "--name-only", "-z", "--diff-filter=U", "--", MARKER])
    return [p for p in out.split("\0") if p]


def preflight(vault_dir: Path) -> None:
    """Refuse -- before reading or writing anything -- when the vault is in a
    state `sync` must not apply from (spec 2026-09-26 §6 A5, §14 A7): a merge,
    rebase or cherry-pick in progress, unresolved conflicts under `.artmind/`,
    or a running ingest worker. Each message says what to do next."""
    what = operation_in_progress(vault_dir)
    if what is not None:
        raise VaultSyncError(
            f"{what} is in progress in this vault -- finish it in Obsidian "
            "(or git) first, then re-run `vault sync`"
        )
    unmerged = unresolved_artmind_conflicts(vault_dir)
    if unmerged:
```


**Append to** `artmind/vault_sync.py` (after its last line, leaving two blank lines):

```python
def status_report(vault_dir: Path) -> dict:
    """Everything `vault status` shows about sync. Never raises for an
    unreachable graph, a repo with no commits, or a vault with no vault_id:
    each becomes a field the caller prints."""
    from artmind.manifest import ManifestError, read_vault_id

    rc, out, _ = run_command(["git", "rev-parse", "HEAD"], cwd=vault_dir, expected_codes=(128,))
    head = out.strip() if rc == 0 else None
    report: dict = {
        "head": head,
        "vault_id": None,
        "operation_in_progress": None,
        "unresolved_conflicts": [],
        "legacy_cursor": None,
        "graph_error": None,
        "stores": {},
        "message": None,
    }
    try:
        report["operation_in_progress"] = operation_in_progress(vault_dir)
        report["unresolved_conflicts"] = unresolved_artmind_conflicts(vault_dir)
    except VaultSyncError as e:
        report["graph_error"] = f"not a git repository: {e}"
        return report
    try:
        report["vault_id"] = read_vault_id(vault_dir)
    except ManifestError as e:
        report["graph_error"] = str(e)

    stores: tuple[str, ...] = STORES
    if report["vault_id"] is None:
        stores = ("structured",)
        report["graph_error"] = report["graph_error"] or "no vault_id in .artmind/vault.yaml -- run `artmind init`"
    try:
        marks = read_bookmarks(vault_dir, vault_id=report["vault_id"], stores=stores, timeout=ADVISORY_TIMEOUT)
    except Exception as e:  # an unreachable graph is a status, not a crash
        report["graph_error"] = f"graph unreachable: {e}"
        stores = ("structured",)
        marks = read_bookmarks(vault_dir, vault_id=None, stores=stores)
    report["legacy_cursor"] = marks.legacy
    if head is None:
        report["stores"] = {s: {"bookmark": marks.effective(s), "state": "no_commits"} for s in stores}
        return report
    report["stores"] = pending_work(vault_dir, head, marks, stores=stores)
    report["message"] = staleness_message(report["stores"])
    return report
```


**Replace in** `artmind/cli.py` (lines 3416-3423):

```python
    if compact:
        _echo_json(info, compact=True)
        return
    click.echo(f"Vault:    {info['vault']}")
    click.echo(f"Data:     {info['data_dir']}")
    click.echo(f"Manifest: {info['manifest'] or '(none — run artmind init)'}")
    click.echo(f"Config:   {', '.join(info['config']) or '(none loaded)'}")
    click.echo(f"Graph:    {info['graph']['uri'] or '(unset)'}  db={info['graph']['database'] or '(unset)'}")
```

**with:**

```python
    from artmind.vault_sync import status_report

    info["sync"] = status_report(vault_dir)
    if compact:
        _echo_json(info, compact=True)
        return
    click.echo(f"Vault:    {info['vault']}")
    click.echo(f"Data:     {info['data_dir']}")
    click.echo(f"Manifest: {info['manifest'] or '(none — run artmind init)'}")
    click.echo(f"Config:   {', '.join(info['config']) or '(none loaded)'}")
    click.echo(f"Graph:    {info['graph']['uri'] or '(unset)'}  db={info['graph']['database'] or '(unset)'}")
    _echo_sync_status(info["sync"])


def _echo_sync_status(sync: dict) -> None:
    """The `vault sync` half of `vault status`, human-readable."""
    click.echo(f"HEAD:     {sync['head'] or '(no commits yet)'}")
    for store, label in (("graph", "graph     "), ("structured", "structured")):
        report = sync["stores"].get(store)
        if report is None:
            click.echo(f"Sync:     {label} (unknown — {sync['graph_error']})")
            continue
        bookmark = report.get("bookmark") or "none"
        state = report.get("state")
        if state == "behind":
            tables = len(report.get("tables") or [])
            what = f"{report['docs']} docs / {tables} tables behind" if store == "graph" else f"{tables} tables behind"
        else:
            what = {
                "current": "current",
                "no_bookmark": "no bookmark — `vault sync --bootstrapEmpty` or `--bootstrapSynced`",
                "not_ancestor": "not in this clone's history — let Obsidian Git pull first",
                "no_commits": "nothing committed yet",
                "error": f"cannot tell — {report.get('detail')}",
            }.get(state, state)
        click.echo(f"Sync:     {label} {bookmark}  {what}")
    if sync.get("legacy_cursor"):
        click.echo(f"          (legacy last_synced_commit {sync['legacy_cursor']} still seeds a store with no bookmark)")
    op = sync.get("operation_in_progress")
    click.echo(f"Merge:    {op + ' is in progress — finish it before `vault sync`' if op else 'none in progress'}")
    conflicts = sync.get("unresolved_conflicts") or []
    if conflicts:
        shown = ", ".join(conflicts[:5]) + (", ..." if len(conflicts) > 5 else "")
        click.echo(f"Conflicts: {len(conflicts)} unresolved under .artmind/ ({shown})")
    else:
        click.echo("Conflicts: none under .artmind/")
    if sync.get("message"):
        click.echo(sync["message"])
```


**Replace in** `artmind/cli.py` (lines 3434-3436):

```python
def vault_status(compact: bool):
    """Show which vault is active and how it was resolved."""
    _vault_status_impl(compact)
```

**with:**

```python
def vault_status(compact: bool):
    """Show which vault is active, and how far each store is behind it.

    Reports the vault, its config and graph, then `vault sync`'s view: HEAD,
    the graph bookmark (read from the graph itself, so on a shared AuraDB it
    is the one every machine sees) and the structured-store bookmark (this
    machine's .artmind/state.json), the documents and tables each store has
    still to apply (after the fingerprint check), a merge/rebase in progress,
    and unresolved conflicts under .artmind/. An unreachable graph is
    reported, not an error. Read-only. --compact emits all of it as JSON.
    """
    _setup_logger()
    _vault_status_impl(compact)
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_cli.py test/test_vault_sync.py -q`
Expected: all pass (the preflight tests still see their messages).

Run: `uv run --group dev pytest test/ -q`
Expected: `2405 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py artmind/cli.py test/test_vault_cli.py
git commit -m "feat(vault): vault status shows HEAD, both bookmarks, pending work, merges and conflicts" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: every `query` command prints the staleness line on stderr (spec §6 A4)

**Where it hooks in, given the `serve` proxy.** `artmind/_entry.py` must stay stdlib-only (its point is skipping the heavy import tree), so it cannot read git history or Neo4j — it is the wrong place. The simplest correct place is a Click **result callback on the `query` group** in `cli.py`:

- it runs only after a `query` subcommand returned normally — never for `--help` (Click exits while parsing), never after a failed query;
- it writes with `click.echo(..., err=True)`, so `--compact` JSON on stdout is untouched;
- it runs identically in-process and inside the `serve` daemon: the daemon executes the same Click command under `CliRunner`, `server.run_cli_args` returns `result.stderr` in the response's `"stderr"` field, and `_entry._proxy` already writes that field to the real stderr. No change to `_entry.py` or `server.py`. A daemon started before this code shipped keeps printing nothing until restarted (CLAUDE.md §1) — the Live verification section says so;
- every exception is swallowed, `run_command`'s DEBUG lines are kept off stderr while it runs (query commands leave loguru's default sink in place), and `ARTMIND_NO_STALENESS_CHECK=1` turns it off.

**Files:**
- Modify: `artmind/cli.py:2180-2183` (the `query` group)
- Test: `test/test_query_staleness.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_query_staleness.py`:

```python
"""The staleness line on `query` commands (spec 2026-09-26 §6 A4): stderr
only, after a successful query, through both the in-process CLI and the
`serve` daemon -- and never able to break the query it rides along with."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli

MESSAGE = "artmind: graph is 2 docs / 1 tables behind the vault — run `artmind vault sync`"
OVERVIEW = {"domains": [{"name": "banking"}]}


@pytest.fixture()
def in_vault(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", tmp_path)
    monkeypatch.delenv("ARTMIND_NO_STALENESS_CHECK", raising=False)
    return tmp_path


def _warning(monkeypatch, fn):
    import artmind.vault_sync as vs

    seen = []

    def _wrapped(vault_dir):
        seen.append(vault_dir)
        return fn(vault_dir)

    monkeypatch.setattr(vs, "query_staleness_warning", _wrapped)
    return seen


def test_the_warning_goes_to_stderr_and_compact_json_stays_clean(in_vault, monkeypatch):
    seen = _warning(monkeypatch, lambda v: MESSAGE)

    with patch("artmind.cli.graph_query.domains_overview", return_value=OVERVIEW):
        result = CliRunner().invoke(cli, ["query", "domains-overview", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == OVERVIEW
    assert result.stderr.strip() == MESSAGE
    assert seen == [in_vault]


def test_nested_graph_commands_warn_too(in_vault, monkeypatch):
    _warning(monkeypatch, lambda v: MESSAGE)

    with patch("artmind.cli.graph_query.graph_metadata", return_value={"labels": []}):
        result = CliRunner().invoke(cli, ["query", "graph", "metadata", "--domain", "banking", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"labels": []}
    assert MESSAGE in result.stderr


def test_a_failing_check_is_swallowed(in_vault, monkeypatch):
    def _boom(vault_dir):
        raise RuntimeError("neo4j unreachable")

    _warning(monkeypatch, _boom)

    with patch("artmind.cli.graph_query.domains_overview", return_value=OVERVIEW):
        result = CliRunner().invoke(cli, ["query", "domains-overview", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == OVERVIEW
    assert result.stderr == ""


def test_a_failed_query_is_not_followed_by_a_warning(in_vault, monkeypatch):
    seen = _warning(monkeypatch, lambda v: MESSAGE)

    with patch("artmind.cli.graph_query.domains_overview", side_effect=RuntimeError("down")):
        result = CliRunner().invoke(cli, ["query", "domains-overview"])

    assert result.exit_code != 0
    assert seen == []


def test_help_never_runs_the_check(in_vault, monkeypatch):
    seen = _warning(monkeypatch, lambda v: MESSAGE)

    result = CliRunner().invoke(cli, ["query", "domains-overview", "--help"])

    assert result.exit_code == 0
    assert seen == []


def test_the_env_switch_turns_it_off(in_vault, monkeypatch):
    seen = _warning(monkeypatch, lambda v: MESSAGE)
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")

    with patch("artmind.cli.graph_query.domains_overview", return_value=OVERVIEW):
        result = CliRunner().invoke(cli, ["query", "domains-overview", "--compact"])

    assert result.stderr == ""
    assert seen == []


def test_the_serve_daemon_returns_the_warning_in_its_stderr_field(in_vault, monkeypatch):
    """`_entry._proxy` writes `body["stderr"]` to the real stderr, so a
    proxied query shows the same line an in-process one does."""
    from artmind.server import run_cli_args

    _warning(monkeypatch, lambda v: MESSAGE)

    with patch("artmind.cli.graph_query.domains_overview", return_value=OVERVIEW):
        body = run_cli_args(["query", "domains-overview", "--compact"])

    assert body["exit_code"] == 0
    assert json.loads(body["output"]) == OVERVIEW
    assert body["stderr"].strip() == MESSAGE


def test_the_proxy_prints_the_daemons_stderr(monkeypatch, capsys):
    import artmind._entry as entry

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"output": '{"a":1}\n', "stderr": MESSAGE + "\n", "exit_code": 0}).encode()

    monkeypatch.setattr(entry.urllib.request, "urlopen", lambda *a, **k: _Resp())

    assert entry._proxy("http://127.0.0.1:1", ["query", "domains-overview", "--compact"]) == 0
    out, err = capsys.readouterr()
    assert out == '{"a":1}\n'
    assert err.strip() == MESSAGE
```


- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_query_staleness.py -q`
Expected: `3 failed, 5 passed`. The three that expect the line fail with `assert '' == 'artmind: graph is ...'`. The five that pass already pin what must not happen (no line after a failed query, for `--help`, with the switch off, when the check raises) and the existing proxy behaviour.

- [ ] **Step 3: Implement**

**Replace in** `artmind/cli.py` (lines 2180-2183):

```python
@cli.group()
def query():
    """Query the knowledge graph and vector index, plus the structured store (text2sql, resolve-key)."""
    pass
```

**with:**

```python
@cli.group()
def query():
    """Query the knowledge graph and vector index, plus the structured store (text2sql, resolve-key).

    After a query succeeds inside a vault that `vault sync` has run in, a
    line on stderr says when the graph or structured store is behind the
    vault's HEAD (never in stdout, so --compact JSON stays clean). Set
    ARTMIND_NO_STALENESS_CHECK=1 to skip it.
    """
    pass


@query.result_callback()
def _warn_if_vault_stale(*_args, **_kwargs) -> None:
    """Spec 2026-09-26 §6 A4: after a `query` command SUCCEEDS, say on stderr
    when this vault's stores are behind HEAD. Advisory only: it never applies
    anything (D4), and any failure to work it out is swallowed -- a query is
    never broken, or made to fail, by its own warning.

    Hooked here, not in `_entry.py`: `_entry` must stay stdlib-only and
    cannot read git history or Neo4j. A result callback runs only after the
    subcommand returned normally -- never for `--help` or a failed query --
    and it runs identically in-process and inside the `serve` daemon, whose
    CliRunner captures stderr and `_entry._proxy` writes it back. (A daemon
    started before this code shipped prints nothing: restart it.)"""
    if os.environ.get("ARTMIND_NO_STALENESS_CHECK"):
        return
    import paths
    from artmind.vault_sync import query_staleness_warning

    # run_command logs every git call at DEBUG, and query commands leave
    # loguru's default stderr sink in place: keep those lines out.
    logger.disable("utils")
    logger.disable("artmind")
    try:
        message = query_staleness_warning(paths.ARTMIND_VAULT_DIR)
    except Exception:
        message = None
    finally:
        logger.enable("utils")
        logger.enable("artmind")
    if message:
        click.echo(message, err=True)
```


- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_query_staleness.py test/test_serve.py test/test_entry_proxy.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2413 passed, 14 skipped` — every existing `query` test still passes: in the suite `paths.ARTMIND_VAULT_DIR` is `None`, so the callback returns before touching anything.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_query_staleness.py
git commit -m "feat(query): warn on stderr when the vault's stores are behind HEAD" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: docs, docstrings, skills; full suite and CLI help

CLAUDE.md §4: the command docstrings, the skills and the docs move together. No `justfile` recipe changes (see "File map").

**Files:**
- Modify: `artmind/cli.py:3507` (`vault` group docstring), `:3559-3585` (`vault sync` docstring)
- Modify: `artmind/vault_sync.py:13-15` (end of the module docstring)
- Modify: `docs/vault.md:82` (Layout), `:120` ("What is in git"), `:279` (the ingest-manifest section), `:329` and `:358-364` ("Git: Obsidian Git owns transport", plus a new "Sync bookmarks, fingerprints, and the two topologies" subsection), `:510` ("Machine-level config"), `:584` ("Snapshots")
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md:454` (Situation J), `:517` (Common Gotchas); `artmind/skills/artmind-query/SKILL.md:12` (Grounding Rule)

- [ ] **Step 1: Command docstrings**

**Replace in** `artmind/cli.py` (line 3507):

```python
    """Which vault is active (`status`), git-diff-driven sync into Neo4j/the structured store (`sync`), and read-only readiness checks for Obsidian Git (`doctor`)."""
```

**with:**

```python
    """Which vault is active and how far behind it each store is (`status`), git-diff-driven sync into Neo4j/the structured store (`sync`), and read-only readiness checks for Obsidian Git (`doctor`)."""
```


**Replace in** `artmind/cli.py` (lines 3559-3585):

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

    --domain scopes which changes are applied, but never advances the
    last_synced_commit bookmark: a domain-scoped run only classified and
    applied that domain's slice of the range, so moving the cursor past it
    would silently strand every other domain's changes in that same range.
    A later unscoped `vault sync` still starts from the same bookmark, applies
    whatever was missed, and idempotently re-applies what the scoped run
    already did.

    Applies committed content only (never the working tree) and never
    commits. Refuses while a merge/rebase is in progress, while files under
    .artmind/ have unresolved conflicts, or while the ingest worker is running.
    """
```

**with:**

```python
    """Replay committed KG-staging, structured-text, table-mapping and schema changes into Neo4j/DuckDB since each store's bookmark.

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

    Each store keeps its own bookmark: the graph's lives in the graph
    (one :ArtmindSyncState node per vault_id, so a shared AuraDB carries one
    bookmark for every machine), the structured store's in this machine's
    .artmind/state.json. Each applies its own range, and a document whose
    committed fingerprint the graph already carries is skipped — on a shared
    graph, whatever the ingesting machine wrote. A pre-bookmark
    last_synced_commit seeds any store with no bookmark, once. No bookmark
    yet? pass --bootstrapEmpty or --bootstrapSynced (see each flag's own
    help, and --store).

    --domain scopes which changes are applied, but never advances a
    bookmark: a domain-scoped run only classified and applied that domain's
    slice of the range, so moving a bookmark past it would silently strand
    every other domain's changes in that same range. A later unscoped `vault
    sync` still starts from the same bookmarks, applies whatever was missed,
    and idempotently re-applies what the scoped run already did.

    Applies committed content only (never the working tree) and never
    commits. Refuses while a merge/rebase is in progress, while files under
    .artmind/ have unresolved conflicts, while the ingest worker is running,
    or while a bookmark is not in HEAD's history (pull first).
    """
```


- [ ] **Step 2: `vault_sync` module docstring**

**Replace in** `artmind/vault_sync.py` (lines 13-15):

```python
never from the working tree, and `sync` never commits (spec
2026-09-26-vault-git-transport-design.md, §6 A1, D1, §14).
"""
```

**with:**

```python
never from the working tree, and `sync` never commits (spec
2026-09-26-vault-git-transport-design.md, §6 A1, D1, §14).

Each store has its own bookmark (§6 A2): the graph's in the graph
(`artmind.sync_state`), the structured store's in `state.json`. A document
whose committed fingerprint the graph already carries is skipped (§6 A3),
and `pending_work`/`query_staleness_warning` report what is still to apply
(§6 A4). artmind never writes to this vault's git (D2) -- only reads it.
"""
```


- [ ] **Step 3: `docs/vault.md`**

**Replace in** `docs/vault.md` (line 82):

```markdown
    ├── vault.yaml                      ← the ingest manifest; COMMITTED
```

**with:**

```markdown
    ├── vault.yaml                      ← the ingest manifest + vault_id; COMMITTED
```


**Replace in** `docs/vault.md` (line 120):

```markdown
| `.artmind/logs/`, `state.json`, `serve.json`, `worker.pid` | machine-local runtime state, meaningless on another machine |
```

**with:**

```markdown
| `.artmind/logs/`, `state.json`, `serve.json`, `worker.pid` | machine-local runtime state, meaningless on another machine (`state.json` holds this machine's structured-store sync bookmark) |
```


**Replace in** `docs/vault.md` (line 279):

```markdown
unmapped files → prompt.
```

**with:**

```markdown
unmapped files → prompt.

`artmind init` also appends a `vault_id: "<uuid>"` line (with a comment) the
first time it runs in a vault — and in a vault initialised before it existed,
without touching anything else in the file. It names this vault's sync
bookmark in the graph (see "Sync bookmarks" below), so two vaults sharing one
AuraDB never share a bookmark. Commit it; every clone must carry the same
value. If two machines each run `init` before either has pulled the other's
`vault_id`, the merge conflicts on that line — keep either one.
```


**Replace in** `docs/vault.md` (line 329):

```markdown
**merge** (not rebase — commit shas are provenance and the sync cursor),
```

**with:**

```markdown
**merge** (not rebase — commit shas are provenance and the sync bookmarks),
```


**Replace in** `docs/vault.md` (lines 358-364):

```markdown
`vault sync` refuses while a merge, rebase or cherry-pick is in progress,
while a file under `.artmind/` has unresolved conflicts (a conflicted note of
yours does not block it), or while the ingest worker is running. A machine
with no Obsidian that must still follow the remote (e.g. a
query-only host) uses a plain `git pull --no-rebase` on a launchd/cron timer
instead — documented, not built into artmind. Design:
`docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`.
```

**with:**

```markdown
`vault sync` refuses while a merge, rebase or cherry-pick is in progress,
while a file under `.artmind/` has unresolved conflicts (a conflicted note of
yours does not block it), while the ingest worker is running, or while a
store's bookmark is not in `HEAD`'s history (pull first). A machine
with no Obsidian that must still follow the remote (e.g. a
query-only host) uses a plain `git pull --no-rebase` on a launchd/cron timer
instead — documented, not built into artmind. Design:
`docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`.

#### Sync bookmarks, fingerprints, and the two topologies

A bookmark means "this store reflects the vault's commits up to X", and each
store has its own:

| Store | Bookmark | Why there |
|---|---|---|
| Graph | a `(:ArtmindSyncState {vault_id, last_applied_commit, applied_at})` node in Neo4j | a shared AuraDB carries one bookmark that every machine using it sees; a separate local Neo4j carries its own — same code for both |
| Structured store | `last_structured_commit` in `.artmind/state.json` | DuckDB is always a local file |

Each store applies its own range (`bookmark..HEAD`), so a graph ahead of the
structured store, or the reverse, is normal. `vault sync --store graph` or
`--store structured` runs one store alone (a structured-only run never
connects to Neo4j); `--bootstrapEmpty`/`--bootstrapSynced` apply to every
store in the run. A vault that synced before bookmarks existed has a single
`last_synced_commit` in `state.json`: it seeds any store with no bookmark of
its own and is removed once both stores have one. Where the graph already has
a bookmark (another machine sharing it wrote one), the graph's own bookmark
wins.

Every graph write of a staging folder — ingest, `table2graph`,
`write-to-graph`, `pull-kg`, archive restore, dashboard import, `vault sync`
itself — records a **fingerprint** on the `Document`: sha256 over the raw
bytes of `document.json`, `chunks.json`, `observations.json` and
`relationships.json`. `vault sync` computes the same digest from the
committed blobs and skips a folder whose fingerprint the graph already
carries, with one Cypher read for the whole run. So:

| | Shared AuraDB | Separate local Neo4j per machine |
|---|---|---|
| Machine B's apply, graph side | the ingesting machine already wrote it: fingerprints match, near no-op, the bookmark advances | full replay of `bookmark..HEAD` |
| Machine B's apply, structured side | regenerate changed tables into B's DuckDB | same |
| First `vault sync` on a new machine | the graph bookmark is already there; `vault sync --store structured --bootstrapEmpty` fills DuckDB, then plain `vault sync` | `vault sync --bootstrapEmpty` |

artmind never writes to this vault's git — no commit, no ref, nothing
(spec D1, D2). Obsidian Git merges (never rebases), so a bookmarked commit
stays reachable on its own; if history is ever rewritten anyway, the next
section covers what `vault sync` does about it.

A bookmark that is not an ancestor of `HEAD` is refused, never diffed:
diffing from it would read the other machine's new documents as removals.
Pulling (Obsidian Git merges) usually fixes this — the bookmark's commit
was there all along, just not yet in this clone's history. If it doesn't
(the commit was rewritten away, not merely unpulled), the refusal also
names the way out: `vault sync --store <name> --bootstrapSynced` if that
store is already known-current, or `--store <name> --bootstrapEmpty` to
replay everything committed.

`artmind vault status` shows HEAD, both bookmarks, how many documents and
tables each store is behind (after the fingerprint check), a merge in
progress, and unresolved conflicts under `.artmind/`; `--compact` gives the
same as JSON. After a successful `artmind query ...` in a vault this machine
has synced, a line such as

    artmind: graph is 3 docs / 1 tables behind the vault — run `artmind vault sync`

goes to **stderr** — never stdout, so `--compact` JSON stays clean. It is
advisory: queries never apply anything. It costs a `git rev-parse` and one
Cypher read when nothing is pending (capped at 2 s to connect), and nothing
at all on a machine that has never run `vault sync`. `ARTMIND_NO_STALENESS_CHECK=1`
turns it off. Through the `serve` daemon the line arrives the same way, once
the daemon has been restarted on this code.
```


**Replace in** `docs/vault.md` (line 510):

```markdown
| **Runtime** | `ARTMIND_NO_PROXY`, `--vault`, `ARTMIND_IMPORT_MAX_BYTES`
```

**with:**

```markdown
| **Runtime** | `ARTMIND_NO_PROXY`, `ARTMIND_NO_STALENESS_CHECK` (silence the `query` staleness line), `--vault`, `ARTMIND_IMPORT_MAX_BYTES`
```


**Replace in** `docs/vault.md` (line 584):

```markdown
> slow leak with no supported remedy, since nothing else prunes them.
```

**with:**

```markdown
> slow leak with no supported remedy, since nothing else prunes them.

A graph snapshot (`session close`, and the graph component of `snapshot
create`) carries the graph's `:ArtmindSyncState` bookmarks, so `session
initiate` puts back exactly the bookmark the graph had when it was saved —
never one newer than the restored content. A snapshot taken before bookmarks
existed carries none; the next `vault sync` then asks for `--bootstrapSynced`
or `--bootstrapEmpty`, and `session initiate` says so.
```


- [ ] **Step 4: Skills**

In `artmind/skills/artmind-ingestion-helper/SKILL.md`, Situation J (after the **Other machines.** paragraph) and Common Gotchas:

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (line 454):

```markdown
retracted there. A table no mapping names is restored to the structured store only.
```

**with:**

```markdown
retracted there. A table no mapping names is restored to the structured store only.

**Where `vault sync` starts.** Each store has its own bookmark: the graph's is a node in the
graph (shared by every machine using the same AuraDB), the structured store's is in this
machine's `.artmind/state.json`. `artmind vault status` shows both and what is pending. On a
shared AuraDB a document the other machine ingested is skipped by fingerprint, so its
`vault sync` mostly just restores tables into DuckDB. A new machine on a shared graph runs
`artmind vault sync --store structured --bootstrapEmpty` once, then plain `vault sync`.
```


**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (line 517):

```markdown
| Empty graph after Neo4j restart | Neo4j was ephemeral and lost data | Run `artmind session initiate` to restore from snapshot, or `write-to-graph` if JSON exists |
```

**with:**

```markdown
| Empty graph after Neo4j restart | Neo4j was ephemeral and lost data | Run `artmind session initiate` to restore from snapshot (it restores the graph's sync bookmark too), or `write-to-graph` if JSON exists |
| `vault sync`: "no graph/structured bookmark recorded yet" | This store has never been synced on this graph/machine (or the snapshot predates bookmarks) | `vault sync --bootstrapSynced` if the store is known-current, else `--bootstrapEmpty`; add `--store graph`/`--store structured` to do one store only |
| `vault sync`: "the graph bookmark ... is not a commit in this clone" / "not an ancestor of HEAD" | Another machine sharing the graph synced commits this clone has not pulled | Let Obsidian Git pull (merge), then re-run `vault sync` |
```


In `artmind/skills/artmind-query/SKILL.md`, at the end of the Grounding Rule:

**Replace in** `artmind/skills/artmind-query/SKILL.md` (line 12):

```markdown
Do not invent entities, events, relationships, motivations, or source details not present in the returned data.
```

**with:**

```markdown
Do not invent entities, events, relationships, motivations, or source details not present in the returned data.

A stderr line starting `artmind: graph is N docs / M tables behind the vault` (or `artmind: structured store is M tables behind the vault`) is advisory, not an error and not query data: the answer may miss recently pulled documents. Tell the user once; do not run `vault sync` yourself.
```


- [ ] **Step 5: Full suite**

Run: `uv run --group dev pytest test/ -q`
Expected: `2413 passed, 14 skipped` (`2334 passed` at `290303d` plus the 79 tests this plan adds), no failures. A dry run of this whole plan in a scratch worktree produced exactly that; any failure is a regression to fix before committing.

- [ ] **Step 6: CLI help sanity**

Run: `ARTMIND_NO_PROXY=1 uv run artmind vault sync --help | head -5`
Expected: the new first line, `Replay committed KG-staging, structured-text, table-mapping and schema changes into Neo4j/DuckDB since each store's bookmark.`, and among the options `--store [graph|structured]` beside the unchanged `--bootstrapEmpty`, `--bootstrapSynced`, `--domain`, `--dryRun`, `--compact`.

Run: `ARTMIND_NO_PROXY=1 uv run artmind query --help | head -8`
Expected: the `query` group help now ends its description with the stderr staleness line and `ARTMIND_NO_STALENESS_CHECK=1`. (`ARTMIND_NO_PROXY=1` matters here: `query` is the one group `_entry.py` proxies to a running daemon — CLAUDE.md §1.)

Run: `just dev-cli-help 2>/dev/null | grep "COMMAND: cli vault"`
Expected exactly four lines — `COMMAND: cli vault`, `COMMAND: cli vault status`, `COMMAND: cli vault sync`, `COMMAND: cli vault doctor` — no command added or removed.

- [ ] **Step 7: Commit**

```bash
git add artmind/cli.py artmind/vault_sync.py docs/vault.md artmind/skills/artmind-ingestion-helper/SKILL.md artmind/skills/artmind-query/SKILL.md
git commit -m "docs(vault-sync): per-store bookmarks, fingerprints, topologies and the staleness line" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

After this plan: the user runs `just dev-install` (it stops running daemons, so the next `serve` imports this code, and re-seeds the skills — CLAUDE.md §1–§2), runs `artmind init` once in each existing vault (it appends `vault_id` to `.artmind/vault.yaml` and changes nothing else), lets Obsidian Git commit that file before running `init` in the vault's other clones, and follows "Live verification (user)" below.

---

## Live verification (user)

Hermetic tests cannot see a real Neo4j (CLAUDE.md §3). Run these against your **local** Neo4j, in throwaway vaults, after the plan is merged and installed. Every command runs in-process (`ARTMIND_NO_PROXY=1`), so a stale `serve` daemon cannot answer with old code (CLAUDE.md §1). Ingesting needs your machine-level LLM config (`~/.artmind/config.env`); nothing else here costs LLM calls.

**Use two databases that hold nothing you need** — step 6 runs `session initiate`, which wipes its database:

- `planbshared` — the "shared AuraDB" stand-in, used by clones A and B;
- `planbc` — clone C's own graph, the "separate local Neo4j" case.

With Neo4j Desktop or Enterprise, create both in Neo4j Browser: `:use system`, then `CREATE DATABASE planbshared;` and `CREATE DATABASE planbc;` — the vaults' `config.env` then set `ARTMIND_KG_NEO4J_DATABASE` to the name, URI `neo4j://127.0.0.1:7687`. Community edition serves one database per server, so run two throwaway servers instead: `docker run -d --name planb-shared -p 7688:7687 -e NEO4J_AUTH=neo4j/planbpass neo4j:5` and `docker run -d --name planb-c -p 7689:7687 -e NEO4J_AUTH=neo4j/planbpass neo4j:5`, and use URIs `neo4j://127.0.0.1:7688` / `:7689` with database `neo4j`.

### 0. Install and set up

```bash
cd ~/projects/artmind9 && just dev-install      # stops daemons, installs this checkout, seeds skills
export ARTMIND_NO_PROXY=1
export LIVE=$HOME/planb-live && rm -rf "$LIVE" && mkdir -p "$LIVE" && cd "$LIVE"
git init -q --bare --initial-branch=main remote.git   # stands in for GitHub
git clone -q remote.git A 2>/dev/null && cd "$LIVE/A"   # (an empty clone; the first push creates main)
git config user.email a@example.com && git config user.name A
artmind init                                     # prints "Vault id: <uuid> (new ...)"
grep -n vault_id .artmind/vault.yaml             # the appended, quoted uuid
$EDITOR .artmind/config.env                      # point at planbshared (see above)
artmind setup                                    # "Neo4j constraints:" now lists artmind_sync_state_vault_id
```

Obsidian Git is simulated with plain git: `git add -A && git commit -qm MSG && git pull -q --no-rebase origin main; git push -q origin HEAD:main`.

Two Cypher checks are used below; run them in Neo4j Browser (or `cypher-shell`) against the database named:

```cypher
MATCH (s:ArtmindSyncState) RETURN s.vault_id, s.last_applied_commit, s.applied_at;
MATCH (d:Document) RETURN d.name, d.fingerprint ORDER BY d.name;
```

### 1. One machine: fingerprints are written, bookmarks set

```bash
cd "$LIVE/A"
mkdir notes
printf '# Acme\n\nAcme Bank is headquartered in Pune. Its CEO is Priya Rao.\n' > notes/acme.md
artmind ingest sync notes/acme.md --domain general
git add -A && git commit -qm "A: acme" && git push -q origin HEAD:main
artmind vault status        # HEAD <sha>; Sync: graph none  no bookmark ...; structured none  no bookmark ...
artmind vault sync          # refuses: "no graph or structured bookmark recorded yet -- pass --bootstrapEmpty ... or --bootstrapSynced"
artmind vault sync --bootstrapSynced --compact   # {"bootstrap":"synced","stores":["graph","structured"],"graph_bookmark":"<HEAD>","structured_bookmark":"<HEAD>"}
git for-each-ref refs/artmind                    # nothing -- artmind never writes a ref
git status --short                               # nothing staged or modified by sync
artmind vault status        # both stores: current
```

Cypher: one `ArtmindSyncState` row with this vault's `vault_id` and HEAD; the `acme` Document has a 64-hex `fingerprint`. It must equal the committed folder's digest:

```bash
uv run --project ~/projects/artmind9 python -c "
from pathlib import Path; from artmind.sync_state import folder_fingerprint
import glob; [print(d, folder_fingerprint(Path(d))) for d in glob.glob('.artmind/data/kg/general/*/')]"
```

### 2. Second clone, same Neo4j database (shared-graph topology)

```bash
cd "$LIVE" && git clone -q remote.git B && cd "$LIVE/B"
git config user.email b@example.com && git config user.name B
artmind init                                     # "Vault id: <same uuid>" -- NOT "(new ...)": it came with the clone
cp "$LIVE/A/.artmind/config.env" .artmind/config.env   # same graph (planbshared): config.env is gitignored
artmind vault status        # graph: <A's HEAD>  current  (read from the shared graph); structured: none  no bookmark
artmind vault sync          # refuses: "no structured bookmark ...; add `--store structured` to bootstrap that store alone"
artmind vault sync --store structured --bootstrapEmpty --compact   # "stores":["structured"]; never connects to Neo4j
artmind vault status        # both current
```

Now machine A ingests again and B follows:

```bash
cd "$LIVE/A"
printf '# Beta\n\nBeta Ltd is a supplier to Acme Bank.\n' > notes/beta.md
artmind ingest sync notes/beta.md --domain general
git add -A && git commit -qm "A: beta" && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main
artmind query domains-overview --compact >/dev/null   # NO stderr line: the shared graph already has beta (fingerprint matches)
artmind vault sync --dryRun --compact     # "replay":0,"unchanged":1
artmind vault sync --compact              # "replayed":0,"unchanged":1, graph_bookmark = B's HEAD
```

Cypher: still one `ArtmindSyncState` row, now at B's HEAD — the bookmark belongs to the store, not the machine.

### 3. "Pull first": the shared bookmark is ahead of a clone

```bash
cd "$LIVE/A"
printf '# Gamma\n\nGamma Corp audits Acme Bank.\n' > notes/gamma.md
artmind ingest sync notes/gamma.md --domain general
git add -A && git commit -qm "A: gamma" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
artmind vault sync --compact              # A advances the shared graph bookmark to its HEAD
cd "$LIVE/B"                              # B has NOT pulled
artmind vault status        # graph: <A's HEAD>  not in this clone's history — let Obsidian Git pull first
artmind vault sync          # refuses: "the graph bookmark ... is not a commit in this clone ... pull"
git pull -q --no-rebase origin main && artmind vault sync --compact   # ok; nothing retracted, gamma unchanged
```

### 4. A third clone with its OWN graph (separate-local topology)

```bash
cd "$LIVE" && git clone -q remote.git C && cd "$LIVE/C"
git config user.email c@example.com && git config user.name C
artmind init && $EDITOR .artmind/config.env       # point at planbc
artmind setup
artmind vault status        # graph: none  no bookmark (its own, empty graph)
artmind vault sync --bootstrapEmpty --compact     # "replayed":3,"unchanged":0 -- a full replay, nothing to skip
artmind query domains-overview --compact >/dev/null   # no stderr line: current
cd "$LIVE/A"
printf '# Delta\n\nDelta Inc is a customer of Beta Ltd.\n' > notes/delta.md
artmind ingest sync notes/delta.md --domain general
git add -A && git commit -qm "A: delta" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/C" && git pull -q --no-rebase origin main
artmind query domains-overview --compact 2>err.txt | python3 -m json.tool >/dev/null && cat err.txt
#   stdout parses as JSON; err.txt holds exactly:
#   artmind: graph is 1 docs / 0 tables behind the vault — run `artmind vault sync`
time artmind query domains-overview --compact >/dev/null 2>&1                                  # note the time
time ARTMIND_NO_STALENESS_CHECK=1 artmind query domains-overview --compact >/dev/null 2>&1     # the difference is the check's cost
artmind vault sync --compact     # "replayed":1
rm err.txt
```

### 5. Through the `serve` daemon

```bash
cd "$LIVE/A"
printf '# Epsilon\n\nEpsilon GmbH lends to Delta Inc.\n' > notes/epsilon.md
artmind ingest sync notes/epsilon.md --domain general
git add -A && git commit -qm "A: epsilon" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/C" && git pull -q --no-rebase origin main
(unset ARTMIND_NO_PROXY; artmind serve >/dev/null 2>&1 &) ; sleep 5
curl -s http://127.0.0.1:8377/health                                   # {"service":"artmind",...}
(unset ARTMIND_NO_PROXY; artmind query domains-overview --compact >/dev/null)   # the same stderr line, via the proxy
cd ~/projects/artmind9 && just dev-stop-daemons && cd "$LIVE/C"
artmind vault sync --compact
```

(The daemon was started from `$LIVE/C`, so it serves C's vault.)

### 6. `session close` / `session initiate` carry the bookmark

```bash
cd "$LIVE/A"
artmind vault status                 # note the graph bookmark
artmind session close
artmind session initiate --yes       # "Sync bookmark: restored with the graph (...)"
artmind vault status                 # the same graph bookmark as before
```

### 7. Migrating a vault that synced before this plan

```bash
cd "$LIVE/C"
PREV=$(git rev-parse HEAD~1)
python3 -c "import json; json.dump({'last_synced_commit': '$PREV'}, open('.artmind/state.json', 'w'))"
```

Delete C's bookmark node in its own database (`MATCH (s:ArtmindSyncState) DETACH DELETE s`), then:

```bash
artmind vault status          # both stores: <PREV>  current (PREV..HEAD holds only epsilon, already in C's graph
                              # with a matching fingerprint), plus "(legacy last_synced_commit <PREV> still seeds ...)"
artmind vault sync --compact  # "bases":{"graph":"<PREV>","structured":"<PREV>"}, "unchanged":1
cat .artmind/state.json       # only last_structured_commit remains; last_synced_commit is gone
```

### Clean up

```bash
rm -rf "$LIVE"
```

Then drop the two databases (`:use system`, `DROP DATABASE planbshared;`, `DROP DATABASE planbc;`) or `docker rm -f planb-shared planb-c`.

---

## Self-review

### Scope → tasks

| Scope item | Task(s) |
|---|---|
| A2 — `(:ArtmindSyncState {vault_id, last_applied_commit, applied_at})`; its Cypher in one place | 1 (`sync_state.py`), used by 4 |
| A2 — constraint on `ArtmindSyncState.vault_id` where constraints are created | 1 (`_setup_neo4j`, `setup_all`) |
| A2 — `vault_id` minted once by `init` into committed `vault.yaml`; appended to existing vaults without disturbing the file | 3 |
| A2 — structured bookmark in `state.json` under a new key (`last_structured_commit`) | 4 |
| A2 — migrate `last_synced_commit` into both bookmarks, then retire it; the graph-bookmark-already-exists conflict rule | 4 (rule: decision 1) |
| A2 — no pinned ref: artmind only reads git (D2); the ancestor-of-HEAD check (decision 7, below) covers what pinning was meant to, and names the bootstrap-flag recovery when it refuses | 6 (dropped — see decision 9, below), 4 (the check and its refusal message) |
| A2 — `session initiate` restores the graph bookmark (from the graph snapshot itself; deviation: decision 8) | 7 |
| A2 — `--domain`-scoped sync advances neither bookmark | 4 (existing test retargeted to both bookmarks; `test_a_domain_scoped_run_pins_nothing` was dropped with Task 6) |
| A2 — `--bootstrapEmpty`/`--bootstrapSynced` per store | 4 (`--store`; tests `test_store_structured_bootstraps_duckdb_alone...`, `test_store_graph_bootstrap_synced_stamps_only_the_graph`) |
| A2 — graph ahead of structured (or the reverse): each store applies its own range; `classify_diff` per store without re-reading git needlessly | 4 (`test_the_graphs_own_bookmark_wins...`, `test_a_graph_ahead_of_the_structured_store...`, `test_equal_bases_classify_the_structured_tables_once`) |
| A3 — fingerprint recorded by every graph write of a staging folder (ingest, table2graph, vault sync replay, and every other `_write_to_neo4j` caller) | 2 |
| A3 — exact byte definition, agreed between ingest (file) and apply (committed blob) | 1 (definition), 2 (ingest side), 5 (apply side, `git cat-file`) |
| A3 — apply skips a matching document, one Cypher read per sync | 5 |
| A4 — staleness line on `vault status` and every `query` command, stderr only, never in `--compact` JSON | 8 (message), 9 (`vault status`), 10 (`query`) |
| A4 — cheap: rev-parse, diff names, one Cypher read; harmless with no vault/git/Neo4j/bookmark | 8 (`query_staleness_warning`'s order), 10 (swallow, 2 s cap, env switch) |
| A4 — hook point given the `_entry` proxy and the `serve` daemon | 10 (decision 4) |
| Fuller `vault status`: HEAD, both bookmarks, pending docs/tables, merge in progress, `.artmind` conflicts, `--compact` JSON | 9 |
| Docs: `docs/vault.md`, `vault sync`/`vault status` docstrings, skills, justfile | 11 (justfile: no change needed, see File map); `vault status` docstring in 9 |
| Final full suite and CLI help | 11 |
| Live verification against a local Neo4j, second clone on the same Neo4j, second database for the local case | "Live verification (user)" |

### Placeholder scan

No "TBD", "similar to Task N", or step without its code. Every replaced block is quoted exactly and was matched by the verification script (below); every test step names the exact command and the observed result.

### Type and name consistency

`sync_state`: `CONSTRAINT_CYPHER`, `FINGERPRINTED_FILES`, `staging_fingerprint`, `folder_fingerprint`, `set_document_fingerprint`, `read_document_fingerprints(doc_ids, *, timeout)`, `read_graph_bookmark(vault_id, *, timeout)`, `write_graph_bookmark(vault_id, commit)` (Task 1) → used by Tasks 2, 4, 5, 8 and faked with the same signatures by `_FakeGraphState` (Task 4). `manifest.read_vault_id`/`ensure_vault_id` (Task 3) → `vault_sync.vault_id_or_raise` (Task 4), `status_report`, `query_staleness_warning` (Tasks 8–9). `vault_sync`: `STORES`, `STRUCTURED_BOOKMARK_KEY`, `LEGACY_CURSOR_KEY`, `Bookmarks.own/effective`, `read_bookmarks`, `check_bookmark`, `_advance_bookmarks` (Task 4; `check_bookmark`'s ancestor refusal also names the bootstrap-flag recovery, added in a Task 4 follow-up); `_cat_blobs`, `_document_id`, `committed_fingerprints`, `drop_unchanged` (Task 5); `_is_unchanged`, `ADVISORY_TIMEOUT`, `_store_pending`, `_graph_pending`, `pending_work`, `staleness_message`, `query_staleness_warning` (Task 8); `operation_in_progress`, `unresolved_artmind_conflicts`, `status_report` (Task 9). `sync(..., store=)` (Task 4) ← `vault sync --store` (Task 4). Result keys `stores`, `bases`, `graph_bookmark`, `structured_bookmark`, `unchanged`, `cursor_advanced`, `cursor_would_advance` are the same in code, tests and docs.

### Ordering and green suite

Each task leaves `uv run --group dev pytest test/ -q` green; the verification record below has the count after each one. Task 4 is the only task that changes existing tests' expectations, and it does so in the same task: the autouse `graph` fixture gives every existing `vault_sync` test a `vault_id` and a graph bookmark store, and the seven assertions on the retired cursor move to `_bookmarks(repo)`.

### Design decisions the spec did not settle (for review)

1. **Migration conflict rule — the graph's own bookmark wins.** When `state.json` has a legacy `last_synced_commit` L and the graph already has an `:ArtmindSyncState` G for this `vault_id` (another machine sharing the AuraDB set it), the graph starts from G and the structured store from L. G describes the graph itself; L only recorded what this machine applied, and on a shared graph the other machine has since moved it. A too-old start re-applies idempotent work, which fingerprints make cheap; a too-new start would skip work. L is still exactly right for this machine's DuckDB. L is removed from `state.json` by the first run after which both stores have their own bookmark (a `--store`-limited run keeps it while the other store still needs it).
2. **Per-store diff strategy.** The graph needs the full `classify_diff(G, HEAD)`. The structured store needs only its tables. When S = G (the normal case) it reuses the graph plan's tables: git is read exactly once, as before this plan. When S ≠ G it runs `_classify_structured_text_diff(S, HEAD)` alone — one extra scoped `git diff`. Every table the graph projects is also restored into DuckDB first (idempotent, from HEAD), because `table2graph` reads the rows from DuckDB and a structured bookmark behind the graph's would otherwise project stale rows.
3. **Fingerprint byte definition.** sha256 over, for each of `document.json`, `chunks.json`, `observations.json`, `relationships.json` in that order: the file name, NUL, then either `absent` NUL or the byte length in ASCII decimal, NUL, and the raw bytes. **Not `observations.json` alone** (the spec's wording): Plan A made track A replay a folder when any file changes (§14 A4), and those four are exactly what `_load_staged` writes to the graph; hashing only observations would let a chunks- or document-only change be skipped on a local Neo4j. Raw bytes, not canonical JSON, so ingest never re-parses what it wrote; apply reads the committed blobs with `git cat-file --batch` (no smudge/eol/LFS filter), which are the bytes ingest wrote because artmind's `.gitattributes` sets no text/eol/filter rule for `.artmind/data/**`. A user rule that does rewrite them produces a mismatch, and a mismatch only ever causes a replay. A folder with no `observations.json` has no fingerprint (and is not replayed anyway). `vault sync`'s own replay writes the fingerprint of the files `git archive` materialised; with no user filter those equal the blobs.
4. **Staleness-warning hook point: a result callback on the `query` Click group.** `_entry.py` must stay stdlib-only and cannot read git or Neo4j. The callback runs after a query succeeded (never for `--help` or a failure), writes to stderr, and runs unchanged inside the `serve` daemon, whose `CliRunner` stderr `_entry._proxy` already forwards — no protocol change. It is gated on `state.json` holding a bookmark, so a machine that never ran `vault sync` pays nothing (no git, no Neo4j); then one `git rev-parse` and one bookmark read capped at 2 s; only when HEAD differs, the classification and one fingerprint read. All exceptions are swallowed, `run_command`'s DEBUG logging is muted for its duration, and `ARTMIND_NO_STALENESS_CHECK=1` disables it. A machine that only ever ran `vault sync --store graph` has no structured bookmark and so gets no warning — accepted.
5. **Staleness message.** `artmind: graph is N docs / M tables behind the vault — run \`artmind vault sync\`` (the spec's wording), where N counts documents to replay (not already fingerprint-matched) or retract (still present in the graph), and M counts every table either store must restore or project (their union). When only DuckDB is behind: `artmind: structured store is M tables behind the vault — ...`. When a bookmark is outside this clone's history: a "let Obsidian Git pull" line. No bookmark at all: silent on queries (`vault status` shows it). Table projections have no fingerprint (`table__*` is gitignored), so on a shared graph a changed table counts until `vault sync` runs on this machine.
6. **`--store graph|structured` is a new `vault sync` option**, and the bootstrap flags apply to every store in the run. Without it there is no way to bootstrap DuckDB on a new machine whose shared graph already has a bookmark. With no flag and one store unbookmarked, `vault sync` refuses and names that store and the `--store` form.
7. **A bookmark must be an ancestor of HEAD, or `vault sync` refuses** ("pull first"). Diffing from a commit HEAD does not contain reads everything it has and HEAD lacks as removals — on a shared graph, the other machine's new documents would be retracted. Two machines racing `vault sync` on one AuraDB leave whichever bookmark was written last; the loser's next run is refused until it pulls, then re-diffs a larger range that fingerprints make cheap.
8. **`session initiate` restores the bookmark the graph had, not the manifest's `vault_commit` — a deviation from spec §6 A2.** The spec sets the graph bookmark from a snapshot manifest's `vault_commit` when `vault_dirty` is false. `vault_commit` is the vault's HEAD at export, and a clean HEAD can be *ahead* of the graph: Obsidian Git pulls another machine's commits, nobody runs `vault sync`, `snapshot create` runs, and a restore would then mark those commits applied although the graph never received them — silently, forever on a local Neo4j. (Also, `session close` writes a graph `.tar.gz` with no manifest; only `snapshot create`'s zip has one.) So `:ArtmindSyncState` is exported with the graph and restored with it, which is never ahead of the restored content; an older snapshot restores none, and the next sync needs a bootstrap flag, as the spec already prescribes for the dirty case. To follow the spec literally instead, `import_graph` would need the manifest passed in, and the data-loss case above would have to be accepted.
9. **No pinned ref — Task 6 is dropped, deviating from spec §6 A2's "pin each advanced bookmark" and from D2's own wording ("artmind only reads git ... update-ref on its own private ref").** The spec permitted a private-ref write; on review this is unnecessary and against D1/D2's actual intent (artmind never writes to `.git`). Obsidian Git merges rather than rebasing (D5), so a bookmarked commit stays reachable through ordinary history with nothing pinning it. Should history ever be rewritten anyway, decision 7's ancestor-of-HEAD check already refuses `vault sync` from that stale bookmark, pinned or not — a pin bought no additional safety, only a git write this design otherwise has zero of. `test/test_no_git_writes.py` keeps no `update-ref` exception, and `check_bookmark`'s refusal now names the recovery (`--store <name> --bootstrapSynced`/`--bootstrapEmpty`) for the one case pulling can't fix.
10. **`vault_id` format and minting.** A `uuid4`, quoted (`vault_id: "<uuid>"`) so YAML never reads it as a number, appended as text with a comment so the user's file is otherwise byte-identical. A present-but-empty key is refused, not duplicated. If two machines both run `init` before either pulls the other's `vault_id`, git reports a conflict on that line; keeping either value is fine (the loser's bookmark node is simply orphaned). Deriving the id from the root commit would avoid the race but make two vaults forked from one template share a bookmark — the case `vault_id` exists to prevent.
11. **`vault sync --dryRun` now reads the graph** (its bookmark, and fingerprints to report `unchanged`). A dry run with `--store structured` still never connects.
12. **Retractions are always executed, never fingerprint-skipped** (retracting an absent document is already a no-op); they are only *counted* as pending while the graph still has the document.
13. **`Document.fingerprint` is an ordinary property**, set to null (removed) when a folder has none, so a document committed without one can never be skipped. It is not added to `graph_query`'s internal-property strip list; it may appear where Document properties are listed.

### Accepted residual risk

- **Per-query cost on AuraDB.** On a machine that has synced, every successful `query` opens one extra driver for the bookmark read — a TLS/routing handshake of a few hundred milliseconds on AuraDB (the query commands already open one per graph call). Measured by step 4 of the live verification; `ARTMIND_NO_STALENESS_CHECK=1` removes it. Caching the bookmark locally was rejected: on a shared graph another machine moves it.
- **User `.gitattributes` rules** (eol conversion, LFS) on `.artmind/data/**` make committed blobs differ from the bytes ingest hashed: those documents are replayed instead of skipped (never the reverse). Same class as Plan A's accepted `git archive` risk.
- **Very large bootstraps** pass one pathspec per replayed folder to `git ls-tree`, as `_materialize` already does to `git archive`; a vault with tens of thousands of documents could approach the OS argument-length limit on both.

## Verification record

- Worktree: `git worktree add --detach <scratchpad>/verify 290303d`, then a script parsed this file and applied, in document order, every `**Create**`, `**Append to**` and `**Replace in** … **with:**` block (a replacement must match exactly once), running `uv run --group dev pytest test/ -q` after each task.
- Results after each task: Task 1: `2350 passed, 14 skipped`; Task 2: `2355 passed, 14 skipped`; Task 3: `2362 passed, 14 skipped`; Task 4: `2376 passed, 14 skipped`; Task 5: `2382 passed, 14 skipped`; Task 6: `2389 passed, 14 skipped`; Task 7: `2391 passed, 14 skipped`; Task 8: `2400 passed, 14 skipped`; Task 9: `2405 passed, 14 skipped`; Task 10: `2413 passed, 14 skipped`; Task 11: `2413 passed, 14 skipped`.
- Expected-failure claims: for every task with tests, the task's non-test source files were reverted to the previous task's state and the task's test files re-run; the "Expected: FAIL" text in each Step 2 is what that produced. Re-checked on the plan-applied worktree for Tasks 1, 4, 6, 8 and 10: collection error; `19 failed, 127 passed`; `5 failed, 193 passed`; `9 failed, 117 passed`; `3 failed, 5 passed` — as stated.
- The worktree was removed afterwards; nothing but this file was committed.

**Post-authoring deviation.** Task 6 (pinning each advanced bookmark under `refs/artmind/`) was
dropped during execution (see decision 7/9, "No pinned ref") — artmind must never write to this
vault's `.git`, not even a private ref, and the ancestor check (decision 7) already made the pin
redundant. The counts above, recorded when Task 6 was still part of the plan, are historical only;
actual execution's pass counts diverge from Task 6 onward (fewer tests, since Task 6 added none),
and each task was independently re-verified against the live suite as it landed, not against this
table.
