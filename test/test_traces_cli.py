"""`artmind traces list/show/triage/feedback/stats/prune`."""

import json
import time

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    now = time.time()
    store.insert_turn("t1", source="admin", question="Who leads X?", started_at=now)
    store.update_turn("t1", answer="Alice", status="done", fell_through=1, answer_basis="vector",
                      graph_attempts=1, graph_productive=0, domains='["b"]', retrieval_path="[]", turns=2)


def _json(args):
    result = CliRunner().invoke(cli, ["traces", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_list_and_show():
    listed = _json(["list"])
    assert [t["trace_id"] for t in listed["turns"]] == ["t1"]
    assert listed["turns"][0]["question"] == "Who leads X?"
    shown = _json(["show", "t1"])
    assert shown["turn"]["answer"] == "Alice"


def test_show_missing_is_an_error():
    result = CliRunner().invoke(cli, ["traces", "show", "nope"])
    assert result.exit_code != 0
    assert "no trace" in result.output


def test_feedback_then_list_filters_by_rating():
    result = CliRunner().invoke(cli, ["traces", "feedback", "t1", "--rating", "wrong", "--expected", "Bob"])
    assert result.exit_code == 0, result.output
    assert [t["trace_id"] for t in _json(["list", "--rating", "wrong"])["turns"]] == ["t1"]
    assert _json(["list", "--rating", "good"])["turns"] == []


def test_triage_and_stats():
    assert [i["trace_id"] for i in _json(["triage"])["items"]] == ["t1"]
    stats = _json(["stats", "--by", "all"])["rows"]
    assert stats[0]["domain"] == "b" and stats[0]["graph_miss_rate"] == 1.0


def test_prune_reports_count():
    assert _json(["prune", "--days", "90"]) == {"pruned": 0}


def test_list_passes_limit_to_db_when_no_filters():
    """With no --rating and no --domain, --limit should be forwarded to the DB
    so the store does not fetch unbounded rows."""
    import json as _json_mod
    from artmind.traces import store as _store
    from unittest.mock import patch, MagicMock

    calls = []
    original = _store.list_turns

    def capturing(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    with patch.object(_store, "list_turns", side_effect=capturing):
        result = CliRunner().invoke(cli, ["traces", "list", "--limit", "5", "--compact"])
    assert result.exit_code == 0
    # When no rating/domain filter, limit should be passed as a kwarg to list_turns
    assert any(c.get("limit") == 5 for c in calls), f"calls: {calls}"


def test_list_rows_carry_the_conversation_id():
    store.insert_turn("t-conv", source="admin", question="q", session_id="conv-1")
    store.update_turn("t-conv", status="done")

    result = CliRunner().invoke(cli, ["traces", "list", "--compact"])

    assert result.exit_code == 0, result.output
    row = next(t for t in json.loads(result.stdout)["turns"] if t["trace_id"] == "t-conv")
    assert row["session_id"] == "conv-1"
