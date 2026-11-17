"""FastAPI dependencies: a web write path on an unmigrated vault answers 409 (spec 2026-11-15 §8.1)."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request

from artmind import data_model


async def _dry_run(request: Request) -> bool:
    """`dry_run` from the query string or a JSON body (uploads are never parsed)."""
    if request.query_params.get("dry_run", "").lower() in ("1", "true", "yes"):
        return True
    if "json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("dry_run") is True
    return False


async def _gate(request: Request, stamp_fresh: bool) -> None:
    dry = await _dry_run(request)
    try:  # may read the graph marker: off the event loop
        await asyncio.to_thread(data_model.require_current, "web write", stamp_fresh=stamp_fresh, dry_run=dry)
    except data_model.DataModelBehind as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc


async def data_model_gate(request: Request) -> None:
    await _gate(request, True)


async def data_model_gate_no_stamp(request: Request) -> None:
    """For restores and imports: they bring data of unknown model, so an empty graph is
    never marked current on their account."""
    await _gate(request, False)
