# Vault-Resident Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop copying a binary that already lives in the vault, and put the images docling extracts next to the markdown that references them — so a `.pptx` you drop in your vault is converted in place, its markdown and images are committed, and the binary itself needs no second copy.

**Architecture:** The copy in `documents/originals/` is not a backup — it is the **change-detection baseline**: `binary_changed` compares the incoming file's hash against the previously-copied one. Removing the copy therefore requires replacing that baseline first, which this plan does by recording the source hash in the derived markdown's own frontmatter as `_source_sha256` — exactly symmetric to the existing `_derived_sha256`, and it travels with the document into git. Only then is the copy removable, and only for sources that already live in the vault.

**Tech Stack:** Python 3.14, Click (rich_click), pytest, `uv`, `just`.

**Follows:** [2026-08-30-vault-foundation.md](./2026-08-30-vault-foundation.md) and [2026-08-30-ingest-manifest.md](./2026-08-30-ingest-manifest.md).

**Read before starting:** [docs/vault.md](../../vault.md) ("What is in git, and what is not", "Two duplications this removes"), [docs/stores-and-repos.md](../../stores-and-repos.md) (flow A), and [docs/document-identity.md](../../document-identity.md) ("Derived-markdown promotion") — that last one explains why `binary_changed` and `markdown_edited` are independent signals, which Task 2 must not break.

---

## Scope decisions, and why

Two findings from reading the code changed what this plan should contain. Both are worth understanding before starting.

**1. `documents/markdowns/` stays, for now.** The roadmap said to delete it in favour of `_derived/`. `MARKDOWNS_DIR` has **eleven** consumers (`ingest.py`, `temporal.py`, `document_identity.py`, `setup.py`, and seven test modules), and removing it is pure de-duplication with no user-visible benefit — nothing a user can see changes. It is deferred to its own plan rather than bundled into a behaviour change.

**2. `VaultLayout` adopts `paths.py`'s names, not the reverse.** The mismatch recorded in `docs/vault.md`'s "Known gaps" is real, but the fix runs the opposite way from what that note implies. `VaultLayout` invented tidier names (`data/snapshots`, `data/jobs`, `data/originals`); `paths.py` uses the names the system has always used (`data/graph_snapshot`, `data/ingestion_jobs`, `data/documents/originals`). Renaming toward `VaultLayout` would be a **data migration for every existing install** — this developer's own `~/artmind_data/graph_snapshot` holds 467 MB — bought for nothing but prettier names that were never load-bearing. So `VaultLayout` is corrected to describe reality, `paths.py` derives from it so they cannot diverge again, and `docs/vault.md`'s layout section is corrected to match.

---

## File Structure

| File | Responsibility |
|---|---|
| `artmind/vault.py` (modify) | `VaultLayout` becomes accurate: real directory names, no aspirational entries. |
| `paths.py` (modify) | Derives its data-dir constants from `VaultLayout` instead of rebuilding them, so the two cannot drift. |
| `artmind/document_identity.py` (modify) | `_source_sha256` joins the frontmatter contract. |
| `artmind/ingest.py` (modify) | Record `_source_sha256`; compute `binary_changed` from it; skip the copy for vault-resident sources; write artifacts beside the derived markdown. |
| `test/test_vault_layout_parity.py` (create) | The two layouts agree — the regression guard for the drift this plan closes. |
| `test/test_ingest_binary_derived.py` (modify) | Existing binary-path tests; extend rather than replace. |
| `test/test_vault_resident_sources.py` (create) | No copy for vault-resident sources; copy retained for outside ones; artifacts in the vault. |

---

## Task 1: Make `VaultLayout` accurate, and have `paths.py` derive from it

**Files:**
- Modify: `artmind/vault.py`, `paths.py`
- Test: `test/test_vault_layout_parity.py` (create), `test/test_vault.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_vault_layout_parity.py`:

```python
"""`VaultLayout` and `paths.py` must describe the SAME layout.

They drifted once already: `VaultLayout` declared `data/snapshots`,
`data/jobs` and `data/originals` while `paths.py` used `data/graph_snapshot`,
`data/ingestion_jobs` and `data/documents/originals`. Nothing read the
`VaultLayout` names, so nothing broke — but reaching for `layout.snapshots_dir`
returned a path the system does not use, which is a landmine rather than a bug.

This test is the guard. If it fails, one of the two moved.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = (
    "import json, paths;"
    "from artmind.vault import VaultLayout;"
    "L = VaultLayout(paths.ARTMIND_VAULT_DIR);"
    "print(json.dumps({"
    "'originals': [str(paths.ORIGINALS_DIR), str(L.originals_dir)],"
    "'kg': [str(paths.KG_DIR), str(L.kg_dir)],"
    "'registry': [str(paths.DB_PATH), str(L.registry_db)],"
    "'structured': [str(paths.STRUCTURED_DIR), str(L.structured_dir)],"
    "'snapshots': [str(paths.GRAPH_SNAPSHOT_DIR), str(L.snapshots_dir)],"
    "'jobs': [str(paths.JOBS_DIR), str(L.jobs_dir)],"
    "'markdowns': [str(paths.MARKDOWNS_DIR), str(L.markdowns_dir)],"
    "'documents': [str(paths.DOCUMENTS_DIR), str(L.documents_dir)],"
    "'structured_snap': [str(paths.STRUCTURED_SNAPSHOT_DIR), str(L.structured_snapshots_dir)],"
    "'worker_pid': [str(paths.WORKER_PID_FILE), str(L.worker_pid)],"
    "'refine': [str(paths.REFINE_DIR), str(L.refine_dir)],"
    "'logs': [str(paths.LOGS_DIR), str(L.logs_dir)],"
    "'schemas': [str(paths.DOMAIN_SCHEMAS_DIR), str(L.schemas_dir)],"
    "'meta': [str(paths.DOMAIN_META_PATH), str(L.meta_yaml)],"
    "}))"
)


def test_every_shared_path_agrees(tmp_path):
    """Run inside a real vault, in a subprocess, because paths.py computes its
    constants at import."""
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text("ingest: {}\n")

    import json, os

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    for key in ("ARTMIND_HOME", "ARTMIND_DATA_DIR", "ARTMIND_VAULT"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr

    for name, (from_paths, from_layout) in json.loads(result.stdout).items():
        assert from_paths == from_layout, (
            f"{name}: paths.py says {from_paths}, VaultLayout says {from_layout}"
        )
```

Also append to `test/test_vault.py`:

```python
def test_layout_declares_no_path_the_system_does_not_use(tmp_path):
    """`chunks_dir` was aspirational -- split chunks live beside the derived
    markdown, not under a `data/chunks/` the code never creates. A layout that
    describes paths nothing uses is a trap for the next reader."""
    assert not hasattr(vault.VaultLayout(tmp_path), "chunks_dir")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_layout_parity.py test/test_vault.py -v`
Expected: FAIL — `originals`, `snapshots` and `jobs` disagree, and `chunks_dir` still exists.

- [ ] **Step 3: Correct `VaultLayout`**

In `artmind/vault.py`, replace the four disagreeing properties and delete `chunks_dir`:

```python
    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def originals_dir(self) -> Path:
        """Only sources ingested from OUTSIDE the vault. A binary already in the
        vault is never copied (docs/stores-and-repos.md)."""
        return self.documents_dir / "originals"

    @property
    def markdowns_dir(self) -> Path:
        """docling output and the split chunks beside it."""
        return self.documents_dir / "markdowns"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "graph_snapshot"

    @property
    def structured_snapshots_dir(self) -> Path:
        return self.data_dir / "structured_snapshot"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "ingestion_jobs"

    @property
    def worker_pid(self) -> Path:
        return self.data_dir / "worker.pid"
```

Update the class docstring to say these names are the ones the system actually
uses, and that `paths.py` derives from here.

- [ ] **Step 4: Have `paths.py` derive from it**

In `paths.py`, replace the hand-built data-dir block (`DOCUMENTS_DIR` through
`STRUCTURED_SNAPSHOT_DIR`) with derivation from a layout. Note this must work in
BOTH modes: inside a vault `_LAYOUT` already exists; outside one, build a layout
rooted at the legacy data dir's parent so the same property names apply.

```python
# Derived from VaultLayout rather than rebuilt, so the two cannot drift -- they
# did once, and nothing caught it because nothing read the VaultLayout names.
# Outside a vault the same layout logic is applied to the legacy data dir, so
# there is one description of the tree rather than two.
_DATA_LAYOUT = _LAYOUT if _LAYOUT is not None else VaultLayout(ARTMIND_DATA_DIR.parent)
if _LAYOUT is None or ARTMIND_DATA_DIR != _LAYOUT.data_dir:
    # ARTMIND_DATA_DIR was overridden (env var, or the legacy default), so the
    # tree hangs off it directly rather than off a vault's .artmind/.
    class _RootedLayout(VaultLayout):
        @property
        def data_dir(self) -> Path:
            return ARTMIND_DATA_DIR

    _DATA_LAYOUT = _RootedLayout(ARTMIND_DATA_DIR)

DATA_DIR = ARTMIND_DATA_DIR
DOCUMENTS_DIR = _DATA_LAYOUT.documents_dir
ORIGINALS_DIR = _DATA_LAYOUT.originals_dir
MARKDOWNS_DIR = _DATA_LAYOUT.markdowns_dir
DB_PATH = _DATA_LAYOUT.registry_db
JOBS_DIR = _DATA_LAYOUT.jobs_dir
KG_DIR = _DATA_LAYOUT.kg_dir
REFINE_DIR = _DATA_LAYOUT.refine_dir
GRAPH_SNAPSHOT_DIR = _DATA_LAYOUT.snapshots_dir
WORKER_PID_FILE = _DATA_LAYOUT.worker_pid
STRUCTURED_DIR = _DATA_LAYOUT.structured_dir
STRUCTURED_SNAPSHOT_DIR = _DATA_LAYOUT.structured_snapshots_dir
```

`VaultLayout` is a frozen dataclass, so subclassing it for the override is legal;
if that fights the dataclass, simpler is fine — pass the root and let `data_dir`
be a plain attribute. **Whatever shape you choose, the parity test is the
contract:** every constant must equal its layout property in both modes.

- [ ] **Step 5: Run the parity test, then the full suite**

Run: `uv run --group dev pytest test/test_vault_layout_parity.py test/test_vault.py -v`
Expected: PASS

Run: `just dev-test`
Expected: 1832 passed, 14 skipped (1831 + the two new tests, minus none). Every
existing consumer of these constants must be unaffected — the paths are
byte-identical to before, only their derivation changed.

- [ ] **Step 6: Commit**

```bash
git add artmind/vault.py paths.py test/test_vault_layout_parity.py test/test_vault.py
git commit -m "fix(vault): one description of the data tree, derived not duplicated"
```

---

## Task 2: Record the source hash in frontmatter

`binary_changed` currently compares the incoming binary against the copy in
`originals/`. That copy is the only reason the copy exists for vault-resident
sources — so before it can go, the baseline must live somewhere else.

The derived markdown already carries `_derived_sha256`, the fingerprint of its
own body at conversion time. `_source_sha256` is the exact counterpart: the
hash of the binary it was converted *from*. It travels with the document, into
git, and needs no second file on disk.

**Files:**
- Modify: `artmind/document_identity.py`, `artmind/ingest.py`
- Test: `test/test_ingest_binary_derived.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_ingest_binary_derived.py`. It already has an `env` fixture
returning `(vault, source)` — note its `source` lives at
`tmp_path/incoming/deck.pptx`, **outside** the vault — and a `_fake_docling`
helper yielding successive bodies. Reuse both.

```python
def test_conversion_records_the_source_hash_in_frontmatter(env, monkeypatch):
    """`_source_sha256` is the counterpart to `_derived_sha256`: the hash of the
    binary this markdown was converted FROM, carried by the document itself so
    no second copy is needed as a baseline."""
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    derived = vault / "_derived" / "general" / "deck.md"
    meta, _ = ing._parse_md_frontmatter(derived.read_text(encoding="utf-8"))
    assert meta["_source_sha256"] == ing._compute_sha256(source)
    assert "_derived_sha256" in meta, "the body fingerprint must still be written"


def test_the_frontmatter_hash_is_the_baseline_not_the_copy(env, monkeypatch):
    """Delete the data-dir copy entirely: the document's own `_source_sha256`
    must be enough to recognise unchanged bytes. This is what makes the copy
    removable in the next task."""
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    for stale in ing.ORIGINALS_DIR.iterdir():
        stale.unlink()

    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run")),
    )
    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert result["tier"] == "no_op"


def test_a_changed_binary_is_detected_without_the_copy(env, monkeypatch):
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    first = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    for stale in ing.ORIGINALS_DIR.iterdir():
        stale.unlink()
    source.write_bytes(b"fake binary v2")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v2.\n"]))

    second = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert second["version"] == first["version"] + 1


def test_a_document_predating_the_field_still_works(env, monkeypatch):
    """The fallback path: a vault converted before `_source_sha256` existed must
    not see every binary as changed on its first ingest after the upgrade."""
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    # Simulate the old shape: strip the new field, keep the originals/ copy.
    derived = vault / "_derived" / "general" / "deck.md"
    meta, body = ing._parse_md_frontmatter(derived.read_text(encoding="utf-8"))
    del meta["_source_sha256"]
    import artmind.document_identity as di
    di.write_document(derived, meta, body)

    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run")),
    )
    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert result["tier"] == "no_op"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_binary_derived.py -v`
Expected: FAIL — no `_source_sha256` is written.

- [ ] **Step 3: Add the field to the contract**

In `artmind/document_identity.py`, add `"_source_sha256"` to `SYSTEM_FIELDS`,
immediately after `"_derived_sha256"`, with a comment:

```python
    # The hash of the BINARY this markdown was converted from. Counterpart to
    # `_derived_sha256` (which fingerprints the markdown's own body): together
    # they let `binary_changed` and `markdown_edited` be decided from the
    # document alone, with no copy of the original kept as a baseline.
    # Removed on promotion, like `_derived_sha256` -- a promoted document has
    # no binary source any more.
    "_source_sha256",
```

- [ ] **Step 4: Write and read it in `artmind/ingest.py`**

In `_ingest_binary_derived`, replace the `binary_changed` computation:

```python
    binary_changed = dest_path.exists() and _compute_sha256(dest_path) != _compute_sha256(source)
```

with one that prefers the frontmatter baseline and falls back to the old copy
comparison for documents converted before this field existed:

```python
    source_sha256 = _compute_sha256(source)
```

and then, AFTER `existing_meta` has been read (it is read further down — move
this computation below that point):

```python
    # The baseline is the document's own `_source_sha256`. Fall back to
    # comparing against the copy in `originals/` for documents converted before
    # that field existed, so an existing vault does not see every binary as
    # changed on the first ingest after this upgrade.
    prior_source_sha256 = existing_meta.get("_source_sha256")
    if prior_source_sha256 is not None:
        binary_changed = prior_source_sha256 != source_sha256
    else:
        binary_changed = dest_path.exists() and _compute_sha256(dest_path) != source_sha256
```

Then, where `new_meta["_derived_sha256"] = new_derived_sha256` is set (around
line 1023), add alongside it:

```python
        new_meta["_source_sha256"] = source_sha256
```

And where promotion pops `_derived_sha256` (around line 976), pop this too:

```python
        promoted_meta.pop("_source_sha256", None)
```

- [ ] **Step 5: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_ingest_binary_derived.py -v`
Expected: PASS

Run: `just dev-test`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add artmind/document_identity.py artmind/ingest.py test/test_ingest_binary_derived.py
git commit -m "feat(ingest): the derived markdown carries its source's hash"
```

---

## Task 3: Stop copying a binary that already lives in the vault

Now that the baseline is in frontmatter, the copy is redundant for a source
already inside the vault — the symmetric case to what Phase 2 did for
vault-native markdown. A source from **outside** the vault is still copied,
because there artmind genuinely is the only keeper.

**Files:**
- Modify: `artmind/ingest.py`
- Test: `test/test_vault_resident_sources.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_vault_resident_sources.py`:

```python
"""A source already in the vault is never copied (docs/stores-and-repos.md).

The symmetric case to what Phase 2 did for vault-native markdown. The
consequence is stated in the spec and worth restating here: a gitignored binary
in the vault has no version history and no second copy, so what survives is the
markdown in `_derived/`, which git does hold.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from artmind.ingest import _is_inside_vault


def test_a_file_in_the_vault_is_recognised(tmp_path):
    assert _is_inside_vault(tmp_path / "sources" / "deck.pptx", tmp_path) is True


def test_a_file_outside_the_vault_is_not(tmp_path):
    assert _is_inside_vault(Path("/tmp/elsewhere/deck.pptx"), tmp_path) is False


def test_no_vault_means_nothing_is_inside_it(tmp_path):
    assert _is_inside_vault(tmp_path / "deck.pptx", None) is False
```

Plus two integration tests. Put these in the same new file, importing the
existing harness so there is one docling stub, not two:

```python
import artmind.ingest as ing
from test.test_ingest_binary_derived import _fake_docling  # one stub, not two


def test_a_vault_resident_binary_is_not_copied(env, monkeypatch):
    """The vault copy IS the original -- copying it into the data dir would
    duplicate a file git is already deliberately not versioning."""
    vault, _ = env
    resident = vault / "sources" / "deck.pptx"
    resident.parent.mkdir(parents=True)
    resident.write_bytes(b"fake binary v1")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    ing.ingest_file(resident, "gemma4:e4b", "general", chunk_size=6000)

    assert list(ing.ORIGINALS_DIR.iterdir()) == [], "a vault-resident source needs no copy"
    assert (vault / "_derived" / "general" / "deck.md").is_file()


def test_a_binary_from_outside_the_vault_is_still_copied(env, monkeypatch):
    """Nothing else holds it, so artmind must. The `env` fixture's own source
    lives outside the vault, which is exactly this case."""
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert [p.name for p in ing.ORIGINALS_DIR.iterdir()] == ["deck.pptx"]
```

`env` here is the fixture from `test/test_ingest_binary_derived.py`. Importing a
fixture across modules does not work in pytest — either move `env` and
`_fake_docling` into `test/conftest.py`, or re-declare a local fixture in the new
file. **Prefer moving them to `conftest.py`**: two copies of a fixture that
monkeypatches five module globals is exactly the drift this codebase keeps
paying for. Say which you did and why in your report.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_resident_sources.py -v`
Expected: FAIL, `ImportError: cannot import name '_is_inside_vault'`

- [ ] **Step 3: Implement**

Add to `artmind/ingest.py`, near the other predicates (`_is_vault_native_markdown`,
`_is_promotable_binary`):

```python
def _is_inside_vault(source: Path, vault_dir: "Path | None") -> bool:
    """Does `source` already live in the vault?

    Decides whether artmind needs to keep its own copy. A source from outside
    the vault is copied into `documents/originals/` because nothing else holds
    it; one already in the vault is not, exactly as Phase 2 stopped copying
    vault-native markdown (docs/stores-and-repos.md).
    """
    if vault_dir is None:
        return False
    try:
        Path(source).resolve().relative_to(Path(vault_dir).resolve())
    except (OSError, ValueError):
        return False
    return True
```

Then in `_ingest_binary_derived`, make the copy conditional:

```python
    if _is_inside_vault(source, ARTMIND_VAULT_DIR):
        # The vault copy IS the original. Copying it into the data dir would
        # duplicate a file git is already deliberately not versioning.
        dest_path = source
    else:
        ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest_path)
        logger.debug("Copied original to: {}", dest_path)
```

**Watch `dest_path`.** It is assigned earlier as `ORIGINALS_DIR / source.name`
and used afterwards (`orig_registry_path`, the fallback `binary_changed`
comparison, and possibly conversion). Read every use before changing it, and
make sure a vault-resident source's `dest_path` pointing at the vault file is
correct at each one — in particular the fallback branch of `binary_changed`,
where comparing a file against itself must not report a change.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_vault_resident_sources.py test/test_ingest_binary_derived.py -v`
Expected: PASS

Run: `just dev-test`
Expected: all green. If `test/test_archive.py` or `test/test_unified_snapshot_phase5.py`
fails, it is asserting on `originals/` contents — check whether the fixture's
source was inside a vault, and whether the assertion is still the right one.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_vault_resident_sources.py
git commit -m "feat(ingest): a source already in the vault is never copied"
```

---

## Task 4: Extracted images land beside their markdown

docling writes extracted images to `MARKDOWNS_DIR / f"{stem}_artifacts"` in the
data dir, and the derived markdown references them. Since the markdown is
committed to the vault, its image links resolve to a gitignored data-dir path —
so a fresh clone renders broken images, and Obsidian shows nothing.

**Files:**
- Modify: `artmind/ingest.py`
- Test: `test/test_vault_resident_sources.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_vault_resident_sources.py`:

```python
def test_extracted_images_land_next_to_the_committed_markdown(env, monkeypatch):
    """The derived markdown is committed and references these images by
    relative path. Left in the gitignored data dir, a fresh clone renders
    broken images and Obsidian shows nothing -- which is why the vault
    .gitignore negates `!_derived/**`."""
    vault, source = env

    def _convert_with_artifacts(dest_path, image_model):
        artifacts = ing.MARKDOWNS_DIR / f"{Path(dest_path).stem}_artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "image-1.png").write_bytes(b"png bytes")
        return "# Deck\n\n![](deck_artifacts/image-1.png)\n", {}

    monkeypatch.setattr(ing, "_convert_binary_via_docling", _convert_with_artifacts)

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert (vault / "_derived" / "general" / "deck_artifacts" / "image-1.png").is_file()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_vault_resident_sources.py -v`
Expected: FAIL — the image is only in the data dir.

- [ ] **Step 3: Implement**

In `_ingest_binary_derived`, after the derived markdown is written to the vault,
copy the artifacts directory beside it. Read the existing image-description
block (`artmind/ingest.py:704-712`) first — it already locates
`artifacts_dir = MARKDOWNS_DIR / f"{stem}_artifacts"`, and that logic must keep
working since it runs during conversion, before this copy.

```python
    # The derived markdown is committed to the vault and references these
    # images by relative path, so they must be committed with it -- the vault
    # .gitignore negates `!_derived/**` for exactly this. The data-dir copy
    # stays as docling's working output.
    artifacts_src = MARKDOWNS_DIR / f"{stem}_artifacts"
    if artifacts_src.is_dir():
        artifacts_dest = derived_path.parent / artifacts_src.name
        shutil.copytree(artifacts_src, artifacts_dest, dirs_exist_ok=True)
        logger.debug("Copied {} artifact(s) beside the derived markdown", 
                     sum(1 for _ in artifacts_dest.iterdir()))
```

Place this so it runs for both the first conversion and a reconversion, but NOT
when the document has been promoted (a promoted document is no longer produced
from a binary). Read the surrounding control flow and choose the point
deliberately, then say in your report which branch you put it in and why.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_vault_resident_sources.py -v`
Expected: PASS

Run: `just dev-test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_vault_resident_sources.py
git commit -m "feat(ingest): extracted images are committed with their markdown"
```

---

## Task 5: Documentation

**Files:**
- Modify: `docs/vault.md`, `docs/stores-and-repos.md`

- [ ] **Step 1: Correct the layout in `docs/vault.md`**

Its "Layout" section and the line beginning "Inside `.artmind/data/`:" describe
the invented names. Replace that line with the real tree:

```markdown
Inside `.artmind/data/`: `documents/originals/` (only sources from *outside*
the vault) and `documents/markdowns/` (docling output plus the split chunks),
`kg/` (staging — the expensive layer), `document_registry.db`, `structured/`,
`graph_snapshot/`, `structured_snapshot/`, `ingestion_jobs/`, `refine/`.
```

- [ ] **Step 2: Remove the resolved entry from "Known gaps"**

Delete the `VaultLayout` / `paths.py` bullet — Task 1 closed it. Leave the other
three bullets alone.

- [ ] **Step 3: Update the status line**

Add vault-resident sources to what has landed, and remove them from the not-yet
list. Do not claim `documents/markdowns/` has been removed — it has not.

- [ ] **Step 4: Correct flow A in `docs/stores-and-repos.md`**

Its mermaid diagram for the binary flow already shows no copy into `originals/`,
which is now true. Check the surrounding prose and the "What this means for
where you work" table still match, and correct anything that implies a copy is
made for a vault-resident source. Add `_source_sha256` to the description of how
a re-ingest is detected.

- [ ] **Step 5: Commit**

```bash
git add docs/vault.md docs/stores-and-repos.md
git commit -m "docs: the real data-dir layout, and vault-resident sources"
```

---

## Task 6: End-to-end verification

Green tests do not mean the CLI works (`CLAUDE.md`). Manual, with a real binary.

- [ ] **Step 1: Stop stale daemons and reinstall**

```bash
just dev-stop-daemons && just dev-install
```

- [ ] **Step 2: Build a vault with a real binary in it**

```bash
cd /tmp && rm -rf vrs-e2e && mkdir vrs-e2e && cd vrs-e2e
ARTMIND_NO_PROXY=1 artmind init
mkdir -p sources
# Any small real .docx or .pdf you have; docling must be able to read it.
cp <some-small-document>.docx sources/
cat > .artmind/vault.yaml <<'EOF'
ingest:
  trigger: manual
  mappings:
    - path: sources/**
      domain: general
EOF
ARTMIND_NO_PROXY=1 artmind ingest sync . --stage-only
```

This makes real LLM calls — use one small document.

- [ ] **Step 3: Confirm no copy was made**

```bash
ls -la .artmind/data/documents/originals/ 2>/dev/null || echo "originals/ absent — correct"
```

Expected: empty or absent. The vault copy is the original.

- [ ] **Step 4: Confirm the frontmatter carries the source hash**

```bash
head -20 _derived/general/*.md | grep -E "_source_sha256|_derived_sha256"
```

Expected: both present.

- [ ] **Step 5: Confirm re-ingesting is a no-op, not a false change**

```bash
ARTMIND_NO_PROXY=1 artmind ingest sync . --stage-only 2>&1 | grep -iE "unchanged|version|skip"
```

Expected: no new version minted — the source hash matches, so nothing changed.

- [ ] **Step 6: Confirm images are committed and the binary is not**

```bash
git add -A && git status --porcelain --untracked-files=all | grep -E "_derived|sources"
```

Expected: `_derived/general/*.md` and any `_derived/general/*_artifacts/*` staged;
`sources/*.docx` absent (gitignored).

- [ ] **Step 7: Clean up**

```bash
cd /tmp && rm -rf vrs-e2e
```

---

## Notes for whoever writes the next plan

- **`documents/markdowns/` still exists** and is still written for binary
  sources. Removing it in favour of `_derived/` touches eleven consumers and is
  pure de-duplication — its own plan, and low priority.
- **`_source_sha256` has a fallback path** for documents converted before it
  existed (compare against `originals/`). That fallback can be deleted once no
  vault predates the field, but there is no way to know that automatically, so
  it should stay until someone decides to drop it deliberately.
- The remaining "Known gaps" in `docs/vault.md` after this plan: `--vault` is
  not a real flag, `load_env()` returns the whole environment, and a command
  needing the vault must resolve it fresh.
