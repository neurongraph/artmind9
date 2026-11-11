"""guide keys on results (spec 2026-11-11 skill progressive disclosure §5.1)."""
import pytest

from artmind import guides


def test_keys_are_the_reference_stems_in_precedence_order():
    assert guides.GUIDES == ("hubs", "resolve", "temporal", "composites", "conflicts", "graph-model", "survey")


@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM j__mart_fact_happening", True),
    ("SELECT * FROM j__visits", False),
    (None, False),
])
def test_reads_mart(sql, expected):
    assert guides.reads_mart(sql) is expected


FIRES = [
    ("hubs", "query entity-context", {"rows": [{"entity": {}, "hub": True, "cut": {"connections": 3}}]}),
    ("resolve", "query entity-resolve", {"rows": [{"entity": {}}], "twins": [[{"_id": "a"}, {"_id": "b"}]]}),
    ("resolve", "query views run", {"status": "needs_disambiguation", "candidates": []}),
    ("resolve", "query entity-resolve", {"status": "no_match", "rows": []}),
    ("temporal", "query vector-text", {"rows": [{"doc": {"id": "d", "replaced": [{"name": "old"}]}}]}),
    ("temporal", "query survey", {"documents": [{"doc_id": "d", "replaced": [{"name": "old"}]}]}),
    ("temporal", "query chunks", {"rows": [{"doc": {"id": "d", "replaced": [{"name": "old"}]}}]}),
    ("composites", "query text2sql", {"rows": [{"n": 3}], "generated_sql": "SELECT count(*) FROM j__mart_x"}),
    ("conflicts", "query graph conflicts", {"rows": [{"id": "c1"}]}),
    ("graph-model", "query graph text2cypher", {"rows": [], "generated_cypher": "MATCH (n) RETURN n"}),
    ("graph-model", "query graph pattern8", {"rows": [{"entity": {}}]}),
    ("survey", "query survey", {"documents": [{"doc_id": "d"}], "coverage": {"matching_documents": 3, "shown": 3}}),
    ("survey", "query topics run", {"rows": [{"doc_id": "d"}]}),
]


@pytest.mark.parametrize("key,command,payload", FIRES)
def test_each_key_fires(key, command, payload):
    assert key in guides.guides_for(command, payload)


QUIET = [
    ("query entity-context", {"rows": [{"entity": {}}]}),
    ("query entity-resolve", {"rows": [{"entity": {}}]}),
    ("query views run", {"status": "ok", "rows": []}),
    ("query vector-text", {"rows": [{"doc": {"id": "d"}}]}),
    ("query text2sql", {"rows": [{"n": 3}], "generated_sql": "SELECT 1 FROM j__visits"}),
    ("query text2sql", {"rows": [], "generated_sql": "SELECT 1 FROM j__mart_x"}),
    ("query text2sql", {"rows": [{"n": 1}], "generated_sql": "SELECT 1 FROM j__mart_x", "dry_run": True}),
    ("query graph conflicts", {"rows": []}),
    ("query graph pattern8", {"rows": []}),
    ("query survey", {"rows": []}),
    ("query orient", {"domains": []}),
    ("ingest sync", {"rows": [{"hub": True}]}),
]


@pytest.mark.parametrize("command,payload", QUIET)
def test_nothing_fires(command, payload):
    assert guides.guides_for(command, payload) == []


def test_at_most_two_keys_in_precedence_order():
    payload = {"rows": [{"entity": {}, "hub": True, "doc": {"replaced": [{"name": "o"}]}}],
               "twins": [[{"_id": "a"}]]}
    # entity-context fires hubs; twins and replaced are only read for their own commands
    assert guides.guides_for("query entity-context", payload) == ["hubs"]
    many = {"documents": [{"doc": {"replaced": [{"name": "o"}]}}], "coverage": {"matching_documents": 1, "shown": 1}}
    assert guides.guides_for("query survey", many) == ["temporal", "survey"]


def test_with_guide_adds_only_when_something_fires_and_keeps_a_set_guide():
    assert "guide" not in guides.with_guide("query survey", {"documents": []})
    assert guides.with_guide("query survey", {"documents": [{"doc_id": "d"}]})["guide"] == ["survey"]
    own = {"documents": [{"doc_id": "d"}], "guide": ["hubs"]}
    assert guides.with_guide("query survey", own)["guide"] == ["hubs"]
    assert guides.with_guide(None, {"documents": [{"doc_id": "d"}]}) == {"documents": [{"doc_id": "d"}]}
    assert guides.with_guide("query survey", ["not", "a", "dict"]) == ["not", "a", "dict"]


import json

import click
from click.testing import CliRunner


def _run_echo(command_name: str, payload: dict) -> dict:
    from artmind.cli import _echo_json

    @click.group()
    def root():
        pass

    @root.group(name="query")
    def query():
        pass

    @query.command(name=command_name)
    def cmd():
        _echo_json(payload, compact=True)

    result = CliRunner().invoke(root, ["query", command_name])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_echo_json_adds_guide_after_the_envelope():
    out = _run_echo("survey", {"documents": [{"doc_id": "d"}]})
    assert list(out)[:3] == ["command", "warnings", "next"]
    assert out["guide"] == ["survey"]
    assert set(out["guide"]) <= set(guides.GUIDES)


def test_echo_json_adds_no_guide_when_nothing_fires():
    assert "guide" not in _run_echo("survey", {"documents": []})


def test_text2sql_mart_hint_uses_the_shared_mart_test(monkeypatch):
    from artmind import hints

    calls = []
    monkeypatch.setattr(guides, "reads_mart", lambda sql: calls.append(sql) or True)
    assert hints.text2sql_mart({"rows": [{"n": 1}], "generated_sql": "x"}, ["d"], "q")
    assert calls == ["x"]


def test_survey_reads_its_documents_list_not_rows():
    """`query survey` prints its list under `documents` (cli_conventions.LIST_KEY); run P2b banking 1 missed it."""
    assert guides.guides_for("query survey", {"rows": [{"doc_id": "d"}]}) == []
    assert guides.guides_for("query survey", {"documents": [{"doc_id": "d"}]}) == ["survey"]
