"""C6 (spec 2026-11-02 §3.5): exit 3 when a store is unreachable; every other exit code unchanged."""
import click
import duckdb
import httpx
import openai
import pytest
from click.testing import CliRunner
from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired

from artmind import cli_conventions as cc
from artmind.cli import cli


@pytest.fixture()
def boom(monkeypatch):
    """A `query boom` command that raises whatever `boom.exc` is, as an uncaught error."""
    holder = {}

    @click.command("boom")
    def _boom():
        raise holder["exc"]

    monkeypatch.setitem(cli.commands["query"].commands, "boom", _boom)

    def arm(exc):
        holder["exc"] = exc
        return CliRunner().invoke(cli, ["query", "boom"])

    return arm


def _openai_connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://user:secret@llm.example.test:8443/v1/embeddings"))


@pytest.mark.parametrize("exc, store", [
    (ServiceUnavailable("Couldn't connect to db.example.test:7687"), "neo4j"),
    (AuthError("The client is unauthorized due to authentication failure."), "neo4j"),
    (SessionExpired("Session expired"), "neo4j"),
    (duckdb.IOException("Cannot open file catalog.duckdb: No such file"), "structured store"),
    (duckdb.ConnectionException("connection closed"), "structured store"),
    (_openai_connection_error(), "LLM or embedding service"),
])
def test_an_uncaught_store_failure_exits_3_with_one_stderr_line(boom, monkeypatch, exc, store):
    monkeypatch.setenv("ARTMIND_KG_NEO4J_URI", "neo4j+s://admin:hunter2@db.example.test:7687/neo4j")

    result = boom(exc)

    assert result.exit_code == cc.EXIT_STORE == 3
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith(f"artmind: {store} unreachable at "), result.stderr
    assert "Traceback" not in result.stderr
    assert "hunter2" not in result.stderr and "secret" not in result.stderr
    assert result.stdout == ""


def test_the_neo4j_address_is_host_and_port_only(boom, monkeypatch):
    monkeypatch.setenv("ARTMIND_KG_NEO4J_URI", "neo4j+s://admin:hunter2@db.example.test:7687/neo4j")

    result = boom(ServiceUnavailable("down"))

    assert "unreachable at db.example.test:7687: down" in result.stderr


def test_the_llm_address_comes_from_the_request_without_credentials(boom):
    result = boom(_openai_connection_error())

    assert "unreachable at llm.example.test:8443:" in result.stderr


def test_an_unrelated_exception_keeps_exit_1_and_its_traceback(boom):
    result = boom(ValueError("bug"))

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert "unreachable" not in result.stderr


def test_a_neo4j_query_error_is_not_a_store_failure(boom):
    from neo4j.exceptions import CypherSyntaxError

    result = boom(CypherSyntaxError("bad cypher"))

    assert result.exit_code == 1 and "unreachable" not in result.stderr


def test_a_duckdb_sql_error_is_not_a_store_failure(boom):
    result = boom(duckdb.BinderException("no such column"))

    assert result.exit_code == 1 and "unreachable" not in result.stderr


def test_click_exceptions_and_usage_errors_keep_their_codes(boom):
    assert boom(click.ClickException("bad input")).exit_code == 1
    assert CliRunner().invoke(cli, ["query", "boom", "--nope"]).exit_code == 2


def test_the_serve_daemon_returns_the_same_code(boom, monkeypatch):
    from artmind.server import run_cli_args

    monkeypatch.setenv("ARTMIND_KG_NEO4J_URI", "neo4j://db.example.test:7687")
    boom.__closure__  # the fixture registered the command; arm it without invoking
    cli.commands["query"].commands["boom"].callback = lambda: (_ for _ in ()).throw(ServiceUnavailable("down"))

    served = run_cli_args(["query", "boom"])

    assert served["exit_code"] == 3
    assert "artmind: neo4j unreachable at db.example.test:7687: down" in served["stderr"]
