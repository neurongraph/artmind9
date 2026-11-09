from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli
import json


@pytest.fixture
def runner():
    return CliRunner()


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_pattern6_empty_next_with_resolved_classes(runner):
    found = {"rows": [{"entity": {"entity_class": "PERSON"}, "matched_by": ["exact"]}]}
    with patch("artmind.cli.graph_query.execute_pattern", return_value={"rows": []}), \
         patch("artmind.cli.vector_query.entity_name_candidates", return_value=found) as cand:
        out = _json(runner.invoke(cli, ["query", "graph", "pattern6", "--domain", "bank",
                                        "--entityName1", "Ann", "--entityName2", "Bob", "--compact"]))
    assert out["next"] == (
        'artmind query graph pattern5 --domain bank --entityClass1 PERSON --entityClass2 PERSON '
        '--entityName1 Ann --entityName2 Bob --mode shortest --compact'
    )
    assert cand.call_count == 2


def test_pattern6_empty_unresolved_keeps_placeholders(runner):
    with patch("artmind.cli.graph_query.execute_pattern", return_value={"rows": []}), \
         patch("artmind.cli.vector_query.entity_name_candidates", side_effect=RuntimeError("down")):
        out = _json(runner.invoke(cli, ["query", "graph", "pattern6", "--domain", "bank",
                                        "--entityName1", "Ann", "--entityName2", "Bob", "--compact"]))
    assert "--entityClass1 <CLASS1> --entityClass2 <CLASS2>" in out["next"]


def test_pattern6_non_empty_has_no_next(runner):
    with patch("artmind.cli.graph_query.execute_pattern", return_value={"rows": [{"a": 1}]}):
        out = _json(runner.invoke(cli, ["query", "graph", "pattern6", "--domain", "bank", "--compact"]))
    assert out["next"] is None


def test_pattern1_empty_joins_existing_next(runner):
    with patch("artmind.cli.graph_query.execute_pattern", return_value={"rows": [], "next": "earlier"}):
        out = _json(runner.invoke(cli, ["query", "graph", "pattern1", "--domain", "bank",
                                        "--entityClass", "PERSON", "--compact"]))
    assert out["next"] == (
        'earlier; artmind query graph entity-listing --domain bank --nameFilter "<word>" --compact'
    )


def test_hierarchy_empty_walk(runner):
    payload = {"root": {"entity_class": "ORG_UNIT"}, "rows": [], "summary": {"total": 0}}
    with patch("artmind.cli.graph_query.hierarchy", return_value=payload):
        out = _json(runner.invoke(cli, ["query", "graph", "hierarchy", "--domain", "bank",
                                        "--entityId", "e1", "--childOf", "PART_OF", "--compact", "who?"]))
    assert out["next"] == "artmind query graph pattern1 --domain bank --entityClass ORG_UNIT --compact 'who?'"


def test_resolve_key_empty(runner):
    with patch("artmind.cli.resolve_key.resolve_key", return_value={"candidates": []}):
        out = _json(runner.invoke(cli, ["query", "resolve-key", "--domain", "bank", "--compact", "acme"]))
    assert out["next"] == 'artmind query resolve-key --domain bank --topK 25 --compact acme'
    with patch("artmind.cli.resolve_key.resolve_key", return_value={"candidates": []}):
        out = _json(runner.invoke(cli, ["query", "resolve-key", "--domain", "bank", "--topK", "25",
                                        "--compact", "acme"]))
    assert out["next"] == 'artmind query entity-resolve --domain bank --compact acme'


def test_text2cypher_empty_and_nonempty(runner):
    with patch("artmind.cli.text2cypher.execute_text2cypher", return_value={"rows": [], "rows_total": 0}):
        out = _json(runner.invoke(cli, ["query", "graph", "text2cypher", "--domain", "bank", "--compact", "q?"]))
    assert out["next"] == (
        "artmind query graph text2cypher --domain bank --dryRun --compact 'q?'; "
        "artmind query orient --domain bank --compact"
    )
    with patch("artmind.cli.text2cypher.execute_text2cypher", return_value={"rows": [1], "rows_total": 1}):
        out = _json(runner.invoke(cli, ["query", "graph", "text2cypher", "--domain", "bank", "--compact", "q?"]))
    assert out["next"] is None


def test_text2sql_empty(runner):
    with patch("artmind.cli.text2sql.execute_text2sql", return_value={"rows": []}):
        out = _json(runner.invoke(cli, ["query", "text2sql", "--domain", "bank", "--compact", "q?"]))
    assert out["next"] == (
        "artmind query text2sql --domain bank --dryRun --compact 'q?'; "
        "artmind db schema --domain bank --compact"
    )


def test_text2sql_failure_appends_next(runner):
    with patch("artmind.cli.text2sql.execute_text2sql", side_effect=ValueError("bad sql")):
        result = runner.invoke(cli, ["query", "text2sql", "--domain", "bank", "q?"])
    assert result.exit_code == 1
    assert "bad sql" in result.output
    assert "next: artmind query text2sql --domain bank --dryRun --compact 'q?'" in result.output


def test_text2cypher_dry_run_failure_points_at_orient(runner):
    with patch("artmind.cli.text2cypher.execute_text2cypher", side_effect=ValueError("nope")):
        result = runner.invoke(cli, ["query", "graph", "text2cypher", "--domain", "bank", "--dryRun", "q?"])
    assert result.exit_code == 1
    assert "next: artmind query orient --domain bank --compact" in result.output


def test_pattern6_lookup_not_called_when_rows_present(runner):
    with patch("artmind.cli.graph_query.execute_pattern", return_value={"rows": [{"a": 1}]}), \
         patch("artmind.cli.vector_query.entity_name_candidates") as cand:
        _json(runner.invoke(cli, ["query", "graph", "pattern6", "--domain", "bank",
                                  "--entityName1", "Ann", "--entityName2", "Bob", "--compact"]))
    cand.assert_not_called()


def test_pattern6_down_graph_costs_one_lookup(runner):
    with patch("artmind.cli.graph_query.execute_pattern", return_value={"rows": []}), \
         patch("artmind.cli.vector_query.entity_name_candidates", side_effect=RuntimeError("down")) as cand:
        out = _json(runner.invoke(cli, ["query", "graph", "pattern6", "--domain", "bank",
                                        "--entityName1", "Ann", "--entityName2", "Bob", "--compact"]))
    assert cand.call_count == 1
    assert "<CLASS1>" in out["next"]


def test_multi_domain_repeats_flag(runner):
    with patch("artmind.cli.resolve_key.resolve_key", return_value={"candidates": []}):
        out = _json(runner.invoke(cli, ["query", "resolve-key", "--domain", "bank", "--domain", "kyc",
                                        "--compact", "acme"]))
    assert out["next"] == "artmind query resolve-key --domain bank --domain kyc --topK 25 --compact acme"


def test_nonempty_results_keep_next_null(runner):
    with patch("artmind.cli.graph_query.execute_pattern", return_value={"rows": [1]}):
        out = _json(runner.invoke(cli, ["query", "graph", "pattern1", "--domain", "bank",
                                        "--entityClass", "PERSON", "--compact"]))
    assert out["next"] is None
    payload = {"root": {"entity_class": "X"}, "rows": [1], "summary": {"total": 1}}
    with patch("artmind.cli.graph_query.hierarchy", return_value=payload):
        out = _json(runner.invoke(cli, ["query", "graph", "hierarchy", "--domain", "bank",
                                        "--entityId", "e1", "--childOf", "P", "--compact"]))
    assert out["next"] is None
    with patch("artmind.cli.text2sql.execute_text2sql", return_value={"rows": [{"a": 1}]}):
        out = _json(runner.invoke(cli, ["query", "text2sql", "--domain", "bank", "--compact", "q"]))
    assert out["next"] is None
