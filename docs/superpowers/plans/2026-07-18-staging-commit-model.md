# Staging → Commit Model for KG Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the staged KG-JSON folder a first-class waypoint that all three ingestion sources converge on, and make a single complete `commit_to_graph` step the only path from staged JSON into Neo4j.

**Architecture:** Today `ingest_to_kg` does extract → write → temporal in one blocking call, while pull-from-repo stages without writing and bundle-import writes without temporal — three inconsistent behaviours. This plan extracts a complete commit primitive `commit_to_graph(doc_kg_dir, domain)` that runs `write_to_graph` (nodes + relationships + entity embeddings) then the two *self-asserted-truth* hooks — temporal normalization and per-document supersession — so a committed document is temporally correct regardless of which source produced it. `ingest` gains an opt-in `--stage-only` flag that stops at the waypoint; the default stays end-to-end so no existing contract breaks. The admin dashboard gets the missing commit endpoint and surfaces staged-vs-committed state. Cross-document *judgment* operations (entity merge, conflict detection, description consolidation) deliberately stay out of commit — they require the settled whole-domain graph and are gated by the existing dry-run/apply review workflow in `refine_pipeline`.

**Tech Stack:** Python 3.14 (managed with `uv`), Click CLI, FastAPI + Jinja2 (admin dashboard), SQLite (job store, `artmind/db.py`), Neo4j (graph), pytest with `CliRunner`/`TestClient` and `monkeypatch` (hermetic — no Neo4j or network in tests).

---

## Background the implementer needs

Read `CLAUDE.md` at the repo root first — especially "Installed, not run from the checkout" and "Testing implications". Key facts:

- `artmind` is installed globally via `just dev-install` (editable). Python edits are live, but a running `serve` daemon serves stale code. Tests bypass all of this.
- Run the suite with `just dev-test` (which is `uv run --group dev pytest test/ -v`). Tests live in `test/` (singular). They import modules directly and mock externals; **no Neo4j is available in tests**.
- The staged KG-JSON folder for one document is `KG_DIR/<domain>/<doc_stem>/` and contains `document.json`, `entities.json`, `properties.json`, `relationships.json`. `KG_DIR` comes from the root-level `paths` module.
- After changing any skill or CLI help, the group docstring, the skill in `artmind/skills/`, and the `justfile` recipe are updated together (per CLAUDE.md "Docs and code drift"). This plan touches CLI help; update the `ingest` skill if one references `sync`/`async` flags.

### The fold-in boundary (the design rule this plan encodes)

`artmind/refine_pipeline.py`'s module docstring is the authority. It lists all refinement steps in dependency order and splits them by how they may run:

- **Fold into commit** (deterministic, additive, idempotent, per-document self-truth): `time` (temporal normalization — already auto-chained today) and `supersession` (a document's own "## Supersession Notice"). Embeddings already run inside `write_to_graph`.
- **NEVER fold into commit** (cross-document/cross-domain LLM judgment, gated by dry-run/apply): `merge` (`refine_graph`), `conflicts` (`detect_conflicts`), `consolidate` (`consolidate_descriptions`). These stay in `refine_pipeline`, invoked explicitly.

`test/test_ingest_hooks.py` already enforces half of this rule (asserts `ingest_to_kg` calls `normalize_ingested_document` but not `refine_graph`/`detect_conflicts`). This plan moves the temporal call into `commit_to_graph`, so that test must be updated (Task 3) — do not just delete its assertions; relocate them to target `commit_to_graph`.

### Current call sites (all verified against the tree)

| Path | Function | File:line | Writes to graph today? | Temporal today? |
|---|---|---|---|---|
| Sync ingest | `ingest_to_kg` | `artmind/ingest.py:1151` | yes | yes |
| Async ingest (worker) | `ingest_to_kg` | `artmind/worker.py:96` | yes | yes |
| Bundle import | `write_to_graph` | `artmind/webui/dashboard_routes.py:261` | yes | **no** |
| Pull from repo | `pull_kg` (stages only) | `artmind/webui/dashboard_routes.py:269` | **no** | no |
| CLI write-to-graph | `write_to_graph` | `artmind/cli.py:643, 684` | yes | **no** |

After this plan, every "→ graph" arrow routes through `commit_to_graph`, closing the temporal-skip gap on bundle-import and CLI, and giving pull a commit path it never had.

---

## File Structure

- `artmind/temporal.py` — add `only_doc_name` scoping param to `detect_supersession`; no new file (supersession logic already lives here).
- `artmind/ingest.py` — add `commit_to_graph`; refactor `ingest_to_kg` to `extract_kg` + `commit_to_graph` with a `stage_only` param.
- `artmind/worker.py` — read `stage_only` from the job and pass it through.
- `artmind/jobs.py` — `_create_job` accepts and persists `stage_only`.
- `artmind/db.py` — migration adding the `stage_only` column.
- `artmind/cli.py` — `--stage-only` on `ingest sync` and `ingest async`; switch `ingest write-to-graph` to `commit_to_graph`.
- `artmind/webui/dashboard_routes.py` — switch bundle-import to `commit_to_graph`; add `POST /api/artifacts/{domain}/{doc}/write-to-graph`; add `committed` state to the artifacts listing.
- `artmind/webui/static/dashboard.js` + `templates/dashboard.html` + `static/dashboard.css` — Browse-as-staging state badge and a "Write to graph" action.
- `test/test_supersession.py`, `test/test_ingest_hooks.py`, `test/test_webui_admin_api.py` — new/updated tests.

---

## Task 1: Scope `detect_supersession` to a single document

Add an optional `only_doc_name` parameter so the same, already-tested resolution logic can be reused per-document at commit time (DRY — do not duplicate the version-resolution loop). When set, the function still builds its version map from **all** documents in the domain (needed to resolve the older side) but only *applies* the notice for the named document.

**Files:**
- Modify: `artmind/temporal.py` (function `detect_supersession`, currently starts near line where `def detect_supersession(domain: str, dry_run: bool = False) -> dict:` appears)
- Test: `test/test_supersession.py`

- [ ] **Step 1: Read the current function**

Open `artmind/temporal.py` and locate `detect_supersession`. Its body builds `by_version` from all domain docs, then loops `for d in docs:` parsing each doc's notice and calling `apply_supersession`. You will add a filter inside that loop.

- [ ] **Step 2: Write the failing test**

Add to `test/test_supersession.py`:

```python
def test_detect_supersession_only_doc_name_filters_application(monkeypatch):
    """only_doc_name applies just that doc's notice; version map still sees all docs."""
    import artmind.temporal as temporal

    docs = [
        {"id": "docA", "name": "v3.md", "version": "3.0"},
        {"id": "docB", "name": "v2.md", "version": "2.0"},
    ]

    class _Result:
        def data(self):
            return docs

    class FakeSession:
        def run(self, *a, **k):
            return _Result()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    bodies = {"v3.md": "v3 notice body", "v2.md": "v2 plain body"}
    applied = []

    monkeypatch.setattr(temporal, "neo4j_session", lambda: FakeSession())
    monkeypatch.setattr(temporal, "_read_doc_body", lambda name: bodies[name], raising=False)
    # Only the v3 body carries a supersedes notice.
    monkeypatch.setattr(
        temporal, "parse_supersession_notice",
        lambda body: {"superseded_version": "2.0", "effective": "2026-01-01"} if "notice" in body else None,
    )
    monkeypatch.setattr(
        temporal, "apply_supersession",
        lambda newer, older, scope, eff, detected_by: applied.append((newer, older)),
    )

    # Scoped to v2.md (which has no notice): nothing applies, even though v3.md's
    # notice exists — proving the APPLY loop is filtered, not the version map build.
    result = temporal.detect_supersession("d", only_doc_name="v2.md")
    assert applied == []
    assert result["applied"] == []

    # Scoped to v3.md: its notice resolves against docB and applies.
    applied.clear()
    result = temporal.detect_supersession("d", only_doc_name="v3.md")
    assert applied == [("docA", "docB")]
```

Note: `_read_doc_body(name)` is the single markdown-read seam this test patches (`raising=False` because it does not exist until Step 3). Step 3 both introduces that helper and adds the `only_doc_name` filter, so before Step 3 this test fails on the unexpected `only_doc_name` keyword — the correct failure.

- [ ] **Step 3: Add the parameter and the read helper**

In `artmind/temporal.py`, add near the other private helpers:

```python
def _read_doc_body(name: str) -> str | None:
    """Return the markdown body for a registered document, or None if absent."""
    md_file = MARKDOWNS_DIR / f"{Path(name).stem}.md"
    if not md_file.exists():
        return None
    from artmind.ingest import _parse_md_frontmatter
    _, body = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
    return body
```

Change the signature to:

```python
def detect_supersession(domain: str, dry_run: bool = False, only_doc_name: str | None = None) -> dict:
```

Inside the `for d in docs:` loop, replace the markdown-read block and add the filter as the first statement in the loop:

```python
    for d in docs:
        if only_doc_name is not None and d["name"] != only_doc_name:
            continue
        body = _read_doc_body(d["name"])
        if body is None:
            continue
        notice = parse_supersession_notice(body)
        if not notice:
            continue
        older = by_version.get(notice["superseded_version"])
        if not older or older["id"] == d["id"]:
            continue
        report["applied"].append({"newer": d["id"], "older": older["id"], "effective": notice["effective"]})
        if not dry_run:
            apply_supersession(d["id"], older["id"], "document", notice["effective"], detected_by="notice")
    return report
```

- [ ] **Step 4: Run the test**

Run: `just dev-test` (or `uv run --group dev pytest test/test_supersession.py -v`)
Expected: the new test passes; all previously-passing supersession tests still pass (the `only_doc_name=None` default preserves old behaviour).

- [ ] **Step 5: Commit**

```bash
git add artmind/temporal.py test/test_supersession.py
git commit -m "feat(temporal): scope detect_supersession to a single document

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Add the `commit_to_graph` primitive

The single complete commit: write nodes/rels/embeddings, then run the two self-truth hooks. Both hooks are best-effort (a down temporal/supersession path must not fail the write, matching how `ingest_to_kg` already guards the temporal hook today).

**Files:**
- Modify: `artmind/ingest.py` (add function near `write_to_graph`, which is at `artmind/ingest.py:1452`)
- Test: `test/test_ingest_hooks.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_ingest_hooks.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --group dev pytest test/test_ingest_hooks.py::test_commit_to_graph_runs_write_then_temporal_then_supersession -v`
Expected: FAIL with `AttributeError: module 'artmind.ingest' has no attribute 'commit_to_graph'`.

- [ ] **Step 3: Implement `commit_to_graph`**

In `artmind/ingest.py`, immediately after `write_to_graph` (line 1452-1454), add:

```python
def commit_to_graph(doc_kg_dir: Path, domain: str) -> bool:
    """Complete commit of staged KG JSON to Neo4j: write, then the per-document
    self-asserted-truth hooks (temporal normalization, then supersession).

    This is the single convergence point for all three ingestion sources
    (extract, pull-from-repo, import-bundle). Cross-document judgment steps
    (merge/conflicts/consolidate) are deliberately NOT run here — see
    artmind.refine_pipeline. Hooks are best-effort: a down hook logs a warning
    but does not fail the commit, since the graph write already succeeded.
    """
    import json

    ok = write_to_graph(doc_kg_dir)
    if not ok:
        return False

    # 1. Temporal normalization (additive, idempotent, per-document).
    try:
        from artmind.temporal import normalize_ingested_document
        normalize_ingested_document(doc_kg_dir, domain)
    except Exception as e:
        logger.warning("commit_to_graph: temporal hook failed for {}: {}", doc_kg_dir, e)

    # 2. Supersession from this document's own notice (must follow temporal so
    #    canonical dates/version exist). Scoped to just this document.
    try:
        from artmind.temporal import detect_supersession
        document = json.loads((doc_kg_dir / "document.json").read_text(encoding="utf-8"))
        detect_supersession(domain, only_doc_name=document.get("name"))
    except Exception as e:
        logger.warning("commit_to_graph: supersession hook failed for {}: {}", doc_kg_dir, e)

    return True
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_ingest_hooks.py -v`
Expected: both new `commit_to_graph` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_ingest_hooks.py
git commit -m "feat(ingest): add commit_to_graph, the complete staged-JSON commit primitive

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Refactor `ingest_to_kg` to use `commit_to_graph` + add `stage_only`

`ingest_to_kg` becomes `extract_kg` followed by `commit_to_graph`, with a new `stage_only` flag that stops at the waypoint. The inline temporal call moves into `commit_to_graph` (done in Task 2), so it is removed here. Default behaviour is unchanged.

**Files:**
- Modify: `artmind/ingest.py` (`ingest_to_kg`, lines 1151-1191)
- Test: `test/test_ingest_hooks.py`

- [ ] **Step 1: Update the existing hook test to target `commit_to_graph`**

The existing `test_ingest_to_kg_calls_normalize_after_write` asserts `ingest_to_kg`'s source contains `normalize_ingested_document`. After this refactor that call lives in `commit_to_graph`, not `ingest_to_kg`. Replace that test with:

```python
def test_ingest_to_kg_commits_via_commit_to_graph():
    import inspect
    import artmind.ingest as ing
    src = inspect.getsource(ing.ingest_to_kg)
    assert "extract_kg" in src
    assert "commit_to_graph" in src
    assert src.index("extract_kg") < src.index("commit_to_graph")


def test_ingest_to_kg_still_does_not_call_refine_or_detect():
    import inspect
    import artmind.ingest as ing
    src = inspect.getsource(ing.ingest_to_kg) + inspect.getsource(ing.commit_to_graph)
    assert "refine_graph" not in src
    assert "detect_conflicts" not in src
```

Keep the existing `test_ingest_sync_and_async_do_not_auto_detect_conflicts` test as-is. Remove the now-obsolete `test_ingest_to_kg_calls_normalize_after_write` and `test_ingest_to_kg_does_not_call_refine_or_detect`.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run --group dev pytest test/test_ingest_hooks.py::test_ingest_to_kg_commits_via_commit_to_graph -v`
Expected: FAIL (`ingest_to_kg` source does not yet contain `commit_to_graph`).

- [ ] **Step 3: Refactor `ingest_to_kg`**

Replace the tail of `ingest_to_kg` (from the `doc_kg_dir = extract_kg(...)` line at 1178 through the `return ok` at 1191). Add `stage_only: bool = False` to the signature:

```python
def ingest_to_kg(
    file_result: dict,
    domain: str,
    text_model: str = "ministral-3:14b",
    embed_model: str = "nomic-embed-text:latest",
    chunk_size: int = 6000,
    stage_only: bool = False,
) -> bool:
    """Orchestrate KG extraction and (unless stage_only) commit for one document."""
```

Replace the extract/write/temporal tail with:

```python
    doc_kg_dir = extract_kg(file_result, domain, text_model, embed_model)
    if doc_kg_dir is None:
        return False
    if stage_only:
        logger.info("Staged (not committed): {}", doc_kg_dir)
        return True
    return commit_to_graph(doc_kg_dir, domain)
```

(The old inline `normalize_ingested_document` block is deleted — it now lives in `commit_to_graph`.)

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_ingest_hooks.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_ingest_hooks.py
git commit -m "refactor(ingest): ingest_to_kg = extract + commit_to_graph, add stage_only

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Route bundle-import and CLI write-to-graph through `commit_to_graph`

Close the temporal-skip gap: both paths currently call raw `write_to_graph` and never normalize. Switch them to `commit_to_graph` so imported/re-written docs get identical treatment to ingested ones.

**Files:**
- Modify: `artmind/webui/dashboard_routes.py:22` (import) and `:261` (bundle import call)
- Modify: `artmind/cli.py:26` (import), `:643` and `:684` (write calls)
- Test: `test/test_webui_admin_api.py`

- [ ] **Step 1: Write the failing test for bundle import**

Add to `test/test_webui_admin_api.py`:

```python
def test_bundle_import_uses_commit_to_graph(monkeypatch, tmp_path):
    import io, zipfile, json
    from artmind.webui import dashboard_routes

    seen = {}
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)

    def fake_commit(doc_kg_dir, domain):
        seen["domain"] = domain
        return True

    monkeypatch.setattr(dashboard_routes, "commit_to_graph", fake_commit, raising=False)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("document.json", json.dumps({"id": "d1", "name": "f.md"}))
    buf.seek(0)

    resp = _client().post(
        "/api/artifacts/import",
        data={"domain": "mydomain", "doc": "f"},
        files={"file": ("bundle.zip", buf, "application/zip")},
    )
    assert resp.status_code == 200
    assert seen["domain"] == "mydomain"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --group dev pytest test/test_webui_admin_api.py::test_bundle_import_uses_commit_to_graph -v`
Expected: FAIL — `dashboard_routes` imports `write_to_graph`, not `commit_to_graph`.

- [ ] **Step 3: Swap the imports and calls**

In `artmind/webui/dashboard_routes.py:22`, change the import line:

```python
from artmind.ingest import _build_file_result_from_db, commit_to_graph, embed_entities_backfill, extract_kg
```

(remove `write_to_graph` from this import if nothing else in the file uses it — grep to confirm; `api_artifact_import` at line 261 is the only user.)

At line 261, replace:

```python
        ok = await asyncio.to_thread(write_to_graph, dest_dir)
```

with:

```python
        ok = await asyncio.to_thread(commit_to_graph, dest_dir, domain)
```

In `artmind/cli.py:26`, add `commit_to_graph` to the `from artmind.ingest import (...)` block. At lines 643 and 684, replace `write_to_graph(doc_kg_dir)` with `commit_to_graph(doc_kg_dir, domain)` (single mode) and `commit_to_graph(doc_kg_dir, resolved_domain)` (batch mode — the batch loop variable is `resolved_domain`; confirm at cli.py:672).

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_webui_admin_api.py -v`
Expected: PASS. Also run `uv run --group dev pytest test/test_pull_kg_cli.py test/test_kg_pull.py -v` to confirm nothing regressed.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/dashboard_routes.py artmind/cli.py test/test_webui_admin_api.py
git commit -m "fix(commit): route bundle-import and CLI write-to-graph through commit_to_graph

Closes the gap where imported/re-written docs skipped temporal normalization.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Persist and thread `stage_only` through the async job path

So `--stage-only` works from `ingest async` and (later) the dashboard, the worker must honour it. Add a `stage_only` column with a migration, accept it in `_create_job`, and read it in the worker.

**Files:**
- Modify: `artmind/db.py` (migration block near line 89-91)
- Modify: `artmind/jobs.py` (`_create_job`, lines 8-17)
- Modify: `artmind/worker.py` (`_process_job` signature line 70, `ingest_to_kg` call line 96, `_worker_loop` SELECT at 144 and `_process_job` call at 151)
- Test: `test/test_ingest_hooks.py` (structural) — a full worker integration test needs Neo4j, so assert threading structurally.

- [ ] **Step 1: Write the failing migration + create_job test**

Add a new file `test/test_jobs_stage_only.py`:

```python
def test_create_job_persists_stage_only(tmp_path, monkeypatch):
    import sqlite3
    import artmind.db as db
    import artmind.jobs as jobs

    dbfile = tmp_path / "reg.db"
    monkeypatch.setattr(db, "DB_PATH", dbfile)

    job_id = jobs._create_job(["/a.pdf"], domain="d", stage_only=True)

    conn = sqlite3.connect(dbfile)
    row = conn.execute("SELECT stage_only FROM ingestion_jobs WHERE job_id=?", (job_id,)).fetchone()
    assert row[0] == 1
```

Note: first confirm how `jobs._create_job` obtains its connection. If it calls `db._get_db()` (which reads `db.DB_PATH`), the single patch above is enough. If it does `sqlite3.connect(DB_PATH)` against a `DB_PATH` imported into `artmind.jobs`, also `monkeypatch.setattr(jobs, "DB_PATH", dbfile)`. Patch whatever the real code reads so the write lands in `tmp_path`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --group dev pytest test/test_jobs_stage_only.py -v`
Expected: FAIL — no `stage_only` column / parameter.

- [ ] **Step 3: Add the migration**

In `artmind/db.py`, in the migration block (after the `force` migration at lines 89-91), add:

```python
    if "stage_only" not in existing:
        cursor.execute("ALTER TABLE ingestion_jobs ADD COLUMN stage_only INTEGER DEFAULT 0")
```

(`existing` is the set already computed for the `ingestion_jobs` PRAGMA at line 89 — reuse it.) Also add `stage_only INTEGER DEFAULT 0` to the `CREATE TABLE IF NOT EXISTS ingestion_jobs` DDL at lines 23-35 so fresh databases get it too.

- [ ] **Step 4: Thread through `_create_job`**

In `artmind/jobs.py:8-17`, change the signature and INSERT:

```python
def _create_job(batch_files: list[str], domain: str = "general", force: bool = False, stage_only: bool = False) -> str:
    """Create a new ingestion job with per-file rows; return job_id."""
```

Update the INSERT column list and values to include `stage_only` / `int(stage_only)` (mirror exactly how `force` / `int(force)` are handled in the same statement).

- [ ] **Step 5: Thread through the worker**

In `artmind/worker.py`:
- `_process_job` signature (line 70): add `stage_only: bool = False`.
- The `ingest_to_kg(...)` call (line 96): add `stage_only=stage_only` as the trailing argument.
- `_worker_loop` SELECT (line 144): change to `SELECT job_id, domain, force, stage_only FROM ingestion_jobs`.
- `_process_job` call (line 151): pass `stage_only=bool(row[3])`.

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_jobs_stage_only.py -v`
Expected: PASS. Run the full suite `just dev-test` to confirm no regressions in job/worker tests.

- [ ] **Step 7: Commit**

```bash
git add artmind/db.py artmind/jobs.py artmind/worker.py test/test_jobs_stage_only.py
git commit -m "feat(jobs): persist and thread stage_only through the async worker

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Add `--stage-only` to `ingest sync` and `ingest async`

Expose the waypoint at the CLI. Default off (end-to-end unchanged).

**Files:**
- Modify: `artmind/cli.py` (`ingest_sync` at 419-470, `ingest_async` at 473-508)
- Test: `test/test_ingest_sync_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_ingest_sync_cli.py`:

```python
def test_ingest_sync_stage_only_passes_flag(monkeypatch, tmp_path):
    from click.testing import CliRunner
    import artmind.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "ingest_file", lambda *a, **k: {"status": "ok"})
    def fake_kg(result, domain, tm, em, cs, stage_only=False):
        seen["stage_only"] = stage_only
        return True
    monkeypatch.setattr(cli, "ingest_to_kg", fake_kg)
    monkeypatch.setattr(cli, "load_env", lambda: {})
    monkeypatch.setattr(cli, "resolve_llm_model", lambda env: "m")

    f = tmp_path / "a.txt"
    f.write_text("x")
    result = CliRunner().invoke(cli.ingest_sync, [str(f), "--domain", "d", "--stage-only"])
    assert result.exit_code == 0
    assert seen["stage_only"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --group dev pytest test/test_ingest_sync_cli.py::test_ingest_sync_stage_only_passes_flag -v`
Expected: FAIL — no such option.

- [ ] **Step 3: Add the option to `ingest sync`**

In `artmind/cli.py`, add after the `--force` option on `ingest_sync` (line 422):

```python
@click.option("--stage-only", is_flag=True, help="Extract KG JSON but do not write to the graph (leaves it staged for a later commit)")
```

Update the signature to `def ingest_sync(file_path: str, domain: str | None, force: bool, stage_only: bool):` and the `ingest_to_kg(...)` call at line 458 to pass `stage_only=stage_only`.

- [ ] **Step 4: Add the option to `ingest async`**

Add the identical `--stage-only` option after `--force` on `ingest_async` (line 476). Update the signature to include `stage_only: bool` and the `_create_job(...)` call at line 496 to pass `stage_only=stage_only`.

- [ ] **Step 5: Run the tests + verify help**

Run: `uv run --group dev pytest test/test_ingest_sync_cli.py -v`
Expected: PASS.
Run: `ARTMIND_NO_PROXY=1 artmind ingest sync --help` and confirm `--stage-only` appears. (Per CLAUDE.md, update the `ingest` skill in `artmind/skills/` and any `justfile` recipe that documents these flags, then `artmind init` to reseed.)

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py test/test_ingest_sync_cli.py artmind/skills/
git commit -m "feat(cli): add --stage-only to ingest sync/async

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Add the dashboard commit endpoint

The missing action from last design pass: a pulled (or stage-only-ingested) artifact has no way to reach the graph from the UI. Add `POST /api/artifacts/{domain}/{doc}/write-to-graph`.

**Files:**
- Modify: `artmind/webui/dashboard_routes.py` (add route near the other `/api/artifacts` routes, ~line 264)
- Test: `test/test_webui_admin_api.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_webui_admin_api.py`:

```python
def test_commit_artifact_endpoint(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes

    domain_dir = tmp_path / "d" / "mydoc"
    domain_dir.mkdir(parents=True)
    (domain_dir / "document.json").write_text('{"id":"d1","name":"f.md"}', encoding="utf-8")
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)

    called = {}
    monkeypatch.setattr(dashboard_routes, "commit_to_graph",
                        lambda p, dom: called.setdefault("dom", dom) or True, raising=False)

    resp = _client().post("/api/artifacts/d/mydoc/write-to-graph")
    assert resp.status_code == 200
    assert resp.json() == {"domain": "d", "doc": "mydoc", "written": True}
    assert called["dom"] == "d"


def test_commit_artifact_missing_returns_404(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    resp = _client().post("/api/artifacts/d/nope/write-to-graph")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --group dev pytest test/test_webui_admin_api.py::test_commit_artifact_endpoint -v`
Expected: FAIL (404 route missing).

- [ ] **Step 3: Add the route**

In `artmind/webui/dashboard_routes.py`, after `api_artifact_import` (ends line 264), add:

```python
    @app.post("/api/artifacts/{domain}/{doc}/write-to-graph")
    async def api_artifact_commit(domain: str, doc: str):
        doc_dir = KG_DIR / domain / doc
        if not (doc_dir / "document.json").exists():
            raise HTTPException(status_code=404, detail=f"Staged KG not found: {domain}/{doc}")
        ok = await asyncio.to_thread(commit_to_graph, doc_dir, domain)
        if not ok:
            raise HTTPException(status_code=400, detail="commit_to_graph failed — check logs for Neo4j errors")
        return {"domain": domain, "doc": doc, "written": True}
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_webui_admin_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/dashboard_routes.py test/test_webui_admin_api.py
git commit -m "feat(admin): add POST /api/artifacts/{domain}/{doc}/write-to-graph

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Surface staged-vs-committed state in the Browse UI

`api_artifacts` already returns `inGraph` per doc (`dashboard_routes.py:225`). Use it: show a state badge and a "Write to graph" button on staged-but-not-committed docs, wired to the Task 7 endpoint. This is the visible half of "Browse becomes the staging area."

**Files:**
- Modify: `artmind/webui/static/dashboard.js` (`refreshArtifacts`, lines 279-301)
- Modify: `artmind/webui/static/dashboard.css` (add badge/button styles)
- Test: manual (UI); route already covered by Task 7. No unit test framework for the vanilla JS in this repo.

- [ ] **Step 1: Update `refreshArtifacts` to render state + commit action**

In `artmind/webui/static/dashboard.js`, replace the card-building loop inside `refreshArtifacts` (lines 289-300) with:

```javascript
  for (const a of artifacts) {
    const card = el("div", "job-card");
    const head = el("div", "job-card-head", a.name);
    head.appendChild(el("span", a.inGraph ? "state-badge in-graph" : "state-badge staged",
                        a.inGraph ? "in graph" : "staged"));
    card.appendChild(head);
    card.appendChild(el("div", "dash-note",
      `${a.entityCount} entities · ${a.propertyCount} properties · ${a.relationshipCount} relationships`));

    if (!a.inGraph) {
      const commitBtn = el("button", "btn-link", "Write to graph");
      commitBtn.addEventListener("click", async () => {
        commitBtn.disabled = true;
        commitBtn.textContent = "Writing…";
        try {
          await api(`/api/artifacts/${encodeURIComponent(domain)}/${encodeURIComponent(a.doc)}/write-to-graph`,
                    { method: "POST" });
          refreshArtifacts();
        } catch (err) {
          alert(`Write to graph failed: ${err.message}`);
          commitBtn.disabled = false;
          commitBtn.textContent = "Write to graph";
        }
      });
      card.appendChild(commitBtn);
    }

    const exportLink = el("a", "btn-link", "Export bundle");
    exportLink.href = `/api/artifacts/${encodeURIComponent(domain)}/${encodeURIComponent(a.doc)}/bundle`;
    card.appendChild(exportLink);
    container.appendChild(card);
  }
```

- [ ] **Step 2: Add badge styles**

In `artmind/webui/static/dashboard.css`, add near the `.in-graph-badge` rule (lines 71-75):

```css
.state-badge {
  margin-left: 8px; padding: 1px 6px; border-radius: 6px;
  font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;
}
.state-badge.in-graph { background: var(--bg-inset); color: #5da571; }
.state-badge.staged { background: var(--bg-inset); color: var(--text-muted); }
```

(You can remove the now-superseded `.in-graph-badge` rule and its use, since `refreshArtifacts` no longer emits `in-graph-badge`. Grep first to confirm no other emitter.)

- [ ] **Step 3: Reseed and verify in a live admin UI**

Run: `just dev-install` (reseeds the run folder so the admin UI serves the edited static assets), then start the admin UI and open `/dashboard`. Confirm: a pulled/stage-only doc shows a grey "staged" badge and a "Write to graph" button; clicking it flips the badge to green "in graph" and the button disappears. (Requires a live Neo4j — the user starts Neo4j manually; check with `nc -z localhost 7687` first and ask if it is not up.)

- [ ] **Step 4: Commit**

```bash
git add artmind/webui/static/dashboard.js artmind/webui/static/dashboard.css
git commit -m "feat(admin-ui): show staged/in-graph state and a Write-to-graph action in Browse

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Run the full suite:** `just dev-test` — expect all green (the ~421 existing tests plus the new ones).
- [ ] **End-to-end, out of process (per CLAUDE.md — a running daemon serves stale code):** with Neo4j up,
  ```bash
  just dev-stop-daemons
  ARTMIND_NO_PROXY=1 artmind ingest sync <somefile> --domain <d> --stage-only
  ```
  Confirm the doc appears under Browse as **staged** (not in graph), then POST the commit endpoint (or click "Write to graph") and confirm it flips to **in graph** and entities carry canonical temporal properties.
- [ ] **Idempotency check (the design's load-bearing assumption):** commit the same staged doc twice via the endpoint. Confirm no duplicate nodes/relationships (MERGE semantics) and that `description_raw` / canonical temporal props are unchanged on the second commit. If re-commit is NOT clean, stop and reconsider before shipping — the whole model assumes commit is safely repeatable.

---

## Follow-up (separate spec — do NOT implement in this plan)

**Bring the "Refine steps" into the Admin UI dashboard.**

Refinement (`artmind/refine_pipeline.py`: merge → conflicts → consolidate, plus the standalone `normalize-time` / `detect-supersession` backfills) is today CLI-only and deliberately excluded from commit because it is cross-document/cross-domain LLM judgment gated by a **dry-run → review-proposal-file → apply** workflow. A dashboard surface for it is genuinely useful but is its own design problem, because the review gate — a human editing per-domain sub-proposal JSON files under `data/refine/pipeline/` before applying — has no obvious UI analogue yet.

Scope to explore in a future brainstorming/spec session:
- A "Refine" panel (its own zone in the admin dashboard, akin to the Maintenance/Snapshots zone) that runs the pipeline in **propose** mode and renders the resulting report (`{"domains", "per_domain", "cross_domain_conflicts"}`) as a reviewable diff — proposed merges, detected conflicts, description rewrites — with per-item accept/reject.
- How "edit the proposal before apply" maps to UI controls (inline toggles that rewrite the sub-proposal files, then `apply --from-file`).
- Whether refine should be a tracked/queued long-running operation (it makes many LLM calls) with progress like the ingest jobs, rather than a synchronous request.
- Domain/scope selection (single vs multi-domain, since conflict detection is cross-only for 2+ domains).
- Guardrails: merges and supersession retirement are destructive relative to snapshots — reuse the snapshot/guardrail pattern already in the dashboard.

This follow-up depends on nothing in the staging→commit work above except that both share the admin dashboard shell; it can be specced independently.
