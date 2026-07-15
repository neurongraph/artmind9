"""Unit tests for the SDK→UI event mapping. No API calls."""

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from artmind.webui.agent import EventMapper, clip


def _stream_event(event: dict, parent_tool_use_id=None) -> StreamEvent:
    return StreamEvent(
        uuid="u1", session_id="s1", event=event, parent_tool_use_id=parent_tool_use_id
    )


def test_text_delta():
    mapper = EventMapper()
    events = mapper.map(
        _stream_event(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "Hel"}}
        )
    )
    assert events == [{"type": "text_delta", "text": "Hel"}]


def test_thinking_delta():
    mapper = EventMapper()
    events = mapper.map(
        _stream_event(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "hmm"}}
        )
    )
    assert events == [{"type": "thinking_delta", "text": "hmm"}]


def test_block_done_reports_block_type_from_start_event():
    mapper = EventMapper()
    mapper.map(_stream_event(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking"}}))
    mapper.map(_stream_event(
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text"}}))
    assert mapper.map(_stream_event({"type": "content_block_stop", "index": 0})) == [
        {"type": "block_done", "block": "thinking"}
    ]
    assert mapper.map(_stream_event({"type": "content_block_stop", "index": 1})) == [
        {"type": "block_done", "block": "text"}
    ]


def test_tool_use_block_stop_emits_nothing():
    mapper = EventMapper()
    mapper.map(_stream_event(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use"}}))
    assert mapper.map(_stream_event({"type": "content_block_stop", "index": 0})) == []


def test_subagent_stream_events_are_skipped():
    mapper = EventMapper()
    events = mapper.map(
        _stream_event(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "inner"}},
            parent_tool_use_id="tool_1",
        )
    )
    assert events == []


def test_assistant_message_emits_tool_calls_only():
    # text/thinking already arrived via deltas; only ToolUseBlocks map to events
    message = AssistantMessage(
        content=[
            TextBlock(text="already streamed"),
            ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
        ],
        model="claude-fable-5",
    )
    events = EventMapper().map(message)
    assert events == [
        {"type": "tool_call", "id": "t1", "name": "Bash",
         "input": clip({"command": "ls"})}
    ]


def test_user_message_emits_tool_results():
    message = UserMessage(
        content=[ToolResultBlock(tool_use_id="t1", content="file1\nfile2")]
    )
    events = EventMapper().map(message)
    assert events == [
        {"type": "tool_result", "tool_id": "t1", "content": "file1\nfile2"}
    ]


def test_user_message_with_plain_string_content_emits_nothing():
    assert EventMapper().map(UserMessage(content="hello")) == []


def test_result_message_emits_turn_done():
    message = ResultMessage(
        subtype="success", duration_ms=12345, duration_api_ms=10000,
        is_error=False, num_turns=3, session_id="s1", total_cost_usd=0.0421,
    )
    events = EventMapper().map(message)
    assert events == [
        {"type": "turn_done", "turns": 3, "duration_s": 12.3, "cost": 0.0421}
    ]


def test_clip_truncates_long_values():
    long = "x" * 700
    clipped = clip(long)
    assert clipped.startswith("x" * 600)
    assert "[700 chars total]" in clipped
    assert clip("short") == "short"
    assert clip({"a": 1}) == '{"a": 1}'
