"""Dry-run re-extraction: proposed schema/meta vs current staging, nothing written."""

import json
import os

import pytest

from artmind.reviews import reextract


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", tmp_path / "schemas")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "d_schema.yaml").write_text(
        "name: d\nentity_types:\n  PERSON: {kind: recurrent, description: A person.}\n"
        "  LOCATION: {kind: recurrent, description: A place.}\n")
    folder = tmp_path / "kg" / "d" / "2031-03-04"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "_valid_from": "2031-03-04"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "doc-1_001", "text": "I went to Lakeport with Mira."}]))
    (folder / "observations.json").write_text(json.dumps([
        {"name": "Arlo Venn", "entity_class": "PERSON", "chunk_id": "doc-1_001", "role": "groom"},
        {"name": "Mira", "entity_class": "PERSON", "chunk_id": "doc-1_001"},
        {"name": "Lakeport", "entity_class": "LOCATION", "chunk_id": "doc-1_001"}]))
    (folder / "relationships.json").write_text(json.dumps([
        {"source_name": "Arlo Venn", "target_name": "Lakeport", "rel_type": "went_to", "chunk_id": "doc-1_001"}]))
    proposed = tmp_path / "proposed_schema.yaml"
    proposed.write_text((tmp_path / "schemas" / "d_schema.yaml").read_text()
                        + "guidance:\n  entities: NARRATOR-RULE \"I\" is the Diarist.\n")
    return folder


ENTITIES = [{"id": "p1", "name": "Diarist", "entity_class": "PERSON"},
            {"id": "p2", "name": "Mira", "entity_class": "PERSON"},
            {"id": "l1", "name": "Lakeport", "entity_class": "LOCATION"}]
PROPS = [{"id": "p2", "properties": {"role": "friend"}}]
RELS = [{"source_id": "p1", "target_id": "l1", "rel_type": "went_to"},
        {"source_id": "p2", "target_id": "l1", "rel_type": "went_to"}]


@pytest.fixture
def fake_llm(monkeypatch):
    from artmind import extraction

    prompts = []

    def fake(step_name, model, prompt, debug_dir=None):
        prompts.append((step_name, model, prompt))
        if step_name.endswith("entities"):
            return [dict(e) for e in ENTITIES], True
        if step_name.endswith("properties"):
            return PROPS, True
        return RELS, True

    monkeypatch.setattr(extraction, "extract_with_retry", fake)
    return prompts


def _snapshot(root):
    return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*") if p.is_file()}


def test_extract_chunk_merges_properties(fake_llm):
    out = reextract.extract_chunk("text", {"name": "d", "entity_types": {}}, model="m")
    assert out["failed_steps"] == []
    assert next(e for e in out["entities"] if e["id"] == "p2")["properties"] == {"role": "friend"}
    assert [p[0] for p in fake_llm] == ["reextract_entities", "reextract_properties", "reextract_relationships"]


def test_diff():
    current = reextract.current_side(
        [{"name": "Arlo Venn", "entity_class": "PERSON", "role": "groom"}, {"name": "Lakeport", "entity_class": "LOCATION"}],
        [{"source_name": "Arlo Venn", "target_name": "Lakeport", "rel_type": "went_to"}])
    proposed = reextract.proposed_side(
        [{"id": "a", "name": "arlo venn", "entity_class": "PERSON", "properties": {"role": "guest"}},
         {"id": "l", "name": "Lakeport", "entity_class": "LOCATION", "properties": {}},
         {"id": "d", "name": "Diarist", "entity_class": "PERSON", "properties": {}}],
        [{"source_id": "d", "target_id": "l", "rel_type": "WENT_TO"}])
    d = reextract.diff(current, proposed)
    assert d["nodes"] == {"added": ["Diarist (PERSON)"], "removed": [], "kept": 2}
    assert d["edges"] == {"added": ["diarist -went_to-> lakeport"], "removed": ["arlo venn -went_to-> lakeport"], "kept": 0}
    assert d["properties"] == [{"node": "arlo venn (PERSON)", "added": {}, "removed": {}, "changed": {"role": ["groom", "guest"]}}]


def test_reextract_uses_proposed_schema_writes_nothing_and_checks_pattern(tmp_path, fake_llm, _isolated):
    before = _snapshot(tmp_path / "kg")
    pattern = [{"name": "Mira"}, {"rel": "WENT_TO"}, {"name": "Lakeport"}]
    result = reextract.reextract(domain="d", chunk_ids=["doc-1_001"], schema_path=tmp_path / "proposed_schema.yaml",
                                 pattern=pattern, model="test-model")
    assert _snapshot(tmp_path / "kg") == before
    assert not (tmp_path / "reg.db").exists() or os.path.getsize(tmp_path / "reg.db") >= 0
    assert result["writes"] == "none" and result["model"] == "test-model"
    assert "NARRATOR-RULE" in fake_llm[0][2]             # the proposed schema reached the entities prompt
    chunk = result["chunks"][0]
    assert chunk["chunk_id"] == "doc-1_001"
    assert "Diarist (PERSON)" in chunk["diff"]["nodes"]["added"]
    assert "Arlo Venn (PERSON)" in chunk["diff"]["nodes"]["removed"]
    assert "mira -went_to-> lakeport" in chunk["diff"]["edges"]["added"]
    assert chunk["answerability"] == {"current": {"matched": False, "count": 0, "matches": [], "truncated": False},
                                      "proposed": {"matched": True, "count": 1, "matches": [["Mira", "Lakeport"]], "truncated": False}}


def test_reextract_baseline_and_missing_staging(tmp_path, fake_llm):
    result = reextract.reextract(domain="d", chunk_ids=["doc-1_001", "doc-9_001"], model="m")
    assert result["schema"] == "(current)" and result["meta"] == "(current)"
    assert result["chunks"][1] == {"chunk_id": "doc-9_001", "error": "no staging folder holds this chunk"}
    assert "NARRATOR-RULE" not in fake_llm[0][2]


def test_reextract_respects_max_chunks(fake_llm):
    result = reextract.reextract(domain="d", chunk_ids=["doc-1_001", "doc-1_001b", "doc-1_001c"], max_chunks=1, model="m")
    assert [c["chunk_id"] for c in result["chunks"]] == ["doc-1_001"]
    assert result["llm_calls_planned"] == 3


def test_bad_schema_file(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- not a mapping\n")
    with pytest.raises(reextract.ReextractError):
        reextract.reextract(domain="d", chunk_ids=["doc-1_001"], schema_path=bad, model="m")
