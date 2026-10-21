"""An unknown --domain is flagged, never failed (query-routing study P9).

The guard's sources are the packaged schemas the conftest copies into the test
run folder (`fiction`, `banking.*`, ...), the structured registry, and the graph;
the graph and the registry are stubbed per test so each assertion says which
source made a name known.
"""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind import domain_guard
from artmind.cli import cli

pytestmark = pytest.mark.real_domain_guard


@pytest.fixture(autouse=True)
def _no_tables_no_graph(monkeypatch):
    monkeypatch.setattr(domain_guard, "_table_domains", lambda: set())
    monkeypatch.setattr(domain_guard, "_graph_domains", lambda: set())


def _listing(domain: str):
    """Run `query graph entity-listing` for `domain` over an empty graph result."""
    payload = {"domains": [domain], "domain": domain, "query_type": "graph",
               "command": "entity_listing", "rows": []}
    with patch("artmind.cli.graph_query.entity_listing", return_value=payload):
        return CliRunner().invoke(cli, ["query", "graph", "entity-listing", "--domain", domain])


# ── the guard itself ──────────────────────────────────────────────────────────

def test_a_name_nothing_explains_is_flagged_with_the_real_domains():
    (warning,) = domain_guard.unknown_domain_warnings(["banking_corpus"])
    assert "unknown domain 'banking_corpus'" in warning
    assert "banking.policy" in warning and "domains-overview" in warning


def test_a_typo_gets_a_did_you_mean():
    (warning,) = domain_guard.unknown_domain_warnings(["banking.polcy"])
    assert "Did you mean 'banking.policy'" in warning


def test_schema_domains_and_their_roots_are_known():
    assert domain_guard.unknown_domain_warnings(["banking.policy", "banking", "fiction"]) == []


def test_a_root_is_known_when_any_domain_below_it_is():
    with patch.object(domain_guard, "_graph_domains", lambda: {"acme.sales.emea"}):
        assert domain_guard.unknown_domain_warnings(["acme", "acme.sales", "acme.sales.emea"]) == []
        assert domain_guard.unknown_domain_warnings(["acme.sale"])  # a prefix of a label is not a domain


def test_a_structured_table_domain_is_known(monkeypatch):
    monkeypatch.setattr(domain_guard, "_table_domains", lambda: {"ops"})
    assert domain_guard.unknown_domain_warnings(["ops"]) == []


def test_graph_data_without_a_schema_is_known(monkeypatch):
    """A query-only host has no schema files: its real domains must not be called unknown."""
    monkeypatch.setattr(domain_guard, "_schema_domains", lambda: set())
    monkeypatch.setattr(domain_guard, "_graph_domains", lambda: {"journal"})
    assert domain_guard.unknown_domain_warnings(["journal"]) == []
    assert domain_guard.unknown_domain_warnings(["jurnal"])


def test_the_graph_is_asked_only_when_the_local_sources_leave_a_name_unexplained(monkeypatch):
    asked = []
    monkeypatch.setattr(domain_guard, "_graph_domains", lambda: asked.append(1) or set())
    assert domain_guard.unknown_domain_warnings(["fiction"]) == []
    assert asked == [], "a schema-known domain must not cost a graph query"
    domain_guard.unknown_domain_warnings(["nothing_here"])
    assert asked == [1]


def test_when_the_graph_cannot_be_asked_there_is_no_warning(monkeypatch):
    def down():
        raise ConnectionError("neo4j is down")

    monkeypatch.setattr(domain_guard, "_graph_domains", down)
    assert domain_guard.unknown_domain_warnings(["nothing_here"]) == []


def test_only_the_unknown_names_are_flagged():
    warnings = domain_guard.unknown_domain_warnings(["fiction", "nothing_here"])
    assert len(warnings) == 1 and "'nothing_here'" in warnings[0]


# ── through the CLI ───────────────────────────────────────────────────────────

def test_an_unknown_domain_adds_warnings_to_the_json_and_a_stderr_line():
    result = _listing("banking_corpus")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["rows"] == [], "the result itself is untouched"
    assert len(payload["warnings"]) == 1 and "unknown domain 'banking_corpus'" in payload["warnings"][0]
    assert "artmind: unknown domain 'banking_corpus'" in result.stderr


def test_a_known_domain_adds_nothing():
    result = _listing("fiction")

    assert result.exit_code == 0, result.output
    assert "warnings" not in json.loads(result.stdout)
    assert "unknown domain" not in result.stderr


def test_existing_warnings_are_kept_and_extended():
    payload = {"domains": ["x_unknown"], "rows": [], "warnings": ["truncated"]}
    with patch("artmind.cli.graph_query.entity_listing", return_value=payload):
        result = CliRunner().invoke(cli, ["query", "graph", "entity-listing", "--domain", "x_unknown"])

    warnings = json.loads(result.stdout)["warnings"]
    assert warnings[0] == "truncated" and "unknown domain 'x_unknown'" in warnings[1]


def test_a_warning_does_not_leak_into_the_next_invocation():
    """`serve` runs many commands in one process; each is a fresh click context."""
    _listing("banking_corpus")
    again = _listing("fiction")
    assert "warnings" not in json.loads(again.stdout)


def test_a_failing_guard_never_breaks_the_command(monkeypatch):
    def boom(domains):
        raise RuntimeError("guard bug")

    monkeypatch.setattr(domain_guard, "_schema_domains", boom)
    result = _listing("banking_corpus")
    assert result.exit_code == 0, result.output
    assert "warnings" not in json.loads(result.stdout)
