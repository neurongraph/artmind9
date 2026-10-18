"""`ingest empty-extractions`: staged notes whose extraction is empty although
their text is not (spec 2026-10-18-extraction-loss-guard-design.md §3.3).
Synthetic staging folders only; no Neo4j, no LLM."""
import json

from click.testing import CliRunner

import paths
from artmind.cli import cli
from artmind.staging_audit import empty_extractions


def _stage(kg, domain, stem, *, text, observations):
    folder = kg / domain / stem
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps(
        {"id": f"id-{stem}", "path": f"notes/{stem}.md", "_domain": domain}), encoding="utf-8")
    (folder / "chunks.json").write_text(json.dumps(
        [{"id": f"id-{stem}_001", "text": text}]), encoding="utf-8")
    (folder / "observations.json").write_text(json.dumps(observations), encoding="utf-8")
    return folder


def test_lists_only_empty_notes_with_enough_text(tmp_path):
    kg = tmp_path / "kg"
    _stage(kg, "journal", "emptied", text="x" * 300, observations=[])
    _stage(kg, "journal", "short", text="x" * 50, observations=[])
    _stage(kg, "journal", "fine", text="x" * 300, observations=[{"id": "o1"}])

    report = empty_extractions(kg, min_chars=200)

    assert report == {"notes": [{"path": "notes/emptied.md", "domain": "journal", "chars": 300, "chunks": 1}],
                      "scanned": 3}


def test_domain_filter_matches_the_family(tmp_path):
    kg = tmp_path / "kg"
    _stage(kg, "bank", "a", text="x" * 300, observations=[])
    _stage(kg, "bank.policy", "b", text="x" * 300, observations=[])
    _stage(kg, "banking", "c", text="x" * 300, observations=[])

    paths_found = [n["path"] for n in empty_extractions(kg, domains=["bank"])["notes"]]

    assert paths_found == ["notes/a.md", "notes/b.md"]


def test_unreadable_folders_are_skipped_and_a_missing_kg_dir_is_empty(tmp_path):
    kg = tmp_path / "kg"
    folder = _stage(kg, "journal", "broken", text="x" * 300, observations=[])
    (folder / "chunks.json").write_text("{not json", encoding="utf-8")

    assert empty_extractions(kg)["notes"] == []
    assert empty_extractions(tmp_path / "nowhere") == {"notes": [], "scanned": 0}


def test_cli_reads_the_configured_kg_dir(tmp_path, monkeypatch):
    kg = tmp_path / "kg"
    _stage(kg, "journal", "emptied", text="x" * 300, observations=[])
    monkeypatch.setattr(paths, "KG_DIR", kg)

    out = CliRunner().invoke(cli, ["ingest", "empty-extractions", "--domain", "journal",
                                   "--minChars", "100", "--compact"])

    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["notes"][0]["path"] == "notes/emptied.md"
