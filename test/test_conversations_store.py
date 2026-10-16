"""Conversation tables and artmind.conversations.store (spec 2026-10-16 §3-§4)."""

import pytest

from artmind.conversations import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _columns(table: str) -> set[str]:
    import artmind.db as db

    conn = db._get_db()
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_tables_exist_with_spec_columns():
    assert {
        "conversation_id", "profile", "title", "title_source", "pinned", "archived_at",
        "created_at", "updated_at",
    } <= _columns("conversations")
    assert {
        "conversation_id", "seq", "user_text", "blocks", "answer_text", "backend", "status",
        "error_message", "trace_id", "cost_usd", "duration_s", "started_at", "completed_at",
    } <= _columns("conversation_turns")


def test_make_title_collapses_whitespace_and_truncates():
    assert store.make_title("  hello\n  world \t!") == "hello world !"
    long = "word " * 40
    title = store.make_title(long)
    assert len(title) == store.TITLE_MAX
    assert title.endswith("…")
    assert store.make_title("   ") == "New conversation"


def test_ensure_creates_once_and_keeps_the_first_title():
    first = store.ensure("c1", "qa", "First question", now=100.0)
    again = store.ensure("c1", "qa", "A different question", now=200.0)
    assert first["title"] == again["title"] == "First question"
    assert again["title_source"] == "auto"
    assert again["created_at"] == 100.0


def test_ensure_rejects_a_conversation_owned_by_another_profile():
    store.ensure("c1", "qa", "Hi")
    with pytest.raises(store.WrongProfile):
        store.ensure("c1", "admin", "Hi")


def test_get_filters_by_profile():
    store.ensure("c1", "qa", "Hi")
    assert store.get("c1")["profile"] == "qa"
    assert store.get("c1", profile="qa") is not None
    assert store.get("c1", profile="admin") is None
    assert store.get("nope") is None


def test_turn_lifecycle_assigns_sequential_seq_and_bumps_updated_at():
    store.ensure("c1", "qa", "Hi", now=100.0)
    seq0 = store.begin_turn("c1", "Hi", "claude-sdk", now=110.0)
    row = store.list_turns("c1")[0]
    assert (seq0, row["status"], row["blocks"], row["answer_text"]) == (0, "running", [], None)
    assert store.get("c1")["updated_at"] == 110.0

    store.finish_turn(
        "c1", seq0, blocks=[{"type": "text", "md": "Hello"}], answer_text="Hello", status="done",
        trace_id="t1", cost_usd=0.02, duration_s=1.5, now=120.0,
    )
    seq1 = store.begin_turn("c1", "Again", "acp", now=130.0)
    assert seq1 == 1
    turns = store.list_turns("c1")
    assert [t["seq"] for t in turns] == [0, 1]
    assert turns[0]["blocks"] == [{"type": "text", "md": "Hello"}]
    assert (turns[0]["answer_text"], turns[0]["status"], turns[0]["trace_id"]) == ("Hello", "done", "t1")
    assert (turns[0]["cost_usd"], turns[0]["duration_s"], turns[0]["completed_at"]) == (0.02, 1.5, 120.0)
    assert turns[1]["backend"] == "acp"
    assert store.get("c1")["updated_at"] == 130.0


def test_finish_turn_rejects_unknown_status():
    store.ensure("c1", "qa", "Hi")
    seq = store.begin_turn("c1", "Hi", "claude-sdk")
    with pytest.raises(ValueError):
        store.finish_turn("c1", seq, blocks=[], answer_text=None, status="bogus")


def test_list_orders_by_activity_and_paginates_with_a_keyset_cursor():
    for i, cid in enumerate(["a", "b", "c"]):
        store.ensure(cid, "qa", f"title {cid}", now=100.0 + i)
    page1 = store.list_conversations("qa", limit=2)
    assert [c["conversation_id"] for c in page1] == ["c", "b"]
    assert page1[0]["turn_count"] == 0
    last = page1[-1]
    page2 = store.list_conversations("qa", limit=2, before=(last["pinned"], last["updated_at"], last["conversation_id"]))
    assert [c["conversation_id"] for c in page2] == ["a"]


def test_list_ties_on_updated_at_do_not_skip_or_repeat():
    for cid in ["a", "b", "c"]:
        store.ensure(cid, "qa", cid, now=100.0)
    page1 = store.list_conversations("qa", limit=2)
    last = page1[-1]
    page2 = store.list_conversations("qa", limit=2, before=(last["pinned"], last["updated_at"], last["conversation_id"]))
    seen = [c["conversation_id"] for c in page1 + page2]
    assert sorted(seen) == ["a", "b", "c"] and len(seen) == 3


def test_list_filters_profile_and_archived():
    store.ensure("q1", "qa", "q")
    store.ensure("a1", "admin", "a")
    store.ensure("q2", "qa", "q2")
    store.update("q2", "qa", archived=True, now=500.0)
    assert [c["conversation_id"] for c in store.list_conversations("qa")] == ["q1"]
    assert [c["conversation_id"] for c in store.list_conversations("qa", archived=True)] == ["q2"]
    assert [c["conversation_id"] for c in store.list_conversations("admin")] == ["a1"]


def test_update_rename_pin_archive_and_unarchive():
    store.ensure("c1", "qa", "Original", now=100.0)
    assert store.update("c1", "qa", title="  Renamed  ") is True
    row = store.get("c1")
    assert (row["title"], row["title_source"], row["updated_at"]) == ("Renamed", "user", 100.0)
    store.update("c1", "qa", pinned=True)
    assert store.get("c1")["pinned"] == 1
    store.update("c1", "qa", archived=True, now=300.0)
    assert store.get("c1")["archived_at"] == 300.0
    store.update("c1", "qa", archived=False)
    assert store.get("c1")["archived_at"] is None
    assert store.update("c1", "admin", title="x") is False
    assert store.update("nope", "qa", title="x") is False


def test_delete_removes_conversation_and_turns_only_for_its_profile():
    store.ensure("c1", "qa", "Hi")
    store.begin_turn("c1", "Hi", "claude-sdk")
    assert store.delete("c1", "admin") is False
    assert store.get("c1") is not None
    assert store.delete("c1", "qa") is True
    assert store.get("c1") is None
    assert store.list_turns("c1") == []
    assert store.delete("c1", "qa") is False


def test_sweep_running_marks_only_this_profiles_running_turns():
    store.ensure("q", "qa", "q")
    store.ensure("a", "admin", "a")
    store.begin_turn("q", "q", "claude-sdk")
    store.begin_turn("a", "a", "claude-sdk")
    done = store.begin_turn("q", "q2", "claude-sdk")
    store.finish_turn("q", done, blocks=[], answer_text="x", status="done")

    assert store.sweep_running("qa", now=900.0) == 1
    qa_turns = store.list_turns("q")
    assert (qa_turns[0]["status"], qa_turns[0]["completed_at"]) == ("interrupted", 900.0)
    assert qa_turns[0]["error_message"] == "interrupted: server restarted"
    assert qa_turns[1]["status"] == "done"
    assert store.list_turns("a")[0]["status"] == "running"


def test_update_blank_title_raises_and_leaves_title_unchanged():
    store.ensure("c1", "qa", "Original")
    with pytest.raises(ValueError):
        store.update("c1", "qa", title="   ")
    assert store.get("c1")["title"] == "Original"
    assert store.get("c1")["title_source"] == "auto"


def test_update_with_no_fields_reports_existence_for_the_profile():
    store.ensure("c1", "qa", "Hi")
    assert store.update("c1", "qa") is True
    assert store.update("c1", "admin") is False
    assert store.update("nope", "qa") is False


def test_finish_turn_on_a_missing_turn_does_not_raise():
    store.finish_turn("nope", 3, blocks=[], answer_text=None, status="done")
    assert store.list_turns("nope") == []


def test_finish_turn_serialises_non_json_values_in_blocks():
    import datetime

    store.ensure("c1", "qa", "Hi")
    seq = store.begin_turn("c1", "Hi", "claude-sdk")
    store.finish_turn(
        "c1", seq, status="done", answer_text="x",
        blocks=[{"type": "tool", "id": "t", "input": {1, 2}, "result": datetime.datetime(2026, 1, 2)}],
    )
    turn = store.list_turns("c1")[0]
    assert turn["status"] == "done"
    assert turn["blocks"][0]["type"] == "tool"
    assert "2026-01-02" in turn["blocks"][0]["result"]


def test_begin_turn_caps_an_oversize_user_text():
    from artmind.traces.store import PAYLOAD_CAP

    store.ensure("c1", "qa", "Hi")
    store.begin_turn("c1", "x" * (PAYLOAD_CAP + 100), "claude-sdk")
    stored = store.list_turns("c1")[0]["user_text"]
    assert "truncated" in stored
    assert len(stored) < PAYLOAD_CAP + 100


def test_list_turns_tolerates_corrupt_blocks_json():
    import artmind.db as db

    store.ensure("c1", "qa", "Hi")
    store.begin_turn("c1", "Hi", "claude-sdk")
    conn = db._get_db()
    try:
        conn.execute("UPDATE conversation_turns SET blocks = 'not json' WHERE conversation_id = 'c1'")
        conn.commit()
    finally:
        conn.close()
    assert store.list_turns("c1")[0]["blocks"] == []


def test_list_puts_pinned_first_and_pages_across_the_boundary():
    for i, cid in enumerate(["a", "b", "c", "d"]):
        store.ensure(cid, "qa", cid, now=100.0 + i)
    store.update("a", "qa", pinned=True)
    page1 = store.list_conversations("qa", limit=2)
    assert [c["conversation_id"] for c in page1] == ["a", "d"]
    last = page1[-1]
    page2 = store.list_conversations("qa", limit=2, before=(last["pinned"], last["updated_at"], last["conversation_id"]))
    assert [c["conversation_id"] for c in page2] == ["c", "b"]


def test_list_turns_without_blocks_reads_only_what_replay_needs():
    store.ensure("c1", "qa", "Hi")
    seq = store.begin_turn("c1", "Hi", "claude-sdk")
    store.finish_turn("c1", seq, blocks=[{"type": "text", "md": "A"}], answer_text="A", status="done")
    slim = store.list_turns("c1", with_blocks=False)
    assert slim == [{"seq": 0, "user_text": "Hi", "answer_text": "A", "status": "done", "blocks": []}]
    assert store.list_turns("c1")[0]["blocks"] == [{"type": "text", "md": "A"}]
