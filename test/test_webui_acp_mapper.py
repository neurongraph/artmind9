"""Unit tests for the ACP session/update → UI event mapper."""

from artmind.webui.backends.acp_events import ACPEventMapper


def _chunk(kind: str, text: str) -> dict:
    return {"sessionUpdate": kind, "content": {"type": "text", "text": text}}


def test_message_chunks_stream_as_text_deltas():
    mapper = ACPEventMapper()
    assert mapper.map_update(_chunk("agent_message_chunk", "Hello ")) == [
        {"type": "text_delta", "text": "Hello "}
    ]
    assert mapper.map_update(_chunk("agent_message_chunk", "world")) == [
        {"type": "text_delta", "text": "world"}
    ]


def test_thought_chunks_stream_as_thinking_deltas():
    mapper = ACPEventMapper()
    assert mapper.map_update(_chunk("agent_thought_chunk", "hmm")) == [
        {"type": "thinking_delta", "text": "hmm"}
    ]


def test_chunk_kind_transition_closes_previous_block():
    mapper = ACPEventMapper()
    mapper.map_update(_chunk("agent_thought_chunk", "hmm"))
    events = mapper.map_update(_chunk("agent_message_chunk", "Hi"))
    assert events == [
        {"type": "block_done", "block": "thinking"},
        {"type": "text_delta", "text": "Hi"},
    ]


def test_tool_call_closes_open_text_block():
    mapper = ACPEventMapper()
    mapper.map_update(_chunk("agent_message_chunk", "Let me check"))
    events = mapper.map_update(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "call-1",
            "title": "artmind query",
            "kind": "execute",
            "rawInput": {"command": "artmind query pattern1"},
        }
    )
    assert events[0] == {"type": "block_done", "block": "text"}
    assert events[1]["type"] == "tool_call"
    assert events[1]["id"] == "call-1"
    assert events[1]["name"] == "artmind query"
    assert "pattern1" in events[1]["input"]


def test_tool_call_falls_back_to_kind_for_name():
    mapper = ACPEventMapper()
    [event] = mapper.map_update(
        {"sessionUpdate": "tool_call", "toolCallId": "c", "kind": "read"}
    )
    assert event["name"] == "read"


def test_tool_call_update_completed_maps_to_tool_result():
    mapper = ACPEventMapper()
    [event] = mapper.map_update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-1",
            "status": "completed",
            "content": [
                {"type": "content", "content": {"type": "text", "text": "42 rows"}}
            ],
        }
    )
    assert event == {"type": "tool_result", "tool_id": "call-1", "content": "42 rows"}


def test_tool_call_update_failed_is_prefixed():
    mapper = ACPEventMapper()
    [event] = mapper.map_update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-1",
            "status": "failed",
            "rawOutput": "command not found",
        }
    )
    assert event["content"].startswith("[failed] ")
    assert "command not found" in event["content"]


def test_tool_call_update_in_progress_is_ignored():
    mapper = ACPEventMapper()
    assert (
        mapper.map_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call-1",
                "status": "in_progress",
            }
        )
        == []
    )


def test_unknown_update_variants_are_ignored():
    mapper = ACPEventMapper()
    assert mapper.map_update({"sessionUpdate": "plan", "entries": []}) == []
    assert mapper.map_update({"sessionUpdate": "brand_new_thing", "x": 1}) == []
    assert mapper.map_update({}) == []


def test_finish_flushes_open_block_and_emits_turn_done():
    mapper = ACPEventMapper()
    mapper.map_update(_chunk("agent_message_chunk", "Hi"))
    events = mapper.finish("end_turn", 2.34)
    assert events == [
        {"type": "block_done", "block": "text"},
        {"type": "turn_done", "turns": 1, "duration_s": 2.3, "cost": None},
    ]


def test_finish_on_refusal_emits_error_before_turn_done():
    mapper = ACPEventMapper()
    events = mapper.finish("refusal", 0.5)
    assert events[0]["type"] == "error"
    assert events[-1]["type"] == "turn_done"
