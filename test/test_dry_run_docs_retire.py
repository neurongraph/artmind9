"""`docs retire --dryRun` (restore: test_dry_run_docs_restore.py): reads what would move; no record file, no label swap, no sweep."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher, write_calls
from artmind.cli import cli

KEY = "acme ltd|ORGANISATION|demo.one"


def _respond(cypher, params):
    if "d.id = $ref" in cypher:
        return [{"id": "doc-1", "name": "policy"}]
    if "DISTINCT o.key AS key" in cypher:
        return [{"key": KEY}]
    if "count(o) AS n" in cypher:
        return [{"n": 4}] if "REMOVE" not in cypher else [{"n": 4}]
    if "RETURN d.name AS name" in cypher:
        return [{"name": "policy"}]
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
    swept = []
    with patch("artmind.graph_query.read_session", return_value=recorder), \
         patch("artmind.lifecycle.neo4j_session", return_value=recorder), \
         patch("artmind.projection.rebuild", return_value={"rebuilt": 1}), \
         patch("artmind.lifecycle._sweep", side_effect=lambda d, k: swept.append((d, k)) or 0):
        yield recorder, tmp_path / "curation", swept


def _run(verb, *args):
    result = CliRunner().invoke(cli, ["docs", verb, "--domain", "demo.one", "--documentName", "policy", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_sends_no_write_cypher_and_writes_no_record(world, verb="retire", kind="document_retire"):
    recorder, curation, swept = world

    payload = _run(verb, "--dryRun")

    assert_no_write_cypher(recorder)
    assert not curation.exists() and swept == []
    assert payload["dry_run"] is True and payload["observations"] == 4
    assert_effect_shape(payload["effect"])
    assert [c["kind"] for c in payload["effect"]["changes"]] == [kind, "observation_move", "entity_change"]
    assert payload["effect"]["changes"][1]["count"] == 4
    assert payload["effect"]["stale"]["topics"] == ["acme"]
    assert payload["effect"]["travels"] is True and len(payload["effect"]["records"]) == 1


def test_retire_real_run_has_the_same_effect_and_writes(world):
    recorder, curation, swept = world

    dry = _run("retire", "--dryRun")
    real = _run("retire")

    assert real["effect"] == dry["effect"] and "dry_run" not in real
    assert (curation / "lifecycle").is_dir() and len(list((curation / "lifecycle").glob("*.json"))) == 1
    assert write_calls(recorder), "the real run swaps labels"
    assert swept and swept[0][0] == "demo.one"
