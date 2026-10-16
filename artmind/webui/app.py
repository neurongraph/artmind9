"""FastAPI app serving the artmind chat web UI."""

import asyncio
import functools
import json
import logging
import os
import re
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anyio
import anyio.to_thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from artmind.conversations import store as conv_store
from artmind.conversations.recorder import ConversationRecorder
from artmind.conversations.replay import build_context
from artmind.webui.backends import ADMIN_PROFILE, BACKEND_NAMES, DEFAULT_BACKEND, backend_factory
from artmind.webui.benchmark_routes import register_benchmark_routes
from artmind.webui.dashboard_routes import register_dashboard_routes
from artmind.webui.sessions import SessionRegistry
from artmind.webui.source_links import SourceLinkError, obsidian_uri, open_uri, vault_relative

logger = logging.getLogger(__name__)

WEBUI_DIR = Path(__file__).resolve().parent
DEFAULT_UI_PORT = 8378
DEFAULT_ADMIN_UI_PORT = 8379
#: Longest `?prompt=` the admin console prefills (the Obsidian plugin's
#: table-review deep link is one sentence; anything longer is not a link).
PREFILL_MAX_CHARS = 2000
SWEEP_INTERVAL_S = 60


_CONVERSATION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class _ClosingStreamingResponse(StreamingResponse):
    """Closes the body generator when the response ends or is cancelled, so the
    generator's `finally` (turn recording, agent cleanup) always runs promptly
    rather than at garbage collection. `on_close` runs last, even when the
    generator never started (an unstarted generator's `finally` never runs)."""

    def __init__(self, content, *, on_close=None, **kwargs) -> None:
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    if self._on_close is not None:
                        self._on_close()


class ChatRequest(BaseModel):
    conversation_id: str
    prompt: str
    backend: str = DEFAULT_BACKEND

    @field_validator("conversation_id")
    @classmethod
    def _valid_conversation_id(cls, value: str) -> str:
        if not _CONVERSATION_ID.fullmatch(value):
            raise ValueError("conversation_id must be 1-64 characters of letters, digits, '-' or '_'")
        return value

    @field_validator("backend")
    @classmethod
    def _known_backend(cls, value: str) -> str:
        if value not in BACKEND_NAMES:
            raise ValueError(f"backend must be one of {BACKEND_NAMES}")
        return value


class OpenSourceRequest(BaseModel):
    path: str


class FeedbackRequest(BaseModel):
    rating: Literal["good", "partial", "wrong"]
    expected: str | None = None


def static_url(name: str) -> str:
    """`/static/<name>?v=<mtime>` -- a changed asset gets a new URL. /static is
    served with no cache headers, so a browser (notably Obsidian's Web Viewer)
    otherwise keeps running its cached app.js against a restarted, newer server."""
    try:
        version = (WEBUI_DIR / "static" / name).stat().st_mtime_ns
    except OSError:
        version = 0
    return f"/static/{name}?v={version}"


def create_app(
    registry: SessionRegistry | None = None,
    template_name: str = "index.html",
    page_title: str | None = None,
    admin_routes: bool = False,
    trace_turns: bool = False,
) -> FastAPI:
    registry = registry or SessionRegistry()
    profile_name = "admin" if admin_routes else "qa"
    # conversations with a turn currently streaming -> that turn's owner token
    in_flight: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if trace_turns:
            try:
                from artmind.traces import store

                await asyncio.to_thread(store.prune, int(os.environ.get("ARTMIND_TRACE_RETENTION_DAYS", "90")))
            except Exception:
                logger.exception("trace prune failed")

        async def sweep_loop():
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_S)
                try:
                    await registry.sweep()
                except Exception:
                    logger.exception("session sweep failed")

        task = asyncio.create_task(sweep_loop())
        yield
        task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEBUI_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=WEBUI_DIR / "templates")
    templates.env.globals["static_url"] = static_url

    @app.get("/api/health")
    async def health():
        """Who is on this port: which app, for which vault, at which version,
        in which process. The Obsidian plugin probes it before opening the
        admin console -- a port answering is not proof it is artmind, let
        alone this vault's -- and matches `pid` before stopping one it started.
        """
        import os
        from importlib.metadata import PackageNotFoundError, version

        import paths

        try:
            artmind_version = version("artmind9")
        except PackageNotFoundError:
            artmind_version = None
        vault = paths.ARTMIND_VAULT_DIR
        return {
            "app": "admin-ui" if admin_routes else "chat-ui",
            "vault": str(vault) if vault else None,
            "version": artmind_version,
            "pid": os.getpid(),
        }

    @app.get("/")
    async def index(request: Request):
        context = {"page_title": page_title} if page_title is not None else {}
        if admin_routes:
            # `?prompt=` prefills the agent chat's composer (plan 2026-10-03
            # B7). Rendered into the textarea, autoescaped; app.js only
            # enables Send -- nothing is ever sent without the operator.
            context["prefill"] = request.query_params.get("prompt", "")[:PREFILL_MAX_CHARS]
        return templates.TemplateResponse(request, template_name, context)

    @app.post("/api/chat")
    async def chat(payload: ChatRequest) -> StreamingResponse:
        from artmind.traces.recorder import TraceRecorder, public_event

        cid = payload.conversation_id
        existing = await asyncio.to_thread(conv_store.get, cid)
        if existing is not None and existing["profile"] != profile_name:
            raise HTTPException(status_code=404, detail=f"no conversation {cid}")
        # Nothing is awaited between this check and the claim below, so two
        # requests cannot both pass it.
        if cid in in_flight:
            raise HTTPException(status_code=409, detail="this conversation already has a turn in progress")
        owner = object()
        in_flight[cid] = owner

        def release() -> None:
            # Idempotent, and never releases a later turn's claim.
            if in_flight.get(cid) is owner:
                del in_flight[cid]

        async def stream():
            recorder = None
            conv = None
            error = None
            fresh = False
            delivered = False
            completed = False
            replay_failed = False
            try:
                # Recorded first so a turn whose agent fails to start is still
                # kept (as an error) rather than silently lost.
                # The thread hop is shielded so a cancel during it cannot
                # orphan a `running` row.
                with anyio.CancelScope(shield=True):
                    conv = await anyio.to_thread.run_sync(functools.partial(
                        ConversationRecorder.start,
                        conversation_id=cid, profile=profile_name, user_text=payload.prompt, backend=payload.backend,
                    ))
                client, fresh = await registry.get_or_create(cid, payload.backend)
                prompt = payload.prompt
                if fresh:
                    # The agent has no memory of this conversation: replay it.
                    replay = None
                    try:
                        prior = await asyncio.to_thread(conv_store.list_turns, cid)
                        replay = build_context(prior)
                    except Exception:
                        logger.exception("conversation replay unavailable")
                        replay_failed = True  # drop the agent below so the next turn retries the replay
                    if replay is not None:
                        prompt = f"{replay.text}\n\n{payload.prompt}"
                        restored = {"type": "context_restored", "turns": replay.included, "omitted": replay.omitted}
                        yield f"data: {json.dumps(restored)}\n\n"
                context_path = getattr(client, "trace_context_path", None)
                if trace_turns and context_path is not None:
                    # Query-trace capture (spec 2026-10-11 §5.1): the id goes into
                    # the session's context file before the agent sees the prompt.
                    with anyio.CancelScope(shield=True):  # as for the conversation recorder
                        recorder = await anyio.to_thread.run_sync(functools.partial(
                            TraceRecorder.start,
                            source="admin", question=payload.prompt, session_id=cid,
                            profile="admin", backend=payload.backend, context_path=context_path,
                        ))
                    if recorder.enabled:
                        yield f"data: {json.dumps({'type': 'trace', 'trace_id': recorder.trace_id})}\n\n"
                await client.query(prompt)
                delivered = True
                async for event in client.receive_events():
                    event = recorder.observe(event) if recorder is not None else public_event(event)
                    conv.observe(event)
                    if event.get("type") == "turn_done":
                        completed = True
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as exc:  # stream errors to the client, don't 500
                error = str(exc)
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            except BaseException:
                error = error or "interrupted: the client disconnected"
                raise
            finally:
                trace_id = recorder.trace_id if recorder is not None and recorder.enabled else None

                def _finish() -> None:
                    if recorder is not None:
                        recorder.finish(error=error)
                    if conv is not None:
                        conv.finish(error=error, trace_id=trace_id)

                # Shielded: on a client disconnect Starlette cancels this task
                # and re-cancels every await, which would skip the recording.
                # The claim is released last, so the next turn cannot start
                # (and write the trace-context file) while this one is still
                # finishing.
                try:
                    with anyio.CancelScope(shield=True):
                        # A live agent that did not see this turn through is
                        # unsafe to reuse: it never received the replay, or it
                        # keeps producing this turn's remaining events, which
                        # the next turn would read as its own. Dropping it makes
                        # the next turn fresh, so it replays the stored history.
                        if (fresh and not delivered) or (delivered and not completed) or replay_failed:
                            try:
                                with anyio.move_on_after(10, shield=True) as drop_scope:
                                    await registry.drop(cid)
                                if drop_scope.cancel_called:
                                    logger.error("timed out dropping the agent for conversation %s", cid)
                            except Exception:
                                logger.exception("could not drop the agent for conversation %s", cid)
                        await anyio.to_thread.run_sync(_finish)
                finally:
                    release()

        return _ClosingStreamingResponse(stream(), media_type="text/event-stream", on_close=release)

    @app.post("/api/open-source")
    async def open_source(payload: OpenSourceRequest):
        """Open a cited source document in Obsidian -- see `source_links`."""
        import paths

        vault = paths.ARTMIND_VAULT_DIR
        if vault is None:
            raise HTTPException(status_code=409, detail="no vault: source links open only inside a vault")
        try:
            rel = vault_relative(payload.path, vault)
        except SourceLinkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        uri = obsidian_uri(vault, rel)
        try:
            await asyncio.to_thread(open_uri, uri)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=500, detail=f"could not open {uri}: {exc}") from None
        return {"ok": True, "path": rel, "uri": uri}

    @app.post("/api/session/{session_id}/interrupt")
    async def interrupt(session_id: str):
        client = registry.peek(session_id)
        if client is not None:
            await client.interrupt()
        return {"ok": True}

    @app.post("/api/session/{session_id}/refresh")
    async def refresh_session(session_id: str):
        """Rebuild a live session's agent so a just-authored skill is picked up
        (skills load only at session start — ADR 0007 / A7). On the Claude SDK
        backend the conversation context is resumed; on ACP it restarts clean.
        Reports whether context was preserved so the UI can tell the user."""
        client = await registry.refresh(session_id)
        if client is None:
            return {"ok": False, "refreshed": False, "reason": "no live session"}
        return {
            "ok": True,
            "refreshed": True,
            "context_preserved": bool(getattr(client, "supports_resume", False)),
            "session_id": getattr(client, "session_id", None),
        }

    @app.delete("/api/session/{session_id}")
    async def close_session(session_id: str):
        """Release the live agent for a conversation. The stored conversation
        is kept; DELETE /api/conversations/{id} removes it."""
        await registry.drop(session_id)
        return {"ok": True}

    if trace_turns:
        @app.post("/api/traces/{trace_id}/feedback")
        async def trace_feedback(trace_id: str, payload: FeedbackRequest):
            from artmind.traces import store

            try:
                await asyncio.to_thread(store.add_feedback, trace_id, payload.rating, payload.expected)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"no trace {trace_id}") from None
            return {"ok": True}

    if admin_routes:
        register_dashboard_routes(app, templates)
        register_benchmark_routes(app, templates)

    return app


def run_chat_ui(host: str = "127.0.0.1", port: int = DEFAULT_UI_PORT) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


def run_admin_ui(host: str = "127.0.0.1", port: int = DEFAULT_ADMIN_UI_PORT) -> None:
    import uvicorn

    app = create_app(
        SessionRegistry(client_factory=backend_factory(ADMIN_PROFILE, trace=True)),
        template_name="admin.html",
        admin_routes=True,
        trace_turns=True,
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")
