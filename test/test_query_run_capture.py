"""Capture mode: a step runs as a real Click command and its JSON is caught, not printed (P3 spec §3.3)."""

import pytest

from artmind.query_run import capture


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)


def _overview(monkeypatch, rows=({"domain": "bank"},)):
    from artmind import cli as cli_mod

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": list(rows)})


def test_allowed_query_and_read_only_db_only():
    assert capture.allowed(["query", "vector-text", "--domain", "b", "q"])
    assert capture.allowed(["db", "sql", "select 1"])
    assert capture.allowed(["db", "bridge", "--domain", "b"])
    assert not capture.allowed(["db", "bridge", "--domain", "b", "confirm"])
    assert not capture.allowed(["db", "mappings"])
    assert not capture.allowed(["query", "run", "q"])
    assert not capture.allowed(["query", "plan", "q"])
    assert not capture.allowed(["query", "page", "abc"])
    assert not capture.allowed(["ingest", "sync", "x"])
    assert not capture.allowed(["query", "vector-text", "--help"])
    assert not capture.allowed([])


def test_a_bare_group_is_not_a_step():
    assert not capture.allowed(["query", "graph"])
    assert not capture.allowed(["query", "views"])
    assert capture.allowed(["query", "graph", "pattern7", "--domain", "b"])


def test_a_captured_step_returns_the_payload_and_prints_nothing(monkeypatch, capsys):
    _overview(monkeypatch)
    result = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert result.id == "s1" and result.exit_code == 0 and result.error is None
    assert result.payload["command"] == "query domains-overview"
    assert result.payload["rows"][0]["domain"] == "bank"
    assert result.chars > 0 and result.ms >= 0
    assert capsys.readouterr().out == ""


def test_the_payload_is_whole_whatever_the_output_budget(monkeypatch):
    _overview(monkeypatch, rows=[{"domain": f"d{i}", "note": "x" * 200} for i in range(50)])
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "500")
    result = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert len(result.payload["rows"]) == 50 and "handle" not in result.payload


def test_a_usage_error_is_a_step_error():
    result = capture.invoke_step(["query", "vector-text", "--compact"], "s1")
    assert result.exit_code == 2 and result.payload is None
    assert "QUESTION" in result.error


def test_a_click_exception_is_a_step_error(monkeypatch):
    import click

    from artmind import cli as cli_mod

    def bad(*a, **k):
        raise click.ClickException("bad input here\nsecond line")

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", bad)
    result = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert result.exit_code == 1 and result.error == "bad input here"


def test_a_store_failure_exits_3(monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    from artmind import cli as cli_mod

    def down(*a, **k):
        raise ServiceUnavailable("connection refused")

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", down)
    result = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert result.exit_code == 3


def test_a_command_that_prints_no_json_is_no_json(monkeypatch):
    class Quiet:
        def main(self, **kw):
            return None

    monkeypatch.setattr(capture, "_cli", lambda: Quiet())
    result = capture.invoke_step(["query", "domains-overview"], "s1")
    assert (result.exit_code, result.error) == (1, "no_json")


def test_a_refused_command_never_runs(monkeypatch):
    monkeypatch.setattr(capture, "_cli", lambda: pytest.fail("must not run"))
    result = capture.invoke_step(["ingest", "sync", "x"], "s1")
    assert (result.exit_code, result.error, result.skipped) == (1, "not_allowed", None)


def test_capture_works_inside_an_outer_cli_runner(monkeypatch):
    """The serve path: an outer CliRunner holds stdout; a captured step must not need its own."""
    import click
    from click.testing import CliRunner

    from artmind import cli as cli_mod

    _overview(monkeypatch)

    @click.command("probe-capture")
    def probe():
        r = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
        cli_mod._echo_json({"inner": r.payload["command"], "exit": r.exit_code}, True)

    cli_mod.query.add_command(probe)
    try:
        out = CliRunner().invoke(cli_mod.cli, ["query", "probe-capture"])
    finally:
        cli_mod.query.commands.pop("probe-capture", None)
    assert out.exit_code == 0, out.output
    assert '"inner":"query domains-overview"' in out.stdout and out.stdout.count('"command"') == 1


def test_the_staleness_check_is_skipped_for_a_captured_step(monkeypatch):
    import artmind.vault_sync as vault_sync

    calls = []
    monkeypatch.delenv("ARTMIND_NO_STALENESS_CHECK", raising=False)
    monkeypatch.setattr(vault_sync, "query_staleness_warning", lambda *a, **k: calls.append(1))
    _overview(monkeypatch)
    capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert calls == []
