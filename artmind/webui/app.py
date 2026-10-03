"""FastAPI app serving the artmind chat web UI."""

import asyncio
import json
import logging
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from artmind.webui.backends import ADMIN_PROFILE, BACKEND_NAMES, DEFAULT_BACKEND, backend_factory
from artmind.webui.benchmark_routes import register_benchmark_routes
from artmind.webui.dashboard_routes import register_dashboard_routes
from artmind.webui.sessions import SessionRegistry
from artmind.webui.source_links import SourceLinkError, obsidian_uri, open_uri, vault_relative

logger = logging.getLogger(__name__)

WEBUI_DIR = Path(__file__).resolve().parent
DEFAULT_UI_PORT = 8378
DEFAULT_ADMIN_UI_PORT = 8379
SWEEP_INTERVAL_S = 60


class ChatRequest(BaseModel):
    session_id: str
    prompt: str
    backend: str = DEFAULT_BACKEND

    @field_validator("backend")
    @classmethod
    def _known_backend(cls, value: str) -> str:
        if value not in BACKEND_NAMES:
            raise ValueError(f"backend must be one of {BACKEND_NAMES}")
        return value


class OpenSourceRequest(BaseModel):
    path: str


def create_app(
    registry: SessionRegistry | None = None,
    template_name: str = "index.html",
    page_title: str | None = None,
    admin_routes: bool = False,
) -> FastAPI:
    registry = registry or SessionRegistry()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
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
        return templates.TemplateResponse(request, template_name, context)

    @app.post("/api/chat")
    async def chat(payload: ChatRequest) -> StreamingResponse:
        async def stream():
            try:
                client = await registry.get(payload.session_id, payload.backend)
                await client.query(payload.prompt)
                async for event in client.receive_events():
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as exc:  # stream errors to the client, don't 500
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

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
        await registry.drop(session_id)
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
        SessionRegistry(client_factory=backend_factory(ADMIN_PROFILE)),
        template_name="admin.html",
        admin_routes=True,
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")
