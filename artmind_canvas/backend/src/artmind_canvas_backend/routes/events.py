"""Change-event SSE channel (ADR 0008).

A single long-lived ``GET /api/events`` stream per client. The frontend opens one
EventSource app-wide and fans change events out to Cards; each Card re-queries its own
scope when a relevant change arrives. Distinct from ``/api/chat`` (a per-turn stream) —
this is a persistent broadcast channel, not tied to a chat turn.
"""

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from artmind_canvas_backend.events import broker

router = APIRouter()

# Emit a comment line if idle this long, so proxies (and Vite's dev proxy) keep the
# connection open and the client notices a dropped backend promptly.
KEEPALIVE_S = 20


@router.get("/api/events")
async def events() -> StreamingResponse:
    q = broker.subscribe()

    async def stream():
        try:
            yield ": connected\n\n"  # open the stream immediately
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            broker.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream")
