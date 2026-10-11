"""TraceRecorder: turn-side capture, tool-id matching, derived columns."""

import json

import pytest

from artmind.traces import store
from artmind.traces.recorder import TraceRecorder, domains_of, match_tool_ids, public_event


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _span(trace_id, span_id, command, started_at, rows=1, documents=None, domain=("b",)):
    store.insert_span({
        "span_id": span_id, "trace_id": trace_id, "command": command,
        "args": json.dumps({"domain": list(domain)}), "exit_code": 0, "duration_ms": 5, "rows": rows,
        "rows_total": None, "truncated": 0,
        "evidence": json.dumps({"documents": [{"doc_id": None, "path": p} for p in documents or []]}),
        "started_at": started_at,
    }, [])


def test_public_event_strips_trace_key_without_mutating():
    event = {"type": "tool_call", "input": "x", "_trace": {"input": "xx"}}
    assert public_event(event) == {"type": "tool_call", "input": "x"}
    assert "_trace" in event


def test_start_writes_context_file_and_running_row(tmp_path):
    ctx = tmp_path / "c.id"
    ctx.write_text("")
    rec = TraceRecorder.start(source="admin", question="Who?", session_id="s", profile="admin",
                              backend="claude-sdk", context_path=ctx)
    assert rec.enabled
    assert ctx.read_text() == rec.trace_id
    assert store.get_turn(rec.trace_id)["status"] == "running"


def test_start_failure_yields_disabled_recorder(monkeypatch):
    monkeypatch.setattr(store, "insert_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    rec = TraceRecorder.start(source="admin", question="Who?")
    assert not rec.enabled
    assert rec.observe({"type": "text_delta", "text": "a", "_trace": {}}) == {"type": "text_delta", "text": "a"}
    rec.finish()  # no-op, no raise


def test_full_turn_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.traces.recorder.graph_state", lambda domains: {"schemas": {}, "domains": domains})
    ctx = tmp_path / "c.id"
    rec = TraceRecorder.start(source="admin", question="Who leads X?", context_path=ctx)

    long_input = {"command": "artmind query vector-text --domain b 'who leads'" + " " * 2000}
    events = [
        {"type": "thinking_delta", "text": "plan"},
        {"type": "block_done", "block": "thinking"},
        {"type": "tool_call", "id": "tu1", "name": "Bash", "input": "clipped", "_trace": {"input": long_input, "parent_tool_id": None}},
        {"type": "tool_result", "tool_id": "tu1", "content": "clipped", "_trace": {"content": "FULL OUTPUT"}},
        {"type": "text_delta", "text": "Alice.\n\n### Sources\n- [P](<b/P.md>) — b\n"},
        {"type": "block_done", "block": "text"},
        {"type": "turn_done", "turns": 3, "duration_s": 4.2, "cost": 0.05,
         "_trace": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20},
                    "model": "claude-x", "result": "Alice."}},
    ]
    out = [rec.observe(e) for e in events]
    assert all("_trace" not in e for e in out)

    # the CLI wrote its span while the tool call was running
    window = rec._tools["tu1"]
    _span(rec.trace_id, "sp1", "query vector-text",
          started_at=(window["call_ts"] + window["result_ts"]) / 2, rows=2, documents=["b/P.md"])
    rec.finish()

    turn = store.get_turn(rec.trace_id)
    assert turn["status"] == "done"
    assert turn["answer"].startswith("Alice.")
    assert (turn["turns"], turn["duration_s"], turn["cost_usd"]) == (3, 4.2, 0.05)
    assert (turn["input_tokens"], turn["output_tokens"], turn["model"]) == (150, 20, "claude-x")
    assert turn["domains"] == ["b"]
    assert turn["answer_basis"] == "vector"
    assert turn["fell_through"] == 0  # no graph attempt, so not a fall-through
    assert turn["retrieval_path"][0]["command"] == "query vector-text"

    recorded = store.list_events(rec.trace_id)
    assert [e["type"] for e in recorded] == ["thinking", "tool_call", "tool_result", "text"]
    assert json.loads(recorded[1]["payload"]) == long_input  # unclipped
    assert recorded[2]["payload"] == "FULL OUTPUT"
    assert store.list_spans(rec.trace_id)[0]["tool_id"] == "tu1"
    assert ctx.read_text() == ""  # cleared at turn end


def test_tool_call_update_replaces_input():
    rec = TraceRecorder.start(source="admin", question="q")
    rec.observe({"type": "tool_call", "id": "c1", "name": "bash", "input": "{}", "_trace": {"input": {"cwd": "/v"}}})
    rec.observe({"type": "tool_call_update", "id": "c1", "name": "bash", "input": "x",
                 "_trace": {"input": {"command": "artmind query chunks"}}})
    rec.finish()
    events = store.list_events(rec.trace_id)
    assert [e["type"] for e in events] == ["tool_call"]
    assert json.loads(events[0]["payload"]) == {"command": "artmind query chunks"}


def test_error_marks_turn():
    rec = TraceRecorder.start(source="admin", question="q")
    rec.observe({"type": "error", "message": "boom"})
    rec.finish(error="stream died")
    turn = store.get_turn(rec.trace_id)
    assert turn["status"] == "error"
    assert turn["error_message"] == "stream died"


def test_match_tool_ids_by_window_and_command_text():
    tools = {
        "a": {"input": {"command": "artmind query graph pattern5 --domain b"}, "call_ts": 1.0, "result_ts": 2.0},
        "b": {"input": {"command": "artmind query vector-text --domain b q"}, "call_ts": 3.0, "result_ts": 4.0},
        "c": {"input": {"command": "ls"}, "call_ts": 5.0, "result_ts": 6.0},
    }
    spans = [
        {"span_id": "s1", "command": "query graph pattern5", "started_at": 1.5},
        {"span_id": "s2", "command": "query vector-text", "started_at": 3.2},
        {"span_id": "s3", "command": "query chunks", "started_at": 5.5},  # no artmind call there
    ]
    assert match_tool_ids(spans, tools) == [("s1", "a"), ("s2", "b")]


def test_domains_of_splits_commas_and_reads_argv():
    spans = [{"args": {"domain": ["banking,hr"]}}, {"args": ["vector-text", "--domain", "ops", "q"]},
             {"args": None}]
    assert domains_of(spans) == ["banking", "hr", "ops"]


def test_leaf_word_inside_a_question_does_not_match():
    tools = {"a": {"input": {"command": "artmind query vector-text --domain b 'how are chunks made'"},
                   "call_ts": 1.0, "result_ts": 2.0}}
    assert match_tool_ids([{"span_id": "s1", "command": "query chunks", "started_at": 1.5}], tools) == []


def test_interrupted_text_block_is_still_stored():
    rec = TraceRecorder.start(source="admin", question="q")
    rec.observe({"type": "text_delta", "text": "partial answer"})
    rec.finish(error="interrupted")
    assert [e["type"] for e in store.list_events(rec.trace_id)] == ["text"]
    assert store.get_turn(rec.trace_id)["answer"] == "partial answer"


def test_answer_joins_text_blocks_around_tool_calls():
    rec = TraceRecorder.start(source="admin", question="q")
    for e in [
        {"type": "text_delta", "text": "Looking. "}, {"type": "block_done", "block": "text"},
        {"type": "tool_call", "id": "t", "name": "Bash", "input": "x", "_trace": {"input": {"command": "ls"}}},
        {"type": "tool_result", "tool_id": "t", "content": "x"},
        {"type": "text_delta", "text": "Alice.\n\n### Sources\n- [P](<b/P.md>) — b\n"}, {"type": "block_done", "block": "text"},
    ]:
        rec.observe(e)
    rec.finish()
    answer = store.get_turn(rec.trace_id)["answer"]
    assert answer.startswith("Looking. ") and answer.endswith("— b\n")


def test_leaf_word_inside_a_double_quoted_question_does_not_match():
    tools = {"a": {"input": {"command": 'artmind query vector-text --domain b "how are \\"chunks\\" made"'},
                   "call_ts": 1.0, "result_ts": 2.0},
             "b": {"input": {"command": 'artmind query chunks --domain b --idList c1'},
                   "call_ts": 1.2, "result_ts": 2.0}}
    assert match_tool_ids([{"span_id": "s1", "command": "query chunks", "started_at": 1.5}], tools) == [("s1", "b")]
    del tools["b"]
    assert match_tool_ids([{"span_id": "s1", "command": "query chunks", "started_at": 1.5}], tools) == []
