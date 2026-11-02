"""`vault sync --dryRun`: classifies the committed diff, applies and stamps nothing; the effect matches the real run."""
import json

from click.testing import CliRunner

import artmind.vault as vault_mod
from _dry_run import assert_effect_shape
from artmind.cli import cli
from test_vault_sync import (  # noqa: F401  (fixtures: git repo, fake graph bookmark store, no worker)
    _bookmarks, _commit_all, _no_worker, _patch_ingest_and_projection, _patch_kg_dir,
    _patch_structured_text_dir, _write_doc_folder, graph, repo,
)


def _run(repo, *args):
    result = CliRunner().invoke(cli, ["vault", "sync", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _world(repo, monkeypatch):
    monkeypatch.setattr(vault_mod, "resolve_vault", lambda *a, **k: repo)
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _write_doc_folder(kg_dir, "banking", "doc2", "docid-2")
    _commit_all(repo, "add two documents")
    return _patch_ingest_and_projection(monkeypatch)


def test_dry_run_applies_nothing_and_matches_the_real_effect(repo, monkeypatch, graph):
    calls = _world(repo, monkeypatch)

    dry = _run(repo, "--bootstrapEmpty", "--dryRun")

    assert calls["write_to_neo4j"] == [] and calls["rebuild_in_batches"] == [] and calls["sweeps"] == []
    assert graph.bookmark_writes == []
    assert dry["dry_run"] is True and dry["replay"] == 2
    assert_effect_shape(dry["effect"])
    assert dry["effect"]["changes"] == [
        {"kind": "document_ingest", "count": 2, "sample": ["banking/doc1", "banking/doc2"]}]
    assert dry["effect"]["travels"] is False

    real = _run(repo, "--bootstrapEmpty")

    assert sorted(p.rsplit("/", 1)[1] for p, _ in calls["write_to_neo4j"]) == ["doc1", "doc2"]
    assert "dry_run" not in real
    assert real["effect"] == dry["effect"]


def test_a_domain_scoped_sync_says_the_bookmark_stays(repo, monkeypatch, graph):
    _world(repo, monkeypatch)

    dry = _run(repo, "--bootstrapEmpty", "--domain", "banking", "--dryRun")

    assert "leaves the bookmark" in dry["effect"]["summary"]


def test_bootstrap_synced_dry_run_stamps_nothing(repo, monkeypatch, graph):
    _world(repo, monkeypatch)

    dry = _run(repo, "--bootstrapSynced", "--dryRun")

    assert graph.bookmark_writes == [] and dry["dry_run"] is True
    assert_effect_shape(dry["effect"])
    real = _run(repo, "--bootstrapSynced")
    assert real["effect"] == dry["effect"] and graph.bookmark_writes
