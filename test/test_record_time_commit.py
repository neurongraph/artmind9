"""`_recorded_at` on commit (spec 2026-11-15 §6.1, plan question 17)."""
import json

from _neo4j_recorder import Recorder

from artmind import ingest, passage_store, projection, summary_store


def _stage(tmp_path, relationships=()):
    folder = tmp_path / "doc"
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps({"id": "d1", "name": "a.md", "_valid_from": "2026-01-07"}))
    (folder / "chunks.json").write_text(json.dumps([
        {"id": "d1_000", "doc_id": "d1", "text": "Alpha met Beta at the harbour.", "_domain": "pj"}]))
    (folder / "observations.json").write_text(json.dumps([
        {"id": "o1", "key": "alpha|PERSON|pj", "chunk_id": "d1_000", "doc_id": "d1", "name": "Alpha",
         "canonical_name": "Alpha", "_domain": "pj"},
        {"id": "o2", "key": "beta|PERSON|pj", "chunk_id": "d1_000", "doc_id": "d1", "name": "Beta",
         "canonical_name": "Beta", "_domain": "pj"},
    ]))
    (folder / "relationships.json").write_text(json.dumps(list(relationships)))
    return folder


def _commit(tmp_path, monkeypatch, *, defer=True, relationships=()):
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id: set())
    tx = Recorder(lambda c, p: [])
    ingest._commit_document_tx(tx, ingest._load_staged(_stage(tmp_path, relationships), "pj"), defer)
    return tx


def test_one_timestamp_reaches_every_write(tmp_path, monkeypatch):
    rel = {"source_name": "Alpha", "target_name": "Beta", "rel_type": "MET", "chunk_id": "d1_000"}
    tx = _commit(tmp_path, monkeypatch, relationships=[rel])
    stamped = {p["recorded_at"] for c, p in tx.calls if "recorded_at" in p}
    assert len(stamped) == 1
    (recorded_at,) = stamped
    assert recorded_at.endswith("+00:00")
    merges = [c for c, p in tx.calls if p.get("recorded_at") == recorded_at]
    for label in ("Document", "DocChunk", "Observation"):
        assert any(f"CREATE (n:{label}" in c for c in merges), label
    assert any("CREATE (n:DocPassage)" in c for c in merges)
    assert any("ASSERTS_RELATION" in c and "_recorded_at = coalesce(old_recorded, $recorded_at)" in c
               for c in merges)


def test_the_rebuild_reuses_the_commit_timestamp(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: seen.update(kw) or {})
    tx = _commit(tmp_path, monkeypatch, defer=False)
    stamped = {p["recorded_at"] for c, p in tx.calls if "recorded_at" in p}
    assert seen["recorded_at"] in stamped and len(stamped) == 1


def test_revive_keeps_the_first_recorded_at_and_sets_revived_at():
    tx = Recorder()
    ingest._merge_relabeled(tx, "Observation", "ObservationHistory", "o1", {"id": "o1"}, replace=True,
                            recorded_at="2026-11-16T00:00:00+00:00")
    cypher, params = tx.calls[0]
    assert params["recorded_at"] == "2026-11-16T00:00:00+00:00"
    # read before `SET n = $props` wipes it
    assert cypher.index("existing._recorded_at AS old_recorded") < cypher.index("SET n = $props")
    assert "SET n._recorded_at = coalesce(old_recorded, $recorded_at)" in cypher
    assert "o1 IS NULL AND o2 IS NOT NULL AND o2._retired_at <> $recorded_at AS revived" in cypher
    assert "SET n._revived_at = $recorded_at" in cypher
    # every node leaving History loses its retirement stamps, revived or re-written
    existing_branch = cypher.split("FOREACH")[2]
    assert "REMOVE n:ObservationHistory, n._retired_at, n._retired_by" in existing_branch
    create_branch = cypher.split("FOREACH")[1]
    assert "SET n._recorded_at = $recorded_at" in create_branch


def test_passages_and_summaries_keep_the_first_recorded_at():
    tx = Recorder()
    passage_store.write_chunk_passages(tx, "c1", [{"id": "p1", "chunk_id": "c1", "props": {}}],
                                       recorded_at="2026-11-16T00:00:00+00:00")
    tx2 = Recorder()
    summary_store.write_document_summaries(
        tx2, "d1", [{"id": "d1:summary", "target": "d1", "target_label": "Document", "props": {}}],
        recorded_at="2026-11-16T00:00:00+00:00")
    for rec in (tx, tx2):
        cypher, params = rec.calls[-1]
        assert params["recorded_at"] == "2026-11-16T00:00:00+00:00"
        assert "SET n._recorded_at = $recorded_at" in cypher
        assert "existing._recorded_at = coalesce(old_recorded, $recorded_at)" in cypher
        assert "SET existing._revived_at = $recorded_at" in cypher
        assert "o2._retired_at <> $recorded_at AS revived" in cypher


def test_entities_are_stamped_on_create_only():
    tx = Recorder()
    projection._merge_entities_batch(
        tx, [{"id": "e1", "label": "PERSON", "keep": ["name"], "props": {"name": "A"}, "description": None}],
        "2026-11-16T00:00:00+00:00")
    cypher, params = tx.calls[0]
    assert "ON CREATE SET e.embedding_stale = true, e._recorded_at = $recorded_at" in cypher
    assert params["recorded_at"] == "2026-11-16T00:00:00+00:00"
    assert "_recorded_at" in projection._PRESERVED_ENTITY_KEYS


def test_a_rebuild_with_no_timestamp_stamps_utc_now():
    tx = Recorder()
    projection._merge_entities_batch(
        tx, [{"id": "e1", "label": "PERSON", "keep": [], "props": {}, "description": None}])
    assert tx.calls[0][1]["recorded_at"].endswith("+00:00")


def test_record_time_keys_are_never_merged_as_content():
    for key in ("_recorded_at", "_revived_at", "_retired_at", "_retired_by", "_record_time_approx"):
        assert key in projection._OBSERVATION_SYSTEM_KEYS


def test_a_recommit_is_not_a_revive_but_an_earlier_retirement_is(tmp_path, monkeypatch):
    """Step 2 demotes the prior version at this commit's timestamp; the merges that follow
    treat only a node retired at another time as revived (spec 2026-11-15 §6.1)."""
    monkeypatch.setattr(ingest, "_record_now", lambda: "2026-11-17T09:00:00+00:00")
    tx = _commit(tmp_path, monkeypatch)
    demote = [p for c, p in tx.calls if "SET o:ObservationHistory" in c and "_retired_at = $now" in c]
    assert demote and demote[0]["now"] == "2026-11-17T09:00:00+00:00"
    merges = [(c, p) for c, p in tx.calls if "AS revived" in c]
    assert {"Observation", "DocChunk", "Document", "DocPassage"} <= {
        lbl for c, _ in merges for lbl in ("Observation", "DocChunk", "Document", "DocPassage")
        if f"OPTIONAL MATCH (o2:{lbl}History" in c}
    for cypher, params in merges:
        assert "o2._retired_at <> $recorded_at AS revived" in cypher
        assert params["recorded_at"] == demote[0]["now"]
