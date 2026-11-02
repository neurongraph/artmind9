"""`traces prune --dryRun`: counts the turns that would go, deletes none."""
import json
import time

import pytest
from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from artmind.traces import store


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    old = time.time() - 200 * 86400
    for tid in ("old1", "old2", "kept"):
        store.insert_turn(tid, source="admin", question="q", started_at=old)
    store.insert_turn("recent", source="admin", question="q", started_at=time.time())
    store.add_feedback("kept", "good")


def _prune(*args):
    result = CliRunner().invoke(cli, ["traces", "prune", "--days", "90", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _turns():
    return {t["trace_id"] for t in store.list_turns(limit=100)}


def test_dry_run_deletes_nothing_and_matches_the_real_count():
    before = _turns()

    dry = _prune("--dryRun")
    assert _turns() == before
    real = _prune()

    assert dry["dry_run"] is True and "dry_run" not in real
    assert dry["pruned"] == real["pruned"] == 2
    assert dry["effect"] == real["effect"]
    assert_effect_shape(dry["effect"])
    assert dry["effect"]["changes"][0]["kind"] == "trace_delete"
    assert dry["effect"]["changes"][0]["count"] == dry["pruned"]
    assert _turns() == {"recent", "kept"}
