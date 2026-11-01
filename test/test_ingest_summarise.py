import json

from click.testing import CliRunner

import artmind.ingest as ing
from artmind import summary_tree as st
from artmind.cli import cli


def _stage(kg, domain, name, chunks, summaries=None):
    folder = kg / domain / name
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": f"doc-{name}", "name": f"{name}.md", "title": name}))
    (folder / "chunks.json").write_text(json.dumps(chunks))
    (folder / "observations.json").write_text("[]")
    if summaries is not None:
        (folder / st.SUMMARIES_FILE).write_text(json.dumps(summaries))
    return folder


def _env(monkeypatch, tmp_path):
    kg = tmp_path / "kg"
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "notes_schema.yaml").write_text("name: notes\ndescription: Notes.\n")
    monkeypatch.setattr(ing, "KG_DIR", kg)
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(ing, "resolve_llm_model", lambda env: "m")
    committed = []
    monkeypatch.setattr(ing, "commit_to_graph", lambda folder, domain, defer_rebuild=False: committed.append(folder.name) or True)
    return kg, committed


CHUNKS = [{"id": "c1", "doc_id": "doc-a", "text": "Alpha text."}, {"id": "c2", "doc_id": "doc-a", "text": "Beta text."}]


def test_dry_run_counts_calls_and_writes_nothing(monkeypatch, tmp_path):
    kg, committed = _env(monkeypatch, tmp_path)
    folder = _stage(kg, "notes", "a", CHUNKS)
    out = ing.summarise_staged(domain="notes", dry_run=True)
    assert out["documents"] == 1 and out["calls"] == 3          # 2 chunks + 1 reduce
    assert not (folder / st.SUMMARIES_FILE).exists() and committed == []


def test_missing_summaries_are_built_written_and_committed(monkeypatch, tmp_path):
    kg, committed = _env(monkeypatch, tmp_path)
    folder = _stage(kg, "notes", "a", CHUNKS)
    out = ing.summarise_staged(domain="notes")
    data = json.loads((folder / st.SUMMARIES_FILE).read_text())
    assert [c["chunk_id"] for c in data["chunks"]] == ["c1", "c2"] and data["document"]
    assert committed == ["a"] and out["summarised"] == 1 and out["failed_chunks"] == 0


def test_complete_folders_are_skipped(monkeypatch, tmp_path):
    kg, committed = _env(monkeypatch, tmp_path)
    folder = _stage(kg, "notes", "a", CHUNKS)
    ing.summarise_staged(domain="notes")
    committed.clear()

    def forbidden(*a, **k):
        raise AssertionError("asked again")

    monkeypatch.setattr(ing, "_summary_llm", forbidden)
    out = ing.summarise_staged(domain="notes")
    assert out["summarised"] == 0 and out["up_to_date"] == 1 and committed == []


def test_match_filters_folders_and_table_folders_are_skipped(monkeypatch, tmp_path):
    kg, committed = _env(monkeypatch, tmp_path)
    _stage(kg, "notes", "a", CHUNKS)
    _stage(kg, "notes", "b", [{"id": "x1", "doc_id": "doc-b", "text": "x"}])
    _stage(kg, "notes", "table__t", [])
    out = ing.summarise_staged(domain="notes", match="b*")
    assert committed == ["b"] and out["documents"] == 1


def test_cli_prints_the_result(monkeypatch, tmp_path):
    kg, _ = _env(monkeypatch, tmp_path)
    _stage(kg, "notes", "a", CHUNKS)
    result = CliRunner().invoke(cli, ["ingest", "summarise", "--domain", "notes", "--dry-run", "--compact"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["calls"] == 3


def test_stale_document_key_triggers_exactly_one_reduce_call(monkeypatch, tmp_path):
    """When chunk keys all match but the document key is stale (e.g. DOC_PROMPT_VERSION bump),
    exactly 1 call (the document reduce) is counted. Chunk summaries are not regenerated."""
    kg, committed = _env(monkeypatch, tmp_path)
    # Two chunks so a reduce call is needed
    chunks = [
        {"id": "c1", "doc_id": "doc-a", "text": "Alpha text."},
        {"id": "c2", "doc_id": "doc-a", "text": "Beta text."},
    ]
    # Build up-to-date chunk keys
    chunk_key_c1 = st.chunk_key("m", "Alpha text.")
    chunk_key_c2 = st.chunk_key("m", "Beta text.")
    # Use a deliberately wrong document key (simulates old DOC_PROMPT_VERSION)
    stale_doc_key = "stale_key_from_old_version"
    prior_summaries = {
        "version": st.FILE_VERSION,
        "model": "m",
        "prompt_version": st.SUMMARY_PROMPT_VERSION,
        "document": {"key": stale_doc_key, "summary": "Old summary."},
        "chunks": [
            {"chunk_id": "c1", "key": chunk_key_c1, "summary": "chunk 1 summary"},
            {"chunk_id": "c2", "key": chunk_key_c2, "summary": "chunk 2 summary"},
        ],
    }
    _stage(kg, "notes", "a", chunks, summaries=prior_summaries)
    out = ing.summarise_staged(domain="notes", dry_run=True)
    # 0 chunk calls + 1 reduce = 1 total
    assert out["calls"] == 1, f"expected exactly 1 call (reduce), got {out['calls']}"


def test_old_prompt_version_summaries_require_all_calls(monkeypatch, tmp_path):
    """summaries.json written with the old SUMMARY_PROMPT_VERSION (1) must be treated as stale:
    all chunk + reduce calls are counted by summarise_staged(dry_run=True)."""
    kg, committed = _env(monkeypatch, tmp_path)
    chunks = [
        {"id": "c1", "doc_id": "doc-a", "text": "Alpha text."},
        {"id": "c2", "doc_id": "doc-a", "text": "Beta text."},
    ]
    # Build chunk keys as the OLD prompt version (1) would have computed them
    monkeypatch.setattr(st, "SUMMARY_PROMPT_VERSION", 1)
    old_key_c1 = st.chunk_key("m", "Alpha text.")
    old_key_c2 = st.chunk_key("m", "Beta text.")
    old_doc_key = st.document_key("m", [old_key_c1, old_key_c2])
    # Restore the new version so subsequent calls see the bumped version
    monkeypatch.setattr(st, "SUMMARY_PROMPT_VERSION", 2)
    prior_summaries = {
        "version": st.FILE_VERSION,
        "model": "m",
        "prompt_version": 1,  # old version
        "document": {"key": old_doc_key, "summary": "Old doc summary."},
        "chunks": [
            {"chunk_id": "c1", "key": old_key_c1, "summary": "chunk 1 summary"},
            {"chunk_id": "c2", "key": old_key_c2, "summary": "chunk 2 summary"},
        ],
    }
    _stage(kg, "notes", "a", chunks, summaries=prior_summaries)
    out = ing.summarise_staged(domain="notes", dry_run=True)
    # 2 chunk calls + 1 reduce = 3 total (all stale because chunk keys changed)
    assert out["calls"] == 3, f"expected 3 calls (all stale), got {out['calls']}"
