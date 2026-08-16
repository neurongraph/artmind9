"""Vault access for the document Card + editor pane (ADR 0002, ADR 0015).

Read stays open; write is a **conditional** write (ADR 0015): the client sends the
version it loaded and the write is refused with 409 if the file changed underneath it.
Writing is markdown-only (ADR 0005) and traversal-guarded (see ``vault``).
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from artmind_canvas_backend.events import publish_change
from artmind_canvas_backend.vault import (
    VaultConflict,
    list_vault_dir,
    read_vault_file,
    write_vault_file,
)

router = APIRouter()


class WriteRequest(BaseModel):
    path: str
    content: str
    baseVersion: str | None = None  # the version the client loaded; None = create


@router.get("/api/vault/file")
async def get_vault_file(path: str = Query(..., description="Vault-root-relative path")):
    try:
        content, version = read_vault_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"path": path, "content": content, "version": version}


@router.put("/api/vault/file")
async def put_vault_file(payload: WriteRequest):
    try:
        version = write_vault_file(payload.path, payload.content, payload.baseVersion)
    except VaultConflict as conflict:
        # 409 carries the current on-disk version so the client can offer reload/diff.
        raise HTTPException(
            status_code=409,
            detail={"error": "file changed on disk", "currentVersion": conflict.current_version},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Broadcast so any read-only document Card showing this path re-queries (ADR 0008).
    publish_change("document", path=payload.path)
    return {"path": payload.path, "version": version}


@router.get("/api/vault/list")
async def get_vault_list(dir: str = Query("", description="Vault-root-relative dir")):
    try:
        return list_vault_dir(dir)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="directory not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
