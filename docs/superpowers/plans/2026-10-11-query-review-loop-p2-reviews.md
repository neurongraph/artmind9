# Query review loop — P2: review skill, dossiers, `artmind review` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a recorded trace into a diagnosed, self-contained review dossier in the vault. A dossier holds the failure stage with cited evidence, a shape audit, a proposal, a synthetic example, a regression question, and a privacy-guarded agent prompt. The work is driven by a new `artmind-review` skill and an `artmind review` CLI.

**Architecture:**
- A new `artmind/reviews/` package:
  - `dossier`: the file format, parsing and rendering, and atomic save.
  - `validate`: per-stage completeness checks.
  - `scaffold`: builds a new dossier from a trace via `artmind.traces.analysis.show`.
  - `index`: keeps the local `trace_reviews` index in sync with the dossier files.
  - `leakcheck`: scans a checkout's diff for private terms and quoted text.
- **Dossiers** live under `paths.REVIEWS_DIR`:
  - In a vault, that's `<vault>/.artmind/reviews/`. The folder is committed with the vault's private repo, and nothing ingests it because the walk skips dot-directories.
  - Outside a vault, it's `~/.artmind/reviews/`.
- **`trace_reviews`** becomes a link table keyed on (review_id, trace_id), rebuilt from frontmatter.
- **`vault doctor`** gains a check that warns when tracked dossiers sit in a public GitHub repo.

**Tech Stack:** Python 3.14, click 8.4 / rich-click, PyYAML, sqlite3, pytest with `CliRunner`, git/gh (read-only).

**Spec:** `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`. P2 covers §7.1, §7.2, §8 and §10's P2 row. The shape-audit *tools* (§7.3 `review shape`, §7.4 `review reextract`) are P3. In P2 the skill does the shape audit by hand, using existing commands and the staging JSON.

**Branch:** `feat/query-review-p2` (already created from `master` @ 867c750).

---

## Ground rules for every task

- Run tests from the repo root with `uv run --group dev pytest <path> -v`. The suite is hermetic. `test/conftest.py` points `ARTMIND_HOME` and `ARTMIND_DATA_DIR` at a temp dir and blocks Neo4j.
- Any test that touches the registry DB isolates it like this:

  ```python
  @pytest.fixture(autouse=True)
  def _isolated_db(tmp_path, monkeypatch):
      import artmind.db as db
      monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
  ```

- Any test that touches dossiers points `paths.REVIEWS_DIR` and `paths.LOGS_DIR` at tmp. Every module reads them at call time:

  ```python
  @pytest.fixture(autouse=True)
  def _isolated_reviews(tmp_path, monkeypatch):
      import paths
      monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
      monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
  ```

- **Privacy:** tests and fixtures use invented data only, such as "Person A", "City B" or `journal/2025-01-01.md`.
- `test/test_no_git_writes.py` source-scans `artmind/` and `utils/` for git *write* subcommands. Use only read commands (`diff`, `ls-files`, `remote get-url`). Never put a literal git write command in a string; build fix hints in parts if you need one.
- Commit after each task. End every commit message with a blank line, then exactly:

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```

## File map

| File | Status | Responsibility |
|---|---|---|
| `artmind/db.py` | modify | `trace_reviews` → PK `(review_id, trace_id)`, with an old-schema migration |
| `artmind/traces/store.py` | modify | `index_review`, `clear_review_index` |
| `paths.py` | modify | `REVIEWS_DIR` |
| `artmind/vault.py` | modify | `VaultLayout.reviews_dir` |
| `artmind/reviews/__init__.py` | create | package docstring |
| `artmind/reviews/dossier.py` | create | constants, `Dossier`, `parse`, `render`, `new_review_id`, `dossier_path`, `load`, `iter_dossiers`, `save`, `append_log` |
| `artmind/reviews/validate.py` | create | `validate(dossier) -> {ok, errors, warnings}` |
| `artmind/reviews/scaffold.py` | create | `scaffold(trace_id, …)`, `harvest_private_terms`, `open_reviews_for` |
| `artmind/reviews/index.py` | create | `index_dossier`, `reindex` |
| `artmind/reviews/leakcheck.py` | create | `leakcheck(dossier, repo, base)` |
| `artmind/vault_doctor.py` | modify | `check_review_privacy` |
| `artmind/cli.py` | modify | `review` group: `new`, `list`, `show`, `status`, `validate`, `reindex`, `leakcheck`; `COMMAND_GROUPS` |
| `artmind/skills/artmind-review/SKILL.md` + `references/stage-walk.md`, `references/dossier-format.md` | create | the reviewer's method |
| `obsidian/artmind-obsidian/test/fixtures/vault-doctor.json` | regenerate | the doctor's output gains a check |
| `CLAUDE.md`, `justfile`, the spec | modify | docs |
| `test/test_reviews_*.py`, `test/test_vault_doctor.py` | create / modify | tests |

---

### Task 1: `trace_reviews` becomes a link table; index functions

**Files:**
- Modify: `artmind/db.py` (the `CREATE TABLE IF NOT EXISTS trace_reviews` block in `_init_db`)
- Modify: `artmind/traces/store.py` (add two functions after `insert_review_stub`)
- Test: `test/test_reviews_index_store.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_index_store.py`:

```python
"""trace_reviews as a (review_id, trace_id) link table, and its index helpers."""

import json
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _pk(table):
    import artmind.db as db

    conn = db._get_db()
    try:
        rows = [r for r in conn.execute(f"PRAGMA table_info({table})") if r[5]]
        return [r[1] for r in sorted(rows, key=lambda r: r[5])]
    finally:
        conn.close()


def test_trace_reviews_primary_key_is_review_and_trace():
    assert _pk("trace_reviews") == ["review_id", "trace_id"]


def test_old_single_key_table_is_recreated(tmp_path, monkeypatch):
    import artmind.db as db

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trace_reviews (review_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL,"
                 " stage TEXT, elements TEXT, status TEXT NOT NULL DEFAULT 'open', dossier_path TEXT,"
                 " updated_at REAL NOT NULL)")
    conn.execute("INSERT INTO trace_reviews VALUES ('rv-old', 't1', NULL, NULL, 'open', NULL, 0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    assert _pk("trace_reviews") == ["review_id", "trace_id"]


def test_index_review_replaces_rows_for_that_review():
    from artmind.traces import store

    store.index_review("rv-1", ["t1", "t2", "t1"], stage="schema", elements=["d/A-REL->"],
                       status="open", dossier_path="/x/rv-1.md")
    store.index_review("rv-1", ["t2"], stage="schema", elements=[], status="fixed", dossier_path="/x/rv-1.md")
    store.index_review("rv-2", ["t3"], stage=None, elements=[], status="open", dossier_path="/x/rv-2.md")

    import artmind.db as db

    conn = db._get_db()
    try:
        rows = conn.execute("SELECT review_id, trace_id, status, elements FROM trace_reviews"
                            " ORDER BY review_id, trace_id").fetchall()
    finally:
        conn.close()
    assert rows == [("rv-1", "t2", "fixed", "[]"), ("rv-2", "t3", "open", "[]")]


def test_clear_review_index():
    from artmind.traces import store

    store.index_review("rv-1", ["t1", "t2"], stage=None, elements=[], status="open", dossier_path=None)
    assert store.clear_review_index() == 2
    assert store.clear_review_index() == 0


def test_reviewed_turns_still_excluded_from_triage_and_prune():
    import time

    from artmind.traces import store

    store.insert_turn("t1", source="admin", question="q", started_at=time.time() - 200 * 86400)
    store.index_review("rv-1", ["t1"], stage="routing", elements=[], status="open", dossier_path=None)
    assert store.list_turns()[0]["reviewed"] == 1
    assert store.prune(90) == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_index_store.py -v`
Expected: FAIL. The PK is `["review_id"]`, and `index_review` doesn't exist.

- [ ] **Step 3: Implement the migration**

In `artmind/db.py`, replace the existing `trace_reviews` comment and `CREATE TABLE` block with:

```python
    # A local index of review dossiers (spec 2026-10-11 §8.1): one row per
    # (dossier, trace) -- a dossier can cover several traces. The dossier
    # files are the source of truth and `artmind review reindex` rebuilds
    # this table from them, so the P1 single-key table is simply recreated.
    old_pk = sorted((r for r in cursor.execute("PRAGMA table_info(trace_reviews)") if r[5]), key=lambda r: r[5])
    if [r[1] for r in old_pk] == ["review_id"]:
        cursor.execute("DROP TABLE trace_reviews")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_reviews (
            review_id     TEXT NOT NULL,
            trace_id      TEXT NOT NULL,
            stage         TEXT,
            elements      TEXT,
            status        TEXT NOT NULL DEFAULT 'open',
            dossier_path  TEXT,
            updated_at    REAL NOT NULL,
            PRIMARY KEY (review_id, trace_id)
        )
    """)
```

- [ ] **Step 4: Implement the index helpers**

In `artmind/traces/store.py`, add after `insert_review_stub`:

```python
def index_review(review_id: str, trace_ids: list[str], *, stage: str | None, elements: list[str],
                 status: str, dossier_path: str | None) -> None:
    """Replace `review_id`'s rows in the local dossier index, one per trace."""
    now = time.time()
    conn = _connect()
    try:
        conn.execute("DELETE FROM trace_reviews WHERE review_id = ?", (review_id,))
        conn.executemany(
            "INSERT INTO trace_reviews (review_id, trace_id, stage, elements, status, dossier_path, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(review_id, t, stage, json.dumps(list(elements)), status, dossier_path, now)
             for t in dict.fromkeys(trace_ids)],
        )
        conn.commit()
    finally:
        conn.close()


def clear_review_index() -> int:
    conn = _connect()
    try:
        deleted = conn.execute("DELETE FROM trace_reviews").rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_index_store.py test/test_traces_store.py test/test_traces_analysis.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/db.py artmind/traces/store.py test/test_reviews_index_store.py
git commit -m "feat(reviews): trace_reviews is a (review, trace) link index"
```

---

### Task 2: `REVIEWS_DIR` and `VaultLayout.reviews_dir`

**Files:**
- Modify: `paths.py` (after `LOGS_DIR = ARTMIND_HOME / "logs"`)
- Modify: `artmind/vault.py` (`VaultLayout`, in the committed section after `views_dir`)
- Test: `test/test_reviews_dossier.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_reviews_dossier.py`:

```python
"""Review dossier files (spec 2026-10-11 §8)."""

from pathlib import Path


def test_reviews_dir_is_under_artmind_home():
    import paths

    assert paths.REVIEWS_DIR == paths.ARTMIND_HOME / "reviews"


def test_vault_layout_reviews_dir():
    from artmind.vault import VaultLayout

    assert VaultLayout(Path("/v")).reviews_dir == Path("/v/.artmind/reviews")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_reviews_dossier.py -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

In `paths.py`, after the `LOGS_DIR` line:

```python
# Review dossiers (docs/superpowers/specs/2026-10-11-query-review-loop-design.md
# §8.1): <vault>/.artmind/reviews/ in a vault -- committed with the vault's
# private repo, never ingested (the note walk skips dot-dirs) -- else
# ~/.artmind/reviews/. They hold private corpus evidence.
REVIEWS_DIR = ARTMIND_HOME / "reviews"
```

In `artmind/vault.py`, in `VaultLayout` after the `views_dir` property:

```python
    @property
    def reviews_dir(self) -> Path:
        """Review dossiers (spec 2026-10-11 §8.1): committed with the vault,
        so they travel to every machine; one file per review, so two
        machines never edit the same file."""
        return self.artmind_dir / "reviews"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --group dev pytest test/test_reviews_dossier.py test/test_vault_scaffold.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add paths.py artmind/vault.py test/test_reviews_dossier.py
git commit -m "feat(reviews): REVIEWS_DIR and VaultLayout.reviews_dir"
```

---

### Task 3: The dossier format — `artmind/reviews/dossier.py`

**Files:**
- Create: `artmind/reviews/__init__.py`, `artmind/reviews/dossier.py`
- Test: `test/test_reviews_dossier.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `test/test_reviews_dossier.py`)

```python
from datetime import date

import pytest

from artmind.reviews import dossier as dz


@pytest.fixture(autouse=True)
def _isolated_reviews(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")


def _dossier(**meta):
    base = {"review_id": "rv-2026-01-02-abcdef", "status": "open", "stage": None, "elements": [],
            "trace_ids": ["t1"], "private_terms": [], "graph_state": {}, "created": "2026-01-02",
            "updated": "2026-01-02"}
    base.update(meta)
    return dz.Dossier(meta=base, sections={t: f"body of {t}" for t in dz.SECTIONS})


def test_new_review_id_shape():
    rid = dz.new_review_id(date(2026, 1, 2))
    assert rid.startswith("rv-2026-01-02-") and len(rid) == len("rv-2026-01-02-abcdef")
    assert dz.new_review_id() != dz.new_review_id()


def test_dossier_path_rejects_non_ids():
    import paths

    assert dz.dossier_path("rv-2026-01-02-abcdef") == paths.REVIEWS_DIR / "rv-2026-01-02-abcdef.md"
    with pytest.raises(dz.DossierError):
        dz.dossier_path("../etc/passwd")


def test_render_parse_round_trip():
    d = _dossier(stage="schema", elements=["d/Trip-WENT_TO->"])
    back = dz.parse(dz.render(d))
    assert back.meta == d.meta
    assert back.sections == d.sections


def test_render_orders_sections_and_quotes_dates():
    text = dz.render(_dossier())
    assert text.startswith("---\nreview_id: rv-2026-01-02-abcdef\n")
    assert "created: '2026-01-02'" in text
    positions = [text.index(f"\n## {t}\n") for t in dz.SECTIONS]
    assert positions == sorted(positions)


def test_parse_keeps_unknown_and_fenced_headings_as_content():
    d = _dossier()
    d.sections["Proposal"] = "```yaml\n## not a section\nkey: v\n```\n\n## Not a known section\nmore"
    back = dz.parse(dz.render(d))
    assert back.sections["Proposal"] == d.sections["Proposal"]


def test_parse_normalizes_crlf_and_unquoted_dates():
    text = dz.render(_dossier()).replace("created: '2026-01-02'", "created: 2026-01-02").replace("\n", "\r\n")
    assert dz.parse(text).meta["created"] == "2026-01-02"


def test_parse_errors():
    with pytest.raises(dz.DossierError):
        dz.parse("no frontmatter here")
    with pytest.raises(dz.DossierError):
        dz.parse("---\n- a list\n---\n")


def test_save_load_iter_and_append_log():
    d = _dossier()
    path = dz.save(d)
    assert path.exists()
    assert not any(path.parent.glob("*.tmp"))  # temp files never land in the committed folder
    loaded = dz.load("rv-2026-01-02-abcdef")
    assert loaded.sections == d.sections and loaded.path == path

    dz.append_log(loaded, "status open → fixed: patched", today=date(2026, 1, 3))
    assert loaded.sections["Log"].endswith("\n- 2026-01-03 status open → fixed: patched")

    (path.parent / "broken.md").write_text("garbage")
    found = list(dz.iter_dossiers())
    assert [(p.name, e is None) for p, _, e in found] == [("broken.md", False), (path.name, True)]


def test_load_missing_raises():
    with pytest.raises(dz.DossierError):
        dz.load("rv-2026-01-02-000000")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_dossier.py -v`
Expected: FAIL with `ModuleNotFoundError: artmind.reviews`.

- [ ] **Step 3: Implement**

Create `artmind/reviews/__init__.py`:

```python
"""Review dossiers: diagnosed query failures and the fixes they propose
(docs/superpowers/specs/2026-10-11-query-review-loop-design.md §7-§8).

`dossier` owns the file format, `validate` the completeness rules,
`scaffold` builds a new dossier from a trace, `index` keeps the local
`trace_reviews` index in step with the files, and `leakcheck` scans a code
checkout's diff for private evidence. Dossiers hold private corpus data.
"""
```

Create `artmind/reviews/dossier.py`:

```python
"""The review dossier file (spec 2026-10-11 §8.2): YAML frontmatter plus a
fixed sequence of `## ` sections. The file is the source of truth for a
review; status lives in its frontmatter.

Only the known section titles start a section, and headings inside fenced
code blocks never do, so a proposal may quote YAML or markdown freely.
"""

import os
import re
import secrets
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

STATUSES = ("open", "in-progress", "fixed", "verified", "dismissed")
STAGES = ("corpus-gap", "synthesis", "routing", "missing-view", "identity", "extraction", "schema")
SECTIONS = ("Symptom", "Diagnosis", "Shape audit", "Proposal", "Proof", "Synthetic example",
            "Regression question", "Agent prompt", "Log")

PRIVACY_PREAMBLE = (
    "> The evidence in this dossier is private corpus data. Never copy names,\n"
    "> quotes, or values from it into code, tests, fixtures, comments, commit\n"
    "> messages, or PR text in the checkout. Build tests from the \"Synthetic\n"
    "> example\" section."
)

_ID_RE = re.compile(r"^rv-\d{4}-\d{2}-\d{2}-[0-9a-f]{6}$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
_HEADING_RE = re.compile(r"^## (.+?)\s*$")
_DATE_KEYS = ("created", "updated")


class DossierError(ValueError):
    pass


@dataclass
class Dossier:
    meta: dict
    sections: dict[str, str]
    path: Path | None = None

    @property
    def review_id(self) -> str:
        return self.meta["review_id"]


def new_review_id(today: date | None = None) -> str:
    return f"rv-{(today or date.today()).isoformat()}-{secrets.token_hex(3)}"


def reviews_dir() -> Path:
    import paths

    return paths.REVIEWS_DIR


def dossier_path(review_id: str) -> Path:
    if not _ID_RE.match(review_id or ""):
        raise DossierError(f"not a review id: {review_id!r} (expected rv-YYYY-MM-DD-xxxxxx)")
    return reviews_dir() / f"{review_id}.md"


def _split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    fenced = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else _HEADING_RE.match(line)
        if match and match.group(1) in SECTIONS:
            if current is not None:
                sections[current] = "\n".join(buf).strip("\n")
            current, buf = match.group(1), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip("\n")
    return sections


def parse(text: str, path: Path | None = None) -> Dossier:
    text = text.replace("\r\n", "\n")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise DossierError("a dossier starts with a --- YAML frontmatter block")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise DossierError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict) or "review_id" not in meta:
        raise DossierError("frontmatter must be a mapping with a review_id")
    for key in _DATE_KEYS:
        if isinstance(meta.get(key), date):
            meta[key] = meta[key].isoformat()
    return Dossier(meta=meta, sections=_split_sections(text[match.end():]), path=path)


def render(dossier: Dossier) -> str:
    front = yaml.safe_dump(dossier.meta, sort_keys=False, allow_unicode=True, default_flow_style=None).strip()
    parts = [f"---\n{front}\n---\n"]
    for title in SECTIONS:
        body = (dossier.sections.get(title) or "").strip("\n")
        parts.append(f"\n## {title}\n\n{body}\n" if body else f"\n## {title}\n")
    return "".join(parts)


def load(review_id: str) -> Dossier:
    path = dossier_path(review_id)
    if not path.is_file():
        raise DossierError(f"no dossier {review_id} in {path.parent}")
    return parse(path.read_text(encoding="utf-8"), path)


def iter_dossiers() -> Iterator[tuple[Path, Dossier | None, str | None]]:
    """Every `*.md` in the reviews folder, sorted by name: (path, dossier, error)."""
    directory = reviews_dir()
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.md")):
        try:
            yield path, parse(path.read_text(encoding="utf-8"), path), None
        except (OSError, DossierError) as exc:
            yield path, None, str(exc)


def save(dossier: Dossier) -> Path:
    """Write atomically. The temp file goes in LOGS_DIR (gitignored, same
    filesystem), never in the committed reviews folder."""
    import paths

    path = dossier_path(dossier.review_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    dossier.meta["updated"] = date.today().isoformat()
    fd, tmp = tempfile.mkstemp(prefix=".review-", suffix=".tmp", dir=paths.LOGS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render(dossier))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    dossier.path = path
    return path


def append_log(dossier: Dossier, line: str, *, today: date | None = None) -> None:
    entry = f"- {(today or date.today()).isoformat()} {line}"
    log = (dossier.sections.get("Log") or "").rstrip("\n")
    dossier.sections["Log"] = f"{log}\n{entry}" if log else entry
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_dossier.py -v`
Expected: PASS.

`test_render_parse_round_trip` compares `meta` after a save-free round trip. `render` doesn't touch `updated`; only `save` does.

- [ ] **Step 5: Commit**

```bash
git add artmind/reviews/__init__.py artmind/reviews/dossier.py test/test_reviews_dossier.py
git commit -m "feat(reviews): dossier file format, parsing, atomic save"
```

---

### Task 4: Validation — `artmind/reviews/validate.py`

**Files:**
- Create: `artmind/reviews/validate.py`
- Test: `test/test_reviews_validate.py`

The rules come from spec §7.2, §8.2, §8.3 and §9:

| Rule | Severity |
|---|---|
| `status` ∈ STATUSES; `stage` ∈ STAGES; `trace_ids` a non-empty list | error |
| every element matches `domain/Class`, `domain/Class.property` or `domain/Class-REL->` | error |
| identity / extraction / schema verdicts name ≥ 1 element | error |
| all nine sections are present | error |
| always filled: Symptom, Diagnosis, Proposal, Regression question, Agent prompt, Log; also Shape audit for identity/extraction/schema; also Synthetic example unless corpus-gap | error |
| Agent prompt starts with `PRIVACY_PREAMBLE` | error |
| Regression question holds exactly one benchmark-format question with a filled evaluation comment | error |
| `private_terms` empty | warning |
| Proof empty for identity/extraction/schema/missing-view | warning |

A section counts as "filled" when it still has text after HTML comments (`<!-- … -->`, the scaffold's placeholders) are removed.

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_validate.py`:

```python
"""Per-stage completeness rules for a review dossier."""

import pytest

from artmind.reviews import dossier as dz
from artmind.reviews.validate import validate

REGRESSION = ("### R-rv-2026-01-02-abcdef — trips\n\n**Question:** How many trips did Person A take?\n\n"
              "**Evaluation comment:** Two trips; answered from the graph.")


def _complete(stage="schema", **meta):
    base = {"review_id": "rv-2026-01-02-abcdef", "status": "open", "stage": stage,
            "elements": ["d/Person-WENT_TO->"], "trace_ids": ["t1"], "private_terms": ["Person A"],
            "graph_state": {}, "created": "2026-01-02", "updated": "2026-01-02"}
    base.update(meta)
    sections = {t: f"filled {t}" for t in dz.SECTIONS}
    sections["Regression question"] = REGRESSION
    sections["Agent prompt"] = dz.PRIVACY_PREAMBLE + "\n\nDo the thing."
    return dz.Dossier(meta=base, sections=sections)


def test_complete_dossier_is_ok():
    result = validate(_complete())
    assert result == {"review_id": "rv-2026-01-02-abcdef", "ok": True, "errors": [], "warnings": []}


@pytest.mark.parametrize("meta,fragment", [
    ({"status": "done"}, "status"),
    ({"stage": None}, "stage"),
    ({"trace_ids": []}, "trace_ids"),
    ({"elements": ["not an element"]}, "element"),
    ({"elements": []}, "schema element"),
])
def test_frontmatter_errors(meta, fragment):
    result = validate(_complete(**meta))
    assert not result["ok"]
    assert any(fragment in e for e in result["errors"])


def test_placeholder_only_section_is_empty():
    d = _complete()
    d.sections["Diagnosis"] = "<!-- fill me -->"
    assert any("Diagnosis" in e for e in validate(d)["errors"])


def test_shape_audit_required_only_for_graph_shape_stages():
    d = _complete(stage="routing", elements=[])
    d.sections["Shape audit"] = ""
    assert validate(d)["ok"]
    d = _complete(stage="extraction")
    d.sections["Shape audit"] = ""
    assert any("Shape audit" in e for e in validate(d)["errors"])


def test_corpus_gap_needs_no_synthetic_example():
    d = _complete(stage="corpus-gap", elements=[])
    d.sections["Synthetic example"] = ""
    d.sections["Shape audit"] = ""
    d.sections["Proof"] = ""
    assert validate(d)["ok"]


def test_missing_section_and_missing_preamble():
    d = _complete()
    del d.sections["Proof"]
    d.sections["Agent prompt"] = "Do the thing."
    errors = validate(d)["errors"]
    assert any("Proof" in e for e in errors)
    assert any("privacy preamble" in e for e in errors)


def test_regression_question_must_be_one_block_with_comment():
    d = _complete()
    d.sections["Regression question"] = ("### R-x — t\n\n**Question:** Q?\n\n**Evaluation comment:** <!-- todo -->")
    assert any("Regression question" in e for e in validate(d)["errors"])


def test_warnings():
    d = _complete(private_terms=[])
    d.sections["Proof"] = "<!-- later -->"
    result = validate(d)
    assert result["ok"]
    assert any("private_terms" in w for w in result["warnings"])
    assert any("Proof" in w for w in result["warnings"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_validate.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/reviews/validate.py`:

```python
"""Completeness rules for a review dossier (spec 2026-10-11 §7.2, §8.2,
§8.3). `validate` never raises on a malformed dossier; it reports."""

import re

from artmind.reviews.dossier import PRIVACY_PREAMBLE, SECTIONS, STAGES, STATUSES, Dossier

GRAPH_SHAPE_STAGES = frozenset({"identity", "extraction", "schema"})
_ELEMENT_RE = re.compile(r"^[\w.\-]+/\w+(\.\w+|-\w+->)?$")
_PLACEHOLDER_RE = re.compile(r"<!--.*?-->", re.S)
_ALWAYS = ("Symptom", "Diagnosis", "Proposal", "Regression question", "Agent prompt", "Log")


def filled(text: str | None) -> bool:
    return bool(_PLACEHOLDER_RE.sub("", text or "").strip())


def validate(dossier: Dossier) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    meta, sections = dossier.meta, dossier.sections

    if meta.get("status") not in STATUSES:
        errors.append(f"status must be one of {', '.join(STATUSES)}; got {meta.get('status')!r}")
    stage = meta.get("stage")
    if stage not in STAGES:
        errors.append(f"stage must be one of {', '.join(STAGES)}; got {stage!r}")
    trace_ids = meta.get("trace_ids")
    if not isinstance(trace_ids, list) or not trace_ids:
        errors.append("trace_ids must list at least one trace")
    elements = meta.get("elements") or []
    if not isinstance(elements, list):
        errors.append("elements must be a list")
        elements = []
    for element in elements:
        if not _ELEMENT_RE.match(str(element)):
            errors.append(f"element {element!r} must look like domain/Class, domain/Class.property or domain/Class-REL->")
    if stage in GRAPH_SHAPE_STAGES and not elements:
        errors.append("an identity, extraction or schema verdict must name at least one schema element")
    if not meta.get("private_terms"):
        warnings.append("private_terms is empty: review leakcheck can then match only quoted text")

    for title in SECTIONS:
        if title not in sections:
            errors.append(f"section '{title}' is missing")
    required = list(_ALWAYS)
    if stage in GRAPH_SHAPE_STAGES:
        required.append("Shape audit")
    if stage != "corpus-gap":
        required.append("Synthetic example")
    for title in required:
        if title in sections and not filled(sections[title]):
            errors.append(f"section '{title}' is empty")
    if stage in GRAPH_SHAPE_STAGES | {"missing-view"} and not filled(sections.get("Proof")):
        warnings.append("section 'Proof' is empty: add `views test` output, or a re-extraction diff once P3 ships")

    if "Agent prompt" in sections and not sections["Agent prompt"].lstrip().startswith(PRIVACY_PREAMBLE):
        errors.append("the Agent prompt must start with the privacy preamble (artmind.reviews.dossier.PRIVACY_PREAMBLE)")

    if "Regression question" in sections:
        from artmind.benchmark import _parse_questions

        questions = _parse_questions(_PLACEHOLDER_RE.sub("", sections["Regression question"]))
        if len(questions) != 1 or not filled(questions[0].get("eval_comment")):
            errors.append("the Regression question must hold exactly one '### R-… / **Question:** / "
                          "**Evaluation comment:**' block with a filled evaluation comment")

    return {"review_id": meta.get("review_id"), "ok": not errors, "errors": errors, "warnings": warnings}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_validate.py -v`
Expected: PASS.

`_parse_questions` falls back to looser strategies. In `test_regression_question_must_be_one_block_with_comment`, the placeholder is stripped, so the evaluation comment is empty and the error fires. If the parser ever returns the block with `eval_comment=None`, `filled(None)` is False, so the error fires there too.

- [ ] **Step 5: Commit**

```bash
git add artmind/reviews/validate.py test/test_reviews_validate.py
git commit -m "feat(reviews): per-stage dossier validation"
```

---

### Task 5: Scaffold a dossier from a trace, and keep the index in step

**Files:**
- Create: `artmind/reviews/scaffold.py`, `artmind/reviews/index.py`
- Test: `test/test_reviews_scaffold.py`

`scaffold` pre-fills everything the trace knows:
- **frontmatter:** `graph_state`, `trace_ids`, and the harvested `private_terms`
- **Symptom:** question, rating and note, an answer excerpt, and the retrieval-path table
- **Regression question:** the question
- **Agent prompt:** the privacy preamble
- **Log:** the opening line

Every other section is a placeholder comment telling the reviewer what belongs there. The question and the answer are blockquoted, because `leakcheck` treats blockquotes as quoted text.

`harvest_private_terms` collects every value that names something in the user's data:
- `resolved[].name` from span evidence;
- view entity-param inputs and names;
- short string arguments of spans (≤ 60 chars, not flags, not hex ids), excluding the keys `domain`, `compact`, `render`, `mode`, `format`, `as_of`, `top_k`.

Over-inclusion is the safe direction.

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_scaffold.py`:

```python
"""Building a dossier from a recorded trace."""

import json
import time

import pytest

from artmind.reviews import dossier as dz
from artmind.reviews import index, scaffold
from artmind.reviews.validate import validate
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")


def _trace(tid="t1"):
    store.insert_turn(tid, source="admin", question="How many trips did Person A take to City B?",
                      started_at=time.time())
    store.update_turn(tid, answer="Person A went twice.\n\n### Sources\n- [x](<j/2025-01-01.md>) — d\n",
                      status="done", turns=7, cost_usd=0.2, fell_through=1, answer_basis="vector",
                      graph_attempts=2, graph_productive=0, domains='["d"]',
                      graph_state=json.dumps({"artmind_version": "9.9", "schemas": {"d": "abc"}, "vault_head": "f00"}),
                      retrieval_path=json.dumps([
                          {"span_id": "s1", "layer": "graph", "command": "query graph pattern6", "rows": 0, "productive": False},
                          {"span_id": "s2", "layer": "vector", "command": "query vector-text", "rows": 4, "productive": True}]))
    store.insert_span({"span_id": "s0", "trace_id": tid, "command": "query entity-resolve",
                       "args": json.dumps({"domain": ["d"], "reference": "Person A", "compact": True, "top_k": 5}),
                       "exit_code": 0, "duration_ms": 1, "rows": 2, "rows_total": None, "truncated": 0,
                       "evidence": json.dumps({"resolved": [{"id": "e1", "name": "Person A", "score": 0.03},
                                                            {"id": "e2", "name": "Person Ab", "score": 0.02}]}),
                       "started_at": time.time()}, [])
    store.insert_span({"span_id": "s3", "trace_id": tid, "command": "query graph entity-listing",
                       "args": json.dumps({"domain": ["d"], "name_filter": "City B",
                                           "entity_id": "448613e83d8d2476fbdd2b62d2db00fd"}),
                       "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
                       "evidence": "{}", "started_at": time.time()}, [])
    store.add_feedback(tid, "wrong", "Three trips, not two")


def test_harvest_private_terms():
    _trace()
    from artmind.traces.analysis import show

    terms = scaffold.harvest_private_terms(show("t1"))
    assert terms == ["Person A", "Person Ab", "City B"]


def test_scaffold_prefills_from_the_trace():
    _trace()
    d = scaffold.scaffold("t1", stage="schema", elements=["d/Person-WENT_TO->"])
    assert d.meta["status"] == "open"
    assert d.meta["stage"] == "schema"
    assert d.meta["trace_ids"] == ["t1"]
    assert d.meta["graph_state"]["vault_head"] == "f00"
    assert "Person A" in d.meta["private_terms"]
    symptom = d.sections["Symptom"]
    assert "> How many trips did Person A take to City B?" in symptom
    assert "**Rating:** wrong — expected: Three trips, not two" in symptom
    assert "| 1 | graph | `query graph pattern6` | 0 |" in symptom
    assert "fell through: yes" in symptom and "`vector`" in symptom
    assert d.sections["Agent prompt"].startswith(dz.PRIVACY_PREAMBLE)
    assert "**Question:** How many trips did Person A take to City B?" in d.sections["Regression question"]
    assert d.sections["Log"].endswith("opened from trace `t1`")
    result = validate(d)  # placeholders are not content
    assert not result["ok"] and any("Diagnosis" in e for e in result["errors"])


def test_scaffold_unknown_trace():
    with pytest.raises(dz.DossierError):
        scaffold.scaffold("missing")


def test_index_and_open_reviews_for():
    _trace()
    d = scaffold.scaffold("t1")
    path = dz.save(d)
    index.index_dossier(d, path)
    assert scaffold.open_reviews_for("t1") == [d.review_id]
    d.meta["status"] = "dismissed"
    dz.save(d)
    assert scaffold.open_reviews_for("t1") == []
    assert store.list_turns()[0]["reviewed"] == 1


def test_reindex_rebuilds_from_files():
    _trace()
    d = scaffold.scaffold("t1")
    dz.save(d)
    (dz.reviews_dir() / "junk.md").write_text("garbage")
    store.index_review("rv-2000-01-01-aaaaaa", ["ghost"], stage=None, elements=[], status="open", dossier_path=None)
    result = index.reindex()
    assert result["indexed"] == 1 and result["links"] == 1
    assert [i["path"].endswith("junk.md") for i in result["invalid"]] == [True]
    assert store.clear_review_index() == 1  # only the real dossier's row survived the rebuild
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_scaffold.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `index.py`**

Create `artmind/reviews/index.py`:

```python
"""Keep the local `trace_reviews` index in step with the dossier files
(spec 2026-10-11 §8.1). The files win: `reindex` rebuilds the table."""

from pathlib import Path

from artmind.reviews.dossier import Dossier, iter_dossiers


def index_dossier(dossier: Dossier, path: Path | None) -> None:
    from artmind.traces import store

    meta = dossier.meta
    store.index_review(dossier.review_id, list(meta.get("trace_ids") or []), stage=meta.get("stage"),
                       elements=list(meta.get("elements") or []), status=meta.get("status") or "open",
                       dossier_path=str(path) if path else None)


def reindex() -> dict:
    from artmind.traces import store

    store.clear_review_index()
    indexed = links = 0
    invalid = []
    for path, dossier, error in iter_dossiers():
        if error:
            invalid.append({"path": str(path), "error": error})
            continue
        index_dossier(dossier, path)
        indexed += 1
        links += len(dict.fromkeys(dossier.meta.get("trace_ids") or []))
    return {"indexed": indexed, "links": links, "invalid": invalid}
```

- [ ] **Step 4: Implement `scaffold.py`**

Create `artmind/reviews/scaffold.py`:

```python
"""A new review dossier pre-filled from a recorded trace (spec 2026-10-11
§7.2 step 1, §8.2). What the trace knows is written out; what needs
judgement is left as a placeholder comment for the reviewer."""

import re
from datetime import date, datetime

from artmind.reviews.dossier import PRIVACY_PREAMBLE, SECTIONS, Dossier, DossierError, iter_dossiers, new_review_id

ANSWER_EXCERPT = 1500
_SKIP_ARG_KEYS = frozenset({"domain", "compact", "render", "mode", "format", "as_of", "top_k"})
_HEXISH = re.compile(r"^[0-9a-f-]{16,}$", re.I)
_ACTIVE = ("open", "in-progress")

_PLACEHOLDERS = {
    "Diagnosis": "<!-- Stage verdict (corpus-gap | synthesis | routing | missing-view | identity | extraction | "
                 "schema; set `stage:` in the frontmatter to match) and the evidence chain: span id -> rows -> "
                 "chunk sentence -> staging entry. Quote source sentences as > blockquotes. "
                 "See the artmind-review skill, references/stage-walk.md. -->",
    "Shape audit": "<!-- Required for identity / extraction / schema verdicts. A table | element | check | "
                   "source sentence | why the question needs it |, then the answerability pattern as JSON. -->",
    "Proposal": "<!-- One of: view.yaml + SKILL.md draft | schema YAML diff | meta.yaml prompt diff | code-change "
                "spec (files, functions, behaviour) | for corpus-gap: what to ingest. -->",
    "Proof": "<!-- `artmind views test` output for a view; a re-extraction shape diff once P3 ships. -->",
    "Synthetic example": "<!-- The same failure with invented entities and text, for tests in the public checkout. -->",
}


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.strip().split("\n"))


def _spans(shown: dict) -> list[dict]:
    spans = [s for e in shown["events"] for s in e.get("spans", [])]
    return spans + list(shown.get("unattributed_spans") or [])


def harvest_private_terms(shown: dict) -> list[str]:
    terms: dict[str, None] = {}

    def add(value) -> None:
        if isinstance(value, str):
            value = value.strip()
            if 3 <= len(value) <= 60 and not value.startswith("-") and not _HEXISH.match(value):
                terms.setdefault(value, None)

    for span in _spans(shown):
        evidence = span.get("evidence") or {}
        for candidate in evidence.get("resolved") or []:
            add(candidate.get("name"))
        for param in ((evidence.get("view") or {}).get("params") or {}).values():
            if isinstance(param, dict):
                add(param.get("input"))
                add(param.get("name"))
            else:
                add(param)
        args = span.get("args")
        if isinstance(args, dict):
            for key, value in args.items():
                if key in _SKIP_ARG_KEYS:
                    continue
                for item in value if isinstance(value, (list, tuple)) else [value]:
                    add(item)
    return list(terms)


def _symptom(shown: dict) -> str:
    turn = shown["turn"]
    feedback = shown.get("feedback") or {}
    state = turn.get("graph_state") or {}
    started = datetime.fromtimestamp(turn["started_at"]).isoformat(timespec="minutes")
    cost = f" · ${turn['cost_usd']:.2f}" if turn.get("cost_usd") is not None else ""
    rating = feedback.get("rating") or "unrated"
    if feedback.get("expected"):
        rating += f" — expected: {feedback['expected']}"
    lines = [
        f"**Trace:** `{turn['trace_id']}` · {turn['source']} · {started} · {turn.get('turns') or '?'} turns{cost}",
        f"**Rating:** {rating}",
        f"**Graph state:** artmind {state.get('artmind_version')}, vault HEAD `{(state.get('vault_head') or '')[:12]}`",
        "",
        "**Question**",
        "",
        _quote(turn["question"]),
        "",
        "**Answer (excerpt)**",
        "",
        _quote((turn.get("answer") or "(no answer)")[:ANSWER_EXCERPT]),
        "",
        f"**Retrieval path** — fell through: {'yes' if turn.get('fell_through') else 'no'}, "
        f"answer basis: `{turn.get('answer_basis')}`, graph attempts {turn.get('graph_attempts') or 0} "
        f"({turn.get('graph_productive') or 0} returned rows)",
        "",
        "| # | layer | command | rows |",
        "|---|---|---|---|",
    ]
    for i, step in enumerate(turn.get("retrieval_path") or [], 1):
        lines.append(f"| {i} | {step['layer']} | `{step['command']}` | {step['rows']} |")
    return "\n".join(lines)


def scaffold(trace_id: str, *, stage: str | None = None, elements: list[str] | tuple = (),
             today: date | None = None) -> Dossier:
    from artmind.traces.analysis import show

    shown = show(trace_id)
    if shown is None:
        raise DossierError(f"no trace {trace_id}")
    turn = shown["turn"]
    review_id = new_review_id(today)
    day = (today or date.today()).isoformat()
    meta = {
        "review_id": review_id,
        "status": "open",
        "stage": stage,
        "elements": list(elements),
        "trace_ids": [trace_id],
        "private_terms": harvest_private_terms(shown),
        "graph_state": turn.get("graph_state") or {},
        "created": day,
        "updated": day,
    }
    sections = {title: _PLACEHOLDERS.get(title, "") for title in SECTIONS}
    sections["Symptom"] = _symptom(shown)
    sections["Regression question"] = (
        f"### R-{review_id} — <!-- short title -->\n\n**Question:** {turn['question'].strip()}\n\n"
        "**Evaluation comment:** <!-- what a correct answer must say; for a graph miss also: answered from "
        "the graph (answer_basis graph or mixed, no fall-through) -->"
    )
    sections["Agent prompt"] = (
        f"{PRIVACY_PREAMBLE}\n\n<!-- A self-contained task for a coding session: context, files, acceptance "
        f"criteria (tests built on the Synthetic example; `artmind review leakcheck {review_id} --base master` "
        "passes; the regression question passes, after a re-ingest if the schema changed). -->"
    )
    sections["Log"] = f"- {day} opened from trace `{trace_id}`"
    return Dossier(meta=meta, sections=sections)


def open_reviews_for(trace_id: str) -> list[str]:
    """Review ids of open or in-progress dossiers that cover `trace_id` (from the files)."""
    return [d.review_id for _, d, err in iter_dossiers()
            if err is None and d.meta.get("status") in _ACTIVE and trace_id in (d.meta.get("trace_ids") or [])]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_scaffold.py -v`
Expected: PASS.

In `test_harvest_private_terms`, the expected order is resolved names first (spans in time order), then argument values. `reference: "Person A"` is already present, so it's deduplicated; `name_filter: "City B"` is added; and the hex `entity_id` is skipped. If span ordering from `show()` differs (attributed spans first, then unattributed), adjust only the expected **order** in the test, never the set, and say so in your report.

- [ ] **Step 6: Commit**

```bash
git add artmind/reviews/scaffold.py artmind/reviews/index.py test/test_reviews_scaffold.py
git commit -m "feat(reviews): scaffold a dossier from a trace; keep the index in step"
```

---

### Task 6: `leakcheck` — scan a checkout's diff for private evidence

**Files:**
- Create: `artmind/reviews/leakcheck.py`
- Test: `test/test_reviews_leakcheck.py`

What counts as private:
- **Terms:** every `private_terms` entry of 3 characters or more, matched case-insensitively and as a whole word.
- **Quoted text:** blockquote lines (`>`) in Symptom, Diagnosis and Shape audit, plus table rows in Shape audit (its source-sentence column).

An added line is a hit when it contains a term, or when it shares a 5-word sequence (case-folded, word tokens only) with the quoted text.

Added lines come from `git diff --no-color --unified=0 <base>` (working tree vs. base, staged and unstaged) plus untracked, non-ignored files, whose every line counts as added. Paths under `.artmind/reviews/` are skipped.

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_leakcheck.py`:

```python
"""leakcheck: added checkout lines that carry a dossier's private evidence."""

import subprocess

import pytest

from artmind.reviews import dossier as dz
from artmind.reviews.leakcheck import LeakcheckError, leakcheck


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "checkout"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "app.py").write_text("print('hello')\n")
    _git(r, "add", "app.py")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _dossier():
    sections = {t: "" for t in dz.SECTIONS}
    sections["Symptom"] = "**Question**\n\n> How often did Person Q travel to Kestrel Bay last spring?\n"
    sections["Shape audit"] = ("| element | check | source sentence | why |\n|---|---|---|---|\n"
                               "| d/Trip | coverage | took the slow ferry across the northern sound | count |")
    sections["Diagnosis"] = "The model looked at the wrong pattern for this question entirely."  # prose, not quoted
    return dz.Dossier(meta={"review_id": "rv-2026-01-02-abcdef", "private_terms": ["Person Q", "Kestrel Bay", "ab"]},
                      sections=sections)


def test_clean_diff_passes(repo):
    (repo / "app.py").write_text("print('hello')\nprint('Person X travels')\n")
    result = leakcheck(_dossier(), repo)
    assert result["ok"] is True and result["hits"] == []


def test_term_in_modified_file_is_a_hit(repo):
    (repo / "app.py").write_text("print('hello')\nCITY = 'kestrel bay'\n")
    result = leakcheck(_dossier(), repo)
    assert not result["ok"]
    assert result["hits"] == [{"file": "app.py", "line": 2, "kind": "term", "match": "Kestrel Bay"}]


def test_quoted_five_gram_in_untracked_file_is_a_hit(repo):
    (repo / "test_new.py").write_text("# fixture: Took the slow ferry across the northern sound\n")
    result = leakcheck(_dossier(), repo)
    assert [(h["file"], h["kind"]) for h in result["hits"]] == [("test_new.py", "quote")]


def test_unquoted_prose_and_short_terms_are_ignored(repo):
    (repo / "app.py").write_text("print('hello')\n# looked at the wrong pattern for this question\nab = 1\n")
    assert leakcheck(_dossier(), repo)["ok"] is True


def test_staged_changes_count_and_review_folder_is_skipped(repo):
    (repo / "staged.py").write_text("WHO = 'Person Q'\n")
    _git(repo, "add", "staged.py")
    (repo / ".artmind" / "reviews").mkdir(parents=True)
    (repo / ".artmind" / "reviews" / "rv.md").write_text("Person Q\n")
    hits = leakcheck(_dossier(), repo)["hits"]
    assert [h["file"] for h in hits] == ["staged.py"]


def test_committed_changes_against_a_base(repo):
    (repo / "app.py").write_text("print('hello')\nWHERE = 'Kestrel Bay'\n")
    _git(repo, "commit", "-q", "-am", "leak")
    assert leakcheck(_dossier(), repo)["ok"] is True          # nothing uncommitted vs HEAD
    assert not leakcheck(_dossier(), repo, base="HEAD~1")["ok"]


def test_bad_base_raises(repo):
    with pytest.raises(LeakcheckError):
        leakcheck(_dossier(), repo, base="no-such-ref")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_leakcheck.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/reviews/leakcheck.py`:

```python
"""A backstop against copying a dossier's private evidence into the public
code checkout (spec 2026-10-11 §8.3). Read-only git: `diff` and `ls-files`.

Private = the dossier's `private_terms` (whole word, case-insensitive) and
its quoted text: blockquote lines in Symptom / Diagnosis / Shape audit and
table rows in Shape audit. An added line is a hit when it contains a term
or shares a 5-word sequence with the quoted text. Reviewer prose outside
quotes is not private, so a coding agent may paraphrase the diagnosis.
"""

import re
from pathlib import Path

from utils.functions import run_command

from artmind.reviews.dossier import Dossier

NGRAM = 5
MIN_TERM = 3
MAX_HITS = 200
MAX_FILE_BYTES = 1_000_000
QUOTED_SECTIONS = ("Symptom", "Diagnosis", "Shape audit")
_WORD = re.compile(r"\w+")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_SKIP_PREFIX = ".artmind/reviews/"


class LeakcheckError(RuntimeError):
    pass


def _tokens(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text)]


def _ngrams(tokens: list[str]) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + NGRAM]) for i in range(len(tokens) - NGRAM + 1)}


def quoted_text(dossier: Dossier) -> str:
    lines = []
    for title in QUOTED_SECTIONS:
        for line in (dossier.sections.get(title) or "").split("\n"):
            stripped = line.strip()
            if stripped.startswith(">"):
                lines.append(stripped.lstrip("> "))
            elif title == "Shape audit" and stripped.startswith("|") and not set(stripped) <= set("|-: "):
                lines.append(stripped)
    return "\n".join(lines)


def _git(repo: Path, args: list[str]) -> str:
    rc, out, err = run_command(["git", *args], cwd=repo)
    if rc != 0:
        raise LeakcheckError(f"git {' '.join(args)} failed: {err.strip() or out.strip()}")
    return out


def added_lines(repo: Path, base: str = "HEAD"):
    """(path, line number, text) for every line added vs `base`, plus every
    line of untracked, non-ignored files."""
    current, number = None, 0
    for line in _git(repo, ["diff", "--no-color", "--unified=0", base]).split("\n"):
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif (hunk := _HUNK.match(line)):
            number = int(hunk.group(1))
        elif line.startswith("+") and current is not None:
            yield current, number, line[1:]
            number += 1
    for rel in filter(None, _git(repo, ["ls-files", "--others", "--exclude-standard", "-z"]).split("\0")):
        path = Path(repo) / rel
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, content in enumerate(text.split("\n"), 1):
            yield rel, i, content


def leakcheck(dossier: Dossier, repo: Path | str, base: str = "HEAD") -> dict:
    repo = Path(repo)
    terms = [t for t in dossier.meta.get("private_terms") or [] if isinstance(t, str) and len(t.strip()) >= MIN_TERM]
    patterns = [(t, re.compile(rf"(?<!\w){re.escape(t.strip())}(?!\w)", re.I)) for t in terms]
    grams = _ngrams(_tokens(quoted_text(dossier)))
    hits: list[dict] = []
    for rel, number, text in added_lines(repo, base):
        if rel.startswith(_SKIP_PREFIX):
            continue
        for term, pattern in patterns:
            if pattern.search(text):
                hits.append({"file": rel, "line": number, "kind": "term", "match": term})
        for gram in _ngrams(_tokens(text)):
            if gram in grams:
                hits.append({"file": rel, "line": number, "kind": "quote", "match": " ".join(gram)})
                break
    return {"review_id": dossier.meta.get("review_id"), "repo": str(repo), "base": base,
            "ok": not hits, "hit_count": len(hits), "hits": hits[:MAX_HITS]}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_leakcheck.py test/test_no_git_writes.py -v`
Expected: PASS.

`test_no_git_writes` must still pass: `diff` and `ls-files` are reads. If that test flags the `["git", *args]` splat in `_git`, add `artmind/reviews/leakcheck.py::_git` to its `SPLAT_WRAPPERS` allow-list, the same way `vault_doctor._git` is listed. Read that test first to see the exact format.

- [ ] **Step 5: Commit**

```bash
git add artmind/reviews/leakcheck.py test/test_reviews_leakcheck.py
git commit -m "feat(reviews): leakcheck a checkout diff against a dossier's private evidence"
```

(Also add `test/test_no_git_writes.py` if you had to allow-list the wrapper.)

---

### Task 7: `artmind review` CLI group

**Files:**
- Modify: `artmind/cli.py`:
  - imports: add `from artmind.reviews.dossier import STAGES, STATUSES` next to the `TracedGroup` import;
  - `COMMAND_GROUPS`: the Query entry becomes `["query", "views", "traces", "review"]`;
  - the new group goes after the last `traces` command.
- Test: `test/test_reviews_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_cli.py`:

```python
"""`artmind review new/list/show/status/validate/reindex/leakcheck`."""

import json
import subprocess
import time

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.reviews import dossier as dz
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    store.insert_turn("t1", source="admin", question="How many trips did Person A take?", started_at=time.time())
    store.update_turn("t1", answer="Two.", status="done", retrieval_path="[]", domains='["d"]')


def _run(*args, ok=True):
    result = CliRunner().invoke(cli, ["review", *args])
    if ok:
        assert result.exit_code == 0, result.output
    return result


def _json(*args):
    return json.loads(_run(*args, "--compact").stdout)


def test_new_list_show():
    created = _json("new", "t1", "--stage", "routing")
    rid = created["review_id"]
    assert created["path"].endswith(f"{rid}.md")
    listed = _json("list")["reviews"]
    assert [(r["review_id"], r["status"], r["stage"], r["trace_ids"]) for r in listed] == [(rid, "open", "routing", ["t1"])]
    shown = _json("show", rid)
    assert shown["meta"]["review_id"] == rid and "Symptom" in shown["sections"]
    raw = _run("show", rid, "--markdown").stdout
    assert raw.startswith("---\nreview_id: ")
    assert store.list_turns()[0]["reviewed"] == 1


def test_new_refuses_a_second_open_review_unless_forced():
    _json("new", "t1")
    result = _run("new", "t1", ok=False)
    assert result.exit_code != 0 and "already has open review" in result.output
    _json("new", "t1", "--force")
    assert len(_json("list")["reviews"]) == 2


def test_new_unknown_trace_is_an_error():
    result = _run("new", "nope", ok=False)
    assert result.exit_code != 0 and "no trace" in result.output


def test_list_filters():
    a = _json("new", "t1", "--stage", "schema", "--element", "d/Person-WENT_TO->")["review_id"]
    _json("new", "t1", "--force", "--stage", "routing")
    assert [r["review_id"] for r in _json("list", "--stage", "schema")["reviews"]] == [a]
    assert [r["review_id"] for r in _json("list", "--element", "WENT_TO")["reviews"]] == [a]


def test_status_updates_frontmatter_log_and_index():
    rid = _json("new", "t1")["review_id"]
    _run("status", rid, "fixed", "--note", "patched the skill")
    d = dz.load(rid)
    assert d.meta["status"] == "fixed"
    assert d.sections["Log"].splitlines()[-1].endswith("status open → fixed: patched the skill")
    assert _json("list", "--status", "fixed")["reviews"][0]["review_id"] == rid


def test_validate_exit_codes():
    rid = _json("new", "t1")["review_id"]
    result = _run("validate", rid, "--compact", ok=False)
    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False


def test_reindex():
    _json("new", "t1")
    assert store.clear_review_index() == 1
    assert _json("reindex")["indexed"] == 1


def test_leakcheck_exit_codes(tmp_path):
    rid = _json("new", "t1")["review_id"]
    repo = tmp_path / "co"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True)
    assert _json("leakcheck", rid, "--repo", str(repo))["ok"] is True
    (repo / "a.py").write_text("x = 1\nq = 'How many trips did Person A take'\n")
    result = _run("leakcheck", rid, "--repo", str(repo), "--compact", ok=False)
    assert result.exit_code == 1 and json.loads(result.stdout)["hit_count"] >= 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_cli.py -v`
Expected: FAIL with `No such command 'review'`.

- [ ] **Step 3: Implement**

In `artmind/cli.py`:
- Add the import `from artmind.reviews.dossier import STAGES, STATUSES` beside the `from artmind.traces.span import …` line.
- Change the Query `COMMAND_GROUPS` entry to `["query", "views", "traces", "review"]`.
- After the last `traces_group` command, add:

```python
# ── artmind review ────────────────────────────────────────────────────────────


@cli.group("review")
def review_group() -> None:
    """Review dossiers: diagnosed query failures and the fixes they propose.

    One markdown file per review under <vault>/.artmind/reviews/ -- committed
    with the vault's private repo (~/.artmind/reviews/ outside a vault). A
    dossier holds private corpus evidence: never paste it into the public code
    checkout; `leakcheck` scans a checkout diff for leaks. Driven by the
    artmind-review skill, run from Claude Code in the checkout with
    ARTMIND_VAULT=<vault>. Status lives in each file; `reindex` rebuilds the
    local index from them.
    """
    pass


def _load_dossier(review_id):
    from artmind.reviews import dossier as dz

    try:
        return dz.load(review_id)
    except dz.DossierError as exc:
        raise click.ClickException(str(exc)) from exc


@review_group.command("new")
@click.argument("trace_id")
@click.option("--stage", type=click.Choice(STAGES), default=None, help="Verdict, if already known")
@click.option("--element", "elements", multiple=True,
              help="Schema element domain/Class[.prop|-REL->] (repeatable; comma-splittable)")
@click.option("--force", is_flag=True, help="Open another dossier even if the trace already has an open one")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_new(trace_id, stage, elements, force, compact) -> None:
    """Scaffold a dossier from a recorded trace (symptom, retrieval path, private terms)."""
    from artmind.reviews import dossier as dz
    from artmind.reviews import index, scaffold

    if not force:
        existing = scaffold.open_reviews_for(trace_id)
        if existing:
            raise click.ClickException(
                f"trace {trace_id} already has open review(s): {', '.join(existing)} (use --force)")
    try:
        d = scaffold.scaffold(trace_id, stage=stage, elements=_parse_repeatable(elements))
    except dz.DossierError as exc:
        raise click.ClickException(str(exc)) from exc
    path = dz.save(d)
    index.index_dossier(d, path)
    _echo_json({"review_id": d.review_id, "path": str(path)}, compact)


@review_group.command("list")
@click.option("--status", type=click.Choice(STATUSES), default=None)
@click.option("--stage", type=click.Choice(STAGES), default=None)
@click.option("--element", default=None, help="Substring of a schema element")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_list(status, stage, element, compact) -> None:
    """Dossiers in this vault's reviews folder, read from the files."""
    from artmind.reviews import dossier as dz

    reviews, invalid = [], []
    for path, d, error in dz.iter_dossiers():
        if error:
            invalid.append({"path": str(path), "error": error})
            continue
        meta = d.meta
        if status and meta.get("status") != status:
            continue
        if stage and meta.get("stage") != stage:
            continue
        if element and not any(element in str(e) for e in meta.get("elements") or []):
            continue
        reviews.append({k: meta.get(k) for k in ("review_id", "status", "stage", "elements", "trace_ids",
                                                 "created", "updated")} | {"path": str(path)})
    _echo_json({"command": "review_list", "reviews": reviews, "invalid": invalid}, compact)


@review_group.command("show")
@click.argument("review_id")
@click.option("--markdown", is_flag=True, help="Print the dossier file as is")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_show(review_id, markdown, compact) -> None:
    """One dossier: frontmatter and sections as JSON, or the raw file."""
    d = _load_dossier(review_id)
    if markdown:
        click.echo(d.path.read_text(encoding="utf-8"), nl=False)
        return
    _echo_json({"meta": d.meta, "sections": d.sections, "path": str(d.path)}, compact)


@review_group.command("status")
@click.argument("review_id")
@click.argument("status", type=click.Choice(STATUSES))
@click.option("--note", default=None, help="Why, for the dossier's Log")
def review_status(review_id, status, note) -> None:
    """Move a dossier to a new status; the change is logged in the dossier."""
    from artmind.reviews import dossier as dz
    from artmind.reviews import index

    d = _load_dossier(review_id)
    old = d.meta.get("status")
    d.meta["status"] = status
    dz.append_log(d, f"status {old} → {status}" + (f": {note}" if note else ""))
    path = dz.save(d)
    index.index_dossier(d, path)
    click.echo(f"{review_id}: {old} → {status}")


@review_group.command("validate")
@click.argument("review_id")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_validate(review_id, compact) -> None:
    """Check a dossier is complete for its stage; exits 1 on errors."""
    from artmind.reviews.validate import validate

    result = validate(_load_dossier(review_id))
    _echo_json(result, compact)
    if not result["ok"]:
        raise SystemExit(1)


@review_group.command("reindex")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_reindex(compact) -> None:
    """Rebuild the local review index from the dossier files."""
    from artmind.reviews.index import reindex

    _echo_json(reindex(), compact)


@review_group.command("leakcheck")
@click.argument("review_id")
@click.option("--base", default="HEAD", show_default=True, help="Compare the working tree with this ref, e.g. master")
@click.option("--repo", default=".", show_default=True, type=click.Path(file_okay=False, exists=True),
              help="The code checkout to scan")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_leakcheck(review_id, base, repo, compact) -> None:
    """Scan a checkout's added lines for the dossier's private terms and quoted text; exits 1 on a hit."""
    from artmind.reviews.leakcheck import LeakcheckError, leakcheck

    try:
        result = leakcheck(_load_dossier(review_id), repo, base)
    except LeakcheckError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
    if not result["ok"]:
        raise SystemExit(1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_cli.py test/test_cli_guide.py -v`, then `uv run artmind review --help`.
Expected: PASS, with seven commands listed.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_reviews_cli.py
git commit -m "feat(reviews): artmind review new/list/show/status/validate/reindex/leakcheck"
```

---

### Task 8: `vault doctor` warns when tracked dossiers sit in a public GitHub repo

**Files:**
- Modify: `artmind/vault_doctor.py` (a new check, registered in `run()` after `check_tracked_ignored`)
- Regenerate: `obsidian/artmind-obsidian/test/fixtures/vault-doctor.json`
- Test: `test/test_vault_doctor.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `test/test_vault_doctor.py`)

Read the file's existing `repo` fixture and its `_git` / `_by_name` helpers first, and reuse them. Then append:

```python
NAME = "review dossiers privacy"


def _track_dossier(repo):
    reviews = repo / ".artmind" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / "rv-2026-01-02-abcdef.md").write_text("---\nreview_id: rv-2026-01-02-abcdef\n---\n")
    _git(repo, "add", ".artmind/reviews")


def test_review_privacy_no_dossiers(repo):
    assert _by_name(doc.run(repo))[NAME]["status"] == "ok"


def test_review_privacy_no_origin(repo):
    _track_dossier(repo)
    check = _by_name(doc.run(repo))[NAME]
    assert check["status"] == "ok" and "no origin" in check["detail"]


def test_review_privacy_not_github(repo):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "https://gitlab.example.com/me/vault.git")
    check = _by_name(doc.run(repo))[NAME]
    assert check["status"] == "ok" and "not on GitHub" in check["detail"]


def test_review_privacy_gh_missing(repo, monkeypatch):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:me/vault.git")
    monkeypatch.setattr(doc.shutil, "which", lambda name: None)
    assert _by_name(doc.run(repo))[NAME]["status"] == "unknown"


@pytest.mark.parametrize("visibility,status", [("PUBLIC", "warn"), ("PRIVATE", "ok"), ("INTERNAL", "ok")])
def test_review_privacy_visibility(repo, monkeypatch, visibility, status):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "https://github.com/me/vault.git")
    monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/gh")
    seen = {}

    def fake(slug):
        seen["slug"] = slug
        return 0, visibility + "\n", ""

    monkeypatch.setattr(doc, "_gh_visibility", fake)
    check = _by_name(doc.run(repo))[NAME]
    assert seen["slug"] == "me/vault"
    assert check["status"] == status
    if status == "warn":
        assert check["fix"]


def test_review_privacy_gh_error_is_unknown(repo, monkeypatch):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "https://github.com/me/vault")
    monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(doc, "_gh_visibility", lambda slug: (1, "", "not logged in"))
    assert _by_name(doc.run(repo))[NAME]["status"] == "unknown"
```

(Use the module alias the file already uses for `artmind.vault_doctor`, assumed here to be `doc`. Add `import pytest` if it isn't imported.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_doctor.py -v`
Expected: the new tests fail with `KeyError: 'review dossiers privacy'`.

- [ ] **Step 3: Implement**

In `artmind/vault_doctor.py`:
- Add `import re` and `import shutil` if absent.
- Add the following after `check_tracked_ignored`:

```python
_GITHUB_REMOTE = re.compile(r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([^/]+)/(.+?)(?:\.git)?/?$")


def _gh_visibility(slug: str) -> tuple[int, str, str]:
    return run_command(["gh", "repo", "view", slug, "--json", "visibility", "--jq", ".visibility"], timeout=15)


def check_review_privacy(vault_dir: Path) -> Check:
    """Review dossiers (.artmind/reviews/, spec 2026-10-11 §8.1) hold private
    corpus evidence and are committed with the vault. Warn when that repo is a
    public GitHub repo. Read-only: `git ls-files`, `git remote get-url`, `gh repo view`."""
    name = "review dossiers privacy"
    rc, tracked, _ = _git(vault_dir, ["ls-files", "--", ".artmind/reviews"])
    if rc != 0 or not tracked.strip():
        return Check(name, OK, "no review dossiers are tracked")
    rc, url, _ = _git(vault_dir, ["remote", "get-url", "origin"], expected_codes=(2,))
    url = url.strip()
    if rc != 0 or not url:
        return Check(name, OK, "review dossiers are tracked; the vault has no origin remote")
    match = _GITHUB_REMOTE.match(url)
    if not match:
        return Check(name, OK, "origin is not on GitHub; its visibility is not checked")
    slug = f"{match.group(1)}/{match.group(2)}"
    if shutil.which("gh") is None:
        return Check(name, UNKNOWN, f"review dossiers are tracked in {slug} on GitHub, but `gh` is not installed, "
                     "so the repo's visibility is unknown",
                     fix="install the GitHub CLI and run `gh auth login`, or confirm the vault repo is private")
    rc, out, err = _gh_visibility(slug)
    visibility = out.strip().upper()
    if rc != 0 or not visibility:
        return Check(name, UNKNOWN, f"`gh repo view {slug}` failed: {(err or out).strip()[:200]}")
    if visibility == "PUBLIC":
        return Check(name, WARN, f"review dossiers in .artmind/reviews/ hold private corpus evidence and are "
                     f"tracked in a PUBLIC GitHub repo ({slug})",
                     fix=f"make {slug} private (`gh repo edit {slug} --visibility private`), "
                         "or stop tracking .artmind/reviews/")
    return Check(name, OK, f"tracked review dossiers live in a {visibility.lower()} repo ({slug})")
```

Check `_git`'s real signature (the map shows `_git(vault_dir, args, expected_codes=())`). `git remote get-url` exits 2 when the remote is absent.

Then register `check_review_privacy(vault_dir)` in `run()` immediately after `check_tracked_ignored(vault_dir)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_doctor.py test/test_no_git_writes.py -v`
Expected: PASS.

- [ ] **Step 5: Regenerate the plugin fixture**

The doctor's JSON gained a check, so the Obsidian plugin's fixture must be regenerated (see CLAUDE.md, the `obsidian/artmind-obsidian/` row):

```bash
ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -v
uv run --group dev pytest test/test_plugin_fixtures.py -v
just obsidian-plugin-test
```

Expected: the regenerated `vault-doctor.json` includes a `"review dossiers privacy"` check, and both runs pass. If `just obsidian-plugin-test` can't run (no node/npm), say so in your report; don't skip silently. If the plugin test fails because the plugin hard-codes the check list, report it, and don't change plugin code without asking.

- [ ] **Step 6: Commit**

```bash
git add artmind/vault_doctor.py test/test_vault_doctor.py obsidian/artmind-obsidian/test/fixtures/vault-doctor.json
git commit -m "feat(reviews): vault doctor warns when tracked dossiers sit in a public GitHub repo"
```

---

### Task 9: The `artmind-review` skill

**Files:**
- Create: `artmind/skills/artmind-review/SKILL.md`
- Create: `artmind/skills/artmind-review/references/stage-walk.md`
- Create: `artmind/skills/artmind-review/references/dossier-format.md`
- Test: `test/test_reviews_skill.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_skill.py`:

```python
"""The artmind-review skill ships, is well-formed, and is in no web-UI profile."""

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parents[1] / "artmind" / "skills" / "artmind-review"


def test_skill_frontmatter():
    text = (SKILL / "SKILL.md").read_text()
    assert text.startswith("---\n")
    meta = yaml.safe_load(text.split("---\n")[1])
    assert meta["name"] == "artmind-review"
    assert "dossier" in meta["description"]


def test_references_exist_and_are_linked():
    text = (SKILL / "SKILL.md").read_text()
    for ref in ("references/stage-walk.md", "references/dossier-format.md"):
        assert (SKILL / ref).is_file()
        assert ref in text


def test_skill_names_every_stage_and_the_privacy_rule():
    from artmind.reviews.dossier import STAGES

    walk = (SKILL / "references" / "stage-walk.md").read_text()
    for stage in STAGES:
        assert f"`{stage}`" in walk
    assert "leakcheck" in (SKILL / "SKILL.md").read_text()


def test_skill_is_in_no_webui_profile():
    from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, QA_PROFILE

    for profile in (QA_PROFILE, ADMIN_PROFILE, BENCHMARK_PROFILE):
        assert "artmind-review" not in profile.skills
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_skill.py -v`
Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write `SKILL.md`**

Create `artmind/skills/artmind-review/SKILL.md` with exactly this content:

````markdown
---
name: artmind-review
description: Review recorded artmind query traces (admin console and benchmark) and turn a failure into a diagnosed review dossier — the failing pipeline stage with cited evidence, a graph-shape audit, a proposal (view, schema change, prompt change or code change), a synthetic example, a regression question and a privacy-guarded agent prompt. Use for "review the last queries", "why did this answer fail", "triage traces", "open a review for trace X", or "what should we fix in extraction or the schema". Run from Claude Code in the artmind checkout with ARTMIND_VAULT set; never from the chat or admin UI.
---

# artmind Review

You turn one recorded query turn into one **review dossier**: a markdown file in
the vault (`<vault>/.artmind/reviews/<review_id>.md`) that says **which stage of
the pipeline failed**, **proves it with evidence**, and **proposes the fix**. A
human applies fixes — you never do.

Read `references/stage-walk.md` before your first verdict and
`references/dossier-format.md` before filling a dossier.

## Setup

Run from the artmind code checkout, pointing artmind at the vault:

```bash
export ARTMIND_VAULT=<path to the vault>
artmind traces list --limit 5        # confirms the trace DB is the vault's
```

`artmind traces …` and `artmind review …` read this machine's trace DB and the
vault's reviews folder. Query commands (`artmind query …`) hit the live graph.

## Ground rules

1. **Privacy.** A dossier holds private corpus data (names, quotes, values). The
   checkout you are running in is a **public** repo. Never write dossier
   content, names, quotes or values into any file in the checkout, a commit
   message, an issue or a PR. Never publish a dossier anywhere — it lives only
   in the vault. Quote source text in the dossier as `>` blockquotes, and add
   every private name or value you quote to the frontmatter `private_terms`.
2. **Evidence or no verdict.** Every verdict cites at least one span id, result
   row, chunk sentence or staging entry. "Probably" is not a verdict.
3. **Cheapest stage first.** Walk the stages in the fixed order and stop at the
   first that failed. Blame the schema last: a schema change forces a re-ingest.
4. **Shape matters.** For identity, extraction and schema verdicts, compare the
   source text, the extracted subgraph and the question (the shape audit).
5. **One problem, one dossier.** Before opening a dossier, check
   `artmind review list --element <element>` for an open dossier on the same
   schema element; if one exists, add this trace to it (append the symptom,
   add the trace id to `trace_ids`) instead of opening another.
6. **Propose, never apply.** No graph writes, no schema edits, no code edits,
   no `artmind update`. The dossier's Agent prompt is for a later coding session.

## Flow

1. **Pick.** `artmind traces triage --since 14d` lists turns worth reviewing
   (rated wrong first, then partial, then unrated turns by signal score; graph
   misses rated good last). Take the top one unless the user named a trace.
2. **Load.** `artmind traces show <trace_id>` — the question, answer, rating
   and `expected` note, every agent tool call with the CLI spans it ran
   (command, args, rows, evidence ids), the retrieval path, `fell_through` and
   `answer_basis`. A warning about missing spans means a stale `serve`
   daemon recorded nothing; say so and stop.
3. **Scaffold.** `artmind review new <trace_id>` writes the dossier with the
   Symptom, Regression question, Agent prompt preamble and Log pre-filled and
   prints its path. Edit that file for the remaining steps.
4. **Ground truth.** Find the chunks a correct answer rests on — the
   procedure is in `references/stage-walk.md` ("Ground truth"). None at all →
   verdict `corpus-gap`.
5. **Walk the stages** (`references/stage-walk.md`): synthesis → routing →
   missing-view → identity → extraction → schema. A **graph miss**
   (`fell_through` with `answer_basis: vector`) has its own entry point.
6. **Shape audit** for identity / extraction / schema verdicts — the checklist
   and the staging lookup are in `references/stage-walk.md`.
7. **Fill the dossier** (`references/dossier-format.md`): set `stage` and
   `elements` in the frontmatter, then Diagnosis, Shape audit, Proposal, Proof
   (for a view: run `artmind views test`), Synthetic example, the Regression
   question's title and evaluation comment, and the Agent prompt task.
   Add every private name or value you quoted to `private_terms`.
8. **Validate.** `artmind review validate <review_id>` must report `ok: true`.
   Fix every error; read the warnings.
9. **Report** to the user: the review id, the verdict in one sentence, the
   proposal in one sentence, and the dossier path. Do not paste dossier
   content beyond that.

## Commands

| Command | Use |
|---|---|
| `artmind traces triage [--since 7d]` | turns worth reviewing |
| `artmind traces show <trace_id>` | one turn's full evidence chain |
| `artmind traces stats --by week` | fall-through and graph-miss trends per domain |
| `artmind review new <trace_id> [--stage …] [--element …]` | scaffold a dossier |
| `artmind review list [--status] [--stage] [--element]` | dossiers in the vault |
| `artmind review show <review_id> [--markdown]` | one dossier |
| `artmind review validate <review_id>` | completeness for its stage |
| `artmind review status <review_id> <status> --note "…"` | open → in-progress → fixed → verified, or dismissed |
| `artmind review leakcheck <review_id> --base master` | run in a coding session before committing |
| `artmind query chunks --domain D --idList <id> --expand 1` | source text of a chunk and its neighbours |
| `artmind query entity-context --domain D --entityId <id>` | an entity's properties, relationships and source chunks |
| `artmind query vector-text --domain D "<text>"` | find the chunks that state a fact |
| `artmind query views list --domain D` / `artmind views test …` | existing views / prove a drafted view |

`review shape` and `review reextract` (automatic shape bundles and dry-run
re-extraction) arrive in a later phase; until then do the shape audit by hand.

## Failure modes

- **No spans in the trace** — the `serve` daemon predates trace capture;
  restart it and ask the user to re-run the question.
- **The graph changed since the trace** — compare `graph_state` in the Symptom
  with `artmind vault status`; re-check the evidence against the live graph
  before blaming extraction.
- **You can't decide between two stages** — pick the cheaper (earlier) one,
  and say in the Diagnosis what evidence would move it later.
````

- [ ] **Step 4: Write `references/stage-walk.md`**

Create `artmind/skills/artmind-review/references/stage-walk.md` with exactly this content:

````markdown
# Stage walk

Walk the stages in this order and stop at the first that failed. Each later
stage is more expensive to fix. A verdict must cite evidence: a span id, a
result row, a chunk sentence (quoted as `>`), or a staging entry.

## Ground truth

The chunks a correct answer rests on.

1. The chunks the answer **used**: the `chunks` and `documents` evidence of the
   productive spans in `artmind traces show`, and the answer's `### Sources`.
2. The chunks it **should** have used: search the question and the operator's
   `expected` note — `artmind query vector-text --domain D "<expected fact>"` —
   and read the hits with `artmind query chunks --domain D --idList <id> --expand 1`.
3. If no chunk states what a correct answer needs → `corpus-gap`. Stop.

## The stages

### `corpus-gap`
No ingested document states the fact. Proposal: what to ingest (which kind of
document, which domain). No shape audit, no synthetic example.

### `synthesis`
The right rows came back, but the answer misstated them, left them out, or went
beyond them. Evidence: the span rows next to the answer sentence. Proposal: a
change to the `artmind-query` skill's answer rules (file and section).

### `routing`
A view, pattern or command that answers the question exists and was not used,
or was used with the wrong parameters (wrong entity id, wrong relationship
type, wrong domain). Evidence: the unproductive span's command and args, and
the command you ran that does answer it. Proposal: a change to the query skill
(which step, which rule) or to a view's `SKILL.md` description.

### `missing-view`
The question needs specific Cypher and it recurs (`artmind traces list` shows
similar questions). Proposal: a `view.yaml` + `SKILL.md` draft
(`artmind-create-view`'s format); Proof: `artmind views test` output.

### `identity`
The facts exist but are split across duplicate entities or merged into the
wrong one. Evidence: two `entity-resolve` candidates or `entity-context`
results for one real thing. Shape audit required. Proposal: a same-as /
curation task (`artmind-curate`), or a canonicalization prompt fix if it is
systematic.

### `extraction`
The fact is in the chunk, the schema can represent it, and it is absent from
(or wrong in) the staging JSON. Shape audit required. Proposal: a
`meta.yaml` prompt change or extraction-code change, with the Synthetic
example as the test case.

### `schema`
The schema has no class, property or relationship type that could hold the
fact in the shape the question needs. Shape audit required. Proposal: a schema
YAML diff (domain schema in the vault — vault content — or a packaged schema
in `artmind/domains/schemas/` — code). Note the re-ingest it forces.

## Graph misses

A turn with `fell_through: true` and `answer_basis: vector` was answered from
chunk text after the graph failed — even if the answer is right.

- The vector-returned chunks the answer cites are the ground truth; no search.
- Skip `synthesis` when the answer is rated good or unrated.
- Start at `routing`: for every unproductive graph span in the retrieval path,
  was the command, entity id and relationship type right? Re-run it corrected;
  if the corrected call answers, the verdict is `routing`.
- Then `missing-view`, `identity`, `extraction`, `schema`, always with the
  shape audit on the cited chunks.
- The regression question's evaluation comment must require a graph answer:
  "answered from the graph (answer_basis graph or mixed, no fall-through)".

## Shape audit

Compare three things for the ground-truth chunks:

- **Text** — `artmind query chunks --domain D --idList <ids> --expand 1`.
- **Graph** — the entities and relationships extracted from those chunks:
  `artmind query entity-context --domain D --entityId <id>` for each entity
  the text mentions (its `chunks` show which chunks it came from).
- **Staging** — what extraction produced before merge/commit. Find the
  document's staging folder and filter its files by chunk id:

  ```bash
  KG="$ARTMIND_VAULT/.artmind/data/kg/<domain>"
  grep -l '"<chunk_id>"' "$KG"/*/chunks.json        # → <folder>/chunks.json
  # then read <folder>/observations.json and <folder>/relationships.json,
  # keeping entries whose "chunk_id" is <chunk_id>
  ```

  In staging but not in the graph → lost at merge/commit (identity or a code
  bug). Not in staging → lost at extraction (or the schema has no place for it).
- **Schema** — `$ARTMIND_VAULT/.artmind/domains/schemas/<domain>_schema.yaml`,
  the classes involved.
- **Question** — the traversal that would answer it, written as an
  answerability pattern:

  ```json
  [{"class": "Person"}, {"rel": "WENT_TO", "dir": "out"}, {"class": "Place"},
   {"require": {"node": 0, "properties": ["name"]}}]
  ```

Grade against the checklist; tag each finding `<domain>/<Class>[.<property>|-<REL>->]`:

| Check | Finding when |
|---|---|
| coverage | a fact the question needs is in the text and absent from the subgraph |
| granularity | a fact is flattened into a string property or `description` when the question needs an entity plus a relationship — or a trivial value is promoted to a node |
| specificity / direction | a generic relationship type where the question needs a typed one, or the edge points the wrong way |
| reification | an n-ary fact (actor, action, object, time, role) collapsed into a binary edge, losing qualifiers |
| qualifiers | temporal, status or provenance qualifiers missing or on the wrong element |
| identity | one real thing split across nodes, or distinct things merged |
| noise | entities or edges the text does not support |
| answerability | the answerability pattern has no match in the subgraph |
````

- [ ] **Step 5: Write `references/dossier-format.md`**

Create `artmind/skills/artmind-review/references/dossier-format.md` with exactly this content:

````markdown
# Dossier format

A dossier is `<vault>/.artmind/reviews/<review_id>.md`: YAML frontmatter, then
nine `## ` sections in a fixed order. `artmind review new` writes the skeleton;
`artmind review validate` checks it. HTML comments (`<!-- … -->`) are
placeholders and do not count as content.

## Frontmatter

| Key | Value |
|---|---|
| `review_id` | `rv-YYYY-MM-DD-xxxxxx` (set by `review new`; never change it) |
| `status` | `open` · `in-progress` · `fixed` · `verified` · `dismissed` — change with `artmind review status` |
| `stage` | `corpus-gap` · `synthesis` · `routing` · `missing-view` · `identity` · `extraction` · `schema` |
| `elements` | schema elements `domain/Class`, `domain/Class.property`, `domain/Class-REL->`; required for identity / extraction / schema |
| `trace_ids` | every trace this dossier covers |
| `private_terms` | every private name or value the dossier quotes (pre-filled from the trace; add more) — `review leakcheck` uses it |
| `graph_state` | what the first trace ran against (from the trace) |
| `created`, `updated` | dates (`updated` is set on every save) |

## Sections

| Section | Content | Required |
|---|---|---|
| Symptom | question, answer excerpt, rating and note, retrieval path (pre-filled) | always |
| Diagnosis | the stage verdict and its evidence chain: span id → rows → chunk sentence (`>` quote) → staging entry | always |
| Shape audit | findings table `| element | check | source sentence | why the question needs it |`, then the answerability pattern (JSON) | identity, extraction, schema |
| Proposal | view.yaml + SKILL.md draft · schema YAML diff · meta.yaml prompt diff · code-change spec (files, functions, behaviour) · for corpus-gap: what to ingest | always |
| Proof | `artmind views test` output for a view (re-extraction diffs come later) | recommended |
| Synthetic example | the same failure with **invented** entities and text — the only material a coding session may put in the public checkout | all but corpus-gap |
| Regression question | exactly one `### R-<review_id> — <title>` / `**Question:**` / `**Evaluation comment:**` block | always |
| Agent prompt | starts with the privacy preamble (pre-filled; keep it verbatim), then a self-contained task: context, files to change, acceptance criteria — tests built on the Synthetic example; `artmind review leakcheck <review_id> --base master` passes; the regression question passes (after a re-ingest if the schema changed) | always |
| Log | dated lines; `review status` appends status changes | always |

## Quoting

Quote source sentences and other private text as `>` blockquotes, and put
source sentences in the Shape audit table. `review leakcheck` treats exactly
those — plus `private_terms` — as private. Your own analysis prose is not
checked, so a coding session may paraphrase it.
````

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_skill.py test/test_webui_profiles.py test/test_webui_help.py -v`
Expected: PASS.

- [ ] **Step 7: Link the skill into the checkout**

Run `just dev-refresh-skills`, then `ls -la .claude/skills/artmind-review`. Expected: a symlink into `artmind/skills/artmind-review`. `.claude/skills` is gitignored, so there's nothing to commit.

- [ ] **Step 8: Commit**

```bash
git add artmind/skills/artmind-review test/test_reviews_skill.py
git commit -m "feat(reviews): artmind-review skill -- stage walk, shape audit, dossier format"
```

---

### Task 10: Docs, recipes, spec

**Files:**
- Modify: `CLAUDE.md`, `justfile`, `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`

- [ ] **Step 1: `CLAUDE.md`**

1. Insert a repo-layout row after the `artmind/traces/` row:

   ```markdown
   | `artmind/reviews/` | Review dossiers (spec `docs/superpowers/specs/2026-10-11-query-review-loop-design.md` §7-§8): one markdown file per diagnosed query failure under `paths.REVIEWS_DIR` (`<vault>/.artmind/reviews/`, committed with the vault's private repo; never ingested). `dossier` (format, atomic save — temp files go to `logs/`, never the committed folder), `validate`, `scaffold` (from a trace), `index` (`trace_reviews` is a rebuildable (review, trace) index), `leakcheck` (scans a checkout diff for a dossier's `private_terms` and quoted text). Driven by the `artmind-review` skill from Claude Code in the checkout with `ARTMIND_VAULT=<vault>`. **Dossiers hold private corpus data; the checkout is public** — never copy dossier content into code, tests or commits. CLI: `artmind review new/list/show/status/validate/reindex/leakcheck` (not proxied). |
   ```

2. Under "Command routing quick reference", after the `artmind traces <cmd>` bullet:

   ```markdown
   - `artmind review <cmd>` — review dossiers: `new`, `list`, `show`, `status`, `validate`, `reindex`, `leakcheck` (not proxied)
   ```

- [ ] **Step 2: `justfile`**

Add these after the `traces-*` recipes, in the same section style:

```just
# ── artmind review ───────────────────────────────────────────────────────────

# review dossiers in the vault  (usage: just review-list [status])
review-list status="":
    uv run artmind review list {{ if status != "" { "--status " + status } else { "" } }}

# check a dossier is complete for its stage  (usage: just review-validate <review_id>)
review-validate review_id:
    uv run artmind review validate {{ review_id }}

# scan this checkout's changes for a dossier's private evidence  (usage: just review-leakcheck <review_id> [base])
review-leakcheck review_id base="master":
    uv run artmind review leakcheck {{ review_id }} --base {{ base }}
```

- [ ] **Step 3: Spec**

In `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`:

1. In §8.2, add `private_terms: [<private names and values the dossier quotes>]` as a frontmatter line after `trace_ids:`, and `updated: 2026-10-11` after `created:`.

2. At the end of §8.1, add:

   ```markdown
   - CLI (P2): `artmind review new <trace_id>` scaffolds a dossier from a trace
     (Symptom, Regression question, Agent prompt preamble and Log pre-filled;
     `private_terms` harvested from the trace's resolved entity names and
     arguments); `list`, `show`, `status <id> <status> --note`, `validate`
     (per-stage completeness), `reindex`, `leakcheck`. Dossier temp files are
     written to `logs/`, never the committed folder. An unresolved merge
     conflict in a dossier blocks `vault sync` (its preflight refuses
     conflicts under `.artmind/`), like any artmind file.
   ```

3. In §8.3, replace "or that shares a 5-word sequence (case-folded) with the dossier's quoted text" with:

   ```markdown
   or that shares a 5-word sequence (case-folded) with the dossier's quoted
   text: the `>` blockquotes in Symptom, Diagnosis and Shape audit, and the
   Shape audit table rows. The reviewer's own prose is not private, so a
   coding session may paraphrase the diagnosis. Untracked files count as
   added; `.artmind/reviews/` is skipped.
   ```

- [ ] **Step 4: Check**

Run: `just --list | grep review`, `uv run artmind review --help`, and `uv run --group dev pytest test/test_cli_guide.py -q`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md justfile docs/superpowers/specs/2026-10-11-query-review-loop-design.md
git commit -m "docs(reviews): CLAUDE.md layout and routing, just recipes, spec details"
```

---

### Task 11: Full suite and an end-to-end review

- [ ] **Step 1:** `just dev-test`. Everything passes.
- [ ] **Step 2:** Run `just dev-install`, then restart `serve` from the vault.
- [ ] **Step 3:** From the checkout, with `ARTMIND_VAULT=<vault>`:
  1. Run `artmind traces triage`.
  2. Run `artmind review new <trace_id>` on the top item.
  3. Run the `artmind-review` skill on it: fill the dossier, then `artmind review validate` until it reports ok.
  4. Confirm the dossier is under `<vault>/.artmind/reviews/` and that `artmind vault status` shows no drift.
  5. Confirm `artmind vault doctor` reports the "review dossiers privacy" check.
- [ ] **Step 4:** Make a throwaway edit in the checkout that quotes a dossier sentence. Confirm `artmind review leakcheck <id>` exits 1, then revert the edit.
- [ ] **Step 5:** Report what was verified.

---

## Self-review

**Spec coverage (P2 row of §10):**

| Spec item | Task |
|---|---|
| skill with stage attribution (§7.1–7.2) | 9 |
| dossier writer and format (§8.1–8.2) | 3, 5 |
| `review list/show/reindex` | 7, 5 |
| `.artmind/reviews/` layout | 2 |
| `vault doctor` visibility check | 8 |
| privacy preamble and `review leakcheck` (§8.3) | 3, 6, 7 |
| docs | 10 |
| end-to-end check | 11 |

**Additions beyond the spec, with reasons:**
- `review new`, `status` and `validate`: the skill needs a deterministic scaffold and gate.
- `private_terms`: leakcheck needs a machine-readable list of names.

**Deferred to P3/P4:** `review shape`, `reextract`, `shape-report`, appending a trace to an existing dossier by command (P2 does it by hand, per skill rule 5), `regressions`, `verify`.
