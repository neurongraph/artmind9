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
