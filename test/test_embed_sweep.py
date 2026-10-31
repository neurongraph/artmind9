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



from _neo4j_recorder import Recorder

from artmind import embed_sweep, passages

KEY = "ek"


def _respond(build_rows=(), embed_rows=(), chat_rows=()):
    state = {"build": list(build_rows), "embed": list(embed_rows), "chat": list(chat_rows)}

    def respond(cypher, params):
        if "AS chunk_text" in cypher:               # chunks needing passages
            rows, state["build"] = state["build"], []
            return rows
        if "MATCH (p:DocPassage)" in cypher and "RETURN p.id AS id" in cypher:
            rows, state["embed"] = state["embed"], []
            return rows
        if "MATCH (c:UserChat)" in cypher and "RETURN c.id AS id" in cypher:
            rows, state["chat"] = state["chat"], []
            return rows
        return []
    return respond


def test_builds_passages_for_chunks_without_them(monkeypatch):
    calls = []
    monkeypatch.setattr(embed_sweep.passage_store, "write_chunk_passages",
                        lambda tx, cid, rows: calls.append((cid, rows)) or len(rows))
    s = Recorder(_respond(build_rows=[{"id": "d1_001", "doc_id": "d1", "_domain": "p",
                                       "chunk_text": "Some text.", "header_path": "Intro", "title": "Doc"}]))
    out = embed_sweep.reconcile_passages(s, embed=lambda t: [1.0], embed_key=KEY, cache=None)
    (cypher, params), = s.queries_containing("AS chunk_text")
    assert "NOT EXISTS { MATCH (:DocPassage)-[:PART_OF]->(c) }" in cypher
    assert "p._split_key <> $split_key" in cypher and "trim(coalesce(c.text, '')) <> ''" in cypher
    assert params["split_key"] == passages.split_key()
    assert calls[0][0] == "d1_001" and calls[0][1][0]["props"]["context"] == "Doc > Intro"
    assert out["chunks_split"] == 1


def test_embeds_stale_passages_with_context_and_key():
    s = Recorder(_respond(embed_rows=[{"id": "d1_001_p01", "text": "Body.", "context": "Doc > Intro"}]))
    seen = []
    out = embed_sweep.reconcile_passages(s, embed=lambda t: seen.append(t) or [0.5], embed_key=KEY, cache=None)
    (select, sp), = s.queries_containing("RETURN p.id AS id")
    assert "p.embedding IS NULL OR p._embed_key IS NULL OR p._embed_key <> $embed_key" in select
    assert sp["embed_key"] == KEY
    assert seen == ["Doc > Intro\nBody."]
    (write, wp), = s.queries_containing("SET p.embedding = $embedding, p._embed_key = $embed_key")
    assert wp == {"id": "d1_001_p01", "embedding": [0.5], "embed_key": KEY}
    assert out["embedded"] == 1 and out["remaining"] == 0


def test_a_failed_embedding_is_skipped_and_the_loop_ends():
    rows = [{"id": f"p{i}", "text": "t", "context": ""} for i in range(3)]
    s = Recorder(_respond(embed_rows=rows))

    def boom(t):
        raise RuntimeError("down")
    out = embed_sweep.reconcile_passages(s, embed=boom, embed_key=KEY, cache=None, batch_size=3)
    assert out == {"chunks_split": 0, "embedded": 0, "remaining": 3, "chats_embedded": 0}
    assert not s.queries_containing("SET p.embedding")


def test_scopes_reach_both_steps():
    s = Recorder()
    embed_sweep.reconcile_passages(s, chunk_ids=["d1_001"], embed=lambda t: [1.0], embed_key=KEY, cache=None)
    for cypher, params in s.calls:
        if "AS chunk_text" in cypher:
            assert "c.id IN $chunk_ids" in cypher
        if "RETURN p.id AS id" in cypher:
            assert "p.chunk_id IN $chunk_ids" in cypher
    assert not s.queries_containing("MATCH (c:UserChat)")   # chats only on unscoped/domain runs


def test_empty_scope_is_a_no_op():
    s = Recorder()
    assert embed_sweep.reconcile_passages(s, chunk_ids=[], embed_key=KEY) == \
        {"chunks_split": 0, "embedded": 0, "remaining": 0, "chats_embedded": 0}
    assert s.calls == []


def test_unscoped_run_reembeds_stale_chats():
    s = Recorder(_respond(chat_rows=[{"id": "u1", "raw_text": "hi"}]))
    out = embed_sweep.reconcile_passages(s, embed=lambda t: [1.0], embed_key=KEY, cache=None)
    (select, _), = s.queries_containing("MATCH (c:UserChat)")
    assert "c._embed_key <> $embed_key" in select
    assert out["chats_embedded"] == 1


# ── write-to-graph: --noEmbed actually gates the sweep ──────────────────────
#
# No CLI test file for write-to-graph existed before this -- covered here
# since this file is already the home for everything about the chunk-embed
# sweep. Single-document mode is enough (per review): the folder/batch path
# calls the same `_run_passage_sweep` helper, gated the same way.

def _stage_document_json(tmp_path, monkeypatch, domain="general", name="sample.pdf"):
    """Fake just enough of a KG staging tree for write-to-graph's single-document
    mode to get past its file checks without touching commit_to_graph for real."""
    import paths

    kg_dir = tmp_path / "kg"
    doc_dir = kg_dir / domain / Path(name).stem
    doc_dir.mkdir(parents=True)
    (doc_dir / "document.json").write_text("{}")
    monkeypatch.setattr(paths, "KG_DIR", kg_dir)
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
