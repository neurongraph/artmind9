"""`sameas approve --dryRun` (spec 2026-11-02 §3.4): plans the approval, writes nothing."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher
from artmind.cli import cli

A = "alpha corp|ORGANISATION|demo.one"
B = "alpha corporation|ORGANISATION|demo.two"


def _proposal():
    return {"id": "p1", "status": "open", "canonical": A, "members": [A, B]}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """One open proposal; the recorder answers its lookup; same_as and the rebuild are observed."""
    import paths

    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path / "_Topics")
    (tmp_path / "_Topics").mkdir()
    (tmp_path / "_Topics" / "alpha.md").write_text(
        "---\nartmind_topic: Alpha\nanchor:\n  kind: entity\n  key: " + A + "\nsources: []\n---\nBody\n\n## My notes\n"
    )
    recorder = WritableRecorder(
        lambda cypher, params: [{"p": _proposal()}] if "RETURN properties(p)" in cypher else []
    )
    saved, rebuilt = [], []
    with patch("artmind.sameas.neo4j_session", return_value=recorder), \
         patch("artmind.same_as.load_groups", side_effect=lambda: []), \
         patch("artmind.same_as.save_groups", side_effect=lambda g, path=None: saved.append(list(g))), \
         patch("artmind.projection.full_rebuild", side_effect=lambda tx, d, **k: rebuilt.append(d) or {"rebuilt": 2}):
        yield recorder, saved, rebuilt


def _run(*args):
    result = CliRunner().invoke(cli, ["sameas", "approve", "p1", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_sends_no_write_cypher_saves_no_file_and_rebuilds_nothing(world):
    recorder, saved, rebuilt = world

    payload = _run("--dryRun")

    assert_no_write_cypher(recorder)
    assert saved == [] and rebuilt == []
    assert [c for c, _ in recorder.calls] and all(p == {"id": "p1"} for _, p in recorder.calls)
    assert payload["dry_run"] is True
    assert_effect_shape(payload["effect"])


def test_dry_run_effect_equals_the_real_runs(world):
    recorder, saved, rebuilt = world

    dry = _run("--dryRun")
    real = _run()

    assert dry["effect"] == real["effect"]
    assert len(saved) == 1 and rebuilt == [["demo.one", "demo.two"]]
    assert "dry_run" not in real
    change = real["effect"]["changes"][0]
    assert change["kind"] == "entity_merge" and change["count"] == 1
    assert change["sample"] == [{"canonical": A, "group": [A, B]}]
    assert real["effect"]["stale"]["topics"] == ["alpha"]
    assert real["effect"]["travels"] is True and real["effect"]["records"] == [".artmind/same_as.yaml"]


def test_dry_run_of_a_refused_proposal_is_still_an_error(world):
    recorder, _, _ = world
    recorder._respond = lambda cypher, params: []

    result = CliRunner().invoke(cli, ["sameas", "approve", "p1", "--dryRun", "--compact"])

    assert result.exit_code == 1 and "No SameAsProposal" in result.output
