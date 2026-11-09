from click.testing import CliRunner

from artmind.cli import cli


def _flat(res):
    return " ".join(res.output.replace("│", " ").split())


def test_misplaced_top_level_group_is_named():
    res = CliRunner().invoke(cli, ["query", "db", "schema", "--domain", "x"])
    assert res.exit_code == 2
    assert "artmind db schema --domain x" in _flat(res)


def test_misplaced_ingest_group():
    res = CliRunner().invoke(cli, ["query", "ingest", "status"])
    assert res.exit_code == 2
    assert "artmind ingest status" in _flat(res)


def test_plain_unknown_command_unchanged():
    res = CliRunner().invoke(cli, ["query", "nosuch"])
    assert res.exit_code == 2
    assert "No such command 'nosuch'" in _flat(res)
    assert "top-level group" not in _flat(res)
