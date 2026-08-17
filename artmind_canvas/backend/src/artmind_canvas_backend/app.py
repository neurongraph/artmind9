"""artmind_canvas backend — FastAPI app (pure client of artmind, ADR 0003).

Mirrors artmind/webui/app.py's SSE chat contract but binds the CanvasBackend
wrapper (which owns the ``render`` event) and serves the Vite-built frontend
from ../frontend/dist when present. In dev the Vite server proxies /api/* here.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from artmind.webui.backends import BACKEND_NAMES, DEFAULT_BACKEND
from artmind.webui.sessions import SessionRegistry

from artmind_canvas_backend.canvas_backend import canvas_backend_factory
from artmind_canvas_backend.routes.boards import router as boards_router
from artmind_canvas_backend.routes.events import router as events_router
from artmind_canvas_backend.routes.graph import router as graph_router
from artmind_canvas_backend.routes.placement import router as placement_router
from artmind_canvas_backend.routes.provenance import router as provenance_router
from artmind_canvas_backend.routes.settings import router as settings_router
from artmind_canvas_backend.routes.vault import router as vault_router

logger = logging.getLogger(__name__)

DEFAULT_CANVAS_PORT = 8380
SWEEP_INTERVAL_S = 60
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


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


def create_app(registry: SessionRegistry | None = None) -> FastAPI:
    registry = registry or SessionRegistry(client_factory=canvas_backend_factory)

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

    app = FastAPI(title="artmind_canvas", lifespan=lifespan)
    app.include_router(vault_router)
    app.include_router(boards_router)
    app.include_router(settings_router)
    app.include_router(events_router)
    app.include_router(provenance_router)
    app.include_router(placement_router)
    app.include_router(graph_router)

    @app.get("/health")
    async def health():
        return {"ok": True}

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

    # Prod: serve the built frontend. Registered last so /api/* and /health win.
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

    return app


def run(host: str = "127.0.0.1", port: int = DEFAULT_CANVAS_PORT) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
