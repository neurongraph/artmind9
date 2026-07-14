"""NiceGUI chat front end for artmind, powered by the Claude Agent SDK.

The main pane shows only the conversation: user prompts and assistant text.
Thinking blocks, tool calls, and tool results stream into a collapsible
right-hand trace drawer instead. Each browser tab gets its own agent session
whose working directory is this repo, so the artmind-* skills, CLAUDE.md and
the serve-daemon fast path all apply unchanged.
"""

import json
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from nicegui import ui

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UI_PORT = 8378

_TRACE_CLIP = 600


def _clip(value, limit: int = _TRACE_CLIP) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else f"{text[:limit]} … [{len(text)} chars total]"


# The chat agent must NOT inherit the repo's developer environment (CLAUDE.md
# graphify rules, enforcement hooks): it serves end-user Q&A, not coding. So we
# enable only the artmind skills instead of loading all project settings.
_SYSTEM_APPEND = """\
You are the artmind assistant, an end-user interface to the artmind knowledge
system. Users ask about knowledge stored in artmind domains. Route their
requests through the artmind skills: artmind-query for questions,
artmind-update for adding facts, artmind-refine for graph maintenance,
artmind-ingestion-helper for ingesting documents. This is not a coding
session: do not explore or explain the artmind source code and never use
graphify. Answer conversationally; no raw JSON or command output unless asked."""


def _agent_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        skills=[
            "artmind-query",
            "artmind-update",
            "artmind-refine",
            "artmind-ingestion-helper",
        ],
        system_prompt={"type": "preset", "preset": "claude_code", "append": _SYSTEM_APPEND},
        permission_mode="bypassPermissions",
        thinking={"type": "enabled", "budget_tokens": 8000, "display": "summarized"},
    )


@ui.page("/")
async def chat_page():
    client = ClaudeSDKClient(_agent_options())
    connected = False

    ui.page_title("artmind")

    drawer = ui.right_drawer(value=False, fixed=True).props("bordered").classes("p-4")
    with drawer:
        ui.label("Agent trace").classes("text-base font-semibold")
        ui.label("Tool calls and results").classes("text-xs text-gray-500 mb-2")
        trace_column = ui.column().classes("w-full gap-1")

    with ui.header().classes("items-center justify-between px-4"):
        ui.label("artmind").classes("text-lg font-semibold")
        with ui.row().classes("items-center gap-1"):
            spinner = ui.spinner(size="1.5em")
            spinner.visible = False
            ui.button(icon="manage_search", on_click=drawer.toggle).props(
                "flat round color=white"
            ).tooltip("Toggle agent trace")

    with ui.column().classes("w-full max-w-3xl mx-auto pb-24"):
        chat_column = ui.column().classes("w-full")

    with ui.footer().classes("bg-transparent pb-4"):
        with ui.row().classes("w-full max-w-3xl mx-auto items-center gap-2 px-4"):
            input_box = (
                ui.input(placeholder="Ask artmind…")
                .classes("flex-grow")
                .props("outlined rounded dense")
            )
            send_button = ui.button(icon="send")

    open_thinking: list[ui.expansion] = []

    def add_thinking(text: str) -> None:
        # thinking lives in the main pane, muted, expanded while the turn runs
        # (live progress) and collapsed once the answer arrives
        with chat_column:
            expansion = (
                ui.expansion("Thinking", icon="psychology", value=True)
                .props("dense")
                .classes("w-full max-w-xl text-sm text-gray-500")
            )
            with expansion:
                ui.label(text).classes("text-xs italic text-gray-500 whitespace-pre-wrap")
        open_thinking.append(expansion)

    def collapse_thinking() -> None:
        for expansion in open_thinking:
            expansion.value = False
        open_thinking.clear()

    def add_trace(kind: str, title: str, body: str) -> None:
        icons = {"tool": "build", "result": "output", "done": "flag", "error": "error"}
        with trace_column:
            with ui.expansion(title, icon=icons.get(kind, "info")).classes(
                "w-full border rounded text-sm"
            ):
                ui.label(body).classes("text-xs font-mono whitespace-pre-wrap break-all")

    def add_assistant_text(text: str) -> None:
        with chat_column:
            with ui.chat_message(name="artmind", sent=False).props(
                "bg-color=purple-2 text-color=grey-10"
            ).classes("w-full"):
                # code-friendly stops mid-word underscores (entity_names) becoming italics
                ui.markdown(text, extras=["fenced-code-blocks", "tables", "code-friendly"])

    def handle_message(message) -> None:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    add_assistant_text(block.text)
                elif isinstance(block, ThinkingBlock) and block.thinking.strip():
                    add_thinking(block.thinking)
                elif isinstance(block, ToolUseBlock):
                    add_trace("tool", f"Tool: {block.name}", _clip(block.input))
        elif isinstance(message, UserMessage):
            content = message.content if isinstance(message.content, list) else []
            for block in content:
                if isinstance(block, ToolResultBlock):
                    add_trace("result", "Tool result", _clip(block.content))
        elif isinstance(message, ResultMessage):
            collapse_thinking()
            cost = getattr(message, "total_cost_usd", None)
            summary = (
                f"turns={message.num_turns} duration={message.duration_ms / 1000:.1f}s"
                + (f" cost=${cost:.4f}" if cost else "")
            )
            add_trace("done", "Turn complete", summary)
        ui.run_javascript("window.scrollTo(0, document.body.scrollHeight)")

    async def send() -> None:
        nonlocal connected
        prompt = (input_box.value or "").strip()
        if not prompt or spinner.visible:
            return
        input_box.value = ""
        spinner.visible = True
        with chat_column:
            ui.chat_message(prompt, name="you", sent=True).classes("w-full")
        try:
            if not connected:
                await client.connect()
                connected = True
            await client.query(prompt)
            async for message in client.receive_response():
                handle_message(message)
        except Exception as exc:  # surface agent/CLI failures in both panes
            ui.notify(f"Agent error: {exc}", type="negative")
            add_trace("error", "Error", str(exc))
        finally:
            spinner.visible = False

    send_button.on_click(send)
    input_box.on("keydown.enter", send)

    async def cleanup() -> None:
        if connected:
            await client.disconnect()

    ui.context.client.on_disconnect(cleanup)


def run_chat_ui(host: str = "127.0.0.1", port: int = DEFAULT_UI_PORT) -> None:
    ui.run(host=host, port=port, title="artmind", dark=None, reload=False, show=False)
