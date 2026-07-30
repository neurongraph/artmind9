# Entity Supersession History Zone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make document supersession reach `:Entity` nodes, so `--asOf` queries stop returning entities from superseded documents as current, and preserve overwritten values in a queryable history zone.

**Architecture:** Selective versioning with an anchor and deltas. The live `:Entity` node stays the anchor and keeps its current shape; overwritten property values snapshot to `:EntityVersion` nodes that carry neither `:Entity` nor a class label, so no existing query, vector index, or refine pass can see them by construction. Entities no longer asserted by any live document are retired via a single-source-guarded stamp inside `apply_supersession()`, which all three supersession routes already converge on.

**Tech Stack:** Python 3.14, Click, Neo4j (via the `neo4j` driver), pytest. Tests are hermetic — no Neo4j, no network — using `monkeypatch` and hand-rolled fake session objects.

**Spec:** `docs/superpowers/specs/2026-07-29-entity-supersession-history-zone-design.md`

---

## Before you start

Read these three things:

1. **`CLAUDE.md`** — especially "Installed, not run from the checkout" and "Testing implications". The short version: `artmind` is installed globally and editable, a running `serve` daemon serves **stale code**, and green tests do not prove the CLI works.
2. **The spec** (path above). The plan implements it; the spec explains *why*.
3. **`test/test_ingest_hooks.py` and `test/test_supersession.py`** — every test in this plan follows their fake-session idiom. Copy their shape rather than reaching for `unittest.mock` machinery.

Run the suite once before touching anything, so you know the baseline is green:

```bash
just dev-test
```

Expected: all tests pass in roughly 9 seconds.

### The one non-obvious trap

`_upsert_entity`'s merge is **accretive**: when two documents assert the same property, strings become `"old | new"`. This happens inside `write_to_graph()`. That is why Task 4 captures prior values *before* the write — capturing afterwards would record `"£500 | £2,000"` instead of `"£500"`. Several tasks depend on this ordering; do not reorder them.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `artmind/entity_history.py` | The whole history zone: the capture gate, prior-value capture, snapshot writes, and the version query. Kept out of `ingest.py`, which is already ~1650 lines. |
| `test/test_entity_history.py` | Tests for the above. |
| `test/test_entity_retirement.py` | Tests for the retirement mechanism in `temporal.py`. |
| `test/test_conflict_resolution.py` | Tests for `resolve_conflict()` and its CLI command. |
| `test/test_domain_family.py` | Tests for `expand_domain_family()` and its two call sites. |

**Modified:**

| File | Change |
|---|---|
| `artmind/temporal.py` | `_retire_orphaned_entities()`, called from `apply_supersession()`; `normalize_time()` rollup |
| `artmind/ingest.py` | `RESERVED_REL_TYPES` gains `PRIOR_STATE`; `commit_to_graph()` wiring; `_reassert_superseding_properties()` signature |
| `artmind/graph_query.py` | `expand_domain_family()`; `entity_versions()` |
| `artmind/conflicts.py` | `resolve_conflict()`; domain expansion in `detect_conflicts()` |
| `artmind/cli.py` | `ingest resolve-conflict`, `query graph entity-versions`, scope rejection in `ingest supersede`, `COMMAND_GROUPS` routing |
| `artmind/setup.py` | Constraint + 3 indexes for `:EntityVersion` |
| `artmind/text2cypher.py` | `:EntityVersion` in the structural schema prompt |
| `artmind/skills/artmind-refine/SKILL.md` | Document the new commands |
| `docs/CAPABILITIES.md` | Section 4 rows |

**Task order rationale:** Tasks 1–3 are independent leaf pieces (store setup, reserved type, retirement) that later tasks build on. Tasks 4–7 build the history zone bottom-up (gate → capture → snapshot → wiring). Tasks 8–9 expose it for reading. Tasks 10–12 are the two unrelated fixes. Task 13 is docs.

---

## Task 1: Store setup for the history zone

Adds the constraint and indexes `:EntityVersion` needs. Nothing reads them yet — this is deliberately first so later tasks can assume they exist.

**Files:**
- Modify: `artmind/setup.py` (after line 180, the `conflict_status` index)
- Test: `test/test_entity_history.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_entity_history.py`:

```python
"""Entity history zone: setup, capture gate, snapshots, and version queries."""


def test_setup_creates_entity_version_constraint_and_indexes():
    """The history zone needs its own uniqueness constraint and lookup indexes.

    entity_id backs the anchor join, valid_to backs point-in-time filtering,
    and domain backs the same scoping every other label already has.
    """
    import inspect
    import artmind.setup as s

    src = inspect.getsource(s)
    assert "entity_version_id" in src
    assert "FOR (n:EntityVersion) REQUIRE n.id IS UNIQUE" in src
    assert "entity_version_entity" in src
    assert "ON (n.entity_id)" in src
    assert "entity_version_valid_to" in src
    assert "entity_version_domain" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: FAIL — `assert "entity_version_id" in src`

- [ ] **Step 3: Write minimal implementation**

In `artmind/setup.py`, immediately after the `conflict_status` index line (line 180), add:

```python
    # ── History zone (:EntityVersion) ─────────────────────────────────────────
    # Snapshots of overwritten entity property values. Deliberately NOT labelled
    # :Entity and carrying no class label, so no entity query, no vector index,
    # and no refine pass can reach them without asking explicitly.
    session.run(
        "CREATE CONSTRAINT entity_version_id IF NOT EXISTS "
        "FOR (n:EntityVersion) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE INDEX entity_version_entity IF NOT EXISTS FOR (n:EntityVersion) ON (n.entity_id)"
    )
    session.run(
        "CREATE INDEX entity_version_valid_to IF NOT EXISTS FOR (n:EntityVersion) ON (n.valid_to)"
    )
    session.run(
        "CREATE INDEX entity_version_domain IF NOT EXISTS FOR (n:EntityVersion) ON (n.domain)"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add artmind/setup.py test/test_entity_history.py
git commit -m "feat(history): add :EntityVersion constraint and indexes"
```

---

## Task 2: Reserve PRIOR_STATE against LLM extraction

`RESERVED_REL_TYPES` blocks LLM-extracted relationships from minting system-managed edge types with no provenance. `PRIOR_STATE` needs the same protection for the same reason.

**Files:**
- Modify: `artmind/ingest.py:742`
- Test: `test/test_reserved_relationships.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_reserved_relationships.py`:

```python
def test_prior_state_is_reserved():
    """History edges are system-managed, like SUPERSEDES and EXTRACTED_FROM.

    An LLM-extracted PRIOR_STATE edge would fabricate entity history carrying
    no snapshot node and no provenance — the same failure mode the existing
    reserved types guard against.
    """
    from artmind.ingest import RESERVED_REL_TYPES

    assert "PRIOR_STATE" in RESERVED_REL_TYPES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_reserved_relationships.py::test_prior_state_is_reserved -v`
Expected: FAIL — `assert 'PRIOR_STATE' in frozenset({'SUPERSEDES', 'EXTRACTED_FROM'})`

- [ ] **Step 3: Write minimal implementation**

In `artmind/ingest.py`, replace line 742:

```python
RESERVED_REL_TYPES = frozenset({"SUPERSEDES", "EXTRACTED_FROM", "PRIOR_STATE"})
```

And extend the comment block above it (after the `EXTRACTED_FROM` paragraph, before the `PART_OF` paragraph) with:

```python
# PRIOR_STATE is reserved on the same grounds: it links a live Entity to an
# :EntityVersion snapshot and is written only by artmind.entity_history. An
# LLM-minted one would imply history that no snapshot node backs.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_reserved_relationships.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_reserved_relationships.py
git commit -m "feat(history): reserve PRIOR_STATE against LLM extraction"
```

---

## Task 3: Retire entities the superseded document solely sourced

This is the fix for the original bug. Case 2 from the spec: the newer document drops an entity entirely, so it must stop reading as current.

The single-source condition (`size(docIds) = 1`) is what keeps cases 3 and 4 safe — an entity the newer document *also* asserts has two `doc_id`s by the time this runs, and an entity with an unrelated live source likewise.

**Files:**
- Modify: `artmind/temporal.py` (new function before `apply_supersession` at line 449; call site inside it)
- Test: `test/test_entity_retirement.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_entity_retirement.py`:

```python
"""Document supersession must retire entities the superseded document solely sourced."""

import artmind.temporal as t


class _Rec:
    def __init__(self, data):
        self._data = data

    def single(self):
        return self._data

    def data(self):
        return [self._data] if self._data else []


class FakeSession:
    """Records every Cypher statement and its parameters."""

    def __init__(self):
        self.runs = []

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec({"n": 1})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_retire_orphaned_entities_uses_single_source_guard():
    """Only entities whose entire evidence is the superseded document may retire.

    An entity the newer document re-asserts has EXTRACTED_FROM edges to both
    documents by the time this runs, and an entity with an unrelated live
    source likewise — both must survive, which the size(docIds) = 1 test is
    what enforces.
    """
    session = FakeSession()

    t._retire_orphaned_entities(session, "older-doc", "newer-doc", "2026-06-01")

    assert len(session.runs) == 1
    cypher, kwargs = session.runs[0]
    assert "size(docIds) = 1" in cypher
    assert "docIds[0] = $olderDocId" in cypher
    assert "coalesce(e.valid_to, $effective)" in cypher
    assert kwargs["olderDocId"] == "older-doc"
    assert kwargs["newerDocId"] == "newer-doc"
    assert kwargs["effective"] == "2026-06-01"


def test_retire_orphaned_entities_noop_without_effective_date():
    """Without an effective date there is no validity boundary to stamp.

    apply_supersession already tolerates a null effective for the document
    stamp (coalesce keeps the old value); retiring entities to a null valid_to
    would set nothing while still writing status, so skip entirely.
    """
    session = FakeSession()

    t._retire_orphaned_entities(session, "older-doc", "newer-doc", None)

    assert session.runs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_retirement.py -v`
Expected: FAIL — `AttributeError: module 'artmind.temporal' has no attribute '_retire_orphaned_entities'`

- [ ] **Step 3: Write minimal implementation**

In `artmind/temporal.py`, insert immediately before `def apply_supersession(` (line 449):

```python
def _retire_orphaned_entities(
    session, older_doc_id: str, newer_doc_id: str, effective: str | None
) -> None:
    """Stamp valid_to on entities the superseded document solely sourced.

    The counterpart to `_stamp_chunk_valid_from` for the entity layer, and the
    reason `--asOf` works on entity-oriented queries at all: `asof_predicate`
    is applied per node type, so `pattern1`/`pattern2`/`pattern9` filter on
    `Entity.valid_to` — a property nothing else ever sets from a *document*
    supersession.

    The single-source condition is the whole safety story. By the time this
    runs the newer document is already written, so an entity it re-asserts
    carries EXTRACTED_FROM edges to both documents and is left alone; so is an
    entity with any unrelated live source. Only entities whose entire evidence
    is the superseded document retire.

    Idempotent via coalesce. A null `effective` is a no-op: there is no
    boundary to stamp, and writing `status` alone would retire an entity that
    still reads as current to every as-of query.
    """
    if not effective:
        return
    session.run(
        """
        MATCH (e:Entity)-[:EXTRACTED_FROM]->(c:DocChunk)
        WITH e, collect(DISTINCT c.doc_id) AS docIds
        WHERE size(docIds) = 1 AND docIds[0] = $olderDocId
        SET e.valid_to      = coalesce(e.valid_to, $effective),
            e.superseded_by = $newerDocId,
            e.status        = 'superseded'
        """,
        olderDocId=older_doc_id,
        newerDocId=newer_doc_id,
        effective=effective,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_entity_retirement.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add artmind/temporal.py test/test_entity_retirement.py
git commit -m "feat(temporal): retire entities solely sourced from a superseded document"
```

---

## Task 4: Call retirement from apply_supersession

Wires Task 3 in. Placing the call inside `apply_supersession` means all three supersession routes — manual CLI, notice scan, conflict adjudicator — inherit it without each needing to remember.

**Files:**
- Modify: `artmind/temporal.py:449-479` (inside `apply_supersession`)
- Test: `test/test_entity_retirement.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_entity_retirement.py`:

```python
def test_apply_supersession_retires_entities_for_document_scope(monkeypatch):
    """All three supersession routes converge here, so retirement belongs here.

    Gated on document scope, matching the existing chunk stamp: a section- or
    clause-scoped supersession does not retire whole entities.
    """
    session = FakeSession()
    monkeypatch.setattr(t, "neo4j_session", lambda: session)
    calls = []
    monkeypatch.setattr(
        t, "_retire_orphaned_entities",
        lambda s, older, newer, eff: calls.append((older, newer, eff)),
    )

    t.apply_supersession("newer-doc", "older-doc", "document", "2026-06-01")

    assert calls == [("older-doc", "newer-doc", "2026-06-01")]


def test_apply_supersession_skips_retirement_for_non_document_scope(monkeypatch):
    """Sub-document scopes retire no entities — there is no sub-document unit."""
    session = FakeSession()
    monkeypatch.setattr(t, "neo4j_session", lambda: session)
    calls = []
    monkeypatch.setattr(
        t, "_retire_orphaned_entities",
        lambda s, older, newer, eff: calls.append((older, newer, eff)),
    )

    t.apply_supersession("newer-doc", "older-doc", "section", "2026-06-01")

    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_retirement.py -v`
Expected: FAIL — `assert [] == [('older-doc', 'newer-doc', '2026-06-01')]`

- [ ] **Step 3: Write minimal implementation**

In `artmind/temporal.py`, inside `apply_supersession`, replace this block:

```python
        if scope == "document" and effective:
            session.run(
                "MATCH (c:DocChunk {doc_id:$older}) SET c.valid_to = coalesce($effective, c.valid_to)",
                older=older_doc_id, effective=effective,
            )
```

with:

```python
        if scope == "document" and effective:
            session.run(
                "MATCH (c:DocChunk {doc_id:$older}) SET c.valid_to = coalesce($effective, c.valid_to)",
                older=older_doc_id, effective=effective,
            )
            _retire_orphaned_entities(session, older_doc_id, newer_doc_id, effective)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_entity_retirement.py test/test_supersession.py -v`
Expected: PASS — the 4 new tests plus every existing supersession test

- [ ] **Step 5: Commit**

```bash
git add artmind/temporal.py test/test_entity_retirement.py
git commit -m "feat(temporal): wire entity retirement into apply_supersession"
```

---

## Task 5: The capture gate

Capture is skipped entirely unless supersession could fire. The parse functions are pure regex over markdown already on disk, so the gate costs a file read and two regexes — and saves a Neo4j read on the vast majority of ingests.

**Files:**
- Create: `artmind/entity_history.py`
- Test: `test/test_entity_history.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_entity_history.py`:

```python
import artmind.entity_history as eh


def test_gate_closed_when_document_declares_no_supersession(monkeypatch):
    """The common case: no notice, no metadata row, no family flag — no read."""
    monkeypatch.setattr(eh, "_read_doc_body", lambda name: "# A policy\n\nSome prose.\n")
    monkeypatch.setattr(eh, "load_schema", lambda domain: {})

    assert eh.supersession_possible("doc.md", "banking.policy") is False


def test_gate_open_for_prose_notice(monkeypatch):
    monkeypatch.setattr(
        eh, "_read_doc_body",
        lambda name: "## Supersession Notice\n\nThis supersedes Version 2.0.\n",
    )
    monkeypatch.setattr(eh, "load_schema", lambda domain: {})

    assert eh.supersession_possible("doc.md", "banking.policy") is True


def test_gate_open_for_metadata_table_row(monkeypatch):
    monkeypatch.setattr(
        eh, "_read_doc_body",
        lambda name: "| Supersedes | [[older_doc]] |\n",
    )
    monkeypatch.setattr(eh, "load_schema", lambda domain: {})

    assert eh.supersession_possible("doc.md", "banking.reference") is True


def test_gate_open_when_schema_enables_title_family(monkeypatch):
    """Title-family inference needs no in-document signal, so it can't be gated."""
    monkeypatch.setattr(eh, "_read_doc_body", lambda name: "# Nothing special\n")
    monkeypatch.setattr(
        eh, "load_schema",
        lambda domain: {"temporal": {"defaults": {"supersede_on_title_family": True}}},
    )

    assert eh.supersession_possible("doc.md", "banking.reference") is True


def test_gate_closed_when_markdown_is_missing(monkeypatch):
    """A document with no markdown on disk can declare nothing."""
    monkeypatch.setattr(eh, "_read_doc_body", lambda name: None)
    monkeypatch.setattr(eh, "load_schema", lambda domain: {})

    assert eh.supersession_possible("doc.md", "banking.policy") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'artmind.entity_history'`

- [ ] **Step 3: Write minimal implementation**

Create `artmind/entity_history.py`:

```python
"""The entity history zone: snapshots of overwritten entity property values.

Document supersession retires entities wholesale (see temporal._retire_orphaned_entities).
This module handles the other half: when a superseding document *overwrites* an
entity's property values rather than dropping the entity, the prior values are
preserved as an :EntityVersion node so point-in-time questions stay answerable.

Snapshots deliberately carry neither the :Entity label nor a class label. Every
existing consumer — pattern1-9, entity_listing, entity-resolve, the
entity_embedding vector index, refine-graph clustering, candidate_pairs — matches
on :Entity or a class label, so none can see history without asking. That
isolation is structural, not a filter anyone has to remember.
"""
from artmind.temporal import (
    _read_doc_body,
    load_schema,
    parse_supersession_metadata_table,
    parse_supersession_notice,
)


def supersession_possible(doc_name: str, domain: str) -> bool:
    """Could supersession fire for this document? Pure local work.

    The parse step needs no graph access — both parsers are regex over markdown
    already on disk — so this runs before the (much more expensive) prior-value
    capture and skips it entirely for the overwhelming majority of documents,
    which declare no supersession at all.

    The title-family route is the one signal that lives outside the document, so
    a domain with `supersede_on_title_family` set always passes the gate. That
    flag is off by default and set only by schema authors who want version
    chains, so those domains genuinely expect supersession.
    """
    defaults = (load_schema(domain).get("temporal") or {}).get("defaults") or {}
    if defaults.get("supersede_on_title_family"):
        return True
    body = _read_doc_body(doc_name)
    if not body:
        return False
    return bool(parse_supersession_notice(body) or parse_supersession_metadata_table(body))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: PASS (6 tests — 1 from Task 1, 5 new)

- [ ] **Step 5: Commit**

```bash
git add artmind/entity_history.py test/test_entity_history.py
git commit -m "feat(history): gate prior-value capture on a pure-local supersession check"
```

---

## Task 6: Capture prior values

Reads the current values of exactly the keys this document will assert. One batched query, projecting only those keys — never `properties(n)`, which would drag the 768-float embedding per entity over the wire.

**Files:**
- Modify: `artmind/entity_history.py`
- Test: `test/test_entity_history.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_entity_history.py`:

```python
import json


class _CaptureResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class CaptureSession:
    def __init__(self, rows=None):
        self.runs = []
        self._rows = rows or []

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _CaptureResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _write_staged(tmp_path):
    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
        {"id": "c1_e2", "name": "No Props", "entity_class": "POLICY", "domain": "banking.policy"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text(json.dumps([
        {"id": "c1_e1", "properties": {"approval_limit": "£2,000"}},
    ]), encoding="utf-8")


def test_capture_projects_only_asserted_keys_never_all_properties(tmp_path, monkeypatch):
    """properties(n) would return the 768-float embedding for every entity.

    That is ~1.2MB for a 200-entity document on every gated commit, for data
    the snapshot never uses. Project the asserted keys instead.
    """
    _write_staged(tmp_path)
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    eh.capture_prior_values(tmp_path, "banking.policy")

    assert len(session.runs) == 1, "capture must be a single batched round trip"
    cypher, kwargs = session.runs[0]
    assert "properties(n)" not in cypher
    assert "embedding" not in cypher
    assert "UNWIND $rows AS r" in cypher
    assert "[k IN r.keys | [k, n[k]]]" in cypher
    # Only the entity that asserts properties is looked up.
    assert len(kwargs["rows"]) == 1
    assert kwargs["rows"][0]["name"] == "Fee Policy"
    assert kwargs["rows"][0]["keys"] == ["approval_limit"]


def test_capture_returns_prior_values_keyed_by_entity_identity(tmp_path, monkeypatch):
    _write_staged(tmp_path)
    session = CaptureSession(rows=[{
        "idx": 0,
        "id": "live-uuid-1",
        "vf": "2026-01-15",
        "prior": [["approval_limit", "£500"]],
    }])
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    out = eh.capture_prior_values(tmp_path, "banking.policy")

    key = ("Fee Policy", "POLICY", "banking.policy")
    assert out[key]["entity_id"] == "live-uuid-1"
    assert out[key]["valid_from"] == "2026-01-15"
    assert out[key]["values"] == {"approval_limit": "£500"}


def test_capture_returns_empty_for_brand_new_entity(tmp_path, monkeypatch):
    """An entity this document introduces has no pre-write node, so no snapshot.

    Correct by construction: there is no prior state to preserve.
    """
    _write_staged(tmp_path)
    session = CaptureSession(rows=[])
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    assert eh.capture_prior_values(tmp_path, "banking.policy") == {}


def test_capture_is_empty_when_no_entity_asserts_properties(tmp_path, monkeypatch):
    """No asserted properties means nothing can be overwritten — skip the query."""
    (tmp_path / "entities.json").write_text(json.dumps([
        {"id": "c1_e1", "name": "Fee Policy", "entity_class": "POLICY", "domain": "banking.policy"},
    ]), encoding="utf-8")
    (tmp_path / "properties.json").write_text("[]", encoding="utf-8")
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    assert eh.capture_prior_values(tmp_path, "banking.policy") == {}
    assert session.runs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: FAIL — `AttributeError: module 'artmind.entity_history' has no attribute 'capture_prior_values'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `artmind/entity_history.py`:

```python
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
```

Then append:

```python
_CAPTURE_CYPHER = """
UNWIND $rows AS r
MATCH (n:Entity {name: r.name, entity_class: r.ec, domain: r.domain})
RETURN r.idx AS idx, n.id AS id, n.valid_from AS vf, [k IN r.keys | [k, n[k]]] AS prior
"""


def _staged_assertions(doc_kg_dir: Path, domain: str) -> list[dict]:
    """The (identity, asserted property keys) pairs this document will write.

    Mirrors _reassert_superseding_properties' own scope: only the domain
    properties from properties.json. name/description/aliases/context stay
    accretive — that is consolidation's job, not history's.
    """
    try:
        entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
        properties_path = doc_kg_dir / "properties.json"
        properties_list = (
            json.loads(properties_path.read_text(encoding="utf-8"))
            if properties_path.exists() else []
        )
    except Exception as e:
        logger.warning("entity_history: could not load staged JSON from {}: {}", doc_kg_dir, e)
        return []

    props_by_id = {p["id"]: p.get("properties", {}) for p in properties_list}
    rows: list[dict] = []
    for e in entities:
        keys = sorted(k for k, v in props_by_id.get(e["id"], {}).items() if v not in (None, "", []))
        if not keys:
            continue
        rows.append({
            "idx": len(rows),
            "name": e["name"],
            "ec": e["entity_class"],
            "domain": e.get("domain") or domain,
            "keys": keys,
        })
    return rows


def capture_prior_values(doc_kg_dir: Path, domain: str) -> dict:
    """Read the live values of exactly the keys this document is about to assert.

    Must run BEFORE write_to_graph(). _upsert_entity's merge is accretive — two
    documents asserting the same string property produce "old | new" — so
    capturing after the write would record the concatenation rather than the
    clean prior value.

    Returns {(name, entity_class, domain): {entity_id, valid_from, values}}.
    An entity with no pre-write node is simply absent: nothing to preserve.
    """
    rows = _staged_assertions(doc_kg_dir, domain)
    if not rows:
        return {}
    with neo4j_session() as session:
        records = session.run(_CAPTURE_CYPHER, rows=rows).data()

    by_idx = {r["idx"]: r for r in rows}
    out: dict = {}
    for rec in records:
        row = by_idx.get(rec["idx"])
        if not row:
            continue
        out[(row["name"], row["ec"], row["domain"])] = {
            "entity_id": rec["id"],
            "valid_from": rec["vf"],
            "values": {k: v for k, v in (rec["prior"] or []) if v is not None},
        }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add artmind/entity_history.py test/test_entity_history.py
git commit -m "feat(history): capture prior entity values before the accretive write"
```

---

## Task 7: Write snapshots for changed values only

Compares captured prior values against what the document asserts, and writes an `:EntityVersion` for the differences. Identical values produce nothing — spec case 3.

**Files:**
- Modify: `artmind/entity_history.py`
- Test: `test/test_entity_history.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_entity_history.py`:

```python
def test_snapshot_written_only_for_changed_values(monkeypatch):
    """Case 1 writes history; case 3 (identical values) writes nothing."""
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    prior = {
        ("Fee Policy", "POLICY", "banking.policy"): {
            "entity_id": "live-1", "valid_from": "2026-01-15",
            "values": {"approval_limit": "£500", "owner": "Ops"},
        },
    }
    incoming = {
        ("Fee Policy", "POLICY", "banking.policy"): {"approval_limit": "£2,000", "owner": "Ops"},
    }

    written = eh.snapshot_changed_values(
        prior, incoming, effective="2026-06-01", newer_doc_id="doc-v3",
    )

    assert written == 1
    cypher, kwargs = session.runs[0]
    assert ":EntityVersion" in cypher
    assert "PRIOR_STATE" in cypher
    # Only the property that actually changed is preserved.
    assert kwargs["props"]["approval_limit"] == "£500"
    assert "owner" not in kwargs["props"]
    assert kwargs["validFrom"] == "2026-01-15"
    assert kwargs["validTo"] == "2026-06-01"
    assert kwargs["closedBy"] == "supersession"
    assert kwargs["supersededByDoc"] == "doc-v3"
    assert kwargs["entityId"] == "live-1"


def test_snapshot_skipped_when_nothing_changed(monkeypatch):
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    prior = {
        ("Fee Policy", "POLICY", "banking.policy"): {
            "entity_id": "live-1", "valid_from": None,
            "values": {"approval_limit": "£2,000"},
        },
    }
    incoming = {("Fee Policy", "POLICY", "banking.policy"): {"approval_limit": "£2,000"}}

    assert eh.snapshot_changed_values(prior, incoming, "2026-06-01", "doc-v3") == 0
    assert session.runs == []


def test_snapshot_preserves_the_clean_prior_value(monkeypatch):
    """Guards the Task 6 ordering contract: capture precedes the accretive write.

    If capture ever moves after write_to_graph, prior values arrive already
    merged as "old | new" and the history zone silently fills with
    concatenations instead of the values that were actually superseded. This
    asserts the clean value survives end to end.
    """
    session = CaptureSession()
    monkeypatch.setattr(eh, "neo4j_session", lambda: session)

    prior = {
        ("Fee Policy", "POLICY", "banking.policy"): {
            "entity_id": "live-1", "valid_from": None,
            "values": {"approval_limit": "£500"},
        },
    }
    incoming = {("Fee Policy", "POLICY", "banking.policy"): {"approval_limit": "£2,000"}}

    eh.snapshot_changed_values(prior, incoming, "2026-06-01", "doc-v3")

    _, kwargs = session.runs[0]
    assert kwargs["props"]["approval_limit"] == "£500"
    assert " | " not in kwargs["props"]["approval_limit"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: FAIL — `AttributeError: module 'artmind.entity_history' has no attribute 'snapshot_changed_values'`

- [ ] **Step 3: Write minimal implementation**

Append to `artmind/entity_history.py`:

```python
_SNAPSHOT_CYPHER = """
MATCH (e:Entity {id: $entityId})
CREATE (v:EntityVersion)
SET v = $props,
    v.id                = $versionId,
    v.entity_id         = $entityId,
    v.name              = $name,
    v.entity_class      = $entityClass,
    v.domain            = $domain,
    v.valid_from        = $validFrom,
    v.valid_to          = $validTo,
    v.closed_by         = $closedBy,
    v.superseded_by_doc = $supersededByDoc,
    v.snapshot_at       = $snapshotAt
CREATE (e)-[:PRIOR_STATE]->(v)
"""


def snapshot_changed_values(
    prior: dict,
    incoming: dict,
    effective: str | None,
    newer_doc_id: str,
) -> int:
    """Preserve the prior values of properties this document overwrites.

    Only genuinely changed keys are recorded — an entity re-asserted with
    identical values produces no snapshot, so the history zone holds real
    changes rather than noise.

    Snapshots record the graph's *then-current state*, not "what the older
    document claimed". That is the right answer for point-in-time questions and
    honest about the multi-document case, where a prior value may legitimately
    blend several still-live sources.
    """
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    pending: list[dict] = []
    for key, snap in prior.items():
        new_values = incoming.get(key) or {}
        changed = {
            k: v for k, v in (snap.get("values") or {}).items()
            if k in new_values and new_values[k] != v
        }
        if not changed:
            continue
        name, entity_class, domain = key
        pending.append({
            "props": changed,
            "versionId": uuid.uuid4().hex,
            "entityId": snap["entity_id"],
            "name": name,
            "entityClass": entity_class,
            "domain": domain,
            "validFrom": snap.get("valid_from"),
            "validTo": effective,
            "closedBy": "supersession",
            "supersededByDoc": newer_doc_id,
            "snapshotAt": now,
        })

    if not pending:
        return 0
    with neo4j_session() as session:
        for params in pending:
            session.run(_SNAPSHOT_CYPHER, **params)
            written += 1
    logger.info("entity_history: wrote {} snapshot(s) for {}", written, newer_doc_id)
    return written
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add artmind/entity_history.py test/test_entity_history.py
git commit -m "feat(history): snapshot overwritten entity values to :EntityVersion"
```

---

## Task 8: Wire capture and snapshotting into commit_to_graph

Connects Tasks 5–7 to the ingest pipeline. The gate runs first; capture happens before the write; snapshots are written only when supersession actually applied.

**Files:**
- Modify: `artmind/ingest.py:1564` (`_reassert_superseding_properties` signature) and `artmind/ingest.py:1613` (`commit_to_graph`)
- Test: `test/test_ingest_hooks.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_ingest_hooks.py`:

```python
def test_commit_to_graph_skips_capture_when_supersession_impossible(monkeypatch, tmp_path):
    """The gate saves a Neo4j read on the vast majority of ingests."""
    import json
    import artmind.ingest as ing
    import artmind.entity_history as eh
    import artmind.temporal as temporal

    (tmp_path / "document.json").write_text(
        json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8"
    )
    monkeypatch.setattr(ing, "write_to_graph", lambda p: True)
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: None)
    monkeypatch.setattr(temporal, "detect_supersession", lambda d, only_doc_name=None: {"applied": []})
    monkeypatch.setattr(eh, "supersession_possible", lambda name, domain: False)

    captured = []
    monkeypatch.setattr(eh, "capture_prior_values", lambda p, d: captured.append(1) or {})

    assert ing.commit_to_graph(tmp_path, "banking.policy") is True
    assert captured == [], "capture must not run when the gate is closed"


def test_commit_to_graph_captures_before_write_and_snapshots_after(monkeypatch, tmp_path):
    """Ordering is the whole correctness story: capture, then write, then snapshot.

    Capturing after write_to_graph would record _upsert_entity's accretive
    "old | new" concatenation instead of the clean prior value.
    """
    import json
    import artmind.ingest as ing
    import artmind.entity_history as eh
    import artmind.temporal as temporal

    (tmp_path / "document.json").write_text(
        json.dumps({"id": "d1", "name": "f.md"}), encoding="utf-8"
    )
    order = []
    monkeypatch.setattr(eh, "supersession_possible", lambda name, domain: True)
    monkeypatch.setattr(
        eh, "capture_prior_values",
        lambda p, d: order.append("capture") or {"k": {"entity_id": "e1"}},
    )
    monkeypatch.setattr(ing, "write_to_graph", lambda p: order.append("write") or True)
    monkeypatch.setattr(temporal, "normalize_ingested_document", lambda p, d: None)
    monkeypatch.setattr(
        temporal, "detect_supersession",
        lambda d, only_doc_name=None: {"applied": [{"newer": "d1", "older": "d0", "effective": "2026-06-01"}]},
    )
    monkeypatch.setattr(ing, "_reassert_superseding_properties", lambda *a, **k: {"entities_reasserted": 0})

    snapshots = []
    monkeypatch.setattr(
        eh, "snapshot_changed_values",
        lambda prior, incoming, effective, newer_doc_id: snapshots.append(
            (effective, newer_doc_id)
        ) or len(snapshots),
    )

    assert ing.commit_to_graph(tmp_path, "banking.policy") is True
    assert order == ["capture", "write"], f"wrong ordering: {order}"
    assert snapshots == [("2026-06-01", "d1")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_ingest_hooks.py -v`
Expected: FAIL — `assert ['write'] == ['capture', 'write']` (capture is never called)

- [ ] **Step 3: Write minimal implementation**

`_reassert_superseding_properties` is **not** modified — history capture belongs to the caller, so that function keeps its single job of re-asserting the superseding document's own values.

Replace `commit_to_graph` (line 1613) with:

```python
def commit_to_graph(doc_kg_dir: Path, domain: str) -> bool:
    """Complete commit of staged KG JSON to Neo4j: write, then the per-document
    self-asserted-truth hooks (temporal normalization, then supersession).

    This is the single convergence point for all three ingestion sources
    (extract, pull-from-repo, import-bundle). Cross-document judgment steps
    (merge/conflicts/consolidate) are deliberately NOT run here — see
    artmind.refine_pipeline. Hooks are best-effort: a down hook logs a warning
    but does not fail the commit, since the graph write already succeeded.
    """
    from artmind import entity_history

    # 0. Prior-value capture, for the entity history zone. Must precede the
    #    write: _upsert_entity's merge is accretive, so afterwards the live
    #    node holds "old | new" rather than the clean prior value. Gated on a
    #    pure-local check so documents that declare no supersession — the
    #    overwhelming majority — pay no Neo4j read at all.
    prior: dict = {}
    try:
        document_name = json.loads(
            (doc_kg_dir / "document.json").read_text(encoding="utf-8")
        ).get("name")
        if document_name and entity_history.supersession_possible(document_name, domain):
            prior = entity_history.capture_prior_values(doc_kg_dir, domain)
    except Exception as e:
        logger.warning("commit_to_graph: prior-value capture failed for {}: {}", doc_kg_dir, e)

    ok = write_to_graph(doc_kg_dir)
    if not ok:
        return False

    # 1. Temporal normalization (additive, idempotent, per-document).
    try:
        from artmind.temporal import normalize_ingested_document
        normalize_ingested_document(doc_kg_dir, domain)
    except Exception as e:
        logger.warning("commit_to_graph: temporal hook failed for {}: {}", doc_kg_dir, e)

    # 2. Supersession from this document's own declaration (must follow temporal
    #    so canonical dates/version exist). Scoped to just this document. When a
    #    SUPERSEDES edge was applied, snapshot the prior values this document
    #    overwrites, then re-assert its own values over the accretive merge —
    #    the superseding version's values win (see _reassert_superseding_properties).
    try:
        from artmind.temporal import detect_supersession
        document = json.loads((doc_kg_dir / "document.json").read_text(encoding="utf-8"))
        sup_report = detect_supersession(domain, only_doc_name=document.get("name"))
        applied = (sup_report or {}).get("applied")
        if applied:
            if prior:
                try:
                    entity_history.snapshot_changed_values(
                        prior,
                        _incoming_property_values(doc_kg_dir, domain),
                        applied[0].get("effective"),
                        document.get("id"),
                    )
                except Exception as e:
                    logger.warning("commit_to_graph: history snapshot failed for {}: {}", doc_kg_dir, e)
            _reassert_superseding_properties(doc_kg_dir, domain)
    except Exception as e:
        logger.warning("commit_to_graph: supersession hook failed for {}: {}", doc_kg_dir, e)

    return True
```

And add this helper immediately above `commit_to_graph`:

```python
def _incoming_property_values(doc_kg_dir: Path, domain: str) -> dict:
    """The property values this document asserts, keyed by entity identity.

    The comparison side of the history snapshot: capture_prior_values supplies
    what the graph held, this supplies what the document says, and only the
    differences become :EntityVersion nodes.
    """
    try:
        entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
        properties_path = doc_kg_dir / "properties.json"
        properties_list = (
            json.loads(properties_path.read_text(encoding="utf-8"))
            if properties_path.exists() else []
        )
    except Exception as e:
        logger.warning("_incoming_property_values: could not load staged JSON: {}", e)
        return {}
    props_by_id = {p["id"]: p.get("properties", {}) for p in properties_list}
    out: dict = {}
    for e in entities:
        props = _flatten_props(props_by_id.get(e["id"], {}))
        if props:
            out[(e["name"], e["entity_class"], e.get("domain") or domain)] = props
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_ingest_hooks.py -v`
Expected: PASS — the 2 new tests plus all 11 existing hook tests

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_ingest_hooks.py
git commit -m "feat(history): capture prior values and snapshot on supersession commit"
```

---

## Task 9: The entity-versions query

Exposes the history zone for reading. Without `--asOf`, full history; with it, the state current on that date.

**Files:**
- Modify: `artmind/graph_query.py` (after `list_timeline`, line 1053)
- Test: `test/test_entity_history.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_entity_history.py`:

```python
def test_entity_versions_cypher_scopes_by_domain_and_orders_by_valid_from(monkeypatch):
    import artmind.graph_query as gq

    seen = {}
    monkeypatch.setattr(
        gq, "_run_read_query",
        lambda cypher, params: seen.update({"cypher": cypher, "params": params}) or [],
    )

    out = gq.entity_versions(["banking.policy"], "live-1")

    assert out["command"] == "entity_versions"
    assert ":EntityVersion" in seen["cypher"]
    assert "v.entity_id = $entityId" in seen["cypher"]
    assert "ORDER BY v.valid_from" in seen["cypher"]
    assert seen["params"]["entityId"] == "live-1"


def test_entity_versions_asof_selects_the_covering_snapshot(monkeypatch):
    """With --asOf, return the state in force on that date, not the whole chain."""
    import artmind.graph_query as gq

    seen = {}
    monkeypatch.setattr(
        gq, "_run_read_query",
        lambda cypher, params: seen.update({"cypher": cypher, "params": params}) or [],
    )

    gq.entity_versions(["banking.policy"], "live-1", as_of="2026-03-01")

    assert "$asOf" in seen["cypher"]
    assert seen["params"]["asOf"] == "2026-03-01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: FAIL — `AttributeError: module 'artmind.graph_query' has no attribute 'entity_versions'`

- [ ] **Step 3: Write minimal implementation**

Append to `artmind/graph_query.py` after `list_timeline`:

```python
def entity_versions(
    domains: "str | Sequence[str]",
    entity_id: str,
    as_of: str | None = None,
) -> dict:
    """An entity's prior states from the history zone.

    Without as_of, the full chain oldest-first. With as_of, only the snapshot
    whose validity window covers that date — the state in force then. An empty
    result with as_of set means no snapshot covers the date, so the live entity
    was already current: callers fall back to the entity itself.

    Deliberately a separate command rather than making pattern2 --asOf return
    historical values: that would silently change the meaning of the most-used
    pattern (see pattern10's asOf_ignored for the precedent this follows).
    """
    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    asof_clause = ""
    params: dict = {"domains": domains, "entityId": entity_id}
    if as_of:
        asof_clause = (
            "\n      AND (v.valid_from IS NULL OR v.valid_from <= $asOf)"
            "\n      AND (v.valid_to IS NULL OR v.valid_to > $asOf)"
        )
        params["asOf"] = as_of
    cypher = f"""
    MATCH (v:EntityVersion)
    WHERE v.entity_id = $entityId
      AND {domain_predicate("v")}{asof_clause}
    OPTIONAL MATCH (e:Entity {{id: $entityId}})
    RETURN v AS version, e {{ .id, .name, .entity_class, .valid_from, .valid_to }} AS entity
    ORDER BY v.valid_from
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "entity_versions",
        "entity_id": entity_id,
        **({"asOf": as_of} if as_of else {}),
        "rows": strip_embeddings(_run_read_query(cypher, params)),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_query.py test/test_entity_history.py
git commit -m "feat(query): add entity-versions read over the history zone"
```

---

## Task 10: CLI — entity-versions command and scope rejection

Two CLI changes: expose Task 9, and make `ingest supersede --scope section|clause` fail loudly instead of half-applying.

**Files:**
- Modify: `artmind/cli.py:1084` (scope rejection), `artmind/cli.py:1903` (new command), `artmind/cli.py:204` (`COMMAND_GROUPS`)
- Test: `test/test_entity_history.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_entity_history.py`:

```python
from click.testing import CliRunner


def test_supersede_rejects_non_document_scope():
    """Sub-document scopes currently half-apply: the document retires but its
    chunks stay live. There is no sub-document unit in the graph to scope to,
    so fail loudly rather than produce inconsistent state.
    """
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, [
        "ingest", "supersede", "--domain", "banking.policy",
        "--newer", "v3.md", "--older", "v2.md", "--scope", "clause",
    ])

    assert result.exit_code != 0
    assert "not yet supported" in result.output.lower()


def test_entity_versions_command_is_registered():
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["query", "graph", "entity-versions", "--help"])

    assert result.exit_code == 0, result.output
    assert "--entityId" in result.output
    assert "--asOf" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: FAIL — the supersede test gets `exit_code == 1` from a Neo4j connection error rather than the scope message, and `entity-versions --help` returns a "No such command" error

- [ ] **Step 3: Write minimal implementation**

**(a)** In `artmind/cli.py`, inside `ingest_supersede` (line 1084), insert as the very first statement of the function body, immediately after the docstring:

```python
    if scope != "document":
        raise click.ClickException(
            f"--scope {scope} is not yet supported. Sub-document supersession needs "
            "section/clause units the graph does not model, and applying it today "
            "would retire the whole document while leaving its chunks live. "
            "Use --scope document."
        )
```

**(b)** After `graph_timeline` (line 1903), add:

```python
@graph.command("entity-versions")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain(s)")
@click.option("--entityId", "entity_id", required=True, help="Entity id whose prior states to list")
@click.option("--asOf", "as_of", default=None, help="Return the state in force on this ISO date (omit for the full chain)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_entity_versions(domain: tuple, entity_id: str, as_of: str | None, compact: bool) -> None:
    """Prior states of an entity from the history zone (superseded property values)."""
    domains = _parse_domains(domain)
    try:
        result = graph_query.entity_versions(domains, entity_id, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
```

**(c)** `entity-versions` sits under the already-routed `graph` group, so `COMMAND_GROUPS` needs no change. Confirm this in Step 4 via the guide test.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_entity_history.py test/test_cli_guide.py -v`
Expected: PASS — including `test_every_command_is_routed_into_the_guide`

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_entity_history.py
git commit -m "feat(cli): add query graph entity-versions; reject unsupported supersede scopes"
```

---

## Task 11: Conflict resolution

`materialize()` only ever writes `status='open'`, so the `resolved`/`dismissed` filter on `query graph conflicts` selects over a state nothing can produce. This adds the missing transition — explicit only.

**Files:**
- Modify: `artmind/conflicts.py`, `artmind/cli.py` (new command + `COMMAND_GROUPS` line 193)
- Test: `test/test_conflict_resolution.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_conflict_resolution.py`:

```python
"""Conflict status transitions — the missing half of query graph conflicts --status."""

from click.testing import CliRunner

import artmind.conflicts as c


class _Rec:
    def __init__(self, data):
        self._data = data

    def single(self):
        return self._data


class FakeSession:
    def __init__(self, found=True):
        self.runs = []
        self._found = found

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec({"id": "abc123", "status": kwargs.get("status")} if self._found else None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_resolve_conflict_sets_status_and_provenance(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    out = c.resolve_conflict("abc123", "resolved", reason="Policy v3 settled it")

    assert out["id"] == "abc123"
    assert out["status"] == "resolved"
    cypher, kwargs = session.runs[0]
    assert "co.status = $status" in cypher
    assert "co.resolved_at" in cypher
    assert kwargs["status"] == "resolved"
    assert kwargs["reason"] == "Policy v3 settled it"


def test_resolve_conflict_raises_on_unknown_id(monkeypatch):
    """A no-match must fail loudly: silently succeeding would let an operator
    believe they closed a conflict that is still open. Orphaned CONFLICTS_WITH
    edges have no Conflict node and so cannot carry status at all.
    """
    import pytest

    session = FakeSession(found=False)
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    with pytest.raises(ValueError, match="abc123"):
        c.resolve_conflict("abc123", "dismissed")


def test_resolve_conflict_rejects_unknown_status(monkeypatch):
    import pytest

    monkeypatch.setattr(c, "neo4j_session", lambda: FakeSession())

    with pytest.raises(ValueError, match="status"):
        c.resolve_conflict("abc123", "banana")


def test_resolve_conflict_cli_reports_unknown_id(monkeypatch):
    import artmind.cli as cli

    monkeypatch.setattr(c, "neo4j_session", lambda: FakeSession(found=False))

    result = CliRunner().invoke(cli.cli, ["ingest", "resolve-conflict", "abc123", "--status", "resolved"])

    assert result.exit_code != 0
    assert "abc123" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_conflict_resolution.py -v`
Expected: FAIL — `AttributeError: module 'artmind.conflicts' has no attribute 'resolve_conflict'`

- [ ] **Step 3: Write minimal implementation**

**(a)** Append to `artmind/conflicts.py`:

```python
RESOLUTION_STATUSES = ("resolved", "dismissed")


def resolve_conflict(conflict_id: str, status: str, reason: str | None = None) -> dict:
    """Close a materialized conflict as resolved or dismissed.

    Detection is deliberately one-way: materialize() only ever creates
    conflicts with status 'open', and nothing closes them automatically. A
    conflict represents two authorities genuinely disagreeing, which a
    re-detection pass cannot adjudicate — closing it is a human judgment, so it
    is an explicit command and never a side effect.

    Raises ValueError when the id matches no Conflict node. That includes the
    orphaned-edge case: a CONFLICTS_WITH edge whose Conflict node was deleted
    still surfaces in list_conflicts (reported as 'open' via coalesce) but has
    nowhere to record a status.
    """
    if status not in RESOLUTION_STATUSES:
        raise ValueError(
            f"status must be one of {', '.join(RESOLUTION_STATUSES)}; got {status!r}"
        )
    with neo4j_session() as session:
        rec = session.run(
            """
            MATCH (co:Conflict {id: $id})
            SET co.status = $status,
                co.resolved_at = $now,
                co.resolution_reason = $reason
            RETURN co.id AS id, co.status AS status
            """,
            id=conflict_id,
            status=status,
            now=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        ).single()
    if not rec:
        raise ValueError(
            f"No Conflict node with id {conflict_id!r}. If `query graph conflicts` "
            "listed it, the row may come from an orphaned CONFLICTS_WITH edge whose "
            "Conflict node no longer exists — such rows report as 'open' but carry no status."
        )
    logger.info("conflict {} → {} ({})", conflict_id, status, reason or "no reason given")
    return {"id": rec["id"], "status": rec["status"], "reason": reason}
```

**(b)** In `artmind/cli.py`, after `ingest_detect_conflicts` (which ends at line 1074), add:

```python
@ingest.command("resolve-conflict")
@click.argument("conflict_id")
@click.option("--status", type=click.Choice(["resolved", "dismissed"]), required=True, help="How this conflict was closed")
@click.option("--reason", default=None, help="Why — recorded on the Conflict node for audit")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_resolve_conflict(conflict_id: str, status: str, reason: str | None, compact: bool) -> None:
    """Close a materialized conflict as resolved or dismissed.

    Detection never closes conflicts on its own — two authorities disagreeing is
    a human judgment call. Use `query graph conflicts --status all` to see closed
    ones afterwards.
    """
    _setup_logger()
    from artmind.conflicts import resolve_conflict
    try:
        result = resolve_conflict(conflict_id, status, reason=reason)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
```

**(c)** In `artmind/cli.py`, add `"resolve-conflict"` to the Refinement group list (line 191, after `"detect-conflicts"`):

```python
                "detect-conflicts",
                "resolve-conflict",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_conflict_resolution.py test/test_cli_guide.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add artmind/conflicts.py artmind/cli.py test/test_conflict_resolution.py
git commit -m "feat(conflicts): add explicit resolve/dismiss status transitions"
```

---

## Task 12: Hierarchical domain rollup

Closes the two `TODO(hierarchical-domains)` sites. Children are derived from the graph, not the schema directory — no filesystem dependency, and it returns exactly the domains holding data.

**Files:**
- Modify: `artmind/graph_query.py` (after `domain_predicate`, line 59), `artmind/temporal.py:222` (`normalize_time`), `artmind/conflicts.py:46` (`candidate_pairs`)
- Test: `test/test_domain_family.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_domain_family.py`:

```python
"""Hierarchical domain rollup for the two non-query paths that lacked it."""

import artmind.graph_query as gq


class _Rec:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self.runs = []
        self._rows = rows

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_expand_domain_family_includes_parent_and_children(monkeypatch):
    monkeypatch.setattr(
        gq, "neo4j_session",
        lambda: FakeSession([{"dom": "banking.policy"}, {"dom": "banking.cases"}]),
    )

    assert gq.expand_domain_family("banking") == ["banking", "banking.cases", "banking.policy"]


def test_expand_domain_family_avoids_an_unlabelled_full_graph_scan(monkeypatch):
    """MATCH (n) with no label scans every node in the database.

    Document and Entity both carry domain indexes; restricting to them keeps
    the lookup index-backed.
    """
    session = FakeSession([])
    monkeypatch.setattr(gq, "neo4j_session", lambda: session)

    gq.expand_domain_family("banking")

    cypher = session.runs[0][0]
    assert "MATCH (n)" not in cypher
    assert ":Document" in cypher and ":Entity" in cypher


def test_expand_domain_family_leaves_childless_domain_unchanged(monkeypatch):
    monkeypatch.setattr(gq, "neo4j_session", lambda: FakeSession([]))

    assert gq.expand_domain_family("banking.policy") == ["banking.policy"]


def test_normalize_time_loops_every_concrete_child(monkeypatch):
    """A parent-scoped run previously touched only nodes stamped exactly 'banking'
    — normally none, since abstract parents hold no documents.
    """
    import artmind.temporal as t

    monkeypatch.setattr(t, "expand_domain_family", lambda d: ["banking", "banking.policy"])
    seen = []

    def fake_one(domain, dry_run=False):
        seen.append(domain)
        return {"domain": domain, "documents": 1, "entities": 2,
                "deterministic": 3, "llm": 0, "dry_run": dry_run}

    monkeypatch.setattr(t, "_normalize_time_one_domain", fake_one)

    out = t.normalize_time("banking", dry_run=False)

    assert seen == ["banking", "banking.policy"]
    assert out["documents"] == 2
    assert out["entities"] == 4
    assert out["deterministic"] == 6
    assert out["domains_processed"] == ["banking", "banking.policy"]
    assert out["domain"] == "banking"


def test_detect_conflicts_expands_domains_before_pairing(monkeypatch):
    """--domain banking must mean cross-child conflicts within the family."""
    import artmind.conflicts as c

    monkeypatch.setattr(c, "expand_domain_family", lambda d: {
        "banking": ["banking", "banking.policy", "banking.cases"]
    }[d])
    monkeypatch.setattr(c, "check_refine_precondition", lambda s, d: [])

    seen = {}
    monkeypatch.setattr(
        c, "candidate_pairs",
        lambda domains, nf, st, mp: seen.update({"domains": domains}) or [],
    )

    class _S:
        def run(self, *a, **k):
            class R:
                def single(self_inner):
                    return {"done": []}
            return R()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(c, "neo4j_session", lambda: _S())

    report = c.detect_conflicts(domains=["banking"], dry_run=True)

    assert seen["domains"] == ["banking", "banking.policy", "banking.cases"]
    assert report["domains_requested"] == ["banking"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_domain_family.py -v`
Expected: FAIL — `AttributeError: module 'artmind.graph_query' has no attribute 'expand_domain_family'`

- [ ] **Step 3: Write minimal implementation**

**(a)** In `artmind/graph_query.py`, after `domain_predicate` (line 59), add:

```python
def expand_domain_family(domain: str) -> list[str]:
    """A domain plus every descendant domain that actually holds data.

    Retrieval paths get hierarchy free via `domain_predicate`'s STARTS WITH
    rollup, but two write/analysis paths need a *concrete list* rather than a
    predicate: normalize_time loads a schema per domain, and candidate_pairs
    restricts ANN neighbours to specific other domains. Both matched `domain`
    exactly before this, so a parent-scoped run silently did nothing.

    Children are derived from the graph rather than the schema directory: no
    filesystem dependency, no cli import, and the result is exactly the domains
    holding data.

    Restricted to :Document and :Entity — both carry domain indexes, so the
    STARTS WITH stays index-backed. An unlabelled MATCH (n) would scan every
    node in the database, including the history zone.
    """
    with neo4j_session() as session:
        rows = session.run(
            """
            CALL () {
              MATCH (d:Document) WHERE d.domain STARTS WITH ($d + '.')
              RETURN DISTINCT d.domain AS dom
            UNION
              MATCH (e:Entity) WHERE e.domain STARTS WITH ($d + '.')
              RETURN DISTINCT e.domain AS dom
            }
            RETURN dom
            """,
            d=domain,
        ).data()
    return [domain] + sorted({r["dom"] for r in rows if r.get("dom")})
```

**(b)** In `artmind/temporal.py`, add the import at the top (alongside the existing `from artmind.graph_query import neo4j_session`):

```python
from artmind.graph_query import expand_domain_family, neo4j_session
```

Rename the existing `normalize_time` function to `_normalize_time_one_domain` (change only the `def` line, leaving the body and its docstring intact but removing the now-obsolete `TODO(hierarchical-domains)` paragraph from the docstring), then add a new `normalize_time` immediately after it:

```python
def normalize_time(domain: str, dry_run: bool = False) -> dict:
    """Backfill canonical temporal properties across a domain family.

    A parent domain fans out to every concrete child holding data, so each
    child's own schema (and therefore its own temporal mappings) loads. The
    return shape stays flat with summed counts so existing consumers —
    refine_pipeline's report, skills/artmind-refine's summarize_gates.py —
    keep working; `domains_processed` is additive.
    """
    domains = expand_domain_family(domain)
    totals = {"domain": domain, "documents": 0, "entities": 0,
              "deterministic": 0, "llm": 0, "dry_run": dry_run,
              "domains_processed": domains}
    for d in domains:
        one = _normalize_time_one_domain(d, dry_run=dry_run)
        for key in ("documents", "entities", "deterministic", "llm"):
            totals[key] += one.get(key, 0)
    return totals
```

**(c)** In `artmind/conflicts.py`, change the import line:

```python
from artmind.graph_query import expand_domain_family, neo4j_session
```

Delete the `TODO(hierarchical-domains)` comment block inside `candidate_pairs` (lines 62–71) and replace it with:

```python
    # `domains` arrives already expanded by detect_conflicts (see
    # expand_domain_family), so a parent like `banking` reaches its concrete
    # children here and cross-child pairing works as intended.
```

Then in `detect_conflicts`, immediately after the `report` dict is built, insert:

```python
    requested = list(domains)
    domains = []
    for d in requested:
        for expanded in expand_domain_family(d):
            if expanded not in domains:
                domains.append(expanded)
    report["domains_requested"] = requested
    report["domains"] = domains
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --group dev pytest test/test_domain_family.py test/test_conflicts.py test/test_temporal.py test/test_refine_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_query.py artmind/temporal.py artmind/conflicts.py test/test_domain_family.py
git commit -m "fix(domains): roll parent domains up to concrete children in normalize-time and conflict pairing"
```

---

## Task 13: Documentation

The text2cypher schema prompt is functional, not cosmetic: without it, generated Cypher neither knows `:EntityVersion` exists nor knows to exclude it.

**Files:**
- Modify: `artmind/text2cypher.py:88-93`, `artmind/skills/artmind-refine/SKILL.md`, `docs/CAPABILITIES.md`
- Test: `test/test_entity_history.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_entity_history.py`:

```python
def test_text2cypher_schema_documents_the_history_zone():
    """Generated Cypher must know :EntityVersion exists — and that live entity
    questions should not traverse into it.
    """
    from artmind.text2cypher import STRUCTURAL_SCHEMA

    assert ":EntityVersion" in STRUCTURAL_SCHEMA
    assert "PRIOR_STATE" in STRUCTURAL_SCHEMA
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --group dev pytest test/test_entity_history.py -v`
Expected: FAIL — `assert ':EntityVersion' in ...`

- [ ] **Step 3: Write the documentation**

**(a)** In `artmind/text2cypher.py`, after the `SUPERSEDES` line (line 92), add:

```
  Node :EntityVersion properties=[id, entity_id, name, entity_class, domain, valid_from, valid_to, closed_by, superseded_by_doc]
  Relationship (:Entity)-[:PRIOR_STATE]->(:EntityVersion) — a superseded snapshot of that entity's values
    (history only — never traverse into it for "what is true now" questions; the
     live :Entity node always holds current values)
```

**(b)** In `artmind/skills/artmind-refine/SKILL.md`, under "Known Caveats", replace the caveat reading `Re-running detect-conflicts is not a guaranteed no-op...` by keeping it and adding after the list:

```markdown
## Closing a conflict

Detection never closes conflicts — two authorities disagreeing is a human
judgment. Once you have adjudicated one:

```bash
artmind ingest resolve-conflict <conflict_id> --status resolved --reason "<why>"
```

Use `--status dismissed` for a false positive. `query graph conflicts --status all`
shows closed ones afterwards.

## Reading superseded entity values

When a document supersedes another and overwrites entity properties, the prior
values are preserved:

```bash
artmind query graph entity-versions --domain <d> --entityId <id> --compact
artmind query graph entity-versions --domain <d> --entityId <id> --asOf 2026-03-01 --compact
```

Entities the newer document drops entirely are retired instead (`valid_to` set),
so `--asOf today` stops returning them.
```

**(c)** In `docs/CAPABILITIES.md`, update section 4's table: mark row 4.7's Statement to note entity-level effects, and add two rows after 4.7:

```markdown
| 4.8 |  | Entity retirement on supersession | When a document is superseded, entities it solely sourced stop being returned as current by point-in-time queries, while entities still asserted by live documents are unaffected. | `_retire_orphaned_entities` (`temporal.py`) |
| 4.9 |  | Superseded-value history | Property values a superseding document overwrites are preserved in a queryable history partition that is invisible to ordinary entity queries and semantic search. | `entity_history.py`, `artmind query graph entity-versions` |
| 4.10 |  | Conflict resolution | A detected conflict can be explicitly closed as resolved or dismissed, with the reason recorded; closure is never automatic. | `artmind ingest resolve-conflict` |
```

Also add to the mindmap under `4 Graph Refinement`:

```
      Entity retirement
      Superseded-value history
```

- [ ] **Step 4: Run the full suite**

Run: `just dev-test`
Expected: PASS — all tests, including the new ones

- [ ] **Step 5: Commit**

```bash
git add artmind/text2cypher.py artmind/skills/artmind-refine/SKILL.md docs/CAPABILITIES.md test/test_entity_history.py
git commit -m "docs: document the history zone, entity retirement, and conflict resolution"
```

---

## Task 14: End-to-end verification against a real graph

Green tests do not prove the CLI works — the suite is hermetic and bypasses the entry-point proxy entirely. This task is manual and requires a running Neo4j.

**Files:** none (verification only)

- [ ] **Step 1: Reinstall and re-seed**

```bash
just dev-install
```

This stops daemons, reinstalls editable, and runs `artmind init` (which re-seeds the run folder's skill copies — the chat UI reads those, not the checkout).

- [ ] **Step 2: Apply the new store setup**

```bash
ARTMIND_NO_PROXY=1 artmind setup
```

Expected: completes without error, creating the `:EntityVersion` constraint and indexes.

- [ ] **Step 3: Verify the CLI surface exists**

```bash
ARTMIND_NO_PROXY=1 artmind query graph entity-versions --help
```

Expected: help text showing `--entityId` and `--asOf`.

`ARTMIND_NO_PROXY=1` matters: without it a running `serve` daemon answers from the code it imported at startup, so a missing command would appear present (or vice versa).

- [ ] **Step 4: Verify scope rejection**

```bash
ARTMIND_NO_PROXY=1 artmind ingest supersede --domain banking.policy --newer a.md --older b.md --scope clause
```

Expected: exits non-zero with the "not yet supported" message — **before** any Neo4j lookup.

- [ ] **Step 5: Verify retirement on the real corpus**

Pick a domain with a known supersession pair, then:

```bash
ARTMIND_NO_PROXY=1 artmind ingest detect-supersession --domain banking.policy --compact
ARTMIND_NO_PROXY=1 artmind query graph pattern1 --domain banking.policy --entityClass POLICY --asOf today --compact
```

Expected: entities sourced solely from the superseded document no longer appear in the `--asOf today` result, while entities from live documents still do. Compare against the same `pattern1` call without `--asOf` — the superseded ones should reappear there.

- [ ] **Step 6: Commit nothing, report findings**

If anything diverges from expectations, that is a real bug the hermetic suite could not catch. Report it rather than patching blind.

---

## Self-review notes

**Spec coverage** — every spec section maps to a task: §4.2 → Task 1; §4.1 `PRIOR_STATE` → Task 2; §6 → Tasks 3–4; §5.2 → Task 5; §5.1/§5.3 → Task 6; §5.5/case 3 → Task 7; §5.1 wiring → Task 8; §7 → Tasks 9–10; §8 → Task 10; §9 → Task 11; §10 → Task 12; §7 text2cypher + §11 docs → Task 13; CLAUDE.md's stale-daemon warning → Task 14.

**Deliberately deferred (spec §13):** full Entity-State/ESR migration, transaction time, sub-document scopes, `pattern2 --asOf` returning historical values, rollup for `refine_graph`/`detect_supersession`, skipping retired entities in consolidate/refine, and snapshots for natural expiry.

**Naming consistency:** `capture_prior_values`, `snapshot_changed_values`, `supersession_possible`, `_retire_orphaned_entities`, `expand_domain_family`, `resolve_conflict`, `entity_versions`, `_normalize_time_one_domain`, `_incoming_property_values` — each defined once and referenced by that exact name everywhere after.
