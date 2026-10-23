"""H1 (spec 2026-10-22 §5.1): a dry run says it ran nothing and names the command that would."""

import json

from click.testing import CliRunner

from artmind import cli as cli_mod
from artmind.cli import cli


def test_text2cypher_dry_run_names_the_command_to_run(monkeypatch):
    monkeypatch.setattr(
        cli_mod.text2cypher, "execute_text2cypher",
        lambda **kw: {"command": "text2cypher", "generated_cypher": "MATCH (n) RETURN n", "dry_run": True},
    )
    result = CliRunner().invoke(
        cli, ["query", "graph", "text2cypher", "--domain", "fiction", "--dry-run", "--compact", "how many people?"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "rows" not in payload
    assert payload["next"] == "artmind query graph text2cypher --domain fiction 'how many people?' --compact"


def test_text2cypher_without_dry_run_has_no_next(monkeypatch):
    monkeypatch.setattr(
        cli_mod.text2cypher, "execute_text2cypher",
        lambda **kw: {"command": "text2cypher", "rows": [{"n": 1}]},
    )
    result = CliRunner().invoke(cli, ["query", "graph", "text2cypher", "--domain", "fiction", "--compact", "q"])
    assert result.exit_code == 0, result.output
    assert "next" not in json.loads(result.output)


def test_text2sql_dry_run_names_the_command_to_run(monkeypatch):
    monkeypatch.setattr(
        cli_mod.text2sql, "execute_text2sql",
        lambda question, domains, dry_run=False, as_of=None: {"command": "text2sql", "dry_run": True},
    )
    result = CliRunner().invoke(
        cli, ["query", "text2sql", "--domain", "bank", "--dry-run", "--asOf", "2026-01-01", "--compact", "total fees"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["next"] == "artmind query text2sql --domain bank --asOf 2026-01-01 'total fees' --compact"


def test_text2cypher_dry_run_next_keeps_a_non_default_max_rows(monkeypatch):
    monkeypatch.setattr(
        cli_mod.text2cypher, "execute_text2cypher",
        lambda **kw: {"command": "text2cypher", "dry_run": True},
    )
    result = CliRunner().invoke(
        cli,
        ["query", "graph", "text2cypher", "--domain", "a", "--domain", "b", "--dry-run", "--maxRows", "50",
         "--compact", "what's up?"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["next"] == "artmind query graph text2cypher --domain a,b 'what'\"'\"'s up?' --maxRows 50 --compact"


def test_text2sql_dry_run_without_as_of_has_no_as_of_in_next(monkeypatch):
    monkeypatch.setattr(
        cli_mod.text2sql, "execute_text2sql",
        lambda question, domains, dry_run=False, as_of=None: {"command": "text2sql", "dry_run": True},
    )
    result = CliRunner().invoke(cli, ["query", "text2sql", "--domain", "bank", "--dry-run", "--compact", "total fees"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "--asOf" not in payload["next"]
    assert payload["next"] == "artmind query text2sql --domain bank 'total fees' --compact"
