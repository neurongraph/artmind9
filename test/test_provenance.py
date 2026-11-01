"""Deterministic rendering of compact trace provenance."""

from pathlib import Path

from artmind.provenance import render, render_for_trace, select_final_evidence_span


def _provenance(*, passages=None):
    return {
        "documents": [{
            "doc_id": "doc-1",
            "path": "policies/Policy.md",
            "source_path": "policies/Policy.md",
            "domain": "banking",
            "chunks": [{
                "chunk_id": "doc-1_014", "sequence": 14, "name": "Chunk 14/20",
                "header_path": "Defaults", "passages": passages or [],
            }],
        }],
        "tables": [],
    }


def _cache(vault: Path, key="policy-cache") -> Path:
    target = vault / ".artmind" / "data" / "documents" / "markdowns" / f"{key}_chunks"
    target.mkdir(parents=True)
    chunk = target / "chunk_014.md"
    chunk.write_text("source", encoding="utf-8")
    return chunk


def _registry(monkeypatch, vault: Path, *, key="policy-cache"):
    monkeypatch.setattr("artmind.provenance._registry_row_by_artmind_id", lambda doc_id: {
        "artmind_id": doc_id, "domain": "banking", "path": "policies/Policy.md",
    })
    monkeypatch.setattr("artmind.provenance.paths.DB_PATH", vault / ".artmind" / "data" / "document_registry.db")
    monkeypatch.setattr("artmind.ingest._chunks_key", lambda domain, registered, artmind_id: key)


def test_render_document_and_resolved_graph_chunk(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _cache(vault)
    _registry(monkeypatch, vault)

    result = render(_provenance(), vault=vault)

    assert result.cited_documents == 1
    assert result.cited_chunks == 1
    assert result.diagnostics == []
    assert "[Policy.md](<policies/Policy.md>) — banking" in result.markdown
    assert "[Passage: Chunk 14 — Defaults]" in result.markdown
    assert ".artmind/data/documents/markdowns/policy-cache_chunks/chunk_014.md" in result.markdown


def test_render_keeps_document_when_chunk_cache_is_missing(tmp_path, monkeypatch):
    _registry(monkeypatch, tmp_path / "vault")
    result = render(_provenance(), vault=tmp_path / "vault")

    assert result.cited_documents == 1
    assert result.cited_chunks == 0
    assert result.markdown == "### Sources\n- [Policy.md](<policies/Policy.md>) — banking"
    assert result.diagnostics == ["chunk cache unavailable for doc-1_014"]


def test_render_includes_vector_passage_range(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _cache(vault)
    _registry(monkeypatch, vault)

    result = render(_provenance(passages=[{"char_start": 32, "char_end": 148}]), vault=vault)

    assert "(characters 32–148)" in result.markdown


def test_selects_last_productive_evidence_span_not_later_metadata():
    evidence = {"provenance": _provenance()}
    spans = [
        {"command": "query vector-text", "exit_code": 0, "rows": 1, "started_at": 1, "evidence": evidence},
        {"command": "query graph metadata", "exit_code": 0, "rows": 1, "started_at": 2, "evidence": {}},
    ]

    assert select_final_evidence_span(spans) is spans[0]


def test_render_for_trace_omits_no_evidence():
    assert render_for_trace([{"command": "query graph metadata", "exit_code": 0, "rows": 1, "evidence": {}}]).markdown is None


def test_render_never_guesses_a_chunk_folder_without_a_registry_row(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _cache(vault, key="Policy~unrelated")
    monkeypatch.setattr("artmind.provenance._registry_row_by_artmind_id", lambda doc_id: None)

    result = render(_provenance(), vault=vault)

    assert result.cited_chunks == 0
    assert result.markdown == "### Sources\n- [Policy.md](<policies/Policy.md>) — banking"
