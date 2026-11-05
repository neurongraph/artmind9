"""Deterministic rendering of compact trace provenance."""

from pathlib import Path

from artmind.provenance import merge_provenance, render, render_for_trace, select_final_evidence_span


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


def test_render_resolves_chunk_from_recorded_path_without_a_registry_row(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setattr("artmind.provenance._registry_row_by_artmind_id", lambda doc_id: None)
    monkeypatch.setattr("artmind.provenance.paths.DB_PATH", vault / ".artmind" / "data" / "document_registry.db")
    monkeypatch.setattr("artmind.ingest.MARKDOWNS_DIR", vault / ".artmind" / "data" / "documents" / "markdowns")
    _cache(vault, key="Policy~0123456789ab")
    provenance = _provenance()
    provenance["documents"][0]["path"] = ".artmind/data/documents/markdowns/Policy~0123456789ab.md"

    result = render(provenance, vault=vault)

    assert result.cited_chunks == 1
    assert ".artmind/data/documents/markdowns/Policy~0123456789ab_chunks/chunk_014.md" in result.markdown


def _id_only(**extra):
    return {"documents": [{"doc_id": "doc-1", "path": None, "source_path": None, "domain": "banking",
                           "title": "Policy", "chunks": [], **extra}], "tables": []}


def test_render_resolves_path_of_id_only_document_from_registry(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _registry(monkeypatch, vault)

    result = render(_id_only(), vault=vault)

    assert result.markdown == "### Sources\n- [Policy.md](<policies/Policy.md>) — banking"


def test_render_cites_id_only_document_by_title_when_registry_has_no_row(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setattr("artmind.provenance._registry_row_by_artmind_id", lambda doc_id: None)
    monkeypatch.setattr("artmind.provenance.paths.DB_PATH", vault / ".artmind" / "data" / "document_registry.db")

    result = render(_id_only(), vault=vault)

    assert result.markdown == "### Sources\n- Policy — banking"


def test_render_uses_original_of_a_converted_binary(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _registry(monkeypatch, vault)
    markdowns = vault / ".artmind" / "data" / "documents" / "markdowns"
    monkeypatch.setattr("artmind.ingest.MARKDOWNS_DIR", markdowns)
    monkeypatch.setattr("artmind.provenance._registry_row_by_artmind_id", lambda doc_id: {
        "artmind_id": doc_id, "domain": "banking", "path": str(markdowns / "Deal~0123456789ab.md")})
    monkeypatch.setattr("artmind.ingest._converted_owner", lambda key, domain: ("contracts/Deal.docx", "sha"))

    result = render(_id_only(), vault=vault)

    assert result.markdown == "### Sources\n- [Deal.docx](<contracts/Deal.docx>) — banking"


def _span(command, started, *docs):
    return {"command": command, "exit_code": 0, "rows": 1, "started_at": started, "evidence": {"provenance": {
        "documents": [{"doc_id": d, "path": f"{d}.md", "source_path": f"{d}.md", "domain": "banking",
                       "chunks": [{"chunk_id": f"{d}_{n:03d}", "sequence": n} for n in ns]} for d, ns in docs],
        "tables": []}}}


def test_render_for_trace_lists_final_evidence_first_then_earlier_queries(tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.provenance._registry_row_by_artmind_id", lambda doc_id: None)
    spans = [
        _span("query survey", 1, ("a", [1]), ("b", [2])),
        _span("query vector-text", 2, ("c", [3]), ("a", [4])),
        _span("query chunks", 3, ("b", [2])),
        {"command": "query domains-overview", "exit_code": 0, "rows": 2, "started_at": 4, "evidence": {}},
    ]

    result = render_for_trace(spans, vault=tmp_path)

    assert [line for line in result.markdown.splitlines() if line.startswith("- ")] == [
        "- [b.md](<b.md>) — banking", "- [c.md](<c.md>) — banking", "- [a.md](<a.md>) — banking"]
    assert result.cited_documents == 3


def test_merge_provenance_combines_chunks_of_a_repeated_document():
    merged = merge_provenance([
        _span("query chunks", 2, ("a", [4]))["evidence"]["provenance"],
        _span("query survey", 1, ("a", [1, 4]))["evidence"]["provenance"],
    ])

    (doc,) = merged["documents"]
    assert [c["chunk_id"] for c in doc["chunks"]] == ["a_004", "a_001"]


def test_merge_provenance_fills_a_missing_path_from_a_later_sighting():
    first = {"documents": [{"doc_id": "a", "path": None, "source_path": None, "title": "A", "chunks": []}], "tables": []}
    later = {"documents": [{"doc_id": "a", "path": "a.md", "source_path": "a.md", "chunks": []}], "tables": []}

    (doc,) = merge_provenance([first, later])["documents"]

    assert (doc["source_path"], doc["title"]) == ("a.md", "A")
