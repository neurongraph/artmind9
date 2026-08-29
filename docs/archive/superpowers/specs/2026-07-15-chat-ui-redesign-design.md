# Chat UI Redesign — Design

**Date:** 2026-07-15
**Status:** Approved
**Replaces:** NiceGUI chat front end (`artmind/chat_ui.py`)

## Goal

Rebuild the artmind chat web UI with a modern, ChatGPT/Claude-style aesthetic:
free-flowing layout, subtle colors, word-by-word streaming. Same functional
elements as today — chat window, thinking blocks, right-hand tool-call drawer —
but no widget-toolkit look. Lightweight stack: no npm, no build step, no new
heavyweight dependencies.

## Decisions (from brainstorming)

- **Streaming:** word-by-word (SDK partial message deltas over SSE-style stream).
- **Scope:** single conversation per browser tab, no history sidebar, no
  persistence. Layout leaves room to add a history sidebar later.
- **Theme:** light and dark, following `prefers-color-scheme`, with a manual
  toggle persisted in `localStorage`.
- **Stack:** FastAPI + Jinja + one vanilla JS file. NiceGUI dependency removed.
  Approaches rejected: Flask + HTMX (sync framework fights the async Agent
  SDK; HTMX's fragment model fights token streaming), restyling NiceGUI
  (Quasar's boxed DOM structure is the problem being solved).

## Architecture

### File layout

```
artmind/webui/
  __init__.py
  app.py                 # FastAPI app: 3 routes
  agent.py               # session registry + SDK→event mapping
  templates/index.html   # chat shell (single Jinja template)
  static/style.css       # all styling; CSS variables for both themes
  static/app.js          # ~300 lines vanilla JS: composer, stream reader, DOM rendering
  static/vendor/marked.min.js   # only third-party file, vendored (~40KB), no npm
```

`artmind/chat_ui.py` is deleted. The agent configuration (`_agent_options()`:
artmind-* skills allowlist, system prompt append, `bypassPermissions`,
thinking budget 8000 summarized) moves to `agent.py` unchanged, plus
`include_partial_messages=True` for word-level deltas.

### Routes

- `GET /` — serves the chat shell (Jinja template).
- `POST /api/chat` — body `{session_id, prompt}`. Response is a streaming
  `text/event-stream` read by the browser via `fetch()` + ReadableStream
  (POST works because no EventSource is involved). One request per turn.
- `DELETE /api/session/{id}` — fired via `navigator.sendBeacon` on tab close;
  disconnects and drops the SDK client.

### Sessions

- Browser generates `session_id` with `crypto.randomUUID()` on page load.
- Server keeps `dict[session_id, ClaudeSDKClient]`; client created lazily on
  the session's first message. Same per-tab isolation as today.
- Idle sweep: sessions untouched for 30 minutes are disconnected and dropped.

### Dependencies

- Remove `nicegui`.
- Add `jinja2` and `uvicorn` as direct dependencies (both already in the
  lockfile transitively, so the environment gains nothing new).
- `artmind chat-ui` CLI command and justfile targets (`ui-start`,
  `serve-start`, `serve-stop`) keep working; `run_chat_ui()` now launches
  uvicorn on the same default port (8378).

## Streaming protocol

`agent.py` exposes a pure function mapping SDK messages to event dicts
(the unit-testable core):

| SDK input | Event emitted |
|---|---|
| partial `content_block_delta` (thinking) | `thinking_delta {text}` |
| partial `content_block_delta` (text) | `text_delta {text}` |
| `content_block_stop` | `block_done` |
| `ToolUseBlock` | `tool_call {id, name, input}` (input clipped at 600 chars) |
| `ToolResultBlock` | `tool_result {tool_id, content}` (clipped at 600 chars) |
| `ResultMessage` | `turn_done {turns, duration_s, cost}` |
| any exception | `error {message}` |

Client behavior: `text_delta`s append as plain text into the current block
while streaming; on `block_done` the block is re-rendered once through
marked.js (`fenced-code-blocks`, tables; avoids flickering half-parsed
markdown mid-stream).

## Visual design

Content floats on the page background; hierarchy comes from spacing and
typography, not boxes or borders.

- **Layout:** one centered column, `max-width: 48rem`. Minimal header:
  "artmind" left; theme toggle and trace-drawer toggle right as quiet icon
  buttons. No footer bar.
- **User messages:** soft rounded pill, right-aligned, subtle tinted
  background.
- **Assistant messages:** no bubble — markdown flows directly on the page
  background at comfortable line-height. Code blocks get a slightly inset
  background and a copy button.
- **Thinking:** one-line disclosure above the response. Label "Thinking"
  shimmers while deltas stream; expanded during the turn showing muted italic
  text; auto-collapses to `▸ Thought for Ns` when answer text starts. Click
  to re-expand.
- **Composer:** fixed at bottom, floating over a soft gradient fade.
  Rounded bordered textarea auto-grows to ~8 lines; send button inside it
  (arrow icon, accent-colored when input is non-empty). Enter sends,
  Shift+Enter inserts newline. While a turn runs the composer is disabled and
  the send button becomes a stop button.
- **Tool drawer:** slides in from the right (~380px; pushes content on wide
  screens, overlays on narrow). Each tool call is a card: name + status dot
  (running/done), args collapsed to one line and expandable, result attached
  beneath. Header toggle shows a badge with the call count.
- **Color & type:** system font stack, 15px base. CSS variables define both
  themes. Light: warm off-white background, near-black text, one muted accent
  (desaturated indigo). Dark: charcoal (not pure black), soft gray text.

## Error handling

- Server wraps each turn; exceptions stream out as an `error` event, render
  as an inline notice in the chat and an entry in the trace drawer, and the
  composer re-enables.
- If the fetch stream drops mid-turn, the client shows "connection lost —
  send again to retry"; the orphaned server-side client is reclaimed by the
  idle sweep.
- Stop button aborts the fetch client-side and calls `client.interrupt()`
  server-side.

## Testing

- `test/test_webui_events.py`: unit tests for the SDK→event mapping using
  fabricated SDK message objects. No API calls.
- Visual behavior (streaming, thinking collapse, drawer, themes, composer)
  verified live against the dev server. No JS test harness.

## Out of scope

- Conversation history / persistence / left sidebar.
- Authentication, multi-user concerns (localhost tool).
- Mobile-first work beyond the drawer overlay behavior.
