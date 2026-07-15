"""Scripted fake ACP agent for tests: ndjson JSON-RPC 2.0 over stdio.

Usage: python fake_acp_agent.py [happy|permission|crash|cancel]

Answers initialize/session-new, then replays a canned ``session/update``
sequence per scenario when prompted. Mirrors the wire behavior of
``opencode acp`` closely enough to exercise ACPBackend end-to-end.
"""

import json
import sys

SESSION_ID = "sess-1"


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def respond(message_id, result) -> None:
    send({"jsonrpc": "2.0", "id": message_id, "result": result})


def notify(update: dict) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": SESSION_ID, "update": update},
        }
    )


def chunk(kind: str, text: str) -> dict:
    return {"sessionUpdate": kind, "content": {"type": "text", "text": text}}


def run_happy(msg, lines):
    notify(chunk("agent_thought_chunk", "pondering"))
    notify(chunk("agent_message_chunk", "Hello "))
    notify(chunk("agent_message_chunk", "world."))
    notify(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "call-1",
            "title": "artmind query",
            "kind": "execute",
            "status": "pending",
            "rawInput": {"command": "artmind query pattern1"},
        }
    )
    notify({"sessionUpdate": "mystery_future_variant", "data": 123})
    notify(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-1",
            "status": "completed",
            "content": [
                {"type": "content", "content": {"type": "text", "text": "42 rows"}}
            ],
        }
    )
    notify(chunk("agent_message_chunk", "Done."))
    respond(msg["id"], {"stopReason": "end_turn"})


def run_permission(msg, lines):
    send(
        {
            "jsonrpc": "2.0",
            "id": 9001,
            "method": "session/request_permission",
            "params": {
                "sessionId": SESSION_ID,
                "toolCall": {"toolCallId": "call-1", "title": "run command"},
                "options": [
                    {"optionId": "once", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "always", "name": "Always", "kind": "allow_always"},
                    {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                ],
            },
        }
    )
    reply = json.loads(next(lines))
    option = reply.get("result", {}).get("outcome", {}).get("optionId", "?")
    notify(chunk("agent_message_chunk", f"perm:{option}"))
    respond(msg["id"], {"stopReason": "end_turn"})


def run_crash(msg, lines):
    notify(chunk("agent_message_chunk", "partial"))
    sys.exit(1)


def run_cancel(msg, lines):
    notify(chunk("agent_message_chunk", "working on it"))
    for line in lines:  # wait for session/cancel before ending the turn
        inbound = json.loads(line)
        if inbound.get("method") == "session/cancel":
            respond(msg["id"], {"stopReason": "cancelled"})
            return


SCENARIOS = {
    "happy": run_happy,
    "permission": run_permission,
    "crash": run_crash,
    "cancel": run_cancel,
}


def main() -> None:
    scenario = SCENARIOS[sys.argv[1] if len(sys.argv) > 1 else "happy"]
    lines = iter(sys.stdin)
    for line in lines:
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            respond(msg["id"], {"protocolVersion": 1, "agentCapabilities": {}})
        elif method == "session/new":
            respond(msg["id"], {"sessionId": SESSION_ID})
        elif method == "session/set_mode":
            respond(msg["id"], {})
        elif method == "session/prompt":
            scenario(msg, lines)


if __name__ == "__main__":
    main()
