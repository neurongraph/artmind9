"""Exact-id list options split on commas, like every other list option
(CLAUDE.md conventions): `--idList a,b` is two ids, not one id with a comma.
Name lists are left alone -- a name may contain a comma."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli

ROWS = {"rows": []}


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.mark.parametrize("args,expected", [
    (["--idList", "c-1,c-2, c-3"], ["c-1", "c-2", "c-3"]),
    (["--idList", "c-1", "--idList", "c-2"], ["c-1", "c-2"]),
    (["--idList", "c-1,c-2", "--idList", "c-3", "--idList", "c-2"], ["c-1", "c-2", "c-3"]),
    (["--idList", "c-1,,"], ["c-1"]),
])
def test_chunks_id_list_splits_commas(runner, args, expected):
    with patch("artmind.cli.graph_query.chunks_by_id", return_value=ROWS) as call:
        result = runner.invoke(cli, ["query", "chunks", "--domain", "d", *args])
    assert result.exit_code == 0, result.output
    domains, ids = call.call_args.args
    assert domains == ["d"]
    assert ids == expected


@pytest.mark.parametrize("pattern", ["pattern2", "pattern3"])
def test_pattern_entity_id_list_splits_commas(runner, pattern):
    with patch("artmind.cli.graph_query.execute_pattern", return_value=ROWS) as call:
        result = runner.invoke(cli, ["query", "graph", pattern, "--domain", "d",
                                     "--entityIdList", "e-1,e-2", "--entityIdList", "e-3"])
    assert result.exit_code == 0, result.output
    assert list(call.call_args.kwargs["entityIdList"]) == ["e-1", "e-2", "e-3"]


@pytest.mark.parametrize("pattern", ["pattern2", "pattern3"])
def test_pattern_entity_name_list_is_not_split(runner, pattern):
    with patch("artmind.cli.graph_query.execute_pattern", return_value=ROWS) as call:
        result = runner.invoke(cli, ["query", "graph", pattern, "--domain", "d",
                                     "--entityNameList", "Doe, Jane"])
    assert result.exit_code == 0, result.output
    kwargs = call.call_args.kwargs
    assert list(kwargs["entityNameList"]) == ["Doe, Jane"]
    assert not kwargs["entityIdList"]  # no ids given: names still select


def test_conflicts_entity_id_splits_commas(runner):
    with patch("artmind.cli.graph_query.list_conflicts", return_value=ROWS) as call:
        result = runner.invoke(cli, ["query", "graph", "conflicts", "--domain", "d",
                                     "--entityId", "e-1,e-2", "--entityId", "e-3"])
    assert result.exit_code == 0, result.output
    assert call.call_args.kwargs["entity_ids"] == ["e-1", "e-2", "e-3"]
