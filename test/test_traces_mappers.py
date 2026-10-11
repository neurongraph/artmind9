"""Mappers add a private `_trace` key with unclipped data only when capturing."""

from claude_agent_sdk.types import AssistantMessage, ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage

from artmind.webui.agent import EventMapper
from artmind.webui.backends.acp_events import ACPEventMapper
from artmind.webui.backends.base import TRACE_CLIP

LONG = "x" * (TRACE_CLIP * 3)


def _assistant_tool_call():
    return AssistantMessage(content=[ToolUseBlock(id="tu1", name="Bash", input={"command": LONG})],
                            model="claude-test", parent_tool_use_id=None)


def test_claude_mapper_without_capture_has_no_trace_key():
    events = EventMapper().map(_assistant_tool_call())
    assert "_trace" not in events[0]


def test_claude_mapper_capture_keeps_raw_input_result_and_usage():
    mapper = EventMapper(capture=True)
    call = mapper.map(_assistant_tool_call())[0]
    assert len(call["input"]) < len(LONG)  # the UI copy is still clipped
    assert call["_trace"] == {"input": {"command": LONG}, "parent_tool_id": None}

    result = mapper.map(UserMessage(content=[ToolResultBlock(tool_use_id="tu1", content=LONG)]))[0]
    assert result["_trace"]["content"] == LONG

    done = mapper.map(ResultMessage(subtype="success", duration_ms=1500, duration_api_ms=1000, is_error=False,
                                    num_turns=4, session_id="s", total_cost_usd=0.02,
                                    usage={"input_tokens": 10, "output_tokens": 5}, result="final"))[0]
    assert done["type"] == "turn_done"
    assert done["_trace"] == {"usage": {"input_tokens": 10, "output_tokens": 5}, "model": "claude-test",
                              "result": "final"}


def test_acp_mapper_capture_keeps_raw_input_and_output():
    mapper = ACPEventMapper(capture=True)
    call = mapper.map_update({"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "bash",
                              "rawInput": {"command": LONG}})[0]
    assert call["_trace"] == {"input": {"command": LONG}}
    upd = mapper.map_update({"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "in_progress",
                             "rawInput": {"command": "artmind query chunks"}})[0]
    assert upd["_trace"] == {"input": {"command": "artmind query chunks"}}
    res = mapper.map_update({"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
                             "rawOutput": LONG})[0]
    assert res["_trace"]["content"] == LONG
    assert len(res["content"]) < len(LONG)


def test_acp_mapper_without_capture_unchanged():
    call = ACPEventMapper().map_update({"sessionUpdate": "tool_call", "toolCallId": "c1", "rawInput": {}})[0]
    assert "_trace" not in call
