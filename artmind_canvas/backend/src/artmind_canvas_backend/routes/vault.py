"""Read-only Vault access for the document Card (Phase 0)."""

from fastapi import APIRouter, HTTPException, Query

from artmind_canvas_backend.vault import read_vault_file

router = APIRouter()


@router.get("/api/vault/file")
async def get_vault_file(path: str = Query(..., description="Vault-root-relative path")):
    try:
        content = read_vault_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"path": path, "content": content}
