# Extraction Loss Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed or emptied re-extraction can never replace a note's previous staged extraction, emptied notes can be listed, and the CLI, skills and `traces list` stop misleading operators about it.

**Architecture:** `--reextract` sets the chunk cache aside (to `LOGS_DIR`, never the committed staging folder) instead of deleting it; `extract_kg` refuses to write staging while any chunk step is still `failed`, or when a re-extraction of an unchanged note comes back empty over a non-empty result, and puts the cache back. A new read-only `ingest empty-extractions` command audits staging folders. Small CLI-message, skill and `traces list` fixes follow.

**Tech Stack:** Python 3.14, Click (rich-click), pytest with `monkeypatch` and `CliRunner`, sqlite3 registry, `uv`.

**Spec:** `docs/superpowers/specs/2026-10-18-extraction-loss-guard-design.md` — read it first.

**Ground rules for every task:**
- Tests live in `test/` (singular). Run targeted files, not the whole suite:
  `uv run --group dev pytest test/<file>.py -q`. (The full suite stalls on this machine; the final task runs a wider targeted set.)
- Synthetic text only in tests. Never copy vault content (note text, names, places) into code, tests or commits: the checkout is public.
- Assert on files, rows and the arguments actually passed — never on summary counts (CLAUDE.md, "A mocked graph session answers every query successfully").
- Commit after each task on the session's branch. End every commit message with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never run `artmind setup` or `artmind init` from the checkout, and never run an ingest against a real vault: this plan is code-only.

---

## File structure

| File | Change | Responsibility |
|---|---|---|
| `artmind/ingest.py` | modify | stash/restore of the extraction cache; the two write guards in `extract_kg` |
| `artmind/staging_audit.py` | create | `empty_extractions()`: read-only audit of staging folders |
| `artmind/cli.py` | modify | `ingest empty-extractions`; registry-miss hint for `extract-kg` / `write-to-graph`; `session_id` in `traces list`; `COMMAND_GROUPS` routing |
| `test/test_extraction_loss_guard.py` | create | guard + stash tests on a real temp vault with mocked LLM seams |
| `test/test_staging_audit.py` | create | audit function + CLI tests on synthetic staging folders |
| `test/test_registry_miss_hint.py` | create | hint appears only when a staging folder exists |
| `test/test_traces_cli.py` | modify | `traces list` rows carry `session_id` |
| `artmind/skills/artmind-ingestion-helper/SKILL.md` | modify | correct Situation D and the error table; mention the audit |
| `artmind/skills/artmind-query/SKILL.md` | modify | Answer Style: say when the graph lacks an answer found in a note |
| `CLAUDE.md` | modify | `--reextract` row: the guard; layout row for `staging_audit.py` |

---

### Task 1: Set the extraction cache aside instead of deleting it

**Files:**
- Modify: `artmind/ingest.py` (imports near line 50; `_reset_extraction_cache` at line ~161)
- Test: `test/test_extraction_loss_guard.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_extraction_loss_guard.py`:

```python
"""A re-extraction may never cost a note its previous extraction (spec
2026-10-18-extraction-loss-guard-design.md).

`--reextract` sets the chunk cache aside instead of deleting it, and
`extract_kg` refuses to replace staging while a chunk step is still failed, or
with nothing when an unchanged note had something. No Neo4j, no LLM: the
extraction seams are mocked as in test_vault_chunk_cache_merge.py, on a real
temp vault (`ingest_env`). Synthetic text only."""
import json
from pathlib import Path

import pytest

import artmind.ingest as ing
from conftest import stage_as_extracted

PRIOR_OBSERVATIONS = [{"id": "o1", "key": "alpha", "name": "Alpha"}]
PRIOR_RELATIONSHIPS = [{"source_id": "o1", "target_id": "c1", "rel_type": "EXTRACTED_FROM"}]


@pytest.fixture()
def stash_root(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    monkeypatch.setattr(ing, "LOGS_DIR", root)
    return root


def _rows(sha):
    conn = ing._get_db()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT doc_sha256, doc_id, chunk_seq, entities_status, properties_status,"
            " relationships_status, updated_at FROM kg_chunk_status WHERE doc_sha256 = ?"
            " ORDER BY chunk_seq", (sha,))]
    finally:
        conn.close()


def test_reset_moves_the_cache_aside_and_returns_a_stash(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text('{"raw_entities": ["kept"]}', encoding="utf-8")
    ing._init_chunk_rows("sha-1", "doc-1", 1)
    ing._update_chunk_step("sha-1", 1, "entities", "ok")
    before = _rows("sha-1")

    stash = ing._reset_extraction_cache(cache, "sha-1")

    assert not cache.exists()
    assert ing._get_chunk_statuses("sha-1") == {}
    assert stash["rows"] == before
    assert Path(stash["dir"]).parent == stash_root / "reextract-stash"
    assert json.loads((Path(stash["dir"]) / "chunk_001.json").read_text()) == {"raw_entities": ["kept"]}


def test_restore_puts_the_cache_and_rows_back_over_a_failed_run(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text('{"raw_entities": ["kept"]}', encoding="utf-8")
    ing._init_chunk_rows("sha-1", "doc-1", 1)
    ing._update_chunk_step("sha-1", 1, "entities", "ok")
    before = _rows("sha-1")
    stash = ing._reset_extraction_cache(cache, "sha-1")
    # what a failed re-extraction leaves behind
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text('{"raw_entities": []}', encoding="utf-8")
    ing._init_chunk_rows("sha-1", "doc-1", 1)
    ing._update_chunk_step("sha-1", 1, "entities", "failed")

    ing._restore_extraction_cache(stash, cache, "sha-1")

    assert json.loads((cache / "chunk_001.json").read_text()) == {"raw_entities": ["kept"]}
    assert _rows("sha-1") == before
    assert not Path(stash["dir"]).exists()


def test_restore_and_drop_accept_no_stash(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    cache.mkdir(parents=True)
    ing._restore_extraction_cache(None, cache, "sha-1")
    ing._drop_extraction_stash(None)
    assert cache.exists()


def test_drop_deletes_the_stash(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text("{}", encoding="utf-8")
    stash = ing._reset_extraction_cache(cache, "sha-1")

    ing._drop_extraction_stash(stash)

    assert not Path(stash["dir"]).exists()


def test_reset_of_a_missing_cache_stashes_only_rows(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    stash = ing._reset_extraction_cache(cache, "sha-1")
    assert stash == {"dir": None, "rows": []}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_extraction_loss_guard.py -q`
Expected: FAIL — `_reset_extraction_cache` returns `None` (`TypeError: 'NoneType' object is not subscriptable`), `_restore_extraction_cache` / `_drop_extraction_stash` do not exist, `ing.LOGS_DIR` does not exist.

- [ ] **Step 3: Implement**

In `artmind/ingest.py`, add `LOGS_DIR` to the `from paths import (...)` block (keep it alphabetical):

```python
from paths import (
    ARTMIND_VAULT_DIR,
    DOMAIN_SCHEMAS_DIR,
    KG_DIR,
    LOGS_DIR,
    MARKDOWNS_DIR,
    ORIGINALS_DIR,
    PROJECT_ROOT,
)
```

Replace `_reset_extraction_cache` (currently deletes the rows and `rmtree`s the directory) with these three functions:

```python
_CHUNK_STATUS_COLUMNS = (
    "doc_sha256, doc_id, chunk_seq, entities_status, properties_status,"
    " relationships_status, updated_at"
)


def _reset_extraction_cache(chunk_data_dir: Path, doc_sha256: str) -> dict:
    """Set a note's resumable extraction aside so `extract_kg` runs every LLM
    step again. Both caches are keyed by the hash of the (unchanged) file, so without
    this a requested re-extraction would resume as "all steps already ok" and
    re-read the old answers. The embedding sidecar is kept: a vector depends on
    the chunk text, not the schema.

    Moved, not deleted: returns the stash `_restore_extraction_cache` puts back
    when the re-extraction fails, so a failed `--reextract` never costs the
    previous answers (spec 2026-10-18-extraction-loss-guard-design.md §3.1).
    The stash lives under LOGS_DIR -- the staging folder is committed by
    Obsidian Git's timer and must never hold it."""
    conn = _get_db()
    try:
        rows = [tuple(r) for r in conn.execute(
            f"SELECT {_CHUNK_STATUS_COLUMNS} FROM kg_chunk_status WHERE doc_sha256 = ?"
            " ORDER BY chunk_seq",
            (doc_sha256,),
        )]
        conn.execute("DELETE FROM kg_chunk_status WHERE doc_sha256 = ?", (doc_sha256,))
        conn.commit()
    finally:
        conn.close()
    stashed: Path | None = None
    if chunk_data_dir.exists():
        stashed = LOGS_DIR / "reextract-stash" / f"{doc_sha256}-{uuid.uuid4().hex}"
        stashed.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(chunk_data_dir), str(stashed))
    return {"dir": stashed, "rows": rows}


def _restore_extraction_cache(stash: dict | None, chunk_data_dir: Path, doc_sha256: str) -> None:
    """Undo `_reset_extraction_cache` after a re-extraction that must not be
    staged: whatever the failed run wrote is replaced by the stashed cache and
    rows. No-op without a stash (no `--reextract`)."""
    if stash is None:
        return
    shutil.rmtree(chunk_data_dir, ignore_errors=True)
    if stash["dir"] is not None and Path(stash["dir"]).exists():
        chunk_data_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stash["dir"]), str(chunk_data_dir))
    conn = _get_db()
    try:
        conn.execute("DELETE FROM kg_chunk_status WHERE doc_sha256 = ?", (doc_sha256,))
        conn.executemany(
            f"INSERT INTO kg_chunk_status ({_CHUNK_STATUS_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            stash["rows"],
        )
        conn.commit()
    finally:
        conn.close()


def _drop_extraction_stash(stash: dict | None) -> None:
    """Delete the set-aside cache once the re-extraction is staged."""
    if stash is not None and stash["dir"] is not None:
        shutil.rmtree(stash["dir"], ignore_errors=True)
```

`shutil` and `uuid` are already imported at the top of `ingest.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_extraction_loss_guard.py test/test_ingest_reextract.py -q`
Expected: PASS. `test_ingest_reextract.py` has an existing test asserting the reset empties the rows and removes the directory; it must still pass (the directory is moved, so `not chunk_dir.exists()` holds). If it fails because `ing.LOGS_DIR` points outside its temp dir, add `monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")` to that test rather than changing the assertion.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_extraction_loss_guard.py test/test_ingest_reextract.py
git commit -m "fix(ingest): --reextract sets the chunk cache aside instead of deleting it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `extract_kg` refuses to stage while a chunk step is still failed

**Files:**
- Modify: `artmind/ingest.py` — `extract_kg` (starts line ~2562): the reset call (~2660), the first pass + pre-merge retry block (~2934-2978), and just after `write_staging` (~3120)
- Test: `test/test_extraction_loss_guard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_extraction_loss_guard.py`:

```python
# ── extract_kg end to end, LLM mocked ────────────────────────────────────────


def _schemas(vault, monkeypatch):
    schemas = vault / "schemas"
    schemas.mkdir(exist_ok=True)
    src = Path(ing.__file__).parent / "domains" / "schemas" / "general_schema.yaml"
    (schemas / "general_schema.yaml").write_text(src.read_text())
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)


def _seams(monkeypatch, llm):
    """No embedder, no real prompts; `llm(step_name)` answers every LLM step."""
    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: "p")
    monkeypatch.setattr(ing, "build_properties_prompt",
                        lambda text, ents, schema, vocabulary=None, anchor_date=None: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: "p")
    monkeypatch.setattr(ing, "_llm_extract", lambda step_name, model, prompt, debug_dir: llm(step_name))


def _note(vault, text="A synthetic note about Alpha meeting Beta.\n"):
    note = vault / "notes" / "a.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(text, encoding="utf-8")
    return note


def _previously_extracted(vault, monkeypatch, observations=PRIOR_OBSERVATIONS):
    """A note whose earlier extraction is staged (non-empty by default) with a
    complete chunk cache: the state a `--reextract` finds it in. Returns the
    re-extraction's file_result and the staging folder."""
    _schemas(vault, monkeypatch)
    note = _note(vault)
    first = ing.ingest_file(note, "m", "general")
    folder = stage_as_extracted(ing.KG_DIR, first)
    (folder / "observations.json").write_text(json.dumps(observations), encoding="utf-8")
    (folder / "relationships.json").write_text(json.dumps(PRIOR_RELATIONSHIPS), encoding="utf-8")
    result = ing.ingest_file(note, "m", "general", reextract=True)
    result["reextract"] = True
    cache = folder / "chunks" / result["sha256"]
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text('{"raw_entities": ["prior"]}', encoding="utf-8")
    ing._init_chunk_rows(result["sha256"], result["artmind_id"], 1)
    for step in ("entities", "properties", "relationships"):
        ing._update_chunk_step(result["sha256"], 1, step, "ok")
    return result, folder


def _snapshot(folder):
    return {name: (folder / name).read_bytes()
            for name in ("document.json", "observations.json", "relationships.json")}


def test_a_failed_reextraction_leaves_staging_cache_and_rows_as_they_were(ingest_env, monkeypatch, stash_root):
    vault, _ = ingest_env
    result, folder = _previously_extracted(vault, monkeypatch)
    before_files, before_rows = _snapshot(folder), _rows(result["sha256"])
    _seams(monkeypatch, lambda step: ([], False))

    assert ing.extract_kg(result, "general", max_workers=1) is None

    assert _snapshot(folder) == before_files
    assert _rows(result["sha256"]) == before_rows
    cache = folder / "chunks" / result["sha256"]
    assert json.loads((cache / "chunk_001.json").read_text()) == {"raw_entities": ["prior"]}
    assert list((stash_root / "reextract-stash").iterdir()) == []


def test_an_exception_during_reextraction_restores_the_cache(ingest_env, monkeypatch, stash_root):
    vault, _ = ingest_env
    result, folder = _previously_extracted(vault, monkeypatch)
    before_files = _snapshot(folder)

    def boom(step):
        raise RuntimeError("provider down")

    _seams(monkeypatch, boom)

    with pytest.raises(RuntimeError, match="provider down"):
        ing.extract_kg(result, "general", max_workers=1)

    assert _snapshot(folder) == before_files
    cache = folder / "chunks" / result["sha256"]
    assert json.loads((cache / "chunk_001.json").read_text()) == {"raw_entities": ["prior"]}


def test_a_first_ingest_with_failed_steps_stages_nothing_and_keeps_rows_for_resume(
    ingest_env, monkeypatch, stash_root,
):
    vault, _ = ingest_env
    _schemas(vault, monkeypatch)
    result = ing.ingest_file(_note(vault), "m", "general")
    _seams(monkeypatch, lambda step: ([], False))

    assert ing.extract_kg(result, "general", max_workers=1) is None

    folder = ing.KG_DIR / "general" / "a"
    assert not (folder / "observations.json").exists()
    assert ing._get_chunk_statuses(result["sha256"])[1]["entities_status"] == "failed"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_extraction_loss_guard.py -q -k "failed or exception or first_ingest"`
Expected: FAIL — `extract_kg` returns the folder (not `None`) and overwrites `observations.json` with `[]`; the exception test finds the prior chunk file gone.

- [ ] **Step 3: Implement**

In `extract_kg`, replace the reset call:

```python
    chunk_data_dir = doc_kg_dir / "chunks" / doc_sha256
    if file_result.get("reextract"):
        # `ingest --reextract`: the body is unchanged, so the cache above would
        # hit on every step.
        _reset_extraction_cache(chunk_data_dir, doc_sha256)
    chunk_data_dir.mkdir(parents=True, exist_ok=True)
```

with:

```python
    chunk_data_dir = doc_kg_dir / "chunks" / doc_sha256
    # `ingest --reextract`: the body is unchanged, so the cache above would
    # hit on every step. Set aside, not deleted: every refusal below puts it
    # back (spec 2026-10-18-extraction-loss-guard-design.md §3.1).
    stash = _reset_extraction_cache(chunk_data_dir, doc_sha256) if file_result.get("reextract") else None
    chunk_data_dir.mkdir(parents=True, exist_ok=True)
```

Replace the block from `# ── first pass ──` through the end of the pre-merge retry loop — currently:

```python
    # ── first pass ────────────────────────────────────────────────────────────
    statuses = _get_chunk_statuses(doc_sha256)
    workers = _resolve_ingest_workers(chunk_count, max_workers)
    if workers <= 1:
        ...
    else:
        ...
        if first_exc is not None:
            raise first_exc

    # ── pre-merge retry pass ───────────────────────────────────────────────────
    statuses = _get_chunk_statuses(doc_sha256)
    failed_seqs = [
        ...
    ]
    if failed_seqs:
        logger.info("Pre-merge retry: {} chunk(s) with failed steps — retrying", len(failed_seqs))
        for seq in failed_seqs:
            chunk_file = chunks_dir / f"chunk_{seq:03d}.md"
            _process_chunk(seq, chunk_file, statuses)
```

with the same code wrapped in a `try` (body unchanged, re-indented one level), followed by the new guard:

```python
    try:
        # ── first pass ────────────────────────────────────────────────────────
        statuses = _get_chunk_statuses(doc_sha256)
        workers = _resolve_ingest_workers(chunk_count, max_workers)
        if workers <= 1:
            for seq, chunk_file in enumerate(chunk_files, start=1):
                logger.info("Chunk {}/{} ({} bytes)", seq, chunk_count, chunk_file.stat().st_size)
                _process_chunk(seq, chunk_file, statuses)
        else:
            # (keep the existing NOTE(429-backoff) and fan-out comments verbatim)
            logger.info("Extracting {} chunk(s) with {} workers", chunk_count, workers)
            first_exc: Exception | None = None
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_process_chunk, seq, chunk_file, statuses): seq
                    for seq, chunk_file in enumerate(chunk_files, start=1)
                }
                for future in as_completed(futures):
                    seq = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        logger.error("Chunk {} raised during extraction: {}", seq, e)
                        if first_exc is None:
                            first_exc = e
            if first_exc is not None:
                raise first_exc

        # ── pre-merge retry pass ───────────────────────────────────────────────
        statuses = _get_chunk_statuses(doc_sha256)
        failed_seqs = [
            seq for seq, s in statuses.items()
            if "failed" in (s["entities_status"], s["properties_status"], s["relationships_status"])
        ]
        if failed_seqs:
            logger.info("Pre-merge retry: {} chunk(s) with failed steps — retrying", len(failed_seqs))
            for seq in failed_seqs:
                chunk_file = chunks_dir / f"chunk_{seq:03d}.md"
                _process_chunk(seq, chunk_file, statuses)
    except BaseException:
        _restore_extraction_cache(stash, chunk_data_dir, doc_sha256)
        raise

    # ── refuse an incomplete extraction (spec 2026-10-18 §3.1) ────────────────
    # Staging is what Obsidian Git commits and every other machine rebuilds its
    # graph from: an extraction with a step still failed after the retry must
    # never replace it. Without --reextract the failed rows stay for resume.
    still_failed = sorted(
        seq for seq, s in _get_chunk_statuses(doc_sha256).items()
        if "failed" in (s["entities_status"], s["properties_status"], s["relationships_status"])
    )
    if still_failed:
        logger.error(
            "KG extraction of {} incomplete: chunk(s) {} still failed after the retry -- "
            "staging left unchanged and the file marked failed; re-run the same command",
            registered_path.name, still_failed,
        )
        _restore_extraction_cache(stash, chunk_data_dir, doc_sha256)
        return None
```

Keep the variable names (`chunk_files`, `chunk_count`, `_process_chunk`) exactly as the existing code defines them above this block — read the lines before editing; only the indentation and the surrounding `try`/`except`/guard change.

After the `write_staging(doc_kg_dir, {...})` call (the one writing `document.json`, `chunks.json`, `relationships.json`, `observations.json`), add:

```python
    _drop_extraction_stash(stash)
```

Update the `extract_kg` docstring's last line from `Returns doc_kg_dir on success, None if prerequisites are missing.` to:

```python
    Returns doc_kg_dir on success, None if prerequisites are missing or the
    extraction may not be staged (a step still failed after the retry, or a
    re-extraction of an unchanged note came back empty over a non-empty one --
    spec 2026-10-18-extraction-loss-guard-design.md); then staging, the chunk
    cache and its rows are left as they were.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_extraction_loss_guard.py test/test_ingest_reextract.py test/test_ingest_extract_kg_retry.py test/test_ingest_concurrency.py test/test_vault_chunk_cache_merge.py -q`
Expected: PASS. If an existing test relied on a partially failed extraction being staged, read it: if it asserts the old "stage anyway" behaviour, update it to the new contract and say so in the commit message; do not weaken the guard.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/
git commit -m "fix(ingest): never stage an extraction with a step still failed

A re-extraction whose LLM steps kept failing replaced a note's staged
observations with an empty list, which git then spread to every machine.
extract_kg now returns None, leaves staging untouched and restores the
set-aside chunk cache and rows.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: An unchanged note may not re-extract to nothing

**Files:**
- Modify: `artmind/ingest.py` — `extract_kg`, just before the `write_staging(doc_kg_dir, {...})` call; a new helper next to `staged_counts`
- Test: `test/test_extraction_loss_guard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_an_unchanged_note_that_reextracts_to_nothing_keeps_its_previous_extraction(
    ingest_env, monkeypatch, stash_root,
):
    vault, _ = ingest_env
    result, folder = _previously_extracted(vault, monkeypatch)
    before_files = _snapshot(folder)
    _seams(monkeypatch, lambda step: ([], True))  # every step "succeeds" with nothing

    assert ing.extract_kg(result, "general", max_workers=1) is None

    assert _snapshot(folder) == before_files
    cache = folder / "chunks" / result["sha256"]
    assert json.loads((cache / "chunk_001.json").read_text()) == {"raw_entities": ["prior"]}


def test_a_reextraction_over_an_empty_previous_result_is_staged_and_drops_the_stash(
    ingest_env, monkeypatch, stash_root,
):
    vault, _ = ingest_env
    result, folder = _previously_extracted(vault, monkeypatch, observations=[])
    _seams(monkeypatch, lambda step: ([], True))

    assert ing.extract_kg(result, "general", max_workers=1) == folder

    assert json.loads((folder / "observations.json").read_text()) == []
    cache = folder / "chunks" / result["sha256"]
    assert json.loads((cache / "chunk_001.json").read_text())["raw_entities"] == []
    assert list((stash_root / "reextract-stash").iterdir()) == []


def test_without_reextract_an_emptied_result_is_staged(ingest_env, monkeypatch, stash_root):
    """The guard is for an unchanged body only: a note whose text was cut down
    may legitimately extract to nothing."""
    vault, _ = ingest_env
    result, folder = _previously_extracted(vault, monkeypatch)
    result["reextract"] = False
    conn = ing._get_db()
    try:
        conn.execute("DELETE FROM kg_chunk_status WHERE doc_sha256 = ?", (result["sha256"],))
        conn.commit()
    finally:
        conn.close()
    _seams(monkeypatch, lambda step: ([], True))

    assert ing.extract_kg(result, "general", max_workers=1) == folder

    assert json.loads((folder / "observations.json").read_text()) == []
```

- [ ] **Step 2: Run them to verify the first fails**

Run: `uv run --group dev pytest test/test_extraction_loss_guard.py -q -k "nothing or empty_previous or without_reextract"`
Expected: `test_an_unchanged_note_that_reextracts_to_nothing...` FAILS (staging overwritten with `[]`); the other two PASS already.

- [ ] **Step 3: Implement**

Add next to `staged_counts` in `artmind/ingest.py`:

```python
def _staged_observation_count(doc_kg_dir: Path) -> int:
    """How many observations a staging folder holds now; 0 when it holds none
    or cannot be read."""
    try:
        data = json.loads((doc_kg_dir / "observations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return len(data) if isinstance(data, list) else 0
```

In `extract_kg`, immediately before `write_staging(doc_kg_dir, {` (after `all_observations = _build_observations(...)`), add:

```python
    # ── an unchanged note may not shrink to nothing (spec 2026-10-18 §3.2) ────
    # A model answering `[]` reports success, so the failed-step guard above
    # cannot see it. Only for --reextract: the body is unchanged, so an empty
    # answer over a non-empty one is a bad run, not a change to the note.
    if stash is not None and not all_observations:
        prior = _staged_observation_count(doc_kg_dir)
        if prior:
            logger.error(
                "Re-extraction of unchanged {} produced no observations -- keeping the "
                "previous {} and marking the file failed",
                registered_path.name, prior,
            )
            _restore_extraction_cache(stash, chunk_data_dir, doc_sha256)
            return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_extraction_loss_guard.py test/test_ingest_reextract.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_extraction_loss_guard.py
git commit -m "fix(ingest): a re-extraction of an unchanged note may not empty it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `artmind ingest empty-extractions`

**Files:**
- Create: `artmind/staging_audit.py`
- Modify: `artmind/cli.py` — new command after `ingest_pending_cmd` (~line 1068); `COMMAND_GROUPS["artmind ingest"]` "Graph building" list (~line 244)
- Test: `test/test_staging_audit.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_staging_audit.py`:

```python
"""`ingest empty-extractions`: staged notes whose extraction is empty although
their text is not (spec 2026-10-18-extraction-loss-guard-design.md §3.3).
Synthetic staging folders only; no Neo4j, no LLM."""
import json

from click.testing import CliRunner

import paths
from artmind.cli import cli
from artmind.staging_audit import empty_extractions


def _stage(kg, domain, stem, *, text, observations):
    folder = kg / domain / stem
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps(
        {"id": f"id-{stem}", "path": f"notes/{stem}.md", "_domain": domain}), encoding="utf-8")
    (folder / "chunks.json").write_text(json.dumps(
        [{"id": f"id-{stem}_001", "text": text}]), encoding="utf-8")
    (folder / "observations.json").write_text(json.dumps(observations), encoding="utf-8")
    return folder


def test_lists_only_empty_notes_with_enough_text(tmp_path):
    kg = tmp_path / "kg"
    _stage(kg, "journal", "emptied", text="x" * 300, observations=[])
    _stage(kg, "journal", "short", text="x" * 50, observations=[])
    _stage(kg, "journal", "fine", text="x" * 300, observations=[{"id": "o1"}])

    report = empty_extractions(kg, min_chars=200)

    assert report == {"notes": [{"path": "notes/emptied.md", "domain": "journal", "chars": 300, "chunks": 1}],
                      "scanned": 3}


def test_domain_filter_matches_the_family(tmp_path):
    kg = tmp_path / "kg"
    _stage(kg, "bank", "a", text="x" * 300, observations=[])
    _stage(kg, "bank.policy", "b", text="x" * 300, observations=[])
    _stage(kg, "banking", "c", text="x" * 300, observations=[])

    paths_found = [n["path"] for n in empty_extractions(kg, domains=["bank"])["notes"]]

    assert paths_found == ["notes/a.md", "notes/b.md"]


def test_unreadable_folders_are_skipped_and_a_missing_kg_dir_is_empty(tmp_path):
    kg = tmp_path / "kg"
    folder = _stage(kg, "journal", "broken", text="x" * 300, observations=[])
    (folder / "chunks.json").write_text("{not json", encoding="utf-8")

    assert empty_extractions(kg)["notes"] == []
    assert empty_extractions(tmp_path / "nowhere") == {"notes": [], "scanned": 0}


def test_cli_reads_the_configured_kg_dir(tmp_path, monkeypatch):
    kg = tmp_path / "kg"
    _stage(kg, "journal", "emptied", text="x" * 300, observations=[])
    monkeypatch.setattr(paths, "KG_DIR", kg)

    out = CliRunner().invoke(cli, ["ingest", "empty-extractions", "--domain", "journal",
                                   "--minChars", "100", "--compact"])

    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["notes"][0]["path"] == "notes/emptied.md"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_staging_audit.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.staging_audit'`.

- [ ] **Step 3: Implement**

Create `artmind/staging_audit.py`:

```python
"""Read-only audits of the KG staging folders (`KG_DIR/<domain>/<stem>/`).

`empty_extractions` lists notes whose staged extraction is empty although
their chunk text is not -- what a re-extraction that silently came back empty
leaves behind (spec 2026-10-18-extraction-loss-guard-design.md §3.3). Reads
staging only: no registry, no Neo4j, no LLM.
"""
import json
from pathlib import Path


def _in_family(domain: str, wanted: list[str] | None) -> bool:
    return not wanted or any(domain == w or domain.startswith(w + ".") for w in wanted)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def empty_extractions(kg_dir: Path, *, domains: list[str] | None = None, min_chars: int = 200) -> dict:
    """`{"notes": [{path, domain, chars, chunks}], "scanned": N}` for staging
    folders whose `observations.json` is `[]` while their `chunks.json` text
    totals at least `min_chars`. `scanned` counts folders with an
    `observations.json`; unreadable folders are skipped."""
    kg_dir = Path(kg_dir)
    notes: list[dict] = []
    scanned = 0
    if not kg_dir.is_dir():
        return {"notes": notes, "scanned": scanned}
    for domain_dir in sorted(p for p in kg_dir.iterdir() if p.is_dir()):
        if not _in_family(domain_dir.name, domains):
            continue
        for folder in sorted(p for p in domain_dir.iterdir() if p.is_dir()):
            if not (folder / "observations.json").is_file():
                continue
            scanned += 1
            try:
                observations = _read_json(folder / "observations.json")
                chunks = _read_json(folder / "chunks.json")
            except (OSError, ValueError):
                continue
            if observations != [] or not isinstance(chunks, list):
                continue
            texts = [c.get("text") or "" for c in chunks if isinstance(c, dict)]
            chars = sum(len(t) for t in texts)
            if chars < min_chars:
                continue
            try:
                document = _read_json(folder / "document.json")
            except (OSError, ValueError):
                document = {}
            notes.append({
                "path": document.get("path") or folder.name,
                "domain": document.get("_domain") or domain_dir.name,
                "chars": chars,
                "chunks": len(texts),
            })
    return {"notes": notes, "scanned": scanned}
```

In `artmind/cli.py`, add after `ingest_pending_cmd`:

```python
@ingest.command("empty-extractions")
@click.option("--domain", "domain", multiple=True,
              help="Only these domains or domain families (repeatable; comma-splittable). Default: all")
@click.option("--minChars", "min_chars", type=int, default=200, show_default=True,
              help="Ignore notes whose staged chunk text is shorter than this")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_empty_extractions(domain: tuple, min_chars: int, compact: bool) -> None:
    """List staged notes whose extraction is empty although their text is not. Read-only: no Neo4j, no LLM.

    A re-extraction that came back empty used to replace a note's facts
    silently. Prints `{notes, scanned}`: each note's vault-relative `path`,
    `domain`, staged `chars` and `chunks`. Re-extract one with
    `artmind ingest sync "<path>" --reextract`.
    """
    import paths
    from artmind.staging_audit import empty_extractions

    _echo_json(empty_extractions(paths.KG_DIR, domains=_parse_repeatable(domain) or None,
                                 min_chars=min_chars), compact)
```

(Read `paths.KG_DIR` at call time, not via `from paths import KG_DIR`, so the test's `monkeypatch.setattr(paths, "KG_DIR", ...)` reaches it.)

In `COMMAND_GROUPS["artmind ingest"]`, add `"empty-extractions"` to the "Graph building" list after `"classify-reingest"`:

```python
            "commands": ["extract-kg", "classify-reingest", "empty-extractions", "write-to-graph", "retime", "table2graph", "pull-kg", "embed-entities", "embed-chunks"],
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_staging_audit.py test/test_cli_guide.py -q`
Expected: PASS (`test_cli_guide.py` fails if a command is not routed in `COMMAND_GROUPS`).

- [ ] **Step 5: Commit**

```bash
git add artmind/staging_audit.py artmind/cli.py test/test_staging_audit.py
git commit -m "feat(ingest): empty-extractions lists notes whose staged extraction is empty

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Registry-miss hint for `extract-kg` and `write-to-graph`

**Files:**
- Modify: `artmind/cli.py` — new helper near `_parse_repeatable` (~line 99); the two `raise click.ClickException(f"Document '{document_name}' not found in registry for domain '{domain}'")` lines (~1219 in `ingest_extract_kg`, ~1398 in `write-to-graph`)
- Test: `test/test_registry_miss_hint.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_registry_miss_hint.py`:

```python
"""A note staged here but registered on another machine: `extract-kg` /
`write-to-graph` say so and name the command that works (spec
2026-10-18-extraction-loss-guard-design.md §3.4). Synthetic folders only."""
import json

from click.testing import CliRunner

import artmind.cli as cli_module
import paths
from artmind.cli import cli


def _miss(monkeypatch):
    monkeypatch.setattr(cli_module, "_build_file_result_from_db", lambda name, domain: None)


def test_extract_kg_names_the_reextract_command_for_a_staged_note(tmp_path, monkeypatch):
    _miss(monkeypatch)
    folder = tmp_path / "kg" / "journal" / "day"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"path": "notes/day.md"}), encoding="utf-8")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")

    out = CliRunner().invoke(cli, ["ingest", "extract-kg", "day.md", "--domain", "journal"])

    assert out.exit_code != 0
    assert "not in this machine's registry" in out.output
    assert 'artmind ingest sync "notes/day.md" --reextract' in out.output


def test_write_to_graph_names_it_too(tmp_path, monkeypatch):
    _miss(monkeypatch)
    folder = tmp_path / "kg" / "journal" / "day"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"path": "notes/day.md"}), encoding="utf-8")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")

    out = CliRunner().invoke(cli, ["ingest", "write-to-graph", "day.md", "--domain", "journal"])

    assert out.exit_code != 0
    assert "--reextract" in out.output


def test_an_unstaged_note_gets_the_plain_message(tmp_path, monkeypatch):
    _miss(monkeypatch)
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")

    out = CliRunner().invoke(cli, ["ingest", "extract-kg", "day.md", "--domain", "journal"])

    assert out.exit_code != 0
    assert "not found in registry for domain 'journal'" in out.output
    assert "--reextract" not in out.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_registry_miss_hint.py -q`
Expected: the two hint tests FAIL (plain message only); the third PASSES. If `write-to-graph` fails earlier for an unrelated reason (e.g. a required option), read its signature at `@ingest.command("write-to-graph")` and add that option to the test's argv — do not change the command.

- [ ] **Step 3: Implement**

Add to `artmind/cli.py` after `_parse_repeatable`:

```python
def _registry_miss_message(document_name: str, domain: str) -> str:
    """`not found in registry`, plus the way out when the note is staged here
    but was ingested on another machine: the registry is per machine, staging
    travels with the vault (spec 2026-10-18-extraction-loss-guard-design.md §3.4)."""
    import paths

    message = f"Document '{document_name}' not found in registry for domain '{domain}'"
    folder = paths.KG_DIR / domain / Path(document_name).stem
    try:
        staged = json.loads((folder / "document.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return message
    path = staged.get("path") or document_name
    return (
        f"{message}.\nIt is staged ({folder}) but not in this machine's registry -- it was "
        f"probably ingested on another machine. Re-extract it here with:\n"
        f'  artmind ingest sync "{path}" --reextract'
    )
```

Replace both occurrences of

```python
raise click.ClickException(f"Document '{document_name}' not found in registry for domain '{domain}'")
```

with

```python
raise click.ClickException(_registry_miss_message(document_name, domain))
```

(keep each line's existing indentation).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_registry_miss_hint.py test/test_ingest_extract_kg_retry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_registry_miss_hint.py
git commit -m "fix(cli): extract-kg/write-to-graph explain a note staged but registered elsewhere

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `traces list` shows `session_id`

**Files:**
- Modify: `artmind/cli.py` — the `turns.append({k: t[k] for k in (...)})` line in the `traces list` command (~line 3614)
- Test: `test/test_traces_cli.py` (append)

- [ ] **Step 1: Write the failing test**

Read the top of `test/test_traces_cli.py` for its DB-isolation fixture and how it inserts turns, then append a test in the same style:

```python
def test_list_rows_carry_the_conversation_id():
    from artmind.traces import store

    store.insert_turn("t-conv", source="admin", question="q", session_id="conv-1")
    store.update_turn("t-conv", status="done")

    out = CliRunner().invoke(cli, ["traces", "list", "--compact"])

    assert out.exit_code == 0, out.output
    row = next(t for t in json.loads(out.output)["turns"] if t["trace_id"] == "t-conv")
    assert row["session_id"] == "conv-1"
```

(Use the file's existing imports and its autouse DB fixture; add `import json` / `CliRunner` / `cli` imports only if the file lacks them.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_traces_cli.py -q -k conversation_id`
Expected: FAIL with `KeyError: 'session_id'`.

- [ ] **Step 3: Implement**

In the `traces list` command, change

```python
        turns.append({k: t[k] for k in ("trace_id", "source", "started_at", "rating", "question", "turns",
                                        "cost_usd", "fell_through", "answer_basis", "status")})
```

to

```python
        turns.append({k: t[k] for k in ("trace_id", "source", "session_id", "started_at", "rating", "question",
                                        "turns", "cost_usd", "fell_through", "answer_basis", "status")})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_traces_cli.py test/test_plugin_fixtures.py -q`
Expected: PASS. If `test_plugin_fixtures.py` fails because a fixture holds `traces list` output, regenerate with `ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -q` and include the regenerated fixture in the commit.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/
git commit -m "feat(traces): traces list shows each turn's conversation id

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Skills

**Files:**
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md` — Situation D (~lines 200-230), the error table (~lines 620-630)
- Modify: `artmind/skills/artmind-query/SKILL.md` — `## Answer Style`

No tests: these are prose. Read each section before editing; change only what is listed.

- [ ] **Step 1: ingestion-helper, Situation D**

Directly under the `artmind ingest extract-kg DOCUMENT_NAME --domain YOUR_DOMAIN` code block (~line 206), add:

```markdown
`extract-kg` finds the note in **this machine's** registry. A note ingested on
another machine of the same vault is staged and in the graph but not in this
registry, and `extract-kg` reports `not found in registry` (it then prints the
command below). Re-extract such a note from inside the vault with:

```bash
artmind ingest sync "<vault-relative path>" --reextract
```

A re-extraction that fails, or comes back empty for an unchanged note, keeps the
note's previous extraction and marks the file failed — re-run the same command.
```

- [ ] **Step 2: ingestion-helper, error table**

Replace the row

```markdown
| `Document not found in registry` | Document was never ingested with `sync`/`async` | Run `artmind ingest sync FILE --domain DOMAIN` first |
```

with

```markdown
| `Document not found in registry` | Never ingested on **this machine** — either never ingested, or ingested on another machine of the vault (then the message says it is staged) | Never ingested: `artmind ingest sync FILE`. Staged elsewhere: `artmind ingest sync "<path>" --reextract` from inside the vault |
```

Replace the row starting `| Extraction completes in seconds with 0 entities and all chunks failed |` with

```markdown
| Extraction completes in seconds with 0 entities and all chunks failed | Too many concurrent jobs — Ollama cloud rate limiter rejected requests | Run max 5 jobs at a time. The file is marked failed and its previous extraction is kept; re-run it (`extract-kg`, or `ingest sync "<path>" --reextract`) |
```

Add a row after it:

```markdown
| A note's facts are missing from answers though its text is there | Its staged extraction is empty (an earlier bad run, before the loss guard) | `artmind ingest empty-extractions --domain DOMAIN` lists such notes; re-extract each with `artmind ingest sync "<path>" --reextract` |
```

- [ ] **Step 3: artmind-query, Answer Style**

Add a paragraph to `## Answer Style`, after its first paragraph:

```markdown
If an answer came from reading a note directly (possible on the admin console)
rather than from `artmind query` output, say plainly that the graph does not hold
it, and suggest the operator check `artmind ingest empty-extractions` and
re-extract the note — a fact only the note holds is missed by every later query.
```

- [ ] **Step 4: Run the skill-text tests**

Run: `uv run --group dev pytest test/test_query_skill_views_step.py test/test_insights_skills.py -q`
Expected: PASS (they pin the query skill's Views/Insights sections, which this task does not touch).

- [ ] **Step 5: Commit**

```bash
git add artmind/skills/
git commit -m "docs(skills): re-extracting a note ingested elsewhere; empty-extractions; say when the graph lacks an answer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: CLAUDE.md and the final check

**Files:**
- Modify: `CLAUDE.md` — the `artmind/ingest.py` `--reextract` row of the layout table; add a row for `artmind/staging_audit.py`

- [ ] **Step 1: Update CLAUDE.md**

In the layout row that begins ``| `artmind/ingest.py` `--reextract` |``, append to its description:

```markdown
 The cache is set aside under `logs/reextract-stash/`, not deleted; `extract_kg` refuses to stage (returns None, file failed) while a chunk step is still `failed` after the retry, or when a re-extraction of an unchanged note comes back with no observations over a non-empty result — then it restores the cache and rows (spec `docs/superpowers/specs/2026-10-18-extraction-loss-guard-design.md`).
```

Add a row after the `artmind/table2graph.py` row:

```markdown
| `artmind/staging_audit.py` | `ingest empty-extractions`: read-only audit of staging folders, listing notes whose `observations.json` is empty although their chunk text is not. No registry, Neo4j or LLM. |
```

- [ ] **Step 2: Run the wider targeted set**

Run:

```bash
uv run --group dev pytest test/test_extraction_loss_guard.py test/test_staging_audit.py test/test_registry_miss_hint.py test/test_traces_cli.py test/test_traces_views_check.py test/test_ingest_reextract.py test/test_ingest_extract_kg_retry.py test/test_ingest_concurrency.py test/test_vault_chunk_cache_merge.py test/test_ingest_vault_native.py test/test_document_dates.py test/test_cli_guide.py test/test_query_skill_views_step.py test/test_insights_skills.py -q
```

Expected: all PASS. Report the exact pass count. If anything fails, fix it in the task it belongs to (with its own commit), not here.

- [ ] **Step 3: Check the CLI from the checkout without touching a vault**

```bash
ARTMIND_NO_PROXY=1 uv run artmind ingest empty-extractions --help
ARTMIND_NO_PROXY=1 uv run artmind ingest --help
```

Expected: the help text prints and `empty-extractions` is listed under "Graph building". Do not run it against a real vault.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md for the extraction loss guard and empty-extractions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Not in this plan (operator follow-up, spec §5)

Recovering the notes already emptied: after this branch is merged and installed,
the operator runs, from inside the affected vault, `artmind ingest
empty-extractions --domain <d>` and then `artmind ingest sync "<path>" --reextract`
for each note they confirm. The implementing session must not run either against a
real vault.
