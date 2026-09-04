# Vault Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a directory containing `.artmind/` a self-contained artmind vault, discovered by walking up from the current directory, so `artmind` anchors to whichever vault you are standing in.

**Architecture:** A new pure module `artmind/vault.py` owns discovery (walk up for `.artmind/`, like git) and layout (where everything sits inside it). `paths.py` consumes it and, when no vault is found, **falls back to today's `~/.artmind` + `~/artmind_data` layout unchanged** — that bridge is what lets the 1734-test suite keep passing while the migration proceeds incrementally. `artmind init` changes meaning from "scaffold the run folder" to "make this directory a vault".

**Tech Stack:** Python 3.14, Click (via rich_click), pytest, `uv` for dependency management, `just` as task runner.

**Scope note:** `docs/vault.md` specifies more than one subsystem. This plan covers **only the foundation** — discovery, layout, `init`, and the docs that describe installation. It produces working, testable software on its own: you can create a vault and run commands anchored to it. Five follow-on plans are listed under "Follow-on plans"; each is independently shippable and should be written separately when this one lands.

**Read before starting:** [docs/vault.md](../../vault.md) (the specification) and [docs/stores-and-repos.md](../../stores-and-repos.md) (the topology). `CLAUDE.md` explains why a running daemon can mask your changes and why green tests do not mean the CLI works.

---

## File Structure

| File | Responsibility |
|---|---|
| `artmind/vault.py` (create) | Pure discovery + layout. No I/O beyond `is_dir()`. No imports from `paths` — `paths` imports *it*, so the dependency runs one way only. |
| `paths.py` (modify) | Resolve the vault at import; derive every path constant from it; fall back to the legacy layout when no vault is found. |
| `artmind/setup.py` (modify) | `scaffold_vault()` — create `.artmind/`, write `.gitignore`, seed starter schemas, symlink skills. |
| `artmind/cli.py` (modify) | `artmind init` becomes "make this directory a vault"; add `artmind vault` status command. |
| `justfile` (modify) | `dev-install` stops running `artmind init`. |
| `test/test_vault.py` (create) | Discovery, layout, precedence, traversal safety. |
| `test/test_vault_scaffold.py` (create) | What `init` writes into a fresh directory. |
| `docs/INSTALL.md` (rewrite) | The vault install/layout reference. |
| `README.md` (modify) | Install + layout sections for vaults, plus a correctness pass on stale references. |

`artmind/vault.py` is deliberately separate from `paths.py`: `paths` runs at import for every command and must stay cheap, and discovery needs to be unit-testable without reimporting `paths`.

**Import-cycle constraint.** `paths.py` will import `artmind.vault`, which imports the `artmind` package. `artmind/__init__.py` is empty today and **must stay empty** — anything it imports that reaches `paths` creates a cycle that breaks every command. `artmind/vault.py` itself must import stdlib only, for the same reason.

---

## Task 1: Vault discovery

**Files:**
- Create: `artmind/vault.py`
- Test: `test/test_vault.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_vault.py`:

```python
"""Vault discovery and layout (docs/vault.md)."""
from __future__ import annotations

from pathlib import Path

import pytest

from artmind import vault


def test_finds_vault_in_the_directory_itself(tmp_path):
    (tmp_path / ".artmind").mkdir()
    assert vault.find_vault(tmp_path) == tmp_path.resolve()


def test_walks_up_to_find_the_vault(tmp_path):
    (tmp_path / ".artmind").mkdir()
    deep = tmp_path / "notes" / "2026" / "august"
    deep.mkdir(parents=True)

    assert vault.find_vault(deep) == tmp_path.resolve()


def test_returns_none_outside_any_vault(tmp_path):
    assert vault.find_vault(tmp_path) is None


def test_innermost_vault_wins(tmp_path):
    """Nested vaults behave like nested git repos."""
    (tmp_path / ".artmind").mkdir()
    inner = tmp_path / "inner"
    (inner / ".artmind").mkdir(parents=True)

    assert vault.find_vault(inner) == inner.resolve()


def test_a_file_named_dot_artmind_is_not_a_vault(tmp_path):
    (tmp_path / ".artmind").write_text("not a directory")
    assert vault.find_vault(tmp_path) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'artmind.vault'`

- [ ] **Step 3: Write the minimal implementation**

Create `artmind/vault.py`:

```python
"""Vault discovery and layout (docs/vault.md).

A **vault** is a directory containing `.artmind/`. It is the user's Obsidian
vault, their git repo and their artmind knowledge base at once. You do not
select a vault — you are standing in one, or you are not, exactly as with a git
repo.

This module is deliberately pure and free of `paths` imports: `paths` imports
*this*, runs at import time for every command, and must stay cheap. Keeping
discovery here also makes it unit-testable without reimporting `paths`.
"""
from __future__ import annotations

from pathlib import Path

# The marker directory. A vault is any directory containing one.
MARKER = ".artmind"


def find_vault(start: Path) -> Path | None:
    """The innermost vault at or above `start`, or None.

    Walks up exactly as git walks up for `.git/`, so nested vaults behave like
    nested repos: the innermost wins.
    """
    try:
        current = Path(start).expanduser().resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / MARKER).is_dir():
            return candidate
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add artmind/vault.py test/test_vault.py
git commit -m "feat(vault): walk up from cwd to discover the vault"
```

---

## Task 2: Vault resolution precedence

**Files:**
- Modify: `artmind/vault.py`
- Test: `test/test_vault.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault.py`:

```python
def test_explicit_path_beats_everything(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    (explicit / ".artmind").mkdir(parents=True)
    other = tmp_path / "other"
    (other / ".artmind").mkdir(parents=True)
    monkeypatch.setenv("ARTMIND_VAULT", str(other))
    monkeypatch.chdir(other)

    assert vault.resolve_vault(str(explicit)) == explicit.resolve()


def test_env_var_beats_the_walk_up(tmp_path, monkeypatch):
    """ARTMIND_VAULT exists for cron and anything with no meaningful cwd."""
    env_vault = tmp_path / "env"
    (env_vault / ".artmind").mkdir(parents=True)
    cwd_vault = tmp_path / "cwd"
    (cwd_vault / ".artmind").mkdir(parents=True)
    monkeypatch.setenv("ARTMIND_VAULT", str(env_vault))
    monkeypatch.chdir(cwd_vault)

    assert vault.resolve_vault() == env_vault.resolve()


def test_falls_back_to_the_walk_up(tmp_path, monkeypatch):
    cwd_vault = tmp_path / "cwd"
    (cwd_vault / ".artmind").mkdir(parents=True)
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.chdir(cwd_vault)

    assert vault.resolve_vault() == cwd_vault.resolve()


def test_resolves_to_none_outside_any_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert vault.resolve_vault() is None


def test_an_env_var_pointing_at_a_non_vault_is_refused(tmp_path, monkeypatch):
    """Silently falling back to the walk-up would ingest into the wrong vault."""
    monkeypatch.setenv("ARTMIND_VAULT", str(tmp_path / "nope"))

    with pytest.raises(vault.VaultError, match="ARTMIND_VAULT"):
        vault.resolve_vault()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault.py -v`
Expected: FAIL, `AttributeError: module 'artmind.vault' has no attribute 'resolve_vault'`

- [ ] **Step 3: Write the implementation**

Add to `artmind/vault.py` (add `import os` at the top, beside `from pathlib import Path`):

```python
class VaultError(Exception):
    """A vault could not be resolved, or the one named does not exist."""


def resolve_vault(explicit: str | None = None) -> Path | None:
    """The active vault, or None when the caller is not inside one.

    Precedence, highest first (docs/vault.md, "Resolution"):

    1. ``explicit`` — the ``--vault`` flag
    2. ``ARTMIND_VAULT`` — for cron and anything with no meaningful cwd
    3. the walk up from the current directory

    An explicitly named vault that does not exist **raises** rather than
    falling back to the walk-up: silently ingesting into a different knowledge
    base than the one you named is the worst failure this system can have.
    """
    for value, source in ((explicit, "--vault"), (os.environ.get("ARTMIND_VAULT"), "ARTMIND_VAULT")):
        if not value:
            continue
        root = Path(value).expanduser().resolve()
        if not (root / MARKER).is_dir():
            raise VaultError(
                f"{source}={value!r} is not an artmind vault ({root / MARKER} not found). "
                f"Run `artmind init` inside it to make it one."
            )
        return root
    return find_vault(Path.cwd())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add artmind/vault.py test/test_vault.py
git commit -m "feat(vault): --vault and ARTMIND_VAULT outrank the walk-up"
```

---

## Task 3: Vault layout

**Files:**
- Modify: `artmind/vault.py`
- Test: `test/test_vault.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault.py`:

```python
def test_layout_places_everything_under_dot_artmind(tmp_path):
    layout = vault.VaultLayout(tmp_path)

    assert layout.artmind_dir == tmp_path / ".artmind"
    assert layout.config_env == tmp_path / ".artmind" / "config.env"
    assert layout.vault_yaml == tmp_path / ".artmind" / "vault.yaml"
    assert layout.state_json == tmp_path / ".artmind" / "state.json"
    assert layout.same_as == tmp_path / ".artmind" / "same_as.yaml"
    assert layout.schemas_dir == tmp_path / ".artmind" / "domains" / "schemas"
    assert layout.meta_yaml == tmp_path / ".artmind" / "domains" / "meta.yaml"
    assert layout.logs_dir == tmp_path / ".artmind" / "logs"


def test_derived_data_is_isolated_under_one_directory(tmp_path):
    """Everything ignorable sits under data/, so one .gitignore line covers it."""
    layout = vault.VaultLayout(tmp_path)
    data = tmp_path / ".artmind" / "data"

    assert layout.data_dir == data
    assert layout.kg_dir == data / "kg"
    assert layout.originals_dir == data / "originals"
    assert layout.chunks_dir == data / "chunks"
    assert layout.registry_db == data / "document_registry.db"
    assert layout.structured_dir == data / "structured"
    assert layout.snapshots_dir == data / "snapshots"
    assert layout.jobs_dir == data / "jobs"
    assert layout.refine_dir == data / "refine"


def test_skills_land_where_claude_code_looks(tmp_path):
    """ClaudeAgentOptions.skills resolves names from .claude/skills relative to
    the agent's cwd, which is the vault."""
    assert vault.VaultLayout(tmp_path).skills_dir == tmp_path / ".claude" / "skills"


def test_derived_markdown_stays_visible_in_the_vault(tmp_path):
    """_derived/ holds editable, promotable documents, so it is NOT hidden."""
    assert vault.VaultLayout(tmp_path).derived_dir == tmp_path / "_derived"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault.py -v`
Expected: FAIL, `AttributeError: module 'artmind.vault' has no attribute 'VaultLayout'`

- [ ] **Step 3: Write the implementation**

Add to `artmind/vault.py` (add `from dataclasses import dataclass` at the top):

```python
@dataclass(frozen=True)
class VaultLayout:
    """Where everything sits inside a vault (docs/vault.md, "Layout").

    One place that knows the layout, so a path is never spelled out twice. The
    split that matters: everything under ``data_dir`` is derived and gitignored;
    everything else under ``artmind_dir`` is authoritative and committed.
    """

    root: Path

    # ── authoritative, committed ──────────────────────────────────────────────
    @property
    def artmind_dir(self) -> Path:
        return self.root / MARKER

    @property
    def vault_yaml(self) -> Path:
        """The ingest manifest: folder->domain mapping and settings."""
        return self.artmind_dir / "vault.yaml"

    @property
    def same_as(self) -> Path:
        return self.artmind_dir / "same_as.yaml"

    @property
    def domains_dir(self) -> Path:
        return self.artmind_dir / "domains"

    @property
    def schemas_dir(self) -> Path:
        return self.domains_dir / "schemas"

    @property
    def meta_yaml(self) -> Path:
        return self.domains_dir / "meta.yaml"

    @property
    def derived_dir(self) -> Path:
        """Markdown converted from binaries. Visible and committed, NOT hidden:
        these are editable documents that become vault-native on promotion."""
        return self.root / "_derived"

    @property
    def skills_dir(self) -> Path:
        """`ClaudeAgentOptions.skills` takes names resolved from
        `.claude/skills/` relative to the agent's cwd, which is the vault."""
        return self.root / ".claude" / "skills"

    # ── machine-local, gitignored ────────────────────────────────────────────
    @property
    def config_env(self) -> Path:
        return self.artmind_dir / "config.env"

    @property
    def state_json(self) -> Path:
        """The ingest cursor: `last_ingested_commit`."""
        return self.artmind_dir / "state.json"

    @property
    def logs_dir(self) -> Path:
        return self.artmind_dir / "logs"

    # ── derived, gitignored ──────────────────────────────────────────────────
    @property
    def data_dir(self) -> Path:
        return self.artmind_dir / "data"

    @property
    def kg_dir(self) -> Path:
        return self.data_dir / "kg"

    @property
    def originals_dir(self) -> Path:
        """Only sources ingested from OUTSIDE the vault. A binary already in the
        vault is never copied (docs/stores-and-repos.md)."""
        return self.data_dir / "originals"

    @property
    def chunks_dir(self) -> Path:
        return self.data_dir / "chunks"

    @property
    def registry_db(self) -> Path:
        return self.data_dir / "document_registry.db"

    @property
    def structured_dir(self) -> Path:
        return self.data_dir / "structured"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def refine_dir(self) -> Path:
        return self.data_dir / "refine"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault.py -v`
Expected: PASS, 14 passed

- [ ] **Step 5: Commit**

```bash
git add artmind/vault.py test/test_vault.py
git commit -m "feat(vault): one place that knows the vault layout"
```

---

## Task 4: `paths.py` derives from the vault, with a legacy fallback

This is the riskiest task in the plan: `paths.py` is imported by every module, and its constants are read at import time. The fallback is what keeps the existing suite green.

**Files:**
- Modify: `paths.py`
- Test: `test/test_vault_paths.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_vault_paths.py`:

```python
"""paths.py deriving from a vault, and falling back when there is none.

Run in subprocesses because paths.py computes its constants at import; a
monkeypatched reload would not exercise the real code path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = (
    "import json, paths;"
    "print(json.dumps({"
    "'vault': str(paths.ARTMIND_VAULT_DIR) if paths.ARTMIND_VAULT_DIR else None,"
    "'home': str(paths.ARTMIND_HOME),"
    "'data': str(paths.ARTMIND_DATA_DIR),"
    "'schemas': str(paths.DOMAIN_SCHEMAS_DIR),"
    "'kg': str(paths.KG_DIR),"
    "}))"
)


def _probe(cwd: Path, **env_overrides) -> dict:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    for key in ("ARTMIND_HOME", "ARTMIND_DATA_DIR", "ARTMIND_VAULT"):
        env.pop(key, None)
    env.update({k: v for k, v in env_overrides.items() if v is not None})
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_paths_derive_from_the_discovered_vault(tmp_path):
    (tmp_path / ".artmind").mkdir()

    out = _probe(tmp_path)

    assert out["vault"] == str(tmp_path.resolve())
    assert out["home"] == str(tmp_path.resolve() / ".artmind")
    assert out["data"] == str(tmp_path.resolve() / ".artmind" / "data")
    assert out["schemas"] == str(tmp_path.resolve() / ".artmind" / "domains" / "schemas")
    assert out["kg"] == str(tmp_path.resolve() / ".artmind" / "data" / "kg")


def test_paths_follow_the_vault_from_a_subdirectory(tmp_path):
    (tmp_path / ".artmind").mkdir()
    deep = tmp_path / "notes" / "august"
    deep.mkdir(parents=True)

    assert _probe(deep)["vault"] == str(tmp_path.resolve())


def test_outside_a_vault_falls_back_to_the_legacy_layout(tmp_path):
    """The bridge that keeps the existing suite and existing installs working."""
    out = _probe(tmp_path)

    assert out["vault"] is None
    assert out["home"] == str(Path.home() / ".artmind")
    assert out["data"] == str(Path.home() / "artmind_data")


def test_artmind_home_still_overrides_everything(tmp_path):
    """conftest.py sets ARTMIND_HOME to a temp dir for the whole suite; that
    must keep working, vault or no vault."""
    (tmp_path / ".artmind").mkdir()
    override = tmp_path / "override"
    override.mkdir()

    out = _probe(tmp_path, ARTMIND_HOME=str(override))

    assert out["home"] == str(override.resolve())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_paths.py -v`
Expected: FAIL — `test_paths_derive_from_the_discovered_vault` fails because `home` is `~/.artmind`, not the vault's `.artmind`.

- [ ] **Step 3: Write the implementation**

In `paths.py`, replace the run-folder and data-dir blocks. Find:

```python
ARTMIND_HOME = Path(
    os.environ.get("ARTMIND_HOME") or (Path.home() / ".artmind")
).expanduser().resolve()
```

Replace with:

```python
from artmind.vault import VaultLayout, resolve_vault

# ── vault discovery ───────────────────────────────────────────────────────────
# A vault is a directory containing `.artmind/` (docs/vault.md). When we are
# inside one, every path below is a position inside it. When we are not, we fall
# back to the pre-vault layout unchanged, so existing installs and the test
# suite keep working while the migration proceeds file by file.
ARTMIND_VAULT_DIR = resolve_vault()
_LAYOUT = VaultLayout(ARTMIND_VAULT_DIR) if ARTMIND_VAULT_DIR else None

# ARTMIND_HOME remains the raw escape hatch, above vault discovery: CLAUDE.md
# documents it and test/conftest.py repoints it at a temp dir for the whole
# suite, which must keep working whether or not a vault is in play.
if os.environ.get("ARTMIND_HOME"):
    ARTMIND_HOME = Path(os.environ["ARTMIND_HOME"]).expanduser().resolve()
elif _LAYOUT is not None:
    ARTMIND_HOME = _LAYOUT.artmind_dir
else:
    ARTMIND_HOME = (Path.home() / ".artmind").resolve()
```

Then find the data-dir block:

```python
ARTMIND_DATA_DIR = Path(
    os.environ.get("ARTMIND_DATA_DIR") or (Path.home() / "artmind_data")
).expanduser().resolve()
```

Replace with:

```python
if os.environ.get("ARTMIND_DATA_DIR"):
    ARTMIND_DATA_DIR = Path(os.environ["ARTMIND_DATA_DIR"]).expanduser().resolve()
elif _LAYOUT is not None:
    ARTMIND_DATA_DIR = _LAYOUT.data_dir
else:
    ARTMIND_DATA_DIR = (Path.home() / "artmind_data").resolve()
```

Then delete the old `ARTMIND_VAULT_DIR` block entirely — the vault is discovered now, not configured:

```python
_vault = os.environ.get("ARTMIND_VAULT_DIR")
ARTMIND_VAULT_DIR = Path(_vault).expanduser().resolve() if _vault else None
```

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `uv run --group dev pytest test/test_vault_paths.py -v`
Expected: PASS, 4 passed

Run: `just dev-test`
Expected: PASS, 1734 passed, 14 skipped. The suite exercises the fallback branch, because `conftest.py` sets `ARTMIND_HOME` before anything imports `paths`.

- [ ] **Step 5: Commit**

```bash
git add paths.py test/test_vault_paths.py
git commit -m "feat(vault): derive paths from the discovered vault, legacy fallback intact"
```

---

## Task 4b: Two-level config loading

Without this, Task 6 writes a `config.env` that `paths.py` never reads.
`docs/vault.md` splits config by lifetime: machine-wide identity in
`~/.artmind/config.env`, this vault's graph in `<vault>/.artmind/config.env`.

**Files:**
- Modify: `paths.py`
- Test: `test/test_vault_config.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_vault_config.py`:

```python
"""Config loading, most-specific-first (docs/vault.md, "Machine-level config").

Subprocesses again: paths.py loads config at import.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = (
    "import json, os, paths;"
    "print(json.dumps({"
    "'db': os.environ.get('ARTMIND_KG_NEO4J_DATABASE', ''),"
    "'model': os.environ.get('ARTMIND_KG_LLM_MODEL', ''),"
    "'loaded': [str(p) for p in paths.LOADED_ENV_FILES],"
    "}))"
)


def _probe(cwd: Path, home: Path, **extra) -> dict:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "HOME": str(home)}
    for key in ("ARTMIND_HOME", "ARTMIND_DATA_DIR", "ARTMIND_VAULT",
                "ARTMIND_KG_NEO4J_DATABASE", "ARTMIND_KG_LLM_MODEL",
                "ARTMIND_ALLOW_REPO_ENV"):
        env.pop(key, None)
    env.update(extra)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _machine_config(home: Path, body: str) -> None:
    (home / ".artmind").mkdir(parents=True, exist_ok=True)
    (home / ".artmind" / "config.env").write_text(body)


def test_vault_config_overrides_the_machine_config(tmp_path):
    home = tmp_path / "home"
    _machine_config(home, "ARTMIND_KG_NEO4J_DATABASE=machine\nARTMIND_KG_LLM_MODEL=shared\n")
    vault_root = tmp_path / "MyVault"
    (vault_root / ".artmind").mkdir(parents=True)
    (vault_root / ".artmind" / "config.env").write_text("ARTMIND_KG_NEO4J_DATABASE=thisvault\n")

    out = _probe(vault_root, home)

    assert out["db"] == "thisvault", "the vault's own value must win"
    assert out["model"] == "shared", "machine identity still reaches the vault"


def test_machine_config_is_loaded_when_the_vault_has_none(tmp_path):
    home = tmp_path / "home"
    _machine_config(home, "ARTMIND_KG_LLM_MODEL=shared\n")
    vault_root = tmp_path / "MyVault"
    (vault_root / ".artmind").mkdir(parents=True)

    assert _probe(vault_root, home)["model"] == "shared"


def test_a_real_environment_variable_beats_both(tmp_path):
    home = tmp_path / "home"
    _machine_config(home, "ARTMIND_KG_NEO4J_DATABASE=machine\n")
    vault_root = tmp_path / "MyVault"
    (vault_root / ".artmind").mkdir(parents=True)
    (vault_root / ".artmind" / "config.env").write_text("ARTMIND_KG_NEO4J_DATABASE=thisvault\n")

    out = _probe(vault_root, home, ARTMIND_KG_NEO4J_DATABASE="fromenv")

    assert out["db"] == "fromenv"


def test_the_legacy_dot_env_still_loads(tmp_path):
    """Existing installs keep working: ~/.artmind/.env is still read."""
    home = tmp_path / "home"
    (home / ".artmind").mkdir(parents=True)
    (home / ".artmind" / ".env").write_text("ARTMIND_KG_LLM_MODEL=legacy\n")
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    assert _probe(outside, home)["model"] == "legacy"


def test_the_checkout_env_is_not_loaded_by_default(tmp_path):
    """Guardrail 1: it silently loaded another knowledge base's credentials and
    graph whenever a run folder had none of its own."""
    home = tmp_path / "home"
    (home / ".artmind").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    loaded = _probe(outside, home)["loaded"]

    assert not [p for p in loaded if p == str(REPO_ROOT / ".env")]


def test_the_checkout_env_can_be_opted_back_in(tmp_path):
    home = tmp_path / "home"
    (home / ".artmind").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    loaded = _probe(outside, home, ARTMIND_ALLOW_REPO_ENV="1")["loaded"]

    if (REPO_ROOT / ".env").is_file():
        assert str(REPO_ROOT / ".env") in loaded
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_config.py -v`
Expected: FAIL — `AttributeError: module 'paths' has no attribute 'LOADED_ENV_FILES'`

- [ ] **Step 3: Write the implementation**

In `paths.py`, replace the whole env-loading block:

```python
ENV_FILE = ARTMIND_HOME / ".env"
for _candidate in (ARTMIND_HOME / ".env", _SELF_DIR / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        ENV_FILE = _candidate
        break
```

with:

```python
# ── config, loaded MOST SPECIFIC FIRST ────────────────────────────────────────
# `override=False` means an already-set key wins, so reading the vault's own
# config before the machine's is what makes the vault override the machine
# rather than the reverse. Real environment variables were set before either and
# therefore beat both.
#
# Secrets stay machine-wide because a vault is a repo you may push: the line is
# "secrets and models belong to the machine; knowledge belongs to the vault"
# (docs/vault.md).
#
# There is deliberately NO implicit fallback to a checkout-local .env: it
# silently loaded another knowledge base's config -- credentials and graph
# included -- whenever a run folder had none of its own.
MACHINE_CONFIG_DIR = (Path.home() / ".artmind").resolve()
MACHINE_CONFIG_ENV = MACHINE_CONFIG_DIR / "config.env"

LOADED_ENV_FILES: "list[Path]" = []
_candidates = [
    ARTMIND_HOME / "config.env",   # this vault
    ARTMIND_HOME / ".env",         # legacy run folder, still honoured
    MACHINE_CONFIG_ENV,            # machine-wide identity
]
if os.environ.get("ARTMIND_ALLOW_REPO_ENV", "").strip().lower() in ("1", "true", "yes"):
    _candidates.append(_SELF_DIR / ".env")
for _candidate in _candidates:
    if _candidate.is_file() and _candidate not in LOADED_ENV_FILES:
        load_dotenv(_candidate, override=False)
        LOADED_ENV_FILES.append(_candidate)

# Retained for backward compatibility. Prefer LOADED_ENV_FILES, which reports
# every file read rather than just the first.
ENV_FILE = LOADED_ENV_FILES[0] if LOADED_ENV_FILES else ARTMIND_HOME / ".env"
```

Note the dedup check matters: outside a vault `ARTMIND_HOME` *is* `~/.artmind`,
so `ARTMIND_HOME / "config.env"` and `MACHINE_CONFIG_ENV` are the same file.

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `uv run --group dev pytest test/test_vault_config.py -v`
Expected: PASS, 6 passed

Run: `just dev-test`
Expected: PASS, 1734 passed

- [ ] **Step 5: Commit**

```bash
git add paths.py test/test_vault_config.py
git commit -m "feat(vault): load vault config over machine config, drop the repo .env fallback"
```

---

## Task 5: `.gitignore` for a new vault

**Files:**
- Modify: `artmind/vault.py`
- Test: `test/test_vault_scaffold.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_vault_scaffold.py`:

```python
"""What `artmind init` writes into a fresh directory (docs/vault.md)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from artmind import vault


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return tmp_path


def test_derived_data_is_ignored(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data" / "kg").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "kg" / "doc.json").write_text("{}")

    assert "data/kg/doc.json" not in _git(tmp_path, "status", "--porcelain")


def test_curation_and_schemas_are_committed(tmp_path):
    """same_as.yaml is authoritative curation; losing it means redoing human
    merge adjudication."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "domains" / "schemas").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "same_as.yaml").write_text("groups: []\n")
    (tmp_path / ".artmind" / "domains" / "schemas" / "general_schema.yaml").write_text("name: general\n")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")
    assert ".artmind/same_as.yaml" in status
    assert ".artmind/domains/schemas/general_schema.yaml" in status


def test_the_graph_password_is_never_committed(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".artmind").mkdir(parents=True, exist_ok=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "config.env").write_text("ARTMIND_KG_NEO4J_PASSWORD=secret\n")

    assert "config.env" not in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def test_binaries_are_ignored_but_extracted_images_are_not(tmp_path):
    """The negation that matters: a .pptx is opaque and stays out, but the
    images docling extracted are referenced by committed markdown, so without
    them Obsidian renders broken."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind").mkdir()
    vault.write_gitignore(tmp_path)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "deck.pptx").write_bytes(b"binary")
    artifacts = tmp_path / "_derived" / "general" / "deck_artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "image-1.png").write_bytes(b"png")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")
    assert "sources/deck.pptx" not in status
    assert "_derived/general/deck_artifacts/image-1.png" in status


def test_writing_the_gitignore_twice_does_not_duplicate_it(tmp_path):
    (tmp_path / ".artmind").mkdir()
    vault.write_gitignore(tmp_path)
    first = (tmp_path / ".gitignore").read_text()
    vault.write_gitignore(tmp_path)

    assert (tmp_path / ".gitignore").read_text() == first


def test_an_existing_gitignore_is_appended_to_not_replaced(tmp_path):
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".gitignore").write_text(".DS_Store\n")

    vault.write_gitignore(tmp_path)

    content = (tmp_path / ".gitignore").read_text()
    assert ".DS_Store" in content
    assert ".artmind/data/" in content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v`
Expected: FAIL, `AttributeError: module 'artmind.vault' has no attribute 'write_gitignore'`

- [ ] **Step 3: Write the implementation**

Add to `artmind/vault.py`:

```python
# The authoritative/derived split as a mechanism rather than prose
# (docs/stores-and-repos.md). Git holds what git can meaningfully version:
# documents, the markdown derived from binaries, and the images that markdown
# references. Not opaque binaries, not regenerable derivatives, not credentials.
GITIGNORE_BLOCK = """\
# ── artmind ───────────────────────────────────────────────────────────────────
# Derived and unbounded: KG staging, chunks, the registry, snapshots.
.artmind/data/
# Machine-local runtime state.
.artmind/logs/
.artmind/state.json
.artmind/serve.json
.artmind/worker.pid
# May hold the graph password.
.artmind/config.env
# artmind's own skills are symlinks to the installed copy; yours are not
# matched by this and stay committable.
.claude/skills/artmind-*

# Opaque binaries: git versions their markdown in _derived/ instead. NOTE this
# means a binary here has no version history and no second copy -- backing it
# up is yours to arrange (docs/stores-and-repos.md).
*.pdf
*.pptx
*.docx
*.xlsx
*.png
*.jpg
*.jpeg
*.gif
*.webp
# ...except images docling extracted, which committed markdown references.
!_derived/**
# ── end artmind ───────────────────────────────────────────────────────────────
"""

_GITIGNORE_SENTINEL = "# ── artmind ─"


def write_gitignore(root: Path) -> bool:
    """Add artmind's ignore rules to the vault's `.gitignore`.

    Appends rather than replaces — the vault may be an established repo with
    rules of its own — and is idempotent, so re-running `init` is safe.
    Returns True when the file was changed.
    """
    target = Path(root) / ".gitignore"
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    if _GITIGNORE_SENTINEL in existing:
        return False
    prefix = existing if existing.endswith("\n") or not existing else existing + "\n"
    target.write_text(prefix + ("\n" if prefix else "") + GITIGNORE_BLOCK, encoding="utf-8")
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add artmind/vault.py test/test_vault_scaffold.py
git commit -m "feat(vault): write the authoritative/derived split as a gitignore"
```

---

## Task 6: Scaffold a vault

**Files:**
- Modify: `artmind/setup.py`
- Test: `test/test_vault_scaffold.py`

Note on schema seeding: `docs/vault.md` says `init` seeds only the **starter** set and only what is missing, because overwrite-always would clobber hand-authored vault schemas. Full provenance tracking and `artmind domains update` are follow-on plan 3; this task implements the seed-if-missing half.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_scaffold.py`:

```python
def test_scaffold_creates_the_vault_skeleton(tmp_path):
    from artmind.setup import scaffold_vault

    result = scaffold_vault(tmp_path)

    layout = vault.VaultLayout(tmp_path)
    assert layout.artmind_dir.is_dir()
    assert layout.schemas_dir.is_dir()
    assert layout.data_dir.is_dir()
    assert layout.logs_dir.is_dir()
    assert layout.meta_yaml.is_file()
    assert layout.config_env.is_file()
    assert result["vault"] == str(tmp_path)


def test_scaffold_seeds_starter_schemas_only(tmp_path):
    """A personal vault has no use for the banking demo corpus's domains, and
    offering domains with no data degrades the agent's routing."""
    from artmind.setup import scaffold_vault

    seeded = scaffold_vault(tmp_path)["schemas"]

    assert "general" in seeded
    assert not [s for s in seeded if s.startswith("banking")], seeded


def test_scaffold_never_overwrites_an_edited_schema(tmp_path):
    """Overwrite-always was safe for one reseeded run folder; here it would
    destroy authored work."""
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    schema = vault.VaultLayout(tmp_path).schemas_dir / "general_schema.yaml"
    schema.write_text("name: general\n# my edit\n")

    scaffold_vault(tmp_path)

    assert "# my edit" in schema.read_text()


def test_scaffold_never_overwrites_config_env(tmp_path):
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    config = vault.VaultLayout(tmp_path).config_env
    config.write_text("ARTMIND_KG_NEO4J_DATABASE=mine\n")

    scaffold_vault(tmp_path)

    assert config.read_text() == "ARTMIND_KG_NEO4J_DATABASE=mine\n"


def test_scaffold_writes_a_starter_vault_yaml(tmp_path):
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)

    import yaml
    manifest = yaml.safe_load(vault.VaultLayout(tmp_path).vault_yaml.read_text())
    assert manifest["ingest"]["trigger"] == "manual"
    assert manifest["ingest"]["mappings"] == []


def test_scaffold_symlinks_skills_to_the_installed_copy(tmp_path):
    """One canonical copy, so an artmind upgrade reaches every vault without
    re-seeding."""
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)

    linked = vault.VaultLayout(tmp_path).skills_dir / "artmind-query"
    assert linked.is_symlink()
    assert (linked / "SKILL.md").is_file()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v`
Expected: FAIL, `ImportError: cannot import name 'scaffold_vault' from 'artmind.setup'`

- [ ] **Step 3: Write the implementation**

Add to `artmind/setup.py` (imports: `from pathlib import Path`, `import yaml`, and `from artmind.vault import VaultLayout, write_gitignore`):

```python
# Which schemas a NEW vault starts with. The `banking.*` family is a demo
# corpus's schemas, not a default every knowledge base should inherit
# (docs/vault.md, "Schemas").
STARTER_SCHEMAS = ("general", "personal_journal")

_STARTER_VAULT_YAML = """\
# artmind ingest manifest (docs/vault.md).
#
# `mappings` does two jobs: it says which domain governs a path's extraction,
# AND whether to ingest it at all. An unmapped path is never ingested -- so an
# attachments folder needs no ignore rule, and an unmapped Inbox/ is a drafting
# area where MOVING a note into a mapped folder is what says "this is ready".
ingest:
  # manual | commit | schedule. Default manual: nobody should discover
  # automatic LLM spend by surprise.
  trigger: manual
  mappings: []
  #  - path: notes/**
  #    domain: personal_journal
"""


def scaffold_vault(root: Path) -> dict:
    """Make `root` an artmind vault. Idempotent, and never destructive.

    Package assets are seeded ONLY when absent. This inverts
    `scaffold_run_folder`'s overwrite-always policy for schemas, which was safe
    when one run folder was reseeded from the package but would now clobber
    hand-authored vault schemas (docs/vault.md, "Schemas"). Skills keep their
    always-current property a different way -- they are symlinked to the
    installed copy rather than copied.
    """
    root = Path(root).expanduser().resolve()
    layout = VaultLayout(root)

    for directory in (
        layout.artmind_dir, layout.domains_dir, layout.schemas_dir,
        layout.data_dir, layout.kg_dir, layout.originals_dir, layout.chunks_dir,
        layout.structured_dir, layout.snapshots_dir, layout.jobs_dir,
        layout.refine_dir, layout.logs_dir, layout.skills_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    seeded_schemas: list[str] = []
    for src in sorted(PACKAGE_SCHEMAS_DIR.glob("*_schema.yaml")):
        name = src.name.removesuffix("_schema.yaml")
        if name not in STARTER_SCHEMAS:
            continue
        dest = layout.schemas_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        seeded_schemas.append(name)

    if PACKAGE_META_YAML.is_file() and not layout.meta_yaml.exists():
        shutil.copy2(PACKAGE_META_YAML, layout.meta_yaml)

    if not layout.config_env.exists() and PACKAGE_ENV_EXAMPLE.is_file():
        shutil.copy2(PACKAGE_ENV_EXAMPLE, layout.config_env)
        layout.config_env.chmod(0o600)

    if not layout.vault_yaml.exists():
        layout.vault_yaml.write_text(_STARTER_VAULT_YAML, encoding="utf-8")

    linked = _symlink_skills(layout.skills_dir)
    gitignore_written = write_gitignore(root)

    return {
        "vault": str(root),
        "schemas": seeded_schemas,
        "skills": linked,
        "gitignore": gitignore_written,
    }


def _symlink_skills(dest: Path) -> list[str]:
    """Symlink each packaged skill into the vault's `.claude/skills/`.

    Symlinks rather than copies so an artmind upgrade reaches every vault with
    no re-seeding and no N-copies-to-update problem -- the same pattern the
    checkout already uses (see CLAUDE.md). Where symlinks are unavailable
    (Windows without privileges, some sync services) fall back to a copy; the
    cost is that upgrades then need an explicit refresh.
    """
    linked: list[str] = []
    for src in sorted(PACKAGE_SKILLS_DIR.iterdir()):
        if not src.is_dir():
            continue
        target = dest / src.name
        if target.is_symlink() or target.exists():
            if target.is_symlink() and target.resolve() == src.resolve():
                linked.append(src.name)
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
        try:
            target.symlink_to(src, target_is_directory=True)
        except OSError:
            shutil.copytree(src, target)
        linked.append(src.name)
    return linked
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v`
Expected: PASS, 12 passed

- [ ] **Step 5: Commit**

```bash
git add artmind/setup.py test/test_vault_scaffold.py
git commit -m "feat(vault): scaffold a vault without clobbering authored work"
```

---

## Task 7: `artmind init` makes the current directory a vault

**Files:**
- Modify: `artmind/cli.py` (the `init` command)
- Test: `test/test_vault_cli.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_vault_cli.py`:

```python
"""`artmind init` and `artmind vault` (docs/vault.md)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from artmind import vault
from artmind.cli import cli


def test_init_makes_the_current_directory_a_vault(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert vault.VaultLayout(tmp_path).artmind_dir.is_dir()
    assert vault.find_vault(tmp_path) == tmp_path.resolve()


def test_init_runs_git_init_when_the_directory_is_not_a_repo(tmp_path, monkeypatch):
    """The vault IS a git repo: identity, history and the ingest cursor all
    depend on it."""
    monkeypatch.chdir(tmp_path)

    CliRunner().invoke(cli, ["init"])

    assert (tmp_path / ".git").is_dir()


def test_init_leaves_an_existing_repo_alone(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "note.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    CliRunner().invoke(cli, ["init"])

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "first" in log


def test_init_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])
    schema = vault.VaultLayout(tmp_path).schemas_dir / "general_schema.yaml"
    schema.write_text("name: general\n# edited\n")

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "# edited" in schema.read_text()


def test_init_accepts_an_explicit_path(tmp_path):
    target = tmp_path / "MyVault"
    target.mkdir()

    result = CliRunner().invoke(cli, ["init", str(target)])

    assert result.exit_code == 0, result.output
    assert (target / ".artmind").is_dir()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_cli.py -v`
Expected: FAIL — `init` scaffolds `~/.artmind` (redirected by conftest), so `.artmind` is not created in `tmp_path`.

- [ ] **Step 3: Write the implementation**

In `artmind/cli.py`, replace the whole `init` command with:

```python
@cli.command("init")
@click.argument("directory", type=click.Path(file_okay=False), default=".")
def init(directory: str):
    """Make DIRECTORY (default: the current one) an artmind vault.

    The vault is your Obsidian vault, your git repo and your artmind knowledge
    base at once — `git init` for knowledge. Everything artmind knows about it
    lives in `.artmind/` inside it, and every command run from anywhere beneath
    it anchors here (docs/vault.md).
    """
    from artmind.setup import scaffold_vault

    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise click.ClickException(f"{root} does not exist.")

    git_initialised = False
    if not (root / ".git").exists():
        # The vault IS a repo: document identity, history, and the ingest
        # cursor all key off it.
        result = subprocess.run(
            ["git", "init", "-q"], cwd=root, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise click.ClickException(f"git init failed: {result.stderr.strip()}")
        git_initialised = True

    try:
        summary = scaffold_vault(root)
    except Exception as e:
        raise click.ClickException(str(e))

    click.echo(f"Vault:    {summary['vault']}")
    if git_initialised:
        click.echo("Git:      initialised")
    click.echo(f"Schemas:  {', '.join(summary['schemas']) or '(none)'}")
    click.echo(f"Skills:   {len(summary['skills'])} linked")
    click.echo(f"Manifest: {root / '.artmind' / 'vault.yaml'}")
    click.echo(
        f"\nNext:\n"
        f"  $EDITOR {root / '.artmind' / 'config.env'}   # Neo4j connection\n"
        f"  artmind setup                                  # graph constraints + indexes\n"
        f"  $EDITOR {root / '.artmind' / 'vault.yaml'}   # map folders to domains"
    )
```

- [ ] **Step 4: Run the tests, then the whole suite**

Run: `uv run --group dev pytest test/test_vault_cli.py -v`
Expected: PASS, 5 passed

Run: `just dev-test`
Expected: PASS. If `test/test_scaffold_run_folder.py` fails, it is asserting the *old* `init` behaviour — update it to call `scaffold_run_folder()` directly rather than through the CLI, since that function still exists for the legacy layout.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_vault_cli.py test/test_scaffold_run_folder.py
git commit -m "feat(vault): artmind init makes the current directory a vault"
```

---

## Task 8: `artmind vault` status command

**Files:**
- Modify: `artmind/cli.py`
- Test: `test/test_vault_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_cli.py`:

```python
def test_vault_reports_the_active_vault(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])

    result = CliRunner().invoke(cli, ["vault"])

    assert result.exit_code == 0, result.output
    assert str(tmp_path.resolve()) in result.output


def test_vault_outside_a_vault_explains_rather_than_guessing(tmp_path, monkeypatch):
    """`git status` outside a repo, not a silent default."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.delenv("ARTMIND_HOME", raising=False)

    result = CliRunner().invoke(cli, ["vault"])

    assert result.exit_code != 0
    assert "artmind init" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_cli.py::test_vault_reports_the_active_vault -v`
Expected: FAIL, `Error: No such command 'vault'.`

- [ ] **Step 3: Write the implementation**

Add to `artmind/cli.py`, immediately before the `# ── artmind setup ─` banner:

```python
# ── artmind vault ─────────────────────────────────────────────────────────────


@cli.command("vault")
@click.option("--compact", is_flag=True, help="Emit compact JSON instead of the summary")
def vault_status(compact: bool):
    """Show which vault is active and how it was resolved.

    Human-readable by default rather than JSON-first like `projection status`:
    this exists to be glanced at, and a wrong answer here means every other
    command is operating on the wrong knowledge base.
    """
    from artmind import vault as vault_mod
    from paths import ARTMIND_DATA_DIR, ARTMIND_HOME, ARTMIND_VAULT_DIR, LOADED_ENV_FILES

    if ARTMIND_VAULT_DIR is None:
        raise click.ClickException(
            "Not inside an artmind vault.\n"
            "  cd into one, or run `artmind init` to make this directory a vault."
        )

    layout = vault_mod.VaultLayout(ARTMIND_VAULT_DIR)
    info = {
        "vault": str(ARTMIND_VAULT_DIR),
        "artmind_dir": str(ARTMIND_HOME),
        "data_dir": str(ARTMIND_DATA_DIR),
        "manifest": str(layout.vault_yaml) if layout.vault_yaml.is_file() else None,
        "config": [str(p) for p in LOADED_ENV_FILES],
        "graph": {
            "uri": os.environ.get("ARTMIND_KG_NEO4J_URI", ""),
            "database": os.environ.get("ARTMIND_KG_NEO4J_DATABASE", ""),
        },
    }
    if compact:
        _echo_json(info, compact=True)
        return
    click.echo(f"Vault:    {info['vault']}")
    click.echo(f"Data:     {info['data_dir']}")
    click.echo(f"Manifest: {info['manifest'] or '(none — run artmind init)'}")
    click.echo(f"Config:   {', '.join(info['config']) or '(none loaded)'}")
    click.echo(f"Graph:    {info['graph']['uri'] or '(unset)'}  db={info['graph']['database'] or '(unset)'}")
```

Then route it into the CLI guide. In `click.rich_click.COMMAND_GROUPS`, change the `"Setup & tools"` entry to:

```python
        {"name": "Setup & tools", "commands": ["vault", "setup", "init", "serve", "chat-ui", "admin-ui"]},
```

- [ ] **Step 4: Run the tests, then the guide's coverage check**

Run: `uv run --group dev pytest test/test_vault_cli.py test/test_cli_guide.py -v`
Expected: PASS. `test_every_command_is_routed_into_the_guide` fails if you skipped the `COMMAND_GROUPS` edit.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_vault_cli.py
git commit -m "feat(vault): artmind vault reports the active vault"
```

---

## Task 9: `just dev-install` stops creating a run folder

**Files:**
- Modify: `justfile:47-49`

- [ ] **Step 1: Make the change**

Installing the CLI and creating a vault are separate acts, and at install time there is no vault to initialise. In `justfile`, replace:

```make
dev-install: dev-stop-daemons
    uv tool install --force --editable '.[ingest]'
    artmind init
```

with:

```make
# Install the global `artmind` command. Deliberately does NOT run `artmind init`
# any more: init means "make THIS directory a vault" (docs/vault.md), and at
# install time there is no vault. Create one with `cd <dir> && artmind init`.
dev-install: dev-stop-daemons
    uv tool install --force --editable '.[ingest]'
    @echo "Installed. Create a vault with:  mkdir ~/MyVault && cd ~/MyVault && artmind init"
```

Also update the comment block above it (`justfile:45-46`), which currently says "Then edit ~/.artmind/.env and run `artmind setup`":

```make
# `artmind` runs from anywhere, and anchors to whichever vault you are standing
# in. Create one with `artmind init`, then edit <vault>/.artmind/config.env and
# run `artmind setup`. See docs/INSTALL.md.
```

- [ ] **Step 2: Verify install still works**

Run: `just dev-install`
Expected: installs, prints the "Create a vault with…" hint, does **not** create or touch `~/.artmind`.

Run: `artmind --help`
Expected: the command hierarchy, including `vault`.

- [ ] **Step 3: Commit**

```bash
git add justfile
git commit -m "chore: dev-install no longer runs artmind init"
```

---

## Task 10: Rewrite `docs/INSTALL.md`

**Files:**
- Rewrite: `docs/INSTALL.md`

- [ ] **Step 1: Rewrite the document**

`docs/INSTALL.md` is named by `CLAUDE.md` as the authoritative install/runtime reference, so it must not lag the code. Replace its entire contents with the following. Keep the `Core vs the [ingest] extra`, `Daemons`, and `Upgrade / uninstall` sections **verbatim from the current file** — they are unaffected by this change — and replace everything else:

```markdown
# Installing artmind

artmind installs as a global `artmind` command and anchors to whichever **vault**
you are standing in.

## The vault

A vault is a directory containing `.artmind/`. It is your Obsidian vault, your
git repo and your artmind knowledge base at once. Commands walk up from the
current directory to find it, exactly as git walks up for `.git/`:

```bash
cd ~/Notes         && artmind query …     # this vault
cd ~/work-research && artmind admin-ui    # that vault
```

Two terminals, two vaults, at once. There is no "current vault" setting to get
wrong. Outside any vault, commands that need one fail with guidance rather than
guessing.

| Inside the vault | Holds | In git |
|---|---|---|
| `.artmind/vault.yaml` | the ingest manifest: folder→domain mapping | yes |
| `.artmind/domains/` | schemas + meta-schema | yes |
| `.artmind/same_as.yaml` | curation | yes |
| `.artmind/config.env` | this vault's Neo4j connection | no |
| `.artmind/data/` | originals, chunks, KG staging, registry, snapshots | no |
| `.artmind/logs/` | logs | no |
| `.claude/skills/` | artmind's (symlinked) + your own | only yours |
| `_derived/<domain>/` | markdown converted from your binaries, plus its images | yes |

One file stays global: `~/.artmind/config.env`, holding the LLM provider, API
keys and models. Secrets must not live in a vault you may push. Config loads
most-specific-first, so a vault's `config.env` overrides the machine's, and real
environment variables beat both.

Resolution precedence: `--vault PATH`, then `ARTMIND_VAULT`, then the walk up
from the current directory.

## Prerequisites

- Python (see `.python-version`) and [`uv`](https://docs.astral.sh/uv/).
- A running **Neo4j** with vector-index support.
- LLM/embeddings access: local **Ollama**, or an **OpenRouter** API key.
- `git` — a vault is a git repo.

## Install

```bash
just dev-install
```

This is the single install path for both development and running. It is
editable, so code edits are live, and the `artmind` command runs from any
directory. It does **not** create anything: installing the CLI and creating a
vault are separate acts.

## Create a vault

```bash
mkdir ~/MyVault && cd ~/MyVault
artmind init
```

`artmind init` runs `git init` if needed, creates `.artmind/`, writes a
`.gitignore` that encodes the authoritative/derived split, seeds the starter
domain schemas, symlinks artmind's skills into `.claude/skills/`, and writes a
starter `vault.yaml`. It is idempotent and needs no Neo4j, and it **never
overwrites** a schema or config file you have edited.

Then:

```bash
$EDITOR ~/.artmind/config.env          # provider, API keys, models (machine-wide)
$EDITOR ~/MyVault/.artmind/config.env  # this vault's Neo4j connection
artmind setup                          # Neo4j constraints/indexes + SQLite tables
```

## Run

```bash
cd ~/MyVault
artmind query graph metadata --domain <domain> --compact
artmind serve
artmind admin-ui
```

The chat agent's working directory is the vault, so it can read your documents
and finds artmind's skills at `.claude/skills/`.
```

- [ ] **Step 2: Verify every command in the doc**

Run each command block in a scratch directory and confirm the described output.
Any command that does not behave as written is a doc bug — fix the doc, not the
test.

- [ ] **Step 3: Commit**

```bash
git add docs/INSTALL.md
git commit -m "docs: rewrite INSTALL.md for the vault model"
```

---

## Task 11: Update `README.md`

**Files:**
- Modify: `README.md`

The README needs two independent passes. Do them as one commit but verify each
separately.

- [ ] **Step 1: Fix the stale references**

These are wrong today, independent of the vault change. Verified by inspection
on 2026-08-30:

| Says | Reality | Action |
|---|---|---|
| `artmind-refine` skill (4 mentions) | does not exist; `artmind/skills/` holds `artmind-create-schema`, `artmind-curate`, `artmind-ingestion-helper`, `artmind-query`, `artmind-update` | replace the `artmind-refine` section with `artmind-curate`, and fix "five Claude Code skills, located under `skills/`" to name the real five under `artmind/skills/` |
| `uv run artmind docs clean` | no such command; removal is `artmind docs archive`, and `docs --help` says "the only removal artmind has. There is deliberately no `purge`" | replace the "Remove a document" section with `docs archive` |
| `docs/refine-merge-conflict-supersede-guide.md` | archived to `docs/archive/` | drop the link |
| `domains/schemas/` (2 mentions) | `artmind/domains/schemas/` | correct both |
| `skills/` in Project layout | `artmind/skills/` | correct, and drop `scripts/migrate_poole.py` if absent |
| "Everything runs locally: no cloud APIs, no telemetry" | OpenRouter and hosted AuraDB are both first-class | soften to "runs fully locally with Ollama + a local Neo4j, or against hosted providers" |

- [ ] **Step 2: Rewrite the install and layout sections for vaults**

Replace the "Global CLI install (optional)" section — including the `> **Note:**`
block claiming data lives inside the cloned repo, which has been false since the
run-folder split and is doubly false now:

```markdown
### Install the CLI

```bash
just dev-install
```

`artmind` then works from any directory, anchoring to whichever **vault** you are
standing in — a directory containing `.artmind/`, discovered by walking up from
the current directory exactly as git finds `.git/`.

### Create a vault

```bash
mkdir ~/MyVault && cd ~/MyVault
artmind init
```

Your documents, schemas, curation and derived data all live inside that
directory; it is also a git repo and can be an Obsidian vault. Only credentials
stay machine-wide, in `~/.artmind/config.env`.

See [docs/INSTALL.md](docs/INSTALL.md) for the full layout and
[docs/vault.md](docs/vault.md) for why it works this way.
```

Then replace the single `Edit .env:` dotenv block with these two, which split
by lifetime:

````markdown
Machine-wide — `~/.artmind/config.env`. Credentials and models, shared by every
vault:

```dotenv
# LLM for extraction
ARTMIND_KG_LLM_PROVIDER=ollama
ARTMIND_KG_LLM_URL=http://localhost:11434
ARTMIND_KG_LLM_MODEL=qwen3.6:35b-a3b-coding-nvfp4

# Embeddings
ARTMIND_KG_EMBEDDINGS_PROVIDER=ollama
ARTMIND_KG_EMBEDDINGS_URL=http://localhost:11434
ARTMIND_KG_EMBEDDINGS_MODEL=nomic-embed-text:latest
ARTMIND_KG_EMBEDDING_DIMENSIONS=768

# Image descriptions (used when ingesting PDFs that contain images)
ARTMIND_IMAGE_MODEL=gemma4:e4b
ARTMIND_OLLAMA_TIMEOUT=600

# Your identity for update audit trails (optional)
ARTMIND_USER=you@example.com
```

Per-vault — `<vault>/.artmind/config.env`. Each vault has its own graph, and
this file is gitignored because it holds a password:

```dotenv
ARTMIND_KG_NEO4J_URI=neo4j://127.0.0.1:7687
ARTMIND_KG_NEO4J_USERNAME=neo4j
ARTMIND_KG_NEO4J_PASSWORD=your_password
ARTMIND_KG_NEO4J_DATABASE=your_neo4j_database
```

Config loads most-specific-first, so a vault's value overrides the machine's,
and a real environment variable beats both.
````

Finally, in "Justfile recipes", change:

```
just dev-install                    # put `artmind` on PATH + scaffold ~/.artmind
```

to:

```
just dev-install                # put `artmind` on PATH (create a vault with `artmind init`)
```

- [ ] **Step 3: Verify the claims**

Run: `uv run artmind --help` and `ls artmind/skills/`
Confirm every command and skill the README names actually exists. This is the
check that would have caught `artmind-refine` and `docs clean`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README for the vault model, plus a correctness pass"
```

---

## Task 12: End-to-end verification

Green tests do not mean the CLI works (`CLAUDE.md`). This task is manual and
uses a real vault.

- [ ] **Step 1: Stop any stale daemons**

Run: `just dev-stop-daemons`
Expected: any running `serve` / `chat-ui` / `admin-ui` / worker are stopped. A
daemon started before this change resolved its paths the old way and will
silently serve them.

- [ ] **Step 2: Reinstall and create a real vault**

```bash
just dev-install
mkdir -p /tmp/artmind-e2e && cd /tmp/artmind-e2e && artmind init
```

Expected: `Vault: /tmp/artmind-e2e`, git initialised, starter schemas listed
with **no** `banking.*` entries, skills linked.

- [ ] **Step 3: Confirm the vault is discovered from a subdirectory**

```bash
mkdir -p /tmp/artmind-e2e/notes/deep && cd /tmp/artmind-e2e/notes/deep
artmind vault
```

Expected: `Vault: /tmp/artmind-e2e`, and `Data:` under `.artmind/data`.

- [ ] **Step 4: Confirm the gitignore split**

```bash
cd /tmp/artmind-e2e && git status --porcelain --untracked-files=all
```

Expected: `.artmind/vault.yaml`, `.artmind/domains/…` and `.gitignore` appear;
`.artmind/data/`, `.artmind/config.env` and `.artmind/logs/` do not.

- [ ] **Step 5: Confirm the legacy fallback still works**

```bash
cd ~ && ARTMIND_NO_PROXY=1 artmind vault
```

Expected: fails with "Not inside an artmind vault" and the `artmind init` hint —
**not** a stack trace, and not a silent default to `~/.artmind`.

- [ ] **Step 6: Clean up**

```bash
rm -rf /tmp/artmind-e2e
```

---

## Follow-on plans

Each is independently shippable and should be written as its own plan once this
one lands. Ordered by dependency.

1. **Ingest manifest** — read `.artmind/vault.yaml`; folder→domain mapping with
   the precedence chain from `docs/vault.md`; unmapped paths never ingested; a
   supported-type allowlist so `.canvas` files are skipped and reported instead
   of handed to docling.
2. **Vault-resident sources** — stop copying a binary that already lives in the
   vault; delete `documents/markdowns/` in favour of `_derived/`; commit
   extracted images alongside their markdown.
3. **Schema and skill lifecycle** — schema provenance (`_source: package` +
   hash) and `artmind domains update`, refreshing only unmodified schemas and
   reporting the diverged.
4. **Ingest triggers** — `.artmind/state.json` cursor, "enqueue everything
   between the cursor and HEAD", the `trigger:` setting, and the git-hook and
   schedule pokes.
5. **Daemon discovery** — bind port 0, write `.artmind/serve.json`, have
   `_entry.py` read it; retire fixed ports.
6. **Admin-ui snapshot delete** — a per-entry delete button so snapshots can be
   downloaded, stored elsewhere and removed from the vault.
7. **`artmind vault adopt`** — fold an existing `~/.artmind` + `~/artmind_data`
   into a directory's `.artmind/`, copying and leaving the original in place.
   This is what migrates the banking corpus.
