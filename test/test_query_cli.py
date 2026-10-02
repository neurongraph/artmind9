import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind import graph_query
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
    query.assert_called_once_with(["fiction"])
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
            cli, ["query", "graph", "entity-listing", "--domain", "fiction"]
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with(["fiction"], name_filter=None, count_all=False)
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
            ["query", "graph", "entity-listing", "--domain", "fiction", "--nameFilter", "holmes"],
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with(["fiction"], name_filter="holmes", count_all=False)
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
            ["query", "graph", "entity-listing", "--domain", "fiction", "--countAll"],
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with(["fiction"], name_filter=None, count_all=True)
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
    assert call_kwargs["domains"] == ["fiction"]
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


def test_graph_hierarchy_cli_discovery_mode(runner):
    payload = {
        "domain": "banking",
        "query_type": "graph",
        "command": "hierarchy",
        "root": {"_id": "e1", "name": "Alex", "entity_class": "ROLE_PERSON"},
        "edge_candidates": [{"rel_type": "REPORTS_TO", "direction": "in", "count": 3, "neighbor_classes": ["ROLE_PERSON"]}],
    }
    with patch("artmind.cli.graph_query.hierarchy", return_value=payload) as query:
        result = runner.invoke(
            cli,
            ["query", "graph", "hierarchy", "--domain", "banking", "--entityId", "e1"],
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with(
        ["banking"],
        entity_id="e1",
        entity_name=None,
        entity_class=None,
        child_of=[],
        parent_of=[],
        direction="down",
        max_depth=graph_query.HIERARCHY_DEFAULT_DEPTH,
        limit=graph_query.HIERARCHY_DEFAULT_LIMIT,
        node_class=[],
        question=None,
    )
    assert json.loads(result.output) == payload


def test_graph_hierarchy_cli_passes_traversal_options(runner):
    payload = {"domain": "banking", "query_type": "graph", "command": "hierarchy", "rows": []}
    with patch("artmind.cli.graph_query.hierarchy", return_value=payload) as query:
        result = runner.invoke(
            cli,
            [
                "query", "graph", "hierarchy",
                "--domain", "banking",
                "--entityName", "Alex Smith",
                "--entityClass", "ROLE_PERSON",
                "--childOf", "REPORTS_TO,MEMBER_OF",
                "--parentOf", "MANAGES",
                "--direction", "up",
                "--maxDepth", "3",
                "--limit", "500",
                "--nodeClass", "ROLE_PERSON",
                "--nodeClass", "ORGANIZATIONAL_UNIT",
                "--compact",
                "who does Alex report to?",
            ],
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with(
        ["banking"],
        entity_id=None,
        entity_name="Alex Smith",
        entity_class="ROLE_PERSON",
        child_of=["REPORTS_TO", "MEMBER_OF"],
        parent_of=["MANAGES"],
        direction="up",
        max_depth=3,
        limit=500,
        node_class=["ROLE_PERSON", "ORGANIZATIONAL_UNIT"],
        question="who does Alex report to?",
    )
    assert result.output.strip() == json.dumps(payload, separators=(",", ":"))


def test_graph_hierarchy_cli_rejects_bad_direction(runner):
    result = runner.invoke(
        cli,
        ["query", "graph", "hierarchy", "--domain", "banking", "--entityId", "e1", "--direction", "sideways"],
    )

    assert result.exit_code != 0
    assert "Invalid value for '--direction'" in result.output


def test_graph_hierarchy_cli_surfaces_validation_errors(runner):
    with patch(
        "artmind.cli.graph_query.hierarchy",
        side_effect=ValueError("--entityId or --entityName is required"),
    ):
        result = runner.invoke(
            cli,
            ["query", "graph", "hierarchy", "--domain", "banking"],
        )

    assert result.exit_code != 0
    assert "--entityId or --entityName is required" in result.output


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


def test_vector_text_cli_dispatches_and_outputs_json(runner):
    payload = {
        "domain": "fiction",
        "query_type": "vector_text",
        "question": "Where did Holmes go?",
        "parameters": {"topK": 3},
        "rows": [],
    }
    with patch("artmind.cli.vector_query.vector_text_search", return_value=payload) as query:
        result = runner.invoke(
            cli,
            [
                "query",
                "vector-text",
                "--domain",
                "fiction",
                "--topK",
                "3",
                "Where did Holmes go?",
            ],
        )

    assert result.exit_code == 0, result.output
    query.assert_called_once_with(["fiction"], "Where did Holmes go?", 3, as_of=None)
    assert json.loads(result.output) == payload
