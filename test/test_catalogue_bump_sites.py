"""Every graph write path that changes what a catalogue reads bumps the domain
generation inside its own transaction (spec 2026-11-02 §3).

Each test drives the real function over a recording transaction and asserts the
`:DomainState` statement is among the statements it sent. The last test fails
when a listed function stops naming the helper at all.
"""
import inspect
import json
from contextlib import contextmanager

import pytest
from _neo4j_recorder import Recorder

from artmind import ingest, lifecycle, passage_store, projection, synthesize
from artmind.observations import key_string


def _bumps(calls):
    return [p for c, p in calls if "MERGE (s:DomainState" in c]


def test_document_commit_bumps_the_document_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(passage_store, "write_chunk_passages", lambda tx, cid, rows: len(rows))
    folder = tmp_path / "doc"
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps({"id": "doc-1", "name": "a.md"}))
    (folder / "chunks.json").write_text("[]")
    (folder / "observations.json").write_text("[]")
    (folder / "relationships.json").write_text("[]")
    tx = Recorder(lambda c, p: [])
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id: set())
    ingest._commit_document_tx(tx, ingest._load_staged(folder, "papers"), True)
    assert [b["domains"] for b in _bumps(tx.calls)] == [["papers"]]


@pytest.mark.parametrize("to_history", [True, False])
def test_retire_and_restore_bump_the_document_domain(monkeypatch, to_history):
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id: set())
    tx = Recorder(lambda c, p: [{"n": 0}])
    lifecycle._transition(tx, "d1", to_history=to_history, rebuild=False)
    bumps = [(c, p) for c, p in tx.calls if "MERGE (s:DomainState" in c]
    assert len(bumps) == 1
    assert "d.id = $doc_id" in bumps[0][0] and bumps[0][1]["doc_id"] == "d1"


def test_projection_rebuild_bumps_each_domain_it_touched_once():
    keys = {("a", "THING", "bank.ref"), ("b", "THING", "bank.ref"), ("c", "THING", "other")}
    tx = Recorder()
    projection.rebuild(tx, keys, same_as_groups=[])
    assert [b["domains"] for b in _bumps(tx.calls)] == [["bank.ref", "other"]]


def test_projection_rebuild_of_no_keys_bumps_nothing():
    tx = Recorder()
    projection.rebuild(tx, set(), same_as_groups=[])
    assert _bumps(tx.calls) == []


def test_synthesis_write_bumps_the_entity_domain(monkeypatch):
    from unittest.mock import MagicMock, patch

    monkeypatch.setattr(synthesize.projection, "read_latest_observations",
                        lambda tx, key: [{"id": "o1", "description": "A widget."}])
    monkeypatch.setattr(synthesize, "call_llm", lambda *a, **k: "{}")
    monkeypatch.setattr(synthesize, "parse_json_response", lambda raw: {"description": "Clean."})
    monkeypatch.setattr(synthesize, "embed_document", lambda m, t: [0.1])
    monkeypatch.setattr(synthesize.projection, "rebuild_key", lambda *a, **k: "rebuilt")
    monkeypatch.setattr("artmind.curation_records.write_record", lambda *a, **k: "fp")
    calls = []
    session = MagicMock()
    session.execute_read.side_effect = lambda fn: fn(session)
    session.execute_write.side_effect = lambda fn: fn(session)
    session.run.side_effect = lambda cypher, **kw: calls.append((cypher, kw)) or MagicMock()
    with patch("artmind.synthesize.neo4j_session") as ctx:
        ctx.return_value.__enter__.return_value = session
        out = synthesize.synthesize_key(("w", "THING", "bank.ref"), "W", "THING", model="m", embed_model="e",
                                        same_as_groups=[])
    assert out["status"] == "synthesized"
    assert [b["domains"] for b in _bumps(calls)] == [["bank.ref"]]
    # the bump follows the entity write, inside the same write function
    texts = [c for c, _ in calls]
    assert max(i for i, c in enumerate(texts) if "e.embedding = $embedding" in c) < \
        next(i for i, c in enumerate(texts) if "MERGE (s:DomainState" in c)


# The functions that own a write path. If one of these stops naming the helper,
# the catalogue goes stale silently -- so this fails instead.
_SITES = [
    (ingest, "_commit_document_tx"),
    (lifecycle, "_transition"),
    (projection, "rebuild"),
    (synthesize, "synthesize_key"),
    ("artmind.conflicts", "resolve_conflict"),
]


@pytest.mark.parametrize("module,name", _SITES, ids=[n for _, n in _SITES])
def test_every_write_path_still_calls_the_bump(module, name):
    import importlib

    mod = importlib.import_module(module) if isinstance(module, str) else module
    source = inspect.getsource(getattr(mod, name))
    assert "catalogue_state.bump" in source, f"{name} no longer bumps the domain generation"
