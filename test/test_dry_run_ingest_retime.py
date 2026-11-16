"""`ingest retime --dryRun`: re-dating is computed from reads; nothing is written, committed or asked of an LLM."""
import json

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
    from artmind import backfill

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    commits = _stub_commit(monkeypatch)
    no_llm = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM"))  # noqa: E731
    monkeypatch.setattr(ing, "_llm_extract", no_llm)
    monkeypatch.setattr(backfill, "_ask", no_llm)
    chunks = len(json.loads((folder / "chunks.json").read_text()))
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}

    dry = _run("--dryRun", "--redoProperties")

    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before and commits == []
    assert_effect_shape(dry["effect"], llm_calls=chunks)
    assert dry["effect"]["llm"]["model"]

    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: ([{"id": "o1", "properties": {"date_or_time": "2026-01-07"}}], True))
    real = _run("--redoProperties")
    assert real["effect"] == dry["effect"] and real["committed"] == 1 and commits == [folder]


def test_redo_properties_does_not_plan_a_folder_without_a_dated_class(tmp_path, monkeypatch):
    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)
    _run()  # dates settled: only the redo could change anything now
    obs = json.loads((folder / "observations.json").read_text())
    (folder / "observations.json").write_text(json.dumps([{**o, "entity_class": "PLACE"} for o in obs]))
    _run()  # PLACE declares nothing: the EVENT key is dropped, then nothing is left to retime

    dry = _run("--dryRun", "--redoProperties")

    assert dry["skipped"] == [] and dry["changed"] == 0
    assert dry["effect"]["changes"] == [] and dry["effect"]["llm"]["calls"] == 0


def test_dry_run_names_the_property_values_it_would_drop(tmp_path, monkeypatch):
    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)
    _run()  # dates settled
    obs = json.loads((folder / "observations.json").read_text())
    (folder / "observations.json").write_text(json.dumps([{**o, "mood": "calm"} for o in obs]))
    dry = _run("--dryRun")
    real = _run()
    assert {"kind": "property_values_dropped", "count": 1, "sample": ["mood"]} in dry["effect"]["changes"]
    assert real["effect"] == dry["effect"]


def test_a_failed_redo_commit_names_the_retry_command(tmp_path, monkeypatch):
    from artmind import backfill

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)
    _run()  # dates settled: only the redo writes now
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: False)
    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: ([{"id": "o1", "properties": {"date_or_time": "2026-01-05"}}], True))

    r = CliRunner().invoke(cli, ["ingest", "retime", "--domain", "journal", "--redoProperties", "--compact"])

    assert r.exit_code == 1
    out = json.loads(r.stdout)
    assert out["next"] == f"artmind ingest backfill-properties --retryCommit {out['redo_run_id']}"
    assert "--retryCommit" in r.output.split(r.stdout.strip())[-1]
