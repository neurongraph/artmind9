"""`ingest unsupersede --dryRun`: resolves both documents and plans the restore from reads alone."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher
from artmind.cli import cli

KEY = "acme ltd|ORGANISATION|demo.one"


def _respond(cypher, params):
    cy = " ".join(cypher.split())
    if "d.name IN [$n,$o]" in cy:
        return [{"name": "policy v2", "id": "new-1"}, {"name": "policy v1", "id": "old-1"}]
    if "RETURN count(s) AS n" in cy and "NOT (a.id" in cy:
        return [{"n": 0}]
    if "RETURN count(s) AS n" in cy:
        return [{"n": 1}]
    if "d:DocumentHistory AS retired" in cy:
        return [{"id": "new-1", "name": "policy v2", "retired": False}, {"id": "old-1", "name": "policy v1", "retired": True}]
    if "RETURN DISTINCT o.key AS key" in cy:
        return [{"key": KEY}]
    if "count(o) AS n" in cy:
        return [{"n": 5}]
    if "RETURN d.name AS name LIMIT 1" in cy:
        return [{"name": "policy v1"}]
    return []


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path / "_Topics")
    (tmp_path / "_Topics").mkdir()
    recorder = WritableRecorder(_respond)
    with patch("artmind.graph_query.read_session", return_value=recorder), \
         patch("artmind.graph_query.neo4j_session", return_value=recorder), \
         patch("artmind.temporal.neo4j_session", return_value=recorder):
        yield recorder, tmp_path / "curation"


def _run(*args):
    result = CliRunner().invoke(cli, ["ingest", "unsupersede", "--domain", "demo.one", "--newer", "policy v2",
                                      "--older", "policy v1", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_sends_no_write_cypher_and_keeps_the_record(world):
    recorder, curation = world
    from artmind import curation_records, supersession_records

    rid = supersession_records.record_id("new-1", "old-1", "document")
    curation_records.write_record(supersession_records.NAME, {"id": rid})

    payload = _run("--dryRun")

    assert_no_write_cypher(recorder)
    assert (curation / "supersessions" / f"{rid}.json").exists()
    assert payload["dry_run"] is True and payload["newer"] == "new-1" and payload["older"] == "old-1"
    effect = payload["effect"]
    assert_effect_shape(effect)
    assert [c["kind"] for c in effect["changes"]] == [
        "document_unsupersede", "document_restore", "observation_move", "entity_change"]
    assert effect["changes"][0]["count"] == 1 and effect["changes"][2]["count"] == 5
    assert len(effect["records"]) == 2 and effect["travels"] is True


def test_dry_run_reports_nothing_to_remove_when_no_edge(world):
    recorder, _ = world
    base = recorder._respond
    recorder._respond = lambda cy, p: [{"n": 0}] if "RETURN count(s) AS n" in " ".join(cy.split()) else base(cy, p)

    payload = _run("--dryRun")

    assert payload["effect"]["changes"][0]["count"] == 0
    assert len(payload["effect"]["changes"]) == 1


def test_dry_run_refuses_an_unknown_document(world):
    recorder, _ = world
    recorder._respond = lambda cypher, params: []

    result = CliRunner().invoke(cli, ["ingest", "unsupersede", "--domain", "demo.one", "--newer", "a",
                                      "--older", "b", "--dryRun"])

    assert result.exit_code == 1 and "Could not resolve" in result.output
