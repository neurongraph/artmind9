"""`sameas reject --dryRun`: reports the rejection, writes nothing."""
import json
from unittest.mock import patch

from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher, write_calls
from artmind.cli import cli

KEY = "alpha corp|ORGANISATION|demo.one"


def _recorder():
    def respond(cypher, params):
        if "RETURN properties(p)" in cypher:
            return [{"p": {"id": "p1", "status": "open", "canonical": KEY, "members": [KEY, "x|ORGANISATION|demo.one"]}}]
        if "RETURN p.id" in cypher:
            return [{"id": "p1", "status": "rejected", "canonical": KEY}]
        return []

    return WritableRecorder(respond)


def _run(*args):
    result = CliRunner().invoke(cli, ["sameas", "reject", "p1", "--reason", "not the same", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_sends_no_write_cypher():
    recorder = _recorder()
    with patch("artmind.sameas.neo4j_session", return_value=recorder):
        payload = _run("--dryRun")

    assert_no_write_cypher(recorder)
    assert payload["dry_run"] is True and payload["status"] == "rejected"
    assert_effect_shape(payload["effect"])
    assert payload["effect"]["changes"][0]["kind"] == "proposal_reject"


def test_real_run_writes_the_status_and_carries_the_same_effect():
    recorder = _recorder()
    with patch("artmind.sameas.neo4j_session", return_value=recorder):
        dry = _run("--dryRun")
        real = _run()

    writes = write_calls(recorder)
    assert len(writes) == 1 and writes[0][1]["id"] == "p1" and writes[0][1]["reason"] == "not the same"
    assert real["effect"] == dry["effect"] and "dry_run" not in real
