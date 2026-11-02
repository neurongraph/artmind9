"""`ingest retime --dryRun`: re-dating is computed from reads; nothing is written, committed or asked of an LLM."""
import json
import shutil

from click.testing import CliRunner

import artmind.ingest as ing
from _dry_run import assert_effect_shape
from artmind.cli import cli
from test_document_dates import _ingested_under_the_old_schema


def _run(*args):
    result = CliRunner().invoke(cli, ["ingest", "retime", "--domain", "journal", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _stub_commit(monkeypatch):
    commits = []
    monkeypatch.setattr(ing, "commit_to_graph", lambda d, dom, defer_rebuild=False: commits.append(d) or True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})
    return commits


def test_dry_run_writes_nothing_and_matches_the_real_effect(tmp_path, monkeypatch):
    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    commits = _stub_commit(monkeypatch)
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}

    dry = _run("--dryRun")

    assert commits == [] and {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
    assert dry["dry_run"] is True
    assert_effect_shape(dry["effect"])
    assert dry["effect"]["changes"] == [{"kind": "document_reextract", "count": 1, "sample": ["2026-01-07.md"]}]
    assert dry["effect"]["records"] == [".artmind/data/kg/journal"]

    real = _run()

    assert commits == [folder]
    assert real["dry_run"] is False
    assert real["effect"] == dry["effect"]


def test_nothing_to_retime_is_an_empty_effect(tmp_path, monkeypatch):
    _ingested_under_the_old_schema(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)
    _run()

    dry = _run("--dryRun")

    assert dry["effect"]["changes"] == [] and dry["effect"]["records"] == []


def test_redo_properties_dry_run_estimates_calls_and_asks_nothing(tmp_path, monkeypatch):
    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)
    monkeypatch.setattr(ing, "_llm_extract", lambda *a: (_ for _ in ()).throw(AssertionError("no LLM")))
    chunks = len(json.loads((folder / "chunks.json").read_text()))
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}

    dry = _run("--dryRun", "--redoProperties")

    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
    assert_effect_shape(dry["effect"], llm_calls=chunks)
    assert dry["effect"]["llm"]["model"]


def test_redo_properties_dry_run_names_the_documents_it_would_skip(tmp_path, monkeypatch):
    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)
    shutil.rmtree(folder / "chunks")

    dry = _run("--dryRun", "--redoProperties")

    assert "chunk extraction cache" in dry["skipped"][0]["skipped"]
    assert dry["effect"]["changes"] == [] and dry["effect"]["llm"]["calls"] == 0
