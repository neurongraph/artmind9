"""A sweep skipped for a dead connection must surface (plan 2026-10-03, B2).

Job 0c616a6f: the rebuild committed, the Neo4j connection was dead for both
embed sweeps, and the job reported `completed` with `embedded: 0` for 2,801
entities. The sweeps' never-raising default stays (every per-commit caller
relies on it); `strict=True` is what the rebuild paths use.
"""
from __future__ import annotations

import pytest

import artmind.embed_sweep as embed_sweep
import artmind.ingest as ing
import artmind.projection as projection

REFUSED = "Couldn't connect to 127.0.0.1:7687 (reason [Errno 61] Connection refused)"


def _refuse(*args, **kwargs):
    raise OSError(REFUSED)


def test_a_strict_entity_sweep_raises_when_neo4j_is_unreachable(monkeypatch):
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", _refuse)

    with pytest.raises(ing.SweepSkipped, match=r"entity embed sweep skipped for general .*Connection refused"):
        ing._sweep_embeddings("general", [("a", "C", "general")], strict=True)


def test_the_default_entity_sweep_still_never_raises(monkeypatch):
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", _refuse)

    assert ing._sweep_embeddings("general", [("a", "C", "general")]) == 0


def test_a_strict_chunk_sweep_raises_and_the_default_does_not(monkeypatch):
    monkeypatch.setattr(embed_sweep, "embed_missing_chunk_embeddings", _refuse)

    with pytest.raises(ing.SweepSkipped, match=r"chunk embed sweep skipped for domain 'general'"):
        ing._sweep_chunk_embeddings(domain="general", strict=True)
    assert ing._sweep_chunk_embeddings(domain="general") == 0


def test_rebuild_projection_reports_both_skipped_sweeps(monkeypatch):
    monkeypatch.setattr(projection, "full_rebuild_batched", lambda domains=None: {"rebuilt": 1, "keys": 1})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: {("a", "C", "general")})
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", _refuse)
    monkeypatch.setattr(embed_sweep, "embed_missing_chunk_embeddings", _refuse)

    summary = ing.rebuild_projection("general")

    assert (summary["embedded"], summary["chunks_embedded"]) == (0, 0)
    assert len(summary["sweep_errors"]) == 2
    assert summary["sweep_errors"][0].startswith("entity embed sweep skipped for general")
    assert summary["sweep_errors"][1].startswith("chunk embed sweep skipped for domain 'general'")
    assert all("Connection refused" in e for e in summary["sweep_errors"])


def test_rebuild_projection_reports_no_sweep_errors_when_both_sweeps_ran(monkeypatch):
    seen: list = []
    monkeypatch.setattr(projection, "full_rebuild_batched", lambda domains=None: {"rebuilt": 1, "keys": 1})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: {("a", "C", "general")})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys, strict=False: seen.append(("e", domain, strict)) or 5)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda chunk_ids=None, domain=None, strict=False: seen.append(("c", domain, strict)) or 2)

    summary = ing.rebuild_projection("general")

    assert seen == [("e", "general", True), ("c", "general", True)]
    assert (summary["embedded"], summary["chunks_embedded"], summary["sweep_errors"]) == (5, 2, [])
