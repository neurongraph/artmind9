# Vault sync — Plan D (phase 5: conflicts and frontmatter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge conflicts in artmind's generated files resolve themselves deterministically (`artmind vault resolve`), human notes carry only `_artmind_id` and `_domain` (every per-ingest field moves to the staging folder's `document.json`), and existing vaults move there once with `artmind vault migrate-frontmatter`.

**Architecture:** Spec [2026-09-26-vault-git-transport-design.md](../specs/2026-09-26-vault-git-transport-design.md) §5 R6, R7, §12 item 5; this plan adds §16 A11–A14 and amends D1/D2. A new line-level frontmatter editor (`artmind/frontmatter.py`) is the only thing that writes a note; ingest stamps identity with it once, and the extraction records the per-ingest provenance in `document.json`, which becomes the versioning baseline (`document_identity.ingest_baseline`, with a pre-R6 note's own frontmatter as the fallback). `artmind/frontmatter_migration.py` moves old notes' fields. `artmind/vault_resolve.py` sorts every conflicted path into units (KG folder, table, curation record, other generated file, reported human file), picks one whole side per unit by content-only rules, and stages it — the one git write artmind makes, scoped by `test/test_no_git_writes.py`.

**Built on:** Plans C and C2 (`2026-09-28-vault-sync-plan-c-curation.md`, `…-plan-c2-curation-kinds.md`), which **must be merged first** — the curation-record kinds `vault resolve` has rules for come from them.

**Verified:** every code block in this plan was applied, in task order, by a script that parses this file, to a scratch worktree of Plan C2's final commit (branch `planC2-applied`), and the full suite was run after each task — see "Verification record" at the end. The "Expected: FAIL" claims come from reverting each task's non-test files and re-running its tests.

**Tech Stack:** Python 3.14, Click (rich-click), PyYAML, pytest (`uv run --group dev pytest test/ -q`, ~75 s, `2560 passed, 14 skipped` after Plan C2), real throwaway git repos in `tmp_path` — real merge conflicts for `vault resolve` — and no Neo4j anywhere in this plan's code paths.

---

## Context for the implementer

Read before starting:

- `CLAUDE.md`, "Testing implications" §1–§4.
- The spec above: §2 (D1, D2), §5 R3, R4, R6, R7, §12 item 5, §14, §15.
- `artmind/document_identity.py` (the frontmatter contract, `decide_version`), `artmind/ingest.py` `_ingest_vault_native`, `_ingest_binary_derived`, `_build_file_result_from_db`, `extract_kg` (the `document` dict it stages), `_parse_md_frontmatter`; `artmind/reindex.py`; `artmind/archive.py`; `artmind/vault.py` (`GITIGNORE_BLOCK`, `_BLOCK_START`, `_write_block`); `artmind/vault_doctor.py`; `artmind/vault_sync.py` (`operation_in_progress`, `unresolved_artmind_conflicts`, `preflight`, `_cat_blobs`); `artmind/sync_state.py` `staging_fingerprint`; `artmind/curation_records.py` and the four record kinds (`conflict_records`, `supersession_records`, `lifecycle_records`, `synthesis_records`); `test/test_no_git_writes.py`.
- `test/conftest.py`: autouse fixtures null the Neo4j session and repoint `ARTMIND_HOME`/`ARTMIND_DATA_DIR` at a temp dir. `artmind.ingest` imports `KG_DIR`/`MARKDOWNS_DIR` by value, so tests repoint `artmind.ingest.KG_DIR`; code that must follow a test's repointing at call time reads `paths.KG_DIR`/`paths.MARKDOWNS_DIR`.

Terminology:

- **Fat frontmatter**: what pre-R6 artmind wrote into every note on every ingest — `_version`, `_content_sha256`, `_source_sha256`, `_source_commit`, `_ingested_at`, `_source_path`, `_source_type`, `_status` (`document_identity.MOVED_FIELDS`).
- **Staging folder**: `.artmind/data/kg/<domain>/<note stem>/` — `document.json`, `observations.json`, `chunks.json`, `relationships.json` and a chunk cache — written by `extract_kg` in one atomic swap.
- **Baseline**: what the last successful extraction recorded — `document.json`'s `version`, `content_sha256`, `source_sha256` — that the next ingest versions against.
- **Unit** (`vault resolve`): the set of files one pick covers — a whole staging folder, a table's CSV + `.meta.json`, one curation record, or one other file.

**Line numbers** are those of each file at the moment the replacement is applied (earlier replacements in the same task already done). Every replacement quotes the exact old code: match on the text, not the number.

Run the suite with:

```bash
uv run --group dev pytest test/ -q
```

Do **not** run `just dev-install`, `artmind init` outside `tmp_path`, or anything against a live Neo4j. The user tests live afterwards ("Live verification (user)" at the end).

## Decisions this plan makes (the spec left them open)

Each is argued under "Self-review → Design decisions the spec did not settle"; the number in brackets is its number there.

1. **`vault resolve` takes the whole side with `git checkout <HEAD|MERGE_HEAD> -- <paths>`, not `--ours/--theirs`** [1]. A unit's non-conflicted files (a file only one side changed, which git merged to that side's version; a chunk-cache file only one side has) must follow the pick too, and `--ours/--theirs` only acts on unmerged entries. Files the chosen side lacks are deleted; then `git add -- <paths>` stages it all. Pathspecs are literal (`GIT_LITERAL_PATHSPECS=1`). Nothing else is written, no commit is made, and `_assert_scoped` refuses any path outside `.artmind/data/` before a git command runs.
2. **Rules read content, never the side's name** [2], so machine A merging B and machine B merging A pick the same bytes and their merge commits merge cleanly; an exact tie means identical content.
3. **KG folder rule** [3]: the side whose `document.json` `content_sha256` equals the merged note's body hash (note found by `source_path`, else by `_artmind_id` across the vault's notes); if the merge deleted the note, the side without the folder; otherwise the greater staging fingerprint. A folder whose note still has conflict markers in the working tree is **pending** (the note's body decides); a note resolved in Obsidian but not yet staged counts as resolved.
4. **Table rule** [4]: the side that kept the table, then the later `table.ingested_at` (the spec's `refreshed_at` does not exist; `ingested_at` is the table's last refresh), then the greater digest of the two files.
5. **Record rules** [5]: a kept record beats a deleted one (every kind); then *conflicts* — a decision (`resolved`/`dismissed`) beats `open`, then the later change (`resolved_at`, else `detected_at`); *supersessions* — a `manual` assertion beats a detected one; *syntheses* — the later `created_at`; *lifecycle* — no key (its content is fixed by its id); then the greater record fingerprint for all. Settles Plan C leftover (b).
6. **Other files under `.artmind/data/`** [6] (a converted binary's markdown, a legacy `manifest.json`, a committed scratch file): the side that kept it, then the greater content hash. Human notes and `.artmind/` files outside `data/` (`same_as.yaml`, schemas, mappings, `vault.yaml`) are reported only.
7. **The per-ingest fields are recorded by the extraction, not at ingest** [7]: `_ingest_vault_native` passes them to `extract_kg` in `file_result["provenance"]`, and `extract_kg` writes them into `document.json`. So a version whose extraction failed is never recorded as done — the fat frontmatter recorded the new hash before extracting, and a failed extraction then read as "unchanged" forever. The new `document.json` keys become `:Document` properties like every other key there.
8. **`_status` is dropped, not moved** [8]: it was always `latest` and nothing read it; a retirement is a lifecycle record. `_valid_from`/`_valid_to`/`_valid_time_source` are human input and stay in the note.
9. **artmind stops seeding authored fields** (`title`, `created_on`, `declared_version`) into notes [9]; the Document's `title` defaults to the file stem at extraction. A converted binary's markdown follows the same identity-only contract.
10. **The baseline lookup** [10]: `kg/<domain>/<stem>/`, then the note's stem before a `move`, then — only for `adopt`/`heal` — every folder in the domain by id; a domain change looks in the old domain's folder too. A `new` note looks nowhere. A baseline with a version but no hash continues that version (+1), never restarts at 1.
11. **A note is stamped before its version is decided** [11], and re-read, so the body hashed is the body as it stands after the stamp. A note whose frontmatter cannot be edited line by line (unparsable YAML) fails that file's ingest with a clear error instead of being overwritten.
12. **Migration copies only absent keys, and the four ingest-bound ones only when versions match** [12]: `content_sha256`, `source_sha256`, `source_commit`, `ingested_at` are copied only when the note's `_version` equals the staged `version` (otherwise that extraction never landed and the next ingest must re-extract). A note with no staging folder keeps its fields and is reported. No graph write: each machine's next `vault sync` replays migrated folders once (their fingerprints changed), at no LLM cost.
13. **Note rewrites are atomic**, through a temp file in the vault's `.artmind/data/` renamed over the note, and the `.gitignore` block **v4** ignores scratch *files* under `.artmind/data/` [13] — Plan C's record temp files and these. `vault doctor` names both block versions.

## File map

| File | Change |
|---|---|
| `artmind/vault.py` | `GITIGNORE_BLOCK` v4 (scratch file rules); `block_version` (Task 1) |
| `artmind/vault_doctor.py` | an outdated block names both versions (Task 1) |
| `artmind/frontmatter.py` | new: line-level `set_fields`, `remove_fields`, `read_note`, atomic `write_note` (Task 2) |
| `artmind/document_identity.py` | `IDENTITY_FIELDS`, `MOVED_FIELDS`, `ingest_baseline`, `needs_stamp`, `decide_version` continues a hash-less version (Task 3); `build_frontmatter`/`frontmatter_unchanged` removed (Task 4) |
| `artmind/ingest.py` | identity-only stamp, `staged_document`, `_stamp_note`, `provenance` → `document.json`, binary baseline, retry baseline (Task 4) |
| `artmind/reindex.py`, `artmind/archive.py` | read the baseline / `source_type` from `document.json`; archive's new-id restore edits one line (Task 5) |
| `artmind/vault_sync.py` | `worker_running` (Task 6); preflight names `vault resolve` (Task 8) |
| `artmind/frontmatter_migration.py` | new: `migrate` (Task 6) |
| `artmind/vault_resolve.py` | new: `plan`, `resolve` and the per-unit rules (Task 7) |
| `artmind/cli.py` | `vault migrate-frontmatter` (Task 6); `vault resolve`, status line, docstrings (Task 8) |
| `test/test_no_git_writes.py` | the scoped exception (Task 7) |
| `test/_historical_blocks.py`, `test/test_vault_scaffold.py`, `test/test_vault_doctor.py` | v3 fixture and upgrade tests (Task 1) |
| `test/test_frontmatter.py`, `test/test_frontmatter_migration.py`, `test/test_vault_resolve.py` | new |
| `test/test_document_identity.py`, `test/conftest.py`, `test/test_ingest_vault_native.py`, `test/test_ingest_binary_derived.py`, `test/test_ingest_extract_kg_retry.py`, `test/test_reindex.py`, `test/test_archive.py` | extended; tests that encoded "the note carries its own baseline" now stage `document.json` first (`conftest.stage_as_extracted`) |
| spec (D1, D2, §3, §10, new §16), `docs/vault.md`, `docs/document-identity.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md`, `artmind/skills/artmind-curate/SKILL.md`, `justfile` | prose (Task 9) |

---

### Task 1: `.gitignore` block v4 ignores scratch files; `vault doctor` names both versions

Plan C writes each curation record through a temp FILE (`<id>.json.artmind-tmp`), and Task 6's note rewrites use a temp file under `.artmind/data/` too. Block v3 ignores only scratch *folders* (`*.artmind-tmp/`), so a crash between write and rename leaves a file Obsidian Git commits. v4 adds file rules. Plan A's versioned-block machinery (`_write_block`) already replaces an older block in place; a frozen v3 block joins the historical-upgrade test to prove v3 → v4 is byte-exact, LF and CRLF. `vault doctor` already fails an outdated block; it now says which version the vault has and which this artmind writes.

**Files:**
- Modify: `artmind/vault.py`
- Modify: `artmind/vault_doctor.py`
- Test: `test/_historical_blocks.py`, `test/test_vault_scaffold.py`, `test/test_vault_doctor.py`

- [ ] **Step 1: Write the failing tests**

Freeze the current (v3) block as a fixture — **before** touching `artmind/vault.py`.

**Append to** `test/_historical_blocks.py`:

```python
V3_GITIGNORE_BLOCK = "# ── artmind (v3) ──────────────────────────────────────────────────────────────\n# .artmind/ belongs to artmind and is versioned with your vault, so a clone\n# reproduces the graph without paying for extraction again. These are the\n# exceptions, and each is a secret, a churning binary, or machine-local state.\n\n# Holds the graph password. A vault is a repo you may push.\n.artmind/config.env\n# A SQLite binary rewritten on every ingest; merges catastrophically, and\n# `artmind docs reindex` rebuilds it from vault frontmatter.\n.artmind/data/document_registry.db\n.artmind/data/document_registry.db-shm\n.artmind/data/document_registry.db-wal\n# Machine-local runtime state, meaningless on another machine.\n.artmind/logs/\n.artmind/state.json\n.artmind/serve.json\n.artmind/worker.pid\n# The chat/admin UI agent's CLAUDE_CONFIG_DIR (webui/backends): auth config\n# and full conversation transcripts. Private, and per machine.\n.artmind/.claude-sdk-auth/\n# artmind's own skills are symlinks to the installed copy; yours are not\n# matched by this and stay committable.\n.claude/skills/artmind-*\n# Same deal for the opencode/ACP persona (custom agent modes): artmind's own\n# are symlinks to the installed copy; a custom agent you author yourself\n# (any other .md name under .opencode/agent/) is not matched and stays\n# committable.\n.opencode/agent/artmind*.md\n\n# Locally-cached chunk vectors, split out of committed chunks.json into this\n# sidecar precisely so it can be excluded. Derived from (text, model), and\n# undeltable -- ten versions of one chunks.json cost 60 KB of git objects with\n# embeddings inline and 20 KB with them split into embeddings.json instead. A\n# clone has no embeddings.json and rebuilds it once.\n.artmind/data/kg/**/embeddings.json\n\n# Snapshots: large, opaque, and already a complete copy of what git versions.\n# By extension rather than path, so one dropped anywhere stays out of both git\n# and ingestion.\n*.zip\n*.tar.gz\n*.tgz\n\n# The structured store's parquet + DuckDB catalog are a rebuildable cache, not\n# the source of truth: `.artmind/data/structured_text/` (CSV + per-table .meta.json,\n# written by `db export-text`) is what's committed, and `db reindex` rebuilds\n# these from it. Committing the binaries too would make every ingest a\n# growing, undiffable blob in git history for no benefit over the text.\n.artmind/data/structured/**/*.parquet\n.artmind/data/structured/*.duckdb\n.artmind/data/structured/*.duckdb.wal\n\n# Regenerated by `vault sync` from structured_text CSV + the table mapping,\n# the same way parquet is. Committing them made sync create commits on the\n# receiving machine (spec 2026-09-26 R4).\n.artmind/data/kg/*/table__*/\n# Atomic-write scratch (R2). A crash can leave one behind; the next write of\n# that document removes it.\n.artmind/data/**/*.artmind-tmp/\n.artmind/data/**/*.artmind-old/\n\n# Not artmind's files, but a vault synced between machines needs them out:\n# OS litter, and Obsidian's per-device pane layout, which changes on every\n# click and so conflicts whenever two machines commit it. The rest of\n# .obsidian/ (settings, plugins, their config) stays committed -- it is what\n# makes the second machine behave like the first.\n.DS_Store\nThumbs.db\n/.obsidian/workspace.json\n/.obsidian/workspace-mobile.json\n# ── end artmind ───────────────────────────────────────────────────────────────\n"  # planC2-applied b6a25ad
```

(If you generate it yourself instead: `uv run python -c "from artmind.vault import GITIGNORE_BLOCK as b; print('V3_GITIGNORE_BLOCK = ' + repr(b) + '  # planC2-applied b6a25ad')" >> test/_historical_blocks.py`, run before Step 3.)

**Replace in** `test/test_vault_scaffold.py` (lines 13-15):

```python
    V2_GITIGNORE_BLOCK_A,
    V2_GITIGNORE_BLOCK_B,
)
```

**with:**

```python
    V2_GITIGNORE_BLOCK_A,
    V2_GITIGNORE_BLOCK_B,
    V3_GITIGNORE_BLOCK,
)
```

**Replace in** `test/test_vault_scaffold.py` (lines 615-621):

```python
    "v2-b": V2_GITIGNORE_BLOCK_B,
}


@pytest.mark.parametrize("name", sorted(_HISTORICAL_GITIGNORE_BLOCKS))
@pytest.mark.parametrize("crlf", [False, True])
def test_a_real_historical_block_upgrades_to_v3_byte_exactly(tmp_path, name, crlf):
```

**with:**

```python
    "v2-b": V2_GITIGNORE_BLOCK_B,
    "v3": V3_GITIGNORE_BLOCK,
}


@pytest.mark.parametrize("name", sorted(_HISTORICAL_GITIGNORE_BLOCKS))
@pytest.mark.parametrize("crlf", [False, True])
def test_a_real_historical_block_upgrades_to_current_byte_exactly(tmp_path, name, crlf):
```

**Replace in** `test/test_vault_scaffold.py` (lines 665-666):

```python
    assert _ignored(tmp_path, ".artmind/data/kg/banking/doc.artmind-old/document.json")
    assert not _ignored(tmp_path, ".artmind/data/kg/banking/doc/document.json")
```

**with:**

```python
    assert _ignored(tmp_path, ".artmind/data/kg/banking/doc.artmind-old/document.json")
    assert not _ignored(tmp_path, ".artmind/data/kg/banking/doc/document.json")


def test_scratch_files_are_ignored_as_well_as_scratch_folders(tmp_path):
    """A curation record's temp file (Plan C) and a note rewrite's temp file
    are FILES; v3 ignored only `*.artmind-tmp/` folders, so a crash between
    write and rename left a file Obsidian Git would commit (block v4)."""
    _init_repo(tmp_path)
    vault.write_gitignore(tmp_path)

    assert _ignored(tmp_path, ".artmind/data/curation/conflicts/abc.json.artmind-tmp")
    assert _ignored(tmp_path, ".artmind/data/0f3a.artmind-tmp")
    assert _ignored(tmp_path, ".artmind/data/kg/banking/doc/document.json.artmind-old")
    assert not _ignored(tmp_path, ".artmind/data/curation/conflicts/abc.json")
    assert not _ignored(tmp_path, "notes/draft.artmind-tmp"), "only artmind's data dir is scratch space"
```

**Replace in** `test/test_vault_doctor.py` (line 71):

```python
def test_a_missing_gitattributes_fails(repo):
```

**with:**

```python
def test_a_v3_block_is_flagged_by_version_and_its_tracked_scratch_file_listed_after_init(repo):
    """A vault initialised before block v4 ignores scratch folders only: a
    curation record's temp file a crash left behind was committable. Doctor
    names both versions; after `artmind init` (write_gitignore) the file is
    ignored-but-tracked and doctor prints the exact untrack command."""
    from _historical_blocks import V3_GITIGNORE_BLOCK

    (repo / ".gitignore").write_text(V3_GITIGNORE_BLOCK)
    scratch = repo / ".artmind" / "data" / "curation" / "conflicts" / "abc.json.artmind-tmp"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("{}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a crash left a record's scratch file, and it was committed")

    result = doc.run(repo)

    block = _by_name(result)[".gitignore artmind block"]
    assert block["status"] == "fail"
    assert block["detail"] == "artmind block outdated (v3; this artmind writes v4)"
    assert "artmind init" in block["fix"]
    assert _by_name(result)["ignored paths still tracked"]["status"] == "ok", "v3 does not ignore a file"

    assert vault.write_gitignore(repo) is True
    tracked = _by_name(doc.run(repo))["ignored paths still tracked"]

    assert tracked["status"] == "fail"
    assert tracked["fix"] == "git rm -r --cached -- .artmind/data/curation/conflicts/abc.json.artmind-tmp"


def test_a_missing_gitattributes_fails(repo):
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_scaffold.py test/test_vault_doctor.py -q`
Expected: `4 failed, 67 passed` — the v3 fixture reads as `current` (the block is still v3), the scratch-file paths are not ignored, and doctor's detail has no versions.

- [ ] **Step 3: Implement**

**Replace in** `artmind/vault.py` (lines 236-237):

```python
GITIGNORE_BLOCK = """\
# ── artmind (v3) ──────────────────────────────────────────────────────────────
```

**with:**

```python
GITIGNORE_BLOCK = """\
# ── artmind (v4) ──────────────────────────────────────────────────────────────
```

**Replace in** `artmind/vault.py` (lines 293-296):

```python
# Atomic-write scratch (R2). A crash can leave one behind; the next write of
# that document removes it.
.artmind/data/**/*.artmind-tmp/
.artmind/data/**/*.artmind-old/
```

**with:**

```python
# Atomic-write scratch (R2): a staging folder's `.artmind-tmp`/`.artmind-old`
# sibling folder, and a scratch FILE -- a curation record's temp file, or a
# note rewrite's (`vault migrate-frontmatter`). A crash can leave one behind;
# the next write of that document or record removes it.
.artmind/data/**/*.artmind-tmp/
.artmind/data/**/*.artmind-old/
.artmind/data/**/*.artmind-tmp
.artmind/data/**/*.artmind-old
```

**Replace in** `artmind/vault.py` (line 400):

```python
def _write_block(path: Path, block: str) -> bool:
```

**with:**

```python
def block_version(text: str) -> int | None:
    """The version in the first artmind block header found in `text` -- 1 for
    the original unversioned header -- or None when there is none. Pass a
    file's text to read what a vault holds, or a block constant to read what
    this artmind writes (`vault doctor` names both)."""
    match = _BLOCK_START.search(text)
    if match is None:
        return None
    return int(match.group(1)) if match.group(1) else 1


def _write_block(path: Path, block: str) -> bool:
```

**Replace in** `artmind/vault_doctor.py` (line 18):

```python
from artmind.vault import GITATTRIBUTES_BLOCK, GITIGNORE_BLOCK, VaultLayout, block_status
```

**with:**

```python
from artmind.vault import GITATTRIBUTES_BLOCK, GITIGNORE_BLOCK, VaultLayout, block_status, block_version
```

**Replace in** `artmind/vault_doctor.py` (lines 70-73):

```python
    return Check(
        name, FAIL, f"artmind block {status}",
        "run `artmind init` in the vault root (rewrites only artmind's block; your own rules are kept)",
    )
```

**with:**

```python
    detail = f"artmind block {status}"
    if status == "outdated":
        path = vault_dir / filename
        found = block_version(path.read_bytes().decode("utf-8", errors="replace"))
        detail += f" (v{found}; this artmind writes v{block_version(block)})"
    return Check(
        name, FAIL, detail,
        "run `artmind init` in the vault root (rewrites only artmind's block; your own rules are kept)",
    )
```

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `uv run --group dev pytest test/test_vault_scaffold.py test/test_vault_doctor.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2564 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault.py artmind/vault_doctor.py test/_historical_blocks.py test/test_vault_scaffold.py test/test_vault_doctor.py
git commit -m "feat(vault): .gitignore block v4 ignores scratch files; doctor names both block versions" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `artmind/frontmatter.py` — line-level note edits

The only code that will write a human note. `set_fields` stamps keys (in place when present, at the top of the block when missing, a new block when the note has none); `remove_fields` drops keys with their continuation lines (a folded long value, a block list). Neither parses-and-dumps YAML: the user's other lines, their order, comments, quoting, the line ending and every body byte are untouched, and each edit is checked by parsing the result — a mapping that is not exactly the expected one raises `FrontmatterEditError` and nothing is written. `write_note` is atomic (temp file, fsync, rename, mode kept); its temp file goes to the vault's `.artmind/data/` (ignored by block v4) when given, else beside the note.

**Files:**
- Create: `artmind/frontmatter.py`
- Test: `test/test_frontmatter.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_frontmatter.py`:

```python
"""Text-level frontmatter edits (spec 2026-09-26 §5 R6): only the lines
artmind must change move; the body, the user's keys, their order, comments,
quoting and line endings stay byte-for-byte."""
import os
import stat

import pytest

from artmind import frontmatter as fm
from artmind.ingest import _parse_md_frontmatter

USER_BLOCK = (
    "---\n"
    'title: "My note"   # my own comment\n'
    "tags:\n"
    "- alpha\n"
    "- beta\n"
    "aliases: [x, y]\n"
    "---\n"
)
BODY = "\n# Heading\n\nBody text -- with trailing spaces   \n"


def test_a_note_without_frontmatter_gets_a_block_and_keeps_every_byte():
    text = "# Heading\n\nBody.\n"

    new = fm.set_fields(text, {"_artmind_id": "0192-id", "_domain": "banking.ops"})

    assert new == "---\n_artmind_id: 0192-id\n_domain: banking.ops\n---\n" + text
    meta, body = _parse_md_frontmatter(new)
    assert meta == {"_artmind_id": "0192-id", "_domain": "banking.ops"}
    assert body == text


def test_a_crlf_note_without_frontmatter_gets_crlf_lines():
    text = "# Heading\r\n\r\nBody.\r\n"

    new = fm.set_fields(text, {"_artmind_id": "id", "_domain": "d"})

    assert new == "---\r\n_artmind_id: id\r\n_domain: d\r\n---\r\n" + text


def test_stamping_keeps_the_users_keys_their_order_comments_and_quoting():
    text = USER_BLOCK + BODY

    new = fm.set_fields(text, {"_artmind_id": "id-1", "_domain": "general"})

    assert new == (
        "---\n"
        "_artmind_id: id-1\n"
        "_domain: general\n"
        'title: "My note"   # my own comment\n'
        "tags:\n"
        "- alpha\n"
        "- beta\n"
        "aliases: [x, y]\n"
        "---\n"
    ) + BODY


def test_a_domain_change_rewrites_only_that_line_in_place():
    text = "---\ntitle: T\n_domain: general\n_artmind_id: id-1\nextra: 1\n---\nBody\n"

    new = fm.set_fields(text, {"_artmind_id": "id-1", "_domain": "banking.ops"})

    assert new == "---\ntitle: T\n_domain: banking.ops\n_artmind_id: id-1\nextra: 1\n---\nBody\n"


def test_a_value_that_is_already_right_is_not_rewritten():
    text = "---\n_artmind_id: 'id-1'\n_domain: \"general\"\n---\nBody\n"

    assert fm.set_fields(text, {"_artmind_id": "id-1", "_domain": "general"}) == text


def test_remove_fields_drops_the_fat_block_and_nothing_else_crlf_preserved():
    long_path = "notes/" + "very-long-folder-name/" * 5 + "note.md"
    text = (
        "---\r\n"
        "_artmind_id: id-1\r\n"
        "_version: 3\r\n"
        "_content_sha256: " + "a" * 64 + "\r\n"
        "_domain: general\r\n"
        "_status: latest\r\n"
        "_source_commit: " + "b" * 40 + "\r\n"
        "_source_path: " + long_path[:60] + "\r\n"
        "  " + long_path[60:] + "\r\n"
        "_source_type: md\r\n"
        "_ingested_at: '2026-09-01T10:00:00+00:00'\r\n"
        "title: Mine\r\n"
        "tags:\r\n"
        "- a\r\n"
        "---\r\n"
        "\r\nBody\r\n"
    )
    moved = ("_version", "_content_sha256", "_status", "_source_commit", "_source_path", "_source_type", "_ingested_at")

    new = fm.remove_fields(text, moved)

    assert new == (
        "---\r\n"
        "_artmind_id: id-1\r\n"
        "_domain: general\r\n"
        "title: Mine\r\n"
        "tags:\r\n"
        "- a\r\n"
        "---\r\n"
        "\r\nBody\r\n"
    )


def test_removing_keys_that_are_not_there_changes_nothing():
    text = USER_BLOCK + BODY

    assert fm.remove_fields(text, ("_version", "_ingested_at")) == text
    assert fm.remove_fields("no frontmatter\n", ("_version",)) == "no frontmatter\n"


def test_a_key_the_line_editor_cannot_see_is_refused_not_half_removed():
    text = '---\n"_version": 2\n_artmind_id: id\n---\nBody\n'

    with pytest.raises(fm.FrontmatterEditError):
        fm.remove_fields(text, ("_version",))


def test_unparsable_frontmatter_is_refused():
    with pytest.raises(fm.FrontmatterEditError):
        fm.set_fields("---\n: : bad: [\n---\nBody\n", {"_artmind_id": "id"})


def test_write_note_is_atomic_keeps_the_mode_and_leaves_no_scratch(tmp_path):
    note = tmp_path / "notes" / "a.md"
    note.parent.mkdir()
    note.write_bytes(b"old\r\n")
    os.chmod(note, 0o600)
    scratch = tmp_path / ".artmind" / "data"

    fm.write_note(note, "new\r\n", scratch_dir=scratch)

    assert note.read_bytes() == b"new\r\n"
    assert stat.S_IMODE(note.stat().st_mode) == 0o600
    assert list(scratch.iterdir()) == []
    assert sorted(p.name for p in note.parent.iterdir()) == ["a.md"]


def test_read_note_keeps_crlf(tmp_path):
    note = tmp_path / "a.md"
    note.write_bytes(b"---\r\n_domain: d\r\n---\r\nBody\r\n")

    assert fm.read_note(note) == "---\r\n_domain: d\r\n---\r\nBody\r\n"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_frontmatter.py -q`
Expected: `1 error` during collection — `ImportError: cannot import name 'frontmatter' from 'artmind'`.

- [ ] **Step 3: Implement**

**Create** `artmind/frontmatter.py`:

```python
"""Text-level frontmatter edits for human notes (spec 2026-09-26 §5 R6).

artmind writes a human note only to stamp its identity -- `_artmind_id` and
`_domain`, once, and again only when `_domain` genuinely changes -- and,
once per vault, when `artmind vault migrate-frontmatter` removes the
per-ingest fields older artmind wrote there. Each edit here changes only the
frontmatter lines it has to:

- every byte after the closing `---` (the body) is left as it is;
- the user's own keys keep their text, order, comments and quoting;
- a line artmind adds uses the file's own line ending (LF or CRLF).

A YAML round trip (parse, then dump) would reorder keys, re-quote values and
drop comments, so none is done. Every edit is checked instead: the edited
block must parse to exactly the mapping expected, or `FrontmatterEditError`
is raised and the caller writes nothing.

"Frontmatter" is recognised exactly as `ingest._parse_md_frontmatter` does:
the text starts with `---`, and the block runs to the next line that starts
with `---`.
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import uuid
from pathlib import Path

import yaml

#: A scratch file's suffix -- `.gitignore` block v4 ignores it under
#: `.artmind/data/` (spec R2, R4).
TMP_SUFFIX = ".artmind-tmp"


class FrontmatterEditError(ValueError):
    """The frontmatter could not be edited without touching more than the
    lines asked for (unparsable YAML, not a mapping, or a key written in a
    form the line editor does not recognise). Nothing was written."""


def _split(text: str) -> tuple[str, str, str] | None:
    """`(head, block, tail)`: `head` is the opening `---`, `block` runs from
    right after it through the newline before the closing `---`, and `tail`
    starts at the closing `---`. None when the text has no frontmatter."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[:3], text[3:end + 1], text[end + 1:]


def _lines(block: str) -> list[str]:
    """`block` as lines, each with its own ending. Only `\\n` splits a line
    (unlike `str.splitlines`), so no other character can move a byte."""
    return re.findall(r"[^\n]*\n|[^\n]+$", block)


def _eol(line: str) -> str:
    return "\r\n" if line.endswith("\r\n") else "\n"


def _load(block: str) -> dict:
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        raise FrontmatterEditError(f"frontmatter is not valid YAML: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterEditError("frontmatter is not a YAML mapping")
    return data


def _key_line(key: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(key)}[ \t]*:(?=[ \t\r\n]|$)")


def _is_continuation(line: str) -> bool:
    """A line belonging to the key above it: indented (a folded scalar, a
    nested mapping, an indented list) or a block-sequence entry at column 0
    (`tags:` followed by `- a`)."""
    return line.startswith((" ", "\t")) or re.match(r"-(?=[ \t\r\n]|$)", line) is not None


def _key_span(lines: list[str], key: str) -> tuple[int, int] | None:
    pattern = _key_line(key)
    for i in range(1, len(lines)):
        if pattern.match(lines[i]):
            j = i + 1
            while j < len(lines) and _is_continuation(lines[j]):
                j += 1
            return i, j
    return None


def _render(key: str, value: object, eol: str) -> str:
    dumped = yaml.safe_dump(
        {key: value}, allow_unicode=True, default_flow_style=False, sort_keys=False, width=float("inf"),
    )
    return dumped.replace("\n", eol)


def has_frontmatter(text: str) -> bool:
    return _split(text) is not None


def set_fields(text: str, updates: dict[str, object]) -> str:
    """`text` with each key in `updates` set to its value.

    A key already present with that value is left exactly as written; one
    present with another value has its line(s) replaced in place; a missing
    key is inserted at the top of the block, in `updates` order. A note with
    no frontmatter gets a new block holding just `updates`, and every byte of
    the note follows it unchanged."""
    split = _split(text)
    if split is None:
        first_line = _lines(text)[0] if text else "\n"
        eol = _eol(first_line)
        block = eol + "".join(_render(k, v, eol) for k, v in updates.items())
        if _load(block) != dict(updates):
            raise FrontmatterEditError(f"could not render {sorted(updates)} as frontmatter")
        return "---" + block + "---" + eol + text

    head, block, tail = split
    before = _load(block)
    lines = _lines(block)
    eol = _eol(lines[0]) if lines else "\n"
    if not lines:
        lines = [eol]
    missing = []
    for key, value in updates.items():
        if key in before and before[key] == value:
            continue
        span = _key_span(lines, key)
        if span is None:
            missing.append(key)
            continue
        i, j = span
        lines[i:j] = [_render(key, value, eol)]
    lines[1:1] = [_render(key, updates[key], eol) for key in missing]
    new_block = "".join(lines)
    if _load(new_block) != {**before, **updates}:
        raise FrontmatterEditError(f"setting {sorted(updates)} would change other frontmatter keys")
    return head + new_block + tail


def remove_fields(text: str, keys) -> str:
    """`text` without the top-level `keys` (each with its continuation
    lines). Keys that are not there are ignored; a note with no frontmatter
    comes back unchanged."""
    split = _split(text)
    if split is None:
        return text
    head, block, tail = split
    before = _load(block)
    doomed = [k for k in keys if k in before]
    if not doomed:
        return text
    lines = _lines(block)
    for key in doomed:
        span = _key_span(lines, key)
        if span is not None:
            del lines[span[0]:span[1]]
    new_block = "".join(lines)
    expected = {k: v for k, v in before.items() if k not in doomed}
    if _load(new_block) != expected:
        raise FrontmatterEditError(
            f"could not remove {sorted(doomed)} line by line (a key written in a form artmind "
            "does not recognise?) -- remove them by hand"
        )
    return head + new_block + tail


def read_note(path: Path) -> str:
    """A note's text exactly as stored -- no newline translation, so a CRLF
    note stays CRLF through an edit."""
    return Path(path).read_bytes().decode("utf-8")


def write_note(path: Path, text: str, *, scratch_dir: Path | None = None) -> None:
    """Replace `path` with `text` atomically: a temp file, fsynced, renamed
    over it -- an interrupted write leaves the old note or the new one, never
    half of either. The temp file is created in `scratch_dir` (the vault's
    `.artmind/data/`, where `.gitignore` ignores it) when given, else beside
    the note; the note's permission bits are kept."""
    path = Path(path)
    data = text.encode("utf-8")
    for directory in ([Path(scratch_dir)] if scratch_dir is not None else []) + [path.parent]:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".{uuid.uuid4().hex}{TMP_SUFFIX}"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                shutil.copymode(path, tmp)
            except OSError:
                pass
            os.replace(tmp, path)
            return
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
        finally:
            tmp.unlink(missing_ok=True)
    raise OSError(errno.EXDEV, f"could not rename a temp file over {path}")
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_frontmatter.py -q`
Expected: `11 passed`.

Run: `uv run --group dev pytest test/ -q`
Expected: `2576 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/frontmatter.py test/test_frontmatter.py
git commit -m "feat(frontmatter): line-level note edits that keep the body, key order and line endings" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: the versioning baseline — `document.json` first, legacy frontmatter as the fallback

Pure functions, used from Task 4 on. `MOVED_FIELDS` maps each fat-frontmatter key to its `document.json` key (`_status` to None: dropped). `ingest_baseline(staged, frontmatter)` returns `{_content_sha256, _version, _source_sha256}` from `document.json` once it records a `content_sha256`, else from the note's legacy fields (with `document.json`'s `version` standing in when the note has none). `needs_stamp` says whether a note's identity must be (re)written. `decide_version` keeps its signature (callers now pass the baseline) and continues a hash-less version instead of restarting at 1.

**Files:**
- Modify: `artmind/document_identity.py`
- Test: `test/test_document_identity.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_document_identity.py` (lines 9-22):

```python
from artmind.document_identity import (
    AUTHORED_FIELDS,
    IdentityConflict,
    Resolution,
    SYSTEM_FIELDS,
    build_frontmatter,
    canonical_path,
    compute_content_sha256,
    decide_version,
    frontmatter_unchanged,
    lift_declared_version,
    markdown_path_for,
    mint_artmind_id,
    render_document,
```

**with:**

```python
from artmind.document_identity import (
    AUTHORED_FIELDS,
    IDENTITY_FIELDS,
    IdentityConflict,
    MOVED_FIELDS,
    Resolution,
    SYSTEM_FIELDS,
    build_frontmatter,
    canonical_path,
    compute_content_sha256,
    decide_version,
    frontmatter_unchanged,
    ingest_baseline,
    lift_declared_version,
    markdown_path_for,
    mint_artmind_id,
    needs_stamp,
    render_document,
```

**Replace in** `test/test_document_identity.py` (lines 195-199):

```python
def test_decide_version_body_unchanged_is_metadata_only():
    sha = compute_content_sha256("same body")
    decision = decide_version("same body", existing_meta={"_content_sha256": sha, "_version": 2})
    assert decision.tier == "metadata_only"
    assert decision.version == 2
```

**with:**

```python
def test_decide_version_body_unchanged_is_metadata_only():
    sha = compute_content_sha256("same body")
    decision = decide_version("same body", existing_meta={"_content_sha256": sha, "_version": 2})
    assert decision.tier == "metadata_only"
    assert decision.version == 2


def test_decide_version_without_a_prior_hash_continues_the_staged_version():
    """A pre-R6 staging folder has a version but no hash, and a migrated note
    no longer says: the next ingest is a content change at version + 1, never
    a restart at 1 that would reuse an old version's observation ids."""
    decision = decide_version("body", existing_meta={"_content_sha256": None, "_version": 3})
    assert decision.tier == "content"
    assert decision.version == 4


# ── the baseline: document.json first, legacy frontmatter as a fallback ─────


def test_ingest_baseline_is_document_json_once_it_records_a_hash():
    staged = {"id": "id-1", "version": 5, "content_sha256": "new", "source_sha256": "bin"}
    fat = {"_content_sha256": "old", "_version": 2, "_source_sha256": "oldbin"}

    assert ingest_baseline(staged, fat) == {"_content_sha256": "new", "_version": 5, "_source_sha256": "bin"}


def test_ingest_baseline_falls_back_to_legacy_frontmatter():
    staged = {"id": "id-1", "version": 2}  # pre-R6 document.json: no hash
    fat = {"_content_sha256": "old", "_version": 2, "_source_sha256": "oldbin"}

    assert ingest_baseline(staged, fat) == {"_content_sha256": "old", "_version": 2, "_source_sha256": "oldbin"}
    assert ingest_baseline(None, {}) == {"_content_sha256": None, "_version": None, "_source_sha256": None}
    assert ingest_baseline(staged, {"_artmind_id": "id-1"})["_version"] == 2


def test_needs_stamp_only_for_missing_or_changed_identity():
    assert needs_stamp({}, "id-1", "general") is True
    assert needs_stamp({"_artmind_id": "id-1"}, "id-1", "general") is True
    assert needs_stamp({"_artmind_id": "id-1", "_domain": "other"}, "id-1", "general") is True
    assert needs_stamp({"_artmind_id": "id-0", "_domain": "general"}, "id-1", "general") is True
    assert needs_stamp({"_artmind_id": "id-1", "_domain": "general", "_version": 9, "tags": ["x"]}, "id-1", "general") is False


def test_moved_fields_are_system_fields_and_never_identity():
    assert set(MOVED_FIELDS) <= set(SYSTEM_FIELDS)
    assert not set(MOVED_FIELDS) & set(IDENTITY_FIELDS)
    assert set(IDENTITY_FIELDS) <= set(SYSTEM_FIELDS)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_document_identity.py -q`
Expected: `1 error` during collection — `ImportError: cannot import name 'IDENTITY_FIELDS' from 'artmind.document_identity'`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/document_identity.py` (lines 24-27):

```python
# ── the frontmatter contract ─────────────────────────────────────────────────
# System: artmind writes these; extraction must never emit them. The
# underscore *is* the rule — it means artmind owns the property. Order here is
# the order they're serialized in, so the file reads sensibly to a human.
```

**with:**

```python
# ── the frontmatter contract ─────────────────────────────────────────────────
# System: artmind owns these; extraction must never emit them. The underscore
# *is* the rule — it means artmind owns the property. Order here is the order
# `serialize_frontmatter` writes them in. Since spec 2026-09-26 R6 artmind
# writes only `IDENTITY_FIELDS` into a human note; the per-ingest fields
# (`MOVED_FIELDS`) live in the staging folder's `document.json`, and older
# notes that still carry them are read as a fallback. `_valid_from`/
# `_valid_to`/`_valid_time_source` are a human's input, never written by
# artmind, and stay in the note.
```

**Replace in** `artmind/document_identity.py` (lines 53-55):

```python
# Authored: artmind seeds a value once (only if absent), then never touches it
# again — a human's edit to any of these must survive every future ingest.
AUTHORED_FIELDS = (
```

**with:**

```python
#: The only fields artmind writes into a human note (spec R6): once, at the
#: first ingest, and again only when `_domain` genuinely changes.
IDENTITY_FIELDS = ("_artmind_id", "_domain")

#: Per-ingest fields older artmind wrote into every note on every ingest,
#: mapped to their `document.json` key. `_status` maps to None: it was always
#: "latest" and nothing reads it (a retirement is a lifecycle record, spec
#: §15 A10), so it is dropped rather than moved. `vault migrate-frontmatter`
#: moves these once; until then they are read as a fallback.
MOVED_FIELDS = {
    "_version": "version",
    "_content_sha256": "content_sha256",
    "_source_sha256": "source_sha256",
    "_source_commit": "source_commit",
    "_ingested_at": "ingested_at",
    "_source_path": "source_path",
    "_source_type": "source_type",
    "_status": None,
}

# Authored: artmind seeds a value once (only if absent), then never touches it
# again — a human's edit to any of these must survive every future ingest.
AUTHORED_FIELDS = (
```

**Replace in** `artmind/document_identity.py` (lines 275-294):

```python
def decide_version(body: str, existing_meta: dict) -> VersionDecision:
    """Compare the incoming body against the file's OWN previously-written
    `_content_sha256` — no registry or graph lookup needed, the file already
    carries its own baseline.

    `metadata_only` covers BOTH "only frontmatter differs" and "nothing
    differs" from the spec's versioning table: writing the (possibly
    byte-identical) frontmatter back and letting git's own diff decide
    whether anything actually changed is simpler and exactly as correct as
    tracking a separate "did frontmatter change" signal would be, since
    artmind's own writes are idempotent (see `build_frontmatter`).
    """
    content_sha256 = compute_content_sha256(body)
    prior_sha = existing_meta.get("_content_sha256")
    prior_version = existing_meta.get("_version")

    if prior_sha is None or content_sha256 != prior_sha:
        new_version = int(prior_version) + 1 if (prior_sha is not None and prior_version) else 1
        return VersionDecision("content", new_version, content_sha256)
    return VersionDecision("metadata_only", int(prior_version) if prior_version else 1, content_sha256)
```

**with:**

```python
def ingest_baseline(staged: dict | None, frontmatter: dict) -> dict:
    """What the last successful ingest left behind -- `{"_content_sha256",
    "_version", "_source_sha256"}` -- for `decide_version` (and a binary's
    no-op check) to compare against.

    The staging folder's `document.json` (`staged`) is the baseline once it
    records a `content_sha256` (spec R6): it is written by the extraction
    itself, so it can never claim a version whose extraction failed. A
    `document.json` from before R6 has no hash; then the note's own legacy
    frontmatter is the baseline, with `document.json`'s `version` standing in
    when the note has none (a note `vault migrate-frontmatter` stripped
    whose extraction had not caught up)."""
    staged = staged or {}
    if staged.get("content_sha256"):
        return {
            "_content_sha256": staged["content_sha256"],
            "_version": staged.get("version"),
            "_source_sha256": staged.get("source_sha256"),
        }
    return {
        "_content_sha256": frontmatter.get("_content_sha256"),
        "_version": frontmatter.get("_version") or staged.get("version"),
        "_source_sha256": frontmatter.get("_source_sha256"),
    }


def needs_stamp(existing_meta: dict, artmind_id: str, domain: str) -> bool:
    """Whether the note must be (re)written: its identity fields are missing
    or differ -- the first stamp, a heal or fork, or a genuine `_domain`
    change. Nothing else ever rewrites a human note (spec R6)."""
    return existing_meta.get("_artmind_id") != artmind_id or existing_meta.get("_domain") != domain


def decide_version(body: str, existing_meta: dict) -> VersionDecision:
    """Compare the incoming body against the baseline the last ingest left
    -- `existing_meta` is `ingest_baseline(document.json, frontmatter)`, in
    frontmatter vocabulary (`_content_sha256`, `_version`). No registry or
    graph lookup: the vault carries its own baseline.

    `metadata_only` covers BOTH "only frontmatter differs" and "nothing
    differs" from the spec's versioning table; neither rewrites the note.
    A baseline with a version but no hash (a staged extraction from before
    R6, whose note no longer says) is a content change continuing that
    version, never a restart at 1.
    """
    content_sha256 = compute_content_sha256(body)
    prior_sha = existing_meta.get("_content_sha256")
    prior_version = existing_meta.get("_version")

    if prior_sha is None or content_sha256 != prior_sha:
        new_version = int(prior_version) + 1 if prior_version else 1
        return VersionDecision("content", new_version, content_sha256)
    return VersionDecision("metadata_only", int(prior_version) if prior_version else 1, content_sha256)
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_document_identity.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2581 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/document_identity.py test/test_document_identity.py
git commit -m "feat(identity): the versioning baseline is document.json, legacy frontmatter a fallback" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: notes carry identity only; the extraction stages the per-ingest fields in `document.json`

`_ingest_vault_native` stamps `_artmind_id`/`_domain` line by line only when `needs_stamp` says so (first ingest, heal, fork, a genuine domain change), then re-reads the note and versions it against `ingest_baseline(staged_document(...), frontmatter)`. The fields it used to write into the note travel in `file_result["provenance"]`, and `extract_kg` writes them into `document.json` — the extraction, not the ingest, records them. The binary path does the same (its no-op check reads `source_sha256` from the baseline, and its converted markdown carries identity only). The extract-kg retry decides the version against the same baseline. `build_frontmatter` and `frontmatter_unchanged` lose their last callers and go.

Tests that asserted "a second `ingest_file` right after the first is `metadata_only`" encoded the old design, where the note carried its own baseline before any extraction. They now stage `document.json` between the two ingests with `conftest.stage_as_extracted`, which writes what `extract_kg` writes; one retry test proves the real `extract_kg` does write it.

**Files:**
- Modify: `artmind/ingest.py`
- Modify: `artmind/document_identity.py`
- Test: `test/conftest.py`, `test/test_ingest_vault_native.py`, `test/test_ingest_binary_derived.py`, `test/test_ingest_extract_kg_retry.py`, `test/test_document_identity.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/conftest.py` (lines 305-306):

```python
    monkeypatch.setattr(ing, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", markdowns)
```

**with:**

```python
    monkeypatch.setattr(ing, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", markdowns)
    monkeypatch.setattr(ing, "KG_DIR", vault / ".artmind" / "data" / "kg")
```

**Replace in** `test/conftest.py` (line 316):

```python
def _fake_docling(body_by_call):
```

**with:**

```python
def stage_as_extracted(kg_dir, file_result, domain="general"):
    """Write the `document.json` `extract_kg` stages for `file_result` --
    identity, version and the ingest's `provenance` -- without running an
    extraction. Since spec 2026-09-26 R6 that file, not the note, is the
    baseline the next ingest versions against."""
    import json
    from pathlib import Path

    folder = Path(kg_dir) / domain / Path(file_result["registered_path"]).stem
    folder.mkdir(parents=True, exist_ok=True)
    document = {"id": file_result["artmind_id"], "version": file_result["version"], **file_result["provenance"]}
    (folder / "document.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    return folder


def _fake_docling(body_by_call):
```

**Replace in** `test/test_ingest_vault_native.py` (lines 9-13):

```python
import subprocess

import pytest

import artmind.ingest as ing
```

**with:**

```python
import json
import subprocess

import pytest

import artmind.ingest as ing
from conftest import stage_as_extracted
```

**Replace in** `test/test_ingest_vault_native.py` (lines 43-46):

```python
    # No real Neo4j: the metadata-only fast path's graph update is a no-op.
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)

    return v, doc
```

**with:**

```python
    # No real Neo4j: the metadata-only fast path's graph update is a no-op.
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)
    # Staging folders (document.json: the versioning baseline, spec R6).
    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")

    return v, doc
```

**Replace in** `test/test_ingest_vault_native.py` (lines 51-80):

```python
def test_first_ingest_is_new_and_writes_full_system_block(vault):
    v, doc = vault
    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert result["resolution_verdict"] == "new"
    assert result["version"] == 1
    assert result["tier"] == "content"

    meta, _ = ing._parse_md_frontmatter(doc.read_text())
    assert meta["_artmind_id"] == result["artmind_id"]
    assert meta["_domain"] == "general"
    assert meta["_status"] == "latest"


def test_reingest_truly_unchanged_does_not_rewrite_or_commit(vault):
    """Regression: decide_version's "metadata_only" tier only ever compares
    the BODY, so it can't by itself tell "nothing differs" from "only
    frontmatter differs" (docs/document-identity.md's own separate rows) --
    without frontmatter_unchanged() splitting these apart, _ingested_at/
    _source_commit refreshing unconditionally meant a truly unedited file
    still got rewritten (and committed) on every single sync, forever.

    tier stays "metadata_only" either way (decide_version's own vocabulary is
    unchanged) -- what changes is that a truly no-op touch no longer sets
    touched_path, so the note is not rewritten on disk at all (and Obsidian
    Git has nothing to commit)."""
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    before = doc.read_text()
```

**with:**

```python
def test_first_ingest_stamps_identity_only_and_keeps_the_body(vault):
    """Spec 2026-09-26 R6: a human note carries `_artmind_id` and `_domain`
    and nothing else artmind writes; every per-ingest field travels in
    `provenance`, which the extraction records in document.json."""
    v, doc = vault
    original = doc.read_bytes()
    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert result["resolution_verdict"] == "new"
    assert result["version"] == 1
    assert result["tier"] == "content"

    assert doc.read_bytes() == (
        f"---\n_artmind_id: {result['artmind_id']}\n_domain: general\n---\n".encode() + original
    )
    assert result["provenance"]["source_path"] == "notes/doc.md"
    assert result["provenance"]["source_type"] == "md"
    assert result["provenance"]["content_sha256"] == result["content_sha256"]
    assert set(result["provenance"]) == {"content_sha256", "source_commit", "ingested_at", "source_path", "source_type"}


def test_stamping_keeps_the_users_own_frontmatter_and_crlf(vault):
    v, doc = vault
    doc.write_bytes(b"---\r\ntags: [b, a]  # mine\r\ntitle: Mine\r\n---\r\n\r\n# Doc\r\nBody.\r\n")

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert doc.read_bytes() == (
        f"---\r\n_artmind_id: {result['artmind_id']}\r\n_domain: general\r\n".encode()
        + b"tags: [b, a]  # mine\r\ntitle: Mine\r\n---\r\n\r\n# Doc\r\nBody.\r\n"
    )


def test_a_domain_change_rewrites_only_the_domain_line(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    folder = stage_as_extracted(ing.KG_DIR, r1)
    staged = json.loads((folder / "document.json").read_text())
    (folder / "document.json").write_text(json.dumps({**staged, "version": 3}))
    before = doc.read_text()

    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000, set_domain="technical_paper")

    assert r2["tier"] == "content", "a new schema means a new extraction"
    assert r2["version"] == 3, "the body is unchanged: the OLD domain's staging folder is the baseline"
    assert doc.read_text() == before.replace("_domain: general", "_domain: technical_paper")


def test_reingest_truly_unchanged_does_not_rewrite_or_commit(vault):
    """Spec 2026-09-26 R6: every per-ingest field lives in the staging
    folder's document.json, so re-ingesting an unchanged note writes
    nothing to it -- no touched_path, identical bytes, nothing for Obsidian
    Git to commit (and nothing to conflict on across machines)."""
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)
    before = doc.read_text()
```

**Replace in** `test/test_ingest_vault_native.py` (lines 131-134):

```python
    v, doc = vault
    ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    from artmind.document_identity import render_document
```

**with:**

```python
    v, doc = vault
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000))

    from artmind.document_identity import render_document
```

**Replace in** `test/test_ingest_vault_native.py` (lines 153-168):

```python
def test_reingest_edited_body_bumps_version(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    doc.write_text(doc.read_text() + "\nA new paragraph.\n")
    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["resolution_verdict"] == "reingest"
    assert r2["tier"] == "content"
    assert r2["version"] == r1["version"] + 1
    assert r2["artmind_id"] == r1["artmind_id"]


def test_git_mv_is_recognised_as_a_silent_move(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
```

**with:**

```python
def test_reingest_edited_body_bumps_version(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)
    stamped = doc.read_text()

    doc.write_text(doc.read_text() + "\nA new paragraph.\n")
    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["resolution_verdict"] == "reingest"
    assert r2["tier"] == "content"
    assert r2["version"] == r1["version"] + 1
    assert r2["artmind_id"] == r1["artmind_id"]
    assert "touched_path" not in r2, "a content change does not rewrite the note"
    assert doc.read_text() == stamped + "\nA new paragraph.\n"


def test_a_failed_extraction_is_retried_at_the_same_version(vault):
    """The version lives in document.json, written by the extraction: an
    ingest whose extraction never staged anything leaves the baseline where
    it was, so the next ingest is a content change again at the same number
    -- never a metadata_only that skips the extraction the note still needs
    (the fat-frontmatter design recorded the new hash before extracting)."""
    v, doc = vault
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000))
    doc.write_text(doc.read_text() + "\nEdited.\n")

    failed = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)  # extraction then fails
    retried = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert (failed["tier"], failed["version"]) == ("content", 2)
    assert (retried["tier"], retried["version"]) == ("content", 2)


def test_a_legacy_fat_frontmatter_note_still_versions_from_its_own_fields(vault):
    """Backward compatibility: a note stamped by pre-R6 artmind, whose staging
    folder predates `content_sha256` in document.json, versions from its own
    `_content_sha256`/`_version` -- and is not rewritten."""
    from artmind.document_identity import compute_content_sha256

    v, doc = vault
    body = "# Doc\n\nOriginal body.\n"
    doc.write_text(
        "---\n_artmind_id: 0192legacy\n_version: 4\n_content_sha256: "
        + compute_content_sha256(body)
        + "\n_domain: general\n_status: latest\n_ingested_at: '2026-01-01T00:00:00+00:00'\n---\n\n"
        + body
    )
    folder = ing.KG_DIR / "general" / "doc"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "0192legacy", "version": 4}))
    before = doc.read_bytes()

    unchanged = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert (unchanged["tier"], unchanged["version"], unchanged["artmind_id"]) == ("metadata_only", 4, "0192legacy")
    assert doc.read_bytes() == before

    doc.write_text(doc.read_text() + "More.\n")
    edited = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert (edited["tier"], edited["version"]) == ("content", 5)


def test_git_mv_is_recognised_as_a_silent_move(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)  # kg/general/doc/ -- under the OLD name
```

**Replace in** `test/test_ingest_vault_native.py` (lines 226-229):

```python
    r2 = ing.ingest_file(new_path, "gemma4:e4b", "general", chunk_size=6000)
    assert r2["resolution_verdict"] == "move"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"]
```

**with:**

```python
    r2 = ing.ingest_file(new_path, "gemma4:e4b", "general", chunk_size=6000)
    assert r2["resolution_verdict"] == "move"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"]
    assert r2["tier"] == "metadata_only", "the old name's staging folder is still the baseline"
    assert "touched_path" not in r2, "a move never rewrites the note"
```

**Replace in** `test/test_ingest_binary_derived.py` (lines 8-16):

```python
Covers the one decision left after promotion was deleted -- no_op vs
convert, driven by comparing the incoming binary's hash against
`_source_sha256` in the registered markdown's own frontmatter -- for both a
source copied in from outside the vault and a vault-resident source.
"""
import subprocess

import artmind.ingest as ing
from conftest import _fake_docling
```

**with:**

```python
Covers the one decision left after promotion was deleted -- no_op vs
convert, driven by comparing the incoming binary's hash against the
`source_sha256` its last extraction staged in document.json (spec 2026-09-26
R6; the converted markdown's own `_source_sha256` frontmatter is the legacy
fallback) -- for both a source copied in from outside the vault and a
vault-resident source.
"""
import json
import subprocess

import artmind.ingest as ing
from conftest import _fake_docling, stage_as_extracted
```

**Replace in** `test/test_ingest_binary_derived.py` (lines 33-37):

```python
    meta, body = ing._parse_md_frontmatter(derived_path.read_text())
    assert meta["_artmind_id"] == result["artmind_id"]
    assert meta["_source_type"] == "pptx"
    assert "_source_sha256" in meta
    assert body == "# Deck\n\nBody v1.\n"
```

**with:**

```python
    meta, body = ing._parse_md_frontmatter(derived_path.read_text())
    assert meta == {"_artmind_id": result["artmind_id"], "_domain": "general"}
    assert result["provenance"]["source_type"] == "pptx"
    assert result["provenance"]["source_sha256"] == ing._compute_sha256(source)
    assert body == "# Deck\n\nBody v1.\n"
```

**Replace in** `test/test_ingest_binary_derived.py` (lines 50-52):

```python
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    # Re-ingest the identical binary bytes -- docling must not even be called.
```

**with:**

```python
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)

    # Re-ingest the identical binary bytes -- docling must not even be called.
```

**Replace in** `test/test_ingest_binary_derived.py` (lines 70-72):

```python
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    source.write_bytes(b"fake binary v2 -- different bytes")
```

**with:**

```python
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)

    source.write_bytes(b"fake binary v2 -- different bytes")
```

**Replace in** `test/test_ingest_binary_derived.py` (lines 111-112):

```python
    r1 = ing.ingest_file(resident, "gemma4:e4b", "general", chunk_size=6000)
    assert r1["status"] == "ok"
```

**with:**

```python
    r1 = ing.ingest_file(resident, "gemma4:e4b", "general", chunk_size=6000)
    assert r1["status"] == "ok"
    stage_as_extracted(ing.KG_DIR, r1)
```

**Append to** `test/test_ingest_binary_derived.py`:

```python


def test_an_unextracted_conversion_is_not_a_no_op(ingest_env, monkeypatch):
    """The source hash is recorded by the extraction (document.json), not at
    conversion: a conversion whose extraction never ran converts again
    rather than no-op forever on a markdown nothing was extracted from."""
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"] * 2))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)  # extraction then fails
    again = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert again.get("tier") != "no_op"
    assert "chunks_dir" in again, "converted and chunked again, ready to extract"
    assert again["version"] == 1


def test_a_legacy_conversion_no_ops_from_its_own_frontmatter(ingest_env, monkeypatch):
    """Backward compatibility: a conversion stamped by pre-R6 artmind (fat
    frontmatter, a document.json without `source_sha256`) still no-ops."""
    vault, source = ingest_env
    derived = ing.MARKDOWNS_DIR / "deck.md"
    derived.write_text(
        "---\n_artmind_id: 0192legacy\n_version: 3\n_domain: general\n_source_sha256: "
        + ing._compute_sha256(source) + "\n---\n\n# Deck\n"
    )
    folder = ing.KG_DIR / "general" / "deck"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "0192legacy", "version": 3}))
    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a no_op")),
    )

    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert (result["tier"], result["version"], result["artmind_id"]) == ("no_op", 3, "0192legacy")
```

**Replace in** `test/test_ingest_extract_kg_retry.py` (lines 16-19):

```python
import subprocess

import artmind.db as db
import artmind.ingest as ing
```

**with:**

```python
import json
import subprocess

import artmind.db as db
import artmind.ingest as ing
from conftest import stage_as_extracted
```

**Replace in** `test/test_ingest_extract_kg_retry.py` (lines 46-47):

```python
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)
```

**with:**

```python
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)
    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
```

**Replace in** `test/test_ingest_extract_kg_retry.py` (lines 74-82):

```python
def test_build_file_result_from_db_reflects_a_later_version_bump(tmp_path, monkeypatch):
    _v, doc, first = _ingest_vault_doc(tmp_path, monkeypatch)

    doc.write_text(doc.read_text() + "\nA new paragraph.\n")
    second = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert second["version"] == first["version"] + 1

    rebuilt = ing._build_file_result_from_db("retry_doc.md", "general")
    assert rebuilt["version"] == second["version"] == 2
```

**with:**

```python
def test_build_file_result_from_db_reflects_a_later_version_bump(tmp_path, monkeypatch):
    """The retry lands on the version the failed run chose: the extraction
    that failed staged nothing, so the baseline is still version 1's."""
    _v, doc, first = _ingest_vault_doc(tmp_path, monkeypatch)
    stage_as_extracted(ing.KG_DIR, first)

    doc.write_text(doc.read_text() + "\nA new paragraph.\n")
    second = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert second["version"] == first["version"] + 1

    rebuilt = ing._build_file_result_from_db("retry_doc.md", "general")
    assert rebuilt["version"] == second["version"] == 2
    assert rebuilt["provenance"]["content_sha256"] == second["content_sha256"]
    assert rebuilt["provenance"]["source_path"] == "notes/retry_doc.md"
```

**Replace in** `test/test_ingest_extract_kg_retry.py` (lines 116-121):

```python
    file_result = ing._build_file_result_from_db("retry_doc.md", "general")
    assert file_result is not None

    doc_kg_dir = ing.extract_kg(file_result, "general", max_workers=1)

    assert doc_kg_dir is not None
```

**with:**

```python
    file_result = ing._build_file_result_from_db("retry_doc.md", "general")
    assert file_result is not None

    doc_kg_dir = ing.extract_kg(file_result, "general", max_workers=1)

    assert doc_kg_dir is not None
    # Spec 2026-09-26 R6: the extraction records the ingest's provenance in
    # document.json -- the next ingest's baseline -- and seeds the title the
    # note no longer carries.
    document = json.loads((doc_kg_dir / "document.json").read_text())
    assert document["id"] == file_result["artmind_id"]
    assert document["content_sha256"] == file_result["provenance"]["content_sha256"]
    assert {"source_commit", "ingested_at", "source_path", "source_type"} <= set(document)
    assert document["title"] == "retry_doc"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_vault_native.py test/test_ingest_binary_derived.py test/test_ingest_extract_kg_retry.py -q`
Expected: `16 failed, 13 passed` — `KeyError: 'provenance'`, the notes still get the fat block, and second ingests without a staged `document.json` still read as `metadata_only`.

- [ ] **Step 3: Implement — `artmind/ingest.py`**

**Replace in** `artmind/ingest.py` (lines 17-27):

```python
from artmind.document_identity import (
    build_frontmatter,
    canonical_path,
    decide_version,
    frontmatter_unchanged,
    markdown_path_for,
    mint_artmind_id,
    resolve_canonical_path,
    resolve_identity,
    write_document,
)
```

**with:**

```python
from artmind.document_identity import (
    canonical_path,
    decide_version,
    ingest_baseline,
    markdown_path_for,
    mint_artmind_id,
    needs_stamp,
    resolve_canonical_path,
    resolve_identity,
    write_document,
)
```

The retry path re-decides the version against the baseline, and two helpers follow it — `staged_document` (the baseline lookup, decision 10) and `_stamp_note` (the one note write):

**Replace in** `artmind/ingest.py` (lines 257-274):

```python
        # extract_kg (ingest.py) branches on "artmind_id" in file_result to
        # treat this as a vault-native markdown doc and reads file_result["version"]
        # unconditionally -- unlike _ingest_vault_native, this retry path never ran
        # decide_version(), so the registry alone can't supply it (the `documents`
        # table has no version column, docs/redesign-phase-plan.md "E"). Read it
        # back from the frontmatter that write_document() persisted, falling back
        # to 1 the same way the binary no_op path does if it's missing or unreadable.
        version = 1
        try:
            meta, _ = _parse_md_frontmatter(registered_path.read_text(encoding="utf-8"))
            version = int(meta.get("_version") or 1)
        except Exception as e:
            logger.warning(
                "Could not read _version from frontmatter for {}: {} (defaulting to 1)",
                registered_path, e,
            )
        result["version"] = version
    return result
```

**with:**

```python
        # extract_kg (ingest.py) branches on "artmind_id" in file_result to
        # treat this as a vault-native markdown doc and reads file_result["version"]
        # unconditionally -- unlike _ingest_vault_native, this retry path never ran
        # decide_version(), so the registry alone can't supply it (the `documents`
        # table has no version column, docs/redesign-phase-plan.md "E"). Decide it
        # again, against the same baseline ingest used (the staging folder's
        # document.json, legacy frontmatter as a fallback -- spec 2026-09-26 R6):
        # a failed extraction recorded nothing, so the retry lands on the version
        # the failed run chose. Falls back to 1 the same way the binary no_op path
        # does if the markdown is missing or unreadable.
        version = 1
        try:
            meta, body = _parse_md_frontmatter(registered_path.read_text(encoding="utf-8"))
            staged = staged_document(
                meta.get("_domain") or domain, [registered_path.stem], artmind_id, search=True,
            ) or {}
            decision = decide_version(body, ingest_baseline(staged, meta))
            version = decision.version
            provenance = {k: staged[k] for k in ("source_path", "source_type", "source_sha256") if staged.get(k)}
            if "source_path" not in provenance and _is_vault_native_markdown(registered_path):
                provenance.update(source_path=canonical_path(registered_path), source_type="md")
            provenance.update(
                content_sha256=decision.content_sha256,
                source_commit=_vault_current_commit(),
                ingested_at=datetime.now(_datetime.timezone.utc).isoformat(),
            )
            result["provenance"] = provenance
        except Exception as e:
            logger.warning(
                "Could not decide the version of {}: {} (defaulting to 1)",
                registered_path, e,
            )
        result["version"] = version
    return result


def staged_document(
    domain: str | None, stems: list[str], artmind_id: str | None, *, search: bool = False
) -> dict | None:
    """`artmind_id`'s staging `document.json` in `domain`, or None -- the
    versioning baseline since spec 2026-09-26 R6 (what the last successful
    extraction recorded; `document_identity.ingest_baseline`).

    Looked up at `kg/<domain>/<stem>/` for each of `stems` (where extraction
    wrote it: the note's name now, and before a move), then, with `search`,
    across every folder in the domain -- for a note whose registry row is
    gone (`adopt`, `heal`) and whose folder may sit under an older name. A
    folder whose `document.json` names another id is never taken."""
    if not domain or not artmind_id:
        return None
    domain_dir = KG_DIR / domain
    candidates = [domain_dir / stem / "document.json" for stem in dict.fromkeys(stems)]
    if search and domain_dir.is_dir():
        candidates += [
            p for p in sorted(domain_dir.glob("*/document.json"))
            if p not in candidates and not p.parent.name.endswith((".artmind-tmp", ".artmind-old"))
        ]
    for path in candidates:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and document.get("id") == artmind_id:
            return document
    return None


def _stamp_note(source: Path, artmind_id: str, domain: str) -> None:
    """Write the note's identity (`_artmind_id`, `_domain`) into its
    frontmatter, changing no other byte (`artmind.frontmatter`) -- the only
    write artmind makes to a human note (spec 2026-09-26 R6). The temp file
    goes to the vault's `.artmind/data/`, which `.gitignore` covers."""
    from artmind import frontmatter

    raw = frontmatter.read_note(source)
    new = frontmatter.set_fields(raw, {"_artmind_id": artmind_id, "_domain": domain})
    if new == raw:
        return
    scratch = None
    if ARTMIND_VAULT_DIR is not None and (Path(ARTMIND_VAULT_DIR) / ".artmind").is_dir():
        from artmind.vault import VaultLayout

        scratch = VaultLayout(Path(ARTMIND_VAULT_DIR)).data_dir
    frontmatter.write_note(source, new, scratch_dir=scratch)
```

In `_ingest_vault_native`, the stamp, the baseline and the registry refresh:

**Replace in** `artmind/ingest.py` (lines 700-751):

```python
    if resolution.verdict == "heal":
        # Frontmatter lost its id; the registry supplies it back — not a
        # content change, so it does not by itself affect versioning below.
        existing_meta = {**existing_meta, "_artmind_id": resolution.artmind_id}

    version_decision = decide_version(body, existing_meta)
    tier = "content" if domain_changed else version_decision.tier

    ingested_at = datetime.now(_datetime.timezone.utc).isoformat()
    new_meta = build_frontmatter(
        existing_meta,
        artmind_id=resolution.artmind_id,
        version=version_decision.version,
        content_sha256=version_decision.content_sha256,
        domain=effective_domain,
        source_commit=_vault_current_commit(),
        source_path=canonical_path(source),
        source_type="md",
        ingested_at=ingested_at,
        body=body,
    )

    # The versioning table's "nothing differs" row, as opposed to "only
    # frontmatter differs" (docs/document-identity.md, "Versioning") --
    # decide_version alone can't tell these apart, since it only ever
    # compares the BODY. `file_unchanged` catches the pure "nothing differs"
    # case: nothing else in the frontmatter differs either, ignoring the two
    # fields meant to refresh on every touch regardless. When it's True there
    # is genuinely nothing to WRITE -- skip the file rewrite (so git sees no
    # diff and commits nothing) and the registry refresh.
    #
    # `apply_metadata_only` below still runs unconditionally for every
    # metadata_only tier, `file_unchanged` or not -- it must NOT be gated on
    # the same check. A human hand-editing only `tags`/`project`/`area` is
    # invisible to `file_unchanged`: by the time this function reads the
    # file, the edit is already IN `existing_meta` (parsed fresh off disk),
    # and `build_frontmatter` only ever carries authored fields forward from
    # `existing_meta` -- it never independently recomputes them, so
    # `new_meta`'s authored fields are always identical to `existing_meta`'s
    # regardless of whether a human just changed them. Skipping the cheap,
    # idempotent graph push in that case would silently stop a genuine
    # frontmatter edit from ever reaching the graph until the next real
    # content change. The file-rewrite skip has no such risk: whether or not
    # this run rewrites the file, its bytes end up identical either way.
    file_unchanged = tier == "metadata_only" and frontmatter_unchanged(existing_meta, new_meta)

    if not file_unchanged:
        write_document(source, new_meta, body)
        _register_document(
            effective_domain, source, resolution.artmind_id,
            content_sha256=version_decision.content_sha256,
        )
```

**with:**

```python
    # The note is written only to stamp its identity -- first ingest, heal,
    # fork, or a genuine `_domain` change (spec 2026-09-26 R6). Every
    # per-ingest field lives in the staging folder's document.json, written
    # by the extraction, so re-ingesting an unchanged note writes nothing to
    # it and gives Obsidian Git nothing to commit or conflict on. Stamped
    # line by line (`artmind.frontmatter`): the body, the user's own keys,
    # their order and the line endings are kept. Stamping first, then
    # re-reading, means the body hashed below is the body as it now stands.
    stamped = needs_stamp(existing_meta, resolution.artmind_id, effective_domain)
    if stamped:
        from artmind.frontmatter import FrontmatterEditError

        try:
            _stamp_note(source, resolution.artmind_id, effective_domain)
        except FrontmatterEditError as e:
            file_result["error"] = f"{source}: cannot stamp identity into its frontmatter: {e}"
            logger.error(file_result["error"])
            return file_result
        existing_meta, body = _parse_md_frontmatter(source.read_text(encoding="utf-8"))

    # The baseline is what the last successful extraction staged
    # (document.json), with a pre-R6 note's own frontmatter as the fallback.
    # A domain change looks in the old domain's folder too.
    staged = None
    if resolution.verdict != "new":
        stems = [source.stem] + ([Path(resolution.prior_path).stem] if resolution.prior_path else [])
        for staged_domain in dict.fromkeys(d for d in (effective_domain, prior_domain) if d):
            staged = staged_document(
                staged_domain, stems, resolution.artmind_id,
                search=resolution.verdict in ("adopt", "heal"),
            )
            if staged is not None:
                break
    version_decision = decide_version(body, ingest_baseline(staged, existing_meta))
    tier = "content" if domain_changed else version_decision.tier

    ingested_at = datetime.now(_datetime.timezone.utc).isoformat()

    if stamped or tier == "content" or resolution.verdict != "reingest":
        _register_document(
            effective_domain, source, resolution.artmind_id,
            content_sha256=version_decision.content_sha256,
        )
```

**Replace in** `artmind/ingest.py` (lines 757-762):

```python
        "registered_path": str(source.resolve()),
        "resolution_verdict": resolution.verdict,
        "tier": tier,
    })
    if not file_unchanged:
        file_result["touched_path"] = source
```

**with:**

```python
        "registered_path": str(source.resolve()),
        "resolution_verdict": resolution.verdict,
        "tier": tier,
        # What extract_kg records in document.json (spec R6) -- the next
        # ingest's baseline, and this version's provenance.
        "provenance": {
            "content_sha256": version_decision.content_sha256,
            "source_commit": _vault_current_commit(),
            "ingested_at": ingested_at,
            "source_path": canonical_path(source),
            "source_type": "md",
        },
    })
    if stamped:
        file_result["touched_path"] = source
```

**Replace in** `artmind/ingest.py` (lines 780-794):

```python
                metadata={
                    k: new_meta.get(k)
                    for k in ("title", "project", "area", "tags", "created_on", "modified_on")
                    if new_meta.get(k)
                },
            )
        except Exception as e:
            logger.warning("metadata-only graph update skipped for {}: {}", source.name, e)
        file_result["chunk_count"] = 0
        logger.info(
            "── Ingest done in {:.1f}s: {} (metadata_only, v{}{})",
            time.monotonic() - t_file_start, source.name, version_decision.version,
            ", unchanged" if file_unchanged else "",
        )
        return file_result
```

**with:**

```python
                metadata={
                    k: existing_meta.get(k)
                    for k in ("title", "project", "area", "tags", "created_on", "modified_on")
                    if existing_meta.get(k)
                },
            )
        except Exception as e:
            logger.warning("metadata-only graph update skipped for {}: {}", source.name, e)
        file_result["chunk_count"] = 0
        logger.info(
            "── Ingest done in {:.1f}s: {} (metadata_only, v{}{})",
            time.monotonic() - t_file_start, source.name, version_decision.version,
            "" if stamped else ", note unchanged",
        )
        return file_result
```

In `_ingest_binary_derived` — its docstring, the baseline, the identity-only markdown, and the provenance:

**Replace in** `artmind/ingest.py` (lines 1017-1023):

```python
    writes. The only decision left is `no_op` vs `convert`: the incoming
    binary's hash (`_compute_sha256(source)`) is compared against
    `_source_sha256`, a fingerprint recorded in that markdown's own
    frontmatter the last time it was converted. Whether the resulting
    document body actually changed (and therefore whether `_version` bumps)
    is decided the usual way, by `decide_version` against the file's own
    `_content_sha256` — the same mechanism `_ingest_vault_native` uses.
```

**with:**

```python
    writes. The only decision left is `no_op` vs `convert`: the incoming
    binary's hash (`_compute_sha256(source)`) is compared against the
    `source_sha256` the last extraction recorded in the staging folder's
    `document.json` (spec 2026-09-26 R6; a pre-R6 conversion's own
    `_source_sha256` frontmatter is the fallback). Whether the resulting
    document body actually changed (and therefore whether the version bumps)
    is decided the usual way, by `decide_version` against the same baseline
    -- the same mechanism `_ingest_vault_native` uses.
```

**Replace in** `artmind/ingest.py` (lines 1070-1082):

```python
    registered_path = MARKDOWNS_DIR / f"{stem}.md"
    existing_meta: dict = {}
    if registered_path.exists():
        existing_meta, _ = _parse_md_frontmatter(
            registered_path.read_text(encoding="utf-8")
        )

    if existing_meta and existing_meta.get("_source_sha256") == source_sha256:
        file_result["status"] = "ok"
        file_result["domain"] = effective_domain
        file_result["artmind_id"] = existing_meta.get("_artmind_id")
        file_result["version"] = int(existing_meta.get("_version") or 1)
        file_result["registered_path"] = str(registered_path)
```

**with:**

```python
    registered_path = MARKDOWNS_DIR / f"{stem}.md"
    existing_meta: dict = {}
    if registered_path.exists():
        existing_meta, _ = _parse_md_frontmatter(
            registered_path.read_text(encoding="utf-8")
        )
    # The baseline is the staging folder's document.json (spec 2026-09-26 R6),
    # with a pre-R6 conversion's own frontmatter as the fallback: a binary
    # whose extraction failed is never mistaken for converted and extracted.
    staged = staged_document(effective_domain, [stem], existing_meta.get("_artmind_id"), search=True)
    baseline = ingest_baseline(staged, existing_meta)

    if existing_meta and baseline.get("_source_sha256") == source_sha256:
        file_result["status"] = "ok"
        file_result["domain"] = effective_domain
        file_result["artmind_id"] = existing_meta.get("_artmind_id")
        file_result["version"] = int(baseline.get("_version") or 1)
        file_result["registered_path"] = str(registered_path)
```

**Replace in** `artmind/ingest.py` (lines 1101-1118):

```python
    artmind_id = existing_meta.get("_artmind_id") or mint_artmind_id()
    version_decision = decide_version(body, existing_meta)

    MARKDOWNS_DIR.mkdir(parents=True, exist_ok=True)
    new_meta = build_frontmatter(
        existing_meta,
        artmind_id=artmind_id,
        version=version_decision.version,
        content_sha256=version_decision.content_sha256,
        domain=effective_domain,
        source_commit=_vault_current_commit(),
        source_path=orig_registry_path,
        source_type=source.suffix.lstrip(".").lower(),
        ingested_at=datetime.now(_datetime.timezone.utc).isoformat(),
        body=body,
    )
    new_meta["_source_sha256"] = source_sha256
    write_document(registered_path, new_meta, body)
```

**with:**

```python
    artmind_id = existing_meta.get("_artmind_id") or mint_artmind_id()
    version_decision = decide_version(body, baseline)

    MARKDOWNS_DIR.mkdir(parents=True, exist_ok=True)
    # Identity only, like a human note (spec R6): the per-conversion fields go
    # to document.json with the extraction (`provenance` below).
    write_document(registered_path, {"_artmind_id": artmind_id, "_domain": effective_domain}, body)
```

**Replace in** `artmind/ingest.py` (lines 1124-1127):

```python
    file_result["sha256"] = _compute_sha256(registered_path)
    file_result["registered_path"] = str(registered_path)
    file_result["chunks_dir"] = str(chunks_dir)
    file_result["chunk_count"] = len(chunks)
```

**with:**

```python
    file_result["sha256"] = _compute_sha256(registered_path)
    file_result["registered_path"] = str(registered_path)
    file_result["chunks_dir"] = str(chunks_dir)
    file_result["chunk_count"] = len(chunks)
    file_result["provenance"] = {
        "content_sha256": version_decision.content_sha256,
        "source_sha256": source_sha256,
        "source_commit": _vault_current_commit(),
        "ingested_at": datetime.now(_datetime.timezone.utc).isoformat(),
        "source_path": orig_registry_path,
        "source_type": source.suffix.lstrip(".").lower(),
    }
```

In `extract_kg`, the provenance goes into `document.json`, and the title the note no longer carries defaults to the stem:

**Replace in** `artmind/ingest.py` (lines 2441-2444):

```python
    if logical_id is not None:
        document["logical_id"] = logical_id
    else:
        document["artmind_id"] = doc_id
```

**with:**

```python
    if logical_id is not None:
        document["logical_id"] = logical_id
    else:
        document["artmind_id"] = doc_id
        # Per-ingest provenance lives here, not in the note (spec 2026-09-26
        # R6): `content_sha256` is the next ingest's versioning baseline and
        # `vault resolve`'s tie to the note's body. Written by the extraction
        # itself, so it never claims a version whose extraction failed.
        document.update(file_result.get("provenance") or {})
```

**Replace in** `artmind/ingest.py` (lines 2453-2454):

```python
    if meta.get("title"):
        document["title"] = str(meta["title"])
```

**with:**

```python
    if meta.get("title"):
        document["title"] = str(meta["title"])
    elif logical_id is None:
        # The title artmind used to seed into the note (spec R6: it no
        # longer writes anything but identity there).
        document["title"] = registered_path.stem
```

- [ ] **Step 4: Remove what lost its callers — `artmind/document_identity.py` and its tests**

**Replace in** `artmind/document_identity.py` (lines 236-275):

```python
# Fields expected to refresh on every touch regardless of whether anything a
# human or a re-ingest would care about actually changed -- both are pure
# provenance ("`_source_commit` records the vault's git sha at ingest --
# provenance, not identity", docs/document-identity.md), never meaningful to
# compare for a versioning decision.
_PROVENANCE_ONLY_FIELDS = frozenset({"_ingested_at", "_source_commit"})


def frontmatter_unchanged(existing_meta: dict, new_meta: dict) -> bool:
    """Whether `new_meta` carries no information beyond what `existing_meta`
    already had, ignoring `_PROVENANCE_ONLY_FIELDS`.

    This is what actually distinguishes "nothing differs" from "only
    frontmatter differs" -- the versioning table's own two separate rows
    (docs/document-identity.md, "Versioning") -- something `decide_version`
    alone cannot do, since it only ever compares the BODY. Without this
    check, `_ingested_at`/`_source_commit` refreshing unconditionally meant a
    genuinely no-op touch still produced different file bytes on every
    ingest, so `git diff` always found something to commit -- exactly the
    "letting git's own diff decide whether anything actually changed"
    design `decide_version`'s own docstring describes, but which the
    always-fresh timestamp silently defeated.

    **Answers "would rewriting the file change anything", not "is the graph
    already in sync".** A human hand-editing an authored field (tags/title/
    project/area) is invisible here: `existing_meta` is parsed fresh off the
    just-edited file, and callers only ever carry authored fields forward
    from `existing_meta` rather than recomputing them, so `new_meta`'s
    authored fields are always identical to `existing_meta`'s regardless of
    whether a human just changed them. Safe to gate a file-rewrite/commit on
    (rewriting produces the same bytes either way); NOT safe to gate a graph
    metadata push on -- that must still run whenever the tier calls for it,
    using the file's current values, or a genuine edit never reaches the
    graph until the next real content change.
    """
    keys = (set(existing_meta) | set(new_meta)) - _PROVENANCE_ONLY_FIELDS
    return all(existing_meta.get(k) == new_meta.get(k) for k in keys)


def ingest_baseline(staged: dict | None, frontmatter: dict) -> dict:
```

**with:**

```python
def ingest_baseline(staged: dict | None, frontmatter: dict) -> dict:
```

**Replace in** `artmind/document_identity.py` (lines 303-355):

```python
def build_frontmatter(
    existing_meta: dict,
    *,
    artmind_id: str,
    version: int,
    content_sha256: str,
    domain: str,
    status: str = "latest",
    valid_from: str | None = None,
    valid_to: str | None = None,
    valid_time_source: str | None = None,
    source_commit: str | None = None,
    source_path: str,
    source_type: str,
    ingested_at: str,
    body: str | None = None,
) -> dict:
    """Merge the system block onto `existing_meta`.

    Authored fields are seeded via `setdefault` — present once, in the file
    itself, they are never overwritten by any later call. `title` seeds from
    the source filename stem; `created_on` from the ingest timestamp;
    `declared_version` lifts from the body's own "Version" header when `body`
    is given and the document doesn't already declare one.
    """
    out = dict(existing_meta)
    out["_artmind_id"] = artmind_id
    out["_version"] = version
    out["_content_sha256"] = content_sha256
    out["_domain"] = domain
    out["_status"] = status
    if valid_from is not None:
        out["_valid_from"] = valid_from
    if valid_to is not None:
        out["_valid_to"] = valid_to
    if valid_time_source is not None:
        out["_valid_time_source"] = valid_time_source
    if source_commit is not None:
        out["_source_commit"] = source_commit
    out["_source_path"] = source_path
    out["_source_type"] = source_type
    out["_ingested_at"] = ingested_at

    out.setdefault("title", Path(source_path).stem)
    out.setdefault("created_on", ingested_at)
    if body is not None and "declared_version" not in out:
        lifted = lift_declared_version(body)
        if lifted:
            out["declared_version"] = lifted
    return out


def serialize_frontmatter(meta: dict) -> str:
```

**with:**

```python
def serialize_frontmatter(meta: dict) -> str:
```

**Replace in** `artmind/document_identity.py` (lines 73-75):

```python
# Authored: artmind seeds a value once (only if absent), then never touches it
# again — a human's edit to any of these must survive every future ingest.
AUTHORED_FIELDS = (
```

**with:**

```python
# Authored: a human's own fields. artmind reads them (the graph's Document
# carries them) but no longer seeds them into a note (spec R6); a value a
# human writes survives every ingest.
AUTHORED_FIELDS = (
```

**Replace in** `test/test_document_identity.py` (lines 15-21):

```python
    SYSTEM_FIELDS,
    build_frontmatter,
    canonical_path,
    compute_content_sha256,
    decide_version,
    frontmatter_unchanged,
    ingest_baseline,
```

**with:**

```python
    SYSTEM_FIELDS,
    canonical_path,
    compute_content_sha256,
    decide_version,
    ingest_baseline,
```

**Replace in** `test/test_document_identity.py` (lines 242-299):

```python
# ── frontmatter_unchanged: splitting "metadata_only" into the versioning
# table's real two rows ──────────────────────────────────────────────────────
# decide_version only ever compares the BODY, so its "metadata_only" tier
# collapses the table's "only frontmatter differs" and "nothing differs" rows
# into one. Regression: with nothing to separate them, `_ingested_at`/
# `_source_commit` refreshing unconditionally on every touch meant a
# genuinely no-op re-ingest still produced different file bytes every time,
# so git always found something to commit.


def test_frontmatter_unchanged_true_when_only_provenance_fields_differ():
    existing = {"_version": 2, "_ingested_at": "2026-01-01T00:00:00Z", "_source_commit": "aaa", "tags": ["x"]}
    new = {"_version": 2, "_ingested_at": "2026-02-02T00:00:00Z", "_source_commit": "bbb", "tags": ["x"]}
    assert frontmatter_unchanged(existing, new) is True


def test_frontmatter_unchanged_false_when_an_authored_field_differs():
    existing = {"_version": 2, "_ingested_at": "2026-01-01T00:00:00Z", "tags": ["x"]}
    new = {"_version": 2, "_ingested_at": "2026-02-02T00:00:00Z", "tags": ["x", "urgent"]}
    assert frontmatter_unchanged(existing, new) is False


def test_frontmatter_unchanged_false_when_version_differs():
    existing = {"_version": 2, "_ingested_at": "2026-01-01T00:00:00Z"}
    new = {"_version": 3, "_ingested_at": "2026-02-02T00:00:00Z"}
    assert frontmatter_unchanged(existing, new) is False


def test_frontmatter_unchanged_false_when_new_meta_adds_a_key():
    existing = {"_version": 2}
    new = {"_version": 2, "project": "Q4 planning"}
    assert frontmatter_unchanged(existing, new) is False


# ── frontmatter contract ─────────────────────────────────────────────────────


def test_build_frontmatter_seeds_title_and_created_on_once():
    meta = build_frontmatter(
        {}, artmind_id="id-1", version=1, content_sha256="sha", domain="general",
        source_path="notes/foo.md", source_type="md", ingested_at="2026-01-01T00:00:00Z",
    )
    assert meta["title"] == "foo"
    assert meta["created_on"] == "2026-01-01T00:00:00Z"


def test_build_frontmatter_never_overwrites_existing_authored_fields():
    existing = {"title": "My Custom Title", "created_on": "2020-01-01", "tags": "a,b"}
    meta = build_frontmatter(
        existing, artmind_id="id-1", version=2, content_sha256="sha", domain="general",
        source_path="notes/foo.md", source_type="md", ingested_at="2026-06-01T00:00:00Z",
    )
    assert meta["title"] == "My Custom Title"
    assert meta["created_on"] == "2020-01-01"
    assert meta["tags"] == "a,b"


def test_lift_declared_version_from_table_header():
```

**with:**

```python
# ── frontmatter contract ─────────────────────────────────────────────────────


def test_lift_declared_version_from_table_header():
```

**Replace in** `test/test_document_identity.py` (lines 261-297):

```python
def test_build_frontmatter_lifts_declared_version_from_body_once():
    body = "| Version | 3.0 |\n"
    meta = build_frontmatter(
        {}, artmind_id="id-1", version=1, content_sha256="sha", domain="general",
        source_path="notes/foo.md", source_type="md", ingested_at="2026-01-01T00:00:00Z",
        body=body,
    )
    assert meta["declared_version"] == "3.0"


def test_build_frontmatter_never_overwrites_existing_declared_version():
    existing = {"declared_version": "9.9"}
    meta = build_frontmatter(
        existing, artmind_id="id-1", version=2, content_sha256="sha", domain="general",
        source_path="notes/foo.md", source_type="md", ingested_at="2026-01-01T00:00:00Z",
        body="| Version | 3.0 |\n",
    )
    assert meta["declared_version"] == "9.9"


def test_build_frontmatter_sets_the_full_system_block():
    meta = build_frontmatter(
        {}, artmind_id="id-1", version=1, content_sha256="sha", domain="general",
        valid_from="2026-01-01", valid_to=None, valid_time_source="header",
        source_commit="abc123", source_path="notes/foo.md", source_type="md",
        ingested_at="2026-01-01T00:00:00Z",
    )
    assert meta["_artmind_id"] == "id-1"
    assert meta["_version"] == 1
    assert meta["_domain"] == "general"
    assert meta["_status"] == "latest"
    assert meta["_valid_from"] == "2026-01-01"
    assert "_valid_to" not in meta  # None is omitted, not written as null
    assert meta["_source_commit"] == "abc123"


def test_serialize_frontmatter_orders_system_then_authored_then_extra():
```

**with:**

```python
def test_serialize_frontmatter_orders_system_then_authored_then_extra():
```

- [ ] **Step 5: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_ingest_vault_native.py test/test_ingest_binary_derived.py test/test_ingest_extract_kg_retry.py test/test_document_identity.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2578 passed, 14 skipped`.

- [ ] **Step 6: Commit**

```bash
git add artmind/ingest.py artmind/document_identity.py test/conftest.py test/test_ingest_vault_native.py test/test_ingest_binary_derived.py test/test_ingest_extract_kg_retry.py test/test_document_identity.py
git commit -m "feat(ingest): notes carry identity only; the extraction stages per-ingest fields in document.json" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `docs reindex` and archive read the baseline from `document.json`

`docs reindex` takes each note's content hash from its staging `document.json` (stem lookup only — a reindex over thousands of notes must not scan a domain per note), the legacy frontmatter hash next, the body last. `archive_document` reads a converted binary's `source_type` from `document.json`, legacy frontmatter next. `restore_from_archive --newId` changes only the id line (`frontmatter.set_fields`) instead of re-rendering the whole frontmatter.

**Files:**
- Modify: `artmind/reindex.py`, `artmind/archive.py`
- Test: `test/test_reindex.py`, `test/test_archive.py`

- [ ] **Step 1: Write the failing tests**

**Append to** `test/test_reindex.py`:

```python


def test_reindex_reads_the_content_hash_from_document_json_for_a_minimal_note(vault, tmp_path, monkeypatch):
    """Spec 2026-09-26 R6: a note carries identity only; the hash the last
    extraction recorded is in its staging folder's document.json. A note
    with neither falls back to hashing its body."""
    import json

    import artmind.ingest as ing
    from artmind.document_identity import compute_content_sha256

    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
    (vault / "notes").mkdir()
    (vault / "notes" / "a.md").write_text("---\n_artmind_id: id-1\n_domain: general\n---\n\nBody A\n")
    (vault / "notes" / "b.md").write_text("---\n_artmind_id: id-2\n_domain: general\n---\n\nBody B\n")
    folder = tmp_path / "kg" / "general" / "a"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "id-1", "version": 2, "content_sha256": "staged-hash"}))

    reindex_mod.reindex()

    conn = sqlite3.connect(vault.parent / "registry.db")
    try:
        rows = dict(conn.execute("SELECT artmind_id, content_sha256 FROM documents"))
    finally:
        conn.close()
    assert rows == {"id-1": "staged-hash", "id-2": compute_content_sha256("Body B\n")}
```

**Replace in** `test/test_archive.py` (line 203):

```python
def test_archive_document_raises_when_not_found(env, monkeypatch):
```

**with:**

```python
def test_archive_document_reads_the_source_type_from_document_json(env, monkeypatch):
    """Spec 2026-09-26 R6: a converted binary's markdown carries identity
    only; its source type is in the staging folder's document.json."""
    vault, archive_root, kg_dir, originals = env

    doc = vault / "_derived" / "banking" / "deck.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\n_artmind_id: doc-2\n_domain: banking\n---\n\nBody.\n", encoding="utf-8")
    staged = kg_dir / "banking" / "deck"
    staged.mkdir(parents=True)
    (staged / "document.json").write_text(json.dumps({"id": "doc-2", "source_type": "pptx"}), encoding="utf-8")
    (originals / "deck.pptx").write_bytes(b"fake pptx bytes")

    monkeypatch.setattr(archive, "resolve_document_id", lambda name, domain: "doc-2")
    monkeypatch.setattr(
        archive, "_document_info",
        lambda doc_id: {"path": "_derived/banking/deck.md", "version": 1, "name": "deck.md"},
    )
    monkeypatch.setattr(archive, "_observation_valid_time_span", lambda doc_id: (None, None))
    _mock_session(monkeypatch, _Tx())
    monkeypatch.setattr("artmind.projection.keys_for_document", lambda tx, doc_id: set())
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {})

    result = archive.archive_document("banking", "deck")

    assert result["manifest"]["source_type"] == "pptx"
    assert (archive_root / "doc-2" / "original.pptx").read_bytes() == b"fake pptx bytes"


def test_restore_under_a_new_id_rewrites_only_the_id_line(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    (bundle_dir / "document.md").write_bytes(
        b"---\r\ntitle: Policy\r\n_artmind_id: doc-1\r\n_domain: general\r\n---\r\n\r\nBody.\r\n"
    )
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})

    archive.restore_from_archive("doc-1", to_path="notes/policy-copy.md", new_id="doc-9")

    assert (vault / "notes" / "policy-copy.md").read_bytes() == (
        b"---\r\ntitle: Policy\r\n_artmind_id: doc-9\r\n_domain: general\r\n---\r\n\r\nBody.\r\n"
    )


def test_archive_document_raises_when_not_found(env, monkeypatch):
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reindex.py test/test_archive.py -q`
Expected: `3 failed, 16 passed` — reindex stores the body hash instead of `staged-hash`, archive reports `source_type` `md` instead of `pptx`, and the new-id restore re-renders the whole frontmatter (LF, reordered).

- [ ] **Step 3: Implement**

**Replace in** `artmind/reindex.py` (lines 1-4):

```python
"""Rebuild the registry (`document_registry.db`'s path <-> id cache) from
vault frontmatter (docs/document-identity.md; docs/redesign-phase-plan.md,
Phase 5 "D"). The registry is never authoritative — `docs/document-
identity.md` says so directly — so wiping and rebuilding it is always safe.
```

**with:**

```python
"""Rebuild the registry (`document_registry.db`'s path <-> id cache) from
vault frontmatter (docs/document-identity.md; docs/redesign-phase-plan.md,
Phase 5 "D"). The registry is never authoritative — `docs/document-
identity.md` says so directly — so wiping and rebuilding it is always safe.

Identity (`_artmind_id`, `_domain`) comes from each note's frontmatter; the
content hash from the staging folder's `document.json` (spec 2026-09-26 R6),
a pre-R6 note's own `_content_sha256` as the fallback, else the body itself.
```

**Replace in** `artmind/reindex.py` (line 58):

```python
    from artmind.ingest import _parse_md_frontmatter  # local: avoid a cycle, ingest imports this module
```

**with:**

```python
    from artmind.document_identity import ingest_baseline
    from artmind.ingest import _parse_md_frontmatter, staged_document  # local: avoid a cycle
```

**Replace in** `artmind/reindex.py` (line 79):

```python
            content_sha256 = meta.get("_content_sha256") or compute_content_sha256(body)
```

**with:**

```python
            staged = staged_document(meta.get("_domain"), [path.stem], artmind_id)
            content_sha256 = ingest_baseline(staged, meta)["_content_sha256"] or compute_content_sha256(body)
```

**Replace in** `artmind/archive.py` (lines 180-181):

```python
    stem = vault_path.stem if vault_path is not None else Path(str(info.get("name") or doc_id)).stem
    source_type = meta.get("_source_type", "md")
```

**with:**

```python
    stem = vault_path.stem if vault_path is not None else Path(str(info.get("name") or doc_id)).stem
    kg_dir = KG_DIR / domain / stem
    # The source type is per-ingest provenance: the staging folder's
    # document.json carries it (spec 2026-09-26 R6), a pre-R6 note's own
    # frontmatter is the fallback.
    staged: dict = {}
    try:
        loaded = json.loads((kg_dir / "document.json").read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and loaded.get("id") == doc_id:
            staged = loaded
    except (OSError, ValueError):
        pass
    source_type = staged.get("source_type") or meta.get("_source_type", "md")
```

**Replace in** `artmind/archive.py` (lines 212-214):

```python
    kg_dir = KG_DIR / domain / stem
    if kg_dir.exists():
        shutil.copytree(kg_dir, bundle_dir / "kg" / domain / stem)
```

**with:**

```python
    if kg_dir.exists():
        shutil.copytree(kg_dir, bundle_dir / "kg" / domain / stem)
```

**Replace in** `artmind/archive.py` (lines 316-322):

```python
    if new_id and target_path.exists():
        from artmind.ingest import _parse_md_frontmatter
        from artmind.document_identity import render_document

        meta, body = _parse_md_frontmatter(target_path.read_text(encoding="utf-8"))
        meta["_artmind_id"] = new_id
        target_path.write_text(render_document(meta, body), encoding="utf-8")
```

**with:**

```python
    if new_id and target_path.exists():
        from artmind import frontmatter

        # Only the id line changes (spec 2026-09-26 R6): the note's other
        # keys, their order, its body and line endings are the user's.
        frontmatter.write_note(
            target_path, frontmatter.set_fields(frontmatter.read_note(target_path), {"_artmind_id": new_id})
        )
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_reindex.py test/test_archive.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2581 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/reindex.py artmind/archive.py test/test_reindex.py test/test_archive.py
git commit -m "feat(identity): docs reindex and archive read per-ingest fields from document.json" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `artmind vault migrate-frontmatter`

The one bulk note rewrite (spec §12 item 5). For each vault note and each converted binary's markdown that still carries fat frontmatter: find its staging folder (by stem, else by id across the domain — read once per domain), copy each moved field into `document.json` where the key is absent (the four ingest-bound ones only when the note's `_version` equals the staged `version`), then strip the fields from the note with `frontmatter.remove_fields`. `document.json` is written first, so an interrupted run resumes: the rerun finds its keys present and only strips the note. It refuses during a merge/rebase and while the worker runs — `vault sync`'s worker check moves into `vault_sync.worker_running` so both use it — and the CLI prints the "pause Obsidian Git" instruction on stderr before anything else.

**Files:**
- Modify: `artmind/vault_sync.py` (`worker_running`)
- Create: `artmind/frontmatter_migration.py`
- Modify: `artmind/cli.py` (`vault migrate-frontmatter`, the `vault` group docstring)
- Test: `test/test_frontmatter_migration.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_frontmatter_migration.py`:

```python
"""`artmind vault migrate-frontmatter` (spec 2026-09-26 §5 R6, §12 item 5):
per-ingest fields move from each note into its staging document.json, and
each note is rewritten once, without them -- body, the user's keys, their
order and line endings byte-for-byte. Real throwaway git repos; no graph."""
import json
import subprocess

import pytest
from click.testing import CliRunner

from artmind import frontmatter_migration as fmm
from artmind.document_identity import compute_content_sha256

BODY = "\r\n# Policy\r\n\r\nBody text.\r\n"
SHA = compute_content_sha256("# Policy\n\nBody text.\n")


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def _fat(version=2, sha=SHA):
    """A note as pre-R6 artmind left it (CRLF; the user's own keys around
    artmind's)."""
    return (
        "---\r\n"
        "_artmind_id: id-1\r\n"
        f"_version: {version}\r\n"
        f"_content_sha256: {sha}\r\n"
        "_domain: general\r\n"
        "_status: latest\r\n"
        "_source_commit: " + "c" * 40 + "\r\n"
        "_source_path: notes/policy.md\r\n"
        "_source_type: md\r\n"
        "_ingested_at: '2026-09-01T10:00:00+00:00'\r\n"
        "title: Policy\r\n"
        "tags: [b, a]  # mine\r\n"
        "---\r\n" + BODY
    )


MINIMAL = "---\r\n_artmind_id: id-1\r\n_domain: general\r\ntitle: Policy\r\ntags: [b, a]  # mine\r\n---\r\n" + BODY


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    import paths

    root = tmp_path / "v"
    (root / ".artmind" / "data").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    monkeypatch.setattr(paths, "KG_DIR", root / ".artmind" / "data" / "kg")
    monkeypatch.setattr(paths, "MARKDOWNS_DIR", root / ".artmind" / "data" / "documents" / "markdowns")
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    (root / "notes").mkdir()
    note = root / "notes" / "policy.md"
    note.write_bytes(_fat().encode())
    return root, note


def _stage(root, document, domain="general", stem="policy"):
    folder = root / ".artmind" / "data" / "kg" / domain / stem
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "document.json").write_text(json.dumps(document, indent=2, ensure_ascii=False))
    (folder / "observations.json").write_text("[]")
    return folder


def test_a_fat_note_moves_its_fields_into_document_json_and_is_stripped_once(vault):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2, "name": "policy.md"})

    report = fmm.migrate(root)

    assert note.read_bytes() == MINIMAL.encode()
    assert json.loads((folder / "document.json").read_text()) == {
        "id": "id-1", "version": 2, "name": "policy.md",
        "content_sha256": SHA, "source_commit": "c" * 40, "ingested_at": "2026-09-01T10:00:00+00:00",
        "source_path": "notes/policy.md", "source_type": "md",
    }
    assert (folder / "observations.json").read_text() == "[]", "the rest of the folder is carried over"
    assert report["migrated"] == [{
        "path": "notes/policy.md",
        "removed": ["_version", "_content_sha256", "_source_commit", "_ingested_at", "_source_path", "_source_type", "_status"],
        "document_json": {
            "folder": ".artmind/data/kg/general/policy",
            "added": ["content_sha256", "ingested_at", "source_commit", "source_path", "source_type"],
        },
    }]
    assert list((root / ".artmind" / "data").glob("*.artmind-tmp")) == []

    before = (note.read_bytes(), (folder / "document.json").read_bytes())
    again = fmm.migrate(root)

    assert (again["migrated"], again["already_minimal"]) == ([], 1)
    assert (note.read_bytes(), (folder / "document.json").read_bytes()) == before


def test_a_dry_run_reports_and_writes_nothing(vault):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2})
    doc_before = (folder / "document.json").read_bytes()

    report = fmm.migrate(root, dry_run=True)

    assert report["dry_run"] is True
    assert [m["path"] for m in report["migrated"]] == ["notes/policy.md"]
    assert note.read_bytes() == _fat().encode()
    assert (folder / "document.json").read_bytes() == doc_before


def test_a_value_a_newer_ingest_recorded_is_never_overwritten(vault):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 3, "content_sha256": "newer", "ingested_at": "2026-09-20"})

    report = fmm.migrate(root)

    staged = json.loads((folder / "document.json").read_text())
    assert (staged["version"], staged["content_sha256"], staged["ingested_at"]) == (3, "newer", "2026-09-20")
    assert note.read_bytes() == MINIMAL.encode()
    assert report["version_mismatch"] == ["notes/policy.md"]


def test_fields_of_an_extraction_that_never_landed_are_not_moved(vault):
    """The note says v3 but the staged extraction is v2 (v3's extraction
    failed): v3's hash must not be written beside v2's observations, or the
    next ingest would skip the extraction the note still needs."""
    root, note = vault
    note.write_bytes(_fat(version=3).encode())
    folder = _stage(root, {"id": "id-1", "version": 2})

    report = fmm.migrate(root)

    staged = json.loads((folder / "document.json").read_text())
    assert "content_sha256" not in staged and "ingested_at" not in staged
    assert (staged["source_path"], staged["source_type"]) == ("notes/policy.md", "md")
    assert report["version_mismatch"] == ["notes/policy.md"]
    assert note.read_bytes() == MINIMAL.encode()


def test_a_note_never_extracted_keeps_its_fields_and_is_reported(vault):
    root, note = vault

    report = fmm.migrate(root)

    assert note.read_bytes() == _fat().encode()
    assert [s["path"] for s in report["skipped"]] == ["notes/policy.md"]
    assert "no staging folder" in report["skipped"][0]["reason"]


def test_a_renamed_note_finds_its_folder_by_id(vault):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2}, stem="old-name")

    fmm.migrate(root)

    assert note.read_bytes() == MINIMAL.encode()
    assert json.loads((folder / "document.json").read_text())["content_sha256"] == SHA


def test_an_interrupted_run_resumes(vault, monkeypatch):
    root, note = vault
    folder = _stage(root, {"id": "id-1", "version": 2})
    from artmind import frontmatter

    real = frontmatter.write_note

    def _crash(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(frontmatter, "write_note", _crash)
    with pytest.raises(KeyboardInterrupt):
        fmm.migrate(root)
    assert note.read_bytes() == _fat().encode(), "the note is untouched: the crash hit before its rewrite"
    staged_after_crash = (folder / "document.json").read_bytes()

    monkeypatch.setattr(frontmatter, "write_note", real)
    report = fmm.migrate(root)

    assert note.read_bytes() == MINIMAL.encode()
    assert (folder / "document.json").read_bytes() == staged_after_crash
    assert report["document_json_updated"] == 0


def test_a_converted_binarys_markdown_is_migrated_too(vault):
    root, _note = vault
    markdowns = root / ".artmind" / "data" / "documents" / "markdowns"
    markdowns.mkdir(parents=True)
    derived = markdowns / "deck.md"
    derived.write_text(
        "---\n_artmind_id: id-2\n_version: 1\n_domain: general\n_source_sha256: bin\n"
        "_source_type: pptx\ntitle: deck\n---\n\n# Deck\n"
    )
    folder = _stage(root, {"id": "id-2", "version": 1}, stem="deck")

    fmm.migrate(root)

    assert derived.read_text() == "---\n_artmind_id: id-2\n_domain: general\ntitle: deck\n---\n\n# Deck\n"
    staged = json.loads((folder / "document.json").read_text())
    assert (staged["source_sha256"], staged["source_type"]) == ("bin", "pptx")


def test_a_migrated_note_ingests_as_unchanged_and_is_not_rewritten(vault, tmp_path, monkeypatch):
    """Backward compatibility, end to end: after migration the next ingest
    finds its baseline in document.json -- same version, no re-extraction,
    no rewrite."""
    import artmind.db as db
    import artmind.document_identity as di
    import artmind.ingest as ing
    import paths

    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    fmm.migrate(root)
    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", root)
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", root)
    monkeypatch.setattr(ing, "KG_DIR", paths.KG_DIR)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)

    result = ing.ingest_file(note, "gemma4:e4b", "general", chunk_size=6000)

    assert (result["tier"], result["version"], result["artmind_id"]) == ("metadata_only", 2, "id-1")
    assert note.read_bytes() == MINIMAL.encode()


def test_refuses_during_a_merge(vault):
    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    (root / "x.md").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "other")
    (root / "x.md").write_text("theirs\n")
    _git(root, "commit", "-qam", "theirs")
    _git(root, "checkout", "-q", "-")
    (root / "x.md").write_text("ours\n")
    _git(root, "commit", "-qam", "ours")
    subprocess.run(["git", "merge", "-q", "other"], cwd=root, capture_output=True)

    with pytest.raises(fmm.MigrationError, match="a merge is in progress"):
        fmm.migrate(root)
    assert note.read_bytes() == _fat().encode()


def test_refuses_while_the_worker_runs(vault):
    """A live worker holds an exclusive flock on the vault's pid file (the
    liveness test `vault sync` uses); hold it the same way."""
    import fcntl
    import os

    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    pid_file = root / ".artmind" / "worker.pid"
    pid_file.write_text(str(os.getpid()))
    holder = open(pid_file, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        with pytest.raises(fmm.MigrationError, match="worker is running"):
            fmm.migrate(root)
    finally:
        holder.close()
    assert note.read_bytes() == _fat().encode()


def test_the_cli_prints_the_pause_notice_first_and_reports_json(vault, monkeypatch):
    from artmind.cli import cli

    root, note = vault
    _stage(root, {"id": "id-1", "version": 2})
    monkeypatch.setattr("artmind.vault.resolve_vault", lambda explicit=None: root)

    result = CliRunner().invoke(cli, ["vault", "migrate-frontmatter", "--dryRun", "--compact"])

    assert result.exit_code == 0, result.output
    assert "pause Obsidian Git" in result.stderr
    assert json.loads(result.stdout)["migrated"][0]["path"] == "notes/policy.md"
    assert note.read_bytes() == _fat().encode()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_frontmatter_migration.py -q`
Expected: `1 error` during collection — `ImportError: cannot import name 'frontmatter_migration' from 'artmind'`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/vault_sync.py` (lines 120-143):

```python
            f"{', ...' if len(unmerged) > 5 else ''}) -- resolve them first"
        )
    from artmind.vault import VaultLayout
    from artmind.worker_pid import live_pid
    import paths

    # The worker writes the same graph keys `sync` replays, and its staging
    # writes are what `sync` would be reading. Checked at every location a
    # live worker for this vault could plausibly hold (re-review item 2):
    #  - this vault's own pid file (item 1's fix; the common case)
    #  - paths.WORKER_PID_FILE: the process-wide location, which differs
    #    from the vault's own when ARTMIND_HOME is explicitly pointed
    #    elsewhere (a supported setup) -- the worker for THIS vault then
    #    writes there, not under vault_dir/.artmind/
    #  - paths.DATA_DIR / "worker.pid": the legacy (pre-item-1) location --
    #    a worker started before that fix, still running old code, would
    #    still hold this one. Drop this fallback after one release.
    pid_files = {VaultLayout(vault_dir).worker_pid, paths.WORKER_PID_FILE, paths.DATA_DIR / "worker.pid"}
    for pid_file in pid_files:
        if live_pid(pid_file) is not None:
            raise VaultSyncError(
                "the ingest worker is running for this vault -- wait for it to finish "
                "(`artmind ingest job-status`), then re-run `vault sync`"
            )
```

**with:**

```python
            f"{', ...' if len(unmerged) > 5 else ''}) -- resolve them first"
        )
    # The worker writes the same graph keys `sync` replays, and its staging
    # writes are what `sync` would be reading.
    if worker_running(vault_dir):
        raise VaultSyncError(
            "the ingest worker is running for this vault -- wait for it to finish "
            "(`artmind ingest job-status`), then re-run `vault sync`"
        )


def worker_running(vault_dir: Path) -> bool:
    """Whether the ingest worker is running for this vault -- `vault sync`
    and `vault migrate-frontmatter` both refuse while it is.

    Checked at every location a live worker for this vault could plausibly
    hold (re-review item 2):
     - this vault's own pid file (item 1's fix; the common case)
     - paths.WORKER_PID_FILE: the process-wide location, which differs
       from the vault's own when ARTMIND_HOME is explicitly pointed
       elsewhere (a supported setup) -- the worker for THIS vault then
       writes there, not under vault_dir/.artmind/
     - paths.DATA_DIR / "worker.pid": the legacy (pre-item-1) location --
       a worker started before that fix, still running old code, would
       still hold this one. Drop this fallback after one release."""
    from artmind.vault import VaultLayout
    from artmind.worker_pid import live_pid
    import paths

    pid_files = {VaultLayout(vault_dir).worker_pid, paths.WORKER_PID_FILE, paths.DATA_DIR / "worker.pid"}
    return any(live_pid(pid_file) is not None for pid_file in pid_files)
```

**Create** `artmind/frontmatter_migration.py`:

```python
"""`artmind vault migrate-frontmatter` (spec 2026-09-26 §5 R6, §12 item 5).

Before R6 every ingest rewrote a note's frontmatter with `_version`,
`_content_sha256`, `_source_commit`, `_ingested_at` and friends -- so nearly
every note conflict between two machines had artmind's lines in it. Those
fields now live in the note's staging folder (`document.json`); this command
moves them there for every note that still carries them, and rewrites each
such note once, removing them. It is the one bulk note rewrite, run
deliberately, with Obsidian Git's auto-commit paused.

Per note, in this order:

1. copy each moved field into `document.json` **only where that key is
   absent** -- a value a newer ingest already recorded always wins;
   `content_sha256`, `source_sha256`, `source_commit` and `ingested_at`
   describe one ingest, so they are copied only when the note's `_version`
   equals the staged `version` (otherwise the extraction they describe never
   landed, and leaving them out makes the next ingest re-extract, which is
   what that note needs);
2. rewrite the note without the moved fields (`artmind.frontmatter`: body,
   the user's keys and their order, and line endings kept byte-for-byte).

Idempotent: a note with none of the fields is left alone. Resumable: an
interruption between 1 and 2 leaves `document.json` already holding what
step 1 would copy again, and the rerun finishes step 2. A note with no
staging folder (never extracted) keeps its fields -- they still work as the
legacy baseline -- and is reported. Only the note's frontmatter and
`document.json` are written; nothing touches git or the graph.
"""
from __future__ import annotations

import json
from pathlib import Path

from artmind.document_identity import MOVED_FIELDS

#: Printed before anything else, dry run or not.
PAUSE_NOTICE = (
    "Before migrating: pause Obsidian Git's automatic commit-and-sync on this machine "
    "(Obsidian -> Settings -> Git: set the auto commit-and-sync interval to 0, or disable "
    "the plugin) and let every other machine finish pushing. This rewrites the frontmatter "
    "of every note that still carries artmind's per-ingest fields. When it finishes, "
    "commit the result as one change, then turn auto commit-and-sync back on."
)

#: Fields that describe one particular ingest: meaningful in document.json
#: only when that ingest's extraction is the one staged there.
_VERSION_BOUND = frozenset({"content_sha256", "source_sha256", "source_commit", "ingested_at"})


class MigrationError(Exception):
    """The vault is in a state the migration must not run in; nothing was
    written."""


def _plain(value):
    """YAML may hand back a date/datetime for an unquoted timestamp."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _notes(vault_dir: Path) -> list[Path]:
    """Every vault note (the walk `docs reindex` uses) and every converted
    binary's markdown in the data dir's markdown store."""
    import paths
    from artmind.reindex import _iter_vault_markdown

    notes = list(_iter_vault_markdown(vault_dir))
    markdowns = Path(paths.MARKDOWNS_DIR)
    if markdowns.is_dir():
        notes += sorted(markdowns.glob("*.md"))
    return notes


class _StagingIndex:
    """`(domain, artmind_id) -> staging folder`: the folder named after the
    note first, else any folder in the domain whose document.json carries
    the id (a note renamed since its extraction). Each domain is read once."""

    def __init__(self, kg_dir: Path):
        self._kg_dir = Path(kg_dir)
        self._by_domain: dict[str, dict[str, Path]] = {}

    @staticmethod
    def _read(folder: Path) -> dict | None:
        try:
            document = json.loads((folder / "document.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def find(self, domain: str, stem: str, artmind_id: str) -> tuple[Path, dict] | None:
        folder = self._kg_dir / domain / stem
        document = self._read(folder)
        if document is not None and document.get("id") == artmind_id:
            return folder, document
        if domain not in self._by_domain:
            index: dict[str, Path] = {}
            domain_dir = self._kg_dir / domain
            if domain_dir.is_dir():
                for candidate in sorted(p for p in domain_dir.iterdir() if p.is_dir()):
                    if candidate.name.endswith((".artmind-tmp", ".artmind-old")):
                        continue
                    found = self._read(candidate)
                    if found is not None and isinstance(found.get("id"), str):
                        index.setdefault(found["id"], candidate)
            self._by_domain[domain] = index
        folder = self._by_domain[domain].get(artmind_id)
        if folder is None:
            return None
        document = self._read(folder)
        return (folder, document) if document is not None else None


def _plan(path: Path, index: _StagingIndex) -> dict:
    """What migrating one note does, without doing it."""
    from artmind import frontmatter
    from artmind.ingest import _parse_md_frontmatter

    entry: dict = {"path": path}
    try:
        raw = frontmatter.read_note(path)
    except (OSError, UnicodeDecodeError) as e:
        return {**entry, "action": "skip", "reason": f"unreadable: {e}"}
    meta, _ = _parse_md_frontmatter(raw.replace("\r\n", "\n"))
    moved = [key for key in MOVED_FIELDS if key in meta]
    if not moved:
        return {**entry, "action": "minimal"}
    artmind_id, domain = meta.get("_artmind_id"), meta.get("_domain")
    if not artmind_id or not domain:
        return {**entry, "action": "skip", "reason": "no _artmind_id/_domain -- not an artmind note"}
    found = index.find(domain, path.stem, artmind_id)
    if found is None:
        return {
            **entry, "action": "skip",
            "reason": "no staging folder (never extracted) -- its fields stay; they still work",
        }
    folder, document = found
    try:
        stripped = frontmatter.remove_fields(raw, moved)
    except frontmatter.FrontmatterEditError as e:
        return {**entry, "action": "skip", "reason": str(e)}
    same_version = str(meta.get("_version")) == str(document.get("version"))
    updates = {}
    for key in moved:
        target = MOVED_FIELDS[key]
        if target is None or target == "version" or target in document:
            continue
        if target in _VERSION_BOUND and not same_version:
            continue
        updates[target] = _plain(meta[key])
    return {
        **entry, "action": "migrate", "raw": raw, "stripped": stripped, "moved": moved,
        "staging": folder, "document": document, "updates": updates,
        "version_mismatch": not same_version,
    }


def migrate(vault_dir: Path, *, dry_run: bool = False) -> dict:
    """Move every note's per-ingest frontmatter into its `document.json` and
    strip it from the note. Raises `MigrationError`, writing nothing, while a
    merge/rebase is in progress or the ingest worker runs."""
    import paths
    from artmind import frontmatter, vault_sync
    from artmind.ingest import write_staging
    from artmind.vault import VaultLayout

    vault_dir = Path(vault_dir)
    what = vault_sync.operation_in_progress(vault_dir)
    if what is not None:
        raise MigrationError(f"{what} is in progress in this vault -- finish it first, then re-run")
    if vault_sync.worker_running(vault_dir):
        raise MigrationError(
            "the ingest worker is running for this vault -- wait for it to finish "
            "(`artmind ingest job-status`), then re-run"
        )

    def _rel(path: Path) -> str:
        try:
            return path.relative_to(vault_dir).as_posix()
        except ValueError:
            return str(path)

    index = _StagingIndex(paths.KG_DIR)
    scratch = VaultLayout(vault_dir).data_dir
    report: dict = {
        "dry_run": dry_run, "notes": 0, "migrated": [], "already_minimal": 0,
        "document_json_updated": 0, "version_mismatch": [], "skipped": [],
    }
    for path in _notes(vault_dir):
        report["notes"] += 1
        plan = _plan(path, index)
        if plan["action"] == "minimal":
            report["already_minimal"] += 1
            continue
        if plan["action"] == "skip":
            report["skipped"].append({"path": _rel(path), "reason": plan["reason"]})
            continue
        if plan["version_mismatch"]:
            report["version_mismatch"].append(_rel(path))
        if plan["updates"]:
            report["document_json_updated"] += 1
        report["migrated"].append({
            "path": _rel(path), "removed": plan["moved"],
            "document_json": {"folder": _rel(plan["staging"]), "added": sorted(plan["updates"])},
        })
        if dry_run:
            continue
        if plan["updates"]:
            write_staging(plan["staging"], {"document.json": {**plan["document"], **plan["updates"]}})
        frontmatter.write_note(path, plan["stripped"], scratch_dir=scratch)
    return report
```

**Replace in** `artmind/cli.py` (lines 3583-3586):

```python
@cli.group("vault")
def vault():
    """Which vault is active and how far behind it each store is (`status`), git-diff-driven sync into Neo4j/the structured store (`sync`), and read-only readiness checks for Obsidian Git (`doctor`)."""
    pass
```

**with:**

```python
@cli.group("vault")
def vault():
    """Which vault is active and how far behind it each store is (`status`), git-diff-driven sync into Neo4j/the structured store (`sync`), read-only readiness checks for Obsidian Git (`doctor`), and the one-off move of per-ingest fields out of notes (`migrate-frontmatter`)."""
    pass
```

**Replace in** `artmind/cli.py` (lines 3736-3739):

```python
    result = doctor_run(vault_dir)
    _echo_json(result, compact)
    if not result["ok"]:
        raise SystemExit(1)
```

**with:**

```python
    result = doctor_run(vault_dir)
    _echo_json(result, compact)
    if not result["ok"]:
        raise SystemExit(1)


@vault.command("migrate-frontmatter")
@click.option("--dryRun", "dry_run", is_flag=True, help="Report which notes would be rewritten and what would move into document.json, writing nothing.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_migrate_frontmatter_cmd(dry_run, compact):
    """Move per-ingest fields (_version, _content_sha256, ...) out of every note's frontmatter into its staging document.json -- once.

    Notes keep only `_artmind_id` and `_domain` (spec 2026-09-26 R6); older
    artmind also wrote `_version`, `_content_sha256`, `_source_sha256`,
    `_source_commit`, `_ingested_at`, `_source_path`, `_source_type` and
    `_status` on every ingest. This copies each into the note's staging
    folder (`.artmind/data/kg/<domain>/<note>/document.json`) where that key
    is absent, then rewrites the note without them -- body, your own keys,
    their order and line endings untouched. Converted binaries' markdown
    (`.artmind/data/documents/markdowns/`) is migrated the same way.

    Pause Obsidian Git's auto commit-and-sync first (the command reminds
    you), then commit the result as one change. Idempotent and resumable:
    re-run it after an interruption. A note with no staging folder keeps its
    fields (they still work) and is listed under `skipped`. Refuses while a
    merge/rebase is in progress or the ingest worker is running. Writes no
    git and no graph.
    """
    _setup_logger()
    from artmind import vault as vault_mod
    from artmind.frontmatter_migration import PAUSE_NOTICE, MigrationError, migrate

    try:
        vault_dir = vault_mod.resolve_vault()
    except vault_mod.VaultError as e:
        raise click.ClickException(str(e))
    if vault_dir is None:
        raise click.ClickException(
            "Not inside an artmind vault.\n"
            "  cd into one, or run `artmind init` to make this directory a vault."
        )
    click.echo(PAUSE_NOTICE, err=True)
    try:
        result = migrate(vault_dir, dry_run=dry_run)
    except MigrationError as e:
        raise click.ClickException(str(e))
    _echo_json(result, compact)
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_frontmatter_migration.py test/test_vault_sync.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2594 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py artmind/frontmatter_migration.py artmind/cli.py test/test_frontmatter_migration.py
git commit -m "feat(vault): migrate-frontmatter moves per-ingest fields into document.json, once" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `artmind/vault_resolve.py` — one whole side per unit, by content, staged

`plan(vault_dir)` refuses unless exactly one merge is in progress, lists every unmerged path (`git diff --name-only -z --diff-filter=U`), and sorts each: under `.artmind/data/` into a unit (`kg` folder / `table` / curation `record` / other `file`), everything else into `reported`. For each unit it reads both sides' committed files (`ls-tree` + one `cat-file --batch` per side) and applies the unit's rule (decisions 2–6). `resolve(vault_dir, dry_run=False)` then, per picked unit, runs `git checkout <HEAD|MERGE_HEAD> -- <files the side has>`, deletes the files it lacks (and the folders that leaves empty), and `git add -- <paths>` — after `_assert_scoped` has refused anything outside `.artmind/data/`, and after re-checking the merge heads have not moved. It never commits.

These are the first git writes in artmind since spec D1, so `test/test_no_git_writes.py` gets its exception in the same task: `ALLOWED` maps `artmind/vault_resolve.py` to exactly `{"checkout", "add"}`; any other write subcommand there, or these anywhere else, still fails, and an allowance nothing uses fails too.

Tests build real merge conflicts: a throwaway repo with the real `.gitattributes` block (`merge=binary` for `.artmind/data/**`), a base commit, a `theirs` branch and a `main` commit, then `git merge` left in progress.

**Files:**
- Create: `artmind/vault_resolve.py`
- Modify: `test/test_no_git_writes.py`
- Test: `test/test_vault_resolve.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_vault_resolve.py`:

```python
"""`artmind vault resolve` (spec 2026-09-26 §5 R7) against real merge
conflicts in throwaway git repos: a whole side per unit, deterministic in
either merge direction, only under `.artmind/data/`, never a commit."""
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from artmind import vault, vault_resolve as vr
from artmind.document_identity import compute_content_sha256

KG = ".artmind/data/kg/general/policy"
NOTE = "notes/policy.md"


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check).stdout


def _write(repo, files: dict):
    for rel, content in files.items():
        path = repo / rel
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    import paths

    root = tmp_path / "v"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    vault.write_gitattributes(root)  # .artmind/data/** merge=binary (R3)
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    return root


def _conflict(repo, base: dict, ours: dict, theirs: dict, *, into="main", other="theirs"):
    """Commit `base` on main, `theirs` on a branch and `ours` on main, then
    merge the branch into `into` -- leaving the merge in progress."""
    _write(repo, base)
    _commit(repo, "base")
    _git(repo, "checkout", "-qb", "theirs")
    _write(repo, theirs)
    _commit(repo, "theirs")
    _git(repo, "checkout", "-q", "main")
    _write(repo, ours)
    _commit(repo, "ours")
    if into != "main":
        _git(repo, "checkout", "-q", into)
    _git(repo, "merge", "--no-edit", other, check=False)
    assert (repo / ".git" / "MERGE_HEAD").exists()


def _doc_folder(doc_id="id-1", *, body="Body v1.\n", obs="[1]", rel="[]", extra=None):
    document = {"id": doc_id, "version": 2, "content_sha256": compute_content_sha256(body),
                "source_path": NOTE, "source_type": "md"}
    files = {
        f"{KG}/document.json": json.dumps(document, indent=2),
        f"{KG}/observations.json": obs,
        f"{KG}/chunks.json": "[]",
        f"{KG}/relationships.json": rel,
    }
    files.update(extra or {})
    return files


def _note(body):
    return f"---\n_artmind_id: id-1\n_domain: general\n---\n\n{body}"


def _index(repo):
    return _git(repo, "ls-files", "--stage")


def test_the_folder_extracted_from_the_merged_note_wins_whole(repo):
    """Theirs extracted v2 of the note, which the merge took; ours re-ran
    v1's extraction (different observations) and also touched a file theirs
    did not. The whole folder comes from theirs -- the file only ours
    changed, and the chunk cache only ours has, included."""
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = _doc_folder(obs="[1]", rel='["ours only"]', extra={f"{KG}/chunks/sha1/chunk_001.json": "{}"})
    theirs = {NOTE: _note("Body v2.\n"), **_doc_folder(body="Body v2.\n", obs="[2]")}
    _conflict(repo, base, ours, theirs)
    head = _git(repo, "rev-parse", "HEAD")

    report = vr.resolve(repo)

    [unit] = report["resolved"]
    assert (unit["unit"], unit["kind"], unit["side"]) == (KG, "kg", "theirs")
    assert "merged note" in unit["rule"]
    for name in ("document.json", "observations.json", "relationships.json", "chunks.json"):
        assert (repo / KG / name).read_text() == _git(repo, "show", f"theirs:{KG}/{name}")
    assert not (repo / KG / "chunks" / "sha1" / "chunk_001.json").exists()
    assert _git(repo, "diff", "--name-only", "--diff-filter=U") == ""
    assert _git(repo, "diff", "--cached", "--name-only", "theirs", "--", KG) == "", "the index holds theirs exactly"
    assert _git(repo, "rev-parse", "HEAD") == head, "never a commit"
    assert (repo / ".git" / "MERGE_HEAD").exists(), "Obsidian Git (or you) concludes the merge"


def _resolved_bytes(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in sorted((repo / KG).rglob("*")) if p.is_file()}


def test_both_directions_pick_the_same_content(repo):
    """Two machines extracted the same note version (both match its body):
    the greater fingerprint wins, whichever machine merges which -- so the
    two merge commits then merge with each other cleanly."""
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    a = _doc_folder(obs='["machine A"]')
    b = _doc_folder(obs='["machine B"]')
    _conflict(repo, base, a, b)

    vr.resolve(repo)
    a_into = _resolved_bytes(repo)

    _git(repo, "merge", "--abort")
    _git(repo, "checkout", "-q", "theirs")
    _git(repo, "merge", "--no-edit", "main", check=False)
    report = vr.resolve(repo)
    b_into = _resolved_bytes(repo)

    assert "fingerprint" in report["resolved"][0]["rule"]
    assert a_into == b_into


def test_a_folder_whose_note_is_still_conflicted_waits(repo):
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = {NOTE: _note("Body ours.\n"), **_doc_folder(body="Body ours.\n", obs="[1]")}
    theirs = {NOTE: _note("Body theirs.\n"), **_doc_folder(body="Body theirs.\n", obs="[2]")}
    _conflict(repo, base, ours, theirs)
    before = (_index(repo), (repo / KG / "observations.json").read_bytes())

    report = vr.resolve(repo)

    assert report["resolved"] == []
    assert [p["unit"] for p in report["pending"]] == [KG]
    assert report["reported"] == [{"path": NOTE, "why": "your note -- resolve it in Obsidian"}]
    assert (_index(repo), (repo / KG / "observations.json").read_bytes()) == before

    (repo / NOTE).write_text(_note("Body ours.\n"))  # the user resolves the note in Obsidian
    again = vr.resolve(repo)

    assert [(u["unit"], u["side"]) for u in again["resolved"]] == [(KG, "ours")]


def test_a_note_deleted_by_the_merge_takes_the_side_without_its_folder(repo):
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    ours = {NOTE: None, **{k: None for k in _doc_folder()}}  # archived on this machine
    theirs = _doc_folder(obs="[2]")  # re-extracted on the other
    _conflict(repo, base, ours, theirs)

    report = vr.resolve(repo)

    assert [(u["unit"], u["side"]) for u in report["resolved"]] == [(KG, "ours")]
    assert not (repo / KG).exists(), "no empty folder left behind"
    assert _git(repo, "diff", "--name-only", "--diff-filter=U") == ""


TABLE = ".artmind/data/structured_text/banking/accounts"


def _table(ingested_at, rows):
    meta = {"table": {"domain": "banking", "table_name": "accounts", "ingested_at": ingested_at}}
    return {f"{TABLE}.csv": "id\n" + rows, f"{TABLE}.meta.json": json.dumps(meta, sort_keys=True)}


def test_a_table_takes_the_later_refresh_csv_and_meta_together(repo):
    base = _table("2026-09-01T00:00:00", "1\n")
    ours = _table("2026-09-03T00:00:00", "1\n3\n")
    theirs = _table("2026-09-02T00:00:00", "1\n2\n")
    _conflict(repo, base, ours, theirs)

    report = vr.resolve(repo)

    assert [(u["unit"], u["kind"], u["side"]) for u in report["resolved"]] == [(TABLE, "table", "ours")]
    assert (repo / f"{TABLE}.csv").read_text() == "id\n1\n3\n"
    assert "2026-09-03" in (repo / f"{TABLE}.meta.json").read_text()


RECORD = ".artmind/data/curation/conflicts/c0ffee.json"


def _conflict_record(**fields):
    record = {"id": "c0ffee", "status": "open", "resolved_at": None, "resolution_reason": None,
              "detected_at": "2026-09-01T00:00:00+00:00", "claim_a": "x", "claim_b": "y"}
    record.update(fields)
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def test_two_machines_detecting_the_same_conflict_settle_on_one_record(repo):
    """Plan C leftover (b): both machines detected (and one resolved) the
    same conflict before pulling -- a git conflict on that one record file.
    The decision beats open, in either merge direction."""
    base = {"a.txt": "x"}
    ours = {RECORD: _conflict_record(detected_at="2026-09-05T00:00:00+00:00")}
    theirs = {RECORD: _conflict_record(status="resolved", resolved_at="2026-09-02T00:00:00+00:00",
                                       resolution_reason="board minutes")}
    _conflict(repo, base, ours, theirs)

    report = vr.resolve(repo)

    assert [(u["kind"], u["side"]) for u in report["resolved"]] == [("record", "theirs")]
    assert json.loads((repo / RECORD).read_text())["status"] == "resolved"


@pytest.mark.parametrize("ours, theirs, expected", [
    ({"status": "open"}, {"status": "dismissed", "resolved_at": "2026-01-01"}, "theirs"),
    ({"status": "resolved", "resolved_at": "2026-09-03"}, {"status": "resolved", "resolved_at": "2026-09-02"}, "ours"),
    ({"detected_at": "2026-09-01"}, {"detected_at": "2026-09-04"}, "theirs"),
])
def test_the_conflict_record_rule(ours, theirs, expected):
    sides = {
        "ours": {RECORD: _conflict_record(**ours).encode()},
        "theirs": {RECORD: _conflict_record(**theirs).encode()},
    }
    assert vr._decide_record(RECORD, sides)[0] == expected
    swapped = {"ours": sides["theirs"], "theirs": sides["ours"]}
    assert vr._decide_record(RECORD, swapped)[0] != expected, "the rule reads content, never the side's name"


def test_the_other_kinds_rules():
    sup = ".artmind/data/curation/supersessions/s1.json"
    manual = json.dumps({"id": "s1", "detected_by": "manual", "effective": "2026-01-01"}).encode()
    detected = json.dumps({"id": "s1", "detected_by": "notice", "effective": "2026-02-01"}).encode()
    assert vr._decide_record(sup, {"ours": {sup: detected}, "theirs": {sup: manual}})[0] == "theirs"

    syn = ".artmind/data/curation/syntheses/e1.json"
    old = json.dumps({"id": "e1", "created_at": "2026-09-01"}).encode()
    new = json.dumps({"id": "e1", "created_at": "2026-09-09"}).encode()
    assert vr._decide_record(syn, {"ours": {syn: new}, "theirs": {syn: old}})[0] == "ours"

    life = ".artmind/data/curation/lifecycle/l1.json"
    x, y = b'{"domain": "a"}', b'{"domain": "b"}'

    def picked(ours, theirs):
        sides = {"ours": {life: ours}, "theirs": {life: theirs}}
        return sides[vr._decide_record(life, sides)[0]][life]

    assert picked(x, y) == picked(y, x), "the greater fingerprint, whichever side holds it"
    assert vr._decide_record(life, {"ours": {}, "theirs": {life: x}})[0] == "theirs", "kept over deleted"


def test_human_curated_files_and_notes_are_reported_never_touched(repo):
    same_as = ".artmind/same_as.yaml"
    base = {same_as: "groups: []\n", NOTE: "base\n"}
    ours = {same_as: "groups: [a]\n", NOTE: "ours\n"}
    theirs = {same_as: "groups: [b]\n", NOTE: "theirs\n"}
    _conflict(repo, base, ours, theirs)
    before = (_index(repo), (repo / same_as).read_bytes(), (repo / NOTE).read_bytes())

    report = vr.resolve(repo)

    assert report["resolved"] == [] and report["remaining"] == 2
    assert sorted(r["path"] for r in report["reported"]) == [same_as, NOTE]
    assert (_index(repo), (repo / same_as).read_bytes(), (repo / NOTE).read_bytes()) == before


def test_a_dry_run_shows_every_pick_and_writes_nothing(repo):
    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    _conflict(repo, base, _doc_folder(obs="[1]"), _doc_folder(obs="[2]"))
    before = (_index(repo), (repo / KG / "observations.json").read_bytes())

    report = vr.resolve(repo, dry_run=True)

    assert report["dry_run"] is True
    assert [u["unit"] for u in report["resolved"]] == [KG]
    assert (_index(repo), (repo / KG / "observations.json").read_bytes()) == before


def test_refuses_without_a_merge_in_progress(repo):
    _write(repo, {"a.txt": "x"})
    _commit(repo, "base")

    with pytest.raises(vr.ResolveError, match="no merge is in progress"):
        vr.resolve(repo)


def test_the_write_guard_refuses_any_path_outside_artmind_data(repo):
    """Whatever the planner produced, a path outside `.artmind/data/` never
    reaches git: an uncommitted edit to the note survives the attempt."""
    _write(repo, {NOTE: "committed\n", ".artmind/same_as.yaml": "groups: []\n"})
    _commit(repo, "base")
    _write(repo, {NOTE: "my unsaved edit\n"})

    for present, absent in (([NOTE], []), ([], [NOTE]), ([".artmind/same_as.yaml"], []),
                            ([".artmind/data/../../notes/policy.md"], [])):
        with pytest.raises(vr.ResolveError, match="only writes under"):
            vr._stage_side(repo, "HEAD", present, absent)

    assert (repo / NOTE).read_text() == "my unsaved edit\n"
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_after_resolve_the_merge_commits_and_vault_sync_preflight_passes(repo):
    from artmind import vault_sync

    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]"), RECORD: _conflict_record()}
    ours = {**_doc_folder(obs="[1]"), RECORD: _conflict_record(detected_at="2026-09-02")}
    theirs = {**_doc_folder(obs="[2]"), RECORD: _conflict_record(detected_at="2026-09-03")}
    _conflict(repo, base, ours, theirs)
    with pytest.raises(vault_sync.VaultSyncError):
        vault_sync.preflight(repo)

    vr.resolve(repo)
    _git(repo, "commit", "-qm", "merge (Obsidian Git)")

    vault_sync.preflight(repo)
```

**Replace in** `test/test_no_git_writes.py` (lines 1-3):

```python
"""Guard for spec 2026-09-26 D1: artmind never writes to the vault's git repo.

Obsidian Git owns commit/pull/push. A source scan is deliberately blunt: any
```

**with:**

```python
"""Guard for spec 2026-09-26 D1: artmind never writes to the vault's git repo
-- with one narrow, named exception (`ALLOWED`).

Obsidian Git owns commit/pull/push. A source scan is deliberately blunt: any
```

**Replace in** `test/test_no_git_writes.py` (lines 66-81):

```python
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

**with:**

```python
# The exceptions: source file -> the write subcommands it may use, each with
# its reason. Any other write subcommand in that file -- and these same ones
# anywhere else -- still fails.
#
# `vault resolve` (spec 2026-09-26 R7; D1/D2 as amended): settling a merge
# conflict in artmind's generated files means staging the side it picks --
# `git checkout <HEAD|MERGE_HEAD> -- <paths>` and `git add -- <paths>` --
# only for paths under `.artmind/data/**` (`vault_resolve._assert_scoped`),
# only while a merge is in progress, and never a commit.
ALLOWED: dict[str, frozenset[str]] = {
    "artmind/vault_resolve.py": frozenset({"checkout", "add"}),
}


def _hits(rel: str, text: str) -> list[str]:
    allowed = ALLOWED.get(rel, frozenset())
    return [
        f"{rel}:{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        for p in PATTERNS
        for m in p.finditer(line)
        if m.group(1) not in allowed
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_git_write_calls(path):
    rel = str(path.relative_to(REPO))
    hits = _hits(rel, path.read_text(encoding="utf-8"))
    assert not hits, "artmind must not write to the vault's git repo:\n" + "\n".join(hits)


def test_the_resolve_exception_is_only_checkout_and_add_only_there():
    checkout = 'run_command(["git", "checkout", rev, "--", *batch], cwd=vault_dir)'
    assert _hits("artmind/vault_resolve.py", checkout) == []
    assert _hits("artmind/vault_resolve.py", 'run_command(["git", "add", "--", *batch], cwd=d)') == []
    assert _hits("artmind/vault_resolve.py", 'run_command(["git", "commit", "-m", "x"], cwd=d)')
    assert _hits("artmind/vault_resolve.py", 'run_command(["git", "rm", "--cached", p], cwd=d)')
    assert _hits("artmind/vault_sync.py", checkout), "the same call anywhere else still fails"


def test_every_allowance_is_still_used():
    """An exception nothing needs any more is removed, not left open."""
    for rel, subcommands in ALLOWED.items():
        text = (REPO / rel).read_text(encoding="utf-8")
        for sub in subcommands:
            assert f'["git", "{sub}"' in text, f"{rel} no longer runs git {sub}; drop it from ALLOWED"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_resolve.py test/test_no_git_writes.py -q`
Expected: `1 error` during collection — `ImportError: cannot import name 'vault_resolve' from 'artmind'` (and, once the module exists without the `ALLOWED` change, `test_no_git_write_calls[artmind/vault_resolve.py]` fails on its `checkout`/`add` lines).

- [ ] **Step 3: Implement**

**Create** `artmind/vault_resolve.py`:

```python
"""`artmind vault resolve` (spec 2026-09-26 §5 R7): settle merge conflicts in
artmind's generated files, deterministically, so two machines resolving the
same conflict pick the same content.

Everything under `.artmind/data/**` is regenerable and `merge=binary` (R3):
git reports a conflict instead of line-merging two extraction runs into a
valid-looking mix. This command picks one WHOLE side per unit and stages it:

- **KG document folder** (`kg/<domain>/<folder>/`: document.json,
  observations.json, chunks.json, relationships.json and its chunk cache,
  all together, never mixed). The side whose `document.json`
  `content_sha256` equals the sha of the merged note's body wins; if the
  merged note was deleted, the side without the folder wins; otherwise the
  greater staging fingerprint (`sync_state.staging_fingerprint`) -- arbitrary
  but the same on every machine.
- **Structured table** (`structured_text/<domain>/<table>.csv` +
  `.meta.json` together): the side that has the table over the side that
  deleted it, then the later `table.ingested_at` (the table's last refresh),
  then the greater digest of the two files.
- **Curation record** (`curation/<kind>/<id>.json`, one file per record,
  spec §15 A9/A10): a kept record over a deleted one, then a per-kind rule
  (`_RECORD_RULES`), then the greater record fingerprint.
- **Any other file under `.artmind/data/`**: a kept file over a deleted one,
  then the greater sha256 of its bytes.

Every rule reads only the two sides' committed content and the merged note
-- never which side is "ours" -- so the machine that merged A into B and the
one that merged B into A choose the same bytes, and their merge commits then
merge cleanly with each other.

Reported, never touched: a human note (resolve it in Obsidian), and
artmind's human-curated files outside `.artmind/data/` (`same_as.yaml`,
domain schemas and table mappings, `vault.yaml`). A KG folder whose note is
still conflicted waits until the note is resolved (its body decides).

**The one git write artmind makes (spec D1/D2, as amended).** For each unit
it picked, `git checkout <HEAD|MERGE_HEAD> -- <paths>` for the files the
chosen side has, deletes those it lacks, then `git add -- <paths>`. Only
paths under `.artmind/data/`, only while a merge is in progress, and never a
commit: Obsidian Git (or you) concludes the merge. `--dryRun` shows each
pick and writes nothing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from utils.functions import run_command

from artmind.vault import MARKER

#: Everything `vault resolve` may write, relative to the vault root.
DATA_PREFIX = f"{MARKER}/data/"

#: Pathspecs are literal: a file name holding `*` or `[` is one file.
_LITERAL = {"GIT_LITERAL_PATHSPECS": "1"}

_BATCH = 200

_CONFLICT_MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")


class ResolveError(Exception):
    """`vault resolve` cannot run; nothing was written."""


def _read(vault_dir: Path, args: list[str]) -> str:
    rc, out, err = run_command(["git", *args], cwd=vault_dir, extra_env=_LITERAL)
    if rc != 0:
        raise ResolveError(f"git {' '.join(args[:3])} failed: {(err or out).strip()}")
    return out


def _merge_heads(vault_dir: Path) -> tuple[str, str]:
    """`(HEAD, MERGE_HEAD)` of the merge in progress; refuses otherwise."""
    from artmind.vault_sync import operation_in_progress

    what = operation_in_progress(vault_dir)
    if what is None:
        raise ResolveError("no merge is in progress in this vault -- nothing to resolve")
    if what != "a merge":
        raise ResolveError(f"{what} is in progress, not a merge -- `vault resolve` only settles merges (spec D5)")
    git_dir = Path(_read(vault_dir, ["rev-parse", "--absolute-git-dir"]).strip())
    heads = [line.strip() for line in (git_dir / "MERGE_HEAD").read_text().splitlines() if line.strip()]
    if len(heads) != 1:
        raise ResolveError("an octopus merge (several MERGE_HEADs) -- resolve it by hand")
    return _read(vault_dir, ["rev-parse", "HEAD"]).strip(), heads[0]


def _unmerged(vault_dir: Path) -> list[str]:
    out = _read(vault_dir, ["diff", "--name-only", "-z", "--diff-filter=U"])
    return sorted({p for p in out.split("\0") if p})


def _unit_of(path: str) -> tuple[str, str]:
    """`(kind, unit)` for a conflicted path under `.artmind/data/`."""
    parts = PurePosixPath(path[len(DATA_PREFIX):]).parts
    if len(parts) >= 4 and parts[0] == "kg":
        return "kg", f"{DATA_PREFIX}kg/{parts[1]}/{parts[2]}"
    if len(parts) == 3 and parts[0] == "structured_text":
        for suffix in (".meta.json", ".csv"):
            if parts[2].endswith(suffix):
                return "table", f"{DATA_PREFIX}structured_text/{parts[1]}/{parts[2][:-len(suffix)]}"
    if len(parts) == 3 and parts[0] == "curation" and parts[2].endswith(".json"):
        return "record", path
    return "file", path


def _unit_paths(vault_dir: Path, kind: str, unit: str, heads: tuple[str, str]) -> list[str]:
    """Every file of the unit on either side."""
    if kind == "kg":
        found: set[str] = set()
        for rev in heads:
            out = _read(vault_dir, ["ls-tree", "-r", "-z", "--name-only", rev, "--", unit])
            found.update(p for p in out.split("\0") if p)
        return sorted(found)
    if kind == "table":
        return [f"{unit}.csv", f"{unit}.meta.json"]
    return [unit]


def _side_files(vault_dir: Path, rev: str, paths: list[str]) -> dict[str, bytes]:
    """`{path: bytes}` for the unit's files present at `rev`."""
    from artmind.vault_sync import _cat_blobs

    out = _read(vault_dir, ["ls-tree", "-r", "-z", rev, "--", *paths])
    oid_of = {}
    for entry in out.split("\0"):
        meta, _, path = entry.partition("\t")
        fields = meta.split(" ")
        if path in paths and len(fields) == 3 and fields[1] == "blob":
            oid_of[path] = fields[2]
    blobs = _cat_blobs(vault_dir, list(oid_of.values()))
    return {path: blobs[oid] for path, oid in oid_of.items() if oid in blobs}


def _json(data: bytes | None) -> dict | None:
    if data is None:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _digest(files: dict[str, bytes], paths: list[str]) -> str:
    """A deterministic digest of one side of a unit: each path and its bytes,
    in path order; empty when the side has none of them."""
    if not files:
        return ""
    h = hashlib.sha256()
    for path in paths:
        data = files.get(path)
        h.update(path.encode("utf-8") + b"\0" + (b"absent" if data is None else hashlib.sha256(data).digest()))
    return h.hexdigest()


def _greater(keys: dict[str, tuple]) -> str:
    """The side whose key is greater; `ours` on a tie (the sides are then
    identical in everything the rule reads)."""
    return "theirs" if keys["theirs"] > keys["ours"] else "ours"


# ── per-unit rules ───────────────────────────────────────────────────────────


def _note_state(vault_dir: Path, documents: dict, ids: "_NoteIndex") -> tuple[str, str | None]:
    """`("body", sha)` for the merged note's body, `("deleted", path)` when
    the merge removed it, `("conflicted", path)` while its working-tree file
    still holds conflict markers, or `("unknown", None)` when no note can be
    found (a converted binary, an `artmind update` folder, a document from
    before `source_path` whose note no longer names it). The working tree is
    what counts: a note you resolved in Obsidian but have not staged yet is
    resolved."""
    from artmind.document_identity import compute_content_sha256
    from artmind.ingest import _parse_md_frontmatter

    candidates = []
    for document in documents.values():
        if not document:
            continue
        source = document.get("source_path")
        if (
            isinstance(source, str) and document.get("source_type") in (None, "md")
            and not PurePosixPath(source).is_absolute() and not source.startswith(MARKER)
        ):
            candidates.append(source)
        found = ids.path_of(document.get("id"))
        if found:
            candidates.append(found)
    for relpath in dict.fromkeys(candidates):
        note = vault_dir / relpath
        if not note.is_file():
            continue
        text = note.read_text(encoding="utf-8", errors="replace")
        if any(line.startswith(_CONFLICT_MARKERS) for line in text.splitlines()):
            return "conflicted", relpath
        _, body = _parse_md_frontmatter(text)
        return "body", compute_content_sha256(body)
    if candidates:
        return "deleted", candidates[0]
    return "unknown", None


class _NoteIndex:
    """`_artmind_id -> vault-relative note path`, read from the working
    tree's notes once, on first use."""

    def __init__(self, vault_dir: Path):
        self._vault_dir = vault_dir
        self._paths: dict[str, str] | None = None

    def path_of(self, artmind_id) -> str | None:
        if not isinstance(artmind_id, str):
            return None
        if self._paths is None:
            from artmind.ingest import _parse_md_frontmatter
            from artmind.reindex import _iter_vault_markdown

            self._paths = {}
            for note in _iter_vault_markdown(self._vault_dir):
                try:
                    meta, _ = _parse_md_frontmatter(note.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue
                if isinstance(meta.get("_artmind_id"), str):
                    self._paths.setdefault(meta["_artmind_id"], note.relative_to(self._vault_dir).as_posix())
        return self._paths.get(artmind_id)


def _decide_kg(vault_dir, unit, paths, sides, ids) -> tuple[str | None, str]:
    from artmind.sync_state import staging_fingerprint

    documents = {s: _json(files.get(f"{unit}/document.json")) for s, files in sides.items()}
    state, value = _note_state(vault_dir, documents, ids)
    if state == "conflicted":
        return None, f"its note {value} is still conflicted -- resolve the note, then re-run `vault resolve`"
    if state == "body":
        matching = [s for s, d in documents.items() if d and d.get("content_sha256") == value]
        if len(matching) == 1:
            return matching[0], "the side extracted from the merged note's body"
    if state == "deleted":
        absent = [s for s, files in sides.items() if not files]
        if len(absent) == 1:
            return absent[0], f"the merge deleted its note {value}"
    fingerprints = {
        s: (staging_fingerprint(lambda name, f=files: f.get(f"{unit}/{name}")) or "", _digest(files, paths))
        for s, files in sides.items()
    }
    return _greater(fingerprints), "the greater staging fingerprint (both or neither match the note)"


def _decide_table(unit, paths, sides) -> tuple[str, str]:
    present = [s for s, files in sides.items() if files]
    if len(present) == 1:
        return present[0], "the side that kept the table"

    def _key(files):
        meta = _json(files.get(f"{unit}.meta.json")) or {}
        table = meta.get("table") if isinstance(meta.get("table"), dict) else {}
        return (str(table.get("ingested_at") or ""), _digest(files, paths))

    return _greater({s: _key(files) for s, files in sides.items()}), "the later refresh (table.ingested_at), then the greater digest"


def _conflict_key(record: dict) -> tuple:
    """A decision beats an open conflict; then the later last change
    (`resolved_at`, else `detected_at`)."""
    decided = (record.get("status") or "open") != "open"
    return (decided, str(record.get("resolved_at") or record.get("detected_at") or ""))


def _supersession_key(record: dict) -> tuple:
    """A human's assertion (`ingest supersede`) beats a detector's."""
    return (record.get("detected_by") == "manual",)


def _synthesis_key(record: dict) -> tuple:
    """The later synthesis (it read the later observation set)."""
    return (str(record.get("created_at") or ""),)


#: Per kind: a key over the parsed record; greater wins, then the greater
#: record fingerprint. A kind with no entry (lifecycle: its content is fixed
#: by its id) goes straight to the fingerprint.
_RECORD_RULES = {
    "conflicts": (_conflict_key, "a decision over open, then the later change"),
    "supersessions": (_supersession_key, "a manual assertion over a detected one"),
    "syntheses": (_synthesis_key, "the later synthesis"),
}


def _decide_record(unit, sides) -> tuple[str, str]:
    from artmind import curation_records

    present = [s for s, files in sides.items() if files]
    if len(present) == 1:
        return present[0], "the side that kept the record"
    kind = PurePosixPath(unit).parts[-2]
    key_fn, why = _RECORD_RULES.get(kind, (lambda record: (), "identical rule keys"))
    keys = {}
    for s, files in sides.items():
        data = files[unit]
        keys[s] = (key_fn(_json(data) or {}), curation_records.fingerprint(data))
    return _greater(keys), f"{why}, then the greater record fingerprint"


def _decide_file(unit, sides) -> tuple[str, str]:
    present = [s for s, files in sides.items() if files]
    if len(present) == 1:
        return present[0], "the side that kept the file"
    return (
        _greater({s: (hashlib.sha256(files[unit]).hexdigest(),) for s, files in sides.items()}),
        "the greater content hash",
    )


# ── plan and apply ───────────────────────────────────────────────────────────


def _reason_for_human(path: str) -> str:
    if path.startswith(f"{MARKER}/"):
        return "human-curated (same_as.yaml, schemas, table mappings, vault.yaml) -- resolve it by hand"
    return "your note -- resolve it in Obsidian"


def plan(vault_dir: Path) -> dict:
    """Every conflicted path, sorted into resolved units (with the side each
    takes and why), pending units, and reported paths. Reads only."""
    vault_dir = Path(vault_dir)
    ours, theirs = _merge_heads(vault_dir)
    conflicted = _unmerged(vault_dir)
    ids = _NoteIndex(vault_dir)

    units: dict[str, str] = {}
    reported = []
    for path in conflicted:
        if path.startswith(DATA_PREFIX):
            kind, unit = _unit_of(path)
            units.setdefault(unit, kind)
        else:
            reported.append({"path": path, "why": _reason_for_human(path)})

    resolved, pending = [], []
    for unit, kind in sorted(units.items()):
        paths = _unit_paths(vault_dir, kind, unit, (ours, theirs))
        sides = {"ours": _side_files(vault_dir, ours, paths), "theirs": _side_files(vault_dir, theirs, paths)}
        if kind == "kg":
            side, why = _decide_kg(vault_dir, unit, paths, sides, ids)
        elif kind == "table":
            side, why = _decide_table(unit, paths, sides)
        elif kind == "record":
            side, why = _decide_record(unit, sides)
        else:
            side, why = _decide_file(unit, sides)
        entry = {"unit": unit, "kind": kind, "paths": paths}
        if side is None:
            pending.append({**entry, "why": why})
        else:
            resolved.append({**entry, "side": side, "rule": why})
    return {
        "head": ours, "merge_head": theirs,
        "resolved": resolved, "pending": pending, "reported": reported,
        "remaining": len(pending) + len(reported),
    }


def _assert_scoped(paths: list[str]) -> None:
    """The write guard: every path is under `.artmind/data/`, relative, with
    no `..` -- checked before any git write, whatever the planner produced."""
    for path in paths:
        parts = PurePosixPath(path).parts
        if not path.startswith(DATA_PREFIX) or PurePosixPath(path).is_absolute() or ".." in parts:
            raise ResolveError(f"refusing to write {path!r}: `vault resolve` only writes under {DATA_PREFIX}")


def _batches(paths: list[str]):
    for i in range(0, len(paths), _BATCH):
        yield paths[i:i + _BATCH]


def _prune_empty_dirs(vault_dir: Path, removed: list[str]) -> None:
    """Remove the directories deleting `removed` emptied, up to (never
    including) `.artmind/data/` -- a folder the chosen side does not have
    leaves no empty shell for a folder scan to trip over."""
    stop = vault_dir / DATA_PREFIX
    for directory in sorted({(vault_dir / p).parent for p in removed}, key=lambda d: len(d.parts), reverse=True):
        while directory != stop and stop in directory.parents:
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent


def _stage_side(vault_dir: Path, rev: str, present: list[str], absent: list[str]) -> None:
    """Make the index and working tree hold `rev`'s version of the unit:
    check out the files it has, delete the ones it lacks, stage the lot."""
    _assert_scoped(present + absent)
    for batch in _batches(present):
        rc, out, err = run_command(["git", "checkout", rev, "--", *batch], cwd=vault_dir, extra_env=_LITERAL)
        if rc != 0:
            raise ResolveError(f"git checkout {rev[:12]} failed: {(err or out).strip()}")
    tracked = set()
    if absent:
        listed = _read(vault_dir, ["ls-files", "-z", "--", *absent])
        tracked = {p for p in listed.split("\0") if p}
    for path in absent:
        (vault_dir / path).unlink(missing_ok=True)
    _prune_empty_dirs(vault_dir, absent)
    to_add = present + [p for p in absent if p in tracked]
    for batch in _batches(to_add):
        rc, out, err = run_command(["git", "add", "--", *batch], cwd=vault_dir, extra_env=_LITERAL)
        if rc != 0:
            raise ResolveError(f"git add failed: {(err or out).strip()}")


def resolve(vault_dir: Path, *, dry_run: bool = False) -> dict:
    """Plan, then (unless `dry_run`) stage the chosen side of every resolved
    unit. Never commits."""
    vault_dir = Path(vault_dir)
    report = plan(vault_dir)
    report["dry_run"] = dry_run
    if dry_run:
        return report
    heads = {"ours": report["head"], "theirs": report["merge_head"]}
    for unit in report["resolved"]:
        _assert_scoped(unit["paths"])
    for unit in report["resolved"]:
        if _merge_heads(vault_dir) != (report["head"], report["merge_head"]):
            raise ResolveError("the merge changed underneath `vault resolve` -- re-run it")
        rev = heads[unit["side"]]
        have = set(_side_files(vault_dir, rev, unit["paths"]))
        present = [p for p in unit["paths"] if p in have]
        absent = [p for p in unit["paths"] if p not in have]
        _stage_side(vault_dir, rev, present, absent)
    return report
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_vault_resolve.py test/test_no_git_writes.py -q`
Expected: all pass (`test_no_git_write_calls[artmind/vault_resolve.py]` included: its `checkout`/`add` are the allowance).

Run: `uv run --group dev pytest test/ -q`
Expected: `2612 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_resolve.py test/test_vault_resolve.py test/test_no_git_writes.py
git commit -m "feat(vault): resolve picks one whole side per generated unit, by content, and stages it" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: `artmind vault resolve` on the CLI; `vault sync` and `vault status` point at it

**Files:**
- Modify: `artmind/cli.py` (`vault resolve`, the `vault` group docstring, `vault sync`'s docstring, `vault status`'s conflicts line)
- Modify: `artmind/vault_sync.py` (the preflight refusal)
- Test: `test/test_vault_resolve.py`

- [ ] **Step 1: Write the failing test**

**Append to** `test/test_vault_resolve.py`:

```python


def test_the_cli_reports_json(repo, monkeypatch):
    from artmind.cli import cli

    base = {NOTE: _note("Body v1.\n"), **_doc_folder(obs="[0]")}
    _conflict(repo, base, _doc_folder(obs="[1]"), _doc_folder(obs="[2]"))
    monkeypatch.setattr("artmind.vault.resolve_vault", lambda explicit=None: repo)

    result = CliRunner().invoke(cli, ["vault", "resolve", "--dryRun", "--compact"])

    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert (out["dry_run"], [u["unit"] for u in out["resolved"]]) == (True, [KG])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_vault_resolve.py -q`
Expected: `1 failed, 15 passed` — `test_the_cli_reports_json`: `No such command 'resolve'`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/cli.py` (lines 3583-3586):

```python
@cli.group("vault")
def vault():
    """Which vault is active and how far behind it each store is (`status`), git-diff-driven sync into Neo4j/the structured store (`sync`), read-only readiness checks for Obsidian Git (`doctor`), and the one-off move of per-ingest fields out of notes (`migrate-frontmatter`)."""
    pass
```

**with:**

```python
@cli.group("vault")
def vault():
    """Which vault is active and how far behind it each store is (`status`), git-diff-driven sync into Neo4j/the structured store (`sync`), settling merge conflicts in artmind's generated files (`resolve`), read-only readiness checks for Obsidian Git (`doctor`), and the one-off move of per-ingest fields out of notes (`migrate-frontmatter`)."""
    pass
```

**Replace in** `artmind/cli.py` (lines 3712-3714):

```python
@vault.command("doctor")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_doctor_cmd(compact):
```

**with:**

```python
@vault.command("resolve")
@click.option("--dryRun", "dry_run", is_flag=True, help="Show the side chosen for each folder, table and record, and why, writing nothing.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_resolve_cmd(dry_run, compact):
    """Settle merge conflicts in artmind's generated files (.artmind/data/**), deterministically, and stage the result.

    Run it while Obsidian Git's merge is stopped on conflicts. For each
    conflicted unit it picks ONE whole side: a KG document folder (all its
    files together) takes the side extracted from the merged note's body,
    else the greater fingerprint; a structured table (CSV + .meta.json
    together) the later refresh; a curation record a per-kind rule (a
    conflict: a decision beats open, then the later change). Every rule reads
    content only, so two machines resolving the same conflict pick the same
    bytes. Your notes and human-curated files (same_as.yaml, schemas,
    mappings) are listed under `reported` and never touched; a folder whose
    note is still conflicted is `pending` until you resolve the note.

    The one git write artmind makes: `git checkout` of the chosen side and
    `git add`, only for paths under .artmind/data/, only while a merge is in
    progress. It never commits -- Obsidian Git (or you) concludes the merge.
    """
    _setup_logger()
    from artmind import vault as vault_mod
    from artmind.vault_resolve import ResolveError, resolve

    try:
        vault_dir = vault_mod.resolve_vault()
    except vault_mod.VaultError as e:
        raise click.ClickException(str(e))
    if vault_dir is None:
        raise click.ClickException(
            "Not inside an artmind vault.\n"
            "  cd into one, or run `artmind init` to make this directory a vault."
        )
    try:
        result = resolve(vault_dir, dry_run=dry_run)
    except ResolveError as e:
        raise click.ClickException(str(e))
    _echo_json(result, compact)


@vault.command("doctor")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_doctor_cmd(compact):
```

**Replace in** `artmind/cli.py` (line 3576):

```python
        click.echo(f"Conflicts: {len(conflicts)} unresolved under .artmind/ ({shown})")
```

**with:**

```python
        click.echo(f"Conflicts: {len(conflicts)} unresolved under .artmind/ ({shown}) — `artmind vault resolve`")
```

**Replace in** `artmind/cli.py` (lines 3678-3681):

```python
    Applies committed content only (never the working tree) and never
    commits. Refuses while a merge/rebase is in progress, while files under
    .artmind/ have unresolved conflicts, while the ingest worker is running,
    or while a bookmark is not in HEAD's history (pull first).
```

**with:**

```python
    Applies committed content only (never the working tree) and never
    commits. Refuses while a merge/rebase is in progress, while files under
    .artmind/ have unresolved conflicts (`vault resolve` settles artmind's
    generated ones), while the ingest worker is running, or while a bookmark
    is not in HEAD's history (pull first).
```

**Replace in** `artmind/vault_sync.py` (lines 120-121):

```python
            f"{', ...' if len(unmerged) > 5 else ''}) -- resolve them first"
        )
```

**with:**

```python
            f"{', ...' if len(unmerged) > 5 else ''}) -- run `artmind vault resolve` for artmind's "
            "generated files under .artmind/data/, resolve the rest by hand"
        )
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_vault_resolve.py test/test_vault_sync.py test/test_vault_cli.py test/test_cli_guide.py -q`
Expected: all pass (`vault` is routed as a group in `COMMAND_GROUPS`, so the new subcommand needs no routing change).

Run: `uv run --group dev pytest test/ -q`
Expected: `2613 passed, 14 skipped`.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py artmind/vault_sync.py test/test_vault_resolve.py
git commit -m "feat(cli): vault resolve; vault sync and vault status point at it" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: docs, skills, spec amendment, justfile; full suite and CLI help

Prose only. The spec's D1/D2 describe the one exception precisely (and `§3`, `§10`), and a new §16 records A11–A14 (decisions 1–13). `docs/vault.md` gets "Merge conflicts in artmind's files: `vault resolve`" and "Frontmatter: identity only", and drops "keep either side" for colliding records. `docs/document-identity.md`'s versioning and frontmatter-contract sections move the baseline to `document.json`. The ingestion-helper skill describes the identity-only stamp and the migration, and gains a Common Gotchas row for the preflight refusal; the curate skill says how colliding curation records settle. The `justfile` wraps both new commands.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`, `docs/vault.md`, `docs/document-identity.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md`, `artmind/skills/artmind-curate/SKILL.md`, `justfile`

- [ ] **Step 1: The spec**

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (lines 47-48):

````markdown
| D1 | **Obsidian Git owns all git transport and all commits.** artmind never runs `git add/commit/rm/push/pull/merge` against the vault. |
| D2 | **artmind only reads git** (`rev-parse`, `diff`, `show`, `cat-file`, `status`). No write, not even a private ref — see §6 A2's amendment (2026-09-27 vault-sync-completion): a considered `update-ref` pin was dropped as unnecessary and against this decision's intent. |
````

**with:**

````markdown
| D1 | **Obsidian Git owns all git transport and all commits.** artmind never runs `git add/commit/rm/push/pull/merge` against the vault — with one exception, `artmind vault resolve` (R7, §16 A11): it runs `git checkout <HEAD\|MERGE_HEAD> -- <paths>` and `git add -- <paths>`, and nothing else, only for paths under `.artmind/data/**`, only while a merge is in progress, and never commits — Obsidian Git (or the user) concludes the merge. |
| D2 | **artmind only reads git** (`rev-parse`, `diff`, `show`, `cat-file`, `status`, `ls-tree`, `ls-files`), apart from D1's single exception: `vault resolve`'s staging of a generated file's chosen side, which writes the index and working tree for those paths and never history or a ref. No other write, not even a private ref — see §6 A2's amendment (2026-09-27 vault-sync-completion): a considered `update-ref` pin was dropped as unnecessary and against this decision's intent. `test/test_no_git_writes.py` enforces both, with the exception scoped to `artmind/vault_resolve.py` and those two subcommands. |
````

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (line 59):

````markdown
| **artmind — readiness** (write side) | writing files in a form that is always safe to auto-commit and merge: atomic staging folders, `.gitignore`/`.gitattributes`, per-table manifests, minimal frontmatter, `vault resolve` for generated-file conflicts, `vault doctor` | any git write |
````

**with:**

````markdown
| **artmind — readiness** (write side) | writing files in a form that is always safe to auto-commit and merge: atomic staging folders, `.gitignore`/`.gitattributes`, per-table manifests, minimal frontmatter, `vault resolve` for generated-file conflicts, `vault doctor` | any git write but `vault resolve`'s scoped staging (D1) |
````

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (line 371):

````markdown
| `artmind vault resolve` | new (R7); `--dryRun` shows the chosen side per folder/table |
````

**with:**

````markdown
| `artmind vault resolve` | new (R7, §16 A11); `--dryRun` shows the chosen side per folder/table/record |
| `artmind vault migrate-frontmatter` | new (R6, §16 A12–A13); `--dryRun` lists the notes it would rewrite |
````

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (lines 548-552):

````markdown
Still machine-local, by design (§14 A6): same-as proposals (`sameas propose`/`reject`,
`ingest refine-graph`, `detect-conflicts`' same-entity verdicts) -- a review queue whose approved
outcome travels as `same_as.yaml`. `query graph text2cypher` cannot write at all: it runs through
`graph_query.read_session()`, which the server enforces as read-only.

````

**with:**

````markdown
Still machine-local, by design (§14 A6): same-as proposals (`sameas propose`/`reject`,
`ingest refine-graph`, `detect-conflicts`' same-entity verdicts) -- a review queue whose approved
outcome travels as `same_as.yaml`. `query graph text2cypher` cannot write at all: it runs through
`graph_query.read_session()`, which the server enforces as read-only.

## 16. Amendments (2026-09-29, phase 5 -- conflicts and frontmatter)

**A11. `vault resolve`: one whole side per unit, by content (amends R7, D1, D2).** It runs only
while a merge (not a rebase, cherry-pick or octopus merge) is in progress, and sorts every
conflicted path:

- a **KG document folder** takes the side whose `document.json` `content_sha256` equals the
  sha of the merged note's body (the note found by `document.json`'s `source_path`, else by
  `_artmind_id`); if the merge deleted the note, the side without the folder; otherwise the
  greater staging fingerprint (`sync_state.staging_fingerprint`). A folder whose note still
  holds conflict markers is **pending** until the user resolves the note -- the working tree
  counts, so a note resolved in Obsidian but not yet staged is resolved;
- a **structured table** (CSV + `.meta.json`) takes the side that kept the table, then the later
  `table.ingested_at` (the meta has no `refreshed_at`; `ingested_at` is the table's last
  refresh), then the greater digest of the two files;
- a **curation record** (§15 A9/A10) takes the side that kept it over one that deleted it, then
  a per-kind rule, then the greater record fingerprint: *conflicts* -- a decision (`resolved`,
  `dismissed`) beats `open`, then the later change (`resolved_at`, else `detected_at`);
  *supersessions* -- a `manual` assertion beats a detected one; *syntheses* -- the later
  `created_at`; *lifecycle* -- the fingerprint alone (its content is fixed by its id). This
  settles two machines detecting the same conflict before either pulled;
- **any other file under `.artmind/data/`** (a converted binary's markdown, a legacy
  `manifest.json`) takes the side that kept it, then the greater content hash;
- **human notes and human-curated files** (`same_as.yaml`, schemas, table mappings,
  `vault.yaml`) are reported and never touched.

Every rule reads the two sides' committed content and the merged note, never which side is
"ours", so two machines resolving the same conflict -- A merging B, B merging A -- choose the
same bytes, and their merge commits then merge cleanly. The whole side is taken: files the other
side changed without conflict, and files only the other side has (a chunk cache), are replaced or
removed with it. The write is `git checkout <HEAD|MERGE_HEAD> -- <paths>` (a plain
`--ours`/`--theirs` would leave the unit's non-conflicted files as merged), deleting what the
chosen side lacks, then `git add -- <paths>`, with literal pathspecs; every path is checked to be
under `.artmind/data/` first. `vault sync`'s preflight refusal names `vault resolve`.

**A12. Minimal frontmatter (amends R6).** A human note carries `_artmind_id` and `_domain`,
written line by line (`artmind/frontmatter.py`: body, the user's keys, their order, comments,
quoting and line endings untouched), at the first stamp, a heal or fork, and a genuine `_domain`
change -- never otherwise. The per-ingest fields -- `version`, `content_sha256`, `source_sha256`,
`source_commit`, `ingested_at`, `source_path`, `source_type` -- are recorded in the staging
folder's `document.json` by the **extraction**, not at ingest, so a version whose extraction
failed is never recorded as done (the fat frontmatter recorded the new hash before extracting,
and a failed extraction then read as unchanged forever). `_status` is dropped, not moved: it
was always `latest` and nothing read it. `_valid_from`/`_valid_to`/`_valid_time_source` are a
human's input and stay. artmind no longer seeds `title`/`created_on`/`declared_version` into a
note; the Document's `title` defaults to the file stem at extraction. A converted binary's
markdown follows the same contract. The versioning baseline is `document.json` once it records
a `content_sha256`, the note's own legacy fields otherwise (`document_identity.ingest_baseline`),
found by the note's name, its name before a move, or -- for `adopt`/`heal` -- its id. `docs
reindex`, archive and the extract-kg retry read the same baseline. The new `document.json` keys
become `:Document` properties, like every other key there.

**A13. `vault migrate-frontmatter`.** Copies each moved field into `document.json` only where
that key is absent, and the four that describe one ingest (`content_sha256`, `source_sha256`,
`source_commit`, `ingested_at`) only when the note's `_version` equals the staged `version`;
then strips the note. Idempotent, resumable, `--dryRun`; refuses during a merge/rebase or while
the worker runs; prints the instruction to pause Obsidian Git's auto-commit first. A note with no
staging folder keeps its fields (they still work) and is reported. It writes no git and no
graph: every migrated folder's fingerprint changes, so each machine's next `vault sync` replays
those documents once, without LLM cost.

**A14. `.gitignore` block v4 (amends R4).** Scratch **files** are ignored too
(`.artmind/data/**/*.artmind-tmp`, `*.artmind-old`): a curation record's temp file (§15 A9)
and a note rewrite's temp file are files, which v3's directory rules missed. `vault doctor`
names both block versions for a vault on an older block.
````

- [ ] **Step 2: `docs/vault.md`**

**Replace in** `docs/vault.md` (line 127):

````markdown
| `*.artmind-tmp/`, `*.artmind-old/` under `.artmind/data/` | scratch from the atomic staging-folder swap; a crash can leave one, the next write removes it |
````

**with:**

````markdown
| `*.artmind-tmp`, `*.artmind-old` (folders and files) under `.artmind/data/` | scratch from an atomic write — a staging folder's swap, a curation record's temp file, a note rewrite's temp file; a crash can leave one, the next write removes it. Folders only before block v4: `artmind init` upgrades the block, and `vault doctor` prints the `git rm --cached` for a scratch file that got committed |
````

**Replace in** `docs/vault.md` (lines 333-337):

````markdown
artmind never commits, pulls or pushes the vault. It writes files — note
frontmatter, KG staging under `.artmind/data/kg/`, structured text under
`.artmind/data/structured_text/` — and the Obsidian Git plugin commits and
syncs them like any other change. One writer per repo means no `index.lock`
races and no rejected pushes.
````

**with:**

````markdown
artmind never commits, pulls or pushes the vault. It writes files — a
note's identity (once; see "Frontmatter: identity only" below), KG staging
under `.artmind/data/kg/`, structured text under
`.artmind/data/structured_text/` — and the Obsidian Git plugin commits and
syncs them like any other change. One writer per repo means no `index.lock`
races and no rejected pushes. The single exception is `artmind vault
resolve`, which stages its pick for a conflicted generated file during a
merge (below) and still never commits.
````

**Replace in** `docs/vault.md` (lines 379-381):

````markdown
`vault sync` refuses while a merge, rebase or cherry-pick is in progress,
while a file under `.artmind/` has unresolved conflicts (a conflicted note of
yours does not block it), while the ingest worker is running, or while a
````

**with:**

````markdown
`vault sync` refuses while a merge, rebase or cherry-pick is in progress,
while a file under `.artmind/` has unresolved conflicts (a conflicted note of
yours does not block it; `artmind vault resolve` settles those under
`.artmind/data/`), while the ingest worker is running, or while a
````

**Replace in** `docs/vault.md` (lines 410-412):

````markdown
resolved conflict is not reopened). If two machines both detect one before
either pulls, git reports a conflict on that one file — keep either side
(`git checkout --ours` or `--theirs` on it), it is the same conflict.
````

**with:**

````markdown
resolved conflict is not reopened). If two machines both detect one before
either pulls, git reports a conflict on that one file, and `artmind vault
resolve` settles it: a decision (resolved, dismissed) beats open, then the
later change wins — the same pick on both machines.
````

**Replace in** `docs/vault.md` (lines 467-470):

````markdown
artmind never writes to this vault's git — no commit, no ref, nothing. Obsidian
Git merges (never rebases), so a bookmarked commit stays reachable on its
own; if history is ever rewritten anyway, the next section covers what
`vault sync` does about it.
````

**with:**

````markdown
artmind never writes to this vault's git — no commit, no ref — apart from
`vault resolve` staging a conflicted generated file's chosen side. Obsidian
Git merges (never rebases), so a bookmarked commit stays reachable on its
own; if history is ever rewritten anyway, the next section covers what
`vault sync` does about it.
````

**Replace in** `docs/vault.md` (lines 510-515):

````markdown
artmind never runs these itself.

The loop terminates by construction: artmind writes frontmatter → Obsidian Git commits
it → the cursor sees a change → but `compute_content_sha256` hashes the **body
only**, so the version decision is `metadata_only`, minting no observations at no
LLM cost.
````

**with:**

````markdown
artmind never runs these itself. A vault initialised by an older artmind
shows its `.gitignore` block as outdated, naming both versions (for example
`v3; this artmind writes v4`); `artmind init` upgrades it in place.

#### Merge conflicts in artmind's files: `vault resolve`

Everything under `.artmind/data/` merges as a whole file, so two machines
that regenerated the same file stop the merge on a conflict. While Obsidian
Git's merge is stopped, run:

```bash
artmind vault resolve --dryRun   # which side each folder, table and record takes, and why
artmind vault resolve            # stage those sides; then let Obsidian Git commit the merge
```

It takes **one whole side** per unit, never a mix:

| Unit | Side taken |
|---|---|
| a KG document folder (`kg/<domain>/<note>/`, every file in it) | the one extracted from the merged note's body; if the merge deleted the note, the side without the folder; otherwise the greater fingerprint |
| a structured table (`<table>.csv` + `.meta.json`) | the side that kept it, then the later refresh, then the greater digest |
| a curation record (`curation/<kind>/<id>.json`) | the side that kept it, then — a conflict: a decision beats open, then the later change; a supersession: a manual assertion beats a detected one; a synthesis: the later one — then the greater fingerprint |
| any other file under `.artmind/data/` | the side that kept it, then the greater content hash |

Every rule reads content, never which side is "ours", so both machines pick
the same bytes. Your notes, `same_as.yaml`, schemas and table mappings are
listed under `reported` and never touched: resolve those in Obsidian. A
folder whose note is still conflicted is `pending`; resolve the note, then
run `vault resolve` again. This is the one place artmind writes to git — `git
checkout` of the chosen side and `git add`, only under `.artmind/data/`, only
during a merge, never a commit. A side resolved away costs a re-extraction at
most, never knowledge: the note is yours and always merges by hand.

#### Frontmatter: identity only

artmind writes two fields into a note — `_artmind_id` and `_domain` — once,
at its first ingest, and again only if you change its domain. It edits those
lines alone: the body, your own keys and their order, comments and line
endings are untouched. Everything that changes per ingest (the version, the
body hash, the source commit, when and from where it was ingested) lives in
the note's staging folder, `.artmind/data/kg/<domain>/<note>/document.json`,
written by the extraction. So re-ingesting a note writes nothing to it,
Obsidian Git has nothing of artmind's to commit, and two machines never
conflict on artmind's lines in your notes.

A vault ingested by older artmind still carries those fields in every note;
they keep working. To move them out, once:

```bash
# pause Obsidian Git's auto commit-and-sync first (the command reminds you)
artmind vault migrate-frontmatter --dryRun
artmind vault migrate-frontmatter
# commit the result as one change, then turn auto commit-and-sync back on
```

It copies each field into the note's `document.json` where that key is
missing (a newer value there always wins), then rewrites the note without
them. It is idempotent — re-run it after an interruption — and refuses
during a merge or while the ingest worker runs. A note never extracted has
no staging folder; it keeps its fields and is listed under `skipped`. Each
migrated folder's fingerprint changes, so every machine's next `vault sync`
replays those documents once (no LLM cost).

The loop terminates by construction: artmind stamps a note's identity once →
Obsidian Git commits it → the cursor sees a change → but
`compute_content_sha256` hashes the **body only**, so the version decision is
`metadata_only`, minting no observations at no LLM cost, and writing nothing.
````

- [ ] **Step 3: `docs/document-identity.md`**

**Replace in** `docs/document-identity.md` (line 39):

````markdown
| **present** | id known, **path matches** | **re-ingest** — bump `_version` only if the body hash changed |
````

**with:**

````markdown
| **present** | id known, **path matches** | **re-ingest** — bump the version only if the body hash changed |
````

**Replace in** `docs/document-identity.md` (lines 62-83):

````markdown
## Versioning

**`_content_sha256` hashes the body only, with frontmatter excluded.** Otherwise
artmind writing `_version: 2` changes the file's bytes, which changes the hash,
which triggers version 3, which rewrites frontmatter — forever. There is precedent:
`delta._compute_body_block_hashes` already parses frontmatter off before hashing.

| Change | Result |
|---|---|
| body differs from `_content_sha256` | `_version` + 1; prior chunks and observations → `_status = history` |
| only frontmatter differs | **metadata fast path** — no version, no chunking, no extraction, no observations |
| nothing differs | no-op |

`_version` is an **integer counting content states**, not re-ingests. It is
system-owned; a document's own `| Version | 2.1 |` header lifts to
**`declared_version`**, a string with no system meaning. Conflating these is why
63 of 64 documents currently carry a *string* `version` and why
`int(rec.get("version") or 1) + 1` raises on re-ingest.

`_source_commit` records the vault's git sha at ingest — provenance, not identity.
One commit can touch many documents and one document many commits, so it is a
pointer, never a version.
````

**with:**

````markdown
## Versioning

**The content hash covers the body only, with frontmatter excluded**, so stamping
a note's identity never looks like an edit. There is precedent:
`delta._compute_body_block_hashes` already parses frontmatter off before hashing.

**The baseline is the staging folder's `document.json`** (spec 2026-09-26 R6):
`version`, `content_sha256` and, for a converted binary, `source_sha256`, recorded
there by the extraction itself — so a version whose extraction failed is never
recorded as done, and the next ingest extracts it again. The folder is found by the
note's name, its name before a move, or (after a registry wipe) its id. A note from
before R6 whose `document.json` has no `content_sha256` versions from its own
legacy `_content_sha256`/`_version` instead (`document_identity.ingest_baseline`).

| Change | Result |
|---|---|
| body differs from the baseline's `content_sha256` | version + 1; prior chunks and observations → history |
| only frontmatter differs | **metadata fast path** — no version, no chunking, no extraction, no observations |
| nothing differs | no-op — and the note is not rewritten |

The version is an **integer counting content states**, not re-ingests. It is
system-owned; a document's own `| Version | 2.1 |` header lifts to
**`declared_version`**, a string with no system meaning. Conflating these is why
63 of 64 documents currently carry a *string* `version` and why
`int(rec.get("version") or 1) + 1` raises on re-ingest.

`source_commit` records the vault's git sha at extraction — provenance, not
identity. One commit can touch many documents and one document many commits, so it
is a pointer, never a version.
````

**Replace in** `docs/document-identity.md` (lines 92-108):

````markdown
## The frontmatter contract

**System** — artmind writes these; extraction must never emit them. The underscore
*is* the rule: an underscore means artmind owns it.

```
_artmind_id  _version  _content_sha256  _domain  _status
_valid_from  _valid_to  _valid_time_source
_source_commit  _source_path  _source_type  _ingested_at
```

**Authored** — artmind seeds, then leaves alone:

```
title (seeded from the filename stem)  project  area  tags
declared_version  created_on  modified_on
```
````

**with:**

````markdown
## The frontmatter contract

**Identity** — the only fields artmind writes into a note, line by line, leaving
every other byte alone: at the first ingest, and again only when `_domain`
genuinely changes (spec 2026-09-26 R6).

```
_artmind_id  _domain
```

**Per-ingest provenance** — lives in the staging folder's `document.json`, written
by the extraction. Older artmind wrote these into every note on every ingest, so
nearly every note conflict between machines had artmind's lines in it; `artmind
vault migrate-frontmatter` moves them out once, and until then they are read as a
fallback. `_status` is dropped rather than moved (it was always `latest`; a
retirement is a lifecycle record).

| Old frontmatter | `document.json` |
|---|---|
| `_version` | `version` |
| `_content_sha256` | `content_sha256` |
| `_source_sha256` | `source_sha256` |
| `_source_commit` | `source_commit` |
| `_ingested_at` | `ingested_at` |
| `_source_path` | `source_path` |
| `_source_type` | `source_type` |

**Valid time** — `_valid_from  _valid_to  _valid_time_source` are yours to write;
artmind reads them and never writes them. The underscore still marks them as
system-meaning: extraction must never emit any `_` field.

**Authored** — yours; artmind reads them into the graph and never writes them
(it no longer seeds them either — the Document's `title` defaults to the file
name at extraction):

```
title  project  area  tags  declared_version  created_on  modified_on
```
````

- [ ] **Step 4: The skills**

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (lines 44-53):

````markdown
**A vault-native markdown file gets its identity seeded on first ingest**:
`_artmind_id` (a uuid7), `_version`, `_content_sha256`, and the rest of the
system frontmatter block are written into the file itself; the Obsidian Git
plugin commits the change (artmind never commits). If a vault's git setup looks
wrong (pushes rejected, a merge rebasing, generated folders committed), run
`artmind vault doctor`: it prints the exact fix for each problem. Re-ingesting the same file later
bumps `_version` only if the body changed; editing only frontmatter (tags,
title) takes a metadata-only fast path with no new version and no
re-extraction. If `--domain` is omitted, a file's own `_domain` frontmatter
wins over anything passed on the command line.
````

**with:**

````markdown
**A vault-native markdown file gets its identity stamped on first ingest**:
`_artmind_id` (a uuid7) and `_domain` — and nothing else — are written into the
file's frontmatter, leaving its body and your own keys untouched; the Obsidian
Git plugin commits the change (artmind never commits). The version, body hash
and other per-ingest provenance live in the staging folder's `document.json`
(`.artmind/data/kg/<domain>/<note>/`), so re-ingesting never rewrites the note.
If a vault's git setup looks wrong (pushes rejected, a merge rebasing, generated
folders committed), run `artmind vault doctor`: it prints the exact fix for
each problem. Re-ingesting the same file later bumps the version only if the
body changed (an extraction that failed is retried at the same version);
editing only frontmatter (tags, title) takes a metadata-only fast path with no
new version and no re-extraction. If `--domain` is omitted, a file's own
`_domain` frontmatter wins over anything passed on the command line.

**Notes from an older artmind still carry `_version`, `_content_sha256`,
`_ingested_at` and friends.** They keep working. To move them into
`document.json` once, pause Obsidian Git's auto commit-and-sync, then run
`artmind vault migrate-frontmatter --dryRun` and `artmind vault
migrate-frontmatter` (idempotent; re-run after an interruption), and commit
the result as one change.
````

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (line 540):

````markdown
| `vault sync`: "no graph/structured bookmark recorded yet" | This store has never been synced on this graph/machine (or the snapshot predates bookmarks) | `vault sync --bootstrapSynced` if the store is known-current, else `--bootstrapEmpty`; add `--store graph`/`--store structured` to do one store only |
````

**with:**

````markdown
| `vault sync`: "unresolved conflicts in N file(s) under .artmind/" | Obsidian Git's merge stopped on a conflict in artmind's generated files — two machines regenerated the same document folder, table or curation record | `artmind vault resolve --dryRun` to see the side each takes, then `artmind vault resolve`; resolve any `reported` notes in Obsidian, let Obsidian Git commit the merge, then `vault sync` |
| `vault sync`: "no graph/structured bookmark recorded yet" | This store has never been synced on this graph/machine (or the snapshot predates bookmarks) | `vault sync --bootstrapSynced` if the store is known-current, else `--bootstrapEmpty`; add `--store graph`/`--store structured` to do one store only |
````

**Replace in** `artmind/skills/artmind-curate/SKILL.md` (lines 29-32):

````markdown
machine with its own Neo4j ends up with the same curation. Same-as
**proposals** (the review queue) do not travel: they stay on the machine
that ran `sameas propose` / `detect-conflicts` / `refine-graph`, and only an
approved group does.
````

**with:**

````markdown
machine with its own Neo4j ends up with the same curation. Same-as
**proposals** (the review queue) do not travel: they stay on the machine
that ran `sameas propose` / `detect-conflicts` / `refine-graph`, and only an
approved group does.

**When two machines wrote the same record before pulling** (both detected
one conflict, or one resolved it while the other re-detected it), Obsidian
Git's merge stops on that record file. `artmind vault resolve` settles it
the same way on every machine: a decision (resolved/dismissed) beats open,
then the later change; a manual supersession beats a detected one; the later
synthesis wins. It never touches `same_as.yaml` — a conflict there is
reported, and you merge the groups by hand.
````

- [ ] **Step 5: The justfile**

**Replace in** `justfile` (lines 150-151):

```make
vault-doctor:
    uv run artmind vault doctor
```

**with:**

```make
vault-doctor:
    uv run artmind vault doctor

# settle merge conflicts in artmind's generated files (.artmind/data/**): one whole side per folder/table/record, staged, never committed (usage: just vault-resolve [--dryRun])
vault-resolve *flags:
    uv run artmind vault resolve {{ flags }}

# once: move per-ingest fields out of every note's frontmatter into its document.json -- pause Obsidian Git's auto-commit first (usage: just vault-migrate-frontmatter [--dryRun])
vault-migrate-frontmatter *flags:
    uv run artmind vault migrate-frontmatter {{ flags }}
```

- [ ] **Step 6: Full suite and CLI help**

Run: `uv run --group dev pytest test/ -q`
Expected: `2613 passed, 14 skipped`.

Run: `just dev-cli-help | grep -A12 "artmind vault"` and `uv run artmind vault resolve --help`, `uv run artmind vault migrate-frontmatter --help`
Expected: the `vault` group lists `doctor`, `migrate-frontmatter`, `resolve`, `status`, `sync`; each new command shows its docstring and its `--dryRun`/`--compact` options.

Run: `just --list | grep vault-`
Expected: `vault-doctor`, `vault-migrate-frontmatter *flags`, `vault-resolve *flags`.

- [ ] **Step 7: Push the skills to the run folder (the chat UI reads the copy there)**

The skill edits reach the chat UI agent only through `artmind init` (CLAUDE.md, "Skills reach the chat UI only through the run folder"). That is the user's step after merging — see "Live verification (user)" step 0 — not the implementer's.

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/specs/2026-09-26-vault-git-transport-design.md docs/vault.md docs/document-identity.md artmind/skills/artmind-ingestion-helper/SKILL.md artmind/skills/artmind-curate/SKILL.md justfile
git commit -m "docs(vault-sync): vault resolve, identity-only frontmatter, migrate-frontmatter -- vault.md, document-identity.md, skills, spec §16" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Live verification (user)

Hermetic tests cannot see Obsidian Git, a real LLM extraction or a real Neo4j (CLAUDE.md §3). Run these after this plan is merged and installed, in throwaway vaults, against a **local** Neo4j. Everything runs in-process (`ARTMIND_NO_PROXY=1`), so a stale `serve` daemon cannot answer with old code. `ingest sync` and `ingest extract-kg` call your LLM; nothing else here does.

Two machines, each with **its own** Neo4j database: in Neo4j Browser, `:use system`, `CREATE DATABASE planda;` and `CREATE DATABASE plandb;` (Desktop/Enterprise). On Community edition run two throwaway servers instead: `docker run -d --name pland-a -p 7688:7687 -e NEO4J_AUTH=neo4j/plandpass neo4j:5` and `docker run -d --name pland-b -p 7689:7687 -e NEO4J_AUTH=neo4j/plandpass neo4j:5`, with URIs `neo4j://127.0.0.1:7688` / `:7689` and database `neo4j`.

Obsidian Git is simulated with plain git: `git add -A && git commit -qm MSG && git pull -q --no-rebase origin main && git push -q origin HEAD:main`.

### 0. Install and set up two clones

```bash
cd ~/projects/artmind9 && just dev-install          # also runs `artmind init` for the run folder: skills reach the chat UI
export ARTMIND_NO_PROXY=1
export LIVE=$HOME/pland-live && rm -rf "$LIVE" && mkdir -p "$LIVE" && cd "$LIVE"
git init -q --bare --initial-branch=main remote.git
git clone -q remote.git A && cd "$LIVE/A" && git config user.email a@example.com && git config user.name A
artmind init && $EDITOR .artmind/config.env         # point at planda
artmind setup
grep -n "artmind (v4)\|artmind-tmp$\|artmind-old$" .gitignore   # the v4 header and the two scratch FILE rules
mkdir -p notes
printf -- '---\r\ntitle: Policy\r\ntags: [risk, ops]  # mine\r\n---\r\n\r\n# Policy\r\n\r\nAcme Bank caps overdrafts at 500 EUR.\r\n' > notes/policy.md
```

### 1. A note carries identity only; `document.json` carries the rest

```bash
cd "$LIVE/A"
artmind ingest sync notes/policy.md --domain banking.organization
head -c 200 notes/policy.md | od -c | head -8     # ---\r\n_artmind_id: ...\r\n_domain: banking.organization\r\ntitle: Policy ... -- CRLF kept, your keys after
python3 -c "import json;d=json.load(open('.artmind/data/kg/banking.organization/policy/document.json'));print({k:d.get(k) for k in ('version','content_sha256','source_commit','ingested_at','source_path','source_type','title')})"
git add -A && git commit -qm "A: policy" && git push -q origin HEAD:main
artmind ingest sync notes/policy.md --domain banking.organization   # unchanged: metadata_only
git status --short notes/                          # nothing -- the note was not rewritten
printf 'Overdrafts above 500 EUR need a manager.\r\n' >> notes/policy.md
artmind ingest sync notes/policy.md --domain banking.organization   # content: version 2
git diff --stat notes/                             # only your added line; no artmind line changed
python3 -c "import json;print(json.load(open('.artmind/data/kg/banking.organization/policy/document.json'))['version'])"   # 2
git add -A && git commit -qm "A: policy v2" && git push -q origin HEAD:main
artmind vault sync --bootstrapSynced --compact
cd "$LIVE" && git clone -q remote.git B && cd "$LIVE/B" && git config user.email b@example.com && git config user.name B
artmind init && $EDITOR .artmind/config.env         # point at plandb
artmind setup && artmind vault sync --bootstrapEmpty --compact
```

### 2. Two machines re-extract the same note: `vault resolve` converges

Both machines re-extract version 2 (the LLM output differs), commit, and B keeps its pre-merge commit on a side branch so A can merge it too.

```bash
cd "$LIVE/A" && artmind ingest extract-kg policy.md --domain banking.organization && git add -A && git commit -qm "A: re-extract" && git push -q origin HEAD:main
cd "$LIVE/B" && artmind ingest extract-kg policy.md --domain banking.organization && git add -A && git commit -qm "B: re-extract"
git push -q origin HEAD:refs/heads/b-side           # B's commit, before any merge
git pull -q --no-rebase origin main                 # CONFLICT in .artmind/data/kg/banking.organization/policy/*
artmind vault sync                                  # refused: "... run `artmind vault resolve` ..."
artmind vault status                                # Conflicts: N unresolved under .artmind/ (...) — `artmind vault resolve`
artmind vault resolve --dryRun --compact            # one unit, kind "kg", side + rule "the greater staging fingerprint (both or neither match the note)"
artmind vault resolve --compact
git diff --name-only --diff-filter=U                # nothing
git log -1 --format=%s                              # still "B: re-extract" -- resolve made no commit
git commit -qm "B: merge" && git rev-parse HEAD:.artmind/data/kg/banking.organization/policy   # note this tree id (T_B)
cd "$LIVE/A" && git fetch -q origin b-side && git merge --no-edit FETCH_HEAD   # the same conflict, the other direction
artmind vault resolve --compact && git commit -qm "A: merge"
git rev-parse HEAD:.artmind/data/kg/banking.organization/policy                # equals T_B
cd "$LIVE/B" && git push -q origin HEAD:main && cd "$LIVE/A" && git pull -q --no-rebase origin main   # merges cleanly: no conflict
artmind vault sync --compact                        # replays the resolved folder (its fingerprint differs from A's graph)
```

Then a conflict in your note: both machines edit the note's body differently and re-ingest.

```bash
cd "$LIVE/A" && printf 'A says 600 EUR.\r\n' >> notes/policy.md && artmind ingest sync notes/policy.md --domain banking.organization && git add -A && git commit -qm "A: 600" && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main
printf 'B says 700 EUR.\r\n' >> notes/policy.md && artmind ingest sync notes/policy.md --domain banking.organization && git add -A && git commit -qm "B: 700"
git pull -q --no-rebase origin main                 # CONFLICT in notes/policy.md AND in the staging folder
artmind vault resolve --compact                     # notes/policy.md under "reported"; the folder under "pending": resolve the note first
$EDITOR notes/policy.md                             # keep A's line only, remove the markers, save (do not stage)
artmind vault resolve --compact                     # the folder: side "theirs" (A's), rule "the side extracted from the merged note's body"
                                                    # (if your editor changed the line endings, the body matches neither side: rule "the greater staging fingerprint")
git add notes/policy.md && git commit -qm "B: merge" && git push -q origin HEAD:main
```

A curation record (optional — needs your LLM to find a conflict between two notes; see Plan C's live step 2 for a pair that does): run `artmind ingest detect-conflicts --domain D1 --domain D2 --compact` on **both** machines before either pulls, and `artmind ingest resolve-conflict <id> --status resolved --reason test` on A only. Commit on both, push A, pull on B: the conflict on `.artmind/data/curation/conflicts/<id>.json` resolves to A's `resolved` record (`vault resolve --dryRun` shows rule "a decision over open, then the later change, then the greater record fingerprint"), and the same on A if it merges B's side branch.

### 3. `vault migrate-frontmatter` on a copy of a real vault

Use a **clone** of an existing vault (not `cp -R`: a clone carries no `config.env`, so nothing can reach your real graph), ingested by an older artmind:

```bash
git clone -q ~/MyVault "$LIVE/copy" && cd "$LIVE/copy"
artmind init && $EDITOR .artmind/config.env         # point at planda (a throwaway graph)
artmind vault doctor                                # ".gitignore artmind block": outdated (v3; this artmind writes v4) -- init already upgraded it; re-run shows ok
grep -rl "^_ingested_at:" --include=*.md . | wc -l   # notes still carrying fat frontmatter
artmind vault migrate-frontmatter --dryRun --compact | python3 -c "import json,sys;r=json.load(sys.stdin);print(len(r['migrated']),r['already_minimal'],len(r['skipped']),len(r['version_mismatch']))"
artmind vault migrate-frontmatter --compact > /tmp/migrate.json   # the pause notice is on stderr
git diff --stat | tail -1                           # notes + document.json files
git diff -U0 -- '*.md' | grep '^[-+]' | grep -v '^[-+][-+]' | grep -v '^-_' | head   # nothing: only removed `_...` lines
git diff -- '*.md' | grep '^+' | grep -v '^+++' | head   # nothing added to any note
artmind vault migrate-frontmatter --compact | python3 -c "import json,sys;print(json.load(sys.stdin)['migrated'])"   # [] -- idempotent
python3 -c "import json;[print(s) for s in json.load(open('/tmp/migrate.json'))['skipped'][:5]]"   # never-extracted notes keep their fields
artmind ingest sync <a migrated note> --domain <its domain>   # tier metadata_only, same version; `git status --short` shows no change to it
```

Interrupt a second copy mid-run (Ctrl-C during `vault migrate-frontmatter` on a large vault), then run it again: it finishes, and `git diff` looks the same as an uninterrupted run.

### Clean up

```bash
rm -rf "$LIVE"
```

Then `DROP DATABASE planda; DROP DATABASE plandb;` (from `:use system`) or `docker rm -f pland-a pland-b`.

---

## Self-review

### Scope → tasks

| Scope item | Task(s) |
|---|---|
| R7: resolves only under `.artmind/data/**`, refuses anything else | 7 (`_assert_scoped`, `reported`), `test_the_write_guard_refuses_any_path_outside_artmind_data`, `test_human_curated_files_and_notes_are_reported_never_touched` |
| R7: KG folder, whole side, note-body match else greater fingerprint | 7; decision 3; `test_the_folder_extracted_from_the_merged_note_wins_whole`, `test_both_directions_pick_the_same_content`, `…note_is_still_conflicted_waits`, `…note_deleted_by_the_merge…` |
| R7: structured table, CSV + meta together | 7; decision 4; `test_a_table_takes_the_later_refresh_csv_and_meta_together` |
| R7: a rule per curation record kind, justified | 7; decision 5 (argued below); `test_the_conflict_record_rule`, `test_the_other_kinds_rules` |
| R7: `same_as.yaml`, schemas, mappings reported, never resolved | 7; decision 6 |
| R7: `git checkout` + `git add`, only under `.artmind/data/**`, only during a merge, never commits | 7; decision 1; `test_refuses_without_a_merge_in_progress`, HEAD/MERGE_HEAD asserts |
| R7: narrow exception in `test_no_git_writes.py`, with a reason | 7 (`ALLOWED`, `test_the_resolve_exception_is_only_checkout_and_add_only_there`, `test_every_allowance_is_still_used`) |
| R7: spec D1/D2 describe the exception | 9 |
| R7: `--dryRun` shows the side per folder, table, record | 7 (`resolve(dry_run=True)`), 8 (CLI) |
| R6: notes keep only `_artmind_id`, `_domain`; every per-ingest field in `document.json` | 3, 4; decisions 7–9 |
| R6: `decide_version`, `frontmatter_unchanged`, `docs reindex`, archive, vault-native ingest paths read `document.json` | 3, 4 (`frontmatter_unchanged` replaced by `needs_stamp`), 5 |
| R6: written at most once, again only on a genuine `_domain` change | 4 (`needs_stamp`, `test_reingest_truly_unchanged…`, `test_a_domain_change_rewrites_only_the_domain_line`, `test_git_mv…` "a move never rewrites the note") |
| R6: fat frontmatter still ingests, versions and reindexes | 3 (`ingest_baseline` fallback), 4 (`test_a_legacy_fat_frontmatter_note…`, `test_a_legacy_conversion_no_ops…`), 5 (reindex falls back) |
| Migration: move fields, rewrite once, `--dryRun`, idempotent, resumable | 6 (tests for each) |
| Migration: refuses during a merge or with the worker running | 6 |
| Migration: pause-Obsidian-Git instruction | 6 (`PAUSE_NOTICE`, CLI test) |
| Migration: body bytes, the user's keys and order, line endings | 2 (`frontmatter`), 6 (`MINIMAL` is byte-exact CRLF) |
| Plan C leftover (a): gitignore file rules, block bump, doctor flags old block, versioned-block upgrade still clean | 1 |
| Plan C leftover (b): two machines detecting one conflict | 5 (decision), 7 (`test_two_machines_detecting_the_same_conflict_settle_on_one_record`) |
| Docs: vault.md, document-identity.md, ingestion-helper and curate skills; final suite + CLI help | 9 |

### Placeholder scan

No "TBD", "similar to Task N", or step without its code. Every replaced block is quoted exactly and was matched once by the verification script (below); line numbers were filled in by that script as it applied each block.

### Type and name consistency

`frontmatter`: `FrontmatterEditError`, `set_fields`, `remove_fields`, `read_note`, `write_note(path, text, *, scratch_dir=None)`, `TMP_SUFFIX` (2) → `ingest._stamp_note` (4), `archive` (5), `frontmatter_migration` (6). `document_identity`: `IDENTITY_FIELDS`, `MOVED_FIELDS`, `ingest_baseline(staged, frontmatter)`, `needs_stamp(existing_meta, artmind_id, domain)` (3) → `ingest` (4), `reindex` (5), `frontmatter_migration` (6). `ingest.staged_document(domain, stems, artmind_id, *, search=False)`, `ingest._stamp_note`, `file_result["provenance"]` (4) → `reindex` (5), `conftest.stage_as_extracted` (4). `vault_sync.worker_running` (6) → `frontmatter_migration.migrate`. `frontmatter_migration`: `PAUSE_NOTICE`, `MigrationError`, `migrate(vault_dir, *, dry_run=False)` (6) → CLI. `vault_resolve`: `DATA_PREFIX`, `ResolveError`, `plan`, `resolve(vault_dir, *, dry_run=False)`, `_assert_scoped`, `_decide_record`, `_RECORD_RULES` (7) → CLI (8). `vault.block_version` (1) → `vault_doctor`. Report keys: resolve — `head`, `merge_head`, `resolved[{unit, kind, paths, side, rule}]`, `pending[{unit, kind, paths, why}]`, `reported[{path, why}]`, `remaining`, `dry_run`; migrate — `dry_run`, `notes`, `migrated[{path, removed, document_json{folder, added}}]`, `already_minimal`, `document_json_updated`, `version_mismatch`, `skipped[{path, reason}]`. The same in code, tests and docs.

### Ordering and green suite

Each task leaves `uv run --group dev pytest test/ -q` green (verification record). Task 3 only adds; Task 4 removes `build_frontmatter`/`frontmatter_unchanged` in the same task that removes their last caller. Task 4 changes existing tests' expectations — each change is the design change itself: `test_first_ingest…` (identity only, byte-exact), and four tests that staged no `document.json` between two ingests now do. Task 7 carries the `test_no_git_writes.py` exception because it is the task that introduces the writes.

### Design decisions the spec did not settle (for review)

1. **Whole side via `git checkout <commit> -- <paths>`, plus deletion and `git add`.** The spec says `git checkout --ours/--theirs`. That touches only unmerged entries: in a folder where one side changed `relationships.json` alone, git merges it to that side's version without conflict, and `--ours` on the conflicted files would leave a mixed folder — exactly what R7 forbids. Checking out from the side's commit covers every file of the unit on either side; deleting what the side lacks covers a chunk cache only the other side has. Still only `checkout` and `add`, still only under `.artmind/data/`, still no commit.
2. **Content-only rules.** Nothing depends on which side is "ours", so the two machines of a crossed merge pick the same bytes and their merges merge cleanly; the drill in "Live verification" step 2 checks tree ids.
3. **KG folder.** Spec rule, plus two refinements: the merged note is read from the **working tree** (a note resolved in Obsidian but not yet staged is resolved; one with conflict markers makes the folder `pending` rather than guessed at), and a note **deleted** by the merge takes the side without the folder (an archive on one machine, a re-extraction on the other — keeping the folder would leave a graph document with no note). The note is found by `document.json`'s `source_path`, else by `_artmind_id` across the vault's notes (pre-R6 folders have no `source_path`).
4. **Table.** The spec's `refreshed_at` does not exist in `.meta.json`; `table.ingested_at` is the last refresh. It is a naive local timestamp, so across timezones "later" is approximate — the pick is still deterministic, and a wrong pick costs a re-import, never data (the CSV is regenerated by the next `db` export on the machine that owns the source). A table deleted on one side and changed on the other keeps the table.
5. **Curation records** (the spec said "report, not resolve"; this plan resolves them, because two machines colliding on one record is expected — Plan C decision 7 — and would otherwise block `vault sync` on every such collision). For every kind a **kept record beats a deleted one**: a deletion (`docs restore`, `update retract`, un-asserting by hand) is repeatable, a lost record is not. **Conflicts**: a decision beats `open` because a human's resolution is the only non-derivable information in the record (the rest re-derives from re-detection); between two decisions or two opens, the later change (`resolved_at`, else `detected_at`). **Supersessions**: a `manual` assertion (`ingest supersede`) beats a detector's; two of the same provenance differ only in `effective`, and neither is more right, so the fingerprint decides. **Syntheses**: the later `created_at` — it was written from the later observation set. **Lifecycle**: the record's content is fixed by its id (`sha1(doc_id)`), so two present sides can differ only in `domain`; the fingerprint decides. Every kind ends at the greater record fingerprint.
6. **Other generated files and reported files.** Anything else under `.artmind/data/` (a converted binary's markdown, a legacy `manifest.json`, a committed scratch file) is regenerable: kept over deleted, then the greater content hash. `.artmind/` files outside `data/` and every note are human content: reported, never touched.
7. **The extraction records the per-ingest fields.** Writing them into `document.json` at ingest would put a new hash beside old observations — the fat-frontmatter bug in another file. So a failed extraction leaves the baseline where it was and the next ingest re-extracts (`test_a_failed_extraction_is_retried_at_the_same_version`, `test_an_unextracted_conversion_is_not_a_no_op`). The retry path recomputes the version against the same baseline, which lands on the number the failed run chose.
8. **`_status` dropped.** Always `latest`, never read; retirement lives in the lifecycle record (§15 A10). Moving it would add a meaningless `status` to every `:Document`.
9. **No more seeded authored fields.** "Keeps only `_artmind_id` and `_domain`" rules out seeding `title`/`created_on`/`declared_version` on the first stamp. The graph loses nothing: `extract_kg` defaults the Document's `title` to the stem (what was seeded) and already fell back to the file's birth time for `created_on`; `declared_version` in frontmatter never reached the graph. Existing seeded values stay in notes (they are indistinguishable from human edits, so the migration leaves them).
10. **Baseline lookup order.** Stem, then the pre-move stem, then — only for `adopt`/`heal` — a scan of the domain by id. A scan for every new note would make a directory ingest quadratic; `new` has a fresh id and cannot have a folder. `docs reindex` uses the stem only for the same reason. A domain change looks in the old domain's folder (the body may be unchanged; the version continues).
11. **Stamp first, then version.** The stamp can change how the body parses (a note that began with a blank line); versioning the post-stamp body means the recorded hash is the one the next ingest will compute. An unparsable frontmatter now fails that file's ingest with a message instead of being replaced — the old `render_document` path silently dropped the broken block.
12. **Migration details.** Only absent keys are copied, so a newer ingest's values always win and a rerun writes nothing new; the four ingest-bound fields only when versions match, so a hash never lands beside an extraction it does not describe (that note re-extracts on its next ingest, which is what it needed anyway). A note with no staging folder is skipped, not stripped: its fields are its only baseline. No graph write: re-fingerprinting this machine's graph would make the migration depend on Neo4j and still leave every other machine to replay; the one-time replay costs graph writes, not LLM calls.
13. **Atomic note writes in `.artmind/data/`.** `write_note` renames a temp file over the note; placing the temp file in the vault's `.artmind/data/` (same filesystem, ignored by block v4) means a crash leaves nothing Obsidian shows or Obsidian Git commits. v4 keeps the v3 folder rules and adds the file rules, rather than replacing `*.artmind-tmp/` with `*.artmind-tmp` (which would match both), so a reader of the block sees both cases named.

### Accepted residual risk

- **Every migrated folder is replayed once per machine** by the next `vault sync` (its `document.json` changed, so its fingerprint did). Graph writes and a local re-embed sweep of nothing new, no LLM calls — but on a large vault it is a long first sync.
- **`docs reindex` does not see converted binaries' markdown**: its note walk skips dot-directories, so `.artmind/data/documents/markdowns/` is never read (pre-existing, not introduced here). Their registry rows come back only by re-ingesting. `vault migrate-frontmatter` does read that directory.
- **A renamed note's old staging folder remains** after its next content change (the extraction writes under the new name) — pre-existing; `vault sync` already avoids retracting an id another folder still carries.
- **Frontmatter the line editor cannot edit** (a quoted `"_version":` key, a flow mapping spanning lines) makes the migration skip that note with the reason, and makes an ingest that must change its identity fail with a clear error. Fix those notes by hand.
- **`vault resolve` reads every note once** when a conflicted folder's `document.json` predates `source_path` (the id scan). One pass per run.
- **The table rule's timestamps are local and naive** (decision 4).

---

## Verification record

- Worktree: `git worktree add --detach <scratchpad>/verifyD planC2-applied` (Plan C2 applied, `b6a25ad`). A script parsed this file and applied, in document order, every **Create**, **Append to** and **Replace in** … **with:** block — a replacement must match its file exactly once, and the script filled in each `(lines …)` from where it matched — running `uv run --group dev pytest test/ -q` after each task and committing it.
- Baseline (Plan C2 applied): `2560 passed, 14 skipped`.
- Results after each task (all `… passed, 14 skipped`): Task 1: `2564`; Task 2: `2576`; Task 3: `2581`; Task 4: `2578` (nine `build_frontmatter`/`frontmatter_unchanged` tests removed with the functions); Task 5: `2581`; Task 6: `2594`; Task 7: `2612`; Task 8: `2613`; Task 9: `2613`.
- The applied tree is kept as branch `planD-applied` (one commit per task on top of `planC2-applied`, messages as in each task's Step 5), for Plan E's planner and for review.
- Expected-failure claims: for **every** task with tests (1–8), the task's non-test files were reverted to the previous task's state (new modules deleted) and the task's test files re-run; each Step 2 states what that produced. Task 9 is prose only.
- Real git behaviour relied on, observed in these runs rather than assumed: `merge=binary` turns two regenerated `document.json`/`observations.json` into conflicts; `git checkout <commit> -- <path>` clears an unmerged entry; `git add -- <path>` of a deleted unmerged path stages the deletion; both merge directions of the same two commits resolved to identical bytes (`test_both_directions_pick_the_same_content`).
- The verification worktrees were removed afterwards; only this plan file was committed to `vault-sync-cde`.

