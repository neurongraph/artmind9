# Ownership Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `.artmind/` artmind-owned and committed — so a vault clone reproduces the graph without paying for extraction again — and delete the promotion machinery that only existed because converted markdown used to be user-editable.

**Architecture:** Ordered so nothing bloats git. Embeddings are stripped from KG staging **first**, because committing staging before that would put megabytes of undeltable float noise into history permanently. Then the `.gitignore` flips to commit derived output. Then sources stop being copied when they already live in the vault, external ones move to `_external_docs/` under path identity, and finally the promotion branches are deleted — by which point nothing depends on them.

**Tech Stack:** Python 3.14, Click (rich_click), pytest, `uv`, `just`.

**Runs after:** [2026-08-30-agent-grounding-gate.md](./2026-08-30-agent-grounding-gate.md).

**Read before starting:** [docs/vault.md](../../vault.md) — "The ownership rule", "What is in git", "Embeddings", "Where a document lands" — and [docs/stores-and-repos.md](../../stores-and-repos.md). Also `CLAUDE.md`: green tests do not mean the CLI works, and a mocked graph session answers every query successfully.

**Not in scope:** standalone-image handling (describe rather than OCR) — independent of git layout, and its own plan. Removing `documents/markdowns/` — it is now the *canonical* home for converted markdown, so it stays.

---

## Why the order is the design

Three of these tasks could be done in any order and two cannot:

- **Embeddings must be stripped before staging is committed.** Git never forgets. Committing `chunks.json` with vectors and stripping them later leaves the vectors in history forever, and only a history rewrite removes them.
- **Promotion must be deleted last.** Tasks 3–5 change where sources land, and every one of them touches `_ingest_binary_derived`. Gutting it first means re-deriving decisions the later tasks need.

---

## File Structure

| File | Responsibility |
|---|---|
| `artmind/ingest.py` (modify) | Strip embeddings when persisting; stop copying vault-resident sources; route external ones to `_external_docs/`; delete the promotion branches. |
| `artmind/embed_sweep.py` (create) | The resumable chunk-embedding sweep. Separate because it is a standalone operation with its own CLI command, not part of the ingest path. |
| `artmind/vault.py` (modify) | `GITIGNORE_BLOCK` inverts: commit derived output, exclude a named list. `_external_docs` joins the layout. |
| `artmind/projection.py` (modify) | `status()` reports unembedded chunks. |
| `artmind/cli.py` (modify) | `ingest embed-chunks`; `write-to-graph --noEmbed`. |
| `artmind/derived_markdown.py` (**delete**) | The promotion decision table. Nothing needs it. |
| `test/test_embed_sweep.py` (create) | The sweep, its resumability, and the status count. |
| `test/test_vault_scaffold.py` (modify) | The inverted `.gitignore`. |
| `test/test_external_docs.py` (create) | Path identity and the copy rules. |
| `test/test_ingest_binary_derived.py` (modify) | Promotion tests are deleted, not weakened. |

---

## Task 1: Strip embeddings from persisted KG staging

**Files:**
- Modify: `artmind/ingest.py`
- Test: `test/test_embed_sweep.py` (create)

An embedding is a pure function of `(text, embedding model)` — derived, deterministic, and reproducible locally at no API cost. It is the one thing in KG staging that git should not carry: measured on this corpus, ten versions of one `chunks.json` cost **60 KB** of git objects with embeddings and **20 KB** without, because changing one word changes all 768 floats and there is nothing to delta against.

Embeddings are written in two places — the per-chunk JSON (`artmind/ingest.py:2035`) and the aggregated `chunks.json` (`:2210`). Both must be stripped.

- [ ] **Step 1: Write the failing tests**

Create `test/test_embed_sweep.py`:

```python
"""Embeddings are stripped from persisted staging (docs/vault.md, "Embeddings").

They are a pure function of (text, model): derived, deterministic, and free to
recompute locally. Keeping them out of committed staging is what stops git
history accumulating undeltable float noise.
"""
from __future__ import annotations

import json

from artmind.ingest import strip_embeddings


def test_an_embedding_is_removed():
    chunk = {"chunk_id": "c1", "text": "hello", "embedding": [0.1, 0.2, 0.3]}

    assert strip_embeddings(chunk) == {"chunk_id": "c1", "text": "hello"}


def test_everything_else_survives():
    """Only the vector goes -- the chunk's identity, text and filing metadata
    are what the graph write needs."""
    chunk = {
        "chunk_id": "c1", "text": "hello", "embedding": [0.1],
        "doc_id": "d1", "domain": "general", "project": "p", "area": "a",
        "prev_chunk_id": None, "next_chunk_id": "c2",
    }

    stripped = strip_embeddings(chunk)

    assert "embedding" not in stripped
    assert stripped["chunk_id"] == "c1"
    assert stripped["next_chunk_id"] == "c2"
    assert stripped["project"] == "p"


def test_a_chunk_without_an_embedding_is_unchanged():
    chunk = {"chunk_id": "c1", "text": "hello"}

    assert strip_embeddings(chunk) == chunk


def test_the_original_is_not_mutated():
    """The in-memory chunk keeps its vector -- it is still written to the graph
    in this same run. Only what lands on disk is stripped."""
    chunk = {"chunk_id": "c1", "embedding": [0.1]}

    strip_embeddings(chunk)

    assert chunk["embedding"] == [0.1]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_embed_sweep.py -v`
Expected: FAIL, `ImportError: cannot import name 'strip_embeddings'`

- [ ] **Step 3: Implement**

Add to `artmind/ingest.py`, near the other JSON helpers:

```python
def strip_embeddings(chunk: dict) -> dict:
    """A copy of `chunk` with its vector removed, for persisting to disk.

    An embedding is a pure function of (text, embedding model) -- derived,
    deterministic, and free to recompute locally. KG staging is committed to
    git (docs/vault.md), and a vector changes completely when one word of its
    text changes, so it cannot be delta-compressed: ten versions of one
    chunks.json cost 60 KB of git objects with embeddings and 20 KB without.

    Returns a copy so the in-memory chunk keeps its vector -- the same run
    still writes it to the graph.
    """
    if "embedding" not in chunk:
        return chunk
    return {k: v for k, v in chunk.items() if k != "embedding"}
```

Then apply it at both persistence points. At `artmind/ingest.py:2035`:

```python
        chunk_json.write_text(
            json.dumps(strip_embeddings(data), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
```

and where `chunks.json` is aggregated (`:2210`, via `_write_json`):

```python
    _write_json("chunks.json", [strip_embeddings(c) for c in all_chunks])
```

**Do not change the graph-write path.** `_write_to_neo4j` builds its chunk node with `{k: data[k] for k in (…) if k in data}` (`:2109`), which already tolerates a missing `embedding` — a chunk simply arrives without a vector. That is exactly the state Task 2's sweep exists to repair.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_embed_sweep.py -v`
Expected: PASS, 4 passed

Run: `just dev-test`
Expected: all green. If a test asserts an embedding is present in a written `chunks.json`, that assertion is now wrong — but check whether it was really testing persistence or testing that embedding *happened*, and preserve the latter.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_embed_sweep.py
git commit -m "feat(ingest): persisted staging carries no embeddings"
```

---

## Task 2: A resumable chunk-embedding sweep

Restoring from committed staging now produces chunks with no vectors, and **a null embedding is absent from the vector index** — the chunk is simply invisible to semantic search. No error, just quietly worse answers. That silence is why this needs both a repair and a report.

The pattern already exists for entities: `embed_missing_entity_embeddings`, the `embedding_stale` flag, and `artmind ingest embed-entities`. This mirrors it.

**Files:**
- Create: `artmind/embed_sweep.py`
- Modify: `artmind/cli.py`
- Test: `test/test_embed_sweep.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_embed_sweep.py`:

```python
class _RecordingSession:
    """Records the Cypher run and the parameters sent.

    CLAUDE.md: a mocked session returns truthy for ANY query, so asserting on
    counts proves nothing. Assert on what was actually sent.
    """

    def __init__(self, rows):
        self._rows = rows
        self.queries: list[tuple[str, dict]] = []

    def run(self, cypher, **params):
        self.queries.append((cypher, params))
        rows, self._rows = self._rows, []
        return _Result(rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows

    def single(self):
        return self._rows[0] if self._rows else None


def test_only_chunks_without_a_vector_are_embedded():
    from artmind.embed_sweep import embed_missing_chunk_embeddings

    session = _RecordingSession([
        {"id": "c1", "text": "alpha"},
        {"id": "c2", "text": "beta"},
    ])
    embedded = []

    result = embed_missing_chunk_embeddings(
        session, embed=lambda t: embedded.append(t) or [0.1, 0.2],
    )

    assert embedded == ["alpha", "beta"]
    assert result["embedded"] == 2
    fetch = session.queries[0][0]
    assert "embedding IS NULL" in fetch, "the sweep must select only unembedded chunks"


def test_the_vector_is_written_back_keyed_by_chunk_id():
    from artmind.embed_sweep import embed_missing_chunk_embeddings

    session = _RecordingSession([{"id": "c1", "text": "alpha"}])

    embed_missing_chunk_embeddings(session, embed=lambda t: [0.5])

    writes = [(q, p) for q, p in session.queries if "SET" in q]
    assert writes, "nothing was written back"
    assert writes[0][1].get("id") == "c1" or "c1" in str(writes[0][1])


def test_an_already_embedded_graph_is_a_no_op():
    from artmind.embed_sweep import embed_missing_chunk_embeddings

    session = _RecordingSession([])

    result = embed_missing_chunk_embeddings(session, embed=lambda t: [0.1])

    assert result["embedded"] == 0


def test_progress_is_reported_for_a_long_run():
    """A fresh clone is minutes of local work; silence looks like a hang.

    Progress goes through an injected callback rather than loguru, so it can be
    asserted directly. `test_ingest_entity_filtering.py` takes `caplog` and
    never asserts on it — loguru does not feed pytest's caplog without a
    bridge, so a caplog assertion here would pass for the wrong reason.
    """
    from artmind.embed_sweep import embed_missing_chunk_embeddings

    session = _RecordingSession([{"id": f"c{i}", "text": "x"} for i in range(120)])
    seen: list[tuple[int, int]] = []

    embed_missing_chunk_embeddings(
        session, embed=lambda t: [0.1],
        progress_every=50, on_progress=lambda done, total: seen.append((done, total)),
    )

    assert seen, "expected at least one progress callback"
    assert seen[-1][0] <= 120


def test_progress_defaults_to_no_callback():
    """The default path must not require a caller to supply one."""
    from artmind.embed_sweep import embed_missing_chunk_embeddings

    session = _RecordingSession([{"id": "c1", "text": "x"}])

    result = embed_missing_chunk_embeddings(session, embed=lambda t: [0.1])

    assert result["embedded"] == 1
```

**Read `artmind/ingest.py`'s `embed_missing_entity_embeddings` before writing the implementation** — match its batching, its Cypher shape, and how it reports. The point is symmetry with a mechanism that already works, not a second idiom.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_embed_sweep.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'artmind.embed_sweep'`

- [ ] **Step 3: Implement the sweep**

Create `artmind/embed_sweep.py` with
`embed_missing_chunk_embeddings(session, *, embed=None, batch_size=100, progress_every=50, on_progress=None) -> dict`.
It must:

- select `:DocChunk` nodes where `embedding IS NULL`, in batches
- embed each chunk's `text` (defaulting `embed` to the real embedding call, injectable for tests)
- write the vector back keyed by chunk id
- return `{"embedded": N, "remaining": M}`
- call `on_progress(done, total)` every `progress_every` chunks when given, and
  log the same through loguru so a real run is visible. The callback exists so
  the behaviour is assertable — loguru does not feed pytest's `caplog`.
- log a one-line summary at the end

Resumability is inherent: each batch commits its own writes, so an interrupted run leaves the chunks it finished embedded and the rest still `NULL` for the next run to pick up. **Say that in the docstring** — it is the property that justifies a sweep rather than inline work.

- [ ] **Step 4: Add the CLI command and the `write-to-graph` flag**

In `artmind/cli.py`, add `ingest embed-chunks` alongside the existing `embed-entities`, taking `--domain` and `--compact`. Route it into `COMMAND_GROUPS` under Ingestion's "Graph building" panel, or `test_cli_guide.py` will fail.

Then have `write-to-graph` run the sweep by default, with `--noEmbed` to skip it:

```python
@click.option("--noEmbed", "no_embed", is_flag=True,
              help="Skip the chunk-embedding sweep. Chunks written without a vector are "
                   "invisible to semantic search until `ingest embed-chunks` runs.")
```

Before a long sweep, print what is about to happen — a fresh clone is minutes of local work and silence looks like a hang:

```
Re-embedding 1,529 chunk(s) — committed KG staging carries no vectors by design
(docs/vault.md). Local model, no API cost. This runs once per clone.
```

- [ ] **Step 5: Report the gap in `projection status`**

Narrating work while it happens does not help with the dangerous state, which is the one where the work did **not** happen and nobody noticed. Add an unembedded-chunk count to `projection.status` (`artmind/projection.py:1150`) so the question "is my search actually complete?" has an answer you can ask for.

Add to both returned dicts:

```python
        "unembedded_chunks": tx.run(
            "MATCH (c:DocChunk) WHERE c.embedding IS NULL RETURN count(c) AS n"
        ).single()["n"],
```

`status` takes a `tx` and is called through `read_session()`, so this is a read and stays within the read-only guarantee its docstring makes.

- [ ] **Step 6: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_embed_sweep.py test/test_cli_guide.py -v`
Expected: PASS

Run: `just dev-test`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add artmind/embed_sweep.py artmind/cli.py artmind/projection.py test/test_embed_sweep.py
git commit -m "feat(ingest): resumable chunk-embed sweep, and report the gap"
```

---

## Task 3: Invert the `.gitignore`

Staging is now git-ready, so derived output can be committed. The exclusions are not arbitrary — each is a secret, a churning binary, or machine-local state.

**Files:**
- Modify: `artmind/vault.py`
- Test: `test/test_vault_scaffold.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_scaffold.py`:

```python
def test_derived_output_is_committed(tmp_path):
    """The ownership rule: .artmind/ is artmind's, and it is versioned."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data" / "markdowns").mkdir(parents=True)
    (tmp_path / ".artmind" / "data" / "kg" / "general" / "doc").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "markdowns" / "deck.md").write_text("# deck")
    (tmp_path / ".artmind" / "data" / "kg" / "general" / "doc" / "chunks.json").write_text("[]")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert "data/markdowns/deck.md" in status
    assert "data/kg/general/doc/chunks.json" in status


def test_the_graph_password_is_still_never_committed(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".artmind").mkdir(exist_ok=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "config.env").write_text("ARTMIND_KG_NEO4J_PASSWORD=secret\n")

    assert "config.env" not in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def test_the_registry_is_not_committed(tmp_path):
    """A SQLite binary rewritten on every ingest merges catastrophically, and
    `docs reindex` rebuilds it."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "document_registry.db").write_bytes(b"sqlite")

    assert "document_registry.db" not in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def test_archives_are_never_committed_wherever_they_are(tmp_path):
    """Snapshots are large opaque duplicates of what git already versions, and
    the rule is by extension so one dropped anywhere stays out."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data" / "graph_snapshot").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "graph_snapshot" / "s.zip").write_bytes(b"zip")
    (tmp_path / "stray.tar.gz").write_bytes(b"tgz")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert "s.zip" not in status
    assert "stray.tar.gz" not in status


def test_binaries_in_the_vault_are_now_committed(tmp_path):
    """This reverses the previous model, which gitignored them and left them
    with no version history and no second copy."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind").mkdir(exist_ok=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / "area1").mkdir()
    (tmp_path / "area1" / "deck.pptx").write_bytes(b"binary")

    assert "area1/deck.pptx" in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def test_logs_and_runtime_state_are_not_committed(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "logs").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "logs" / "x.log").write_text("log")
    (tmp_path / ".artmind" / "state.json").write_text("{}")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert "x.log" not in status
    assert "state.json" not in status
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v`
Expected: FAIL — the current block ignores `.artmind/data/` wholesale and ignores `*.pptx`.

- [ ] **Step 3: Implement**

Replace `GITIGNORE_BLOCK` in `artmind/vault.py`. The rule inverts: everything is committed **except** a named list.

```python
GITIGNORE_BLOCK = """\
# ── artmind ───────────────────────────────────────────────────────────────────
# .artmind/ belongs to artmind and is versioned with your vault, so a clone
# reproduces the graph without paying for extraction again. These are the
# exceptions, and each is a secret, a churning binary, or machine-local state.

# Holds the graph password. A vault is a repo you may push.
.artmind/config.env
# A SQLite binary rewritten on every ingest; merges catastrophically, and
# `artmind docs reindex` rebuilds it from vault frontmatter.
.artmind/data/document_registry.db
.artmind/data/document_registry.db-shm
.artmind/data/document_registry.db-wal
# Machine-local runtime state, meaningless on another machine.
.artmind/logs/
.artmind/state.json
.artmind/serve.json
.artmind/worker.pid
# artmind's own skills are symlinks to the installed copy; yours are not
# matched by this and stay committable.
.claude/skills/artmind-*

# Snapshots: large, opaque, and already a complete copy of what git versions.
# By extension rather than path, so one dropped anywhere stays out of both git
# and ingestion.
*.zip
*.tar.gz
*.tgz
# ── end artmind ───────────────────────────────────────────────────────────────
"""
```

Note what is **absent**: no `*.pdf`, `*.pptx`, `*.png` rules, and no blanket `.artmind/data/`. Binaries and derived output are committed now, so the `!_derived/**` negation the old block needed is gone with them.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_vault_scaffold.py -v`
Expected: PASS

Run: `just dev-test`
Expected: all green. Tests asserting binaries were ignored are now asserting the opposite of the design — delete them rather than inverting them in place, and say so in your report.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault.py test/test_vault_scaffold.py
git commit -m "feat(vault): commit derived output, exclude secrets and churn"
```

---

## Task 4: `_Inbox/` and archives are never ingested

**Files:**
- Modify: `artmind/ingest.py`
- Test: `test/test_ingest_supported_types.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_ingest_supported_types.py`:

```python
def test_the_inbox_is_never_ingested(tmp_path):
    """A drafting area that needs no configuration -- moving a note OUT of it
    is what says "this is ready"."""
    (tmp_path / "_Inbox").mkdir()
    (tmp_path / "_Inbox" / "draft.md").write_text("# half-written")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "real.md").write_text("# real")

    found = [f.name for f in collect_ingest_files(tmp_path)]

    assert found == ["real.md"]


def test_archives_are_never_ingested(tmp_path):
    """A snapshot is a copy of the graph; ingesting it would be circular."""
    (tmp_path / "snap.zip").write_bytes(b"zip")
    (tmp_path / "backup.tar.gz").write_bytes(b"tgz")
    (tmp_path / "note.md").write_text("# note")

    found = [f.name for f in collect_ingest_files(tmp_path)]

    assert found == ["note.md"]


def test_a_nested_inbox_is_also_skipped(tmp_path):
    (tmp_path / "area1" / "_Inbox").mkdir(parents=True)
    (tmp_path / "area1" / "_Inbox" / "draft.md").write_text("# draft")

    assert collect_ingest_files(tmp_path) == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_ingest_supported_types.py -v`
Expected: FAIL — the inbox draft and the archives are all returned.

- [ ] **Step 3: Implement**

In `artmind/ingest.py`, add beside `SUPPORTED_SUFFIXES`:

```python
# Directory names never walked, at any depth. `_Inbox` is a drafting area:
# moving a note OUT of it is what marks the note ready, which needs no
# configuration and no status field (docs/vault.md).
NEVER_WALKED = frozenset({"_Inbox"})

# Archives are never ingested. A snapshot is a copy of the graph, so ingesting
# one would be circular; they are also excluded from git for the same reason.
ARCHIVE_SUFFIXES = frozenset({".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar"})
```

Then extend the directory-walk filter in `collect_ingest_files`:

```python
            and not any(p in NEVER_WALKED for p in f.relative_to(path).parts)
            and f.suffix.lower() not in ARCHIVE_SUFFIXES
```

`ARCHIVE_SUFFIXES` is belt-and-braces — none of them are in `SUPPORTED_SUFFIXES` — but it states the rule where a reader looks for it, and the `.gitignore` states the same rule for git.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_ingest_supported_types.py -v`
Expected: PASS

Run: `just dev-test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_ingest_supported_types.py
git commit -m "feat(ingest): _Inbox and archives are never walked"
```

---

## Task 5: External sources land in `_external_docs/`, under path identity

**Files:**
- Modify: `artmind/ingest.py`, `artmind/vault.py`
- Test: `test/test_external_docs.py` (create)

A source from outside the vault is copied in, so the vault holds a record of what was ingested. Identity is the **source path**, not the filename: two different decks both called `deck.pptx` are different documents. Name-based identity is precisely the problem `_artmind_id` was introduced to solve, and it must not creep back in here.

- [ ] **Step 1: Write the failing tests**

Create `test/test_external_docs.py`:

```python
"""External sources are copied into the vault under path identity."""
from __future__ import annotations

from pathlib import Path

from artmind.ingest import external_copy_path


def test_a_source_is_copied_under_the_vault(tmp_path):
    dest = external_copy_path(Path("/somewhere/else/deck.pptx"), tmp_path)

    assert dest.is_relative_to(tmp_path / "_external_docs")
    assert dest.name == "deck.pptx"


def test_the_same_path_maps_to_the_same_destination(tmp_path):
    """Re-ingesting an edited file must land on its previous copy, so git shows
    a new version rather than a new document."""
    a = external_copy_path(Path("/somewhere/deck.pptx"), tmp_path)
    b = external_copy_path(Path("/somewhere/deck.pptx"), tmp_path)

    assert a == b


def test_different_sources_with_the_same_name_do_not_collide(tmp_path):
    """Two different decks both called deck.pptx are different documents, not
    versions of each other."""
    a = external_copy_path(Path("/team-a/deck.pptx"), tmp_path)
    b = external_copy_path(Path("/team-b/deck.pptx"), tmp_path)

    assert a != b
    assert a.name == b.name == "deck.pptx"


def test_the_destination_is_stable_across_runs(tmp_path):
    """Derived from the path alone, so it does not depend on what is already
    on disk -- otherwise the mapping would drift as files come and go."""
    first = external_copy_path(Path("/somewhere/deck.pptx"), tmp_path)
    (first.parent).mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"x")

    assert external_copy_path(Path("/somewhere/deck.pptx"), tmp_path) == first
```

Plus an integration test, reusing `test/test_ingest_binary_derived.py`'s `env` fixture and `_fake_docling` stub — **move both to `test/conftest.py` if they are still module-local**, rather than declaring a second copy:

```python
def test_a_vault_resident_source_is_not_copied(env, monkeypatch):
    """The vault copy IS the source, and git already versions it."""


def test_an_external_source_is_copied_in(env, monkeypatch):
    """Nothing else in the vault records what was ingested."""
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_external_docs.py -v`
Expected: FAIL, `ImportError: cannot import name 'external_copy_path'`

- [ ] **Step 3: Implement**

Add to `artmind/ingest.py`:

```python
def external_copy_path(source: Path, vault_dir: Path) -> Path:
    """Where a source from outside the vault is copied to.

    Identity is the SOURCE PATH, not the filename: two different decks both
    called `deck.pptx` are different documents, and same-path-changed-bytes is
    a new version of one. Name-based identity is exactly the problem
    `_artmind_id` was introduced to solve (docs/document-identity.md) and must
    not creep back in here.

    The path is hashed rather than mirrored so the destination is short, stable
    and free of the source's directory structure — which may be absolute,
    machine-specific, or contain characters the vault should not inherit.
    """
    import hashlib

    digest = hashlib.sha256(str(Path(source).resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(vault_dir) / "_external_docs" / digest / Path(source).name
```

Add `external_docs_dir` to `VaultLayout` in `artmind/vault.py`, returning `self.root / "_external_docs"`.

Then in `_ingest_binary_derived` (and the ad-hoc markdown path), replace the unconditional copy into `ORIGINALS_DIR` with:

```python
    if _is_inside_vault(source, ARTMIND_VAULT_DIR):
        # The vault copy IS the source, and git already versions it.
        dest_path = source
    else:
        dest_path = external_copy_path(source, ARTMIND_VAULT_DIR)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest_path)
```

with `_is_inside_vault` as:

```python
def _is_inside_vault(source: Path, vault_dir: "Path | None") -> bool:
    """Does `source` already live in the vault? Decides whether artmind needs
    to keep its own copy."""
    if vault_dir is None:
        return False
    try:
        Path(source).resolve().relative_to(Path(vault_dir).resolve())
    except (OSError, ValueError):
        return False
    return True
```

**Read every later use of `dest_path` before changing it** — it feeds the registry path, the docling input, and change detection. Each must still be correct when `dest_path` is the vault file itself.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run --group dev pytest test/test_external_docs.py test/test_ingest_binary_derived.py -v`
Expected: PASS

Run: `just dev-test`
Expected: all green. `test/test_archive.py` and `test/test_unified_snapshot_phase5.py` assert on `originals/` contents — check whether their fixtures' sources are inside a vault, and whether the assertion is still the right one.

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py artmind/vault.py test/test_external_docs.py
git commit -m "feat(ingest): external sources land in _external_docs under path identity"
```

---

## Task 6: Delete the promotion machinery

Last, because everything above touches `_ingest_binary_derived` and gutting it first would mean re-deriving decisions those tasks need.

Promotion existed for one reason: converted markdown lived in a user-visible `_derived/` folder, so artmind had to detect your edits and adjudicate between them and the binary. Under the ownership rule nobody edits `.artmind/`, so there is nothing to adjudicate.

**Files:**
- Delete: `artmind/derived_markdown.py`
- Modify: `artmind/ingest.py`, `artmind/document_identity.py`
- Test: `test/test_ingest_binary_derived.py`, delete `test/test_derived_markdown.py` if present

- [ ] **Step 1: Delete the promotion tests first**

They assert behaviour that is being removed by design, so they go — **deleted, not weakened**. In `test/test_ingest_binary_derived.py` that is `test_editing_the_derived_markdown_promotes_it` and `test_binary_and_markdown_both_changed_is_a_collision`, plus any test importing `artmind.derived_markdown`.

Keep and re-verify: `test_first_ingest_converts_and_mints_an_artmind_id`, `test_reingest_unchanged_binary_is_a_no_op`, `test_binary_changed_reconverts_and_bumps_version`. Those describe behaviour that survives.

Run: `uv run --group dev pytest test/test_ingest_binary_derived.py -v`
Expected: the three surviving tests pass; the deleted ones are gone.

- [ ] **Step 2: Add the replacement test**

```python
def test_the_converted_markdown_stays_in_artmind(env, monkeypatch):
    """No `_derived/` in the vault: `.artmind/` owns the conversion, so it has
    one location for its whole life and nothing ever moves."""
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody.\n"]))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert not (vault / "_derived").exists()
    assert (ing.MARKDOWNS_DIR / "deck.md").is_file()
```

- [ ] **Step 3: Remove the machinery**

Delete `artmind/derived_markdown.py`. Then in `artmind/ingest.py` remove, in `_ingest_binary_derived`:

- the `derived_path` / `promoted_path_guess` resolution and the `already_promoted` branch
- `markdown_edited`, `_markdown_was_edited`, and the `_decide_promotion` call
- the `action == "promote"` and `action == "collision"` branches entirely
- every `_derived_sha256` read and write

What remains is: resolve identity, convert, write markdown to `MARKDOWNS_DIR`, split, extract. `no_op` survives — an unchanged binary should still skip the work — driven by the source hash alone.

In `artmind/document_identity.py`, remove `_derived_sha256` from `SYSTEM_FIELDS`.

Record the binary's hash so `no_op` still works. Since `.artmind/data/markdowns/<stem>.md` is artmind-owned and never user-edited, its frontmatter is a trustworthy baseline — add `_source_sha256` to `SYSTEM_FIELDS` and write it on every conversion:

```python
    # The hash of the binary this markdown was converted from. Trustworthy
    # because `.artmind/` is artmind-owned: nothing else writes this file, so
    # it needs no counterpart fingerprint of its own body the way the old
    # `_derived_sha256` did.
    "_source_sha256",
```

- [ ] **Step 4: Run the full suite**

Run: `just dev-test`
Expected: all green. Any remaining import of `artmind.derived_markdown` is a leftover — `grep -rn "derived_markdown\|_derived_sha256\|_decide_promotion" --include='*.py' .` should return nothing outside your own deletions.

- [ ] **Step 5: Commit**

```bash
git rm artmind/derived_markdown.py
git add -A
git commit -m "refactor(ingest): delete the promotion machinery"
```

---

## Task 7: Documentation

**Files:**
- Modify: `docs/vault.md`, `docs/INSTALL.md`, `README.md`

- [ ] **Step 1: Update `docs/vault.md`'s status line**

It currently says the code still implements the `_derived/` model. It no longer does. Move the promotion model wholly into "What this replaces", and remove the "The code still implements the `_derived/` model" bullet from "Known gaps".

- [ ] **Step 2: Document the fix-bad-conversion workflow where someone will hit it**

`docs/vault.md` states it under "The ownership rule". Add it to `README.md` too, next to the ingestion section, because that is where a user meets a mangled table:

```markdown
### When a conversion comes out wrong

You do not edit `.artmind/` — artmind owns it. Instead: copy the converted
markdown out into your vault as an ordinary note, move the original binary to
`_Inbox/` so it is not re-ingested, and ingest the note. It is then just a note,
with no special machinery holding it.
```

- [ ] **Step 3: `docs/INSTALL.md`**

Its layout table lists what is and is not in git. Update it for the inversion, and add `_external_docs/` and `_Inbox/`.

- [ ] **Step 4: Verify every command you documented**

Run each in a scratch vault. A doc that has drifted from the code is worse than no doc.

- [ ] **Step 5: Commit**

```bash
git add docs/vault.md docs/INSTALL.md README.md
git commit -m "docs: the ownership rule as implemented"
```

---

## Task 8: End-to-end verification

Green tests do not mean the CLI works (`CLAUDE.md`). Manual, with a real binary.

- [ ] **Step 1: Fresh install and vault**

```bash
just dev-stop-daemons && just dev-install
cd /tmp && rm -rf own-e2e && mkdir own-e2e && cd own-e2e
ARTMIND_NO_PROXY=1 artmind init
mkdir -p area1 _Inbox
cp <some-small-document>.docx area1/
echo "# draft" > _Inbox/draft.md
cat > .artmind/vault.yaml <<'EOF'
ingest:
  trigger: manual
  mappings:
    - path: area1/**
      domain: general
EOF
ARTMIND_NO_PROXY=1 artmind ingest sync . --stage-only
```

Real LLM calls — one small document.

- [ ] **Step 2: Confirm the layout**

```bash
ls .artmind/data/markdowns/                    # the converted markdown
ls _derived 2>/dev/null || echo "_derived absent — correct"
ls .artmind/data/documents/originals/ 2>/dev/null || echo "no copy — correct"
```

- [ ] **Step 3: Confirm what git sees**

```bash
git add -A && git status --porcelain | sed 's/^/  /'
```

Expected staged: `area1/*.docx`, `.artmind/data/markdowns/**`, `.artmind/data/kg/**`, `.artmind/vault.yaml`, `_Inbox/draft.md`.
Expected absent: `.artmind/config.env`, `.artmind/data/document_registry.db`, `.artmind/logs/**`.

- [ ] **Step 4: Confirm staging carries no vectors**

```bash
grep -c '"embedding"' .artmind/data/kg/general/*/chunks.json || echo "no embeddings — correct"
```

- [ ] **Step 5: Confirm the draft was skipped**

The ingest log should report `_Inbox/draft.md` skipped. Then move it into `area1/` and re-ingest: it should now be picked up.

- [ ] **Step 6: Confirm the embed sweep and its report**

```bash
ARTMIND_NO_PROXY=1 artmind projection status
```

Expected: an `unembedded_chunks` count. With a live Neo4j, run `artmind ingest embed-chunks` and confirm the count drops to zero.

- [ ] **Step 7: Clean up**

```bash
cd /tmp && rm -rf own-e2e
```

---

## Follow-on plans

- **Standalone images** — describe via the vision model rather than OCR; store the description as `<image>_desc.md` beside the image; OCR opt-in per mapping for scans of text, where a description is a poor substitute.
- **Migrating an existing vault** — `~/Projects/artmind-corpus` has `_derived/` content and a gitignore from the old model. A one-shot `artmind vault migrate` that moves `_derived/*` into the vault as ordinary notes, rewrites the gitignore, and re-ingests.
- **ACP grounding** — `opencode` runs as a separate process, so the claude-sdk gate does not reach it.
