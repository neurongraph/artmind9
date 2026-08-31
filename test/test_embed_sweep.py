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


# ── _load_staged: the sidecar merges back in at graph-write time ────────────
#
# A staged chunks.json entry keys its identifier "id"; the sidecar (see
# write_embedding_sidecar) keys the same identifier "chunk_id". Same value,
# different literal key name on each side -- _load_staged has to bridge that
# itself, and a mismatch there fails silently (no vector merges in, no error).

def test_load_staged_merges_the_sidecar_into_a_chunk_missing_its_vector(tmp_path):
    """This is what lets the graph write get a vector for a chunk whose
    committed chunks.json entry has none -- both right after an ingest and
    when replaying staging into a wiped graph later (docs/vault.md,
    "Embeddings")."""
    from artmind.ingest import _load_staged, write_embedding_sidecar

    (tmp_path / "chunks.json").write_text(
        json.dumps([{"id": "d1_001", "doc_id": "d1", "text": "hello"}]),
        encoding="utf-8",
    )
    write_embedding_sidecar(tmp_path, [{"chunk_id": "d1_001", "embedding": [0.4, 0.5]}])

    staged = _load_staged(tmp_path, "general")

    assert staged["chunks"][0]["embedding"] == [0.4, 0.5]


def test_load_staged_leaves_a_chunk_with_no_matching_sidecar_entry_alone(tmp_path):
    """A fresh clone (no sidecar) or a chunk the sidecar doesn't cover must
    come back with no vector rather than erroring -- the sweep (Task 2) is
    what repairs it."""
    from artmind.ingest import _load_staged, write_embedding_sidecar

    (tmp_path / "chunks.json").write_text(
        json.dumps([{"id": "d1_001", "doc_id": "d1", "text": "hello"}]),
        encoding="utf-8",
    )
    write_embedding_sidecar(tmp_path, [{"chunk_id": "some-other-chunk", "embedding": [0.9]}])

    staged = _load_staged(tmp_path, "general")

    assert "embedding" not in staged["chunks"][0]


def test_load_staged_does_not_override_an_embedding_already_present(tmp_path):
    """A chunks.json that already carries a vector (e.g. hand-authored test
    fixtures, or a pre-sidecar staging tree) must not be clobbered by a
    sidecar entry for the same chunk."""
    from artmind.ingest import _load_staged, write_embedding_sidecar

    (tmp_path / "chunks.json").write_text(
        json.dumps([{"id": "d1_001", "doc_id": "d1", "embedding": [0.1]}]),
        encoding="utf-8",
    )
    write_embedding_sidecar(tmp_path, [{"chunk_id": "d1_001", "embedding": [0.9, 0.9]}])

    staged = _load_staged(tmp_path, "general")

    assert staged["chunks"][0]["embedding"] == [0.1]
