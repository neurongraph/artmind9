"""`ingest resolve-conflict --dryRun`: looks the conflict up, writes no record and no status."""
import json

import pytest
from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher, write_calls
import artmind.conflicts as c
from artmind.cli import cli

NODE = {"id": "cf1", "status": "open", "verdict": "conflicting_claims", "severity": "low", "domains": ["demo"]}


@pytest.fixture()
def curation(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")
    return tmp_path / "curation"


def _adjudicator(monkeypatch):
    def respond(cypher, params):
        if "OPTIONAL MATCH (co)-[:CONFLICT_OF]" in cypher:
            return [{"co": NODE, "entities": [], "evidence": []}]
        return []

    recorder = WritableRecorder(respond)
    monkeypatch.setattr(c, "neo4j_session", lambda: recorder)
    return recorder


def _projection(monkeypatch):
    """No adjudicator node (no record can be made); the plain Conflict node answers."""
    def respond(cypher, params):
        if "RETURN co.status AS status" in cypher:
            return [{"status": "open"}]
        if "RETURN co.id AS id" in cypher:
            return [{"id": "cf1", "status": params["status"]}]
        return []

    recorder = WritableRecorder(respond)
    monkeypatch.setattr(c, "neo4j_session", lambda: recorder)
    return recorder


def _run(*args):
    result = CliRunner().invoke(cli, ["ingest", "resolve-conflict", "cf1", "--status", "resolved",
                                      "--reason", "settled", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_of_a_recorded_conflict_writes_no_file_and_no_cypher(monkeypatch, curation):
    recorder = _adjudicator(monkeypatch)

    payload = _run("--dryRun")

    assert_no_write_cypher(recorder)
    assert not curation.exists(), "no record file written"
    assert payload["dry_run"] is True
    assert_effect_shape(payload["effect"])
    assert payload["effect"]["changes"] == [
        {"kind": "conflict_resolve", "count": 1,
         "sample": [{"id": "cf1", "status_before": "open", "status_after": "resolved"}]}]
    assert payload["effect"]["travels"] is True
    assert payload["effect"]["records"] == [".artmind/data/curation/conflicts/cf1.json"]


def test_real_run_of_a_recorded_conflict_has_the_same_effect(monkeypatch, curation):
    recorder = _adjudicator(monkeypatch)

    dry = _run("--dryRun")
    real = _run()

    assert real["effect"] == dry["effect"] and "dry_run" not in real
    record = json.loads((curation / "conflicts" / "cf1.json").read_text())
    assert record["status"] == "resolved" and record["resolution_reason"] == "settled"
    assert write_calls(recorder), "the real run applies the node"


def test_dry_run_of_a_projection_conflict_is_a_graph_only_change(monkeypatch, curation):
    recorder = _projection(monkeypatch)

    dry = _run("--dryRun")

    assert_no_write_cypher(recorder)
    assert dry["effect"]["travels"] is False and dry["effect"]["records"] == []
    assert dry["effect"]["changes"][0]["sample"][0]["status_before"] == "open"

    real = _run()
    assert write_calls(recorder)[0][1]["status"] == "resolved"
    assert real["effect"]["summary"] == dry["effect"]["summary"]
    assert real["effect"]["travels"] is False


def test_dry_run_of_an_unknown_conflict_still_fails(monkeypatch, curation):
    monkeypatch.setattr(c, "neo4j_session", lambda: WritableRecorder())

    result = CliRunner().invoke(cli, ["ingest", "resolve-conflict", "cf1", "--status", "dismissed", "--dryRun"])

    assert result.exit_code == 1 and "cf1" in result.output
