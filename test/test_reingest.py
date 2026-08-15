"""A1d — idempotent replace, tombstone, and hard purge.

The retraction primitive is scoped and shared-entity-safe: it removes exactly
one document's contributions (its share of each edge's provenance, its property
ledger entries, its chunks, and only the entities that lose their last source),
leaving knowledge other documents still back untouched. We assert the params
actually sent and which Cypher ran — never summary counts — because a MagicMock
session returns truthy for any query (CLAUDE.md).
"""
import json
import sqlite3
from hashlib import sha256

import artmind.ingest as ing


# ── fake session plumbing ────────────────────────────────────────────────────


class _Rec:
    """A run() result routed by the Cypher that produced it."""

    def __init__(self, session, cypher, kw):
        self.session, self.cypher, self.kw = session, cypher, kw

    def single(self):
        c = self.cypher
        if "collect(DISTINCT e.id) AS ids" in c:
            return {"ids": list(self.session.touched_ids)}
        # edge update / edge delete / chunk delete / entity GC all RETURN count.
        return {"n": self.session.counts.get(_classify(c), 0)}

    def data(self):
        if "e._prop_sources AS ps" in self.cypher:
            ids = self.kw.get("ids", [])
            return [
                {"id": i, "ps": json.dumps(self.session.ledgers[i])}
                for i in ids
                if i in self.session.ledgers
            ]
        return []


def _classify(cypher: str) -> str:
    if "x <> $doc_id" in cypher:
        return "edge_update"
    if "size(r.doc_ids) = 0" in cypher:
        return "edge_delete"
    if "DETACH DELETE c" in cypher:
        return "chunk_delete"
    if "NOT (e)-[:EXTRACTED_FROM]->(:DocChunk)" in cypher and "DETACH DELETE e" in cypher:
        return "entity_gc"
    return "other"


class _RetractSession:
    def __init__(self, touched_ids=(), ledgers=None, counts=None):
        self.touched_ids = list(touched_ids)
        self.ledgers = dict(ledgers or {})
        self.counts = counts or {}
        self.calls: list[tuple[str, dict]] = []
        self.writes: dict[str, dict] = {}

    def run(self, cypher, **kw):
        self.calls.append((cypher, kw))
        if "SET e += $props" in cypher and "id: $id" in cypher:
            self.writes[kw["id"]] = kw["props"]
        return _Rec(self, cypher, kw)

    # convenience lookups
    def call(self, kind):
        for cypher, kw in self.calls:
            if _classify(cypher) == kind:
                return cypher, kw
        raise AssertionError(f"no {kind!r} query ran; saw {[_classify(c) for c, _ in self.calls]}")


def _patch_session(monkeypatch, session):
    class _Ctx:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    import artmind.graph_query as gq

    monkeypatch.setattr(gq, "neo4j_session", lambda *a, **k: _Ctx())
    return session


# ── _retract_document_from_neo4j: the scoped retraction Cypher + params ───────


def test_retraction_runs_the_five_scoped_steps_with_doc_id(monkeypatch):
    session = _RetractSession(
        touched_ids=["e-shared", "e-unique"],
        ledgers={"e-shared": {"description": [["docA", "x"]]}},
        counts={"edge_update": 2, "edge_delete": 1, "chunk_delete": 3, "entity_gc": 1},
    )
    _patch_session(monkeypatch, session)

    result = ing._retract_document_from_neo4j("banking", "docA")

    # 1a. Edge provenance is rewritten to exclude this doc_id (both list props).
    cypher, kw = session.call("edge_update")
    assert kw["doc_id"] == "docA"
    assert "x <> $doc_id" in cypher  # doc_ids
    assert "STARTS WITH ($doc_id + '_')" in cypher  # chunk_ids
    assert kw["domain"] == "banking"

    # 1b. Only edges left with zero contributing documents are deleted.
    del_cypher, _ = session.call("edge_delete")
    assert "size(r.doc_ids) = 0" in del_cypher
    assert "r.doc_ids IS NOT NULL" in del_cypher  # never touches pre-A1b edges

    # 3. This document's chunks are deleted by doc_id.
    _, chunk_kw = session.call("chunk_delete")
    assert chunk_kw["doc_id"] == "docA"

    # Counts flow straight from what each RETURN count reported.
    assert result["edges_retracted"] == 2
    assert result["edges_deleted"] == 1
    assert result["chunks"] == 3
    assert result["gc_entities"] == 1


def test_retraction_gc_is_scoped_to_touched_ids_and_guarded(monkeypatch):
    """Shared-entity safety: the GC deletes only touched entities that have lost
    their last EXTRACTED_FROM — a shared entity another doc still cites survives
    because the query's own guard excludes it, not because we filtered ids."""
    session = _RetractSession(touched_ids=["e-shared", "e-unique"])
    _patch_session(monkeypatch, session)

    ing._retract_document_from_neo4j("banking", "docA")

    cypher, kw = session.call("entity_gc")
    # Scoped to exactly the entities this document fed (collected before the
    # chunk delete removed the EXTRACTED_FROM edges)...
    assert kw["ids"] == ["e-shared", "e-unique"]
    # ...and guarded so an entity still sourced by another document is spared.
    assert "NOT (e)-[:EXTRACTED_FROM]->(:DocChunk)" in cypher


def test_retraction_skips_gc_when_no_entities_were_touched(monkeypatch):
    session = _RetractSession(touched_ids=[])
    _patch_session(monkeypatch, session)

    result = ing._retract_document_from_neo4j("banking", "docA")

    assert result["gc_entities"] == 0
    assert not any(_classify(c) == "entity_gc" for c, _ in session.calls)


# ── _rollback_property_ledger: drop a doc's contribution, recompute clean value ─


def _rollback(ledgers, doc_id, ids):
    session = _RetractSession(ledgers=ledgers)
    updated = ing._rollback_property_ledger(session, doc_id, ids)
    return session, updated


def test_rollback_drops_contribution_and_recomputes_clean_value():
    session, updated = _rollback(
        {"e1": {"description": [["docA", "A"], ["docB", "B"]]}}, "docA", ["e1"]
    )
    assert updated == 1
    props = session.writes["e1"]
    # The retracted doc's value is gone; the survivor's clean value is recomputed.
    assert props["description"] == "B"
    assert json.loads(props["_prop_sources"]) == {"description": [["docB", "B"]]}


def test_rollback_removes_property_when_ledger_empties():
    session, updated = _rollback(
        {"e1": {"description": [["docA", "only"]]}}, "docA", ["e1"]
    )
    assert updated == 1
    props = session.writes["e1"]
    # Emptied property is removed from the node (null via SET +=), and so is the
    # now-empty ledger.
    assert props["description"] is None
    assert props["_prop_sources"] is None


def test_rollback_preserves_legacy_contribution():
    session, _ = _rollback(
        {"e1": {"description": [["__legacy__", "keep"], ["docA", "x"]]}},
        "docA",
        ["e1"],
    )
    props = session.writes["e1"]
    # The un-attributable pre-ledger base is never dropped by a purge.
    assert props["description"] == "keep"
    assert json.loads(props["_prop_sources"]) == {"description": [["__legacy__", "keep"]]}


def test_rollback_leaves_untouched_entity_alone():
    # A shared entity whose ledger holds no contribution from the retracted doc
    # must not be written at all.
    session, updated = _rollback(
        {"e1": {"description": [["docB", "B"]]}}, "docA", ["e1"]
    )
    assert updated == 0
    assert "e1" not in session.writes


def test_rollback_only_updates_affected_properties():
    session, _ = _rollback(
        {
            "e1": {
                "description": [["docA", "A"], ["docB", "B"]],
                "fee": [["docB", "10"]],
            }
        },
        "docA",
        ["e1"],
    )
    props = session.writes["e1"]
    # Only description changed; fee (docA never fed it) is not re-written...
    assert props["description"] == "B"
    assert "fee" not in props
    # ...but the ledger written back still carries fee's untouched contribution.
    assert json.loads(props["_prop_sources"]) == {
        "description": [["docB", "B"]],
        "fee": [["docB", "10"]],
    }


# ── registry: --replace UPDATEs the existing row, never a second INSERT ────────


def _patch_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    return db


def test_register_replace_updates_row_in_place(tmp_path, monkeypatch):
    db = _patch_db(tmp_path, monkeypatch)
    db._init_db()

    f = tmp_path / "a.md"
    f.write_text("alpha", encoding="utf-8")
    ing._register_document("banking", f, "lid-1")

    # Re-ingest edited content at the same path/logical id under --replace.
    f.write_text("alpha-edited", encoding="utf-8")
    ing._register_document("banking", f, "lid-1", replace=True)

    conn = sqlite3.connect(db.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT sha256 FROM documents WHERE domain = 'banking' AND logical_id = 'lid-1'"
        ).fetchall()
    finally:
        conn.close()
    # Exactly one row (UPDATE, not a second INSERT), with the new content hash.
    assert len(rows) == 1
    assert rows[0][0] == sha256(b"alpha-edited").hexdigest()


def test_register_replace_without_prior_row_inserts_fresh(tmp_path, monkeypatch):
    db = _patch_db(tmp_path, monkeypatch)
    db._init_db()

    f = tmp_path / "new.md"
    f.write_text("brand new", encoding="utf-8")
    # replace=True but nothing registered for this logical id yet → plain insert.
    ing._register_document("banking", f, "lid-new", replace=True)

    conn = sqlite3.connect(db.DB_PATH)
    try:
        row = conn.execute(
            "SELECT filename, logical_id FROM documents WHERE logical_id = 'lid-new'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("new.md", "lid-new")


# ── commit_to_graph(replace=True): retract the prior version before rewriting ─


def test_commit_replace_retracts_prior_version_first(tmp_path, monkeypatch):
    doc_kg_dir = tmp_path / "doc"
    doc_kg_dir.mkdir()
    (doc_kg_dir / "document.json").write_text(
        json.dumps({"id": "phys-1", "name": "docA.md"}), encoding="utf-8"
    )

    retractions: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ing, "_retract_document_from_neo4j",
        lambda domain, doc_id: retractions.append((domain, doc_id)) or {},
    )
    # Isolate the retraction hook: stub the write and the best-effort post-hooks.
    order: list[str] = []
    monkeypatch.setattr(ing, "write_to_graph", lambda d: order.append("write") or True)

    import artmind.entity_history as eh
    monkeypatch.setattr(eh, "supersession_possible", lambda *a, **k: False)
    import artmind.temporal as tmp_mod
    monkeypatch.setattr(tmp_mod, "normalize_ingested_document", lambda *a, **k: None)
    monkeypatch.setattr(tmp_mod, "detect_supersession", lambda *a, **k: {"applied": []})

    # Record retraction ordering relative to the write.
    real_retract = ing._retract_document_from_neo4j
    monkeypatch.setattr(
        ing, "_retract_document_from_neo4j",
        lambda domain, doc_id: (order.append("retract"), retractions.append((domain, doc_id)), {})[-1],
    )

    ok = ing.commit_to_graph(doc_kg_dir, "banking", replace=True)

    assert ok is True
    assert retractions == [("banking", "phys-1")]
    # Retraction must precede the write, so the re-commit replaces rather than
    # accretes onto the prior version.
    assert order == ["retract", "write"]


def test_commit_without_replace_does_not_retract(tmp_path, monkeypatch):
    doc_kg_dir = tmp_path / "doc"
    doc_kg_dir.mkdir()
    (doc_kg_dir / "document.json").write_text(
        json.dumps({"id": "phys-1", "name": "docA.md"}), encoding="utf-8"
    )

    retractions: list = []
    monkeypatch.setattr(
        ing, "_retract_document_from_neo4j",
        lambda domain, doc_id: retractions.append((domain, doc_id)) or {},
    )
    monkeypatch.setattr(ing, "write_to_graph", lambda d: True)
    import artmind.entity_history as eh
    monkeypatch.setattr(eh, "supersession_possible", lambda *a, **k: False)
    import artmind.temporal as tmp_mod
    monkeypatch.setattr(tmp_mod, "normalize_ingested_document", lambda *a, **k: None)
    monkeypatch.setattr(tmp_mod, "detect_supersession", lambda *a, **k: {"applied": []})

    ing.commit_to_graph(doc_kg_dir, "banking")  # replace defaults to False

    assert retractions == []
