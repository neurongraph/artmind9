"""`artmind query catalogue`: per-part data, source and generation; read-only."""
import json

import pytest
from click.testing import CliRunner

from artmind import graph_query
from artmind.catalogue import cache, parts
from artmind.catalogue.model import NameEntry
from artmind.cli import cli


@pytest.fixture
def cat(monkeypatch, tmp_path):
    import paths

    calls = []

    def read(cypher, params):
        calls.append((cypher, params))
        return [{"domain": d, "generation": 7, "entities": 2, "documents": 1, "history": 0} for d in params["domains"]]

    monkeypatch.setattr(graph_query, "_run_read_query", read)
    monkeypatch.setattr(parts, "BUILDERS", {
        "classes": lambda d: {"classes": (("PERSON", 2, "a person"),)},
        "relations": lambda d: {"relations": (("KNOWS", 1),)},
        "names": lambda d: {"names": (NameEntry("e1", "Ada", ("A",), "PERSON", 3, d),), "truncated": False},
    })
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setenv("ARTMIND_PLAN_CATALOG", "1")
    cache.reset()
    yield calls
    cache.reset()


def _run(*args):
    result = CliRunner().invoke(cli, ["query", "catalogue", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_payload_shape_and_generation_per_part(cat):
    out = _run("--domain", "journal")
    d = out["domains"][0]
    assert d["domain"] == "journal" and d["generation"] == 7
    assert list(d["parts"]) == ["classes", "relations", "names"]
    classes = d["parts"]["classes"]
    assert classes["source"] == "live" and classes["generation"] == 7
    assert classes["data"] == [["PERSON", 2, "a person"]]
    assert set(classes) == {"source", "generation", "built_at", "data"}


def test_names_are_a_count_unless_asked_for(cat):
    assert _run("--domain", "journal", "--part", "names")["domains"][0]["parts"]["names"]["data"] == {
        "count": 1, "truncated": False}
    listed = _run("--domain", "journal", "--part", "names", "--names")["domains"][0]["parts"]["names"]["data"]
    assert listed["names"][0]["name"] == "Ada" and listed["truncated"] is False


def test_a_second_call_is_served_from_memory(cat):
    _run("--domain", "journal")
    out = _run("--domain", "journal")
    assert {p["source"] for p in out["domains"][0]["parts"].values()} == {"memory"}


def test_health_is_absent_until_something_builds_it(cat, monkeypatch):
    monkeypatch.setitem(parts.BUILDERS, "health", lambda d: {"health": {}})
    out = _run("--domain", "journal", "--part", "health")
    h = out["domains"][0]["parts"]["health"]
    assert h == {"source": "absent", "generation": None, "built_at": None, "data": None}


def test_the_command_only_reads(cat):
    _run("--domain", "journal")
    assert cat and all("DomainState" in c and "MERGE" not in c and "SET " not in c for c, _ in cat)


def test_an_unknown_part_is_a_click_error(cat):
    result = CliRunner().invoke(cli, ["query", "catalogue", "--domain", "journal", "--part", "nope"])
    assert result.exit_code != 0 and "unknown catalogue part" in result.output


def test_the_command_is_routed_in_the_help_groups():
    import rich_click

    groups = rich_click.rich_click.COMMAND_GROUPS
    assert "catalogue" in groups["artmind"][3]["commands"]
    assert "catalogue" in [c for g in groups["artmind query"] for c in g["commands"]]
    assert groups["artmind catalogue"] == [{"name": "Cache", "commands": ["refresh"]}]
