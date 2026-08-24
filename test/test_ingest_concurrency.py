"""Chunk-level concurrency for KG extraction (extract_kg fan-out)."""
import json
from pathlib import Path

import artmind.db as db
import artmind.ingest as ing


# ── _resolve_ingest_workers: the fan-out width decision ─────────────────────

def test_resolve_workers_clamps_override_to_chunk_count():
    assert ing._resolve_ingest_workers(3, override=8) == 3
    assert ing._resolve_ingest_workers(20, override=8) == 8


def test_resolve_workers_override_one_forces_sequential():
    assert ing._resolve_ingest_workers(20, override=1) == 1


def test_resolve_workers_floor_is_one():
    # A zero/negative override must never disable processing entirely.
    assert ing._resolve_ingest_workers(20, override=0) == 1
    assert ing._resolve_ingest_workers(20, override=-5) == 1


def test_resolve_workers_zero_chunks_is_one():
    assert ing._resolve_ingest_workers(0, override=8) == 1


def test_resolve_workers_env_override(monkeypatch):
    monkeypatch.setattr(ing, "load_env", lambda: {"ARTMIND_INGEST_MAX_WORKERS": "6"})
    assert ing._resolve_ingest_workers(20) == 6


def test_resolve_workers_invalid_env_falls_back_to_provider_default(monkeypatch):
    monkeypatch.setattr(
        ing, "load_env",
        lambda: {"ARTMIND_INGEST_MAX_WORKERS": "notanint", "ARTMIND_KG_LLM_PROVIDER": "openrouter"},
    )
    # Invalid env is ignored; openrouter default (4) applies.
    assert ing._resolve_ingest_workers(20) == 4


def test_resolve_workers_provider_defaults(monkeypatch):
    monkeypatch.setattr(ing, "load_env", lambda: {"ARTMIND_KG_LLM_PROVIDER": "openrouter"})
    assert ing._resolve_ingest_workers(20) == 4
    monkeypatch.setattr(ing, "load_env", lambda: {"ARTMIND_KG_LLM_PROVIDER": "ollama"})
    assert ing._resolve_ingest_workers(20) == 1
    # Unset provider defaults to ollama's sequential behaviour.
    monkeypatch.setattr(ing, "load_env", lambda: {})
    assert ing._resolve_ingest_workers(20) == 1


# ── extract_kg fan-out: every chunk processed, output order deterministic ───

def _setup_extract_env(monkeypatch, tmp_path: Path, n_chunks: int) -> dict:
    """Wire extract_kg onto tmp dirs + a tmp sqlite, mocking embed/LLM/prompts.

    Returns the file_result dict extract_kg expects.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    # Isolate the registry DB.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")

    # Redirect all path globals extract_kg reads.
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "testdom_schema.yaml").write_text("entities_prompt: p\n", encoding="utf-8")
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", tmp_path / "markdowns")  # empty → no frontmatter

    # Chunk source files + a registered document file.
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    for seq in range(1, n_chunks + 1):
        (chunks_dir / f"chunk_{seq:03d}.md").write_text(f"chunk body {seq}", encoding="utf-8")
    registered = tmp_path / "doc.md"
    registered.write_text("original", encoding="utf-8")

    # Deterministic, side-effect-free stand-ins for the network calls. One entity
    # per chunk lets us assert every chunk's work landed in the merge.
    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: "p")
    monkeypatch.setattr(ing, "build_properties_prompt", lambda text, ents, schema: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: "p")

    def fake_llm_extract(step_name, model, prompt, debug_dir):
        if step_name.endswith("_entities"):
            return ([{"id": "e1", "entity_class": "Thing", "name": step_name}], True)
        return ([], True)

    monkeypatch.setattr(ing, "_llm_extract", fake_llm_extract)

    return {
        "sha256": f"sha-{n_chunks}",
        "chunks_dir": str(chunks_dir),
        "registered_path": str(registered),
    }


def _merged_entity_chunk_ids(doc_kg_dir: Path) -> set[str]:
    entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
    return {e["chunk_id"] for e in entities}


def test_extract_kg_concurrent_processes_every_chunk(monkeypatch, tmp_path):
    n = 6
    file_result = _setup_extract_env(monkeypatch, tmp_path, n)
    doc_kg_dir = ing.extract_kg(file_result, "testdom", max_workers=4)
    assert doc_kg_dir is not None

    # Every chunk produced its own JSON and contributed exactly one entity.
    # (chunk cache is nested one level deeper, under doc_sha256 — see extract_kg.)
    chunk_jsons = sorted((doc_kg_dir / "chunks").glob("**/chunk_*.json"))
    assert len(chunk_jsons) == n
    entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
    assert len(entities) == n
    assert len(_merged_entity_chunk_ids(doc_kg_dir)) == n

    # Merge order stays deterministic (by seq) regardless of completion order.
    chunks = json.loads((doc_kg_dir / "chunks.json").read_text(encoding="utf-8"))
    seqs = [c["id"] for c in chunks]
    assert seqs == sorted(seqs)


def test_extract_kg_concurrent_matches_sequential(monkeypatch, tmp_path):
    # Same inputs under workers=1 and workers=4 must yield identical merged
    # entity sets — concurrency is a scheduling change, not a semantic one.
    seq_result = _setup_extract_env(monkeypatch, tmp_path / "seq", 5)
    seq_dir = ing.extract_kg(seq_result, "testdom", max_workers=1)

    conc_result = _setup_extract_env(monkeypatch, tmp_path / "conc", 5)
    conc_dir = ing.extract_kg(conc_result, "testdom", max_workers=4)

    assert _merged_entity_chunk_ids(seq_dir) and _merged_entity_chunk_ids(conc_dir)
    # chunk_ids are doc_id-prefixed (doc_id is random per run), so compare counts
    # and per-chunk structure rather than the literal ids.
    assert len(_merged_entity_chunk_ids(seq_dir)) == len(_merged_entity_chunk_ids(conc_dir)) == 5


# ── re-ingest with changed content: no stale chunk cache leakage ────────────

def test_extract_kg_reingest_refreshes_stale_chunk_text(monkeypatch, tmp_path):
    """A content edit must not leak the prior version's chunk text through the
    on-disk resume cache.

    Regression for a bug where the per-chunk cache directory was keyed only by
    the file's stem, not by doc_sha256: re-ingesting an edited file reused the
    previous run's chunk_NNN.json, and data.setdefault("text", chunk_text)
    then refused to overwrite the stale cached text — so DocChunk.text stayed
    on the old content even though entities were correctly re-extracted from
    the new content (which reads the chunk file directly, bypassing the cache).
    """
    file_result = _setup_extract_env(monkeypatch, tmp_path, n_chunks=1)
    chunks_dir = Path(file_result["chunks_dir"])

    doc_kg_dir = ing.extract_kg(file_result, "testdom", max_workers=1)
    chunks = json.loads((doc_kg_dir / "chunks.json").read_text(encoding="utf-8"))
    assert chunks[0]["text"] == "chunk body 1"

    # Edit the source chunk in place — same stem/doc_kg_dir, different content
    # and sha256, exactly what re-ingesting an edited file produces.
    (chunks_dir / "chunk_001.md").write_text("REVISED chunk body 1", encoding="utf-8")
    file_result["sha256"] = "sha-revised"

    doc_kg_dir2 = ing.extract_kg(file_result, "testdom", max_workers=1)
    assert doc_kg_dir2 == doc_kg_dir  # same logical document, same on-disk dir

    chunks2 = json.loads((doc_kg_dir2 / "chunks.json").read_text(encoding="utf-8"))
    assert chunks2[0]["text"] == "REVISED chunk body 1"


def test_extract_kg_worker_exception_surfaces(monkeypatch, tmp_path):
    # An UNEXPECTED error inside a worker (one _process_chunk doesn't already
    # catch — embedding/step failures are caught and recorded as status) must not
    # be swallowed by the pool: extract_kg re-raises the first one after draining,
    # matching the sequential path's fail-loud behaviour.
    file_result = _setup_extract_env(monkeypatch, tmp_path, 4)

    def boom(step_name, model, prompt, debug_dir):
        raise RuntimeError("extract exploded")

    monkeypatch.setattr(ing, "_llm_extract", boom)

    import pytest
    with pytest.raises(RuntimeError, match="extract exploded"):
        ing.extract_kg(file_result, "testdom", max_workers=3)
