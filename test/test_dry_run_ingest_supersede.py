"""`ingest supersede --dryRun`: resolves both documents and plans the retirement from reads alone."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher, write_calls
from artmind.cli import cli

KEY = "acme ltd|ORGANISATION|demo.one"


def _respond(cypher, params):
    cy = " ".join(cypher.split())
    if "WHERE d._domain=$domain AND d.name IN" in cy:
        return [{"name": "policy v2", "id": "new-1"}, {"name": "policy v1", "id": "old-1"}]
    if "SUPERSEDES {record_id: $id}]->() RETURN count(s)" in cy:
        return [{"n": 0}]
    if "d.id IN $ids RETURN d.id AS id, d.name AS name" in cy:
        return [{"id": "new-1", "name": "policy v2"}, {"id": "old-1", "name": "policy v1"}]
    if "RETURN DISTINCT o.key AS key" in cy:
        return [{"key": KEY}]
    if "count(o) AS n" in cy:
        return [{"n": 5}]
    if "RETURN d.name AS name LIMIT 1" in cy:
        return [{"name": "policy v1"}]
    if "RETURN count(s) AS n" in cy:
        return [{"n": 1}]
    return []


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path / "_Topics")
    (tmp_path / "_Topics").mkdir()
    (tmp_path / "_Topics" / "acme.md").write_text(
        "---\nartmind_topic: Acme\nanchor:\n  kind: entity\n  key: " + KEY + "\nsources: []\n---\nBody\n\n## My notes\n"
    )
    recorder = WritableRecorder(_respond)
    with patch("artmind.graph_query.read_session", return_value=recorder), \
         patch("artmind.graph_query.neo4j_session", return_value=recorder), \
         patch("artmind.temporal.neo4j_session", return_value=recorder), \
         patch("artmind.lifecycle.neo4j_session", return_value=recorder), \
         patch("artmind.projection.rebuild", return_value={"rebuilt": 1}):
        yield recorder, tmp_path / "curation"


def _run(*args):
    result = CliRunner().invoke(cli, ["ingest", "supersede", "--domain", "demo.one", "--newer", "policy v2",
                                      "--older", "policy v1", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_sends_no_write_cypher_and_writes_no_record(world):
    recorder, curation = world

    payload = _run("--dryRun")

    assert_no_write_cypher(recorder)
    assert not curation.exists()
    assert payload["dry_run"] is True and payload["newer"] == "new-1" and payload["older"] == "old-1"
    effect = payload["effect"]
    assert_effect_shape(effect)
    assert [c["kind"] for c in effect["changes"]] == ["document_supersede", "observation_move", "entity_change"]
    assert effect["changes"][0]["sample"][0]["older"] == "policy v1"
    assert effect["changes"][1]["count"] == 5 and effect["changes"][2]["count"] == 1
    assert effect["stale"]["topics"] == ["acme"]
    assert len(effect["records"]) == 2 and effect["travels"] is True
    # the retirement plan read the older document, not the newer one
    assert any(p.get("doc_id") == "old-1" for _, p in recorder.calls)


def test_real_run_has_the_same_effect_and_writes_both_records(world):
    recorder, curation = world

    dry = _run("--dryRun")
    real = _run()

    assert "dry_run" not in real and real["effect"] == dry["effect"]
    assert len(list((curation / "supersessions").glob("*.json"))) == 1
    assert len(list((curation / "lifecycle").glob("*.json"))) == 1
    assert write_calls(recorder)


def test_dry_run_still_refuses_an_unknown_document(world):
    recorder, _ = world
    recorder._respond = lambda cypher, params: []

    result = CliRunner().invoke(cli, ["ingest", "supersede", "--domain", "demo.one", "--newer", "a", "--older", "b",
                                      "--dryRun"])

    assert result.exit_code == 1 and "Could not resolve" in result.output
