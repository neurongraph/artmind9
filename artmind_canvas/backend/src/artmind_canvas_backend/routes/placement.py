"""Placement Card endpoints — propose → review → confirm (ADR 0012, A5/A6).

The canvas stays a **pure client**. *Propose* is a read-only ``query`` subcommand
(``propose-placement``, A6) routed through the ``artmind serve`` daemon via the
``run_query`` seam — no Neo4j, no LLM in this process. *Confirm* writes the
accepted filing facets into the document's frontmatter through the ordinary
conditional Vault write and publishes a change event so open Cards refresh.

Re-ingest is deliberately **not** triggered here: the doc-first write lands on
disk, and the ADR 0011 watched-Vault filesystem watcher (not yet built) is what
converges the graph. Confirm therefore reports ``reingest.triggered = false``.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from artmind_canvas_backend.artmind_query import (
    ArtmindQueryError,
    ServeUnavailable,
    _domain_flags,
    run_query,
)
from artmind_canvas_backend.events import publish_change
from artmind_canvas_backend.frontmatter import merge_frontmatter, split_frontmatter
from artmind_canvas_backend.vault import VaultConflict, read_vault_file, write_vault_file

router = APIRouter()


class ProposeRequest(BaseModel):
    vaultPath: str | None = None  # read the Vault doc's body as the text to classify
    text: str | None = None  # or classify this raw text directly (unsaved draft)
    domains: list[str] = []  # scope proposals to these domain(s) (optional)
    context: str | None = None  # extra hint for the classifier


class AcceptedPlacement(BaseModel):
    area: str | None = None
    project: str | None = None
    tags: list[str] | None = None
    title: str | None = None


class ConfirmRequest(BaseModel):
    path: str
    baseVersion: str | None = None  # the version the client loaded (conditional write)
    placement: AcceptedPlacement


@router.post("/api/placement/propose")
async def propose_placement_route(payload: ProposeRequest):
    """Propose ``{domain, area, project, tags, target_file_hint}`` for a document.

    Returns ``{proposal, vaultPath, version}`` — ``version`` is the on-disk
    version the client echoes back on confirm so the write stays conditional.
    """
    version: str | None = None
    if payload.text is not None and payload.text.strip():
        text = payload.text
    elif payload.vaultPath:
        try:
            content, version = read_vault_file(payload.vaultPath)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="file not found")
        except ValueError as exc:  # traversal
            raise HTTPException(status_code=400, detail=str(exc))
        # Classify the document body, not its YAML header — existing filing keys
        # would only bias the proposal we're regenerating.
        _, text = split_frontmatter(content)
    else:
        raise HTTPException(status_code=400, detail="provide vaultPath or text")

    args = ["propose-placement", "--text", text, *_domain_flags(payload.domains)]
    if payload.context:
        args += ["--context", payload.context]

    try:
        result = await run_query(args)
    except ServeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ArtmindQueryError as exc:
        raise HTTPException(status_code=502, detail=f"artmind query failed: {exc}")

    return {"proposal": result, "vaultPath": payload.vaultPath, "version": version}


@router.post("/api/placement/confirm")
async def confirm_placement_route(payload: ConfirmRequest):
    """Write the accepted filing facets into the doc's frontmatter (doc-first).

    Conditional write (409 on stale ``baseVersion``); publishes a ``document``
    change so open Cards re-read. Does not trigger re-ingest — the ADR 0011
    watcher converges the graph.
    """
    try:
        current, _ = read_vault_file(payload.path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")
    except ValueError as exc:  # traversal
        raise HTTPException(status_code=400, detail=str(exc))

    p = payload.placement
    merged = merge_frontmatter(
        current, area=p.area, project=p.project, tags=p.tags, title=p.title
    )

    try:
        version = write_vault_file(payload.path, merged, payload.baseVersion)
    except VaultConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail={"error": "file changed on disk", "currentVersion": conflict.current_version},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    publish_change("document", path=payload.path)
    return {
        "path": payload.path,
        "version": version,
        "reingest": {"triggered": False, "reason": "watcher not yet built"},
    }
