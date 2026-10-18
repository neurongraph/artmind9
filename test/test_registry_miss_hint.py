"""A note staged here but registered on another machine: `extract-kg` /
`write-to-graph` say so and name the command that works (spec
2026-10-18-extraction-loss-guard-design.md §3.4). Synthetic folders only."""
import json

from click.testing import CliRunner

import artmind.cli as cli_module
import paths
from artmind.cli import cli


def _flat(output: str) -> str:
    """The error renders in a width-limited rich box: drop the frame and the
    line wrapping so a phrase is found wherever it broke."""
    return " ".join(output.replace("│", " ").split())


def _miss(monkeypatch):
    monkeypatch.setattr(cli_module, "_build_file_result_from_db", lambda name, domain: None)


def test_extract_kg_names_the_reextract_command_for_a_staged_note(tmp_path, monkeypatch):
    _miss(monkeypatch)
    folder = tmp_path / "kg" / "journal" / "day"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"path": "notes/day.md"}), encoding="utf-8")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")

    out = CliRunner().invoke(cli, ["ingest", "extract-kg", "day.md", "--domain", "journal"])

    assert out.exit_code != 0
    flat = _flat(out.output)
    assert "not in this machine's registry" in flat
    assert 'artmind ingest sync "notes/day.md" --reextract' in flat


def test_write_to_graph_names_it_too(tmp_path, monkeypatch):
    _miss(monkeypatch)
    folder = tmp_path / "kg" / "journal" / "day"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"path": "notes/day.md"}), encoding="utf-8")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")

    out = CliRunner().invoke(cli, ["ingest", "write-to-graph", "day.md", "--domain", "journal"])

    assert out.exit_code != 0
    assert "--reextract" in _flat(out.output)


def test_an_unstaged_note_gets_the_plain_message(tmp_path, monkeypatch):
    _miss(monkeypatch)
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")

    out = CliRunner().invoke(cli, ["ingest", "extract-kg", "day.md", "--domain", "journal"])

    assert out.exit_code != 0
    assert "not found in registry for domain 'journal'" in _flat(out.output)
    assert "--reextract" not in out.output
