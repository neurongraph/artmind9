"""Canvas board persistence endpoints (ADR 0009).

Named boards are application state owned by the canvas backend — distinct from
the Vault and the graph. CRUD over a canvas-owned JSON store; net-new client
endpoints on the dedicated backend (ADR 0003), not a change to artmind.
"""

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from artmind_canvas_backend.board_store import (
    Board,
    BoardPatch,
    create_board,
    delete_board,
    get_board,
    list_boards,
    save_board,
)

router = APIRouter(prefix="/api/boards")


class CreateBoardRequest(BaseModel):
    name: str = "Untitled board"


@router.get("")
async def get_boards() -> dict:
    return {"boards": [s.model_dump() for s in list_boards()]}


@router.post("", status_code=201)
async def post_board(payload: CreateBoardRequest) -> Board:
    return create_board(payload.name)


@router.get("/{board_id}")
async def get_one_board(board_id: str) -> Board:
    try:
        return get_board(board_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="board not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/{board_id}")
async def put_board(board_id: str, patch: BoardPatch) -> Board:
    try:
        return save_board(board_id, patch)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="board not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{board_id}", status_code=204)
async def del_board(board_id: str) -> Response:
    try:
        delete_board(board_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(status_code=204)
