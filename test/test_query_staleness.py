"""The staleness line on `query` commands (spec 2026-09-26 §6 A4): stderr
only, after a successful query, through both the in-process CLI and the
`serve` daemon -- and never able to break the query it rides along with."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli

MESSAGE = "artmind: graph is 2 docs / 1 tables behind the vault — run `artmind vault sync`"
OVERVIEW = {"domains": [{"name": "banking"}]}


@pytest.fixture()
def in_vault(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", tmp_path)
    monkeypatch.delenv("ARTMIND_NO_STALENESS_CHECK", raising=False)
    return tmp_path


def _warning(monkeypatch, fn):
    import artmind.vault_sync as vs

    seen = []

    def _wrapped(vault_dir):
        seen.append(vault_dir)
        return fn(vault_dir)

    monkeypatch.setattr(vs, "query_staleness_warning", _wrapped)
    return seen


def test_the_warning_goes_to_stderr_and_compact_json_stays_clean(in_vault, monkeypatch):
    seen = _warning(monkeypatch, lambda v: MESSAGE)

    with patch("artmind.cli.graph_query.domains_overview", return_value=OVERVIEW):
        result = CliRunner().invoke(cli, ["query", "domains-overview", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == OVERVIEW
    assert result.stderr.strip() == MESSAGE
    assert seen == [in_vault]


def test_nested_graph_commands_warn_too(in_vault, monkeypatch):
    _warning(monkeypatch, lambda v: MESSAGE)

    with patch("artmind.cli.graph_query.graph_metadata", return_value={"labels": []}):
        result = CliRunner().invoke(cli, ["query", "graph", "metadata", "--domain", "banking", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"labels": []}
    assert MESSAGE in result.stderr


def test_a_failing_check_is_swallowed(in_vault, monkeypatch):
    def _boom(vault_dir):
        raise RuntimeError("neo4j unreachable")

    _warning(monkeypatch, _boom)

    with patch("artmind.cli.graph_query.domains_overview", return_value=OVERVIEW):
        result = CliRunner().invoke(cli, ["query", "domains-overview", "--compact"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == OVERVIEW
    assert result.stderr == ""


def test_a_failed_query_is_not_followed_by_a_warning(in_vault, monkeypatch):
    seen = _warning(monkeypatch, lambda v: MESSAGE)

    with patch("artmind.cli.graph_query.domains_overview", side_effect=RuntimeError("down")):
        result = CliRunner().invoke(cli, ["query", "domains-overview"])

    assert result.exit_code != 0
    assert seen == []


def test_help_never_runs_the_check(in_vault, monkeypatch):
    seen = _warning(monkeypatch, lambda v: MESSAGE)

    result = CliRunner().invoke(cli, ["query", "domains-overview", "--help"])

    assert result.exit_code == 0
    assert seen == []


def test_the_env_switch_turns_it_off(in_vault, monkeypatch):
    seen = _warning(monkeypatch, lambda v: MESSAGE)
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")

    with patch("artmind.cli.graph_query.domains_overview", return_value=OVERVIEW):
        result = CliRunner().invoke(cli, ["query", "domains-overview", "--compact"])

    assert result.stderr == ""
    assert seen == []


def test_the_serve_daemon_returns_the_warning_in_its_stderr_field(in_vault, monkeypatch):
    """`_entry._proxy` writes `body["stderr"]` to the real stderr, so a
    proxied query shows the same line an in-process one does."""
    from artmind.server import run_cli_args

    _warning(monkeypatch, lambda v: MESSAGE)

    with patch("artmind.cli.graph_query.domains_overview", return_value=OVERVIEW):
        body = run_cli_args(["query", "domains-overview", "--compact"])

    assert body["exit_code"] == 0
    assert json.loads(body["output"]) == OVERVIEW
    assert body["stderr"].strip() == MESSAGE


def test_the_proxy_prints_the_daemons_stderr(monkeypatch, capsys):
    import artmind._entry as entry

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"output": '{"a":1}\n', "stderr": MESSAGE + "\n", "exit_code": 0}).encode()

    monkeypatch.setattr(entry.urllib.request, "urlopen", lambda *a, **k: _Resp())

    assert entry._proxy("http://127.0.0.1:1", ["query", "domains-overview", "--compact"]) == 0
    out, err = capsys.readouterr()
    assert out == '{"a":1}\n'
    assert err.strip() == MESSAGE
