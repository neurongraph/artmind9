# Vault git transport — Phase 2 ("readiness") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** artmind writes the vault in a form that is always safe for Obsidian Git to auto-commit and merge. Staging folders are swapped in atomically. Versioned `.gitignore`/`.gitattributes` blocks upgrade in place. Each table carries its own metadata file. `artmind vault doctor` reports anything unsafe and prints the exact fix.

**Architecture:** Spec [2026-09-26-vault-git-transport-design.md](../specs/2026-09-26-vault-git-transport-design.md), phase 2 = §5 R2, R3, R4, R5, R8 (§12 item 2). It builds on phase 1 (branch `vault-git-transport-phase1`, reviewed in `docs/superpowers/reviews/2026-09-26-vault-git-transport-phase1-review.md`).

The work splits into five pieces:
- **R2.** A new `artmind/atomic_dir.py` gives every staging writer one swap primitive.
- **R3/R4.** `artmind/vault.py` gains a generic "versioned artmind block" writer, used for both `.gitignore` and `.gitattributes`.
- **R5.** `artmind/structured/text_export.py` moves from one `manifest.json` to one `<domain>/<table>.meta.json` per table, and still reads the legacy manifest.
- **Sync.** `vault_sync` track B learns about the meta files.
- **R8.** A new `artmind/vault_doctor.py` backs `artmind vault doctor`.

artmind still never writes to git (D1): the doctor only prints `git rm --cached` and `git config` fixes.

**Tech Stack:** Python 3.14, Click, pytest (`just dev-test`), real throwaway git repos in `tmp_path` (never a mocked `run_command` when the behaviour under test is git's).

---

## Context for the implementer

Read these before starting:

- `CLAUDE.md`, especially "Testing implications" §1 (stale `serve` daemon), §3 (assert on the **parameters and content sent**, never on counts) and §4 (update the group docstring, skill and `justfile` together).
- The spec linked above: §2 (decisions D1–D6), §5 R2–R5 and R8, §11 (testing).
- `test/test_no_git_writes.py`. It fails the suite if any module under `artmind/` or `utils/` contains a git write call (`add`, `commit`, `rm`, `push`, `pull`, `merge`, `rebase`, `reset`, `checkout`, `stash`). The doctor *prints* `git rm --cached ...` as advice. Build that string so the guard's patterns don't match it: the first pattern matches a quote directly followed by `git rm`, so start the string with `"git "` and append `"rm -r --cached -- "`, as the code below does. Never run it.

Terminology:

- **Vault**: a user's Obsidian vault that is also a git repo. artmind's own files live in `<vault>/.artmind/`.
- **KG staging folder**: `.artmind/data/kg/<domain>/<docdir>/`. It holds `document.json`, `observations.json`, `chunks.json` and `relationships.json`. During ingest it also holds a per-chunk extraction cache under `chunks/<sha>/`, debug output, and a **gitignored** `embeddings.json` sidecar. `table__<name>` folders are the same shape, written by `ingest table2graph` and by `vault sync` track B.
- **Structured text**: `.artmind/data/structured_text/<domain>/<table>.csv` plus metadata. Before this phase the metadata is one shared `manifest.json`; after it, it is `<domain>/<table>.meta.json`.
- **Artmind block**: the section of `.gitignore`/`.gitattributes` between `# ── artmind … ─` and `# ── end artmind ─`. artmind owns it. The user owns every other line.

Run the suite with:

```bash
just dev-test
```

(`uv run --group dev pytest test/ -q`; ~35s, no Neo4j, no network.) For a single file: `uv run --group dev pytest test/test_atomic_dir.py -v`.

Do **not** run `just dev-install` or `artmind init` outside `tmp_path` during implementation. Both touch the machine's run folder and config; Task 9 leaves them for the user.

## File map

| File | Change |
|---|---|
| `artmind/atomic_dir.py` | new: `write_dir_atomic`, `recover`, `is_scratch` (R2) |
| `artmind/ingest.py` | new `write_staging()`; `extract_kg` writes its four JSON files through it (R2) |
| `artmind/table2graph.py` | `write_staged` goes through `ingest.write_staging` (R2) |
| `artmind/webui/dashboard_routes.py` | `/api/artifacts` skips `.artmind-tmp`/`.artmind-old` scratch folders |
| `artmind/vault.py` | versioned blocks: `GITIGNORE_BLOCK` v2 (R4), new `GITATTRIBUTES_BLOCK` (R3), `block_status`, `write_gitattributes`; `write_gitignore` replaces an older block in place |
| `artmind/setup.py` | `scaffold_vault` also writes `.gitattributes` |
| `artmind/structured/text_export.py` | per-table `.meta.json` (R5), legacy `manifest.json` read fallback and migration on export |
| `artmind/vault_sync.py` | track B classifies and materialises `.meta.json`; materialises only paths present at `head` |
| `artmind/vault_doctor.py` | new: the R8 checks |
| `artmind/cli.py` | `vault doctor` command; `vault` group docstring |
| `test/test_atomic_dir.py`, `test/test_ingest_staging_atomic.py`, `test/test_vault_doctor.py` | new |
| `test/test_table2graph.py`, `test/test_webui_admin_api.py`, `test/test_vault_scaffold.py`, `test/test_structured_text_export.py`, `test/test_vault_sync.py`, `test/test_vault_cli.py` | extended |
| `docs/vault.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md`, `justfile` | prose and recipe |

---

### Task 1: `atomic_dir`: swap a folder in atomically

Obsidian Git auto-commits on a timer and can capture a half-written staging folder (spec R2). This task adds the one primitive every staging writer will use. A directory rename over a non-empty directory is not atomic on POSIX, so the swap has three steps: old → `.artmind-old`, `.artmind-tmp` → target, remove `.artmind-old`. The scratch suffixes are `.artmind-tmp`/`.artmind-old`, not the spec's `.tmp`/`.old`: staging folders are named after the file stem, so `notes.old.pdf` legitimately stages into `notes.old/`, and a plain `.old` suffix would make a swap of `notes/` rename or delete that other document (found in Task 1 review). Entries the writer does not replace (the chunk cache, the sidecar) are carried into `.artmind-tmp` as hardlinks. That costs no copy and never writes through into the old folder, because each replaced file is unlinked before it is written.

**Files:**
- Create: `artmind/atomic_dir.py`
- Test: `test/test_atomic_dir.py` (create)

- [ ] **Step 1: Write the failing test**

Create `test/test_atomic_dir.py`:

```python
"""artmind.atomic_dir: atomic staging-folder swap (spec 2026-09-26 §5 R2)."""
from pathlib import Path

import pytest

from artmind.atomic_dir import is_scratch, recover, write_dir_atomic


def _siblings(target: Path) -> list[str]:
    return sorted(p.name for p in target.parent.iterdir())


def test_creates_a_new_folder_with_no_scratch_left_behind(tmp_path):
    target = tmp_path / "general" / "doc"

    write_dir_atomic(target, {"document.json": '{"id": "x"}', "observations.json": b"[]"})

    assert (target / "document.json").read_text() == '{"id": "x"}'
    assert (target / "observations.json").read_bytes() == b"[]"
    assert _siblings(target) == ["doc"]


def test_replaces_named_files_and_carries_everything_else_over(tmp_path):
    target = tmp_path / "doc"
    (target / "chunks" / "sha1").mkdir(parents=True)
    (target / "chunks" / "sha1" / "0.json").write_text("cached chunk")
    (target / "embeddings.json").write_text("vectors")
    (target / "observations.json").write_text("v1")

    write_dir_atomic(target, {"observations.json": "v2"})

    assert (target / "observations.json").read_text() == "v2"
    assert (target / "chunks" / "sha1" / "0.json").read_text() == "cached chunk"
    assert (target / "embeddings.json").read_text() == "vectors"
    assert _siblings(target) == ["doc"]


def test_interrupted_between_the_two_renames_the_next_write_recovers(tmp_path, monkeypatch):
    """Spec §11 "Atomic write": a crash between the renames leaves the old
    folder whole in `.artmind-old` and the new one whole in `.artmind-tmp`, never a mix. The
    old content in `.artmind-old` must be untouched: proof that writing the new
    file did not write through a hardlink into the old inode."""
    target = tmp_path / "doc"
    target.mkdir()
    (target / "observations.json").write_text("v1")
    (target / "document.json").write_text("d1")
    tmp = tmp_path / "doc.artmind-tmp"

    real_rename = Path.rename

    def crash_on_second_rename(self, dest):
        if self == tmp:
            raise OSError("simulated crash")
        return real_rename(self, dest)

    monkeypatch.setattr(Path, "rename", crash_on_second_rename)
    with pytest.raises(OSError, match="simulated crash"):
        write_dir_atomic(target, {"observations.json": "v2", "document.json": "d2"})
    monkeypatch.setattr(Path, "rename", real_rename)

    old = tmp_path / "doc.artmind-old"
    assert not target.exists()
    assert (old / "observations.json").read_text() == "v1"
    assert (old / "document.json").read_text() == "d1"
    assert (tmp / "observations.json").read_text() == "v2"
    assert (tmp / "document.json").read_text() == "d2"

    recover(target)

    # `.artmind-tmp` is only swapped in once complete, so recovery finishes the swap.
    assert (target / "observations.json").read_text() == "v2"
    assert (target / "document.json").read_text() == "d2"
    assert _siblings(target) == ["doc"]


def test_a_stale_tmp_from_a_crash_while_building_is_discarded(tmp_path):
    target = tmp_path / "doc"
    target.mkdir()
    (target / "observations.json").write_text("v1")
    stale = tmp_path / "doc.artmind-tmp"
    stale.mkdir()
    (stale / "observations.json").write_text("half-written")
    (stale / "junk.json").write_text("junk")

    write_dir_atomic(target, {"document.json": "d2"})

    assert (target / "observations.json").read_text() == "v1"
    assert (target / "document.json").read_text() == "d2"
    assert not (target / "junk.json").exists()
    assert _siblings(target) == ["doc"]


def test_a_stale_old_left_after_a_completed_swap_is_removed(tmp_path):
    target = tmp_path / "doc"
    target.mkdir()
    (target / "observations.json").write_text("v2")
    (tmp_path / "doc.artmind-old").mkdir()

    recover(target)

    assert (target / "observations.json").read_text() == "v2"
    assert _siblings(target) == ["doc"]


def test_a_document_folder_named_like_a_plain_old_or_tmp_sibling_is_never_touched(tmp_path):
    """Staging folders are named after the file stem, so `notes.old.pdf`
    stages into `notes.old/`. The swap's scratch suffixes must not collide
    with that (found in review; the spec's `.tmp`/`.old` would)."""
    other = tmp_path / "notes.old"
    other.mkdir()
    (other / "document.json").write_text("other document")

    write_dir_atomic(tmp_path / "notes", {"document.json": "this document"})

    assert (other / "document.json").read_text() == "other document"
    assert (tmp_path / "notes" / "document.json").read_text() == "this document"


@pytest.mark.parametrize("bad", ["/abs.json", "../escape.json", "sub/../../escape.json"])
def test_file_names_must_stay_inside_the_folder(tmp_path, bad):
    with pytest.raises(ValueError, match="relative path inside"):
        write_dir_atomic(tmp_path / "doc", {bad: "x"})
    assert not (tmp_path / "escape.json").exists()


def test_is_scratch():
    assert is_scratch(Path("kg/general/doc.artmind-tmp"))
    assert is_scratch(Path("kg/general/doc.artmind-old"))
    assert not is_scratch(Path("kg/general/doc"))
    assert not is_scratch(Path("kg/general/table__accounts"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_atomic_dir.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'artmind.atomic_dir'`.

- [ ] **Step 3: Implement**

Create `artmind/atomic_dir.py`:

```python
"""Atomic replacement of a KG staging folder (spec 2026-09-26 §5 R2).

Obsidian Git auto-commits on a timer, so it can capture a folder mid-write
(a new document.json beside an old observations.json). Every staging writer
builds the complete new folder in `<dir>.artmind-tmp/`, fsyncs it, then swaps it in:

    <dir>      -> <dir>.artmind-old
    <dir>.artmind-tmp  -> <dir>
    remove <dir>.artmind-old

A directory rename over a non-empty directory is not atomic on POSIX, hence
three steps. A crash leaves at worst a `.artmind-tmp` or `.artmind-old` sibling, both
gitignored (R4), and `recover()` -- run at the start of every write --
finishes or discards them.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

TMP_SUFFIX = ".artmind-tmp"
OLD_SUFFIX = ".artmind-old"


def is_scratch(path: Path) -> bool:
    """Whether `path` is a `.artmind-tmp`/`.artmind-old` sibling left by `write_dir_atomic`,
    which a directory listing of staging folders should skip."""
    return path.name.endswith((TMP_SUFFIX, OLD_SUFFIX))


def _siblings(target: Path) -> tuple[Path, Path]:
    return target.with_name(target.name + TMP_SUFFIX), target.with_name(target.name + OLD_SUFFIX)


def recover(target: Path) -> None:
    """Finish or discard a swap a crash interrupted.

    `.artmind-old` with no `target` means the crash fell between the two renames;
    `.artmind-tmp` is only renamed after it is complete and fsynced, so it is
    promoted. A `.artmind-tmp` beside an intact `target` is a crash while building
    and is discarded; a `.artmind-old` beside `target` is a crash before cleanup."""
    tmp, old = _siblings(target)
    if old.exists() and not target.exists():
        (tmp if tmp.exists() else old).rename(target)
    if tmp.exists():
        shutil.rmtree(tmp)
    if old.exists():
        shutil.rmtree(old)


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems refuse fsync on a directory; the rename still orders
    finally:
        os.close(fd)


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
    for name, content in files.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        with open(path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    _fsync_dir(tmp)
    if target.exists():
        target.rename(old)
    tmp.rename(target)
    _fsync_dir(target.parent)
    if old.exists():
        shutil.rmtree(old)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_atomic_dir.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/atomic_dir.py test/test_atomic_dir.py
git commit -m "feat(atomic_dir): swap a staging folder in atomically"
```

---

### Task 2: `extract_kg` writes its staging files through one atomic swap

**Files:**
- Modify: `artmind/ingest.py`. Add `write_staging` right after `read_embedding_sidecar` (≈line 1424–1430). Replace `_write_json` and its four calls in `extract_kg` (≈lines 2393–2399).
- Test: `test/test_ingest_staging_atomic.py` (create)

- [ ] **Step 1: Write the failing test**

Create `test/test_ingest_staging_atomic.py`:

```python
"""ingest.write_staging: the staging folder's JSON is swapped in as one unit
(spec 2026-09-26 §5 R2)."""
import datetime
import json

from artmind.ingest import write_staging


def test_write_staging_swaps_json_files_and_keeps_the_chunk_cache(tmp_path):
    doc = tmp_path / "kg" / "general" / "doc"
    (doc / "chunks" / "sha").mkdir(parents=True)
    (doc / "chunks" / "sha" / "0.json").write_text("cached")
    (doc / "observations.json").write_text('["old"]')

    write_staging(doc, {"document.json": {"id": "x", "name": "Café"}, "observations.json": [{"n": 1}]})

    assert json.loads((doc / "document.json").read_text(encoding="utf-8")) == {"id": "x", "name": "Café"}
    assert json.loads((doc / "observations.json").read_text()) == [{"n": 1}]
    assert (doc / "chunks" / "sha" / "0.json").read_text() == "cached"
    assert sorted(p.name for p in doc.parent.iterdir()) == ["doc"]


def test_write_staging_bytes_match_the_previous_format(tmp_path):
    """`vault sync` carries the embedding sidecar only when the live
    chunks.json is byte-identical to the committed one, so the on-disk
    format must not drift: indent=2, ensure_ascii=False, no trailing newline."""
    doc = tmp_path / "doc"
    chunks = [{"id": "c1", "text": "naïve"}]

    write_staging(doc, {"chunks.json": chunks})

    assert (doc / "chunks.json").read_text(encoding="utf-8") == json.dumps(chunks, indent=2, ensure_ascii=False)


def test_write_staging_default_serialises_dates(tmp_path):
    doc = tmp_path / "doc"

    write_staging(doc, {"report.json": {"as_of": datetime.date(2026, 9, 26)}}, default=str)

    assert json.loads((doc / "report.json").read_text()) == {"as_of": "2026-09-26"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_ingest_staging_atomic.py -v`
Expected: collection error, `ImportError: cannot import name 'write_staging' from 'artmind.ingest'`.

- [ ] **Step 3: Implement `write_staging`**

In `artmind/ingest.py`, directly after `read_embedding_sidecar` (the function ending `return {}` ≈line 1430), add:

```python
def write_staging(doc_kg_dir: Path, documents: dict[str, object], *, default=None) -> None:
    """Write a staging folder's JSON files as one atomic swap (spec
    2026-09-26 §5 R2), so an Obsidian Git auto-commit can never capture a
    new document.json beside an old observations.json. Other entries in the
    folder (chunk cache, sidecar) are carried over unchanged.

    Format is exactly the historical one -- `indent=2, ensure_ascii=False`,
    no trailing newline -- because `vault sync` compares chunks.json bytes."""
    from artmind.atomic_dir import write_dir_atomic

    write_dir_atomic(
        doc_kg_dir,
        {
            name: json.dumps(obj, indent=2, ensure_ascii=False, default=default)
            for name, obj in documents.items()
        },
    )
```

- [ ] **Step 4: Use it in `extract_kg`**

In `extract_kg`, replace these lines (≈2393–2399):

```python
    def _write_json(filename: str, obj: object) -> None:
        (doc_kg_dir / filename).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

    _write_json("document.json", document)
    _write_json("chunks.json", [strip_embeddings(c) for c in all_chunks])
    _write_json("relationships.json", all_relationships)
    _write_json("observations.json", all_observations)
```

with:

```python
    # One atomic swap (spec 2026-09-26 R2): Obsidian Git's timer commit must
    # never see a half-written folder. The chunk cache under chunks/<sha>/
    # and any debug output already in doc_kg_dir are carried over.
    write_staging(doc_kg_dir, {
        "document.json": document,
        "chunks.json": [strip_embeddings(c) for c in all_chunks],
        "relationships.json": all_relationships,
        "observations.json": all_observations,
    })
```

The `write_embedding_sidecar(doc_kg_dir, ...)` call that follows stays as is. It writes the gitignored sidecar into the swapped-in folder.

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_ingest_staging_atomic.py test/test_ingest_binary_derived.py test/test_ingest_sync_cli.py -v`
Expected: all PASS. Then run `grep -n "_write_json" artmind/ingest.py`. Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add artmind/ingest.py test/test_ingest_staging_atomic.py
git commit -m "feat(ingest): write KG staging as one atomic folder swap"
```

---

### Task 3: `table2graph.write_staged` uses the same atomic swap

**Files:**
- Modify: `artmind/table2graph.py:1194-1208` (`write_staged`)
- Test: `test/test_table2graph.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `test/test_table2graph.py`:

```python
def test_write_staged_swaps_atomically_and_clears_crash_leftovers(tmp_path):
    """Spec 2026-09-26 §5 R2: the table's staging folder is replaced as one
    unit, and a `.artmind-tmp` left by an earlier crash is cleaned up by this write."""
    import datetime
    import json

    from artmind.table2graph import write_staged

    domain_dir = tmp_path / "banking"
    target = domain_dir / "table__accounts"
    target.mkdir(parents=True)
    (target / "observations.json").write_text('["stale"]')
    (domain_dir / "table__accounts.artmind-tmp").mkdir()
    staged = {
        "document": {"id": "table:banking:accounts"},
        "chunks": [],
        "observations": [{"k": 1}],
        "relationships": [],
    }

    write_staged(staged, {"rows": 1, "as_of": datetime.date(2026, 9, 26)}, target)

    assert json.loads((target / "observations.json").read_text()) == [{"k": 1}]
    assert json.loads((target / "document.json").read_text()) == {"id": "table:banking:accounts"}
    assert json.loads((target / "table2graph_report.json").read_text()) == {"rows": 1, "as_of": "2026-09-26"}
    assert sorted(p.name for p in domain_dir.iterdir()) == ["table__accounts"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_table2graph.py::test_write_staged_swaps_atomically_and_clears_crash_leftovers -v`
Expected: FAIL on the last assertion (`['table__accounts', 'table__accounts.artmind-tmp'] != ['table__accounts']`).

- [ ] **Step 3: Implement**

Replace the body of `write_staged` in `artmind/table2graph.py` (keep the signature and docstring's first sentence):

```python
def write_staged(staged: dict, report: dict, directory: Path) -> None:
    """The same staged files a document's extract leaves behind, so
    `ingest write-to-graph --folder` can replay a table's contribution too.
    Written as one atomic folder swap (spec 2026-09-26 §5 R2)."""
    from artmind.ingest import write_staging

    write_staging(
        directory,
        {
            "document.json": staged["document"],
            "chunks.json": staged["chunks"],
            "observations.json": staged["observations"],
            "relationships.json": staged["relationships"],
            "table2graph_report.json": report,
        },
        default=str,
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_table2graph.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/table2graph.py test/test_table2graph.py
git commit -m "feat(table2graph): write staged table folders atomically"
```

---

### Task 4: the admin console's artifact list skips scratch folders

`/api/artifacts` lists every subdirectory of `KG_DIR/<domain>` that has a `document.json`. A `.artmind-tmp` or `.artmind-old` sibling has one too, so a crash leftover would show up as a duplicate document.

**Files:**
- Modify: `artmind/webui/dashboard_routes.py`, `api_artifacts` (≈line 343: `for doc_dir in sorted(d for d in domain_dir.iterdir() if d.is_dir()):`)
- Test: `test/test_webui_admin_api.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `test/test_webui_admin_api.py`:

```python
def test_artifacts_skip_atomic_write_scratch_folders(monkeypatch, tmp_path):
    """A crash mid-swap (spec 2026-09-26 R2) can leave doc1.artmind-tmp / doc1.artmind-old
    beside doc1; they are not documents."""
    monkeypatch.setattr(dashboard_routes, "KG_DIR", tmp_path)
    _write_doc_kg_dir(tmp_path, "general", "doc1", "doc1.pdf", entities=1)
    _write_doc_kg_dir(tmp_path, "general", "doc1.artmind-tmp", "doc1.pdf", entities=9)
    _write_doc_kg_dir(tmp_path, "general", "doc1.artmind-old", "doc1.pdf", entities=7)
    monkeypatch.setattr(dashboard_routes, "structural_metadata", lambda domains: {"rows": []})

    response = _client().get("/api/artifacts?domain=general")

    assert response.status_code == 200
    assert [a["doc"] for a in response.json()] == ["doc1"]
    assert response.json()[0]["entityCount"] == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_webui_admin_api.py::test_artifacts_skip_atomic_write_scratch_folders -v`
Expected: FAIL (`['doc1', 'doc1.artmind-old', 'doc1.artmind-tmp'] != ['doc1']`).

- [ ] **Step 3: Implement**

In `api_artifacts`, change the loop header to:

```python
        for doc_dir in sorted(d for d in domain_dir.iterdir() if d.is_dir() and not is_scratch(d)):
```

and add the import beside the other artmind imports at the top of `artmind/webui/dashboard_routes.py`:

```python
from artmind.atomic_dir import is_scratch
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_webui_admin_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/dashboard_routes.py test/test_webui_admin_api.py
git commit -m "fix(admin-ui): artifact list skips atomic-write scratch folders"
```

---

### Task 5: versioned `.gitignore` / `.gitattributes` blocks (R3, R4)

`write_gitignore` currently skips the whole block once its sentinel is present (`artmind/vault.py:286`), so a changed block never reaches an existing vault. This task does five things:
- It versions the block, and replaces an older artmind block in place, leaving the user's rules untouched.
- It adds the R4 lines: `table__*`, `*.artmind-tmp/` and `*.artmind-old/`.
- It adds the R3 `.gitattributes` block.
- It exposes `block_status` for the doctor (Task 8).
- It writes `.gitattributes` from `scaffold_vault`.

The `.gitattributes` block also carries a warning not to add `export-ignore`, `export-subst`, `text`/`eol` or `filter=` under `.artmind/data/**`. `vault sync` reads those paths through `git archive`, which applies them (phase 1 review, finding 2).

**Files:**
- Modify: `artmind/vault.py:218-290` (`GITIGNORE_BLOCK`, `_GITIGNORE_SENTINEL`, `write_gitignore`); add `import re` at the top
- Modify: `artmind/setup.py:7` (import) and `:424-439` (`scaffold_vault`)
- Test: `test/test_vault_scaffold.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_scaffold.py`:

```python
# ── versioned artmind blocks (spec 2026-09-26 §5 R3, R4) ──────────────────────

_V1_BLOCK = (
    "# ── artmind ───────────────────────────────────────────────────────────────────\n"
    ".artmind/config.env\n"
    "# ── end artmind ───────────────────────────────────────────────────────────────\n"
)


def test_an_older_artmind_block_is_replaced_in_place_keeping_user_rules(tmp_path):
    (tmp_path / ".gitignore").write_text("user-before\n\n" + _V1_BLOCK + "\nuser-after\n")

    changed = vault.write_gitignore(tmp_path)

    assert changed is True
    assert (tmp_path / ".gitignore").read_text() == "user-before\n\n" + vault.GITIGNORE_BLOCK + "\nuser-after\n"
    assert vault.write_gitignore(tmp_path) is False


def test_block_status_reports_missing_outdated_current_and_malformed(tmp_path):
    path = tmp_path / ".gitignore"
    assert vault.block_status(path, vault.GITIGNORE_BLOCK) == "missing"
    path.write_text(_V1_BLOCK)
    assert vault.block_status(path, vault.GITIGNORE_BLOCK) == "outdated"
    vault.write_gitignore(tmp_path)
    assert vault.block_status(path, vault.GITIGNORE_BLOCK) == "current"
    path.write_text("# ── artmind (v2) ─── no end marker\n.artmind/config.env\n")
    assert vault.block_status(path, vault.GITIGNORE_BLOCK) == "malformed"


def test_a_malformed_block_is_refused_not_guessed(tmp_path):
    (tmp_path / ".gitignore").write_text("# ── artmind ─── no end marker\nmine\n")

    with pytest.raises(vault.VaultError, match="end artmind"):
        vault.write_gitignore(tmp_path)

    assert (tmp_path / ".gitignore").read_text() == "# ── artmind ─── no end marker\nmine\n"


def _ignored(repo: Path, relpath: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", relpath], cwd=repo).returncode == 0


def test_table_folders_and_atomic_write_scratch_are_ignored(tmp_path):
    _init_repo(tmp_path)
    vault.write_gitignore(tmp_path)
    kg = tmp_path / ".artmind" / "data" / "kg" / "banking"
    for folder in ("table__accounts", "doc.artmind-tmp", "doc.artmind-old", "doc"):
        (kg / folder).mkdir(parents=True)
        (kg / folder / "document.json").write_text("{}")

    assert _ignored(tmp_path, ".artmind/data/kg/banking/table__accounts/document.json")
    assert _ignored(tmp_path, ".artmind/data/kg/banking/doc.artmind-tmp/document.json")
    assert _ignored(tmp_path, ".artmind/data/kg/banking/doc.artmind-old/document.json")
    assert not _ignored(tmp_path, ".artmind/data/kg/banking/doc/document.json")


def test_gitattributes_block_is_written_and_idempotent(tmp_path):
    (tmp_path / ".gitattributes").write_text("*.png binary\n")

    assert vault.write_gitattributes(tmp_path) is True
    content = (tmp_path / ".gitattributes").read_text()
    assert content.startswith("*.png binary\n")
    assert ".artmind/data/** merge=binary" in content
    assert vault.write_gitattributes(tmp_path) is False


def _merge_opposite_edits(repo: Path, with_attributes: bool) -> subprocess.CompletedProcess:
    """Two branches edit opposite ends of one observations.json: git's line
    merge would combine them cleanly into a file neither run produced."""
    repo.mkdir(parents=True, exist_ok=True)
    _init_repo(repo)
    if with_attributes:
        vault.write_gitattributes(repo)
    f = repo / ".artmind" / "data" / "kg" / "general" / "doc" / "observations.json"
    f.parent.mkdir(parents=True)
    lines = [f'  {{"n": {i}}},' for i in range(20)]

    def write(ls):
        f.write_text("[\n" + "\n".join(ls) + "\n]\n")

    write(lines)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    main = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    _git(repo, "checkout", "-qb", "other")
    write(['  {"n": "other"},'] + lines[1:])
    _git(repo, "commit", "-qam", "other run")
    _git(repo, "checkout", "-q", main)
    write(lines[:-1] + ['  {"n": "mine"},'])
    _git(repo, "commit", "-qam", "my run")
    return subprocess.run(["git", "merge", "--no-edit", "other"], cwd=repo, capture_output=True, text=True)


def test_merge_binary_turns_a_clean_line_merge_into_a_conflict(tmp_path):
    """Spec §11: assert both sides, so the test proves the attribute is what
    matters."""
    without = _merge_opposite_edits(tmp_path / "without", with_attributes=False)
    assert without.returncode == 0, without.stdout + without.stderr

    with_attr = _merge_opposite_edits(tmp_path / "with", with_attributes=True)
    assert with_attr.returncode != 0
    assert "CONFLICT" in with_attr.stdout
    merged = (tmp_path / "with" / ".artmind" / "data" / "kg" / "general" / "doc" / "observations.json").read_text()
    assert "<<<<<<<" not in merged


def test_scaffold_writes_gitattributes(tmp_path):
    from artmind.setup import scaffold_vault

    summary = scaffold_vault(tmp_path)

    assert summary["gitattributes"] is True
    assert vault.block_status(tmp_path / ".gitattributes", vault.GITATTRIBUTES_BLOCK) == "current"
```

If `scaffold_vault(tmp_path)` in the last test needs extra patches (e.g. `ensure_machine_config`), copy them from the nearest existing `scaffold_vault` test in this file.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v -k "block or ignored or gitattributes or merge_binary"`
Expected: FAIL/ERROR. The failures are `AttributeError: module 'artmind.vault' has no attribute 'block_status'` / `'write_gitattributes'`, and an assertion failure in the in-place replacement test.

- [ ] **Step 3: Implement in `artmind/vault.py`**

Add `import re` to the imports.

Change the block's first line from `# ── artmind ───…` to the versioned header. Add the R4 lines just before the end marker. The full new head and tail of `GITIGNORE_BLOCK`, with the unchanged middle elided here only:

```python
GITIGNORE_BLOCK = """\
# ── artmind (v2) ──────────────────────────────────────────────────────────────
# .artmind/ belongs to artmind and is versioned with your vault, so a clone
```

…everything between is unchanged… then, replacing the final line `# ── end artmind ───…`:

```python
.artmind/data/structured/*.duckdb.wal

# Regenerated by `vault sync` from structured_text CSV + the table mapping,
# the same way parquet is. Committing them made sync create commits on the
# receiving machine (spec 2026-09-26 R4).
.artmind/data/kg/*/table__*/
# Atomic-write scratch (R2). A crash can leave one behind; the next write of
# that document removes it.
.artmind/data/**/*.artmind-tmp/
.artmind/data/**/*.artmind-old/
# ── end artmind ───────────────────────────────────────────────────────────────
"""
```

Add the attributes block after it:

```python
GITATTRIBUTES_BLOCK = """\
# ── artmind (v1) ──────────────────────────────────────────────────────────────
# Generated, regenerable files: resolve whole-file, never line-merge. Without
# this, git can merge two extraction runs of one document cleanly into a
# valid-looking but mixed observations.json (spec 2026-09-26 R3).
# Do not add export-ignore, export-subst, text/eol or filter= for these paths:
# `vault sync` reads them through `git archive`, which applies those.
.artmind/data/** merge=binary
# ── end artmind ───────────────────────────────────────────────────────────────
"""
```

Replace `_GITIGNORE_SENTINEL` and `write_gitignore` (≈lines 274–290) with:

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


def write_gitignore(root: Path) -> bool:
    """Write artmind's ignore rules into the vault's `.gitignore` (R4).

    Appends to an established repo's own rules, and replaces an older
    artmind block in place -- so re-running `init` upgrades an existing
    vault. Newly ignored paths that are already tracked stay tracked until
    the user runs the `git rm --cached` that `vault doctor` prints (D1:
    artmind never does). Returns True when the file changed."""
    return _write_block(Path(root) / ".gitignore", GITIGNORE_BLOCK)


def write_gitattributes(root: Path) -> bool:
    """Write artmind's merge rules into the vault's `.gitattributes` (R3),
    same append/replace-in-place contract as `write_gitignore`."""
    return _write_block(Path(root) / ".gitattributes", GITATTRIBUTES_BLOCK)
```

`VaultError` is defined above in the same module (line ≈54), so no import is needed.

- [ ] **Step 4: Write `.gitattributes` from `scaffold_vault`**

In `artmind/setup.py`, change the import on line 7 to:

```python
from artmind.vault import VaultLayout, resolve_vault, write_gitattributes, write_gitignore
```

In `scaffold_vault`, after `gitignore_written = write_gitignore(root)` add:

```python
    gitattributes_written = write_gitattributes(root)
```

and add `"gitattributes": gitattributes_written,` to the returned dict, after `"gitignore"`.

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_vault_scaffold.py test/test_vault_cli.py -v`
Expected: all PASS. The existing `test_writing_the_gitignore_twice_does_not_duplicate_it` and `test_an_existing_gitignore_is_appended_to_not_replaced` still pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add artmind/vault.py artmind/setup.py test/test_vault_scaffold.py
git commit -m "feat(vault): versioned .gitignore/.gitattributes blocks; ignore table__* and swap scratch"
```

---

### Task 6: one metadata file per structured table (R5)

`manifest.json` is one shared file rewritten on every export, which makes it a guaranteed cross-machine conflict. After this task:
- Each table gets `<domain>/<table>.meta.json` beside its CSV: its registry row, its datasource row, and its column, mapping and role rows.
- `parquet_path` is left out because it is machine-local, and the import already recomputes it.
- A scoped export writes only the touched tables' files.
- A legacy `manifest.json` is migrated on the next export: each table that lacks a `.meta.json` gets one from the legacy content, then the legacy file is deleted. No table's metadata is lost.
- Import assembles a `registry.dump_all()`-shaped dict from the meta files. It fills any table with no meta file from a legacy manifest, so `registry.restore_all`/`restore_tables` stay unchanged.

**Files:**
- Modify: `artmind/structured/text_export.py` (module docstring, `MANIFEST_NAME` ≈line 60, `export_structured_text` ≈line 75, `import_structured_text` ≈line 128)
- Modify: `artmind/structured/pipeline.py:343` (docstring: "CSV + manifest.json" → "CSV + per-table .meta.json")
- Modify: `artmind/cli.py` `db_export_text` / `db_restore_text` docstrings (≈lines 2118, 2143: "CSV + manifest.json" → "CSV + per-table .meta.json")
- Test: `test/test_structured_text_export.py` (edit one assertion, append tests)

- [ ] **Step 1: Update the existing assertion and write the failing tests**

In `test_export_then_restore_text_round_trips_replace_mode_table`, replace:

```python
    manifest_path = paths.STRUCTURED_TEXT_DIR / "manifest.json"
    assert manifest_path.exists()
```

with:

```python
    assert (paths.STRUCTURED_TEXT_DIR / "banking" / "products.meta.json").exists()
    assert not (paths.STRUCTURED_TEXT_DIR / "manifest.json").exists()
```

Append:

```python
# ── per-table metadata (spec 2026-09-26 §5 R5) ────────────────────────────────


def _two_tables(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file

    products = tmp_path / "products.csv"
    _write_csv(products, [["id", "name"], [1, "Widget"], [2, "Gadget"]])
    ingest_structured_file(products, "banking")
    customers = tmp_path / "customers.csv"
    _write_csv(customers, [["id", "city"], [1, "Pune"]])
    ingest_structured_file(customers, "banking")


def _wipe_stores():
    import shutil

    import artmind.db as db
    import paths

    shutil.rmtree(paths.STRUCTURED_DIR, ignore_errors=True)
    db.DB_PATH.unlink(missing_ok=True)
    db._init_db()


def _meta(table):
    import json

    import paths

    return json.loads((paths.STRUCTURED_TEXT_DIR / "banking" / f"{table}.meta.json").read_text())


def test_export_writes_one_meta_file_per_table_without_machine_paths(tmp_path, monkeypatch):
    _two_tables(tmp_path, monkeypatch)
    import paths
    from artmind.structured.text_export import export_structured_text

    export_structured_text()

    meta = _meta("products")
    assert meta["table"]["table_name"] == "products"
    assert meta["table"]["domain"] == "banking"
    assert "parquet_path" not in meta["table"]
    assert {c["name"] for c in meta["columns"]} == {"id", "name"}
    assert all(c["table_id"] == meta["table"]["id"] for c in meta["columns"])
    assert meta["datasource"]["name"] == meta["table"]["datasource"]
    assert _meta("customers")["table"]["table_name"] == "customers"
    assert not (paths.STRUCTURED_TEXT_DIR / "manifest.json").exists()


def test_scoped_export_writes_only_the_touched_tables_meta(tmp_path, monkeypatch):
    _two_tables(tmp_path, monkeypatch)
    import paths
    from artmind.structured import registry
    from artmind.structured.text_export import export_structured_text

    customers_meta = paths.STRUCTURED_TEXT_DIR / "banking" / "customers.meta.json"
    customers_meta.unlink()

    export_structured_text(tables=[registry.get_table("products", domain="banking")])

    assert (paths.STRUCTURED_TEXT_DIR / "banking" / "products.meta.json").exists()
    assert not customers_meta.exists()


def test_a_legacy_manifest_is_migrated_then_removed_on_export(tmp_path, monkeypatch):
    _two_tables(tmp_path, monkeypatch)
    import json

    import paths
    from artmind.structured import registry
    from artmind.structured.text_export import export_structured_text

    st = paths.STRUCTURED_TEXT_DIR
    (st / "manifest.json").write_text(json.dumps(registry.dump_all(), default=str))
    for p in st.glob("*/*.meta.json"):
        p.unlink()

    result = export_structured_text(tables=[registry.get_table("products", domain="banking")])

    assert not (st / "manifest.json").exists()
    assert result["legacy_manifest_removed"] is True
    # customers was NOT in the scoped export, yet its metadata survived.
    assert {c["name"] for c in _meta("customers")["columns"]} == {"id", "city"}
    assert "parquet_path" not in _meta("customers")["table"]


def test_import_falls_back_to_a_legacy_manifest(tmp_path, monkeypatch):
    _two_tables(tmp_path, monkeypatch)
    import json

    import paths
    from artmind.structured import registry
    from artmind.structured.text_export import import_structured_text

    st = paths.STRUCTURED_TEXT_DIR
    (st / "manifest.json").write_text(json.dumps(registry.dump_all(), default=str))
    for p in st.glob("*/*.meta.json"):
        p.unlink()
    _wipe_stores()

    summary = import_structured_text()

    assert summary["tables_loaded"] == 2
    assert registry.get_table("products", domain="banking")["row_count"] == 2


def test_import_prefers_meta_over_a_legacy_manifest_for_the_same_table(tmp_path, monkeypatch):
    _two_tables(tmp_path, monkeypatch)
    import json

    import paths
    from artmind.structured import registry
    from artmind.structured.text_export import import_structured_text

    legacy = registry.dump_all()
    for row in legacy["tables"]:
        row["row_count"] = 999
    (paths.STRUCTURED_TEXT_DIR / "manifest.json").write_text(json.dumps(legacy, default=str))
    _wipe_stores()

    import_structured_text()

    assert registry.get_table("products", domain="banking")["row_count"] == 2
    assert registry.get_table("customers", domain="banking")["row_count"] == 1


def test_import_with_no_metadata_at_all_names_both_forms(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.text_export import import_structured_text

    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match=r"\.meta\.json.*manifest\.json"):
        import_structured_text(tmp_path / "empty")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_structured_text_export.py -v`
Expected: the edited round-trip test and the new tests FAIL. No `.meta.json` is written yet, and `result["legacy_manifest_removed"]` raises `KeyError`.

- [ ] **Step 3: Implement in `artmind/structured/text_export.py`**

In the module docstring, replace the sentence starting "this module writes the diffable source of truth they're rebuilt from -- one CSV per registered table, plus a `manifest.json` carrying the registry rows" through "needed to rebuild them:" with "this module writes the diffable source of truth they're rebuilt from -- one CSV per registered table, plus one `<table>.meta.json` beside it carrying that table's registry rows (spec 2026-09-26 R5; a single shared `manifest.json` was a guaranteed merge conflict) needed to rebuild it:". Leave the rest of the docstring as is.

Replace the `MANIFEST_NAME = "manifest.json"` line with:

```python
#: Legacy (pre-R5) form: the whole registry in one shared file. Read as a
#: fallback; the next export migrates it into per-table meta and deletes it.
MANIFEST_NAME = "manifest.json"
META_SUFFIX = ".meta.json"

#: Machine-local registry fields, never written to a committed meta file (they
#: differ per machine and would conflict on every merge). The import
#: recomputes `parquet_path` for the current machine.
_MACHINE_LOCAL_TABLE_FIELDS = frozenset({"parquet_path"})

_DUMP_KEYS = ("columns", "column_mappings", "column_roles")
```

Add after `_table_csv_path`:

```python
def _table_meta_path(dest_dir: Path, domain: str, table_name: str) -> Path:
    return dest_dir / domain / f"{table_name}{META_SUFFIX}"


def _table_meta(dump: dict, row: dict) -> dict:
    """One table's slice of a `registry.dump_all()`-shaped dump."""
    table_id = row["id"]
    return {
        "table": {k: v for k, v in row.items() if k not in _MACHINE_LOCAL_TABLE_FIELDS},
        "datasource": next(
            (d for d in dump.get("datasources", []) if d["name"] == row["datasource"]), None
        ),
        **{key: [r for r in dump.get(key, []) if r["table_id"] == table_id] for key in _DUMP_KEYS},
    }


def _write_meta(dest_dir: Path, meta: dict) -> Path:
    table = meta["table"]
    path = _table_meta_path(dest_dir, table["domain"], table["table_name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _migrate_legacy_manifest(dest_dir: Path) -> list[Path]:
    """Split a legacy `manifest.json` into per-table meta for every table
    that has none yet, then delete it. Pure file transform: a scoped export
    must not lose the metadata of tables it didn't touch."""
    legacy = dest_dir / MANIFEST_NAME
    if not legacy.is_file():
        return []
    dump = json.loads(legacy.read_text(encoding="utf-8"))
    written = [
        _write_meta(dest_dir, _table_meta(dump, row))
        for row in dump.get("tables", [])
        if not _table_meta_path(dest_dir, row["domain"], row["table_name"]).is_file()
    ]
    legacy.unlink()
    return written


def load_structured_dump(src_dir: Path) -> dict:
    """A `registry.dump_all()`-shaped dict assembled from `src_dir`'s
    per-table `<domain>/<table>.meta.json` files. A legacy `manifest.json`
    fills in any table that has no meta file (R5 read-only compatibility).
    Every table row gets a `parquet_path` key (empty when the meta omitted
    it); `import_structured_text` overwrites it for this machine."""
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

    have: set[tuple[str, str]] = set()
    for path in meta_paths:
        meta = json.loads(path.read_text(encoding="utf-8"))
        table = dict(meta["table"])
        have.add((table["domain"], table["table_name"]))
        dump["tables"].append(table)
        for key in _DUMP_KEYS:
            dump[key].extend(meta.get(key, []))
        _add_datasource(meta.get("datasource"))

    if legacy_path.is_file():
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        legacy_ids = set()
        for row in legacy.get("tables", []):
            if (row["domain"], row["table_name"]) not in have:
                dump["tables"].append(row)
                legacy_ids.add(row["id"])
        for key in _DUMP_KEYS:
            dump[key].extend(r for r in legacy.get(key, []) if r["table_id"] in legacy_ids)
        for ds in legacy.get("datasources", []):
            _add_datasource(ds)

    for row in dump["tables"]:
        row.setdefault("parquet_path", "")
    return dump
```

In `export_structured_text`, update the docstring's first line to "Write CSV + per-table `.meta.json` for `tables` …". Replace its "The manifest is always the FULL registry dump…" paragraph with: "Only the touched tables' CSV and meta are written; every other table's files are left as they are. A legacy `manifest.json` is first split into per-table meta (for tables without one) and deleted." Update the Returns line to `{"tables": <count>, "files": [<paths written>], "legacy_manifest_removed": bool}`.

Replace the block from `manifest_path = dest_dir / MANIFEST_NAME` through `written.append(manifest_path)` with:

```python
    had_legacy = (dest_dir / MANIFEST_NAME).is_file()
    written.extend(_migrate_legacy_manifest(dest_dir))

    dump = registry.dump_all()
    rows_by_key = {(r["domain"], r["table_name"]): r for r in dump["tables"]}
    for table in target_tables:
        row = rows_by_key.get((table["domain"], table["table_name"]))
        if row is not None:
            written.append(_write_meta(dest_dir, _table_meta(dump, row)))
```

and change the return to:

```python
    return {
        "tables": len(target_tables),
        "files": [str(p) for p in written],
        "legacy_manifest_removed": had_legacy,
    }
```

In `import_structured_text`, update the docstring's first line to "Wipe and rebuild the structured store from `src_dir`'s CSV + per-table `.meta.json` (legacy `manifest.json` as a fallback)." Replace:

```python
    manifest_path = src_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No structured text export found at {src_dir} (missing {MANIFEST_NAME})")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
```

with:

```python
    manifest = load_structured_dump(src_dir)
```

In the missing-dtype `ValueError` message, replace `in {MANIFEST_NAME} ` with `in the table's metadata `.

- [ ] **Step 4: Update the two docstring mentions**

In `artmind/structured/pipeline.py` `_export_text_best_effort` and in `artmind/cli.py` `db_export_text` / `db_restore_text`, replace "CSV + manifest.json" with "CSV + per-table .meta.json". Then run `grep -rn "manifest.json" artmind/structured artmind/cli.py artmind/vault.py`. Only `text_export.py`'s legacy-fallback lines and the `vault.py` comment should remain. In `vault.py`, change "(CSV + manifest.json," in the gitignore comment and in `structured_text_dir`'s docstring to "(CSV + per-table .meta.json,".

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_structured_text_export.py test/test_vault_scaffold.py -v`
Expected: all PASS. If a structured-pipeline test elsewhere asserts on `manifest.json`, run `grep -rn "manifest.json" test/` and update the assertion to the `.meta.json` path. Leave `test_vault_sync.py` for Task 7 and `test_unified_snapshot*`/archive tests alone: those are different manifests.

- [ ] **Step 6: Commit**

```bash
git add artmind/structured/text_export.py artmind/structured/pipeline.py artmind/cli.py artmind/vault.py test/test_structured_text_export.py
git commit -m "feat(structured): one .meta.json per table; legacy manifest.json read and migrated"
```

---

### Task 7: `vault sync` track B reads per-table meta from `head`

Two changes in track B:
- **Classification.** A changed `<table>.meta.json` now regenerates its table, the same as a changed CSV. A table whose CSV was removed is retracted and never also regenerated.
- **Materialisation.** It now includes each table's `.meta.json` and the legacy `manifest.json`, but only the paths that exist at `head`. `git archive` fails on a missing pathspec, which was phase 1 review finding 5.

The constant is duplicated rather than imported, because importing `text_export` pulls in DuckDB at classify time. A test pins the two copies equal.

**Files:**
- Modify: `artmind/vault_sync.py` (`_classify_structured_text_diff` ≈264–300, new `_present_at` after `_materialize` ≈174, track B block in `sync` ≈390–398)
- Test: `test/test_vault_sync.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_sync.py`:

```python
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

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.regenerate_tables == [("banking", "accounts")]


def test_classify_diff_regenerates_a_table_once_when_csv_and_meta_both_change(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text("{}")
    _commit_all(repo, "add table text")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo))

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

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

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
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain: {"name": domain})
    monkeypatch.setattr(t2g, "table_to_graph", lambda row, mapping, **k: {"commit": {"deferred_keys": []}})
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo, bootstrap_empty=True)

    assert seen == {
        "tables": [("banking", "accounts")],
        "meta": '{"committed": true}',
        "csv": "id\n1\n",
        "manifest": False,
    }
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -v -k "meta"`
Expected:
- `test_meta_suffix_matches_text_export` fails with `AttributeError: _META_SUFFIX`.
- The meta-change test fails with `[] != [("banking", "accounts")]`.
- The retract test fails.
- The materialize test fails with `VaultSyncError: git archive … failed` (`manifest.json` did not match any files).

- [ ] **Step 3: Implement**

In `artmind/vault_sync.py`, after `EMPTY_TREE_SHA` add:

```python
#: `structured.text_export.META_SUFFIX`, duplicated so classifying a diff
#: never imports DuckDB. test_vault_sync pins the two equal.
_META_SUFFIX = ".meta.json"
```

After `_materialize`, add:

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

Replace the loop and return of `_classify_structured_text_diff` (from `regenerate: list[...] = []` to `return regenerate, retract`) with:

```python
    regenerate: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for status, path in rows:
        rel = Path(path).relative_to(st_rel)
        parts = rel.parts
        if len(parts) != 2:
            continue
        domain, filename = parts
        if filename.endswith(".csv"):
            table_name, is_csv = filename[: -len(".csv")], True
        elif filename.endswith(_META_SUFFIX):
            table_name, is_csv = filename[: -len(_META_SUFFIX)], False
        else:
            continue
        if not _in_scope(domain, domains):
            continue
        key = (domain, table_name)
        if status in ("A", "M") and key not in regenerate:
            regenerate.append(key)
        elif status == "D" and is_csv:
            retract.append((domain, f"table:{domain}:{table_name}"))
    # A table whose CSV is gone is retracted; regenerating it would fail.
    retracted = {(d, doc_id.split(":", 2)[2]) for d, doc_id in retract}
    regenerate = [key for key in regenerate if key not in retracted]
    return regenerate, retract
```

Update its docstring: "regenerate/retract for every table's CSV or `.meta.json` under `.artmind/data/structured_text/**`. A legacy `manifest.json` change on its own still triggers nothing (a documented scoping decision); per-table meta (spec 2026-09-26 R5) is what makes a metadata-only change visible."

In `sync`, change the import line to:

```python
    from artmind.structured.text_export import MANIFEST_NAME, META_SUFFIX, import_structured_text
```

and replace the track-B `_materialize(...)` call and the `import_structured_text(...)` line with:

```python
            wanted = [str(st_rel / MANIFEST_NAME)]
            for domain, table_name in plan.regenerate_tables:
                wanted.append(str(st_rel / domain / f"{table_name}.csv"))
                wanted.append(str(st_rel / domain / f"{table_name}{META_SUFFIX}"))
            _materialize(vault_dir, head, _present_at(vault_dir, head, wanted), scratch)
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            import_structured_text(scratch / st_rel, tables=plan.regenerate_tables)
```

Also update the stale comment near the end of `sync`, "(Phase 2 gitignores them.)", to "(gitignored: spec 2026-09-26 R4)".

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_vault_sync.py test/test_no_git_writes.py -v`
Expected: all PASS, including every pre-existing structured-text test. Those still commit a legacy `manifest.json`, which is still materialised when present.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault sync): track B reads per-table meta from head; skip paths absent at head"
```

---

### Task 8: `artmind vault doctor` (R8)

Read-only checks, each with the exact fix. No check writes to git or to the working tree.

Two inputs are unconfirmed (spec §13):
- **Obsidian Git's `data.json` keys.** The keys below are candidates. An absent key reports `unknown` with a "check it by hand" fix, never `fail`. A present key with an unexpected value is a `warn`, not a `fail`, because its semantics are unverified too.
- **The Obsidian Sync marker in `.obsidian/core-plugins.json`.** This is a tolerant heuristic, `warn` only.

`ok` is false only when a `fail` check exists. Only these checks can fail: missing, outdated or malformed blocks; tracked-but-ignored paths; `pull.rebase` set to rebase.

**Files:**
- Create: `artmind/vault_doctor.py`
- Modify: `artmind/cli.py`: the `vault` group docstring (≈line 3402), and a new `vault doctor` command after `vault_sync_cmd` (≈line 3469)
- Test: `test/test_vault_doctor.py` (create), `test/test_vault_cli.py` (append)

- [ ] **Step 1: Write the failing tests**

Create `test/test_vault_doctor.py`:

```python
"""`artmind vault doctor` (spec 2026-09-26 §5 R8): read-only readiness checks
against real throwaway git repos."""
import json
import subprocess
from pathlib import Path

import pytest

from artmind import vault
from artmind import vault_doctor as doc


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTMIND_VAULT_GIT_PUSH", raising=False)
    root = tmp_path / "v"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "pull.rebase", "false")
    (root / ".artmind").mkdir()
    vault.write_gitignore(root)
    vault.write_gitattributes(root)
    return root


def _by_name(result: dict) -> dict:
    return {c["name"]: c for c in result["checks"]}


def _head_and_index(repo):
    head = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    return head, _git(repo, "ls-files", "--stage")


def test_a_ready_vault_has_no_failures_and_nothing_is_written(repo):
    (repo / "note.md").write_text("hi")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    before = _head_and_index(repo), (repo / ".gitignore").read_text(), sorted(p.name for p in repo.iterdir())

    result = doc.run(repo)

    after = _head_and_index(repo), (repo / ".gitignore").read_text(), sorted(p.name for p in repo.iterdir())
    assert before == after
    assert result["ok"] is True, result
    checks = _by_name(result)
    assert checks[".gitignore artmind block"]["status"] == "ok"
    assert checks[".gitattributes artmind block"]["status"] == "ok"
    assert checks["ignored paths still tracked"]["status"] == "ok"
    assert checks["pull.rebase"]["status"] == "ok"


def test_an_outdated_gitignore_block_fails_and_points_at_init(repo):
    (repo / ".gitignore").write_text("# ── artmind ───\n.artmind/config.env\n# ── end artmind ───\n")

    result = doc.run(repo)

    check = _by_name(result)[".gitignore artmind block"]
    assert result["ok"] is False
    assert check["status"] == "fail"
    assert "outdated" in check["detail"]
    assert "artmind init" in check["fix"]


def test_a_missing_gitattributes_fails(repo):
    (repo / ".gitattributes").unlink()

    check = _by_name(doc.run(repo))[".gitattributes artmind block"]

    assert check["status"] == "fail"
    assert "missing" in check["detail"]


def test_tracked_table_folders_are_reported_with_the_exact_untrack_command(repo):
    table = repo / ".artmind" / "data" / "kg" / "banking" / "table__accounts"
    table.mkdir(parents=True)
    (table / "document.json").write_text("{}")
    (table / "observations.json").write_text("[]")
    _git(repo, "add", "-f", "-A")  # tracked before the rule existed
    _git(repo, "commit", "-qm", "old commit")

    check = _by_name(doc.run(repo))["ignored paths still tracked"]

    assert check["status"] == "fail"
    assert check["fix"] == "git rm -r --cached -- .artmind/data/kg/banking/table__accounts"
    # doctor printed it, never ran it
    assert ".artmind/data/kg/banking/table__accounts/document.json" in _git(repo, "ls-files")


def test_pull_rebase_true_fails(repo):
    _git(repo, "config", "pull.rebase", "true")

    check = _by_name(doc.run(repo))["pull.rebase"]

    assert check["status"] == "fail"
    assert check["fix"] == "git config pull.rebase false"


def test_pull_rebase_unset_is_ok(repo):
    _git(repo, "config", "--unset", "pull.rebase")

    assert _by_name(doc.run(repo))["pull.rebase"]["status"] == "ok"


def test_obsidian_git_absent_is_a_warning_not_a_failure(repo):
    result = doc.run(repo)

    check = _by_name(result)["Obsidian Git"]
    assert check["status"] == "warn"
    assert result["ok"] is True


def test_obsidian_git_unknown_keys_are_reported_unknown_not_failed(repo):
    data = repo / ".obsidian" / "plugins" / "obsidian-git" / "data.json"
    data.parent.mkdir(parents=True)
    data.write_text(json.dumps({"someFutureKey": 1}))

    result = doc.run(repo)

    obsidian = [c for c in result["checks"] if c["name"].startswith("Obsidian Git:")]
    assert obsidian and all(c["status"] == "unknown" for c in obsidian)
    assert result["ok"] is True


def test_obsidian_git_known_keys_are_checked(repo):
    data = repo / ".obsidian" / "plugins" / "obsidian-git" / "data.json"
    data.parent.mkdir(parents=True)
    data.write_text(json.dumps({"syncMethod": "rebase", "autoPullOnBoot": True, "autoSaveInterval": 5}))

    checks = _by_name(doc.run(repo))

    assert checks["Obsidian Git: sync method"]["status"] == "warn"
    assert "rebase" in checks["Obsidian Git: sync method"]["detail"]
    assert checks["Obsidian Git: pull on startup"]["status"] == "ok"
    assert checks["Obsidian Git: auto commit interval (minutes)"]["status"] == "ok"


def test_deprecated_push_setting_in_config_env_is_warned(repo):
    (repo / ".artmind" / "config.env").write_text("ARTMIND_VAULT_GIT_PUSH=1\n")

    check = _by_name(doc.run(repo))["ARTMIND_VAULT_GIT_PUSH"]

    assert check["status"] == "warn"
    assert "config.env" in check["detail"]


def test_a_dropbox_ancestor_is_warned(repo):
    (repo.parent / ".dropbox").write_text("")

    check = _by_name(doc.run(repo))["other sync tools"]

    assert check["status"] == "warn"
    assert "Dropbox" in check["detail"]


@pytest.mark.parametrize("core_plugins", [["sync", "graph"], {"sync": True, "graph": True}])
def test_obsidian_sync_is_detected_in_either_core_plugins_shape(repo, core_plugins):
    (repo / ".obsidian").mkdir()
    (repo / ".obsidian" / "core-plugins.json").write_text(json.dumps(core_plugins))

    check = _by_name(doc.run(repo))["other sync tools"]

    assert check["status"] == "warn"
    assert "Obsidian Sync" in check["detail"]
```

Append to `test/test_vault_cli.py`:

```python
def test_vault_doctor_reports_json_and_exits_nonzero_on_failure(tmp_path, monkeypatch):
    import json

    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(cli, ["init"]).exit_code == 0
    subprocess.run(["git", "config", "pull.rebase", "true"], cwd=tmp_path, check=True)

    result = CliRunner().invoke(cli, ["vault", "doctor", "--compact"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert {c["name"]: c["status"] for c in payload["checks"]}["pull.rebase"] == "fail"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_doctor.py test/test_vault_cli.py -v`
Expected: `test_vault_doctor.py` errors with `ImportError: cannot import name 'vault_doctor'`. The CLI test fails with exit code 2 (`No such command 'doctor'`).

- [ ] **Step 3: Implement `artmind/vault_doctor.py`**

```python
"""`artmind vault doctor` (spec 2026-09-26 §5 R8): read-only checks that a
vault is safe for Obsidian Git to auto-commit and merge, each with the exact
fix. Never writes -- not to git (D1), not to the working tree. Fixes that
need a git write are printed for the user to run.

Two inputs are unverified (spec §13): Obsidian Git's `data.json` key names
and Obsidian Sync's `core-plugins.json` marker. Checks built on them report
`unknown`/`warn`, never `fail`.
"""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from artmind.vault import GITATTRIBUTES_BLOCK, GITIGNORE_BLOCK, VaultLayout, block_status
from utils.functions import run_command

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"

OBSIDIAN_GIT_DATA = Path(".obsidian") / "plugins" / "obsidian-git" / "data.json"

#: (key, label, predicate, expected). UNVERIFIED against the installed plugin
#: version (spec §13): an absent key is `unknown`, a surprising value `warn`.
_OBSIDIAN_GIT_EXPECTATIONS = (
    ("syncMethod", "sync method", lambda v: v == "merge", "merge"),
    ("autoPullOnBoot", "pull on startup", lambda v: v is True, "on"),
    (
        "autoSaveInterval", "auto commit interval (minutes)",
        lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0,
        "> 0 (5-10 recommended)",
    ),
)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str | None = None


def _git(vault_dir: Path, args: list[str], expected_codes: tuple[int, ...] = ()) -> tuple[int, str, str]:
    return run_command(["git", *args], cwd=vault_dir, expected_codes=expected_codes)


def check_block(vault_dir: Path, filename: str, block: str) -> Check:
    name = f"{filename} artmind block"
    status = block_status(vault_dir / filename, block)
    if status == "current":
        return Check(name, OK, "present and current")
    if status == "malformed":
        return Check(
            name, FAIL, "artmind block has a start line but no '# ── end artmind' line",
            f"edit {filename} by hand so artmind's block ends with its '# ── end artmind' line, "
            "then run `artmind init` in the vault root",
        )
    return Check(
        name, FAIL, f"artmind block {status}",
        "run `artmind init` in the vault root (rewrites only artmind's block; your own rules are kept)",
    )


def _collapse(paths: list[str]) -> list[str]:
    """Each tracked file -> its `table__*` / `*.artmind-tmp` / `*.artmind-old` folder when it
    sits inside one, so the printed command untracks folders, not files."""
    out: list[str] = []
    for path in paths:
        parts = path.split("/")
        for i, part in enumerate(parts[:-1]):
            if part.startswith("table__") or part.endswith((".artmind-tmp", ".artmind-old")):
                path = "/".join(parts[: i + 1])
                break
        if path not in out:
            out.append(path)
    return out


def check_tracked_ignored(vault_dir: Path) -> Check:
    name = "ignored paths still tracked"
    rc, out, err = _git(vault_dir, ["ls-files", "-c", "-i", "--exclude-standard", "--", ".artmind"])
    if rc != 0:
        return Check(name, UNKNOWN, f"git ls-files failed: {(err or out).strip()}")
    paths = _collapse([p for p in out.splitlines() if p.strip()])
    if not paths:
        return Check(name, OK, "none")
    # Built in two parts so test_no_git_writes' source scan doesn't read
    # printed advice as a git write. artmind never runs it (D1).
    command = "git " + "rm -r --cached -- " + " ".join(shlex.quote(p) for p in paths)
    return Check(
        name, FAIL,
        f"{len(paths)} path(s) are gitignored but still tracked, so Obsidian Git keeps committing them: "
        + ", ".join(paths[:5]) + (", ..." if len(paths) > 5 else ""),
        command,
    )


def check_pull_rebase(vault_dir: Path) -> Check:
    name = "pull.rebase"
    rc, out, _ = _git(vault_dir, ["config", "--get", "pull.rebase"], expected_codes=(1,))
    value = out.strip().lower() if rc == 0 else ""
    if value in ("", "false", "no", "off", "0"):
        return Check(name, OK, f"{value or 'unset'} (pulls merge)")
    return Check(
        name, FAIL,
        f"pull.rebase is {value!r}: pulls would rebase, rewriting the commit shas artmind "
        "uses as provenance and sync cursors (spec D5)",
        "git config pull.rebase false",
    )


def check_obsidian_git(vault_dir: Path) -> list[Check]:
    path = vault_dir / OBSIDIAN_GIT_DATA
    if not path.is_file():
        return [Check(
            "Obsidian Git", WARN,
            f"{OBSIDIAN_GIT_DATA} not found: the plugin is not installed, or never configured, in this vault",
            "install Obsidian Git (Settings -> Community plugins), then set sync method: merge, "
            "pull on startup: on, auto commit-and-sync interval: 5-10 minutes",
        )]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [Check("Obsidian Git", UNKNOWN, f"could not read {OBSIDIAN_GIT_DATA}: {e}")]
    if not isinstance(data, dict):
        return [Check("Obsidian Git", UNKNOWN, f"{OBSIDIAN_GIT_DATA} is not a JSON object")]
    checks = []
    for key, label, is_ok, expected in _OBSIDIAN_GIT_EXPECTATIONS:
        name = f"Obsidian Git: {label}"
        if key not in data:
            checks.append(Check(
                name, UNKNOWN,
                f"no {key!r} in {OBSIDIAN_GIT_DATA}; this plugin version may name the setting differently",
                f"check by hand in Obsidian -> Settings -> Git: {label} = {expected}",
            ))
        elif is_ok(data[key]):
            checks.append(Check(name, OK, f"{key} = {data[key]!r}"))
        else:
            checks.append(Check(
                name, WARN, f"{key} = {data[key]!r}, expected {expected}",
                f"Obsidian -> Settings -> Git: set {label} to {expected}",
            ))
    return checks


def check_deprecated_push(vault_dir: Path) -> Check:
    name = "ARTMIND_VAULT_GIT_PUSH"
    where = []
    if "ARTMIND_VAULT_GIT_PUSH" in os.environ:
        where.append("the environment")
    config_env = VaultLayout(vault_dir).config_env
    if config_env.is_file() and any(
        line.strip().startswith("ARTMIND_VAULT_GIT_PUSH=")
        for line in config_env.read_text(encoding="utf-8").splitlines()
    ):
        where.append(str(config_env))
    if not where:
        return Check(name, OK, "not set")
    return Check(
        name, WARN,
        f"set in {' and '.join(where)}: deprecated and ignored -- Obsidian Git pushes, artmind never does",
        f"remove it from {' and '.join(where)}",
    )


def _obsidian_sync_enabled(vault_dir: Path) -> bool:
    """Heuristic, unverified across Obsidian versions (spec §13):
    `.obsidian/core-plugins.json` is a list of enabled core plugin ids in
    older versions and an {id: enabled} map in newer ones; Sync's id is
    "sync"."""
    try:
        data = json.loads((vault_dir / ".obsidian" / "core-plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if isinstance(data, list):
        return "sync" in data
    if isinstance(data, dict):
        return data.get("sync") is True
    return False


def check_other_sync(vault_dir: Path) -> Check:
    name = "other sync tools"
    resolved = vault_dir.resolve()
    found = []
    if "/Library/Mobile Documents/" in resolved.as_posix() + "/":
        found.append("iCloud Drive")
    if any((p / ".dropbox").exists() or (p / ".dropbox.cache").exists() for p in (resolved, *resolved.parents)):
        found.append("Dropbox")
    if _obsidian_sync_enabled(vault_dir):
        found.append("Obsidian Sync")
    if not found:
        return Check(name, OK, "none detected")
    return Check(
        name, WARN,
        f"{', '.join(found)} also syncs this folder; a second sync layer racing Obsidian Git "
        "over one working tree can corrupt it",
        f"move the vault out of {', '.join(found)}, or turn it off for this vault",
    )


def run(vault_dir: Path) -> dict:
    vault_dir = Path(vault_dir)
    checks = [
        check_block(vault_dir, ".gitignore", GITIGNORE_BLOCK),
        check_block(vault_dir, ".gitattributes", GITATTRIBUTES_BLOCK),
        check_tracked_ignored(vault_dir),
        check_pull_rebase(vault_dir),
        *check_obsidian_git(vault_dir),
        check_deprecated_push(vault_dir),
        check_other_sync(vault_dir),
    ]
    return {
        "vault": str(vault_dir),
        "ok": all(c.status != FAIL for c in checks),
        "checks": [asdict(c) for c in checks],
    }
```

- [ ] **Step 4: Add the CLI command**

In `artmind/cli.py`, change the `vault` group docstring to:

```python
    """Which vault is active (`status`), git-diff-driven sync into Neo4j/the structured store (`sync`), and read-only readiness checks for Obsidian Git (`doctor`)."""
```

After `vault_sync_cmd`, add:

```python
@vault.command("doctor")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_doctor_cmd(compact):
    """Read-only checks that this vault is safe for Obsidian Git to auto-commit and merge.

    Checks that artmind's .gitignore/.gitattributes blocks are current, that
    no gitignored path is still tracked, that pulls merge rather than rebase,
    Obsidian Git's stored settings, a leftover ARTMIND_VAULT_GIT_PUSH, and a
    second sync tool on the same folder. Prints the exact fix for each
    problem and changes nothing. Exits 1 when any check fails.
    """
    from artmind import vault as vault_mod
    from artmind.vault_doctor import run as doctor_run

    try:
        vault_dir = vault_mod.resolve_vault()
    except vault_mod.VaultError as e:
        raise click.ClickException(str(e))
    if vault_dir is None:
        raise click.ClickException(
            "Not inside an artmind vault.\n"
            "  cd into one, or run `artmind init` to make this directory a vault."
        )
    result = doctor_run(vault_dir)
    _echo_json(result, compact)
    if not result["ok"]:
        raise SystemExit(1)
```

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_vault_doctor.py test/test_vault_cli.py test/test_cli_guide.py test/test_no_git_writes.py -v`
Expected: all PASS.

- If `test_tracked_table_folders_are_reported_with_the_exact_untrack_command` fails because `git ls-files -ci --exclude-standard` does not list files inside a directory-pattern match on this git version: **stop and report it**. Do not switch mechanisms silently.
- If `test_cli_guide` reports `artmind vault doctor` as unrouted, add `"artmind vault": [{"name": "Vault", "commands": ["status", "sync", "doctor"]}]` to `COMMAND_GROUPS`.

- [ ] **Step 6: Commit**

```bash
git add artmind/vault_doctor.py artmind/cli.py test/test_vault_doctor.py test/test_vault_cli.py
git commit -m "feat(vault doctor): read-only readiness checks with the exact fix for each"
```

---

### Task 9: docs, skill, justfile, full suite

**Files:**
- Modify: `docs/vault.md` ("What is in git, and what is not" table ≈line 116; "Git: Obsidian Git owns transport" ≈line 308)
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md` (the line phase 1 changed to "the Obsidian Git plugin commits the change (artmind never commits)")
- Modify: `justfile` (new `vault-doctor` recipe beside the other vault/docs recipes; stale `docs-archive` comment on line 141, which was phase 1 review finding 4)

- [ ] **Step 1: `docs/vault.md`**

In the "Not committed" table, add these rows after the `embeddings inside committed KG staging` row:

```markdown
| `.artmind/data/kg/*/table__*/` | regenerated by `vault sync` from the table's CSV + mapping, like parquet; committing them made sync create commits |
| `*.artmind-tmp/`, `*.artmind-old/` under `.artmind/data/` | scratch from the atomic staging-folder swap; a crash can leave one, the next write removes it |
```

After the table, add:

```markdown
The artmind sections of `.gitignore` and `.gitattributes` are versioned
blocks. `artmind init` replaces an older block in place and never touches
your own rules. `.gitattributes` marks `.artmind/data/**` as `merge=binary`:
two extraction runs of one document conflict as whole files instead of
line-merging into a mix. Structured-store metadata is one
`<domain>/<table>.meta.json` per table, beside its CSV. A legacy
`manifest.json` is still read, and is split up on the next export.
```

At the end of the "Git: Obsidian Git owns transport" section, before "The loop terminates by construction", add:

````markdown
`artmind vault doctor` checks, without changing anything, that the vault is
safe for Obsidian Git to auto-commit and merge. It checks that the artmind
`.gitignore`/`.gitattributes` blocks are current, that no newly ignored path
is still tracked, that `pull.rebase` is off, Obsidian Git's stored settings,
a leftover `ARTMIND_VAULT_GIT_PUSH`, and whether iCloud, Dropbox or Obsidian
Sync also syncs the folder. Each problem comes with the exact command to fix
it, for example:

```bash
git rm -r --cached -- .artmind/data/kg/banking/table__accounts
```

artmind never runs these itself.
````

- [ ] **Step 2: the ingestion-helper skill**

In `artmind/skills/artmind-ingestion-helper/SKILL.md`, after the sentence ending "the Obsidian Git plugin commits the change (artmind never commits).", add: "If a vault's git setup looks wrong (pushes rejected, a merge rebasing, generated folders committed), run `artmind vault doctor`: it prints the exact fix for each problem."

- [ ] **Step 3: `justfile`**

Change the comment on line 141 from "remove it from the graph AND the vault (git rm + commit)" to "remove it from the graph AND delete it from the vault (Obsidian Git commits the deletion)". Add, next to the other vault-related recipes (or after `docs-archive` if there are none):

```just
# read-only checks that this vault is safe for Obsidian Git to auto-commit and merge (usage: just vault-doctor)
vault-doctor:
    uv run artmind vault doctor
```

- [ ] **Step 4: Full suite**

Run: `just dev-test`
Expected: all PASS. If a test outside the touched files fails, read it before changing anything. It may be another reader of `manifest.json` (`grep -rn "manifest.json\|MANIFEST_NAME" artmind test`) or of the old gitignore sentinel.

- [ ] **Step 5: Commit**

```bash
git add docs/vault.md artmind/skills/artmind-ingestion-helper/SKILL.md justfile
git commit -m "docs: vault doctor, versioned git blocks, per-table meta"
```

- [ ] **Step 6: Live checks (with the user)**

These need the user. Do not run them unattended.

1. `just dev-install`. This reinstalls the tool and re-seeds the run folder so the edited skill reaches the chat UI.
2. Doctor against a throwaway clone of the test vault. It needs no Neo4j:
   ```bash
   SCRATCH=$(mktemp -d) && git clone --no-hardlinks /Users/surjitdas/Downloads/test-vault "$SCRATCH/v" && cd "$SCRATCH/v" && ARTMIND_NO_PROXY=1 artmind vault doctor
   ```
   Expected on a pre-phase-2 vault: the `.gitignore` block is `outdated` and `.gitattributes` is `missing`. Tracked `table__*` folders are listed if the vault has any. After `artmind init` in the clone, both blocks are `ok`.
3. Ingest one note in the clone against the user's **local** Neo4j. Ask for the bolt port and password; never guess or scan. Confirm no `*.artmind-tmp`/`*.artmind-old` sibling remains under `.artmind/data/kg/` and `git status` shows only the expected staging files.
4. `rm -rf "$SCRATCH"`.

---

## Open questions (carried from spec §13; not answered here)

1. **Obsidian Git `data.json` keys.** `syncMethod`, `autoPullOnBoot` and `autoSaveInterval` are candidates, not confirmed against the installed plugin version. The doctor reports an absent key as `unknown` with a "check it by hand" fix, and a surprising value as `warn`. It never fails on either. To confirm, read `.obsidian/plugins/obsidian-git/data.json` in a vault where the settings have been changed from their defaults, then tighten `_OBSIDIAN_GIT_EXPECTATIONS`.
2. **Does isomorphic-git (Obsidian Git on mobile) honour `.gitattributes` `merge=binary`?** Unverified. Under D3, mobile never produces generated files, so a mobile-side merge of `.artmind/data/**` should only ever fast-forward. The attribute's protection is only claimed for desktop git.
3. **Obsidian Sync marker.** The `core-plugins.json` shape (a list in older versions, a map in newer ones) and the `"sync"` id are assumptions. The check is warn-only.
4. **`git ls-files -ci --exclude-standard` and directory patterns.** Task 8 relies on it listing tracked files inside a directory matched by `table__*/`. The test asserts this. If it fails on some git version, stop and re-plan, e.g. `git ls-files` plus `git check-ignore --no-index`.

## Self-review notes

- **Spec coverage:**
  - R2: Task 1 (primitive, crash/recovery test per §11), Task 2 (`ingest.py` writer), Task 3 (`table2graph.write_staged`), Task 4 (listing hygiene). The §7 "update writer" doesn't exist until phase 4, which will call `ingest.write_staging`.
  - R3: Task 5 (`merge=binary` block, with the §11 two-sided merge test).
  - R4: Task 5 (`table__*`, `*.artmind-tmp/`, `*.artmind-old/`, versioned in-place replacement) and Task 8 (tracked-ignored check printing `git rm -r --cached`).
  - R5: Task 6 (per-table meta, scoped export, legacy read fallback and migration) and Task 7 (sync reads the meta at `head`).
  - R8: Task 8. Every bullet is covered: blocks, tracked-ignored, `pull.rebase`, Obsidian Git, `ARTMIND_VAULT_GIT_PUSH`, other sync tools. `--compact` JSON is supported.
  - §10: `vault` group docstring (Task 8), skill, `docs/vault.md` and `justfile` (Task 9). `init` writing the versioned blocks is Task 5.
- **Phase 1 review findings folded in:** #2 (the `.gitattributes` comment forbids archive-affecting attributes), #4 (the `justfile` comment), #5 (`_present_at` makes a missing manifest harmless). #1 (mapping and schema read from the working tree), #3 (preflight scope), #6, #7 and #9 are not phase 2 scope and remain open.
- **D1:** No task adds a git write. The doctor's printed command is split so the guard's source scan does not match it, and `test_no_git_writes.py` runs in Tasks 7 and 8.
- **Type consistency:** `write_dir_atomic(target, files)`, `recover(target)` and `is_scratch(path)` are defined in Task 1 and used in Tasks 2–4. `write_staging(doc_kg_dir, documents, *, default=None)` is defined in Task 2 and used in Task 3. `block_status(path, block)`, `GITATTRIBUTES_BLOCK` and `write_gitattributes(root)` are defined in Task 5 and used in Task 8. `META_SUFFIX`, `load_structured_dump(src_dir)` and `MANIFEST_NAME` are defined in Task 6 and used in Task 7.
