# Vault sync — Plan C2 (phase 4, continued: every curation write travels) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The four remaining graph- or registry-only curation writes — supersessions, document retirements, LLM syntheses, and structured-table classifications — reach a machine with its own Neo4j through `artmind vault sync`, idempotently, fingerprint-skipped on a shared graph, counted while pending, and with dry-run parity.

**Architecture:** Builds on Plan C (`2026-09-28-vault-sync-plan-c-curation.md`), which **must be merged first**: its curation-record framework (`artmind/curation_records.py` — one JSON file per record under `.artmind/data/curation/<kind>/`, a `Kind` per record type, and `vault sync`'s track C that applies, removes, fingerprint-skips and counts every registered kind). This plan adds three kinds — `supersessions`, `lifecycle`, `syntheses` — each a small module owning its record shape and Cypher, and makes the structured store's curation commands re-export the table's `.meta.json`, which track B then applies cheaply. Spec [2026-09-26-vault-git-transport-design.md](../specs/2026-09-26-vault-git-transport-design.md) §7, D6; this plan adds §15 A10.

**Verified:** every code block in this plan was applied, in task order, by a script that parses this file, to a scratch worktree of Plan C's final commit (branch `planC-applied`), and the full suite was run after each task — see "Verification record" at the end. The "Expected: FAIL" claims come from reverting each task's non-test files and re-running its tests.

**Tech Stack:** Python 3.14, Click (rich-click), neo4j driver, DuckDB/SQLite (structured store), pytest (`uv run --group dev pytest test/ -q`, ~70 s, `2519 passed, 14 skipped` after Plan C), real throwaway git repos in `tmp_path`, recording fakes for every Neo4j seam.

---

## Context for the implementer

Read before starting:

- `CLAUDE.md`, "Testing implications" §1–§4 (§3: assert on the Cypher and parameters sent, never on counts).
- Plan C, especially its Task 3 (`curation_records`, `Kind`, `conflict_records` as the model every kind here follows) and Task 5 (track C in `vault_sync.sync`: `"pre"` kinds apply before the union rebuild and their returned keys join it; `"post"` kinds after it; removals receive the record as it existed at the sync's base).
- `artmind/temporal.py` `apply_supersession`, `artmind/lifecycle.py`, `artmind/synthesize.py` `synthesize_key`, `artmind/projection.py` `load_synthesis_batch`, `artmind/structured/text_export.py`, `artmind/structured/pipeline.py` `_export_text_best_effort`/`_project_catalogue_best_effort`, and the `db grain`/`db mappings`/`db bridge`/`db propose` commands in `artmind/cli.py`.
- `test/test_vault_sync.py`'s helpers from Plans A–C, reused by name: `repo`, `_commit_all`, `_write_doc_folder`, `_patch_kg_dir`, `_patch_structured_text_dir`, `_patch_curation_dir`, `_patch_ingest_and_projection`, `_patch_track_b`, the `curation_graph` fixture (a recording graph session that answers `record_fingerprint` reads), `_CurationGraph`, `_lifecycle` (added in Task 2).

**Line numbers** are those of each file as it stands when the task starts. Every replacement quotes the exact old code: match on the text, not the number.

Run the suite with `uv run --group dev pytest test/ -q`. Do **not** run `just dev-install`, `artmind init` outside `tmp_path`, or anything against a live Neo4j.

## Decisions this plan makes (the spec left them open)

Argued in full under "Self-review → Design decisions the spec did not settle" (numbers in brackets).

1. **A supersession is a record keyed by `sha1(newer|older|scope)`** [1], with no timestamp so re-detection rewrites nothing. **`older.valid_to` and `older.superseded_by` are derived from every `SUPERSEDES` edge still pointing at the older document** — `superseded_by` from the edge with the latest `effective` (ties by newer id), `valid_to` that latest non-null `effective`, both null when no edge remains — recomputed after every apply and every removal. So un-asserting (deleting the file) restores exactly what the remaining assertions say, on every machine, in any order. This is exact because only supersession writes those two properties (a document's own window is `_valid_from`/`_valid_to`). It also changes one behaviour: with two supersessions of one document, the latest `effective` now wins instead of the last one applied.
2. **A retirement is a record that exists exactly while the document is retired** [2]: `docs retire` (and a document-scoped supersession, and archive restore, which call it) writes `.artmind/data/curation/lifecycle/<sha1(doc_id)>.json`; `docs restore` deletes it.
3. **A retirement is sticky** [3]: re-ingesting a retired document, or `vault sync` replaying it, commits the new version and moves it straight back to history in the same transaction (`_commit_document_tx` step 5b, driven by the working tree's record), and `vault sync` re-applies the record committed at HEAD after replaying the document (`Kind.reapply_for`). Only `docs restore` brings it back. **Behaviour change:** a re-ingest used to revive a retired document.
4. **Removing a lifecycle record restores only a document that record retired** [4] (the document carries `lifecycle_record`), so a document retracted since (its folder deleted — `retract_document` clears the mark) or retired before records existed is left alone.
5. **Un-asserting a supersession does not un-retire the older document** [5]: the two records are independent; `docs restore` is the explicit way back.
6. **A synthesis record is the `:Synthesis` node's properties plus the entity key, with no embedding** [6]; it applies before the union rebuild and adds its key, so the rebuilt description is the synthesis and the entity sweep re-embeds locally. No machine pays the LLM twice.
7. **Structured-store curation re-exports only the table's `.meta.json`** [7] (`export_structured_text(meta_only=True)`; the CSV is written only if missing), after `db grain --set`, `db mappings set/confirm/clear/--acceptProposed`, `db bridge confirm/clear` and `db propose` (the last two beyond the brief: they write the same registry rows).
8. **A curation-only meta change is applied cheaply** [8]: when a table's CSV is the same blob at base and HEAD and its `.meta.json` differs only in grain, bridge columns, column mappings or their proposal statuses, track B restores the registry rows alone — no parquet rewrite, no `table2graph` re-projection. A table with no parquet on this machine is still restored fully.
9. **`vault sync` re-projects the structured catalogue** (`:Table`/`:TableColumn`/`MAPS_TO_CLASS`) for every domain whose registry rows it restored, when the graph is in the run [9]. It never did (a gap since Plan A): the catalogue is derived from the registry, and a confirmed mapping otherwise never reached another machine's graph.
10. **Still machine-local, by design** [10]: same-as proposals (`sameas propose`/`reject`, `ingest refine-graph`, `detect-conflicts`' same-entity verdicts — spec §14 A6: a review queue whose approved outcome travels as `same_as.yaml`). `query graph text2cypher` cannot write: it runs through `graph_query.read_session()`, which Neo4j enforces as read-only (`graph_query._run_read_query`).

## File map

| File | Change |
|---|---|
| `artmind/supersession_records.py` | new: the `supersessions` kind (Task 1) |
| `artmind/temporal.py` | `apply_supersession` writes the record and applies it (Task 1) |
| `artmind/lifecycle_records.py` | new: the `lifecycle` kind (Task 2) |
| `artmind/lifecycle.py` | `_transition(rebuild=)`; retire/restore write/delete the record (Task 2) |
| `artmind/ingest.py` | `_load_staged` reads the lifecycle record; `_commit_document_tx` step 5b; `retract_document` clears the mark (Task 2) |
| `artmind/curation_records.py` | registers each kind (Tasks 1–3); `Kind.reapply_for` (Task 2) |
| `artmind/vault_sync.py` | `reapply_for_replayed` (Task 2); curation-only tables, catalogue (Task 5); docstring (Task 6) |
| `artmind/synthesis_records.py` | new: the `syntheses` kind (Task 3) |
| `artmind/synthesize.py` | `synthesize_key` writes the record and applies it (Task 3) |
| `artmind/structured/text_export.py` | `export_structured_text(meta_only=)` (Task 4), `import_structured_text(registry_only=)` (Task 5) |
| `artmind/structured/pipeline.py` | `_export_text_best_effort(meta_only=)` (Task 4) |
| `artmind/cli.py` | `_reexport_table_meta` after each registry curation command (Task 4); docstrings (Task 6) |
| `docs/vault.md`, `artmind/skills/artmind-curate/SKILL.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md`, the spec (§15 A10) | prose (Task 6) |
| `test/test_supersession_records.py`, `test/test_lifecycle_records.py`, `test/test_synthesis_records.py` | new |
| `test/test_vault_sync.py`, `test/test_db_mappings_cli.py`, `test/test_structured_text_export.py` | extended |

---

### Task 1: supersessions travel as curation records; `valid_to` derived from the edges

`artmind/supersession_records.py` is the `supersessions` kind (`phase: "pre"`). `record_for` builds `{id, newer_doc_id, older_doc_id, scope, effective, detected_by, domains}` (domains read from the two documents). `apply` MERGEs `(newer)-[:SUPERSEDES {scope}]->(older)` — either document may be `:Document` or `:DocumentHistory` (a lifecycle record may already have retired the older one) — with `effective`, `detected_by`, `record_id`, `record_fingerprint`, then runs `_RECOMPUTE`, which sets the older document's `superseded_by`/`valid_to` from all its remaining edges (decision 1). `remove` deletes the edge the base record names and recomputes. `temporal.apply_supersession` — used by `ingest supersede`, `detect-supersession` and the adjudicator — writes the record and applies it; its document-scoped retirement still calls `lifecycle.retire_document` (a separate record from Task 2 on).

**Files:**
- Modify: `artmind/curation_records.py`
- Create: `artmind/supersession_records.py`
- Modify: `artmind/temporal.py`
- Test: `test/test_supersession_records.py` (new)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_supersession_records.py`:

```python
"""Supersession assertions travel as curation records (spec 2026-09-26 §15
A10). No Neo4j: recording sessions stand in; every test asserts on the
Cypher and parameters sent (CLAUDE.md §3)."""
import hashlib
import json

import pytest

from artmind import curation_records, supersession_records


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, domains=("banking.ops",)):
        self.calls = []
        self._domains = domains

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "RETURN DISTINCT d._domain AS domain" in cypher:
            return _Result({"domain": d} for d in self._domains)
        return _Result()


def _record(**overrides):
    record = supersession_records.record_for(_Session(), "new-doc", "old-doc", "document", "2026-03-01", "manual")
    record.update(overrides)
    return record


def test_a_supersession_record_is_keyed_by_newer_older_and_scope():
    record = _record()

    assert record == {
        "id": hashlib.sha1(b"new-doc|old-doc|document").hexdigest(),
        "newer_doc_id": "new-doc", "older_doc_id": "old-doc", "scope": "document",
        "effective": "2026-03-01", "detected_by": "manual", "domains": ["banking.ops"],
    }
    assert supersession_records.record_id("new-doc", "old-doc", "node") != record["id"]


def test_apply_merges_the_edge_then_derives_valid_to_from_every_edge():
    tx = _Session()
    record = _record()

    assert supersession_records.apply(tx, record, "fp") == set()

    (merge, merge_params), (recompute, recompute_params) = tx.calls
    assert "MERGE (newer)-[s:SUPERSEDES {scope: $scope}]->(older)" in merge
    assert "OPTIONAL MATCH (o2:DocumentHistory {id: $older})" in merge, "a retired older document still gets its edge"
    assert merge_params == {
        "newer": "new-doc", "older": "old-doc", "scope": "document", "effective": "2026-03-01",
        "detected_by": "manual", "id": record["id"], "fingerprint": "fp",
    }
    assert "ORDER BY coalesce(s.effective, '') DESC, newer.id DESC" in recompute
    assert "older.valid_to = head([e IN edges WHERE e.effective IS NOT NULL | e.effective])" in recompute
    assert "older.superseded_by = CASE WHEN size(edges) = 0 THEN null" in recompute
    assert recompute_params == {"older": "old-doc"}


def test_remove_deletes_the_edge_and_re_derives_valid_to_from_what_remains():
    tx = _Session()

    supersession_records.remove(tx, _record())

    (delete, params), (recompute, recompute_params) = tx.calls
    assert "MATCH (newer)-[s:SUPERSEDES {scope: $scope}]->(older)" in delete and "DELETE s" in delete
    assert params == {"newer": "new-doc", "older": "old-doc", "scope": "document"}
    assert recompute_params == {"older": "old-doc"}


def test_remove_of_an_unreadable_record_matches_the_edge_by_record_id():
    tx = _Session()

    supersession_records.remove(tx, {"id": "abc"})

    assert tx.calls == [("MATCH ()-[s:SUPERSEDES {record_id: $id}]->() DELETE s", {"id": "abc"})]


def test_the_supersessions_kind_runs_before_the_rebuild():
    kind = curation_records.kinds()["supersessions"]

    assert kind.phase == "pre"
    assert kind.domains(_record()) == ["banking.ops"]


def test_apply_supersession_writes_the_record_and_applies_it(monkeypatch, curation_dir):
    from contextlib import contextmanager

    import artmind.temporal as temporal

    session = _Session()

    @contextmanager
    def _fake(*a, **k):
        yield session

    retired = []
    monkeypatch.setattr(temporal, "neo4j_session", _fake)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, *a, **k: retired.append(doc_id))

    temporal.apply_supersession("new-doc", "old-doc", "document", "2026-03-01", detected_by="notice")

    record_id = supersession_records.record_id("new-doc", "old-doc", "document")
    path = curation_dir / "supersessions" / f"{record_id}.json"
    record = json.loads(path.read_text())
    assert (record["effective"], record["detected_by"]) == ("2026-03-01", "notice")
    merges = [p for c, p in session.calls if "MERGE (newer)-[s:SUPERSEDES" in c]
    assert merges[0]["fingerprint"] == curation_records.fingerprint(path.read_bytes())
    assert retired == ["old-doc"]


def test_re_asserting_the_same_supersession_does_not_rewrite_its_file(monkeypatch, curation_dir):
    from contextlib import contextmanager

    import artmind.temporal as temporal

    @contextmanager
    def _fake(*a, **k):
        yield _Session()

    monkeypatch.setattr(temporal, "neo4j_session", _fake)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda *a, **k: None)
    temporal.apply_supersession("new-doc", "old-doc", "node", None)
    path = curation_dir / "supersessions" / f"{supersession_records.record_id('new-doc', 'old-doc', 'node')}.json"
    before = path.stat().st_mtime_ns

    temporal.apply_supersession("new-doc", "old-doc", "node", None)

    assert path.stat().st_mtime_ns == before
```

**Replace in** `test/test_vault_sync.py` (lines 3599-3601):

```python
    })

    assert "graph      b  0 docs / 0 tables / 3 curation records behind" in capsys.readouterr().out
```

**with:**

```python
    })

    assert "graph      b  0 docs / 0 tables / 3 curation records behind" in capsys.readouterr().out


def test_sync_applies_and_removes_supersession_records(repo, monkeypatch, curation_graph):
    from artmind import curation_records, supersession_records

    def _supersession(newer, older):
        return {"id": supersession_records.record_id(newer, older, "document"), "newer_doc_id": newer,
                "older_doc_id": older, "scope": "document", "effective": "2026-03-01",
                "detected_by": "manual", "domains": ["banking"]}

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _patch_curation_dir(monkeypatch, repo)
    gone = _supersession("d2", "d1")
    curation_records.write_record("supersessions", gone)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "base")
    base = vs.head_sha(repo)
    added = _supersession("d3", "d2")
    curation_records.write_record("supersessions", added)
    curation_records.delete_record("supersessions", gone["id"])
    _commit_all(repo, "d3 supersedes d2; d2 no longer supersedes d1")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo)

    merges = [p for c, p in curation_graph.calls if "MERGE (newer)-[s:SUPERSEDES" in c]
    deletes = [p for c, p in curation_graph.calls if "MATCH (newer)-[s:SUPERSEDES {scope: $scope}]->(older)" in c]
    assert [(p["newer"], p["older"]) for p in merges] == [("d3", "d2")]
    assert deletes == [{"newer": "d2", "older": "d1", "scope": "document"}], "removed from the record as it was at base"
    assert result["curation"] == {"supersessions": {"apply": 1, "remove": 1}}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_supersession_records.py test/test_vault_sync.py -q`
Expected: `1 error` during collection — `ImportError: cannot import name 'supersession_records' from 'artmind'`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/curation_records.py` (lines 72-80):

```python

def kinds() -> dict[str, Kind]:
    """Every registered kind, by directory name, in apply order."""
    from artmind import conflict_records

    return {kind.name: kind for kind in (conflict_records.KIND,)}


def curation_dir() -> Path:
```

**with:**

```python

def kinds() -> dict[str, Kind]:
    """Every registered kind, by directory name, in apply order."""
    from artmind import conflict_records, supersession_records

    return {kind.name: kind for kind in (supersession_records.KIND, conflict_records.KIND)}


def curation_dir() -> Path:
```

**Create** `artmind/supersession_records.py`:

```python
"""Supersession records -- the `supersessions` curation kind (spec 2026-09-26
§7, D6; §15 A10).

`(:Document)-[:SUPERSEDES {scope}]->(:Document)` is asserted by
`temporal.apply_supersession` -- from `ingest supersede`, `ingest
detect-supersession`, and the adjudicator's "superseded" verdict -- and used
to live only in the graph. Each assertion is now also a file,
`.artmind/data/curation/supersessions/<id>.json`:

    {"id", "newer_doc_id", "older_doc_id", "scope", "effective",
     "detected_by", "domains"}

The id is sha1 of `newer|older|scope`, so re-asserting the same supersession
on any machine names the same record. No timestamp: re-running detection
rewrites nothing.

**`older.valid_to` and `older.superseded_by` are derived from the edges.**
Only supersession writes them (a document's own window is `_valid_from`/
`_valid_to`, from its staging folder), so both are recomputed from every
`SUPERSEDES` edge still pointing at the older document after each apply or
removal: `superseded_by` is the newer document of the edge with the latest
`effective` (ties by id), `valid_to` that latest non-null `effective`, both
null when no edge remains. Un-asserting a supersession therefore restores
exactly what the remaining assertions say -- no snapshot of an earlier value
to go stale, and the same result on every machine whatever order records are
applied in.

Superseding with `scope: document` also retires the older document -- a
separate curation record (`lifecycle`), which un-asserting the supersession
does not remove: `docs restore` does.
"""
from __future__ import annotations

import hashlib

from artmind.curation_records import Kind

NAME = "supersessions"

# Both documents, whether live or retired: a `lifecycle` record may already
# have moved the older one to :DocumentHistory.
_BOTH_DOCS = """
OPTIONAL MATCH (n1:Document {id: $newer}) OPTIONAL MATCH (n2:DocumentHistory {id: $newer})
WITH coalesce(n1, n2) AS newer
OPTIONAL MATCH (o1:Document {id: $older}) OPTIONAL MATCH (o2:DocumentHistory {id: $older})
WITH newer, coalesce(o1, o2) AS older
WHERE newer IS NOT NULL AND older IS NOT NULL
"""

_APPLY = _BOTH_DOCS + """
MERGE (newer)-[s:SUPERSEDES {scope: $scope}]->(older)
SET s.effective = $effective, s.detected_by = $detected_by,
    s.record_id = $id, s.record_fingerprint = $fingerprint
"""

_REMOVE = _BOTH_DOCS + """
MATCH (newer)-[s:SUPERSEDES {scope: $scope}]->(older)
DELETE s
"""

_RECOMPUTE = """
OPTIONAL MATCH (o1:Document {id: $older}) OPTIONAL MATCH (o2:DocumentHistory {id: $older})
WITH coalesce(o1, o2) AS older
WHERE older IS NOT NULL
OPTIONAL MATCH (newer)-[s:SUPERSEDES]->(older)
WITH older, newer, s
ORDER BY coalesce(s.effective, '') DESC, newer.id DESC
WITH older, collect(CASE WHEN s IS NULL THEN null ELSE {newer: newer.id, effective: s.effective} END) AS edges
SET older.superseded_by = CASE WHEN size(edges) = 0 THEN null ELSE edges[0].newer END,
    older.valid_to = head([e IN edges WHERE e.effective IS NOT NULL | e.effective])
"""

_DOMAINS = """
MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id IN $ids
RETURN DISTINCT d._domain AS domain
"""

_READ_FINGERPRINTS = (
    "MATCH ()-[s:SUPERSEDES]->() WHERE s.record_id IN $ids "
    "RETURN s.record_id AS id, s.record_fingerprint AS fingerprint"
)


def record_id(newer_doc_id: str, older_doc_id: str, scope: str) -> str:
    return hashlib.sha1(f"{newer_doc_id}|{older_doc_id}|{scope}".encode("utf-8")).hexdigest()


def record_for(
    session, newer_doc_id: str, older_doc_id: str, scope: str, effective: str | None, detected_by: str
) -> dict:
    """The record for one assertion; its `domains` read from the two
    documents (for `vault sync --domain`)."""
    rows = session.run(_DOMAINS, ids=[newer_doc_id, older_doc_id]).data()
    domains = sorted({row.get("domain") for row in rows if isinstance(row, dict) and isinstance(row.get("domain"), str)})
    return {
        "id": record_id(newer_doc_id, older_doc_id, scope),
        "newer_doc_id": newer_doc_id,
        "older_doc_id": older_doc_id,
        "scope": scope,
        "effective": effective,
        "detected_by": detected_by,
        "domains": domains,
    }


def apply(tx, record: dict, fingerprint: str) -> set:
    """MERGE the `SUPERSEDES` edge (both documents must exist, live or
    retired), then recompute the older document's `superseded_by`/`valid_to`
    from its edges. Document-level: returns no projection keys."""
    tx.run(
        _APPLY, newer=record["newer_doc_id"], older=record["older_doc_id"], scope=record["scope"],
        effective=record.get("effective"), detected_by=record.get("detected_by"),
        id=record["id"], fingerprint=fingerprint,
    )
    tx.run(_RECOMPUTE, older=record["older_doc_id"])
    return set()


def remove(tx, record: dict) -> set:
    """Un-assert: delete the edge the record names, then recompute the older
    document's `superseded_by`/`valid_to` from the edges that remain. A
    record whose content could not be read (only its id is known) matches
    the edge by `record_id` instead."""
    if record.get("newer_doc_id") and record.get("older_doc_id"):
        tx.run(_REMOVE, newer=record["newer_doc_id"], older=record["older_doc_id"], scope=record.get("scope"))
        tx.run(_RECOMPUTE, older=record["older_doc_id"])
    else:
        tx.run("MATCH ()-[s:SUPERSEDES {record_id: $id}]->() DELETE s", id=record["id"])
    return set()


def read_fingerprints(ids: list[str], *, timeout: float | None = None) -> dict:
    from artmind import graph_query

    if not ids:
        return {}
    with graph_query.neo4j_session(timeout=timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, ids=sorted(set(ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def domains(record: dict) -> list:
    return list(record.get("domains") or [])


KIND = Kind(name=NAME, phase="pre", apply=apply, remove=remove, read_fingerprints=read_fingerprints, domains=domains)
```

**Replace in** `artmind/temporal.py` (lines 362-381):

```python
    aggregate key left with zero `latest` observations loses its `:Entity`.
    Not a heuristic — an arithmetic fact about what is still asserted.

    Idempotent.
    """
    with neo4j_session() as session:
        session.run(
            """
            MATCH (newer:Document {id:$newer}), (older:Document {id:$older})
            MERGE (newer)-[s:SUPERSEDES {scope:$scope}]->(older)
            SET s.effective=$effective, s.detected_by=$detectedBy
            SET older.valid_to = coalesce($effective, older.valid_to),
                older.superseded_by = newer.id
            """,
            newer=newer_doc_id, older=older_doc_id, scope=scope,
            effective=effective, detectedBy=detected_by,
        )
    if scope == "document":
        # Retirement is document-granular by nature: a whole document's
        # assertions stop being the current record at once.
```

**with:**

```python
    aggregate key left with zero `latest` observations loses its `:Entity`.
    Not a heuristic — an arithmetic fact about what is still asserted.

    The assertion travels (spec 2026-09-26 §15 A10): it is written to its
    record file, `.artmind/data/curation/supersessions/<id>.json`, and the
    graph is written from that record -- the same path `vault sync` applies
    on every other machine (`artmind.supersession_records`). The older
    document's `valid_to`/`superseded_by` are recomputed from all of its
    `SUPERSEDES` edges.

    Idempotent.
    """
    from artmind import curation_records, supersession_records

    with neo4j_session() as session:
        record = supersession_records.record_for(
            session, newer_doc_id, older_doc_id, scope, effective, detected_by,
        )
        fingerprint = curation_records.write_record(supersession_records.NAME, record)
        supersession_records.apply(session, record, fingerprint)
    if scope == "document":
        # Retirement is document-granular by nature: a whole document's
        # assertions stop being the current record at once.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_supersession_records.py test/test_vault_sync.py -q`
Expected: `172 passed`. Then the full suite — all pass (`test_entity_retirement.py`'s supersession tests included: the new Cypher sets no `e.status` and no `superseded_by = newer.id`).

- [ ] **Step 5: Commit**

```bash
git add artmind/curation_records.py artmind/supersession_records.py artmind/temporal.py test/test_supersession_records.py test/test_vault_sync.py
git commit -m "feat(supersession): each SUPERSEDES assertion is a curation record; valid_to derived from the edges"
```

---

### Task 2: a retirement is a curation record, sticky across re-ingest and replay

`artmind/lifecycle_records.py` is the `lifecycle` kind (`phase: "pre"`, first in apply order). Its record `{id: sha1(doc_id), doc_id, domain, status: "retired"}` exists exactly while the document is retired (decision 2). `apply` retires via `lifecycle._transition(..., rebuild=False)` (the union rebuild covers the keys it returns) and marks the `:DocumentHistory` with `lifecycle_record`/`lifecycle_fingerprint`; `remove` restores only a document this record marked (decision 4). `Kind` gains an optional `reapply_for(doc_ids)`: `vault sync`'s new `reapply_for_replayed` adds, after the fingerprint skip and before the dry-run return, every lifecycle record at HEAD whose document this run replays (decision 3).

`docs retire` writes the record inside its transaction (vault first), swaps labels and rebuilds as before, then marks; `docs restore` deletes the record, swaps back and unmarks. `_load_staged` reports `retired` from the working tree's record, and `_commit_document_tx`'s new step 5b moves a just-committed retired document straight back to history before the rebuild. `retract_document` clears the mark.

**Files:**
- Modify: `artmind/curation_records.py`
- Modify: `artmind/ingest.py`
- Modify: `artmind/lifecycle.py`
- Create: `artmind/lifecycle_records.py`
- Modify: `artmind/vault_sync.py`
- Test: `test/test_lifecycle_records.py` (new)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_lifecycle_records.py`:

```python
"""A retirement travels as a curation record, and sticks (spec 2026-09-26
§15 A10). No Neo4j: recording sessions stand in; every test asserts on the
Cypher and parameters sent (CLAUDE.md §3)."""
import hashlib
import json
from contextlib import contextmanager

import pytest

import artmind.ingest as ing
from artmind import curation_records, lifecycle, lifecycle_records


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Tx:
    """Answers `keys_for_document` with one key, the domain lookup with
    `banking`, and the "did this record retire it?" check with `held`."""

    def __init__(self, held=1):
        self.calls = []
        self.held = held

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "RETURN d._domain AS domain" in cypher:
            return _Result([{"domain": "banking"}])
        if "RETURN count(d) AS n" in cypher and "lifecycle_record" in cypher:
            return _Result([{"n": self.held}])
        if "o.key AS key" in cypher or "RETURN DISTINCT o.key" in cypher or ".key AS key" in cypher:
            return _Result([{"key": "acme|ORG|banking"}])
        return _Result([{"n": 2}])

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    def texts(self):
        return [c for c, _ in self.calls]


@pytest.fixture()
def no_rebuild(monkeypatch):
    import artmind.projection as projection

    seen = []
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: seen.append(sorted(keys)) or {})
    return seen


def test_a_lifecycle_record_is_keyed_by_a_hash_of_the_document_id():
    record = lifecycle_records.record_for(_Tx(), "table:banking:accounts")

    assert record == {
        "id": hashlib.sha1(b"table:banking:accounts").hexdigest(),
        "doc_id": "table:banking:accounts", "domain": "banking", "status": "retired",
    }


def test_retire_document_writes_the_record_and_marks_the_document(monkeypatch, curation_dir, no_rebuild):
    tx = _Tx()

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(lifecycle, "neo4j_session", _fake)

    lifecycle.retire_document("doc-1")

    path = curation_dir / "lifecycle" / f"{lifecycle_records.record_id('doc-1')}.json"
    assert json.loads(path.read_text())["doc_id"] == "doc-1"
    marks = [p for c, p in tx.calls if "SET d.lifecycle_record = $id" in c]
    assert marks == [{"doc_id": "doc-1", "id": lifecycle_records.record_id("doc-1"),
                      "fingerprint": curation_records.fingerprint(path.read_bytes())}]
    assert any("REMOVE d:Document SET d:DocumentHistory" in c for c in tx.texts())
    assert len(no_rebuild) == 1, "retire still rebuilds in its own transaction"


def test_restore_document_deletes_the_record_and_unmarks(monkeypatch, curation_dir, no_rebuild):
    curation_records.write_record("lifecycle", lifecycle_records.record_for(_Tx(), "doc-1"))
    tx = _Tx()

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(lifecycle, "neo4j_session", _fake)

    lifecycle.restore_document("doc-1")

    assert curation_records.read_record("lifecycle", lifecycle_records.record_id("doc-1")) is None
    assert any("REMOVE d.lifecycle_record, d.lifecycle_fingerprint" in c for c in tx.texts())
    assert any("REMOVE d:DocumentHistory SET d:Document" in c for c in tx.texts())


def test_apply_retires_without_rebuilding_and_returns_the_keys(no_rebuild):
    tx = _Tx()
    record = lifecycle_records.record_for(tx, "doc-1")

    keys = lifecycle_records.apply(tx, record, "fp")

    assert keys == {("acme", "ORG", "banking")}
    assert no_rebuild == [], "vault sync's union rebuild covers it"
    assert any("REMOVE d:Document SET d:DocumentHistory" in c for c in tx.texts())
    assert ("MATCH (d:DocumentHistory {id: $doc_id}) SET d.lifecycle_record = $id, d.lifecycle_fingerprint = $fingerprint",
            {"doc_id": "doc-1", "id": record["id"], "fingerprint": "fp"}) in tx.calls


def test_remove_restores_only_a_document_this_record_retired(no_rebuild):
    record = lifecycle_records.record_for(_Tx(), "doc-1")

    not_ours = _Tx(held=0)
    assert lifecycle_records.remove(not_ours, record) == set()
    assert not any("SET d:Document" in c for c in not_ours.texts())

    ours = _Tx(held=1)
    assert lifecycle_records.remove(ours, record) == {("acme", "ORG", "banking")}
    assert any("REMOVE d:DocumentHistory SET d:Document" in c for c in ours.texts())
    assert any("REMOVE d.lifecycle_record, d.lifecycle_fingerprint" in c for c in ours.texts())


def _stage(folder, doc_id="doc-1"):
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": doc_id, "name": "a.md"}))
    (folder / "observations.json").write_text("[]")
    return folder


def _commit(monkeypatch, folder):
    import artmind.graph_query as graph_query

    tx = _Tx()

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    summary = ing._write_to_neo4j(folder, "banking", defer_rebuild=True)
    return tx, summary


def test_a_commit_of_a_retired_document_keeps_it_retired(tmp_path, monkeypatch):
    curation_records.write_record("lifecycle", lifecycle_records.record_for(_Tx(), "doc-1"))

    tx, summary = _commit(monkeypatch, _stage(tmp_path / "doc"))

    texts = tx.texts()
    revive = next(i for i, c in enumerate(texts) if "CREATE (n:Document {id: $id})" in c)
    retire = next(i for i, c in enumerate(texts) if "REMOVE d:Document SET d:DocumentHistory" in c)
    assert retire > revive
    assert summary["retired"] is True


def test_a_commit_of_a_live_document_does_not_retire_it(tmp_path, monkeypatch):
    tx, summary = _commit(monkeypatch, _stage(tmp_path / "doc"))

    assert not any("REMOVE d:Document SET d:DocumentHistory" in c for c in tx.texts())
    assert "retired" not in summary


def test_retracting_a_document_clears_its_lifecycle_mark(monkeypatch):
    import artmind.graph_query as graph_query
    import artmind.projection as projection

    tx = _Tx()

    @contextmanager
    def _fake(*a, **k):
        yield tx

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, **kw: set())

    ing.retract_document("doc-1", "banking")

    assert ("MATCH (d:DocumentHistory {id: $doc_id}) REMOVE d.lifecycle_record, d.lifecycle_fingerprint",
            {"doc_id": "doc-1"}) in tx.calls


def test_the_lifecycle_kind_runs_first_and_re_applies_after_a_replay():
    kinds = curation_records.kinds()

    assert list(kinds)[0] == "lifecycle"
    assert kinds["lifecycle"].phase == "pre"
    assert kinds["lifecycle"].reapply_for(["doc-1", None]) == [lifecycle_records.record_id("doc-1")]
```

**Replace in** `test/test_vault_sync.py` (lines 3632-3634):

```python
    assert [(p["newer"], p["older"]) for p in merges] == [("d3", "d2")]
    assert deletes == [{"newer": "d2", "older": "d1", "scope": "document"}], "removed from the record as it was at base"
    assert result["curation"] == {"supersessions": {"apply": 1, "remove": 1}}
```

**with:**

```python
    assert [(p["newer"], p["older"]) for p in merges] == [("d3", "d2")]
    assert deletes == [{"newer": "d2", "older": "d1", "scope": "document"}], "removed from the record as it was at base"
    assert result["curation"] == {"supersessions": {"apply": 1, "remove": 1}}


# ── lifecycle records: a retirement travels, and survives a replay ───────────


def _lifecycle(doc_id, domain="banking"):
    from artmind import lifecycle_records

    return {"id": lifecycle_records.record_id(doc_id), "doc_id": doc_id, "domain": domain, "status": "retired"}


def test_sync_re_applies_a_committed_retirement_after_replaying_the_document(repo, monkeypatch, curation_graph):
    """The document's folder changed but its (older) lifecycle record did
    not: the replay revives the node, so the record must be applied again."""
    from artmind import curation_records

    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _patch_curation_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    curation_records.write_record("lifecycle", _lifecycle("docid-1"))
    _commit_all(repo, "doc1, retired")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "observations.json").write_text('[{"id": "o2"}]')
    _commit_all(repo, "doc1 re-extracted")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    calls = _patch_ingest_and_projection(monkeypatch)

    dry = vs.sync(repo, dry_run=True)
    result = vs.sync(repo)

    assert [Path(p).name for p, _ in calls["write_to_neo4j"]] == ["doc1"]
    marks = [p for c, p in curation_graph.calls if "SET d.lifecycle_record = $id" in c]
    assert [p["doc_id"] for p in marks] == ["docid-1"]
    assert dry["curation"] == result["curation"] == {"lifecycle": {"apply": 1, "remove": 0}}


def test_sync_restores_a_document_whose_lifecycle_record_was_deleted(repo, monkeypatch, curation_graph):
    from artmind import curation_records

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _patch_curation_dir(monkeypatch, repo)
    curation_records.write_record("lifecycle", _lifecycle("docid-1"))
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "retired")
    base = vs.head_sha(repo)
    curation_records.delete_record("lifecycle", _lifecycle("docid-1")["id"])
    _commit_all(repo, "docs restore")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    _patch_ingest_and_projection(monkeypatch)

    vs.sync(repo)

    guards = [p for c, p in curation_graph.calls if "RETURN count(d) AS n" in c and "lifecycle_record" in c]
    assert guards == [{"doc_id": "docid-1", "id": _lifecycle("docid-1")["id"]}], "the doc id comes from the record at base"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_lifecycle_records.py test/test_vault_sync.py -q`
Expected: `1 error` during collection — `ImportError: cannot import name 'lifecycle_records' from 'artmind'`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/curation_records.py` (lines 60-65):

```python
      another machine already applied, and `vault status` count only what is
      really pending.
    - `domains(record)`: the domains a record belongs to, for `--domain`.
    """

    name: str
```

**with:**

```python
      another machine already applied, and `vault status` count only what is
      really pending.
    - `domains(record)`: the domains a record belongs to, for `--domain`.
    - `reapply_for(doc_ids)` (optional): ids of this kind's records to apply
      again after `vault sync` replays those documents -- for a kind whose
      effect a document replay would undo (a retirement).
    """

    name: str
```

**Replace in** `artmind/curation_records.py` (lines 68-80):

```python
    remove: Callable[[Any, dict], set]
    read_fingerprints: Callable[..., dict]
    domains: Callable[[dict], list]


def kinds() -> dict[str, Kind]:
    """Every registered kind, by directory name, in apply order."""
    from artmind import conflict_records, supersession_records

    return {kind.name: kind for kind in (supersession_records.KIND, conflict_records.KIND)}


def curation_dir() -> Path:
```

**with:**

```python
    remove: Callable[[Any, dict], set]
    read_fingerprints: Callable[..., dict]
    domains: Callable[[dict], list]
    reapply_for: Callable[[list], list] | None = None


def kinds() -> dict[str, Kind]:
    """Every registered kind, by directory name, in apply order."""
    from artmind import conflict_records, lifecycle_records, supersession_records

    return {kind.name: kind for kind in (lifecycle_records.KIND, supersession_records.KIND, conflict_records.KIND)}


def curation_dir() -> Path:
```

**Replace in** `artmind/ingest.py` (lines 1685-1690):

```python
            "MATCH (c:UserChat {id: $doc_id}) REMOVE c:UserChat SET c:UserChatHistory",
            doc_id=doc_id,
        )
        return {"doc_id": doc_id, "domain": domain, "affected_keys": sorted(keys), **retracted}

    with neo4j_session() as session:
```

**with:**

```python
            "MATCH (c:UserChat {id: $doc_id}) REMOVE c:UserChat SET c:UserChatHistory",
            doc_id=doc_id,
        )
        # A retracted document is no longer "retired by a record": removing
        # that record later must not bring it back (`lifecycle_records.remove`).
        tx.run(
            "MATCH (d:DocumentHistory {id: $doc_id}) REMOVE d.lifecycle_record, d.lifecycle_fingerprint",
            doc_id=doc_id,
        )
        return {"doc_id": doc_id, "domain": domain, "affected_keys": sorted(keys), **retracted}

    with neo4j_session() as session:
```

**Replace in** `artmind/ingest.py` (lines 2843-2848):

```python
        tx, staged["relationships"], document, observations
    )

    # 6. The affected-key union, then the rebuild — which now also aggregates
    #    RELATES_TO edges for every key it touches.
    keys = projection.affected_keys(
```

**with:**

```python
        tx, staged["relationships"], document, observations
    )

    # 5b. A retired document stays retired (spec 2026-09-26 §15 A10): step 3
    #     revived its node, so a lifecycle record for it moves the version
    #     just written straight back to history. `affected_keys` below already
    #     holds its keys, so the rebuild drops what only it asserted.
    if staged.get("retired") and source_label == "Document":
        from artmind.lifecycle import _transition

        _transition(tx, doc_id, to_history=True, rebuild=False)
        summary["retired"] = True

    # 6. The affected-key union, then the rebuild — which now also aggregates
    #    RELATES_TO edges for every key it touches.
    keys = projection.affected_keys(
```

**Replace in** `artmind/ingest.py` (lines 2877-2884):

```python
    `fingerprint` is taken from the files' bytes as they are on disk, before
    any parsing (`sync_state.staging_fingerprint`), so it equals what `vault
    sync` computes from the same files' committed blobs.
    """
    from artmind import sync_state

    def _load(name: str, default=None):
        path = doc_kg_dir / name
```

**with:**

```python
    `fingerprint` is taken from the files' bytes as they are on disk, before
    any parsing (`sync_state.staging_fingerprint`), so it equals what `vault
    sync` computes from the same files' committed blobs.

    `retired` says whether this working tree holds a lifecycle record
    retiring the document (`artmind.lifecycle_records`), which the commit
    honours.
    """
    from artmind import lifecycle_records, sync_state

    def _load(name: str, default=None):
        path = doc_kg_dir / name
```

**Replace in** `artmind/ingest.py` (lines 2900-2912):

```python
            for chunk in chunks:
                if "embedding" not in chunk and chunk.get("id") in sidecar:
                    chunk["embedding"] = sidecar[chunk["id"]]
        return {
            "domain": domain,
            "document": _load("document.json"),
            "chunks": chunks,
            "observations": _load("observations.json", []),
            "relationships": _load("relationships.json", []),
            "fingerprint": sync_state.folder_fingerprint(doc_kg_dir),
        }
    except Exception as e:
        logger.error("Failed to load KG JSON files from {}: {}", doc_kg_dir, e)
```

**with:**

```python
            for chunk in chunks:
                if "embedding" not in chunk and chunk.get("id") in sidecar:
                    chunk["embedding"] = sidecar[chunk["id"]]
        document = _load("document.json")
        return {
            "domain": domain,
            "document": document,
            "chunks": chunks,
            "observations": _load("observations.json", []),
            "relationships": _load("relationships.json", []),
            "fingerprint": sync_state.folder_fingerprint(doc_kg_dir),
            "retired": lifecycle_records.retired_in_working_tree(
                document.get("id") if isinstance(document, dict) else None
            ),
        }
    except Exception as e:
        logger.error("Failed to load KG JSON files from {}: {}", doc_kg_dir, e)
```

**Replace in** `artmind/lifecycle.py` (lines 28-37):

```python
from artmind.graph_query import neo4j_session


def _transition(tx, doc_id: str, *, to_history: bool) -> dict:
    """Move one document's node, chunks and observations between the base
    label and its History counterpart, then rebuild the keys it touched.
    Runs in the caller's transaction.

    A **label swap**, not a status property — there is no `_status` left on
    these nodes. `retire`/`restore` also mean "leave"/"return to"
```

**with:**

```python
from artmind.graph_query import neo4j_session


def _transition(tx, doc_id: str, *, to_history: bool, rebuild: bool = True) -> dict:
    """Move one document's node, chunks and observations between the base
    label and its History counterpart, then rebuild the keys it touched --
    unless `rebuild=False`, for a caller that rebuilds them itself (a
    document commit, `vault sync`'s union rebuild). Runs in the caller's
    transaction.

    A **label swap**, not a status property — there is no `_status` left on
    these nodes. `retire`/`restore` also mean "leave"/"return to"
```

**Replace in** `artmind/lifecycle.py` (lines 73-79):

```python
        doc_id=doc_id,
    )

    summary = projection.rebuild(tx, keys, synthesis_loader=lambda ks: projection.load_synthesis_batch(tx, ks))
    return {
        "doc_id": doc_id,
        "observations": int(observations["n"]) if observations else 0,
```

**with:**

```python
        doc_id=doc_id,
    )

    summary = (
        projection.rebuild(tx, keys, synthesis_loader=lambda ks: projection.load_synthesis_batch(tx, ks))
        if rebuild else None
    )
    return {
        "doc_id": doc_id,
        "observations": int(observations["n"]) if observations else 0,
```

**Replace in** `artmind/lifecycle.py` (lines 89-97):

```python
    asking for them; they leave every index. Entities left with no `latest`
    observation anywhere are deleted by the rebuild — not because this function
    decided they were orphans, but because nothing asserts them any more.
    """
    with neo4j_session() as session:
        result = session.execute_write(_transition, doc_id, to_history=True)
    logger.info(
        "Retired {}: {} observation(s) → history, projection {}",
        doc_id, result["observations"], result["projection"],
```

**with:**

```python
    asking for them; they leave every index. Entities left with no `latest`
    observation anywhere are deleted by the rebuild — not because this function
    decided they were orphans, but because nothing asserts them any more.

    The retirement travels (spec 2026-09-26 §15 A10): its record,
    `.artmind/data/curation/lifecycle/<sha1(doc_id)>.json`, is written first
    and the document is marked with it -- `vault sync` retires it on every
    other machine, and a later re-ingest or replay keeps it retired
    (`artmind.lifecycle_records`).
    """
    with neo4j_session() as session:
        result = session.execute_write(_retire_tx, doc_id, domain)
    logger.info(
        "Retired {}: {} observation(s) → history, projection {}",
        doc_id, result["observations"], result["projection"],
```

**Replace in** `artmind/lifecycle.py` (lines 106-115):

```python

    The exact inverse. Because ids are deterministic and the projection is
    derived, restoring recreates the same entities with the same ids rather
    than a parallel set.
    """
    with neo4j_session() as session:
        result = session.execute_write(_transition, doc_id, to_history=False)
    logger.info(
        "Restored {}: {} observation(s) → latest, projection {}",
        doc_id, result["observations"], result["projection"],
```

**with:**

```python

    The exact inverse. Because ids are deterministic and the projection is
    derived, restoring recreates the same entities with the same ids rather
    than a parallel set. Deletes the document's lifecycle record, so `vault
    sync` restores it on every other machine too.
    """
    from artmind import curation_records, lifecycle_records

    curation_records.delete_record(lifecycle_records.NAME, lifecycle_records.record_id(doc_id))
    with neo4j_session() as session:
        result = session.execute_write(_restore_tx, doc_id)
    logger.info(
        "Restored {}: {} observation(s) → latest, projection {}",
        doc_id, result["observations"], result["projection"],
```

**Replace in** `artmind/lifecycle.py` (lines 119-124):

```python
    return result


def _sweep(domain: str, keys: list) -> int:
    from artmind.ingest import _sweep_embeddings

```

**with:**

```python
    return result


def _retire_tx(tx, doc_id: str, domain: str | None) -> dict:
    """`retire_document`'s transaction: the record file, then the label swap
    and rebuild, then the mark tying the document to its record."""
    from artmind import curation_records, lifecycle_records

    record = lifecycle_records.record_for(tx, doc_id, domain)
    fingerprint = curation_records.write_record(lifecycle_records.NAME, record)
    result = _transition(tx, doc_id, to_history=True)
    lifecycle_records.mark(tx, record, fingerprint)
    return result


def _restore_tx(tx, doc_id: str) -> dict:
    from artmind import lifecycle_records

    result = _transition(tx, doc_id, to_history=False)
    lifecycle_records.unmark(tx, doc_id)
    return result


def _sweep(domain: str, keys: list) -> int:
    from artmind.ingest import _sweep_embeddings

```

**Create** `artmind/lifecycle_records.py`:

```python
"""Lifecycle records -- the `lifecycle` curation kind (spec 2026-09-26 §7,
D6; §15 A10).

`docs retire` (and a document-scoped supersession, and archive restore) move
a document and everything it asserted from `latest` to `history`; `docs
restore` moves it back. That used to live only in the graph. A retired
document now also has a file, `.artmind/data/curation/lifecycle/<id>.json`:

    {"id": sha1(doc_id), "doc_id", "domain", "status": "retired"}

**The file exists exactly while the document is retired**: `docs retire`
writes it, `docs restore` deletes it. So `vault sync` retires a document when
its record appears and restores it when the record disappears.

**A retirement is sticky across re-ingest and replay.** Committing a
document (`ingest._commit_document_tx`) revives its node under `:Document`;
when a lifecycle record for its id exists, the same transaction moves it
straight back to history. Locally the commit reads the working tree's
record; `vault sync` also re-applies, after replaying a document, the record
committed at `head` (`Kind.reapply_for`). A retired document therefore stays
retired until `docs restore`, whatever order machines ingest, replay and
sync in.

The id is a hash because a document id may hold characters no file name can
(`table:<domain>:<table>`); the record carries the id itself.
"""
from __future__ import annotations

import hashlib

from artmind.curation_records import Kind

NAME = "lifecycle"

_DOMAIN = (
    "MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $doc_id "
    "RETURN d._domain AS domain LIMIT 1"
)
_MARK = (
    "MATCH (d:DocumentHistory {id: $doc_id}) "
    "SET d.lifecycle_record = $id, d.lifecycle_fingerprint = $fingerprint"
)
_UNMARK = (
    "MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $doc_id "
    "REMOVE d.lifecycle_record, d.lifecycle_fingerprint"
)
_HELD = (
    "MATCH (d:DocumentHistory {id: $doc_id}) WHERE d.lifecycle_record = $id "
    "RETURN count(d) AS n"
)
_READ_FINGERPRINTS = (
    "MATCH (d:DocumentHistory) WHERE d.lifecycle_record IN $ids "
    "RETURN d.lifecycle_record AS id, d.lifecycle_fingerprint AS fingerprint"
)


def record_id(doc_id: str) -> str:
    return hashlib.sha1(doc_id.encode("utf-8")).hexdigest()


def record_for(tx, doc_id: str, domain: str | None = None) -> dict:
    """The record retiring `doc_id`; its domain read from the graph when not
    given."""
    if domain is None:
        rec = tx.run(_DOMAIN, doc_id=doc_id).single()
        value = rec.get("domain") if isinstance(rec, dict) else None
        domain = value if isinstance(value, str) else None
    return {"id": record_id(doc_id), "doc_id": doc_id, "domain": domain, "status": "retired"}


def retired_in_working_tree(doc_id: str | None) -> bool:
    """Does this working tree hold a lifecycle record retiring `doc_id`?"""
    from artmind import curation_records

    return bool(doc_id) and curation_records.read_record(NAME, record_id(doc_id)) is not None


def mark(tx, record: dict, fingerprint: str) -> None:
    """Record, on the retired document, which record retired it."""
    tx.run(_MARK, doc_id=record["doc_id"], id=record["id"], fingerprint=fingerprint)


def unmark(tx, doc_id: str) -> None:
    tx.run(_UNMARK, doc_id=doc_id)


def apply(tx, record: dict, fingerprint: str) -> set:
    """Retire the document (idempotent: an already-retired one only gets its
    mark) and return the keys its observations feed, for the rebuild."""
    from artmind.lifecycle import _transition

    result = _transition(tx, record["doc_id"], to_history=True, rebuild=False)
    mark(tx, record, fingerprint)
    return {tuple(k) for k in result["keys"]}


def remove(tx, record: dict) -> set:
    """The record is gone: restore the document -- only when THIS record
    retired it. A document retracted since (its staging folder deleted),
    or retired before records existed, is left where it is."""
    from artmind.lifecycle import _transition

    doc_id = record.get("doc_id")
    if not doc_id:
        return set()
    held = tx.run(_HELD, doc_id=doc_id, id=record["id"]).single()
    if not held or not held.get("n"):
        return set()
    result = _transition(tx, doc_id, to_history=False, rebuild=False)
    unmark(tx, doc_id)
    return {tuple(k) for k in result["keys"]}


def read_fingerprints(ids: list[str], *, timeout: float | None = None) -> dict:
    from artmind import graph_query

    if not ids:
        return {}
    with graph_query.neo4j_session(timeout=timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, ids=sorted(set(ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def domains(record: dict) -> list:
    return [record["domain"]] if record.get("domain") else []


def reapply_for(doc_ids: list[str]) -> list[str]:
    """The record ids to re-apply after `vault sync` replays `doc_ids`: a
    replay revives the node, and a record committed at `head` must win."""
    return sorted({record_id(d) for d in doc_ids if d})


KIND = Kind(
    name=NAME, phase="pre", apply=apply, remove=remove, read_fingerprints=read_fingerprints,
    domains=domains, reapply_for=reapply_for,
)
```

**Replace in** `artmind/vault_sync.py` (lines 919-924):

```python
    return len(unchanged)


def _curation_counts(items: list[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for kind, _, action in items:
```

**with:**

```python
    return len(unchanged)


def reapply_for_replayed(vault_dir: Path, plan: "SyncPlan") -> int:
    """Add to `plan.curation` the records a kind asks to re-apply after the
    documents `plan` replays (`Kind.reapply_for`) -- a replay revives a
    document's node, and a retirement committed at `head` must still win.
    Only records that exist at `head`; returns how many were added."""
    from artmind import curation_records

    known = curation_records.kinds()
    hooks = {name: kind for name, kind in known.items() if kind.reapply_for}
    if not hooks or not plan.replay_docs or _curation_rel(vault_dir) is None:
        return 0
    doc_ids = [doc_id for doc_id, _ in committed_fingerprints(vault_dir, plan.head, plan.replay_docs).values() if doc_id]
    wanted = [(name, rid) for name, kind in hooks.items() for rid in kind.reapply_for(doc_ids)]
    present = _blobs_at(vault_dir, plan.head, [_curation_relpath(vault_dir, n, r) for n, r in wanted])
    added = 0
    for name, rid in wanted:
        item = (name, rid, "apply")
        if _curation_relpath(vault_dir, name, rid) in present and item not in plan.curation:
            plan.curation.append(item)
            added += 1
    return added


def _curation_counts(items: list[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for kind, _, action in items:
```

**Replace in** `artmind/vault_sync.py` (lines 1232-1237):

```python
        plan = classify_diff(vault_dir, bases["graph"], head, domains)
        unchanged = drop_unchanged(vault_dir, plan)
        curation_unchanged = drop_unchanged_curation(vault_dir, plan)
    else:
        plan = SyncPlan(base=bases["structured"], head=head)
        unchanged = []
```

**with:**

```python
        plan = classify_diff(vault_dir, bases["graph"], head, domains)
        unchanged = drop_unchanged(vault_dir, plan)
        curation_unchanged = drop_unchanged_curation(vault_dir, plan)
        reapply_for_replayed(vault_dir, plan)
    else:
        plan = SyncPlan(base=bases["structured"], head=head)
        unchanged = []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_lifecycle_records.py test/test_vault_sync.py -q`
Expected: `176 passed`. Then the full suite — all pass (`test_entity_retirement.py`'s retire/restore tests included: the label swaps and the rebuild are unchanged; the record write and the mark are additions).

- [ ] **Step 5: Commit**

```bash
git add artmind/curation_records.py artmind/ingest.py artmind/lifecycle.py artmind/lifecycle_records.py artmind/vault_sync.py test/test_lifecycle_records.py test/test_vault_sync.py
git commit -m "feat(lifecycle): a retirement is a curation record, sticky across re-ingest and replay"
```

---

### Task 3: syntheses travel as curation records, applied before the rebuild

`artmind/synthesis_records.py` is the `syntheses` kind (`phase: "pre"`). Its record is the `:Synthesis` node's properties (`id` = entity id, `text`, `observation_set_hash`, `observation_ids`, `created_at`, `model`) plus the entity's `key`; no embedding (decision 6). `apply` writes the node (`SET s = $props`, with `record_fingerprint`) and returns the key, so `vault sync`'s union rebuild — which reads `:Synthesis` back through `projection.load_synthesis_batch` — writes the synthesis as the description and flags the entity for the local embedding sweep. `remove` deletes the node and returns the key. `synthesize_key` writes the record first and applies it inside its existing transaction, before `rebuild_key` and the embedding write.

**Files:**
- Modify: `artmind/curation_records.py`
- Create: `artmind/synthesis_records.py`
- Modify: `artmind/synthesize.py`
- Test: `test/test_synthesis_records.py` (new)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

**Create** `test/test_synthesis_records.py`:

```python
"""A synthesis travels as a curation record (spec 2026-09-26 §15 A10). No
Neo4j: recording sessions stand in (CLAUDE.md §3)."""
import json
from unittest.mock import MagicMock, patch

import pytest

from artmind import curation_records, synthesis_records
from artmind.observations import entity_id


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Tx:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return MagicMock()


_KEY = ("widget rate", "RATE_ENTRY", "banking.reference")
_SYNTHESIS = {"id": entity_id(_KEY), "text": "A clean description.", "observation_set_hash": "h",
              "observation_ids": ["o1", "o2"], "created_at": "t", "model": "m"}


def test_a_synthesis_record_is_the_node_plus_its_key():
    record = synthesis_records.record_from(_KEY, _SYNTHESIS)

    assert record == {**_SYNTHESIS, "key": "widget rate|RATE_ENTRY|banking.reference"}
    assert "embedding" not in record


def test_apply_writes_the_node_and_hands_back_its_key():
    tx = _Tx()
    record = synthesis_records.record_from(_KEY, _SYNTHESIS)

    assert synthesis_records.apply(tx, record, "fp") == {_KEY}
    assert tx.calls == [("MERGE (s:Synthesis {id: $id}) SET s = $props",
                         {"id": entity_id(_KEY), "props": {**record, "record_fingerprint": "fp"}})]


def test_remove_deletes_the_node_and_hands_back_its_key():
    tx = _Tx()

    assert synthesis_records.remove(tx, synthesis_records.record_from(_KEY, _SYNTHESIS)) == {_KEY}
    assert tx.calls == [("MATCH (s:Synthesis {id: $id}) DETACH DELETE s", {"id": entity_id(_KEY)})]
    assert synthesis_records.remove(_Tx(), {"id": "x"}) == set()


def test_the_syntheses_kind_runs_before_the_rebuild():
    kind = curation_records.kinds()["syntheses"]

    assert kind.phase == "pre"
    assert kind.domains(synthesis_records.record_from(_KEY, _SYNTHESIS)) == ["banking.reference"]


def test_synthesize_key_writes_the_record_and_applies_it(monkeypatch, curation_dir):
    from artmind.synthesize import synthesize_key

    monkeypatch.setattr("artmind.synthesize.projection.read_latest_observations",
                        lambda tx, key: [{"id": "o1", "description": "x"}, {"id": "o2"}])
    monkeypatch.setattr("artmind.synthesize.call_llm", lambda model, prompt: "{}")
    monkeypatch.setattr("artmind.synthesize.parse_json_response", lambda raw: {"description": "A clean description."})
    monkeypatch.setattr("artmind.synthesize.embed_text", lambda model, text: [0.1])
    seen = {}
    monkeypatch.setattr("artmind.synthesize.projection.rebuild_key",
                        lambda tx, key, *, synthesis=None, **kw: seen.setdefault("synthesis", synthesis) and "rebuilt")
    tx = _Tx()
    session = MagicMock()
    session.execute_read.side_effect = lambda fn: fn(tx)
    session.execute_write.side_effect = lambda fn: fn(tx)

    with patch("artmind.synthesize.neo4j_session") as ctx:
        ctx.return_value.__enter__.return_value = session
        synthesize_key(_KEY, "Widget Rate", "RATE_ENTRY", model="m", embed_model="e")

    path = curation_dir / "syntheses" / f"{entity_id(_KEY)}.json"
    record = json.loads(path.read_text())
    assert (record["text"], record["key"]) == ("A clean description.", "widget rate|RATE_ENTRY|banking.reference")
    writes = [p for c, p in tx.calls if c == "MERGE (s:Synthesis {id: $id}) SET s = $props"]
    assert writes[0]["props"]["record_fingerprint"] == curation_records.fingerprint(path.read_bytes())
    assert seen["synthesis"]["text"] == "A clean description."
```

**Replace in** `test/test_vault_sync.py` (lines 3690-3692):

```python

    guards = [p for c, p in curation_graph.calls if "RETURN count(d) AS n" in c and "lifecycle_record" in c]
    assert guards == [{"doc_id": "docid-1", "id": _lifecycle("docid-1")["id"]}], "the doc id comes from the record at base"
```

**with:**

```python

    guards = [p for c, p in curation_graph.calls if "RETURN count(d) AS n" in c and "lifecycle_record" in c]
    assert guards == [{"doc_id": "docid-1", "id": _lifecycle("docid-1")["id"]}], "the doc id comes from the record at base"


def test_sync_applies_a_synthesis_before_the_rebuild_and_rebuilds_its_key(repo, monkeypatch, curation_graph):
    """The rebuild reads `:Synthesis` back through its synthesis_loader, so
    the node must exist first -- and its key must be in the rebuild."""
    from artmind import curation_records, synthesis_records
    from artmind.observations import entity_id
    import artmind.table2graph as t2g

    key = ("widget rate", "RATE_ENTRY", "banking")
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _patch_curation_dir(monkeypatch, repo)
    curation_records.write_record("syntheses", synthesis_records.record_from(key, {
        "id": entity_id(key), "text": "Synthesised.", "observation_set_hash": "h",
        "observation_ids": ["o1"], "created_at": "t", "model": "m",
    }))
    _commit_all(repo, "a synthesis from the other machine")
    _patch_ingest_and_projection(monkeypatch)
    order = []
    real_run = curation_graph.run

    def _run(cypher, **params):
        if cypher.startswith("MERGE (s:Synthesis"):
            order.append("synthesis")
        return real_run(cypher, **params)

    curation_graph.run = _run
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: order.append(("rebuild", sorted(keys))) or {})

    vs.sync(repo, bootstrap_empty=True)

    assert order == ["synthesis", ("rebuild", [key])]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_synthesis_records.py test/test_vault_sync.py -q`
Expected: `1 error` during collection — `ImportError: cannot import name 'synthesis_records' from 'artmind'`.

- [ ] **Step 3: Implement**

**Replace in** `artmind/curation_records.py` (lines 76-84):

```python

def kinds() -> dict[str, Kind]:
    """Every registered kind, by directory name, in apply order."""
    from artmind import conflict_records, lifecycle_records, supersession_records

    return {kind.name: kind for kind in (lifecycle_records.KIND, supersession_records.KIND, conflict_records.KIND)}


def curation_dir() -> Path:
```

**with:**

```python

def kinds() -> dict[str, Kind]:
    """Every registered kind, by directory name, in apply order."""
    from artmind import conflict_records, lifecycle_records, supersession_records, synthesis_records

    return {
        kind.name: kind
        for kind in (lifecycle_records.KIND, supersession_records.KIND, synthesis_records.KIND, conflict_records.KIND)
    }


def curation_dir() -> Path:
```

**Create** `artmind/synthesis_records.py`:

```python
"""Synthesis records -- the `syntheses` curation kind (spec 2026-09-26 §7,
D6; §15 A10).

`projection synthesize` asks an LLM to rewrite an entity's description from
all its observations and stores the result as a `:Synthesis {id: entity id}`
node, which every projection rebuild reads back (`synthesis_loader`). It
lived only in the graph, so a machine with its own Neo4j paid for it again --
or never had it. Each synthesis is now also a file,
`.artmind/data/curation/syntheses/<entity id>.json`: the node's properties
plus the entity's aggregate `key`.

No embedding is stored: the entity's vector is derived (description + local
model). A rebuild that applies the synthesis flags the entity
`embedding_stale`, and `vault sync`'s entity sweep re-embeds it locally --
the same reason chunk vectors live in a gitignored sidecar.

Applied before `vault sync`'s union rebuild ("pre"), with the entity's key
joining it, so the rebuilt description is the synthesis. Removing the file
removes the node and rebuilds the key, falling back to the winning
observation's description.
"""
from __future__ import annotations

from artmind.curation_records import Kind

NAME = "syntheses"

_APPLY = "MERGE (s:Synthesis {id: $id}) SET s = $props"
_REMOVE = "MATCH (s:Synthesis {id: $id}) DETACH DELETE s"
_READ_FINGERPRINTS = (
    "MATCH (s:Synthesis) WHERE s.id IN $ids "
    "RETURN s.id AS id, s.record_fingerprint AS fingerprint"
)


def record_from(key: tuple[str, str, str], synthesis: dict) -> dict:
    """The record for one synthesis: its node properties and the key."""
    from artmind.observations import key_string

    return {**synthesis, "key": key_string(key)}


def _key(record: dict) -> set:
    parts = str(record.get("key") or "").split("|")
    return {tuple(parts)} if len(parts) == 3 else set()


def apply(tx, record: dict, fingerprint: str) -> set:
    """Write the `:Synthesis` node from the record; its key must be rebuilt
    for the description to follow."""
    tx.run(_APPLY, id=record["id"], props={**record, "record_fingerprint": fingerprint})
    return _key(record)


def remove(tx, record: dict) -> set:
    tx.run(_REMOVE, id=record["id"])
    return _key(record)


def read_fingerprints(ids: list[str], *, timeout: float | None = None) -> dict:
    from artmind import graph_query

    if not ids:
        return {}
    with graph_query.neo4j_session(timeout=timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, ids=sorted(set(ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def domains(record: dict) -> list:
    return [key[2] for key in _key(record)]


KIND = Kind(name=NAME, phase="pre", apply=apply, remove=remove, read_fingerprints=read_fingerprints, domains=domains)
```

**Replace in** `artmind/synthesize.py` (lines 164-173):

```python
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }

    def _write(tx):
        tx.run("MERGE (s:Synthesis {id: $id}) SET s = $props", id=eid, props=synthesis)
        outcome = projection.rebuild_key(tx, key, synthesis=synthesis)
        # rebuild_key already wrote description = synthesis["text"] and
        # flagged embedding_stale = true (the description changed). Overwrite
        # both here, in the SAME transaction — the embedding is never null
```

**with:**

```python
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }
    # The synthesis travels (spec 2026-09-26 §15 A10): its record file is
    # written first, and the node is written from it -- the same path `vault
    # sync` applies on every other machine, which saves the LLM call there.
    from artmind import curation_records, synthesis_records

    record = synthesis_records.record_from(key, synthesis)
    fingerprint = curation_records.write_record(synthesis_records.NAME, record)

    def _write(tx):
        synthesis_records.apply(tx, record, fingerprint)
        outcome = projection.rebuild_key(tx, key, synthesis=record)
        # rebuild_key already wrote description = synthesis["text"] and
        # flagged embedding_stale = true (the description changed). Overwrite
        # both here, in the SAME transaction — the embedding is never null
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_synthesis_records.py test/test_vault_sync.py -q`
Expected: `173 passed`. Then the full suite — all pass (`test_synthesize.py` included: the `:Synthesis` write still matches `MERGE (s:Synthesis` with the same `text`).

- [ ] **Step 5: Commit**

```bash
git add artmind/curation_records.py artmind/synthesis_records.py artmind/synthesize.py test/test_synthesis_records.py test/test_vault_sync.py
git commit -m "feat(synthesize): each synthesis is a curation record, applied before vault sync's rebuild"
```

---

### Task 4: registry-only curation commands re-export the table's `.meta.json`

Decision 7. `export_structured_text` gains `meta_only`: the `.meta.json` of every requested table is rewritten, the CSV only when it does not exist yet (and DuckDB is not opened when no CSV is written). `pipeline._export_text_best_effort` passes it through. A new `cli._reexport_table_meta(table_id)` calls it best-effort after `db grain --set`, `db mappings --acceptProposed`, `db mappings set/confirm/clear`, `db bridge confirm/clear` and `db propose` — after the registry write, so a failed export never fails the command.

**Files:**
- Modify: `artmind/cli.py`
- Modify: `artmind/structured/pipeline.py`
- Modify: `artmind/structured/text_export.py`
- Test: `test/test_db_mappings_cli.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_db_mappings_cli.py` (lines 237-239):

```python
# row rather than overwriting the first (see register_table in
# artmind/structured/registry.py). _resolve_table_id's ambiguity guard exists
# to handle exactly that case, but isn't exercised here.
```

**with:**

```python
# row rather than overwriting the first (see register_table in
# artmind/structured/registry.py). _resolve_table_id's ambiguity guard exists
# to handle exactly that case, but isn't exercised here.


# ── a registry-only curation change travels as the table's .meta.json ────────


import json


def _meta(tmp_path=None):
    import paths

    return json.loads((paths.STRUCTURED_TEXT_DIR / "banking" / "products.meta.json").read_text())


def _csv_mtime():
    import paths

    return (paths.STRUCTURED_TEXT_DIR / "banking" / "products.csv").stat().st_mtime_ns


def test_confirming_a_mapping_re_exports_only_the_tables_meta(ingested):
    import artmind.cli as cli

    before = _csv_mtime()
    result = CliRunner().invoke(
        cli.cli, ["db", "mappings", "products", "confirm", "--column", "name", "--entityClass", "PRODUCT"],
    )

    assert result.exit_code == 0, result.output
    assert [(m["column"], m["entity_class"], m["confirmed"]) for m in _meta()["column_mappings"]] == [
        ("name", "PRODUCT", 1)
    ]
    assert _csv_mtime() == before, "a curation change never re-dumps the rows"


def test_set_clear_and_accept_proposed_re_export_the_meta(ingested):
    import artmind.cli as cli

    runner = CliRunner()
    runner.invoke(cli.cli, ["db", "mappings", "products", "set", "--column", "id", "--entityClass", "PRODUCT_ID"])
    assert ("id", "PRODUCT_ID", 1) in [(m["column"], m["entity_class"], m["confirmed"]) for m in _meta()["column_mappings"]]

    runner.invoke(cli.cli, ["db", "mappings", "products", "--acceptProposed"])
    assert all(m["confirmed"] == 1 for m in _meta()["column_mappings"])

    runner.invoke(cli.cli, ["db", "mappings", "products", "clear"])
    assert _meta()["column_mappings"] == []


def test_confirming_grain_and_bridge_columns_re_exports_the_meta(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    table = registry.get_table("products", domain="banking")
    registry.upsert_column_role(table["id"], "name", "term", 0.9, confirmed=False)
    runner = CliRunner()

    runner.invoke(cli.cli, ["db", "grain", "products", "--set", "lookup"])
    assert (_meta()["table"]["grain"], _meta()["table"]["grain_confirmed"]) == ("lookup", 1)

    runner.invoke(cli.cli, ["db", "bridge", "confirm", "--table", "products", "--column", "name"])
    assert [(r["column"], r["confirmed"]) for r in _meta()["column_roles"]] == [("name", 1)]

    runner.invoke(cli.cli, ["db", "bridge", "clear", "--table", "products"])
    assert _meta()["column_roles"] == []


def test_re_proposing_classifications_re_exports_the_meta(ingested, monkeypatch):
    import artmind.cli as cli
    from artmind.structured import registry

    def _propose(table_id, domain, steps=None, redo=False, model=None):
        registry.upsert_mapping(table_id, "id", "PRODUCT_ID", 0.7, confirmed=False)
        return {"steps": ["mapping"]}

    monkeypatch.setattr("artmind.structured.semantics.propose_table_semantics", _propose)

    result = CliRunner().invoke(cli.cli, ["db", "propose", "products"])

    assert result.exit_code == 0, result.output
    assert ("id", "PRODUCT_ID", 0) in [(m["column"], m["entity_class"], m["confirmed"]) for m in _meta()["column_mappings"]]


def test_a_meta_only_export_still_writes_a_missing_csv(ingested):
    import paths
    from artmind.structured import registry, text_export

    csv = paths.STRUCTURED_TEXT_DIR / "banking" / "products.csv"
    csv.unlink()

    text_export.export_structured_text(tables=[registry.get_table("products", domain="banking")], meta_only=True)

    assert csv.is_file()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_db_mappings_cli.py -q`
Expected: `5 failed, 18 passed` — the meta file still holds the ingest-time classifications (`AssertionError` on `column_mappings`/`grain`/`column_roles`), and `export_structured_text()` rejects `meta_only` (`TypeError: ... unexpected keyword argument 'meta_only'`). The `WARNING ... A test reached artmind.extraction.call_llm` lines are the ingest fixture's classification step being blocked by the conftest, as in every run of this file.

- [ ] **Step 3: Implement**

**Replace in** `artmind/cli.py` (lines 1750-1755):

```python
    return _resolve_table_row(table_name, domain)["id"]


def _ctx_table_id(ctx: click.Context) -> int:
    """Resolve (once, then cache) the TABLE that ``_TableFirstGroup`` peeled off.

```

**with:**

```python
    return _resolve_table_row(table_name, domain)["id"]


def _reexport_table_meta(table_id: int) -> None:
    """After a registry-only curation change (grain, bridge columns,
    column->entityClass mappings, re-proposed classifications), rewrite that
    table's `.meta.json` so the change travels with Obsidian Git's next
    commit (spec 2026-09-26 §15 A10). Best-effort, exactly like an ingest's
    own export: the registry write already happened and must not fail."""
    from artmind.structured.pipeline import _export_text_best_effort

    row = structured_registry.get_table_by_id(table_id)
    if row is not None:
        _export_text_best_effort(row["domain"], [row["table_name"]], meta_only=True)


def _ctx_table_id(ctx: click.Context) -> int:
    """Resolve (once, then cache) the TABLE that ``_TableFirstGroup`` peeled off.

```

**Replace in** `artmind/cli.py` (lines 1788-1793):

```python
                structured_registry.set_mapping_confirmed(
                    table_id, m["column"], m["entity_class"], True
                )
    _echo_json({"table": table, "mappings": structured_registry.list_mappings(table_id)}, compact)


```

**with:**

```python
                structured_registry.set_mapping_confirmed(
                    table_id, m["column"], m["entity_class"], True
                )
        _reexport_table_meta(table_id)
    _echo_json({"table": table, "mappings": structured_registry.list_mappings(table_id)}, compact)


```

**Replace in** `artmind/cli.py` (lines 1801-1806):

```python
    """Upsert a confirmed column-to-entityClass mapping."""
    table_id = _ctx_table_id(ctx)
    structured_registry.upsert_mapping(table_id, column, entity_class, confidence, confirmed=True)
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )
```

**with:**

```python
    """Upsert a confirmed column-to-entityClass mapping."""
    table_id = _ctx_table_id(ctx)
    structured_registry.upsert_mapping(table_id, column, entity_class, confidence, confirmed=True)
    _reexport_table_meta(table_id)
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )
```

**Replace in** `artmind/cli.py` (lines 1826-1831):

```python
        raise click.ClickException(
            f"no mapping found for column '{column}' / entityClass '{entity_class}'"
        )
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )
```

**with:**

```python
        raise click.ClickException(
            f"no mapping found for column '{column}' / entityClass '{entity_class}'"
        )
    _reexport_table_meta(table_id)
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )
```

**Replace in** `artmind/cli.py` (lines 1846-1851):

```python
    """
    table_id = _ctx_table_id(ctx)
    structured_registry.clear_mappings(table_id, column)
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )
```

**with:**

```python
    """
    table_id = _ctx_table_id(ctx)
    structured_registry.clear_mappings(table_id, column)
    _reexport_table_meta(table_id)
    _echo_json(
        {"table": ctx.obj["table"], "mappings": structured_registry.list_mappings(table_id)}, compact
    )
```

**Replace in** `artmind/cli.py` (lines 1902-1907):

```python
            f"no bridge column '{column}' on table '{table}' — run 'db grain {table}'"
            " to see which columns have a bridge role proposed"
        )
    _echo_json(
        {
            "table": row["table_name"],
```

**with:**

```python
            f"no bridge column '{column}' on table '{table}' — run 'db grain {table}'"
            " to see which columns have a bridge role proposed"
        )
    _reexport_table_meta(row["id"])
    _echo_json(
        {
            "table": row["table_name"],
```

**Replace in** `artmind/cli.py` (lines 1926-1931):

```python
    """
    row = _resolve_table_row(table, domain)
    structured_registry.clear_column_roles(row["id"], column)
    _echo_json(
        {
            "table": row["table_name"],
```

**with:**

```python
    """
    row = _resolve_table_row(table, domain)
    structured_registry.clear_column_roles(row["id"], column)
    _reexport_table_meta(row["id"])
    _echo_json(
        {
            "table": row["table_name"],
```

**Replace in** `artmind/cli.py` (lines 2013-2018):

```python
    result = propose_table_semantics(
        row["id"], row["domain"], steps=list(steps) or None, redo=redo, model=model
    )
    _echo_json(result, compact)


```

**with:**

```python
    result = propose_table_semantics(
        row["id"], row["domain"], steps=list(steps) or None, redo=redo, model=model
    )
    _reexport_table_meta(row["id"])
    _echo_json(result, compact)


```

**Replace in** `artmind/cli.py` (lines 2034-2039):

```python
            structured_registry.set_grain(row["id"], grain, confirmed=True)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        row = structured_registry.get_table_by_id(row["id"])
    _echo_json(
        {
```

**with:**

```python
            structured_registry.set_grain(row["id"], grain, confirmed=True)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        _reexport_table_meta(row["id"])
        row = structured_registry.get_table_by_id(row["id"])
    _echo_json(
        {
```

**Replace in** `artmind/structured/pipeline.py` (lines 339-358):

```python
        logger.warning("structured pipeline: catalogue projection failed for domain '{}': {}", domain, e)


def _export_text_best_effort(domain: str, table_names: list[str]) -> None:
    """Re-export ``table_names``' CSV + per-table .meta.json into the vault's
    structured_text dir (``structured/text_export.py`` -- the git-commitable
    text ``db reindex`` rebuilds parquet + registry rows from). Best-effort for
    the same reason as ``_project_catalogue_best_effort``: a write to the
    vault's working tree must never fail the ingest that produced it. artmind
    does not commit it (spec 2026-09-26, D1) -- Obsidian Git does."""
    try:
        from artmind.structured import text_export

        tables = [t for name in table_names if (t := registry.get_table(name, domain=domain)) is not None]
        if not tables:
            return
        text_export.export_structured_text(tables=tables)
    except Exception as e:
        logger.warning("structured pipeline: text export failed for domain '{}': {}", domain, e)

```

**with:**

```python
        logger.warning("structured pipeline: catalogue projection failed for domain '{}': {}", domain, e)


def _export_text_best_effort(domain: str, table_names: list[str], *, meta_only: bool = False) -> None:
    """Re-export ``table_names``' CSV + per-table .meta.json into the vault's
    structured_text dir (``structured/text_export.py`` -- the git-commitable
    text ``db reindex`` rebuilds parquet + registry rows from). Best-effort for
    the same reason as ``_project_catalogue_best_effort``: a write to the
    vault's working tree must never fail the ingest that produced it. artmind
    does not commit it (spec 2026-09-26, D1) -- Obsidian Git does.

    ``meta_only=True`` -- a registry-only curation change -- rewrites just the
    ``.meta.json`` of a table whose CSV is already exported."""
    try:
        from artmind.structured import text_export

        tables = [t for name in table_names if (t := registry.get_table(name, domain=domain)) is not None]
        if not tables:
            return
        text_export.export_structured_text(tables=tables, meta_only=meta_only)
    except Exception as e:
        logger.warning("structured pipeline: text export failed for domain '{}': {}", domain, e)

```

**Replace in** `artmind/structured/text_export.py` (lines 217-226):

```python
    return dump


def export_structured_text(dest_dir: Path | None = None, *, tables: list[dict] | None = None) -> dict:
    """Write CSV + per-table `.meta.json` for `tables` (default: every registered
    table) to `dest_dir` (default: `paths.STRUCTURED_TEXT_DIR`).

    Row order is `ORDER BY ALL` (every column, left to right) -- deterministic
    across re-exports of unchanged data, so a re-export that changed nothing
    produces a byte-identical CSV and git sees no change, instead of a
```

**with:**

```python
    return dump


def export_structured_text(
    dest_dir: Path | None = None, *, tables: list[dict] | None = None, meta_only: bool = False
) -> dict:
    """Write CSV + per-table `.meta.json` for `tables` (default: every registered
    table) to `dest_dir` (default: `paths.STRUCTURED_TEXT_DIR`).

    `meta_only=True` rewrites only the `.meta.json` of a table whose CSV is
    already there -- what a registry-only curation change (`db grain`, `db
    mappings`, `db bridge`, `db propose`) needs, without re-dumping every
    row. A table with no CSV yet still gets one, so it travels at all.

    Row order is `ORDER BY ALL` (every column, left to right) -- deterministic
    across re-exports of unchanged data, so a re-export that changed nothing
    produces a byte-identical CSV and git sees no change, instead of a
```

**Replace in** `artmind/structured/text_export.py` (lines 235-246):

```python
    dest_dir.mkdir(parents=True, exist_ok=True)

    target_tables = tables if tables is not None else registry.list_tables()

    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())

    written: list[Path] = []
    for table in target_tables:
        csv_path = _table_csv_path(dest_dir, table["domain"], table["table_name"])
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        view = view_name(table["domain"], table["table_name"])
```

**with:**

```python
    dest_dir.mkdir(parents=True, exist_ok=True)

    target_tables = tables if tables is not None else registry.list_tables()
    csv_tables = [
        t for t in target_tables
        if not meta_only or not _table_csv_path(dest_dir, t["domain"], t["table_name"]).is_file()
    ]

    if csv_tables:
        ds = DuckDBDatasource()
        ds.ensure_views(registry.list_tables())

    written: list[Path] = []
    for table in csv_tables:
        csv_path = _table_csv_path(dest_dir, table["domain"], table["table_name"])
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        view = view_name(table["domain"], table["table_name"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_db_mappings_cli.py -q`
Expected: `23 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py artmind/structured/pipeline.py artmind/structured/text_export.py test/test_db_mappings_cli.py
git commit -m "feat(structured): registry-only curation commands re-export the table's .meta.json"
```

---

### Task 5: a curation-only `.meta.json` change restores registry rows alone; the catalogue follows

Decisions 8 and 9. `classify_diff` splits the tables track B would regenerate: `_split_curation_only` compares, per table, the CSV's blob id at base and HEAD (`_oids_at`, no content read) and the two `.meta.json` versions with the curation fields dropped (`_meta_without_curation`); a table whose rows are unchanged and whose meta differs only there goes to `plan.curate_tables`. A mapping or schema change on the same table moves it back to a full regenerate. `sync()` applies the split for the structured store's own range too, restores a curation-only table with `import_structured_text(..., registry_only=True)` (registry rows only, its parquet kept) — or fully when this machine has no parquet for it — never runs `table2graph` for it, and reports `curated_tables` in dry and real runs. When the graph is in the run, it re-projects the catalogue (`pipeline._project_catalogue_best_effort`) for every domain whose registry rows it restored.

**Files:**
- Modify: `artmind/structured/text_export.py`
- Modify: `artmind/vault_sync.py`
- Test: `test/test_structured_text_export.py`
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

**Replace in** `test/test_structured_text_export.py` (lines 839-841):

```python
    assert _git_out(tmp_path, "diff", "--cached", "--name-only") == "", "artmind must not stage anything"
    untracked = set(_git_out(tmp_path, "ls-files", "-z", "--others", "--exclude-standard").split("\0"))
    assert {"structured_text/banking/products.csv", "structured_text/banking/products.meta.json"} <= untracked
```

**with:**

```python
    assert _git_out(tmp_path, "diff", "--cached", "--name-only") == "", "artmind must not stage anything"
    untracked = set(_git_out(tmp_path, "ls-files", "-z", "--others", "--exclude-standard").split("\0"))
    assert {"structured_text/banking/products.csv", "structured_text/banking/products.meta.json"} <= untracked


def test_a_registry_only_import_restores_curation_and_keeps_the_parquet(tmp_path, monkeypatch):
    """`vault sync`'s curation-only case: the classification changed on
    another machine, the rows did not."""
    import csv as _csv

    import artmind.db as db
    import paths
    from artmind.structured import registry, text_export
    from artmind.structured.duckdb_adapter import parquet_path_for
    from artmind.structured.pipeline import ingest_structured_file

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    db._init_db()
    source = tmp_path / "products.csv"
    with open(source, "w", newline="") as f:
        _csv.writer(f).writerows([["id", "name"], [1, "Widget"]])
    ingest_structured_file(source, "banking")
    table = registry.get_table("products", domain="banking")
    registry.upsert_mapping(table["id"], "name", "PRODUCT", 1.0, confirmed=True)
    text_export.export_structured_text(tables=[registry.get_table("products", domain="banking")], meta_only=True)
    registry.clear_mappings(table["id"], None)   # this machine's registry, before the sync
    parquet = parquet_path_for("banking", "products")
    before = parquet.stat().st_mtime_ns

    text_export.import_structured_text(paths.STRUCTURED_TEXT_DIR, tables=[("banking", "products")], registry_only=True)

    restored = registry.get_table("products", domain="banking")
    assert [(m["column"], m["confirmed"]) for m in registry.list_mappings(restored["id"])] == [("name", 1)]
    assert parquet.stat().st_mtime_ns == before
```

**Replace in** `test/test_vault_sync.py` (lines 3723-3725):

```python
    vs.sync(repo, bootstrap_empty=True)

    assert order == ["synthesis", ("rebuild", [key])]
```

**with:**

```python
    vs.sync(repo, bootstrap_empty=True)

    assert order == ["synthesis", ("rebuild", [key])]


# ── a curation-only .meta.json change restores registry rows alone ──────────


def _meta(grain="instance", mappings=(), rows=3):
    import json

    return json.dumps({
        "table": {"domain": "banking", "table_name": "accounts", "grain": grain, "grain_confirmed": 1,
                  "mapping_status": "ok", "row_count": rows},
        "columns": [{"name": "id", "dtype": "BIGINT"}],
        "column_mappings": [{"column": c, "entity_class": e, "confirmed": 1} for c, e in mappings],
        "column_roles": [],
    }, sort_keys=True)


def _table_base(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "banking" / "accounts.meta.json").write_text(_meta())
    _commit_all(repo, "a table")
    return st_dir, vs.head_sha(repo)


def test_classify_diff_marks_a_curation_only_meta_change_as_curation(repo, monkeypatch):
    st_dir, base = _table_base(repo, monkeypatch)
    (st_dir / "banking" / "accounts.meta.json").write_text(_meta(grain="lookup", mappings=[("id", "ACCOUNT")]))
    _commit_all(repo, "db grain / db mappings")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert (plan.regenerate_tables, plan.curate_tables) == ([], [("banking", "accounts")])


def test_a_meta_change_beyond_curation_still_regenerates(repo, monkeypatch):
    st_dir, base = _table_base(repo, monkeypatch)
    (st_dir / "banking" / "accounts.meta.json").write_text(_meta(grain="lookup", rows=4))
    _commit_all(repo, "a refresh changed the row count")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert (plan.regenerate_tables, plan.curate_tables) == ([("banking", "accounts")], [])


def test_changed_rows_with_curation_still_regenerate(repo, monkeypatch):
    st_dir, base = _table_base(repo, monkeypatch)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n2\n")
    (st_dir / "banking" / "accounts.meta.json").write_text(_meta(grain="lookup"))
    _commit_all(repo, "rows and grain")

    plan = vs.classify_diff(repo, base, vs.head_sha(repo))

    assert (plan.regenerate_tables, plan.curate_tables) == ([("banking", "accounts")], [])


def _patch_curation_track_b(monkeypatch, *, parquet_exists=True):
    calls = _patch_track_b(monkeypatch)
    calls["registry_only"] = []
    calls["catalogue"] = []

    def _import(*a, **k):
        (calls["registry_only"] if k.get("registry_only") else calls["import"]).append(list(k.get("tables")))
        return {"tables_loaded": 1}

    monkeypatch.setattr("artmind.structured.text_export.import_structured_text", _import)
    monkeypatch.setattr(
        "artmind.structured.duckdb_adapter.parquet_path_for",
        lambda domain, table: type("P", (), {"is_file": lambda self: parquet_exists})(),
    )
    monkeypatch.setattr(
        "artmind.structured.pipeline._project_catalogue_best_effort", lambda d: calls["catalogue"].append(d),
    )
    _patch_ingest_and_projection(monkeypatch)
    return calls


def test_sync_restores_only_registry_rows_for_a_curation_only_change(repo, monkeypatch):
    st_dir, base = _table_base(repo, monkeypatch)
    (st_dir / "banking" / "accounts.meta.json").write_text(_meta(grain="lookup"))
    _commit_all(repo, "db grain --set lookup")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    calls = _patch_curation_track_b(monkeypatch)

    dry = vs.sync(repo, dry_run=True)
    result = vs.sync(repo)

    assert calls["registry_only"] == [[("banking", "accounts")]]
    assert calls["import"] == [] and calls["table_to_graph"] == [], "no parquet rewrite, no re-projection"
    assert (dry["curated_tables"], dry["regenerate_tables"]) == (result["curated_tables"], result["regenerated_tables"]) == (1, 0)
    assert calls["catalogue"] == ["banking"], "the graph's catalogue follows the registry"


def test_a_curation_only_change_for_a_table_with_no_parquet_here_restores_it_fully(repo, monkeypatch):
    st_dir, base = _table_base(repo, monkeypatch)
    (st_dir / "banking" / "accounts.meta.json").write_text(_meta(grain="lookup"))
    _commit_all(repo, "db grain --set lookup")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})
    calls = _patch_curation_track_b(monkeypatch, parquet_exists=False)

    vs.sync(repo)

    assert (calls["import"], calls["registry_only"]) == ([[("banking", "accounts")]], [])


def test_a_structured_only_run_never_projects_the_catalogue(repo, monkeypatch):
    st_dir, base = _table_base(repo, monkeypatch)
    (st_dir / "banking" / "accounts.meta.json").write_text(_meta(grain="lookup"))
    _commit_all(repo, "db grain --set lookup")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_structured_commit": base})
    calls = _patch_curation_track_b(monkeypatch)

    vs.sync(repo, store="structured")

    assert calls["registry_only"] == [[("banking", "accounts")]]
    assert calls["catalogue"] == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_structured_text_export.py test/test_vault_sync.py -q`
Expected: `6 failed, 195 passed` — `TypeError: import_structured_text() got an unexpected keyword argument 'registry_only'`; the three classifier tests with `AttributeError: 'SyncPlan' object has no attribute 'curate_tables'`; the two `sync()` tests with `AssertionError: assert [] == [[('banking', 'accounts')]]` (no registry-only restore happened). `test_a_curation_only_change_for_a_table_with_no_parquet_here_restores_it_fully` already passes — it pins that the full restore stays the fallback.

- [ ] **Step 3: Implement**

**Replace in** `artmind/structured/text_export.py` (lines 289-295):

```python


def import_structured_text(
    src_dir: Path | None = None, *, tables: list[tuple[str, str]] | None = None
) -> dict:
    """Wipe and rebuild the structured store from `src_dir`'s CSV + per-table
    `.meta.json` (legacy `manifest.json` as a fallback).
```

**with:**

```python


def import_structured_text(
    src_dir: Path | None = None, *, tables: list[tuple[str, str]] | None = None, registry_only: bool = False
) -> dict:
    """Wipe and rebuild the structured store from `src_dir`'s CSV + per-table
    `.meta.json` (legacy `manifest.json` as a fallback).
```

**Replace in** `artmind/structured/text_export.py` (lines 309-314):

```python
    `read_csv` matches that map to the file *positionally*, not by name, so a
    mismatch would silently read every column as the wrong type instead of
    raising.
    """
    src_dir = Path(src_dir) if src_dir else paths.STRUCTURED_TEXT_DIR
    manifest = load_structured_dump(src_dir)
```

**with:**

```python
    `read_csv` matches that map to the file *positionally*, not by name, so a
    mismatch would silently read every column as the wrong type instead of
    raising.

    `registry_only=True` (with `tables`) restores only the registry rows --
    a table's classifications changed but not its rows (`vault sync`'s
    curation-only case) -- and leaves an existing parquet file as it is; a
    table with no parquet yet is still loaded from its CSV.
    """
    src_dir = Path(src_dir) if src_dir else paths.STRUCTURED_TEXT_DIR
    manifest = load_structured_dump(src_dir)
```

**Replace in** `artmind/structured/text_export.py` (lines 340-345):

```python
    loaded = 0
    skipped: list[str] = []
    for table in target_rows:
        csv_path = _table_csv_path(src_dir, table["domain"], table["table_name"])
        if not csv_path.is_file():
            logger.warning(
```

**with:**

```python
    loaded = 0
    skipped: list[str] = []
    for table in target_rows:
        if registry_only and Path(table["parquet_path"]).is_file():
            continue
        csv_path = _table_csv_path(src_dir, table["domain"], table["table_name"])
        if not csv_path.is_file():
            logger.warning(
```

**Replace in** `artmind/vault_sync.py` (lines 264-269):

```python
    same_as_keys: list[tuple[str, str, str]] = field(default_factory=list)  # track D: keys to rebuild
    same_as_groups: int = 0                                                 # track D: groups added/removed/changed
    curation: list[tuple[str, str, str]] = field(default_factory=list)      # track C: (kind, record_id, "apply"|"remove")


def _in_scope(domain: str, domains: list[str] | None) -> bool:
```

**with:**

```python
    same_as_keys: list[tuple[str, str, str]] = field(default_factory=list)  # track D: keys to rebuild
    same_as_groups: int = 0                                                 # track D: groups added/removed/changed
    curation: list[tuple[str, str, str]] = field(default_factory=list)      # track C: (kind, record_id, "apply"|"remove")
    curate_tables: list[tuple[str, str]] = field(default_factory=list)      # track B: registry-only (curation) changes


def _in_scope(domain: str, domains: list[str] | None) -> bool:
```

**Replace in** `artmind/vault_sync.py` (lines 589-594):

```python
    return regenerate, retract


def _mapping_at(vault_dir: Path, rev: str, relpath: str):
    """The table mapping at `relpath` as committed at `rev` -- never the
    working tree (spec 2026-09-26 §14 A1). Raises `MappingError` (a
```

**with:**

```python
    return regenerate, retract


#: What a registry-only curation command changes in a table's `.meta.json`
#: (`db grain`, `db mappings`, `db bridge`, `db propose`).
_CURATION_TABLE_FIELDS = ("grain", "grain_confirmed", "grain_status", "bridge_status", "mapping_status")
_CURATION_META_LISTS = ("column_mappings", "column_roles")


def _meta_without_curation(data: bytes | None):
    """A `.meta.json` with its curation fields dropped, for comparison; None
    when it is not a JSON object."""
    import json

    try:
        meta = json.loads(data.decode("utf-8")) if data is not None else None
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    meta = {k: v for k, v in meta.items() if k not in _CURATION_META_LISTS}
    if isinstance(meta.get("table"), dict):
        meta["table"] = {k: v for k, v in meta["table"].items() if k not in _CURATION_TABLE_FIELDS}
    return meta


def _split_curation_only(
    vault_dir: Path, base: str, head: str, tables: list[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """`(full, curation_only)` for tables track B would regenerate. A table
    whose CSV is the same blob at `base` and `head` and whose `.meta.json`
    changed only in curation fields (grain, bridge columns, column
    mappings, their proposal statuses) needs its registry rows restored --
    not its rows re-imported into parquet, nor its graph projection rebuilt
    (`table2graph` reads neither)."""
    import paths

    if not tables or base == EMPTY_TREE_SHA:
        return list(tables), []
    try:
        st_rel = Path(paths.STRUCTURED_TEXT_DIR).relative_to(vault_dir)
    except ValueError:
        return list(tables), []
    csv = {t: str(st_rel / t[0] / f"{t[1]}.csv") for t in tables}
    meta = {t: str(st_rel / t[0] / f"{t[1]}{_META_SUFFIX}") for t in tables}
    base_oids = _oids_at(vault_dir, base, list(csv.values()))
    head_oids = _oids_at(vault_dir, head, list(csv.values()))
    base_meta = _blobs_at(vault_dir, base, list(meta.values()))
    head_meta = _blobs_at(vault_dir, head, list(meta.values()))
    full, curation_only = [], []
    for t in tables:
        same_rows = csv[t] in head_oids and base_oids.get(csv[t]) == head_oids[csv[t]]
        before, after = base_meta.get(meta[t]), head_meta.get(meta[t])
        if (
            same_rows and before is not None and after is not None and before != after
            and _meta_without_curation(before) is not None
            and _meta_without_curation(before) == _meta_without_curation(after)
        ):
            curation_only.append(t)
        else:
            full.append(t)
    return full, curation_only


def _mapping_at(vault_dir: Path, rev: str, relpath: str):
    """The table mapping at `relpath` as committed at `rev` -- never the
    working tree (spec 2026-09-26 §14 A1). Raises `MappingError` (a
```

**Replace in** `artmind/vault_sync.py` (lines 819-828):

```python
    return str(_curation_rel(vault_dir) / kind / f"{record_id}.json")


def _blobs_at(vault_dir: Path, rev: str, relpaths: list[str]) -> dict[str, bytes]:
    """`{relpath: committed bytes}` for the files among `relpaths` that exist
    at `rev`: one `git ls-tree` and one `git cat-file --batch`, raw blobs
    (the bytes `curation_records.fingerprint` hashes)."""
    if not relpaths:
        return {}
    listing = _git(vault_dir, ["ls-tree", "-r", "-z", rev, "--", *relpaths])
```

**with:**

```python
    return str(_curation_rel(vault_dir) / kind / f"{record_id}.json")


def _oids_at(vault_dir: Path, rev: str, relpaths: list[str]) -> dict[str, str]:
    """`{relpath: blob oid}` for the files among `relpaths` that exist at
    `rev` -- one `git ls-tree`, no content read."""
    if not relpaths:
        return {}
    listing = _git(vault_dir, ["ls-tree", "-r", "-z", rev, "--", *relpaths])
```

**Replace in** `artmind/vault_sync.py` (lines 834-839):

```python
        fields = meta.split(" ")
        if len(fields) == 3 and fields[1] == "blob":
            oid_of[path] = fields[2]
    blobs = _cat_blobs(vault_dir, list(oid_of.values()))
    return {path: blobs[oid] for path, oid in oid_of.items() if oid in blobs}

```

**with:**

```python
        fields = meta.split(" ")
        if len(fields) == 3 and fields[1] == "blob":
            oid_of[path] = fields[2]
    return oid_of


def _blobs_at(vault_dir: Path, rev: str, relpaths: list[str]) -> dict[str, bytes]:
    """`{relpath: committed bytes}` for the files among `relpaths` that exist
    at `rev`: one `git ls-tree` and one `git cat-file --batch`, raw blobs
    (the bytes `curation_records.fingerprint` hashes)."""
    oid_of = _oids_at(vault_dir, rev, relpaths)
    blobs = _cat_blobs(vault_dir, list(oid_of.values()))
    return {path: blobs[oid] for path, oid in oid_of.items() if oid in blobs}

```

**Replace in** `artmind/vault_sync.py` (lines 996-1004):

```python
    (`table:<domain>:<table>` no mapping covers any more) in the same run."""
    plan = SyncPlan(base=base, head=head)
    plan.replay_docs, kg_retract = _classify_kg_diff(vault_dir, base, head, domains)
    plan.regenerate_tables, table_retract = _classify_structured_text_diff(vault_dir, base, head, domains)
    source_regenerate, source_retract = _classify_table_sources(vault_dir, base, head, domains)
    for key in source_regenerate:
        if key not in plan.regenerate_tables:
            plan.regenerate_tables.append(key)

```

**with:**

```python
    (`table:<domain>:<table>` no mapping covers any more) in the same run."""
    plan = SyncPlan(base=base, head=head)
    plan.replay_docs, kg_retract = _classify_kg_diff(vault_dir, base, head, domains)
    changed_tables, table_retract = _classify_structured_text_diff(vault_dir, base, head, domains)
    plan.regenerate_tables, plan.curate_tables = _split_curation_only(vault_dir, base, head, changed_tables)
    source_regenerate, source_retract = _classify_table_sources(vault_dir, base, head, domains)
    for key in source_regenerate:
        if key in plan.curate_tables:
            plan.curate_tables.remove(key)
        if key not in plan.regenerate_tables:
            plan.regenerate_tables.append(key)

```

**Replace in** `artmind/vault_sync.py` (lines 1263-1275):

```python
    project_tables = list(plan.regenerate_tables)
    if "structured" not in stores:
        structured_tables: list[tuple[str, str]] = []
    elif "graph" in stores and bases["structured"] == bases["graph"]:
        structured_tables = list(project_tables)
    else:
        structured_tables, _ = _classify_structured_text_diff(vault_dir, bases["structured"], head, domains)
    # A projected table is restored too, so table2graph reads DuckDB at
    # `head` whatever the structured bookmark says (idempotent).
    restore_tables = project_tables + [key for key in structured_tables if key not in project_tables]

    if dry_run:
        structured_only_dry: list[str] = []
```

**with:**

```python
    project_tables = list(plan.regenerate_tables)
    if "structured" not in stores:
        structured_tables: list[tuple[str, str]] = []
        structured_curate: list[tuple[str, str]] = []
    elif "graph" in stores and bases["structured"] == bases["graph"]:
        structured_tables = list(project_tables)
        structured_curate = list(plan.curate_tables)
    else:
        changed, _ = _classify_structured_text_diff(vault_dir, bases["structured"], head, domains)
        structured_tables, structured_curate = _split_curation_only(vault_dir, bases["structured"], head, changed)
    # A projected table is restored too, so table2graph reads DuckDB at
    # `head` whatever the structured bookmark says (idempotent).
    restore_tables = project_tables + [key for key in structured_tables if key not in project_tables]
    # A curation-only change restores registry rows alone -- unless this
    # machine has no parquet for the table yet, which needs the rows too.
    from artmind.structured.duckdb_adapter import parquet_path_for

    curate_tables = []
    for key in structured_curate:
        if key in restore_tables:
            continue
        if parquet_path_for(*key).is_file():
            curate_tables.append(key)
        else:
            restore_tables.append(key)

    if dry_run:
        structured_only_dry: list[str] = []
```

**Replace in** `artmind/vault_sync.py` (lines 1307-1312):

```python
            "unchanged": len(unchanged),
            "retract": len(plan.retract),
            "regenerate_tables": len(restore_tables),
            "structured_only_tables": structured_only_dry,
            "same_as_groups": plan.same_as_groups,
            "same_as_keys": len(plan.same_as_keys),
```

**with:**

```python
            "unchanged": len(unchanged),
            "retract": len(plan.retract),
            "regenerate_tables": len(restore_tables),
            "curated_tables": len(curate_tables),
            "structured_only_tables": structured_only_dry,
            "same_as_groups": plan.same_as_groups,
            "same_as_keys": len(plan.same_as_keys),
```

**Replace in** `artmind/vault_sync.py` (lines 1335-1349):

```python
        #    itself be mistaken for a track-A input in this same run (the
        #    diff_range above is already fixed from `plan`). Inputs come from
        #    `head`, never the live structured_text dir.
        if restore_tables:
            st_rel = STRUCTURED_TEXT_DIR.relative_to(vault_dir)
            wanted = [str(st_rel / MANIFEST_NAME)]
            for domain, table_name in restore_tables:
                wanted.append(str(st_rel / domain / f"{table_name}.csv"))
                wanted.append(str(st_rel / domain / f"{table_name}{META_SUFFIX}"))
            _materialize(vault_dir, head, _present_at(vault_dir, head, wanted), scratch)
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            import_structured_text(scratch / st_rel, tables=restore_tables)
        if project_tables:
            mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, scratch)
            schemas_at_head = _dir_at(vault_dir, head, paths.DOMAIN_SCHEMAS_DIR, scratch)
```

**with:**

```python
        #    itself be mistaken for a track-A input in this same run (the
        #    diff_range above is already fixed from `plan`). Inputs come from
        #    `head`, never the live structured_text dir.
        if restore_tables or curate_tables:
            st_rel = STRUCTURED_TEXT_DIR.relative_to(vault_dir)
            wanted = [str(st_rel / MANIFEST_NAME)]
            for domain, table_name in restore_tables:
                wanted.append(str(st_rel / domain / f"{table_name}.csv"))
                wanted.append(str(st_rel / domain / f"{table_name}{META_SUFFIX}"))
            for domain, table_name in curate_tables:
                wanted.append(str(st_rel / domain / f"{table_name}{META_SUFFIX}"))
            _materialize(vault_dir, head, _present_at(vault_dir, head, wanted), scratch)
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            if restore_tables:
                import_structured_text(scratch / st_rel, tables=restore_tables)
            if curate_tables:
                import_structured_text(scratch / st_rel, tables=curate_tables, registry_only=True)
            if "graph" in stores:
                # The graph's catalogue subgraph (:Table/:TableColumn/
                # MAPS_TO_CLASS) is derived from the registry just restored.
                from artmind.structured.pipeline import _project_catalogue_best_effort

                for d in sorted({d for d, _ in restore_tables + curate_tables}):
                    _project_catalogue_best_effort(d)
        if project_tables:
            mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, scratch)
            schemas_at_head = _dir_at(vault_dir, head, paths.DOMAIN_SCHEMAS_DIR, scratch)
```

**Replace in** `artmind/vault_sync.py` (lines 1453-1458):

```python
        "unchanged": len(unchanged),
        "retracted": len(plan.retract),
        "regenerated_tables": len(restore_tables),
        "structured_only_tables": structured_only,
        "same_as_groups": plan.same_as_groups,
        "same_as_keys": len(plan.same_as_keys),
```

**with:**

```python
        "unchanged": len(unchanged),
        "retracted": len(plan.retract),
        "regenerated_tables": len(restore_tables),
        "curated_tables": len(curate_tables),
        "structured_only_tables": structured_only,
        "same_as_groups": plan.same_as_groups,
        "same_as_keys": len(plan.same_as_keys),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_text_export.py test/test_vault_sync.py -q`
Expected: `201 passed`. Then the full suite — all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/structured/text_export.py artmind/vault_sync.py test/test_structured_text_export.py test/test_vault_sync.py
git commit -m "feat(vault-sync): a curation-only .meta.json change restores registry rows alone; the catalogue follows"
```

---

### Task 6: pending counts for every kind; docs, docstrings, skills, spec; full suite and CLI help

One characterization test pins that pending work counts every new kind through the framework (one fingerprint read per kind) — it passes as soon as it is added, because Plan C's `_graph_pending` iterates `curation_records.kinds()`. Then prose: `docs/vault.md` (the curation table, sticky retirement, derived `valid_to`, cheap curation-only tables), the `vault sync`, `docs retire`/`restore`, `ingest supersede` and `projection synthesize` docstrings, the `artmind-curate` and `artmind-ingestion-helper` skills, and the spec's new §15 A10 (decisions 1–10, including what stays machine-local and why `text2cypher` cannot write).

**Files:**
- Modify: `artmind/cli.py`
- Modify: `artmind/skills/artmind-curate/SKILL.md`
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md`
- Modify: `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md`
- Modify: `docs/vault.md`
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Add the characterization test**

**Replace in** `test/test_vault_sync.py` (lines 3844-3846):

```python

    assert calls["registry_only"] == [[("banking", "accounts")]]
    assert calls["catalogue"] == []
```

**with:**

```python

    assert calls["registry_only"] == [[("banking", "accounts")]]
    assert calls["catalogue"] == []


def test_pending_work_counts_every_curation_kind(repo, monkeypatch, curation_graph):
    """Lifecycle, supersession and synthesis records are counted like
    conflicts: each kind's own fingerprint read, one query per kind."""
    from artmind import curation_records, synthesis_records
    from artmind.observations import entity_id

    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _patch_curation_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "base")
    base = vs.head_sha(repo)
    curation_records.write_record("lifecycle", _lifecycle("docid-1"))
    curation_records.write_record("supersessions", {
        "id": "s1", "newer_doc_id": "d2", "older_doc_id": "d1", "scope": "document",
        "effective": None, "detected_by": "manual", "domains": ["banking"],
    })
    key = ("widget", "RATE", "banking")
    curation_records.write_record("syntheses", synthesis_records.record_from(key, {
        "id": entity_id(key), "text": "t", "observation_set_hash": "h", "observation_ids": [],
        "created_at": "t", "model": "m",
    }))
    _commit_all(repo, "three kinds of curation")
    head = vs.head_sha(repo)

    pending = vs.pending_work(repo, head, vs.Bookmarks(graph=base, structured=head))

    assert (pending["graph"]["state"], pending["graph"]["curation"]) == ("behind", 3)
    reads = [c for c, _ in curation_graph.calls if "AS fingerprint" in c]
    assert len(reads) == 3, "one fingerprint read per kind"
    assert "3 curation records" in vs.staleness_message(pending)
```

Run: `uv run --group dev pytest test/test_vault_sync.py -q -k every_curation_kind`
Expected: `1 passed`.

- [ ] **Step 2: Apply the prose changes**

**Replace in** `artmind/cli.py` (lines 1518-1524):

```python
@click.option("--effective", default=None, help="ISO date the supersession takes effect")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_supersede(domain: str, newer_name: str, older_name: str, scope: str, effective: str | None, compact: bool) -> None:
    """Manually assert that one document supersedes another (sets SUPERSEDES + valid_to)."""
    if scope != "document":
        raise click.ClickException(
            f"--scope {scope} is not yet supported. Sub-document supersession needs "
```

**with:**

```python
@click.option("--effective", default=None, help="ISO date the supersession takes effect")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_supersede(domain: str, newer_name: str, older_name: str, scope: str, effective: str | None, compact: bool) -> None:
    """Manually assert that one document supersedes another (sets SUPERSEDES + valid_to).

    The assertion is also a vault file (.artmind/data/curation/supersessions/),
    so `vault sync` applies it on every other machine; the older document is
    retired, which is its own vault file too.
    """
    if scope != "document":
        raise click.ClickException(
            f"--scope {scope} is not yet supported. Sub-document supersession needs "
```

**Replace in** `artmind/cli.py` (lines 2780-2785):

```python
    Entities left with no `latest` observation anywhere are then deleted by the
    projection rebuild — not because retire decided they were orphans, but
    because nothing asserts them any more. Reversible with `docs restore`.
    """
    _setup_logger()
    from artmind.lifecycle import resolve_document_id, retire_document
```

**with:**

```python
    Entities left with no `latest` observation anywhere are then deleted by the
    projection rebuild — not because retire decided they were orphans, but
    because nothing asserts them any more. Reversible with `docs restore`.

    The retirement is also a vault file (.artmind/data/curation/lifecycle/),
    so `vault sync` retires the document on every other machine, and a later
    re-ingest or replay of it keeps it retired.
    """
    _setup_logger()
    from artmind.lifecycle import resolve_document_id, retire_document
```

**Replace in** `artmind/cli.py` (lines 2801-2807):

```python

    The exact inverse of `docs retire`. Because entity ids are deterministic
    and the projection is derived, restoring recreates the same entities with
    the same ids rather than a parallel set.
    """
    _setup_logger()
    from artmind.lifecycle import resolve_document_id, restore_document
```

**with:**

```python

    The exact inverse of `docs retire`. Because entity ids are deterministic
    and the projection is derived, restoring recreates the same entities with
    the same ids rather than a parallel set. Deletes the retirement's vault
    file, so `vault sync` restores it on every other machine too.
    """
    _setup_logger()
    from artmind.lifecycle import resolve_document_id, restore_document
```

**Replace in** `artmind/cli.py` (lines 2997-3003):

```python
    immediately (Entity.description + a fresh embedding, in the same write as
    the :Synthesis node) — a later `projection rebuild` will read the
    :Synthesis store back and reproduce the identical description, proving it
    is a real input rather than a side effect.
    """
    _setup_logger()
    from artmind.synthesize import synthesize
```

**with:**

```python
    immediately (Entity.description + a fresh embedding, in the same write as
    the :Synthesis node) — a later `projection rebuild` will read the
    :Synthesis store back and reproduce the identical description, proving it
    is a real input rather than a side effect. Each synthesis is also a vault
    file (.artmind/data/curation/syntheses/), so `vault sync` gives every
    other machine the same description without another LLM call.
    """
    _setup_logger()
    from artmind.synthesize import synthesize
```

**Replace in** `artmind/cli.py` (lines 3637-3647):

```python

    Curation travels the same way: an `artmind update` is a document folder
    (kg/<domain>/update__<session>__<draft>/, replayed as a UserChat); a
    curation record under .artmind/data/curation/<kind>/ (a detected or
    resolved conflict) is applied when added or changed and removed when
    deleted; and the members of every same-as group added, removed or
    changed in .artmind/same_as.yaml are rebuilt, with the groups as
    committed at HEAD. Same-as proposals stay machine-local.

    Each store keeps its own bookmark: the graph's lives in the graph
    (one :ArtmindSyncState node per vault_id, so a shared AuraDB carries one
```

**with:**

```python

    Curation travels the same way: an `artmind update` is a document folder
    (kg/<domain>/update__<session>__<draft>/, replayed as a UserChat); a
    curation record under .artmind/data/curation/<kind>/ (a conflict, a
    supersession, a retirement, a synthesis) is applied when added or changed
    and removed when deleted; the members of every same-as group added,
    removed or changed in .artmind/same_as.yaml are rebuilt, with the groups
    as committed at HEAD; and a table whose .meta.json changed only in its
    classifications (grain, bridge columns, mappings) has its registry rows
    restored without re-importing its rows. A replayed document that is
    retired at HEAD stays retired. Same-as proposals stay machine-local.

    Each store keeps its own bookmark: the graph's lives in the graph
    (one :ArtmindSyncState node per vault_id, so a shared AuraDB carries one
```

**Replace in** `artmind/skills/artmind-curate/SKILL.md` (lines 21-29):

```markdown

**What travels to other machines.** In a vault, `same_as.yaml` is
`.artmind/same_as.yaml` (committed), and every detected conflict — with its
resolution — is its own file under `.artmind/data/curation/conflicts/`.
Obsidian Git commits both, and `artmind vault sync` on another machine
rebuilds the changed same-as groups and applies the conflict records, so a
machine with its own Neo4j ends up with the same curation. Same-as
**proposals** (the review queue) do not travel: they stay on the machine
that ran `sameas propose` / `detect-conflicts` / `refine-graph`, and only an
```

**with:**

```markdown

**What travels to other machines.** In a vault, `same_as.yaml` is
`.artmind/same_as.yaml` (committed), and every detected conflict — with its
resolution — every supersession, every retirement and every synthesis is its
own file under `.artmind/data/curation/`. Structured-table classifications
(Workflow D) travel in the table's `.meta.json`, which each `db grain` /
`db mappings` / `db bridge` / `db propose` rewrites. Obsidian Git commits all
of them, and `artmind vault sync` on another machine applies them, so a
machine with its own Neo4j ends up with the same curation. Same-as
**proposals** (the review queue) do not travel: they stay on the machine
that ran `sameas propose` / `detect-conflicts` / `refine-graph`, and only an
```

**Replace in** `artmind/skills/artmind-curate/SKILL.md` (lines 290-298):

````markdown
artmind db catalogue --domain <d>          # push confirmations into Neo4j (no re-ingest)
```

`db catalogue` matters: confirming a mapping updates the registry only. The
graph's catalogue subgraph is refreshed by ingest hooks, so a later
confirmation needs this explicit re-projection to reach Neo4j.

### What confirming a bridge column currently buys

````

**with:**

````markdown
artmind db catalogue --domain <d>          # push confirmations into Neo4j (no re-ingest)
```

`db catalogue` matters: confirming a mapping updates the registry (and the
table's `.meta.json`) only. The graph's catalogue subgraph is refreshed by
ingest hooks, so a later confirmation needs this explicit re-projection to
reach Neo4j on this machine; on other machines `vault sync` restores the
confirmation and re-projects the catalogue itself.

### What confirming a bridge column currently buys

````

**Replace in** `artmind/skills/artmind-ingestion-helper/SKILL.md` (lines 300-305):

````markdown
artmind docs restore --domain YOUR_DOMAIN --documentName DOCUMENT_NAME
```

**Archive** — the only actual removal artmind has (there is deliberately no
`purge`). Bundles the document (staged KG JSON, vault markdown, original
binary if any, a manifest) under `ARTMIND_ARCHIVE_DIR`, then removes it from
````

**with:**

````markdown
artmind docs restore --domain YOUR_DOMAIN --documentName DOCUMENT_NAME
```

A retirement is a vault file (`.artmind/data/curation/lifecycle/`), so it
reaches every machine through `vault sync`, and it **sticks**: re-ingesting
the document, or another machine replaying it, keeps it retired. Only `docs
restore` brings it back.

**Archive** — the only actual removal artmind has (there is deliberately no
`purge`). Bundles the document (staged KG JSON, vault markdown, original
binary if any, a manifest) under `ARTMIND_ARCHIVE_DIR`, then removes it from
````

**Replace in** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` (lines 516-518):

```markdown
Track D rebuilds with `same_as.yaml` as committed at `head`, never the working tree, and expands
every key it rebuilds to the whole same-as groups touching it.

```

**with:**

```markdown
Track D rebuilds with `same_as.yaml` as committed at `head`, never the working tree, and expands
every key it rebuilds to the whole same-as groups touching it.

**A10. Every graph-only curation write travels (extends §7 and A9).** Tracing every command the
chat and admin agents can run found four more writes that lived only in Neo4j or SQLite. Each
is now a curation kind (A9's one file per record) or a table's `.meta.json`:

- **Supersession** (`ingest supersede`, `ingest detect-supersession`, the adjudicator's
  "superseded" verdict): `curation/supersessions/<sha1(newer|older|scope)>.json`. The older
  document's `valid_to` and `superseded_by` are *derived* from every `SUPERSEDES` edge still
  pointing at it (latest `effective` wins, ties by id; both null when none remains), so
  un-asserting one -- deleting its file -- restores exactly what the remaining assertions say,
  on every machine, whatever order they apply in. Only supersession writes those two properties
  (a document's own window is `_valid_from`/`_valid_to`), which is what makes the derivation exact.
- **Retirement** (`docs retire`/`restore`, archive restore, a document-scoped supersession):
  `curation/lifecycle/<sha1(doc_id)>.json`, present exactly while the document is retired. A
  retirement is **sticky**: a later re-ingest or replay of that document commits it and moves it
  straight back to history in the same transaction, and `vault sync` re-applies the record
  committed at `head` after replaying the document. Only `docs restore` (deleting the record)
  brings it back. Un-asserting a supersession does not un-retire; the two records are
  independent.
- **Synthesis** (`projection synthesize`): `curation/syntheses/<entity id>.json` -- the
  `:Synthesis` node plus the entity key, no embedding (derived; re-embedded locally). Applied
  before `vault sync`'s rebuild, with its key in it, so the rebuilt description is the synthesis
  and no machine pays the LLM again.
- **Structured-store curation** (`db grain`, `db mappings`, `db bridge`, `db propose`): the command
  re-exports the table's `.meta.json` (not its CSV). `vault sync` restores only the registry rows
  of a table whose CSV is unchanged and whose meta changed only in curation fields -- no parquet
  rewrite, no `table2graph` -- and re-projects the graph's catalogue subgraph for the domains whose
  registry rows it restored.

Still machine-local, by design (§14 A6): same-as proposals (`sameas propose`/`reject`,
`ingest refine-graph`, `detect-conflicts`' same-entity verdicts) -- a review queue whose approved
outcome travels as `same_as.yaml`. `query graph text2cypher` cannot write at all: it runs through
`graph_query.read_session()`, which the server enforces as read-only.

```

**Replace in** `docs/vault.md` (lines 91-97):

```markdown
        │   └── a_deck_chunks/          ← chunk_001.md, chunks_meta.json
        ├── kg/<domain>/<doc>/          ← extraction output; COMMITTED
        ├── kg/<domain>/update__<session>__<draft>/  ← an `artmind update` fact; COMMITTED
        ├── curation/<kind>/<id>.json   ← curation records (conflicts); COMMITTED
        ├── document_registry.db        ← path↔id cache; NOT committed
        ├── graph_snapshot/             ← *.tar.gz; NOT committed
        └── structured_snapshot/        ← *.tar.gz; NOT committed
```

**with:**

```markdown
        │   └── a_deck_chunks/          ← chunk_001.md, chunks_meta.json
        ├── kg/<domain>/<doc>/          ← extraction output; COMMITTED
        ├── kg/<domain>/update__<session>__<draft>/  ← an `artmind update` fact; COMMITTED
        ├── curation/<kind>/<id>.json   ← curation records (conflicts, supersessions,
        │                                  lifecycle, syntheses); COMMITTED
        ├── document_registry.db        ← path↔id cache; NOT committed
        ├── graph_snapshot/             ← *.tar.gz; NOT committed
        └── structured_snapshot/        ← *.tar.gz; NOT committed
```

**Replace in** `docs/vault.md` (lines 391-396):

```markdown
|---|---|---|
| a fact added with `artmind update` | `kg/<domain>/update__<session>__<draft>/` (a staging folder) | `update confirm`; `update retract` deletes it |
| a detected conflict, and its resolution | `curation/conflicts/<id>.json` | `ingest detect-conflicts`, `ingest resolve-conflict` |
| a same-as group | `.artmind/same_as.yaml` | `sameas approve`, or you |

Curation records are **one file per record**, never one shared file: every
```

**with:**

```markdown
|---|---|---|
| a fact added with `artmind update` | `kg/<domain>/update__<session>__<draft>/` (a staging folder) | `update confirm`; `update retract` deletes it |
| a detected conflict, and its resolution | `curation/conflicts/<id>.json` | `ingest detect-conflicts`, `ingest resolve-conflict` |
| a supersession | `curation/supersessions/<id>.json` | `ingest supersede`, `ingest detect-supersession`, the adjudicator |
| a retired document | `curation/lifecycle/<id>.json` (exists while retired) | `docs retire`; `docs restore` deletes it |
| an LLM-synthesised description | `curation/syntheses/<entity id>.json` | `projection synthesize` |
| a table's grain, bridge columns, column mappings | the table's `.meta.json` | `db grain`, `db mappings`, `db bridge`, `db propose` |
| a same-as group | `.artmind/same_as.yaml` | `sameas approve`, or you |

Curation records are **one file per record**, never one shared file: every
```

**Replace in** `docs/vault.md` (lines 402-407):

```markdown
either pulls, git reports a conflict on that one file — keep either side
(`git checkout --ours` or `--theirs` on it), it is the same conflict.

Same-as **proposals** (`sameas propose`, `ingest refine-graph`, and the
adjudicator's "same entity" verdicts) stay on the machine that made them: they
are a review queue, and an approved group travels as `same_as.yaml`.
```

**with:**

```markdown
either pulls, git reports a conflict on that one file — keep either side
(`git checkout --ours` or `--theirs` on it), it is the same conflict.

A **retirement sticks**: re-ingesting a retired document, or `vault sync`
replaying it, commits the new version and leaves it retired — only `docs
restore` brings it back. A supersession sets the older document's `valid_to`
and `superseded_by` from **all** the supersessions pointing at it, so deleting
one supersession's file restores what the others say (nothing, when none is
left); it does not un-retire the older document. A synthesis carries no
embedding: `vault sync` re-embeds the entity locally. A table whose rows did
not change but whose classifications did is restored into the registry alone —
no parquet rewrite, no re-projection — and the graph's table catalogue is
re-projected for its domain.

Same-as **proposals** (`sameas propose`, `ingest refine-graph`, and the
adjudicator's "same entity" verdicts) stay on the machine that made them: they
are a review queue, and an approved group travels as `same_as.yaml`.
```

- [ ] **Step 3: Full suite**

Run: `uv run --group dev pytest test/ -q`
Expected: `2560 passed, 14 skipped`.

- [ ] **Step 4: CLI help**

```bash
ARTMIND_NO_PROXY=1 uv run artmind vault sync --help
ARTMIND_NO_PROXY=1 uv run artmind docs retire --help
ARTMIND_NO_PROXY=1 uv run artmind ingest supersede --help
ARTMIND_NO_PROXY=1 uv run artmind projection synthesize --help
```

Expected: `vault sync --help` names "a conflict, a supersession, a retirement, a synthesis" and the curation-only table case; the other three each mention their vault file under `.artmind/data/curation/`.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py artmind/skills/artmind-curate/SKILL.md artmind/skills/artmind-ingestion-helper/SKILL.md docs/superpowers/specs/2026-09-26-vault-git-transport-design.md docs/vault.md test/test_vault_sync.py
git commit -m "docs(vault-sync): every graph-only curation write travels -- vault.md, docstrings, skills, spec §15 A10"
```

---

## Live verification (user)

Run after Plans C and C2 are merged and installed, against your **local** Neo4j, in throwaway vaults, in-process (`ARTMIND_NO_PROXY=1`). Reuse Plan C's setup: steps 0 of Plan C's live verification (two clones `A`/`B` of one bare remote, each with its own database, `planca`/`plancb`), with both notes ingested on A and `vault sync --bootstrapEmpty` run on B. Obsidian Git is simulated with `git add -A && git commit -qm MSG && git pull -q --no-rebase origin main && git push -q origin HEAD:main`. `projection synthesize` calls your LLM.

### 1. A supersession, its retirement, and un-asserting it

```bash
cd "$LIVE/A"
printf '# Acme v2\n\nAcme Bank is headquartered in Mumbai. Its CEO is Priya Rao.\n' > notes/acme-v2.md
artmind ingest sync notes/acme-v2.md --domain banking.organization
artmind ingest supersede --domain banking.organization --newer acme-v2.md --older acme.md --effective 2026-09-01 --compact
ls .artmind/data/curation/supersessions/ .artmind/data/curation/lifecycle/   # one file each
git add -A && git commit -qm "A: v2 supersedes v1" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main
artmind vault status                 # "... / 2 curation records behind" (plus the new document)
artmind vault sync --compact         # "curation":{"lifecycle":{"apply":1,...},"supersessions":{"apply":1,...}}
```

Cypher on `plancb`:

```cypher
MATCH (n)-[s:SUPERSEDES]->(o) RETURN n.name, o.name, s.effective, s.record_id IS NOT NULL;
MATCH (d) WHERE d.name = 'acme.md' RETURN labels(d), d.valid_to, d.superseded_by, d.lifecycle_record IS NOT NULL;
```

`acme.md` is `:DocumentHistory`, `valid_to = 2026-09-01`, `superseded_by` = v2's id. Now un-assert the supersession on A by deleting its file:

```bash
cd "$LIVE/A" && rm .artmind/data/curation/supersessions/*.json
git add -A && git commit -qm "A: un-assert" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main && artmind vault sync --compact   # supersessions remove:1
```

On `plancb`: no `SUPERSEDES` edge; `acme.md` has `valid_to` and `superseded_by` null — and is **still** `:DocumentHistory` (decision 5). `artmind docs restore --domain banking.organization --documentName acme.md` on A, commit, pull and sync on B: it is `:Document` again on both.

### 2. A retirement survives a re-ingest and a replay

```bash
cd "$LIVE/A"
artmind docs retire --domain banking.risk_governance --documentName acme-risk.md --compact
printf '\nThe Reserve Bank inspected Acme Bank in 2026.\n' >> notes/acme-risk.md
artmind ingest sync notes/acme-risk.md --domain banking.risk_governance      # re-ingest a retired document
```

On `planca`: `MATCH (d {name: 'acme-risk.md'}) RETURN labels(d)` is `DocumentHistory` (sticky — decision 3). Commit, push, pull on B, `vault sync`: the document folder is replayed and the document is `:DocumentHistory` on `plancb` too.

### 3. A synthesis travels without a second LLM call

```bash
cd "$LIVE/A"
artmind projection synthesize --domain banking.organization --nameFilter acme --compact
ls .artmind/data/curation/syntheses/
git add -A && git commit -qm "A: synthesis" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main && artmind vault sync --compact   # syntheses apply:N
```

On both databases: `MATCH (e:Entity) WHERE e.name =~ '(?i)acme.*' RETURN e.name, e.description, e.embedding IS NOT NULL` — identical descriptions, and embeddings present on B (swept locally). B's LLM call log (`.artmind/logs/llm_calls.log` in B's vault) has no synthesize call from this sync.

### 4. A table classification travels cheaply

```bash
cd "$LIVE/A"
printf 'id,name\n1,Widget\n2,Gadget\n' > products.csv
artmind ingest sync products.csv --domain banking.products
git add -A && git commit -qm "A: table" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main && artmind vault sync --compact
cd "$LIVE/A"
artmind db grain products --domain banking.products --set lookup --compact
git diff --stat                     # only .artmind/data/structured_text/banking.products/products.meta.json
git add -A && git commit -qm "A: grain" && git pull -q --no-rebase origin main && git push -q origin HEAD:main
cd "$LIVE/B" && git pull -q --no-rebase origin main
stat -f '%m %N' .artmind/data/structured/*/products.parquet   # note the parquet mtime
artmind vault sync --dryRun --compact   # "curated_tables":1,"regenerate_tables":0
artmind vault sync --compact
artmind db grain products --domain banking.products --compact     # "grain":"lookup","grainConfirmed":true
```

`stat -f '%m %N' .artmind/data/structured/*/products.parquet` prints the same mtime as before, and `MATCH (t:Table {name: 'products'}) RETURN t.grain, t.grain_confirmed` on `plancb` shows `lookup`, `true` (the catalogue was re-projected).

### Clean up

As Plan C: `rm -rf "$LIVE"`, then drop `planca`/`plancb` (or `docker rm -f planc-a planc-b`).

---

## Self-review

### Scope → tasks

| Scope item (coordinator's extension) | Task(s) |
|---|---|
| 1. Supersession: each assertion persisted, keyed deterministically (newer, older, scope) | 1 |
| 1. apply MERGEs it; a removed file un-asserts it | 1 (kind), Plan C track C |
| 1. how an un-assert restores `valid_to`, justified | decision 1; 1 (`_RECOMPUTE`) |
| 2. Document lifecycle: status override per doc id persisted and applied | 2 |
| 2. interaction with a later re-ingest or replay | decisions 3–5; 2 (`_commit_document_tx` 5b, `reapply_for_replayed`) |
| 3. `projection synthesize`: each synthesis persisted, keyed by entity id, applied | 3 |
| 3. embedding stored or not | decision 6: not stored; re-embedded by the sweep |
| 3. how `synthesis_loader` and rebuilds consume an applied synthesis | 3 ("pre" phase + key in the rebuild; sync test pins the order) |
| 4. `db grain`/`mappings`/`bridge` re-export the table's `.meta.json`, best-effort | 4 (plus `--acceptProposed` and `db propose`) |
| 4. track B applies a curation-only meta change correctly and cheaply | 5 |
| Fingerprint and pending-count integration, dry-run parity, for every new kind | Plan C's framework; each kind's `read_fingerprints` (1–3); dry-run tests (2, 5); 6 (pending for every kind) |
| `sameas reject`/`propose` and `refine-graph` output machine-local, intended; `text2cypher` cannot write | decision 10; 6 (spec §15 A10) |

### Placeholder scan

No "TBD", "similar to Task N", or step without its code; every replaced block is quoted exactly and was matched by the verification script.

### Type and name consistency

`curation_records.Kind(..., reapply_for=None)` (2) → `vault_sync.reapply_for_replayed` (2). `supersession_records`: `NAME`, `record_id`, `record_for`, `apply`, `remove`, `read_fingerprints`, `domains`, `KIND` (1) → `temporal.apply_supersession`. `lifecycle_records`: `NAME`, `record_id`, `record_for`, `retired_in_working_tree`, `mark`, `unmark`, `apply`, `remove`, `read_fingerprints`, `domains`, `reapply_for`, `KIND` (2) → `lifecycle._retire_tx`/`_restore_tx`, `ingest._load_staged`. `lifecycle._transition(..., rebuild=True)` (2) → `lifecycle_records`, `ingest._commit_document_tx`. `synthesis_records`: `NAME`, `record_from`, `apply`, `remove`, `read_fingerprints`, `domains`, `KIND` (3) → `synthesize.synthesize_key`. `text_export.export_structured_text(meta_only=)` (4), `import_structured_text(registry_only=)` (5); `pipeline._export_text_best_effort(meta_only=)` (4); `cli._reexport_table_meta` (4); `vault_sync._oids_at`, `_meta_without_curation`, `_split_curation_only`, `SyncPlan.curate_tables` (5). Result keys `curated_tables`, `curation`, `retired` are the same in code, tests and docs. Kind order in `kinds()`: lifecycle, supersessions, syntheses, conflicts.

### Ordering and green suite

Each task leaves the full suite green (verification record). No existing test's expectation changes in this plan; the characterization test in Task 6 and the fallback test in Task 5 pass on arrival, which Step 2/Step 1 say.

### Design decisions the spec did not settle (for review)

1. **Supersession record and derived `valid_to`.** Keyed by `sha1(newer|older|scope)`, no timestamp. Apply and remove both recompute the older document's `superseded_by`/`valid_to` from its remaining `SUPERSEDES` edges (latest non-null `effective` wins; ties by newer id; both null with no edge). Alternatives rejected: storing the pre-assertion `valid_to` in the record (stale on a machine whose graph differs, and order-dependent with two supersessions) and restoring from `document.json` (it holds `_valid_to`, a different property — supersession's `valid_to` has no other source). With two supersessions of one document, the latest `effective` now wins rather than the last applied.
2. **Lifecycle record present ⇔ retired**, id `sha1(doc_id)` because a document id can hold `:` (`table:<domain>:<table>`), which no portable file name can. No timestamp.
3. **Sticky retirement** — a behaviour change: re-ingesting a retired document used to revive it (`_merge_relabeled` moves the node back to `:Document`); now the commit moves it straight back to history when a lifecycle record exists. The alternative — re-ingest deletes the record — would scatter record deletions across every ingest path, and a replay on another machine would then race the deletion's own sync. With stickiness the head state always wins, in any order. `vault sync` honours the record committed at HEAD after a replay; a local replay also honours this working tree's uncommitted record (this machine's own `docs retire`, already applied to its graph).
4. **Guarded restore**: removing a record restores only a document marked with that record id; `retract_document` clears the mark, so a document whose folder was deleted is never revived by a later `docs restore` elsewhere.
5. **Supersession and retirement are independent records**; un-asserting a supersession leaves the retirement.
6. **No embedding in synthesis records**; the rebuild marks the entity stale and `vault sync`'s entity sweep re-embeds it locally, as for chunk vectors.
7. **`.meta.json`-only re-export**, and `db propose`/`--acceptProposed` included — they write the same registry rows the brief's commands do, and `db propose` spends LLM budget worth keeping.
8. **Curation-only fields**: `table.grain`, `grain_confirmed`, `grain_status`, `bridge_status`, `mapping_status`, and the `column_mappings`/`column_roles` lists. Anything else changing (row count, version, columns, a refresh) is a full regenerate, as before.
9. **Catalogue re-projection in `vault sync`** — a new graph write, best-effort (logged, never fails the sync), per domain restored, only when the graph is in the run; `--store structured` stays Neo4j-free.
10. **Machine-local by design**: same-as proposals (and so `sameas reject`) — a review queue, spec §14 A6; `text2cypher` is read-only by construction (`read_session`, server-enforced), so it has nothing to persist.

### Accepted residual risk

- **Curation that predates records does not travel until re-asserted**: an existing `SUPERSEDES` edge (re-run `ingest detect-supersession`), an existing retirement (retire again), an existing `:Synthesis` (re-run `projection synthesize --force` for that domain). Their graph state is untouched on this machine.
- **A lifecycle record for a document this graph has not replayed yet** marks nothing; pending work counts it until the bookmark passes, then it is applied when the document's folder next replays (`reapply_for`).
- **One synthesis file per synthesised entity**: a large domain adds many small files; each re-synthesis rewrites its file.
- **The catalogue re-projection wipes and re-writes a whole domain's catalogue** from this machine's registry, as `ingest` already does.

## Verification record

- Worktree: `git worktree add --detach <scratchpad>/verifyC2 planC-applied` (Plan C applied, `cbb7732`). A script parsed this file and applied, in document order, every **Create** and **Replace in** … **with:** block — a replacement must match its file exactly once — running `uv run --group dev pytest test/ -q` after each task and committing it.
- Baseline (Plan C applied): `2519 passed, 14 skipped`.
- Results after each task (all `… passed, 14 skipped`): Task 1: `2528`; Task 2: `2540`; Task 3: `2547`; Task 4: `2552`; Task 5: `2559`; Task 6: `2560`.
- The applied tree is identical to branch `planC2-applied` (`git diff --quiet planC2-applied HEAD` → exit 0), which holds the same code as one commit per task on top of `planC-applied`.
- Expected-failure claims: for Tasks 1–5 the task's non-test files were reverted to the previous task's state (new modules deleted) and its test files re-run; each Step 2 states what that produced (Tasks 1–3: a collection error on the new kind's import; Task 4: `5 failed, 18 passed`; Task 5: `6 failed, 195 passed`, with the one fallback test that passes on arrival named). Task 6's test is a characterization test and passes on arrival, as its Step 1 says.
- The verification worktree was removed afterwards; only this plan file (and Plan C's) was committed to `vault-sync-cde`.
