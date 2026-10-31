# Document keys (same-named sources) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two sources with the same filename in different folders become two documents, with their own converted markdown, artifacts, chunks and staging folder.

**Architecture:** A pure `document_key(vault_relative_path)` in `artmind/document_identity.py` (`<stem>~<12 hex of sha256(path)>`). In `artmind/ingest.py`, `plan_binary` chooses a binary's key once (own bare-stem folder → own key → moved binary → new key, with a collision guard) and everything downstream already derives names from `registered_path.stem`, so the key propagates. The few sites that build names from `source.stem` / `dest_path.stem` switch to path helpers in `ingest.py`. Vault notes keep their staging folder when it already holds their id, else get a keyed one.

**Tech Stack:** Python 3.14, pytest (`uv run --group dev pytest`), Click, docling CLI (subprocess).

Spec: `docs/superpowers/specs/2026-10-31-document-key-collisions-design.md`.

**Deviation from the spec, deliberate:** the path helpers (`_markdown_path`, `_artifacts_dir`, `_chunks_dir`, `_staging_dir`) live in `artmind/ingest.py`, not `document_identity.py`, because the test fixture `ingest_env` (`test/conftest.py`) monkeypatches `ingest.MARKDOWNS_DIR` / `ingest.KG_DIR`; helpers reading `document_identity`'s own globals would escape it. Only the pure `document_key` lives in `document_identity`. Task 8 updates the spec to say so.

## Ground rules for every task

- Work only in this worktree: `/Users/surjitdas/projects/artmind9/.claude/worktrees/fix-document-key-collisions`.
- Run the suite with `uv run --group dev pytest test/<file> -v`; the whole suite with `just dev-test` (~3 min).
- Never run `artmind setup` / `artmind init` from the checkout. Never touch `/Users/surjitdas/artmind_vaults/*` before Task 9.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

| File | Change |
|---|---|
| `artmind/document_identity.py` | add `KEY_SEPARATOR`, `KEY_HASH_CHARS`, `document_key()` |
| `artmind/ingest.py` | path helpers; `_converted_owner()`; `_resolve_binary_key()`; `plan_binary` uses it; `_convert_binary_via_docling(…, key=)`; `_docling_input_named()`; binary chunk dirs from the key; note chunk dir + `_note_staging_dir()`; `extract_kg` staging dir; vault-native staged lookups include the key |
| `artmind/cli.py` (~1511), `artmind/archive.py` (~357), `artmind/reindex.py` (~87) | use the helpers |
| `test/test_document_key.py` | new: key, binary resolution, notes, guard, source scan |
| `test/test_ingest_binary_derived.py`, `test/test_ingest_conversion.py`, `test/test_page_check.py`, `test/test_page_repair.py`, `test/test_ingest_extract_kg_retry.py`, `test/test_ingest_vault_native.py`, `test/test_ingest_pending.py`, `test/conftest.py` | hard-coded `<stem>.md` / `<stem>_chunks` / `kg/<d>/<stem>` paths → read `result["registered_path"]` |
| `docs/document-identity.md`, `CLAUDE.md`, the spec | document the key |

---

### Task 1: `document_key`

**Files:**
- Modify: `artmind/document_identity.py` (after `canonical_path`, ~line 135)
- Test: `test/test_document_key.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Document keys: same-named sources must not collide
(spec docs/superpowers/specs/2026-10-31-document-key-collisions-design.md)."""
from hashlib import sha256

from artmind.document_identity import KEY_HASH_CHARS, document_key


def test_key_is_stem_tilde_twelve_hex_of_the_path():
    path = "All_contracts/A/Summary.pdf"
    assert document_key(path) == "Summary~" + sha256(path.encode()).hexdigest()[:12]
    assert KEY_HASH_CHARS == 12


def test_same_name_in_two_folders_gets_two_keys():
    assert document_key("A/Summary.pdf") != document_key("B/Summary.pdf")


def test_extension_is_part_of_the_key():
    assert document_key("A/X.pdf") != document_key("A/X.docx")


def test_key_depends_on_the_path_alone():
    # Two laptops, no registry: the same vault path gives the same name.
    assert document_key("A/Summary.pdf") == document_key("A/Summary.pdf")


def test_full_key_carries_the_whole_digest():
    path = "A/Summary.pdf"
    assert document_key(path, full=True) == "Summary~" + sha256(path.encode()).hexdigest()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: FAIL, `ImportError: cannot import name 'KEY_HASH_CHARS'`.

- [ ] **Step 3: Implement** — add to `artmind/document_identity.py` (add `from hashlib import sha256` and `from pathlib import PurePosixPath` to its imports if absent):

```python
# ── document keys ───────────────────────────────────────────────────────────

KEY_SEPARATOR = "~"
KEY_HASH_CHARS = 12


def document_key(vault_relative_path: str, *, full: bool = False) -> str:
    """The name a document's derived files and staging folder take when it is
    first ingested: `<stem>~<12 hex of sha256(path)>` (spec 2026-10-31
    document-key-collisions). Computed from the vault-relative POSIX path
    alone, so every laptop names one source alike, and two sources sharing a
    filename never share a name. Chosen once and then found again
    (`ingest._resolve_binary_key`, `ingest._note_staging_dir`); a move keeps
    it. `full` is the collision guard's fallback: the whole digest."""
    digest = sha256(vault_relative_path.encode("utf-8")).hexdigest()
    stem = PurePosixPath(vault_relative_path).stem
    return f"{stem}{KEY_SEPARATOR}{digest if full else digest[:KEY_HASH_CHARS]}"
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add artmind/document_identity.py test/test_document_key.py
git commit -m "feat(identity): document_key names a source by stem and path hash"
```

---

### Task 2: Path helpers and the occupant reader in `ingest.py`

**Files:**
- Modify: `artmind/ingest.py` (import `document_key` in the `from artmind.document_identity import (...)` block, line ~22; add helpers just above `class BinaryPlan`, ~line 1586)
- Test: `test/test_document_key.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
import json

import artmind.ingest as ing


def test_path_helpers_name_by_key(ingest_env):
    assert ing._markdown_path("S~ab") == ing.MARKDOWNS_DIR / "S~ab.md"
    assert ing._artifacts_dir("S~ab") == ing.MARKDOWNS_DIR / "S~ab_artifacts"
    assert ing._chunks_dir("S~ab") == ing.MARKDOWNS_DIR / "S~ab_chunks"
    assert ing._staging_dir("d", "S~ab") == ing.KG_DIR / "d" / "S~ab"


def test_converted_owner_reads_the_conversion_sidecar(ingest_env):
    chunks = ing._chunks_dir("Summary")
    chunks.mkdir(parents=True)
    (chunks / ing.CONVERSION_SIDECAR).write_text(json.dumps(
        {"source_path": "A/Summary.pdf", "source_sha256": "abc", "source_type": "pdf"}))
    assert ing._converted_owner("Summary", "general") == ("A/Summary.pdf", "abc")


def test_converted_owner_falls_back_to_staging(ingest_env):
    folder = ing._staging_dir("general", "Summary")
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps(
        {"id": "x", "source_path": "B/Summary.pdf", "source_sha256": "def"}))
    assert ing._converted_owner("Summary", "general") == ("B/Summary.pdf", "def")


def test_converted_owner_is_none_for_a_free_name(ingest_env):
    assert ing._converted_owner("Nothing", "general") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: the 4 new tests FAIL with `AttributeError: module 'artmind.ingest' has no attribute '_markdown_path'`.

- [ ] **Step 3: Implement** — in `artmind/ingest.py`:

```python
# ── derived-file names (spec 2026-10-31 document-key-collisions) ───────────
# Every converted markdown, its artifacts and chunks, and every staging folder
# is named by a document key (`document_identity.document_key`), or, for a
# document ingested before keys, by its bare stem. Build them here only.


def _markdown_path(key: str) -> Path:
    return MARKDOWNS_DIR / f"{key}.md"


def _artifacts_dir(key: str) -> Path:
    return MARKDOWNS_DIR / f"{key}_artifacts"


def _chunks_dir(key: str) -> Path:
    return MARKDOWNS_DIR / f"{key}_chunks"


def _staging_dir(domain: str, key: str) -> Path:
    return KG_DIR / domain / key


def _converted_owner(key: str, domain: str) -> tuple[str, str | None] | None:
    """`(source_path, source_sha256)` of the source whose conversion holds the
    name `key`, or None when no conversion of that name records one. Read
    from the conversion sidecar (written by every conversion, committed,
    domain-free), else the staging `document.json` in `domain`, else the
    converted markdown's pre-R6 frontmatter."""
    candidates = [
        _chunks_dir(key) / CONVERSION_SIDECAR,
        _staging_dir(domain, key) / "document.json",
    ]
    for path in candidates:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("source_path"):
            return str(record["source_path"]), record.get("source_sha256")
    md = _markdown_path(key)
    if md.exists():
        meta, _ = _parse_md_frontmatter(md.read_text(encoding="utf-8"))
        if meta.get("_source_path"):
            return str(meta["_source_path"]), meta.get("_source_sha256")
    return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_document_key.py
git commit -m "feat(ingest): path helpers and the owner of a derived name"
```

---

### Task 3: Choose a binary's key in `plan_binary`

**Files:**
- Modify: `artmind/ingest.py` — `plan_binary` (~line 1609), new `_resolve_binary_key` above it
- Test: `test/test_document_key.py`

- [ ] **Step 1: Write the failing tests** — append (uses the `ingest_env` fixture; sources live **inside** the vault, like `nbs_audit_2026`):

```python
from conftest import _fake_docling, stage_as_extracted


def _vault_pdf(vault, rel, data):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_two_same_named_pdfs_become_two_documents(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    b = _vault_pdf(vault, "B/Summary.pdf", b"certificate B")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# A\n", "# B\n"]))

    ra = ing.ingest_file(a, "m", "general", chunk_size=6000)
    rb = ing.ingest_file(b, "m", "general", chunk_size=6000)

    assert ra["artmind_id"] != rb["artmind_id"]
    assert ra["version"] == rb["version"] == 1
    assert ra["registered_path"] != rb["registered_path"]
    assert ing.Path(ra["registered_path"]).name == document_key("A/Summary.pdf") + ".md"
    assert ing.Path(rb["chunks_dir"]).name == document_key("B/Summary.pdf") + "_chunks"


def test_existing_bare_stem_document_keeps_its_name(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    # A document converted before keys: bare-stem markdown + sidecar naming A.
    ing.write_document(ing._markdown_path("Summary"), {"_artmind_id": "old-id", "_domain": "general"}, "# A\n")
    chunks = ing._chunks_dir("Summary")
    chunks.mkdir(parents=True)
    (chunks / ing.CONVERSION_SIDECAR).write_text(json.dumps(
        {"source_path": "A/Summary.pdf", "source_sha256": ing._compute_sha256(a), "source_type": "pdf"}))

    plan = ing.plan_binary(a, "general")

    assert plan.registered_path == ing._markdown_path("Summary")
    assert plan.existing_meta["_artmind_id"] == "old-id"


def test_namesake_of_a_bare_stem_document_gets_its_own_key(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    b = _vault_pdf(vault, "B/Summary.pdf", b"certificate B")
    ing.write_document(ing._markdown_path("Summary"), {"_artmind_id": "old-id", "_domain": "general"}, "# A\n")
    chunks = ing._chunks_dir("Summary")
    chunks.mkdir(parents=True)
    (chunks / ing.CONVERSION_SIDECAR).write_text(json.dumps(
        {"source_path": "A/Summary.pdf", "source_sha256": ing._compute_sha256(a), "source_type": "pdf"}))

    plan = ing.plan_binary(b, "general")

    assert plan.registered_path == ing._markdown_path(document_key("B/Summary.pdf"))
    assert plan.is_new


def test_moved_binary_keeps_its_key(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# A\n"]))
    r1 = ing.ingest_file(a, "m", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)

    moved = vault / "C" / "Summary.pdf"
    moved.parent.mkdir()
    a.rename(moved)
    plan = ing.plan_binary(moved, "general")

    assert plan.registered_path == ing.Path(r1["registered_path"])
    assert plan.unchanged


def test_collision_guard_never_takes_another_sources_key(ingest_env, monkeypatch):
    vault, _ = ingest_env
    b = _vault_pdf(vault, "B/Summary.pdf", b"certificate B")
    _vault_pdf(vault, "Z/Other.pdf", b"other")
    taken = document_key("B/Summary.pdf")
    chunks = ing._chunks_dir(taken)
    chunks.mkdir(parents=True)
    (chunks / ing.CONVERSION_SIDECAR).write_text(json.dumps(
        {"source_path": "Z/Other.pdf", "source_sha256": "zzz", "source_type": "pdf"}))

    plan = ing.plan_binary(b, "general")

    assert plan.registered_path == ing._markdown_path(document_key("B/Summary.pdf", full=True))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: the 5 new tests FAIL (`registered_path` is `Summary.md`; the two-documents test sees one `artmind_id` and version 2).

- [ ] **Step 3: Implement** — in `artmind/ingest.py` add (needs `import glob` at the top) and rewrite `plan_binary`:

```python
def _resolve_binary_key(dest_path: Path, source_sha256: str, domain: str) -> str:
    """The name `dest_path`'s derived files take, chosen once and then found
    again (spec 2026-10-31 document-key-collisions §2):

    1. its own bare-stem conversion (a document from before keys), or a
       bare-stem conversion that records no source (pre-R6, kept as it was);
    2. its own key;
    3. a same-stem conversion of these exact bytes whose recorded source is
       gone from the vault: a moved binary keeps its name;
    4. a new key -- or, when that key is held by another source, the
       full-digest key (the collision guard; never overwrite an occupant)."""
    rel = canonical_path(dest_path)
    stem = dest_path.stem
    key = document_key(rel)
    bare_owner = _converted_owner(stem, domain)
    if _markdown_path(stem).exists() and (bare_owner is None or bare_owner[0] == rel):
        return stem
    key_owner = _converted_owner(key, domain)
    if key_owner is not None and key_owner[0] == rel:
        return key
    for md in sorted(MARKDOWNS_DIR.glob(f"{glob.escape(stem)}*.md")):
        name = md.stem
        if name != stem and not name.startswith(stem + KEY_SEPARATOR):
            continue
        owner = _converted_owner(name, domain)
        if (
            owner is not None and owner[0] != rel and owner[1] == source_sha256
            and ARTMIND_VAULT_DIR is not None and not (Path(ARTMIND_VAULT_DIR) / owner[0]).exists()
        ):
            logger.info("{}: moved from {}; keeps the name {}", rel, owner[0], name)
            return name
    if key_owner is not None or _markdown_path(key).exists():
        full = document_key(rel, full=True)
        logger.warning(
            "{}: the name {} belongs to {}; using {}",
            rel, key, key_owner[0] if key_owner else "another conversion", full,
        )
        return full
    return key


def plan_binary(source: Path, effective_domain: str) -> BinaryPlan:
    """Read, never write: the converted markdown this binary maps to and the
    baseline its last extraction left (spec 2026-09-26 R6: the staging
    folder's document.json, with a pre-R6 conversion's own frontmatter as
    the fallback -- a binary whose extraction failed is never mistaken for
    converted and extracted). The markdown is named by the binary's document
    key (`_resolve_binary_key`), never by its bare stem alone: two binaries
    sharing a filename are two documents."""
    dest_path = (
        source.resolve() if _is_inside_vault(source, ARTMIND_VAULT_DIR)
        else external_copy_path(source, ARTMIND_VAULT_DIR)
    )
    source_sha256 = _compute_sha256(source)
    key = _resolve_binary_key(dest_path, source_sha256, effective_domain)
    registered_path = _markdown_path(key)
    existing_meta: dict = {}
    if registered_path.exists():
        existing_meta, _ = _parse_md_frontmatter(
            registered_path.read_text(encoding="utf-8")
        )
    staged = staged_document(effective_domain, [key], existing_meta.get("_artmind_id"), search=True)
    return BinaryPlan(
        registered_path=registered_path,
        existing_meta=existing_meta,
        baseline=ingest_baseline(staged, existing_meta),
        source_sha256=source_sha256,
    )
```

Also add `KEY_SEPARATOR` and `document_key` to the `from artmind.document_identity import (...)` block.

**Why `_markdown_path(key).exists()` in step 4:** a conversion that wrote its markdown but no sidecar yet (crash) still holds the name.

**`external_copy_path` caveat:** `plan_binary` is called by `ingest pending` too (`artmind/ingest_pending.py:48`), which must not copy anything; `external_copy_path` only computes a path, so this stays read-only.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: the `plan_binary` tests pass; `test_two_same_named_pdfs_become_two_documents` still FAILS on `chunks_dir` (still `MARKDOWNS_DIR / f"{stem}_chunks"`) — fixed in Task 4.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_document_key.py
git commit -m "fix(ingest): a binary's markdown is named by its document key"
```

---

### Task 4: Conversion and chunks follow the key

**Files:**
- Modify: `artmind/ingest.py` — `_convert_binary_via_docling` (~1370-1470), `_ingest_binary_derived` (~1771, 1781), `_reextract_converted_binary` (~1856), `_ingest_binary_or_adhoc` (~1548-1572)
- Test: `test/test_document_key.py`

- [ ] **Step 1: Write the failing test** — append:

```python
def test_docling_writes_the_keyed_markdown_and_artifacts(ingest_env, monkeypatch, tmp_path):
    vault, _ = ingest_env
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")  # the scratch link goes here
    (tmp_path / "logs").mkdir()
    a = _vault_pdf(vault, "A/Summary.pdf", b"%PDF certificate A")
    key = document_key("A/Summary.pdf")
    seen = {}

    def fake_run(command, cwd=None):
        seen["command"] = command
        (ing.MARKDOWNS_DIR / f"{key}.md").write_text("# A\n")
        return 0, "", ""

    monkeypatch.setattr(ing, "run_command", fake_run)
    monkeypatch.setattr(ing, "_render_from_docling_json", lambda *a, **k: None)

    body, _ = ing._convert_binary_via_docling(a.resolve(), "m", key=key)

    assert body.strip() == "# A"
    assert f'{key}.pdf"' in seen["command"]
    assert not list(ing.MARKDOWNS_DIR.glob("Summary.md"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --group dev pytest test/test_document_key.py::test_docling_writes_the_keyed_markdown_and_artifacts -v`
Expected: FAIL, `TypeError: _convert_binary_via_docling() got an unexpected keyword argument 'key'`.

- [ ] **Step 3: Implement**

(a) Add above `_convert_binary_via_docling`:

```python
from contextlib import contextmanager


@contextmanager
def _docling_input_named(dest_path: Path, key: str):
    """`dest_path` under the name `<key><ext>`, for the docling CLI, which
    names its outputs after its input: a hard link in a scratch folder under
    `LOGS_DIR` (a copy where linking fails), removed afterwards."""
    scratch = Path(tempfile.mkdtemp(prefix="docling-input-", dir=LOGS_DIR))
    named = scratch / f"{key}{dest_path.suffix}"
    try:
        try:
            named.hardlink_to(dest_path)
        except OSError:
            shutil.copy2(dest_path, named)
        yield named
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
```

(Check `LOGS_DIR` is imported from `paths` in `ingest.py`; `_convert_docx_in_process` already uses it.)

(b) `_convert_binary_via_docling(dest_path, image_model, *, key: str, reuse_image_descriptions=False, ...)` — keep the existing keyword parameters; add `key` as keyword-only and replace:

```python
    md_file = _markdown_path(key)
    json_file = MARKDOWNS_DIR / f"{key}.json"
```

and the CLI call:

```python
        json_file.unlink(missing_ok=True)  # a stale one is not this conversion's
        with _docling_input_named(dest_path, key) as docling_input:
            returncode, stdout, stderr = run_command(_docling_command(docling_input), cwd=PROJECT_ROOT)
```

and `artifacts_dir = _artifacts_dir(key)`. Update its docstring's `MARKDOWNS_DIR / f"{dest_path.stem}.md"` to `_markdown_path(key)`. The page check and `_render_from_docling_json(dest_path, …)` keep the real `dest_path`. `_convert_docx_in_process(dest_path, md_file, record)` already names artifacts from `md_file.stem`, so it follows the key.

(c) `_ingest_binary_derived`: pass the key and name chunks by it:

```python
    key = registered_path.stem
    body, err = _convert_binary_via_docling(
        dest_path, image_model, key=key, reuse_image_descriptions=bool(file_result.get("reextract")),
    )
    ...
    chunks_dir = _chunks_dir(key)
    ...
    logger.info("Saved {} chunk(s) to {}_chunks/", len(chunks), key)
```

Delete the now-unused `stem = source.stem` line if nothing else reads it (grep the function).

(d) `_reextract_converted_binary`: `chunks_dir = _chunks_dir(registered_path.stem)`.

(e) `_ingest_binary_or_adhoc` (the vault-less ad-hoc flow, out of scope): keep its behaviour, routed through the helpers:

```python
    md_file = _markdown_path(dest_path.stem)
    ...
        body, err = _convert_binary_via_docling(dest_path, image_model, key=dest_path.stem)
    ...
    chunks_dir = _chunks_dir(dest_path.stem)
```

(f) `_fake_docling` in `test/conftest.py` already accepts `**kwargs`; no change.

- [ ] **Step 4: Run to verify**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: all pass, including `test_two_same_named_pdfs_become_two_documents`.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_document_key.py
git commit -m "fix(ingest): docling output and chunks are named by the document key"
```

---

### Task 5: Vault notes — chunks and staging

**Files:**
- Modify: `artmind/ingest.py` — `_ingest_vault_native` chunk dir (~1234) and its staged lookups (~1069 `stems = [...]`), `extract_kg` `doc_kg_dir` (~3376), `_build_file_result_from_db` chunk dir (~347), the extract-kg retry chunk dir (~2989), new `_note_staging_dir`, new `_derived_key`
- Modify: `artmind/reindex.py:87`
- Test: `test/test_document_key.py`

- [ ] **Step 1: Write the failing test that confirms the note bug**

```python
def _note(vault, rel, body):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n_domain: general\n---\n{body}\n")
    return path


def test_two_same_named_notes_get_distinct_staging(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    b = _note(vault, "B/README.md", "# B\n\nBeta.")
    ra = ing.ingest_file(a, "m", "general", chunk_size=6000)
    rb = ing.ingest_file(b, "m", "general", chunk_size=6000)

    assert ra["chunks_dir"] != rb["chunks_dir"]
    da = ing._note_staging_dir("general", ing.Path(ra["registered_path"]), ra["artmind_id"])
    db_ = ing._note_staging_dir("general", ing.Path(rb["registered_path"]), rb["artmind_id"])
    assert da != db_


def test_existing_note_keeps_its_bare_staging_folder(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    ra = ing.ingest_file(a, "m", "general", chunk_size=6000)
    bare = ing._staging_dir("general", "README")
    bare.mkdir(parents=True)
    (bare / "document.json").write_text(json.dumps({"id": ra["artmind_id"]}))

    assert ing._note_staging_dir("general", a, ra["artmind_id"]) == bare


def test_new_note_staging_is_keyed(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    ra = ing.ingest_file(a, "m", "general", chunk_size=6000)
    assert ing._note_staging_dir("general", a, ra["artmind_id"]) == ing._staging_dir(
        "general", document_key("A/README.md"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --group dev pytest test/test_document_key.py -k note -v`
Expected: FAIL — first on `ra["chunks_dir"] == rb["chunks_dir"]` (both `README_chunks`): this confirms the note half of the bug. If that assertion unexpectedly passes, stop and report: the spec's note claim was wrong.

- [ ] **Step 3: Implement** — in `artmind/ingest.py`, next to the Task 2 helpers:

```python
def _derived_key(registered_path: Path) -> str:
    """The key a registered document's chunks are named by: a converted
    binary's markdown already carries it in its name; a vault note's is
    computed from the note's vault path."""
    path = Path(registered_path)
    try:
        if path.resolve().is_relative_to(Path(MARKDOWNS_DIR).resolve()):
            return path.stem
    except OSError:
        pass
    return document_key(canonical_path(path))


def _note_staging_dir(domain: str, note: Path, artmind_id: str) -> Path:
    """Where a vault note's extraction is staged: its bare-stem folder when that
    already holds this note (a note staged before keys, no migration), else its
    key's folder -- or the full-digest key's when another document holds that
    (the collision guard)."""
    bare = _staging_dir(domain, note.stem)
    if _read_staged_document(bare, artmind_id) is not None:
        return bare
    rel = canonical_path(note)
    folder = _staging_dir(domain, document_key(rel))
    occupant = (folder / "document.json")
    if occupant.exists() and _read_staged_document(folder, artmind_id) is None:
        full = _staging_dir(domain, document_key(rel, full=True))
        logger.warning("{}: staging folder {} belongs to another document; using {}", rel, folder.name, full.name)
        return full
    return folder


def _document_staging_dir(domain: str, registered_path: Path, artmind_id: str) -> Path:
    """`extract_kg`'s staging folder for a registered document: a converted
    binary's is named by its key (its markdown's stem); a note's by
    `_note_staging_dir`."""
    key = _derived_key(registered_path)
    if key == Path(registered_path).stem:
        return _staging_dir(domain, key)
    return _note_staging_dir(domain, Path(registered_path), artmind_id)
```

Then switch the sites:

- `_ingest_vault_native` (~1234): `chunks_dir = _chunks_dir(_derived_key(source))`.
- `_ingest_vault_native` staged lookup (~1069): add both keys so the direct lookup hits without a domain scan:

```python
        stems = [source.stem, document_key(canonical_path(source))] + (
            [Path(resolution.prior_path).stem, document_key(resolution.prior_path)] if resolution.prior_path else []
        )
```

  (`resolution.prior_path` is the registry's vault-relative path; confirm by reading `Resolution` in `document_identity.py` — if it is absolute, wrap with `canonical_path(Path(...))`.)
- `extract_kg` (~3376): `doc_kg_dir = _document_staging_dir(domain, registered_path, doc_id)` — `doc_id` is assigned before this line? Read the 40 lines above it; if `doc_id` is assigned later, move this line below that assignment (`doc_kg_dir` is first used at `chunk_data_dir = doc_kg_dir / "chunks" / doc_sha256`).
- `_build_file_result_from_db` (~347) and the retry (~2989): `chunks_dir = _chunks_dir(_derived_key(registered_path))`; the retry's `md_file` (~2937): `_markdown_path(registered_path.stem)` for a converted binary — leave `markdown_path_for(...)` (~2981) as it is.
- `artmind/reindex.py:87`: `staged = staged_document(safe_domain, [path.stem, document_key(canonical_path(path))], artmind_id)` (import both from `artmind.document_identity`).

- [ ] **Step 4: Run to verify**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py artmind/reindex.py test/test_document_key.py
git commit -m "fix(ingest): same-named vault notes get their own chunks and staging"
```

---

### Task 6: The remaining sites and the source scan

**Files:**
- Modify: `artmind/cli.py:~1511`, `artmind/archive.py:~357`
- Test: `test/test_document_key.py`

- [ ] **Step 1: Write the failing source-scan test**

```python
import re
from pathlib import Path as _P

_HAND_BUILT = re.compile(
    r'MARKDOWNS_DIR\s*/\s*f"\{[^}]*\}(\.md|_artifacts|_chunks)"'
    r'|KG_DIR\s*/\s*\w+\s*/\s*[\w.]+\.stem\b'
)


def test_no_hand_built_derived_paths_outside_the_helpers():
    root = _P(ing.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _HAND_BUILT.search(line) and "def _" not in line:
                offenders.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    allowed = {"ingest.py"}  # the helpers' own bodies
    offenders = [o for o in offenders if not (o.split(":")[0] in allowed and "return " in o)]
    assert not offenders, "\n".join(offenders)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --group dev pytest test/test_document_key.py::test_no_hand_built_derived_paths_outside_the_helpers -v`
Expected: FAIL, listing `cli.py`, `archive.py` and any `ingest.py` site Tasks 4–5 missed.

- [ ] **Step 3: Fix each offender**
- `cli.py` (~1511): `doc_kg_dir = ingest._document_staging_dir(domain, registered_path, file_result.get("artmind_id") or "")` (check how `cli.py` imports from `artmind.ingest` and match it).
- `archive.py` (~357): `doc_kg_dir = ingest._document_staging_dir(domain, target_path, <the restored id>)` — use the id variable the surrounding function restores under (read the function; it is the id written into `doc_md`).
- Any `ingest.py` leftover: replace with the matching helper.
- `document_identity.markdown_path_for` keeps its `MARKDOWNS_DIR / f"{stem}.md"` (its `stem` is now a key); add `document_identity.py` to `allowed` with that one line, not a blanket exemption.

- [ ] **Step 4: Run to verify**

Run: `uv run --group dev pytest test/test_document_key.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py artmind/archive.py artmind/ingest.py test/test_document_key.py
git commit -m "refactor: every derived path goes through the document-key helpers"
```

---

### Task 7: Bring the existing suite in line

**Files:** the tests listed in the file map.

- [ ] **Step 1: Run the whole suite**

Run: `just dev-test 2>&1 | tail -60`
Expected: failures only where a test hard-codes a new document's name, e.g. `ing.MARKDOWNS_DIR / "deck.md"`, `MARKDOWNS_DIR / "deck_chunks"`, `KG_DIR / "general" / "deck"`.

- [ ] **Step 2: Fix each failure by reading the name from the result, never by re-deriving it**

Pattern, from `test/test_ingest_binary_derived.py::test_first_ingest_converts_and_mints_an_artmind_id`:

```python
    derived_path = ing.Path(result["registered_path"])
    assert derived_path.name == document_key(ing.canonical_path(ing.external_copy_path(source, vault))) + ".md"
```

For a test with no `result` in hand, use `ing.plan_binary(source, "general").registered_path`. `stage_as_extracted` in `test/conftest.py` already derives its folder from `registered_path` — correct for binaries; for a note, change it to `ing._document_staging_dir(domain, Path(file_result["registered_path"]), file_result["artmind_id"])`.

A failure that is **not** a hard-coded name is a real regression: stop and investigate with the diagnosing-bugs skill, don't edit the assertion.

- [ ] **Step 3: Run the whole suite**

Run: `just dev-test 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 4: Plugin fixtures**

Run: `uv run --group dev pytest test/test_plugin_fixtures.py -v`
Expected: pass. If a fixture changed, regenerate with `ARTMIND_REGEN_PLUGIN_FIXTURES=1` and run `just obsidian-plugin-test`.

- [ ] **Step 5: Commit**

```bash
git add test/
git commit -m "test: read derived names from the ingest result"
```

---

### Task 8: Docs

**Files:** `docs/document-identity.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-31-document-key-collisions-design.md`

- [ ] **Step 1:** In `docs/document-identity.md`, add a section `## Derived-file names (document keys)` after "Resolution table": the key format, the four-step choice for binaries, the note rule, the collision guard, "existing documents keep their bare-stem names", and why a uuid is not used (two laptops).
- [ ] **Step 2:** In `CLAUDE.md`'s repo-layout table, add to the `artmind/document_identity.py`… row (or the `ingest.py` row): "Derived files and staging folders are named by `document_key` (`<stem>~<12 hex of the vault path>`), chosen once by `_resolve_binary_key` / `_note_staging_dir`; build their paths only with `ingest._markdown_path/_artifacts_dir/_chunks_dir/_staging_dir` (a test enforces it). Spec `docs/superpowers/specs/2026-10-31-document-key-collisions-design.md`."
- [ ] **Step 3:** In the spec, change §1's helper table to say the path helpers live in `ingest.py` (the fixture reason), and add "the vault-less ad-hoc flow (`_ingest_binary_or_adhoc`)" to Out of scope.
- [ ] **Step 4: Commit**

```bash
git add docs/document-identity.md CLAUDE.md docs/superpowers/specs/2026-10-31-document-key-collisions-design.md
git commit -m "docs: document keys for derived files and staging"
```

---

### Task 9: End to end in a scratch vault (needs a Neo4j the user names)

Not hermetic. Ask the user for a Neo4j to use (bolt port + password) — never probe `localhost:7687`, never use `nbs_audit_2026`'s graph.

- [ ] **Step 1:** Make a scratch vault outside the checkout and outside `~/artmind_vaults` (e.g. `/private/tmp/…/scratchpad/keyvault`), `artmind init` inside it with the user's Neo4j, and a `general` domain.
- [ ] **Step 2:** Copy two different real PDFs into `A/Summary.pdf` and `B/Summary.pdf`, and one note into `A/README.md` and another into `B/README.md`.
- [ ] **Step 3:** From inside the scratch vault: `env -u ANTHROPIC_BASE_URL ARTMIND_NO_PROXY=1 uv run --project <worktree> artmind ingest sync .`
- [ ] **Step 4: Check**
  - `ls .artmind/data/documents/markdowns` shows `Summary~<12hex>.md` twice, each with its own `_artifacts` and `_chunks`.
  - `ls .artmind/data/kg/general` shows four keyed folders.
  - `artmind docs list --domain general` (or the registry) shows four documents, each version 1.
  - Re-run the sync: every file `no_op`.
  - `git mv A/Summary.pdf C/Summary.pdf`, sync again: the same key, no reconversion.
- [ ] **Step 5:** Report the outputs to the user. No commit (nothing in the checkout changed).

---

### Task 10: Repair `nbs_audit_2026` — only after the branch is merged and the user says go

Every step needs the user's go-ahead; it changes their vault.

- [ ] **Step 1:** `just dev-install` from the merged checkout; `just dev-stop-daemons`.
- [ ] **Step 2:** Inside the vault: `artmind docs retire --domain contracts_vector --documentName 01a1924a-81ab-7208-9a1b-823ebe00919d` and the same for `01a1922d-111a-77a1-8377-225cfcbfa591`.
- [ ] **Step 3:** Remove `kg/contracts_vector/Summary/`, `markdowns/Summary.md`, `markdowns/Summary_chunks/`, `markdowns/Summary_artifacts/` (show the user the list first).
- [ ] **Step 4:** `env -u ANTHROPIC_BASE_URL artmind ingest sync` over the 84 `Summary.pdf` files; check 84 `Summary~*` documents in `contracts_vector`.
- [ ] **Step 5:** Resume job `0329980f-cbbc-4cd0-9a21-a80777a58e6d` (`artmind ingest retry-job` for the failed/interrupted row; start the worker for the 53 queued) and report progress.
