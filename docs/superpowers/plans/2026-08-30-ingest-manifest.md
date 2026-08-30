# Ingest Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `.artmind/vault.yaml` drive ingestion — mapping folders to domains, and deciding which paths get ingested at all — so `artmind ingest sync .` ingests a whole vault correctly in one command.

**Architecture:** A new module `artmind/manifest.py` reads the manifest and answers two questions about a vault-relative path: *which domain governs it* and *should it be ingested*. `artmind/cli.py` applies it per file inside the `ingest sync` loop, passing the mapped domain as that file's `--domain`. **`artmind/ingest.py`'s domain resolution is not touched** — it already computes `set_domain or prior_domain or domain`, so supplying a per-file `domain` slots the mapping into exactly the right precedence slot for free. A separate, independent change adds a supported-type allowlist so a `.canvas` file is skipped and reported rather than handed to docling.

**Tech Stack:** Python 3.14, PyYAML, Click (rich_click), pytest, `uv`, `just`.

**Follows:** [2026-08-30-vault-foundation.md](./2026-08-30-vault-foundation.md), which landed vault discovery, layout, config, and `artmind init`. That plan's `scaffold_vault` already writes a starter `vault.yaml` with `ingest.trigger: manual` and `ingest.mappings: []`; nothing reads it yet. This plan makes it real.

**Read before starting:** [docs/vault.md](../../vault.md), sections "The ingest manifest" and "Guardrails that survive". Also `CLAUDE.md` — particularly that green tests do not mean the CLI works, and that a running `serve` daemon can mask changes.

**Not in scope:** ingest triggers and the `.artmind/state.json` cursor (`trigger:` is read and validated here, but only `manual` is acted on); vault-resident binaries and the `_derived/` changes; schema provenance. Each is its own plan.

---

## File Structure

| File | Responsibility |
|---|---|
| `artmind/manifest.py` (create) | Read and validate `.artmind/vault.yaml`. Answer `domain_for(relpath)` and `should_ingest(relpath)`. Knows nothing about ingestion mechanics. |
| `artmind/ingest.py` (modify) | A supported-type allowlist: which suffixes artmind can actually ingest. `collect_ingest_files` applies it to directory walks only. |
| `artmind/cli.py` (modify) | `_manifest_for_ingest` helper; `ingest sync` applies it per file, `ingest async` gets the filter only. |
| `test/test_manifest.py` (create) | Manifest parsing, glob matching, precedence, malformed input. |
| `test/test_ingest_manifest_cli.py` (create) | The `ingest sync`/`async` integration: filtering and per-file domain. |
| `test/test_ingest_supported_types.py` (create) | The allowlist. |

`artmind/manifest.py` is separate from `artmind/vault.py` because `vault.py` must stay stdlib-only — `paths.py` imports it at module load for every command, and `manifest.py` needs PyYAML. `manifest.py` is only imported by ingestion paths, which already pay for yaml.

---

## Task 1: Read and validate the manifest

**Files:**
- Create: `artmind/manifest.py`
- Test: `test/test_manifest.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_manifest.py`:

```python
"""The ingest manifest, `.artmind/vault.yaml` (docs/vault.md)."""
from __future__ import annotations

import pytest

from artmind import manifest


def _write(tmp_path, body: str):
    (tmp_path / ".artmind").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".artmind" / "vault.yaml").write_text(body)
    return tmp_path


def test_a_missing_manifest_is_not_an_error(tmp_path):
    """A vault predating this feature, or one mid-init, must still ingest."""
    loaded = manifest.load(tmp_path)

    assert loaded.mappings == []
    assert loaded.trigger == "manual"


def test_reads_trigger_and_mappings(tmp_path):
    _write(tmp_path, """
ingest:
  trigger: manual
  mappings:
    - path: policies/**
      domain: banking.policy
    - path: notes/**
      domain: personal_journal
""")

    loaded = manifest.load(tmp_path)

    assert loaded.trigger == "manual"
    assert [m.domain for m in loaded.mappings] == ["banking.policy", "personal_journal"]


def test_an_empty_manifest_parses(tmp_path):
    """`scaffold_vault` writes `mappings: []` — that must not crash."""
    _write(tmp_path, "ingest:\n  trigger: manual\n  mappings: []\n")

    loaded = manifest.load(tmp_path)

    assert loaded.mappings == []


def test_a_malformed_manifest_names_the_file(tmp_path):
    """The user hand-edits this; a parse error must say which file and why."""
    _write(tmp_path, "ingest: [this is not a mapping]\n")

    with pytest.raises(manifest.ManifestError, match="vault.yaml"):
        manifest.load(tmp_path)


def test_a_mapping_without_a_domain_is_refused(tmp_path):
    _write(tmp_path, "ingest:\n  mappings:\n    - path: notes/**\n")

    with pytest.raises(manifest.ManifestError, match="domain"):
        manifest.load(tmp_path)


def test_a_mapping_without_a_path_is_refused(tmp_path):
    _write(tmp_path, "ingest:\n  mappings:\n    - domain: general\n")

    with pytest.raises(manifest.ManifestError, match="path"):
        manifest.load(tmp_path)


def test_an_unknown_trigger_is_refused(tmp_path):
    """Silently treating a typo as `manual` would leave someone believing
    ingestion is automatic when it is not."""
    _write(tmp_path, "ingest:\n  trigger: whenever\n  mappings: []\n")

    with pytest.raises(manifest.ManifestError, match="trigger"):
        manifest.load(tmp_path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_manifest.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'artmind.manifest'`

- [ ] **Step 3: Write the implementation**

Create `artmind/manifest.py`:

```python
"""The ingest manifest — `.artmind/vault.yaml` (docs/vault.md).

`_meta/schema_mapping.md` in the banking corpus was this feature written as
prose: a table of which schema governs which folder, executed by hand as one
`ingest sync` per folder. Here it is configuration, and it does two jobs:

1. **Which domain** governs a path's extraction.
2. **Whether to ingest the path at all.** An unmapped path is never ingested,
   so an `attachments/` folder needs no separate ignore mechanism, and an
   unmapped `Inbox/` becomes a drafting area where *moving* a note into a
   mapped folder is what says "this is ready".

Separate from `artmind/vault.py` because that module must stay stdlib-only —
`paths.py` imports it at module load for every command. This one needs yaml and
is imported only by ingestion paths, which already pay for it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from artmind.vault import MANIFEST, MARKER

# Only `manual` is acted on today. The others are accepted and validated so a
# manifest written for a later artmind does not fail to parse, but nothing
# schedules or hooks anything yet (see the triggers plan).
VALID_TRIGGERS = ("manual", "commit", "schedule")


class ManifestError(Exception):
    """`.artmind/vault.yaml` is unreadable, malformed, or self-contradictory."""


@dataclass(frozen=True)
class Mapping:
    """One `path` glob and the `domain` that governs everything matching it."""

    path: str
    domain: str

    def matches(self, relpath: str) -> bool:
        """Does this mapping cover `relpath` (vault-relative, posix)?

        `PurePath.full_match` gives real recursive-glob semantics, so
        `policies/**` covers `policies/a.md` and `policies/sub/b.md` alike.
        """
        return PurePosixPath(relpath).full_match(self.path)


@dataclass(frozen=True)
class Manifest:
    trigger: str = "manual"
    mappings: list[Mapping] = field(default_factory=list)

    def domain_for(self, relpath: str) -> str | None:
        """The domain governing `relpath`, or None when nothing maps it.

        **First match wins**, in the order written, so the manifest reads
        top-down like a routing table and a specific rule can be placed above a
        general one.
        """
        for mapping in self.mappings:
            if mapping.matches(relpath):
                return mapping.domain
        return None

    def should_ingest(self, relpath: str) -> bool:
        """Whether `relpath` is ingested at all.

        A manifest with NO mappings maps nothing and therefore filters nothing —
        a vault that has not configured mappings behaves exactly as before this
        feature, rather than suddenly ingesting zero files.
        """
        if not self.mappings:
            return True
        return self.domain_for(relpath) is not None


def load(vault_root: Path) -> Manifest:
    """Read `<vault_root>/.artmind/vault.yaml`.

    A missing manifest is the normal state for a vault created before this
    feature, or one being initialised — never an error.
    """
    path = Path(vault_root) / MARKER / MANIFEST
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return Manifest()

    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: not valid YAML -- {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: expected a mapping at the top level")

    ingest = data.get("ingest") or {}
    if not isinstance(ingest, dict):
        raise ManifestError(f"{path}: 'ingest' must be a mapping, got {type(ingest).__name__}")

    trigger = ingest.get("trigger", "manual")
    if trigger not in VALID_TRIGGERS:
        raise ManifestError(
            f"{path}: unknown ingest trigger {trigger!r}. "
            f"Choose from: {', '.join(VALID_TRIGGERS)}."
        )

    raw_mappings = ingest.get("mappings") or []
    if not isinstance(raw_mappings, list):
        raise ManifestError(f"{path}: 'ingest.mappings' must be a list")

    mappings: list[Mapping] = []
    for index, entry in enumerate(raw_mappings):
        if not isinstance(entry, dict):
            raise ManifestError(f"{path}: ingest.mappings[{index}] must be a mapping")
        if not entry.get("path"):
            raise ManifestError(f"{path}: ingest.mappings[{index}] is missing 'path'")
        if not entry.get("domain"):
            raise ManifestError(
                f"{path}: ingest.mappings[{index}] ({entry['path']}) is missing 'domain'"
            )
        mappings.append(Mapping(path=str(entry["path"]), domain=str(entry["domain"])))

    return Manifest(trigger=trigger, mappings=mappings)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_manifest.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add artmind/manifest.py test/test_manifest.py
git commit -m "feat(ingest): read and validate the vault manifest"
```

---

## Task 2: Match paths to domains

**Files:**
- Test: `test/test_manifest.py`

No implementation is expected here — `Mapping.matches` and `Manifest.domain_for` were written in Task 1. These tests pin the *semantics* those methods must have, and exist because glob behaviour is exactly the kind of thing that silently changes meaning under a refactor.

- [ ] **Step 1: Write the tests**

Append to `test/test_manifest.py`:

```python
def _manifest(*pairs) -> manifest.Manifest:
    return manifest.Manifest(
        mappings=[manifest.Mapping(path=p, domain=d) for p, d in pairs]
    )


def test_a_recursive_glob_covers_nested_paths():
    m = _manifest(("policies/**", "banking.policy"))

    assert m.domain_for("policies/policy_aml.md") == "banking.policy"
    assert m.domain_for("policies/sub/deep.md") == "banking.policy"


def test_an_unmapped_path_has_no_domain():
    m = _manifest(("policies/**", "banking.policy"))

    assert m.domain_for("attachments/photo.png") is None


def test_first_match_wins_so_a_specific_rule_can_precede_a_general_one():
    """The manifest reads top-down like a routing table."""
    m = _manifest(
        ("notes/archive/**", "general"),
        ("notes/**", "personal_journal"),
    )

    assert m.domain_for("notes/archive/old.md") == "general"
    assert m.domain_for("notes/today.md") == "personal_journal"


def test_an_unmapped_path_is_not_ingested():
    """This is the second job of the mapping: an attachments/ folder needs no
    separate ignore mechanism, it is simply not mapped."""
    m = _manifest(("notes/**", "personal_journal"))

    assert m.should_ingest("notes/a.md") is True
    assert m.should_ingest("attachments/photo.png") is False


def test_a_manifest_with_no_mappings_filters_nothing():
    """A vault that has not configured mappings must behave exactly as it did
    before this feature -- NOT suddenly ingest zero files."""
    empty = manifest.Manifest()

    assert empty.should_ingest("anything/at/all.md") is True
    assert empty.domain_for("anything/at/all.md") is None


def test_a_single_file_glob_matches_only_that_file():
    m = _manifest(("structured/customers.csv", "banking"))

    assert m.domain_for("structured/customers.csv") == "banking"
    assert m.domain_for("structured/agents.csv") is None
```

- [ ] **Step 2: Run the tests**

Run: `uv run --group dev pytest test/test_manifest.py -v`
Expected: PASS, 13 passed. If any fail, the Task 1 implementation is wrong — fix `artmind/manifest.py`, not the tests.

- [ ] **Step 3: Commit**

```bash
git add test/test_manifest.py
git commit -m "test(ingest): pin manifest glob and precedence semantics"
```

---

## Task 3: A supported-type allowlist

Independent of the manifest. `ingest_file` currently routes **every** non-`.md` file to docling (`artmind/ingest.py`, `_is_promotable_binary` / `_ingest_binary_or_adhoc`), so a `.canvas` file — which is JSON — is handed to a document converter that cannot read it, and a stray `.zip` is attempted too.

**Files:**
- Modify: `artmind/ingest.py`
- Test: `test/test_ingest_supported_types.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_ingest_supported_types.py`:

```python
"""Which file types artmind will attempt (docs/vault.md, "Guardrails")."""
from __future__ import annotations

import pytest

from artmind.ingest import SUPPORTED_SUFFIXES, collect_ingest_files, is_supported


@pytest.mark.parametrize("name", [
    "note.md", "deck.pptx", "paper.pdf", "memo.docx",
    "table.csv", "book.xlsx", "scan.png", "photo.jpg",
])
def test_types_artmind_can_actually_ingest(tmp_path, name):
    assert is_supported(tmp_path / name) is True


@pytest.mark.parametrize("name", [
    "board.canvas",      # Obsidian canvas -- JSON, docling cannot read it
    "sketch.excalidraw",  # likewise
    "archive.zip",
    "video.mp4",
    "notes.txt.bak",
])
def test_types_artmind_must_not_hand_to_docling(tmp_path, name):
    assert is_supported(tmp_path / name) is False


def test_the_suffix_check_is_case_insensitive(tmp_path):
    assert is_supported(tmp_path / "DECK.PPTX") is True


def test_a_directory_walk_skips_unsupported_types(tmp_path):
    (tmp_path / "note.md").write_text("# note")
    (tmp_path / "board.canvas").write_text("{}")
    (tmp_path / "deck.pptx").write_bytes(b"x")

    found = [f.name for f in collect_ingest_files(tmp_path)]

    assert sorted(found) == ["deck.pptx", "note.md"]


def test_a_directory_walk_still_skips_dotfiles(tmp_path):
    """Pre-existing behaviour that must survive: .artmind/, .obsidian/, .git/."""
    (tmp_path / "note.md").write_text("# note")
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}")

    found = [f.name for f in collect_ingest_files(tmp_path)]

    assert found == ["note.md"]


def test_naming_one_unsupported_file_explicitly_still_returns_it(tmp_path):
    """A directory walk filters silently; naming a file is an explicit request,
    and the caller reports why it cannot be ingested rather than the walk
    pretending it was never there."""
    target = tmp_path / "board.canvas"
    target.write_text("{}")

    assert collect_ingest_files(target) == [target]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_supported_types.py -v`
Expected: FAIL, `ImportError: cannot import name 'SUPPORTED_SUFFIXES' from 'artmind.ingest'`

- [ ] **Step 3: Write the implementation**

In `artmind/ingest.py`, add above `collect_ingest_files`:

```python
# What artmind can actually ingest. `ingest_file` routes every non-`.md` file
# to docling, so without this an Obsidian vault's `.canvas` files (JSON) are
# handed to a document converter that cannot read them, and its `.png`
# attachments run through image description at full LLM cost merely for being
# present. Unknown types are skipped by a directory walk and reported by the
# caller -- never silently attempted.
SUPPORTED_SUFFIXES = frozenset({
    ".md",                              # vault-native markdown
    ".pdf", ".pptx", ".docx",           # docling conversion
    ".csv", ".xlsx",                    # the structured store
    ".png", ".jpg", ".jpeg", ".webp",   # images, when a folder of them is mapped
})


def is_supported(path: Path) -> bool:
    """Can artmind ingest this file type at all?"""
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES
```

Then change `collect_ingest_files` to filter the directory walk. Replace:

```python
    if path.is_dir():
        return sorted(
            f for f in path.rglob("*")
            if f.is_file()
            and not any(p.startswith(".") for p in f.relative_to(path).parts)
        )
    return [path]
```

with:

```python
    if path.is_dir():
        return sorted(
            f for f in path.rglob("*")
            if f.is_file()
            and not any(p.startswith(".") for p in f.relative_to(path).parts)
            and is_supported(f)
        )
    # A named file is an explicit request: return it and let the caller report
    # why it cannot be ingested, rather than silently pretending it was absent.
    return [path]
```

Update the docstring's second paragraph to record the new rule:

```python
    A single file ingests as itself, whatever its type -- naming it is an
    explicit request, and the caller reports an unsupported type rather than
    the walk silently dropping it. A directory is walked recursively, skipping
    any file under a dotfile/dot-directory (``.DS_Store``, ``.git/``,
    ``.artmind/``, ``.obsidian/``) and any file whose type artmind cannot
    ingest (see ``SUPPORTED_SUFFIXES``).
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_ingest_supported_types.py -v`
Expected: PASS, 15 passed

Run: `just dev-test`
Expected: all green. If a pre-existing ingestion test breaks, it is almost certainly feeding a fixture file with an unsupported suffix (e.g. `.txt`) through a directory walk — check whether that type *should* be in `SUPPORTED_SUFFIXES` before changing the test.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_ingest_supported_types.py
git commit -m "feat(ingest): only attempt file types artmind can actually ingest"
```

---

## Task 4: `ingest sync` applies the manifest

The integration. Note what this task deliberately does **not** touch: `artmind/ingest.py`'s domain resolution. `_ingest_vault_native` already computes `effective_domain = set_domain or prior_domain or domain` (`artmind/ingest.py:531-532`), so passing a per-file `domain` puts the mapping in exactly the right precedence slot with no change there.

Resulting precedence, highest first: `--setDomain` → the file's own `_domain` frontmatter → the folder mapping → `--domain` as the fallback for unmapped files.

**Files:**
- Modify: `artmind/cli.py` (the `ingest_sync` command)
- Test: `test/test_ingest_manifest_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_ingest_manifest_cli.py`:

```python
"""`ingest sync` driven by the vault manifest (docs/vault.md).

These assert on WHICH FILES were offered to ingestion and WITH WHAT DOMAIN, by
recording the calls -- never on summary counts, which can report success for
work that never happened (CLAUDE.md).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import artmind.cli as cli_module
from artmind.cli import cli


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A vault with a manifest, and ingestion stubbed to record its calls."""
    (tmp_path / ".artmind").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def recorded(monkeypatch):
    """Record (filename, domain) for every ingest_file call."""
    calls: list[tuple[str, str | None]] = []

    def fake_ingest_file(source, image_model, domain=None, **kwargs):
        calls.append((Path(source).name, domain))
        return {"status": "ok", "domain": domain, "touched_path": source}

    monkeypatch.setattr(cli_module, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(cli_module, "ingest_to_kg", lambda *a, **k: True)
    return calls


def _manifest(vault_root: Path, body: str) -> None:
    (vault_root / ".artmind" / "vault.yaml").write_text(body)


def test_only_mapped_paths_are_ingested(vault, recorded):
    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: personal_journal
""")
    (vault / "notes").mkdir()
    (vault / "notes" / "a.md").write_text("# a")
    (vault / "attachments").mkdir()
    (vault / "attachments" / "b.md").write_text("# b")

    result = CliRunner().invoke(cli, ["ingest", "sync", "."])

    assert result.exit_code == 0, result.output
    assert [name for name, _ in recorded] == ["a.md"]


def test_each_file_gets_the_domain_its_folder_maps_to(vault, recorded):
    _manifest(vault, """
ingest:
  mappings:
    - path: policies/**
      domain: banking.policy
    - path: notes/**
      domain: personal_journal
""")
    for folder, name in (("policies", "p.md"), ("notes", "n.md")):
        (vault / folder).mkdir()
        (vault / folder / name).write_text("# x")

    result = CliRunner().invoke(cli, ["ingest", "sync", "."])

    assert result.exit_code == 0, result.output
    assert dict(recorded) == {"p.md": "banking.policy", "n.md": "personal_journal"}


def test_an_explicit_domain_is_the_fallback_for_unmapped_files(vault, recorded):
    """--domain does not override a mapping; it covers what nothing maps."""
    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: personal_journal
""")
    (vault / "notes").mkdir()
    (vault / "notes" / "n.md").write_text("# n")

    result = CliRunner().invoke(cli, ["ingest", "sync", ".", "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert dict(recorded) == {"n.md": "personal_journal"}


def test_a_vault_with_no_mappings_ingests_everything_as_before(vault, recorded):
    """Back-compat: configuring no mappings must not mean ingesting nothing."""
    _manifest(vault, "ingest:\n  trigger: manual\n  mappings: []\n")
    (vault / "a.md").write_text("# a")
    (vault / "b.md").write_text("# b")

    result = CliRunner().invoke(cli, ["ingest", "sync", ".", "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert sorted(name for name, _ in recorded) == ["a.md", "b.md"]


def test_naming_a_file_directly_bypasses_the_mapping_filter(vault, recorded):
    """An explicit request is honoured even from an unmapped folder -- the
    filter is for walks, not for overriding what the user just asked for."""
    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: personal_journal
""")
    (vault / "scratch").mkdir()
    (vault / "scratch" / "one.md").write_text("# one")

    result = CliRunner().invoke(
        cli, ["ingest", "sync", "scratch/one.md", "--domain", "general"]
    )

    assert result.exit_code == 0, result.output
    assert [name for name, _ in recorded] == ["one.md"]


def test_a_malformed_manifest_stops_the_run(vault, recorded):
    """Ingesting into wrong domains because a mapping was mistyped is worse
    than refusing to start."""
    _manifest(vault, "ingest:\n  mappings:\n    - path: notes/**\n")
    (vault / "notes").mkdir()
    (vault / "notes" / "n.md").write_text("# n")

    result = CliRunner().invoke(cli, ["ingest", "sync", "."])

    assert result.exit_code != 0
    assert "domain" in result.output
    assert recorded == [], "nothing may be ingested after a manifest error"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_manifest_cli.py -v`
Expected: FAIL — files from unmapped folders are ingested and the mapped domain is ignored, because nothing reads the manifest yet.

- [ ] **Step 3: Write the implementation**

In `artmind/cli.py`'s `ingest_sync`, find this pair — **around line 564**.
The identical two lines appear again around line 695 in `ingest_async`; that is
a different command and is handled in Task 4b. Confirm you are in `ingest_sync`
by checking the lines just above mention `--domain is required for structured
files` further down, or simply that you are inside the function named
`ingest_sync`.

```python
    path = Path(file_path)
    files = collect_ingest_files(path)
```

Replace with:

```python
    path = Path(file_path)
    files = collect_ingest_files(path)

    # The manifest does two jobs (docs/vault.md): it says which domain governs
    # a path, and whether the path is ingested at all. Filtering applies to a
    # directory WALK only -- naming a file is an explicit request and is always
    # honoured, mapped or not.
    from artmind.manifest import ManifestError, load as _load_manifest
    from paths import ARTMIND_VAULT_DIR

    vault_manifest = None
    if ARTMIND_VAULT_DIR is not None:
        try:
            vault_manifest = _load_manifest(ARTMIND_VAULT_DIR)
        except ManifestError as e:
            # Ingesting into the wrong domains because a mapping was mistyped
            # is worse than refusing to start.
            raise click.ClickException(str(e))

    def _mapped_domain(f: Path) -> str | None:
        if vault_manifest is None or ARTMIND_VAULT_DIR is None:
            return None
        try:
            rel = f.resolve().relative_to(ARTMIND_VAULT_DIR).as_posix()
        except ValueError:
            return None  # outside the vault; the manifest says nothing about it
        return vault_manifest.domain_for(rel)

    if vault_manifest is not None and path.is_dir():
        kept = []
        skipped = 0
        for f in files:
            try:
                rel = f.resolve().relative_to(ARTMIND_VAULT_DIR).as_posix()
            except ValueError:
                kept.append(f)
                continue
            if vault_manifest.should_ingest(rel):
                kept.append(f)
            else:
                skipped += 1
        if skipped:
            logger.info(
                "Skipped {} file(s) no mapping covers "
                "(.artmind/vault.yaml, ingest.mappings)", skipped,
            )
        files = kept
```

Then, inside the `for f in files:` loop, replace the `ingest_file` call:

```python
            result = ingest_file(
                f, image_model, domain, chunk_size=chunk_size,
                set_domain=set_domain, fork=fork, adopt=adopt,
            )
```

with:

```python
            # `ingest.py` computes `set_domain or prior_domain or domain`, so
            # passing the mapped domain here lands the mapping in exactly the
            # right precedence slot: --setDomain > the file's own _domain >
            # the folder mapping > --domain as the fallback.
            result = ingest_file(
                f, image_model, _mapped_domain(f) or domain, chunk_size=chunk_size,
                set_domain=set_domain, fork=fork, adopt=adopt,
            )
```

Finally, the pre-loop `--domain` validation must not reject a run whose domains all come from mappings. Find:

```python
    if domain is not None and domain not in _get_available_domains():
```

and add, immediately before it:

```python
    # Every domain a mapping names must exist, checked once up front rather
    # than failing partway through a batch.
    if vault_manifest is not None:
        available = _get_available_domains()
        unknown = sorted({
            m.domain for m in vault_manifest.mappings if m.domain not in available
        })
        if unknown:
            raise click.ClickException(
                f"Manifest maps to unknown domain(s): {', '.join(unknown)}. "
                "Run 'artmind domains list' to see available domains."
            )
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_ingest_manifest_cli.py -v`
Expected: PASS, 6 passed

Run: `just dev-test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_ingest_manifest_cli.py
git commit -m "feat(ingest): the manifest decides what is ingested and as what"
```

---

## Task 4b: `ingest async` respects the manifest too

`ingest async` walks the same directories and queues them for the background
worker, so without this a user who runs `ingest async .` gets exactly the
behaviour the manifest exists to prevent: `attachments/` handed to docling.

It gets the **filter** only, not per-file domains. `_create_job` stores one
`domain` for the whole batch (`artmind/cli.py`, around line 700), so per-file
mapped domains need the job schema to carry them — deliberately deferred rather
than half-built here.

**Files:**
- Modify: `artmind/cli.py` (the `ingest_async` command)
- Test: `test/test_ingest_manifest_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_ingest_manifest_cli.py`:

```python
def test_async_also_skips_unmapped_paths(vault, monkeypatch):
    """`ingest async` walks the same directories; without the filter it queues
    exactly what the manifest exists to keep out."""
    queued: list[list[str]] = []

    def fake_create_job(batch_files, **kwargs):
        queued.append([Path(f).name for f in batch_files])
        return "job-1"

    monkeypatch.setattr(cli_module, "_create_job", fake_create_job)
    monkeypatch.setattr(cli_module, "_ensure_worker_running", lambda: None)

    _manifest(vault, """
ingest:
  mappings:
    - path: notes/**
      domain: general
""")
    (vault / "notes").mkdir()
    (vault / "notes" / "a.md").write_text("# a")
    (vault / "attachments").mkdir()
    (vault / "attachments" / "b.md").write_text("# b")

    result = CliRunner().invoke(cli, ["ingest", "async", ".", "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert queued == [["a.md"]]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_ingest_manifest_cli.py::test_async_also_skips_unmapped_paths -v`
Expected: FAIL — `b.md` is queued too.

- [ ] **Step 3: Extract the filter, then use it in both commands**

Task 4 inlined the filtering in `ingest_sync`. Two copies would drift, so lift it
to a module-level helper in `artmind/cli.py`, placed just above `ingest_sync`:

```python
def _manifest_for_ingest(path: Path, files: "list[Path]") -> tuple[object, "list[Path]"]:
    """Load the vault manifest and drop files no mapping covers.

    Filtering applies to a directory WALK only — naming a file is an explicit
    request and is always honoured, mapped or not. Returns the manifest (or
    None outside a vault) alongside the files to actually ingest.
    """
    from artmind.manifest import ManifestError, load as _load_manifest
    from paths import ARTMIND_VAULT_DIR

    if ARTMIND_VAULT_DIR is None:
        return None, files
    try:
        vault_manifest = _load_manifest(ARTMIND_VAULT_DIR)
    except ManifestError as e:
        # Ingesting into the wrong domains because a mapping was mistyped is
        # worse than refusing to start.
        raise click.ClickException(str(e))

    if not path.is_dir():
        return vault_manifest, files

    kept, skipped = [], 0
    for f in files:
        try:
            rel = f.resolve().relative_to(ARTMIND_VAULT_DIR).as_posix()
        except ValueError:
            kept.append(f)  # outside the vault; the manifest says nothing
            continue
        if vault_manifest.should_ingest(rel):
            kept.append(f)
        else:
            skipped += 1
    if skipped:
        logger.info(
            "Skipped {} file(s) no mapping covers "
            "(.artmind/vault.yaml, ingest.mappings)", skipped,
        )
    return vault_manifest, kept
```

Then in `ingest_sync`, replace the inlined block from Task 4 with:

```python
    vault_manifest, files = _manifest_for_ingest(path, files)
```

keeping the `_mapped_domain` closure and the unknown-domain validation from
Task 4 exactly as they are.

And in `ingest_async`, after its own `files = collect_ingest_files(path)`
(around line 695), add:

```python
    _, files = _manifest_for_ingest(path, files)
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_ingest_manifest_cli.py -v`
Expected: PASS, 7 passed

Run: `just dev-test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_ingest_manifest_cli.py
git commit -m "feat(ingest): async respects the manifest filter too"
```

---

## Task 5: Seed a useful starter manifest

`scaffold_vault` writes `mappings: []` with a commented example. Now that mappings do something, the comment should show the shape that actually works and name the drafting-folder idiom.

**Files:**
- Modify: `artmind/setup.py` (`_STARTER_VAULT_YAML`)
- Test: `test/test_vault_scaffold.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_vault_scaffold.py`:

```python
def test_the_starter_manifest_parses_with_the_real_reader(tmp_path):
    """The template is the first thing a user edits; if it does not parse, the
    first thing they see is an error."""
    from artmind.manifest import load
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    loaded = load(tmp_path)

    assert loaded.trigger == "manual"
    assert loaded.mappings == []
```

- [ ] **Step 2: Run the test**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v`
Expected: PASS already — the Task 1 reader tolerates `mappings: []`. If it FAILS, the reader or the template is wrong; fix whichever is at fault before continuing.

- [ ] **Step 3: Improve the template**

In `artmind/setup.py`, replace `_STARTER_VAULT_YAML` with:

```python
_STARTER_VAULT_YAML = """\
# artmind ingest manifest (docs/vault.md).
#
# `mappings` does two jobs: it says which domain governs a path's extraction,
# AND whether to ingest it at all. An unmapped path is never ingested -- so an
# attachments folder needs no ignore rule, and an unmapped Inbox/ is a drafting
# area where MOVING a note into a mapped folder is what says "this is ready".
#
# First match wins, so put a specific rule above a general one. Paths are globs
# relative to the vault root; `**` matches any depth.
#
# Domain precedence, highest first:
#   --setDomain  >  the file's own _domain frontmatter  >  a mapping  >  --domain
ingest:
  # manual | commit | schedule. Only `manual` acts today. Default manual:
  # nobody should discover automatic LLM spend by surprise.
  trigger: manual
  mappings: []
  #  - path: notes/**
  #    domain: personal_journal
  #  - path: scans/**
  #    domain: general
"""
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v`
Expected: PASS, 16 passed

- [ ] **Step 5: Commit**

```bash
git add artmind/setup.py test/test_vault_scaffold.py
git commit -m "docs(vault): starter manifest shows the shape that works"
```

---

## Task 6: Document it

**Files:**
- Modify: `docs/vault.md`
- Modify: `README.md`

- [ ] **Step 1: Update the status line in `docs/vault.md`**

It currently reads "Landed: discovery, resolution precedence, the `VaultLayout` class, and the machine/vault config split." Add the manifest:

```markdown
**Status: partially implemented** on branch `feat/vault`. Landed: discovery,
resolution precedence, the `VaultLayout` class, the machine/vault config split,
`artmind init`, and the ingest manifest (folder→domain mapping, unmapped paths
skipped, supported-type allowlist). Not yet: ingest triggers and the commit
cursor, vault-resident binaries, schema provenance, daemon discovery, and
`artmind vault adopt` — see
[the follow-on plans](./superpowers/plans/2026-08-30-vault-foundation.md).
```

- [ ] **Step 2: Correct the trigger claim in `docs/vault.md`**

The "Ingest triggers" section describes the cursor as though it exists. Add this immediately under that heading:

```markdown
> **Not yet implemented.** `trigger:` is read and validated, and an unknown
> value is refused, but only `manual` does anything today. The cursor and the
> commit/schedule pokes are their own plan.
```

- [ ] **Step 3: Add a manifest section to `README.md`**

Insert immediately before the "### Synchronous (recommended for single files)" heading under "Ingesting documents":

````markdown
### Mapping folders to domains

A vault's `.artmind/vault.yaml` says which domain governs which folder — and
what to ingest at all:

```yaml
ingest:
  trigger: manual
  mappings:
    - path: policies/**
      domain: banking.policy
    - path: notes/**
      domain: personal_journal
```

Then one command ingests the whole vault, each folder into its own domain:

```bash
artmind ingest sync .
```

An **unmapped path is never ingested**, so an `attachments/` folder needs no
ignore rule, and an unmapped `Inbox/` becomes a drafting area — moving a note
into a mapped folder is what marks it ready.

First match wins, so a specific rule can sit above a general one. Domain
precedence, highest first: `--setDomain`, then the file's own `_domain`
frontmatter, then the mapping, then `--domain` as the fallback for unmapped
files.
````

- [ ] **Step 4: Verify every command you documented**

```bash
cd /tmp && rm -rf manifest-doc && mkdir manifest-doc && cd manifest-doc
ARTMIND_NO_PROXY=1 artmind init
mkdir -p notes attachments && echo "# a note" > notes/a.md && echo "x" > attachments/b.md
python3 - <<'EOF'
from pathlib import Path
p = Path(".artmind/vault.yaml")
p.write_text("ingest:\n  trigger: manual\n  mappings:\n    - path: notes/**\n      domain: general\n")
EOF
ARTMIND_NO_PROXY=1 artmind ingest sync . --domain general 2>&1 | grep -iE "skipped|sync ingest"
cd /tmp && rm -rf manifest-doc
```

Expected: the log reports 1 file skipped (`attachments/b.md`) and ingests only `notes/a.md`. **This makes real LLM calls** — keep the fixture to one tiny file, or run with `--stage-only` to skip the graph write.

- [ ] **Step 5: Commit**

```bash
git add docs/vault.md README.md
git commit -m "docs: the ingest manifest, and what triggers do not do yet"
```

---

## Task 7: End-to-end verification

Green tests do not mean the CLI works (`CLAUDE.md`). Manual, against a real vault.

- [ ] **Step 1: Stop stale daemons**

Run: `just dev-stop-daemons`
A daemon started before this change loaded the old code.

- [ ] **Step 2: Build a vault with a realistic shape**

```bash
just dev-install
cd /tmp && rm -rf manifest-e2e && mkdir manifest-e2e && cd manifest-e2e
ARTMIND_NO_PROXY=1 artmind init
mkdir -p notes Inbox attachments
echo "# real note" > notes/keep.md
echo "# half-written" > Inbox/draft.md
echo "not a document" > attachments/photo.png
echo '{"nodes":[]}' > notes/board.canvas
```

- [ ] **Step 3: Map only `notes/`**

```bash
cat > .artmind/vault.yaml <<'EOF'
ingest:
  trigger: manual
  mappings:
    - path: notes/**
      domain: general
EOF
ARTMIND_NO_PROXY=1 artmind ingest sync . --stage-only 2>&1 | grep -iE "sync ingest|skipped|canvas"
```

Expected: `notes/keep.md` is ingested as `general`. `Inbox/draft.md` and `attachments/photo.png` are skipped as unmapped. `notes/board.canvas` never reaches docling — it is filtered by the type allowlist even though `notes/**` maps it.

- [ ] **Step 4: Confirm a mistyped manifest refuses to run**

```bash
cat > .artmind/vault.yaml <<'EOF'
ingest:
  mappings:
    - path: notes/**
      domain: no_such_domain
EOF
ARTMIND_NO_PROXY=1 artmind ingest sync . --stage-only; echo "exit=$?"
```

Expected: a message naming `no_such_domain`, exit 1, and nothing ingested.

- [ ] **Step 5: Confirm the drafting idiom**

```bash
cat > .artmind/vault.yaml <<'EOF'
ingest:
  trigger: manual
  mappings:
    - path: notes/**
      domain: general
EOF
git mv Inbox/draft.md notes/draft.md
ARTMIND_NO_PROXY=1 artmind ingest sync . --stage-only 2>&1 | grep -iE "sync ingest|skipped"
```

Expected: `draft.md` is now ingested. Moving a note into a mapped folder is what marks it ready — no new concept, no status field.

- [ ] **Step 6: Clean up**

```bash
cd /tmp && rm -rf manifest-e2e
```

---

## Notes for whoever writes the next plan

Findings from the foundation plan's execution that affect later work:

- **`ingest async` gets the manifest FILTER but not per-file domains.**
  `_create_job` stores one `domain` for a whole batch, so a mapped domain
  per file needs the job schema to carry it. Until then, an async batch
  spanning several mapped folders still uses the single `--domain` given.

- **`VaultLayout` and `paths.py` disagree on data-dir names.** `VaultLayout` declares `data/originals`, `data/chunks`, `data/snapshots`, `data/jobs`; `paths.py` still derives `data/documents/originals`, `data/ingestion_jobs`, `data/graph_snapshot`, `data/structured_snapshot`, and has no `chunks` concept. Nothing reads the `VaultLayout` names yet, so nothing is broken — but reaching for `layout.snapshots_dir` today returns a path the system does not use. **Reconciling these belongs in the vault-resident-sources plan**, as its first task.
- **`--vault` is not wired.** `resolve_vault()` accepts an `explicit` argument but no command passes one. Making it real means a global Click option on every command, which is its own piece of work.
- **`load_env()` now returns `dict(os.environ)`**, not one file's values. Any new code reading config should use it, and must not assume the returned dict contains only artmind keys.
- **`artmind vault` resolves fresh** rather than reading `paths.ARTMIND_VAULT_DIR`, because that module global is frozen at first import and cannot see a `chdir` within one process. Any new command that needs the vault should do the same.
