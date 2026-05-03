import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli


@pytest.fixture()
def runner():
    return CliRunner()


def test_graph_metadata_cli_outputs_json(runner):
    payload = {
        "domain": "fiction",
        "query_type": "graph",
        "command": "metadata",
        "rows": [{"category": "nodes", "name": "CHARACTER"}],
    }
    with patch("artmind.cli.graph_query.graph_metadata", return_value=payload) as query:
        result = runner.invoke(
            cli, ["query", "graph", "metadata", "--domain", "fiction"]
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with("fiction")
    assert json.loads(result.output) == payload


def test_graph_entity_listing_cli_outputs_json(runner):
    payload = {
        "domain": "fiction",
        "query_type": "graph",
        "command": "entity_listing",
        "rows": [{"label": "CHARACTER", "typeGroups": []}],
    }
    with patch("artmind.cli.graph_query.entity_listing", return_value=payload) as query:
        result = runner.invoke(
            cli, ["query", "graph", "entity_listing", "--domain", "fiction"]
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with("fiction", name_filter=None, count_all=False)
    assert json.loads(result.output) == payload


def test_graph_entity_listing_cli_passes_name_filter(runner):
    payload = {
        "domain": "fiction",
        "query_type": "graph",
        "command": "entity_listing",
        "name_filter": "holmes",
        "rows": [],
    }
    with patch("artmind.cli.graph_query.entity_listing", return_value=payload) as query:
        result = runner.invoke(
            cli,
            ["query", "graph", "entity_listing", "--domain", "fiction", "--nameFilter", "holmes"],
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with("fiction", name_filter="holmes", count_all=False)
    assert json.loads(result.output) == payload


def test_graph_entity_listing_cli_passes_count_all(runner):
    payload = {
        "domain": "fiction",
        "query_type": "graph",
        "command": "entity_listing",
        "total_entities": 42,
        "rows": [],
    }
    with patch("artmind.cli.graph_query.entity_listing", return_value=payload) as query:
        result = runner.invoke(
            cli,
            ["query", "graph", "entity_listing", "--domain", "fiction", "--countAll"],
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with("fiction", name_filter=None, count_all=True)
    assert json.loads(result.output)["total_entities"] == 42


@pytest.mark.parametrize(
    ("pattern", "args", "expected"),
    [
        ("pattern1", ["--entityClass", "Character"], {"entityClass": "Character"}),
        (
            "pattern2",
            ["--entityNameList", "Holmes", "--entityNameList", "Watson"],
            {"entityNameList": ("Holmes", "Watson")},
        ),
        (
            "pattern3",
            ["--entityNameList", "Holmes", "--entityNameList", "Watson"],
            {"entityNameList": ("Holmes", "Watson")},
        ),
        (
            "pattern4",
            ["--entityClass", "Character", "--entityName", "Holmes"],
            {"entityClass": "Character", "entityName": "Holmes"},
        ),
        (
            "pattern5",
            [
                "--entityClass1", "Character",
                "--entityClass2", "Location",
                "--entityName1", "Holmes",
                "--entityName2", "London",
                "--mode", "all",
            ],
            {"entityClass1": "Character", "entityClass2": "Location", "mode": "all"},
        ),
        (
            "pattern6",
            ["--entityName1", "Holmes", "--entityName2", "Watson"],
            {"entityName1": "Holmes", "entityName2": "Watson"},
        ),
        ("pattern7", ["--searchTerm", "hair"], {"searchTerm": "hair", "limit": 10}),
        (
            "pattern8",
            ["--entityClass", "Object", "--entityName", "Holmes"],
            {"entityClass": "Object", "entityName": "Holmes"},
        ),
        ("pattern9", ["--entityClass", "Character"], {"entityClass": "Character", "topN": 5}),
    ],
)
def test_graph_pattern_cli_dispatches_every_pattern(runner, pattern, args, expected):
    payload = {
        "domain": "fiction",
        "query_type": "graph",
        "command": "pattern",
        "pattern": pattern,
        "question": "Question?",
        "parameters": {},
        "rows": [],
    }
    with patch("artmind.cli.graph_query.execute_pattern", return_value=payload) as query:
        result = runner.invoke(
            cli,
            ["query", "graph", pattern, "--domain", "fiction", *args, "Question?"],
        )

    assert result.exit_code == 0, result.output
    call_kwargs = query.call_args.kwargs
    assert call_kwargs["domain"] == "fiction"
    assert call_kwargs["pattern"] == pattern
    assert call_kwargs["question"] == "Question?"
    for key, value in expected.items():
        assert call_kwargs[key] == value
    assert json.loads(result.output) == payload


def test_graph_pattern_cli_surfaces_validation_errors(runner):
    with patch(
        "artmind.cli.graph_query.execute_pattern",
        side_effect=ValueError("Missing required option(s) for pattern1: --entityClass"),
    ):
        result = runner.invoke(
            cli,
            ["query", "graph", "pattern1", "--domain", "fiction", "--entityClass", "CHARACTER"],
        )

    assert result.exit_code != 0
    assert "--entityClass" in result.output


@pytest.mark.parametrize(
    ("pattern", "expected_missing"),
    [
        ("pattern1", "--entityClass"),
        ("pattern2", "--entityNameList"),
        ("pattern3", "--entityNameList"),
        ("pattern4", "--entityClass"),
        ("pattern5", "--entityClass1"),
        ("pattern6", "--entityName1"),
        ("pattern7", "--searchTerm"),
        ("pattern8", "--entityClass"),
        ("pattern9", "--entityClass"),
    ],
)
def test_graph_pattern_cli_validates_missing_params_before_query(
    runner, pattern, expected_missing
):
    with patch("artmind.graph_query._run_read_query") as run_query:
        result = runner.invoke(
            cli,
            ["query", "graph", pattern, "--domain", "fiction"],
        )

    assert result.exit_code != 0
    assert expected_missing in result.output
    run_query.assert_not_called()


def test_graph_pattern_cli_rejects_invalid_mode_before_dispatch(runner):
    result = runner.invoke(
        cli,
        [
            "query", "graph", "pattern5",
            "--domain", "fiction",
            "--entityClass1", "CHARACTER",
            "--entityClass2", "CHARACTER",
            "--entityName1", "Holmes",
            "--entityName2", "Watson",
            "--mode", "sideways",
        ],
    )

    assert result.exit_code != 0
    assert "Invalid value for '--mode'" in result.output


def test_compact_json_output(runner):
    payload = {
        "domain": "fiction",
        "query_type": "graph",
        "command": "metadata",
        "rows": [],
    }
    with patch("artmind.cli.graph_query.graph_metadata", return_value=payload):
        result = runner.invoke(
            cli,
            ["query", "graph", "metadata", "--domain", "fiction", "--compact"],
        )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == json.dumps(payload, separators=(",", ":"))


def test_vector_cli_dispatches_and_outputs_json(runner):
    payload = {
        "domain": "fiction",
        "query_type": "vector",
        "question": "Where did Holmes go?",
        "parameters": {"topK": 3},
        "rows": [],
    }
    with patch("artmind.cli.vector_query.vector_search", return_value=payload) as query:
        result = runner.invoke(
            cli,
            [
                "query",
                "vector",
                "--domain",
                "fiction",
                "--topK",
                "3",
                "Where did Holmes go?",
            ],
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with("fiction", "Where did Holmes go?", 3)
    assert json.loads(result.output) == payload
