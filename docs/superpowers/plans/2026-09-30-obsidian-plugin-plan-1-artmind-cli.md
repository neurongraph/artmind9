# artmind for Obsidian — Plan 1: the CLI additions (X1–X4) and the plugin's fixtures

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Obsidian plugin the four things it needs from `artmind` that do not exist yet — `artmind --version`, `artmind ingest pending`, `artmind ingest async --pending` and `artmind ingest table2graph --pending` — and pin every `--compact` JSON shape the plugin reads with fixtures regenerated from the real CLI.

**Architecture:** Spec [2026-09-30-obsidian-plugin-design.md](../specs/2026-09-30-obsidian-plugin-design.md) §6 (X1–X4) and §9 (the fixture test). X1 is a Click `version_option` on the root group. X2 must use the ingest's *own* unchanged/changed rules, so Task 2 first factors each rule out of the ingest into a read-only planner (`ingest.plan_vault_native`, `ingest.plan_binary`, `structured.pipeline.registered_tables`/`is_unchanged`) that the ingest itself now calls; `artmind/ingest_pending.py` (Task 3) walks the vault the way `ingest async <vault>` does and asks those planners. X3 (Task 4) enqueues exactly X2's list through the same job path `ingest async` uses. X4 (Task 5) compares the registry's `version` and a digest of the mapping with what the table's staged projection (`kg/<domain>/table__<table>/document.json`) recorded; the digest is new, written by `build_staged`. Task 6 adds `test/test_plugin_fixtures.py`, which runs the real CLI on throwaway vaults and writes/compares `obsidian/artmind-obsidian/test/fixtures/*.json` — the inputs of Plan 2's `VaultState` tests.

**Verified:** every code block in this plan was applied, in task order, by a script that parses this file, to a fresh worktree of `master` (a669f81), on branch `obsidian-plans-applied`, one commit per task, with the full suite run after each task — see "Verification record" at the end. Each "Expected: FAIL" is what the task's new tests printed when run before that task's source change.

**Tech Stack:** Python 3.14, Click (rich-click), pytest (`uv run --group dev pytest test/ -q`, ~2 min, `2886 passed, 14 skipped` at `a669f81`), real throwaway git vaults in `tmp_path`, the real registry (SQLite) and structured store (DuckDB), recording fakes for every Neo4j seam.

---

## Context for the implementer

Read before starting:

- `CLAUDE.md`, "Testing implications". §3: a mocked graph session answers everything, so tests here assert on **what was sent or written** (job rows, staged files, recorded commits), never on a count the code reports about itself. §4: a new command updates its group docstring, the relevant skill and the `justfile` together — Task 3 and Task 5 update the `ingest` group docstring, Task 7 the skill and the `justfile`.
- The spec above, §6 and §9. Plan 2 (`2026-09-30-obsidian-plugin-plan-2-plugin.md`) builds the plugin on these commands and reads Task 6's fixtures; it must be implemented **after** this plan.
- `artmind/ingest.py`: `_ingest_vault_native` (a note's `content` vs `metadata_only` tier: `decide_version` against `ingest_baseline(staged document.json, frontmatter)`, plus a `_domain` change), `_ingest_binary_derived` (a binary's `no_op`: the source's sha256 equals the `source_sha256` its last extraction staged). `artmind/structured/pipeline.py`: `ingest_structured_file` skips a file whose every table is registered with the file's sha256.
- `artmind/table2graph.py`: `find_mappings`, `build_staged` (the Document it writes carries the registry's `version` and the mapping file's name), `stage_dir` (`kg/<domain>/table__<table>/`, gitignored: this machine's projection).
- `test/conftest.py`: autouse fixtures redirect `ARTMIND_HOME`, `HOME`, the registry (`db.DB_PATH`), the structured text export and every Neo4j session to throwaway stand-ins; the LLM refuses to be called. `ingest_env` builds a git vault with `artmind.ingest`'s path constants pointed into it; `stage_as_extracted` writes the `document.json` an extraction would leave; `_fake_docling` stands in for docling.

Terminology:

- **Work**: what an ingest pays for — extracting a note (`tier == "content"`), converting a binary (not `no_op`), loading a table (not `skipped`). A note that only moved is `metadata_only`: not work.
- **Mapped file**: a supported file under a `.artmind/vault.yaml` mapping, outside dot-folders and `_Inbox`, as `collect_ingest_files` + `manifest.filter_for_ingest` select it for `ingest async <vault>`.
- **Projection**: this machine's staged copy of a table's graph document, `kg/<domain>/table__<table>/document.json`, rewritten by every `table2graph` run and by `vault sync` when it re-projects the table.

Run the suite with:

```bash
uv run --group dev pytest test/ -q
```

Do **not** run `just dev-install`, `artmind init` outside `tmp_path`, or anything against a live Neo4j. The user checks the real thing afterwards ("Live verification (user)").

## Decisions this plan makes (the spec left them open)

Each is argued under "Self-review → Design decisions the spec did not settle".

1. **"Unchanged" is factored out, not re-implemented** [1]. `plan_vault_native` returns everything `_ingest_vault_native` decided before writing (domain, identity verdict, baseline, version, tier) and the ingest acts on it; `plan_binary` does the same for a binary; `registered_tables` + `is_unchanged` for a table file. The planners are the ingest's code moved, so the two can never disagree.
2. **A planned note is hashed as it will stand after its identity stamp** [2]. `_stamped_view` computes the stamped text (`frontmatter.set_fields`) without writing it; the ingest now uses the planner's body instead of re-reading the file after stamping. A test pins the hash to the body on disk after a real ingest, for a note with leading blank lines, a CRLF note and a titled note.
3. **What X2 walks and which domain a file gets** [3]: exactly `ingest async <vault>`'s walk; the mapping's domain, else `general` — the worker's own fallback for a job submitted without `--domain` (`row[1] or "general"`).
4. **X2 adds an `errors` list** beside `notes`/`binaries`/`tables` [4]: mapped files the ingest would refuse as they stand (an `_artmind_id` collision, a `_domain` that is not a folder name, an unreadable workbook). They are not work, so X3 never enqueues them, but hiding them would leave the plugin saying "in sync" about a file that can never be ingested.
5. **X3 adds `--compact` to `ingest async`** [5], which had none: the plugin passes `--compact` to every command. The printed shape is unchanged.
6. **X4's projection is the staged `document.json`, and its staleness is `version` + mapping digest** [6]. `missing` = no staged document; `table_refreshed` = its `version` differs from the registry's (bumped by every load and refresh, carried between machines by `.meta.json`); `mapping_changed` = it names another mapping file, or records another `table_mapping_sha256` — a new field, the sha256 of the *parsed* mapping (comments and key order do not count). A projection staged before this plan has no digest and is judged on the file name alone. A table two mappings match is listed with reason `ambiguous`. File modification times were rejected: git rewrites them on every checkout.
7. **Refusals are fixtures of stderr, not new JSON** [7]. `vault sync` refuses with a `ClickException`, which rich-click prints as a boxed, wrapped error on stderr. The fixture test captures that box verbatim (log lines above it dropped) at a fixed `COLUMNS=80`, so Plan 2's `errorText` unboxing and refusal classification are tested on the real text. The spec's "`--compact` JSON" for refusals would need a new error channel in every command; not in scope.
8. **Fixture drift is exact, after normalisation** [8]. Temp paths, shas, uuids and timestamps are replaced by stable stand-ins; everything else must match byte for byte — a changed enum value (`"behind"`) breaks the plugin as surely as a renamed key. `ARTMIND_REGEN_PLUGIN_FIXTURES=1` rewrites the files instead.
9. **The version becomes 0.9.0** [9], the plugin's minimum: the first artmind with X1–X4.

## File map

| File | Change |
|---|---|
| `artmind/cli.py` | `--version` (Task 1); `ingest pending`, `_vault_or_raise`, `_pending_report`, `ingest` docstring (Task 3); `ingest async --pending --compact`, `_submit_job`, `_submit_pending` (Task 4); `table2graph --pending`, `ingest` docstring (Task 5) |
| `artmind/ingest.py` | `NotePlanError`, `NotePlan`, `_stamped_view`, `plan_vault_native`; `BinaryPlan`, `plan_binary`; the two ingest functions call them (Task 2) |
| `artmind/structured/pipeline.py` | `registered_tables`, `is_unchanged`; `ingest_structured_file` calls them (Task 2) |
| `artmind/ingest_pending.py` | new: `pending`, `pending_files` (Task 3) |
| `artmind/table2graph.py` | `TableMapping.digest`, `mapping_digest`, `projection_staleness`, `pending_projections`; `build_staged` records `table_mapping_sha256` (Task 5) |
| `test/test_cli_version.py`, `test/test_ingest_plans.py`, `test/test_ingest_pending.py`, `test/test_table2graph_pending.py`, `test/test_plugin_fixtures.py` | new (Tasks 1, 2, 3–4, 5, 6) |
| `obsidian/artmind-obsidian/test/fixtures/*.json` | new, generated by `test/test_plugin_fixtures.py` (Task 6) |
| `pyproject.toml`, `uv.lock` | version 0.9.0 (Task 7; `uv` rewrites the lock's own version line) |
| `docs/vault.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md`, `justfile` | prose and recipes (Task 7) |

---

### Task 1: `artmind --version` (spec §6 X1)

The plugin runs `artmind --version` at start-up and refuses an artmind older than the one it was built for. Click's `version_option` reads the installed distribution's metadata (`artmind9`), so `pyproject.toml` stays the one place the version is written. `_entry.main` only proxies `query` calls to the `serve` daemon, so `--version` always runs in-process.

**Files:**
- Modify: `artmind/cli.py` (the `cli` group)
- Test: `test/test_cli_version.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_cli_version.py`:

```python
"""`artmind --version` (spec 2026-09-30 §6 X1): the Obsidian plugin reads it
to refuse an artmind older than the one it was built against."""
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from click.testing import CliRunner

from artmind.cli import cli

REPO = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_version_prints_the_package_version():
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output == f"artmind {_pyproject_version()}\n"


def test_version_is_one_parseable_line():
    """The plugin matches `^artmind (\\d+)\\.(\\d+)\\.(\\d+)$` on stdout."""
    result = CliRunner().invoke(cli, ["--version"])

    assert re.fullmatch(r"artmind \d+\.\d+\.\d+\n", result.output)


def test_version_through_the_console_entry_point():
    """What the plugin actually runs: the `artmind` console script, whose
    `_entry.main` proxies only `query` calls to the serve daemon."""
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.argv = ['artmind', '--version']; "
         "from artmind._entry import main; main()"],
        capture_output=True, text=True, cwd=REPO,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"artmind {_pyproject_version()}\n"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_cli_version.py -q`
Expected: `3 failed` — each with rich-click's usage error "No such option '--version'." (exit code 2).

- [ ] **Step 3: Implement**

**Replace in** `artmind/cli.py`:

```python
@click.group()
def cli():
    """Artmind — a knowledge system that synchronizes with your mind."""
    pass
```

**with:**

```python
@click.group()
@click.version_option(
    package_name="artmind9", prog_name="artmind", message="%(prog)s %(version)s",
    help="Print the installed artmind version (`artmind X.Y.Z`) and exit.",
)
def cli():
    """Artmind — a knowledge system that synchronizes with your mind."""
    pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_cli_version.py test/test_cli_guide.py -q`
Expected: all pass (`test_cli_guide.py` renders every option; `--version` is an option of the root group, not a command).

Run: `uv run --group dev pytest test/ -q`
Expected: `2889 passed, 14 skipped, 1 warning`.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_cli_version.py
git commit -m "feat(cli): artmind --version" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: one decision, two callers — the ingest's "would this do work?" as read-only planners

`ingest pending` (Task 3) must list exactly what an ingest would work on. Re-deriving that — a second copy of the identity table, the baseline lookup and the version rule — is exactly the drift the spec forbids. So this task moves each decision out of the ingest, unchanged, into a function that only reads, and makes the ingest call it:

- **Notes.** `plan_vault_native(source, *, domain, set_domain, fork, adopt) -> NotePlan` does everything `_ingest_vault_native` did before its first write: read the note, pick the domain (`--setDomain` > `_domain` > the mapping), validate it, `resolve_identity`, decide whether the identity stamp is due, find the staged baseline (`_locate_staged`) and `decide_version`. `NotePlan.tier` is `content` for a body change or a `_domain` change, else `metadata_only`; `NotePlan.is_new` is "no baseline at all".
- **The stamp.** Before, the ingest stamped the note, then re-read it and hashed the body as it then stood. The planner cannot write, so `_stamped_view` computes the stamped text with the same `frontmatter.set_fields` and reads it the way `read_text` would (universal newlines). The stamped body differs from the note's current one when the note has no frontmatter and starts with blank lines (`_parse_md_frontmatter` strips leading newlines only after a frontmatter block). The ingest now uses the planner's body, so there is one computation, not two.
- **Binaries.** `plan_binary(source, effective_domain) -> BinaryPlan` finds the converted markdown and its staged baseline; `BinaryPlan.unchanged` is the ingest's `no_op` test.
- **Tables.** `registered_tables(source, domain, ...)` returns the file's table specs and their registry rows; `is_unchanged(rows, sha256)` is the skip test `ingest_structured_file` used inline.

No behaviour changes: the existing ingest suites (`test_ingest_vault_native.py`, `test_ingest_identity.py`, `test_reingest.py`, `test_ingest_binary_derived.py`, `test_external_docs.py`, `test_structured_*.py`, …) pass unmodified, and the new tests pin each planner to the ingest that follows it.

**Files:**
- Modify: `artmind/ingest.py` (imports; `_ingest_vault_native`; `_ingest_binary_derived`)
- Modify: `artmind/structured/pipeline.py` (`ingest_structured_file`)
- Test: `test/test_ingest_plans.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_ingest_plans.py`:

```python
"""The ingest's "would this do work?" decisions, factored out so `ingest
pending` (spec 2026-09-30 §6 X2) reuses them instead of copying them:
`ingest.plan_vault_native`, `ingest.plan_binary` and
`structured.pipeline.registered_tables`/`is_unchanged`.

Each planner must (a) write nothing and (b) decide exactly what the real
ingest then does -- asserted here by planning, ingesting for real, and
comparing."""
import pytest

import artmind.ingest as ing
from conftest import _fake_docling, stage_as_extracted


def _note(vault, rel, text, newline=None):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    return path


# ── notes ────────────────────────────────────────────────────────────────────


def test_a_new_note_plans_content_and_writes_nothing(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "Hello.\n")

    plan = ing.plan_vault_native(note, domain="general")

    assert (plan.tier, plan.is_new, plan.stamp) == ("content", True, True)
    assert plan.resolution.verdict == "new"
    assert note.read_text() == "Hello.\n", "planning never stamps the note"
    from artmind.db import _registry_row_by_path
    from artmind.document_identity import canonical_path

    assert _registry_row_by_path(canonical_path(note)) is None, "planning never registers"


def test_an_ingested_unchanged_note_plans_metadata_only(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "Hello.\n")
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(note, "m", "general"))

    plan = ing.plan_vault_native(note, domain="general")

    assert (plan.tier, plan.is_new, plan.stamp) == ("metadata_only", False, False)
    assert plan.resolution.verdict == "reingest"


def test_an_edited_body_plans_content_but_not_new(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "Hello.\n")
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(note, "m", "general"))
    note.write_text(note.read_text().replace("Hello.", "Hello again."))

    plan = ing.plan_vault_native(note, domain="general")

    assert (plan.tier, plan.is_new) == ("content", False)
    assert plan.version.version == 2


def test_a_domain_change_plans_content(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "Hello.\n")
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(note, "m", "general"))

    plan = ing.plan_vault_native(note, domain="general", set_domain="banking")

    assert plan.domain_changed is True
    assert plan.tier == "content"
    assert plan.version.tier == "metadata_only", "the body itself did not change"


@pytest.mark.parametrize(
    "text, newline",
    [
        ("\n\nStarts with blank lines.\n", None),
        ("Line one.\r\nLine two.\r\n", ""),
        ("---\ntitle: kept\n---\nBody under a title.\n", None),
    ],
)
def test_the_planned_hash_is_the_one_the_ingest_records(ingest_env, text, newline):
    """The body is hashed as it stands AFTER the identity stamp -- which can
    differ from the note's body before it (leading blank lines are dropped
    once a frontmatter block precedes them; CRLF reads back as LF). The plan
    computes the stamped text without writing it."""
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", text, newline=newline)

    from artmind.document_identity import compute_content_sha256

    plan = ing.plan_vault_native(note, domain="general")
    result = ing.ingest_file(note, "m", "general")
    _, body_on_disk = ing._parse_md_frontmatter(note.read_text(encoding="utf-8"))

    assert plan.version.content_sha256 == compute_content_sha256(body_on_disk)
    assert result["content_sha256"] == compute_content_sha256(body_on_disk)


def test_a_domain_that_is_not_a_folder_name_is_a_plan_error(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "---\n_domain: ../up\n---\n\nBody.\n")

    with pytest.raises(ing.NotePlanError, match="not a plain folder name"):
        ing.plan_vault_native(note, domain="general")


def test_the_ingest_reports_a_plan_error_as_before(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "No domain anywhere.\n")

    result = ing.ingest_file(note, "m", None)

    assert result["status"] == "failed"
    assert result["error"] == f"{note}: no '_domain' in frontmatter and no --domain given"


# ── binaries ─────────────────────────────────────────────────────────────────


def test_a_binary_plans_new_then_unchanged_then_changed(ingest_env, monkeypatch):
    vault, _ = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nv1\n"]))
    deck = vault / "decks" / "deck.pptx"
    deck.parent.mkdir()
    deck.write_bytes(b"v1")

    first = ing.plan_binary(deck, "general")
    assert (first.is_new, first.unchanged) == (True, False)
    assert not first.registered_path.exists(), "planning never converts"

    stage_as_extracted(ing.KG_DIR, ing.ingest_file(deck, "m", "general"))
    again = ing.plan_binary(deck, "general")
    assert (again.is_new, again.unchanged) == (False, True)

    deck.write_bytes(b"v2")
    changed = ing.plan_binary(deck, "general")
    assert (changed.is_new, changed.unchanged) == (False, False)


def test_an_unchanged_binary_plan_is_the_ingests_no_op(ingest_env, monkeypatch):
    vault, _ = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nv1\n"]))
    deck = vault / "deck.pptx"
    deck.write_bytes(b"v1")
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(deck, "m", "general"))

    assert ing.plan_binary(deck, "general").unchanged
    assert ing.ingest_file(deck, "m", "general")["tier"] == "no_op"


# ── tables ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    return tmp_path


def test_a_table_file_is_unchanged_only_when_every_table_holds_its_bytes(stores):
    from artmind.ingest import _compute_sha256
    from artmind.structured.pipeline import ingest_structured_file, is_unchanged, registered_tables

    source = stores / "accounts.csv"
    source.write_text("id\n1\n")
    specs, rows = registered_tables(source, "banking")
    assert [s["table_name"] for s in specs] == ["accounts"]
    assert rows == [None]
    assert not is_unchanged(rows, _compute_sha256(source))

    ingest_structured_file(source, "banking")
    _, rows = registered_tables(source, "banking")
    assert is_unchanged(rows, _compute_sha256(source))
    assert ingest_structured_file(source, "banking")["status"] == "skipped"

    source.write_text("id\n1\n2\n")
    _, rows = registered_tables(source, "banking")
    assert not is_unchanged(rows, _compute_sha256(source))


def test_no_tables_is_never_unchanged():
    from artmind.structured.pipeline import is_unchanged

    assert is_unchanged([], "abc") is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_plans.py -q`
Expected: `12 failed, 1 passed` — `AttributeError: module 'artmind.ingest' has no attribute 'plan_vault_native'` (and `'NotePlanError'`, `'plan_binary'`), and `ImportError: cannot import name 'is_unchanged' from 'artmind.structured.pipeline'`. The one pass is `test_the_ingest_reports_a_plan_error_as_before`, which pins behaviour the refactor must keep.

- [ ] **Step 3: Factor the note decision out of `_ingest_vault_native`**

Add the imports the planner types need:

**Replace in** `artmind/ingest.py`:

```python
from datetime import datetime
from hashlib import sha1, sha256
from pathlib import Path
```

**with:**

```python
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1, sha256
from pathlib import Path
```

**Replace in** `artmind/ingest.py`:

```python
    ingest_baseline,
    markdown_path_for,
```

**with:**

```python
    ingest_baseline,
    markdown_path_for,
    Resolution,
    VersionDecision,
```

Replace the opening of `_ingest_vault_native` — from its `def` down to (not including) its `ingested_at = ...` line — with the planner and a shorter opening that acts on it. Everything from `ingested_at = ...` on is unchanged: it reads `resolution`, `effective_domain`, `existing_meta`, `stamped`, `staged`, `staged_folder`, `version_decision`, `tier` and `body`, which the new opening still defines.

**Replace in** `artmind/ingest.py` (the head of `_ingest_vault_native`):

```python
def _ingest_vault_native(
    source: Path,
    *,
    domain: str | None,
    job_id: str | None,
    chunk_size: int,
    set_domain: str | None,
    fork: bool,
    adopt: bool,
) -> dict:
    file_result = {"filename": source.name, "status": "failed"}
    t_file_start = time.monotonic()

    raw_text = source.read_text(encoding="utf-8")
    existing_meta, body = _parse_md_frontmatter(raw_text)

    prior_domain = existing_meta.get("_domain")
    effective_domain = set_domain or prior_domain or domain
    # `_domain` (and --setDomain) becomes `kg/<domain>/`: one plain folder name.
    for label, value in (("_domain in frontmatter", prior_domain), ("--setDomain", set_domain)):
        if value is not None and not is_plain_folder_name(value):
            file_result["error"] = (
                f"{source}: {label} {value!r} is not a plain folder name "
                "(no '/', '\\', '..', leading '.', and not empty)"
            )
            logger.error(file_result["error"])
            return file_result
    if not effective_domain:
        file_result["error"] = (
            f"{source}: no '_domain' in frontmatter and no --domain given"
        )
        logger.error(file_result["error"])
        return file_result
    domain_changed = prior_domain is not None and prior_domain != effective_domain

    try:
        resolution = resolve_identity(source, existing_meta.get("_artmind_id"), fork=fork, adopt=adopt)
    except Exception as e:  # IdentityConflict, most likely
        file_result["error"] = str(e)
        logger.error("Identity resolution failed for {}: {}", source, e)
        return file_result
    logger.info("Identity resolution for {}: {}", source.name, resolution.verdict)

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
    # A domain change looks in the old domain's folder too; a folder that is
    # nowhere near where the note now points (a `git mv` left it under the
    # old name, a hand-edited `_domain`) is found by `_locate_staged`'s
    # fallback scan, so the version carries on.
    staged = None
    staged_folder = None
    if resolution.verdict != "new":
        stems = [source.stem] + ([Path(resolution.prior_path).stem] if resolution.prior_path else [])
        found = _locate_staged(
            resolution.artmind_id, stems, [effective_domain, prior_domain],
            search=resolution.verdict in ("adopt", "heal"),
        )
        if found:
            staged_folder, staged = found
    version_decision = decide_version(body, ingest_baseline(staged, existing_meta))
    tier = "content" if domain_changed else version_decision.tier

```

**with:**

```python
class NotePlanError(ValueError):
    """A vault-native note that cannot be ingested as it stands: its
    `_domain` (or `--setDomain`) is not a plain folder name, or it has no
    domain at all. The message is the one `ingest` reports for the file."""


@dataclass(frozen=True)
class NotePlan:
    """What ingesting a vault-native note would do, decided without writing
    anything. `_ingest_vault_native` acts on it; `ingest pending` (spec
    2026-09-30 §6 X2) only reports it -- one decision, so the two can never
    disagree about whether a note has changed.

    `existing_meta`/`body` are the note as the ingest hashes it: after the
    identity stamp when one is due (`stamp`), exactly as `_stamp_note` would
    leave it."""

    existing_meta: dict
    body: str
    prior_domain: str | None
    effective_domain: str
    domain_changed: bool
    resolution: Resolution
    stamp: bool
    staged_folder: Path | None
    staged: dict | None
    baseline: dict
    version: VersionDecision

    @property
    def tier(self) -> str:
        """`content` (extract) or `metadata_only` (nothing to extract). A
        `_domain` change is always `content`: the note moves to another
        schema's extraction."""
        return "content" if self.domain_changed else self.version.tier

    @property
    def is_new(self) -> bool:
        """No ingest of this note ever left a baseline -- no staged
        extraction and no legacy frontmatter hash or version."""
        return not self.baseline.get("_content_sha256") and not self.baseline.get("_version")


def _stamped_view(source: Path, artmind_id: str, domain: str) -> tuple[dict, str]:
    """The note's `(frontmatter, body)` as `_stamp_note` would leave it and
    `source.read_text()` would read it back -- computed, not written.
    `read_text` decodes with universal newlines, hence the two replaces.
    Raises `FrontmatterEditError` for a note that cannot be stamped."""
    from artmind import frontmatter

    text = frontmatter.set_fields(
        frontmatter.read_note(source), {"_artmind_id": artmind_id, "_domain": domain}
    )
    return _parse_md_frontmatter(text.replace("\r\n", "\n").replace("\r", "\n"))


def plan_vault_native(
    source: Path,
    *,
    domain: str | None,
    set_domain: str | None = None,
    fork: bool = False,
    adopt: bool = False,
) -> NotePlan:
    """Decide, reading only, what ingesting `source` would do.

    Raises `NotePlanError` for a bad or missing domain, whatever
    `resolve_identity` raises (`IdentityConflict`, most likely), and
    `FrontmatterEditError` when the identity stamp could not be written."""
    raw_text = source.read_text(encoding="utf-8")
    existing_meta, body = _parse_md_frontmatter(raw_text)

    prior_domain = existing_meta.get("_domain")
    effective_domain = set_domain or prior_domain or domain
    # `_domain` (and --setDomain) becomes `kg/<domain>/`: one plain folder name.
    for label, value in (("_domain in frontmatter", prior_domain), ("--setDomain", set_domain)):
        if value is not None and not is_plain_folder_name(value):
            raise NotePlanError(
                f"{source}: {label} {value!r} is not a plain folder name "
                "(no '/', '\\', '..', leading '.', and not empty)"
            )
    if not effective_domain:
        raise NotePlanError(f"{source}: no '_domain' in frontmatter and no --domain given")
    domain_changed = prior_domain is not None and prior_domain != effective_domain

    resolution = resolve_identity(source, existing_meta.get("_artmind_id"), fork=fork, adopt=adopt)

    # The note is written only to stamp its identity -- first ingest, heal,
    # fork, or a genuine `_domain` change (spec 2026-09-26 R6). The body
    # hashed below is the body as it will stand after that stamp.
    stamp = needs_stamp(existing_meta, resolution.artmind_id, effective_domain)
    if stamp:
        existing_meta, body = _stamped_view(source, resolution.artmind_id, effective_domain)

    # The baseline is what the last successful extraction staged
    # (document.json), with a pre-R6 note's own frontmatter as the fallback.
    # A domain change looks in the old domain's folder too; a folder that is
    # nowhere near where the note now points (a `git mv` left it under the
    # old name, a hand-edited `_domain`) is found by `_locate_staged`'s
    # fallback scan, so the version carries on.
    staged = None
    staged_folder = None
    if resolution.verdict != "new":
        stems = [source.stem] + ([Path(resolution.prior_path).stem] if resolution.prior_path else [])
        found = _locate_staged(
            resolution.artmind_id, stems, [effective_domain, prior_domain],
            search=resolution.verdict in ("adopt", "heal"),
        )
        if found:
            staged_folder, staged = found
    baseline = ingest_baseline(staged, existing_meta)
    return NotePlan(
        existing_meta=existing_meta,
        body=body,
        prior_domain=prior_domain,
        effective_domain=effective_domain,
        domain_changed=domain_changed,
        resolution=resolution,
        stamp=stamp,
        staged_folder=staged_folder,
        staged=staged,
        baseline=baseline,
        version=decide_version(body, baseline),
    )


def _ingest_vault_native(
    source: Path,
    *,
    domain: str | None,
    job_id: str | None,
    chunk_size: int,
    set_domain: str | None,
    fork: bool,
    adopt: bool,
) -> dict:
    from artmind.frontmatter import FrontmatterEditError

    file_result = {"filename": source.name, "status": "failed"}
    t_file_start = time.monotonic()

    try:
        plan = plan_vault_native(source, domain=domain, set_domain=set_domain, fork=fork, adopt=adopt)
    except NotePlanError as e:
        file_result["error"] = str(e)
        logger.error(file_result["error"])
        return file_result
    except FrontmatterEditError as e:
        file_result["error"] = f"{source}: cannot stamp identity into its frontmatter: {e}"
        logger.error(file_result["error"])
        return file_result
    except Exception as e:  # IdentityConflict, most likely
        file_result["error"] = str(e)
        logger.error("Identity resolution failed for {}: {}", source, e)
        return file_result
    resolution = plan.resolution
    effective_domain = plan.effective_domain
    existing_meta = plan.existing_meta
    logger.info("Identity resolution for {}: {}", source.name, resolution.verdict)

    # Every per-ingest field lives in the staging folder's document.json,
    # written by the extraction, so re-ingesting an unchanged note writes
    # nothing to it and gives Obsidian Git nothing to commit or conflict on.
    # Stamped line by line (`artmind.frontmatter`): the body, the user's own
    # keys, their order and the line endings are kept.
    stamped = plan.stamp
    if stamped:
        try:
            _stamp_note(source, resolution.artmind_id, effective_domain)
        except FrontmatterEditError as e:
            file_result["error"] = f"{source}: cannot stamp identity into its frontmatter: {e}"
            logger.error(file_result["error"])
            return file_result

    staged = plan.staged
    staged_folder = plan.staged_folder
    version_decision = plan.version
    tier = plan.tier
    body = plan.body

```

- [ ] **Step 4: Factor the binary decision out of `_ingest_binary_derived`**

**Replace in** `artmind/ingest.py`:

```python
def _ingest_binary_derived(
    source: Path,
    image_model: str,
```

**with:**

```python
@dataclass(frozen=True)
class BinaryPlan:
    """Whether ingesting a binary source (pdf/pptx/docx, an image) would
    convert it, decided without writing anything. `_ingest_binary_derived`
    acts on it; `ingest pending` (spec 2026-09-30 §6 X2) only reports it."""

    registered_path: Path
    existing_meta: dict
    baseline: dict
    source_sha256: str

    @property
    def unchanged(self) -> bool:
        """Converted before, and the source's bytes are the ones the last
        extraction recorded: the ingest is a `no_op`."""
        return bool(self.existing_meta) and self.baseline.get("_source_sha256") == self.source_sha256

    @property
    def is_new(self) -> bool:
        """Never converted: no markdown of it exists yet."""
        return not self.existing_meta


def plan_binary(source: Path, effective_domain: str) -> BinaryPlan:
    """Read, never write: the converted markdown this binary maps to and the
    baseline its last extraction left (spec 2026-09-26 R6: the staging
    folder's document.json, with a pre-R6 conversion's own frontmatter as
    the fallback -- a binary whose extraction failed is never mistaken for
    converted and extracted)."""
    stem = source.stem
    registered_path = MARKDOWNS_DIR / f"{stem}.md"
    existing_meta: dict = {}
    if registered_path.exists():
        existing_meta, _ = _parse_md_frontmatter(
            registered_path.read_text(encoding="utf-8")
        )
    staged = staged_document(effective_domain, [stem], existing_meta.get("_artmind_id"), search=True)
    return BinaryPlan(
        registered_path=registered_path,
        existing_meta=existing_meta,
        baseline=ingest_baseline(staged, existing_meta),
        source_sha256=_compute_sha256(source),
    )


def _ingest_binary_derived(
    source: Path,
    image_model: str,
```

**Replace in** `artmind/ingest.py` (in `_ingest_binary_derived`):

```python
    orig_registry_path = canonical_path(dest_path)
    source_sha256 = _compute_sha256(source)

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
```

**with:**

```python
    orig_registry_path = canonical_path(dest_path)
    plan = plan_binary(source, effective_domain)
    source_sha256 = plan.source_sha256
    registered_path = plan.registered_path
    existing_meta = plan.existing_meta
    baseline = plan.baseline

    if plan.unchanged:
```

`stem`, `effective_domain` and `dest_path` are still defined above this block; the rest of the function reads `source_sha256`, `registered_path`, `existing_meta` and `baseline` exactly as before.

- [ ] **Step 5: Factor the table decision out of `ingest_structured_file`**

**Replace in** `artmind/structured/pipeline.py`:

```python
def ingest_structured_file(
    source: Path,
    domain: str,
```

**with:**

```python
def registered_tables(
    source: Path, domain: str, *, table: str | None = None, sheet: str | None = None, header_row: int = 0
) -> tuple[list[dict], list[dict | None]]:
    """`(table_specs, registry_rows)` for the tables `source` loads into under
    `domain` -- one per sheet -- with `None` for a table not registered yet.
    Reads only."""
    table_specs = _enumerate_source_tables(source, table=table, sheet=sheet, header_row=header_row)
    return table_specs, [registry.get_table(spec["table_name"], domain=domain) for spec in table_specs]


def is_unchanged(existing_rows: list[dict | None], file_sha256: str) -> bool:
    """Every table the file loads is registered, from these exact bytes --
    what `ingest_structured_file` skips on (without `force`), and what
    `ingest pending` (spec 2026-09-30 §6 X2) leaves out."""
    return bool(existing_rows) and all(row and row.get("sha256") == file_sha256 for row in existing_rows)


def ingest_structured_file(
    source: Path,
    domain: str,
```

**Replace in** `artmind/structured/pipeline.py` (in `ingest_structured_file`):

```python
    table_specs = _enumerate_source_tables(source, table=table, sheet=sheet, header_row=header_row)

    if not force:
        existing_rows = [
            registry.get_table(spec["table_name"], domain=domain) for spec in table_specs
        ]
        if existing_rows and all(
            row and row.get("sha256") == file_sha256 for row in existing_rows
        ):
            return {
                "status": "skipped",
                "domain": domain,
                "tables": [row["table_name"] for row in existing_rows],
            }
```

**with:**

```python
    table_specs, existing_rows = registered_tables(
        source, domain, table=table, sheet=sheet, header_row=header_row
    )

    if not force and is_unchanged(existing_rows, file_sha256):
        return {
            "status": "skipped",
            "domain": domain,
            "tables": [row["table_name"] for row in existing_rows],
        }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_ingest_plans.py test/test_ingest_vault_native.py test/test_ingest_identity.py test/test_reingest.py test/test_ingest_binary_derived.py test/test_external_docs.py test/test_structured_dispatch.py test/test_db_cli.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2902 passed, 14 skipped, 1 warning`.

- [ ] **Step 7: Commit**

```bash
git add artmind/ingest.py artmind/structured/pipeline.py test/test_ingest_plans.py
git commit -m "refactor(ingest): the would-this-do-work decisions as read-only planners" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `artmind ingest pending` (spec §6 X2)

`artmind/ingest_pending.py` walks the vault exactly as `ingest async <vault>` does (`collect_ingest_files` + `manifest.filter_for_ingest`), gives each file its mapping's domain (else `general`, the worker's fallback), and asks Task 2's planners. The CLI command resolves the vault the way every `ingest` command does (`paths.ARTMIND_VAULT_DIR`).

The central test, `test_equals_what_a_following_ingest_does_work_on`, builds every case at once (new/changed/unchanged note, binary and table; a moved note; a note the ingest refuses; unmapped and `_Inbox` files), records `pending`'s list, then runs the real `ingest sync <vault>` — extraction stood in for by staging the `document.json` it would write — and records which files it actually extracted, converted or loaded. The two sets must be equal, and afterwards nothing is pending.

**Files:**
- Create: `artmind/ingest_pending.py`
- Modify: `artmind/cli.py` (`COMMAND_GROUPS`, the `ingest` group docstring, a new command before `ingest jobs`)
- Test: `test/test_ingest_pending.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_ingest_pending.py`:

```python
"""`artmind ingest pending` (spec 2026-09-30 §6 X2): the mapped files an
ingest would do work on, decided by the ingest's own rules.

Real throwaway vault (`ingest_env`: a git repo, the registry, `.artmind/`
staging), real structured store, real identity resolution. Only docling and
the LLM extraction are stood in for: `_convert_binary_via_docling` returns a
body, and `ingest_to_kg` stages the `document.json` an extraction would
leave (`stage_as_extracted`) instead of calling a model."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import artmind.ingest as ing
from artmind.ingest_pending import pending, pending_files
from conftest import stage_as_extracted

MANIFEST = """\
ingest:
  mappings:
    - path: "notes/**"
      domain: general
    - path: "decks/**"
      domain: general
    - path: "tables/**"
      domain: banking
"""


def _fake_docling(dest_path, image_model):
    """A conversion whose body follows the binary's bytes, like docling's."""
    return f"# {dest_path.stem}\n\n{dest_path.read_bytes().decode()}\n", {}


@pytest.fixture()
def pvault(ingest_env, tmp_path, monkeypatch):
    """`ingest_env`'s vault, mapped by a manifest, with the CLI's own vault
    constant and the structured store pointed into it."""
    import paths

    vault, _ = ingest_env
    (vault / ".artmind" / "vault.yaml").write_text(MANIFEST)
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling)
    return vault


def _write(vault: Path, rel: str, content) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _ingest(path: Path, domain: str) -> None:
    """One real ingest of `path`, extraction stood in for: what a previous
    `ingest sync`/`async` left behind."""
    from artmind.structured import is_structured_source
    from artmind.structured.pipeline import ingest_structured_file

    if is_structured_source(path):
        assert ingest_structured_file(path, domain)["status"] == "ok"
        return
    result = ing.ingest_file(path, "image-model", domain)
    assert result["status"] == "ok", result
    stage_as_extracted(ing.KG_DIR, result, domain)


def _build(vault: Path) -> None:
    """Every case at once: new, changed and unchanged of each kind, a moved
    note, a note the ingest would refuse, and files no ingest walks."""
    _ingest(_write(vault, "notes/unchanged.md", "Same body.\n"), "general")
    _ingest(_write(vault, "notes/edited.md", "Body v1.\n"), "general")
    _ingest(_write(vault, "notes/moving.md", "Moves, unchanged.\n"), "general")
    _ingest(_write(vault, "decks/same.pptx", b"deck bytes v1"), "general")
    _ingest(_write(vault, "decks/redone.pptx", b"deck bytes v1"), "general")
    _ingest(_write(vault, "tables/kept.csv", "id,name\n1,a\n"), "banking")
    _ingest(_write(vault, "tables/grown.csv", "id,name\n1,a\n"), "banking")

    edited = vault / "notes/edited.md"
    edited.write_text(edited.read_text().replace("Body v1.", "Body v2."))
    (vault / "notes/sub").mkdir()
    (vault / "notes/moving.md").rename(vault / "notes/sub/moved.md")
    _write(vault, "decks/redone.pptx", b"deck bytes v2")
    _write(vault, "tables/grown.csv", "id,name\n1,a\n2,b\n")

    _write(vault, "notes/fresh.md", "A new note.\n")
    _write(vault, "decks/new.pdf", b"pdf bytes")
    _write(vault, "tables/new.csv", "id\n1\n")
    _write(vault, "notes/bad_domain.md", "---\n_domain: ../escape\n---\n\nBody.\n")
    _write(vault, "drafts/unmapped.md", "Not mapped.\n")
    _write(vault, "notes/_Inbox/wip.md", "Drafting.\n")


def test_lists_new_and_changed_files_by_kind(pvault):
    _build(pvault)

    report = pending(pvault)

    assert report["notes"] == [
        {"path": "notes/edited.md", "reason": "changed"},
        {"path": "notes/fresh.md", "reason": "new"},
    ]
    assert report["binaries"] == [
        {"path": "decks/new.pdf", "reason": "new"},
        {"path": "decks/redone.pptx", "reason": "changed"},
    ]
    assert report["tables"] == [
        {"path": "tables/grown.csv", "reason": "changed"},
        {"path": "tables/new.csv", "reason": "new"},
    ]
    assert [e["path"] for e in report["errors"]] == ["notes/bad_domain.md"]
    assert "not a plain folder name" in report["errors"][0]["error"]


def test_writes_nothing(pvault):
    """Read-only: no note is stamped, nothing is registered or staged."""
    _build(pvault)
    fresh = pvault / "notes/fresh.md"
    before = fresh.read_bytes()
    staging = sorted(p.relative_to(ing.KG_DIR) for p in ing.KG_DIR.rglob("*"))

    pending(pvault)

    assert fresh.read_bytes() == before, "a new note is not stamped"
    assert sorted(p.relative_to(ing.KG_DIR) for p in ing.KG_DIR.rglob("*")) == staging
    assert not (ing.MARKDOWNS_DIR / "new.md").exists(), "a new binary is not converted"


def test_equals_what_a_following_ingest_does_work_on(pvault, monkeypatch):
    """The contract: what `pending` lists is exactly what the next ingest of
    the vault extracts, converts or loads -- and afterwards nothing is
    pending. Drives the real `ingest sync <vault>`, recording each file's
    outcome; `ingest_to_kg` stages the extraction it would have made."""
    import artmind.cli as cli
    from artmind.structured.pipeline import ingest_structured_file

    _build(pvault)
    listed = {e["path"] for kind in ("notes", "binaries", "tables") for e in pending(pvault)[kind]}

    worked: set[str] = set()
    real_ingest_file = cli.ingest_file

    def _recording_ingest_file(path, *args, **kwargs):
        result = real_ingest_file(path, *args, **kwargs)
        if result.get("status") == "ok" and ing.kg_work_was_done(result):
            worked.add(Path(path).resolve().relative_to(pvault).as_posix())
        return result

    def _staging_ingest_to_kg(result, domain, *args, **kwargs):
        if ing.kg_work_was_done(result):
            stage_as_extracted(ing.KG_DIR, result, domain)
        return True

    def _recording_structured(path, domain, **kwargs):
        result = ingest_structured_file(path, domain, **kwargs)
        if result["status"] == "ok":
            worked.add(Path(path).resolve().relative_to(pvault).as_posix())
        return result

    monkeypatch.setattr(cli, "ingest_file", _recording_ingest_file)
    monkeypatch.setattr(cli, "ingest_to_kg", _staging_ingest_to_kg)
    monkeypatch.setattr(cli, "ingest_structured_file", _recording_structured)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain=None, keys=None: {})

    result = CliRunner().invoke(cli.cli, ["ingest", "sync", str(pvault)])

    assert result.exit_code == 0, result.output
    assert worked == listed
    after = pending(pvault)
    assert (after["notes"], after["binaries"], after["tables"]) == ([], [], [])


def test_pending_files_are_absolute_and_in_walk_order(pvault):
    _build(pvault)

    files = pending_files(pvault)

    assert files == sorted(files)
    assert [f.relative_to(pvault).as_posix() for f in files] == [
        "decks/new.pdf", "decks/redone.pptx", "notes/edited.md", "notes/fresh.md",
        "tables/grown.csv", "tables/new.csv",
    ]


def test_a_manifest_without_mappings_considers_every_supported_file(pvault):
    """No mappings maps nothing and so filters nothing (`Manifest.should_ingest`);
    such a file takes the worker's default domain."""
    (pvault / ".artmind" / "vault.yaml").write_text("ingest:\n  mappings: []\n")
    _write(pvault, "anywhere/note.md", "Hello.\n")

    assert pending(pvault)["notes"] == [{"path": "anywhere/note.md", "reason": "new"}]


def test_cli_prints_the_report_as_json(pvault):
    from artmind.cli import cli

    _build(pvault)

    result = CliRunner().invoke(cli, ["ingest", "pending", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == pending(pvault)
    assert "\n" not in result.stdout.strip(), "--compact is one line"


def test_cli_refuses_outside_a_vault(monkeypatch):
    import paths
    from artmind.cli import cli

    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", None)

    result = CliRunner().invoke(cli, ["ingest", "pending"])

    assert result.exit_code != 0
    assert "Not inside an artmind vault" in result.output


def test_cli_reports_a_broken_manifest(pvault):
    from artmind.cli import cli

    (pvault / ".artmind" / "vault.yaml").write_text("ingest: [not, a, mapping]\n")

    result = CliRunner().invoke(cli, ["ingest", "pending"])

    assert result.exit_code != 0
    assert "'ingest' must be a mapping" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_pending.py -q`
Expected: `1 error` during collection — `ModuleNotFoundError: No module named 'artmind.ingest_pending'`.

- [ ] **Step 3: Implement the listing**

**Create** `artmind/ingest_pending.py`:

```python
"""`artmind ingest pending` (spec 2026-09-30 §6 X2): the mapped files an
ingest of this vault would do work on. Read-only, no LLM.

"Work" is what costs something: extracting a note, converting a binary,
loading a table. Every decision is the ingest's own, reused rather than
re-implemented -- `ingest.plan_vault_native` (body hash against the staged
baseline, a `_domain` change), `ingest.plan_binary` (source hash against the
staged baseline) and `structured.pipeline.is_unchanged` (file hash against
the registry) -- so a file listed here is exactly a file `ingest async
--pending` (X3) would extract, convert or load, and one left out is one it
would skip. A note that only moved (`metadata_only`) is not work.

The files considered are the ones `ingest async <vault>` walks: supported
types, no dot-folders, no `_Inbox`, and only paths a `.artmind/vault.yaml`
mapping covers. A file's domain is its mapping's, falling back to the
worker's own default (`general`) exactly as a job with no `--domain` does.
"""
from __future__ import annotations

from pathlib import Path

#: The domain `worker.py` gives a file in a job submitted without `--domain`
#: that no mapping covers (`row[1] or "general"`).
DEFAULT_DOMAIN = "general"

KINDS = ("notes", "binaries", "tables")


def _classify(path: Path, domain: str) -> tuple[str, str | None]:
    """`(kind, reason)` for one file: `reason` is `new`, `changed`, or None
    when an ingest would do no work on it. Raises whatever the ingest's own
    decision raises for a file it could not ingest."""
    from artmind import ingest
    from artmind.structured import is_structured_source

    if is_structured_source(path):
        from artmind.structured.pipeline import is_unchanged, registered_tables

        _, rows = registered_tables(path, domain)
        if is_unchanged(rows, ingest._compute_sha256(path)):
            return "tables", None
        return "tables", "changed" if any(rows) else "new"
    if ingest._is_vault_native_markdown(path):
        plan = ingest.plan_vault_native(path, domain=domain)
        if plan.tier != "content":
            return "notes", None
        return "notes", "new" if plan.is_new else "changed"
    plan = ingest.plan_binary(path, domain)
    if plan.unchanged:
        return "binaries", None
    return "binaries", "new" if plan.is_new else "changed"


def pending(vault_root: Path) -> dict:
    """`{"notes": [...], "binaries": [...], "tables": [...], "errors": [...]}`.

    Each of the first three lists `{"path", "reason"}` in walk order, with
    `path` vault-relative (posix) and `reason` `new` or `changed`. `errors`
    lists `{"path", "error"}` for a mapped file the ingest could not ingest
    as it stands (an `_artmind_id` collision, an unreadable workbook, a
    `_domain` that is not a folder name): not work, but worth showing.
    Raises `manifest.ManifestError` for a broken `.artmind/vault.yaml`."""
    from artmind.ingest import collect_ingest_files
    from artmind.manifest import filter_for_ingest

    root = Path(vault_root).resolve()
    manifest, files, _ = filter_for_ingest(root, root, collect_ingest_files(root))
    report: dict = {kind: [] for kind in KINDS}
    report["errors"] = []
    for path in files:
        rel = path.resolve().relative_to(root).as_posix()
        domain = manifest.domain_for(rel) or DEFAULT_DOMAIN
        try:
            kind, reason = _classify(path, domain)
        except Exception as e:  # the ingest would fail on this file, not work on it
            report["errors"].append({"path": rel, "error": str(e)})
            continue
        if reason is not None:
            report[kind].append({"path": rel, "reason": reason})
    return report


def pending_files(vault_root: Path, report: dict | None = None) -> list[Path]:
    """Absolute paths of every file `pending` lists as work, in walk order --
    what `ingest async --pending` (X3) enqueues."""
    root = Path(vault_root).resolve()
    report = pending(root) if report is None else report
    return sorted(root / entry["path"] for kind in KINDS for entry in report[kind])
```

- [ ] **Step 4: Add the command**

Route it in the CLI guide and `--help` (`test/test_cli_guide.py` fails for an unrouted command):

**Replace in** `artmind/cli.py` (`COMMAND_GROUPS`):

```python
                "sync", "async", "jobs", "jobs-active", "jobs-completed",
```

**with:**

```python
                "sync", "async", "pending", "jobs", "jobs-active", "jobs-completed",
```

Name it in the group's docstring (CLAUDE.md §4):

**Replace in** `artmind/cli.py`:

```python
    """Manage document ingestion (sync, async, status, results,...) and table -> graph projection (table2graph)."""
```

**with:**

```python
    """Manage document ingestion (sync, async, pending, status, results,...) and table -> graph projection (table2graph)."""
```

Add the helpers and the command just before `ingest jobs`:

**Replace in** `artmind/cli.py`:

```python
@ingest.command("jobs")
@click.option("--status", default=None, help="Filter by status (queued/processing/completed/failed)")
```

**with:**

```python
def _vault_or_raise() -> Path:
    """The vault this command runs in (`paths.ARTMIND_VAULT_DIR`, as every
    ingest command resolves it), or a ClickException outside one."""
    from paths import ARTMIND_VAULT_DIR

    if ARTMIND_VAULT_DIR is None:
        raise click.ClickException(
            "Not inside an artmind vault.\n"
            "  cd into one, or run `artmind init` to make this directory a vault."
        )
    return ARTMIND_VAULT_DIR


def _pending_report(vault_dir: Path) -> dict:
    """`ingest_pending.pending`, with a broken manifest as a ClickException."""
    from artmind.ingest_pending import pending
    from artmind.manifest import ManifestError

    try:
        return pending(vault_dir)
    except ManifestError as e:
        raise click.ClickException(str(e))


@ingest.command("pending")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_pending_cmd(compact: bool):
    """List the mapped files an ingest would do work on: new, or changed since their last ingest. Read-only, no LLM.

    Walks the vault exactly as `ingest async <vault>` does (mapped folders
    only; no dot-folders, no _Inbox) and applies the ingest's own rules to
    each file: a note is listed when its body changed since the extraction
    staged in its document.json (or its `_domain` changed), a binary when its
    bytes differ from the ones last converted, a table (csv/xlsx) when its
    bytes differ from the ones registered. A note that only moved is not
    listed -- re-ingesting it extracts nothing. Prints `{notes, binaries,
    tables, errors}`: each entry a vault-relative `path` and a `reason`
    (`new` / `changed`); `errors` names mapped files the ingest would refuse
    as they stand. `ingest async --pending` enqueues exactly this list.
    """
    _setup_logger()
    _echo_json(_pending_report(_vault_or_raise()), compact)


@ingest.command("jobs")
@click.option("--status", default=None, help="Filter by status (queued/processing/completed/failed)")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_ingest_pending.py test/test_cli_guide.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2911 passed, 14 skipped, 1 warning`.

- [ ] **Step 6: Commit**

```bash
git add artmind/ingest_pending.py artmind/cli.py test/test_ingest_pending.py
git commit -m "feat(ingest): ingest pending -- the mapped files an ingest would do work on" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `artmind ingest async --pending` (spec §6 X3)

`FILE_PATH` becomes optional; `--pending` takes its place and submits `pending_files(vault)` — X2's list, absolute, in walk order — as one job through the same `_create_job` + `_ensure_worker_running` path. The job's `domain` is `--domain` (a fallback only; `None` by default), exactly as for a directory submission: the worker re-derives each file's mapped domain. Nothing pending prints `{"job_id": null, "file_count": 0}` and starts no worker. `--compact` is added because the plugin passes it to every command; without it the output is the same indented JSON as before.

**Files:**
- Modify: `artmind/cli.py` (`ingest async`)
- Test: `test/test_ingest_pending.py` (appended)

- [ ] **Step 1: Write the failing tests**

**Append to** `test/test_ingest_pending.py`:

```python


# ── `ingest async --pending` (spec 2026-09-30 §6 X3) ─────────────────────────


@pytest.fixture()
def workers(monkeypatch):
    """Records worker starts instead of spawning `worker.py`."""
    import artmind.cli as cli

    started = []
    monkeypatch.setattr(cli, "_ensure_worker_running", lambda: started.append(True))
    return started


def _job_files(job_id: str) -> list[str]:
    from artmind.db import _get_db

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT filename FROM ingestion_job_files WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def test_async_pending_enqueues_exactly_the_pending_list_as_one_job(pvault, workers):
    from artmind.cli import cli

    _build(pvault)
    expected = [str(f) for f in pending_files(pvault)]

    result = CliRunner().invoke(cli, ["ingest", "async", "--pending", "--compact"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload) == {"job_id", "domain", "file_count", "submitted_at", "message"}
    assert payload["file_count"] == len(expected) == 6
    assert payload["domain"] is None, "each file's mapping decides its domain"
    assert _job_files(payload["job_id"]) == expected
    assert workers == [True]


def test_async_pending_with_nothing_pending_submits_no_job(pvault, workers):
    from artmind.cli import cli
    from artmind.jobs import _list_jobs

    result = CliRunner().invoke(cli, ["ingest", "async", "--pending", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"job_id": None, "file_count": 0}
    assert _list_jobs() == []
    assert workers == []


def test_async_pending_takes_no_file_path(pvault, workers):
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "async", str(pvault), "--pending"])

    assert result.exit_code != 0
    assert "--pending takes no FILE_PATH" in result.output
    assert workers == []


def test_async_needs_a_file_path_or_pending(pvault, workers):
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "async"])

    assert result.exit_code != 0
    assert "Give a FILE_PATH to ingest, or --pending" in result.output


def test_async_with_a_path_prints_the_same_shape_compact(pvault, workers):
    """--compact is new on `ingest async`: the plugin passes it to every command."""
    from artmind.cli import cli

    _write(pvault, "notes/one.md", "One.\n")

    result = CliRunner().invoke(cli, ["ingest", "async", str(pvault / "notes/one.md"), "--compact"])

    assert result.exit_code == 0, result.output
    assert "\n" not in result.stdout.strip()
    payload = json.loads(result.stdout)
    assert payload["file_count"] == 1
    assert _job_files(payload["job_id"]) == [str((pvault / "notes/one.md").resolve())]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_pending.py -q`
Expected: `5 failed, 8 passed` — Task 3's eight tests still pass; the five new ones fail because `ingest async` has no `--pending`/`--compact` and requires `FILE_PATH`: the CLI prints rich-click's usage box ("No such option '--pending'.") instead of the job JSON, so `json.loads`/the message assertions fail.

- [ ] **Step 3: Implement**

**Replace in** `artmind/cli.py`:

```python
@ingest.command("async")
@click.argument("file_path", type=click.Path(exists=True))
```

**with:**

```python
@ingest.command("async")
@click.argument("file_path", type=click.Path(exists=True), required=False)
```

**Replace in** `artmind/cli.py` (the options, signature and opening of `ingest_async`):

```python
@click.option("--force", is_flag=True, help="Ingest even if identical content is already registered")
@click.option("--stage-only", is_flag=True, help="Extract KG JSON but do not write to the graph (leaves it staged for a later commit)")
def ingest_async(file_path: str, domain: str | None, force: bool, stage_only: bool):
    """Submit a file or directory for background ingestion; returns job_id immediately."""
    _require_ingest_extra()
    _setup_logger()
    path = Path(file_path)
```

**with:**

```python
@click.option("--force", is_flag=True, help="Ingest even if identical content is already registered")
@click.option("--stage-only", is_flag=True, help="Extract KG JSON but do not write to the graph (leaves it staged for a later commit)")
@click.option(
    "--pending", "pending", is_flag=True,
    help="Instead of FILE_PATH: enqueue exactly the files `ingest pending` lists (new or"
    " changed mapped files in this vault), as one job. Prints {\"job_id\": null,"
    " \"file_count\": 0} when nothing is pending.",
)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_async(
    file_path: str | None, domain: str | None, force: bool, stage_only: bool, pending: bool, compact: bool,
):
    """Submit a file or directory for background ingestion; returns job_id immediately.

    With --pending (and no FILE_PATH), submits the vault's pending files
    instead: exactly what `artmind ingest pending` lists.
    """
    _require_ingest_extra()
    _setup_logger()
    if pending:
        if file_path is not None:
            raise click.ClickException("--pending takes no FILE_PATH: it submits the vault's pending files")
        _submit_pending(domain=domain, force=force, stage_only=stage_only, compact=compact)
        return
    if file_path is None:
        raise click.ClickException("Give a FILE_PATH to ingest, or --pending for the vault's pending files")
    path = Path(file_path)
```

**Replace in** `artmind/cli.py` (the end of `ingest_async`):

```python
    batch_files = [str(f.resolve()) for f in files]
    job_id = _create_job(batch_files, domain=domain, force=force, stage_only=stage_only)
    _ensure_worker_running()

    _echo_json({
        "job_id": job_id,
        "domain": domain,
        "file_count": len(batch_files),
        "submitted_at": datetime.now().isoformat(),
        "message": f"Job submitted with {len(batch_files)} file(s)",
    })
```

**with:**

```python
    batch_files = [str(f.resolve()) for f in files]
    _submit_job(batch_files, domain=domain, force=force, stage_only=stage_only, compact=compact)


def _submit_job(batch_files: list[str], *, domain: str | None, force: bool, stage_only: bool, compact: bool) -> None:
    """Create the job, make sure a worker runs it, print it -- `ingest
    async`'s one output shape, with or without --pending."""
    job_id = _create_job(batch_files, domain=domain, force=force, stage_only=stage_only)
    _ensure_worker_running()

    _echo_json({
        "job_id": job_id,
        "domain": domain,
        "file_count": len(batch_files),
        "submitted_at": datetime.now().isoformat(),
        "message": f"Job submitted with {len(batch_files)} file(s)",
    }, compact)


def _submit_pending(*, domain: str | None, force: bool, stage_only: bool, compact: bool) -> None:
    """`ingest async --pending` (spec 2026-09-30 §6 X3): one job for exactly
    `ingest pending`'s list. `--domain` is only the fallback for a file no
    mapping covers, as for any directory submission."""
    from artmind.ingest_pending import pending_files
    from artmind.manifest import load as load_manifest

    vault_dir = _vault_or_raise()
    if domain is not None and domain not in _get_available_domains():
        raise click.ClickException(
            f"Unknown domain '{domain}'. Run 'artmind domains list' to see available domains."
        )
    files = pending_files(vault_dir, _pending_report(vault_dir))
    if not files:
        _echo_json({"job_id": None, "file_count": 0}, compact)
        return
    _validate_manifest_domains(load_manifest(vault_dir))
    _submit_job([str(f) for f in files], domain=domain, force=force, stage_only=stage_only, compact=compact)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_ingest_pending.py test/test_ingest_manifest_cli.py test/test_worker_manifest.py test/test_jobs_stage_only.py -q`
Expected: all pass.

Run: `uv run --group dev pytest test/ -q`
Expected: `2916 passed, 14 skipped, 1 warning`.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_ingest_pending.py
git commit -m "feat(ingest): ingest async --pending, and --compact on ingest async" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `artmind ingest table2graph --pending` (spec §6 X4)

What the code already stores decides "older than": `build_staged` writes the registry's `version` into the projection's `document.json`, and `register_table` bumps that version on every load and refresh (and `.meta.json` carries it to the other laptop). For "the mapping's last change" nothing was stored beyond the file's name, so every projection now also records `table_mapping_sha256`, a digest of the parsed mapping — the same whether `table2graph` read the file or `vault sync` read the committed blob, and blind to comments and key order. `projection_staleness` compares the three; `pending_projections` lists every registered table exactly one mapping matches whose projection is out of date, plus tables two mappings match (`ambiguous`). A mapping that does not parse fails the listing, as it fails `table2graph`.

The central test, `test_equals_what_a_following_table2graph_changes`, runs the real `table2graph` on every mapped table and checks that exactly the listed tables' staged files changed (`sync_state.folder_fingerprint` before and after), and that afterwards only the ambiguous table remains.

**Files:**
- Modify: `artmind/table2graph.py`
- Modify: `artmind/cli.py` (`ingest table2graph`, the `ingest` group docstring)
- Test: `test/test_table2graph_pending.py` (new)

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_table2graph_pending.py`:

```python
"""`artmind ingest table2graph --pending` (spec 2026-09-30 §6 X4): the
registered tables a mapping matches whose projection is missing, or older
than the table's last refresh or the mapping's last change.

Real structured store and registry, real mappings on disk, the real
`table2graph` build and staging. Only Neo4j is stood in for: the document
commit and the batched rebuild are recorded, never sent (CLAUDE.md §3)."""
import json

import pytest
from click.testing import CliRunner

from artmind import table2graph as t2g

SCHEMA = {
    "name": "banking",
    "entity_types": {"ACCOUNT": {"kind": "recurrent", "properties": {"account_id": {}, "holder": {}}}},
}


def _mapping_yaml(table: str, *, extra: str = "") -> str:
    return (
        f'table: "{table}"\n'
        "entities:\n"
        "  acct:\n"
        "    class: ACCOUNT\n"
        '    name: "{account_id}"\n'
        "    properties:\n"
        "      account_id: id\n"
        f"{extra}"
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A registry, a structured store, a mappings dir and a KG staging dir
    of the test's own; Neo4j recorded."""
    import artmind.db as db
    import artmind.ingest as ing
    import artmind.temporal as temporal
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    mappings = tmp_path / "table_mappings"
    mappings.mkdir()
    monkeypatch.setattr(paths, "TABLE_MAPPINGS_DIR", mappings)
    monkeypatch.setattr(temporal, "load_schema", lambda domain: SCHEMA)
    commits = []

    def _write(directory, domain, defer_rebuild=False):
        commits.append((directory.name, domain))
        return {"deferred_keys": [], "unembedded_chunk_ids": []}

    monkeypatch.setattr(ing, "_write_to_neo4j", _write)
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: {"keys": len(keys)})
    return tmp_path, mappings, commits


def _load(tmp_path, table: str, rows: str) -> None:
    from artmind.structured.pipeline import ingest_structured_file

    source = tmp_path / f"{table}.csv"
    source.write_text(rows)
    assert ingest_structured_file(source, "banking")["status"] == "ok"


def _project(table: str) -> None:
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "table2graph", table, "--noEmbed", "--compact"])
    assert result.exit_code == 0, result.output


def _pending() -> list[dict]:
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "table2graph", "--pending", "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _build(tmp_path, mappings) -> None:
    """One table per reason, plus a current one and an unmapped one."""
    for table in ("never_projected", "current", "refreshed", "remapped", "recommented"):
        _load(tmp_path, table, "id,holder\n1,Ann\n")
        (mappings / f"{table}.yaml").write_text(_mapping_yaml(table))
    _load(tmp_path, "unmapped", "id\n1\n")
    _load(tmp_path, "twice", "id\n1\n")
    (mappings / "twice_a.yaml").write_text(_mapping_yaml("twice"))
    (mappings / "twice_b.yaml").write_text(_mapping_yaml("tw*"))
    for table in ("current", "refreshed", "remapped", "recommented"):
        _project(table)
    _load(tmp_path, "refreshed", "id,holder\n1,Ann\n2,Bo\n")
    (mappings / "remapped.yaml").write_text(_mapping_yaml("remapped", extra="      holder: holder\n"))
    (mappings / "recommented.yaml").write_text("# only a comment changed\n" + _mapping_yaml("recommented"))


def test_lists_each_out_of_date_projection_with_its_reason(env):
    tmp_path, mappings, _ = env
    _build(tmp_path, mappings)

    assert _pending() == [
        {"table": "never_projected", "domain": "banking", "mapping": "never_projected.yaml", "reason": "missing"},
        {"table": "refreshed", "domain": "banking", "mapping": "refreshed.yaml", "reason": "table_refreshed"},
        {"table": "remapped", "domain": "banking", "mapping": "remapped.yaml", "reason": "mapping_changed"},
        {"table": "twice", "domain": "banking", "mapping": "twice_a.yaml, twice_b.yaml", "reason": "ambiguous"},
    ]


def test_equals_what_a_following_table2graph_changes(env):
    """The contract: projecting every mapped table rewrites the staged
    projection of exactly the tables `--pending` listed (the other runs
    rebuild byte-identical files), and afterwards only the ambiguous table
    -- which `table2graph` refuses -- is left."""
    from artmind import sync_state
    from artmind.structured import registry

    tmp_path, mappings, _ = env
    _build(tmp_path, mappings)
    listed = {e["table"] for e in _pending() if e["reason"] != "ambiguous"}

    changed = set()
    for row in registry.list_tables():
        if len(t2g.find_mappings(row["table_name"], row["domain"])) != 1:
            continue
        folder = t2g.stage_dir(row["domain"], row["table_name"])
        before = sync_state.folder_fingerprint(folder) if folder.is_dir() else None
        _project(row["table_name"])
        if sync_state.folder_fingerprint(folder) != before:
            changed.add(row["table_name"])

    assert changed == listed
    assert [e["reason"] for e in _pending()] == ["ambiguous"]


def test_a_projection_from_before_the_digest_is_judged_on_its_mapping_name(env):
    """A projection staged before `table_mapping_sha256` was recorded is not
    reported as `mapping_changed` merely for lacking it."""
    tmp_path, mappings, _ = env
    _load(tmp_path, "legacy", "id\n1\n")
    (mappings / "legacy.yaml").write_text(_mapping_yaml("legacy"))
    _project("legacy")
    document_path = t2g.stage_dir("banking", "legacy") / "document.json"
    document = json.loads(document_path.read_text())
    del document["table_mapping_sha256"]
    document_path.write_text(json.dumps(document))

    assert _pending() == []

    document["table_mapping"] = "some_other.yaml"
    document_path.write_text(json.dumps(document))
    assert [e["reason"] for e in _pending()] == ["mapping_changed"]


def test_every_projection_records_its_mapping_digest(env):
    tmp_path, mappings, commits = env
    _load(tmp_path, "accounts", "id\n1\n")
    (mappings / "accounts.yaml").write_text(_mapping_yaml("accounts"))

    _project("accounts")

    document = json.loads((t2g.stage_dir("banking", "accounts") / "document.json").read_text())
    assert document["table_mapping"] == "accounts.yaml"
    assert document["table_mapping_sha256"] == t2g.load_mapping(mappings / "accounts.yaml").digest
    assert commits == [("table__accounts", "banking")]


def test_the_digest_ignores_comments_and_key_order_but_not_content():
    base = t2g.parse_mapping({"table": "t", "entities": {"a": {"class": "C", "name": "{x}"}}})
    reordered = t2g.parse_mapping({"entities": {"a": {"name": "{x}", "class": "C"}}, "table": "t"})
    edited = t2g.parse_mapping({"table": "t", "entities": {"a": {"class": "C", "name": "{y}"}}})

    assert base.digest == reordered.digest
    assert base.digest != edited.digest


def test_pending_writes_nothing(env):
    tmp_path, mappings, commits = env
    _load(tmp_path, "accounts", "id\n1\n")
    (mappings / "accounts.yaml").write_text(_mapping_yaml("accounts"))

    assert [e["reason"] for e in _pending()] == ["missing"]
    assert commits == []
    assert not (tmp_path / "kg").exists()


def test_domain_scopes_the_listing(env):
    from artmind.cli import cli

    tmp_path, mappings, _ = env
    _load(tmp_path, "accounts", "id\n1\n")
    (mappings / "accounts.yaml").write_text(_mapping_yaml("accounts"))

    other = CliRunner().invoke(cli, ["ingest", "table2graph", "--pending", "--domain", "fiction", "--compact"])
    mine = CliRunner().invoke(cli, ["ingest", "table2graph", "--pending", "--domain", "banking", "--compact"])

    assert json.loads(other.stdout) == []
    assert [e["table"] for e in json.loads(mine.stdout)] == ["accounts"]


def test_pending_takes_no_table(env):
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "table2graph", "accounts", "--pending"])

    assert result.exit_code != 0
    assert "--pending takes no TABLE" in result.output


def test_table2graph_still_needs_a_table_without_pending(env):
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "table2graph"])

    assert result.exit_code != 0
    assert "Give one or more TABLEs to project, or --pending" in result.output


def test_a_mapping_that_does_not_parse_fails_the_listing(env):
    """As it fails `table2graph` itself: silently skipping it would report
    "nothing waiting" about tables it plainly names."""
    from artmind.cli import cli

    tmp_path, mappings, _ = env
    _load(tmp_path, "accounts", "id\n1\n")
    (mappings / "broken.yaml").write_text("table: [unclosed\n")

    result = CliRunner().invoke(cli, ["ingest", "table2graph", "--pending"])

    assert result.exit_code != 0
    assert "invalid YAML" in result.output
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_table2graph_pending.py -q`
Expected: `10 failed` — the CLI's usage box ("No such option '--pending'. Did you mean '--mapping'?") where JSON or the new messages were expected (`AssertionError`, `JSONDecodeError`), `KeyError: 'table_mapping_sha256'` (no digest in the staged document yet) and `AttributeError: 'TableMapping' object has no attribute 'digest'`.

- [ ] **Step 3: The mapping digest, recorded on every projection**

**Replace in** `artmind/table2graph.py`:

```python
import fnmatch
import math
```

**with:**

```python
import fnmatch
import hashlib
import json
import math
```

**Replace in** `artmind/table2graph.py` (`TableMapping`):

```python
    valid_from: str
    entities: dict[str, EntitySpec]
    relationships: list[RelationshipSpec]

    def matches(self, table_name: str, domain: str | None = None) -> bool:
```

**with:**

```python
    valid_from: str
    entities: dict[str, EntitySpec]
    relationships: list[RelationshipSpec]
    #: sha256 of the parsed mapping (`mapping_digest`), recorded on every
    #: projection's Document as `table_mapping_sha256`: `ingest table2graph
    #: --pending` compares it to tell an edited mapping from an unchanged one.
    digest: str = ""

    def matches(self, table_name: str, domain: str | None = None) -> bool:
```

**Replace in** `artmind/table2graph.py`:

```python
def parse_mapping(data: dict, path: Path | None = None) -> TableMapping:
```

**with:**

```python
def mapping_digest(data) -> str:
    """sha256 of a mapping document as parsed -- key order, comments and
    whitespace do not count, so only an edit that can change what the
    mapping builds changes it. The same whether the mapping was read from
    the working tree (`load_mapping`) or from a commit (`vault sync`)."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_mapping(data: dict, path: Path | None = None) -> TableMapping:
```

**Replace in** `artmind/table2graph.py` (the end of `parse_mapping`):

```python
        valid_from=str(document.get("valid_from", "$ingested_at")),
        entities=entities,
        relationships=relationships,
    )
```

**with:**

```python
        valid_from=str(document.get("valid_from", "$ingested_at")),
        entities=entities,
        relationships=relationships,
        digest=mapping_digest(data),
    )
```

**Replace in** `artmind/table2graph.py` (in `build_staged`):

```python
    if mapping.path:
        document["table_mapping"] = mapping.path.name
```

**with:**

```python
    if mapping.path:
        document["table_mapping"] = mapping.path.name
    document["table_mapping_sha256"] = mapping.digest
```

- [ ] **Step 4: Which projections are out of date**

**Replace in** `artmind/table2graph.py`:

```python
# ── validation against the schema and the table ──────────────────────────────
```

**with:**

```python
# ── which projections are out of date (spec 2026-09-30 §6 X4) ────────────────


def projection_staleness(table: dict, mapping: TableMapping) -> str | None:
    """Why `table`'s projection through `mapping` is out of date, or None.

    The projection is this machine's staging folder for the table
    (`stage_dir`: `kg/<domain>/table__<table>/`, gitignored), whose
    `document.json` every `table2graph` run -- and every `vault sync` that
    re-projects the table -- rewrites before committing it to the graph:

    - `missing`: no `document.json` -- never projected here;
    - `table_refreshed`: its `version` is not the registry's -- the table
      was re-ingested or refreshed since (`register_table` bumps it on every
      load, and `.meta.json` carries it between machines);
    - `mapping_changed`: it names another mapping file, or its
      `table_mapping_sha256` is not this mapping's digest. A projection made
      before the digest was recorded carries none, and is judged on the
      file name alone.
    """
    staged = stage_dir(table["domain"], table["table_name"]) / "document.json"
    try:
        document = json.loads(staged.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "missing"
    if not isinstance(document, dict):
        return "missing"
    if int(document.get("version") or 1) != int(table.get("version") or 1):
        return "table_refreshed"
    if mapping.path and document.get("table_mapping") != mapping.path.name:
        return "mapping_changed"
    recorded = document.get("table_mapping_sha256")
    if recorded is not None and recorded != mapping.digest:
        return "mapping_changed"
    return None


def pending_projections(tables: list[dict] | None = None, mappings_dir: Path | None = None) -> list[dict]:
    """`[{table, domain, mapping, reason}]` for every registered table a
    mapping matches whose projection is out of date (`projection_staleness`).
    A table two mappings match is listed as `ambiguous` (with both names):
    `table2graph` refuses it until one pattern is narrowed. A table no
    mapping matches is a structured-store table only, and never listed.
    Read-only. Raises `MappingError` for a mapping that does not parse, as
    `table2graph` itself would."""
    if tables is None:
        from artmind.structured import registry

        tables = registry.list_tables()
    out = []
    for table in tables:
        found = find_mappings(table["table_name"], table["domain"], mappings_dir)
        if not found:
            continue
        entry = {"table": table["table_name"], "domain": table["domain"]}
        if len(found) > 1:
            names = ", ".join(m.path.name if m.path else "mapping" for m in found)
            out.append({**entry, "mapping": names, "reason": "ambiguous"})
            continue
        reason = projection_staleness(table, found[0])
        if reason is not None:
            out.append({**entry, "mapping": found[0].path.name if found[0].path else None, "reason": reason})
    return out


# ── validation against the schema and the table ──────────────────────────────
```

- [ ] **Step 5: The `--pending` option**

**Replace in** `artmind/cli.py`:

```python
@ingest.command("table2graph")
@click.argument("tables", nargs=-1, required=True)
```

**with:**

```python
@ingest.command("table2graph")
@click.argument("tables", nargs=-1, required=False)
```

**Replace in** `artmind/cli.py` (the last options, signature, docstring and opening of `ingest_table2graph`):

```python
@click.option("--noEmbed", "no_embed", is_flag=True, help="Skip the post-commit entity/chunk embedding sweeps (run `ingest embed-entities` / `embed-chunks` later).")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_table2graph(
    tables: tuple[str, ...],
    domain: tuple[str, ...],
    mapping_path: str | None,
    as_of: str | None,
    dry_run: bool,
    no_embed: bool,
    compact: bool,
) -> None:
    """Build graph entities and relationships from structured TABLE(s) — no LLM.

    Each row is projected through a declarative table mapping (see the
    artmind-create-schema skill): which schema classes a row yields, how their
    names and properties derive from columns, and which relationships connect
    them. The table becomes a Document and each contributing row a DocChunk,
    so provenance and re-runs behave exactly like a document ingest: running
    it again replaces what the previous run wrote. Use --dryRun first.
    """
    _setup_logger()
    from artmind import table2graph
    from artmind.temporal import load_schema

```

**with:**

```python
@click.option("--noEmbed", "no_embed", is_flag=True, help="Skip the post-commit entity/chunk embedding sweeps (run `ingest embed-entities` / `embed-chunks` later).")
@click.option(
    "--pending", "pending", is_flag=True,
    help="Instead of projecting: list every registered table a mapping matches whose projection is"
    " missing, older than the table's last refresh, or older than the mapping's last change, as"
    " [{table, domain, mapping, reason}]. Read-only. Takes no TABLE; --domain scopes it.",
)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_table2graph(
    tables: tuple[str, ...],
    domain: tuple[str, ...],
    mapping_path: str | None,
    as_of: str | None,
    dry_run: bool,
    no_embed: bool,
    pending: bool,
    compact: bool,
) -> None:
    """Build graph entities and relationships from structured TABLE(s) — no LLM.

    Each row is projected through a declarative table mapping (see the
    artmind-create-schema skill): which schema classes a row yields, how their
    names and properties derive from columns, and which relationships connect
    them. The table becomes a Document and each contributing row a DocChunk,
    so provenance and re-runs behave exactly like a document ingest: running
    it again replaces what the previous run wrote. Use --dryRun first.

    --pending lists the tables waiting for a projection instead (`reason`:
    `missing`, `table_refreshed`, `mapping_changed`, or `ambiguous` when two
    mappings match). It compares the registry and the mapping with this
    machine's staged projection (kg/<domain>/table__<table>/document.json).
    """
    _setup_logger()
    from artmind import table2graph
    from artmind.temporal import load_schema

    if pending:
        if tables or mapping_path or as_of or dry_run:
            raise click.ClickException("--pending takes no TABLE, --mapping, --asOf or --dryRun")
        try:
            rows = structured_registry.list_tables(_parse_domains(domain) if domain else None)
            _echo_json(table2graph.pending_projections(rows), compact)
        except table2graph.MappingError as e:
            raise click.ClickException(str(e))
        return
    if not tables:
        raise click.ClickException("Give one or more TABLEs to project, or --pending to list the tables waiting")

```

**Replace in** `artmind/cli.py`:

```python
    """Manage document ingestion (sync, async, pending, status, results,...) and table -> graph projection (table2graph)."""
```

**with:**

```python
    """Manage document ingestion (sync, async, pending, status, results,...) and table -> graph projection (table2graph; table2graph --pending lists the tables waiting)."""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_table2graph_pending.py test/test_table2graph.py test/test_ingest_fingerprint.py test/test_vault_sync.py -q`
Expected: all pass (`build_staged`'s extra field is inside the fingerprinted `document.json`; `test_vault_sync.py`'s table tests fake `table_to_graph`).

Run: `uv run --group dev pytest test/ -q`
Expected: `2926 passed, 14 skipped, 1 warning`.

- [ ] **Step 7: Commit**

```bash
git add artmind/table2graph.py artmind/cli.py test/test_table2graph_pending.py
git commit -m "feat(table2graph): table2graph --pending, and a mapping digest on every projection" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: the plugin's fixtures, regenerated from the real CLI (spec §9)

`test/test_plugin_fixtures.py` builds one throwaway vault per scenario (`artmind init` on a real git repo, Neo4j in memory, docling and the worker stood in for), runs one command through `CliRunner` with the vault as `cwd` — exactly how the plugin runs it — and compares the normalised record with `obsidian/artmind-obsidian/test/fixtures/<command>.<state>.json`. With `ARTMIND_REGEN_PLUGIN_FIXTURES=1` it writes the file instead. A last test fails on a fixture no scenario produces, so a dropped scenario cannot leave a stale file behind.

A fixture is `{"args", "exit_code", "json", "stderr"}`: `json` is stdout parsed (`null` when empty), `stderr` the rich-click error box, verbatim, from its `╭─ Error` line (the log lines above it are dropped; `COLUMNS=80` fixes the wrapping). `normalise` replaces temp paths (`/vault`, `/artmind-home`, `/home`), each distinct commit sha (`1111…`, `2222…` in order of appearance, 12-character abbreviations included, same length so a box keeps its wrapping), uuids and timestamps; everything else must match exactly.

The 24 fixtures, one per command and state the plugin reads:

- `ingest-async.nothing-pending.json`
- `ingest-async.pending.json`
- `ingest-job-results.done.json`
- `ingest-job-status.done.json`
- `ingest-job-status.running.json`
- `ingest-jobs-active.json`
- `ingest-pending.json`
- `table2graph.dry-run-no-mapping.json`
- `table2graph.dry-run.json`
- `table2graph.pending.json`
- `vault-doctor.json`
- `vault-resolve.dry-run.json`
- `vault-status.behind.json`
- `vault-status.in-sync.json`
- `vault-status.merge-conflict.json`
- `vault-status.neo4j-unreachable.json`
- `vault-status.no-bookmark.json`
- `vault-sync.refusal-artmind-conflicts.json`
- `vault-sync.refusal-bookmark-not-ancestor.json`
- `vault-sync.refusal-bookmark-unpulled.json`
- `vault-sync.refusal-merge-in-progress.json`
- `vault-sync.refusal-no-bookmark.json`
- `vault-sync.refusal-worker-running.json`
- `vault-sync.success.json`

`vault status` covers in sync, behind (two documents and a table), a merge conflict under `.artmind/`, Neo4j unreachable (the graph bookmark read raising `OSError`, as the driver does) and no bookmark yet. `vault sync` covers success and each refusal the plugin maps to an action: the ingest worker running (a real `flock` on the vault's `worker.pid`, which is what `worker_pid.live_pid` tests), a merge in progress, `.artmind/` conflicts without a merge (a conflicting `git stash pop`), a shared graph's bookmark this clone has not pulled, a bookmark not an ancestor of HEAD, and no bookmark. `ingest jobs-active` and `ingest job-results` are included because the plugin reads them to find a job started elsewhere and to show a finished job's failures.

**Files:**
- Test: `test/test_plugin_fixtures.py` (new)
- Create (generated): `obsidian/artmind-obsidian/test/fixtures/*.json`

- [ ] **Step 1: Write the test**

**Create** `test/test_plugin_fixtures.py`:

```python
"""The `--compact` JSON the Obsidian plugin reads, captured from the real CLI
(spec 2026-09-30 §9).

Each scenario builds a throwaway vault, runs one `artmind` command through
`CliRunner` exactly as the plugin would (`cwd` = the vault root), and
compares what it printed with the fixture checked in under
`obsidian/artmind-obsidian/test/fixtures/<name>.json` -- the same file the
plugin's `VaultState`/`ArtmindCli` tests load. A CLI output change therefore
fails HERE, before it silently breaks the plugin.

When a change is intended, regenerate and then run the plugin's tests:

    ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -q
    just obsidian-plugin-test

A fixture is `{"args", "exit_code", "json", "stderr"}`: `json` is stdout
parsed (null when stdout is empty), `stderr` the error box rich-click prints
on a failure (null on success), exactly as the plugin receives it -- log
lines above the box are dropped. Values that differ per run are normalised
the same way on both sides: temp paths, commit shas (each distinct sha
becomes `1111…`, `2222…`, in order of appearance, abbreviations included),
uuids and timestamps.

Hermetic: Neo4j is an in-memory bookmark/fingerprint store plus recorded
writes (as in test_vault_sync.py), docling and the worker are stood in for,
and git, the registry, DuckDB and every artmind file are real.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent.parent / "obsidian" / "artmind-obsidian" / "test" / "fixtures"
REGENERATE = os.environ.get("ARTMIND_REGEN_PLUGIN_FIXTURES") == "1"

MANIFEST = """\
ingest:
  mappings:
    - path: "Notes/**"
      domain: general
    - path: "Team/**"
      domain: general
"""

TEAM_MAPPING = """\
table: "team"
domain: general
entities:
  person:
    class: PERSON
    name: "{full_name}"
    properties:
      full_name: {column: name, required: true}
      affiliation: employer
      seniority: level
  org:
    class: ORGANIZATION
    name: "{org_name}"
    properties:
      org_name: {column: employer, required: true}
  manager:
    class: PERSON
    lookup: {entity: person, column: manager, match_column: name, on_unresolved: stub, on_ambiguous: skip}
    name: "{manager_name}"
    properties:
      manager_name: manager
relationships:
  - {source: person, rel_type: works_at, target: org}
  - {source: person, rel_type: reports_to, target: manager}
"""


# ── the vault ────────────────────────────────────────────────────────────────


class FakeGraph:
    """What one Neo4j holds for `vault sync`: bookmarks and fingerprints."""

    def __init__(self):
        self.bookmarks: dict[str, str] = {}
        self.fingerprints: dict[str, str | None] = {}
        self.unreachable = False

    def read_graph_bookmark(self, vault_id, *, timeout=None):
        if self.unreachable:
            raise OSError("Couldn't connect to 127.0.0.1:7687 (reason [Errno 61] Connection refused)")
        return self.bookmarks.get(vault_id)

    def write_graph_bookmark(self, vault_id, commit):
        self.bookmarks[vault_id] = commit

    def read_document_fingerprints(self, doc_ids, *, timeout=None):
        return {d: self.fingerprints[d] for d in doc_ids if d in self.fingerprints}


class Vault:
    def __init__(self, root: Path, graph: FakeGraph):
        self.root = root
        self.graph = graph

    def git(self, *args: str, check: bool = True) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=check
        ).stdout.strip()

    def write(self, rel: str, content: str | bytes) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def run(self, *args: str) -> dict:
        from artmind.cli import cli

        result = CliRunner().invoke(cli, list(args), catch_exceptions=False)
        stdout = result.stdout.strip()
        box = result.stderr[result.stderr.find("╭─ Error"):] if "╭─ Error" in result.stderr else ""
        return {
            "args": list(args),
            "exit_code": result.exit_code,
            "json": json.loads(stdout) if stdout else None,
            "stderr": box.rstrip() or None,
        }

    def sync_bootstrap(self) -> None:
        assert self.run("vault", "sync", "--bootstrapSynced", "--compact")["exit_code"] == 0

    def kg_doc(self, name: str, doc_id: str) -> None:
        """A committed KG staging folder, as an extraction leaves one."""
        folder = f".artmind/data/kg/general/{name}"
        self.write(f"{folder}/document.json", json.dumps({"id": doc_id, "name": f"{name}.md"}))
        for part in ("chunks", "observations", "relationships"):
            self.write(f"{folder}/{part}.json", "[]")

    def conflict(self, base: dict, ours: dict, theirs: dict) -> None:
        """`base` committed, `theirs` on a branch, `ours` on main, then the
        branch merged into main -- the merge left in progress."""
        for rel, content in base.items():
            self.write(rel, content)
        self.commit("base")
        self.git("checkout", "-qb", "theirs")
        for rel, content in theirs.items():
            self.write(rel, content)
        self.commit("theirs")
        self.git("checkout", "-q", "main")
        for rel, content in ours.items():
            self.write(rel, content)
        self.commit("ours")
        self.git("merge", "--no-edit", "theirs", check=False)
        assert (self.root / ".git" / "MERGE_HEAD").exists()


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """An `artmind init`ed git vault with one commit, as the plugin finds it,
    every path constant pointed into it, and Neo4j in memory."""
    import artmind.db as db
    import artmind.document_identity as di
    import artmind.ingest as ing
    import artmind.table2graph as t2g
    import artmind.vault_git as vg
    import paths
    from artmind import sync_state
    from artmind.cli import cli

    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("ARTMIND_KG_NEO4J_URI", "neo4j://127.0.0.1:7687")
    monkeypatch.setenv("ARTMIND_KG_NEO4J_DATABASE", "neo4j")
    for key, value in {
        "GIT_AUTHOR_DATE": "2026-09-30T12:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-09-30T12:00:00+00:00",
    }.items():
        monkeypatch.setenv(key, value)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Laptop A"], cwd=root, check=True)
    assert CliRunner().invoke(cli, ["init"]).exit_code == 0

    data = root / ".artmind" / "data"
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", root)
    monkeypatch.setattr(paths, "KG_DIR", data / "kg")
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", data / "structured_text")
    monkeypatch.setattr(paths, "TABLE_MAPPINGS_DIR", root / ".artmind" / "domains" / "table_mappings")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "WORKER_PID_FILE", tmp_path / "worker.pid")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "document_registry.db")
    for module in (ing, di, vg):
        monkeypatch.setattr(module, "ARTMIND_VAULT_DIR", root)
    monkeypatch.setattr(ing, "KG_DIR", data / "kg")
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", data / "documents" / "markdowns")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", lambda dest, model: (f"# {dest.stem}\n", {}))

    graph = FakeGraph()
    for name in ("read_graph_bookmark", "write_graph_bookmark", "read_document_fingerprints"):
        monkeypatch.setattr(sync_state, name, getattr(graph, name))
    monkeypatch.setattr(ing, "_write_to_neo4j", lambda folder, domain, defer_rebuild=False: {
        "chunks": 1, "observations": 2, "relationships": 1, "retracted": 0,
        "deferred_keys": [], "unembedded_chunk_ids": [],
    })
    monkeypatch.setattr(ing, "retract_document", lambda doc_id, domain: {"doc_id": doc_id, "affected_keys": []})
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: {"rebuilt": len(keys), "keys": len(keys)})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: 0)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda chunk_ids=None, domain=None: 0)

    import artmind.cli as cli_module

    monkeypatch.setattr(cli_module, "_ensure_worker_running", lambda: None)

    v = Vault(root, graph)
    v.write(".artmind/vault.yaml", (root / ".artmind" / "vault.yaml").read_text() + MANIFEST)
    v.write("Notes/welcome.md", "Welcome to the vault.\n")
    v.commit("init")
    return v


def _hold_worker_lock(vault: Vault):
    """A live ingest worker, as `worker_pid.live_pid` sees one: an exclusive
    flock on the vault's pid file."""
    from artmind.vault import VaultLayout

    pid_file = VaultLayout(vault.root).worker_pid
    handle = open(pid_file, "a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    handle.write("4242")
    handle.flush()
    return handle


def _behind(vault: Vault) -> None:
    """Synced, then the other laptop's commit pulled: two documents and one
    structured table this machine's stores have not applied."""
    from artmind.structured.pipeline import ingest_structured_file

    vault.sync_bootstrap()
    vault.kg_doc("quarterly_review", "doc-quarterly-review")
    vault.kg_doc("hiring_plan", "doc-hiring-plan")
    source = vault.write("Team/team.csv", "name,employer,level\nAnn Lee,Acme,senior\nBo Chan,Acme,junior\n")
    ingest_structured_file(source, "general")
    vault.commit("from laptop B")


def _table_registered_and_mapped(vault: Vault) -> None:
    from artmind.structured.pipeline import ingest_structured_file

    source = vault.write(
        "Team/team.csv",
        "name,employer,level,manager\n"
        "Ann Lee,Acme,senior,\n"
        "Bo Chan,Initech,junior,Ann Lee\n"
        ",Nobody,junior,\n"
        "Cy Dee,Acme,junior,Zed Unknown\n",
    )
    ingest_structured_file(source, "general")
    vault.write(".artmind/domains/table_mappings/team.yaml", TEAM_MAPPING)


def _jobs(states: list[tuple[str, str | None]], *, status: str, processed: int) -> str:
    """A job as the worker leaves it: its row and one row per file."""
    from artmind.jobs import _create_job, _update_job_file_status, _update_job_status

    files = [f"/vault/Notes/file{i}.md" for i in range(1, len(states) + 1)]
    job_id = _create_job(files, domain=None)
    _update_job_status(job_id, status=status, processed_count=processed, started_at="2026-09-30T12:00:00")
    if status in ("completed", "failed"):
        _update_job_status(job_id, completed_at="2026-09-30T12:05:00")
    for path, (file_status, error) in zip(files, states):
        _update_job_file_status(
            job_id, path, status=file_status,
            current_step="extract_kg" if file_status == "processing" else None,
            error_message=error,
        )
    return job_id


# ── the scenarios: one per fixture ───────────────────────────────────────────


def s_vault_status_no_bookmark(vault):
    return vault.run("vault", "status", "--compact")


def s_vault_status_in_sync(vault):
    vault.sync_bootstrap()
    return vault.run("vault", "status", "--compact")


def s_vault_status_behind(vault):
    _behind(vault)
    return vault.run("vault", "status", "--compact")


def s_vault_status_merge_conflict(vault):
    vault.sync_bootstrap()
    vault.conflict(
        {".artmind/same_as.yaml": "groups: []\n"},
        {".artmind/same_as.yaml": "groups: [[a, b]]\n"},
        {".artmind/same_as.yaml": "groups: [[c, d]]\n"},
    )
    return vault.run("vault", "status", "--compact")


def s_vault_status_neo4j_unreachable(vault):
    vault.sync_bootstrap()
    vault.graph.unreachable = True
    return vault.run("vault", "status", "--compact")


def s_vault_sync_success(vault):
    _behind(vault)
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_worker_running(vault):
    vault.sync_bootstrap()
    handle = _hold_worker_lock(vault)
    try:
        return vault.run("vault", "sync", "--compact")
    finally:
        handle.close()


def s_vault_sync_refusal_merge_in_progress(vault):
    vault.sync_bootstrap()
    vault.conflict(
        {"Notes/plan.md": "Plan v1.\n"},
        {"Notes/plan.md": "Plan, ours.\n"},
        {"Notes/plan.md": "Plan, theirs.\n"},
    )
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_artmind_conflicts(vault):
    """Unmerged paths under `.artmind/` with no merge in progress: what a
    conflicting `git stash pop` leaves."""
    vault.sync_bootstrap()
    vault.write(".artmind/same_as.yaml", "groups: []\n")
    vault.commit("same_as")
    vault.write(".artmind/same_as.yaml", "groups: [[a, b]]\n")
    vault.git("stash")
    vault.write(".artmind/same_as.yaml", "groups: [[c, d]]\n")
    vault.commit("other edit")
    vault.git("stash", "pop", check=False)
    assert not (vault.root / ".git" / "MERGE_HEAD").exists()
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_bookmark_unpulled(vault):
    """A shared graph's bookmark set by the other laptop, past anything this
    clone has pulled."""
    from artmind.manifest import read_vault_id

    vault.sync_bootstrap()
    vault.graph.bookmarks[read_vault_id(vault.root)] = "9" * 40
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_bookmark_not_ancestor(vault):
    """The structured bookmark on a commit HEAD's history does not contain
    (history rewritten, or a diverged branch)."""
    from artmind.vault import VaultLayout, write_state

    vault.sync_bootstrap()
    vault.git("checkout", "-qb", "elsewhere")
    vault.write("Notes/elsewhere.md", "Elsewhere.\n")
    elsewhere = vault.commit("elsewhere")
    vault.git("checkout", "-q", "main")
    write_state(VaultLayout(vault.root), {"last_structured_commit": elsewhere})
    return vault.run("vault", "sync", "--compact")


def s_vault_sync_refusal_no_bookmark(vault):
    return vault.run("vault", "sync", "--compact")


def s_vault_resolve_dry_run(vault):
    """One unit resolved (a KG folder whose note merged cleanly), one pending
    (its note is still conflicted), one reported (same_as.yaml)."""
    from artmind.document_identity import compute_content_sha256

    def note(doc_id, body):
        return f"---\n_artmind_id: {doc_id}\n_domain: general\n---\n\n{body}"

    def folder(name, doc_id, body, obs):
        document = {"id": doc_id, "version": 2, "content_sha256": compute_content_sha256(body),
                    "source_path": f"Notes/{name}.md", "source_type": "md"}
        base = f".artmind/data/kg/general/{name}"
        return {f"{base}/document.json": json.dumps(document, indent=2), f"{base}/observations.json": obs,
                f"{base}/chunks.json": "[]", f"{base}/relationships.json": "[]"}

    vault.conflict(
        {"Notes/policy.md": note("id-policy", "Policy v1.\n"), **folder("policy", "id-policy", "Policy v1.\n", "[0]"),
         "Notes/memo.md": note("id-memo", "Memo v1.\n"), **folder("memo", "id-memo", "Memo v1.\n", "[0]"),
         ".artmind/same_as.yaml": "groups: []\n"},
        {**folder("policy", "id-policy", "Policy v1.\n", "[1]"),
         "Notes/memo.md": note("id-memo", "Memo, ours.\n"), **folder("memo", "id-memo", "Memo, ours.\n", "[1]"),
         ".artmind/same_as.yaml": "groups: [[a, b]]\n"},
        {"Notes/policy.md": note("id-policy", "Policy v2.\n"), **folder("policy", "id-policy", "Policy v2.\n", "[2]"),
         "Notes/memo.md": note("id-memo", "Memo, theirs.\n"), **folder("memo", "id-memo", "Memo, theirs.\n", "[2]"),
         ".artmind/same_as.yaml": "groups: [[c, d]]\n"},
    )
    return vault.run("vault", "resolve", "--dryRun", "--compact")


def s_vault_doctor(vault):
    vault.git("config", "pull.rebase", "true")
    return vault.run("vault", "doctor", "--compact")


def s_ingest_pending(vault):
    vault.write("Notes/new_idea.md", "A new idea.\n")
    vault.write("Team/org_chart.pdf", b"%PDF-1.4 org chart")
    vault.write("Team/team.csv", "name,employer,level\nAnn Lee,Acme,senior\n")
    vault.write("Drafts/unmapped.md", "Not mapped, never listed.\n")
    return vault.run("ingest", "pending", "--compact")


def s_ingest_async_pending(vault):
    vault.write("Notes/new_idea.md", "A new idea.\n")
    vault.write("Team/org_chart.pdf", b"%PDF-1.4 org chart")
    return vault.run("ingest", "async", "--pending", "--compact")


def s_ingest_async_nothing_pending(vault):
    import artmind.ingest as ing
    from conftest import stage_as_extracted

    result = ing.ingest_file(vault.root / "Notes/welcome.md", "image-model", "general")
    stage_as_extracted(ing.KG_DIR, result, "general")
    return vault.run("ingest", "async", "--pending", "--compact")


def s_ingest_job_status_running(vault):
    job_id = _jobs(
        [("completed", None), ("completed", None), ("processing", None), ("queued", None), ("queued", None)],
        status="processing", processed=2,
    )
    return vault.run("ingest", "job-status", job_id, "--compact")


def s_ingest_job_status_done(vault):
    job_id = _jobs(
        [("completed", None), ("completed", None), ("completed", None), ("completed", None),
         ("failed", "KG ingestion failed")],
        status="failed", processed=5,
    )
    return vault.run("ingest", "job-status", job_id, "--compact")


def s_ingest_jobs_active(vault):
    """A job started elsewhere (a terminal, the admin-ui): the plugin finds
    it here and follows it."""
    _jobs([("completed", None), ("processing", None), ("queued", None)], status="processing", processed=1)
    return vault.run("ingest", "jobs-active", "--compact")


def s_ingest_job_results_done(vault):
    job_id = _jobs(
        [("completed", None), ("failed", "KG ingestion failed")],
        status="failed", processed=2,
    )
    return vault.run("ingest", "job-results", job_id, "--compact")


def s_table2graph_dry_run(vault):
    _table_registered_and_mapped(vault)
    return vault.run("ingest", "table2graph", "team", "--dryRun", "--compact")


def s_table2graph_dry_run_no_mapping(vault):
    from artmind.structured.pipeline import ingest_structured_file

    ingest_structured_file(vault.write("Team/budget.csv", "item,cost\nlaptops,1200\n"), "general")
    return vault.run("ingest", "table2graph", "budget", "--dryRun", "--compact")


def s_table2graph_pending(vault):
    _table_registered_and_mapped(vault)
    return vault.run("ingest", "table2graph", "--pending", "--compact")


SCENARIOS = {
    name[2:].replace("_", "-"): fn for name, fn in sorted(globals().items()) if name.startswith("s_") and callable(fn)
}

def fixture_name(scenario: str) -> str:
    """`vault-status-in-sync` -> `vault-status.in-sync`: the command, a dot,
    then the state."""
    for command in ("vault-status", "vault-sync", "vault-resolve", "vault-doctor", "ingest-pending",
                    "ingest-async", "ingest-job-status", "ingest-job-results", "ingest-jobs-active", "table2graph"):
        if scenario == command:
            return command
        if scenario.startswith(command + "-"):
            return f"{command}.{scenario[len(command) + 1:]}"
    raise AssertionError(f"scenario {scenario!r} names no known command")


# ── normalisation ────────────────────────────────────────────────────────────

_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_ABBREVIATED_SHA = re.compile(r"\b[0-9a-f]{12}\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?")


def normalise(record: dict, vault: Vault) -> dict:
    """The same record from any run: see the module docstring."""
    import paths

    text = json.dumps(record, ensure_ascii=False)
    for real, fake in (
        (str(vault.root.resolve()), "/vault"),
        (str(vault.root), "/vault"),
        (str(Path(paths.ARTMIND_HOME).resolve()), "/artmind-home"),
        (str(paths.ARTMIND_HOME), "/artmind-home"),
        (str(Path.home().resolve()), "/home"),
        (str(Path.home()), "/home"),
    ):
        text = text.replace(real, fake)
    shas: dict[str, str] = {}
    for sha in _SHA.findall(text):
        shas.setdefault(sha, (format(len(shas) + 1, "x") * 40)[:40])
    for sha, fake in shas.items():
        text = text.replace(sha, fake).replace(sha[:12], fake[:12])
    # An abbreviation of a commit named nowhere in full (a refusal's own
    # message): same length, so a rich-click error box keeps its wrapping.
    for short in _ABBREVIATED_SHA.findall(text):
        if short not in {fake[:12] for fake in shas.values()}:
            shas.setdefault(short, (format(len(shas) + 1, "x") * 40)[:40])
            text = text.replace(short, shas[short][:12])
    uuids: dict[str, str] = {}
    for found in _UUID.findall(text):
        uuids.setdefault(found, f"00000000-0000-4000-8000-{len(uuids) + 1:012d}")
    for found, fake in uuids.items():
        text = text.replace(found, fake)
    text = _TIMESTAMP.sub("2026-09-30T12:00:00", text)
    return json.loads(text)


# ── the check ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_fixture_matches_the_cli(scenario, vault):
    record = normalise(SCENARIOS[scenario](vault), vault)
    path = FIXTURES / f"{fixture_name(scenario)}.json"
    if REGENERATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert path.is_file(), f"{path.name} is missing -- regenerate (see this module's docstring)"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert record == stored, (
        f"`artmind {' '.join(record['args'])}` no longer prints what {path.name} holds. If the change "
        "is intended, regenerate the fixtures and run the plugin's tests (this module's docstring)."
    )


def test_every_fixture_has_a_scenario():
    """A fixture no scenario regenerates would go stale unnoticed."""
    expected = {f"{fixture_name(s)}.json" for s in SCENARIOS}
    present = {p.name for p in FIXTURES.glob("*.json")}
    assert present == expected
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_plugin_fixtures.py -q`
Expected: `25 failed` — every scenario with `AssertionError: <name>.json is missing -- regenerate (see this module's docstring)`, and `test_every_fixture_has_a_scenario` (no fixture directory yet).

- [ ] **Step 3: Generate the fixtures**

Run: `ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -q`
Expected: `25 passed`, and `ls obsidian/artmind-obsidian/test/fixtures/` lists the 24 files above.

Read three of them before going on: `vault-status.behind.json` must show `"state": "behind"` with `"docs": 2` and one table for the graph store; `vault-sync.refusal-worker-running.json` must hold an error box whose text starts "the ingest worker is running"; `table2graph.dry-run.json` must list the `manager` lookup with `"unresolved_values": ["Zed Unknown"]`.

- [ ] **Step 4: Run the comparison, twice, to verify it passes and is stable**

Run: `uv run --group dev pytest test/test_plugin_fixtures.py -q` (twice)
Expected: `25 passed` both times.

Run: `uv run --group dev pytest test/ -q`
Expected: `2951 passed, 14 skipped, 1 warning`.

- [ ] **Step 5: Commit**

```bash
git add test/test_plugin_fixtures.py obsidian/artmind-obsidian/test/fixtures
git commit -m "test: the Obsidian plugin's CLI fixtures, regenerated from the real CLI" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: docs, the skill, the justfile, and version 0.9.0

CLAUDE.md §4: the new commands go into the prose that describes them. The version moves to 0.9.0, the plugin's `MIN_ARTMIND_VERSION` (Plan 2): the first artmind with X1–X4.

**Files:**
- Modify: `pyproject.toml` (and `uv.lock`, rewritten by `uv`)
- Modify: `docs/vault.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md`, `justfile`

- [ ] **Step 1: Bump the version**

**Replace in** `pyproject.toml`:

```toml
version = "0.8.0"
```

**with:**

```toml
version = "0.9.0"
```

Run: `uv run --group dev pytest test/test_cli_version.py -q`
Expected: `3 passed` — `uv run` re-syncs the editable install, so the installed metadata now says 0.9.0, and `uv.lock`'s `artmind9` entry now reads `version = "0.9.0"` (`git diff uv.lock` shows that one line). Do not edit `uv.lock` by hand.

- [ ] **Step 2: `docs/vault.md`**

**Replace in** `docs/vault.md`:

```markdown
### Git: Obsidian Git owns transport
```

**with:**

```markdown
### What an ingest would do: `ingest pending`

Until the triggers above exist, the question "what do I need to ingest?" has a
read-only answer. `artmind ingest pending` walks the vault as `ingest async .`
would (mapped folders only, no `_Inbox`, no dot-folders) and applies the
ingest's own decisions to every file -- the same functions, not a copy of them:

| Kind | Listed when | Decided by |
|---|---|---|
| note (`.md`) | its body differs from the one its last extraction staged in `document.json`, or its `_domain` changed | `ingest.plan_vault_native` (`decide_version`) |
| binary (pdf/pptx/docx, images) | its bytes differ from the ones last converted | `ingest.plan_binary` (`source_sha256`) |
| table (csv/xlsx) | its bytes differ from the ones registered | `structured.pipeline.is_unchanged` |

Each entry is a vault-relative `path` with a `reason`, `new` or `changed`. A
note that only moved is not listed: re-ingesting it extracts nothing. Mapped
files the ingest would refuse as they stand (an `_artmind_id` collision, a
`_domain` that is not a folder name) are listed under `errors`. `artmind
ingest async --pending` submits exactly the listed files as one job (and
prints `{"job_id": null, "file_count": 0}` when there are none), so a file
is ingested only when it changed -- the cost rule above, kept by hand.

Tables have a second step. `artmind ingest table2graph --pending` lists every
registered table a mapping matches whose projection on this machine is
`missing`, `table_refreshed` (the registry's `version` moved past the one the
projection recorded), `mapping_changed` (the projection recorded another
mapping file, or another `table_mapping_sha256` -- a digest of the parsed
mapping, so a comment edit does not count), or `ambiguous` (two mappings
match). The projection it compares with is the staged
`kg/<domain>/table__<table>/document.json` that every `table2graph` run, and
every `vault sync` that re-projects the table, rewrites.

The Obsidian plugin (`obsidian/artmind-obsidian/`,
`docs/superpowers/specs/2026-09-30-obsidian-plugin-design.md`) runs these,
`vault status`/`sync`/`resolve`/`doctor` and `artmind --version`, always
with `--compact`. The JSON it reads is pinned by fixtures under
`obsidian/artmind-obsidian/test/fixtures/`, regenerated from the real CLI by
`test/test_plugin_fixtures.py` -- change a command's output and that test
fails until the fixtures (and the plugin) follow.

### Git: Obsidian Git owns transport
```

- [ ] **Step 3: The ingestion-helper skill**

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (Situation A):

```markdown
Then track it with the admin UI's dashboard (`artmind admin-ui`, then open `/dashboard`) or `artmind ingest job-status JOB_ID`.
```

**with:**

````markdown
Then track it with the admin UI's dashboard (`artmind admin-ui`, then open `/dashboard`) or `artmind ingest job-status JOB_ID`.

**Just what changed, inside a vault:**
```bash
artmind ingest pending --compact       # read-only: new/changed mapped files, no LLM
artmind ingest async --pending         # one background job for exactly that list
```
`ingest pending` walks the vault the way `ingest async .` does (mapped folders only, no
`_Inbox`, no dot-folders) and applies the ingest's own rules: a note is listed when its body
changed since the extraction staged in its `document.json` (or its `_domain` changed), a
binary when its bytes differ from the ones last converted, a csv/xlsx when its bytes differ
from the ones registered. A note that only moved is not listed (re-ingesting it extracts
nothing). It prints `{notes, binaries, tables, errors}`, each entry a vault-relative `path`
and a `reason` (`new`/`changed`); `errors` names mapped files the ingest would refuse as they
stand (an `_artmind_id` collision, a `_domain` that is not a folder name). With nothing
pending, `ingest async --pending` prints `{"job_id": null, "file_count": 0}` and starts
nothing. This is what the Obsidian plugin's *Ingest what changed* runs.
````

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (Situation J):

```markdown
No `--domain` needed: the table's own registered domain is used (see Situation I). The dry-run
```

**with:**

```markdown
**Which tables are waiting?** `artmind ingest table2graph --pending --compact` lists every
registered table a mapping matches whose projection on this machine is out of date, as
`[{table, domain, mapping, reason}]`: `missing` (never projected here), `table_refreshed` (the
table was re-ingested or refreshed since), `mapping_changed` (the mapping was edited since --
comments and key order do not count), or `ambiguous` (two mappings match; `table2graph`
refuses until one `table:` pattern is narrowed). Read-only; `--domain` scopes it. It compares
the registry and the mapping with the staged projection, `kg/<domain>/table__<table>/document.json`.

No `--domain` needed: the table's own registered domain is used (see Situation I). The dry-run
```

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (Full Pipeline Reference):

```markdown
**Re-run / repair workflow:**
```

**with:**

````markdown
**Vault workflow (what the Obsidian plugin runs):**
```
1. artmind vault sync --compact                      # apply the other laptop's commits first
2. artmind ingest pending --compact                  # what changed here
3. artmind ingest async --pending --compact          # ingest exactly that, in the background
4. artmind ingest table2graph --pending --compact    # tables waiting for the graph
5. artmind ingest table2graph TABLE --dryRun --compact, then without --dryRun
```

**Re-run / repair workflow:**
````

- [ ] **Step 4: The justfile**

**Replace in** `justfile`:

```just
# initialize SQLite tables and Neo4j constraints/indexes (idempotent)
cli-setup:
    uv run artmind setup
```

**with:**

```just
# initialize SQLite tables and Neo4j constraints/indexes (idempotent)
cli-setup:
    uv run artmind setup

# print the installed artmind version (the Obsidian plugin checks it)
cli-version:
    uv run artmind --version
```

**Replace in** `justfile`:

```just
# list recent ingestion jobs  (usage: just ingest-jobs [status])
```

**with:**

```just
# list the mapped files an ingest would do work on (new/changed), read-only  (usage: just ingest-pending)
ingest-pending:
    uv run artmind ingest pending

# submit exactly the pending files as one background job  (usage: just ingest-async-pending)
ingest-async-pending:
    uv run artmind ingest async --pending

# list recent ingestion jobs  (usage: just ingest-jobs [status])
```

**Replace in** `justfile`:

```just
# pull KG JSON from an external GitHub repo  (usage: just ingest-pull-kg <repo_url> <repo_path> <domain>)
```

**with:**

```just
# list registered tables whose graph projection is missing or out of date, read-only  (usage: just ingest-table2graph-pending)
ingest-table2graph-pending:
    uv run artmind ingest table2graph --pending

# pull KG JSON from an external GitHub repo  (usage: just ingest-pull-kg <repo_url> <repo_path> <domain>)
```

Run: `just --list | grep -E "cli-version|ingest-pending|ingest-async-pending|ingest-table2graph-pending"`
Expected: the four recipes, each with its comment.

- [ ] **Step 5: Run the suite**

Run: `uv run --group dev pytest test/ -q`
Expected: `2951 passed, 14 skipped, 1 warning`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock docs/vault.md artmind/skills/artmind-ingestion-helper/SKILL.md justfile
git commit -m "docs: ingest pending, async --pending, table2graph --pending; version 0.9.0" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

The run folder's skill copy only changes with `artmind init` (CLAUDE.md "Testing implications" §2); the user does that in "Live verification".

---

### Task 8: full suite and the real command hierarchy

No code. Confirms the whole plan together, and that the help text tells the truth (CLAUDE.md §4: trust `just dev-cli-help` over prose).

- [ ] **Step 1: The full suite**

Run: `uv run --group dev pytest test/ -q`
Expected: `2951 passed, 14 skipped, 1 warning`.

- [ ] **Step 2: The command hierarchy**

Run: `just dev-cli-help | grep -n -E "COMMAND: cli ingest pending|--pending|--version|--compact" | head -20`
Expected: `cli ingest pending` listed; `--pending` under both `ingest async` and `ingest table2graph`; `--version` on the root; `--compact` on `ingest async`.

Run: `just dev-cli-guide-check`
Expected: `6 passed, 1 warning` (every command routed into the CLI guide).

- [ ] **Step 3: The fixtures are current**

Run: `uv run --group dev pytest test/test_plugin_fixtures.py -q && git status --short obsidian/`
Expected: all pass, and `git status` prints nothing: the checked-in fixtures are exactly what the CLI prints.

Nothing to commit.

---
## Live verification (user)

Hermetic tests cannot see a real vault, a real worker or a real Neo4j (CLAUDE.md §3). Run these after the plan is merged, in a throwaway vault, with the real install. Every command runs in-process (`ARTMIND_NO_PROXY=1` is harmless here — none of them is a `query` — but keeps a stale daemon out of the picture, CLAUDE.md §1). Extraction costs LLM calls: step 3 ingests two small files.

### 0. Install

```bash
cd ~/projects/artmind9 && just dev-install
export ARTMIND_NO_PROXY=1
artmind --version                                # artmind 0.9.0
export LIVE=$HOME/plan1-live && rm -rf "$LIVE" && mkdir -p "$LIVE/v" && cd "$LIVE/v"
git init -q -b main && git config user.email a@example.com && git config user.name A
artmind init && artmind setup                    # local Neo4j, as in docs/USER_GUIDE_TWO_LAPTOPS.md
printf 'ingest:\n  mappings:\n    - path: "Notes/**"\n      domain: general\n' >> .artmind/vault.yaml
mkdir Notes && echo "A first note about Acme and its CEO Ann Lee." > Notes/one.md
git add -A && git commit -qm init && artmind vault sync --bootstrapSynced
```

### 1. `ingest pending` lists the new note

```bash
artmind ingest pending --compact     # {"notes":[{"path":"Notes/one.md","reason":"new"}],"binaries":[],"tables":[],"errors":[]}
```

### 2. Nothing pending submits nothing

```bash
mv Notes/one.md /tmp/ && artmind ingest async --pending --compact   # {"job_id":null,"file_count":0}
mv /tmp/one.md Notes/
```

### 3. `ingest async --pending` ingests exactly that list

```bash
echo "Acme bought Initech in 2025." > Notes/two.md
artmind ingest async --pending --compact          # file_count 2
artmind ingest jobs-active --compact              # the job, until the worker finishes
artmind ingest job-status <job_id> --compact      # completed, 2 files
artmind ingest pending --compact                  # every list empty
echo "An edit to the body." >> Notes/one.md
artmind ingest pending --compact                  # Notes/one.md, "changed"
git mv Notes/two.md Notes/renamed.md 2>/dev/null || mv Notes/two.md Notes/renamed.md
artmind ingest pending --compact                  # still only Notes/one.md: a move is not work
```

### 4. `table2graph --pending`

```bash
printf 'name,employer\nAnn Lee,Acme\n' > Notes/team.csv
artmind ingest sync Notes/team.csv
mkdir -p .artmind/domains/table_mappings
printf 'table: "team"\nentities:\n  person:\n    class: PERSON\n    name: "{n}"\n    properties:\n      n: name\n' > .artmind/domains/table_mappings/team.yaml
artmind ingest table2graph --pending --compact    # [{"table":"team",...,"reason":"missing"}]
artmind ingest table2graph team --noEmbed --compact
artmind ingest table2graph --pending --compact    # []
echo "# a comment only" >> .artmind/domains/table_mappings/team.yaml
artmind ingest table2graph --pending --compact    # [] -- comments do not count
printf 'name,employer\nAnn Lee,Acme\nBo Chan,Initech\n' > Notes/team.csv && artmind ingest sync Notes/team.csv
artmind ingest table2graph --pending --compact    # reason "table_refreshed"
```

### 5. The run folder's skill

```bash
artmind init        # re-seeds ~/.artmind/.claude/skills: the ingestion-helper skill now documents ingest pending
grep -c "ingest pending" ~/.artmind/.claude/skills/artmind-ingestion-helper/SKILL.md   # > 0
```

### Clean up

```bash
rm -rf "$LIVE"
```

---
## Self-review

### Spec → tasks

| Spec item | Task(s) |
|---|---|
| §6 X1 — `artmind --version` prints the package version | 1 (and 7: 0.9.0, the plugin's minimum) |
| §6 X2 — `ingest pending [--compact]`: read-only, no LLM; `{notes, binaries, tables}` with vault-relative paths and `new`/`changed` | 3 (plus `errors`, decision 4) |
| §6 X2 — the same identity/version rules as ingest (body hash, source hash, table bytes): reuse, never duplicate | 2 (the planners the ingest itself now calls), 3 |
| §6 X2 — "exactly what an ingest would do work on", checked against a following ingest | 3 (`test_equals_what_a_following_ingest_does_work_on`) |
| §6 X3 — `ingest async --pending` enqueues exactly X2's list as one job; same JSON as `ingest async`; `{"job_id": null, "file_count": 0}` when nothing is pending | 4 |
| §6 X4 — `table2graph --pending [--compact]`: `[{table, domain, mapping, reason}]`, read-only; "missing, or older than the table's last refresh or the mapping's last change" defined concretely | 5 (decision 6) |
| §6 X4 — checked against what a following `table2graph` does | 5 (`test_equals_what_a_following_table2graph_changes`) |
| §6 — pytest coverage in `test/`, on real throwaway vaults | 1–6 |
| §6 — docs in `docs/vault.md`, the ingestion-helper skill, each command in the `justfile`'s CLI help | 3 and 5 (group docstring), 7, 8 (`dev-cli-help`) |
| §9 — real `--compact` JSON captured from the CLI, checked in as fixtures; a test in `test/` regenerates them against a throwaway vault and fails on drift | 6 |
| §9 — `VaultState` inputs for every state the plugin reads (status in sync/behind/conflict/unreachable, sync success and each refusal, resolve dry run, doctor, ingest async, job status running/done, ingest pending, table2graph dry run and pending) | 6 (25 fixtures, listed there) |

### Placeholder scan

No "TBD", no "similar to Task N", no step without its code or command. Every replaced block quotes the exact old text; the verification script matched each one exactly once, in task order.

### Type and name consistency

`ingest.NotePlan` (`existing_meta`, `body`, `prior_domain`, `effective_domain`, `domain_changed`, `resolution`, `stamp`, `staged_folder`, `staged`, `baseline`, `version`, `.tier`, `.is_new`), `NotePlanError`, `_stamped_view`, `plan_vault_native(source, *, domain, set_domain=None, fork=False, adopt=False)`; `BinaryPlan` (`registered_path`, `existing_meta`, `baseline`, `source_sha256`, `.unchanged`, `.is_new`), `plan_binary(source, effective_domain)` (Task 2) → used by `ingest_pending._classify` (Task 3). `pipeline.registered_tables(source, domain, *, table, sheet, header_row)`, `is_unchanged(rows, sha256)` (Task 2) → Task 3. `ingest_pending.pending(vault_root)`, `pending_files(vault_root, report=None)`, `DEFAULT_DOMAIN`, `KINDS` (Task 3) → `cli._pending_report`, `cli._submit_pending` (Tasks 3–4). `table2graph.mapping_digest`, `TableMapping.digest`, `projection_staleness(table, mapping)`, `pending_projections(tables=None, mappings_dir=None)` (Task 5). JSON keys `notes`/`binaries`/`tables`/`errors`, `path`/`reason`, `job_id`/`file_count`, `table`/`domain`/`mapping`/`reason` are the same in code, tests, fixtures and docs, and are what Plan 2's `types.ts` declares.

### Ordering and green suite

Each task leaves `uv run --group dev pytest test/ -q` green (counts in the verification record). No existing test changes: Task 2 is a pure refactor, and the existing suites are its regression test.

### Design decisions the spec did not settle (for review)

1. **Factor out, don't re-implement.** The spec requires X2 to use "the same identity and version rules as ingest". Anything short of the ingest calling the very function `pending` calls can drift: the note path alone has a six-row identity table, a staged-baseline search with a fallback scan, a pre-R6 frontmatter fallback, and a domain-change override. Task 2 moves that code, verbatim, into `plan_vault_native`/`plan_binary`/`is_unchanged`, and the ingest acts on their result. Cost: `_ingest_vault_native` now catches the planner's three failure kinds (`NotePlanError`, `FrontmatterEditError`, anything from `resolve_identity`) where it caught them inline; an exception from the baseline scan, which used to propagate to the caller (which marked the file failed), is now reported the same way directly.
2. **The stamped body, computed.** The only way to plan without writing and still hash the body the ingest hashes is to compute the stamp (`frontmatter.set_fields` is pure) and decode it as `read_text` would. The ingest now uses that computed body rather than re-reading the file after the stamp, so the two cannot disagree. Pinned by `test_the_planned_hash_is_the_one_the_ingest_records` against the file on disk.
3. **Walk and domain.** X2 walks what `ingest async <vault>` walks — that is the command X3 replaces. A file no mapping covers exists only when the manifest has no mappings at all (then everything is "mapped"); it gets `general`, the worker's fallback. `ingest sync <vault>` would instead refuse such a note ("no `_domain` … and no --domain"); X2 follows the async path because that is what X3 runs.
4. **`errors` in X2's output** (an addition to the spec's `{notes, binaries, tables}`): a mapped file the ingest would refuse as it stands. Listing it as `new` would make X3 enqueue a file that can only fail, every time; leaving it out silently would show "in sync". The plugin's panel lists them.
5. **`--compact` on `ingest async`** (the spec says X3's output is "the same JSON as `ingest async`", which had no `--compact`): added for the plugin's uniform `--compact`; the default output is unchanged.
6. **X4's definition.** *Projection* = this machine's staged `document.json` for the table (gitignored, rewritten by `table2graph` and by `vault sync`'s re-projection, and committed to the graph in the same run). *Older than the table's last refresh* = its `version` differs from the registry's (`register_table` bumps it on every load, `db refresh` included; `.meta.json` carries it, so both laptops agree). *Older than the mapping's last change* = it names another mapping file, or records a different `table_mapping_sha256`, a digest of the parsed mapping that `build_staged` now writes (comments, key order and whitespace do not count — they cannot change what is built). Rejected: comparing with the graph's `Document` node (needs Neo4j for a read the plugin runs on every refresh, and the plugin must work while Neo4j is down); file mtimes (git rewrites them on checkout). **Known limits:** a projection whose staging was written but whose graph commit then failed counts as current until the next run; a projection staged before this plan carries no digest, so a mapping edited before the upgrade is not detected (the name check still applies) — it is re-projected the next time the table or mapping changes; a schema change is not considered (the spec does not ask; `vault sync` handles it on the other laptop).
7. **Refusal fixtures are rich-click's error box.** `vault sync` refuses with text on stderr; the plugin must classify it. The fixtures keep the box exactly as printed at 80 columns so Plan 2's unboxing is tested on the real bytes. Adding a JSON error channel to every command would be the cleaner contract, and a larger change than the spec's "everything else already exists".
8. **Exact comparison after normalisation**, not a shape-only check: a renamed enum value breaks the plugin as surely as a renamed key. The price is that any output change — a reworded doctor detail, say — fails this test until the fixtures are regenerated (one env var) and the plugin's tests re-run.
9. **0.9.0.** Bumped in the docs task, so the version that first reports itself is the one that has all four additions.

### Accepted residual risk

- `ingest pending` reads every mapped note and hashes every mapped binary and table on each call. For a vault of a few thousand files that is seconds, not milliseconds; the plugin calls it on refresh (after a pull, on focus, after an action), not on a timer.
- The fixture test builds 25 small vaults (~6 s). It runs with the rest of the suite.

---

## Verification record

- Worktree: `git worktree add -b obsidian-plans-applied <scratchpad>/applied master` (`a669f81`, `2886 passed, 14 skipped`). A script parsed this file and applied, per task, every `**Create**`, `**Append to**` and `**Replace in** … **with:**` block (a replacement must match exactly once). For each task it first applied only the task's test files and ran them — the "Expected: FAIL" text in each Step 2 is that output — then applied the rest, ran the task's tests and the full suite (`uv run --group dev pytest test/ -q`), and committed with the task's own message. Task 6 also ran the regeneration, then the comparison twice (stable: `25 passed` both times).
- Results after each task: Task 1: `2889 passed, 14 skipped, 1 warning` (`84c4584`); Task 2: `2902 passed, 14 skipped, 1 warning` (`c2fdeaa`); Task 3: `2911 passed, 14 skipped, 1 warning` (`83576d3`); Task 4: `2916 passed, 14 skipped, 1 warning` (`e8f1bc1`); Task 5: `2926 passed, 14 skipped, 1 warning` (`96b7ec9`); Task 6: `2951 passed, 14 skipped, 1 warning` (`45e46ac`); Task 7: `2951 passed, 14 skipped, 1 warning` (`cb4ca91`); Task 8: `2951 passed, 14 skipped, 1 warning` (`no commit`). Task 8: `just dev-cli-guide-check` `6 passed, 1 warning`; `git status --short obsidian/` empty after the fixture comparison.
- Fail-first, re-checked on the fully applied tree (Plan 2 on top) by reverting one task's source and re-running its tests, then restoring it: Task 2 (`ingest.py`, `pipeline.py` at `a669f81`) → `12 failed, 1 passed`; Task 3 (`artmind/ingest_pending.py` removed) → `1 error` (collection); Task 5 (`table2graph.py` at `e8f1bc1`, the CLI part kept) → `8 failed, 2 passed` — as stated, minus the two CLI-only tests the kept `cli.py` satisfies. Separately, Task 2's stamped-body test was run against a planner that hashes the pre-stamp body: its blank-lines case fails, so it pins the post-stamp hash.
- The worktree was removed afterwards; the branch `obsidian-plans-applied` was kept (Plan 1 = its first 7 commits after `a669f81`). Only the two plan files were committed to `master`.
