"""ConversationRecorder: folds neutral UI events into stored blocks."""

import pytest

from artmind.conversations import store
from artmind.conversations.recorder import ConversationRecorder
from artmind.traces.store import PAYLOAD_CAP


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _start(cid="c1", user_text="Who?", backend="claude-sdk"):
    return ConversationRecorder.start(conversation_id=cid, profile="qa", user_text=user_text, backend=backend)


def _feed(rec, events):
    for event in events:
        rec.observe(event)


def _only_turn(cid="c1"):
    turns = store.list_turns(cid)
    assert len(turns) == 1
    return turns[0]


def test_start_creates_conversation_and_running_turn():
    rec = _start(user_text="  Who runs   Atlas?  ")
    assert rec.enabled
    assert store.get("c1")["title"] == "Who runs Atlas?"
    turn = _only_turn()
    assert (turn["status"], turn["user_text"], turn["backend"]) == ("running", "  Who runs   Atlas?  ", "claude-sdk")


def test_plain_answer_becomes_one_text_block_with_cost_and_duration():
    rec = _start()
    _feed(rec, [
        {"type": "text_delta", "text": "Hel"},
        {"type": "text_delta", "text": "lo"},
        {"type": "block_done", "block": "text"},
        {"type": "turn_done", "turns": 1, "duration_s": 2.5, "cost": 0.02},
    ])
    rec.finish()
    turn = _only_turn()
    assert turn["blocks"] == [{"type": "text", "md": "Hello"}]
    assert (turn["answer_text"], turn["status"], turn["cost_usd"], turn["duration_s"]) == ("Hello", "done", 0.02, 2.5)


def test_thinking_precedes_text_and_is_not_part_of_the_answer():
    rec = _start()
    _feed(rec, [
        {"type": "thinking_delta", "text": "hmm"},
        {"type": "block_done", "block": "thinking"},
        {"type": "text_delta", "text": "Answer"},
        {"type": "block_done", "block": "text"},
    ])
    rec.finish()
    turn = _only_turn()
    assert [b["type"] for b in turn["blocks"]] == ["thinking", "text"]
    assert turn["blocks"][0]["md"] == "hmm"
    assert turn["answer_text"] == "Answer"


def test_tool_call_update_and_result_fill_one_tool_block_in_order():
    rec = _start()
    _feed(rec, [
        {"type": "text_delta", "text": "Looking."},
        {"type": "block_done", "block": "text"},
        {"type": "tool_call", "id": "t1", "name": "Bash", "input": "artmind query"},
        {"type": "tool_call_update", "id": "t1", "name": "Bash", "input": "artmind query chunks"},
        {"type": "tool_result", "tool_id": "t1", "content": "3 rows"},
        {"type": "text_delta", "text": "Found it."},
        {"type": "block_done", "block": "text"},
    ])
    rec.finish()
    turn = _only_turn()
    assert turn["blocks"] == [
        {"type": "text", "md": "Looking."},
        {"type": "tool", "id": "t1", "name": "Bash", "input": "artmind query chunks", "result": "3 rows"},
        {"type": "text", "md": "Found it."},
    ]
    # narration before the last tool result is not the answer
    assert turn["answer_text"] == "Found it."


def test_unflushed_text_before_a_tool_call_keeps_its_place():
    rec = _start()
    _feed(rec, [
        {"type": "text_delta", "text": "Checking"},
        {"type": "tool_call", "id": "t1", "name": "Bash", "input": "x"},
        {"type": "tool_result", "tool_id": "t1", "content": "ok"},
        {"type": "text_delta", "text": "Done"},
    ])
    rec.finish()
    blocks = _only_turn()["blocks"]
    assert [b["type"] for b in blocks] == ["text", "tool", "text"]
    assert blocks[0]["md"] == "Checking" and blocks[2]["md"] == "Done"


def test_tool_result_for_an_unknown_tool_is_ignored():
    rec = _start()
    _feed(rec, [{"type": "tool_result", "tool_id": "ghost", "content": "x"}, {"type": "text_delta", "text": "A"}])
    rec.finish()
    turn = _only_turn()
    assert [b["type"] for b in turn["blocks"]] == ["text"]


def test_an_error_event_marks_the_turn_error():
    rec = _start()
    _feed(rec, [{"type": "error", "message": "model overloaded"}])
    rec.finish()
    turn = _only_turn()
    assert (turn["status"], turn["error_message"]) == ("error", "model overloaded")


def test_finish_with_an_interrupted_error_is_interrupted_and_keeps_partial_text():
    rec = _start()
    _feed(rec, [{"type": "text_delta", "text": "Partial"}])
    rec.finish(error="interrupted: the client disconnected")
    turn = _only_turn()
    assert (turn["status"], turn["answer_text"]) == ("interrupted", "Partial")
    assert turn["blocks"] == [{"type": "text", "md": "Partial"}]


def test_finish_with_another_error_is_error_and_records_the_trace_id():
    rec = _start()
    rec.finish(error="boom", trace_id="trace-9")
    turn = _only_turn()
    assert (turn["status"], turn["error_message"], turn["trace_id"]) == ("error", "boom", "trace-9")


def test_long_tool_payloads_are_capped():
    rec = _start()
    big = "x" * (PAYLOAD_CAP + 1000)
    _feed(rec, [
        {"type": "tool_call", "id": "t1", "name": "Bash", "input": "i"},
        {"type": "tool_result", "tool_id": "t1", "content": big},
    ])
    rec.finish()
    result = _only_turn()["blocks"][0]["result"]
    assert len(result.encode()) < len(big.encode())
    assert "truncated" in result


def test_start_failure_yields_a_disabled_recorder_that_never_raises(monkeypatch):
    monkeypatch.setattr(store, "begin_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    rec = _start()
    assert not rec.enabled
    rec.observe({"type": "text_delta", "text": "a"})
    rec.finish(error="x")  # no-op, no raise


def test_observe_swallows_malformed_events():
    rec = _start()
    rec.observe({"type": "tool_call"})  # no id
    rec.observe({"type": "text_delta"})  # no text
    rec.finish()
    assert _only_turn()["status"] == "done"


def test_wrong_profile_disables_the_recorder():
    store.ensure("c1", "admin", "Hi")
    rec = ConversationRecorder.start(conversation_id="c1", profile="qa", user_text="Hi", backend="claude-sdk")
    assert not rec.enabled
