# Pipeline Checklist — Plan 1: Backend & CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement §6 (B1–B9) of `docs/superpowers/specs/2026-10-03-obsidian-pipeline-checklist-design.md`: the full projection rebuild never runs as one huge transaction, an ingest job remembers the rebuild and sweeps it owes, `projection status` counts what is missing, `projection rebuild --sweep` is the one repair command, a table this machine ingested is not reported as "to apply", the admin console accepts a `?prompt=` deep link, the plugin fixtures cover every new shape, and the docs say all of it. The last task repairs the live `my_second_brain` graph.

**Architecture:** Batching moves from `table2graph` into `projection` (`REBUILD_BATCH`, `batch_keys`, `rebuild_in_batches`). A new `projection.full_rebuild_batched(domains)` reads every key in one read transaction, commits the batches, and writes `:ProjectionState` in its own final transaction only after every batch succeeded (and only when unscoped). `ingest.rebuild_projection` uses it, so the worker and `ingest sync` get it for free. The embed sweeps gain a `strict=True` mode that raises `ingest.SweepSkipped` instead of logging and returning 0; `rebuild_projection` turns that into `sweep_errors`. `ingestion_jobs` gains `finalize_domains` / `finalize_state` / `finalize_error`; the worker appends a domain when a file's commit defers and finishes the persisted list after its file loop, even with 0 queued files. `projection.status` adds three grouped count queries. A machine-local fingerprint of each table's committed structured text (in `.artmind/state.json`) lets `vault status` skip tables this machine already holds.

**Tech Stack:** Python ≥3.14 (uv), Click/rich-click, Neo4j 5/2025 (Cypher 25), SQLite registry, DuckDB, FastAPI + Jinja2, pytest (hermetic, `CliRunner`), vitest (plugin), Docker (disposable Neo4j for the memory check only).

---

## Ground rules for whoever executes this

- Read `CLAUDE.md` first, especially "Testing implications". Tests are hermetic: no Neo4j, no network. A mocked session answers every query, so **every graph test here asserts on the transactions opened, the Cypher sent and its parameters**, never on summary counts alone (`test/test_update.py`'s `run_side_effect` recorders are the pattern).
- Never touch the container `neo4j-my_second_brain`. The only Docker work in this plan (Task 3) starts its own uniquely named container on its own port and refuses to run if the name or port is taken.
- The global `artmind` command is an editable install. Hermetic tests do not need a reinstall. Operator steps that run the real CLI do: `just dev-install`, which also runs `just dev-stop-daemons`, then `ARTMIND_NO_PROXY=1` on every command.
- Run single tests with `uv run --group dev pytest test/<file>::<name> -v`. Run the full suite with `just dev-test`.

## File structure

| File | Create / Modify | Responsibility |
|---|---|---|
| `artmind/projection.py` | Modify | Owns `REBUILD_BATCH`, `batch_keys`, `rebuild_in_batches` (moved from `table2graph`); new `full_rebuild_batched`; `status` gains `unembedded_entities`, `unprojected_keys`, `domains`, `drift` |
| `artmind/table2graph.py` | Modify | Keeps the names `REBUILD_BATCH`, `batch_keys`, `_rebuild_in_batches` as thin delegates to `projection` |
| `artmind/ingest.py` | Modify | `SweepSkipped`; `strict` on `_sweep_embeddings` / `_sweep_chunk_embeddings`; `_sweep_domain`; `rebuild_projection` goes through `full_rebuild_batched` and reports `sweep_errors`; new `rebuild_and_sweep_all` |
| `artmind/db.py` | Modify | `ingestion_jobs.finalize_domains`, `finalize_state`, `finalize_error` (CREATE + migration) |
| `artmind/jobs.py` | Modify | `_finalize`, `_add_finalize_domain`, `_get_finalize`, `_set_finalize_state`, `_resolve_failed_finalizes`; `finalize` block in `_get_job_status`, `_fetch_active_jobs`, `_get_job_results` |
| `artmind/worker.py` | Modify | Persist owed domains per file; `_finalize_job` after the loop, exceptions caught |
| `artmind/cli.py` | Modify | `projection rebuild --sweep`; help text for the `projection` group, `rebuild`, `status`; (conditional) `projection synthesize --countOnly` |
| `artmind/synthesize.py` | Modify (conditional, Task 9) | `count_only` |
| `artmind/vault_sync.py` | Modify | `TABLE_FINGERPRINTS_KEY`, `git_blob_oid`, `table_text_fingerprint`, `record_table_text_fingerprints`, `_drop_tables_held_here`; used by `_store_pending` and `_graph_pending` |
| `artmind/structured/text_export.py` | Modify | `export_structured_text` records this machine's table fingerprints |
| `artmind/webui/app.py` | Modify | `/` passes a `prefill` from `?prompt=` (admin console only, capped) |
| `artmind/webui/templates/admin.html` | Modify | textarea renders `prefill` |
| `artmind/webui/static/app.js` | Modify | enable Send and autogrow when the composer starts filled; never send |
| `scripts/rebuild_memory_check.py` | Create | Disposable-container before/after check for B1 |
| `justfile` | Modify | `dev-rebuild-memcheck`, `projection-rebuild-sweep` |
| `docs/INSTALL.md` | Modify | Neo4j memory settings, colima VM minimum |
| `docs/projection-pipeline.md` | Modify | §4: batched full rebuild, `--sweep`, `finalize` |
| `artmind/skills/artmind-ingestion-helper/SKILL.md` | Modify | `projection rebuild --sweep`, `finalize` |
| `artmind/skills/artmind-curate/SKILL.md` | Modify | `projection rebuild --sweep` |
| `test/test_projection_batched_rebuild.py` | Create | B1 |
| `test/test_sweep_failures.py` | Create | B2 (sweeps) |
| `test/test_job_finalize.py` | Create | B2 (columns, read paths) |
| `test/test_worker_finalize.py` | Create | B2 (worker) |
| `test/test_projection_status.py` | Modify | B3 |
| `test/test_projection_rebuild_sweep.py` | Create | B4 |
| `test/test_synthesize.py` | Modify (conditional) | B5 |
| `test/test_vault_status_own_tables.py` | Create | B6 |
| `test/test_webui_app.py` | Modify | B7 |
| `test/test_reingest.py` | Modify (lines 443–487) | follow `rebuild_projection`'s new seams |
| `test/test_plugin_fixtures.py` | Modify | B8 scenarios, sweep stubs, 64-hex normalisation, `_behind` |
| `obsidian/artmind-obsidian/test/fixtures/*.json` | Regenerate | B8 |

---

## Task 1 (B1a): Batching lives in `projection`

**Files:**
- Modify: `artmind/projection.py` (insert after `full_rebuild`, currently lines 1371–1389)
- Modify: `artmind/table2graph.py` (imports at lines 43–56; replace lines 1302–1375: `REBUILD_BATCH`, `batch_keys`, `_rebuild_in_batches`)
- Test: `test/test_projection_batched_rebuild.py` (create)

- [ ] **Step 1: Write the failing test**

Create `test/test_projection_batched_rebuild.py`:

```python
"""The batched projection rebuild (plan 2026-10-03, B1).

Root cause, found live on 2026-10-03: job 5f5c2d11's deferred full rebuild ran
4,332 keys in ONE transaction, which ran colima's 2 GB VM out of memory and
killed Neo4j. Every assertion here is on the transactions opened and the
queries sent, in order -- a bare mock would report success for any of them.
"""
from __future__ import annotations

import pytest

import artmind.graph_query as graph_query
import artmind.projection as projection
import artmind.same_as as same_as


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return None


class _Recorder:
    """One Neo4j session, recording every transaction it opens, every query
    it runs and every `projection.rebuild` batch, in order. `all_keys`'
    Observation query answers with `self.keys`; every other query is empty."""

    def __init__(self):
        self.keys: list[tuple[str, str, str]] = []
        self.log: list[tuple] = []

    def run(self, cypher, **params):
        self.log.append(("run", cypher, params))
        if "RETURN DISTINCT n.key" in cypher and "(n:Observation)" in cypher:
            return _Rows([{"key": "|".join(k)} for k in self.keys])
        return _Rows([])

    def execute_read(self, fn, *args, **kwargs):
        self.log.append(("read",))
        return fn(self, *args, **kwargs)

    def execute_write(self, fn, *args, **kwargs):
        self.log.append(("write",))
        return fn(self, *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def events(self) -> list:
        """The log reduced to what the assertions care about."""
        out: list = []
        for entry in self.log:
            if entry[0] in ("read", "write"):
                out.append(entry[0])
            elif entry[0] == "rebuild":
                out.append(("rebuild", len(entry[1])))
            elif entry[0] == "run" and "MERGE (s:ProjectionState" in entry[1]:
                out.append("record")
        return out

    def batches(self) -> list[list[tuple]]:
        return [entry[1] for entry in self.log if entry[0] == "rebuild"]


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(graph_query, "neo4j_session", lambda *a, **k: rec)
    monkeypatch.setattr(same_as, "load_groups", lambda: [])
    monkeypatch.setattr(same_as, "content_hash", lambda path=None: "same-as-hash")
    monkeypatch.setattr(projection, "schema_set_hash", lambda domains_dir=None: "schema-hash")

    def fake_rebuild(tx, keys, **kwargs):
        rec.log.append(("rebuild", list(keys), kwargs))
        return {"rebuilt": len(keys), "deleted": 0, "absent": 0, "keys": len(keys)}

    monkeypatch.setattr(projection, "rebuild", fake_rebuild)
    monkeypatch.setattr(projection, "REBUILD_BATCH", 3)
    return rec


def _keys(n: int, domain: str = "general") -> list[tuple[str, str, str]]:
    return [(f"k{i}", "PERSON", domain) for i in range(n)]


def test_rebuild_in_batches_commits_one_write_transaction_per_batch(recorder):
    totals = projection.rebuild_in_batches(_keys(7, "d"))

    assert recorder.events() == ["write", ("rebuild", 3), "write", ("rebuild", 3), "write", ("rebuild", 1)]
    assert (totals["rebuilt"], totals["keys"], totals["batches"]) == (7, 7, 3)
    for entry in recorder.log:
        if entry[0] == "rebuild":
            assert entry[2]["same_as_groups"] == []
            assert callable(entry[2]["synthesis_loader"]), "a synthesis must survive the rebuild"


def test_rebuild_in_batches_names_the_batch_that_failed(recorder, monkeypatch):
    calls: list[list] = []

    def boom(tx, keys, **kwargs):
        calls.append(list(keys))
        if len(calls) == 2:
            raise RuntimeError("MemoryPoolOutOfMemoryError")
        return {"rebuilt": len(keys), "keys": len(keys)}

    monkeypatch.setattr(projection, "rebuild", boom)

    with pytest.raises(RuntimeError, match=r"batch 2/3 \(MemoryPoolOutOfMemoryError\)"):
        projection.rebuild_in_batches(_keys(7, "d"))
    assert len(calls) == 2, "no batch may run after the one that failed"


def test_table2graph_keeps_its_names_for_the_moved_helpers():
    import artmind.table2graph as t2g

    assert t2g.batch_keys is projection.batch_keys
    assert t2g.REBUILD_BATCH == projection.REBUILD_BATCH == 400
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_projection_batched_rebuild.py -v`
Expected: FAIL. Every test errors in setup with `AttributeError: <module 'artmind.projection' ...> has no attribute 'REBUILD_BATCH'`, and the last one fails on `projection.batch_keys`.

- [ ] **Step 3: Move the batching into `projection.py`**

In `artmind/projection.py`, insert directly after `full_rebuild` (after current line 1389):

```python
#: Keys rebuilt per transaction. Neo4j caps a transaction's memory
#: (`db.memory.transaction.total.max`), and a rebuild's footprint grows with
#: its key count: a 1223-row table (2450 keys) failed at commit on a 343 MiB
#: cap once its entities carried embeddings, and a 4,332-key directory ingest
#: ran a 2 GB colima VM out of memory and killed Neo4j (2026-10-03).
REBUILD_BATCH = 400


def batch_keys(keys, groups: list[list[tuple]], size: int = REBUILD_BATCH) -> list[list[tuple]]:
    """Split `keys` into rebuild batches of about `size`. Pure.

    A same-as group never straddles two batches: `_plan_groups` only folds a
    group whose members are all in the keys it is given, so the group is kept
    whole even when that overfills a batch. Deterministic (sorted), so a rerun
    rebuilds in the same order.
    """
    group_of: dict[tuple, int] = {}
    for index, group in enumerate(groups):
        for member in group:
            group_of.setdefault(tuple(member), index)
    units: dict[object, list[tuple]] = {}
    for key in sorted({tuple(k) for k in keys}):
        units.setdefault(("group", group_of[key]) if key in group_of else ("key", key), []).append(key)
    batches: list[list[tuple]] = []
    current: list[tuple] = []
    for unit in units.values():
        if current and len(current) + len(unit) > size:
            batches.append(current)
            current = []
        current.extend(unit)
    if current:
        batches.append(current)
    return batches


def rebuild_in_batches(keys, groups: list | None = None, *, size: int | None = None) -> dict:
    """Rebuild `keys`, one write transaction per batch of `size` (default
    `REBUILD_BATCH`).

    The end state equals one big `rebuild`: each batch deletes and rewrites
    every `RELATES_TO` edge touching its own entities, resolving them from
    both ends, so an edge to an entity in a later batch is written when that
    batch runs. A zero-observation key is GC'd in whichever batch holds it.

    The cost is a window in which part of the projection is stale, and if a
    batch fails it stays stale until repaired. Every batch is idempotent, so
    the repair is to run the same command again (or `artmind projection
    rebuild --sweep`).

    `groups` are the same-as groups to batch and rebuild with. None reads
    `same_as.yaml` from disk -- right for `ingest table2graph`, wrong for
    `vault sync`, which passes the file as committed at `head` so an
    uncommitted edit never reaches the graph (spec 2026-09-26 §6 A1).
    """
    from artmind import same_as
    from artmind.graph_query import neo4j_session

    groups = same_as.load_groups() if groups is None else groups
    batches = batch_keys(keys, groups, REBUILD_BATCH if size is None else size)
    totals = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": len(batches)}
    with neo4j_session() as session:
        for number, batch in enumerate(batches, start=1):
            try:
                summary = session.execute_write(
                    lambda tx: rebuild(
                        tx, batch, same_as_groups=groups,
                        synthesis_loader=lambda ks: load_synthesis_batch(tx, ks),
                    )
                )
            except Exception as e:
                raise RuntimeError(
                    f"projection rebuild failed on batch {number}/{len(batches)} ({e}); the "
                    "observations are committed but part of the projection is stale -- re-run "
                    "the same command (every batch is idempotent) or `artmind projection rebuild --sweep`"
                ) from e
            for field_name in ("rebuilt", "deleted", "absent", "keys"):
                totals[field_name] += summary.get(field_name, 0)
            logger.info("projection: rebuilt batch {}/{} ({} key(s))", number, len(batches), len(batch))
    return totals
```

- [ ] **Step 4: Make `table2graph` delegate**

In `artmind/table2graph.py`, add to the import block after `from loguru import logger` (line 56):

```python

from artmind.projection import REBUILD_BATCH, batch_keys  # noqa: F401  (re-exported: vault_sync, update, tests)
```

Replace current lines 1302–1375 (the `REBUILD_BATCH` comment and constant, `batch_keys`, `_rebuild_in_batches`) with:

```python
def _rebuild_in_batches(keys: list, groups: list | None = None) -> dict:
    """The projection rebuild for a committed table, one transaction per batch.

    This departs from a document commit, whose rebuild runs inside the same
    transaction as its observations; a table is simply too big for one. The
    batching itself is `projection.rebuild_in_batches`, shared with the full
    rebuild; this wrapper only pins the batch size to this module's
    `REBUILD_BATCH`, read at call time, and keeps the name `vault_sync` and
    `update` import. `groups`: see `projection.rebuild_in_batches`.
    """
    from artmind import projection

    return projection.rebuild_in_batches(keys, groups, size=REBUILD_BATCH)
```

Also update the module docstring's pointer at line 12, "(see `_rebuild_in_batches`)", to "(see `projection.rebuild_in_batches`)".

- [ ] **Step 5: Run the new and existing batching tests**

Run: `uv run --group dev pytest test/test_projection_batched_rebuild.py test/test_table2graph.py -v`
Expected: PASS. That includes `test_batch_keys_splits_by_size_but_never_splits_a_same_as_group`, `test_rebuild_in_batches_commits_one_transaction_per_batch` (it patches `t2g.REBUILD_BATCH = 3`, which the wrapper reads at call time) and `test_rebuild_in_batches_uses_the_groups_it_is_given_not_the_file`.

- [ ] **Step 6: Commit**

```bash
git add artmind/projection.py artmind/table2graph.py test/test_projection_batched_rebuild.py
git commit -m "refactor(projection): own the batched rebuild; table2graph delegates

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2 (B1b): `full_rebuild_batched`, used by `rebuild_projection`

**Files:**
- Modify: `artmind/projection.py` (after `rebuild_in_batches` from Task 1; docstring of `full_rebuild`, lines 1371–1380)
- Modify: `artmind/ingest.py` (`rebuild_projection`, lines 3594–3630)
- Modify: `test/test_reingest.py` (lines 443–487)
- Test: `test/test_projection_batched_rebuild.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_projection_batched_rebuild.py`:

```python
# ── the full rebuild ─────────────────────────────────────────────────────────


def test_full_rebuild_batched_records_projection_state_last_in_its_own_transaction(recorder):
    recorder.keys = _keys(7)

    summary = projection.full_rebuild_batched()

    assert recorder.events() == [
        "read",
        "write", ("rebuild", 3), "write", ("rebuild", 3), "write", ("rebuild", 1),
        "write", "record",
    ]
    (params,) = [e[2] for e in recorder.log if e[0] == "run" and "MERGE (s:ProjectionState" in e[1]]
    assert (params["same_as_hash"], params["schema_hash"]) == ("same-as-hash", "schema-hash")
    assert (summary["recorded"], summary["domains"], summary["batches"]) == (True, ["general"], 3)


def test_a_failed_batch_never_records_the_rebuild(recorder, monkeypatch):
    recorder.keys = _keys(7)
    calls: list = []

    def boom(tx, keys, **kwargs):
        calls.append(list(keys))
        if len(calls) == 3:
            raise RuntimeError("Couldn't connect to 127.0.0.1:7687")
        return {"rebuilt": len(keys), "keys": len(keys)}

    monkeypatch.setattr(projection, "rebuild", boom)

    with pytest.raises(RuntimeError, match="batch 3/3"):
        projection.full_rebuild_batched()
    assert "record" not in recorder.events(), "drift must stay visible after a part-way failure"


def test_a_domain_scoped_full_rebuild_never_records_state(recorder):
    recorder.keys = [("a", "PERSON", "general")]

    summary = projection.full_rebuild_batched(["general"])

    assert "record" not in recorder.events()
    assert summary["recorded"] is False
    key_reads = [e[2] for e in recorder.log if e[0] == "run" and "RETURN DISTINCT n.key" in e[1]]
    assert key_reads == [{"domains": ["general"]}, {"domains": ["general"]}]


def test_a_same_as_group_stays_in_one_batch(recorder, monkeypatch):
    group = [("k1", "PERSON", "general"), ("k5", "PERSON", "general")]
    monkeypatch.setattr(same_as, "load_groups", lambda: [group])
    recorder.keys = _keys(7)

    projection.full_rebuild_batched()

    home = [b for b in recorder.batches() if group[0] in b]
    assert len(home) == 1 and group[1] in home[0]
    assert all(e[2]["same_as_groups"] == [group] for e in recorder.log if e[0] == "rebuild")


def test_rebuild_projection_goes_through_the_batched_full_rebuild(monkeypatch):
    import artmind.ingest as ing

    seen: list = []
    monkeypatch.setattr(
        projection, "full_rebuild",
        lambda *a, **k: pytest.fail("the single-transaction full_rebuild must not run"),
    )
    monkeypatch.setattr(projection, "full_rebuild_batched", lambda domains=None: seen.append(domains) or {"rebuilt": 0})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: set())
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys, strict=False: 0)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda chunk_ids=None, domain=None, strict=False: 0)

    ing.rebuild_projection("general")
    ing.rebuild_projection()

    assert seen == [["general"], None]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_projection_batched_rebuild.py -v`
Expected: the five new tests FAIL. The first four fail with `AttributeError: module 'artmind.projection' has no attribute 'full_rebuild_batched'`. The last fails in `monkeypatch.setattr` for the same missing attribute.

- [ ] **Step 3: Add `full_rebuild_batched`**

In `artmind/projection.py`, after `rebuild_in_batches`:

```python
def full_rebuild_batched(domains: list[str] | None = None) -> dict:
    """Rebuild every key (in `domains`, or every domain), in batches.

    The deferred path for a directory ingest and the recovery path for drift.
    Unlike `full_rebuild`, it opens its own sessions: one read transaction for
    `all_keys`, one write transaction per batch (`rebuild_in_batches`), and,
    when `domains is None`, one final write transaction for `:ProjectionState`.
    That final write runs only after the last batch committed, so a failure
    part-way leaves `projection status` reporting drift. The hashes it records
    are taken before the first batch, so a `same_as.yaml` edit made while the
    rebuild runs also stays visible as drift.

    Returns `rebuild_in_batches`' totals plus `domains` (every domain a
    rebuilt key belongs to) and `recorded`.
    """
    from artmind import same_as
    from artmind.graph_query import neo4j_session

    groups = same_as.load_groups()
    same_as_hash = same_as.content_hash()
    schema_hash = schema_set_hash()
    with neo4j_session() as session:
        keys = session.execute_read(lambda tx: all_keys(tx, domains))
    logger.info("Full projection rebuild over {} key(s), in batches of {}", len(keys), REBUILD_BATCH)
    summary = rebuild_in_batches(sorted(keys), groups)
    summary["domains"] = sorted({k[2] for k in keys if k[2]})
    if domains is None:
        with neo4j_session() as session:
            session.execute_write(
                lambda tx: record_rebuild(tx, same_as_hash=same_as_hash, schema_hash=schema_hash)
            )
    summary["recorded"] = domains is None
    return summary
```

In `full_rebuild`'s docstring (line 1372), append this paragraph after its last paragraph:

```
    One transaction for every key: only for callers that already hold a
    transaction and a bounded key set (`sameas approve`, snapshot import).
    Everything else uses `full_rebuild_batched`.
```

- [ ] **Step 4: Point `rebuild_projection` at it**

In `artmind/ingest.py`, replace `rebuild_projection` (lines 3594–3630) with:

```python
def rebuild_projection(domain: str | None = None, keys: list | None = None) -> dict:
    """Rebuild the projection outside an ingest: the deferred directory path,
    and the recovery path for drift.

    A full rebuild when `keys` is omitted, committed in batches
    (`projection.full_rebuild_batched`). One transaction for 4,332 keys ran a
    2 GB colima VM out of memory and killed Neo4j (2026-10-03). Two embed
    sweeps follow: the entity sweep, because a rebuild leaves everything it
    touched flagged stale; and the chunk sweep, because a batch ingest's
    per-document `commit_to_graph` calls run with `defer_rebuild=True` and skip
    their own chunk sweep. Both sweeps only run `if domain:`. A global rebuild
    (`domain=None`) skips both; `rebuild_and_sweep_all` is the global rebuild
    that sweeps.
    """
    from artmind import projection
    from artmind.graph_query import neo4j_session

    domains = [domain] if domain else None
    if keys:
        with neo4j_session() as session:
            summary = session.execute_write(
                lambda tx: projection.rebuild(
                    tx, keys, synthesis_loader=lambda ks: projection.load_synthesis_batch(tx, ks)
                )
            )
        swept_keys = list(keys)
    else:
        summary = projection.full_rebuild_batched(domains)
        with neo4j_session() as session:
            swept_keys = sorted(session.execute_read(lambda tx: projection.all_keys(tx, domains)))
    if domain:
        summary["embedded"] = _sweep_embeddings(domain, swept_keys)
        summary["chunks_embedded"] = _sweep_chunk_embeddings(domain=domain)
    return summary
```

- [ ] **Step 5: Follow the seam in `test/test_reingest.py`**

Replace lines 443–487 (the two `test_rebuild_projection_*` tests) with:

```python
def test_rebuild_projection_sweeps_chunks_by_domain_when_domain_given(monkeypatch):
    session = _RetractSession()
    _patch_session(monkeypatch, session)

    import artmind.projection as projection

    monkeypatch.setattr(projection, "full_rebuild_batched", lambda domains=None: {})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: set())
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys, **kw: 0)
    swept_chunks: list = []
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings", lambda **kw: swept_chunks.append(kw) or 7
    )

    summary = ing.rebuild_projection("general")

    assert swept_chunks == [{"domain": "general"}]
    assert summary["chunks_embedded"] == 7, (
        "a dropped or mis-assigned summary['chunks_embedded'] = ... line must fail this test"
    )


def test_rebuild_projection_skips_the_chunk_sweep_on_a_global_rebuild(monkeypatch):
    """No `domain` -- a multi-domain/global rebuild -- must skip the chunk
    sweep entirely, mirroring how the entity sweep is already skipped in
    that case."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)

    import artmind.projection as projection

    monkeypatch.setattr(projection, "full_rebuild_batched", lambda domains=None: {})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: set())
    swept_chunks: list = []
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings", lambda **kw: swept_chunks.append(kw) or 0
    )

    ing.rebuild_projection()

    assert swept_chunks == [], "a global rebuild (domain=None) must not sweep chunks either"
```

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_projection_batched_rebuild.py test/test_reingest.py test/test_ingest_sync_cli.py test/test_worker_manifest.py test/test_job_counts.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add artmind/projection.py artmind/ingest.py test/test_projection_batched_rebuild.py test/test_reingest.py
git commit -m "fix(projection): commit the full rebuild in batches; record state only after the last

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3 (B1c): Before/after check on a disposable Neo4j

**Files:**
- Create: `scripts/rebuild_memory_check.py`
- Modify: `justfile` (add a recipe after `dev-cli-help`, line 96–98)

This is an operator check, not part of the hermetic suite. It starts its **own** container (`artmind-rebuild-memcheck`, `127.0.0.1:17687`). It refuses if that name or port is taken. It also refuses if another Neo4j container is running in a Docker VM under 4 GiB, because starting a second JVM could OOM the VM and take the user's database down with it. It never connects anywhere else.

- [ ] **Step 1: Write the script**

Create `scripts/rebuild_memory_check.py`:

```python
#!/usr/bin/env python3
"""Before/after check for the batched full projection rebuild (plan 2026-10-03, B1).

Starts a DISPOSABLE Neo4j container with a small transaction-memory pool,
seeds ~4,300 synthetic aggregate keys through plain Cypher, gives the
projected entities 768-float embeddings (what made the real table commit
overflow), then shows:

  1. the OLD path -- `projection.full_rebuild` in ONE transaction -- fails
     with Neo4j's transaction-memory error;
  2. the NEW path -- `projection.full_rebuild_batched` -- passes, and
     `projection.status` reports 0 unprojected keys and no drift.

Safety: it only ever connects to 127.0.0.1:<ARTMIND_MEMCHECK_PORT> (default
17687), refuses if its container name or that port is already in use,
refuses to share a Docker VM under 4 GiB with another running Neo4j
container, and removes its container on exit (`docker run --rm` + stop).

    just dev-rebuild-memcheck

Knobs: ARTMIND_MEMCHECK_PORT, ARTMIND_MEMCHECK_IMAGE (default neo4j:latest;
needs Cypher 25, i.e. Neo4j 2025.06+), ARTMIND_MEMCHECK_TX_MAX (default 128m),
ARTMIND_MEMCHECK_KEYS (default 4300), ARTMIND_MEMCHECK_SHARE_VM=1 to skip
the VM-size refusal.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

CONTAINER = "artmind-rebuild-memcheck"
PORT = int(os.environ.get("ARTMIND_MEMCHECK_PORT", "17687"))
PASSWORD = "memcheck-disposable-only"
IMAGE = os.environ.get("ARTMIND_MEMCHECK_IMAGE", "neo4j:latest")
TX_TOTAL_MAX = os.environ.get("ARTMIND_MEMCHECK_TX_MAX", "128m")
KEYS = int(os.environ.get("ARTMIND_MEMCHECK_KEYS", "4300"))
OBS_PER_KEY = 3
DOMAIN = "memcheck"
MIN_SHARED_VM_BYTES = 4 * 1024**3

# Point artmind at the disposable container BEFORE importing it. `paths`
# loads every .env with override=False, so these real env vars win.
os.environ.update(
    ARTMIND_KG_NEO4J_URI=f"bolt://127.0.0.1:{PORT}",
    ARTMIND_KG_NEO4J_USERNAME="neo4j",
    ARTMIND_KG_NEO4J_PASSWORD=PASSWORD,
    ARTMIND_KG_NEO4J_DATABASE="neo4j",
)


def say(message: str) -> None:
    print(f"memcheck: {message}", flush=True)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _preflight() -> None:
    names = _docker("ps", "-a", "--format", "{{.Names}}").stdout.split()
    if CONTAINER in names:
        sys.exit(f"refusing: a container named {CONTAINER} already exists (docker rm -f {CONTAINER})")
    if _port_in_use(PORT):
        sys.exit(f"refusing: 127.0.0.1:{PORT} is already in use -- set ARTMIND_MEMCHECK_PORT")
    running = [
        line.split(" ", 1)[0]
        for line in _docker("ps", "--format", "{{.Names}} {{.Image}}").stdout.splitlines()
        if "neo4j" in line.split(" ", 1)[-1]
    ]
    mem_total = int(_docker("info", "--format", "{{.MemTotal}}").stdout.strip() or 0)
    if running and mem_total < MIN_SHARED_VM_BYTES and os.environ.get("ARTMIND_MEMCHECK_SHARE_VM") != "1":
        sys.exit(
            f"refusing: the Docker VM has {mem_total / 1024**3:.1f} GiB and Neo4j is already running in "
            f"{', '.join(running)}; a second JVM could OOM the VM and kill it. Stop those first "
            "(e.g. `neo4j-manager stop <name>`), or give colima more memory "
            "(`colima stop && colima start --memory 4`), or set ARTMIND_MEMCHECK_SHARE_VM=1."
        )


def _start() -> None:
    _docker(
        "run", "-d", "--rm", "--name", CONTAINER, "-p", f"127.0.0.1:{PORT}:7687",
        "-e", f"NEO4J_AUTH=neo4j/{PASSWORD}",
        "-e", f"NEO4J_db_memory_transaction_total_max={TX_TOTAL_MAX}",
        "-e", "NEO4J_server_memory_heap_max__size=512m",
        "-e", "NEO4J_server_memory_pagecache_size=128m",
        IMAGE,
    )
    say(f"started {CONTAINER} ({IMAGE}) on 127.0.0.1:{PORT}, db.memory.transaction.total.max={TX_TOTAL_MAX}")


def _wait_ready(timeout_s: int = 120) -> None:
    from artmind.graph_query import neo4j_session

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with neo4j_session() as s:
                s.run("RETURN 1").consume()
            return
        except Exception:
            time.sleep(2)
    sys.exit("the disposable Neo4j did not come up in time")


def _seed() -> None:
    from artmind.graph_query import neo4j_session
    from artmind.observations import aggregate_key, key_string
    from artmind.setup import _setup_neo4j

    filler = "lorem ipsum dolor sit amet " * 70  # ~1.9 KB, like a real extracted description
    rows, pairs = [], []
    for i in range(KEYS):
        name = f"Synthetic entity {i:05d}"
        key = key_string(aggregate_key(name, "CONCEPT", DOMAIN))
        for j in range(OBS_PER_KEY):
            rows.append({
                "id": f"memcheck-obs-{i}-{j}", "key": key, "canonical_name": name, "name": name,
                "entity_class": "CONCEPT", "_domain": DOMAIN, "_kind": "recurrent",
                "doc_id": f"memcheck-doc-{j}", "doc_version": 1, "chunk_id": f"memcheck-doc-{j}_{i:05d}",
                "_doc_valid_from": f"2026-0{j + 1}-01", "_valid_from": f"2026-0{j + 1}-01",
                "description": f"{name} (source {j}): {filler}",
            })
        if i + 1 < KEYS:
            pairs.append({"id": f"memcheck-rel-{i}", "src": f"memcheck-obs-{i}-0", "tgt": f"memcheck-obs-{i + 1}-0",
                          "chunk": f"memcheck-doc-0_{i:05d}"})
    with neo4j_session() as s:
        _setup_neo4j(s, 768)
        for start in range(0, len(rows), 1000):
            s.run("UNWIND $rows AS row CREATE (o:Observation) SET o = row", rows=rows[start:start + 1000]).consume()
        for start in range(0, len(pairs), 1000):
            s.run(
                """
                UNWIND $pairs AS p
                MATCH (s:Observation {id: p.src}), (t:Observation {id: p.tgt})
                CREATE (s)-[:ASSERTS_RELATION {id: p.id, rel_type: 'related_to',
                                               doc_id: 'memcheck-doc-0', chunk_id: p.chunk}]->(t)
                """,
                pairs=pairs[start:start + 1000],
            ).consume()
    say(f"seeded {len(rows)} observations and {len(pairs)} relations over {KEYS} keys (domain {DOMAIN})")


def _embed_entities() -> None:
    from artmind.graph_query import neo4j_session

    with neo4j_session() as s:
        s.run(
            """
            MATCH (e:Entity {_domain: $d})
            CALL (e) {
              SET e.embedding = [x IN range(1, 768) | rand()], e.embedding_stale = false
            } IN TRANSACTIONS OF 500 ROWS
            """,
            d=DOMAIN,
        ).consume()
        n = s.run("MATCH (e:Entity {_domain: $d}) WHERE e.embedding IS NOT NULL RETURN count(e) AS n", d=DOMAIN).single()["n"]
    say(f"gave {n} entities a 768-float embedding")


def _old_path_fails() -> bool:
    from artmind import projection
    from artmind.graph_query import neo4j_session

    try:
        with neo4j_session() as s:
            s.execute_write(
                lambda tx: projection.full_rebuild(
                    tx, None, synthesis_loader=lambda ks: projection.load_synthesis_batch(tx, ks)
                )
            )
    except Exception as e:  # the driver retries a TransientError for ~30 s first
        text = f"{getattr(e, 'code', '')} {e}"
        if "memory" in text.lower():
            say(f"OLD single-transaction full_rebuild failed as expected: {text[:200]}")
            return True
        raise
    return False


def main() -> int:
    _preflight()
    _start()
    try:
        _wait_ready()
        from artmind import projection
        from artmind.graph_query import neo4j_session

        _seed()
        first = projection.full_rebuild_batched()
        say(f"first batched build -- {first['batches']} batches, {first['rebuilt']} rebuilt")
        _embed_entities()
        if not _old_path_fails():
            say(f"OLD PATH DID NOT FAIL -- lower ARTMIND_MEMCHECK_TX_MAX (now {TX_TOTAL_MAX}) "
                f"or raise ARTMIND_MEMCHECK_KEYS (now {KEYS}) and re-run")
            return 2
        second = projection.full_rebuild_batched()
        say(f"NEW batched full rebuild passed -- {second['batches']} batches, "
            f"{second['rebuilt']} rebuilt, recorded={second['recorded']}")
        with neo4j_session() as s:
            st = s.execute_read(projection.status)
        say(f"projection status -- unprojected_keys={st['unprojected_keys']} "
            f"unembedded_entities={st['unembedded_entities']} drift={st['drift']}")
        if st["unprojected_keys"] or st["drift"]:
            say("FAIL: the batched rebuild left keys unprojected or drift set")
            return 1
        say("PASS")
        return 0
    finally:
        _docker("stop", CONTAINER, check=False)
        say(f"stopped and removed {CONTAINER}")


if __name__ == "__main__":
    sys.exit(main())
```

(`projection.status` gains `unprojected_keys`, `unembedded_entities` and `drift` in Task 7. Run Step 3 after Task 7.)

- [ ] **Step 2: Add the justfile recipe**

In `justfile`, after the `dev-cli-help` recipe (lines 95–97), add:

```
# B1 before/after on a DISPOSABLE Neo4j (own name + port; never touches another container):
# the old single-transaction full rebuild must hit the memory limit, the batched one must pass  (usage: just dev-rebuild-memcheck)
dev-rebuild-memcheck:
    uv run --group dev python scripts/rebuild_memory_check.py
```

- [ ] **Step 3: Run it (operator; after Task 7)**

Run: `just dev-rebuild-memcheck`
Expected output (counts can vary slightly; the shape and the PASS must not):

```
memcheck: started artmind-rebuild-memcheck (neo4j:latest) on 127.0.0.1:17687, db.memory.transaction.total.max=128m
memcheck: seeded 12900 observations and 4299 relations over 4300 keys (domain memcheck)
memcheck: first batched build -- 11 batches, 4300 rebuilt
memcheck: gave 4300 entities a 768-float embedding
memcheck: OLD single-transaction full_rebuild failed as expected: Neo.TransientError.General.MemoryPoolOutOfMemoryError ...
memcheck: NEW batched full rebuild passed -- 11 batches, 4300 rebuilt, recorded=True
memcheck: projection status -- unprojected_keys=0 unembedded_entities=0 drift=False
memcheck: PASS
memcheck: stopped and removed artmind-rebuild-memcheck
```

If it prints `OLD PATH DID NOT FAIL`, re-run with `ARTMIND_MEMCHECK_TX_MAX=64m just dev-rebuild-memcheck`. If a single batch then fails as well, the batch size is too large for that cap: report it, and do not change `REBUILD_BATCH` without measuring.

- [ ] **Step 4: Commit**

```bash
git add scripts/rebuild_memory_check.py justfile
git commit -m "test(projection): disposable-Neo4j before/after check for the batched rebuild

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4 (B2a): A sweep that cannot reach Neo4j is reported, not swallowed

**Files:**
- Modify: `artmind/ingest.py` (`_sweep_embeddings` lines 3525–3547, `_sweep_chunk_embeddings` lines 3550–3591, `rebuild_projection` from Task 2)
- Modify: `test/test_reingest.py` (the test replaced in Task 2)
- Modify: `test/test_plugin_fixtures.py` (lines 220–221)
- Test: `test/test_sweep_failures.py` (create)

Decision: the sweeps keep their never-raising default, because `commit_to_graph`, `vault_sync`, `table2graph`, `update`, `lifecycle` and `graph_snapshot` rely on it. They gain `strict=True`, which raises `SweepSkipped` instead. Only `rebuild_projection` and `rebuild_and_sweep_all` (Task 8) pass `strict=True`. Both catch the exception and report it under `sweep_errors`, which the worker (Task 6) treats as a failed finalize.

- [ ] **Step 1: Write the failing tests**

Create `test/test_sweep_failures.py`:

```python
"""A sweep skipped for a dead connection must surface (plan 2026-10-03, B2).

Job 0c616a6f: the rebuild committed, the Neo4j connection was dead for both
embed sweeps, and the job reported `completed` with `embedded: 0` for 2,801
entities. The sweeps' never-raising default stays (every per-commit caller
relies on it); `strict=True` is what the rebuild paths use.
"""
from __future__ import annotations

import pytest

import artmind.embed_sweep as embed_sweep
import artmind.ingest as ing
import artmind.projection as projection

REFUSED = "Couldn't connect to 127.0.0.1:7687 (reason [Errno 61] Connection refused)"


def _refuse(*args, **kwargs):
    raise OSError(REFUSED)


def test_a_strict_entity_sweep_raises_when_neo4j_is_unreachable(monkeypatch):
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", _refuse)

    with pytest.raises(ing.SweepSkipped, match=r"entity embed sweep skipped for general .*Connection refused"):
        ing._sweep_embeddings("general", [("a", "C", "general")], strict=True)


def test_the_default_entity_sweep_still_never_raises(monkeypatch):
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", _refuse)

    assert ing._sweep_embeddings("general", [("a", "C", "general")]) == 0


def test_a_strict_chunk_sweep_raises_and_the_default_does_not(monkeypatch):
    monkeypatch.setattr(embed_sweep, "embed_missing_chunk_embeddings", _refuse)

    with pytest.raises(ing.SweepSkipped, match=r"chunk embed sweep skipped for domain 'general'"):
        ing._sweep_chunk_embeddings(domain="general", strict=True)
    assert ing._sweep_chunk_embeddings(domain="general") == 0


def test_rebuild_projection_reports_both_skipped_sweeps(monkeypatch):
    monkeypatch.setattr(projection, "full_rebuild_batched", lambda domains=None: {"rebuilt": 1, "keys": 1})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: {("a", "C", "general")})
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", _refuse)
    monkeypatch.setattr(embed_sweep, "embed_missing_chunk_embeddings", _refuse)

    summary = ing.rebuild_projection("general")

    assert (summary["embedded"], summary["chunks_embedded"]) == (0, 0)
    assert len(summary["sweep_errors"]) == 2
    assert summary["sweep_errors"][0].startswith("entity embed sweep skipped for general")
    assert summary["sweep_errors"][1].startswith("chunk embed sweep skipped for domain 'general'")
    assert all("Connection refused" in e for e in summary["sweep_errors"])


def test_rebuild_projection_reports_no_sweep_errors_when_both_sweeps_ran(monkeypatch):
    seen: list = []
    monkeypatch.setattr(projection, "full_rebuild_batched", lambda domains=None: {"rebuilt": 1, "keys": 1})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: {("a", "C", "general")})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys, strict=False: seen.append(("e", domain, strict)) or 5)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda chunk_ids=None, domain=None, strict=False: seen.append(("c", domain, strict)) or 2)

    summary = ing.rebuild_projection("general")

    assert seen == [("e", "general", True), ("c", "general", True)]
    assert (summary["embedded"], summary["chunks_embedded"], summary["sweep_errors"]) == (5, 2, [])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_sweep_failures.py -v`
Expected: FAIL. The strict tests fail with `AttributeError: module 'artmind.ingest' has no attribute 'SweepSkipped'`, and `test_rebuild_projection_reports_*` fails with `KeyError: 'sweep_errors'`.

- [ ] **Step 3: Implement**

In `artmind/ingest.py`, replace `_sweep_embeddings` (lines 3525–3547) with:

```python
class SweepSkipped(RuntimeError):
    """A strict embed sweep could not run at all (Neo4j or the embedding
    service unreachable). Its message is what `sweep_errors` reports."""


def _sweep_embeddings(domain: str, keys: list, *, strict: bool = False) -> int:
    """Post-commit embed sweep, scoped to the affected keys.

    Never fatal by default: the commit already succeeded and the graph is
    correct. An entity the sweep could not embed keeps `embedding_stale =
    true` and is picked up next time, which is the whole point of flagging
    rather than nulling. `strict=True` (the rebuild paths) raises
    `SweepSkipped` when the sweep could not run at all, so a dead connection
    is reported instead of reading as "nothing to embed".
    """
    if not keys:
        return 0
    try:
        from artmind.graph_query import neo4j_session

        env = load_env()
        embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
        with neo4j_session() as session:
            return embed_missing_entity_embeddings(session, domain, embed_model, keys=keys)
    except Exception as e:
        logger.warning(
            "Embed sweep skipped for {} ({}); entities stay marked stale and will be "
            "picked up by the next sweep", domain, e,
        )
        if strict:
            raise SweepSkipped(f"entity embed sweep skipped for {domain} ({e})") from e
        return 0
```

Change `_sweep_chunk_embeddings`'s signature (line 3550) to:

```python
def _sweep_chunk_embeddings(
    chunk_ids: list | None = None, domain: str | None = None, *, strict: bool = False
) -> int:
```

Add this sentence at the end of its docstring: "`strict=True` raises `SweepSkipped` instead of returning 0 when the sweep could not run (see `_sweep_embeddings`)." Then replace its `except` block (the last 7 lines of the function) with:

```python
    except Exception as e:
        scope_desc = f"{len(chunk_ids)} chunk(s)" if chunk_ids else f"domain {domain!r}"
        logger.warning(
            "Chunk embed sweep skipped for {} ({}); they stay unembedded "
            "and will be picked up by the next sweep", scope_desc, e,
        )
        if strict:
            raise SweepSkipped(f"chunk embed sweep skipped for {scope_desc} ({e})") from e
        return 0
```

Add `_sweep_domain` directly above `rebuild_projection`:

```python
def _sweep_domain(domain: str, keys: list) -> dict:
    """Both strict sweeps for one domain. Never raises: a skipped sweep
    becomes an entry in `sweep_errors`, and the other sweep still runs."""
    out: dict = {"embedded": 0, "chunks_embedded": 0, "sweep_errors": []}
    try:
        out["embedded"] = _sweep_embeddings(domain, keys, strict=True)
    except SweepSkipped as e:
        out["sweep_errors"].append(str(e))
    try:
        out["chunks_embedded"] = _sweep_chunk_embeddings(domain=domain, strict=True)
    except SweepSkipped as e:
        out["sweep_errors"].append(str(e))
    return out
```

In `rebuild_projection` (Task 2's version), replace the last four lines:

```python
    if domain:
        summary["embedded"] = _sweep_embeddings(domain, swept_keys)
        summary["chunks_embedded"] = _sweep_chunk_embeddings(domain=domain)
    return summary
```

with:

```python
    if domain:
        summary.update(_sweep_domain(domain, swept_keys))
    return summary
```

Append this to its docstring: "A sweep that could not run at all is listed under `sweep_errors` (empty when both ran); the worker treats a non-empty list as a failed finalize."

- [ ] **Step 4: Follow the new keyword in two existing stubs**

In `test/test_reingest.py`, in `test_rebuild_projection_sweeps_chunks_by_domain_when_domain_given`, change:

```python
    assert swept_chunks == [{"domain": "general"}]
```

to:

```python
    assert swept_chunks == [{"domain": "general", "strict": True}]
```

In `test/test_plugin_fixtures.py`, replace lines 220–221 with:

```python
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys, strict=False: 0)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda chunk_ids=None, domain=None, strict=False: 0)
```

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_sweep_failures.py test/test_reingest.py test/test_projection_batched_rebuild.py test/test_plugin_fixtures.py test/test_vault_sync.py test/test_table2graph.py test/test_graph_snapshot.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/ingest.py test/test_sweep_failures.py test/test_reingest.py test/test_plugin_fixtures.py
git commit -m "fix(ingest): a sweep skipped for a dead connection is reported as sweep_errors

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5 (B2b): The job row records what it still owes

**Files:**
- Modify: `artmind/db.py` (CREATE TABLE `ingestion_jobs` lines 52–66; migration lines 228–232)
- Modify: `artmind/jobs.py` (imports lines 1–5; `_fetch_active_jobs` 158–196; `_get_job_status` 303–345; `_get_job_results` 347–384; new helpers after `_update_job_file_status`)
- Test: `test/test_job_finalize.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_job_finalize.py`:

```python
"""The `finalize` block on ingest jobs (plan 2026-10-03, B2).

Against the real (per-test temp) registry DB, so the assertions are on what
SQLite holds and what every read path returns.
"""
from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

import artmind.db as db
from artmind.cli import cli
from artmind.jobs import (
    _add_finalize_domain,
    _create_job,
    _fetch_active_jobs,
    _get_finalize,
    _get_job_results,
    _get_job_status,
    _set_finalize_state,
)

FILES = ["/vault/a.md", "/vault/b.md"]
NONE = {"state": "none", "domains": [], "error": None}


def test_an_old_jobs_table_gains_the_finalize_columns():
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE ingestion_jobs (
            job_id TEXT PRIMARY KEY, status TEXT NOT NULL, file_count INTEGER NOT NULL,
            processed_count INTEGER DEFAULT 0, queued_at TEXT NOT NULL, started_at TEXT,
            completed_at TEXT, error_message TEXT, results_json TEXT, domain TEXT DEFAULT 'general'
        )
    """)
    conn.execute("INSERT INTO ingestion_jobs (job_id, status, file_count, queued_at) VALUES ('old', 'completed', 1, 't')")
    conn.commit()
    conn.close()

    db._get_db().close()

    conn = sqlite3.connect(db.DB_PATH)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(ingestion_jobs)")}
    conn.close()
    assert {"finalize_domains", "finalize_state", "finalize_error"} <= cols
    assert _get_job_status("old")["finalize"] == NONE, "a job from before the columns owes nothing"


def test_a_new_job_owes_nothing_on_every_read_path():
    job_id = _create_job(FILES, domain="general")
    assert _get_job_status(job_id)["finalize"] == NONE
    assert _get_job_results(job_id)["finalize"] == NONE
    assert _fetch_active_jobs()[0]["finalize"] == NONE


def test_owed_domains_accumulate_once_each_and_mark_the_job_pending():
    job_id = _create_job(FILES, domain="general")
    for domain in ("habits", "general", "habits"):
        _add_finalize_domain(job_id, domain)

    expected = {"state": "pending", "domains": ["general", "habits"], "error": None}
    assert _get_finalize(job_id) == expected
    assert _get_job_status(job_id)["finalize"] == expected
    assert _fetch_active_jobs()[0]["finalize"] == expected


def test_a_failed_finalize_carries_its_error_and_done_clears_it():
    job_id = _create_job(FILES, domain="general")
    _add_finalize_domain(job_id, "general")

    _set_finalize_state(job_id, "failed", error="general: Couldn't connect")
    assert _get_job_results(job_id)["finalize"] == {
        "state": "failed", "domains": ["general"], "error": "general: Couldn't connect",
    }

    _set_finalize_state(job_id, "done")
    assert _get_job_status(job_id)["finalize"] == {"state": "done", "domains": ["general"], "error": None}


def test_adding_a_domain_to_an_unknown_job_is_a_no_op():
    _add_finalize_domain("no-such-job", "general")
    assert _get_finalize("no-such-job") == NONE


def test_the_cli_emits_finalize():
    job_id = _create_job(FILES, domain="general")
    _add_finalize_domain(job_id, "general")
    for command in ("job-status", "job-results"):
        result = CliRunner().invoke(cli, ["ingest", command, job_id, "--compact"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["finalize"]["domains"] == ["general"]
    result = CliRunner().invoke(cli, ["ingest", "jobs-active", "--compact"])
    assert json.loads(result.stdout)[0]["finalize"]["state"] == "pending"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_job_finalize.py -v`
Expected: FAIL at collection with `ImportError: cannot import name '_add_finalize_domain' from 'artmind.jobs'`.

- [ ] **Step 3: Add the columns**

In `artmind/db.py`, in the `ingestion_jobs` CREATE TABLE (lines 52–66), replace

```python
            force            INTEGER DEFAULT 0,
            stage_only       INTEGER DEFAULT 0
        )
```

with

```python
            force            INTEGER DEFAULT 0,
            stage_only       INTEGER DEFAULT 0,
            finalize_domains TEXT,
            finalize_state   TEXT,
            finalize_error   TEXT
        )
```

After the `stage_only` migration (line 232), add:

```python
    # What a job still owes after its file loop (plan 2026-10-03 B2): the
    # domains whose deferred projection rebuild + embed sweeps have not run
    # yet (a JSON list), and whether they did. Persisted so a resumed or
    # retried job finishes them even with 0 queued files. NULL state = owes
    # nothing ("none").
    for col in ("finalize_domains", "finalize_state", "finalize_error"):
        if col not in existing:
            cursor.execute(f"ALTER TABLE ingestion_jobs ADD COLUMN {col} TEXT")
```

- [ ] **Step 4: Add the helpers and the read-path block**

In `artmind/jobs.py`, change the imports (lines 1–5) to:

```python
import json
import uuid
from datetime import datetime
from pathlib import Path

from artmind.db import _get_db
```

After `_update_job_file_status` (ends at line 155), add:

```python
FINALIZE_STATES = ("pending", "done", "failed")


def _finalize(state: str | None, domains_json: str | None, error: str | None) -> dict:
    """The `finalize` block every job read path returns: whether the job's
    deferred projection rebuild and embed sweeps have run.

    `none` -- the job deferred nothing; `pending` -- owed and not finished
    (running now, or its worker died -- see `stalled`); `done`; `failed` --
    `error` says why. `domains` -- what it owes (or owed)."""
    return {
        "state": state or "none",
        "domains": json.loads(domains_json) if domains_json else [],
        "error": error,
    }


def _add_finalize_domain(job_id: str, domain: str) -> None:
    """Record that `job_id` owes `domain` a rebuild + sweeps, as each file's
    KG commit defers. Idempotent per domain; marks the job `pending`. A
    missing job is a no-op."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT finalize_domains FROM ingestion_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return
        domains = json.loads(row[0]) if row[0] else []
        if domain not in domains:
            domains.append(domain)
        conn.execute(
            "UPDATE ingestion_jobs SET finalize_domains = ?, finalize_state = 'pending',"
            " finalize_error = NULL WHERE job_id = ?",
            (json.dumps(sorted(domains)), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_finalize(job_id: str) -> dict:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT finalize_state, finalize_domains, finalize_error FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return _finalize(*row) if row else _finalize(None, None, None)


def _set_finalize_state(job_id: str, state: str, error: str | None = None) -> None:
    """Set the finalize state; `error` is written as given, so `done` clears it."""
    if state not in FINALIZE_STATES:
        raise ValueError(f"finalize state must be one of {FINALIZE_STATES}, not {state!r}")
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE ingestion_jobs SET finalize_state = ?, finalize_error = ? WHERE job_id = ?",
            (state, error, job_id),
        )
        conn.commit()
    finally:
        conn.close()
```

Replace `_fetch_active_jobs` (lines 158–196) with:

```python
def _fetch_active_jobs() -> list[dict]:
    """Return queued/processing jobs with their file rows."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT job_id, status, file_count, processed_count,"
            " queued_at, started_at, domain, finalize_state, finalize_domains, finalize_error"
            " FROM ingestion_jobs WHERE status IN ('queued','processing')"
            " ORDER BY queued_at DESC LIMIT 20"
        ).fetchall()
        result = []
        for row in rows:
            job_id = row[0]
            files = conn.execute(
                "SELECT filename, status, current_step, doc_sha256, entity_count, relationship_count"
                " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
            result.append({
                "job_id": job_id,
                "status": row[1],
                "file_count": row[2],
                "processed_count": row[3],
                "queued_at": row[4],
                "started_at": row[5],
                "domain": row[6] or "general",
                "stalled": _stalled(row[1]),
                "finalize": _finalize(row[7], row[8], row[9]),
                "files": [
                    {
                        "filename": f[0], "status": f[1], "current_step": f[2], "doc_sha256": f[3],
                        "entities": f[4], "relationships": f[5],
                    }
                    for f in files
                ],
            })
        return result
    finally:
        conn.close()
```

Replace `_get_job_status` (lines 303–345) with:

```python
def _get_job_status(job_id: str) -> dict | None:
    """Retrieve job status with per-file progress; return None if not found."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT job_id, status, file_count, processed_count, queued_at, started_at,"
            " completed_at, error_message, domain, finalize_state, finalize_domains, finalize_error"
            " FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            "SELECT filename, status, current_step, doc_sha256, entity_count, relationship_count"
            " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        files = []
        for r in cursor.fetchall():
            entry = {
                "filename": r[0], "status": r[1], "current_step": r[2],
                "entities": r[4], "relationships": r[5],
            }
            if r[3] and r[2] == "extract_kg":
                entry["chunk_progress"] = _get_chunk_progress(r[3])
            files.append(entry)
        return {
            "job_id": row[0],
            "status": row[1],
            "file_count": row[2],
            "processed_count": row[3],
            "queued_at": row[4],
            "started_at": row[5],
            "completed_at": row[6],
            "error_message": row[7],
            "domain": row[8] or "general",
            "stalled": _stalled(row[1]),
            "finalize": _finalize(row[9], row[10], row[11]),
            "files": files,
        }
    finally:
        conn.close()
```

In `_get_job_results` (lines 347–384), replace

```python
        cursor.execute(
            "SELECT status, file_count, error_message FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        status, file_count, error_message = row
```

with

```python
        cursor.execute(
            "SELECT status, file_count, error_message, finalize_state, finalize_domains, finalize_error"
            " FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        status, file_count, error_message = row[:3]
        finalize = _finalize(*row[3:])
```

and replace

```python
        result = {"job_id": job_id, "status": status, "file_count": file_count, "files": files}
```

with

```python
        result = {
            "job_id": job_id, "status": status, "file_count": file_count,
            "finalize": finalize, "files": files,
        }
```

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_job_finalize.py test/test_job_counts.py test/test_job_stalled.py test/test_jobs_stage_only.py test/test_retry_job_deregister.py test/test_webui_admin_api.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/db.py artmind/jobs.py test/test_job_finalize.py
git commit -m "feat(jobs): persist the rebuild a job owes; job reads report finalize

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6 (B2c): The worker finishes what the job owes, and survives a failure

**Files:**
- Modify: `artmind/worker.py` (import at line 17; `_process_job` lines 122–268)
- Test: `test/test_worker_finalize.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_worker_finalize.py`:

```python
"""The worker's deferred rebuild is owed by the job row, not by the run
(plan 2026-10-03, B2).

Job 5f5c2d11: the deferred rebuild killed Neo4j, the uncaught error killed the
worker, and the retry re-ran the job with 0 files -- which rebuilt nothing,
because only files processed in the current run counted -- and marked it
`completed`. Assertions are on the job rows SQLite holds and on the rebuild
calls actually made, in order.
"""
from __future__ import annotations

import pytest

import artmind.ingest as ing
import artmind.worker as worker_module
import paths
from artmind.jobs import (
    _add_finalize_domain,
    _create_job,
    _get_finalize,
    _get_job_status,
    _retry_job,
    _update_job_file_status,
    _update_job_status,
)

FILES = ["/vault/notes/a.md", "/vault/habits/b.md"]


def _domain_of(path) -> str:
    return "habits" if "habits" in str(path) else "general"


@pytest.fixture
def events(tmp_path, monkeypatch):
    """Record each file commit and each rebuild, in order; no worker is alive."""
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", None)
    monkeypatch.setattr(worker_module, "ARTMIND_VAULT_DIR", None)
    log: list[tuple] = []
    monkeypatch.setattr(
        worker_module, "ingest_file",
        lambda path, *a, **k: {"status": "ok", "sha256": f"sha-{path}", "domain": _domain_of(path)},
    )
    monkeypatch.setattr(
        worker_module, "ingest_to_kg",
        lambda result, domain, *a, **k: log.append(("commit", domain, k.get("defer_rebuild"))) or True,
    )
    monkeypatch.setattr(worker_module, "kg_work_was_done", lambda result: True)
    return log


def test_a_multi_file_job_persists_what_it_owes_then_rebuilds_each_domain_once(events, monkeypatch):
    job_id = _create_job(FILES, domain="general")

    def rebuild(domain):
        assert _get_finalize(job_id)["state"] == "pending", "owed domains are on the row before the rebuild runs"
        events.append(("rebuild", domain))
        return {"sweep_errors": []}

    monkeypatch.setattr(ing, "rebuild_projection", rebuild)

    worker_module._process_job(job_id, "general", env={})

    assert events == [
        ("commit", "general", True), ("commit", "habits", True),
        ("rebuild", "general"), ("rebuild", "habits"),
    ]
    status = _get_job_status(job_id)
    assert status["status"] == "completed"
    assert status["finalize"] == {"state": "done", "domains": ["general", "habits"], "error": None}


def test_a_retried_stalled_job_with_no_queued_files_still_rebuilds_what_it_owes(events, monkeypatch):
    job_id = _create_job(FILES, domain="general")
    _update_job_status(job_id, status="processing", processed_count=2, started_at="2026-10-03T10:00:00")
    for f in FILES:
        _update_job_file_status(job_id, f, status="completed")
    _add_finalize_domain(job_id, "general")  # the worker died inside the deferred rebuild
    assert _retry_job(job_id)["stalled"] is True

    monkeypatch.setattr(worker_module, "ingest_file", lambda *a, **k: pytest.fail("no file is queued"))
    rebuilds: list = []
    monkeypatch.setattr(ing, "rebuild_projection", lambda d: rebuilds.append(d) or {"sweep_errors": []})

    worker_module._process_job(job_id, "general", env={})

    assert rebuilds == ["general"]
    status = _get_job_status(job_id)
    assert status["status"] == "completed"
    assert status["finalize"] == {"state": "done", "domains": ["general"], "error": None}


def test_a_failing_rebuild_marks_finalize_failed_and_the_worker_survives(events, monkeypatch):
    job_id = _create_job(FILES, domain="general")
    attempted: list = []

    def rebuild(domain):
        attempted.append(domain)
        if domain == "general":
            raise RuntimeError("projection rebuild failed on batch 3/11 (Couldn't connect)")
        return {"sweep_errors": []}

    monkeypatch.setattr(ing, "rebuild_projection", rebuild)

    worker_module._process_job(job_id, "general", env={})  # must not raise

    assert attempted == ["general", "habits"], "one domain failing must not skip the next"
    status = _get_job_status(job_id)
    assert status["status"] == "completed", "every file committed; the finalize failure is reported separately"
    assert status["finalize"] == {
        "state": "failed", "domains": ["general", "habits"],
        "error": "general: projection rebuild failed on batch 3/11 (Couldn't connect)",
    }


def test_a_skipped_sweep_marks_finalize_failed(events, monkeypatch):
    job_id = _create_job(FILES, domain="general")
    monkeypatch.setattr(
        ing, "rebuild_projection",
        lambda d: {"sweep_errors": [f"entity embed sweep skipped for {d} (Connection refused)"]},
    )

    worker_module._process_job(job_id, "general", env={})

    finalize = _get_job_status(job_id)["finalize"]
    assert finalize["state"] == "failed"
    assert finalize["error"] == (
        "general: entity embed sweep skipped for general (Connection refused); "
        "habits: entity embed sweep skipped for habits (Connection refused)"
    )


def test_a_single_file_job_defers_nothing_and_owes_nothing(events, monkeypatch):
    job_id = _create_job(FILES[:1], domain="general")
    monkeypatch.setattr(ing, "rebuild_projection", lambda d: pytest.fail("a single file rebuilds inline"))

    worker_module._process_job(job_id, "general", env={})

    assert events == [("commit", "general", False)]
    assert _get_job_status(job_id)["finalize"] == {"state": "none", "domains": [], "error": None}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_worker_finalize.py -v`
Expected: FAIL. The multi-file test fails on `assert _get_finalize(job_id)["state"] == "pending"` (the worker keeps owed domains only in memory). The retry test fails with `assert [] == ['general']`. The failing-rebuild test fails with the uncaught `RuntimeError`. The single-file test passes already.

- [ ] **Step 3: Implement**

In `artmind/worker.py`, replace line 17:

```python
from artmind.jobs import _update_job_file_status, _update_job_status
```

with

```python
from artmind.jobs import (
    _add_finalize_domain,
    _get_finalize,
    _set_finalize_state,
    _update_job_file_status,
    _update_job_status,
)
```

Add above `_process_job`:

```python
def _finalize_job(job_id: str) -> None:
    """Run the deferred projection rebuild + embed sweeps the job row owes.

    Reads the owed domains from the row, never from this run, so a resumed
    or retried job finishes them even with 0 queued files. Catches
    everything: a failure marks `finalize` failed with the reason and moves
    on to the next domain. Before this, an uncaught Neo4j error killed the
    worker (2026-10-03). A sweep that could not run (`sweep_errors`) counts
    as a failure too.
    """
    finalize = _get_finalize(job_id)
    if not finalize["domains"] or finalize["state"] not in ("pending", "failed"):
        return
    from artmind.ingest import rebuild_projection

    _set_finalize_state(job_id, "pending")
    logger.info("═══ Deferred projection rebuild over {} domain(s)", len(finalize["domains"]))
    errors: list[str] = []
    for domain in finalize["domains"]:
        try:
            summary = rebuild_projection(domain)
        except Exception as e:
            logger.error("Deferred projection rebuild failed for {}: {}", domain, e)
            errors.append(f"{domain}: {e}")
            continue
        logger.info("Projection rebuilt for {}: {}", domain, summary)
        errors.extend(f"{domain}: {err}" for err in summary.get("sweep_errors") or [])
    if errors:
        _set_finalize_state(job_id, "failed", error="; ".join(errors))
    else:
        _set_finalize_state(job_id, "done")
```

In `_process_job`, make three edits:

1. Replace

```python
    defer_rebuild = len(queued_files) > 1 and not stage_only
    deferred_domains: set[str] = set()
```

with

```python
    defer_rebuild = len(queued_files) > 1 and not stage_only
```

and append to the comment block above it: "What the job owes is persisted on its row (`_add_finalize_domain`), not kept in this run, so a retried job with 0 queued files still finishes it (`_finalize_job`)."

2. Replace

```python
                    if kg_ok and defer_rebuild and kg_work_was_done(result):
                        deferred_domains.add(effective_domain)
```

with

```python
                    if kg_ok and defer_rebuild and kg_work_was_done(result):
                        _add_finalize_domain(job_id, effective_domain)
```

3. Replace the post-loop block

```python
    if deferred_domains:
        from artmind.ingest import rebuild_projection

        logger.info("═══ Deferred projection rebuild over {} domain(s)", len(deferred_domains))
        for deferred_domain in sorted(deferred_domains):
            summary = rebuild_projection(deferred_domain)
            logger.info("Projection rebuilt for {}: {}", deferred_domain, summary)
```

with

```python
    _finalize_job(job_id)
```

The job stays `processing` while `_finalize_job` runs, which is what lets the plugin show "finishing: building the graph". The final status line below is unchanged.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_worker_finalize.py test/test_worker_manifest.py test/test_job_counts.py test/test_job_stalled.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/worker.py test/test_worker_finalize.py
git commit -m "fix(worker): finish the rebuild a job owes, even on a 0-file retry; never die on it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7 (B3): `projection status` counts what is missing

**Files:**
- Modify: `artmind/projection.py` (`status`, lines 1503–1534)
- Modify: `test/test_projection_status.py` (the fake, lines 22–39; new tests appended)

- [ ] **Step 1: Update the fake and write the failing tests**

In `test/test_projection_status.py`, replace the `_FakeProjectionTx` class (lines 22–39) with:

```python
class _FakeProjectionTx:
    """Minimal `tx`-like fake answering exactly the query shapes `status`
    issues: the `:ProjectionState` lookup and the three per-domain gap counts
    (un-embedded entities, un-projected observation keys, un-embedded chunks).
    Anything else raises, so an unexpected query is a loud failure rather than
    a silently-truthy result. Every query is recorded in `queries`."""

    def __init__(self, state: dict | None, unembedded_count: int = 0, *,
                 entity_rows=(), unprojected_rows=(), chunk_rows=None):
        self._state = state
        self.entity_rows = list(entity_rows)
        self.unprojected_rows = list(unprojected_rows)
        if chunk_rows is None:
            chunk_rows = [{"domain": "general", "n": unembedded_count}] if unembedded_count else []
        self.chunk_rows = list(chunk_rows)
        self.queries: list[str] = []

    def run(self, cypher, **params):
        self.queries.append(cypher)
        if "ProjectionState" in cypher:
            rows = [{"p": self._state}] if self._state is not None else []
            return _Result(rows)
        if "MATCH (e:Entity)" in cypher and "embedding" in cypher:
            return _Result(self.entity_rows)
        if "MATCH (o:Observation)" in cypher and "AGGREGATES" in cypher:
            return _Result(self.unprojected_rows)
        if "MATCH (c:DocChunk)" in cypher and "count(c)" in cypher:
            return _Result(self.chunk_rows)
        raise AssertionError(f"projection.status issued an unexpected query: {cypher!r}")
```

Append to the file:

```python
# ── B3: what is missing, per domain ─────────────────────────────────────────


def test_status_counts_unembedded_entities_and_unprojected_keys_per_domain():
    from artmind.projection import status

    tx = _FakeProjectionTx(
        state=None,
        entity_rows=[{"domain": "general", "n": 2801}, {"domain": "habits", "n": 1}],
        unprojected_rows=[{"domain": "general", "n": 40}],
        chunk_rows=[{"domain": "habits", "n": 4}, {"domain": None, "n": 2}],
    )

    result = status(tx)

    assert (result["unembedded_entities"], result["unprojected_keys"], result["unembedded_chunks"]) == (2802, 40, 6)
    assert result["domains"] == {
        "(none)": {"unembedded_entities": 0, "unprojected_keys": 0, "unembedded_chunks": 2},
        "general": {"unembedded_entities": 2801, "unprojected_keys": 40, "unembedded_chunks": 0},
        "habits": {"unembedded_entities": 1, "unprojected_keys": 0, "unembedded_chunks": 4},
    }


def test_status_counts_with_the_embed_sweeps_own_predicates():
    """`unembedded_entities` must count exactly what the entity sweep would
    pick up, and an observation is unprojected when no Entity AGGREGATES it --
    a same-as member's observations hang off the canonical's Entity, so
    matching on the key alone would count every merged alias."""
    from artmind.projection import status

    tx = _FakeProjectionTx(state=None)
    status(tx)

    (entity_q,) = [q for q in tx.queries if "MATCH (e:Entity)" in q]
    assert "e.name IS NOT NULL" in entity_q
    assert "e.embedding IS NULL OR e.embedding_stale" in entity_q
    (obs_q,) = [q for q in tx.queries if "MATCH (o:Observation)" in q]
    assert "NOT EXISTS { MATCH (:Entity)-[:AGGREGATES]->(o) }" in obs_q
    assert "count(DISTINCT o.key)" in obs_q
    assert len(tx.queries) == 4, "one state read and three grouped counts, never a query per domain"


def test_drift_is_one_flag(monkeypatch):
    import artmind.projection as projection
    import artmind.same_as as same_as

    monkeypatch.setattr(same_as, "content_hash", lambda path=None: "a")
    monkeypatch.setattr(projection, "schema_set_hash", lambda domains_dir=None: "b")

    assert projection.status(_FakeProjectionTx(state=None))["drift"] is True
    assert projection.status(_FakeProjectionTx(state={"same_as_hash": "a", "schema_hash": "b"}))["drift"] is False
    assert projection.status(_FakeProjectionTx(state={"same_as_hash": "x", "schema_hash": "b"}))["drift"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_projection_status.py -v`
Expected: FAIL. The new tests fail with `KeyError: 'unembedded_entities'` / `KeyError: 'drift'`. The three existing tests fail with `AssertionError: projection.status issued an unexpected query` on the old `count_unembedded_chunks` query, which has no `domain` column and is answered only by the grouped shape now.

- [ ] **Step 3: Implement**

In `artmind/projection.py`, replace `status` (lines 1503–1534) with:

```python
_UNEMBEDDED_ENTITIES_BY_DOMAIN = """
MATCH (e:Entity)
WHERE e.name IS NOT NULL AND (e.embedding IS NULL OR e.embedding_stale)
RETURN e._domain AS domain, count(e) AS n
"""

_UNPROJECTED_KEYS_BY_DOMAIN = """
MATCH (o:Observation)
WHERE o.key IS NOT NULL AND NOT EXISTS { MATCH (:Entity)-[:AGGREGATES]->(o) }
RETURN o._domain AS domain, count(DISTINCT o.key) AS n
"""

_UNEMBEDDED_CHUNKS_BY_DOMAIN = """
MATCH (c:DocChunk) WHERE c.embedding IS NULL
RETURN c._domain AS domain, count(c) AS n
"""

_GAP_FIELDS = ("unembedded_entities", "unprojected_keys", "unembedded_chunks")


def _gaps_by_domain(tx) -> dict[str, dict[str, int]]:
    """`{domain: {unembedded_entities, unprojected_keys, unembedded_chunks}}`
    in three grouped reads -- never one query per domain. An entity counts
    exactly when the entity embed sweep would pick it up; an observation key
    counts when no Entity AGGREGATES its observation yet (so a same-as
    member, which hangs off its canonical's Entity, does not). A node with no
    domain is grouped under "(none)"."""
    domains: dict[str, dict[str, int]] = {}
    for field_name, cypher in zip(
        _GAP_FIELDS,
        (_UNEMBEDDED_ENTITIES_BY_DOMAIN, _UNPROJECTED_KEYS_BY_DOMAIN, _UNEMBEDDED_CHUNKS_BY_DOMAIN),
    ):
        for row in tx.run(cypher).data():
            entry = domains.setdefault(row["domain"] or "(none)", dict.fromkeys(_GAP_FIELDS, 0))
            entry[field_name] = row["n"]
    return {d: domains[d] for d in sorted(domains)}


def status(tx) -> dict:
    """Compare the recorded `:ProjectionState` against `same_as.yaml` and the
    schema set right now, and count what the projection is still missing.
    Read-only, deliberately: queries run through `read_session()`
    (`READ_ACCESS`), so drift is reported, never auto-fixed.

    `drift` is true when there is no recorded state or either hash differs.
    `unembedded_entities`, `unprojected_keys` and `unembedded_chunks` are
    totals of `domains`' per-domain counts; any of them non-zero, or `drift`,
    means `projection rebuild --sweep` has work to do."""
    from artmind import same_as

    current_same_as = same_as.content_hash()
    current_schema = schema_set_hash()
    recorded = read_state(tx)
    gaps = _gaps_by_domain(tx)
    totals = {f: sum(g[f] for g in gaps.values()) for f in _GAP_FIELDS}
    if not recorded:
        return {
            "known": False,
            "drift": True,
            "same_as_drift": True,
            "schema_drift": True,
            "current_same_as_hash": current_same_as,
            "current_schema_hash": current_schema,
            **totals,
            "domains": gaps,
        }
    same_as_drift = recorded.get("same_as_hash") != current_same_as
    schema_drift = recorded.get("schema_hash") != current_schema
    return {
        "known": True,
        "drift": same_as_drift or schema_drift,
        "last_rebuilt_at": recorded.get("last_rebuilt_at"),
        "same_as_drift": same_as_drift,
        "schema_drift": schema_drift,
        "recorded_same_as_hash": recorded.get("same_as_hash"),
        "current_same_as_hash": current_same_as,
        "recorded_schema_hash": recorded.get("schema_hash"),
        "current_schema_hash": current_schema,
        **totals,
        "domains": gaps,
    }
```

`count_unembedded_chunks` stays in `artmind/embed_sweep.py` for its other caller (the CLI's pre-sweep notice). Only `status` stops using it.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_projection_status.py test/test_graph_snapshot.py test/test_query_staleness.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/projection.py test/test_projection_status.py
git commit -m "feat(projection): status counts unembedded entities, unprojected keys, per domain

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8 (B4): `projection rebuild --sweep`

**Files:**
- Modify: `artmind/ingest.py` (add `rebuild_and_sweep_all` after `rebuild_projection`)
- Modify: `artmind/jobs.py` (add `_resolve_failed_finalizes` after `_set_finalize_state`)
- Modify: `artmind/cli.py` (`projection_rebuild`, lines 3227–3247)
- Test: `test/test_projection_rebuild_sweep.py` (create)

One gap the spec leaves open: row 4's "last job's rebuild failed: …" would never clear, because nothing ever rewrites a job's `finalize_state`. A clean `--sweep` covers every domain with both sweeps, so it does everything any failed job still owed. It therefore marks those jobs `done` and lists them under `finalize_resolved`. It resolves nothing when any sweep was skipped.

- [ ] **Step 1: Write the failing tests**

Create `test/test_projection_rebuild_sweep.py`:

```python
"""`artmind projection rebuild --sweep` (plan 2026-10-03, B4): what the
plugin's **Rebuild graph** runs. Asserts on the calls actually made -- which
rebuild, which sweep, for which domain, with which keys -- and on the job
rows SQLite holds afterwards.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import artmind.ingest as ing
import artmind.projection as projection
from artmind.cli import cli
from artmind.jobs import _add_finalize_domain, _create_job, _get_job_status, _set_finalize_state

KEYS = {
    ("ann lee", "PERSON", "general"),
    ("acme", "ORGANIZATION", "general"),
    ("morning run", "HABIT", "habits"),
}


@pytest.fixture
def calls(monkeypatch):
    seen: dict[str, list] = {"rebuild": [], "entities": [], "chunks": []}
    monkeypatch.setattr(
        projection, "full_rebuild_batched",
        lambda domains=None: seen["rebuild"].append(domains) or {
            "rebuilt": 3, "deleted": 0, "absent": 0, "keys": 3, "batches": 1,
            "domains": ["general", "habits"], "recorded": domains is None,
        },
    )
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: set(KEYS))
    monkeypatch.setattr(
        ing, "_sweep_embeddings",
        lambda domain, keys, strict=False: seen["entities"].append((domain, sorted(keys), strict)) or len(keys),
    )
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings",
        lambda chunk_ids=None, domain=None, strict=False: seen["chunks"].append((domain, strict)) or 1,
    )
    return seen


def _failed_job() -> str:
    job_id = _create_job(["/vault/a.md", "/vault/b.md"], domain="general")
    _add_finalize_domain(job_id, "general")
    _set_finalize_state(job_id, "failed", error="general: Couldn't connect")
    return job_id


def _run(*args: str):
    return CliRunner().invoke(cli, ["projection", "rebuild", *args, "--compact"])


def test_sweep_rebuilds_every_domain_then_sweeps_each_domain_it_rebuilt(calls):
    result = _run("--sweep")

    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert calls["rebuild"] == [None], "a full, unscoped rebuild -- the only kind that clears drift"
    assert calls["entities"] == [
        ("general", sorted(k for k in KEYS if k[2] == "general"), True),
        ("habits", [("morning run", "HABIT", "habits")], True),
    ]
    assert calls["chunks"] == [("general", True), ("habits", True)]
    assert (out["domains_swept"], out["embedded"], out["chunks_embedded"], out["sweep_errors"]) == (
        ["general", "habits"], 3, 2, [],
    )
    assert out["recorded"] is True


def test_a_clean_sweep_clears_a_jobs_failed_finalize(calls):
    job_id = _failed_job()

    out = json.loads(_run("--sweep").stdout)

    assert out["finalize_resolved"] == [job_id]
    assert _get_job_status(job_id)["finalize"] == {"state": "done", "domains": ["general"], "error": None}


def test_a_skipped_sweep_is_reported_and_resolves_no_job(calls, monkeypatch):
    job_id = _failed_job()

    def refuse(chunk_ids=None, domain=None, strict=False):
        raise ing.SweepSkipped(f"chunk embed sweep skipped for domain {domain!r} (Connection refused)")

    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", refuse)

    out = json.loads(_run("--sweep").stdout)

    assert out["sweep_errors"] == [
        "general: chunk embed sweep skipped for domain 'general' (Connection refused)",
        "habits: chunk embed sweep skipped for domain 'habits' (Connection refused)",
    ]
    assert out["finalize_resolved"] == []
    assert _get_job_status(job_id)["finalize"]["state"] == "failed"


def test_sweep_refuses_a_domain(calls):
    result = _run("--sweep", "--domain", "general")

    assert result.exit_code == 2
    assert "drop --domain" in result.output
    assert calls["rebuild"] == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_projection_rebuild_sweep.py -v`
Expected: FAIL. Each test exits 2 with `No such option: --sweep`, so `json.loads` raises `JSONDecodeError`, or the `exit_code == 0` assertion fails. `test_sweep_refuses_a_domain` fails on `"drop --domain" in result.output`.

- [ ] **Step 3: Implement**

In `artmind/jobs.py`, after `_set_finalize_state`:

```python
def _resolve_failed_finalizes() -> list[str]:
    """Mark every job whose deferred rebuild failed as `done`, and return
    their ids. Only `projection rebuild --sweep` calls this, and only after a
    clean run: a full rebuild of every domain plus both sweeps for each is a
    superset of what any of those jobs still owed. A host with no registry DB
    (query-only) has no jobs, and gets no DB created for it."""
    import artmind.db as db

    if not Path(db.DB_PATH).exists():
        return []
    conn = _get_db()
    try:
        ids = [
            r[0] for r in conn.execute(
                "SELECT job_id FROM ingestion_jobs WHERE finalize_state = 'failed' ORDER BY queued_at"
            ).fetchall()
        ]
        if ids:
            conn.executemany(
                "UPDATE ingestion_jobs SET finalize_state = 'done', finalize_error = NULL WHERE job_id = ?",
                [(job_id,) for job_id in ids],
            )
            conn.commit()
        return ids
    finally:
        conn.close()
```

In `artmind/ingest.py`, after `rebuild_projection`:

```python
def rebuild_and_sweep_all() -> dict:
    """`projection rebuild --sweep`: the one repair command.

    A full, unscoped, batched rebuild (`projection.full_rebuild_batched`,
    which clears drift once the last batch commits), then both strict embed
    sweeps for every domain a key belongs to. Returns the rebuild totals plus
    `domains_swept`, `embedded`, `chunks_embedded`, `sweep_errors` (each
    prefixed with its domain; empty when every sweep ran) and
    `finalize_resolved` (the ingest jobs whose failed deferred rebuild this
    run superseded -- only when no sweep was skipped).
    """
    from artmind import projection
    from artmind.graph_query import neo4j_session
    from artmind.jobs import _resolve_failed_finalizes

    summary = projection.full_rebuild_batched(None)
    with neo4j_session() as session:
        keys = sorted(session.execute_read(lambda tx: projection.all_keys(tx, None)))
    by_domain: dict[str, list] = {}
    for key in keys:
        if key[2]:
            by_domain.setdefault(key[2], []).append(key)
    embedded = chunks = 0
    errors: list[str] = []
    for domain in sorted(by_domain):
        swept = _sweep_domain(domain, by_domain[domain])
        embedded += swept["embedded"]
        chunks += swept["chunks_embedded"]
        errors.extend(f"{domain}: {err}" for err in swept["sweep_errors"])
    summary.update(
        domains_swept=sorted(by_domain), embedded=embedded, chunks_embedded=chunks, sweep_errors=errors,
    )
    summary["finalize_resolved"] = [] if errors else _resolve_failed_finalizes()
    return summary
```

In `artmind/cli.py`, replace `projection_rebuild` (lines 3227–3247) with:

```python
@projection.command("rebuild")
@click.option("--domain", default=None, help="Restrict to one domain family (default: every domain)")
@click.option(
    "--sweep", is_flag=True,
    help="Repair: rebuild every domain, then run both embed sweeps for each domain rebuilt",
)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def projection_rebuild(domain: str | None, sweep: bool, compact: bool) -> None:
    """Recompute Entities from observations.

    Deterministic and needs no language model. Entity ids are
    `sha256(canonical_name | entity_class | domain)`, so dropping the whole
    projection and rebuilding it produces a byte-identical result — which is
    also why this is safe to run at any time.

    Commits in batches of 400 keys, one transaction each, so a large graph
    never needs one huge transaction (that ran Neo4j out of memory). Without
    `--domain` it then records `:ProjectionState`, which clears `projection
    status`'s drift — only after the last batch committed, so a rebuild that
    fails part-way leaves drift visible. Re-running is always safe.

    `--sweep` is the repair command (the Obsidian plugin's "Rebuild graph"):
    a full rebuild of every domain, then the entity and chunk embed sweeps
    for each domain it rebuilt (`domains_swept`). A sweep that could not run
    is listed under `sweep_errors`; a clean run also clears any ingest job
    whose deferred rebuild had failed (`finalize_resolved`).

    With `--domain` (no `--sweep`), both sweeps run for that domain and drift
    is not cleared. A plain domain-unscoped rebuild runs no sweep.
    """
    _setup_logger()
    if sweep and domain:
        raise click.UsageError("--sweep rebuilds every domain; drop --domain")
    if sweep:
        from artmind.ingest import rebuild_and_sweep_all

        _echo_json(rebuild_and_sweep_all(), compact)
        return
    from artmind.ingest import rebuild_projection

    _echo_json(rebuild_projection(domain), compact)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_projection_rebuild_sweep.py test/test_job_finalize.py test/test_cli_guide.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py artmind/jobs.py artmind/cli.py test/test_projection_rebuild_sweep.py
git commit -m "feat(projection): rebuild --sweep -- batched full rebuild, sweeps per domain, clears failed finalizes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9 (B5): Synthesize count — measure first; no new CLI unless it is needed

**Files:**
- (conditional) Modify: `artmind/synthesize.py` (`synthesize`, lines 194–250)
- (conditional) Modify: `artmind/cli.py` (`projection_synthesize`, lines 3270–3313)
- (conditional) Test: `test/test_synthesize.py`

The plugin runs `projection synthesize --domain D --dry-run --compact` per mapped domain and reads `examined` / `counts`. The dry run makes one Cypher read and no LLM call (`synthesize` returns before any `synthesize_key`). The read has an `OPTIONAL MATCH (s:Synthesis {id: e._id})`, backed by the `synthesis_id` uniqueness constraint (`artmind/setup.py:883`). Its cost is one row per entity. What can grow is the output: `candidates` lists every entity to synthesize. A `--countOnly` would only drop that list. It would not speed the query up.

- [ ] **Step 1: Measure (operator, read-only, against the live vault)**

```bash
cd ~/artmind_vaults/my_second_brain
grep -E '^\s*domain:' .artmind/vault.yaml | awk '{print $2}' | sort -u
# for each domain D printed above:
/usr/bin/time -p env ARTMIND_NO_PROXY=1 artmind projection synthesize --domain D --dry-run --compact > /tmp/synth-D.json
wc -c /tmp/synth-D.json
python3 -c "import json; d=json.load(open('/tmp/synth-D.json')); print(d['examined'], d['counts'], len(d['candidates']))"
```

Expected: `real` ≤ 3 s and output ≤ 512 KB for every domain. If so, **stop here**: B5 needs no code, and the fixture is added in Task 12. Note the measured numbers in the PR description. Continue to Step 2 only if a domain exceeds either limit.

- [ ] **Step 2 (only if Step 1 exceeded a limit): Write the failing test**

Append to `test/test_synthesize.py`:

```python
def test_count_only_dry_run_returns_counts_without_the_candidate_list(monkeypatch):
    import artmind.synthesize as synth

    rows = [
        {"id": "e1", "key": "a|PERSON|general", "name": "A", "entity_class": "PERSON",
         "observation_count": 3, "current_hash": "h1", "synth_hash": None, "has_open_conflict": False},
        {"id": "e2", "key": "b|PERSON|general", "name": "B", "entity_class": "PERSON",
         "observation_count": 1, "current_hash": "h2", "synth_hash": None, "has_open_conflict": False},
    ]
    sent: list = []

    class _Session:
        def run(self, cypher, **params):
            sent.append(params)
            return type("R", (), {"data": lambda self: rows})()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(synth, "neo4j_session", lambda *a, **k: _Session())

    result = synth.synthesize("general", dry_run=True, count_only=True)

    assert sent == [{"domain": "general", "nameFilter": None}]
    assert result["examined"] == 2
    assert result["counts"] == {"synthesize": 1, "skipped_too_few_observations": 1}
    assert "candidates" not in result
```

Run: `uv run --group dev pytest test/test_synthesize.py::test_count_only_dry_run_returns_counts_without_the_candidate_list -v`
Expected: FAIL with `TypeError: synthesize() got an unexpected keyword argument 'count_only'`.

- [ ] **Step 3 (conditional): Implement**

In `artmind/synthesize.py`, add `count_only: bool = False,` after `dry_run: bool = False,` in `synthesize`'s signature, and replace the dry-run return block with:

```python
    if dry_run:
        result = {
            "domain": domain, "command": "projection_synthesize", "model": resolved_model,
            "dry_run": True, "examined": len(rows), "counts": counts,
        }
        if not count_only:
            result["candidates"] = [{"key": r["key"], "name": r["name"]} for r in candidates]
        return result
```

In `artmind/cli.py`, add to `projection_synthesize` after the `--dry-run` option:

```python
@click.option("--countOnly", "count_only", is_flag=True, help="With --dry-run: only examined/counts, no candidate list")
```

add `count_only: bool,` to its parameters after `dry_run: bool,`, and pass `count_only=count_only,` to `synthesize(...)` after `dry_run=dry_run,`.

Run: `uv run --group dev pytest test/test_synthesize.py -v`
Expected: PASS.

- [ ] **Step 4 (conditional): Commit**

```bash
git add artmind/synthesize.py artmind/cli.py test/test_synthesize.py
git commit -m "feat(projection): synthesize --dry-run --countOnly for the plugin's count

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 10 (B6): A table this machine ingested is not "to apply"

**Files:**
- Test: `test/test_vault_status_own_tables.py` (create)
- Modify: `artmind/vault_sync.py` (imports lines 28–41; new helpers after `_blobs_at`, line 928; `_store_pending` line 1669; `_graph_pending` line 1726)
- Modify: `artmind/structured/text_export.py` (`export_structured_text`, before its `logger.info`, ~line 303)
- Modify: `test/test_plugin_fixtures.py` (`_behind`, lines 237–248)

- [ ] **Step 1: Write the failing test first**

Create `test/test_vault_status_own_tables.py`:

```python
"""A table ingested and committed on THIS machine is not "to apply"
(plan 2026-10-03, B6).

Uses the plugin-fixture vault: real git, a real registry and DuckDB, real
`ingest_structured_file` (which exports the table's CSV + .meta.json into the
vault), Neo4j in memory.
"""
from __future__ import annotations

import json

from test_plugin_fixtures import vault  # noqa: F401  (the fixture)

TEAM = "name,employer,level\nAnn Lee,Acme,senior\n"
CSV = ".artmind/data/structured_text/general/team.csv"
META = ".artmind/data/structured_text/general/team.meta.json"


def _stores(vault) -> dict:
    record = vault.run("vault", "status", "--compact")
    assert record["exit_code"] == 0, record
    return record["json"]["sync"]["stores"]


def _ingest_here(vault) -> None:
    from artmind.structured.pipeline import ingest_structured_file

    vault.sync_bootstrap()
    ingest_structured_file(vault.write("Team/team.csv", TEAM), "general")
    vault.commit("ingested team on this machine")


def test_a_table_ingested_and_committed_here_is_current(vault):
    _ingest_here(vault)

    stores = _stores(vault)

    assert (stores["structured"]["state"], stores["structured"]["tables"]) == ("current", [])
    assert (stores["graph"]["state"], stores["graph"]["tables"]) == ("current", [])


def test_rows_changed_on_the_other_machine_are_still_behind(vault):
    _ingest_here(vault)
    csv = vault.root / CSV
    csv.write_text(csv.read_text() + "Bo Chan,Acme,junior\n")
    vault.commit("laptop B added a row")

    stores = _stores(vault)

    assert stores["structured"]["tables"] == [["general", "team"]]
    assert stores["graph"]["tables"] == [["general", "team"]]


def test_a_curation_change_from_the_other_machine_is_still_behind_for_the_structured_store(vault):
    _ingest_here(vault)
    meta_path = vault.root / META
    meta = json.loads(meta_path.read_text())
    meta["table"]["grain"] = "lookup"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    vault.commit("laptop B: db grain --set lookup")

    assert _stores(vault)["structured"]["tables"] == [["general", "team"]]
```

- [ ] **Step 2: Run it to verify it fails, and apply the drop rule**

Run: `uv run --group dev pytest test/test_vault_status_own_tables.py -v`
Expected: `test_a_table_ingested_and_committed_here_is_current` FAILS with `assert ('behind', [['general', 'team']]) == ('current', [])`. The checked-in `vault-status.behind.json` already shows this shape for a table this machine ingested. The other two pass.

**If `test_a_table_ingested_and_committed_here_is_current` passes on this first run, drop B6:** delete the test file, skip the rest of this task, and note "B6 dropped: already current" in the PR description.

- [ ] **Step 3: Implement the machine-local fingerprint**

In `artmind/vault_sync.py`, add `import hashlib` to the imports (after `from __future__ import annotations`, before `import io`).

After `_blobs_at` (ends at line 928), add:

```python
#: Machine-local `state.json` key: `{"<domain>/<table>": fingerprint}` of the
#: structured text this machine itself last wrote (`export_structured_text`).
#: `vault status` skips a table whose committed text equals it: this
#: machine's stores already hold it.
TABLE_FINGERPRINTS_KEY = "table_text_fingerprints"


def git_blob_oid(data: bytes) -> str:
    """The object id git gives `data` as a blob (`git hash-object`), so a file
    on disk compares with `_oids_at` without a git call."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def table_text_fingerprint(csv_oid: str, meta: bytes | None) -> str:
    """One table's structured text as a comparable string: the CSV's blob id
    plus a digest of its whole `.meta.json`. Curation fields are included,
    so a curation-only change from the other machine still counts for the
    structured store."""
    return f"{csv_oid}:{hashlib.sha256(meta or b'').hexdigest()}"


def record_table_text_fingerprints(vault_dir: Path, st_dir: Path, tables: list[tuple[str, str]]) -> None:
    """Remember, in this machine's `state.json`, the text it just wrote for
    `tables` under `st_dir`. A table with no CSV there is skipped."""
    from artmind.vault import VaultLayout, read_state, write_state

    if not tables:
        return
    layout = VaultLayout(vault_dir)
    held = dict(read_state(layout).get(TABLE_FINGERPRINTS_KEY) or {})
    for domain, table_name in tables:
        csv_path = st_dir / domain / f"{table_name}.csv"
        if not csv_path.is_file():
            continue
        meta_path = st_dir / domain / f"{table_name}{_META_SUFFIX}"
        meta = meta_path.read_bytes() if meta_path.is_file() else None
        held[f"{domain}/{table_name}"] = table_text_fingerprint(git_blob_oid(csv_path.read_bytes()), meta)
    write_state(layout, {TABLE_FINGERPRINTS_KEY: held})


def _drop_tables_held_here(vault_dir: Path, head: str, tables) -> list[tuple[str, str]]:
    """`tables` minus those whose text committed at `head` is exactly what
    this machine last wrote: its structured store already holds them. Reads
    only blob ids for the CSVs (one `git ls-tree`) and the small
    `.meta.json` blobs. Reporting only: `vault sync` still restores every
    table its diff names (idempotent)."""
    import paths
    from artmind.vault import VaultLayout, read_state

    tables = list(tables)
    if not tables:
        return tables
    held = read_state(VaultLayout(vault_dir)).get(TABLE_FINGERPRINTS_KEY) or {}
    if not held:
        return tables
    try:
        st_rel = Path(paths.STRUCTURED_TEXT_DIR).relative_to(vault_dir)
    except ValueError:
        return tables
    csv = {t: str(st_rel / t[0] / f"{t[1]}.csv") for t in tables}
    meta = {t: str(st_rel / t[0] / f"{t[1]}{_META_SUFFIX}") for t in tables}
    oids = _oids_at(vault_dir, head, list(csv.values()))
    metas = _blobs_at(vault_dir, head, list(meta.values()))
    return [
        t for t in tables
        if not (
            csv[t] in oids
            and held.get(f"{t[0]}/{t[1]}") == table_text_fingerprint(oids[csv[t]], metas.get(meta[t]))
        )
    ]
```

In `_store_pending` (line 1669), replace

```python
        if store == "structured":
            tables, _ = _classify_structured_text_diff(vault_dir, bookmark, head, None)
            docs = curation = same_as_groups = 0
```

with

```python
        if store == "structured":
            tables, _ = _classify_structured_text_diff(vault_dir, bookmark, head, None)
            tables = _drop_tables_held_here(vault_dir, head, tables)
            docs = curation = same_as_groups = 0
```

In `_graph_pending` (line 1726), replace

```python
    tables = set(plan.regenerate_tables)
```

with

```python
    tables = set(_drop_tables_held_here(vault_dir, head, plan.regenerate_tables))
```

and add this sentence to `_graph_pending`'s docstring: "A table counts unless its committed text is what this machine itself last exported (`_drop_tables_held_here`), the table counterpart of the document fingerprint check."

In `artmind/structured/text_export.py`, in `export_structured_text`, insert directly before `logger.info("structured text export: ...")`:

```python
    # Remember what this machine wrote, so `vault status` does not report
    # its own tables as "to apply" once they are committed (plan 2026-10-03 B6).
    vault_dir = paths.ARTMIND_VAULT_DIR
    if vault_dir is not None and dest_dir.resolve() == Path(paths.STRUCTURED_TEXT_DIR).resolve():
        from artmind.vault_sync import record_table_text_fingerprints

        record_table_text_fingerprints(
            Path(vault_dir), dest_dir, [(t["domain"], t["table_name"]) for t in target_tables]
        )
```

- [ ] **Step 4: Keep the "from laptop B" fixtures honest**

`_behind` in `test/test_plugin_fixtures.py` ingests its table locally to stand in for the other laptop's commit. Now it must forget this machine's fingerprint, or `vault-status.behind` and `vault-sync.success` would stop showing the table. Replace `_behind` (lines 237–248) with:

```python
def _behind(vault: Vault) -> None:
    """Synced, then the other laptop's commit pulled: two documents and one
    structured table this machine's stores have not applied. The table is
    loaded here to produce its committed text, then this machine's record of
    having written it is dropped -- as on the machine that pulled it."""
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.vault import VaultLayout, write_state
    from artmind.vault_sync import TABLE_FINGERPRINTS_KEY

    vault.sync_bootstrap()
    vault.kg_doc("quarterly_review", "doc-quarterly-review")
    vault.kg_doc("hiring_plan", "doc-hiring-plan")
    source = vault.write("Team/team.csv", "name,employer,level\nAnn Lee,Acme,senior\nBo Chan,Acme,junior\n")
    ingest_structured_file(source, "general")
    write_state(VaultLayout(vault.root), {}, remove=(TABLE_FINGERPRINTS_KEY,))
    vault.commit("from laptop B")
```

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_vault_status_own_tables.py test/test_plugin_fixtures.py test/test_vault_sync.py test/test_vault_cli.py test/test_structured_text_export.py -v`
Expected: PASS, with no fixture diff (`vault-status.behind`, `vault-sync.success` unchanged).

- [ ] **Step 6: Commit**

```bash
git add artmind/vault_sync.py artmind/structured/text_export.py test/test_vault_status_own_tables.py test/test_plugin_fixtures.py
git commit -m "fix(vault): a table this machine exported and committed is not reported as to apply

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 11 (B7): `?prompt=` prefills the admin console's chat, never sends it

**Files:**
- Modify: `artmind/webui/app.py` (module constants near line 26; `index`, lines 104–107)
- Modify: `artmind/webui/templates/admin.html` (line 87)
- Modify: `artmind/webui/static/app.js` (after the `keydown` listener, line 468)
- Test: `test/test_webui_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_webui_app.py`:

```python
# ── B7: deep link into the admin console's agent chat ───────────────────────

REVIEW = "Review the proposed classifications for table <team> (domain general)"


def _admin_client() -> TestClient:
    return TestClient(create_app(
        registry=SessionRegistry(client_factory=FakeBackend), template_name="admin.html", admin_routes=True,
    ))


def test_admin_console_prefills_the_chat_from_a_prompt_query():
    body = _admin_client().get("/", params={"prompt": REVIEW}).text
    assert (
        ">Review the proposed classifications for table &lt;team&gt; (domain general)</textarea>" in body
    ), "prefilled, and HTML-escaped"


def test_admin_console_without_a_prompt_has_an_empty_composer():
    body = _admin_client().get("/").text
    assert 'autofocus></textarea>' in body


def test_an_overlong_prompt_is_capped():
    from artmind.webui.app import PREFILL_MAX_CHARS

    body = _admin_client().get("/", params={"prompt": "x" * (PREFILL_MAX_CHARS + 500)}).text
    assert "x" * PREFILL_MAX_CHARS + "</textarea>" in body
    assert "x" * (PREFILL_MAX_CHARS + 1) not in body


def test_the_chat_ui_ignores_a_prompt_query():
    client, _ = _client()
    assert "Review the proposed" not in client.get("/", params={"prompt": REVIEW}).text


def test_a_prefilled_page_starts_no_agent_turn():
    registry = SessionRegistry(client_factory=FakeBackend)
    client = TestClient(create_app(registry=registry, template_name="admin.html", admin_routes=True))
    client.get("/", params={"prompt": REVIEW})
    assert registry._sessions == {}, "rendering the page must never reach the agent"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_webui_app.py -v`
Expected: `test_admin_console_prefills_the_chat_from_a_prompt_query` FAILS (the textarea is empty). `test_an_overlong_prompt_is_capped` FAILS with `ImportError: cannot import name 'PREFILL_MAX_CHARS'`. The others pass already.

- [ ] **Step 3: Implement**

In `artmind/webui/app.py`, after `DEFAULT_ADMIN_UI_PORT = 8379` (line 26):

```python
#: Longest `?prompt=` the admin console prefills (the Obsidian plugin's
#: table-review deep link is one sentence; anything longer is not a link).
PREFILL_MAX_CHARS = 2000
```

Replace `index` (lines 104–107) with:

```python
    @app.get("/")
    async def index(request: Request):
        context = {"page_title": page_title} if page_title is not None else {}
        if admin_routes:
            # `?prompt=` prefills the agent chat's composer (plan 2026-10-03
            # B7). Rendered into the textarea, autoescaped; app.js only
            # enables Send -- nothing is ever sent without the operator.
            context["prefill"] = request.query_params.get("prompt", "")[:PREFILL_MAX_CHARS]
        return templates.TemplateResponse(request, template_name, context)
```

In `artmind/webui/templates/admin.html`, replace line 87:

```html
      <textarea id="prompt" rows="1" placeholder="Ask the admin assistant…" aria-label="Message" autofocus></textarea>
```

with

```html
      <textarea id="prompt" rows="1" placeholder="Ask the admin assistant…" aria-label="Message" autofocus>{{ prefill | default("") }}</textarea>
```

In `artmind/webui/static/app.js`, insert after the `promptEl.addEventListener("keydown", …)` block (ends line 468):

```js
// A deep link (`/?prompt=…`, the Obsidian plugin's table-review row) arrives
// with the composer already filled server-side. Never sent: the operator
// reads it, edits it if they want, and presses Enter.
if (promptEl.value.trim() !== "") {
  autogrow();
  sendBtn.disabled = false;
  promptEl.setSelectionRange(promptEl.value.length, promptEl.value.length);
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_webui_app.py test/test_webui_admin_api.py -v`
Expected: PASS.

- [ ] **Step 5: Browser check in the admin-ui preview**

Start the `admin-ui` configuration from `.claude/launch.json` (`preview_start` with name `admin-ui`, port 8379). Then:
1. `preview_eval`: `location.href = "/?prompt=" + encodeURIComponent("Review the proposed classifications for table team (domain general)")`.
2. `preview_eval`: `({value: document.getElementById("prompt").value, sendDisabled: document.getElementById("send").disabled, turns: document.getElementById("chat").childElementCount})`.
   Expected: `{value: "Review the proposed classifications for table team (domain general)", sendDisabled: false, turns: 0}`.
3. `preview_network`: no `POST /api/chat` request.
4. `preview_screenshot`: the composer shows the text, grown to fit.
Stop the server afterwards with `preview_stop`.

- [ ] **Step 6: Commit**

```bash
git add artmind/webui/app.py artmind/webui/templates/admin.html artmind/webui/static/app.js test/test_webui_app.py
git commit -m "feat(admin-ui): ?prompt= prefills the agent chat (never auto-sends)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 12 (B8): Plugin fixtures for every new shape

**Files:**
- Modify: `test/test_plugin_fixtures.py` (imports lines 28–37; new fake + scenarios before `SCENARIOS`, line ~503; `fixture_name`, lines ~506–516; `normalise`, lines ~521–560)
- Regenerate: `obsidian/artmind-obsidian/test/fixtures/*.json`

New fixtures: `ingest-job-status.finishing`, `ingest-job-status.finalize-failed`, `projection-status.ready`, `projection-status.needs-rebuild`, `projection-rebuild.sweep`, `projection-synthesize.dry-run`, `db-review`. Regenerating also adds `finalize` to the existing `ingest-job-status.*`, `ingest-jobs-active` and `ingest-job-results.done`.

- [ ] **Step 1: Add the scenarios**

In `test/test_plugin_fixtures.py`, add `from contextlib import contextmanager` to the imports (after `import subprocess`).

Insert before the line `SCENARIOS = {`:

```python
# ── Neo4j as the projection commands read it ────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return None


class ProjectionGraph:
    """Canned rows per query shape for `projection status`, `projection
    rebuild --sweep` and `projection synthesize --dry-run`. An unexpected
    query raises, so a new read cannot slip into a fixture unnoticed."""

    def __init__(self, *, state=None, entity_gaps=(), unprojected=(), chunk_gaps=(), keys=(), entities=()):
        self.state = state
        self.entity_gaps = list(entity_gaps)
        self.unprojected = list(unprojected)
        self.chunk_gaps = list(chunk_gaps)
        self.keys = list(keys)
        self.entities = list(entities)

    def run(self, cypher, **params):
        if "MERGE (s:ProjectionState" in cypher:
            self.state = {"same_as_hash": params["same_as_hash"], "schema_hash": params["schema_hash"],
                          "last_rebuilt_at": params["now"]}
            return _Rows([])
        if "ProjectionState" in cypher:
            return _Rows([{"p": self.state}] if self.state else [])
        if "RETURN DISTINCT n.key" in cypher:
            return _Rows([{"key": k} for k in self.keys] if "(n:Observation)" in cypher else [])
        if "MATCH (e:Entity)" in cypher and "embedding IS NULL" in cypher:
            return _Rows(self.entity_gaps)
        if "MATCH (o:Observation)" in cypher and "AGGREGATES" in cypher:
            return _Rows(self.unprojected)
        if "MATCH (c:DocChunk)" in cypher:
            return _Rows(self.chunk_gaps)
        if "MATCH (s:Synthesis" in cypher:
            return _Rows(self.entities)
        raise AssertionError(f"unexpected query in a plugin fixture: {cypher!r}")

    def execute_read(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    execute_write = execute_read

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextmanager
def _graph(graph: ProjectionGraph):
    import artmind.graph_query as graph_query
    import artmind.synthesize as synthesize_module

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(graph_query, "neo4j_session", lambda *a, **k: graph)
        mp.setattr(synthesize_module, "neo4j_session", lambda *a, **k: graph)
        yield mp


def _current_projection_state() -> dict:
    from artmind import projection, same_as

    return {"same_as_hash": same_as.content_hash(), "schema_hash": projection.schema_set_hash(),
            "last_rebuilt_at": "2026-09-30T12:00:00+00:00"}


def s_projection_status_ready(vault):
    with _graph(ProjectionGraph(state=_current_projection_state())):
        return vault.run("projection", "status", "--compact")


def s_projection_status_needs_rebuild(vault):
    """What job 0c616a6f left: rebuilt, but neither sweep reached Neo4j --
    plus a same_as.yaml edit since the last full rebuild."""
    graph = ProjectionGraph(
        state={**_current_projection_state(), "same_as_hash": "0" * 64},
        entity_gaps=[{"domain": "general", "n": 2801}, {"domain": "habits", "n": 12}],
        unprojected=[{"domain": "general", "n": 40}],
        chunk_gaps=[{"domain": "general", "n": 310}],
    )
    with _graph(graph):
        return vault.run("projection", "status", "--compact")


def s_projection_rebuild_sweep(vault):
    import artmind.ingest as ing
    from artmind import projection

    graph = ProjectionGraph(keys=["ann lee|PERSON|general", "acme|ORGANIZATION|general", "morning run|HABIT|habits"])
    with _graph(graph) as mp:
        mp.setattr(projection, "rebuild",
                   lambda tx, keys, **kw: {"rebuilt": len(keys), "deleted": 0, "absent": 0, "keys": len(keys)})
        mp.setattr(ing, "_sweep_embeddings", lambda domain, keys, strict=False: len(keys))
        mp.setattr(ing, "_sweep_chunk_embeddings", lambda chunk_ids=None, domain=None, strict=False: 3)
        return vault.run("projection", "rebuild", "--sweep", "--compact")


def s_projection_synthesize_dry_run(vault):
    rows = [
        {"id": "e1", "key": "ann lee|PERSON|general", "name": "Ann Lee", "entity_class": "PERSON",
         "observation_count": 4, "current_hash": "h1", "synth_hash": None, "has_open_conflict": False},
        {"id": "e2", "key": "acme|ORGANIZATION|general", "name": "Acme", "entity_class": "ORGANIZATION",
         "observation_count": 3, "current_hash": "h2", "synth_hash": "h2", "has_open_conflict": False},
        {"id": "e3", "key": "bo chan|PERSON|general", "name": "Bo Chan", "entity_class": "PERSON",
         "observation_count": 1, "current_hash": "h3", "synth_hash": None, "has_open_conflict": False},
    ]
    with _graph(ProjectionGraph(entities=rows)) as mp:
        mp.setenv("ARTMIND_KG_LLM_PROVIDER", "ollama")
        mp.setenv("ARTMIND_KG_LLM_MODEL", "ministral-3:14b")
        return vault.run("projection", "synthesize", "--domain", "general", "--dry-run", "--compact")


def s_db_review(vault):
    _table_registered_and_mapped(vault)
    return vault.run("db", "review", "--compact")


def s_ingest_job_status_finishing(vault):
    """Every file done; the deferred rebuild is running (`finalize.state == pending`)."""
    from artmind.jobs import _add_finalize_domain

    job_id = _jobs([("completed", None), ("completed", None)], status="processing", processed=2)
    _add_finalize_domain(job_id, "general")
    with _hold_worker_lock(vault):
        return vault.run("ingest", "job-status", job_id, "--compact")


def s_ingest_job_status_finalize_failed(vault):
    """Every file committed, but the deferred rebuild could not reach Neo4j."""
    from artmind.jobs import _add_finalize_domain, _set_finalize_state

    job_id = _jobs([("completed", None), ("completed", None)], status="completed", processed=2)
    _add_finalize_domain(job_id, "general")
    _set_finalize_state(
        job_id, "failed",
        error="general: projection rebuild failed on batch 3/11 (Couldn't connect to 127.0.0.1:7687)",
    )
    return vault.run("ingest", "job-status", job_id, "--compact")
```

In `fixture_name`, replace the command tuple with:

```python
    for command in ("vault-status", "vault-sync", "vault-resolve", "vault-doctor", "ingest-pending",
                    "ingest-async", "ingest-job-status", "ingest-job-results", "ingest-jobs-active", "ingest-retry-job",
                    "table2graph", "projection-status", "projection-rebuild", "projection-synthesize", "db-review"):
```

In the normalisation section, add after `_TIMESTAMP = ...`:

```python
_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
```

and in `normalise`, insert directly after the path-replacement loop (before `shas: dict[str, str] = {}`):

```python
    # sha256 digests (`projection status`' same_as/schema hashes): stable
    # stand-ins, equal where the originals were equal, so a schema edit
    # elsewhere never churns a fixture.
    digests: dict[str, str] = {}
    for found in _SHA256.findall(text):
        digests.setdefault(found, (format(len(digests) + 10, "x") * 64)[:64])
    for found, fake in digests.items():
        text = text.replace(found, fake)
```

Also add this to the module docstring's normalisation sentence: "sha256 digests (each distinct one becomes `aaaa…`, `bbbb…`)".

- [ ] **Step 2: Run the fixture check to see it fail**

Run: `uv run --group dev pytest test/test_plugin_fixtures.py -v`
Expected: FAIL. The seven new scenarios fail with `… .json is missing -- regenerate`. The existing job scenarios fail on the record mismatch (new `finalize` key). `test_every_fixture_has_a_scenario` fails.

- [ ] **Step 3: Regenerate, then check it passes**

```bash
ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -q
uv run --group dev pytest test/test_plugin_fixtures.py -v
git diff --stat obsidian/artmind-obsidian/test/fixtures/
```

Expected: the second run PASSES. The diff shows the seven new files plus a `finalize` block in `ingest-job-status.{done,running,stalled}.json`, `ingest-jobs-active.json` and `ingest-job-results.done.json`. Nothing else changes; in particular `vault-status.*` and `vault-sync.*` are untouched.

Open the new files and check the shapes by eye:
- `projection-status.needs-rebuild.json`: `drift: true`, `same_as_drift: true`, `unembedded_entities: 2813`, `unprojected_keys: 40`, `unembedded_chunks: 310`, `domains.general`, `domains.habits`.
- `projection-status.ready.json`: `drift: false`, totals 0, `domains: {}`.
- `projection-rebuild.sweep.json`: `recorded: true`, `domains_swept: ["general", "habits"]`, `embedded: 3`, `chunks_embedded: 6`, `sweep_errors: []`, `finalize_resolved: []`.
- `projection-synthesize.dry-run.json`: `examined: 3`, `counts: {synthesize: 1, skipped_unchanged: 1, skipped_too_few_observations: 1}`, `candidates: [{key: "ann lee|PERSON|general", name: "Ann Lee"}]`.
- `ingest-job-status.finishing.json`: `status: processing`, `finalize: {state: "pending", domains: ["general"], error: null}`.
- `ingest-job-status.finalize-failed.json`: `finalize.state: "failed"` with the error.
- `db-review.json`: `exit_code: 0` and the `team` table listed.

- [ ] **Step 4: Run the plugin's tests**

Run: `just obsidian-plugin-test`
Expected: PASS. No plugin code reads the new keys yet (that is Plan 2), and every fixture is loaded by name (`test/fixtures.ts`).

- [ ] **Step 5: Commit**

```bash
git add test/test_plugin_fixtures.py obsidian/artmind-obsidian/test/fixtures/
git commit -m "test(plugin): fixtures for finalize, projection status/rebuild --sweep/synthesize --dry-run, db review

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 13 (B9): Docs, skills, CLI help, justfile

**Files:**
- Modify: `docs/INSTALL.md` (new subsection after "## Run", line 210)
- Modify: `docs/projection-pipeline.md` (§4, lines 211–241)
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md` (lines 85–95, 520–523)
- Modify: `artmind/skills/artmind-curate/SKILL.md` (lines 127–133, 157–158, 334–335)
- Modify: `artmind/cli.py` (`projection` group docstring line 3217; `projection_status` docstring lines 3253–3260)
- Modify: `justfile` (lines 176–182)

- [ ] **Step 1: `docs/INSTALL.md`**

Insert before `## Core vs the [ingest] extra` (line 223), as a new `### Neo4j memory` subsection of "Run":

````markdown
### Neo4j memory

Give Neo4j a bounded heap and a transaction-memory cap. With a cap, a
transaction that is too large fails cleanly with
`MemoryPoolOutOfMemoryError`. Without one, it grows until the JVM, or the
Docker VM it runs in, is killed.

| Setting | Recommended | Docker env var |
|---|---|---|
| `server.memory.heap.max_size` (and `initial_size`) | `1G` | `NEO4J_server_memory_heap_max__size=1G` |
| `server.memory.pagecache.size` | `512M` | `NEO4J_server_memory_pagecache_size=512M` |
| `db.memory.transaction.total.max` | `512M` (must stay below the heap) | `NEO4J_db_memory_transaction_total_max=512M` |

**colima / Docker VM: at least 4 GiB** (`colima stop && colima start --memory 4`).
On 2026-10-03, a 2 GiB VM was run out of memory by one 4,332-key rebuild
transaction, and Neo4j was killed.

artmind keeps its own transactions small. A full projection rebuild commits
400 keys per transaction (`projection.REBUILD_BATCH`), and `table2graph` uses
the same batches. To see the cap and the batching work, run
`just dev-rebuild-memcheck` from the checkout. It uses a disposable Neo4j
container on its own port and never touches yours.
````

- [ ] **Step 2: `docs/projection-pipeline.md` §4**

In the mermaid block, replace

```
    T7["directory ingest"] --> DEFER["deferred — one full rebuild at the end"]
```

with

```
    T7["directory ingest"] --> DEFER["deferred — one batched rebuild per domain at the end"]
```

Replace the "Command sequences" table with:

```markdown
| | Sequence |
|---|---|
| **single file** | write observations + rebuild *(one transaction)* → commit → embed sweep |
| **directory** | per-document observation writes → per domain: full rebuild **in batches of 400 keys, one transaction each** → embed sweeps → *(explicit)* `projection synthesize` |
| **repair** | `projection rebuild --sweep`: batched full rebuild of every domain → `:ProjectionState` recorded → entity + chunk sweeps per domain |
```

After the paragraph ending "…and that guarantee is worth more than the convenience.", add:

```markdown
### Batched full rebuild, `finalize`, and `--sweep`

A full rebuild (`projection.full_rebuild_batched`) reads every key in one read
transaction and commits them in batches (`REBUILD_BATCH = 400`). A same-as
group always stays within one batch. The end state equals one big
transaction: each batch rewrites every `RELATES_TO` edge touching its own
entities, from both ends. `:ProjectionState` is written last, in its own
transaction, and only for an unscoped rebuild, only after every batch has
committed. A failure part-way therefore leaves `projection status` reporting
drift. Every batch is idempotent; the repair is to run it again.

An ingest job **owes** its deferred rebuild on its row: `ingestion_jobs.finalize_domains`
is appended as each file's commit defers. After the file loop, the worker
rebuilds and sweeps each owed domain. A retried or resumed job does the same
with 0 queued files. `ingest job-status` / `jobs-active` / `job-results`
report `finalize: {state: none|pending|done|failed, domains, error}`. A
rebuild that raises, or a sweep that could not reach Neo4j (`sweep_errors`),
makes it `failed`. The worker keeps running.

`projection status` also counts what is missing: `unembedded_entities` (what
the entity sweep would pick up), `unprojected_keys` (observation keys no
Entity aggregates yet), `unembedded_chunks`, each broken down under
`domains`, plus one `drift` flag. **`projection rebuild --sweep`** is the
repair for all of them, and what the Obsidian plugin's "Rebuild graph" runs.
A clean run also marks every `failed` job finalize `done` (`finalize_resolved`).
```

- [ ] **Step 3: Skills**

In `artmind/skills/artmind-ingestion-helper/SKILL.md`, after the bulk-load paragraph (ends line 94, "…for the full `projection synthesize` reference."), add:

````markdown
**If the deferred rebuild fails** (Neo4j down or out of memory), the job's files
still show `completed`, but `ingest job-status <id> --compact` shows
`finalize.state: "failed"` and the reason. `pending` with `stalled: true` means
the worker died mid-rebuild: `ingest retry-job <id>` finishes it even though no
file is re-queued. To repair directly, and to clear a failed `finalize`:
```bash
artmind projection rebuild --sweep --compact   # batched full rebuild + both embed sweeps per domain
artmind projection status --compact            # expect drift false, unembedded_entities 0, unprojected_keys 0
```
````

At line 523, replace "re-run the same `ingest table2graph` — it is idempotent — or `artmind projection rebuild`." with "re-run the same `ingest table2graph` — it is idempotent — or `artmind projection rebuild --sweep`."

In `artmind/skills/artmind-curate/SKILL.md`, replace lines 128–129:

```bash
artmind projection status --compact      # confirm same_as_drift: true
artmind projection rebuild --compact     # clears it
```

with

```bash
artmind projection status --compact            # confirm same_as_drift: true
artmind projection rebuild --sweep --compact   # clears it, and re-embeds what the rebuild touched
```

Replace lines 157–158:

```
2. `artmind projection rebuild --domain <d> --compact` (or a bare
   `artmind projection rebuild --compact` to also clear drift).
```

with

```
2. `artmind projection rebuild --domain <d> --compact` (or
   `artmind projection rebuild --sweep --compact` to rebuild every domain,
   clear drift and re-embed).
```

Replace lines 334–335:

```bash
artmind projection status --compact             # drift: same_as.yaml / schema-set hash vs. last full rebuild
artmind projection rebuild [--domain <d>] --compact   # recompute Entities from observations; no LLM
```

with

```bash
artmind projection status --compact             # drift + unembedded_entities / unprojected_keys / unembedded_chunks, per domain
artmind projection rebuild [--domain <d>] --compact   # recompute Entities from observations, in batches; no LLM
artmind projection rebuild --sweep --compact    # the repair: every domain, then both embed sweeps per domain
```

- [ ] **Step 4: CLI help text**

In `artmind/cli.py`, in the `projection` group docstring (line 3217), append this paragraph:

```
    `projection rebuild --sweep` is the one repair command: a batched full
    rebuild, then both embed sweeps for every domain. `projection status`
    says whether it is needed.
```

Replace `projection_status`'s docstring with:

```python
    """Report drift and what the projection is still missing.

    Drift: compares the `:ProjectionState` singleton (recorded by the last
    FULL `projection rebuild`) against `same_as.yaml`'s current hash and the
    domain schemas' current hash right now; `drift` is either. Gaps:
    `unembedded_entities` (what the entity embed sweep would pick up),
    `unprojected_keys` (observation keys no Entity aggregates yet),
    `unembedded_chunks`, with a per-domain breakdown under `domains`.
    Read-only: queries cannot self-heal (`read_session()` stays
    READ_ACCESS). `projection rebuild --sweep` clears all of it.
    """
```

- [ ] **Step 5: justfile**

Replace the `projection-rebuild` and `projection-status` recipes (lines 176–182) with:

```
# recompute Entities from observations, in batches (default: every domain)  (usage: just projection-rebuild [domain])
projection-rebuild domain="":
    uv run artmind projection rebuild {{ if domain != "" { "--domain " + domain } else { "" } }}

# the repair: batched full rebuild of every domain, then both embed sweeps per domain  (usage: just projection-rebuild-sweep)
projection-rebuild-sweep:
    uv run artmind projection rebuild --sweep

# report drift and unembedded entities / unprojected keys / unembedded chunks, per domain  (usage: just projection-status)
projection-status:
    uv run artmind projection status
```

- [ ] **Step 6: Verify the help and the guide**

```bash
just dev-cli-help | grep -A3 "rebuild"
uv run --group dev pytest test/test_cli_guide.py test/test_webui_help.py -v
```

Expected: `--sweep` is listed under `projection rebuild`, and the tests PASS.

- [ ] **Step 7: Commit**

```bash
git add docs/INSTALL.md docs/projection-pipeline.md artmind/skills/artmind-ingestion-helper/SKILL.md artmind/skills/artmind-curate/SKILL.md artmind/cli.py justfile
git commit -m "docs: Neo4j memory settings, batched rebuild, finalize, projection rebuild --sweep

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Full suite

- [ ] **Step 1: Run everything**

Run: `just dev-test`
Expected: all tests PASS (the previous count plus about 45 new ones). Fix any stub that still has the old `_sweep_embeddings(domain, keys)` signature and is reached through `rebuild_projection`. Give it `strict=False` (Task 4 pattern); never weaken an assertion.

Run: `just obsidian-plugin-test`
Expected: PASS.

- [ ] **Step 2: Commit any follow-up stub fixes**

```bash
git add test/
git commit -m "test: follow the strict sweep keyword in remaining stubs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

(Skip this step if Step 1 needed no changes.)

---

## Task 15: Repairing the live graph (manual, run by the user/operator)

Run this only after Tasks 1–8 pass (B1–B4) and Task 3's memory check printed `PASS`. Run every command yourself; an agent must not run them. This writes to the `my_second_brain` graph. It is idempotent and safe to repeat.

- [ ] **Step 1: Install the new code and stop stale daemons**

```bash
cd ~/projects/artmind9
just dev-install            # runs dev-stop-daemons first: no serve/worker keeps the old code
colima list                 # MEMORY column: if it says 2GiB, give the VM 4 (docs/INSTALL.md "Neo4j memory")
```

If the VM has 2 GiB: stop the vault's Neo4j cleanly, then `colima stop && colima start --memory 4`, then start the vault's Neo4j again with `neo4j-manager`. The embedding service (Ollama with `nomic-embed-text`, or your configured provider) must be running too, or the sweeps report `sweep_errors`.

- [ ] **Step 2: Look before repairing**

```bash
cd ~/artmind_vaults/my_second_brain
ARTMIND_NO_PROXY=1 artmind projection status --compact
```

Expected: `unembedded_entities` around 2,801 (job `0c616a6f`'s skipped sweeps), possibly `unprojected_keys` > 0 (job `5f5c2d11`'s rebuild that never ran), and `drift` true or false. `domains` shows where the gaps are.

- [ ] **Step 3: Repair**

```bash
ARTMIND_NO_PROXY=1 artmind projection rebuild --sweep --compact
```

Expected: one JSON object, for example:

```json
{"rebuilt": 4310, "deleted": 0, "absent": 22, "keys": 4332, "batches": 11, "domains": ["general", "habits", "..."], "recorded": true, "domains_swept": ["general", "habits", "..."], "embedded": 2801, "chunks_embedded": 0, "sweep_errors": [], "finalize_resolved": []}
```

The run must show `recorded: true` and `sweep_errors: []`, and `batches` must be `ceil(keys / 400)`. The logs show `projection: rebuilt batch n/N` lines. Neo4j stays up the whole time (`docker stats` shows memory flat, not climbing to the VM limit).

If `sweep_errors` is not empty, read the reason (embedding service down, or Neo4j unreachable), fix it, and run the same command again.

- [ ] **Step 4: Confirm**

```bash
ARTMIND_NO_PROXY=1 artmind projection status --compact
```

Expected: `"known": true`, `"drift": false`, `"unembedded_entities": 0`, `"unprojected_keys": 0`, and `last_rebuilt_at` is the time of Step 3. `unembedded_chunks` should be 0 for every swept domain.

- [ ] **Step 5: Restart what you use**

From inside the vault, start `artmind serve` (and `admin-ui` if you use it) again, because `just dev-install` stopped them.

---

## Out of scope / follow-ups noticed while planning

- `sameas approve` (`artmind/sameas.py:159`) and snapshot import (`artmind/graph_snapshot.py:534`) still call the single-transaction `projection.full_rebuild(tx, …)`. The snapshot import rebuilds every domain and can hit the same memory limit on a large graph. Both should move to `full_rebuild_batched` in a separate change. It is left out here because the spec names only `rebuild_projection` and the worker, and both callers have test suites built around the in-transaction seam.
- `import_structured_text` (the `vault sync` restore) does not record table fingerprints. A table restored by a domain-scoped sync, which does not advance the bookmark, therefore still reads "behind" until the next full sync. That matches the behaviour before this plan.

---

## Self-review against the spec (§6)

| Item | Covered by | Notes |
|---|---|---|
| **B1** batched full rebuild | Tasks 1, 2, 3 | `full_rebuild_batched` uses `batch_keys` / `rebuild_in_batches` (now in `projection`; `table2graph` keeps its names). `record_rebuild` runs in its own final transaction, only after the last batch, only when unscoped. `rebuild_projection` uses it, so the worker and `ingest sync` both get it. Tests assert transactions in order, batch sizes ≤ the limit, `:ProjectionState` last and absent after a failed batch, and the same-as group kept whole. Task 3 is the disposable-container before/after check (old path hits the memory limit, new path passes); it never touches `neo4j-my_second_brain`. Deletions stay correct: `all_keys` includes every Entity key, so a zero-observation entity is GC'd in its batch. |
| **B2** job remembers it owes a rebuild | Tasks 4, 5, 6 | New `finalize_domains` / `finalize_state` / `finalize_error` columns via the `_init_db` migration path. Domains are appended per deferring file; finalize runs from the row (0 queued files works); exceptions are caught per domain. A dead-connection sweep raises `SweepSkipped` under `strict=True` and becomes `sweep_errors`, which counts as `failed`; non-strict callers (vault_sync, table2graph, update, lifecycle, graph_snapshot, commit_to_graph) are unchanged. `finalize` is on job-status, jobs-active and job-results. Tests: resumed 0-file job rebuilds; failing rebuild → failed + error; failing sweep → failed. |
| **B3** status counts what is missing | Task 7 | `unembedded_entities` (sweep's predicate), `unprojected_keys` (no `AGGREGATES` — correct under same-as merges, where matching Entity keys would not be), `unembedded_chunks`, `domains` breakdown, plus a `drift` flag the final task checks. Three grouped reads, asserted on the Cypher sent. The live check is in Task 15. |
| **B4** `projection rebuild --sweep` | Task 8 | Full unscoped batched rebuild, then both strict sweeps per domain; `domains_swept`; exclusive with `--domain`. It also resolves failed job finalizes so the checklist's "last job's rebuild failed" row can clear. Test asserts both sweeps per domain with the right keys. |
| **B5** synthesize count | Task 9 (measure), Task 12 (fixture) | No new CLI by default: the dry run is one indexed read and no LLM call. `--countOnly` code is given, gated on a measured threshold. |
| **B6** own tables not "to apply" | Task 10 | Failing test first, with an explicit drop rule if it passes. A machine-local fingerprint (CSV blob id + meta digest) in `state.json`, written by `export_structured_text`, filters `_store_pending` (structured) and `_graph_pending` (`:1726`). Behind-ness from the other machine (rows or curation) is still reported. `_behind` keeps its fixtures unchanged. |
| **B7** admin `?prompt=` deep link | Task 11 | Server-rendered, autoescaped, capped prefill; admin console only; app.js enables Send without sending. Hermetic tests plus a browser check in the `admin-ui` preview. |
| **B8** plugin fixtures | Task 12 | New fixtures: job-status finishing / finalize-failed, projection-status ready / needs-rebuild, projection-rebuild.sweep, projection-synthesize.dry-run, db-review. Existing job fixtures regain `finalize`. Regenerated via `ARTMIND_REGEN_PLUGIN_FIXTURES=1`; `just obsidian-plugin-test`. |
| **B9** docs | Task 13 | INSTALL.md (heap, page cache, `db.memory.transaction.total.max`, colima ≥ 4 GiB), projection-pipeline.md §4 (batched rebuild, `--sweep`, `finalize`), both skills, CLI help for the group / rebuild / status, justfile recipes; `just dev-cli-help`. |
| **Repairing the live graph** | Task 15 | Manual, after B1–B4: `just dev-install` (stops daemons), VM memory check, `ARTMIND_NO_PROXY=1 artmind projection rebuild --sweep --compact`, then `projection status` expecting `unembedded_entities: 0`, `unprojected_keys: 0`, `drift: false`. |

Placeholder scan: no TBD; every code step shows its code. Every function used either exists (verified: `all_keys`, `rebuild`, `record_rebuild`, `load_synthesis_batch`, `schema_set_hash`, `same_as.load_groups` / `content_hash`, `_oids_at`, `_blobs_at`, `VaultLayout` / `read_state` / `write_state`, `_get_db`, `_retry_job`, `kg_work_was_done`, `embed_missing_entity_embeddings`, `embed_sweep.embed_missing_chunk_embeddings`, `_table_registered_and_mapped`, `_jobs`, `_hold_worker_lock`) or is defined by an earlier task (`rebuild_in_batches`, `full_rebuild_batched`, `SweepSkipped`, `_sweep_domain`, `_add_finalize_domain`, `_get_finalize`, `_set_finalize_state`, `_resolve_failed_finalizes`, `rebuild_and_sweep_all`, `TABLE_FINGERPRINTS_KEY`, `PREFILL_MAX_CHARS`).
