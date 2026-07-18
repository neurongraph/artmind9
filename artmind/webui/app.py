"""FastAPI app serving the artmind chat web UI."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from artmind.webui.backends import ADMIN_PROFILE, BACKEND_NAMES, DEFAULT_BACKEND, backend_factory
from artmind.webui.sessions import SessionRegistry

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


def create_app(
    registry: SessionRegistry | None = None,
    template_name: str = "index.html",
    page_title: str | None = None,
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

    @app.post("/api/session/{session_id}/interrupt")
    async def interrupt(session_id: str):
        client = registry.peek(session_id)
        if client is not None:
            await client.interrupt()
        return {"ok": True}

    @app.delete("/api/session/{session_id}")
    async def close_session(session_id: str):
        await registry.drop(session_id)
        return {"ok": True}

    return app


def run_chat_ui(host: str = "127.0.0.1", port: int = DEFAULT_UI_PORT) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


def run_admin_ui(host: str = "127.0.0.1", port: int = DEFAULT_ADMIN_UI_PORT) -> None:
    import uvicorn

    app = create_app(
        SessionRegistry(client_factory=backend_factory(ADMIN_PROFILE)),
        template_name="admin.html",
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")
