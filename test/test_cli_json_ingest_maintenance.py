"""write-to-graph, pull-kg and refine-graph print one JSON envelope on stdout; their text is on stderr."""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from artmind.cli import cli
from test_embed_sweep import _stage_document_json


def _payload(result):
    payload = json.loads(result.stdout)
    assert {"command", "warnings", "next"} <= set(payload)
    return payload


def test_write_to_graph_single_document(tmp_path, monkeypatch):
    file_result = _stage_document_json(tmp_path, monkeypatch)
    with patch("artmind.cli._build_file_result_from_db", return_value=file_result), \
         patch("artmind.cli.commit_to_graph", return_value=True), \
         patch("artmind.cli._run_passage_sweep"):
        result = CliRunner().invoke(
            cli, ["ingest", "write-to-graph", "sample.pdf", "--domain", "general", "--noEmbed", "--compact"])
    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload["command"] == "ingest write-to-graph"
    assert (payload["mode"], payload["written"], payload["failed"], payload["embedded"]) == ("document", 1, 0, False)


def test_write_to_graph_folder_reports_failures_and_keeps_the_exit_code(tmp_path):
    folder = tmp_path / "kg" / "general"
    for name in ("doc1", "doc2"):
        (folder / name).mkdir(parents=True)
        (folder / name / "document.json").write_text("{}")
    with patch("artmind.cli.commit_to_graph", side_effect=lambda d, domain: d.name == "doc1"), \
         patch("artmind.cli._run_passage_sweep") as sweep:
        result = CliRunner().invoke(
            cli, ["ingest", "write-to-graph", "--folder", str(folder), "--domain", "general", "--compact"])
    assert result.exit_code == 1
    payload = _payload(result)
    assert (payload["mode"], payload["documents"], payload["written"], payload["failed"]) == ("folder", 2, 1, 1)
    assert payload["failed_documents"] == ["doc2"]
    sweep.assert_called_once()


def test_pull_kg_prints_the_summary_as_json():
    summary = {"pulled_count": 3, "domain": "sales", "repo_url": "https://example.test/r.git", "conflicts": []}
    with patch("artmind.cli.pull_kg_fn", return_value=summary):
        result = CliRunner().invoke(cli, ["ingest", "pull-kg", "--repo", "https://example.test/r.git",
                                          "--repo-path", "data/kg/sales", "--domain", "sales", "--compact"])
    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload["command"] == "ingest pull-kg" and payload["pulled_count"] == 3 and payload["conflicts"] == []


def _refine(monkeypatch, tmp_path, report, *args):
    import artmind.cli as cli_module

    monkeypatch.setattr(cli_module, "refine_graph", lambda **kw: report)
    monkeypatch.setattr(cli_module, "REFINE_DIR", tmp_path)
    return CliRunner().invoke(cli_module.cli, ["ingest", "refine-graph", "--compact", *args])


def test_refine_graph_dry_run_json_and_text(monkeypatch, tmp_path):
    report = {"proposed_merges": {"a": "b", "c": "b"}, "stats": {"proposed": 2}, "within_document_merges": 1}
    result = _refine(monkeypatch, tmp_path, report, "--dry-run")
    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload["dry_run"] is True and payload["proposed"] == 2 and payload["within_document_merges"] == 1
    assert payload["output_file"] == str(tmp_path / "proposed_merges.json")
    assert payload["next"] == f"artmind ingest refine-graph --from-file {tmp_path / 'proposed_merges.json'}"
    assert "Dry-run complete — 2 merge(s) proposed" in result.stderr


def test_refine_graph_proposal_run(monkeypatch, tmp_path):
    report = {"proposed_merges": {}, "stats": {"proposed": 4, "skipped": 1}, "skipped_cross_domain": {"x": ["a", "b"]}}
    result = _refine(monkeypatch, tmp_path, report)
    payload = _payload(result)
    assert payload["dry_run"] is False and payload["stats"] == {"proposed": 4, "skipped": 1}
    assert payload["skipped_cross_domain"] == {"x": ["a", "b"]} and payload["next"] == "artmind sameas list"
    assert "Proposals written — proposed=4" in result.stderr
