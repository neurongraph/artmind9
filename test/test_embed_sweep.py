"""Embeddings are stripped from persisted staging (docs/vault.md, "Embeddings").

They are a pure function of (text, model): derived, deterministic, and free to
recompute locally. Keeping them out of committed staging is what stops git
history accumulating undeltable float noise.
"""
from __future__ import annotations

import json

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


def test_the_sidecar_round_trips(tmp_path):
    """Vectors move out of committed staging, not out of existence -- otherwise
    every ingest computes each embedding twice."""
    from artmind.ingest import read_embedding_sidecar, write_embedding_sidecar

    write_embedding_sidecar(tmp_path, [
        {"chunk_id": "c1", "embedding": [0.1, 0.2]},
        {"chunk_id": "c2", "embedding": [0.3]},
    ])

    assert read_embedding_sidecar(tmp_path) == {"c1": [0.1, 0.2], "c2": [0.3]}


def test_a_missing_sidecar_reads_as_empty(tmp_path):
    """The normal state after a fresh clone -- not an error."""
    from artmind.ingest import read_embedding_sidecar

    assert read_embedding_sidecar(tmp_path) == {}


def test_chunks_without_vectors_write_no_sidecar(tmp_path):
    from artmind.ingest import write_embedding_sidecar

    write_embedding_sidecar(tmp_path, [{"chunk_id": "c1", "text": "x"}])

    assert not (tmp_path / "embeddings.json").exists()
