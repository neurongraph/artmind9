"""A re-extraction may never cost a note its previous extraction (spec
2026-10-18-extraction-loss-guard-design.md).

`--reextract` sets the chunk cache aside instead of deleting it, and
`extract_kg` refuses to replace staging while a chunk step is still failed, or
with nothing when an unchanged note had something. No Neo4j, no LLM: the
extraction seams are mocked as in test_vault_chunk_cache_merge.py, on a real
temp vault (`ingest_env`). Synthetic text only."""
import json
from pathlib import Path

import pytest

import artmind.ingest as ing
from conftest import stage_as_extracted

PRIOR_OBSERVATIONS = [{"id": "o1", "key": "alpha", "name": "Alpha"}]
PRIOR_RELATIONSHIPS = [{"source_id": "o1", "target_id": "c1", "rel_type": "EXTRACTED_FROM"}]


@pytest.fixture()
def stash_root(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    monkeypatch.setattr(ing, "LOGS_DIR", root)
    return root


def _rows(sha):
    conn = ing._get_db()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT doc_sha256, doc_id, chunk_seq, entities_status, properties_status,"
            " relationships_status, updated_at FROM kg_chunk_status WHERE doc_sha256 = ?"
            " ORDER BY chunk_seq", (sha,))]
    finally:
        conn.close()


def test_reset_moves_the_cache_aside_and_returns_a_stash(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text('{"raw_entities": ["kept"]}', encoding="utf-8")
    ing._init_chunk_rows("sha-1", "doc-1", 1)
    ing._update_chunk_step("sha-1", 1, "entities", "ok")
    before = _rows("sha-1")

    stash = ing._reset_extraction_cache(cache, "sha-1")

    assert not cache.exists()
    assert ing._get_chunk_statuses("sha-1") == {}
    assert stash["rows"] == before
    assert Path(stash["dir"]).parent == stash_root / "reextract-stash"
    assert json.loads((Path(stash["dir"]) / "chunk_001.json").read_text()) == {"raw_entities": ["kept"]}


def test_restore_puts_the_cache_and_rows_back_over_a_failed_run(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text('{"raw_entities": ["kept"]}', encoding="utf-8")
    ing._init_chunk_rows("sha-1", "doc-1", 1)
    ing._update_chunk_step("sha-1", 1, "entities", "ok")
    before = _rows("sha-1")
    stash = ing._reset_extraction_cache(cache, "sha-1")
    # what a failed re-extraction leaves behind
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text('{"raw_entities": []}', encoding="utf-8")
    ing._init_chunk_rows("sha-1", "doc-1", 1)
    ing._update_chunk_step("sha-1", 1, "entities", "failed")

    ing._restore_extraction_cache(stash, cache, "sha-1")

    assert json.loads((cache / "chunk_001.json").read_text()) == {"raw_entities": ["kept"]}
    assert _rows("sha-1") == before
    assert not Path(stash["dir"]).exists()


def test_restore_and_drop_accept_no_stash(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    cache.mkdir(parents=True)
    ing._restore_extraction_cache(None, cache, "sha-1")
    ing._drop_extraction_stash(None)
    assert cache.exists()


def test_drop_deletes_the_stash(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text("{}", encoding="utf-8")
    stash = ing._reset_extraction_cache(cache, "sha-1")

    ing._drop_extraction_stash(stash)

    assert not Path(stash["dir"]).exists()


def test_reset_of_a_missing_cache_stashes_only_rows(ingest_env, stash_root):
    cache = ing.KG_DIR / "general" / "a" / "chunks" / "sha-1"
    stash = ing._reset_extraction_cache(cache, "sha-1")
    assert stash == {"dir": None, "rows": []}


# ── extract_kg end to end, LLM mocked ────────────────────────────────────────


def _schemas(vault, monkeypatch):
    schemas = vault / "schemas"
    schemas.mkdir(exist_ok=True)
    src = Path(ing.__file__).parent / "domains" / "schemas" / "general_schema.yaml"
    (schemas / "general_schema.yaml").write_text(src.read_text())
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)


def _seams(monkeypatch, llm):
    """No embedder, no real prompts; `llm(step_name)` answers every LLM step."""
    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: "p")
    monkeypatch.setattr(ing, "build_properties_prompt",
                        lambda text, ents, schema, vocabulary=None, anchor_date=None: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: "p")
    monkeypatch.setattr(ing, "_llm_extract", lambda step_name, model, prompt, debug_dir: llm(step_name))


def _note(vault, text="A synthetic note about Alpha meeting Beta.\n"):
    note = vault / "notes" / "a.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(text, encoding="utf-8")
    return note


def _previously_extracted(vault, monkeypatch, observations=PRIOR_OBSERVATIONS):
    """A note whose earlier extraction is staged (non-empty by default) with a
    complete chunk cache: the state a `--reextract` finds it in. Returns the
    re-extraction's file_result and the staging folder."""
    _schemas(vault, monkeypatch)
    note = _note(vault)
    first = ing.ingest_file(note, "m", "general")
    folder = stage_as_extracted(ing.KG_DIR, first)
    (folder / "observations.json").write_text(json.dumps(observations), encoding="utf-8")
    (folder / "relationships.json").write_text(json.dumps(PRIOR_RELATIONSHIPS), encoding="utf-8")
    result = ing.ingest_file(note, "m", "general", reextract=True)
    result["reextract"] = True
    cache = folder / "chunks" / result["sha256"]
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text('{"raw_entities": ["prior"]}', encoding="utf-8")
    ing._init_chunk_rows(result["sha256"], result["artmind_id"], 1)
    for step in ("entities", "properties", "relationships"):
        ing._update_chunk_step(result["sha256"], 1, step, "ok")
    return result, folder


def _snapshot(folder):
    return {name: (folder / name).read_bytes()
            for name in ("document.json", "observations.json", "relationships.json")}


def test_a_failed_reextraction_leaves_staging_cache_and_rows_as_they_were(ingest_env, monkeypatch, stash_root):
    vault, _ = ingest_env
    result, folder = _previously_extracted(vault, monkeypatch)
    before_files, before_rows = _snapshot(folder), _rows(result["sha256"])
    _seams(monkeypatch, lambda step: ([], False))

    assert ing.extract_kg(result, "general", max_workers=1) is None

    assert _snapshot(folder) == before_files
    assert _rows(result["sha256"]) == before_rows
    cache = folder / "chunks" / result["sha256"]
    assert json.loads((cache / "chunk_001.json").read_text()) == {"raw_entities": ["prior"]}
    assert list((stash_root / "reextract-stash").iterdir()) == []


def test_an_exception_during_reextraction_restores_the_cache(ingest_env, monkeypatch, stash_root):
    vault, _ = ingest_env
    result, folder = _previously_extracted(vault, monkeypatch)
    before_files = _snapshot(folder)

    def boom(step):
        raise RuntimeError("provider down")

    _seams(monkeypatch, boom)

    with pytest.raises(RuntimeError, match="provider down"):
        ing.extract_kg(result, "general", max_workers=1)

    assert _snapshot(folder) == before_files
    cache = folder / "chunks" / result["sha256"]
    assert json.loads((cache / "chunk_001.json").read_text()) == {"raw_entities": ["prior"]}


def test_a_first_ingest_with_failed_steps_stages_nothing_and_keeps_rows_for_resume(
    ingest_env, monkeypatch, stash_root,
):
    vault, _ = ingest_env
    _schemas(vault, monkeypatch)
    result = ing.ingest_file(_note(vault), "m", "general")
    _seams(monkeypatch, lambda step: ([], False))

    assert ing.extract_kg(result, "general", max_workers=1) is None

    folder = ing.KG_DIR / "general" / "a"
    assert not (folder / "observations.json").exists()
    assert ing._get_chunk_statuses(result["sha256"])[1]["entities_status"] == "failed"
