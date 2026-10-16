# Chat conversations and history -- design

Date: 2026-10-16. Status: design approved, awaiting spec review.
Scope: phases 1 and 2 (storage, API, replay, sidebar UI). Phase 3 (FTS search,
markdown export, retention prune) is out of scope and gets its own spec.

## 1. Problem

Chat history exists only as rendered HTML in the browser's `sessionStorage`
(`artmind/webui/static/app.js`, "chat history persistence"). Consequences:

- A closed tab, or a second device, loses the conversation. The server stores
  nothing for chat-ui; admin-ui records turns only as traces.
- The restored view is misleading. `sessionId` is a fresh `crypto.randomUUID()`
  on every page load, and `pagehide` sends `DELETE /api/session/{id}` (which also
  fires when a source link navigates Obsidian's Web Viewer away). After "back" the
  old conversation is on screen but the agent has no memory of it.
- `trace_turns` is not a conversation store: admin-only, pruned by age (default
  90 days), keeps only the text after the last tool result, caps payloads at
  64 KB, and mixes in benchmark rows.

Goal: server-side, durable conversations for chat-ui and admin-ui, listed in a
sidebar, reopenable and continuable, stored in the existing SQLite registry DB.

## 2. Decisions

| Topic | Decision |
|---|---|
| Entity name | **conversation**. "session" already means `SessionRegistry` (live agent connections), `update_sessions` (update drafts) and `artmind session` (graph snapshots). |
| Storage | New tables in `document_registry.db` (`DATA_DIR`, machine-local, never synced). Not an extension of `trace_turns`. |
| Stored content | Raw markdown plus structured blocks, never HTML. The client re-renders with marked + DOMPurify. |
| Context on reopen | **Always replay a condensed transcript** into a freshly created agent. No native provider resume, no `agent_session_id`. Uniform across backends. |
| Replay bound | User questions plus final answers (no thinking, no tool calls), newest first, within a character budget; older turns dropped with an "N earlier turns omitted" line. No LLM summarisation. |
| Backend switch | Continues the same conversation: the new backend gets the transcript replayed. Each turn records the backend that answered. |
| Visibility | Each UI lists only its own profile (chat-ui `qa`, admin-ui `admin`). One shared table. |
| Retention | Keep until deleted. `ARTMIND_CONVERSATION_RETENTION_DAYS` is a phase 3 option. |
| Titles | First user message, truncated to 60 chars. User rename sets `title_source = 'user'`. No LLM titles. |

## 3. Data model

Created in `db._init_db` (same place as the trace tables). Timestamps are epoch
seconds (REAL), matching the trace tables.

```sql
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,   -- uuid4 hex; also the live-registry key
    profile         TEXT NOT NULL,      -- 'qa' | 'admin'
    title           TEXT NOT NULL,
    title_source    TEXT NOT NULL DEFAULT 'auto',   -- 'auto' | 'user'
    pinned          INTEGER NOT NULL DEFAULT 0,
    archived_at     REAL,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_profile_updated
    ON conversations(profile, updated_at);

CREATE TABLE IF NOT EXISTS conversation_turns (
    conversation_id TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    user_text       TEXT NOT NULL,
    blocks          TEXT NOT NULL,      -- JSON, see below
    answer_text     TEXT,               -- final answer text; replay source
    backend         TEXT NOT NULL,
    status          TEXT NOT NULL,      -- running | done | interrupted | error
    error_message   TEXT,
    trace_id        TEXT,               -- admin only; links to trace_turns
    cost_usd        REAL,
    duration_s      REAL,
    started_at      REAL NOT NULL,
    completed_at    REAL,
    PRIMARY KEY (conversation_id, seq)
);
```

`blocks` is an ordered JSON list:
`{"type": "thinking", "md": "..."}`,
`{"type": "text", "md": "..."}`,
`{"type": "tool", "id": "...", "name": "...", "input": "...", "result": "..."}`.
Each string goes through `traces.store.cap()` (64 KB). `answer_text` is the
concatenation of `text` blocks emitted after the last tool result, the same rule
`TraceRecorder` uses for `trace_turns.answer`, so a turn that narrates between
tool calls does not replay the narration.

Deleting a conversation removes its turns in one transaction (no FK cascade
reliance; the store deletes both explicitly, as `store.prune` does for traces).

## 4. Server

New package `artmind/conversations/`:

- `store.py` -- plain functions, each opens and closes its own connection via
  `artmind.db._get_db()` (the registry convention): `ensure` (create if absent;
  the first message sets the title), `get`, `list_conversations` (profile and
  `archived` filters; ordered pinned first, then most recently active; keyset
  pagination on `(pinned, updated_at, conversation_id)`), `update` (rename, pin,
  archive), `delete`, `begin_turn` (returns `seq`), `finish_turn`, `list_turns`
  (`with_blocks=False` for the lean replay read), `sweep_running(profile)`.
- `recorder.py` -- `ConversationRecorder`, modelled on `TraceRecorder`:
  `start(...)` creates the conversation row on the first turn and inserts the
  turn as `running` before the agent sees the prompt; `observe(event)` folds the
  neutral UI events (`text_delta`, `thinking_delta`, `block_done`, `tool_call`,
  `tool_call_update`, `tool_result`, `turn_done`, `error`) into `blocks`;
  `finish(error)` writes blocks, `answer_text`, status, cost and duration. A
  recorder that fails to start disables itself and logs; it never breaks a turn.
  The stream's `finally` calls `finish` with `error="interrupted: ..."` on
  `BaseException` so a client disconnect leaves an `interrupted` turn, mirroring
  the existing trace handling in `app.py`.
- `replay.py` -- `build_context(turns, budget=None) -> Replay | None`, where
  `Replay` carries `text`, `included` and `omitted` (the route needs the counts
  for the `context_restored` event).

All store calls from request handlers run through `asyncio.to_thread`.

### 4.1 `/api/chat`

`ChatRequest.session_id` is replaced by `conversation_id` (a client-generated
uuid, so the first message can create the conversation and the UI can put the
id in the URL before the response arrives).

1. Reject with 409 if `conversation_id` already has a turn in flight (an
   in-memory set in `create_app`; a second tab POSTing mid-turn must not
   interleave two prompts into one agent).
2. `client, fresh = await registry.get_or_create(conversation_id, backend)`.
   `get_or_create` is `SessionRegistry.get` plus a flag that is true when it
   created or replaced the client (including the existing backend-mismatch
   replacement). `get` stays for compatibility.
3. Start the `ConversationRecorder` (and, in admin-ui, the existing
   `TraceRecorder`; the turn row stores `trace_id`).
4. If `fresh` and the conversation has prior `done` turns, build the transcript
   with `replay.build_context` and prepend it to the prompt sent to the agent
   (after the ACP persona preamble, which `ACPBackend.query` handles itself).
   The stored `user_text` and the trace `question` stay the user's original text,
   not the augmented prompt. Emit a `{"type": "context_restored", "turns":
   included, "omitted": dropped}` event first so the UI can show a notice.
5. Stream events as today.

The response always closes its generator (`_ClosingStreamingResponse`), and
recording finishes under a shielded scope before the in-flight claim is
released, so a client disconnect leaves the turn `interrupted` (not `running`).
A turn that did not reach `turn_done` (disconnect, Stop, agent error), whose
prompt was never delivered, or whose replay read failed drops the live agent, so
the next turn is fresh and replays the stored `done` turns instead of reading
the abandoned turn's remaining events.

Replay format (plain text, backend-neutral):

```
[Restored conversation. The earlier exchange is below; continue it naturally.
 N earlier turns omitted.]
User: <question>
Assistant: <final answer>
...
[End of restored conversation. The new message follows.]
```

Turns are taken newest first until `ARTMIND_REPLAY_BUDGET_CHARS` (default 24000)
is reached, then emitted oldest first. A single turn larger than the budget is
truncated with an ellipsis rather than skipped. Only `done` turns are replayed
(`interrupted` and `error` turns are shown in the UI but carry no answer).

### 4.2 Routes (both apps)

The profile is fixed by the app (`admin` when `create_app(admin_routes=True)`,
else `qa`) and is never client-supplied.

- `GET  /api/conversations?limit&cursor&archived` -- list for this app's profile, ordered pinned first, then most recently active.
- `GET  /api/conversations/{id}` -- conversation plus all turns. 404 if the id
  does not exist or belongs to another profile.
- `PATCH /api/conversations/{id}` -- `{title?, pinned?, archived?}`.
- `DELETE /api/conversations/{id}` -- drops the live agent, then the rows.
- `DELETE /api/session/{id}` -- now only releases the live agent (kept for the
  `pagehide` cleanup); renamed in docs as "release". `interrupt` and `refresh`
  routes keep their paths and take the conversation id.

### 4.3 Lifecycle

- `create_app` lifespan calls `store.sweep_running(profile)` once at startup:
  any `running` turn of this app's profile becomes `interrupted` (a server crash
  or restart). Scoped to the profile because chat-ui and admin-ui are separate
  processes sharing one DB; starting one must not touch the other's live turns.
- The registry's 30-minute idle sweep still drops only live agents. The next
  message after a sweep is `fresh`, so it replays.
- Skill refresh (`/refresh`) is unchanged: SDK keeps native context across
  `restart()`; ACP restarts clean. If a refresh drops context, the next turn is
  *not* auto-replayed in this phase (the registry entry is not fresh); this is a
  known gap, noted in section 7.

## 5. UI (`app.js`, `style.css`, `index.html`, `admin.html`)

- **Sidebar**, collapsible, remembered per viewer in `localStorage` (a
  convenience only). New chat button; a pinned section; groups Today,
  Yesterday, Previous 7 days, Earlier; a client-side title filter box over the
  loaded page; row actions rename, pin, archive, delete (delete confirms).
  Cursor-paged "load more".
- **URL is the state**: `/?c=<conversation_id>`. A new chat generates the id
  client-side and `history.replaceState`s it on the first send. Loading a URL
  with `c=` fetches `GET /api/conversations/{id}` and renders turns; an unknown
  id shows "conversation not found" and starts a fresh chat. This replaces the
  `sessionStorage` history, including `pushHistory`/`restoreHistory`, which are
  deleted. Back/forward and the Obsidian Web Viewer round trip now reload from
  the server.
- **Rendering a stored turn**: user pill from `user_text`; for `text` blocks,
  `DOMPurify.sanitize(marked.parse(md))` then the existing `addCopyButtons` and
  `linkVaultSources`; `thinking` blocks as collapsed `<details>`; `tool` blocks
  populate the trace drawer (so the tool badge count matches). `interrupted` and
  `error` turns show a small status line.
- **Backend picker**: stays enabled between turns. A switch inserts the existing
  divider ("Continued on <backend>") and keeps the conversation. It no longer
  generates a new id or deletes the session server-side; the next send carries
  the new `backend`, the registry replaces the client, and replay runs.
- **Admin-only**: restored turns with a `trace_id` re-render the feedback
  control (existing `feedbackControl`, delegated handler already in place). There
  is no web trace page to link to, so the `trace_id` is stored for
  `artmind traces show`.
- **`context_restored` event**: renders a quiet divider line, "Context restored
  from N earlier turns (M older omitted)".
- The composer and streaming code are otherwise unchanged. The `?prompt=`
  prefill stays; it opens a new conversation.

## 6. Testing (`test/`, hermetic, no Neo4j or network)

- `store`: temp DB; create/list ordering and pagination, profile isolation,
  rename/pin/archive, delete removes turns, `sweep_running`.
- `recorder`: synthetic event streams -- plain answer, narration between tool
  calls (answer_text excludes it), tool call + update + result, error event,
  `BaseException` mid-stream (turn ends `interrupted`), recorder-start failure
  (turn still streams, nothing recorded).
- `replay`: budget, newest-first selection with oldest-first output, omitted
  count, oversize single turn, only `done` turns, empty history returns `None`.
- routes (FastAPI `TestClient` with a fake backend, per `test_update.py`'s
  recorder pattern): assert the **exact prompt sent to the backend** on a fresh
  client with history, no prefix when the client is live, original text stored
  in `user_text`, 409 on a concurrent turn, cross-profile 404, delete drops the
  live client then the rows, backend switch triggers replay and records the new
  backend on that turn.
- UI: verified manually in the browser preview on both apps (sidebar, URL
  restore, back-navigation, backend switch, admin feedback restore). The
  long-running `chat-ui` / `admin-ui` processes must be restarted to pick up the
  change; `artmind/webui/static` URLs are mtime-versioned already.

## 7. Known gaps and risks

- After a skill refresh on ACP the agent loses context but the next turn is not
  auto-replayed. Fix later by having `registry.refresh` mark the entry
  `needs_replay` when `supports_resume` is false.
- Replay changes what the agent sees, not only what the UI shows. Answers that
  depended on tool results from earlier turns are re-derived from the final
  answers only; the agent may re-run a query. Accepted trade-off of option C.
- Conversations contain private corpus content. They stay in the machine-local
  DB (`.artmind/data` in a vault, which is not synced); dossier-style leak rules
  do not apply because nothing is written to the checkout. Never copy
  conversation content into code, tests or commits -- tests use synthetic text.
- Two processes (chat-ui and admin-ui) write one SQLite file. WAL and the 30 s
  busy timeout already cover this for the registry; the new writes are small
  and per-turn.
- `updated_at` is bumped when a turn starts and finishes; the list order is
  "most recently active", not "most recently created".
