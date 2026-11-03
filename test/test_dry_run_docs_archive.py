"""`docs archive --dryRun`: reports the bundle, graph rows and vault file; creates and deletes nothing."""
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import artmind.archive as archive
from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher, write_calls
from artmind.cli import cli

KEY = "acme ltd|ORGANISATION|demo.one"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import artmind.db as db
    import artmind.ingest as ing
    import paths

    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "policy.md"
    note.write_text("---\n_artmind_id: doc-1\n---\nBody\n")
    kg = tmp_path / "kg"
    kg.mkdir()
    monkeypatch.setattr(archive, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(archive, "ARTMIND_ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(archive, "KG_DIR", kg)
    monkeypatch.setattr(archive, "ORIGINALS_DIR", tmp_path / "originals")
    monkeypatch.setattr(ing, "KG_DIR", kg)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path / "_Topics")
    monkeypatch.setattr(archive, "resolve_document_id", lambda n, d: "doc-1")
    monkeypatch.setattr(archive, "_document_info", lambda i: {"path": "policy.md", "version": 1, "name": "policy"})
    monkeypatch.setattr(archive, "_observation_valid_time_span", lambda i: (None, None))
    monkeypatch.setattr("artmind.document_identity.ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr("artmind.vault_git.current_commit", lambda: None)

    def respond(cypher, params):
        if "DISTINCT o.key AS key" in cypher:
            return [{"key": KEY}]
        if "count(o) AS n" in cypher or "DETACH DELETE o" in cypher:
            return [{"n": 3}]
        return []

    recorder = WritableRecorder(respond)
    monkeypatch.setattr(archive, "read_session", lambda *a, **k: recorder)
    monkeypatch.setattr(archive, "neo4j_session", lambda *a, **k: recorder)
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {"rebuilt": len(keys)})
    return recorder, vault, tmp_path


def _run(*args):
    result = CliRunner().invoke(cli, ["docs", "archive", "--domain", "demo.one", "--documentName", "policy",
                                      *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_changes_nothing_and_does_not_ask(world):
    recorder, vault, tmp = world

    payload = _run("--dryRun")  # no --yes: a dry run never prompts

    assert_no_write_cypher(recorder)
    assert (vault / "policy.md").exists() and not (tmp / "archive").exists()
    assert payload["dry_run"] is True and payload["manifest"] is None
    assert_effect_shape(payload["effect"])
    assert [c["kind"] for c in payload["effect"]["changes"]] == ["document_archive", "observation_move", "entity_change"]
    assert payload["effect"]["changes"][1]["count"] == 3
    assert payload["effect"]["records"] == ["policy.md"]
    assert payload["effect"]["undo"] == "artmind docs restore-from-archive --id doc-1"


def test_real_run_has_the_same_effect_and_acts(world):
    recorder, vault, tmp = world

    dry = _run("--dryRun")
    real = _run("--yes")

    assert real["effect"] == dry["effect"] and "dry_run" not in real
    assert not (vault / "policy.md").exists() and (tmp / "archive" / "doc-1" / "manifest.json").is_file()
    assert write_calls(recorder)


def test_dry_run_refuses_when_a_bundle_already_exists(world):
    recorder, vault, tmp = world
    (tmp / "archive" / "doc-1").mkdir(parents=True)

    result = CliRunner().invoke(cli, ["docs", "archive", "--domain", "demo.one", "--documentName", "policy", "--dryRun"])

    assert result.exit_code == 1 and "a bundle already exists" in result.output
