import json
from unittest.mock import patch

from click.testing import CliRunner

from artmind.cli import cli


def _row(**kw):
    row = {"entityData": {"_id": "e1", "name": "X"}, "connections_total": 3, "connections": [1, 2, 3],
           "chunks_total": 2, "chunks": [{"id": "a"}, {"id": "b"}], "source_documents_total": 1}
    row.update(kw)
    return row


def _invoke(row, *extra):
    with patch("artmind.cli.graph_query.entity_context", return_value={"rows": [row]}) as m:
        res = CliRunner().invoke(cli, ["query", "entity-context", "--domain", "bank",
                                       "--entityId", "e1", "--compact", *extra])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout), m


def test_positional_question_becomes_query():
    _, m = _invoke(_row(), "what about gifts")
    assert m.call_args.kwargs["query"] == "what about gifts"


def test_query_option_wins_with_warning():
    out, m = _invoke(_row(), "--query", "A", "B")
    assert m.call_args.kwargs["query"] == "A"
    assert any("--query" in w for w in out["warnings"])


def test_no_question_no_query():
    _, m = _invoke(_row())
    assert m.call_args.kwargs["query"] is None


def test_hub_cut_block():
    out, _ = _invoke(_row(connections_total=40, connections=list(range(25)),
                          chunks_total=9, truncated_properties={"tags": 30}))
    row = out["rows"][0]
    assert row["hub"] is True
    assert row["cut"] == {"connections": {"shown": 25, "total": 40},
                          "chunks": {"shown": 2, "total": 9}, "properties": ["tags"]}


def test_nothing_cut_has_no_cut_or_hub():
    row = _invoke(_row())[0]["rows"][0]
    assert "cut" not in row and "hub" not in row
