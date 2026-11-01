"""Embeddings are stripped from persisted staging (docs/vault.md, "Embeddings").

They are a pure function of (text, model): derived, deterministic, and free to
recompute locally. Keeping them out of committed staging is what stops git
history accumulating undeltable float noise.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from artmind.ingest import strip_embeddings


def test_an_embedding_is_removed():
    chunk = {"chunk_id": "c1", "text": "hello", "embedding": [0.1, 0.2, 0.3]}

    assert strip_embeddings(chunk) == {"chunk_id": "c1", "text": "hello"}


def test_everything_else_survives():
    """Only the vector goes -- the chunk's identity, text and filing metadata
    are what the graph write needs."""
    chunk = {
        "chunk_id": "c1", "text": "hello", "embedding": [0.1],
        "doc_id": "d1", "domain": "general", "project": "p", "area": "a",
        "prev_chunk_id": None, "next_chunk_id": "c2",
    }

    stripped = strip_embeddings(chunk)

    assert "embedding" not in stripped
    assert stripped["chunk_id"] == "c1"
    assert stripped["next_chunk_id"] == "c2"
    assert stripped["project"] == "p"


def test_a_chunk_without_an_embedding_is_unchanged():
    chunk = {"chunk_id": "c1", "text": "hello"}

    assert strip_embeddings(chunk) == chunk


def test_the_original_is_not_mutated():
    """The in-memory chunk keeps its vector -- it is still written to the graph
    in this same run. Only what lands on disk is stripped."""
    chunk = {"chunk_id": "c1", "embedding": [0.1]}

    strip_embeddings(chunk)

    assert chunk["embedding"] == [0.1]


def test_load_staged_ignores_a_stray_legacy_sidecar(tmp_path):
    """Old embeddings.json files may remain on disk; nothing merges them in."""
    from artmind.ingest import _load_staged

    (tmp_path / "chunks.json").write_text(
        json.dumps([{"id": "d1_001", "doc_id": "d1", "text": "hello"}]),
        encoding="utf-8",
    )
    (tmp_path / "embeddings.json").write_text(json.dumps({"d1_001": [0.4, 0.5]}), encoding="utf-8")

    staged = _load_staged(tmp_path, "general")

    assert "embedding" not in staged["chunks"][0]


# ── write-to-graph: --noEmbed actually gates the sweep ──────────────────────
#
# Single-document mode is enough (per review): the folder/batch path calls the
# same `_run_passage_sweep` helper, gated the same way.

def _stage_document_json(tmp_path, monkeypatch, domain="general", name="sample.pdf"):
    """Fake just enough of a KG staging tree for write-to-graph's single-document
    mode to get past its file checks without touching commit_to_graph for real."""
    import paths

    import artmind.ingest as ing

    kg_dir = tmp_path / "kg"
    monkeypatch.setattr(paths, "KG_DIR", kg_dir)
    monkeypatch.setattr(ing, "KG_DIR", kg_dir)
    # Named the way the command finds it: by the document's key.
    doc_dir = ing._document_staging_dir(domain, Path(name), "")
    doc_dir.mkdir(parents=True)
    (doc_dir / "document.json").write_text("{}")
    return {"registered_path": name}


def test_no_embed_skips_the_sweep(tmp_path, monkeypatch):
    from artmind.cli import cli

    file_result = _stage_document_json(tmp_path, monkeypatch)
    runner = CliRunner()

    with patch("artmind.cli._build_file_result_from_db", return_value=file_result), \
         patch("artmind.cli.commit_to_graph", return_value=True), \
         patch("artmind.cli._run_passage_sweep") as mock_sweep:
        result = runner.invoke(
            cli, ["ingest", "write-to-graph", "sample.pdf", "--domain", "general", "--noEmbed"]
        )

    assert result.exit_code == 0, result.output
    mock_sweep.assert_not_called()


def test_without_no_embed_the_sweep_runs(tmp_path, monkeypatch):
    """The flip side of the test above -- otherwise "skips" could pass simply
    because the sweep is never wired in at all."""
    from artmind.cli import cli

    file_result = _stage_document_json(tmp_path, monkeypatch)
    runner = CliRunner()

    with patch("artmind.cli._build_file_result_from_db", return_value=file_result), \
         patch("artmind.cli.commit_to_graph", return_value=True), \
         patch("artmind.cli._run_passage_sweep") as mock_sweep:
        result = runner.invoke(
            cli, ["ingest", "write-to-graph", "sample.pdf", "--domain", "general"]
        )

    assert result.exit_code == 0, result.output
    mock_sweep.assert_called_once()


# ── write-to-graph --folder: atomic-write scratch siblings are skipped ──────
#
# A crash mid `write_dir_atomic` (artmind/atomic_dir.py) can leave a
# `<name>.artmind-tmp`/`<name>.artmind-old` sibling next to a real staging
# folder, both gitignored and both still carrying a document.json. Batch
# write-to-graph must never treat either as a document to write.

def test_folder_mode_skips_atomic_write_scratch_siblings(tmp_path, monkeypatch):
    from artmind.cli import cli

    folder = tmp_path / "kg" / "general"
    for name in ("doc1", "doc1.artmind-old", "doc2.artmind-tmp", "doc2"):
        d = folder / name
        d.mkdir(parents=True)
        (d / "document.json").write_text("{}")

    written = []
    runner = CliRunner()

    with patch(
        "artmind.cli.commit_to_graph",
        side_effect=lambda doc_kg_dir, domain: written.append(doc_kg_dir.name) or True,
    ), patch("artmind.cli._run_passage_sweep"):
        result = runner.invoke(
            cli, ["ingest", "write-to-graph", "--folder", str(folder), "--domain", "general"]
        )

    assert result.exit_code == 0, result.output
    assert sorted(written) == ["doc1", "doc2"]
